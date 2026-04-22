from __future__ import annotations

from collections.abc import Callable
from typing import Any

ToolHandler = Callable[[dict[str, Any]], dict[str, Any]]


class ToolRegistry:
    """Registers structured tools for the runtime."""

    def __init__(self, tools: dict[str, ToolHandler] | None = None) -> None:
        self._tools = tools or {}

    def register(self, name: str, handler: ToolHandler) -> None:
        self._tools[name] = handler

    def execute(self, name: str, params: dict[str, Any]) -> dict[str, Any]:
        handler = self._tools.get(name)
        if handler is None:
            raise ValueError(f"Unknown tool: {name}")
        return handler(params)

    @property
    def schemas(self) -> list[dict[str, Any]]:
        return [
            {
                "name": name,
                "description": "placeholder tool",
                "input_schema": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            }
            for name in self._tools
        ]
