"""Model-first result and approval boundary contracts.

These tests intentionally avoid the removed router/advisor/planner stack. The
runtime completes from provider/tool evidence without post-hoc completion
advisor gates. Legacy completion review approvals are tolerated only as
internal historical data.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

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
    *,
    enable_hooks: bool = False,
    enable_run_command: bool = False,
) -> SimpleNamespace:
    event_bus = EventBus()
    store = SQLiteStore(str(tmp_path / "runtime.sqlite3"))
    config = store.get_config({})["config"]
    guard = PolicyGuard(approval_mode=config["policy"]["approvalMode"])
    tools: dict[str, Any] = {
        "read_file": build_read_file_tool(guard, store)["handler"],
    }
    if enable_run_command:
        tools["run_command"] = build_run_command_tool(
            guard,
            store,
            permission_engine=PermissionEngine(config=config, store=store),
        )["handler"]

    class DummyProvider:
        def generate(self, prompt: str, context: dict[str, Any]) -> dict[str, Any]:
            return {"text": "ok", "status": "done"}

    orchestrator = Orchestrator(
        store=store,
        event_bus=event_bus,
        tool_registry=ToolRegistry(tools=tools),
        provider=DummyProvider(),
        hook_service=HookService(store, event_bus) if enable_hooks else None,
    )
    return SimpleNamespace(orchestrator=orchestrator, store=store, event_bus=event_bus)


def _make_session_task(
    rt: SimpleNamespace,
    tmp_path: Any,
    *,
    goal: str = "modify the implementation",
    task_type: str = "edit",
    routing: dict[str, Any] | None = None,
    status: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)
    workspace = rt.store.upsert_workspace(str(project))
    session = rt.store.create_session(workspace_id=workspace["id"], title="completion contracts")
    task = rt.store.create_task(
        session_id=session["id"],
        task_type=task_type,
        goal=goal,
        plan=[],
        routing=routing or {},
        **({"status": status} if status else {}),
    )
    return session, task


class TestStructuredResultStore:
    def test_update_task_structured_result(self, tmp_path: Any) -> None:
        store = SQLiteStore(str(tmp_path / "test.sqlite3"))
        task = store.create_task(session_id="s1", task_type="main", goal="test", plan=[])
        structured = {
            "summary": "All tests passed",
            "status": "success",
            "changedFiles": [{"path": "src/main.py", "action": "modified"}],
            "testsRun": [{"name": "test_main", "status": "passed"}],
            "risks": [{"type": "compatibility", "description": "old API", "severity": "low"}],
            "keyFindings": ["Refactored auth module"],
        }

        updated = store.update_task(task_id=task["id"], structured_result=structured)

        assert updated["structuredResult"] == structured

    def test_new_tasks_have_no_structured_result(self, tmp_path: Any) -> None:
        store = SQLiteStore(str(tmp_path / "test.sqlite3"))
        task = store.create_task(session_id="s1", task_type="main", goal="test", plan=[])

        assert task.get("structuredResult") is None

    def test_migration_adds_structured_result_column(self, tmp_path: Any) -> None:
        store = SQLiteStore(str(tmp_path / "test.sqlite3"))
        columns = {
            row["name"]
            for row in store._conn.execute("PRAGMA table_info(tasks)").fetchall()
        }

        assert "structured_result_json" in columns


class TestModelFirstCompletionContracts:
    def test_summary_only_completion_does_not_create_visible_review_by_default(self, tmp_path: Any) -> None:
        rt = _make_runtime(tmp_path)
        captured_events: list[Any] = []
        rt.event_bus.subscribe(captured_events.append)
        session, task = _make_session_task(rt, tmp_path)

        result = rt.orchestrator._complete_task(
            session_id=session["id"],
            task=task,
            summary="I changed the implementation.",
            context={},
            skip_reflection=True,
        )

        assert result["status"] == "completed"
        assert result["structuredResult"]["completionEvidence"]["evidenceLevel"] == "summary_only"
        assert "completionGate" not in result["structuredResult"]
        approvals = rt.store._conn.execute(
            "SELECT * FROM approvals WHERE task_id = ? AND kind = ?",
            (task["id"], "completion_review"),
        ).fetchall()
        assert approvals == []
        assert not [
            event for event in captured_events
            if event.type == "task.waiting_approval" and event.payload.get("kind") == "completion_review"
        ]

    def test_workspace_evidence_contract_is_diagnostic_not_a_gate(self, tmp_path: Any) -> None:
        rt = _make_runtime(tmp_path)
        captured_events: list[Any] = []
        rt.event_bus.subscribe(captured_events.append)
        contract = {
            "profile": {
                "workspaceEvidenceRequired": {
                    "required": True,
                    "source": "task_contract",
                    "requiredTools": ["read_file", "search_files"],
                },
            },
        }
        session, task = _make_session_task(
            rt,
            tmp_path,
            goal="summarize current project progress",
            routing=contract,
        )

        result = rt.orchestrator._complete_task(
            session_id=session["id"],
            task=task,
            summary="Project progress summarized.",
            context={"routing": contract},
            skip_reflection=True,
        )

        evidence = result["structuredResult"]["completionEvidence"]["workspaceEvidence"]
        assert result["status"] == "completed"
        assert "completionGate" not in result["structuredResult"]
        assert evidence["required"] is True
        assert evidence["status"] == "missing"
        assert not [event for event in captured_events if event.type == "task.runtime_work_waiting"]
        assert not [event for event in captured_events if event.type == "agent.decision.completion"]

    def test_explicit_workspace_evidence_contract_accepts_read_only_tool_result(self, tmp_path: Any) -> None:
        rt = _make_runtime(tmp_path)
        contract = {
            "profile": {
                "workspaceEvidenceRequired": {
                    "required": True,
                    "source": "task_contract",
                    "requiredTools": ["read_file", "search_files"],
                },
            },
        }
        session, task = _make_session_task(
            rt,
            tmp_path,
            goal="summarize current project progress",
            routing=contract,
        )

        result = rt.orchestrator._complete_task(
            session_id=session["id"],
            task=task,
            summary="Project progress summarized from README.",
            context={"routing": contract},
            tool_results=[
                {
                    "name": "read_file",
                    "result": {
                        "status": "completed",
                        "path": "README.md",
                        "summary": "read README",
                    },
                },
            ],
            skip_reflection=True,
        )

        evidence = result["structuredResult"]["completionEvidence"]["workspaceEvidence"]
        assert result["status"] == "completed"
        assert evidence["status"] == "satisfied"
        assert evidence["evidence"][0]["name"] == "read_file"

    def test_failed_verification_is_evidence_not_a_completion_gate(self, tmp_path: Any) -> None:
        rt = _make_runtime(tmp_path)
        captured_events: list[Any] = []
        rt.event_bus.subscribe(captured_events.append)
        session, task = _make_session_task(rt, tmp_path)
        task = rt.store.update_task(
            task_id=task["id"],
            changed_files=[{"path": "src/feature.py", "action": "modified"}],
            verification=[{"name": "pytest", "status": "failed", "summary": "1 failed"}],
        )

        result = rt.orchestrator._complete_task(
            session_id=session["id"],
            task=task,
            summary="Implementation finished, but tests failed.",
            context={},
            skip_reflection=True,
        )

        assert result["status"] == "completed"
        assert result["structuredResult"]["completionEvidence"]["evidenceLevel"] == "failed_verification"
        assert "completionGate" not in result["structuredResult"]
        assert not [event for event in captured_events if event.type == "task.runtime_work_waiting"]
        assert not [event for event in captured_events if event.type == "agent.decision.completion"]

    def test_passed_command_verification_completes_with_verified_evidence(self, tmp_path: Any) -> None:
        rt = _make_runtime(tmp_path)
        session, task = _make_session_task(rt, tmp_path)
        command_log = rt.store.create_command_log(
            task_id=task["id"],
            command="python -m py_compile game.py",
            cwd=".",
            shell="powershell",
        )
        rt.store.update_command_log(command_log["id"], status="completed", exit_code=0)

        result = rt.orchestrator._complete_task(
            session_id=session["id"],
            task=task,
            summary="Created game.py and py_compile passed.",
            context={},
            tool_results=[
                {
                    "name": "write_file",
                    "result": {"status": "written", "path": "game.py", "bytesWritten": 100},
                },
                {
                    "name": "run_command",
                    "result": {"status": "completed", "exitCode": 0},
                },
            ],
            skip_reflection=True,
        )

        evidence = result["structuredResult"]["completionEvidence"]
        assert result["status"] == "completed"
        assert evidence["evidenceLevel"] == "verified"
        assert evidence["counts"]["passedVerification"] >= 1

    def test_legacy_completion_review_approval_is_internal_and_does_not_resume_task(self, tmp_path: Any) -> None:
        rt = _make_runtime(tmp_path)
        captured_events: list[Any] = []
        rt.event_bus.subscribe(captured_events.append)
        _session, task = _make_session_task(rt, tmp_path, status="waiting_approval")
        approval = rt.store.create_approval(
            task["id"],
            "completion_review",
            {
                "summary": "Legacy review should not re-enter the agent loop.",
                "completionEvidence": {"evidenceLevel": "summary_only"},
            },
        )

        result = rt.orchestrator.submit_approval(
            {"approvalId": approval["id"], "decision": "approved"},
        )

        refreshed = rt.store.get_task({"taskId": task["id"]})["task"]
        assert result["ignored"] is True
        assert refreshed["status"] == "waiting_approval"
        resolved_events = [
            event for event in captured_events
            if event.type == "approval.resolved" and event.payload.get("kind") == "completion_review"
        ]
        assert resolved_events
        assert resolved_events[0].payload["internal"] is True
        assert resolved_events[0].payload["ignored"] is True

    def test_resubmitting_resolved_approval_is_ignored_without_duplicate_events(self, tmp_path: Any) -> None:
        rt = _make_runtime(tmp_path)
        captured_events: list[Any] = []
        rt.event_bus.subscribe(captured_events.append)
        _session, task = _make_session_task(rt, tmp_path, status="running")
        approval = rt.store.create_approval(
            task["id"],
            "write_file",
            {"path": "index.html", "summary": "write a page"},
        )

        first = rt.orchestrator.submit_approval({"approvalId": approval["id"], "decision": "approved"})
        second = rt.orchestrator.submit_approval({"approvalId": approval["id"], "decision": "approved"})

        assert first["approval"]["decision"] == "approved"
        assert second["ignored"] is True
        assert len([
            event for event in captured_events
            if event.type == "approval.resolved" and event.payload.get("approvalId") == approval["id"]
        ]) == 1

    def test_legacy_tool_approval_without_pending_react_state_does_not_synthesize_final(self, tmp_path: Any) -> None:
        rt = _make_runtime(tmp_path)
        captured_events: list[Any] = []
        rt.event_bus.subscribe(captured_events.append)
        _session, task = _make_session_task(rt, tmp_path, status="waiting_approval")
        approval = rt.store.create_approval(
            task["id"],
            "run_command",
            {"command": "echo ok", "cwd": "."},
        )

        result = rt.orchestrator.submit_approval({"approvalId": approval["id"], "decision": "approved"})

        refreshed = rt.store.get_task({"taskId": task["id"]})["task"]
        assert result["ignored"] is True
        assert refreshed["status"] == "waiting_approval"
        assert not [event for event in captured_events if event.type in {"command.started", "message.completed"}]
        resolved_events = [
            event for event in captured_events
            if event.type == "approval.resolved" and event.payload.get("approvalId") == approval["id"]
        ]
        assert resolved_events
        assert resolved_events[-1].visibility == "trace"
        assert resolved_events[-1].payload["ignored"] is True
