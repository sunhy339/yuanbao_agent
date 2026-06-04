from __future__ import annotations

import json
from typing import Any

import pytest

from local_agent_runtime.provider.adapter import ProviderAdapter, ProviderAdapterError
from local_agent_runtime.provider.failure_recovery import classify_provider_failure
from local_agent_runtime.orchestrator.provider_turn import ProviderTurnMixin


def _context() -> dict[str, Any]:
    return {
        "workspace_name": "demo",
        "workspace_root": "D:\\demo",
        "search_config": {"ignore": []},
        "config": {},
    }


def test_openai_compatible_without_api_key_raises_readable_error(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "OPENAI_API_KEY",
        "LOCAL_AGENT_OPENAI_API_KEY",
        "LOCAL_AGENT_PROVIDER_API_KEY",
        "MISSING_PROVIDER_KEY",
    ):
        monkeypatch.delenv(key, raising=False)

    def fail_post(**_kwargs: Any) -> tuple[int, bytes]:
        raise AssertionError("HTTP transport should not be used without an API key")

    adapter = ProviderAdapter(
        config={
            "provider": {
                "mode": "openai-compatible",
                "model": "real-model",
                "apiKeyEnvVarName": "MISSING_PROVIDER_KEY",
            }
        },
        http_post=fail_post,
    )

    with pytest.raises(ProviderAdapterError, match="MISSING_PROVIDER_KEY"):
        adapter.generate("inspect workspace", _context())


def test_mock_provider_summarizes_without_workspace_probe_by_default() -> None:
    adapter = ProviderAdapter(config={"provider": {"mode": "mock", "model": "mock-model"}})

    response = adapter.generate("inspect workspace", _context())

    assert response["message"] == "Completed the requested tool action."
    assert response["final"] == "Completed the requested tool action."
    assert response["finish_reason"] == "mock_final"
    assert response["prompt"] == "inspect workspace"


def test_mock_provider_leaves_opt_in_deterministic_fallback_to_orchestrator() -> None:
    adapter = ProviderAdapter(
        config={"provider": {"mode": "mock", "model": "mock-model", "deterministicFallback": True}}
    )

    response = adapter.generate("inspect workspace", _context())

    assert response["message"] == "Completed the requested tool action."
    assert "final" not in response
    assert "final_answer" not in response


def test_mock_provider_string_false_does_not_enable_deterministic_fallback() -> None:
    adapter = ProviderAdapter(
        config={"provider": {"mode": "mock", "model": "mock-model", "deterministicFallback": "false"}}
    )

    response = adapter.generate("inspect workspace", _context())

    assert response["final"] == "Completed the requested tool action."


@pytest.mark.parametrize(
    ("goal", "tool_name"),
    [
        ("run command: npm test", "run_command"),
        ("apply patch: *** Begin Patch", "apply_patch"),
        ("show git status", "git_status"),
        ("show git diff", "git_diff"),
    ],
)
def test_deterministic_fallback_does_not_probe_workspace_for_explicit_tools(goal: str, tool_name: str) -> None:
    adapter = ProviderAdapter(config={"provider": {"mode": "mock", "model": "mock-model"}})

    sequence = adapter.choose_tool_sequence(goal, _context())

    assert [item["name"] for item in sequence] == [tool_name]


def test_deterministic_fallback_can_be_enabled_for_workspace_probe() -> None:
    adapter = ProviderAdapter(
        config={"provider": {"mode": "mock", "model": "mock-model", "deterministicFallback": True}}
    )

    sequence = adapter.choose_tool_sequence("inspect workspace", _context())

    assert [item["name"] for item in sequence] == ["list_dir"]


def test_openai_compatible_request_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "LOCAL_AGENT_PROVIDER_MODEL",
        "OPENAI_MODEL",
        "ANTHROPIC_MODEL",
        "ANTHROPIC_DEFAULT_SONNET_MODEL",
        "ANTHROPIC_DEFAULT_OPUS_MODEL",
        "ANTHROPIC_DEFAULT_HAIKU_MODEL",
    ):
        monkeypatch.delenv(key, raising=False)

    calls: list[dict[str, Any]] = []

    def fake_post(**kwargs: Any) -> tuple[int, bytes]:
        calls.append(kwargs)
        return 200, json.dumps(
            {
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "plain answer"},
                        "finish_reason": "stop",
                    }
                ]
            }
        ).encode("utf-8")

    adapter = ProviderAdapter(
        config={
            "provider": {
                "mode": "openai-compatible",
                "apiKey": "sk-test",
                "baseUrl": "https://llm.example.test/v1",
                "model": "test-chat",
                "temperature": 0.7,
                "maxTokens": 123,
                "timeout": 9,
            }
        },
        http_post=fake_post,
    )
    tools = [
        {
            "type": "function",
            "function": {
                "name": "search_files",
                "description": "Search files",
                "parameters": {"type": "object"},
            },
        }
    ]

    response = adapter.chat(messages=[{"role": "user", "content": "hi"}], tools=tools)

    assert response["message"]["content"] == "plain answer"
    assert len(calls) == 1
    call = calls[0]
    assert call["url"] == "https://llm.example.test/v1/chat/completions"
    assert call["timeout"] == 120.0
    assert call["headers"]["Authorization"] == "Bearer sk-test"
    assert call["headers"]["Content-Type"] == "application/json"
    payload = json.loads(call["body"].decode("utf-8"))
    assert payload == {
        "model": "test-chat",
        "messages": [{"role": "user", "content": "hi"}],
        "temperature": 0.7,
        "max_tokens": 123,
        "tools": tools,
    }


def test_openai_compatible_response_reasoning_content_becomes_thought_summary() -> None:
    def fake_post(**_kwargs: Any) -> tuple[int, bytes]:
        return 200, json.dumps(
            {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "reasoning_content": "Inspect files before answering.",
                            "content": "Done",
                        },
                        "finish_reason": "stop",
                    }
                ]
            }
        ).encode("utf-8")

    adapter = ProviderAdapter(
        config={
            "provider": {
                "mode": "openai-compatible",
                "apiKey": "sk-test",
                "baseUrl": "https://llm.example.test/v1",
                "model": "test-chat",
            }
        },
        http_post=fake_post,
    )

    response = adapter.chat(messages=[{"role": "user", "content": "hi"}])

    assert response["message"]["content"] == "Done"
    assert response["thought_summary"] == "Inspect files before answering."
    assert response["thoughtSummary"] == "Inspect files before answering."


def test_openai_chat_serializes_image_attachments(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "LOCAL_AGENT_PROVIDER_MODEL",
        "OPENAI_MODEL",
        "ANTHROPIC_MODEL",
        "ANTHROPIC_DEFAULT_SONNET_MODEL",
        "ANTHROPIC_DEFAULT_OPUS_MODEL",
        "ANTHROPIC_DEFAULT_HAIKU_MODEL",
    ):
        monkeypatch.delenv(key, raising=False)

    calls: list[dict[str, Any]] = []

    def fake_post(**kwargs: Any) -> tuple[int, bytes]:
        calls.append(kwargs)
        return 200, b'{"choices":[{"message":{"role":"assistant","content":"ok"}}]}'

    adapter = ProviderAdapter(
        config={
            "provider": {
                "mode": "openai-compatible",
                "apiKey": "sk-test",
                "baseUrl": "https://llm.example.test/v1",
                "model": "test-chat",
            }
        },
        http_post=fake_post,
    )

    adapter.chat(
        messages=[
            {
                "role": "user",
                "content": "inspect this",
                "imageAttachments": [
                    {"source": "base64", "mimeType": "image/png", "data": "abc123"},
                ],
            }
        ]
    )

    payload = json.loads(calls[0]["body"].decode("utf-8"))
    assert payload["messages"] == [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "inspect this"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc123"}},
            ],
        }
    ]


def test_openai_chat_preserves_context_prefix_message_boundaries() -> None:
    calls: list[dict[str, Any]] = []

    def fake_post(**kwargs: Any) -> tuple[int, bytes]:
        calls.append(kwargs)
        return 200, b'{"choices":[{"message":{"role":"assistant","content":"ok"}}]}'

    adapter = ProviderAdapter(
        config={
            "provider": {
                "mode": "openai-compatible",
                "apiKey": "sk-test",
                "baseUrl": "https://llm.example.test/v1",
                "model": "test-chat",
            }
        },
        http_post=fake_post,
    )

    adapter.chat(
        messages=[
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "Stable context prefix:\nstable"},
            {"role": "user", "content": "Dynamic context tail:\ndynamic"},
            {"role": "user", "content": "Current user request:\ncontinue"},
        ]
    )

    payload = json.loads(calls[0]["body"].decode("utf-8"))
    assert payload["messages"] == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "Stable context prefix:\nstable"},
        {"role": "user", "content": "Dynamic context tail:\ndynamic"},
        {"role": "user", "content": "Current user request:\ncontinue"},
    ]


def test_openai_compatible_http_error_with_html_body_is_reported_before_json_decode() -> None:
    def fake_post(**_kwargs: Any) -> tuple[int, bytes]:
        return 502, "<html><title>网站请求超时</title></html>".encode("utf-8")

    adapter = ProviderAdapter(
        config={
            "provider": {
                "mode": "openai-compatible",
                "apiKey": "sk-test",
                "baseUrl": "https://llm.example.test/v1",
                "model": "test-chat",
            }
        },
        http_post=fake_post,
    )

    with pytest.raises(ProviderAdapterError) as exc_info:
        adapter.chat(messages=[{"role": "user", "content": "hi"}])

    message = str(exc_info.value)
    assert "HTTP 502" in message
    assert "网站请求超时" in message
    assert "invalid JSON" not in message


