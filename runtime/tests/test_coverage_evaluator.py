from __future__ import annotations

from local_agent_runtime.planner.coverage import CoverageEvaluator
from local_agent_runtime.planner.types import Subtask


# ---------------------------------------------------------------------------
# Coverage evaluation tests
# ---------------------------------------------------------------------------


class TestCoverageEvaluator:
    def test_full_coverage(self) -> None:
        evaluator = CoverageEvaluator()
        goal = "refactor authentication module"
        subtasks = [
            Subtask(id="s0", title="Refactor auth", description="Refactor the authentication module code"),
            Subtask(id="s1", title="Update tests", description="Update tests for authentication"),
        ]
        score = evaluator.evaluate(goal, subtasks)
        assert score >= 0.8

    def test_partial_coverage(self) -> None:
        evaluator = CoverageEvaluator()
        goal = "refactor authentication database migration"
        subtasks = [
            Subtask(id="s0", title="Refactor auth", description="Refactor the authentication code"),
        ]
        score = evaluator.evaluate(goal, subtasks)
        assert 0.1 < score < 1.0

    def test_no_coverage(self) -> None:
        evaluator = CoverageEvaluator()
        goal = "deploy to production"
        subtasks = [
            Subtask(id="s0", title="Write docs", description="Write documentation"),
            Subtask(id="s1", title="Code review", description="Review code changes"),
        ]
        score = evaluator.evaluate(goal, subtasks)
        assert score < 0.5

    def test_empty_goal_returns_full(self) -> None:
        evaluator = CoverageEvaluator()
        score = evaluator.evaluate("", [Subtask(id="s0", title="X", description="x")])
        assert score == 1.0

    def test_empty_subtasks_returns_zero(self) -> None:
        evaluator = CoverageEvaluator()
        score = evaluator.evaluate("refactor code", [])
        assert score == 0.0

    def test_cjk_keywords(self) -> None:
        evaluator = CoverageEvaluator()
        goal = "重构认证模块"
        subtasks = [
            Subtask(id="s0", title="重构", description="重构认证模块代码"),
        ]
        score = evaluator.evaluate(goal, subtasks)
        assert score >= 0.8

    def test_cjk_partial_coverage(self) -> None:
        evaluator = CoverageEvaluator()
        goal = "重构认证模块并添加日志"
        subtasks = [
            Subtask(id="s0", title="重构", description="重构认证模块"),
        ]
        score = evaluator.evaluate(goal, subtasks)
        assert 0.0 < score < 1.0

    def test_mixed_language(self) -> None:
        evaluator = CoverageEvaluator()
        goal = "refactor authentication 认证 module"
        subtasks = [
            Subtask(id="s0", title="Refactor", description="refactor the authentication 认证 module code"),
        ]
        score = evaluator.evaluate(goal, subtasks)
        assert score >= 0.8

    def test_stop_words_filtered(self) -> None:
        evaluator = CoverageEvaluator()
        goal = "the quick brown fox"
        subtasks = [
            Subtask(id="s0", title="Quick brown fox", description="Handle the quick brown fox"),
        ]
        # "the" is a stop word, so it shouldn't affect coverage
        score = evaluator.evaluate(goal, subtasks)
        assert score == 1.0
