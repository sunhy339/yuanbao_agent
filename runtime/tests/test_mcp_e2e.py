"""MCP end-to-end integration tests.

Covers the full lifecycle through Orchestrator + RPC + Store + ToolRegistry,
using mocked MCP transports.  Focuses on flows NOT covered by the existing
unit-level test_mcp_client.py:

- initialize_mcp_servers (startup reconnection)
- create with enabled=False (no connection)
- update that disables a connected server
- tool execution via ToolRegistry after MCP registration
- refresh all servers (serverId=None)
- refresh error propagation
- multi-server tool isolation
- concurrent create+delete+refresh
- shutdown through orchestrator
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
    _ServerConnection,
    mcp_tool_to_internal_schema,
)
from local_agent_runtime.orchestrator.service import Orchestrator
from local_agent_runtime.provider.adapter import ProviderAdapter
from local_agent_runtime.rpc.server import JsonRpcServer
from local_agent_runtime.store.sqlite_store import SQLiteStore
from local_agent_runtime.tools.registry import ToolRegistry


# ── helpers ─────────────────────────────────────────────────────────────


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


def _make_schema(server_id: str, tool_name: str, description: str = "") -> dict[str, Any]:
    return mcp_tool_to_internal_schema(server_id, _make_tool(tool_name, description))


def _build_harness(tmp_path, *, with_builtin_tools: bool = False):
    """Build a full stack and return (server, store, orchestrator, events)."""
    db_path = tmp_path / "e2e.sqlite3"
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

    events: list[dict[str, Any]] = []
    event_bus.subscribe(lambda event: events.append(event_bus.as_payload(event)))

    return server, store, orchestrator, events


def _call(server: JsonRpcServer, method: str, params: dict[str, Any]) -> dict[str, Any]:
    envelope = {
        "jsonrpc": "2.0",
        "id": "e2e-1",
        "method": method,
        "params": params,
    }
    return server.handle_line(json.dumps(envelope))


def _connect_patch(orch: Orchestrator, schemas: list[dict[str, Any]]):
    """Return a patch for sync_connect_server that returns *schemas*."""
    return patch.object(orch._mcp_manager, "sync_connect_server", return_value=schemas)


# ── initialize_mcp_servers ─────────────────────────────────────────────


class TestInitializeMcpServers:
    """Startup reconnection: enabled servers in DB should be reconnected."""

    def test_reconnects_enabled_servers(self, tmp_path):
        server, store, orch, events = _build_harness(tmp_path)

        # Pre-seed two servers: one enabled, one disabled
        store.create_mcp_server({"id": "pg", "name": "PG", "enabled": True})
        store.create_mcp_server({"id": "mysql", "name": "MySQL", "enabled": False})

        schema_pg = _make_schema("pg", "query")
        with _connect_patch(orch, [schema_pg]):
            orch.initialize_mcp_servers()

        # pg should be connected and tool registered
        assert orch._tool_registry.has_tool("mcp__pg__query")
        # mysql should NOT be connected (disabled)
        assert not orch._mcp_manager.is_connected("mysql")

        store.close()

    def test_connect_failure_does_not_block_others(self, tmp_path):
        server, store, orch, _ = _build_harness(tmp_path)

        store.create_mcp_server({"id": "bad", "name": "Bad", "enabled": True})
        store.create_mcp_server({"id": "good", "name": "Good", "enabled": True})

        schema_good = _make_schema("good", "search")
        call_count = 0

        def _connect_side_effect(config):
            nonlocal call_count
            call_count += 1
            if config.id == "bad":
                raise ConnectionError("cannot reach bad server")
            return [schema_good]

        with patch.object(orch._mcp_manager, "sync_connect_server", side_effect=_connect_side_effect):
            orch.initialize_mcp_servers()

        # bad failed but good should still be registered
        assert orch._tool_registry.has_tool("mcp__good__search")
        assert call_count == 2
        failed_events = [event for event in events if event["type"] == "mcp.server.failed"]
        assert len(failed_events) == 1
        assert failed_events[0]["payload"]["serverId"] == "bad"
        assert failed_events[0]["payload"]["error"] == "ConnectionError: cannot reach bad server"

        store.close()

    def test_no_enabled_servers_is_noop(self, tmp_path):
        _, store, orch, _ = _build_harness(tmp_path)

        store.create_mcp_server({"id": "s1", "name": "S1", "enabled": False})
        orch.initialize_mcp_servers()

        assert len(orch._tool_registry.list_tools()) == 0
        store.close()


# ── create with enabled=False ──────────────────────────────────────────


class TestCreateDisabledServer:
    def test_disabled_server_not_connected(self, tmp_path):
        server, store, orch, _ = _build_harness(tmp_path)

        resp = _call(server, "mcp.server.create", {
            "id": "pg",
            "name": "Postgres",
            "enabled": False,
        })
        assert "result" in resp
        assert resp["result"]["server"]["enabled"] == 0

        # No tools should be registered
        assert not orch._tool_registry.has_tool("mcp__pg__query")
        assert not orch._mcp_manager.is_connected("pg")

        store.close()


# ── update disables connected server ───────────────────────────────────


class TestUpdateDisablesServer:
    def test_update_enabled_false_disconnects(self, tmp_path):
        server, store, orch, _ = _build_harness(tmp_path)

        schema = _make_schema("pg", "query")

        # Create enabled server
        with _connect_patch(orch, [schema]):
            _call(server, "mcp.server.create", {"id": "pg", "name": "PG"})

        assert orch._tool_registry.has_tool("mcp__pg__query")

        # Now disable it
        with patch.object(orch._mcp_manager, "is_connected", return_value=True), \
             patch.object(orch._mcp_manager, "sync_disconnect_server"):
            resp = _call(server, "mcp.server.update", {
                "serverId": "pg",
                "enabled": False,
            })

        assert resp["result"]["server"]["enabled"] == 0
        assert not orch._tool_registry.has_tool("mcp__pg__query")

        store.close()

    def test_update_reconnects_with_new_tools(self, tmp_path):
        server, store, orch, _ = _build_harness(tmp_path)

        old_schema = _make_schema("pg", "query")
        new_schemas = [_make_schema("pg", "query"), _make_schema("pg", "insert")]

        # Create with one tool
        with _connect_patch(orch, [old_schema]):
            _call(server, "mcp.server.create", {"id": "pg", "name": "PG"})

        assert orch._tool_registry.has_tool("mcp__pg__query")
        assert not orch._tool_registry.has_tool("mcp__pg__insert")

        # Update → disconnect old + reconnect with two tools
        with patch.object(orch._mcp_manager, "is_connected", return_value=True), \
             patch.object(orch._mcp_manager, "sync_disconnect_server"), \
             _connect_patch(orch, new_schemas):
            _call(server, "mcp.server.update", {"serverId": "pg", "name": "PG v2"})

        assert orch._tool_registry.has_tool("mcp__pg__query")
        assert orch._tool_registry.has_tool("mcp__pg__insert")

        store.close()


# ── tool execution via ToolRegistry ────────────────────────────────────


class TestMcpToolExecution:
    def test_registered_tool_callable_through_registry(self, tmp_path):
        server, store, orch, _ = _build_harness(tmp_path)

        schema = _make_schema("calc", "add")

        with _connect_patch(orch, [schema]):
            _call(server, "mcp.server.create", {"id": "calc", "name": "Calculator"})

        # The tool handler delegates to sync_call_tool — mock it
        with patch.object(
            orch._mcp_manager,
            "sync_call_tool",
            return_value={"status": "ok", "output": "42"},
        ) as mock_call:
            result = orch._tool_registry.execute("mcp__calc__add", {"sql": "SELECT 1"}, session_id="s1")

        assert result["status"] == "ok"
        assert result["output"] == "42"
        mock_call.assert_called_once_with("mcp__calc__add", {"sql": "SELECT 1"})

        store.close()

    def test_tool_execution_after_server_deleted_fails(self, tmp_path):
        server, store, orch, _ = _build_harness(tmp_path)

        schema = _make_schema("calc", "add")

        with _connect_patch(orch, [schema]):
            _call(server, "mcp.server.create", {"id": "calc", "name": "Calc"})

        # Delete the server
        with patch.object(orch._mcp_manager, "sync_disconnect_server"):
            _call(server, "mcp.server.delete", {"serverId": "calc"})

        # Tool should be gone from registry
        assert not orch._tool_registry.has_tool("mcp__calc__add")

        store.close()

    def test_multi_server_tools_isolated(self, tmp_path):
        """Tools from different servers with same raw name should coexist."""
        server, store, orch, _ = _build_harness(tmp_path)

        schema_a = _make_schema("pg", "query", "PG query")
        schema_b = _make_schema("mysql", "query", "MySQL query")

        def _connect_side_effect(config):
            if config.id == "pg":
                return [schema_a]
            return [schema_b]

        with patch.object(orch._mcp_manager, "sync_connect_server", side_effect=_connect_side_effect):
            _call(server, "mcp.server.create", {"id": "pg", "name": "PG"})
            _call(server, "mcp.server.create", {"id": "mysql", "name": "MySQL"})

        assert orch._tool_registry.has_tool("mcp__pg__query")
        assert orch._tool_registry.has_tool("mcp__mysql__query")

        # Deleting pg should not affect mysql
        with patch.object(orch._mcp_manager, "sync_disconnect_server"):
            _call(server, "mcp.server.delete", {"serverId": "pg"})

        assert not orch._tool_registry.has_tool("mcp__pg__query")
        assert orch._tool_registry.has_tool("mcp__mysql__query")

        store.close()


# ── refresh all servers ────────────────────────────────────────────────


class TestRefreshAllServers:
    def test_refresh_all_with_no_server_id(self, tmp_path):
        server, store, orch, _ = _build_harness(tmp_path)

        schema_a = _make_schema("pg", "query")
        schema_b = _make_schema("redis", "get")

        def _connect_side_effect(config):
            return [schema_a] if config.id == "pg" else [schema_b]

        # Create two servers
        with patch.object(orch._mcp_manager, "sync_connect_server", side_effect=_connect_side_effect):
            _call(server, "mcp.server.create", {"id": "pg", "name": "PG"})
            _call(server, "mcp.server.create", {"id": "redis", "name": "Redis"})

        # Refresh all
        new_schemas = [_make_schema("pg", "query"), _make_schema("pg", "insert")]
        with patch.object(orch._mcp_manager, "sync_refresh_tools", return_value=new_schemas):
            resp = _call(server, "mcp.tools.refresh", {})

        assert "result" in resp
        assert resp["result"]["refreshed"] == 2

        store.close()

    def test_refresh_error_propagates(self, tmp_path):
        server, store, orch, _ = _build_harness(tmp_path)

        schema = _make_schema("pg", "query")
        with _connect_patch(orch, [schema]):
            _call(server, "mcp.server.create", {"id": "pg", "name": "PG"})

        with patch.object(
            orch._mcp_manager,
            "sync_refresh_tools",
            side_effect=RuntimeError("transport lost"),
        ):
            resp = _call(server, "mcp.tools.refresh", {"serverId": "pg"})

        assert "error" in resp
        assert "transport lost" in resp["error"]["message"]

        store.close()


# ── full lifecycle ─────────────────────────────────────────────────────


class TestMcpFullLifecycle:
    """Create → register → execute → update → refresh → delete."""

    def test_full_lifecycle(self, tmp_path):
        server, store, orch, events = _build_harness(tmp_path)

        # ── Step 1: Create server with one tool
        schema_v1 = _make_schema("srv", "search", "Search docs")
        with _connect_patch(orch, [schema_v1]):
            resp = _call(server, "mcp.server.create", {
                "id": "srv",
                "name": "Docs Server",
                "transport": "stdio",
                "command": "npx",
            })

        assert "result" in resp
        assert resp["result"]["server"]["name"] == "Docs Server"
        assert orch._tool_registry.has_tool("mcp__srv__search")

        # ── Step 2: Execute the tool
        with patch.object(
            orch._mcp_manager, "sync_call_tool",
            return_value={"status": "ok", "output": "found 3 results"},
        ):
            result = orch._tool_registry.execute("mcp__srv__search", {"sql": "SELECT * FROM docs"}, session_id="s1")
        assert result["output"] == "found 3 results"

        # ── Step 3: Update server → discovers a new tool
        schemas_v2 = [_make_schema("srv", "search"), _make_schema("srv", "index")]
        with patch.object(orch._mcp_manager, "is_connected", return_value=True), \
             patch.object(orch._mcp_manager, "sync_disconnect_server"), \
             _connect_patch(orch, schemas_v2):
            resp = _call(server, "mcp.server.update", {
                "serverId": "srv",
                "name": "Docs Server v2",
            })

        assert resp["result"]["server"]["name"] == "Docs Server v2"
        assert orch._tool_registry.has_tool("mcp__srv__search")
        assert orch._tool_registry.has_tool("mcp__srv__index")

        # ── Step 4: Refresh tools → tool list changes again
        schemas_v3 = [_make_schema("srv", "search"), _make_schema("srv", "index"), _make_schema("srv", "delete")]
        with patch.object(orch._mcp_manager, "sync_refresh_tools", return_value=schemas_v3):
            resp = _call(server, "mcp.tools.refresh", {"serverId": "srv"})

        assert resp["result"]["refreshed"] == 3
        assert "mcp__srv__delete" in resp["result"]["tools"]

        # ── Step 5: Delete server → tools unregistered
        with patch.object(orch._mcp_manager, "sync_disconnect_server"):
            resp = _call(server, "mcp.server.delete", {"serverId": "srv"})

        assert resp["result"]["deleted"] is True
        assert not orch._tool_registry.has_tool("mcp__srv__search")
        assert not orch._tool_registry.has_tool("mcp__srv__index")
        assert not orch._tool_registry.has_tool("mcp__srv__delete")

        # ── Step 6: Verify store is clean
        resp = _call(server, "mcp.server.list", {})
        assert len(resp["result"]["servers"]) == 0

        store.close()


# ── shutdown through orchestrator ──────────────────────────────────────


class TestOrchestratorShutdownMcp:
    def test_shutdown_mcp_delegates_to_manager(self, tmp_path):
        _, store, orch, _ = _build_harness(tmp_path)

        with patch.object(orch._mcp_manager, "shutdown") as mock_shutdown:
            orch.shutdown_mcp()
            mock_shutdown.assert_called_once()

        store.close()


# ── concurrent operations ──────────────────────────────────────────────


class TestMcpConcurrentOperations:
    def test_create_delete_refresh_sequential(self, tmp_path):
        """Rapid create-delete-create cycle should leave clean state."""
        server, store, orch, _ = _build_harness(tmp_path)

        schema = _make_schema("srv", "ping")

        # Create
        with _connect_patch(orch, [schema]):
            _call(server, "mcp.server.create", {"id": "srv", "name": "Srv"})

        assert orch._tool_registry.has_tool("mcp__srv__ping")

        # Delete
        with patch.object(orch._mcp_manager, "sync_disconnect_server"):
            _call(server, "mcp.server.delete", {"serverId": "srv"})

        assert not orch._tool_registry.has_tool("mcp__srv__ping")

        # Recreate with different tools
        schema2 = _make_schema("srv", "pong")
        with _connect_patch(orch, [schema2]):
            _call(server, "mcp.server.create", {"id": "srv", "name": "Srv v2"})

        assert orch._tool_registry.has_tool("mcp__srv__pong")
        assert not orch._tool_registry.has_tool("mcp__srv__ping")  # old tool stays gone

        store.close()


# ── call_tool error paths ──────────────────────────────────────────────


class TestMcpCallToolErrors:
    def test_call_tool_server_not_connected(self, tmp_path):
        store = SQLiteStore(str(tmp_path / "test.sqlite3"))
        mgr = McpClientManager(store)

        # Manually register a tool in _tool_map but no connection
        mgr._tool_map["mcp__ghost__ping"] = ("ghost", "ping")

        result = mgr.sync_call_tool("mcp__ghost__ping", {})
        assert result["status"] == "failed"
        assert "not connected" in result["error"]

        store.close()

    def test_call_tool_session_exception(self, tmp_path):
        """When the MCP session raises, call_tool should return a failed result."""
        store = SQLiteStore(str(tmp_path / "test.sqlite3"))
        mgr = McpClientManager(store)

        # Build a fake connected server with a session that raises
        config = McpServerConfig(id="srv", name="Srv", transport="stdio", command="echo")
        conn = _ServerConnection(config)
        mock_session = MagicMock()
        mock_session.call_tool = MagicMock(side_effect=RuntimeError("timeout"))
        conn.session = mock_session
        conn.exit_stack = MagicMock()
        conn.tool_schemas = [_make_schema("srv", "ping")]
        mgr._connections["srv"] = conn
        mgr._tool_map["mcp__srv__ping"] = ("srv", "ping")

        result = mgr.sync_call_tool("mcp__srv__ping", {"x": 1})
        assert result["status"] == "failed"
        assert "timeout" in result["error"]

        store.close()


class TestMcpToolVisibility:
    """Verify MCP tools are visible to the model via _provider_tools."""

    def test_registered_mcp_tool_appears_in_provider_tools(self, tmp_path):
        """MCP tool registered after ContextBuilder construction should be
        visible in the merged provider tool list."""
        _, store, orch, _ = _build_harness(tmp_path)

        schema = _make_schema("pg", "query", "PG query")

        with _connect_patch(orch, [schema]):
            _call(server := JsonRpcServer(orchestrator=orch, store=store, event_bus=EventBus()),
                  "mcp.server.create", {"id": "pg", "name": "PG"})

        # Simulate a context with openai_tools built before MCP registration
        context = {
            "openai_tools": [{"type": "function", "function": {"name": "read_file"}}],
            "tools": [{"name": "read_file"}],
        }
        provider_tools = orch._provider_tools(context)
        tool_names = [t["function"]["name"] if "function" in t else t.get("name") for t in provider_tools]

        assert "mcp__pg__query" in tool_names
        assert "read_file" in tool_names

        store.close()

    def test_mcp_tool_visible_after_context_build(self, tmp_path):
        """Register MCP tool after context is built — it should still appear."""
        _, store, orch, _ = _build_harness(tmp_path)

        # Build context first (no MCP tools yet)
        from local_agent_runtime.context.builder import ContextBuilder
        builder = ContextBuilder(store=store)
        ws = store.upsert_workspace(str(tmp_path))
        session = store.create_session(workspace_id=ws["id"], title="test")
        context = builder.build(session["id"], "test goal")

        # Now register an MCP tool
        schema = _make_schema("calc", "add")
        orch._tool_registry.register(
            "mcp__calc__add",
            lambda args: {"status": "ok", "output": "42"},
            schema,
        )

        # _provider_tools should include the newly registered tool
        provider_tools = orch._provider_tools(context)
        tool_names = [t["function"]["name"] if "function" in t else t.get("name") for t in provider_tools]

        assert "mcp__calc__add" in tool_names

        store.close()

    def test_deleted_mcp_tool_removed_from_provider_tools(self, tmp_path):
        """Deleting an MCP server should remove its tools from provider visibility."""
        _, store, orch, _ = _build_harness(tmp_path)

        schema = _make_schema("redis", "get")
        with _connect_patch(orch, [schema]):
            orch._tool_registry.register(
                "mcp__redis__get",
                lambda args: {"status": "ok"},
                schema,
            )

        # Verify it's visible
        tools_before = orch._provider_tools({})
        names_before = [t["function"]["name"] if "function" in t else t.get("name") for t in tools_before]
        assert "mcp__redis__get" in names_before

        # Unregister (simulates delete)
        orch._tool_registry.unregister("mcp__redis__get")

        tools_after = orch._provider_tools({})
        names_after = [t["function"]["name"] if "function" in t else t.get("name") for t in tools_after]
        assert "mcp__redis__get" not in names_after

        store.close()
