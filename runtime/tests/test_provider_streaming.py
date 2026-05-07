from __future__ import annotations

import json
from typing import Any, Iterable

import pytest

from local_agent_runtime.provider.adapter import ProviderAdapter, ProviderAdapterError


def _adapter(http_stream: Any) -> ProviderAdapter:
    def fail_post(**_kwargs: Any) -> tuple[int, bytes]:
        raise AssertionError("non-streaming transport should not be used")

    return ProviderAdapter(
        config={
            "provider": {
                "mode": "openai-compatible",
                "apiKey": "sk-test",
                "baseUrl": "https://llm.example.test/v1",
                "model": "test-chat",
            }
        },
        http_post=fail_post,
        http_stream=http_stream,
    )


def _sse(payload: dict[str, Any] | str) -> bytes:
    data = payload if isinstance(payload, str) else json.dumps(payload)
    return f"data: {data}\n\n".encode("utf-8")


def test_content_chunks_stream_and_emit_final_response() -> None:
    calls: list[dict[str, Any]] = []

    def fake_stream(**kwargs: Any) -> tuple[int, Iterable[bytes]]:
        calls.append(kwargs)
        return 200, iter(
            [
                _sse({"id": "chatcmpl_1", "model": "test-chat", "choices": [{"delta": {"role": "assistant", "content": "Hel"}, "index": 0}]}),
                _sse({"id": "chatcmpl_1", "model": "test-chat", "choices": [{"delta": {"content": "lo"}, "index": 0, "finish_reason": "stop"}]}),
                _sse("[DONE]"),
            ]
        )

    events = list(_adapter(fake_stream).chat_stream(messages=[{"role": "user", "content": "hi"}]))

    assert [event["type"] for event in events] == [
        "content_delta",
        "content_delta",
        "finish_reason",
        "final",
    ]
    assert events[0]["delta"] == "Hel"
    assert events[1]["delta"] == "lo"
    assert events[2]["finish_reason"] == "stop"
    assert events[3]["response"] == {
        "message": {"role": "assistant", "content": "Hello", "tool_calls": []},
        "finish_reason": "stop",
        "raw": {"id": "chatcmpl_1", "model": "test-chat", "usage": None},
    }
    assert len(calls) == 1
    assert calls[0]["url"] == "https://llm.example.test/v1/chat/completions"
    assert calls[0]["headers"]["Accept"] == "text/event-stream"
    payload = json.loads(calls[0]["body"].decode("utf-8"))
    assert payload["stream"] is True


def test_tool_call_chunks_stream_and_arguments_are_merged() -> None:
    def fake_stream(**_kwargs: Any) -> tuple[int, Iterable[bytes]]:
        return 200, iter(
            [
                _sse(
                    {
                        "choices": [
                            {
                                "delta": {
                                    "tool_calls": [
                                        {
                                            "index": 0,
                                            "id": "call_1",
                                            "type": "function",
                                            "function": {"name": "search_files", "arguments": "{\"query\":\"nee"},
                                        }
                                    ]
                                },
                                "index": 0,
                            }
                        ]
                    }
                ),
                _sse(
                    {
                        "choices": [
                            {
                                "delta": {
                                    "tool_calls": [
                                        {"index": 0, "function": {"arguments": "dle\",\"max_results\":3}"}}
                                    ]
                                },
                                "index": 0,
                                "finish_reason": "tool_calls",
                            }
                        ]
                    }
                ),
                _sse("[DONE]"),
            ]
        )

    events = list(_adapter(fake_stream).chat_stream(messages=[{"role": "user", "content": "find needle"}]))

    tool_delta_events = [event for event in events if event["type"] == "tool_call_delta"]
    assert tool_delta_events == [
        {
            "type": "tool_call_delta",
            "index": 0,
            "id": "call_1",
            "tool_type": "function",
            "name": "search_files",
            "arguments_delta": "{\"query\":\"nee",
        },
        {
            "type": "tool_call_delta",
            "index": 0,
            "id": None,
            "tool_type": None,
            "name": None,
            "arguments_delta": "dle\",\"max_results\":3}",
        },
    ]
    assert events[-1] == {
        "type": "final",
        "response": {
            "message": {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "name": "search_files",
                        "arguments": {"query": "needle", "max_results": 3},
                    }
                ],
            },
            "finish_reason": "tool_calls",
            "raw": {"id": None, "model": None, "usage": None},
        },
    }


