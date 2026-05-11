"""P0: DecisionAdvisor — unified interface for LLM-assisted material decisions.

Every material runtime decision passes through this advisor:
  1. Build an LLM prompt from the decision kind and input context.
  2. Parse the LLM response into a structured proposal payload.
  3. Validate the proposal through existing validators.
  4. Return the result (accepted/rejected/fallback) with rationale.

Safety guarantee: the LLM proposes, runtime validators decide.
The advisor never bypasses validators or directly executes side effects.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol

from ..models import ProposalKind
from .proposal_validator import validate_proposal

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Decision result
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class AdviceResult:
    """Result of a single DecisionAdvisor.advise() call."""

    proposal_id: str
    kind: str
    payload: dict[str, Any]
    confidence: float
    rationale: str
    source: str  # "llm", "rule_fallback", "validation_rejected"
    validation_reasons: list[str] = field(default_factory=list)
    accepted: bool = True
    model_id: str | None = None
    fallback_reason: str | None = None


# ---------------------------------------------------------------------------
# Decision kind registry entry
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DecisionKindEntry:
    """Registry entry describing one material decision kind."""

    kind: str
    description: str
    required_input_fields: tuple[str, ...]
    allowed_proposal_schema: tuple[str, ...]
    fallback: str  # what to do when LLM fails or proposal is rejected
    trace_event: str  # event name for decision tracing


# ---------------------------------------------------------------------------
# Material Decision Registry
# ---------------------------------------------------------------------------

_DECISION_REGISTRY: dict[str, DecisionKindEntry] = {}


def register_decision(entry: DecisionKindEntry) -> None:
    """Register a material decision kind."""
    _DECISION_REGISTRY[entry.kind] = entry


def get_decision_kind(kind: str) -> DecisionKindEntry | None:
    """Look up a registered decision kind."""
    return _DECISION_REGISTRY.get(kind)


def list_decision_kinds() -> list[str]:
    """List all registered decision kind names."""
    return sorted(_DECISION_REGISTRY.keys())


# Register the first four P0 decisions
register_decision(DecisionKindEntry(
    kind="intent_mode",
    description="Classify conversation mode: direct answer, task execution, queued task, supplement, or clarification",
    required_input_fields=("goal",),
    allowed_proposal_schema=("mode",),
    fallback="rule-based intent classification",
    trace_event="agent.decision.intent_mode",
))
register_decision(DecisionKindEntry(
    kind="routing_strategy",
    description="Select execution strategy: fast ReAct, standard ReAct, reflection, skill mode, planning, supervisor, or swarm",
    required_input_fields=("goal",),
    allowed_proposal_schema=("strategy", "scenario", "skill_id"),
    fallback="default to standard ReAct",
    trace_event="agent.decision.routing_strategy",
))
register_decision(DecisionKindEntry(
    kind="context_policy",
    description="Decide which context sections to include, when to compact, and what to summarize",
    required_input_fields=("goal", "token_budget"),
    allowed_proposal_schema=("sections", "compaction_threshold"),
    fallback="default context template with hard budget compaction",
    trace_event="agent.decision.context_policy",
))
register_decision(DecisionKindEntry(
    kind="decomposition",
    description="Decide whether to split into subtasks, dependency graph, and parallelism",
    required_input_fields=("goal",),
    allowed_proposal_schema=("subtasks", "parallel"),
    fallback="single-agent execution",
    trace_event="agent.decision.decomposition",
))
register_decision(DecisionKindEntry(
    kind="react_turn_decision",
    description="Decide whether to continue with tools, provide final answer, ask user, seek approval, or stop",
    required_input_fields=("goal", "step", "tool_results_summary"),
    allowed_proposal_schema=("decision", "thought_summary", "why_complete", "remaining_risks"),
    fallback="continue if tool_calls present, final_answer otherwise",
    trace_event="agent.decision.react_turn",
))
register_decision(DecisionKindEntry(
    kind="completion_decision",
    description="Validate whether the task is truly complete before marking it done",
    required_input_fields=("goal", "summary", "changed_files"),
    allowed_proposal_schema=("is_complete", "why_complete", "remaining_risks"),
    fallback="accept provider's final answer",
    trace_event="agent.decision.completion",
))


# ---------------------------------------------------------------------------
# Provider protocol
# ---------------------------------------------------------------------------

class AdvisorProvider(Protocol):
    """Minimal interface the advisor needs to call an LLM."""

    def generate(self, prompt: str, context: dict[str, Any]) -> dict[str, Any]: ...


# ---------------------------------------------------------------------------
# DecisionAdvisor
# ---------------------------------------------------------------------------

_ADVISOR_PROMPT_TEMPLATE = """\
You are a runtime decision advisor. Given the decision kind and input context,
propose the best decision as a JSON object.

**Decision kind**: {kind}
**Description**: {description}
**Allowed proposal fields**: {allowed_fields}
**Input context**:
{input_context}

Respond ONLY with valid JSON:
{{
  "proposal": {{ ... }},
  "confidence": <0.0-1.0>,
  "rationale": "<brief reason>"
}}

