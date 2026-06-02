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

    def test_readme_only_stays_doc_write(self) -> None:
        decision = self.router.route("Please update the README.md usage section")
        assert decision.scenario == Scenario.DOC_WRITE
        assert decision.skill_id == "doc_writer"
        assert decision.max_steps >= 35

    def test_doc_expert_snake_project_doc_request_gets_doc_budget(self) -> None:
        decision = self.router.route(
            "\u4f7f\u7528\u6587\u6863\u4e13\u5bb6\u7f16\u5199\u4e00\u4e0b"
            "\u5f53\u524d\u8d2a\u5403\u86c7\u9879\u76ee\u7684\u6587\u6863"
        )

        assert decision.scenario == Scenario.DOC_WRITE
        assert decision.skill_id == "doc_writer"
        assert decision.max_steps >= 35

    def test_readme_mentioned_in_development_task_routes_to_code_edit(self) -> None:
        decision = self.router.route(
            "First read README.md, then fix cart.py and add a regression test in "
            "test_cart.py. Run `python -m pytest -q` before summarizing."
        )

        assert decision.scenario == Scenario.CODE_EDIT
        assert decision.skill_id is None
        assert decision.confidence >= 0.80

    def test_generated_python_game_with_readme_routes_to_code_edit(self) -> None:
        decision = self.router.route(
            "Use Python to develop a simple 2D thunder fighter game. Generate "
            "game.py and README.md, then run a syntax check."
        )

        assert decision.scenario == Scenario.CODE_EDIT
        assert decision.skill_id is None
        assert decision.confidence >= 0.80

    def test_chinese_python_game_with_readme_routes_to_code_edit(self) -> None:
        decision = self.router.route(
            "\u7528 Python \u5f00\u53d1\u4e00\u4e2a\u7b80\u5355\u7684 2D "
            "\u96f7\u9706\u6218\u673a\u5c0f\u6e38\u620f\uff0c\u751f\u6210 "
            "game.py \u548c README.md\uff0c\u5e76\u8fd0\u884c\u8bed\u6cd5\u68c0\u67e5\u3002"
        )

        assert decision.scenario == Scenario.CODE_EDIT
        assert decision.skill_id is None
        assert decision.confidence >= 0.80

    def test_chinese_development_goal_without_readme_routes_to_code_edit(self) -> None:
        decision = self.router.route(
            "\u6211\u60f3\u7528 Python \u5f00\u53d1\u4e00\u4e2a\u7b80\u5355\u7684 "
            "2D \u96f7\u9706\u6218\u673a\u5c0f\u6e38\u620f"
        )

        assert decision.scenario == Scenario.CODE_EDIT
        assert decision.skill_id is None
        assert decision.confidence >= 0.80

    def test_static_blog_website_with_readme_routes_to_code_edit(self) -> None:
        decision = self.router.route(
            "\u8bf7\u7528 HTML/CSS/JavaScript \u751f\u6210\u4e00\u4e2a\u6280\u672f"
            "\u535a\u5ba2\u7f51\u7ad9\uff0c\u751f\u6210 index.html\u3001styles.css "
            "\u548c README.md\uff0c\u5e76\u8fd0\u884c\u8f7b\u91cf\u68c0\u67e5\u3002"
        )

        assert decision.scenario == Scenario.CODE_EDIT
        assert decision.skill_id is None
        assert decision.confidence >= 0.80

    def test_blog_website_without_explicit_files_routes_to_code_edit(self) -> None:
        decision = self.router.route(
            "\u751f\u6210\u4e00\u4e2a\u5e26\u6709\u6280\u672f\u6587\u7ae0\u7684"
            "\u9759\u6001\u535a\u5ba2\u7f51\u7ad9"
        )

        assert decision.scenario == Scenario.CODE_EDIT
        assert decision.skill_id is None
        assert decision.confidence >= 0.80

    def test_complex_feedback_plan_with_explicit_agents_routes_to_swarm(self) -> None:
        decision = self.router.route(
            "\u8bbe\u8ba1\u4e00\u4e2a\u7528\u6237\u53cd\u9988\u7cfb\u7edf\uff0c"
            "\u5305\u542b\u524d\u7aef\u5165\u53e3\u3001\u540e\u7aef API\u3001"
            "\u6570\u636e\u5b58\u50a8\u3001\u6821\u9a8c\u3001\u6d4b\u8bd5\u548c "
            "README \u66f4\u65b0\u3002\u8bf7\u505a\u4efb\u52a1\u89c4\u5212\uff0c"
            "\u62c6\u5206\u591a\u4e2a agent \u5e76\u884c\u5904\u7406\uff0c"
            "\u4e0d\u8981\u5b9e\u73b0\u4ee3\u7801\u3002"
        )

        assert decision.scenario == Scenario.SWARM_TASK
        assert decision.strategy == ExecutionStrategy.PLAN_SWARM
        assert decision.enable_planning is True
        assert decision.skill_id is None

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

    def test_greeting_only_routes_to_simple_query(self) -> None:
        decision = self.router.route("hello")
        assert decision.scenario == Scenario.SIMPLE_QUERY
        assert decision.strategy == ExecutionStrategy.REACT_FAST
        assert decision.confidence >= 0.95

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
        decision = self.router.route("delegate this")
        assert decision.strategy == ExecutionStrategy.REACT_STANDARD

    def test_simple_query_uses_react_fast(self) -> None:
        decision = self.router.route("explain closures")
        assert decision.strategy == ExecutionStrategy.REACT_FAST
        assert decision.max_steps <= 20

    def test_work_scenarios_have_room_beyond_simple_loop_defaults(self) -> None:
        doc_decision = self.router.route("Please update the project documentation")
        code_decision = self.router.route("implement a new settings panel")
        debug_decision = self.router.route("debug this crash")
        multi_step_decision = self.router.route("refactor all modules")

        assert doc_decision.max_steps >= 35
        assert code_decision.max_steps >= 35
        assert debug_decision.max_steps >= 45
        assert multi_step_decision.max_steps >= 60


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
