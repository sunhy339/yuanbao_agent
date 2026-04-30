from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class Subtask:
    """A single decomposed sub-task within a plan."""

    id: str  # "sub-0", "sub-1", ...
    title: str  # short title
    description: str  # detailed description (used as child task prompt)
    dependencies: list[str] = field(default_factory=list)  # dependency subtask IDs
    status: str = "queued"  # queued / running / completed / failed
    result: str | None = None  # execution result summary


@dataclass(slots=True)
class PlanResult:
    """Output of TaskDecomposer.decompose(): subtasks + DAG + execution order."""

    subtasks: list[Subtask]
    dag: dict[str, list[str]]  # adjacency list {id: [dependency IDs]}
    execution_order: list[str]  # topological sort result
