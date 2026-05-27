"""Tests for Task state machine validation.

Covers:
  - Legal transitions: running → completed/failed/cancelled/paused/waiting_approval
  - Legal transitions: paused → running/cancelled
  - Legal transitions: waiting_approval → running/cancelled
  - Illegal transitions: terminal → anything
  - _complete_task / _fail_task / cancel_task / pause_task / resume_task validation
  - RPC-level rejection of illegal transitions
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from local_agent_runtime.event_bus import EventBus
from local_agent_runtime.orchestrator.service import Orchestrator
from local_agent_runtime.rpc.server import JsonRpcServer
from local_agent_runtime.store.sqlite_store import SQLiteStore
from local_agent_runtime.tools.registry import ToolRegistry


# ── helpers ────────────────────────────────────────────────────────────────


def _make_runtime(tmp_path: Any) -> SimpleNamespace:
    event_bus = EventBus()
    store = SQLiteStore(str(tmp_path / "runtime.sqlite3"))
    tool_registry = ToolRegistry()

    class DummyProvider:
        def generate(self, prompt: str, context: dict[str, Any]) -> dict[str, Any]:
            return {"text": "done", "status": "done"}

    orchestrator = Orchestrator(
        store=store, event_bus=event_bus,
        tool_registry=tool_registry, provider=DummyProvider(),
    )
    server = JsonRpcServer(orchestrator=orchestrator, store=store, event_bus=event_bus)
    events: list[dict[str, Any]] = []
    event_bus.subscribe(lambda event: events.append(event_bus.as_payload(event)))
    return SimpleNamespace(server=server, store=store, events=events)


def _rpc(runtime: SimpleNamespace, method: str, params: dict[str, Any]) -> dict[str, Any]:
    request_id = f"req_{method}"
    envelope = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
    return runtime.server.handle_line(json.dumps(envelope, ensure_ascii=False))


def _open_session(runtime: SimpleNamespace, tmp_path: Any) -> dict[str, Any]:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(exist_ok=True)
    ws = _rpc(runtime, "workspace.open", {"path": str(workspace_root)})["result"]["workspace"]
    return _rpc(runtime, "session.create", {
        "workspaceId": ws["id"], "title": "Test Session",
    })["result"]["session"]


def _create_task(store: SQLiteStore, session_id: str, status: str = "running") -> dict[str, Any]:
    task = store.create_task(session_id=session_id, task_type="react", goal="test task", plan=[])
    if status != "queued":
        store.update_task_status(task_id=task["id"], status=status)
        task = store.get_task({"taskId": task["id"]})["task"]
    return task


# ── unit: _validate_task_transition ────────────────────────────────────────


class TestTaskTransitionValidation:
    """Unit tests for the _validate_task_transition method."""

    @pytest.fixture()
    def orch(self, tmp_path: Any) -> Orchestrator:
        event_bus = EventBus()
        store = SQLiteStore(str(tmp_path / "test.sqlite3"))
        tool_registry = ToolRegistry()

        class DummyProvider:
            def generate(self, prompt: str, context: dict[str, Any]) -> dict[str, Any]:
                return {"text": "done", "status": "done"}

        return Orchestrator(
            store=store, event_bus=event_bus,
            tool_registry=tool_registry, provider=DummyProvider(),
        )

    # Legal transitions from running
    def test_running_to_completed(self, orch: Orchestrator) -> None:
        orch._validate_task_transition("running", "completed", "t1")

    def test_running_to_failed(self, orch: Orchestrator) -> None:
        orch._validate_task_transition("running", "failed", "t1")

    def test_running_to_cancelled(self, orch: Orchestrator) -> None:
        orch._validate_task_transition("running", "cancelled", "t1")

    def test_running_to_paused(self, orch: Orchestrator) -> None:
        orch._validate_task_transition("running", "paused", "t1")

    def test_running_to_waiting_approval(self, orch: Orchestrator) -> None:
        orch._validate_task_transition("running", "waiting_approval", "t1")

    # Legal transitions from paused
    def test_paused_to_running(self, orch: Orchestrator) -> None:
        orch._validate_task_transition("paused", "running", "t1")

    def test_paused_to_cancelled(self, orch: Orchestrator) -> None:
        orch._validate_task_transition("paused", "cancelled", "t1")

    # Legal transitions from waiting_approval
    def test_waiting_approval_to_running(self, orch: Orchestrator) -> None:
        orch._validate_task_transition("waiting_approval", "running", "t1")

    def test_waiting_approval_to_cancelled(self, orch: Orchestrator) -> None:
        orch._validate_task_transition("waiting_approval", "cancelled", "t1")

    def test_waiting_approval_to_failed(self, orch: Orchestrator) -> None:
        orch._validate_task_transition("waiting_approval", "failed", "t1")

    def test_waiting_approval_to_paused(self, orch: Orchestrator) -> None:
        orch._validate_task_transition("waiting_approval", "paused", "t1")

    # Legal from queued
    def test_queued_to_running(self, orch: Orchestrator) -> None:
        orch._validate_task_transition("queued", "running", "t1")

    def test_queued_to_cancelled(self, orch: Orchestrator) -> None:
        orch._validate_task_transition("queued", "cancelled", "t1")

    # Illegal transitions from terminal states
    def test_completed_to_running_raises(self, orch: Orchestrator) -> None:
        with pytest.raises(ValueError, match="cannot transition"):
            orch._validate_task_transition("completed", "running", "t1")

    def test_failed_to_running_raises(self, orch: Orchestrator) -> None:
        with pytest.raises(ValueError, match="cannot transition"):
            orch._validate_task_transition("failed", "running", "t1")

    def test_completed_to_failed_raises(self, orch: Orchestrator) -> None:
        with pytest.raises(ValueError, match="cannot transition"):
            orch._validate_task_transition("completed", "failed", "t1")

    def test_cancelled_to_running_raises(self, orch: Orchestrator) -> None:
        with pytest.raises(ValueError, match="cannot transition"):
            orch._validate_task_transition("cancelled", "running", "t1")


# ── integration: cancel_task is idempotent for terminal states ─────────────


class TestCancelTaskStateMachine:
    """cancel_task should no-op for terminal states and cancel active work."""

    def test_cancel_completed_task_returns_current_task(self, tmp_path: Any) -> None:
        runtime = _make_runtime(tmp_path)
        session = _open_session(runtime, tmp_path)
        task = _create_task(runtime.store, session["id"], status="completed")

        resp = _rpc(runtime, "task.cancel", {"taskId": task["id"]})
        assert "result" in resp
        assert resp["result"]["task"]["status"] == "completed"
        assert "task.cancelled" not in [event["type"] for event in runtime.events]

    def test_cancel_failed_task_returns_current_task(self, tmp_path: Any) -> None:
        runtime = _make_runtime(tmp_path)
        session = _open_session(runtime, tmp_path)
        task = _create_task(runtime.store, session["id"], status="failed")

        resp = _rpc(runtime, "task.cancel", {"taskId": task["id"]})
        assert "result" in resp
        assert resp["result"]["task"]["status"] == "failed"
        assert "task.cancelled" not in [event["type"] for event in runtime.events]

    def test_cancel_cancelled_task_returns_current_task(self, tmp_path: Any) -> None:
        runtime = _make_runtime(tmp_path)
        session = _open_session(runtime, tmp_path)
        task = _create_task(runtime.store, session["id"], status="cancelled")

        resp = _rpc(runtime, "task.cancel", {"taskId": task["id"]})
        assert "result" in resp
        assert resp["result"]["task"]["status"] == "cancelled"
        assert "task.cancelled" not in [event["type"] for event in runtime.events]

    def test_cancel_running_task_allowed(self, tmp_path: Any) -> None:
        runtime = _make_runtime(tmp_path)
        session = _open_session(runtime, tmp_path)
        task = _create_task(runtime.store, session["id"], status="running")

        resp = _rpc(runtime, "task.cancel", {"taskId": task["id"]})
        assert "result" in resp
        assert resp["result"]["task"]["status"] == "cancelled"


# ── integration: pause_task validation ─────────────────────────────────────


class TestPauseTaskStateMachine:
    """pause_task should reject illegal states and allow legal ones."""

    def test_pause_running_task_allowed(self, tmp_path: Any) -> None:
        runtime = _make_runtime(tmp_path)
        session = _open_session(runtime, tmp_path)
        task = _create_task(runtime.store, session["id"], status="running")

        resp = _rpc(runtime, "task.pause", {"taskId": task["id"]})
        assert "result" in resp
        assert resp["result"]["task"]["status"] == "paused"

    def test_pause_completed_task_rejected(self, tmp_path: Any) -> None:
        runtime = _make_runtime(tmp_path)
        session = _open_session(runtime, tmp_path)
        task = _create_task(runtime.store, session["id"], status="completed")

        resp = _rpc(runtime, "task.pause", {"taskId": task["id"]})
        assert "error" in resp

    def test_pause_failed_task_rejected(self, tmp_path: Any) -> None:
        runtime = _make_runtime(tmp_path)
        session = _open_session(runtime, tmp_path)
        task = _create_task(runtime.store, session["id"], status="failed")

        resp = _rpc(runtime, "task.pause", {"taskId": task["id"]})
        assert "error" in resp


# ── integration: resume_task validation ────────────────────────────────────


class TestResumeTaskStateMachine:
    """resume_task should only work from paused state."""

    def test_resume_non_paused_task_rejected(self, tmp_path: Any) -> None:
        runtime = _make_runtime(tmp_path)
        session = _open_session(runtime, tmp_path)
        task = _create_task(runtime.store, session["id"], status="running")

        resp = _rpc(runtime, "task.resume", {"taskId": task["id"]})
        assert "error" in resp

    def test_resume_completed_task_rejected(self, tmp_path: Any) -> None:
        runtime = _make_runtime(tmp_path)
        session = _open_session(runtime, tmp_path)
        task = _create_task(runtime.store, session["id"], status="completed")

        resp = _rpc(runtime, "task.resume", {"taskId": task["id"]})
        assert "error" in resp


# ── integration: _complete_task / _fail_task on already terminal ────────────


class TestCompleteFailStateMachine:
    """_complete_task / _fail_task on terminal tasks should return silently."""

    def test_complete_already_completed_returns_silently(self, tmp_path: Any) -> None:
        runtime = _make_runtime(tmp_path)
        session = _open_session(runtime, tmp_path)
        task = _create_task(runtime.store, session["id"], status="completed")

        result = runtime.server._orchestrator._complete_task(
            session_id=session["id"],
            task=task,
            summary="should not crash",
        )
        assert result["id"] == task["id"]
        assert result["status"] == "completed"

    def test_fail_already_failed_returns_silently(self, tmp_path: Any) -> None:
        runtime = _make_runtime(tmp_path)
        session = _open_session(runtime, tmp_path)
        task = _create_task(runtime.store, session["id"], status="failed")

        result = runtime.server._orchestrator._fail_task(
            session_id=session["id"],
            task=task,
            summary="should not crash",
            error_code="TEST",
        )
        assert result["id"] == task["id"]
        assert result["status"] == "failed"

    def test_fail_completed_task_returns_silently(self, tmp_path: Any) -> None:
        runtime = _make_runtime(tmp_path)
        session = _open_session(runtime, tmp_path)
        task = _create_task(runtime.store, session["id"], status="completed")

        result = runtime.server._orchestrator._fail_task(
            session_id=session["id"],
            task=task,
            summary="should not crash",
            error_code="TEST",
        )
        assert result["id"] == task["id"]
        # status stays completed — _fail_task short-circuits on terminal
        assert result["status"] == "completed"
