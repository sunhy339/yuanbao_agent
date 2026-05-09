"""Tests for P12: Trace Summarizer and Parent Synthesis Contracts."""

from __future__ import annotations

import pytest

from local_agent_runtime.policy.synthesis_contract import (
    validate_synthesis_input,
    validate_synthesis_output,
    validate_trace_summary_input,
    validate_trace_summary_output,
)


# ---------------------------------------------------------------------------
# Trace Summary Input
# ---------------------------------------------------------------------------


class TestTraceSummaryInput:
    def test_valid_minimal(self):
        payload = {
            "parentTaskId": "pt-1",
            "sessionId": "s-1",
            "eventRange": {"afterSeq": 0, "beforeSeq": 10},
        }
        assert validate_trace_summary_input(payload) == []

    def test_valid_with_child_task_ids(self):
        payload = {
            "parentTaskId": "pt-1",
            "sessionId": "s-1",
            "eventRange": {"afterSeq": 0, "beforeSeq": 10},
            "childTaskIds": ["ct-1", "ct-2"],
        }
        assert validate_trace_summary_input(payload) == []

    def test_missing_parent_task_id(self):
        payload = {
            "sessionId": "s-1",
            "eventRange": {"afterSeq": 0, "beforeSeq": 10},
        }
        reasons = validate_trace_summary_input(payload)
        assert any("parentTaskId" in r for r in reasons)

    def test_missing_session_id(self):
        payload = {
            "parentTaskId": "pt-1",
            "eventRange": {"afterSeq": 0, "beforeSeq": 10},
        }
        reasons = validate_trace_summary_input(payload)
        assert any("sessionId" in r for r in reasons)

    def test_missing_event_range(self):
        payload = {
            "parentTaskId": "pt-1",
            "sessionId": "s-1",
        }
        reasons = validate_trace_summary_input(payload)
        assert any("eventRange" in r for r in reasons)

    def test_event_range_missing_after_seq(self):
        payload = {
            "parentTaskId": "pt-1",
            "sessionId": "s-1",
            "eventRange": {"beforeSeq": 10},
        }
        reasons = validate_trace_summary_input(payload)
        assert any("afterSeq" in r for r in reasons)

    def test_event_range_missing_before_seq(self):
        payload = {
            "parentTaskId": "pt-1",
            "sessionId": "s-1",
            "eventRange": {"afterSeq": 0},
        }
        reasons = validate_trace_summary_input(payload)
        assert any("beforeSeq" in r for r in reasons)

    def test_event_range_after_greater_than_before(self):
        payload = {
            "parentTaskId": "pt-1",
            "sessionId": "s-1",
            "eventRange": {"afterSeq": 20, "beforeSeq": 10},
        }
        reasons = validate_trace_summary_input(payload)
        assert any("afterSeq" in r and "beforeSeq" in r for r in reasons)

    def test_event_range_equal_is_valid(self):
        payload = {
            "parentTaskId": "pt-1",
            "sessionId": "s-1",
            "eventRange": {"afterSeq": 10, "beforeSeq": 10},
        }
        assert validate_trace_summary_input(payload) == []

    def test_child_task_ids_not_list(self):
        payload = {
            "parentTaskId": "pt-1",
            "sessionId": "s-1",
            "eventRange": {"afterSeq": 0, "beforeSeq": 10},
            "childTaskIds": "not-a-list",
        }
        reasons = validate_trace_summary_input(payload)
        assert any("childTaskIds" in r for r in reasons)

    def test_all_fields_none(self):
        payload = {
            "parentTaskId": None,
            "sessionId": None,
            "eventRange": None,
        }
        reasons = validate_trace_summary_input(payload)
        assert len(reasons) >= 3


# ---------------------------------------------------------------------------
# Trace Summary Output
# ---------------------------------------------------------------------------


