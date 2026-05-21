"""task tool — delegate to subagent service."""

from __future__ import annotations

from typing import Any

from ..policy.permission_engine import PermissionRequest as PermRequest


def build_task_tool(policy_guard: Any, store: Any, subagent_service: Any | None = None, *, permission_engine: Any | None = None) -> dict[str, Any]:
    def task(params: dict[str, Any]) -> dict[str, Any]:
        if subagent_service is None:
            raise ValueError("task tool is not configured")

        # PermissionEngine gate for subagent dispatch
        if permission_engine is not None:
            decision = permission_engine.evaluate(PermRequest(
                capability="subagents",
                tool_name="task",
                context={
                    "prompt": str(params.get("prompt", ""))[:500],
                    "untrustedContentSignals": params.get("untrustedContentSignals"),
                },
            ))
            if decision.decision == "deny":
                return {
                    "status": "blocked",
                    "error": decision.reason,
                }
            if decision.decision == "approval_required":
                task_id = str(params.get("taskId") or params.get("task_id") or "").strip()
                if not task_id:
                    raise ValueError("taskId is required when subagent approval is needed")
                approval = store.create_approval(
                    task_id=task_id,
                    kind="subagent_dispatch",
                    request={"prompt": str(params.get("prompt", ""))[:200]},
                )
                return {
                    "status": "approval_required",
                    "approval": approval,
                }

        return subagent_service.dispatch(params)

    return {"handler": task}
