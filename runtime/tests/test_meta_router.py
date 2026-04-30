"""Tests for the MetaRouter scenario classifier."""

from __future__ import annotations

from local_agent_runtime.router import MetaRouter, Scenario
from local_agent_runtime.router.types import ExecutionStrategy


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _FakeProvider:
    """A fake provider for testing LLM-based routing."""

    def __init__(self, response: dict | None = None) -> None:
        self._response = response

    def generate(self, prompt: str, context: dict) -> dict:
        if self._response is None:
            return {}
        return self._response


# ---------------------------------------------------------------------------
# Rule-based routing
# ---------------------------------------------------------------------------

class TestRuleBasedRouting:
    """Phase 1: keyword matching should classify goals correctly."""

    def setup_method(self) -> None:
        self.router = MetaRouter()  # no provider → rule-only

    # -- Chinese keywords --

    def test_debug_scenario_from_chinese(self) -> None:
        decision = self.router.route("这个报错是怎么回事")
        assert decision.scenario == Scenario.DEBUG
        assert decision.confidence >= 0.80

    def test_code_review_scenario_from_chinese(self) -> None:
        decision = self.router.route("请帮我审查一下这段代码")
        assert decision.scenario == Scenario.CODE_REVIEW
        assert decision.confidence >= 0.80

    def test_test_write_scenario_from_chinese(self) -> None:
        decision = self.router.route("帮我写单元测试")
        assert decision.scenario == Scenario.TEST_WRITE
        assert decision.confidence >= 0.80

    def test_doc_write_scenario_from_chinese(self) -> None:
        decision = self.router.route("帮我写个文档")
        assert decision.scenario == Scenario.DOC_WRITE
        assert decision.confidence >= 0.80

    def test_code_search_scenario_from_chinese(self) -> None:
        decision = self.router.route("搜索一下这个函数在哪")
        assert decision.scenario == Scenario.CODE_SEARCH
        assert decision.confidence >= 0.80

    def test_simple_query_scenario_from_chinese(self) -> None:
        decision = self.router.route("解释一下什么是闭包")
        assert decision.scenario == Scenario.SIMPLE_QUERY
        assert decision.confidence >= 0.70

    def test_multi_step_from_refactor(self) -> None:
        decision = self.router.route("帮我重构整个模块")
        assert decision.scenario == Scenario.MULTI_STEP_TASK
        assert decision.confidence >= 0.80

    # -- English keywords --

    def test_debug_scenario_from_english(self) -> None:
        decision = self.router.route("debug this crash")
        assert decision.scenario == Scenario.DEBUG
        assert decision.confidence >= 0.80

    def test_code_review_from_english(self) -> None:
        decision = self.router.route("please review my code")
        assert decision.scenario == Scenario.CODE_REVIEW

    def test_test_write_from_english(self) -> None:
        decision = self.router.route("write a test for this")
        assert decision.scenario == Scenario.TEST_WRITE

    def test_code_search_from_english(self) -> None:
        decision = self.router.route("search for the function")
        assert decision.scenario == Scenario.CODE_SEARCH

    def test_simple_query_from_english(self) -> None:
        decision = self.router.route("explain what is a decorator")
        assert decision.scenario == Scenario.SIMPLE_QUERY

    # -- Fallback --

    def test_free_form_fallback(self) -> None:
        decision = self.router.route("delegate this")
        assert decision.scenario == Scenario.FREE_FORM
        assert decision.confidence < 0.80

    def test_empty_goal_fallback(self) -> None:
        decision = self.router.route("")
        assert decision.scenario == Scenario.FREE_FORM


# ---------------------------------------------------------------------------
# RoutingDecision structure
# ---------------------------------------------------------------------------

