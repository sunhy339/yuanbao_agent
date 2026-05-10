"""Tests for P6.4: child workers cannot directly git commit/push."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from local_agent_runtime.event_bus import EventBus
from local_agent_runtime.orchestrator.service import Orchestrator
from local_agent_runtime.store.sqlite_store import SQLiteStore
from local_agent_runtime.tools.registry import ToolRegistry


def _make_runtime(tmp_path: Any, *, child_allowlist: list[str] | None = None) -> SimpleNamespace:
    event_bus = EventBus()
    store = SQLiteStore(str(tmp_path / "runtime.sqlite3"))
    store.set_feature_flag("multiAgent", True)
    tool_registry = ToolRegistry()

    class DummyProvider:
        def generate(self, prompt: str, context: dict[str, Any]) -> dict[str, Any]:
            return {"text": "ok", "status": "done"}

    orchestrator = Orchestrator(
        store=store, event_bus=event_bus,
        tool_registry=tool_registry, provider=DummyProvider(),
    )
    if child_allowlist is not None:
        import os
        os.environ["LOCAL_AGENT_CHILD_TOOL_ALLOWLIST"] = ",".join(child_allowlist)
    return SimpleNamespace(orchestrator=orchestrator, store=store, event_bus=event_bus)


def _cleanup_allowlist_env() -> None:
    import os
    os.environ.pop("LOCAL_AGENT_CHILD_TOOL_ALLOWLIST", None)


class TestChildWorkerCommitGuard:
    """Child workers (with tool allowlist set) cannot run git commit/push."""

    def test_child_git_commit_blocked(self, tmp_path: Any) -> None:
        """Child worker running 'git commit' via run_command raises ValueError."""
        try:
            runtime = _make_runtime(tmp_path, child_allowlist=["read_file", "run_command"])
            with pytest.raises(ValueError, match="Child workers cannot"):
                runtime.orchestrator._ensure_command_safe_for_child_worker("git commit -m 'wip'")
        finally:
            _cleanup_allowlist_env()

    def test_child_git_push_blocked(self, tmp_path: Any) -> None:
        """Child worker running 'git push' via run_command raises ValueError."""
        try:
            runtime = _make_runtime(tmp_path, child_allowlist=["read_file", "run_command"])
            with pytest.raises(ValueError, match="Child workers cannot"):
                runtime.orchestrator._ensure_command_safe_for_child_worker("git push origin main")
        finally:
            _cleanup_allowlist_env()

    def test_child_git_status_allowed(self, tmp_path: Any) -> None:
        """Child worker can run 'git status' without restriction."""
        try:
            runtime = _make_runtime(tmp_path, child_allowlist=["read_file", "run_command"])
            # Should not raise
            runtime.orchestrator._ensure_command_safe_for_child_worker("git status")
        finally:
            _cleanup_allowlist_env()

    def test_child_git_diff_allowed(self, tmp_path: Any) -> None:
        """Child worker can run 'git diff' without restriction."""
        try:
            runtime = _make_runtime(tmp_path, child_allowlist=["read_file", "run_command"])
            runtime.orchestrator._ensure_command_safe_for_child_worker("git diff HEAD~1")
        finally:
            _cleanup_allowlist_env()

    def test_root_task_no_restriction(self, tmp_path: Any) -> None:
        """Root task (no allowlist) can run any command."""
        runtime = _make_runtime(tmp_path)
        # Should not raise - root task has no child allowlist
        runtime.orchestrator._ensure_command_safe_for_child_worker("git commit -m 'release'")

    def test_case_insensitive_commit(self, tmp_path: Any) -> None:
        """git COMMIT is also blocked (case-insensitive)."""
        try:
            runtime = _make_runtime(tmp_path, child_allowlist=["run_command"])
            with pytest.raises(ValueError, match="Child workers cannot"):
                runtime.orchestrator._ensure_command_safe_for_child_worker("git COMMIT -m 'test'")
        finally:
            _cleanup_allowlist_env()

    def test_multiline_command_with_commit(self, tmp_path: Any) -> None:
        """git commit embedded in a multi-command string is also blocked."""
        try:
            runtime = _make_runtime(tmp_path, child_allowlist=["run_command"])
            with pytest.raises(ValueError, match="Child workers cannot"):
                runtime.orchestrator._ensure_command_safe_for_child_worker(
                    "echo hello && git commit -m 'sneaky'"
                )
        finally:
            _cleanup_allowlist_env()

    def test_execute_tool_blocks_commit_for_child(self, tmp_path: Any) -> None:
        """Full _execute_tool flow blocks git commit for child workers."""
        try:
            runtime = _make_runtime(tmp_path, child_allowlist=["run_command"])
            tool_spec = {
                "name": "run_command",
                "id": "tc_1",
                "arguments": {"command": "git commit -m 'wip'"},
                "start_token": "<tool_start>",
            }
            task = {"id": "t1", "role": "worker", "status": "running"}
            with pytest.raises(ValueError, match="Child workers cannot"):
                runtime.orchestrator._execute_tool(
                    session_id="s1",
                    task=task,
                    tool_spec=tool_spec,
                )
        finally:
            _cleanup_allowlist_env()
