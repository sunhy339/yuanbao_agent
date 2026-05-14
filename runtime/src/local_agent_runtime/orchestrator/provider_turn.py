"""Provider Turn Mixin — extracted from ReactRunnerMixin.

Handles provider request/response, streaming, trace helpers,
response parsing, and turn result extraction.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from ..react.types import ProviderTurnResult, TurnDecision

logger = logging.getLogger(__name__)


class ProviderTurnMixin:
    """Mixin providing provider turn handling, streaming, and response parsing."""

    def _request_provider_response(
        self,
        *,
        session_id: str,
        task: dict[str, Any],
        goal: str,
        provider_context: dict[str, Any],
        budget: Any | None = None,
    ) -> dict[str, Any]:
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
                if _stream_text_parts:
                    _active_msg_id = task.get("activeAssistantMessageId")
                    if _active_msg_id:
                        self._store.update_message(_active_msg_id, content="".join(_stream_text_parts))
                break
            except Exception as stream_exc:
                from ..provider.openai_compatible import ProviderAdapterError
                error_text = str(stream_exc).lower()
                is_retryable = isinstance(stream_exc, ProviderAdapterError) and "timed out" in error_text
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
                if hasattr(self._provider, "generate"):
                    logger.warning(
                        "Provider stream failed for task=%s; falling back to non-streaming request: %s",
                        task["id"],
                        stream_exc,
                    )
                    self._append_provider_trace(
                        task=task,
                        event_type="provider.stream.fallback_non_stream",
                        payload={"attempt": _stream_attempt + 1, "error": str(stream_exc)[:500]},
                    )
                    return self._request_non_streaming_provider_response(
                        session_id=session_id,
                        task=task,
                        goal=goal,
                        provider_context=provider_context,
                        budget=budget,
                        fallback_from_stream=True,
                    )
                raise

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

    def _should_stream_provider(self, provider_context: dict[str, Any]) -> bool:
        if not hasattr(self._provider, "stream"):
            return False
        config = provider_context.get("config") or {}
        provider_config = config.get("provider") if isinstance(config, dict) else {}
        if not isinstance(provider_config, dict):
            return False
        provider_config = self._provider_trace_provider_config(provider_config)
        api_format = self._provider_trace_api_format(provider_config)
        if api_format != "openai-chat":
            return False
        stream_flag = self._provider_stream_flag(provider_config)
        if stream_flag is not None:
            return stream_flag
        mode = str(provider_config.get("mode") or provider_config.get("providerMode") or "").strip().lower()
        return mode in {"openai", "openai-compatible", "openai_compatible", "openai-compatible-chat"}

    def _request_non_streaming_provider_response(
        self,
        *,
        session_id: str,
        task: dict[str, Any],
        goal: str,
        provider_context: dict[str, Any],
        budget: Any | None,
        fallback_from_stream: bool = False,
    ) -> dict[str, Any]:
        payload = self._provider_trace_payload(provider_context)
        if fallback_from_stream:
            payload["fallbackFromStream"] = True
        self._append_provider_trace(task=task, event_type="provider.request", payload=payload)
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
        self._append_provider_trace(
            task=task,
            event_type="provider.response",
            payload={**self._provider_response_trace(response), "fallbackFromStream": fallback_from_stream},
        )
        return response

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
        thought_summary = response.get("thought_summary") or response.get("thoughtSummary") or message[:200]
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
                elif allow_fallback and self._has_deterministic_fallback():
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

    def _has_deterministic_fallback(self) -> bool:
        return hasattr(self._provider, "choose_tool_sequence") and hasattr(self._provider, "summarize_findings")
