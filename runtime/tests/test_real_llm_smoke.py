from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from local_agent_runtime.main import build_server
from local_agent_runtime.provider.adapter import ProviderAdapter
from local_agent_runtime.router.types import ExecutionStrategy, RoutingDecision, Scenario


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


@pytest.mark.real_llm
def test_real_llm_streaming_smoke_is_env_gated() -> None:
    if _env("YUANBAO_REAL_LLM_SMOKE") != "1":
        pytest.skip("Set YUANBAO_REAL_LLM_SMOKE=1 to contact a live provider.")

    config = _real_smoke_config()
    config["provider"]["streamingEnabled"] = True
    adapter = ProviderAdapter(config=config)

    events = list(
        adapter.chat_stream(
            messages=[
                {
                    "role": "user",
                    "content": "Reply with exactly one short sentence confirming this runtime stream smoke is connected.",
                }
            ],
            tools=None,
            context={"config": config},
        )
    )

    assert events
    final_event = next((event for event in reversed(events) if event.get("type") == "final"), None)
    assert isinstance(final_event, dict)
    response = final_event["response"]
    message = response.get("message") or {}
    content = message.get("content")
    assert isinstance(content, str)
    assert content.strip()
    assert response.get("finish_reason")


@pytest.mark.real_llm
def test_real_llm_accepts_tool_result_roundtrip_messages() -> None:
    if _env("YUANBAO_REAL_LLM_SMOKE") != "1":
        pytest.skip("Set YUANBAO_REAL_LLM_SMOKE=1 to contact a live provider.")

    config = _real_smoke_config()
    adapter = ProviderAdapter(config=config)

    response = adapter.chat(
        messages=[
            {
                "role": "user",
                "content": "Use the tool result and reply with exactly: tool roundtrip connected",
            },
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_roundtrip_1",
                        "type": "function",
                        "name": "search_files",
                        "arguments": {"query": "incident"},
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call_roundtrip_1",
                "name": "search_files",
                "content": '{"matches":[{"path":"incident_seed.py"}]}',
            },
        ],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "search_files",
                    "description": "Search files in the workspace.",
                    "parameters": {
                        "type": "object",
                        "properties": {"query": {"type": "string"}},
                        "required": ["query"],
                    },
                },
            }
        ],
        context={"config": config},
    )

    message = response.get("message") or {}
    content = message.get("content")
    assert isinstance(content, str)
    assert "tool roundtrip connected" in content.lower()
    assert response.get("finish_reason")


def _force_simple_react_route(server: Any) -> None:
    router = server._orchestrator._meta_router  # noqa: SLF001

    def route(_goal: str, _context: dict[str, Any] | None = None) -> RoutingDecision:
        return RoutingDecision(
            scenario=Scenario.SIMPLE_QUERY,
            strategy=ExecutionStrategy.REACT_STANDARD,
            confidence=0.99,
            max_steps=1,
            enable_reflection=False,
            enable_planning=False,
            reasoning="forced-real-provider-hook-smoke",
        )

    router.route = route


@pytest.mark.real_llm
def test_real_llm_provider_turn_runs_hook_side_effects(tmp_path: Path) -> None:
    if _env("YUANBAO_REAL_LLM_SMOKE") != "1":
        pytest.skip("Set YUANBAO_REAL_LLM_SMOKE=1 to contact a live provider.")

    config = _real_smoke_config()["provider"]
    config["streamingEnabled"] = False
    server = build_server(database_path=str(tmp_path / "real_llm_hooks.sqlite3"))
    store = server._store  # noqa: SLF001
    _force_simple_react_route(server)
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    workspace = server._handlers["workspace.open"]({"path": str(workspace_root)})["workspace"]  # noqa: SLF001
    session = server._handlers["session.create"]({  # noqa: SLF001
        "workspaceId": workspace["id"],
        "title": "Real provider hook smoke",
    })["session"]
    store.update_config({
        "config": {
            "provider": config,
            "permissions": {
                "preset": "balanced",
                "capabilities": {
                    "hooksExecute": {"mode": "allow", "scope": "*"},
                    "memoryWrite": {"mode": "allow", "scope": "*"},
                },
            },
            "policy": {"maxTaskSteps": 1},
        }
    })
    server._handlers["hook.create"]({  # noqa: SLF001
        "workspaceId": workspace["id"],
        "name": "remember provider turn",
        "event": "before_provider_turn",
        "action": {
            "type": "memory_write",
            "kind": "session",
            "content": "Real provider hook smoke turn {providerTurnId} for task {taskId}",
        },
        "authority": {"requiresApproval": False},
        "onFailure": "warn",
    })
    server._handlers["hook.create"]({  # noqa: SLF001
        "workspaceId": workspace["id"],
        "name": "suggest provider verification",
        "event": "after_provider_turn",
        "action": {
            "type": "auto_verification_suggestion",
            "checks": ["provider_turn_completed", "hook_side_effect_recorded"],
            "suggestion": "Verify provider turn and hook side effects were recorded.",
        },
        "authority": {"requiresApproval": False},
        "onFailure": "warn",
    })

    response = server._handlers["message.send"]({  # noqa: SLF001
        "sessionId": session["id"],
        "content": "Reply with exactly: hook smoke connected",
        "newTask": True,
    })
    task = response["task"]
    turns = server._handlers["provider_turn.list"]({"taskId": task["id"]})["turns"]  # noqa: SLF001
    hook_executions = server._handlers["hook.listExecutions"]({  # noqa: SLF001
        "taskId": task["id"],
        "limit": 20,
    })["hookExecutions"]
    memories = server._handlers["memory.list"]({  # noqa: SLF001
        "workspaceId": workspace["id"],
        "sessionId": session["id"],
        "limit": 20,
    })["entries"]

    assert task["status"] == "completed"
    assert turns
    assert any(turn.get("status") == "completed" for turn in turns)
    assert {item["event"] for item in hook_executions} >= {"before_provider_turn", "after_provider_turn"}
    assert all(item["status"] == "completed" for item in hook_executions)
    assert any("Real provider hook smoke turn" in item["content"] for item in memories)
