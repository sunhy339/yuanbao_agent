"""Provider Turn Mixin 鈥?extracted from ReactRunnerMixin.

Handles provider request/response, streaming, trace helpers,
response parsing, and turn result extraction.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from ..context.token_budget import estimate_tokens
from ..execution.tool_pipeline import _tool_category, _tool_phase_metadata, _tool_semantic_parent_metadata
from ..provider.failure_recovery import classify_provider_failure
from ..react.types import ProviderTurnResult, TurnDecision

logger = logging.getLogger(__name__)


class ProviderTurnMixin:
    """Mixin providing provider turn handling, streaming, and response parsing."""

    def _raise_if_provider_task_cancelled(self, task: dict[str, Any]) -> None:
        checker = getattr(self, "_task_is_cancelled", None)
        if callable(checker) and checker(task):
            raise RuntimeError("Task was cancelled.")

    @staticmethod
    def _provider_stream_tool_metadata(tool_name: Any, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        if not isinstance(tool_name, str) or not tool_name.strip():
            return {}
        tool_arguments = arguments if isinstance(arguments, dict) else {}
        category = _tool_category(tool_name.strip(), tool_arguments)
        phase_metadata = _tool_phase_metadata(category)
        return {
            "toolCategory": category,
            **phase_metadata,
            **_tool_semantic_parent_metadata(phase_metadata),
        }

    def _provider_preflight_decision(
        self,
        *,
        session_id: str,
        task: dict[str, Any],
        goal: str,
        provider_context: dict[str, Any],
        token_estimate: int,
        compaction_threshold: int,
    ) -> dict[str, Any]:
        facts = self._provider_preflight_facts(
            provider_context=provider_context,
            token_estimate=token_estimate,
            compaction_threshold=compaction_threshold,
        )
        runtime_action = self._provider_preflight_runtime_action(
            facts=facts,
        )
        result_context = dict(provider_context)
        result_messages = provider_context.get("messages")
        original_message_count = len(result_messages) if isinstance(result_messages, list) else None
        original_tokens = token_estimate
        applied = False
        if runtime_action == "compact_context" and isinstance(result_messages, list):
            compacted_messages = self._provider_preflight_compact_messages(result_messages)
            result_context["messages"] = compacted_messages
            result_context["_provider_preflight_runtime_action"] = runtime_action
            result_context["_provider_preflight"] = {
                "runtimeAction": runtime_action,
                "reason": self._provider_preflight_reason(facts=facts),
                "originalMessageCount": original_message_count,
                "messageCount": len(compacted_messages),
                "originalTokenEstimate": original_tokens,
                "tokenEstimate": sum(estimate_tokens(message.get("content", "")) for message in compacted_messages),
            }
            result_messages = compacted_messages
            token_estimate = int(result_context["_provider_preflight"]["tokenEstimate"])
            applied = True
        else:
            result_context["_provider_preflight_runtime_action"] = runtime_action
            result_context["_provider_preflight"] = {
                "runtimeAction": runtime_action,
                "reason": self._provider_preflight_reason(facts=facts),
                "originalMessageCount": original_message_count,
                "messageCount": original_message_count,
                "originalTokenEstimate": original_tokens,
                "tokenEstimate": token_estimate,
            }
        facts_after = dict(facts)
        facts_after.update({
            "messageCountAfter": len(result_messages) if isinstance(result_messages, list) else None,
            "estimatedInputTokensAfter": token_estimate,
        })
        decision = {
            "facts": facts_after,
            "runtimeAction": runtime_action,
            "runtimeApplied": applied,
            "providerPreflight": result_context.get("_provider_preflight"),
        }
        return {
            "provider_context": result_context,
            "messages": result_messages,
            "token_estimate": token_estimate,
            "decision": decision,
        }

    def _provider_preflight_facts(
        self,
        *,
        provider_context: dict[str, Any],
        token_estimate: int,
        compaction_threshold: int,
    ) -> dict[str, Any]:
        messages = provider_context.get("messages")
        tools = provider_context.get("openai_tools") or provider_context.get("tools") or []
        budget_stats = provider_context.get("budgetStats") if isinstance(provider_context, dict) else None
        max_context_tokens = None
        if isinstance(budget_stats, dict):
            max_context_tokens = budget_stats.get("maxContextTokens")
        if max_context_tokens is None:
            config = provider_context.get("config") if isinstance(provider_context, dict) else None
            provider_config = config.get("provider") if isinstance(config, dict) else None
            if isinstance(provider_config, dict):
                max_context_tokens = provider_config.get("maxContextTokens")
            if max_context_tokens is None and isinstance(config, dict):
                max_context_tokens = config.get("maxContextTokens")
        try:
            max_context = int(max_context_tokens) if max_context_tokens is not None else None
        except (TypeError, ValueError):
            max_context = None
        threshold = max(1, int(compaction_threshold or max_context or 1))
        risk_ratio_base = max_context if isinstance(max_context, int) and max_context > 0 else threshold
        cache_policy = self._provider_prompt_cache_policy(provider_context)
        near_ratio = self._provider_preflight_near_context_ratio(provider_context)
        token_ratio = float(token_estimate) / float(max(1, risk_ratio_base))
        threshold_ratio = float(token_estimate) / float(threshold)
        near_context_limit = token_ratio >= near_ratio or threshold_ratio >= near_ratio
        over_context_limit = token_estimate >= threshold or (
            isinstance(max_context, int) and max_context > 0 and token_estimate >= max_context
        )
        prior_failure = self._provider_preflight_recent_failure(provider_context)
        if over_context_limit or prior_failure.get("hasPriorProviderFailure"):
            risk_level = "high"
        elif near_context_limit:
            risk_level = "medium"
        else:
            risk_level = "low"
        return {
            **self._provider_trace_payload(provider_context),
            "messageCount": len(messages) if isinstance(messages, list) else None,
            "toolCount": len(tools) if isinstance(tools, list) else None,
            "estimatedInputTokens": int(token_estimate),
            "maxContextTokens": max_context,
            "compactionThreshold": threshold,
            "nearContextRatio": near_ratio,
            "nearContextLimit": near_context_limit,
            "overContextLimit": over_context_limit,
            "promptCachePolicy": cache_policy,
            "riskLevel": risk_level,
            "streamingEnabled": self._should_stream_provider(provider_context),
            "step": provider_context.get("step"),
            "maxSteps": provider_context.get("max_steps"),
            "childWorker": provider_context.get("_child_worker") is True,
            **prior_failure,
        }

    def _provider_preflight_recent_failure(self, provider_context: dict[str, Any]) -> dict[str, Any]:
        tool_results = provider_context.get("tool_results")
        if not isinstance(tool_results, list):
            return {"hasPriorProviderFailure": False}
        for item in reversed(tool_results[-5:]):
            if not isinstance(item, dict):
                continue
            result = item.get("result")
            if not isinstance(result, dict):
                continue
            recovery = result.get("failureRecovery") or result.get("failure_recovery")
            if isinstance(recovery, dict):
                return {
                    "hasPriorProviderFailure": True,
                    "priorProviderFailure": {
                        "category": recovery.get("category"),
                        "strategy": recovery.get("strategy"),
                        "recoverable": recovery.get("recoverable"),
                    },
                }
        return {"hasPriorProviderFailure": False}

    def _provider_prompt_cache_policy(self, provider_context: dict[str, Any]) -> dict[str, Any]:
        config = provider_context.get("config") if isinstance(provider_context, dict) else None
        provider_config = config.get("provider") if isinstance(config, dict) else None
        prompt_cache = provider_config.get("promptCache") if isinstance(provider_config, dict) else None
        if isinstance(prompt_cache, dict):
            return prompt_cache
        budget_stats = provider_context.get("budgetStats") if isinstance(provider_context, dict) else None
        prompt_cache = budget_stats.get("promptCache") if isinstance(budget_stats, dict) else None
        return prompt_cache if isinstance(prompt_cache, dict) else {}

    def _provider_preflight_near_context_ratio(self, provider_context: dict[str, Any]) -> float:
        cache_policy = self._provider_prompt_cache_policy(provider_context)
        for key in ("nearContextRatio", "compactionNearRatio"):
            if key not in cache_policy:
                continue
            try:
                return min(0.98, max(0.5, float(cache_policy[key])))
            except (TypeError, ValueError):
                continue
        return 0.92

    @staticmethod
    def _provider_preflight_reason(*, facts: dict[str, Any]) -> str:
        if facts.get("overContextLimit"):
            return "Runtime detected provider context at or above the configured context limit."
        if facts.get("nearContextLimit"):
            return "Runtime detected provider context near the configured context limit."
        if facts.get("hasPriorProviderFailure"):
            return "Runtime detected a prior provider failure in recent tool results."
        return "Provider request is within preflight runtime bounds."

    def _provider_preflight_runtime_action(self, *, facts: dict[str, Any]) -> str:
        if facts.get("overContextLimit"):
            return "compact_context"
        return "proceed"

    def _provider_preflight_compact_messages(self, messages: list[Any]) -> list[Any]:
        system_messages: list[dict[str, Any]] = []
        conversation_messages: list[dict[str, Any]] = []
        for item in messages:
            if not isinstance(item, dict):
                continue
            normalized = self._provider_recovery_trim_message(item)
            role = str(normalized.get("role") or "").strip().lower()
            if role in {"system", "developer"} and len(system_messages) < 2:
                system_messages.append(normalized)
            else:
                conversation_messages.append(normalized)
        notice = {
            "role": "system",
            "content": (
                "Provider preflight compacted the request context before the model call. "
                "Continue from the recent context below; ask for missing details only if required."
            ),
        }
        tail = conversation_messages[-8:]
        return [*system_messages, notice, *tail]

    def _record_provider_preflight_decision(
        self,
        *,
        session_id: str,
        task: dict[str, Any],
        provider_context: dict[str, Any],
        provider_turn_id: str | None,
        decision: dict[str, Any] | None,
    ) -> None:
        if not isinstance(decision, dict):
            return
        facts = decision.get("facts") if isinstance(decision.get("facts"), dict) else {}
        runtime_action = str(decision.get("runtimeAction") or "proceed")
        runtime_applied = bool(decision.get("runtimeApplied"))
        should_record_trace = (
            runtime_action != "proceed"
            or runtime_applied
        )
        if not should_record_trace:
            return
        payload: dict[str, Any] = {
            "providerTurnId": provider_turn_id,
            "facts": facts,
            "runtimeAction": runtime_action,
            "runtimeApplied": runtime_applied,
            "providerPreflight": decision.get("providerPreflight"),
        }
        self._append_provider_trace(
            task=task,
            event_type="provider.preflight.decision",
            payload=payload,
        )
        if hasattr(self, "_publish"):
            self._publish(
                session_id=session_id,
                task=task,
                event_type="agent.decision.provider_preflight",
                payload=payload,
                visibility="trace",
            )
            preflight = decision.get("providerPreflight")
            if (
                payload["runtimeAction"] == "compact_context"
                and payload["runtimeApplied"]
                and isinstance(preflight, dict)
            ):
                self._publish_provider_compact_summary(
                    session_id=session_id,
                    task=task,
                    phase="provider_preflight",
                    reason=str(preflight.get("reason") or self._provider_preflight_reason(facts=facts)),
                    original_count=preflight.get("originalMessageCount"),
                    compacted_count=preflight.get("messageCount"),
                    tokens_before=preflight.get("originalTokenEstimate"),
                    tokens_after=preflight.get("tokenEstimate"),
                    strategy="compact_context",
                )
        recorder = getattr(self, "_record_provider_preflight_proposal", None)
        if callable(recorder):
            recorder(
                session_id=session_id,
                task=task,
                provider_turn_id=provider_turn_id,
                preflight=decision,
            )

    def _request_provider_response(
        self,
        *,
        session_id: str,
        task: dict[str, Any],
        goal: str,
        provider_context: dict[str, Any],
        budget: Any | None = None,
    ) -> dict[str, Any]:
        status_publisher = getattr(self, "_publish_chat_status", None)
        if callable(status_publisher):
            status_publisher(
                session_id=session_id,
                task=task,
                state="thinking",
                verb="model",
                payload={
                    "step": provider_context.get("step"),
                },
            )
        if not self._should_stream_provider(provider_context):
            return self._request_non_streaming_provider_response(
                session_id=session_id,
                task=task,
                goal=goal,
                provider_context=provider_context,
                budget=budget,
            )

        self._append_provider_trace(
            task=task,
            event_type="provider.request",
            payload={**self._provider_trace_payload(provider_context), "stream": True},
        )
        if callable(status_publisher):
            status_publisher(
                session_id=session_id,
                task=task,
                state="streaming",
                verb="model",
                payload={
                    "step": provider_context.get("step"),
                },
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
        _content_block_started = False
        _active_tool_streams: dict[int, dict[str, Any]] = {}
        _thinking_parts: list[str] = []
        _thinking_source: str | None = None

        def _flush_streaming_thinking(*, force: bool = False) -> None:
            nonlocal _thinking_source
            if not _thinking_parts:
                return
            if not force and not self._should_flush_streaming_thinking(_thinking_parts):
                return
            text = self._streaming_thinking_text(_thinking_parts)
            _thinking_parts.clear()
            if not text:
                return
            self._publish(
                session_id=session_id,
                task=task,
                event_type="thinking",
                payload={
                    "text": text,
                    "messageId": task.get("activeAssistantMessageId"),
                    "source": _thinking_source or "provider_reasoning_delta",
                    "step": provider_context.get("step"),
                },
            )
            _thinking_source = None

        for _stream_attempt in range(_max_stream_retries + 1):
            final_response = None
            streamed_content = False
            _stream_text_parts = []
            _content_block_started = False
            _active_tool_streams = {}
            _thinking_parts = []
            _thinking_source = None
            try:
                for event in self._provider.stream(goal, provider_context):
                    self._raise_if_provider_task_cancelled(task)
                    event_type = event.get("type")
                    if event_type == "content_delta":
                        delta = event.get("delta")
                        if isinstance(delta, str) and delta:
                            _flush_streaming_thinking(force=True)
                            streamed_content = True
                            _delta_count += 1
                            if _delta_count <= 3 or _delta_count % 50 == 0:
                                logger.debug(
                                    "Stream delta #%d for task=%s: %r",
                                    _delta_count, task["id"], delta[:80],
                                )
                            _stream_text_parts.append(delta)
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
                            if not _content_block_started:
                                self._publish(
                                    session_id=session_id,
                                    task=task,
                                    event_type="content_start",
                                    payload={
                                        "blockType": "text",
                                        "messageId": task.get("activeAssistantMessageId"),
                                    },
                                )
                                _content_block_started = True
                            self._publish(
                                session_id=session_id,
                                task=task,
                                event_type="assistant.token",
                                payload={"delta": delta, "step": provider_context.get("step")},
                            )
                    elif event_type == "final":
                        _flush_streaming_thinking(force=True)
                        response = event.get("response")
                        if isinstance(response, dict):
                            final_response = response
                    elif event_type == "finish_reason":
                        self._append_provider_trace(task=task, event_type="provider.stream.finish", payload=event)
                    elif event_type == "thinking_delta":
                        delta = event.get("delta")
                        if isinstance(delta, str) and delta:
                            self._append_provider_trace(task=task, event_type="provider.stream.thinking_delta", payload=event)
                            _thinking_parts.append(delta)
                            _thinking_source = _thinking_source or self._thinking_source(event.get("source"), streaming=True)
                            _flush_streaming_thinking()
                    elif event_type == "tool_call_delta":
                        _flush_streaming_thinking(force=True)
                        index = event.get("index")
                        if not isinstance(index, int):
                            continue
                        stream_state = _active_tool_streams.setdefault(index, {"toolUseId": None, "toolName": None})
                        tool_use_id = event.get("id")
                        tool_name = event.get("name")
                        parent_tool_use_id = event.get("parentToolUseId")
                        if isinstance(tool_use_id, str) and tool_use_id:
                            stream_state["toolUseId"] = tool_use_id
                        if isinstance(tool_name, str) and tool_name:
                            stream_state["toolName"] = tool_name
                        if isinstance(parent_tool_use_id, str) and parent_tool_use_id:
                            stream_state["parentToolUseId"] = parent_tool_use_id
                        stream_metadata = self._provider_stream_tool_metadata(stream_state.get("toolName"))
                        arguments_delta = event.get("arguments_delta")
                        if isinstance(arguments_delta, str) and arguments_delta:
                            self._append_provider_trace(
                                task=task,
                                event_type="provider.stream.tool_call_delta",
                                payload={
                                    "toolUseId": stream_state.get("toolUseId"),
                                    "toolName": stream_state.get("toolName"),
                                    **({"parentToolUseId": stream_state.get("parentToolUseId")} if stream_state.get("parentToolUseId") else {}),
                                    **stream_metadata,
                                    "argumentsDeltaChars": len(arguments_delta),
                                },
                            )
                logger.info(
                    "Stream completed for task=%s: deltas=%d streamed=%s has_final=%s",
                    task["id"], _delta_count, streamed_content, final_response is not None,
                )
                _flush_streaming_thinking(force=True)
                self._raise_if_provider_task_cancelled(task)
                if _stream_text_parts:
                    _active_msg_id = task.get("activeAssistantMessageId")
                    if _active_msg_id:
                        self._store.update_message(_active_msg_id, content="".join(_stream_text_parts))
                break
            except Exception as stream_exc:
                if self._task_is_cancelled(task):
                    raise
                from ..provider.openai_compatible import ProviderAdapterError
                recovery = classify_provider_failure(stream_exc)
                has_partial_output = bool(streamed_content or _stream_text_parts or final_response)
                recovery_decision = self._provider_failure_recovery_decision(
                    session_id=session_id,
                    task=task,
                    goal=goal,
                    provider_context=provider_context,
                    recovery=recovery,
                    error=stream_exc,
                    stage="stream",
                    recovery_retry=False,
                    has_partial_output=has_partial_output,
                )
                self._record_provider_failure_recovery_decision(
                    session_id=session_id,
                    task=task,
                    provider_context=provider_context,
                    recovery_decision=recovery_decision,
                    error=stream_exc,
                )
                recovery_payload = recovery_decision["failureRecovery"]
                strategy = str(recovery_decision.get("strategy") or "")
                is_retryable = (
                    isinstance(stream_exc, ProviderAdapterError)
                    and self._provider_recovery_should_retry(
                        recovery,
                        strategy=strategy,
                        has_partial_output=has_partial_output,
                    )
                )
                if (
                    strategy == "fallback"
                    and hasattr(self._provider, "generate")
                    and self._can_fallback_to_non_stream(
                        recovery,
                        has_partial_output=has_partial_output,
                    )
                ):
                    logger.warning(
                        "Provider stream failed for task=%s; falling back to non-streaming request: %s",
                        task["id"],
                        stream_exc,
                    )
                    self._append_provider_trace(
                        task=task,
                        event_type="provider.stream.fallback_non_stream",
                        payload={
                            "attempt": _stream_attempt + 1,
                            "error": str(stream_exc)[:500],
                            "failureRecovery": recovery_payload,
                        },
                    )
                    self._publish_provider_api_retry(
                        session_id=session_id,
                        task=task,
                        stage="stream",
                        strategy=strategy,
                        recovery_payload=recovery_payload,
                        error=stream_exc,
                        attempt=_stream_attempt + 1,
                        max_attempts=_max_stream_retries + 1,
                        fallback_from_stream=True,
                    )
                    return self._request_non_streaming_provider_response(
                        session_id=session_id,
                        task=task,
                        goal=goal,
                        provider_context=provider_context,
                        budget=budget,
                        fallback_from_stream=True,
                    )
                if is_retryable and _stream_attempt < _max_stream_retries:
                    if strategy == "compact_or_split_context":
                        provider_context = self._provider_recovery_retry_context(
                            provider_context=provider_context,
                            recovery=recovery,
                            strategy=strategy,
                            recovery_decision=recovery_decision,
                        )
                    logger.warning(
                        "Provider stream timed out (attempt %d/%d), retrying: %s",
                        _stream_attempt + 1, _max_stream_retries + 1, stream_exc,
                    )
                    self._append_provider_trace(
                        task=task,
                        event_type="provider.stream.retry",
                        payload={
                            "attempt": _stream_attempt + 1,
                            "error": str(stream_exc),
                            "failureRecovery": recovery_payload,
                            "strategy": provider_context.get("_provider_recovery_retry"),
                        },
                    )
                    self._publish_provider_api_retry(
                        session_id=session_id,
                        task=task,
                        stage="stream",
                        strategy=strategy,
                        recovery_payload=recovery_payload,
                        error=stream_exc,
                        attempt=_stream_attempt + 1,
                        max_attempts=_max_stream_retries + 1,
                    )
                    self._publish_provider_recovery_compact_summary(
                        session_id=session_id,
                        task=task,
                        retry_context=provider_context,
                        recovery_payload=recovery_payload,
                    )
                    continue
                raise

        self._raise_if_provider_task_cancelled(task)
        if final_response is None:
            if hasattr(self._provider, "generate"):
                self._append_provider_trace(
                    task=task,
                    event_type="provider.stream.fallback_non_stream",
                    payload={"error": "Provider stream ended without a final response."},
                )
                return self._request_non_streaming_provider_response(
                    session_id=session_id,
                    task=task,
                    goal=goal,
                    provider_context=provider_context,
                    budget=budget,
                    fallback_from_stream=True,
                )
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
            "_response_transport": "stream",
        }
        if not response["tool_calls"]:
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

    @staticmethod
    def _streaming_thinking_text(parts: list[str], *, limit: int = 1200) -> str:
        text = "".join(parts).strip()
        if not text:
            return ""
        if len(text) <= limit:
            return text
        return f"{text[:limit].rstrip()}..."

    @staticmethod
    def _should_flush_streaming_thinking(parts: list[str]) -> bool:
        text = "".join(parts)
        if len(text) >= 900:
            return True
        stripped = text.rstrip()
        return len(stripped) >= 320 and stripped.endswith((".", "!", "?", "\u3002", "\uff01", "\uff1f", "\n"))

    def _should_stream_provider(self, provider_context: dict[str, Any]) -> bool:
        if not hasattr(self._provider, "stream"):
            return False
        config = provider_context.get("config") or {}
        provider_config = config.get("provider") if isinstance(config, dict) else {}
        if not isinstance(provider_config, dict):
            return False
        provider_config = self._provider_trace_provider_config(provider_config)
        api_format = self._provider_trace_api_format(provider_config)
        if api_format not in {"openai-chat", "openai-responses", "anthropic-messages"}:
            return False
        stream_flag = self._provider_stream_flag(provider_config)
        if stream_flag is not None:
            return stream_flag
        mode = str(provider_config.get("mode") or provider_config.get("providerMode") or "").strip().lower()
        return mode in {
            "openai",
            "openai-compatible",
            "openai_compatible",
            "openai-compatible-chat",
            "anthropic",
            "anthropic-messages",
            "anthropic_messages",
        }

    @staticmethod
    def _can_fallback_to_non_stream(recovery: Any, *, has_partial_output: bool = False) -> bool:
        category = getattr(recovery, "category", None)
        if category == "invalid_response":
            return True
        return bool(has_partial_output) and category in {"timeout", "network", "server_error"}

    def _request_non_streaming_provider_response(
        self,
        *,
        session_id: str,
        task: dict[str, Any],
        goal: str,
        provider_context: dict[str, Any],
        budget: Any | None,
        fallback_from_stream: bool = False,
        recovery_retry: bool = False,
    ) -> dict[str, Any]:
        payload = self._provider_trace_payload(provider_context)
        if fallback_from_stream:
            payload["fallbackFromStream"] = True
        if recovery_retry:
            payload["recoveryRetry"] = provider_context.get("_provider_recovery_retry") or True
        self._append_provider_trace(task=task, event_type="provider.request", payload=payload)
        span = self._tracer.start_span(
            "llm_generate",
            trace_id=getattr(self, "_active_trace_id", None),
            parent_span_id=getattr(self, "_active_parent_span_id", None),
        )
        try:
            response = self._provider.generate(goal, provider_context)
        except Exception as exc:
            self._tracer.end_span(span.span_id, status="error")
            recovery = classify_provider_failure(exc)
            recovery_decision = self._provider_failure_recovery_decision(
                session_id=session_id,
                task=task,
                goal=goal,
                provider_context=provider_context,
                recovery=recovery,
                error=exc,
                stage="non_stream",
                recovery_retry=recovery_retry,
                has_partial_output=False,
            )
            self._record_provider_failure_recovery_decision(
                session_id=session_id,
                task=task,
                provider_context=provider_context,
                recovery_decision=recovery_decision,
                error=exc,
            )
            recovery_payload = recovery_decision["failureRecovery"]
            strategy = str(recovery_decision.get("strategy") or "")
            will_retry = (
                not recovery_retry
                and self._provider_recovery_should_retry(
                    recovery,
                    strategy=strategy,
                    has_partial_output=False,
                )
            )
            self._append_provider_trace(
                task=task,
                event_type="provider.failure.classified",
                payload={
                    **self._provider_trace_payload(provider_context),
                    "fallbackFromStream": fallback_from_stream,
                    "recoveryRetry": recovery_retry,
                    "error": str(exc)[:500],
                    "failureRecovery": recovery_payload,
                    "willRetry": will_retry,
                },
            )
            if not will_retry:
                self._append_provider_trace(
                    task=task,
                    event_type="provider.response",
                    payload={
                        **self._provider_trace_payload(provider_context),
                        "status": "failed",
                        "fallbackFromStream": fallback_from_stream,
                        "recoveryRetry": recovery_retry,
                        "error": str(exc)[:500],
                        "failureRecovery": recovery_payload,
                    },
                )
            if will_retry:
                retry_context = self._provider_recovery_retry_context(
                    provider_context=provider_context,
                    recovery=recovery,
                    strategy=strategy,
                    recovery_decision=recovery_decision,
                )
                self._append_provider_trace(
                    task=task,
                    event_type="provider.failure.recovery_retry",
                    payload={
                        **self._provider_trace_payload(retry_context),
                        "failureRecovery": recovery_payload,
                        "strategy": retry_context.get("_provider_recovery_retry"),
                    },
                )
                self._publish_provider_api_retry(
                    session_id=session_id,
                    task=task,
                    stage="non_stream",
                    strategy=strategy,
                    recovery_payload=recovery_payload,
                    error=exc,
                    attempt=2,
                    max_attempts=2,
                    fallback_from_stream=fallback_from_stream,
                )
                self._publish_provider_recovery_compact_summary(
                    session_id=session_id,
                    task=task,
                    retry_context=retry_context,
                    recovery_payload=recovery_payload,
                )
                return self._request_non_streaming_provider_response(
                    session_id=session_id,
                    task=task,
                    goal=goal,
                    provider_context=retry_context,
                    budget=budget,
                    fallback_from_stream=fallback_from_stream,
                    recovery_retry=True,
                )
            raise
        self._tracer.end_span(span.span_id, status="ok")
        self._consume_budget_from_provider_response(
            session_id=session_id,
            task=task,
            budget=budget,
            response=response,
        )
        self._append_provider_trace(
            task=task,
            event_type="provider.response",
            payload={
                **self._provider_response_trace(response),
                "fallbackFromStream": fallback_from_stream,
                "recoveryRetry": recovery_retry,
            },
        )
        response["_response_transport"] = "fallback_non_stream" if fallback_from_stream else "non_stream"
        return response

    def _provider_failure_recovery_decision(
        self,
        *,
        session_id: str,
        task: dict[str, Any],
        goal: str,
        provider_context: dict[str, Any],
        recovery: Any,
        error: BaseException | str,
        stage: str,
        recovery_retry: bool,
        has_partial_output: bool = False,
    ) -> dict[str, Any]:
        runtime_gate = self._provider_failure_runtime_gate(
            recovery=recovery,
            stage=stage,
            recovery_retry=recovery_retry,
            has_partial_output=has_partial_output,
        )
        can_non_stream = bool(
            hasattr(self._provider, "generate")
            and self._can_fallback_to_non_stream(
                recovery,
                has_partial_output=has_partial_output,
            )
        )
        strategy = self._provider_recovery_strategy(
            recovery=recovery,
            stage=stage,
            can_non_stream=can_non_stream,
            has_partial_output=has_partial_output,
        )
        max_retries = 0
        if not recovery_retry and self._provider_recovery_should_retry(
            recovery,
            strategy=strategy,
            has_partial_output=has_partial_output,
        ):
            max_retries = 1
        payload = {
            **recovery.to_dict(),
            "strategy": strategy,
            "maxRetries": max_retries,
            "stage": stage,
            "hasPartialOutput": has_partial_output,
            "runtimeGate": runtime_gate,
        }
        return {
            "strategy": strategy,
            "failureRecovery": payload,
        }

    def _provider_failure_runtime_gate(
        self,
        *,
        recovery: Any,
        stage: str,
        recovery_retry: bool,
        has_partial_output: bool,
    ) -> dict[str, Any]:
        category = str(getattr(recovery, "category", "") or "")
        if recovery_retry:
            return {
                "allowRecovery": False,
                "reason": "recovery_retry_already_used",
                "hasPartialOutput": has_partial_output,
            }
        if category in {"auth", "refusal"} or not bool(getattr(recovery, "recoverable", False)):
            return {
                "allowRecovery": False,
                "reason": "hard_provider_failure",
                "hasPartialOutput": has_partial_output,
            }
        if has_partial_output:
            return {
                "allowRecovery": True,
                "reason": "partial_output_available",
                "hasPartialOutput": True,
            }
        return {
            "allowRecovery": False,
            "reason": "no_partial_output",
            "hasPartialOutput": False,
        }

    def _provider_recovery_available_actions(
        self,
        *,
        recovery: Any,
        stage: str,
        has_partial_output: bool = False,
    ) -> list[str]:
        category = str(getattr(recovery, "category", "") or "")
        if category in {"auth", "refusal"} or not bool(getattr(recovery, "recoverable", False)):
            return ["ask_user", "surface_error", "abort"]
        actions = ["ask_user", "surface_error", "abort"]
        if bool(getattr(recovery, "retryable", False)):
            actions.extend(["retry", "retry_with_backoff"])
        if category == "context_too_large" or (
            has_partial_output and category in {"timeout", "rate_limit", "network", "server_error", "invalid_response"}
        ):
            actions.append("compact_or_split_context")
        if stage == "stream" and self._can_fallback_to_non_stream(
            recovery,
            has_partial_output=has_partial_output,
        ):
            actions.append("fallback")
        if category == "unsupported_format":
            actions.append("fix_provider_api_format")
        if category == "request_validation":
            actions.append("fix_provider_request")
        return actions

    def _provider_recovery_strategy(
        self,
        *,
        recovery: Any,
        stage: str,
        can_non_stream: bool,
        has_partial_output: bool = False,
    ) -> str:
        category = str(getattr(recovery, "category", "") or "")
        if category == "auth":
            return "fix_provider_credentials"
        if category == "refusal":
            return "ask_user_or_change_request"
        if not bool(getattr(recovery, "recoverable", False)):
            return "surface_error"

        if stage == "stream" and can_non_stream:
            return "fallback"
        if category == "context_too_large":
            return "compact_or_split_context"
        if category == "unsupported_format":
            return "fix_provider_api_format"
        if category == "request_validation":
            return "fix_provider_request"
        if category == "invalid_response":
            return "inspect_provider_response"
        if bool(getattr(recovery, "retryable", False)) and has_partial_output:
            return "retry_with_backoff" if category in {"rate_limit", "server_error"} else "retry"
        return "surface_error"

    def _record_provider_failure_recovery_decision(
        self,
        *,
        session_id: str,
        task: dict[str, Any],
        provider_context: dict[str, Any],
        recovery_decision: dict[str, Any],
        error: BaseException | str,
    ) -> None:
        payload = {
            "providerTurnId": provider_context.get("_provider_turn_id"),
            "failureRecovery": recovery_decision.get("failureRecovery"),
            "strategy": recovery_decision.get("strategy"),
            "error": str(error)[:500],
        }
        provider_context["_provider_failure_recovery_recorded"] = True
        if isinstance(recovery_decision.get("failureRecovery"), dict):
            provider_context["_provider_failure_recovery_payload"] = recovery_decision["failureRecovery"]
        self._append_provider_trace(
            task=task,
            event_type="provider.failure.recovery_decision",
            payload=payload,
        )
        if hasattr(self, "_publish"):
            self._publish(
                session_id=session_id,
                task=task,
                event_type="agent.decision.failure_recovery",
                payload=payload,
                visibility="trace",
            )
        recorder = getattr(self, "_record_failure_recovery_proposal", None)
        if callable(recorder):
            recorder(
                session_id=session_id,
                task=task,
                provider_turn_id=provider_context.get("_provider_turn_id"),
                failure_recovery=recovery_decision.get("failureRecovery"),
                error=str(error),
            )

    def _publish_provider_api_retry(
        self,
        *,
        session_id: str,
        task: dict[str, Any],
        stage: str,
        strategy: str,
        recovery_payload: dict[str, Any] | None,
        error: BaseException | str,
        attempt: int,
        max_attempts: int,
        fallback_from_stream: bool = False,
    ) -> None:
        if not hasattr(self, "_publish"):
            return
        recovery = recovery_payload if isinstance(recovery_payload, dict) else {}
        payload: dict[str, Any] = {
            "title": "API 閲嶈瘯",
            "summary": self._provider_api_retry_summary(
                stage=stage,
                strategy=strategy,
                recovery=recovery,
                attempt=attempt,
                max_attempts=max_attempts,
                fallback_from_stream=fallback_from_stream,
            ),
            "status": "warning",
            "attempt": attempt,
            "maxAttempts": max_attempts,
            "stage": stage,
            "strategy": strategy,
            "category": recovery.get("category"),
            "reason": recovery.get("reason"),
            "message": recovery.get("userMessage") or str(error)[:400],
            "hasPartialOutput": recovery.get("hasPartialOutput"),
            "fallbackFromStream": fallback_from_stream,
        }
        for key in ("httpStatus",):
            if recovery.get(key) is not None:
                payload[key] = recovery.get(key)
        self._publish(
            session_id=session_id,
            task=task,
            event_type="api_retry",
            payload=payload,
        )

    @staticmethod
    def _provider_api_retry_summary(
        *,
        stage: str,
        strategy: str,
        recovery: dict[str, Any],
        attempt: int,
        max_attempts: int,
        fallback_from_stream: bool,
    ) -> str:
        category = str(recovery.get("category") or "")
        if strategy == "fallback" or fallback_from_stream:
            action = "Streaming response was interrupted; retrying with a non-streaming request"
        elif strategy == "compact_or_split_context":
            action = "Request context is too large; compacting context before retry"
        elif strategy == "retry_with_backoff":
            action = "Provider is temporarily unavailable; retrying with backoff"
        else:
            action = "Provider is temporarily unavailable; retrying"
        if stage == "stream" and category == "timeout" and strategy != "fallback":
            action = "Streaming response timed out; retrying the provider request"
        attempt_text = f"attempt {attempt}/{max_attempts}"
        reason = str(recovery.get("reason") or "").strip()
        return f"{action} ({attempt_text}){f': {reason}' if reason else ''}"

    def _publish_provider_recovery_compact_summary(
        self,
        *,
        session_id: str,
        task: dict[str, Any],
        retry_context: dict[str, Any],
        recovery_payload: dict[str, Any] | None,
    ) -> None:
        retry_meta = retry_context.get("_provider_recovery_retry")
        if not isinstance(retry_meta, dict):
            return
        if retry_meta.get("strategy") != "compact_or_split_context":
            return
        recovery = recovery_payload if isinstance(recovery_payload, dict) else {}
        self._publish_provider_compact_summary(
            session_id=session_id,
            task=task,
            phase="provider_recovery",
            reason=str(recovery.get("reason") or "provider request required a smaller context before retry"),
            original_count=retry_meta.get("originalMessageCount"),
            compacted_count=retry_meta.get("retryMessageCount"),
            strategy="compact_or_split_context",
            category=recovery.get("category"),
        )

    def _publish_provider_compact_summary(
        self,
        *,
        session_id: str,
        task: dict[str, Any],
        phase: str,
        reason: str,
        original_count: Any,
        compacted_count: Any,
        strategy: str,
        tokens_before: Any | None = None,
        tokens_after: Any | None = None,
        category: Any | None = None,
    ) -> None:
        if not hasattr(self, "_publish"):
            return
        count_text = ""
        if isinstance(original_count, int) and isinstance(compacted_count, int):
            if compacted_count <= original_count:
                count_text = f"{original_count} -> {compacted_count} messages"
            else:
                count_text = f"original {original_count} messages, compacted context {compacted_count} messages"
        token_text = ""
        if isinstance(tokens_before, int) and isinstance(tokens_after, int):
            token_text = f"{tokens_before} -> {tokens_after} tokens"
        details = "; ".join(part for part in (count_text, token_text) if part)
        summary = "Context compacted"
        if details:
            summary = f"{summary} ({details})"
        if reason:
            summary = f"{summary}: {reason}"
        payload: dict[str, Any] = {
            "title": "Context compacted",
            "summary": summary,
            "status": "completed",
            "phase": phase,
            "strategy": strategy,
            "reason": reason,
            "originalMessageCount": original_count,
            "messageCount": compacted_count,
        }
        if tokens_before is not None:
            payload["tokensBefore"] = tokens_before
        if tokens_after is not None:
            payload["tokensAfter"] = tokens_after
        if category is not None:
            payload["category"] = category
        status_payload: dict[str, Any] = {
            "state": "compacting",
            "verb": phase,
            "phase": phase,
            "strategy": strategy,
        }
        if isinstance(tokens_before, int):
            status_payload["tokens"] = tokens_before
        self._publish(
            session_id=session_id,
            task=task,
            event_type="status",
            payload=status_payload,
        )
        self._publish(
            session_id=session_id,
            task=task,
            event_type="compact_summary",
            payload=payload,
        )

    @staticmethod
    def _provider_recovery_should_retry(
        recovery: Any,
        *,
        strategy: str | None = None,
        has_partial_output: bool = False,
    ) -> bool:
        if not bool(getattr(recovery, "recoverable", False)):
            return False
        category = str(getattr(recovery, "category", "") or "")
        if strategy in {"ask_user", "ask_user_or_change_request", "abort", "surface_error"}:
            return False
        if strategy in {"fix_provider_credentials", "fix_provider_request", "fix_provider_api_format", "inspect_provider_response"}:
            return False
        if strategy in {"retry", "retry_with_backoff"}:
            return bool(getattr(recovery, "retryable", False)) and has_partial_output
        if strategy == "compact_or_split_context":
            return category == "context_too_large" or (
                has_partial_output and category in {"timeout", "rate_limit", "network", "server_error", "invalid_response"}
            )
        return category == "context_too_large" or (
            has_partial_output and category in {"timeout", "rate_limit", "network", "server_error", "invalid_response"}
        )

    def _provider_recovery_retry_context(
        self,
        *,
        provider_context: dict[str, Any],
        recovery: Any,
        strategy: str = "compact_or_split_context",
        recovery_decision: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        retry_context = dict(provider_context)
        messages = provider_context.get("messages")
        should_compact = strategy == "compact_or_split_context" or getattr(recovery, "category", None) == "context_too_large"
        if isinstance(messages, list) and should_compact:
            retry_context["messages"] = self._provider_recovery_compact_messages(messages, recovery=recovery)
        retry_context["_provider_recovery_retry"] = {
            "category": getattr(recovery, "category", None),
            "recommendedAction": getattr(recovery, "recommended_action", None),
            "originalMessageCount": len(messages) if isinstance(messages, list) else None,
            "retryMessageCount": len(retry_context.get("messages") or []) if isinstance(retry_context.get("messages"), list) else None,
            "strategy": strategy,
        }
        return retry_context

    def _provider_recovery_compact_messages(
        self,
        messages: list[Any],
        *,
        recovery: Any,
    ) -> list[Any]:
        system_messages: list[dict[str, Any]] = []
        conversation_messages: list[dict[str, Any]] = []
        for item in messages:
            if not isinstance(item, dict):
                continue
            normalized = self._provider_recovery_trim_message(item)
            role = str(normalized.get("role") or "").strip().lower()
            if role in {"system", "developer"} and len(system_messages) < 2:
                system_messages.append(normalized)
            else:
                conversation_messages.append(normalized)
        notice = {
            "role": "system",
            "content": (
                "A previous provider request failed with "
                f"{getattr(recovery, 'category', 'recoverable_provider_failure')}. "
                "Continue from the recent context below; ask for missing details only if required."
            ),
        }
        tail = conversation_messages[-6:]
        return [*system_messages, notice, *tail]

    @staticmethod
    def _provider_recovery_trim_message(message: dict[str, Any], max_chars: int = 6000) -> dict[str, Any]:
        trimmed = dict(message)
        content = trimmed.get("content")
        if isinstance(content, str) and len(content) > max_chars:
            head = content[: max_chars // 2]
            tail = content[-(max_chars // 2):]
            trimmed["content"] = f"{head}\n...[provider recovery compacted middle]...\n{tail}"
        return trimmed

    @staticmethod
    def _provider_stream_flag(provider_config: dict[str, Any]) -> bool | None:
        for key in ("streamingEnabled", "streamResponses", "stream"):
            value = provider_config.get(key)
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                normalized = value.strip().lower()
                if normalized in {"true", "1", "yes", "on"}:
                    return True
                if normalized in {"false", "0", "no", "off"}:
                    return False
        return None

    @staticmethod
    def _detect_stream_repetition(parts: list[str], min_chunk: int = 40, max_occurrences: int = 3) -> bool:
        """Return True when accumulated stream parts show clear repetition loops."""
        if len(parts) < max_occurrences:
            return False
        text = "".join(parts)
        length = len(text)
        if length < min_chunk * max_occurrences:
            return False
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

    def _append_provider_trace(
        self,
        *,
        task: dict[str, Any],
        event_type: str,
        payload: dict[str, Any],
        visibility: str = "trace",
    ) -> None:
        if not hasattr(self._store, "append_trace_event"):
            return
        self._store.append_trace_event(
            task_id=task["id"],
            session_id=task["sessionId"],
            event_type=event_type,
            source="provider",
            related_id=payload.get("model"),
            payload=payload,
            visibility=visibility,
        )

    def _provider_trace_payload(self, provider_context: dict[str, Any]) -> dict[str, Any]:
        config = provider_context.get("config") or {}
        provider_config = config.get("provider") if isinstance(config, dict) else {}
        if not isinstance(provider_config, dict):
            provider_config = {}
        provider_config = self._provider_trace_provider_config(provider_config)
        api_format = self._provider_trace_api_format(provider_config)
        base_url = provider_config.get("baseUrl") or provider_config.get("base_url")
        return {
            "mode": provider_config.get("mode") or provider_config.get("providerMode"),
            "apiFormat": api_format,
            "model": provider_config.get("model") or provider_config.get("defaultModel"),
            "baseUrl": base_url,
            "requestPath": self._provider_trace_request_path(api_format, str(base_url or "")),
            "messageCount": len(provider_context.get("messages") or []),
            "toolCount": len(provider_context.get("openai_tools") or provider_context.get("tools") or []),
            "step": provider_context.get("step"),
        }

    def _provider_trace_provider_config(self, provider_config: dict[str, Any]) -> dict[str, Any]:
        effective = {
            key: value
            for key, value in provider_config.items()
            if key not in {"profiles", "activeProfileId"}
        }
        profiles = provider_config.get("profiles")
        active_profile_id = provider_config.get("activeProfileId")
        selected: dict[str, Any] | None = None
        if isinstance(profiles, list) and profiles:
            if isinstance(active_profile_id, str) and active_profile_id:
                selected = next(
                    (
                        profile
                        for profile in profiles
                        if isinstance(profile, dict) and profile.get("id") == active_profile_id
                    ),
                    None,
                )
            if selected is None:
                selected = next((profile for profile in profiles if isinstance(profile, dict)), None)
        if selected:
            effective.update({
                key: value
                for key, value in selected.items()
                if key not in {"profiles", "activeProfileId"}
            })
        return effective

    def _provider_trace_api_format(self, provider_config: dict[str, Any]) -> str:
        raw_format = (
            provider_config.get("apiFormat")
            or provider_config.get("api_format")
            or provider_config.get("providerApiFormat")
        )
        if raw_format is None:
            mode = (
                str(provider_config.get("mode") or provider_config.get("providerMode") or "")
                .strip()
                .lower()
                .replace("_", "-")
            )
            if mode in {"anthropic", "anthropic-messages"}:
                return "anthropic-messages"
        raw = raw_format or "openai-chat"
        normalized = str(raw).strip().lower().replace("_", "-")
        if normalized in {"chat-completions", "custom-openai-compatible"}:
            return "openai-chat"
        if normalized in {"openai-chat", "openai-responses", "anthropic-messages"}:
            return normalized
        return "openai-chat"

    def _provider_trace_request_path(self, api_format: str, base_url: str) -> str:
        from urllib.parse import urlsplit

        trimmed = base_url.rstrip("/")
        path = urlsplit(trimmed).path or "/"
        if api_format == "openai-responses":
            if trimmed.endswith("/responses"):
                return path
            return "/v1/responses" if path in {"", "/"} else f"{path.rstrip('/')}/responses"
        if api_format == "anthropic-messages":
            if trimmed.endswith("/messages"):
                return path
            return "/v1/messages" if path in {"", "/"} else f"{path.rstrip('/')}/messages"
        if trimmed.endswith("/chat/completions"):
            return path
        return "/v1/chat/completions" if path in {"", "/"} else f"{path.rstrip('/')}/chat/completions"

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

    @staticmethod
    def _thinking_source(value: Any, *, streaming: bool) -> str:
        source = str(value or "").strip()
        if source in {"provider_reasoning_delta", "provider_reasoning_summary", "non_stream_thought_summary"}:
            return source
        if source in {"reasoning_delta", "reasoning"}:
            return "provider_reasoning_delta"
        if source in {"reasoning_summary", "summary"}:
            return "provider_reasoning_summary"
        if source in {"thought_summary", "thoughtSummary"}:
            return "non_stream_thought_summary"
        return "provider_reasoning_delta" if streaming else "non_stream_thought_summary"

    def _parse_provider_response(
        self,
        response: Any,
        *,
        allow_fallback: bool,
        allow_plain_message_final: bool,
    ) -> dict[str, Any]:
        if not isinstance(response, dict):
            raise RuntimeError("Provider returned invalid output: expected an object.")

        explicit_decision = response.get("decision") or response.get("turn_decision")
        if explicit_decision == TurnDecision.ASK_USER.value:
            return {
                "status": "ask_user",
                "message": self._assistant_text(response),
                "tool_calls": [],
            }

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

        if allow_fallback:
            return {"status": "fallback"}

        raise RuntimeError("Provider returned no final answer or tool calls.")

    def _parse_turn_result(
        self,
        response: Any,
        *,
        allow_fallback: bool,
        allow_plain_message_final: bool,
    ) -> ProviderTurnResult:
        """Parse a provider response into a structured ProviderTurnResult."""
        if not isinstance(response, dict):
            return ProviderTurnResult(
                decision=TurnDecision.FAILED,
                thought_summary="Provider returned non-dict output",
                message="",
                raw_response=response if isinstance(response, dict) else None,
            )

        tool_calls = response.get("tool_calls")
        message = self._assistant_text(response)
        explicit_thought_summary = response.get("thought_summary") or response.get("thoughtSummary")
        thought_summary = (
            explicit_thought_summary.strip()
            if isinstance(explicit_thought_summary, str) and explicit_thought_summary.strip()
            else ""
        )
        why_complete = response.get("why_complete") or response.get("whyComplete")
        remaining_risks = response.get("remaining_risks") or response.get("remainingRisks") or []
        policy_needs = response.get("policy_needs") or response.get("policyNeeds")

        explicit_decision = response.get("decision") or response.get("turn_decision")
        decision: TurnDecision | None = None
        if explicit_decision and isinstance(explicit_decision, str):
            try:
                decision = TurnDecision(explicit_decision)
            except ValueError:
                logger.debug("Unknown turn decision %r, inferring from response", explicit_decision)

        if decision is None:
            if isinstance(tool_calls, list) and tool_calls:
                decision = TurnDecision.CONTINUE_WITH_TOOLS
            else:
                final_answer = self._final_answer(response, allow_plain_message=allow_plain_message_final)
                if final_answer is not None:
                    decision = TurnDecision.FINAL_ANSWER
                elif allow_fallback:
                    decision = TurnDecision.FAILED
                else:
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

