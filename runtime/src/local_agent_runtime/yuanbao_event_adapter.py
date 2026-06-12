from __future__ import annotations

from typing import Any

from .models import RuntimeEvent


_DIRECT_EVENT_TYPES = {
    "status",
    "content_start",
    "content_delta",
    "tool_use_complete",
    "tool_result",
    "permission_request",
    "computer_use_permission_request",
    "message_complete",
    "thinking",
    "api_retry",
    "system_notification",
}

_CHAT_COMPAT_ALIAS_EVENT_TYPES = {
    "message.delta",
    "message.completed",
    "assistant.message.completed",
}

_SYSTEM_NOTIFICATION_EVENT_TYPES = {
    "init",
    "compact_boundary",
    "compact_summary",
    "background_task",
    "session_state_changed",
    "task_started",
}

_PROGRESS_NOTIFICATION_EVENT_TYPES = {
    "tool.progress",
    "tool.output",
    "command.output",
}

_TASK_PROGRESS_EVENT_TYPES = {
    "tool.progress",
    "tool.output",
    "command.output",
}

_TASK_STARTED_STATUSES = {"queued", "starting", "started", "running", "active", "in_progress"}
_CHAT_STATUS_STATES = {"idle", "thinking", "compacting", "tool_executing", "streaming", "permission_pending"}
_SYSTEM_NOTIFICATION_SUBTYPES = {
    "init",
    "compact_summary",
    "compact_boundary",
    "memory_saved",
    "task_started",
    "task_progress",
    "session_state_changed",
}

_TOOL_PRESENTATION_FIELDS = {
    "target",
    "inputSummary",
    "displayTitle",
    "displaySummary",
    "displayTarget",
    "displayKind",
    "toolCategory",
    "toolPhaseId",
    "toolPhaseLabel",
    "toolSemanticParentId",
    "toolSemanticParentLabel",
    "toolGroupId",
    "toolIndex",
    "toolTotal",
    "toolOperationId",
    "toolOperationLabel",
}

_SERVER_MESSAGE_FIELDS: dict[str, set[str]] = {
    "content_start": {
        "type",
        "blockType",
        "toolName",
        "toolUseId",
        "parentToolUseId",
        "mediaType",
        *_TOOL_PRESENTATION_FIELDS,
    },
    "content_delta": {
        "type",
        "text",
        "toolInput",
        "toolOutput",
        "outputStream",
        "toolName",
        "toolUseId",
        "parentToolUseId",
        "imageData",
        "mediaType",
        "sourceType",
        *_TOOL_PRESENTATION_FIELDS,
    },
    "tool_use_complete": {
        "type",
        "toolName",
        "toolUseId",
        "input",
        "parentToolUseId",
        *_TOOL_PRESENTATION_FIELDS,
    },
    "tool_result": {
        "type",
        "toolUseId",
        "toolName",
        "content",
        "isError",
        "parentToolUseId",
        "resultSummary",
        "resultPreview",
        "durationMs",
        *_TOOL_PRESENTATION_FIELDS,
    },
    "permission_request": {
        "type",
        "requestId",
        "toolName",
        "toolUseId",
        "input",
        "description",
        "previewRows",
        "previewSections",
        "resolved",
        "decision",
        "status",
        "target",
        "inputSummary",
        "filesChanged",
        "changedPaths",
        "diffText",
        *_TOOL_PRESENTATION_FIELDS,
    },
    "computer_use_permission_request": {"type", "requestId", "request"},
    "message_complete": {"type", "usage"},
    "thinking": {"type", "text", "source"},
    "status": {"type", "state", "verb", "elapsed", "tokens"},
    "api_retry": {
        "type",
        "attempt",
        "maxRetries",
        "retryDelayMs",
        "errorStatus",
        "errorType",
        "errorMessage",
    },
    "error": {"type", "message", "code", "retryable", "businessErrorCode"},
    "system_notification": {"type", "subtype", "message", "data"},
    "connected": {"type", "sessionId"},
    "pong": {"type"},
    "team_update": {"type", "teamName", "members"},
    "team_created": {"type", "teamName"},
    "team_deleted": {"type", "teamName"},
    "task_update": {"type", "taskId", "taskLabel", "status", "progress"},
    "session_title_updated": {"type", "sessionId", "title"},
}

