from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class OrchestrationMode(str, Enum):
    """Which orchestration strategy to use for plan execution."""

    DAG = "dag"                # Existing DAG topological-order execution
    SUPERVISOR = "supervisor"  # Main agent reviews sub-agent output
    SWARM = "swarm"            # Agents hand off to each other dynamically


@dataclass
class OrchestrationResult:
    """Unified result returned by all orchestration strategies."""

    success: bool
    summary: str
    subtask_results: list[dict[str, Any]]
    review_count: int = 0       # supervisor mode: number of reviews performed
    handoff_count: int = 0      # swarm mode: number of handoffs performed
    paused: bool = False
    completed: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    results: dict[str, str] = field(default_factory=dict)
    partial_handoffs: list[dict[str, Any]] = field(default_factory=list)
