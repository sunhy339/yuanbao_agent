"""Planner Contract: dynamic agent profile schema and validation.

P7 of subagent-generation-todolist: Planner Contract.

Defines the schema for LLM planner output and validates dynamic agent profiles
before dispatch. This ensures runtime safety by checking tools, dependencies,
scopes, and risk levels before any child task is created.
"""

from __future__ import annotations

import fnmatch
import posixpath
import re
from typing import Any

from ..services.worker_environment import (
    KNOWN_CHILD_TOOLS,
    UNSAFE_CHILD_TOOLS,
    resolve_tool_alias,
)

# ---------------------------------------------------------------------------
# Dynamic Agent Profile Schema
# ---------------------------------------------------------------------------

# Required fields in a dynamic agent profile
REQUIRED_PROFILE_FIELDS = frozenset({
    "name",
    "baseType",
    "mission",
})

# Allowed base types
VALID_BASE_TYPES = frozenset({
    "explorer",
    "worker",
    "reviewer",
    "verifier",
    "summarizer",
})

# Allowed risk levels
VALID_RISK_LEVELS = frozenset({
    "low",
    "medium",
    "high",
    "critical",
})


# ---------------------------------------------------------------------------
# Profile Validator
# ---------------------------------------------------------------------------


def validate_agent_profile(profile: dict[str, Any]) -> list[str]:
    """Validate a dynamic agent profile.

    Checks:
    - Required fields (name, baseType, mission)
    - baseType is a known type
    - allowedTools (if present) are known and safe
    - ownedScope format (if present)
    - riskLevel (if present) is valid
    - dependencies (if present) reference valid task ids (checked at DAG level)
    - doneCriteria (if present) is non-empty
    """
    reasons: list[str] = []

    # Required fields
    for field in sorted(REQUIRED_PROFILE_FIELDS):
        if field not in profile or not profile[field]:
            reasons.append(f"Agent profile missing required field: {field!r}")

    # baseType validation
    base_type = profile.get("baseType")
    if base_type and base_type not in VALID_BASE_TYPES:
        reasons.append(f"Invalid baseType: {base_type!r}. Must be one of {sorted(VALID_BASE_TYPES)}")

    # Tool validation
    allowed_tools = profile.get("allowedTools")
    if allowed_tools is not None:
        if not isinstance(allowed_tools, list):
            reasons.append("allowedTools must be a list")
        else:
            for tool in allowed_tools:
                resolved = resolve_tool_alias(str(tool))
                if resolved in UNSAFE_CHILD_TOOLS:
                    reasons.append(f"Unsafe tool in profile: {tool!r} (resolved: {resolved!r})")
                elif resolved not in KNOWN_CHILD_TOOLS:
                    reasons.append(f"Unknown tool in profile: {tool!r} (resolved: {resolved!r})")

    # Scope validation
    owned_scope = profile.get("ownedScope")
    if owned_scope is not None:
        if isinstance(owned_scope, str):
            owned_scope = [owned_scope]
        if not isinstance(owned_scope, list):
            reasons.append("ownedScope must be a string or list")
        else:
            for scope in owned_scope:
                if not isinstance(scope, str) or not scope.strip():
                    reasons.append(f"Invalid scope path: {scope!r}")

    # Risk level
    risk = profile.get("riskLevel")
    if risk is not None and risk not in VALID_RISK_LEVELS:
        reasons.append(f"Invalid riskLevel: {risk!r}. Must be one of {sorted(VALID_RISK_LEVELS)}")

    # Done criteria
    done = profile.get("doneCriteria")
    if done is not None:
        if not isinstance(done, list) or len(done) == 0:
            reasons.append("doneCriteria must be a non-empty list")

    # Expected artifacts
    expected_artifacts = profile.get("expectedArtifacts")
    if expected_artifacts is not None:
        if not isinstance(expected_artifacts, list):
            reasons.append("expectedArtifacts must be a list")
        else:
            for j, art in enumerate(expected_artifacts):
                if not isinstance(art, dict):
                    reasons.append(f"expectedArtifacts[{j}] must be a dict")
                elif "kind" not in art:
                    reasons.append(f"expectedArtifacts[{j}] missing required field 'kind'")

    # Handoff notes
    handoff = profile.get("handoffNotes")
    if handoff is not None and not isinstance(handoff, str):
        reasons.append("handoffNotes must be a string")

    # Priority
    priority = profile.get("priority")
    if priority is not None:
        if not isinstance(priority, int) or priority < 0:
            reasons.append("priority must be a non-negative integer")

    # Verification requirements
    verification = profile.get("verificationRequirements")
    if verification is not None:
        if not isinstance(verification, list):
            reasons.append("verificationRequirements must be a list")
        elif len(verification) == 0:
            reasons.append("verificationRequirements must be non-empty if provided")

    # Prompt (optional string with task-specific instructions)
    prompt = profile.get("prompt")
    if prompt is not None and not isinstance(prompt, str):
        reasons.append("prompt must be a string")

    return reasons


# ---------------------------------------------------------------------------
# Planner Output Validator
# ---------------------------------------------------------------------------


