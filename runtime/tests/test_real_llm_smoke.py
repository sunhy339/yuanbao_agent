from __future__ import annotations

import os
from typing import Any

import pytest

from local_agent_runtime.provider.adapter import ProviderAdapter


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _real_smoke_config() -> dict[str, Any]:
    api_key_env = _env("YUANBAO_REAL_LLM_API_KEY_ENV", "LOCAL_AGENT_PROVIDER_API_KEY")
    api_key = _env(api_key_env)
    if not api_key:
        pytest.skip(f"Set YUANBAO_REAL_LLM_SMOKE=1 and {api_key_env} to run the real LLM smoke.")

    api_format = _env("YUANBAO_REAL_LLM_API_FORMAT", _env("LOCAL_AGENT_PROVIDER_API_FORMAT", "openai-chat"))
    mode = _env(
        "YUANBAO_REAL_LLM_PROVIDER_MODE",
        "anthropic-messages" if api_format == "anthropic-messages" else "openai-compatible",
    )
    return {
        "provider": {
            "mode": mode,
            "apiFormat": api_format,
            "baseUrl": _env(
                "YUANBAO_REAL_LLM_BASE_URL",
                _env(
                    "LOCAL_AGENT_PROVIDER_BASE_URL",
                    "https://api.anthropic.com" if api_format == "anthropic-messages" else "https://api.openai.com/v1",
                ),
            ),
            "model": _env(
                "YUANBAO_REAL_LLM_MODEL",
                _env("LOCAL_AGENT_PROVIDER_MODEL", "claude-sonnet-4-5" if api_format == "anthropic-messages" else "gpt-5-codex"),
            ),
            "apiKeyEnvVarName": api_key_env,
            "timeout": float(_env("YUANBAO_REAL_LLM_TIMEOUT", "30")),
            "maxTokens": int(_env("YUANBAO_REAL_LLM_MAX_TOKENS", "64")),
        }
    }


@pytest.mark.real_llm
def test_real_llm_provider_smoke_is_env_gated() -> None:
    if _env("YUANBAO_REAL_LLM_SMOKE") != "1":
        pytest.skip("Set YUANBAO_REAL_LLM_SMOKE=1 to contact a live provider.")

    config = _real_smoke_config()
    adapter = ProviderAdapter(config=config)
    metadata = adapter.provider_request_metadata({"config": config})
    response = adapter.chat(
        messages=[
            {
                "role": "user",
                "content": "Reply with exactly one short sentence confirming this runtime smoke is connected.",
            }
        ],
        tools=None,
        context={"config": config},
    )

    message = response.get("message") or {}
    content = message.get("content")
    assert isinstance(content, str)
    assert content.strip()
    assert response.get("finish_reason")
    assert metadata["apiFormat"] in {"openai-chat", "openai-responses", "anthropic-messages"}
    assert isinstance(metadata["requestPath"], str)
    assert metadata["requestPath"].startswith("/")
