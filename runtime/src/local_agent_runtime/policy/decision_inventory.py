"""P0.1: Hard-Coded Decision Inventory.

Maps every hard-coded runtime decision that could later be delegated to an LLM.
Each decision has an ID, domain, current rule, and a description of what the
LLM would propose instead.

This inventory is the foundation for the LLM-assisted runtime transition:
all items here are candidates for proposal-based decision-making.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Decision:
    """A single hard-coded runtime decision."""

    id: str
    domain: str
    current_rule: str
    llm_proposal: str
    fallback: str = "keep current rule"


# ---------------------------------------------------------------------------
# Master inventory
# ---------------------------------------------------------------------------

INVENTORY: list[Decision] = [
    # P0: Task & Mode Routing
    Decision(
        id="D-MODE-001",
        domain="task_mode_routing",
        current_rule="Intent mode determined by keyword matching and message length heuristics",
        llm_proposal="Let LLM propose whether to answer directly or create a task",
        fallback="rule-based intent classification",
    ),
    Decision(
        id="D-MODE-002",
        domain="task_mode_routing",
        current_rule="Mode (direct/queued/supplement/collaboration) selected by dispatch params",
        llm_proposal="Let LLM propose normal, queued, supplement, or collaboration mode",
        fallback="default to 'direct' mode",
    ),
    Decision(
        id="D-MODE-003",
        domain="task_mode_routing",
        current_rule="Clarification triggered by explicit user question marks or ambiguity score",
        llm_proposal="Let LLM propose whether clarification is needed",
        fallback="no clarification",
    ),
    Decision(
        id="D-MODE-004",
        domain="task_mode_routing",
        current_rule="Mode validated against fixed enum and task state rules",
        llm_proposal="Validate proposed mode against current session/task state",
        fallback="reject invalid mode, fall back to deterministic routing",
    ),
    # P1: Subagent Planning
    Decision(
        id="D-PLAN-001",
        domain="subagent_planning",
        current_rule="Subagent count and roles determined by task size heuristics",
        llm_proposal="Let LLM propose whether subagents are needed",
        fallback="single-agent execution",
    ),
    Decision(
        id="D-PLAN-002",
        domain="subagent_planning",
        current_rule="Agent names assigned from fixed role set (explorer, worker, reviewer, verifier)",
        llm_proposal="Let LLM propose dynamic agent names",
        fallback="use fixed role names",
    ),
    Decision(
        id="D-PLAN-003",
        domain="subagent_planning",
        current_rule="Dependencies computed by simple ordering rules",
        llm_proposal="Let LLM propose dependencies",
        fallback="sequential ordering",
    ),
    Decision(
        id="D-PLAN-004",
        domain="subagent_planning",
        current_rule="Expected artifacts inferred from task type",
        llm_proposal="Let LLM propose expected artifacts",
        fallback="no artifact expectations",
    ),
    Decision(
        id="D-PLAN-005",
        domain="subagent_planning",
        current_rule="Done criteria set from task template",
        llm_proposal="Let LLM propose done criteria",
        fallback="default done criteria",
    ),
    Decision(
        id="D-PLAN-006",
        domain="subagent_planning",
        current_rule="Write scopes partitioned by directory convention",
        llm_proposal="Let LLM propose owned write scopes",
        fallback="shared workspace",
    ),
    Decision(
        id="D-PLAN-007",
        domain="subagent_planning",
        current_rule="Tools selected from fixed role-tool mapping",
        llm_proposal="Let LLM propose allowed tools",
        fallback="read-only tool set",
    ),
    Decision(
        id="D-PLAN-008",
        domain="subagent_planning",
        current_rule="Risk level set by task category",
        llm_proposal="Let LLM propose risk level",
        fallback="medium risk",
    ),
    Decision(
        id="D-PLAN-009",
        domain="subagent_planning",
        current_rule="Reviewer assigned for all multi-agent tasks",
        llm_proposal="Let LLM propose reviewer requirements",
        fallback="always assign reviewer",
    ),
    Decision(
        id="D-PLAN-010",
        domain="subagent_planning",
        current_rule="Verifier runs fixed test command",
        llm_proposal="Let LLM propose verifier requirements",
        fallback="skip verification",
    ),
    Decision(
        id="D-PLAN-011",
        domain="subagent_planning",
        current_rule="Fallback plan is retry-once-then-skip",
        llm_proposal="Let LLM propose fallback plan",
        fallback="retry once, then skip",
    ),
    Decision(
        id="D-PLAN-012",
        domain="subagent_planning",
        current_rule="Failure recovery is retry with exponential backoff",
        llm_proposal="Let LLM propose failure recovery policy",
        fallback="retry up to 3 times",
    ),
    Decision(
        id="D-PLAN-013",
        domain="subagent_planning",
        current_rule="Final synthesis merges all child outputs",
        llm_proposal="Let LLM propose final synthesis hints",
        fallback="merge all outputs",
    ),
    # P2: Model & Provider Selection
    Decision(
        id="D-MODEL-001",
        domain="model_provider",
        current_rule="Model selected from config default",
        llm_proposal="Let LLM propose model class by complexity",
        fallback="use default model",
    ),
    Decision(
        id="D-MODEL-002",
        domain="model_provider",
        current_rule="Planning and review always use strongest model",
        llm_proposal="Let LLM propose stronger model for planning or review",
        fallback="use default model for all",
    ),
    Decision(
        id="D-MODEL-003",
        domain="model_provider",
        current_rule="Summary/classification uses cheapest model",
        llm_proposal="Let LLM propose cheaper model for summary/classification",
        fallback="use default model",
    ),
    Decision(
        id="D-MODEL-004",
        domain="model_provider",
        current_rule="Provider validated against known set",
        llm_proposal="Validate configured provider availability",
        fallback="reject unknown provider",
    ),
    Decision(
        id="D-MODEL-005",
        domain="model_provider",
        current_rule="Budget tracked per-session with fixed limits",
        llm_proposal="Validate budget constraints",
        fallback="reject if over budget",
    ),
    # P3: Skill Selection
    Decision(
        id="D-SKILL-001",
        domain="skill_selection",
        current_rule="Skills matched by command prefix or keyword",
        llm_proposal="Let LLM propose relevant installed skills",
        fallback="no skill",
    ),
    Decision(
        id="D-SKILL-002",
        domain="skill_selection",
        current_rule="No skill proposed when no command prefix matches",
        llm_proposal="Let LLM propose no skill when none is needed",
        fallback="no skill",
    ),
    Decision(
        id="D-SKILL-003",
        domain="skill_selection",
        current_rule="Root-only skills checked against allowlist",
        llm_proposal="Validate root skill allowlist",
        fallback="reject root skill not in allowlist",
    ),
    # P4: Tool Selection & Permission
    Decision(
        id="D-TOOL-001",
        domain="tool_selection",
        current_rule="Minimal tool set assigned per role",
        llm_proposal="Let LLM propose minimal required tools",
        fallback="read-only tools",
    ),
    Decision(
        id="D-TOOL-002",
        domain="tool_selection",
        current_rule="Read-only vs write capability determined by role",
        llm_proposal="Let LLM propose read-only or write-capable tool scope",
        fallback="read-only unless worker",
    ),
    Decision(
        id="D-TOOL-003",
        domain="tool_selection",
        current_rule="Unsafe tools (task, etc.) always rejected",
        llm_proposal="Let LLM propose tools, validate against safety list",
        fallback="reject unsafe tools",
    ),
    # P5: MCP Selection
    Decision(
        id="D-MCP-001",
        domain="mcp_selection",
        current_rule="MCP servers configured statically in workspace config",
        llm_proposal="Let LLM propose relevant MCP server/tool",
        fallback="no MCP",
    ),
    # P6: Context Assembly
    Decision(
        id="D-CTX-001",
        domain="context_assembly",
        current_rule="Context sections assembled from fixed template",
        llm_proposal="Let LLM propose task-relevant context sections",
        fallback="default context template",
    ),
    Decision(
        id="D-CTX-002",
        domain="context_assembly",
        current_rule="Compaction triggered by token count threshold",
        llm_proposal="Let LLM propose context sections to summarize or drop",
        fallback="drop oldest sections first",
    ),
    Decision(
        id="D-CTX-003",
        domain="context_assembly",
        current_rule="Token budget enforced per-request with fixed limit",
        llm_proposal="Validate token budget",
        fallback="reject if over budget",
    ),
    Decision(
        id="D-CTX-004",
        domain="context_assembly",
        current_rule="System and safety sections always included",
        llm_proposal="Validate required system/safety sections",
        fallback="always include them",
    ),
    # P7: Memory
    Decision(
        id="D-MEM-001",
        domain="memory",
        current_rule="Memory recalled by relevance score threshold",
        llm_proposal="Let LLM propose memory recall focus",
        fallback="recall all relevant memories",
    ),
    Decision(
        id="D-MEM-002",
        domain="memory",
        current_rule="Extraction runs after task completion",
        llm_proposal="Let LLM propose memory extraction from completed work",
        fallback="extract automatically",
    ),
    Decision(
        id="D-MEM-003",
        domain="memory",
        current_rule="Invalidation triggered by age or overwrite",
        llm_proposal="Let LLM propose stale memory invalidation",
        fallback="keep all memories",
    ),
    Decision(
        id="D-MEM-004",
        domain="memory",
        current_rule="Source IDs validated as non-empty strings",
        llm_proposal="Validate memory source ids",
        fallback="reject invalid source ids",
    ),
    # P8: Artifact & Report
    Decision(
        id="D-ART-001",
        domain="artifact_report",
        current_rule="Artifacts linked to tasks by write scope",
        llm_proposal="Let LLM propose artifact linking",
        fallback="link by scope overlap",
    ),
    Decision(
        id="D-ART-002",
        domain="artifact_report",
        current_rule="Report generated with fixed template sections",
        llm_proposal="Let LLM propose report structure",
        fallback="default report template",
    ),
    # P9: Risk & Approval
    Decision(
        id="D-RISK-001",
        domain="risk_approval",
        current_rule="Risk level validated against fixed enum (low/medium/high/critical)",
        llm_proposal="Let LLM propose risk level",
        fallback="medium risk",
    ),
    Decision(
        id="D-RISK-002",
        domain="risk_approval",
        current_rule="High/critical risk requires approval gates",
        llm_proposal="Let LLM propose approval gates",
        fallback="require reviewer gate",
    ),
    Decision(
        id="D-RISK-003",
        domain="risk_approval",
        current_rule="Approval gate types validated against fixed set",
        llm_proposal="Validate approval requirements",
        fallback="require at least reviewer",
    ),
    Decision(
        id="D-RISK-004",
        domain="risk_approval",
        current_rule="Test strategy selected by file extension patterns",
        llm_proposal="Let LLM propose test strategy by changed files and risk",
        fallback="run pytest",
    ),
    # P10: Retry & Recovery
    Decision(
        id="D-RETRY-001",
        domain="retry_recovery",
        current_rule="Max retries fixed at 3 with exponential backoff",
        llm_proposal="Let LLM propose retry or fallback strategy",
        fallback="retry up to 3 times",
    ),
    Decision(
        id="D-RETRY-002",
        domain="retry_recovery",
        current_rule="Retry budget validated (0-10 retries, non-negative delay)",
        llm_proposal="Validate retry budget",
        fallback="reject invalid budget",
    ),
    Decision(
        id="D-RETRY-003",
        domain="retry_recovery",
        current_rule="User clarification requested on ambiguous failures",
        llm_proposal="Let LLM propose when to ask the user for clarification",
        fallback="ask on 3rd failure",
    ),
    # P11: Frontend Presentation
    Decision(
        id="D-UI-001",
        domain="frontend",
        current_rule="Events routed to chat/panel/trace by event type",
        llm_proposal="Let LLM propose trace summary grouping and panel labels",
        fallback="default visibility routing",
    ),
    # P12: Synthesis & TODO
    Decision(
        id="D-SYNTH-001",
        domain="synthesis",
        current_rule="Trace summary generated from all events in range",
        llm_proposal="Let LLM propose final synthesis structure",
        fallback="merge all child outputs",
    ),
    Decision(
        id="D-SYNTH-002",
        domain="synthesis",
        current_rule="Unverified artifacts excluded from completedWork",
        llm_proposal="Validate synthesis does not claim unverified artifacts",
        fallback="only include verified artifacts",
    ),
    Decision(
        id="D-SYNTH-003",
        domain="synthesis",
        current_rule="TODO maintenance not automated",
        llm_proposal="Let LLM propose completed TODO updates from verified artifacts",
        fallback="no TODO updates",
    ),
    Decision(
        id="D-SYNTH-004",
        domain="synthesis",
        current_rule="Roadmap edits always require approval",
        llm_proposal="Require approval before editing roadmap docs",
        fallback="always require approval",
    ),
]


# ---------------------------------------------------------------------------
# Lookup helpers
# ---------------------------------------------------------------------------

_INVENTORY_BY_ID: dict[str, Decision] = {d.id: d for d in INVENTORY}
_INVENTORY_BY_DOMAIN: dict[str, list[Decision]] = {}
for _d in INVENTORY:
    _INVENTORY_BY_DOMAIN.setdefault(_d.domain, []).append(_d)


def get_decision(decision_id: str) -> Decision | None:
    """Look up a decision by ID."""
    return _INVENTORY_BY_ID.get(decision_id)


def get_decisions_by_domain(domain: str) -> list[Decision]:
    """Get all decisions in a domain."""
    return _INVENTORY_BY_DOMAIN.get(domain, [])


def list_domains() -> list[str]:
    """List all decision domains."""
    return sorted(_INVENTORY_BY_DOMAIN.keys())


def count_decisions() -> int:
    """Total number of inventoried decisions."""
    return len(INVENTORY)
