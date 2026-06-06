"""Tests for P6.2 AgentRole validation and P6.7 EventVisibility inference + persistence."""

import io
import json
import threading
import tempfile
from pathlib import Path

import pytest

from local_agent_runtime.models import EventVisibility
from local_agent_runtime.rpc.server import JsonRpcServer


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

    # Root assistant.token derives chat text, but the raw compat event stays trace.
    def test_assistant_token_root_derives_chat_but_raw_event_is_trace(self):
        assert self._invoke("assistant.token", role="root") == "chat"
        from local_agent_runtime.orchestrator.service import Orchestrator
        assert Orchestrator._raw_runtime_event_visibility("assistant.token", "chat", None) == "trace"

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

    def test_provider_events_are_trace(self):
        assert self._invoke("provider.request") == "trace"
        assert self._invoke("provider.stream.finish") == "trace"

    def test_assistant_progress_root_is_panel(self):
        assert self._invoke("assistant_progress", role="root") == "panel"

    def test_assistant_progress_child_is_trace(self):
        assert self._invoke("assistant_progress", role="worker") == "trace"

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

    def test_payload_includes_haha_cc_compat_message_when_available(self):
        from local_agent_runtime.event_bus import EventBus
        bus = EventBus()
        event = type("E", (), {
            "event_id": "e3", "session_id": "s1", "task_id": "t1",
            "type": "content_delta", "ts": 0, "seq": 0,
            "payload": {"text": "hello"}, "visibility": "chat",
        })()
        payload = bus.as_payload(event)
        assert payload["yuanbao"] == {"type": "content_delta", "text": "hello"}
        assert payload["hahaCc"] == payload["yuanbao"]

    def test_payload_suppresses_bridge_message_delta_flat_duplicate(self):
        from local_agent_runtime.event_bus import EventBus
        bus = EventBus()
        event = type("E", (), {
            "event_id": "e3b", "session_id": "s1", "task_id": "t1",
            "type": "message.delta", "ts": 0, "seq": 0,
            "payload": {"messageId": "msg_1", "delta": "hello", "_chatCompat": True},
            "visibility": "chat",
        })()
        payload = bus.as_payload(event)
        assert payload["type"] == "message.delta"
        assert "yuanbao" not in payload
        assert "hahaCc" not in payload

    def test_payload_suppresses_bridge_message_completed_flat_duplicate(self):
        from local_agent_runtime.event_bus import EventBus
        bus = EventBus()
        event = type("E", (), {
            "event_id": "e3c", "session_id": "s1", "task_id": "t1",
            "type": "message.completed", "ts": 0, "seq": 0,
            "payload": {"messageId": "msg_1", "content": "done", "_chatCompat": True},
            "visibility": "chat",
        })()
        payload = bus.as_payload(event)
        assert payload["type"] == "message.completed"
        assert "yuanbao" not in payload
        assert "hahaCc" not in payload

    def test_payload_keeps_message_created_as_envelope_only(self):
        from local_agent_runtime.event_bus import EventBus
        bus = EventBus()
        event = type("E", (), {
            "event_id": "e3d", "session_id": "s1", "task_id": "t1",
            "type": "message.created", "ts": 0, "seq": 0,
            "payload": {"message": {"id": "msg_1", "role": "assistant", "content": ""}},
            "visibility": "chat",
        })()
        payload = bus.as_payload(event)
        assert payload["type"] == "message.created"
        assert payload["payload"]["message"]["role"] == "assistant"
        assert "yuanbao" not in payload
        assert "hahaCc" not in payload

    def test_payload_includes_haha_cc_error_for_failed_message(self):
        from local_agent_runtime.event_bus import EventBus
        bus = EventBus()
        event = type("E", (), {
            "event_id": "e4", "session_id": "s1", "task_id": "t1",
            "type": "message.failed", "ts": 0, "seq": 0,
            "payload": {"content": "Provider failed", "errorCode": "MODEL_PROVIDER_ERROR"}, "visibility": "chat",
        })()
        payload = bus.as_payload(event)
        assert payload["yuanbao"] == {
            "type": "error",
            "message": "Provider failed",
            "code": "MODEL_PROVIDER_ERROR",
        }
        assert payload["hahaCc"] == payload["yuanbao"]

    def test_rpc_writer_emits_haha_cc_message_line_when_available(self):
        server = JsonRpcServer.__new__(JsonRpcServer)
        writer = io.StringIO()
        server._writer = writer  # noqa: SLF001
        server._writer_lock = threading.Lock()  # noqa: SLF001

        server._write_event_payload(  # noqa: SLF001
            {
                "eventId": "evt_1",
                "sessionId": "sess_1",
                "taskId": "task_1",
                "type": "content_delta",
                "ts": 1,
                "payload": {"text": "hello"},
                "visibility": "chat",
                "yuanbao": {"type": "content_delta", "text": "hello"},
                "hahaCc": {"type": "content_delta", "text": "hello"},
            }
        )

        lines = [json.loads(line) for line in writer.getvalue().splitlines()]
        assert lines == [
            {
                "kind": "event",
                "payload": {
                    "eventId": "evt_1",
                    "sessionId": "sess_1",
                    "taskId": "task_1",
                    "type": "content_delta",
                    "ts": 1,
                    "payload": {"text": "hello"},
                    "visibility": "chat",
                    "yuanbao": {"type": "content_delta", "text": "hello"},
                    "hahaCc": {"type": "content_delta", "text": "hello"},
                },
            },
            {
                "kind": "yuanbao_message",
                "payload": {"type": "content_delta", "text": "hello"},
            },
            {
                "kind": "haha_cc_message",
                "payload": {"type": "content_delta", "text": "hello"},
            },
        ]

    def test_publish_assigns_monotonic_timestamp_and_sequence(self):
        from local_agent_runtime.event_bus import EventBus
        bus = EventBus()
        captured = []
        bus.subscribe(lambda event: captured.append(bus.as_payload(event)))
        first = type("E", (), {
            "event_id": "e1", "session_id": "s1", "task_id": "t1",
            "type": "content_start", "ts": 100, "seq": 0,
            "payload": {}, "visibility": "chat",
        })()
        second = type("E", (), {
            "event_id": "e2", "session_id": "s1", "task_id": "t1",
            "type": "content_delta", "ts": 100, "seq": 0,
            "payload": {}, "visibility": "chat",
        })()

        bus.publish(first)
        bus.publish(second)

        assert [event["seq"] for event in captured] == [1, 2]
        assert [event["ts"] for event in captured] == [100, 101]
