from __future__ import annotations

from copy import deepcopy
from typing import Any


class Planner:
    """Produces UI-friendly steps and keeps step transitions deterministic."""

    def plan(self, goal: str, context: dict[str, Any] | None = None) -> list[dict[str, str]]:
        workspace_name = context.get("workspace_name", "workspace") if context else "workspace"
        command = self._extract_explicit_command(goal)
        if command:
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
                    "detail": f"Execute the explicit command after policy checks: {command}",
                },
                {
                    "id": "summarize-findings",
                    "title": "Summarize findings",
                    "status": "pending",
                    "detail": "Report command status, output and next action.",
                },
            ]
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

    def _extract_explicit_command(self, goal: str) -> str | None:
        lowered = goal.lower()
        for prefix in ("run command:", "execute command:", "cmd:"):
            if lowered.startswith(prefix):
                command = goal[len(prefix) :].strip()
                return command or None
        return None

    def advance(
        self,
        plan: list[dict[str, Any]],
        completed_step_id: str,
        *,
        next_step_id: str | None = None,
        final_status: str | None = None,
    ) -> list[dict[str, Any]]:
        updated_plan = deepcopy(plan)
        for step in updated_plan:
            if step["id"] == completed_step_id:
                step["status"] = "completed"
            elif step["id"] == next_step_id:
                step["status"] = "active"
            elif final_status is not None and step["status"] != "completed":
                step["status"] = final_status
        return updated_plan
