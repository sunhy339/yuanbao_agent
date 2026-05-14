"""Message Execution Mixin — extracted from MessageFlowMixin.

Handles execution strategies: standard ReAct, fast ReAct, planning/DAG,
supervisor, swarm, and background worker management.
"""
from __future__ import annotations

import json
import logging
from copy import deepcopy
from typing import Any

logger = logging.getLogger(__name__)

from ..policy.guard import PolicyGuard
from ..provider.adapter import ProviderAdapter
from ..services.collaboration_service import CollaborationService
from ..services.subagent_service import SubagentService
from ..context.scratchpad import Scratchpad
from ..tools import build_builtin_tools
from ..tools.registry import ToolRegistry
from ..orchestration import OrchestrationMode
from ..store.sqlite_store import SQLiteStore


class MessageExecutionMixin:
    """Mixin providing execution strategies and background worker management."""

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
            return {
                "task": self._fail_task(
                    session_id=session_id,
                    task=task,
                    summary=str(exc),
                    error_code="LOOP_EXECUTION_FAILED",
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
            summary = react_result.get("summary") or self._provider.summarize_findings(
                goal=goal,
                context=context,
                tool_results=react_result.get("tool_results", []),
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
                    tool_results=react_result.get("tool_results", []),
                    skip_reflection=True,
                )
            }
        except Exception as exc:  # noqa: BLE001
            logger.error("Fast react loop failed for task=%s: %s", task["id"], exc, exc_info=True)
            return {
                "task": self._fail_task(
                    session_id=session_id,
                    task=task,
                    summary=str(exc),
                    error_code="FAST_LOOP_FAILED",
                )
            }

    def _execute_with_planning(
        self,
        *,
        session_id: str,
        task: dict[str, Any],
        goal: str,
        context: dict[str, Any],
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

            # 1. Decompose
            plan_context = json.dumps(
                context.get("tool_results", []), ensure_ascii=False,
            )[:2000]
            decomp_span = self._tracer.start_span(
                "plan_decomposition",
                trace_id=getattr(self, "_active_trace_id", None),
                attributes={"goal": goal[:200]},
            )
            plan = self._decomposer.decompose(goal=goal, context=plan_context)
            self._tracer.end_span(
                decomp_span.span_id, status="ok",
                attributes={"subtask_count": len(plan.subtasks), "execution_order": plan.execution_order},
            )

            self._publish(
                session_id=session_id, task=task,
                event_type="task.planning.decomposed",
                payload={
                    "subtaskCount": len(plan.subtasks),
                    "executionOrder": plan.execution_order,
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
                },
            )

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
                plan_data = {
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
                event_type = f"task.planning.subtask.{event}"
                self._publish(session_id=session_id, task=task, event_type=event_type, payload=details)

            execution = self._dag_executor.execute(
                plan,
                session_id=session_id,
                parent_task_id=task["id"],
                max_workers=self._max_parallel_subtasks(context),
                is_paused_fn=lambda: self._store.get_task({"taskId": task["id"]})["task"]["status"] == "paused",
                tracer=self._tracer,
                on_subtask_callback=_on_subtask_event,
                scope_checker=lambda subtasks: self._store.check_dispatch_scope(
                    {"subtasks": subtasks, "taskId": task["id"], "sessionId": session_id}
                ).get("overlaps", []),
            )

            # Handle DAG cooperative pause
            if execution.get("paused"):
                plan_data = {
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
                        session_id=session_id,
                        goal=goal,
                        context=context,
                        plan_json=json.dumps(plan_data, ensure_ascii=False),
                        completed_ids=execution["completed"],
                        failed_ids=execution["failed"],
                        results=execution.get("results", {}),
                    )
                return {"status": "paused"}

            # 3. Coverage evaluation
            coverage = self._coverage_evaluator.evaluate(
                goal, execution["subtasks"],
            )

            # 4. Auto-supplement if coverage is insufficient
            routing = context.get("routing", {})
            threshold = routing.get("coverage_threshold", 0.7)
            if coverage < threshold:
                gaps = self._coverage_evaluator.find_gaps(goal, execution["subtasks"])
                if gaps:
                    from ..planner.types import Subtask as PlanSubtask
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
                    )
                    try:
                        dispatch_result = self._subagent_service.dispatch({
                            "prompt": supplement.description,
                            "title": supplement.title,
                            "sessionId": session_id,
                            "taskId": task["id"],
                            "agentType": "planner",
                        })
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

            self._publish(
                session_id=session_id, task=task,
                event_type="task.planning.completed",
                payload={
                    "coverage": coverage,
                    "success": execution["success"],
                },
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

    def _resolve_orchestration_mode(self, strategy: str) -> OrchestrationMode:
        """Map an ExecutionStrategy string to an OrchestrationMode."""
        if strategy == "plan_supervise":
            return OrchestrationMode.SUPERVISOR
        if strategy == "plan_swarm":
            return OrchestrationMode.SWARM
        return OrchestrationMode.DAG

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
        plan_data = {
            "subtasks": [
                {"id": s.id, "title": s.title, "description": s.description,
                 "dependencies": s.dependencies, "status": s.status, "result": s.result}
                for s in plan.subtasks
            ],
            "dag": plan.dag,
            "execution_order": plan.execution_order,
        }
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

            # Plan approval gate (strict mode)
            plan_context = json.dumps(
                context.get("tool_results", []), ensure_ascii=False,
            )[:2000]
            plan = self._decomposer.decompose(goal=goal, context=plan_context)
            self._publish(
                session_id=session_id, task=task,
                event_type="task.planning.decomposed",
                payload={
                    "subtaskCount": len(plan.subtasks),
                    "executionOrder": plan.execution_order,
                    "mode": "supervisor",
                },
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

            result = self._supervisor.execute(
                goal, context,
                session_id=session_id, task=task,
                is_paused_fn=lambda: self._store.get_task({"taskId": task["id"]})["task"]["status"] == "paused",
            )

            if result.paused:
                self._tracer.end_span(span.span_id, status="ok", attributes={"paused": True})
                return {"task": self._store.get_task({"taskId": task["id"]})["task"]}

            self._publish(
                session_id=session_id, task=task,
                event_type="task.planning.completed",
                payload={"mode": "supervisor", "reviews": result.review_count},
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

            # Plan approval gate (strict mode)
            plan_context = json.dumps(
                context.get("tool_results", []), ensure_ascii=False,
            )[:2000]
            plan = self._decomposer.decompose(goal=goal, context=plan_context)
            self._publish(
                session_id=session_id, task=task,
                event_type="task.planning.decomposed",
                payload={
                    "subtaskCount": len(plan.subtasks),
                    "executionOrder": plan.execution_order,
                    "mode": "swarm",
                },
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

            result = self._swarm.execute(
                goal, context,
                session_id=session_id, task=task,
                is_paused_fn=lambda: self._store.get_task({"taskId": task["id"]})["task"]["status"] == "paused",
            )

            if result.paused:
                self._tracer.end_span(span.span_id, status="ok", attributes={"paused": True})
                return {"task": self._store.get_task({"taskId": task["id"]})["task"]}

            self._publish(
                session_id=session_id, task=task,
                event_type="task.planning.completed",
                payload={"mode": "swarm", "handoffs": result.handoff_count},
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
        worker = self
        try:
            logger.info(
                "Background message execution started for task=%s session=%s routing=%s",
                task["id"], session_id, routing,
            )
            worker, background_store = self._background_worker_orchestrator()
            if context is None:
                context = worker._context_builder.build(
                    session_id=session_id, goal=goal, skill_id=skill_id, lightweight=False,
                    role=task.get("role"),
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
                    event_type="task.started",
                    payload={
                        "status": task["status"],
                        "plan": task["plan"],
                        "currentStep": task.get("currentStep"),
                        "context": worker._event_context_summary(context),
                    },
                )
                worker._publish(
                    session_id=session_id,
                    task=task,
                    event_type="assistant.token",
                    payload={"delta": "Building context and preparing the first tool calls..."},
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
                    event_type="task.started",
                    payload={
                        "status": task["status"],
                        "plan": task["plan"],
                        "currentStep": task.get("currentStep"),
                        "context": worker._event_context_summary(context),
                    },
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
            if background_store is not None:
                background_store.close()

    def _background_worker_orchestrator(self) -> tuple["Orchestrator", SQLiteStore | None]:
        database_path = str(getattr(self._store, "database_path", ":memory:"))
        if database_path == ":memory:":
            return self, None

        from .service import Orchestrator  # lazy to avoid circular import

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
        tool_registry = ToolRegistry(
            build_builtin_tools(
                policy_guard=policy_guard,
                store=store,
                subagent_service=subagent_service,
                memory_manager=bg_memory_manager,
                scratchpad=bg_scratchpad,
            )
        )
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
        )
