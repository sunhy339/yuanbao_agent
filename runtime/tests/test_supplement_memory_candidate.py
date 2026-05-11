"""Tests for P4.5.3: supplement memory candidate writing.

When a supplement containing preference/convention-like content is consumed,
a memory candidate should be written to working memory.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from local_agent_runtime.event_bus import EventBus
from local_agent_runtime.memory import MemoryManager, MemoryRetriever
from local_agent_runtime.memory.store import MemoryStore
from local_agent_runtime.memory.types import MemoryKind
from local_agent_runtime.orchestrator.service import Orchestrator
from local_agent_runtime.rpc.server import JsonRpcServer
from local_agent_runtime.store.sqlite_store import SQLiteStore
from local_agent_runtime.tools.registry import ToolRegistry


_ROUTING_RESPONSE: dict[str, Any] = {
    "message": '{"scenario": "edit", "confidence": 0.9, "reasoning": "test"}',
}


class ScriptedProvider:
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def generate(self, prompt: str, context: dict[str, Any]) -> dict[str, Any]:
        self.calls.append({"prompt": prompt, "context": context})
        # Auto-answer routing classification prompts
        if "task classifier" in prompt or "classify it into" in prompt:
            return _ROUTING_RESPONSE.copy()
        if not self._responses:
            raise AssertionError("Provider called more times than scripted")
        return self._responses.pop(0)


def _call_result(response: dict[str, Any], key: str) -> dict[str, Any]:
    assert "result" in response, response
    return response["result"][key]


def _make_runtime(tmp_path: Any, provider: Any, tools: dict[str, Any] | None = None) -> SimpleNamespace:
    event_bus = EventBus()
    store = SQLiteStore(str(tmp_path / "runtime.sqlite3"))
    store.set_feature_flag("multiAgent", True)
    tool_registry = ToolRegistry(tools or {})
    memory_manager = MemoryManager(
        store=MemoryStore(store),
        retriever=MemoryRetriever(MemoryStore(store)),
    )
    orchestrator = Orchestrator(
        store=store,
        event_bus=event_bus,
        tool_registry=tool_registry,
        provider=provider,
        memory_manager=memory_manager,
    )
    server = JsonRpcServer(orchestrator=orchestrator, store=store, event_bus=event_bus)
    events: list[dict[str, Any]] = []
    event_bus.subscribe(lambda event: events.append(event_bus.as_payload(event)))
    return SimpleNamespace(server=server, store=store, events=events)


def _rpc(runtime: SimpleNamespace, method: str, params: dict[str, Any]) -> dict[str, Any]:
    import json
    request_id = f"req_{len(runtime.events)}_{method}"
    envelope = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
    return runtime.server.handle_line(json.dumps(envelope, ensure_ascii=False))


def _open_workspace(runtime: SimpleNamespace, tmp_path: Any) -> str:
    import os
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(exist_ok=True)
    ws = _call_result(_rpc(runtime, "workspace.open", {"path": str(workspace_root)}), "workspace")
    return ws["id"]


def _open_session(runtime: SimpleNamespace, tmp_path: Any) -> dict[str, Any]:
    workspace_id = _open_workspace(runtime, tmp_path)
    return _call_result(
        _rpc(runtime, "session.create", {"workspaceId": workspace_id, "title": "Test"}),
        "session",
    )


class TestSupplementMemoryCandidate:
    """When a preference-like supplement is consumed, a memory candidate is written."""

    def test_preference_supplement_creates_memory(self, tmp_path: Any) -> None:
        """A supplement containing a user preference should be stored as a memory candidate."""
        provider = ScriptedProvider([
            {"message": "Working...", "tool_calls": [{"id": "call_slow", "name": "slow_tool", "arguments": {}}]},
            {"final": "Done, using type hints."},
        ])

        store_ref_holder: list[Any] = []

        def slow_tool(params: dict[str, Any]) -> dict[str, Any]:
            store = store_ref_holder[0]
            store.create_inbox_entry(
                task_id=params["taskId"],
                session_id=params["sessionId"],
                content="Always use type hints in Python code",
            )
            return {"result": "ok"}

        runtime = _make_runtime(tmp_path, provider, {"slow_tool": slow_tool})
        store_ref_holder.append(runtime.store)
        session = _open_session(runtime, tmp_path)

        task = _call_result(
            _rpc(runtime, "message.send", {"sessionId": session["id"], "content": "do work"}),
            "task",
        )

        assert task["status"] == "completed"

        # Check that a memory was created for the supplement
        # Note: working memories are auto-consolidated to session kind on task completion
        mem_store = MemoryStore(runtime.store)
        all_memories = mem_store.query_all(session_id=session["id"])
        preference_memories = [m for m in all_memories if "type hints" in m.content]
        assert len(preference_memories) >= 1, f"Expected at least 1 preference memory, got {len(preference_memories)}"

        # Verify metadata
        mem = preference_memories[0]
        assert mem.metadata.get("category") == "user_preference"
        assert mem.metadata.get("source") == "supplement"
        assert mem.metadata.get("confidence") == 0.5
        assert "type hints" in mem.content

    def test_non_preference_supplement_no_memory(self, tmp_path: Any) -> None:
        """A supplement without preference patterns should not create a memory candidate."""
        provider = ScriptedProvider([
            {"message": "Working...", "tool_calls": [{"id": "call_slow", "name": "slow_tool", "arguments": {}}]},
            {"final": "Done."},
        ])

        store_ref_holder: list[Any] = []

        def slow_tool(params: dict[str, Any]) -> dict[str, Any]:
            store = store_ref_holder[0]
            store.create_inbox_entry(
                task_id=params["taskId"],
                session_id=params["sessionId"],
                content="Check the output file for errors",  # no preference patterns
            )
            return {"result": "ok"}

        runtime = _make_runtime(tmp_path, provider, {"slow_tool": slow_tool})
        store_ref_holder.append(runtime.store)
        session = _open_session(runtime, tmp_path)

        task = _call_result(
            _rpc(runtime, "message.send", {"sessionId": session["id"], "content": "do work"}),
            "task",
        )

        assert task["status"] == "completed"

        # No preference memory should be created for non-preference supplement
        mem_store = MemoryStore(runtime.store)
        all_memories = mem_store.query_all(session_id=session["id"])
        supplement_memories = [m for m in all_memories if m.metadata.get("source") == "supplement"]
        assert len(supplement_memories) == 0

    def test_short_supplement_no_memory(self, tmp_path: Any) -> None:
        """Very short supplements should not create memory candidates."""
        provider = ScriptedProvider([
            {"message": "Working...", "tool_calls": [{"id": "call_slow", "name": "slow_tool", "arguments": {}}]},
            {"final": "Done."},
        ])

        store_ref_holder: list[Any] = []

        def slow_tool(params: dict[str, Any]) -> dict[str, Any]:
            store = store_ref_holder[0]
            store.create_inbox_entry(
                task_id=params["taskId"],
                session_id=params["sessionId"],
                content="use x",  # too short (< 10 chars)
            )
            return {"result": "ok"}

        runtime = _make_runtime(tmp_path, provider, {"slow_tool": slow_tool})
        store_ref_holder.append(runtime.store)
        session = _open_session(runtime, tmp_path)

        task = _call_result(
            _rpc(runtime, "message.send", {"sessionId": session["id"], "content": "do work"}),
            "task",
        )

        assert task["status"] == "completed"

        mem_store = MemoryStore(runtime.store)
        all_memories = mem_store.query_all(session_id=session["id"])
        supplement_memories = [m for m in all_memories if m.metadata.get("source") == "supplement"]
        assert len(supplement_memories) == 0

    def test_convention_supplement_creates_memory(self, tmp_path: Any) -> None:
        """A supplement about conventions should be stored as a memory candidate."""
        provider = ScriptedProvider([
            {"message": "Working...", "tool_calls": [{"id": "call_slow", "name": "slow_tool", "arguments": {}}]},
            {"final": "Done."},
        ])

        store_ref_holder: list[Any] = []

        def slow_tool(params: dict[str, Any]) -> dict[str, Any]:
            store = store_ref_holder[0]
            store.create_inbox_entry(
                task_id=params["taskId"],
                session_id=params["sessionId"],
                content="We use snake_case for all function names in this project",
            )
            return {"result": "ok"}

        runtime = _make_runtime(tmp_path, provider, {"slow_tool": slow_tool})
        store_ref_holder.append(runtime.store)
        session = _open_session(runtime, tmp_path)

        task = _call_result(
            _rpc(runtime, "message.send", {"sessionId": session["id"], "content": "do work"}),
            "task",
        )

        assert task["status"] == "completed"

        mem_store = MemoryStore(runtime.store)
        all_memories = mem_store.query_all(session_id=session["id"])
        supplement_memories = [m for m in all_memories if m.metadata.get("source") == "supplement"]
        assert len(supplement_memories) >= 1
        assert "snake_case" in supplement_memories[0].content


class TestRememberSupplementCandidates:
    """Unit tests for _remember_supplement_candidates method."""

    def test_preference_patterns_detected(self) -> None:
        """Words like 'prefer', 'always', 'never' should trigger candidate creation."""
        patterns = Orchestrator._SUPPLEMENT_MEMORY_PATTERNS
        test_cases = [
            ("I prefer dark mode", True),
            ("Always use strict mode", True),
            ("Never use var in JavaScript", True),
            ("Make sure to add tests", True),
            ("We use TypeScript", True),
            ("Convention is to use tabs", True),
            ("Check the file", False),
            ("run the command", False),
        ]
        for content, expected in test_cases:
            lower = content.lower()
            is_match = any(p in lower for p in patterns)
            assert is_match == expected, f"'{content}': expected {expected}, got {is_match}"
