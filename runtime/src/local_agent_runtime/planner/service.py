from __future__ import annotations

import re
from typing import Any


class Planner:
    """Produces UI-friendly steps and keeps step transitions deterministic."""

    # Keep root-task scaffolding out of normal ReAct turns. The model already
    # decides which files, tools, and checks are needed; injecting a generic
    # inspect/search/edit/verify plan makes that loop feel rigid and can push it
    # toward unnecessary git/read/verify calls. Only explicit planning and real
    # plan/supervisor/swarm strategies get visible root steps.
    _PLAN_STRATEGIES: frozenset[str] = frozenset({"plan_execute", "plan_supervise", "plan_swarm"})
    _SCAFFOLDED_PLAN_SCENARIOS: frozenset[str] = frozenset({"multi_step_task", "supervised_task", "swarm_task"})

    def plan(self, goal: str, context: dict[str, Any] | None = None) -> list[dict[str, str]]:
        route = self._route_goal(goal)
        if not self._should_create_visible_plan(goal, context, route):
            return []
        short_goal = self._short_goal(goal)
        localized = self._contains_cjk(goal)
        routing = self._routing_context(context)

        if self._is_orchestration_route(routing):
            return self._orchestration_plan(goal=goal, routing=routing, localized=localized)

        if self._explicit_plan_request(goal):
            return self._planning_only_plan(short_goal=short_goal, localized=localized)
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

    def _planning_only_plan(self, *, short_goal: str, localized: bool) -> list[dict[str, str]]:
        return [
            {
                "id": "clarify-goal",
                "title": self._text(localized, r"\u786e\u8ba4\u76ee\u6807\u548c\u7ea6\u675f", "Confirm goal and constraints"),
                "status": "active",
                "detail": self._text(
                    localized,
                    rf"\u56f4\u7ed5\u201c{short_goal}\u201d\u6574\u7406\u53ef\u6267\u884c\u7684\u8ba1\u5212\u8303\u56f4\u3002",
                    f"Frame an actionable plan for: {short_goal}",
                ),
            },
            {
                "id": "draft-plan",
                "title": self._text(localized, r"\u6574\u7406\u8ba1\u5212", "Draft plan"),
                "status": "pending",
                "detail": self._text(
                    localized,
                    r"\u6309\u4f18\u5148\u7ea7\u548c\u4f9d\u8d56\u5173\u7cfb\u8f93\u51fa\u8def\u7ebf\uff0c\u4e0d\u9884\u8bbe\u5fc5\u987b\u8c03\u7528\u7684\u5de5\u5177\u3002",
                    "Organize the route by priority and dependencies without preselecting required tools.",
                ),
            },
            {
                "id": "present-plan",
                "title": self._text(localized, r"\u5448\u73b0\u65b9\u6848", "Present plan"),
                "status": "pending",
                "detail": self._text(
                    localized,
                    r"\u7ed9\u51fa\u8ba1\u5212\u548c\u5fc5\u8981\u7684\u53d6\u820d\uff0c\u7531\u540e\u7eed\u6267\u884c\u6d41\u51b3\u5b9a\u5de5\u5177\u3002",
                    "Present the plan and trade-offs; the later execution flow decides tools.",
                ),
            },
        ]

    def _should_create_visible_plan(
        self,
        goal: str,
        context: dict[str, Any] | None,
        route: dict[str, str],
    ) -> bool:
        if route["kind"] != "search":
            return False
        if self._explicit_plan_request(goal):
            return True
        routing = context.get("routing") if isinstance(context, dict) else None
        if isinstance(routing, dict):
            if self._is_model_tool_orchestration_route(routing):
                return False
            strategy = str(routing.get("strategy") or "").strip()
            scenario = str(routing.get("scenario") or "").strip()
            if strategy in self._PLAN_STRATEGIES or scenario in self._SCAFFOLDED_PLAN_SCENARIOS:
                return True
        return False

    @staticmethod
    def _explicit_plan_request(goal: str) -> bool:
        lowered = str(goal or "").casefold()
        direct_markers = (
            "roadmap",
            "break down",
            "decompose",
            "task list",
            "subtasks",
            "execution plan",
            "implementation plan",
            "制定计划",
            "做一个计划",
            "做个计划",
            "给我一个计划",
            "生成计划",
            "执行计划",
            "实施计划",
            "路线图",
            "拆分",
            "任务清单",
            "子任务",
        )
        if any(marker in lowered for marker in direct_markers):
            return True
        if re.search(
            r"\b(?:create|make|write|give|draft|propose|prepare|design|outline|generate|build)\s+"
            r"(?:me\s+)?(?:a\s+|an\s+|the\s+)?(?:plan|roadmap)\b",
            lowered,
        ):
            return True
        return re.search(r"\b(?:plan|roadmap)\s+(?:for|to)\b", lowered) is not None

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
