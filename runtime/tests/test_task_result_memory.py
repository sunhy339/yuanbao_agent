from __future__ import annotations

from typing import Any

from local_agent_runtime.event_bus import EventBus
from local_agent_runtime.memory import MemoryManager, MemoryRetriever
from local_agent_runtime.memory.store import MemoryStore
from local_agent_runtime.orchestrator.service import Orchestrator
from local_agent_runtime.provider.adapter import ProviderAdapter
from local_agent_runtime.store.sqlite_store import SQLiteStore
from local_agent_runtime.tools.registry import ToolRegistry


def _make_orchestrator(tmp_path: Any) -> tuple[Orchestrator, SQLiteStore, list[dict[str, Any]]]:
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
    events: list[dict[str, Any]] = []
    event_bus.subscribe(lambda event: events.append(event_bus.as_payload(event)))
    return orchestrator, store, events


def test_completed_task_extracts_verified_capability_and_convention_memories(tmp_path: Any) -> None:
    orchestrator, store, events = _make_orchestrator(tmp_path)
    workspace = store.upsert_workspace(str(tmp_path / "workspace"))
    session = store.create_session(workspace["id"], "Memory extraction")
    task = store.create_task(
        session_id=session["id"],
        task_type="edit",
        goal="Implement backend verification flow",
        plan=[],
        status="running",
    )
    task = store.update_task(
        task["id"],
        status="completed",
        summary="By default we use python -m pytest for verification in this repo.",
        changed_files=[{"path": "incident_rules.py", "status": "modified"}],
        verification=[{"status": "passed", "summary": "pytest passed"}],
    )

    orchestrator._remember_task_result(session_id=session["id"], task=task)  # noqa: SLF001

    memory_store = MemoryStore(store)
    entries = memory_store.query_all(session_id=session["id"], workspace_id=workspace["id"])
    categories = {str(entry.metadata.get("category")) for entry in entries}

    assert "task_learning" in categories
    assert "verified_capability" in categories
    assert "project_convention" in categories
    memory_events = [event for event in events if event["type"] == "memory_event"]
    assert memory_events
    event_categories = {event["payload"]["category"] for event in memory_events}
    assert {"task_learning", "verified_capability", "project_convention"}.issubset(event_categories)
    assert all(event["payload"]["memoryId"] for event in memory_events)
    assert all(event["payload"]["summary"] for event in memory_events)
    assert all(event["payload"].get("_bridge", {}).get("suppressRealtimeFlat") is True for event in memory_events)
    assert all(event["payload"].get("_bridge", {}).get("suppressChatReplay") is True for event in memory_events)

    verified = next(entry for entry in entries if entry.metadata.get("category") == "verified_capability")
    assert "Verified outcome: Implement backend verification flow" in verified.content
    assert "incident_rules.py" in verified.content
    assert "pytest passed" in verified.content

    convention = next(entry for entry in entries if entry.metadata.get("category") == "project_convention")
    assert "python -m pytest" in convention.content


def test_task_result_extracts_runtime_invariant_and_recovery_pattern_memories(tmp_path: Any) -> None:
    orchestrator, store, events = _make_orchestrator(tmp_path)
    workspace = store.upsert_workspace(str(tmp_path / "workspace"))
    session = store.create_session(workspace["id"], "Invariant extraction")

    completed = store.create_task(
        session_id=session["id"],
        task_type="review",
        goal="Review runtime constraints",
        plan=[],
        status="running",
    )
    completed = store.update_task(
        completed["id"],
        status="completed",
        summary="Reviewer child should not write files and must stay read-only in runtime execution.",
        verification=[{"status": "passed", "summary": "review complete"}],
    )
    orchestrator._remember_task_result(session_id=session["id"], task=completed)  # noqa: SLF001

    failed = store.create_task(
        session_id=session["id"],
        task_type="debug",
        goal="Handle flaky verification",
        plan=[],
        status="running",
    )
    failed = store.update_task(
        failed["id"],
        status="failed",
        summary="If pytest fails, retry after fixing imports and revalidate the task.",
    )
    orchestrator._remember_task_result(session_id=session["id"], task=failed)  # noqa: SLF001

    entries = MemoryStore(store).query_all(session_id=session["id"], workspace_id=workspace["id"])
    invariant = next(entry for entry in entries if entry.metadata.get("category") == "runtime_invariant")
    recovery = next(entry for entry in entries if entry.metadata.get("category") == "failure_recovery_pattern")

    assert "read-only" in invariant.content.lower()
    assert "retry" in recovery.content.lower()
    assert "revalidate" in recovery.content.lower()
    event_categories = {
        event["payload"]["category"]
        for event in events
        if event["type"] == "memory_event"
    }
    assert "runtime_invariant" in event_categories
    assert "failure_recovery_pattern" in event_categories


def test_task_result_does_not_double_prefix_structured_memory_labels(tmp_path: Any) -> None:
    orchestrator, store, _events = _make_orchestrator(tmp_path)
    workspace = store.upsert_workspace(str(tmp_path / "workspace"))
    session = store.create_session(workspace["id"], "Prefix normalization")
    task = store.create_task(
        session_id=session["id"],
        task_type="edit",
        goal="Capture canonical labels",
        plan=[],
        status="running",
    )
    task = store.update_task(
        task["id"],
        status="completed",
        summary="Project convention: By default we use python -m pytest in this repo.",
        verification=[{"status": "passed", "summary": "pytest passed"}],
    )

    orchestrator._remember_task_result(session_id=session["id"], task=task)  # noqa: SLF001

    entries = MemoryStore(store).query_all(session_id=session["id"], workspace_id=workspace["id"])
    convention = next(entry for entry in entries if entry.metadata.get("category") == "project_convention")

    assert convention.content == "Project convention: By default we use python -m pytest in this repo."
