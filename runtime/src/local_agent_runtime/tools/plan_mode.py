"""Plan mode tools - read-only planning followed by explicit plan approval."""

from __future__ import annotations

import json
from typing import Any

from ..planner.approval_preview import attach_plan_preview


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


def _normalize_subtasks(plan: dict[str, Any], steps: list[str]) -> list[dict[str, Any]]:
    raw_subtasks = plan.get("subtasks")
    subtasks: list[dict[str, Any]] = []
    if isinstance(raw_subtasks, list):
        for index, item in enumerate(raw_subtasks[:20]):
            if isinstance(item, dict):
                title = _text(
                    item.get("title")
                    or item.get("subtaskTitle")
                    or item.get("summary")
                    or item.get("description"),
                    limit=240,
                )
                if not title:
                    continue
                description = _text(item.get("description") or item.get("summary"), limit=500)
                agent_type = _text(item.get("agentType") or item.get("agent_type"), limit=80)
                subtask = {
                    "id": _text(item.get("id") or item.get("subtaskId") or f"sub-{index}", limit=80),
                    "title": title,
                }
                if description:
                    subtask["description"] = description
                if agent_type:
                    subtask["agentType"] = agent_type
                if isinstance(item.get("dependencies"), list):
                    subtask["dependencies"] = [str(dep)[:80] for dep in item["dependencies"][:10]]
                subtasks.append(subtask)
            else:
                title = _text(item, limit=240)
                if title:
                    subtasks.append({"id": f"sub-{index}", "title": title})
    if subtasks:
        return subtasks
    return [{"id": f"sub-{index}", "title": step} for index, step in enumerate(steps[:20])]


def _normalize_execution_order(params: dict[str, Any], plan: dict[str, Any], subtasks: list[dict[str, Any]]) -> list[str]:
    raw_order = params.get("executionOrder") or params.get("execution_order") or plan.get("executionOrder")
    if isinstance(raw_order, list):
        order = [_text(item, limit=80) for item in raw_order[:20]]
        return [item for item in order if item]
    return [_text(item.get("id"), limit=80) for item in subtasks if _text(item.get("id"), limit=80)]


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
    subtasks = _normalize_subtasks(raw_plan, steps)
    risks = [
        _text(item, limit=300)
        for item in (params.get("risks") if isinstance(params.get("risks"), list) else raw_plan.get("risks") or [])
        if _text(item, limit=300)
    ][:10]
    return {
        "summary": summary,
        "steps": steps,
        **({"subtasks": subtasks} if subtasks else {}),
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
        task_goal = ""
        try:
            task_goal = _text(store.get_task({"taskId": task_id})["task"].get("goal"), limit=1000)
        except Exception:
            task_goal = ""
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

        subtasks = plan.get("subtasks") or []
        request = {
            "goal": _text(params.get("goal") or task_goal, limit=1000),
            "summary": plan["summary"],
            "plan": plan,
            "steps": plan.get("steps") or [],
            "subtasks": subtasks,
            "subtaskCount": len(subtasks),
            "mode": _text(params.get("mode") or params.get("orchestrationMode") or params.get("orchestration_mode"), limit=80, default="plan"),
            "orchestrationMode": _text(params.get("orchestrationMode") or params.get("orchestration_mode") or params.get("mode"), limit=80, default="plan"),
            "executionOrder": _normalize_execution_order(params, plan.get("raw") or {}, subtasks),
            "stepCount": len(plan.get("steps") or []),
            "source": "exit_plan_mode",
        }
        request = attach_plan_preview(request)
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
