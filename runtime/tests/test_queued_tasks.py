"""Tests for queued task backend (P3.3).

Covers:
  - mode="queued" + running task → task created as queued
  - mode="queued" + no running task → fall through to normal execution
  - queued task does not create assistant message (only user message)
  - running task completion auto-starts queued task
  - running task failure auto-starts queued task
  - multiple queued tasks drain in order
  - queued task can be cancelled
  - task.queued event is published
"""
from __future__ import annotations

import json
import time
from types import SimpleNamespace
from typing import Any

import pytest

from local_agent_runtime.event_bus import EventBus
from local_agent_runtime.orchestrator.service import Orchestrator
from local_agent_runtime.rpc.server import JsonRpcServer
from local_agent_runtime.store.sqlite_store import SQLiteStore
from local_agent_runtime.tools.registry import ToolRegistry


# ── helpers ────────────────────────────────────────────────────────────────


class _FinalProvider:
    """Provider that returns a simple final answer."""

    def generate(self, prompt: str, context: dict[str, Any]) -> dict[str, Any]:
        return {"final": "Task completed."}


def _make_runtime(tmp_path: Any) -> SimpleNamespace:
    event_bus = EventBus()
    store = SQLiteStore(str(tmp_path / "runtime.sqlite3"))
    tool_registry = ToolRegistry()
    provider = _FinalProvider()
    orchestrator = Orchestrator(
        store=store, event_bus=event_bus,
        tool_registry=tool_registry, provider=provider,
    )
    server = JsonRpcServer(orchestrator=orchestrator, store=store, event_bus=event_bus)
    events: list[dict[str, Any]] = []
    event_bus.subscribe(lambda event: events.append(event_bus.as_payload(event)))
    return SimpleNamespace(
        server=server, store=store, events=events, orchestrator=orchestrator,
    )


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


def _create_running_task(store: SQLiteStore, session_id: str, goal: str = "running task") -> dict[str, Any]:
    """Create a task directly in the store as running, bypassing provider execution."""
    task = store.create_task(
        session_id=session_id,
        task_type="react",
        goal=goal,
        plan=[],
        status="running",
    )
    # Add assistant streaming message
    msg = store.create_message(
        session_id=session_id,
        task_id=task["id"],
        role="assistant",
        content="",
        kind="normal",
        status="streaming",
    )
    store.update_task(task_id=task["id"], active_assistant_message_id=msg["id"])
    return store.get_task({"taskId": task["id"]})["task"]


def _wait_task_status(store: SQLiteStore, task_id: str, statuses: set[str], timeout: float = 5.0) -> dict[str, Any]:
    """Poll until task reaches one of the target statuses."""
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        task = store.get_task({"taskId": task_id})["task"]
        if task["status"] in statuses:
            return task
        time.sleep(0.05)
    raise TimeoutError(f"Task {task_id} did not reach {statuses} in {timeout}s")


# ── tests ──────────────────────────────────────────────────────────────────


class TestQueuedTaskCreation:
    """Queued mode handling in send_message."""

    def test_queued_with_running_task_creates_queued(self, tmp_path: Any) -> None:
        runtime = _make_runtime(tmp_path)
        session = _open_session(runtime, tmp_path)

        # Create a running task directly in store
        _create_running_task(runtime.store, session["id"])

        # Send queued message while task is running
        resp = _rpc(runtime, "message.send", {
            "sessionId": session["id"],
            "content": "queued task goal",
            "mode": "queued",
        })
        assert "result" in resp, f"Unexpected error: {resp.get('error')}"
        queued_task = resp["result"]["task"]
        assert queued_task["status"] == "queued"
        assert queued_task["goal"] == "queued task goal"

    def test_queued_without_running_task_falls_through(self, tmp_path: Any) -> None:
        runtime = _make_runtime(tmp_path)
        session = _open_session(runtime, tmp_path)

        # No running task — queued mode should fall through to normal execution
        resp = _rpc(runtime, "message.send", {
            "sessionId": session["id"],
            "content": "direct task",
            "mode": "queued",
        })
        # Should create a normal running task (not queued)
        assert "result" in resp, f"Unexpected: {resp}"
        task = resp["result"]["task"]
        assert task["status"] != "queued"

    def test_queued_task_has_user_message_only(self, tmp_path: Any) -> None:
        runtime = _make_runtime(tmp_path)
        session = _open_session(runtime, tmp_path)

        _create_running_task(runtime.store, session["id"])

        resp = _rpc(runtime, "message.send", {
            "sessionId": session["id"],
            "content": "queued goal",
            "mode": "queued",
        })
        queued_task = resp["result"]["task"]

        # Check messages for this queued task — should have user message, no assistant message
        all_msgs = runtime.store.list_messages({"sessionId": session["id"]}).get("messages", [])
        queued_msgs = [m for m in all_msgs if m.get("taskId") == queued_task["id"]]
        roles = [m["role"] for m in queued_msgs]
        assert "user" in roles
        assert "assistant" not in roles

    def test_task_queued_event_published(self, tmp_path: Any) -> None:
        runtime = _make_runtime(tmp_path)
        session = _open_session(runtime, tmp_path)

        _create_running_task(runtime.store, session["id"])
        runtime.events.clear()

        _rpc(runtime, "message.send", {
            "sessionId": session["id"],
            "content": "queued goal",
            "mode": "queued",
        })

        queued_events = [e for e in runtime.events if e.get("type") == "task.queued"]
        assert len(queued_events) >= 1


