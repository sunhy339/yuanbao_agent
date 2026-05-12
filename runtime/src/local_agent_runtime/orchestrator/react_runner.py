from __future__ import annotations

import json
import logging
import os
from copy import deepcopy
from typing import Any

from ..context.token_budget import estimate_tokens
from ..react.types import ProviderTurnResult, TurnDecision
from ..services.worker_budget import WorkerBudget, WorkerBudgetExceededError

logger = logging.getLogger(__name__)


class ReactRunnerMixin:
    def _tool_failed(self, tool_name: str, result: dict[str, Any]) -> bool:
        status = result.get("status")
        return status in {"failed", "timeout", "killed"} or result.get("ok") is False

    def _is_patch_validation_failure(self, tool_name: str, result: dict[str, Any]) -> bool:
        return tool_name == "apply_patch" and result.get("status") == "validation_failed"

    def _tool_failure_summary(self, tool_spec: dict[str, Any], result: dict[str, Any]) -> str:
        tool_name = tool_spec["name"]
        if tool_name == "run_command":
            status = result.get("status", "failed")
            exit_code = result.get("exitCode")
            stdout = (result.get("stdout") or "").strip()
            stderr = (result.get("stderr") or "").strip()
            preview = stdout.splitlines()[0] if stdout else stderr.splitlines()[0] if stderr else "no output"
            return f"Command failed with status {status} and exit code {exit_code}; first output: {preview[:120]}."
        if tool_name == "task":
            child_task_id = result.get("childTaskId") or result.get("task", {}).get("id") or "unknown child task"
            summary = (result.get("summary") or result.get("result", {}).get("summary") or "Child task failed.").strip()
            return f"Child task {child_task_id} failed: {summary}"
        if tool_name == "apply_patch":
            if result.get("status") == "validation_failed":
                summary = (result.get("summary") or "Patch validation failed.").strip()
                error = (result.get("error") or "Unknown validation error.").strip()
                return f"Patch validation failed for {summary}: {error}"
            summary = (result.get("summary") or "Patch tool failed.").strip()
            patch_id = result.get("patch_id", "unknown patch")
            return f"Apply patch failed for {patch_id}: {summary}"
        if tool_name == "git_status":
            summary = (result.get("summary") or "Git status failed.").strip()
            return f"Git status failed: {summary}"
        if tool_name == "git_diff":
            summary = (result.get("summary") or "Git diff failed.").strip()
            return f"Git diff failed: {summary}"
        if tool_name == "search_files":
            query = result.get("query", "unknown query")
            return f"Search failed for query '{query}'."
        if tool_name == "read_file":
            path = result.get("path", "unknown file")
            return f"Read file failed for {path}."
        return f"Tool {tool_name} failed."

    def _max_patch_repair_attempts(self, context: dict[str, Any]) -> int:
        config = context.get("config") or {}
        policy = config.get("policy") if isinstance(config, dict) else {}
        raw_value = policy.get("maxPatchRepairAttempts", 2) if isinstance(policy, dict) else 2
        try:
            return max(0, min(int(raw_value), 10))
        except (TypeError, ValueError):
            return 2

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
                    compaction_decision = self._compactor.should_compact(messages, 60000)
                    if compaction_decision.should_compact:
                        compacted = self._compactor.compact(
                            session_id=session_id,
                            messages=messages,
                            max_tokens=60000,
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

    def _request_provider_response(
        self,
        *,
        session_id: str,
        task: dict[str, Any],
        goal: str,
        provider_context: dict[str, Any],
        budget: WorkerBudget | None = None,
    ) -> dict[str, Any]:
        if not self._should_stream_provider(provider_context):
            self._append_provider_trace(task=task, event_type="provider.request", payload=self._provider_trace_payload(provider_context))
            span = self._tracer.start_span(
                "llm_generate",
                trace_id=getattr(self, "_active_trace_id", None),
                parent_span_id=getattr(self, "_active_parent_span_id", None),
            )
            try:
                response = self._provider.generate(goal, provider_context)
            except Exception:
                self._tracer.end_span(span.span_id, status="error")
                raise
            self._tracer.end_span(span.span_id, status="ok")
            self._consume_budget_from_provider_response(
                session_id=session_id,
                task=task,
                budget=budget,
                response=response,
            )
            self._append_provider_trace(task=task, event_type="provider.response", payload=self._provider_response_trace(response))
            return response

        self._append_provider_trace(
            task=task,
            event_type="provider.request",
            payload={**self._provider_trace_payload(provider_context), "stream": True},
        )
        logger.info(
            "Streaming provider response for task=%s step=%s",
            task["id"], provider_context.get("step"),
        )
        final_response: dict[str, Any] | None = None
        streamed_content = False
        _max_stream_retries = 1
        _stream_text_parts: list[str] = []
        _delta_count = 0
        for _stream_attempt in range(_max_stream_retries + 1):
            final_response = None
            streamed_content = False
            _stream_text_parts = []
            try:
                for event in self._provider.stream(goal, provider_context):
                    event_type = event.get("type")
                    if event_type == "content_delta":
                        delta = event.get("delta")
                        if isinstance(delta, str) and delta:
                            streamed_content = True
                            _delta_count += 1
                            if _delta_count <= 3 or _delta_count % 50 == 0:
                                logger.debug(
                                    "Stream delta #%d for task=%s: %r",
                                    _delta_count, task["id"], delta[:80],
                                )
                            # Repetition detection: if the same phrase appears
                            # 3+ times in accumulated output, truncate.
                            _stream_text_parts.append(delta)
                            # Periodically persist partial content for crash recovery
                            _partial_len = sum(len(p) for p in _stream_text_parts)
                            if _delta_count % 50 == 0 or _partial_len > 2048:
                                _active_msg_id = task.get("activeAssistantMessageId")
                                if _active_msg_id:
                                    self._store.update_message(_active_msg_id, content="".join(_stream_text_parts))
                            if self._detect_stream_repetition(_stream_text_parts):
                                logger.warning(
                                    "Stream repetition detected for task=%s, truncating after %d chars",
                                    task["id"],
                                    sum(len(p) for p in _stream_text_parts),
                                )
                                break
                            self._publish(
                                session_id=session_id,
                                task=task,
                                event_type="assistant.token",
                                payload={"delta": delta, "step": provider_context.get("step")},
                            )
                    elif event_type == "final":
                        response = event.get("response")
                        if isinstance(response, dict):
                            final_response = response
                    elif event_type == "finish_reason":
                        self._append_provider_trace(task=task, event_type="provider.stream.finish", payload=event)
                    elif event_type == "tool_call_delta":
                        self._append_provider_trace(task=task, event_type="provider.stream.tool_call_delta", payload=event)
                logger.info(
                    "Stream completed for task=%s: deltas=%d streamed=%s has_final=%s",
                    task["id"], _delta_count, streamed_content, final_response is not None,
                )
                # Persist full streamed content to assistant message
                if _stream_text_parts:
                    _active_msg_id = task.get("activeAssistantMessageId")
                    if _active_msg_id:
                        self._store.update_message(_active_msg_id, content="".join(_stream_text_parts))
                break  # stream completed successfully
            except Exception as stream_exc:
                from ..provider.openai_compatible import ProviderAdapterError
                is_retryable = isinstance(stream_exc, ProviderAdapterError) and "timed out" in str(stream_exc).lower()
                if is_retryable and _stream_attempt < _max_stream_retries:
                    logger.warning(
                        "Provider stream timed out (attempt %d/%d), retrying: %s",
                        _stream_attempt + 1, _max_stream_retries + 1, stream_exc,
                    )
                    self._append_provider_trace(
                        task=task,
                        event_type="provider.stream.retry",
                        payload={"attempt": _stream_attempt + 1, "error": str(stream_exc)},
                    )
                    continue
                raise

        if final_response is None:
            raise RuntimeError("Provider stream ended without a final response.")

        assistant_message = final_response.get("message", {})
        if not isinstance(assistant_message, dict):
            raise RuntimeError("Provider stream returned invalid final response.")
        response = {
            "message": assistant_message.get("content", ""),
            "assistant_message": assistant_message,
            "tool_calls": assistant_message.get("tool_calls") or [],
            "finish_reason": final_response.get("finish_reason"),
            "raw": final_response.get("raw", {}),
            "prompt": goal,
            "context": provider_context,
            "_streamed_content": streamed_content,
        }
        if not response["tool_calls"]:
            # Use streamed text when the final event's content is empty but
            # tokens were already sent to the frontend via assistant.token.
            final_text = response["message"]
            if not final_text.strip() and streamed_content and _stream_text_parts:
                final_text = "".join(_stream_text_parts)
            response["final"] = final_text
            response["final_answer"] = final_text
        self._consume_budget_from_provider_response(
            session_id=session_id,
            task=task,
            budget=budget,
            response=response,
        )
        self._append_provider_trace(
            task=task,
            event_type="provider.response",
            payload={**self._provider_response_trace(response), "stream": True},
        )
        return response

    def _should_stream_provider(self, provider_context: dict[str, Any]) -> bool:
        if self._streaming_mode_cache is not None:
            return self._streaming_mode_cache
        if not hasattr(self._provider, "stream"):
            self._streaming_mode_cache = False
            return False
        config = provider_context.get("config") or {}
        provider_config = config.get("provider") if isinstance(config, dict) else {}
        if not isinstance(provider_config, dict):
            self._streaming_mode_cache = False
            return False
        mode = str(provider_config.get("mode") or provider_config.get("providerMode") or "").strip().lower()
        result = mode in {"openai", "openai-compatible", "openai_compatible", "openai-compatible-chat"}
        self._streaming_mode_cache = result
        return result

    @staticmethod
    def _detect_stream_repetition(parts: list[str], min_chunk: int = 40, max_occurrences: int = 3) -> bool:
        """Return True when accumulated stream parts show clear repetition loops.

        Scans the full concatenated text for any substring of at least
        *min_chunk* characters that appears *max_occurrences* or more times.
        """
        if len(parts) < max_occurrences:
            return False
        text = "".join(parts)
        length = len(text)
        if length < min_chunk * max_occurrences:
            return False
        # Only check the last portion to keep it O(n) — the repetition
        # pattern, if present, will show up near the tail.
        tail = text[-min(length, 4000):]
        seen: dict[str, int] = {}
        chunk_len = min_chunk
        step = max(chunk_len // 2, 20)
        for start in range(0, len(tail) - chunk_len + 1, step):
            chunk = tail[start : start + chunk_len]
            if not chunk.strip():
                continue
            count = seen.get(chunk, 0) + 1
            seen[chunk] = count
            if count >= max_occurrences:
                return True
        return False

    def _append_provider_trace(self, *, task: dict[str, Any], event_type: str, payload: dict[str, Any]) -> None:
        if not hasattr(self._store, "append_trace_event"):
            return
        self._store.append_trace_event(
            task_id=task["id"],
            session_id=task["sessionId"],
            event_type=event_type,
            source="provider",
            related_id=payload.get("model"),
            payload=payload,
        )

    def _provider_trace_payload(self, provider_context: dict[str, Any]) -> dict[str, Any]:
        config = provider_context.get("config") or {}
        provider_config = config.get("provider") if isinstance(config, dict) else {}
        if not isinstance(provider_config, dict):
            provider_config = {}
        return {
            "mode": provider_config.get("mode") or provider_config.get("providerMode"),
            "model": provider_config.get("model") or provider_config.get("defaultModel"),
            "baseUrl": provider_config.get("baseUrl") or provider_config.get("base_url"),
            "messageCount": len(provider_context.get("messages") or []),
            "toolCount": len(provider_context.get("openai_tools") or provider_context.get("tools") or []),
            "step": provider_context.get("step"),
        }

    def _provider_response_trace(self, response: Any) -> dict[str, Any]:
        if not isinstance(response, dict):
            return {"valid": False, "type": type(response).__name__}
        raw = response.get("raw") if isinstance(response.get("raw"), dict) else {}
        return {
            "valid": True,
            "finishReason": response.get("finish_reason"),
            "model": raw.get("model"),
            "usage": raw.get("usage"),
            "toolCallCount": len(response.get("tool_calls") or []),
            "hasFinal": any(isinstance(response.get(key), str) and bool(response.get(key)) for key in ("final", "final_answer", "answer")),
        }

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
            if runtime_task["status"] == "waiting_approval":
                state["pending_tool_spec"] = pending_spec
                self._save_pending_react_state(task["id"], state)
                return runtime_task

            state["tool_results"].append(tool_result)
            state["messages"].append(self._tool_result_message(state["pending_tool_call"], tool_result))
            self._advance_after_tool(session_id=task["sessionId"], task=runtime_task, tool_spec=pending_spec)

            for index, tool_call in enumerate(list(state.get("remaining_tool_calls", []))):
                tool_spec = self._provider_tool_call_to_spec(tool_call, state["context"])
                tool_result = self._execute_tool(
                    session_id=task["sessionId"],
                    task=runtime_task,
                    tool_spec=tool_spec,
                    budget=None,
                )
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
            return runtime_task
        except Exception as exc:  # noqa: BLE001
            logger.error("Resume after approval failed for task=%s: %s", task["id"], exc, exc_info=True)
            return self._fail_task(
                session_id=task["sessionId"],
                task=runtime_task,
                summary=str(exc),
                error_code="LOOP_EXECUTION_FAILED",
            )

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
            return

        self._collaboration_service.update_collaboration_task(
            {
                "taskId": child_task["id"],
                "status": "blocked",
                "result": result_payload,
            }
        )

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

    def _parse_provider_response(
        self,
        response: Any,
        *,
        allow_fallback: bool,
        allow_plain_message_final: bool,
    ) -> dict[str, Any]:
        if not isinstance(response, dict):
            raise RuntimeError("Provider returned invalid output: expected an object.")

        tool_calls = response.get("tool_calls")
        if tool_calls is not None:
            if not isinstance(tool_calls, list):
                raise RuntimeError("Provider returned invalid tool_calls: expected a list.")
            if tool_calls:
                return {
                    "status": "tool_calls",
                    "message": self._assistant_text(response),
                    "tool_calls": tool_calls,
                }

        final_answer = self._final_answer(response, allow_plain_message=allow_plain_message_final)
        if final_answer is not None:
            return {"status": "completed", "summary": final_answer}

        if allow_fallback and self._has_deterministic_fallback():
            return {"status": "fallback"}

        raise RuntimeError("Provider returned no final answer or tool calls.")

    def _assistant_text(self, response: dict[str, Any]) -> str:
        for key in ("message", "content", "text"):
            value = response.get(key)
            if isinstance(value, str) and value.strip():
                return value
        return ""

    def _final_answer(self, response: dict[str, Any], *, allow_plain_message: bool) -> str | None:
        for key in ("final", "final_answer", "answer"):
            value = response.get(key)
            if isinstance(value, str) and value.strip():
                return value
        if response.get("type") in {"final", "final_answer"}:
            text = self._assistant_text(response)
            if text:
                return text
        if allow_plain_message:
            text = self._assistant_text(response)
            if text:
                return text
        return None

    def _has_deterministic_fallback(self) -> bool:
        return hasattr(self._provider, "choose_tool_sequence") and hasattr(self._provider, "summarize_findings")

    def _parse_turn_result(
        self,
        response: Any,
        *,
        allow_fallback: bool,
        allow_plain_message_final: bool,
    ) -> ProviderTurnResult:
        """Parse a provider response into a structured :class:`ProviderTurnResult`.

        This is the structured companion to ``_parse_provider_response``.  It
        extracts the same information plus an explicit :class:`TurnDecision`,
        ``thought_summary``, ``why_complete``, and ``remaining_risks``.
        """
        if not isinstance(response, dict):
            return ProviderTurnResult(
                decision=TurnDecision.FAILED,
                thought_summary="Provider returned non-dict output",
                message="",
                raw_response=response if isinstance(response, dict) else None,
            )

        tool_calls = response.get("tool_calls")
        message = self._assistant_text(response)
        thought_summary = response.get("thought_summary") or response.get("thoughtSummary") or message[:200]
        why_complete = response.get("why_complete") or response.get("whyComplete")
        remaining_risks = response.get("remaining_risks") or response.get("remainingRisks") or []
        policy_needs = response.get("policy_needs") or response.get("policyNeeds")

        # Check for explicit decision field from structured provider output
        explicit_decision = response.get("decision") or response.get("turn_decision")
        decision: TurnDecision | None = None
        if explicit_decision and isinstance(explicit_decision, str):
            try:
                decision = TurnDecision(explicit_decision)
            except ValueError:
                logger.debug("Unknown turn decision %r, inferring from response", explicit_decision)

        # Infer decision from response shape if no explicit value
        if decision is None:
            if isinstance(tool_calls, list) and tool_calls:
                decision = TurnDecision.CONTINUE_WITH_TOOLS
            else:
                final_answer = self._final_answer(response, allow_plain_message=allow_plain_message_final)
                if final_answer is not None:
                    decision = TurnDecision.FINAL_ANSWER
                elif allow_fallback and self._has_deterministic_fallback():
                    decision = TurnDecision.FAILED
                else:
                    # No tool calls, no final answer — treat as failed
                    decision = TurnDecision.FAILED

        final_answer: str | None = None
        if decision == TurnDecision.FINAL_ANSWER:
            final_answer = self._final_answer(response, allow_plain_message=allow_plain_message_final) or message

        return ProviderTurnResult(
            decision=decision,
            thought_summary=thought_summary,
            message=message,
            tool_calls=tool_calls if isinstance(tool_calls, list) else [],
            final_answer=final_answer,
            why_complete=why_complete,
            remaining_risks=remaining_risks if isinstance(remaining_risks, list) else [],
            policy_needs=policy_needs,
            raw_response=response,
        )

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

    def _provider_tool_call_to_spec(self, tool_call: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(tool_call, dict):
            raise RuntimeError("Provider returned invalid tool call: expected an object.")

        function_payload = tool_call.get("function") if isinstance(tool_call.get("function"), dict) else {}
        tool_name = tool_call.get("name") or function_payload.get("name")
        if not isinstance(tool_name, str) or not tool_name.strip():
            raise RuntimeError("Provider returned a tool call without a tool name.")

        raw_arguments = tool_call.get("arguments", function_payload.get("arguments", {}))
        if isinstance(raw_arguments, str):
            try:
                arguments = json.loads(raw_arguments or "{}")
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"Provider returned invalid JSON arguments for {tool_name}.") from exc
        elif isinstance(raw_arguments, dict):
            arguments = deepcopy(raw_arguments)
        else:
            raise RuntimeError(f"Provider returned invalid arguments for {tool_name}.")

        self._fill_tool_defaults(tool_name, arguments, context)
        return {
            "id": tool_call.get("id"),
            "name": tool_name,
            "arguments": arguments,
            "plan_step_id": self._plan_step_for_tool(tool_name),
            "start_token": f"Running tool: {tool_name}",
        }

    def _fill_tool_defaults(self, tool_name: str, arguments: dict[str, Any], context: dict[str, Any]) -> None:
        workspace_tools = {
            "list_dir",
            "search_files",
            "read_file",
            "run_command",
            "apply_patch",
            "git_status",
            "git_diff",
            "write_file",
            "code_search",
        }
        if tool_name in workspace_tools and "workspaceRoot" not in arguments and "workspace_root" not in arguments:
            arguments["workspaceRoot"] = context["workspace_root"]

        search_config = context.get("search_config", {})
        if tool_name == "list_dir":
            arguments.setdefault("path", ".")
            arguments.setdefault("recursive", False)
            arguments.setdefault("max_depth", 2)
            arguments.setdefault("ignore", search_config.get("ignore", []))
        elif tool_name == "search_files":
            arguments.setdefault("mode", context.get("search_mode", "content"))
            arguments.setdefault("glob", search_config.get("glob", []))
            arguments.setdefault("ignore", search_config.get("ignore", []))
            arguments.setdefault("max_results", 8)
        elif tool_name == "read_file":
            arguments.setdefault("max_bytes", 4000)
            arguments.setdefault("ignore", search_config.get("ignore", []))
        elif tool_name == "run_command":
            arguments.setdefault("cwd", ".")
        elif tool_name == "apply_patch":
            arguments.setdefault("dry_run", False)
        elif tool_name == "task":
            arguments.setdefault("priority", 3)

    def _plan_step_for_tool(self, tool_name: str) -> str:
        return {
            "list_dir": "inspect-workspace",
            "search_files": "search-relevant-files",
            "read_file": "search-relevant-files",
            "task": "task",
            "run_command": "run-command",
            "apply_patch": "apply-patch",
            "git_status": "git-status",
            "git_diff": "git-diff",
        }.get(tool_name, tool_name.replace("_", "-"))

    def _read_file_cache_key(self, tool_spec: dict[str, Any]) -> str | None:
        if tool_spec.get("name") != "read_file":
            return None
        arguments = tool_spec.get("arguments")
        if not isinstance(arguments, dict):
            return None
        relevant_arguments = {
            key: arguments.get(key)
            for key in ("workspaceRoot", "workspace_root", "path", "encoding", "max_bytes")
            if key in arguments
        }
        return json.dumps(relevant_arguments, sort_keys=True, ensure_ascii=False, default=str)

    def _clone_cached_tool_result(self, tool_spec: dict[str, Any], cached_tool_result: dict[str, Any]) -> dict[str, Any]:
        tool_result = deepcopy(cached_tool_result)
        tool_result["id"] = tool_spec.get("id") or self._store.new_id("tc")
        tool_result["arguments"] = deepcopy(tool_spec.get("arguments", {}))
        result = tool_result.get("result")
        if isinstance(result, dict):
            result["cached"] = True
        return tool_result

    def _invalidates_read_file_cache(self, tool_name: str) -> bool:
        return tool_name in {"apply_patch", "write_file", "run_command", "task"}

    def _ensure_tool_allowed_for_child_worker(self, tool_name: str) -> None:
        allowed = self._child_tool_allowlist()
        if allowed is None or tool_name in set(allowed):
            return
        raise ValueError(f"Tool is not allowed in child worker process: {tool_name}")

    def _ensure_command_safe_for_child_worker(self, command: str) -> None:
        """Block git commit/push in child workers — only root may commit."""
        allowed = self._child_tool_allowlist()
        if allowed is None:
            return  # root task, no restriction
        import re as _re
        blocked = (
            _re.compile(r"\bgit\s+commit\b", _re.IGNORECASE),
            _re.compile(r"\bgit\s+push\b", _re.IGNORECASE),
        )
        for pattern in blocked:
            if pattern.search(command):
                raise ValueError(
                    "Child workers cannot run git commit/push directly. "
                    "Only the root task may commit changes."
                )

    def _tool_result_message(self, tool_call: dict[str, Any], tool_result: dict[str, Any]) -> dict[str, Any]:
        return {
            "role": "tool",
            "tool_call_id": tool_call.get("id") or tool_result["id"],
            "name": tool_result["name"],
            "content": json.dumps(tool_result["result"], ensure_ascii=False),
        }

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

    def _execute_tool(
        self,
        session_id: str,
        task: dict[str, Any],
        tool_spec: dict[str, Any],
        budget: WorkerBudget | None = None,
    ) -> dict[str, Any]:
        self._ensure_tool_allowed_for_child_worker(tool_spec["name"])
        # Child workers cannot run git commit/push via run_command
        if tool_spec["name"] == "run_command":
            command = tool_spec.get("arguments", {}).get("command", "")
            if isinstance(command, str):
                self._ensure_command_safe_for_child_worker(command)
        tool_call_id = tool_spec.get("id") or self._store.new_id("tc")
        tool_arguments = {
            **tool_spec["arguments"],
            "taskId": task["id"],
            "sessionId": session_id,
        }
        self._consume_budget_for_tool_call(
            session_id=session_id,
            task=task,
            budget=budget,
            tool_name=tool_spec["name"],
        )
        self._publish(
            session_id=session_id,
            task=task,
            event_type="assistant.token",
            payload={"delta": tool_spec["start_token"]},
        )
        self._publish(
            session_id=session_id,
            task=task,
            event_type="tool.started",
            payload={
                "toolCallId": tool_call_id,
                "toolName": tool_spec["name"],
                "arguments": tool_arguments,
            },
        )
        self._fire_hooks("before_tool_call", session_id, task, extra_context={"toolCallId": tool_call_id, "toolName": tool_spec["name"]})
        if tool_spec["name"] == "apply_patch":
            self._fire_hooks("before_patch_apply", session_id, task, extra_context={"toolCallId": tool_call_id, "patchArguments": tool_spec.get("arguments", {})})
        # MCP-specific lifecycle event
        is_mcp_tool = tool_spec["name"].startswith("mcp__")
        if is_mcp_tool:
            parts = tool_spec["name"].split("__", 2)
            mcp_server_id = parts[1] if len(parts) >= 2 else ""
            self._publish_mcp_event("mcp.tool.started", {
                "toolCallId": tool_call_id,
                "toolName": tool_spec["name"],
                "serverId": mcp_server_id,
            })
        tool_span = self._tracer.start_span(
            "tool_call",
            trace_id=getattr(self, "_active_trace_id", None),
            parent_span_id=getattr(self, "_active_parent_span_id", None),
            attributes={"toolName": tool_spec["name"]},
        )
        try:
            if tool_spec["name"] == "task":
                self._fire_hooks("before_subagent_start", session_id, task, extra_context={"toolArguments": tool_arguments})
                try:
                    result = self._subagent_service.dispatch(tool_arguments)
                    sub_status = result.get("status", "")
                    if sub_status == "failed":
                        self._fire_hooks("on_subagent_failed", session_id, task, extra_context={"subagentResult": result})
                    else:
                        self._fire_hooks("after_subagent_complete", session_id, task, extra_context={"subagentResult": result})
                except Exception as sub_exc:
                    self._fire_hooks("on_subagent_failed", session_id, task, extra_context={"subagentError": str(sub_exc)})
                    raise
            else:
                result = self._tool_registry.execute(tool_spec["name"], tool_arguments, session_id=session_id)
            self._tracer.end_span(tool_span.span_id, status="ok")
        except Exception as exc:  # noqa: BLE001
            import asyncio as _asyncio
            is_timeout = isinstance(exc, _asyncio.TimeoutError)
            result = {
                "status": "failed",
                "ok": False,
                "error": str(exc),
                "summary": f"Tool {tool_spec['name']} raised an exception: {exc}",
            }
            if is_timeout:
                result["timeout"] = True
            self._tracer.end_span(tool_span.span_id, status="error")
            if is_mcp_tool:
                event_name = "mcp.tool.timeout" if is_timeout else "mcp.tool.failed"
                self._publish_mcp_event(event_name, {
                    "toolCallId": tool_call_id,
                    "toolName": tool_spec["name"],
                    "serverId": mcp_server_id,
                    "error": str(exc),
                    "timeout": is_timeout,
                })
        if tool_spec["name"] == "run_command":
            command_log = result.get("commandLog") or {}
            command_id = command_log.get("id")
            if command_id and result.get("stdout"):
                self._publish(
                    session_id=session_id,
                    task=task,
                    event_type="command.output",
                    payload={
                        "commandId": command_id,
                        "stream": "stdout",
                        "chunk": result["stdout"],
                    },
                )
            if command_id and result.get("stderr"):
                self._publish(
                    session_id=session_id,
                    task=task,
                    event_type="command.output",
                    payload={
                        "commandId": command_id,
                        "stream": "stderr",
                        "chunk": result["stderr"],
                    },
                )

        if tool_spec["name"] == "task":
            tool_result = {
                "id": tool_call_id,
                "name": tool_spec["name"],
                "arguments": tool_arguments,
                "result": result,
            }
            self._publish(
                session_id=session_id,
                task=task,
                event_type="tool.completed",
                payload={
                    "toolCallId": tool_call_id,
                    "toolName": tool_spec["name"],
                    "arguments": tool_arguments,
                    "result": result,
                },
            )
            return tool_result

        if result.get("status") == "approval_required":
            approval = result.get("approval", {})
            self._validate_task_transition(task["status"], "waiting_approval", task["id"])
            task["status"] = "waiting_approval"
            self._store.update_task(task_id=task["id"], status="waiting_approval", plan=task["plan"])
            if tool_spec["name"] == "apply_patch":
                patch = result.get("patch", {})
                self._publish(
                    session_id=session_id,
                    task=task,
                    event_type="patch.proposed",
                    payload={
                        "patchId": patch.get("id"),
                        "summary": patch.get("summary", ""),
                        "filesChanged": patch.get("filesChanged", 0),
                    },
                )
            self._publish(
                session_id=session_id,
                task=task,
                event_type="approval.requested",
                payload={
                    "approvalId": approval.get("id"),
                    "taskId": task["id"],
                    "kind": approval.get("kind", tool_spec["name"]),
                    "request": json.loads(approval.get("requestJson", "{}")),
                    "patchId": result.get("patch", {}).get("id"),
                },
            )
            self._fire_hooks("on_approval_required", session_id, task, extra_context={"approvalId": approval.get("id"), "kind": approval.get("kind", tool_spec["name"])})
            self._publish(
                session_id=session_id,
                task=task,
                event_type="task.waiting_approval",
                payload={
                    "status": "waiting_approval",
                    "detail": "执行前需要先审批补丁。"
                    if tool_spec["name"] == "apply_patch"
                    else "执行前需要先审批命令。",
                },
            )
            tool_result = {
                "id": tool_call_id,
                "name": tool_spec["name"],
                "arguments": tool_arguments,
                "result": result,
            }
            self._publish(
                session_id=session_id,
                task=task,
                event_type="tool.completed",
                payload={
                    "toolCallId": tool_call_id,
                    "toolName": tool_spec["name"],
                    "arguments": tool_arguments,
                    "result": result,
                },
            )
            return tool_result

        if self._is_patch_validation_failure(tool_spec["name"], result):
            tool_result = {
                "id": tool_call_id,
                "name": tool_spec["name"],
                "arguments": tool_arguments,
                "result": result,
            }
            self._publish(
                session_id=session_id,
                task=task,
                event_type="tool.failed",
                payload={
                    "toolCallId": tool_call_id,
                    "toolName": tool_spec["name"],
                    "arguments": tool_arguments,
                    "result": result,
                },
            )
            return tool_result

        if self._tool_failed(tool_spec["name"], result):
            tool_result = {
                "id": tool_call_id,
                "name": tool_spec["name"],
                "arguments": tool_arguments,
                "result": result,
            }
            self._record_task_run_tool_result(session_id=session_id, task=task, tool_result=tool_result)
            self._publish(
                session_id=session_id,
                task=task,
                event_type="tool.failed",
                payload={
                    "toolCallId": tool_call_id,
                    "toolName": tool_spec["name"],
                    "arguments": tool_arguments,
                    "result": result,
                },
            )
            self._fire_hooks("after_tool_call", session_id, task, extra_context={"toolCallId": tool_call_id, "toolName": tool_spec["name"], "toolStatus": "failed"})
            if is_mcp_tool:
                is_timeout = bool(result.get("timeout"))
                event_name = "mcp.tool.timeout" if is_timeout else "mcp.tool.failed"
                self._publish_mcp_event(event_name, {
                    "toolCallId": tool_call_id,
                    "toolName": tool_spec["name"],
                    "serverId": mcp_server_id,
                    "error": result.get("error", f"Tool returned status: {result.get('status')}"),
                    "timeout": is_timeout,
                })
            return tool_result

        if tool_spec["name"] == "run_command":
            tool_result = {
                "id": tool_call_id,
                "name": tool_spec["name"],
                "arguments": tool_arguments,
                "result": result,
            }
            self._record_task_run_tool_result(session_id=session_id, task=task, tool_result=tool_result)
            self._publish(
                session_id=session_id,
                task=task,
                event_type="tool.completed",
                payload={
                    "toolCallId": tool_call_id,
                    "toolName": tool_spec["name"],
                    "arguments": tool_arguments,
                    "result": result,
                },
            )
            self._fire_hooks("after_tool_call", session_id, task, extra_context={"toolCallId": tool_call_id, "toolName": tool_spec["name"], "toolStatus": "completed"})
            return tool_result

        if tool_spec["name"] == "apply_patch":
            tool_result = {
                "id": tool_call_id,
                "name": tool_spec["name"],
                "arguments": tool_arguments,
                "result": result,
            }
            self._record_task_run_tool_result(session_id=session_id, task=task, tool_result=tool_result)
            self._publish(
                session_id=session_id,
                task=task,
                event_type="tool.completed",
                payload={
                    "toolCallId": tool_call_id,
                    "toolName": tool_spec["name"],
                    "arguments": tool_arguments,
                    "result": result,
                },
            )
            self._fire_hooks("after_tool_call", session_id, task, extra_context={"toolCallId": tool_call_id, "toolName": tool_spec["name"], "toolStatus": "completed"})
            self._fire_hooks("after_patch_apply", session_id, task, extra_context={"toolCallId": tool_call_id, "patchResult": result})
            return tool_result

        tool_result = {
            "id": tool_call_id,
            "name": tool_spec["name"],
            "arguments": tool_arguments,
            "result": result,
        }
        self._publish(
            session_id=session_id,
            task=task,
            event_type="tool.completed",
            payload={
                "toolCallId": tool_call_id,
                "toolName": tool_spec["name"],
                "arguments": tool_arguments,
                "result": result,
            },
        )
        if is_mcp_tool:
            self._publish_mcp_event("mcp.tool.completed", {
                "toolCallId": tool_call_id,
                "toolName": tool_spec["name"],
                "serverId": mcp_server_id,
                "ok": result.get("ok", True),
            })
        self._fire_hooks("after_tool_call", session_id, task, extra_context={"toolCallId": tool_call_id, "toolName": tool_spec["name"], "toolStatus": "completed"})
        if tool_spec["name"] == "memory.remember" and result.get("ok", True):
            self._fire_hooks("on_memory_write", session_id, task, extra_context={"toolCallId": tool_call_id, "memoryResult": result})
        return tool_result

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

    def _consume_budget_for_tool_call(
        self,
        *,
        session_id: str,
        task: dict[str, Any],
        budget: WorkerBudget | None,
        tool_name: str,
    ) -> None:
        if budget is None:
            return
        consumed = budget.consume_tool_call()
        self._publish(
            session_id=session_id,
            task=task,
            event_type="collab.worker.budget.updated",
            payload={
                "dimension": "toolCalls",
                "consumed": consumed,
                "toolName": tool_name,
                "budget": budget.to_metadata(),
            },
        )

    def _next_step_id(self, tool_sequence: list[dict[str, Any]], current_index: int) -> str | None:
        if current_index + 1 >= len(tool_sequence):
            return "summarize-findings"
        return tool_sequence[current_index + 1]["plan_step_id"]