def test_openai_compatible_maps_unsafe_tool_names_round_trip() -> None:
    calls: list[dict[str, Any]] = []

    def fake_post(**kwargs: Any) -> tuple[int, bytes]:
        calls.append(kwargs)
        return 200, json.dumps(
            {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {"name": "memory_remember", "arguments": "{\"text\":\"keep\"}"},
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ]
            }
        ).encode("utf-8")

    adapter = ProviderAdapter(
        config={
            "provider": {
                "mode": "openai-compatible",
                "apiKey": "sk-test",
                "baseUrl": "https://llm.example.test/v1",
                "model": "test-chat",
            }
        },
        http_post=fake_post,
    )
    response = adapter.chat(
        messages=[{"role": "user", "content": "remember this"}],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "memory.remember",
                    "description": "Remember",
                    "parameters": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {"text": {"type": "string", "default": ""}},
                        "required": ["text"],
                        "oneOf": [{"required": ["text"]}],
                    },
                },
            }
        ],
    )

    payload = json.loads(calls[0]["body"].decode("utf-8"))
    assert payload["tools"][0]["function"]["name"] == "memory_remember"
    parameters = payload["tools"][0]["function"]["parameters"]
    assert "additionalProperties" not in parameters
    assert "oneOf" not in parameters
    assert "default" not in parameters["properties"]["text"]
    assert response["message"]["tool_calls"][0]["name"] == "memory.remember"


def test_openai_compatible_serializes_internal_tool_messages_for_request() -> None:
    calls: list[dict[str, Any]] = []

    def fake_post(**kwargs: Any) -> tuple[int, bytes]:
        calls.append(kwargs)
        return 200, b'{"choices":[{"message":{"role":"assistant","content":"done"},"finish_reason":"stop"}]}'

    adapter = ProviderAdapter(
        config={
            "provider": {
                "mode": "openai-compatible",
                "apiKey": "sk-test",
                "baseUrl": "https://llm.example.test/v1",
                "model": "test-chat",
            }
        },
        http_post=fake_post,
    )

    adapter.chat(
        messages=[
            {"role": "user", "content": "find files"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_search",
                        "type": "function",
                        "name": "search_files",
                        "arguments": {"query": "needle"},
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call_search",
                "name": "search_files",
                "content": '{"matches":[]}',
            },
        ]
    )

    payload = json.loads(calls[0]["body"].decode("utf-8"))
    assert payload["messages"] == [
        {"role": "user", "content": "find files"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_search",
                    "type": "function",
                    "function": {
                        "name": "search_files",
                        "arguments": '{"query": "needle"}',
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call_search",
            "content": '{"matches":[]}',
        },
    ]


def test_openai_compatible_drops_orphan_tool_messages_from_request() -> None:
    calls: list[dict[str, Any]] = []

    def fake_post(**kwargs: Any) -> tuple[int, bytes]:
        calls.append(kwargs)
        return 200, b'{"choices":[{"message":{"role":"assistant","content":"done"},"finish_reason":"stop"}]}'

    adapter = ProviderAdapter(
        config={
            "provider": {
                "mode": "openai-compatible",
                "apiKey": "sk-test",
                "baseUrl": "https://llm.example.test/v1",
                "model": "test-chat",
            }
        },
        http_post=fake_post,
    )

    adapter.chat(
        messages=[
            {"role": "user", "content": "find files"},
            {
                "role": "tool",
                "tool_call_id": "call_missing",
                "name": "search_files",
                "content": '{"matches":[]}',
            },
        ]
    )

    payload = json.loads(calls[0]["body"].decode("utf-8"))
    assert payload["messages"] == [{"role": "user", "content": "find files"}]


def test_openai_compatible_request_can_be_configured_from_env() -> None:
    calls: list[dict[str, Any]] = []

    def fake_post(**kwargs: Any) -> tuple[int, bytes]:
        calls.append(kwargs)
        return 200, b'{"choices":[{"message":{"role":"assistant","content":"env answer"}}]}'

    adapter = ProviderAdapter(
        http_post=fake_post,
        environ={
            "LOCAL_AGENT_PROVIDER_MODE": "openai-compatible",
            "LOCAL_AGENT_PROVIDER_API_KEY": "sk-env",
            "LOCAL_AGENT_PROVIDER_BASE_URL": "https://env.example.test/v1",
            "LOCAL_AGENT_PROVIDER_MODEL": "env-chat",
            "LOCAL_AGENT_PROVIDER_TEMPERATURE": "0.4",
            "LOCAL_AGENT_PROVIDER_MAX_TOKENS": "321",
            "LOCAL_AGENT_PROVIDER_TIMEOUT": "12",
        },
    )

    response = adapter.chat(messages=[{"role": "user", "content": "hi"}])

    assert response["message"]["content"] == "env answer"
    call = calls[0]
    assert call["url"] == "https://env.example.test/v1/chat/completions"
    assert call["timeout"] == 12
    assert call["headers"]["Authorization"] == "Bearer sk-env"
    payload = json.loads(call["body"].decode("utf-8"))
    assert payload["model"] == "env-chat"
    assert payload["temperature"] == 0.4
    assert payload["max_tokens"] == 321


def test_provider_env_overrides_stored_mock_defaults() -> None:
    calls: list[dict[str, Any]] = []

    def fake_post(**kwargs: Any) -> tuple[int, bytes]:
        calls.append(kwargs)
        return 200, b'{"choices":[{"message":{"role":"assistant","content":"env override"}}]}'

    adapter = ProviderAdapter(
        config={
            "provider": {
                "mode": "mock",
                "apiKey": "sk-config",
                "baseUrl": "https://configured.example.test/v1",
                "model": "configured-model",
            }
        },
        http_post=fake_post,
        environ={
            "LOCAL_AGENT_PROVIDER_MODE": "openai-compatible",
            "LOCAL_AGENT_PROVIDER_API_KEY": "sk-env",
            "LOCAL_AGENT_PROVIDER_BASE_URL": "https://env.example.test/v1",
            "LOCAL_AGENT_PROVIDER_MODEL": "env-model",
        },
    )

    response = adapter.chat(messages=[{"role": "user", "content": "hi"}])

    assert response["message"]["content"] == "env override"
    assert calls[0]["url"] == "https://env.example.test/v1/chat/completions"
    assert calls[0]["headers"]["Authorization"] == "Bearer sk-env"
    payload = json.loads(calls[0]["body"].decode("utf-8"))
    assert payload["model"] == "env-model"


def test_explicit_provider_config_takes_precedence_over_global_env() -> None:
    calls: list[dict[str, Any]] = []

    def fake_post(**kwargs: Any) -> tuple[int, bytes]:
        calls.append(kwargs)
        return 200, b'{"choices":[{"message":{"role":"assistant","content":"configured"}}]}'

    adapter = ProviderAdapter(
        config={
            "provider": {
                "mode": "openai-compatible",
                "apiKey": "sk-config",
                "baseUrl": "https://configured.example.test/v1",
                "model": "configured-model",
            }
        },
        http_post=fake_post,
        environ={
            "LOCAL_AGENT_PROVIDER_MODE": "openai-compatible",
            "LOCAL_AGENT_PROVIDER_API_KEY": "sk-env",
            "LOCAL_AGENT_PROVIDER_BASE_URL": "https://env.example.test/v1",
            "LOCAL_AGENT_PROVIDER_MODEL": "env-model",
        },
    )

    response = adapter.chat(messages=[{"role": "user", "content": "hi"}])

    assert response["message"]["content"] == "configured"
    assert calls[0]["url"] == "https://configured.example.test/v1/chat/completions"
    assert calls[0]["headers"]["Authorization"] == "Bearer sk-config"
    payload = json.loads(calls[0]["body"].decode("utf-8"))
    assert payload["model"] == "configured-model"


def test_openai_compatible_bare_base_url_uses_v1_chat_completions() -> None:
    calls: list[dict[str, Any]] = []

    def fake_post(**kwargs: Any) -> tuple[int, bytes]:
        calls.append(kwargs)
        return 200, b'{"choices":[{"message":{"role":"assistant","content":"ok"}}]}'

    adapter = ProviderAdapter(
        config={
            "provider": {
                "mode": "openai-compatible",
                "apiKey": "sk-test",
                "baseUrl": "https://llm.example.test",
                "model": "test-chat",
            }
        },
        http_post=fake_post,
    )

    adapter.chat(messages=[{"role": "user", "content": "hi"}])

    assert calls[0]["url"] == "https://llm.example.test/v1/chat/completions"


def test_provider_api_format_aliases_use_openai_chat_adapter() -> None:
    calls: list[dict[str, Any]] = []

    def fake_post(**kwargs: Any) -> tuple[int, bytes]:
        calls.append(kwargs)
        return 200, b'{"choices":[{"message":{"role":"assistant","content":"ok"}}]}'

    adapter = ProviderAdapter(
        config={
            "provider": {
                "mode": "openai-compatible",
                "apiKey": "sk-test",
                "baseUrl": "https://llm.example.test/v1",
                "apiFormat": "chat-completions",
                "model": "test-chat",
            }
        },
        http_post=fake_post,
    )

    adapter.chat(messages=[{"role": "user", "content": "hi"}])

    assert calls[0]["url"] == "https://llm.example.test/v1/chat/completions"


def test_openai_responses_api_format_posts_responses_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "LOCAL_AGENT_PROVIDER_MODEL",
        "OPENAI_MODEL",
        "ANTHROPIC_MODEL",
        "ANTHROPIC_DEFAULT_SONNET_MODEL",
        "ANTHROPIC_DEFAULT_OPUS_MODEL",
        "ANTHROPIC_DEFAULT_HAIKU_MODEL",
    ):
        monkeypatch.delenv(key, raising=False)

    calls: list[dict[str, Any]] = []

    def fake_post(**kwargs: Any) -> tuple[int, bytes]:
        calls.append(kwargs)
        return 200, json.dumps(
            {
                "id": "resp_1",
                "model": "test-responses",
                "status": "completed",
                "output_text": "responses ok",
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "responses ok"}],
                    }
                ],
                "usage": {"total_tokens": 9},
            }
        ).encode("utf-8")

    adapter = ProviderAdapter(
        config={
            "provider": {
                "mode": "openai-compatible",
                "apiKey": "sk-test",
                "baseUrl": "https://llm.example.test/v1",
                "apiFormat": "openai-responses",
                "model": "test-chat",
            }
        },
        http_post=fake_post,
    )

    response = adapter.chat(messages=[{"role": "user", "content": "hi"}])

    assert response["message"]["content"] == "responses ok"
    assert response["finish_reason"] == "completed"
    call = calls[0]
    assert call["url"] == "https://llm.example.test/v1/responses"
    assert call["headers"]["Authorization"] == "Bearer sk-test"
    payload = json.loads(call["body"].decode("utf-8"))
    assert payload["model"] == "test-chat"
    assert payload["input"] == [{"role": "user", "content": "hi"}]


