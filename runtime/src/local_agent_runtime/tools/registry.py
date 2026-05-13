from __future__ import annotations

from collections.abc import Callable
from enum import Enum
from typing import Any

from ._schemas import (
    BUILTIN_TOOL_SCHEMAS,
    BUILTIN_TOOL_SCHEMAS_BY_NAME,
    to_openai_function_tools,
)

ToolHandler = Callable[[dict[str, Any]], dict[str, Any]]


class ToolRateLimitError(Exception):
    """Raised when a tool's per-session rate limit is exceeded."""


class ToolCategory(str, Enum):
    """Semantic category for a registered tool."""

    FILE_READ = "file_read"
    FILE_WRITE = "file_write"
    SEARCH = "search"
    EXECUTION = "execution"
    NETWORK = "network"
    GIT = "git"
    TASK = "task"
    NOTEBOOK = "notebook"
    MEMORY = "memory"


class SafetyLevel(str, Enum):
    """Risk classification for tool execution."""

    SAFE = "safe"
    MEDIUM = "medium"
    DANGEROUS = "dangerous"


class ToolRegistry:
    """Registers structured tools for the runtime.

    Enhanced with:
    - Parameter validation against JSON Schema ``required`` fields
    - Unified error wrapping on tool exceptions
    - ``has_tool()`` / ``list_tools()`` helpers
    """

    def __init__(
        self,
        tools: dict[str, ToolHandler] | None = None,
        schemas: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self._tools = tools or {}
        self._schemas = schemas or {}
        self._call_counts: dict[str, dict[str, int]] = {}
        self._version: int = 0

    @property
    def version(self) -> int:
        return self._version

    # ── registration ────────────────────────────────────────────────

    def register(self, name: str, handler: ToolHandler, schema: dict[str, Any] | None = None) -> None:
        self._tools[name] = handler
        if schema is not None:
            self._schemas[name] = schema
        self._version += 1

    def unregister(self, name: str) -> None:
        self._tools.pop(name, None)
        self._schemas.pop(name, None)
        self._version += 1

    def unregister_prefix(self, prefix: str) -> int:
        """Remove all tools whose name starts with *prefix*.  Returns count removed."""
        names = [n for n in self._tools if n.startswith(prefix)]
        for n in names:
            self._tools.pop(n, None)
            self._schemas.pop(n, None)
        if names:
            self._version += 1
        return len(names)

    # ── query ───────────────────────────────────────────────────────

    def has_tool(self, name: str) -> bool:
        return name in self._tools

    def list_tools(self) -> list[dict[str, Any]]:
        """Return lightweight tool descriptors for discovery."""
        result: list[dict[str, Any]] = []
        for name in self._tools:
            schema = self._schema_for(name)
            result.append(
                {
                    "name": name,
                    "description": schema.get("description", ""),
                    "safety": schema.get("safety", {}),
                    "metadata": schema.get("metadata", {}),
                }
            )
        return result

    # ── execution ───────────────────────────────────────────────────

    def check_rate_limit(self, name: str, session_id: str) -> bool:
        """Return True if the tool call is within rate limits for the session."""
        schema = self._schema_for(name)
        limit = schema.get("metadata", {}).get("rate_limit")
        if limit is None:
            return True
        count = self._call_counts.get(session_id, {}).get(name, 0)
        return count < limit

    def reset_session(self, session_id: str) -> None:
        """Clear per-session call counts."""
        self._call_counts.pop(session_id, None)

    def execute(self, name: str, params: dict[str, Any], *, session_id: str | None = None) -> dict[str, Any]:
        handler = self._tools.get(name)
        if handler is None:
            raise ValueError(f"Unknown tool: {name}")

        # Rate limit enforcement
        if session_id:
            if not self.check_rate_limit(name, session_id):
                limit = self._schema_for(name).get("metadata", {}).get("rate_limit")
                raise ToolRateLimitError(
                    f"工具 '{name}' 在会话 {session_id} 中已超过调用次数限制（{limit}）"
                )
            self._call_counts.setdefault(session_id, {})[name] = \
                self._call_counts.get(session_id, {}).get(name, 0) + 1

        self._validate_required(name, params)

        try:
            result = handler(params)
        except Exception as exc:  # noqa: BLE001
            return {
                "status": "failed",
                "error": str(exc),
                "toolName": name,
            }
        return result

    @property
    def schemas(self) -> list[dict[str, Any]]:
        return [self._schema_for(name) for name in self._tools]

    @property
    def openai_function_tools(self) -> list[dict[str, Any]]:
        return to_openai_function_tools(self.schemas)

    def _schema_for(self, name: str) -> dict[str, Any]:
        schema = self._schemas.get(name) or BUILTIN_TOOL_SCHEMAS_BY_NAME.get(name)
        if schema is not None:
            return schema
        return {
            "name": name,
            "description": f"Execute the registered tool named {name}.",
            "input_schema": {
                "type": "object",
                "additionalProperties": True,
                "properties": {},
                "required": [],
            },
            "safety": {
                "level": "medium",
                "requires_approval": False,
                "category": "task",
                "sandboxed": False,
                "notes": ["No safety metadata is registered for this custom tool."],
            },
            "hints": [],
        }

    def _validate_required(self, name: str, params: dict[str, Any]) -> None:
        schema = self._schema_for(name)
        input_schema = schema.get("input_schema") or {}
        required = list(input_schema.get("required") or [])
        # oneOf: at least one branch must be fully satisfied
        one_of = input_schema.get("oneOf")
        if one_of:
            branch_ok = any(
                all(field in params for field in branch.get("required", []))
                for branch in one_of
            )
            if not branch_ok:
                branch_names = [
                    ", ".join(branch.get("required", [])) for branch in one_of
                ]
                raise ValueError(
                    f"Missing required parameters for {name}: need one of ({'; '.join(branch_names)})"
                )
        missing = [field for field in required if field not in params]
        if missing:
            raise ValueError(f"Missing required parameters for {name}: {', '.join(missing)}")
