"""Message execution mixin for the model-first task loop.

The live message path mirrors the haha-cc shape: provider output drives text,
tool calls, task/agent state, and approvals. Route-driven planning executors
are intentionally not part of this mixin.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Callable

logger = logging.getLogger(__name__)

from ..policy.guard import PolicyGuard
from ..provider.failure_recovery import classify_provider_failure
from ..provider.adapter import ProviderAdapter
from ..services.collaboration_service import CollaborationService
from ..services.subagent_service import SubagentService
from ..tools import build_builtin_tools
from ..tools.registry import ToolRegistry
from ..store.sqlite_store import SQLiteStore


class MessageExecutionMixin:
    """Mixin providing model-first message execution and background workers."""

    def _publish_root_child_progress(
        self,
        *,
        session_id: str,
        task: dict[str, Any],
        details: dict[str, Any],
    ) -> None:
        bridge = details.get("_bridge") if isinstance(details.get("_bridge"), dict) else {}
        child_event = bridge.get("childEvent") if isinstance(bridge.get("childEvent"), dict) else {}
        child_type = str(bridge.get("childEventType") or child_event.get("type") or "").strip()
        if child_type not in {
            "tool.started",
            "tool.completed",
            "tool.failed",
            "approval.requested",
            "approval.resolved",
            "command.started",
            "command.completed",
            "command.cancelled",
            "command.failed",
            "patch.proposed",
        }:
            return
        payload = child_event.get("payload") if isinstance(child_event.get("payload"), dict) else {}
        line = self._root_child_progress_line(event_type=child_type, payload=payload)
        if not line:
            return
        self._publish(
            session_id=session_id,
            task=task,
            event_type="task.child.progress",
            payload={
                "childEventType": child_type,
                "summary": line,
                "childPayload": payload,
            },
            visibility="panel",
        )

    @staticmethod
    def _root_child_progress_line(*, event_type: str, payload: dict[str, Any]) -> str | None:
        if event_type == "tool.started":
            tool_name = str(payload.get("toolName") or "tool").strip() or "tool"
            return f"Subtask running tool: {tool_name}"
        if event_type == "tool.completed":
            tool_name = str(payload.get("toolName") or "tool").strip() or "tool"
            return f"Subtask tool completed: {tool_name}"
        if event_type == "tool.failed":
            tool_name = str(payload.get("toolName") or "tool").strip() or "tool"
            return f"Subtask tool failed: {tool_name}"
        if event_type == "approval.requested":
            kind = str(payload.get("kind") or "action").strip() or "action"
            return f"Subtask waiting for approval: {kind}"
        if event_type == "approval.resolved":
            decision = str(payload.get("decision") or "resolved").strip() or "resolved"
            return f"Subtask approval {decision}"
        if event_type == "command.started":
            return "Subtask command started"
        if event_type == "command.completed":
            return "Subtask command completed"
        if event_type == "command.cancelled":
            return "Subtask command cancelled"
        if event_type == "command.failed":
            return "Subtask command failed"
        if event_type == "patch.proposed":
            return str(payload.get("summary") or "Subtask patch proposed").strip() or "Subtask patch proposed"
        return None

    def _recover_loop_failure_with_completion_evidence(
        self,
        *,
        session_id: str,
        task: dict[str, Any],
        goal: str,
        context: dict[str, Any],
        error: Exception,
    ) -> dict[str, Any] | None:
        message = str(error or "").strip()
        if "Provider returned no final answer or tool calls." not in message:
            return None
        task_snapshot = self._store.get_task({"taskId": task["id"]})["task"]
        has_changed_files = bool(task_snapshot.get("changedFiles") or [])
        command_logs = self._store.list_command_logs({"taskId": task["id"], "limit": 50})["commandLogs"]
        has_passing_verification = any(
            isinstance(command, dict)
            and str(command.get("status") or "").strip().lower() in {"completed", "passed", "success"}
            and command.get("exitCode") in (0, "0", None)
            and self._completion_text_mentions_targeted_verification(
                str(command.get("command") or command.get("summary") or "")
            )
            for command in command_logs
        )
        if not has_changed_files or not has_passing_verification:
            return None
        recovered = self._complete_task(
            session_id=session_id,
            task=task_snapshot,
            summary=(
                "Recovered after provider returned no final answer or tool calls. "
                "Using successful verification evidence already recorded in the task."
            ),
            context=context,
            tool_results=[],
            skip_reflection=True,
        )
        if recovered.get("status") != "completed":
            return None
        self._publish(
            session_id=session_id,
            task=recovered,
            event_type="task.loop_failure.recovered",
            payload={
                "taskId": recovered["id"],
                "reason": "provider_empty_final",
                "source": "completion_evidence",
            },
        )
        return {"task": recovered}

    def _execute_message_task(
        self,
        *,
        session_id: str,
        task: dict[str, Any],
        goal: str,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        logger.info("Executing model-first ReAct for task=%s", task["id"])
        # Default session messages follow the haha-cc shape: the model emits
        # text/tool calls, and tools create plan/agent/team/task state when the
        # model chooses them. Legacy planner/supervisor/swarm execution remains
        # available only to direct compatibility callers, not message.send.
        # --- Standard ReAct path ---
        try:
            react_result = self._run_react_loop(
                session_id=session_id,
                task=task,
                goal=goal,
                context=context,
            )
            if react_result["status"] == "waiting_approval":
                return {"task": self._store.get_task({"taskId": task["id"]})["task"]}
            if react_result["status"] == "paused":
                return {"task": self._store.get_task({"taskId": task["id"]})["task"]}
            if react_result["status"] == "cancelled":
                return {"task": self._store.get_task({"taskId": task["id"]})["task"]}
            if react_result["status"] == "completed":
                return {
                    "task": self._complete_task(
                        session_id=session_id,
                        task=task,
                        summary=react_result["summary"],
                        context=context,
                        tool_results=react_result.get("tool_results", []),
                    )
                }
            if react_result["status"] == "failed":
                structured_result = {
                    "toolResults": react_result.get("tool_results", []),
                    "budgetExhausted": react_result.get("budget_exhausted") is True,
                }
                if isinstance(react_result.get("structured_result"), dict):
                    structured_result.update(react_result["structured_result"])
                return {
                    "task": self._fail_task(
                        session_id=session_id,
                        task=task,
                        summary=react_result.get("summary") or "ReAct loop failed.",
                        error_code=react_result.get("error_code") or "REACT_LOOP_FAILED",
                        structured_result=structured_result,
                    )
                }
            return {
                "task": self._fail_task(
                    session_id=session_id,
                    task=task,
                    summary=f"Unexpected ReAct status: {react_result.get('status')}",
                    error_code="REACT_LOOP_UNEXPECTED_STATUS",
                    structured_result={"reactResult": react_result},
                )
            }
        except Exception as exc:  # noqa: BLE001
            logger.error("React loop failed for task=%s: %s", task["id"], exc, exc_info=True)
            recovered = self._recover_loop_failure_with_completion_evidence(
                session_id=session_id,
                task=task,
                goal=goal,
                context=context,
                error=exc,
            )
            if recovered is not None:
                return recovered
            failure_recovery = classify_provider_failure(exc).to_dict()
            return {
                "task": self._fail_task(
                    session_id=session_id,
                    task=task,
                    summary=str(exc),
                    error_code="LOOP_EXECUTION_FAILED",
                    structured_result={"failureRecovery": failure_recovery},
                )
            }

    def _run_background_message(
        self,
        *,
        session_id: str,
        task: dict[str, Any],
        goal: str,
        context: dict[str, Any] | None,
        routing: dict[str, Any] | None = None,
        skill_id: str | None = None,
    ) -> None:
        background_store: SQLiteStore | None = None
        background_cleanup: Callable[[], None] | None = None
        worker = self
        try:
            started_at = time.monotonic()
            logger.info(
                "Background message execution started for task=%s session=%s routing=%s",
                task["id"], session_id, routing,
            )
            worker, background_store, background_cleanup = self._background_worker_orchestrator()
            worker._publish(
                session_id=session_id,
                task=task,
                event_type="task.started",
                payload={
                    "status": task.get("status"),
                    "plan": task.get("plan") or [],
                    "currentStep": task.get("currentStep"),
                    "background": True,
                },
            )
            if context is None:
                lightweight_context = True
                worker._publish(
                    session_id=session_id,
                    task=task,
                    event_type="context.build.started",
                    payload={
                        "status": "running",
                        "lightweight": lightweight_context,
                        "minimal": False,
                        "background": True,
                    },
                    visibility="trace",
                )
                context = worker._context_builder.build(
                    session_id=session_id, goal=goal, skill_id=skill_id, lightweight=lightweight_context,
                    role=task.get("role"), minimal=False,
                )
                worker._publish(
                    session_id=session_id,
                    task=task,
                    event_type="context.build.completed",
                    payload={
                        "status": "completed",
                        "lightweight": lightweight_context,
                        "minimal": False,
                        "background": True,
                        "latency_ms": int((time.monotonic() - started_at) * 1000),
                        "tokenEstimate": ((context.get("budgetStats") or {}).get("estimatedTokens")),
                    },
                    visibility="trace",
                )
                # Emit tool filter event if skill filtering was applied
                worker._maybe_publish_tool_filter(context, skill_id)
                if routing is not None:
                    context["routing"] = routing
                context = worker._context_with_task_focus(context, task)
                context = worker._context_with_worktree_binding(
                    context,
                    (routing or {}).get("activeWorktree") if isinstance(routing, dict) else None,
                )
                worker._publish(
                    session_id=session_id,
                    task=task,
                    event_type="task.updated",
                    payload={
                        "status": task.get("status"),
                        "currentStep": task.get("currentStep"),
                        "context": worker._event_context_summary(context),
                    },
                    visibility="panel",
                )
            else:
                context = worker._context_with_task_focus(context, task)
                context = worker._context_with_worktree_binding(
                    context,
                    (routing or {}).get("activeWorktree") if isinstance(routing, dict) else None,
                )
                worker._publish(
                    session_id=session_id,
                    task=task,
                    event_type="task.updated",
                    payload={
                        "status": task.get("status"),
                        "currentStep": task.get("currentStep"),
                        "context": worker._event_context_summary(context),
                    },
                    visibility="panel",
                )
            _provider_mode = (context.get("config") or {}).get("provider", {}).get("mode", "unknown")
            _will_stream = worker._should_stream_provider({**context, "messages": [], "tools": [], "step": 1})
            logger.info(
                "Background worker executing task=%s strategy=%s provider_mode=%s will_stream=%s",
                task["id"], (context.get("routing") or {}).get("strategy"), _provider_mode, _will_stream,
            )
            worker._execute_message_task(
                session_id=session_id,
                task=task,
                goal=goal,
                context=context,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("Background message execution failed: %s", exc, exc_info=True)
            worker._fail_task(
                session_id=session_id,
                task=task,
                summary=str(exc),
                error_code="BACKGROUND_LOOP_FAILED",
            )
        finally:
            if background_cleanup is not None:
                try:
                    background_cleanup()
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Background worker cleanup failed: %s", exc, exc_info=True)
            if background_store is not None:
                background_store.close()

    def _background_worker_orchestrator(self) -> tuple["Orchestrator", SQLiteStore | None, Callable[[], None] | None]:
        database_path = str(getattr(self._store, "database_path", ":memory:"))
        if database_path == ":memory:":
            return self, None, None

        from .service import Orchestrator  # lazy to avoid circular import
        from ..tools.computer_use import build_env_computer_use_executor

        store = SQLiteStore(database_path)
        config = store.get_config({})["config"]
        policy_guard = PolicyGuard(approval_mode=config["policy"]["approvalMode"])
        collaboration = CollaborationService(store, self._event_bus)
        subagent_service = SubagentService(store, collaboration)
        from ..memory import MemoryManager, MemoryRetriever, MemoryStore
        from ..context.scratchpad import Scratchpad

        bg_memory_manager = MemoryManager(
            store=MemoryStore(store),
            retriever=MemoryRetriever(MemoryStore(store)),
        )
        bg_scratchpad = Scratchpad(store)
        computer_use_executor = build_env_computer_use_executor()
        tool_registry = ToolRegistry(
            build_builtin_tools(
                policy_guard=policy_guard,
                store=store,
                subagent_service=subagent_service,
                memory_manager=bg_memory_manager,
                scratchpad=bg_scratchpad,
                computer_use_executor=computer_use_executor,
            )
        )
        close_computer_use_executor = getattr(computer_use_executor, "close", None)
        return (
            Orchestrator(
                store=store,
                event_bus=self._event_bus,
                tool_registry=tool_registry,
                provider=ProviderAdapter(),
                memory_manager=bg_memory_manager,
                _skip_orphan_cleanup=True,
            ),
            store,
            close_computer_use_executor if callable(close_computer_use_executor) else None,
        )
