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

    return reasons
