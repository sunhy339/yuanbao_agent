from __future__ import annotations

from local_agent_runtime.haha_cc_compat import to_haha_cc_server_message
from local_agent_runtime.models import RuntimeEvent
from local_agent_runtime.yuanbao_event_adapter import normalize_yuanbao_usage, to_yuanbao_server_message


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


def test_yuanbao_adapter_rejects_missing_required_fields() -> None:
    assert to_yuanbao_server_message(_event("content_start", {"toolName": "read_file"})) is None
    assert (
        to_yuanbao_server_message(_event("tool_result", {"toolUseId": "call_1", "content": "ok"}))
        is None
    )
    assert (
        to_yuanbao_server_message(_event("permission_request", {"requestId": "approval_1", "input": {}}))
        is None
    )


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
    event = _event("thinking", {"text": "plan"})

    assert to_haha_cc_server_message(event) == to_yuanbao_server_message(event)
