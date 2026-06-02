"""task tool — delegate to subagent service."""

from __future__ import annotations

from typing import Any

from ..policy.permission_engine import PermissionRequest as PermRequest


def _step(label: str, status: str, summary: str) -> dict[str, str]:
    return {"label": label, "status": status, "summary": summary}


def build_task_tool(policy_guard: Any, store: Any, subagent_service: Any | None = None, *, permission_engine: Any | None = None) -> dict[str, Any]:
    def task(params: dict[str, Any]) -> dict[str, Any]:
        if subagent_service is None:
            raise ValueError("task tool is not configured")
        prompt = str(params.get("prompt", "")).strip()
        title = str(params.get("title") or params.get("agentType") or params.get("agent_type") or prompt or "subtask").strip()
        steps = [
            _step("prepare", "completed", title[:120]),
        ]

        # PermissionEngine gate for subagent dispatch
        if permission_engine is not None:
            decision = permission_engine.evaluate(PermRequest(
                capability="subagents",
                tool_name="task",
                context={
                    "prompt": prompt[:500],
                    "untrustedContentSignals": params.get("untrustedContentSignals"),
                },
            ))
            if decision.decision == "deny":
                return {
                    "status": "blocked",
                    "error": decision.reason,
                    "steps": [
                        *steps,
                        _step("permission", "blocked", str(decision.reason)),
                    ],
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
                    "steps": [
                        *steps,
                        _step("permission", "blocked", "subagent approval required"),
                    ],
                }
            steps.append(_step("permission", "completed", str(decision.decision)))

        steps.append(_step("dispatch", "running", title[:120]))
        result = subagent_service.dispatch(params)
        status = str(result.get("status") or "completed")
        child_id = str(result.get("childTaskId") or result.get("taskId") or "").strip()
        summary = str(result.get("summary") or result.get("resultSummary") or child_id or status).strip()
        steps[-1] = _step("dispatch", "completed" if status == "completed" else status, summary[:180])
        if child_id:
            steps.append(_step("child_task", "completed", child_id))
        return {
            **result,
            "steps": [*steps, *result.get("steps", [])] if isinstance(result.get("steps"), list) else steps,
        }

    return {"handler": task}
