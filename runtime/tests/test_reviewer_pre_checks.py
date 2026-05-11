"""Tests for P6.5 reviewer pre-checks: user change overwrite, test gaps, protocol consistency."""

from __future__ import annotations

from local_agent_runtime.orchestration.supervisor import (
    SupervisorOrchestrator,
    _is_test_path,
)


class TestIsTestPath:
    """_is_test_path helper correctly identifies test file paths."""

    def test_pytest_style(self) -> None:
        assert _is_test_path("tests/test_foo.py")

    def test_jest_style(self) -> None:
        assert _is_test_path("src/foo.test.ts")

    def test_spec_style(self) -> None:
        assert _is_test_path("src/bar.spec.js")

    def test_non_test_path(self) -> None:
        assert not _is_test_path("src/main.py")
        assert not _is_test_path("src/utils.ts")


class TestPreCheckUserChangeOverwrite:
    """Pre-check flags when worker overwrites user's uncommitted changes."""

    def test_warns_on_user_modified_files(self) -> None:
        dispatch = {
            "result": {
                "changedFiles": [
                    {"path": "src/main.py", "userModified": True},
                    {"path": "src/utils.py", "userModified": False},
                ],
                "testsRun": [],
                "risks": [],
                "keyFindings": [],
                "summary": "done",
                "status": "success",
            },
        }
        warnings = SupervisorOrchestrator._pre_check_warnings(dispatch)
        assert "user's uncommitted changes" in warnings
        assert "src/main.py" in warnings
        # Non-user-modified file should not appear
        assert "src/utils.py" not in warnings or "userModified" not in warnings

    def test_no_warning_when_no_user_modified(self) -> None:
        dispatch = {
            "result": {
                "changedFiles": [
                    {"path": "src/main.py"},
                    {"path": "src/utils.py"},
                ],
                "testsRun": [],
                "risks": [],
                "keyFindings": [],
                "summary": "done",
                "status": "success",
            },
        }
        warnings = SupervisorOrchestrator._pre_check_warnings(dispatch)
        assert "uncommitted" not in warnings

    def test_no_warning_no_result_key(self) -> None:
        """Empty dispatch (no result key) produces no warnings."""
        # result_data is None → not dict → early return ""
        warnings = SupervisorOrchestrator._pre_check_warnings({"result": None})
        assert warnings == ""


class TestPreCheckTestGap:
    """Pre-check flags when changedFiles exist but no tests."""

    def test_warns_on_code_changes_no_tests(self) -> None:
        dispatch = {
            "result": {
                "changedFiles": [
                    {"path": "src/feature.py"},
                    {"path": "src/helper.py"},
                ],
                "testsRun": [],
                "risks": [],
                "keyFindings": [],
                "summary": "done",
                "status": "success",
            },
        }
        warnings = SupervisorOrchestrator._pre_check_warnings(dispatch)
        assert "test coverage gap" in warnings

    def test_no_warning_when_tests_run(self) -> None:
        dispatch = {
            "result": {
                "changedFiles": [
                    {"path": "src/feature.py"},
                ],
                "testsRun": [{"name": "test_feature", "status": "passed"}],
                "risks": [],
                "keyFindings": [],
                "summary": "done",
                "status": "success",
            },
        }
        warnings = SupervisorOrchestrator._pre_check_warnings(dispatch)
        assert "test coverage gap" not in warnings

    def test_no_warning_when_test_file_changed(self) -> None:
        dispatch = {
            "result": {
                "changedFiles": [
                    {"path": "src/feature.py"},
                    {"path": "tests/test_feature.py"},
                ],
                "testsRun": [],
                "risks": [],
                "keyFindings": [],
                "summary": "done",
                "status": "success",
            },
        }
        warnings = SupervisorOrchestrator._pre_check_warnings(dispatch)
        assert "test coverage gap" not in warnings

    def test_no_warning_when_only_test_files_changed(self) -> None:
        dispatch = {
            "result": {
                "changedFiles": [
                    {"path": "tests/test_utils.py"},
                ],
                "testsRun": [],
                "risks": [],
                "keyFindings": [],
                "summary": "done",
                "status": "success",
            },
        }
        warnings = SupervisorOrchestrator._pre_check_warnings(dispatch)
        assert "test coverage gap" not in warnings


class TestPreCheckProtocolConsistency:
    """Pre-check flags missing required fields in structured result."""

    def test_warns_on_missing_fields(self) -> None:
        dispatch = {
            "result": {
                "changedFiles": [],
                "testsRun": [],
                # missing: summary, status, risks, keyFindings
            },
        }
        warnings = SupervisorOrchestrator._pre_check_warnings(dispatch)
        assert "missing required fields" in warnings
        assert "summary" in warnings
        assert "status" in warnings

    def test_no_warning_when_all_fields_present(self) -> None:
        dispatch = {
            "result": {
                "summary": "All done",
                "status": "success",
                "changedFiles": [],
                "testsRun": [],
                "risks": [],
                "keyFindings": [],
            },
        }
        warnings = SupervisorOrchestrator._pre_check_warnings(dispatch)
        assert "missing required fields" not in warnings

    def test_partial_missing(self) -> None:
        dispatch = {
            "result": {
                "summary": "ok",
                "status": "success",
                "changedFiles": [],
                "testsRun": [],
                "risks": [],
                # missing keyFindings
            },
        }
        warnings = SupervisorOrchestrator._pre_check_warnings(dispatch)
        assert "keyFindings" in warnings


class TestPreCheckCombined:
    """Multiple pre-check warnings can appear together."""

    def test_multiple_warnings(self) -> None:
        dispatch = {
            "result": {
                "changedFiles": [
                    {"path": "src/main.py", "userModified": True},
                ],
                "testsRun": [],
                # missing summary, status, risks, keyFindings
            },
        }
        warnings = SupervisorOrchestrator._pre_check_warnings(dispatch)
        assert "uncommitted changes" in warnings
        assert "test coverage gap" in warnings
        assert "missing required fields" in warnings

    def test_clean_result_no_warnings(self) -> None:
        dispatch = {
            "result": {
                "summary": "All done",
                "status": "success",
                "changedFiles": [{"path": "src/main.py"}],
                "testsRun": [{"name": "test_main", "status": "passed"}],
                "risks": [],
                "keyFindings": ["No issues"],
            },
        }
        warnings = SupervisorOrchestrator._pre_check_warnings(dispatch)
        assert warnings == ""
