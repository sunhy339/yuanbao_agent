"""Publishing Mixin â extracted from Orchestrator.

Handles event publishing, hook firing, runtime snapshots, and event visibility.
"""
from __future__ import annotations

import json
import logging
import re
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
    "status",
    "tool_use_complete",
    "tool_result",
    "permission_request",
    "message_complete",
}

_RECOVERABLE_CHAT_COMPAT_EVENT_TYPES = {
    "computer_use_permission_request",
    "content_start",
    "message_complete",
    "permission_request",
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
    "message.failed",
    "session_title_updated",
    "system_notification",
    "task_summary",
}

_ROOT_CHAT_DERIVATION_EVENT_TYPES = {
    "approval.requested",
    "approval.resolved",
    "assistant.token",
    "message.delta",
}

_ROOT_PANEL_EVENT_TYPES = {
    "goal_event",
    "memory_event",
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
    "runtime.context.prepared",
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
_INTERNAL_VISIBLE_PAYLOAD_KEYS = {
    "activeWorktreeId",
    "active_worktree_id",
    "approval",
    "approvalId",
    "approval_id",
    "encoding",
    "ignore",
    "maxBytes",
    "max_bytes",
    "originalWorkspaceRoot",
    "original_workspace_root",
    "providerRequest",
    "raw",
    "requestJson",
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
    "worktreePath",
    "worktree_path",
    "workspaceRoot",
    "workspace_root",
}

_TOOL_PRESENTATION_PAYLOAD_KEYS = (
    "target",
    "inputSummary",
    "displayTitle",
    "displaySummary",
    "displayTarget",
    "displayKind",
    "parentToolUseId",
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
)


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
            event_type="task.context.updated",
            payload={
                "status": task.get("status"),
                "currentStep": task.get("currentStep"),
                "context": context_summary,
            },
            visibility="trace",
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
        if (
            visibility in {"chat", "panel"}
            and event_type.startswith("task.")
            and not event_type.startswith((
                "task.budget.",
                "task.child.",
                "task.planning.",
                "task.provider_",
                "task.runtime_",
                "task.subtask.",
                "task.supplement.",
                "task.validation.",
                "task.worktree.",
            ))
        ):
            return cls._visible_task_lifecycle_payload(event_type, payload)
        if visibility in {"chat", "panel"} and event_type in {"approval.requested", "approval.resolved"}:
            return cls._visible_approval_payload(event_type, payload)
        if event_type not in _VISIBLE_TOOL_PAYLOAD_EVENT_TYPES:
            return payload
        safe_payload = dict(payload)
        for key in ("arguments", "result"):
            if key == "arguments":
                value = safe_payload.get(key)
                if isinstance(value, dict):
                    safe_payload[key] = cls._public_tool_input(str(safe_payload.get("toolName") or ""), value)
                elif isinstance(value, (list, str)):
                    safe_payload[key] = cls._sanitize_visible_payload_value(key, value)
                continue
            if key == "result" and event_type in {"tool.completed", "tool.failed", "tool.blocked"}:
                value = safe_payload.get(key)
                if isinstance(value, dict):
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
            "plan",
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
    def _visible_approval_payload(cls, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        safe: dict[str, Any] = {}
        for key in (
            "approvalId",
            "taskId",
            "kind",
            "toolCallId",
            "toolUseId",
            "toolName",
            "target",
            "inputSummary",
            "displayTitle",
            "displaySummary",
            "displayTarget",
            "displayKind",
            "parentToolUseId",
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
            "summary",
            "decision",
            "decidedBy",
            "decidedAt",
            "ignored",
            "deferred",
            "taskStatus",
            "comment",
            "internal",
            "_bridge",
        ):
            value = payload.get(key)
            if value in (None, "", [], {}):
                continue
            safe[key] = cls._sanitize_visible_payload_value(key, value)
        request = payload.get("request")
        kind = str(payload.get("kind") or "")
        if isinstance(request, dict):
            public_request = cls._public_permission_request_input(kind, request)
            if public_request:
                safe["request"] = public_request
        for key in ("preview", "previewSections", "filesChanged", "changedPaths", "diffText"):
            value = payload.get(key)
            if value in (None, "", [], {}):
                continue
            if key == "diffText" and isinstance(value, str):
                # Keep diff text as text for the patch/approval renderer. Large
                # file contents are hidden at the tool input/result layer, but
                # compacting diffText into JSON breaks live/replay parity and
                # prevents the UI from rendering a real diff.
                safe[key] = value
            else:
                safe[key] = cls._sanitize_visible_payload_value(key, value)
        return safe

    @classmethod
    def _public_permission_request_input(cls, kind: str, request: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(request, dict):
            return {}
        normalized_kind = str(kind or "").strip()
        if normalized_kind == "plan":
            return cls._public_plan_request_input(request)
        if normalized_kind in {"write_file", "apply_patch"}:
            return cls._public_file_change_request_input(normalized_kind, request)
        if normalized_kind == "run_command":
            return cls._public_command_request_input(request)
        safe: dict[str, Any] = {}
        for key, value in request.items():
            if key in {
                "activeWorktreeId",
                "originalWorkspaceRoot",
                "parentToolUseId",
                "requestJson",
                "sessionId",
                "taskId",
                "toolCategory",
                "toolGroupId",
                "toolIndex",
                "toolOperationId",
                "toolOperationLabel",
                "toolPhaseId",
                "toolPhaseLabel",
                "toolSemanticParentId",
                "toolSemanticParentLabel",
                "toolTotal",
                "toolUseId",
                "untrustedContentSignals",
                "workspaceRoot",
                "workspace_root",
            }:
                continue
            if normalized_kind == "run_command" and key in {
                "cellIndex",
                "command",
                "notebookAction",
                "path",
                "shell",
                "timeoutMs",
                "toolName",
            }:
                if value not in (None, "", [], {}):
                    safe[str(key)] = cls._sanitize_visible_payload_value(str(key), value)
                continue
            if value in (None, "", [], {}):
                continue
            safe[str(key)] = cls._sanitize_visible_payload_value(str(key), value)
        return safe

    @classmethod
    def _public_file_change_request_input(cls, kind: str, request: dict[str, Any]) -> dict[str, Any]:
        safe: dict[str, Any] = {}
        for key in (
            "path",
            "summary",
            "filesChanged",
            "changedPaths",
            "overwrite",
            "create_dirs",
            "dry_run",
            "mode",
            "contentChars",
            "patchChars",
        ):
            value = request.get(key)
            if value in (None, "", [], {}):
                continue
            public_key = "dryRun" if key == "dry_run" else key
            safe[public_key] = cls._sanitize_visible_payload_value(public_key, value)
        content = request.get("content")
        if isinstance(content, str):
            safe["contentChars"] = len(content)
        elif isinstance(content, dict):
            chars = content.get("chars")
            if content.get("omitted") is True and isinstance(chars, int):
                safe["contentChars"] = chars
        files = request.get("files")
        if isinstance(files, list) and files:
            public_files: list[dict[str, Any]] = []
            for item in files[:_VISIBLE_PAYLOAD_LIST_LIMIT]:
                if not isinstance(item, dict):
                    continue
                public_file: dict[str, Any] = {}
                path = item.get("path")
                if isinstance(path, str) and path.strip():
                    public_file["path"] = path
                file_content = item.get("content")
                if isinstance(file_content, str):
                    public_file["contentChars"] = len(file_content)
                elif isinstance(file_content, dict):
                    chars = file_content.get("chars")
                    if file_content.get("omitted") is True and isinstance(chars, int):
                        public_file["contentChars"] = chars
                if public_file:
                    public_files.append(public_file)
            if public_files:
                safe["files"] = public_files
        if kind == "apply_patch":
            patch_text = request.get("patchText") or request.get("patch") or request.get("diffText")
            if isinstance(patch_text, str):
                safe["patchChars"] = len(patch_text)
            elif isinstance(patch_text, dict):
                chars = patch_text.get("chars")
                if patch_text.get("omitted") is True and isinstance(chars, int):
                    safe["patchChars"] = chars
        return safe

    @classmethod
    def _public_command_request_input(cls, request: dict[str, Any]) -> dict[str, Any]:
        safe: dict[str, Any] = {}
        for key in (
            "command",
            "cwd",
            "shell",
            "timeoutMs",
            "background",
            "runInBackground",
            "toolName",
            "notebookAction",
            "path",
            "cellIndex",
        ):
            value = request.get(key)
            if value in (None, "", [], {}):
                continue
            safe[key] = cls._sanitize_visible_payload_value(key, value)
        background_job = request.get("backgroundJob")
        if background_job not in (None, "", [], {}):
            safe["backgroundJob"] = cls._sanitize_visible_payload_value("backgroundJob", background_job)
        return safe

    @classmethod
    def _public_subagent_request_input(cls, request: dict[str, Any]) -> dict[str, Any]:
        safe: dict[str, Any] = {}
        for key in (
            "title",
            "description",
            "prompt",
            "role",
            "agentType",
            "agent_type",
            "subagent_type",
            "priority",
            "summary",
        ):
            value = request.get(key)
            if value in (None, "", [], {}):
                continue
            public_key = {
                "agent_type": "agentType",
                "subagent_type": "agentType",
            }.get(key, key)
            if public_key in safe:
                continue
            safe[public_key] = cls._sanitize_visible_payload_value(public_key, value)
        budget = request.get("budget")
        if isinstance(budget, dict):
            safe_budget: dict[str, Any] = {}
            for key in ("maxTokens", "maxToolCalls", "timeoutMs"):
                value = budget.get(key)
                if value not in (None, "", [], {}):
                    safe_budget[key] = value
            if safe_budget:
                safe["budget"] = safe_budget
        return safe

    @classmethod
    def _public_permission_preview(cls, preview: Any) -> list[dict[str, Any]] | None:
        if not isinstance(preview, list):
            return None
        rows: list[dict[str, Any]] = []
        for row in preview[:10]:
            if not isinstance(row, dict):
                continue
            label = str(row.get("label") or "").strip()
            value = row.get("value")
            if not label or value in (None, "", [], {}):
                continue
            text = str(value)
            if cls._visible_text_has_internal_payload(text):
                continue
            safe_row: dict[str, Any] = {
                "label": cls._sanitize_visible_payload_value("label", label),
                "value": cls._sanitize_visible_payload_value("value", value),
            }
            rows.append(safe_row)
        return rows or None

    @staticmethod
    def _visible_text_has_internal_payload(text: str) -> bool:
        lowered = str(text or "").casefold()
        if not lowered:
            return False
        internal_markers = (
            "requestjson",
            "workspaceroot",
            "originalworkspaceroot",
            "approvalid",
            "sessionid",
            "taskid",
            "difftext",
            "patchtext",
            "providerrequest",
            "authorization",
            "api key",
            "secret",
            "token",
        )
        if any(marker in lowered for marker in internal_markers):
            return True
        return bool(re.search(r"(?:^|[\s\"'([{<])(?:[a-z]:[\\/]|%systemdrive%|/users/|/home/|/tmp/)", lowered))

    @classmethod
    def _public_plan_request_input(cls, request: dict[str, Any]) -> dict[str, Any]:
        plan = request.get("plan") if isinstance(request.get("plan"), dict) else {}
        steps = request.get("steps") if isinstance(request.get("steps"), list) else plan.get("steps")
        subtasks = request.get("subtasks") if isinstance(request.get("subtasks"), list) else plan.get("subtasks")
        risks = request.get("risks") if isinstance(request.get("risks"), list) else plan.get("risks")
        summary = request.get("summary") or plan.get("summary") or plan.get("title")
        safe: dict[str, Any] = {}
        for key in (
            "goal",
            "mode",
            "orchestrationMode",
            "source",
            "decompositionFallback",
            "decompositionFallbackReason",
        ):
            value = request.get(key)
            if value in (None, "", [], {}):
                continue
            safe[key] = cls._sanitize_visible_payload_value(key, value)
        if summary not in (None, ""):
            safe["summary"] = cls._sanitize_visible_payload_value("summary", summary)
        if isinstance(steps, list) and steps:
            safe["steps"] = cls._sanitize_visible_payload_value("steps", steps)
            safe["stepCount"] = request.get("stepCount") if request.get("stepCount") is not None else len(steps)
        elif request.get("stepCount") is not None:
            safe["stepCount"] = request.get("stepCount")
        if isinstance(subtasks, list) and subtasks:
            safe["subtasks"] = cls._sanitize_visible_payload_value("subtasks", subtasks)
            safe["subtaskCount"] = request.get("subtaskCount") if request.get("subtaskCount") is not None else len(subtasks)
        elif request.get("subtaskCount") is not None:
            safe["subtaskCount"] = request.get("subtaskCount")
        if isinstance(risks, list) and risks:
            safe["risks"] = cls._sanitize_visible_payload_value("risks", risks)
        for key in ("executionOrder", "previewRows", "previewSections"):
            value = request.get(key)
            if value in (None, "", [], {}):
                continue
            safe[key] = cls._sanitize_visible_payload_value(key, value)
        public_plan = {
            key: value
            for key, value in {
                "summary": summary,
                "steps": steps,
                "subtasks": subtasks,
                "risks": risks,
            }.items()
            if value not in (None, "", [], {})
        }
        if public_plan:
            safe["plan"] = cls._sanitize_visible_payload_value("plan", public_plan)
        return safe

    @classmethod
    def _public_tool_input(cls, tool_name: str, arguments: Any) -> Any:
        if not isinstance(arguments, dict):
            return arguments if arguments is not None else {}
        normalized_tool_name = str(tool_name or "").strip()
        if normalized_tool_name == "exit_plan_mode":
            return cls._public_plan_request_input(arguments)
        if normalized_tool_name in {"agent", "task"}:
            return cls._public_subagent_request_input(arguments)
        if normalized_tool_name in {"write_file", "apply_patch"}:
            return cls._public_file_change_request_input(normalized_tool_name, arguments)
        if normalized_tool_name == "run_command":
            return cls._public_command_request_input(arguments)
        return cls._sanitize_visible_payload_value("input", arguments)

    @classmethod
    def _public_tool_result_content(
        cls,
        *,
        tool_name: str,
        result: Any,
        target: str = "",
        summary: str = "",
        preview: list[dict[str, str]] | None = None,
    ) -> Any:
        content = _frontend_visible_tool_result(
            tool_name,
            result,
            target,
            summary=summary,
            preview=preview,
        )
        return cls._sanitize_visible_payload_value("content", content)

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
            if cls._is_compacted_text_payload(value):
                return {
                    str(child_key): child_value
                    for child_key, child_value in value.items()
                    if str(child_key) not in _INTERNAL_VISIBLE_PAYLOAD_KEYS
                }
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
    def _is_compacted_text_payload(value: dict[str, Any]) -> bool:
        if value.get("truncated") is True and (
            isinstance(value.get("head"), str)
            or isinstance(value.get("tail"), str)
            or isinstance(value.get("text"), str)
        ):
            return True
        return (
            isinstance(value.get("head"), str)
            and isinstance(value.get("tail"), str)
            and any(key in value for key in ("chars", "omittedChars"))
        )

    @staticmethod
    def _raw_runtime_event_visibility(event_type: str, effective_visibility: str, explicit_visibility: str | None) -> str:
        if explicit_visibility is None and event_type in _RAW_TOOL_LIFECYCLE_EVENT_TYPES:
            return "trace"
        if explicit_visibility is None and event_type == "message.completed":
            return "trace"
        if explicit_visibility is None and event_type == "assistant.token":
            return "trace"
        if explicit_visibility is None and event_type in _RAW_PANEL_MIRROR_EVENT_TYPES:
            return "panel"
        return effective_visibility

    @staticmethod
    def _chat_compat_event_visibility(event_type: str, task: dict[str, Any], effective_visibility: str) -> str:
        if task.get("role", "root") != "root":
            return effective_visibility
        if event_type == "message.completed":
            return "chat"
        if event_type in _RAW_TOOL_LIFECYCLE_EVENT_TYPES:
            return "chat"
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
        if event_type == "content_start" and compat_payload.get("blockType") == "text":
            active_msg_id = compat_payload.get("messageId") or task.get("activeAssistantMessageId")
            block_state = self._open_chat_text_block(
                task=task,
                message_id=active_msg_id,
                content_block_id=compat_payload.get("contentBlockId"),
            )
            if active_msg_id:
                compat_payload["messageId"] = active_msg_id
            if block_state.get("contentBlockId"):
                compat_payload["contentBlockId"] = block_state["contentBlockId"]
            if isinstance(block_state.get("blockIndex"), int):
                compat_payload["blockIndex"] = block_state["blockIndex"]
        elif event_type == "content_start" and compat_payload.get("blockType") == "tool_use":
            self._remember_chat_content_start(task=task, payload=compat_payload)
        elif event_type == "message.delta":
            active_msg_id = compat_payload.get("messageId") or task.get("activeAssistantMessageId")
            if active_msg_id:
                compat_payload["messageId"] = active_msg_id
            block_state = self._current_chat_text_block(task=task, message_id=active_msg_id)
            if isinstance(block_state, dict):
                if block_state.get("contentBlockId"):
                    compat_payload.setdefault("contentBlockId", block_state["contentBlockId"])
                if isinstance(block_state.get("blockIndex"), int):
                    compat_payload.setdefault("blockIndex", block_state["blockIndex"])
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

    @staticmethod
    def _tool_presentation_payload(payload: dict[str, Any]) -> dict[str, Any]:
        return {
            key: payload.get(key)
            for key in _TOOL_PRESENTATION_PAYLOAD_KEYS
            if payload.get(key) not in (None, "", [], {})
        }

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

    def _chat_text_state(self) -> dict[str, dict[str, Any]]:
        state = getattr(self, "_chat_text_stream_state", None)
        if not isinstance(state, dict):
            state = {}
            setattr(self, "_chat_text_stream_state", state)
        return state

    @staticmethod
    def _chat_text_key(*, task: dict[str, Any], message_id: Any = None) -> str:
        return str(message_id or task.get("activeAssistantMessageId") or task.get("id") or "")

    def _chat_text_start_seen(self, *, task: dict[str, Any], message_id: Any = None) -> bool:
        key = self._chat_text_key(task=task, message_id=message_id)
        if not key:
            return False
        return bool(self._chat_text_state().get(key, {}).get("open"))

    def _open_chat_text_block(self, *, task: dict[str, Any], message_id: Any = None, content_block_id: Any = None) -> dict[str, Any]:
        key = self._chat_text_key(task=task, message_id=message_id)
        state = self._chat_text_state()
        previous = state.get(key, {}) if key else {}
        if key and not previous:
            previous = self._recover_chat_text_block_state(task=task, message_id=message_id)
            if previous:
                state[key] = previous
        if (
            content_block_id
            and isinstance(previous, dict)
            and previous.get("open")
            and previous.get("contentBlockId") == str(content_block_id)
        ):
            return previous
        block_index = previous.get("blockIndex")
        if not isinstance(block_index, int):
            block_index = -1
        block_index += 1
        message_key = str(message_id or task.get("activeAssistantMessageId") or task.get("id") or "assistant")
        block_id = str(content_block_id or f"{message_key}:text:{block_index}")
        next_state = {
            "open": True,
            "messageId": message_id or task.get("activeAssistantMessageId"),
            "contentBlockId": block_id,
            "blockIndex": block_index,
        }
        if key:
            state[key] = next_state
        return next_state

    def _recover_chat_text_block_state(self, *, task: dict[str, Any], message_id: Any = None) -> dict[str, Any]:
        active_msg_id = str(message_id or task.get("activeAssistantMessageId") or "").strip()
        task_id = str(task.get("id") or "").strip()
        if not active_msg_id or not task_id:
            return {}
        try:
            trace_events = self._store.list_trace_events({"taskId": task_id, "limit": 5000}).get("traceEvents", [])
        except Exception:  # noqa: BLE001
            logger.debug("Failed to recover chat text state for task=%s message=%s", task_id, active_msg_id, exc_info=True)
            return {}

        last: dict[str, Any] | None = None
        for event in trace_events:
            if not isinstance(event, dict) or event.get("type") != "content_start":
                continue
            payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
            if payload.get("blockType") != "text" or str(payload.get("messageId") or "") != active_msg_id:
                continue
            block_index = payload.get("blockIndex")
            if not isinstance(block_index, int):
                continue
            content_block_id = payload.get("contentBlockId")
            if not isinstance(content_block_id, str) or not content_block_id:
                content_block_id = f"{active_msg_id}:text:{block_index}"
            last = {
                "open": False,
                "messageId": active_msg_id,
                "contentBlockId": content_block_id,
                "blockIndex": block_index,
            }
        return last or {}

    def _current_chat_text_block(self, *, task: dict[str, Any], message_id: Any = None) -> dict[str, Any] | None:
        key = self._chat_text_key(task=task, message_id=message_id)
        if not key:
            return None
        state = self._chat_text_state().get(key)
        return state if isinstance(state, dict) and state.get("open") else None

    def _close_chat_text_block(self, *, task: dict[str, Any], message_id: Any = None) -> None:
        key = self._chat_text_key(task=task, message_id=message_id)
        if not key:
            return
        state = self._chat_text_state().get(key)
        if isinstance(state, dict):
            state["open"] = False

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
            content_block_id = payload.get("contentBlockId")
            if content_block_id:
                self._open_chat_text_block(
                    task=task,
                    message_id=payload.get("messageId"),
                    content_block_id=content_block_id,
                )
        elif block_type == "tool_use":
            self._chat_tool_start_seen(task=task, tool_use_id=payload.get("toolUseId"))
            self._close_chat_text_block(task=task)

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
            if (
                not text
                or text in seen
                or self._tool_activity_text_is_internal(text)
                or self._visible_text_has_internal_payload(text)
            ):
                continue
            seen.add(text)
            deltas.append(f"{text}\n")
        return deltas

    def _public_tool_output_delta_text(self, value: Any, *, limit: int = 8000) -> str:
        if not isinstance(value, str):
            return ""
        text = value
        if not text.strip():
            return ""
        if self._tool_activity_text_is_internal(text) or self._visible_text_has_internal_payload(text):
            return ""
        if len(text) <= limit:
            return text
        return f"{text[:limit].rstrip()}..."

    @staticmethod
    def _tool_activity_text_is_internal(text: str) -> bool:
        lowered = str(text or "").casefold()
        if not lowered:
            return True
        sensitive_markers = (
            "requestjson",
            "workspaceroot",
            "originalworkspaceroot",
            "approvalid",
            "sessionid",
            "taskid",
            "difftext",
            "patchtext",
            "providerrequest",
            "authorization",
            "api key",
            "secret",
            "token",
        )
        if any(marker in lowered for marker in sensitive_markers):
            return True
        stripped = lowered.strip()
        if not stripped.startswith(("{", "[")):
            return False
        return any(marker in stripped for marker in ("request", "approval", "workspace"))

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
        bridge = payload.get("_bridge") if isinstance(payload.get("_bridge"), dict) else {}
        if event_type in _RAW_TOOL_LIFECYCLE_EVENT_TYPES and (
            bridge.get("suppressRealtimeFlat") is True or bridge.get("suppressChatReplay") is True
        ):
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
            visible_arguments = self._public_tool_input(str(tool_name or ""), arguments)
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
                        **self._tool_presentation_payload(payload),
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
                        "input": visible_arguments,
                        **self._tool_presentation_payload(payload),
                    },
                    visibility=effective_visibility,
                )
            output_delta = self._public_tool_output_delta_text(self._tool_started_output_delta_text(payload))
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
                        **self._tool_presentation_payload(payload),
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
            return

        if event_type == "command.output":
            tool_use_id = payload.get("toolUseId")
            chunk = payload.get("chunk")
            if not tool_use_id or not isinstance(chunk, str) or not chunk:
                return
            visible_chunk = _visible_command_output_chunk(chunk)
            visible_chunk = self._public_tool_output_delta_text(visible_chunk)
            if not visible_chunk:
                return
            self._publish_chat_compat_event(
                session_id=session_id,
                task=task,
                event_type="content_delta",
                payload={
                    "toolUseId": tool_use_id,
                    "toolName": payload.get("toolName") or "run_command",
                    **self._tool_presentation_payload(payload),
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
            chunk = self._public_tool_output_delta_text(chunk)
            if not chunk:
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
                    **self._tool_presentation_payload(payload),
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
            result_payload = payload.get("result") if isinstance(payload.get("result"), dict) else {}
            if result_payload.get("_chatCompatSuppressToolResult") is True:
                return
            if (
                event_type == "tool.blocked"
                and (
                    str(payload.get("reason") or "").strip().lower() == "approval_required"
                    or str(result_payload.get("status") or "").strip().lower() == "approval_required"
                )
            ):
                return
            activity_deltas = self._tool_result_activity_delta_texts(payload)
            if activity_deltas and payload.get("toolName") != "run_command":
                for activity_delta in activity_deltas:
                    activity_delta = self._public_tool_output_delta_text(activity_delta)
                    if not activity_delta:
                        continue
                    if self._mark_tool_output_delta_seen(task["id"], tool_call_id, "activity", activity_delta):
                        continue
                    self._publish_chat_compat_event(
                        session_id=session_id,
                        task=task,
                        event_type="content_delta",
                        payload={
                            "toolUseId": tool_call_id,
                            "toolName": payload.get("toolName"),
                            **self._tool_presentation_payload(payload),
                            "toolOutput": activity_delta,
                            "outputStream": "activity",
                        },
                        visibility=effective_visibility,
                    )
            output_delta = self._public_tool_output_delta_text(self._tool_result_output_delta_text(payload))
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
                        **self._tool_presentation_payload(payload),
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
                    "content": self._public_tool_result_content(
                        tool_name=str(payload.get("toolName") or ""),
                        result=payload.get("result"),
                        target=str(payload.get("target") or ""),
                        summary=str(payload.get("resultSummary") or ""),
                        preview=payload.get("resultPreview") if isinstance(payload.get("resultPreview"), list) else None,
                    ),
                    "isError": event_type != "tool.completed",
                    **self._tool_presentation_payload(payload),
                    **({"resultSummary": payload.get("resultSummary")} if payload.get("resultSummary") else {}),
                    **({"resultPreview": payload.get("resultPreview")} if payload.get("resultPreview") else {}),
                    **({"durationMs": payload.get("durationMs")} if payload.get("durationMs") is not None else {}),
                },
                visibility=effective_visibility,
            )
            return

        if event_type == "approval.requested":
            request = payload.get("request")
            request_id = payload.get("approvalId")
            tool_name = payload.get("kind") or "approval"
            tool_use_id = payload.get("toolUseId") or payload.get("toolCallId")
            visible_request = self._public_permission_request_input(
                str(tool_name or ""),
                request if isinstance(request, dict) else {},
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
                    **({"toolUseId": tool_use_id} if tool_use_id else {}),
                    "input": visible_request,
                    "description": payload.get("summary"),
                    **self._tool_presentation_payload(payload),
                    "preview": self._public_permission_preview(payload.get("preview")),
                    "previewSections": (
                        visible_request.get("previewSections")
                        if isinstance(visible_request, dict) and isinstance(visible_request.get("previewSections"), list)
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
                tool_use_id = (
                    payload.get("toolUseId")
                    or payload.get("toolCallId")
                    or (approval_request.get("toolUseId") if isinstance(approval_request, dict) else None)
                    or (approval_request.get("toolCallId") if isinstance(approval_request, dict) else None)
                )
                presentation_source: dict[str, Any] = {}
                if isinstance(approval_request, dict):
                    presentation_source.update(approval_request)
                presentation_source.update(payload)
                visible_request = self._public_permission_request_input(
                    str(tool_name or ""),
                    request if isinstance(request, dict) else {},
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
                            **({"toolUseId": tool_use_id} if tool_use_id else {}),
                            "input": visible_request,
                            "description": payload.get("summary"),
                            **self._tool_presentation_payload(presentation_source),
                            "preview": self._public_permission_preview(payload.get("preview")),
                            "previewSections": (
                                visible_request.get("previewSections")
                                if isinstance(visible_request, dict) and isinstance(visible_request.get("previewSections"), list)
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
        payload = {
            "kind": approval.get("kind"),
            "request": request,
        }
        for key in _TOOL_PRESENTATION_PAYLOAD_KEYS:
            value = approval.get(key)
            if value in (None, "", [], {}) and isinstance(request, dict):
                value = request.get(key)
            if value not in (None, "", [], {}):
                payload[key] = value
        for key in ("toolCallId", "toolUseId", "toolName", "summary"):
            value = approval.get(key)
            if value in (None, "", [], {}) and isinstance(request, dict):
                value = request.get(key)
            if value not in (None, "", [], {}):
                payload[key] = value
        return payload

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
        public_request = PublishingMixin._public_permission_request_input("computer_use", request)
        action = (
            public_request.get("action")
            or public_request.get("permission")
            or public_request.get("summary")
            or public_request.get("description")
            or "computer use action"
        )
        permission = (
            public_request.get("permission")
            or public_request.get("summary")
            or public_request.get("description")
            or action
        )
        app_name = (
            public_request.get("app")
            or public_request.get("application")
            or public_request.get("target")
            or public_request.get("windowTitle")
            or public_request.get("window")
        )
        payload: dict[str, Any] = {
            "approvalId": approval_id,
            "requestId": approval_id,
            "status": status,
            "action": str(action),
            "permission": str(permission),
            "summary": str(permission),
            "request": public_request,
        }
        if app_name:
            payload["app"] = str(app_name)
        for key in ("target", "selector", "text", "x", "y", "direction", "amount"):
            value = public_request.get(key)
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
        self._terminal_pending_react_tool_result(
            session_id=task["sessionId"],
            task=task,
            status="cancelled",
            summary="Task was cancelled before the pending tool completed.",
            reason="The task was cancelled by the user.",
            error_code="TASK_CANCELLED",
        )
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
        if event_type in _CHAT_COMPAT_EVENT_TYPES:
            return "chat" if task_role == "root" else "trace"
        if event_type.startswith("provider."):
            return "trace"
        if event_type == "message.created":
            return "trace"
        if event_type == "message.completed":
            return "trace"
        if event_type in _RAW_TOOL_LIFECYCLE_EVENT_TYPES:
            return "trace" if task_role == "root" else "trace"
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
        raw_payload = deepcopy(payload)
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
        if event_type == "content_start" and payload.get("blockType") == "text":
            payload = dict(payload)
            active_msg_id = payload.get("messageId") or task.get("activeAssistantMessageId")
            block_state = self._open_chat_text_block(
                task=task,
                message_id=active_msg_id,
                content_block_id=payload.get("contentBlockId"),
            )
            if active_msg_id:
                payload["messageId"] = active_msg_id
            if block_state.get("contentBlockId"):
                payload["contentBlockId"] = block_state["contentBlockId"]
            if isinstance(block_state.get("blockIndex"), int):
                payload["blockIndex"] = block_state["blockIndex"]
        elif event_type in {"message_complete", "message.completed", "message.failed", "task.completed", "task.failed", "task.cancelled"}:
            self._close_chat_text_block(task=task, message_id=payload.get("messageId") if isinstance(payload, dict) else None)
        raw_payload = deepcopy(payload)
        effective_visibility = visibility or self._infer_event_visibility(event_type, task)
        if self._should_drop_event_after_terminal_task(
            task=task,
            event_type=event_type,
            payload=payload,
            effective_visibility=effective_visibility,
        ):
            return
        visible_payload = self._sanitize_visible_event_payload(event_type, payload, effective_visibility)
        if event_type == "content_start" and effective_visibility == "chat":
            self._remember_chat_content_start(task=task, payload=visible_payload)
        self._publish_goal_event_for_task_event(
            session_id=session_id,
            task=task,
            event_type=event_type,
            payload=visible_payload if effective_visibility in {"chat", "panel"} else raw_payload,
        )
        if event_type in {"task.completed", "task.failed", "task.cancelled"}:
            self._publish_chat_status(
                session_id=session_id,
                task=task,
                state="idle",
                payload=visible_payload if isinstance(visible_payload, dict) else payload,
                visibility="chat",
            )
        token_delta_payload: dict[str, Any] | None = None
        if event_type == "assistant.token":
            visible_payload = dict(visible_payload)
            raw_payload = dict(raw_payload)
            raw_bridge = raw_payload.get("_bridge")
            raw_payload["_bridge"] = {
                **(raw_bridge if isinstance(raw_bridge, dict) else {}),
                "internal": True,
                "derivedBy": "message.delta",
                "suppressRealtimeFlat": True,
                "suppressChatReplay": True,
            }
            active_msg_id = task.get("activeAssistantMessageId")
            if active_msg_id:
                visible_payload["messageId"] = active_msg_id
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
            block_state = self._current_chat_text_block(task=task, message_id=active_msg_id)
            if isinstance(block_state, dict):
                if block_state.get("contentBlockId"):
                    visible_payload["contentBlockId"] = block_state["contentBlockId"]
                if isinstance(block_state.get("blockIndex"), int):
                    visible_payload["blockIndex"] = block_state["blockIndex"]
            visible_payload["_chatCompat"] = True
            token_delta_payload = {**visible_payload}
            token_delta_payload.setdefault("messageId", active_msg_id or "")
        chat_compat_visibility = self._chat_compat_event_visibility(event_type, task, effective_visibility)
        if event_type in _RAW_TOOL_LIFECYCLE_EVENT_TYPES and self._should_drop_event_after_terminal_task(
            task=task,
            event_type=event_type,
            payload=payload,
            effective_visibility=chat_compat_visibility,
        ):
            pass
        else:
            self._publish_chat_compat_for_event(
                session_id=session_id,
                task=task,
                event_type=event_type,
                payload=visible_payload,
                effective_visibility=chat_compat_visibility,
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
            payload=visible_payload if raw_visibility in {"chat", "panel"} else raw_payload,
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
