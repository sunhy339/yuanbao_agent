"""Provider Turn Mixin — extracted from ReactRunnerMixin.

Handles provider request/response, streaming, trace helpers,
response parsing, and turn result extraction.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from ..context.token_budget import estimate_tokens
from ..planner.preflight_split import build_provider_preflight_split_plan_payload
from ..provider.failure_recovery import classify_provider_failure
from ..react.types import ProviderTurnResult, TurnDecision

logger = logging.getLogger(__name__)


class ProviderTurnMixin:
    """Mixin providing provider turn handling, streaming, and response parsing."""

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
        advice = self._provider_preflight_advice(
            goal=goal,
            provider_context=provider_context,
            facts=facts,
        )
        runtime_action = self._provider_preflight_runtime_action(
            facts=facts,
            advice=advice,
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
                "reason": self._provider_preflight_reason(facts=facts, advice=advice),
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
                "reason": self._provider_preflight_reason(facts=facts, advice=advice),
                "originalMessageCount": original_message_count,
                "messageCount": original_message_count,
                "originalTokenEstimate": original_tokens,
                "tokenEstimate": token_estimate,
            }
        switch_result = None
        if runtime_action == "switch_provider":
            switch_result = self._provider_preflight_switch_provider_context(
                provider_context=result_context,
                facts=facts,
                advice=advice,
            )
            if switch_result is not None:
                result_context = switch_result["provider_context"]
                result_context["_provider_preflight_provider_switch"] = switch_result["switch"]
                applied = True
                if isinstance(result_context.get("_provider_preflight"), dict):
                    result_context["_provider_preflight"]["providerSwitch"] = switch_result["switch"]
        split_plan = self._provider_preflight_split_plan_payload(advice=advice)
        if runtime_action == "execute_split" and split_plan is not None:
            result_context["_provider_preflight_split_plan"] = split_plan
            applied = True
            if isinstance(result_context.get("_provider_preflight"), dict):
                result_context["_provider_preflight"]["splitPlan"] = {
                    "subtaskCount": len(split_plan.get("subtasks") or []),
                    "executionOrder": split_plan.get("execution_order"),
                    "reason": split_plan.get("reason"),
                }

        facts_after = dict(facts)
        facts_after.update({
            "messageCountAfter": len(result_messages) if isinstance(result_messages, list) else None,
            "estimatedInputTokensAfter": token_estimate,
        })
        decision = {
            "facts": facts_after,
            "advice": advice,
            "runtimeAction": runtime_action,
            "runtimeApplied": applied,
            "providerPreflight": result_context.get("_provider_preflight"),
            "splitPlan": split_plan if runtime_action == "execute_split" else None,
            "providerSwitch": switch_result["switch"] if isinstance(switch_result, dict) else None,
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
        routing = provider_context.get("routing") if isinstance(provider_context.get("routing"), dict) else {}
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
        available_profiles = self._provider_preflight_available_profiles(provider_context)
        profile_ranking = self._provider_preflight_profile_ranking(available_profiles)
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
            "alreadyProviderPreflightSplit": bool(routing.get("providerPreflightSplit")),
            "availableProviderProfiles": available_profiles,
            "providerProfileRanking": profile_ranking,
            "topProviderProfile": profile_ranking[0] if profile_ranking else None,
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

    def _provider_preflight_advice(
        self,
        *,
        goal: str,
        provider_context: dict[str, Any],
        facts: dict[str, Any],
    ) -> Any | None:
        advisor = getattr(self, "_decision_advisor", None)
        if advisor is None:
            return None
        if not self._should_consult_provider_preflight_advisor(provider_context=provider_context, facts=facts):
            return None
        input_context: dict[str, Any] = {
            "goal": goal[:2000],
            "preflight_facts": facts,
            "runtime_limits": {
                "autoActions": ["proceed", "compact_context"],
                "executableActions": ["propose_split", "switch_provider"],
                "advisoryOnlyActions": ["ask_user", "abort"],
                "splitExecution": "validated split plans run through the existing root planning/DAG path",
                "switchProviderExecution": "validated provider profile switches apply only to this provider turn context",
                "runtimeWillNotCallProviderAfterTransportFailureForAdvice": True,
            },
            "available_actions": [
                "proceed",
                "compact_context",
                "propose_split",
                "ask_user",
                "switch_provider",
                "abort",
            ],
        }
        config = provider_context.get("config")
        if isinstance(config, dict):
            input_context["config"] = config
        try:
            return advisor.advise("provider_preflight", input_context)
        except Exception:  # noqa: BLE001
            logger.debug("Provider preflight advisor failed", exc_info=True)
            return None

    def _should_consult_provider_preflight_advisor(
        self,
        *,
        provider_context: dict[str, Any],
        facts: dict[str, Any],
    ) -> bool:
        config = provider_context.get("config") if isinstance(provider_context, dict) else None
        advisor_config = {}
        if isinstance(config, dict):
            advisor = config.get("advisor")
            if isinstance(advisor, dict):
                advisor_config = advisor
            autonomy = config.get("autonomy")
            if not advisor_config and isinstance(autonomy, dict) and isinstance(autonomy.get("advisor"), dict):
                advisor_config = autonomy["advisor"]
        if advisor_config.get("providerPreflight") is False:
            return False
        if advisor_config.get("alwaysProviderPreflight") is True:
            return True
        return bool(
            facts.get("nearContextLimit")
            or facts.get("overContextLimit")
            or facts.get("hasPriorProviderFailure")
        )

    @staticmethod
    def _provider_preflight_reason(*, facts: dict[str, Any], advice: Any | None) -> str:
        if advice is not None and bool(getattr(advice, "accepted", False)):
            payload = getattr(advice, "payload", {}) or {}
            if isinstance(payload, dict) and isinstance(payload.get("reason"), str):
                return payload["reason"][:500]
            rationale = getattr(advice, "rationale", None)
            if isinstance(rationale, str) and rationale.strip():
                return rationale[:500]
        if facts.get("overContextLimit"):
            return "Runtime detected provider context at or above the configured context limit."
        if facts.get("nearContextLimit"):
            return "Runtime detected provider context near the configured context limit."
        if facts.get("hasPriorProviderFailure"):
            return "Runtime detected a prior provider failure in recent tool results."
        return "Provider request is within preflight runtime bounds."

    def _provider_preflight_runtime_action(self, *, facts: dict[str, Any], advice: Any | None) -> str:
        proposed = ""
        if advice is not None and bool(getattr(advice, "accepted", False)):
            payload = getattr(advice, "payload", {}) or {}
            if isinstance(payload, dict):
                proposed = str(payload.get("action") or "").strip()
        if proposed == "compact_context":
            return "compact_context"
        split_allowed = not facts.get("childWorker") and not facts.get("alreadyProviderPreflightSplit")
        if proposed == "propose_split" and split_allowed and self._provider_preflight_split_plan_payload(advice=advice) is not None:
            return "execute_split"
        if proposed == "switch_provider" and self._provider_preflight_switch_target(facts=facts, advice=advice) is not None:
            return "switch_provider"
        if facts.get("overContextLimit"):
            return "compact_context"
        return "proceed"

    def _provider_preflight_split_plan_payload(self, *, advice: Any | None) -> dict[str, Any] | None:
        return build_provider_preflight_split_plan_payload(advice=advice)

    def _provider_preflight_switch_target(self, *, facts: dict[str, Any], advice: Any | None) -> str | None:
        if facts.get("childWorker") or facts.get("alreadyProviderPreflightSplit"):
            return None
        if advice is None or not bool(getattr(advice, "accepted", False)):
            return None
        payload = getattr(advice, "payload", {}) or {}
        if not isinstance(payload, dict) or payload.get("action") != "switch_provider":
            return None
        target = payload.get("fallbackProviderId")
        if not isinstance(target, str) or not target.strip():
            return None
        target = target.strip()
        ranked_profiles = facts.get("providerProfileRanking")
        if not isinstance(ranked_profiles, list) or not ranked_profiles:
            ranked_profiles = facts.get("availableProviderProfiles") or []
        for profile in ranked_profiles:
            if not isinstance(profile, dict) or profile.get("id") != target:
                continue
            if profile.get("switchEligible") is False:
                return None
            if profile.get("isActive") is True or profile.get("enabled") is False:
                return None
            last_status = str(profile.get("lastStatus") or "").strip().lower()
            if last_status in {"failed", "missing_env", "unsupported"}:
                return None
            return target
        return None

    def _provider_preflight_switch_provider_context(
        self,
        *,
        provider_context: dict[str, Any],
        facts: dict[str, Any],
        advice: Any | None,
    ) -> dict[str, Any] | None:
        target = self._provider_preflight_switch_target(facts=facts, advice=advice)
        if target is None:
            return None
        config = provider_context.get("config")
        if not isinstance(config, dict):
            return None
        provider_config = config.get("provider")
        if not isinstance(provider_config, dict):
            return None
        profiles = provider_config.get("profiles")
        if not isinstance(profiles, list):
            return None
        selected = next((profile for profile in profiles if isinstance(profile, dict) and profile.get("id") == target), None)
        if not isinstance(selected, dict):
            return None

        updated_config = dict(config)
        updated_provider = dict(provider_config)
        updated_provider["activeProfileId"] = target
        for key, value in selected.items():
            if key not in {"id", "name", "profiles", "activeProfileId"}:
                updated_provider[key] = value
        updated_config["provider"] = updated_provider
        updated_context = dict(provider_context)
        updated_context["config"] = updated_config
        current = str(provider_config.get("activeProfileId") or "").strip() or None
        switch = {
            "fromProfileId": current,
            "toProfileId": target,
            "profileName": selected.get("name"),
            "model": selected.get("model") or selected.get("defaultModel"),
            "mode": selected.get("mode") or updated_provider.get("mode"),
            "reason": self._provider_preflight_reason(facts=facts, advice=advice),
            "scope": "provider_turn",
        }
        ranking = facts.get("providerProfileRanking")
        if isinstance(ranking, list):
            ranked = next((item for item in ranking if isinstance(item, dict) and item.get("id") == target), None)
            if isinstance(ranked, dict):
                switch["health"] = {
                    key: ranked.get(key)
                    for key in ("healthState", "lastStatus", "lastCheckedAt", "lastErrorSummary", "score", "rank", "rankReason")
                    if ranked.get(key) not in (None, "", [])
                }
        return {"provider_context": updated_context, "switch": switch}

    def _provider_preflight_available_profiles(self, provider_context: dict[str, Any]) -> list[dict[str, Any]]:
        config = provider_context.get("config") if isinstance(provider_context, dict) else None
        provider_config = config.get("provider") if isinstance(config, dict) else None
        if not isinstance(provider_config, dict):
            return []
        active_profile_id = provider_config.get("activeProfileId")
        profiles = provider_config.get("profiles")
        if not isinstance(profiles, list):
            return []
        result: list[dict[str, Any]] = []
        for profile in profiles:
            if not isinstance(profile, dict):
                continue
            profile_id = profile.get("id")
            if not isinstance(profile_id, str) or not profile_id.strip():
                continue
            result.append({
                "id": profile_id.strip(),
                "name": profile.get("name"),
                "mode": profile.get("mode") or provider_config.get("mode"),
                "model": profile.get("model") or profile.get("defaultModel"),
                "apiFormat": profile.get("apiFormat") or provider_config.get("apiFormat"),
                "lastStatus": profile.get("lastStatus"),
                "lastCheckedAt": profile.get("lastCheckedAt"),
                "lastErrorSummary": profile.get("lastErrorSummary"),
                "enabled": profile.get("enabled", True),
                "isActive": profile_id == active_profile_id,
            })
        return result

    def _provider_preflight_profile_ranking(self, profiles: list[dict[str, Any]]) -> list[dict[str, Any]]:
        ranked: list[dict[str, Any]] = []
        for profile in profiles:
            if not isinstance(profile, dict):
                continue
            health = self._provider_profile_health(profile)
            item = {
                **profile,
                **health,
            }
            ranked.append(item)
        ranked.sort(key=lambda item: (int(item.get("score") or 0), 0 if item.get("isActive") else 1), reverse=True)
        for index, item in enumerate(ranked, start=1):
            item["rank"] = index
        return ranked

    @staticmethod
    def _provider_profile_health(profile: dict[str, Any]) -> dict[str, Any]:
        enabled = profile.get("enabled", True) is not False
        is_active = profile.get("isActive") is True
        last_status = str(profile.get("lastStatus") or "").strip().lower()
        if not enabled:
            health_state = "disabled"
            score = 0
            reason = "profile disabled"
        elif last_status in {"ok", "success", "succeeded", "healthy"}:
            health_state = "healthy"
            score = 90
            reason = "last provider check succeeded"
        elif not last_status:
            health_state = "unknown"
            score = 65
            reason = "no provider health check recorded"
        elif last_status in {"missing_env", "unsupported", "auth", "failed", "error"}:
            health_state = "unhealthy"
            score = 10
            reason = f"last provider check status: {last_status}"
        else:
            health_state = "degraded"
            score = 45
            reason = f"last provider check status: {last_status}"
        if is_active:
            score = max(0, score - 15)
        switch_eligible = enabled and not is_active and health_state in {"healthy", "unknown", "degraded"}
        if not switch_eligible and is_active:
            reason = f"{reason}; currently active"
        return {
            "healthState": health_state,
            "score": score,
            "rankReason": reason,
            "switchEligible": switch_eligible,
        }

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
        advice = decision.get("advice")
        payload: dict[str, Any] = {
            "providerTurnId": provider_turn_id,
            "facts": facts,
            "runtimeAction": decision.get("runtimeAction"),
            "runtimeApplied": bool(decision.get("runtimeApplied")),
            "providerPreflight": decision.get("providerPreflight"),
        }
        provider_switch = decision.get("providerSwitch")
        if provider_switch is None and isinstance(decision.get("providerPreflight"), dict):
            provider_switch = decision["providerPreflight"].get("providerSwitch")
        if isinstance(provider_switch, dict):
            payload["providerSwitch"] = provider_switch
        split_plan = self._provider_preflight_split_plan_payload(advice=advice)
        if split_plan is not None:
            payload["splitPlan"] = split_plan
        if advice is not None:
            payload["advisor"] = {
                "source": getattr(advice, "source", None),
                "accepted": bool(getattr(advice, "accepted", False)),
                "confidence": getattr(advice, "confidence", None),
                "rationale": getattr(advice, "rationale", None),
                "fallbackReason": getattr(advice, "fallback_reason", None),
                "validationReasons": list(getattr(advice, "validation_reasons", None) or []),
                "proposal": getattr(advice, "payload", None),
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
        recorder = getattr(self, "_record_provider_preflight_proposal", None)
        if callable(recorder):
            recorder(
                session_id=session_id,
                task=task,
                provider_turn_id=provider_turn_id,
                preflight=decision,
                advice=advice,
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
        _content_block_started = False
        _active_tool_streams: dict[int, dict[str, Any]] = {}
        for _stream_attempt in range(_max_stream_retries + 1):
            final_response = None
            streamed_content = False
            _stream_text_parts = []
            _content_block_started = False
            _active_tool_streams = {}
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
                        response = event.get("response")
                        if isinstance(response, dict):
                            final_response = response
                    elif event_type == "finish_reason":
                        self._append_provider_trace(task=task, event_type="provider.stream.finish", payload=event)
                    elif event_type == "tool_call_delta":
                        self._append_provider_trace(task=task, event_type="provider.stream.tool_call_delta", payload=event)
                        index = event.get("index")
                        if not isinstance(index, int):
                            continue
                        stream_state = _active_tool_streams.setdefault(index, {"toolUseId": None, "toolName": None})
                        tool_use_id = event.get("id")
                        tool_name = event.get("name")
                        if isinstance(tool_use_id, str) and tool_use_id:
                            stream_state["toolUseId"] = tool_use_id
                        if isinstance(tool_name, str) and tool_name:
                            stream_state["toolName"] = tool_name
                        if stream_state.get("toolUseId") or stream_state.get("toolName"):
                            if not stream_state.get("started"):
                                self._publish(
                                    session_id=session_id,
                                    task=task,
                                    event_type="content_start",
                                    payload={
                                        "blockType": "tool_use",
                                        "toolUseId": stream_state.get("toolUseId"),
                                        "toolName": stream_state.get("toolName"),
                                    },
                                )
                                stream_state["started"] = True
                        arguments_delta = event.get("arguments_delta")
                        if isinstance(arguments_delta, str) and arguments_delta:
                            self._publish(
                                session_id=session_id,
                                task=task,
                                event_type="content_delta",
                                payload={
                                    "toolUseId": stream_state.get("toolUseId"),
                                    "toolName": stream_state.get("toolName"),
                                    "toolInput": arguments_delta,
                                },
                            )
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
                    continue
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

    def _should_stream_provider(self, provider_context: dict[str, Any]) -> bool:
        if not hasattr(self._provider, "stream"):
            return False
        config = provider_context.get("config") or {}
        provider_config = config.get("provider") if isinstance(config, dict) else {}
        if not isinstance(provider_config, dict):
            return False
        provider_config = self._provider_trace_provider_config(provider_config)
        api_format = self._provider_trace_api_format(provider_config)
        if api_format not in {"openai-chat", "openai-responses"}:
            return False
        stream_flag = self._provider_stream_flag(provider_config)
        if stream_flag is not None:
            return stream_flag
        mode = str(provider_config.get("mode") or provider_config.get("providerMode") or "").strip().lower()
        return mode in {"openai", "openai-compatible", "openai_compatible", "openai-compatible-chat"}

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
        advisor_gate = self._provider_failure_advisor_gate(
            recovery=recovery,
            stage=stage,
            recovery_retry=recovery_retry,
            has_partial_output=has_partial_output,
        )
        advice = self._provider_failure_recovery_advice(
            goal=goal,
            provider_context=provider_context,
            recovery=recovery,
            error=error,
            stage=stage,
            recovery_retry=recovery_retry,
            advisor_gate=advisor_gate,
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
            advice=advice,
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
            "advisorGate": advisor_gate,
            "advisorAvailable": advice is not None,
            "advisorAccepted": bool(getattr(advice, "accepted", False)) if advice is not None else False,
        }
        if advice is not None:
            payload["advisorSource"] = getattr(advice, "source", None)
            if getattr(advice, "fallback_reason", None):
                payload["advisorFallbackReason"] = getattr(advice, "fallback_reason")
        return {
            "strategy": strategy,
            "failureRecovery": payload,
            "advice": advice,
        }

    def _provider_failure_recovery_advice(
        self,
        *,
        goal: str,
        provider_context: dict[str, Any],
        recovery: Any,
        error: BaseException | str,
        stage: str,
        recovery_retry: bool,
        advisor_gate: dict[str, Any] | None = None,
    ) -> Any | None:
        gate = advisor_gate or self._provider_failure_advisor_gate(
            recovery=recovery,
            stage=stage,
            recovery_retry=recovery_retry,
            has_partial_output=False,
        )
        if not bool(gate.get("allowAdvisor")):
            return None
        advisor = getattr(self, "_decision_advisor", None)
        if advisor is None:
            return None
        if not bool(getattr(recovery, "recoverable", False)):
            return None
        category = str(getattr(recovery, "category", "") or "")
        if category in {"auth", "refusal"}:
            return None
        messages = provider_context.get("messages")
        tools = provider_context.get("openai_tools") or provider_context.get("tools") or []
        input_context = {
            "goal": goal,
            "provider_failure": recovery.to_dict(),
            "error_summary": str(error)[:500],
            "stage": stage,
            "attempt": 2 if recovery_retry else 1,
            "runtime_limits": {
                "maxRetriesRemaining": 0 if recovery_retry else 1,
                "canFallbackToNonStream": bool(
                    hasattr(self._provider, "generate")
                    and self._can_fallback_to_non_stream(
                        recovery,
                        has_partial_output=bool(gate.get("hasPartialOutput")),
                    )
                ),
                "authAndRefusalAreNonRetryable": True,
                "advisorRequiresPartialOutputOrSemanticRepair": True,
            },
            "available_actions": self._provider_recovery_available_actions(
                recovery=recovery,
                stage=stage,
                has_partial_output=bool(gate.get("hasPartialOutput")),
            ),
            "provider_request": self._provider_trace_payload(provider_context),
            "context_shape": {
                "messageCount": len(messages) if isinstance(messages, list) else None,
                "toolCount": len(tools) if isinstance(tools, list) else None,
                "step": provider_context.get("step"),
            },
            "advisor_gate": gate,
        }
        config = provider_context.get("config")
        if isinstance(config, dict):
            input_context["config"] = config
        try:
            return advisor.advise("failure_recovery", input_context)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Failure recovery advisor failed", exc_info=True)
            return None

    def _provider_failure_advisor_gate(
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
                "allowAdvisor": False,
                "reason": "recovery_retry_already_used",
                "hasPartialOutput": has_partial_output,
            }
        if category in {"auth", "refusal"} or not bool(getattr(recovery, "recoverable", False)):
            return {
                "allowAdvisor": False,
                "reason": "hard_provider_failure",
                "hasPartialOutput": has_partial_output,
            }
        if has_partial_output:
            return {
                "allowAdvisor": True,
                "reason": "partial_output_available",
                "hasPartialOutput": True,
            }
        return {
            "allowAdvisor": False,
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
        advice: Any | None,
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

        proposed = ""
        if advice is not None and bool(getattr(advice, "accepted", False)):
            payload = getattr(advice, "payload", {}) or {}
            if isinstance(payload, dict):
                proposed = str(payload.get("strategy") or "").strip()
        if proposed:
            if proposed in {"ask_user", "ask_user_or_change_request", "abort", "surface_error"}:
                return proposed
            if proposed in {"fix_provider_request", "fix_provider_api_format", "inspect_provider_response"}:
                return proposed
            if (
                proposed in {"retry", "retry_with_backoff"}
                and bool(getattr(recovery, "retryable", False))
                and has_partial_output
            ):
                return proposed
            if proposed == "compact_or_split_context" and category in {"context_too_large", "invalid_response"}:
                return proposed
            if proposed == "compact_or_split_context" and has_partial_output and category in {
                "timeout",
                "rate_limit",
                "network",
                "server_error",
            }:
                return proposed
            if proposed == "fallback" and stage == "stream" and can_non_stream:
                return proposed

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
        advice = recovery_decision.get("advice")
        if advice is not None:
            payload["advisor"] = {
                "source": getattr(advice, "source", None),
                "accepted": bool(getattr(advice, "accepted", False)),
                "confidence": getattr(advice, "confidence", None),
                "rationale": getattr(advice, "rationale", None),
                "fallbackReason": getattr(advice, "fallback_reason", None),
            }
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
                advice=advice,
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
            "advisorAccepted": bool(
                getattr((recovery_decision or {}).get("advice"), "accepted", False)
            ) if isinstance(recovery_decision, dict) else False,
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
        if not hasattr(self._provider, "choose_tool_sequence") or not hasattr(self._provider, "summarize_findings"):
            return False
        config = {}
        store = getattr(self, "_store", None)
        if store is not None:
            try:
                config = store.get_config({}).get("config", {}) or {}
            except Exception:
                config = {}
        provider_config = config.get("provider") if isinstance(config, dict) else {}
        if not isinstance(provider_config, dict):
            provider_config = {}
        deterministic_fallback = provider_config.get("deterministicFallback")
        if deterministic_fallback is not None:
            return bool(deterministic_fallback)
        return str(provider_config.get("mode") or "").strip().lower() == "mock"
