from __future__ import annotations

from typing import Any

from ..services.subagent_service import SubagentService
from .types import PlanResult, Subtask


class DAGExecutor:
    """Execute sub-tasks in topological order via SubagentService."""

    def __init__(self, subagent_service: SubagentService) -> None:
        self._subagent = subagent_service

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def execute(
        self,
        plan: PlanResult,
        *,
        session_id: str,
        parent_task_id: str,
    ) -> dict[str, Any]:
        """Execute all sub-tasks in topological order.

        Returns ``{subtasks: [...], summary: str, success: bool}``.

        When a sub-task fails, its dependents are skipped but independent
        sub-tasks continue executing.
        """
        completed: set[str] = set()
        failed: set[str] = set()
        results: dict[str, str] = {}

        for subtask_id in plan.execution_order:
            subtask = self._find_subtask(plan.subtasks, subtask_id)
            if subtask is None:
                continue

            # Check if any dependency failed
            if self._has_failed_dependency(subtask, failed):
                subtask.status = "skipped"
                failed.add(subtask.id)
                results[subtask.id] = f"Skipped: dependency failed"
                continue

            # Check all dependencies completed
            if not self._check_dependencies(subtask, completed):
                subtask.status = "skipped"
                failed.add(subtask.id)
                results[subtask.id] = "Skipped: dependencies not met"
                continue

            # Execute
            subtask.status = "running"
            try:
                dispatch_result = self._subagent.dispatch({
                    "prompt": subtask.description,
                    "title": subtask.title,
                    "sessionId": session_id,
                    "taskId": parent_task_id,
                    "agentType": "planner",
                })
                subtask.status = "completed"
                subtask.result = dispatch_result.get("summary") or "Completed"
                completed.add(subtask.id)
                results[subtask.id] = subtask.result
            except Exception as exc:  # noqa: BLE001
                subtask.status = "failed"
                subtask.result = str(exc)
                failed.add(subtask.id)
                results[subtask.id] = f"Failed: {exc}"

        success = len(failed) == 0
        summary = self.synthesize_results(plan.subtasks)
        return {
            "subtasks": plan.subtasks,
            "summary": summary,
            "success": success,
            "completed": list(completed),
            "failed": list(failed),
        }

    def synthesize_results(self, subtasks: list[Subtask]) -> str:
        """Combine all sub-task results into a final summary."""
        parts: list[str] = []
        for subtask in subtasks:
            if subtask.status in ("completed",):
                parts.append(f"- {subtask.title}: {subtask.result or 'Done'}")
            elif subtask.status == "failed":
                parts.append(f"- {subtask.title}: FAILED - {subtask.result}")
            elif subtask.status == "skipped":
                parts.append(f"- {subtask.title}: SKIPPED")
            else:
                parts.append(f"- {subtask.title}: {subtask.status}")

        header = f"Plan execution completed ({_count_by_status(subtasks, 'completed')}/{len(subtasks)} succeeded)"
        return header + "\n" + "\n".join(parts)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _find_subtask(subtasks: list[Subtask], subtask_id: str) -> Subtask | None:
        for s in subtasks:
            if s.id == subtask_id:
                return s
        return None

    @staticmethod
    def _check_dependencies(subtask: Subtask, completed: set[str]) -> bool:
        return all(dep in completed for dep in subtask.dependencies)

    @staticmethod
    def _has_failed_dependency(subtask: Subtask, failed: set[str]) -> bool:
        return bool(set(subtask.dependencies) & failed)


def _count_by_status(subtasks: list[Subtask], status: str) -> int:
    return sum(1 for s in subtasks if s.status == status)
