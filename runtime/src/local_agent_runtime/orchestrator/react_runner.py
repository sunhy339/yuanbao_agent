"""React Runner Mixin — core ReAct loop orchestration.

Composes ProviderTurnMixin, ReactToolHelpersMixin, and ReactResumeMixin.
This module retains the main loop, context/budget helpers, minimal loop,
and plan advancement logic.
"""
from __future__ import annotations

import logging
from copy import deepcopy
from pathlib import Path
from typing import Any

from ..context.token_budget import estimate_tokens
from ..provider.failure_recovery import classify_provider_failure
from ..policy.tool_policy_resolver import ToolPolicyDecision, ToolPolicyResolver
from ..react.types import TurnDecision
from ..services.worker_budget import WorkerBudget, WorkerBudgetExceededError

logger = logging.getLogger(__name__)


class ReactRunnerMixin:
    def _run_react_loop(
        self,
        session_id: str,
        task: dict[str, Any],
        goal: str,
        context: dict[str, Any],
        state: dict[str, Any] | None = None,
        budget: WorkerBudget | None = None,
    ) -> dict[str, Any]:
        if not hasattr(self._provider, "generate"):
            if self._has_deterministic_fallback():
                return {"status": "fallback"}
            raise RuntimeError("Provider does not implement generate().")

        max_steps = self._max_task_steps(context)
        messages = deepcopy(state["messages"]) if state else self._initial_react_messages(context, goal)
        tool_results = deepcopy(state["tool_results"]) if state else []
        steps = int(state.get("steps", 0)) if state else 0
        react_started = bool(state.get("react_started", False)) if state else False
        patch_repair_attempts = int(state.get("patch_repair_attempts", 0)) if state else 0

        if budget is not None and budget.tool_calls.exhausted:
            raise WorkerBudgetExceededError(
                dimension="tool_calls",
                limit=budget.tool_calls.limit or 0,
                attempted=budget.tool_calls.consumed + 1,
                consumed=budget.tool_calls.consumed,
            )

        root_span = self._tracer.start_span(
            "react_loop",
            attributes={"taskId": task["id"], "goal": goal[:200]},
        )
        self._active_trace_id = root_span.trace_id
        self._active_parent_span_id = root_span.span_id

        try:
            return self._run_react_loop_inner(
                session_id, task, goal, context, state, budget,
                messages, tool_results, steps, max_steps, react_started, patch_repair_attempts,
            )
        except Exception:
            self._tracer.end_span(root_span.span_id, status="error")
            raise
        finally:
            if root_span.status != "error":
                self._tracer.end_span(root_span.span_id, status="ok")
            self._active_trace_id = None
            self._active_parent_span_id = None

    def _run_react_loop_inner(
        self,
        session_id: str,
        task: dict[str, Any],
        goal: str,
        context: dict[str, Any],
        state: dict[str, Any] | None,
        budget: WorkerBudget | None,
        messages: list[dict[str, Any]],
        tool_results: list[dict[str, Any]],
        steps: int,
        max_steps: int,
        react_started: bool,
        patch_repair_attempts: int,
    ) -> dict[str, Any]:

        # --- Context policy advisory: consult DecisionAdvisor for compaction threshold ---
        ctx_threshold = self._compaction_threshold(context)
        ctx_policy_advice = self._consult_context_policy_advisor(task, goal, ctx_threshold, context)
        if ctx_policy_advice is not None:
            advised_threshold = ctx_policy_advice.get("compaction_threshold")
            if isinstance(advised_threshold, int) and advised_threshold > 0:
                ctx_threshold = advised_threshold
                # Store the advisor-provided threshold in context so subsequent turns use it
                context = dict(context)
                context["_advised_compaction_threshold"] = ctx_threshold
        # --- End context policy advisory ---

        # Cache provider tools list — it does not change within the loop
        cached_provider_tools: list[dict[str, Any]] | None = None
        read_file_cache: dict[str, dict[str, Any]] = {}
        # Incremental token tracking — avoids re-estimating all messages every turn
        _msg_token_total: int = sum(estimate_tokens(m.get("content", "")) for m in messages)
        _msg_count_at_last_check: int = len(messages)
        # Per-step tracking for ProviderTurn / ContextSnapshot
        _step_supplement_ids: list[str] = []
        _step_memory_ids: list[str] = []

        while True:
            _step_supplement_ids = []
            _step_memory_ids = []
            # Refresh task status to detect external pause
            task = self._store.get_task({"taskId": task["id"]})["task"]
            if task["status"] == "cancelled":
                return {"status": "cancelled", "summary": "Task was cancelled.", "tool_results": tool_results}
            if task["status"] == "paused":
                self._pending_react_tasks[task["id"]] = {
                    "session_id": session_id,
                    "goal": goal,
                    "context": context,
                    "messages": messages,
                    "tool_results": tool_results,
                    "steps": steps,
                    "react_started": react_started,
                    "patch_repair_attempts": patch_repair_attempts,
                    "pending_tool_call": None,
                    "pending_tool_spec": None,
                    "remaining_tool_calls": [],
                }
                self._save_pending_react_state(task["id"], self._pending_react_tasks[task["id"]])
                return {"status": "paused"}

            if steps >= max_steps:
                return self._converge_after_step_budget_exhausted(
                    session_id=session_id,
                    task=task,
                    goal=goal,
                    context=context,
                    messages=messages,
                    tool_results=tool_results,
                    steps=steps,
                    max_steps=max_steps,
                    react_started=react_started,
                    patch_repair_attempts=patch_repair_attempts,
                )

            # Drain pending supplements from task inbox
            pending_supplements = self._store.get_pending_supplements(task["id"])
            if pending_supplements:
                supplement_lines = []
                for entry in pending_supplements:
                    supplement_lines.append(self._supplement_prompt_line(entry, context))
                    self._store.mark_supplement_consumed(
                        entry["id"],
                        consumed_by_turn_id=f"step_{steps}",
                    )
                    _step_supplement_ids.append(entry["id"])
                supplement_text = "[User supplement]\n" + "\n".join(supplement_lines)
                messages.append({"role": "user", "content": supplement_text})
                # Generate memory candidates from supplements
                self._remember_supplement_candidates(
                    session_id=session_id,
                    task=task,
                    supplements=pending_supplements,
                )
                self._publish(
                    session_id=session_id,
                    task=task,
                    event_type="task.supplement.consumed",
                    payload={
                        "count": len(pending_supplements),
                        "entryIds": [e["id"] for e in pending_supplements],
                        "step": steps,
                    },
                )

            # Inject recalled memories into context on first step
            if steps == 0 and self._memory_manager is not None:
                workspace_id = context.get("workspace_id")
                mem_span = self._tracer.start_span(
                    "memory_recall",
                    trace_id=getattr(self, "_active_trace_id", None),
                    parent_span_id=getattr(self, "_active_parent_span_id", None),
                    attributes={"query": goal[:200]},
                )
                try:
                    scored_recall = self._memory_manager.recall_with_scores(
                        workspace_id=workspace_id,
                        session_id=session_id,
                        query=goal,
                        limit=5,
                    )
                    recalled = [e for e, _s in scored_recall]
                    _step_memory_ids = [e.id for e in recalled]
                    if recalled:
                        # Group by category for structured injection
                        groups: dict[str, list[str]] = {}
                        for e in recalled:
                            cat = (e.metadata.get("category") or "other").replace("_", " ").title()
                            groups.setdefault(cat, []).append(f"  - {e.content[:200]}")
                        parts = ["[Relevant memories]"]
                        for cat_name, items in groups.items():
                            parts.append(f"{cat_name}:")
                            parts.extend(items)
                        mem_hint = "\n".join(parts)
                        messages.append({"role": "system", "content": mem_hint})

                        # Record recall for traceability
                        try:
                            memory_ids = [e.id for e in recalled]
                            scores = {e.id: round(s, 3) for e, s in scored_recall}
                            self._memory_manager._store.record_recall(
                                session_id=session_id,
                                task_id=task.get("id"),
                                query=goal[:500],
                                memory_ids=memory_ids,
                                scores=scores,
                                injected=True,
                            )
                        except Exception:  # noqa: BLE001
                            logger.debug("Failed to record memory recall", exc_info=True)

                    self._tracer.end_span(mem_span.span_id, status="ok", attributes={"count": len(recalled) if recalled else 0})
                except Exception:  # noqa: BLE001
                    self._tracer.end_span(mem_span.span_id, status="error")

            if cached_provider_tools is None:
                cached_provider_tools = self._provider_tools(context)
            tool_policy_decision = self._tool_policy_decision_for_turn(
                task=task,
                context=context,
                tool_results=tool_results,
                cached_provider_tools=cached_provider_tools,
            )
            untrusted_content_signals = self._tool_policy_resolver.untrusted_content_signals(
                context=context,
                tool_results=tool_results,
            )
            provider_tools = tool_policy_decision.allowed_tools
            provider_context = {
                **context,
                "messages": messages,
                "tools": provider_tools,
                "openai_tools": provider_tools,
                "tool_results": tool_results,
                "tool_policy_decision": tool_policy_decision.to_dict(),
                "untrustedContentSignals": untrusted_content_signals,
                "role_snapshot": tool_policy_decision.role_snapshot,
                "step": steps + 1,
                "max_steps": max_steps,
            }
            preflight_result = self._provider_preflight_decision(
                session_id=session_id,
                task=task,
                goal=goal,
                provider_context=provider_context,
                token_estimate=_msg_token_total,
                compaction_threshold=context.get("_advised_compaction_threshold") or ctx_threshold,
            )
            provider_context = preflight_result["provider_context"]
            context = provider_context
            preflight_messages = preflight_result.get("messages")
            if isinstance(preflight_messages, list):
                messages = preflight_messages
                context = dict(context)
                context["messages"] = messages
                _msg_count_at_last_check = len(messages)
            _msg_token_total = int(preflight_result.get("token_estimate") or _msg_token_total)
            # --- ProviderTurn: create before provider call ---
            provider_turn = self._store.create_provider_turn(
                task_id=task["id"],
                session_id=session_id,
                turn_index=steps,
                model=context.get("config", {}).get("provider", {}).get("model"),
                request_message_count=len(messages),
                request_tool_count=len(provider_tools),
                request_token_estimate=_msg_token_total,
                tool_policy_decision=tool_policy_decision.to_dict(),
                role_snapshot=tool_policy_decision.role_snapshot,
            )
            provider_context["_provider_turn_id"] = provider_turn["id"]
            self._record_provider_preflight_decision(
                session_id=session_id,
                task=task,
                provider_context=provider_context,
                provider_turn_id=provider_turn["id"],
                decision=preflight_result.get("decision"),
            )
            if provider_context.get("_provider_preflight_runtime_action") == "execute_split":
                self._store.complete_provider_turn(
                    turn_id=provider_turn["id"],
                    finish_reason="provider_preflight_split",
                    usage={},
                    tool_call_count=0,
                    turn_decision="continue",
                    thought_summary="Provider preflight recommended bounded task splitting before the model call.",
                    response_transport="provider_preflight_split",
                )
                return {
                    "status": "provider_preflight_split",
                    "summary": "Provider preflight recommended bounded task splitting before the model call.",
                    "preflight_split_plan": provider_context.get("_provider_preflight_split_plan"),
                    "preflight": provider_context.get("_provider_preflight"),
                    "tool_results": tool_results,
                }
            self._fire_hooks("before_provider_turn", session_id, task, extra_context={"turnIndex": steps, "providerTurnId": provider_turn["id"]})
            # --- ContextSnapshot: capture what the model will see ---
            snapshot_meta = (context.get("_build_result") or context).get("snapshot_metadata", {}) or {}
            active_worktree = context.get("active_worktree")
            if not isinstance(active_worktree, dict):
                routing_for_snapshot = context.get("routing") if isinstance(context.get("routing"), dict) else {}
                active_worktree = routing_for_snapshot.get("activeWorktree") if isinstance(routing_for_snapshot, dict) else None
            snapshot = self._store.create_context_snapshot(
                session_id=session_id,
                task_id=task["id"],
                provider_turn_id=provider_turn["id"],
                included_sections=snapshot_meta.get("included_sections"),
                trimmed_sections=snapshot_meta.get("trimmed_sections"),
                dropped_sections=snapshot_meta.get("dropped_sections"),
                recent_message_ids=[m.get("id") for m in messages if m.get("id")],
                memory_ids=_step_memory_ids or None,
                supplement_inbox_ids=_step_supplement_ids or None,
                tool_count=len(provider_tools),
                skill_id=context.get("routing", {}).get("skill_id") or snapshot_meta.get("skill_id"),
                token_estimate=_msg_token_total,
                max_context_tokens=context.get("budgetStats", {}).get("maxContextTokens"),
                prompt_layers=snapshot_meta.get("prompt_layers"),
                tool_policy_decision={
                    **tool_policy_decision.to_dict(),
                    **({"untrustedContentSignals": untrusted_content_signals} if untrusted_content_signals else {}),
                },
                role_snapshot=tool_policy_decision.role_snapshot,
                active_worktree=active_worktree if isinstance(active_worktree, dict) else None,
            )
            self._fire_hooks("on_context_snapshot", session_id, task, extra_context={"snapshotId": snapshot.get("id"), "tokenEstimate": _msg_token_total})
            try:
                response = self._request_provider_response(
                    session_id=session_id,
                    task=task,
                    goal=goal,
                    provider_context=provider_context,
                    budget=budget,
                )
            except Exception as exc:
                if self._task_is_cancelled(task):
                    return {"status": "cancelled", "summary": "Task was cancelled.", "tool_results": tool_results}
                recorded_recovery = provider_context.get("_provider_failure_recovery_payload")
                failure_recovery = (
                    recorded_recovery
                    if isinstance(recorded_recovery, dict)
                    else classify_provider_failure(exc).to_dict()
                )
                self._store.fail_provider_turn(
                    turn_id=provider_turn["id"],
                    error_summary=str(exc)[:500],
                    failure_recovery=failure_recovery,
                )
                self._publish(
                    session_id=session_id,
                    task=task,
                    event_type="agent.decision.failure_recovery",
                    payload={
                        "providerTurnId": provider_turn["id"],
                        "failureRecovery": failure_recovery,
                        "error": str(exc)[:500],
                    },
                    visibility="panel",
                )
                if not provider_context.get("_provider_failure_recovery_recorded"):
                    self._record_failure_recovery_proposal(
                        session_id=session_id,
                        task=task,
                        provider_turn_id=provider_turn["id"],
                        failure_recovery=failure_recovery,
                        error=str(exc),
                    )
                raise
            task = self._store.get_task({"taskId": task["id"]})["task"]
            if task["status"] == "cancelled":
                return {"status": "cancelled", "summary": "Task was cancelled.", "tool_results": tool_results}
            parsed = self._parse_provider_response(
                response,
                allow_fallback=not react_started and steps == 0,
                allow_plain_message_final=react_started,
            )
            # --- Structured turn result for decision tracing ---
            turn_result = self._parse_turn_result(
                response,
                allow_fallback=not react_started and steps == 0,
                allow_plain_message_final=react_started,
            )
            # --- ProviderTurn: mark completed with turn decision ---
            raw = response.get("raw") if isinstance(response.get("raw"), dict) else {}
            raw_usage = raw.get("usage") if isinstance(raw.get("usage"), dict) else response.get("usage") or {}
            self._store.complete_provider_turn(
                turn_id=provider_turn["id"],
                finish_reason=response.get("finish_reason"),
                usage=raw_usage,
                tool_call_count=len(parsed.get("tool_calls") or []),
                snapshot_id=snapshot["id"],
                turn_decision=turn_result.decision.value,
                thought_summary=turn_result.thought_summary[:500] if turn_result.thought_summary else None,
                failure_recovery=provider_context.get("_provider_failure_recovery_payload"),
                response_transport=str(response.get("_response_transport") or "non_stream"),
            )
            self._fire_hooks("after_provider_turn", session_id, task, extra_context={"providerTurnId": provider_turn["id"], "turnDecision": turn_result.decision.value, "step": steps})
            # --- Publish agent.decision.react_turn event ---
            self._publish(
                session_id=session_id,
                task=task,
                event_type="agent.decision.react_turn",
                payload={
                    "step": steps,
                    "decision": turn_result.decision.value,
                    "thought_summary": turn_result.thought_summary[:500],
                    "tool_count": len(parsed.get("tool_calls") or []),
                    "turn_id": provider_turn["id"],
                },
            )
            explicit_thought_summary = (
                response.get("thought_summary")
                if isinstance(response.get("thought_summary"), str)
                else response.get("thoughtSummary")
                if isinstance(response.get("thoughtSummary"), str)
                else None
            )
            if explicit_thought_summary and response.get("_response_transport") != "stream":
                self._publish(
                    session_id=session_id,
                    task=task,
                    event_type="thinking",
                    payload={
                        "text": explicit_thought_summary,
                        "messageId": task.get("activeAssistantMessageId"),
                        "source": "non_stream_thought_summary",
                        "step": steps + 1,
                    },
                )
            if parsed["status"] == "fallback":
                return parsed

            react_started = True
            steps += 1
            task = self._record_main_workflow_budget_progress(
                session_id=session_id,
                task=task,
                context=context,
                goal=goal,
                tool_results=tool_results,
                steps=steps,
                max_steps=max_steps,
                terminal_success=parsed["status"] == "completed",
            )
            assistant_text = parsed.get("message") or ""
            if parsed["status"] == "completed" and not assistant_text:
                assistant_text = parsed["summary"]

            if turn_result.decision == TurnDecision.ASK_USER:
                return self._pause_react_for_user_question(
                    session_id=session_id,
                    task=task,
                    goal=goal,
                    context=context,
                    messages=messages,
                    tool_results=tool_results,
                    steps=steps,
                    react_started=react_started,
                    patch_repair_attempts=patch_repair_attempts,
                    question=assistant_text or "请补充下一步需要我遵循的信息后继续。",
                    summary=turn_result.thought_summary,
                    reason=turn_result.why_complete or "provider_requested_user_input",
                    options=(
                        turn_result.policy_needs.get("options")
                        if isinstance(turn_result.policy_needs, dict)
                        else None
                    ),
                    source="react_turn",
                )

            streamed_or_published = bool(response.get("_streamed_content"))
            if assistant_text and not response.get("_streamed_content"):
                self._publish(
                    session_id=session_id,
                    task=task,
                    event_type="assistant.token",
                    payload={"delta": assistant_text, "step": steps},
                )
                streamed_or_published = True

            if parsed["status"] == "completed":
                return {
                    "status": "completed",
                    "summary": parsed["summary"],
                    "tool_results": tool_results,
                    "assistant_output_published": streamed_or_published,
                }

            tool_calls = self._annotate_tool_call_batch_with_history(parsed["tool_calls"], tool_results)
            messages.append(
                {
                    "role": "assistant",
                    "content": assistant_text,
                    "tool_calls": tool_calls,
                }
            )
            for index, tool_call in enumerate(tool_calls):
                task = self._store.get_task({"taskId": task["id"]})["task"]
                if task["status"] == "cancelled":
                    return {"status": "cancelled", "summary": "Task was cancelled.", "tool_results": tool_results}
                if tool_results:
                    updated_tool_call = self._annotate_tool_call_with_completed_results(tool_call, tool_results)
                    if updated_tool_call is not tool_call:
                        self._sync_tool_metadata(updated_tool_call, tool_call)
                tool_spec = self._provider_tool_call_to_spec(tool_call, context)
                cache_key = self._read_file_cache_key(tool_spec)
                cached_tool_result = read_file_cache.get(cache_key) if cache_key else None
                if cached_tool_result is not None:
                    tool_result = self._clone_cached_tool_result(tool_spec, cached_tool_result)
                else:
                    tool_result = self._execute_tool(
                        session_id=session_id,
                        task=task,
                        tool_spec=tool_spec,
                        budget=budget,
                        context=context,
                    )
                    task = self._store.get_task({"taskId": task["id"]})["task"]
                    if task["status"] == "cancelled":
                        return {"status": "cancelled", "summary": "Task was cancelled.", "tool_results": tool_results}
                    if cache_key and not self._tool_failed(tool_spec["name"], tool_result["result"]):
                        read_file_cache[cache_key] = deepcopy(tool_result)
                    elif self._invalidates_read_file_cache(tool_spec["name"]):
                        read_file_cache.clear()
                self._ensure_tool_result_operation(tool_spec, tool_result)
                if task["status"] == "waiting_approval":
                    self._pending_react_tasks[task["id"]] = {
                        "session_id": session_id,
                        "goal": goal,
                        "context": context,
                        "messages": messages,
                        "tool_results": tool_results,
                        "steps": steps,
                        "react_started": True,
                        "patch_repair_attempts": patch_repair_attempts,
                        "pending_tool_call": tool_call,
                        "pending_tool_spec": tool_spec,
                        "remaining_tool_calls": tool_calls[index + 1 :],
                    }
                    self._save_pending_react_state(task["id"], self._pending_react_tasks[task["id"]])
                    return {"status": "waiting_approval"}

                tool_results.append(tool_result)
                messages.append(self._tool_result_message(tool_call, tool_result))
                # Compact messages if token budget is exceeded
                if self._compactor is not None:
                    # Incrementally update token count for new messages only
                    for m in messages[_msg_count_at_last_check:]:
                        _msg_token_total += estimate_tokens(m.get("content", ""))
                    _msg_count_at_last_check = len(messages)
                    threshold = context.get("_advised_compaction_threshold") or self._compaction_threshold(context)
                    compaction_decision = self._compactor.should_compact(messages, threshold)
                    if compaction_decision.should_compact:
                        compacted = self._compactor.compact(
                            session_id=session_id,
                            messages=messages,
                            max_tokens=threshold,
                            task_id=task.get("id"),
                            force=compaction_decision.force,
                        )
                        messages = compacted.kept_messages
                        _msg_token_total = compacted.tokens_after
                        _msg_count_at_last_check = len(messages)
                        # Rolling session summary: persist compaction summary
                        if compacted.summary:
                            try:
                                session_rec = self._store.require_session(session_id)
                                existing = session_rec.get("summary") or ""
                                # Prepend new summary, cap at 4000 chars
                                updated = (compacted.summary + "\n" + existing)[:4000].strip()
                                self._store.update_session_summary(session_id, updated)
                            except Exception:  # noqa: BLE001
                                logger.debug("Failed to update session summary after compaction", exc_info=True)
                    context["messages"] = messages
                # Refresh volatile context sections (git status, directory
                # listing) after state-mutating tools so the model sees the
                # current workspace state on subsequent turns.
                if self._context_builder.should_refresh(tool_spec["name"]):
                    context = self._context_builder.refresh_context(
                        context,
                        tool_name=tool_spec["name"],
                        tool_result=tool_result.get("result"),
                    )
                    # Keep the messages list in sync after refresh.
                    context["messages"] = messages
                self._publish_context_update(
                    session_id=session_id,
                    task=task,
                    context=context,
                    messages=messages,
                )
                if self._is_patch_validation_failure(tool_spec["name"], tool_result["result"]):
                    patch_repair_attempts += 1
                    max_attempts = self._max_patch_repair_attempts(context)
                    if patch_repair_attempts > max_attempts:
                        raise RuntimeError(
                            "Patch repair attempts exhausted "
                            f"({patch_repair_attempts}/{max_attempts}): "
                            f"{self._tool_failure_summary(tool_spec, tool_result['result'])}"
                        )
                    continue
                if not self._tool_failed(tool_spec["name"], tool_result["result"]):
                    self._advance_after_tool(session_id=session_id, task=task, tool_spec=tool_spec)

            # After all tool calls in this step, check for cooperative pause
            task = self._store.get_task({"taskId": task["id"]})["task"]
            if task["status"] == "paused":
                self._pending_react_tasks[task["id"]] = {
                    "session_id": session_id,
                    "goal": goal,
                    "context": context,
                    "messages": messages,
                    "tool_results": tool_results,
                    "steps": steps,
                    "react_started": react_started,
                    "patch_repair_attempts": patch_repair_attempts,
                    "pending_tool_call": None,
                    "pending_tool_spec": None,
                    "remaining_tool_calls": [],
                }
                self._save_pending_react_state(task["id"], self._pending_react_tasks[task["id"]])
                return {"status": "paused"}

    def _converge_after_step_budget_exhausted(
        self,
        *,
        session_id: str,
        task: dict[str, Any],
        goal: str,
        context: dict[str, Any],
        messages: list[dict[str, Any]],
        tool_results: list[dict[str, Any]],
        steps: int,
        max_steps: int,
        react_started: bool,
        patch_repair_attempts: int,
    ) -> dict[str, Any]:
        routing = dict(task.get("routing") or context.get("routing") or {})
        workflow = dict(routing.get("mainWorkflow") or {})
        budget_state = dict(workflow.get("budget") or {})
        step_dimension = self._main_workflow_step_dimension(consumed=steps, limit=max_steps)
        budget_state.update({
            "exhausted": True,
            "exhaustedReason": "max_steps",
            "consumedSteps": steps,
            "maxSteps": max_steps,
            "pressure": "exhausted",
            "convergenceRequired": True,
        })
        dimensions = dict(budget_state.get("dimensions") or {})
        dimensions["steps"] = step_dimension
        budget_state["dimensions"] = dimensions
        workflow["budget"] = budget_state
        advice = self._budget_convergence_decision(
            session_id=session_id,
            task=task,
            goal=goal,
            budget_state=budget_state,
            context=context,
            tool_results=tool_results,
            steps=steps,
            max_steps=max_steps,
            reason="max_steps_exhausted",
        )
        convergence = {
            "state": "partial_result",
            "reason": "max_steps_exhausted",
            "toolResultCount": len(tool_results),
            "resumable": True,
            "requiresUserDecision": True,
            "recommendedAction": self._budget_convergence_runtime_action(advice),
            "availableActions": ["review_partial", "continue_with_more_budget", "change_goal", "stop"],
            "budgetPressure": "exhausted",
            "source": advice.get("source") or "runtime",
        }
        if advice.get("proposalRecordId"):
            convergence["proposalRecordId"] = advice["proposalRecordId"]
        if advice.get("advisorProposalId"):
            convergence["advisorProposalId"] = advice["advisorProposalId"]
        if advice.get("reason"):
            convergence["advisorReason"] = advice["reason"]
        if advice.get("handoffFocus"):
            convergence["handoffFocus"] = advice["handoffFocus"]
        if advice.get("resumePolicy"):
            convergence["resumePolicy"] = advice["resumePolicy"]
        if advice.get("nextUserOptions"):
            convergence["nextUserOptions"] = advice["nextUserOptions"]
        if advice.get("constraints"):
            convergence["constraints"] = advice["constraints"]
        workflow["convergence"] = convergence
        routing["mainWorkflow"] = workflow
        task = self._store.update_task(task_id=task["id"], routing=routing)
        self._publish(
            session_id=session_id,
            task=task,
            event_type="task.budget.exhausted",
            payload={
                "dimension": "max_steps",
                "consumedSteps": steps,
                "maxSteps": max_steps,
                "convergence": workflow["convergence"],
                "budget": budget_state,
            },
        )
        self._publish_budget_progress(
            session_id=session_id,
            task=task,
            phase="budget_exhausted",
            pressure="exhausted",
            consumed_steps=steps,
            max_steps=max_steps,
            remaining_steps=0,
            recommended_action=convergence.get("recommendedAction"),
        )
        summary = self._budget_exhausted_summary(goal=goal, tool_results=tool_results, steps=steps, max_steps=max_steps)
        result_counts = self._tool_results_summary_for_budget(tool_results)
        no_successful_tool_results = result_counts["total"] > 0 and result_counts["completed"] == 0
        if not no_successful_tool_results and convergence.get("recommendedAction") == "review_partial":
            question = str(
                advice.get("userMessage")
                or advice.get("handoffFocus")
                or self._budget_handoff_focus(goal=goal, tool_results=tool_results)
                or "当前任务已达到步骤预算，需要你确认下一步。"
            ).strip()
            resume_context = self._context_with_additional_step_budget(
                {**context, "routing": routing, "messages": messages},
                consumed_steps=steps,
                current_limit=max_steps,
            )
            return self._pause_react_for_user_question(
                session_id=session_id,
                task=task,
                goal=goal,
                context=resume_context,
                messages=messages,
                tool_results=tool_results,
                steps=steps,
                react_started=react_started,
                patch_repair_attempts=patch_repair_attempts,
                question=question,
                summary=str(advice.get("reason") or "任务已达到步骤预算，需要用户决定是否继续。"),
                reason="max_steps_exhausted",
                resume_policy=str(advice.get("resumePolicy") or "requires_user_budget_update"),
                options=advice.get("nextUserOptions") or [
                    {
                        "label": "继续并追加预算",
                        "value": "continue_with_more_budget",
                        "description": "允许当前任务继续执行更多步骤。",
                    },
                    {
                        "label": "调整目标",
                        "value": "change_goal",
                        "description": "补充新的范围或约束后继续。",
                    },
                    {
                        "label": "收尾总结",
                        "value": "wrap_up",
                        "description": "保留当前进展并整理结论。",
                    },
                ],
                source=str(advice.get("source") or "budget_convergence"),
            )
        return {
            "status": "failed",
            "summary": summary,
            "error_code": "MAX_STEPS_NO_SUCCESSFUL_TOOLS" if no_successful_tool_results else "MAX_STEPS_EXHAUSTED",
            "tool_results": tool_results,
            "budget_exhausted": True,
        }

    def _record_main_workflow_budget_progress(
        self,
        *,
        session_id: str,
        task: dict[str, Any],
        context: dict[str, Any],
        goal: str,
        tool_results: list[dict[str, Any]],
        steps: int,
        max_steps: int,
        terminal_success: bool = False,
    ) -> dict[str, Any]:
        routing = dict(task.get("routing") or context.get("routing") or {})
        workflow = dict(routing.get("mainWorkflow") or {})
        if not workflow:
            return task
        budget_state = dict(workflow.get("budget") or {})
        step_dimension = self._main_workflow_step_dimension(consumed=steps, limit=max_steps)
        previous_pressure = str(budget_state.get("pressure") or "normal")
        pressure = str(step_dimension.get("pressure") or "normal")
        if terminal_success and pressure == "exhausted":
            step_dimension = {**step_dimension, "pressure": "critical", "completedAtLimit": True}
            pressure = "critical"
        budget_state.update({
            "consumedSteps": steps,
            "maxSteps": max_steps,
            "remainingSteps": step_dimension.get("remaining"),
            "pressure": pressure,
            "exhausted": pressure == "exhausted",
            "convergenceRequired": True,
        })
        dimensions = dict(budget_state.get("dimensions") or {})
        dimensions["steps"] = step_dimension
        context_dimension = self._main_workflow_context_dimension(context)
        if context_dimension:
            dimensions["context"] = context_dimension
        budget_state["dimensions"] = dimensions
        workflow["budget"] = budget_state
        workflow["convergence"] = self._active_budget_convergence_state(
            workflow=workflow,
            pressure=pressure,
            goal=goal,
            tool_results=tool_results,
        )
        routing["mainWorkflow"] = workflow
        task = self._store.update_task(task_id=task["id"], routing=routing)
        if pressure in {"watch", "critical"} and previous_pressure != pressure:
            self._publish(
                session_id=session_id,
                task=task,
                event_type="task.budget.pressure",
                payload={
                    "dimension": "steps",
                    "pressure": pressure,
                    "consumedSteps": steps,
                    "remainingSteps": step_dimension.get("remaining"),
                    "maxSteps": max_steps,
                    "budget": budget_state,
                    "convergence": workflow["convergence"],
                },
                visibility="panel",
            )
            self._publish_budget_progress(
                session_id=session_id,
                task=task,
                phase="budget_pressure",
                pressure=pressure,
                consumed_steps=steps,
                max_steps=max_steps,
                remaining_steps=step_dimension.get("remaining"),
                recommended_action=(workflow.get("convergence") or {}).get("recommendedAction"),
            )
        return task

    def _active_budget_convergence_state(
        self,
        *,
        workflow: dict[str, Any],
        pressure: str,
        goal: str,
        tool_results: list[dict[str, Any]],
    ) -> dict[str, Any]:
        existing = dict(workflow.get("convergence") or {})
        if existing.get("reason") in {"max_steps_exhausted", "wrap_up_requested", "change_requested"}:
            return existing
        if pressure == "critical":
            return {
                **existing,
                "state": "budget_pressure",
                "reason": "step_budget_critical",
                "recommendedAction": "focus_or_wrap_up",
                "resumable": True,
                "requiresUserDecision": False,
                "budgetPressure": pressure,
                "availableActions": ["continue", "pause", "wrap_up", "cancel"],
                "handoffFocus": self._budget_handoff_focus(goal=goal, tool_results=tool_results),
                "source": "runtime",
            }
        if pressure == "watch":
            return {
                **existing,
                "state": "active",
                "reason": "budget_watch",
                "recommendedAction": "continue_with_focus",
                "resumable": True,
                "requiresUserDecision": False,
                "budgetPressure": pressure,
                "availableActions": ["continue", "pause", "cancel"],
                "source": "runtime",
            }
        return {
            **existing,
            "state": "active",
            "reason": "within_budget",
            "recommendedAction": "continue",
            "resumable": True,
            "requiresUserDecision": False,
            "budgetPressure": pressure,
            "availableActions": ["pause", "cancel", "supplement"],
            "source": existing.get("source") or "runtime",
        }

    @staticmethod
    def _main_workflow_context_dimension(context: dict[str, Any]) -> dict[str, Any] | None:
        stats = context.get("budgetStats") if isinstance(context, dict) else None
        if not isinstance(stats, dict):
            return None
        limit = stats.get("maxContextTokens")
        estimated = stats.get("estimatedInputTokens") or stats.get("estimatedTokens") or stats.get("messageTokens")
        try:
            limit_int = int(limit) if limit is not None else 0
            estimated_int = int(estimated) if estimated is not None else 0
        except (TypeError, ValueError):
            return None
        if limit_int <= 0:
            return None
        ratio = estimated_int / limit_int
        if ratio >= 1:
            pressure = "exhausted"
        elif ratio >= 0.85:
            pressure = "critical"
        elif ratio >= 0.65:
            pressure = "watch"
        else:
            pressure = "normal"
        return {
            "limit": limit_int,
            "estimated": estimated_int,
            "remaining": max(0, limit_int - estimated_int),
            "pressure": pressure,
        }

    def _publish_budget_progress(
        self,
        *,
        session_id: str,
        task: dict[str, Any],
        phase: str,
        pressure: str,
        consumed_steps: int,
        max_steps: int,
        remaining_steps: Any = None,
        recommended_action: str | None = None,
    ) -> None:
        publish_progress = getattr(self, "_publish_assistant_progress", None)
        if not callable(publish_progress):
            return
        if phase == "budget_exhausted":
            text = "已达到步骤预算，正在整理当前进展"
            status = "waiting"
        elif pressure == "critical":
            text = "步骤预算接近上限，正在收束当前任务"
            status = "running"
        else:
            return
        publish_progress(
            session_id=session_id,
            task=task,
            text=text,
            phase=phase,
            status=status,
            payload={
                "pressure": pressure,
                "consumedSteps": consumed_steps,
                "remainingSteps": remaining_steps,
                "maxSteps": max_steps,
                "recommendedAction": recommended_action,
            },
        )

    def _budget_convergence_runtime_action(self, advice: dict[str, Any]) -> str:
        action = str(advice.get("action") or "")
        if action in {"pause_for_user", "ask_user", "request_more_budget"}:
            return "review_partial"
        if action == "fail":
            return "review_failure"
        if action == "continue_with_constraints":
            return "continue_requires_budget_update"
        return "summarize_partial"

    def _budget_convergence_decision(
        self,
        *,
        session_id: str,
        task: dict[str, Any],
        goal: str,
        budget_state: dict[str, Any],
        context: dict[str, Any],
        tool_results: list[dict[str, Any]],
        steps: int,
        max_steps: int,
        reason: str,
    ) -> dict[str, Any]:
        fallback = {
            "action": "pause_for_user",
            "reason": reason,
            "handoffFocus": self._budget_handoff_focus(goal=goal, tool_results=tool_results),
            "resumePolicy": "requires_user_follow_up",
            "source": "runtime_fallback",
        }
        advisor = getattr(self, "_decision_advisor", None)
        if advisor is None:
            return fallback
        advice = None
        try:
            input_context = {
                "goal": goal,
                "budget_state": budget_state,
                "task_status": task.get("status"),
                "automation": ((task.get("routing") or {}).get("mainWorkflow") or {}).get("automation"),
                "reason": reason,
                "steps": steps,
                "max_steps": max_steps,
                "tool_results_summary": self._tool_results_summary_for_budget(tool_results),
                "main_workflow": ((task.get("routing") or {}).get("mainWorkflow"))
                if isinstance(task.get("routing"), dict)
                else None,
                "config": context.get("config") if isinstance(context, dict) else None,
            }
            advice = advisor.advise("budget_convergence", input_context)
            payload = advice.payload if isinstance(advice.payload, dict) else {}
            if advice.accepted and payload.get("action"):
                decision = {
                    **fallback,
                    "action": str(payload.get("action")),
                    "reason": str(payload.get("reason") or advice.rationale or reason)[:500],
                    "source": advice.source,
                    "advisorProposalId": advice.proposal_id,
                }
                self._copy_optional_budget_advice_fields(payload, decision)
            else:
                decision = {
                    **fallback,
                    "source": getattr(advice, "source", "rule_fallback"),
                    "fallbackReason": getattr(advice, "fallback_reason", None),
                }
            record_id = self._record_budget_convergence_proposal(
                session_id=session_id,
                task=task,
                goal=goal,
                advice=advice,
                runtime_decision=decision,
            )
            if record_id:
                decision["proposalRecordId"] = record_id
            self._publish(
                session_id=session_id,
                task=task,
                event_type="agent.decision.budget_convergence",
                payload={
                    "decision": decision,
                    "budget": budget_state,
                    "reason": reason,
                },
                visibility="panel",
            )
            return decision
        except Exception as exc:  # noqa: BLE001
            logger.warning("Budget convergence advisor failed for task %s: %s", task.get("id"), exc)
            return fallback

    @staticmethod
    def _copy_optional_budget_advice_fields(payload: dict[str, Any], decision: dict[str, Any]) -> None:
        field_map = {
            "handoff_focus": "handoffFocus",
            "resume_policy": "resumePolicy",
            "next_user_options": "nextUserOptions",
            "constraints": "constraints",
            "userMessage": "userMessage",
        }
        for source_key, target_key in field_map.items():
            value = payload.get(source_key)
            if value:
                decision[target_key] = value

    def _record_budget_convergence_proposal(
        self,
        *,
        session_id: str,
        task: dict[str, Any],
        goal: str,
        advice: Any,
        runtime_decision: dict[str, Any],
    ) -> str | None:
        try:
            proposal = dict(getattr(advice, "payload", None) or {})
            proposal.setdefault("action", runtime_decision.get("action"))
            proposal["runtimeAction"] = runtime_decision.get("action")
            source = {
                "type": getattr(advice, "source", runtime_decision.get("source") or "runtime"),
                "confidence": getattr(advice, "confidence", None),
                "rationale": getattr(advice, "rationale", None),
                "fallbackReason": getattr(advice, "fallback_reason", None),
                "advisorProposalId": getattr(advice, "proposal_id", None),
            }
            model_id = getattr(advice, "model_id", None)
            if model_id:
                source["model_id"] = model_id
            record = self._store.create_proposal({
                "kind": "budget_convergence",
                "sessionId": session_id,
                "taskId": task["id"],
                "proposal": proposal,
                "source": source,
                "inputSummary": str(goal or "")[:500],
                "modelId": model_id,
            })
            proposal_id = record["proposal"]["id"]
            accepted = bool(getattr(advice, "accepted", False))
            reasons = [] if accepted else (
                list(getattr(advice, "validation_reasons", None) or [])
                or [str(getattr(advice, "fallback_reason", None) or "advisor unavailable")]
            )
            self._store.validate_proposal({
                "proposalId": proposal_id,
                "status": "accepted" if accepted else "rejected",
                "reasons": reasons,
            })
            return proposal_id
        except Exception:  # noqa: BLE001
            logger.debug("Failed to record budget convergence proposal", exc_info=True)
            return None

    @staticmethod
    def _budget_handoff_focus(*, goal: str, tool_results: list[dict[str, Any]]) -> str:
        completed = sum(1 for item in tool_results if (item.get("result") or {}).get("status") in {None, "completed", "applied", "written"})
        failed = sum(1 for item in tool_results if (item.get("result") or {}).get("status") in {"failed", "error", "timeout"})
        return (
            f"Preserve partial progress for goal: {goal[:300]}. "
            f"Tool results so far: {len(tool_results)} total, {completed} completed, {failed} failed."
        )

    @staticmethod
    def _tool_results_summary_for_budget(tool_results: list[dict[str, Any]]) -> dict[str, Any]:
        recent: list[dict[str, Any]] = []
        completed = 0
        failed = 0
        for item in tool_results:
            result = item.get("result") if isinstance(item, dict) else None
            status = result.get("status") if isinstance(result, dict) else None
            if status in {None, "completed", "applied", "written"}:
                completed += 1
            if status in {"failed", "error", "timeout"}:
                failed += 1
            recent.append({
                "name": item.get("name"),
                "status": status or "completed",
            })
        return {
            "total": len(tool_results),
            "completed": completed,
            "failed": failed,
            "recent": recent[-5:],
        }

    def _pause_react_for_user_question(
        self,
        *,
        session_id: str,
        task: dict[str, Any],
        goal: str,
        context: dict[str, Any],
        messages: list[dict[str, Any]],
        tool_results: list[dict[str, Any]],
        steps: int,
        react_started: bool,
        patch_repair_attempts: int,
        question: str,
        summary: str | None = None,
        reason: str | None = None,
        resume_policy: str = "requires_user_follow_up",
        options: Any = None,
        source: str = "runtime",
    ) -> dict[str, Any]:
        normalized_options = self._normalize_user_question_options(options)
        if not normalized_options:
            normalized_options = self._default_user_question_options()
        pending_state = {
            "session_id": session_id,
            "goal": goal,
            "context": context,
            "messages": [
                *messages,
                {"role": "assistant", "content": question},
            ],
            "tool_results": tool_results,
            "steps": steps,
            "react_started": react_started,
            "patch_repair_attempts": patch_repair_attempts,
            "pending_tool_call": None,
            "pending_tool_spec": None,
            "remaining_tool_calls": [],
        }
        self._pending_react_tasks[task["id"]] = pending_state
        self._save_pending_react_state(task["id"], pending_state)
        paused_task = self._store.update_task_status(task_id=task["id"], status="paused")
        self._publish(
            session_id=session_id,
            task=paused_task,
            event_type="ask_user_question",
            payload={
                "title": "需要你补充信息",
                "question": question,
                "summary": summary or question,
                "status": "waiting",
                "reason": reason or "needs_user_input",
                "resumePolicy": resume_policy,
                "options": normalized_options,
                "source": source,
            },
        )
        self._publish(
            session_id=session_id,
            task=paused_task,
            event_type="task.paused",
            payload={
                "status": "paused",
                "previousStatus": task.get("status"),
                "reason": "ask_user_question",
            },
        )
        return {"status": "paused", "question": question}

    @staticmethod
    def _normalize_user_question_options(options: Any) -> list[dict[str, str]]:
        if not isinstance(options, list):
            return []
        normalized: list[dict[str, str]] = []
        for index, option in enumerate(options[:4]):
            if isinstance(option, dict):
                label = str(option.get("label") or option.get("title") or option.get("value") or "").strip()
                value = str(option.get("value") or label or f"option_{index + 1}").strip()
                description = str(option.get("description") or option.get("detail") or "").strip()
            else:
                label = str(option or "").strip()
                value = label or f"option_{index + 1}"
                description = ""
            if not label:
                label = f"选项 {index + 1}"
            normalized.append({
                "label": label[:80],
                "value": value[:120],
                "description": description[:240],
            })
        return normalized

    @staticmethod
    def _default_user_question_options() -> list[dict[str, str]]:
        return [
            {
                "label": "继续当前方向",
                "value": "continue",
                "description": "把这条回复作为补充说明，恢复当前任务。",
            },
            {
                "label": "调整目标",
                "value": "change_goal",
                "description": "补充新的约束或范围，让任务按新目标继续。",
            },
            {
                "label": "收尾总结",
                "value": "wrap_up",
                "description": "停止深入执行，优先整理当前进展。",
            },
        ]

    @staticmethod
    def _context_with_additional_step_budget(
        context: dict[str, Any],
        *,
        consumed_steps: int,
        current_limit: int,
    ) -> dict[str, Any]:
        next_context = deepcopy(context)
        additional_steps = max(3, min(10, current_limit))
        next_context["_max_task_steps_override"] = max(current_limit, consumed_steps) + additional_steps
        routing = dict(next_context.get("routing") or {})
        workflow = dict(routing.get("mainWorkflow") or {})
        budget = dict(workflow.get("budget") or {})
        budget["resumeMaxSteps"] = next_context["_max_task_steps_override"]
        budget["resumeAdditionalSteps"] = additional_steps
        workflow["budget"] = budget
        routing["mainWorkflow"] = workflow
        next_context["routing"] = routing
        return next_context

    @staticmethod
    def _budget_exhausted_summary(
        *,
        goal: str,
        tool_results: list[dict[str, Any]],
        steps: int,
        max_steps: int,
    ) -> str:
        completed = sum(1 for item in tool_results if (item.get("result") or {}).get("status") in {None, "completed", "applied", "written"})
        failed = sum(1 for item in tool_results if (item.get("result") or {}).get("status") in {"failed", "error", "timeout"})
        return (
            f"Reached maxTaskSteps ({max_steps}) after {steps} step(s). "
            f"Partial progress is preserved for review. "
            f"Tool results recorded: {len(tool_results)} total, {completed} completed, {failed} failed. "
            f"Original goal: {goal}"
        )

    def _initial_react_messages(self, context: dict[str, Any], goal: str) -> list[dict[str, Any]]:
        messages = context.get("messages")
        if isinstance(messages, list) and messages:
            return list(messages)
        return [{"role": "user", "content": goal}]

    def _supplement_prompt_line(self, entry: dict[str, Any], context: dict[str, Any]) -> str:
        line = f"- {entry['content']}"
        reference_text = self._supplement_reference_text(entry, context)
        if reference_text:
            return f"{line}\n{reference_text}"
        return line

    def _supplement_reference_text(self, entry: dict[str, Any], context: dict[str, Any]) -> str:
        references = self._supplement_file_references(self._supplement_metadata(entry))
        if not references:
            return ""
        workspace_root = context.get("workspace_root")
        if not isinstance(workspace_root, str) or not workspace_root.strip():
            return ""
        root = Path(workspace_root).resolve()
        if not root.exists() or not root.is_dir():
            return ""

        sections: list[str] = []
        for reference in references[:4]:
            resolved = self._resolve_supplement_reference(root, reference)
            if resolved is None:
                continue
            text = self._read_supplement_reference(resolved, max_bytes=12_000)
            if not text:
                continue
            try:
                display_path = resolved.relative_to(root).as_posix()
            except ValueError:
                display_path = reference
            sections.extend(
                [
                    f"Referenced file content: {display_path}",
                    "```text",
                    text,
                    "```",
                ]
            )
        return "\n".join(sections)

    @staticmethod
    def _supplement_metadata(entry: dict[str, Any]) -> dict[str, Any]:
        metadata = entry.get("metadata")
        if isinstance(metadata, dict):
            return metadata
        raw = entry.get("metadata_json")
        if not raw:
            return {}
        import json

        try:
            parsed = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}

    @staticmethod
    def _supplement_file_references(metadata: dict[str, Any]) -> list[str]:
        references: list[str] = []
        for key in ("fileReferences", "attachments"):
            value = metadata.get(key)
            values = value if isinstance(value, list) else [value] if isinstance(value, str) else []
            for item in values:
                if not isinstance(item, str):
                    continue
                reference = item.strip().strip("\"'`").replace("\\", "/")
                if reference and reference not in references:
                    references.append(reference)
        return references

    @staticmethod
    def _resolve_supplement_reference(root: Path, reference: str) -> Path | None:
        if not reference or "\x00" in reference:
            return None
        candidate = Path(reference)
        try:
            resolved = candidate.resolve() if candidate.is_absolute() else (root / reference).resolve()
        except (OSError, RuntimeError):
            return None
        try:
            resolved.relative_to(root)
        except ValueError:
            return None
        return resolved if resolved.is_file() else None

    @staticmethod
    def _read_supplement_reference(path: Path, *, max_bytes: int) -> str:
        if max_bytes <= 0:
            return ""
        try:
            with path.open("rb") as handle:
                data = handle.read(max_bytes + 1)
        except OSError:
            return ""
        if b"\x00" in data[:4096]:
            return ""
        truncated = len(data) > max_bytes
        text = data[:max_bytes].decode("utf-8", errors="replace").strip()
        if not text:
            return ""
        if truncated:
            return f"{text.rstrip()}\n[truncated]"
        return text

    # Strategies that are allowed to create child tasks via the `task` tool.
    _TASK_TOOL_STRATEGIES: frozenset[str] = frozenset({
        "plan_execute", "plan_supervise", "plan_swarm",
    })

    def _provider_tools(self, context: dict[str, Any]) -> list[dict[str, Any]]:
        if context.get("minimal") is True or context.get("disableTools") is True:
            return []
        tools_by_name: dict[str, dict[str, Any]] = {}
        # 1. Start with context-level tools (built by ContextBuilder)
        openai_tools = context.get("openai_tools")
        if isinstance(openai_tools, list):
            for schema in openai_tools:
                if isinstance(schema, dict) and isinstance(schema.get("name"), str):
                    tools_by_name[schema["name"]] = schema
        for schema in context.get("tools") or []:
            if isinstance(schema, dict) and isinstance(schema.get("name"), str):
                tools_by_name.setdefault(schema["name"], schema)
        # 2. Always merge MCP / dynamically registered tools
        for schema in self._tool_registry.schemas:
            if isinstance(schema, dict) and isinstance(schema.get("name"), str):
                tools_by_name.setdefault(schema["name"], schema)
        # 3. Remove the `task` tool when the routing strategy does not need it,
        #    so that the LLM cannot spontaneously create child tasks for simple queries.
        routing = context.get("routing")
        if isinstance(routing, dict):
            strategy = routing.get("strategy", "")
            if strategy not in self._TASK_TOOL_STRATEGIES:
                tools_by_name.pop("task", None)
        return [tools_by_name[name] for name in sorted(tools_by_name)]

    def _provider_tools_for_turn(
        self,
        *,
        context: dict[str, Any],
        tool_results: list[dict[str, Any]],
        cached_provider_tools: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        decision = self._tool_policy_decision_for_turn(
            task={"role": context.get("runtimeRole") or "root"},
            context=context,
            tool_results=tool_results,
            cached_provider_tools=cached_provider_tools,
        )
        return decision.allowed_tools

    def _tool_policy_decision_for_turn(
        self,
        *,
        task: dict[str, Any],
        context: dict[str, Any],
        tool_results: list[dict[str, Any]],
        cached_provider_tools: list[dict[str, Any]],
    ) -> ToolPolicyDecision:
        resolver = getattr(self, "_tool_policy_resolver", None)
        if resolver is None:
            resolver = ToolPolicyResolver()
            self._tool_policy_resolver = resolver
        return resolver.resolve(
            task=task,
            context=context,
            tool_results=tool_results,
            registered_tools=cached_provider_tools,
        )

    def _should_synthesize_after_task_results(
        self,
        context: dict[str, Any],
        tool_results: list[dict[str, Any]],
    ) -> bool:
        if not tool_results:
            return False
        resolver = getattr(self, "_tool_policy_resolver", None)
        if resolver is None:
            resolver = ToolPolicyResolver()
            self._tool_policy_resolver = resolver
        if resolver.allow_tools_after_task_results(context):
            return False
        last_result = tool_results[-1]
        if last_result.get("name") != "task":
            return False
        result = last_result.get("result")
        if isinstance(result, dict) and result.get("status") == "waiting_approval":
            return False
        return True

    def _max_task_steps(self, context: dict[str, Any]) -> int:
        override = context.get("_max_task_steps_override")
        try:
            if override is not None:
                return max(1, int(override))
        except (TypeError, ValueError):
            pass
        routing = context.get("routing")
        if isinstance(routing, dict):
            workflow = routing.get("mainWorkflow")
            budget = workflow.get("budget") if isinstance(workflow, dict) else None
            if isinstance(budget, dict):
                for key in ("resumeMaxSteps", "maxSteps", "max_steps"):
                    budget_steps = budget.get(key)
                    if budget_steps is not None:
                        try:
                            return max(1, int(budget_steps))
                        except (TypeError, ValueError):
                            pass
            routing_steps = routing.get("max_steps")
            if routing_steps is not None:
                try:
                    return max(1, int(routing_steps))
                except (TypeError, ValueError):
                    pass
        autonomy_steps = self._autonomy_profile_int(context, "maxSteps")
        if autonomy_steps is not None:
            return autonomy_steps
        # Fallback to global config
        config = context.get("config") or {}
        policy = config.get("policy") if isinstance(config, dict) else {}
        raw_value = policy.get("maxTaskSteps", 20) if isinstance(policy, dict) else 20
        try:
            return max(1, int(raw_value))
        except (TypeError, ValueError):
            return 20

    def _max_parallel_subtasks(self, context: dict[str, Any]) -> int:
        return self._autonomy_profile_int(context, "maxParallelSubtasks") or 4

    def _child_subtask_timeout_ms(self, context: dict[str, Any]) -> int | None:
        routing = context.get("routing") if isinstance(context, dict) else {}
        workflow = routing.get("mainWorkflow") if isinstance(routing, dict) else {}
        budget = workflow.get("budget") if isinstance(workflow, dict) else {}
        if isinstance(budget, dict):
            for key in ("childTaskTimeoutMs", "childTimeoutMs"):
                raw_budget_timeout = budget.get(key)
                try:
                    if raw_budget_timeout is not None:
                        return max(1000, int(raw_budget_timeout))
                except (TypeError, ValueError):
                    continue
        profile_timeout = (
            self._autonomy_profile_int(context, "childTaskTimeoutMs")
            or self._autonomy_profile_int(context, "childTimeoutMs")
            or self._autonomy_profile_int(context, "subtaskTimeoutMs")
        )
        if profile_timeout is not None:
            return profile_timeout
        policy_timeout = self._policy_timeout_ms(
            context,
            "childTaskTimeoutMs",
            "childTimeoutMs",
            "subtaskTimeoutMs",
        )
        if policy_timeout is not None:
            return policy_timeout
        profile_timeout = self._autonomy_profile_int(context, "timeoutMs")
        if profile_timeout is not None:
            return profile_timeout
        return self._policy_timeout_ms(context, "commandTimeoutMs")

    def _policy_timeout_ms(self, context: dict[str, Any], *keys: str) -> int | None:
        config = context.get("config") if isinstance(context, dict) else {}
        policy = config.get("policy") if isinstance(config, dict) else {}
        if not isinstance(policy, dict):
            return None
        raw_value = next((policy.get(key) for key in keys if policy.get(key) is not None), None)
        try:
            return max(1000, int(raw_value)) if raw_value is not None else None
        except (TypeError, ValueError):
            return None

    def _compaction_threshold(self, context: dict[str, Any]) -> int:
        """Get compaction threshold from autonomy profile, falling back to 256000."""
        threshold = self._autonomy_profile_int(context, "compactionThreshold")
        if threshold is not None:
            return threshold
        return 256000

    def _consult_context_policy_advisor(
        self,
        task: dict[str, Any],
        goal: str,
        current_threshold: int,
        context: dict[str, Any],
    ) -> dict[str, Any] | None:
        advisor = getattr(self, "_decision_advisor", None)
        if advisor is None:
            return None
        if not self._should_consult_context_policy_advisor(task, current_threshold, context):
            return None
        try:
            input_context: dict[str, Any] = {
                "goal": goal[:2000],
                "token_budget": current_threshold,
            }
            config = context.get("config") if isinstance(context, dict) else None
            if isinstance(config, dict):
                input_context["config"] = config
            result = advisor.advise("context_policy", input_context)
            # Emit trace event for the decision
            if hasattr(self._store, "append_trace_event"):
                self._store.append_trace_event(
                    task_id=task["id"],
                    session_id=task.get("sessionId"),
                    event_type="agent.decision.context_policy",
                    source="advisor",
                    related_id=result.proposal_id,
                    payload={
                        "proposalId": result.proposal_id,
                        "accepted": result.accepted,
                        "source": result.source,
                        "rationale": result.rationale,
                        "confidence": result.confidence,
                        "currentThreshold": current_threshold,
                        "advisedThreshold": result.payload.get("compaction_threshold") if result.accepted else None,
                        "fallbackReason": result.fallback_reason,
                    },
                )
            if result.accepted:
                return result.payload
            return None
        except Exception as exc:
            logger.warning("Context policy advisor call failed for task %s: %s", task.get("id"), exc)
            return None

    def _should_consult_context_policy_advisor(
        self,
        task: dict[str, Any],
        current_threshold: int,
        context: dict[str, Any],
    ) -> bool:
        if context.get("_skip_context_policy_advisor") is True:
            return False
        if context.get("_child_worker") is True or task.get("role") != "root":
            return bool(self._advisor_config(context).get("enableChildContextPolicyAdvisor"))
        budget_stats = context.get("budgetStats") if isinstance(context, dict) else None
        estimated_tokens = budget_stats.get("estimatedTokens") if isinstance(budget_stats, dict) else None
        try:
            estimated = int(estimated_tokens)
            threshold = max(1, int(current_threshold))
        except (TypeError, ValueError):
            return False
        near_budget_ratio = self._advisor_context_policy_near_budget_ratio(context)
        return estimated >= int(threshold * near_budget_ratio)

    def _advisor_context_policy_near_budget_ratio(self, context: dict[str, Any]) -> float:
        raw_ratio = self._advisor_config(context).get("contextPolicyNearBudgetRatio", 0.92)
        try:
            ratio = float(raw_ratio)
        except (TypeError, ValueError):
            return 0.92
        return min(0.98, max(0.1, ratio))

    def _advisor_config(self, context: dict[str, Any]) -> dict[str, Any]:
        config = context.get("config") if isinstance(context, dict) else None
        if not isinstance(config, dict):
            return {}
        advisor = config.get("advisor")
        if isinstance(advisor, dict):
            return advisor
        autonomy = config.get("autonomy")
        if isinstance(autonomy, dict):
            advisor = autonomy.get("advisor")
            if isinstance(advisor, dict):
                return advisor
        return {}

    def _autonomy_profile_int(self, context: dict[str, Any], key: str) -> int | None:
        profile = None
        routing = context.get("routing")
        if isinstance(routing, dict):
            snapshot = routing.get("profile_snapshot")
            if isinstance(snapshot, dict):
                profile = snapshot.get("autonomyProfile")
        if not isinstance(profile, dict):
            config = context.get("config") if isinstance(context, dict) else {}
            profile = self._active_config_profile(config, "autonomy") if isinstance(config, dict) else None
        if not isinstance(profile, dict):
            return None
        try:
            return max(1, int(profile.get(key)))
        except (TypeError, ValueError):
            return None

    def _context_with_worker_budget(self, context: dict[str, Any], budget: WorkerBudget) -> dict[str, Any]:
        if budget.tokens.limit is None:
            return context
        updated_context = deepcopy(context)
        config = deepcopy(updated_context.get("config") or {})
        provider = deepcopy(config.get("provider") or {})
        existing = provider.get("maxTokens") or provider.get("maxOutputTokens")
        try:
            existing_limit = int(existing) if existing is not None else None
        except (TypeError, ValueError):
            existing_limit = None
        remaining = budget.tokens.remaining
        capped_limit = remaining if existing_limit is None or remaining is None else min(existing_limit, remaining)
        if capped_limit is not None:
            provider["maxTokens"] = capped_limit
            provider["maxOutputTokens"] = capped_limit
            config["provider"] = provider
            updated_context["config"] = config
        return updated_context

    def _advance_after_tool(self, session_id: str, task: dict[str, Any], tool_spec: dict[str, Any]) -> None:
        next_step_id = self._next_plan_step_id(task.get("plan") or [], tool_spec["plan_step_id"])
        task["plan"] = self._planner.advance(
            task.get("plan") or [],
            tool_spec["plan_step_id"],
            next_step_id=next_step_id,
        )
        updated_task = self._store.update_task(
            task_id=task["id"],
            status=task["status"],
            plan=task["plan"],
        )
        task.update(updated_task)
        self._publish(
            session_id=session_id,
            task=task,
            event_type="task.updated",
            payload={"status": task["status"], "plan": task["plan"], "currentStep": task.get("currentStep")},
        )

    def _run_minimal_loop(
        self,
        session_id: str,
        task: dict[str, Any],
        goal: str,
        context: dict[str, Any],
        budget: WorkerBudget | None = None,
    ) -> list[dict[str, Any]]:
        tool_results: list[dict[str, Any]] = []
        tool_sequence = self._annotate_tool_spec_batch(self._provider.choose_tool_sequence(goal=goal, context=context))

        for index, tool_spec in enumerate(tool_sequence):
            tool_result = self._execute_tool(
                session_id=session_id,
                task=task,
                tool_spec=tool_spec,
                budget=budget,
            )
            tool_results.append(tool_result)
            if self._is_patch_validation_failure(tool_spec["name"], tool_result["result"]):
                raise RuntimeError(self._tool_failure_summary(tool_spec, tool_result["result"]))
            if task["status"] == "waiting_approval":
                self._publish(
                    session_id=session_id,
                    task=task,
                    event_type="task.updated",
                    payload={"status": task["status"], "plan": task["plan"], "currentStep": task.get("currentStep")},
                )
                return tool_results
            task["plan"] = self._planner.advance(
                task["plan"],
                tool_spec["plan_step_id"],
                next_step_id=self._next_minimal_loop_step_id(
                    task["plan"],
                    tool_sequence,
                    current_index=index,
                    completed_step_id=tool_spec["plan_step_id"],
                ),
            )
            updated_task = self._store.update_task(
                task_id=task["id"],
                status=task["status"],
                plan=task["plan"],
            )
            task.update(updated_task)
            self._publish(
                session_id=session_id,
                task=task,
                event_type="task.updated",
                payload={"status": task["status"], "plan": task["plan"], "currentStep": task.get("currentStep")},
            )

        follow_up_tool = self._provider.pick_follow_up_tool(context=context, tool_results=tool_results)
        if follow_up_tool is not None:
            follow_up_tool = self._annotate_follow_up_tool_spec(follow_up_tool, tool_results)
            tool_results.append(
                self._execute_tool(
                    session_id=session_id,
                    task=task,
                    tool_spec=follow_up_tool,
                    budget=budget,
                )
            )

        final_completed_step_id = self._last_completed_minimal_step_id(tool_sequence, tool_results)
        if final_completed_step_id is not None:
            next_step_id = self._next_plan_step_id(task["plan"], final_completed_step_id)
            if next_step_id is not None:
                task["plan"] = self._planner.advance(
                    task["plan"],
                    final_completed_step_id,
                    next_step_id=next_step_id,
                )
        updated_task = self._store.update_task(
            task_id=task["id"],
            status=task["status"],
            plan=task["plan"],
        )
        task.update(updated_task)
        self._publish(
            session_id=session_id,
            task=task,
            event_type="task.updated",
            payload={"status": task["status"], "plan": task["plan"], "currentStep": task.get("currentStep")},
        )
        self._publish(
            session_id=session_id,
            task=task,
            event_type="assistant.token",
            payload={"delta": "Completed the minimal tool loop and preparing a summary..."},
        )
        return tool_results

    def _consume_budget_from_provider_response(
        self,
        *,
        session_id: str,
        task: dict[str, Any],
        budget: WorkerBudget | None,
        response: dict[str, Any],
    ) -> None:
        if budget is None:
            return
        raw = response.get("raw")
        usage = raw.get("usage") if isinstance(raw, dict) else None
        consumed = budget.consume_provider_usage(usage)
        if consumed <= 0:
            return
        self._publish(
            session_id=session_id,
            task=task,
            event_type="collab.worker.budget.updated",
            payload={
                "dimension": "tokens",
                "consumed": consumed,
                "budget": budget.to_metadata(),
            },
        )

    def _next_step_id(self, tool_sequence: list[dict[str, Any]], current_index: int) -> str | None:
        if current_index + 1 >= len(tool_sequence):
            return "summarize-findings"
        return tool_sequence[current_index + 1]["plan_step_id"]

    def _next_minimal_loop_step_id(
        self,
        plan: list[dict[str, Any]],
        tool_sequence: list[dict[str, Any]],
        *,
        current_index: int,
        completed_step_id: str,
    ) -> str | None:
        next_plan_step = self._next_plan_step_id(plan, completed_step_id)
        if next_plan_step is not None:
            return next_plan_step
        return self._next_step_id(tool_sequence, current_index)

    def _last_completed_minimal_step_id(
        self,
        tool_sequence: list[dict[str, Any]],
        tool_results: list[dict[str, Any]],
    ) -> str | None:
        completed_step_ids = [
            spec.get("plan_step_id")
            for spec in tool_sequence
            if isinstance(spec.get("plan_step_id"), str)
        ]
        if tool_results:
            maybe_follow_up = self._plan_step_for_tool(tool_results[-1]["name"])
            if isinstance(maybe_follow_up, str):
                completed_step_ids.append(maybe_follow_up)
        for step_id in reversed(completed_step_ids):
            if isinstance(step_id, str) and step_id:
                return step_id
        return None

    def _next_plan_step_id(self, plan: list[dict[str, Any]], completed_step_id: str) -> str | None:
        seen_completed = False
        for step in plan:
            step_id = step.get("id")
            if step_id == completed_step_id:
                seen_completed = True
                continue
            if not seen_completed:
                continue
            if step.get("status") != "completed":
                return step_id if isinstance(step_id, str) else None
        return None