class TestTraceSummaryOutput:
    def test_valid_minimal(self):
        payload = {
            "parentTaskId": "pt-1",
            "eventRange": {"afterSeq": 0, "beforeSeq": 10},
            "summary": "Task completed successfully.",
        }
        assert validate_trace_summary_output(payload) == []

    def test_valid_with_optional_lists(self):
        payload = {
            "parentTaskId": "pt-1",
            "eventRange": {"afterSeq": 0, "beforeSeq": 10},
            "summary": "Mixed results.",
            "failures": ["f1"],
            "retries": ["r1"],
            "generatedArtifacts": ["a1"],
            "reviewDecisions": ["rd1"],
        }
        assert validate_trace_summary_output(payload) == []

    def test_missing_parent_task_id(self):
        payload = {
            "eventRange": {"afterSeq": 0, "beforeSeq": 10},
            "summary": "ok",
        }
        reasons = validate_trace_summary_output(payload)
        assert any("parentTaskId" in r for r in reasons)

    def test_missing_summary(self):
        payload = {
            "parentTaskId": "pt-1",
            "eventRange": {"afterSeq": 0, "beforeSeq": 10},
        }
        reasons = validate_trace_summary_output(payload)
        assert any("summary" in r for r in reasons)

    def test_summary_not_string(self):
        payload = {
            "parentTaskId": "pt-1",
            "eventRange": {"afterSeq": 0, "beforeSeq": 10},
            "summary": 42,
        }
        reasons = validate_trace_summary_output(payload)
        assert any("summary" in r for r in reasons)

    def test_optional_field_not_list(self):
        payload = {
            "parentTaskId": "pt-1",
            "eventRange": {"afterSeq": 0, "beforeSeq": 10},
            "summary": "ok",
            "failures": "not-a-list",
        }
        reasons = validate_trace_summary_output(payload)
        assert any("failures" in r for r in reasons)

    def test_all_optional_fields_not_list(self):
        payload = {
            "parentTaskId": "pt-1",
            "eventRange": {"afterSeq": 0, "beforeSeq": 10},
            "summary": "ok",
            "failures": 1,
            "retries": 2,
            "generatedArtifacts": 3,
            "reviewDecisions": 4,
        }
        reasons = validate_trace_summary_output(payload)
        assert len(reasons) == 4

    def test_missing_event_range(self):
        payload = {
            "parentTaskId": "pt-1",
            "summary": "ok",
        }
        reasons = validate_trace_summary_output(payload)
        assert any("eventRange" in r for r in reasons)


# ---------------------------------------------------------------------------
# Synthesis Input
# ---------------------------------------------------------------------------


class TestSynthesisInput:
    def test_valid(self):
        payload = {
            "parentTaskId": "pt-1",
            "sessionId": "s-1",
            "childOutcomes": [
                {"taskId": "ct-1", "status": "completed"},
                {"taskId": "ct-2", "status": "failed"},
            ],
        }
        assert validate_synthesis_input(payload) == []

    def test_missing_parent_task_id(self):
        payload = {
            "sessionId": "s-1",
            "childOutcomes": [{"taskId": "ct-1", "status": "completed"}],
        }
        reasons = validate_synthesis_input(payload)
        assert any("parentTaskId" in r for r in reasons)

    def test_missing_session_id(self):
        payload = {
            "parentTaskId": "pt-1",
            "childOutcomes": [{"taskId": "ct-1", "status": "completed"}],
        }
        reasons = validate_synthesis_input(payload)
        assert any("sessionId" in r for r in reasons)

    def test_missing_child_outcomes(self):
        payload = {
            "parentTaskId": "pt-1",
            "sessionId": "s-1",
        }
        reasons = validate_synthesis_input(payload)
        assert any("childOutcomes" in r for r in reasons)

    def test_child_outcomes_not_list(self):
        payload = {
            "parentTaskId": "pt-1",
            "sessionId": "s-1",
            "childOutcomes": "not-a-list",
        }
        reasons = validate_synthesis_input(payload)
        assert any("childOutcomes" in r and "list" in r for r in reasons)

    def test_child_outcomes_empty(self):
        payload = {
            "parentTaskId": "pt-1",
            "sessionId": "s-1",
            "childOutcomes": [],
        }
        reasons = validate_synthesis_input(payload)
        assert any("non-empty" in r for r in reasons)

    def test_child_outcome_not_dict(self):
        payload = {
            "parentTaskId": "pt-1",
            "sessionId": "s-1",
            "childOutcomes": ["not-a-dict"],
        }
        reasons = validate_synthesis_input(payload)
        assert any("childOutcomes[0]" in r for r in reasons)

    def test_child_outcome_missing_task_id(self):
        payload = {
            "parentTaskId": "pt-1",
            "sessionId": "s-1",
            "childOutcomes": [{"status": "completed"}],
        }
        reasons = validate_synthesis_input(payload)
        assert any("taskId" in r for r in reasons)

    def test_child_outcome_missing_status(self):
        payload = {
            "parentTaskId": "pt-1",
            "sessionId": "s-1",
            "childOutcomes": [{"taskId": "ct-1"}],
        }
        reasons = validate_synthesis_input(payload)
        assert any("status" in r for r in reasons)

    def test_multiple_child_outcome_errors(self):
        payload = {
            "parentTaskId": "pt-1",
            "sessionId": "s-1",
            "childOutcomes": ["bad", {"status": "completed"}],
        }
        reasons = validate_synthesis_input(payload)
        assert any("childOutcomes[0]" in r for r in reasons)
        assert any("childOutcomes[1]" in r and "taskId" in r for r in reasons)