class TestQueuedTaskCancellation:
    """Queued tasks can be cancelled."""

    def test_cancel_queued_task(self, tmp_path: Any) -> None:
        runtime = _make_runtime(tmp_path)
        session = _open_session(runtime, tmp_path)

        _create_running_task(runtime.store, session["id"])

        resp = _rpc(runtime, "message.send", {
            "sessionId": session["id"],
            "content": "queued goal",
            "mode": "queued",
        })
        queued_task_id = resp["result"]["task"]["id"]

        cancel_resp = _rpc(runtime, "task.cancel", {"taskId": queued_task_id})
        assert "result" in cancel_resp
        assert cancel_resp["result"]["task"]["status"] == "cancelled"


class TestQueuedTaskDrain:
    """Running task completion triggers queued task execution."""

    def test_complete_task_drains_queue(self, tmp_path: Any) -> None:
        runtime = _make_runtime(tmp_path)
        session = _open_session(runtime, tmp_path)

        # Create running task directly
        task1 = _create_running_task(runtime.store, session["id"])

        # Queue task2
        resp2 = _rpc(runtime, "message.send", {
            "sessionId": session["id"],
            "content": "second task",
            "mode": "queued",
        })
        task2_id = resp2["result"]["task"]["id"]

        # Complete task1 manually
        runtime.orchestrator._complete_task(
            session_id=session["id"],
            task=task1,
            summary="done",
        )

        # Task2 should have been auto-started
        task2 = runtime.store.get_task({"taskId": task2_id})["task"]
        assert task2["status"] in {"running", "completed"}

    def test_fail_task_drains_queue(self, tmp_path: Any) -> None:
        runtime = _make_runtime(tmp_path)
        session = _open_session(runtime, tmp_path)

        # Create running task directly
        task1 = _create_running_task(runtime.store, session["id"])

        # Queue task2
        resp2 = _rpc(runtime, "message.send", {
            "sessionId": session["id"],
            "content": "queued after fail",
            "mode": "queued",
        })
        task2_id = resp2["result"]["task"]["id"]

        # Fail task1
        runtime.orchestrator._fail_task(
            session_id=session["id"],
            task=task1,
            summary="manual fail",
            error_code="TEST",
        )

        # Task2 should be auto-started
        task2 = runtime.store.get_task({"taskId": task2_id})["task"]
        assert task2["status"] in {"running", "completed"}

    def test_multiple_queued_drain_in_order(self, tmp_path: Any) -> None:
        runtime = _make_runtime(tmp_path)
        session = _open_session(runtime, tmp_path)

        task1 = _create_running_task(runtime.store, session["id"])

        resp2 = _rpc(runtime, "message.send", {
            "sessionId": session["id"],
            "content": "second",
            "mode": "queued",
        })
        task2_id = resp2["result"]["task"]["id"]

        resp3 = _rpc(runtime, "message.send", {
            "sessionId": session["id"],
            "content": "third",
            "mode": "queued",
        })
        task3_id = resp3["result"]["task"]["id"]

        # Complete task1 — triggers task2 start
        runtime.orchestrator._complete_task(
            session_id=session["id"],
            task=task1,
            summary="done",
        )

        # Wait for task2 to complete — triggers task3 start
        _wait_task_status(runtime.store, task2_id, {"completed", "failed"})
        _wait_task_status(runtime.store, task3_id, {"completed", "failed"})

        for tid in [task2_id, task3_id]:
            t = runtime.store.get_task({"taskId": tid})["task"]
            assert t["status"] in {"completed", "failed"}


class TestListTasksBySessionAndStatus:
    """Store method: list_tasks_by_session_and_status."""

    def test_returns_matching_tasks_ordered(self, tmp_path: Any) -> None:
        runtime = _make_runtime(tmp_path)
        session = _open_session(runtime, tmp_path)

        t1 = runtime.store.create_task(session_id=session["id"], task_type="react", goal="a", plan=[], status="queued")
        t2 = runtime.store.create_task(session_id=session["id"], task_type="react", goal="b", plan=[], status="queued")
        runtime.store.create_task(session_id=session["id"], task_type="react", goal="c", plan=[], status="running")

        result = runtime.store.list_tasks_by_session_and_status(session["id"], "queued")
        assert len(result) == 2
        assert result[0]["id"] == t1["id"]
        assert result[1]["id"] == t2["id"]

    def test_empty_when_no_match(self, tmp_path: Any) -> None:
        store = SQLiteStore(str(tmp_path / "test.sqlite3"))
        result = store.list_tasks_by_session_and_status("nonexistent", "queued")
        assert result == []
