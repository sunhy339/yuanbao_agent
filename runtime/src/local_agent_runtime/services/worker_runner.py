from __future__ import annotations

import os
import re
import sys
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import Any

from .worker_budget import WorkerBudget
from .worker_process_transport import (
    WorkerProcessExitError,
    WorkerProcessTimeoutError,
    WorkerProcessTransport,
)
from .worker_environment import build_child_worker_env, resolve_child_provider_model
from .worker_policy import WorkerRunPolicy, normalize_worker_policy


@dataclass(slots=True)
class ChildTaskRequest:
    prompt: str
    title: str
    planning_prompt: str | None = None
    skill_id: str | None = None
    agent_type: str = "explorer"
    priority: int = 3
    session_id: str | None = None
    parent_runtime_task_id: str | None = None
    timeout_ms: int | None = None
    retry: dict[str, Any] | None = None
    cancellation: dict[str, Any] | None = None
    budget: dict[str, Any] | None = None
    profile: dict[str, Any] | None = None
    model: str | None = None
    mcp_policy: dict[str, Any] | None = None
    active_worktree: dict[str, Any] | None = None
    event_callback: Callable[[dict[str, Any]], None] | None = None


@dataclass(slots=True)
class ChildTaskExecutionContext:
    request: ChildTaskRequest
    task: dict[str, Any]
    worker: dict[str, Any]
    cancellation_event: Event
    attempt_number: int = 1


ChildTaskExecutor = Callable[[ChildTaskExecutionContext], Any]


class ChildTaskTimeoutError(TimeoutError):
    def __init__(self, timeout_seconds: float) -> None:
        self.timeout_seconds = timeout_seconds
        super().__init__(f"Child task timed out after {timeout_seconds:g} seconds")


class ChildTaskRemoteError(RuntimeError):
    def __init__(
        self,
        *,
        code: str,
        message: str,
        retryable: bool = False,
        payload: dict[str, Any] | None = None,
    ) -> None:
        self.code = code
        self.retryable = retryable
        self.payload = payload or {}
        super().__init__(message)


class ChildTaskProcessRequiredError(RuntimeError):
    code = "CHILD_WORKER_PROCESS_REQUIRED"
    retryable = False


