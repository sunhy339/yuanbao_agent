from __future__ import annotations

from typing import Any

import pytest

from local_agent_runtime.planner.dag_executor import DAGExecutor
from local_agent_runtime.planner.types import PlanResult, Subtask


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class MockSubagentService:
    """Records calls and returns fixed results."""

    def __init__(self, *, results: dict[str, str] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._results = results or {}

    def dispatch(self, params: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(params)
        title = params.get("title", "")
        summary = self._results.get(title, f"Result for {title}")
        return {"summary": summary, "status": "completed"}


class FailingSubagentService:
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


class FailedStatusSubagentService:
    def __init__(self, fail_titles: set[str]) -> None:
        self._fail_titles = fail_titles
        self.calls: list[dict[str, Any]] = []

    def dispatch(self, params: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(params)
        title = params.get("title", "")
        if title in self._fail_titles:
            return {
                "status": "failed",
                "summary": f"Failed status: {title}",
                "error": {"message": f"Failed status: {title}"},
            }
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
# Execution tests
# ---------------------------------------------------------------------------


class TestDAGExecutorExecute:
    def test_linear_chain_in_order(self) -> None:
        subtasks = [
            Subtask(id="s0", title="Step 0", description="Do step 0", dependencies=[]),
            Subtask(id="s1", title="Step 1", description="Do step 1", dependencies=["s0"]),
            Subtask(id="s2", title="Step 2", description="Do step 2", dependencies=["s1"]),
        ]
        plan = _make_plan(subtasks)
        mock = MockSubagentService()
        executor = DAGExecutor(mock)
        result = executor.execute(plan, session_id="sess-1", parent_task_id="task-1")

        assert result["success"] is True
        assert len(mock.calls) == 3
        # Verify order: step 0 first, then 1, then 2
        assert mock.calls[0]["title"] == "Step 0"
        assert mock.calls[1]["title"] == "Step 1"
        assert mock.calls[2]["title"] == "Step 2"

    def test_independent_tasks_all_executed(self) -> None:
        subtasks = [
            Subtask(id="a", title="Task A", description="Do A", dependencies=[]),
            Subtask(id="b", title="Task B", description="Do B", dependencies=[]),
            Subtask(id="c", title="Task C", description="Do C", dependencies=[]),
        ]
        plan = _make_plan(subtasks)
        mock = MockSubagentService()
        executor = DAGExecutor(mock)
        result = executor.execute(plan, session_id="sess-1", parent_task_id="task-1")

        assert result["success"] is True
        assert len(mock.calls) == 3

    def test_skips_dependents_of_failed(self) -> None:
        subtasks = [
            Subtask(id="s0", title="Failing", description="fail", dependencies=[]),
            Subtask(id="s1", title="Dependent", description="depends on failing", dependencies=["s0"]),
            Subtask(id="s2", title="Independent", description="independent", dependencies=[]),
        ]
        plan = _make_plan(subtasks)
        mock = FailingSubagentService(fail_titles={"Failing"})
        executor = DAGExecutor(mock)
        result = executor.execute(plan, session_id="sess-1", parent_task_id="task-1")

        assert result["success"] is False
        assert "s0" in result["failed"]
        assert "s1" in result["failed"]  # skipped because dependency failed
        assert "s2" in result["completed"]  # independent, should succeed

    def test_returns_subtask_status(self) -> None:
        subtasks = [
            Subtask(id="a", title="A", description="a", dependencies=[]),
        ]
        plan = _make_plan(subtasks)
        mock = MockSubagentService()
        executor = DAGExecutor(mock)
        result = executor.execute(plan, session_id="sess-1", parent_task_id="task-1")

        subtask = result["subtasks"][0]
        assert subtask.status == "completed"
        assert subtask.result == "Result for A"

    def test_failed_subtask_has_error_result(self) -> None:
        subtasks = [
            Subtask(id="a", title="Boom", description="fail", dependencies=[]),
        ]
        plan = _make_plan(subtasks)
        mock = FailingSubagentService(fail_titles={"Boom"})
        executor = DAGExecutor(mock)
        result = executor.execute(plan, session_id="sess-1", parent_task_id="task-1")

        assert result["success"] is False
        subtask = result["subtasks"][0]
        assert subtask.status == "failed"
        assert "Failed: Boom" in subtask.result

    def test_failed_dispatch_status_is_treated_as_failure(self) -> None:
        subtasks = [
            Subtask(id="a", title="Boom", description="fail", dependencies=[]),
        ]
        plan = _make_plan(subtasks)
        mock = FailedStatusSubagentService(fail_titles={"Boom"})
        executor = DAGExecutor(mock)

        result = executor.execute(plan, session_id="sess-1", parent_task_id="task-1")

        assert result["success"] is False
        subtask = result["subtasks"][0]
        assert subtask.status == "failed"
        assert "Failed status: Boom" in subtask.result

    def test_failed_dispatch_status_preserves_partial_handoff(self) -> None:
        class PartialFailSubagent:
            def __init__(self) -> None:
                self.calls: list[dict[str, Any]] = []

            def dispatch(self, params: dict[str, Any]) -> dict[str, Any]:
                self.calls.append(params)
                return {
                    "status": "failed",
                    "summary": "Timed out.",
                    "error": {
                        "message": "Timed out.",
                        "partialHandoff": {
                            "status": "CHILD_TASK_TIMEOUT",
                            "changedFiles": [{"path": "incident_models.py"}],
                            "pendingVerification": ["python -m py_compile incident_models.py"],
                        },
                    },
                }

        subtasks = [
            Subtask(id="a", title="Models", description="Build models", dependencies=[]),
        ]
        plan = _make_plan(subtasks)
        executor = DAGExecutor(PartialFailSubagent())

        result = executor.execute(plan, session_id="sess-1", parent_task_id="task-1")

        assert result["success"] is False
        assert result["partialHandoffs"][0]["subtaskId"] == "a"
        assert "pendingVerification=python -m py_compile incident_models.py" in result["subtasks"][0].result

    def test_passes_session_and_parent_to_dispatch(self) -> None:
        subtasks = [
            Subtask(id="a", title="A", description="a", dependencies=[]),
        ]
        plan = _make_plan(subtasks)
        mock = MockSubagentService()
        executor = DAGExecutor(mock)
        executor.execute(plan, session_id="my-session", parent_task_id="my-task")

        call = mock.calls[0]
        assert call["sessionId"] == "my-session"
        assert call["taskId"] == "my-task"
        assert call["agentType"] == "worker"
        assert "apply_patch" in call["childToolAllowlist"]

    def test_respects_llm_selected_read_only_subtask_role(self) -> None:
        subtasks = [
            Subtask(
                id="a",
                title="Assess risk",
                description="Assess risks without changing files.",
                dependencies=[],
                agent_type="planner",
            ),
        ]
        plan = _make_plan(subtasks)
        mock = MockSubagentService()
        executor = DAGExecutor(mock)

        executor.execute(plan, session_id="my-session", parent_task_id="my-task")

        call = mock.calls[0]
        assert call["agentType"] == "planner"
        assert "apply_patch" not in call["childToolAllowlist"]

    def test_child_prompt_preserves_parent_goal_when_available(self) -> None:
        subtasks = [
            Subtask(id="a", title="Implement", description="Build the implementation.", dependencies=[]),
        ]
        plan = _make_plan(subtasks)
        mock = MockSubagentService()
        executor = DAGExecutor(mock)

        executor.execute(
            plan,
            session_id="my-session",
            parent_task_id="my-task",
            parent_goal="Create feedback_models.py and at least 2 pytest files.",
        )

        prompt = mock.calls[0]["prompt"]
        assert "[Parent task]" not in prompt
        assert "Subtask: Implement" in prompt
        assert mock.calls[0]["planningPrompt"] == "Build the implementation."

    def test_child_prompt_is_compact_for_execution(self) -> None:
        subtasks = [
            Subtask(id="a", title="Implement", description="Build the implementation.", dependencies=[]),
        ]
        plan = _make_plan(subtasks)
        mock = MockSubagentService()
        executor = DAGExecutor(mock)

        executor.execute(
            plan,
            session_id="my-session",
            parent_task_id="my-task",
            parent_goal="Create feedback_models.py and at least 2 pytest files.",
        )

        prompt = mock.calls[0]["prompt"]
        assert "[Parent task]" not in prompt
        assert "Subtask: Implement" in prompt

    def test_passes_parent_timeout_budget_to_child_dispatch(self) -> None:
        subtasks = [
            Subtask(id="a", title="Implement", description="Build the implementation.", dependencies=[]),
        ]
        plan = _make_plan(subtasks)
        mock = MockSubagentService()
        executor = DAGExecutor(mock)

        executor.execute(
            plan,
            session_id="my-session",
            parent_task_id="my-task",
            child_timeout_ms=600_000,
        )

        assert mock.calls[0]["timeoutMs"] == 600_000


# ---------------------------------------------------------------------------
# Result synthesis tests
# ---------------------------------------------------------------------------


class TestDAGExecutorSynthesis:
    def test_synthesize_includes_all_tasks(self) -> None:
        subtasks = [
            Subtask(id="a", title="A", description="a", status="completed", result="Done A"),
            Subtask(id="b", title="B", description="b", status="completed", result="Done B"),
        ]
        mock = MockSubagentService()
        executor = DAGExecutor(mock)
        summary = executor.synthesize_results(subtasks)
        assert "A" in summary
        assert "Done A" in summary
        assert "B" in summary
        assert "Done B" in summary

    def test_synthesize_marks_failed(self) -> None:
        subtasks = [
            Subtask(id="a", title="A", description="a", status="failed", result="Error"),
        ]
        mock = MockSubagentService()
        executor = DAGExecutor(mock)
        summary = executor.synthesize_results(subtasks)
        assert "FAILED" in summary

    def test_synthesize_marks_skipped(self) -> None:
        subtasks = [
            Subtask(id="a", title="A", description="a", status="skipped", result="Skipped"),
        ]
        mock = MockSubagentService()
        executor = DAGExecutor(mock)
        summary = executor.synthesize_results(subtasks)
        assert "SKIPPED" in summary

    def test_synthesize_shows_success_count(self) -> None:
        subtasks = [
            Subtask(id="a", title="A", description="a", status="completed", result="ok"),
            Subtask(id="b", title="B", description="b", status="completed", result="ok"),
            Subtask(id="c", title="C", description="c", status="failed", result="err"),
        ]
        mock = MockSubagentService()
        executor = DAGExecutor(mock)
        summary = executor.synthesize_results(subtasks)
        assert "2/3 succeeded" in summary
