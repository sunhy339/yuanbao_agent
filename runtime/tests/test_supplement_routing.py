"""Tests for P6.8: supplement routing to child tasks."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

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


class TestSupplementRouting:
    """Test P6.8: supplement messages route to matching child tasks."""

    def test_no_children_returns_none(self, tmp_path: Any) -> None:
        """When there are no child tasks, routing returns None."""
        rt = _make_runtime(tmp_path)
        root = rt.store.create_task(
            session_id="s1", task_type="main", goal="main task",
            plan=[], status="running",
        )
        result = rt.orchestrator._route_supplement_to_children(
            session_id="s1",
            root_task={"id": root["id"]},
            content="update the auth module",
            message_id="msg1",
        )
        assert result is None

    def test_running_child_with_keyword_match_forwarded(self, tmp_path: Any) -> None:
        """Supplement with matching keyword is forwarded to running child."""
        rt = _make_runtime(tmp_path)
        store = rt.store

        root = store.create_task(
            session_id="s1", task_type="main", goal="main task",
            plan=[], status="running",
        )
        child = store.create_task(
            session_id="s1", task_type="child", goal="implement auth module",
            plan=[], status="running", root_task_id=root["id"],
        )

        result = rt.orchestrator._route_supplement_to_children(
            session_id="s1",
            root_task={"id": root["id"]},
            content="update the auth module",
            message_id="msg1",
        )

        assert result is not None
        assert len(result["routedTo"]) == 1
        assert result["routedTo"][0]["childTaskId"] == child["id"]
        assert result["routedTo"][0]["action"] == "forwarded"

    def test_completed_child_with_keyword_match_follow_up(self, tmp_path: Any) -> None:
        """Supplement matching completed child recommends follow-up."""
        rt = _make_runtime(tmp_path)
        store = rt.store

        root = store.create_task(
            session_id="s1", task_type="main", goal="main task",
            plan=[], status="running",
        )
        child = store.create_task(
            session_id="s1", task_type="child", goal="implement auth module",
            plan=[], status="completed", root_task_id=root["id"],
        )

        result = rt.orchestrator._route_supplement_to_children(
            session_id="s1",
            root_task={"id": root["id"]},
            content="update the auth module",
            message_id="msg1",
        )

        assert result is not None
        assert len(result["followUps"]) == 1
        assert result["followUps"][0]["childTaskId"] == child["id"]
        assert result["followUps"][0]["action"] == "follow_up_recommended"

    def test_no_keyword_overlap_skips_child(self, tmp_path: Any) -> None:
        """Child with no keyword overlap is skipped."""
        rt = _make_runtime(tmp_path)
        store = rt.store

        root = store.create_task(
            session_id="s1", task_type="main", goal="main task",
            plan=[], status="running",
        )
        store.create_task(
            session_id="s1", task_type="child", goal="implement auth module",
            plan=[], status="running", root_task_id=root["id"],
        )

        result = rt.orchestrator._route_supplement_to_children(
            session_id="s1",
            root_task={"id": root["id"]},
            content="update the database schema",
            message_id="msg1",
        )

        # content_words = {update, the, database, schema}
        # goal_words = {implement, auth, module}
        # No overlap -> None
        assert result is None

    def test_multiple_children_route_to_matching(self, tmp_path: Any) -> None:
        """With multiple children, only matching ones get routed."""
        rt = _make_runtime(tmp_path)
        store = rt.store

        root = store.create_task(
            session_id="s1", task_type="main", goal="main task",
            plan=[], status="running",
        )
        child_auth = store.create_task(
            session_id="s1", task_type="child", goal="implement auth",
            plan=[], status="running", root_task_id=root["id"],
        )
        child_db = store.create_task(
            session_id="s1", task_type="child", goal="setup database",
            plan=[], status="running", root_task_id=root["id"],
        )

        result = rt.orchestrator._route_supplement_to_children(
            session_id="s1",
            root_task={"id": root["id"]},
            content="update the auth module",
            message_id="msg1",
        )

        assert result is not None
        # Only child_auth should match (auth keyword overlap)
        assert len(result["routedTo"]) == 1
        assert result["routedTo"][0]["childTaskId"] == child_auth["id"]

    def test_routed_event_published(self, tmp_path: Any) -> None:
        """task.supplement.routed event is published when routing occurs."""
        from unittest.mock import MagicMock

        rt = _make_runtime(tmp_path)
        store = rt.store

        root = store.create_task(
            session_id="s1", task_type="main", goal="main task",
            plan=[], status="running",
        )
        store.create_task(
            session_id="s1", task_type="child", goal="implement auth",
            plan=[], status="running", root_task_id=root["id"],
        )

        # Mock _publish to capture events
        published: list[tuple[Any, ...]] = []
        original_publish = rt.orchestrator._publish
        rt.orchestrator._publish = MagicMock(
            side_effect=lambda *a, **kw: published.append((a, kw))
        )

        rt.orchestrator._route_supplement_to_children(
            session_id="s1",
            root_task={"id": root["id"]},
            content="update the auth",
            message_id="msg1",
        )

        # Check task.supplement.routed event was published
        routed_calls = [
            (a, kw) for a, kw in published
            if kw.get("event_type") == "task.supplement.routed"
        ]
        assert len(routed_calls) == 1
        assert routed_calls[0][1]["payload"]["routedToCount"] == 1

    def test_full_attach_supplemental_message_with_routing(self, tmp_path: Any) -> None:
        """Full _attach_supplemental_message includes routing result."""
        rt = _make_runtime(tmp_path)
        store = rt.store

        root = store.create_task(
            session_id="s1", task_type="main", goal="main task",
            plan=[], status="running",
        )
        child = store.create_task(
            session_id="s1", task_type="child", goal="implement auth",
            plan=[], status="running", root_task_id=root["id"],
        )

        result = rt.orchestrator._attach_supplemental_message(
            session_id="s1",
            task={"id": root["id"], "status": "running"},
            content="update the auth",
        )

        assert "supplementRouting" in result
        assert result["supplementRouting"]["routedTo"][0]["childTaskId"] == child["id"]

    def test_attach_supplemental_no_children_no_routing(self, tmp_path: Any) -> None:
        """When no children exist, _attach_supplemental_message still works."""
        rt = _make_runtime(tmp_path)
        store = rt.store

        root = store.create_task(
            session_id="s1", task_type="main", goal="main task",
            plan=[], status="running",
        )

        result = rt.orchestrator._attach_supplemental_message(
            session_id="s1",
            task={"id": root["id"], "status": "running"},
            content="some supplement",
        )

        # No routing info when no children
        assert result.get("supplementRouting") is None

    def test_cancelled_open_task_is_not_auto_supplemented(self, tmp_path: Any) -> None:
        """Auto supplement should ignore terminal tasks returned by open-task lookup."""
        rt = _make_runtime(tmp_path)
        store = rt.store

        cancelled = store.create_task(
            session_id="s1",
            task_type="main",
            goal="cancelled task",
            plan=[],
            status="cancelled",
        )

        original_lookup = rt.orchestrator._find_open_session_task
        rt.orchestrator._find_open_session_task = lambda _session_id: cancelled
        try:
            assert rt.orchestrator._find_supplement_target_task(session_id="s1", task_id=None, strict=False) is None
        finally:
            rt.orchestrator._find_open_session_task = original_lookup

    def test_attach_supplemental_rejects_terminal_task(self, tmp_path: Any) -> None:
        """Attaching a supplement directly to a terminal task is rejected before state transition."""
        rt = _make_runtime(tmp_path)
        store = rt.store
        cancelled = store.create_task(
            session_id="s1",
            task_type="main",
            goal="cancelled task",
            plan=[],
            status="cancelled",
        )

        with pytest.raises(ValueError, match="not active"):
            rt.orchestrator._attach_supplemental_message(
                session_id="s1",
                task=cancelled,
                content="continue",
            )
