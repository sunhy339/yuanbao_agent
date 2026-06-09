from __future__ import annotations

from .worker_environment import DEFAULT_CHILD_TOOL_ALLOWLIST


VALID_SUBTASK_AGENT_TYPES = frozenset({"planner", "worker", "reviewer", "summarizer"})
WRITING_CHILD_TOOL_ALLOWLIST = (*DEFAULT_CHILD_TOOL_ALLOWLIST, "run_command", "apply_patch", "write_file")
_COMMAND_PREFIXES = ("python", "py", "pytest", "node", "npm", "pnpm", "yarn", "bun", "go", "cargo", "dotnet", "javac", "java", "tsc", "mypy", "ruff", "eslint")
_COMMAND_STOP_TOKENS = (". ", "\u3002", "\n")


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