def test_openai_responses_serializes_image_attachments(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "LOCAL_AGENT_PROVIDER_MODEL",
        "OPENAI_MODEL",
        "ANTHROPIC_MODEL",
        "ANTHROPIC_DEFAULT_SONNET_MODEL",
        "ANTHROPIC_DEFAULT_OPUS_MODEL",
        "ANTHROPIC_DEFAULT_HAIKU_MODEL",
    ):
        monkeypatch.delenv(key, raising=False)

    calls: list[dict[str, Any]] = []

    def fake_post(**kwargs: Any) -> tuple[int, bytes]:
        calls.append(kwargs)
        return 200, json.dumps(
            {
                "id": "resp_1",
                "model": "test-responses",
                "status": "completed",
                "output_text": "ok",
                "output": [],
            }
        ).encode("utf-8")

    adapter = ProviderAdapter(
        config={
            "provider": {
                "mode": "openai-compatible",
                "apiKey": "sk-test",
                "baseUrl": "https://llm.example.test/v1",
                "apiFormat": "openai-responses",
                "model": "test-chat",
            }
        },
        http_post=fake_post,
    )

    adapter.chat(
        messages=[
            {
                "role": "user",
                "content": "inspect this",
                "imageAttachments": [
                    {"source": "base64", "mimeType": "image/jpeg", "data": "jpgdata"},
                ],
            }
        ]
    )

    payload = json.loads(calls[0]["body"].decode("utf-8"))
    assert payload["input"] == [
        {
            "role": "user",
            "content": [
                {"type": "input_text", "text": "inspect this"},
                {"type": "input_image", "image_url": "data:image/jpeg;base64,jpgdata"},
            ],
        }
    ]


def test_openai_responses_preserves_context_prefix_message_boundaries() -> None:
    calls: list[dict[str, Any]] = []

    def fake_post(**kwargs: Any) -> tuple[int, bytes]:
        calls.append(kwargs)
        return 200, b'{"id":"resp_1","status":"completed","output_text":"ok","output":[]}'

    adapter = ProviderAdapter(
        config={
            "provider": {
                "mode": "openai-compatible",
                "apiKey": "sk-test",
                "baseUrl": "https://llm.example.test/v1",
                "apiFormat": "openai-responses",
                "model": "test-chat",
            }
        },
        http_post=fake_post,
    )

    adapter.chat(
        messages=[
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "Stable context prefix:\nstable"},
            {"role": "user", "content": "Dynamic context tail:\ndynamic"},
            {"role": "user", "content": "Current user request:\ncontinue"},
        ]
    )

    payload = json.loads(calls[0]["body"].decode("utf-8"))
    assert payload["input"] == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "Stable context prefix:\nstable"},
        {"role": "user", "content": "Dynamic context tail:\ndynamic"},
        {"role": "user", "content": "Current user request:\ncontinue"},
    ]


def test_openai_responses_tool_call_response_is_normalized() -> None:
    calls: list[dict[str, Any]] = []

    def fake_post(**kwargs: Any) -> tuple[int, bytes]:
        calls.append(kwargs)
        return 200, json.dumps(
            {
                "id": "resp_2",
                "model": "test-responses",
                "status": "requires_action",
                "output": [
                    {
                        "type": "function_call",
                        "call_id": "call_1",
                        "parentToolUseId": "call_parent",
                        "name": "workspace_read",
                        "arguments": "{\"path\":\"README.md\"}",
                    }
                ],
            }
        ).encode("utf-8")

    adapter = ProviderAdapter(
        config={
            "provider": {
                "mode": "openai-compatible",
                "apiKey": "sk-test",
                "baseUrl": "https://llm.example.test/v1",
                "apiFormat": "openai-responses",
                "model": "test-chat",
            }
        },
        http_post=fake_post,
    )

    response = adapter.chat(
        messages=[{"role": "user", "content": "read"}],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "workspace.read",
                    "description": "Read a file",
                    "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
                },
            }
        ],
    )

    payload = json.loads(calls[0]["body"].decode("utf-8"))
    assert payload["tools"][0]["name"] == "workspace_read"
    assert response["message"]["tool_calls"] == [
        {
            "id": "call_1",
            "type": "function",
            "name": "workspace.read",
            "arguments": {"path": "README.md"},
            "parentToolUseId": "call_parent",
        }
    ]


def test_openai_responses_drops_orphan_tool_messages_from_request() -> None:
    calls: list[dict[str, Any]] = []

    def fake_post(**kwargs: Any) -> tuple[int, bytes]:
        calls.append(kwargs)
        return 200, json.dumps(
            {
                "id": "resp_3",
                "model": "test-responses",
                "status": "completed",
                "output_text": "ok",
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "ok"}],
                    }
                ],
            }
        ).encode("utf-8")

    adapter = ProviderAdapter(
        config={
            "provider": {
                "mode": "openai-compatible",
                "apiKey": "sk-test",
                "baseUrl": "https://llm.example.test/v1",
                "apiFormat": "openai-responses",
                "model": "test-chat",
            }
        },
        http_post=fake_post,
    )

    adapter.chat(
        messages=[
            {"role": "user", "content": "read"},
            {"role": "tool", "tool_call_id": "call_missing", "content": '{"status":"completed"}'},
        ],
    )

    payload = json.loads(calls[0]["body"].decode("utf-8"))
    assert payload["input"] == [{"role": "user", "content": "read"}]


def test_anthropic_messages_api_format_posts_messages_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "LOCAL_AGENT_PROVIDER_MODEL",
        "OPENAI_MODEL",
        "ANTHROPIC_MODEL",
        "ANTHROPIC_DEFAULT_SONNET_MODEL",
        "ANTHROPIC_DEFAULT_OPUS_MODEL",
        "ANTHROPIC_DEFAULT_HAIKU_MODEL",
    ):
        monkeypatch.delenv(key, raising=False)

    calls: list[dict[str, Any]] = []

    def fake_post(**kwargs: Any) -> tuple[int, bytes]:
        calls.append(kwargs)
        return 200, json.dumps(
            {
                "id": "msg_1",
                "model": "claude-test",
                "role": "assistant",
                "stop_reason": "end_turn",
                "content": [{"type": "text", "text": "anthropic ok"}],
                "usage": {"input_tokens": 5, "output_tokens": 3},
            }
        ).encode("utf-8")

    adapter = ProviderAdapter(
        config={
            "provider": {
                "mode": "openai-compatible",
                "apiKey": "sk-ant",
                "baseUrl": "https://api.anthropic.test",
                "apiFormat": "anthropic-messages",
                "model": "claude-test",
                "maxTokens": 777,
            }
        },
        http_post=fake_post,
    )

    response = adapter.chat(
        messages=[
            {"role": "system", "content": "be brief"},
            {"role": "user", "content": "hi"},
        ]
    )

    assert response["message"]["content"] == "anthropic ok"
    assert response["finish_reason"] == "end_turn"
    call = calls[0]
    assert call["url"] == "https://api.anthropic.test/v1/messages"
    assert call["headers"]["x-api-key"] == "sk-ant"
    assert call["headers"]["anthropic-version"] == "2023-06-01"
    payload = json.loads(call["body"].decode("utf-8"))
    assert payload["model"] == "claude-test"
    assert payload["max_tokens"] == 777
    assert payload["system"] == "be brief"
    assert payload["messages"] == [{"role": "user", "content": "hi"}]


