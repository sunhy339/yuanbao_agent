"""Tests for P6.4.5: worker testsRun + risks fields and validation."""

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


class TestWorkerOutputFields:
    """Tests for testsRun and risks fields on tasks."""

    def test_update_task_tests_run(self, tmp_path: Any) -> None:
        """update_task accepts and persists tests_run."""
        store = SQLiteStore(str(tmp_path / "test.sqlite3"))
        task = store.create_task(
            session_id="s1", task_type="main", goal="test", plan=[],
        )
        tests = [{"name": "test_foo", "status": "passed", "duration_ms": 42}]
        updated = store.update_task(task_id=task["id"], tests_run=tests)
        assert updated["testsRun"] == tests

    def test_update_task_risks(self, tmp_path: Any) -> None:
        """update_task accepts and persists risks."""
        store = SQLiteStore(str(tmp_path / "test.sqlite3"))
        task = store.create_task(
            session_id="s1", task_type="main", goal="test", plan=[],
        )
        risks = [{"type": "security", "description": "SQL injection risk", "severity": "high"}]
        updated = store.update_task(task_id=task["id"], risks=risks)
        assert updated["risks"] == risks

    def test_get_task_returns_tests_run_and_risks(self, tmp_path: Any) -> None:
        """get_task returns testsRun and risks fields."""
        store = SQLiteStore(str(tmp_path / "test.sqlite3"))
        task = store.create_task(
            session_id="s1", task_type="main", goal="test", plan=[],
        )
        store.update_task(
            task_id=task["id"],
            tests_run=[{"name": "t1", "status": "passed"}],
            risks=[{"type": "perf", "description": "slow query"}],
        )
        fetched = store.get_task({"taskId": task["id"]})["task"]
        assert len(fetched["testsRun"]) == 1
        assert len(fetched["risks"]) == 1

    def test_default_tests_run_risks_empty(self, tmp_path: Any) -> None:
        """New tasks have no testsRun or risks by default."""
        store = SQLiteStore(str(tmp_path / "test.sqlite3"))
        task = store.create_task(
            session_id="s1", task_type="main", goal="test", plan=[],
        )
        # Fields not present when never set
        assert "testsRun" not in task or task.get("testsRun") is None or task.get("testsRun") == []
        assert "risks" not in task or task.get("risks") is None or task.get("risks") == []


class TestWorkerOutputValidation:
    """Tests for _validate_worker_output method."""

    def test_root_task_no_warning(self, tmp_path: Any) -> None:
        """Root tasks don't trigger worker validation warnings."""
        rt = _make_runtime(tmp_path)
        # Mock _publish to capture events
        published: list[dict[str, Any]] = []
        rt.orchestrator._publish = MagicMock(
            side_effect=lambda *a, **kw: published.append(kw)
        )
        rt.orchestrator._validate_worker_output(
            session_id="s1",
            task={"id": "t1", "role": "root", "rootTaskId": "t1"},
        )
        validation_events = [
            e for e in published if e.get("event_type") == "task.worker.validation"
        ]
        assert len(validation_events) == 0

    def test_worker_missing_fields_publishes_warning(self, tmp_path: Any) -> None:
        """Worker task without testsRun/risks publishes warning event."""
        rt = _make_runtime(tmp_path)
        published: list[dict[str, Any]] = []
        rt.orchestrator._publish = MagicMock(
            side_effect=lambda *a, **kw: published.append(kw)
        )
        rt.orchestrator._validate_worker_output(
            session_id="s1",
            task={"id": "child1", "role": "worker", "rootTaskId": "root1"},
        )
        validation_events = [
            e for e in published if e.get("event_type") == "task.worker.validation"
        ]
        assert len(validation_events) == 1
        assert "testsRun" in validation_events[0]["payload"]["missingFields"]
        assert "risks" in validation_events[0]["payload"]["missingFields"]

    def test_worker_with_all_fields_no_warning(self, tmp_path: Any) -> None:
        """Worker task with testsRun and risks doesn't trigger warning."""
        rt = _make_runtime(tmp_path)
        published: list[dict[str, Any]] = []
        rt.orchestrator._publish = MagicMock(
            side_effect=lambda *a, **kw: published.append(kw)
        )
        rt.orchestrator._validate_worker_output(
            session_id="s1",
            task={
                "id": "child1", "role": "worker", "rootTaskId": "root1",
                "testsRun": [{"name": "t1", "status": "passed"}],
                "risks": [{"type": "none"}],
            },
        )
        validation_events = [
            e for e in published if e.get("event_type") == "task.worker.validation"
        ]
        assert len(validation_events) == 0

    def test_worker_missing_only_tests_run(self, tmp_path: Any) -> None:
        """Worker missing only testsRun warns about that field."""
        rt = _make_runtime(tmp_path)
        published: list[dict[str, Any]] = []
        rt.orchestrator._publish = MagicMock(
            side_effect=lambda *a, **kw: published.append(kw)
        )
        rt.orchestrator._validate_worker_output(
            session_id="s1",
            task={
                "id": "child1", "role": "worker", "rootTaskId": "root1",
                "risks": [{"type": "none"}],
            },
        )
        validation_events = [
            e for e in published if e.get("event_type") == "task.worker.validation"
        ]
        assert len(validation_events) == 1
        assert validation_events[0]["payload"]["missingFields"] == ["testsRun"]

    def test_migration_adds_columns(self, tmp_path: Any) -> None:
        """Existing databases get tests_run_json and risks_json columns."""
        store = SQLiteStore(str(tmp_path / "test.sqlite3"))
        columns = {
            row["name"]
            for row in store._conn.execute("PRAGMA table_info(tasks)").fetchall()
        }
        assert "tests_run_json" in columns
        assert "risks_json" in columns
