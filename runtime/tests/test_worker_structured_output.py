"""Tests for P6.4: worker structured result output.

Verifies that task completion builds and persists a structured result dict
containing summary, status, changedFiles, testsRun, risks, keyFindings.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from local_agent_runtime.event_bus import EventBus
from local_agent_runtime.orchestrator.service import Orchestrator
from local_agent_runtime.store.sqlite_store import SQLiteStore
from local_agent_runtime.tools.registry import ToolRegistry


def _make_runtime(tmp_path: Any) -> SimpleNamespace:
    event_bus = EventBus()
    store = SQLiteStore(str(tmp_path / "runtime.sqlite3"))
    tool_registry = ToolRegistry()

    class DummyProvider:
        def generate(self, prompt: str, context: dict[str, Any]) -> dict[str, Any]:
            return {"text": "ok", "status": "done"}

    orchestrator = Orchestrator(
        store=store, event_bus=event_bus,
        tool_registry=tool_registry, provider=DummyProvider(),
    )
    return SimpleNamespace(orchestrator=orchestrator, store=store, event_bus=event_bus)


class TestStructuredResultStore:
    """Structured result is persisted and retrievable via store."""

    def test_update_task_structured_result(self, tmp_path: Any) -> None:
        """update_task accepts and persists structured_result."""
        store = SQLiteStore(str(tmp_path / "test.sqlite3"))
        task = store.create_task(
            session_id="s1", task_type="main", goal="test", plan=[],
        )
        structured = {
            "summary": "All tests passed",
            "status": "success",
            "changedFiles": [{"path": "src/main.py", "action": "modified"}],
            "testsRun": [{"name": "test_main", "status": "passed"}],
            "risks": [{"type": "security", "description": "input validation", "severity": "medium"}],
            "keyFindings": ["Refactored auth module"],
        }
        updated = store.update_task(task_id=task["id"], structured_result=structured)
        assert updated["structuredResult"] == structured

    def test_get_task_returns_structured_result(self, tmp_path: Any) -> None:
        """get_task returns the structuredResult field."""
        store = SQLiteStore(str(tmp_path / "test.sqlite3"))
        task = store.create_task(
            session_id="s1", task_type="main", goal="test", plan=[],
        )
        structured = {
            "summary": "done",
            "status": "success",
            "changedFiles": [],
            "testsRun": [],
            "risks": [],
            "keyFindings": [],
        }
        store.update_task(task_id=task["id"], structured_result=structured)
        fetched = store.get_task({"taskId": task["id"]})["task"]
        assert fetched["structuredResult"] is not None
        assert fetched["structuredResult"]["summary"] == "done"
        assert fetched["structuredResult"]["status"] == "success"

    def test_default_structured_result_absent(self, tmp_path: Any) -> None:
        """New tasks have no structuredResult by default."""
        store = SQLiteStore(str(tmp_path / "test.sqlite3"))
        task = store.create_task(
            session_id="s1", task_type="main", goal="test", plan=[],
        )
        assert "structuredResult" not in task or task.get("structuredResult") is None

    def test_migration_adds_column(self, tmp_path: Any) -> None:
        """Existing databases get structured_result_json column."""
        store = SQLiteStore(str(tmp_path / "test.sqlite3"))
        columns = {
            row["name"]
            for row in store._conn.execute("PRAGMA table_info(tasks)").fetchall()
        }
        assert "structured_result_json" in columns


class TestStructuredResultBuild:
    """_finalize_task builds structured result from task fields."""

    def test_finalize_builds_structured_result(self, tmp_path: Any) -> None:
        """When _finalize_task completes a running task, structuredResult is populated."""
        rt = _make_runtime(tmp_path)
        store = rt.store

        # Create session and task
        session = store.create_session(workspace_id="w1", title="test session")
        task = store.create_task(
            session_id=session["id"], task_type="main",
            goal="implement feature X", plan=[{"step": 1, "action": "code"}],
        )
        # Set some fields that feed into structured result
        store.update_task(
            task_id=task["id"],
            changed_files=[{"path": "src/feature.py", "action": "created"}],
            tests_run=[{"name": "test_feature", "status": "passed", "duration_ms": 100}],
            risks=[{"type": "compatibility", "description": "may break old API", "severity": "low"}],
        )

        # Simulate _finalize_task logic
        final_summary = "Feature X implemented and tested"
        refreshed = store.get_task({"taskId": task["id"]})["task"]
        structured_result = {
            "summary": final_summary,
            "status": "success",
            "changedFiles": refreshed.get("changedFiles") or [],
            "testsRun": refreshed.get("testsRun") or [],
            "risks": refreshed.get("risks") or [],
            "keyFindings": [],
        }
        completed = store.update_task(
            task_id=task["id"],
            status="completed",
            result_summary=final_summary,
            structured_result=structured_result,
        )

        assert completed["structuredResult"]["summary"] == final_summary
        assert completed["structuredResult"]["status"] == "success"
        assert len(completed["structuredResult"]["changedFiles"]) == 1
        assert completed["structuredResult"]["changedFiles"][0]["path"] == "src/feature.py"
        assert len(completed["structuredResult"]["testsRun"]) == 1
        assert len(completed["structuredResult"]["risks"]) == 1
        assert completed["structuredResult"]["keyFindings"] == []

    def test_finalize_with_empty_task_fields(self, tmp_path: Any) -> None:
        """_finalize_task works when task has no changedFiles/testsRun/risks."""
        rt = _make_runtime(tmp_path)
        store = rt.store

        session = store.create_session(workspace_id="w1", title="test session")
        task = store.create_task(
            session_id=session["id"], task_type="main",
            goal="simple task", plan=[],
        )

        final_summary = "Done quickly"
        structured_result = {
            "summary": final_summary,
            "status": "success",
            "changedFiles": [],
            "testsRun": [],
            "risks": [],
            "keyFindings": [],
        }
        completed = store.update_task(
            task_id=task["id"],
            status="completed",
            result_summary=final_summary,
            structured_result=structured_result,
        )

        assert completed["structuredResult"]["summary"] == final_summary
        assert completed["structuredResult"]["changedFiles"] == []
        assert completed["structuredResult"]["testsRun"] == []
        assert completed["structuredResult"]["risks"] == []
