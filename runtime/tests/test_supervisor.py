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
