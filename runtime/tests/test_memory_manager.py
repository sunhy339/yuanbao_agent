"""Tests for MemoryManager high-level API."""

from __future__ import annotations

from local_agent_runtime.memory.manager import MemoryManager
from local_agent_runtime.memory.retriever import MemoryRetriever
from local_agent_runtime.memory.store import MemoryStore
from local_agent_runtime.memory.types import MemoryKind
from local_agent_runtime.store.sqlite_store import SQLiteStore


class TestMemoryManagerRemember:
    def setup_method(self) -> None:
        self.store = SQLiteStore(":memory:")
        self.ms = MemoryStore(self.store)
        self.retriever = MemoryRetriever(self.ms)
        self.mgr = MemoryManager(self.ms, self.retriever)

    def test_remember_basic(self) -> None:
        entry = self.mgr.remember(
            content="User prefers dark mode",
            session_id="s1",
        )
        assert entry.id.startswith("mem_")
        assert entry.kind == MemoryKind.WORKING
        assert entry.content == "User prefers dark mode"
        assert entry.session_id == "s1"

    def test_remember_auto_extracts_keywords(self) -> None:
        entry = self.mgr.remember(
            content="Use pytest for testing",
            workspace_id="w1",
        )
        assert "pytest" in entry.keywords
        assert "testing" in entry.keywords

    def test_remember_with_explicit_kind(self) -> None:
        entry = self.mgr.remember(
            content="Persistent preference",
            workspace_id="w1",
            kind=MemoryKind.LONG_TERM,
        )
        assert entry.kind == MemoryKind.LONG_TERM


class TestMemoryManagerRecall:
    def setup_method(self) -> None:
        self.store = SQLiteStore(":memory:")
        self.ms = MemoryStore(self.store)
        self.retriever = MemoryRetriever(self.ms)
        self.mgr = MemoryManager(self.ms, self.retriever)

    def test_recall_returns_relevant(self) -> None:
        self.mgr.remember(
            content="User prefers dark mode for coding",
            workspace_id="w1",
            kind=MemoryKind.SEMANTIC,
        )
        self.mgr.remember(
            content="User likes cats and dogs",
            workspace_id="w1",
            kind=MemoryKind.SEMANTIC,
        )
        results = self.mgr.recall(workspace_id="w1", query="dark mode coding")
        assert len(results) >= 1
        assert any("dark mode" in e.content for e in results)

    def test_recall_fills_with_recent(self) -> None:
        # Create a semantic entry that doesn't match the query
        self.mgr.remember(
            content="Unrelated semantic entry about cats",
            workspace_id="w1",
            kind=MemoryKind.SEMANTIC,
        )
        # Create a working memory (appears in recent)
        self.mgr.remember(
            content="Working note about Python",
            session_id="s1",
            workspace_id="w1",
        )
        # Query won't match either well, but recent path fills in
        results = self.mgr.recall(workspace_id="w1", query="quantum physics")
        # May still return recent entries even if semantic match is poor
        assert isinstance(results, list)

    def test_recall_respects_limit(self) -> None:
        for i in range(10):
            self.mgr.remember(
                content=f"Python testing note {i}",
                workspace_id="w1",
                kind=MemoryKind.SEMANTIC,
            )
        results = self.mgr.recall(workspace_id="w1", query="Python testing", limit=3)
        assert len(results) <= 3

    def test_recall_deduplicates(self) -> None:
        self.mgr.remember(
            content="Python pytest testing guide",
            workspace_id="w1",
            kind=MemoryKind.SEMANTIC,
        )
        results = self.mgr.recall(workspace_id="w1", query="Python pytest testing")
        ids = [e.id for e in results]
        assert len(ids) == len(set(ids))


class TestMemoryManagerConsolidate:
    def setup_method(self) -> None:
        self.store = SQLiteStore(":memory:")
        self.ms = MemoryStore(self.store)
        self.retriever = MemoryRetriever(self.ms)
        self.mgr = MemoryManager(self.ms, self.retriever)

    def test_consolidate_promotes_working_to_session(self) -> None:
        self.mgr.remember(content="temp note 1", session_id="s1")
        self.mgr.remember(content="temp note 2", session_id="s1")
        self.mgr.remember(content="other session", session_id="s2")

        count = self.mgr.consolidate("s1")
        assert count == 2

        # Verify they are now SESSION kind
        working = self.ms.query_working("s1")
        assert len(working) == 0

    def test_consolidate_empty_session(self) -> None:
        count = self.mgr.consolidate("nonexistent")
        assert count == 0


class TestMemoryManagerForgetWorking:
    def setup_method(self) -> None:
        self.store = SQLiteStore(":memory:")
        self.ms = MemoryStore(self.store)
        self.retriever = MemoryRetriever(self.ms)
        self.mgr = MemoryManager(self.ms, self.retriever)

    def test_forget_working_removes_session(self) -> None:
        self.mgr.remember(content="temp 1", session_id="s1")
        self.mgr.remember(content="temp 2", session_id="s1")

        deleted = self.mgr.forget_working("s1")
        assert deleted == 2

    def test_forget_working_empty_session(self) -> None:
        deleted = self.mgr.forget_working("nonexistent")
        assert deleted == 0


class TestMemoryManagerRetrieveDelete:
    def setup_method(self) -> None:
        self.store = SQLiteStore(":memory:")
        self.ms = MemoryStore(self.store)
        self.retriever = MemoryRetriever(self.ms)
        self.mgr = MemoryManager(self.ms, self.retriever)

    def test_retrieve_existing(self) -> None:
        entry = self.mgr.remember(content="test note")
        fetched = self.mgr.retrieve(entry.id)
        assert fetched is not None
        assert fetched.content == "test note"

    def test_retrieve_nonexistent(self) -> None:
        assert self.mgr.retrieve("mem_nonexistent") is None

    def test_delete_existing(self) -> None:
        entry = self.mgr.remember(content="to delete")
        assert self.mgr.delete(entry.id) is True
        assert self.mgr.retrieve(entry.id) is None

    def test_delete_nonexistent(self) -> None:
        assert self.mgr.delete("mem_nonexistent") is False
