from __future__ import annotations

from dataclasses import dataclass, field

from ..services.worker_environment import DEFAULT_CHILD_TOOL_ALLOWLIST


VALID_SUBTASK_AGENT_TYPES = frozenset({"planner", "worker", "reviewer", "summarizer"})
WRITING_CHILD_TOOL_ALLOWLIST = (*DEFAULT_CHILD_TOOL_ALLOWLIST, "run_command", "apply_patch", "write_file")
_COMMAND_PREFIXES = ("python", "py", "pytest", "node", "npm", "pnpm", "yarn", "bun", "go", "cargo", "dotnet", "javac", "java", "tsc", "mypy", "ruff", "eslint")
_COMMAND_STOP_TOKENS = (". ", "。", "\n")


def normalize_subtask_agent_type(value: object, *, default: str = "worker") -> str:
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in VALID_SUBTASK_AGENT_TYPES:
            return normalized
    return default


def child_tool_allowlist_for_agent(agent_type: str) -> list[str]:
    normalized = normalize_subtask_agent_type(agent_type)
    if normalized == "worker":
        return list(WRITING_CHILD_TOOL_ALLOWLIST)
    return list(DEFAULT_CHILD_TOOL_ALLOWLIST)


def normalize_subtask_owned_scope(value: object) -> list[str]:
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, list):
        items = value
    else:
        return []
    seen: set[str] = set()
    normalized: list[str] = []
    for item in items:
        text = str(item).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        normalized.append(text)
    return normalized


