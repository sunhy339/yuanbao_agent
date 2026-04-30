"""Tests for the ContextCompactor module."""

from __future__ import annotations

from typing import Any

import pytest

from local_agent_runtime.context.compactor import CompactionResult, ContextCompactor
from local_agent_runtime.context.token_budget import estimate_tokens
from local_agent_runtime.store.sqlite_store import SQLiteStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _FakeProvider:
    """Minimal provider stub that echoes a canned summary."""

    def __init__(self, response: str = "This is a summary.") -> None:
        self._response = response
        self.calls: list[dict[str, Any]] = []

    def generate(self, prompt: str, context: dict) -> dict:
        self.calls.append({"prompt": prompt, "context": context})
        return {"message": self._response}


def _msg(role: str, content: str) -> dict[str, str]:
    return {"role": role, "content": content}


def _long_content(n_chars: int) -> str:
    return "x" * n_chars


# ---------------------------------------------------------------------------
# Test: segment splitting
# ---------------------------------------------------------------------------

class TestSplitSegments:
    def setup_method(self) -> None:
        self.store = SQLiteStore(":memory:")
        self.compactor = ContextCompactor(self.store, recent_turns=4)

    def test_no_system_messages(self) -> None:
        msgs = [_msg("user", "hi"), _msg("assistant", "hello")]
        primers, history, recents = self.compactor._split_segments(msgs)
        assert primers == []
        assert history == []
        assert len(recents) == 2

    def test_leading_system_messages_are_primers(self) -> None:
        msgs = [
            _msg("system", "sys1"),
            _msg("system", "sys2"),
            _msg("user", "hi"),
            _msg("assistant", "hello"),
        ]
        primers, history, recents = self.compactor._split_segments(msgs)
        assert len(primers) == 2
        assert primers[0]["content"] == "sys1"
        assert primers[1]["content"] == "sys2"

    def test_recents_keeps_last_n_non_system(self) -> None:
        msgs = [_msg("system", "sys")] + [
            _msg("user", f"turn {i}") for i in range(10)
        ]
        primers, history, recents = self.compactor._split_segments(msgs)
        assert len(recents) == 4  # recent_turns=4
        assert recents[0]["content"] == "turn 6"
        assert recents[-1]["content"] == "turn 9"
        assert len(history) == 6

    def test_fewer_than_recent_turns(self) -> None:
        msgs = [_msg("user", "only one")]
        primers, history, recents = self.compactor._split_segments(msgs)
        assert history == []
        assert len(recents) == 1

    def test_system_only_in_middle_goes_to_history(self) -> None:
        msgs = [
            _msg("system", "sys"),
            _msg("user", "u1"),
            _msg("system", "mid-sys"),
            _msg("user", "u2"),
        ]
        primers, history, recents = self.compactor._split_segments(msgs)
        assert len(primers) == 1
        # mid-sys + u1 are in history (only 2 non-primer body msgs, 2 < 4 recent_turns)
        # so history is empty, recents has everything in body
        assert len(history) + len(recents) == 3  # u1 + mid-sys + u2


# ---------------------------------------------------------------------------
# Test: heuristic summary (no provider)
# ---------------------------------------------------------------------------

class TestHeuristicSummary:
    def setup_method(self) -> None:
        self.store = SQLiteStore(":memory:")
        self.compactor = ContextCompactor(self.store, recent_turns=2)

    def test_empty_history_returns_none(self) -> None:
        result = self.compactor._generate_summary([], 1000)
        assert result is None

    def test_short_history_returned_as_is(self) -> None:
        history = [_msg("user", "short")]
        result = self.compactor._generate_summary(history, 10000)
        assert result is not None
        assert "short" in result

    def test_long_history_is_truncated(self) -> None:
        history = [_msg("user", _long_content(10000))]
        result = self.compactor._generate_summary(history, 100)  # small budget
        assert result is not None
        assert "truncated" in result
        # Truncated result should be shorter than original
        original_len = len(_long_content(10000))
        assert len(result) < original_len


# ---------------------------------------------------------------------------
# Test: LLM summary (with provider)
# ---------------------------------------------------------------------------

class TestLLMSummary:
    def setup_method(self) -> None:
        self.store = SQLiteStore(":memory:")
        self.provider = _FakeProvider("Key decision: use SQLite.")
        self.compactor = ContextCompactor(
            self.store, provider=self.provider, recent_turns=2
        )

    def test_provider_is_called(self) -> None:
        history = [_msg("user", "What DB should we use?")]
        result = self.compactor._generate_summary(history, 1000)
        assert result is not None
        assert "SQLite" in result
        assert len(self.provider.calls) == 1

    def test_provider_exception_falls_back_to_heuristic(self) -> None:
        failing = _FakeProvider()
        failing.generate = lambda p, c: (_ for _ in ()).throw(RuntimeError("boom"))
        compactor = ContextCompactor(self.store, provider=failing, recent_turns=2)
        history = [_msg("user", "hello")]
        result = compactor._generate_summary(history, 10000)
        # Should fall back to heuristic and return something
        assert result is not None

    def test_provider_empty_response_falls_back(self) -> None:
        empty = _FakeProvider("")
        compactor = ContextCompactor(self.store, provider=empty, recent_turns=2)
        history = [_msg("user", "hello")]
        result = compactor._generate_summary(history, 10000)
        assert result is not None


