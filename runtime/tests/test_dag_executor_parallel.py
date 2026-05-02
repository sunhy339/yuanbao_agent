"""Tests for DAGExecutor parallel execution of independent subtasks."""

from __future__ import annotations

import threading
from typing import Any

from local_agent_runtime.planner.dag_executor import DAGExecutor
from local_agent_runtime.planner.types import PlanResult, Subtask


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _SlowSubagentService:
    """Records calls with thread info and simulates work."""

    def __init__(self, results: dict[str, str] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._results = results or {}
        self._lock = threading.Lock()

    def dispatch(self, params: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self.calls.append({**params, "_thread": threading.current_thread().name})
        title = params.get("title", "")
        summary = self._results.get(title, f"Result for {title}")
        return {"summary": summary, "status": "completed"}


class _FailingSubagentService:
    """Fails on specific titles."""

    def __init__(self, fail_titles: set[str]) -> None:
        self._fail_titles = fail_titles
        self.calls: list[dict[str, Any]] = []

    def dispatch(self, params: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(params)
        title = params.get("title", "")
        if title in self._fail_titles:
            raise RuntimeError(f"Failed: {title}")
        return {"summary": f"Result for {title}", "status": "completed"}


def _make_plan(subtasks: list[Subtask]) -> PlanResult:
    """Build a PlanResult with auto-computed DAG and execution order."""
    decomposer = __import__(
        "local_agent_runtime.planner.decomposer", fromlist=["TaskDecomposer"],
    ).TaskDecomposer(__import__(
        "local_agent_runtime.provider.adapter", fromlist=["ProviderAdapter"],
    ).ProviderAdapter())
    dag = decomposer.build_dag(subtasks)
    order = decomposer.topological_sort(dag, [s.id for s in subtasks])
    return PlanResult(subtasks=subtasks, dag=dag, execution_order=order)


# ---------------------------------------------------------------------------
# Parallel execution tests
# ---------------------------------------------------------------------------


class TestParallelGrouping:
    def test_independent_tasks_same_level(self) -> None:
        """Three tasks with no dependencies should be in the same level."""
        subtasks = [
            Subtask(id="a", title="A", description="a", dependencies=[]),
            Subtask(id="b", title="B", description="b", dependencies=[]),
            Subtask(id="c", title="C", description="c", dependencies=[]),
        ]
        plan = _make_plan(subtasks)
        executor = DAGExecutor(_SlowSubagentService())
        levels = executor._group_by_level(plan)

        assert len(levels) == 1
        assert set(levels[0]) == {"a", "b", "c"}

    def test_dependent_tasks_different_levels(self) -> None:
        """Chain A→B→C should produce 3 levels."""
        subtasks = [
            Subtask(id="a", title="A", description="a", dependencies=[]),
            Subtask(id="b", title="B", description="b", dependencies=["a"]),
            Subtask(id="c", title="C", description="c", dependencies=["b"]),
        ]
        plan = _make_plan(subtasks)
        executor = DAGExecutor(_SlowSubagentService())
        levels = executor._group_by_level(plan)

        assert len(levels) == 3
        assert levels[0] == ["a"]
        assert levels[1] == ["b"]
        assert levels[2] == ["c"]

    def test_diamond_produces_two_levels(self) -> None:
        """A→B, A→C, B+C→D should produce: [A], [B,C], [D]."""
        subtasks = [
            Subtask(id="a", title="A", description="a", dependencies=[]),
            Subtask(id="b", title="B", description="b", dependencies=["a"]),
            Subtask(id="c", title="C", description="c", dependencies=["a"]),
            Subtask(id="d", title="D", description="d", dependencies=["b", "c"]),
        ]
        plan = _make_plan(subtasks)
        executor = DAGExecutor(_SlowSubagentService())
        levels = executor._group_by_level(plan)

        assert len(levels) == 3
        assert levels[0] == ["a"]
        assert set(levels[1]) == {"b", "c"}
        assert levels[2] == ["d"]


class TestParallelExecution:
    def test_parallel_tasks_use_multiple_threads(self) -> None:
        """Independent tasks should be dispatched from different threads."""
        subtasks = [
            Subtask(id="a", title="A", description="a", dependencies=[]),
            Subtask(id="b", title="B", description="b", dependencies=[]),
            Subtask(id="c", title="C", description="c", dependencies=[]),
        ]
        plan = _make_plan(subtasks)
        mock = _SlowSubagentService()
        executor = DAGExecutor(mock)
        result = executor.execute(plan, session_id="s1", parent_task_id="t1", max_workers=3)

        assert result["success"] is True
        assert len(mock.calls) == 3
        # ThreadPoolExecutor was used (not the main thread)
        thread_names = {c["_thread"] for c in mock.calls}
        main_thread = threading.current_thread().name
        assert all(t != main_thread for t in thread_names)

    def test_cross_level_dependency_respected(self) -> None:
        """Level-1 tasks must wait for level-0 to finish."""
        subtasks = [
            Subtask(id="a", title="A", description="a", dependencies=[]),
            Subtask(id="b", title="B", description="b", dependencies=["a"]),
        ]
        plan = _make_plan(subtasks)
        mock = _SlowSubagentService()
        executor = DAGExecutor(mock)
        result = executor.execute(plan, session_id="s1", parent_task_id="t1")

        assert result["success"] is True
        # Verify order: A before B
        titles = [c["title"] for c in mock.calls]
        assert titles.index("A") < titles.index("B")

    def test_sibling_failure_does_not_affect_other_sibling(self) -> None:
        """If B fails at level 1, sibling C should still succeed."""
        subtasks = [
            Subtask(id="a", title="A", description="a", dependencies=[]),
            Subtask(id="b", title="B", description="b", dependencies=["a"]),
            Subtask(id="c", title="C", description="c", dependencies=["a"]),
        ]
        plan = _make_plan(subtasks)
        mock = _FailingSubagentService(fail_titles={"B"})
        executor = DAGExecutor(mock)
        result = executor.execute(plan, session_id="s1", parent_task_id="t1")

        assert result["success"] is False
        assert "b" in result["failed"]
        assert "c" in result["completed"]

    def test_failed_dependency_skips_dependent(self) -> None:
        """If B fails, D (which depends on B) should be skipped."""
        subtasks = [
            Subtask(id="a", title="A", description="a", dependencies=[]),
            Subtask(id="b", title="B", description="b", dependencies=["a"]),
            Subtask(id="c", title="C", description="c", dependencies=["a"]),
            Subtask(id="d", title="D", description="d", dependencies=["b", "c"]),
        ]
        plan = _make_plan(subtasks)
        mock = _FailingSubagentService(fail_titles={"B"})
        executor = DAGExecutor(mock)
        result = executor.execute(plan, session_id="s1", parent_task_id="t1")

        assert result["success"] is False
        assert "b" in result["failed"]
        assert "d" in result["failed"]  # skipped because dependency B failed
        assert "c" in result["completed"]

    def test_single_task_does_not_use_thread_pool(self) -> None:
        """A single subtask should execute on the calling thread."""
        subtasks = [
            Subtask(id="a", title="A", description="a", dependencies=[]),
        ]
        plan = _make_plan(subtasks)
        mock = _SlowSubagentService()
        executor = DAGExecutor(mock)
        result = executor.execute(plan, session_id="s1", parent_task_id="t1")

        assert result["success"] is True
        assert len(mock.calls) == 1
        # Single task runs on main thread (no ThreadPoolExecutor)
        assert mock.calls[0]["_thread"] == threading.current_thread().name

    def test_resume_skips_completed_and_failed(self) -> None:
        """Resume should skip already-completed/failed subtasks."""
        subtasks = [
            Subtask(id="a", title="A", description="a", dependencies=[]),
            Subtask(id="b", title="B", description="b", dependencies=[]),
            Subtask(id="c", title="C", description="c", dependencies=["a", "b"]),
        ]
        plan = _make_plan(subtasks)
        mock = _SlowSubagentService()
        executor = DAGExecutor(mock)

        result = executor.execute(
            plan, session_id="s1", parent_task_id="t1",
            completed_ids={"a"}, failed_ids={"b"},
            prior_results={"a": "Done", "b": "Error"},
        )

        # Only C should be dispatched (A completed, B failed)
        # C depends on B which failed, so C is skipped
        assert result["success"] is False
        titles = [c["title"] for c in mock.calls]
        assert "A" not in titles
        assert "B" not in titles


class TestParallelThreadSafety:
    def test_all_results_captured_under_contention(self) -> None:
        """Many parallel tasks should all have their results recorded correctly."""
        subtasks = [
            Subtask(id=f"t{i}", title=f"Task {i}", description=f"do {i}", dependencies=[])
            for i in range(10)
        ]
        plan = _make_plan(subtasks)
        mock = _SlowSubagentService()
        executor = DAGExecutor(mock)
        result = executor.execute(plan, session_id="s1", parent_task_id="t1", max_workers=5)

        assert result["success"] is True
        assert len(result["completed"]) == 10
        assert len(result["failed"]) == 0

    def test_partial_failure_under_contention(self) -> None:
        """With 5 parallel tasks and 2 failing, completed count should be exactly 3."""
        subtasks = [
            Subtask(id=f"t{i}", title=f"Task {i}", description=f"do {i}", dependencies=[])
            for i in range(5)
        ]
        plan = _make_plan(subtasks)
        mock = _FailingSubagentService(fail_titles={"Task 1", "Task 3"})
        executor = DAGExecutor(mock)
        result = executor.execute(plan, session_id="s1", parent_task_id="t1", max_workers=5)

        assert result["success"] is False
        assert len(result["completed"]) == 3
        assert len(result["failed"]) == 2
