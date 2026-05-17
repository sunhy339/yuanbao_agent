"""Tests for P6.4: worker structured result output.

Verifies that task completion builds and persists a structured result dict
containing summary, status, changedFiles, testsRun, risks, keyFindings.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from local_agent_runtime.event_bus import EventBus
from local_agent_runtime.orchestrator.service import Orchestrator
from local_agent_runtime.store.sqlite_store import SQLiteStore
from local_agent_runtime.tools.registry import ToolRegistry


def _make_runtime(tmp_path: Any) -> SimpleNamespace:
    event_bus = EventBus()
    store = SQLiteStore(str(tmp_path / "runtime.sqlite3"))
    tool_registry = ToolRegistry()

    class DummyProvider:
        def generate(self, prompt: str, context: dict[str, Any]) -> dict[str, Any]:
            return {"text": "ok", "status": "done"}

    orchestrator = Orchestrator(
        store=store, event_bus=event_bus,
        tool_registry=tool_registry, provider=DummyProvider(),
    )
    return SimpleNamespace(orchestrator=orchestrator, store=store, event_bus=event_bus)


class TestStructuredResultStore:
    """Structured result is persisted and retrievable via store."""

    def test_update_task_structured_result(self, tmp_path: Any) -> None:
        """update_task accepts and persists structured_result."""
        store = SQLiteStore(str(tmp_path / "test.sqlite3"))
        task = store.create_task(
            session_id="s1", task_type="main", goal="test", plan=[],
        )
        structured = {
            "summary": "All tests passed",
            "status": "success",
            "changedFiles": [{"path": "src/main.py", "action": "modified"}],
            "testsRun": [{"name": "test_main", "status": "passed"}],
            "risks": [{"type": "security", "description": "input validation", "severity": "medium"}],
            "keyFindings": ["Refactored auth module"],
        }
        updated = store.update_task(task_id=task["id"], structured_result=structured)
        assert updated["structuredResult"] == structured

    def test_get_task_returns_structured_result(self, tmp_path: Any) -> None:
        """get_task returns the structuredResult field."""
        store = SQLiteStore(str(tmp_path / "test.sqlite3"))
        task = store.create_task(
            session_id="s1", task_type="main", goal="test", plan=[],
        )
        structured = {
            "summary": "done",
            "status": "success",
            "changedFiles": [],
            "testsRun": [],
            "risks": [],
            "keyFindings": [],
        }
        store.update_task(task_id=task["id"], structured_result=structured)
        fetched = store.get_task({"taskId": task["id"]})["task"]
        assert fetched["structuredResult"] is not None
        assert fetched["structuredResult"]["summary"] == "done"
        assert fetched["structuredResult"]["status"] == "success"

    def test_default_structured_result_absent(self, tmp_path: Any) -> None:
        """New tasks have no structuredResult by default."""
        store = SQLiteStore(str(tmp_path / "test.sqlite3"))
        task = store.create_task(
            session_id="s1", task_type="main", goal="test", plan=[],
        )
        assert "structuredResult" not in task or task.get("structuredResult") is None

    def test_migration_adds_column(self, tmp_path: Any) -> None:
        """Existing databases get structured_result_json column."""
        store = SQLiteStore(str(tmp_path / "test.sqlite3"))
        columns = {
            row["name"]
            for row in store._conn.execute("PRAGMA table_info(tasks)").fetchall()
        }
        assert "structured_result_json" in columns


class TestStructuredResultBuild:
    """_finalize_task builds structured result from task fields."""

    def test_finalize_builds_structured_result(self, tmp_path: Any) -> None:
        """When _finalize_task completes a running task, structuredResult is populated."""
        rt = _make_runtime(tmp_path)
        store = rt.store

        # Create session and task
        session = store.create_session(workspace_id="w1", title="test session")
        task = store.create_task(
            session_id=session["id"], task_type="main",
            goal="implement feature X", plan=[{"step": 1, "action": "code"}],
        )
        # Set some fields that feed into structured result
        store.update_task(
            task_id=task["id"],
            changed_files=[{"path": "src/feature.py", "action": "created"}],
            tests_run=[{"name": "test_feature", "status": "passed", "duration_ms": 100}],
            risks=[{"type": "compatibility", "description": "may break old API", "severity": "low"}],
        )

        # Simulate _finalize_task logic
        final_summary = "Feature X implemented and tested"
        refreshed = store.get_task({"taskId": task["id"]})["task"]
        structured_result = {
            "summary": final_summary,
            "status": "success",
            "changedFiles": refreshed.get("changedFiles") or [],
            "testsRun": refreshed.get("testsRun") or [],
            "risks": refreshed.get("risks") or [],
            "keyFindings": [],
        }
        completed = store.update_task(
            task_id=task["id"],
            status="completed",
            result_summary=final_summary,
            structured_result=structured_result,
        )

        assert completed["structuredResult"]["summary"] == final_summary
        assert completed["structuredResult"]["status"] == "success"
        assert len(completed["structuredResult"]["changedFiles"]) == 1
        assert completed["structuredResult"]["changedFiles"][0]["path"] == "src/feature.py"
        assert len(completed["structuredResult"]["testsRun"]) == 1
        assert len(completed["structuredResult"]["risks"]) == 1
        assert completed["structuredResult"]["keyFindings"] == []

    def test_finalize_with_empty_task_fields(self, tmp_path: Any) -> None:
        """_finalize_task works when task has no changedFiles/testsRun/risks."""
        rt = _make_runtime(tmp_path)
        store = rt.store

        session = store.create_session(workspace_id="w1", title="test session")
        task = store.create_task(
            session_id=session["id"], task_type="main",
            goal="simple task", plan=[],
        )

        final_summary = "Done quickly"
        structured_result = {
            "summary": final_summary,
            "status": "success",
            "changedFiles": [],
            "testsRun": [],
            "risks": [],
            "keyFindings": [],
        }
        completed = store.update_task(
            task_id=task["id"],
            status="completed",
            result_summary=final_summary,
            structured_result=structured_result,
        )

        assert completed["structuredResult"]["summary"] == final_summary
        assert completed["structuredResult"]["changedFiles"] == []
        assert completed["structuredResult"]["testsRun"] == []
        assert completed["structuredResult"]["risks"] == []


class TestCompletionHardGate:
    def test_write_task_summary_only_waits_for_completion_review(self, tmp_path: Any) -> None:
        rt = _make_runtime(tmp_path)
        store = rt.store
        workspace = store.upsert_workspace(str(tmp_path / "project"))
        session = store.create_session(workspace_id=workspace["id"], title="completion gate")
        task = store.create_task(
            session_id=session["id"],
            task_type="edit",
            goal="modify the implementation",
            plan=[],
            routing={"scenario": "code_edit"},
        )

        result = rt.orchestrator._complete_task(
            session_id=session["id"],
            task=task,
            summary="I changed the implementation.",
            context={"routing": {"scenario": "code_edit"}},
            skip_reflection=True,
        )

        assert result["status"] == "waiting_approval"
        assert result["structuredResult"]["status"] == "needs_review"
        assert result["structuredResult"]["completionGate"]["status"] == "needs_user_review"
        assert result["structuredResult"]["completionEvidence"]["evidenceLevel"] == "summary_only"
        approvals = store._conn.execute(
            "SELECT * FROM approvals WHERE task_id = ? AND kind = ?",
            (task["id"], "completion_review"),
        ).fetchall()
        assert len(approvals) == 1

    def test_no_approval_mode_skips_completion_review(self, tmp_path: Any) -> None:
        rt = _make_runtime(tmp_path)
        store = rt.store
        workspace = store.upsert_workspace(str(tmp_path / "project"))
        session = store.create_session(workspace_id=workspace["id"], title="completion gate")
        task = store.create_task(
            session_id=session["id"],
            task_type="edit",
            goal="modify the implementation",
            plan=[],
            routing={"scenario": "code_edit"},
        )

        result = rt.orchestrator._complete_task(
            session_id=session["id"],
            task=task,
            summary="I changed the implementation.",
            context={
                "routing": {"scenario": "code_edit"},
                "config": {"policy": {"approvalMode": "none"}},
            },
            skip_reflection=True,
        )

        assert result["status"] == "completed"
        assert result["structuredResult"]["completionEvidence"]["evidenceLevel"] == "summary_only"
        approvals = store._conn.execute(
            "SELECT * FROM approvals WHERE task_id = ? AND kind = ?",
            (task["id"], "completion_review"),
        ).fetchall()
        assert approvals == []

    def test_write_task_with_runtime_evidence_without_verification_waits_for_review(self, tmp_path: Any) -> None:
        rt = _make_runtime(tmp_path)
        store = rt.store
        workspace = store.upsert_workspace(str(tmp_path / "project"))
        session = store.create_session(workspace_id=workspace["id"], title="completion gate")
        task = store.create_task(
            session_id=session["id"],
            task_type="edit",
            goal="modify the implementation",
            plan=[],
            routing={"scenario": "code_edit"},
        )
        task = store.update_task(
            task_id=task["id"],
            changed_files=[{"path": "src/feature.py", "action": "modified"}],
        )

        result = rt.orchestrator._complete_task(
            session_id=session["id"],
            task=task,
            summary="I changed src/feature.py.",
            context={"routing": {"scenario": "code_edit"}},
            skip_reflection=True,
        )

        assert result["status"] == "waiting_approval"
        assert result["structuredResult"]["status"] == "needs_review"
        assert result["structuredResult"]["completionGate"]["status"] == "needs_verification"
        assert result["structuredResult"]["completionEvidence"]["evidenceLevel"] == "runtime_evidence"
        approvals = store._conn.execute(
            "SELECT * FROM approvals WHERE task_id = ? AND kind = ?",
            (task["id"], "completion_review"),
        ).fetchall()
        assert len(approvals) == 1

    def test_failed_verification_blocks_completion(self, tmp_path: Any) -> None:
        rt = _make_runtime(tmp_path)
        store = rt.store
        workspace = store.upsert_workspace(str(tmp_path / "project"))
        session = store.create_session(workspace_id=workspace["id"], title="completion gate")
        task = store.create_task(
            session_id=session["id"],
            task_type="edit",
            goal="modify the implementation",
            plan=[],
            routing={"scenario": "code_edit"},
        )
        task = store.update_task(
            task_id=task["id"],
            changed_files=[{"path": "src/feature.py", "action": "modified"}],
            verification=[{"name": "pytest", "status": "failed", "summary": "1 failed"}],
        )

        result = rt.orchestrator._complete_task(
            session_id=session["id"],
            task=task,
            summary="Implementation finished, but tests failed.",
            context={"routing": {"scenario": "code_edit"}},
            skip_reflection=True,
        )

        assert result["status"] == "failed"
        assert result["errorCode"] == "COMPLETION_EVIDENCE_INSUFFICIENT"
        assert "verification failed" in result["resultSummary"].lower()
        approvals = store._conn.execute(
            "SELECT * FROM approvals WHERE task_id = ? AND kind = ?",
            (task["id"], "completion_review"),
        ).fetchall()
        assert approvals == []

    def test_write_task_with_passed_verification_completes(self, tmp_path: Any) -> None:
        rt = _make_runtime(tmp_path)
        store = rt.store
        workspace = store.upsert_workspace(str(tmp_path / "project"))
        session = store.create_session(workspace_id=workspace["id"], title="completion gate")
        task = store.create_task(
            session_id=session["id"],
            task_type="edit",
            goal="modify the implementation",
            plan=[],
            routing={"scenario": "code_edit"},
        )
        task = store.update_task(
            task_id=task["id"],
            changed_files=[{"path": "src/feature.py", "action": "modified"}],
            verification=[{"name": "pytest", "status": "passed", "summary": "all passed"}],
        )

        result = rt.orchestrator._complete_task(
            session_id=session["id"],
            task=task,
            summary="Implementation finished and tests passed.",
            context={"routing": {"scenario": "code_edit"}},
            skip_reflection=True,
        )

        assert result["status"] == "completed"
        assert result["structuredResult"]["completionEvidence"]["evidenceLevel"] == "verified"
        approvals = store._conn.execute(
            "SELECT * FROM approvals WHERE task_id = ? AND kind = ?",
            (task["id"], "completion_review"),
        ).fetchall()
        assert approvals == []

    def test_tool_result_py_compile_evidence_completes_without_review(self, tmp_path: Any) -> None:
        rt = _make_runtime(tmp_path)
        store = rt.store
        workspace = store.upsert_workspace(str(tmp_path / "project"))
        session = store.create_session(workspace_id=workspace["id"], title="completion gate")
        task = store.create_task(
            session_id=session["id"],
            task_type="edit",
            goal="create a python file and run a syntax check",
            plan=[],
            routing={"scenario": "code_edit"},
        )
        command_log = store.create_command_log(
            task_id=task["id"],
            command="python -m py_compile game.py",
            cwd=".",
            shell="powershell",
        )
        store.update_command_log(command_log["id"], status="completed", exit_code=0)

        result = rt.orchestrator._complete_task(
            session_id=session["id"],
            task=task,
            summary="Created game.py and py_compile passed.",
            context={"routing": {"scenario": "code_edit"}},
            tool_results=[
                {
                    "name": "write_file",
                    "result": {"status": "written", "path": "game.py", "bytesWritten": 100},
                },
                {
                    "name": "run_command",
                    "result": {
                        "status": "completed",
                        "exitCode": 0,
                    },
                },
            ],
            skip_reflection=True,
        )

        assert result["status"] == "completed"
        evidence = result["structuredResult"]["completionEvidence"]
        assert evidence["evidenceLevel"] == "verified"
        assert evidence["changedFiles"][0]["path"] == "game.py"
        assert evidence["testsRun"][0]["status"] == "passed"
        approvals = store._conn.execute(
            "SELECT * FROM approvals WHERE task_id = ? AND kind = ?",
            (task["id"], "completion_review"),
        ).fetchall()
        assert approvals == []

    def test_failed_acceptance_criteria_waits_for_review(self, tmp_path: Any) -> None:
        rt = _make_runtime(tmp_path)
        store = rt.store
        workspace = store.upsert_workspace(str(tmp_path / "project"))
        session = store.create_session(workspace_id=workspace["id"], title="completion gate")
        task = store.create_task(
            session_id=session["id"],
            task_type="edit",
            goal="modify the implementation",
            plan=[],
            acceptance_criteria=["Feature works", "Docs are updated"],
            routing={"scenario": "code_edit"},
        )
        task = store.update_task(
            task_id=task["id"],
            changed_files=[{"path": "src/feature.py", "action": "modified"}],
            verification=[{"name": "pytest", "status": "passed", "summary": "all passed"}],
        )
        task["acceptanceResults"] = [
            {"criterion": "Feature works", "status": "supported"},
            {"criterion": "Docs are updated", "status": "failed"},
        ]

        result = rt.orchestrator._complete_task(
            session_id=session["id"],
            task=task,
            summary="Implementation finished and tests passed.",
            context={"routing": {"scenario": "code_edit"}},
            skip_reflection=True,
        )

        evidence = result["structuredResult"]["completionEvidence"]
        assert result["status"] == "waiting_approval"
        assert result["structuredResult"]["completionGate"]["status"] == "needs_acceptance_review"
        assert evidence["counts"]["failedAcceptanceCriteria"] == 1
        assert evidence["acceptance"][1]["status"] == "failed"

    def test_missing_explicit_acceptance_criteria_waits_for_review(self, tmp_path: Any) -> None:
        rt = _make_runtime(tmp_path)
        store = rt.store
        workspace = store.upsert_workspace(str(tmp_path / "project"))
        session = store.create_session(workspace_id=workspace["id"], title="completion gate")
        task = store.create_task(
            session_id=session["id"],
            task_type="edit",
            goal="modify the implementation",
            plan=[],
            acceptance_criteria=["Feature works", "Docs are updated"],
            routing={"scenario": "code_edit"},
        )
        task = store.update_task(
            task_id=task["id"],
            changed_files=[{"path": "src/feature.py", "action": "modified"}],
            verification=[{"name": "pytest", "status": "passed", "summary": "all passed"}],
        )
        task["acceptanceResults"] = [
            {"criterion": "Feature works", "status": "supported"},
        ]

        result = rt.orchestrator._complete_task(
            session_id=session["id"],
            task=task,
            summary="Implementation finished and tests passed.",
            context={"routing": {"scenario": "code_edit"}},
            skip_reflection=True,
        )

        evidence = result["structuredResult"]["completionEvidence"]
        assert result["status"] == "waiting_approval"
        assert result["structuredResult"]["completionGate"]["status"] == "needs_acceptance_review"
        assert evidence["counts"]["unverifiedAcceptanceCriteria"] == 1
        assert evidence["acceptance"][1]["source"] == "explicit_missing"

    def test_supported_acceptance_criteria_with_passed_verification_completes(self, tmp_path: Any) -> None:
        rt = _make_runtime(tmp_path)
        store = rt.store
        workspace = store.upsert_workspace(str(tmp_path / "project"))
        session = store.create_session(workspace_id=workspace["id"], title="completion gate")
        task = store.create_task(
            session_id=session["id"],
            task_type="edit",
            goal="modify the implementation",
            plan=[],
            acceptance_criteria=["Feature works", "Docs are updated"],
            routing={"scenario": "code_edit"},
        )
        task = store.update_task(
            task_id=task["id"],
            changed_files=[{"path": "src/feature.py", "action": "modified"}],
            verification=[{"name": "pytest", "status": "passed", "summary": "all passed"}],
        )
        task["acceptanceCriteriaResults"] = {
            "Feature works": "supported",
            "Docs are updated": "verified",
        }

        result = rt.orchestrator._complete_task(
            session_id=session["id"],
            task=task,
            summary="Implementation finished and tests passed.",
            context={"routing": {"scenario": "code_edit"}},
            skip_reflection=True,
        )

        evidence = result["structuredResult"]["completionEvidence"]
        assert result["status"] == "completed"
        assert evidence["counts"]["acceptedAcceptanceCriteria"] == 2
        assert evidence["counts"]["failedAcceptanceCriteria"] == 0
        assert evidence["counts"]["unverifiedAcceptanceCriteria"] == 0

    def test_unresolved_failed_tool_result_waits_for_review(self, tmp_path: Any) -> None:
        rt = _make_runtime(tmp_path)
        store = rt.store
        workspace = store.upsert_workspace(str(tmp_path / "project"))
        session = store.create_session(workspace_id=workspace["id"], title="completion gate")
        task = store.create_task(
            session_id=session["id"],
            task_type="edit",
            goal="modify the implementation",
            plan=[],
            routing={"scenario": "code_edit"},
        )

        result = rt.orchestrator._complete_task(
            session_id=session["id"],
            task=task,
            summary="Implementation finished after a failed command.",
            context={"routing": {"scenario": "code_edit"}},
            tool_results=[
                {
                    "name": "run_command",
                    "result": {
                        "status": "completed",
                        "command": "pytest",
                        "exitCode": 1,
                        "summary": "tests failed",
                    },
                }
            ],
            skip_reflection=True,
        )

        evidence = result["structuredResult"]["completionEvidence"]
        assert result["status"] == "waiting_approval"
        assert result["structuredResult"]["completionGate"]["status"] == "needs_tool_review"
        assert evidence["counts"]["failedToolResults"] == 1
        assert evidence["unresolvedToolFailures"][0]["name"] == "run_command"

    def test_resolved_failed_tool_result_with_passed_verification_completes(self, tmp_path: Any) -> None:
        rt = _make_runtime(tmp_path)
        store = rt.store
        workspace = store.upsert_workspace(str(tmp_path / "project"))
        session = store.create_session(workspace_id=workspace["id"], title="completion gate")
        task = store.create_task(
            session_id=session["id"],
            task_type="edit",
            goal="modify the implementation",
            plan=[],
            routing={"scenario": "code_edit"},
        )
        task = store.update_task(
            task_id=task["id"],
            changed_files=[{"path": "src/feature.py", "action": "modified"}],
            verification=[{"name": "pytest", "status": "passed", "summary": "all passed"}],
        )

        result = rt.orchestrator._complete_task(
            session_id=session["id"],
            task=task,
            summary="Implementation finished after fixing the failed command.",
            context={"routing": {"scenario": "code_edit"}},
            tool_results=[
                {
                    "name": "run_command",
                    "result": {
                        "status": "completed",
                        "command": "pytest",
                        "exitCode": 1,
                        "summary": "tests failed",
                    },
                },
                {
                    "name": "run_command",
                    "result": {
                        "status": "completed",
                        "command": "pytest",
                        "exitCode": 0,
                        "summary": "tests passed",
                    },
                },
            ],
            skip_reflection=True,
        )

        evidence = result["structuredResult"]["completionEvidence"]
        assert result["status"] == "completed"
        assert evidence["counts"]["failedToolResults"] == 0
        assert evidence["counts"]["resolvedFailedToolResults"] == 1

    def test_equivalent_file_listing_failure_after_success_completes(self, tmp_path: Any) -> None:
        rt = _make_runtime(tmp_path)
        store = rt.store
        workspace = store.upsert_workspace(str(tmp_path / "project"))
        session = store.create_session(workspace_id=workspace["id"], title="completion gate")
        task = store.create_task(
            session_id=session["id"],
            task_type="edit",
            goal="generate a static blog website",
            plan=[],
            routing={"scenario": "code_edit"},
        )

        result = rt.orchestrator._complete_task(
            session_id=session["id"],
            task=task,
            summary="Generated the static website files and checked they exist.",
            context={"routing": {"scenario": "code_edit"}},
            tool_results=[
                {
                    "name": "write_file",
                    "result": {"status": "completed", "path": "index.html"},
                },
                {
                    "name": "write_file",
                    "result": {"status": "completed", "path": "styles.css"},
                },
                {
                    "name": "write_file",
                    "result": {"status": "completed", "path": "app.js"},
                },
                {
                    "name": "run_command",
                    "result": {
                        "status": "completed",
                        "command": "Get-ChildItem -Name index.html, styles.css, app.js, README.md",
                        "exitCode": 0,
                        "summary": "files exist",
                    },
                },
                {
                    "name": "run_command",
                    "result": {
                        "status": "completed",
                        "command": "ls index.html styles.css app.js README.md",
                        "exitCode": 1,
                        "summary": "PowerShell argument form failed",
                    },
                },
            ],
            skip_reflection=True,
        )

        evidence = result["structuredResult"]["completionEvidence"]
        assert result["status"] == "completed"
        assert evidence["counts"]["failedToolResults"] == 0
        assert evidence["counts"]["resolvedFailedToolResults"] == 1

    def test_code_change_with_only_structural_verification_waits_for_review(self, tmp_path: Any) -> None:
        rt = _make_runtime(tmp_path)
        store = rt.store
        workspace = store.upsert_workspace(str(tmp_path / "project"))
        session = store.create_session(workspace_id=workspace["id"], title="completion gate")
        task = store.create_task(
            session_id=session["id"],
            task_type="edit",
            goal="modify the implementation",
            plan=[],
            routing={"scenario": "code_edit"},
        )
        task = store.update_task(
            task_id=task["id"],
            changed_files=[{"path": "src/feature.py", "action": "modified"}],
            verification=[
                {"name": "git_status", "status": "passed", "summary": "working tree checked"},
                {"name": "git_diff", "status": "passed", "summary": "diff checked"},
            ],
        )

        result = rt.orchestrator._complete_task(
            session_id=session["id"],
            task=task,
            summary="Implementation finished and git checks passed.",
            context={"routing": {"scenario": "code_edit"}},
            skip_reflection=True,
        )

        assert result["status"] == "waiting_approval"
        assert result["structuredResult"]["completionGate"]["status"] == "needs_verification"

    def test_code_change_with_targeted_verification_completes(self, tmp_path: Any) -> None:
        rt = _make_runtime(tmp_path)
        store = rt.store
        workspace = store.upsert_workspace(str(tmp_path / "project"))
        session = store.create_session(workspace_id=workspace["id"], title="completion gate")
        task = store.create_task(
            session_id=session["id"],
            task_type="edit",
            goal="modify the implementation",
            plan=[],
            routing={"scenario": "code_edit"},
        )
        task = store.update_task(
            task_id=task["id"],
            changed_files=[{"path": "src/feature.py", "action": "modified"}],
            verification=[{"command": "pytest runtime/tests/test_feature.py", "status": "passed", "summary": "all passed"}],
        )

        result = rt.orchestrator._complete_task(
            session_id=session["id"],
            task=task,
            summary="Implementation finished and pytest passed.",
            context={"routing": {"scenario": "code_edit"}},
            skip_reflection=True,
        )

        assert result["status"] == "completed"
        assert result["structuredResult"]["completionEvidence"]["evidenceLevel"] == "verified"

    def test_code_change_with_successful_test_command_completes(self, tmp_path: Any) -> None:
        rt = _make_runtime(tmp_path)
        store = rt.store
        workspace = store.upsert_workspace(str(tmp_path / "project"))
        session = store.create_session(workspace_id=workspace["id"], title="completion gate")
        task = store.create_task(
            session_id=session["id"],
            task_type="edit",
            goal="fix a Python bug",
            plan=[],
            routing={"scenario": "code_edit"},
        )
        task = store.update_task(
            task_id=task["id"],
            changed_files=[{"path": "calc.py", "action": "modified"}],
            commands=[
                {
                    "id": "cmd_pytest",
                    "command": "python -m pytest -q",
                    "status": "completed",
                    "exitCode": 0,
                    "summary": "1 passed",
                }
            ],
        )

        result = rt.orchestrator._complete_task(
            session_id=session["id"],
            task=task,
            summary="Implementation finished and pytest passed.",
            context={"routing": {"scenario": "code_edit"}},
            skip_reflection=True,
        )

        evidence = result["structuredResult"]["completionEvidence"]
        assert result["status"] == "completed"
        assert evidence["evidenceLevel"] == "verified"
        assert evidence["verificationRequirements"]["required"] == ["python"]
        assert evidence["verificationRequirements"]["missing"] == []
        assert evidence["testsRun"][0]["command"] == "python -m pytest -q"

    def test_completion_summary_strips_model_tool_call_markup(self, tmp_path: Any) -> None:
        rt = _make_runtime(tmp_path)
        store = rt.store
        workspace = store.upsert_workspace(str(tmp_path / "project"))
        session = store.create_session(workspace_id=workspace["id"], title="completion summary")
        task = store.create_task(
            session_id=session["id"],
            task_type="edit",
            goal="fix a Python bug",
            plan=[],
            routing={"scenario": "code_edit"},
        )
        task = store.update_task(
            task_id=task["id"],
            changed_files=[{"path": "score.py", "action": "modified"}],
            commands=[
                {
                    "id": "cmd_pytest",
                    "command": "python -m pytest -q",
                    "status": "completed",
                    "exitCode": 0,
                    "summary": "3 passed",
                }
            ],
        )

        result = rt.orchestrator._complete_task(
            session_id=session["id"],
            task=task,
            summary=(
                "<tool_call>run_command<arg_key>command</arg_key>"
                "<arg_value>python -m pytest -q</arg_value></tool_call> "
                "Changed: Update score.py. Validated with pytest."
            ),
            context={"routing": {"scenario": "code_edit"}},
            skip_reflection=True,
        )

        assert result["status"] == "completed"
        assert "<tool_call>" not in result["resultSummary"]
        assert "arg_value" not in result["resultSummary"]
        assert result["resultSummary"].startswith("Changed: Update score.py.")

    def test_later_successful_equivalent_test_command_resolves_prior_failure(self, tmp_path: Any) -> None:
        rt = _make_runtime(tmp_path)
        store = rt.store
        workspace = store.upsert_workspace(str(tmp_path / "project"))
        session = store.create_session(workspace_id=workspace["id"], title="completion gate")
        task = store.create_task(
            session_id=session["id"],
            task_type="edit",
            goal="fix a Python bug",
            plan=[],
            routing={"scenario": "code_edit"},
        )
        task = store.update_task(
            task_id=task["id"],
            changed_files=[{"path": "cart.py", "action": "modified"}],
            commands=[
                {
                    "id": "cmd_bad_shell",
                    "command": 'cd "C:\\tmp\\worktree" && python -m pytest -q',
                    "status": "failed",
                    "exitCode": 1,
                    "summary": "PowerShell rejected &&",
                },
                {
                    "id": "cmd_pytest_passed",
                    "command": 'cd "C:\\tmp\\worktree"; python -m pytest -q',
                    "status": "completed",
                    "exitCode": 0,
                    "summary": "4 passed",
                },
            ],
        )

        result = rt.orchestrator._complete_task(
            session_id=session["id"],
            task=task,
            summary="Implementation finished and pytest passed after retry.",
            context={"routing": {"scenario": "code_edit"}},
            skip_reflection=True,
        )

        evidence = result["structuredResult"]["completionEvidence"]
        assert result["status"] == "completed"
        assert evidence["evidenceLevel"] == "verified"
        assert evidence["counts"]["failedVerification"] == 0
        assert evidence["counts"]["failedTestsRun"] == 0
        assert evidence["counts"]["resolvedFailedTestsRun"] == 1
        assert evidence["counts"]["passedTestsRun"] == 1

    def test_later_success_resolves_prior_failure_when_commands_are_latest_first(self, tmp_path: Any) -> None:
        rt = _make_runtime(tmp_path)
        store = rt.store
        workspace = store.upsert_workspace(str(tmp_path / "project"))
        session = store.create_session(workspace_id=workspace["id"], title="completion gate")
        task = store.create_task(
            session_id=session["id"],
            task_type="edit",
            goal="fix a Python bug",
            plan=[],
            routing={"scenario": "code_edit"},
        )
        task = store.update_task(
            task_id=task["id"],
            changed_files=[{"path": "score.py", "action": "modified"}],
            commands=[
                {
                    "id": "cmd_pytest_passed",
                    "command": r"C:\Python314\python.exe -m pytest -q",
                    "status": "completed",
                    "exitCode": 0,
                    "summary": "3 passed",
                    "startedAt": 300,
                },
                {
                    "id": "cmd_bad_shell",
                    "command": r'cd "D:\workspace"; python -m pytest -q',
                    "status": "failed",
                    "exitCode": 1,
                    "summary": "PowerShell command failed",
                    "startedAt": 200,
                },
            ],
        )

        result = rt.orchestrator._complete_task(
            session_id=session["id"],
            task=task,
            summary="Implementation finished and pytest passed after retry.",
            context={"routing": {"scenario": "code_edit"}},
            skip_reflection=True,
        )

        evidence = result["structuredResult"]["completionEvidence"]
        assert result["status"] == "completed"
        assert evidence["evidenceLevel"] == "verified"
        assert evidence["counts"]["failedVerification"] == 0
        assert evidence["counts"]["failedTestsRun"] == 0
        assert evidence["counts"]["resolvedFailedTestsRun"] == 1
        assert evidence["counts"]["passedTestsRun"] == 1

    def test_javascript_change_with_python_verification_waits_for_framework_review(self, tmp_path: Any) -> None:
        rt = _make_runtime(tmp_path)
        store = rt.store
        workspace = store.upsert_workspace(str(tmp_path / "project"))
        session = store.create_session(workspace_id=workspace["id"], title="completion gate")
        task = store.create_task(
            session_id=session["id"],
            task_type="edit",
            goal="modify the frontend",
            plan=[],
            routing={"scenario": "code_edit"},
        )
        task = store.update_task(
            task_id=task["id"],
            changed_files=[{"path": "app/src/App.tsx", "action": "modified"}],
            verification=[{"command": "pytest runtime/tests/test_feature.py", "status": "passed", "summary": "all passed"}],
        )

        result = rt.orchestrator._complete_task(
            session_id=session["id"],
            task=task,
            summary="Frontend implementation finished.",
            context={"routing": {"scenario": "code_edit"}},
            skip_reflection=True,
        )

        evidence = result["structuredResult"]["completionEvidence"]
        assert result["status"] == "waiting_approval"
        assert result["structuredResult"]["completionGate"]["status"] == "needs_verification"
        assert evidence["verificationRequirements"]["required"] == ["javascript"]
        assert evidence["verificationRequirements"]["missing"] == ["javascript"]

    def test_javascript_change_with_javascript_verification_completes(self, tmp_path: Any) -> None:
        rt = _make_runtime(tmp_path)
        store = rt.store
        workspace = store.upsert_workspace(str(tmp_path / "project"))
        session = store.create_session(workspace_id=workspace["id"], title="completion gate")
        task = store.create_task(
            session_id=session["id"],
            task_type="edit",
            goal="modify the frontend",
            plan=[],
            routing={"scenario": "code_edit"},
        )
        task = store.update_task(
            task_id=task["id"],
            changed_files=[{"path": "app/src/App.tsx", "action": "modified"}],
            verification=[{"command": "npm run test -- App.test.tsx", "status": "passed", "summary": "all passed"}],
        )

        result = rt.orchestrator._complete_task(
            session_id=session["id"],
            task=task,
            summary="Frontend implementation finished.",
            context={"routing": {"scenario": "code_edit"}},
            skip_reflection=True,
        )

        evidence = result["structuredResult"]["completionEvidence"]
        assert result["status"] == "completed"
        assert evidence["verificationRequirements"]["required"] == ["javascript"]
        assert evidence["verificationRequirements"]["missing"] == []
        assert evidence["verificationRequirements"]["status"] == "satisfied"

    def test_javascript_change_with_node_check_verification_completes(self, tmp_path: Any) -> None:
        rt = _make_runtime(tmp_path)
        store = rt.store
        workspace = store.upsert_workspace(str(tmp_path / "project"))
        session = store.create_session(workspace_id=workspace["id"], title="completion gate")
        task = store.create_task(
            session_id=session["id"],
            task_type="edit",
            goal="modify static frontend",
            plan=[],
            routing={"scenario": "code_edit"},
        )
        task = store.update_task(
            task_id=task["id"],
            changed_files=[{"path": "app.js", "action": "modified"}],
            verification=[{"command": "node --check app.js", "status": "passed", "summary": "syntax ok"}],
        )

        result = rt.orchestrator._complete_task(
            session_id=session["id"],
            task=task,
            summary="Static frontend implementation finished.",
            context={"routing": {"scenario": "code_edit"}},
            skip_reflection=True,
        )

        evidence = result["structuredResult"]["completionEvidence"]
        assert result["status"] == "completed"
        assert evidence["verificationRequirements"]["required"] == ["javascript"]
        assert evidence["verificationRequirements"]["missing"] == []
        assert evidence["verificationRequirements"]["status"] == "satisfied"

    def test_doc_change_with_structural_verification_completes(self, tmp_path: Any) -> None:
        rt = _make_runtime(tmp_path)
        store = rt.store
        workspace = store.upsert_workspace(str(tmp_path / "project"))
        session = store.create_session(workspace_id=workspace["id"], title="completion gate")
        task = store.create_task(
            session_id=session["id"],
            task_type="edit",
            goal="update documentation",
            plan=[],
            routing={"scenario": "doc_write"},
        )
        task = store.update_task(
            task_id=task["id"],
            changed_files=[{"path": "docs/usage.md", "action": "modified"}],
            verification=[{"name": "git_diff", "status": "passed", "summary": "diff checked"}],
        )

        result = rt.orchestrator._complete_task(
            session_id=session["id"],
            task=task,
            summary="Documentation finished and diff checked.",
            context={"routing": {"scenario": "doc_write"}},
            skip_reflection=True,
        )

        assert result["status"] == "completed"
        assert result["structuredResult"]["completionEvidence"]["evidenceLevel"] == "verified"

    def test_completion_review_approval_allows_completion(self, tmp_path: Any) -> None:
        rt = _make_runtime(tmp_path)
        store = rt.store
        workspace = store.upsert_workspace(str(tmp_path / "project"))
        session = store.create_session(workspace_id=workspace["id"], title="completion gate")
        task = store.create_task(
            session_id=session["id"],
            task_type="edit",
            goal="modify the implementation",
            plan=[],
            routing={"scenario": "code_edit"},
        )
        waiting = rt.orchestrator._complete_task(
            session_id=session["id"],
            task=task,
            summary="I changed the implementation.",
            context={"routing": {"scenario": "code_edit"}},
            skip_reflection=True,
        )
        approval_id = waiting["structuredResult"]["completionGate"]["approvalId"]

        result = rt.orchestrator.submit_approval({"approvalId": approval_id, "decision": "approved"})

        assert result["task"]["status"] == "completed"
        completed = store.get_task({"taskId": task["id"]})["task"]
        assert completed["status"] == "completed"
        assert completed["structuredResult"]["completionEvidence"]["evidenceLevel"] == "summary_only"
        review = completed["structuredResult"]["completionReview"]
        assert review["approvalId"] == approval_id
        assert review["decision"] == "approved"
        assert completed["structuredResult"]["completionEvidence"]["reviewConclusion"]["decision"] == "approved"

    def test_swarm_task_summary_only_requires_completion_review(self, tmp_path: Any) -> None:
        rt = _make_runtime(tmp_path)
        store = rt.store
        workspace = store.upsert_workspace(str(tmp_path / "project"))
        session = store.create_session(workspace_id=workspace["id"], title="completion gate")
        task = store.create_task(
            session_id=session["id"],
            task_type="agent",
            goal="Use multiple agents to implement a full-stack feature",
            plan=[],
            routing={"scenario": "swarm_task", "strategy": "plan_swarm"},
        )

        result = rt.orchestrator._complete_task(
            session_id=session["id"],
            task=task,
            summary="Plan execution completed with no files changed.",
            context={"routing": {"scenario": "swarm_task", "strategy": "plan_swarm"}},
            skip_reflection=True,
        )

        assert result["status"] == "waiting_approval"
        gate = result["structuredResult"]["completionGate"]
        assert gate["status"] == "needs_user_review"
        assert "summary" in gate["reason"].lower()

    def test_explicit_artifact_requirements_block_incomplete_completion(self, tmp_path: Any) -> None:
        rt = _make_runtime(tmp_path)
        store = rt.store
        project = tmp_path / "project"
        project.mkdir()
        (project / "feedback_models.py").write_text("VALUE = 1\n", encoding="utf-8")
        (project / "tests").mkdir()
        (project / "tests" / "test_feedback_core.py").write_text("def test_ok(): assert True\n", encoding="utf-8")
        workspace = store.upsert_workspace(str(project))
        session = store.create_session(workspace_id=workspace["id"], title="completion gate")
        task = store.create_task(
            session_id=session["id"],
            task_type="agent",
            goal=(
                "Create feedback_models.py, feedback_storage.py, index.html, app.js, styles.css, "
                "and at least 2 pytest files."
            ),
            plan=[],
            routing={"scenario": "swarm_task", "strategy": "plan_swarm"},
        )

        result = rt.orchestrator._complete_task(
            session_id=session["id"],
            task=task,
            summary="Implemented a partial slice.",
            context={"workspace_root": str(project), "routing": {"scenario": "swarm_task", "strategy": "plan_swarm"}},
            tool_results=[{"name": "write_file", "result": {"status": "completed", "path": "feedback_models.py"}}],
            skip_reflection=True,
        )

        assert result["status"] == "waiting_approval"
        evidence = result["structuredResult"]["completionEvidence"]
        failed = [item["criterion"] for item in evidence["acceptance"] if item["status"] == "failed"]
        assert "Expected artifact exists: feedback_storage.py" in failed
        assert "Expected artifact exists: app.js" in failed
        assert "Expected pytest file count >= 2" in failed

    def test_generated_artifact_mojibake_waits_for_review(self, tmp_path: Any) -> None:
        rt = _make_runtime(tmp_path)
        store = rt.store
        project = tmp_path / "project"
        project.mkdir()
        (project / "index.html").write_text("<main>浣犲ソ 鈥? dashboard</main>\n", encoding="utf-8")
        workspace = store.upsert_workspace(str(project))
        session = store.create_session(workspace_id=workspace["id"], title="product gate")
        task = store.create_task(
            session_id=session["id"],
            task_type="edit",
            goal="Create a readable static frontend in index.html",
            plan=[],
            routing={"scenario": "code_edit"},
        )
        task = store.update_task(
            task_id=task["id"],
            changed_files=[{"path": "index.html", "summary": "generated static frontend"}],
        )

        result = rt.orchestrator._complete_task(
            session_id=session["id"],
            task=task,
            summary="Generated index.html.",
            context={"workspace_root": str(project), "routing": {"scenario": "code_edit"}},
            skip_reflection=True,
        )

        assert result["status"] == "waiting_approval"
        evidence = result["structuredResult"]["completionEvidence"]
        readability = [
            item for item in evidence["acceptance"]
            if item["criterion"] == "Readable artifact copy: index.html"
        ][0]
        assert readability["status"] == "failed"
        assert readability["source"] == "product_readability_check"
        assert result["structuredResult"]["completionGate"]["status"] == "needs_acceptance_review"

    def test_static_frontend_missing_asset_waits_for_review(self, tmp_path: Any) -> None:
        rt = _make_runtime(tmp_path)
        store = rt.store
        project = tmp_path / "project"
        project.mkdir()
        (project / "index.html").write_text(
            '<!doctype html><link rel="stylesheet" href="styles.css"><main>Ready</main>\n',
            encoding="utf-8",
        )
        workspace = store.upsert_workspace(str(project))
        session = store.create_session(workspace_id=workspace["id"], title="product gate")
        task = store.create_task(
            session_id=session["id"],
            task_type="edit",
            goal="Create a static frontend in index.html with styles.css",
            plan=[],
            routing={"scenario": "code_edit"},
        )
        task = store.update_task(
            task_id=task["id"],
            changed_files=[{"path": "index.html", "summary": "generated static frontend"}],
            commands=[
                {
                    "id": "cmd_exists",
                    "command": "dir index.html",
                    "status": "completed",
                    "exitCode": 0,
                    "summary": "index.html exists",
                }
            ],
        )

        result = rt.orchestrator._complete_task(
            session_id=session["id"],
            task=task,
            summary="Generated index.html.",
            context={"workspace_root": str(project), "routing": {"scenario": "code_edit"}},
            skip_reflection=True,
        )

        assert result["status"] == "waiting_approval"
        evidence = result["structuredResult"]["completionEvidence"]
        asset = [
            item for item in evidence["acceptance"]
            if item["criterion"] == "Static frontend asset reachable: index.html -> styles.css"
        ][0]
        assert asset["status"] == "failed"
        assert asset["source"] == "static_asset_reachability"
        assert result["structuredResult"]["completionGate"]["status"] == "needs_acceptance_review"

    def test_static_frontend_assets_complete_when_reachable(self, tmp_path: Any) -> None:
        rt = _make_runtime(tmp_path)
        store = rt.store
        project = tmp_path / "project"
        project.mkdir()
        (project / "index.html").write_text(
            '<!doctype html><link rel="stylesheet" href="styles.css"><main>Ready</main>\n',
            encoding="utf-8",
        )
        (project / "styles.css").write_text("main { color: #123456; }\n", encoding="utf-8")
        workspace = store.upsert_workspace(str(project))
        session = store.create_session(workspace_id=workspace["id"], title="product gate")
        task = store.create_task(
            session_id=session["id"],
            task_type="edit",
            goal="Create a static frontend in index.html with styles.css",
            plan=[],
            routing={"scenario": "code_edit"},
        )
        task = store.update_task(
            task_id=task["id"],
            changed_files=[
                {"path": "index.html", "summary": "generated static frontend"},
                {"path": "styles.css", "summary": "generated styles"},
            ],
            commands=[
                {
                    "id": "cmd_exists",
                    "command": "dir index.html styles.css",
                    "status": "completed",
                    "exitCode": 0,
                    "summary": "static assets exist",
                }
            ],
        )

        result = rt.orchestrator._complete_task(
            session_id=session["id"],
            task=task,
            summary="Generated static frontend files.",
            context={"workspace_root": str(project), "routing": {"scenario": "code_edit"}},
            skip_reflection=True,
        )

        assert result["status"] == "completed"
        evidence = result["structuredResult"]["completionEvidence"]
        asset = [
            item for item in evidence["acceptance"]
            if item["criterion"] == "Static frontend asset reachable: index.html -> styles.css"
        ][0]
        assert asset["status"] == "supported"
        assert asset["source"] == "static_asset_reachability"

    def test_static_frontend_blank_route_waits_for_review(self, tmp_path: Any) -> None:
        rt = _make_runtime(tmp_path)
        store = rt.store
        project = tmp_path / "project"
        project.mkdir()
        (project / "index.html").write_text(
            '<!doctype html><script src="app.js"></script>\n',
            encoding="utf-8",
        )
        (project / "app.js").write_text("console.log('ready');\n", encoding="utf-8")
        workspace = store.upsert_workspace(str(project))
        session = store.create_session(workspace_id=workspace["id"], title="product gate")
        task = store.create_task(
            session_id=session["id"],
            task_type="edit",
            goal="Create a static frontend in index.html",
            plan=[],
            routing={"scenario": "code_edit"},
        )
        task = store.update_task(
            task_id=task["id"],
            changed_files=[
                {"path": "index.html", "summary": "generated static frontend"},
                {"path": "app.js", "summary": "generated script"},
            ],
            verification=[{"command": "node --check app.js", "status": "passed", "summary": "syntax ok"}],
        )

        result = rt.orchestrator._complete_task(
            session_id=session["id"],
            task=task,
            summary="Generated static frontend files.",
            context={"workspace_root": str(project), "routing": {"scenario": "code_edit"}},
            skip_reflection=True,
        )

        assert result["status"] == "waiting_approval"
        evidence = result["structuredResult"]["completionEvidence"]
        route = [
            item for item in evidence["acceptance"]
            if item["criterion"] == "Static frontend route opens: index.html"
        ][0]
        assert route["status"] == "failed"
        assert route["source"] == "static_frontend_route_health"
        assert "no visible text found" in route["issues"]
        assert result["structuredResult"]["completionGate"]["status"] == "needs_acceptance_review"

    def test_static_frontend_missing_route_link_waits_for_review(self, tmp_path: Any) -> None:
        rt = _make_runtime(tmp_path)
        store = rt.store
        project = tmp_path / "project"
        project.mkdir()
        (project / "index.html").write_text(
            '<!doctype html><main><a href="about.html">About</a></main>\n',
            encoding="utf-8",
        )
        workspace = store.upsert_workspace(str(project))
        session = store.create_session(workspace_id=workspace["id"], title="product gate")
        task = store.create_task(
            session_id=session["id"],
            task_type="edit",
            goal="Create a static frontend in index.html with about route",
            plan=[],
            routing={"scenario": "code_edit"},
        )
        task = store.update_task(
            task_id=task["id"],
            changed_files=[{"path": "index.html", "summary": "generated static frontend"}],
            commands=[
                {
                    "id": "cmd_exists",
                    "command": "dir index.html",
                    "status": "completed",
                    "exitCode": 0,
                    "summary": "index.html exists",
                }
            ],
        )

        result = rt.orchestrator._complete_task(
            session_id=session["id"],
            task=task,
            summary="Generated index.html.",
            context={"workspace_root": str(project), "routing": {"scenario": "code_edit"}},
            skip_reflection=True,
        )

        assert result["status"] == "waiting_approval"
        evidence = result["structuredResult"]["completionEvidence"]
        route = [
            item for item in evidence["acceptance"]
            if item["criterion"] == "Static frontend asset reachable: index.html -> about.html"
        ][0]
        assert route["status"] == "failed"
        assert route["assetType"] == "route"
        assert result["structuredResult"]["completionGate"]["status"] == "needs_acceptance_review"

    def test_frontend_api_reference_without_backend_route_waits_for_review(self, tmp_path: Any) -> None:
        rt = _make_runtime(tmp_path)
        store = rt.store
        project = tmp_path / "project"
        project.mkdir()
        (project / "index.html").write_text(
            '<!doctype html><script src="app.js"></script><main>Feedback</main>\n',
            encoding="utf-8",
        )
        (project / "app.js").write_text(
            "fetch('/api/feedback', { method: 'POST', body: JSON.stringify({ note: 'ok' }) });\n",
            encoding="utf-8",
        )
        workspace = store.upsert_workspace(str(project))
        session = store.create_session(workspace_id=workspace["id"], title="product gate")
        task = store.create_task(
            session_id=session["id"],
            task_type="edit",
            goal="Create a feedback frontend that submits to /api/feedback",
            plan=[],
            routing={"scenario": "code_edit"},
        )
        task = store.update_task(
            task_id=task["id"],
            changed_files=[
                {"path": "index.html", "summary": "generated frontend"},
                {"path": "app.js", "summary": "generated API call"},
            ],
            verification=[{"command": "node --check app.js", "status": "passed", "summary": "syntax ok"}],
        )

        result = rt.orchestrator._complete_task(
            session_id=session["id"],
            task=task,
            summary="Generated feedback frontend files.",
            context={"workspace_root": str(project), "routing": {"scenario": "code_edit"}},
            skip_reflection=True,
        )

        assert result["status"] == "waiting_approval"
        evidence = result["structuredResult"]["completionEvidence"]
        api = [
            item for item in evidence["acceptance"]
            if item["criterion"] == "Frontend API route reachable: app.js -> POST /api/feedback"
        ][0]
        assert api["status"] == "failed"
        assert api["source"] == "api_contract_reachability"
        assert "no matching backend route found" in api["issues"]
        assert result["structuredResult"]["completionGate"]["status"] == "needs_acceptance_review"

    def test_frontend_api_reference_with_matching_backend_route_completes(self, tmp_path: Any) -> None:
        rt = _make_runtime(tmp_path)
        store = rt.store
        project = tmp_path / "project"
        project.mkdir()
        (project / "index.html").write_text(
            '<!doctype html><script src="app.js"></script><main>Feedback</main>\n',
            encoding="utf-8",
        )
        (project / "app.js").write_text(
            "fetch('/api/feedback', { method: 'POST', body: JSON.stringify({ note: 'ok' }) });\n",
            encoding="utf-8",
        )
        (project / "server.py").write_text(
            "from fastapi import FastAPI\n"
            "app = FastAPI()\n\n"
            "@app.post('/api/feedback')\n"
            "def create_feedback():\n"
            "    return {'ok': True}\n",
            encoding="utf-8",
        )
        workspace = store.upsert_workspace(str(project))
        session = store.create_session(workspace_id=workspace["id"], title="product gate")
        task = store.create_task(
            session_id=session["id"],
            task_type="edit",
            goal="Create a feedback frontend and backend API",
            plan=[],
            routing={"scenario": "code_edit"},
        )
        task = store.update_task(
            task_id=task["id"],
            changed_files=[
                {"path": "index.html", "summary": "generated frontend"},
                {"path": "app.js", "summary": "generated API call"},
                {"path": "server.py", "summary": "generated API route"},
            ],
            verification=[
                {"command": "node --check app.js", "status": "passed", "summary": "syntax ok"},
                {"command": "pytest -q", "status": "passed", "summary": "1 passed"},
            ],
        )

        result = rt.orchestrator._complete_task(
            session_id=session["id"],
            task=task,
            summary="Generated feedback frontend and backend API.",
            context={"workspace_root": str(project), "routing": {"scenario": "code_edit"}},
            skip_reflection=True,
        )

        assert result["status"] == "completed"
        evidence = result["structuredResult"]["completionEvidence"]
        api = [
            item for item in evidence["acceptance"]
            if item["criterion"] == "Frontend API route reachable: app.js -> POST /api/feedback"
        ][0]
        assert api["status"] == "supported"
        assert api["source"] == "api_contract_reachability"
        assert api["backendRoute"]["sourcePath"] == "server.py"

    def test_static_frontend_script_syntax_failure_waits_for_review(self, tmp_path: Any) -> None:
        rt = _make_runtime(tmp_path)
        store = rt.store
        project = tmp_path / "project"
        project.mkdir()
        (project / "index.html").write_text(
            '<!doctype html><script src="app.js"></script><main>Ready</main>\n',
            encoding="utf-8",
        )
        (project / "app.js").write_text("function broken( {\n", encoding="utf-8")
        workspace = store.upsert_workspace(str(project))
        session = store.create_session(workspace_id=workspace["id"], title="product gate")
        task = store.create_task(
            session_id=session["id"],
            task_type="edit",
            goal="Create a static frontend in index.html with app.js",
            plan=[],
            routing={"scenario": "code_edit"},
        )
        task = store.update_task(
            task_id=task["id"],
            changed_files=[
                {"path": "index.html", "summary": "generated static frontend"},
                {"path": "app.js", "summary": "generated script"},
            ],
            verification=[{"command": "node --check app.js", "status": "passed", "summary": "claimed ok"}],
        )

        result = rt.orchestrator._complete_task(
            session_id=session["id"],
            task=task,
            summary="Generated static frontend files.",
            context={"workspace_root": str(project), "routing": {"scenario": "code_edit"}},
            skip_reflection=True,
        )

        assert result["status"] == "waiting_approval"
        evidence = result["structuredResult"]["completionEvidence"]
        syntax = [
            item for item in evidence["acceptance"]
            if item["criterion"] == "Static frontend script syntax: app.js"
        ][0]
        assert syntax["status"] == "failed"
        assert syntax["source"] == "static_frontend_node_check"
        assert result["structuredResult"]["completionGate"]["status"] == "needs_acceptance_review"

    def test_static_frontend_script_syntax_success_is_recorded(self, tmp_path: Any) -> None:
        rt = _make_runtime(tmp_path)
        store = rt.store
        project = tmp_path / "project"
        project.mkdir()
        (project / "index.html").write_text(
            '<!doctype html><script src="app.js"></script><main>Ready</main>\n',
            encoding="utf-8",
        )
        (project / "app.js").write_text("console.log('ready');\n", encoding="utf-8")
        workspace = store.upsert_workspace(str(project))
        session = store.create_session(workspace_id=workspace["id"], title="product gate")
        task = store.create_task(
            session_id=session["id"],
            task_type="edit",
            goal="Create a static frontend in index.html with app.js",
            plan=[],
            routing={"scenario": "code_edit"},
        )
        task = store.update_task(
            task_id=task["id"],
            changed_files=[
                {"path": "index.html", "summary": "generated static frontend"},
                {"path": "app.js", "summary": "generated script"},
            ],
            verification=[{"command": "node --check app.js", "status": "passed", "summary": "syntax ok"}],
        )

        result = rt.orchestrator._complete_task(
            session_id=session["id"],
            task=task,
            summary="Generated static frontend files.",
            context={"workspace_root": str(project), "routing": {"scenario": "code_edit"}},
            skip_reflection=True,
        )

        assert result["status"] == "completed"
        evidence = result["structuredResult"]["completionEvidence"]
        syntax = [
            item for item in evidence["acceptance"]
            if item["criterion"] == "Static frontend script syntax: app.js"
        ][0]
        assert syntax["status"] == "supported"
        assert syntax["source"] == "static_frontend_node_check"

    def test_completion_review_rejection_fails_task(self, tmp_path: Any) -> None:
        rt = _make_runtime(tmp_path)
        store = rt.store
        workspace = store.upsert_workspace(str(tmp_path / "project"))
        session = store.create_session(workspace_id=workspace["id"], title="completion gate")
        task = store.create_task(
            session_id=session["id"],
            task_type="edit",
            goal="modify the implementation",
            plan=[],
            routing={"scenario": "code_edit"},
        )
        waiting = rt.orchestrator._complete_task(
            session_id=session["id"],
            task=task,
            summary="I changed the implementation.",
            context={"routing": {"scenario": "code_edit"}},
            skip_reflection=True,
        )
        approval_id = waiting["structuredResult"]["completionGate"]["approvalId"]

        result = rt.orchestrator.submit_approval({"approvalId": approval_id, "decision": "rejected"})

        assert result["task"]["status"] == "failed"
        assert result["task"]["errorCode"] == "COMPLETION_REVIEW_REJECTED"
        failed = store.get_task({"taskId": task["id"]})["task"]
        assert failed["structuredResult"]["completionReview"]["decision"] == "rejected"
        assert failed["structuredResult"]["completionEvidence"]["reviewConclusion"]["decision"] == "rejected"
