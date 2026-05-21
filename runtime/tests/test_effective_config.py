"""Tests for config.effective and prompt.preview RPC methods.

Covers:
  1. config.effective returns all resolved profile fields
  2. config.effective returns empty/None when no profiles configured
  3. config.effective resolves activeProfileId correctly
  4. config.effective reflects context budget and streaming config
  5. prompt.preview returns systemPrompt + layers + totalTokenEstimate
  6. prompt.preview marks runtime_safety layer as locked
  7. prompt.preview supports role and skillId params
  8. prompt.preview works with sessionId param
  9. prompt.preview works without sessionId (fallback workspaceRoot)
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from local_agent_runtime.event_bus import EventBus
from local_agent_runtime.orchestrator.service import Orchestrator
from local_agent_runtime.provider.adapter import ProviderAdapter
from local_agent_runtime.rpc.server import JsonRpcServer
from local_agent_runtime.services import CollaborationService, SubagentService
from local_agent_runtime.store.sqlite_store import SQLiteStore
from local_agent_runtime.tools import build_builtin_tools
from local_agent_runtime.tools.registry import ToolRegistry
from local_agent_runtime.policy.guard import PolicyGuard


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_harness(tmp_path: Any) -> tuple[JsonRpcServer, SQLiteStore]:
    """Create a JsonRpcServer + SQLiteStore pair for testing."""
    db_path = tmp_path / "test.sqlite3"
    event_bus = EventBus()
    store = SQLiteStore(str(db_path))
    config = store.get_config({})["config"]
    policy_guard = PolicyGuard(approval_mode=config["policy"]["approvalMode"])
    collaboration = CollaborationService(store, event_bus)
    subagent_service = SubagentService(store, collaboration)
    tool_registry = ToolRegistry(
        build_builtin_tools(policy_guard=policy_guard, store=store, subagent_service=subagent_service)
    )
    provider = ProviderAdapter()
    orchestrator = Orchestrator(
        store=store,
        event_bus=event_bus,
        tool_registry=tool_registry,
        provider=provider,
    )
    server = JsonRpcServer(orchestrator=orchestrator, store=store, event_bus=event_bus)
    return server, store


def _call(server: JsonRpcServer, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Call an RPC method and return the result dict."""
    envelope = {
        "jsonrpc": "2.0",
        "id": "test-1",
        "method": method,
        "params": params or {},
    }
    response = server.handle_line(json.dumps(envelope))
    assert "error" not in response, f"RPC error: {response.get('error')}"
    return response["result"]


# ---------------------------------------------------------------------------
# config.effective tests
# ---------------------------------------------------------------------------

class TestConfigEffective:
    """config.effective returns fully resolved runtime configuration."""

    def test_returns_all_profile_fields(self, tmp_path: Any) -> None:
        server, store = _make_harness(tmp_path)
        result = _call(server, "config.effective")
        assert "providerProfile" in result
        assert "autonomyProfile" in result
        assert "agentSoulProfile" in result
        assert "contextBudget" in result
        assert "toolPolicy" in result
        assert "streamingEnabled" in result
        assert "activeProfileIds" in result
        assert "searchMode" in result
        assert "memoryPolicy" in result

    def test_empty_profiles_when_not_configured(self, tmp_path: Any) -> None:
        server, store = _make_harness(tmp_path)
        # Default store config has no profiles
        result = _call(server, "config.effective")
        # With default config, profiles resolve to empty/None
        assert isinstance(result["providerProfile"], dict)
        assert result["autonomyProfile"] is None or isinstance(result["autonomyProfile"], dict)
        assert result["agentSoulProfile"] is None or isinstance(result["agentSoulProfile"], dict)

    def test_resolves_active_profile_ids(self, tmp_path: Any) -> None:
        server, store = _make_harness(tmp_path)
        # Configure autonomy profiles with an active one
        store.update_config({"config": {
            "autonomy": {
                "activeProfileId": "auto-L2",
                "profiles": [
                    {"id": "auto-L0", "name": "L0", "level": 0},
                    {"id": "auto-L2", "name": "L2", "level": 2},
                ],
            },
            "provider": {
                "mode": "mock",
                "profiles": [
                    {"id": "p1", "name": "Test", "mode": "mock"},
                ],
                "activeProfileId": "p1",
            },
        }})
        result = _call(server, "config.effective")
        assert result["activeProfileIds"]["autonomy"] == "auto-L2"
        assert result["autonomyProfile"]["id"] == "auto-L2"
        assert result["autonomyProfile"]["name"] == "L2"
        assert result["activeProfileIds"]["provider"] == "p1"
        assert result["providerProfile"]["id"] == "p1"

    def test_context_budget_and_streaming(self, tmp_path: Any) -> None:
        server, store = _make_harness(tmp_path)
        # Configure maxContextTokens
        store.update_config({"config": {
            "provider": {"mode": "mock", "maxContextTokens": 128000},
        }})
        result = _call(server, "config.effective")
        assert result["contextBudget"]["maxContextTokens"] == 128000
        # ProviderAdapter doesn't have stream(), so streaming should be False
        assert result["streamingEnabled"] is False

    def test_legacy_prompt_cache_defaults_are_lifted_to_cache_friendly_policy(self, tmp_path: Any) -> None:
        _server, store = _make_harness(tmp_path)
        store.update_config({"config": {
            "provider": {
                "promptCache": {
                    "enabled": True,
                    "targetFillRatio": 0.75,
                    "maxStableContextTokens": 160000,
                    "recentMessages": 64,
                    "conversationMessageMaxChars": 6000,
                }
            }
        }})

        prompt_cache = store.get_config({})["config"]["provider"]["promptCache"]

        assert prompt_cache["targetFillRatio"] == 0.92
        assert prompt_cache["maxStableContextTokens"] == 240000
        assert prompt_cache["recentMessages"] == 256
        assert prompt_cache["conversationMessageMaxChars"] == 24000
        assert prompt_cache["nearContextRatio"] == 0.92

    def test_custom_prompt_cache_policy_is_preserved(self, tmp_path: Any) -> None:
        _server, store = _make_harness(tmp_path)
        store.update_config({"config": {
            "provider": {
                "promptCache": {
                    "enabled": True,
                    "targetFillRatio": 0.88,
                    "recentMessages": 40,
                    "nearContextRatio": 0.95,
                }
            }
        }})

        prompt_cache = store.get_config({})["config"]["provider"]["promptCache"]

        assert prompt_cache["targetFillRatio"] == 0.88
        assert prompt_cache["recentMessages"] == 40
        assert prompt_cache["nearContextRatio"] == 0.95

    def test_tool_and_memory_policy(self, tmp_path: Any) -> None:
        server, store = _make_harness(tmp_path)
        result = _call(server, "config.effective")
        assert result["toolPolicy"] == "strict_whitelist"
        assert isinstance(result["memoryPolicy"], dict)


