"""Runtime validators for LLM proposal records.

Each validator receives a proposal payload and returns a list of rejection
reasons.  An empty list means the proposal passes that validator.
"""

from __future__ import annotations

from typing import Any

from ..models import ProposalKind
from ..services.worker_environment import (
    KNOWN_CHILD_TOOLS,
    UNSAFE_CHILD_TOOLS,
    resolve_tool_alias,
)

# ---------------------------------------------------------------------------
# Schema validator
# ---------------------------------------------------------------------------

REQUIRED_FIELDS_BY_KIND: dict[str, list[str]] = {
    "intent_mode": ["mode"],
    "decomposition": ["subtasks"],
    "agent_profile": ["name", "baseType", "mission"],
    "model_policy": ["model"],
    "skill_policy": ["skillId"],
    "tool_policy": ["allowedTools"],
    "mcp_policy": ["serverId"],
    "context_policy": ["sections"],
    "memory_policy": ["action"],
    "artifact_contract": ["kind", "status"],
    "risk_policy": ["riskLevel"],
    "approval_policy": ["gates"],
    "test_strategy": ["commands"],
    "failure_recovery": ["strategy"],
    "event_presentation": ["grouping"],
    "synthesis_strategy": ["structure"],
    "todo_maintenance": ["updates"],
}


def validate_proposal_schema(kind: str, payload: dict[str, Any]) -> list[str]:
    """Check that required fields exist for the proposal kind."""
    if kind not in REQUIRED_FIELDS_BY_KIND:
        return [f"Unknown proposal kind: {kind!r}"]
    required = REQUIRED_FIELDS_BY_KIND[kind]
    reasons: list[str] = []
    for field in required:
        if field not in payload:
            reasons.append(f"Missing required field: {field!r}")
    return reasons


# ---------------------------------------------------------------------------
# Tool allowlist validator
# ---------------------------------------------------------------------------

SAFE_BUILTIN_TOOLS = frozenset({
    "list_dir", "search_files", "read_file", "git_status", "git_diff",
    "code_search", "web_fetch", "browser", "run_command", "apply_patch",
})


def validate_tool_allowlist(payload: dict[str, Any]) -> list[str]:
    """Validate that proposed tools are known and allowed."""
    tools = payload.get("allowedTools", [])
    if not isinstance(tools, list):
        return ["allowedTools must be a list"]
    reasons: list[str] = []
    for tool in tools:
        resolved = resolve_tool_alias(str(tool))
        if resolved not in SAFE_BUILTIN_TOOLS:
            reasons.append(f"Unknown or disallowed tool: {tool!r} (resolved: {resolved!r})")
    return reasons


# ---------------------------------------------------------------------------
# Unsafe tool validator
# ---------------------------------------------------------------------------

UNSAFE_TOOLS = frozenset({"task"}) | UNSAFE_CHILD_TOOLS


def validate_no_unsafe_tools(payload: dict[str, Any]) -> list[str]:
    """Reject proposals that include unsafe tools."""
    tools = payload.get("allowedTools", [])
    if not isinstance(tools, list):
        return []
    reasons: list[str] = []
    for tool in tools:
        resolved = resolve_tool_alias(str(tool))
        if resolved in UNSAFE_TOOLS:
            reasons.append(f"Unsafe tool proposed: {tool!r} (resolved: {resolved!r})")
    return reasons


# ---------------------------------------------------------------------------
# Dependency graph validator
# ---------------------------------------------------------------------------

def validate_dependency_graph(
    subtasks: list[dict[str, Any]],
) -> list[str]:
    """Validate subtask dependency references and detect cycles."""
    if not isinstance(subtasks, list):
        return ["subtasks must be a list"]
    reasons: list[str] = []
    task_ids = set()
    for i, st in enumerate(subtasks):
        if not isinstance(st, dict):
            reasons.append(f"subtasks[{i}] must be a dict")
            continue
        tid = st.get("id") or st.get("taskId")
        if tid:
            task_ids.add(tid)
    # Check dependency references
    for i, st in enumerate(subtasks):
        if not isinstance(st, dict):
            continue
        deps = st.get("dependencies", [])
        if not isinstance(deps, list):
            continue
        for dep in deps:
            if dep not in task_ids:
                reasons.append(f"subtasks[{i}] references unknown dependency: {dep!r}")
    # Detect simple cycles (any task depending on itself)
    for i, st in enumerate(subtasks):
        if not isinstance(st, dict):
            continue
        tid = st.get("id") or st.get("taskId")
        deps = st.get("dependencies", [])
        if tid and tid in deps:
            reasons.append(f"subtasks[{i}] has self-dependency: {tid!r}")
    return reasons


