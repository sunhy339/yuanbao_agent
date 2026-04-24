from __future__ import annotations

import json
from typing import Any

import pytest

from local_agent_runtime.provider.adapter import ProviderAdapter, ProviderAdapterError


def _context() -> dict[str, Any]:
    return {
        "workspace_name": "demo",
        "workspace_root": "D:\\demo",
        "search_config": {"ignore": []},
        "config": {},
    }


def test_no_api_key_falls_back_to_deterministic_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "OPENAI_API_KEY",
        "LOCAL_AGENT_OPENAI_API_KEY",
        "LOCAL_AGENT_PROVIDER_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)

    def fail_post(**_kwargs: Any) -> tuple[int, bytes]:
        raise AssertionError("HTTP transport should not be used without an API key")

    adapter = ProviderAdapter(
        config={"provider": {"mode": "openai-compatible", "model": "real-model"}},
        http_post=fail_post,
    )

    response = adapter.generate("inspect workspace", _context())

    assert response["message"].startswith("Completed an initial pass over workspace demo.")
    assert response["prompt"] == "inspect workspace"


def test_openai_compatible_request_payload() -> None:
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
    assert call["timeout"] == 9
    assert call["headers"]["Authorization"] == "Bearer sk-test"
    assert call["headers"]["Content-Type"] == "application/json"
    payload = json.loads(call["body"].decode("utf-8"))
    assert payload == {
        "model": "test-chat",
        "messages": [{"role": "user", "content": "hi"}],
        "temperature": 0.7,
        "max_tokens": 123,
        "tools": tools,
        "tool_choice": "auto",
    }


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
