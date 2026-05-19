from __future__ import annotations

from copy import deepcopy
from typing import Any


def partial_handoff_from_dispatch_result(result: dict[str, Any]) -> dict[str, Any] | None:
    """Extract a child partial handoff from a failed subagent dispatch result."""
    if not isinstance(result, dict):
        return None
    error = result.get("error") if isinstance(result.get("error"), dict) else {}
    for candidate in (
        result.get("partialHandoff"),
        result.get("partial_handoff"),
        error.get("partialHandoff"),
        error.get("partial_handoff"),
    ):
        if isinstance(candidate, dict) and candidate:
            return deepcopy(candidate)
    return None


def summarize_partial_handoff(handoff: dict[str, Any] | None) -> str:
    if not isinstance(handoff, dict) or not handoff:
        return ""
    parts: list[str] = []
    status = str(handoff.get("status") or "").strip()
    if status:
        parts.append(f"status={status}")
    runtime_task_id = str(handoff.get("runtimeTaskId") or "").strip()
    if runtime_task_id:
        parts.append(f"runtimeTaskId={runtime_task_id}")
    changed = _path_list(handoff.get("changedFiles"))
    if changed:
        parts.append(f"changedFiles={', '.join(changed[:8])}")
    commands = _command_list(handoff.get("commands"))
    if commands:
        parts.append(f"commands={'; '.join(commands[:5])}")
    pending = _string_list(handoff.get("pendingVerification"))
    if pending:
        parts.append(f"pendingVerification={'; '.join(pending[:5])}")
    next_action = str(handoff.get("nextAction") or "").strip()
    if next_action:
        parts.append(f"nextAction={next_action}")
    return "Partial handoff: " + " | ".join(parts) if parts else ""


def build_continuation_prompt(
    *,
    original_description: str,
    handoff: dict[str, Any],
) -> str:
    summary = summarize_partial_handoff(handoff)
    lines = [
        original_description.strip(),
        "",
        "Continue from the previous partial handoff instead of restarting.",
    ]
    if summary:
        lines.append(summary)
    lines.extend(
        [
            "Continuation contract:",
            "- Inspect the existing files and command results first.",
            "- Preserve useful partial work already written.",
            "- Finish only the missing owned-scope artifacts or repairs.",
            "- Run the pending verification commands when possible.",
            "- If time runs out again, report updated changed files, pending verification, and next action.",
        ]
    )
    return "\n".join(line for line in lines if line is not None)


def _path_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    paths: list[str] = []
    for item in value:
        if isinstance(item, dict):
            path = str(item.get("path") or item.get("file") or "").strip()
        else:
            path = str(item or "").strip()
        if path:
            paths.append(path)
    return list(dict.fromkeys(paths))


def _command_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    commands: list[str] = []
    for item in value:
        if isinstance(item, dict):
            command = str(item.get("command") or item.get("name") or "").strip()
            status = str(item.get("status") or "").strip()
            if command and status:
                command = f"{command} ({status})"
        else:
            command = str(item or "").strip()
        if command:
            commands.append(command)
    return list(dict.fromkeys(commands))


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(str(item).strip() for item in value if str(item).strip()))
