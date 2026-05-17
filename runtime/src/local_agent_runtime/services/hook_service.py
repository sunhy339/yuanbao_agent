"""Runtime Hooks Service — P1/P2 Runtime Hooks.

Dispatches configured hook actions when lifecycle events fire.
All actions pass through policy gates and create auditable execution records.

P1 actions: audit_note, notification, run_command
P2 actions: webhook, memory_write, auto_verification_suggestion, external_sync
"""
from __future__ import annotations

import fnmatch
import json
import logging
import urllib.request
import urllib.error
from typing import Any

from ..event_bus import EventBus
from ..models import RuntimeEvent
from ..policy.permission_engine import PermissionEngine, PermissionRequest
from ..store.sqlite_store import SQLiteStore

logger = logging.getLogger(__name__)


class HookService:
    """Finds, evaluates, and executes runtime hooks for lifecycle events."""

    def __init__(
        self,
        store: SQLiteStore,
        event_bus: EventBus,
        permission_engine: PermissionEngine | None = None,
        memory_store: Any | None = None,
    ) -> None:
        self._store = store
        self._event_bus = event_bus
        self._permission_engine = permission_engine
        self._memory_store = memory_store

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
                exec_result = self._record_hook_execution(hook, {
                    "hookId": hook["id"],
                    "event": event,
                    "sessionId": context.get("sessionId"),
                    "taskId": context.get("taskId"),
                    "triggerEventId": context.get("triggerEventId"),
                    "conditionResult": condition_result,
                    "policyOutcome": "skipped",
                    "status": "skipped",
                    "inputSummary": f"Conditions not met: {condition_result}",
                }, context=context)
                results.append(exec_result)
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
                    exec_result = self._record_hook_execution(hook, {
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
                    }, context=context)
                    results.append(exec_result)
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
                file_path = f.get("path") if isinstance(f, dict) else f
                file_path = str(file_path or "")
                for pattern in changed_patterns:
                    if fnmatch.fnmatch(file_path, pattern):
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
        elif action_type == "webhook":
            return self._execute_webhook(hook, action, authority, event, context)
        elif action_type == "memory_write":
            return self._execute_memory_write(hook, action, event, context)
        elif action_type == "auto_verification_suggestion":
            return self._execute_auto_verification(hook, action, event, context)
        elif action_type == "external_sync":
            return self._execute_external_sync(hook, action, authority, event, context)
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

        # Record execution
        return self._record_hook_execution(hook, {
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
        }, context=context, payload_extra={"note": note})

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

        return self._record_hook_execution(hook, {
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
        }, context=context, event_type="hook.notification", payload_extra={
            "message": message,
            "channel": action.get("channel", "default"),
        })

    def _execute_run_command(
        self,
        hook: dict[str, Any],
        action: dict[str, Any],
        authority: dict[str, Any],
        event: str,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """Handle run_command action with policy gating.

        For this first slice, we only record the dispatch decision; actual
        command execution is deferred to a later batch.
        """
        now = self._store.now()
        command = action.get("command", "")
        requires_approval = authority.get("requiresApproval", True)
        permission = self._run_command_permission_decision(
            hook=hook,
            event=event,
            command=command,
            context=context,
        )
        if permission is not None and permission.decision == "deny":
            return self._record_hook_execution(hook, {
                "hookId": hook["id"],
                "event": event,
                "sessionId": context.get("sessionId"),
                "taskId": context.get("taskId"),
                "triggerEventId": context.get("triggerEventId"),
                "conditionResult": "matched",
                "policyOutcome": "denied",
                "status": "failed",
                "startedAt": now,
                "finishedAt": self._store.now(),
                "durationMs": self._store.now() - now,
                "inputSummary": f"Command: {command}",
                "errorSummary": permission.reason,
            }, context=context)
        permission_requires_approval = permission is not None and permission.decision == "approval_required"

        if requires_approval or permission_requires_approval:
            # Record as pending: needs approval before execution.
            reason = permission.reason if permission_requires_approval else "Hook authority requires approval"
            return self._record_hook_execution(hook, {
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
                "outputSummary": f"Awaiting approval: {reason}",
            }, context=context)
        else:
            # Allowed: record as deferred (no actual execution in this slice).
            return self._record_hook_execution(hook, {
                "hookId": hook["id"],
                "event": event,
                "sessionId": context.get("sessionId"),
                "taskId": context.get("taskId"),
                "triggerEventId": context.get("triggerEventId"),
                "conditionResult": "matched",
                "policyOutcome": "allowed",
                "status": "deferred",
                "startedAt": now,
                "finishedAt": self._store.now(),
                "durationMs": self._store.now() - now,
                "inputSummary": f"Command: {command}",
                "outputSummary": "Command dispatch recorded (execution deferred)",
            }, context=context)

    def _run_command_permission_decision(
        self,
        *,
        hook: dict[str, Any],
        event: str,
        command: str,
        context: dict[str, Any],
    ) -> Any | None:
        if self._permission_engine is None:
            return None
        hook_decision = self._permission_engine.evaluate(PermissionRequest(
            capability="hooksExecute",
            tool_name="hook.run_command",
            context={
                **context,
                "hookId": hook.get("id"),
                "hookName": hook.get("name"),
                "event": event,
                "command": command,
            },
        ))
        if hook_decision.decision != "allow":
            return hook_decision
        return self._permission_engine.evaluate(PermissionRequest(
            capability="runCommand",
            tool_name="hook.run_command",
            context={
                **context,
                "hookId": hook.get("id"),
                "event": event,
                "command": command,
            },
        ))

    def _hook_action_permission_decision(
        self,
        *,
        hook: dict[str, Any],
        event: str,
        tool_name: str,
        context: dict[str, Any],
        capability: str | None = None,
        extra_context: dict[str, Any] | None = None,
    ) -> Any | None:
        if self._permission_engine is None:
            return None
        base_context = {
            **context,
            **(extra_context or {}),
            "hookId": hook.get("id"),
            "hookName": hook.get("name"),
            "event": event,
        }
        hook_decision = self._permission_engine.evaluate(PermissionRequest(
            capability="hooksExecute",
            tool_name=tool_name,
            context=base_context,
        ))
        if hook_decision.decision != "allow":
            return hook_decision
        if capability is None:
            return None
        return self._permission_engine.evaluate(PermissionRequest(
            capability=capability,
            tool_name=tool_name,
            context=base_context,
        ))

    def _record_permission_outcome(
        self,
        hook: dict[str, Any],
        *,
        event: str,
        context: dict[str, Any],
        decision: Any,
        input_summary: str,
        now: int,
    ) -> dict[str, Any]:
        if decision.decision == "deny":
            return self._record_hook_execution(hook, {
                "hookId": hook["id"],
                "event": event,
                "sessionId": context.get("sessionId"),
                "taskId": context.get("taskId"),
                "triggerEventId": context.get("triggerEventId"),
                "conditionResult": "matched",
                "policyOutcome": "denied",
                "status": "failed",
                "startedAt": now,
                "finishedAt": self._store.now(),
                "durationMs": self._store.now() - now,
                "inputSummary": input_summary,
                "errorSummary": decision.reason,
            }, context=context)
        return self._record_hook_execution(hook, {
            "hookId": hook["id"],
            "event": event,
            "sessionId": context.get("sessionId"),
            "taskId": context.get("taskId"),
            "triggerEventId": context.get("triggerEventId"),
            "conditionResult": "matched",
            "policyOutcome": "approval_required",
            "status": "pending",
            "startedAt": now,
            "inputSummary": input_summary,
            "outputSummary": f"Awaiting approval: {decision.reason}",
        }, context=context)

    def _record_authority_approval_required(
        self,
        hook: dict[str, Any],
        *,
        event: str,
        context: dict[str, Any],
        input_summary: str,
        now: int,
    ) -> dict[str, Any]:
        return self._record_hook_execution(hook, {
            "hookId": hook["id"],
            "event": event,
            "sessionId": context.get("sessionId"),
            "taskId": context.get("taskId"),
            "triggerEventId": context.get("triggerEventId"),
            "conditionResult": "matched",
            "policyOutcome": "approval_required",
            "status": "pending",
            "startedAt": now,
            "inputSummary": input_summary,
            "outputSummary": "Awaiting approval: hook authority requires approval",
        }, context=context)

    def _record_hook_execution(
        self,
        hook: dict[str, Any],
        params: dict[str, Any],
        *,
        context: dict[str, Any],
        event_type: str = "hook.executed",
        payload_extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        exec_result = self._store.create_hook_execution(params)["hookExecution"]
        payload = {
            "hookExecutionId": exec_result["id"],
            "hookId": hook["id"],
            "hookName": hook.get("name", ""),
            "event": params.get("event"),
            "actionType": (hook.get("action") or {}).get("type", "audit_note"),
            "conditionResult": exec_result.get("conditionResult"),
            "policyOutcome": exec_result.get("policyOutcome"),
            "status": exec_result.get("status"),
        }
        if exec_result.get("errorSummary"):
            payload["errorSummary"] = exec_result["errorSummary"]
        if payload_extra:
            payload.update(payload_extra)
        runtime_event = RuntimeEvent(
            event_id=self._store.new_id("evt"),
            session_id=context.get("sessionId", ""),
            task_id=context.get("taskId", ""),
            type=event_type,
            ts=self._store.now(),
            payload=payload,
            visibility="trace",
        )
        self._event_bus.publish(runtime_event)
        return exec_result

    # -------------------------------------------------------------------
    # P2 Action Executors
    # -------------------------------------------------------------------

    def _execute_webhook(
        self,
        hook: dict[str, Any],
        action: dict[str, Any],
        authority: dict[str, Any],
        event: str,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """Fire an HTTP POST to an external webhook URL.

        The payload includes the hook metadata and the triggering context.
        Respects authority.requiresApproval for policy gating.
        """
        now = self._store.now()
        url = action.get("url", "")
        method = action.get("method", "POST").upper()
        headers = action.get("headers", {"Content-Type": "application/json"})
        timeout_ms = hook.get("timeoutMs", 5000)
        timeout_s = max(timeout_ms / 1000.0, 1.0)
        input_summary = f"Webhook {method} {url}"
        if not url:
            return self._record_hook_execution(hook, {
                "hookId": hook["id"],
                "event": event,
                "sessionId": context.get("sessionId"),
                "taskId": context.get("taskId"),
                "triggerEventId": context.get("triggerEventId"),
                "conditionResult": "matched",
                "policyOutcome": "error",
                "status": "failed",
                "startedAt": now,
                "finishedAt": self._store.now(),
                "durationMs": self._store.now() - now,
                "inputSummary": input_summary,
                "errorSummary": "Webhook URL is required",
            }, context=context)
        if not isinstance(headers, dict):
            return self._record_hook_execution(hook, {
                "hookId": hook["id"],
                "event": event,
                "sessionId": context.get("sessionId"),
                "taskId": context.get("taskId"),
                "triggerEventId": context.get("triggerEventId"),
                "conditionResult": "matched",
                "policyOutcome": "error",
                "status": "failed",
                "startedAt": now,
                "finishedAt": self._store.now(),
                "durationMs": self._store.now() - now,
                "inputSummary": input_summary,
                "errorSummary": "Webhook headers must be a JSON object",
            }, context=context)

        permission = self._hook_action_permission_decision(
            hook=hook,
            event=event,
            tool_name="hook.webhook",
            context=context,
            capability="network",
            extra_context={"url": url, "method": method},
        )
        if permission is not None and permission.decision != "allow":
            return self._record_permission_outcome(
                hook,
                event=event,
                context=context,
                decision=permission,
                input_summary=input_summary,
                now=now,
            )
        if authority.get("requiresApproval", False):
            return self._record_authority_approval_required(
                hook,
                event=event,
                context=context,
                input_summary=input_summary,
                now=now,
            )

        # Build the outgoing payload
        payload: dict[str, Any] = {
            "hookId": hook["id"],
            "hookName": hook.get("name", ""),
            "event": event,
            "timestamp": now,
            "context": {
                "workspaceId": context.get("workspaceId", ""),
                "sessionId": context.get("sessionId"),
                "taskId": context.get("taskId"),
                "taskStatus": context.get("taskStatus"),
            },
        }
        if action.get("extraPayload"):
            payload["extra"] = action["extraPayload"]

        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=body, method=method)
        for k, v in headers.items():
            req.add_header(k, v)

        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                status_code = resp.status
                resp_body = resp.read(4096).decode("utf-8", errors="replace")
            return self._record_hook_execution(hook, {
                "hookId": hook["id"], "event": event,
                "sessionId": context.get("sessionId"), "taskId": context.get("taskId"),
                "triggerEventId": context.get("triggerEventId"),
                "conditionResult": "matched", "policyOutcome": "allowed",
                "status": "completed", "startedAt": now,
                "finishedAt": self._store.now(),
                "durationMs": self._store.now() - now,
                "inputSummary": input_summary,
                "outputSummary": f"HTTP {status_code}: {resp_body[:200]}",
            }, context=context, event_type="hook.webhook", payload_extra={
                "url": url, "statusCode": status_code,
            })
        except Exception as exc:
            logger.warning("Webhook %s failed for hook %s: %s", url, hook["id"], exc)
            return self._record_hook_execution(hook, {
                "hookId": hook["id"], "event": event,
                "sessionId": context.get("sessionId"), "taskId": context.get("taskId"),
                "triggerEventId": context.get("triggerEventId"),
                "conditionResult": "matched", "policyOutcome": "error",
                "status": "failed", "startedAt": now,
                "finishedAt": self._store.now(),
                "durationMs": self._store.now() - now,
                "inputSummary": input_summary,
                "errorSummary": str(exc),
            }, context=context)

    def _execute_memory_write(
        self,
        hook: dict[str, Any],
        action: dict[str, Any],
        event: str,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """Write a structured memory entry via the MemoryStore.

        Action config:
          kind: "working" | "session" | "long_term" | "semantic" (default: "session")
          content: str or a template with {event}, {taskId}, {taskStatus} placeholders
          keywords: list[str] for semantic indexing
        """
        now = self._store.now()
        kind_str = action.get("kind", "session")
        content_template = action.get("content", f"Hook {hook.get('name', '')} fired on {event}")
        keywords = action.get("keywords", [])
        input_summary = f"Memory write: {kind_str}"

        # Template substitution
        content = content_template
        for key, val in [
            ("event", event),
            ("taskId", context.get("taskId", "")),
            ("taskStatus", context.get("taskStatus", "")),
            ("sessionId", context.get("sessionId", "")),
            ("workspaceId", context.get("workspaceId", "")),
        ]:
            content = content.replace("{" + key + "}", str(val))

        permission = self._hook_action_permission_decision(
            hook=hook,
            event=event,
            tool_name="hook.memory_write",
            context=context,
            capability="memoryWrite",
            extra_context={"kind": kind_str},
        )
        if permission is not None and permission.decision != "allow":
            return self._record_permission_outcome(
                hook,
                event=event,
                context=context,
                decision=permission,
                input_summary=input_summary,
                now=now,
            )

        if self._memory_store is None:
            return self._record_hook_execution(hook, {
                "hookId": hook["id"], "event": event,
                "sessionId": context.get("sessionId"), "taskId": context.get("taskId"),
                "triggerEventId": context.get("triggerEventId"),
                "conditionResult": "matched", "policyOutcome": "skipped",
                "status": "skipped", "startedAt": now,
                "finishedAt": self._store.now(),
                "durationMs": self._store.now() - now,
                "inputSummary": input_summary,
                "outputSummary": "Skipped: memory_store not available",
            }, context=context)

        try:
            from ..memory.types import MemoryKind
            kind = MemoryKind(kind_str)
        except ValueError:
            return self._record_hook_execution(hook, {
                "hookId": hook["id"], "event": event,
                "sessionId": context.get("sessionId"), "taskId": context.get("taskId"),
                "triggerEventId": context.get("triggerEventId"),
                "conditionResult": "matched", "policyOutcome": "error",
                "status": "failed", "startedAt": now,
                "finishedAt": self._store.now(),
                "durationMs": self._store.now() - now,
                "inputSummary": input_summary,
                "errorSummary": f"Invalid memory kind: {kind_str}",
            }, context=context)

        try:
            entry = self._memory_store.create(
                kind=kind,
                content=content,
                session_id=context.get("sessionId"),
                workspace_id=context.get("workspaceId"),
                keywords=keywords,
                metadata={"source": "hook", "hookId": hook["id"], "event": event},
            )
            return self._record_hook_execution(hook, {
                "hookId": hook["id"], "event": event,
                "sessionId": context.get("sessionId"), "taskId": context.get("taskId"),
                "triggerEventId": context.get("triggerEventId"),
                "conditionResult": "matched", "policyOutcome": "allowed",
                "status": "completed", "startedAt": now,
                "finishedAt": self._store.now(),
                "durationMs": self._store.now() - now,
                "inputSummary": input_summary,
                "outputSummary": f"Created memory entry {entry.id}: {content[:200]}",
            }, context=context, event_type="hook.memory_write", payload_extra={
                "memoryEntryId": entry.id, "memoryKind": kind_str,
            })
        except Exception as exc:
            logger.warning("Memory write failed for hook %s: %s", hook["id"], exc)
            return self._record_hook_execution(hook, {
                "hookId": hook["id"], "event": event,
                "sessionId": context.get("sessionId"), "taskId": context.get("taskId"),
                "triggerEventId": context.get("triggerEventId"),
                "conditionResult": "matched", "policyOutcome": "error",
                "status": "failed", "startedAt": now,
                "finishedAt": self._store.now(),
                "durationMs": self._store.now() - now,
                "inputSummary": input_summary,
                "errorSummary": str(exc),
            }, context=context)

    def _execute_auto_verification(
        self,
        hook: dict[str, Any],
        action: dict[str, Any],
        event: str,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """Emit a verification suggestion event for the orchestrator to pick up.

        This does not run verification itself — it records a structured suggestion
        that the ReAct loop or task orchestrator can consume.  Action config:
          checks: list of check descriptors (e.g. ["tests_pass", "lint_clean", "build_ok"])
          suggestion: optional human-readable suggestion text
        """
        now = self._store.now()
        checks = action.get("checks", [])
        suggestion = action.get("suggestion", f"Auto-verification suggested after {event}")
        input_summary = f"Verification suggestion: {checks}"
        permission = self._hook_action_permission_decision(
            hook=hook,
            event=event,
            tool_name="hook.auto_verification_suggestion",
            context=context,
        )
        if permission is not None and permission.decision != "allow":
            return self._record_permission_outcome(
                hook,
                event=event,
                context=context,
                decision=permission,
                input_summary=input_summary,
                now=now,
            )

        return self._record_hook_execution(hook, {
            "hookId": hook["id"], "event": event,
            "sessionId": context.get("sessionId"), "taskId": context.get("taskId"),
            "triggerEventId": context.get("triggerEventId"),
            "conditionResult": "matched", "policyOutcome": "allowed",
            "status": "completed", "startedAt": now,
            "finishedAt": self._store.now(),
            "durationMs": self._store.now() - now,
            "inputSummary": input_summary,
            "outputSummary": suggestion,
        }, context=context, event_type="hook.auto_verification_suggestion", payload_extra={
            "checks": checks, "suggestion": suggestion,
        })

    def _execute_external_sync(
        self,
        hook: dict[str, Any],
        action: dict[str, Any],
        authority: dict[str, Any],
        event: str,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """Record an external sync dispatch (actual HTTP call deferred to executor).

        Action config:
          target: str — identifier of the external system (e.g. "github", "jira", "slack")
          operation: str — e.g. "create_issue", "post_message", "update_status"
          mapping: dict — field mapping from context to external payload
        This slice records the sync intent; actual execution happens in a later batch.
        """
        now = self._store.now()
        target = action.get("target", "")
        operation = action.get("operation", "")
        mapping = action.get("mapping", {})
        input_summary = f"External sync: {target}/{operation}"

        # Policy gate
        permission = self._hook_action_permission_decision(
            hook=hook,
            event=event,
            tool_name="hook.external_sync",
            context=context,
            capability="network",
            extra_context={"target": target, "operation": operation},
        )
        if permission is not None and permission.decision != "allow":
            return self._record_permission_outcome(
                hook,
                event=event,
                context=context,
                decision=permission,
                input_summary=input_summary,
                now=now,
            )
        if authority.get("requiresApproval", False):
            return self._record_authority_approval_required(
                hook,
                event=event,
                context=context,
                input_summary=input_summary,
                now=now,
            )

        return self._record_hook_execution(hook, {
            "hookId": hook["id"], "event": event,
            "sessionId": context.get("sessionId"), "taskId": context.get("taskId"),
            "triggerEventId": context.get("triggerEventId"),
            "conditionResult": "matched", "policyOutcome": "allowed",
            "status": "deferred", "startedAt": now,
            "finishedAt": self._store.now(),
            "durationMs": self._store.now() - now,
            "inputSummary": input_summary,
            "outputSummary": "Sync dispatch recorded (execution deferred)",
        }, context=context, event_type="hook.external_sync", payload_extra={
            "target": target, "operation": operation, "mapping": mapping,
        })
