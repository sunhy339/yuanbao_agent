"""Tests for P6.8: root supplement impact scope assessment."""

from __future__ import annotations

from typing import Any

import pytest

from local_agent_runtime.orchestrator.service import Orchestrator


class TestExtractFilePaths:
    """_extract_file_paths correctly pulls file paths from free-form text."""

    def test_extracts_py_file(self) -> None:
        paths = Orchestrator._extract_file_paths("update src/auth/login.py")
        assert "src/auth/login.py" in paths

    def test_extracts_ts_file(self) -> None:
        paths = Orchestrator._extract_file_paths("fix the bug in app/utils.ts")
        assert "app/utils.ts" in paths

    def test_extracts_multiple_files(self) -> None:
        text = "changes to src/main.py and tests/test_main.py and config.yaml"
        paths = Orchestrator._extract_file_paths(text)
        assert "src/main.py" in paths
        assert "tests/test_main.py" in paths
        assert "config.yaml" in paths

    def test_extracts_tsx_file(self) -> None:
        paths = Orchestrator._extract_file_paths("edit src/components/Header.tsx")
        assert "src/components/Header.tsx" in paths

    def test_no_paths_in_plain_text(self) -> None:
        paths = Orchestrator._extract_file_paths("just some random words here")
        assert paths == []

    def test_backtick_wrapped_path(self) -> None:
        paths = Orchestrator._extract_file_paths("see `src/utils.py` for details")
        assert "src/utils.py" in paths

    def test_quote_wrapped_path(self) -> None:
        paths = Orchestrator._extract_file_paths('check "src/models.py" please')
        assert "src/models.py" in paths

    def test_extracts_rust_file(self) -> None:
        paths = Orchestrator._extract_file_paths("modify src/lib.rs")
        assert "src/lib.rs" in paths


class TestAssessSupplementImpact:
    """_assess_supplement_impact maps supplement content to affected children."""

    def test_direct_file_match(self) -> None:
        """Supplement mentioning a file that a child changed is a direct match."""
        children = [
            {
                "id": "c1",
                "goal": "implement auth",
                "changedFiles": [{"path": "src/auth/login.py"}],
            },
        ]
        impact = Orchestrator._assess_supplement_impact(
            "update src/auth/login.py to fix the token refresh",
            children,
        )
        assert "src/auth/login.py" in impact["affectedFiles"]
        assert len(impact["affectedChildTasks"]) == 1
        assert impact["affectedChildTasks"][0]["childTaskId"] == "c1"
        assert impact["affectedChildTasks"][0]["matchType"] == "direct"

    def test_directory_prefix_match(self) -> None:
        """Supplement file under a child's changed directory is a prefix match."""
        children = [
            {
                "id": "c1",
                "goal": "refactor auth module",
                "changedFiles": [{"path": "src/auth/login.py"}],
            },
        ]
        impact = Orchestrator._assess_supplement_impact(
            "update src/auth/session.py to handle expiry",
            children,
        )
        assert len(impact["affectedChildTasks"]) == 1
        assert impact["affectedChildTasks"][0]["matchType"] == "directory_prefix"

    def test_no_match_unrelated_files(self) -> None:
        """Supplement about unrelated files does not match child."""
        children = [
            {
                "id": "c1",
                "goal": "implement auth",
                "changedFiles": [{"path": "src/auth/login.py"}],
            },
        ]
        impact = Orchestrator._assess_supplement_impact(
            "update src/db/migrate.py to add new tables",
            children,
        )
        assert len(impact["affectedChildTasks"]) == 0

    def test_multiple_children_partial_match(self) -> None:
        """Supplement can match some but not all children."""
        children = [
            {
                "id": "c1",
                "goal": "implement auth",
                "changedFiles": [{"path": "src/auth/login.py"}],
            },
            {
                "id": "c2",
                "goal": "setup database",
                "changedFiles": [{"path": "src/db/models.py"}],
            },
        ]
        impact = Orchestrator._assess_supplement_impact(
            "update src/auth/login.py and fix src/db/models.py",
            children,
        )
        affected_ids = {e["childTaskId"] for e in impact["affectedChildTasks"]}
        assert "c1" in affected_ids
        assert "c2" in affected_ids

    def test_summary_contains_file_count(self) -> None:
        """Impact summary mentions file count."""
        children = []
        impact = Orchestrator._assess_supplement_impact(
            "update src/auth.py and src/utils.py",
            children,
        )
        assert "2 file(s)" in impact["summary"]

    def test_no_impact_summary(self) -> None:
        """When no files or children match, summary says so."""
        children = [
            {"id": "c1", "goal": "do stuff", "changedFiles": []},
        ]
        impact = Orchestrator._assess_supplement_impact(
            "just some text with no files",
            children,
        )
        assert "No specific file or task impact" in impact["summary"]

    def test_goal_text_file_references_matched(self) -> None:
        """File paths in child goal text are also considered for matching."""
        children = [
            {
                "id": "c1",
                "goal": "implement auth in src/auth.py",
                "changedFiles": [],
            },
        ]
        impact = Orchestrator._assess_supplement_impact(
            "update src/auth.py to add JWT support",
            children,
        )
        assert len(impact["affectedChildTasks"]) == 1
        assert impact["affectedChildTasks"][0]["childTaskId"] == "c1"

    def test_string_changed_files(self) -> None:
        """changedFiles as plain strings (not dicts) are also matched."""
        children = [
            {
                "id": "c1",
                "goal": "refactor utils",
                "changedFiles": ["src/utils.py"],
            },
        ]
        impact = Orchestrator._assess_supplement_impact(
            "fix src/utils.py",
            children,
        )
        assert len(impact["affectedChildTasks"]) == 1