class WorkerRunner:
    """Executes child collaboration tasks behind a stable runner boundary."""

    _MIN_RETRY_EXECUTION_SLICE_SECONDS = 0.001
    _shared_executor: ThreadPoolExecutor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="worker-runner")

    def __init__(self, collaboration: Any, executor: ChildTaskExecutor | None = None) -> None:
        self._collaboration = collaboration
        self._executor = executor

    def run_child_task(self, request: ChildTaskRequest) -> dict[str, Any]:
        budget_error = self._check_budget_exhausted(request)
        if budget_error is not None:
            return budget_error

        worker = self._create_inline_worker(request=request)
        task = self._create_inline_task(request=request)
        claimed = self._collaboration.claim_collaboration_task(
            {
                "taskId": task["id"],
                "workerId": worker["id"],
            }
        )
        running = self._collaboration.update_collaboration_task(
            {
                "taskId": task["id"],
                "status": "running",
            }
        )["task"]
        context = ChildTaskExecutionContext(
            request=request,
            task=running,
            worker=claimed["worker"],
            cancellation_event=Event(),
        )

        if self._cancel_requested(request):
            context.cancellation_event.set()
            return self._fail_child_task(
                request=request,
                task=running,
                worker=claimed["worker"],
                error={
                    "code": "CHILD_TASK_CANCELLED",
                    "message": "Child task execution was cancelled before the executor started.",
                    "type": "CancelledError",
                },
            )

        try:
            execution = self._execute_with_policy(context)
        except Exception as exc:
            context.cancellation_event.set()
            error = self._error_payload(exc, attempts=context.attempt_number)
            partial_handoff = self._partial_handoff_from_failure(
                request=request,
                task=running,
                error=error,
            )
            if partial_handoff:
                error["partialHandoff"] = partial_handoff
            return self._fail_child_task(
                request=request,
                task=running,
                worker=claimed["worker"],
                error=error,
            )

        if execution.get("status") == "waiting_approval":
            return self._block_child_task(
                request=request,
                task=running,
                worker=claimed["worker"],
                execution=execution,
            )
        if execution.get("status") == "failed":
            error = {
                "code": "CHILD_TASK_EXECUTION_FAILED",
                "message": str(execution.get("summary") or "Child worker failed."),
                "type": "ChildTaskExecutionFailed",
                "runtimeTaskStatus": (
                    execution.get("result", {}).get("runtimeTaskStatus")
                    if isinstance(execution.get("result"), dict)
                    else None
                ),
            }
            partial_handoff = self._partial_handoff_from_execution(
                request=request,
                task=running,
                execution=execution,
                error=error,
            )
            if partial_handoff:
                error["partialHandoff"] = partial_handoff
            return self._fail_child_task(
                request=request,
                task=running,
                worker=claimed["worker"],
                error=error,
            )

        completion = self._collaboration.complete_collaboration_task(
            {
                "taskId": running["id"],
                "workerId": claimed["worker"]["id"],
                "result": execution["result"],
            }
        )
        completed = completion["task"]
        final_worker = completion["worker"]

        # P4: Register artifact candidates from child executor output
        artifact_ids = self._register_artifact_candidates(
            execution=execution,
            task=completed,
            request=request,
        )

        extra_payload = dict(execution["payload"]) if isinstance(execution["payload"], dict) else {}
        if artifact_ids:
            extra_payload["artifactIds"] = artifact_ids

        message = self._collaboration.send_agent_message(
            {
                "senderWorkerId": final_worker["id"],
                "taskId": completed["id"],
                "kind": "result",
                "body": execution["summary"],
                "payload": self._message_payload(
                    request=request,
                    task=completed,
                    worker=final_worker,
                    execution_mode=execution["executionMode"],
                    extra=extra_payload,
                ),
            }
        )["message"]

        return {
            "status": "completed",
            "childTaskId": completed["id"],
            "workerId": final_worker["id"],
            "result": completed["result"],
            "subagent": {
                "agentType": request.agent_type,
                "executionMode": execution["executionMode"],
            },
            "task": completed,
            "worker": final_worker,
            "message": message,
            "summary": execution["summary"],
        }

    def resume_child_approval(
        self,
        *,
        approval: dict[str, Any],
        child_task: dict[str, Any],
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        request = self._request_from_blocked_child_task(child_task)
        worker = self._worker_for_blocked_child_task(child_task)
        context = ChildTaskExecutionContext(
            request=request,
            task=child_task,
            worker=worker,
            cancellation_event=Event(),
        )
        request_timeout = timeout_seconds if timeout_seconds is not None else self._resume_timeout_seconds(request)
        with WorkerProcessTransport.for_python_module(
            "local_agent_runtime.main",
            cwd=str(self._repo_root()),
            env=self._worker_process_env(request),
        ) as transport:
            try:
                response = transport.request(
                    "approval.submit",
                    {
                        "approvalId": approval["id"],
                        "decision": approval["decision"],
                        "_childWorkerApprovalResume": True,
                    },
                    timeout=request_timeout,
                    event_callback=lambda event: self._forward_process_event(context, event),
                )
            except WorkerProcessTimeoutError as exc:
                raise ChildTaskTimeoutError(request_timeout) from exc
            except WorkerProcessExitError as exc:
                raise ChildTaskRemoteError(
                    code="CHILD_WORKER_EXITED",
                    message=str(exc),
                    retryable=True,
                ) from exc

        error = response.get("error")
        if isinstance(error, dict):
            raise ChildTaskRemoteError(
                code=str(error.get("code") or "CHILD_WORKER_APPROVAL_RESUME_FAILED"),
                message=str(error.get("message") or "Child worker approval resume failed."),
                retryable=bool(error.get("retryable", False)),
                payload=deepcopy(error),
            )

        store = self._collaboration.store
        return store.get_task({"taskId": approval["taskId"]})["task"]

    def _request_from_blocked_child_task(self, child_task: dict[str, Any]) -> ChildTaskRequest:
        metadata = child_task.get("metadata") if isinstance(child_task.get("metadata"), dict) else {}
        return ChildTaskRequest(
            prompt=str(child_task.get("description") or child_task.get("title") or "Resume child approval"),
            title=str(child_task.get("title") or "Resume child approval"),
            planning_prompt=(
                str(metadata.get("planningPrompt")).strip()
                if isinstance(metadata.get("planningPrompt"), str) and str(metadata.get("planningPrompt")).strip()
                else None
            ),
            skill_id=(
                str(metadata.get("skillId")).strip()
                if isinstance(metadata.get("skillId"), str) and str(metadata.get("skillId")).strip()
                else None
            ),
            agent_type=str(metadata.get("agentType") or "explorer"),
            priority=self._priority_or_default(child_task.get("priority")),
            session_id=child_task.get("sessionId") if isinstance(child_task.get("sessionId"), str) else None,
            parent_runtime_task_id=metadata.get("parentRuntimeTaskId")
            if isinstance(metadata.get("parentRuntimeTaskId"), str)
            else None,
            timeout_ms=metadata.get("timeoutMs") if isinstance(metadata.get("timeoutMs"), int) else None,
            retry=deepcopy(metadata.get("retry")) if isinstance(metadata.get("retry"), dict) else None,
            cancellation=deepcopy(metadata.get("cancellation")) if isinstance(metadata.get("cancellation"), dict) else None,
            budget=deepcopy(metadata.get("budget")) if isinstance(metadata.get("budget"), dict) else None,
            profile=deepcopy(metadata.get("profile")) if isinstance(metadata.get("profile"), dict) else None,
            model=(
                str(metadata.get("model")).strip()
                if isinstance(metadata.get("model"), str) and str(metadata.get("model")).strip()
                else None
            ),
            mcp_policy=deepcopy(metadata.get("mcpPolicy")) if isinstance(metadata.get("mcpPolicy"), dict) else None,
            active_worktree=deepcopy(metadata.get("activeWorktree")) if isinstance(metadata.get("activeWorktree"), dict) else None,
        )

    def _worker_for_blocked_child_task(self, child_task: dict[str, Any]) -> dict[str, Any]:
        worker_id = child_task.get("assignedWorkerId")
        if not isinstance(worker_id, str) or not worker_id:
            raise ChildTaskRemoteError(
                code="CHILD_WORKER_MISSING_WORKER",
                message="Blocked child task has no assigned worker to resume approval.",
            )
        return self._collaboration.get_agent_worker({"workerId": worker_id})["worker"]

    def _resume_timeout_seconds(self, request: ChildTaskRequest) -> float:
        policy = self._policy_for(request)
        timeout = self._timeout_seconds(request, policy)
        return timeout if timeout is not None and timeout > 0 else 300.0

    def _priority_or_default(self, value: Any) -> int:
        try:
            return max(0, min(int(value), 9))
        except (TypeError, ValueError):
            return 3

    def _block_child_task(
        self,
        *,
        request: ChildTaskRequest,
        task: dict[str, Any],
        worker: dict[str, Any],
        execution: dict[str, Any],
    ) -> dict[str, Any]:
        blocked = self._collaboration.update_collaboration_task(
            {
                "taskId": task["id"],
                "status": "blocked",
                "result": execution["result"],
            }
        )["task"]
        idle_worker = self._collaboration.upsert_agent_worker(
            {
                "workerId": worker["id"],
                "name": worker["name"],
                "role": worker["role"],
                "status": "idle",
                "capabilities": worker.get("capabilities", []),
                "metadata": worker.get("metadata", {}),
            }
        )["worker"]
        message = self._collaboration.send_agent_message(
            {
                "senderWorkerId": idle_worker["id"],
                "taskId": blocked["id"],
                "kind": "system",
                "body": execution["summary"],
                "payload": self._message_payload(
                    request=request,
                    task=blocked,
                    worker=idle_worker,
                    execution_mode=execution["executionMode"],
                    extra=execution["payload"],
                ),
            }
        )["message"]
        return {
            "status": "waiting_approval",
            "childTaskId": blocked["id"],
            "workerId": idle_worker["id"],
            "result": blocked["result"],
            "subagent": {
                "agentType": request.agent_type,
                "executionMode": execution["executionMode"],
            },
            "task": blocked,
            "worker": idle_worker,
            "message": message,
            "summary": execution["summary"],
        }

    def _execute(self, context: ChildTaskExecutionContext, *, timeout_seconds: float | None) -> Any:
        if self._executor is None:
            return self._execute_default(context, timeout_seconds=timeout_seconds)
        if timeout_seconds is None:
            return self._executor(context)
        if timeout_seconds <= 0:
            raise ChildTaskTimeoutError(timeout_seconds)

        future = self._shared_executor.submit(self._executor, context)
        try:
            return future.result(timeout=timeout_seconds)
        except FutureTimeoutError as exc:
            context.cancellation_event.set()
            future.cancel()
            raise ChildTaskTimeoutError(timeout_seconds) from exc

    def _execute_with_policy(self, context: ChildTaskExecutionContext) -> dict[str, Any]:
        policy = self._policy_for(context.request)
        deadline = policy.timeout.deadline_from(time.monotonic())
        last_error: Exception | None = None
        for attempt_number in range(1, policy.retry.max_attempts + 1):
            context.attempt_number = attempt_number
            context.cancellation_event = Event()
            try:
                output = self._execute(
                    context,
                    timeout_seconds=self._effective_timeout_seconds(context.request, policy, deadline),
                )
                execution = self._normalize_execution_result(request=context.request, output=output)
                execution["attempts"] = attempt_number
                execution["result"]["attempts"] = attempt_number
                return execution
            except Exception as exc:
                last_error = exc
                if not policy.retry.should_retry(attempt_number=attempt_number, error=exc):
                    raise
                # P6: emit retry trace event
                self._emit_retry_trace(context, attempt_number=attempt_number, error=exc)
                remaining_seconds = deadline.remaining(time.monotonic())
                if remaining_seconds is not None and remaining_seconds <= self._MIN_RETRY_EXECUTION_SLICE_SECONDS:
                    context.attempt_number = min(attempt_number + 1, policy.retry.max_attempts)
                    raise ChildTaskTimeoutError(0)
        if last_error is not None:
            raise last_error
        raise RuntimeError("Child task executor did not run")

    def _execute_default(self, context: ChildTaskExecutionContext, *, timeout_seconds: float | None) -> dict[str, Any]:
        if not self._can_use_process_worker(context.request):
            raise ChildTaskProcessRequiredError(
                "Child worker process execution requires a sessionId and a file-backed runtime database."
            )
        return self._execute_process_worker(context, timeout_seconds=timeout_seconds)

    def _execute_process_worker(
        self,
        context: ChildTaskExecutionContext,
        *,
        timeout_seconds: float | None,
    ) -> dict[str, Any]:
        request_timeout = timeout_seconds if timeout_seconds is not None else 300.0
        with WorkerProcessTransport.for_python_module(
            "local_agent_runtime.main",
            cwd=str(self._repo_root()),
            env=self._worker_process_env(context.request),
        ) as transport:
            try:
                response = transport.request(
                    "worker.run_child_task",
                    {
                        "sessionId": context.request.session_id,
                        "prompt": context.request.prompt,
                        "title": context.request.title,
                        "planningPrompt": context.request.planning_prompt,
                        "skillId": context.request.skill_id,
                        "budget": deepcopy(context.request.budget) if isinstance(context.request.budget, dict) else {},
                        "parentRuntimeTaskId": context.request.parent_runtime_task_id,
                        "collaborationTaskId": context.task["id"],
                        "agentType": context.request.agent_type,
                        "profile": deepcopy(context.request.profile) if isinstance(context.request.profile, dict) else None,
                        "mcpPolicy": deepcopy(context.request.mcp_policy) if isinstance(context.request.mcp_policy, dict) else None,
                        "activeWorktree": deepcopy(context.request.active_worktree) if isinstance(context.request.active_worktree, dict) else None,
                    },
                    timeout=request_timeout,
                    event_callback=lambda event: self._forward_process_event(context, event),
                )
            except WorkerProcessTimeoutError as exc:
                raise ChildTaskTimeoutError(request_timeout) from exc
            except WorkerProcessExitError as exc:
                raise ChildTaskRemoteError(
                    code="CHILD_WORKER_EXITED",
                    message=str(exc),
                    retryable=True,
                ) from exc

        error = response.get("error")
        if isinstance(error, dict):
            raise ChildTaskRemoteError(
                code=str(error.get("code") or "CHILD_WORKER_ERROR"),
                message=str(error.get("message") or "Child worker returned an error."),
                retryable=bool(error.get("retryable", False)),
                payload=deepcopy(error),
            )

        payload = response.get("result")
        if not isinstance(payload, dict):
            raise ChildTaskRemoteError(
                code="CHILD_WORKER_INVALID_RESPONSE",
                message="Child worker returned an invalid response payload.",
            )

        child_task = payload.get("task") if isinstance(payload.get("task"), dict) else {}
        budget = payload.get("budget") if isinstance(payload.get("budget"), dict) else {}
        summary = self._sanitize_child_summary(payload.get("summary"))
        if not summary:
            summary = self._summary_from_runtime_task(child_task) or "Child worker completed."
        status = str(payload.get("status") or "completed")
        return {
            "status": status,
            "summary": summary,
            "executionMode": "process-rpc",
            "result": {
                "runtimeTaskId": child_task.get("id"),
                "runtimeTaskStatus": child_task.get("status"),
                "budget": deepcopy(budget),
                "approval": deepcopy(payload.get("approval")) if isinstance(payload.get("approval"), dict) else None,
            },
            "payload": {
                "runtimeTask": deepcopy(child_task),
                "budget": deepcopy(budget),
                "approval": deepcopy(payload.get("approval")) if isinstance(payload.get("approval"), dict) else None,
            },
        }

    def _can_use_process_worker(self, request: ChildTaskRequest) -> bool:
        if request.session_id is None:
            return False
        database_path = self._database_path()
        return bool(database_path and database_path != ":memory:")

    def _database_path(self) -> str | None:
        store = getattr(self._collaboration, "store", None)
        if store is None:
            store = getattr(self._collaboration, "_store", None)
        return getattr(store, "database_path", None)

    def _repo_root(self) -> Path:
        return Path(__file__).resolve().parents[4]

    def _runtime_src(self) -> Path:
        return Path(__file__).resolve().parents[2]

    def _worker_process_env(self, request: ChildTaskRequest | None = None) -> dict[str, str]:
        database_path = self._database_path()
        if not database_path:
            raise ChildTaskProcessRequiredError("Child worker process execution requires a file-backed database.")
        return build_child_worker_env(
            parent_env=os.environ,
            runtime_src=self._runtime_src(),
            database_path=database_path,
            tool_allowlist=self._child_tool_allowlist_from_request(request),
            child_model=self._child_model_from_request(request),
        )

    def _child_tool_allowlist_from_request(self, request: ChildTaskRequest | None) -> Any:
        if request is None or not isinstance(request.budget, dict):
            return None
        return (
            request.budget.get("childToolAllowlist")
            or request.budget.get("child_tool_allowlist")
            or request.budget.get("toolAllowlist")
            or request.budget.get("tool_allowlist")
        )

    def _default_executor(self, _context: ChildTaskExecutionContext) -> dict[str, Any]:
        return {
            "summary": self._inline_summary(),
            "executionMode": "inline-skeleton",
        }

    def _forward_process_event(self, context: ChildTaskExecutionContext, event: dict[str, Any]) -> None:
        event_type = event.get("type")
        child_task_id = event.get("taskId")
        if not isinstance(event_type, str) or not event_type:
            return
        if not isinstance(child_task_id, str) or not child_task_id:
            return

        session_id = context.request.session_id or str(event.get("sessionId") or "")
        payload = dict(event.get("payload")) if isinstance(event.get("payload"), dict) else {}
        bridge = {
            "source": "child-worker",
            "childTaskId": child_task_id,
            "childSessionId": event.get("sessionId"),
            "parentRuntimeTaskId": context.request.parent_runtime_task_id,
            "childEventType": event_type,
        }
        callback = context.request.event_callback

        if event_type.startswith("collab.") and child_task_id.startswith("ctask_"):
            payload["_bridge"] = {
                **bridge,
                "skipTraceMirror": True,
            }
            if callback is not None:
                callback(payload)
            self._collaboration.publish_runtime_event(
                session_id=session_id,
                task_id=child_task_id,
                event_type=event_type,
                payload=payload,
            )
            return

        summary = self._child_progress_summary(event_type=event_type, payload=payload)
        if summary is None:
            return
        progress_task = dict(context.task)
        progress_task["updatedAt"] = self._collaboration.store.now()
        progress_task["status"] = "blocked" if event_type == "approval.requested" else "running"
        progress_task["result"] = {"summary": summary}
        bridged_payload = {
            "task": progress_task,
            "worker": self._progress_worker(context),
            "_bridge": {
                **bridge,
                "childEvent": {
                    "taskId": child_task_id,
                    "type": event_type,
                    "payload": payload,
                },
            },
        }
        if callback is not None:
            callback(bridged_payload)
        self._collaboration.publish_runtime_event(
            session_id=session_id,
            task_id=context.task["id"],
            event_type="collab.task.updated",
            payload=bridged_payload,
        )

    def _child_progress_summary(self, *, event_type: str, payload: dict[str, Any]) -> str | None:
        if event_type == "tool.started":
            return f"Running {payload.get('toolName') or 'tool'}"
        if event_type == "tool.completed":
            return f"{payload.get('toolName') or 'Tool'} completed"
        if event_type == "tool.failed":
            return f"{payload.get('toolName') or 'Tool'} failed"
        if event_type == "approval.requested":
            return "Waiting for approval"
        if event_type == "approval.resolved":
            return f"Approval {payload.get('decision') or 'resolved'}"
        if event_type == "patch.proposed":
            return str(payload.get("summary") or "Patch proposed")
        if event_type == "command.started":
            return "Command started"
        if event_type == "command.completed":
            return "Command completed"
        if event_type == "command.failed":
            return "Command failed"
        if event_type == "command.output":
            chunk = str(payload.get("chunk") or "").strip()
            return chunk[:120] or "Command output received"
        if event_type == "collab.worker.budget.updated":
            return "Worker budget updated"
        return None

    def _progress_worker(self, context: ChildTaskExecutionContext) -> dict[str, Any]:
        worker = dict(context.worker)
        worker["status"] = "busy"
        worker["currentTaskId"] = context.task["id"]
        return worker

    def _register_artifact_candidates(
        self,
        *,
        execution: dict[str, Any],
        task: dict[str, Any],
        request: ChildTaskRequest,
    ) -> list[str]:
        """P4: Register artifact candidates from child executor output."""
        candidates = execution.get("artifacts")
        if not isinstance(candidates, list) or not candidates:
            return []
        store = getattr(self._collaboration, "store", None) or getattr(self._collaboration, "_store", None)
        if store is None:
            return []
        session_id = request.session_id or task.get("sessionId", "")
        parent_task_id = request.parent_runtime_task_id
        if not session_id or not parent_task_id:
            return []
        artifact_ids: list[str] = []
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            kind = candidate.get("kind")
            if not isinstance(kind, str) or not kind:
                continue
            try:
                result = store.create_artifact({
                    "sessionId": session_id,
                    "parentTaskId": parent_task_id,
                    "producerTaskId": task["id"],
                    "kind": kind,
                    "title": candidate.get("title"),
                    "description": candidate.get("description"),
                    "content": candidate.get("content", {}),
                    "metadata": candidate.get("metadata", {}),
                })
                artifact_ids.append(result["artifact"]["id"])
            except (ValueError, KeyError):
                continue
        return artifact_ids

    def _normalize_execution_result(self, *, request: ChildTaskRequest, output: Any) -> dict[str, Any]:
        if isinstance(output, str):
            summary = output.strip() or self._inline_summary()
            execution_mode = "inline-skeleton"
            result: dict[str, Any] = {}
            payload: dict[str, Any] = {}
        elif isinstance(output, dict):
            nested_result = output.get("result")
            result = dict(nested_result) if isinstance(nested_result, dict) else {}
            payload_value = output.get("payload")
            payload = dict(payload_value) if isinstance(payload_value, dict) else {}
            summary_value = output.get("summary") or result.get("summary")
            summary = self._sanitize_child_summary(summary_value)
            if not summary:
                runtime_task = payload.get("runtimeTask") if isinstance(payload.get("runtimeTask"), dict) else {}
                summary = self._summary_from_runtime_task(runtime_task) or self._inline_summary()
            mode_value = (
                output.get("executionMode")
                or output.get("execution_mode")
                or result.get("executionMode")
            )
            execution_mode = str(mode_value).strip() if mode_value else "inline-skeleton"
        else:
            raise TypeError("Child task executor must return a string or dictionary result")

        result["summary"] = summary
        result["agentType"] = request.agent_type
        result["executionMode"] = execution_mode

        return {
            "status": str(output.get("status") or "completed") if isinstance(output, dict) else "completed",
            "summary": summary,
            "executionMode": execution_mode,
            "result": result,
            "payload": payload,
            "artifacts": output.get("artifacts") if isinstance(output, dict) else None,
        }

    def _sanitize_child_summary(self, summary: Any) -> str:
        text = str(summary or "")
        text = re.sub(r"<tool_call\b[^>]*>.*?</tool_call>", " ", text, flags=re.IGNORECASE | re.DOTALL)
        text = re.sub(r"<arg_key>.*?</arg_key>", " ", text, flags=re.IGNORECASE | re.DOTALL)
        text = re.sub(r"<arg_value>.*?</arg_value>", " ", text, flags=re.IGNORECASE | re.DOTALL)
        text = re.sub(r"</?(?:tool_call|arg_key|arg_value)\b[^>]*>", " ", text, flags=re.IGNORECASE)
        return re.sub(r"\s+", " ", text).strip()

    def _summary_from_runtime_task(self, task: dict[str, Any]) -> str:
        if not isinstance(task, dict):
            return ""
        existing = self._sanitize_child_summary(task.get("resultSummary") or task.get("summary"))
        if existing:
            return existing

        changed_files = task.get("changedFiles") if isinstance(task.get("changedFiles"), list) else []
        changed_reasons = [
            str(item.get("reason") or item.get("path") or "").strip()
            for item in changed_files
            if isinstance(item, dict) and str(item.get("reason") or item.get("path") or "").strip()
        ]
        changed_reasons = list(dict.fromkeys(changed_reasons))
        changed_text = f"Changed: {'; '.join(changed_reasons)}." if changed_reasons else ""

        commands = task.get("commands") if isinstance(task.get("commands"), list) else []
        passed_commands = [
            str(item.get("command") or "").strip()
            for item in commands
            if isinstance(item, dict)
            and str(item.get("command") or "").strip()
            and str(item.get("status") or "").lower() in {"completed", "passed", "success"}
            and item.get("exitCode") in (0, "0", None)
        ]
        validation_text = ""
        if passed_commands:
            validation_text = f"Validated with {passed_commands[-1]}."

        return " ".join(part for part in (changed_text, validation_text) if part).strip()

    def _fail_child_task(
        self,
        *,
        request: ChildTaskRequest,
        task: dict[str, Any],
        worker: dict[str, Any],
        error: dict[str, Any],
    ) -> dict[str, Any]:
        failure = self._collaboration.fail_collaboration_task(
            {
                "taskId": task["id"],
                "workerId": worker["id"],
                "error": error,
            }
        )
        failed = failure["task"]
        final_worker = failure["worker"]
        execution_mode = self._execution_mode_from_task(failed)
        summary = error["message"]
        message = self._collaboration.send_agent_message(
            {
                "senderWorkerId": final_worker["id"],
                "taskId": failed["id"],
                "kind": "result",
                "body": summary,
                "payload": self._message_payload(
                    request=request,
                    task=failed,
                    worker=final_worker,
                    execution_mode=execution_mode,
                    extra={"error": error},
                ),
            }
        )["message"]

        return {
            "status": "failed",
            "childTaskId": failed["id"],
            "workerId": final_worker["id"],
            "result": failed["result"],
            "error": error,
            "subagent": {
                "agentType": request.agent_type,
                "executionMode": execution_mode,
            },
            "task": failed,
            "worker": final_worker,
            "message": message,
            "summary": summary,
        }

    def _partial_handoff_from_execution(
        self,
        *,
        request: ChildTaskRequest,
        task: dict[str, Any],
        execution: dict[str, Any],
        error: dict[str, Any],
    ) -> dict[str, Any] | None:
        payload = execution.get("payload") if isinstance(execution.get("payload"), dict) else {}
        runtime_task = payload.get("runtimeTask") if isinstance(payload.get("runtimeTask"), dict) else None
        if runtime_task is None:
            result = execution.get("result") if isinstance(execution.get("result"), dict) else {}
            runtime_task_id = result.get("runtimeTaskId")
            runtime_task = self._runtime_task_by_id(runtime_task_id)
        return self._build_partial_handoff(
            request=request,
            collaboration_task=task,
            runtime_task=runtime_task,
            error=error,
        )

    def _partial_handoff_from_failure(
        self,
        *,
        request: ChildTaskRequest,
        task: dict[str, Any],
        error: dict[str, Any],
    ) -> dict[str, Any] | None:
        return self._build_partial_handoff(
            request=request,
            collaboration_task=task,
            runtime_task=self._latest_runtime_task_for_request(request),
            error=error,
        )

    def _build_partial_handoff(
        self,
        *,
        request: ChildTaskRequest,
        collaboration_task: dict[str, Any],
        runtime_task: dict[str, Any] | None,
        error: dict[str, Any],
    ) -> dict[str, Any] | None:
        profile = request.profile if isinstance(request.profile, dict) else {}
        runtime = runtime_task if isinstance(runtime_task, dict) else {}
        changed_files = runtime.get("changedFiles") if isinstance(runtime.get("changedFiles"), list) else []
        commands = runtime.get("commands") if isinstance(runtime.get("commands"), list) else []
        verification = runtime.get("verification") if isinstance(runtime.get("verification"), list) else []
        tests_run = runtime.get("testsRun") if isinstance(runtime.get("testsRun"), list) else []
        expected_artifacts = profile.get("expectedArtifacts") if isinstance(profile.get("expectedArtifacts"), list) else []
        verification_requirements = (
            profile.get("verificationRequirements")
            if isinstance(profile.get("verificationRequirements"), list)
            else []
        )
        pending_verification = self._pending_verification_commands(
            verification_requirements=verification_requirements,
            commands=commands,
            verification=verification,
        )
        has_progress = bool(
            runtime
            or changed_files
            or commands
            or verification
            or expected_artifacts
            or verification_requirements
        )
        if not has_progress:
            return None
        status = str(error.get("code") or error.get("type") or "child_failed")
        handoff = {
            "status": status,
            "reason": str(error.get("message") or "Child task did not finish."),
            "collaborationTaskId": collaboration_task.get("id"),
            "runtimeTaskId": runtime.get("id"),
            "runtimeTaskStatus": runtime.get("status"),
            "title": request.title,
            "agentType": request.agent_type,
            "changedFiles": deepcopy(changed_files),
            "commands": deepcopy(commands),
            "verification": deepcopy(verification),
            "testsRun": deepcopy(tests_run),
            "expectedArtifacts": deepcopy(expected_artifacts),
            "verificationRequirements": deepcopy(verification_requirements),
            "pendingVerification": pending_verification,
            "nextAction": self._partial_handoff_next_action(
                changed_files=changed_files,
                pending_verification=pending_verification,
                expected_artifacts=expected_artifacts,
            ),
        }
        return {key: value for key, value in handoff.items() if value not in (None, "", [])}

    def _pending_verification_commands(
        self,
        *,
        verification_requirements: list[Any],
        commands: list[Any],
        verification: list[Any],
    ) -> list[str]:
        passed_text = " ".join(
            str(item.get("command") or item.get("name") or "")
            for item in [*commands, *verification]
            if isinstance(item, dict)
            and str(item.get("status") or "").strip().lower() in {"passed", "completed", "success"}
            and item.get("exitCode") in (0, "0", None)
        ).casefold()
        pending: list[str] = []
        for item in verification_requirements:
            if not isinstance(item, dict):
                continue
            command = str(item.get("command") or item.get("name") or "").strip()
            if command and command.casefold() not in passed_text:
                pending.append(command)
        return list(dict.fromkeys(pending))

    @staticmethod
    def _partial_handoff_next_action(
        *,
        changed_files: list[Any],
        pending_verification: list[str],
        expected_artifacts: list[Any],
    ) -> str:
        if pending_verification:
            return "Run pending verification and repair any failures."
        if changed_files:
            return "Inspect changed files, finish missing acceptance criteria, then verify."
        if expected_artifacts:
            return "Create the expected artifacts, then verify."
        return "Inspect current state and continue from the last child attempt."

    def _runtime_task_by_id(self, task_id: Any) -> dict[str, Any] | None:
        if not isinstance(task_id, str) or not task_id:
            return None
        store = getattr(self._collaboration, "store", None) or getattr(self._collaboration, "_store", None)
        if store is None or not hasattr(store, "get_task"):
            return None
        try:
            return store.get_task({"taskId": task_id})["task"]
        except Exception:
            return None

    def _latest_runtime_task_for_request(self, request: ChildTaskRequest) -> dict[str, Any] | None:
        if not request.session_id:
            return None
        store = getattr(self._collaboration, "store", None) or getattr(self._collaboration, "_store", None)
        if store is None or not hasattr(store, "list_tasks"):
            return None
        try:
            tasks = store.list_tasks({"sessionId": request.session_id}).get("tasks", [])
        except Exception:
            return None
        parent_id = request.parent_runtime_task_id
        prompt = request.prompt.strip()
        for task in tasks:
            if not isinstance(task, dict):
                continue
            routing = task.get("routing") if isinstance(task.get("routing"), dict) else {}
            if parent_id and routing.get("parentRuntimeTaskId") != parent_id:
                continue
            if prompt and str(task.get("goal") or "").strip() != prompt:
                continue
            return task
        return None

    def _message_payload(
        self,
        *,
        request: ChildTaskRequest,
        task: dict[str, Any],
        worker: dict[str, Any],
        execution_mode: str,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = {
            "agentType": request.agent_type,
            "executionMode": execution_mode,
            "childTaskId": task["id"],
            "workerId": worker["id"],
        }
        if extra:
            payload.update(deepcopy(extra))
        return payload

    def _error_payload(self, exc: Exception, *, attempts: int | None = None) -> dict[str, Any]:
        if isinstance(exc, ChildTaskTimeoutError):
            payload = {
                "code": "CHILD_TASK_TIMEOUT",
                "message": str(exc),
                "type": exc.__class__.__name__,
                "timeoutSeconds": exc.timeout_seconds,
            }
        else:
            payload = {
                "code": str(getattr(exc, "code", "CHILD_TASK_EXECUTION_FAILED")),
                "message": str(exc) or exc.__class__.__name__,
                "type": exc.__class__.__name__,
            }
            extra = getattr(exc, "payload", None)
            if isinstance(extra, dict):
                for key, value in extra.items():
                    if key not in {"code", "message", "retryable"}:
                        payload[key] = deepcopy(value)
            if hasattr(exc, "retryable"):
                payload["retryable"] = bool(getattr(exc, "retryable"))
        if attempts is not None:
            payload["attempts"] = attempts
        return payload

    def _emit_retry_trace(
        self,
        context: ChildTaskExecutionContext,
        *,
        attempt_number: int,
        error: Exception,
    ) -> None:
        """P6: Emit a trace event when a retry attempt begins."""
        session_id = context.request.session_id or ""
        task_id = context.task.get("id", "")
        if not session_id and not task_id:
            return
        error_code = str(getattr(error, "code", error.__class__.__name__))
        self._collaboration.publish_runtime_event(
            session_id=session_id,
            task_id=task_id,
            event_type="child.retry.attempt",
            payload={
                "attemptNumber": attempt_number,
                "nextAttempt": attempt_number + 1,
                "errorCode": error_code,
                "errorMessage": str(error)[:200],
                "childTaskId": task_id,
            },
            visibility="trace",
        )

    def _execution_mode_from_task(self, task: dict[str, Any]) -> str:
        metadata = task.get("metadata")
        if isinstance(metadata, dict):
            value = metadata.get("executionMode")
            if isinstance(value, str) and value:
                return value
        return "process-required"

    def _check_budget_exhausted(self, request: ChildTaskRequest) -> dict[str, Any] | None:
        if request.budget is None:
            return None
        try:
            budget = WorkerBudget.from_metadata(request.budget)
        except (TypeError, ValueError):
            return None
        if budget.tool_calls.limit is not None and budget.tool_calls.limit <= budget.tool_calls.consumed:
            return {
                "error": {
                    "code": "WORKER_BUDGET_TOOL_CALLS_EXCEEDED",
                    "message": f"Tool call budget exceeded: limit={budget.tool_calls.limit}, consumed={budget.tool_calls.consumed}",
                    "retryable": False,
                }
            }
        if budget.tokens.limit is not None and budget.tokens.limit <= budget.tokens.consumed:
            return {
                "error": {
                    "code": "WORKER_BUDGET_TOKENS_EXCEEDED",
                    "message": f"Token budget exceeded: limit={budget.tokens.limit}, consumed={budget.tokens.consumed}",
                    "retryable": False,
                }
            }
        return None

    def _cancel_requested(self, request: ChildTaskRequest) -> bool:
        cancellation = request.cancellation
        if not isinstance(cancellation, dict):
            return False
        for key in ("cancelled", "canceled", "cancelRequested", "cancel_requested"):
            if self._truthy(cancellation.get(key)):
                return True
        return False

    def _truthy(self, value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "y"}
        return False

    def _policy_for(self, request: ChildTaskRequest) -> WorkerRunPolicy:
        raw_timeout: dict[str, Any] = {}
        if request.timeout_ms is not None:
            raw_timeout["perAttemptSeconds"] = request.timeout_ms / 1000
        for metadata in (request.cancellation, request.budget):
            if not isinstance(metadata, dict):
                continue
            for source_key, target_key, divisor in (
                ("timeoutMs", "perAttemptSeconds", 1000),
                ("timeout_ms", "perAttemptSeconds", 1000),
                ("timeoutSeconds", "perAttemptSeconds", 1),
                ("timeout_seconds", "perAttemptSeconds", 1),
                ("totalTimeoutMs", "totalSeconds", 1000),
                ("total_timeout_ms", "totalSeconds", 1000),
                ("totalTimeoutSeconds", "totalSeconds", 1),
                ("total_timeout_seconds", "totalSeconds", 1),
            ):
                value = self._float_or_none(metadata.get(source_key), divisor=divisor)
                if value is not None and target_key not in raw_timeout:
                    raw_timeout[target_key] = value
        raw_policy: dict[str, Any] = {"retry": request.retry}
        if raw_timeout:
            raw_policy["timeout"] = raw_timeout
        return normalize_worker_policy(raw_policy)

    def _timeout_seconds(self, request: ChildTaskRequest, policy: WorkerRunPolicy) -> float | None:
        if policy.timeout.per_attempt_seconds is not None:
            return policy.timeout.per_attempt_seconds
        if request.timeout_ms is not None:
            return self._float_or_none(request.timeout_ms, divisor=1000)

        for metadata in (request.cancellation, request.budget):
            if not isinstance(metadata, dict):
                continue
            for key in ("timeoutMs", "timeout_ms"):
                value = self._float_or_none(metadata.get(key), divisor=1000)
                if value is not None:
                    return value
            for key in ("timeoutSeconds", "timeout_seconds"):
                value = self._float_or_none(metadata.get(key), divisor=1)
                if value is not None:
                    return value
        return None

    def _effective_timeout_seconds(
        self,
        request: ChildTaskRequest,
        policy: WorkerRunPolicy,
        deadline: Any,
    ) -> float | None:
        per_attempt_seconds = self._timeout_seconds(request, policy)
        remaining_seconds = deadline.remaining(time.monotonic())
        if remaining_seconds is None:
            return per_attempt_seconds
        if remaining_seconds <= 0:
            raise ChildTaskTimeoutError(0)
        if per_attempt_seconds is None:
            return remaining_seconds
        return min(per_attempt_seconds, remaining_seconds)

    def _float_or_none(self, value: Any, *, divisor: float) -> float | None:
        if value is None:
            return None
        try:
            return float(value) / divisor
        except (TypeError, ValueError):
            return None

    def _create_inline_worker(self, *, request: ChildTaskRequest) -> dict[str, Any]:
        return self._collaboration.upsert_agent_worker(
            {
                "name": f"{request.agent_type.title()} Worker",
                "role": request.agent_type,
                "status": "idle",
                "capabilities": ["subagent", "collaboration"],
                "metadata": self._worker_metadata(request),
            }
        )["worker"]

    def _create_inline_task(self, *, request: ChildTaskRequest) -> dict[str, Any]:
        params: dict[str, Any] = {
            "sessionId": request.session_id,
            "title": request.title,
            "description": request.prompt,
            "priority": request.priority,
            "metadata": self._task_metadata(request),
        }
        if request.parent_runtime_task_id:
            params["parentTaskId"] = request.parent_runtime_task_id
        return self._collaboration.create_collaboration_task(params)["task"]

    def _worker_metadata(self, request: ChildTaskRequest) -> dict[str, Any]:
        metadata: dict[str, Any] = {
            "mode": self._default_execution_mode(request),
            "parentRuntimeTaskId": request.parent_runtime_task_id,
        }
        if request.cancellation is not None:
            metadata["cancellation"] = deepcopy(request.cancellation)
        if request.budget is not None:
            metadata["budget"] = deepcopy(request.budget)
        if request.timeout_ms is not None:
            metadata["timeoutMs"] = request.timeout_ms
        if request.retry is not None:
            metadata["retry"] = deepcopy(request.retry)
        if isinstance(request.skill_id, str) and request.skill_id.strip():
            metadata["skillId"] = request.skill_id.strip()
        if isinstance(request.mcp_policy, dict):
            metadata["mcpPolicy"] = deepcopy(request.mcp_policy)
        if isinstance(request.active_worktree, dict):
            metadata["activeWorktree"] = deepcopy(request.active_worktree)
        return metadata

    def _task_metadata(self, request: ChildTaskRequest) -> dict[str, Any]:
        metadata: dict[str, Any] = {
            "agentType": request.agent_type,
            "parentRuntimeTaskId": request.parent_runtime_task_id,
            "executionMode": self._default_execution_mode(request),
        }
        if isinstance(request.skill_id, str) and request.skill_id.strip():
            metadata["skillId"] = request.skill_id.strip()
        if isinstance(request.model, str) and request.model.strip():
            metadata["model"] = request.model.strip()
        if request.planning_prompt is not None:
            metadata["planningPrompt"] = request.planning_prompt
        if request.profile is not None:
            metadata["profile"] = deepcopy(request.profile)
            # P9: Extract writeScope from profile.ownedScope for enforcement
            owned_scope = request.profile.get("ownedScope")
            if owned_scope is not None:
                if isinstance(owned_scope, str):
                    metadata["writeScope"] = [owned_scope]
                elif isinstance(owned_scope, list):
                    metadata["writeScope"] = list(owned_scope)
        if request.cancellation is not None:
            metadata["cancellation"] = deepcopy(request.cancellation)
        if request.budget is not None:
            metadata["budget"] = deepcopy(request.budget)
        if request.timeout_ms is not None:
            metadata["timeoutMs"] = request.timeout_ms
        if request.retry is not None:
            metadata["retry"] = deepcopy(request.retry)
        if isinstance(request.mcp_policy, dict):
            metadata["mcpPolicy"] = deepcopy(request.mcp_policy)
        if isinstance(request.active_worktree, dict):
            metadata["activeWorktree"] = deepcopy(request.active_worktree)
        return metadata

    def _inline_summary(self) -> str:
        return (
            "Subagent task executed by the in-process worker runner. "
            "Independent worker process isolation is still pending."
        )

    def _default_execution_mode(self, request: ChildTaskRequest) -> str:
        return "process-rpc" if self._can_use_process_worker(request) else "process-required"

    def _child_model_from_request(self, request: ChildTaskRequest | None) -> str | None:
        if request is None:
            return self._default_child_model()
        if isinstance(request.model, str) and request.model.strip():
            return request.model.strip()
        if isinstance(request.profile, dict):
            profile_model = request.profile.get("model") or request.profile.get("childModel")
            if isinstance(profile_model, str) and profile_model.strip():
                return profile_model.strip()
        return self._default_child_model()

    def _default_child_model(self) -> str | None:
        provider_config = self._provider_config()
        return resolve_child_provider_model(
            parent_env=os.environ,
            provider_config=provider_config,
        )

    def _provider_config(self) -> dict[str, Any]:
        store = getattr(self._collaboration, "store", None)
        if store is None:
            store = getattr(self._collaboration, "_store", None)
        if store is None or not hasattr(store, "get_config"):
            return {}
        try:
            config = store.get_config({}).get("config", {})
        except Exception:
            return {}
        if not isinstance(config, dict):
            return {}
        provider = config.get("provider")
        return dict(provider) if isinstance(provider, dict) else {}
