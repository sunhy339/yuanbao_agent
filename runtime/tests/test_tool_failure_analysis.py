from __future__ import annotations

from local_agent_runtime.tools.failure_analysis import build_tool_failure_record


def test_apply_patch_validation_failure_points_to_write_file_fallback() -> None:
    record = build_tool_failure_record(
        tool_name="apply_patch",
        status="validation_failed",
        payload={"summary": "Patch validation failed.", "error": "truncated patch"},
    )

    assert record["failureKind"] == "patch_validation_failed"
    assert "write_file" in record["recoveryHint"]
