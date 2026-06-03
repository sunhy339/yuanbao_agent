"""task tool — delegate to subagent service."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from ..policy.permission_engine import PermissionRequest as PermRequest


def _step(label: str, status: str, summary: str) -> dict[str, str]:
    return {"label": label, "status": status, "summary": summary}


def build_task_tool(policy_guard: Any, store: Any, subagent_service: Any | None = None, *, permission_engine: Any | None = None) -> dict[str, Any]:
    def task(params: dict[str, Any]) -> dict[str, Any]:
        return _dispatch_subagent_tool(
            params,
            store=store,
            subagent_service=subagent_service,
            permission_engine=permission_engine,
            tool_name="task",
        )

    return {"handler": task}


def build_agent_tool(policy_guard: Any, store: Any, subagent_service: Any | None = None, *, permission_engine: Any | None = None) -> dict[str, Any]:
    def agent(params: dict[str, Any]) -> dict[str, Any]:
        return _dispatch_subagent_tool(
            normalize_agent_tool_params(params),
            store=store,
            subagent_service=subagent_service,
            permission_engine=permission_engine,
            tool_name="agent",
        )

    return {"handler": agent}


def _dispatch_subagent_tool(
    params: dict[str, Any],
    *,
    store: Any,
    subagent_service: Any | None,
    permission_engine: Any | None,
    tool_name: str,
) -> dict[str, Any]:
    if subagent_service is None:
        raise ValueError(f"{tool_name} tool is not configured")
    prompt = str(params.get("prompt", "")).strip()
    title = str(params.get("title") or params.get("agentType") or params.get("agent_type") or prompt or "subtask").strip()
    steps = [
        _step("prepare", "completed", title[:120]),
    ]

    # PermissionEngine gate for subagent dispatch.
    if permission_engine is not None:
        decision = permission_engine.evaluate(PermRequest(
            capability="subagents",
            tool_name=tool_name,
            context={
                "prompt": prompt[:500],
                "agentType": params.get("agentType") or params.get("agent_type"),
                "untrustedContentSignals": params.get("untrustedContentSignals"),
            },
        ))
        if decision.decision == "deny":
            return {
                "status": "blocked",
                "error": decision.reason,
                "steps": [
                    *steps,
                    _step("permission", "blocked", str(decision.reason)),
                ],
            }
        if decision.decision == "approval_required":
            task_id = str(params.get("taskId") or params.get("task_id") or "").strip()
            if not task_id:
                raise ValueError("taskId is required when subagent approval is needed")
            approval = store.create_approval(
                task_id=task_id,
                kind="subagent_dispatch",
                request={
                    "toolName": tool_name,
                    "prompt": str(params.get("prompt", ""))[:200],
                    "agentType": params.get("agentType") or params.get("agent_type"),
                },
            )
            return {
                "status": "approval_required",
                "approval": approval,
                "steps": [
                    *steps,
                    _step("permission", "blocked", "subagent approval required"),
                ],
            }
        steps.append(_step("permission", "completed", str(decision.decision)))

    steps.append(_step("dispatch", "running", title[:120]))
    result = subagent_service.dispatch(params)
    status = str(result.get("status") or "completed")
    child_id = str(result.get("childTaskId") or result.get("taskId") or "").strip()
    summary = str(result.get("summary") or result.get("resultSummary") or child_id or status).strip()
    steps[-1] = _step("dispatch", "completed" if status == "completed" else status, summary[:180])
    if child_id:
        steps.append(_step("child_task", "completed", child_id))
    return {
        **result,
        "steps": [*steps, *result.get("steps", [])] if isinstance(result.get("steps"), list) else steps,
    }


def normalize_agent_tool_params(params: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(params)
    agent_type = _first_text(normalized, "agent_type", "agentType", "role", default="explorer")
    normalized["agentType"] = agent_type
    normalized["agent_type"] = agent_type
    normalized["title"] = str(normalized.get("title") or agent_type or normalized.get("prompt") or "agent").strip()

    tool_allowlist = _first_present(normalized, "tool_allowlist", "toolAllowlist", "child_tool_allowlist", "childToolAllowlist")
    if tool_allowlist is not None:
        normalized["childToolAllowlist"] = deepcopy(tool_allowlist)
        normalized["child_tool_allowlist"] = deepcopy(tool_allowlist)

    profile = dict(normalized.get("profile")) if isinstance(normalized.get("profile"), dict) else {}
    for source_key, profile_key in (
        ("cwd", "cwd"),
        ("mode", "mode"),
        ("plan_mode_required", "planModeRequired"),
        ("planModeRequired", "planModeRequired"),
    ):
        if source_key in normalized and normalized[source_key] is not None:
            profile[profile_key] = deepcopy(normalized[source_key])
    profile.setdefault("source", "agent_tool")
    normalized["profile"] = profile

    budget = dict(normalized.get("budget")) if isinstance(normalized.get("budget"), dict) else {}
    if tool_allowlist is not None:
        budget["childToolAllowlist"] = deepcopy(tool_allowlist)
    if budget:
        normalized["budget"] = budget
    return normalized


def _first_present(params: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in params and params[key] is not None:
            return params[key]
    return None


def _first_text(params: dict[str, Any], *keys: str, default: str = "") -> str:
    for key in keys:
        value = params.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return default
