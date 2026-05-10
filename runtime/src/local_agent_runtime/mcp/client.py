"""MCP Client Manager — connect to external MCP Servers and bridge tools into the runtime.

Design:
- Each connected MCP server is identified by a server_id (stored in SQLite).
- Discovered tools are namespaced as ``mcp__{server_id}__{tool_name}`` to avoid
  collisions with built-in tools.
- The manager is async internally but exposes sync wrappers for the sync runtime.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from contextlib import AsyncExitStack
from typing import Any

from mcp.client.session import ClientSession
from mcp.client.sse import sse_client
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamablehttp_client
from mcp.types import CallToolResult, TextContent

logger = logging.getLogger(__name__)

# ── schema conversion ──────────────────────────────────────────────────


def mcp_tool_to_internal_schema(server_id: str, tool: Any) -> dict[str, Any]:
    """Convert an MCP ``Tool`` into the runtime's internal tool schema format.

    The namespace prefix ``mcp__{server_id}__`` is prepended to the tool name.
    """
    raw_name = getattr(tool, "name", str(tool))
    description = getattr(tool, "description", "") or ""
    input_schema = getattr(tool, "inputSchema", None) or {"type": "object", "properties": {}}

    return {
        "name": f"mcp__{server_id}__{raw_name}",
        "description": f"[MCP:{server_id}] {description}",
        "input_schema": input_schema,
        "safety": {
            "level": "medium",
            "requires_approval": False,
            "category": "execution",
            "sandboxed": False,
            "notes": ["MCP external tool — executes on remote server"],
        },
        "metadata": {},
        "hints": ["remote", f"server:{server_id}"],
        "_mcp_server_id": server_id,
        "_mcp_tool_name": raw_name,
    }


def parse_mcp_result(result: CallToolResult) -> dict[str, Any]:
    """Convert an MCP ``CallToolResult`` into the runtime's tool result format."""
    if result.isError:
        error_parts: list[str] = []
        for block in result.content:
            if isinstance(block, TextContent):
                error_parts.append(block.text)
        return {"status": "failed", "error": "\n".join(error_parts) or "MCP tool error"}

    text_parts: list[str] = []
    for block in result.content:
        if isinstance(block, TextContent):
            text_parts.append(block.text)

    if len(text_parts) == 1:
        content = text_parts[0]
    elif text_parts:
        content = json.dumps(text_parts, ensure_ascii=False)
    else:
        content = ""

    return {"status": "ok", "output": content}


def summarize_mcp_exception(exc: BaseException, *, max_parts: int = 5) -> str:
    """Return a useful one-line summary for MCP connection failures."""

    def _single_message(error: BaseException) -> str:
        message = str(error).strip()
        if isinstance(error, OSError):
            details = []
            filename = getattr(error, "filename", None)
            strerror = getattr(error, "strerror", None)
            if filename:
                details.append(str(filename))
            if strerror and str(strerror) not in message:
                details.append(str(strerror))
            if details:
                message = f"{message} ({'; '.join(details)})" if message else "; ".join(details)
        return f"{type(error).__name__}: {message}" if message else type(error).__name__

    def _walk(error: BaseException, depth: int = 0) -> list[str]:
        if depth > 8:
            return [_single_message(error)]
        if isinstance(error, BaseExceptionGroup):
            parts: list[str] = []
            for child in error.exceptions:
                parts.extend(_walk(child, depth + 1))
            return parts or [_single_message(error)]
        return [_single_message(error)]

    seen: set[str] = set()
    unique_parts: list[str] = []
    for part in _walk(exc):
        if part in seen:
            continue
        seen.add(part)
        unique_parts.append(part)

    if not unique_parts:
        return _single_message(exc)
    suffix = "" if len(unique_parts) <= max_parts else f"; +{len(unique_parts) - max_parts} more"
    return "; ".join(unique_parts[:max_parts]) + suffix


# ── server config dataclass ────────────────────────────────────────────


def strip_wrapping_shell_quotes(value: str) -> str:
    """Remove one complete layer of shell quotes from a persisted token."""
    token = value.strip()
    if len(token) >= 2 and token[0] == token[-1] and token[0] in {'"', "'"}:
        return token[1:-1]
    return token


def normalize_mcp_stdio_args(args: Any) -> list[str]:
    """Normalize persisted MCP stdio args before spawning a child process."""
    if not isinstance(args, list):
        return []
    return [strip_wrapping_shell_quotes(item) for item in args if isinstance(item, str)]


