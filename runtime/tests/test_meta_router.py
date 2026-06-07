"""Tests for the thin MetaRouter turn policy."""

from __future__ import annotations

from types import SimpleNamespace

from local_agent_runtime.router import MetaRouter, Scenario
from local_agent_runtime.router.types import ExecutionStrategy, RoutingDecision


class _FakeProvider:
    """A fake provider for optional semantic routing tests."""

    def __init__(self, response: dict | None = None) -> None:
        self._response = response
        self.calls = 0

    def generate(self, prompt: str, context: dict) -> dict:
        self.calls += 1
        if self._response is None:
            return {}
        return self._response


def _rule_candidate(decision: RoutingDecision) -> dict:
    hints = decision.metadata.get("intentHints")
    assert isinstance(hints, dict)
    candidate = hints.get("ruleCandidate")
    assert isinstance(candidate, dict)
    return candidate


def _assert_model_first(decision: RoutingDecision) -> None:
    assert decision.scenario == Scenario.FREE_FORM
    assert decision.strategy == ExecutionStrategy.REACT_STANDARD
    assert decision.skill_id is None
    assert decision.enable_planning is False
    assert decision.enable_reflection is False
    assert decision.max_steps >= 35
    assert decision.reasoning.startswith("model-first-default:")


class TestModelFirstRouting:
    """Normal natural-language turns stay model-first ReAct."""

    def setup_method(self) -> None:
        self.router = MetaRouter()

    def test_debug_prompt_is_hint_not_skill_route(self) -> None:
        decision = self.router.route("debug this crash")

        _assert_model_first(decision)
        candidate = _rule_candidate(decision)
        assert candidate["scenario"] == Scenario.DEBUG.value
        assert candidate["skill_id"] == "debugger"

    def test_test_prompt_is_hint_not_skill_route(self) -> None:
        decision = self.router.route("write a test for this")

        _assert_model_first(decision)
        candidate = _rule_candidate(decision)
        assert candidate["scenario"] == Scenario.TEST_WRITE.value
        assert candidate["skill_id"] == "test_writer"

    def test_document_prompt_is_hint_not_doc_writer_route(self) -> None:
        decision = self.router.route("Please update the README.md usage section")

        _assert_model_first(decision)
        candidate = _rule_candidate(decision)
        assert candidate["scenario"] == Scenario.DOC_WRITE.value
        assert candidate["skill_id"] == "doc_writer"

    def test_read_only_document_prompt_is_hint_not_fast_tool_filter(self) -> None:
        decision = self.router.route("Read README.md and summarize it in one sentence")

        _assert_model_first(decision)
        candidate = _rule_candidate(decision)
        assert candidate["scenario"] == Scenario.CODE_SEARCH.value
        assert candidate["skill_id"] is None

    def test_explain_prompt_keeps_normal_tools_available(self) -> None:
        decision = self.router.route("explain what is a decorator")

        _assert_model_first(decision)
        candidate = _rule_candidate(decision)
        assert candidate["scenario"] == Scenario.SIMPLE_QUERY.value

    def test_development_prompt_is_hint_not_worktree_route(self) -> None:
        decision = self.router.route("implement a new settings panel")

        _assert_model_first(decision)
        candidate = _rule_candidate(decision)
        assert candidate["scenario"] == Scenario.CODE_EDIT.value

    def test_explicit_plan_words_do_not_enable_fixed_planner_by_default(self) -> None:
        decision = self.router.route("plan and break down a refactor across all modules")

        _assert_model_first(decision)
        candidate = _rule_candidate(decision)
        assert candidate["scenario"] == Scenario.MULTI_STEP_TASK.value
        assert candidate["enable_planning"] is True
        assert decision.enable_planning is False

    def test_free_form_fallback_is_model_first(self) -> None:
        decision = self.router.route("delegate this")

        _assert_model_first(decision)
        candidate = _rule_candidate(decision)
        assert candidate["scenario"] == Scenario.FREE_FORM.value

    def test_greeting_only_keeps_fast_path(self) -> None:
        decision = self.router.route("hello")

        assert decision.scenario == Scenario.SIMPLE_QUERY
        assert decision.strategy == ExecutionStrategy.REACT_FAST
        assert decision.skill_id is None
        assert decision.enable_planning is False
        assert decision.confidence >= 0.95
        assert "intentHints" not in decision.metadata

    def test_direct_answer_with_no_workspace_tools_keeps_fast_path(self) -> None:
        decision = self.router.route("Hello. Reply in one short sentence and do not read files.")

        assert decision.scenario == Scenario.SIMPLE_QUERY
        assert decision.strategy == ExecutionStrategy.REACT_FAST
        assert decision.skill_id is None
        assert decision.enable_planning is False
        assert "explicit-no-workspace-direct-answer" in decision.reasoning

    def test_direct_chat_capability_prompt_keeps_fast_path_without_tools(self) -> None:
        decision = self.router.route("你好，简单说明一下你能做什么。")

        assert decision.scenario == Scenario.SIMPLE_QUERY
        assert decision.strategy == ExecutionStrategy.REACT_FAST
        assert decision.skill_id is None
        assert decision.enable_planning is False
        assert "direct-chat-no-workspace" in decision.reasoning

    def test_explicit_multi_agent_uses_model_tool_orchestration(self) -> None:
        decision = self.router.route("\u8d77\u591a\u4e2a agent \u4f18\u5316\u8fd9\u4e2a\u9879\u76ee")

        assert decision.scenario == Scenario.SWARM_TASK
        assert decision.strategy == ExecutionStrategy.PLAN_SWARM
        assert decision.skill_id is None
        assert decision.enable_planning is False
        assert decision.metadata["orchestrationMode"] == "model_tools"
        assert decision.metadata["runtime"] == "react_tool_loop"
        assert _rule_candidate(decision)["scenario"] == Scenario.FREE_FORM.value


