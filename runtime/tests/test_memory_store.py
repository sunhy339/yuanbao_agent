"""Tests for MemoryStore CRUD and queries."""

from __future__ import annotations

from local_agent_runtime.memory.store import MemoryStore
from local_agent_runtime.memory.types import MemoryEntry, MemoryKind
from local_agent_runtime.store.sqlite_store import SQLiteStore


class TestMemoryStoreCreate:
    def setup_method(self) -> None:
        self.store = SQLiteStore(":memory:")
        self.ms = MemoryStore(self.store)

    def test_create_basic(self) -> None:
        entry = self.ms.create(
            kind=MemoryKind.WORKING,
            content="User prefers compact UI",
            session_id="s1",
        )
        assert entry.id.startswith("mem_")
        assert entry.kind == MemoryKind.WORKING
        assert entry.content == "User prefers compact UI"
        assert entry.session_id == "s1"

    def test_create_with_keywords_and_metadata(self) -> None:
        entry = self.ms.create(
            kind=MemoryKind.SEMANTIC,
            content="Uses pytest for testing",
            workspace_id="w1",
            keywords=["pytest", "testing"],
            metadata={"source": "task_result"},
        )
        assert entry.keywords == ["pytest", "testing"]
        assert entry.metadata == {"source": "task_result"}

    def test_create_all_kinds(self) -> None:
        for kind in MemoryKind:
            entry = self.ms.create(kind=kind, content=f"test {kind.value}")
            assert entry.kind == kind


class TestMemoryStoreRetrieve:
    def setup_method(self) -> None:
        self.store = SQLiteStore(":memory:")
        self.ms = MemoryStore(self.store)
        self.entry = self.ms.create(
            kind=MemoryKind.WORKING,
            content="hello",
            session_id="s1",
        )

    def test_retrieve_existing(self) -> None:
        found = self.ms.retrieve(self.entry.id)
        assert found is not None
        assert found.content == "hello"

    def test_retrieve_nonexistent(self) -> None:
        assert self.ms.retrieve("mem_nope") is None

    def test_retrieve_touches_access_stats(self) -> None:
        assert self.entry.access_count == 0
        found = self.ms.retrieve(self.entry.id)
        assert found is not None
        assert found.access_count == 1
        found2 = self.ms.retrieve(self.entry.id)
        assert found2 is not None
        assert found2.access_count == 2


class TestMemoryStoreUpdate:
    def setup_method(self) -> None:
        self.store = SQLiteStore(":memory:")
        self.ms = MemoryStore(self.store)
        self.entry = self.ms.create(
            kind=MemoryKind.WORKING,
            content="original",
            session_id="s1",
        )

    def test_update_content(self) -> None:
        updated = self.ms.update(self.entry.id, content="updated")
        assert updated is not None
        assert updated.content == "updated"

    def test_update_kind(self) -> None:
        updated = self.ms.update(self.entry.id, kind=MemoryKind.SESSION)
        assert updated is not None
        assert updated.kind == MemoryKind.SESSION

    def test_update_keywords(self) -> None:
        updated = self.ms.update(self.entry.id, keywords=["a", "b"])
        assert updated is not None
        assert updated.keywords == ["a", "b"]

    def test_update_nonexistent(self) -> None:
        assert self.ms.update("mem_nope", content="x") is None


class TestMemoryStoreDelete:
    def setup_method(self) -> None:
        self.store = SQLiteStore(":memory:")
        self.ms = MemoryStore(self.store)

    def test_delete_existing(self) -> None:
        entry = self.ms.create(kind=MemoryKind.WORKING, content="bye")
        assert self.ms.delete(entry.id) is True
        assert self.ms.retrieve(entry.id) is None

    def test_delete_nonexistent(self) -> None:
        assert self.ms.delete("mem_nope") is False


class TestMemoryStoreQueries:
    def setup_method(self) -> None:
        self.store = SQLiteStore(":memory:")
        self.ms = MemoryStore(self.store)
        # Seed data
        self.ms.create(kind=MemoryKind.WORKING, content="w1", session_id="s1", workspace_id="w1")
        self.ms.create(kind=MemoryKind.WORKING, content="w2", session_id="s1", workspace_id="w1")
        self.ms.create(kind=MemoryKind.SESSION, content="s1", session_id="s1", workspace_id="w1")
        self.ms.create(kind=MemoryKind.LONG_TERM, content="lt1", workspace_id="w1")
        self.ms.create(kind=MemoryKind.LONG_TERM, content="lt2", workspace_id="w2")
        self.ms.create(kind=MemoryKind.SEMANTIC, content="sem1", workspace_id="w1")

    def test_query_working(self) -> None:
        results = self.ms.query_working("s1")
        assert len(results) == 2
        assert all(r.kind == MemoryKind.WORKING for r in results)

    def test_query_session(self) -> None:
        results = self.ms.query_session("w1")
        assert len(results) == 1
        assert results[0].kind == MemoryKind.SESSION

    def test_query_long_term(self) -> None:
        results = self.ms.query_long_term("w1")
        assert len(results) == 1
        assert results[0].content == "lt1"

    def test_query_semantic(self) -> None:
        results = self.ms.query_semantic("w1")
        assert len(results) == 1

    def test_query_recent(self) -> None:
        results = self.ms.query_recent("w1", "s1", limit=3)
        assert len(results) == 3

    def test_query_recent_different_workspaces(self) -> None:
        results = self.ms.query_long_term("w2")
        assert len(results) == 1
        assert results[0].content == "lt2"

    def test_delete_by_session(self) -> None:
        count = self.ms.delete_by_session("s1")
        assert count == 2  # 2 WORKING entries
        assert self.ms.query_working("s1") == []


class TestMemoryStorePromote:
    def setup_method(self) -> None:
        self.store = SQLiteStore(":memory:")
        self.ms = MemoryStore(self.store)

    def test_promote_working_to_session(self) -> None:
        entry = self.ms.create(
            kind=MemoryKind.WORKING, content="temp", session_id="s1", workspace_id="w1"
        )
        promoted = self.ms.promote(entry.id, MemoryKind.SESSION)
        assert promoted is not None
        assert promoted.kind == MemoryKind.SESSION
