"""task tool — delegate to subagent service."""

from __future__ import annotations

from typing import Any


def build_task_tool(policy_guard: Any, store: Any, subagent_service: Any | None = None) -> dict[str, Any]:
    def task(params: dict[str, Any]) -> dict[str, Any]:
        if subagent_service is None:
            raise ValueError("task tool is not configured")
        return subagent_service.dispatch(params)

    return {"handler": task}
