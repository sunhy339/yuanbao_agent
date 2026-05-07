from __future__ import annotations

from typing import Any


class Planner:
    """Produces UI-friendly steps and keeps step transitions deterministic."""

    def plan(self, goal: str, context: dict[str, Any] | None = None) -> list[dict[str, str]]:
        workspace_name = context.get("workspace_name", "workspace") if context else "workspace"
        route = self._route_goal(goal)
        if route["kind"] == "run_command":
            return [
                {
                    "id": "inspect-workspace",
                    "title": "Inspect workspace",
                    "status": "active",
                    "detail": f"List the top-level structure of {workspace_name} before running a command.",
                },
                {
                    "id": "run-command",
                    "title": "Run approved command",
                    "status": "pending",
                    "detail": f"Execute the explicit command after policy checks: {route['value']}",
                },
                {
                    "id": "summarize-findings",
                    "title": "Summarize findings",
                    "status": "pending",
                    "detail": "Report command status, output and next action.",
                },
            ]
        if route["kind"] == "apply_patch":
            return [
                {
                    "id": "inspect-workspace",
                    "title": "Inspect workspace",
                    "status": "active",
                    "detail": f"List the top-level structure of {workspace_name} before applying the explicit patch.",
                },
                {
                    "id": "apply-patch",
                    "title": "Apply patch",
                    "status": "pending",
                    "detail": "Apply the explicit patch payload after policy checks.",
                },
                {
                    "id": "summarize-findings",
                    "title": "Summarize findings",
                    "status": "pending",
                    "detail": "Report patch status, affected files and next action.",
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
        if route["kind"] == "code_change":
            return self._code_change_plan(goal=goal, workspace_name=workspace_name)
        return [
            {
                "id": "inspect-workspace",
                "title": "Inspect workspace",
                "status": "active",
                "detail": f"List the top-level structure of {workspace_name} to establish local context.",
            },
            {
                "id": "search-relevant-files",
                "title": "Search relevant files",
                "status": "pending",
                "detail": "Look for filenames or content that match the user's goal.",
            },
            {
                "id": "summarize-findings",
                "title": "Summarize findings",
                "status": "pending",
                "detail": f"Report the next concrete development step for: {goal}",
            },
        ]

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
        code_change_markers = (
            "修改",
            "改动",
            "添加",
            "新增",
            "实现",
            "修复",
            "编辑",
            "update",
            "change",
            "add",
            "implement",
            "fix",
            "edit",
        )
        if any(marker in lowered for marker in code_change_markers) or any(marker in goal for marker in code_change_markers):
            return {"kind": "code_change", "value": ""}
        return {"kind": "search", "value": ""}

    def _code_change_plan(self, *, goal: str, workspace_name: str) -> list[dict[str, str]]:
        lowered = goal.lower()
        is_snake_game = "贪吃蛇" in goal or "snake" in lowered
        is_ai = "ai" in lowered or "人工智能" in goal or "智能" in goal
        inspect_title = "梳理贪吃蛇项目结构" if is_snake_game else "Inspect project structure"
        search_title = "定位蛇、食物和游戏循环" if is_snake_game else "Search relevant implementation files"
        patch_title = "实现 AI 贪吃蛇对战" if is_snake_game and is_ai else "Apply requested code changes"
        verify_title = "验证改动效果" if is_snake_game else "Verify code changes"
        return [
            {
                "id": "inspect-workspace",
                "title": inspect_title,
                "status": "active",
                "detail": f"Inspect {workspace_name} and identify the project structure for: {goal}",
            },
            {
                "id": "search-relevant-files",
                "title": search_title,
                "status": "pending",
                "detail": "Find the files that own the requested behavior before editing.",
            },
            {
                "id": "apply-patch",
                "title": patch_title,
                "status": "pending",
                "detail": "Make the smallest focused code changes needed for the user's request.",
            },
            {
                "id": "run-command",
                "title": verify_title,
                "status": "pending",
                "detail": "Run the relevant local verification command after editing.",
            },
            {
                "id": "summarize-findings",
                "title": "Summarize findings",
                "status": "pending",
                "detail": "Report changed files, command outcomes and remaining risks.",
            },
        ]

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