# ---------------------------------------------------------------------------
# Test: compact() full flow
# ---------------------------------------------------------------------------

class TestCompact:
    def setup_method(self) -> None:
        self.store = SQLiteStore(":memory:")
        self.compactor = ContextCompactor(self.store, recent_turns=2)

    def test_no_compaction_when_under_budget(self) -> None:
        msgs = [_msg("user", "hi"), _msg("assistant", "hello")]
        result = self.compactor.compact("sess_1", msgs, max_tokens=10000)
        assert result.tokens_before == result.tokens_after
        assert result.summary is None
        assert result.compaction_id is None
        assert len(result.kept_messages) == 2

    def test_compaction_triggered_when_over_budget(self) -> None:
        msgs = [
            _msg("system", "sys prompt"),
            *[_msg("user", _long_content(200)) for _ in range(20)],
        ]
        # Budget small enough to trigger compaction
        result = self.compactor.compact("sess_2", msgs, max_tokens=200)
        assert result.tokens_before > result.tokens_after
        assert result.strategy == "primer_summary_recent"
        assert result.compaction_id is not None
        assert result.compaction_id.startswith("cmp_")

    def test_compaction_preserves_primers(self) -> None:
        msgs = [
            _msg("system", "you are a helpful assistant"),
            _msg("system", "use tool X"),
            *[_msg("user", _long_content(200)) for _ in range(20)],
        ]
        result = self.compactor.compact("sess_3", msgs, max_tokens=200)
        sys_msgs = [m for m in result.kept_messages if m["role"] == "system" and "you are" in m["content"]]
        assert len(sys_msgs) == 1

    def test_compaction_preserves_recents(self) -> None:
        msgs = [
            _msg("system", "sys"),
            *[_msg("user", f"turn {i}") for i in range(10)],
        ]
        result = self.compactor.compact("sess_4", msgs, max_tokens=50)
        # Last 2 turns should be preserved verbatim
        recent_contents = [m["content"] for m in result.kept_messages if m["role"] == "user"]
        assert "turn 9" in recent_contents
        assert "turn 8" in recent_contents

    def test_compaction_record_persisted(self) -> None:
        msgs = [_msg("system", "sys")] + [
            _msg("user", _long_content(200)) for _ in range(20)
        ]
        result = self.compactor.compact("sess_5", msgs, max_tokens=200)
        assert result.compaction_id is not None
        row = self.store._conn.execute(
            "SELECT id, session_id, tokens_before, tokens_after FROM compaction_records WHERE id = ?",
            (result.compaction_id,),
        ).fetchone()
        assert row is not None
        assert row["session_id"] == "sess_5"
        assert row["tokens_before"] > row["tokens_after"]

    def test_compaction_with_provider_summary(self) -> None:
        provider = _FakeProvider("Summary: we discussed caching strategies.")
        compactor = ContextCompactor(self.store, provider=provider, recent_turns=2)
        msgs = [_msg("system", "sys")] + [
            _msg("user", _long_content(200)) for _ in range(20)
        ]
        result = compactor.compact("sess_6", msgs, max_tokens=200)
        assert result.summary is not None
        assert "caching strategies" in result.summary


# ---------------------------------------------------------------------------
# Test: CompactionResult dataclass
# ---------------------------------------------------------------------------

class TestCompactionResult:
    def test_default_fields(self) -> None:
        r = CompactionResult(kept_messages=[], tokens_before=0, tokens_after=0)
        assert r.summary is None
        assert r.strategy == "primer_summary_recent"
        assert r.compaction_id is None

    def test_custom_fields(self) -> None:
        r = CompactionResult(
            kept_messages=[{"role": "user", "content": "hi"}],
            tokens_before=100,
            tokens_after=50,
            summary="short summary",
            strategy="primer_summary_recent",
            compaction_id="cmp_test",
        )
        assert r.tokens_before == 100
        assert r.summary == "short summary"


# ---------------------------------------------------------------------------
# Test: primer hashing
# ---------------------------------------------------------------------------

class TestPrimerHashing:
    def test_empty_primers(self) -> None:
        h = ContextCompactor._hash_primers([])
        assert isinstance(h, str)
        assert len(h) == 16

    def test_deterministic(self) -> None:
        primers = [_msg("system", "hello")]
        h1 = ContextCompactor._hash_primers(primers)
        h2 = ContextCompactor._hash_primers(primers)
        assert h1 == h2

    def test_different_content_different_hash(self) -> None:
        h1 = ContextCompactor._hash_primers([_msg("system", "aaa")])
        h2 = ContextCompactor._hash_primers([_msg("system", "bbb")])
        assert h1 != h2
