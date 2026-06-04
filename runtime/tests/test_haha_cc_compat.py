from __future__ import annotations

import json

from local_agent_runtime.haha_cc_compat import (
    normalize_haha_cc_usage,
    normalize_yuanbao_usage,
    to_haha_cc_server_message,
    to_yuanbao_server_message,
)
from local_agent_runtime.models import RuntimeEvent
from local_agent_runtime.store.sqlite_store import SQLiteStore


def _event(event_type: str, payload: dict) -> RuntimeEvent:
    return RuntimeEvent(
        event_id="evt_1",
        session_id="sess_1",
        task_id="task_1",
        type=event_type,
        ts=100,
        payload=payload,
        visibility="chat",
    )


def test_chat_compat_events_flatten_to_haha_cc_server_messages() -> None:
    assert to_haha_cc_server_message(
        _event("content_start", {"blockType": "tool_use", "toolName": "read_file", "toolUseId": "call_1"})
    ) == {
        "type": "content_start",
        "blockType": "tool_use",
        "toolName": "read_file",
        "toolUseId": "call_1",
    }
    assert to_haha_cc_server_message(
        _event("tool_result", {"toolUseId": "call_1", "content": {"ok": True}, "isError": False})
    ) == {
        "type": "tool_result",
        "toolUseId": "call_1",
        "content": {"ok": True},
        "isError": False,
    }


def test_haha_cc_message_keeps_only_server_message_fields() -> None:
    assert to_haha_cc_server_message(
        _event(
            "content_delta",
            {
                "text": "hello",
                "toolOutput": "stdout stays on the local envelope only",
                "target": "npm test",
                "_chatCompat": True,
            },
        )
    ) == {
        "type": "content_delta",
        "text": "hello",
    }


def test_haha_cc_message_rejects_incomplete_server_messages() -> None:
    assert to_haha_cc_server_message(_event("content_start", {"toolName": "read_file"})) is None
    assert to_haha_cc_server_message(_event("content_delta", {"toolOutput": "stdout only"})) is None
    assert (
        to_haha_cc_server_message(
            _event("permission_request", {"requestId": "approval_1", "toolName": "run_command"})
        )
        is None
    )
    assert to_haha_cc_server_message(_event("status", {"state": "retrying_provider"})) is None
    assert (
        to_haha_cc_server_message(
            _event("computer_use_permission_request", {"request": {"action": "click"}})
        )
        is None
    )


def test_computer_use_permission_request_flattens_to_haha_cc_message() -> None:
    event = _event(
        "computer_use_permission_request",
        {
            "requestId": "approval_1",
            "request": {"action": "click", "target": "Submit"},
            "preview": {"risk": "low"},
        },
    )
    assert event.payload["preview"] == {"risk": "low"}
    assert to_haha_cc_server_message(event) == {
        "type": "computer_use_permission_request",
        "requestId": "approval_1",
        "request": {"action": "click", "target": "Submit"},
    }


def test_message_complete_usage_is_normalized_to_snake_case() -> None:
    message = to_haha_cc_server_message(
        _event(
            "message_complete",
            {
                "usage": {
                    "inputTokens": 120,
                    "outputTokens": 30,
                    "cacheReadTokens": 80,
                    "cacheCreationTokens": 12,
                }
            },
        )
    )

    assert message == {
        "type": "message_complete",
        "usage": {
            "input_tokens": 120,
            "output_tokens": 30,
            "cache_read_tokens": 80,
            "cache_creation_tokens": 12,
        },
    }


def test_message_completed_raw_usage_is_normalized() -> None:
    message = to_haha_cc_server_message(
        _event(
            "message.completed",
            {
                "raw": {
                    "usage": {
                        "prompt_tokens": 10,
                        "completion_tokens": 5,
                        "prompt_tokens_details": {"cached_tokens": 7},
                    }
                }
            },
        )
    )

    assert message == {
        "type": "message_complete",
        "usage": {
            "input_tokens": 10,
            "output_tokens": 5,
            "cache_read_tokens": 7,
        },
    }