def test_anthropic_messages_serializes_image_attachments(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "LOCAL_AGENT_PROVIDER_MODEL",
        "OPENAI_MODEL",
        "ANTHROPIC_MODEL",
        "ANTHROPIC_DEFAULT_SONNET_MODEL",
        "ANTHROPIC_DEFAULT_OPUS_MODEL",
        "ANTHROPIC_DEFAULT_HAIKU_MODEL",
    ):
        monkeypatch.delenv(key, raising=False)

    calls: list[dict[str, Any]] = []

    def fake_post(**kwargs: Any) -> tuple[int, bytes]:
        calls.append(kwargs)
        return 200, json.dumps(
            {
                "id": "msg_1",
                "model": "claude-test",
                "role": "assistant",
                "stop_reason": "end_turn",
                "content": [{"type": "text", "text": "ok"}],
            }
        ).encode("utf-8")

    adapter = ProviderAdapter(
        config={
            "provider": {
                "mode": "openai-compatible",
                "apiKey": "sk-ant",
                "baseUrl": "https://api.anthropic.test",
                "apiFormat": "anthropic-messages",
                "model": "claude-test",
                "maxTokens": 777,
            }
        },
        http_post=fake_post,
    )

    adapter.chat(
        messages=[
            {
                "role": "user",
                "content": "inspect this",
                "imageAttachments": [
                    {"source": "base64", "mimeType": "image/webp", "data": "webpdata"},
                ],
            }
        ]
    )

    payload = json.loads(calls[0]["body"].decode("utf-8"))
    assert payload["messages"] == [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "inspect this"},
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/webp",
                        "data": "webpdata",
                    },
                },
            ],
        }
    ]


def test_anthropic_messages_preserves_context_prefix_message_boundaries() -> None:
    calls: list[dict[str, Any]] = []

    def fake_post(**kwargs: Any) -> tuple[int, bytes]:
        calls.append(kwargs)
        return 200, json.dumps(
            {
                "id": "msg_1",
                "model": "claude-test",
                "role": "assistant",
                "stop_reason": "end_turn",
                "content": [{"type": "text", "text": "ok"}],
            }
        ).encode("utf-8")

    adapter = ProviderAdapter(
        config={
            "provider": {
                "mode": "anthropic-messages",
                "apiKey": "sk-ant",
                "baseUrl": "https://api.anthropic.test",
                "model": "claude-test",
                "maxTokens": 777,
            }
        },
        http_post=fake_post,
    )

    adapter.chat(
        messages=[
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "Stable context prefix:\nstable"},
            {"role": "user", "content": "Dynamic context tail:\ndynamic"},
            {"role": "user", "content": "Current user request:\ncontinue"},
        ]
    )

    payload = json.loads(calls[0]["body"].decode("utf-8"))
    assert payload["system"] == "sys"
    assert payload["messages"] == [
        {"role": "user", "content": "Stable context prefix:\nstable"},
        {"role": "user", "content": "Dynamic context tail:\ndynamic"},
        {"role": "user", "content": "Current user request:\ncontinue"},
    ]


def test_anthropic_messages_drops_orphan_tool_messages_from_request() -> None:
    calls: list[dict[str, Any]] = []

    def fake_post(**kwargs: Any) -> tuple[int, bytes]:
        calls.append(kwargs)
        return 200, json.dumps(
            {
                "id": "msg_orphan",
                "model": "claude-test",
                "role": "assistant",
                "stop_reason": "end_turn",
                "content": [{"type": "text", "text": "ok"}],
                "usage": {"input_tokens": 5, "output_tokens": 3},
            }
        ).encode("utf-8")

    adapter = ProviderAdapter(
        config={
            "provider": {
                "mode": "anthropic-messages",
                "apiKey": "sk-ant",
                "baseUrl": "https://api.anthropic.test",
                "apiFormat": "anthropic-messages",
                "model": "claude-test",
                "maxTokens": 777,
            }
        },
        http_post=fake_post,
    )

    adapter.chat(
        messages=[
            {"role": "user", "content": "hi"},
            {"role": "tool", "tool_call_id": "call_missing", "content": '{"status":"completed"}'},
        ]
    )

    payload = json.loads(calls[0]["body"].decode("utf-8"))
    assert payload["messages"] == [{"role": "user", "content": "hi"}]


def test_anthropic_messages_tool_use_response_is_normalized() -> None:
    calls: list[dict[str, Any]] = []

    def fake_post(**kwargs: Any) -> tuple[int, bytes]:
        calls.append(kwargs)
        return 200, json.dumps(
            {
                "id": "msg_2",
                "model": "claude-test",
                "role": "assistant",
                "stop_reason": "tool_use",
                "content": [
                    {"type": "text", "text": "checking"},
                    {
                        "type": "tool_use",
                        "id": "toolu_1",
                        "name": "workspace_read",
                        "input": {"path": "README.md"},
                    },
                ],
            }
        ).encode("utf-8")

    adapter = ProviderAdapter(
        config={
            "provider": {
                "mode": "anthropic",
                "apiKey": "sk-ant",
                "baseUrl": "https://api.anthropic.test/v1",
                "model": "claude-test",
            }
        },
        http_post=fake_post,
    )

    response = adapter.chat(
        messages=[{"role": "user", "content": "read"}],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "workspace.read",
                    "description": "Read a file",
                    "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
                },
            }
        ],
    )

    payload = json.loads(calls[0]["body"].decode("utf-8"))
    assert payload["tools"][0]["name"] == "workspace_read"
    assert payload["tools"][0]["input_schema"]["type"] == "object"
    assert response["message"]["content"] == "checking"
    assert response["message"]["tool_calls"] == [
        {
            "id": "toolu_1",
            "type": "function",
            "name": "workspace.read",
            "arguments": {"path": "README.md"},
        }
    ]


def test_anthropic_messages_thinking_block_becomes_thought_summary() -> None:
    def fake_post(**_kwargs: Any) -> tuple[int, bytes]:
        return 200, json.dumps(
            {
                "id": "msg_thinking",
                "model": "claude-test",
                "role": "assistant",
                "stop_reason": "end_turn",
                "content": [
                    {"type": "thinking", "thinking": "Check context first. "},
                    {"type": "text", "text": "Done"},
                ],
            }
        ).encode("utf-8")

    adapter = ProviderAdapter(
        config={
            "provider": {
                "mode": "anthropic",
                "apiKey": "sk-ant",
                "baseUrl": "https://api.anthropic.test/v1",
                "model": "claude-test",
            }
        },
        http_post=fake_post,
    )

    response = adapter.chat(messages=[{"role": "user", "content": "hi"}])

    assert response["message"]["content"] == "Done"
    assert response["thought_summary"] == "Check context first. "
    assert response["thoughtSummary"] == "Check context first. "


def test_anthropic_messages_stream_routes_text_thinking_and_tool_deltas() -> None:
    stream_calls: list[dict[str, Any]] = []

    def fake_post(**_kwargs: Any) -> tuple[int, bytes]:
        raise AssertionError("non-streaming transport should not be used")

    def sse(event: str, data: dict[str, Any]) -> bytes:
        return f"event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n".encode("utf-8")

    def fake_stream(**kwargs: Any) -> tuple[int, Any]:
        stream_calls.append(kwargs)
        return 200, iter(
            [
                sse(
                    "message_start",
                    {
                        "type": "message_start",
                        "message": {
                            "id": "msg_stream",
                            "model": "claude-test",
                            "role": "assistant",
                            "usage": {"input_tokens": 5, "cache_read_input_tokens": 2},
                        },
                    },
                ),
                sse("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": ""}}),
                sse(
                    "content_block_delta",
                    {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "Plan. "}},
                ),
                sse("content_block_stop", {"type": "content_block_stop", "index": 0}),
                sse("content_block_start", {"type": "content_block_start", "index": 1, "content_block": {"type": "text", "text": "Do"}}),
                sse("content_block_delta", {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "ne"}}),
                sse("content_block_stop", {"type": "content_block_stop", "index": 1}),
                sse(
                    "content_block_start",
                    {
                        "type": "content_block_start",
                        "index": 2,
                        "content_block": {
                            "type": "tool_use",
                            "id": "toolu_1",
                            "name": "workspace_read",
                            "input": {},
                            "parentToolUseId": "call_parent",
                        },
                    },
                ),
                sse(
                    "content_block_delta",
                    {"type": "content_block_delta", "index": 2, "delta": {"type": "input_json_delta", "partial_json": "{\"path\":"}},
                ),
                sse(
                    "content_block_delta",
                    {"type": "content_block_delta", "index": 2, "delta": {"type": "input_json_delta", "partial_json": "\"README.md\"}"}},
                ),
                sse("content_block_stop", {"type": "content_block_stop", "index": 2}),
                sse(
                    "message_delta",
                    {
                        "type": "message_delta",
                        "delta": {"stop_reason": "tool_use"},
                        "usage": {"output_tokens": 10},
                    },
                ),
                sse("message_stop", {"type": "message_stop"}),
            ]
        )

    adapter = ProviderAdapter(
        config={
            "provider": {
                "mode": "anthropic",
                "apiKey": "sk-ant",
                "baseUrl": "https://api.anthropic.test/v1",
                "model": "claude-test",
            }
        },
        http_post=fake_post,
        http_stream=fake_stream,
    )

    events = list(
        adapter.chat_stream(
            messages=[{"role": "user", "content": "hi"}],
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "workspace.read",
                        "description": "Read a file",
                        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
                    },
                }
            ],
        )
    )

    assert len(stream_calls) == 1
    call = stream_calls[0]
    assert call["url"] == "https://api.anthropic.test/v1/messages"
    assert call["headers"]["Accept"] == "text/event-stream"
    payload = json.loads(call["body"].decode("utf-8"))
    assert payload["stream"] is True
    assert payload["tools"][0]["name"] == "workspace_read"
    assert events[:3] == [
        {"type": "thinking_delta", "delta": "Plan. ", "source": "provider_reasoning_delta"},
        {"type": "content_delta", "delta": "Do"},
        {"type": "content_delta", "delta": "ne"},
    ]
    assert events[3:5] == [
        {
            "type": "tool_call_delta",
            "index": 2,
            "id": "toolu_1",
            "tool_type": "function",
            "name": "workspace.read",
            "arguments_delta": "",
            "parentToolUseId": "call_parent",
        },
        {
            "type": "tool_call_delta",
            "index": 2,
            "id": "toolu_1",
            "tool_type": "function",
            "name": "workspace.read",
            "arguments_delta": "{\"path\":",
            "parentToolUseId": "call_parent",
        },
    ]
    assert events[5] == {
        "type": "tool_call_delta",
        "index": 2,
        "id": "toolu_1",
        "tool_type": "function",
        "name": "workspace.read",
        "arguments_delta": "\"README.md\"}",
        "parentToolUseId": "call_parent",
    }
    assert {"type": "finish_reason", "finish_reason": "tool_use"} in events
    assert events[-1]["type"] == "final"
    assert events[-1]["response"]["message"]["content"] == "Done"
    assert events[-1]["response"]["message"]["tool_calls"] == [
        {
            "id": "toolu_1",
            "type": "function",
            "name": "workspace.read",
            "arguments": {"path": "README.md"},
            "parentToolUseId": "call_parent",
        }
    ]
    assert events[-1]["response"]["thought_summary"] == "Plan. "
    assert events[-1]["response"]["raw"]["usage"] == {
        "input_tokens": 5,
        "cache_read_input_tokens": 2,
        "output_tokens": 10,
    }


