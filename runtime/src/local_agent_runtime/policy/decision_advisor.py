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
import time
import uuid
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from ..models import ProposalKind
from .proposal_validator import validate_proposal

logger = logging.getLogger(__name__)

_DEFAULT_ROUTING_ADVISOR_TIMEOUT_SECONDS = 3.0
_DEFAULT_ROUTING_ADVISOR_COOLDOWN_SECONDS = 600.0


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
    description=(
        "Select execution strategy and whether the parent should keep using tools "
        "after child task results. Use tool_continuation for semantic continuation policy; "
        "runtime still enforces budgets, permissions, and safety gates."
    ),
    required_input_fields=("goal",),
    allowed_proposal_schema=("strategy", "scenario", "skill_id", "tool_continuation", "toolContinuation"),
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
    allowed_proposal_schema=(
        "is_complete",
        "why_complete",
        "remaining_risks",
        "surface_type",
        "blocking_issues",
        "recommended_verification",
    ),
    fallback="accept provider's final answer",
    trace_event="agent.decision.completion",
))
register_decision(DecisionKindEntry(
    kind="product_surface_decision",
    description=(
        "Classify the completed artifact surface and recommend semantic evidence "
        "without turning product-shape guesses into hard gates"
    ),
    required_input_fields=("goal", "summary", "changed_files", "objective_signals"),
    allowed_proposal_schema=(
        "surface_type",
        "recommended_verification",
        "verification_intents",
        "evidence_requests",
    ),
    fallback="use objective completion evidence only",
    trace_event="agent.decision.product_surface",
))
register_decision(DecisionKindEntry(
    kind="failure_recovery",
    description=(
        "Suggest how to recover from a provider failure using the supplied failure "
        "facts and runtime limits. The runtime will still clamp auth/refusal, retry "
        "budgets, provider availability, and other hard boundaries."
    ),
    required_input_fields=("goal", "provider_failure"),
    allowed_proposal_schema=(
        "strategy",
        "maxRetries",
        "retryDelayMs",
        "reason",
        "userMessage",
        "contextStrategy",
        "fallbackProviderId",
    ),
    fallback="use conservative provider failure classifier",
    trace_event="agent.decision.failure_recovery",
))
register_decision(DecisionKindEntry(
    kind="provider_preflight",
    description=(
        "Review provider-call facts before the request is sent and propose a bounded "
        "action such as proceeding, compacting context, asking the user, or recording "
        "a split/provider-switch recommendation. The runtime may auto-apply safe "
        "context compaction or execute a validated bounded split through the existing "
        "planning/DAG path; provider switching and user-intervention advice remain audited."
    ),
    required_input_fields=("goal", "preflight_facts"),
    allowed_proposal_schema=(
        "action",
        "reason",
        "riskLevel",
        "contextStrategy",
        "splitRecommendation",
        "subtasks",
        "fallbackProviderId",
        "userMessage",
    ),
    fallback="proceed under runtime token and provider limits",
    trace_event="agent.decision.provider_preflight",
))
register_decision(DecisionKindEntry(
    kind="user_takeover",
    description=(
        "Classify a supplemental user message against the active task as a supplement, "
        "pause, continue, stop, wrap-up, or target-change request. The runtime will "
        "still enforce task state transitions and preserve resumable workflow state."
    ),
    required_input_fields=("message", "task_status"),
    allowed_proposal_schema=("state", "intent", "reason", "target_goal", "handoff_focus"),
    fallback="use conservative user takeover classifier",
    trace_event="agent.decision.user_takeover",
))
register_decision(DecisionKindEntry(
    kind="tool_recovery",
    description=(
        "Choose a bounded recovery action for a failed tool or MCP call from "
        "structured failure facts. The runtime will not execute the action "
        "directly; permissions, approvals, tool policy, and state gates still apply."
    ),
    required_input_fields=("goal", "tool_failure"),
    allowed_proposal_schema=(
        "action",
        "reason",
        "userMessage",
        "retryWithNarrowerArgs",
        "usePartialEvidence",
        "fallbackTool",
        "requestPermission",
        "refreshMcpTools",
        "risk",
    ),
    fallback="use structured recovery hint and continue under normal tool policy",
    trace_event="agent.decision.tool_recovery",
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
Do not include markdown fences, analysis, comments, or extra text.
If you are uncertain, still return the JSON shape above with lower confidence.
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

    def __init__(
        self,
        provider: AdvisorProvider | None = None,
        *,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._provider = provider
        self._clock = clock or time.monotonic
        self._cooldown_until_by_kind: dict[str, float] = {}
        self._cooldown_reason_by_kind: dict[str, str] = {}

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
        kind_policy = self._kind_policy(kind, input_context)
        cooldown_reason = self._cooldown_reason(kind, kind_policy)
        if cooldown_reason:
            return AdviceResult(
                proposal_id=proposal_id,
                kind=kind,
                payload={},
                confidence=0.0,
                rationale=entry.fallback,
                source="rule_fallback",
                accepted=False,
                fallback_reason=cooldown_reason,
            )

        # Try LLM advisory
        if self._provider is not None:
            result, failure_reason = self._call_llm(entry, input_context, proposal_id, model_id, kind_policy)
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
            if failure_reason:
                self._record_kind_failure(kind, kind_policy, failure_reason)

        # Fallback: no provider or LLM call failed
        return AdviceResult(
            proposal_id=proposal_id,
            kind=kind,
            payload={},
            confidence=0.0,
            rationale=entry.fallback,
            source="rule_fallback",
            accepted=False,
            fallback_reason=failure_reason if self._provider is not None and failure_reason else "No LLM provider available or LLM call failed",
        )

    def _call_llm(
        self,
        entry: DecisionKindEntry,
        input_context: dict[str, Any],
        proposal_id: str,
        model_id: str | None,
        kind_policy: dict[str, float | None],
    ) -> tuple[dict[str, Any] | None, str | None]:
        """Build prompt, call LLM, parse response."""
        prompt = _ADVISOR_PROMPT_TEMPLATE.format(
            kind=entry.kind,
            description=entry.description,
            allowed_fields=", ".join(entry.allowed_proposal_schema),
            input_context=json.dumps(
                self._prompt_input_context(input_context),
                indent=2,
                default=str,
            ),
        )
        try:
            provider_context: dict[str, Any] = {
                "messages": [{"role": "user", "content": prompt}],
            }
            config = input_context.get("config")
            if isinstance(config, dict):
                provider_context["config"] = self._provider_context_config(
                    config=config,
                    kind_policy=kind_policy,
                )
            response = self._provider.generate(prompt, provider_context)  # type: ignore[union-attr]
            message = response.get("message") or response.get("final_answer") or ""
            assistant_message = response.get("assistant_message")
            if not message and isinstance(assistant_message, dict):
                assistant_content = assistant_message.get("content")
                if isinstance(assistant_content, str):
                    message = assistant_content
            return self._parse_llm_response(message), None
        except Exception as exc:  # noqa: BLE001
            logger.warning("DecisionAdvisor LLM call failed for %s: %s", entry.kind, exc)
            return None, str(exc)

    def _kind_policy(self, kind: str, input_context: dict[str, Any]) -> dict[str, float | None]:
        advisor_config = self._advisor_config(input_context)
        per_kind = advisor_config.get("decisionKinds")
        kind_config = per_kind.get(kind) if isinstance(per_kind, dict) and isinstance(per_kind.get(kind), dict) else {}
        if not isinstance(kind_config, dict):
            kind_config = {}
        if kind == "routing_strategy":
            timeout = self._bounded_float(
                kind_config.get("timeoutSeconds")
                or advisor_config.get("routingStrategyTimeoutSeconds"),
                _DEFAULT_ROUTING_ADVISOR_TIMEOUT_SECONDS,
                minimum=0.2,
                maximum=180.0,
            )
            cooldown = self._bounded_float(
                kind_config.get("cooldownSeconds")
                or advisor_config.get("routingStrategyCooldownSeconds"),
                _DEFAULT_ROUTING_ADVISOR_COOLDOWN_SECONDS,
                minimum=0.0,
                maximum=3600.0,
            )
            return {"timeoutSeconds": timeout, "cooldownSeconds": cooldown}
        timeout = self._bounded_float(
            kind_config.get("timeoutSeconds"),
            None,
            minimum=0.2,
            maximum=120.0,
        )
        cooldown = self._bounded_float(
            kind_config.get("cooldownSeconds"),
            0.0,
            minimum=0.0,
            maximum=3600.0,
        )
        return {"timeoutSeconds": timeout, "cooldownSeconds": cooldown}

    def _advisor_config(self, input_context: dict[str, Any]) -> dict[str, Any]:
        config = input_context.get("config")
        if not isinstance(config, dict):
            return {}
        advisor = config.get("advisor")
        if isinstance(advisor, dict):
            return advisor
        autonomy = config.get("autonomy")
        if isinstance(autonomy, dict) and isinstance(autonomy.get("advisor"), dict):
            return autonomy["advisor"]
        return {}

    def _provider_context_config(
        self,
        *,
        config: dict[str, Any],
        kind_policy: dict[str, float | None],
    ) -> dict[str, Any]:
        timeout = kind_policy.get("timeoutSeconds")
        if timeout is None:
            return config
        adjusted = deepcopy(config)
        provider = adjusted.get("provider")
        if isinstance(provider, dict):
            self._apply_provider_timeout(provider, timeout)
        return adjusted

    def _apply_provider_timeout(self, provider: dict[str, Any], timeout: float) -> None:
        provider["timeout"] = timeout
        provider["timeoutSeconds"] = timeout
        profiles = provider.get("profiles")
        if isinstance(profiles, list):
            for profile in profiles:
                if isinstance(profile, dict):
                    profile["timeout"] = timeout
                    profile["timeoutSeconds"] = timeout

    def _cooldown_reason(self, kind: str, kind_policy: dict[str, float | None]) -> str | None:
        cooldown = kind_policy.get("cooldownSeconds")
        if not isinstance(cooldown, (int, float)) or cooldown <= 0:
            return None
        retry_after = self._cooldown_until_by_kind.get(kind, 0.0)
        now = self._clock()
        if now >= retry_after:
            return None
        remaining = max(0, int(retry_after - now))
        reason = self._cooldown_reason_by_kind.get(kind) or "previous LLM advisory failure"
        return f"Advisor {kind} is in cooldown for {remaining}s after {reason}; using rule fallback."

    def _record_kind_failure(self, kind: str, kind_policy: dict[str, float | None], reason: str) -> None:
        cooldown = kind_policy.get("cooldownSeconds")
        if not isinstance(cooldown, (int, float)) or cooldown <= 0:
            return
        self._cooldown_until_by_kind[kind] = self._clock() + float(cooldown)
        self._cooldown_reason_by_kind[kind] = reason

    @staticmethod
    def _bounded_float(value: Any, default: float | None, *, minimum: float, maximum: float) -> float | None:
        if value is None or value == "":
            return default
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return default
        return max(minimum, min(maximum, parsed))

    @staticmethod
    def _parse_llm_response(text: str) -> dict[str, Any] | None:
        """Parse LLM response into proposal payload + confidence + rationale."""
        # Try JSON extraction from markdown code fence
        fence_match = re.search(r"```(?:json)?\s*\n?(.*?)```", text, re.DOTALL)
        json_text = fence_match.group(1).strip() if fence_match else text.strip()

        if json_text.lower().startswith("json\n"):
            json_text = json_text[5:].strip()

        try:
            data = json.loads(json_text)
        except (json.JSONDecodeError, ValueError):
            data = None
            for json_object in DecisionAdvisor._extract_json_objects(text):
                try:
                    data = json.loads(json_object)
                    break
                except (json.JSONDecodeError, ValueError):
                    continue
            if data is None:
                return None

        if not isinstance(data, dict):
            return None

        proposal = data.get("proposal")
        if proposal is None:
            proposal = {
                key: value
                for key, value in data.items()
                if key not in {"confidence", "rationale", "reasoning", "explanation"}
            }
        if not isinstance(proposal, dict):
            return None

        try:
            confidence = float(data.get("confidence", 0.5))
        except (TypeError, ValueError):
            confidence = 0.5
        confidence = max(0.0, min(1.0, confidence))
        rationale = str(data.get("rationale") or data.get("reasoning") or data.get("explanation") or "")

        return {
            "payload": proposal,
            "confidence": confidence,
            "rationale": rationale,
        }

    @staticmethod
    def _extract_json_objects(text: str) -> list[str]:
        objects: list[str] = []
        for start, char in enumerate(text):
            if char != "{":
                continue
            depth = 0
            in_string = False
            escaped = False
            for index in range(start, len(text)):
                current = text[index]
                if in_string:
                    if escaped:
                        escaped = False
                    elif current == "\\":
                        escaped = True
                    elif current == '"':
                        in_string = False
                    continue
                if current == '"':
                    in_string = True
                elif current == "{":
                    depth += 1
                elif current == "}":
                    depth -= 1
                    if depth == 0:
                        objects.append(text[start : index + 1])
                        break
        return objects

    @staticmethod
    def _prompt_input_context(input_context: dict[str, Any]) -> dict[str, Any]:
        prompt_context = dict(input_context)
        config = prompt_context.get("config")
        if isinstance(config, dict):
            prompt_context["config"] = DecisionAdvisor._redact_sensitive_config(config)
        return prompt_context

    @staticmethod
    def _redact_sensitive_config(value: Any) -> Any:
        if isinstance(value, dict):
            redacted: dict[str, Any] = {}
            for key, item in value.items():
                key_text = str(key)
                lowered = key_text.lower()
                if any(marker in lowered for marker in ("apikey", "api_key", "authorization", "password", "secret", "token")):
                    redacted[key] = "[redacted]"
                else:
                    redacted[key] = DecisionAdvisor._redact_sensitive_config(item)
            return redacted
        if isinstance(value, list):
            return [DecisionAdvisor._redact_sensitive_config(item) for item in value]
        return value
