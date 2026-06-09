"""Tool failure annotation for the model-first loop.

Tool failures should flow back into the next provider turn as ordinary tool
results. The runtime records the failure kind and a short hint, but it does not
spawn advisor proposals, synthetic approvals, or hidden follow-up work.
"""

from __future__ import annotations

from typing import Any

from ..tools.failure_analysis import build_tool_failure_record


class ToolRecoveryMixin:
    """Annotate failed tool calls without taking over the agent loop."""

    def _annotate_failed_tool_recovery(
        self,
        *,
        session_id: str,
        task: dict[str, Any],
        tool_call_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        result: dict[str, Any],
    ) -> dict[str, Any]:
        failure = build_tool_failure_record(tool_name=tool_name, payload=result)
        result.setdefault("failureKind", failure["failureKind"])
        result.setdefault("recoveryHint", failure["recoveryHint"])
        self._publish(
            session_id=session_id,
            task=task,
            event_type="tool.failure",
            payload={
                "toolCallId": tool_call_id,
                "toolName": tool_name,
                "failureKind": failure.get("failureKind"),
                "summary": failure.get("summary"),
                "recoveryHint": failure.get("recoveryHint"),
            },
            visibility="trace",
        )
        return failure
