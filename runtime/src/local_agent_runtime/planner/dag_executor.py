from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from ..observability.tracer import Tracer
from ..orchestration.partial_handoff import (
    build_continuation_prompt,
    handoff_allows_continuation,
    partial_handoff_from_dispatch_result,
    summarize_partial_handoff,
)
from ..services.subagent_service import SubagentService
from .types import (
    PlanResult,
    Subtask,
    build_subtask_prompt,
    child_tool_allowlist_for_agent,
    normalize_subtask_agent_type,
)

logger = logging.getLogger(__name__)

# Type alias for the scope overlap checker callable.
# Receives a list of subtask dicts (each with id + ownedScope/writeScope),
# returns a list of overlap reason strings (empty = no overlaps).
ScopeChecker = Callable[[list[dict[str, Any]]], list[str]]


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
        skill_id: str | None = None,
        mcp_policy: dict[str, Any] | None = None,
        active_worktree: dict[str, Any] | None = None,
        max_workers: int = 4,
        parent_goal: str | None = None,
        child_timeout_ms: int | None = None,
        is_paused_fn: Callable[[], bool] | None = None,
        completed_ids: set[str] | None = None,
        failed_ids: set[str] | None = None,
        prior_results: dict[str, str] | None = None,
        tracer: Tracer | None = None,
        on_subtask_callback: Callable[[str, str, dict[str, Any]], None] | None = None,
        scope_checker: ScopeChecker | None = None,
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
        partial_handoffs: list[dict[str, Any]] = []
        control_state: dict[str, Any] = {}
        lock = threading.Lock()

        # Build index once to avoid O(N) linear scans
        subtask_index: dict[str, Subtask] = {s.id: s for s in plan.subtasks}
        levels = self._group_by_level(plan, subtask_index)
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
                subtask = subtask_index.get(subtask_id)
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

            # Check for scope overlaps among parallel candidates
            force_serial = False
            if scope_checker is not None and len(runnable) > 1:
                scope_subtasks = []
                for sid in runnable:
                    st = subtask_index.get(sid)
                    if st is None:
                        continue
                    scope_subtasks.append({
                        "id": st.id,
                        "ownedScope": getattr(st, "owned_scope", None) or getattr(st, "write_scope", None),
                    })
                overlaps = scope_checker(scope_subtasks)
                if overlaps:
                    force_serial = True
                    logger.info(
                        "Level %d: scope overlaps detected, downgrading to serial: %s",
                        level_idx, overlaps,
                    )

            # Execute level — parallel if multiple tasks, serial if single or scope conflict
            if len(runnable) == 1 or force_serial:
                for sid in runnable:
                    self._execute_subtask(
                        subtask_index, sid, completed, failed, results,
                        session_id, parent_task_id, lock,
                        skill_id=skill_id,
                        mcp_policy=mcp_policy,
                        active_worktree=active_worktree,
                        parent_goal=parent_goal,
                        child_timeout_ms=child_timeout_ms,
                        partial_handoffs=partial_handoffs,
                        control_state=control_state,
                        tracer=tracer,
                        on_subtask_callback=on_subtask_callback,
                    )
                    if control_state.get("status") == "waiting_approval":
                        break
            else:
                logger.info("Level %d: executing %d subtasks in parallel", level_idx, len(runnable))
                with ThreadPoolExecutor(max_workers=min(max_workers, len(runnable))) as pool:
                    futures = {
                        pool.submit(
                            self._execute_subtask,
                            subtask_index, sid, completed, failed, results,
                            session_id, parent_task_id, lock,
                            skill_id=skill_id,
                            mcp_policy=mcp_policy,
                            active_worktree=active_worktree,
                            parent_goal=parent_goal,
                            child_timeout_ms=child_timeout_ms,
                            partial_handoffs=partial_handoffs,
                            control_state=control_state,
                            tracer=tracer,
                            on_subtask_callback=on_subtask_callback,
                        ): sid
                        for sid in runnable
                    }
                    for future in as_completed(futures):
                        # Result already captured inside _execute_subtask
                        # via shared completed/failed/results sets
                        _ = future.result()  # propagate exceptions if any

            if control_state.get("status") == "waiting_approval":
                if dag_span is not None:
                    tracer.end_span(
                        dag_span.span_id,
                        status="ok",
                        attributes={
                            "paused": True,
                            "status": "waiting_approval",
                            "subtask_id": control_state.get("subtaskId"),
                        },
                    )
                return {
                    "subtasks": plan.subtasks,
                    "summary": "",
                    "success": None,
                    "completed": list(completed),
                    "failed": list(failed),
                    "paused": True,
                    "status": "waiting_approval",
                    "waitingApproval": True,
                    "waitingSubtaskId": control_state.get("subtaskId"),
                    "results": dict(results),
                    "partialHandoffs": list(partial_handoffs),
                }

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
                    "partialHandoffs": list(partial_handoffs),
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
            "partialHandoffs": list(partial_handoffs),
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
        subtask_index: dict[str, Subtask],
        subtask_id: str,
        completed: set[str],
        failed: set[str],
        results: dict[str, str],
        session_id: str,
        parent_task_id: str,
        lock: threading.Lock,
        skill_id: str | None = None,
        mcp_policy: dict[str, Any] | None = None,
        active_worktree: dict[str, Any] | None = None,
        parent_goal: str | None = None,
        child_timeout_ms: int | None = None,
        partial_handoffs: list[dict[str, Any]] | None = None,
        control_state: dict[str, Any] | None = None,
        tracer: Tracer | None = None,
        on_subtask_callback: Callable[[str, str, dict[str, Any]], None] | None = None,
    ) -> None:
        """Execute a single subtask and update shared state."""
        subtask = subtask_index.get(subtask_id)
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
        _subtask_t0 = None
        if on_subtask_callback is not None:
            _subtask_t0 = __import__("time").monotonic()
            on_subtask_callback(subtask_id, "started", {"subtaskId": subtask_id, "subtaskTitle": subtask.title})
        description = subtask.description
        max_continuations = 2
        continuation_attempts = 0
        child_tool_allowlist = self._child_tool_allowlist_for_subtask(
            subtask=subtask,
            skill_id=skill_id,
        )
        child_can_write = self._child_allowlist_can_write(child_tool_allowlist)
        child_active_worktree = (
            dict(active_worktree)
            if isinstance(active_worktree, dict) and not child_can_write
            else None
        )
        try:
            for _attempt in range(max_continuations + 1):
                dispatch_result = self._subagent.dispatch({
                    "prompt": build_subtask_prompt(
                        parent_goal=parent_goal,
                        subtask=subtask,
                        prompt_override=description,
                        completed_context=results,
                        compact=True,
                    ),
                    "planningPrompt": description,
                    "title": subtask.title,
                    "sessionId": session_id,
                    "taskId": parent_task_id,
                    **({"skillId": skill_id} if isinstance(skill_id, str) and skill_id.strip() else {}),
                    "agentType": normalize_subtask_agent_type(subtask.agent_type),
                    "childToolAllowlist": child_tool_allowlist,
                    "profile": {
                        "ownedScope": list(subtask.owned_scope),
                        "expectedArtifacts": [dict(item) for item in subtask.expected_artifacts],
                        "verificationRequirements": [dict(item) for item in subtask.verification_requirements],
                    },
                    **({"mcpPolicy": dict(mcp_policy)} if isinstance(mcp_policy, dict) else {}),
                    **({"activeWorktree": child_active_worktree} if child_active_worktree is not None else {}),
                    **({"timeoutMs": child_timeout_ms} if child_timeout_ms is not None else {}),
                    **(
                        {
                            "_eventCallback": (
                                lambda details, sid=subtask_id: on_subtask_callback(sid, "progress", details)
                            )
                        }
                        if on_subtask_callback is not None
                        else {}
                    ),
                })
                dispatch_status = str(dispatch_result.get("status") or "").strip().lower()
                if dispatch_status == "waiting_approval":
                    message = str(
                        dispatch_result.get("summary")
                        or "Child worker is waiting for parent approval."
                    ).strip()
                    with lock:
                        subtask.status = "waiting_approval"
                        subtask.result = message
                        results[subtask.id] = message
                        if control_state is not None:
                            control_state["status"] = "waiting_approval"
                            control_state["subtaskId"] = subtask.id
                            control_state["summary"] = message
                    if on_subtask_callback is not None and _subtask_t0 is not None:
                        duration_ms = int((__import__("time").monotonic() - _subtask_t0) * 1000)
                        on_subtask_callback(subtask_id, "completed", {
                            "subtaskId": subtask_id, "subtaskTitle": subtask.title,
                            "status": "waiting_approval", "duration_ms": duration_ms,
                        })
                    if span is not None:
                        tracer.end_span(span.span_id, status="ok", attributes={"status": "waiting_approval"})
                    return
                if dispatch_status != "failed":
                    with lock:
                        subtask.status = "completed"
                        subtask.result = dispatch_result.get("summary") or "Completed"
                        completed.add(subtask.id)
                        results[subtask.id] = subtask.result
                    break

                error = dispatch_result.get("error") if isinstance(dispatch_result.get("error"), dict) else {}
                partial_handoff = partial_handoff_from_dispatch_result(dispatch_result)
                message = str(
                    error.get("message")
                    or dispatch_result.get("summary")
                    or "Child subtask failed."
                ).strip()
                if partial_handoff:
                    partial_handoff["subtaskId"] = subtask.id
                    partial_handoff["subtaskTitle"] = subtask.title
                    if partial_handoffs is not None:
                        partial_handoffs.append(partial_handoff)
                    handoff_summary = summarize_partial_handoff(partial_handoff)
                    if handoff_summary:
                        message = f"{message}\n{handoff_summary}"
                    if continuation_attempts < max_continuations and handoff_allows_continuation(partial_handoff):
                        description = build_continuation_prompt(
                            original_description=subtask.description,
                            handoff=partial_handoff,
                        )
                        continuation_attempts += 1
                        continue
                raise RuntimeError(message)
            if on_subtask_callback is not None and _subtask_t0 is not None:
                duration_ms = int((__import__("time").monotonic() - _subtask_t0) * 1000)
                on_subtask_callback(subtask_id, "completed", {
                    "subtaskId": subtask_id, "subtaskTitle": subtask.title,
                    "status": "completed", "duration_ms": duration_ms,
                })
            if span is not None:
                tracer.end_span(span.span_id, status="ok")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Subtask %s failed: %s", subtask_id, exc)
            with lock:
                subtask.status = "failed"
                subtask.result = str(exc)
                failed.add(subtask.id)
                results[subtask.id] = f"Failed: {exc}"
            if on_subtask_callback is not None and _subtask_t0 is not None:
                duration_ms = int((__import__("time").monotonic() - _subtask_t0) * 1000)
                on_subtask_callback(subtask_id, "completed", {
                    "subtaskId": subtask_id, "subtaskTitle": subtask.title,
                    "status": "failed", "duration_ms": duration_ms, "error": str(exc),
                })
            if span is not None:
                tracer.end_span(span.span_id, status="error", attributes={"error": str(exc)})

    @staticmethod
    def _child_tool_allowlist_for_subtask(*, subtask: Subtask, skill_id: str | None) -> list[str]:
        allowlist = list(child_tool_allowlist_for_agent(subtask.agent_type))
        if isinstance(skill_id, str) and skill_id.strip():
            allowlist.append("mcp__*")
        return list(dict.fromkeys(allowlist))

    @staticmethod
    def _child_allowlist_can_write(allowlist: list[str]) -> bool:
        return bool(set(allowlist) & {"write_file", "apply_patch", "run_command"})

    def _group_by_level(
        self,
        plan: PlanResult,
        subtask_index: dict[str, Subtask] | None = None,
    ) -> list[list[str]]:
        """Group execution_order into dependency levels for parallel execution.

        Level 0: tasks with no dependencies.
        Level N: tasks whose dependencies are all at level < N.
        Tasks within the same level can be executed in parallel.
        """
        if subtask_index is None:
            subtask_index = {s.id: s for s in plan.subtasks}
        levels: dict[str, int] = {}
        for sid in plan.execution_order:
            subtask = subtask_index.get(sid)
            if subtask and subtask.dependencies:
                levels[sid] = max(levels.get(d, 0) for d in subtask.dependencies) + 1
            else:
                levels[sid] = 0

        grouped: dict[int, list[str]] = {}
        for sid, lvl in levels.items():
            grouped.setdefault(lvl, []).append(sid)
        return [grouped[i] for i in sorted(grouped)]

    @staticmethod
    def _check_dependencies(subtask: Subtask, completed: set[str]) -> bool:
        return all(dep in completed for dep in subtask.dependencies)

    @staticmethod
    def _has_failed_dependency(subtask: Subtask, failed: set[str]) -> bool:
        return bool(set(subtask.dependencies) & failed)


def _count_by_status(subtasks: list[Subtask], status: str) -> int:
    return sum(1 for s in subtasks if s.status == status)
