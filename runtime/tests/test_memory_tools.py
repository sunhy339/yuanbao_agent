"""Tests for memory.remember and memory.recall tools."""

from __future__ import annotations

from local_agent_runtime.memory.manager import MemoryManager
from local_agent_runtime.memory.retriever import MemoryRetriever
from local_agent_runtime.memory.store import MemoryStore
from local_agent_runtime.memory.types import MemoryKind
from local_agent_runtime.store.sqlite_store import SQLiteStore
from local_agent_runtime.tools.memory import (
    build_memory_recall_tool,
    build_memory_remember_tool,
)


class TestMemoryRememberTool:
    def setup_method(self) -> None:
        self.store = SQLiteStore(":memory:")
        self.ms = MemoryStore(self.store)
        self.retriever = MemoryRetriever(self.ms)
        self.mgr = MemoryManager(self.ms, self.retriever)
        self.tool = build_memory_remember_tool(self.mgr)["handler"]

    def test_remember_basic(self) -> None:
        result = self.tool({"content": "User prefers dark mode"})
        assert result["status"] == "ok"
        assert result["id"].startswith("mem_")
        assert result["kind"] == "working"

    def test_remember_with_kind(self) -> None:
        result = self.tool({"content": "Persistent fact", "kind": "long_term"})
        assert result["status"] == "ok"
        assert result["kind"] == "long_term"

    def test_remember_auto_extracts_keywords(self) -> None:
        result = self.tool({"content": "Use pytest for testing"})
        assert "pytest" in result["keywords"]
        assert "testing" in result["keywords"]

    def test_remember_with_session_workspace(self) -> None:
        result = self.tool({
            "content": "Session note",
            "sessionId": "s1",
            "workspaceId": "w1",
        })
        assert result["status"] == "ok"

    def test_remember_empty_content_raises(self) -> None:
        import pytest
        with pytest.raises(ValueError, match="content is required"):
            self.tool({"content": ""})

    def test_remember_missing_content_raises(self) -> None:
        import pytest
        with pytest.raises(ValueError, match="content is required"):
            self.tool({})


class TestMemoryRecallTool:
    def setup_method(self) -> None:
        self.store = SQLiteStore(":memory:")
        self.ms = MemoryStore(self.store)
        self.retriever = MemoryRetriever(self.ms)
        self.mgr = MemoryManager(self.ms, self.retriever)
        self.remember_tool = build_memory_remember_tool(self.mgr)["handler"]
        self.recall_tool = build_memory_recall_tool(self.mgr)["handler"]

    def test_recall_returns_relevant(self) -> None:
        self.remember_tool({
            "content": "User prefers dark mode for coding",
            "workspaceId": "w1",
            "kind": "semantic",
        })
        self.remember_tool({
            "content": "User likes cats",
            "workspaceId": "w1",
            "kind": "semantic",
        })
        result = self.recall_tool({"query": "dark mode coding", "workspaceId": "w1"})
        assert result["status"] == "ok"
        assert result["count"] >= 1
        assert any("dark mode" in m["content"] for m in result["memories"])

    def test_recall_respects_limit(self) -> None:
        for i in range(10):
            self.remember_tool({
                "content": f"Python testing note {i}",
                "workspaceId": "w1",
                "kind": "semantic",
            })
        result = self.recall_tool({"query": "Python testing", "limit": 3, "workspaceId": "w1"})
        assert result["count"] <= 3

    def test_recall_empty_query_raises(self) -> None:
        import pytest
        with pytest.raises(ValueError, match="query is required"):
            self.recall_tool({"query": ""})

    def test_recall_missing_query_raises(self) -> None:
        import pytest
        with pytest.raises(ValueError, match="query is required"):
            self.recall_tool({})
