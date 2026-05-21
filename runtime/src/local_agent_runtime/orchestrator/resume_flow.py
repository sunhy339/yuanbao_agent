from __future__ import annotations

import json
import logging
from copy import deepcopy
from typing import Any

logger = logging.getLogger(__name__)


class ResumeFlowMixin:
    def resume_task(self, params: dict[str, Any]) -> dict[str, Any]:
        task = self._store.get_task({"taskId": params["taskId"]})["task"]
        self._validate_task_transition(task["status"], "running", task["id"])

        span = self._tracer.start_span("task_resume", trace_id=task.get("id", ""))

        # Check DAG paused state first
        dag_state = self._load_pending_dag_state(task["id"])
        if dag_state is not None:
            running_task = self._store.update_task_status(task_id=task["id"], status="running")
            self._publish(
                session_id=running_task["sessionId"],
                task=running_task,
                event_type="task.resumed",
                payload={"status": "running", "detail": "Resuming paused DAG execution."},
            )
            self._fire_hooks("on_task_resume", running_task["sessionId"], running_task, extra_context={"resumePath": "dag"})
            resumed_task = self._resume_dag_execution(task=running_task, state=dag_state)
            self._tracer.end_span(span.span_id, status="ok", attributes={"path": "dag"})
            return {"task": resumed_task}

        pending_state = self._load_pending_react_state(task["id"])
        if pending_state is not None:
            # Cooperative pause: no pending tool call → resume ReAct loop directly
            is_cooperative = not pending_state.get("pending_tool_call")
            if is_cooperative:
                running_task = self._store.update_task_status(task_id=task["id"], status="running")
                self._publish(
                    session_id=running_task["sessionId"],
                    task=running_task,
                    event_type="task.resumed",
                    payload={"status": "running", "detail": "Resuming cooperative paused ReAct task."},
                )
                self._fire_hooks("on_task_resume", running_task["sessionId"], running_task, extra_context={"resumePath": "react_cooperative"})
                resumed_task = self._resume_cooperative_react(task=running_task, state=pending_state)
                self._tracer.end_span(span.span_id, status="ok", attributes={"path": "react_cooperative"})
                return {"task": resumed_task}

            approval = self._latest_approval_for_task(task["id"])
            if approval is not None and approval.get("decision") == "approved":
                running_task = self._store.update_task_status(task_id=task["id"], status="running")
                self._publish(
                    session_id=running_task["sessionId"],
                    task=running_task,
                    event_type="task.resumed",
                    payload={"status": "running", "detail": "Resuming approved pending ReAct task."},
                )
                self._fire_hooks("on_task_resume", running_task["sessionId"], running_task, extra_context={"resumePath": "react_approved"})
                resumed_task = self._resume_react_after_approval(task=running_task, approval=approval)
                self._tracer.end_span(span.span_id, status="ok", attributes={"path": "react_approved"})
                return {"task": resumed_task}
            if approval is not None and approval.get("decision") == "rejected":
                self._publish(
                    session_id=task["sessionId"],
                    task=task,
                    event_type="task.resumed",
                    payload={"status": "running", "detail": "Resuming rejected pending ReAct task."},
                )
                failed_task = self._fail_task(
                    session_id=task["sessionId"],
                    task=task,
                    summary="Approval was rejected by the user.",
                    error_code="APPROVAL_REJECTED",
                )
                self._tracer.end_span(span.span_id, status="ok", attributes={"path": "react_rejected"})
                return {"task": failed_task}

            self._validate_task_transition(task["status"], "waiting_approval", task["id"])
            waiting_task = self._store.update_task_status(task_id=task["id"], status="waiting_approval")
            self._publish(
                session_id=waiting_task["sessionId"],
                task=waiting_task,
                event_type="task.resumed",
                payload={"status": "waiting_approval", "detail": "Pending ReAct task is waiting for approval."},
            )
            self._tracer.end_span(span.span_id, status="ok", attributes={"path": "react_pending_approval"})
            return {"task": waiting_task}

        running_task = self._store.update_task_status(task_id=task["id"], status="running")
        self._publish(
            session_id=running_task["sessionId"],
            task=running_task,
            event_type="task.resumed",
            payload={"status": running_task["status"]},
        )
        self._fire_hooks("on_task_resume", running_task["sessionId"], running_task, extra_context={"resumePath": "fallback"})
        self._tracer.end_span(span.span_id, status="ok", attributes={"path": "fallback"})
        return {"task": running_task}

    def _resume_cooperative_react(self, task: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
        """Resume a cooperatively paused ReAct loop from its saved checkpoint."""
        try:
            react_result = self._run_react_loop(
                session_id=state["session_id"],
                task=task,
                goal=state["goal"],
                context=state["context"],
                state=state,
            )
            if react_result["status"] == "paused":
                return task  # paused again
            if react_result["status"] == "waiting_approval":
                return task
            summary = react_result.get("summary") or self._provider.summarize_findings(
                goal=state["goal"],
                context=state["context"],
                tool_results=react_result.get("tool_results", []),
            )
            if not react_result.get("assistant_output_published"):
                self._publish(
                    session_id=state["session_id"],
                    task=task,
                    event_type="assistant.token",
                    payload={"delta": summary},
                )
            return self._complete_task(
                session_id=state["session_id"],
                task=task,
                summary=summary,
                context=state["context"],
                tool_results=react_result.get("tool_results", []),
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("Cooperative resume failed for task=%s: %s", task["id"], exc, exc_info=True)
            return self._fail_task(
                session_id=state["session_id"],
                task=task,
                summary=str(exc),
                error_code="RESUME_FAILED",
            )

    # ------------------------------------------------------------------
    # DAG pause/resume helpers
    # ------------------------------------------------------------------

    def _load_pending_dag_state(self, task_id: str) -> dict[str, Any] | None:
        if hasattr(self._store, "get_pending_dag_state"):
            return self._store.get_pending_dag_state(task_id)
        return None

    def _clear_pending_dag_state(self, task_id: str) -> None:
        if hasattr(self._store, "delete_pending_dag_state"):
            self._store.delete_pending_dag_state(task_id)

    def _resume_dag_execution(self, task: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
        """Resume a paused DAG execution from its saved checkpoint."""
        try:
            from ..planner.types import PlanResult, Subtask as PlanSubtask

            plan_data = state["plan"]
            subtasks = [
                PlanSubtask(
                    id=s["id"], title=s["title"], description=s["description"],
                    dependencies=s["dependencies"], status=s.get("status", "pending"),
                    result=s.get("result"),
                )
                for s in plan_data["subtasks"]
            ]
            plan = PlanResult(
                subtasks=subtasks,
                dag=plan_data["dag"],
                execution_order=plan_data["execution_order"],
            )

            execution = self._dag_executor.execute(
                plan,
                session_id=state["session_id"],
                parent_task_id=task["id"],
                max_workers=self._max_parallel_subtasks(state.get("context") or {}),
                parent_goal=state.get("goal"),
                child_timeout_ms=self._child_subtask_timeout_ms(state.get("context") or {}),
                is_paused_fn=lambda: self._store.get_task({"taskId": task["id"]})["task"]["status"] == "paused",
                completed_ids=set(state["completed"]),
                failed_ids=set(state["failed"]),
                prior_results=state["results"],
                tracer=self._tracer,
            )

            if execution.get("paused"):
                # Paused again — update persisted state
                updated_plan_data = {
                    "subtasks": [
                        {"id": s.id, "title": s.title, "description": s.description,
                         "dependencies": s.dependencies, "status": s.status, "result": s.result}
                        for s in plan.subtasks
                    ],
                    "dag": plan.dag,
                    "execution_order": plan.execution_order,
                }
                if hasattr(self._store, "upsert_pending_dag_state"):
                    self._store.upsert_pending_dag_state(
                        task_id=task["id"],
                        session_id=state["session_id"],
                        goal=state["goal"],
                        context=state["context"],
                        plan_json=json.dumps(updated_plan_data, ensure_ascii=False),
                        completed_ids=execution["completed"],
                        failed_ids=execution["failed"],
                        results=execution.get("results", {}),
                    )
                return task

            # Completed — clean up and finalize
            self._clear_pending_dag_state(task["id"])

            # Pre-merge git diff check
            merge_check = self._check_git_diff_before_merge(
                session_id=state["session_id"],
                task=task,
                execution=execution,
                workspace_root=(state.get("context") or {}).get("workspace_root"),
            )
            if not merge_check["safe"]:
                logger.warning(
                    "Pre-merge check found issues for resumed task=%s: %s",
                    task["id"], merge_check["warnings"],
                )

            coverage = self._coverage_evaluator.evaluate(state["goal"], execution["subtasks"])
            summary = execution["summary"]

            self._publish(
                session_id=state["session_id"], task=task,
                event_type="task.planning.completed",
                payload={"coverage": coverage, "success": execution["success"]},
            )

            return self._complete_task(
                session_id=state["session_id"],
                task=task,
                summary=summary,
                context=state["context"],
                force_complete_after_review=True,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("DAG resume failed for task=%s: %s", task["id"], exc, exc_info=True)
            self._clear_pending_dag_state(task["id"])
            return self._fail_task(
                session_id=state["session_id"],
                task=task,
                summary=str(exc),
                error_code="DAG_RESUME_FAILED",
            )

    def _cleanup_orphan_tasks(self) -> None:
        """Reset tasks stuck in running state from a crashed previous process.

        Paused tasks are preserved — they hold persisted state that allows
        resumption after a process restart.
        """
        if not hasattr(self._store, "list_tasks_by_status"):
            return
        # Only clean up running tasks; paused tasks retain their persisted state
        orphaned = self._store.list_tasks_by_status(["running"])
        for task in orphaned:
            self._store.update_task(
                task_id=task["id"],
                status="failed",
                summary="任务因进程重启而中断",
                error_code="ORPHAN_CLEANUP",
            )
            self._publish(
                session_id=task.get("sessionId", ""),
                task=task,
                event_type="task.orphaned",
                payload={"previousStatus": task["status"], "reason": "Process restart"},
            )
        if orphaned:
            logger.info("Cleaned up %d orphan tasks", len(orphaned))

    def _save_pending_react_state(self, task_id: str, state: dict[str, Any]) -> None:
        normalized_state = {
            "session_id": state["session_id"],
            "goal": state["goal"],
            "context": deepcopy(state["context"]),
            "messages": deepcopy(state["messages"]),
            "tool_results": deepcopy(state["tool_results"]),
            "steps": int(state["steps"]),
            "react_started": bool(state["react_started"]),
            "pending_tool_call": deepcopy(state["pending_tool_call"]),
            "pending_tool_spec": deepcopy(state["pending_tool_spec"]),
            "remaining_tool_calls": deepcopy(state.get("remaining_tool_calls", [])),
        }
        self._pending_react_tasks[task_id] = normalized_state
        if hasattr(self._store, "upsert_pending_react_state"):
            self._store.upsert_pending_react_state(
                task_id=task_id,
                session_id=normalized_state["session_id"],
                goal=normalized_state["goal"],
                context=normalized_state["context"],
                messages=normalized_state["messages"],
                tool_results=normalized_state["tool_results"],
                pending_tool_call=normalized_state["pending_tool_call"],
                pending_tool_spec=normalized_state["pending_tool_spec"],
                remaining_tool_calls=normalized_state["remaining_tool_calls"],
                steps=normalized_state["steps"],
                react_started=normalized_state["react_started"],
            )

    def _load_pending_react_state(self, task_id: str) -> dict[str, Any] | None:
        if hasattr(self._store, "get_pending_react_state"):
            state = self._store.get_pending_react_state(task_id)
            if state is not None:
                normalized_state = {
                    "session_id": state["session_id"],
                    "goal": state["goal"],
                    "context": deepcopy(state["context"]),
                    "messages": deepcopy(state["messages"]),
                    "tool_results": deepcopy(state["tool_results"]),
                    "steps": int(state["steps"]),
                    "react_started": bool(state["react_started"]),
                    "pending_tool_call": deepcopy(state["pending_tool_call"]),
                    "pending_tool_spec": deepcopy(state["pending_tool_spec"]),
                    "remaining_tool_calls": deepcopy(state.get("remaining_tool_calls", [])),
                }
                self._pending_react_tasks[task_id] = normalized_state
                return normalized_state
        return self._pending_react_tasks.get(task_id)

    def _clear_pending_react_state(self, task_id: str) -> None:
        self._pending_react_tasks.pop(task_id, None)
        if hasattr(self._store, "delete_pending_react_state"):
            self._store.delete_pending_react_state(task_id)

    def _latest_approval_for_task(self, task_id: str) -> dict[str, Any] | None:
        if hasattr(self._store, "find_latest_approval"):
            return self._store.find_latest_approval(task_id=task_id)
        return None