def test_message_complete_usage_normalizes_anthropic_cache_tokens() -> None:
    message = to_haha_cc_server_message(
        _event(
            "message.completed",
            {
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 12,
                    "cache_read_input_tokens": 80,
                    "cache_creation_input_tokens": 15,
                    "budgetRemainingTokens": 9000,
                }
            },
        )
    )

    assert message == {
        "type": "message_complete",
        "usage": {
            "input_tokens": 100,
            "output_tokens": 12,
            "cache_read_tokens": 80,
            "cache_creation_tokens": 15,
        },
    }


def test_message_complete_usage_normalizes_nested_input_token_details() -> None:
    assert normalize_haha_cc_usage(
        {
            "inputTokens": 200,
            "outputTokens": 20,
            "input_tokens_details": {
                "cached_tokens": 120,
                "cache_creation_tokens": 30,
            },
        }
    ) == {
        "input_tokens": 200,
        "output_tokens": 20,
        "cache_read_tokens": 120,
        "cache_creation_tokens": 30,
    }


def test_failed_events_map_to_haha_cc_error_and_task_update_messages() -> None:
    assert to_haha_cc_server_message(
        _event(
            "message.failed",
            {
                "content": "Provider returned error: quota exceeded",
                "errorCode": "MODEL_PROVIDER_ERROR",
                "retryable": True,
                "businessErrorCode": "quota_exceeded",
            },
        )
    ) == {
        "type": "error",
        "message": "Provider returned error: quota exceeded",
        "code": "MODEL_PROVIDER_ERROR",
        "retryable": True,
        "businessErrorCode": "quota_exceeded",
    }
    assert to_haha_cc_server_message(
        _event(
            "task.failed",
            {
                "resultSummary": "Task failed after approval was rejected.",
                "error": {"code": "APPROVAL_REJECTED", "retryable": False},
            },
        )
    ) == {
        "type": "task_update",
        "taskId": "task_1",
        "status": "failed",
        "progress": "Task failed after approval was rejected.",
    }


def test_task_and_session_events_map_to_haha_cc_names() -> None:
    assert to_haha_cc_server_message(_event("task.updated", {"status": "running", "currentStep": "Reading"})) == {
        "type": "task_update",
        "taskId": "task_1",
        "status": "running",
        "progress": "Reading",
    }
    assert to_haha_cc_server_message(_event("task.created", {"status": "queued", "goal": "Write docs"})) == {
        "type": "task_update",
        "taskId": "task_1",
        "status": "queued",
        "progress": "Write docs",
    }
    assert to_haha_cc_server_message(_event("session.updated", {"title": "New title", "changedFields": ["title"]})) == {
        "type": "session_title_updated",
        "sessionId": "sess_1",
        "title": "New title",
    }
    assert (
        to_haha_cc_server_message(
            _event("session.updated", {"title": "New title", "summary": "Task memory", "changedFields": ["summary"]})
        )
        is None
    )


def test_connected_and_pong_map_to_haha_cc_names() -> None:
    assert to_haha_cc_server_message(_event("connected", {})) == {
        "type": "connected",
        "sessionId": "sess_1",
    }
    assert to_haha_cc_server_message(_event("pong", {})) == {
        "type": "pong",
    }