class TestRoutingDecision:
    """Verify RoutingDecision fields are populated correctly."""

    def setup_method(self) -> None:
        self.router = MetaRouter()

    def test_decision_has_strategy(self) -> None:
        decision = self.router.route("debug this")
        assert decision.strategy in (
            ExecutionStrategy.REACT_FAST,
            ExecutionStrategy.REACT_STANDARD,
            ExecutionStrategy.REACT_WITH_REFLECTION,
            ExecutionStrategy.SKILL_BASED,
            ExecutionStrategy.PLAN_THEN_EXECUTE,
            ExecutionStrategy.PLAN_SUPERVISE,
        )

    def test_decision_has_max_steps(self) -> None:
        decision = self.router.route("debug this")
        assert isinstance(decision.max_steps, int)
        assert decision.max_steps > 0

    def test_decision_has_metadata(self) -> None:
        decision = self.router.route("debug this")
        assert "decision_id" in decision.metadata

    def test_decision_has_reasoning(self) -> None:
        decision = self.router.route("debug this")
        assert decision.reasoning  # non-empty

    def test_debug_uses_skill_based(self) -> None:
        decision = self.router.route("debug this")
        assert decision.strategy == ExecutionStrategy.SKILL_BASED
        assert decision.skill_id == "debugger"

    def test_code_review_uses_skill_based(self) -> None:
        decision = self.router.route("review this code")
        assert decision.strategy == ExecutionStrategy.SKILL_BASED
        assert decision.skill_id == "code_reviewer"

    def test_multi_step_uses_plan_execute(self) -> None:
        decision = self.router.route("refactor all modules")
        assert decision.strategy == ExecutionStrategy.PLAN_THEN_EXECUTE
        assert decision.enable_planning is True

    def test_free_form_uses_react_standard(self) -> None:
        decision = self.router.route("hello world")
        assert decision.strategy == ExecutionStrategy.REACT_STANDARD

    def test_simple_query_uses_react_fast(self) -> None:
        decision = self.router.route("explain closures")
        assert decision.strategy == ExecutionStrategy.REACT_FAST
        assert decision.max_steps <= 10


# ---------------------------------------------------------------------------
# LLM-based routing (Phase 2)
# ---------------------------------------------------------------------------

class TestLLMRouting:
    """When rule confidence is low, LLM should be consulted if available."""

    def test_llm_called_when_rule_confidence_low(self) -> None:
        provider = _FakeProvider(
            response={"message": '{"scenario": "debug", "confidence": 0.9, "reasoning": "test"}'}
        )
        router = MetaRouter(provider=provider)
        decision = router.route("something ambiguous xyz")
        # Should have used LLM result (higher confidence than rule fallback 0.3)
        assert decision.confidence >= 0.5
        assert "llm-match" in decision.reasoning

    def test_no_llm_without_provider(self) -> None:
        router = MetaRouter()
        decision = router.route("something ambiguous xyz")
        assert decision.scenario == Scenario.FREE_FORM
        assert "rule-match" in decision.reasoning

    def test_llm_result_ignored_when_lower_than_rule(self) -> None:
        # Rule should match "debug" with 0.85 confidence
        provider = _FakeProvider(
            response={"message": '{"scenario": "doc_write", "confidence": 0.3, "reasoning": "bad"}'}
        )
        router = MetaRouter(provider=provider)
        decision = router.route("debug this error")
        assert decision.scenario == Scenario.DEBUG  # rule wins
        assert "rule-match" in decision.reasoning

    def test_llm_malformed_json_falls_back_to_rule(self) -> None:
        provider = _FakeProvider(response={"message": "not valid json"})
        router = MetaRouter(provider=provider)
        decision = router.route("something ambiguous xyz")
        assert decision.scenario == Scenario.FREE_FORM

    def test_llm_invalid_scenario_falls_back_to_rule(self) -> None:
        provider = _FakeProvider(
            response={"message": '{"scenario": "nonexistent", "confidence": 0.9, "reasoning": "x"}'}
        )
        router = MetaRouter(provider=provider)
        decision = router.route("something ambiguous xyz")
        assert decision.scenario == Scenario.FREE_FORM


# ---------------------------------------------------------------------------
# Tokenizer
# ---------------------------------------------------------------------------

class TestTokenizer:
    """CJK + Latin tokenization."""

    def test_cjk_characters_split_individually(self) -> None:
        from local_agent_runtime.router.meta_router import _tokenize
        tokens = _tokenize("帮我调试")
        assert "帮" in tokens or "调" in tokens or "试" in tokens

    def test_english_words_preserved(self) -> None:
        from local_agent_runtime.router.meta_router import _tokenize
        tokens = _tokenize("debug this error")
        assert "debug" in tokens
        assert "this" in tokens

    def test_mixed_input(self) -> None:
        from local_agent_runtime.router.meta_router import _tokenize
        tokens = _tokenize("帮我debug这个bug")
        assert "debug" in tokens
        assert "bug" in tokens
