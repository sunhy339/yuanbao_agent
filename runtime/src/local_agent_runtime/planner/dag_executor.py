from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable

from ..observability.tracer import Tracer
from ..services.subagent_service import SubagentService
from .types import PlanResult, Subtask

logger = logging.getLogger(__name__)


class DAGExecutor:
    """Execute sub-tasks in topological order via SubagentService.

    Supports parallel execution of independent sub-tasks within the same
    dependency level using ``concurrent.futures.ThreadPoolExecutor``.
    """

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
        max_workers: int = 4,
        is_paused_fn: Callable[[], bool] | None = None,
        completed_ids: set[str] | None = None,
        failed_ids: set[str] | None = None,
        prior_results: dict[str, str] | None = None,
        tracer: Tracer | None = None,
    ) -> dict[str, Any]:
        """Execute all sub-tasks, parallelising independent tasks per level.

        Returns ``{subtasks: [...], summary: str, success: bool}``.

        When a sub-task fails, its dependents are skipped but independent
        sub-tasks continue executing.

        If *is_paused_fn* is provided and returns True after a sub-task
        completes, execution is paused and a partial result is returned
        with ``paused=True``.  On resume, pass *completed_ids* /
        *failed_ids* / *prior_results* to skip already-completed work.
        """
        completed: set[str] = set(completed_ids or ())
        failed: set[str] = set(failed_ids or ())
        results: dict[str, str] = dict(prior_results or ())
        lock = threading.Lock()

        levels = self._group_by_level(plan)
        logger.info("DAG execution: %d levels, %d total subtasks", len(levels), len(plan.subtasks))

        dag_span = None
        if tracer is not None:
            dag_span = tracer.start_span(
                "dag_execute",
                trace_id=parent_task_id,
                attributes={"levels": len(levels), "subtasks": len(plan.subtasks)},
            )

        for level_idx, level in enumerate(levels):
            # Filter level to only runnable tasks (skip completed/failed/dependency-failed)
            runnable: list[str] = []
            for subtask_id in level:
                subtask = self._find_subtask(plan.subtasks, subtask_id)
                if subtask is None:
                    continue
                if subtask_id in completed:
                    subtask.status = "completed"
                    subtask.result = results.get(subtask_id, "Completed")
                    continue
                if subtask_id in failed:
                    subtask.status = "failed"
                    subtask.result = results.get(subtask_id, "Failed")
                    continue
                if self._has_failed_dependency(subtask, failed):
                    subtask.status = "skipped"
                    failed.add(subtask.id)
                    results[subtask.id] = "Skipped: dependency failed"
                    continue
                if not self._check_dependencies(subtask, completed):
                    subtask.status = "skipped"
                    failed.add(subtask.id)
                    results[subtask.id] = "Skipped: dependencies not met"
                    continue
                runnable.append(subtask_id)

            if not runnable:
                continue

            # Execute level — parallel if multiple tasks, serial if single
            if len(runnable) == 1:
                self._execute_subtask(
                    plan, runnable[0], completed, failed, results,
                    session_id, parent_task_id, lock, tracer,
                )
            else:
                logger.info("Level %d: executing %d subtasks in parallel", level_idx, len(runnable))
                with ThreadPoolExecutor(max_workers=min(max_workers, len(runnable))) as pool:
                    futures = {
                        pool.submit(
                            self._execute_subtask,
                            plan, sid, completed, failed, results,
                            session_id, parent_task_id, lock, tracer,
                        ): sid
                        for sid in runnable
                    }
                    for future in as_completed(futures):
                        # Result already captured inside _execute_subtask
                        # via shared completed/failed/results sets
                        _ = future.result()  # propagate exceptions if any

            # Cooperative pause check after each level
            if is_paused_fn is not None and is_paused_fn():
                if dag_span is not None:
                    tracer.end_span(dag_span.span_id, status="ok", attributes={"paused": True})
                return {
                    "subtasks": plan.subtasks,
                    "summary": "",
                    "success": None,
                    "completed": list(completed),
                    "failed": list(failed),
                    "paused": True,
                    "results": dict(results),
                }

        success = len(failed) == 0
        summary = self.synthesize_results(plan.subtasks)
        if dag_span is not None:
            tracer.end_span(dag_span.span_id, status="ok" if success else "error",
                            attributes={"completed": len(completed), "failed": len(failed)})
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

    def _execute_subtask(
        self,
        plan: PlanResult,
        subtask_id: str,
        completed: set[str],
        failed: set[str],
        results: dict[str, str],
        session_id: str,
        parent_task_id: str,
        lock: threading.Lock,
        tracer: Tracer | None = None,
    ) -> None:
        """Execute a single subtask and update shared state."""
        subtask = self._find_subtask(plan.subtasks, subtask_id)
        if subtask is None:
            return

        span = None
        if tracer is not None:
            span = tracer.start_span(
                "dag_subtask",
                trace_id=parent_task_id,
                attributes={"subtask_id": subtask_id, "title": subtask.title},
            )

        subtask.status = "running"
        try:
            dispatch_result = self._subagent.dispatch({
                "prompt": subtask.description,
                "title": subtask.title,
                "sessionId": session_id,
                "taskId": parent_task_id,
                "agentType": "planner",
            })
            with lock:
                subtask.status = "completed"
                subtask.result = dispatch_result.get("summary") or "Completed"
                completed.add(subtask.id)
                results[subtask.id] = subtask.result
            if span is not None:
                tracer.end_span(span.span_id, status="ok")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Subtask %s failed: %s", subtask_id, exc)
            with lock:
                subtask.status = "failed"
                subtask.result = str(exc)
                failed.add(subtask.id)
                results[subtask.id] = f"Failed: {exc}"
            if span is not None:
                tracer.end_span(span.span_id, status="error", attributes={"error": str(exc)})

    def _group_by_level(self, plan: PlanResult) -> list[list[str]]:
        """Group execution_order into dependency levels for parallel execution.

        Level 0: tasks with no dependencies.
        Level N: tasks whose dependencies are all at level < N.
        Tasks within the same level can be executed in parallel.
        """
        levels: dict[str, int] = {}
        for sid in plan.execution_order:
            subtask = self._find_subtask(plan.subtasks, sid)
            if subtask and subtask.dependencies:
                levels[sid] = max(levels.get(d, 0) for d in subtask.dependencies) + 1
            else:
                levels[sid] = 0

        grouped: dict[int, list[str]] = {}
        for sid, lvl in levels.items():
            grouped.setdefault(lvl, []).append(sid)
        return [grouped[i] for i in sorted(grouped)]

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
