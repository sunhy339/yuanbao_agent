"""Phase 4 tests: MCP Client integration.

Covers:
- Schema conversion (MCP Tool → internal format)
- Namespaced tool naming and collision prevention
- McpClientManager connect/disconnect/call_tool/refresh (mocked transports)
- SQLiteStore mcp_servers CRUD
- ToolRegistry unregister / unregister_prefix
- Integration: tools registered via Orchestrator MCP methods
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from local_agent_runtime.event_bus import EventBus
from local_agent_runtime.mcp.client import (
    McpClientManager,
    McpServerConfig,
    mcp_tool_to_internal_schema,
    parse_mcp_result,
)
from local_agent_runtime.orchestrator.service import Orchestrator
from local_agent_runtime.provider.adapter import ProviderAdapter
from local_agent_runtime.rpc.server import JsonRpcServer
from local_agent_runtime.store.sqlite_store import SQLiteStore
from local_agent_runtime.tools.registry import ToolRegistry
from mcp.types import CallToolResult, TextContent


# ── helpers ─────────────────────────────────────────────────────────────


def _make_store(tmp_path) -> SQLiteStore:
    return SQLiteStore(str(tmp_path / "test.sqlite3"))


def _make_tool(name: str = "query", description: str = "Run a query") -> MagicMock:
    tool = MagicMock()
    tool.name = name
    tool.description = description
    tool.inputSchema = {
        "type": "object",
        "properties": {"sql": {"type": "string"}},
        "required": ["sql"],
    }
    return tool


def _make_config(**overrides) -> McpServerConfig:
    defaults = {
        "id": "postgres",
        "name": "Postgres MCP",
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-postgres"],
    }
    defaults.update(overrides)
    return McpServerConfig(**defaults)


# ── schema conversion ──────────────────────────────────────────────────


class TestSchemaConversion:
    def test_mcp_tool_to_internal_schema_basic(self):
        tool = _make_tool("query", "Run a SQL query")
        schema = mcp_tool_to_internal_schema("postgres", tool)

        assert schema["name"] == "mcp__postgres__query"
        assert schema["description"] == "[MCP:postgres] Run a SQL query"
        assert schema["input_schema"]["type"] == "object"
        assert "sql" in schema["input_schema"]["properties"]
        assert "MCP external tool" in schema["safety"][0]
        assert "remote" in schema["hints"]
        assert "server:postgres" in schema["hints"]
        assert schema["_mcp_server_id"] == "postgres"
        assert schema["_mcp_tool_name"] == "query"

    def test_namespaced_tool_name_prevents_collision(self):
        tool_a = _make_tool("query")
        tool_b = _make_tool("query")

        schema_a = mcp_tool_to_internal_schema("postgres", tool_a)
        schema_b = mcp_tool_to_internal_schema("mysql", tool_b)

        assert schema_a["name"] == "mcp__postgres__query"
        assert schema_b["name"] == "mcp__mysql__query"
        assert schema_a["name"] != schema_b["name"]

    def test_empty_description_fallback(self):
        tool = _make_tool("ping")
        tool.description = None
        schema = mcp_tool_to_internal_schema("srv", tool)
        assert schema["description"] == "[MCP:srv] "

    def test_missing_input_schema_defaults(self):
        tool = MagicMock()
        tool.name = "noop"
        tool.description = "no-op"
        tool.inputSchema = None
        schema = mcp_tool_to_internal_schema("srv", tool)
        assert schema["input_schema"] == {"type": "object", "properties": {}}


class TestParseMcpResult:
    def test_success_single_text(self):
        result = CallToolResult(
            content=[TextContent(type="text", text="hello")],
            isError=False,
        )
        parsed = parse_mcp_result(result)
        assert parsed == {"status": "ok", "output": "hello"}

    def test_success_multiple_text(self):
        result = CallToolResult(
            content=[
                TextContent(type="text", text="line1"),
                TextContent(type="text", text="line2"),
            ],
            isError=False,
        )
        parsed = parse_mcp_result(result)
        assert parsed["status"] == "ok"
        assert json.loads(parsed["output"]) == ["line1", "line2"]

    def test_error_result(self):
        result = CallToolResult(
            content=[TextContent(type="text", text="connection refused")],
            isError=True,
        )
        parsed = parse_mcp_result(result)
        assert parsed["status"] == "failed"
        assert "connection refused" in parsed["error"]

    def test_empty_content(self):
        result = CallToolResult(content=[], isError=False)
        parsed = parse_mcp_result(result)
        assert parsed == {"status": "ok", "output": ""}


# ── McpServerConfig ────────────────────────────────────────────────────


class TestMcpServerConfig:
    def test_from_row(self):
        row = {
            "id": "github",
            "name": "GitHub MCP",
            "transport": "stdio",
            "command": "npx",
            "args": '["-y", "@modelcontextprotocol/server-github"]',
            "url": None,
            "headers": '{"Authorization": "Bearer x"}',
            "env": '{"GITHUB_TOKEN": "abc"}',
        }
        config = McpServerConfig.from_row(row)
        assert config.id == "github"
        assert config.transport == "stdio"
        assert config.args == ["-y", "@modelcontextprotocol/server-github"]
        assert config.headers == {"Authorization": "Bearer x"}
        assert config.env == {"GITHUB_TOKEN": "abc"}

    def test_from_row_empty_json(self):
        row = {
            "id": "s1",
            "name": "S1",
            "args": None,
            "headers": None,
            "env": None,
        }
        config = McpServerConfig.from_row(row)
        assert config.args == []
        assert config.headers is None
        assert config.env is None


# ── McpClientManager (mocked async) ────────────────────────────────────


class TestMcpClientManager:
    def test_connect_registers_tools(self, tmp_path):
        """Simulate a connected server by directly populating McpClientManager state."""
        store = _make_store(tmp_path)
        mgr = McpClientManager(store)

        from local_agent_runtime.mcp.client import _ServerConnection

        config = _make_config()
        tool1 = _make_tool("query", "Run query")
        tool2 = _make_tool("insert", "Insert row")
        schema1 = mcp_tool_to_internal_schema("postgres", tool1)
        schema2 = mcp_tool_to_internal_schema("postgres", tool2)

        conn = _ServerConnection(config)
        conn.session = MagicMock()
        conn.exit_stack = MagicMock()
        conn.tool_schemas = [schema1, schema2]
        mgr._connections["postgres"] = conn
        mgr._tool_map[schema1["name"]] = ("postgres", "query")
        mgr._tool_map[schema2["name"]] = ("postgres", "insert")

        # Check tool schemas are populated
        schemas = mgr.get_tool_schemas()
        assert len(schemas) == 2
        assert schemas[0]["name"] == "mcp__postgres__query"
        assert schemas[1]["name"] == "mcp__postgres__insert"

        # Check namespaced names
        names = mgr.get_namespaced_names()
        assert "mcp__postgres__query" in names
        assert "mcp__postgres__insert" in names

        assert mgr.is_connected("postgres")

        store.close()

    def test_disconnect_removes_tools(self, tmp_path):
        store = _make_store(tmp_path)
        mgr = McpClientManager(store)

        # Manually populate state to simulate a connected server
        config = _make_config()
        schema = mcp_tool_to_internal_schema("postgres", _make_tool("query"))
        from local_agent_runtime.mcp.client import _ServerConnection

        conn = _ServerConnection(config)
        conn.session = MagicMock()
        conn.exit_stack = MagicMock()
        conn.tool_schemas = [schema]
        mgr._connections["postgres"] = conn
        mgr._tool_map["mcp__postgres__query"] = ("postgres", "query")

        assert mgr.is_connected("postgres")

        # Disconnect (sync — no async needed if exit_stack.aclose is mocked)
        mgr._connections["postgres"].exit_stack = MagicMock()
        mgr._run_async = lambda coro: None  # skip async cleanup
        # Directly remove
        mgr._connections.pop("postgres", None)
        mgr._tool_map.pop("mcp__postgres__query", None)

        assert not mgr.is_connected("postgres")
        assert mgr.get_tool_schemas() == []
        assert mgr.get_namespaced_names() == []

        store.close()

    def test_call_tool_unknown_returns_error(self, tmp_path):
        store = _make_store(tmp_path)
        mgr = McpClientManager(store)
        result = mgr.sync_call_tool("mcp__unknown__foo", {})
        assert result["status"] == "failed"
        assert "Unknown MCP tool" in result["error"]
        store.close()

    def test_multiple_servers_no_name_collision(self):
        tool = _make_tool("query")
        schema_a = mcp_tool_to_internal_schema("postgres", tool)
        schema_b = mcp_tool_to_internal_schema("mysql", tool)
        assert schema_a["name"] != schema_b["name"]



# ── SQLiteStore mcp_servers CRUD ───────────────────────────────────────


class TestMcpServerStore:
    def test_create_and_get(self, tmp_path):
        store = _make_store(tmp_path)
        result = store.create_mcp_server({
            "id": "github",
            "name": "GitHub MCP",
            "transport": "stdio",
            "command": "npx",
            "args": ["-y", "@mcp/github"],
        })
        server = result["server"]
        assert server["id"] == "github"
        assert server["name"] == "GitHub MCP"
        assert server["transport"] == "stdio"
        assert server["enabled"] == 1
        assert server["args"] == ["-y", "@mcp/github"]
        store.close()

    def test_list_all_and_enabled_only(self, tmp_path):
        store = _make_store(tmp_path)
        store.create_mcp_server({"id": "s1", "name": "S1", "enabled": True})
        store.create_mcp_server({"id": "s2", "name": "S2", "enabled": False})

        all_servers = store.list_mcp_servers({})["servers"]
        assert len(all_servers) == 2

        enabled = store.list_mcp_servers({"enabledOnly": True})["servers"]
        assert len(enabled) == 1
        assert enabled[0]["id"] == "s1"
        store.close()

    def test_update(self, tmp_path):
        store = _make_store(tmp_path)
        store.create_mcp_server({"id": "s1", "name": "Original", "transport": "stdio"})
        result = store.update_mcp_server({"serverId": "s1", "name": "Updated", "enabled": False})
        assert result["server"]["name"] == "Updated"
        assert result["server"]["enabled"] == 0
        store.close()

    def test_delete(self, tmp_path):
        store = _make_store(tmp_path)
        store.create_mcp_server({"id": "s1", "name": "S1"})
        result = store.delete_mcp_server({"serverId": "s1"})
        assert result["deleted"] is True

        with pytest.raises(ValueError, match="not found"):
            store.get_mcp_server({"serverId": "s1"})
        store.close()

    def test_get_not_found(self, tmp_path):
        store = _make_store(tmp_path)
        with pytest.raises(ValueError, match="not found"):
            store.get_mcp_server({"serverId": "nope"})
        store.close()

    def test_create_auto_id(self, tmp_path):
        store = _make_store(tmp_path)
        result = store.create_mcp_server({"name": "Auto ID"})
        assert result["server"]["id"].startswith("mcp_")
        store.close()

    def test_create_requires_name(self, tmp_path):
        store = _make_store(tmp_path)
        with pytest.raises(ValueError, match="name is required"):
            store.create_mcp_server({"id": "x"})
        store.close()

    def test_serialize_parses_json_fields(self, tmp_path):
        store = _make_store(tmp_path)
        result = store.create_mcp_server({
            "id": "s1",
            "name": "S1",
            "args": ["a", "b"],
            "headers": {"X-Key": "val"},
            "env": {"FOO": "bar"},
        })
        server = result["server"]
        assert server["args"] == ["a", "b"]
        assert server["headers"] == {"X-Key": "val"}
        assert server["env"] == {"FOO": "bar"}
        store.close()


# ── ToolRegistry unregister ────────────────────────────────────────────


class TestToolRegistryUnregister:
    def test_unregister_single(self):
        reg = ToolRegistry()
        handler = lambda p: {"status": "ok"}
        reg.register("test_tool", handler, {"name": "test_tool", "description": "test"})
        assert reg.has_tool("test_tool")

        reg.unregister("test_tool")
        assert not reg.has_tool("test_tool")

    def test_unregister_nonexistent_is_noop(self):
        reg = ToolRegistry()
        reg.unregister("ghost")  # should not raise

    def test_unregister_prefix(self):
        reg = ToolRegistry()
        handler = lambda p: {"status": "ok"}
        reg.register("mcp__pg__query", handler, {"name": "mcp__pg__query", "description": "q"})
        reg.register("mcp__pg__insert", handler, {"name": "mcp__pg__insert", "description": "i"})
        reg.register("mcp__gh__issue", handler, {"name": "mcp__gh__issue", "description": "g"})
        reg.register("read_file", handler, {"name": "read_file", "description": "r"})

        count = reg.unregister_prefix("mcp__pg__")
        assert count == 2
        assert not reg.has_tool("mcp__pg__query")
        assert not reg.has_tool("mcp__pg__insert")
        assert reg.has_tool("mcp__gh__issue")
        assert reg.has_tool("read_file")

    def test_unregister_prefix_no_match(self):
        reg = ToolRegistry()
        count = reg.unregister_prefix("mcp__none__")
        assert count == 0


# ── Integration: Orchestrator + RPC ────────────────────────────────────


class TestMcpRpcIntegration:
    """Test MCP RPC methods through the full server harness (mocked MCP transport)."""

    def _harness(self, tmp_path):
        db_path = tmp_path / "harness.sqlite3"
        store = SQLiteStore(str(db_path))
        event_bus = EventBus()
        tool_registry = ToolRegistry()
        provider = ProviderAdapter()
        orchestrator = Orchestrator(
            store=store,
            event_bus=event_bus,
            tool_registry=tool_registry,
            provider=provider,
        )
        server = JsonRpcServer(orchestrator=orchestrator, store=store, event_bus=event_bus)
        return server, store, orchestrator

    def _call(self, server, method, params):
        envelope = {
            "jsonrpc": "2.0",
            "id": "test-1",
            "method": method,
            "params": params,
        }
        return server.handle_line(json.dumps(envelope))

    def test_mcp_server_list_empty(self, tmp_path):
        server, store, _ = self._harness(tmp_path)
        resp = self._call(server, "mcp.server.list", {})
        assert "result" in resp
        assert resp["result"]["servers"] == []
        store.close()

    def test_mcp_server_create_and_list(self, tmp_path):
        server, store, orch = self._harness(tmp_path)
        # Patch sync_connect_server to avoid actual MCP connection
        with patch.object(orch._mcp_manager, "sync_connect_server", return_value=[]):
            resp = self._call(server, "mcp.server.create", {
                "id": "test-srv",
                "name": "Test Server",
                "transport": "stdio",
                "command": "echo",
            })
        assert "result" in resp
        assert resp["result"]["server"]["id"] == "test-srv"

        resp2 = self._call(server, "mcp.server.list", {})
        assert len(resp2["result"]["servers"]) == 1
        store.close()

    def test_mcp_server_delete(self, tmp_path):
        server, store, orch = self._harness(tmp_path)
        with patch.object(orch._mcp_manager, "sync_connect_server", return_value=[]):
            self._call(server, "mcp.server.create", {
                "id": "del-me",
                "name": "Delete Me",
            })

        resp = self._call(server, "mcp.server.delete", {"serverId": "del-me"})
        assert resp["result"]["deleted"] is True

        resp2 = self._call(server, "mcp.server.list", {})
        assert len(resp2["result"]["servers"]) == 0
        store.close()

    def test_mcp_server_create_registers_tools(self, tmp_path):
        """When creating a server, discovered tools should be registered in ToolRegistry."""
        server, store, orch = self._harness(tmp_path)
        schema = mcp_tool_to_internal_schema("pg", _make_tool("query"))

        with patch.object(
            orch._mcp_manager, "sync_connect_server", return_value=[schema]
        ):
            self._call(server, "mcp.server.create", {
                "id": "pg",
                "name": "Postgres",
                "transport": "stdio",
                "command": "pg-mcp",
            })

        assert orch._tool_registry.has_tool("mcp__pg__query")
        store.close()

    def test_mcp_server_delete_unregisters_tools(self, tmp_path):
        server, store, orch = self._harness(tmp_path)
        schema = mcp_tool_to_internal_schema("pg", _make_tool("query"))

        with patch.object(orch._mcp_manager, "sync_connect_server", return_value=[schema]):
            self._call(server, "mcp.server.create", {
                "id": "pg",
                "name": "Postgres",
            })

        assert orch._tool_registry.has_tool("mcp__pg__query")

        with patch.object(orch._mcp_manager, "sync_disconnect_server"):
            self._call(server, "mcp.server.delete", {"serverId": "pg"})

        assert not orch._tool_registry.has_tool("mcp__pg__query")
        store.close()

    def test_mcp_tools_refresh(self, tmp_path):
        server, store, orch = self._harness(tmp_path)
        schema_old = mcp_tool_to_internal_schema("pg", _make_tool("query"))
        schema_new = mcp_tool_to_internal_schema("pg", _make_tool("query"))
        schema_new2 = mcp_tool_to_internal_schema("pg", _make_tool("insert", "Insert"))

        with patch.object(orch._mcp_manager, "sync_connect_server", return_value=[schema_old]):
            self._call(server, "mcp.server.create", {
                "id": "pg",
                "name": "Postgres",
            })

        with patch.object(orch._mcp_manager, "sync_refresh_tools", return_value=[schema_new, schema_new2]):
            resp = self._call(server, "mcp.tools.refresh", {"serverId": "pg"})

        assert resp["result"]["refreshed"] == 2
        assert "mcp__pg__query" in resp["result"]["tools"]
        assert "mcp__pg__insert" in resp["result"]["tools"]
        store.close()

    def test_mcp_server_update_reconnects(self, tmp_path):
        server, store, orch = self._harness(tmp_path)
        schema = mcp_tool_to_internal_schema("pg", _make_tool("query"))

        with patch.object(orch._mcp_manager, "sync_connect_server", return_value=[schema]):
            self._call(server, "mcp.server.create", {
                "id": "pg",
                "name": "Postgres",
            })

        # Update name, should disconnect and reconnect
        with patch.object(orch._mcp_manager, "is_connected", return_value=True), \
             patch.object(orch._mcp_manager, "sync_disconnect_server"), \
             patch.object(orch._mcp_manager, "sync_connect_server", return_value=[schema]):
            resp = self._call(server, "mcp.server.update", {
                "serverId": "pg",
                "name": "Postgres Updated",
            })

        assert resp["result"]["server"]["name"] == "Postgres Updated"
        store.close()

    def test_unsupported_method_returns_error(self, tmp_path):
        server, store, _ = self._harness(tmp_path)
        resp = self._call(server, "mcp.server.nonexistent", {})
        assert "error" in resp
        assert "NOT_FOUND" in resp["error"]["code"]
        store.close()


# ── McpClientManager shutdown ──────────────────────────────────────────


class TestMcpClientManagerShutdown:
    def test_shutdown_with_no_loop(self, tmp_path):
        store = _make_store(tmp_path)
        mgr = McpClientManager(store)
        mgr.shutdown()  # should not raise
        store.close()

    def test_shutdown_disconnects_all(self, tmp_path):
        store = _make_store(tmp_path)
        mgr = McpClientManager(store)

        # Fake connected servers
        from local_agent_runtime.mcp.client import _ServerConnection

        for sid in ("s1", "s2"):
            config = _make_config(id=sid)
            conn = _ServerConnection(config)
            conn.session = MagicMock()
            conn.exit_stack = MagicMock()
            conn.tool_schemas = [mcp_tool_to_internal_schema(sid, _make_tool())]
            mgr._connections[sid] = conn
            mgr._tool_map[f"mcp__{sid}__query"] = (sid, "query")

        # Mock _run_async to avoid real async
        mgr._run_async = MagicMock()
        loop_mock = MagicMock()
        mgr._loop = loop_mock
        thread = MagicMock()
        mgr._thread = thread

        mgr.shutdown()

        # Should have attempted disconnect for each server
        assert mgr._run_async.call_count == 2
        loop_mock.call_soon_threadsafe.assert_called_once_with(loop_mock.stop)
        thread.join.assert_called_once_with(timeout=5)
        assert mgr._loop is None
        assert mgr._thread is None
        store.close()


# ── McpClientManager transport selection ───────────────────────────────


class TestMcpClientManagerTransport:
    def test_unsupported_transport_raises(self):
        import asyncio

        config = McpServerConfig(
            id="bad",
            name="Bad",
            transport="websocket",
        )
        mgr = McpClientManager(MagicMock())
        with pytest.raises(ValueError, match="Unsupported MCP transport"):
            asyncio.run(mgr._open_transport(config))
