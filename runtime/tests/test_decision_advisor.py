"""Tests for P0: DecisionAdvisor and MaterialDecisionRegistry."""

from __future__ import annotations

from typing import Any

import pytest

from local_agent_runtime.policy.decision_advisor import (
    AdviceResult,
    DecisionAdvisor,
    DecisionKindEntry,
    get_decision_kind,
    list_decision_kinds,
    register_decision,
)


# ---------------------------------------------------------------------------
# Registry tests
# ---------------------------------------------------------------------------

class TestDecisionRegistry:
    """Material decision registry lookup and listing."""

    def test_builtin_kinds_registered(self) -> None:
        kinds = list_decision_kinds()
        assert "intent_mode" in kinds
        assert "routing_strategy" in kinds
        assert "context_policy" in kinds
        assert "decomposition" in kinds

    def test_get_decision_kind_returns_entry(self) -> None:
        entry = get_decision_kind("intent_mode")
        assert entry is not None
        assert entry.kind == "intent_mode"
        assert "goal" in entry.required_input_fields

    def test_get_unknown_kind_returns_none(self) -> None:
        assert get_decision_kind("nonexistent") is None

    def test_register_custom_kind(self) -> None:
        entry = DecisionKindEntry(
            kind="test_custom",
            description="test",
            required_input_fields=("x",),
            allowed_proposal_schema=("y",),
            fallback="do nothing",
            trace_event="agent.decision.test_custom",
        )
        register_decision(entry)
        assert get_decision_kind("test_custom") is not None
        assert "test_custom" in list_decision_kinds()

    def test_entry_has_trace_event(self) -> None:
        for kind in ("intent_mode", "routing_strategy", "context_policy", "decomposition"):
            entry = get_decision_kind(kind)
            assert entry is not None
            assert entry.trace_event.startswith("agent.decision.")


# ---------------------------------------------------------------------------
# DecisionAdvisor core tests
# ---------------------------------------------------------------------------

class _GoodProvider:
    """Provider that returns a valid JSON proposal."""

    def __init__(self, response: str = "") -> None:
        self._response = response or json.dumps({
            "proposal": {"mode": "task"},
            "confidence": 0.9,
            "rationale": "User wants a task executed",
        })
        self.contexts: list[dict[str, Any]] = []
        self.prompts: list[str] = []

    def generate(self, prompt: str, context: dict[str, Any]) -> dict[str, Any]:
        self.prompts.append(prompt)
        self.contexts.append(context)
        return {"message": self._response}


