"""Orchestration strategies for multi-agent task execution.

Import strategy implementations lazily so consumers of `orchestration.types`
do not accidentally initialize planner/supervisor/worker dependencies.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .types import OrchestrationMode, OrchestrationResult

if TYPE_CHECKING:
    from .result_synthesizer import ResultSynthesizer
    from .supervisor import SupervisorOrchestrator
    from .swarm import SwarmOrchestrator

__all__ = [
    "OrchestrationMode",
    "OrchestrationResult",
    "ResultSynthesizer",
    "SupervisorOrchestrator",
    "SwarmOrchestrator",
]


def __getattr__(name: str) -> Any:
    if name == "ResultSynthesizer":
        from .result_synthesizer import ResultSynthesizer

        return ResultSynthesizer
    if name == "SupervisorOrchestrator":
        from .supervisor import SupervisorOrchestrator

        return SupervisorOrchestrator
    if name == "SwarmOrchestrator":
        from .swarm import SwarmOrchestrator

        return SwarmOrchestrator
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
