"""MCP Flow Mixin — extracted from Orchestrator.

Handles MCP server lifecycle: initialization, CRUD, tool refresh, shutdown,
graceful shutdown, and MCP event publishing.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

from ..mcp.client import McpClientManager, McpServerConfig, summarize_mcp_exception
from ..services.command_background import get_background_command_service
from ..models import RuntimeEvent


class McpFlowMixin:
    """Mixin providing MCP server lifecycle management."""

    def initialize_mcp_servers(self) -> None:
        """Connect to all enabled MCP servers and register their tools."""
        result = self._store.list_mcp_servers({"enabledOnly": True})
        for server_row in result["servers"]:
            span = self._tracer.start_span(
                "mcp_connect",
                attributes={"server_id": server_row.get("id", ""), "phase": "init"},
            )
            try:
                config = McpServerConfig.from_row(server_row)
                schemas = self._mcp_manager.sync_connect_server(config)
                for schema in schemas:
                    namespaced_name = schema["name"]
                    self._tool_registry.register(
                        namespaced_name,
                        lambda args, _name=namespaced_name: self._mcp_manager.sync_call_tool(_name, args),
                        schema,
                    )
                self._tracer.end_span(span.span_id, status="ok", attributes={"tool_count": len(schemas)})
                self._publish_mcp_event("mcp.server.connected", {
                    "serverId": server_row.get("id", ""),
                    "serverName": server_row.get("name", ""),
                    "toolCount": len(schemas),
                    "toolNames": [s.get("name", "") for s in schemas],
                })
            except Exception as exc:  # noqa: BLE001
                self._record_mcp_connect_failure(span=span, server=server_row, phase="init", exc=exc)

    def compact_session(self, params: dict[str, Any]) -> dict[str, Any]:
        """Manually trigger context compaction for a session.

        Params: ``{ sessionId: str, maxTokens?: int }``
        Returns: ``{ tokensBefore, tokensAfter, summary, strategy }``
        """
        session_id = params.get("sessionId") or params.get("session_id")
        if not session_id:
            raise ValueError("sessionId is required")
        if self._compactor is None:
            return {"tokensBefore": 0, "tokensAfter": 0, "summary": None, "strategy": "none"}

        msg_result = self._store.list_messages({"sessionId": session_id, "limit": 500})
        messages = msg_result.get("messages", [])
        if not messages:
            return {"tokensBefore": 0, "tokensAfter": 0, "summary": None, "strategy": "none"}

        max_tokens = params.get("maxTokens") or 60000
        self._fire_hooks("before_compaction", session_id, {"id": "system"}, extra_context={"sessionId": session_id, "maxTokens": max_tokens})
        compacted = self._compactor.compact(
            session_id=session_id,
            messages=messages,
            max_tokens=max_tokens,
        )
        # --- Decision trace: context compaction ---
        self._publish(
            session_id=session_id,
            task={"id": "system"},
            event_type="agent.decision.context_policy",
            payload={
                "decision": "compacted",
                "tokensBefore": compacted.tokens_before,
                "tokensAfter": compacted.tokens_after,
                "strategy": compacted.strategy,
                "compactionId": compacted.compaction_id,
            },
        )
        self._fire_hooks("after_compaction", session_id, {"id": "system"}, extra_context={"sessionId": session_id, "tokensBefore": compacted.tokens_before, "tokensAfter": compacted.tokens_after, "compactionId": compacted.compaction_id})
        return {
            "tokensBefore": compacted.tokens_before,
            "tokensAfter": compacted.tokens_after,
            "summary": compacted.summary,
            "strategy": compacted.strategy,
            "compactionId": compacted.compaction_id,
        }

    def mcp_server_list(self, params: dict[str, Any]) -> dict[str, Any]:
        result = self._store.list_mcp_servers(params)
        # Enrich with live connection status and tool counts
        all_mcp_schemas = self._mcp_manager.get_tool_schemas()
        for server in result.get("servers", []):
            sid = server.get("id", "")
            server["connected"] = self._mcp_manager.is_connected(sid)
            server["toolCount"] = sum(1 for s in all_mcp_schemas if s.get("_mcp_server_id") == sid)
        return result

    def mcp_server_create(self, params: dict[str, Any]) -> dict[str, Any]:
        self._validate_mcp_config(params)
        result = self._store.create_mcp_server(params)
        server = result["server"]
        if server.get("enabled", True):
            span = self._tracer.start_span(
                "mcp_connect",
                attributes={"server_id": server.get("id", ""), "phase": "create"},
            )
            try:
                config = McpServerConfig.from_row(server)
                schemas = self._mcp_manager.sync_connect_server(config)
                for schema in schemas:
                    namespaced_name = schema["name"]
                    self._tool_registry.register(
                        namespaced_name,
                        lambda args, _name=namespaced_name: self._mcp_manager.sync_call_tool(_name, args),
                        schema,
                    )
                self._tracer.end_span(span.span_id, status="ok", attributes={"tool_count": len(schemas)})
                self._publish_mcp_event("mcp.server.connected", {
                    "serverId": server.get("id", ""),
                    "serverName": server.get("name", ""),
                    "toolCount": len(schemas),
                    "toolNames": [s.get("name", "") for s in schemas],
                })
            except Exception as exc:  # noqa: BLE001
                self._record_mcp_connect_failure(span=span, server=server, phase="create", exc=exc)
        return result

    def mcp_server_update(self, params: dict[str, Any]) -> dict[str, Any]:
        self._validate_mcp_config(params)
        server_id = params.get("serverId") or params.get("server_id")
        span = self._tracer.start_span(
            "mcp_update",
            attributes={"server_id": server_id or ""},
        )
        # Disconnect old tools if server was connected
        if server_id and self._mcp_manager.is_connected(server_id):
            prefix = f"mcp__{server_id}__"
            self._tool_registry.unregister_prefix(prefix)
            self._mcp_manager.sync_disconnect_server(server_id)
        result = self._store.update_mcp_server(params)
        server = result["server"]
        if server.get("enabled", True):
            try:
                config = McpServerConfig.from_row(server)
                schemas = self._mcp_manager.sync_connect_server(config)
                for schema in schemas:
                    namespaced_name = schema["name"]
                    self._tool_registry.register(
                        namespaced_name,
                        lambda args, _name=namespaced_name: self._mcp_manager.sync_call_tool(_name, args),
                        schema,
                    )
                self._tracer.end_span(span.span_id, status="ok", attributes={"tool_count": len(schemas)})
                self._publish_mcp_event("mcp.server.reconnected", {
                    "serverId": server.get("id", ""),
                    "serverName": server.get("name", ""),
                    "toolCount": len(schemas),
                    "toolNames": [s.get("name", "") for s in schemas],
                })
                self._publish_mcp_event("mcp.server.updated", {
                    "serverId": server.get("id", ""),
                    "serverName": server.get("name", ""),
                    "toolCount": len(schemas),
                })
            except Exception as exc:  # noqa: BLE001
                self._record_mcp_connect_failure(span=span, server=server, phase="update", exc=exc)
        else:
            self._tracer.end_span(span.span_id, status="ok")
        return result

    def mcp_server_delete(self, params: dict[str, Any]) -> dict[str, Any]:
        server_id = params.get("serverId") or params.get("server_id")
        server_name = ""
        if server_id:
            # Try to get server name before deletion for the event
            existing = self._store.list_mcp_servers({})
            for s in existing.get("servers", []):
                if s.get("id") == server_id:
                    server_name = s.get("name", "")
                    break
            span = self._tracer.start_span(
                "mcp_disconnect",
                attributes={"server_id": server_id, "phase": "delete"},
            )
            prefix = f"mcp__{server_id}__"
            self._tool_registry.unregister_prefix(prefix)
            self._mcp_manager.sync_disconnect_server(server_id)
            self._tracer.end_span(span.span_id, status="ok")
            self._publish_mcp_event("mcp.server.disconnected", {
                "serverId": server_id,
                "serverName": server_name,
            })
        return self._store.delete_mcp_server(params)

    def mcp_tools_refresh(self, params: dict[str, Any]) -> dict[str, Any]:
        server_id = params.get("serverId") or params.get("server_id")
        span = self._tracer.start_span(
            "mcp_refresh",
            attributes={"server_id": server_id or "all"},
        )
        # Remove old tools for the target server(s)
        if server_id:
            prefix = f"mcp__{server_id}__"
            self._tool_registry.unregister_prefix(prefix)
        else:
            for sid in list(self._mcp_manager._connections):
                prefix = f"mcp__{sid}__"
                self._tool_registry.unregister_prefix(prefix)
        try:
            schemas = self._mcp_manager.sync_refresh_tools(server_id)
            for schema in schemas:
                namespaced_name = schema["name"]
                self._tool_registry.register(
                    namespaced_name,
                    lambda args, _name=namespaced_name: self._mcp_manager.sync_call_tool(_name, args),
                    schema,
                )
            self._tracer.end_span(span.span_id, status="ok", attributes={"tool_count": len(schemas)})
            self._publish_mcp_event("mcp.tools.refreshed", {
                "serverId": server_id or "all",
                "toolCount": len(schemas),
                "toolNames": [s.get("name", "") for s in schemas],
                "toolRegistryVersion": self._tool_registry.version,
            })
        except Exception as exc:  # noqa: BLE001
            self._tracer.end_span(span.span_id, status="error", attributes={"error": str(exc)})
            raise
        return {"refreshed": len(schemas), "tools": [s["name"] for s in schemas]}

    def shutdown_mcp(self) -> None:
        """Clean up all MCP server connections."""
        self._mcp_manager.shutdown()

    def graceful_shutdown(self, timeout: float = 10.0) -> None:
        """Gracefully shut down the orchestrator.

        1. Reject new tasks.
        2. Wait for running tasks to reach a pause point.
        3. Cancel tasks that didn't stop in time.
        4. Cancel all background commands.
        5. Shut down MCP connections.
        """
        import time as _time

        self._shutting_down = True
        logger.info("Graceful shutdown initiated — rejecting new tasks")

        # Wait for in-flight tasks to finish or pause.
        deadline = _time.monotonic() + timeout
        while _time.monotonic() < deadline:
            if not hasattr(self._store, "list_tasks_by_status"):
                break
            running = self._store.list_tasks_by_status(["running"])
            if not running:
                break
            logger.debug(
                "Waiting for %d running tasks to complete...", len(running),
            )
            _time.sleep(0.1)

        # Cancel any tasks still running after the deadline.
        if hasattr(self._store, "list_tasks_by_status"):
            for task in self._store.list_tasks_by_status(["running"]):
                logger.info("Force-cancelling task %s after shutdown timeout", task["id"])
                try:
                    self.cancel_task({"taskId": task["id"]})
                except Exception:  # noqa: BLE001
                    pass

        # Cancel all background commands.
        try:
            db_path = getattr(self._store, "database_path", ":memory:")
            service = get_background_command_service(db_path)
            for cmd_id in service.active_command_ids():
                try:
                    service.cancel_command(cmd_id)
                except Exception:  # noqa: BLE001
                    pass
        except Exception:  # noqa: BLE001
            pass

        self.shutdown_mcp()
        logger.info("Graceful shutdown complete")

    def _publish_mcp_event(self, event_type: str, payload: dict[str, Any]) -> None:
        """Publish an MCP lifecycle event (no task/session context)."""
        event = RuntimeEvent(
            event_id=self._store.new_id("evt"),
            session_id="",
            task_id="",
            type=event_type,
            ts=self._store.now(),
            payload=payload,
            visibility="panel",
        )
        self._event_bus.publish(event)

    def _record_mcp_connect_failure(
        self,
        *,
        span: Any,
        server: dict[str, Any],
        phase: str,
        exc: BaseException,
    ) -> str:
        summary = summarize_mcp_exception(exc)
        server_id = server.get("id", "")
        server_name = server.get("name", "")
        self._tracer.end_span(span.span_id, status="error", attributes={"error": summary})
        logger.warning("Failed to connect MCP server %s: %s", server_id, summary)
        logger.debug("MCP server %s connection traceback", server_id, exc_info=True)
        self._publish_mcp_event(
            "mcp.server.failed",
            {
                "serverId": server_id,
                "serverName": server_name,
                "phase": phase,
                "error": summary,
                "transport": server.get("transport"),
                "command": server.get("command"),
            },
        )


