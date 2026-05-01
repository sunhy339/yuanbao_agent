"""memory.remember and memory.recall tools for LLM-driven memory management."""

from __future__ import annotations

from typing import Any


def build_memory_remember_tool(memory_manager: Any) -> dict[str, Any]:
    """Build the memory.remember tool handler."""

    def memory_remember(params: dict[str, Any]) -> dict[str, Any]:
        content = params.get("content", "").strip()
        if not content:
            raise ValueError("content is required")

        kind_str = params.get("kind", "working").strip().lower()
        from ..memory.types import MemoryKind

        kind_map = {k.value: k for k in MemoryKind}
        kind = kind_map.get(kind_str, MemoryKind.WORKING)

        session_id = params.get("sessionId")
        workspace_id = params.get("workspaceId")

        entry = memory_manager.remember(
            content=content,
            session_id=session_id,
            workspace_id=workspace_id,
            kind=kind,
        )
        return {
            "status": "ok",
            "id": entry.id,
            "kind": entry.kind.value,
            "keywords": entry.keywords[:10],
        }

    return {"handler": memory_remember}


def build_memory_recall_tool(memory_manager: Any) -> dict[str, Any]:
    """Build the memory.recall tool handler."""

    def memory_recall(params: dict[str, Any]) -> dict[str, Any]:
        query = params.get("query", "").strip()
        if not query:
            raise ValueError("query is required")

        limit = min(max(1, int(params.get("limit", 5))), 20)
        session_id = params.get("sessionId")
        workspace_id = params.get("workspaceId")

        entries = memory_manager.recall(
            query=query,
            session_id=session_id,
            workspace_id=workspace_id,
            limit=limit,
        )
        results = [
            {
                "id": e.id,
                "kind": e.kind.value,
                "content": e.content[:500],
                "keywords": e.keywords[:5],
            }
            for e in entries
        ]
        return {
            "status": "ok",
            "count": len(results),
            "memories": results,
        }

    return {"handler": memory_recall}


# Tool schemas for registry
MEMORY_TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "memory.remember",
        "description": (
            "Store an important fact, preference, or insight into persistent memory. "
            "Use this to remember things the user has told you, decisions made, or key findings "
            "that should persist across conversation turns."
        ),
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "content": {
                    "type": "string",
                    "minLength": 1,
                    "description": "The information to remember.",
                    "examples": [
                        "User prefers dark mode for coding",
                        "Project uses pytest for testing",
                    ],
                },
                "kind": {
                    "type": "string",
                    "description": "Memory type: working (current session), session (cross-session), long_term (persistent), semantic (keyword-indexed).",
                    "enum": ["working", "session", "long_term", "semantic"],
                    "default": "working",
                },
                "sessionId": {
                    "type": "string",
                    "description": "Runtime session id injected by the orchestrator; models usually omit this.",
                },
                "workspaceId": {
                    "type": "string",
                    "description": "Runtime workspace id injected by the orchestrator; models usually omit this.",
                },
            },
            "required": ["content"],
        },
        "safety": {
            "level": "safe",
            "requires_approval": False,
            "category": "memory",
            "sandboxed": True,
            "notes": [
                "Only stores text data in the local SQLite database.",
                "Memory content is scoped to the current workspace/session.",
            ],
        },
        "hints": [
            "Use for user preferences, project conventions, or important findings.",
            "Set kind='long_term' for facts that should persist indefinitely.",
        ],
        "metadata": {
            "rate_limit": None,
            "cost_per_use": 1,
            "estimated_duration_ms": 200,
        },
    },
    {
        "name": "memory.recall",
        "description": (
            "Search and retrieve relevant memories. Use this to recall previously stored facts, "
            "preferences, or insights that may be relevant to the current task."
        ),
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "query": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Search query to find relevant memories.",
                    "examples": [
                        "user preferences",
                        "testing framework",
                        "deployment process",
                    ],
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of memories to return.",
                    "minimum": 1,
                    "maximum": 20,
                    "default": 5,
                },
                "sessionId": {
                    "type": "string",
                    "description": "Runtime session id injected by the orchestrator; models usually omit this.",
                },
                "workspaceId": {
                    "type": "string",
                    "description": "Runtime workspace id injected by the orchestrator; models usually omit this.",
                },
            },
            "required": ["query"],
        },
        "safety": {
            "level": "safe",
            "requires_approval": False,
            "category": "memory",
            "sandboxed": True,
            "notes": [
                "Read-only: only retrieves existing memories.",
                "Search is scoped to the current workspace.",
            ],
        },
        "hints": [
            "Use at the start of a task to recall relevant context.",
            "Query with specific terms for better results.",
        ],
        "metadata": {
            "rate_limit": None,
            "cost_per_use": 1,
            "estimated_duration_ms": 200,
        },
    },
]
