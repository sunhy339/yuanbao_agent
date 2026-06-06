"""Publishing Mixin â extracted from Orchestrator.

Handles event publishing, hook firing, runtime snapshots, and event visibility.
"""
from __future__ import annotations

import json
import logging
import time
from copy import deepcopy
from typing import Any

logger = logging.getLogger(__name__)

from ..context.token_budget import estimate_tokens
from ..execution.tool_pipeline import _frontend_visible_tool_result, _visible_command_output_chunk
from ..yuanbao_event_adapter import normalize_yuanbao_usage
from ..models import RuntimeEvent
from ..services.command_background import cancel_background_commands


_CHAT_COMPAT_EVENT_TYPES = {
    "content_start",
    "content_delta",
    "thinking",
    "assistant_progress",
    "tool_use_complete",
    "tool_result",
    "permission_request",
    "message_complete",
    "status",
    "plan_update",
}

_RECOVERABLE_CHAT_COMPAT_EVENT_TYPES = {
    "assistant_progress",
    "computer_use_permission_request",
    "content_start",
    "message_complete",
    "permission_request",
    "plan_update",
    "status",
    "thinking",
    "tool_result",
    "tool_use_complete",
}


def _should_persist_chat_compat_trace_mirror(
    *,
    event_type: str,
    payload: dict[str, Any],
    visibility: str,
) -> bool:
    if visibility not in {"chat", "panel"}:
        return False
    if event_type in _RECOVERABLE_CHAT_COMPAT_EVENT_TYPES:
        return True
    if event_type == "content_delta":
        return any(isinstance(payload.get(key), str) and payload.get(key) for key in ("toolInput", "toolOutput"))
    return False


_VISIBLE_TOOL_PAYLOAD_EVENT_TYPES = {
    "tool.started",
    "tool.completed",
    "tool.failed",
    "tool.blocked",
}

_RAW_TOOL_LIFECYCLE_EVENT_TYPES = {
    "tool.started",
    "tool.progress",
    "tool.output",
    "tool.completed",
    "tool.failed",
    "tool.blocked",
    "command.started",
    "command.output",
    "command.completed",
    "command.failed",
    "command.cancelled",
}

_ROOT_CHAT_EVENT_TYPES = {
    "api_retry",
    "ask_user_question",
    "background_task",
    "compact_boundary",
    "compact_summary",
    "computer_use_permission",
    "computer_use_permission_request",
    "message.created",
    "message.completed",
    "message.failed",
    "session_title_updated",
    "system_notification",
    "task_summary",
}

_ROOT_CHAT_DERIVATION_EVENT_TYPES = {
    "approval.requested",
    "approval.resolved",
    "assistant.token",
    "command.cancelled",
    "command.completed",
    "command.failed",
    "command.output",
    "command.started",
    "message.delta",
    "task.cancelled",
    "task.completed",
    "task.failed",
    "task.started",
    "task.updated",
    "tool.blocked",
    "tool.completed",
    "tool.failed",
    "tool.output",
    "tool.progress",
    "tool.started",
}

_ROOT_PANEL_EVENT_TYPES = {
    "goal_event",
    "memory_event",
    "task.created",
    "task.orphaned",
    "task.queued",
    "task.runtime_work_waiting",
}

_ROOT_PANEL_EVENT_PREFIXES = (
    "collab.",
    "task.child.",
    "task.planning.",
    "task.provider_",
    "task.reflection.",
    "task.runtime_",
    "task.supplement.",
    "task.worktree.",
)

_ROOT_TRACE_EVENT_PREFIXES = (
    "agent.decision.",
    "mcp.",
    "provider.",
    "routing.",
    "task.routing.",
    "tool.call.",
    "tool_recovery.",
)

_ROOT_TRACE_EVENT_TYPES = {
    "context.trimmed",
    "mcp.error",
    "provider.error",
    "runtime.error",
}

_RAW_PANEL_MIRROR_EVENT_TYPES = {
    "approval.requested",
    "approval.resolved",
    "task.cancelled",
    "task.completed",
    "task.created",
    "task.failed",
    "task.orphaned",
    "task.queued",
    "task.runtime_work_waiting",
    "task.started",
    "task.updated",
}

_TERMINAL_TASK_STATUSES = {"completed", "failed", "cancelled", "canceled"}

_TERMINAL_TASK_ALLOWED_EVENTS = {
    "completed": {
        "agent.decision.completion",
        "memory_event",
        "message.completed",
        "approval.requested",
        "approval.resolved",
        "computer_use_permission_request",
        "computer_use_permission",
        "session.updated",
        "task.completed",
        "task.loop_failure.recovered",
        "task.planning.recovered",
        "task.reflection.completed",
    },
    "failed": {
        "agent.decision.completion",
        "memory_event",
        "message.failed",
        "session.updated",
        "task.failed",
    },
    "cancelled": {
        "command.cancelled",
        "session.updated",
        "task.cancelled",
    },
    "canceled": {
        "command.cancelled",
        "session.updated",
        "task.cancelled",
    },
}

_LARGE_VISIBLE_PAYLOAD_KEYS = {
    "base64",
    "body",
    "content",
    "data",
    "diff",
    "diffText",
    "html",
    "image",
    "imageData",
    "markdown",
    "output",
    "patch",
    "patchText",
    "replacement",
    "stderr",
    "stdout",
    "text",
}
_VISIBLE_PAYLOAD_STRING_LIMIT = 1000
_VISIBLE_PAYLOAD_PREVIEW_LIMIT = 240
_VISIBLE_PAYLOAD_LIST_LIMIT = 20
_VISIBLE_PAYLOAD_MAX_DEPTH = 4
_LOW_VALUE_TOOL_PROGRESS_NAMES = {
    "code_search",
    "git_diff",
    "git_status",
    "list_dir",
    "list_directory",
    "memory.recall",
    "read_file",
    "scratchpad.read",
    "search_files",
}
_LOW_VALUE_TOOL_PROGRESS_PHASES = {
    "context_read",
    "git",
    "memory",
    "search",
}
_INTERNAL_VISIBLE_PAYLOAD_KEYS = {
    "approvalId",
    "approval_id",
    "sessionId",
    "session_id",
    "taskId",
    "task_id",
    "toolGroupId",
    "tool_group_id",
    "toolIndex",
    "tool_index",
    "toolOperationId",
    "tool_operation_id",
    "toolOperationLabel",
    "tool_operation_label",
    "toolTotal",
    "tool_total",
    "workspaceRoot",
    "workspace_root",
}


