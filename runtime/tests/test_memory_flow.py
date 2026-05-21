from __future__ import annotations

from typing import Any

from local_agent_runtime.event_bus import EventBus
from local_agent_runtime.memory import MemoryManager, MemoryRetriever
from local_agent_runtime.memory.store import MemoryStore
from local_agent_runtime.orchestrator.service import Orchestrator
from local_agent_runtime.provider.adapter import ProviderAdapter
from local_agent_runtime.store.sqlite_store import SQLiteStore
from local_agent_runtime.tools.registry import ToolRegistry


def _make_orchestrator(tmp_path: Any) -> tuple[Orchestrator, SQLiteStore]:
    event_bus = EventBus()
    store = SQLiteStore(str(tmp_path / "runtime.sqlite3"))
    memory_store = MemoryStore(store)
    memory_manager = MemoryManager(
        store=memory_store,
        retriever=MemoryRetriever(memory_store),
    )
    orchestrator = Orchestrator(
        store=store,
        event_bus=event_bus,
        tool_registry=ToolRegistry({}),
        provider=ProviderAdapter(),
        memory_manager=memory_manager,
    )
    return orchestrator, store


def test_reminder_style_user_message_is_not_promoted_to_user_preference(tmp_path: Any) -> None:
    orchestrator, store = _make_orchestrator(tmp_path)
    workspace = store.upsert_workspace(str(tmp_path / "workspace"))
    session = store.create_session(workspace["id"], "Preference noise")
    task = store.create_task(
        session_id=session["id"],
        task_type="edit",
        goal="Continue work",
        plan=[],
        status="running",
    )
    store.create_message(
        session_id=session["id"],
        task_id=task["id"],
        role="user",
        content="Before we continue, remind yourself about our testing convention.",
    )
    task = store.update_task(
        task["id"],
        status="completed",
        summary="I will follow the remembered repo conventions and recovery guidance.",
    )

    orchestrator._remember_task_result(session_id=session["id"], task=task)  # noqa: SLF001

    entries = MemoryStore(store).query_all(session_id=session["id"], workspace_id=workspace["id"])
    user_preferences = [entry for entry in entries if entry.metadata.get("category") == "user_preference"]
    conventions = [entry for entry in entries if entry.metadata.get("category") == "project_convention"]

    assert user_preferences == []
    assert conventions == []
