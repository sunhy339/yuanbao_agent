from __future__ import annotations

from local_agent_runtime.execution.tool_recovery import ToolRecoveryMixin
from local_agent_runtime.tools.failure_analysis import build_tool_failure_record


def test_apply_patch_validation_failure_points_to_write_file_fallback() -> None:
    record = build_tool_failure_record(
        tool_name="apply_patch",
        status="validation_failed",
        payload={"summary": "Patch validation failed.", "error": "truncated patch"},
    )

    assert record["failureKind"] == "patch_validation_failed"
    assert "write_file" in record["recoveryHint"]


class _ToolRecoveryHarness(ToolRecoveryMixin):
    _tool_registry = type("Registry", (), {"schemas": [{"name": "write_file"}], "_tools": {"write_file": object()}})()

    def _publish(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        self.last_publish = {"args": args, "kwargs": kwargs}
        return None


def test_patch_validation_failed_is_annotated_for_model_followup() -> None:
    harness = _ToolRecoveryHarness()
    result = {
        "status": "validation_failed",
        "summary": "Patch validation failed.",
        "error": "truncated patch",
    }

    failure = harness._annotate_failed_tool_recovery(
        session_id="sess_1",
        task={"id": "task_1", "sessionId": "sess_1"},
        tool_call_id="call_1",
        tool_name="apply_patch",
        arguments={
            "patchText": (
                "*** Begin Patch\n"
                "*** Delete File: README.md\n"
                "*** Add File: README.md\n"
                "+# Replaced\n"
                "*** End Patch\n"
            )
        },
        result=result,
    )

    assert failure["failureKind"] == "patch_validation_failed"
    assert result["failureKind"] == "patch_validation_failed"
    assert "write_file" in result["recoveryHint"]
    assert harness.last_publish["kwargs"]["event_type"] == "tool.failure"
    assert harness.last_publish["kwargs"]["visibility"] == "trace"
