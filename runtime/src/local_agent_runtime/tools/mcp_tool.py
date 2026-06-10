"""MCP tools — expose McpClientManager capabilities as agent-callable tools.

Three tools align with haha-cc's MCPTool, ListMcpResourcesTool, ReadMcpResourceTool:
- ``mcp_tool``: Call a namespaced MCP tool (``mcp__{server}__{name}``).
- ``list_mcp_resources``: List resources offered by connected MCP servers.
- ``read_mcp_resource``: Read a specific MCP resource by URI.
"""

from __future__ import annotations

from typing import Any


def build_mcp_tool(mcp_client_manager: Any | None = None) -> dict[str, Any]:
    """Build the mcp_tool handler for calling MCP server tools."""

    def handler(params: dict[str, Any]) -> dict[str, Any]:
        if mcp_client_manager is None:
            raise ValueError("MCP client is not configured")
        tool_name = str(params.get("tool_name") or params.get("toolName") or params.get("name") or "").strip()
        if not tool_name:
            raise ValueError("tool_name is required for MCP tool call")
        arguments = params.get("arguments") or params.get("args") or params.get("input") or {}
        if not isinstance(arguments, dict):
            arguments = {}

        # Verify the tool exists in the MCP client's tool map
        tool_map = getattr(mcp_client_manager, "_tool_map", {})
        if tool_name not in tool_map and not tool_name.startswith("mcp__"):
            # Try namespacing automatically
            namespaced = f"mcp__{tool_name}"
            if namespaced in tool_map:
                tool_name = namespaced

        try:
            result = mcp_client_manager.sync_call_tool(tool_name, arguments)
        except Exception as exc:
            return {
                "status": "error",
                "error": str(exc),
                "toolName": tool_name,
                "contentSource": "mcp",
                "contentTrust": "untrusted",
            }

        if isinstance(result, dict):
            result["toolName"] = tool_name
            result["contentTrust"] = "untrusted"
            return result
        return {
            "status": "ok",
            "output": str(result),
            "toolName": tool_name,
            "contentSource": "mcp",
            "contentTrust": "untrusted",
        }

    return {"handler": handler}


def build_list_mcp_resources_tool(mcp_client_manager: Any | None = None) -> dict[str, Any]:
    """Build the list_mcp_resources handler for listing MCP server tools.

    Note: The current McpClientManager exposes tool schemas, not the MCP
    resource API. This handler lists the connected servers and the tools
    they expose (since they are the closest analog Yuanbao currently has).
    """

    def handler(params: dict[str, Any]) -> dict[str, Any]:
        if mcp_client_manager is None:
            raise ValueError("MCP client is not configured")
        server_filter = str(params.get("server_id") or params.get("serverId") or "").strip()

        try:
            schemas = mcp_client_manager.get_tool_schemas()
        except Exception as exc:
            return {
                "status": "error",
                "toolName": "list_mcp_resources",
                "error": str(exc),
            }

        items = []
        for schema in schemas:
            sid = schema.get("_mcp_server_id", "")
            if server_filter and sid != server_filter:
                continue
            items.append({
                "name": schema.get("name", ""),
                "description": schema.get("description", ""),
                "serverId": sid,
                "rawToolName": schema.get("_mcp_tool_name", ""),
            })

        return {
            "status": "ok",
            "toolName": "list_mcp_resources",
            "tools": items,
            "count": len(items),
        }

    return {"handler": handler}


def build_read_mcp_resource_tool(mcp_client_manager: Any | None = None) -> dict[str, Any]:
    """Build the read_mcp_resource handler.

    Note: The current McpClientManager does not expose an MCP resource read
    API. This handler reports an unsupported status; once McpClientManager
    grows resource-read support, this can be wired up.
    """

    def handler(params: dict[str, Any]) -> dict[str, Any]:
        if mcp_client_manager is None:
            raise ValueError("MCP client is not configured")
        uri = str(params.get("uri") or "").strip()
        if not uri:
            raise ValueError("uri is required for reading MCP resource")
        return {
            "status": "unsupported",
            "toolName": "read_mcp_resource",
            "uri": uri,
            "error": "MCP resource read is not yet supported by the runtime client manager",
        }

    return {"handler": handler}
