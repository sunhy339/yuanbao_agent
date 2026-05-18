"""Shared tool failure facts for handoff, recovery advice, and audit."""

from __future__ import annotations

from typing import Any


def tool_failure_summary(payload: dict[str, Any]) -> str:
    summary = (
        payload.get("summary")
        or payload.get("error")
        or payload.get("message")
        or payload.get("content")
        or "Tool failed."
    )
    return str(summary)[:500]


def classify_tool_failure(
    *,
    tool_name: str,
    status: str,
    summary: str,
    payload: dict[str, Any],
) -> str:
    text = " ".join([tool_name, status, summary]).casefold()
    if payload.get("timeout") is True or "timeout" in text or "timed out" in text:
        return "timeout"
    if status.casefold() in {"blocked", "approval_required"} or any(
        token in text for token in ("permission", "denied", "blocked", "not allowed", "allowlist")
    ):
        return "permission_denied"
    if status.casefold() == "partial" or "partial" in text or "incomplete" in text:
        return "partial_response"
    if tool_name.startswith("mcp__") and any(
        token in text for token in ("unavailable", "not connected", "connection", "server", "transport")
    ):
        return "mcp_server_unavailable"
    if tool_name.startswith("mcp__"):
        return "mcp_tool_failed"
    return "tool_failed"


def tool_recovery_hint(*, tool_name: str, failure_kind: str) -> str:
    if failure_kind == "mcp_server_unavailable":
        return f"Check MCP server configuration/connection, refresh tools, then retry {tool_name}."
    if failure_kind == "permission_denied":
        return f"Adjust tool, MCP, or skill policy, or choose an allowed fallback before retrying {tool_name}."
    if failure_kind == "partial_response":
        return f"Use the partial result if sufficient; otherwise retry {tool_name} with narrower arguments."
    if failure_kind == "timeout":
        return f"Retry {tool_name} with a smaller request or longer timeout if policy allows."
    if failure_kind == "mcp_tool_failed":
        return f"Inspect MCP tool error details and retry {tool_name} only after the server/tool state is healthy."
    return f"Inspect the tool error and choose a safe fallback before retrying {tool_name}."


def build_tool_failure_record(
    *,
    tool_name: str,
    payload: dict[str, Any],
    status: str | None = None,
    summary: str | None = None,
) -> dict[str, Any]:
    status_text = str(status if status is not None else payload.get("status") or "").strip()
    summary_text = str(summary)[:500] if summary is not None else tool_failure_summary(payload)
    failure_kind = classify_tool_failure(
        tool_name=tool_name,
        status=status_text,
        summary=summary_text,
        payload=payload,
    )
    return {
        "name": tool_name,
        "status": status_text or "failed",
        "summary": summary_text,
        "failureKind": failure_kind,
        "recoveryHint": tool_recovery_hint(tool_name=tool_name, failure_kind=failure_kind),
    }
