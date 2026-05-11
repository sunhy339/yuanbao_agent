"""Types for the Structured ReAct Turn Contract.

Each ReAct cycle produces a :class:`ProviderTurnResult` with an explicit
:class:`TurnDecision` that drives the loop termination logic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class TurnDecision(str, Enum):
    """Why a ReAct cycle ended.

    Mapping to existing runtime states:

    - ``CONTINUE_WITH_TOOLS`` → loop continues, execute tool calls
    - ``FINAL_ANSWER`` → loop ends normally (``task.completed``)
    - ``ASK_USER`` → pause-like state, waiting for user clarification
    - ``NEEDS_APPROVAL`` → pause-like state, waiting for approval gate
    - ``BLOCKED_BY_POLICY`` → task cannot proceed, soft-failure
    - ``FAILED`` → task cannot proceed, hard-failure or fallback
    """

    CONTINUE_WITH_TOOLS = "continue_with_tools"
    FINAL_ANSWER = "final_answer"
    ASK_USER = "ask_user"
    NEEDS_APPROVAL = "needs_approval"
    BLOCKED_BY_POLICY = "blocked_by_policy"
    FAILED = "failed"


@dataclass(slots=True)
class ProviderTurnResult:
    """Structured result from one provider call inside the ReAct loop.

    This replaces the untyped ``{"status": ..., "summary": ..., "tool_calls": ...}``
    dict returned by ``_parse_provider_response``.  Phase A introduces the type
    alongside the existing dict path; Phase B will make the loop consume it
    directly.
    """

    decision: TurnDecision
    thought_summary: str
    message: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    final_answer: str | None = None
    why_complete: str | None = None
    remaining_risks: list[str] = field(default_factory=list)
    policy_needs: dict[str, Any] | None = None
    raw_response: dict[str, Any] | None = None
