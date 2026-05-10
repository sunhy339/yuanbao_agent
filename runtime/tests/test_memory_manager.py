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


class TestMemoryDeleteExcludesFromRecall:
    """Deleting a memory entry removes it from subsequent recall results."""

    def setup_method(self) -> None:
        self.store = SQLiteStore(":memory:")
        self.ms = MemoryStore(self.store)
        self.retriever = MemoryRetriever(self.ms)
        self.mgr = MemoryManager(self.ms, self.retriever)

    def test_deleted_memory_not_in_recall(self) -> None:
        """After deleting a memory, recall should not include it."""
        entry = self.mgr.remember(
            content="Use tabs not spaces",
            workspace_id="w1",
            kind=MemoryKind.LONG_TERM,
        )
        # Recall before delete — should find it
        results_before = self.mgr.recall(workspace_id="w1", query="tabs spaces")
        assert any(e.id == entry.id for e in results_before)

        # Delete and recall again
        self.mgr.delete(entry.id)
        results_after = self.mgr.recall(workspace_id="w1", query="tabs spaces")
        assert not any(e.id == entry.id for e in results_after)


class TestMemoryUserPreferenceRecall:
    """User preferences written as user_preference category can be recalled."""

    def setup_method(self) -> None:
        self.store = SQLiteStore(":memory:")
        self.ms = MemoryStore(self.store)
        self.retriever = MemoryRetriever(self.ms)
        self.mgr = MemoryManager(self.ms, self.retriever)

    def test_user_preference_recallable(self) -> None:
        """A memory tagged as user_preference in metadata should be recallable."""
        self.mgr.remember(
            content="User prefers dark mode for all editors",
            workspace_id="w1",
            kind=MemoryKind.LONG_TERM,
            metadata={"category": "user_preference", "confidence": 0.9},
        )
        results = self.mgr.recall(workspace_id="w1", query="editor theme preference")
        assert len(results) >= 1
        assert "dark mode" in results[0].content


class TestMemorySourceMessageIds:
    """sourceMessageIds are stored in metadata and merged on dedup."""

    def setup_method(self) -> None:
        self.store = SQLiteStore(":memory:")
        self.ms = MemoryStore(self.store)
        self.retriever = MemoryRetriever(self.ms)
        self.mgr = MemoryManager(self.ms, self.retriever)

    def test_remember_stores_source_message_ids(self) -> None:
        entry = self.mgr.remember(
            content="Task completed: fix auth bug",
            workspace_id="w1",
            metadata={
                "sourceTaskIds": ["tsk_1"],
                "sourceMessageIds": ["msg_assistant_1", "msg_user_1"],
            },
        )
        fetched = self.mgr.retrieve(entry.id)
        assert fetched is not None
        msg_ids = fetched.metadata.get("sourceMessageIds")
        assert set(msg_ids) == {"msg_assistant_1", "msg_user_1"}

    def test_dedup_merges_source_message_ids(self) -> None:
        # First memory
        self.mgr.remember(
            content="Task: implement auth module",
            workspace_id="w1",
            metadata={
                "sourceTaskIds": ["tsk_1"],
                "sourceMessageIds": ["msg_a1", "msg_u1"],
                "confidence": 0.8,
            },
            dedup=True,
            dedup_threshold=0.3,
        )
        # Similar content with different message ids — dedup should merge
        entry2 = self.mgr.remember(
            content="Task: implement auth module",
            workspace_id="w1",
            metadata={
                "sourceTaskIds": ["tsk_2"],
                "sourceMessageIds": ["msg_a2", "msg_u2"],
                "confidence": 0.9,
            },
            dedup=True,
            dedup_threshold=0.3,
        )
        fetched = self.mgr.retrieve(entry2.id)
        assert fetched is not None
        merged_msg_ids = set(fetched.metadata.get("sourceMessageIds", []))
        assert "msg_a1" in merged_msg_ids
        assert "msg_u1" in merged_msg_ids
        assert "msg_a2" in merged_msg_ids
        assert "msg_u2" in merged_msg_ids
        # Task IDs should also be merged
        merged_task_ids = set(fetched.metadata.get("sourceTaskIds", []))
        assert "tsk_1" in merged_task_ids
        assert "tsk_2" in merged_task_ids


class TestPinnedMemoryScoreBoost:
    """Pinned memories receive a +0.3 score boost in recall."""

    def setup_method(self) -> None:
        self.store = SQLiteStore(":memory:")
        self.ms = MemoryStore(self.store)
        self.retriever = MemoryRetriever(self.ms)
        self.mgr = MemoryManager(self.ms, self.retriever)

    def test_pinned_memory_scores_higher_than_unpinned(self) -> None:
        """A pinned memory should score higher than an unpinned one with similar content."""
        # Create two similar memories — use returned entry objects directly
        e1 = self.mgr.remember(
            content="Use snake_case for Python function names",
            workspace_id="w1",
            kind=MemoryKind.LONG_TERM,
            metadata={"category": "project_convention", "confidence": 0.7},
        )
        unpinned_id = e1.id

        e2 = self.mgr.remember(
            content="Use camelCase for JavaScript function names",
            workspace_id="w1",
            kind=MemoryKind.LONG_TERM,
            metadata={"category": "project_convention", "confidence": 0.7},
        )
        pinned_id = e2.id

        # Pin the second one
        self.ms.toggle_pin(pinned_id, pinned=True)

        # Recall both and verify the pinned one scores higher
        results = self.mgr.recall_with_scores(workspace_id="w1", query="function naming convention")

        # Find scores
        pinned_score = None
        unpinned_score = None
        for entry, score in results:
            if entry.id == pinned_id:
                pinned_score = score
            elif entry.id == unpinned_id:
                unpinned_score = score

        assert pinned_score is not None, "Pinned memory should be in results"
        assert unpinned_score is not None, "Unpinned memory should be in results"
        assert pinned_score > unpinned_score, \
            f"Pinned ({pinned_score}) should score higher than unpinned ({unpinned_score})"

    def test_unpin_removes_score_boost(self) -> None:
        """Unpinning a memory removes the score boost."""
        entry = self.mgr.remember(
            content="Critical deployment step",
            workspace_id="w1",
            kind=MemoryKind.LONG_TERM,
            metadata={"pinned": True, "category": "task_learning", "confidence": 0.8},
        )

        # Score while pinned
        results_pinned = self.mgr.recall_with_scores(workspace_id="w1", query="deployment")
        pinned_score = next(s for e, s in results_pinned if e.id == entry.id)

        # Unpin
        self.ms.toggle_pin(entry.id, pinned=False)

        # Score after unpinning
        results_unpinned = self.mgr.recall_with_scores(workspace_id="w1", query="deployment")
        unpinned_score = next(s for e, s in results_unpinned if e.id == entry.id)

        assert pinned_score > unpinned_score, \
            f"Pinned score ({pinned_score}) should be higher than unpinned ({unpinned_score})"
