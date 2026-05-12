"""Message Routing Mixin — extracted from MessageFlowMixin.

Handles the send_message entry point: routing, task creation,
context building, and background message dispatch.
"""
from __future__ import annotations

import logging
import threading
from copy import deepcopy
from typing import Any

logger = logging.getLogger(__name__)


class MessageRoutingMixin:
    """Mixin providing message routing and background dispatch."""

    def send_message(self, params: dict[str, Any]) -> dict[str, Any]:
        if self._shutting_down:
            raise RuntimeError("服务正在关闭，暂不接受新任务")
        session = self._store.require_session(params["sessionId"])
        goal = params["content"]
        client_message_id = params.get("clientMessageId")

        explicit_supplement = params.get("mode") == "supplement"
        explicit_task_id = params.get("taskId") or params.get("task_id")
        is_queued_mode = params.get("mode") == "queued"
        should_auto_supplement = not is_queued_mode and params.get("background") is not True and params.get("newTask") is not True
        if explicit_supplement or should_auto_supplement:
            active_task = (
                self._find_supplement_target_task(
                    session_id=session["id"],
                    task_id=str(explicit_task_id) if explicit_task_id else None,
                    strict=explicit_supplement,
                )
                if explicit_task_id
                else self._find_open_session_task(session["id"])
            )
            if active_task is not None:
                return self._attach_supplemental_message(session_id=session["id"], task=active_task, content=goal)

        # --- Queued mode: create task but don't execute if another is running ---
        if params.get("mode") == "queued":
            active_task = self._find_open_session_task(session["id"])
            if active_task is not None:
                queued_task = self._store.create_task(
                    session_id=session["id"],
                    task_type="edit",
                    goal=goal,
                    plan=[],
                    status="queued",
                )
                user_msg = self._store.create_message(
                    session_id=session["id"],
                    task_id=queued_task["id"],
                    role="user",
                    content=goal,
                    client_message_id=client_message_id,
                    kind="normal",
                    status="completed",
                )
                self._publish(session["id"], queued_task, "message.created", {"message": user_msg})
                self._publish(session["id"], queued_task, "task.created", {"status": "queued", "goal": goal})
                self._publish(session["id"], queued_task, "task.queued", {"status": "queued", "goal": goal})
                return {"task": queued_task, "userMessage": user_msg}

        # --- Phase 0: MetaRouter scenario classification ---
        import time as _time
        _route_t0 = _time.monotonic()
        routing_span = self._tracer.start_span("routing_decision", attributes={"goal": goal[:200]})
        try:
            routing = self._meta_router.route(goal)
        except Exception:
            self._tracer.end_span(routing_span.span_id, status="error")
            raise
        _route_latency_ms = int((_time.monotonic() - _route_t0) * 1000)
        routing_dict = {
            "scenario": routing.scenario.value,
            "strategy": routing.strategy.value,
            "confidence": routing.confidence,
            "max_steps": routing.max_steps,
            "enable_reflection": routing.enable_reflection,
            "enable_planning": routing.enable_planning,
            "reasoning": routing.reasoning,
            "skill_id": routing.skill_id,
        }
        routing_dict["profile_snapshot"] = self._runtime_profile_snapshot()
        self._tracer.end_span(
            routing_span.span_id,
            status="ok",
            attributes={
                "scenario": routing.scenario.value,
                "strategy": routing.strategy.value,
                "confidence": routing.confidence,
                "skill_id": routing.skill_id,
                "latency_ms": _route_latency_ms,
            },
        )

        if params.get("background") is True:
            # Pass routing info to the background worker without blocking on
            # context build — the worker will build context with routing data.
            plan = self._planner.plan(goal, context={"routing": routing_dict})
            task = self._store.create_task(
                session_id=session["id"],
                task_type="edit",
                goal=goal,
                plan=plan,
                acceptance_criteria=self._default_acceptance_criteria(goal),
                out_of_scope=self._default_out_of_scope(),
                routing=routing_dict,
            )
            runtime_task = {**task, "plan": plan}
            user_msg = self._store.create_message(
                session_id=session["id"],
                task_id=runtime_task["id"],
                role="user",
                content=goal,
                client_message_id=client_message_id,
                kind="normal",
                status="completed",
            )
            assistant_msg = self._store.create_message(
                session_id=session["id"],
                task_id=runtime_task["id"],
                role="assistant",
                content="",
                kind="normal",
                status="streaming",
            )
            self._store.update_task(
                task_id=runtime_task["id"],
                active_assistant_message_id=assistant_msg["id"],
            )
            runtime_task["activeAssistantMessageId"] = assistant_msg["id"]
            self._record_routing_proposal(
                session_id=session["id"],
                task_id=runtime_task["id"],
                goal=goal,
                routing=routing,
                routing_dict=routing_dict,
            )
            self._publish(
                session_id=session["id"],
                task=runtime_task,
                event_type="message.created",
                payload={"message": user_msg},
            )
            self._publish(
                session_id=session["id"],
                task=runtime_task,
                event_type="message.created",
                payload={"message": assistant_msg},
            )
            self._publish(
                session_id=session["id"],
                task=runtime_task,
                event_type="task.created",
                payload={"status": runtime_task["status"], "goal": goal},
            )
            self._publish(
                session_id=session["id"],
                task=runtime_task,
                event_type="task.routing.decided",
                payload={**routing_dict, "latency_ms": _route_latency_ms},
            )
            self._start_background_message(
                session_id=session["id"],
                task=runtime_task,
                goal=goal,
                context=None,
                routing=routing_dict,
                skill_id=routing.skill_id,
            )
            self._record_skill_usage(
                task_id=runtime_task["id"],
                session_id=session["id"],
                skill_id=routing.skill_id,
            )
            return {"task": runtime_task, "userMessage": user_msg, "assistantMessage": assistant_msg}

        context = self._context_builder.build(session_id=session["id"], goal=goal, skill_id=routing.skill_id, lightweight=False)
        routing_dict["profile_snapshot"] = self._runtime_profile_snapshot(context)
        # Inject routing decision into context as a plain dict for JSON safety.
        context["routing"] = routing_dict
        # Emit tool filter event if skill filtering was applied
        self._maybe_publish_tool_filter(context, routing.skill_id)
        logger.info(
            "Routing decision: scenario=%s strategy=%s confidence=%.2f max_steps=%d skill=%s",
            routing.scenario.value, routing.strategy.value,
            routing.confidence, routing.max_steps, routing.skill_id,
        )

        plan = self._planner.plan(goal, context=context)
        task = self._store.create_task(
            session_id=session["id"],
            task_type="edit",
            goal=goal,
            plan=plan,
            acceptance_criteria=self._default_acceptance_criteria(goal),
            out_of_scope=self._default_out_of_scope(),
            routing=routing_dict,
        )
        runtime_task = {**task, "plan": plan}
        user_msg = self._store.create_message(
            session_id=session["id"],
            task_id=runtime_task["id"],
            role="user",
            content=goal,
            client_message_id=client_message_id,
            kind="normal",
            status="completed",
        )
        assistant_msg = self._store.create_message(
            session_id=session["id"],
            task_id=runtime_task["id"],
            role="assistant",
            content="",
            kind="normal",
            status="streaming",
        )
        self._store.update_task(
            task_id=runtime_task["id"],
            active_assistant_message_id=assistant_msg["id"],
        )
        runtime_task["activeAssistantMessageId"] = assistant_msg["id"]
        context = self._context_with_task_focus(context, runtime_task)

        self._record_routing_proposal(
            session_id=session["id"],
            task_id=runtime_task["id"],
            goal=goal,
            routing=routing,
            routing_dict=routing_dict,
        )

        self._record_skill_usage(
            task_id=runtime_task["id"],
            session_id=session["id"],
            skill_id=routing.skill_id,
        )

        self._publish(
            session_id=session["id"],
            task=runtime_task,
            event_type="message.created",
            payload={"message": user_msg},
        )
        self._publish(
            session_id=session["id"],
            task=runtime_task,
            event_type="message.created",
            payload={"message": assistant_msg},
        )
        self._publish(
            session_id=session["id"],
            task=runtime_task,
            event_type="task.created",
            payload={"status": runtime_task["status"], "goal": goal},
        )
        self._publish(
            session_id=session["id"],
            task=runtime_task,
            event_type="task.started",
            payload={
                "status": runtime_task["status"],
                "plan": runtime_task["plan"],
                "currentStep": runtime_task.get("currentStep"),
                "context": self._event_context_summary(context),
                "routing": context["routing"],
            },
        )
        self._fire_hooks("before_task_start", session["id"], runtime_task, extra_context={"routing": context.get("routing")})
        self._publish(
            session_id=session["id"],
            task=runtime_task,
            event_type="task.routing.decided",
            payload={**routing_dict, "latency_ms": _route_latency_ms},
        )
        self._publish(
            session_id=session["id"],
            task=runtime_task,
            event_type="assistant.token",
            payload={"delta": "Building context and preparing the first tool calls..."},
        )

        return self._execute_message_task(
            session_id=session["id"],
            task=runtime_task,
            goal=goal,
            context=context,
        )

    def _start_background_message(
        self,
        *,
        session_id: str,
        task: dict[str, Any],
        goal: str,
        context: dict[str, Any] | None,
        routing: dict[str, Any] | None = None,
        skill_id: str | None = None,
    ) -> None:
        worker = threading.Thread(
            target=self._run_background_message,
            kwargs={
                "session_id": session_id,
                "task": deepcopy(task),
                "goal": goal,
                "context": deepcopy(context) if context is not None else None,
                "routing": deepcopy(routing) if routing is not None else None,
                "skill_id": skill_id,
            },
            name=f"message-task-{task['id']}",
            daemon=True,
        )
        worker.start()