# ---------------------------------------------------------------------------
# prompt.preview tests
# ---------------------------------------------------------------------------

class TestPromptPreview:
    """prompt.preview returns composed prompt layers without executing a task."""

    def test_returns_system_prompt_and_layers(self, tmp_path: Any) -> None:
        server, store = _make_harness(tmp_path)
        result = _call(server, "prompt.preview", {"workspaceRoot": "/tmp/test"})
        assert "systemPrompt" in result
        assert isinstance(result["systemPrompt"], str)
        assert "layers" in result
        assert isinstance(result["layers"], list)
        assert len(result["layers"]) > 0
        assert "totalTokenEstimate" in result
        assert isinstance(result["totalTokenEstimate"], int)

    def test_runtime_safety_layer_is_locked(self, tmp_path: Any) -> None:
        server, store = _make_harness(tmp_path)
        result = _call(server, "prompt.preview", {"workspaceRoot": "/tmp/test"})
        safety_layers = [l for l in result["layers"] if l.get("name") == "runtime_safety"]
        assert len(safety_layers) == 1
        assert safety_layers[0].get("locked") is True
        assert safety_layers[0].get("editable") is False

    def test_role_layer_with_custom_role(self, tmp_path: Any) -> None:
        server, store = _make_harness(tmp_path)
        result = _call(server, "prompt.preview", {
            "workspaceRoot": "/tmp/test",
            "role": "child",
        })
        runtime_role_layers = [l for l in result["layers"] if l.get("name") == "runtime_role"]
        assert len(runtime_role_layers) == 1
        assert "child" in str(runtime_role_layers[0].get("role", "")).lower()

    def test_with_session_id(self, tmp_path: Any) -> None:
        server, store = _make_harness(tmp_path)
        # Create a workspace + session using tmp_path to avoid OS path differences
        workspace = store.upsert_workspace(str(tmp_path / "my-project"))
        session = store.create_session(workspace["id"], "Test session")
        result = _call(server, "prompt.preview", {"sessionId": session["id"]})
        assert "systemPrompt" in result
        assert "my-project" in result["systemPrompt"]

    def test_without_session_uses_workspace_root(self, tmp_path: Any) -> None:
        server, store = _make_harness(tmp_path)
        result = _call(server, "prompt.preview", {"workspaceRoot": "/custom/root"})
        assert "/custom/root" in result["systemPrompt"]

    def test_layers_have_token_estimates(self, tmp_path: Any) -> None:
        server, store = _make_harness(tmp_path)
        result = _call(server, "prompt.preview", {"workspaceRoot": "/tmp/test"})
        for layer in result["layers"]:
            assert "tokenEstimate" in layer
            assert isinstance(layer["tokenEstimate"], int)
            assert layer["tokenEstimate"] >= 0
