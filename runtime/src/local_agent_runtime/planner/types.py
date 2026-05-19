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
        if not _looks_like_shell_command(command):
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


def _looks_like_shell_command(command: str) -> bool:
    lowered = command.casefold()
    if any(token in command for token in _COMMAND_STOP_TOKENS):
        return False
    tokens = [token for token in command.split() if token]
    if not tokens:
        return False
    head = tokens[0].casefold()
    tail = tokens[1:]
    has_option = any(token.startswith("-") for token in tail)
    has_path = any(
        token.endswith((".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".json", ".html", ".css", ".md"))
        or "/" in token
        or "\\" in token
        for token in tail
    )
    if head in {"python", "py"}:
        return has_option or has_path or any(token in {"-m", "-c"} for token in tail)
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
) -> str:
    prompt = (prompt_override or subtask.description).strip()
    parent = str(parent_goal or "").strip()
    if not parent:
        return prompt

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
        "- If a required artifact cannot be produced, say exactly which parent requirement is blocked instead of reporting success.",
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
