from __future__ import annotations

from typing import Any


class Planner:
    """Keeps explicit task plans moving without creating backend plans.

    The default runtime path is model-first ReAct: the model decides whether it
    needs a textual plan, file reads, tools, or an explicit plan-mode tool call.
    This class must not turn ordinary prompts or route metadata into a fixed
    backend scaffold. Visible plan/task panels should come from model tool
    calls, persisted task state, or explicit user-approved plan-mode output.
    """

    def plan(self, goal: str, context: dict[str, Any] | None = None) -> list[dict[str, str]]:
        del goal
        if not isinstance(context, dict):
            return []
        explicit = context.get("explicitPlan") or context.get("explicit_plan")
        if explicit is None:
            return []
        if isinstance(explicit, dict):
            raw_steps = explicit.get("steps")
        else:
            raw_steps = explicit
        if not isinstance(raw_steps, list):
            return []
        plan: list[dict[str, str]] = []
        for index, item in enumerate(raw_steps[:20]):
            if isinstance(item, dict):
                title = str(item.get("title") or item.get("label") or item.get("summary") or "").strip()
                detail = str(item.get("detail") or item.get("description") or item.get("summary") or "").strip()
                status = str(item.get("status") or ("active" if index == 0 else "pending")).strip()
                step_id = str(item.get("id") or f"step-{index}").strip()
            else:
                title = str(item or "").strip()
                detail = ""
                status = "active" if index == 0 else "pending"
                step_id = f"step-{index}"
            if not title:
                continue
            step = {"id": step_id, "title": title[:240], "status": status or "pending"}
            if detail:
                step["detail"] = detail[:800]
            plan.append(step)
        return plan

    def advance(
        self,
        plan: list[dict[str, Any]],
        completed_step_id: str,
        *,
        next_step_id: str | None = None,
        final_status: str | None = None,
    ) -> list[dict[str, Any]]:
        updated_plan = [dict(step) for step in plan]
        for step in updated_plan:
            if step["id"] == completed_step_id:
                step["status"] = "completed"
            elif step["id"] == next_step_id:
                step["status"] = "active"
            elif final_status is not None and step["status"] != "completed":
                step["status"] = final_status
        return updated_plan
