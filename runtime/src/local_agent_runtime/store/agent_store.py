from __future__ import annotations

import json
import re
import sqlite3
import time
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any

class AgentStoreMixin:
    def upsert_agent_worker(self, params: dict[str, Any]) -> dict[str, Any]:
        worker_id = self._optional_string(params, "workerId") or self._optional_string(params, "id") or self.new_id("agent")
        name = self._require_non_empty(params, "name")
        role = self._require_non_empty(params, "role")
        status = self._normalize_agent_worker_status(params.get("status", "idle"))
        current_task_id = self._optional_string(params, "currentTaskId")
        capabilities = self._string_list(params.get("capabilities", []), "capabilities")
        metadata = self._dict_value(params.get("metadata", {}), "metadata")
        now = self.now()
        self._conn.execute(
            """
            INSERT INTO agent_workers (
                id, name, role, status, current_task_id, capabilities_json, metadata_json,
                created_at, updated_at, last_heartbeat_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name,
                role = excluded.role,
                status = excluded.status,
                current_task_id = excluded.current_task_id,
                capabilities_json = excluded.capabilities_json,
                metadata_json = excluded.metadata_json,
                updated_at = excluded.updated_at,
                last_heartbeat_at = excluded.last_heartbeat_at
            """,
            (
                worker_id,
                name,
                role,
                status,
                current_task_id,
                json.dumps(capabilities, ensure_ascii=False),
                json.dumps(metadata, ensure_ascii=False, sort_keys=True),
                now,
                now,
                now,
            ),
        )
        self._conn.commit()
        return {"worker": self.require_agent_worker(worker_id)}

    def require_agent_worker(self, worker_id: str) -> dict[str, Any]:
        row = self._conn.execute("SELECT * FROM agent_workers WHERE id = ?", (worker_id,)).fetchone()
        if row is None:
            raise ValueError(f"Agent worker not found: {worker_id}")
        return self._serialize_agent_worker(dict(row))

    def get_agent_worker(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"worker": self.require_agent_worker(self._require_non_empty(params, "workerId"))}

    def list_agent_workers(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        status = self._optional_string(params or {}, "status")
        if status:
            rows = self._conn.execute(
                "SELECT * FROM agent_workers WHERE status = ? ORDER BY updated_at DESC",
                (status,),
            ).fetchall()
        else:
            rows = self._conn.execute("SELECT * FROM agent_workers ORDER BY updated_at DESC").fetchall()
        return {"workers": [self._serialize_agent_worker(dict(row)) for row in rows]}

    def heartbeat_agent_worker(self, params: dict[str, Any]) -> dict[str, Any]:
        worker_id = self._require_non_empty(params, "workerId")
        current_task_id = self._optional_string(params, "currentTaskId")
        status = self._normalize_agent_worker_status(params.get("status", "idle" if current_task_id is None else "busy"))
        now = self.now()
        cursor = self._conn.execute(
            """
            UPDATE agent_workers
            SET status = ?, current_task_id = ?, last_heartbeat_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (status, current_task_id, now, now, worker_id),
        )
        if cursor.rowcount == 0:
            raise ValueError(f"Agent worker not found: {worker_id}")
        self._conn.commit()
        return {"worker": self.require_agent_worker(worker_id)}

    def send_agent_message(self, params: dict[str, Any]) -> dict[str, Any]:
        sender_worker_id = self._require_non_empty(params, "senderWorkerId")
        self.require_agent_worker(sender_worker_id)
        recipient_worker_id = self._optional_string(params, "recipientWorkerId")
        if recipient_worker_id is not None:
            self.require_agent_worker(recipient_worker_id)
        task_id = self._optional_string(params, "taskId")
        if task_id is not None:
            self.require_collaboration_task(task_id)
        kind = self._normalize_agent_message_kind(params.get("kind", "note"))
        body = self._require_non_empty(params, "body")
        payload = self._dict_value(params.get("payload", {}), "payload")
        message_id = self.new_id("msg")
        now = self.now()
        self._conn.execute(
            """
            INSERT INTO agent_messages (
                id, sender_worker_id, recipient_worker_id, task_id, kind, body, payload_json, created_at, read_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)
            """,
            (
                message_id,
                sender_worker_id,
                recipient_worker_id,
                task_id,
                kind,
                body,
                json.dumps(payload, ensure_ascii=False, sort_keys=True),
                now,
            ),
        )
        self._conn.commit()
        return {"message": self.require_agent_message(message_id)}

    def require_agent_message(self, message_id: str) -> dict[str, Any]:
        row = self._conn.execute("SELECT * FROM agent_messages WHERE id = ?", (message_id,)).fetchone()
        if row is None:
            raise ValueError(f"Agent message not found: {message_id}")
        return self._serialize_agent_message(dict(row))

    def list_agent_messages(self, params: dict[str, Any]) -> dict[str, Any]:
        clauses: list[str] = []
        values: list[Any] = []
        for param_name, column_name in (
            ("taskId", "task_id"),
            ("senderWorkerId", "sender_worker_id"),
            ("recipientWorkerId", "recipient_worker_id"),
            ("kind", "kind"),
        ):
            value = self._optional_string(params, param_name)
            if value is not None:
                clauses.append(f"{column_name} = ?")
                values.append(value)
        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        limit = int(params.get("limit", 100))
        rows = self._conn.execute(
            f"""
            SELECT *
            FROM agent_messages
            {where_sql}
            ORDER BY created_at DESC
            LIMIT ?
            """,
            [*values, max(1, min(limit, 500))],
        ).fetchall()
        return {"messages": [self._serialize_agent_message(dict(row)) for row in rows]}

    # ------------------------------------------------------------------
    # ProviderTurn
    # ------------------------------------------------------------------

    def create_provider_turn(
        self,
        *,
        task_id: str,
        session_id: str,
        turn_index: int,
        model: str | None = None,
        request_message_count: int | None = None,
        request_tool_count: int | None = None,
        request_token_estimate: int | None = None,
    ) -> dict[str, Any]:
        turn_id = self.new_id("pt")
        now = self.now()
        self._conn.execute(
            """
            INSERT INTO provider_turns
                (id, task_id, session_id, turn_index, model, status,
                 request_message_count, request_tool_count, request_token_estimate,
                 created_at)
            VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)
            """,
            (turn_id, task_id, session_id, turn_index, model,
             request_message_count, request_tool_count, request_token_estimate,
             now),
        )
        self._conn.commit()
        row = self._conn.execute("SELECT * FROM provider_turns WHERE id = ?", (turn_id,)).fetchone()
        return dict(row) if row else {}

    def complete_provider_turn(
        self,
        *,
        turn_id: str,
        finish_reason: str | None = None,
        usage: dict | None = None,
        tool_call_count: int | None = None,
        snapshot_id: str | None = None,
        turn_decision: str | None = None,
        thought_summary: str | None = None,
    ) -> dict[str, Any]:
        now = self.now()
        usage_json = json.dumps(usage, ensure_ascii=False) if usage else None
        self._conn.execute(
            """
            UPDATE provider_turns
            SET status = 'completed',
                response_finish_reason = ?,
                response_usage_json = ?,
                response_tool_call_count = ?,
                context_snapshot_id = ?,
                turn_decision = ?,
                thought_summary = ?,
                completed_at = ?
            WHERE id = ?
            """,
            (finish_reason, usage_json, tool_call_count, snapshot_id,
             turn_decision, thought_summary, now, turn_id),
        )
        self._conn.commit()
        row = self._conn.execute("SELECT * FROM provider_turns WHERE id = ?", (turn_id,)).fetchone()
        return dict(row) if row else {}

    def fail_provider_turn(
        self,
        *,
        turn_id: str,
        error_summary: str,
    ) -> dict[str, Any]:
        now = self.now()
        self._conn.execute(
            """
            UPDATE provider_turns
            SET status = 'failed',
                error_summary = ?,
                completed_at = ?
            WHERE id = ?
            """,
            (error_summary[:500], now, turn_id),
        )
        self._conn.commit()
        row = self._conn.execute("SELECT * FROM provider_turns WHERE id = ?", (turn_id,)).fetchone()
        return dict(row) if row else {}

    def list_provider_turns(self, task_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM provider_turns WHERE task_id = ? ORDER BY turn_index",
            (task_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # ContextSnapshot
    # ------------------------------------------------------------------

    def create_context_snapshot(
        self,
        *,
        session_id: str,
        task_id: str,
        provider_turn_id: str | None = None,
        included_sections: list[str] | None = None,
        trimmed_sections: list[dict] | None = None,
        dropped_sections: list[dict] | None = None,
        recent_message_ids: list[str] | None = None,
        summarized_message_ids: list[str] | None = None,
        memory_ids: list[str] | None = None,
        supplement_inbox_ids: list[str] | None = None,
        tool_count: int | None = None,
        skill_id: str | None = None,
        token_estimate: int | None = None,
        max_context_tokens: int | None = None,
        prompt_layers: list[dict[str, Any]] | None = None,
        tool_policy_decision: dict[str, Any] | None = None,
        role_snapshot: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        snap_id = self.new_id("cs")
        now = self.now()
        self._conn.execute(
            """
            INSERT INTO context_snapshots
                (id, session_id, task_id, provider_turn_id,
                 included_sections_json, trimmed_sections_json, dropped_sections_json,
                 recent_message_ids_json, summarized_message_ids_json,
                 memory_ids_json, supplement_inbox_ids_json,
                 tool_count, skill_id, token_estimate, created_at,
                 max_context_tokens, prompt_layers_json,
                 tool_policy_decision_json, role_snapshot_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snap_id, session_id, task_id, provider_turn_id,
                json.dumps(included_sections or [], ensure_ascii=False),
                json.dumps(trimmed_sections or [], ensure_ascii=False),
                json.dumps(dropped_sections or [], ensure_ascii=False),
                json.dumps(recent_message_ids or [], ensure_ascii=False),
                json.dumps(summarized_message_ids or [], ensure_ascii=False),
                json.dumps(memory_ids or [], ensure_ascii=False),
                json.dumps(supplement_inbox_ids or [], ensure_ascii=False),
                tool_count, skill_id, token_estimate, now,
                max_context_tokens,
                json.dumps(prompt_layers or [], ensure_ascii=False),
                json.dumps(tool_policy_decision or {}, ensure_ascii=False, sort_keys=True),
                json.dumps(role_snapshot or {}, ensure_ascii=False, sort_keys=True),
            ),
        )
        self._conn.commit()
        row = self._conn.execute("SELECT * FROM context_snapshots WHERE id = ?", (snap_id,)).fetchone()
        return dict(row) if row else {}

    def get_context_snapshot(self, snapshot_id: str) -> dict[str, Any] | None:
        row = self._conn.execute("SELECT * FROM context_snapshots WHERE id = ?", (snapshot_id,)).fetchone()
        return dict(row) if row else None

    def list_context_snapshots(self, task_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM context_snapshots WHERE task_id = ? ORDER BY created_at",
            (task_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def _serialize_context_snapshot(self, row: dict[str, Any]) -> dict[str, Any]:
        """Serialize a context_snapshots row for API responses."""
        return {
            "id": row["id"],
            "taskId": row["task_id"],
            "sessionId": row["session_id"],
            "providerTurnId": row.get("provider_turn_id"),
            "tokenEstimate": row.get("token_estimate"),
            "maxContextTokens": row.get("max_context_tokens"),
            "includedSections": json.loads(row.get("included_sections_json") or "[]"),
            "trimmedSections": json.loads(row.get("trimmed_sections_json") or "[]"),
            "droppedSections": json.loads(row.get("dropped_sections_json") or "[]"),
            "memoryIds": json.loads(row.get("memory_ids_json") or "[]"),
            "toolCount": row.get("tool_count"),
            "skillId": row.get("skill_id"),
            "promptLayers": json.loads(row.get("prompt_layers_json") or "[]"),
            "toolPolicyDecision": json.loads(row.get("tool_policy_decision_json") or "{}"),
            "roleSnapshot": json.loads(row.get("role_snapshot_json") or "{}"),
            "createdAt": row["created_at"],
        }

    def get_context_budget(self, params: dict[str, Any]) -> dict[str, Any]:
        """Return context budget summary for a task or session."""
        task_id = params.get("taskId")
        session_id = params.get("sessionId")

        # 1. Resolve maxContextTokens from config
        config = self.get_config({})["config"]
        provider_config = config.get("provider") or {}
        max_context = provider_config.get("maxContextTokens") or config.get("maxContextTokens")
        if max_context is not None:
            try:
                max_context = max(1, int(max_context))
            except (TypeError, ValueError):
                max_context = 256000
        else:
            max_context = 256000

        # 2. Get latest context snapshot
        snapshot = None
        if task_id:
            rows = self._conn.execute(
                "SELECT * FROM context_snapshots WHERE task_id = ? ORDER BY created_at DESC LIMIT 1",
                (task_id,),
            ).fetchall()
            snapshot = dict(rows[0]) if rows else None
        elif session_id:
            rows = self._conn.execute(
                "SELECT * FROM context_snapshots WHERE session_id = ? ORDER BY created_at DESC LIMIT 1",
                (session_id,),
            ).fetchall()
            snapshot = dict(rows[0]) if rows else None

        # 3. Get compaction records for the session
        effective_session = session_id
        if not effective_session and task_id:
            task_row = self._conn.execute(
                "SELECT session_id FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()
            if task_row:
                effective_session = task_row["session_id"]
        compactions: list[dict[str, Any]] = []
        if effective_session:
            rows = self._conn.execute(
                "SELECT * FROM compaction_records WHERE session_id = ? ORDER BY created_at DESC LIMIT 20",
                (effective_session,),
            ).fetchall()
            compactions = [dict(r) for r in rows]

        # 4. Get all snapshots for historical trend (if task_id)
        trend: list[dict[str, Any]] = []
        if task_id:
            rows = self._conn.execute(
                "SELECT id, token_estimate, created_at FROM context_snapshots WHERE task_id = ? ORDER BY created_at ASC",
                (task_id,),
            ).fetchall()
            trend = [
                {"snapshotId": r["id"], "tokenEstimate": r["token_estimate"], "createdAt": r["created_at"]}
                for r in rows
            ]

        # 5. Build result
        prompt_layers: list[dict[str, Any]] = []
        if snapshot and snapshot.get("prompt_layers_json"):
            try:
                prompt_layers = json.loads(snapshot["prompt_layers_json"])
            except (json.JSONDecodeError, TypeError):
                pass

        return {
            "maxContextTokens": max_context,
            "latestSnapshot": self._serialize_context_snapshot(snapshot) if snapshot else None,
            "compactions": compactions,
            "tokenTrend": trend,
            "promptLayers": prompt_layers,
        }

    # ------------------------------------------------------------------
    # Autonomy Run Report
    # ------------------------------------------------------------------

    def get_autonomy_report(self, params: dict[str, Any]) -> dict[str, Any]:
        """Return a comprehensive autonomy report for a task.

        Aggregates task, metrics, approvals, patches, commands, compactions,
        subagents, artifacts, decisions, memory recall, context budget,
        autonomy profile, and soul profile into a single report.
        """
        task_id = self._require_non_empty(params, "taskId")

        # 1. Task
        task_row = self._conn.execute(
            "SELECT * FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()
        if task_row is None:
            raise ValueError(f"Task not found: {task_id}")
        task = self._serialize_task(dict(task_row))
        session_id = task["sessionId"]

        # 2. Config (autonomy + soul profiles)
        config = self.get_config({})["config"]
        autonomy_profile = self._active_profile_from_config(config, "autonomy")
        soul_profile = self._active_profile_from_config(config, "agentSoul")

        # 3. Metrics
        metrics_row = self._conn.execute(
            "SELECT * FROM task_metrics WHERE task_id = ?", (task_id,)
        ).fetchone()
        metrics = dict(metrics_row) if metrics_row else None

        # 4. Routing decision from task + proposal
        routing = task.get("routing") or {}
        routing_proposal = None
        if task_id:
            prop_rows = self._conn.execute(
                "SELECT * FROM proposal_records WHERE task_id = ? AND kind = 'routing_strategy' ORDER BY created_at DESC LIMIT 1",
                (task_id,),
            ).fetchall()
            if prop_rows:
                routing_proposal = self._serialize_proposal(dict(prop_rows[0]))

        # 5. Decision trace events
        decision_rows = self._conn.execute(
            "SELECT * FROM trace_events WHERE task_id = ? AND type LIKE 'agent.decision.%' ORDER BY created_at ASC, sequence ASC",
            (task_id,),
        ).fetchall()
        decisions = [self._serialize_trace_event(dict(r)) for r in decision_rows]

        # 6. Approvals
        approval_rows = self._conn.execute(
            "SELECT * FROM approvals WHERE task_id = ? ORDER BY created_at ASC",
            (task_id,),
        ).fetchall()
        approvals = [self._serialize_approval(dict(r)) for r in approval_rows]

        # 7. Policy gate outcomes (aggregated from approvals)
        gate_outcomes: dict[str, int] = {
            "allowed": 0,
            "approvalRequired": 0,
            "blocked": 0,
            "deferred": 0,
            "sandboxed": 0,
        }
        for a in approvals:
            decision = a.get("decision") or ""
            if decision == "approved":
                gate_outcomes["allowed"] += 1
            elif decision == "rejected":
                gate_outcomes["blocked"] += 1
            elif decision in ("deferred", "pending"):
                gate_outcomes["deferred"] += 1
            elif decision == "sandboxed":
                gate_outcomes["sandboxed"] += 1
            else:
                # approval_required means decision is still None (pending)
                gate_outcomes["approvalRequired"] += 1
        # Count pending approvals as approvalRequired
        pending = [a for a in approvals if not a.get("decision")]
        gate_outcomes["approvalRequired"] = len(pending)

        # 8. Patches
        patch_rows = self._conn.execute(
            "SELECT * FROM patches WHERE task_id = ? ORDER BY created_at ASC",
            (task_id,),
        ).fetchall()
        patches = [self._serialize_patch(dict(r)) for r in patch_rows]

        # 9. Commands
        command_rows = self._conn.execute(
            "SELECT * FROM command_logs WHERE task_id = ? ORDER BY started_at ASC",
            (task_id,),
        ).fetchall()
        commands = [self._serialize_command_log(dict(r)) for r in command_rows]

        # 10. Compactions (by session)
        compaction_rows = self._conn.execute(
            "SELECT * FROM compaction_records WHERE session_id = ? ORDER BY created_at DESC LIMIT 20",
            (session_id,),
        ).fetchall()
        compactions = [dict(r) for r in compaction_rows]

        # 11. Subagents
        subagent_rows = self._conn.execute(
            "SELECT * FROM collaboration_tasks WHERE parent_task_id = ? ORDER BY created_at ASC",
            (task_id,),
        ).fetchall()
        subagents = [self._serialize_collaboration_task(dict(r)) for r in subagent_rows]

        # 12. Artifacts
        artifact_rows = self._conn.execute(
            "SELECT * FROM artifacts WHERE parent_task_id = ? ORDER BY created_at ASC",
            (task_id,),
        ).fetchall()
        artifacts = [self._serialize_artifact(dict(r)) for r in artifact_rows]

        # 13. Memory recall (from context snapshots)
        snapshot_rows = self._conn.execute(
            "SELECT * FROM context_snapshots WHERE task_id = ? ORDER BY created_at ASC",
            (task_id,),
        ).fetchall()
        all_memory_ids: list[str] = []
        for sr in snapshot_rows:
            sr_dict = dict(sr)
            raw = sr_dict.get("memory_ids_json")
            if raw:
                try:
                    ids = json.loads(raw)
                    if isinstance(ids, list):
                        all_memory_ids.extend(ids)
                except (json.JSONDecodeError, TypeError):
                    pass
        # Deduplicate while preserving order
        seen: set[str] = set()
        unique_memory_ids: list[str] = []
        for mid in all_memory_ids:
            if mid not in seen:
                seen.add(mid)
                unique_memory_ids.append(mid)

        # 14. Context budget (latest snapshot)
        latest_snap_row = self._conn.execute(
            "SELECT * FROM context_snapshots WHERE task_id = ? ORDER BY created_at DESC LIMIT 1",
            (task_id,),
        ).fetchone()
        context_budget = self._serialize_context_snapshot(dict(latest_snap_row)) if latest_snap_row else None

        # 15. Hook executions
        hook_exec_rows = self._conn.execute(
            "SELECT * FROM hook_executions WHERE task_id = ? ORDER BY created_at ASC",
            (task_id,),
        ).fetchall()
        hook_executions = [self._serialize_hook_execution(dict(r)) for r in hook_exec_rows]

        # 16. Scope conflict checks
        scope_conflict_rows = self._conn.execute(
            "SELECT * FROM scope_conflict_checks WHERE task_id = ? ORDER BY created_at ASC",
            (task_id,),
        ).fetchall()
        scope_conflicts = [self._serialize_scope_conflict_check(dict(r)) for r in scope_conflict_rows]

        return {
            "task": task,
            "autonomyProfile": autonomy_profile,
            "agentSoulProfile": soul_profile,
            "routing": {
                **routing,
                "proposal": routing_proposal,
            },
            "metrics": metrics,
            "decisions": decisions,
            "approvals": approvals,
            "policyGateOutcomes": gate_outcomes,
            "patches": patches,
            "commands": commands,
            "compactions": compactions,
            "subagents": subagents,
            "artifacts": artifacts,
            "memoryRecall": {
                "memoryIds": unique_memory_ids,
                "count": len(unique_memory_ids),
            },
            "contextBudget": context_budget,
            "hookExecutions": hook_executions,
            "scopeConflicts": scope_conflicts,
        }

    def _active_profile_from_config(self, config: dict[str, Any], key: str) -> dict[str, Any] | None:
        """Resolve active profile from a config section (autonomy, agentSoul, provider)."""
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

    # ------------------------------------------------------------------
    # Trace Events
    # ------------------------------------------------------------------

