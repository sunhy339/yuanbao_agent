"""Tests for scratchpad.write and scratchpad.read tools."""

from __future__ import annotations

from local_agent_runtime.context.scratchpad import Scratchpad
from local_agent_runtime.store.sqlite_store import SQLiteStore
from local_agent_runtime.tools.scratchpad_tool import (
    build_scratchpad_read_tool,
    build_scratchpad_write_tool,
)


class TestScratchpadWriteTool:
    def setup_method(self) -> None:
        self.store = SQLiteStore(":memory:")
        self.scratchpad = Scratchpad(self.store)
        self.tool = build_scratchpad_write_tool(self.scratchpad)["handler"]

    def test_write_basic(self) -> None:
        result = self.tool({"key": "hypothesis", "value": "test value", "sessionId": "s1"})
        assert result["status"] == "ok"
        assert result["key"] == "hypothesis"
        assert result["id"].startswith("sp")

    def test_write_upsert(self) -> None:
        self.tool({"key": "plan", "value": "v1", "sessionId": "s1"})
        result = self.tool({"key": "plan", "value": "v2", "sessionId": "s1"})
        assert result["status"] == "ok"
        read_tool = build_scratchpad_read_tool(self.scratchpad)["handler"]
        read_result = read_tool({"key": "plan", "sessionId": "s1"})
        assert read_result["value"] == "v2"

    def test_write_empty_key_raises(self) -> None:
        import pytest
        with pytest.raises(ValueError, match="key is required"):
            self.tool({"key": "", "value": "x", "sessionId": "s1"})

    def test_write_missing_session_raises(self) -> None:
        import pytest
        with pytest.raises(ValueError, match="sessionId is required"):
            self.tool({"key": "k", "value": "v"})


class TestScratchpadReadTool:
    def setup_method(self) -> None:
        self.store = SQLiteStore(":memory:")
        self.scratchpad = Scratchpad(self.store)
        self.write_tool = build_scratchpad_write_tool(self.scratchpad)["handler"]
        self.read_tool = build_scratchpad_read_tool(self.scratchpad)["handler"]

    def test_read_existing(self) -> None:
        self.write_tool({"key": "hypothesis", "value": "maybe a bug", "sessionId": "s1"})
        result = self.read_tool({"key": "hypothesis", "sessionId": "s1"})
        assert result["status"] == "ok"
        assert result["value"] == "maybe a bug"

    def test_read_not_found(self) -> None:
        result = self.read_tool({"key": "nonexistent", "sessionId": "s1"})
        assert result["status"] == "not_found"

    def test_read_empty_key_raises(self) -> None:
        import pytest
        with pytest.raises(ValueError, match="key is required"):
            self.read_tool({"key": "", "sessionId": "s1"})

    def test_read_missing_session_raises(self) -> None:
        import pytest
        with pytest.raises(ValueError, match="sessionId is required"):
            self.read_tool({"key": "k"})
