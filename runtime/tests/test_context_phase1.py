"""Tests for Phase 1 integration: Scratchpad + Compactor + JIT injection in ContextBuilder."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from local_agent_runtime.context.builder import ContextBuilder
from local_agent_runtime.context.compactor import ContextCompactor
from local_agent_runtime.context.scratchpad import Scratchpad
from local_agent_runtime.store.sqlite_store import SQLiteStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _FakeProvider:
    def __init__(self, response: str = "Summary of prior discussion.") -> None:
        self._response = response

    def generate(self, prompt: str, context: dict) -> dict:
        return {"message": self._response}


def _setup_session(
    store: SQLiteStore,
    workspace_root: Path,
    *,
    title: str = "Phase 1 test",
) -> dict[str, Any]:
    workspace_root.mkdir(parents=True, exist_ok=True)
    workspace = store.upsert_workspace(str(workspace_root))
    session = store.create_session(workspace_id=workspace["id"], title=title)
    return {
        "workspace": workspace,
        "session": session,
        "session_id": session["id"],
    }


def _message_text(context: dict[str, Any]) -> str:
    return "\n".join(str(m["content"]) for m in context["messages"])


@pytest.fixture
def store(tmp_path: Path) -> SQLiteStore:
    db_path = tmp_path / "runtime.sqlite3"
    s = SQLiteStore(str(db_path))
    try:
        yield s
    finally:
        s.close()


# ---------------------------------------------------------------------------
# Scratchpad integration in ContextBuilder
# ---------------------------------------------------------------------------

class TestScratchpadInBuilder:
    def test_scratchpad_entries_appear_in_messages(
        self, store: SQLiteStore, tmp_path: Path
    ) -> None:
        setup = _setup_session(store, tmp_path / "ws")
        pad = Scratchpad(store)
        pad.write(setup["session_id"], "hypothesis", "Bug is in parser.py line 42")
        pad.write(setup["session_id"], "plan_step", "1. Read parser.py 2. Fix regex")

        builder = ContextBuilder(store, scratchpad=pad)
        context = builder.build(session_id=setup["session_id"], goal="Fix the parser bug")

        text = _message_text(context)
        assert "Scratchpad" in text
        assert "hypothesis" in text
        assert "Bug is in parser.py" in text
        assert "plan_step" in text

    def test_scratchpad_empty_produces_no_section(
        self, store: SQLiteStore, tmp_path: Path
    ) -> None:
        setup = _setup_session(store, tmp_path / "ws")
        pad = Scratchpad(store)

        builder = ContextBuilder(store, scratchpad=pad)
        context = builder.build(session_id=setup["session_id"], goal="Hello")

        text = _message_text(context)
        assert "Scratchpad" not in text

    def test_no_scratchpad_no_section(
        self, store: SQLiteStore, tmp_path: Path
    ) -> None:
        setup = _setup_session(store, tmp_path / "ws")
        builder = ContextBuilder(store)
        context = builder.build(session_id=setup["session_id"], goal="Hello")
        text = _message_text(context)
        assert "Scratchpad" not in text

    def test_scratchpad_respects_session_isolation(
        self, store: SQLiteStore, tmp_path: Path
    ) -> None:
        setup_a = _setup_session(store, tmp_path / "ws_a", title="Session A")
        setup_b = _setup_session(store, tmp_path / "ws_b", title="Session B")
        pad = Scratchpad(store)
        pad.write(setup_a["session_id"], "shared_key", "value for A")
        pad.write(setup_b["session_id"], "shared_key", "value for B")

        builder = ContextBuilder(store, scratchpad=pad)
        ctx_a = builder.build(session_id=setup_a["session_id"], goal="Work")
        ctx_b = builder.build(session_id=setup_b["session_id"], goal="Work")

        text_a = _message_text(ctx_a)
        text_b = _message_text(ctx_b)
        assert "value for A" in text_a
        assert "value for B" in text_b
        assert "value for B" not in text_a
        assert "value for A" not in text_b


# ---------------------------------------------------------------------------
# Compactor integration in ContextBuilder
# ---------------------------------------------------------------------------

class TestCompactorInBuilder:
    def test_compactor_available_in_builder(
        self, store: SQLiteStore, tmp_path: Path
    ) -> None:
        """Compactor is wired in — budget handles truncation first, compactor is safety net."""
        setup = _setup_session(store, tmp_path / "ws")
        store.update_config({"config": {"provider": {"maxContextTokens": 500}}})
        for i in range(10):
            store.create_message(
                session_id=setup["session_id"],
                role="user",
                content=f"Message number {i} with padding " * 10,
            )

        compactor = ContextCompactor(store)
        builder = ContextBuilder(store, compactor=compactor)
        context = builder.build(
            session_id=setup["session_id"],
            goal="Continue the work",
            lightweight=False,
        )

        # Budget trimming handles it, but compactor is wired in and builder completes
        assert len(context["messages"]) >= 2
        # Messages were trimmed to fit (some history dropped)
        text = _message_text(context)
        assert "Continue the work" in text

    def test_no_compactor_means_no_compression(
        self, store: SQLiteStore, tmp_path: Path
    ) -> None:
        setup = _setup_session(store, tmp_path / "ws")
        store.update_config({"config": {"provider": {"maxContextTokens": 150}}})
        for i in range(10):
            store.create_message(
                session_id=setup["session_id"],
                role="user",
                content=f"Long message {i} " * 20,
            )

        builder = ContextBuilder(store)  # no compactor
        context = builder.build(
            session_id=setup["session_id"],
            goal="Work",
            lightweight=False,
        )
        # Without compactor, messages are built normally (budget still trims sections)
        assert len(context["messages"]) >= 1


# ---------------------------------------------------------------------------
# JIT context injection
# ---------------------------------------------------------------------------

class TestJITInjection:
    def test_inject_context_with_matching_trigger(
        self, store: SQLiteStore, tmp_path: Path
    ) -> None:
        setup = _setup_session(store, tmp_path / "ws")
        pad = Scratchpad(store)
        # Write a JIT-related scratchpad entry
        pad.write(setup["session_id"], "jit_debug_logs", "Error traceback found in app.py")
        builder = ContextBuilder(store, scratchpad=pad)
        context = builder.build(session_id=setup["session_id"], goal="debug the crash")

        # Inject based on "debug" trigger
        injected = builder.inject_context(context, trigger="debug")
        messages = injected["messages"]
        # Should have added a system message with the JIT section
        jit_msgs = [m for m in messages if "[debug_logs]" in m.get("content", "")]
        assert len(jit_msgs) >= 1
        assert "jit_debug_logs" in jit_msgs[0]["content"]
        assert "Error traceback" in jit_msgs[0]["content"]

    def test_inject_context_no_matching_trigger(
        self, store: SQLiteStore, tmp_path: Path
    ) -> None:
        setup = _setup_session(store, tmp_path / "ws")
        builder = ContextBuilder(store)
        context = builder.build(session_id=setup["session_id"], goal="Hello")
        original_msg_count = len(context["messages"])

        injected = builder.inject_context(context, trigger="unknown_trigger_xyz")
        assert len(injected["messages"]) == original_msg_count

    def test_inject_context_no_scratchpad(
        self, store: SQLiteStore, tmp_path: Path
    ) -> None:
        setup = _setup_session(store, tmp_path / "ws")
        builder = ContextBuilder(store)  # no scratchpad
        context = builder.build(session_id=setup["session_id"], goal="Hello")
        original_msg_count = len(context["messages"])

        injected = builder.inject_context(context, trigger="debug")
        # No scratchpad means no JIT content to inject
        assert len(injected["messages"]) == original_msg_count

    def test_inject_context_chinese_trigger(
        self, store: SQLiteStore, tmp_path: Path
    ) -> None:
        setup = _setup_session(store, tmp_path / "ws")
        pad = Scratchpad(store)
        pad.write(setup["session_id"], "jit_testing_patterns", "Use pytest fixtures")
        builder = ContextBuilder(store, scratchpad=pad)
        context = builder.build(session_id=setup["session_id"], goal="Write tests")

        injected = builder.inject_context(context, trigger="测试")
        jit_msgs = [m for m in injected["messages"] if "[testing_patterns]" in m.get("content", "")]
        assert len(jit_msgs) >= 1

    def test_inject_context_system_msg_inserted_before_last_user(
        self, store: SQLiteStore, tmp_path: Path
    ) -> None:
        setup = _setup_session(store, tmp_path / "ws")
        pad = Scratchpad(store)
        pad.write(setup["session_id"], "jit_security_policy", "Always validate inputs")
        builder = ContextBuilder(store, scratchpad=pad)
        context = builder.build(session_id=setup["session_id"], goal="secure endpoint")

        injected = builder.inject_context(context, trigger="security")
        msgs = injected["messages"]
        # Find the JIT message
        jit_idx = next(
            i for i, m in enumerate(msgs) if "[security_policy]" in m.get("content", "")
        )
        # Find the last user message
        user_indices = [i for i, m in enumerate(msgs) if m.get("role") == "user"]
        if user_indices:
            assert jit_idx < user_indices[-1]


# ---------------------------------------------------------------------------
# Combined Scratchpad + Compactor
# ---------------------------------------------------------------------------

class TestCombinedPhase1:
    def test_scratchpad_and_compactor_work_together(
        self, store: SQLiteStore, tmp_path: Path
    ) -> None:
        setup = _setup_session(store, tmp_path / "ws")
        pad = Scratchpad(store)
        compactor = ContextCompactor(store)

        pad.write(setup["session_id"], "current_plan", "Step 1: read files, Step 2: fix bug")
        pad.write(setup["session_id"], "hypothesis", "Null pointer in handle_request")

        store.update_config({"config": {"provider": {"maxContextTokens": 2000}}})
        for i in range(20):
            store.create_message(
                session_id=setup["session_id"],
                role="user",
                content=f"Historical message {i} with enough content to fill the budget " * 3,
            )

        builder = ContextBuilder(store, compactor=compactor, scratchpad=pad, tool_schemas=[])
        context = builder.build(
            session_id=setup["session_id"],
            goal="Fix the null pointer",
            lightweight=False,
        )

        assert len(context["messages"]) >= 2
        # Scratchpad entries should be present
        text = _message_text(context)
        assert "current_plan" in text

    def test_scratchpad_survives_compaction(
        self, store: SQLiteStore, tmp_path: Path
    ) -> None:
        """Scratchpad entries should appear even when compaction happens."""
        setup = _setup_session(store, tmp_path / "ws")
        pad = Scratchpad(store)
        compactor = ContextCompactor(store)
        pad.write(setup["session_id"], "key_fact", "The API key is stored in env vars")

        builder = ContextBuilder(store, compactor=compactor, scratchpad=pad)
        context = builder.build(
            session_id=setup["session_id"],
            goal="Access the API",
        )

        text = _message_text(context)
        assert "key_fact" in text
        assert "env vars" in text