def test_anthropic_mode_replaces_default_openai_base_url() -> None:
    calls: list[dict[str, Any]] = []

    def fake_post(**kwargs: Any) -> tuple[int, bytes]:
        calls.append(kwargs)
        return 200, b'{"id":"msg_3","model":"claude-test","role":"assistant","stop_reason":"end_turn","content":[]}'

    adapter = ProviderAdapter(
        config={
            "provider": {
                "mode": "anthropic",
                "apiKey": "sk-ant",
                "baseUrl": "https://api.openai.com/v1",
                "model": "claude-test",
            }
        },
        http_post=fake_post,
    )

    adapter.chat(messages=[{"role": "user", "content": "hi"}])

    assert calls[0]["url"] == "https://api.anthropic.com/v1/messages"


def test_anthropic_env_can_drive_openai_compatible_request_without_mode() -> None:
    calls: list[dict[str, Any]] = []

    def fake_post(**kwargs: Any) -> tuple[int, bytes]:
        calls.append(kwargs)
        return 200, b'{"choices":[{"message":{"role":"assistant","content":"anthropic env answer"}}]}'

    adapter = ProviderAdapter(
        http_post=fake_post,
        environ={
            "ANTHROPIC_AUTH_TOKEN": "sk-anthropic",
            "ANTHROPIC_BASE_URL": "https://anthropic-proxy.example.test",
            "ANTHROPIC_MODEL": "anthropic-env-chat",
        },
    )

    response = adapter.chat(messages=[{"role": "user", "content": "hi"}])

    assert response["message"]["content"] == "anthropic env answer"
    call = calls[0]
    assert call["url"] == "https://anthropic-proxy.example.test/v1/chat/completions"
    assert call["headers"]["Authorization"] == "Bearer sk-anthropic"
    payload = json.loads(call["body"].decode("utf-8"))
    assert payload["model"] == "anthropic-env-chat"


def test_anthropic_env_var_name_normalizes_root_base_url() -> None:
    calls: list[dict[str, Any]] = []

    def fake_post(**kwargs: Any) -> tuple[int, bytes]:
        calls.append(kwargs)
        return 200, b'{"choices":[{"message":{"role":"assistant","content":"configured env answer"}}]}'

    adapter = ProviderAdapter(
        config={
            "provider": {
                "mode": "openai-compatible",
                "apiKeyEnvVarName": "ANTHROPIC_AUTH_TOKEN",
                "baseUrl": "https://anthropic-profile.example.test",
                "model": "configured-env-chat",
            }
        },
        http_post=fake_post,
        environ={"ANTHROPIC_AUTH_TOKEN": "sk-configured-anthropic"},
    )

    response = adapter.chat(messages=[{"role": "user", "content": "hi"}])

    assert response["message"]["content"] == "configured env answer"
    call = calls[0]
    assert call["url"] == "https://anthropic-profile.example.test/v1/chat/completions"
    assert call["headers"]["Authorization"] == "Bearer sk-configured-anthropic"


def test_configured_api_key_env_var_name_is_used() -> None:
    calls: list[dict[str, Any]] = []

    def fake_post(**kwargs: Any) -> tuple[int, bytes]:
        calls.append(kwargs)
        return 200, b'{"choices":[{"message":{"role":"assistant","content":"env name answer"}}]}'

    adapter = ProviderAdapter(
        config={
            "provider": {
                "mode": "openai-compatible",
                "apiKeyEnvVarName": "CUSTOM_PROVIDER_KEY",
                "model": "env-name-chat",
            }
        },
        http_post=fake_post,
        environ={"CUSTOM_PROVIDER_KEY": "sk-custom"},
    )

    response = adapter.chat(messages=[{"role": "user", "content": "hi"}])

    assert response["message"]["content"] == "env name answer"
    assert calls[0]["headers"]["Authorization"] == "Bearer sk-custom"


def test_active_provider_profile_is_used_for_real_requests() -> None:
    calls: list[dict[str, Any]] = []

    def fake_post(**kwargs: Any) -> tuple[int, bytes]:
        calls.append(kwargs)
        return 200, b'{"choices":[{"message":{"role":"assistant","content":"profile answer"}}]}'

    adapter = ProviderAdapter(
        config={
            "provider": {
                "mode": "mock",
                "baseUrl": "https://legacy.example.test/v1",
                "model": "legacy-chat",
                "activeProfileId": "secondary",
                "profiles": [
                    {
                        "id": "default",
                        "name": "Default",
                        "mode": "mock",
                        "baseUrl": "https://default.example.test/v1",
                        "model": "default-chat",
                    },
                    {
                        "id": "secondary",
                        "name": "Secondary",
                        "mode": "openai-compatible",
                        "apiKeyEnvVarName": "SECONDARY_PROVIDER_KEY",
                        "baseUrl": "https://secondary.example.test/v1",
                        "model": "secondary-chat",
                        "temperature": 0.3,
                    },
                ],
            }
        },
        http_post=fake_post,
        environ={"SECONDARY_PROVIDER_KEY": "sk-secondary"},
    )

    response = adapter.chat(messages=[{"role": "user", "content": "hi"}])

    assert response["message"]["content"] == "profile answer"
    call = calls[0]
    assert call["url"] == "https://secondary.example.test/v1/chat/completions"
    assert call["headers"]["Authorization"] == "Bearer sk-secondary"
    payload = json.loads(call["body"].decode("utf-8"))
    assert payload["model"] == "secondary-chat"
    assert payload["temperature"] == 0.3


def test_tool_calls_response_is_normalized() -> None:
    def fake_post(**_kwargs: Any) -> tuple[int, bytes]:
        return 200, json.dumps(
            {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "parentToolUseId": "call_parent",
                                    "function": {
                                        "name": "search_files",
                                        "arguments": "{\"query\":\"needle\",\"max_results\":3}",
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ]
            }
        ).encode("utf-8")

    adapter = ProviderAdapter(
        config={"provider": {"mode": "openai-compatible", "apiKey": "sk-test", "model": "test-chat"}},
        http_post=fake_post,
    )

    response = adapter.chat(messages=[{"role": "user", "content": "find needle"}])

    assert response == {
        "message": {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "name": "search_files",
                    "arguments": {"query": "needle", "max_results": 3},
                    "parentToolUseId": "call_parent",
                }
            ],
        },
        "finish_reason": "tool_calls",
        "raw": {
            "id": None,
            "model": None,
            "usage": None,
        },
    }


def test_generate_exposes_react_tool_calls() -> None:
    def fake_post(**_kwargs: Any) -> tuple[int, bytes]:
        return 200, json.dumps(
            {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "I need to inspect files.",
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": "read_file",
                                        "arguments": "{\"path\":\"README.md\"}",
                                    },
                                }
                            ],
                        }
                    }
                ]
            }
        ).encode("utf-8")

    adapter = ProviderAdapter(
        config={"provider": {"mode": "openai-compatible", "apiKey": "sk-test", "model": "test-chat"}},
        http_post=fake_post,
    )

    response = adapter.generate("read README", _context())

    assert response["message"] == "I need to inspect files."
    assert response["tool_calls"] == [
        {
            "id": "call_1",
            "type": "function",
            "name": "read_file",
            "arguments": {"path": "README.md"},
        }
    ]