_SERVER_MESSAGE_REQUIRED_FIELDS: dict[str, set[str]] = {
    "connected": {"type", "sessionId"},
    "content_start": {"type", "blockType"},
    "tool_use_complete": {"type", "toolName", "toolUseId", "input"},
    "tool_result": {"type", "toolUseId", "content", "isError"},
    "permission_request": {"type", "requestId", "toolName", "input"},
    "computer_use_permission_request": {"type", "requestId", "request"},
    "message_complete": {"type", "usage"},
    "thinking": {"type", "text"},
    "status": {"type", "state"},
    "api_retry": {"type", "attempt", "maxRetries", "retryDelayMs", "errorStatus"},
    "error": {"type", "message", "code"},
    "system_notification": {"type", "subtype"},
    "pong": {"type"},
    "team_update": {"type", "teamName", "members"},
    "team_created": {"type", "teamName"},
    "team_deleted": {"type", "teamName"},
    "task_update": {"type", "taskId", "status"},
    "session_title_updated": {"type", "sessionId", "title"},
}


def to_yuanbao_server_message(event: RuntimeEvent) -> dict[str, Any] | None:
    """Return the Yuanbao flat ServerMessage for compatible runtime events."""

    if event.visibility in {"panel", "trace"}:
        return None

    payload = event.payload if isinstance(event.payload, dict) else {}
    message: dict[str, Any] | None = None
    if event.type in _CHAT_COMPAT_ALIAS_EVENT_TYPES and not _is_chat_compat_payload(payload):
        return None
    if event.type == "system_notification":
        message = _system_notification_message(event.type, payload)
    elif event.type in _DIRECT_EVENT_TYPES:
        message = _flatten_payload(event.type, payload)
        if event.type == "message_complete":
            message["usage"] = normalize_yuanbao_usage(payload.get("usage") or _raw_usage(payload))
    elif event.type in {"connected", "pong"}:
        message = _flatten_payload(event.type, payload)
        if event.type == "connected" and not message.get("sessionId"):
            message["sessionId"] = event.session_id
    elif event.type == "message.delta":
        if not _is_chat_compat_payload(payload):
            return None
        delta = _delta_text_value(payload.get("delta"), payload.get("text"))
        if delta:
            message = {"type": "content_delta", "text": delta}
    elif event.type in {"message.completed", "assistant.message.completed"}:
        if not _is_chat_compat_payload(payload):
            return None
        message = {
            "type": "message_complete",
            "usage": normalize_yuanbao_usage(payload.get("usage") or _raw_usage(payload)),
        }
    elif event.type == "message.failed":
        message = _error_message(event, payload)
    elif event.type == "session.updated":
        title = payload.get("title")
        session_id = payload.get("sessionId") or event.session_id
        if isinstance(title, str) and title and _field_changed(payload, "title"):
            message = {"type": "session_title_updated", "sessionId": session_id, "title": title}
    elif event.type.startswith("task."):
        message = _task_update_message(event, payload)
    elif event.type.startswith("collab."):
        message = _team_message(event.type, payload)
    elif event.type in _SYSTEM_NOTIFICATION_EVENT_TYPES:
        message = _system_notification_message(event.type, payload)

    return _server_message_shape(message) if message is not None else None


def should_emit_yuanbao_server_message(event: RuntimeEvent, *, mode: str = "live") -> bool:
    """Return whether a runtime event should project to a flat chat ServerMessage.

    Live websocket delivery and replay/history loading must share the same
    boundary. The only intended difference is the explicit bridge suppression
    flag used for each mode.
    """

    if event.visibility in {"panel", "trace"}:
        return False
    payload = event.payload if isinstance(event.payload, dict) else {}
    bridge = payload.get("_bridge")
    bridge_payload = bridge if isinstance(bridge, dict) else {}
    normalized_mode = str(mode or "live").strip().lower()
    if normalized_mode == "live":
        if event.type == "message.completed" and payload.get("_chatCompat") is True:
            return False
        if event.type == "message.delta" and payload.get("_chatCompat") is True:
            return True
        return bridge_payload.get("suppressRealtimeFlat") is not True
    if normalized_mode == "replay":
        return bridge_payload.get("suppressChatReplay") is not True
    return bridge_payload.get("suppressRealtimeFlat") is not True