class TestRoutingWithImpactScope:
    """Full routing includes impactScope in result and event payload."""

    def test_impact_scope_in_routing_result(self, tmp_path: Any) -> None:
        """Routing result includes impactScope when file paths match."""
        from local_agent_runtime.event_bus import EventBus
        from local_agent_runtime.store.sqlite_store import SQLiteStore
        from local_agent_runtime.tools.registry import ToolRegistry

        event_bus = EventBus()
        store = SQLiteStore(str(tmp_path / "rt.sqlite3"))

        class DummyProvider:
            def generate(self, prompt: str, context: dict[str, Any]) -> dict[str, Any]:
                return {"text": "ok", "status": "done"}

        orchestrator = Orchestrator(
            store=store, event_bus=event_bus,
            tool_registry=ToolRegistry(), provider=DummyProvider(),
        )

        root = store.create_task(
            session_id="s1", task_type="main", goal="main", plan=[], status="running",
        )
        child = store.create_task(
            session_id="s1", task_type="child", goal="implement auth in src/auth.py",
            plan=[], status="running", root_task_id=root["id"],
        )

        result = orchestrator._route_supplement_to_children(
            session_id="s1",
            root_task={"id": root["id"]},
            content="update src/auth.py to add JWT",
            message_id="msg1",
        )

        assert result is not None
        assert "impactScope" in result
        assert "src/auth.py" in result["impactScope"]["affectedFiles"]
        assert len(result["impactScope"]["affectedChildTasks"]) == 1
        # matchReasons should include file_path_match
        assert "file_path_match" in result["routedTo"][0].get("matchReasons", [])

    def test_impact_scope_in_event_payload(self, tmp_path: Any) -> None:
        """task.supplement.routed event payload includes impactScope."""
        from unittest.mock import MagicMock

        from local_agent_runtime.event_bus import EventBus
        from local_agent_runtime.store.sqlite_store import SQLiteStore
        from local_agent_runtime.tools.registry import ToolRegistry

        event_bus = EventBus()
        store = SQLiteStore(str(tmp_path / "rt.sqlite3"))

        class DummyProvider:
            def generate(self, prompt: str, context: dict[str, Any]) -> dict[str, Any]:
                return {"text": "ok", "status": "done"}

        orchestrator = Orchestrator(
            store=store, event_bus=event_bus,
            tool_registry=ToolRegistry(), provider=DummyProvider(),
        )

        root = store.create_task(
            session_id="s1", task_type="main", goal="main", plan=[], status="running",
        )
        store.create_task(
            session_id="s1", task_type="child", goal="fix src/api.py",
            plan=[], status="running", root_task_id=root["id"],
        )

        published: list[tuple[Any, ...]] = []
        orchestrator._publish = MagicMock(
            side_effect=lambda *a, **kw: published.append((a, kw))
        )

        orchestrator._route_supplement_to_children(
            session_id="s1",
            root_task={"id": root["id"]},
            content="update src/api.py with new endpoints",
            message_id="msg1",
        )

        routed_calls = [
            kw for _, kw in published
            if kw.get("event_type") == "task.supplement.routed"
        ]
        assert len(routed_calls) == 1
        assert "impactScope" in routed_calls[0]["payload"]
        assert "src/api.py" in routed_calls[0]["payload"]["impactScope"]["affectedFiles"]

    def test_file_match_routes_even_without_keyword_overlap(self, tmp_path: Any) -> None:
        """A file path match can route to a child even when keywords don't overlap."""
        from local_agent_runtime.event_bus import EventBus
        from local_agent_runtime.store.sqlite_store import SQLiteStore
        from local_agent_runtime.tools.registry import ToolRegistry

        event_bus = EventBus()
        store = SQLiteStore(str(tmp_path / "rt.sqlite3"))

        class DummyProvider:
            def generate(self, prompt: str, context: dict[str, Any]) -> dict[str, Any]:
                return {"text": "ok", "status": "done"}

        orchestrator = Orchestrator(
            store=store, event_bus=event_bus,
            tool_registry=ToolRegistry(), provider=DummyProvider(),
        )

        root = store.create_task(
            session_id="s1", task_type="main", goal="main", plan=[], status="running",
        )
        child = store.create_task(
            session_id="s1", task_type="child", goal="handle authentication flow",
            plan=[], status="running", root_task_id=root["id"],
        )
        # Add changedFiles to child so file match can kick in
        store.update_task(
            task_id=child["id"],
            changed_files=[{"path": "src/auth/session.py"}],
        )

        result = orchestrator._route_supplement_to_children(
            session_id="s1",
            root_task={"id": root["id"]},
            content="please modify src/auth/session.py to add refresh logic",
            message_id="msg1",
        )

        assert result is not None
        assert len(result["routedTo"]) == 1
        assert "file_path_match" in result["routedTo"][0]["matchReasons"]
