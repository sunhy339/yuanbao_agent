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
_CODE_FILE_RE = re.compile(
    r"(?<![\w.-])[\w./\\-]+\."
    r"(?:py|html|css|ts|tsx|js|jsx|rs|go|java|kt|cs|cpp|c|h|hpp|php|rb|swift|scala|sql|sh|ps1)"
    r"(?![\w.-])",
    re.IGNORECASE,
)
_TEST_COMMAND_RE = re.compile(
    r"\b(?:python\s+-m\s+pytest|pytest|npm(?:\.cmd)?\s+(?:test|run\s+test)|"
    r"pnpm\s+test|yarn\s+test|cargo\s+test|go\s+test|vitest|jest|unittest)\b",
    re.IGNORECASE,
)
_TEST_INTENT_RE = re.compile(r"\b(?:test|tests|spec|regression)\b|测试|回归", re.IGNORECASE)
_CODE_EDIT_INTENT_RE = re.compile(
    r"\b(?:fix|modify|change|implement|refactor|update)\b|修复|修改|实现|重构|更新",
    re.IGNORECASE,
)

_CODE_GENERATION_INTENT_RE = re.compile(
    r"\b(?:develop|create|generate|build|write|make|scaffold)\b|"
    r"\u5f00\u53d1|\u751f\u6210|\u521b\u5efa|\u5b9e\u73b0|\u7f16\u5199|\u505a\u4e00\u4e2a",
    re.IGNORECASE,
)
_CODE_DEVELOPMENT_TARGET_RE = re.compile(
    r"\b(?:python|html|css|javascript|typescript|tkinter|pygame|script|cli|app|game|website|site|blog|webpage|frontend)\b|"
    r"\u7f51\u7ad9|\u535a\u5ba2|\u9759\u6001\u9875|\u9875\u9762|\u524d\u7aef|\u6e38\u620f|\u5c0f\u6e38\u620f|\u811a\u672c|\u5e94\u7528",
    re.IGNORECASE,
)
_DOC_SIGNAL_RE = re.compile(
    r"\b(?:readme|docs?|documentation)\b|\u6587\u6863|\u8bf4\u660e",
    re.IGNORECASE,
)
_WORK_DOMAIN_SIGNAL_RE = re.compile(
    r"\b(?:frontend|front-end|backend|back-end|api|database|storage|validation|"
    r"error handling|tests?|readme|docs?|documentation|ui|data model)\b|"
    r"\u524d\u7aef|\u540e\u7aef|\u63a5\u53e3|\u6570\u636e|\u5b58\u50a8|"
    r"\u6821\u9a8c|\u9a8c\u8bc1|\u9519\u8bef\u5904\u7406|\u6d4b\u8bd5|"
    r"\u6587\u6863|\u67b6\u6784",
    re.IGNORECASE,
)


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
        defer_rule_to_llm = self._should_defer_rule_to_llm(goal, rule_result)
        if rule_result.confidence >= _RULE_CONFIDENCE_THRESHOLD and not defer_rule_to_llm:
            return rule_result

        llm_result = self._llm_route(
            goal,
            context,
            rule_candidate=rule_result if defer_rule_to_llm else None,
        )
        if llm_result is not None and defer_rule_to_llm:
            llm_result.metadata["rule_candidate"] = {
                "scenario": rule_result.scenario.value,
                "strategy": rule_result.strategy.value,
                "confidence": rule_result.confidence,
                "reasoning": rule_result.reasoning,
            }
            return self._guard_deferred_llm_route(goal, rule_result, llm_result)
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

        # Also try the full lowered goal against keyword index for multi-word
        # keywords like "code_review" or "安全检查". Do this even when a token
        # already matched so mixed requests such as "read README, then fix
        # cart.py" do not get pinned to README alone.
        lowered = goal.lower().strip()
        for kw, (scenario, conf) in _KEYWORD_INDEX.items():
            if len(kw) > 1 and kw in lowered and conf > best_conf:
                best_scenario = scenario
                best_conf = conf
                matched_keyword = kw

        if best_scenario is None and self._looks_like_development_goal(goal):
            best_scenario = Scenario.CODE_EDIT
            best_conf = 0.82
            matched_keyword = "development-artifact"
        elif best_scenario is None:
            best_scenario = Scenario.FREE_FORM
            best_conf = 0.3
        else:
            override = self._planning_task_override(goal, best_scenario, matched_keyword)
            if override is None:
                override = self._development_task_override(goal, best_scenario, matched_keyword)
            if override is not None:
                best_scenario, best_conf, matched_keyword = override

        return self._build_decision(
            scenario=best_scenario,
            confidence=best_conf,
            reasoning=f"rule-match: keyword='{matched_keyword}'",
        )

    def _planning_task_override(
        self,
        goal: str,
        scenario: Scenario,
        matched_keyword: str,
    ) -> tuple[Scenario, float, str] | None:
        if self._decision_advisor is not None or self._provider is not None:
            return None
        if scenario not in {Scenario.DOC_WRITE, Scenario.CODE_EDIT, Scenario.TEST_WRITE, Scenario.DEBUG}:
            return None
        if not self._looks_like_complex_planning_goal(goal):
            return None
        return (
            Scenario.MULTI_STEP_TASK,
            0.84,
            f"planning-task-overrides-doc:{matched_keyword or 'planning'}",
        )

    def _development_task_override(
        self,
        goal: str,
        scenario: Scenario,
        matched_keyword: str,
    ) -> tuple[Scenario, float, str] | None:
        if scenario not in {Scenario.DOC_WRITE, Scenario.TEST_WRITE, Scenario.DEBUG}:
            return None
        code_files = [match.group(0) for match in _CODE_FILE_RE.finditer(goal)]
        if not code_files:
            return None
        non_test_code_files = [path for path in code_files if not self._looks_like_test_file(path)]
        if not non_test_code_files:
            return None
        has_test_command = _TEST_COMMAND_RE.search(goal) is not None
        has_code_edit_intent = (
            _CODE_EDIT_INTENT_RE.search(goal) is not None
            or _CODE_GENERATION_INTENT_RE.search(goal) is not None
        )
        has_test_file = len(non_test_code_files) != len(code_files)
        has_test_intent = _TEST_INTENT_RE.search(goal) is not None
        if not (has_code_edit_intent and (has_test_command or has_test_file or not has_test_intent)):
            return None
        return (
            Scenario.CODE_EDIT,
            0.86,
            f"development-task-overrides-doc:{matched_keyword or 'doc'}",
        )

    def _looks_like_development_goal(self, goal: str) -> bool:
        return (
            _CODE_GENERATION_INTENT_RE.search(goal) is not None
            and (
                _CODE_FILE_RE.search(goal) is not None
                or _CODE_DEVELOPMENT_TARGET_RE.search(goal) is not None
            )
        )

    def _should_defer_rule_to_llm(self, goal: str, rule_result: RoutingDecision) -> bool:
        if self._decision_advisor is None and self._provider is None:
            return False
        if not _DOC_SIGNAL_RE.search(goal):
            return False
        if rule_result.scenario not in {Scenario.DOC_WRITE, Scenario.CODE_EDIT, Scenario.TEST_WRITE, Scenario.DEBUG}:
            return False
        code_file_signal = _CODE_FILE_RE.search(goal) is not None
        development_intent = (
            _CODE_EDIT_INTENT_RE.search(goal) is not None
            or _CODE_GENERATION_INTENT_RE.search(goal) is not None
            or _TEST_COMMAND_RE.search(goal) is not None
            or _TEST_INTENT_RE.search(goal) is not None
        )
        target_signal = _CODE_DEVELOPMENT_TARGET_RE.search(goal) is not None
        return (
            (code_file_signal and development_intent)
            or (target_signal and development_intent)
            or self._looks_like_complex_planning_goal(goal)
        )

    def _guard_deferred_llm_route(
        self,
        goal: str,
        rule_result: RoutingDecision,
        llm_result: RoutingDecision,
    ) -> RoutingDecision:
        """Keep the advisor from over-planning simple artifact generation."""
        if (
            rule_result.scenario == Scenario.CODE_EDIT
            and llm_result.scenario == Scenario.MULTI_STEP_TASK
            and self._looks_like_development_goal(goal)
            and not self._has_planning_or_delegation_signal(goal)
        ):
            guarded = self._build_decision(
                scenario=Scenario.CODE_EDIT,
                confidence=max(rule_result.confidence, 0.86),
                reasoning=(
                    "rule-fallback-after-advisor-overplanned: "
                    f"{llm_result.reasoning}"
                ),
            )
            guarded.metadata["advisor_candidate"] = {
                "scenario": llm_result.scenario.value,
                "strategy": llm_result.strategy.value,
                "confidence": llm_result.confidence,
                "reasoning": llm_result.reasoning,
            }
            guarded.metadata["rule_candidate"] = dict(llm_result.metadata.get("rule_candidate") or {})
            return guarded
        return llm_result

    @staticmethod
    def _has_planning_or_delegation_signal(goal: str) -> bool:
        lowered = goal.casefold()
        planning_markers = (
            "plan",
            "planning",
            "roadmap",
            "break down",
            "decompose",
            "subtask",
            "multi-agent",
            "subagent",
            "swarm",
            "supervise",
            "migrate",
            "migration",
            "across modules",
            "entire codebase",
            "agent",
            "agents",
            "\u89c4\u5212",
            "\u4efb\u52a1\u89c4\u5212",
            "\u62c6\u5206",
            "\u5e76\u884c",
            "\u591a agent",
            "\u591a\u4e2a agent",
            "\u5b50\u4efb\u52a1",
            "\u591a\u9636\u6bb5",
        )
        return any(marker in lowered for marker in planning_markers)

    def _looks_like_complex_planning_goal(self, goal: str) -> bool:
        if not self._has_planning_or_delegation_signal(goal):
            return False
        domain_hits = {
            match.group(0).casefold()
            for match in _WORK_DOMAIN_SIGNAL_RE.finditer(goal)
            if match.group(0).strip()
        }
        return len(domain_hits) >= 2

    @staticmethod
    def _looks_like_test_file(path: str) -> bool:
        normalized = path.replace("\\", "/").lower()
        name = normalized.rsplit("/", 1)[-1]
        return (
            name.startswith("test_")
            or "_test." in name
            or ".test." in name
            or ".spec." in name
            or "/tests/" in f"/{normalized}"
        )

    # ------------------------------------------------------------------
    # Phase 2 – LLM-based routing (optional)
    # ------------------------------------------------------------------

    @property
    def last_advice(self) -> AdviceResult | None:
        """Return the last DecisionAdvisor result from the most recent route() call."""
        return self._last_advice

    def _llm_route(
        self,
        goal: str,
        context: dict[str, Any] | None,
        *,
        rule_candidate: RoutingDecision | None = None,
    ) -> RoutingDecision | None:
        self._last_advice = None

        # Prefer DecisionAdvisor path — creates durable proposal records
        if self._decision_advisor is not None:
            input_context: dict[str, Any] = {"goal": goal}
            if rule_candidate is not None:
                input_context["rule_candidate"] = {
                    "scenario": rule_candidate.scenario.value,
                    "strategy": rule_candidate.strategy.value,
                    "confidence": rule_candidate.confidence,
                    "reasoning": rule_candidate.reasoning,
                }
                input_context["routing_guidance"] = [
                    (
                        "If the user asks only for a complex work plan, task decomposition, "
                        "or multi-agent execution plan, choose scenario=multi_step_task "
                        "and strategy=plan_execute, even if README/docs are one subtask."
                    ),
                    (
                        "If the primary deliverable is code, an app, a game, or a static website, "
                        "and README/docs are only supporting files, choose "
                        "scenario=code_edit and strategy=react_standard."
                    ),
                    (
                        "If the primary deliverable is code but the user explicitly requires "
                        "multi-agent work, subagents, delegation, parallel agents, or a long-running "
                        "orchestrated execution flow, choose scenario=swarm_task and strategy=plan_swarm "
                        "so the runtime can delegate and then continue implementation."
                    ),
                    (
                        "Do not choose multi_step_task just because the user asked for multiple "
                        "files or a lightweight verification command."
                    ),
                    (
                        "Choose multi_step_task only when the user asks for broad planning, "
                        "migration, decomposition, subagents, supervision, or multi-phase work."
                    ),
                ]
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