def test_generate_prefers_streaming_when_enabled() -> None:
    stream_calls: list[dict[str, Any]] = []

    def fail_post(**_kwargs: Any) -> tuple[int, bytes]:
        raise AssertionError("non-streaming transport should not be used")

    def fake_stream(**kwargs: Any) -> tuple[int, Any]:
        stream_calls.append(kwargs)
        return 200, iter(
            [
                b'data: {"id":"chatcmpl_1","model":"test-chat","choices":[{"delta":{"role":"assistant","content":"Hel"},"index":0}]}\n\n',
                b'data: {"id":"chatcmpl_1","model":"test-chat","choices":[{"delta":{"content":"lo"},"index":0,"finish_reason":"stop"}]}\n\n',
                b"data: [DONE]\n\n",
            ]
        )

    adapter = ProviderAdapter(
        config={
            "provider": {
                "mode": "openai-compatible",
                "apiKey": "sk-test",
                "baseUrl": "https://llm.example.test/v1",
                "model": "test-chat",
                "streamingEnabled": False,
            }
        },
        http_post=fail_post,
        http_stream=fake_stream,
    )
    context = _context()
    context["config"] = {
        "provider": {
            "mode": "openai-compatible",
            "apiFormat": "openai-chat",
            "streamingEnabled": True,
            "apiKey": "sk-test",
            "baseUrl": "https://llm.example.test/v1",
            "model": "test-chat",
        }
    }

    response = adapter.generate("say hello", context)

    assert len(stream_calls) == 1
    assert response["message"] == "Hello"
    assert response["final"] == "Hello"
    assert response["finish_reason"] == "stop"


def test_generate_streams_anthropic_messages_when_enabled() -> None:
    stream_calls: list[dict[str, Any]] = []

    def fail_post(**_kwargs: Any) -> tuple[int, bytes]:
        raise AssertionError("non-streaming transport should not be used")

    def fake_stream(**kwargs: Any) -> tuple[int, Any]:
        stream_calls.append(kwargs)
        return 200, iter(
            [
                b'event: message_start\n',
                b'data: {"type":"message_start","message":{"id":"msg_1","model":"claude-test","role":"assistant","usage":{"input_tokens":3}}}\n\n',
                b'event: content_block_delta\n',
                b'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hi"}}\n\n',
                b'event: message_delta\n',
                b'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":1}}\n\n',
                b'event: message_stop\n',
                b'data: {"type":"message_stop"}\n\n',
            ]
        )

    adapter = ProviderAdapter(
        config={
            "provider": {
                "mode": "anthropic",
                "apiKey": "sk-ant",
                "baseUrl": "https://api.anthropic.test",
                "model": "claude-test",
            }
        },
        http_post=fail_post,
        http_stream=fake_stream,
    )
    context = _context()
    context["config"] = {
        "provider": {
            "mode": "anthropic",
            "apiFormat": "anthropic-messages",
            "streamingEnabled": True,
            "apiKey": "sk-ant",
            "baseUrl": "https://api.anthropic.test",
            "model": "claude-test",
        }
    }

    response = adapter.generate("say hi", context)

    assert len(stream_calls) == 1
    payload = json.loads(stream_calls[0]["body"].decode("utf-8"))
    assert payload["stream"] is True
    assert response["message"] == "Hi"
    assert response["final"] == "Hi"
    assert response["finish_reason"] == "end_turn"
    assert response["raw"]["usage"] == {"input_tokens": 3, "output_tokens": 1}


def test_openai_responses_streams_when_enabled() -> None:
    stream_calls: list[dict[str, Any]] = []

    def fail_post(**_kwargs: Any) -> tuple[int, bytes]:
        raise AssertionError("non-streaming transport should not be used")

    def fake_stream(**kwargs: Any) -> tuple[int, Any]:
        stream_calls.append(kwargs)
        return 200, iter(
            [
                b'event: response.created\n',
                b'data: {"type":"response.created","response":{"id":"resp_1","model":"test-responses","status":"in_progress","output":[]}}\n\n',
                b'event: response.output_text.delta\n',
                b'data: {"type":"response.output_text.delta","delta":"Hel"}\n\n',
                b'event: response.output_text.delta\n',
                b'data: {"type":"response.output_text.delta","delta":"lo"}\n\n',
                b'event: response.completed\n',
                b'data: {"type":"response.completed","response":{"id":"resp_1","model":"test-responses","status":"completed","output_text":"Hello","output":[],"usage":{"total_tokens":5}}}\n\n',
            ]
        )

    adapter = ProviderAdapter(
        config={
            "provider": {
                "mode": "openai-compatible",
                "apiKey": "sk-test",
                "baseUrl": "https://llm.example.test/v1",
                "apiFormat": "openai-responses",
                "model": "test-chat",
            }
        },
        http_post=fail_post,
        http_stream=fake_stream,
    )
    context = _context()
    context["config"] = {
        "provider": {
            "mode": "openai-compatible",
            "apiFormat": "openai-responses",
            "streamingEnabled": True,
            "apiKey": "sk-test",
            "baseUrl": "https://llm.example.test/v1",
            "model": "test-chat",
        }
    }

    events = list(adapter.chat_stream(messages=[{"role": "user", "content": "hi"}], context=context))

    assert len(stream_calls) == 1
    payload = json.loads(stream_calls[0]["body"].decode("utf-8"))
    assert payload["stream"] is True
    assert [event for event in events if event["type"] == "content_delta"] == [
        {"type": "content_delta", "delta": "Hel"},
        {"type": "content_delta", "delta": "lo"},
    ]
    assert events[-1]["type"] == "final"
    assert events[-1]["response"]["message"]["content"] == "Hello"


def test_openai_responses_stream_routes_reasoning_summary_to_thinking_delta() -> None:
    def fail_post(**_kwargs: Any) -> tuple[int, bytes]:
        raise AssertionError("non-streaming transport should not be used")

    def fake_stream(**_kwargs: Any) -> tuple[int, Any]:
        return 200, iter(
            [
                b'event: response.created\n',
                b'data: {"type":"response.created","response":{"id":"resp_1","model":"test-responses","status":"in_progress","output":[]}}\n\n',
                b'event: response.reasoning_summary_text.delta\n',
                b'data: {"type":"response.reasoning_summary_text.delta","delta":"Checking "}\n\n',
                b'event: response.reasoning_summary_text.delta\n',
                b'data: {"type":"response.reasoning_summary_text.delta","delta":"files."}\n\n',
                b'event: response.output_text.delta\n',
                b'data: {"type":"response.output_text.delta","delta":"Done"}\n\n',
                b'event: response.completed\n',
                b'data: {"type":"response.completed","response":{"id":"resp_1","model":"test-responses","status":"completed","output_text":"Done","output":[]}}\n\n',
            ]
        )

    adapter = ProviderAdapter(
        config={
            "provider": {
                "mode": "openai-compatible",
                "apiKey": "sk-test",
                "baseUrl": "https://llm.example.test/v1",
                "apiFormat": "openai-responses",
                "model": "test-chat",
            }
        },
        http_post=fail_post,
        http_stream=fake_stream,
    )
    context = _context()
    context["config"] = {
        "provider": {
            "mode": "openai-compatible",
            "apiFormat": "openai-responses",
            "streamingEnabled": True,
            "apiKey": "sk-test",
            "baseUrl": "https://llm.example.test/v1",
            "model": "test-chat",
        }
    }

    events = list(adapter.chat_stream(messages=[{"role": "user", "content": "hi"}], context=context))

    assert [event for event in events if event["type"] == "thinking_delta"] == [
        {"type": "thinking_delta", "delta": "Checking ", "source": "provider_reasoning_summary"},
        {"type": "thinking_delta", "delta": "files.", "source": "provider_reasoning_summary"},
    ]
    assert [event for event in events if event["type"] == "content_delta"] == [
        {"type": "content_delta", "delta": "Done"},
    ]
    assert events[-1]["response"]["message"]["content"] == "Done"


def test_openai_responses_stream_routes_reasoning_text_to_thinking_delta() -> None:
    def fail_post(**_kwargs: Any) -> tuple[int, bytes]:
        raise AssertionError("non-streaming transport should not be used")

    def fake_stream(**_kwargs: Any) -> tuple[int, Any]:
        return 200, iter(
            [
                b'event: response.created\n',
                b'data: {"type":"response.created","response":{"id":"resp_1","model":"test-responses","status":"in_progress","output":[]}}\n\n',
                b'event: response.reasoning_text.delta\n',
                b'data: {"type":"response.reasoning_text.delta","delta":"Plan "}\n\n',
                b'event: response.thinking.delta\n',
                b'data: {"type":"response.thinking.delta","delta":"then write. "}\n\n',
                b'event: response.output_text.delta\n',
                b'data: {"type":"response.output_text.delta","delta":"Done"}\n\n',
                b'event: response.completed\n',
                b'data: {"type":"response.completed","response":{"id":"resp_1","model":"test-responses","status":"completed","output_text":"Done","output":[]}}\n\n',
            ]
        )

    adapter = ProviderAdapter(
        config={
            "provider": {
                "mode": "openai-compatible",
                "apiKey": "sk-test",
                "baseUrl": "https://llm.example.test/v1",
                "apiFormat": "openai-responses",
                "model": "test-chat",
            }
        },
        http_post=fail_post,
        http_stream=fake_stream,
    )
    context = _context()
    context["config"] = {
        "provider": {
            "mode": "openai-compatible",
            "apiFormat": "openai-responses",
            "streamingEnabled": True,
            "apiKey": "sk-test",
            "baseUrl": "https://llm.example.test/v1",
            "model": "test-chat",
        }
    }

    events = list(adapter.chat_stream(messages=[{"role": "user", "content": "hi"}], context=context))

    assert [event for event in events if event["type"] == "thinking_delta"] == [
        {"type": "thinking_delta", "delta": "Plan ", "source": "provider_reasoning_delta"},
        {"type": "thinking_delta", "delta": "then write. ", "source": "provider_reasoning_delta"},
    ]
    assert [event for event in events if event["type"] == "content_delta"] == [
        {"type": "content_delta", "delta": "Done"},
    ]
    assert events[-1]["response"]["message"]["content"] == "Done"


