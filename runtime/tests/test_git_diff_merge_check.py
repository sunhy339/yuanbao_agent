"""Tests for P6.6: root checks git diff before merging child results."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from local_agent_runtime.event_bus import EventBus
from local_agent_runtime.orchestrator.service import Orchestrator
from local_agent_runtime.store.sqlite_store import SQLiteStore
from local_agent_runtime.tools.registry import ToolRegistry


def _make_runtime(tmp_path: Any) -> SimpleNamespace:
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
    return SimpleNamespace(orchestrator=orchestrator, store=store, event_bus=event_bus)


class TestCheckGitDiffBeforeMerge:
    """Unit tests for _check_git_diff_before_merge."""

    def test_no_changes_returns_safe(self, tmp_path: Any) -> None:
        """No child changes → safe=True, empty lists."""
        runtime = _make_runtime(tmp_path)
        result = runtime.orchestrator._check_git_diff_before_merge(
            session_id="s1",
            task={"id": "t1"},
            execution={"subtasks": []},
        )
        assert result["safe"] is True
        assert result["changed_files"] == []
        assert result["warnings"] == []

    def test_single_child_change_is_safe(self, tmp_path: Any) -> None:
        """Single child changing files is safe (no overlap)."""
        runtime = _make_runtime(tmp_path)
        execution = {
            "subtasks": [
                SimpleNamespace(id="c1", changed_files=["src/foo.py"]),
            ],
        }
        result = runtime.orchestrator._check_git_diff_before_merge(
            session_id="s1",
            task={"id": "t1"},
            execution=execution,
        )
        assert result["safe"] is True
        assert "src/foo.py" in result["changed_files"]
        assert result["scope_overlaps"] == []

    def test_scope_overlap_detected(self, tmp_path: Any) -> None:
        """Two children modifying same file → overlap detected, safe=False."""
        runtime = _make_runtime(tmp_path)
        execution = {
            "subtasks": [
                SimpleNamespace(id="c1", changed_files=["src/bar.py"]),
                SimpleNamespace(id="c2", changed_files=["src/bar.py", "src/baz.py"]),
            ],
        }
        result = runtime.orchestrator._check_git_diff_before_merge(
            session_id="s1",
            task={"id": "t1"},
            execution=execution,
        )
        assert result["safe"] is False
        assert "src/bar.py" in result["scope_overlaps"]
        assert any("src/bar.py" in w for w in result["warnings"])

    def test_dict_style_subtasks(self, tmp_path: Any) -> None:
        """Subtasks as dicts (not SimpleNamespace) also work."""
        runtime = _make_runtime(tmp_path)
        execution = {
            "subtasks": [
                {"id": "c1", "changed_files": ["a.py"]},
                {"id": "c2", "changed_files": ["a.py"]},
            ],
        }
        result = runtime.orchestrator._check_git_diff_before_merge(
            session_id="s1",
            task={"id": "t1"},
            execution=execution,
        )
        assert result["safe"] is False
        assert "a.py" in result["scope_overlaps"]

    def test_json_string_changed_files(self, tmp_path: Any) -> None:
        """changed_files as JSON string is parsed correctly."""
        import json
        runtime = _make_runtime(tmp_path)
        execution = {
            "subtasks": [
                {"id": "c1", "changed_files": json.dumps(["file1.py", "file2.py"])},
            ],
        }
        result = runtime.orchestrator._check_git_diff_before_merge(
            session_id="s1",
            task={"id": "t1"},
            execution=execution,
        )
        assert result["safe"] is True
        assert "file1.py" in result["changed_files"]
        assert "file2.py" in result["changed_files"]

    def test_merge_check_event_published_on_warnings(self, tmp_path: Any) -> None:
        """When overlap detected, task.merge.check event is published."""
        runtime = _make_runtime(tmp_path)
        collected: list[Any] = []
        runtime.event_bus.subscribe(collected.append)

        runtime.orchestrator._check_git_diff_before_merge(
            session_id="s1",
            task={"id": "t1", "role": "root"},
            execution={
                "subtasks": [
                    SimpleNamespace(id="c1", changed_files=["x.py"]),
                    SimpleNamespace(id="c2", changed_files=["x.py"]),
                ],
            },
        )
        types = [e.type for e in collected]
        assert "task.merge.check" in types

    def test_no_event_when_safe(self, tmp_path: Any) -> None:
        """No event published when merge is safe."""
        runtime = _make_runtime(tmp_path)
        collected: list[Any] = []
        runtime.event_bus.subscribe(collected.append)

        runtime.orchestrator._check_git_diff_before_merge(
            session_id="s1",
            task={"id": "t1"},
            execution={"subtasks": []},
        )
        types = [e.type for e in collected]
        assert "task.merge.check" not in types

    def test_no_workspace_root_still_checks_scope(self, tmp_path: Any) -> None:
        """Without workspace_root, scope overlap check still works."""
        runtime = _make_runtime(tmp_path)
        execution = {
            "subtasks": [
                SimpleNamespace(id="c1", changed_files=["a.py", "b.py"]),
                SimpleNamespace(id="c2", changed_files=["b.py", "c.py"]),
            ],
        }
        result = runtime.orchestrator._check_git_diff_before_merge(
            session_id="s1",
            task={"id": "t1"},
            execution=execution,
            workspace_root=None,
        )
        assert result["safe"] is False
        assert "b.py" in result["scope_overlaps"]

    def test_nonexistent_workspace_root_graceful(self, tmp_path: Any) -> None:
        """Non-existent workspace_root doesn't crash."""
        runtime = _make_runtime(tmp_path)
        result = runtime.orchestrator._check_git_diff_before_merge(
            session_id="s1",
            task={"id": "t1"},
            execution={
                "subtasks": [
                    SimpleNamespace(id="c1", changed_files=["x.py"]),
                ],
            },
            workspace_root="/nonexistent/path",
        )
        # Should still work — just no git diff check
        assert result["safe"] is True
        assert "x.py" in result["changed_files"]
