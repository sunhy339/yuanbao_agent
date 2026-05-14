"""Meta-router: classify user goals into scenarios and pick an execution strategy.

Two-phase routing:
  1. Rule-based keyword matching (zero cost, deterministic).
  2. Optional LLM-based classification via DecisionAdvisor when rule confidence is low.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any, TYPE_CHECKING

from .defaults import SCENARIO_STRATEGY_MAP, lookup_keyword
from .types import ExecutionStrategy, RoutingDecision, Scenario

if TYPE_CHECKING:
    from ..policy.decision_advisor import AdviceResult, DecisionAdvisor

logger = logging.getLogger(__name__)

_RULE_CONFIDENCE_THRESHOLD = 0.80
_LLM_CONFIDENCE_THRESHOLD = 0.60

# Chinese word segmentation helper – split on punctuation/whitespace and
# keep CJK character runs as individual tokens.
_CJK_RE = re.compile(r"([\u4e00-\u9fff])")
_SPLIT_RE = re.compile(r"[\s,，。！？；：、（）()\[\]{}\"'`]+")


def _tokenize(text: str) -> list[str]:
    """Split *text* into searchable tokens (handles mixed CJK/Latin)."""
    # Separate each CJK character so individual Chinese characters become tokens.
    text = _CJK_RE.sub(r" \1 ", text)
    return [t for t in _SPLIT_RE.split(text) if t]


class MetaRouter:
    """Analyse a user goal and produce a :class:`RoutingDecision`."""

    def __init__(
        self,
        provider: Any | None = None,
        decision_advisor: DecisionAdvisor | None = None,
    ) -> None:
        self._provider = provider
        self._decision_advisor = decision_advisor
        # Keep last advisor result for caller inspection (proposal recording)
        self._last_advice: AdviceResult | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def route(self, goal: str, context: dict[str, Any] | None = None) -> RoutingDecision:
        """Return a routing decision for *goal*.

        Phase 1: rule-based match (free, instant).
        Phase 2: if confidence < 0.8 and a provider is available, ask the LLM.
        Fallback: FREE_FORM → REACT_STANDARD.
        """
        rule_result = self._rule_based_route(goal, context)
        if rule_result.confidence >= _RULE_CONFIDENCE_THRESHOLD:
            return rule_result

        llm_result = self._llm_route(goal, context)
        if llm_result is not None and llm_result.confidence > rule_result.confidence:
            return llm_result

        return rule_result

    # ------------------------------------------------------------------
    # Phase 1 – rule-based routing
    # ------------------------------------------------------------------

    def _rule_based_route(self, goal: str, context: dict[str, Any] | None) -> RoutingDecision:
        tokens = _tokenize(goal)
        best_scenario: Scenario | None = None
        best_conf = 0.0
        matched_keyword = ""

        for token in tokens:
            hit = lookup_keyword(token)
            if hit and hit[1] > best_conf:
                best_scenario, best_conf = hit
                matched_keyword = token

        if best_scenario is None:
            # Also try the full lowered goal against keyword index for
            # multi-word keywords like "code_review" or "安全检查".
            lowered = goal.lower().strip()
            for kw, (scenario, conf) in _KEYWORD_INDEX.items():
                if len(kw) > 1 and kw in lowered and conf > best_conf:
                    best_scenario = scenario
                    best_conf = conf
                    matched_keyword = kw

        if best_scenario is None:
            best_scenario = Scenario.FREE_FORM
            best_conf = 0.3

        return self._build_decision(
            scenario=best_scenario,
            confidence=best_conf,
            reasoning=f"rule-match: keyword='{matched_keyword}'",
        )

    # ------------------------------------------------------------------
    # Phase 2 – LLM-based routing (optional)
    # ------------------------------------------------------------------

    @property
    def last_advice(self) -> AdviceResult | None:
        """Return the last DecisionAdvisor result from the most recent route() call."""
        return self._last_advice

    def _llm_route(self, goal: str, context: dict[str, Any] | None) -> RoutingDecision | None:
        self._last_advice = None

        # Prefer DecisionAdvisor path — creates durable proposal records
        if self._decision_advisor is not None:
            input_context: dict[str, Any] = {"goal": goal}
            config = (context or {}).get("config") if isinstance(context, dict) else None
            if isinstance(config, dict):
                input_context["config"] = config
            result = self._decision_advisor.advise("routing_strategy", input_context)
            self._last_advice = result
            if result.accepted and result.payload:
                routing = self._advisor_payload_to_decision(result.payload, result.rationale)
                if routing is not None:
                    return routing
            # Advisor rejected or malformed — fall through to direct provider call
            logger.debug("DecisionAdvisor routing rejected: %s", result.fallback_reason)

        # Legacy direct provider path (used when no advisor is configured)
        if self._provider is None:
            return None
        if not hasattr(self._provider, "generate"):
            return None

        prompt = self._build_classification_prompt(goal)
        try:
            result = self._provider.generate(prompt, context or {})
            message = result.get("message") or result.get("final_answer") or ""
            return self._parse_llm_response(goal, message)
        except Exception:  # noqa: BLE001
            return None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _build_decision(
        self,
        scenario: Scenario,
        confidence: float,
        reasoning: str = "",
    ) -> RoutingDecision:
        cfg = SCENARIO_STRATEGY_MAP.get(scenario, SCENARIO_STRATEGY_MAP[Scenario.FREE_FORM])
        return RoutingDecision(
            scenario=scenario,
            strategy=cfg["strategy"],
            confidence=confidence,
            skill_id=cfg.get("skill_id"),
            max_steps=cfg.get("max_steps", 20),
            enable_reflection=cfg.get("enable_reflection", False),
            enable_planning=cfg.get("enable_planning", False),
            reasoning=reasoning,
            metadata={"decision_id": uuid.uuid4().hex[:12]},
        )

    @staticmethod
    def _build_classification_prompt(goal: str) -> str:
        scenario_names = ", ".join(s.value for s in Scenario)
        return (
            "You are a task classifier. Given the user's goal, classify it into one of "
            f"these scenarios: [{scenario_names}].\n\n"
            "Respond with ONLY a JSON object:\n"
            '{"scenario": "<scenario_value>", "confidence": <0.0-1.0>, "reasoning": "<brief reason>"}\n\n'
            f"User goal: {goal}"
        )

    def _parse_llm_response(self, goal: str, message: str) -> RoutingDecision | None:
        # Try to extract JSON from the response
        json_match = re.search(r"\{[^}]+\}", message, re.DOTALL)
        if not json_match:
            return None

        try:
            data = json.loads(json_match.group())
        except json.JSONDecodeError:
            return None

        scenario_str = data.get("scenario", "")
        try:
            scenario = Scenario(scenario_str)
        except ValueError:
            return None

        confidence = float(data.get("confidence", 0.5))
        confidence = max(0.0, min(1.0, confidence))
        reasoning = data.get("reasoning", "llm-classification")

        return self._build_decision(
            scenario=scenario,
            confidence=confidence,
            reasoning=f"llm-match: {reasoning}",
        )

    def _advisor_payload_to_decision(
        self, payload: dict[str, Any], rationale: str,
    ) -> RoutingDecision | None:
        """Convert a DecisionAdvisor payload into a RoutingDecision."""
        # Try scenario first, then infer from strategy
        scenario_str = payload.get("scenario", "")
        scenario: Scenario | None = None
        if scenario_str:
            try:
                scenario = Scenario(scenario_str)
            except ValueError:
                pass

        strategy_str = payload.get("strategy", "")
        strategy: ExecutionStrategy | None = None
        if strategy_str:
            try:
                strategy = ExecutionStrategy(strategy_str)
            except ValueError:
                pass

        # If we have a strategy but no scenario, look up the scenario from the map
        if scenario is None and strategy is not None:
            for scen, cfg in SCENARIO_STRATEGY_MAP.items():
                if cfg.get("strategy") == strategy:
                    scenario = scen
                    break

        # If we have a scenario but no strategy, look up from the map
        if strategy is None and scenario is not None:
            cfg = SCENARIO_STRATEGY_MAP.get(scenario, SCENARIO_STRATEGY_MAP[Scenario.FREE_FORM])
            strategy = cfg["strategy"]

        if scenario is None:
            scenario = Scenario.FREE_FORM
        if strategy is None:
            cfg = SCENARIO_STRATEGY_MAP.get(scenario, SCENARIO_STRATEGY_MAP[Scenario.FREE_FORM])
            strategy = cfg["strategy"]

        cfg = SCENARIO_STRATEGY_MAP.get(scenario, SCENARIO_STRATEGY_MAP[Scenario.FREE_FORM])
        skill_id = payload.get("skill_id") or cfg.get("skill_id")

        return RoutingDecision(
            scenario=scenario,
            strategy=strategy,
            confidence=0.7,  # advisor-accepted confidence
            skill_id=skill_id,
            max_steps=cfg.get("max_steps", 20),
            enable_reflection=cfg.get("enable_reflection", False),
            enable_planning=cfg.get("enable_planning", False),
            reasoning=f"advisor-match: {rationale}",
            metadata={"decision_id": uuid.uuid4().hex[:12], "advisor_source": "decision_advisor"},
        )


# Re-export the keyword index so it is accessible from tests if needed.
from .defaults import _KEYWORD_INDEX  # noqa: E402