class McpServerConfig:
    """Lightweight config object for a single MCP server connection."""

    __slots__ = ("id", "name", "transport", "command", "args", "url", "headers", "env")

    def __init__(self, *, id: str, name: str, transport: str = "stdio",
                 command: str | None = None, args: list[str] | None = None,
                 url: str | None = None, headers: dict[str, str] | None = None,
                 env: dict[str, str] | None = None) -> None:
        self.id = id
        self.name = name
        self.transport = transport
        self.command = command
        self.args = args or []
        self.url = url
        self.headers = headers
        self.env = env

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> McpServerConfig:
        def _parse_json_field(value: Any, default: Any) -> Any:
            if value is None:
                return default
            if isinstance(value, str):
                return json.loads(value)
            return value  # already parsed (e.g. by _serialize_mcp_server)

        return cls(
            id=row["id"],
            name=row["name"],
            transport=row.get("transport", "stdio"),
            command=strip_wrapping_shell_quotes(row["command"]) if isinstance(row.get("command"), str) else row.get("command"),
            args=normalize_mcp_stdio_args(_parse_json_field(row.get("args"), [])),
            url=row.get("url"),
            headers=_parse_json_field(row.get("headers"), None),
            env=_parse_json_field(row.get("env"), None),
        )


# ── per-server connection state ────────────────────────────────────────


class _ServerConnection:
    """Holds the async resources for a single MCP server connection."""

    __slots__ = ("config", "session", "exit_stack", "tool_schemas")

    def __init__(self, config: McpServerConfig) -> None:
        self.config = config
        self.session: ClientSession | None = None
        self.exit_stack: AsyncExitStack | None = None
        self.tool_schemas: list[dict[str, Any]] = []


# ── manager ────────────────────────────────────────────────────────────


