"""task tool — delegate to subagent service."""

from __future__ import annotations

from copy import deepcopy
import hashlib
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


def continuation_handle_for_child(
    *,
    child_task_id: Any,
    worker_id: Any,
    title: Any = None,
    agent_type: Any = None,
) -> str:
    """Return a stable model-visible handle for continuing a child agent."""

    child_text = str(child_task_id or "").strip()
    worker_text = str(worker_id or "").strip()
    if not child_text or not worker_text:
        return ""
    label_source = str(title or agent_type or "agent").strip().lower()
    parts: list[str] = []
    current: list[str] = []
    for char in label_source:
        if char.isalnum():
            current.append(char)
            continue
        if current:
            parts.append("".join(current))
            current = []
    if current:
        parts.append("".join(current))
    slug = "-".join(parts)[:32].strip("-") or "agent"
    digest = hashlib.sha256(f"{child_text}:{worker_text}".encode("utf-8")).hexdigest()[:10]
    return f"agent:{slug}-{digest}"


def continuation_for_subagent_result(result: dict[str, Any]) -> dict[str, Any]:
    task = result.get("task") if isinstance(result.get("task"), dict) else {}
    worker = result.get("worker") if isinstance(result.get("worker"), dict) else {}
    subagent = result.get("subagent") if isinstance(result.get("subagent"), dict) else {}
    child_task_id = (
        result.get("childTaskId")
        or result.get("collaborationTaskId")
        or task.get("id")
        or result.get("taskId")
    )
    worker_id = result.get("workerId") or task.get("assignedWorkerId") or worker.get("id")
    title = result.get("title") or task.get("title") or task.get("description")
    agent_type = result.get("agentType") or subagent.get("agentType") or subagent.get("agent_type") or worker.get("role")
    handle = continuation_handle_for_child(
        child_task_id=child_task_id,
        worker_id=worker_id,
        title=title,
        agent_type=agent_type,
    )
    if not handle:
        return {}
    return {
        "to": handle,
        "tool": "send_message",
        "usage": f"send_message(to={handle!r}, message='...')",
    }


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
    tool_name: str = "task",
) -> dict[str, Any]:
    """Project child runner internals into a model/frontend-facing result.

    The collaboration store still owns raw child task ids, worker ids, runtime
    tasks, approval records, and messages. The task/agent tool result should be
    closer to haha-cc's public AgentTool surface: status, agent label, concise
    summary, and typed evidence/artifacts.
    """

    public: dict[str, Any] = {
        "status": status,
        "toolName": tool_name,
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

    continuation = continuation_for_subagent_result(result)
    if continuation:
        public["continuation"] = continuation

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


def build_send_message_tool(
    policy_guard: Any,
    store: Any,
    subagent_service: Any | None = None,
    *,
    permission_engine: Any | None = None,
) -> dict[str, Any]:
    collaboration = getattr(subagent_service, "_collaboration", None)

    def send_message(params: dict[str, Any]) -> dict[str, Any]:
        return _send_message_to_agent(
            params,
            store=store,
            collaboration=collaboration,
            permission_engine=permission_engine,
        )

    return {"handler": send_message}


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
                "toolName": tool_name,
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
                "toolName": tool_name,
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
    return _public_subagent_result(result, title=title, status=status, steps=public_steps, tool_name=tool_name)


def _send_message_to_agent(
    params: dict[str, Any],
    *,
    store: Any,
    collaboration: Any | None,
    permission_engine: Any | None,
) -> dict[str, Any]:
    to = _first_text(params, "to", "agent", "agentId", "agent_id", "recipient", default="")
    message = _first_text(params, "message", "prompt", "content", "body", default="")
    if not to:
        raise ValueError("to is required")
    if not message:
        raise ValueError("message is required")

    if permission_engine is not None:
        decision = permission_engine.evaluate(PermRequest(
            capability="subagents",
            tool_name="send_message",
            context={
                "to": to,
                "message": message[:500],
                "untrustedContentSignals": params.get("untrustedContentSignals"),
            },
        ))
        if decision.decision == "deny":
            return {"status": "blocked", "toolName": "send_message", "error": decision.reason or "send_message denied by permission policy"}

    target = _resolve_agent_message_target(params, store=store, to=to)
    task = target["task"]
    worker = target["worker"]
    session_id = str(task.get("sessionId") or params.get("sessionId") or "").strip()
    coordinator = _ensure_coordinator_worker(
        store,
        session_id=session_id,
        parent_task_id=str(params.get("taskId") or ""),
    )
    kind = _send_message_kind(params.get("kind"))
    payload = {
        "source": "send_message",
        "to": target["handle"],
        "recipient": target["recipient"],
    }
    message_record = store.send_agent_message({
        "senderWorkerId": coordinator["id"],
        "recipientWorkerId": worker["id"],
        "taskId": task["id"],
        "kind": kind,
        "body": message,
        "payload": payload,
    })["message"]

    summary = f"Message delivered to {target['recipient']['name']}"
    public = {
        "status": "delivered",
        "toolName": "send_message",
        "summary": summary,
        "to": target["handle"],
        "recipient": target["recipient"],
        "message": _compact_text(message, 600),
    }

    if collaboration is not None and hasattr(collaboration, "publish_runtime_event"):
        collaboration.publish_runtime_event(
            session_id=session_id,
            task_id=str(task.get("id") or params.get("taskId") or ""),
            event_type="collab.message.sent",
            visibility="panel",
            payload={
                "summary": summary,
                "status": "delivered",
                "message": {
                    "kind": kind,
                    "body": _compact_text(message, 600),
                    "to": target["handle"],
                    "recipient": target["recipient"],
                },
            },
        )
    if isinstance(message_record, dict) and message_record.get("createdAt") is not None:
        public["deliveredAt"] = message_record["createdAt"]
    return public


def _resolve_agent_message_target(params: dict[str, Any], *, store: Any, to: str) -> dict[str, Any]:
    session_id = _first_text(params, "sessionId", "session_id", default="")
    list_params = {"sessionId": session_id} if session_id else {}
    tasks = store.list_collaboration_tasks(list_params).get("tasks", [])
    if not isinstance(tasks, list):
        tasks = []
    for task in tasks:
        if not isinstance(task, dict):
            continue
        worker_id = str(task.get("assignedWorkerId") or "").strip()
        if not worker_id:
            continue
        metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
        agent_type = metadata.get("agentType") or task.get("agentType")
        handle = continuation_handle_for_child(
            child_task_id=task.get("id"),
            worker_id=worker_id,
            title=task.get("title") or task.get("description"),
            agent_type=agent_type,
        )
        if to not in {handle, str(task.get("id") or ""), worker_id}:
            continue
        worker = store.get_agent_worker({"workerId": worker_id}).get("worker")
        if not isinstance(worker, dict):
            raise ValueError(f"Agent worker not found for {to}")
        return {
            "handle": handle or to,
            "task": task,
            "worker": worker,
            "recipient": _public_recipient(task, worker),
        }
    raise ValueError(f"Agent continuation target not found: {to}")


def _public_recipient(task: dict[str, Any], worker: dict[str, Any]) -> dict[str, Any]:
    metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
    name = str(worker.get("name") or metadata.get("agentName") or task.get("title") or "Agent").strip()
    role = str(worker.get("role") or metadata.get("agentType") or "agent").strip()
    recipient = {
        "name": name,
        "role": role,
        "taskTitle": task.get("title"),
        "status": task.get("status"),
    }
    return {key: value for key, value in recipient.items() if value not in (None, "", [], {})}


def _ensure_coordinator_worker(store: Any, *, session_id: str, parent_task_id: str) -> dict[str, Any]:
    source = session_id or parent_task_id or "default"
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()[:12]
    return store.upsert_agent_worker({
        "workerId": f"agent_coordinator_{digest}",
        "name": "Coordinator",
        "role": "coordinator",
        "status": "idle",
        "capabilities": ["coordination", "collaboration"],
        "metadata": {
            "sessionId": session_id,
            "parentRuntimeTaskId": parent_task_id,
            "source": "send_message",
        },
    })["worker"]


def _send_message_kind(value: Any) -> str:
    text = str(value or "handoff").strip()
    if text in {"note", "handoff", "broadcast", "result", "system"}:
        return text
    return "handoff"


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
