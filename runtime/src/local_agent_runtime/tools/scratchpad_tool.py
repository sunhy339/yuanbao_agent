"""scratchpad.write and scratchpad.read tools for LLM-driven reasoning state."""

from __future__ import annotations

from typing import Any


def build_scratchpad_write_tool(scratchpad: Any) -> dict[str, Any]:
    """Build the scratchpad.write tool handler."""

    def scratchpad_write(params: dict[str, Any]) -> dict[str, Any]:
        key = params.get("key", "").strip()
        if not key:
            raise ValueError("key is required")
        value = params.get("value", "")
        session_id = params.get("sessionId")
        if not session_id:
            raise ValueError("sessionId is required (injected by orchestrator)")

        entry = scratchpad.write(session_id=session_id, key=key, value=value)
        return {
            "status": "ok",
            "id": entry.id,
            "key": entry.key,
        }

    return {"handler": scratchpad_write}


def build_scratchpad_read_tool(scratchpad: Any) -> dict[str, Any]:
    """Build the scratchpad.read tool handler."""

    def scratchpad_read(params: dict[str, Any]) -> dict[str, Any]:
        key = params.get("key", "").strip()
        if not key:
            raise ValueError("key is required")
        session_id = params.get("sessionId")
        if not session_id:
            raise ValueError("sessionId is required (injected by orchestrator)")

        entry = scratchpad.read(session_id=session_id, key=key)
        if entry is None:
            return {"status": "not_found", "key": key}
        return {
            "status": "ok",
            "id": entry.id,
            "key": entry.key,
            "value": entry.value,
        }

    return {"handler": scratchpad_read}


# Tool schemas for registry
SCRATCHPAD_TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "scratchpad.write",
        "description": (
            "Persist a key-value pair into the session scratchpad for cross-turn reasoning. "
            "Use this to store intermediate hypotheses, partial plans, or deduced facts "
            "that you want to retrieve later in the same session."
        ),
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "key": {
                    "type": "string",
                    "minLength": 1,
                    "description": "A short identifier for the stored value.",
                    "examples": ["hypothesis", "partial_plan", "found_symbols"],
                },
                "value": {
                    "type": "string",
                    "description": "The value to persist.",
                },
                "sessionId": {
                    "type": "string",
                    "description": "Runtime session id injected by the orchestrator.",
                },
            },
            "required": ["key", "value"],
        },
        "safety": [
            "Stores text data in the local SQLite database, scoped to the current session.",
        ],
        "hints": [
            "Use for intermediate reasoning state that you need across ReAct iterations.",
            "Key should be a short, descriptive identifier.",
        ],
    },
    {
        "name": "scratchpad.read",
        "description": (
            "Read a previously stored value from the session scratchpad. "
            "Returns the current value for the given key, or not_found if the key does not exist."
        ),
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "key": {
                    "type": "string",
                    "minLength": 1,
                    "description": "The key to look up.",
                    "examples": ["hypothesis", "partial_plan", "found_symbols"],
                },
                "sessionId": {
                    "type": "string",
                    "description": "Runtime session id injected by the orchestrator.",
                },
            },
            "required": ["key"],
        },
        "safety": [
            "Read-only: retrieves existing scratchpad entries.",
        ],
        "hints": [
            "Use to recover intermediate state from earlier in the session.",
        ],
    },
]
