"""Child Task Mixin â extracted from Orchestrator.

Handles child task execution and worker management.
"""
from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

from ..services.worker_budget import WorkerBudget, WorkerBudgetExceededError
from ..services.worker_environment import normalize_child_tool_allowlist
from ..tools.registry import BUILTIN_TOOL_SCHEMAS


class ChildTaskMixin:
    """Mixin providing child task execution and worker management."""

    def run_child_task(self, params: dict[str, Any]) -> dict[str, Any]:

        session_id = params.get("sessionId")
        prompt = params.get("prompt")
        collaboration_task_id = params.get("collaborationTaskId") or params.get("collaboration_task_id")
        if not isinstance(session_id, str) or not session_id.strip():
            raise ValueError("sessionId is required")
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("prompt is required")
        if collaboration_task_id is not None and not isinstance(collaboration_task_id, str):
            raise ValueError("collaborationTaskId must be a string")

        span = self._tracer.start_span(
            "child_task",
            attributes={"prompt": prompt[:200]},
        )

        session = self._store.require_session(session_id)
        budget = WorkerBudget.from_metadata(params.get("budget"), params)
        child_role = params.get("agentType", "worker")
        context = self._context_builder.build(session_id=session["id"], goal=prompt.strip(), lightweight=False, role=child_role)
        context = self._context_with_worker_budget(context, budget)
        plan = self._planner.plan(prompt.strip(), context=context)
        task = self._store.create_task(
            session_id=session["id"],
            task_type="subagent",
            goal=prompt.strip(),
            plan=plan,
            acceptance_criteria=self._default_acceptance_criteria(prompt.strip()),
            out_of_scope=self._default_out_of_scope(),
            role=child_role,
            routing={
                "childCollaborationTaskId": collaboration_task_id,
                "parentRuntimeTaskId": params.get("parentRuntimeTaskId"),
            } if collaboration_task_id else None,
        )
        runtime_task = {**task, "plan": plan}
        context = self._context_with_task_focus(context, runtime_task)

        self._publish(
            session_id=session["id"],
            task=runtime_task,
            event_type="task.started",
            payload={
                "status": runtime_task["status"],
                "plan": runtime_task["plan"],
                "currentStep": runtime_task.get("currentStep"),
                "context": context,
                "childWorker": True,
            },
        )

        try:
            react_result = self._run_react_loop(
                session_id=session["id"],
                task=runtime_task,
                goal=prompt.strip(),
                context=context,
                budget=budget,
            )
            if react_result["status"] == "waiting_approval":
                self._tracer.end_span(span.span_id, status="ok", attributes={"status": "waiting_approval"})
                return self._waiting_child_task_response(
                    task=runtime_task,
                    summary="Child worker is waiting for parent approval.",
                    budget=budget,
                )
            if react_result["status"] == "completed":
                completed_task = self._complete_task(
                    session_id=session["id"],
                    task=runtime_task,
                    summary=react_result["summary"],
                    context=context,
                    tool_results=react_result.get("tool_results", []),
                    skip_drain=True,
                )
                self._tracer.end_span(span.span_id, status="ok")
                return {
                    "status": "completed",
                    "task": completed_task,
                    "summary": completed_task.get("resultSummary") or react_result["summary"],
                    "budget": budget.to_metadata(),
                }

            tool_results = self._run_minimal_loop(
                session_id=session["id"],
                task=runtime_task,
                goal=prompt.strip(),
                context=context,
                budget=budget,
            )
            if runtime_task["status"] == "waiting_approval":
                self._tracer.end_span(span.span_id, status="ok", attributes={"status": "waiting_approval"})
                return self._waiting_child_task_response(
                    task=runtime_task,
                    summary="Child worker is waiting for parent approval.",
                    budget=budget,
                )
            summary = self._provider.summarize_findings(
                goal=prompt.strip(),
                context=context,
                tool_results=tool_results,
            )
            completed_task = self._complete_task(
                session_id=session["id"],
                task=runtime_task,
                summary=summary,
                context=context,
                tool_results=tool_results,
                skip_drain=True,
            )
            self._tracer.end_span(span.span_id, status="ok")
            return {
                "status": "completed",
                "task": completed_task,
                "summary": completed_task.get("resultSummary") or summary,
                "budget": budget.to_metadata(),
            }
        except Exception as exc:  # noqa: BLE001
            logger.error("Worker task execution failed: %s", exc, exc_info=True)
            self._tracer.end_span(span.span_id, status="error", attributes={"error": str(exc)})
            self._fail_task(
                session_id=session["id"],
                task=runtime_task,
                summary=str(exc),
                error_code=str(getattr(exc, "code", "LOOP_EXECUTION_FAILED")),
                skip_drain=True,
            )
            raise

    def _waiting_child_task_response(
        self,
        *,
        task: dict[str, Any],
        summary: str,
        budget: WorkerBudget,
    ) -> dict[str, Any]:
        persisted_task = self._store.get_task({"taskId": task["id"]})["task"]
        approval = self._latest_pending_approval(task["id"])
        return {
            "status": "waiting_approval",
            "task": persisted_task,
            "summary": summary,
            "approval": approval,
            "budget": budget.to_metadata(),
        }

    def _latest_pending_approval(self, task_id: str) -> dict[str, Any] | None:
        if not hasattr(self._store, "_conn"):
            return None
        row = self._store._conn.execute(  # noqa: SLF001
            """
            SELECT *
            FROM approvals
            WHERE task_id = ? AND decision IS NULL
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (task_id,),
        ).fetchone()
        if row is None:
            return None
        return self._store._serialize_approval(dict(row))  # noqa: SLF001

    def _context_tool_schemas(self) -> list[dict[str, Any]] | None:
        allowed = self._child_tool_allowlist()
        if allowed is None:
            return None
        allowed_set = set(allowed)
        return [schema for schema in BUILTIN_TOOL_SCHEMAS if schema.get("name") in allowed_set]

    def _child_tool_allowlist(self) -> list[str] | None:
        raw = os.environ.get("LOCAL_AGENT_CHILD_TOOL_ALLOWLIST")
        if raw is None:
            return None
        return normalize_child_tool_allowlist(raw)
