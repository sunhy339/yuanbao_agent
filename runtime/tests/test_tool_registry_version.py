"""Tests for ToolRegistry version tracking.

Covers:
  - Initial version = 0
  - register() increments version
  - unregister() increments version
  - unregister_prefix() increments version (when tools removed)
  - unregister_prefix() with no match does NOT increment
"""
from __future__ import annotations

from typing import Any

import pytest

from local_agent_runtime.tools.registry import ToolRegistry


class TestToolRegistryVersion:
    """Tests for ToolRegistry._version tracking."""

    def test_initial_version_is_zero(self) -> None:
        registry = ToolRegistry()
        assert registry.version == 0

    def test_register_increments(self) -> None:
        registry = ToolRegistry()
        registry.register("tool_a", lambda args: args)
        assert registry.version == 1

    def test_register_twice_increments_twice(self) -> None:
        registry = ToolRegistry()
        registry.register("tool_a", lambda args: args)
        registry.register("tool_b", lambda args: args)
        assert registry.version == 2

    def test_unregister_increments(self) -> None:
        registry = ToolRegistry()
        registry.register("tool_a", lambda args: args)
        assert registry.version == 1
        registry.unregister("tool_a")
        assert registry.version == 2

    def test_unregister_nonexistent_still_increments(self) -> None:
        registry = ToolRegistry()
        registry.unregister("nonexistent")
        assert registry.version == 1

    def test_unregister_prefix_increments_when_tools_removed(self) -> None:
        registry = ToolRegistry()
        registry.register("mcp__s1__tool_a", lambda args: args)
        registry.register("mcp__s1__tool_b", lambda args: args)
        registry.register("builtin_tool", lambda args: args)
        assert registry.version == 3

        count = registry.unregister_prefix("mcp__s1__")
        assert count == 2
        assert registry.version == 4

    def test_unregister_prefix_no_match_does_not_increment(self) -> None:
        registry = ToolRegistry()
        registry.register("tool_a", lambda args: args)
        assert registry.version == 1

        count = registry.unregister_prefix("nonexistent__")
        assert count == 0
        assert registry.version == 1  # no change

    def test_version_persists_across_operations(self) -> None:
        registry = ToolRegistry()
        registry.register("a", lambda args: args)   # v=1
        registry.register("b", lambda args: args)   # v=2
        registry.unregister("a")                     # v=3
        registry.unregister_prefix("x")             # no match, v=3
        registry.register("c", lambda args: args)   # v=4
        registry.unregister_prefix("c")             # v=5
        assert registry.version == 5