def test_streaming_tool_call_name_maps_back_to_runtime_name() -> None:
    calls: list[dict[str, Any]] = []

    def fake_stream(**kwargs: Any) -> tuple[int, Iterable[bytes]]:
        calls.append(kwargs)
        return 200, iter(
            [
                _sse(
                    {
                        "choices": [
                            {
                                "delta": {
                                    "tool_calls": [
                                        {
                                            "index": 0,
                                            "id": "call_1",
                                            "type": "function",
                                            "function": {"name": "memory_remember", "arguments": "{\"text\":\"keep\"}"},
                                        }
                                    ]
                                },
                                "index": 0,
                                "finish_reason": "tool_calls",
                            }
                        ]
                    }
                ),
                _sse("[DONE]"),
            ]
        )

    events = list(
        _adapter(fake_stream).chat_stream(
            messages=[{"role": "user", "content": "remember"}],
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "memory.remember",
                        "description": "Remember",
                        "parameters": {"type": "object"},
                    },
                }
            ],
        )
    )

    payload = json.loads(calls[0]["body"].decode("utf-8"))
    assert payload["tools"][0]["function"]["name"] == "memory_remember"
    assert events[-1]["response"]["message"]["tool_calls"][0]["name"] == "memory.remember"


def test_done_without_prior_finish_reason_still_emits_final_response() -> None:
    def fake_stream(**_kwargs: Any) -> tuple[int, Iterable[bytes]]:
        return 200, iter([_sse({"choices": [{"delta": {"content": "ok"}, "index": 0}]}), _sse("[DONE]")])

    events = list(_adapter(fake_stream).chat_stream(messages=[{"role": "user", "content": "hi"}]))

    assert events[-1] == {
        "type": "final",
        "response": {
            "message": {"role": "assistant", "content": "ok", "tool_calls": []},
            "finish_reason": None,
            "raw": {"id": None, "model": None, "usage": None},
        },
    }


def test_stream_retries_timeout_before_first_event() -> None:
    calls = 0

    def timeout_before_first_event() -> Iterable[bytes]:
        raise ProviderAdapterError("Provider stream timed out after 30s")
        yield b""

    def fake_stream(**_kwargs: Any) -> tuple[int, Iterable[bytes]]:
        nonlocal calls
        calls += 1
        if calls == 1:
            return 200, timeout_before_first_event()
        return 200, iter([_sse({"choices": [{"delta": {"content": "ok"}, "index": 0}]}), _sse("[DONE]")])

    events = list(_adapter(fake_stream).chat_stream(messages=[{"role": "user", "content": "hi"}]))

    assert calls == 2
    assert events[0] == {"type": "content_delta", "delta": "ok"}
    assert events[-1]["type"] == "final"


def test_stream_does_not_retry_after_emitting_content() -> None:
    calls = 0

    def timeout_after_content() -> Iterable[bytes]:
        yield _sse({"choices": [{"delta": {"content": "partial"}, "index": 0}]})
        raise ProviderAdapterError("Provider stream timed out after 30s")

    def fake_stream(**_kwargs: Any) -> tuple[int, Iterable[bytes]]:
        nonlocal calls
        calls += 1
        return 200, timeout_after_content()

    with pytest.raises(ProviderAdapterError, match="Provider stream timed out after 30s"):
        list(_adapter(fake_stream).chat_stream(messages=[{"role": "user", "content": "hi"}]))

    assert calls == 1


def test_invalid_sse_json_is_skipped_gracefully() -> None:
    """Malformed SSE data (e.g. truncated by network) should be skipped, not crash the stream."""
    def fake_stream(**_kwargs: Any) -> tuple[int, Iterable[bytes]]:
        return 200, iter([
            b"data: not-json\n\n",
            b'data: {"choices":[{"delta":{"content":"ok"},"index":0}]}\n\n',
        ])

    events = list(_adapter(fake_stream).chat_stream(messages=[{"role": "user", "content": "hi"}]))
    # Should get content from the valid chunk, skipping the malformed one
    content_events = [e for e in events if e.get("type") == "content_delta"]
    assert len(content_events) == 1
    assert content_events[0]["delta"] == "ok"


def test_streaming_http_error_raises_provider_adapter_error() -> None:
    def fake_stream(**_kwargs: Any) -> tuple[int, Iterable[bytes]]:
        return 500, iter([b'{"error":{"message":"server exploded"}}'])

    with pytest.raises(ProviderAdapterError, match="Provider request failed with HTTP 500: server exploded"):
        list(_adapter(fake_stream).chat_stream(messages=[{"role": "user", "content": "hi"}]))
