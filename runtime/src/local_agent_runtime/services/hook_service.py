"""Runtime Hooks Service — P1 Runtime Hooks.

Dispatches configured hook actions when lifecycle events fire.
All actions pass through policy gates and create auditable execution records.
"""
from __future__ import annotations

import fnmatch
from typing import Any

from ..event_bus import EventBus
from ..models import RuntimeEvent
from ..store.sqlite_store import SQLiteStore


class HookService:
    """Finds, evaluates, and executes runtime hooks for lifecycle events."""

    def __init__(self, store: SQLiteStore, event_bus: EventBus) -> None:
        self._store = store
        self._event_bus = event_bus

    def invoke_hooks(self, event: str, context: dict[str, Any]) -> list[dict[str, Any]]:
        """Dispatch all matching hooks for a lifecycle event.

        Args:
            event: One of the VALID_HOOK_EVENTS.
            context: Must contain at least workspaceId. May also contain
                     sessionId, taskId, taskStatus, triggerEventId, changedFiles.

        Returns:
            List of hook execution records (serialized).
        """
        workspace_id = context.get("workspaceId", "")
        if not workspace_id:
            return []

        # Find enabled hooks for this workspace + event
        hooks_result = self._store.list_hooks({"workspaceId": workspace_id, "event": event})
        hooks = [h for h in hooks_result["hooks"] if h.get("enabled", True)]
        # Sort by priority ascending (already sorted by list_hooks)
        results: list[dict[str, Any]] = []

        for hook in hooks:
            # Evaluate conditions
            conditions = hook.get("conditions", {})
            condition_result = self._evaluate_conditions(conditions, context)

            if condition_result != "matched":
                # Record skipped execution
                exec_result = self._store.create_hook_execution({
                    "hookId": hook["id"],
                    "event": event,
                    "sessionId": context.get("sessionId"),
                    "taskId": context.get("taskId"),
                    "triggerEventId": context.get("triggerEventId"),
                    "conditionResult": condition_result,
                    "policyOutcome": "skipped",
                    "status": "skipped",
                    "inputSummary": f"Conditions not met: {condition_result}",
                })
                results.append(exec_result["hookExecution"])
                continue

            # Execute action
            action = hook.get("action", {})
            authority = hook.get("authority", {})
            on_failure = hook.get("onFailure", "warn")

            try:
                exec_result = self._execute_action(hook, action, authority, event, context)
                results.append(exec_result)
            except Exception as exc:
                if on_failure in ("warn", "ignore"):
                    exec_result = self._store.create_hook_execution({
                        "hookId": hook["id"],
                        "event": event,
                        "sessionId": context.get("sessionId"),
                        "taskId": context.get("taskId"),
                        "triggerEventId": context.get("triggerEventId"),
                        "conditionResult": "matched",
                        "policyOutcome": "error",
                        "status": "failed",
                        "inputSummary": str(action),
                        "errorSummary": str(exc),
                    })
                    results.append(exec_result["hookExecution"])
                else:
                    raise

        return results

    def _evaluate_conditions(self, conditions: dict[str, Any], context: dict[str, Any]) -> str:
        """Return 'matched' if all conditions pass, otherwise a reason string."""
        if not conditions:
            return "matched"

        # taskStatus condition
        task_statuses = conditions.get("taskStatus")
        if task_statuses:
            ctx_status = context.get("taskStatus", "")
            if ctx_status not in task_statuses:
                return f"taskStatus mismatch: {ctx_status} not in {task_statuses}"

        # changedFiles condition (glob matching)
        changed_patterns = conditions.get("changedFiles")
        if changed_patterns:
            ctx_files = context.get("changedFiles", [])
            if not ctx_files:
                return "changedFiles: no files in context"
            matched_any = False
            for f in ctx_files:
                for pattern in changed_patterns:
                    if fnmatch.fnmatch(f, pattern):
                        matched_any = True
                        break
                if matched_any:
                    break
            if not matched_any:
                return "changedFiles: no match"

        return "matched"

    def _execute_action(
        self,
        hook: dict[str, Any],
        action: dict[str, Any],
        authority: dict[str, Any],
        event: str,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """Execute a hook action and return the execution record."""
        action_type = action.get("type", "audit_note")

        if action_type == "audit_note":
            return self._execute_audit_note(hook, action, event, context)
        elif action_type == "notification":
            return self._execute_notification(hook, action, event, context)
        elif action_type == "run_command":
            return self._execute_run_command(hook, action, authority, event, context)
        else:
            raise ValueError(f"Unknown action type: {action_type}")

    def _execute_audit_note(
        self,
        hook: dict[str, Any],
        action: dict[str, Any],
        event: str,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """Emit a trace event as an audit note."""
        now = self._store.now()
        note = action.get("note", f"Hook {hook['name']} triggered on {event}")

        # Emit a trace event via EventBus
        runtime_event = RuntimeEvent(
            event_id=self._store.new_id("evt"),
            session_id=context.get("sessionId", ""),
            task_id=context.get("taskId", ""),
            type="hook.executed",
            ts=now,
            payload={
                "hookId": hook["id"],
                "hookName": hook["name"],
                "event": event,
                "actionType": "audit_note",
                "note": note,
            },
        )
        self._event_bus.publish(runtime_event)

        # Record execution
        exec_result = self._store.create_hook_execution({
            "hookId": hook["id"],
            "event": event,
            "sessionId": context.get("sessionId"),
            "taskId": context.get("taskId"),
            "triggerEventId": context.get("triggerEventId"),
            "conditionResult": "matched",
            "policyOutcome": "allowed",
            "status": "completed",
            "startedAt": now,
            "finishedAt": self._store.now(),
            "durationMs": self._store.now() - now,
            "inputSummary": note,
            "outputSummary": "Audit note emitted",
        })
        return exec_result["hookExecution"]

    def _execute_notification(
        self,
        hook: dict[str, Any],
        action: dict[str, Any],
        event: str,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """Emit an EventBus notification event."""
        now = self._store.now()
        message = action.get("message", f"Hook {hook['name']} triggered on {event}")

        runtime_event = RuntimeEvent(
            event_id=self._store.new_id("evt"),
            session_id=context.get("sessionId", ""),
            task_id=context.get("taskId", ""),
            type="hook.notification",
            ts=now,
            payload={
                "hookId": hook["id"],
                "hookName": hook["name"],
                "event": event,
                "message": message,
                "channel": action.get("channel", "default"),
            },
        )
        self._event_bus.publish(runtime_event)

        exec_result = self._store.create_hook_execution({
            "hookId": hook["id"],
            "event": event,
            "sessionId": context.get("sessionId"),
            "taskId": context.get("taskId"),
            "triggerEventId": context.get("triggerEventId"),
            "conditionResult": "matched",
            "policyOutcome": "allowed",
            "status": "completed",
            "startedAt": now,
            "finishedAt": self._store.now(),
            "durationMs": self._store.now() - now,
            "inputSummary": message,
            "outputSummary": "Notification emitted",
        })
        return exec_result["hookExecution"]

    def _execute_run_command(
        self,
        hook: dict[str, Any],
        action: dict[str, Any],
        authority: dict[str, Any],
        event: str,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """Handle run_command action with policy gating.

        For this first slice, we only record the dispatch decision —
        actual command execution is deferred to a later batch.
        """
        now = self._store.now()
        command = action.get("command", "")
        requires_approval = authority.get("requiresApproval", True)

        if requires_approval:
            # Record as pending — needs approval before execution
            exec_result = self._store.create_hook_execution({
                "hookId": hook["id"],
                "event": event,
                "sessionId": context.get("sessionId"),
                "taskId": context.get("taskId"),
                "triggerEventId": context.get("triggerEventId"),
                "conditionResult": "matched",
                "policyOutcome": "approval_required",
                "status": "pending",
                "startedAt": now,
                "inputSummary": f"Command: {command}",
                "outputSummary": "Awaiting approval",
            })
            return exec_result["hookExecution"]
        else:
            # Allowed — record as completed (no actual execution in this slice)
            exec_result = self._store.create_hook_execution({
                "hookId": hook["id"],
                "event": event,
                "sessionId": context.get("sessionId"),
                "taskId": context.get("taskId"),
                "triggerEventId": context.get("triggerEventId"),
                "conditionResult": "matched",
                "policyOutcome": "allowed",
                "status": "completed",
                "startedAt": now,
                "finishedAt": self._store.now(),
                "durationMs": self._store.now() - now,
                "inputSummary": f"Command: {command}",
                "outputSummary": "Command dispatch recorded (execution deferred)",
            })
            return exec_result["hookExecution"]
