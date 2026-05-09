"""Tests for P6.2 AgentRole validation and P6.7 EventVisibility inference + persistence."""

import json
import tempfile
from pathlib import Path

import pytest

from local_agent_runtime.models import EventVisibility


# ---------------------------------------------------------------------------
# P6.2 — AgentRole validation (in SQLiteStore.create_task)
# ---------------------------------------------------------------------------


class TestAgentRoleValidation:
    """Role must be one of the AgentRole literal set or None (defaults to root)."""

    def _make_store(self):
        from local_agent_runtime.store.sqlite_store import SQLiteStore
        return SQLiteStore(Path(tempfile.mkdtemp()) / "test.db")

    def test_root_role_accepted(self):
        store = self._make_store()
        task = store.create_task(
            session_id="s1", task_type="agent", goal="g", plan=[],
            role="root",
        )
        # root is the default; _serialize_task omits it when "root"
        assert task.get("role", "root") == "root"

    def test_worker_role_accepted(self):
        store = self._make_store()
        task = store.create_task(
            session_id="s1", task_type="agent", goal="g", plan=[],
            role="worker",
        )
        assert task["role"] == "worker"

    def test_planner_role_accepted(self):
        store = self._make_store()
        task = store.create_task(
            session_id="s1", task_type="agent", goal="g", plan=[],
            role="planner",
        )
        assert task["role"] == "planner"

    def test_reviewer_role_accepted(self):
        store = self._make_store()
        task = store.create_task(
            session_id="s1", task_type="agent", goal="g", plan=[],
            role="reviewer",
        )
        assert task["role"] == "reviewer"

    def test_summarizer_role_accepted(self):
        store = self._make_store()
        task = store.create_task(
            session_id="s1", task_type="agent", goal="g", plan=[],
            role="summarizer",
        )
        assert task["role"] == "summarizer"

    def test_none_role_defaults_to_root(self):
        store = self._make_store()
        task = store.create_task(
            session_id="s1", task_type="agent", goal="g", plan=[],
            role=None,
        )
        # root is default and omitted by serializer
        assert task.get("role", "root") == "root"

    def test_invalid_role_raises(self):
        store = self._make_store()
        with pytest.raises(ValueError, match="Invalid task role"):
            store.create_task(
                session_id="s1", task_type="agent", goal="g", plan=[],
                role="hacker",
            )


# ---------------------------------------------------------------------------
# P6.7 — EventVisibility inference (Orchestrator._infer_event_visibility)
# ---------------------------------------------------------------------------


class TestEventVisibilityInference:
    """Visibility is inferred from event_type and task role."""

    def _invoke(self, event_type, role="root"):
        from local_agent_runtime.orchestrator.service import Orchestrator
        task = {"role": role}
        return Orchestrator._infer_event_visibility(event_type, task)

    # Root streaming: chat (user sees assistant output)
    def test_assistant_token_root_is_chat(self):
        assert self._invoke("assistant.token", role="root") == "chat"

    def test_message_delta_root_is_chat(self):
        assert self._invoke("message.delta", role="root") == "chat"

    # Child streaming: trace
    def test_assistant_token_child_is_trace(self):
        assert self._invoke("assistant.token", role="worker") == "trace"

    def test_message_delta_child_is_trace(self):
        assert self._invoke("message.delta", role="worker") == "trace"

    def test_tool_call_started_is_trace(self):
        assert self._invoke("tool.call.started") == "trace"

    def test_tool_call_completed_is_trace(self):
        assert self._invoke("tool.call.completed") == "trace"

    def test_tool_call_failed_is_trace(self):
        assert self._invoke("tool.call.failed") == "trace"

    # Collab events → panel
    def test_collab_started_is_panel(self):
        assert self._invoke("collab.started") == "panel"

    def test_collab_completed_is_panel(self):
        assert self._invoke("collab.completed") == "panel"

    # Root task: everything else is chat
    def test_task_started_root_is_chat(self):
        assert self._invoke("task.started", role="root") == "chat"

    def test_task_completed_root_is_chat(self):
        assert self._invoke("task.completed", role="root") == "chat"

    # Non-root task: task. and message. → panel, others → trace
    def test_task_started_worker_is_panel(self):
        assert self._invoke("task.started", role="worker") == "panel"

    def test_task_completed_planner_is_panel(self):
        assert self._invoke("task.completed", role="planner") == "panel"

    def test_non_root_non_task_event_is_trace(self):
        assert self._invoke("some.other", role="worker") == "trace"

    def test_non_root_message_event_is_panel(self):
        assert self._invoke("message.created", role="reviewer") == "panel"


