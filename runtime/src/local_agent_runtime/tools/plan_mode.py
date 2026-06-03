"""Plan mode tools - read-only planning followed by explicit plan approval."""

from __future__ import annotations

import json
from typing import Any


def _text(value: Any, *, limit: int, default: str = "") -> str:
    text = str(value or "").strip()
    if not text:
        text = default
    return text[:limit]


def _normalize_steps(value: Any) -> list[str]:
    raw_steps = value
    if isinstance(value, dict):
        raw_steps = value.get("steps") or value.get("subtasks") or value.get("items")
    if not isinstance(raw_steps, list):
        return []
    steps: list[str] = []
    for item in raw_steps[:20]:
        if isinstance(item, dict):
            text = _text(item.get("title") or item.get("summary") or item.get("description"), limit=500)
        else:
            text = _text(item, limit=500)
        if text:
            steps.append(text)
    return steps


def _normalize_plan(params: dict[str, Any]) -> dict[str, Any]:
    raw_plan = params.get("plan")
    if isinstance(raw_plan, str):
        try:
            decoded = json.loads(raw_plan)
            raw_plan = decoded if isinstance(decoded, dict) else {"summary": raw_plan}
        except json.JSONDecodeError:
            raw_plan = {"summary": raw_plan}
    if not isinstance(raw_plan, dict):
        raw_plan = {}
    summary = _text(
        params.get("summary") or raw_plan.get("summary") or raw_plan.get("title") or raw_plan.get("description"),
        limit=1200,
        default="Proposed execution plan.",
    )
    steps = _normalize_steps(raw_plan) or _normalize_steps(params.get("steps"))
    risks = [
        _text(item, limit=300)
        for item in (params.get("risks") if isinstance(params.get("risks"), list) else raw_plan.get("risks") or [])
        if _text(item, limit=300)
    ][:10]
    return {
        "summary": summary,
        "steps": steps,
        **({"risks": risks} if risks else {}),
        **({"raw": raw_plan} if raw_plan else {}),
    }


def _approval_by_id(store: Any, approval_id: str | None) -> dict[str, Any] | None:
    if not approval_id:
        return None
    try:
        return store.get_approval({"approvalId": approval_id})["approval"]
    except ValueError as exc:
        if "not found" in str(exc).casefold():
            return None
        raise


def build_enter_plan_mode_tool(*_: Any, **__: Any) -> dict[str, Any]:
    def handler(params: dict[str, Any]) -> dict[str, Any]:
        reason = _text(params.get("reason") or params.get("summary"), limit=500, default="Plan mode requested.")
        return {
            "status": "plan_mode_entered",
            "summary": reason,
            "reason": reason,
            "instructions": "Use read-only tools to inspect context, then call exit_plan_mode with the proposed plan.",
        }

    return {"handler": handler}


def build_exit_plan_mode_tool(policy_guard: Any, store: Any, *_: Any, **__: Any) -> dict[str, Any]:
    def handler(params: dict[str, Any]) -> dict[str, Any]:
        task_id = _text(params.get("taskId") or params.get("task_id"), limit=120)
        if not task_id:
            raise ValueError("taskId is required")
        plan = _normalize_plan(params)
        approval_id = _text(params.get("approvalId") or params.get("approval_id"), limit=120)
        approval = _approval_by_id(store, approval_id)
        if approval is not None:
            if approval.get("taskId") != task_id:
                raise ValueError("Approval does not belong to the active task")
            if approval.get("kind") != "plan":
                raise ValueError("Approval kind mismatch")
            decision = approval.get("decision")
            if decision == "approved":
                return {
                    "status": "plan_approved",
                    "summary": "Plan approved by the user.",
                    "approvalId": approval["id"],
                    "plan": plan,
                }
            if decision == "rejected":
                return {
                    "status": "plan_rejected",
                    "summary": "Plan rejected by the user.",
                    "approvalId": approval["id"],
                    "plan": plan,
                }
            return {
                "status": "approval_required",
                "summary": "Plan approval is still pending.",
                "approval": approval,
                "plan": plan,
            }

        request = {
            "goal": _text(params.get("goal"), limit=1000),
            "summary": plan["summary"],
            "plan": plan,
            "steps": plan.get("steps") or [],
            "stepCount": len(plan.get("steps") or []),
            "source": "exit_plan_mode",
        }
        approval = store.create_approval(task_id=task_id, kind="plan", request=request)
        return {
            "status": "approval_required",
            "summary": "Plan approval required before execution.",
            "approval": approval,
            "plan": plan,
            "steps": [
                {"label": "plan", "status": "completed", "summary": plan["summary"]},
                {"label": "approval", "status": "blocked", "summary": "plan approval required"},
            ],
        }

    return {"handler": handler}
