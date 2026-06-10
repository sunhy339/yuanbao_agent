"""task tool — delegate to subagent service."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from ..policy.permission_engine import PermissionRequest as PermRequest

_PUBLIC_SUBAGENT_RESULT_KEYS = {
    "status",
    "summary",
    "resultSummary",
    "changedFiles",
    "commands",
    "verification",
    "testsRun",
    "artifactIds",
    "artifacts",
    "error",
}


def _step(label: str, status: str, summary: str) -> dict[str, str]:
    return {"label": label, "status": status, "summary": summary}


def _public_child_step_summary(*, result: dict[str, Any], title: str, status: str) -> str:
    for value in (
        result.get("summary"),
        result.get("resultSummary"),
        title,
        result.get("agentType"),
        result.get("agent_type"),
        status,
    ):
        text = str(value or "").strip()
        if text:
            return text[:180]
    return "Child task finished"


def _compact_text(value: Any, limit: int = 600) -> Any:
    if not isinstance(value, str):
        return value
    text = value.strip()
    if len(text) <= limit:
        return text
    head = max(0, limit // 2)
    tail = max(0, limit - head)
    return {
        "head": text[:head],
        "tail": text[-tail:] if tail else "",
        "chars": len(text),
        "omittedChars": max(0, len(text) - limit),
        "truncated": True,
    }


def _public_nested_result(value: Any, *, depth: int = 0) -> Any:
    if isinstance(value, str):
        return _compact_text(value)
    if isinstance(value, list):
        return [_public_nested_result(item, depth=depth + 1) for item in value[:12]]
    if not isinstance(value, dict):
        return value
    if depth >= 3:
        return {"omitted": True, "type": "object", "keys": len(value)}
    public: dict[str, Any] = {}
    for key, item in value.items():
        key_text = str(key)
        if key_text in {
            "approval",
            "approvalId",
            "approval_id",
            "childTaskId",
            "collaborationTaskId",
            "id",
            "messageIds",
            "providerRequest",
            "raw",
            "requestJson",
            "runtimeTask",
            "runtimeTaskId",
            "senderWorkerId",
            "task",
            "taskId",
            "worker",
            "workerId",
        }:
            continue
        if key_text.startswith("_") or item in (None, "", [], {}):
            continue
        public[key_text] = _public_nested_result(item, depth=depth + 1)
    return public


def _public_subagent_result(
    result: dict[str, Any],
    *,
    title: str,
    status: str,
    steps: list[dict[str, str]],
) -> dict[str, Any]:
    """Project child runner internals into a model/frontend-facing result.

    The collaboration store still owns raw child task ids, worker ids, runtime
    tasks, approval records, and messages. The task/agent tool result should be
    closer to haha-cc's public AgentTool surface: status, agent label, concise
    summary, and typed evidence/artifacts.
    """

    public: dict[str, Any] = {
        "status": status,
        "summary": _compact_text(result.get("summary") or result.get("resultSummary") or status, 600),
        "title": title,
        "steps": steps,
    }

    subagent = result.get("subagent")
    if isinstance(subagent, dict):
        agent_type = subagent.get("agentType") or subagent.get("agent_type")
        if agent_type:
            public["agentType"] = _compact_text(str(agent_type), 160)

    task = result.get("task")
    if isinstance(task, dict):
        task_title = task.get("title")
        task_status = task.get("status")
        if task_title:
            public["taskTitle"] = _compact_text(str(task_title), 240)
        if task_status:
            public["taskStatus"] = str(task_status)

    structured = result.get("result") if isinstance(result.get("result"), dict) else {}
    for key in _PUBLIC_SUBAGENT_RESULT_KEYS:
        value = result.get(key)
        if value in (None, "", [], {}) and isinstance(structured, dict):
            value = structured.get(key)
        if value in (None, "", [], {}):
            continue
        public[key] = _public_nested_result(value)

    approval = result.get("approval")
    if isinstance(approval, dict):
        public["approvalKind"] = str(approval.get("kind") or "approval")
        public["approvalStatus"] = str(approval.get("decision") or "waiting")

    error = result.get("error")
    if isinstance(error, dict):
        public["error"] = {
            key: _compact_text(error.get(key), 500)
            for key in ("code", "message", "type", "retryable")
            if error.get(key) not in (None, "", [], {})
        }
    elif isinstance(error, str) and error.strip():
        public["error"] = _compact_text(error, 500)

    return {key: value for key, value in public.items() if value not in (None, "", [], {})}


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
    title = str(
        params.get("description")
        or params.get("title")
        or params.get("agentType")
        or params.get("agent_type")
        or params.get("subagent_type")
        or prompt
        or "subtask"
    ).strip()
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
        steps.append(_step("child_task", "completed", _public_child_step_summary(result=result, title=title, status=status)))
    public_steps = [*steps, *result.get("steps", [])] if isinstance(result.get("steps"), list) else steps
    return _public_subagent_result(result, title=title, status=status, steps=public_steps)


def normalize_agent_tool_params(params: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(params)
    agent_type = _first_text(normalized, "subagent_type", "agent_type", "agentType", "role", default="explorer")
    normalized["agentType"] = agent_type
    normalized["agent_type"] = agent_type
    normalized["subagent_type"] = agent_type
    normalized["title"] = str(
        normalized.get("description")
        or normalized.get("title")
        or agent_type
        or normalized.get("prompt")
        or "agent"
    ).strip()

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
