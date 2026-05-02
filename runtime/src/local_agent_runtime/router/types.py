from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Scenario(str, Enum):
    """Identifiable user-intent scenarios that the agent can distinguish."""

    SIMPLE_QUERY = "simple_query"
    CODE_SEARCH = "code_search"
    CODE_EDIT = "code_edit"
    CODE_REVIEW = "code_review"
    DEBUG = "debug"
    TEST_WRITE = "test_write"
    DOC_WRITE = "doc_write"
    MULTI_STEP_TASK = "multi_step_task"
    SUPERVISED_TASK = "supervised_task"
    SWARM_TASK = "swarm_task"
    FREE_FORM = "free_form"


class ExecutionStrategy(str, Enum):
    """Execution strategy chosen by the meta-router for a given scenario."""

    REACT_FAST = "react_fast"
    REACT_STANDARD = "react_standard"
    REACT_WITH_REFLECTION = "react_reflect"
    SKILL_BASED = "skill_based"
    PLAN_THEN_EXECUTE = "plan_execute"
    PLAN_SUPERVISE = "plan_supervise"
    PLAN_SWARM = "plan_swarm"


@dataclass(slots=True)
class RoutingDecision:
    """The output of the meta-router: which scenario and strategy to use."""

    scenario: Scenario
    strategy: ExecutionStrategy
    confidence: float
    skill_id: str | None = None
    max_steps: int = 20
    enable_reflection: bool = False
    enable_planning: bool = False
    reasoning: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
