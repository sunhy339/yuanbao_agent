from __future__ import annotations

import json
import re
import sqlite3
import time
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any

class SessionStoreMixin:
    def upsert_workspace(self, path: str) -> dict[str, Any]:
        root = str(Path(path))
        workspace_id = self.new_id("ws")
        now = self.now()
        self._conn.execute(
            """
            INSERT INTO workspaces (id, name, root_path, focus, summary, created_at, updated_at)
            VALUES (?, ?, ?, NULL, NULL, ?, ?)
            ON CONFLICT(root_path) DO UPDATE SET updated_at = excluded.updated_at
            """,
            (workspace_id, Path(root).name or root, root, now, now),
        )
        self._conn.commit()
        row = self._conn.execute(
            "SELECT * FROM workspaces WHERE root_path = ?",
            (root,),
        ).fetchone()
        return self._serialize_workspace(dict(row))

    def require_workspace(self, workspace_id: str) -> dict[str, Any]:
        row = self._conn.execute(
            "SELECT * FROM workspaces WHERE id = ?",
            (workspace_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"Workspace not found: {workspace_id}")
        return self._serialize_workspace(dict(row))

    def update_workspace_summary(self, workspace_id: str, summary: str | None) -> dict[str, Any]:
        now = self.now()
        self._conn.execute(
            """
            UPDATE workspaces
            SET summary = ?, updated_at = ?
            WHERE id = ?
            """,
            (summary, now, workspace_id),
        )
        self._conn.commit()
        return self.require_workspace(workspace_id)

    def update_workspace_focus(self, params: dict[str, Any]) -> dict[str, Any]:
        workspace_id = params.get("workspaceId") or params.get("workspace_id")
        if not isinstance(workspace_id, str) or not workspace_id.strip():
            raise ValueError("workspaceId is required")
        focus = params.get("focus")
        if focus is not None:
            focus = str(focus).strip() or None
        now = self.now()
        self._conn.execute(
            """
            UPDATE workspaces
            SET focus = ?, updated_at = ?
            WHERE id = ?
            """,
            (focus, now, workspace_id.strip()),
        )
        self._conn.commit()
        return {"workspace": self.require_workspace(workspace_id.strip())}

    def clear_workspace_memory(self, params: dict[str, Any]) -> dict[str, Any]:
        workspace_id = params.get("workspaceId") or params.get("workspace_id")
        if not isinstance(workspace_id, str) or not workspace_id.strip():
            raise ValueError("workspaceId is required")
        workspace = self.update_workspace_summary(workspace_id.strip(), None)
        return {"workspace": workspace}

    def create_session(self, workspace_id: str, title: str) -> dict[str, Any]:
        session_id = self.new_id("sess")
        now = self.now()
        self._conn.execute(
            """
            INSERT INTO sessions (id, workspace_id, title, status, summary, created_at, updated_at)
            VALUES (?, ?, ?, 'active', NULL, ?, ?)
            """,
            (session_id, workspace_id, title, now, now),
        )
        self._conn.commit()
        return self.require_session(session_id)

    def require_session(self, session_id: str) -> dict[str, Any]:
        row = self._conn.execute(
            """
            SELECT s.*, w.name AS workspace_name, w.root_path AS workspace_root
            FROM sessions s
            LEFT JOIN workspaces w ON w.id = s.workspace_id
            WHERE s.id = ?
            """,
            (session_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"Session not found: {session_id}")
        return self._serialize_session(dict(row))

    def get_session(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"session": self.require_session(params["sessionId"])}

    def list_sessions(self, _params: dict[str, Any]) -> dict[str, Any]:
        rows = self._conn.execute(
            """
            SELECT s.*, w.name AS workspace_name, w.root_path AS workspace_root
            FROM sessions s
            LEFT JOIN workspaces w ON w.id = s.workspace_id
            ORDER BY s.updated_at DESC
            """
        ).fetchall()
        return {"sessions": [self._serialize_session(dict(row)) for row in rows]}

    def create_message(
        self,
        *,
        session_id: str,
        role: str,
        content: str,
        task_id: str | None = None,
        client_message_id: str | None = None,
        kind: str | None = None,
        status: str | None = None,
        created_seq: int | None = None,
    ) -> dict[str, Any]:
        if role not in {"user", "assistant", "system", "tool"}:
            raise ValueError(f"Unsupported message role: {role}")
        message_id = self.new_id("msg")
        now = self.now()
        self._conn.execute(
            """
            INSERT INTO messages (id, session_id, task_id, role, content, created_at,
                                  client_message_id, kind, status, created_seq, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                message_id, session_id, task_id, role, content, now,
                client_message_id,
                kind or "normal",
                status or "completed",
                created_seq,
                now,
            ),
        )
        self._conn.execute(
            """
            UPDATE sessions
            SET updated_at = ?
            WHERE id = ?
            """,
            (now, session_id),
        )
        self._conn.commit()
        row = self._conn.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
        if row is None:
            raise ValueError(f"Message not found: {message_id}")
        return self._serialize_message(dict(row))

    def update_message(
        self,
        message_id: str,
        *,
        content: str | None = None,
        status: str | None = None,
        kind: str | None = None,
    ) -> dict[str, Any] | None:
        assignments: list[str] = ["updated_at = ?"]
        values: list[Any] = [self.now()]
        if content is not None:
            assignments.append("content = ?")
            values.append(content)
        if status is not None:
            assignments.append("status = ?")
            values.append(status)
        if kind is not None:
            assignments.append("kind = ?")
            values.append(kind)
        values.append(message_id)
        self._conn.execute(
            f"UPDATE messages SET {', '.join(assignments)} WHERE id = ?",
            values,
        )
        self._conn.commit()
        row = self._conn.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
        return self._serialize_message(dict(row)) if row else None

    def list_messages(self, params: dict[str, Any]) -> dict[str, Any]:
        session_id = self._require_non_empty(params, "sessionId")
        limit = int(params.get("limit") or 500)
        rows = self._conn.execute(
            """
            SELECT *
            FROM messages
            WHERE session_id = ?
            ORDER BY created_at ASC, id ASC
            LIMIT ?
            """,
            (session_id, max(1, min(limit, 1000))),
        ).fetchall()
        return {"messages": [self._serialize_message(dict(row)) for row in rows]}

    def list_messages_by_task(self, task_id: str) -> list[dict[str, Any]]:
        """Return all messages for a task, ordered by created_at."""
        rows = self._conn.execute(
            """
            SELECT * FROM messages
            WHERE task_id = ?
            ORDER BY created_at ASC, id ASC
            """,
            (task_id,),
        ).fetchall()
        return [self._serialize_message(dict(row)) for row in rows]

    # ── task_inbox ──────────────────────────────────────────────────────

    def create_inbox_entry(
        self,
        *,
        task_id: str,
        session_id: str,
        content: str,
        message_id: str | None = None,
    ) -> dict[str, Any]:
        entry_id = self.new_id("ibx")
        now = self.now()
        seq = self.next_seq()
        self._conn.execute(
            """
            INSERT INTO task_inbox (id, task_id, session_id, message_id, content, status, created_seq, created_at)
            VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)
            """,
            (entry_id, task_id, session_id, message_id, content, seq, now),
        )
        self._conn.commit()
        row = self._conn.execute("SELECT * FROM task_inbox WHERE id = ?", (entry_id,)).fetchone()
        return dict(row) if row else {}

    def get_pending_supplements(self, task_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            """
            SELECT * FROM task_inbox
            WHERE task_id = ? AND status = 'pending'
            ORDER BY created_at ASC, id ASC
            """,
            (task_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def mark_supplement_consumed(
        self,
        entry_id: str,
        *,
        consumed_by_turn_id: str | None = None,
    ) -> dict[str, Any]:
        now = self.now()
        self._conn.execute(
            """
            UPDATE task_inbox
            SET status = 'consumed', consumed_by_turn_id = ?, consumed_at = ?
            WHERE id = ?
            """,
            (consumed_by_turn_id, now, entry_id),
        )
        self._conn.commit()
        row = self._conn.execute("SELECT * FROM task_inbox WHERE id = ?", (entry_id,)).fetchone()
        return dict(row) if row else {}

    def list_task_inbox_items(self, task_id: str) -> list[dict[str, Any]]:
        """Return all inbox items for a task, ordered by created_seq then created_at."""
        rows = self._conn.execute(
            """
            SELECT * FROM task_inbox
            WHERE task_id = ?
            ORDER BY COALESCE(created_seq, 0) ASC, created_at ASC, id ASC
            """,
            (task_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def update_session(self, params: dict[str, Any]) -> dict[str, Any]:
        session_id = self._require_non_empty(params, "sessionId")
        now = self.now()
        updates: list[str] = []
        values: list[Any] = []
        if "title" in params:
            updates.append("title = ?")
            values.append(params["title"])
        if "status" in params:
            updates.append("status = ?")
            values.append(params["status"])
        if not updates:
            return {"session": self.require_session(session_id)}
        updates.append("updated_at = ?")
        values.append(now)
        values.append(session_id)
        self._conn.execute(
            f"UPDATE sessions SET {', '.join(updates)} WHERE id = ?",
            values,
        )
        self._conn.commit()
        return {"session": self.require_session(session_id)}

    def delete_session(self, params: dict[str, Any]) -> dict[str, Any]:
        session_id = self._require_non_empty(params, "sessionId")
        session = self.require_session(session_id)
        self._delete_session_related_rows(session_id)
        self._conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
        self._conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        self._conn.commit()
        return {"session": session}

    def _delete_session_related_rows(self, session_id: str) -> None:
        task_rows = self._conn.execute(
            "SELECT id FROM tasks WHERE session_id = ?",
            (session_id,),
        ).fetchall()
        task_ids = [str(row["id"]) for row in task_rows]

        if task_ids:
            self._delete_task_related_rows(task_ids)

        self._conn.execute("DELETE FROM memory_recall_records WHERE session_id = ?", (session_id,))
        self._conn.execute("DELETE FROM session_rolling_summaries WHERE session_id = ?", (session_id,))
        self._conn.execute("DELETE FROM memory_entries WHERE session_id = ?", (session_id,))
        self._conn.execute("DELETE FROM task_inbox WHERE session_id = ?", (session_id,))
        self._conn.execute("DELETE FROM agent_messages WHERE task_id IN (SELECT id FROM collaboration_tasks WHERE session_id = ?)", (session_id,))
        self._conn.execute("DELETE FROM collaboration_tasks WHERE session_id = ?", (session_id,))
        self._conn.execute("DELETE FROM agent_workers WHERE current_task_id IS NULL")

    def _delete_task_related_rows(self, task_ids: list[str]) -> None:
        if not task_ids:
            return

        for task_id in task_ids:
            self._delete_command_artifacts_for_task(task_id)

        placeholders = ", ".join("?" for _ in task_ids)
        values: list[Any] = list(task_ids)

        self._conn.execute(f"DELETE FROM patches WHERE task_id IN ({placeholders})", values)
        self._conn.execute(f"DELETE FROM approvals WHERE task_id IN ({placeholders})", values)
        self._conn.execute(f"DELETE FROM pending_react_tasks WHERE task_id IN ({placeholders})", values)
        self._conn.execute(f"DELETE FROM pending_dag_tasks WHERE task_id IN ({placeholders})", values)
        self._conn.execute(f"DELETE FROM trace_events WHERE task_id IN ({placeholders})", values)
        self._conn.execute(f"DELETE FROM provider_turns WHERE task_id IN ({placeholders})", values)
        self._conn.execute(f"DELETE FROM context_snapshots WHERE task_id IN ({placeholders})", values)
        self._conn.execute(f"DELETE FROM command_logs WHERE task_id IN ({placeholders})", values)
        self._conn.execute(f"DELETE FROM task_metrics WHERE task_id IN ({placeholders})", values)
        self._conn.execute(f"DELETE FROM compaction_records WHERE task_id IN ({placeholders})", values)
        self._conn.execute(f"DELETE FROM proposal_records WHERE task_id IN ({placeholders})", values)
        self._conn.execute(f"DELETE FROM artifacts WHERE parent_task_id IN ({placeholders}) OR producer_task_id IN ({placeholders})", values + values)
        self._conn.execute(f"DELETE FROM hook_executions WHERE task_id IN ({placeholders})", values)
        self._conn.execute(f"DELETE FROM scope_conflict_checks WHERE task_id IN ({placeholders})", values)
        self._conn.execute(f"DELETE FROM replay_sessions WHERE source_task_id IN ({placeholders})", values)
        self._conn.execute(f"DELETE FROM task_worktrees WHERE task_id IN ({placeholders})", values)
        self._conn.execute(f"DELETE FROM messages WHERE task_id IN ({placeholders})", values)
        self._conn.execute(f"DELETE FROM task_inbox WHERE task_id IN ({placeholders})", values)
        self._conn.execute(f"DELETE FROM memory_recall_records WHERE task_id IN ({placeholders})", values)
        self._conn.execute(f"DELETE FROM collaboration_tasks WHERE parent_task_id IN ({placeholders})", values)
        self._conn.execute(f"DELETE FROM agent_messages WHERE task_id IN ({placeholders})", values)
        self._conn.execute(f"DELETE FROM tasks WHERE id IN ({placeholders})", values)

    def _delete_command_artifacts_for_task(self, task_id: str) -> None:
        rows = self._conn.execute(
            "SELECT stdout_path, stderr_path FROM command_logs WHERE task_id = ?",
            (task_id,),
        ).fetchall()
        for row in rows:
            for key in ("stdout_path", "stderr_path"):
                path = row[key]
                if not isinstance(path, str) or not path.strip():
                    continue
                try:
                    artifact_path = Path(path)
                    if artifact_path.exists():
                        artifact_path.unlink()
                except OSError:
                    continue

    def update_session_summary(self, session_id: str, summary: str | None) -> dict[str, Any]:
        now = self.now()
        self._conn.execute(
            """
            UPDATE sessions
            SET summary = ?, updated_at = ?
            WHERE id = ?
            """,
            (summary, now, session_id),
        )
        self._conn.commit()
        return self.require_session(session_id)

    # -- Rolling session summary --

    def get_rolling_summary(self, session_id: str) -> dict[str, Any] | None:
        """Get the current rolling summary for a session."""
        row = self._conn.execute(
            "SELECT * FROM session_rolling_summaries WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        if row is None:
            return None
        import json
        d = dict(row)
        d["covered_message_ids"] = json.loads(d.get("covered_message_ids") or "[]")
        return d

    def upsert_rolling_summary(
        self,
        session_id: str,
        summary: str,
        covered_message_ids: list[str],
        token_estimate: int,
    ) -> dict[str, Any]:
        """Insert or update the rolling summary for a session."""
        import json
        now = self.now()
        existing = self._conn.execute(
            "SELECT id FROM session_rolling_summaries WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        if existing:
            self._conn.execute(
                """
                UPDATE session_rolling_summaries
                SET summary = ?, covered_message_ids = ?, token_estimate = ?, updated_at = ?
                WHERE session_id = ?
                """,
                (
                    summary,
                    json.dumps(covered_message_ids, ensure_ascii=False),
                    token_estimate,
                    now,
                    session_id,
                ),
            )
        else:
            rid = self.new_id("rs")
            self._conn.execute(
                """
                INSERT INTO session_rolling_summaries
                    (id, session_id, summary, covered_message_ids, token_estimate, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    rid,
                    session_id,
                    summary,
                    json.dumps(covered_message_ids, ensure_ascii=False),
                    token_estimate,
                    now,
                    now,
                ),
            )
        self._conn.commit()
        return self.get_rolling_summary(session_id)  # type: ignore[return-value]