class TestRoutingDecisionShape:
    """Verify RoutingDecision fields remain populated for callers."""

    def setup_method(self) -> None:
        self.router = MetaRouter()

    def test_decision_has_strategy_max_steps_metadata_and_reasoning(self) -> None:
        decision = self.router.route("debug this")

        assert decision.strategy == ExecutionStrategy.REACT_STANDARD
        assert isinstance(decision.max_steps, int)
        assert decision.max_steps >= 35
        assert "decision_id" in decision.metadata
        assert decision.reasoning


class TestOptionalSemanticRouting:
    """Legacy semantic routing is opt-in, never the default path."""

    def test_provider_is_not_called_without_opt_in(self) -> None:
        provider = _FakeProvider(
            response={"message": '{"scenario": "debug", "confidence": 0.9, "reasoning": "test"}'}
        )
        router = MetaRouter(provider=provider)

        decision = router.route("something ambiguous xyz")

        assert provider.calls == 0
        _assert_model_first(decision)

    def test_provider_can_route_when_explicitly_enabled(self) -> None:
        provider = _FakeProvider(
            response={"message": '{"scenario": "debug", "confidence": 0.9, "reasoning": "test"}'}
        )
        router = MetaRouter(provider=provider)

        decision = router.route(
            "something ambiguous xyz",
            {"config": {"advisor": {"routingStrategyUseForHighConfidence": True}}},
        )

        assert provider.calls == 1
        assert decision.scenario == Scenario.DEBUG
        assert decision.strategy == ExecutionStrategy.SKILL_BASED
        assert decision.skill_id == "debugger"
        assert "llm-match" in decision.reasoning

    def test_provider_malformed_json_falls_back_to_model_first(self) -> None:
        provider = _FakeProvider(response={"message": "not valid json"})
        router = MetaRouter(provider=provider)

        decision = router.route(
            "something ambiguous xyz",
            {"config": {"advisor": {"routingStrategyUseForHighConfidence": True}}},
        )

        assert provider.calls == 1
        _assert_model_first(decision)

    def test_advisor_overplanning_broad_refactor_is_guarded_to_react(self) -> None:
        class OverplanningAdvisor:
            def advise(self, kind: str, _input_context: dict) -> SimpleNamespace:
                assert kind == "routing_strategy"
                return SimpleNamespace(
                    accepted=True,
                    payload={"scenario": "multi_step_task", "strategy": "plan_execute"},
                    rationale="The task is broad.",
                    source="llm",
                    fallback_reason=None,
                    proposal_id="proposal_route",
                    confidence=0.9,
                    validation_reasons=[],
                )

        router = MetaRouter(decision_advisor=OverplanningAdvisor())

        decision = router.route(
            "refactor all modules",
            {"config": {"advisor": {"routingStrategyUseForHighConfidence": True}}},
        )

        assert decision.scenario == Scenario.CODE_EDIT
        assert decision.strategy == ExecutionStrategy.REACT_STANDARD
        assert decision.enable_planning is False
        assert decision.metadata["advisor_candidate"]["strategy"] == "plan_execute"

    def test_advisor_explicit_plan_request_uses_model_tool_orchestration_by_default(self) -> None:
        class PlanningAdvisor:
            def advise(self, kind: str, _input_context: dict) -> SimpleNamespace:
                assert kind == "routing_strategy"
                return SimpleNamespace(
                    accepted=True,
                    payload={"scenario": "multi_step_task", "strategy": "plan_execute"},
                    rationale="The user asked to plan and break down the work.",
                    source="llm",
                    fallback_reason=None,
                    proposal_id="proposal_route",
                    confidence=0.9,
                    validation_reasons=[],
                )

        router = MetaRouter(decision_advisor=PlanningAdvisor())

        decision = router.route(
            "plan and break down a refactor across all modules",
            {"config": {"advisor": {"routingStrategyUseForHighConfidence": True}}},
        )

        assert decision.scenario == Scenario.MULTI_STEP_TASK
        assert decision.strategy == ExecutionStrategy.PLAN_THEN_EXECUTE
        assert decision.enable_planning is False
        assert decision.metadata["orchestrationMode"] == "model_tools"
        assert decision.metadata["runtime"] == "react_tool_loop"
        assert decision.metadata["advisor_candidate"]["strategy"] == "plan_execute"

    def test_advisor_can_request_legacy_plan_execute_with_explicit_flag(self) -> None:
        class PlanningAdvisor:
            def advise(self, kind: str, _input_context: dict) -> SimpleNamespace:
                assert kind == "routing_strategy"
                return SimpleNamespace(
                    accepted=True,
                    payload={
                        "scenario": "multi_step_task",
                        "strategy": "plan_execute",
                        "legacyPlanExecution": True,
                    },
                    rationale="The caller explicitly enabled legacy planner execution.",
                    source="llm",
                    fallback_reason=None,
                    proposal_id="proposal_route",
                    confidence=0.9,
                    validation_reasons=[],
                )

        router = MetaRouter(decision_advisor=PlanningAdvisor())

        decision = router.route(
            "plan and break down a refactor across all modules",
            {"config": {"advisor": {"routingStrategyUseForHighConfidence": True}}},
        )

        assert decision.scenario == Scenario.MULTI_STEP_TASK
        assert decision.strategy == ExecutionStrategy.PLAN_THEN_EXECUTE
        assert decision.enable_planning is True
        assert decision.metadata["legacyPlanExecution"] is True


class TestTokenizer:
    """CJK + Latin tokenization."""

    def test_cjk_characters_split_individually(self) -> None:
        from local_agent_runtime.router.meta_router import _tokenize

        tokens = _tokenize("\u5e2e\u6211\u8c03\u8bd5")
        assert "\u5e2e" in tokens or "\u8c03" in tokens or "\u8bd5" in tokens

    def test_english_words_preserved(self) -> None:
        from local_agent_runtime.router.meta_router import _tokenize

        tokens = _tokenize("debug this error")
        assert "debug" in tokens
        assert "this" in tokens

    def test_mixed_input(self) -> None:
        from local_agent_runtime.router.meta_router import _tokenize

        tokens = _tokenize("\u5e2e\u6211debug\u8fd9\u4e2abug")
        assert "debug" in tokens
        assert "bug" in tokens
