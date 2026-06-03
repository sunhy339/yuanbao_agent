from __future__ import annotations

import json
import re
import sqlite3
import time
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any

class EventStoreMixin:
    def append_trace_event(
        self,
        *,
        task_id: str,
        event_type: str,
        source: str,
        payload: Any,
        related_id: str | None = None,
        session_id: str | None = None,
        created_at: int | None = None,
        visibility: str = "chat",
    ) -> dict[str, Any]:
        task_row = self._conn.execute(
            "SELECT session_id FROM tasks WHERE id = ?",
            (task_id,),
        ).fetchone()
        if task_row is None:
            raise ValueError(f"Task not found: {task_id}")

        trace_session_id = session_id or task_row["session_id"]
        return self._append_trace_event_row(
            task_id=task_id,
            session_id=trace_session_id,
            event_type=event_type,
            source=source,
            payload=payload,
            related_id=related_id,
            created_at=created_at,
            visibility=visibility,
        )

    def append_collaboration_trace_event(
        self,
        *,
        task_id: str,
        event_type: str,
        source: str,
        payload: Any,
        related_id: str | None = None,
        session_id: str | None = None,
        created_at: int | None = None,
        visibility: str = "panel",
    ) -> dict[str, Any]:
        self.require_collaboration_task(task_id)
        return self._append_trace_event_row(
            task_id=task_id,
            session_id=session_id or "",
            event_type=event_type,
            source=source,
            payload=payload,
            related_id=related_id,
            created_at=created_at,
            visibility=visibility,
        )

    def _append_trace_event_row(
        self,
        *,
        task_id: str,
        session_id: str,
        event_type: str,
        source: str,
        payload: Any,
        related_id: str | None = None,
        created_at: int | None = None,
        visibility: str = "chat",
    ) -> dict[str, Any]:
        trace_id = self.new_id("trace")
        timestamp = self.now() if created_at is None else int(created_at)
        payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        with self._conn._lock:
            sequence_row = self._conn.execute("SELECT COALESCE(MAX(sequence), 0) + 1 FROM trace_events").fetchone()
            sequence = int(sequence_row[0])
            self._conn.execute(
                """
                INSERT INTO trace_events (
                    id, task_id, session_id, type, source, related_id, payload_json, created_at, sequence, visibility
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trace_id,
                    task_id,
                    session_id,
                    event_type,
                    source,
                    related_id,
                    payload_json,
                    timestamp,
                    sequence,
                    visibility,
                ),
            )
            self._conn.commit()
            row = self._conn.execute("SELECT * FROM trace_events WHERE id = ?", (trace_id,)).fetchone()
        if row is None:
            raise ValueError(f"Trace event not found: {trace_id}")
        return self._serialize_trace_event(dict(row))

    def append_runtime_event(self, event: Any) -> dict[str, Any] | None:
        event_type = getattr(event, "type", None)
        task_id = getattr(event, "task_id", None)
        if not event_type or not task_id:
            return None

        normalized_type = str(event_type)
        if normalized_type.startswith(("approval.", "patch.")):
            return None
        if normalized_type in {"command.started", "command.completed", "command.failed", "command.cancelled"}:
            return None

        payload = getattr(event, "payload", {})
        if isinstance(payload, dict):
            bridge = payload.get("_bridge")
            if (
                isinstance(bridge, dict)
                and bool(bridge.get("skipTraceMirror"))
                and not bool(bridge.get("persistTraceMirror"))
            ):
                return None
        event_visibility = getattr(event, "visibility", "chat")
        if normalized_type.startswith("session."):
            event_session_id = getattr(event, "session_id", None)
            if not event_session_id and isinstance(payload, dict):
                event_session_id = payload.get("sessionId") or payload.get("session_id")
            if not event_session_id:
                return None
            return self._append_trace_event_row(
                task_id=str(task_id or event_session_id),
                session_id=str(event_session_id),
                event_type=normalized_type,
                source=self._trace_source(normalized_type),
                related_id=self._trace_related_id(payload),
                payload=payload,
                created_at=getattr(event, "ts", None),
                visibility=event_visibility,
            )
        if normalized_type.startswith("collab."):
            if not str(task_id).startswith("ctask_"):
                event_session_id = getattr(event, "session_id", None)
                if not event_session_id and isinstance(payload, dict):
                    event_session_id = payload.get("sessionId") or payload.get("session_id")
                if not event_session_id:
                    return None
                return self._append_trace_event_row(
                    task_id=str(task_id or event_session_id),
                    session_id=str(event_session_id),
                    event_type=normalized_type,
                    source=self._trace_source(normalized_type),
                    related_id=self._trace_related_id(payload),
                    payload=payload,
                    created_at=getattr(event, "ts", None),
                    visibility=event_visibility,
                )
            return self.append_collaboration_trace_event(
                task_id=task_id,
                session_id=getattr(event, "session_id", None),
                event_type=normalized_type,
                source=self._trace_source(normalized_type),
                related_id=self._trace_related_id(payload),
                payload=payload,
                created_at=getattr(event, "ts", None),
                visibility=event_visibility,
            )
        return self.append_trace_event(
            task_id=task_id,
            session_id=getattr(event, "session_id", None),
            event_type=normalized_type,
            source=self._trace_source(normalized_type),
            related_id=self._trace_related_id(payload),
            payload=payload,
            created_at=getattr(event, "ts", None),
            visibility=event_visibility,
        )

    def list_trace_events(self, params: dict[str, Any]) -> dict[str, Any]:
        task_id = params.get("taskId") or params.get("task_id")
        if not task_id:
            raise ValueError("taskId is required")
        limit = int(params.get("limit", 500))
        limit = max(1, min(limit, 5000))
        rows = self._conn.execute(
            """
            SELECT *
            FROM trace_events
            WHERE task_id = ?
            ORDER BY created_at ASC, sequence ASC
            LIMIT ?
            """,
            (task_id, limit),
        ).fetchall()
        return {"traceEvents": [self._serialize_trace_event(dict(row)) for row in rows]}

    def list_decision_events(self, params: dict[str, Any]) -> dict[str, Any]:
        """List agent decision events for a task or session.

        Filters by ``type LIKE 'agent.decision.%'`` plus optional
        taskId, sessionId, or kind filters.
        """
        task_id = params.get("taskId") or params.get("task_id")
        session_id = params.get("sessionId") or params.get("session_id")
        kind = params.get("kind")
        limit = min(int(params.get("limit") or 200), 1000)

        conditions = ["type LIKE 'agent.decision.%'"]
        args: list[Any] = []
        if task_id:
            conditions.append("task_id = ?")
            args.append(task_id)
        if session_id:
            conditions.append("session_id = ?")
            args.append(session_id)
        if kind:
            conditions.append("type = ?")
            args.append(f"agent.decision.{kind}")
        args.append(limit)

        rows = self._conn.execute(
            f"SELECT * FROM trace_events WHERE {' AND '.join(conditions)} ORDER BY created_at ASC, sequence ASC LIMIT ?",
            args,
        ).fetchall()
        return {"decisions": [self._serialize_trace_event(dict(row)) for row in rows]}

    def events_after(self, session_id: str, after_seq: int, *, limit: int = 500) -> dict[str, Any]:
        """Return trace events for a session after a given sequence number.

        Used for event recovery after reconnect / page refresh.
        """
        limit = max(1, min(limit, 500))
        rows = self._conn.execute(
            """
            SELECT *
            FROM trace_events
            WHERE session_id = ? AND sequence > ?
            ORDER BY sequence ASC
            LIMIT ?
            """,
            (session_id, after_seq, limit),
        ).fetchall()
        total_after = self._conn.execute(
            "SELECT COUNT(*) FROM trace_events WHERE session_id = ? AND sequence > ?",
            (session_id, after_seq),
        ).fetchone()[0]
        return {
            "events": [self._serialize_trace_event(dict(row)) for row in rows],
            "truncated": total_after > limit,
        }

    # ── trace spans ────────────────────────────────────────────────────

    def get_trace_spans(self, params: dict[str, Any]) -> dict[str, Any]:
        """Return all spans for a given trace_id."""
        trace_id = params.get("traceId") or params.get("trace_id")
        if not trace_id:
            raise ValueError("traceId is required")
        rows = self._conn.execute(
            """
            SELECT * FROM trace_spans
            WHERE trace_id = ?
            ORDER BY started_at ASC
            """,
            (trace_id,),
        ).fetchall()
        spans = [dict(row) for row in rows]
        # Parse JSON attributes
        for span in spans:
            for key in ("attributes", "result_attributes"):
                val = span.get(key)
                if isinstance(val, str):
                    try:
                        span[key] = json.loads(val)
                    except (json.JSONDecodeError, TypeError):
                        pass
        return {"traceId": trace_id, "spans": spans}

    def get_stats_summary(self, params: dict[str, Any]) -> dict[str, Any]:
        """Return aggregated observability stats."""
        limit = int(params.get("limit", 100))

        # Recent tool calls from spans
        tool_rows = self._conn.execute(
            """
            SELECT attributes FROM trace_spans
            WHERE operation = 'tool_call'
            ORDER BY started_at DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
        tool_calls = []
        for row in tool_rows:
            try:
                attrs = json.loads(row["attributes"]) if isinstance(row["attributes"], str) else row["attributes"]
                tool_calls.append(attrs)
            except (json.JSONDecodeError, TypeError):
                pass

        # Error distribution from trace_events
        error_rows = self._conn.execute(
            """
            SELECT event_type, COUNT(*) as cnt
            FROM trace_events
            WHERE event_type LIKE '%error%' OR event_type LIKE '%fail%'
            GROUP BY event_type
            ORDER BY cnt DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        error_distribution = {row["event_type"]: row["cnt"] for row in error_rows}

        return {
            "toolCalls": tool_calls,
            "toolCallCount": len(tool_calls),
            "errorDistribution": error_distribution,
        }

    def export_logs(self, params: dict[str, Any]) -> dict[str, Any]:
        session_id = params.get("sessionId")
        sessions = self._rows("SELECT * FROM sessions", session_id)
        tasks = self._rows("SELECT * FROM tasks", session_id)
        messages = self._rows("SELECT * FROM messages", session_id)
        command_logs = self._rows_joined("command_logs", "tasks", session_id)
        patches = self._rows_joined("patches", "tasks", session_id)
        approvals = self._rows_joined("approvals", "tasks", session_id)
        trace_events = self._rows("SELECT * FROM trace_events", session_id)
        return {
            "exportedAt": self.now(),
            "sessions": sessions,
            "tasks": tasks,
            "messages": messages,
            "commandLogs": command_logs,
            "patches": patches,
            "approvals": approvals,
            "traceEvents": trace_events,
            "config": self.get_config({})["config"],
        }

    def list_errors(self, params: dict[str, Any]) -> dict[str, Any]:
        session_id = params.get("sessionId")
        task_id = params.get("taskId")
        source = params.get("source")
        limit = min(int(params.get("limit") or 50), 200)
        errors: list[dict[str, Any]] = []

        if not source or source == "task":
            query, args = "SELECT * FROM tasks WHERE status = 'failed'", []
            if session_id:
                query += " AND session_id = ?"
                args.append(session_id)
            if task_id:
                query += " AND id = ?"
                args.append(task_id)
            for r in self._conn.execute(query, args).fetchall():
                d = dict(r)
                errors.append({
                    "id": d["id"], "source": "task",
                    "sessionId": d["session_id"], "taskId": d["id"],
                    "errorCode": d.get("error_code"),
                    "errorMessage": d.get("summary") or d.get("goal") or "Task failed",
                    "timestamp": d.get("updated_at") or d.get("created_at", 0),
                    "metadata": {"status": d["status"], "goal": d.get("goal")},
                })

        if not source or source == "command":
            query, args = "SELECT cl.*, t.session_id FROM command_logs cl JOIN tasks t ON t.id = cl.task_id WHERE cl.status IN ('failed','timeout','killed')", []
            if session_id:
                query += " AND t.session_id = ?"
                args.append(session_id)
            if task_id:
                query += " AND cl.task_id = ?"
                args.append(task_id)
            for r in self._conn.execute(query, args).fetchall():
                d = dict(r)
                errors.append({
                    "id": d["id"], "source": "command",
                    "sessionId": d["session_id"], "taskId": d["task_id"],
                    "errorCode": d.get("status"),
                    "errorMessage": d.get("summary") or f"Command {d.get('status')}: {(d.get('command') or '')[:100]}",
                    "timestamp": d.get("finished_at") or d.get("started_at", 0),
                    "metadata": {"command": d.get("command"), "exitCode": d.get("exit_code"), "cwd": d.get("cwd")},
                })

        if not source or source == "patch":
            query, args = "SELECT p.*, t.session_id FROM patches p JOIN tasks t ON t.id = p.task_id WHERE p.status IN ('failed','rejected')", []
            if session_id:
                query += " AND t.session_id = ?"
                args.append(session_id)
            if task_id:
                query += " AND p.task_id = ?"
                args.append(task_id)
            for r in self._conn.execute(query, args).fetchall():
                d = dict(r)
                errors.append({
                    "id": d["id"], "source": "patch",
                    "sessionId": d["session_id"], "taskId": d["task_id"],
                    "errorCode": d.get("status"),
                    "errorMessage": d.get("summary") or f"Patch {d.get('status')}: {d.get('files_changed', 0)} files",
                    "timestamp": d.get("updated_at") or d.get("created_at", 0),
                    "metadata": {"status": d["status"], "filesChanged": d.get("files_changed")},
                })

        errors.sort(key=lambda e: e["timestamp"], reverse=True)
        by_source: dict[str, int] = {}
        for e in errors:
            s = e["source"]
            by_source[s] = by_source.get(s, 0) + 1
        return {
            "errors": errors[:limit],
            "summary": {"totalErrors": len(errors), "bySource": by_source},
        }

    def _rows(self, query: str, session_id: str | None = None) -> list[dict[str, Any]]:
        if session_id and "session_id" in query:
            query += " WHERE session_id = ?"
            return [dict(r) for r in self._conn.execute(query, [session_id]).fetchall()]
        return [dict(r) for r in self._conn.execute(query).fetchall()]

    def _rows_joined(self, table: str, join_table: str, session_id: str | None = None) -> list[dict[str, Any]]:
        query = f"SELECT {table}.* FROM {table} JOIN {join_table} t ON t.id = {table}.task_id"
        if session_id:
            query += " WHERE t.session_id = ?"
            return [dict(r) for r in self._conn.execute(query, [session_id]).fetchall()]
        return [dict(r) for r in self._conn.execute(query).fetchall()]

    def record_task_metrics(self, metrics: dict[str, Any]) -> None:
        self._conn.execute(
            """INSERT INTO task_metrics (
                id, task_id, session_id, created_at, duration_ms,
                tool_call_count, command_count, patch_count, provider_call_count,
                patch_success_count, patch_failure_count,
                command_success_count, command_failure_count,
                task_status, patch_repair_attempts,
                approval_approved_count, approval_rejected_count, was_cancelled
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                metrics.get("id") or self.new_id("metric"),
                metrics["taskId"],
                metrics["sessionId"],
                metrics.get("createdAt") or self.now(),
                metrics.get("durationMs"),
                metrics.get("toolCallCount", 0),
                metrics.get("commandCount", 0),
                metrics.get("patchCount", 0),
                metrics.get("providerCallCount", 0),
                metrics.get("patchSuccessCount", 0),
                metrics.get("patchFailureCount", 0),
                metrics.get("commandSuccessCount", 0),
                metrics.get("commandFailureCount", 0),
                metrics.get("taskStatus"),
                metrics.get("patchRepairAttempts", 0),
                metrics.get("approvalApprovedCount", 0),
                metrics.get("approvalRejectedCount", 0),
                1 if metrics.get("wasCancelled") else 0,
            ),
        )
        self._conn.commit()

    def list_metrics(self, params: dict[str, Any]) -> dict[str, Any]:
        session_id = params.get("sessionId")
        limit = min(int(params.get("limit") or 50), 200)
        query = "SELECT * FROM task_metrics"
        args: list[Any] = []
        if session_id:
            query += " WHERE session_id = ?"
            args.append(session_id)
        query += " ORDER BY created_at DESC LIMIT ?"
        args.append(limit)
        rows = [dict(r) for r in self._conn.execute(query, args).fetchall()]
        return {"metrics": rows}

    # -----------------------------------------------------------------------
    # Proposal Record CRUD — P1 of llm-assisted-runtime-decision-todolist
    # -----------------------------------------------------------------------

