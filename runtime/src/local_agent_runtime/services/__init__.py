"""Application services backed by the store.

Keep this package initializer lazy. Several low-level modules import service
submodules for constants or types; eagerly importing every service here can pull
the planner and worker stack into unrelated code paths.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .collaboration_service import CollaborationService
    from .hook_service import HookService
    from .replay_service import ReplayService
    from .subagent_service import SubagentService
    from .worker_runner import ChildTaskRequest, WorkerRunner

__all__ = [
    "ChildTaskRequest",
    "CollaborationService",
    "HookService",
    "ReplayService",
    "SubagentService",
    "WorkerRunner",
]


def __getattr__(name: str) -> Any:
    if name == "CollaborationService":
        from .collaboration_service import CollaborationService

        return CollaborationService
    if name == "HookService":
        from .hook_service import HookService

        return HookService
    if name == "ReplayService":
        from .replay_service import ReplayService

        return ReplayService
    if name == "SubagentService":
        from .subagent_service import SubagentService

        return SubagentService
    if name in {"ChildTaskRequest", "WorkerRunner"}:
        from .worker_runner import ChildTaskRequest, WorkerRunner

        return ChildTaskRequest if name == "ChildTaskRequest" else WorkerRunner
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