The proposal object must only contain fields from the allowed list.
Respond ONLY with valid JSON, no other text.
"""


class DecisionAdvisor:
    """Unified interface for LLM-assisted material runtime decisions.

    Usage:
        advisor = DecisionAdvisor(provider=my_provider)
        result = advisor.advise("intent_mode", {"goal": "fix the login bug"})
        if result.accepted:
            use(result.payload)
        else:
            fallback()
    """

    def __init__(self, provider: AdvisorProvider | None = None) -> None:
        self._provider = provider

    def advise(
        self,
        kind: str,
        input_context: dict[str, Any],
        *,
        model_id: str | None = None,
    ) -> AdviceResult:
        """Ask for a decision proposal.

        1. Look up the decision kind in the registry.
        2. If provider is available, build prompt and call LLM.
        3. Parse response into proposal payload.
        4. Validate through existing validators.
        5. Return AdviceResult with accepted/rejected status.
        """
        entry = get_decision_kind(kind)
        if entry is None:
            return AdviceResult(
                proposal_id=uuid.uuid4().hex[:12],
                kind=kind,
                payload={},
                confidence=0.0,
                rationale=f"Unknown decision kind: {kind}",
                source="rule_fallback",
                accepted=False,
                fallback_reason=f"Unknown decision kind: {kind}",
            )

        # Validate required input fields
        missing_input = [
            f for f in entry.required_input_fields
            if f not in input_context and input_context.get(f) is None
        ]
        if missing_input:
            return AdviceResult(
                proposal_id=uuid.uuid4().hex[:12],
                kind=kind,
                payload={},
                confidence=0.0,
                rationale=f"Missing required input fields: {', '.join(missing_input)}",
                source="rule_fallback",
                accepted=False,
                fallback_reason=f"Missing input: {', '.join(missing_input)}",
            )

        proposal_id = uuid.uuid4().hex[:12]

        # Try LLM advisory
        if self._provider is not None:
            result = self._call_llm(entry, input_context, proposal_id, model_id)
            if result is not None:
                # Validate the proposal
                validation_reasons = validate_proposal(kind, result["payload"])
                if not validation_reasons:
                    return AdviceResult(
                        proposal_id=proposal_id,
                        kind=kind,
                        payload=result["payload"],
                        confidence=result["confidence"],
                        rationale=result["rationale"],
                        source="llm",
                        validation_reasons=[],
                        accepted=True,
                        model_id=model_id,
                    )
                # Validation failed
                logger.info(
                    "DecisionAdvisor: LLM proposal for %s rejected by validator: %s",
                    kind, validation_reasons,
                )
                return AdviceResult(
                    proposal_id=proposal_id,
                    kind=kind,
                    payload=result["payload"],
                    confidence=result["confidence"],
                    rationale=result["rationale"],
                    source="validation_rejected",
                    validation_reasons=validation_reasons,
                    accepted=False,
                    model_id=model_id,
                    fallback_reason="LLM proposal failed validation",
                )

        # Fallback: no provider or LLM call failed
        return AdviceResult(
            proposal_id=proposal_id,
            kind=kind,
            payload={},
            confidence=0.0,
            rationale=entry.fallback,
            source="rule_fallback",
            accepted=False,
            fallback_reason="No LLM provider available or LLM call failed",
        )

    def _call_llm(
        self,
        entry: DecisionKindEntry,
        input_context: dict[str, Any],
        proposal_id: str,
        model_id: str | None,
    ) -> dict[str, Any] | None:
        """Build prompt, call LLM, parse response."""
        prompt = _ADVISOR_PROMPT_TEMPLATE.format(
            kind=entry.kind,
            description=entry.description,
            allowed_fields=", ".join(entry.allowed_proposal_schema),
            input_context=json.dumps(input_context, indent=2, default=str),
        )
        try:
            response = self._provider.generate(prompt, {"messages": [{"role": "user", "content": prompt}]})  # type: ignore[union-attr]
            message = response.get("message") or response.get("final_answer") or ""
            return self._parse_llm_response(message)
        except Exception as exc:  # noqa: BLE001
            logger.warning("DecisionAdvisor LLM call failed for %s: %s", entry.kind, exc)
            return None

    @staticmethod
    def _parse_llm_response(text: str) -> dict[str, Any] | None:
        """Parse LLM response into proposal payload + confidence + rationale."""
        # Try JSON extraction from markdown code fence
        fence_match = re.search(r"```(?:json)?\s*\n?(.*?)```", text, re.DOTALL)
        json_text = fence_match.group(1).strip() if fence_match else text.strip()

        try:
            data = json.loads(json_text)
        except (json.JSONDecodeError, ValueError):
            # Try finding a JSON object in the text
            obj_match = re.search(r"\{.*\}", text, re.DOTALL)
            if obj_match:
                try:
                    data = json.loads(obj_match.group())
                except (json.JSONDecodeError, ValueError):
                    return None
            else:
                return None

        if not isinstance(data, dict):
            return None

        proposal = data.get("proposal", {})
        if not isinstance(proposal, dict):
            return None

        confidence = float(data.get("confidence", 0.5))
        confidence = max(0.0, min(1.0, confidence))
        rationale = str(data.get("rationale", ""))

        return {
            "payload": proposal,
            "confidence": confidence,
            "rationale": rationale,
        }
