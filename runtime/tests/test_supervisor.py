from __future__ import annotations

import json
from typing import Any

import pytest

from local_agent_runtime.orchestration.supervisor import SupervisorOrchestrator
from local_agent_runtime.orchestration.types import OrchestrationResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class MockSubagentService:
    """Records calls and returns fixed results."""

    def __init__(self, *, results: dict[str, str] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._results = results or {}
        self._call_count = 0

    def dispatch(self, params: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(params)
        self._call_count += 1
        title = params.get("title", "")
        summary = self._results.get(title, f"Result for {title} (call {self._call_count})")
        return {"summary": summary, "status": "completed"}


class MockProvider:
    """Returns configurable review responses."""

    def __init__(self, *, decompose_subtasks: list[dict] | None = None, reviews: list[str] | None = None) -> None:
        self._subtasks = decompose_subtasks or [
            {"id": "sub-0", "title": "Step A", "description": "Do A", "dependencies": []},
            {"id": "sub-1", "title": "Step B", "description": "Do B", "dependencies": ["sub-0"]},
        ]
        self._reviews = reviews or []
        self._review_idx = 0
        self._decompose_called = False

    def generate(self, prompt: str, context: dict[str, Any]) -> dict[str, Any]:
        # Decompose call
        if "task decomposition specialist" in prompt and not self._decompose_called:
            self._decompose_called = True
            return {"message": json.dumps(self._subtasks)}

        # Review call
        if "supervisor reviewing" in prompt:
            if self._review_idx < len(self._reviews):
                resp = self._reviews[self._review_idx]
                self._review_idx += 1
            else:
                resp = json.dumps({"approved": True, "feedback": ""})
            return {"message": resp}

        # Synthesis call or other
        return {"message": "Synthesized result"}


def _make_task() -> dict[str, Any]:
    return {"id": "task-1", "status": "running"}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSupervisorApproved:
    def test_all_approved_first_try(self) -> None:
        mock_sub = MockSubagentService()
        mock_prov = MockProvider(
            reviews=[
                json.dumps({"approved": True, "feedback": ""}),
                json.dumps({"approved": True, "feedback": ""}),
            ],
        )
        supervisor = SupervisorOrchestrator(
            provider=mock_prov, subagent_service=mock_sub, max_retries=2,
        )
        result = supervisor.execute(
            "Do A then B", {},
            session_id="sess-1", task=_make_task(),
        )

        assert isinstance(result, OrchestrationResult)
        assert result.success is True
        assert result.review_count == 2
        assert len(mock_sub.calls) == 2  # one per subtask, no retries


class TestSupervisorRetry:
    def test_reject_then_approve(self) -> None:
        mock_sub = MockSubagentService()
        mock_prov = MockProvider(
            reviews=[
                # sub-0: rejected once, then approved
                json.dumps({"approved": False, "feedback": "Need more detail"}),
                json.dumps({"approved": True, "feedback": ""}),
                # sub-1: approved
                json.dumps({"approved": True, "feedback": ""}),
            ],
        )
        supervisor = SupervisorOrchestrator(
            provider=mock_prov, subagent_service=mock_sub, max_retries=2,
        )
        result = supervisor.execute(
            "Do A then B", {},
            session_id="sess-1", task=_make_task(),
        )

        assert result.success is True
        assert result.review_count == 3  # reject + approve for sub-0, approve for sub-1
        assert len(mock_sub.calls) == 3  # sub-0 twice, sub-1 once


class TestSupervisorRetryExhausted:
    def test_max_retries_exhausted(self) -> None:
        mock_sub = MockSubagentService()
        mock_prov = MockProvider(
            reviews=[
                # sub-0: always rejected
                json.dumps({"approved": False, "feedback": "Not good enough"}),
                json.dumps({"approved": False, "feedback": "Still bad"}),
                json.dumps({"approved": False, "feedback": "Final rejection"}),
                # sub-1: approved (independent or skip check)
            ],
        )
        supervisor = SupervisorOrchestrator(
            provider=mock_prov, subagent_service=mock_sub, max_retries=2,
        )
        result = supervisor.execute(
            "Do A then B", {},
            session_id="sess-1", task=_make_task(),
        )

        # sub-0 fails (exhausted retries), sub-1 skipped (dependency failed)
        assert result.success is False
        assert "sub-0" in result.failed


class TestSupervisorPause:
    def test_cooperative_pause(self) -> None:
        pause_counter = {"count": 0}

        def is_paused():
            pause_counter["count"] += 1
            return pause_counter["count"] > 1  # pause after first subtask

        mock_sub = MockSubagentService()
        mock_prov = MockProvider(
            reviews=[
                json.dumps({"approved": True, "feedback": ""}),
            ],
        )
        supervisor = SupervisorOrchestrator(
            provider=mock_prov, subagent_service=mock_sub, max_retries=2,
        )
        result = supervisor.execute(
            "Do A then B", {},
            session_id="sess-1", task=_make_task(),
            is_paused_fn=is_paused,
        )

        assert result.paused is True
        assert len(result.completed) >= 1


class TestReviewerReceivesChildOutput:
    """P6.9: Verify reviewer prompt contains child worker output."""

    def test_review_prompt_includes_child_result(self) -> None:
        """The review LLM call must include the child worker's output text."""
        received_prompts: list[str] = []

        class CapturingProvider:
            def generate(self, prompt: str, context: dict[str, Any]) -> dict[str, Any]:
                received_prompts.append(prompt)
                if "task decomposition specialist" in prompt:
                    return {"message": json.dumps([
                        {"id": "sub-0", "title": "Implement auth", "description": "Write auth module", "dependencies": []},
                    ])}
                if "supervisor reviewing" in prompt:
                    return {"message": json.dumps({"approved": True, "feedback": ""})}
                return {"message": "ok"}

        mock_sub = MockSubagentService(results={"Implement auth": "Created auth.py with JWT validation"})
        supervisor = SupervisorOrchestrator(
            provider=CapturingProvider(), subagent_service=mock_sub, max_retries=2,
        )
        result = supervisor.execute(
            "Implement authentication", {},
            session_id="sess-1", task=_make_task(),
        )

        assert result.success is True

        # Find the review prompt (contains "supervisor reviewing")
        review_prompts = [p for p in received_prompts if "supervisor reviewing" in p]
        assert len(review_prompts) == 1, f"Expected 1 review prompt, got {len(review_prompts)}"

        review_prompt = review_prompts[0]
        # Verify child output is included in the review prompt
        assert "Created auth.py with JWT validation" in review_prompt, \
            "Review prompt must include child worker output"
        assert "Implement auth" in review_prompt, \
            "Review prompt must include sub-task title"
        assert "Write auth module" in review_prompt, \
            "Review prompt must include sub-task description"

    def test_reviewer_can_approve_or_reject_based_on_output(self) -> None:
        """Reviewer should be able to reject based on child output quality."""
        mock_sub = MockSubagentService(results={"Step A": "Incomplete: only stubs"})

        class RejectProvider:
            def __init__(self) -> None:
                self._decompose_called = False

            def generate(self, prompt: str, context: dict[str, Any]) -> dict[str, Any]:
                if "task decomposition specialist" in prompt and not self._decompose_called:
                    self._decompose_called = True
                    return {"message": json.dumps([
                        {"id": "sub-0", "title": "Step A", "description": "Do A", "dependencies": []},
                    ])}
                if "supervisor reviewing" in prompt:
                    # Reject because output says "Incomplete"
                    return {"message": json.dumps({"approved": False, "feedback": "Output is incomplete, implement fully"})}
                return {"message": "ok"}

        supervisor = SupervisorOrchestrator(
            provider=RejectProvider(), subagent_service=mock_sub, max_retries=1,
        )
        result = supervisor.execute(
            "Do A", {},
            session_id="sess-1", task=_make_task(),
        )

        assert result.success is False
        assert "sub-0" in result.failed


class TestReviewStructuredContext:
    """P6.5: Reviewer receives structured context from dispatch_result."""

    def test_review_prompt_includes_changed_files(self) -> None:
        """Review prompt should list changed files from dispatch_result."""
        received_prompts: list[str] = []

        class CapturingProvider:
            def generate(self, prompt: str, context: dict[str, Any]) -> dict[str, Any]:
                received_prompts.append(prompt)
                if "task decomposition specialist" in prompt:
                    return {"message": json.dumps([
                        {"id": "sub-0", "title": "Add tests", "description": "Write unit tests", "dependencies": []},
                    ])}
                if "supervisor reviewing" in prompt:
                    return {"message": json.dumps({"approved": True, "feedback": ""})}
                return {"message": "ok"}

        class StructuredSubagent:
            def dispatch(self, params: dict[str, Any]) -> dict[str, Any]:
                return {
                    "summary": "Added 3 test files",
                    "status": "completed",
                    "result": {
                        "changedFiles": ["test_auth.py", "test_api.py", "test_models.py"],
                        "testsRun": {"passed": 12, "failed": 0},
                    },
                }

        supervisor = SupervisorOrchestrator(
            provider=CapturingProvider(), subagent_service=StructuredSubagent(), max_retries=2,
        )
        result = supervisor.execute(
            "Add unit tests", {},
            session_id="sess-1", task=_make_task(),
        )

        assert result.success is True
        review_prompts = [p for p in received_prompts if "supervisor reviewing" in p]
        assert len(review_prompts) == 1

        prompt = review_prompts[0]
        assert "test_auth.py" in prompt
        assert "Changed files" in prompt
        assert "Tests run" in prompt

    def test_review_prompt_includes_risks(self) -> None:
        """Review prompt should include risks from dispatch_result."""
        received_prompts: list[str] = []

        class CapturingProvider:
            def generate(self, prompt: str, context: dict[str, Any]) -> dict[str, Any]:
                received_prompts.append(prompt)
                if "task decomposition specialist" in prompt:
                    return {"message": json.dumps([
                        {"id": "sub-0", "title": "Refactor DB", "description": "Refactor database layer", "dependencies": []},
                    ])}
                if "supervisor reviewing" in prompt:
                    return {"message": json.dumps({"approved": True, "feedback": ""})}
                return {"message": "ok"}

        class RiskySubagent:
            def dispatch(self, params: dict[str, Any]) -> dict[str, Any]:
                return {
                    "summary": "Refactored DB layer",
                    "status": "completed",
                    "result": {
                        "changedFiles": ["db/models.py", "db/queries.py"],
                        "risks": ["Migration needed for production", "Breaking change in API response format"],
                    },
                }

        supervisor = SupervisorOrchestrator(
            provider=CapturingProvider(), subagent_service=RiskySubagent(), max_retries=2,
        )
        result = supervisor.execute(
            "Refactor database", {},
            session_id="sess-1", task=_make_task(),
        )

        assert result.success is True
        review_prompts = [p for p in received_prompts if "supervisor reviewing" in p]
        prompt = review_prompts[0]
        assert "Risks" in prompt
        assert "Migration needed" in prompt

    def test_review_prompt_without_structured_context(self) -> None:
        """When dispatch_result has no structured data, prompt should still work."""
        received_prompts: list[str] = []

        class CapturingProvider:
            def generate(self, prompt: str, context: dict[str, Any]) -> dict[str, Any]:
                received_prompts.append(prompt)
                if "task decomposition specialist" in prompt:
                    return {"message": json.dumps([
                        {"id": "sub-0", "title": "Task A", "description": "Do A", "dependencies": []},
                    ])}
                if "supervisor reviewing" in prompt:
                    return {"message": json.dumps({"approved": True, "feedback": ""})}
                return {"message": "ok"}

        mock_sub = MockSubagentService()
        supervisor = SupervisorOrchestrator(
            provider=CapturingProvider(), subagent_service=mock_sub, max_retries=2,
        )
        result = supervisor.execute(
            "Do task A", {},
            session_id="sess-1", task=_make_task(),
        )

        assert result.success is True
        review_prompts = [p for p in received_prompts if "supervisor reviewing" in p]
        # Prompt should not contain structured sections when none provided
        prompt = review_prompts[0]
        assert "Changed files" not in prompt
        assert "Risks" not in prompt


class TestBuildStructuredContext:
    """Unit tests for _build_structured_context static method."""

    def test_extracts_changed_files(self) -> None:
        ctx = SupervisorOrchestrator._build_structured_context({
            "result": {"changedFiles": ["a.py", "b.py"]},
        })
        assert "Changed files" in ctx
        assert "a.py" in ctx

    def test_extracts_tests_run(self) -> None:
        ctx = SupervisorOrchestrator._build_structured_context({
            "result": {"testsRun": {"passed": 5, "failed": 1}},
        })
        assert "Tests run" in ctx
        assert "passed" in ctx

    def test_extracts_risks(self) -> None:
        ctx = SupervisorOrchestrator._build_structured_context({
            "result": {"risks": ["Breaking change", "Needs migration"]},
        })
        assert "Risks" in ctx
        assert "Breaking change" in ctx

    def test_extracts_artifacts(self) -> None:
        ctx = SupervisorOrchestrator._build_structured_context({
            "artifacts": ["report.html"],
        })
        assert "Artifacts" in ctx

    def test_empty_dispatch_result(self) -> None:
        ctx = SupervisorOrchestrator._build_structured_context({})
        assert ctx == ""

    def test_no_result_key(self) -> None:
        ctx = SupervisorOrchestrator._build_structured_context({"summary": "done"})
        assert ctx == ""


class TestSupervisorInstanceIsolation:
    def test_review_count_not_shared_across_instances(self) -> None:
        """Two SupervisorOrchestrator instances must have independent review counts."""
        mock_sub1 = MockSubagentService()
        mock_prov1 = MockProvider(
            reviews=[
                json.dumps({"approved": False, "feedback": "Retry"}),
                json.dumps({"approved": True, "feedback": ""}),
                json.dumps({"approved": True, "feedback": ""}),
            ],
        )
        s1 = SupervisorOrchestrator(provider=mock_prov1, subagent_service=mock_sub1, max_retries=2)
        result1 = s1.execute("Task A", {}, session_id="s1", task=_make_task())
        assert result1.review_count == 3  # reject + approve for sub-0, approve for sub-1

        mock_sub2 = MockSubagentService()
        mock_prov2 = MockProvider(
            reviews=[
                json.dumps({"approved": True, "feedback": ""}),
                json.dumps({"approved": True, "feedback": ""}),
            ],
        )
        s2 = SupervisorOrchestrator(provider=mock_prov2, subagent_service=mock_sub2, max_retries=2)
        result2 = s2.execute("Task B", {}, session_id="s2", task=_make_task())
        # s2 should have its own independent count, not inherited from s1
        assert result2.review_count == 2