class PublishingMixin:
    """Mixin providing event publishing and hook firing helpers."""

    def _event_context_summary(
        self,
        context: dict[str, Any],
        messages: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        budget_stats = context.get("budgetStats")
        summary: dict[str, Any] = {
            "workspaceId": context.get("workspace_id"),
            "workspaceName": context.get("workspace_name"),
            "workspaceRoot": context.get("workspace_root"),
            "projectFocus": context.get("project_focus"),
            "projectMemory": context.get("project_memory"),
            "searchQuery": context.get("search_query"),
            "searchMode": context.get("search_mode"),
            "toolCount": len(context.get("tools") or []),
        }
        if isinstance(budget_stats, dict):
            message_tokens = (
                sum(estimate_tokens(message.get("content", "")) for message in messages)
                if messages is not None
                else budget_stats.get("messageTokens")
            )
            tool_schema_tokens = budget_stats.get("toolSchemaTokens") or 0
            estimated_input_tokens = (
                message_tokens + tool_schema_tokens
                if isinstance(message_tokens, (int, float)) and isinstance(tool_schema_tokens, (int, float))
                else budget_stats.get("estimatedInputTokens")
            )
            summary["budgetStats"] = {
                "estimatedTokens": estimated_input_tokens,
                "estimatedInputTokens": estimated_input_tokens,
                "messageTokens": message_tokens,
                "toolSchemaTokens": budget_stats.get("toolSchemaTokens"),
                "stablePrefixTokens": budget_stats.get("stablePrefixTokens"),
                "promptCache": budget_stats.get("promptCache"),
                "maxContextTokens": budget_stats.get("maxContextTokens"),
                "droppedSections": budget_stats.get("droppedSections"),
                "trimmedSections": budget_stats.get("trimmedSections"),
                "includedSections": budget_stats.get("includedSections"),
                "stablePrefixSections": budget_stats.get("stablePrefixSections"),
                "dynamicTailSections": budget_stats.get("dynamicTailSections"),
                "promptLayers": budget_stats.get("promptLayers"),
            }
        task_focus = context.get("task_focus")
        if isinstance(task_focus, dict):
            summary["taskFocus"] = {
                "taskId": task_focus.get("taskId"),
                "currentStep": task_focus.get("currentStep"),
                "acceptanceCriteriaCount": len(task_focus.get("acceptanceCriteria") or []),
                "outOfScopeCount": len(task_focus.get("outOfScope") or []),
            }
        return summary

    def _persist_session_context_preview(self, *, session_id: str, context_summary: dict[str, Any]) -> None:
        if not hasattr(self._store, "update_session_metadata"):
            return
        preview = deepcopy(context_summary)
        budget_stats = preview.get("budgetStats")
        if isinstance(budget_stats, dict):
            budget_stats["updatedAt"] = int(time.time() * 1000)
            budget_stats["estimated"] = False
        try:
            self._store.update_session_metadata(session_id, {"contextPreview": preview})
        except Exception:
            logger.debug("Failed to persist session context preview", exc_info=True)

    def _publish_context_update(
        self,
        *,
        session_id: str,
        task: dict[str, Any],
        context: dict[str, Any],
        messages: list[dict[str, Any]],
    ) -> None:
        context_summary = self._event_context_summary(context, messages=messages)
        self._persist_session_context_preview(session_id=session_id, context_summary=context_summary)
        self._publish(
            session_id=session_id,
            task=task,
            event_type="task.updated",
            payload={
                "status": task.get("status"),
                "currentStep": task.get("currentStep"),
                "context": context_summary,
            },
            visibility="panel",
        )

    def _publish_task_run_snapshot(self, *, session_id: str, task: dict[str, Any]) -> None:
        self._publish(
            session_id=session_id,
            task=task,
            event_type="task.updated",
            payload={
                "status": task["status"],
                "plan": task.get("plan"),
                "currentStep": task.get("currentStep"),
                "changedFiles": task.get("changedFiles") or [],
                "commands": task.get("commands") or [],
                "verification": task.get("verification") or [],
                "summary": task.get("summary"),
                "resultSummary": task.get("resultSummary"),
            },
        )

    def _publish_event_raw(
        self,
        *,
        session_id: str,
        task: dict[str, Any],
        event_type: str,
        payload: dict[str, Any],
        visibility: str = "chat",
    ) -> None:
        event = RuntimeEvent(
            event_id=self._store.new_id("evt"),
            session_id=session_id,
            task_id=task["id"],
            type=event_type,
            ts=self._store.now(),
            payload=payload,
            visibility=visibility,
        )
        self._event_bus.publish(event)

    @staticmethod
    def _compact_goal_event_text(value: Any, limit: int = 220) -> str:
        text = " ".join(str(value or "").split())
        if len(text) <= limit:
            return text
        return f"{text[:limit - 1].rstrip()}…"

    @classmethod
    def _sanitize_visible_event_payload(
        cls,
        event_type: str,
        payload: dict[str, Any],
        visibility: str,
    ) -> dict[str, Any]:
        if visibility in {"chat", "panel"} and event_type in {
            "task.started",
            "task.completed",
            "task.failed",
            "task.cancelled",
        }:
            return cls._visible_task_lifecycle_payload(event_type, payload)
        if event_type not in _VISIBLE_TOOL_PAYLOAD_EVENT_TYPES:
            return payload
        safe_payload = dict(payload)
        for key in ("arguments", "result"):
            if key == "result" and event_type in {"tool.completed", "tool.failed", "tool.blocked"}:
                value = safe_payload.get(key)
                if isinstance(value, dict) and (
                    str(value.get("status") or "").strip().lower() == "approval_required"
                    or isinstance(value.get("approval"), dict)
                ):
                    preview = safe_payload.get("resultPreview")
                    safe_payload[key] = _frontend_visible_tool_result(
                        str(safe_payload.get("toolName") or ""),
                        value,
                        str(safe_payload.get("target") or ""),
                        summary=str(safe_payload.get("resultSummary") or ""),
                        preview=preview if isinstance(preview, list) else None,
                    )
                continue
            value = safe_payload.get(key)
            if isinstance(value, (dict, list, str)):
                safe_payload[key] = cls._sanitize_visible_payload_value(key, value)
        return safe_payload

    @classmethod
    def _visible_task_lifecycle_payload(cls, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        safe: dict[str, Any] = {
            "status": payload.get("status") or event_type.removeprefix("task."),
        }
        for key in (
            "goal",
            "currentStep",
            "summary",
            "resultSummary",
            "detail",
            "errorCode",
            "businessErrorCode",
            "retryable",
            "changedFiles",
            "commands",
            "verification",
        ):
            value = payload.get(key)
            if value in (None, "", [], {}):
                continue
            safe[key] = cls._sanitize_visible_payload_value(key, value)
        if "summary" not in safe and isinstance(payload.get("message"), str) and payload.get("message").strip():
            safe["summary"] = cls._sanitize_visible_payload_value("summary", payload["message"])
        return safe

    @classmethod
    def _sanitize_visible_payload_value(cls, key: str, value: Any, *, depth: int = 0) -> Any:
        key_name = str(key)
        if isinstance(value, str):
            should_compact = key_name in _LARGE_VISIBLE_PAYLOAD_KEYS or len(value) > _VISIBLE_PAYLOAD_STRING_LIMIT
            if not should_compact:
                return value
            return {
                "omitted": True,
                "chars": len(value),
                "preview": value[:_VISIBLE_PAYLOAD_PREVIEW_LIMIT],
            }
        if isinstance(value, dict):
            if depth >= _VISIBLE_PAYLOAD_MAX_DEPTH:
                return {
                    "omitted": True,
                    "type": "object",
                    "keys": len(value),
                }
            return {
                str(child_key): cls._sanitize_visible_payload_value(str(child_key), child_value, depth=depth + 1)
                for child_key, child_value in value.items()
                if str(child_key) not in _INTERNAL_VISIBLE_PAYLOAD_KEYS
            }
        if isinstance(value, list):
            if depth >= _VISIBLE_PAYLOAD_MAX_DEPTH:
                return {
                    "omitted": True,
                    "type": "array",
                    "items": len(value),
                }
            items = [
                cls._sanitize_visible_payload_value(key_name, item, depth=depth + 1)
                for item in value[:_VISIBLE_PAYLOAD_LIST_LIMIT]
            ]
            if len(value) > _VISIBLE_PAYLOAD_LIST_LIMIT:
                items.append(
                    {
                        "omitted": True,
                        "items": len(value) - _VISIBLE_PAYLOAD_LIST_LIMIT,
                    }
                )
            return items
        return value

    @staticmethod
    def _raw_runtime_event_visibility(event_type: str, effective_visibility: str, explicit_visibility: str | None) -> str:
        if explicit_visibility is None and event_type == "assistant.token":
            return "trace"
        if explicit_visibility is None and event_type in _RAW_TOOL_LIFECYCLE_EVENT_TYPES:
            return "trace"
        if explicit_visibility is None and event_type in _RAW_PANEL_MIRROR_EVENT_TYPES:
            return "panel"
        return effective_visibility

    def _latest_terminal_task_status(self, task: dict[str, Any]) -> str | None:
        task_id = str(task.get("id") or "").strip()
        if not task_id or task_id.startswith("ctask_"):
            return None
        status = str(task.get("status") or "").strip().lower()
        try:
            latest = self._store.get_task({"taskId": task_id})["task"]
            status = str(latest.get("status") or status).strip().lower()
        except Exception:  # noqa: BLE001
            logger.debug("Failed to refresh task status before publishing %s", task_id, exc_info=True)
        return status if status in _TERMINAL_TASK_STATUSES else None

    def _should_drop_event_after_terminal_task(
        self,
        *,
        task: dict[str, Any],
        event_type: str,
        payload: dict[str, Any],
        effective_visibility: str,
    ) -> bool:
        status = self._latest_terminal_task_status(task)
        if status is None:
            return False
        if event_type == "approval.resolved":
            return not (payload.get("ignored") is True or payload.get("deferred") is True)
        allowed = _TERMINAL_TASK_ALLOWED_EVENTS.get(status, set())
        if event_type in allowed:
            return False
        if status not in {"cancelled", "canceled"} and effective_visibility == "trace":
            return False
        logger.debug(
            "Suppressing late %s event for terminal task %s (%s)",
            event_type,
            task.get("id"),
            status,
        )
        return True

    def _goal_event_payload_for_task_event(self, event_type: str, task: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any] | None:
        if event_type == "task.failed":
            failure_task = dict(task)
            if payload.get("errorCode"):
                failure_task["errorCode"] = payload.get("errorCode")
            if isinstance(payload.get("structuredResult"), dict):
                failure_task["structuredResult"] = payload["structuredResult"]
            is_provider_failure = getattr(self, "_is_provider_runtime_failure", None)
            if callable(is_provider_failure) and is_provider_failure(failure_task):
                return None
        mapping = {
            "task.started": ("目标已开始", "started", "running"),
            "task.completed": ("目标已完成", "completed", "completed"),
            "task.failed": ("目标未完成", "failed", "failed"),
            "task.cancelled": ("目标已取消", "cancelled", "cancelled"),
        }
        mapped = mapping.get(event_type)
        if mapped is None:
            return None
        title, action, status = mapped
        goal = self._compact_goal_event_text(task.get("goal") or payload.get("goal") or "")
        summary_source = (
            payload.get("summary")
            or payload.get("resultSummary")
            or payload.get("detail")
            or payload.get("errorCode")
            or goal
        )
        return {
            "title": title,
            "summary": self._compact_goal_event_text(summary_source),
            "status": status,
            "action": action,
            "goal": goal,
            "category": "task_goal",
        }

    def _publish_goal_event_for_task_event(
        self,
        *,
        session_id: str,
        task: dict[str, Any],
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        if task.get("role", "root") != "root":
            return
        goal_payload = self._goal_event_payload_for_task_event(event_type, task, payload)
        if goal_payload is None:
            return
        self._publish_event_raw(
            session_id=session_id,
            task=task,
            event_type="goal_event",
            payload=goal_payload,
            visibility=self._infer_event_visibility("goal_event", task),
        )

    def _publish_chat_compat_event(
        self,
        *,
        session_id: str,
        task: dict[str, Any],
        event_type: str,
        payload: dict[str, Any],
        visibility: str = "chat",
        persist_trace: bool = False,
    ) -> None:
        compat_payload = dict(payload)
        compat_payload["_chatCompat"] = True
        if _should_persist_chat_compat_trace_mirror(
            event_type=event_type,
            payload=compat_payload,
            visibility=visibility,
        ):
            persist_trace = True
        if persist_trace:
            bridge = compat_payload.get("_bridge")
            compat_payload["_bridge"] = {
                **(bridge if isinstance(bridge, dict) else {}),
                "persistTraceMirror": True,
            }
        else:
            bridge = compat_payload.get("_bridge")
            compat_payload["_bridge"] = {
                **(bridge if isinstance(bridge, dict) else {}),
                "skipTraceMirror": True,
            }
        self._publish_event_raw(
            session_id=session_id,
            task=task,
            event_type=event_type,
            payload=compat_payload,
            visibility=visibility,
        )

    def _publish_chat_status(
        self,
        *,
        session_id: str,
        task: dict[str, Any],
        state: str,
        verb: Any = None,
        payload: dict[str, Any] | None = None,
        visibility: str = "chat",
        force: bool = False,
    ) -> None:
        if task.get("role", "root") != "root":
            return
        normalized_state = str(state or "").strip()
        if not normalized_state:
            return
        status_payload: dict[str, Any] = {"state": normalized_state}
        if verb not in (None, ""):
            status_payload["verb"] = str(verb)
        if isinstance(payload, dict):
            for key in ("step", "elapsed", "tokens", "phase", "reason", "strategy"):
                if payload.get(key) is not None:
                    status_payload[key] = payload.get(key)

        fingerprint_cache = getattr(self, "_chat_status_fingerprints", None)
        if not isinstance(fingerprint_cache, dict):
            fingerprint_cache = {}
            setattr(self, "_chat_status_fingerprints", fingerprint_cache)
        cache_key = str(task.get("id") or "")
        fingerprint = json.dumps(
            {
                key: status_payload.get(key)
                for key in ("state", "verb", "step", "phase", "strategy")
                if key in status_payload
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        if not force and cache_key and fingerprint_cache.get(cache_key) == fingerprint:
            return
        if cache_key:
            fingerprint_cache[cache_key] = fingerprint
        self._publish_chat_compat_event(
            session_id=session_id,
            task=task,
            event_type="status",
            payload=status_payload,
            visibility=visibility,
        )

    def _mark_tool_output_delta_seen(self, task_id: str, tool_use_id: Any, stream: Any, text: Any) -> bool:
        if not tool_use_id or not isinstance(text, str) or not text:
            return False
        cache = getattr(self, "_chat_tool_output_delta_fingerprints", None)
        if not isinstance(cache, set):
            cache = set()
            setattr(self, "_chat_tool_output_delta_fingerprints", cache)
        key = (str(task_id or ""), str(tool_use_id), str(stream or ""), text)
        if key in cache:
            return True
        cache.add(key)
        return False

    def _mark_tool_stream_seen(self, task_id: str, tool_use_id: Any, stream: Any) -> bool:
        if not tool_use_id:
            return False
        cache = getattr(self, "_chat_tool_output_streams", None)
        if not isinstance(cache, set):
            cache = set()
            setattr(self, "_chat_tool_output_streams", cache)
        key = (str(task_id or ""), str(tool_use_id), str(stream or ""))
        if key in cache:
            return True
        cache.add(key)
        return False

    def _chat_text_start_seen(self, *, task: dict[str, Any], message_id: Any = None) -> bool:
        cache = getattr(self, "_chat_text_start_message_ids", None)
        if not isinstance(cache, set):
            cache = set()
            setattr(self, "_chat_text_start_message_ids", cache)
        key = str(message_id or task.get("activeAssistantMessageId") or task.get("id") or "")
        if not key:
            return False
        if key in cache:
            return True
        cache.add(key)
        return False

    def _chat_tool_start_seen(self, *, task: dict[str, Any], tool_use_id: Any) -> bool:
        if not tool_use_id:
            return False
        cache = getattr(self, "_chat_tool_start_tool_use_ids", None)
        if not isinstance(cache, set):
            cache = set()
            setattr(self, "_chat_tool_start_tool_use_ids", cache)
        key = f"{task.get('id') or ''}:{tool_use_id}"
        if key in cache:
            return True
        cache.add(key)
        return False

    def _remember_chat_content_start(self, *, task: dict[str, Any], payload: dict[str, Any]) -> None:
        block_type = payload.get("blockType")
        if block_type == "text":
            self._chat_text_start_seen(task=task, message_id=payload.get("messageId"))
        elif block_type == "tool_use":
            self._chat_tool_start_seen(task=task, tool_use_id=payload.get("toolUseId"))

    @staticmethod
    def _chat_compat_usage_from_payload(payload: dict[str, Any]) -> dict[str, Any] | None:
        usage = payload.get("usage")
        if isinstance(usage, dict):
            raw_usage = dict(usage)
            return {
                **raw_usage,
                **normalize_yuanbao_usage(raw_usage),
            }
        raw = payload.get("raw")
        if isinstance(raw, dict) and isinstance(raw.get("usage"), dict):
            raw_usage = dict(raw["usage"])
            return {
                **raw_usage,
                **normalize_yuanbao_usage(raw_usage),
            }
        return None

    @staticmethod
    def _compact_chat_event_text(value: Any, limit: int = 260) -> str:
        text = " ".join(str(value or "").split())
        if len(text) <= limit:
            return text
        return f"{text[:limit - 1].rstrip()}…"

    @staticmethod
    def _plan_update_fingerprint(payload: dict[str, Any]) -> str:
        plan = payload.get("plan")
        if isinstance(plan, list):
            parts: list[str] = []
            for step in plan:
                if not isinstance(step, dict):
                    continue
                parts.append(
                    "|".join(
                        str(step.get(key) or "").strip()
                        for key in ("id", "title", "status")
                    )
                )
            if parts:
                return "\n".join(parts)
        return "|".join(
            str(payload.get(key) or "").strip()
            for key in ("status", "currentStep", "summary", "resultSummary", "detail")
        )

    def _plan_update_payload_from_task_update(self, task: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any] | None:
        plan = payload.get("plan")
        current_step = payload.get("currentStep") or task.get("currentStep")
        summary = (
            payload.get("detail")
            or payload.get("summary")
            or payload.get("resultSummary")
            or current_step
        )
        has_plan = isinstance(plan, list) and bool(plan)
        if not has_plan:
            return None
        status = str(payload.get("status") or task.get("status") or "running")
        title = "计划更新"
        if current_step:
            title = "正在处理"
        if status in {"completed", "failed", "cancelled", "canceled"}:
            title = "任务进展"
        event_payload: dict[str, Any] = {
            "title": title,
            "status": status,
            "summary": self._compact_chat_event_text(summary),
            "currentStep": current_step,
            "fingerprint": self._plan_update_fingerprint(payload),
        }
        if has_plan:
            event_payload["plan"] = plan
            event_payload["stepCount"] = len(plan)
            active_steps = [
                step for step in plan
                if isinstance(step, dict) and str(step.get("status") or "").strip().lower() in {"active", "running", "in_progress"}
            ]
            completed_steps = [
                step for step in plan
                if isinstance(step, dict) and str(step.get("status") or "").strip().lower() in {"completed", "complete", "done"}
            ]
            if active_steps:
                event_payload["activeStep"] = active_steps[0].get("title") or active_steps[0].get("id")
            event_payload["completedSteps"] = len(completed_steps)
        return event_payload

    def _publish_assistant_progress(
        self,
        *,
        session_id: str,
        task: dict[str, Any],
        text: str,
        phase: str,
        status: str = "running",
        payload: dict[str, Any] | None = None,
        visibility: str = "panel",
    ) -> None:
        if task.get("role", "root") != "root":
            return
        step_value = payload.get("step") if isinstance(payload, dict) else None
        display_text = text
        if phase == "provider_request" and step_value not in (None, ""):
            display_text = f"{text}（第 {step_value} 轮）"
        summary = self._compact_chat_event_text(text)
        display_summary = self._compact_chat_event_text(display_text)
        if not summary:
            return
        progress_payload: dict[str, Any] = {
            "text": display_summary or summary,
            "summary": summary,
            "phase": phase,
            "status": status,
        }
        if isinstance(payload, dict):
            for key in (
                "step",
                "model",
                "operation",
                "stream",
                "pressure",
                "consumedSteps",
                "remainingSteps",
                "maxSteps",
                "recommendedAction",
                "toolName",
                "toolUseId",
                "target",
                "inputSummary",
                "resultSummary",
                "resultPreview",
                "durationMs",
                "toolCategory",
                "toolPhaseId",
                "toolPhaseLabel",
                "toolSemanticParentId",
                "toolSemanticParentLabel",
                "reason",
                "parentToolUseId",
                "toolNames",
                "stage",
                "mode",
                "isError",
            ):
                if payload.get(key) is not None:
                    progress_payload[key] = payload.get(key)
        self._publish_chat_compat_event(
            session_id=session_id,
            task=task,
            event_type="assistant_progress",
            payload=progress_payload,
            visibility=visibility,
            persist_trace=bool(payload.get("persistTrace")) if isinstance(payload, dict) else False,
        )

    @staticmethod
    def _tool_batch_metadata_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
        metadata: dict[str, Any] = {}
        tool_group_id = payload.get("toolGroupId")
        if isinstance(tool_group_id, str) and tool_group_id.strip():
            metadata["toolGroupId"] = tool_group_id
        for key in ("toolIndex", "toolTotal"):
            value = payload.get(key)
            if isinstance(value, int):
                metadata[key] = value
        for key in ("toolOperationId", "toolOperationLabel"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                metadata[key] = value.strip()
        return metadata

    def _tool_started_progress_text(self, payload: dict[str, Any]) -> str | None:
        tool_name = str(payload.get("toolName") or "").strip()
        block_summary = str(payload.get("inputSummary") or "").strip().lower()
        if block_summary == "blocked by tool policy":
            target = self._compact_chat_event_text(payload.get("target") or tool_name, limit=120)
            return f"工具本轮不可用：{target}" if target else "工具本轮不可用"
        if block_summary == "blocked by plan mode":
            target = self._compact_chat_event_text(payload.get("target") or tool_name, limit=120)
            return f"计划模式已拦截工具：{target}" if target else "计划模式已拦截工具"
        if block_summary == "exit_plan_mode outside plan mode":
            return "计划模式未激活，不能提交计划审批"
        quiet_tools = {
            *_LOW_VALUE_TOOL_PROGRESS_NAMES,
            "git_status",
            "list_dir",
            "list_directory",
            "read_file",
            "scratchpad.read",
        }
        if tool_name in quiet_tools:
            return None
        labels = {
            "run_command": "准备运行命令",
            "apply_patch": "准备应用改动",
            "code_search": "准备搜索代码",
            "git_diff": "准备读取 Git 差异",
            "git_status": "准备检查 Git 状态",
            "list_dir": "准备列出目录",
            "list_directory": "准备列出目录",
            "read_file": "准备读取文件",
            "search_files": "准备搜索文件",
            "write_file": "准备写入文件",
            "agent": "准备启动子任务",
            "task": "准备启动子任务",
            "computer_use": "准备进行桌面操作",
        }
        label = labels.get(tool_name)
        if label is None:
            return None
        target = self._compact_chat_event_text(
            payload.get("inputSummary") or payload.get("target") or "",
            limit=120,
        )
        return f"{label}：{target}" if target else label

    def _tool_started_output_delta_text(self, payload: dict[str, Any]) -> str:
        tool_name = str(payload.get("toolName") or "").strip()
        executor_progress_tools = {
            "apply_patch",
            "browser",
            "code_search",
            "computer_use",
            "git_diff",
            "git_status",
            "list_dir",
            "list_directory",
            "memory.recall",
            "memory.remember",
            "notebook",
            "read_file",
            "run_command",
            "scratchpad.read",
            "scratchpad.write",
            "search_files",
            "task",
            "web_fetch",
            "write_file",
        }
        if tool_name in executor_progress_tools or tool_name.startswith("mcp__"):
            return ""
        labels = {
            "code_search": "正在搜索代码",
            "git_diff": "正在读取 Git 差异",
            "git_status": "正在检查 Git 状态",
            "list_dir": "正在列出目录",
            "list_directory": "正在列出目录",
            "memory.recall": "正在检索记忆",
            "memory.remember": "正在写入记忆",
            "notebook": "正在处理 Notebook",
            "read_file": "正在读取文件",
            "scratchpad.read": "正在读取 scratchpad",
            "scratchpad.write": "正在写入 scratchpad",
            "search_files": "正在搜索文件",
            "browser": "正在读取网页",
            "web_fetch": "正在读取网页",
        }
        label = labels.get(tool_name)
        if label is None and tool_name.startswith("mcp__"):
            label = "正在调用 MCP 工具"
        if label is None and tool_name:
            label = "正在调用工具"
        if label is None:
            return ""
        detail = self._compact_chat_event_text(
            payload.get("inputSummary") or payload.get("target") or "",
            limit=160,
        )
        text = f"{label}：{detail}" if detail else label
        return f"{text}\n"

    def _publish_tool_started_progress(
        self,
        *,
        session_id: str,
        task: dict[str, Any],
        payload: dict[str, Any],
        visibility: str,
    ) -> None:
        text = self._tool_started_progress_text(payload)
        if not text:
            return
        self._publish_assistant_progress(
            session_id=session_id,
            task=task,
            text=text,
            phase="tool_execution",
            payload={
                "toolName": payload.get("toolName"),
                "toolUseId": payload.get("toolCallId"),
                "target": payload.get("target"),
                "inputSummary": payload.get("inputSummary"),
                "toolCategory": payload.get("toolCategory"),
                "toolPhaseId": payload.get("toolPhaseId"),
                "toolPhaseLabel": payload.get("toolPhaseLabel"),
                "toolSemanticParentId": payload.get("toolSemanticParentId"),
                "toolSemanticParentLabel": payload.get("toolSemanticParentLabel"),
            },
            visibility=visibility,
        )

    def _maybe_publish_tool_phase_progress(
        self,
        *,
        session_id: str,
        task: dict[str, Any],
        payload: dict[str, Any],
        visibility: str,
    ) -> None:
        tool_name = str(payload.get("toolName") or "").strip()
        tool_phase_id = str(payload.get("toolPhaseId") or "").strip()
        tool_category = str(payload.get("toolCategory") or "").strip()
        if (
            tool_name in _LOW_VALUE_TOOL_PROGRESS_NAMES
            or tool_phase_id in _LOW_VALUE_TOOL_PROGRESS_PHASES
            or tool_category in _LOW_VALUE_TOOL_PROGRESS_PHASES
        ):
            return
        semantic_parent_id = str(payload.get("toolSemanticParentId") or "").strip()
        if not semantic_parent_id:
            return
        task_id = str(task.get("id") or "")
        cache_key = f"{task_id}:{semantic_parent_id}"
        seen = getattr(self, "_chat_tool_phase_progress_seen", None)
        if not isinstance(seen, set):
            seen = set()
            setattr(self, "_chat_tool_phase_progress_seen", seen)
        if cache_key in seen:
            return
        seen.add(cache_key)
        label = str(payload.get("toolSemanticParentLabel") or payload.get("toolPhaseLabel") or "工具阶段").strip() or "工具阶段"
        self._publish_assistant_progress(
            session_id=session_id,
            task=task,
            text=f"进入{label}阶段",
            phase="tool_phase",
            payload={
                "toolName": payload.get("toolName"),
                "toolUseId": payload.get("toolCallId"),
                "target": payload.get("target"),
                "inputSummary": payload.get("inputSummary"),
                "toolCategory": payload.get("toolCategory"),
                "toolPhaseId": payload.get("toolPhaseId"),
                "toolPhaseLabel": payload.get("toolPhaseLabel"),
                "toolSemanticParentId": semantic_parent_id,
                "toolSemanticParentLabel": label,
            },
            visibility=visibility,
        )

    def _tool_result_progress_detail(self, payload: dict[str, Any]) -> str:
        for key in ("resultSummary", "reason", "recoveryHint"):
            detail = self._compact_chat_event_text(payload.get(key) or "", limit=140)
            if detail:
                return detail
        result = payload.get("result")
        if isinstance(result, dict):
            for key in ("summary", "error", "message", "stderr", "stdout"):
                detail = self._compact_chat_event_text(result.get(key) or "", limit=140)
                if detail:
                    return detail
        return ""

    @staticmethod
    def _is_background_running_command_result(payload: dict[str, Any]) -> bool:
        if str(payload.get("toolName") or "").strip() != "run_command":
            return False
        result = payload.get("result")
        if not isinstance(result, dict):
            return False
        return result.get("background") is True or str(result.get("status") or "").strip().lower() == "running"

    def _tool_result_progress_text(self, event_type: str, payload: dict[str, Any]) -> str | None:
        tool_name = str(payload.get("toolName") or "").strip()
        detail = self._tool_result_progress_detail(payload)
        if event_type == "tool.completed":
            if self._is_background_running_command_result(payload):
                command = self._compact_chat_event_text(
                    payload.get("inputSummary") or payload.get("target") or detail,
                    limit=140,
                )
                return f"命令已在后台运行：{command}" if command else "命令已在后台运行"
            labels = {
                "run_command": "命令已完成",
                "apply_patch": "改动已应用",
                "write_file": "文件已写入",
                "agent": "子任务已完成",
                "task": "子任务已完成",
                "computer_use": "桌面操作已完成",
            }
            label = labels.get(tool_name)
            if label is None:
                return None
            return f"{label}：{detail}" if detail else label
        if event_type == "tool.blocked":
            result = payload.get("result")
            if isinstance(result, dict) and str(result.get("failureKind") or "") == "tool_not_available":
                target = self._compact_chat_event_text(payload.get("target") or tool_name, limit=120)
                label = f"工具本轮不可用：{target}" if target else "工具本轮不可用"
                return f"{label}（已按策略拦截）"
            if isinstance(result, dict):
                error_text = str(result.get("error") or "")
                summary_text = str(result.get("summary") or "")
                if "Plan mode allows only" in error_text or summary_text == "Tool blocked by plan mode.":
                    target = self._compact_chat_event_text(payload.get("target") or tool_name, limit=120)
                    label = f"计划模式已拦截工具：{target}" if target else "计划模式已拦截工具"
                    return f"{label}：{detail}" if detail else label
                if "plan mode is not active" in error_text or "plan mode is not active" in summary_text:
                    return f"计划模式未激活：{detail}" if detail else "计划模式未激活"
            labels = {
                "run_command": "命令被阻止",
                "apply_patch": "改动被阻止",
                "write_file": "写入文件被阻止",
                "agent": "子任务被阻止",
                "task": "子任务被阻止",
                "computer_use": "桌面操作被阻止",
            }
            label = labels.get(tool_name, "工具被阻止")
            return f"{label}：{detail}" if detail else label
        labels = {
            "run_command": "命令执行失败",
            "apply_patch": "应用改动失败",
            "write_file": "写入文件失败",
            "read_file": "读取文件失败",
            "list_files": "列出文件失败",
            "search_files": "搜索文件失败",
            "agent": "子任务失败",
            "task": "子任务失败",
            "computer_use": "桌面操作失败",
        }
        label = labels.get(tool_name, "工具执行失败")
        return f"{label}：{detail}" if detail else label

    def _tool_result_output_delta_text(self, payload: dict[str, Any]) -> str:
        result = payload.get("result")
        if isinstance(result, dict) and str(result.get("status") or "").strip().lower() == "approval_required":
            return ""
        preview = payload.get("resultPreview")
        lines: list[str] = []
        if isinstance(preview, list):
            for row in preview[:5]:
                if not isinstance(row, dict):
                    continue
                label = self._compact_chat_event_text(row.get("label") or "", limit=24)
                value = self._compact_chat_event_text(row.get("value") or "", limit=160)
                if label and value:
                    lines.append(f"{label}: {value}")
        if lines:
            return "\n".join(lines) + "\n"
        summary = self._compact_chat_event_text(payload.get("resultSummary") or "", limit=220)
        return f"{summary}\n" if summary else ""

    def _tool_result_activity_delta_texts(self, payload: dict[str, Any]) -> list[str]:
        result = payload.get("result")
        if not isinstance(result, dict):
            return []
        activity_items: list[Any] = []
        for key in ("steps", "logs", "events", "progress", "timeline"):
            value = result.get(key)
            if isinstance(value, list) and value:
                activity_items = value
                break
        if not activity_items:
            return []

        deltas: list[str] = []
        seen: set[str] = set()
        for item in activity_items[:8]:
            text = self._tool_result_activity_item_text(item)
            if not text or text in seen:
                continue
            seen.add(text)
            deltas.append(f"{text}\n")
        return deltas

    def _tool_result_activity_item_text(self, item: Any) -> str:
        if isinstance(item, str):
            return self._compact_chat_event_text(item, limit=220)
        if not isinstance(item, dict):
            return self._compact_chat_event_text(item, limit=220)

        label = self._compact_chat_event_text(
            item.get("label")
            or item.get("title")
            or item.get("name")
            or item.get("phase")
            or item.get("step")
            or item.get("action")
            or item.get("type")
            or item.get("event")
            or item.get("level")
            or "",
            limit=80,
        )
        status = self._compact_chat_event_text(item.get("status") or item.get("state") or "", limit=40)
        detail = self._compact_chat_event_text(
            item.get("summary")
            or item.get("message")
            or item.get("detail")
            or item.get("description")
            or item.get("output")
            or item.get("text")
            or "",
            limit=180,
        )
        target = self._compact_chat_event_text(
            item.get("target") or item.get("path") or item.get("file") or item.get("url") or "",
            limit=120,
        )

        prefix = label
        if prefix and status:
            prefix = f"{prefix} ({status})"
        elif status:
            prefix = status
        if prefix and detail:
            return f"{prefix}: {detail}"
        if prefix and target:
            return f"{prefix}: {target}"
        return prefix or detail or target

    def _publish_tool_result_progress(
        self,
        *,
        session_id: str,
        task: dict[str, Any],
        event_type: str,
        payload: dict[str, Any],
        visibility: str,
    ) -> None:
        text = self._tool_result_progress_text(event_type, payload)
        if not text:
            return
        if event_type == "tool.completed":
            if self._is_background_running_command_result(payload):
                phase = "tool_running"
                status = "running"
            else:
                phase = "tool_completed"
                status = "completed"
            is_error = False
        elif event_type == "tool.blocked":
            phase = "tool_blocked"
            status = "blocked"
            is_error = True
        else:
            phase = "tool_failed"
            status = "failed"
            is_error = True
        self._publish_assistant_progress(
            session_id=session_id,
            task=task,
            text=text,
            phase=phase,
            status=status,
            payload={
                "toolName": payload.get("toolName"),
                "toolUseId": payload.get("toolCallId"),
                "target": payload.get("target"),
                "inputSummary": payload.get("inputSummary"),
                "resultSummary": payload.get("resultSummary"),
                "resultPreview": payload.get("resultPreview"),
                "durationMs": payload.get("durationMs"),
                "toolCategory": payload.get("toolCategory"),
                "toolPhaseId": payload.get("toolPhaseId"),
                "toolPhaseLabel": payload.get("toolPhaseLabel"),
                "toolSemanticParentId": payload.get("toolSemanticParentId"),
                "toolSemanticParentLabel": payload.get("toolSemanticParentLabel"),
                "reason": payload.get("reason"),
                "parentToolUseId": payload.get("parentToolUseId"),
                **self._tool_batch_metadata_from_payload(payload),
                "isError": is_error,
            },
            visibility=visibility,
        )

    def _publish_chat_compat_for_event(
        self,
        *,
        session_id: str,
        task: dict[str, Any],
        event_type: str,
        payload: dict[str, Any],
        effective_visibility: str,
    ) -> None:
        if effective_visibility != "chat" or task.get("role", "root") != "root":
            return
        if event_type in _CHAT_COMPAT_EVENT_TYPES:
            return

        active_msg_id = task.get("activeAssistantMessageId")

        if event_type in {"task.completed", "task.failed", "task.cancelled"}:
            self._publish_chat_status(
                session_id=session_id,
                task=task,
                state="idle",
                payload=payload,
                visibility=effective_visibility,
            )

        if event_type == "task.updated":
            plan_payload = self._plan_update_payload_from_task_update(task, payload)
            if plan_payload is not None:
                fingerprint_cache = getattr(self, "_chat_plan_update_fingerprints", None)
                if not isinstance(fingerprint_cache, dict):
                    fingerprint_cache = {}
                    setattr(self, "_chat_plan_update_fingerprints", fingerprint_cache)
                cache_key = str(task.get("id") or "")
                last_key = fingerprint_cache.get(cache_key)
                next_key = plan_payload.get("fingerprint")
                if next_key and next_key != last_key:
                    if cache_key:
                        fingerprint_cache[cache_key] = next_key
                    self._publish_chat_compat_event(
                        session_id=session_id,
                        task=task,
                        event_type="plan_update",
                        payload=plan_payload,
                        visibility=effective_visibility,
                    )
            return

        if event_type == "assistant.token":
            delta = payload.get("delta")
            if not isinstance(delta, str) or not delta:
                return
            if not self._chat_text_start_seen(task=task, message_id=active_msg_id):
                self._publish_chat_compat_event(
                    session_id=session_id,
                    task=task,
                    event_type="content_start",
                    payload={
                        "blockType": "text",
                        "messageId": active_msg_id,
                    },
                    visibility=effective_visibility,
                )
            return

        if event_type == "tool.started":
            tool_call_id = payload.get("toolCallId")
            tool_name = payload.get("toolName")
            arguments = payload.get("arguments")
            parent_tool_use_id = payload.get("parentToolUseId")
            if not self._chat_tool_start_seen(task=task, tool_use_id=tool_call_id):
                self._publish_chat_compat_event(
                    session_id=session_id,
                    task=task,
                    event_type="content_start",
                    payload={
                        "blockType": "tool_use",
                        "toolName": tool_name,
                        "toolUseId": tool_call_id,
                        **({"target": payload.get("target")} if payload.get("target") else {}),
                        **({"inputSummary": payload.get("inputSummary")} if payload.get("inputSummary") else {}),
                        **({"parentToolUseId": parent_tool_use_id} if parent_tool_use_id else {}),
                        **({"toolCategory": payload.get("toolCategory")} if payload.get("toolCategory") else {}),
                        **({"toolPhaseId": payload.get("toolPhaseId")} if payload.get("toolPhaseId") else {}),
                        **({"toolPhaseLabel": payload.get("toolPhaseLabel")} if payload.get("toolPhaseLabel") else {}),
                        **({"toolSemanticParentId": payload.get("toolSemanticParentId")} if payload.get("toolSemanticParentId") else {}),
                        **({"toolSemanticParentLabel": payload.get("toolSemanticParentLabel")} if payload.get("toolSemanticParentLabel") else {}),
                    },
                    visibility=effective_visibility,
                )
            if tool_call_id and tool_name:
                self._publish_chat_compat_event(
                    session_id=session_id,
                    task=task,
                    event_type="tool_use_complete",
                    payload={
                        "toolUseId": tool_call_id,
                        "toolName": tool_name,
                        "input": arguments if arguments is not None else {},
                        **({"target": payload.get("target")} if payload.get("target") else {}),
                        **({"inputSummary": payload.get("inputSummary")} if payload.get("inputSummary") else {}),
                        **({"parentToolUseId": parent_tool_use_id} if parent_tool_use_id else {}),
                        **({"toolCategory": payload.get("toolCategory")} if payload.get("toolCategory") else {}),
                        **({"toolPhaseId": payload.get("toolPhaseId")} if payload.get("toolPhaseId") else {}),
                        **({"toolPhaseLabel": payload.get("toolPhaseLabel")} if payload.get("toolPhaseLabel") else {}),
                        **({"toolSemanticParentId": payload.get("toolSemanticParentId")} if payload.get("toolSemanticParentId") else {}),
                        **({"toolSemanticParentLabel": payload.get("toolSemanticParentLabel")} if payload.get("toolSemanticParentLabel") else {}),
                    },
                    visibility=effective_visibility,
                )
            output_delta = self._tool_started_output_delta_text(payload)
            if tool_call_id and output_delta:
                if self._mark_tool_output_delta_seen(task["id"], tool_call_id, "activity", output_delta):
                    output_delta = ""
            if tool_call_id and output_delta:
                self._mark_tool_stream_seen(task["id"], tool_call_id, "activity")
                self._publish_chat_compat_event(
                    session_id=session_id,
                    task=task,
                    event_type="content_delta",
                    payload={
                        "toolUseId": tool_call_id,
                        "toolName": tool_name,
                        "target": payload.get("target"),
                        "inputSummary": payload.get("inputSummary"),
                        "parentToolUseId": parent_tool_use_id,
                        "toolCategory": payload.get("toolCategory"),
                        "toolPhaseId": payload.get("toolPhaseId"),
                        "toolPhaseLabel": payload.get("toolPhaseLabel"),
                        "toolSemanticParentId": payload.get("toolSemanticParentId"),
                        "toolSemanticParentLabel": payload.get("toolSemanticParentLabel"),
                        "toolOutput": output_delta,
                        "outputStream": "activity",
                    },
                    visibility=effective_visibility,
                )
            self._publish_chat_status(
                session_id=session_id,
                task=task,
                state="tool_executing",
                verb=str(tool_name or "tool"),
                payload=payload,
                visibility=effective_visibility,
            )
            self._maybe_publish_tool_phase_progress(
                session_id=session_id,
                task=task,
                payload=payload,
                visibility="panel",
            )
            self._publish_tool_started_progress(
                session_id=session_id,
                task=task,
                payload=payload,
                visibility="panel",
            )
            return

        if event_type == "command.output":
            tool_use_id = payload.get("toolUseId")
            chunk = payload.get("chunk")
            if not tool_use_id or not isinstance(chunk, str) or not chunk:
                return
            visible_chunk = _visible_command_output_chunk(chunk)
            self._publish_chat_compat_event(
                session_id=session_id,
                task=task,
                event_type="content_delta",
                payload={
                    "toolUseId": tool_use_id,
                    "toolName": payload.get("toolName") or "run_command",
                    "target": payload.get("target"),
                    "inputSummary": payload.get("inputSummary"),
                    "parentToolUseId": payload.get("parentToolUseId"),
                    "toolCategory": payload.get("toolCategory"),
                    "toolPhaseId": payload.get("toolPhaseId"),
                    "toolPhaseLabel": payload.get("toolPhaseLabel"),
                    "toolSemanticParentId": payload.get("toolSemanticParentId"),
                    "toolSemanticParentLabel": payload.get("toolSemanticParentLabel"),
                    "toolOutput": visible_chunk,
                    "outputStream": payload.get("stream") or "stdout",
                },
                visibility=effective_visibility,
            )
            return

        if event_type in {"tool.progress", "tool.output"}:
            tool_use_id = payload.get("toolUseId") or payload.get("toolCallId")
            chunk = (
                payload.get("chunk")
                or payload.get("delta")
                or payload.get("toolOutput")
                or payload.get("message")
                or payload.get("summary")
                or payload.get("text")
            )
            if not tool_use_id or not isinstance(chunk, str) or not chunk:
                return
            stream = payload.get("outputStream") or payload.get("stream")
            if not isinstance(stream, str) or not stream.strip():
                stream = "activity" if event_type == "tool.progress" else "result_preview"
            if self._mark_tool_output_delta_seen(task["id"], tool_use_id, stream, chunk):
                return
            self._mark_tool_stream_seen(task["id"], tool_use_id, stream)
            self._publish_chat_compat_event(
                session_id=session_id,
                task=task,
                event_type="content_delta",
                payload={
                    "toolUseId": tool_use_id,
                    "toolName": payload.get("toolName"),
                    "target": payload.get("target"),
                    "inputSummary": payload.get("inputSummary"),
                    "parentToolUseId": payload.get("parentToolUseId"),
                    "toolCategory": payload.get("toolCategory"),
                    "toolPhaseId": payload.get("toolPhaseId"),
                    "toolPhaseLabel": payload.get("toolPhaseLabel"),
                    "toolSemanticParentId": payload.get("toolSemanticParentId"),
                    "toolSemanticParentLabel": payload.get("toolSemanticParentLabel"),
                    "toolOutput": chunk,
                    "outputStream": stream,
                },
                visibility=effective_visibility,
            )
            return

        if event_type in {"tool.completed", "tool.failed", "tool.blocked"}:
            tool_call_id = payload.get("toolCallId")
            if not tool_call_id:
                return
            activity_deltas = self._tool_result_activity_delta_texts(payload)
            if activity_deltas and payload.get("toolName") != "run_command":
                for activity_delta in activity_deltas:
                    if self._mark_tool_output_delta_seen(task["id"], tool_call_id, "activity", activity_delta):
                        continue
                    self._publish_chat_compat_event(
                        session_id=session_id,
                        task=task,
                        event_type="content_delta",
                        payload={
                            "toolUseId": tool_call_id,
                            "toolName": payload.get("toolName"),
                            "target": payload.get("target"),
                            "inputSummary": payload.get("inputSummary"),
                            "parentToolUseId": payload.get("parentToolUseId"),
                            "toolCategory": payload.get("toolCategory"),
                            "toolPhaseId": payload.get("toolPhaseId"),
                            "toolPhaseLabel": payload.get("toolPhaseLabel"),
                            "toolSemanticParentId": payload.get("toolSemanticParentId"),
                            "toolSemanticParentLabel": payload.get("toolSemanticParentLabel"),
                            "toolOutput": activity_delta,
                            "outputStream": "activity",
                    },
                    visibility=effective_visibility,
                )
            output_delta = self._tool_result_output_delta_text(payload)
            already_streamed_result_preview = self._mark_tool_stream_seen(
                task["id"],
                tool_call_id,
                "result_preview",
            )
            if (
                output_delta
                and payload.get("toolName") != "run_command"
                and not payload.get("resultPreviewStreamed")
                and not already_streamed_result_preview
            ):
                if self._mark_tool_output_delta_seen(task["id"], tool_call_id, "result_preview", output_delta):
                    output_delta = ""
            if (
                output_delta
                and payload.get("toolName") != "run_command"
                and not payload.get("resultPreviewStreamed")
                and not already_streamed_result_preview
            ):
                self._publish_chat_compat_event(
                    session_id=session_id,
                    task=task,
                    event_type="content_delta",
                    payload={
                        "toolUseId": tool_call_id,
                        "toolName": payload.get("toolName"),
                        "target": payload.get("target"),
                        "inputSummary": payload.get("inputSummary"),
                        "parentToolUseId": payload.get("parentToolUseId"),
                        "toolCategory": payload.get("toolCategory"),
                        "toolPhaseId": payload.get("toolPhaseId"),
                        "toolPhaseLabel": payload.get("toolPhaseLabel"),
                        "toolSemanticParentId": payload.get("toolSemanticParentId"),
                        "toolSemanticParentLabel": payload.get("toolSemanticParentLabel"),
                        "toolOutput": output_delta,
                        "outputStream": "result_preview",
                    },
                    visibility=effective_visibility,
                )
            self._publish_chat_compat_event(
                session_id=session_id,
                task=task,
                event_type="tool_result",
                payload={
                    "toolUseId": tool_call_id,
                    "toolName": payload.get("toolName"),
                    "content": _frontend_visible_tool_result(
                        str(payload.get("toolName") or ""),
                        payload.get("result"),
                        str(payload.get("target") or ""),
                        summary=str(payload.get("resultSummary") or ""),
                        preview=payload.get("resultPreview") if isinstance(payload.get("resultPreview"), list) else None,
                    ),
                    "isError": event_type != "tool.completed",
                    **({"target": payload.get("target")} if payload.get("target") else {}),
                    **({"inputSummary": payload.get("inputSummary")} if payload.get("inputSummary") else {}),
                    **({"resultSummary": payload.get("resultSummary")} if payload.get("resultSummary") else {}),
                    **({"resultPreview": payload.get("resultPreview")} if payload.get("resultPreview") else {}),
                    **({"durationMs": payload.get("durationMs")} if payload.get("durationMs") is not None else {}),
                    **({"toolCategory": payload.get("toolCategory")} if payload.get("toolCategory") else {}),
                    **({"toolPhaseId": payload.get("toolPhaseId")} if payload.get("toolPhaseId") else {}),
                    **({"toolPhaseLabel": payload.get("toolPhaseLabel")} if payload.get("toolPhaseLabel") else {}),
                    **({"toolSemanticParentId": payload.get("toolSemanticParentId")} if payload.get("toolSemanticParentId") else {}),
                    **({"toolSemanticParentLabel": payload.get("toolSemanticParentLabel")} if payload.get("toolSemanticParentLabel") else {}),
                    **({"parentToolUseId": payload.get("parentToolUseId")} if payload.get("parentToolUseId") else {}),
                },
                visibility=effective_visibility,
            )
            self._publish_tool_result_progress(
                session_id=session_id,
                task=task,
                event_type=event_type,
                payload=payload,
                visibility="panel",
            )
            return

        if event_type == "approval.requested":
            request = payload.get("request")
            request_id = payload.get("approvalId")
            tool_name = payload.get("kind") or "approval"
            if tool_name == "completion_review":
                self._publish_chat_status(
                    session_id=session_id,
                    task=task,
                    state="idle",
                    payload=payload,
                    visibility="panel",
                )
                return
            if tool_name == "computer_use":
                self._publish_chat_compat_event(
                    session_id=session_id,
                    task=task,
                    event_type="computer_use_permission_request",
                    payload=self._computer_use_permission_payload(
                        approval_id=str(request_id or ""),
                        request=request if isinstance(request, dict) else {},
                        status="waiting_approval",
                    ),
                    visibility=effective_visibility,
                )
                self._publish_chat_status(
                    session_id=session_id,
                    task=task,
                    state="permission_pending",
                    verb="computer_use",
                    payload=payload,
                    visibility=effective_visibility,
                )
                return
            self._publish_chat_compat_event(
                session_id=session_id,
                task=task,
                event_type="permission_request",
                payload={
                    "requestId": request_id,
                    "toolName": tool_name,
                    "input": request if request is not None else {},
                    "description": payload.get("summary"),
                    "preview": payload.get("preview"),
                    "previewSections": (
                        request.get("previewSections")
                        if isinstance(request, dict) and isinstance(request.get("previewSections"), list)
                        else payload.get("previewSections")
                    ),
                    "filesChanged": payload.get("filesChanged"),
                    "changedPaths": payload.get("changedPaths"),
                    "diffText": payload.get("diffText"),
                },
                visibility=effective_visibility,
            )
            self._publish_chat_status(
                session_id=session_id,
                task=task,
                state="permission_pending",
                verb=str(tool_name),
                payload=payload,
                visibility=effective_visibility,
            )
            return

        if event_type == "approval.resolved":
            approval_id = payload.get("approvalId")
            if approval_id:
                approval_request = self._approval_request_payload(str(approval_id))
                payload_request = payload.get("request")
                request = payload_request if isinstance(payload_request, dict) else (
                    approval_request.get("request") if isinstance(approval_request, dict) else {}
                )
                tool_name = payload.get("kind") or (
                    approval_request.get("kind") if isinstance(approval_request, dict) else None
                )
                if tool_name == "completion_review":
                    self._publish_chat_status(
                        session_id=session_id,
                        task=task,
                        state="idle",
                        payload=payload,
                        visibility="panel",
                    )
                    return
                has_resolved_details = bool(
                    tool_name
                    or request
                    or payload.get("preview")
                    or payload.get("filesChanged") is not None
                    or payload.get("changedPaths")
                    or payload.get("diffText")
                )
                if tool_name == "computer_use":
                    self._publish_chat_compat_event(
                        session_id=session_id,
                        task=task,
                        event_type="computer_use_permission",
                        payload=self._computer_use_permission_payload(
                            approval_id=str(approval_id),
                            request=request if isinstance(request, dict) else {},
                            status=str(payload.get("decision") or "resolved"),
                            resolved=True,
                            decision=str(payload.get("decision") or ""),
                            decided_by=str(payload.get("decidedBy") or "user"),
                            decided_at=payload.get("decidedAt"),
                        ),
                        visibility=effective_visibility,
                    )
                elif has_resolved_details:
                    tool_name = tool_name or "approval"
                    self._publish_chat_compat_event(
                        session_id=session_id,
                        task=task,
                        event_type="permission_request",
                        payload={
                            "requestId": approval_id,
                            "toolName": tool_name,
                            "input": request if isinstance(request, dict) else {},
                            "description": payload.get("summary"),
                            "preview": payload.get("preview"),
                            "previewSections": (
                                request.get("previewSections")
                                if isinstance(request, dict) and isinstance(request.get("previewSections"), list)
                                else payload.get("previewSections")
                            ),
                            "filesChanged": payload.get("filesChanged"),
                            "changedPaths": payload.get("changedPaths"),
                            "diffText": payload.get("diffText"),
                            "resolved": True,
                            "decision": payload.get("decision") or "approved",
                            "decidedBy": payload.get("decidedBy"),
                            "decidedAt": payload.get("decidedAt"),
                        },
                        visibility=effective_visibility,
                    )
            self._publish_chat_status(
                session_id=session_id,
                task=task,
                state="idle",
                payload=payload,
                visibility=effective_visibility,
            )
            return

        if event_type == "message.completed":
            self._publish_chat_compat_event(
                session_id=session_id,
                task=task,
                event_type="message_complete",
                payload={
                    "messageId": payload.get("messageId") or active_msg_id,
                    "content": payload.get("content"),
                    "usage": self._chat_compat_usage_from_payload(payload),
                },
                visibility=effective_visibility,
            )
            self._publish_chat_status(
                session_id=session_id,
                task=task,
                state="idle",
                payload=payload,
                visibility=effective_visibility,
            )
            return

        if event_type == "message.failed":
            self._publish_chat_status(
                session_id=session_id,
                task=task,
                state="idle",
                payload=payload,
                visibility=effective_visibility,
            )

    def _approval_request_payload(self, approval_id: str) -> dict[str, Any] | None:
        try:
            result = self._store.get_approval({"approvalId": approval_id})
        except Exception:  # noqa: BLE001
            logger.debug("Failed to load approval %s for chat compatibility", approval_id, exc_info=True)
            return None
        approval = result.get("approval") if isinstance(result, dict) else None
        if not isinstance(approval, dict):
            return None
        request: dict[str, Any] = {}
        try:
            decoded = json.loads(approval.get("requestJson") or "{}")
            if isinstance(decoded, dict):
                request = decoded
        except Exception:  # noqa: BLE001
            logger.debug("Failed to decode approval request %s", approval_id, exc_info=True)
        return {
            "kind": approval.get("kind"),
            "request": request,
        }

    @staticmethod
    def _computer_use_permission_payload(
        *,
        approval_id: str,
        request: dict[str, Any],
        status: str,
        resolved: bool = False,
        decision: str = "",
        decided_by: str = "",
        decided_at: Any = None,
    ) -> dict[str, Any]:
        action = (
            request.get("action")
            or request.get("permission")
            or request.get("summary")
            or request.get("description")
            or "computer use action"
        )
        permission = (
            request.get("permission")
            or request.get("summary")
            or request.get("description")
            or action
        )
        app_name = (
            request.get("app")
            or request.get("application")
            or request.get("target")
            or request.get("windowTitle")
            or request.get("window")
        )
        payload: dict[str, Any] = {
            "approvalId": approval_id,
            "requestId": approval_id,
            "status": status,
            "action": str(action),
            "permission": str(permission),
            "summary": str(permission),
            "request": request,
        }
        if app_name:
            payload["app"] = str(app_name)
        for key in ("target", "selector", "text", "x", "y", "direction", "amount"):
            value = request.get(key)
            if value not in (None, ""):
                payload[key] = value
        preview: list[dict[str, str]] = []
        for label, value in (
            ("应用", app_name),
            ("动作", action),
            ("目标", request.get("selector") or request.get("target")),
            ("坐标", f"{request.get('x')}, {request.get('y')}" if request.get("x") not in (None, "") and request.get("y") not in (None, "") else ""),
            ("文本", request.get("text")),
            ("滚动", f"{request.get('direction') or 'down'} {request.get('amount')}" if request.get("direction") or request.get("amount") else ""),
            ("权限", request.get("permission") or request.get("summary")),
        ):
            text = str(value).strip() if value not in (None, "") else ""
            if text:
                preview.append({"label": label, "value": text[:220]})
        if preview:
            payload["previewRows"] = preview[:6]
        details = request.get("details")
        if isinstance(details, str) and details.strip():
            payload["details"] = details.strip()
        if resolved:
            payload["resolved"] = True
            payload["decision"] = decision
            if decided_by:
                payload["decidedBy"] = decided_by
            if decided_at is not None:
                payload["decidedAt"] = decided_at
        return payload

    def cancel_task(self, params: dict[str, Any]) -> dict[str, Any]:
        task = self._store.get_task({"taskId": params["taskId"]})["task"]
        if task["status"] in {"completed", "failed", "cancelled"}:
            return {"task": task}
        self._validate_task_transition(task["status"], "cancelled", task["id"])
        cancel_background_commands(database_path=self._store.database_path, task_id=task["id"])
        task = self._store.update_task(task_id=params["taskId"], status="cancelled")
        self._clear_pending_react_state(task["id"])
        self._publish(
            session_id=task["sessionId"],
            task=task,
            event_type="task.cancelled",
            payload={"status": task["status"]},
        )
        self._fire_hooks("on_task_cancel", task["sessionId"], task)
        return {"task": task}

    def pause_task(self, params: dict[str, Any]) -> dict[str, Any]:
        task = self._store.get_task({"taskId": params["taskId"]})["task"]
        self._validate_task_transition(task["status"], "paused", task["id"])
        span = self._tracer.start_span("task_pause", trace_id=task.get("id", ""))
        paused_task = self._store.update_task(task_id=task["id"], status="paused")
        self._publish(
            session_id=paused_task["sessionId"],
            task=paused_task,
            event_type="task.paused",
            payload={"status": paused_task["status"], "previousStatus": task["status"]},
        )
        self._fire_hooks("on_task_pause", paused_task["sessionId"], paused_task)
        return {"task": paused_task}

    def _maybe_publish_tool_filter(self, context: dict[str, Any], skill_id: str | None) -> None:
        """Publish skill.tools.filtered event when a skill filtered the available tools."""
        if not skill_id:
            return
        snapshot_meta = context.get("snapshot_metadata") or {}
        skill_fallback = context.get("skillFallback") or snapshot_meta.get("skillFallback")
        if isinstance(skill_fallback, dict):
            session_id = context.get("session_id", "")
            task_id = context.get("task_id") or ""
            task = {"id": task_id, "goal": context.get("goal", "")}
            self._publish(
                session_id=session_id,
                task=task,
                event_type="skill.fallback",
                payload={
                    "skillId": skill_id,
                    "requestedSkillId": skill_fallback.get("requestedSkillId") or skill_id,
                    "reason": skill_fallback.get("reason"),
                    "fallback": skill_fallback.get("fallback"),
                    "status": skill_fallback.get("status"),
                },
            )
            return
        filtered_names = snapshot_meta.get("filtered_tool_names")
        if filtered_names is None:
            return
        task_id = context.get("task_id") or ""
        session_id = context.get("session_id", "")
        task = {"id": task_id, "goal": context.get("goal", "")}
        original_names = snapshot_meta.get("original_tool_names") or [t.get("name", "") for t in context.get("tools", [])]
        self._publish(
            session_id=session_id,
            task=task,
            event_type="skill.tools.filtered",
            payload={
                "skillId": skill_id,
                "allowedTools": filtered_names,
                "filteredOut": [n for n in original_names if n not in filtered_names],
                "policy": (context.get("routing") or {}).get("tool_policy", "strict_whitelist"),
            },
        )

    def _runtime_profile_snapshot(self, context: dict[str, Any] | None = None) -> dict[str, Any]:
        """Capture active behavior/prompt profiles for task-level audit."""
        config = context.get("config") if isinstance(context, dict) else None
        if not isinstance(config, dict):
            config = self._store.get_config({})["config"]
        snapshot_meta = context.get("snapshot_metadata") if isinstance(context, dict) else {}
        if not isinstance(snapshot_meta, dict):
            snapshot_meta = {}
        return {
            "autonomyProfile": self._active_config_profile(config, "autonomy"),
            "agentSoulProfile": self._active_config_profile(config, "agentSoul"),
            "promptLayers": snapshot_meta.get("prompt_layers") or [],
        }

    @staticmethod
    def _active_config_profile(config: dict[str, Any], key: str) -> dict[str, Any] | None:
        section = config.get(key)
        if not isinstance(section, dict):
            return None
        profiles = section.get("profiles")
        if not isinstance(profiles, list):
            return None
        active_id = section.get("activeProfileId")
        for profile in profiles:
            if isinstance(profile, dict) and profile.get("id") == active_id:
                return deepcopy(profile)
        for profile in profiles:
            if isinstance(profile, dict):
                return deepcopy(profile)
        return None

    @staticmethod
    def _infer_event_visibility(event_type: str, task: dict[str, Any]) -> str:
        """Determine event visibility based on event type and task role.

        - "chat": root-level user-facing stream frames and message lifecycle
        - "panel": task, approval, and collaboration records for structured UI panels
        - "trace": diagnostics and provider/runtime internals
        """
        task_role = task.get("role", "root")
        if event_type == "assistant_progress":
            return "panel" if task_role == "root" else "trace"
        if event_type in _CHAT_COMPAT_EVENT_TYPES:
            return "chat" if task_role == "root" else "trace"
        if event_type.startswith("provider."):
            return "trace"
        # Root streaming deltas are user-facing chat output; child deltas stay in trace.
        if event_type in {"assistant.token", "message.delta"}:
            return "chat" if task_role == "root" else "trace"
        # Panel-level: child task events detected via role
        if task_role != "root":
            if event_type.startswith(("task.", "message.")):
                return "panel"
            return "trace"
        if event_type in _ROOT_CHAT_DERIVATION_EVENT_TYPES:
            return "chat"
        if event_type in _ROOT_CHAT_EVENT_TYPES:
            return "chat"
        if event_type in _ROOT_PANEL_EVENT_TYPES or event_type.startswith(_ROOT_PANEL_EVENT_PREFIXES):
            return "panel"
        if event_type in _ROOT_TRACE_EVENT_TYPES or event_type.startswith(_ROOT_TRACE_EVENT_PREFIXES):
            return "trace"
        if event_type.startswith("task."):
            return "panel"
        return "trace"

    def _publish(self, session_id: str, task: dict[str, Any], event_type: str, payload: dict[str, Any], *, visibility: str | None = None) -> None:
        if event_type in _CHAT_COMPAT_EVENT_TYPES:
            payload = dict(payload)
            payload.setdefault("_chatCompat", True)
            effective_visibility_for_bridge = visibility or self._infer_event_visibility(event_type, task)
            if _should_persist_chat_compat_trace_mirror(
                event_type=event_type,
                payload=payload,
                visibility=effective_visibility_for_bridge,
            ):
                payload.setdefault("_bridge", {"persistTraceMirror": True})
            else:
                payload.setdefault("_bridge", {"skipTraceMirror": True})
        elif event_type == "message.completed":
            payload = dict(payload)
            bridge = payload.get("_bridge")
            if isinstance(bridge, dict):
                payload["_bridge"] = {
                    **bridge,
                    "suppressChatReplay": True,
                    "suppressRealtimeFlat": True,
                }
            else:
                payload["_bridge"] = {
                    "suppressChatReplay": True,
                    "suppressRealtimeFlat": True,
                }
        if event_type.startswith("task."):
            payload = dict(payload)
            payload.setdefault("goal", task.get("goal"))
            payload.setdefault("acceptanceCriteria", list(task.get("acceptanceCriteria") or []))
            payload.setdefault("outOfScope", list(task.get("outOfScope") or []))
            payload.setdefault("currentStep", task.get("currentStep"))
        effective_visibility = visibility or self._infer_event_visibility(event_type, task)
        if self._should_drop_event_after_terminal_task(
            task=task,
            event_type=event_type,
            payload=payload,
            effective_visibility=effective_visibility,
        ):
            return
        payload = self._sanitize_visible_event_payload(event_type, payload, effective_visibility)
        if event_type == "content_start" and effective_visibility == "chat":
            self._remember_chat_content_start(task=task, payload=payload)
        self._publish_goal_event_for_task_event(
            session_id=session_id,
            task=task,
            event_type=event_type,
            payload=payload,
        )
        token_delta_payload: dict[str, Any] | None = None
        if event_type == "assistant.token":
            payload = dict(payload)
            active_msg_id = task.get("activeAssistantMessageId")
            if active_msg_id:
                payload["messageId"] = active_msg_id
            payload["_chatCompat"] = True
            token_delta_payload = {**payload}
            token_delta_payload.setdefault("messageId", active_msg_id or "")
        self._publish_chat_compat_for_event(
            session_id=session_id,
            task=task,
            event_type=event_type,
            payload=payload,
            effective_visibility=effective_visibility,
        )
        if token_delta_payload is not None:
            delta_event = RuntimeEvent(
                event_id=self._store.new_id("evt"),
                session_id=session_id,
                task_id=task["id"],
                type="message.delta",
                ts=self._store.now(),
                payload=token_delta_payload,
                visibility=effective_visibility,
            )
            self._event_bus.publish(delta_event)
        raw_visibility = self._raw_runtime_event_visibility(event_type, effective_visibility, visibility)
        event = RuntimeEvent(
            event_id=self._store.new_id("evt"),
            session_id=session_id,
            task_id=task["id"],
            type=event_type,
            ts=self._store.now(),
            payload=payload,
            visibility=raw_visibility,
        )
        self._event_bus.publish(event)

    def _fire_hooks(
        self,
        hook_event: str,
        session_id: str,
        task: dict[str, Any],
        *,
        extra_context: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Fire matching hooks for a lifecycle event. No-op if no hook service."""
        if self._hook_service is None:
            return []
        # Resolve workspaceId from session. Hook dispatch must not break the
        # task lifecycle if the session has already disappeared.
        try:
            session = self._store.require_session(session_id)
        except Exception:
            logger.warning("Hook context session lookup failed for %s", session_id, exc_info=True)
            return []
        workspace_id = ""
        if isinstance(session, dict):
            workspace_id = session.get("workspaceId") or session.get("workspace_id") or ""
        context: dict[str, Any] = {
            "workspaceId": workspace_id,
            "sessionId": session_id,
            "taskId": task.get("id", ""),
            "taskStatus": task.get("status", ""),
            "changedFiles": task.get("changedFiles") or [],
        }
        if extra_context:
            context.update(extra_context)
        try:
            return self._hook_service.invoke_hooks(hook_event, context)
        except Exception:
            logger.warning("Hook execution failed for %s", hook_event, exc_info=True)
            return []
