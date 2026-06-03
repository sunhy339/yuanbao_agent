from __future__ import annotations

from typing import Any

from .models import RuntimeEvent


_DIRECT_EVENT_TYPES = {
    "content_start",
    "content_delta",
    "tool_use_complete",
    "tool_result",
    "permission_request",
    "computer_use_permission_request",
    "message_complete",
    "thinking",
    "status",
    "api_retry",
    "system_notification",
}

_SERVER_MESSAGE_FIELDS: dict[str, set[str]] = {
    "content_start": {"type", "blockType", "toolName", "toolUseId", "parentToolUseId"},
    "content_delta": {"type", "text", "toolInput"},
    "tool_use_complete": {"type", "toolName", "toolUseId", "input", "parentToolUseId"},
    "tool_result": {"type", "toolUseId", "content", "isError", "parentToolUseId"},
    "permission_request": {"type", "requestId", "toolName", "toolUseId", "input", "description"},
    "computer_use_permission_request": {"type", "requestId", "request"},
    "message_complete": {"type", "usage"},
    "thinking": {"type", "text"},
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
    "task_update": {"type", "taskId", "status", "progress"},
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

    payload = event.payload if isinstance(event.payload, dict) else {}
    message: dict[str, Any] | None = None
    if event.type in _DIRECT_EVENT_TYPES:
        message = _flatten_payload(event.type, payload)
        if event.type == "message_complete":
            message["usage"] = normalize_yuanbao_usage(payload.get("usage") or _raw_usage(payload))
    elif event.type in {"connected", "pong"}:
        message = _flatten_payload(event.type, payload)
        if event.type == "connected" and not message.get("sessionId"):
            message["sessionId"] = event.session_id
    elif event.type == "assistant.token":
        delta = payload.get("delta")
        if isinstance(delta, str) and delta:
            message = {"type": "content_delta", "text": delta}
    elif event.type == "message.completed":
        message = {
            "type": "message_complete",
            "usage": normalize_yuanbao_usage(payload.get("usage") or _raw_usage(payload)),
        }
    elif event.type == "assistant.message.completed":
        message = {
            "type": "message_complete",
            "usage": normalize_yuanbao_usage(payload.get("usage") or _raw_usage(payload)),
        }
    elif event.type in {"message.failed", "task.failed"}:
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

    return _server_message_shape(message) if message is not None else None


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
    return shaped


def _raw_usage(payload: dict[str, Any]) -> Any:
    raw = payload.get("raw")
    if isinstance(raw, dict):
        return raw.get("usage")
    return None


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
    task_id = payload.get("taskId") or event.task_id
    status = payload.get("status")
    if not status:
        status = event.type.removeprefix("task.")
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
        "taskId": task_id,
        "status": str(status),
    }
    if progress:
        message["progress"] = str(progress)
    return message


def _team_message(event_type: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    team_name = _team_name(payload)
    if event_type in {"collab.team.created", "collab.task.created", "collab.worker.upserted"}:
        return {
            "type": "team_created",
            "teamName": team_name,
        }
    if event_type in {"collab.team.deleted", "collab.worker.deleted"}:
        return {
            "type": "team_deleted",
            "teamName": team_name,
        }
    if event_type == "collab.message.sent":
        return {
            "type": "team_update",
            "teamName": team_name,
            "members": _team_members(payload),
        }
    if event_type.startswith("collab.task.") or event_type.startswith("collab.worker."):
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
    workers = payload.get("workers")
    if isinstance(workers, list):
        return [_team_member_from_worker(worker) for worker in workers if isinstance(worker, dict)]
    worker = payload.get("worker")
    if isinstance(worker, dict):
        return [_team_member_from_worker(worker)]
    message = payload.get("message")
    if isinstance(message, dict):
        return [_team_member_from_message(message)]
    budget = payload.get("budget")
    if isinstance(budget, dict):
        return [_team_member_from_budget(payload, budget)]
    task = payload.get("task")
    if isinstance(task, dict):
        return [
            {
                "agentId": str(task.get("workerId") or task.get("id") or "task"),
                "role": str(task.get("agentType") or task.get("role") or "worker"),
                "status": _team_status(task.get("status")),
                "currentTask": task.get("title") or task.get("description") or task.get("id"),
            }
        ]
    return []


def _team_member_from_worker(worker: dict[str, Any]) -> dict[str, Any]:
    member = {
        "agentId": str(worker.get("id") or worker.get("workerId") or worker.get("name") or "worker"),
        "role": str(worker.get("role") or worker.get("agentType") or "worker"),
        "status": _team_status(worker.get("status")),
    }
    current_task = worker.get("currentTask") or worker.get("currentTaskId")
    if current_task is not None:
        member["currentTask"] = str(current_task)
    return member


def _team_member_from_message(message: dict[str, Any]) -> dict[str, Any]:
    return {
        "agentId": str(message.get("senderWorkerId") or message.get("senderId") or "worker"),
        "role": str(message.get("senderRole") or message.get("role") or message.get("kind") or "worker"),
        "status": "running",
        "currentTask": message.get("body") or message.get("taskId") or message.get("id"),
    }


def _team_member_from_budget(payload: dict[str, Any], budget: dict[str, Any]) -> dict[str, Any]:
    return {
        "agentId": str(budget.get("workerId") or budget.get("agentId") or "worker"),
        "role": str(budget.get("role") or budget.get("agentType") or "worker"),
        "status": "running",
        "currentTask": f"{payload.get('dimension') or 'budget'} budget consumed {payload.get('consumed') or 0}",
    }


def _team_status(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"running", "busy", "claimed", "active", "in_progress"}:
        return "running"
    if normalized in {"completed", "done", "succeeded"}:
        return "completed"
    if normalized in {"failed", "error", "cancelled", "canceled"}:
        return "error"
    return "idle"


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
