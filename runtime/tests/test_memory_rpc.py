"""Tests for memory management RPC endpoints: memory.list/get/edit/delete."""

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
    orchestrator = Orchestrator(
        store=store, event_bus=event_bus,
        tool_registry=tool_registry, provider=None,
    )
    server = JsonRpcServer(orchestrator=orchestrator, store=store, event_bus=event_bus)
    return SimpleNamespace(server=server, store=store)


def _rpc(runtime: SimpleNamespace, method: str, params: dict[str, Any]) -> dict[str, Any]:
    request_id = f"req_{method}"
    envelope = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
    return runtime.server.handle_line(json.dumps(envelope, ensure_ascii=False))


def _open_workspace(runtime: SimpleNamespace, tmp_path: Any) -> str:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(exist_ok=True)
    ws = _rpc(runtime, "workspace.open", {"path": str(workspace_root)})["result"]["workspace"]
    return ws["id"]


# ── tests ──────────────────────────────────────────────────────────────────


class TestMemoryListRpc:
    """memory.list returns entries matching filters."""

    def test_list_empty(self, tmp_path: Any) -> None:
        runtime = _make_runtime(tmp_path)
        resp = _rpc(runtime, "memory.list", {})
        assert resp["result"]["entries"] == []

    def test_list_with_session_filter(self, tmp_path: Any) -> None:
        runtime = _make_runtime(tmp_path)
        from local_agent_runtime.memory.store import MemoryStore
        from local_agent_runtime.memory.types import MemoryKind
        mem = MemoryStore(runtime.store)
        mem.create(kind=MemoryKind.WORKING, content="for-session", session_id="s1")
        mem.create(kind=MemoryKind.WORKING, content="for-session-2", session_id="s2")

        resp = _rpc(runtime, "memory.list", {"sessionId": "s1"})
        entries = resp["result"]["entries"]
        assert len(entries) == 1
        assert entries[0]["content"] == "for-session"

    def test_list_with_kind_filter(self, tmp_path: Any) -> None:
        runtime = _make_runtime(tmp_path)
        from local_agent_runtime.memory.store import MemoryStore
        from local_agent_runtime.memory.types import MemoryKind
        mem = MemoryStore(runtime.store)
        mem.create(kind=MemoryKind.WORKING, content="working", session_id="s1")
        mem.create(kind=MemoryKind.LONG_TERM, content="long-term", workspace_id="w1")

        resp = _rpc(runtime, "memory.list", {"kind": "long_term"})
        entries = resp["result"]["entries"]
        assert len(entries) == 1
        assert entries[0]["kind"] == "long_term"


class TestMemoryGetRpc:
    """memory.get retrieves a single entry by ID."""

    def test_get_existing(self, tmp_path: Any) -> None:
        runtime = _make_runtime(tmp_path)
        from local_agent_runtime.memory.store import MemoryStore
        from local_agent_runtime.memory.types import MemoryKind
        mem = MemoryStore(runtime.store)
        entry = mem.create(kind=MemoryKind.WORKING, content="hello", session_id="s1")

        resp = _rpc(runtime, "memory.get", {"entryId": entry.id})
        assert resp["result"]["entry"]["id"] == entry.id
        assert resp["result"]["entry"]["content"] == "hello"

    def test_get_nonexistent(self, tmp_path: Any) -> None:
        runtime = _make_runtime(tmp_path)
        resp = _rpc(runtime, "memory.get", {"entryId": "nope"})
        assert "error" in resp


class TestMemoryEditRpc:
    """memory.edit updates entry fields."""

    def test_edit_content(self, tmp_path: Any) -> None:
        runtime = _make_runtime(tmp_path)
        from local_agent_runtime.memory.store import MemoryStore
        from local_agent_runtime.memory.types import MemoryKind
        mem = MemoryStore(runtime.store)
        entry = mem.create(kind=MemoryKind.WORKING, content="original", session_id="s1")

        resp = _rpc(runtime, "memory.edit", {"entryId": entry.id, "content": "updated"})
        assert resp["result"]["entry"]["content"] == "updated"

    def test_edit_kind(self, tmp_path: Any) -> None:
        runtime = _make_runtime(tmp_path)
        from local_agent_runtime.memory.store import MemoryStore
        from local_agent_runtime.memory.types import MemoryKind
        mem = MemoryStore(runtime.store)
        entry = mem.create(kind=MemoryKind.WORKING, content="data", session_id="s1")

        resp = _rpc(runtime, "memory.edit", {"entryId": entry.id, "kind": "session"})
        assert resp["result"]["entry"]["kind"] == "session"

    def test_edit_metadata(self, tmp_path: Any) -> None:
        runtime = _make_runtime(tmp_path)
        from local_agent_runtime.memory.store import MemoryStore
        from local_agent_runtime.memory.types import MemoryKind
        mem = MemoryStore(runtime.store)
        entry = mem.create(kind=MemoryKind.WORKING, content="data", session_id="s1")

        resp = _rpc(runtime, "memory.edit", {
            "entryId": entry.id,
            "metadata": {"confidence": 0.9},
        })
        assert resp["result"]["entry"]["metadata"]["confidence"] == 0.9

    def test_edit_nonexistent(self, tmp_path: Any) -> None:
        runtime = _make_runtime(tmp_path)
        resp = _rpc(runtime, "memory.edit", {"entryId": "nope", "content": "x"})
        assert "error" in resp


class TestMemoryDeleteRpc:
    """memory.delete removes an entry."""

    def test_delete_existing(self, tmp_path: Any) -> None:
        runtime = _make_runtime(tmp_path)
        from local_agent_runtime.memory.store import MemoryStore
        from local_agent_runtime.memory.types import MemoryKind
        mem = MemoryStore(runtime.store)
        entry = mem.create(kind=MemoryKind.WORKING, content="bye", session_id="s1")

        resp = _rpc(runtime, "memory.delete", {"entryId": entry.id})
        assert resp["result"]["deleted"] is True

        # Verify it's gone
        resp2 = _rpc(runtime, "memory.get", {"entryId": entry.id})
        assert "error" in resp2

    def test_delete_nonexistent(self, tmp_path: Any) -> None:
        runtime = _make_runtime(tmp_path)
        resp = _rpc(runtime, "memory.delete", {"entryId": "nope"})
        assert resp["result"]["deleted"] is False
