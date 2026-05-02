"""Tests for tool rate-limit enforcement in ToolRegistry."""

from __future__ import annotations

import pytest

from local_agent_runtime.tools.registry import ToolRateLimitError, ToolRegistry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DUMMY_HANDLER = lambda params: {"status": "ok", "result": "done"}  # noqa: E731


def _make_registry() -> ToolRegistry:
    """Create a registry with two tools: one rate-limited, one unlimited."""
    reg = ToolRegistry()

    reg.register(
        name="limited_tool",
        schema={
            "name": "limited_tool",
            "description": "A tool with rate limit",
            "parameters": {"type": "object", "properties": {}},
            "metadata": {"rate_limit": 2},
        },
        handler=_DUMMY_HANDLER,
    )

    reg.register(
        name="unlimited_tool",
        schema={
            "name": "unlimited_tool",
            "description": "A tool without rate limit",
            "parameters": {"type": "object", "properties": {}},
            "metadata": {"rate_limit": None},
        },
        handler=_DUMMY_HANDLER,
    )

    return reg


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestToolRateLimit:
    def test_under_limit_executes_normally(self) -> None:
        """Calls under the rate limit should succeed."""
        reg = _make_registry()

        result = reg.execute("limited_tool", {}, session_id="s1")
        assert result["status"] == "ok"

        result = reg.execute("limited_tool", {}, session_id="s1")
        assert result["status"] == "ok"

    def test_over_limit_raises_error(self) -> None:
        """Exceeding the rate limit should raise ToolRateLimitError."""
        reg = _make_registry()

        reg.execute("limited_tool", {}, session_id="s1")
        reg.execute("limited_tool", {}, session_id="s1")

        with pytest.raises(ToolRateLimitError, match="limited_tool"):
            reg.execute("limited_tool", {}, session_id="s1")

    def test_unlimited_tool_not_restricted(self) -> None:
        """Tools with rate_limit=None should never be restricted."""
        reg = _make_registry()

        for _ in range(20):
            result = reg.execute("unlimited_tool", {}, session_id="s1")
            assert result["status"] == "ok"

    def test_different_sessions_independent(self) -> None:
        """Rate limits should be tracked per session independently."""
        reg = _make_registry()

        # Exhaust session s1
        reg.execute("limited_tool", {}, session_id="s1")
        reg.execute("limited_tool", {}, session_id="s1")

        # s2 should still have its own quota
        result = reg.execute("limited_tool", {}, session_id="s2")
        assert result["status"] == "ok"

    def test_reset_session_clears_counts(self) -> None:
        """reset_session should allow the session to use tools again."""
        reg = _make_registry()

        reg.execute("limited_tool", {}, session_id="s1")
        reg.execute("limited_tool", {}, session_id="s1")

        reg.reset_session("s1")

        # After reset, the tool should be callable again
        result = reg.execute("limited_tool", {}, session_id="s1")
        assert result["status"] == "ok"

    def test_no_session_id_skips_rate_limit(self) -> None:
        """Without a session_id, rate limiting should not apply."""
        reg = _make_registry()

        for _ in range(10):
            result = reg.execute("limited_tool", {})
            assert result["status"] == "ok"

    def test_check_rate_limit_method(self) -> None:
        """check_rate_limit should return bool without side effects."""
        reg = _make_registry()

        assert reg.check_rate_limit("limited_tool", "s1") is True
        reg.execute("limited_tool", {}, session_id="s1")
        assert reg.check_rate_limit("limited_tool", "s1") is True
        reg.execute("limited_tool", {}, session_id="s1")
        assert reg.check_rate_limit("limited_tool", "s1") is False
