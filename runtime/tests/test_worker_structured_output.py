"""Tests for P6.4: worker structured result output.

Verifies that task completion builds and persists a structured result dict
containing summary, status, changedFiles, testsRun, risks, keyFindings.
"""

from __future__ import annotations

import os
import subprocess
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from local_agent_runtime.event_bus import EventBus
from local_agent_runtime.orchestrator.service import Orchestrator
from local_agent_runtime.policy.guard import PolicyGuard
from local_agent_runtime.policy.permission_engine import PermissionEngine
from local_agent_runtime.services.hook_service import HookService
from local_agent_runtime.store.sqlite_store import SQLiteStore
from local_agent_runtime.tools.read_file import build_read_file_tool
from local_agent_runtime.tools.registry import ToolRegistry
from local_agent_runtime.tools.run_command import build_run_command_tool


def _make_runtime(
    tmp_path: Any,
    decision_advisor: Any | None = None,
    *,
    enable_hooks: bool = False,
    enable_run_command: bool = False,
) -> SimpleNamespace:
    event_bus = EventBus()
    store = SQLiteStore(str(tmp_path / "runtime.sqlite3"))
    tools = {}
    config = store.get_config({})["config"]
    tools["read_file"] = build_read_file_tool(
        PolicyGuard(approval_mode=config["policy"]["approvalMode"]),
        store,
    )["handler"]
    if enable_run_command:
        tools["run_command"] = build_run_command_tool(
            PolicyGuard(approval_mode=config["policy"]["approvalMode"]),
            store,
            permission_engine=PermissionEngine(config=config, store=store),
        )["handler"]
    tool_registry = ToolRegistry(tools=tools)

    class DummyProvider:
        def generate(self, prompt: str, context: dict[str, Any]) -> dict[str, Any]:
            return {"text": "ok", "status": "done"}

    orchestrator = Orchestrator(
        store=store, event_bus=event_bus,
        tool_registry=tool_registry, provider=DummyProvider(),
        decision_advisor=decision_advisor,
        hook_service=HookService(store, event_bus) if enable_hooks else None,
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

    def test_no_approval_mode_fails_summary_only_write_task(self, tmp_path: Any) -> None:
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

        assert result["status"] == "failed"
        assert result["errorCode"] == "COMPLETION_EVIDENCE_INSUFFICIENT"
        assert "summary" in result["resultSummary"].lower()
        assert "disabled" in result["resultSummary"].lower()
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

    def test_child_worker_completion_uses_profile_scope_instead_of_parent_prompt_artifacts(self, tmp_path: Any) -> None:
        rt = _make_runtime(tmp_path)
        store = rt.store
        project = tmp_path / "project"
        project.mkdir()
        (project / "blog_models.py").write_text("from dataclasses import dataclass\n", encoding="utf-8")
        (project / "blog_service.py").write_text("def create_post():\n    return True\n", encoding="utf-8")
        workspace = store.upsert_workspace(str(project))
        session = store.create_session(workspace_id=workspace["id"], title="completion gate")
        task = store.create_task(
            session_id=session["id"],
            task_type="subagent",
            goal=(
                "Parent task asks for a full-stack blog system with index.html, app.js, styles.css, "
                "tests/test_blog_service.py, and tests/test_blog_api.py."
            ),
            plan=[],
            routing={
                "scenario": "code_edit",
                "profile": {
                    "ownedScope": ["blog_models.py", "blog_service.py"],
                    "expectedArtifacts": [],
                    "verificationRequirements": [],
                },
            },
            role="worker",
        )
        task = store.update_task(
            task_id=task["id"],
            changed_files=[
                {"path": "blog_models.py", "action": "modified"},
                {"path": "blog_service.py", "action": "modified"},
            ],
        )

        result = rt.orchestrator._complete_task(
            session_id=session["id"],
            task=task,
            summary="Backend child task completed its owned files.",
            context={
                "routing": {"scenario": "code_edit"},
                "workspace_root": str(project),
                "_child_profile": {
                    "ownedScope": ["blog_models.py", "blog_service.py"],
                    "expectedArtifacts": [],
                    "verificationRequirements": [],
                },
                "config": {"policy": {"approvalMode": "none"}},
            },
            skip_reflection=True,
        )

        evidence = result["structuredResult"]["completionEvidence"]
        structural = [
            item for item in evidence["acceptance"]
            if item.get("source") == "structural_file_check"
        ]
        assert result["status"] == "completed"
        assert evidence["verificationRequirements"]["status"] == "not_required"
        assert {item["criterion"] for item in structural} == {
            "Expected artifact exists: blog_models.py",
            "Expected artifact exists: blog_service.py",
        }

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

    def test_failed_apply_patch_resolved_by_later_write_and_verification(self, tmp_path: Any) -> None:
        rt = _make_runtime(tmp_path)
        store = rt.store
        workspace = store.upsert_workspace(str(tmp_path / "project"))
        session = store.create_session(workspace_id=workspace["id"], title="completion gate")
        task = store.create_task(
            session_id=session["id"],
            task_type="edit",
            goal="repair a generated API bug",
            plan=[],
            routing={"scenario": "code_edit"},
        )
        task = store.update_task(
            task_id=task["id"],
            changed_files=[{"path": "blog_api.py", "action": "modified"}],
        )

        result = rt.orchestrator._complete_task(
            session_id=session["id"],
            task=task,
            summary="Repaired the API bug and reran verification.",
            context={"routing": {"scenario": "code_edit"}},
            tool_results=[
                {
                    "name": "apply_patch",
                    "result": {
                        "status": "validation_failed",
                        "ok": False,
                        "summary": "Patch validation failed.",
                    },
                },
                {
                    "name": "write_file",
                    "result": {
                        "status": "written",
                        "path": "blog_api.py",
                        "summary": "Wrote the repaired handler directly.",
                    },
                },
                {
                    "name": "run_command",
                    "result": {
                        "status": "completed",
                        "command": "python -m pytest -q",
                        "exitCode": 0,
                        "summary": "tests passed",
                    },
                },
                {
                    "name": "run_command",
                    "result": {
                        "status": "completed",
                        "command": "python -m py_compile blog_api.py",
                        "exitCode": 0,
                        "summary": "py_compile passed",
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

    def test_later_same_language_verification_resolves_prior_subfamily_failure(self, tmp_path: Any) -> None:
        rt = _make_runtime(tmp_path)
        store = rt.store
        workspace = store.upsert_workspace(str(tmp_path / "project"))
        session = store.create_session(workspace_id=workspace["id"], title="completion gate")
        task = store.create_task(
            session_id=session["id"],
            task_type="edit",
            goal="Implement TodoStore and CLI command behavior",
            plan=[],
            role="worker",
            routing={
                "runtimeRole": "worker",
                "profile": {
                    "ownedScope": ["todo.py"],
                    "expectedArtifacts": [{"kind": "file", "path": "todo.py"}],
                    "verificationRequirements": [],
                },
            },
        )
        task = store.update_task(
            task_id=task["id"],
            changed_files=[{"path": "todo.py", "action": "added"}],
            commands=[
                {
                    "id": "cmd_pytest",
                    "command": "python -m pytest -q",
                    "status": "failed",
                    "exitCode": 5,
                    "summary": "Command failed",
                },
                {
                    "id": "cmd_compile",
                    "command": "python -m py_compile todo.py",
                    "status": "completed",
                    "exitCode": 0,
                    "summary": "Command completed with exit 0",
                },
            ],
        )

        result = rt.orchestrator._complete_task(
            session_id=session["id"],
            task=task,
            summary="Implemented TodoStore and CLI behavior. py_compile passed after an earlier Python verification failure.",
            context={
                "routing": {
                    "runtimeRole": "worker",
                    "profile": {
                        "ownedScope": ["todo.py"],
                        "expectedArtifacts": [{"kind": "file", "path": "todo.py"}],
                        "verificationRequirements": [],
                    },
                }
            },
            skip_reflection=True,
        )

        evidence = result["structuredResult"]["completionEvidence"]
        assert result["status"] == "completed"
        assert evidence["counts"]["failedVerification"] == 0
        assert evidence["counts"]["resolvedFailedTestsRun"] == 1

    def test_python_test_file_change_requires_test_verification_not_lint_only(self, tmp_path: Any) -> None:
        rt = _make_runtime(tmp_path)
        store = rt.store
        workspace = store.upsert_workspace(str(tmp_path / "project"))
        session = store.create_session(workspace_id=workspace["id"], title="completion gate")
        task = store.create_task(
            session_id=session["id"],
            task_type="edit",
            goal="update Python tests",
            plan=[],
            routing={"scenario": "code_edit"},
        )
        task = store.update_task(
            task_id=task["id"],
            changed_files=[{"path": "tests/test_cart.py", "action": "modified"}],
            verification=[{"command": "ruff check tests/test_cart.py", "status": "passed", "summary": "lint passed"}],
        )

        result = rt.orchestrator._complete_task(
            session_id=session["id"],
            task=task,
            summary="Updated tests and lint passed.",
            context={"routing": {"scenario": "code_edit"}},
            skip_reflection=True,
        )

        evidence = result["structuredResult"]["completionEvidence"]
        assert result["status"] == "waiting_approval"
        assert result["structuredResult"]["completionGate"]["status"] == "needs_verification"
        assert evidence["verificationRequirements"]["required"] == ["python:test"]
        assert evidence["verificationRequirements"]["missing"] == ["python:test"]
        assert "python:lint" in evidence["verificationRequirements"]["matched"]

    def test_python_test_file_change_with_pytest_completes(self, tmp_path: Any) -> None:
        rt = _make_runtime(tmp_path)
        store = rt.store
        workspace = store.upsert_workspace(str(tmp_path / "project"))
        session = store.create_session(workspace_id=workspace["id"], title="completion gate")
        task = store.create_task(
            session_id=session["id"],
            task_type="edit",
            goal="update Python tests",
            plan=[],
            routing={"scenario": "code_edit"},
        )
        task = store.update_task(
            task_id=task["id"],
            changed_files=[{"path": "tests/test_cart.py", "action": "modified"}],
            commands=[{
                "id": "cmd_pytest",
                "command": "python -m pytest tests/test_cart.py -q",
                "status": "completed",
                "exitCode": 0,
                "summary": "2 passed",
            }],
        )

        result = rt.orchestrator._complete_task(
            session_id=session["id"],
            task=task,
            summary="Updated tests and pytest passed.",
            context={"routing": {"scenario": "code_edit"}},
            skip_reflection=True,
        )

        evidence = result["structuredResult"]["completionEvidence"]
        assert result["status"] == "completed"
        assert evidence["verificationRequirements"]["required"] == ["python:test"]
        assert evidence["verificationRequirements"]["missing"] == []
        assert "python:test" in evidence["verificationRequirements"]["matched"]

    def test_completion_advisor_can_accept_test_file_verification_gap(self, tmp_path: Any) -> None:
        class TestGapAdvisor:
            def advise(self, kind: str, input_context: dict[str, Any]) -> Any:
                if kind == "product_surface_decision":
                    return SimpleNamespace(
                        accepted=True,
                        source="llm",
                        rationale="A focused external integration smoke covers this changed test harness.",
                        fallback_reason=None,
                        proposal_id="surface_test_gap",
                        confidence=0.84,
                        payload={
                            "surface_type": "test_harness",
                            "recommended_verification": [],
                            "evidence_requests": [],
                        },
                    )
                return SimpleNamespace(
                    accepted=True,
                    source="llm",
                    rationale="The typecheck plus external harness run is sufficient for this test-only harness update.",
                    fallback_reason=None,
                    proposal_id="completion_test_gap",
                    confidence=0.87,
                    payload={
                        "is_complete": True,
                        "verification_sufficient": True,
                        "verification_assessment": {
                            "status": "domain_sufficient",
                            "reason": "The changed test harness is validated by the external integration smoke referenced in the task.",
                        },
                    },
                )

        rt = _make_runtime(tmp_path, decision_advisor=TestGapAdvisor())
        store = rt.store
        workspace = store.upsert_workspace(str(tmp_path / "project"))
        session = store.create_session(workspace_id=workspace["id"], title="completion gate")
        task = store.create_task(
            session_id=session["id"],
            task_type="edit",
            goal="update generated test harness metadata",
            plan=[],
            routing={"scenario": "code_edit"},
        )
        task = store.update_task(
            task_id=task["id"],
            changed_files=[{"path": "tests/test_harness.py", "action": "modified"}],
            verification=[{"command": "mypy tests/test_harness.py", "status": "passed", "summary": "typecheck passed"}],
        )

        result = rt.orchestrator._complete_task(
            session_id=session["id"],
            task=task,
            summary="Updated the generated test harness metadata and validated the harness externally.",
            context={"routing": {"scenario": "code_edit"}},
            skip_reflection=True,
        )

        evidence = result["structuredResult"]["completionEvidence"]
        assert result["status"] == "completed"
        assert evidence["verificationRequirements"]["required"] == ["python:test"]
        assert evidence["verificationRequirements"]["missing"] == ["python:test"]
        assert evidence["verificationRequirements"]["status"] == "advisor_accepted"
        assert evidence["verificationRequirements"]["advisorResolution"]["gapKind"] == "framework_mismatch"

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

    def test_completion_advisor_can_accept_domain_specific_verification_gap(self, tmp_path: Any) -> None:
        class VerificationAdvisor:
            def __init__(self) -> None:
                self.calls: list[tuple[str, dict[str, Any]]] = []

            def advise(self, kind: str, input_context: dict[str, Any]) -> Any:
                self.calls.append((kind, input_context))
                if kind == "product_surface_decision":
                    return SimpleNamespace(
                        accepted=True,
                        source="llm",
                        rationale="The TSX change is exercised through the Python integration smoke.",
                        fallback_reason=None,
                        proposal_id="surface_verification_gap",
                        confidence=0.82,
                        payload={
                            "surface_type": "integration_backed_ui",
                            "recommended_verification": [],
                            "evidence_requests": [],
                        },
                    )
                return SimpleNamespace(
                    accepted=True,
                    source="llm",
                    rationale="The integration smoke is the requested product proof even though it is not a JavaScript test command.",
                    fallback_reason=None,
                    proposal_id="completion_verification_gap",
                    confidence=0.86,
                    payload={
                        "is_complete": True,
                        "surface_type": "integration_backed_ui",
                        "verification_sufficient": True,
                        "verification_assessment": {
                            "status": "domain_sufficient",
                            "reason": "The passed Python smoke drives the changed UI through the app integration path.",
                        },
                    },
                )

        advisor = VerificationAdvisor()
        rt = _make_runtime(tmp_path, decision_advisor=advisor)
        store = rt.store
        workspace = store.upsert_workspace(str(tmp_path / "project"))
        session = store.create_session(workspace_id=workspace["id"], title="completion advisor verification")
        task = store.create_task(
            session_id=session["id"],
            task_type="edit",
            goal="modify the frontend integration path",
            plan=[],
            routing={"scenario": "code_edit"},
        )
        task = store.update_task(
            task_id=task["id"],
            changed_files=[{"path": "app/src/App.tsx", "action": "modified"}],
            verification=[{
                "command": "pytest runtime/tests/test_frontend_integration.py",
                "status": "passed",
                "summary": "integration smoke exercised the UI path",
            }],
        )

        result = rt.orchestrator._complete_task(
            session_id=session["id"],
            task=task,
            summary="Frontend integration path updated and exercised by smoke.",
            context={"routing": {"scenario": "code_edit"}},
            skip_reflection=True,
        )

        evidence = result["structuredResult"]["completionEvidence"]
        assert result["status"] == "completed"
        assert [call[0] for call in advisor.calls] == [
            "product_surface_decision",
            "completion_decision",
        ]
        assert evidence["verificationRequirements"]["required"] == ["javascript"]
        assert evidence["verificationRequirements"]["missing"] == ["javascript"]
        assert evidence["verificationRequirements"]["status"] == "advisor_accepted"
        resolution = evidence["verificationRequirements"]["advisorResolution"]
        assert resolution["gapKind"] == "framework_mismatch"
        assert resolution["assessmentStatus"] == "domain_sufficient"
        assert resolution["proposalRecordId"]

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

    def test_javascript_test_file_change_requires_test_verification_not_typecheck_only(self, tmp_path: Any) -> None:
        rt = _make_runtime(tmp_path)
        store = rt.store
        workspace = store.upsert_workspace(str(tmp_path / "project"))
        session = store.create_session(workspace_id=workspace["id"], title="completion gate")
        task = store.create_task(
            session_id=session["id"],
            task_type="edit",
            goal="update frontend tests",
            plan=[],
            routing={"scenario": "code_edit"},
        )
        task = store.update_task(
            task_id=task["id"],
            changed_files=[{"path": "app/src/App.test.tsx", "action": "modified"}],
            verification=[{"command": "npm run typecheck", "status": "passed", "summary": "typecheck passed"}],
        )

        result = rt.orchestrator._complete_task(
            session_id=session["id"],
            task=task,
            summary="Frontend tests updated and typecheck passed.",
            context={"routing": {"scenario": "code_edit"}},
            skip_reflection=True,
        )

        evidence = result["structuredResult"]["completionEvidence"]
        assert result["status"] == "waiting_approval"
        assert result["structuredResult"]["completionGate"]["status"] == "needs_verification"
        assert evidence["verificationRequirements"]["required"] == ["javascript:test"]
        assert evidence["verificationRequirements"]["missing"] == ["javascript:test"]
        assert "javascript:typecheck" in evidence["verificationRequirements"]["matched"]

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

    def test_completion_audit_includes_approval_conclusions_for_advisor_and_trace(self, tmp_path: Any) -> None:
        class RecordingAdvisor:
            def __init__(self) -> None:
                self.calls: list[tuple[str, dict[str, Any]]] = []

            def advise(self, kind: str, input_context: dict[str, Any]) -> Any:
                self.calls.append((kind, input_context))
                return SimpleNamespace(
                    accepted=True,
                    source="llm",
                    rationale="Audit-aware completion decision.",
                    fallback_reason=None,
                    proposal_id=f"{kind}_{len(self.calls)}",
                    confidence=0.8,
                    payload={
                        "is_complete": True,
                        "why_complete": "Completion review and audit facts are available.",
                    },
                )

        advisor = RecordingAdvisor()
        rt = _make_runtime(tmp_path, decision_advisor=advisor)
        captured_events: list[Any] = []
        rt.event_bus.subscribe(captured_events.append)
        store = rt.store
        workspace = store.upsert_workspace(str(tmp_path / "project"))
        session = store.create_session(workspace_id=workspace["id"], title="completion audit")
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
        completion_calls = [context for kind, context in advisor.calls if kind == "completion_decision"]
        assert len(completion_calls) == 2
        final_audit = completion_calls[-1]["completion_audit"]
        assert final_audit["approvalCounts"] == {"total": 1, "approved": 1, "rejected": 0, "pending": 0}
        assert final_audit["reviewConclusion"]["approvalId"] == approval_id
        assert final_audit["reviewConclusion"]["decision"] == "approved"
        assert final_audit["approvals"][0]["kind"] == "completion_review"
        assert final_audit["approvals"][0]["decision"] == "approved"

        completed = store.get_task({"taskId": task["id"]})["task"]
        evidence_audit = completed["structuredResult"]["completionEvidence"]["audit"]
        assert evidence_audit["approvalCounts"]["approved"] == 1
        assert evidence_audit["completionAdvisor"]["proposalRecordId"]
        completion_event = [
            event for event in captured_events
            if event.type == "agent.decision.completion" and event.payload.get("decision") == "completed"
        ][-1]
        assert completion_event.payload["audit"]["approvalCounts"]["approved"] == 1
        assert completion_event.payload["audit"]["completionAdvisor"]["proposalRecordId"]
        proposal = store.list_proposals({
            "taskId": task["id"],
            "kind": "completion_decision",
        })["proposals"][0]
        assert proposal["source"]["completionAudit"]["approvalCounts"]["approved"] == 1

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

    def test_summarizer_child_summary_only_can_complete_without_write_or_verification_evidence(self, tmp_path: Any) -> None:
        rt = _make_runtime(tmp_path)
        store = rt.store
        workspace = store.upsert_workspace(str(tmp_path / "project"))
        session = store.create_session(workspace_id=workspace["id"], title="summarizer completion gate")
        task = store.create_task(
            session_id=session["id"],
            task_type="agent",
            goal="Summarize outcomes and evidence",
            plan=[],
            role="summarizer",
            routing={"scenario": "multi_step_task", "strategy": "plan_execute", "runtimeRole": "summarizer"},
        )

        result = rt.orchestrator._complete_task(
            session_id=session["id"],
            task=task,
            summary="Summary of completed work and verification evidence.",
            context={
                "routing": {"scenario": "multi_step_task", "strategy": "plan_execute", "runtimeRole": "summarizer"},
                "config": {"policy": {"approvalMode": "none"}},
                "_child_profile": {
                    "agentType": "summarizer",
                    "ownedScope": [],
                    "expectedArtifacts": [],
                    "verificationRequirements": [],
                },
            },
            skip_reflection=True,
        )

        assert result["status"] == "completed"
        assert result["structuredResult"]["completionEvidence"]["evidenceLevel"] == "summary_only"

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

    def test_readme_quality_observation_records_healthy_docs_without_blocking_completion(self, tmp_path: Any) -> None:
        rt = _make_runtime(tmp_path)
        store = rt.store
        project = tmp_path / "project"
        project.mkdir()
        (project / "README.md").write_text(
            "# Feedback Service\n\n"
            "## Overview\n\n"
            "The service stores feedback entries and exposes a review workflow.\n\n"
            "## Usage\n\n"
            "```bash\npytest -q\n```\n",
            encoding="utf-8",
        )
        workspace = store.upsert_workspace(str(project))
        session = store.create_session(workspace_id=workspace["id"], title="docs gate")
        task = store.create_task(
            session_id=session["id"],
            task_type="edit",
            goal="Update README.md with usage docs",
            plan=[],
            routing={"scenario": "code_edit"},
        )
        task = store.update_task(
            task_id=task["id"],
            changed_files=[{"path": "README.md", "summary": "updated docs"}],
            verification=[{"command": "git diff -- README.md", "status": "passed", "summary": "docs reviewed"}],
        )

        result = rt.orchestrator._complete_task(
            session_id=session["id"],
            task=task,
            summary="Updated README.md.",
            context={"workspace_root": str(project), "routing": {"scenario": "code_edit"}},
            skip_reflection=True,
        )

        assert result["status"] == "completed"
        evidence = result["structuredResult"]["completionEvidence"]
        advisory = [
            item for item in evidence["productAdvisories"]
            if item["kind"] == "docs_quality_observation"
        ][0]
        assert advisory["severity"] == "info"
        assert advisory["issueCount"] == 0
        assert advisory["documents"][0]["kind"] == "readme"
        assert advisory["documents"][0]["headingCount"] == 3

    def test_readme_quality_observation_surfaces_draft_signals_without_hard_gate(self, tmp_path: Any) -> None:
        rt = _make_runtime(tmp_path)
        store = rt.store
        project = tmp_path / "project"
        project.mkdir()
        (project / "README.md").write_text(
            "Feedback service TODO TBD placeholder copy.\n"
            "See [missing guide](docs/missing-guide.md).\n"
            "```python\nprint('unfinished')\n",
            encoding="utf-8",
        )
        workspace = store.upsert_workspace(str(project))
        session = store.create_session(workspace_id=workspace["id"], title="docs gate")
        task = store.create_task(
            session_id=session["id"],
            task_type="edit",
            goal="Draft README.md for the feedback service",
            plan=[],
            routing={"scenario": "code_edit"},
        )
        task = store.update_task(
            task_id=task["id"],
            changed_files=[{"path": "README.md", "summary": "drafted docs"}],
            verification=[{"command": "git diff -- README.md", "status": "passed", "summary": "docs reviewed"}],
        )

        result = rt.orchestrator._complete_task(
            session_id=session["id"],
            task=task,
            summary="Drafted README.md.",
            context={"workspace_root": str(project), "routing": {"scenario": "code_edit"}},
            skip_reflection=True,
        )

        assert result["status"] == "completed"
        evidence = result["structuredResult"]["completionEvidence"]
        advisory = [
            item for item in evidence["productAdvisories"]
            if item["kind"] == "docs_quality_observation"
        ][0]
        assert advisory["severity"] == "suggestion"
        assert advisory["issueCount"] >= 4
        issues = advisory["documents"][0]["issues"]
        assert "visible TODO placeholder" in issues
        assert "visible TBD placeholder" in issues
        assert "unbalanced fenced code blocks" in issues
        assert "missing relative link target: docs/missing-guide.md" in issues
        gate = result["structuredResult"].get("completionGate") or {}
        assert gate.get("status") != "needs_acceptance_review"

    def test_changed_surface_observation_feeds_product_advisor_without_hard_gate(self, tmp_path: Any) -> None:
        class RecordingAdvisor:
            def __init__(self) -> None:
                self.product_surface_inputs: list[dict[str, Any]] = []

            def advise(self, kind: str, input_context: dict[str, Any]) -> Any:
                if kind == "product_surface_decision":
                    self.product_surface_inputs.append(input_context)
                    return SimpleNamespace(
                        accepted=True,
                        source="llm",
                        rationale="The observed surfaces are enough for this task.",
                        fallback_reason=None,
                        proposal_id="surface_observation_1",
                        confidence=0.83,
                        payload={
                            "surface_type": "cli_with_persistence_migration",
                            "evidence_requests": [],
                        },
                    )
                return SimpleNamespace(
                    accepted=True,
                    source="llm",
                    rationale="Completion is acceptable with the current verification.",
                    fallback_reason=None,
                    proposal_id="completion_surface_observation_1",
                    confidence=0.82,
                    payload={"is_complete": True, "surface_type": "cli_with_persistence_migration"},
                )

        advisor = RecordingAdvisor()
        rt = _make_runtime(tmp_path, decision_advisor=advisor)
        store = rt.store
        project = tmp_path / "project"
        migrations = project / "migrations"
        migrations.mkdir(parents=True)
        (project / "cli.py").write_text(
            "import argparse\n\n"
            "def main():\n"
            "    parser = argparse.ArgumentParser()\n"
            "    parser.parse_args()\n\n"
            "if __name__ == \"__main__\":\n"
            "    main()\n",
            encoding="utf-8",
        )
        (migrations / "001_init.sql").write_text("CREATE TABLE feedback (id integer primary key);\n", encoding="utf-8")
        workspace = store.upsert_workspace(str(project))
        session = store.create_session(workspace_id=workspace["id"], title="surface observation")
        task = store.create_task(
            session_id=session["id"],
            task_type="edit",
            goal="Add a local CLI and database migration",
            plan=[],
            routing={"scenario": "code_edit"},
        )
        task = store.update_task(
            task_id=task["id"],
            changed_files=[
                {"path": "cli.py", "summary": "added CLI entrypoint"},
                {"path": "migrations/001_init.sql", "summary": "added schema migration"},
            ],
            verification=[{"command": "python -m pytest -q", "status": "passed", "summary": "tests passed"}],
        )

        result = rt.orchestrator._complete_task(
            session_id=session["id"],
            task=task,
            summary="Added CLI and migration.",
            context={"workspace_root": str(project), "routing": {"scenario": "code_edit"}},
            skip_reflection=True,
        )

        assert result["status"] == "completed"
        evidence = result["structuredResult"]["completionEvidence"]
        advisory = [
            item for item in evidence["productAdvisories"]
            if item["kind"] == "changed_surface_observation"
        ][0]
        assert advisory["severity"] == "info"
        assert advisory["roleCounts"]["cli_or_entrypoint"] == 1
        assert advisory["roleCounts"]["persistence_or_migration"] >= 1
        assert any(
            surface["path"] == "cli.py" and "cli_or_entrypoint" in surface["roles"]
            for surface in advisory["surfaces"]
        )
        assert any(
            surface["path"] == "migrations/001_init.sql" and "persistence_or_migration" in surface["roles"]
            for surface in advisory["surfaces"]
        )
        objective_advisories = advisor.product_surface_inputs[0]["objective_signals"]["objective_product_advisories"]
        assert any(item["kind"] == "changed_surface_observation" for item in objective_advisories)
        gate = result["structuredResult"].get("completionGate") or {}
        assert gate.get("status") != "needs_acceptance_review"

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

    def test_frontend_api_reference_without_backend_route_records_observation(self, tmp_path: Any) -> None:
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

        assert result["status"] == "completed"
        evidence = result["structuredResult"]["completionEvidence"]
        advisory = evidence["productAdvisories"][0]
        assert advisory["kind"] == "api_reference_observation"
        assert advisory["source"] == "objective_surface_scan"
        assert advisory["apiReferences"][0]["localRouteMatched"] is False

    def test_frontend_api_reference_with_matching_backend_route_adds_non_blocking_advisory(self, tmp_path: Any) -> None:
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
            item for item in evidence["productAdvisories"][0]["apiReferences"]
            if item["path"] == "/api/feedback"
        ][0]
        assert api["localRouteMatched"] is True
        assert api["backendRoute"]["sourcePath"] == "server.py"
        advisory = evidence["productAdvisories"][0]
        assert advisory["kind"] == "api_reference_observation"
        assert advisory["severity"] == "info"
        assert advisory["recommendedVerification"] == []
        assert not any(
            item.get("source") == "objective_surface_scan"
            for item in evidence["acceptance"]
        )

    def test_frontend_api_reference_with_runtime_signal_records_advisory_signal(self, tmp_path: Any) -> None:
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
                {
                    "command": "pytest -q tests/test_feedback_api.py",
                    "status": "passed",
                    "summary": "FastAPI TestClient POST /api/feedback round trip persisted feedback",
                },
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
        advisory = evidence["productAdvisories"][0]
        assert advisory["kind"] == "api_reference_observation"
        assert advisory["severity"] == "info"
        assert advisory["signals"][0]["source"] == "verification"

    def test_completion_advisor_gets_product_advisories_and_can_request_review(self, tmp_path: Any) -> None:
        class RecordingAdvisor:
            def __init__(self) -> None:
                self.calls: list[tuple[str, dict[str, Any]]] = []

            def advise(self, kind: str, input_context: dict[str, Any]) -> Any:
                self.calls.append((kind, input_context))
                if kind == "product_surface_decision":
                    return SimpleNamespace(
                        accepted=True,
                        source="llm",
                        rationale="The changed files form an interactive feedback flow.",
                        fallback_reason=None,
                        proposal_id="surface_review_1",
                        confidence=0.82,
                        payload={
                            "surface_type": "interactive_feedback_flow",
                            "recommended_verification": [
                                "Provide end-to-end evidence for the requested feedback flow.",
                            ],
                            "verification_intents": [
                                {"kind": "flow_evidence", "target": "feedback submission path"},
                            ],
                            "evidence_requests": [
                                {
                                    "kind": "flow_evidence",
                                    "summary": "Submit feedback and verify the resulting state or persistence.",
                                    "target": "feedback submission",
                                    "blocking": True,
                                },
                            ],
                        },
                    )
                return SimpleNamespace(
                    accepted=True,
                    source="llm",
                    rationale="The task asked for an end-to-end feedback flow but no runtime flow proof is present.",
                    fallback_reason=None,
                    proposal_id="advisor_review_1",
                    confidence=0.88,
                    payload={
                        "is_complete": False,
                        "surface_type": "interactive_feedback_flow",
                        "blocking_issues": ["Missing end-to-end evidence for the requested feedback flow."],
                        "recommended_verification": ["Provide flow evidence that submits feedback and verifies the outcome."],
                    },
        )

        advisor = RecordingAdvisor()
        rt = _make_runtime(tmp_path, decision_advisor=advisor, enable_hooks=True)
        captured_events: list[Any] = []
        rt.event_bus.subscribe(captured_events.append)
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
        session = store.create_session(workspace_id=workspace["id"], title="product advisor")
        task = store.create_task(
            session_id=session["id"],
            task_type="edit",
            goal="Create an end-to-end feedback frontend and backend API flow",
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

        assert result["status"] == "waiting_approval"
        assert [call[0] for call in advisor.calls] == [
            "product_surface_decision",
            "completion_decision",
        ]
        surface_context = advisor.calls[0][1]
        assert surface_context["objective_signals"]["objective_product_advisories"][0]["kind"] == "api_reference_observation"
        advisor_context = advisor.calls[1][1]
        assert any(
            item.get("source") == "llm_product_surface_advisor"
            for item in advisor_context["product_advisories"]
        )
        evidence = result["structuredResult"]["completionEvidence"]
        assert evidence["productSurfaceAdvisor"]["payload"]["surface_type"] == "interactive_feedback_flow"
        surface_proposals = store.list_proposals({
            "taskId": task["id"],
            "kind": "product_surface_decision",
        })["proposals"]
        assert len(surface_proposals) == 1
        assert evidence["productSurfaceAdvisor"]["proposalRecordId"] == surface_proposals[0]["id"]
        assert surface_proposals[0]["proposal"]["evidence_requests"][0]["kind"] == "flow_evidence"
        assert evidence["advisorRequestedEvidence"][0]["kind"] == "flow_evidence"
        assert evidence["advisorRequestedEvidence"][0]["status"] == "missing"
        evidence_events = [event for event in captured_events if event.type == "agent.evidence.requested"]
        assert len(evidence_events) == 1
        assert evidence_events[0].payload["surfaceType"] == "interactive_feedback_flow"
        assert evidence_events[0].payload["evidenceRequests"][0]["kind"] == "flow_evidence"
        assert evidence_events[0].payload["missingBlockingCount"] == 1
        llm_advisory = [
            item for item in evidence["productAdvisories"]
            if item.get("source") == "llm_product_surface_advisor"
        ][0]
        assert llm_advisory["surfaceType"] == "interactive_feedback_flow"
        assert llm_advisory["evidenceRequests"][0]["kind"] == "flow_evidence"
        assert llm_advisory["hasBlockingEvidenceRequest"] is True
        assert evidence["completionAdvisor"]["payload"]["is_complete"] is False
        proposal_records = store.list_proposals({
            "taskId": task["id"],
            "kind": "completion_decision",
        })["proposals"]
        assert len(proposal_records) == 1
        proposal = proposal_records[0]
        assert evidence["completionAdvisor"]["proposalRecordId"] == proposal["id"]
        assert proposal["status"] == "accepted"
        assert proposal["proposal"]["is_complete"] is False
        assert proposal["proposal"]["surface_type"] == "interactive_feedback_flow"
        assert proposal["source"]["type"] == "llm"
        assert proposal["source"]["advisorProposalId"] == "advisor_review_1"
        assert result["structuredResult"]["completionGate"]["status"] == "advisor_needs_review"

    def test_product_surface_advisor_can_request_design_evidence(self, tmp_path: Any) -> None:
        class RecordingAdvisor:
            def __init__(self) -> None:
                self.calls: list[tuple[str, dict[str, Any]]] = []

            def advise(self, kind: str, input_context: dict[str, Any]) -> Any:
                self.calls.append((kind, input_context))
                if kind == "product_surface_decision":
                    return SimpleNamespace(
                        accepted=True,
                        source="llm",
                        rationale="This is a design task, so the completion evidence should include reviewable trade-offs.",
                        fallback_reason=None,
                        proposal_id="surface_design_1",
                        confidence=0.86,
                        payload={
                            "surface_type": "architecture_design",
                            "recommended_verification": ["Review ADR alternatives, rollout, and rollback sections."],
                            "verification_intents": [
                                {"kind": "design_review", "target": "ADR trade-offs"},
                            ],
                            "evidence_requests": [
                                {
                                    "kind": "design_review",
                                    "summary": "ADR includes alternatives, decision rationale, rollout, rollback, and open risks.",
                                    "target": "docs/auth-storage-adr.md",
                                    "blocking": True,
                                },
                            ],
                        },
                    )
                return SimpleNamespace(
                    accepted=True,
                    source="llm",
                    rationale="Design evidence is needed before this can be considered reviewed.",
                    fallback_reason=None,
                    proposal_id="completion_design_1",
                    confidence=0.74,
                    payload={
                        "is_complete": True,
                        "surface_type": "architecture_design",
                        "remaining_risks": ["Design review evidence is still pending."],
                    },
                )

        advisor = RecordingAdvisor()
        rt = _make_runtime(tmp_path, decision_advisor=advisor, enable_hooks=True)
        captured_events: list[Any] = []
        rt.event_bus.subscribe(captured_events.append)
        store = rt.store
        project = tmp_path / "project"
        docs = project / "docs"
        docs.mkdir(parents=True)
        (docs / "auth-storage-adr.md").write_text(
            "# Auth Storage ADR\n\nDecision: migrate token storage behind a repository boundary.\n",
            encoding="utf-8",
        )
        workspace = store.upsert_workspace(str(project))
        session = store.create_session(workspace_id=workspace["id"], title="design evidence")
        task = store.create_task(
            session_id=session["id"],
            task_type="edit",
            goal="Design the auth storage migration plan",
            plan=[],
            routing={"scenario": "doc_write"},
        )
        task = store.update_task(
            task_id=task["id"],
            changed_files=[
                {"path": "docs/auth-storage-adr.md", "summary": "documented migration design"},
            ],
            verification=[
                {"command": "markdown lint docs/auth-storage-adr.md", "status": "passed", "summary": "markdown ok"},
            ],
        )

        result = rt.orchestrator._complete_task(
            session_id=session["id"],
            task=task,
            summary="Drafted the auth storage migration ADR.",
            context={"workspace_root": str(project), "routing": {"scenario": "doc_write"}},
            skip_reflection=True,
        )

        assert result["status"] == "waiting_approval"
        evidence = result["structuredResult"]["completionEvidence"]
        assert evidence["productSurfaceAdvisor"]["payload"]["surface_type"] == "architecture_design"
        assert evidence["advisorRequestedEvidence"][0]["kind"] == "design_review"
        assert evidence["advisorRequestedEvidence"][0]["status"] == "missing"
        assert result["structuredResult"]["completionGate"]["status"] == "advisor_evidence_requested"
        evidence_events = [event for event in captured_events if event.type == "agent.evidence.requested"]
        assert len(evidence_events) == 1
        assert evidence_events[0].payload["surfaceType"] == "architecture_design"
        assert evidence_events[0].payload["evidenceRequests"][0]["target"] == "docs/auth-storage-adr.md"
        approval_event = next(event for event in captured_events if event.type == "approval.requested")
        request = approval_event.payload["request"]
        assert request["advisorRequestedEvidence"][0]["kind"] == "design_review"
        assert result["structuredResult"]["completionGate"]["advisorRequestedEvidence"][0]["kind"] == "design_review"

    def test_product_surface_advisor_nonblocking_evidence_event_is_generic(self, tmp_path: Any) -> None:
        class RecordingAdvisor:
            def advise(self, kind: str, input_context: dict[str, Any]) -> Any:
                if kind == "product_surface_decision":
                    return SimpleNamespace(
                        accepted=True,
                        source="llm",
                        rationale="The artifact is embedded firmware, so hardware evidence may be useful but is not blocking.",
                        fallback_reason=None,
                        proposal_id="surface_embedded_1",
                        confidence=0.83,
                        payload={
                            "surface_type": "embedded_firmware_update",
                            "recommended_verification": ["Capture UART telemetry on target hardware when available."],
                            "verification_intents": [
                                {"kind": "hardware_in_loop", "target": "UART telemetry"},
                            ],
                            "evidence_requests": [
                                {
                                    "kind": "hardware_in_loop",
                                    "summary": "Run the firmware on the target board and capture UART telemetry.",
                                    "target": "target board",
                                    "blocking": False,
                                },
                            ],
                        },
                    )
                return SimpleNamespace(
                    accepted=True,
                    source="llm",
                    rationale="The available build evidence is enough for this slice; hardware evidence can follow later.",
                    fallback_reason=None,
                    proposal_id="completion_embedded_1",
                    confidence=0.81,
                    payload={"is_complete": True, "surface_type": "embedded_firmware_update"},
                )

        advisor = RecordingAdvisor()
        rt = _make_runtime(tmp_path, decision_advisor=advisor, enable_hooks=True)
        captured_events: list[Any] = []
        rt.event_bus.subscribe(captured_events.append)
        store = rt.store
        project = tmp_path / "project"
        firmware = project / "firmware"
        firmware.mkdir(parents=True)
        (firmware / "main.c").write_text("int main(void) { return 0; }\n", encoding="utf-8")
        workspace = store.upsert_workspace(str(project))
        store.create_hook({
            "workspaceId": workspace["id"],
            "name": "Advisor evidence suggestion",
            "event": "on_evidence_requested",
            "action": {
                "type": "auto_verification_suggestion",
                "checks": ["advisor_requested_evidence"],
                "suggestion": "Review the advisor-requested evidence for this task.",
            },
        })
        session = store.create_session(workspace_id=workspace["id"], title="embedded evidence")
        task = store.create_task(
            session_id=session["id"],
            task_type="edit",
            goal="Update the embedded firmware startup path",
            plan=[],
            routing={"scenario": "code_edit"},
        )
        task = store.update_task(
            task_id=task["id"],
            changed_files=[{"path": "firmware/main.c", "summary": "updated startup path"}],
            verification=[
                {"command": "cmake --build build --target firmware", "status": "passed", "summary": "native firmware build passed"},
            ],
        )

        result = rt.orchestrator._complete_task(
            session_id=session["id"],
            task=task,
            summary="Updated firmware startup path and verified the native build.",
            context={"workspace_root": str(project), "routing": {"scenario": "code_edit"}},
            skip_reflection=True,
        )

        assert result["status"] == "completed"
        evidence = result["structuredResult"]["completionEvidence"]
        assert evidence["advisorRequestedEvidence"][0]["kind"] == "hardware_in_loop"
        assert evidence["advisorRequestedEvidence"][0]["status"] == "requested"
        evidence_events = [event for event in captured_events if event.type == "agent.evidence.requested"]
        assert len(evidence_events) == 1
        assert evidence_events[0].payload["surfaceType"] == "embedded_firmware_update"
        assert evidence_events[0].payload["evidenceRequests"][0]["kind"] == "hardware_in_loop"
        assert evidence_events[0].payload["blockingCount"] == 0
        assert evidence_events[0].payload["missingBlockingCount"] == 0
        hook_events = [event for event in captured_events if event.type == "hook.auto_verification_suggestion"]
        assert len(hook_events) == 1
        assert hook_events[0].payload["checks"] == ["advisor_requested_evidence"]

    def test_advisor_suggested_command_becomes_permission_gated_evidence_suggestion(self, tmp_path: Any) -> None:
        class RecordingAdvisor:
            def advise(self, kind: str, input_context: dict[str, Any]) -> Any:
                if kind == "product_surface_decision":
                    return SimpleNamespace(
                        accepted=True,
                        source="llm",
                        rationale="The advisor wants one extra domain-specific proof command.",
                        fallback_reason=None,
                        proposal_id="surface_command_1",
                        confidence=0.86,
                        payload={
                            "surface_type": "firmware_module",
                            "evidence_requests": [
                                {
                                    "kind": "target_probe",
                                    "summary": "Build the target probe binary when hardware is unavailable.",
                                    "target": "firmware/probe",
                                    "suggestedCommand": "cmake --build build --target probe",
                                    "blocking": False,
                                },
                            ],
                        },
                    )
                return SimpleNamespace(
                    accepted=True,
                    source="llm",
                    rationale="The available evidence is enough; the probe command is an optional follow-up.",
                    fallback_reason=None,
                    proposal_id="completion_command_1",
                    confidence=0.82,
                    payload={"is_complete": True, "surface_type": "firmware_module"},
                )

        advisor = RecordingAdvisor()
        rt = _make_runtime(tmp_path, decision_advisor=advisor, enable_hooks=True)
        captured_events: list[Any] = []
        rt.event_bus.subscribe(captured_events.append)
        store = rt.store
        project = tmp_path / "project"
        firmware = project / "firmware"
        firmware.mkdir(parents=True)
        (firmware / "main.c").write_text("int main(void) { return 0; }\n", encoding="utf-8")
        workspace = store.upsert_workspace(str(project))
        session = store.create_session(workspace_id=workspace["id"], title="advisor command")
        task = store.create_task(
            session_id=session["id"],
            task_type="edit",
            goal="Update the firmware probe module",
            plan=[],
            routing={"scenario": "code_edit"},
        )
        task = store.update_task(
            task_id=task["id"],
            changed_files=[{"path": "firmware/main.c", "summary": "updated probe path"}],
            verification=[
                {"command": "cmake --build build --target firmware", "status": "passed", "summary": "native build passed"},
            ],
        )

        result = rt.orchestrator._complete_task(
            session_id=session["id"],
            task=task,
            summary="Updated firmware probe module and verified the native build.",
            context={"workspace_root": str(project), "routing": {"scenario": "code_edit"}},
            skip_reflection=True,
        )

        assert result["status"] == "completed"
        evidence = result["structuredResult"]["completionEvidence"]
        suggestion = evidence["advisorEvidenceExecutionSuggestions"][0]
        assert suggestion["type"] == "run_command"
        assert suggestion["command"] == "cmake --build build --target probe"
        assert suggestion["requestKind"] == "target_probe"
        assert suggestion["executionMode"] == "suggestion_only"
        assert suggestion["executionState"] == "approval_required"
        assert suggestion["permissionDecision"] == "approval_required"
        assert suggestion["approvalKind"] == "run_command"
        assert suggestion["requiresApproval"] is True
        assert suggestion["adapterKind"] == "run_command"
        assert evidence["advisorEvidenceAdapters"]["status"] == "approval_required"
        assert evidence["advisorEvidenceAdapters"]["counts"]["approvalRequired"] == 1
        assert evidence["advisorEvidenceAdapters"]["adapters"][0]["adapterKind"] == "run_command"
        evidence_event = next(event for event in captured_events if event.type == "agent.evidence.requested")
        assert evidence_event.payload["executionSuggestions"][0]["command"] == "cmake --build build --target probe"
        assert evidence_event.payload["adapterSummary"]["status"] == "approval_required"
        assert not [
            event for event in captured_events
            if event.type == "approval.requested" and event.payload.get("kind") == "run_command"
        ]

    def test_blocking_advisor_suggested_command_waits_for_approval_then_completes(self, tmp_path: Any) -> None:
        command = "python -c \"print('advisor evidence ok')\""

        class RecordingAdvisor:
            def advise(self, kind: str, input_context: dict[str, Any]) -> Any:
                if kind == "product_surface_decision":
                    return SimpleNamespace(
                        accepted=True,
                        source="llm",
                        rationale="The advisor wants one blocking proof command before completion.",
                        fallback_reason=None,
                        proposal_id="surface_blocking_command_1",
                        confidence=0.88,
                        payload={
                            "surface_type": "generic_runtime_artifact",
                            "evidence_requests": [
                                {
                                    "kind": "runtime_probe",
                                    "summary": "Run the generic probe command before completing.",
                                    "target": "runtime evidence",
                                    "suggestedCommand": command,
                                    "blocking": True,
                                },
                            ],
                        },
                    )
                return SimpleNamespace(
                    accepted=True,
                    source="llm",
                    rationale="Completion is acceptable once the requested evidence is present.",
                    fallback_reason=None,
                    proposal_id="completion_blocking_command_1",
                    confidence=0.84,
                    payload={"is_complete": True, "surface_type": "generic_runtime_artifact"},
                )

        advisor = RecordingAdvisor()
        rt = _make_runtime(tmp_path, decision_advisor=advisor, enable_hooks=True, enable_run_command=True)
        captured_events: list[Any] = []
        rt.event_bus.subscribe(captured_events.append)
        store = rt.store
        project = tmp_path / "project"
        src = project / "src"
        src.mkdir(parents=True)
        (src / "feature.py").write_text("VALUE = 1\n", encoding="utf-8")
        workspace = store.upsert_workspace(str(project))
        session = store.create_session(workspace_id=workspace["id"], title="blocking advisor command")
        task = store.create_task(
            session_id=session["id"],
            task_type="edit",
            goal="Update the runtime artifact",
            plan=[],
            routing={"scenario": "code_edit"},
        )
        task = store.update_task(
            task_id=task["id"],
            changed_files=[{"path": "src/feature.py", "summary": "updated runtime artifact"}],
            verification=[
                {"command": "python -m pytest", "status": "passed", "summary": "tests passed"},
            ],
        )

        result = rt.orchestrator._complete_task(
            session_id=session["id"],
            task=task,
            summary="Updated the runtime artifact and verified tests.",
            context={"workspace_root": str(project), "routing": {"scenario": "code_edit"}},
            skip_reflection=True,
        )

        assert result["status"] == "waiting_approval"
        evidence = result["structuredResult"]["completionEvidence"]
        suggestion = evidence["advisorEvidenceExecutionSuggestions"][0]
        approval_id = suggestion["approvalId"]
        assert suggestion["executionMode"] == "approval_then_run_command"
        assert suggestion["executionState"] == "approval_pending"
        assert suggestion["approvalRequest"]["command"] == command
        assert result["structuredResult"]["completionGate"]["approvalIds"] == [approval_id]
        executor = evidence["advisorEvidenceExecutor"][0]
        assert executor["id"]
        assert executor["status"] == "approval_requested"
        assert executor["executionType"] == "run_command"
        assert executor["approvalId"] == approval_id
        approval_events = [
            event for event in captured_events
            if event.type == "approval.requested" and event.payload.get("kind") == "run_command"
        ]
        assert len(approval_events) == 1
        assert approval_events[0].payload["approvalId"] == approval_id
        assert approval_events[0].payload["advisorEvidence"]["requestKind"] == "runtime_probe"
        assert approval_events[0].payload["advisorEvidence"]["executorId"] == executor["id"]
        executor_events = [event for event in captured_events if event.type == "agent.evidence.executor.updated"]
        assert executor_events[-1].payload["executor"]["id"] == executor["id"]
        assert executor_events[-1].payload["executor"]["status"] == "approval_requested"
        completion_reviews = store._conn.execute(
            "SELECT * FROM approvals WHERE task_id = ? AND kind = ?",
            (task["id"], "completion_review"),
        ).fetchall()
        assert completion_reviews == []

        approved = rt.orchestrator.submit_approval({"approvalId": approval_id, "decision": "approved"})
        completed_task = approved["task"]
        assert completed_task["status"] == "completed"
        completed_evidence = completed_task["structuredResult"]["completionEvidence"]
        assert completed_evidence["advisorRequestedEvidence"][0]["status"] == "satisfied"
        assert completed_task["commands"][0]["command"] == command
        assert completed_task["commands"][0]["status"] == "completed"
        transitions = [
            event.payload["executor"]["status"]
            for event in captured_events
            if event.type == "agent.evidence.executor.updated"
            and event.payload["executor"]["id"] == executor["id"]
        ]
        assert transitions[-2:] == ["running", "satisfied"]

    def test_blocking_advisor_suggested_command_list_creates_independent_approvals(self, tmp_path: Any) -> None:
        commands = [
            "python -c \"print('server smoke ok')\"",
            "python -c \"print('persistence smoke ok')\"",
        ]

        class RecordingAdvisor:
            def advise(self, kind: str, input_context: dict[str, Any]) -> Any:
                if kind == "product_surface_decision":
                    return SimpleNamespace(
                        accepted=True,
                        source="llm",
                        rationale="The advisor wants two proof commands before completion.",
                        fallback_reason=None,
                        proposal_id="surface_blocking_command_list_1",
                        confidence=0.88,
                        payload={
                            "surface_type": "fullstack_local_flow",
                            "evidence_requests": [
                                {
                                    "kind": "flow_probe",
                                    "summary": "Run server and persistence proof commands before completing.",
                                    "target": "local flow evidence",
                                    "suggestedCommands": commands,
                                    "blocking": True,
                                },
                            ],
                        },
                    )
                return SimpleNamespace(
                    accepted=True,
                    source="llm",
                    rationale="Completion is acceptable once the requested probes pass.",
                    fallback_reason=None,
                    proposal_id="completion_blocking_command_list_1",
                    confidence=0.84,
                    payload={"is_complete": True, "surface_type": "fullstack_local_flow"},
                )

        advisor = RecordingAdvisor()
        rt = _make_runtime(tmp_path, decision_advisor=advisor, enable_hooks=True, enable_run_command=True)
        captured_events: list[Any] = []
        rt.event_bus.subscribe(captured_events.append)
        store = rt.store
        project = tmp_path / "project"
        src = project / "src"
        src.mkdir(parents=True)
        (src / "feature.py").write_text("VALUE = 1\n", encoding="utf-8")
        workspace = store.upsert_workspace(str(project))
        session = store.create_session(workspace_id=workspace["id"], title="blocking advisor command list")
        task = store.create_task(
            session_id=session["id"],
            task_type="edit",
            goal="Update the full-stack local flow",
            plan=[],
            routing={"scenario": "code_edit"},
        )
        task = store.update_task(
            task_id=task["id"],
            changed_files=[{"path": "src/feature.py", "summary": "updated runtime artifact"}],
            verification=[
                {"command": "python -m pytest", "status": "passed", "summary": "tests passed"},
            ],
        )

        result = rt.orchestrator._complete_task(
            session_id=session["id"],
            task=task,
            summary="Updated the full-stack local flow and verified tests.",
            context={"workspace_root": str(project), "routing": {"scenario": "code_edit"}},
            skip_reflection=True,
        )

        assert result["status"] == "waiting_approval"
        evidence = result["structuredResult"]["completionEvidence"]
        requested = evidence["advisorRequestedEvidence"]
        suggestions = evidence["advisorEvidenceExecutionSuggestions"]
        approval_ids = result["structuredResult"]["completionGate"]["approvalIds"]
        assert [item["suggestedCommand"] for item in requested] == commands
        assert [item["command"] for item in suggestions] == commands
        assert len(approval_ids) == 2
        assert all(item["executionMode"] == "approval_then_run_command" for item in suggestions)
        approval_events = [
            event for event in captured_events
            if event.type == "approval.requested" and event.payload.get("kind") == "run_command"
        ]
        assert len(approval_events) == 2

    def test_blocking_advisor_suggested_command_with_allow_still_requires_approval(self, tmp_path: Any) -> None:
        command = "python -c \"print('advisor allow evidence ok')\""

        class RecordingAdvisor:
            def advise(self, kind: str, input_context: dict[str, Any]) -> Any:
                if kind == "product_surface_decision":
                    return SimpleNamespace(
                        accepted=True,
                        source="llm",
                        rationale="The advisor wants explicit proof even though shell is generally allowed.",
                        fallback_reason=None,
                        proposal_id="surface_blocking_allowed_command_1",
                        confidence=0.88,
                        payload={
                            "surface_type": "generic_runtime_artifact",
                            "evidence_requests": [
                                {
                                    "kind": "runtime_probe",
                                    "summary": "Run the generic proof command before completion.",
                                    "target": "runtime evidence",
                                    "suggestedCommand": command,
                                    "blocking": True,
                                },
                            ],
                        },
                    )
                return SimpleNamespace(
                    accepted=True,
                    source="llm",
                    rationale="Completion is acceptable once the requested proof command succeeds.",
                    fallback_reason=None,
                    proposal_id="completion_blocking_allowed_command_1",
                    confidence=0.84,
                    payload={"is_complete": True, "surface_type": "generic_runtime_artifact"},
                )

        advisor = RecordingAdvisor()
        rt = _make_runtime(tmp_path, decision_advisor=advisor, enable_hooks=True, enable_run_command=True)
        captured_events: list[Any] = []
        rt.event_bus.subscribe(captured_events.append)
        store = rt.store
        store.update_config({
            "permissions": {
                "capabilities": {
                    "runCommand": {"mode": "allow", "scope": "*"},
                },
            },
        })
        project = tmp_path / "project"
        src = project / "src"
        src.mkdir(parents=True)
        (src / "feature.py").write_text("VALUE = 1\n", encoding="utf-8")
        workspace = store.upsert_workspace(str(project))
        session = store.create_session(workspace_id=workspace["id"], title="allowed advisor command")
        task = store.create_task(
            session_id=session["id"],
            task_type="edit",
            goal="Update the runtime artifact",
            plan=[],
            routing={"scenario": "code_edit"},
        )
        task = store.update_task(
            task_id=task["id"],
            changed_files=[{"path": "src/feature.py", "summary": "updated runtime artifact"}],
            verification=[
                {"command": "python -m pytest", "status": "passed", "summary": "tests passed"},
            ],
        )

        result = rt.orchestrator._complete_task(
            session_id=session["id"],
            task=task,
            summary="Updated the runtime artifact and verified tests.",
            context={"workspace_root": str(project), "routing": {"scenario": "code_edit"}},
            skip_reflection=True,
        )

        assert result["status"] == "waiting_approval"
        evidence = result["structuredResult"]["completionEvidence"]
        suggestion = evidence["advisorEvidenceExecutionSuggestions"][0]
        approval_id = suggestion["approvalId"]
        assert suggestion["permissionDecision"] == "allow"
        assert suggestion["requiresApproval"] is True
        assert suggestion["executionMode"] == "approval_then_run_command"
        assert suggestion["executionState"] == "approval_pending"
        assert suggestion["approvalRequest"]["command"] == command
        assert result["structuredResult"]["completionGate"]["approvalIds"] == [approval_id]
        approval_events = [
            event for event in captured_events
            if event.type == "approval.requested" and event.payload.get("kind") == "run_command"
        ]
        assert len(approval_events) == 1
        completion_reviews = store._conn.execute(
            "SELECT * FROM approvals WHERE task_id = ? AND kind = ?",
            (task["id"], "completion_review"),
        ).fetchall()
        assert completion_reviews == []

        approved = rt.orchestrator.submit_approval({"approvalId": approval_id, "decision": "approved"})
        completed_task = approved["task"]
        assert completed_task["status"] == "completed"
        assert completed_task["structuredResult"]["completionEvidence"]["advisorRequestedEvidence"][0]["status"] == "satisfied"
        assert completed_task["commands"][0]["command"] == command

    def test_blocking_advisor_suggested_tool_waits_for_approval_then_completes(self, tmp_path: Any) -> None:
        class RecordingAdvisor:
            def advise(self, kind: str, input_context: dict[str, Any]) -> Any:
                if kind == "product_surface_decision":
                    return SimpleNamespace(
                        accepted=True,
                        source="llm",
                        rationale="The advisor wants to inspect the produced artifact before completion.",
                        fallback_reason=None,
                        proposal_id="surface_blocking_tool_1",
                        confidence=0.88,
                        payload={
                            "surface_type": "generic_runtime_artifact",
                            "evidence_requests": [
                                {
                                    "kind": "artifact_readback",
                                    "summary": "Read back the generated README artifact.",
                                    "target": "README.md",
                                    "suggestedTool": {
                                        "name": "read_file",
                                        "arguments": {"path": "README.md"},
                                    },
                                    "blocking": True,
                                },
                            ],
                        },
                    )
                return SimpleNamespace(
                    accepted=True,
                    source="llm",
                    rationale="Completion is acceptable once the requested readback evidence is present.",
                    fallback_reason=None,
                    proposal_id="completion_blocking_tool_1",
                    confidence=0.84,
                    payload={"is_complete": True, "surface_type": "generic_runtime_artifact"},
                )

        advisor = RecordingAdvisor()
        rt = _make_runtime(tmp_path, decision_advisor=advisor, enable_hooks=True)
        captured_events: list[Any] = []
        rt.event_bus.subscribe(captured_events.append)
        store = rt.store
        store.update_config({
            "config": {
                "permissions": {
                    "capabilities": {
                        "readFile": {"mode": "ask", "scope": "*"},
                    },
                },
            },
        })
        project = tmp_path / "project"
        project.mkdir()
        (project / "README.md").write_text("# Artifact\n\nRuntime evidence.\n", encoding="utf-8")
        workspace = store.upsert_workspace(str(project))
        session = store.create_session(workspace_id=workspace["id"], title="blocking advisor tool")
        task = store.create_task(
            session_id=session["id"],
            task_type="edit",
            goal="Update the runtime artifact README",
            plan=[],
            routing={"scenario": "doc_write"},
        )
        task = store.update_task(
            task_id=task["id"],
            changed_files=[{"path": "README.md", "summary": "updated artifact readme"}],
            verification=[
                {"command": "markdown lint README.md", "status": "passed", "summary": "markdown ok"},
            ],
        )

        result = rt.orchestrator._complete_task(
            session_id=session["id"],
            task=task,
            summary="Updated the runtime artifact README.",
            context={"workspace_root": str(project), "routing": {"scenario": "doc_write"}},
            skip_reflection=True,
        )

        assert result["status"] == "waiting_approval"
        evidence = result["structuredResult"]["completionEvidence"]
        suggestion = evidence["advisorEvidenceExecutionSuggestions"][0]
        approval_id = suggestion["approvalId"]
        assert suggestion["type"] == "tool"
        assert suggestion["toolName"] == "read_file"
        assert suggestion["arguments"] == {"path": "README.md"}
        assert suggestion["executionMode"] == "approval_then_tool"
        assert suggestion["approvalKind"] == "advisor_tool"
        assert suggestion["permissionDecision"] == "approval_required"
        assert result["structuredResult"]["completionGate"]["approvalIds"] == [approval_id]
        executor = evidence["advisorEvidenceExecutor"][0]
        assert executor["status"] == "approval_requested"
        assert executor["executionType"] == "tool"
        assert executor["toolName"] == "read_file"
        assert executor["approvalId"] == approval_id
        approval_events = [
            event for event in captured_events
            if event.type == "approval.requested" and event.payload.get("kind") == "advisor_tool"
        ]
        assert len(approval_events) == 1
        assert approval_events[0].payload["advisorEvidence"]["requestKind"] == "artifact_readback"
        assert approval_events[0].payload["advisorEvidence"]["executorId"] == executor["id"]
        completion_reviews = store._conn.execute(
            "SELECT * FROM approvals WHERE task_id = ? AND kind = ?",
            (task["id"], "completion_review"),
        ).fetchall()
        assert completion_reviews == []

        approved = rt.orchestrator.submit_approval({"approvalId": approval_id, "decision": "approved"})
        completed_task = approved["task"]
        assert completed_task["status"] == "completed"
        completed_evidence = completed_task["structuredResult"]["completionEvidence"]
        assert completed_evidence["advisorRequestedEvidence"][0]["status"] == "satisfied"
        tool_evidence = completed_evidence["toolResults"][0]
        assert tool_evidence["name"] == "read_file"
        assert tool_evidence["status"] == "completed"
        transitions = [
            event.payload["executor"]["status"]
            for event in captured_events
            if event.type == "agent.evidence.executor.updated"
            and event.payload["executor"]["id"] == executor["id"]
        ]
        assert transitions[-2:] == ["running", "satisfied"]

    def test_blocking_advisor_suggested_mcp_tool_waits_for_approval_then_completes(self, tmp_path: Any) -> None:
        class RecordingAdvisor:
            def advise(self, kind: str, input_context: dict[str, Any]) -> Any:
                if kind == "product_surface_decision":
                    return SimpleNamespace(
                        accepted=True,
                        source="llm",
                        rationale="The advisor wants an MCP knowledge-base readback before completion.",
                        fallback_reason=None,
                        proposal_id="surface_blocking_mcp_tool_1",
                        confidence=0.88,
                        payload={
                            "surface_type": "release_notes_with_external_context",
                            "evidence_requests": [
                                {
                                    "kind": "mcp_readback",
                                    "summary": "Read the release checklist through the MCP knowledge base.",
                                    "target": "release checklist",
                                    "suggestedTool": {
                                        "name": "mcp__kb__lookup",
                                        "arguments": {"query": "release checklist"},
                                    },
                                    "blocking": True,
                                },
                            ],
                        },
                    )
                return SimpleNamespace(
                    accepted=True,
                    source="llm",
                    rationale="Completion is acceptable once the requested MCP readback is present.",
                    fallback_reason=None,
                    proposal_id="completion_blocking_mcp_tool_1",
                    confidence=0.84,
                    payload={"is_complete": True, "surface_type": "release_notes_with_external_context"},
                )

        advisor = RecordingAdvisor()
        rt = _make_runtime(tmp_path, decision_advisor=advisor, enable_hooks=True)
        rt.orchestrator._tool_registry.register(
            "mcp__kb__lookup",
            lambda params: {
                "status": "completed",
                "content": f"KB result for {params['query']}",
            },
            {"name": "mcp__kb__lookup", "description": "Lookup KB content."},
        )
        captured_events: list[Any] = []
        rt.event_bus.subscribe(captured_events.append)
        store = rt.store
        project = tmp_path / "project"
        project.mkdir()
        (project / "release.md").write_text("# Release\n\nChecklist updated.\n", encoding="utf-8")
        workspace = store.upsert_workspace(str(project))
        session = store.create_session(workspace_id=workspace["id"], title="blocking advisor mcp tool")
        task = store.create_task(
            session_id=session["id"],
            task_type="edit",
            goal="Update release notes using external KB context",
            plan=[],
            routing={"scenario": "doc_write"},
        )
        task = store.update_task(
            task_id=task["id"],
            changed_files=[{"path": "release.md", "summary": "updated release notes"}],
            verification=[
                {"command": "git diff -- release.md", "status": "passed", "summary": "release notes reviewed"},
            ],
        )

        result = rt.orchestrator._complete_task(
            session_id=session["id"],
            task=task,
            summary="Updated release notes.",
            context={"workspace_root": str(project), "routing": {"scenario": "doc_write"}},
            skip_reflection=True,
        )

        assert result["status"] == "waiting_approval"
        evidence = result["structuredResult"]["completionEvidence"]
        suggestion = evidence["advisorEvidenceExecutionSuggestions"][0]
        approval_id = suggestion["approvalId"]
        assert suggestion["type"] == "tool"
        assert suggestion["toolName"] == "mcp__kb__lookup"
        assert suggestion["capability"] == "mcpTool"
        assert suggestion["executionMode"] == "approval_then_tool"
        assert suggestion["permissionDecision"] == "approval_required"
        assert result["structuredResult"]["completionGate"]["approvalIds"] == [approval_id]
        approval_events = [
            event for event in captured_events
            if event.type == "approval.requested" and event.payload.get("kind") == "advisor_tool"
        ]
        assert len(approval_events) == 1

        approved = rt.orchestrator.submit_approval({"approvalId": approval_id, "decision": "approved"})
        completed_task = approved["task"]
        assert completed_task["status"] == "completed"
        completed_evidence = completed_task["structuredResult"]["completionEvidence"]
        assert completed_evidence["advisorRequestedEvidence"][0]["status"] == "satisfied"
        tool_evidence = completed_evidence["toolResults"][0]
        assert tool_evidence["name"] == "mcp__kb__lookup"
        assert tool_evidence["status"] == "completed"

    def test_advisor_suggested_mcp_tool_respects_mcp_policy_denial(self, tmp_path: Any) -> None:
        class RecordingAdvisor:
            def advise(self, kind: str, input_context: dict[str, Any]) -> Any:
                if kind == "product_surface_decision":
                    return SimpleNamespace(
                        accepted=True,
                        source="llm",
                        rationale="The advisor suggested an optional MCP lookup.",
                        fallback_reason=None,
                        proposal_id="surface_denied_mcp_tool_1",
                        confidence=0.82,
                        payload={
                            "surface_type": "release_notes",
                            "evidence_requests": [
                                {
                                    "kind": "mcp_readback",
                                    "summary": "Optionally read the release checklist through MCP.",
                                    "target": "release checklist",
                                    "suggestedTool": {
                                        "name": "mcp__kb__lookup",
                                        "arguments": {"query": "release checklist"},
                                    },
                                    "blocking": False,
                                },
                            ],
                        },
                    )
                return SimpleNamespace(
                    accepted=True,
                    source="llm",
                    rationale="The optional MCP lookup is policy-denied and tracked as a follow-up.",
                    fallback_reason=None,
                    proposal_id="completion_denied_mcp_tool_1",
                    confidence=0.8,
                    payload={"is_complete": True, "surface_type": "release_notes"},
                )

        advisor = RecordingAdvisor()
        rt = _make_runtime(tmp_path, decision_advisor=advisor, enable_hooks=True)
        rt.orchestrator._tool_registry.register(
            "mcp__kb__lookup",
            lambda _params: {"status": "completed", "content": "KB result"},
            {"name": "mcp__kb__lookup", "description": "Lookup KB content."},
        )
        captured_events: list[Any] = []
        rt.event_bus.subscribe(captured_events.append)
        store = rt.store
        project = tmp_path / "project"
        project.mkdir()
        (project / "release.md").write_text("# Release\n\nChecklist updated.\n", encoding="utf-8")
        workspace = store.upsert_workspace(str(project))
        session = store.create_session(workspace_id=workspace["id"], title="denied advisor mcp tool")
        task = store.create_task(
            session_id=session["id"],
            task_type="edit",
            goal="Update release notes",
            plan=[],
            routing={"scenario": "doc_write"},
        )
        task = store.update_task(
            task_id=task["id"],
            changed_files=[{"path": "release.md", "summary": "updated release notes"}],
            verification=[
                {"command": "git diff -- release.md", "status": "passed", "summary": "release notes reviewed"},
            ],
        )

        result = rt.orchestrator._complete_task(
            session_id=session["id"],
            task=task,
            summary="Updated release notes.",
            context={
                "workspace_root": str(project),
                "routing": {"scenario": "doc_write"},
                "mcpPolicy": {"mode": "disabled"},
            },
            skip_reflection=True,
        )

        assert result["status"] == "completed"
        suggestion = result["structuredResult"]["completionEvidence"]["advisorEvidenceExecutionSuggestions"][0]
        assert suggestion["toolName"] == "mcp__kb__lookup"
        assert suggestion["permissionDecision"] == "deny"
        assert suggestion["executionState"] == "denied_by_policy"
        assert suggestion["requiresApproval"] is False
        assert "disabled" in suggestion["permissionReason"]
        executor = result["structuredResult"]["completionEvidence"]["advisorEvidenceExecutor"][0]
        assert executor["status"] == "blocked"
        assert executor["toolName"] == "mcp__kb__lookup"
        assert executor["permissionDecision"] == "deny"
        assert not [
            event for event in captured_events
            if event.type == "approval.requested" and event.payload.get("kind") == "advisor_tool"
        ]

    def test_advisor_suggested_tool_denies_unregistered_tool_without_execution(self, tmp_path: Any) -> None:
        class RecordingAdvisor:
            def advise(self, kind: str, input_context: dict[str, Any]) -> Any:
                if kind == "product_surface_decision":
                    return SimpleNamespace(
                        accepted=True,
                        source="llm",
                        rationale="The advisor suggested a hardware probe tool that is not registered here.",
                        fallback_reason=None,
                        proposal_id="surface_unknown_tool_1",
                        confidence=0.82,
                        payload={
                            "surface_type": "embedded_firmware_update",
                            "evidence_requests": [
                                {
                                    "kind": "hardware_probe",
                                    "summary": "Capture target-board telemetry when the tool is available.",
                                    "target": "target board",
                                    "suggestedTool": {
                                        "name": "hardware_probe",
                                        "arguments": {"port": "COM3"},
                                    },
                                    "blocking": False,
                                },
                            ],
                        },
                    )
                return SimpleNamespace(
                    accepted=True,
                    source="llm",
                    rationale="The optional probe is tracked as follow-up evidence.",
                    fallback_reason=None,
                    proposal_id="completion_unknown_tool_1",
                    confidence=0.8,
                    payload={"is_complete": True, "surface_type": "embedded_firmware_update"},
                )

        advisor = RecordingAdvisor()
        rt = _make_runtime(tmp_path, decision_advisor=advisor, enable_hooks=True)
        captured_events: list[Any] = []
        rt.event_bus.subscribe(captured_events.append)
        store = rt.store
        project = tmp_path / "project"
        src = project / "firmware"
        src.mkdir(parents=True)
        (src / "main.c").write_text("int main(void) { return 0; }\n", encoding="utf-8")
        workspace = store.upsert_workspace(str(project))
        session = store.create_session(workspace_id=workspace["id"], title="unknown advisor tool")
        task = store.create_task(
            session_id=session["id"],
            task_type="edit",
            goal="Update the firmware startup path",
            plan=[],
            routing={"scenario": "code_edit"},
        )
        task = store.update_task(
            task_id=task["id"],
            changed_files=[{"path": "firmware/main.c", "summary": "updated startup path"}],
            verification=[
                {"command": "cmake --build build --target firmware", "status": "passed", "summary": "native build passed"},
            ],
        )

        result = rt.orchestrator._complete_task(
            session_id=session["id"],
            task=task,
            summary="Updated firmware startup path and verified the native build.",
            context={"workspace_root": str(project), "routing": {"scenario": "code_edit"}},
            skip_reflection=True,
        )

        assert result["status"] == "completed"
        suggestion = result["structuredResult"]["completionEvidence"]["advisorEvidenceExecutionSuggestions"][0]
        assert suggestion["type"] == "tool"
        assert suggestion["toolName"] == "hardware_probe"
        assert suggestion["permissionDecision"] == "deny"
        assert suggestion["executionState"] == "denied_by_policy"
        assert suggestion["requiresApproval"] is False
        assert "not registered" in suggestion["permissionReason"]
        evidence = result["structuredResult"]["completionEvidence"]
        assert evidence["advisorEvidenceAdapters"]["status"] == "blocked"
        assert evidence["advisorEvidenceAdapters"]["counts"]["blocked"] == 1
        assert evidence["advisorEvidenceAdapters"]["adapters"][0]["adapterKind"] == "tool"
        evidence_event = next(event for event in captured_events if event.type == "agent.evidence.requested")
        assert evidence_event.payload["executionSuggestions"][0]["executionState"] == "denied_by_policy"
        assert evidence_event.payload["adapterSummary"]["status"] == "blocked"
        assert not [
            event for event in captured_events
            if event.type == "approval.requested" and event.payload.get("kind") == "advisor_tool"
        ]

    def test_advisor_browser_inspection_maps_to_registered_adapter_without_hard_gate(self, tmp_path: Any) -> None:
        class RecordingAdvisor:
            def __init__(self) -> None:
                self.calls: list[tuple[str, dict[str, Any]]] = []

            def advise(self, kind: str, input_context: dict[str, Any]) -> Any:
                self.calls.append((kind, input_context))
                if kind == "product_surface_decision":
                    return SimpleNamespace(
                        accepted=True,
                        source="llm",
                        rationale="The advisor selected browser inspection as useful page evidence.",
                        fallback_reason=None,
                        proposal_id="surface_browser_1",
                        confidence=0.86,
                        payload={
                            "surface_type": "static_page",
                            "evidence_requests": [
                                {
                                    "kind": "browser_inspection",
                                    "summary": "Inspect the rendered documentation page.",
                                    "target": "http://localhost:4173/docs",
                                    "suggestedTool": {
                                        "name": "browser",
                                        "arguments": {"url": "http://localhost:4173/docs", "action": "read"},
                                    },
                                    "blocking": False,
                                },
                            ],
                        },
                    )
                assert input_context["advisor_evidence_adapters"]["adapters"][0]["adapterKind"] == "browser_inspection_adapter"
                return SimpleNamespace(
                    accepted=True,
                    source="llm",
                    rationale="Browser inspection is tracked as optional evidence; current docs proof is enough.",
                    fallback_reason=None,
                    proposal_id="completion_browser_1",
                    confidence=0.82,
                    payload={"is_complete": True, "surface_type": "static_page"},
                )

        advisor = RecordingAdvisor()
        rt = _make_runtime(tmp_path, decision_advisor=advisor, enable_hooks=True)
        rt.orchestrator._tool_registry.register(  # noqa: SLF001
            "browser",
            lambda params: {"status": "ok", "url": params.get("url"), "content": "Docs page"},
            {"name": "browser", "description": "Inspect browser content."},
        )
        captured_events: list[Any] = []
        rt.event_bus.subscribe(captured_events.append)
        store = rt.store
        store.update_config({
            "permissions": {
                "capabilities": {
                    "browserAutomation": {"mode": "ask", "scope": "*"},
                },
            },
        })
        project = tmp_path / "project"
        project.mkdir()
        (project / "docs.html").write_text("<main>Docs page</main>\n", encoding="utf-8")
        workspace = store.upsert_workspace(str(project))
        session = store.create_session(workspace_id=workspace["id"], title="browser adapter")
        task = store.create_task(
            session_id=session["id"],
            task_type="edit",
            goal="Create a static docs page",
            plan=[],
            routing={"scenario": "doc_write"},
        )
        task = store.update_task(
            task_id=task["id"],
            changed_files=[{"path": "docs.html", "summary": "created docs page"}],
            verification=[{"command": "git diff -- docs.html", "status": "passed", "summary": "docs reviewed"}],
        )

        result = rt.orchestrator._complete_task(
            session_id=session["id"],
            task=task,
            summary="Created the static docs page.",
            context={"workspace_root": str(project), "routing": {"scenario": "doc_write"}},
            skip_reflection=True,
        )

        assert result["status"] == "completed"
        evidence = result["structuredResult"]["completionEvidence"]
        suggestion = evidence["advisorEvidenceExecutionSuggestions"][0]
        assert suggestion["toolName"] == "browser"
        assert suggestion["adapterKind"] == "browser_inspection_adapter"
        assert evidence["advisorEvidenceAdapters"]["status"] in {"ready", "approval_required"}
        assert evidence["advisorEvidenceAdapters"]["adapters"][0]["adapterKind"] == "browser_inspection_adapter"
        evidence_event = next(event for event in captured_events if event.type == "agent.evidence.requested")
        assert evidence_event.payload["adapterSummary"]["adapters"][0]["adapterKind"] == "browser_inspection_adapter"

    def test_advisor_browser_inspection_without_suggested_tool_uses_adapter_registry(self, tmp_path: Any) -> None:
        class RecordingAdvisor:
            def __init__(self) -> None:
                self.completion_inputs: list[dict[str, Any]] = []

            def advise(self, kind: str, input_context: dict[str, Any]) -> Any:
                if kind == "product_surface_decision":
                    return SimpleNamespace(
                        accepted=True,
                        source="llm",
                        rationale="The advisor asks for browser evidence without naming a tool.",
                        fallback_reason=None,
                        proposal_id="surface_browser_registry_1",
                        confidence=0.86,
                        payload={
                            "surface_type": "rendered_page",
                            "evidence_requests": [
                                {
                                    "kind": "browser_inspection",
                                    "summary": "Inspect the rendered page.",
                                    "target": "http://localhost:4173/docs",
                                    "blocking": False,
                                },
                            ],
                        },
                    )
                self.completion_inputs.append(input_context)
                return SimpleNamespace(
                    accepted=True,
                    source="llm",
                    rationale="Browser evidence is optional and adapter availability is tracked.",
                    fallback_reason=None,
                    proposal_id="completion_browser_registry_1",
                    confidence=0.82,
                    payload={"is_complete": True, "surface_type": "rendered_page"},
                )

        advisor = RecordingAdvisor()
        rt = _make_runtime(tmp_path, decision_advisor=advisor, enable_hooks=True)
        store = rt.store
        store.update_config({
            "permissions": {
                "capabilities": {
                    "browserAutomation": {"mode": "ask", "scope": "*"},
                },
            },
        })
        rt.orchestrator._tool_registry.register(  # noqa: SLF001
            "browser",
            lambda params: {"status": "ok", "url": params.get("url"), "content": "Docs page"},
            {"name": "browser", "description": "Inspect browser content."},
        )
        captured_events: list[Any] = []
        rt.event_bus.subscribe(captured_events.append)
        project = tmp_path / "project"
        project.mkdir()
        (project / "docs.html").write_text("<main>Docs page</main>\n", encoding="utf-8")
        workspace = store.upsert_workspace(str(project))
        session = store.create_session(workspace_id=workspace["id"], title="browser registry adapter")
        task = store.create_task(
            session_id=session["id"],
            task_type="edit",
            goal="Create a rendered docs page",
            plan=[],
            routing={"scenario": "doc_write"},
        )
        task = store.update_task(
            task_id=task["id"],
            changed_files=[{"path": "docs.html", "summary": "created docs page"}],
            verification=[{"command": "git diff -- docs.html", "status": "passed", "summary": "docs reviewed"}],
        )

        result = rt.orchestrator._complete_task(
            session_id=session["id"],
            task=task,
            summary="Created the rendered docs page.",
            context={"workspace_root": str(project), "routing": {"scenario": "doc_write"}},
            skip_reflection=True,
        )

        assert result["status"] == "completed"
        evidence = result["structuredResult"]["completionEvidence"]
        suggestion = evidence["advisorEvidenceExecutionSuggestions"][0]
        assert suggestion["source"] == "runtime_evidence_adapter_registry"
        assert suggestion["toolName"] == "browser"
        assert suggestion["arguments"] == {"url": "http://localhost:4173/docs", "action": "read"}
        assert suggestion["adapterKind"] == "browser_inspection_adapter"
        assert suggestion["executionMode"] == "adapter_suggestion"
        executor = evidence["advisorEvidenceExecutor"][0]
        assert executor["source"] == "runtime_evidence_adapter_registry"
        assert executor["adapterKind"] == "browser_inspection_adapter"
        assert advisor.completion_inputs[0]["advisor_evidence_adapters"]["adapters"][0]["adapterKind"] == "browser_inspection_adapter"
        evidence_event = next(event for event in captured_events if event.type == "agent.evidence.requested")
        assert evidence_event.payload["executionSuggestions"][0]["source"] == "runtime_evidence_adapter_registry"

    def test_blocking_registry_adapter_waits_for_approval_then_executes(self, tmp_path: Any) -> None:
        url = "http://localhost:4173/docs"

        class RecordingAdvisor:
            def advise(self, kind: str, input_context: dict[str, Any]) -> Any:
                if kind == "product_surface_decision":
                    return SimpleNamespace(
                        accepted=True,
                        source="llm",
                        rationale="The advisor requires browser inspection before completion.",
                        fallback_reason=None,
                        proposal_id="surface_browser_registry_blocking_1",
                        confidence=0.88,
                        payload={
                            "surface_type": "rendered_page",
                            "evidence_requests": [
                                {
                                    "kind": "browser_inspection",
                                    "summary": "Inspect the rendered page before completing.",
                                    "target": url,
                                    "blocking": True,
                                },
                            ],
                        },
                    )
                return SimpleNamespace(
                    accepted=True,
                    source="llm",
                    rationale="Completion is acceptable once browser inspection runs.",
                    fallback_reason=None,
                    proposal_id="completion_browser_registry_blocking_1",
                    confidence=0.82,
                    payload={"is_complete": True, "surface_type": "rendered_page"},
                )

        advisor = RecordingAdvisor()
        rt = _make_runtime(tmp_path, decision_advisor=advisor, enable_hooks=True)
        store = rt.store
        store.update_config({
            "permissions": {
                "capabilities": {
                    "browserAutomation": {"mode": "ask", "scope": "*"},
                },
            },
        })
        rt.orchestrator._tool_registry.register(  # noqa: SLF001
            "browser",
            lambda params: {"status": "ok", "url": params.get("url"), "content": "Docs page"},
            {"name": "browser", "description": "Inspect browser content."},
        )
        captured_events: list[Any] = []
        rt.event_bus.subscribe(captured_events.append)
        project = tmp_path / "project"
        project.mkdir()
        (project / "docs.html").write_text("<main>Docs page</main>\n", encoding="utf-8")
        workspace = store.upsert_workspace(str(project))
        session = store.create_session(workspace_id=workspace["id"], title="blocking browser registry adapter")
        task = store.create_task(
            session_id=session["id"],
            task_type="edit",
            goal="Create a rendered docs page",
            plan=[],
            routing={"scenario": "doc_write"},
        )
        task = store.update_task(
            task_id=task["id"],
            changed_files=[{"path": "docs.html", "summary": "created docs page"}],
            verification=[{"command": "git diff -- docs.html", "status": "passed", "summary": "docs reviewed"}],
        )

        waiting = rt.orchestrator._complete_task(
            session_id=session["id"],
            task=task,
            summary="Created the rendered docs page.",
            context={"workspace_root": str(project), "routing": {"scenario": "doc_write"}},
            skip_reflection=True,
        )

        assert waiting["status"] == "waiting_approval"
        suggestion = waiting["structuredResult"]["completionEvidence"]["advisorEvidenceExecutionSuggestions"][0]
        approval_id = suggestion["approvalId"]
        assert suggestion["source"] == "runtime_evidence_adapter_registry"
        assert suggestion["executionMode"] == "approval_then_tool"
        assert suggestion["approvalRequest"]["toolName"] == "browser"
        assert suggestion["approvalRequest"]["arguments"] == {"url": url, "action": "read"}
        approval_events = [
            event for event in captured_events
            if event.type == "approval.requested" and event.payload.get("kind") == "advisor_tool"
        ]
        assert len(approval_events) == 1

        approved = rt.orchestrator.submit_approval({"approvalId": approval_id, "decision": "approved"})
        completed_task = approved["task"]
        assert completed_task["status"] == "completed"
        completed_evidence = completed_task["structuredResult"]["completionEvidence"]
        assert completed_evidence["advisorRequestedEvidence"][0]["status"] == "satisfied"
        assert completed_evidence["toolResults"][0]["name"] == "browser"
        assert completed_evidence["toolResults"][0]["approvalId"] == approval_id
        assert completed_evidence["toolResults"][0]["advisorEvidence"]["source"] == "runtime_evidence_adapter_registry"
        assert completed_evidence["toolResults"][0]["advisorEvidence"]["target"] == url

    def test_advisor_suggested_command_records_permission_denial_without_execution(self, tmp_path: Any) -> None:
        class RecordingAdvisor:
            def advise(self, kind: str, input_context: dict[str, Any]) -> Any:
                if kind == "product_surface_decision":
                    return SimpleNamespace(
                        accepted=True,
                        source="llm",
                        rationale="The advisor suggested a local command, but policy may forbid it.",
                        fallback_reason=None,
                        proposal_id="surface_denied_command_1",
                        confidence=0.84,
                        payload={
                            "surface_type": "migration_plan",
                            "evidence_requests": [
                                {
                                    "kind": "dry_run",
                                    "summary": "Run the migration dry-run before release.",
                                    "target": "database migration",
                                    "suggestedCommand": "python manage.py migrate --dry-run",
                                    "blocking": False,
                                },
                            ],
                        },
                    )
                return SimpleNamespace(
                    accepted=True,
                    source="llm",
                    rationale="The document is complete; the dry-run is tracked as a policy-gated follow-up.",
                    fallback_reason=None,
                    proposal_id="completion_denied_command_1",
                    confidence=0.8,
                    payload={"is_complete": True, "surface_type": "migration_plan"},
                )

        advisor = RecordingAdvisor()
        rt = _make_runtime(tmp_path, decision_advisor=advisor, enable_hooks=True)
        captured_events: list[Any] = []
        rt.event_bus.subscribe(captured_events.append)
        store = rt.store
        store.update_config({
            "permissions": {
                "capabilities": {
                    "runCommand": {"mode": "blocked", "scope": "*"},
                },
            },
        })
        project = tmp_path / "project"
        docs = project / "docs"
        docs.mkdir(parents=True)
        (docs / "migration.md").write_text("# Migration\n\nDry-run plan documented.\n", encoding="utf-8")
        workspace = store.upsert_workspace(str(project))
        session = store.create_session(workspace_id=workspace["id"], title="denied advisor command")
        task = store.create_task(
            session_id=session["id"],
            task_type="edit",
            goal="Document the migration plan",
            plan=[],
            routing={"scenario": "doc_write"},
        )
        task = store.update_task(
            task_id=task["id"],
            changed_files=[{"path": "docs/migration.md", "summary": "documented migration plan"}],
            verification=[
                {"command": "markdown lint docs/migration.md", "status": "passed", "summary": "markdown ok"},
            ],
        )

        result = rt.orchestrator._complete_task(
            session_id=session["id"],
            task=task,
            summary="Documented the migration plan.",
            context={"workspace_root": str(project), "routing": {"scenario": "doc_write"}},
            skip_reflection=True,
        )

        assert result["status"] == "completed"
        suggestion = result["structuredResult"]["completionEvidence"]["advisorEvidenceExecutionSuggestions"][0]
        assert suggestion["command"] == "python manage.py migrate --dry-run"
        assert suggestion["permissionDecision"] == "deny"
        assert suggestion["executionState"] == "denied_by_policy"
        assert suggestion["requiresApproval"] is False
        assert "blocked" in suggestion["permissionReason"]
        evidence_event = next(event for event in captured_events if event.type == "agent.evidence.requested")
        assert evidence_event.payload["executionSuggestions"][0]["executionState"] == "denied_by_policy"
        assert evidence_event.payload["executor"][0]["status"] == "blocked"
        assert evidence_event.payload["executor"][0]["command"] == "python manage.py migrate --dry-run"
        assert not [
            event for event in captured_events
            if event.type == "approval.requested" and event.payload.get("kind") == "run_command"
        ]

    def test_rejected_advisor_evidence_command_updates_executor_state(self, tmp_path: Any) -> None:
        command = "python -c \"print('should not run')\""

        class RecordingAdvisor:
            def advise(self, kind: str, input_context: dict[str, Any]) -> Any:
                if kind == "product_surface_decision":
                    return SimpleNamespace(
                        accepted=True,
                        source="llm",
                        rationale="The advisor wants a blocking proof command before completion.",
                        fallback_reason=None,
                        proposal_id="surface_rejected_command_1",
                        confidence=0.88,
                        payload={
                            "surface_type": "generic_runtime_artifact",
                            "evidence_requests": [
                                {
                                    "kind": "runtime_probe",
                                    "summary": "Run a proof command before completing.",
                                    "target": "runtime evidence",
                                    "suggestedCommand": command,
                                    "blocking": True,
                                },
                            ],
                        },
                    )
                return SimpleNamespace(
                    accepted=True,
                    source="llm",
                    rationale="Completion depends on the requested proof command.",
                    fallback_reason=None,
                    proposal_id="completion_rejected_command_1",
                    confidence=0.84,
                    payload={"is_complete": True, "surface_type": "generic_runtime_artifact"},
                )

        rt = _make_runtime(tmp_path, decision_advisor=RecordingAdvisor(), enable_hooks=True, enable_run_command=True)
        captured_events: list[Any] = []
        rt.event_bus.subscribe(captured_events.append)
        store = rt.store
        project = tmp_path / "project"
        project.mkdir()
        (project / "feature.py").write_text("VALUE = 1\n", encoding="utf-8")
        workspace = store.upsert_workspace(str(project))
        session = store.create_session(workspace_id=workspace["id"], title="rejected advisor command")
        task = store.create_task(
            session_id=session["id"],
            task_type="edit",
            goal="Update the runtime artifact",
            plan=[],
            routing={"scenario": "code_edit"},
        )
        task = store.update_task(
            task_id=task["id"],
            changed_files=[{"path": "feature.py", "summary": "updated runtime artifact"}],
            verification=[{"command": "python -m pytest", "status": "passed", "summary": "tests passed"}],
        )

        waiting = rt.orchestrator._complete_task(
            session_id=session["id"],
            task=task,
            summary="Updated the runtime artifact.",
            context={"workspace_root": str(project), "routing": {"scenario": "code_edit"}},
            skip_reflection=True,
        )

        approval_id = waiting["structuredResult"]["completionGate"]["approvalIds"][0]
        executor_id = waiting["structuredResult"]["completionEvidence"]["advisorEvidenceExecutor"][0]["id"]
        result = rt.orchestrator.submit_approval({"approvalId": approval_id, "decision": "rejected"})

        assert result["task"]["status"] == "failed"
        assert result["task"]["errorCode"] == "APPROVAL_REJECTED"
        transitions = [
            event.payload["executor"]["status"]
            for event in captured_events
            if event.type == "agent.evidence.executor.updated"
            and event.payload["executor"]["id"] == executor_id
        ]
        assert transitions[-1] == "rejected"

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

    def test_static_frontend_script_syntax_recovers_missing_node_command_from_runtime(self, tmp_path: Any, monkeypatch) -> None:
        rt = _make_runtime(tmp_path)
        store = rt.store
        project = tmp_path / "project"
        project.mkdir()
        (project / "index.html").write_text(
            '<!doctype html><script src="app.js"></script><main>Ready</main>\n',
            encoding="utf-8",
        )
        (project / "app.js").write_text("console.log('ready');\n", encoding="utf-8")
        node_dir = tmp_path / "node" / "bin"
        node_dir.mkdir(parents=True)
        node_executable = node_dir / ("node.exe" if os.name == "nt" else "node")
        node_executable.write_text("", encoding="utf-8")
        monkeypatch.setenv("LOCAL_AGENT_NODE_EXECUTABLE", str(node_executable))
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
            commands=[
                {
                    "id": "cmd_node_missing",
                    "command": "node --check app.js",
                    "status": "failed",
                    "exitCode": 1,
                    "summary": "node : 无法将“node”项识别为 cmdlet、函数、脚本文件或可运行程序的名称。",
                    "startedAt": 100,
                }
            ],
        )

        calls: list[list[str]] = []
        real_run = subprocess.run

        def fake_run(args, **kwargs):
            calls.append(list(args))
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        try:
            result = rt.orchestrator._complete_task(
                session_id=session["id"],
                task=task,
                summary="Generated static frontend files.",
                context={"workspace_root": str(project), "routing": {"scenario": "code_edit"}},
                skip_reflection=True,
            )
        finally:
            monkeypatch.setattr(subprocess, "run", real_run)

        assert result["status"] == "completed"
        evidence = result["structuredResult"]["completionEvidence"]
        syntax = [
            item for item in evidence["acceptance"]
            if item["criterion"] == "Static frontend script syntax: app.js"
        ][0]
        assert syntax["status"] == "supported"
        assert calls
        assert calls[0][0] == str(node_executable)

    def test_node_check_recovery_marks_prior_failed_test_run_resolved(self, tmp_path: Any, monkeypatch) -> None:
        rt = _make_runtime(tmp_path)
        store = rt.store
        project = tmp_path / "project"
        project.mkdir()
        (project / "index.html").write_text(
            '<!doctype html><script src="app.js"></script><main>Ready</main>\n',
            encoding="utf-8",
        )
        (project / "app.js").write_text("console.log('ready');\n", encoding="utf-8")
        node_dir = tmp_path / "node" / "bin"
        node_dir.mkdir(parents=True)
        node_executable = node_dir / ("node.exe" if os.name == "nt" else "node")
        node_executable.write_text("", encoding="utf-8")
        monkeypatch.setenv("LOCAL_AGENT_NODE_EXECUTABLE", str(node_executable))
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
        )
        command_log = store.create_command_log(
            task_id=task["id"],
            command="node --check app.js",
            cwd=".",
            shell="powershell",
        )
        stderr_path = store.write_command_artifact(
            command_log["id"],
            "stderr",
            "node : not recognized as a command",
        )
        store.update_command_log(
            command_log["id"],
            status="failed",
            exit_code=1,
            finished_at=store.now(),
            stdout_path=None,
            stderr_path=stderr_path,
        )

        calls: list[list[str]] = []
        real_run = subprocess.run

        def fake_run(args, **kwargs):
            calls.append(list(args))
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        try:
            result = rt.orchestrator._complete_task(
                session_id=session["id"],
                task=task,
                summary="Generated static frontend files.",
                context={"workspace_root": str(project), "routing": {"scenario": "code_edit"}},
                skip_reflection=True,
            )
        finally:
            monkeypatch.setattr(subprocess, "run", real_run)

        evidence = result["structuredResult"]["completionEvidence"]
        assert result["status"] == "completed"
        assert evidence["counts"]["failedTestsRun"] == 0
        assert evidence["counts"]["resolvedFailedTestsRun"] == 1
        assert evidence["verificationRequirements"]["status"] == "satisfied"
        assert calls

    def test_windows_node_executable_check_counts_as_javascript_verification(self, tmp_path: Any) -> None:
        rt = _make_runtime(tmp_path)
        store = rt.store
        project = tmp_path / "project"
        project.mkdir()
        (project / "app.js").write_text("console.log('ok');\n", encoding="utf-8")
        workspace = store.upsert_workspace(str(project))
        session = store.create_session(workspace_id=workspace["id"], title="completion gate")
        task = store.create_task(
            session_id=session["id"],
            task_type="subagent",
            goal="Implement static frontend",
            plan=[],
            routing={
                "profile": {
                    "ownedScope": ["app.js"],
                    "expectedArtifacts": [{"kind": "file", "path": "app.js"}],
                    "verificationRequirements": [{"kind": "command", "command": "node --check app.js", "family": "javascript"}],
                }
            },
        )
        task = store.update_task(
            task_id=task["id"],
            changed_files=[{"path": "app.js", "summary": "updated frontend script"}],
            commands=[
                {
                    "id": "cmd_node_path",
                    "command": r'& "D:\Program Files\nodejs\node.EXE" --check app.js',
                    "status": "completed",
                    "exitCode": 0,
                    "summary": "syntax ok",
                    "startedAt": 100,
                }
            ],
        )

        result = rt.orchestrator._complete_task(
            session_id=session["id"],
            task=task,
            summary="Frontend implementation finished.",
            context={"workspace_root": str(project), "routing": {"scenario": "code_edit"}},
            skip_reflection=True,
        )

        evidence = result["structuredResult"]["completionEvidence"]
        assert evidence["verificationRequirements"]["matched"] == ["javascript", "javascript:typecheck"]
        assert evidence["verificationRequirements"]["status"] == "satisfied"

    def test_failed_child_task_is_resolved_by_later_successful_sibling_of_same_kind(self, tmp_path: Any) -> None:
        rt = _make_runtime(tmp_path)
        store = rt.store
        project = tmp_path / "project"
        project.mkdir()
        (project / "index.html").write_text("<!doctype html><main>ok</main>\n", encoding="utf-8")
        (project / "app.js").write_text("console.log('ok');\n", encoding="utf-8")
        workspace = store.upsert_workspace(str(project))
        session = store.create_session(workspace_id=workspace["id"], title="completion gate")
        task = store.create_task(
            session_id=session["id"],
            task_type="edit",
            goal="Build frontend and verify it",
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
            summary="Frontend is complete after retry.",
            context={"workspace_root": str(project), "routing": {"scenario": "code_edit"}},
            tool_results=[
                {
                    "name": "child_task",
                    "failed": True,
                    "status": "failed",
                    "summary": "Implement static frontend",
                    "agentType": "worker",
                },
                {
                    "name": "child_task",
                    "failed": False,
                    "status": "completed",
                    "summary": "Implemented the static frontend for the blog system.",
                    "agentType": "worker",
                },
            ],
            skip_reflection=True,
        )

        evidence = result["structuredResult"]["completionEvidence"]
        assert evidence["counts"]["failedToolResults"] == 0
        assert evidence["counts"]["resolvedFailedToolResults"] == 1

    def test_failed_child_task_can_be_resolved_by_root_verification_after_continuation(self, tmp_path: Any) -> None:
        rt = _make_runtime(tmp_path)
        store = rt.store
        project = tmp_path / "project"
        project.mkdir()
        (project / "incident_models.py").write_text("VALUE = 1\n", encoding="utf-8")
        (project / "incident_rules.py").write_text("VALUE = 2\n", encoding="utf-8")
        (project / "README.md").write_text("# Incident Engine\n", encoding="utf-8")
        tests_dir = project / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_incident_engine_core.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
        workspace = store.upsert_workspace(str(project))
        session = store.create_session(workspace_id=workspace["id"], title="completion gate")
        task = store.create_task(
            session_id=session["id"],
            task_type="edit",
            goal="Build incident engine and verify it",
            plan=[],
            routing={"scenario": "code_edit"},
        )
        task = store.update_task(
            task_id=task["id"],
            changed_files=[
                {"path": "incident_models.py", "summary": "implemented incident models"},
                {"path": "incident_rules.py", "summary": "implemented rules"},
                {"path": "tests/test_incident_engine_core.py", "summary": "added pytest coverage"},
            ],
            commands=[
                {
                    "id": "cmd_pytest_passed",
                    "command": r"C:\Python314\python.exe -m pytest -q",
                    "status": "completed",
                    "exitCode": 0,
                    "summary": "3 passed",
                    "startedAt": 300,
                }
            ],
        )

        result = rt.orchestrator._complete_task(
            session_id=session["id"],
            task=task,
            summary="Incident engine is complete after continuation and root verification.",
            context={"workspace_root": str(project), "routing": {"scenario": "code_edit"}},
            tool_results=[
                {
                    "name": "child_task",
                    "failed": True,
                    "status": "failed",
                    "summary": "Completion blocked because verification failed. Fix the failed checks before marking the task completed.",
                    "childTaskId": "task_failed_child",
                    "verificationRequirements": [{"kind": "command", "command": "python -m pytest -q", "family": "python"}],
                    "ownedScope": ["incident_models.py", "incident_rules.py", "tests/", "README.md"],
                    "changedFiles": [
                        {"path": "incident_models.py"},
                        {"path": "incident_rules.py"},
                        {"path": "tests/test_incident_engine_core.py"},
                    ],
                }
            ],
            skip_reflection=True,
        )

        evidence = result["structuredResult"]["completionEvidence"]
        assert result["status"] == "completed"
        assert evidence["evidenceLevel"] == "verified"
        assert evidence["counts"]["failedVerification"] == 0
        assert evidence["counts"]["failedToolResults"] == 0
        assert evidence["counts"]["resolvedFailedToolResults"] == 1

    def test_child_task_with_owned_scope_is_still_write_or_verification_work(self, tmp_path: Any) -> None:
        rt = _make_runtime(tmp_path)
        store = rt.store
        project = tmp_path / "project"
        project.mkdir()
        workspace = store.upsert_workspace(str(project))
        session = store.create_session(workspace_id=workspace["id"], title="completion gate")
        task = store.create_task(
            session_id=session["id"],
            task_type="subagent",
            goal="Build frontend assets",
            plan=[],
            routing={
                "profile": {
                    "ownedScope": ["index.html", "app.js", "styles.css"],
                    "expectedArtifacts": [],
                    "verificationRequirements": [],
                }
            },
        )

        assert rt.orchestrator._is_write_or_verification_task(
            task=task,
            context={"workspace_root": str(project)},
        ) is True

    def test_child_task_with_not_required_verification_contract_can_complete_without_targeted_checks(self, tmp_path: Any) -> None:
        rt = _make_runtime(tmp_path)
        store = rt.store
        project = tmp_path / "project"
        project.mkdir()
        (project / "blog_models.py").write_text("VALUE = 1\n", encoding="utf-8")
        workspace = store.upsert_workspace(str(project))
        session = store.create_session(workspace_id=workspace["id"], title="completion gate")
        task = store.create_task(
            session_id=session["id"],
            task_type="subagent",
            goal="Implement backend module",
            plan=[],
            routing={
                "profile": {
                    "ownedScope": ["blog_models.py"],
                    "expectedArtifacts": [],
                    "verificationRequirements": [],
                }
            },
        )
        task = store.update_task(
            task_id=task["id"],
            changed_files=[{"path": "blog_models.py", "summary": "implemented backend module"}],
        )

        result = rt.orchestrator._complete_task(
            session_id=session["id"],
            task=task,
            summary="Implemented backend module.",
            context={"workspace_root": str(project)},
            skip_reflection=True,
        )

        assert result["status"] == "completed"
        requirements = result["structuredResult"]["completionEvidence"]["verificationRequirements"]
        assert requirements["status"] == "not_required"

    def test_planner_child_with_owned_scope_only_is_not_treated_as_write_work(self, tmp_path: Any) -> None:
        rt = _make_runtime(tmp_path)
        store = rt.store
        project = tmp_path / "project"
        project.mkdir()
        workspace = store.upsert_workspace(str(project))
        session = store.create_session(workspace_id=workspace["id"], title="planner child")
        task = store.create_task(
            session_id=session["id"],
            task_type="subagent",
            goal="Review backend risks",
            plan=[],
            role="planner",
            routing={
                "runtimeRole": "planner",
                "profile": {
                    "ownedScope": [
                        "backend architecture",
                        "data model",
                        "API contract",
                    ],
                    "expectedArtifacts": [],
                    "verificationRequirements": [],
                },
            },
        )

        assert rt.orchestrator._is_write_or_verification_task(
            task=task,
            context={"workspace_root": str(project), "routing": dict(task.get("routing") or {})},
        ) is False

        result = rt.orchestrator._complete_task(
            session_id=session["id"],
            task=task,
            summary="Backend risk review complete.",
            context={"workspace_root": str(project), "routing": dict(task.get("routing") or {})},
            skip_reflection=True,
        )

        assert result["status"] == "completed"

    def test_reviewer_child_with_file_owned_scope_only_is_not_forced_to_produce_artifacts(self, tmp_path: Any) -> None:
        rt = _make_runtime(tmp_path)
        store = rt.store
        project = tmp_path / "project"
        project.mkdir()
        (project / "incident_models.py").write_text("class Incident: ...\n", encoding="utf-8")
        (project / "incident_rules.py").write_text("def build_rules():\n    return []\n", encoding="utf-8")
        workspace = store.upsert_workspace(str(project))
        session = store.create_session(workspace_id=workspace["id"], title="reviewer child")
        task = store.create_task(
            session_id=session["id"],
            task_type="subagent",
            goal="Review backend rule engine risks",
            plan=[],
            role="reviewer",
            routing={
                "runtimeRole": "reviewer",
                "profile": {
                    "ownedScope": ["incident_models.py", "incident_rules.py"],
                    "expectedArtifacts": [],
                    "verificationRequirements": [],
                },
            },
        )

        assert rt.orchestrator._is_write_or_verification_task(
            task=task,
            context={"workspace_root": str(project), "routing": dict(task.get("routing") or {})},
        ) is False

        result = rt.orchestrator._complete_task(
            session_id=session["id"],
            task=task,
            summary="Reviewed backend risks and documented findings.",
            context={"workspace_root": str(project), "routing": dict(task.get("routing") or {})},
            skip_reflection=True,
        )

        assert result["status"] == "completed"
        acceptance = result["structuredResult"]["completionEvidence"]["acceptance"]
        assert not any(item["criterion"] == "Expected artifact exists: incident_models.py" for item in acceptance)
        assert not any(item["criterion"] == "Expected artifact exists: incident_rules.py" for item in acceptance)

    def test_completion_ignores_glob_owned_scope_as_literal_expected_artifact(self, tmp_path: Any) -> None:
        rt = _make_runtime(tmp_path)
        store = rt.store
        project = tmp_path / "project"
        project.mkdir()
        (project / "tests").mkdir()
        (project / "tests" / "test_blog_service.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
        workspace = store.upsert_workspace(str(project))
        session = store.create_session(workspace_id=workspace["id"], title="glob scope child")
        task = store.create_task(
            session_id=session["id"],
            task_type="subagent",
            goal="Write pytest coverage",
            plan=[],
            role="worker",
            routing={
                "runtimeRole": "worker",
                "profile": {
                    "ownedScope": ["tests/**", "test_*.py"],
                    "expectedArtifacts": [],
                    "verificationRequirements": [],
                },
            },
        )
        task = store.update_task(
            task_id=task["id"],
            changed_files=[{"path": "tests/test_blog_service.py", "summary": "added pytest coverage"}],
        )

        result = rt.orchestrator._complete_task(
            session_id=session["id"],
            task=task,
            summary="Added pytest coverage.",
            context={"workspace_root": str(project), "routing": dict(task.get("routing") or {})},
            skip_reflection=True,
        )

        acceptance = result["structuredResult"]["completionEvidence"]["acceptance"]
        assert not any(item["criterion"] == "Expected artifact exists: test_*.py" for item in acceptance)

    def test_worker_child_does_not_inherit_parent_structural_artifacts_from_goal_text(self, tmp_path: Any) -> None:
        rt = _make_runtime(tmp_path)
        store = rt.store
        project = tmp_path / "project"
        project.mkdir()
        (project / "tests").mkdir()
        (project / "tests" / "test_blog_api.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
        (project / "tests" / "test_blog_service.py").write_text("def test_ok_two():\n    assert True\n", encoding="utf-8")
        workspace = store.upsert_workspace(str(project))
        session = store.create_session(workspace_id=workspace["id"], title="worker child acceptance scope")
        task = store.create_task(
            session_id=session["id"],
            task_type="subagent",
            goal=(
                "Parent task mentions index.html, app.js, and styles.css. "
                "Assigned subtask: author pytest coverage for backend and API."
            ),
            plan=[],
            role="worker",
            routing={
                "runtimeRole": "worker",
                "profile": {
                    "ownedScope": ["tests/"],
                    "expectedArtifacts": [],
                    "verificationRequirements": [],
                },
            },
        )
        task = store.update_task(
            task_id=task["id"],
            changed_files=[
                {"path": "tests/test_blog_service.py", "summary": "added service tests"},
                {"path": "tests/test_blog_api.py", "summary": "added api tests"},
            ],
            commands=[
                {
                    "id": "cmd_pytest",
                    "command": '& "C:\\Python314\\python.exe" -m pytest -q',
                    "cwd": "tests",
                    "status": "completed",
                    "exitCode": 0,
                    "summary": "3 passed",
                }
            ],
        )

        result = rt.orchestrator._complete_task(
            session_id=session["id"],
            task=task,
            summary="Added pytest coverage and it passed.",
            context={"workspace_root": str(project), "routing": dict(task.get("routing") or {})},
            skip_reflection=True,
        )

        assert result["status"] == "completed"
        acceptance = result["structuredResult"]["completionEvidence"]["acceptance"]
        assert not any(item["criterion"] == "Expected artifact exists: app.js" for item in acceptance)
        assert not any(item["criterion"] == "Expected artifact exists: styles.css" for item in acceptance)

    def test_completion_does_not_require_deleted_out_of_scope_artifact_from_followup_text(self, tmp_path: Any) -> None:
        rt = _make_runtime(tmp_path)
        store = rt.store
        project = tmp_path / "project"
        project.mkdir()
        (project / "incident_models.py").write_text("VALUE = 1\n", encoding="utf-8")
        (project / "incident_storage.py").write_text("VALUE = 2\n", encoding="utf-8")
        (project / "incident_rules.py").write_text("VALUE = 3\n", encoding="utf-8")
        (project / "incident_reporting.py").write_text("VALUE = 4\n", encoding="utf-8")
        (project / "incident_import_export.py").write_text("VALUE = 5\n", encoding="utf-8")
        (project / "incident_cli.py").write_text("VALUE = 6\n", encoding="utf-8")
        (project / "README.md").write_text(
            "# Incident Rules Engine\n\nSINGLE_MODEL_INCIDENT_RULES_ENGINE\n\nINCIDENT_RULES_MEMORY_SQLITE_PYTEST\n",
            encoding="utf-8",
        )
        tests_dir = project / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_incident_engine_rules.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
        workspace = store.upsert_workspace(str(project))
        session = store.create_session(workspace_id=workspace["id"], title="follow-up deletion")
        task = store.create_task(
            session_id=session["id"],
            task_type="edit",
            goal=(
                "Follow-up fix for the previous incident rules engine task. "
                "The backend implementation and tests passed, but you added an out-of-scope frontend artifact: incident_dashboard.html. "
                "This task is backend-only and non-frontend. If incident_dashboard.html is not required for the requested backend deliverable, delete it. "
                "Do not remove the backend Python modules, tests, or README anchors. "
                "Then rerun python -m pytest -q and python -m py_compile incident_models.py incident_storage.py incident_rules.py incident_reporting.py incident_import_export.py incident_cli.py."
            ),
            plan=[],
            routing={"scenario": "code_edit"},
        )
        task = store.update_task(
            task_id=task["id"],
            commands=[
                {
                    "id": "cmd_remove",
                    "command": 'Remove-Item -LiteralPath "incident_dashboard.html"',
                    "status": "completed",
                    "exitCode": 0,
                    "summary": "Removed out-of-scope frontend artifact",
                },
                {
                    "id": "cmd_pytest",
                    "command": '& "C:\\Python314\\python.exe" -m pytest -q',
                    "status": "completed",
                    "exitCode": 0,
                    "summary": "1 passed",
                },
                {
                    "id": "cmd_py_compile",
                    "command": '& "C:\\Python314\\python.exe" -m py_compile incident_models.py incident_storage.py incident_rules.py incident_reporting.py incident_import_export.py incident_cli.py',
                    "status": "completed",
                    "exitCode": 0,
                    "summary": "Command passed",
                },
            ],
        )

        result = rt.orchestrator._complete_task(
            session_id=session["id"],
            task=task,
            summary="Removed the out-of-scope frontend artifact and reverified the backend deliverable.",
            context={"workspace_root": str(project), "routing": {"scenario": "code_edit"}},
            skip_reflection=True,
        )

        assert result["status"] == "completed"
        acceptance = result["structuredResult"]["completionEvidence"]["acceptance"]
        assert not any(item["criterion"] == "Expected artifact exists: incident_dashboard.html" for item in acceptance)

    def test_completion_reinserts_required_focus_and_memory_anchors(self, tmp_path: Any) -> None:
        rt = _make_runtime(tmp_path)
        store = rt.store
        project = tmp_path / "project"
        project.mkdir()
        workspace = store.upsert_workspace(str(project))
        session = store.create_session(workspace_id=workspace["id"], title="anchor completion")
        task = store.create_task(
            session_id=session["id"],
            task_type="edit",
            goal=(
                "Keep SINGLE_MODEL_INCIDENT_RULES_ENGINE and INCIDENT_RULES_MEMORY_SQLITE_PYTEST "
                "visible in the final summary while finishing the backend task."
            ),
            plan=[],
            routing={"scenario": "code_edit"},
        )
        task = store.update_task(
            task_id=task["id"],
            changed_files=[{"path": "incident_rules.py", "action": "modified"}],
            commands=[
                {
                    "id": "cmd_pytest",
                    "command": 'python -m pytest -q',
                    "status": "completed",
                    "exitCode": 0,
                    "summary": "3 passed",
                }
            ],
        )

        result = rt.orchestrator._complete_task(
            session_id=session["id"],
            task=task,
            summary="Finished the backend implementation and validation.",
            context={"routing": {"scenario": "code_edit"}},
            skip_reflection=True,
        )

        assert result["status"] == "completed"
        assert "SINGLE_MODEL_INCIDENT_RULES_ENGINE" in result["resultSummary"]
        assert "INCIDENT_RULES_MEMORY_SQLITE_PYTEST" in result["resultSummary"]

    def test_package_style_python_layout_counts_as_equivalent_artifact_and_test_shape(self, tmp_path: Any) -> None:
        rt = _make_runtime(tmp_path)
        store = rt.store
        project = tmp_path / "project"
        package_dir = project / "blog"
        tests_dir = project / "tests"
        package_dir.mkdir(parents=True)
        tests_dir.mkdir(parents=True)
        (package_dir / "__init__.py").write_text("", encoding="utf-8")
        (package_dir / "app.py").write_text("def build_app():\n    return 'ok'\n", encoding="utf-8")
        (package_dir / "store.py").write_text("class Store:\n    pass\n", encoding="utf-8")
        (tests_dir / "blog_system_test.py").write_text("def test_blog_system():\n    assert True\n", encoding="utf-8")
        (tests_dir / "test_blog_api.py").write_text("def test_blog_api():\n    assert True\n", encoding="utf-8")
        workspace = store.upsert_workspace(str(project))
        session = store.create_session(workspace_id=workspace["id"], title="package layout")
        task = store.create_task(
            session_id=session["id"],
            task_type="edit",
            goal=(
                "Suggested filenames include blog_models.py, blog_storage.py, blog_service.py, blog_api.py, and blog_server.py, "
                "but close equivalents are acceptable. Create at least 2 pytest files."
            ),
            plan=[],
            routing={"scenario": "code_edit"},
        )
        task = store.update_task(
            task_id=task["id"],
            changed_files=[
                {"path": "blog/app.py", "summary": "package app entry"},
                {"path": "blog/store.py", "summary": "package storage"},
                {"path": "tests/blog_system_test.py", "summary": "package layout test"},
                {"path": "tests/test_blog_api.py", "summary": "api test"},
            ],
            commands=[
                {
                    "id": "cmd_pytest",
                    "command": 'python -m pytest -q',
                    "status": "completed",
                    "exitCode": 0,
                    "summary": "2 passed",
                },
                {
                    "id": "cmd_py_compile",
                    "command": 'python -m py_compile blog/app.py blog/store.py',
                    "status": "completed",
                    "exitCode": 0,
                    "summary": "syntax passed",
                },
            ],
        )

        result = rt.orchestrator._complete_task(
            session_id=session["id"],
            task=task,
            summary="Implemented the package-style backend and its pytest coverage.",
            context={"workspace_root": str(project), "routing": {"scenario": "code_edit"}},
            skip_reflection=True,
        )

        assert result["status"] == "completed"
        acceptance = result["structuredResult"]["completionEvidence"]["acceptance"]
        failed = [item["criterion"] for item in acceptance if item["status"] == "failed"]
        assert "Expected pytest file count >= 2" not in failed

    def test_node_exe_check_command_counts_as_existing_node_check(self, tmp_path: Any) -> None:
        rt = _make_runtime(tmp_path)
        store = rt.store
        project = tmp_path / "project"
        project.mkdir()
        (project / "index.html").write_text('<script src="app.js"></script><main>Blog</main>\n', encoding="utf-8")
        (project / "app.js").write_text("console.log('ok');\n", encoding="utf-8")
        workspace = store.upsert_workspace(str(project))
        session = store.create_session(workspace_id=workspace["id"], title="node check")
        task = store.create_task(
            session_id=session["id"],
            task_type="edit",
            goal="Implement a static frontend with index.html and app.js",
            plan=[],
            routing={"scenario": "code_edit"},
        )
        task = store.update_task(
            task_id=task["id"],
            changed_files=[
                {"path": "index.html", "summary": "frontend entry"},
                {"path": "app.js", "summary": "frontend script"},
            ],
            commands=[
                {
                    "id": "cmd_node_check",
                    "command": '& "C:\\Program Files\\nodejs\\node.exe" --check app.js',
                    "status": "completed",
                    "exitCode": 0,
                    "summary": "syntax ok",
                }
            ],
        )

        result = rt.orchestrator._complete_task(
            session_id=session["id"],
            task=task,
            summary="Implemented the static frontend and verified script syntax.",
            context={"workspace_root": str(project), "routing": {"scenario": "code_edit"}},
            skip_reflection=True,
        )

        assert result["status"] == "completed"
        acceptance = result["structuredResult"]["completionEvidence"]["acceptance"]
        node_record = next(
            item for item in acceptance
            if item["criterion"] == "Static frontend script syntax: app.js"
        )
        assert node_record["status"] == "supported"

    def test_later_root_level_equivalent_pytest_success_resolves_failed_child_verification(self, tmp_path: Any) -> None:
        rt = _make_runtime(tmp_path)
        store = rt.store
        project = tmp_path / "project"
        project.mkdir()
        (project / "blog_models.py").write_text("VALUE = 1\n", encoding="utf-8")
        (project / "tests").mkdir()
        (project / "tests" / "test_blog_service.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
        workspace = store.upsert_workspace(str(project))
        session = store.create_session(workspace_id=workspace["id"], title="child failure recovery")
        task = store.create_task(
            session_id=session["id"],
            task_type="edit",
            goal="Finish the blog backend and verify it end to end.",
            plan=[],
            routing={"scenario": "code_edit"},
        )
        task = store.update_task(
            task_id=task["id"],
            changed_files=[
                {"path": "blog_models.py", "summary": "backend model"},
                {"path": "tests/test_blog_service.py", "summary": "service coverage"},
            ],
            commands=[
                {
                    "id": "cmd_root_pytest",
                    "command": '"C:\\Python314\\python.exe" -m pytest -q',
                    "status": "completed",
                    "exitCode": 0,
                    "summary": "1 passed",
                },
            ],
        )

        result = rt.orchestrator._complete_task(
            session_id=session["id"],
            task=task,
            summary="Completed the backend and reran pytest successfully.",
            context={"workspace_root": str(project), "routing": {"scenario": "code_edit"}},
            tool_results=[
                {
                    "name": "child_task",
                    "result": {
                        "status": "failed",
                        "summary": "Add pytest coverage for backend and API paths",
                        "changedFiles": [{"path": "tests/test_blog_service.py"}],
                        "verification": [
                            {
                                "command": '"C:\\Python314\\python.exe" -m pytest -q',
                                "status": "failed",
                                "summary": "test failure before fix",
                            }
                        ],
                        "testsRun": [
                            {
                                "command": '"C:\\Python314\\python.exe" -m pytest -q',
                                "status": "failed",
                                "summary": "test failure before fix",
                            }
                        ],
                    },
                }
            ],
            skip_reflection=True,
        )

        evidence = result["structuredResult"]["completionEvidence"]
        assert result["status"] == "completed"
        assert evidence["counts"]["failedVerification"] == 0
        assert evidence["counts"]["failedTestsRun"] == 0
        assert evidence["counts"]["passedTestsRun"] >= 1

    def test_timestamped_root_pytest_success_resolves_untimestamped_child_failures(self, tmp_path: Any) -> None:
        rt = _make_runtime(tmp_path)
        store = rt.store
        project = tmp_path / "project"
        project.mkdir()
        (project / "blog_service.py").write_text("def ok():\n    return True\n", encoding="utf-8")
        (project / "tests").mkdir()
        (project / "tests" / "test_blog_service.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
        workspace = store.upsert_workspace(str(project))
        session = store.create_session(workspace_id=workspace["id"], title="timestamp ordering recovery")
        task = store.create_task(
            session_id=session["id"],
            task_type="edit",
            goal="Repair blog workflow tests and rerun the root verification successfully.",
            plan=[],
            routing={"scenario": "code_edit"},
        )
        task = store.update_task(
            task_id=task["id"],
            changed_files=[
                {"path": "blog_service.py", "summary": "service fix"},
                {"path": "tests/test_blog_service.py", "summary": "test coverage"},
            ],
            commands=[
                {
                    "id": "cmd_root_pytest",
                    "command": "python -m pytest -q",
                    "status": "completed",
                    "exitCode": 0,
                    "summary": "1 passed",
                    "startedAt": 200.0,
                    "finishedAt": 210.0,
                },
            ],
        )

        result = rt.orchestrator._complete_task(
            session_id=session["id"],
            task=task,
            summary="Fixed the workflow and reran pytest successfully.",
            context={"workspace_root": str(project), "routing": {"scenario": "code_edit"}},
            tool_results=[
                {
                    "name": "child_task",
                    "result": {
                        "status": "failed",
                        "summary": "Initial child pytest run failed before the root retry.",
                        "changedFiles": [{"path": "tests/test_blog_service.py"}],
                        "testsRun": [
                            {
                                "command": '"C:\\Python314\\python.exe" -m pytest -q',
                                "status": "failed",
                                "summary": "child root-level pytest failed before fix",
                            },
                            {
                                "command": '"C:\\Python314\\python.exe" -m pytest tests/test_blog_service.py -q',
                                "status": "failed",
                                "summary": "child targeted pytest failed before fix",
                            },
                        ],
                    },
                }
            ],
            skip_reflection=True,
        )

        evidence = result["structuredResult"]["completionEvidence"]
        assert result["status"] == "completed"
        assert evidence["counts"]["failedVerification"] == 0
        assert evidence["counts"]["failedTestsRun"] == 0
        assert evidence["counts"]["passedTestsRun"] >= 1

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