class McpClientManager:
    """Manages connections to multiple MCP servers.

    Usage (sync wrappers):
        mgr = McpClientManager(store)
        mgr.sync_connect_server(config)      # discover & cache tools
        schemas = mgr.get_tool_schemas()      # list internal-format schemas
        result = mgr.sync_call_tool(name, args)  # call a namespaced tool
    """

    def __init__(self, store: Any) -> None:
        self._store = store
        self._connections: dict[str, _ServerConnection] = {}
        self._tool_map: dict[str, tuple[str, str]] = {}  # namespaced → (server_id, raw_name)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None

    # ── async lifecycle ─────────────────────────────────────────────

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        """Return the dedicated event loop, starting the background thread if needed."""
        if self._loop is not None and self._loop.is_running():
            return self._loop

        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._loop.run_forever,
            name="mcp-client-loop",
            daemon=True,
        )
        self._thread.start()
        return self._loop

    def _run_async(self, coro: Any) -> Any:
        """Run *coro* on the dedicated event loop and block until done."""
        loop = self._ensure_loop()
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        return future.result(timeout=30)

    async def _open_transport(self, config: McpServerConfig) -> tuple[Any, Any, AsyncExitStack]:
        """Open a transport for the given config, returning (read, write, exit_stack)."""
        stack = AsyncExitStack()
        if config.transport == "stdio":
            params = StdioServerParameters(
                command=config.command or "",
                args=config.args,
                env=config.env,
            )
            read, write = await stack.enter_async_context(stdio_client(params))
        elif config.transport == "sse":
            read, write = await stack.enter_async_context(
                sse_client(url=config.url or "", headers=config.headers)
            )
        elif config.transport == "streamable_http":
            read, write, _get_session_id = await stack.enter_async_context(
                streamablehttp_client(url=config.url or "", headers=config.headers)
            )
        else:
            raise ValueError(f"Unsupported MCP transport: {config.transport}")
        return read, write, stack

    async def connect_server(self, config: McpServerConfig) -> list[dict[str, Any]]:
        """Connect to an MCP server, initialize, discover tools.

        Returns the list of internal-format tool schemas.
        """
        if config.id in self._connections:
            await self.disconnect_server(config.id)

        read, write, stack = await self._open_transport(config)
        session = await stack.enter_async_context(ClientSession(read, write))
        await session.initialize()

        list_result = await session.list_tools()

        conn = _ServerConnection(config)
        conn.session = session
        conn.exit_stack = stack

        for tool in list_result.tools:
            schema = mcp_tool_to_internal_schema(config.id, tool)
            conn.tool_schemas.append(schema)
            self._tool_map[schema["name"]] = (config.id, schema["_mcp_tool_name"])

        self._connections[config.id] = conn
        logger.info("MCP server %s connected: %d tools discovered", config.id, len(conn.tool_schemas))
        return conn.tool_schemas

    async def disconnect_server(self, server_id: str) -> None:
        """Disconnect a server and clean up its tools."""
        conn = self._connections.pop(server_id, None)
        if conn is None:
            return
        # Remove tool_map entries
        names_to_remove = [n for n, (sid, _) in self._tool_map.items() if sid == server_id]
        for n in names_to_remove:
            del self._tool_map[n]
        # Close async resources
        if conn.exit_stack is not None:
            try:
                await conn.exit_stack.aclose()
            except Exception:  # noqa: BLE001
                pass
        logger.info("MCP server %s disconnected", server_id)

    async def call_tool(
        self,
        namespaced_name: str,
        arguments: dict[str, Any],
        timeout_seconds: float = 120,
    ) -> dict[str, Any]:
        """Route a namespaced tool call to the correct MCP server session."""
        entry = self._tool_map.get(namespaced_name)
        if entry is None:
            return {"status": "failed", "error": f"Unknown MCP tool: {namespaced_name}"}
        server_id, raw_name = entry
        conn = self._connections.get(server_id)
        if conn is None or conn.session is None:
            return {"status": "failed", "error": f"MCP server {server_id} not connected"}

        try:
            result: CallToolResult = await asyncio.wait_for(
                conn.session.call_tool(raw_name, arguments),
                timeout=timeout_seconds,
            )
            return parse_mcp_result(result)
        except asyncio.TimeoutError:
            return {
                "status": "failed",
                "error": f"MCP tool {namespaced_name} timed out after {timeout_seconds}s",
                "timeout": True,
            }
        except Exception as exc:  # noqa: BLE001
            return {"status": "failed", "error": str(exc)}

    async def refresh_tools(self, server_id: str | None = None) -> list[dict[str, Any]]:
        """Re-discover tools for a specific server or all servers."""
        if server_id is not None:
            targets = {server_id: self._connections.get(server_id)}
        else:
            targets = dict(self._connections)

        all_schemas: list[dict[str, Any]] = []
        for sid, conn in targets.items():
            if conn is None or conn.session is None:
                continue
            # Remove old tool_map entries
            old_names = [n for n, (s, _) in self._tool_map.items() if s == sid]
            for n in old_names:
                del self._tool_map[n]
            # Re-discover
            list_result = await conn.session.list_tools()
            conn.tool_schemas = []
            for tool in list_result.tools:
                schema = mcp_tool_to_internal_schema(sid, tool)
                conn.tool_schemas.append(schema)
                self._tool_map[schema["name"]] = (sid, schema["_mcp_tool_name"])
            all_schemas.extend(conn.tool_schemas)
        return all_schemas

    # ── sync wrappers (for the sync runtime) ────────────────────────

    def sync_connect_server(self, config: McpServerConfig) -> list[dict[str, Any]]:
        return self._run_async(self.connect_server(config))

    def sync_disconnect_server(self, server_id: str) -> None:
        self._run_async(self.disconnect_server(server_id))

    def sync_call_tool(self, namespaced_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return self._run_async(self.call_tool(namespaced_name, arguments))

    def sync_refresh_tools(self, server_id: str | None = None) -> list[dict[str, Any]]:
        return self._run_async(self.refresh_tools(server_id))

    # ── query ───────────────────────────────────────────────────────

    def get_tool_schemas(self) -> list[dict[str, Any]]:
        """Return all MCP tool schemas in internal format."""
        schemas: list[dict[str, Any]] = []
        for conn in self._connections.values():
            schemas.extend(conn.tool_schemas)
        return schemas

    def get_namespaced_names(self) -> list[str]:
        """Return all namespaced tool names."""
        return list(self._tool_map.keys())

    def is_connected(self, server_id: str) -> bool:
        return server_id in self._connections

    # ── shutdown ────────────────────────────────────────────────────

    def shutdown(self) -> None:
        """Disconnect all servers and stop the event loop cleanly."""
        if self._loop is None:
            return
        # Disconnect all servers synchronously
        for sid in list(self._connections):
            try:
                self._run_async(self.disconnect_server(sid))
            except Exception:  # noqa: BLE001
                pass
        # Cancel all remaining tasks and await their cleanup to avoid
        # "RuntimeWarning: coroutine was never awaited" warnings.
        try:
            future = asyncio.run_coroutine_threadsafe(
                self._cancel_and_await_tasks(), self._loop,
            )
            future.result(timeout=5)
        except Exception:  # noqa: BLE001
            pass
        # Stop loop
        self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
        self._loop = None

    async def _cancel_and_await_tasks(self) -> None:
        """Cancel all outstanding tasks and wait for them to finish."""
        if self._loop is None:
            return
        tasks = [t for t in asyncio.all_tasks(self._loop) if t is not asyncio.current_task()]
        if not tasks:
            return
        for task in tasks:
            task.cancel()
        # Wait for cancelled tasks to suppress RuntimeWarning
        await asyncio.gather(*tasks, return_exceptions=True)
