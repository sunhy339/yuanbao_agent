"""Runtime validators for LLM proposal records.

Each validator receives a proposal payload and returns a list of rejection
reasons.  An empty list means the proposal passes that validator.
"""

from __future__ import annotations

import fnmatch
import posixpath
import re
from typing import Any

from ..models import ProposalKind
from ..router.types import ExecutionStrategy, Scenario
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
    "routing_strategy": ["strategy"],
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
    "provider_preflight": ["action"],
    "event_presentation": ["grouping"],
    "synthesis_strategy": ["structure"],
    "todo_maintenance": ["updates"],
    "completion_decision": ["is_complete"],
    "product_surface_decision": ["surface_type"],
    "user_takeover": ["state"],
    "tool_recovery": ["action"],
    "budget_convergence": ["action"],
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
    "agent", "task",
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

UNSAFE_TOOLS = frozenset({"agent", "task"}) | UNSAFE_CHILD_TOOLS


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
        tid = st.get("id") or st.get("taskId") or st.get("task_id")
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
        tid = st.get("id") or st.get("taskId") or st.get("task_id")
        deps = st.get("dependencies", [])
        if tid and tid in deps:
            reasons.append(f"subtasks[{i}] has self-dependency: {tid!r}")
    # Detect longer dependency cycles, not just self-references.
    graph: dict[str, list[str]] = {}
    for st in subtasks:
        if not isinstance(st, dict):
            continue
        tid = st.get("id") or st.get("taskId") or st.get("task_id")
        deps = st.get("dependencies", [])
        if tid and isinstance(deps, list):
            graph[str(tid)] = [str(dep) for dep in deps if dep in task_ids]

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str, path: list[str]) -> None:
        if node in visited:
            return
        if node in visiting:
            cycle_start = path.index(node) if node in path else 0
            cycle = path[cycle_start:] + [node]
            reasons.append(f"Dependency cycle detected: {' -> '.join(cycle)}")
            return
        visiting.add(node)
        for dep in graph.get(node, []):
            visit(dep, [*path, dep])
        visiting.remove(node)
        visited.add(node)

    for node in sorted(graph):
        visit(node, [node])
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
    scope_map: dict[str, str] = {}  # normalized scope_path -> task_id
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
            scope_str = _normalize_relative_path(scope)
            if _is_invalid_relative_path(scope_str):
                reasons.append(f"Invalid write scope {scope!r} for {tid!r}")
                continue
            overlapping = next(
                (
                    existing
                    for existing in scope_map
                    if _path_contains(existing, scope_str) or _path_contains(scope_str, existing)
                ),
                None,
            )
            if overlapping is not None:
                reasons.append(
                    f"Overlapping write scope {scope_str!r} between "
                    f"{scope_map[overlapping]!r} and {tid!r}"
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

    # Routing strategy validator
    if kind == "routing_strategy":
        reasons.extend(validate_routing_strategy(payload))

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

    # Provider preflight validator
    if kind == "provider_preflight":
        reasons.extend(validate_provider_preflight(payload))

    # Event presentation validator
    if kind == "event_presentation":
        reasons.extend(validate_frontend_visibility(payload))

    # TODO maintenance validator
    if kind == "todo_maintenance":
        reasons.extend(validate_roadmap_edit(payload))

    # Completion decision validator
    if kind == "completion_decision":
        reasons.extend(validate_completion_decision(payload))

    # Product surface advisor validator
    if kind == "product_surface_decision":
        reasons.extend(validate_product_surface_decision(payload))

    # User takeover validator
    if kind == "user_takeover":
        reasons.extend(validate_user_takeover(payload))

    # Tool/MCP recovery validator
    if kind == "tool_recovery":
        reasons.extend(validate_tool_recovery(payload))

    # Budget convergence validator
    if kind == "budget_convergence":
        reasons.extend(validate_budget_convergence(payload))

    # Skill policy validator
    if kind == "skill_policy":
        reasons.extend(validate_skill_availability(payload))
        reasons.extend(validate_skill_root_allowlist(payload))

    return reasons


def validate_completion_decision(payload: dict[str, Any]) -> list[str]:
    """Validate completion advisor proposals without making them authoritative."""
    reasons: list[str] = []
    if "is_complete" in payload and not isinstance(payload.get("is_complete"), bool):
        reasons.append("is_complete must be a boolean")
    if "verification_sufficient" in payload and not isinstance(payload.get("verification_sufficient"), bool):
        reasons.append("verification_sufficient must be a boolean")
    verification_assessment = payload.get("verification_assessment")
    if verification_assessment is not None and not isinstance(verification_assessment, dict):
        reasons.append("verification_assessment must be an object when provided")
    for field in ("remaining_risks", "blocking_issues", "recommended_verification"):
        value = payload.get(field)
        if value is not None and not isinstance(value, list):
            reasons.append(f"{field} must be a list when provided")
    return reasons


def validate_product_surface_decision(payload: dict[str, Any]) -> list[str]:
    """Validate semantic product-surface advice while keeping it advisory."""
    reasons: list[str] = []
    surface_type = payload.get("surface_type")
    if "surface_type" in payload and (not isinstance(surface_type, str) or not surface_type.strip()):
        reasons.append("surface_type must be a non-empty string")
    for field in ("recommended_verification", "verification_intents", "evidence_requests"):
        value = payload.get(field)
        if value is not None and not isinstance(value, list):
            reasons.append(f"{field} must be a list when provided")
    evidence_requests = payload.get("evidence_requests")
    if isinstance(evidence_requests, list):
        for index, item in enumerate(evidence_requests):
            if not isinstance(item, dict):
                reasons.append(f"evidence_requests[{index}] must be an object")
                continue
            kind = item.get("kind")
            if kind is not None and not isinstance(kind, str):
                reasons.append(f"evidence_requests[{index}].kind must be a string when provided")
            blocking = item.get("blocking")
            if blocking is not None and not isinstance(blocking, bool):
                reasons.append(f"evidence_requests[{index}].blocking must be a boolean when provided")
            satisfied = item.get("satisfied")
            if satisfied is not None and not isinstance(satisfied, bool):
                reasons.append(f"evidence_requests[{index}].satisfied must be a boolean when provided")
            status = item.get("status")
            if status is not None and not isinstance(status, str):
                reasons.append(f"evidence_requests[{index}].status must be a string when provided")
            suggested_command = item.get("suggestedCommand")
            if suggested_command is None:
                suggested_command = item.get("suggested_command")
            if suggested_command is not None and not isinstance(suggested_command, str):
                reasons.append(f"evidence_requests[{index}].suggestedCommand must be a string when provided")
            suggested_commands = item.get("suggestedCommands")
            if suggested_commands is None:
                suggested_commands = item.get("suggested_commands")
            if suggested_commands is not None:
                if not isinstance(suggested_commands, list):
                    reasons.append(f"evidence_requests[{index}].suggestedCommands must be a list when provided")
                else:
                    for command_index, command_item in enumerate(suggested_commands):
                        if isinstance(command_item, str):
                            if not command_item.strip():
                                reasons.append(
                                    f"evidence_requests[{index}].suggestedCommands[{command_index}] must not be empty"
                                )
                        elif isinstance(command_item, dict):
                            command = command_item.get("suggestedCommand") or command_item.get("suggested_command") or command_item.get("command")
                            if not isinstance(command, str) or not command.strip():
                                reasons.append(
                                    f"evidence_requests[{index}].suggestedCommands[{command_index}].command must be a non-empty string"
                                )
                        else:
                            reasons.append(
                                f"evidence_requests[{index}].suggestedCommands[{command_index}] must be a string or object"
                            )
            suggested_tool = item.get("suggestedTool")
            if suggested_tool is None:
                suggested_tool = item.get("suggested_tool")
            if suggested_tool is not None:
                if not isinstance(suggested_tool, dict):
                    reasons.append(f"evidence_requests[{index}].suggestedTool must be an object when provided")
                else:
                    name = suggested_tool.get("name") or suggested_tool.get("toolName")
                    if not isinstance(name, str) or not name.strip():
                        reasons.append(f"evidence_requests[{index}].suggestedTool.name must be a non-empty string")
                    arguments = suggested_tool.get("arguments", {})
                    if arguments is not None and not isinstance(arguments, dict):
                        reasons.append(f"evidence_requests[{index}].suggestedTool.arguments must be an object when provided")
            suggested_tools = item.get("suggestedTools")
            if suggested_tools is None:
                suggested_tools = item.get("suggested_tools")
            if suggested_tools is not None:
                if not isinstance(suggested_tools, list):
                    reasons.append(f"evidence_requests[{index}].suggestedTools must be a list when provided")
                else:
                    for tool_index, tool_item in enumerate(suggested_tools):
                        if not isinstance(tool_item, dict):
                            reasons.append(f"evidence_requests[{index}].suggestedTools[{tool_index}] must be an object")
                            continue
                        name = tool_item.get("name") or tool_item.get("toolName")
                        if not isinstance(name, str) or not name.strip():
                            reasons.append(
                                f"evidence_requests[{index}].suggestedTools[{tool_index}].name must be a non-empty string"
                            )
                        arguments = tool_item.get("arguments", {})
                        if arguments is not None and not isinstance(arguments, dict):
                            reasons.append(
                                f"evidence_requests[{index}].suggestedTools[{tool_index}].arguments must be an object when provided"
                            )
    return reasons


def validate_user_takeover(payload: dict[str, Any]) -> list[str]:
    """Validate LLM-advised user takeover intent without applying side effects."""
    reasons: list[str] = []
    valid_states = {
        "supplement",
        "pause_requested",
        "continue_requested",
        "wrap_up_requested",
        "stop_requested",
        "change_requested",
    }
    state = payload.get("state")
    if state not in valid_states:
        reasons.append(f"Invalid user takeover state: {state!r}")
    for field in ("intent", "reason", "target_goal", "handoff_focus"):
        value = payload.get(field)
        if value is not None and not isinstance(value, str):
            reasons.append(f"{field} must be a string when provided")
    return reasons


def validate_tool_recovery(payload: dict[str, Any]) -> list[str]:
    """Validate advisor-selected tool recovery without executing it."""
    reasons: list[str] = []
    valid_actions = {
        "retry_same",
        "retry_narrower",
        "refresh_mcp_tools",
        "request_permission",
        "use_partial_evidence",
        "fallback_tool",
        "ask_user",
        "skip_with_risk",
        "abort",
    }
    action = payload.get("action")
    if action not in valid_actions:
        reasons.append(f"Invalid tool recovery action: {action!r}")
    for field in ("reason", "userMessage", "risk"):
        value = payload.get(field)
        if value is not None and not isinstance(value, str):
            reasons.append(f"{field} must be a string when provided")
    for field in ("retryWithNarrowerArgs", "usePartialEvidence", "requestPermission", "refreshMcpTools"):
        value = payload.get(field)
        if value is not None and not isinstance(value, bool):
            reasons.append(f"{field} must be a boolean when provided")
    fallback_tool = payload.get("fallbackTool")
    if fallback_tool is not None:
        if not isinstance(fallback_tool, dict):
            reasons.append("fallbackTool must be an object when provided")
        else:
            name = fallback_tool.get("name") or fallback_tool.get("toolName")
            if not isinstance(name, str) or not name.strip():
                reasons.append("fallbackTool.name must be a non-empty string")
            arguments = fallback_tool.get("arguments", {})
            if arguments is not None and not isinstance(arguments, dict):
                reasons.append("fallbackTool.arguments must be an object when provided")
    return reasons


def validate_budget_convergence(payload: dict[str, Any]) -> list[str]:
    """Validate budget-convergence advice without letting it bypass runtime gates."""
    reasons: list[str] = []
    valid_actions = {
        "summarize_partial",
        "pause_for_user",
        "request_more_budget",
        "continue_with_constraints",
        "ask_user",
        "fail",
    }
    action = payload.get("action")
    if action not in valid_actions:
        reasons.append(f"Invalid budget convergence action: {action!r}")
    for field in ("reason", "handoff_focus", "resume_policy", "userMessage"):
        value = payload.get(field)
        if value is not None and not isinstance(value, str):
            reasons.append(f"{field} must be a string when provided")
    next_options = payload.get("next_user_options")
    if next_options is not None and not isinstance(next_options, list):
        reasons.append("next_user_options must be a list when provided")
    constraints = payload.get("constraints")
    if constraints is not None and not isinstance(constraints, (dict, list, str)):
        reasons.append("constraints must be a dict, list, or string when provided")
    return reasons


def validate_provider_preflight(payload: dict[str, Any]) -> list[str]:
    """Validate pre-provider-call advice while keeping runtime actions bounded."""
    reasons: list[str] = []
    valid_actions = {
        "proceed",
        "compact_context",
        "propose_split",
        "ask_user",
        "switch_provider",
        "abort",
    }
    action = payload.get("action")
    if action not in valid_actions:
        reasons.append(f"Invalid provider preflight action: {action!r}")
    risk_level = payload.get("riskLevel")
    if risk_level is not None and risk_level not in VALID_RISK_LEVELS:
        reasons.append(f"Invalid riskLevel: {risk_level!r}. Must be one of {sorted(VALID_RISK_LEVELS)}")
    for field in ("reason", "contextStrategy", "fallbackProviderId", "userMessage"):
        value = payload.get(field)
        if value is not None and not isinstance(value, str):
            reasons.append(f"{field} must be a string when provided")
    if action == "switch_provider":
        fallback_provider_id = payload.get("fallbackProviderId")
        if not isinstance(fallback_provider_id, str) or not fallback_provider_id.strip():
            reasons.append("fallbackProviderId must be a non-empty string when action is switch_provider")
    split = payload.get("splitRecommendation")
    if split is not None and not isinstance(split, (str, dict, list)):
        reasons.append("splitRecommendation must be a string, object, or list when provided")
    if action == "propose_split":
        if isinstance(split, dict):
            split_subtasks = split.get("subtasks")
        elif isinstance(split, list):
            split_subtasks = split
        else:
            split_subtasks = payload.get("subtasks")
        if not isinstance(split_subtasks, list):
            reasons.append("splitRecommendation.subtasks or subtasks must be a list when action is propose_split")
            return reasons
        if len(split_subtasks) < 2:
            reasons.append("splitRecommendation.subtasks must contain at least 2 subtasks")
        if len(split_subtasks) > 10:
            reasons.append("splitRecommendation.subtasks must contain at most 10 subtasks")
        task_ids: set[str] = set()
        for index, item in enumerate(split_subtasks):
            if not isinstance(item, dict):
                reasons.append(f"splitRecommendation.subtasks[{index}] must be an object")
                continue
            subtask_id = item.get("id") or item.get("taskId") or item.get("task_id")
            title = item.get("title")
            description = item.get("description") or item.get("prompt") or item.get("instructions")
            if not isinstance(subtask_id, str) or not subtask_id.strip():
                reasons.append(f"splitRecommendation.subtasks[{index}].id must be a non-empty string")
            elif subtask_id in task_ids:
                reasons.append(f"Duplicate split subtask id: {subtask_id!r}")
            else:
                task_ids.add(subtask_id)
            if not isinstance(title, str) or not title.strip():
                reasons.append(f"splitRecommendation.subtasks[{index}].title must be a non-empty string")
            if not isinstance(description, str) or not description.strip():
                reasons.append(f"splitRecommendation.subtasks[{index}].description must be a non-empty string")
            dependencies = item.get("dependencies", [])
            if dependencies is not None and not isinstance(dependencies, list):
                reasons.append(f"splitRecommendation.subtasks[{index}].dependencies must be a list when provided")
            agent_type = item.get("agentType") or item.get("agent_type")
            normalized_agent_type = agent_type.strip().lower() if isinstance(agent_type, str) else agent_type
            if normalized_agent_type is not None and normalized_agent_type not in {"planner", "worker", "reviewer", "summarizer"}:
                reasons.append(f"splitRecommendation.subtasks[{index}].agentType is invalid: {agent_type!r}")
        reasons.extend(validate_dependency_graph(split_subtasks))
        reasons.extend(validate_write_scopes(split_subtasks))
    return reasons


def validate_routing_strategy(payload: dict[str, Any]) -> list[str]:
    """Validate advisor routing proposals without owning semantic routing."""
    reasons: list[str] = []
    strategy = payload.get("strategy")
    valid_strategies = {item.value for item in ExecutionStrategy}
    if strategy is not None and strategy not in valid_strategies:
        reasons.append(f"Invalid routing strategy: {strategy!r}")
    scenario = payload.get("scenario")
    valid_scenarios = {item.value for item in Scenario}
    if scenario is not None and scenario not in valid_scenarios:
        reasons.append(f"Invalid routing scenario: {scenario!r}")
    continuation = payload.get("tool_continuation")
    camel_continuation = payload.get("toolContinuation")
    if continuation is not None and camel_continuation is not None:
        reasons.append("Provide only one of tool_continuation or toolContinuation")
        return reasons
    continuation = continuation if continuation is not None else camel_continuation
    if continuation is None:
        return reasons
    if not isinstance(continuation, dict):
        return ["tool_continuation must be an object when provided"]
    bool_fields = (
        "allow_tools_after_task_results",
        "allowToolsAfterTaskResults",
        "allow_more_subtasks_after_task_results",
        "allowMoreSubtasksAfterTaskResults",
    )
    for field in bool_fields:
        value = continuation.get(field)
        if value is not None and not isinstance(value, bool):
            reasons.append(f"tool_continuation.{field} must be a boolean when provided")
    max_calls = continuation.get("max_task_tool_calls")
    if max_calls is None:
        max_calls = continuation.get("maxTaskToolCalls")
    if max_calls is not None:
        if not isinstance(max_calls, int) or isinstance(max_calls, bool) or max_calls <= 0 or max_calls > 20:
            reasons.append("tool_continuation.maxTaskToolCalls must be an integer from 1 to 20 when provided")
    rationale = continuation.get("rationale")
    if rationale is not None and not isinstance(rationale, str):
        reasons.append("tool_continuation.rationale must be a string when provided")
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
        valid_strategies = {
            "retry",
            "retry_with_backoff",
            "fallback",
            "compact_or_split_context",
            "fix_provider_credentials",
            "fix_provider_request",
            "fix_provider_api_format",
            "inspect_provider_response",
            "surface_error",
            "skip",
            "abort",
            "ask_user",
            "ask_user_or_change_request",
        }
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
    target_str = _normalize_relative_path(target_path)
    if _is_invalid_relative_path(target_str):
        reasons.append(f"Patch target {target_path!r} is not a safe relative path")
        return reasons
    normalized_scopes = [
        scope
        for scope in (_normalize_relative_path(s) for s in allowed_scopes)
        if not _is_invalid_relative_path(scope)
    ]
    in_scope = any(_path_contains(scope, target_str) for scope in normalized_scopes)
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
    if isinstance(proposed, list):
        proposed_items = list(proposed)
    else:
        proposed_items = []
    skill_id = payload.get("skillId") or payload.get("skill_id")
    if isinstance(skill_id, str) and skill_id.strip():
        proposed_items.append(skill_id.strip())
    if not proposed_items:
        return reasons
    installed = payload.get("installedSkills")
    if not isinstance(installed, (list, set)):
        return reasons
    installed_set = set(installed)
    for skill in proposed_items:
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
    proposed_items = list(skills) if isinstance(skills, list) else []
    skill_id = payload.get("skillId") or payload.get("skill_id")
    if isinstance(skill_id, str) and skill_id.strip():
        proposed_items.append(skill_id.strip())
    if not proposed_items:
        return reasons
    allowed = payload.get("rootAllowedSkills")
    if not isinstance(allowed, (list, set)):
        return reasons
    allowed_set = set(allowed)
    for skill in proposed_items:
        if isinstance(skill, str) and skill not in allowed_set:
            reasons.append(
                f"Skill {skill!r} is not allowed for root agent. "
                f"Allowed: {sorted(allowed_set)}"
            )
    return reasons


_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:")


def _normalize_relative_path(path: object) -> str:
    text = str(path).replace("\\", "/").strip()
    while text.startswith("./"):
        text = text[2:]
    normalized = posixpath.normpath(text)
    return "." if normalized == "" else normalized.rstrip("/")


def _is_invalid_relative_path(path: str) -> bool:
    return (
        not path
        or path.startswith("/")
        or path == ".."
        or path.startswith("../")
        or bool(_WINDOWS_DRIVE_RE.match(path))
    )


def _path_contains(scope: str, target: str) -> bool:
    if any(ch in scope for ch in "*?[]"):
        return fnmatch.fnmatch(target, scope)
    if scope == ".":
        return not _is_invalid_relative_path(target)
    return target == scope or target.startswith(scope.rstrip("/") + "/")
