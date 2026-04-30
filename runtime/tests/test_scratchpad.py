"""Tests for the Scratchpad module."""

from __future__ import annotations

from local_agent_runtime.context.scratchpad import Scratchpad, ScratchpadEntry
from local_agent_runtime.store.sqlite_store import SQLiteStore


class TestScratchpadWriteRead:
    """Write and read back entries."""

    def setup_method(self) -> None:
        self.store = SQLiteStore(":memory:")
        self.pad = Scratchpad(self.store)
        self.sid = "sess_abc"

    def test_write_and_read(self) -> None:
        entry = self.pad.write(self.sid, "hypothesis", "the bug is in parser.py")
        assert entry is not None
        assert entry.key == "hypothesis"
        assert entry.value == "the bug is in parser.py"
        assert entry.session_id == self.sid

    def test_read_nonexistent_returns_none(self) -> None:
        assert self.pad.read(self.sid, "nope") is None

    def test_write_upserts(self) -> None:
        self.pad.write(self.sid, "plan", "step 1")
        self.pad.write(self.sid, "plan", "step 1 + step 2")
        entry = self.pad.read(self.sid, "plan")
        assert entry is not None
        assert entry.value == "step 1 + step 2"

    def test_updated_at_changes_on_upsert(self) -> None:
        first = self.pad.write(self.sid, "k", "v1")
        second = self.pad.write(self.sid, "k", "v2")
        assert second is not None
        assert second.updated_at >= first.updated_at


class TestScratchpadList:
    """List keys and entries."""

    def setup_method(self) -> None:
        self.store = SQLiteStore(":memory:")
        self.pad = Scratchpad(self.store)
        self.sid = "sess_list"

    def test_list_keys_empty(self) -> None:
        assert self.pad.list_keys(self.sid) == []

    def test_list_keys_returns_all(self) -> None:
        self.pad.write(self.sid, "a", "1")
        self.pad.write(self.sid, "b", "2")
        self.pad.write(self.sid, "c", "3")
        keys = self.pad.list_keys(self.sid)
        assert set(keys) == {"a", "b", "c"}

    def test_list_keys_ordered_by_updated_at(self) -> None:
        self.pad.write(self.sid, "first", "1")
        self.pad.write(self.sid, "second", "2")
        # Update 'first' to make it most recent
        self.pad.write(self.sid, "first", "1-updated")
        keys = self.pad.list_keys(self.sid)
        assert keys[0] == "first"

    def test_list_entries_returns_structured(self) -> None:
        self.pad.write(self.sid, "x", "val_x")
        entries = self.pad.list_entries(self.sid)
        assert len(entries) == 1
        assert isinstance(entries[0], ScratchpadEntry)
        assert entries[0].key == "x"

    def test_session_isolation(self) -> None:
        self.pad.write("sess_a", "k", "val_a")
        self.pad.write("sess_b", "k", "val_b")
        assert self.pad.read("sess_a", "k").value == "val_a"  # type: ignore[union-attr]
        assert self.pad.read("sess_b", "k").value == "val_b"  # type: ignore[union-attr]
        assert self.pad.list_keys("sess_a") == ["k"]
        assert self.pad.list_keys("sess_b") == ["k"]


class TestScratchpadDelete:
    """Delete and clear entries."""

    def setup_method(self) -> None:
        self.store = SQLiteStore(":memory:")
        self.pad = Scratchpad(self.store)
        self.sid = "sess_del"

    def test_delete_existing(self) -> None:
        self.pad.write(self.sid, "k", "v")
        assert self.pad.delete(self.sid, "k") is True
        assert self.pad.read(self.sid, "k") is None

    def test_delete_nonexistent(self) -> None:
        assert self.pad.delete(self.sid, "nope") is False

    def test_clear_returns_count(self) -> None:
        self.pad.write(self.sid, "a", "1")
        self.pad.write(self.sid, "b", "2")
        self.pad.write(self.sid, "c", "3")
        count = self.pad.clear(self.sid)
        assert count == 3
        assert self.pad.list_keys(self.sid) == []

    def test_clear_only_affects_target_session(self) -> None:
        self.pad.write("s1", "k", "v1")
        self.pad.write("s2", "k", "v2")
        self.pad.clear("s1")
        assert self.pad.read("s2", "k") is not None
