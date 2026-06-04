"""Message Execution Mixin — extracted from MessageFlowMixin.

Handles execution strategies: standard ReAct, fast ReAct, planning/DAG,
supervisor, swarm, and background worker management.
"""
from __future__ import annotations

import json
import logging
import re
import time
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

from ..policy.guard import PolicyGuard
from ..provider.failure_recovery import classify_provider_failure
from ..provider.adapter import ProviderAdapter
from ..planner.preflight_split import build_provider_preflight_plan_from_payload
from ..services.command_execution import run_shell_command
from ..services.collaboration_service import CollaborationService
from ..services.subagent_service import SubagentService
from ..context.scratchpad import Scratchpad
from ..tools import build_builtin_tools
from ..tools.run_command import _powershell_execution_command
from ..tools.registry import ToolRegistry
from ..orchestration import OrchestrationMode
from ..planner.types import child_tool_allowlist_for_agent, plan_result_to_dict
from ..store.sqlite_store import SQLiteStore


class MessageExecutionMixin:
    """Mixin providing execution strategies and background worker management."""

    def _publish_planning_thinking(
        self,
        *,
        session_id: str,
        task: dict[str, Any],
        text: str,
        phase: str,
        mode: str = "planning",
        status: str = "running",
        payload: dict[str, Any] | None = None,
    ) -> None:
        event_payload: dict[str, Any] = {
            "text": text,
            "source": f"{mode}_{phase}",
            "phase": phase,
            "mode": mode,
        }
        if isinstance(payload, dict):
            event_payload.update({key: value for key, value in payload.items() if value is not None})
        event_payload["_bridge"] = {"persistTraceMirror": True}
        self._publish(
            session_id=session_id,
            task=task,
            event_type="thinking",
            payload=event_payload,
            visibility="chat",
        )
        self._publish_assistant_progress(
            session_id=session_id,
            task=task,
            text=text,
            phase=phase,
            status=status,
            payload={"mode": mode, "persistTrace": True, **(payload or {})},
            visibility="chat",
        )

    def _publish_root_subtask_progress(
        self,
        *,
        session_id: str,
        task: dict[str, Any],
        event: str,
        details: dict[str, Any],
    ) -> None:
        title = str(details.get("subtaskTitle") or details.get("title") or details.get("subtaskId") or "subtask").strip()
        if not title:
            title = "subtask"
        if event == "started":
            line = f"Started subtask: {title}"
            status = "running"
        elif event == "completed":
            status = str(details.get("status") or "completed").strip() or "completed"
            line = f"Finished subtask: {title} ({status})"
        else:
            return
        self._publish(
            session_id=session_id,
            task=task,
            event_type="task.subtask.progress",
            payload={
                "event": event,
                "title": title,
                "status": status,
                "summary": line,
                "subtaskId": details.get("subtaskId"),
            },
            visibility="panel",
        )

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

    def _persist_pending_dag_execution(
        self,
        *,
        task: dict[str, Any],
        session_id: str,
        goal: str,
        context: dict[str, Any],
        plan: Any,
        execution: dict[str, Any],
        extra_subtasks: list[Any] | None = None,
    ) -> None:
        if not hasattr(self._store, "upsert_pending_dag_state"):
            return
        plan_data = plan_result_to_dict(plan, extra_subtasks=extra_subtasks)
        self._store.upsert_pending_dag_state(
            task_id=task["id"],
            session_id=session_id,
            goal=goal,
            context=context,
            plan_json=json.dumps(plan_data, ensure_ascii=False),
            completed_ids=list(execution.get("completed") or []),
            failed_ids=list(execution.get("failed") or []),
            results=execution.get("results", {}) if isinstance(execution.get("results"), dict) else {},
        )

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
        # --- Planning branch ---
        routing = context.get("routing", {})
        strategy = routing.get("strategy", "react_standard")
        logger.info("Executing strategy=%s for task=%s", strategy, task["id"])
        if routing.get("enable_planning"):
            orch_mode = self._resolve_orchestration_mode(strategy)
            if orch_mode == OrchestrationMode.SUPERVISOR:
                result = self._execute_with_supervisor(
                    session_id=session_id, task=task, goal=goal, context=context,
                )
                if result.get("status") in ("paused", "waiting_approval"):
                    return {"task": self._store.get_task({"taskId": task["id"]})["task"]}
                return result
            if orch_mode == OrchestrationMode.SWARM:
                result = self._execute_with_swarm(
                    session_id=session_id, task=task, goal=goal, context=context,
                )
                if result.get("status") in ("paused", "waiting_approval"):
                    return {"task": self._store.get_task({"taskId": task["id"]})["task"]}
                return result
            result = self._execute_with_planning(
                session_id=session_id, task=task, goal=goal, context=context,
            )
            if result.get("status") in ("paused", "waiting_approval"):
                return {"task": self._store.get_task({"taskId": task["id"]})["task"]}
            return result
        # --- REACT_FAST: simplified path, skip minimal_loop and reflection ---
        if strategy == "react_fast":
            return self._execute_react_fast(
                session_id=session_id, task=task, goal=goal, context=context,
            )
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
            if react_result["status"] == "provider_preflight_split":
                return self._execute_provider_preflight_split(
                    session_id=session_id,
                    task=task,
                    goal=goal,
                    context=context,
                    react_result=react_result,
                )
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
                return {
                    "task": self._fail_task(
                        session_id=session_id,
                        task=task,
                        summary=react_result.get("summary") or "ReAct loop failed.",
                        error_code=react_result.get("error_code") or "REACT_LOOP_FAILED",
                        structured_result={
                            "toolResults": react_result.get("tool_results", []),
                            "budgetExhausted": react_result.get("budget_exhausted") is True,
                        },
                    )
                }

            tool_results = self._run_minimal_loop(
                session_id=session_id,
                task=task,
                goal=goal,
                context=context,
            )
            if task["status"] == "waiting_approval":
                return {"task": task}
            summary = self._provider.summarize_findings(
                goal=goal,
                context=context,
                tool_results=tool_results,
            )
            self._publish(
                session_id=session_id,
                task=task,
                event_type="assistant.token",
                payload={"delta": summary},
            )

            return {
                "task": self._complete_task(
                    session_id=session_id,
                    task=task,
                    summary=summary,
                    context=context,
                    tool_results=tool_results,
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

    def _execute_react_fast(
        self,
        *,
        session_id: str,
        task: dict[str, Any],
        goal: str,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """Fast ReAct path for simple queries. Skips minimal_loop and reflection."""
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
            if react_result["status"] == "provider_preflight_split":
                return self._execute_provider_preflight_split(
                    session_id=session_id,
                    task=task,
                    goal=goal,
                    context=context,
                    react_result=react_result,
                )
            if react_result["status"] == "failed":
                return {
                    "task": self._fail_task(
                        session_id=session_id,
                        task=task,
                        summary=react_result.get("summary") or "ReAct loop failed.",
                        error_code=react_result.get("error_code") or "REACT_LOOP_FAILED",
                        structured_result={
                            "toolResults": react_result.get("tool_results", []),
                            "budgetExhausted": react_result.get("budget_exhausted") is True,
                        },
                    )
                }
            summary = react_result.get("summary") or self._provider.summarize_findings(
                goal=goal,
                context=context,
                tool_results=react_result.get("tool_results", []),
            )
            if not react_result.get("assistant_output_published"):
                self._publish(
                    session_id=session_id,
                    task=task,
                    event_type="assistant.token",
                    payload={"delta": summary},
                )
            return {
                "task": self._complete_task(
                    session_id=session_id,
                    task=task,
                    summary=summary,
                    context=context,
                    tool_results=react_result.get("tool_results", []),
                    skip_reflection=True,
                )
            }
        except Exception as exc:  # noqa: BLE001
            logger.error("Fast react loop failed for task=%s: %s", task["id"], exc, exc_info=True)
            failure_recovery = classify_provider_failure(exc).to_dict()
            return {
                "task": self._fail_task(
                    session_id=session_id,
                    task=task,
                    summary=str(exc),
                    error_code="FAST_LOOP_FAILED",
                    structured_result={"failureRecovery": failure_recovery},
                )
            }

    def _execute_provider_preflight_split(
        self,
        *,
        session_id: str,
        task: dict[str, Any],
        goal: str,
        context: dict[str, Any],
        react_result: dict[str, Any],
    ) -> dict[str, Any]:
        plan = build_provider_preflight_plan_from_payload(react_result.get("preflight_split_plan"))
        if plan is None:
            return {
                "task": self._fail_task(
                    session_id=session_id,
                    task=task,
                    summary="Provider preflight requested splitting, but the split plan was invalid.",
                    error_code="PROVIDER_PREFLIGHT_SPLIT_INVALID",
                    structured_result={"providerPreflight": react_result.get("preflight")},
                )
            }
        updated_context = dict(context)
        updated_context["providerPreflight"] = react_result.get("preflight")
        updated_context["providerPreflightSplitPlan"] = react_result.get("preflight_split_plan")
        updated_context["routing"] = {
            **(context.get("routing") if isinstance(context.get("routing"), dict) else {}),
            "strategy": "plan_execute",
            "enable_planning": True,
            "providerPreflightSplit": True,
        }
        self._publish(
            session_id=session_id,
            task=task,
            event_type="task.provider_preflight.split.started",
            payload={
                "subtaskCount": len(plan.subtasks),
                "executionOrder": plan.execution_order,
                "reason": (react_result.get("preflight_split_plan") or {}).get("reason")
                if isinstance(react_result.get("preflight_split_plan"), dict)
                else None,
            },
        )
        return self._execute_with_planning(
            session_id=session_id,
            task=task,
            goal=goal,
            context=updated_context,
            plan_override=plan,
            plan_source="provider_preflight",
        )

    def _execute_with_planning(
        self,
        *,
        session_id: str,
        task: dict[str, Any],
        goal: str,
        context: dict[str, Any],
        plan_override: Any | None = None,
        plan_source: str = "decomposer",
    ) -> dict[str, Any]:
        """Planning mode: decompose goal → execute subtasks → synthesize."""
        plan_span = self._tracer.start_span(
            "planning",
            trace_id=getattr(self, "_active_trace_id", None),
            attributes={"goal": goal[:200]},
        )
        try:
            self._publish(
                session_id=session_id, task=task,
                event_type="task.planning.started",
                payload={"goal": goal},
            )
            self._publish_planning_thinking(
                session_id=session_id,
                task=task,
                text="正在拆分任务并确定执行顺序。",
                phase="planning_started",
                mode="planning",
                payload={"strategy": "plan_execute"},
            )

            # 1. Decompose
            plan_context = json.dumps(
                context.get("tool_results", []), ensure_ascii=False,
            )[:2000]
            if plan_override is not None:
                plan = plan_override
            else:
                decomp_span = self._tracer.start_span(
                    "plan_decomposition",
                    trace_id=getattr(self, "_active_trace_id", None),
                    attributes={"goal": goal[:200]},
                )
                planning_provider_context = self._planning_provider_context(context)
                self._trace_planning_provider_request(
                    task=task,
                    provider_context=planning_provider_context,
                    operation="planning.decomposition",
                )
                plan = self._decomposer.decompose(
                    goal=goal,
                    context=plan_context,
                    provider_context=planning_provider_context,
                )
                self._trace_planning_provider_response(
                    task=task,
                    plan=plan,
                    operation="planning.decomposition",
                )
                self._tracer.end_span(
                    decomp_span.span_id, status="ok",
                    attributes={"subtask_count": len(plan.subtasks), "execution_order": plan.execution_order},
                )

            self._publish_planning_thinking(
                session_id=session_id,
                task=task,
                text=f"已生成 {len(plan.subtasks)} 个子任务，正在准备执行。",
                phase="planning_decomposed",
                mode="planning",
                payload={
                    "subtaskCount": len(plan.subtasks),
                    "executionOrder": plan.execution_order,
                    "source": plan_source,
                },
            )
            self._publish(
                session_id=session_id, task=task,
                event_type="task.planning.decomposed",
                payload={
                    "subtaskCount": len(plan.subtasks),
                    "executionOrder": plan.execution_order,
                    "source": plan_source,
                },
            )
            # --- Decision trace: decomposition ---
            self._publish(
                session_id=session_id, task=task,
                event_type="agent.decision.decomposition",
                payload={
                    "decision": "decomposed",
                    "subtaskCount": len(plan.subtasks),
                    "parallel": plan.execution_order != [list(range(len(plan.subtasks)))],
                    "source": plan_source,
                },
            )

            if self._is_plan_only_goal(goal):
                summary = self._format_plan_only_summary(goal=goal, plan=plan)
                self._publish(
                    session_id=session_id, task=task,
                    event_type="task.planning.completed",
                    payload={
                        "subtaskCount": len(plan.subtasks),
                        "executionOrder": plan.execution_order,
                        "planOnly": True,
                    },
                )
                self._publish_planning_thinking(
                    session_id=session_id,
                    task=task,
                    text="已整理出计划，正在收尾输出。",
                    phase="planning_completed",
                    mode="planning",
                    status="completed",
                    payload={"planOnly": True, "subtaskCount": len(plan.subtasks)},
                )
                self._tracer.end_span(
                    plan_span.span_id, status="ok",
                    attributes={"subtaskCount": len(plan.subtasks), "planOnly": True},
                )
                return {
                    "task": self._complete_task(
                        session_id=session_id,
                        task=task,
                        summary=summary,
                        context={**context, "_allow_summary_only_completion": True},
                    ),
                }

            # 1b. Plan approval gate (strict mode)
            config = self._store.get_config({})["config"]
            approval_mode = config.get("policy", {}).get("approvalMode", "on_write_or_command")
            if approval_mode == "strict":
                plan_summary = [f"- {s.id}: {s.title}" for s in plan.subtasks]
                approval = self._store.create_approval(
                    task_id=task["id"],
                    kind="plan",
                    request={
                        "goal": goal,
                        "subtaskCount": len(plan.subtasks),
                        "subtasks": plan_summary,
                        "executionOrder": plan.execution_order,
                    },
                )
                plan_data = plan_result_to_dict(plan)
                if hasattr(self._store, "upsert_pending_dag_state"):
                    self._store.upsert_pending_dag_state(
                        task_id=task["id"],
                        session_id=session_id,
                        goal=goal,
                        context=context,
                        plan_json=json.dumps(plan_data, ensure_ascii=False),
                        completed_ids=[],
                        failed_ids=[],
                        results={},
                    )
                self._validate_task_transition(task["status"], "waiting_approval", task["id"])
                self._store.update_task_status(task_id=task["id"], status="waiting_approval")
                self._publish(
                    session_id=session_id, task=task,
                    event_type="approval.requested",
                    payload={
                        "approvalId": approval["id"],
                        "taskId": task["id"],
                        "kind": "plan",
                        "request": {
                            "goal": goal,
                            "subtaskCount": len(plan.subtasks),
                            "subtasks": plan_summary,
                            "executionOrder": plan.execution_order,
                        },
                    },
                )
                self._fire_hooks("on_approval_required", session_id, task, extra_context={"approvalId": approval["id"], "kind": "plan"})
                self._publish(
                    session_id=session_id, task=task,
                    event_type="task.waiting_approval",
                    payload={"status": "waiting_approval", "detail": "执行前需要先审批计划。"},
                )
                self._tracer.end_span(plan_span.span_id, status="ok", attributes={"status": "waiting_plan_approval"})
                return {"status": "waiting_approval"}

            # 2. Execute subtasks
            def _on_subtask_event(subtask_id: str, event: str, details: dict[str, Any]) -> None:
                if event == "progress":
                    self._publish_root_child_progress(
                        session_id=session_id,
                        task=task,
                        details=details,
                    )
                    return
                event_type = f"task.planning.subtask.{event}"
                self._publish(session_id=session_id, task=task, event_type=event_type, payload=details)
                self._publish_root_subtask_progress(
                    session_id=session_id,
                    task=task,
                    event=event,
                    details=details,
                )

            routing = context.get("routing", {})
            self._publish_planning_thinking(
                session_id=session_id,
                task=task,
                text="正在派发子任务并收集执行结果。",
                phase="subtasks_started",
                mode="planning",
                payload={"subtaskCount": len(plan.subtasks)},
            )
            execution = self._dag_executor.execute(
                plan,
                session_id=session_id,
                parent_task_id=task["id"],
                skill_id=(
                    str(routing.get("skill_id")).strip()
                    if isinstance(routing.get("skill_id"), str) and str(routing.get("skill_id")).strip()
                    else None
                ),
                mcp_policy=context.get("mcpPolicy") if isinstance(context.get("mcpPolicy"), dict) else None,
                active_worktree=(
                    routing.get("activeWorktree")
                    if isinstance(routing.get("activeWorktree"), dict)
                    else None
                ),
                max_workers=self._max_parallel_subtasks(context),
                parent_goal=goal,
                child_timeout_ms=self._child_subtask_timeout_ms(context),
                is_paused_fn=lambda: self._store.get_task({"taskId": task["id"]})["task"]["status"] == "paused",
                tracer=self._tracer,
                on_subtask_callback=_on_subtask_event,
                scope_checker=lambda subtasks: self._store.check_dispatch_scope(
                    {"subtasks": subtasks, "taskId": task["id"], "sessionId": session_id}
                ).get("overlaps", []),
            )

            # Handle DAG cooperative pause
            if execution.get("paused"):
                self._persist_pending_dag_execution(
                    task=task,
                    session_id=session_id,
                    goal=goal,
                    context=context,
                    plan=plan,
                    execution=execution,
                )
                if execution.get("waitingApproval") or execution.get("status") == "waiting_approval":
                    latest = self._store.get_task({"taskId": task["id"]})["task"]
                    if latest.get("status") != "waiting_approval":
                        self._validate_task_transition(latest["status"], "waiting_approval", task["id"])
                        latest = self._store.update_task_status(task_id=task["id"], status="waiting_approval")
                    self._publish(
                        session_id=session_id, task=latest,
                        event_type="task.waiting_approval",
                        payload={
                            "status": "waiting_approval",
                            "detail": "Child worker is waiting for approval.",
                            "waitingSubtaskId": execution.get("waitingSubtaskId"),
                        },
                    )
                    self._tracer.end_span(
                        plan_span.span_id,
                        status="ok",
                        attributes={"status": "waiting_approval", "waitingSubtaskId": execution.get("waitingSubtaskId")},
                    )
                    return {"status": "waiting_approval"}
                return {"status": "paused"}

            # 3. Coverage evaluation
            coverage = self._coverage_evaluator.evaluate(
                goal, execution["subtasks"],
            )

            # 4. Auto-supplement if coverage is insufficient
            threshold = routing.get("coverage_threshold", 0.7)
            if coverage < threshold:
                gaps = self._coverage_evaluator.find_gaps(goal, execution["subtasks"])
                if gaps:
                    from ..planner.types import (
                        Subtask as PlanSubtask,
                        child_tool_allowlist_for_agent,
                    )
                    supplement = PlanSubtask(
                        id="supplement-0",
                        title="Address uncovered aspects",
                        description=(
                            f"The original goal has uncovered aspects related to: "
                            f"{', '.join(gaps)}. Please address these."
                        ),
                        dependencies=[
                            s.id for s in execution["subtasks"] if s.status == "completed"
                        ],
                        agent_type="worker",
                    )
                    try:
                        dispatch_result = self._subagent_service.dispatch({
                            "prompt": supplement.description,
                            "title": supplement.title,
                            "sessionId": session_id,
                            "taskId": task["id"],
                            "agentType": supplement.agent_type,
                            "childToolAllowlist": child_tool_allowlist_for_agent(supplement.agent_type),
                        })
                        dispatch_status = str(dispatch_result.get("status") or "").strip().lower()
                        if dispatch_status == "waiting_approval":
                            supplement.status = "waiting_approval"
                            supplement.result = dispatch_result.get("summary") or "Child worker is waiting for approval."
                            execution["subtasks"].append(supplement)
                            execution.setdefault("completed", [])
                            execution.setdefault("failed", [])
                            results = execution.get("results") if isinstance(execution.get("results"), dict) else {}
                            results[supplement.id] = supplement.result
                            execution["results"] = results
                            execution["paused"] = True
                            execution["status"] = "waiting_approval"
                            execution["waitingApproval"] = True
                            execution["waitingSubtaskId"] = supplement.id
                            self._persist_pending_dag_execution(
                                task=task,
                                session_id=session_id,
                                goal=goal,
                                context=context,
                                plan=plan,
                                execution=execution,
                                extra_subtasks=[supplement],
                            )
                            latest = self._store.get_task({"taskId": task["id"]})["task"]
                            if latest.get("status") != "waiting_approval":
                                self._validate_task_transition(latest["status"], "waiting_approval", task["id"])
                                latest = self._store.update_task_status(task_id=task["id"], status="waiting_approval")
                            self._publish(
                                session_id=session_id, task=latest,
                                event_type="task.waiting_approval",
                                payload={
                                    "status": "waiting_approval",
                                    "detail": "Supplement worker is waiting for approval.",
                                    "waitingSubtaskId": supplement.id,
                                },
                            )
                            self._tracer.end_span(
                                plan_span.span_id,
                                status="ok",
                                attributes={"status": "waiting_approval", "waitingSubtaskId": supplement.id},
                            )
                            return {"status": "waiting_approval"}
                        supplement.status = "completed"
                        supplement.result = dispatch_result.get("summary") or "Completed"
                        execution["subtasks"].append(supplement)
                        # Re-evaluate coverage after supplement
                        coverage = self._coverage_evaluator.evaluate(goal, execution["subtasks"])
                        logger.info(
                            "Supplement subtask executed, coverage: %.2f → %.2f",
                            coverage, coverage,
                        )
                    except Exception as supp_exc:  # noqa: BLE001
                        logger.warning("Supplement subtask failed: %s", supp_exc)

            # 5. Pre-merge git diff check
            merge_check = self._check_git_diff_before_merge(
                session_id=session_id,
                task=task,
                execution=execution,
                workspace_root=context.get("workspace_root") if context else None,
            )
            if not merge_check["safe"]:
                logger.warning(
                    "Pre-merge check found issues for task=%s: %s",
                    task["id"], merge_check["warnings"],
                )

            summary = execution["summary"]
            self._publish_planning_thinking(
                session_id=session_id,
                task=task,
                text="子任务已返回，正在合并结果与检查完成条件。",
                phase="synthesis_started",
                mode="planning",
                payload={
                    "success": execution.get("success"),
                    "completed": len(execution.get("completed") or []),
                    "failed": len(execution.get("failed") or []),
                },
            )

            if execution["success"] is False:
                completion_recovery: dict[str, Any] | None = None
                recovery = self._recover_planning_failures_with_verification(
                    session_id=session_id,
                    task=task,
                    execution=execution,
                    workspace_root=context.get("workspace_root") if context else None,
                    context=context,
                )
                if recovery.get("recovered"):
                    execution["success"] = True
                    execution["verificationRecovery"] = recovery
                    summary = self._append_planning_recovery_summary(summary, recovery)
                    execution["summary"] = summary

                    self._publish(
                        session_id=session_id, task=task,
                        event_type="task.planning.recovered",
                        payload={
                            "recovered": True,
                            "commands": recovery.get("commands", []),
                            "failedSubtaskIds": recovery.get("failedSubtaskIds", []),
                        },
                    )
                else:
                    completion_recovery = self._recover_planning_failures_with_completion_evidence(
                        session_id=session_id,
                        task=task,
                        execution=execution,
                        context=context,
                    )
                    if completion_recovery.get("recovered"):
                        execution["success"] = True
                        execution["completionRecovery"] = completion_recovery
                        summary = self._append_planning_completion_recovery_summary(summary, completion_recovery)
                        execution["summary"] = summary

                        self._publish(
                            session_id=session_id, task=task,
                            event_type="task.planning.recovered",
                            payload={
                                "recovered": True,
                                "mode": "completion_evidence",
                                "failedSubtaskIds": completion_recovery.get("failedSubtaskIds", []),
                            },
                        )
                    elif completion_recovery.get("probeStatus") == "waiting_approval":
                        execution["completionRecovery"] = completion_recovery
                        self._publish(
                            session_id=session_id, task=task,
                            event_type="task.planning.completed",
                            payload={
                                "coverage": coverage,
                                "success": execution["success"],
                                "partialHandoffs": execution.get("partialHandoffs", []),
                                "verificationRecovery": recovery,
                                "completionRecovery": completion_recovery,
                                "status": "waiting_approval",
                            },
                        )
                        self._tracer.end_span(
                            plan_span.span_id, status="ok",
                            attributes={
                                "subtaskCount": len(execution["subtasks"]),
                                "coverage": coverage,
                                "status": "waiting_completion_review",
                            },
                        )
                        return {"status": "waiting_approval"}
                if execution["success"] is False:
                    self._publish(
                        session_id=session_id, task=task,
                        event_type="task.planning.completed",
                        payload={
                            "coverage": coverage,
                            "success": execution["success"],
                            "partialHandoffs": execution.get("partialHandoffs", []),
                            "verificationRecovery": recovery,
                            "completionRecovery": completion_recovery,
                        },
                    )
                    self._tracer.end_span(
                        plan_span.span_id, status="error",
                        attributes={"subtaskCount": len(execution["subtasks"]), "coverage": coverage},
                    )
                    return {
                        "task": self._fail_task(
                            session_id=session_id,
                            task=task,
                            summary=summary,
                            error_code="PLANNING_SUBTASKS_FAILED",
                            structured_result={
                                "status": "failed",
                                "coverage": coverage,
                                "partialHandoffs": execution.get("partialHandoffs", []),
                                "verificationRecovery": recovery,
                                "completionRecovery": completion_recovery,
                                "subtasks": [
                                    {
                                        "id": subtask.id,
                                        "title": subtask.title,
                                        "status": subtask.status,
                                        "result": subtask.result,
                                    }
                                    for subtask in execution["subtasks"]
                                ],
                            },
                        ),
                    }

            if execution["success"] is False:
                self._publish(
                    session_id=session_id, task=task,
                    event_type="task.planning.completed",
                    payload={
                        "coverage": coverage,
                        "success": execution["success"],
                        "partialHandoffs": execution.get("partialHandoffs", []),
                    },
                )
                self._tracer.end_span(
                    plan_span.span_id, status="error",
                    attributes={"subtaskCount": len(execution["subtasks"]), "coverage": coverage},
                )
                return {
                    "task": self._fail_task(
                        session_id=session_id,
                        task=task,
                        summary=summary,
                        error_code="PLANNING_SUBTASKS_FAILED",
                        structured_result={
                            "status": "failed",
                            "coverage": coverage,
                            "partialHandoffs": execution.get("partialHandoffs", []),
                            "subtasks": [
                                {
                                    "id": subtask.id,
                                    "title": subtask.title,
                                    "status": subtask.status,
                                    "result": subtask.result,
                                }
                                for subtask in execution["subtasks"]
                            ],
                        },
                    ),
                }

            self._publish(
                session_id=session_id, task=task,
                event_type="task.planning.completed",
                payload={
                    "coverage": coverage,
                    "success": execution["success"],
                    "partialHandoffs": execution.get("partialHandoffs", []),
                },
            )
            self._publish_planning_thinking(
                session_id=session_id,
                task=task,
                text="规划执行已完成，正在生成最终答复。",
                phase="planning_completed",
                mode="planning",
                status="completed",
                payload={"coverage": coverage, "success": execution["success"]},
            )

            self._tracer.end_span(
                plan_span.span_id, status="ok",
                attributes={"subtaskCount": len(execution["subtasks"]), "coverage": coverage},
            )

            return {
                "task": self._complete_task(
                    session_id=session_id,
                    task=task,
                    summary=summary,
                    context=context,
                    tool_results=self._planning_completion_tool_results(execution),
                ),
            }
        except Exception as exc:  # noqa: BLE001
            logger.error("Planning execution failed for task=%s: %s", task["id"], exc, exc_info=True)
            self._tracer.end_span(plan_span.span_id, status="error")
            return {
                "task": self._fail_task(
                    session_id=session_id,
                    task=task,
                    summary=str(exc),
                    error_code="PLANNING_EXECUTION_FAILED",
                ),
            }

    def _recover_planning_failures_with_completion_evidence(
        self,
        *,
        session_id: str,
        task: dict[str, Any],
        execution: dict[str, Any],
        context: dict[str, Any] | None,
    ) -> dict[str, Any]:
        failed_subtasks = [
            subtask for subtask in execution.get("subtasks", [])
            if getattr(subtask, "status", None) == "failed"
        ]
        if not failed_subtasks:
            return {"recovered": False, "reason": "no_failed_subtasks"}
        failed_ids = sorted({
            str(getattr(subtask, "id", "")).strip()
            for subtask in failed_subtasks
            if str(getattr(subtask, "id", "")).strip()
        })
        structured_subtasks = [
            {
                "id": subtask.id,
                "title": subtask.title,
                "status": subtask.status,
                "result": subtask.result,
            }
            for subtask in execution.get("subtasks", [])
        ]
        probe = self._complete_task(
            session_id=session_id,
            task=task,
            summary=execution.get("summary") or "",
            context=context or {},
            tool_results=self._planning_completion_tool_results(execution),
            skip_reflection=True,
            skip_drain=True,
        )
        if probe.get("status") != "completed":
            return {
                "recovered": False,
                "reason": "completion_gate_blocked",
                "failedSubtaskIds": failed_ids,
                "probeStatus": probe.get("status"),
                "probeErrorCode": probe.get("errorCode"),
            }
        structured = probe.get("structuredResult") if isinstance(probe.get("structuredResult"), dict) else {}
        evidence = structured.get("completionEvidence") if isinstance(structured.get("completionEvidence"), dict) else {}
        counts = evidence.get("counts") if isinstance(evidence.get("counts"), dict) else {}
        failed_tool_results = int(counts.get("failedToolResults") or 0)
        if failed_tool_results > 0:
            return {
                "recovered": False,
                "reason": "unresolved_failed_tool_results",
                "failedSubtaskIds": failed_ids,
                "failedToolResults": failed_tool_results,
            }
        self._planning_record_recovered_failed_subtasks(execution, failed_subtasks)
        for subtask in failed_subtasks:
            subtask.status = "completed"
            subtask.result = self._append_recovered_subtask_result(
                str(subtask.result or "Failed subtask."),
                [],
            )
        execution["failed"] = [item for item in execution.get("failed", []) if str(item) not in set(failed_ids)]
        completed = {str(item) for item in execution.get("completed", [])}
        completed.update(failed_ids)
        execution["completed"] = sorted(completed)
        execution["subtasks"] = execution.get("subtasks", [])
        execution["recoveredStructuredResult"] = structured
        execution["recoveredCompletionEvidence"] = evidence
        execution["summary"] = probe.get("resultSummary") or execution.get("summary") or ""
        return {
            "recovered": True,
            "reason": "completion_evidence_satisfied",
            "failedSubtaskIds": failed_ids,
            "subtasks": structured_subtasks,
            "completionEvidence": evidence,
        }

    @staticmethod
    def _append_planning_completion_recovery_summary(summary: str, recovery: dict[str, Any]) -> str:
        failed_ids = recovery.get("failedSubtaskIds") or []
        if not failed_ids:
            return summary
        suffix = (
            "\nPlanning recovery: downstream completion evidence and root-level verification "
            f"resolved earlier failed subtask(s): {', '.join(str(item) for item in failed_ids)}."
        )
        if suffix.strip() in summary:
            return summary
        return f"{summary}{suffix}"

    @staticmethod
    def _planning_failed_subtask_snapshot(subtask: Any) -> dict[str, Any]:
        return {
            "id": str(getattr(subtask, "id", "")).strip(),
            "title": str(getattr(subtask, "title", "")).strip(),
            "status": str(getattr(subtask, "status", "")).strip().lower(),
            "result": str(getattr(subtask, "result", "") or "").strip(),
            "agentType": str(getattr(subtask, "agent_type", "") or "").strip(),
            "ownedScope": list(getattr(subtask, "owned_scope", []) or []),
            "expectedArtifacts": [dict(item) for item in (getattr(subtask, "expected_artifacts", []) or []) if isinstance(item, dict)],
            "verificationRequirements": [dict(item) for item in (getattr(subtask, "verification_requirements", []) or []) if isinstance(item, dict)],
        }

    def _planning_record_recovered_failed_subtasks(self, execution: dict[str, Any], failed_subtasks: list[Any]) -> None:
        snapshots = execution.setdefault("recoveredFailedSubtasks", [])
        if not isinstance(snapshots, list):
            snapshots = []
            execution["recoveredFailedSubtasks"] = snapshots
        existing_ids = {
            str(item.get("id") or "").strip()
            for item in snapshots
            if isinstance(item, dict)
        }
        for subtask in failed_subtasks:
            snapshot = self._planning_failed_subtask_snapshot(subtask)
            snapshot_id = snapshot.get("id")
            if snapshot_id and snapshot_id in existing_ids:
                continue
            snapshots.append(snapshot)
            if snapshot_id:
                existing_ids.add(snapshot_id)

    def _planning_completion_tool_results(self, execution: dict[str, Any]) -> list[dict[str, Any]]:
        tool_results: list[dict[str, Any]] = []
        recovered_failed = execution.get("recoveredFailedSubtasks")
        if isinstance(recovered_failed, list):
            for item in recovered_failed:
                if not isinstance(item, dict):
                    continue
                tool_results.append({
                    "name": "child_task",
                    "status": item.get("status") or "failed",
                    "failed": True,
                    "summary": item.get("result") or item.get("title") or "Failed subtask",
                    "title": item.get("title"),
                    "agentType": item.get("agentType"),
                    "childTaskId": item.get("id"),
                    "ownedScope": item.get("ownedScope") or [],
                    "expectedArtifacts": item.get("expectedArtifacts") or [],
                    "verificationRequirements": item.get("verificationRequirements") or [],
                })
        recovered_ids = {
            str(item.get("id") or "").strip()
            for item in (recovered_failed or [])
            if isinstance(item, dict) and str(item.get("id") or "").strip()
        }
        for subtask in execution.get("subtasks", []) or []:
            subtask_id = str(getattr(subtask, "id", "")).strip()
            if not subtask_id or subtask_id not in recovered_ids:
                continue
            status = str(getattr(subtask, "status", "")).strip().lower()
            tool_results.append({
                "name": "child_task",
                "status": status or "completed",
                "failed": status in {"failed", "cancelled"},
                "summary": str(getattr(subtask, "result", "") or getattr(subtask, "title", "") or "Completed subtask").strip(),
                "title": str(getattr(subtask, "title", "")).strip(),
                "agentType": str(getattr(subtask, "agent_type", "") or "").strip(),
                "childTaskId": subtask_id,
                "ownedScope": list(getattr(subtask, "owned_scope", []) or []),
                "expectedArtifacts": [dict(item) for item in (getattr(subtask, "expected_artifacts", []) or []) if isinstance(item, dict)],
                "verificationRequirements": [dict(item) for item in (getattr(subtask, "verification_requirements", []) or []) if isinstance(item, dict)],
            })
        return tool_results

    def _resolve_orchestration_mode(self, strategy: str) -> OrchestrationMode:
        """Map an ExecutionStrategy string to an OrchestrationMode."""
        if strategy == "plan_supervise":
            return OrchestrationMode.SUPERVISOR
        if strategy == "plan_swarm":
            return OrchestrationMode.SWARM
        return OrchestrationMode.DAG

    def _planning_provider_context(self, context: dict[str, Any]) -> dict[str, Any]:
        config = context.get("config") if isinstance(context, dict) else None
        if not isinstance(config, dict):
            try:
                config = self._store.get_config({}).get("config")
            except Exception:  # noqa: BLE001
                config = None
        if not isinstance(config, dict):
            return {}
        adjusted = deepcopy(config)
        provider = adjusted.get("provider")
        if isinstance(provider, dict):
            timeout = self._planning_timeout_seconds(provider)
            stream_timeout = self._planning_stream_timeout_seconds(provider, timeout=timeout)
            provider["timeout"] = timeout
            provider["timeoutSeconds"] = timeout
            provider["streamTimeout"] = stream_timeout
            provider["streamTimeoutSeconds"] = stream_timeout
            profiles = provider.get("profiles")
            if isinstance(profiles, list):
                for profile in profiles:
                    if isinstance(profile, dict):
                        profile_timeout = self._planning_timeout_seconds(profile, fallback=timeout)
                        profile_stream_timeout = self._planning_stream_timeout_seconds(profile, timeout=profile_timeout)
                        profile["timeout"] = profile_timeout
                        profile["timeoutSeconds"] = profile_timeout
                        profile["streamTimeout"] = profile_stream_timeout
                        profile["streamTimeoutSeconds"] = profile_stream_timeout
        return {"config": adjusted}

    def _trace_planning_provider_request(
        self,
        *,
        task: dict[str, Any],
        provider_context: dict[str, Any],
        operation: str,
    ) -> None:
        self._append_provider_trace(
            task=task,
            event_type="provider.request",
            payload={
                **self._provider_trace_payload(provider_context),
                "step": provider_context.get("step") or operation,
                "operation": operation,
                "stream": False,
            },
        )

    def _trace_planning_provider_response(
        self,
        *,
        task: dict[str, Any],
        plan: Any,
        operation: str,
    ) -> None:
        self._append_provider_trace(
            task=task,
            event_type="provider.response",
            payload={
                **self._provider_response_trace(getattr(plan, "provider_response", None)),
                "operation": operation,
                "subtaskCount": len(getattr(plan, "subtasks", []) or []),
                "stream": False,
            },
        )

    @staticmethod
    def _planning_timeout_seconds(provider: dict[str, Any], *, fallback: float = 180.0) -> float:
        for key in ("planningTimeoutSeconds", "planningTimeout", "timeout", "timeoutSeconds"):
            try:
                value = float(provider.get(key))
            except (TypeError, ValueError):
                continue
            if value > 0:
                return max(1.0, value)
        return fallback

    @staticmethod
    def _planning_stream_timeout_seconds(provider: dict[str, Any], *, timeout: float) -> float:
        for key in ("planningStreamTimeoutSeconds", "planningStreamTimeout", "streamTimeout", "streamTimeoutSeconds"):
            try:
                value = float(provider.get(key))
            except (TypeError, ValueError):
                continue
            if value > 0:
                return max(1.0, value)
        return max(timeout, 600.0)

    @staticmethod
    def _is_plan_only_goal(goal: str) -> bool:
        lowered = goal.casefold()
        execution_markers = (
            "run:",
            "run ",
            "execute ",
            "read ",
            "inspect ",
            "pytest",
            "npm ",
            "python ",
            "powershell",
            "shell",
            "command",
            "\u8fd0\u884c",
            "\u6267\u884c",
            "\u8bfb\u53d6",
            "\u68c0\u67e5",
        )
        plan_only_markers = (
            "only output",
            "plan only",
            "only provide a plan",
            "only give a plan",
            "do not execute",
            "don't execute",
            "do not run commands",
            "\u53ea\u9700\u8981\u8f93\u51fa\u65b9\u6848",
            "\u53ea\u8f93\u51fa\u65b9\u6848",
            "\u53ea\u7ed9\u65b9\u6848",
            "\u53ea\u505a\u65b9\u6848",
            "\u4e0d\u8981\u6267\u884c",
            "\u4e0d\u8981\u8fd0\u884c\u547d\u4ee4",
        )
        if any(marker in lowered for marker in plan_only_markers):
            return True
        plan_markers = (
            "do not implement",
            "don't implement",
            "do not create files",
            "\u4e0d\u8981\u5b9e\u73b0",
            "\u4e0d\u8981\u521b\u5efa\u6587\u4ef6",
        )
        if not any(marker in lowered for marker in plan_markers):
            return False
        return not any(marker in lowered for marker in execution_markers)

    @staticmethod
    def _format_plan_only_summary(*, goal: str, plan: Any) -> str:
        lines = [
            "\u8fd9\u662f\u4e00\u4e2a\u590d\u6742\u4efb\u52a1\uff0c\u672c\u8f6e\u53ea\u8f93\u51fa\u65b9\u6848\uff0c\u4e0d\u6267\u884c\u4ee3\u7801\u5b9e\u73b0\u3002",
            "",
            "\u4efb\u52a1\u89c4\u5212\uff1a",
        ]
        subtask_by_id = {subtask.id: subtask for subtask in plan.subtasks}
        for index, subtask_id in enumerate(plan.execution_order, start=1):
            subtask = subtask_by_id.get(subtask_id)
            if subtask is None:
                continue
            deps = ", ".join(subtask.dependencies) if subtask.dependencies else "\u65e0"
            lines.append(f"{index}. {subtask.title}")
            lines.append(f"   - agent: {subtask.id}")
            lines.append(f"   - \u4f9d\u8d56: {deps}")
            lines.append(f"   - \u5de5\u4f5c\u5185\u5bb9: {subtask.description}")
        lines.extend([
            "",
            "\u5e76\u884c\u5efa\u8bae\uff1a\u65e0\u4f9d\u8d56\u7684\u5b50\u4efb\u52a1\u53ef\u4ea4\u7ed9\u591a\u4e2a agent \u5e76\u884c\uff1b\u6709\u4f9d\u8d56\u7684\u5b50\u4efb\u52a1\u6309\u4e0a\u9762\u987a\u5e8f\u4e32\u884c\u3002",
            "\u98ce\u9669\u70b9\uff1aAPI \u5951\u7ea6\u4e0d\u4e00\u81f4\u3001\u6570\u636e\u5b58\u50a8\u548c\u6821\u9a8c\u8fb9\u754c\u4e0d\u6e05\u3001\u6d4b\u8bd5\u8986\u76d6\u4e0d\u8db3\u3001\u6587\u6863\u548c\u5b9e\u73b0\u8131\u8282\u3002",
            "\u9a8c\u8bc1\u65b9\u5f0f\uff1a\u5148\u68c0\u67e5\u65b9\u6848\u662f\u5426\u8986\u76d6\u524d\u7aef\u3001\u540e\u7aef\u3001\u6570\u636e\u3001\u6d4b\u8bd5\u548c\u6587\u6863\uff1b\u5b9e\u65bd\u9636\u6bb5\u518d\u8fd0\u884c\u5355\u5143\u6d4b\u8bd5\u3001API \u96c6\u6210\u6d4b\u8bd5\u548c\u7aef\u5230\u7aef\u9a8c\u8bc1\u3002",
            "",
            f"\u539f\u59cb\u76ee\u6807\uff1a{goal}",
        ])
        return "\n".join(lines)

    def _check_plan_approval(
        self,
        *,
        session_id: str,
        task: dict[str, Any],
        goal: str,
        context: dict[str, Any],
        orchestration_mode: str,
        plan: Any,
        span: Any,
    ) -> dict[str, Any] | None:
        """Check if plan approval is required. Returns response dict if waiting, None if okay to proceed."""
        config = self._store.get_config({})["config"]
        approval_mode = config.get("policy", {}).get("approvalMode", "on_write_or_command")
        if approval_mode != "strict":
            return None

        plan_summary = [f"- {s.id}: {s.title}" for s in plan.subtasks]
        approval = self._store.create_approval(
            task_id=task["id"],
            kind="plan",
            request={
                "goal": goal,
                "subtaskCount": len(plan.subtasks),
                "subtasks": plan_summary,
                "executionOrder": plan.execution_order,
                "orchestrationMode": orchestration_mode,
            },
        )
        plan_data = plan_result_to_dict(plan)
        context_with_mode = {**context, "orchestration_mode": orchestration_mode}
        if hasattr(self._store, "upsert_pending_dag_state"):
            self._store.upsert_pending_dag_state(
                task_id=task["id"],
                session_id=session_id,
                goal=goal,
                context=context_with_mode,
                plan_json=json.dumps(plan_data, ensure_ascii=False),
                completed_ids=[],
                failed_ids=[],
                results={},
            )
        self._validate_task_transition(task["status"], "waiting_approval", task["id"])
        self._store.update_task_status(task_id=task["id"], status="waiting_approval")
        self._publish(
            session_id=session_id, task=task,
            event_type="approval.requested",
            payload={
                "approvalId": approval["id"],
                "taskId": task["id"],
                "kind": "plan",
                "request": {
                    "goal": goal,
                    "subtaskCount": len(plan.subtasks),
                    "subtasks": plan_summary,
                    "executionOrder": plan.execution_order,
                    "orchestrationMode": orchestration_mode,
                },
            },
        )
        self._fire_hooks("on_approval_required", session_id, task, extra_context={"approvalId": approval["id"], "kind": "plan"})
        self._publish(
            session_id=session_id, task=task,
            event_type="task.waiting_approval",
            payload={"status": "waiting_approval", "detail": "执行前需要先审批计划。"},
        )
        self._tracer.end_span(span.span_id, status="ok", attributes={"status": "waiting_plan_approval"})
        return {"status": "waiting_approval"}

    def _recover_planning_failures_with_verification(
        self,
        *,
        session_id: str,
        task: dict[str, Any],
        execution: dict[str, Any],
        workspace_root: Any,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        failed_subtasks = [
            subtask for subtask in execution.get("subtasks", [])
            if getattr(subtask, "status", None) == "failed"
        ]
        if not failed_subtasks:
            return {"recovered": False, "reason": "no_failed_subtasks"}
        failed_ids = {
            str(getattr(subtask, "id", "")).strip()
            for subtask in failed_subtasks
            if str(getattr(subtask, "id", "")).strip()
        }
        partial_handoffs = [
            handoff for handoff in execution.get("partialHandoffs", [])
            if isinstance(handoff, dict) and str(handoff.get("subtaskId") or "").strip() in failed_ids
        ]
        pending_commands = self._planning_recovery_pending_verification_commands(partial_handoffs)
        if not pending_commands:
            return {
                "recovered": False,
                "reason": "no_pending_verification",
                "failedSubtaskIds": sorted(failed_ids),
            }
        if not self._planning_failures_are_verification_only(failed_subtasks, partial_handoffs):
            return {
                "recovered": False,
                "reason": "non_verification_failures",
                "failedSubtaskIds": sorted(failed_ids),
                "commands": pending_commands,
            }
        workspace = self._planning_recovery_workspace(workspace_root)
        if workspace is None:
            return {
                "recovered": False,
                "reason": "workspace_unavailable",
                "failedSubtaskIds": sorted(failed_ids),
                "commands": pending_commands,
            }

        results: list[dict[str, Any]] = []
        all_passed = True
        for command in pending_commands:
            result = self._run_planning_recovery_command(
                session_id=session_id,
                task=task,
                workspace=workspace,
                command=command,
            )
            results.append(result)
            if result.get("status") != "completed" or result.get("exitCode") not in (0, None):
                all_passed = False

        if not all_passed:
            repair = self._repair_planning_failures_with_child_task(
                session_id=session_id,
                task=task,
                execution=execution,
                workspace=workspace,
                failed_subtasks=failed_subtasks,
                failed_commands=results,
                partial_handoffs=partial_handoffs,
                pending_commands=pending_commands,
                context=context or {},
            )
            if repair.get("recovered"):
                return repair
            return {
                "recovered": False,
                "reason": "verification_failed",
                "failedSubtaskIds": sorted(failed_ids),
                "commands": results,
            }

        self._planning_record_recovered_failed_subtasks(execution, failed_subtasks)
        for subtask in failed_subtasks:
            subtask.status = "completed"
            subtask.result = self._append_recovered_subtask_result(str(subtask.result or "Failed subtask."), results)
        execution["failed"] = [
            item for item in execution.get("failed", [])
            if str(item) not in failed_ids
        ]
        completed = {str(item) for item in execution.get("completed", [])}
        completed.update(failed_ids)
        execution["completed"] = sorted(completed)
        return {
            "recovered": True,
            "reason": "pending_verification_passed",
            "failedSubtaskIds": sorted(failed_ids),
            "commands": results,
        }

    def _repair_planning_failures_with_child_task(
        self,
        *,
        session_id: str,
        task: dict[str, Any],
        execution: dict[str, Any],
        workspace: Path,
        failed_subtasks: list[Any],
        failed_commands: list[dict[str, Any]],
        partial_handoffs: list[dict[str, Any]],
        pending_commands: list[str],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        prompt = self._build_planning_repair_prompt(
            task=task,
            execution=execution,
            failed_subtasks=failed_subtasks,
            failed_commands=failed_commands,
            partial_handoffs=partial_handoffs,
            pending_commands=pending_commands,
        )
        dispatch_result = self._subagent_service.dispatch({
            "prompt": prompt,
            "planningPrompt": prompt,
            "title": "Repair failed planning verification",
            "sessionId": session_id,
            "taskId": task["id"],
            "agentType": "worker",
            "childToolAllowlist": child_tool_allowlist_for_agent("worker"),
            "timeoutMs": self._child_subtask_timeout_ms(context),
            "profile": {
                "ownedScope": self._planning_repair_owned_scope(failed_subtasks, partial_handoffs),
                "verificationRequirements": [
                    {"kind": "command", "command": command}
                    for command in pending_commands
                ],
            },
        })
        if str(dispatch_result.get("status") or "").strip().lower() == "waiting_approval":
            return {
                "recovered": False,
                "reason": "repair_waiting_approval",
                "failedSubtaskIds": self._planning_failed_ids(failed_subtasks),
                "commands": failed_commands,
            }
        if str(dispatch_result.get("status") or "").strip().lower() == "failed":
            return {
                "recovered": False,
                "reason": "repair_child_failed",
                "failedSubtaskIds": self._planning_failed_ids(failed_subtasks),
                "commands": failed_commands,
                "repairSummary": dispatch_result.get("summary"),
                "repairError": dispatch_result.get("error"),
            }

        post_repair_results: list[dict[str, Any]] = []
        all_passed = True
        for command in pending_commands:
            result = self._run_planning_recovery_command(
                session_id=session_id,
                task=task,
                workspace=workspace,
                command=command,
            )
            post_repair_results.append(result)
            if result.get("status") != "completed" or result.get("exitCode") not in (0, None):
                all_passed = False

        all_commands = [*failed_commands, *post_repair_results]
        failed_ids = self._planning_failed_ids(failed_subtasks)
        if not all_passed:
            return {
                "recovered": False,
                "reason": "repair_verification_failed",
                "failedSubtaskIds": failed_ids,
                "commands": all_commands,
                "repairSummary": dispatch_result.get("summary"),
            }

        self._planning_record_recovered_failed_subtasks(execution, failed_subtasks)
        for subtask in failed_subtasks:
            subtask.status = "completed"
            subtask.result = self._append_repaired_subtask_result(
                str(getattr(subtask, "result", "") or "Failed subtask."),
                dispatch_result,
                post_repair_results,
            )
        self._planning_mark_skipped_dependents_repaired(execution, dispatch_result, post_repair_results)
        execution["failed"] = [
            item for item in execution.get("failed", [])
            if str(item) not in set(failed_ids)
        ]
        completed = {str(item) for item in execution.get("completed", [])}
        completed.update(failed_ids)
        completed.update(
            str(getattr(subtask, "id", "")).strip()
            for subtask in execution.get("subtasks", [])
            if str(getattr(subtask, "status", "")).strip().lower() == "completed"
            and str(getattr(subtask, "id", "")).strip()
        )
        execution["completed"] = sorted(completed)
        return {
            "recovered": True,
            "reason": "repair_child_verified",
            "failedSubtaskIds": failed_ids,
            "commands": all_commands,
            "repairSummary": dispatch_result.get("summary"),
        }

    def _build_planning_repair_prompt(
        self,
        *,
        task: dict[str, Any],
        execution: dict[str, Any],
        failed_subtasks: list[Any],
        failed_commands: list[dict[str, Any]],
        partial_handoffs: list[dict[str, Any]],
        pending_commands: list[str],
    ) -> str:
        skipped_subtasks = [
            subtask for subtask in execution.get("subtasks", [])
            if str(getattr(subtask, "status", "")).strip().lower() == "skipped"
        ]
        return "\n".join([
            "A planning workflow failed after a verification-oriented child task left repairable work.",
            "Continue from the existing workspace. Inspect the files and command artifacts first, preserve useful work, repair the root cause, finish any downstream plan items that were skipped because of the failure, and rerun the required verification.",
            "",
            f"Parent goal: {str(task.get('goal') or '')[:3000]}",
            "",
            "Failed subtask facts:",
            json.dumps([self._planning_failed_subtask_snapshot(item) for item in failed_subtasks], ensure_ascii=False, indent=2)[:6000],
            "",
            "Skipped downstream plan items to finish if still relevant:",
            json.dumps([self._planning_failed_subtask_snapshot(item) for item in skipped_subtasks], ensure_ascii=False, indent=2)[:4000],
            "",
            "Partial handoffs:",
            json.dumps(partial_handoffs, ensure_ascii=False, indent=2)[:6000],
            "",
            "Failed verification commands and observed output:",
            json.dumps(self._planning_repair_command_facts(failed_commands), ensure_ascii=False, indent=2)[:8000],
            "",
            "Required verification commands to rerun:",
            "\n".join(f"- {command}" for command in pending_commands),
            "",
            "Repair contract:",
            "- Do not reinterpret the parent goal or mark success by summary alone.",
            "- Fix the underlying code, tests, docs, or wiring indicated by the observed facts.",
            "- Complete skipped downstream artifacts when they are still part of the parent goal.",
            "- Run the listed verification commands after repairs whenever possible.",
            "- In the final summary, list changed files, verification commands, and residual risks.",
        ])

    def _planning_repair_command_facts(self, commands: list[dict[str, Any]]) -> list[dict[str, Any]]:
        facts: list[dict[str, Any]] = []
        for command in commands:
            if not isinstance(command, dict):
                continue
            command_log_id = str(command.get("commandLogId") or "").strip()
            fact = {
                "command": command.get("command"),
                "status": command.get("status"),
                "exitCode": command.get("exitCode"),
                "durationMs": command.get("durationMs"),
            }
            if command_log_id:
                try:
                    log = self._store.get_command_log({"commandId": command_log_id}).get("commandLog", {})
                    for key in ("stdoutPath", "stderrPath"):
                        path = log.get(key)
                        if isinstance(path, str) and path.strip():
                            text = Path(path).read_text(encoding="utf-8", errors="replace")
                            fact[key.removesuffix("Path")] = text[-3000:]
                except Exception:  # noqa: BLE001
                    logger.debug("Failed reading planning repair command artifact", exc_info=True)
            facts.append(fact)
        return facts

    def _planning_repair_owned_scope(
        self,
        failed_subtasks: list[Any],
        partial_handoffs: list[dict[str, Any]],
    ) -> list[str]:
        scope: list[str] = []
        for subtask in failed_subtasks:
            scope.extend(str(item) for item in (getattr(subtask, "owned_scope", []) or []))
        for handoff in partial_handoffs:
            changed = handoff.get("changedFiles")
            if not isinstance(changed, list):
                continue
            for item in changed:
                path = item.get("path") if isinstance(item, dict) else item
                if str(path or "").strip():
                    scope.append(str(path).strip())
        return list(dict.fromkeys(item for item in scope if item))

    @staticmethod
    def _planning_failed_ids(failed_subtasks: list[Any]) -> list[str]:
        return sorted({
            str(getattr(subtask, "id", "")).strip()
            for subtask in failed_subtasks
            if str(getattr(subtask, "id", "")).strip()
        })

    def _planning_mark_skipped_dependents_repaired(
        self,
        execution: dict[str, Any],
        dispatch_result: dict[str, Any],
        commands: list[dict[str, Any]],
    ) -> None:
        for subtask in execution.get("subtasks", []) or []:
            if str(getattr(subtask, "status", "")).strip().lower() != "skipped":
                continue
            subtask.status = "completed"
            subtask.result = self._append_repaired_subtask_result(
                str(getattr(subtask, "result", "") or "Skipped subtask."),
                dispatch_result,
                commands,
            )

    def _planning_recovery_pending_verification_commands(self, handoffs: list[dict[str, Any]]) -> list[str]:
        commands: list[str] = []
        seen: set[str] = set()
        for handoff in handoffs:
            pending = handoff.get("pendingVerification")
            if not isinstance(pending, list):
                continue
            for item in pending:
                command = str(item or "").strip()
                if not command or not self._planning_recovery_command_allowed(command):
                    continue
                key = self._planning_recovery_command_key(command)
                if key in seen:
                    continue
                seen.add(key)
                commands.append(command)
        return commands

    def _planning_failures_are_verification_only(
        self,
        failed_subtasks: list[Any],
        handoffs: list[dict[str, Any]],
    ) -> bool:
        handoff_ids = {
            str(handoff.get("subtaskId") or "").strip()
            for handoff in handoffs
            if isinstance(handoff.get("pendingVerification"), list) and handoff.get("pendingVerification")
        }
        if not handoff_ids:
            return False
        for subtask in failed_subtasks:
            subtask_id = str(getattr(subtask, "id", "")).strip()
            result = str(getattr(subtask, "result", "") or "").casefold()
            if subtask_id not in handoff_ids:
                return False
            if not any(token in result for token in ("verification", "pytest", "test", "check", "compile", "lint")):
                return False
        return True

    def _planning_recovery_workspace(self, workspace_root: Any) -> Path | None:
        if not isinstance(workspace_root, str) or not workspace_root.strip():
            return None
        workspace = Path(workspace_root).resolve()
        if not workspace.exists() or not workspace.is_dir():
            return None
        return workspace

    def _run_planning_recovery_command(
        self,
        *,
        session_id: str,
        task: dict[str, Any],
        workspace: Path,
        command: str,
    ) -> dict[str, Any]:
        shell_name = "powershell" if __import__("os").name == "nt" else "bash"
        execution_command = _powershell_execution_command(command, shell_name)
        command_log = self._store.create_command_log(
            task_id=task["id"],
            command=execution_command,
            cwd=str(workspace),
            shell=shell_name,
        )
        stdout = ""
        stderr = ""
        exit_code: int | None = None
        status = "completed"
        duration_ms = 0
        try:
            stdout, stderr, exit_code, status, duration_ms = run_shell_command(
                shell_name,
                execution_command,
                workspace,
                self._planning_recovery_command_timeout_ms(),
            )
        except Exception as exc:  # noqa: BLE001
            stderr = str(exc)
            status = "failed"
        finally:
            finished_at = self._store.now()
            stdout_path = self._store.write_command_artifact(command_log["id"], "stdout", stdout)
            stderr_path = self._store.write_command_artifact(command_log["id"], "stderr", stderr)
            command_log = self._store.update_command_log(
                command_log["id"],
                status=status,
                exit_code=exit_code,
                stdout_path=stdout_path,
                stderr_path=stderr_path,
                finished_at=finished_at,
            )

        check = {
            "name": "planning_recovery_verification",
            "status": status,
            "command": execution_command,
            "result": {
                "status": status,
                "command": execution_command,
                "commandLog": command_log,
                "stdout": stdout[-4000:],
                "stderr": stderr[-4000:],
                "exitCode": exit_code,
                "durationMs": duration_ms,
                "cwd": str(workspace),
            },
        }
        self._record_task_verification(
            session_id=session_id,
            task=task,
            validation={"checks": [check]},
        )
        return {
            "command": execution_command,
            "status": status,
            "exitCode": exit_code,
            "durationMs": duration_ms,
            "commandLogId": command_log.get("id"),
        }

    def _planning_recovery_command_timeout_ms(self) -> int:
        try:
            config = self._store.get_config({}).get("config", {})
            command_policy = config.get("commandPolicy") if isinstance(config, dict) else {}
            timeout = command_policy.get("commandTimeoutMs") if isinstance(command_policy, dict) else None
            if isinstance(timeout, int) and timeout > 0:
                return max(1000, min(timeout, 1_800_000))
        except Exception:  # noqa: BLE001
            logger.debug("Failed to read planning recovery command timeout", exc_info=True)
        return 300_000

    @staticmethod
    def _planning_recovery_command_allowed(command: str) -> bool:
        normalized = command.strip().casefold()
        if not normalized:
            return False
        if re.search(r"(?:^|[;&|`])\s*(?:rm|del|erase|remove-item|mv|move|copy|cp|curl|wget)\b", normalized):
            return False
        return any(
            token in normalized
            for token in (
                "pytest",
                "py_compile",
                "compileall",
                "node --check",
                "npm test",
                "npm run test",
                "npm run typecheck",
                "tsc",
                "vitest",
                "ruff",
                "mypy",
            )
        )

    @staticmethod
    def _planning_recovery_command_key(command: str) -> str:
        return " ".join(command.strip().split()).casefold()

    @staticmethod
    def _append_recovered_subtask_result(result: str, commands: list[dict[str, Any]]) -> str:
        command_summary = "; ".join(
            f"{item.get('command')} ({item.get('status')})"
            for item in commands[:5]
            if item.get("command")
        )
        suffix = f"Recovered by parent verification: {command_summary}" if command_summary else "Recovered by parent verification."
        if suffix in result:
            return result
        return f"{result}\n{suffix}"

    @staticmethod
    def _append_repaired_subtask_result(
        result: str,
        dispatch_result: dict[str, Any],
        commands: list[dict[str, Any]],
    ) -> str:
        command_summary = "; ".join(
            f"{item.get('command')} ({item.get('status')})"
            for item in commands[:5]
            if isinstance(item, dict) and item.get("command")
        )
        repair_summary = str(dispatch_result.get("summary") or "").strip()
        suffix = "Recovered by repair child task"
        if command_summary:
            suffix = f"{suffix} and verified by: {command_summary}."
        else:
            suffix = f"{suffix}."
        if repair_summary:
            suffix = f"{suffix}\nRepair summary: {repair_summary[:1200]}"
        if suffix in result:
            return result
        return f"{result}\n{suffix}"

    @staticmethod
    def _append_planning_recovery_summary(summary: str, recovery: dict[str, Any]) -> str:
        commands = recovery.get("commands") if isinstance(recovery.get("commands"), list) else []
        command_summary = "; ".join(
            f"{item.get('command')} ({item.get('status')})"
            for item in commands[:5]
            if isinstance(item, dict) and item.get("command")
        )
        line = (
            "Planning recovery: failed verification subtasks were recovered by parent verification"
            + (f" - {command_summary}" if command_summary else "")
            + "."
        )
        if line in summary:
            return summary
        return f"{summary}\n{line}"

    def _execute_with_supervisor(
        self,
        *,
        session_id: str,
        task: dict[str, Any],
        goal: str,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """Supervisor mode: decompose → execute with review → synthesize."""
        span = self._tracer.start_span(
            "supervisor_execute",
            trace_id=task.get("id", ""),
            attributes={"goal": goal[:200]},
        )
        try:
            self._publish(
                session_id=session_id, task=task,
                event_type="task.planning.started",
                payload={"goal": goal, "mode": "supervisor"},
            )
            self._publish_planning_thinking(
                session_id=session_id,
                task=task,
                text="正在用 supervisor 模式拆分任务。",
                phase="planning_started",
                mode="supervisor",
            )

            # Plan approval gate (strict mode)
            plan_context = json.dumps(
                context.get("tool_results", []), ensure_ascii=False,
            )[:2000]
            planning_provider_context = self._planning_provider_context(context)
            self._trace_planning_provider_request(
                task=task,
                provider_context=planning_provider_context,
                operation="supervisor.decomposition",
            )
            plan = self._decomposer.decompose(
                goal=goal,
                context=plan_context,
                provider_context=planning_provider_context,
            )
            self._trace_planning_provider_response(
                task=task,
                plan=plan,
                operation="supervisor.decomposition",
            )
            self._publish(
                session_id=session_id, task=task,
                event_type="task.planning.decomposed",
                payload={
                    "subtaskCount": len(plan.subtasks),
                    "executionOrder": plan.execution_order,
                    "mode": "supervisor",
                },
            )
            self._publish_planning_thinking(
                session_id=session_id,
                task=task,
                text=f"已拆分 {len(plan.subtasks)} 个 supervisor 子任务，准备派发执行。",
                phase="planning_decomposed",
                mode="supervisor",
                payload={"subtaskCount": len(plan.subtasks), "executionOrder": plan.execution_order},
            )
            # --- Decision trace: decomposition (supervisor) ---
            self._publish(
                session_id=session_id, task=task,
                event_type="agent.decision.decomposition",
                payload={
                    "decision": "decomposed",
                    "subtaskCount": len(plan.subtasks),
                    "parallel": True,
                    "mode": "supervisor",
                },
            )
            approval_response = self._check_plan_approval(
                session_id=session_id, task=task, goal=goal, context=context,
                orchestration_mode="supervisor", plan=plan, span=span,
            )
            if approval_response is not None:
                return approval_response

            self._publish_planning_thinking(
                session_id=session_id,
                task=task,
                text="正在派发 supervisor 子任务并进行结果审查。",
                phase="subtasks_started",
                mode="supervisor",
                payload={"subtaskCount": len(plan.subtasks)},
            )
            result = self._supervisor.execute(
                goal, {**context, "_provider_context": self._planning_provider_context(context)},
                session_id=session_id, task=task,
                child_timeout_ms=self._child_subtask_timeout_ms(context),
                is_paused_fn=lambda: self._store.get_task({"taskId": task["id"]})["task"]["status"] == "paused",
                plan=plan,
            )
            self._publish_planning_thinking(
                session_id=session_id,
                task=task,
                text="子任务审查已返回，正在合并 supervisor 结果。",
                phase="synthesis_started",
                mode="supervisor",
                payload={"reviews": result.review_count},
            )

            if result.paused:
                self._tracer.end_span(span.span_id, status="ok", attributes={"paused": True})
                return {"task": self._store.get_task({"taskId": task["id"]})["task"]}

            self._publish(
                session_id=session_id, task=task,
                event_type="task.planning.completed",
                payload={"mode": "supervisor", "reviews": result.review_count},
            )
            self._publish_planning_thinking(
                session_id=session_id,
                task=task,
                text="supervisor 执行已完成，正在生成最终答复。",
                phase="planning_completed",
                mode="supervisor",
                status="completed",
                payload={"reviews": result.review_count},
            )

            self._tracer.end_span(span.span_id, status="ok", attributes={"reviews": result.review_count})
            return {
                "task": self._complete_task(
                    session_id=session_id,
                    task=task,
                    summary=result.summary,
                    context=context,
                ),
            }
        except Exception as exc:  # noqa: BLE001
            logger.error("Supervisor execution failed for task=%s: %s", task["id"], exc, exc_info=True)
            self._tracer.end_span(span.span_id, status="error", attributes={"error": str(exc)})
            return {
                "task": self._fail_task(
                    session_id=session_id,
                    task=task,
                    summary=str(exc),
                    error_code="SUPERVISOR_EXECUTION_FAILED",
                ),
            }

    def _execute_with_swarm(
        self,
        *,
        session_id: str,
        task: dict[str, Any],
        goal: str,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """Swarm mode: decompose → execute with handoff → synthesize."""
        span = self._tracer.start_span(
            "swarm_execute",
            trace_id=task.get("id", ""),
            attributes={"goal": goal[:200]},
        )
        try:
            self._publish(
                session_id=session_id, task=task,
                event_type="task.planning.started",
                payload={"goal": goal, "mode": "swarm"},
            )
            self._publish_planning_thinking(
                session_id=session_id,
                task=task,
                text="正在用 swarm 模式拆分并安排多 agent 协作。",
                phase="planning_started",
                mode="swarm",
            )

            # Plan approval gate (strict mode)
            plan_context = json.dumps(
                context.get("tool_results", []), ensure_ascii=False,
            )[:2000]
            planning_provider_context = self._planning_provider_context(context)
            self._trace_planning_provider_request(
                task=task,
                provider_context=planning_provider_context,
                operation="swarm.decomposition",
            )
            plan = self._decomposer.decompose(
                goal=goal,
                context=plan_context,
                provider_context=planning_provider_context,
            )
            self._trace_planning_provider_response(
                task=task,
                plan=plan,
                operation="swarm.decomposition",
            )
            self._publish(
                session_id=session_id, task=task,
                event_type="task.planning.decomposed",
                payload={
                    "subtaskCount": len(plan.subtasks),
                    "executionOrder": plan.execution_order,
                    "mode": "swarm",
                },
            )
            self._publish_planning_thinking(
                session_id=session_id,
                task=task,
                text=f"已拆分 {len(plan.subtasks)} 个 swarm 子任务，准备派发 agent。",
                phase="planning_decomposed",
                mode="swarm",
                payload={"subtaskCount": len(plan.subtasks), "executionOrder": plan.execution_order},
            )
            # --- Decision trace: decomposition (swarm) ---
            self._publish(
                session_id=session_id, task=task,
                event_type="agent.decision.decomposition",
                payload={
                    "decision": "decomposed",
                    "subtaskCount": len(plan.subtasks),
                    "parallel": True,
                    "mode": "swarm",
                },
            )
            approval_response = self._check_plan_approval(
                session_id=session_id, task=task, goal=goal, context=context,
                orchestration_mode="swarm", plan=plan, span=span,
            )
            if approval_response is not None:
                return approval_response

            self._publish_planning_thinking(
                session_id=session_id,
                task=task,
                text="正在派发 swarm 子任务并跟踪 agent 交接。",
                phase="subtasks_started",
                mode="swarm",
                payload={"subtaskCount": len(plan.subtasks)},
            )
            result = self._swarm.execute(
                goal, {**context, "_provider_context": self._planning_provider_context(context)},
                session_id=session_id, task=task,
                child_timeout_ms=self._child_subtask_timeout_ms(context),
                is_paused_fn=lambda: self._store.get_task({"taskId": task["id"]})["task"]["status"] == "paused",
                plan=plan,
            )
            self._publish_planning_thinking(
                session_id=session_id,
                task=task,
                text="多 agent 执行结果已返回，正在汇总交接和最终结果。",
                phase="synthesis_started",
                mode="swarm",
                payload={"handoffs": result.handoff_count},
            )

            if result.paused:
                self._tracer.end_span(span.span_id, status="ok", attributes={"paused": True})
                return {"task": self._store.get_task({"taskId": task["id"]})["task"]}

            self._publish(
                session_id=session_id, task=task,
                event_type="task.planning.completed",
                payload={"mode": "swarm", "handoffs": result.handoff_count},
            )
            self._publish_planning_thinking(
                session_id=session_id,
                task=task,
                text="swarm 执行已完成，正在生成最终答复。",
                phase="planning_completed",
                mode="swarm",
                status="completed",
                payload={"handoffs": result.handoff_count},
            )

            self._tracer.end_span(span.span_id, status="ok", attributes={"handoffs": result.handoff_count})
            return {
                "task": self._complete_task(
                    session_id=session_id,
                    task=task,
                    summary=result.summary,
                    context=context,
                ),
            }
        except Exception as exc:  # noqa: BLE001
            logger.error("Swarm execution failed for task=%s: %s", task["id"], exc, exc_info=True)
            self._tracer.end_span(span.span_id, status="error", attributes={"error": str(exc)})
            return {
                "task": self._fail_task(
                    session_id=session_id,
                    task=task,
                    summary=str(exc),
                    error_code="SWARM_EXECUTION_FAILED",
                ),
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
                minimal_context = isinstance(routing, dict) and routing.get("contextMode") == "minimal"
                worker._publish(
                    session_id=session_id,
                    task=task,
                    event_type="context.build.started",
                    payload={
                        "status": "running",
                        "lightweight": minimal_context,
                        "minimal": minimal_context,
                        "background": True,
                    },
                    visibility="trace",
                )
                context = worker._context_builder.build(
                    session_id=session_id, goal=goal, skill_id=skill_id, lightweight=minimal_context,
                    role=task.get("role"), minimal=minimal_context,
                )
                worker._publish(
                    session_id=session_id,
                    task=task,
                    event_type="context.build.completed",
                    payload={
                        "status": "completed",
                        "lightweight": minimal_context,
                        "minimal": minimal_context,
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
                plan = worker._planner.plan(goal, context=context)
                task = worker._store.update_task(task_id=task["id"], plan=plan)
                task = {**task, "plan": plan}
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
                worker._publish(
                    session_id=session_id,
                    task=task,
                    event_type="assistant.token",
                    payload={"delta": "Building context and preparing the first tool calls..."},
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
