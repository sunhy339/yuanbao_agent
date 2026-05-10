"""Tests for MCP server config validation in mcp_server_create / mcp_server_update.

Covers:
  - stdio transport: missing command, bad args, suspicious flags
  - sse / streamable_http transport: missing url
  - env / headers type checks
  - valid configs pass without error
  - RPC-level validation (create + update)
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from local_agent_runtime.event_bus import EventBus
from local_agent_runtime.orchestrator.service import Orchestrator
from local_agent_runtime.rpc.server import JsonRpcServer
from local_agent_runtime.store.sqlite_store import SQLiteStore
from local_agent_runtime.tools.registry import ToolRegistry


# ── helpers ────────────────────────────────────────────────────────────────


def _make_runtime(tmp_path: Any) -> SimpleNamespace:
    event_bus = EventBus()
    store = SQLiteStore(str(tmp_path / "runtime.sqlite3"))
    tool_registry = ToolRegistry()

    class DummyProvider:
        def generate(self, prompt: str, context: dict[str, Any]) -> dict[str, Any]:
            return {"text": "ok", "status": "done"}

    orchestrator = Orchestrator(
        store=store, event_bus=event_bus,
        tool_registry=tool_registry, provider=DummyProvider(),
    )
    server = JsonRpcServer(orchestrator=orchestrator, store=store, event_bus=event_bus)
    return SimpleNamespace(server=server, store=store)


def _rpc(runtime: SimpleNamespace, method: str, params: dict[str, Any]) -> dict[str, Any]:
    request_id = f"req_{method}"
    envelope = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
    return runtime.server.handle_line(json.dumps(envelope, ensure_ascii=False))


# ── direct validation tests ────────────────────────────────────────────────


class TestMcpConfigValidation:
    """Unit tests calling _validate_mcp_config directly."""

    @pytest.fixture()
    def orch(self, tmp_path: Any) -> Orchestrator:
        event_bus = EventBus()
        store = SQLiteStore(str(tmp_path / "test.sqlite3"))
        tool_registry = ToolRegistry()

        class DummyProvider:
            def generate(self, prompt: str, context: dict[str, Any]) -> dict[str, Any]:
                return {"text": "ok", "status": "done"}

        return Orchestrator(
            store=store, event_bus=event_bus,
            tool_registry=tool_registry, provider=DummyProvider(),
        )

    # stdio: missing command
    def test_stdio_missing_command_raises(self, orch: Orchestrator) -> None:
        with pytest.raises(ValueError, match="command"):
            orch._validate_mcp_config({"transport": "stdio", "command": ""})

    def test_stdio_none_command_raises(self, orch: Orchestrator) -> None:
        with pytest.raises(ValueError, match="command"):
            orch._validate_mcp_config({"transport": "stdio"})

    def test_stdio_whitespace_command_raises(self, orch: Orchestrator) -> None:
        with pytest.raises(ValueError, match="command"):
            orch._validate_mcp_config({"transport": "stdio", "command": "   "})

    # stdio: bad args
    def test_stdio_args_not_array_raises(self, orch: Orchestrator) -> None:
        with pytest.raises(ValueError, match="string array"):
            orch._validate_mcp_config({
                "transport": "stdio", "command": "npx",
                "args": "--port 8080",
            })

    def test_stdio_args_non_string_element_raises(self, orch: Orchestrator) -> None:
        with pytest.raises(ValueError, match="args\\[1\\]"):
            orch._validate_mcp_config({
                "transport": "stdio", "command": "npx",
                "args": ["--port", 8080],
            })

    # sse: missing url
    def test_sse_missing_url_raises(self, orch: Orchestrator) -> None:
        with pytest.raises(ValueError, match="url"):
            orch._validate_mcp_config({"transport": "sse"})

    def test_sse_empty_url_raises(self, orch: Orchestrator) -> None:
        with pytest.raises(ValueError, match="url"):
            orch._validate_mcp_config({"transport": "sse", "url": ""})

    # streamable_http: missing url
    def test_streamable_http_missing_url_raises(self, orch: Orchestrator) -> None:
        with pytest.raises(ValueError, match="url"):
            orch._validate_mcp_config({"transport": "streamable_http"})

    # env type check
    def test_env_not_dict_raises(self, orch: Orchestrator) -> None:
        with pytest.raises(ValueError, match="env"):
            orch._validate_mcp_config({
                "transport": "stdio", "command": "npx",
                "env": "bad",
            })

    # headers type check
    def test_headers_not_dict_raises(self, orch: Orchestrator) -> None:
        with pytest.raises(ValueError, match="headers"):
            orch._validate_mcp_config({
                "transport": "sse", "url": "http://localhost:8080",
                "headers": [1, 2],
            })

    # valid configs pass
    def test_valid_stdio_config_passes(self, orch: Orchestrator) -> None:
        orch._validate_mcp_config({
            "transport": "stdio",
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-memory"],
        })

    def test_valid_sse_config_passes(self, orch: Orchestrator) -> None:
        orch._validate_mcp_config({
            "transport": "sse",
            "url": "http://localhost:8080/sse",
        })

    def test_valid_stdio_with_env_passes(self, orch: Orchestrator) -> None:
        orch._validate_mcp_config({
            "transport": "stdio",
            "command": "python",
            "args": ["server.py"],
            "env": {"API_KEY": "test"},
        })


# ── RPC-level validation tests ─────────────────────────────────────────────


class TestMcpConfigValidationRpc:
    """Tests that validation fires through RPC handlers."""

    def test_rpc_create_rejects_bad_config(self, tmp_path: Any) -> None:
        runtime = _make_runtime(tmp_path)
        resp = _rpc(runtime, "mcp.server.create", {
            "name": "bad-server",
            "transport": "stdio",
            "command": "",
        })
        assert "error" in resp
        assert "command" in resp["error"]["message"]

    def test_rpc_update_rejects_bad_config(self, tmp_path: Any) -> None:
        runtime = _make_runtime(tmp_path)
        # First create a valid server
        store = runtime.store
        result = store.create_mcp_server({
            "name": "test-server",
            "transport": "stdio",
            "command": "npx",
            "enabled": False,
        })
        server_id = result["server"]["id"]

        # Now update with bad config
        resp = _rpc(runtime, "mcp.server.update", {
            "serverId": server_id,
            "transport": "sse",
            "url": "",
        })
        assert "error" in resp
        assert "url" in resp["error"]["message"]

    def test_rpc_create_rejects_non_array_args(self, tmp_path: Any) -> None:
        runtime = _make_runtime(tmp_path)
        resp = _rpc(runtime, "mcp.server.create", {
            "name": "bad-args",
            "transport": "stdio",
            "command": "npx",
            "args": "not-an-array",
        })
        assert "error" in resp
        assert "string array" in resp["error"]["message"]

    def test_rpc_create_rejects_bad_env(self, tmp_path: Any) -> None:
        runtime = _make_runtime(tmp_path)
        resp = _rpc(runtime, "mcp.server.create", {
            "name": "bad-env",
            "transport": "stdio",
            "command": "npx",
            "env": "bad-env",
        })
        assert "error" in resp
        assert "env" in resp["error"]["message"]


class TestFirecrawlConfig:
    """Firecrawl MCP server config: save and retrieve with env preserved."""

    def test_firecrawl_config_save_success(self, tmp_path: Any) -> None:
        """Firecrawl stdio config with API key in env saves correctly."""
        runtime = _make_runtime(tmp_path)
        resp = _rpc(runtime, "mcp.server.create", {
            "name": "Firecrawl",
            "transport": "stdio",
            "command": "npx",
            "args": ["-y", "@anthropic/mcp-server-firecrawl"],
            "env": {"FIRECRAWL_API_KEY": "fc-test-key-12345"},
            "enabled": False,
        })
        assert "result" in resp, f"Expected success, got error: {resp.get('error')}"
        server = resp["result"]["server"]
        assert server["name"] == "Firecrawl"
        assert server["transport"] == "stdio"
        assert server["command"] == "npx"

    def test_firecrawl_env_preserved(self, tmp_path: Any) -> None:
        """API key in env is stored and can be retrieved without loss."""
        runtime = _make_runtime(tmp_path)
        resp = _rpc(runtime, "mcp.server.create", {
            "name": "Firecrawl",
            "transport": "stdio",
            "command": "npx",
            "args": ["-y", "@anthropic/mcp-server-firecrawl"],
            "env": {"FIRECRAWL_API_KEY": "fc-secret-key"},
            "enabled": False,
        })
        server_id = resp["result"]["server"]["id"]

        # Retrieve via list
        list_resp = _rpc(runtime, "mcp.server.list", {})
        servers = list_resp["result"]["servers"]
        fc = next(s for s in servers if s["id"] == server_id)
        assert fc["env"] == {"FIRECRAWL_API_KEY": "fc-secret-key"}

    def test_firecrawl_missing_api_key_passes_validation(self, tmp_path: Any) -> None:
        """Config without FIRECRAWL_API_KEY still validates (env may be empty).

        The API key is typically injected at runtime. Config validation only
        checks structure, not whether keys are present.
        """
        runtime = _make_runtime(tmp_path)
        resp = _rpc(runtime, "mcp.server.create", {
            "name": "Firecrawl-no-key",
            "transport": "stdio",
            "command": "npx",
            "args": ["-y", "@anthropic/mcp-server-firecrawl"],
            "enabled": False,
        })
        assert "result" in resp, f"Expected success, got error: {resp.get('error')}"

    def test_firecrawl_update_preserves_env(self, tmp_path: Any) -> None:
        """Updating firecrawl config with same transport preserves env."""
        runtime = _make_runtime(tmp_path)
        create_resp = _rpc(runtime, "mcp.server.create", {
            "name": "Firecrawl",
            "transport": "stdio",
            "command": "npx",
            "args": ["-y", "@anthropic/mcp-server-firecrawl"],
            "env": {"FIRECRAWL_API_KEY": "fc-original-key"},
            "enabled": False,
        })
        server_id = create_resp["result"]["server"]["id"]

        # Update name + keep full config (validation requires transport fields)
        update_resp = _rpc(runtime, "mcp.server.update", {
            "serverId": server_id,
            "name": "Firecrawl Renamed",
            "transport": "stdio",
            "command": "npx",
            "args": ["-y", "@anthropic/mcp-server-firecrawl"],
            "env": {"FIRECRAWL_API_KEY": "fc-original-key"},
            "enabled": False,
        })
        assert "result" in update_resp

        list_resp = _rpc(runtime, "mcp.server.list", {})
        fc = next(s for s in list_resp["result"]["servers"] if s["id"] == server_id)
        assert fc["name"] == "Firecrawl Renamed"
        assert fc["env"] == {"FIRECRAWL_API_KEY": "fc-original-key"}
