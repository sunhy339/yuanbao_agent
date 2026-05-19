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
    _decision_advisor = None
    _tool_registry = type("Registry", (), {"schemas": [{"name": "write_file"}], "_tools": {"write_file": object()}})()

    def _record_tool_recovery_proposals(self, **_kwargs):  # type: ignore[no-untyped-def]
        return []

    def _create_tool_recovery_followup_approval(self, **_kwargs):  # type: ignore[no-untyped-def]
        return None

    def _publish(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        return None


def test_patch_validation_failed_defaults_to_write_file_fallback_tool() -> None:
    harness = _ToolRecoveryHarness()

    decision = harness._select_tool_recovery_action(
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
        failure={
            "name": "apply_patch",
            "status": "validation_failed",
            "summary": "Patch validation failed.",
            "failureKind": "patch_validation_failed",
            "recoveryHint": (
                "Retry with a smaller valid patch, or use write_file for new files and full-file replacements "
                "when that tool is available."
            ),
        },
    )

    assert decision["action"] == "fallback_tool"
    assert decision["source"] == "runtime_fallback"
    assert decision["fallbackTool"]["name"] == "write_file"
    assert decision["fallbackTool"]["available"] is True


def test_patch_validation_failed_keeps_ask_user_for_regular_small_patch() -> None:
    harness = _ToolRecoveryHarness()

    decision = harness._select_tool_recovery_action(
        session_id="sess_1",
        task={"id": "task_1", "sessionId": "sess_1"},
        tool_call_id="call_1",
        tool_name="apply_patch",
        arguments={
            "patchText": (
                "--- a/README.md\n"
                "+++ b/README.md\n"
                "@@ -1 +1 @@\n"
                "-old line\n"
                "+new line\n"
            )
        },
        failure={
            "name": "apply_patch",
            "status": "validation_failed",
            "summary": "Patch validation failed.",
            "failureKind": "patch_validation_failed",
            "recoveryHint": (
                "Retry with a smaller valid patch, or use write_file for new files and full-file replacements "
                "when that tool is available."
            ),
        },
    )

    assert decision["action"] == "ask_user"
    assert "fallbackTool" not in decision