# ---------------------------------------------------------------------------
# Write scope validator
# ---------------------------------------------------------------------------

def validate_write_scopes(
    subtasks: list[dict[str, Any]],
) -> list[str]:
    """Detect overlapping write scopes across subtasks."""
    if not isinstance(subtasks, list):
        return ["subtasks must be a list"]
    reasons: list[str] = []
    scope_map: dict[str, str] = {}  # scope_path -> task_id
    for i, st in enumerate(subtasks):
        if not isinstance(st, dict):
            continue
        tid = st.get("id") or st.get("taskId") or f"subtask_{i}"
        scopes = st.get("ownedScope") or st.get("writeScope")
        if not scopes:
            continue
        if isinstance(scopes, str):
            scopes = [scopes]
        if not isinstance(scopes, list):
            continue
        for scope in scopes:
            scope_str = str(scope)
            if scope_str in scope_map:
                reasons.append(
                    f"Overlapping write scope {scope_str!r} between "
                    f"{scope_map[scope_str]!r} and {tid!r}"
                )
            else:
                scope_map[scope_str] = tid
    return reasons


# ---------------------------------------------------------------------------
# Artifact contract validator
# ---------------------------------------------------------------------------

VALID_ARTIFACT_KINDS = frozenset({"plan", "file", "patch", "review", "test_report", "asset"})


def validate_artifact_contract(payload: dict[str, Any]) -> list[str]:
    """Validate artifact contract proposals."""
    reasons: list[str] = []
    kind = payload.get("kind")
    if kind and kind not in VALID_ARTIFACT_KINDS:
        reasons.append(f"Invalid artifact kind: {kind!r}")
    status = payload.get("status")
    valid_statuses = {"proposed", "applied", "verified", "rejected"}
    if status and status not in valid_statuses:
        reasons.append(f"Invalid artifact status: {status!r}")
    return reasons


# ---------------------------------------------------------------------------
# Composite validator
# ---------------------------------------------------------------------------

def validate_proposal(kind: str, payload: dict[str, Any]) -> list[str]:
    """Run all applicable validators and return combined rejection reasons."""
    reasons: list[str] = []
    reasons.extend(validate_proposal_schema(kind, payload))

    # Tool validators
    if kind == "tool_policy":
        reasons.extend(validate_tool_allowlist(payload))
        reasons.extend(validate_no_unsafe_tools(payload))

    # Dependency + scope validators for decomposition
    if kind == "decomposition":
        subtasks = payload.get("subtasks", [])
        reasons.extend(validate_dependency_graph(subtasks))
        reasons.extend(validate_write_scopes(subtasks))

    # Artifact contract validator
    if kind == "artifact_contract":
        reasons.extend(validate_artifact_contract(payload))

    # Risk policy validator
    if kind == "risk_policy":
        reasons.extend(validate_risk_policy(payload))

    # Approval gate validator
    if kind == "approval_policy":
        reasons.extend(validate_approval_gates(payload))

    # Test strategy validator
    if kind == "test_strategy":
        reasons.extend(validate_test_strategy(payload))

    # Intent mode validator
    if kind == "intent_mode":
        reasons.extend(validate_mode(payload))
        reasons.extend(validate_session_task_state(payload))

    # Model policy validator
    if kind == "model_policy":
        reasons.extend(validate_model_provider(payload))

    # MCP policy validator
    if kind == "mcp_policy":
        reasons.extend(validate_mcp_availability(payload))

    # Context policy validator
    if kind == "context_policy":
        reasons.extend(validate_context_budget(payload))

    # Memory policy validator
    if kind == "memory_policy":
        reasons.extend(validate_memory_source(payload))

    # Failure recovery validator
    if kind == "failure_recovery":
        reasons.extend(validate_retry_budget(payload))

    # Event presentation validator
    if kind == "event_presentation":
        reasons.extend(validate_frontend_visibility(payload))

    # TODO maintenance validator
    if kind == "todo_maintenance":
        reasons.extend(validate_roadmap_edit(payload))

    # Skill policy validator
    if kind == "skill_policy":
        reasons.extend(validate_skill_availability(payload))
        reasons.extend(validate_skill_root_allowlist(payload))

    return reasons


