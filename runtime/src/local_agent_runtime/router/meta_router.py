"""Meta-router: classify user goals into scenarios and pick an execution strategy.

Two-phase routing:
  1. Rule-based keyword matching (zero cost, deterministic).
  2. Optional LLM-based classification when rule confidence is low.
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any

from .defaults import SCENARIO_STRATEGY_MAP, lookup_keyword
from .types import ExecutionStrategy, RoutingDecision, Scenario

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

    def __init__(self, provider: Any | None = None) -> None:
        self._provider = provider

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

    def _llm_route(self, goal: str, context: dict[str, Any] | None) -> RoutingDecision | None:
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


# Re-export the keyword index so it is accessible from tests if needed.
from .defaults import _KEYWORD_INDEX  # noqa: E402
