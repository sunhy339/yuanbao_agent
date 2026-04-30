"""Tests for MemoryRetriever keyword search and extract_keywords."""

from __future__ import annotations

from local_agent_runtime.memory.retriever import MemoryRetriever, extract_keywords
from local_agent_runtime.memory.store import MemoryStore
from local_agent_runtime.memory.types import MemoryKind
from local_agent_runtime.store.sqlite_store import SQLiteStore


class TestExtractKeywords:
    def test_english_keywords(self) -> None:
        kw = extract_keywords("The quick brown fox jumps over the lazy dog")
        assert "quick" in kw
        assert "brown" in kw
        assert "fox" in kw
        assert "jumps" in kw
        # stop words removed
        assert "the" not in kw
        assert "lazy" in kw  # not a stop word

    def test_chinese_keywords(self) -> None:
        kw = extract_keywords("用户喜欢紧凑的界面设计")
        assert "用户" in kw
        assert "界面" in kw
        assert "设计" in kw
        # stop words removed
        assert "的" not in kw

    def test_mixed_chinese_english(self) -> None:
        kw = extract_keywords("Use pytest for Python testing")
        assert "pytest" in kw
        assert "python" in kw
        assert "testing" in kw

    def test_empty_string(self) -> None:
        assert extract_keywords("") == []

    def test_only_stop_words(self) -> None:
        kw = extract_keywords("the a an is it")
        assert kw == []


class TestMemoryRetrieverSearch:
    def setup_method(self) -> None:
        self.store = SQLiteStore(":memory:")
        self.ms = MemoryStore(self.store)
        self.retriever = MemoryRetriever(self.ms)

    def test_search_returns_relevant_entries(self) -> None:
        self.ms.create(
            kind=MemoryKind.SEMANTIC,
            content="User prefers dark mode for coding",
            workspace_id="w1",
            keywords=["dark", "mode", "coding"],
        )
        self.ms.create(
            kind=MemoryKind.SEMANTIC,
            content="User likes cats",
            workspace_id="w1",
            keywords=["cats", "pets"],
        )

        results = self.retriever.search("w1", "dark mode coding")
        assert len(results) >= 1
        entry, score = results[0]
        assert "dark mode" in entry.content
        assert score > 0.0

    def test_search_empty_query(self) -> None:
        self.ms.create(kind=MemoryKind.SEMANTIC, content="hello", workspace_id="w1")
        results = self.retriever.search("w1", "")
        assert results == []

    def test_search_no_match_above_threshold(self) -> None:
        self.ms.create(
            kind=MemoryKind.SEMANTIC,
            content="User likes cats",
            workspace_id="w1",
            keywords=["cats", "pets"],
        )
        results = self.retriever.search("w1", "quantum physics equations")
        assert results == []

    def test_search_respects_limit(self) -> None:
        for i in range(10):
            self.ms.create(
                kind=MemoryKind.SEMANTIC,
                content=f"Python testing with pytest variant {i}",
                workspace_id="w1",
                keywords=["pytest", "testing", "python"],
            )
        results = self.retriever.search("w1", "pytest testing", limit=3)
        assert len(results) <= 3

    def test_search_filters_by_kind(self) -> None:
        self.ms.create(
            kind=MemoryKind.WORKING,
            content="Working memory about Python",
            workspace_id="w1",
        )
        self.ms.create(
            kind=MemoryKind.SEMANTIC,
            content="Semantic memory about Python",
            workspace_id="w1",
        )
        results = self.retriever.search("w1", "Python", kind=MemoryKind.SEMANTIC)
        assert all(e.kind == MemoryKind.SEMANTIC for e, _ in results)

    def test_search_uses_keywords_for_matching(self) -> None:
        self.ms.create(
            kind=MemoryKind.SEMANTIC,
            content="Brief content",
            workspace_id="w1",
            keywords=["distributed systems architecture design"],
        )
        results = self.retriever.search("w1", "distributed systems architecture")
        assert len(results) >= 1

    def test_search_chinese_content(self) -> None:
        self.ms.create(
            kind=MemoryKind.SEMANTIC,
            content="用户偏好使用深色主题进行编程",
            workspace_id="w1",
            keywords=["深色", "主题", "编程"],
        )
        results = self.retriever.search("w1", "深色主题编程")
        assert len(results) >= 1