def test_collaboration_events_map_to_haha_cc_team_messages() -> None:
    assert to_haha_cc_server_message(
        _event(
            "collab.worker.heartbeat",
            {
                "team": {
                    "teamName": "sess_1",
                    "workers": [
                        {"id": "worker_1", "role": "reviewer", "status": "busy", "currentTaskId": "child_1"}
                    ],
                    "tasks": [
                        {
                            "id": "child_1",
                            "title": "Review patch",
                            "status": "running",
                            "assignedWorkerId": "worker_1",
                            "metadata": {"agentType": "reviewer"},
                        }
                    ],
                },
                "worker": {"id": "worker_1", "role": "reviewer", "status": "busy", "currentTaskId": "child_1"},
            },
        )
    ) == {
        "type": "team_update",
        "teamName": "sess_1",
        "members": [
            {
                "agentId": "worker_1",
                "role": "reviewer",
                "status": "running",
                "currentTask": "Review patch",
            }
        ],
    }
    assert to_haha_cc_server_message(
        _event(
            "collab.task.created",
            {
                "task": {
                    "id": "child_1",
                    "sessionId": "sess_1",
                    "title": "Inspect workspace",
                    "status": "queued",
                    "metadata": {"agentType": "explorer"},
                }
            },
        )
    ) == {
        "type": "team_update",
        "teamName": "sess_1",
        "members": [
            {
                "agentId": "child_1",
                "role": "explorer",
                "status": "running",
                "currentTask": "Inspect workspace",
            }
        ],
    }
    assert to_haha_cc_server_message(
        _event(
            "collab.message.sent",
            {
                "team": {
                    "teamName": "sess_1",
                    "tasks": [
                        {
                            "id": "child_1",
                            "title": "Inspect workspace",
                            "status": "completed",
                            "assignedWorkerId": "worker_1",
                            "result": {"summary": "Done"},
                            "metadata": {"agentType": "explorer"},
                        }
                    ],
                    "workers": [{"id": "worker_1", "role": "explorer", "status": "idle"}],
                },
                "message": {"taskId": "child_1", "senderWorkerId": "worker_1", "kind": "result", "body": "Done"},
            },
        )
    ) == {
        "type": "team_update",
        "teamName": "sess_1",
        "members": [
            {
                "agentId": "worker_1",
                "role": "explorer",
                "status": "completed",
                "currentTask": "Done",
            }
        ],
    }
    assert to_haha_cc_server_message(
        _event(
            "collab.worker.budget.updated",
            {"dimension": "tokens", "consumed": 50, "budget": {"workerId": "worker_1", "role": "coder"}},
        )
    ) == {
        "type": "team_update",
        "teamName": "default",
        "members": [
            {
                "agentId": "worker_1",
                "role": "coder",
                "status": "running",
                "currentTask": "tokens budget consumed 50",
            }
        ],
    }


def test_special_chat_events_map_to_haha_cc_system_notifications() -> None:
    assert to_haha_cc_server_message(_event("init", {"message": "Session ready"})) == {
        "type": "system_notification",
        "subtype": "init",
        "message": "Session ready",
        "data": {"message": "Session ready"},
    }
    assert to_haha_cc_server_message(_event("compact_summary", {"summary": "Context compacted"})) == {
        "type": "system_notification",
        "subtype": "compact_summary",
        "message": "Context compacted",
        "data": {"summary": "Context compacted"},
    }
    assert to_haha_cc_server_message(_event("goal_event", {"message": "Goal complete"})) == {
        "type": "system_notification",
        "subtype": "goal_event",
        "message": "Goal complete",
        "data": {"message": "Goal complete"},
    }
    assert to_haha_cc_server_message(_event("memory_event", {"message": "Saved memory"})) == {
        "type": "system_notification",
        "subtype": "memory_saved",
        "message": "Saved memory",
        "data": {"message": "Saved memory"},
    }
    assert to_haha_cc_server_message(_event("compact_boundary", {"message": "Boundary reached"})) == {
        "type": "system_notification",
        "subtype": "compact_boundary",
        "message": "Boundary reached",
        "data": {"message": "Boundary reached"},
    }
    assert to_haha_cc_server_message(_event("session_state_changed", {"state": "ready"})) == {
        "type": "system_notification",
        "subtype": "session_state_changed",
        "message": "ready",
        "data": {"state": "ready"},
    }
    assert to_haha_cc_server_message(
        _event("system_notification", {"summary": "Switched provider", "phase": "provider_preflight", "model": "fallback"})
    ) == {
        "type": "system_notification",
        "subtype": "session_state_changed",
        "message": "Switched provider",
        "data": {"summary": "Switched provider", "phase": "provider_preflight", "model": "fallback"},
    }


def test_progress_events_map_to_haha_cc_task_progress_notifications() -> None:
    assert to_haha_cc_server_message(_event("assistant_progress", {"summary": "Inspecting repo"})) == {
        "type": "system_notification",
        "subtype": "task_progress",
        "message": "Inspecting repo",
        "data": {"summary": "Inspecting repo"},
    }
    assert to_haha_cc_server_message(_event("tool.output", {"toolName": "run_command", "chunk": "npm ok"})) == {
        "type": "system_notification",
        "subtype": "task_progress",
        "message": "npm ok",
        "data": {"toolName": "run_command", "chunk": "npm ok"},
    }


def test_normalize_usage_falls_back_total_tokens_to_input() -> None:
    assert normalize_haha_cc_usage({"total_tokens": 42}) == {
        "input_tokens": 42,
        "output_tokens": 0,
    }


