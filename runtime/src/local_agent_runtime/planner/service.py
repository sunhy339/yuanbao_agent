from __future__ import annotations

import re
from typing import Any


class Planner:
    """Produces UI-friendly steps and keeps step transitions deterministic."""

    # Scenarios where a scaffolded plan usually feels heavier than the natural
    # provider/tool stream. Concrete edit, command, and orchestration routes
    # still get plan steps below.
    _SKIP_PLAN_SCENARIOS: frozenset[str] = frozenset({"simple_query", "free_form"})

    def plan(self, goal: str, context: dict[str, Any] | None = None) -> list[dict[str, str]]:
        # Skip plan generation for simple/free-form scenarios.
        if context:
            routing = context.get("routing")
            if isinstance(routing, dict) and routing.get("scenario") in self._SKIP_PLAN_SCENARIOS:
                strategy = str(routing.get("strategy") or "").strip()
                if strategy not in {"plan", "plan_supervise", "plan_swarm"} and not self._goal_needs_visible_plan(goal):
                    return []

        workspace_name = context.get("workspace_name", "workspace") if context else "workspace"
        route = self._route_goal(goal)
        short_goal = self._short_goal(goal)
        localized = self._contains_cjk(goal)

        if route["kind"] == "run_command":
            return [
                {
                    "id": "inspect-workspace",
                    "title": self._text(localized, r"\u68c0\u67e5\u547d\u4ee4\u4e0a\u4e0b\u6587", "Inspect command context"),
                    "status": "active",
                    "detail": self._text(
                        localized,
                        rf"\u786e\u8ba4 {workspace_name} \u7684\u5f53\u524d\u72b6\u6001\uff0c\u518d\u6267\u884c\u7528\u6237\u6307\u5b9a\u547d\u4ee4\u3002",
                        f"Confirm the current state of {workspace_name} before running the requested command.",
                    ),
                },
                {
                    "id": "run-command",
                    "title": self._text(localized, r"\u6267\u884c\u6307\u5b9a\u547d\u4ee4", "Run approved command"),
                    "status": "pending",
                    "detail": self._text(
                        localized,
                        rf"\u5b8c\u6210\u7b56\u7565\u68c0\u67e5\u540e\u6267\u884c\uff1a{route['value']}",
                        f"Execute the explicit command after policy checks: {route['value']}",
                    ),
                },
                {
                    "id": "summarize-findings",
                    "title": self._text(localized, r"\u6c47\u603b\u547d\u4ee4\u7ed3\u679c", "Summarize findings"),
                    "status": "pending",
                    "detail": self._text(
                        localized,
                        r"\u62a5\u544a\u547d\u4ee4\u72b6\u6001\u3001\u5173\u952e\u8f93\u51fa\u548c\u4e0b\u4e00\u6b65\u3002",
                        "Report command status, output and next action.",
                    ),
                },
            ]

        if route["kind"] == "apply_patch":
            return [
                {
                    "id": "inspect-workspace",
                    "title": self._text(localized, r"\u68c0\u67e5\u8865\u4e01\u4e0a\u4e0b\u6587", "Inspect patch context"),
                    "status": "active",
                    "detail": self._text(
                        localized,
                        rf"\u786e\u8ba4 {workspace_name} \u7684\u6587\u4ef6\u72b6\u6001\uff0c\u518d\u5e94\u7528\u7528\u6237\u63d0\u4f9b\u7684\u8865\u4e01\u3002",
                        f"List the top-level structure of {workspace_name} before applying the explicit patch.",
                    ),
                },
                {
                    "id": "apply-patch",
                    "title": self._text(localized, r"\u5e94\u7528\u8865\u4e01", "Apply patch"),
                    "status": "pending",
                    "detail": self._text(
                        localized,
                        r"\u5b8c\u6210\u7b56\u7565\u68c0\u67e5\u540e\u5e94\u7528\u8865\u4e01\u5185\u5bb9\u3002",
                        "Apply the explicit patch payload after policy checks.",
                    ),
                },
                {
                    "id": "summarize-findings",
                    "title": self._text(localized, r"\u6c47\u603b\u8865\u4e01\u7ed3\u679c", "Summarize findings"),
                    "status": "pending",
                    "detail": self._text(
                        localized,
                        r"\u62a5\u544a\u8865\u4e01\u72b6\u6001\u3001\u5f71\u54cd\u6587\u4ef6\u548c\u4e0b\u4e00\u6b65\u3002",
                        "Report patch status, affected files and next action.",
                    ),
                },
            ]

        if route["kind"] == "git_status":
            return [
                {
                    "id": "inspect-workspace",
                    "title": "Inspect workspace",
                    "status": "active",
                    "detail": f"List the top-level structure of {workspace_name} before checking git status.",
                },
                {
                    "id": "git-status",
                    "title": "Show git status",
                    "status": "pending",
                    "detail": "Inspect the repository status and summarize changed files.",
                },
                {
                    "id": "summarize-findings",
                    "title": "Summarize findings",
                    "status": "pending",
                    "detail": "Report repository state and next action.",
                },
            ]

        if route["kind"] == "git_diff":
            return [
                {
                    "id": "inspect-workspace",
                    "title": "Inspect workspace",
                    "status": "active",
                    "detail": f"List the top-level structure of {workspace_name} before checking git diff.",
                },
                {
                    "id": "git-diff",
                    "title": "Show git diff",
                    "status": "pending",
                    "detail": "Inspect the repository diff and summarize code changes.",
                },
                {
                    "id": "summarize-findings",
                    "title": "Summarize findings",
                    "status": "pending",
                    "detail": "Report repository diff and next action.",
                },
            ]

        if self._looks_like_code_change(goal):
            return [
                {
                    "id": "inspect-workspace",
                    "title": self._inspect_title(goal, localized),
                    "status": "active",
                    "detail": self._inspect_detail(workspace_name, short_goal, localized),
                },
                {
                    "id": "search-relevant-files",
                    "title": self._search_title(goal, localized),
                    "status": "pending",
                    "detail": self._search_detail(short_goal, localized),
                },
                {
                    "id": "apply-patch",
                    "title": self._implementation_title(goal, localized),
                    "status": "pending",
                    "detail": self._implementation_detail(short_goal, localized),
                },
                {
                    "id": "run-command",
                    "title": self._text(localized, r"\u9a8c\u8bc1\u6539\u52a8\u6548\u679c", "Verify the change"),
                    "status": "pending",
                    "detail": self._text(
                        localized,
                        r"\u8fd0\u884c\u5408\u9002\u7684\u6d4b\u8bd5\u3001\u6784\u5efa\u6216\u4eba\u5de5\u68c0\u67e5\uff0c\u786e\u8ba4\u6539\u52a8\u771f\u5b9e\u53ef\u7528\u3002",
                        "Run the appropriate tests, build, or inspection to confirm the change works.",
                    ),
                },
                {
                    "id": "summarize-findings",
                    "title": self._text(localized, r"\u6c47\u62a5\u5b8c\u6210\u60c5\u51b5", "Report completion"),
                    "status": "pending",
                    "detail": self._text(
                        localized,
                        r"\u8bf4\u660e\u6539\u4e86\u4ec0\u4e48\u3001\u9a8c\u8bc1\u7ed3\u679c\uff0c\u4ee5\u53ca\u662f\u5426\u8fd8\u6709\u98ce\u9669\u6216\u540e\u7eed\u4e8b\u9879\u3002",
                        "Summarize what changed, verification results, and any remaining risks.",
                    ),
                },
            ]

        return [
            {
                "id": "inspect-workspace",
                "title": self._inspect_title(goal, localized),
                "status": "active",
                "detail": self._inspect_detail(workspace_name, short_goal, localized),
            },
            {
                "id": "search-relevant-files",
                "title": self._search_title(goal, localized),
                "status": "pending",
                "detail": self._search_detail(short_goal, localized),
            },
            {
                "id": "summarize-findings",
                "title": self._text(localized, r"\u6574\u7406\u7b54\u590d", "Prepare response"),
                "status": "pending",
                "detail": self._text(
                    localized,
                    rf"\u56f4\u7ed5\u201c{short_goal}\u201d\u7ed9\u51fa\u5177\u4f53\u7ed3\u8bba\u548c\u4e0b\u4e00\u6b65\u3002",
                    f"Report concrete findings and next steps for: {short_goal}",
                ),
            },
        ]

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

    def _looks_like_code_change(self, goal: str) -> bool:
        lowered = goal.lower()
        change_terms = (
            "add",
            "build",
            "change",
            "create",
            "edit",
            "fix",
            "implement",
            "optimize",
            "refactor",
            "update",
            "\u6dfb\u52a0",
            "\u521b\u5efa",
            "\u4fee\u590d",
            "\u5b9e\u73b0",
            "\u6539",
            "\u66f4\u6362",
            "\u65b0\u589e",
            "\u4f18\u5316",
        )
        return any(term in lowered for term in change_terms)

    def _goal_needs_visible_plan(self, goal: str) -> bool:
        route = self._route_goal(goal)
        if route["kind"] != "search":
            return True
        return self._looks_like_code_change(goal)

    def _inspect_title(self, goal: str, localized: bool) -> str:
        if localized:
            if "ui" in goal.lower() or "\u754c\u9762" in goal:
                return self._text(True, r"\u68b3\u7406\u5f53\u524d UI \u5b9e\u73b0", "")
            return self._text(True, r"\u7406\u89e3\u4efb\u52a1\u76ee\u6807", "")
        if "ui" in goal.lower():
            return "Inspect current UI"
        return "Understand task context"

    def _search_title(self, goal: str, localized: bool) -> str:
        if localized:
            if "ui" in goal.lower() or "\u754c\u9762" in goal:
                return self._text(True, r"\u5b9a\u4f4d\u76f8\u5173\u754c\u9762\u7ec4\u4ef6", "")
            return self._text(True, r"\u5b9a\u4f4d\u76f8\u5173\u6587\u4ef6", "")
        if "ui" in goal.lower():
            return "Find related UI components"
        return "Find relevant files"

    def _implementation_title(self, goal: str, localized: bool) -> str:
        lowered = goal.lower()
        if localized:
            if "ui" in lowered or "\u754c\u9762" in goal:
                return self._text(True, r"\u8c03\u6574\u754c\u9762\u4ea4\u4e92\u548c\u6837\u5f0f", "")
            return self._text(True, r"\u5b8c\u6210\u76ee\u6807\u6539\u52a8", "")
        if "ui" in lowered:
            return "Update UI behavior and styling"
        return "Implement requested change"

    def _inspect_detail(self, workspace_name: str, short_goal: str, localized: bool) -> str:
        if localized:
            return self._text(
                True,
                rf"\u786e\u8ba4 {workspace_name} \u4e2d\u4e0e\u201c{short_goal}\u201d\u76f8\u5173\u7684\u5165\u53e3\u3001\u6a21\u5757\u548c\u73b0\u6709\u72b6\u6001\u3002",
                "",
            )
        return f"Inspect {workspace_name} for entry points, modules, and current behavior related to: {short_goal}"

    def _search_detail(self, short_goal: str, localized: bool) -> str:
        if localized:
            return self._text(
                True,
                rf"\u67e5\u627e\u652f\u6491\u201c{short_goal}\u201d\u6240\u9700\u4fee\u6539\u7684\u4ee3\u7801\u3001\u8d44\u6e90\u548c\u6d4b\u8bd5\u4f4d\u7f6e\u3002",
                "",
            )
        return f"Locate the code, assets, and tests needed for: {short_goal}"

    def _implementation_detail(self, short_goal: str, localized: bool) -> str:
        if localized:
            return self._text(
                True,
                rf"\u6309\u4efb\u52a1\u76ee\u6807\u9010\u6b65\u4fee\u6539\u672c\u5730\u6587\u4ef6\uff0c\u4fdd\u6301\u6539\u52a8\u805a\u7126\u4e8e\u201c{short_goal}\u201d\u3002",
                "",
            )
        return f"Edit local files to satisfy the requested goal while keeping the change focused: {short_goal}"

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