# ---------------------------------------------------------------------------
# Risk policy validator
# ---------------------------------------------------------------------------

VALID_RISK_LEVELS = frozenset({"low", "medium", "high", "critical"})

RISK_APPROVAL_REQUIREMENTS: dict[str, list[str]] = {
    "low": [],
    "medium": [],
    "high": ["reviewer"],
    "critical": ["reviewer", "verifier"],
}


def validate_risk_policy(payload: dict[str, Any]) -> list[str]:
    """Validate risk level and approval requirements."""
    reasons: list[str] = []
    risk = payload.get("riskLevel")
    if not risk:
        return reasons
    if risk not in VALID_RISK_LEVELS:
        reasons.append(f"Invalid riskLevel: {risk!r}. Must be one of {sorted(VALID_RISK_LEVELS)}")
        return reasons
    # Check if high/critical risk has required gates
    required_gates = RISK_APPROVAL_REQUIREMENTS.get(risk, [])
    if required_gates:
        gates = payload.get("approvalGates", [])
        if not isinstance(gates, list):
            gates = []
        for gate in required_gates:
            if gate not in gates:
                reasons.append(
                    f"Risk level {risk!r} requires approval gate {gate!r} but it is not specified"
                )
    return reasons


# ---------------------------------------------------------------------------
# Approval gate validator
# ---------------------------------------------------------------------------

VALID_GATE_TYPES = frozenset({"reviewer", "verifier", "user", "automated"})


def validate_approval_gates(payload: dict[str, Any]) -> list[str]:
    """Validate approval gate proposals."""
    reasons: list[str] = []
    gates = payload.get("gates")
    if not isinstance(gates, list):
        reasons.append("gates must be a list")
        return reasons
    if len(gates) == 0:
        reasons.append("gates must be non-empty")
        return reasons
    for i, gate in enumerate(gates):
        if not isinstance(gate, dict):
            reasons.append(f"gates[{i}] must be a dict")
            continue
        gate_type = gate.get("type")
        if not gate_type:
            reasons.append(f"gates[{i}] missing required field 'type'")
        elif gate_type not in VALID_GATE_TYPES:
            reasons.append(
                f"gates[{i}] invalid gate type: {gate_type!r}. "
                f"Must be one of {sorted(VALID_GATE_TYPES)}"
            )
        # condition is optional but must be a non-empty string if present
        condition = gate.get("condition")
        if condition is not None and (not isinstance(condition, str) or not condition.strip()):
            reasons.append(f"gates[{i}] condition must be a non-empty string if provided")
    return reasons


# ---------------------------------------------------------------------------
# Test strategy validator
# ---------------------------------------------------------------------------

SAFE_TEST_COMMANDS = frozenset({
    "python -m pytest",
    "pytest",
    "npm test",
    "npx jest",
    "make test",
    "go test ./...",
    "cargo test",
})

DANGEROUS_TEST_PATTERNS = frozenset({
    "rm ", "del ", "format ", "shutdown", "reboot",
    "drop ", "delete from", "truncate ",
})


def validate_test_strategy(payload: dict[str, Any]) -> list[str]:
    """Validate test strategy proposals including command safety."""
    reasons: list[str] = []
    commands = payload.get("commands")
    if not isinstance(commands, list):
        reasons.append("commands must be a list")
        return reasons
    if len(commands) == 0:
        reasons.append("commands must be non-empty")
        return reasons
    for i, cmd in enumerate(commands):
        if not isinstance(cmd, str):
            reasons.append(f"commands[{i}] must be a string")
            continue
        cmd_lower = cmd.lower().strip()
        if not cmd_lower:
            reasons.append(f"commands[{i}] must be a non-empty string")
            continue
        # Check for dangerous patterns
        for pattern in DANGEROUS_TEST_PATTERNS:
            if pattern in cmd_lower:
                reasons.append(f"commands[{i}] contains dangerous pattern: {pattern.strip()!r}")
                break
    return reasons


# ---------------------------------------------------------------------------
# Session/task state validator
# ---------------------------------------------------------------------------

VALID_SESSION_STATES = frozenset({"active", "paused", "closed"})
VALID_TASK_STATES = frozenset({
    "running", "queued", "completed", "failed", "cancelled", "paused",
})


