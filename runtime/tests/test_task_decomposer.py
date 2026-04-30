from __future__ import annotations

import json
from typing import Any

import pytest

from local_agent_runtime.planner.decomposer import TaskDecomposer
from local_agent_runtime.planner.types import Subtask


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class MockProvider:
    """Minimal provider stub that returns canned ``generate()`` responses."""

    def __init__(self, *, response: str | None = None) -> None:
        self._response = response or "[]"

    def generate(self, prompt: str, context: dict[str, Any]) -> dict[str, Any]:
        return {"message": self._response, "prompt": prompt}


# ---------------------------------------------------------------------------
# Decomposition tests
# ---------------------------------------------------------------------------


class TestTaskDecomposerDecompose:
    def test_parses_subtasks_from_json_array(self) -> None:
        raw = json.dumps([
            {"id": "sub-0", "title": "Step A", "description": "Do A", "dependencies": []},
            {"id": "sub-1", "title": "Step B", "description": "Do B", "dependencies": ["sub-0"]},
        ])
        provider = MockProvider(response=raw)
        decomposer = TaskDecomposer(provider)
        result = decomposer.decompose(goal="refactor module")
        assert len(result.subtasks) == 2
        assert result.subtasks[0].id == "sub-0"
        assert result.subtasks[1].dependencies == ["sub-0"]

    def test_parses_fenced_json_block(self) -> None:
        raw = 'Here is the plan:\n```json\n[\n  {"id": "sub-0", "title": "Analyze", "description": "Read files", "dependencies": []}\n]\n```\nDone.'
        provider = MockProvider(response=raw)
        decomposer = TaskDecomposer(provider)
        result = decomposer.decompose(goal="migrate database")
        assert len(result.subtasks) == 1
        assert result.subtasks[0].title == "Analyze"

    def test_fallback_to_single_task_on_invalid_json(self) -> None:
        raw = "I cannot decompose this task."
        provider = MockProvider(response=raw)
        decomposer = TaskDecomposer(provider)
        result = decomposer.decompose(goal="simple task")
        assert len(result.subtasks) == 1
        assert result.subtasks[0].id == "sub-0"
        assert result.subtasks[0].description == "simple task"

    def test_fallback_on_empty_array(self) -> None:
        provider = MockProvider(response="[]")
        decomposer = TaskDecomposer(provider)
        result = decomposer.decompose(goal="do stuff")
        assert len(result.subtasks) == 1  # fallback

    def test_subtasks_missing_id_get_generated(self) -> None:
        raw = json.dumps([
            {"title": "Step A", "description": "Do A", "dependencies": []},
            {"title": "Step B", "description": "Do B", "dependencies": []},
        ])
        provider = MockProvider(response=raw)
        decomposer = TaskDecomposer(provider)
        result = decomposer.decompose(goal="test")
        assert len(result.subtasks) == 2
        assert result.subtasks[0].id == "sub-0"
        assert result.subtasks[1].id == "sub-1"

    def test_context_included_in_prompt(self) -> None:
        captured: dict[str, Any] = {}

        def capture_generate(prompt: str, context: dict[str, Any]) -> dict[str, Any]:
            captured["prompt"] = prompt
            return {"message": json.dumps([{"id": "sub-0", "title": "t", "description": "d", "dependencies": []}])}

        provider = MockProvider()
        provider.generate = capture_generate  # type: ignore[assignment]
        decomposer = TaskDecomposer(provider)
        decomposer.decompose(goal="goal", context="extra info")
        assert "extra info" in captured["prompt"]

    def test_decompose_returns_plan_result(self) -> None:
        raw = json.dumps([
            {"id": "s0", "title": "A", "description": "a", "dependencies": []},
            {"id": "s1", "title": "B", "description": "b", "dependencies": ["s0"]},
        ])
        provider = MockProvider(response=raw)
        decomposer = TaskDecomposer(provider)
        result = decomposer.decompose(goal="test")
        assert result.dag == {"s0": [], "s1": ["s0"]}
        assert result.execution_order == ["s0", "s1"]


# ---------------------------------------------------------------------------
# DAG builder tests
# ---------------------------------------------------------------------------


class TestBuildDAG:
    def test_builds_adjacency_list(self) -> None:
        subtasks = [
            Subtask(id="a", title="A", description="a", dependencies=[]),
            Subtask(id="b", title="B", description="b", dependencies=["a"]),
            Subtask(id="c", title="C", description="c", dependencies=["a", "b"]),
        ]
        decomposer = TaskDecomposer(MockProvider())
        dag = decomposer.build_dag(subtasks)
        assert dag == {"a": [], "b": ["a"], "c": ["a", "b"]}

    def test_empty_dependencies(self) -> None:
        subtasks = [
            Subtask(id="x", title="X", description="x", dependencies=[]),
            Subtask(id="y", title="Y", description="y", dependencies=[]),
        ]
        decomposer = TaskDecomposer(MockProvider())
        dag = decomposer.build_dag(subtasks)
        assert dag == {"x": [], "y": []}


# ---------------------------------------------------------------------------
# Topological sort tests
# ---------------------------------------------------------------------------


class TestTopologicalSort:
    def test_linear_chain(self) -> None:
        decomposer = TaskDecomposer(MockProvider())
        dag = {"a": [], "b": ["a"], "c": ["b"]}
        order = decomposer.topological_sort(dag, ["a", "b", "c"])
        assert order.index("a") < order.index("b") < order.index("c")

    def test_independent_tasks(self) -> None:
        decomposer = TaskDecomposer(MockProvider())
        dag = {"a": [], "b": [], "c": []}
        order = decomposer.topological_sort(dag, ["a", "b", "c"])
        assert set(order) == {"a", "b", "c"}

    def test_diamond_dependency(self) -> None:
        # a → b, a → c, b → d, c → d
        decomposer = TaskDecomposer(MockProvider())
        dag = {"a": [], "b": ["a"], "c": ["a"], "d": ["b", "c"]}
        order = decomposer.topological_sort(dag, ["a", "b", "c", "d"])
        assert order.index("a") < order.index("b")
        assert order.index("a") < order.index("c")
        assert order.index("b") < order.index("d")
        assert order.index("c") < order.index("d")

    def test_detects_cycle(self) -> None:
        decomposer = TaskDecomposer(MockProvider())
        dag = {"a": ["b"], "b": ["a"]}
        with pytest.raises(ValueError, match="cycle"):
            decomposer.topological_sort(dag, ["a", "b"])

    def test_self_cycle(self) -> None:
        decomposer = TaskDecomposer(MockProvider())
        dag = {"a": ["a"]}
        with pytest.raises(ValueError, match="cycle"):
            decomposer.topological_sort(dag, ["a"])

    def test_three_node_cycle(self) -> None:
        decomposer = TaskDecomposer(MockProvider())
        dag = {"a": ["c"], "b": ["a"], "c": ["b"]}
        with pytest.raises(ValueError, match="cycle"):
            decomposer.topological_sort(dag, ["a", "b", "c"])

    def test_unknown_dependency_ignored(self) -> None:
        """Dependencies referencing non-existent IDs are ignored."""
        decomposer = TaskDecomposer(MockProvider())
        dag = {"a": ["nonexistent"]}
        order = decomposer.topological_sort(dag, ["a"])
        assert order == ["a"]
