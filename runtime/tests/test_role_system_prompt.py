"""Tests for P6.2: role-based system prompts."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from local_agent_runtime.context.builder import ContextBuilder
from local_agent_runtime.event_bus import EventBus
from local_agent_runtime.orchestrator.service import Orchestrator
from local_agent_runtime.store.sqlite_store import SQLiteStore
from local_agent_runtime.tools.registry import ToolRegistry


def _make_store(tmp_path: Any) -> tuple[SQLiteStore, str]:
    store = SQLiteStore(str(tmp_path / "test.sqlite3"))
    workspace = store.upsert_workspace(str(tmp_path))
    session = store.create_session(workspace_id=workspace["id"], title="test")
    return store, session["id"]


def _first_system_message(context: dict[str, Any]) -> str:
    """Extract the first system message content from built context."""
    for msg in context["messages"]:
        if msg.get("role") == "system":
            return msg.get("content", "")
    return ""


class TestRoleSystemPrompt:
    """Test that different roles produce different system prompts."""

    def test_root_role_default_prompt(self, tmp_path: Any) -> None:
        """Root role uses the default coding agent prompt."""
        store, session_id = _make_store(tmp_path)
        builder = ContextBuilder(store=store)
        context = builder.build(session_id=session_id, goal="test", role="root")
        system_msg = _first_system_message(context)
        assert "local coding agent" in system_msg
        assert "worker agent" not in system_msg
        assert "reviewer agent" not in system_msg

    def test_none_role_same_as_root(self, tmp_path: Any) -> None:
        """None role defaults to root prompt."""
        store, session_id = _make_store(tmp_path)
        builder = ContextBuilder(store=store)
        context = builder.build(session_id=session_id, goal="test", role=None)
        system_msg = _first_system_message(context)
        assert "local coding agent" in system_msg

    def test_worker_role_prompt(self, tmp_path: Any) -> None:
        """Worker role gets worker-specific instructions."""
        store, session_id = _make_store(tmp_path)
        builder = ContextBuilder(store=store)
        context = builder.build(session_id=session_id, goal="test", role="worker")
        system_msg = _first_system_message(context)
        assert "worker agent" in system_msg
        assert "assigned scope" in system_msg
        assert "NOT commit" in system_msg

    def test_reviewer_role_prompt(self, tmp_path: Any) -> None:
        """Reviewer role gets read-only review instructions."""
        store, session_id = _make_store(tmp_path)
        builder = ContextBuilder(store=store)
        context = builder.build(session_id=session_id, goal="test", role="reviewer")
        system_msg = _first_system_message(context)
        assert "reviewer agent" in system_msg
        assert "Read-only" in system_msg or "read-only" in system_msg

    def test_planner_role_prompt(self, tmp_path: Any) -> None:
        """Planner role gets planning instructions."""
        store, session_id = _make_store(tmp_path)
        builder = ContextBuilder(store=store)
        context = builder.build(session_id=session_id, goal="test", role="planner")
        system_msg = _first_system_message(context)
        assert "planner agent" in system_msg
        assert "subtask" in system_msg.lower()

    def test_summarizer_role_prompt(self, tmp_path: Any) -> None:
        """Summarizer role gets synthesis instructions."""
        store, session_id = _make_store(tmp_path)
        builder = ContextBuilder(store=store)
        context = builder.build(session_id=session_id, goal="test", role="summarizer")
        system_msg = _first_system_message(context)
        assert "summarizer agent" in system_msg
        assert "synthesize" in system_msg.lower() or "summary" in system_msg.lower()

    def test_all_roles_include_safety_boundaries(self, tmp_path: Any) -> None:
        """All roles include the safety boundaries section."""
        store, session_id = _make_store(tmp_path)
        builder = ContextBuilder(store=store)

        for role in [None, "root", "worker", "reviewer", "planner", "summarizer"]:
            context = builder.build(session_id=session_id, goal="test", role=role)
            system_msg = _first_system_message(context)
            assert "Safety boundaries" in system_msg, f"Role {role} missing safety boundaries"
            assert "stay within the workspace root" in system_msg, f"Role {role} missing workspace constraint"

    def test_run_child_task_passes_role_to_build(self, tmp_path: Any) -> None:
        """run_child_task passes agentType as role to create_task and context builder."""
        store = SQLiteStore(str(tmp_path / "test.sqlite3"))
        workspace = store.upsert_workspace(str(tmp_path))
        session = store.create_session(workspace_id=workspace["id"], title="test")
        event_bus = EventBus()
        tool_registry = ToolRegistry()

        class DummyProvider:
            def generate(self, prompt: str, context: dict[str, Any]) -> dict[str, Any]:
                return {"final_answer": "done"}

        orchestrator = Orchestrator(
            store=store, event_bus=event_bus,
            tool_registry=tool_registry, provider=DummyProvider(),
        )

        result = orchestrator.run_child_task({
            "sessionId": session["id"],
            "prompt": "Implement auth module",
            "agentType": "worker",
        })

        task = result["task"]
        assert task["role"] == "worker"

    def test_run_child_task_default_role(self, tmp_path: Any) -> None:
        """run_child_task defaults to 'worker' when agentType not specified."""
        store = SQLiteStore(str(tmp_path / "test.sqlite3"))
        workspace = store.upsert_workspace(str(tmp_path))
        session = store.create_session(workspace_id=workspace["id"], title="test")
        event_bus = EventBus()
        tool_registry = ToolRegistry()

        class DummyProvider:
            def generate(self, prompt: str, context: dict[str, Any]) -> dict[str, Any]:
                return {"final_answer": "done"}

        orchestrator = Orchestrator(
            store=store, event_bus=event_bus,
            tool_registry=tool_registry, provider=DummyProvider(),
        )

        result = orchestrator.run_child_task({
            "sessionId": session["id"],
            "prompt": "Review the changes",
        })

        task = result["task"]
        assert task["role"] == "worker"