def validate_session_task_state(payload: dict[str, Any]) -> list[str]:
    """Validate session and task state references."""
    reasons: list[str] = []
    session_state = payload.get("sessionState")
    if session_state and session_state not in VALID_SESSION_STATES:
        reasons.append(
            f"Invalid sessionState: {session_state!r}. "
            f"Must be one of {sorted(VALID_SESSION_STATES)}"
        )
    task_state = payload.get("taskState")
    if task_state and task_state not in VALID_TASK_STATES:
        reasons.append(
            f"Invalid taskState: {task_state!r}. "
            f"Must be one of {sorted(VALID_TASK_STATES)}"
        )
    return reasons


# ---------------------------------------------------------------------------
# Mode validator
# ---------------------------------------------------------------------------

VALID_MODES = frozenset({
    "direct", "task", "queued", "supplement", "collaboration", "clarification",
})


def validate_mode(payload: dict[str, Any]) -> list[str]:
    """Validate intent mode proposal."""
    reasons: list[str] = []
    mode = payload.get("mode")
    if not mode:
        return reasons
    if mode not in VALID_MODES:
        reasons.append(
            f"Invalid mode: {mode!r}. Must be one of {sorted(VALID_MODES)}"
        )
        return reasons
    # Validate mode compatibility with task state when taskState is provided
    task_state = payload.get("taskState")
    if task_state:
        if mode == "supplement" and task_state not in ("running", "paused"):
            reasons.append(
                "Supplement mode requires an active or paused task"
            )
    return reasons


# ---------------------------------------------------------------------------
# Model/provider availability validator
# ---------------------------------------------------------------------------

KNOWN_MODELS = frozenset({
    "claude-sonnet-4-6", "claude-opus-4-7", "claude-haiku-4-5",
    "gpt-4o", "gpt-4o-mini", "gpt-4-turbo",
    "glm-5.1", "glm-4-plus",
})

KNOWN_PROVIDERS = frozenset({
    "anthropic", "openai", "zhipu", "azure", "local",
})


def validate_model_provider(payload: dict[str, Any]) -> list[str]:
    """Validate model and provider selection proposals."""
    reasons: list[str] = []
    model = payload.get("model")
    if model and model not in KNOWN_MODELS:
        reasons.append(f"Unknown model: {model!r}")
    provider = payload.get("provider")
    if provider and provider not in KNOWN_PROVIDERS:
        reasons.append(f"Unknown provider: {provider!r}")
    # Validate model budget
    budget = payload.get("budget")
    if budget is not None:
        if not isinstance(budget, (int, float)) or budget < 0:
            reasons.append("budget must be a non-negative number")
    return reasons


# ---------------------------------------------------------------------------
# MCP server availability validator
# ---------------------------------------------------------------------------


def validate_mcp_availability(payload: dict[str, Any]) -> list[str]:
    """Validate MCP server and tool schema proposals."""
    reasons: list[str] = []
    server_id = payload.get("serverId")
    if server_id is None:
        return reasons
    if not isinstance(server_id, str) or not server_id.strip():
        reasons.append("serverId must be a non-empty string")
        return reasons
    # Validate tool schemas if provided
    tools = payload.get("tools")
    if tools is not None:
        if not isinstance(tools, list):
            reasons.append("tools must be a list")
        else:
            for i, tool in enumerate(tools):
                if not isinstance(tool, dict):
                    reasons.append(f"tools[{i}] must be a dict")
                elif "name" not in tool:
                    reasons.append(f"tools[{i}] missing required field 'name'")
    return reasons


# ---------------------------------------------------------------------------
# Context token budget validator
# ---------------------------------------------------------------------------

REQUIRED_CONTEXT_SECTIONS = frozenset({"system", "safety"})


def validate_context_budget(payload: dict[str, Any]) -> list[str]:
    """Validate context assembly and token budget proposals."""
    reasons: list[str] = []
    sections = payload.get("sections")
    if sections is not None:
        if not isinstance(sections, list):
            reasons.append("sections must be a list")
        else:
            for i, sec in enumerate(sections):
                if not isinstance(sec, dict):
                    reasons.append(f"sections[{i}] must be a dict")
                elif "name" not in sec:
                    reasons.append(f"sections[{i}] missing required field 'name'")
    # Validate token budget
    token_budget = payload.get("tokenBudget")
    if token_budget is not None:
        if not isinstance(token_budget, (int, float)) or token_budget <= 0:
            reasons.append("tokenBudget must be a positive number")
    # Check required sections are included
    if sections and isinstance(sections, list):
        section_names = set()
        for sec in sections:
            if isinstance(sec, dict):
                section_names.add(sec.get("name"))
        for req in REQUIRED_CONTEXT_SECTIONS:
            if req not in section_names:
                reasons.append(f"Required context section missing: {req!r}")
    return reasons