def normalize_subtask_object_list(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    normalized: list[dict[str, object]] = []
    for item in value:
        if isinstance(item, dict):
            normalized.append(dict(item))
    return normalized


def normalize_subtask_expected_artifacts(value: object) -> list[dict[str, object]]:
    artifacts = normalize_subtask_object_list(value)
    return [artifact for artifact in artifacts if str(artifact.get("kind") or "").strip()]


def normalize_subtask_verification_requirements(value: object) -> list[dict[str, object]]:
    normalized: list[dict[str, object]] = []
    for item in normalize_subtask_object_list(value):
        kind = str(item.get("kind") or "").strip().lower()
        if kind != "command":
            normalized.append(item)
            continue
        command = str(item.get("command") or "").strip().strip("`\"' \t.,:;!?")
        lowered = command.casefold()
        if not command or not lowered.startswith(_COMMAND_PREFIXES):
            continue
        if not looks_like_shell_command(command):
            continue
        cleaned = dict(item)
        cleaned["kind"] = "command"
        cleaned["command"] = command
        normalized.append(cleaned)
    return normalized


def normalize_subtask_profile_contract(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    normalized: dict[str, object] = dict(value)
    if any(key in value for key in ("ownedScope", "owned_scope", "writeScope", "write_scope")):
        normalized["ownedScope"] = normalize_subtask_owned_scope(
            value.get("ownedScope") or value.get("owned_scope") or value.get("writeScope") or value.get("write_scope"),
        )
        normalized.pop("owned_scope", None)
        normalized.pop("writeScope", None)
        normalized.pop("write_scope", None)
    if any(key in value for key in ("expectedArtifacts", "expected_artifacts")):
        normalized["expectedArtifacts"] = normalize_subtask_expected_artifacts(
            value.get("expectedArtifacts") or value.get("expected_artifacts"),
        )
        normalized.pop("expected_artifacts", None)
    if any(key in value for key in ("verificationRequirements", "verification_requirements")):
        normalized["verificationRequirements"] = normalize_subtask_verification_requirements(
            value.get("verificationRequirements") or value.get("verification_requirements"),
        )
        normalized.pop("verification_requirements", None)
    return normalized


def _looks_like_path_token(token: str) -> bool:
    normalized = token.strip().strip("`\"'(),")
    if not normalized:
        return False
    return (
        normalized.endswith((".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".json", ".html", ".css", ".md"))
        or "/" in normalized
        or "\\" in normalized
        or "*" in normalized
        or normalized.startswith("test_")
        or normalized.startswith("tests")
    )


def looks_like_shell_command(command: str) -> bool:
    if any(token in command for token in _COMMAND_STOP_TOKENS):
        return False
    tokens = [token for token in command.split() if token]
    if not tokens:
        return False
    head = tokens[0].casefold()
    tail = tokens[1:]
    has_option = any(token.startswith("-") for token in tail)
    has_path = any(_looks_like_path_token(token) for token in tail)
    if head in {"python", "py"}:
        if "-c" in tail:
            return True
        if "-m" in tail:
            module_index = tail.index("-m") + 1
            if module_index >= len(tail):
                return False
            module = tail[module_index].casefold()
            remainder = tail[module_index + 1 :]
            if not module:
                return False
            if module == "pytest":
                return any(token.startswith("-") for token in remainder) or any(
                    _looks_like_path_token(token) or token in {"tests", "test", "::"}
                    for token in remainder
                )
            if module in {"py_compile", "compileall"}:
                return any(_looks_like_path_token(token) for token in remainder)
            return bool(remainder) and (
                any(token.startswith("-") for token in remainder)
                or any(_looks_like_path_token(token) for token in remainder)
            )
        return has_option or has_path
    if head == "pytest":
        return has_option or has_path or any(token in {"tests", "test", "::"} for token in tail)
    if head == "node":
        return has_option or has_path
    return has_option or has_path


def build_subtask_prompt(
    *,
    parent_goal: str | None,
    subtask: "Subtask",
    prompt_override: str | None = None,
    completed_context: dict[str, str] | None = None,
    compact: bool = False,
) -> str:
    prompt = (prompt_override or subtask.description).strip()
    parent = str(parent_goal or "").strip()
    if not parent:
        return prompt
    if compact:
        lines = [
            f"Subtask: {subtask.title}",
            f"Role: {normalize_subtask_agent_type(subtask.agent_type)}",
            f"Instructions: {prompt}",
        ]
        if subtask.owned_scope:
            lines.append(f"Owned scope: {', '.join(str(item) for item in subtask.owned_scope)}")
        if subtask.expected_artifacts:
            lines.append(f"Expected artifacts: {subtask.expected_artifacts}")
        if subtask.verification_requirements:
            lines.append(f"Verification requirements: {subtask.verification_requirements}")
        if completed_context:
            lines.append("Completed sibling context:")
            for key, value in completed_context.items():
                if str(value).strip():
                    lines.append(f"- {key}: {str(value).strip()[:600]}")
        lines.extend(
            [
                "Execution contract:",
                "- Stay inside your owned scope and produce only the artifacts this subtask calls for.",
                "- Run the verification commands this subtask requires when they are relevant.",
                "- Prioritize durable progress: write the smallest useful artifact set first, then verify.",
                "- If blocked, timed out, or unable to finish, report changed files, pending verification, blockers, and the next action instead of claiming success.",
            ]
        )
        return "\n".join(lines)

    lines = [
        "[Parent task]",
        parent,
        "",
        "[Assigned subtask]",
        f"Title: {subtask.title}",
        f"Role: {normalize_subtask_agent_type(subtask.agent_type)}",
        f"Instructions: {prompt}",
    ]
    if subtask.owned_scope:
        lines.append(f"Owned scope: {', '.join(str(item) for item in subtask.owned_scope)}")
    if subtask.expected_artifacts:
        lines.append(f"Expected artifacts: {subtask.expected_artifacts}")
    if subtask.verification_requirements:
        lines.append(f"Verification requirements: {subtask.verification_requirements}")
    if completed_context:
        lines.extend(["", "[Completed sibling context]"])
        for key, value in completed_context.items():
            if str(value).strip():
                lines.append(f"- {key}: {str(value).strip()[:1200]}")
    lines.extend([
        "",
        "[Execution contract]",
        "- Do not shrink or reinterpret the parent task. Preserve explicit file, test, documentation, and verification requirements from the parent task.",
        "- If this subtask owns implementation or verification, produce the concrete files and commands needed for the parent acceptance criteria.",
        "- Prioritize durable progress: write the smallest useful artifact set first, then run verification.",
        "- If a required artifact cannot be produced or time runs out, say exactly which parent requirement is blocked, which files changed, which verification is pending, and what should happen next.",
    ])
    return "\n".join(lines)


@dataclass(slots=True)
class Subtask:
    """A single decomposed sub-task within a plan."""

    id: str  # "sub-0", "sub-1", ...
    title: str  # short title
    description: str  # detailed description (used as child task prompt)
    dependencies: list[str] = field(default_factory=list)  # dependency subtask IDs
    agent_type: str = "worker"  # planner / worker / reviewer / summarizer
    owned_scope: list[str] = field(default_factory=list)
    expected_artifacts: list[dict[str, object]] = field(default_factory=list)
    verification_requirements: list[dict[str, object]] = field(default_factory=list)
    status: str = "queued"  # queued / running / completed / failed
    result: str | None = None  # execution result summary


@dataclass(slots=True)
class PlanResult:
    """Output of TaskDecomposer.decompose(): subtasks + DAG + execution order."""

    subtasks: list[Subtask]
    dag: dict[str, list[str]]  # adjacency list {id: [dependency IDs]}
    execution_order: list[str]  # topological sort result
    provider_response: dict[str, object] | None = None


def _unique_strings(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    seen: set[str] = set()
    result: list[str] = []
    for item in value:
        text = str(item).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _dict_alias(data: dict[str, object], *keys: str) -> object:
    for key in keys:
        if key in data:
            return data[key]
    return None


def subtask_to_dict(subtask: Subtask) -> dict[str, object]:
    return {
        "id": subtask.id,
        "title": subtask.title,
        "description": subtask.description,
        "dependencies": list(subtask.dependencies),
        "agentType": normalize_subtask_agent_type(subtask.agent_type),
        "ownedScope": list(subtask.owned_scope),
        "expectedArtifacts": [dict(item) for item in subtask.expected_artifacts],
        "verificationRequirements": [dict(item) for item in subtask.verification_requirements],
        "status": subtask.status,
        "result": subtask.result,
    }


def subtask_from_dict(data: dict[str, object]) -> Subtask:
    subtask_id = str(data.get("id") or "").strip()
    title = str(data.get("title") or subtask_id or "Subtask").strip()
    description = str(data.get("description") or "").strip()
    result_value = data.get("result")
    result = str(result_value) if result_value is not None else None
    status = str(data.get("status") or "queued").strip() or "queued"
    return Subtask(
        id=subtask_id,
        title=title,
        description=description,
        dependencies=_unique_strings(data.get("dependencies")),
        agent_type=normalize_subtask_agent_type(_dict_alias(data, "agentType", "agent_type")),
        owned_scope=normalize_subtask_owned_scope(_dict_alias(data, "ownedScope", "owned_scope", "writeScope", "write_scope")),
        expected_artifacts=normalize_subtask_expected_artifacts(_dict_alias(data, "expectedArtifacts", "expected_artifacts")),
        verification_requirements=normalize_subtask_verification_requirements(
            _dict_alias(data, "verificationRequirements", "verification_requirements")
        ),
        status=status,
        result=result,
    )


def plan_result_to_dict(plan: PlanResult, *, extra_subtasks: list[Subtask] | None = None) -> dict[str, object]:
    subtasks = list(plan.subtasks)
    seen = {str(subtask.id) for subtask in subtasks if str(subtask.id).strip()}
    for subtask in extra_subtasks or []:
        subtask_id = str(subtask.id).strip()
        if subtask_id and subtask_id not in seen:
            subtasks.append(subtask)
            seen.add(subtask_id)

    execution_order = _unique_strings(list(plan.execution_order))
    for subtask in subtasks:
        if subtask.id and subtask.id not in execution_order:
            execution_order.append(subtask.id)

    dag: dict[str, list[str]] = {}
    for key, value in (plan.dag or {}).items():
        dag[str(key)] = _unique_strings(value)
    for subtask in subtasks:
        dag.setdefault(subtask.id, list(subtask.dependencies))

    data: dict[str, object] = {
        "subtasks": [subtask_to_dict(subtask) for subtask in subtasks],
        "dag": dag,
        "execution_order": execution_order,
    }
    if plan.provider_response is not None:
        data["providerResponse"] = dict(plan.provider_response)
    return data


def plan_result_from_dict(data: dict[str, object]) -> PlanResult:
    raw_subtasks = data.get("subtasks")
    subtasks = [
        subtask_from_dict(item)
        for item in raw_subtasks
        if isinstance(raw_subtasks, list) and isinstance(item, dict)
    ] if isinstance(raw_subtasks, list) else []
    subtasks = [subtask for subtask in subtasks if subtask.id]

    raw_dag = data.get("dag")
    dag: dict[str, list[str]] = {}
    if isinstance(raw_dag, dict):
        for key, value in raw_dag.items():
            key_text = str(key).strip()
            if key_text:
                dag[key_text] = _unique_strings(value)
    for subtask in subtasks:
        dag.setdefault(subtask.id, list(subtask.dependencies))

    raw_order = data.get("execution_order") or data.get("executionOrder")
    execution_order = _unique_strings(raw_order)
    for subtask in subtasks:
        if subtask.id not in execution_order:
            execution_order.append(subtask.id)

    provider_response = data.get("providerResponse")
    return PlanResult(
        subtasks=subtasks,
        dag=dag,
        execution_order=execution_order,
        provider_response=dict(provider_response) if isinstance(provider_response, dict) else None,
    )
