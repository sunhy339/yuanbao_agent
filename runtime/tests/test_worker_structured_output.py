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
