from __future__ import annotations

from local_agent_runtime.memory import MemoryManager, MemoryRetriever, MemoryStore
from local_agent_runtime.memory.types import MemoryKind
from local_agent_runtime.store.sqlite_store import SQLiteStore


def _manager(store: SQLiteStore) -> MemoryManager:
    memory_store = MemoryStore(store)
    return MemoryManager(
        store=memory_store,
        retriever=MemoryRetriever(memory_store),
    )


def test_semantic_recall_finds_workspace_memory_from_other_session() -> None:
    store = SQLiteStore(":memory:")
    manager = _manager(store)

    manager.remember(
        workspace_id="ws_1",
        session_id="sess_previous",
        content="Project uses Playwright visual regression for desktop UI smoke tests.",
        kind=MemoryKind.SESSION,
    )

    recalled = manager.recall(
        workspace_id="ws_1",
        session_id="sess_current",
        query="desktop visual regression",
        limit=5,
    )

    assert [entry.content for entry in recalled] == [
        "Project uses Playwright visual regression for desktop UI smoke tests.",
    ]


def test_semantic_recall_keeps_current_session_as_relevance_boost_not_filter() -> None:
    store = SQLiteStore(":memory:")
    manager = _manager(store)

    other = manager.remember(
        workspace_id="ws_1",
        session_id="sess_previous",
        content="Use pytest for backend regression tests.",
        kind=MemoryKind.SESSION,
    )
    current = manager.remember(
        workspace_id="ws_1",
        session_id="sess_current",
        content="Use pytest for backend regression tests.",
        kind=MemoryKind.WORKING,
    )

    scored = manager.recall_with_scores(
        workspace_id="ws_1",
        session_id="sess_current",
        query="pytest backend regression",
        limit=5,
    )

    assert {entry.id for entry, _score in scored} == {other.id, current.id}
    assert scored[0][0].id == current.id