def test_openai_responses_stream_merges_function_call_parts() -> None:
    stream_calls: list[dict[str, Any]] = []

    def fail_post(**_kwargs: Any) -> tuple[int, bytes]:
        raise AssertionError("non-streaming transport should not be used")

    def fake_stream(**kwargs: Any) -> tuple[int, Any]:
        stream_calls.append(kwargs)
        return 200, iter(
            [
                b'event: response.created\n',
                b'data: {"type":"response.created","response":{"id":"resp_tool","model":"test-responses","status":"in_progress","output":[]}}\n\n',
                b'event: response.output_item.added\n',
                b'data: {"type":"response.output_item.added","output_index":0,"item":{"id":"fc_1","type":"function_call","call_id":"call_1","parentToolUseId":"call_parent","name":"workspace_read","arguments":""}}\n\n',
                b'event: response.function_call_arguments.delta\n',
                b'data: {"type":"response.function_call_arguments.delta","output_index":0,"delta":"{\\"path\\":"}\n\n',
                b'event: response.function_call_arguments.delta\n',
                b'data: {"type":"response.function_call_arguments.delta","output_index":0,"delta":"\\"README.md\\"}"}\n\n',
                b'event: response.output_item.done\n',
                b'data: {"type":"response.output_item.done","output_index":0,"item":{"id":"fc_1","type":"function_call","call_id":"call_1"}}\n\n',
                b'event: response.completed\n',
                b'data: {"type":"response.completed","response":{"id":"resp_tool","model":"test-responses","status":"completed","output":[],"usage":{"total_tokens":5}}}\n\n',
            ]
        )

    adapter = ProviderAdapter(
        config={
            "provider": {
                "mode": "openai-compatible",
                "apiKey": "sk-test",
                "baseUrl": "https://llm.example.test/v1",
                "apiFormat": "openai-responses",
                "model": "test-chat",
            }
        },
        http_post=fail_post,
        http_stream=fake_stream,
    )
    context = _context()
    context["config"] = {
        "provider": {
            "mode": "openai-compatible",
            "apiFormat": "openai-responses",
            "streamingEnabled": True,
            "apiKey": "sk-test",
            "baseUrl": "https://llm.example.test/v1",
            "model": "test-chat",
        }
    }

    events = list(
        adapter.chat_stream(
            messages=[{"role": "user", "content": "read"}],
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "workspace.read",
                        "description": "Read a file",
                        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
                    },
                }
            ],
            context=context,
        )
    )

    assert len(stream_calls) == 1
    assert [event for event in events if event["type"] == "tool_call_delta"] == [
        {
            "type": "tool_call_delta",
            "index": 0,
            "id": "call_1",
            "tool_type": "function",
            "name": "workspace.read",
            "parentToolUseId": "call_parent",
            "arguments_delta": "",
        },
        {
            "type": "tool_call_delta",
            "index": 0,
            "id": "call_1",
            "tool_type": "function",
            "name": "workspace.read",
            "parentToolUseId": "call_parent",
            "arguments_delta": "{\"path\":",
        },
        {
            "type": "tool_call_delta",
            "index": 0,
            "id": "call_1",
            "tool_type": "function",
            "name": "workspace.read",
            "parentToolUseId": "call_parent",
            "arguments_delta": "\"README.md\"}",
        },
    ]
    assert events[-1]["type"] == "final"
    assert events[-1]["response"]["message"]["tool_calls"] == [
        {
            "id": "call_1",
            "type": "function",
            "name": "workspace.read",
            "arguments": {"path": "README.md"},
            "parentToolUseId": "call_parent",
        }
    ]


def test_openai_responses_stream_uses_function_call_arguments_done() -> None:
    def fail_post(**_kwargs: Any) -> tuple[int, bytes]:
        raise AssertionError("non-streaming transport should not be used")

    def fake_stream(**_kwargs: Any) -> tuple[int, Any]:
        return 200, iter(
            [
                b'event: response.created\n',
                b'data: {"type":"response.created","response":{"id":"resp_tool","model":"test-responses","status":"in_progress","output":[]}}\n\n',
                b'event: response.output_item.added\n',
                b'data: {"type":"response.output_item.added","item_id":"fc_1","output_index":0,"item":{"id":"fc_1","type":"function_call","call_id":"call_1","name":"read_file","arguments":""}}\n\n',
                b'event: response.function_call_arguments.done\n',
                b'data: {"type":"response.function_call_arguments.done","item_id":"fc_1","output_index":0,"arguments":"{\\"path\\":\\"blog_service.py\\"}"}\n\n',
                b'event: response.output_item.done\n',
                b'data: {"type":"response.output_item.done","item_id":"fc_1","output_index":0,"item":{"id":"fc_1","type":"function_call","call_id":"call_1","name":"read_file","arguments":""}}\n\n',
                b'event: response.completed\n',
                b'data: {"type":"response.completed","response":{"id":"resp_tool","model":"test-responses","status":"completed","output":[]}}\n\n',
            ]
        )

    adapter = ProviderAdapter(
        config={
            "provider": {
                "mode": "openai-compatible",
                "apiKey": "sk-test",
                "baseUrl": "https://llm.example.test/v1",
                "apiFormat": "openai-responses",
                "model": "test-chat",
            }
        },
        http_post=fail_post,
        http_stream=fake_stream,
    )
    context = _context()
    context["config"] = {
        "provider": {
            "mode": "openai-compatible",
            "apiFormat": "openai-responses",
            "streamingEnabled": True,
            "apiKey": "sk-test",
            "baseUrl": "https://llm.example.test/v1",
            "model": "test-chat",
        }
    }

    events = list(adapter.chat_stream(messages=[{"role": "user", "content": "read"}], context=context))

    assert events[-1]["type"] == "final"
    assert events[-1]["response"]["message"]["tool_calls"] == [
        {
            "id": "call_1",
            "type": "function",
            "name": "read_file",
            "arguments": {"path": "blog_service.py"},
        }
    ]


def test_openai_responses_stream_patches_empty_completed_tool_arguments() -> None:
    def fail_post(**_kwargs: Any) -> tuple[int, bytes]:
        raise AssertionError("non-streaming transport should not be used")

    def fake_stream(**_kwargs: Any) -> tuple[int, Any]:
        return 200, iter(
            [
                b'event: response.output_item.added\n',
                b'data: {"type":"response.output_item.added","item_id":"fc_1","output_index":0,"item":{"id":"fc_1","type":"function_call","call_id":"call_1","name":"apply_patch","arguments":""}}\n\n',
                b'event: response.function_call_arguments.done\n',
                b'data: {"type":"response.function_call_arguments.done","item_id":"fc_1","output_index":0,"arguments":"{\\"patchText\\":\\"*** Begin Patch\\\\n*** End Patch\\"}"}\n\n',
                b'event: response.output_item.done\n',
                b'data: {"type":"response.output_item.done","item_id":"fc_1","output_index":0,"item":{"id":"fc_1","type":"function_call","call_id":"call_1","name":"apply_patch","arguments":""}}\n\n',
                b'event: response.completed\n',
                b'data: {"type":"response.completed","response":{"id":"resp_tool","model":"test-responses","status":"completed","output":[{"id":"fc_1","type":"function_call","call_id":"call_1","name":"apply_patch","arguments":""}]}}\n\n',
            ]
        )

    adapter = ProviderAdapter(
        config={
            "provider": {
                "mode": "openai-compatible",
                "apiKey": "sk-test",
                "baseUrl": "https://llm.example.test/v1",
                "apiFormat": "openai-responses",
                "model": "test-chat",
            }
        },
        http_post=fail_post,
        http_stream=fake_stream,
    )
    context = _context()
    context["config"] = {
        "provider": {
            "mode": "openai-compatible",
            "apiFormat": "openai-responses",
            "streamingEnabled": True,
            "apiKey": "sk-test",
            "baseUrl": "https://llm.example.test/v1",
            "model": "test-chat",
        }
    }

    events = list(adapter.chat_stream(messages=[{"role": "user", "content": "patch"}], context=context))

    assert events[-1]["type"] == "final"
    assert events[-1]["response"]["message"]["tool_calls"] == [
        {
            "id": "call_1",
            "type": "function",
            "name": "apply_patch",
            "arguments": {"patchText": "*** Begin Patch\n*** End Patch"},
        }
    ]