def test_yuanbao_alias_matches_haha_cc_compat_message() -> None:
    event = _event("content_delta", {"text": "hello"})

    assert to_yuanbao_server_message(event) == to_haha_cc_server_message(event)
    assert normalize_yuanbao_usage({"total_tokens": 42}) == normalize_haha_cc_usage({"total_tokens": 42})


def test_trace_list_and_events_after_include_haha_cc_message(tmp_path) -> None:
    store = SQLiteStore(str(tmp_path / "runtime.sqlite3"))
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    workspace = store.upsert_workspace(str(workspace_root))
    session = store.create_session(workspace_id=workspace["id"], title="compat trace")
    task = store.create_task(session_id=session["id"], task_type="chat", goal="stream", plan=[])

    trace = store.append_trace_event(
        task_id=task["id"],
        session_id=session["id"],
        event_type="content_delta",
        source="assistant",
        payload={"text": "hello", "toolOutput": "kept only on local payload"},
    )

    assert trace["yuanbao"] == {"type": "content_delta", "text": "hello"}
    assert trace["hahaCc"] == trace["yuanbao"]
    listed = store.list_trace_events({"taskId": task["id"]})["traceEvents"]
    assert listed[0]["yuanbao"] == {"type": "content_delta", "text": "hello"}
    assert listed[0]["hahaCc"] == listed[0]["yuanbao"]
    assert listed[0]["payload"]["toolOutput"] == "kept only on local payload"
    after = store.events_after(session["id"], 0)["events"]
    assert after[0]["yuanbao"] == {"type": "content_delta", "text": "hello"}
    assert after[0]["hahaCc"] == after[0]["yuanbao"]


def test_rpc_haha_cc_events_after_returns_flat_messages_and_last_sequence(tmp_path) -> None:
    from local_agent_runtime.event_bus import EventBus
    from local_agent_runtime.orchestrator.service import Orchestrator
    from local_agent_runtime.rpc.server import JsonRpcServer
    from local_agent_runtime.tools.registry import ToolRegistry

    store = SQLiteStore(str(tmp_path / "runtime.sqlite3"))
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    workspace = store.upsert_workspace(str(workspace_root))
    session = store.create_session(workspace_id=workspace["id"], title="compat rpc")
    task = store.create_task(session_id=session["id"], task_type="chat", goal="stream", plan=[])
    store.append_trace_event(
        task_id=task["id"],
        session_id=session["id"],
        event_type="content_delta",
        source="assistant",
        payload={"text": "hello"},
    )
    store.append_trace_event(
        task_id=task["id"],
        session_id=session["id"],
        event_type="provider.request",
        source="provider",
        payload={"model": "test"},
        visibility="trace",
    )
    store.append_trace_event(
        task_id=task["id"],
        session_id=session["id"],
        event_type="thinking",
        source="assistant",
        payload={"text": "plan"},
    )

    event_bus = EventBus()
    orchestrator = Orchestrator(
        store=store,
        event_bus=event_bus,
        tool_registry=ToolRegistry({}),
        provider=None,
    )
    server = JsonRpcServer(orchestrator=orchestrator, store=store, event_bus=event_bus)
    response = server.handle_line(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": "req_1",
                "method": "events.hahaCcAfter",
                "params": {"sessionId": session["id"], "afterSeq": 0},
            }
        )
    )

    assert response["result"] == {
        "messages": [
            {"type": "content_delta", "text": "hello"},
            {"type": "thinking", "text": "plan"},
        ],
        "lastSeq": 3,
        "truncated": False,
    }

    yuanbao_response = server.handle_line(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": "req_2",
                "method": "events.yuanbaoAfter",
                "params": {"sessionId": session["id"], "afterSeq": 0},
            }
        )
    )
    assert yuanbao_response["result"] == response["result"]


