"""React Resume Mixin — extracted from ReactRunnerMixin.

Handles resuming the ReAct loop after approval, child collaboration
finalization, and related collaboration task lookups.
"""
from __future__ import annotations

import json
import logging
from copy import deepcopy
from typing import Any

logger = logging.getLogger(__name__)


class ReactResumeMixin:
    """Mixin providing resume-after-approval and child collaboration helpers."""

    def _resume_react_after_approval(self, task: dict[str, Any], approval: dict[str, Any]) -> dict[str, Any]:
        state = self._load_pending_react_state(task["id"])
        if state is None:
            return task

        runtime_task = {**task, "plan": task.get("plan") or []}
        try:
            pending_spec = deepcopy(state["pending_tool_spec"])
            pending_spec["arguments"] = {
                **pending_spec.get("arguments", {}),
                "approvalId": approval["id"],
            }
            tool_result = self._execute_tool(
                session_id=task["sessionId"],
                task=runtime_task,
                tool_spec=pending_spec,
                budget=None,
            )
            runtime_task = self._latest_runtime_task_snapshot(runtime_task)
            self._ensure_tool_result_operation(pending_spec, tool_result)
            if runtime_task["status"] == "waiting_approval":
                state["pending_tool_spec"] = pending_spec
                self._save_pending_react_state(task["id"], state)
                return runtime_task

            state["tool_results"].append(tool_result)
            state["messages"].append(self._tool_result_message(state["pending_tool_call"], tool_result))
            self._advance_after_tool(session_id=task["sessionId"], task=runtime_task, tool_spec=pending_spec)

            for index, tool_call in enumerate(list(state.get("remaining_tool_calls", []))):
                if state.get("tool_results"):
                    updated_tool_call = self._annotate_tool_call_with_completed_results(tool_call, state["tool_results"])
                    if updated_tool_call is not tool_call:
                        self._sync_tool_metadata(updated_tool_call, tool_call)
                tool_spec = self._provider_tool_call_to_spec(tool_call, state["context"])
                tool_result = self._execute_tool(
                    session_id=task["sessionId"],
                    task=runtime_task,
                    tool_spec=tool_spec,
                    budget=None,
                )
                runtime_task = self._latest_runtime_task_snapshot(runtime_task)
                self._ensure_tool_result_operation(tool_spec, tool_result)
                if runtime_task["status"] == "waiting_approval":
                    state["pending_tool_call"] = tool_call
                    state["pending_tool_spec"] = tool_spec
                    state["remaining_tool_calls"] = state.get("remaining_tool_calls", [])[index + 1 :]
                    self._save_pending_react_state(task["id"], state)
                    return runtime_task

                state["tool_results"].append(tool_result)
                state["messages"].append(self._tool_result_message(tool_call, tool_result))
                self._advance_after_tool(session_id=task["sessionId"], task=runtime_task, tool_spec=tool_spec)

            state["remaining_tool_calls"] = []
            result = self._run_react_loop(
                session_id=task["sessionId"],
                task=runtime_task,
                goal=state["goal"],
                context=state["context"],
                state=state,
                budget=None,
            )
            if result["status"] == "completed":
                return self._complete_task(
                    session_id=task["sessionId"],
                    task=runtime_task,
                    summary=result["summary"],
                    context=state["context"],
                    tool_results=result.get("tool_results", []),
                )
            return self._latest_runtime_task_snapshot(runtime_task)
        except Exception as exc:  # noqa: BLE001
            logger.error("Resume after approval failed for task=%s: %s", task["id"], exc, exc_info=True)
            return self._fail_task(
                session_id=task["sessionId"],
                task=runtime_task,
                summary=str(exc),
                error_code="LOOP_EXECUTION_FAILED",
            )

    def _latest_runtime_task_snapshot(self, runtime_task: dict[str, Any]) -> dict[str, Any]:
        task_id = runtime_task.get("id")
        if not isinstance(task_id, str) or not task_id:
            return runtime_task
        try:
            latest = self._store.get_task({"taskId": task_id})["task"]
        except Exception:  # noqa: BLE001
            return runtime_task
        if latest.get("plan") is None and runtime_task.get("plan") is not None:
            latest = {**latest, "plan": runtime_task.get("plan") or []}
        return latest

    def _finalize_child_collaboration_after_approval(
        self,
        *,
        approval: dict[str, Any],
        runtime_task: dict[str, Any],
    ) -> None:
        child_task = self._blocked_child_collaboration_for_runtime_task(approval=approval, runtime_task=runtime_task)
        if child_task is None:
            return
        worker = self._prepare_child_collaboration_worker(child_task)
        if worker is None:
            return

        status = runtime_task.get("status")
        summary = (
            runtime_task.get("resultSummary")
            or (runtime_task.get("result") or {}).get("summary")
            or ("Child worker completed after approval." if status == "completed" else "Child worker stopped after approval.")
        )
        result_payload = {
            "summary": summary,
            "runtimeTaskId": runtime_task.get("id"),
            "runtimeTaskStatus": status,
            "approval": deepcopy(approval),
        }
        if isinstance(runtime_task.get("result"), dict):
            result_payload["runtimeTaskResult"] = deepcopy(runtime_task["result"])

        if status == "completed":
            completion = self._collaboration_service.complete_collaboration_task(
                {
                    "taskId": child_task["id"],
                    "workerId": worker["id"],
                    "result": result_payload,
                }
            )
            self._collaboration_service.send_agent_message(
                {
                    "senderWorkerId": completion["worker"]["id"],
                    "taskId": completion["task"]["id"],
                    "kind": "result",
                    "body": str(summary),
                    "payload": {
                        "executionMode": "process-rpc",
                        "approval": deepcopy(approval),
                        "runtimeTask": deepcopy(runtime_task),
                    },
                }
            )
            parent_task_id = self._parent_runtime_task_id_from_child(child_task)
            if parent_task_id:
                try:
                    parent = self._store.get_task({"taskId": parent_task_id})["task"]
                    pending_dag_state = self._load_pending_dag_state(parent_task_id)
                    if parent.get("status") == "waiting_approval" and pending_dag_state is not None:
                        pending_dag_state = self._mark_child_dag_subtask_completed(
                            parent_task_id=parent_task_id,
                            state=pending_dag_state,
                            child_task=child_task,
                            summary=str(summary),
                        )
                        parent = self._ensure_task_running_after_approval(
                            task=parent,
                            detail="Child approval completed; resuming parent DAG.",
                        )
                        self._resume_dag_execution(parent, pending_dag_state)
                    elif parent.get("status") == "running" and pending_dag_state is not None:
                        self._mark_child_dag_subtask_completed(
                            parent_task_id=parent_task_id,
                            state=pending_dag_state,
                            child_task=child_task,
                            summary=str(summary),
                        )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Parent DAG resume after child approval failed for task=%s: %s", parent_task_id, exc)
            return

        if status in {"failed", "cancelled"}:
            error = {
                "code": runtime_task.get("errorCode") or "CHILD_WORKER_APPROVAL_RESUME_FAILED",
                "message": str(summary),
                "type": "ChildApprovalResumeError",
                "approval": deepcopy(approval),
                "runtimeTaskId": runtime_task.get("id"),
            }
            failure = self._collaboration_service.fail_collaboration_task(
                {
                    "taskId": child_task["id"],
                    "workerId": worker["id"],
                    "error": error,
                }
            )
            self._collaboration_service.send_agent_message(
                {
                    "senderWorkerId": failure["worker"]["id"],
                    "taskId": failure["task"]["id"],
                    "kind": "result",
                    "body": str(summary),
                    "payload": {"error": error, "runtimeTask": deepcopy(runtime_task)},
                }
            )
            parent_task_id = self._parent_runtime_task_id_from_child(child_task)
            if parent_task_id:
                try:
                    parent = self._store.get_task({"taskId": parent_task_id})["task"]
                    pending_dag_state = self._load_pending_dag_state(parent_task_id)
                    if parent.get("status") == "waiting_approval" and pending_dag_state is not None:
                        pending_dag_state = self._mark_child_dag_subtask_failed(
                            parent_task_id=parent_task_id,
                            state=pending_dag_state,
                            child_task=child_task,
                            summary=str(summary),
                        )
                        parent = self._ensure_task_running_after_approval(
                            task=parent,
                            detail="Child approval failed; resuming parent DAG.",
                        )
                        self._resume_dag_execution(parent, pending_dag_state)
                    elif parent.get("status") == "running" and pending_dag_state is not None:
                        self._mark_child_dag_subtask_failed(
                            parent_task_id=parent_task_id,
                            state=pending_dag_state,
                            child_task=child_task,
                            summary=str(summary),
                        )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Parent DAG resume after child approval failure failed for task=%s: %s", parent_task_id, exc)
            return

        self._collaboration_service.update_collaboration_task(
            {
                "taskId": child_task["id"],
                "status": "blocked",
                "result": result_payload,
            }
        )

    def _parent_runtime_task_id_from_child(self, child_task: dict[str, Any]) -> str | None:
        parent_task_id = child_task.get("parentTaskId")
        if isinstance(parent_task_id, str) and parent_task_id.strip():
            return parent_task_id.strip()
        metadata = child_task.get("metadata") if isinstance(child_task.get("metadata"), dict) else {}
        parent_task_id = metadata.get("parentRuntimeTaskId")
        if isinstance(parent_task_id, str) and parent_task_id.strip():
            return parent_task_id.strip()
        return None

    def _mark_child_dag_subtask_completed(
        self,
        *,
        parent_task_id: str,
        state: dict[str, Any],
        child_task: dict[str, Any],
        summary: str,
    ) -> dict[str, Any]:
        plan = state.get("plan") if isinstance(state.get("plan"), dict) else {}
        subtasks = plan.get("subtasks") if isinstance(plan.get("subtasks"), list) else []
        child_title = str(child_task.get("title") or "").strip()
        completed = {str(item) for item in state.get("completed", [])}
        results = dict(state.get("results") if isinstance(state.get("results"), dict) else {})
        matched_id: str | None = None
        for subtask in subtasks:
            if not isinstance(subtask, dict):
                continue
            subtask_id = str(subtask.get("id") or "").strip()
            if not subtask_id:
                continue
            title = str(subtask.get("title") or "").strip()
            if subtask.get("status") == "waiting_approval" or (child_title and title == child_title):
                subtask["status"] = "completed"
                subtask["result"] = summary
                completed.add(subtask_id)
                results[subtask_id] = summary
                matched_id = subtask_id
                break
        if matched_id is None:
            return state
        failed = [item for item in state.get("failed", []) if str(item) != matched_id]
        if hasattr(self._store, "upsert_pending_dag_state"):
            self._store.upsert_pending_dag_state(
                task_id=parent_task_id,
                session_id=state["session_id"],
                goal=state["goal"],
                context=state["context"],
                plan_json=json.dumps(plan, ensure_ascii=False),
                completed_ids=sorted(completed),
                failed_ids=failed,
                results=results,
            )
            refreshed = self._load_pending_dag_state(parent_task_id)
            if refreshed is not None:
                return refreshed
        updated = dict(state)
        updated["plan"] = plan
        updated["completed"] = sorted(completed)
        updated["failed"] = failed
        updated["results"] = results
        return updated

    def _mark_child_dag_subtask_failed(
        self,
        *,
        parent_task_id: str,
        state: dict[str, Any],
        child_task: dict[str, Any],
        summary: str,
    ) -> dict[str, Any]:
        plan = state.get("plan") if isinstance(state.get("plan"), dict) else {}
        subtasks = plan.get("subtasks") if isinstance(plan.get("subtasks"), list) else []
        child_title = str(child_task.get("title") or "").strip()
        completed = {str(item) for item in state.get("completed", [])}
        failed = {str(item) for item in state.get("failed", [])}
        results = dict(state.get("results") if isinstance(state.get("results"), dict) else {})
        failure_summary = summary or "Child worker failed after approval."
        matched_id: str | None = None
        for subtask in subtasks:
            if not isinstance(subtask, dict):
                continue
            subtask_id = str(subtask.get("id") or "").strip()
            if not subtask_id:
                continue
            title = str(subtask.get("title") or "").strip()
            if subtask.get("status") == "waiting_approval" or (child_title and title == child_title):
                subtask["status"] = "failed"
                subtask["result"] = failure_summary
                failed.add(subtask_id)
                completed.discard(subtask_id)
                results[subtask_id] = f"Failed: {failure_summary}"
                matched_id = subtask_id
                break
        if matched_id is None:
            return state
        if hasattr(self._store, "upsert_pending_dag_state"):
            self._store.upsert_pending_dag_state(
                task_id=parent_task_id,
                session_id=state["session_id"],
                goal=state["goal"],
                context=state["context"],
                plan_json=json.dumps(plan, ensure_ascii=False),
                completed_ids=sorted(completed),
                failed_ids=sorted(failed),
                results=results,
            )
            refreshed = self._load_pending_dag_state(parent_task_id)
            if refreshed is not None:
                return refreshed
        updated = dict(state)
        updated["plan"] = plan
        updated["completed"] = sorted(completed)
        updated["failed"] = sorted(failed)
        updated["results"] = results
        return updated

    def _blocked_child_collaboration_for_runtime_task(
        self,
        *,
        approval: dict[str, Any],
        runtime_task: dict[str, Any],
    ) -> dict[str, Any] | None:
        session_id = runtime_task.get("sessionId")
        if not isinstance(session_id, str) or not session_id:
            return None
        tasks = self._collaboration_service.list_collaboration_tasks({"sessionId": session_id}).get("tasks", [])
        for task in tasks:
            if task.get("status") != "blocked":
                continue
            result = task.get("result") if isinstance(task.get("result"), dict) else {}
            approval_payload = result.get("approval") if isinstance(result.get("approval"), dict) else {}
            if result.get("runtimeTaskId") == approval.get("taskId"):
                return task
            if approval_payload.get("id") == approval.get("id"):
                return task
            if approval_payload.get("approvalId") == approval.get("id"):
                return task
        return None

    def _prepare_child_collaboration_worker(self, child_task: dict[str, Any]) -> dict[str, Any] | None:
        worker_id = child_task.get("assignedWorkerId")
        if not isinstance(worker_id, str) or not worker_id:
            return None
        try:
            worker = self._collaboration_service.get_agent_worker({"workerId": worker_id})["worker"]
        except ValueError:
            return None
        return self._collaboration_service.upsert_agent_worker(
            {
                "workerId": worker["id"],
                "name": worker["name"],
                "role": worker["role"],
                "status": "busy",
                "currentTaskId": child_task["id"],
                "capabilities": worker.get("capabilities", []),
                "metadata": worker.get("metadata", {}),
            }
        )["worker"]