class _BadProvider:
    """Provider that raises on generate."""

    def generate(self, prompt: str, context: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("provider error")


class _RecordingFailureProvider:
    """Provider that records contexts before raising."""

    def __init__(self) -> None:
        self.contexts: list[dict[str, Any]] = []
        self.calls = 0

    def generate(self, prompt: str, context: dict[str, Any]) -> dict[str, Any]:
        self.calls += 1
        self.contexts.append(context)
        raise RuntimeError("provider timeout")


class _MalformedProvider:
    """Provider that returns non-JSON."""

    def generate(self, prompt: str, context: dict[str, Any]) -> dict[str, Any]:
        return {"message": "I think the user wants to do something."}


import json


class TestDecisionAdvisorAdvise:
    """DecisionAdvisor.advise() core behavior."""

    def test_accepted_when_llm_returns_valid_proposal(self) -> None:
        advisor = DecisionAdvisor(provider=_GoodProvider())
        result = advisor.advise("intent_mode", {"goal": "fix the login bug"})
        assert result.accepted is True
        assert result.source == "llm"
        assert result.payload["mode"] == "task"
        assert result.confidence == 0.9

    def test_rejected_when_validation_fails(self) -> None:
        # Return a proposal missing required fields for decomposition
        provider = _GoodProvider(response=json.dumps({
            "proposal": {"subtasks": "not_a_list"},
            "confidence": 0.8,
            "rationale": "bad data",
        }))
        advisor = DecisionAdvisor(provider=provider)
        result = advisor.advise("decomposition", {"goal": "build the app"})
        assert result.accepted is False
        assert result.source == "validation_rejected"
        assert len(result.validation_reasons) > 0

    def test_fallback_when_no_provider(self) -> None:
        advisor = DecisionAdvisor(provider=None)
        result = advisor.advise("intent_mode", {"goal": "hello"})
        assert result.accepted is False
        assert result.source == "rule_fallback"
        assert "No LLM provider" in result.fallback_reason

    def test_fallback_when_provider_raises(self) -> None:
        advisor = DecisionAdvisor(provider=_BadProvider())
        result = advisor.advise("intent_mode", {"goal": "hello"})
        assert result.accepted is False
        assert result.source == "rule_fallback"

    def test_fallback_when_provider_returns_malformed(self) -> None:
        advisor = DecisionAdvisor(provider=_MalformedProvider())
        result = advisor.advise("intent_mode", {"goal": "hello"})
        assert result.accepted is False
        assert result.source == "rule_fallback"

    def test_unknown_kind_rejected(self) -> None:
        advisor = DecisionAdvisor(provider=_GoodProvider())
        result = advisor.advise("nonexistent_kind", {"goal": "hello"})
        assert result.accepted is False
        assert "Unknown decision kind" in result.rationale

    def test_missing_required_input_rejected(self) -> None:
        advisor = DecisionAdvisor(provider=_GoodProvider())
        result = advisor.advise("context_policy", {"goal": "hello"})
        # context_policy requires "token_budget"
        assert result.accepted is False
        assert "Missing required input" in result.rationale

    def test_result_has_proposal_id(self) -> None:
        advisor = DecisionAdvisor(provider=_GoodProvider())
        result = advisor.advise("intent_mode", {"goal": "hello"})
        assert len(result.proposal_id) == 12

    def test_model_id_passed_through(self) -> None:
        advisor = DecisionAdvisor(provider=_GoodProvider())
        result = advisor.advise("intent_mode", {"goal": "hello"}, model_id="gpt-4o")
        assert result.model_id == "gpt-4o"

    def test_provider_context_receives_runtime_config(self) -> None:
        provider = _GoodProvider()
        advisor = DecisionAdvisor(provider=provider)

        result = advisor.advise(
            "intent_mode",
            {
                "goal": "hello",
                "config": {
                    "provider": {
                        "apiKey": "sk-secret",
                        "timeout": 30,
                        "streamTimeout": 20,
                    }
                },
            },
        )

        assert result.accepted is True
        assert provider.contexts
        assert provider.contexts[0]["config"]["provider"]["apiKey"] == "sk-secret"
        assert provider.contexts[0]["config"]["provider"]["timeout"] == 30
        assert provider.contexts[0]["config"]["provider"]["streamTimeout"] == 20
        assert "sk-secret" not in provider.prompts[0]
        assert "[redacted]" in provider.prompts[0]

    def test_routing_strategy_uses_short_advisor_timeout(self) -> None:
        provider = _RecordingFailureProvider()
        advisor = DecisionAdvisor(provider=provider)

        result = advisor.advise(
            "routing_strategy",
            {
                "goal": "ambiguous change",
                "config": {
                    "provider": {
                        "timeout": 30,
                        "profiles": [
                            {
                                "id": "active",
                                "timeout": 30,
                            }
                        ],
                        "activeProfileId": "active",
                    }
                },
            },
        )

        assert result.source == "rule_fallback"
        assert provider.calls == 1
        provider_config = provider.contexts[0]["config"]["provider"]
        assert provider_config["timeout"] == 3.0
        assert provider_config["profiles"][0]["timeout"] == 3.0

    def test_routing_strategy_failure_enters_cooldown(self) -> None:
        provider = _RecordingFailureProvider()
        now = [100.0]
        advisor = DecisionAdvisor(provider=provider, clock=lambda: now[0])
        context = {
            "goal": "ambiguous change",
            "config": {
                "advisor": {"routingStrategyCooldownSeconds": 60},
                "provider": {"timeout": 30},
            },
        }

        first = advisor.advise("routing_strategy", context)
        second = advisor.advise("routing_strategy", context)

        assert first.source == "rule_fallback"
        assert second.source == "rule_fallback"
        assert "cooldown" in (second.fallback_reason or "")
        assert provider.calls == 1


class TestDecisionAdvisorLLMParsing:
    """LLM response parsing edge cases."""

    def test_parse_json_in_code_fence(self) -> None:
        provider = _GoodProvider(response='```json\n{"proposal": {"mode": "direct"}, "confidence": 0.7, "rationale": "simple question"}\n```')
        advisor = DecisionAdvisor(provider=provider)
        result = advisor.advise("intent_mode", {"goal": "what is 2+2"})
        assert result.accepted is True
        assert result.payload["mode"] == "direct"

    def test_parse_json_with_extra_text(self) -> None:
        provider = _GoodProvider(response='Here is my analysis:\n{"proposal": {"mode": "task"}, "confidence": 0.85, "rationale": "complex task"}\nHope this helps!')
        advisor = DecisionAdvisor(provider=provider)
        result = advisor.advise("intent_mode", {"goal": "fix the bug"})
        assert result.accepted is True

    def test_parse_top_level_proposal_fields(self) -> None:
        provider = _GoodProvider(response='{"mode": "task", "confidence": 0.73, "reasoning": "needs tools"}')
        advisor = DecisionAdvisor(provider=provider)
        result = advisor.advise("intent_mode", {"goal": "create files"})
        assert result.accepted is True
        assert result.payload == {"mode": "task"}
        assert result.rationale == "needs tools"

    def test_parse_first_balanced_json_object_with_braces_in_text(self) -> None:
        provider = _GoodProvider(
            response='Thought: use {braces} in prose.\n{"proposal": {"mode": "direct"}, "confidence": 0.8, "rationale": "answered"}\nDone.'
        )
        advisor = DecisionAdvisor(provider=provider)
        result = advisor.advise("intent_mode", {"goal": "hello"})
        assert result.accepted is True
        assert result.payload["mode"] == "direct"

    def test_parse_assistant_message_content_fallback(self) -> None:
        class _AssistantMessageProvider:
            def generate(self, prompt: str, context: dict[str, Any]) -> dict[str, Any]:
                return {
                    "message": "",
                    "assistant_message": {
                        "content": '{"proposal": {"mode": "direct"}, "confidence": 0.6, "rationale": "short"}',
                    },
                }

        advisor = DecisionAdvisor(provider=_AssistantMessageProvider())
        result = advisor.advise("intent_mode", {"goal": "hello"})
        assert result.accepted is True
        assert result.payload["mode"] == "direct"

    def test_parse_confidence_clamped(self) -> None:
        provider = _GoodProvider(response=json.dumps({
            "proposal": {"mode": "direct"},
            "confidence": 1.5,
            "rationale": "test",
        }))
        advisor = DecisionAdvisor(provider=provider)
        result = advisor.advise("intent_mode", {"goal": "hello"})
        assert result.confidence == 1.0

    def test_parse_confidence_negative_clamped(self) -> None:
        provider = _GoodProvider(response=json.dumps({
            "proposal": {"mode": "direct"},
            "confidence": -0.5,
            "rationale": "test",
        }))
        advisor = DecisionAdvisor(provider=provider)
        result = advisor.advise("intent_mode", {"goal": "hello"})
        assert result.confidence == 0.0


class TestDecisionAdvisorRoutingStrategy:
    """Routing strategy decision via advisor."""

    def test_routing_strategy_accepted(self) -> None:
        provider = _GoodProvider(response=json.dumps({
            "proposal": {"strategy": "react_standard", "scenario": "code_edit"},
            "confidence": 0.88,
            "rationale": "Code editing task",
        }))
        advisor = DecisionAdvisor(provider=provider)
        result = advisor.advise("routing_strategy", {"goal": "refactor the auth module"})
        assert result.accepted is True
        assert result.payload["strategy"] == "react_standard"

    def test_routing_strategy_fallback(self) -> None:
        advisor = DecisionAdvisor(provider=None)
        result = advisor.advise("routing_strategy", {"goal": "hello"})
        assert result.accepted is False
        assert result.source == "rule_fallback"

    def test_routing_strategy_allows_long_smoke_timeout(self) -> None:
        provider = _GoodProvider(response=json.dumps({
            "proposal": {"scenario": "multi_step_task", "strategy": "plan_execute"},
            "confidence": 0.88,
            "rationale": "Complex task needs planning.",
        }))
        advisor = DecisionAdvisor(provider=provider)
        result = advisor.advise(
            "routing_strategy",
            {
                "goal": "build a full-stack system",
                "config": {
                    "advisor": {"routingStrategyTimeoutSeconds": 150},
                    "provider": {
                        "mode": "openai-compatible",
                        "timeout": 30,
                        "profiles": [{"id": "active", "timeout": 30}],
                    },
                },
            },
        )

        provider_config = provider.contexts[0]["config"]["provider"]
        assert result.accepted is True
        assert provider_config["timeout"] == 150
        assert provider_config["profiles"][0]["timeout"] == 150


class TestDecisionAdvisorContextPolicy:
    """Context policy decision via advisor."""

    def test_context_policy_accepted(self) -> None:
        provider = _GoodProvider(response=json.dumps({
            "proposal": {
                "sections": [
                    {"name": "system", "included": True},
                    {"name": "safety", "included": True},
                    {"name": "history", "included": True},
                ],
                "compaction_threshold": 50000,
            },
            "confidence": 0.75,
            "rationale": "Medium complexity task",
        }))
        advisor = DecisionAdvisor(provider=provider)
        result = advisor.advise("context_policy", {"goal": "fix bug", "token_budget": 128000})
        assert result.accepted is True
        assert "sections" in result.payload

    def test_context_policy_missing_budget(self) -> None:
        advisor = DecisionAdvisor(provider=_GoodProvider())
        result = advisor.advise("context_policy", {"goal": "fix bug"})
        assert result.accepted is False
        assert "token_budget" in result.rationale


class TestDecisionAdvisorDecomposition:
    """Decomposition decision via advisor."""

    def test_decomposition_accepted(self) -> None:
        provider = _GoodProvider(response=json.dumps({
            "proposal": {
                "subtasks": [
                    {"id": "s1", "title": "auth", "dependencies": []},
                    {"id": "s2", "title": "db", "dependencies": ["s1"]},
                ],
                "parallel": False,
            },
            "confidence": 0.82,
            "rationale": "Two dependent subtasks",
        }))
        advisor = DecisionAdvisor(provider=provider)
        result = advisor.advise("decomposition", {"goal": "build auth with db"})
        assert result.accepted is True
        assert len(result.payload["subtasks"]) == 2

    def test_decomposition_rejected_invalid_subtasks(self) -> None:
        provider = _GoodProvider(response=json.dumps({
            "proposal": {"subtasks": "not a list"},
            "confidence": 0.9,
            "rationale": "bad",
        }))
        advisor = DecisionAdvisor(provider=provider)
        result = advisor.advise("decomposition", {"goal": "build app"})
        assert result.accepted is False
        assert result.source == "validation_rejected"
