from __future__ import annotations

from local_agent_runtime.haha_cc_compat import to_haha_cc_server_message
from local_agent_runtime.models import RuntimeEvent
from local_agent_runtime.yuanbao_event_adapter import (
    collect_yuanbao_server_messages,
    normalize_yuanbao_usage,
    to_yuanbao_output_frames,
    to_yuanbao_server_message,
    yuanbao_message_from_event_payload,
)


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


def test_yuanbao_adapter_keeps_only_server_message_fields() -> None:
    message = to_yuanbao_server_message(
        _event(
            "content_delta",
            {
                "text": "hello",
                "toolOutput": "local envelope only",
                "target": "npm test",
                "_chatCompat": True,
            },
        )
    )

    assert message == {
        "type": "content_delta",
        "text": "hello",
    }


def test_yuanbao_adapter_keeps_tool_presentation_fields_when_tool_delta_has_id() -> None:
    assert to_yuanbao_server_message(
        _event(
            "content_delta",
            {
                "toolUseId": "call_1",
                "toolOutput": "read README.md",
                "target": "README.md",
                "displayTitle": "读取 README.md",
                "displaySummary": "读取文件完成",
                "_chatCompat": True,
                "workspaceRoot": "D:/py/test_pro",
            },
        )
    ) == {
        "type": "content_delta",
        "toolUseId": "call_1",
        "toolOutput": "read README.md",
        "target": "README.md",
        "displayTitle": "读取 README.md",
        "displaySummary": "读取文件完成",
    }


def test_yuanbao_adapter_rejects_missing_required_fields() -> None:
    assert to_yuanbao_server_message(_event("content_start", {"toolName": "read_file"})) is None
    assert to_yuanbao_server_message(_event("content_delta", {"toolOutput": "stdout only"})) is None
    assert (
        to_yuanbao_server_message(_event("tool_result", {"toolUseId": "call_1", "content": "ok"}))
        is None
    )
    assert (
        to_yuanbao_server_message(_event("permission_request", {"requestId": "approval_1", "input": {}}))
        is None
    )


def test_yuanbao_adapter_maps_haha_cc_status_states() -> None:
    assert to_yuanbao_server_message(_event("status", {"state": "thinking", "phase": "private"})) == {
        "type": "status",
        "state": "thinking",
    }
    assert to_yuanbao_server_message(_event("status", {"state": "retrying_provider", "_chatCompat": True})) is None


def test_yuanbao_adapter_maps_chat_compat_message_delta_to_content_delta() -> None:
    assert to_yuanbao_server_message(_event("message.delta", {"messageId": "msg_1", "delta": "hello"})) is None
    assert to_yuanbao_server_message(_event("message.delta", {"messageId": "msg_1", "delta": "hello", "_chatCompat": True})) == {
        "type": "content_delta",
        "text": "hello",
    }


def test_yuanbao_adapter_keeps_message_created_out_of_flat_protocol() -> None:
    assert (
        to_yuanbao_server_message(
            _event(
                "message.created",
                {
                    "message": {
                        "id": "msg_1",
                        "role": "assistant",
                        "content": "",
                    }
                },
            )
        )
        is None
    )


def test_yuanbao_adapter_does_not_flatten_bridge_assistant_token() -> None:
    assert (
        to_yuanbao_server_message(
            _event("assistant.token", {"messageId": "msg_1", "delta": "hello", "_chatCompat": True})
        )
        is None
    )
    assert to_yuanbao_server_message(_event("assistant.token", {"delta": "hello"})) is None