def yuanbao_message_from_event_payload(payload: Any) -> dict[str, Any] | None:
    """Extract the flat ServerMessage from a runtime event envelope payload."""

    if not isinstance(payload, dict):
        return None
    message = payload.get("yuanbao")
    return message if isinstance(message, dict) else None


def to_yuanbao_output_frames(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Return stdout/adapter frames for one runtime event envelope payload."""

    frames: list[dict[str, Any]] = []
    yuanbao = yuanbao_message_from_event_payload(payload)
    if yuanbao is not None:
        frames.append(
            {
                "kind": "yuanbao_message",
                "payload": yuanbao,
            }
        )
    return frames


def collect_yuanbao_server_messages(
    events: Any,
    *,
    after_seq: int = 0,
) -> dict[str, Any]:
    """Collect flat ServerMessages from stored event envelopes."""

    messages: list[dict[str, Any]] = []
    last_seq = int(after_seq)
    if not isinstance(events, list):
        return {
            "messages": messages,
            "lastSeq": last_seq,
        }

    for event in events:
        if not isinstance(event, dict):
            continue
        sequence = event.get("sequence")
        if isinstance(sequence, (int, float)) and not isinstance(sequence, bool):
            last_seq = max(last_seq, int(sequence))
        message = yuanbao_message_from_event_payload(event)
        if message is not None:
            messages.append(message)
    return {
        "messages": messages,
        "lastSeq": last_seq,
    }


def normalize_yuanbao_usage(usage: Any) -> dict[str, int]:
    if not isinstance(usage, dict):
        return {
            "input_tokens": 0,
            "output_tokens": 0,
        }
    input_tokens = _int_value(
        usage.get("input_tokens"),
        usage.get("inputTokens"),
        usage.get("prompt_tokens"),
        usage.get("promptTokens"),
    )
    output_tokens = _int_value(
        usage.get("output_tokens"),
        usage.get("outputTokens"),
        usage.get("completion_tokens"),
        usage.get("completionTokens"),
    )
    if input_tokens == 0 and output_tokens == 0:
        total_tokens = _int_value(usage.get("total_tokens"), usage.get("totalTokens"))
        input_tokens = total_tokens
    result = {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
    }
    cache_read = _cache_read_tokens(usage)
    if cache_read is not None:
        result["cache_read_tokens"] = cache_read
    cache_creation = _int_or_none(
        usage.get("cache_creation_tokens"),
        usage.get("cacheCreationTokens"),
        usage.get("cache_creation_input_tokens"),
        usage.get("cacheCreationInputTokens"),
        usage.get("cache_write_tokens"),
        usage.get("cacheWriteTokens"),
        _nested_int(
            usage,
            (
                "input_tokens_details",
                "inputTokensDetails",
                "prompt_tokens_details",
                "promptTokensDetails",
            ),
            (
                "cache_creation_tokens",
                "cacheCreationTokens",
                "cache_creation_input_tokens",
                "cacheCreationInputTokens",
            ),
        ),
    )
    if cache_creation is not None:
        result["cache_creation_tokens"] = cache_creation
    return result


def _flatten_payload(event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    message = {"type": event_type}
    message.update({key: value for key, value in payload.items() if not str(key).startswith("_")})
    return _server_message_filter(message)


def _server_message_filter(message: dict[str, Any]) -> dict[str, Any]:
    event_type = message.get("type")
    allowed = _SERVER_MESSAGE_FIELDS.get(str(event_type))
    if allowed is None:
        return message
    return {key: value for key, value in message.items() if key in allowed}


def _server_message_shape(message: dict[str, Any]) -> dict[str, Any] | None:
    shaped = _server_message_filter(message)
    event_type = shaped.get("type")
    required = _SERVER_MESSAGE_REQUIRED_FIELDS.get(str(event_type), {"type"})
    for key in required:
        if key not in shaped:
            return None
        if shaped[key] is None and key not in {"content", "errorStatus", "input"}:
            return None
        if isinstance(shaped[key], str) and not shaped[key]:
            return None
    if event_type == "content_delta" and ("toolInput" in shaped or "toolOutput" in shaped) and not shaped.get("toolUseId"):
        shaped.pop("toolInput", None)
        shaped.pop("toolOutput", None)
        shaped.pop("outputStream", None)
        for key in _TOOL_PRESENTATION_FIELDS:
            shaped.pop(key, None)
    if event_type == "content_delta" and not any(key in shaped for key in ("text", "toolInput", "toolOutput")):
        return None
    if event_type == "status" and shaped.get("state") not in _CHAT_STATUS_STATES:
        return None
    return shaped


def _raw_usage(payload: dict[str, Any]) -> Any:
    raw = payload.get("raw")
    if isinstance(raw, dict):
        return raw.get("usage")
    return None


def _is_chat_compat_payload(payload: dict[str, Any]) -> bool:
    return payload.get("_chatCompat") is True


def _delta_text_value(*values: Any) -> str:
    for value in values:
        if isinstance(value, str) and value:
            return value
    return ""


def _error_message(event: RuntimeEvent, payload: dict[str, Any]) -> dict[str, Any]:
    error = payload.get("error")
    error_payload = error if isinstance(error, dict) else {}
    message = _string_value(
        payload.get("content"),
        payload.get("message"),
        payload.get("resultSummary"),
        payload.get("summary"),
        payload.get("detail"),
        payload.get("errorMessage"),
        error_payload.get("message"),
        error if isinstance(error, str) else None,
    )
    code = _string_value(
        payload.get("errorCode"),
        payload.get("code"),
        error_payload.get("code"),
        event.type.replace(".", "_").upper(),
    )
    result: dict[str, Any] = {
        "type": "error",
        "message": message,
        "code": code,
    }
    retryable = payload.get("retryable")
    if not isinstance(retryable, bool):
        retryable = error_payload.get("retryable")
    if isinstance(retryable, bool):
        result["retryable"] = retryable
    business_error_code = _string_value(
        payload.get("businessErrorCode"),
        payload.get("business_error_code"),
        error_payload.get("businessErrorCode"),
        error_payload.get("business_error_code"),
    )
    if business_error_code:
        result["businessErrorCode"] = business_error_code
    return result


def _task_update_message(event: RuntimeEvent, payload: dict[str, Any]) -> dict[str, Any]:
    if not _should_emit_task_update(event, payload):
        return {}
    status = payload.get("status")
    if not status:
        status = event.type.removeprefix("task.")
    task_label = _string_value(
        payload.get("displayTitle"),
        payload.get("title"),
        payload.get("goal"),
        payload.get("currentStep"),
        payload.get("summary"),
        payload.get("resultSummary"),
    )
    progress = (
        payload.get("currentStep")
        or payload.get("detail")
        or payload.get("summary")
        or payload.get("resultSummary")
        or payload.get("goal")
        or payload.get("title")
    )
    message: dict[str, Any] = {
        "type": "task_update",
        "taskId": _public_task_key(payload, event),
        "status": str(status),
    }
    if task_label:
        message["taskLabel"] = _truncate_text(task_label, 160)
    if progress:
        message["progress"] = _truncate_text(str(progress), 500)
    return message


def _public_task_key(payload: dict[str, Any], event: RuntimeEvent) -> str:
    label = _string_value(
        payload.get("displayTitle"),
        payload.get("title"),
        payload.get("goal"),
        payload.get("currentStep"),
        payload.get("summary"),
        payload.get("resultSummary"),
    )
    if label:
        return _stable_public_id(label)
    status = _string_value(payload.get("status"), event.type.removeprefix("task."))
    return _stable_public_id(f"task:{status or 'update'}")


def _should_emit_task_update(event: RuntimeEvent, payload: dict[str, Any]) -> bool:
    if event.type in {"task.routing.decided", "runtime.context.prepared"}:
        return False
    if event.type in {"task.failed", "task.cancelled"}:
        return True
    if event.type == "task.created":
        return _is_collaboration_child_task_event(event, payload)
    if event.type == "task.updated":
        return _is_collaboration_child_task_event(event, payload)
    return False


def _is_collaboration_child_task_event(event: RuntimeEvent, payload: dict[str, Any]) -> bool:
    if payload.get("source") == "collaboration" or payload.get("taskKind") == "collaboration_child":
        return True
    task_id = _string_value(payload.get("taskId"), payload.get("task_id"), event.task_id)
    return task_id.startswith("ctask_")


def _task_update_has_user_visible_progress(payload: dict[str, Any]) -> bool:
    for key in (
        "currentStep",
        "detail",
        "summary",
        "resultSummary",
        "changedFiles",
        "commands",
        "verification",
        "progress",
        "message",
    ):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return True
        if isinstance(value, (list, tuple, set)) and len(value) > 0:
            return True
        if isinstance(value, dict) and len(value) > 0:
            return True
    plan = payload.get("plan")
    return isinstance(plan, list) and any(isinstance(item, dict) and item.get("status") for item in plan)


def _team_message(event_type: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    team_name = _team_name(payload)
    if event_type == "collab.team.created":
        return {
            "type": "team_created",
            "teamName": team_name,
        }
    if event_type == "collab.team.deleted":
        return {
            "type": "team_deleted",
            "teamName": team_name,
        }
    if (
        event_type == "collab.message.sent"
        or event_type.startswith("collab.task.")
        or event_type.startswith("collab.worker.")
    ):
        return {
            "type": "team_update",
            "teamName": team_name,
            "members": _team_members(payload),
        }
    return None


def _team_name(payload: dict[str, Any]) -> str:
    for key in ("teamName", "team_name", "name"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    team = payload.get("team")
    if isinstance(team, dict):
        value = team.get("teamName") or team.get("team_name") or team.get("name") or team.get("sessionId")
        if isinstance(value, str) and value.strip():
            return value.strip()
    task = payload.get("task")
    if isinstance(task, dict):
        value = task.get("teamName") or task.get("team") or task.get("parentTaskId") or task.get("sessionId")
        if isinstance(value, str) and value.strip():
            return value.strip()
    worker = payload.get("worker")
    if isinstance(worker, dict):
        value = worker.get("teamName") or worker.get("team") or worker.get("role")
        if isinstance(value, str) and value.strip():
            return value.strip()
    message = payload.get("message")
    if isinstance(message, dict):
        value = message.get("teamName") or message.get("team") or message.get("taskId")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "default"


def _team_members(payload: dict[str, Any]) -> list[dict[str, Any]]:
    team = payload.get("team")
    if isinstance(team, dict):
        members = _team_members_from_snapshot(team, payload)
        if members:
            return members
    workers = payload.get("workers")
    if isinstance(workers, list):
        return [
            member
            for worker in workers
            if isinstance(worker, dict)
            for member in (_team_member_from_worker(worker),)
            if member
        ]
    worker = payload.get("worker")
    if isinstance(worker, dict):
        member = _team_member_from_worker(worker)
        return [member] if member else []
    message = payload.get("message")
    if isinstance(message, dict):
        member = _team_member_from_message(message)
        return [member] if member else []
    budget = payload.get("budget")
    if isinstance(budget, dict):
        member = _team_member_from_budget(payload, budget)
        return [member] if member else []
    task = payload.get("task")
    if isinstance(task, dict):
        member = _team_member_from_task(task)
        return [member] if member else []
    return []


def _team_members_from_snapshot(team: dict[str, Any], payload: dict[str, Any]) -> list[dict[str, Any]]:
    message = payload.get("message") if isinstance(payload.get("message"), dict) else None
    raw_members = team.get("members")
    if message is None and isinstance(raw_members, list) and raw_members:
        return [
            member
            for raw_member in raw_members
            if isinstance(raw_member, dict)
            for member in (_team_member_shape(raw_member),)
            if member
        ]

    workers = [worker for worker in team.get("workers", []) if isinstance(worker, dict)] if isinstance(team.get("workers"), list) else []
    tasks = [task for task in team.get("tasks", []) if isinstance(task, dict)] if isinstance(team.get("tasks"), list) else []
    worker_by_id = {
        str(worker.get("id") or worker.get("workerId")): worker
        for worker in workers
        if worker.get("id") or worker.get("workerId")
    }

    members: list[dict[str, Any]] = []
    assigned_worker_ids: set[str] = set()
    for task in tasks:
        worker_id = _string_value(task.get("assignedWorkerId"), task.get("workerId"))
        worker = worker_by_id.get(worker_id or "")
        member = _team_member_from_task(task, worker=worker, message=message)
        if member:
            members.append(member)
        if worker_id:
            assigned_worker_ids.add(worker_id)

    for worker in workers:
        worker_id = _string_value(worker.get("id"), worker.get("workerId"))
        if worker_id and worker_id in assigned_worker_ids:
            continue
        member = _team_member_from_worker(worker)
        if member:
            members.append(member)

    return _dedupe_team_members(members)


def _team_member_from_task(
    task: dict[str, Any],
    *,
    worker: dict[str, Any] | None = None,
    message: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
    agent_id = _string_value(
        worker.get("name") if isinstance(worker, dict) else None,
        worker.get("role") if isinstance(worker, dict) else None,
        metadata.get("agentName"),
        metadata.get("agentType"),
        task.get("agentName"),
        task.get("title"),
        task.get("role"),
    )
    role = _string_value(
        worker.get("role") if isinstance(worker, dict) else None,
        worker.get("agentType") if isinstance(worker, dict) else None,
        metadata.get("agentType"),
        task.get("agentType"),
        task.get("role"),
        "worker",
    )
    if not agent_id or not role:
        return None
    member: dict[str, Any] = {
        "agentId": agent_id,
        "role": role,
        "status": _team_status(task.get("status")),
    }
    current_task = _task_current_task(task, message=message)
    if current_task:
        member["currentTask"] = current_task
    return _team_member_shape(member)


def _team_member_from_worker(worker: dict[str, Any]) -> dict[str, Any] | None:
    member = {
        "agentId": str(worker.get("name") or worker.get("role") or worker.get("agentType") or "worker"),
        "role": str(worker.get("role") or worker.get("agentType") or "worker"),
        "status": _team_status(worker.get("status")),
    }
    current_task = worker.get("currentTask")
    if current_task is not None:
        member["currentTask"] = str(current_task)
    return _team_member_shape(member)


def _team_member_from_message(message: dict[str, Any]) -> dict[str, Any] | None:
    return _team_member_shape({
        "agentId": str(message.get("senderName") or message.get("senderRole") or message.get("role") or "worker"),
        "role": str(message.get("senderRole") or message.get("role") or message.get("kind") or "worker"),
        "status": "running",
        "currentTask": message.get("body"),
    })


def _team_member_from_budget(payload: dict[str, Any], budget: dict[str, Any]) -> dict[str, Any] | None:
    return _team_member_shape({
        "agentId": str(budget.get("agentName") or budget.get("role") or budget.get("agentType") or "worker"),
        "role": str(budget.get("role") or budget.get("agentType") or "worker"),
        "status": "running",
        "currentTask": f"{payload.get('dimension') or 'budget'} budget consumed {payload.get('consumed') or 0}",
    })


def _task_current_task(task: dict[str, Any], *, message: dict[str, Any] | None = None) -> str | None:
    if isinstance(message, dict):
        message_task_id = _string_value(message.get("taskId"))
        task_id = _string_value(task.get("id"))
        body = _string_value(message.get("body"))
        if body and (not message_task_id or message_task_id == task_id):
            return body
    result = task.get("result")
    if isinstance(result, dict):
        summary = _string_value(result.get("summary"), result.get("resultSummary"), result.get("message"))
        if summary:
            return summary
    error = task.get("error")
    if isinstance(error, dict):
        message_text = _string_value(error.get("message"), error.get("summary"), error.get("detail"))
        if message_text:
            return message_text
    return _string_value(task.get("title"), task.get("description"))


def _team_member_shape(member: dict[str, Any]) -> dict[str, Any] | None:
    agent_id = _string_value(member.get("agentId"), member.get("agent_id"))
    role = _string_value(member.get("role"), member.get("agentType"), member.get("name"))
    if not agent_id or not role:
        return None
    shaped = {
        "agentId": agent_id,
        "role": role,
        "status": _team_status(member.get("status")),
    }
    current_task = _string_value(member.get("currentTask"), member.get("current_task"))
    if current_task:
        shaped["currentTask"] = current_task
    return shaped


def _dedupe_team_members(members: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: dict[str, dict[str, Any]] = {}
    for member in members:
        agent_id = member.get("agentId")
        if not isinstance(agent_id, str) or not agent_id:
            continue
        deduped[agent_id] = member
    return list(deduped.values())


def _team_status(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {
        "running",
        "busy",
        "claimed",
        "active",
        "in_progress",
        "queued",
        "pending",
        "planning",
        "starting",
        "started",
        "blocked",
        "waiting_approval",
        "verifying",
    }:
        return "running"
    if normalized in {"completed", "done", "succeeded"}:
        return "completed"
    if normalized in {"failed", "error", "cancelled", "canceled"}:
        return "error"
    return "idle"


def _system_notification_message(event_type: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    subtype = _system_notification_subtype(event_type, payload)
    if not subtype:
        return None
    message = {
        "type": "system_notification",
        "subtype": subtype,
        "data": _public_payload(payload),
    }
    text = _notification_text(payload)
    if text:
        message["message"] = _truncate_text(text)
    return message


def _progress_notification_message(event_type: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    message = _system_notification_message(event_type, payload)
    if message is None:
        return None
    message["data"] = _progress_notification_data(payload)
    if not message.get("message"):
        message["message"] = _progress_fallback_message(event_type, payload)
    return message


def _system_notification_subtype(event_type: str, payload: dict[str, Any]) -> str | None:
    explicit_subtype = _string_value(payload.get("subtype"))
    if explicit_subtype in _SYSTEM_NOTIFICATION_SUBTYPES:
        return explicit_subtype
    if event_type == "system_notification":
        if _looks_like_session_state_change(payload):
            return "session_state_changed"
        return "task_progress"
    if event_type in {"init", "compact_boundary", "session_state_changed", "task_started"}:
        return event_type
    if event_type == "compact_summary":
        return "compact_summary"
    if event_type == "background_task":
        status = str(payload.get("status") or payload.get("state") or "").strip().lower()
        return "task_started" if status in _TASK_STARTED_STATUSES else "task_progress"
    if event_type in _TASK_PROGRESS_EVENT_TYPES:
        return "task_progress"
    return None


def _looks_like_session_state_change(payload: dict[str, Any]) -> bool:
    for key in ("sessionId", "session_id", "profileName", "profile_name", "model", "mode", "scope"):
        if payload.get(key) not in (None, ""):
            return True
    phase = str(payload.get("phase") or "").strip().lower()
    return phase in {"provider_preflight", "provider_recovery", "session", "session_state"}


def _notification_text(payload: dict[str, Any]) -> str:
    return _string_value(
        payload.get("message"),
        payload.get("summary"),
        payload.get("description"),
        payload.get("detail"),
        payload.get("reason"),
        payload.get("title"),
        payload.get("state"),
        payload.get("status"),
        payload.get("phase"),
        payload.get("content"),
        payload.get("text"),
        payload.get("body"),
        payload.get("chunk"),
        payload.get("delta"),
        payload.get("toolOutput"),
    )


def _progress_fallback_message(event_type: str, payload: dict[str, Any]) -> str:
    tool_name = _string_value(payload.get("toolName"), payload.get("tool_name"), payload.get("name"))
    target = _string_value(payload.get("target"), payload.get("command"), payload.get("phase"), payload.get("status"))
    if tool_name and target:
        return f"{tool_name}: {target}"
    if tool_name:
        return f"{tool_name} progress"
    if target:
        return target
    return event_type.replace(".", " ")


def _truncate_text(value: str, max_length: int = 500) -> str:
    if len(value) <= max_length:
        return value
    return f"{value[:max_length]}..."


def _public_payload(payload: dict[str, Any]) -> dict[str, Any]:
    value = _public_payload_value(payload)
    return value if isinstance(value, dict) else {}


def _progress_notification_data(payload: dict[str, Any]) -> dict[str, Any]:
    data = _public_payload(payload)
    for key in ("chunk", "delta", "toolOutput", "text"):
        value = data.get(key)
        if isinstance(value, str) and len(value) > 500:
            data[key] = _truncate_text(value)
            data[f"{key}Truncated"] = True
    return data


_PUBLIC_PAYLOAD_DENY_KEYS = {
    "activeWorktree",
    "activeWorktreeId",
    "active_worktree",
    "active_worktree_id",
    "apiKey",
    "api_key",
    "auth",
    "authorization",
    "headers",
    "originalWorkspaceRoot",
    "original_workspace_root",
    "providerRequest",
    "requestJson",
    "raw",
    "secret",
    "sessionId",
    "session_id",
    "taskId",
    "task_id",
    "token",
    "workspaceRoot",
    "workspace_root",
    "worktreePath",
    "worktree_path",
}

_PUBLIC_PAYLOAD_MAX_DEPTH = 3
_PUBLIC_PAYLOAD_LIST_LIMIT = 12
_PUBLIC_PAYLOAD_TEXT_LIMIT = 500


def _public_payload_value(value: Any, *, key: str = "", depth: int = 0) -> Any:
    key_text = str(key)
    if key_text.startswith("_") or key_text in _PUBLIC_PAYLOAD_DENY_KEYS:
        return None
    folded = key_text.replace("-", "_").casefold()
    if any(marker in folded for marker in ("token", "secret", "apikey", "api_key", "authorization")):
        return None
    if isinstance(value, str):
        return _truncate_text(value, _PUBLIC_PAYLOAD_TEXT_LIMIT) if len(value) > _PUBLIC_PAYLOAD_TEXT_LIMIT else value
    if isinstance(value, list):
        if depth >= _PUBLIC_PAYLOAD_MAX_DEPTH:
            return {"omitted": True, "type": "array", "items": len(value)}
        return [
            item
            for item in (
                _public_payload_value(child, key=key_text, depth=depth + 1)
                for child in value[:_PUBLIC_PAYLOAD_LIST_LIMIT]
            )
            if item not in (None, "", [], {})
        ]
    if isinstance(value, dict):
        if depth >= _PUBLIC_PAYLOAD_MAX_DEPTH:
            return {"omitted": True, "type": "object", "keys": len(value)}
        public: dict[str, Any] = {}
        for child_key, child_value in value.items():
            child_key_text = str(child_key)
            public_value = _public_payload_value(child_value, key=child_key_text, depth=depth + 1)
            if public_value in (None, "", [], {}):
                continue
            public[child_key_text] = public_value
        return public
    return value


def _field_changed(payload: dict[str, Any], field_name: str) -> bool:
    changed_fields = payload.get("changedFields")
    if changed_fields is None:
        changed_fields = payload.get("changed_fields")
    if changed_fields is None:
        return True
    if isinstance(changed_fields, dict):
        value = changed_fields.get(field_name)
        return bool(value)
    if isinstance(changed_fields, (list, tuple, set)):
        return field_name in {str(item) for item in changed_fields}
    return True


def _cache_read_tokens(usage: dict[str, Any]) -> int | None:
    return _int_or_none(
        usage.get("cache_read_tokens"),
        usage.get("cacheReadTokens"),
        usage.get("cache_read_input_tokens"),
        usage.get("cacheReadInputTokens"),
        usage.get("cached_tokens"),
        usage.get("cachedTokens"),
        _nested_int(
            usage,
            (
                "input_tokens_details",
                "inputTokensDetails",
                "prompt_tokens_details",
                "promptTokensDetails",
            ),
            (
                "cached_tokens",
                "cachedTokens",
                "cache_read_tokens",
                "cacheReadTokens",
                "cache_read_input_tokens",
                "cacheReadInputTokens",
            ),
        ),
    )


def _nested_int(usage: dict[str, Any], parent_keys: tuple[str, ...], child_keys: tuple[str, ...]) -> int | None:
    for parent_key in parent_keys:
        details = usage.get(parent_key)
        if not isinstance(details, dict):
            continue
        parsed = _int_or_none(*(details.get(child_key) for child_key in child_keys))
        if parsed is not None:
            return parsed
    return None


def _int_value(*values: Any) -> int:
    parsed = _int_or_none(*values)
    return parsed if parsed is not None else 0


def _int_or_none(*values: Any) -> int | None:
    for value in values:
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            return max(0, int(value))
        if isinstance(value, str):
            try:
                return max(0, int(float(value)))
            except ValueError:
                continue
    return None


def _string_value(*values: Any) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
        if value is not None and not isinstance(value, (dict, list, tuple, set)):
            text = str(value).strip()
            if text:
                return text
    return ""


def _stable_public_id(value: str) -> str:
    normalized = " ".join(str(value or "").strip().split()).lower()
    if not normalized:
        return "task"
    chars: list[str] = []
    previous_dash = False
    for char in normalized:
        if char.isalnum():
            chars.append(char)
            previous_dash = False
        elif not previous_dash:
            chars.append("-")
            previous_dash = True
    public_id = "".join(chars).strip("-")
    return public_id[:80] or "task"
