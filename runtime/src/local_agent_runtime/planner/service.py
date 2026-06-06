from __future__ import annotations

import re
from typing import Any


class Planner:
    """Produces optional UI root-plan steps for explicit legacy orchestration.

    The default runtime path is model-first ReAct: the model decides whether it
    needs a textual plan, file reads, tools, or an explicit plan-mode tool call.
    This planner must not turn ordinary roadmap/task-list prompts into a fixed
    backend plan.
    """

    _PLAN_STRATEGIES: frozenset[str] = frozenset({"plan_execute", "plan_supervise", "plan_swarm"})
    _SCAFFOLDED_PLAN_SCENARIOS: frozenset[str] = frozenset({"multi_step_task", "supervised_task", "swarm_task"})

    def plan(self, goal: str, context: dict[str, Any] | None = None) -> list[dict[str, str]]:
        route = self._route_goal(goal)
        if not self._should_create_visible_plan(context, route):
            return []
        localized = self._contains_cjk(goal)
        routing = self._routing_context(context)

        if self._is_orchestration_route(routing):
            return self._orchestration_plan(goal=goal, routing=routing, localized=localized)

        return []

    def _text(self, localized: bool, localized_text: str, english_text: str) -> str:
        if localized:
            return re.sub(r"\\u([0-9a-fA-F]{4})", lambda match: chr(int(match.group(1), 16)), localized_text)
        return english_text

    def _contains_cjk(self, value: str) -> bool:
        return any("\u4e00" <= char <= "\u9fff" for char in value)

    def _short_goal(self, goal: str, limit: int = 56) -> str:
        normalized = " ".join(str(goal).split())
        if len(normalized) <= limit:
            return normalized
        return normalized[: limit - 1].rstrip() + "..."

    @staticmethod
    def _routing_context(context: dict[str, Any] | None) -> dict[str, Any]:
        routing = context.get("routing") if isinstance(context, dict) else None
        return routing if isinstance(routing, dict) else {}

    def _is_orchestration_route(self, routing: dict[str, Any]) -> bool:
        if self._is_model_tool_orchestration_route(routing):
            return False
        strategy = str(routing.get("strategy") or "").strip()
        scenario = str(routing.get("scenario") or "").strip()
        return strategy in self._PLAN_STRATEGIES or scenario in self._SCAFFOLDED_PLAN_SCENARIOS

    @staticmethod
    def _is_model_tool_orchestration_route(routing: dict[str, Any]) -> bool:
        mode = str(routing.get("orchestrationMode") or routing.get("orchestration_mode") or "").strip()
        if mode == "model_tools":
            return True
        strategy = str(routing.get("strategy") or "").strip()
        scenario = str(routing.get("scenario") or "").strip()
        planning_like = strategy in Planner._PLAN_STRATEGIES or scenario in Planner._SCAFFOLDED_PLAN_SCENARIOS
        return planning_like and routing.get("enable_planning") is False

    def _orchestration_plan(
        self,
        *,
        goal: str,
        routing: dict[str, Any],
        localized: bool,
    ) -> list[dict[str, str]]:
        mode = str(routing.get("strategy") or routing.get("scenario") or "plan").strip()
        is_swarm = mode == "plan_swarm" or str(routing.get("scenario") or "") == "swarm_task"
        short_goal = self._short_goal(goal)
        return [
            {
                "id": "decompose-work",
                "title": self._text(localized, r"\u62c6\u5206\u534f\u4f5c\u8303\u56f4", "Split collaboration scope"),
                "status": "active",
                "detail": self._text(
                    localized,
                    rf"\u6839\u636e\u201c{short_goal}\u201d\u786e\u5b9a\u9700\u8981\u54ea\u4e9b\u5b50\u4efb\u52a1\u548c\u8fb9\u754c\u3002",
                    f"Decide which subtasks and boundaries are needed for: {short_goal}",
                ),
            },
            {
                "id": "dispatch-work",
                "title": self._text(
                    localized,
                    r"\u6d3e\u53d1 agent \u6216\u5b50\u4efb\u52a1" if is_swarm else r"\u5b89\u6392\u6267\u884c\u987a\u5e8f",
                    "Dispatch agents or subtasks" if is_swarm else "Arrange execution order",
                ),
                "status": "pending",
                "detail": self._text(
                    localized,
                    r"\u53ea\u5c06\u5fc5\u8981\u7684\u5de5\u4f5c\u5206\u914d\u51fa\u53bb\uff0c\u5b50\u4efb\u52a1\u5404\u81ea\u62a5\u544a\u7ed3\u679c\u3002",
                    "Assign only the necessary work and let each child task report its result.",
                ),
            },
            {
                "id": "synthesize-results",
                "title": self._text(localized, r"\u6c47\u603b\u7ed3\u679c", "Synthesize results"),
                "status": "pending",
                "detail": self._text(
                    localized,
                    r"\u5408\u5e76\u5b50\u4efb\u52a1\u8f93\u51fa\uff0c\u53ea\u5728\u9700\u8981\u65f6\u7ee7\u7eed\u8865\u5145\u5de5\u5177\u8c03\u7528\u3002",
                    "Merge child-task output and continue with tools only when needed.",
                ),
            },
        ]

    def _should_create_visible_plan(
        self,
        context: dict[str, Any] | None,
        route: dict[str, str],
    ) -> bool:
        if route["kind"] != "search":
            return False
        routing = context.get("routing") if isinstance(context, dict) else None
        if not isinstance(routing, dict):
            return False
        if self._is_model_tool_orchestration_route(routing):
            return False
        strategy = str(routing.get("strategy") or "").strip()
        scenario = str(routing.get("scenario") or "").strip()
        return strategy in self._PLAN_STRATEGIES or scenario in self._SCAFFOLDED_PLAN_SCENARIOS

    def _route_goal(self, goal: str) -> dict[str, str]:
        lowered = goal.lower().strip()
        for kind, prefixes in (
            ("run_command", ("run command:", "execute command:", "cmd:")),
            ("apply_patch", ("apply patch:",)),
            ("git_status", ("show git status", "git status:")),
            ("git_diff", ("show git diff", "git diff:")),
        ):
            for prefix in prefixes:
                if lowered.startswith(prefix):
                    value = goal[len(prefix) :].strip()
                    return {"kind": kind, "value": value}
        return {"kind": "search", "value": ""}

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
