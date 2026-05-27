"""Publishing Mixin â extracted from Orchestrator.

Handles event publishing, hook firing, runtime snapshots, and event visibility.
"""
from __future__ import annotations

import logging
from copy import deepcopy
from typing import Any

logger = logging.getLogger(__name__)

from ..context.token_budget import estimate_tokens
from ..models import RuntimeEvent
from ..services.command_background import cancel_background_commands


_CHAT_COMPAT_EVENT_TYPES = {
    "content_start",
    "content_delta",
    "thinking",
    "tool_use_complete",
    "tool_result",
    "permission_request",
    "message_complete",
    "status",
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
                "maxContextTokens": budget_stats.get("maxContextTokens"),
                "droppedSections": budget_stats.get("droppedSections"),
                "trimmedSections": budget_stats.get("trimmedSections"),
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

    def _publish_context_update(
        self,
        *,
        session_id: str,
        task: dict[str, Any],
        context: dict[str, Any],
        messages: list[dict[str, Any]],
    ) -> None:
        self._publish(
            session_id=session_id,
            task=task,
            event_type="task.updated",
            payload={
                "status": task.get("status"),
                "currentStep": task.get("currentStep"),
                "context": self._event_context_summary(context, messages=messages),
            },
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

    def _publish_chat_compat_event(
        self,
        *,
        session_id: str,
        task: dict[str, Any],
        event_type: str,
        payload: dict[str, Any],
        visibility: str = "chat",
    ) -> None:
        compat_payload = dict(payload)
        compat_payload["_chatCompat"] = True
        compat_payload["_bridge"] = {"skipTraceMirror": True}
        self._publish_event_raw(
            session_id=session_id,
            task=task,
            event_type=event_type,
            payload=compat_payload,
            visibility=visibility,
        )

    @staticmethod
    def _chat_compat_usage_from_payload(payload: dict[str, Any]) -> dict[str, Any] | None:
        usage = payload.get("usage")
        if isinstance(usage, dict):
            return dict(usage)
        raw = payload.get("raw")
        if isinstance(raw, dict) and isinstance(raw.get("usage"), dict):
            return dict(raw["usage"])
        return None

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

        if event_type == "assistant.token":
            delta = payload.get("delta")
            if not isinstance(delta, str) or not delta:
                return
            self._publish_chat_compat_event(
                session_id=session_id,
                task=task,
                event_type="content_delta",
                payload={
                    "messageId": active_msg_id,
                    "text": delta,
                    "step": payload.get("step"),
                },
                visibility=effective_visibility,
            )
            return

        if event_type == "tool.started":
            tool_call_id = payload.get("toolCallId")
            tool_name = payload.get("toolName")
            arguments = payload.get("arguments")
            self._publish_chat_compat_event(
                session_id=session_id,
                task=task,
                event_type="content_start",
                payload={
                    "blockType": "tool_use",
                    "toolName": tool_name,
                    "toolUseId": tool_call_id,
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
                    },
                    visibility=effective_visibility,
                )
            self._publish_chat_compat_event(
                session_id=session_id,
                task=task,
                event_type="status",
                payload={"state": "tool_executing", "verb": str(tool_name or "tool")},
                visibility=effective_visibility,
            )
            return

        if event_type in {"tool.completed", "tool.failed", "tool.blocked"}:
            tool_call_id = payload.get("toolCallId")
            if not tool_call_id:
                return
            self._publish_chat_compat_event(
                session_id=session_id,
                task=task,
                event_type="tool_result",
                payload={
                    "toolUseId": tool_call_id,
                    "toolName": payload.get("toolName"),
                    "content": payload.get("result"),
                    "isError": event_type != "tool.completed",
                },
                visibility=effective_visibility,
            )
            return

        if event_type == "approval.requested":
            request = payload.get("request")
            request_id = payload.get("approvalId")
            tool_name = payload.get("kind") or "approval"
            self._publish_chat_compat_event(
                session_id=session_id,
                task=task,
                event_type="permission_request",
                payload={
                    "requestId": request_id,
                    "toolName": tool_name,
                    "input": request if request is not None else {},
                    "description": payload.get("summary"),
                },
                visibility=effective_visibility,
            )
            self._publish_chat_compat_event(
                session_id=session_id,
                task=task,
                event_type="status",
                payload={"state": "permission_pending", "verb": str(tool_name)},
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
            self._publish_chat_compat_event(
                session_id=session_id,
                task=task,
                event_type="status",
                payload={"state": "idle"},
                visibility=effective_visibility,
            )
            return

        if event_type == "message.failed":
            self._publish_chat_compat_event(
                session_id=session_id,
                task=task,
                event_type="status",
                payload={"state": "idle"},
                visibility=effective_visibility,
            )

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

        - "chat": root-level user-facing output (messages, task status changes)
        - "panel": child/worker progress visible in task panel
        - "trace": fine-grained token/tool details for debugging
        """
        task_role = task.get("role", "root")
        if event_type in _CHAT_COMPAT_EVENT_TYPES:
            return "chat" if task_role == "root" else "trace"
        # Root streaming deltas are user-facing chat output; child deltas stay in trace.
        if event_type in {"assistant.token", "message.delta"}:
            return "chat" if task_role == "root" else "trace"
        # Trace-level: tool call details
        if event_type in {"tool.call.started", "tool.call.completed", "tool.call.failed"}:
            return "trace"
        # Panel-level: child task lifecycle events
        if event_type.startswith("collab."):
            return "panel"
        # Panel-level: child task events detected via role
        if task_role != "root":
            if event_type.startswith(("task.", "message.")):
                return "panel"
            return "trace"
        # Chat-level: everything else for root tasks
        return "chat"

    def _publish(self, session_id: str, task: dict[str, Any], event_type: str, payload: dict[str, Any], *, visibility: str | None = None) -> None:
        if event_type in _CHAT_COMPAT_EVENT_TYPES:
            payload = dict(payload)
            payload.setdefault("_chatCompat", True)
            payload.setdefault("_bridge", {"skipTraceMirror": True})
        if event_type.startswith("task."):
            payload = dict(payload)
            payload.setdefault("goal", task.get("goal"))
            payload.setdefault("acceptanceCriteria", list(task.get("acceptanceCriteria") or []))
            payload.setdefault("outOfScope", list(task.get("outOfScope") or []))
            payload.setdefault("currentStep", task.get("currentStep"))
        effective_visibility = visibility or self._infer_event_visibility(event_type, task)
        # Enrich streaming token events with messageId and emit unified message.delta
        if event_type == "assistant.token":
            payload = dict(payload)
            active_msg_id = task.get("activeAssistantMessageId")
            if active_msg_id:
                payload["messageId"] = active_msg_id
            payload["_chatCompat"] = True
            # Emit the new unified event name alongside the legacy one
            delta_payload = {**payload}
            delta_payload.setdefault("messageId", active_msg_id or "")
            delta_event = RuntimeEvent(
                event_id=self._store.new_id("evt"),
                session_id=session_id,
                task_id=task["id"],
                type="message.delta",
                ts=self._store.now(),
                payload=delta_payload,
                visibility=effective_visibility,
            )
            self._event_bus.publish(delta_event)
        self._publish_chat_compat_for_event(
            session_id=session_id,
            task=task,
            event_type=event_type,
            payload=payload,
            effective_visibility=effective_visibility,
        )
        event = RuntimeEvent(
            event_id=self._store.new_id("evt"),
            session_id=session_id,
            task_id=task["id"],
            type=event_type,
            ts=self._store.now(),
            payload=payload,
            visibility=effective_visibility,
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