def test_yuanbao_adapter_normalizes_usage() -> None:
    assert normalize_yuanbao_usage(
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


def test_haha_cc_compat_exports_yuanbao_message_alias() -> None:
    event = _event("thinking", {"text": "plan", "_chatCompat": True})

    assert to_haha_cc_server_message(event) == to_yuanbao_server_message(event)


def test_yuanbao_output_frames_keep_flat_messages_in_sync() -> None:
    payload = {
        "eventId": "evt_1",
        "sessionId": "sess_1",
        "taskId": "task_1",
        "type": "content_delta",
        "payload": {"text": "hello"},
        "visibility": "chat",
        "yuanbao": {"type": "content_delta", "text": "hello"},
    }

    frames = to_yuanbao_output_frames(payload)

    assert frames == [
        {"kind": "yuanbao_message", "payload": {"type": "content_delta", "text": "hello"}},
    ]
    assert "eventId" not in frames[0]["payload"]


def test_yuanbao_output_frames_ignore_legacy_haha_cc_payload() -> None:
    payload = {
        "eventId": "evt_1",
        "type": "thinking",
        "payload": {"text": "plan"},
        "hahaCc": {"type": "thinking", "text": "plan"},
    }

    assert yuanbao_message_from_event_payload(payload) is None
    assert to_yuanbao_output_frames(payload) == []


def test_collect_yuanbao_server_messages_uses_same_flat_extraction_rules() -> None:
    events = [
        {"sequence": 7, "yuanbao": {"type": "content_delta", "text": "hello"}},
        {"sequence": 8, "type": "provider.request", "payload": {"model": "test"}},
        {"sequence": 9, "hahaCc": {"type": "thinking", "text": "plan"}},
    ]

    assert collect_yuanbao_server_messages(events, after_seq=3) == {
        "messages": [
            {"type": "content_delta", "text": "hello"},
        ],
        "lastSeq": 9,
    }


def test_yuanbao_adapter_maps_special_chat_events_to_system_notifications() -> None:
    assert to_yuanbao_server_message(_event("init", {"message": "Session ready"})) == {
        "type": "system_notification",
        "subtype": "init",
        "message": "Session ready",
        "data": {"message": "Session ready"},
    }
    assert to_yuanbao_server_message(
        _event("compact_summary", {"summary": "Context compacted", "phase": "completed"})
    ) == {
        "type": "system_notification",
        "subtype": "compact_summary",
        "message": "Context compacted",
        "data": {"summary": "Context compacted", "phase": "completed"},
    }
    assert to_yuanbao_server_message(_event("goal_event", {"message": "Goal complete", "status": "complete"})) is None
    assert to_yuanbao_server_message(_event("memory_event", {"message": "Saved MEMORY.md"})) is None
    assert to_yuanbao_server_message(_event("compact_boundary", {"summary": "Compaction boundary"})) == {
        "type": "system_notification",
        "subtype": "compact_boundary",
        "message": "Compaction boundary",
        "data": {"summary": "Compaction boundary"},
    }
    assert to_yuanbao_server_message(_event("session_state_changed", {"state": "ready"})) == {
        "type": "system_notification",
        "subtype": "session_state_changed",
        "message": "ready",
        "data": {"state": "ready"},
    }
    assert to_yuanbao_server_message(_event("task_started", {"summary": "Task started"})) == {
        "type": "system_notification",
        "subtype": "task_started",
        "message": "Task started",
        "data": {"summary": "Task started"},
    }
    assert (
        to_yuanbao_server_message(
            _event(
                "plan_update",
                {
                    "summary": "Ready to dispatch agents",
                    "plan": [{"id": "inspect", "title": "Inspect workflow", "status": "active"}],
                },
            )
        )
        is None
    )
    assert (
        to_yuanbao_server_message(
            _event(
                "task_summary",
                {
                    "summary": "Legacy task summary should not become chat output",
                    "resultSummary": "done",
                },
            )
        )
        is None
    )
    assert (
        to_haha_cc_server_message(
            _event(
                "task_summary",
                {
                    "summary": "Legacy task summary should not become chat output",
                    "resultSummary": "done",
                },
            )
        )
        is None
    )
    assert to_yuanbao_server_message(
        _event(
            "system_notification",
            {
                "title": "模型配置已切换",
                "summary": "Switched to fallback model",
                "phase": "provider_preflight",
                "model": "fallback-model",
            },
        )
    ) == {
        "type": "system_notification",
        "subtype": "session_state_changed",
        "message": "Switched to fallback model",
        "data": {
            "title": "模型配置已切换",
            "summary": "Switched to fallback model",
            "phase": "provider_preflight",
            "model": "fallback-model",
        },
    }
    assert to_yuanbao_server_message(
        _event("system_notification", {"subtype": "custom_local", "summary": "Generic notice"})
    ) == {
        "type": "system_notification",
        "subtype": "task_progress",
        "message": "Generic notice",
        "data": {"subtype": "custom_local", "summary": "Generic notice"},
    }


def test_yuanbao_adapter_does_not_map_backend_assistant_progress_to_flat_output() -> None:
    assert to_yuanbao_server_message(
        _event("assistant_progress", {"summary": "Reading files", "phase": "inspect"})
    ) is None


def test_yuanbao_adapter_keeps_raw_tool_progress_out_of_flat_chat_protocol() -> None:
    assert to_yuanbao_server_message(
        _event("tool.progress", {"toolName": "read_file", "message": "Read package.json"})
    ) is None
    assert to_yuanbao_server_message(
        _event("command.output", {"commandId": "cmd_1", "stream": "stdout", "chunk": "pytest passed"})
    ) is None


def test_yuanbao_adapter_does_not_flatten_raw_tool_output() -> None:
    chunk = "x" * 600
    message = to_yuanbao_server_message(_event("tool.output", {"toolName": "run_command", "chunk": chunk}))

    assert message is None


def test_yuanbao_output_frames_golden_sequence_for_typical_chat_turn() -> None:
    events = [
        _event("connected", {"sessionId": "sess_1"}),
        _event("status", {"state": "thinking", "verb": "plan", "phase": "local-only", "_chatCompat": True}),
        _event("message.created", {"message": {"id": "msg_1", "role": "assistant", "content": ""}}),
        _event("content_start", {"blockType": "text", "messageId": "msg_1", "_chatCompat": True}),
        _event("content_delta", {"text": "Hi", "messageId": "msg_1", "_chatCompat": True}),
        _event(
            "message_complete",
            {
                "_chatCompat": True,
                "usage": {
                    "inputTokens": 10,
                    "outputTokens": 2,
                    "cacheReadTokens": 4,
                    "cacheCreationTokens": 1,
                }
            },
        ),
    ]
    payloads = [
        {
            "eventId": event.event_id,
            "sessionId": event.session_id,
            "taskId": event.task_id,
        "type": event.type,
        "ts": event.ts,
        "payload": event.payload,
        "visibility": event.visibility,
        "yuanbao": to_yuanbao_server_message(event),
    }
        for event in events
    ]

    flat_messages = [
        frame["payload"]
        for payload in payloads
        for frame in to_yuanbao_output_frames(payload)
        if frame["kind"] == "yuanbao_message"
    ]

    assert flat_messages == [
        {"type": "connected", "sessionId": "sess_1"},
        {"type": "status", "state": "thinking", "verb": "plan"},
        {"type": "content_start", "blockType": "text"},
        {"type": "content_delta", "text": "Hi"},
        {
            "type": "message_complete",
            "usage": {
                "input_tokens": 10,
                "output_tokens": 2,
                "cache_read_tokens": 4,
                "cache_creation_tokens": 1,
            },
        },
    ]
    assert collect_yuanbao_server_messages(
        [
            {**payload, "sequence": index + 1}
            for index, payload in enumerate(payloads)
        ],
        after_seq=0,
    ) == {
        "messages": flat_messages,
        "lastSeq": 6,
    }
    assert all("eventId" not in message for message in flat_messages)
    assert all("messageId" not in message for message in flat_messages)


def test_yuanbao_adapter_covers_all_core_server_message_types() -> None:
    messages = [
        to_yuanbao_server_message(_event("connected", {"sessionId": "sess_1"})),
        to_yuanbao_server_message(_event("content_start", {"blockType": "tool_use", "toolName": "read_file", "toolUseId": "call_1", "_chatCompat": True})),
        to_yuanbao_server_message(_event("content_delta", {"text": "hello", "_chatCompat": True})),
        to_yuanbao_server_message(_event("tool_use_complete", {"toolName": "read_file", "toolUseId": "call_1", "input": {"path": "README.md"}, "_chatCompat": True})),
        to_yuanbao_server_message(_event("tool_result", {"toolUseId": "call_1", "content": "ok", "isError": False, "_chatCompat": True})),
        to_yuanbao_server_message(_event("permission_request", {"requestId": "approval_1", "toolName": "run_command", "input": {"command": "pytest"}, "_chatCompat": True})),
        to_yuanbao_server_message(_event("computer_use_permission_request", {"requestId": "approval_2", "request": {"action": "click"}, "_chatCompat": True})),
        to_yuanbao_server_message(_event("message_complete", {"usage": {"inputTokens": 1, "outputTokens": 2}, "_chatCompat": True})),
        to_yuanbao_server_message(_event("thinking", {"text": "plan", "_chatCompat": True})),
        to_yuanbao_server_message(_event("api_retry", {"attempt": 1, "maxRetries": 3, "retryDelayMs": 250, "errorStatus": 429, "_chatCompat": True})),
        to_yuanbao_server_message(_event("message.failed", {"content": "failed", "errorCode": "MODEL_ERROR"})),
        to_yuanbao_server_message(_event("system_notification", {"summary": "notice"})),
        to_yuanbao_server_message(_event("pong", {})),
        to_yuanbao_server_message(_event("collab.team.created", {"teamName": "docs"})),
        to_yuanbao_server_message(_event("collab.task.created", {"task": {"id": "child_1", "sessionId": "sess_1", "title": "Inspect", "status": "queued"}})),
        to_yuanbao_server_message(_event("collab.team.deleted", {"teamName": "docs"})),
        to_yuanbao_server_message(_event("task.runtime_work_waiting", {"taskId": "task_1", "status": "running", "detail": "Waiting"})),
        to_yuanbao_server_message(_event("session.updated", {"sessionId": "sess_1", "title": "New title", "changedFields": ["title"]})),
    ]

    assert [message["type"] for message in messages if message is not None] == [
        "connected",
        "content_start",
        "content_delta",
        "tool_use_complete",
        "tool_result",
        "permission_request",
        "computer_use_permission_request",
        "message_complete",
        "thinking",
        "api_retry",
        "error",
        "system_notification",
        "pong",
        "team_created",
        "team_update",
        "team_deleted",
        "task_update",
        "session_title_updated",
    ]
    assert all(message is not None for message in messages)


def test_runtime_work_waiting_is_task_update_not_permission_request() -> None:
    message = to_yuanbao_server_message(
        _event(
            "task.runtime_work_waiting",
            {
                "status": "running",
                "goal": "Finish cleanup",
                "detail": "Completion is waiting for runtime work to settle.",
                "completionGate": {"status": "waiting_runtime_work"},
            },
        )
    )

    assert message == {
        "type": "task_update",
        "taskId": "finish-cleanup",
        "taskLabel": "Finish cleanup",
        "status": "running",
        "progress": "Completion is waiting for runtime work to settle.",
    }


def test_root_task_updates_do_not_leak_into_flat_chat_protocol() -> None:
    assert (
        to_yuanbao_server_message(
            _event(
                "task.updated",
                {
                    "status": "running",
                    "goal": "Inspect repo",
                    "currentStep": "Reading README.md",
                },
            )
        )
        is None
    )
    assert to_yuanbao_server_message(_event("task.completed", {"status": "completed", "summary": "done"})) is None


def test_yuanbao_adapter_maps_collaboration_snapshot_to_stable_team_update() -> None:
    message = to_yuanbao_server_message(
        _event(
            "collab.task.completed",
            {
                "team": {
                    "teamName": "sess_1",
                    "tasks": [
                        {
                            "id": "child_1",
                            "title": "Write docs",
                            "status": "completed",
                            "assignedWorkerId": "worker_1",
                            "result": {"summary": "Docs finished."},
                            "metadata": {"agentType": "writer"},
                        },
                        {
                            "id": "child_2",
                            "title": "Review docs",
                            "status": "running",
                            "assignedWorkerId": "worker_2",
                            "metadata": {"agentType": "reviewer"},
                        },
                    ],
                    "workers": [
                        {"id": "worker_1", "role": "writer", "status": "idle"},
                        {"id": "worker_2", "role": "reviewer", "status": "busy", "currentTaskId": "child_2"},
                    ],
                },
                "task": {"id": "child_1", "sessionId": "sess_1"},
            },
        )
    )

    assert message == {
        "type": "team_update",
        "teamName": "sess_1",
        "members": [
                {
                    "agentId": "writer",
                    "role": "writer",
                    "status": "completed",
                    "currentTask": "Docs finished.",
                },
                {
                    "agentId": "reviewer",
                    "role": "reviewer",
                    "status": "running",
                    "currentTask": "Review docs",
            },
        ],
    }


def test_yuanbao_adapter_keeps_team_lifecycle_distinct_from_task_timeline() -> None:
    assert to_yuanbao_server_message(_event("collab.team.created", {"teamName": "docs"})) == {
        "type": "team_created",
        "teamName": "docs",
    }
    assert to_yuanbao_server_message(_event("collab.team.deleted", {"teamName": "docs"})) == {
        "type": "team_deleted",
        "teamName": "docs",
    }
    assert to_yuanbao_server_message(
        _event(
            "collab.task.created",
            {
                "task": {
                    "id": "child_1",
                    "sessionId": "sess_1",
                    "title": "Inspect repo",
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
                    "agentId": "explorer",
                    "role": "explorer",
                    "status": "running",
                    "currentTask": "Inspect repo",
            }
        ],
    }