# ---------------------------------------------------------------------------
# Memory source validator
# ---------------------------------------------------------------------------

VALID_MEMORY_ACTIONS = frozenset({"recall", "extract", "invalidate", "update"})


def validate_memory_source(payload: dict[str, Any]) -> list[str]:
    """Validate memory recall and extraction proposals."""
    reasons: list[str] = []
    action = payload.get("action")
    if not action:
        return reasons
    if action not in VALID_MEMORY_ACTIONS:
        reasons.append(
            f"Invalid memory action: {action!r}. "
            f"Must be one of {sorted(VALID_MEMORY_ACTIONS)}"
        )
    source_ids = payload.get("sourceIds")
    if source_ids is not None:
        if not isinstance(source_ids, list):
            reasons.append("sourceIds must be a list")
        else:
            for i, sid in enumerate(source_ids):
                if not isinstance(sid, str) or not sid.strip():
                    reasons.append(f"sourceIds[{i}] must be a non-empty string")
    return reasons


# ---------------------------------------------------------------------------
# Retry budget validator
# ---------------------------------------------------------------------------


def validate_retry_budget(payload: dict[str, Any]) -> list[str]:
    """Validate retry budget and failure recovery proposals."""
    reasons: list[str] = []
    max_retries = payload.get("maxRetries")
    if max_retries is not None:
        if not isinstance(max_retries, int) or max_retries < 0:
            reasons.append("maxRetries must be a non-negative integer")
        elif max_retries > 10:
            reasons.append("maxRetries must not exceed 10")
    retry_delay = payload.get("retryDelayMs")
    if retry_delay is not None:
        if not isinstance(retry_delay, (int, float)) or retry_delay < 0:
            reasons.append("retryDelayMs must be a non-negative number")
    strategy = payload.get("strategy")
    if strategy is not None:
        valid_strategies = {"retry", "fallback", "skip", "abort", "ask_user"}
        if strategy not in valid_strategies:
            reasons.append(
                f"Invalid recovery strategy: {strategy!r}. "
                f"Must be one of {sorted(valid_strategies)}"
            )
    return reasons


# ---------------------------------------------------------------------------
# Frontend visibility validator
# ---------------------------------------------------------------------------

VALID_VISIBILITIES = frozenset({"chat", "panel", "trace"})


def validate_frontend_visibility(payload: dict[str, Any]) -> list[str]:
    """Validate event presentation and visibility proposals."""
    reasons: list[str] = []
    visibility = payload.get("visibility")
    if visibility and visibility not in VALID_VISIBILITIES:
        reasons.append(
            f"Invalid visibility: {visibility!r}. "
            f"Must be one of {sorted(VALID_VISIBILITIES)}"
        )
    grouping = payload.get("grouping")
    if grouping is not None:
        if not isinstance(grouping, list):
            reasons.append("grouping must be a list")
        else:
            for i, g in enumerate(grouping):
                if not isinstance(g, dict):
                    reasons.append(f"grouping[{i}] must be a dict")
                elif "label" not in g:
                    reasons.append(f"grouping[{i}] missing required field 'label'")
    return reasons


# ---------------------------------------------------------------------------
# Roadmap edit approval validator
# ---------------------------------------------------------------------------


def validate_roadmap_edit(payload: dict[str, Any]) -> list[str]:
    """Validate roadmap/doc maintenance proposals requiring approval."""
    reasons: list[str] = []
    updates = payload.get("updates")
    if not isinstance(updates, list):
        reasons.append("updates must be a list")
        return reasons
    if len(updates) == 0:
        reasons.append("updates must be non-empty")
        return reasons
    for i, upd in enumerate(updates):
        if not isinstance(upd, dict):
            reasons.append(f"updates[{i}] must be a dict")
            continue
        action = upd.get("action")
        valid_actions = {"check", "add", "remove", "reorder", "update_status"}
        if action and action not in valid_actions:
            reasons.append(
                f"updates[{i}] invalid action: {action!r}. "
                f"Must be one of {sorted(valid_actions)}"
            )
    # Roadmap edits always require approval
    requires_approval = payload.get("requiresApproval", True)
    if not requires_approval:
        reasons.append("Roadmap edits must require approval")
    return reasons


