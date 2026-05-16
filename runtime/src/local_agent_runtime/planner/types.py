from __future__ import annotations

from dataclasses import dataclass, field

from ..services.worker_environment import DEFAULT_CHILD_TOOL_ALLOWLIST


VALID_SUBTASK_AGENT_TYPES = frozenset({"planner", "worker", "reviewer", "summarizer"})
WRITING_CHILD_TOOL_ALLOWLIST = (*DEFAULT_CHILD_TOOL_ALLOWLIST, "run_command", "apply_patch", "write_file")


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
    status: str = "queued"  # queued / running / completed / failed
    result: str | None = None  # execution result summary


@dataclass(slots=True)
class PlanResult:
    """Output of TaskDecomposer.decompose(): subtasks + DAG + execution order."""

    subtasks: list[Subtask]
    dag: dict[str, list[str]]  # adjacency list {id: [dependency IDs]}
    execution_order: list[str]  # topological sort result
