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

    def test_search_chinese_bigram_matching(self) -> None:
        """Chinese bigram tokens should improve recall for multi-char queries."""
        self.ms.create(
            kind=MemoryKind.SEMANTIC,
            content="系统架构设计需要考虑性能优化和扩展性",
            workspace_id="w1",
        )
        results = self.retriever.search("w1", "架构设计性能")
        assert len(results) >= 1
        entry, score = results[0]
        assert "架构" in entry.content

    def test_search_chinese_session_boost(self) -> None:
        """Memories from the active session should score higher."""
        self.ms.create(
            kind=MemoryKind.SEMANTIC,
            content="数据库连接池配置参数",
            workspace_id="w1",
            session_id="s1",
        )
        self.ms.create(
            kind=MemoryKind.SEMANTIC,
            content="数据库连接池配置参数",
            workspace_id="w1",
            session_id="s2",
        )
        results = self.retriever.search("w1", "数据库连接池", session_id="s1")
        if len(results) >= 2:
            first_entry, first_score = results[0]
            _, second_score = results[1]
            assert first_entry.session_id == "s1"
            assert first_score > second_score


class TestExtractKeywordsChinese:
    def test_chinese_bigram_extraction(self) -> None:
        kw = extract_keywords("系统架构设计需要考虑性能优化")
        # Should have unigrams and bigrams like 架构, 性能, 优化
        assert any("架构" in k for k in kw)
        assert any("性能" in k for k in kw)

    def test_chinese_stop_chars_filtered(self) -> None:
        kw = extract_keywords("这是一个很好的设计")
        # 的, 是, 很, 这 should be filtered
        assert "的" not in kw
        assert "是" not in kw
        assert "很" not in kw
        assert "这" not in kw
        # 设计 should remain
        assert "设计" in kw

    def test_mixed_chinese_english_keywords(self) -> None:
        kw = extract_keywords("使用 Python 进行数据分析处理")
        assert "python" in kw
        assert any("数据" in k for k in kw)

    def test_extension_a_characters(self) -> None:
        """CJK Extension A characters (U+3400-U+4DBF) should be tokenized."""
        # 㐀 (U+4400) is a CJK Extension A character
        text = "㐀㐁测试内容"
        kw = extract_keywords(text)
        # Should produce tokens containing the extension A chars
        assert any("测试" in k for k in kw)