def validate_planner_output(output: dict[str, Any]) -> list[str]:
    """Validate the full planner output containing subtasks.

    Expected structure:
    {
        "title": str,
        "subtasks": [
            {
                "name": str,
                "baseType": str,
                "mission": str,
                "dependencies": [str, ...],
                "allowedTools": [str, ...],
                "ownedScope": [str, ...],
                "expectedArtifacts": [str, ...],
                "priority": int,
                ...
            }
        ]
    }
    """
    reasons: list[str] = []

    # Top-level title
    if not output.get("title"):
        reasons.append("Planner output missing 'title'")

    # Subtasks
    subtasks = output.get("subtasks")
    if not isinstance(subtasks, list) or len(subtasks) == 0:
        reasons.append("Planner output must contain non-empty 'subtasks' list")
        return reasons

    task_names: set[str] = set()
    for i, task in enumerate(subtasks):
        if not isinstance(task, dict):
            reasons.append(f"subtasks[{i}] must be a dict")
            continue

        # Validate profile fields
        profile_reasons = validate_agent_profile(task)
        for r in profile_reasons:
            reasons.append(f"subtasks[{i}]: {r}")

        # Track names for uniqueness
        name = task.get("name")
        if name:
            if name in task_names:
                reasons.append(f"subtasks[{i}]: duplicate name {name!r}")
            task_names.add(name)

    # Validate dependency references
    for i, task in enumerate(subtasks):
        if not isinstance(task, dict):
            continue
        deps = task.get("dependencies", [])
        if not isinstance(deps, list):
            continue
        for dep in deps:
            if dep not in task_names:
                reasons.append(f"subtasks[{i}]: references unknown dependency {dep!r}")

    # Detect self-dependencies
    for i, task in enumerate(subtasks):
        if not isinstance(task, dict):
            continue
        name = task.get("name")
        deps = task.get("dependencies", [])
        if name and name in deps:
            reasons.append(f"subtasks[{i}]: self-dependency on {name!r}")

    # Detect overlapping write scopes
    scope_map: dict[str, str] = {}
    for i, task in enumerate(subtasks):
        if not isinstance(task, dict):
            continue
        name = task.get("name") or f"subtask_{i}"
        scopes = task.get("ownedScope")
        if scopes is None:
            continue
        if isinstance(scopes, str):
            scopes = [scopes]
        if not isinstance(scopes, list):
            continue
        for scope in scopes:
            scope_str = _normalize_relative_path(scope)
            if _is_invalid_relative_path(scope_str):
                reasons.append(f"subtasks[{i}]: invalid write scope {scope!r}")
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
                    f"{scope_map[overlapping]!r} and {name!r}"
                )
            else:
                scope_map[scope_str] = name

    # Check high-risk tasks have approval gates
    for i, task in enumerate(subtasks):
        if not isinstance(task, dict):
            continue
        risk = task.get("riskLevel")
        if risk in ("high", "critical"):
            gates = task.get("approvalGates")
            if not isinstance(gates, list) or len(gates) == 0:
                reasons.append(
                    f"subtasks[{i}]: risk level {risk!r} requires approval gates"
                )

    return reasons


# ---------------------------------------------------------------------------
# DAG Scheduler
# ---------------------------------------------------------------------------


def compute_execution_order(subtasks: list[dict[str, Any]]) -> list[list[str]]:
    """Compute execution layers for DAG scheduling.

    Returns a list of layers where each layer contains task names that can run
    concurrently. Layer 0 has no dependencies, layer 1 depends only on layer 0, etc.

    Raises ValueError if the dependency graph has cycles.
    """
    # Build adjacency
    task_names = set()
    for task in subtasks:
        name = task.get("name")
        if name:
            task_names.add(name)

    # Build dependency map: name -> set of deps that exist in task_names
    dep_map: dict[str, set[str]] = {}
    for task in subtasks:
        name = task.get("name")
        if not name:
            continue
        deps = task.get("dependencies", [])
        if isinstance(deps, list):
            dep_map[name] = {d for d in deps if d in task_names}
        else:
            dep_map[name] = set()

    # Topological sort via BFS layers (Kahn's algorithm)
    layers: list[list[str]] = []
    remaining = dict(dep_map)  # copy

    while remaining:
        # Find tasks with no remaining deps
        ready = sorted(name for name, deps in remaining.items() if not deps)
        if not ready:
            # Cycle detected
            raise ValueError(f"Circular dependency detected among: {sorted(remaining.keys())}")
        layers.append(ready)
        # Remove ready tasks from remaining deps
        for name in ready:
            del remaining[name]
        for name in remaining:
            remaining[name] -= set(ready)

    return layers


def compute_blocked_downstream(
    subtasks: list[dict[str, Any]],
    failed_task_name: str,
) -> list[str]:
    """Compute which downstream tasks are blocked by a failed task.

    Returns a list of task names that transitively depend on the failed task.
    """
    # Build reverse adjacency: task -> set of tasks that depend on it
    task_names = set()
    for task in subtasks:
        name = task.get("name")
        if name:
            task_names.add(name)

    reverse_deps: dict[str, set[str]] = {n: set() for n in task_names}
    for task in subtasks:
        name = task.get("name")
        if not name:
            continue
        deps = task.get("dependencies", [])
        if isinstance(deps, list):
            for dep in deps:
                if dep in task_names:
                    reverse_deps[dep].add(name)

    # BFS from failed task
    blocked: list[str] = []
    visited: set[str] = set()
    queue = [failed_task_name]
    while queue:
        current = queue.pop(0)
        for downstream in sorted(reverse_deps.get(current, set())):
            if downstream not in visited:
                visited.add(downstream)
                blocked.append(downstream)
                queue.append(downstream)

    return blocked


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