def test_events_after_uses_message_delta_as_historical_content_delta(tmp_path) -> None:
    from local_agent_runtime.event_bus import EventBus
    from local_agent_runtime.orchestrator.service import Orchestrator
    from local_agent_runtime.rpc.server import JsonRpcServer
    from local_agent_runtime.tools.registry import ToolRegistry

    store = SQLiteStore(str(tmp_path / "runtime.sqlite3"))
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    workspace = store.upsert_workspace(str(workspace_root))
    session = store.create_session(workspace_id=workspace["id"], title="compat rpc")
    task = store.create_task(session_id=session["id"], task_type="chat", goal="stream", plan=[])
    store.append_trace_event(
        task_id=task["id"],
        session_id=session["id"],
        event_type="assistant.token",
        source="assistant",
        payload={"messageId": "msg_1", "delta": "hello", "_chatCompat": True},
    )
    store.append_trace_event(
        task_id=task["id"],
        session_id=session["id"],
        event_type="message.delta",
        source="assistant",
        payload={"messageId": "msg_1", "delta": "hello", "_chatCompat": True},
    )
    store.append_trace_event(
        task_id=task["id"],
        session_id=session["id"],
        event_type="message.completed",
        source="assistant",
        payload={
            "messageId": "msg_1",
            "content": "hello",
            "_chatCompat": True,
            "usage": {"inputTokens": 3, "outputTokens": 1},
        },
    )

    event_bus = EventBus()
    orchestrator = Orchestrator(
        store=store,
        event_bus=event_bus,
        tool_registry=ToolRegistry({}),
        provider=None,
    )
    server = JsonRpcServer(orchestrator=orchestrator, store=store, event_bus=event_bus)
    response = server.handle_line(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": "req_1",
                "method": "events.yuanbaoAfter",
                "params": {"sessionId": session["id"], "afterSeq": 0},
            }
        )
    )

    assert response["result"] == {
        "messages": [
            {"type": "content_delta", "text": "hello"},
            {"type": "message_complete", "usage": {"input_tokens": 3, "output_tokens": 1}},
        ],
        "lastSeq": 3,
        "truncated": False,
    }


def test_events_after_keeps_message_created_out_of_flat_history(tmp_path) -> None:
    from local_agent_runtime.event_bus import EventBus
    from local_agent_runtime.orchestrator.service import Orchestrator
    from local_agent_runtime.rpc.server import JsonRpcServer
    from local_agent_runtime.tools.registry import ToolRegistry

    store = SQLiteStore(str(tmp_path / "runtime.sqlite3"))
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    workspace = store.upsert_workspace(str(workspace_root))
    session = store.create_session(workspace_id=workspace["id"], title="created lifecycle")
    task = store.create_task(session_id=session["id"], task_type="chat", goal="stream", plan=[])
    store.append_trace_event(
        task_id=task["id"],
        session_id=session["id"],
        event_type="message.created",
        source="assistant",
        payload={"message": {"id": "msg_1", "role": "assistant", "content": ""}},
    )
    store.append_trace_event(
        task_id=task["id"],
        session_id=session["id"],
        event_type="content_start",
        source="assistant",
        payload={"blockType": "text", "messageId": "msg_1"},
    )
    store.append_trace_event(
        task_id=task["id"],
        session_id=session["id"],
        event_type="content_delta",
        source="assistant",
        payload={"text": "hello", "messageId": "msg_1"},
    )

    event_bus = EventBus()
    orchestrator = Orchestrator(
        store=store,
        event_bus=event_bus,
        tool_registry=ToolRegistry({}),
        provider=None,
    )
    server = JsonRpcServer(orchestrator=orchestrator, store=store, event_bus=event_bus)
    response = server.handle_line(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": "req_1",
                "method": "events.yuanbaoAfter",
                "params": {"sessionId": session["id"], "afterSeq": 0},
            }
        )
    )

    assert response["result"] == {
        "messages": [
            {"type": "content_start", "blockType": "text"},
            {"type": "content_delta", "text": "hello"},
        ],
        "lastSeq": 3,
        "truncated": False,
    }


def test_trace_list_includes_haha_cc_error_message(tmp_path) -> None:
    store = SQLiteStore(str(tmp_path / "runtime.sqlite3"))
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    workspace = store.upsert_workspace(str(workspace_root))
    session = store.create_session(workspace_id=workspace["id"], title="compat trace")
    task = store.create_task(session_id=session["id"], task_type="chat", goal="fail", plan=[])

    trace = store.append_trace_event(
        task_id=task["id"],
        session_id=session["id"],
        event_type="message.failed",
        source="message",
        payload={"content": "Provider failed", "errorCode": "MODEL_PROVIDER_ERROR"},
    )

    assert trace["yuanbao"] == {
        "type": "error",
        "message": "Provider failed",
        "code": "MODEL_PROVIDER_ERROR",
    }
    assert trace["hahaCc"] == trace["yuanbao"]
