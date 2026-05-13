"""React Runner Mixin — core ReAct loop orchestration.

Composes ProviderTurnMixin, ReactToolHelpersMixin, and ReactResumeMixin.
This module retains the main loop, context/budget helpers, minimal loop,
and plan advancement logic.
"""
from __future__ import annotations

import logging
from copy import deepcopy
from typing import Any

from ..context.token_budget import estimate_tokens
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
                raise RuntimeError(f"Reached maxTaskSteps ({max_steps}) before the provider returned a final answer.")

            # Drain pending supplements from task inbox
            pending_supplements = self._store.get_pending_supplements(task["id"])
            if pending_supplements:
                supplement_lines = []
                for entry in pending_supplements:
                    supplement_lines.append(f"- {entry['content']}")
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
            provider_context = {
                **context,
                "messages": messages,
                "tools": cached_provider_tools,
                "openai_tools": context.get("openai_tools") or cached_provider_tools,
                "tool_results": tool_results,
                "step": steps + 1,
                "max_steps": max_steps,
            }
            # --- ProviderTurn: create before provider call ---
            provider_turn = self._store.create_provider_turn(
                task_id=task["id"],
                session_id=session_id,
                turn_index=steps,
                model=context.get("config", {}).get("provider", {}).get("model"),
                request_message_count=len(messages),
                request_tool_count=len(cached_provider_tools),
                request_token_estimate=_msg_token_total,
            )
            self._fire_hooks("before_provider_turn", session_id, task, extra_context={"turnIndex": steps, "providerTurnId": provider_turn["id"]})
            # --- ContextSnapshot: capture what the model will see ---
            snapshot_meta = (context.get("_build_result") or context).get("snapshot_metadata", {}) or {}
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
                tool_count=len(cached_provider_tools),
                skill_id=context.get("routing", {}).get("skill_id") or snapshot_meta.get("skill_id"),
                token_estimate=_msg_token_total,
                max_context_tokens=context.get("budgetStats", {}).get("maxContextTokens"),
                prompt_layers=snapshot_meta.get("prompt_layers"),
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
                self._store.fail_provider_turn(turn_id=provider_turn["id"], error_summary=str(exc)[:500])
                raise
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
            raw_usage = response.get("usage") or {}
            self._store.complete_provider_turn(
                turn_id=provider_turn["id"],
                finish_reason=response.get("finish_reason"),
                usage=raw_usage,
                tool_call_count=len(parsed.get("tool_calls") or []),
                snapshot_id=snapshot["id"],
                turn_decision=turn_result.decision.value,
                thought_summary=turn_result.thought_summary[:500] if turn_result.thought_summary else None,
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
            if parsed["status"] == "fallback":
                return parsed

            react_started = True
            steps += 1
            assistant_text = parsed.get("message") or ""
            if parsed["status"] == "completed" and not assistant_text:
                assistant_text = parsed["summary"]
            if assistant_text and not response.get("_streamed_content"):
                self._publish(
                    session_id=session_id,
                    task=task,
                    event_type="assistant.token",
                    payload={"delta": assistant_text, "step": steps},
                )

            if parsed["status"] == "completed":
                return {
                    "status": "completed",
                    "summary": parsed["summary"],
                    "tool_results": tool_results,
                }

            tool_calls = parsed["tool_calls"]
            messages.append(
                {
                    "role": "assistant",
                    "content": assistant_text,
                    "tool_calls": tool_calls,
                }
            )
            for index, tool_call in enumerate(tool_calls):
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
                    )
                    if cache_key and not self._tool_failed(tool_spec["name"], tool_result["result"]):
                        read_file_cache[cache_key] = deepcopy(tool_result)
                    elif self._invalidates_read_file_cache(tool_spec["name"]):
                        read_file_cache.clear()
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

    def _initial_react_messages(self, context: dict[str, Any], goal: str) -> list[dict[str, Any]]:
        messages = context.get("messages")
        if isinstance(messages, list) and messages:
            return list(messages)
        return [{"role": "user", "content": goal}]

    # Strategies that are allowed to create child tasks via the `task` tool.
    _TASK_TOOL_STRATEGIES: frozenset[str] = frozenset({
        "plan_execute", "plan_supervise", "plan_swarm",
    })

    def _provider_tools(self, context: dict[str, Any]) -> list[dict[str, Any]]:
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
        return list(tools_by_name.values())

    def _max_task_steps(self, context: dict[str, Any]) -> int:
        autonomy_steps = self._autonomy_profile_int(context, "maxSteps")
        if autonomy_steps is not None:
            return autonomy_steps
        # Prefer routing-level max_steps (scenario-aware) over global config
        routing = context.get("routing")
        if isinstance(routing, dict):
            routing_steps = routing.get("max_steps")
            if routing_steps is not None:
                try:
                    return max(1, int(routing_steps))
                except (TypeError, ValueError):
                    pass
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

    def _compaction_threshold(self, context: dict[str, Any]) -> int:
        """Get compaction threshold from autonomy profile, falling back to 60000."""
        threshold = self._autonomy_profile_int(context, "compactionThreshold")
        if threshold is not None:
            return threshold
        return 60000

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
        try:
            result = advisor.advise("context_policy", {
                "goal": goal[:2000],
                "token_budget": current_threshold,
            })
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
        task["plan"] = self._planner.advance(
            task.get("plan") or [],
            tool_spec["plan_step_id"],
            next_step_id="summarize-findings",
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
        tool_sequence = self._provider.choose_tool_sequence(goal=goal, context=context)

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
                next_step_id=self._next_step_id(tool_sequence, index),
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
            tool_results.append(
                self._execute_tool(
                    session_id=session_id,
                    task=task,
                    tool_spec=follow_up_tool,
                    budget=budget,
                )
            )

        task["plan"] = self._planner.advance(
            task["plan"],
            "search-relevant-files",
            next_step_id="summarize-findings",
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