# ---------------------------------------------------------------------------
# P6.7 — EventVisibility persistence (SQLiteStore trace_events)
# ---------------------------------------------------------------------------


class TestEventVisibilityPersistence:
    """Visibility column is persisted and round-trips correctly."""

    def _make_store(self):
        from local_agent_runtime.store.sqlite_store import SQLiteStore
        return SQLiteStore(Path(tempfile.mkdtemp()) / "test.db")

    def _create_task(self, store):
        return store.create_task(
            session_id="s1", task_type="agent", goal="g", plan=[],
        )

    def test_append_trace_event_with_visibility(self):
        store = self._make_store()
        task = self._create_task(store)
        store.append_trace_event(
            task_id=task["id"], session_id="s1", event_type="task.started",
            payload={"goal": "g"}, source="orchestrator", related_id="r1",
            visibility="trace",
        )
        result = store.list_trace_events({"taskId": task["id"]})
        events = result["traceEvents"]
        assert len(events) == 1
        assert events[0]["visibility"] == "trace"

    def test_append_trace_event_default_chat(self):
        store = self._make_store()
        task = self._create_task(store)
        store.append_trace_event(
            task_id=task["id"], session_id="s1", event_type="task.started",
            payload={}, source="orchestrator", related_id="r1",
        )
        result = store.list_trace_events({"taskId": task["id"]})
        assert result["traceEvents"][0]["visibility"] == "chat"

    def test_append_collaboration_event_is_panel(self):
        store = self._make_store()
        task = self._create_task(store)
        # Create a collaboration task for the parent task
        store.create_collaboration_task({
            "parentTaskId": task["id"],
            "sessionId": "s1",
            "title": "child task",
        })
        collab_tasks = store.list_collaboration_tasks({"parentTaskId": task["id"]})
        collab_task_id = collab_tasks["tasks"][0]["id"]
        store.append_collaboration_trace_event(
            task_id=collab_task_id, session_id="s1", event_type="collab.started",
            payload={}, source="orchestrator", related_id="r1",
        )
        result = store.list_trace_events({"taskId": collab_task_id})
        assert result["traceEvents"][0]["visibility"] == "panel"

    def test_runtime_event_visibility_roundtrip(self):
        store = self._make_store()
        task = self._create_task(store)
        store.append_runtime_event(
            type("E", (), {
                "event_id": "e1", "session_id": "s1", "task_id": task["id"],
                "type": "task.completed", "ts": 0, "seq": 0,
                "payload": {}, "visibility": "panel",
            })(),
        )
        result = store.list_trace_events({"taskId": task["id"]})
        assert result["traceEvents"][0]["visibility"] == "panel"


# ---------------------------------------------------------------------------
# P6.7 — EventBus payload includes visibility
# ---------------------------------------------------------------------------


class TestEventBusVisibility:
    """EventBus.as_payload() includes visibility key."""

    def test_payload_has_visibility(self):
        from local_agent_runtime.event_bus import EventBus
        bus = EventBus()
        event = type("E", (), {
            "event_id": "e1", "session_id": "s1", "task_id": "t1",
            "type": "task.started", "ts": 0, "seq": 0,
            "payload": {}, "visibility": "trace",
        })()
        payload = bus.as_payload(event)
        assert payload["visibility"] == "trace"

    def test_payload_chat_visibility(self):
        from local_agent_runtime.event_bus import EventBus
        bus = EventBus()
        event = type("E", (), {
            "event_id": "e2", "session_id": "s1", "task_id": "t1",
            "type": "task.completed", "ts": 0, "seq": 0,
            "payload": {}, "visibility": "chat",
        })()
        payload = bus.as_payload(event)
        assert payload["visibility"] == "chat"