# ---------------------------------------------------------------------------
# P9: Multi-agent write safety validators
# ---------------------------------------------------------------------------


def validate_write_scope_overlap(subtasks: list[dict[str, Any]]) -> list[str]:
    """Detect and reject overlapping write scopes between child tasks."""
    return validate_write_scopes(subtasks)


def validate_patch_in_scope(
    payload: dict[str, Any],
    allowed_scopes: list[str] | None = None,
) -> list[str]:
    """Validate that a patch artifact target is within the task's write scope."""
    reasons: list[str] = []
    target_path = payload.get("targetPath") or payload.get("path")
    if not target_path:
        return reasons  # no target to validate
    if not allowed_scopes:
        reasons.append(f"Patch target {target_path!r} has no allowed write scope")
        return reasons
    target_str = str(target_path)
    in_scope = any(target_str.startswith(str(s).rstrip("/") + "/") or target_str == str(s) for s in allowed_scopes)
    if not in_scope:
        reasons.append(
            f"Patch target {target_str!r} is outside allowed write scopes: {allowed_scopes}"
        )
    return reasons


def detect_patch_conflicts(patches: list[dict[str, Any]]) -> list[str]:
    """Detect conflicting patch targets across multiple patches."""
    reasons: list[str] = []
    if not isinstance(patches, list):
        return reasons
    target_map: dict[str, str] = {}  # path -> producer task id
    for i, patch in enumerate(patches):
        if not isinstance(patch, dict):
            continue
        target = patch.get("targetPath") or patch.get("path")
        producer = patch.get("producerTaskId") or f"patch_{i}"
        if not target:
            continue
        target_str = str(target)
        if target_str in target_map:
            reasons.append(
                f"Conflicting patch target {target_str!r}: "
                f"{target_map[target_str]!r} vs {producer!r}"
            )
        else:
            target_map[target_str] = producer
    return reasons


def validate_reviewer_gate(payload: dict[str, Any]) -> list[str]:
    """Validate that reviewer rejection prevents final merge."""
    reasons: list[str] = []
    review_status = payload.get("reviewStatus")
    if not review_status:
        return reasons
    valid_statuses = {"pending", "approved", "rejected", "changes_requested"}
    if review_status not in valid_statuses:
        reasons.append(
            f"Invalid reviewStatus: {review_status!r}. "
            f"Must be one of {sorted(valid_statuses)}"
        )
    merge_requested = payload.get("mergeRequested", False)
    if merge_requested and review_status in ("rejected", "changes_requested"):
        reasons.append(
            f"Cannot merge when reviewStatus is {review_status!r}"
        )
    return reasons


def validate_skill_availability(payload: dict[str, Any]) -> list[str]:
    """Validate that proposed skills are available in the installed skill set.

    Expects:
    - payload["skills"]: list of skill names proposed
    - payload["installedSkills"]: list/set of available skill names
    """
    reasons: list[str] = []
    proposed = payload.get("skills")
    if not isinstance(proposed, list) or not proposed:
        return reasons
    installed = payload.get("installedSkills")
    if not isinstance(installed, (list, set)):
        return reasons
    installed_set = set(installed)
    for skill in proposed:
        if isinstance(skill, str) and skill not in installed_set:
            reasons.append(f"Skill {skill!r} is not installed")
    return reasons


def validate_skill_root_allowlist(payload: dict[str, Any]) -> list[str]:
    """Validate that skills proposed for root execution are on the root allowlist.

    Some skills should only be available to subagents, not root agents.
    Expects:
    - payload["skills"]: list of skill names proposed
    - payload["rootAllowedSkills"]: list/set of skill names allowed for root
    - payload["role"]: the agent role (default "root")
    """
    reasons: list[str] = []
    role = payload.get("role", "root")
    if role != "root":
        return reasons
    skills = payload.get("skills")
    if not isinstance(skills, list) or not skills:
        return reasons
    allowed = payload.get("rootAllowedSkills")
    if not isinstance(allowed, (list, set)):
        return reasons
    allowed_set = set(allowed)
    for skill in skills:
        if isinstance(skill, str) and skill not in allowed_set:
            reasons.append(
                f"Skill {skill!r} is not allowed for root agent. "
                f"Allowed: {sorted(allowed_set)}"
            )
    return reasons
