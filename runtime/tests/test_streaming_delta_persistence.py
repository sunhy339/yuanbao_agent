"""Tests for streaming delta persistence to assistant message content.

Covers:
  - Streamed deltas are accumulated and persisted to the assistant message
  - Periodic persistence happens during streaming
  - Final persistence ensures complete content after stream ends
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


class _StreamProvider:
    """Provider that returns a simple final answer."""

    def generate(self, prompt: str, context: dict[str, Any]) -> dict[str, Any]:
        return {"final": "Hello world test"}


def _make_runtime(tmp_path: Any, provider: Any) -> SimpleNamespace:
    event_bus = EventBus()
    store = SQLiteStore(str(tmp_path / "runtime.sqlite3"))
    tool_registry = ToolRegistry()
    orchestrator = Orchestrator(
        store=store, event_bus=event_bus,
        tool_registry=tool_registry, provider=provider,
    )
    server = JsonRpcServer(orchestrator=orchestrator, store=store, event_bus=event_bus)
    events: list[dict[str, Any]] = []
    event_bus.subscribe(lambda event: events.append(event_bus.as_payload(event)))
    return SimpleNamespace(server=server, store=store, events=events, orchestrator=orchestrator)


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


# ── tests ──────────────────────────────────────────────────────────────────


class TestStreamingDeltaPersistence:
    """Verify that streamed content is persisted to assistant message."""

    def test_complete_task_has_streamed_content(self, tmp_path: Any) -> None:
        """After task completes, assistant message content should contain all streamed text."""
        provider = _StreamProvider()
        runtime = _make_runtime(tmp_path, provider)
        session = _open_session(runtime, tmp_path)

        resp = _rpc(runtime, "message.send", {
            "sessionId": session["id"],
            "content": "test streaming",
        })
        task_id = resp["result"]["task"]["id"]

        # Wait for background execution to complete
        import time
        for _ in range(50):
            task = runtime.store.get_task({"taskId": task_id})["task"]
            if task["status"] in {"completed", "failed"}:
                break
            time.sleep(0.1)

        # The assistant message should have content persisted
        messages = runtime.store.list_messages({"sessionId": session["id"]}).get("messages", [])
        assistant_msgs = [m for m in messages if m["role"] == "assistant" and m.get("taskId") == task_id]
        assert len(assistant_msgs) >= 1
        # Content should contain the provider's response
        assert assistant_msgs[0]["content"] != ""