def test_openai_responses_stream_patches_single_tool_when_argument_key_differs() -> None:
    def fail_post(**_kwargs: Any) -> tuple[int, bytes]:
        raise AssertionError("non-streaming transport should not be used")

    def fake_stream(**_kwargs: Any) -> tuple[int, Any]:
        return 200, iter(
            [
                b'event: response.output_item.added\n',
                b'data: {"type":"response.output_item.added","item_id":"fc_1","output_index":0,"item":{"id":"fc_1","type":"function_call","call_id":"call_1","name":"read_file","arguments":""}}\n\n',
                b'event: response.function_call_arguments.done\n',
                b'data: {"type":"response.function_call_arguments.done","output_index":0,"arguments":"{\\"path\\":\\"blog_service.py\\"}"}\n\n',
                b'event: response.completed\n',
                b'data: {"type":"response.completed","response":{"id":"resp_tool","model":"test-responses","status":"completed","output":[{"id":"fc_1","type":"function_call","call_id":"call_1","name":"read_file","arguments":""}]}}\n\n',
            ]
        )

    adapter = ProviderAdapter(
        config={
            "provider": {
                "mode": "openai-compatible",
                "apiKey": "sk-test",
                "baseUrl": "https://llm.example.test/v1",
                "apiFormat": "openai-responses",
                "model": "test-chat",
            }
        },
        http_post=fail_post,
        http_stream=fake_stream,
    )
    context = _context()
    context["config"] = {
        "provider": {
            "mode": "openai-compatible",
            "apiFormat": "openai-responses",
            "streamingEnabled": True,
            "apiKey": "sk-test",
            "baseUrl": "https://llm.example.test/v1",
            "model": "test-chat",
        }
    }

    events = list(adapter.chat_stream(messages=[{"role": "user", "content": "read"}], context=context))

    assert events[-1]["response"]["message"]["tool_calls"] == [
        {
            "id": "call_1",
            "type": "function",
            "name": "read_file",
            "arguments": {"path": "blog_service.py"},
        }
    ]


def test_openai_responses_stream_dedupes_repeated_argument_payload() -> None:
    def fail_post(**_kwargs: Any) -> tuple[int, bytes]:
        raise AssertionError("non-streaming transport should not be used")

    def fake_stream(**_kwargs: Any) -> tuple[int, Any]:
        return 200, iter(
            [
                b'event: response.output_item.added\n',
                b'data: {"type":"response.output_item.added","item_id":"fc_1","output_index":0,"item":{"id":"fc_1","type":"function_call","call_id":"call_1","name":"read_file","arguments":"{\\"path\\":\\"blog_service.py\\"}"}}\n\n',
                b'event: response.function_call_arguments.done\n',
                b'data: {"type":"response.function_call_arguments.done","output_index":0,"arguments":"{\\"path\\":\\"blog_service.py\\"}"}\n\n',
                b'event: response.completed\n',
                b'data: {"type":"response.completed","response":{"id":"resp_tool","model":"test-responses","status":"completed","output":[]}}\n\n',
            ]
        )

    adapter = ProviderAdapter(
        config={
            "provider": {
                "mode": "openai-compatible",
                "apiKey": "sk-test",
                "baseUrl": "https://llm.example.test/v1",
                "apiFormat": "openai-responses",
                "model": "test-chat",
            }
        },
        http_post=fail_post,
        http_stream=fake_stream,
    )
    context = _context()
    context["config"] = {
        "provider": {
            "mode": "openai-compatible",
            "apiFormat": "openai-responses",
            "streamingEnabled": True,
            "apiKey": "sk-test",
            "baseUrl": "https://llm.example.test/v1",
            "model": "test-chat",
        }
    }

    events = list(adapter.chat_stream(messages=[{"role": "user", "content": "read"}], context=context))

    assert events[-1]["response"]["message"]["tool_calls"] == [
        {
            "id": "call_1",
            "type": "function",
            "name": "read_file",
            "arguments": {"path": "blog_service.py"},
        }
    ]


def test_plain_text_response_is_normalized() -> None:
    def fake_post(**_kwargs: Any) -> tuple[int, bytes]:
        return 200, b'{"id":"chatcmpl_1","model":"test-chat","choices":[{"message":{"role":"assistant","content":"hello"}}],"usage":{"total_tokens":7}}'

    adapter = ProviderAdapter(
        config={"provider": {"mode": "openai-compatible", "apiKey": "sk-test", "model": "test-chat"}},
        http_post=fake_post,
    )

    response = adapter.chat(messages=[{"role": "user", "content": "hi"}])

    assert response == {
        "message": {"role": "assistant", "content": "hello", "tool_calls": []},
        "finish_reason": None,
        "raw": {"id": "chatcmpl_1", "model": "test-chat", "usage": {"total_tokens": 7}},
    }


def test_provider_error_is_readable() -> None:
    def fake_post(**_kwargs: Any) -> tuple[int, bytes]:
        return 429, b'{"error":{"message":"rate limit exceeded","type":"rate_limit_error"}}'

    adapter = ProviderAdapter(
        config={"provider": {"mode": "openai-compatible", "apiKey": "sk-test", "model": "test-chat"}},
        http_post=fake_post,
    )

    with pytest.raises(ProviderAdapterError, match="Provider request failed with HTTP 429: rate limit exceeded"):
        adapter.chat(messages=[{"role": "user", "content": "hi"}])


def test_provider_retries_malformed_json_response() -> None:
    calls: list[dict[str, Any]] = []

    def fake_post(**kwargs: Any) -> tuple[int, bytes]:
        calls.append(kwargs)
        if len(calls) == 1:
            return 200, b""
        return 200, b'{"id":"chatcmpl_1","model":"test-chat","choices":[{"message":{"role":"assistant","content":"hello"}}],"usage":{"total_tokens":7}}'

    adapter = ProviderAdapter(
        config={"provider": {"mode": "openai-compatible", "apiKey": "sk-test", "model": "test-chat"}},
        http_post=fake_post,
    )

    response = adapter.chat(messages=[{"role": "user", "content": "hi"}])

    assert response["message"]["content"] == "hello"
    assert len(calls) == 2


@pytest.mark.parametrize(
    ("message", "category", "retryable", "action"),
    [
        ("Provider request timed out after 30s", "timeout", True, "retry"),
        ("Provider request failed with HTTP 429: rate limit exceeded", "rate_limit", True, "retry_with_backoff"),
        ("Provider request failed with HTTP 413: context length exceeded", "context_too_large", False, "compact_or_split_context"),
        ("Environment variable MISSING_KEY is not set.", "auth", False, "fix_provider_credentials"),
        ("Provider refused due to content filter", "refusal", False, "ask_user_or_change_request"),
        ("Provider returned invalid JSON: Expecting value", "invalid_response", True, "retry"),
    ],
)
def test_provider_failure_recovery_classifier(
    message: str,
    category: str,
    retryable: bool,
    action: str,
) -> None:
    recovery = classify_provider_failure(ProviderAdapterError(message))

    assert recovery.category == category
    assert recovery.retryable is retryable
    assert recovery.recommended_action == action


class _TraceProbeProvider:
    def stream(self, *_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("not used")


class _TraceProbe(ProviderTurnMixin):
    def __init__(self) -> None:
        self._provider = _TraceProbeProvider()


def test_provider_trace_records_api_format_and_request_path() -> None:
    probe = _TraceProbe()
    context = {
        "config": {
            "provider": {
                "mode": "openai-compatible",
                "apiFormat": "openai-responses",
                "baseUrl": "https://api.openai.test/v1",
                "model": "gpt-test",
                "stream": True,
            }
        },
        "messages": [{"role": "user", "content": "hi"}],
        "tools": [{"name": "read_file"}],
        "step": 2,
    }

    payload = probe._provider_trace_payload(context)

    assert payload["apiFormat"] == "openai-responses"
    assert payload["requestPath"] == "/v1/responses"
    assert payload["messageCount"] == 1
    assert payload["toolCount"] == 1
    assert probe._should_stream_provider(context) is True


def test_provider_trace_defaults_anthropic_mode_to_messages() -> None:
    probe = _TraceProbe()
    context = {
        "config": {
            "provider": {
                "mode": "anthropic",
                "baseUrl": "https://api.anthropic.test",
                "model": "claude-test",
                "stream": True,
            }
        }
    }

    payload = probe._provider_trace_payload(context)

    assert payload["apiFormat"] == "anthropic-messages"
    assert payload["requestPath"] == "/v1/messages"
    assert probe._should_stream_provider(context) is True


def test_openai_chat_trace_remains_streamable_by_default() -> None:
    probe = _TraceProbe()
    context = {
        "config": {
            "provider": {
                "mode": "openai-compatible",
                "apiFormat": "openai-chat",
                "baseUrl": "https://api.openai.test/v1",
            }
        }
    }

    payload = probe._provider_trace_payload(context)

    assert payload["apiFormat"] == "openai-chat"
    assert payload["requestPath"] == "/v1/chat/completions"
    assert probe._should_stream_provider(context) is True


def test_provider_trace_uses_active_profile_api_format() -> None:
    probe = _TraceProbe()
    context = {
        "config": {
            "provider": {
                "mode": "openai-compatible",
                "apiFormat": "openai-chat",
                "baseUrl": "https://root.example.test/v1",
                "activeProfileId": "responses",
                "profiles": [
                    {
                        "id": "responses",
                        "mode": "openai-compatible",
                        "apiFormat": "openai-responses",
                        "baseUrl": "https://profile.example.test/v1",
                        "model": "gpt-profile",
                    }
                ],
            }
        }
    }

    payload = probe._provider_trace_payload(context)

    assert payload["apiFormat"] == "openai-responses"
    assert payload["baseUrl"] == "https://profile.example.test/v1"
    assert payload["requestPath"] == "/v1/responses"
    assert probe._should_stream_provider(context) is True