# ---------------------------------------------------------------------------
# Synthesis Output
# ---------------------------------------------------------------------------


class TestSynthesisOutput:
    def test_valid_empty_lists(self):
        payload = {
            "parentTaskId": "pt-1",
            "completedWork": [],
            "failedWork": [],
            "skippedWork": [],
        }
        assert validate_synthesis_output(payload) == []

    def test_valid_with_completed_work(self):
        payload = {
            "parentTaskId": "pt-1",
            "completedWork": [
                {
                    "artifactIds": ["a1", "a2"],
                    "verifiedArtifactIds": ["a1", "a2"],
                },
            ],
            "failedWork": [{"taskId": "ct-2"}],
            "skippedWork": [],
        }
        assert validate_synthesis_output(payload) == []

    def test_missing_parent_task_id(self):
        payload = {
            "completedWork": [],
            "failedWork": [],
            "skippedWork": [],
        }
        reasons = validate_synthesis_output(payload)
        assert any("parentTaskId" in r for r in reasons)

    def test_missing_completed_work(self):
        payload = {
            "parentTaskId": "pt-1",
            "failedWork": [],
            "skippedWork": [],
        }
        reasons = validate_synthesis_output(payload)
        assert any("completedWork" in r for r in reasons)

    def test_missing_failed_work(self):
        payload = {
            "parentTaskId": "pt-1",
            "completedWork": [],
            "skippedWork": [],
        }
        reasons = validate_synthesis_output(payload)
        assert any("failedWork" in r for r in reasons)

    def test_missing_skipped_work(self):
        payload = {
            "parentTaskId": "pt-1",
            "completedWork": [],
            "failedWork": [],
        }
        reasons = validate_synthesis_output(payload)
        assert any("skippedWork" in r for r in reasons)

    def test_unverified_artifacts_rejected(self):
        """P12: No unverified artifacts claimed as complete."""
        payload = {
            "parentTaskId": "pt-1",
            "completedWork": [
                {
                    "artifactIds": ["a1", "a2", "a3"],
                    "verifiedArtifactIds": ["a1"],
                },
            ],
            "failedWork": [],
            "skippedWork": [],
        }
        reasons = validate_synthesis_output(payload)
        assert any("unverified" in r for r in reasons)
        assert any("a2" in r or "a3" in r for r in reasons)

    def test_completed_work_item_not_dict(self):
        payload = {
            "parentTaskId": "pt-1",
            "completedWork": ["not-a-dict"],
            "failedWork": [],
            "skippedWork": [],
        }
        reasons = validate_synthesis_output(payload)
        assert any("completedWork[0]" in r for r in reasons)

    def test_failed_work_not_list(self):
        payload = {
            "parentTaskId": "pt-1",
            "completedWork": [],
            "failedWork": "not-a-list",
            "skippedWork": [],
        }
        reasons = validate_synthesis_output(payload)
        assert any("failedWork" in r for r in reasons)

    def test_skipped_work_not_list(self):
        payload = {
            "parentTaskId": "pt-1",
            "completedWork": [],
            "failedWork": [],
            "skippedWork": 42,
        }
        reasons = validate_synthesis_output(payload)
        assert any("skippedWork" in r for r in reasons)

    def test_all_verified_ok(self):
        """When all artifact IDs are verified, no errors."""
        payload = {
            "parentTaskId": "pt-1",
            "completedWork": [
                {"artifactIds": ["x"], "verifiedArtifactIds": ["x"]},
                {"artifactIds": [], "verifiedArtifactIds": []},
                {},  # no artifact fields at all
            ],
            "failedWork": [],
            "skippedWork": [],
        }
        assert validate_synthesis_output(payload) == []
