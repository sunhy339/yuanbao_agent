from __future__ import annotations

import json
from typing import Any


class HookStoreMixin:
    # -----------------------------------------------------------------------
    # Runtime Hooks CRUD — P1 Runtime Hooks
    # -----------------------------------------------------------------------

    VALID_HOOK_EVENTS = frozenset({
        # P0: Minimum Local Development Loop
        "before_task_start", "after_task_complete", "on_task_failed",
        "on_task_cancel", "on_task_pause", "on_approval_required",
        "before_tool_call", "after_tool_call",
        # P1: Agent Loop Control
        "before_provider_turn", "after_provider_turn",
        "before_compaction", "after_compaction",
        "on_task_resume",
        # P2: Advanced Integration
        "before_subagent_start", "after_subagent_complete", "on_subagent_failed",
        "before_worktree_create", "after_worktree_create",
        "before_worktree_merge", "after_worktree_merge",
        "before_patch_apply", "after_patch_apply",
        "on_memory_write", "on_context_snapshot",
    })
    VALID_HOOK_ACTIONS = frozenset({"audit_note", "notification", "run_command"})
    VALID_HOOK_FAILURE_MODES = frozenset({"warn", "block", "retry", "ignore", "ask_user"})

    def _serialize_hook(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": row["id"],
            "name": row["name"],
            "enabled": bool(row["enabled"]),
            "scope": row["scope"],
            "workspaceId": row["workspace_id"],
            "event": row["event"],
            "priority": row["priority"],
            "conditions": json.loads(row.get("conditions_json") or "{}"),
            "action": json.loads(row.get("action_json") or "{}"),
            "authority": json.loads(row.get("authority_json") or "{}"),
            "timeoutMs": row["timeout_ms"],
            "retry": json.loads(row.get("retry_json") or '{"maxAttempts":0}'),
            "onFailure": row["on_failure"],
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        }

    def _serialize_hook_execution(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": row["id"],
            "hookId": row["hook_id"],
            "event": row["event"],
            "sessionId": row.get("session_id"),
            "taskId": row.get("task_id"),
            "triggerEventId": row.get("trigger_event_id"),
            "conditionResult": row["condition_result"],
            "policyOutcome": row["policy_outcome"],
            "approvalId": row.get("approval_id"),
            "status": row["status"],
            "startedAt": row["started_at"],
            "finishedAt": row.get("finished_at"),
            "durationMs": row.get("duration_ms"),
            "inputSummary": row.get("input_summary"),
            "outputSummary": row.get("output_summary"),
            "errorSummary": row.get("error_summary"),
            "createdAt": row["created_at"],
        }

    def create_hook(self, params: dict[str, Any]) -> dict[str, Any]:
        name = self._require_non_empty(params, "name")
        workspace_id = self._require_non_empty(params, "workspaceId")
        event = self._require_non_empty(params, "event")
        if event not in self.VALID_HOOK_EVENTS:
            raise ValueError(f"Invalid hook event: {event}")
        action = self._dict_value(params.get("action", {}), "action")
        action_type = action.get("type", "")
        if action_type and action_type not in self.VALID_HOOK_ACTIONS:
            raise ValueError(f"Invalid hook action type: {action_type}")
        conditions = self._dict_value(params.get("conditions", {}), "conditions")
        authority = self._dict_value(params.get("authority", {}), "authority")
        priority = int(params.get("priority", 100))
        timeout_ms = int(params.get("timeoutMs", 60000))
        retry = self._dict_value(params.get("retry", {}), "retry")
        on_failure = params.get("onFailure", "warn")
        if on_failure not in self.VALID_HOOK_FAILURE_MODES:
            raise ValueError(f"Invalid on_failure: {on_failure}")
        now = self.now()
        hook_id = self.new_id("hook")
        self._conn.execute(
            """INSERT INTO runtime_hooks
               (id, name, enabled, scope, workspace_id, event, priority,
                conditions_json, action_json, authority_json, timeout_ms,
                retry_json, on_failure, created_at, updated_at)
               VALUES (?, ?, 1, 'workspace', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (hook_id, name, workspace_id, event, priority,
             json.dumps(conditions, ensure_ascii=False),
             json.dumps(action, ensure_ascii=False),
             json.dumps(authority, ensure_ascii=False),
             timeout_ms,
             json.dumps(retry, ensure_ascii=False),
             on_failure, now, now),
        )
        self._conn.commit()
        row = dict(self._conn.execute("SELECT * FROM runtime_hooks WHERE id = ?", (hook_id,)).fetchone())
        return {"hook": self._serialize_hook(row)}

    def update_hook(self, params: dict[str, Any]) -> dict[str, Any]:
        hook_id = self._require_non_empty(params, "hookId")
        existing = self._conn.execute("SELECT * FROM runtime_hooks WHERE id = ?", (hook_id,)).fetchone()
        if existing is None:
            raise ValueError(f"Hook not found: {hook_id}")
        updates: list[str] = []
        args: list[Any] = []
        for field, col in [("name", "name"), ("priority", "priority"), ("timeoutMs", "timeout_ms"), ("onFailure", "on_failure")]:
            if field in params:
                updates.append(f"{col} = ?")
                if field == "onFailure":
                    val = params[field]
                    if val not in self.VALID_HOOK_FAILURE_MODES:
                        raise ValueError(f"Invalid on_failure: {val}")
                    args.append(val)
                elif field in ("priority", "timeoutMs"):
                    args.append(int(params[field]))
                else:
                    args.append(params[field])
        if "enabled" in params:
            updates.append("enabled = ?")
            args.append(1 if params["enabled"] else 0)
        if "conditions" in params:
            updates.append("conditions_json = ?")
            args.append(json.dumps(self._dict_value(params["conditions"], "conditions"), ensure_ascii=False))
        if "action" in params:
            action = self._dict_value(params["action"], "action")
            action_type = action.get("type", "")
            if action_type and action_type not in self.VALID_HOOK_ACTIONS:
                raise ValueError(f"Invalid hook action type: {action_type}")
            updates.append("action_json = ?")
            args.append(json.dumps(action, ensure_ascii=False))
        if "authority" in params:
            updates.append("authority_json = ?")
            args.append(json.dumps(self._dict_value(params["authority"], "authority"), ensure_ascii=False))
        if "retry" in params:
            updates.append("retry_json = ?")
            args.append(json.dumps(self._dict_value(params["retry"], "retry"), ensure_ascii=False))
        if not updates:
            raise ValueError("No fields to update")
        updates.append("updated_at = ?")
        args.append(self.now())
        args.append(hook_id)
        self._conn.execute(f"UPDATE runtime_hooks SET {', '.join(updates)} WHERE id = ?", args)
        self._conn.commit()
        row = dict(self._conn.execute("SELECT * FROM runtime_hooks WHERE id = ?", (hook_id,)).fetchone())
        return {"hook": self._serialize_hook(row)}

    def delete_hook(self, params: dict[str, Any]) -> dict[str, Any]:
        hook_id = self._require_non_empty(params, "hookId")
        existing = self._conn.execute("SELECT * FROM runtime_hooks WHERE id = ?", (hook_id,)).fetchone()
        if existing is None:
            raise ValueError(f"Hook not found: {hook_id}")
        self._conn.execute("DELETE FROM runtime_hooks WHERE id = ?", (hook_id,))
        self._conn.commit()
        return {"deleted": True, "hookId": hook_id}

    def list_hooks(self, params: dict[str, Any]) -> dict[str, Any]:
        workspace_id = self._require_non_empty(params, "workspaceId")
        filters = ["workspace_id = ?"]
        args: list[Any] = [workspace_id]
        if params.get("event"):
            filters.append("event = ?")
            args.append(params["event"])
        where = " AND ".join(filters)
        rows = [dict(r) for r in self._conn.execute(
            f"SELECT * FROM runtime_hooks WHERE {where} ORDER BY priority ASC, created_at ASC", args
        ).fetchall()]
        return {"hooks": [self._serialize_hook(r) for r in rows]}

    def get_hook(self, params: dict[str, Any]) -> dict[str, Any]:
        hook_id = self._require_non_empty(params, "hookId")
        row = self._conn.execute("SELECT * FROM runtime_hooks WHERE id = ?", (hook_id,)).fetchone()
        if row is None:
            raise ValueError(f"Hook not found: {hook_id}")
        return {"hook": self._serialize_hook(dict(row))}

    def create_hook_execution(self, params: dict[str, Any]) -> dict[str, Any]:
        hook_id = self._require_non_empty(params, "hookId")
        event = self._require_non_empty(params, "event")
        now = self.now()
        exec_id = self.new_id("hookexec")
        self._conn.execute(
            """INSERT INTO hook_executions
               (id, hook_id, event, session_id, task_id, trigger_event_id,
                condition_result, policy_outcome, approval_id, status,
                started_at, finished_at, duration_ms,
                input_summary, output_summary, error_summary, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (exec_id, hook_id, event,
             params.get("sessionId"), params.get("taskId"), params.get("triggerEventId"),
             params.get("conditionResult", "matched"),
             params.get("policyOutcome", "allowed"),
             params.get("approvalId"),
             params.get("status", "pending"),
             params.get("startedAt", now), params.get("finishedAt"),
             params.get("durationMs"),
             params.get("inputSummary"), params.get("outputSummary"),
             params.get("errorSummary"), now),
        )
        self._conn.commit()
        row = dict(self._conn.execute("SELECT * FROM hook_executions WHERE id = ?", (exec_id,)).fetchone())
        return {"hookExecution": self._serialize_hook_execution(row)}

    def list_hook_executions(self, params: dict[str, Any]) -> dict[str, Any]:
        filters: list[str] = []
        args: list[Any] = []
        if params.get("taskId"):
            filters.append("task_id = ?")
            args.append(params["taskId"])
        if params.get("hookId"):
            filters.append("hook_id = ?")
            args.append(params["hookId"])
        limit = min(int(params.get("limit", 100)), 500)
        where = f" WHERE {' AND '.join(filters)}" if filters else ""
        rows = [dict(r) for r in self._conn.execute(
            f"SELECT * FROM hook_executions{where} ORDER BY created_at ASC LIMIT ?", args + [limit]
        ).fetchall()]
        return {"hookExecutions": [self._serialize_hook_execution(r) for r in rows]}
