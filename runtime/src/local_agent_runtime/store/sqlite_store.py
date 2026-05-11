from __future__ import annotations

import json
import re
import sqlite3
import time
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any


CONFIG_KEY = "app_config"

DEFAULT_AUTONOMY_PROFILE = {
    "id": "balanced",
    "name": "Balanced",
    "level": "L2",
    "maxSteps": 20,
    "maxParallelSubtasks": 4,
    "allowBackground": True,
    "allowSubagents": True,
    "allowFileWrite": "approval_required",
    "allowShell": "approval_required",
    "allowNetwork": False,
    "memoryRecallPolicy": "workspace_first_session_boosted",
    "retryLimit": 2,
    "timeoutMs": 600000,
}

DEFAULT_AGENT_SOUL_PROFILE = {
    "id": "default",
    "name": "Default",
    "description": "Default local coding agent identity.",
    "identity": "A capable local coding agent that works inside the user's desktop runtime.",
    "principles": [
        "Be practical, careful, and transparent about uncertainty.",
        "Prefer existing project patterns over unnecessary new abstractions.",
        "Keep the user in control of risky actions.",
    ],
    "communicationStyle": "Clear, concise, collaborative.",
    "reasoningStyle": "Inspect the current workspace before making changes.",
    "collaborationStyle": "Explain meaningful decisions and keep work scoped to the user's request.",
    "domainPreferences": [],
    "customSystemPrompt": "",
    "enabled": True,
    "scope": "global",
    "createdAt": 0,
    "updatedAt": 0,
}


DEFAULT_CONFIG = {
    "provider": {
        "mode": "mock",
        "baseUrl": "https://api.openai.com/v1",
        "model": "gpt-5-codex",
        "defaultModel": "gpt-5-codex",
        "fallbackModel": "claude-sonnet",
        "apiKeyEnvVarName": "LOCAL_AGENT_PROVIDER_API_KEY",
        "temperature": 0.2,
        "maxTokens": 4000,
        "maxOutputTokens": 4000,
        "maxContextTokens": 256000,
        "timeout": 30,
        "activeProfileId": "default",
        "profiles": [
            {
                "id": "default",
                "name": "Default",
                "mode": "mock",
                "baseUrl": "https://api.openai.com/v1",
                "model": "gpt-5-codex",
                "defaultModel": "gpt-5-codex",
                "fallbackModel": "claude-sonnet",
                "apiKeyEnvVarName": "LOCAL_AGENT_PROVIDER_API_KEY",
                "temperature": 0.2,
                "maxTokens": 4000,
                "maxOutputTokens": 4000,
                "maxContextTokens": 256000,
                "timeout": 30,
            }
        ],
    },
    "workspace": {
        "rootPath": "",
        "ignore": [".git", "node_modules", "dist", ".venv"],
        "writableRoots": [],
    },
    "search": {
        "glob": [],
        "ignore": [".git", "node_modules", "dist", ".venv", "target", "__pycache__"],
    },
    "policy": {
        "approvalMode": "on_write_or_command",
        "commandTimeoutMs": 600000,
        "maxTaskSteps": 20,
        "maxPatchRepairAttempts": 2,
        "maxFilesPerPatch": 20,
        "allowNetwork": False,
        "postTaskValidation": {
            "command": None,
        },
    },
    "autonomy": {
        "activeProfileId": "balanced",
        "profiles": [
            {
                **DEFAULT_AUTONOMY_PROFILE,
                "id": "locked_down",
                "name": "Locked Down",
                "level": "L0",
                "maxSteps": 4,
                "maxParallelSubtasks": 1,
                "allowBackground": False,
                "allowSubagents": False,
                "allowFileWrite": "blocked",
                "allowShell": "blocked",
                "allowNetwork": False,
                "retryLimit": 0,
            },
            {
                **DEFAULT_AUTONOMY_PROFILE,
                "id": "conservative",
                "name": "Conservative",
                "level": "L1",
                "maxSteps": 10,
                "maxParallelSubtasks": 2,
                "allowBackground": False,
                "allowSubagents": True,
            },
            deepcopy(DEFAULT_AUTONOMY_PROFILE),
            {
                **DEFAULT_AUTONOMY_PROFILE,
                "id": "autonomous",
                "name": "Autonomous",
                "level": "L3",
                "maxSteps": 40,
                "maxParallelSubtasks": 6,
                "allowBackground": True,
                "allowSubagents": True,
            },
        ],
    },
    "agentSoul": {
        "activeProfileId": "default",
        "workspaceInstructions": "",
        "sessionOverrideEnabled": False,
        "profiles": [deepcopy(DEFAULT_AGENT_SOUL_PROFILE)],
    },
    "tools": {
        "runCommand": {
            "allowedShell": "powershell",
            "allowedCommands": [],
            "allowlist": [],
            "deniedCommands": [],
            "denylist": [],
            "blockedPatterns": ["rm -rf", "shutdown", "format"],
            "allowedCwdRoots": [],
        }
    },
    "reflection": {
        "enabled": False,
        "maxRetries": 2,
        "confidenceThreshold": 0.7,
        "evaluationPrompt": "",
    },
    "ui": {
        "language": "zh-CN",
        "showRawEvents": False,
        "theme": "light",
        "density": "comfortable",
        "radius": "md",
        "motion": "subtle",
        "accentColor": "cyan",
        "transparency": 0.78,
        "fontScale": 1,
        "reasoningEffort": "max",
        "webFetchPreflight": True,
    },
    "features": {
        "multiAgent": True,
        "streamingDeltaPersist": True,
    },
}


class SQLiteStore:
    """Small SQLite wrapper for the MVP scaffold."""

    def __init__(self, database_path: str) -> None:
        self._database_path = database_path
        database_file = Path(database_path)
        if database_path != ":memory:":
            database_file.expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(database_path)
        self._conn.row_factory = sqlite3.Row
        self._artifact_dir = (
            Path.cwd() / "runtime_artifacts"
            if database_path == ":memory:"
            else database_file.expanduser().resolve().parent / "runtime_artifacts"
        )
        self._artifact_dir.mkdir(parents=True, exist_ok=True)
        self._bootstrap()
        self._config = self._load_or_initialize_config()
        self._config_snapshot: dict[str, Any] | None = None

    def close(self) -> None:
        self._conn.close()

    @property
    def database_path(self) -> str:
        return self._database_path

    def now(self) -> int:
        return int(time.time() * 1000)

    def new_id(self, prefix: str) -> str:
        return f"{prefix}_{uuid.uuid4().hex[:12]}"

    def next_seq(self) -> int:
        """Allocate a monotonically increasing sequence number from a dedicated counter table."""
        self._conn.execute(
            "UPDATE seq_counter SET val = val + 1 WHERE id = 1"
        )
        row = self._conn.execute(
            "SELECT val FROM seq_counter WHERE id = 1"
        ).fetchone()
        self._conn.commit()
        return int(row["val"])

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
        self._conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
        self._conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        self._conn.commit()
        return {"session": session}

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

    def create_scheduled_task(self, params: dict[str, Any]) -> dict[str, Any]:
        name = self._require_non_empty(params, "name")
        prompt = self._require_non_empty(params, "prompt")
        schedule = self._require_non_empty(params, "schedule")
        enabled = bool(params.get("enabled", True))
        status = self._normalize_scheduled_status(params.get("status"), enabled)
        enabled = status == "active"
        now = self.now()
        task_id = self.new_id("sched")
        next_run_at = self._next_scheduled_run_at(schedule, now) if enabled else None

        self._conn.execute(
            """
            INSERT INTO scheduled_tasks (
                id, name, prompt, schedule, status, enabled, created_at, updated_at, last_run_at, next_run_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)
            """,
            (task_id, name, prompt, schedule, status, int(enabled), now, now, next_run_at),
        )
        self._conn.commit()
        return {"task": self.require_scheduled_task(task_id)}

    def list_scheduled_tasks(self, _params: dict[str, Any] | None = None) -> dict[str, Any]:
        rows = self._conn.execute(
            """
            SELECT *
            FROM scheduled_tasks
            ORDER BY created_at DESC
            """
        ).fetchall()
        return {"tasks": [self._serialize_scheduled_task(dict(row)) for row in rows]}

    def require_scheduled_task(self, task_id: str) -> dict[str, Any]:
        row = self._conn.execute(
            "SELECT * FROM scheduled_tasks WHERE id = ?",
            (task_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"Scheduled task not found: {task_id}")
        return self._serialize_scheduled_task(dict(row))

    def update_scheduled_task(self, params: dict[str, Any]) -> dict[str, Any]:
        task_id = self._require_non_empty(params, "taskId")
        current = self.require_scheduled_task(task_id)
        name = self._optional_non_empty(params, "name", current["name"])
        prompt = self._optional_non_empty(params, "prompt", current["prompt"])
        schedule = self._optional_non_empty(params, "schedule", current["schedule"])
        enabled = bool(params.get("enabled", current["enabled"]))
        status = self._normalize_scheduled_status(params.get("status"), enabled)
        enabled = status == "active"
        now = self.now()
        next_run_at = self._next_scheduled_run_at(schedule, now) if enabled else None

        self._conn.execute(
            """
            UPDATE scheduled_tasks
            SET name = ?, prompt = ?, schedule = ?, status = ?, enabled = ?, updated_at = ?, next_run_at = ?
            WHERE id = ?
            """,
            (name, prompt, schedule, status, int(enabled), now, next_run_at, task_id),
        )
        self._conn.commit()
        return {"task": self.require_scheduled_task(task_id)}

    def toggle_scheduled_task(self, params: dict[str, Any]) -> dict[str, Any]:
        task_id = self._require_non_empty(params, "taskId")
        enabled = bool(params.get("enabled", True))
        current = self.require_scheduled_task(task_id)
        return self.update_scheduled_task(
            {
                "taskId": task_id,
                "name": current["name"],
                "prompt": current["prompt"],
                "schedule": current["schedule"],
                "enabled": enabled,
            }
        )

    def create_scheduled_task_run(
        self,
        *,
        task_id: str,
        status: str,
        started_at: int,
        finished_at: int | None,
        summary: str | None,
        error: str | None = None,
    ) -> dict[str, Any]:
        task = self.require_scheduled_task(task_id)
        run_id = self.new_id("schedrun")
        self._conn.execute(
            """
            INSERT INTO scheduled_task_runs (
                id, task_id, status, started_at, finished_at, summary, error
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (run_id, task_id, status, started_at, finished_at, summary, error),
        )

        next_run_at = self._next_scheduled_run_at(task["schedule"], finished_at or started_at) if task["enabled"] else None
        self._conn.execute(
            """
            UPDATE scheduled_tasks
            SET last_run_at = ?, next_run_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (started_at, next_run_at, finished_at or started_at, task_id),
        )
        self._conn.commit()

        row = self._conn.execute("SELECT * FROM scheduled_task_runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            raise ValueError(f"Scheduled run not found: {run_id}")
        return self._serialize_scheduled_task_run(dict(row))

    def list_scheduled_task_runs(self, params: dict[str, Any]) -> dict[str, Any]:
        task_id = params.get("taskId")
        limit = int(params.get("limit", 100))
        limit = max(1, min(limit, 1000))
        if task_id:
            rows = self._conn.execute(
                """
                SELECT *
                FROM scheduled_task_runs
                WHERE task_id = ?
                ORDER BY started_at DESC
                LIMIT ?
                """,
                (task_id, limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                """
                SELECT *
                FROM scheduled_task_runs
                ORDER BY started_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return {"logs": [self._serialize_scheduled_task_run(dict(row)) for row in rows]}

    def create_task(
        self,
        session_id: str,
        task_type: str,
        goal: str,
        plan: list[dict[str, Any]],
        *,
        acceptance_criteria: list[str] | None = None,
        out_of_scope: list[str] | None = None,
        current_step: str | None = None,
        routing: dict[str, Any] | None = None,
        root_task_id: str | None = None,
        role: str | None = None,
        created_seq: int | None = None,
        status: str = "running",
    ) -> dict[str, Any]:
        task_id = self.new_id("task")
        now = self.now()
        current_step = current_step or self._current_step_from_plan(plan)
        valid_roles = {"root", "planner", "worker", "reviewer", "summarizer"}
        effective_role = role or "root"
        if effective_role not in valid_roles:
            raise ValueError(f"Invalid task role: {effective_role!r}. Must be one of {sorted(valid_roles)}")
        effective_root_task_id = root_task_id or task_id
        self._conn.execute(
            """
            INSERT INTO tasks (
                id, session_id, type, status, goal, acceptance_criteria_json, out_of_scope_json,
                current_step, plan_json, changed_files_json, commands_json, verification_json,
                reflection_json, routing_json, summary, result_json, error_code,
                created_at, updated_at, root_task_id, role, created_seq
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '[]', '[]', '[]', NULL, ?, NULL, NULL, NULL, ?, ?, ?, ?, ?)
            """,
            (
                task_id,
                session_id,
                task_type,
                status,
                goal,
                json.dumps(acceptance_criteria or [], ensure_ascii=False),
                json.dumps(out_of_scope or [], ensure_ascii=False),
                current_step,
                json.dumps(plan, ensure_ascii=False),
                json.dumps(routing, ensure_ascii=False) if routing else None,
                now,
                now,
                effective_root_task_id,
                effective_role,
                created_seq,
            ),
        )
        self._conn.commit()
        row = self._conn.execute(
            "SELECT * FROM tasks WHERE id = ?",
            (task_id,),
        ).fetchone()
        return self._serialize_task(dict(row))

    def update_task_status(self, task_id: str, status: str) -> dict[str, Any]:
        now = self.now()
        self._conn.execute(
            "UPDATE tasks SET status = ?, updated_at = ? WHERE id = ?",
            (status, now, task_id),
        )
        self._conn.commit()
        return self.get_task({"taskId": task_id})["task"]

    def update_task(
        self,
        task_id: str,
        *,
        status: str | None = None,
        plan: list[dict[str, Any]] | None = None,
        acceptance_criteria: list[str] | None = None,
        out_of_scope: list[str] | None = None,
        current_step: str | None = None,
        changed_files: list[dict[str, Any]] | None = None,
        commands: list[dict[str, Any]] | None = None,
        verification: list[dict[str, Any]] | None = None,
        reflection: dict[str, Any] | None = None,
        summary: str | None = None,
        result_summary: str | None = None,
        error_code: str | None = None,
        active_assistant_message_id: str | None = None,
        tests_run: list[dict[str, Any]] | None = None,
        risks: list[dict[str, Any]] | None = None,
        structured_result: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        assignments: list[str] = ["updated_at = ?"]
        values: list[Any] = [self.now()]

        if status is not None:
            assignments.append("status = ?")
            values.append(status)
        if plan is not None:
            assignments.append("plan_json = ?")
            values.append(json.dumps(plan, ensure_ascii=False))
            if current_step is None:
                current_step = self._current_step_from_plan(plan)
        if acceptance_criteria is not None:
            assignments.append("acceptance_criteria_json = ?")
            values.append(json.dumps(acceptance_criteria, ensure_ascii=False))
        if out_of_scope is not None:
            assignments.append("out_of_scope_json = ?")
            values.append(json.dumps(out_of_scope, ensure_ascii=False))
        if current_step is not None:
            assignments.append("current_step = ?")
            values.append(current_step)
        if changed_files is not None:
            assignments.append("changed_files_json = ?")
            values.append(json.dumps(changed_files, ensure_ascii=False))
        if commands is not None:
            assignments.append("commands_json = ?")
            values.append(json.dumps(commands, ensure_ascii=False))
        if verification is not None:
            assignments.append("verification_json = ?")
            values.append(json.dumps(verification, ensure_ascii=False))
        if reflection is not None:
            assignments.append("reflection_json = ?")
            values.append(json.dumps(reflection, ensure_ascii=False))
        if summary is not None:
            assignments.append("summary = ?")
            values.append(summary)
        if result_summary is not None:
            assignments.append("result_json = ?")
            values.append(result_summary)
        if error_code is not None:
            assignments.append("error_code = ?")
            values.append(error_code)
        if active_assistant_message_id is not None:
            assignments.append("active_assistant_message_id = ?")
            values.append(active_assistant_message_id)
        if tests_run is not None:
            assignments.append("tests_run_json = ?")
            values.append(json.dumps(tests_run, ensure_ascii=False))
        if risks is not None:
            assignments.append("risks_json = ?")
            values.append(json.dumps(risks, ensure_ascii=False))
        if structured_result is not None:
            assignments.append("structured_result_json = ?")
            values.append(json.dumps(structured_result, ensure_ascii=False))

        values.append(task_id)
        self._conn.execute(
            f"UPDATE tasks SET {', '.join(assignments)} WHERE id = ?",
            values,
        )
        self._conn.commit()
        return self.get_task({"taskId": task_id})["task"]

    def get_task(self, params: dict[str, Any]) -> dict[str, Any]:
        row = self._conn.execute(
            "SELECT * FROM tasks WHERE id = ?",
            (params["taskId"],),
        ).fetchone()
        if row is None:
            raise ValueError(f"Task not found: {params['taskId']}")
        return {"task": self._serialize_task(dict(row))}

    def list_tasks(self, params: dict[str, Any]) -> dict[str, Any]:
        session_id = params.get("sessionId") or params.get("session_id")
        if session_id:
            rows = self._conn.execute(
                "SELECT * FROM tasks WHERE session_id = ? ORDER BY updated_at DESC, created_at DESC",
                (session_id,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM tasks ORDER BY updated_at DESC, created_at DESC",
            ).fetchall()
        return {"tasks": [self._serialize_task(dict(row)) for row in rows]}

    def create_collaboration_task(self, params: dict[str, Any]) -> dict[str, Any]:
        title = self._require_non_empty(params, "title")
        description = params.get("description")
        if description is not None and not isinstance(description, str):
            raise ValueError("description must be a string")
        session_id = self._optional_string(params, "sessionId")
        parent_task_id = self._optional_string(params, "parentTaskId")
        assigned_worker_id = self._optional_string(params, "assignedWorkerId")
        dependencies = self._string_list(params.get("dependencies", []), "dependencies")
        priority = self._normalize_priority(params.get("priority", 3))
        metadata = self._dict_value(params.get("metadata", {}), "metadata")
        task_id = self.new_id("ctask")
        now = self.now()
        self._conn.execute(
            """
            INSERT INTO collaboration_tasks (
                id, session_id, parent_task_id, title, description, status, priority,
                assigned_worker_id, dependencies_json, result_json, error_json, metadata_json,
                claimed_at, completed_at, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, 'queued', ?, ?, ?, NULL, NULL, ?, NULL, NULL, ?, ?)
            """,
            (
                task_id,
                session_id,
                parent_task_id,
                title,
                description.strip() if isinstance(description, str) else None,
                priority,
                assigned_worker_id,
                json.dumps(dependencies, ensure_ascii=False),
                json.dumps(metadata, ensure_ascii=False, sort_keys=True),
                now,
                now,
            ),
        )
        self._conn.commit()
        return {"task": self.require_collaboration_task(task_id)}

    def complete_collaboration_task(self, params: dict[str, Any]) -> dict[str, Any]:
        task_id = self._require_non_empty(params, "taskId")
        worker_id = self._require_non_empty(params, "workerId")
        result = self._dict_value(params.get("result"), "result")
        return self._finalize_collaboration_task(
            task_id=task_id,
            worker_id=worker_id,
            task_status="completed",
            worker_status="idle",
            result=result,
            error=None,
        )

    def fail_collaboration_task(self, params: dict[str, Any]) -> dict[str, Any]:
        task_id = self._require_non_empty(params, "taskId")
        worker_id = self._require_non_empty(params, "workerId")
        if "error" not in params:
            raise ValueError("error is required")
        error = self._json_value(params.get("error"), "error")
        return self._finalize_collaboration_task(
            task_id=task_id,
            worker_id=worker_id,
            task_status="failed",
            worker_status="failed",
            result=None,
            error=error,
        )

    def require_collaboration_task(self, task_id: str) -> dict[str, Any]:
        row = self._conn.execute(
            "SELECT * FROM collaboration_tasks WHERE id = ?",
            (task_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"Collaboration task not found: {task_id}")
        return self._serialize_collaboration_task(dict(row))

    def get_collaboration_task(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"task": self.require_collaboration_task(self._require_non_empty(params, "taskId"))}

    def list_collaboration_tasks(self, params: dict[str, Any]) -> dict[str, Any]:
        clauses: list[str] = []
        values: list[Any] = []
        for param_name, column_name in (
            ("sessionId", "session_id"),
            ("parentTaskId", "parent_task_id"),
            ("assignedWorkerId", "assigned_worker_id"),
            ("status", "status"),
        ):
            value = self._optional_string(params, param_name)
            if value is not None:
                clauses.append(f"{column_name} = ?")
                values.append(value)
        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._conn.execute(
            f"""
            SELECT *
            FROM collaboration_tasks
            {where_sql}
            ORDER BY priority ASC, updated_at DESC, created_at DESC
            """,
            values,
        ).fetchall()
        return {"tasks": [self._serialize_collaboration_task(dict(row)) for row in rows]}

    def update_collaboration_task(self, params: dict[str, Any]) -> dict[str, Any]:
        task_id = self._require_non_empty(params, "taskId")
        current = self.require_collaboration_task(task_id)
        assignments: list[str] = ["updated_at = ?"]
        values: list[Any] = [self.now()]

        if "title" in params:
            assignments.append("title = ?")
            values.append(self._require_non_empty(params, "title"))
        if "description" in params:
            description = params.get("description")
            if description is not None and not isinstance(description, str):
                raise ValueError("description must be a string")
            assignments.append("description = ?")
            values.append(description.strip() if isinstance(description, str) else None)
        if "status" in params:
            status = self._normalize_collaboration_task_status(params.get("status"))
            assignments.append("status = ?")
            values.append(status)
            if status in {"completed", "failed", "cancelled"} and current["completedAt"] is None:
                assignments.append("completed_at = ?")
                values.append(self.now())
        if "priority" in params:
            assignments.append("priority = ?")
            values.append(self._normalize_priority(params.get("priority")))
        if "assignedWorkerId" in params:
            assignments.append("assigned_worker_id = ?")
            values.append(self._optional_string(params, "assignedWorkerId"))
        if "dependencies" in params:
            assignments.append("dependencies_json = ?")
            values.append(json.dumps(self._string_list(params.get("dependencies"), "dependencies"), ensure_ascii=False))
        if "result" in params:
            assignments.append("result_json = ?")
            values.append(json.dumps(self._dict_value(params.get("result"), "result"), ensure_ascii=False, sort_keys=True))
        if "metadata" in params:
            assignments.append("metadata_json = ?")
            values.append(json.dumps(self._dict_value(params.get("metadata"), "metadata"), ensure_ascii=False, sort_keys=True))

        values.append(task_id)
        self._conn.execute(f"UPDATE collaboration_tasks SET {', '.join(assignments)} WHERE id = ?", values)
        self._conn.commit()
        return {"task": self.require_collaboration_task(task_id)}

    def claim_collaboration_task(self, params: dict[str, Any]) -> dict[str, Any]:
        task_id = self._require_non_empty(params, "taskId")
        worker_id = self._require_non_empty(params, "workerId")
        task = self.require_collaboration_task(task_id)
        if task["status"] not in {"queued", "blocked"}:
            raise ValueError(f"Task cannot be claimed from status: {task['status']}")
        if task["assignedWorkerId"] and task["assignedWorkerId"] != worker_id:
            raise ValueError(f"Task is already assigned to {task['assignedWorkerId']}")
        self.require_agent_worker(worker_id)
        now = self.now()
        self._conn.execute(
            """
            UPDATE collaboration_tasks
            SET status = 'claimed', assigned_worker_id = ?, claimed_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (worker_id, now, now, task_id),
        )
        self._conn.execute(
            """
            UPDATE agent_workers
            SET status = 'busy', current_task_id = ?, last_heartbeat_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (task_id, now, now, worker_id),
        )
        self._conn.commit()
        return {"task": self.require_collaboration_task(task_id), "worker": self.require_agent_worker(worker_id)}

    def release_collaboration_task(self, params: dict[str, Any]) -> dict[str, Any]:
        task_id = self._require_non_empty(params, "taskId")
        worker_id = self._optional_string(params, "workerId")
        task = self.require_collaboration_task(task_id)
        if worker_id is not None and task["assignedWorkerId"] != worker_id:
            raise ValueError(f"Task is assigned to {task['assignedWorkerId']}, not {worker_id}")
        previous_worker_id = task["assignedWorkerId"]
        now = self.now()
        self._conn.execute(
            """
            UPDATE collaboration_tasks
            SET status = 'queued', assigned_worker_id = NULL, claimed_at = NULL, updated_at = ?
            WHERE id = ?
            """,
            (now, task_id),
        )
        if previous_worker_id is not None:
            self._conn.execute(
                """
                UPDATE agent_workers
                SET status = 'idle', current_task_id = NULL, last_heartbeat_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (now, now, previous_worker_id),
            )
        self._conn.commit()
        return {"task": self.require_collaboration_task(task_id)}

    def _finalize_collaboration_task(
        self,
        *,
        task_id: str,
        worker_id: str,
        task_status: str,
        worker_status: str,
        result: dict[str, Any] | None,
        error: Any,
    ) -> dict[str, Any]:
        if task_status not in {"completed", "failed"}:
            raise ValueError(f"Unsupported final collaboration task status: {task_status}")
        if worker_status not in {"idle", "failed"}:
            raise ValueError(f"Unsupported final worker status: {worker_status}")

        now = self.now()
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            task_row = self._conn.execute(
                "SELECT * FROM collaboration_tasks WHERE id = ?",
                (task_id,),
            ).fetchone()
            if task_row is None:
                raise ValueError(f"Collaboration task not found: {task_id}")
            worker_row = self._conn.execute(
                "SELECT * FROM agent_workers WHERE id = ?",
                (worker_id,),
            ).fetchone()
            if worker_row is None:
                raise ValueError(f"Agent worker not found: {worker_id}")

            task = self._serialize_collaboration_task(dict(task_row))
            worker = self._serialize_agent_worker(dict(worker_row))
            if task["assignedWorkerId"] != worker_id:
                raise ValueError(f"Task is assigned to {task['assignedWorkerId']}, not {worker_id}")
            if worker["currentTaskId"] != task_id:
                raise ValueError(f"Worker is not assigned to task {task_id}")
            if task["status"] not in {"claimed", "running", "blocked"}:
                raise ValueError(f"Task cannot be finalized from status: {task['status']}")

            self._conn.execute(
                """
                UPDATE collaboration_tasks
                SET status = ?, result_json = ?, error_json = ?, completed_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    task_status,
                    json.dumps(result, ensure_ascii=False, sort_keys=True) if result is not None else None,
                    json.dumps(error, ensure_ascii=False, sort_keys=True) if error is not None else None,
                    now,
                    now,
                    task_id,
                ),
            )
            self._conn.execute(
                """
                UPDATE agent_workers
                SET status = ?, current_task_id = NULL, last_heartbeat_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (worker_status, now, now, worker_id),
            )
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

        return {
            "task": self.require_collaboration_task(task_id),
            "worker": self.require_agent_worker(worker_id),
        }

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
                 max_context_tokens, prompt_layers_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
    # Trace Events
    # ------------------------------------------------------------------

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
        sequence_row = self._conn.execute("SELECT COALESCE(MAX(sequence), 0) + 1 FROM trace_events").fetchone()
        sequence = int(sequence_row[0])
        payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True)
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
        if normalized_type in {"command.started", "command.completed", "command.failed"}:
            return None

        payload = getattr(event, "payload", {})
        if isinstance(payload, dict):
            bridge = payload.get("_bridge")
            if isinstance(bridge, dict) and bool(bridge.get("skipTraceMirror")):
                return None
        event_visibility = getattr(event, "visibility", "chat")
        if normalized_type.startswith("collab."):
            if not str(task_id).startswith("ctask_"):
                return None
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

    def resolve_approval(self, approval_id: str, decision: str) -> dict[str, Any]:
        now = self.now()
        self._conn.execute(
            """
            UPDATE approvals
            SET decision = ?, decided_by = 'user', decided_at = ?
            WHERE id = ?
            """,
            (decision, now, approval_id),
        )
        self._conn.commit()
        row = self._conn.execute(
            "SELECT * FROM approvals WHERE id = ?",
            (approval_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"Approval not found: {approval_id}")
        approval = self._serialize_approval(dict(row))
        self.append_trace_event(
            task_id=approval["taskId"],
            event_type="approval.resolved",
            source="approval",
            related_id=approval["id"],
            payload={
                "approvalId": approval["id"],
                "kind": approval["kind"],
                "decision": approval["decision"],
                "decidedBy": approval["decidedBy"],
            },
            created_at=approval["decidedAt"],
        )
        return approval

    def create_patch(
        self,
        *,
        task_id: str,
        workspace_id: str,
        summary: str,
        diff_text: str,
        files_changed: int,
        status: str = "proposed",
    ) -> dict[str, Any]:
        patch_id = self.new_id("patch")
        now = self.now()
        self._conn.execute(
            """
            INSERT INTO patches (
                id, task_id, workspace_id, summary, diff_text, status, files_changed, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                patch_id,
                task_id,
                workspace_id,
                summary,
                diff_text,
                status,
                files_changed,
                now,
                now,
            ),
        )
        self._conn.commit()
        row = self._conn.execute("SELECT * FROM patches WHERE id = ?", (patch_id,)).fetchone()
        if row is None:
            raise ValueError(f"Patch not found: {patch_id}")
        patch = self._serialize_patch(dict(row))
        self.append_trace_event(
            task_id=patch["taskId"],
            event_type="patch.proposed" if patch["status"] == "proposed" else f"patch.{patch['status']}",
            source="patch",
            related_id=patch["id"],
            payload={
                "patchId": patch["id"],
                "workspaceId": patch["workspaceId"],
                "summary": patch["summary"],
                "status": patch["status"],
                "filesChanged": patch["filesChanged"],
            },
            created_at=patch["createdAt"],
        )
        return patch

    def update_patch(
        self,
        patch_id: str,
        *,
        status: str | None = None,
        summary: str | None = None,
        diff_text: str | None = None,
        files_changed: int | None = None,
    ) -> dict[str, Any]:
        assignments: list[str] = ["updated_at = ?"]
        values: list[Any] = [self.now()]

        if status is not None:
            assignments.append("status = ?")
            values.append(status)
        if summary is not None:
            assignments.append("summary = ?")
            values.append(summary)
        if diff_text is not None:
            assignments.append("diff_text = ?")
            values.append(diff_text)
        if files_changed is not None:
            assignments.append("files_changed = ?")
            values.append(files_changed)

        values.append(patch_id)
        self._conn.execute(
            f"UPDATE patches SET {', '.join(assignments)} WHERE id = ?",
            values,
        )
        self._conn.commit()
        row = self._conn.execute("SELECT * FROM patches WHERE id = ?", (patch_id,)).fetchone()
        if row is None:
            raise ValueError(f"Patch not found: {patch_id}")
        patch = self._serialize_patch(dict(row))
        self.append_trace_event(
            task_id=patch["taskId"],
            event_type=f"patch.{patch['status']}",
            source="patch",
            related_id=patch["id"],
            payload={
                "patchId": patch["id"],
                "workspaceId": patch["workspaceId"],
                "summary": patch["summary"],
                "status": patch["status"],
                "filesChanged": patch["filesChanged"],
            },
            created_at=patch["updatedAt"],
        )
        return patch

    def get_approval(self, params: dict[str, Any]) -> dict[str, Any]:
        row = self._conn.execute(
            "SELECT * FROM approvals WHERE id = ?",
            (params["approvalId"],),
        ).fetchone()
        if row is None:
            raise ValueError(f"Approval not found: {params['approvalId']}")
        return {"approval": self._serialize_approval(dict(row))}

    def create_approval(self, task_id: str, kind: str, request: dict[str, Any]) -> dict[str, Any]:
        approval_id = self.new_id("appr")
        now = self.now()
        request_json = json.dumps(request, ensure_ascii=False, sort_keys=True)
        self._conn.execute(
            """
            INSERT INTO approvals (id, task_id, kind, request_json, decision, decided_by, created_at, decided_at)
            VALUES (?, ?, ?, ?, NULL, NULL, ?, NULL)
            """,
            (approval_id, task_id, kind, request_json, now),
        )
        self._conn.commit()
        row = self._conn.execute("SELECT * FROM approvals WHERE id = ?", (approval_id,)).fetchone()
        if row is None:
            raise ValueError(f"Approval not found: {approval_id}")
        approval = self._serialize_approval(dict(row))
        self.append_trace_event(
            task_id=approval["taskId"],
            event_type="approval.requested",
            source="approval",
            related_id=approval["id"],
            payload={
                "approvalId": approval["id"],
                "kind": approval["kind"],
                "request": request,
            },
            created_at=approval["createdAt"],
        )
        return approval

    def find_approval(
        self,
        *,
        task_id: str,
        kind: str,
        request: dict[str, Any],
        decision: str | None = None,
    ) -> dict[str, Any] | None:
        request_json = json.dumps(request, ensure_ascii=False, sort_keys=True)
        query = ["SELECT * FROM approvals WHERE task_id = ? AND kind = ? AND request_json = ?"]
        values: list[Any] = [task_id, kind, request_json]
        if decision is not None:
            query.append("AND decision = ?")
            values.append(decision)
        query.append("ORDER BY created_at DESC LIMIT 1")
        row = self._conn.execute(" ".join(query), values).fetchone()
        if row is None:
            return None
        return self._serialize_approval(dict(row))

    def find_latest_approval(self, *, task_id: str, decision: str | None = None) -> dict[str, Any] | None:
        query = ["SELECT * FROM approvals WHERE task_id = ?"]
        values: list[Any] = [task_id]
        if decision is not None:
            query.append("AND decision = ?")
            values.append(decision)
        query.append("ORDER BY created_at DESC LIMIT 1")
        row = self._conn.execute(" ".join(query), values).fetchone()
        if row is None:
            return None
        return self._serialize_approval(dict(row))

    def upsert_pending_react_state(
        self,
        *,
        task_id: str,
        session_id: str,
        goal: str,
        context: dict[str, Any],
        messages: list[dict[str, Any]],
        tool_results: list[dict[str, Any]],
        pending_tool_call: dict[str, Any],
        pending_tool_spec: dict[str, Any],
        remaining_tool_calls: list[dict[str, Any]],
        steps: int,
        react_started: bool,
    ) -> dict[str, Any]:
        now = self.now()
        self._conn.execute(
            """
            INSERT INTO pending_react_tasks (
                task_id, session_id, goal, context_json, messages_json, tool_results_json,
                pending_tool_call_json, pending_tool_spec_json, remaining_tool_calls_json,
                steps, react_started, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(task_id) DO UPDATE SET
                session_id = excluded.session_id,
                goal = excluded.goal,
                context_json = excluded.context_json,
                messages_json = excluded.messages_json,
                tool_results_json = excluded.tool_results_json,
                pending_tool_call_json = excluded.pending_tool_call_json,
                pending_tool_spec_json = excluded.pending_tool_spec_json,
                remaining_tool_calls_json = excluded.remaining_tool_calls_json,
                steps = excluded.steps,
                react_started = excluded.react_started,
                updated_at = excluded.updated_at
            """,
            (
                task_id,
                session_id,
                goal,
                json.dumps(context, ensure_ascii=False),
                json.dumps(messages, ensure_ascii=False),
                json.dumps(tool_results, ensure_ascii=False),
                json.dumps(pending_tool_call, ensure_ascii=False),
                json.dumps(pending_tool_spec, ensure_ascii=False),
                json.dumps(remaining_tool_calls, ensure_ascii=False),
                int(steps),
                1 if react_started else 0,
                now,
                now,
            ),
        )
        self._conn.commit()
        state = self.get_pending_react_state(task_id)
        if state is None:
            raise ValueError(f"Pending ReAct state not found after upsert: {task_id}")
        return state

    def get_pending_react_state(self, task_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM pending_react_tasks WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        if row is None:
            return None
        return self._serialize_pending_react_state(dict(row))

    def delete_pending_react_state(self, task_id: str) -> None:
        self._conn.execute("DELETE FROM pending_react_tasks WHERE task_id = ?", (task_id,))
        self._conn.commit()

    # ------------------------------------------------------------------
    # Pending DAG state
    # ------------------------------------------------------------------

    def upsert_pending_dag_state(
        self,
        *,
        task_id: str,
        session_id: str,
        goal: str,
        context: dict[str, Any],
        plan_json: str,
        completed_ids: list[str],
        failed_ids: list[str],
        results: dict[str, str],
    ) -> dict[str, Any]:
        now = self.now()
        self._conn.execute(
            """
            INSERT INTO pending_dag_tasks (
                task_id, session_id, goal, context_json, plan_json,
                completed_ids_json, failed_ids_json, results_json,
                created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(task_id) DO UPDATE SET
                session_id = excluded.session_id,
                goal = excluded.goal,
                context_json = excluded.context_json,
                plan_json = excluded.plan_json,
                completed_ids_json = excluded.completed_ids_json,
                failed_ids_json = excluded.failed_ids_json,
                results_json = excluded.results_json,
                updated_at = excluded.updated_at
            """,
            (
                task_id,
                session_id,
                goal,
                json.dumps(context, ensure_ascii=False),
                plan_json,
                json.dumps(completed_ids, ensure_ascii=False),
                json.dumps(failed_ids, ensure_ascii=False),
                json.dumps(results, ensure_ascii=False),
                now,
                now,
            ),
        )
        self._conn.commit()
        state = self.get_pending_dag_state(task_id)
        if state is None:
            raise ValueError(f"Pending DAG state not found after upsert: {task_id}")
        return state

    def get_pending_dag_state(self, task_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM pending_dag_tasks WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        if row is None:
            return None
        d = dict(row)
        return {
            "task_id": d["task_id"],
            "session_id": d["session_id"],
            "goal": d["goal"],
            "context": json.loads(d["context_json"]),
            "plan": json.loads(d["plan_json"]),
            "completed": json.loads(d["completed_ids_json"]),
            "failed": json.loads(d["failed_ids_json"]),
            "results": json.loads(d["results_json"]),
        }

    def delete_pending_dag_state(self, task_id: str) -> None:
        self._conn.execute("DELETE FROM pending_dag_tasks WHERE task_id = ?", (task_id,))
        self._conn.commit()

    def list_tasks_by_status(self, statuses: list[str]) -> list[dict[str, Any]]:
        """Return tasks matching any of the given statuses."""
        placeholders = ",".join("?" for _ in statuses)
        rows = self._conn.execute(
            f"SELECT * FROM tasks WHERE status IN ({placeholders})",
            tuple(statuses),
        ).fetchall()
        return [self._serialize_task(dict(r)) for r in rows]

    def list_tasks_by_session_and_status(self, session_id: str, status: str) -> list[dict[str, Any]]:
        """Return tasks matching session_id and status, ordered by created_at."""
        rows = self._conn.execute(
            "SELECT * FROM tasks WHERE session_id = ? AND status = ? ORDER BY created_at ASC",
            (session_id, status),
        ).fetchall()
        return [self._serialize_task(dict(r)) for r in rows]

    def get_patch(self, params: dict[str, Any]) -> dict[str, Any]:
        row = self._conn.execute(
            "SELECT * FROM patches WHERE id = ?",
            (params["patchId"],),
        ).fetchone()
        if row is None:
            raise ValueError(f"Patch not found: {params['patchId']}")
        patch = self._serialize_patch(dict(row))
        return {"patch": patch, "diffText": patch["diffText"]}

    def find_patch(
        self,
        *,
        task_id: str,
        workspace_id: str,
        diff_text: str,
    ) -> dict[str, Any] | None:
        row = self._conn.execute(
            """
            SELECT * FROM patches
            WHERE task_id = ? AND workspace_id = ? AND diff_text = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (task_id, workspace_id, diff_text),
        ).fetchone()
        if row is None:
            return None
        return self._serialize_patch(dict(row))

    def get_command_log(self, params: dict[str, Any]) -> dict[str, Any]:
        row = self._conn.execute(
            "SELECT * FROM command_logs WHERE id = ?",
            (params["commandId"],),
        ).fetchone()
        if row is None:
            raise ValueError(f"Command log not found: {params['commandId']}")
        return {"commandLog": self._serialize_command_log(dict(row))}

    def list_command_logs(self, params: dict[str, Any]) -> dict[str, Any]:
        task_id = params.get("taskId") or params.get("task_id")
        session_id = params.get("sessionId") or params.get("session_id")
        status = params.get("status")
        limit = int(params.get("limit", 100))
        limit = max(1, min(limit, 1000))

        query = [
            """
            SELECT command_logs.*
            FROM command_logs
            JOIN tasks ON tasks.id = command_logs.task_id
            """
        ]
        where: list[str] = []
        values: list[Any] = []
        if isinstance(task_id, str) and task_id.strip():
            where.append("command_logs.task_id = ?")
            values.append(task_id.strip())
        if isinstance(session_id, str) and session_id.strip():
            where.append("tasks.session_id = ?")
            values.append(session_id.strip())
        if isinstance(status, str) and status.strip():
            where.append("command_logs.status = ?")
            values.append(status.strip())
        if where:
            query.append("WHERE " + " AND ".join(where))
        query.append("ORDER BY command_logs.started_at DESC LIMIT ?")
        values.append(limit)

        rows = self._conn.execute("\n".join(query), values).fetchall()
        return {"commandLogs": [self._serialize_command_log(dict(row)) for row in rows]}

    def create_command_log(
        self,
        *,
        task_id: str,
        command: str,
        cwd: str,
        shell: str,
    ) -> dict[str, Any]:
        command_id = self.new_id("cmd")
        now = self.now()
        self._conn.execute(
            """
            INSERT INTO command_logs (
                id, task_id, command, cwd, exit_code, status, stdout_path, stderr_path, started_at, finished_at
            )
            VALUES (?, ?, ?, ?, NULL, 'running', NULL, NULL, ?, NULL)
            """,
            (command_id, task_id, command, cwd, now),
        )
        self._conn.commit()
        row = self._conn.execute("SELECT * FROM command_logs WHERE id = ?", (command_id,)).fetchone()
        if row is None:
            raise ValueError(f"Command log not found: {command_id}")
        record = self._serialize_command_log(dict(row))
        record["shell"] = shell
        self.append_trace_event(
            task_id=record["taskId"],
            event_type="command.started",
            source="command",
            related_id=record["id"],
            payload={
                "commandId": record["id"],
                "command": record["command"],
                "cwd": record["cwd"],
                "shell": shell,
                "status": record["status"],
            },
            created_at=record["startedAt"],
        )
        return record

    def update_command_log(
        self,
        command_id: str,
        *,
        status: str,
        exit_code: int | None,
        stdout_path: str | None = None,
        stderr_path: str | None = None,
        finished_at: int | None = None,
    ) -> dict[str, Any]:
        finished = finished_at if finished_at is not None else self.now()
        self._conn.execute(
            """
            UPDATE command_logs
            SET status = ?, exit_code = ?, stdout_path = ?, stderr_path = ?, finished_at = ?
            WHERE id = ?
            """,
            (status, exit_code, stdout_path, stderr_path, finished, command_id),
        )
        self._conn.commit()
        row = self._conn.execute("SELECT * FROM command_logs WHERE id = ?", (command_id,)).fetchone()
        if row is None:
            raise ValueError(f"Command log not found: {command_id}")
        command_log = self._serialize_command_log(dict(row))
        event_type = "command.completed" if command_log["status"] == "completed" else "command.failed"
        self.append_trace_event(
            task_id=command_log["taskId"],
            event_type=event_type,
            source="command",
            related_id=command_log["id"],
            payload={
                "commandId": command_log["id"],
                "command": command_log["command"],
                "cwd": command_log["cwd"],
                "status": command_log["status"],
                "exitCode": command_log["exitCode"],
                "durationMs": command_log["durationMs"],
                "stdoutPath": command_log["stdoutPath"],
                "stderrPath": command_log["stderrPath"],
            },
            created_at=command_log["finishedAt"],
        )
        return command_log

    def write_command_artifact(self, command_id: str, stream_name: str, content: str) -> str:
        artifact_path = self._artifact_dir / f"{command_id}_{stream_name}.log"
        artifact_path.write_text(content, encoding="utf-8", errors="replace")
        return str(artifact_path)

    def get_config(self, _params: dict[str, Any]) -> dict[str, Any]:
        if self._config_snapshot is None:
            self._config_snapshot = deepcopy(self._config)
        return {"config": self._config_snapshot}

    def update_config(self, params: dict[str, Any]) -> dict[str, Any]:
        patch = params.get("config", params)
        if not isinstance(patch, dict):
            raise ValueError("config.update expects an object payload")

        merged = self._merge_config(self._config, patch)
        provider_patch = patch.get("provider") if isinstance(patch.get("provider"), dict) else None
        if isinstance(provider_patch, dict) and "profiles" not in provider_patch:
            self._apply_provider_patch_to_active_profile(merged, provider_patch)
        self._config = self._normalize_config(merged)
        self._config_snapshot = None
        self._persist_config(self._config)
        return {"config": deepcopy(self._config)}

    # -- Feature flags --

    def get_feature_flag(self, key: str, default: bool = False) -> bool:
        """Read a feature flag value from config.features."""
        features = self._config.get("features")
        if not isinstance(features, dict):
            return default
        return bool(features.get(key, default))

    def set_feature_flag(self, key: str, value: bool) -> dict[str, Any]:
        """Set a feature flag value in config.features and persist."""
        features = self._config.get("features")
        if not isinstance(features, dict):
            features = deepcopy(DEFAULT_CONFIG.get("features", {}))
        features[key] = value
        self._config["features"] = features
        self._config_snapshot = None
        self._persist_config(self._config)
        return {"features": deepcopy(features)}

    def list_feature_flags(self) -> dict[str, Any]:
        """Return all feature flags with current values."""
        features = self._config.get("features")
        if not isinstance(features, dict):
            features = deepcopy(DEFAULT_CONFIG.get("features", {}))
        return {"features": deepcopy(features)}

    def update_provider_profile_health(
        self,
        profile_id: str,
        *,
        last_checked_at: int,
        last_status: str,
        last_error_summary: str | None,
    ) -> dict[str, Any]:
        provider = deepcopy(self._config.get("provider"))
        if not isinstance(provider, dict):
            return {"config": deepcopy(self._config)}

        profiles = provider.get("profiles")
        if not isinstance(profiles, list):
            return {"config": deepcopy(self._config)}

        updated = False
        for profile in profiles:
            if not isinstance(profile, dict) or profile.get("id") != profile_id:
                continue
            profile["lastCheckedAt"] = int(last_checked_at)
            profile["lastStatus"] = str(last_status)
            if isinstance(last_error_summary, str) and last_error_summary.strip():
                profile["lastErrorSummary"] = last_error_summary.strip()
            else:
                profile.pop("lastErrorSummary", None)
            updated = True
            break

        if not updated:
            return {"config": deepcopy(self._config)}

        provider["profiles"] = profiles
        self._config = self._normalize_config({
            **deepcopy(self._config),
            "provider": provider,
        })
        self._config_snapshot = None
        self._persist_config(self._config)
        return {"config": deepcopy(self._config)}

    def _serialize_workspace(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": row["id"],
            "name": row["name"],
            "rootPath": row["root_path"],
            "focus": row.get("focus"),
            "summary": row.get("summary"),
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        }

    def _serialize_session(self, row: dict[str, Any]) -> dict[str, Any]:
        session = {
            "id": row["id"],
            "workspaceId": row["workspace_id"],
            "title": row["title"],
            "status": row["status"],
            "summary": row["summary"],
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        }
        if row.get("workspace_name"):
            session["workspaceName"] = row["workspace_name"]
        if row.get("workspace_root"):
            session["workspaceRoot"] = row["workspace_root"]
        return session

    def _serialize_message(self, row: dict[str, Any]) -> dict[str, Any]:
        message = {
            "id": row["id"],
            "sessionId": row["session_id"],
            "role": row["role"],
            "content": row["content"],
            "createdAt": row["created_at"],
        }
        if row.get("task_id"):
            message["taskId"] = row["task_id"]
        # New fields — use .get() for backward compat with legacy rows
        if row.get("client_message_id"):
            message["clientMessageId"] = row["client_message_id"]
        if row.get("kind") and row["kind"] != "normal":
            message["kind"] = row["kind"]
        if row.get("status") and row["status"] != "completed":
            message["status"] = row["status"]
        if row.get("created_seq") is not None:
            message["createdSeq"] = row["created_seq"]
        if row.get("updated_at") is not None:
            message["updatedAt"] = row["updated_at"]
        return message

    def _serialize_scheduled_task(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": row["id"],
            "name": row["name"],
            "prompt": row["prompt"],
            "schedule": row["schedule"],
            "status": row["status"],
            "enabled": bool(row["enabled"]),
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
            "lastRunAt": row["last_run_at"],
            "nextRunAt": row["next_run_at"],
        }

    def _serialize_scheduled_task_run(self, row: dict[str, Any]) -> dict[str, Any]:
        started_at = row["started_at"]
        finished_at = row["finished_at"]
        duration_ms = None if finished_at is None else max(0, int(finished_at) - int(started_at))
        return {
            "id": row["id"],
            "taskId": row["task_id"],
            "status": row["status"],
            "startedAt": started_at,
            "finishedAt": finished_at,
            "durationMs": duration_ms,
            "summary": row["summary"],
            "error": row["error"],
        }

    def _serialize_task(self, row: dict[str, Any]) -> dict[str, Any]:
        result = {
            "id": row["id"],
            "sessionId": row["session_id"],
            "type": row["type"],
            "status": row["status"],
            "goal": row["goal"],
            "acceptanceCriteria": self._json_list(row.get("acceptance_criteria_json")),
            "outOfScope": self._json_list(row.get("out_of_scope_json")),
            "currentStep": row.get("current_step"),
            "plan": self._json_list(row.get("plan_json")),
            "changedFiles": self._json_list(row.get("changed_files_json")),
            "commands": self._json_list(row.get("commands_json")),
            "verification": self._json_list(row.get("verification_json")),
            "reflection": self._json_dict(row.get("reflection_json")),
            "routing": self._json_dict(row.get("routing_json")),
            "summary": row.get("summary"),
            "resultSummary": row["result_json"],
            "errorCode": row["error_code"],
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        }
        # New fields — use .get() for backward compat with legacy rows
        if row.get("root_task_id"):
            result["rootTaskId"] = row["root_task_id"]
        if row.get("role") and row["role"] != "root":
            result["role"] = row["role"]
        if row.get("active_assistant_message_id"):
            result["activeAssistantMessageId"] = row["active_assistant_message_id"]
        if row.get("created_seq") is not None:
            result["createdSeq"] = row["created_seq"]
        tests_run_raw = row.get("tests_run_json")
        if tests_run_raw:
            result["testsRun"] = self._json_list(tests_run_raw)
        risks_raw = row.get("risks_json")
        if risks_raw:
            result["risks"] = self._json_list(risks_raw)
        structured_raw = row.get("structured_result_json")
        if structured_raw:
            result["structuredResult"] = self._json_dict(structured_raw)
        return result

    def _json_list(self, raw: Any) -> list[Any]:
        if not raw:
            return []
        try:
            parsed = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            return []
        return parsed if isinstance(parsed, list) else []

    def _json_dict(self, raw: Any) -> dict[str, Any] | None:
        if not raw:
            return None
        try:
            parsed = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            return None
        return parsed if isinstance(parsed, dict) else None

    def _current_step_from_plan(self, plan: list[dict[str, Any]]) -> str | None:
        for preferred_status in ("active", "pending"):
            for step in plan:
                if step.get("status") == preferred_status and isinstance(step.get("title"), str):
                    return step["title"]
        first_title = plan[0].get("title") if plan else None
        return first_title if isinstance(first_title, str) else None

    def _serialize_collaboration_task(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": row["id"],
            "sessionId": row["session_id"],
            "parentTaskId": row["parent_task_id"],
            "title": row["title"],
            "description": row["description"],
            "status": row["status"],
            "priority": row["priority"],
            "assignedWorkerId": row["assigned_worker_id"],
            "dependencies": json.loads(row["dependencies_json"] or "[]"),
            "result": json.loads(row["result_json"] or "{}"),
            "error": json.loads(row["error_json"] or "null"),
            "metadata": json.loads(row["metadata_json"] or "{}"),
            "claimedAt": row["claimed_at"],
            "completedAt": row["completed_at"],
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        }

    def _serialize_agent_worker(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": row["id"],
            "name": row["name"],
            "role": row["role"],
            "status": row["status"],
            "currentTaskId": row["current_task_id"],
            "capabilities": json.loads(row["capabilities_json"] or "[]"),
            "metadata": json.loads(row["metadata_json"] or "{}"),
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
            "lastHeartbeatAt": row["last_heartbeat_at"],
        }

    def _serialize_agent_message(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": row["id"],
            "senderWorkerId": row["sender_worker_id"],
            "recipientWorkerId": row["recipient_worker_id"],
            "taskId": row["task_id"],
            "kind": row["kind"],
            "body": row["body"],
            "payload": json.loads(row["payload_json"] or "{}"),
            "createdAt": row["created_at"],
            "readAt": row["read_at"],
        }

    def _serialize_approval(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": row["id"],
            "taskId": row["task_id"],
            "kind": row["kind"],
            "requestJson": row["request_json"],
            "decision": row["decision"],
            "decidedBy": row["decided_by"],
            "createdAt": row["created_at"],
            "decidedAt": row["decided_at"],
        }

    def _serialize_patch(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": row["id"],
            "taskId": row["task_id"],
            "workspaceId": row["workspace_id"],
            "summary": row["summary"] or "",
            "diffText": row["diff_text"],
            "status": row["status"],
            "filesChanged": row["files_changed"],
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        }

    def _serialize_command_log(self, row: dict[str, Any]) -> dict[str, Any]:
        started_at = row["started_at"]
        finished_at = row["finished_at"]
        duration_ms = None if finished_at is None else max(0, int(finished_at) - int(started_at))
        return {
            "id": row["id"],
            "taskId": row["task_id"],
            "command": row["command"],
            "cwd": row["cwd"],
            "status": row["status"],
            "exitCode": row["exit_code"],
            "startedAt": started_at,
            "finishedAt": finished_at,
            "durationMs": duration_ms,
            "stdoutPath": row["stdout_path"],
            "stderrPath": row["stderr_path"],
        }

    def _serialize_trace_event(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": row["id"],
            "taskId": row["task_id"],
            "sessionId": row["session_id"],
            "type": row["type"],
            "source": row["source"],
            "relatedId": row["related_id"],
            "payload": json.loads(row["payload_json"]),
            "createdAt": row["created_at"],
            "sequence": row["sequence"],
            "visibility": row.get("visibility", "chat"),
        }

    def _require_non_empty(self, params: dict[str, Any], key: str) -> str:
        value = params.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{key} is required")
        return value.strip()

    def _optional_non_empty(self, params: dict[str, Any], key: str, fallback: str) -> str:
        if key not in params:
            return fallback
        return self._require_non_empty(params, key)

    def _optional_string(self, params: dict[str, Any], key: str) -> str | None:
        value = params.get(key)
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError(f"{key} must be a string")
        stripped = value.strip()
        return stripped or None

    def _dict_value(self, value: Any, key: str) -> dict[str, Any]:
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ValueError(f"{key} must be an object")
        return deepcopy(value)

    def _json_value(self, value: Any, key: str) -> Any:
        try:
            json.dumps(value, ensure_ascii=False, sort_keys=True)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{key} must be JSON serializable") from exc
        return deepcopy(value)


    def _normalize_priority(self, value: Any) -> int:
        try:
            priority = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("priority must be an integer") from exc
        return max(0, min(priority, 9))

    def _normalize_collaboration_task_status(self, value: Any) -> str:
        allowed = {"queued", "claimed", "running", "blocked", "completed", "failed", "cancelled"}
        if value not in allowed:
            raise ValueError(f"Unsupported collaboration task status: {value}")
        return str(value)

    def _normalize_agent_worker_status(self, value: Any) -> str:
        allowed = {"idle", "busy", "offline", "stopped", "failed"}
        if value not in allowed:
            raise ValueError(f"Unsupported agent worker status: {value}")
        return str(value)

    def _normalize_agent_message_kind(self, value: Any) -> str:
        allowed = {"note", "handoff", "broadcast", "result", "system"}
        if value not in allowed:
            raise ValueError(f"Unsupported agent message kind: {value}")
        return str(value)

    def _normalize_scheduled_status(self, status: Any, enabled: bool) -> str:
        if not enabled:
            return "disabled"
        if status == "disabled":
            return "disabled"
        return "active"

    def _next_scheduled_run_at(self, schedule: str, base_ms: int) -> int:
        normalized = schedule.strip().lower()
        minutes = 30
        match = re.search(r"every\s+(\d+)\s*(minute|minutes|min|hour|hours|hr|hrs)", normalized)
        if match:
            amount = max(1, int(match.group(1)))
            unit = match.group(2)
            minutes = amount * 60 if unit.startswith(("hour", "hr")) else amount
        return int(base_ms) + minutes * 60_000

    def _trace_related_id(self, payload: Any) -> str | None:
        if not isinstance(payload, dict):
            return None
        for key in ("toolCallId", "approvalId", "patchId", "commandId", "providerRequestId"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                return value
        return None

    def _trace_source(self, event_type: str) -> str:
        source, _separator, _name = event_type.partition(".")
        return source or "runtime"

    def _serialize_pending_react_state(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "task_id": row["task_id"],
            "session_id": row["session_id"],
            "goal": row["goal"],
            "context": json.loads(row["context_json"] or "{}"),
            "messages": json.loads(row["messages_json"] or "[]"),
            "tool_results": json.loads(row["tool_results_json"] or "[]"),
            "pending_tool_call": json.loads(row["pending_tool_call_json"] or "{}"),
            "pending_tool_spec": json.loads(row["pending_tool_spec_json"] or "{}"),
            "remaining_tool_calls": json.loads(row["remaining_tool_calls_json"] or "[]"),
            "steps": int(row["steps"]),
            "react_started": bool(row["react_started"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def _merge_config(self, base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
        merged = deepcopy(base)
        for key, value in patch.items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = self._merge_config(merged[key], value)
            elif value is not None:
                merged[key] = deepcopy(value)
        return merged

    def _load_or_initialize_config(self) -> dict[str, Any]:
        row = self._conn.execute(
            "SELECT value FROM config WHERE key = ?",
            (CONFIG_KEY,),
        ).fetchone()
        if row is None:
            config = deepcopy(DEFAULT_CONFIG)
            self._persist_config(config)
            return config

        try:
            loaded = json.loads(row["value"])
        except json.JSONDecodeError:
            loaded = {}

        if not isinstance(loaded, dict):
            loaded = {}

        config = self._normalize_config(self._merge_config(DEFAULT_CONFIG, loaded))
        if config != loaded:
            self._persist_config(config)
        return config

    def _normalize_config(self, config: dict[str, Any]) -> dict[str, Any]:
        normalized = deepcopy(config)
        provider = normalized.get("provider")
        if not isinstance(provider, dict):
            normalized["provider"] = deepcopy(DEFAULT_CONFIG["provider"])
        else:
            normalized["provider"] = self._normalize_provider_config(provider)

        autonomy = normalized.get("autonomy")
        normalized["autonomy"] = self._normalize_autonomy_config(
            autonomy if isinstance(autonomy, dict) else {},
        )
        agent_soul = normalized.get("agentSoul")
        normalized["agentSoul"] = self._normalize_agent_soul_config(
            agent_soul if isinstance(agent_soul, dict) else {},
        )
        return normalized

    def _normalize_autonomy_config(self, autonomy: dict[str, Any]) -> dict[str, Any]:
        normalized = deepcopy(DEFAULT_CONFIG["autonomy"])
        normalized.update(deepcopy(autonomy))
        raw_profiles = normalized.get("profiles")
        profiles = [
            self._normalize_autonomy_profile(item)
            for item in raw_profiles or []
            if isinstance(item, dict)
        ]
        if not profiles:
            profiles = [self._normalize_autonomy_profile(DEFAULT_AUTONOMY_PROFILE)]
        profiles = self._dedupe_profiles(profiles, fallback_prefix="autonomy")
        active_profile_id = self._valid_active_profile_id(
            normalized.get("activeProfileId"),
            profiles,
        )
        normalized["activeProfileId"] = active_profile_id
        normalized["profiles"] = profiles
        return normalized

    def _normalize_autonomy_profile(self, profile: dict[str, Any]) -> dict[str, Any]:
        merged = deepcopy(DEFAULT_AUTONOMY_PROFILE)
        merged.update(deepcopy(profile))
        profile_id = self._safe_profile_id(merged.get("id"), "autonomy")
        name = self._safe_profile_name(merged.get("name"), profile_id)
        return {
            **merged,
            "id": profile_id,
            "name": name,
            "level": str(merged.get("level") or "L2"),
            "maxSteps": self._bounded_int(merged.get("maxSteps"), 1, 200, DEFAULT_AUTONOMY_PROFILE["maxSteps"]),
            "maxParallelSubtasks": self._bounded_int(
                merged.get("maxParallelSubtasks"), 1, 32, DEFAULT_AUTONOMY_PROFILE["maxParallelSubtasks"],
            ),
            "allowBackground": bool(merged.get("allowBackground")),
            "allowSubagents": bool(merged.get("allowSubagents")),
            "allowFileWrite": self._string_or_default(merged.get("allowFileWrite"), "approval_required"),
            "allowShell": self._string_or_default(merged.get("allowShell"), "approval_required"),
            "allowNetwork": bool(merged.get("allowNetwork")),
            "memoryRecallPolicy": self._string_or_default(
                merged.get("memoryRecallPolicy"), DEFAULT_AUTONOMY_PROFILE["memoryRecallPolicy"],
            ),
            "retryLimit": self._bounded_int(merged.get("retryLimit"), 0, 20, DEFAULT_AUTONOMY_PROFILE["retryLimit"]),
            "timeoutMs": self._bounded_int(merged.get("timeoutMs"), 1000, 86400000, DEFAULT_AUTONOMY_PROFILE["timeoutMs"]),
        }

    def _normalize_agent_soul_config(self, agent_soul: dict[str, Any]) -> dict[str, Any]:
        normalized = deepcopy(DEFAULT_CONFIG["agentSoul"])
        normalized.update(deepcopy(agent_soul))
        raw_profiles = normalized.get("profiles")
        profiles = [
            self._normalize_agent_soul_profile(item)
            for item in raw_profiles or []
            if isinstance(item, dict)
        ]
        if not profiles:
            profiles = [self._normalize_agent_soul_profile(DEFAULT_AGENT_SOUL_PROFILE)]
        profiles = self._dedupe_profiles(profiles, fallback_prefix="soul")
        active_profile_id = self._valid_active_profile_id(
            normalized.get("activeProfileId"),
            profiles,
        )
        workspace_instructions = normalized.get("workspaceInstructions")
        normalized["activeProfileId"] = active_profile_id
        normalized["workspaceInstructions"] = (
            workspace_instructions.strip() if isinstance(workspace_instructions, str) else ""
        )
        normalized["sessionOverrideEnabled"] = bool(normalized.get("sessionOverrideEnabled", False))
        normalized["profiles"] = profiles
        return normalized

    def _normalize_agent_soul_profile(self, profile: dict[str, Any]) -> dict[str, Any]:
        merged = deepcopy(DEFAULT_AGENT_SOUL_PROFILE)
        merged.update(deepcopy(profile))
        profile_id = self._safe_profile_id(merged.get("id"), "soul")
        name = self._safe_profile_name(merged.get("name"), profile_id)
        return {
            **merged,
            "id": profile_id,
            "name": name,
            "description": self._string_or_default(merged.get("description"), ""),
            "identity": self._string_or_default(merged.get("identity"), DEFAULT_AGENT_SOUL_PROFILE["identity"]),
            "principles": self._string_list(merged.get("principles")),
            "communicationStyle": self._string_or_default(merged.get("communicationStyle"), ""),
            "reasoningStyle": self._string_or_default(merged.get("reasoningStyle"), ""),
            "collaborationStyle": self._string_or_default(merged.get("collaborationStyle"), ""),
            "domainPreferences": self._string_list(merged.get("domainPreferences")),
            "customSystemPrompt": self._string_or_default(merged.get("customSystemPrompt"), ""),
            "enabled": bool(merged.get("enabled", True)),
            "scope": self._string_or_default(merged.get("scope"), "global"),
            "createdAt": self._bounded_int(merged.get("createdAt"), 0, 9999999999999, 0),
            "updatedAt": self._bounded_int(merged.get("updatedAt"), 0, 9999999999999, 0),
        }

    def _dedupe_profiles(self, profiles: list[dict[str, Any]], *, fallback_prefix: str) -> list[dict[str, Any]]:
        seen: set[str] = set()
        unique: list[dict[str, Any]] = []
        for index, profile in enumerate(profiles):
            profile_id = profile.get("id")
            if not isinstance(profile_id, str) or not profile_id:
                profile_id = f"{fallback_prefix}_{index + 1}"
                profile["id"] = profile_id
            if profile_id in seen:
                continue
            seen.add(profile_id)
            unique.append(profile)
        return unique

    def _valid_active_profile_id(self, value: Any, profiles: list[dict[str, Any]]) -> str:
        profile_ids = {profile["id"] for profile in profiles if isinstance(profile.get("id"), str)}
        if isinstance(value, str) and value in profile_ids:
            return value
        return profiles[0]["id"]

    def _safe_profile_id(self, value: Any, fallback_prefix: str) -> str:
        if isinstance(value, str) and value.strip():
            return value.strip()
        return f"{fallback_prefix}_{uuid.uuid4().hex[:8]}"

    def _safe_profile_name(self, value: Any, fallback: str) -> str:
        if isinstance(value, str) and value.strip():
            return value.strip()
        return fallback

    def _string_or_default(self, value: Any, default: str) -> str:
        if isinstance(value, str):
            return value.strip()
        return default

    def _string_list(self, value: Any, _key: str = "") -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item).strip() for item in value if str(item).strip()]

    def _bounded_int(self, value: Any, minimum: int, maximum: int, default: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            parsed = default
        return max(minimum, min(parsed, maximum))

    def _normalize_provider_config(self, provider: dict[str, Any]) -> dict[str, Any]:
        normalized = deepcopy(provider)
        legacy_profile = self._profile_from_provider(normalized)
        raw_profiles = normalized.get("profiles")
        profiles = [self._normalize_provider_profile(item, legacy_profile) for item in raw_profiles or [] if isinstance(item, dict)]
        if isinstance(raw_profiles, list) and not raw_profiles:
            default_profile = self._normalize_provider_profile(
                deepcopy(DEFAULT_CONFIG["provider"]["profiles"][0]),
                deepcopy(DEFAULT_CONFIG["provider"]["profiles"][0]),
            )
            profiles = [default_profile]
        if not profiles:
            profiles = [self._normalize_provider_profile(legacy_profile, legacy_profile)]

        seen: set[str] = set()
        unique_profiles: list[dict[str, Any]] = []
        for profile in profiles:
            profile_id = profile["id"]
            if profile_id in seen:
                continue
            seen.add(profile_id)
            unique_profiles.append(profile)

        active_profile_id = normalized.get("activeProfileId")
        if not isinstance(active_profile_id, str) or not active_profile_id.strip():
            active_profile_id = unique_profiles[0]["id"]
        elif active_profile_id not in {profile["id"] for profile in unique_profiles}:
            active_profile_id = unique_profiles[0]["id"]

        active_profile = next(profile for profile in unique_profiles if profile["id"] == active_profile_id)
        for key, value in active_profile.items():
            if key in {"id", "name"}:
                continue
            normalized[key] = deepcopy(value)
        normalized["activeProfileId"] = active_profile_id
        normalized["profiles"] = unique_profiles
        return normalized

    def _profile_from_provider(self, provider: dict[str, Any]) -> dict[str, Any]:
        profile: dict[str, Any] = {
            "id": provider.get("activeProfileId") if isinstance(provider.get("activeProfileId"), str) else "default",
            "name": provider.get("profileName") if isinstance(provider.get("profileName"), str) else "Default",
        }
        for key in (
            "mode",
            "baseUrl",
            "base_url",
            "model",
            "defaultModel",
            "fallbackModel",
            "apiKeyEnvVarName",
            "api_key_env_var_name",
            "envKey",
            "apiKey",
            "api_key",
            "temperature",
            "maxTokens",
            "max_tokens",
            "maxOutputTokens",
            "maxContextTokens",
            "timeout",
            "timeoutSeconds",
            "timeoutMs",
        ):
            if key in provider and provider[key] is not None:
                profile[key] = deepcopy(provider[key])
        return profile

    def _normalize_provider_profile(self, profile: dict[str, Any], defaults: dict[str, Any]) -> dict[str, Any]:
        merged = deepcopy(defaults)
        merged.update(deepcopy(profile))

        profile_id = merged.get("id")
        if not isinstance(profile_id, str) or not profile_id.strip():
            profile_id = f"profile_{uuid.uuid4().hex[:8]}"
        profile_name = merged.get("name")
        if not isinstance(profile_name, str) or not profile_name.strip():
            profile_name = profile_id

        model = merged.get("model") or merged.get("defaultModel") or DEFAULT_CONFIG["provider"]["model"]
        max_tokens = merged.get("maxTokens") or merged.get("maxOutputTokens") or DEFAULT_CONFIG["provider"]["maxTokens"]
        normalized = {
            **merged,
            "id": profile_id.strip(),
            "name": profile_name.strip(),
            "mode": merged.get("mode") or DEFAULT_CONFIG["provider"]["mode"],
            "baseUrl": merged.get("baseUrl") or merged.get("base_url") or DEFAULT_CONFIG["provider"]["baseUrl"],
            "model": model,
            "defaultModel": merged.get("defaultModel") or model,
            "apiKeyEnvVarName": merged.get("apiKeyEnvVarName")
            or merged.get("api_key_env_var_name")
            or merged.get("envKey")
            or DEFAULT_CONFIG["provider"]["apiKeyEnvVarName"],
            "temperature": merged.get("temperature", DEFAULT_CONFIG["provider"]["temperature"]),
            "maxTokens": max_tokens,
            "maxOutputTokens": merged.get("maxOutputTokens") or max_tokens,
            "maxContextTokens": merged.get("maxContextTokens", DEFAULT_CONFIG["provider"]["maxContextTokens"]),
            "timeout": merged.get("timeout", DEFAULT_CONFIG["provider"]["timeout"]),
        }
        last_checked_at = merged.get("lastCheckedAt")
        if isinstance(last_checked_at, (int, float)):
            normalized["lastCheckedAt"] = int(last_checked_at)
        last_status = merged.get("lastStatus")
        if isinstance(last_status, str) and last_status.strip():
            normalized["lastStatus"] = last_status.strip()
        last_error_summary = merged.get("lastErrorSummary")
        if isinstance(last_error_summary, str) and last_error_summary.strip():
            normalized["lastErrorSummary"] = last_error_summary.strip()
        return normalized

    def _apply_provider_patch_to_active_profile(
        self,
        config: dict[str, Any],
        provider_patch: dict[str, Any],
    ) -> None:
        provider = config.get("provider")
        if not isinstance(provider, dict):
            return
        profiles = provider.get("profiles")
        if not isinstance(profiles, list) or not profiles:
            return

        active_profile_id = provider.get("activeProfileId")
        active_profile = None
        if isinstance(active_profile_id, str):
            active_profile = next(
                (
                    profile
                    for profile in profiles
                    if isinstance(profile, dict) and profile.get("id") == active_profile_id
                ),
                None,
            )
        if active_profile is None:
            active_profile = next((profile for profile in profiles if isinstance(profile, dict)), None)
        if active_profile is None:
            return

        for key, value in provider_patch.items():
            if key not in {"profiles", "activeProfileId"} and value is not None:
                active_profile[key] = deepcopy(value)

    def _persist_config(self, config: dict[str, Any]) -> None:
        self._conn.execute(
            """
            INSERT INTO config (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = excluded.updated_at
            """,
            (CONFIG_KEY, json.dumps(config, ensure_ascii=False), self.now()),
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # Skill presets
    # ------------------------------------------------------------------

    def get_skill(self, params: dict[str, Any]) -> dict[str, Any]:
        skill_id = params.get("skillId") or params.get("skill_id")
        if not isinstance(skill_id, str) or not skill_id.strip():
            raise ValueError("skillId is required")
        row = self._conn.execute(
            "SELECT * FROM skill_presets WHERE id = ?",
            (skill_id.strip(),),
        ).fetchone()
        if row is None:
            raise ValueError(f"Skill not found: {skill_id}")
        return {"skill": self._serialize_skill(dict(row))}

    def list_skills(self, params: dict[str, Any]) -> dict[str, Any]:
        category = params.get("category")
        if isinstance(category, str) and category.strip():
            rows = self._conn.execute(
                "SELECT * FROM skill_presets WHERE category = ? ORDER BY created_at DESC",
                (category.strip(),),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM skill_presets ORDER BY created_at DESC"
            ).fetchall()
        return {"skills": [self._serialize_skill(dict(r)) for r in rows]}

    def create_skill(self, params: dict[str, Any]) -> dict[str, Any]:
        skill_id = params.get("id") or params.get("skillId") or self.new_id("skill")
        if not isinstance(skill_id, str) or not skill_id.strip():
            raise ValueError("id is required")
        skill_id = skill_id.strip()
        name = params.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("name is required")
        description = params.get("description", "")
        system_prompt = params.get("system_prompt", "")
        tool_whitelist = params.get("tool_whitelist", [])
        parameter_constraints = params.get("parameter_constraints", {})
        category = params.get("category", "custom")
        tool_policy = params.get("tool_policy", "strict_whitelist")
        now = self.now()
        self._conn.execute(
            """
            INSERT INTO skill_presets (id, name, description, system_prompt, tool_whitelist,
                                        parameter_constraints, category, tool_policy, is_builtin, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
            """,
            (
                skill_id,
                name.strip(),
                description,
                system_prompt,
                json.dumps(tool_whitelist, ensure_ascii=False),
                json.dumps(parameter_constraints, ensure_ascii=False),
                category,
                tool_policy,
                now,
                now,
            ),
        )
        self._conn.commit()
        return self.get_skill({"skillId": skill_id})

    def update_skill(self, params: dict[str, Any]) -> dict[str, Any]:
        skill_id = params.get("skillId") or params.get("skill_id")
        if not isinstance(skill_id, str) or not skill_id.strip():
            raise ValueError("skillId is required")
        skill_id = skill_id.strip()
        existing = self._conn.execute(
            "SELECT * FROM skill_presets WHERE id = ?", (skill_id,)
        ).fetchone()
        if existing is None:
            raise ValueError(f"Skill not found: {skill_id}")
        existing = dict(existing)
        updates: dict[str, Any] = {}
        for field_name in ("name", "description", "system_prompt", "category"):
            if field_name in params:
                updates[field_name] = params[field_name]
        if "tool_whitelist" in params:
            updates["tool_whitelist"] = json.dumps(params["tool_whitelist"], ensure_ascii=False)
        if "parameter_constraints" in params:
            updates["parameter_constraints"] = json.dumps(params["parameter_constraints"], ensure_ascii=False)
        if "tool_policy" in params:
            updates["tool_policy"] = params["tool_policy"]
        if not updates:
            return {"skill": self._serialize_skill(existing)}
        updates["updated_at"] = self.now()
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        self._conn.execute(
            f"UPDATE skill_presets SET {set_clause} WHERE id = ?",
            (*updates.values(), skill_id),
        )
        self._conn.commit()
        return self.get_skill({"skillId": skill_id})

    def delete_skill(self, params: dict[str, Any]) -> dict[str, Any]:
        skill_id = params.get("skillId") or params.get("skill_id")
        if not isinstance(skill_id, str) or not skill_id.strip():
            raise ValueError("skillId is required")
        skill_id = skill_id.strip()
        existing = self._conn.execute(
            "SELECT * FROM skill_presets WHERE id = ?", (skill_id,)
        ).fetchone()
        if existing is None:
            raise ValueError(f"Skill not found: {skill_id}")
        if dict(existing).get("is_builtin"):
            raise ValueError("Cannot delete built-in skills")
        self._conn.execute("DELETE FROM skill_presets WHERE id = ?", (skill_id,))
        self._conn.commit()
        return {"deleted": True, "skillId": skill_id}

    def upsert_skill(self, skill_id: str, *, name: str, description: str,
                     system_prompt: str, tool_whitelist: list[str],
                     parameter_constraints: dict[str, Any], category: str,
                     tool_policy: str = "strict_whitelist",
                     is_builtin: bool = True) -> dict[str, Any]:
        """Upsert a skill preset (used for loading built-in skills)."""
        now = self.now()
        self._conn.execute(
            """
            INSERT INTO skill_presets (id, name, description, system_prompt, tool_whitelist,
                                        parameter_constraints, category, tool_policy, is_builtin, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name,
                description = excluded.description,
                system_prompt = excluded.system_prompt,
                tool_whitelist = excluded.tool_whitelist,
                parameter_constraints = excluded.parameter_constraints,
                category = excluded.category,
                tool_policy = excluded.tool_policy,
                updated_at = excluded.updated_at
            """,
            (
                skill_id,
                name,
                description,
                system_prompt,
                json.dumps(tool_whitelist, ensure_ascii=False),
                json.dumps(parameter_constraints, ensure_ascii=False),
                category,
                tool_policy,
                1 if is_builtin else 0,
                now,
                now,
            ),
        )
        self._conn.commit()
        row = self._conn.execute(
            "SELECT * FROM skill_presets WHERE id = ?", (skill_id,)
        ).fetchone()
        return self._serialize_skill(dict(row))

    def _serialize_skill(self, row: dict[str, Any]) -> dict[str, Any]:
        result = dict(row)
        for json_field in ("tool_whitelist", "parameter_constraints"):
            raw = result.get(json_field)
            if isinstance(raw, str):
                try:
                    result[json_field] = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    result[json_field] = [] if json_field == "tool_whitelist" else {}
        return result

    # ── skill_usage audit ────────────────────────────────────────────

    def record_skill_usage(
        self, *, task_id: str, session_id: str, skill_id: str, triggered_by: str = "routing",
    ) -> dict[str, Any]:
        usage_id = str(uuid.uuid4())
        now = self.now()
        self._conn.execute(
            "INSERT INTO skill_usage (id, task_id, session_id, skill_id, triggered_at, triggered_by) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (usage_id, task_id, session_id, skill_id, now, triggered_by),
        )
        self._conn.commit()
        return {
            "id": usage_id, "taskId": task_id, "sessionId": session_id,
            "skillId": skill_id, "triggeredAt": now, "triggeredBy": triggered_by,
        }

    def list_skill_usage(self, params: dict[str, Any]) -> dict[str, Any]:
        """Query skill usage history. Supports filtering by skillId, taskId, sessionId."""
        clauses: list[str] = []
        values: list[Any] = []
        for key, col in [("skillId", "skill_id"), ("taskId", "task_id"), ("sessionId", "session_id")]:
            val = params.get(key)
            if val is not None:
                clauses.append(f"{col} = ?")
                values.append(val)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        limit = min(int(params.get("limit", 100)), 500)
        offset = int(params.get("offset", 0))
        rows = self._conn.execute(
            f"SELECT * FROM skill_usage{where} ORDER BY triggered_at DESC LIMIT ? OFFSET ?",
            (*values, limit, offset),
        ).fetchall()
        total = self._conn.execute(
            f"SELECT COUNT(*) FROM skill_usage{where}", values,
        ).fetchone()[0]
        return {
            "usage": [dict(r) for r in rows],
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    # ── mcp_servers CRUD ────────────────────────────────────────────

    def get_mcp_server(self, params: dict[str, Any]) -> dict[str, Any]:
        server_id = params.get("serverId") or params.get("server_id")
        if not isinstance(server_id, str) or not server_id.strip():
            raise ValueError("serverId is required")
        row = self._conn.execute(
            "SELECT * FROM mcp_servers WHERE id = ?", (server_id.strip(),)
        ).fetchone()
        if row is None:
            raise ValueError(f"MCP server not found: {server_id}")
        return {"server": self._serialize_mcp_server(dict(row))}

    def list_mcp_servers(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        enabled_only = (params or {}).get("enabledOnly", False)
        if enabled_only:
            rows = self._conn.execute(
                "SELECT * FROM mcp_servers WHERE enabled = 1 ORDER BY created_at DESC"
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM mcp_servers ORDER BY created_at DESC"
            ).fetchall()
        return {"servers": [self._serialize_mcp_server(dict(r)) for r in rows]}

    def create_mcp_server(self, params: dict[str, Any]) -> dict[str, Any]:
        server_id = params.get("id") or params.get("serverId") or self.new_id("mcp")
        if not isinstance(server_id, str) or not server_id.strip():
            raise ValueError("id is required")
        server_id = server_id.strip()
        name = params.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("name is required")
        transport = params.get("transport", "stdio")
        command = params.get("command")
        args = params.get("args", [])
        url = params.get("url")
        headers = params.get("headers", {})
        env = params.get("env", {})
        enabled = 1 if params.get("enabled", True) else 0
        now = self.now()
        self._conn.execute(
            """
            INSERT INTO mcp_servers (id, name, transport, command, args, url, headers, env,
                                      enabled, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                server_id,
                name.strip(),
                transport,
                command,
                json.dumps(args, ensure_ascii=False),
                url,
                json.dumps(headers, ensure_ascii=False),
                json.dumps(env, ensure_ascii=False),
                enabled,
                now,
                now,
            ),
        )
        self._conn.commit()
        return self.get_mcp_server({"serverId": server_id})

    def update_mcp_server(self, params: dict[str, Any]) -> dict[str, Any]:
        server_id = params.get("serverId") or params.get("server_id")
        if not isinstance(server_id, str) or not server_id.strip():
            raise ValueError("serverId is required")
        server_id = server_id.strip()
        existing = self._conn.execute(
            "SELECT * FROM mcp_servers WHERE id = ?", (server_id,)
        ).fetchone()
        if existing is None:
            raise ValueError(f"MCP server not found: {server_id}")
        updates: dict[str, Any] = {}
        for field_name in ("name", "transport", "command", "url"):
            if field_name in params:
                updates[field_name] = params[field_name]
        if "args" in params:
            updates["args"] = json.dumps(params["args"], ensure_ascii=False)
        if "headers" in params:
            updates["headers"] = json.dumps(params["headers"], ensure_ascii=False)
        if "env" in params:
            updates["env"] = json.dumps(params["env"], ensure_ascii=False)
        if "enabled" in params:
            updates["enabled"] = 1 if params["enabled"] else 0
        if not updates:
            return {"server": self._serialize_mcp_server(dict(existing))}
        updates["updated_at"] = self.now()
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        self._conn.execute(
            f"UPDATE mcp_servers SET {set_clause} WHERE id = ?",
            (*updates.values(), server_id),
        )
        self._conn.commit()
        return self.get_mcp_server({"serverId": server_id})

    def delete_mcp_server(self, params: dict[str, Any]) -> dict[str, Any]:
        server_id = params.get("serverId") or params.get("server_id")
        if not isinstance(server_id, str) or not server_id.strip():
            raise ValueError("serverId is required")
        server_id = server_id.strip()
        existing = self._conn.execute(
            "SELECT * FROM mcp_servers WHERE id = ?", (server_id,)
        ).fetchone()
        if existing is None:
            raise ValueError(f"MCP server not found: {server_id}")
        self._conn.execute("DELETE FROM mcp_servers WHERE id = ?", (server_id,))
        self._conn.commit()
        return {"deleted": True, "serverId": server_id}

    def _serialize_mcp_server(self, row: dict[str, Any]) -> dict[str, Any]:
        result = dict(row)
        for json_field in ("args", "headers", "env"):
            raw = result.get(json_field)
            if isinstance(raw, str):
                try:
                    result[json_field] = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    result[json_field] = [] if json_field == "args" else {}
        return result

    def _bootstrap(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS config (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS workspaces (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                root_path TEXT NOT NULL UNIQUE,
                focus TEXT,
                summary TEXT,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL,
                title TEXT,
                status TEXT NOT NULL,
                summary TEXT,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                type TEXT NOT NULL,
                status TEXT NOT NULL,
                goal TEXT NOT NULL,
                acceptance_criteria_json TEXT,
                out_of_scope_json TEXT,
                current_step TEXT,
                plan_json TEXT,
                changed_files_json TEXT,
                commands_json TEXT,
                verification_json TEXT,
                reflection_json TEXT,
                routing_json TEXT,
                summary TEXT,
                result_json TEXT,
                error_code TEXT,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                root_task_id TEXT DEFAULT NULL,
                role TEXT DEFAULT 'root',
                active_assistant_message_id TEXT DEFAULT NULL,
                created_seq INTEGER DEFAULT NULL,
                tests_run_json TEXT DEFAULT NULL,
                risks_json TEXT DEFAULT NULL,
                structured_result_json TEXT DEFAULT NULL
            );

            CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                task_id TEXT,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                client_message_id TEXT DEFAULT NULL,
                kind TEXT DEFAULT 'normal',
                status TEXT DEFAULT 'completed',
                created_seq INTEGER DEFAULT NULL,
                updated_at INTEGER
            );

            CREATE TABLE IF NOT EXISTS scheduled_tasks (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                prompt TEXT NOT NULL,
                schedule TEXT NOT NULL,
                status TEXT NOT NULL,
                enabled INTEGER NOT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                last_run_at INTEGER,
                next_run_at INTEGER
            );

            CREATE TABLE IF NOT EXISTS scheduled_task_runs (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                status TEXT NOT NULL,
                started_at INTEGER NOT NULL,
                finished_at INTEGER,
                summary TEXT,
                error TEXT
            );

            CREATE TABLE IF NOT EXISTS patches (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                workspace_id TEXT NOT NULL,
                summary TEXT,
                diff_text TEXT NOT NULL,
                status TEXT NOT NULL,
                files_changed INTEGER NOT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS command_logs (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                command TEXT NOT NULL,
                cwd TEXT NOT NULL,
                exit_code INTEGER,
                status TEXT NOT NULL,
                stdout_path TEXT,
                stderr_path TEXT,
                started_at INTEGER NOT NULL,
                finished_at INTEGER
            );

            CREATE TABLE IF NOT EXISTS approvals (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                request_json TEXT NOT NULL,
                decision TEXT,
                decided_by TEXT,
                created_at INTEGER NOT NULL,
                decided_at INTEGER
            );

            CREATE TABLE IF NOT EXISTS pending_react_tasks (
                task_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                goal TEXT NOT NULL,
                context_json TEXT NOT NULL,
                messages_json TEXT NOT NULL,
                tool_results_json TEXT NOT NULL,
                pending_tool_call_json TEXT NOT NULL,
                pending_tool_spec_json TEXT NOT NULL,
                remaining_tool_calls_json TEXT NOT NULL,
                steps INTEGER NOT NULL,
                react_started INTEGER NOT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS pending_dag_tasks (
                task_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                goal TEXT NOT NULL,
                context_json TEXT NOT NULL,
                plan_json TEXT NOT NULL,
                completed_ids_json TEXT NOT NULL,
                failed_ids_json TEXT NOT NULL,
                results_json TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS trace_events (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                type TEXT NOT NULL,
                source TEXT NOT NULL,
                related_id TEXT,
                payload_json TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                sequence INTEGER NOT NULL,
                visibility TEXT NOT NULL DEFAULT 'chat'
            );

            CREATE TABLE IF NOT EXISTS collaboration_tasks (
                id TEXT PRIMARY KEY,
                session_id TEXT,
                parent_task_id TEXT,
                title TEXT NOT NULL,
                description TEXT,
                status TEXT NOT NULL,
                priority INTEGER NOT NULL,
                assigned_worker_id TEXT,
                dependencies_json TEXT NOT NULL,
                result_json TEXT,
                error_json TEXT,
                metadata_json TEXT NOT NULL,
                claimed_at INTEGER,
                completed_at INTEGER,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS agent_workers (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                role TEXT NOT NULL,
                status TEXT NOT NULL,
                current_task_id TEXT,
                capabilities_json TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                last_heartbeat_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS agent_messages (
                id TEXT PRIMARY KEY,
                sender_worker_id TEXT NOT NULL,
                recipient_worker_id TEXT,
                task_id TEXT,
                kind TEXT NOT NULL,
                body TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                read_at INTEGER
            );

            CREATE INDEX IF NOT EXISTS idx_trace_events_task_order
                ON trace_events (task_id, created_at, sequence);

            CREATE INDEX IF NOT EXISTS idx_messages_session_created
                ON messages (session_id, created_at);

            CREATE INDEX IF NOT EXISTS idx_scheduled_task_runs_task_started
                ON scheduled_task_runs (task_id, started_at DESC);

            CREATE INDEX IF NOT EXISTS idx_collaboration_tasks_queue
                ON collaboration_tasks (status, priority, updated_at DESC);

            CREATE INDEX IF NOT EXISTS idx_collaboration_tasks_parent
                ON collaboration_tasks (parent_task_id, created_at DESC);

            CREATE INDEX IF NOT EXISTS idx_agent_workers_status
                ON agent_workers (status, updated_at DESC);

            CREATE INDEX IF NOT EXISTS idx_agent_messages_task_created
                ON agent_messages (task_id, created_at DESC);

            CREATE INDEX IF NOT EXISTS idx_agent_messages_recipient_created
                ON agent_messages (recipient_worker_id, created_at DESC);

            CREATE TABLE IF NOT EXISTS task_metrics (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                duration_ms INTEGER,
                tool_call_count INTEGER DEFAULT 0,
                command_count INTEGER DEFAULT 0,
                patch_count INTEGER DEFAULT 0,
                provider_call_count INTEGER DEFAULT 0,
                patch_success_count INTEGER DEFAULT 0,
                patch_failure_count INTEGER DEFAULT 0,
                command_success_count INTEGER DEFAULT 0,
                command_failure_count INTEGER DEFAULT 0,
                task_status TEXT,
                patch_repair_attempts INTEGER DEFAULT 0,
                approval_approved_count INTEGER DEFAULT 0,
                approval_rejected_count INTEGER DEFAULT 0,
                was_cancelled INTEGER DEFAULT 0
            );

            CREATE INDEX IF NOT EXISTS idx_task_metrics_session
                ON task_metrics (session_id, created_at DESC);

            CREATE TABLE IF NOT EXISTS scratchpad_entries (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                UNIQUE(session_id, key)
            );

            CREATE INDEX IF NOT EXISTS idx_scratchpad_session
                ON scratchpad_entries (session_id, updated_at DESC);

            CREATE TABLE IF NOT EXISTS compaction_records (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                strategy TEXT NOT NULL,
                tokens_before INTEGER NOT NULL,
                tokens_after INTEGER NOT NULL,
                summary TEXT,
                primer_hash TEXT,
                created_at INTEGER NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_compaction_session
                ON compaction_records (session_id, created_at DESC);

            CREATE TABLE IF NOT EXISTS skill_presets (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT NOT NULL,
                system_prompt TEXT NOT NULL,
                tool_whitelist TEXT NOT NULL,
                parameter_constraints TEXT NOT NULL DEFAULT '{}',
                category TEXT NOT NULL DEFAULT 'custom',
                tool_policy TEXT NOT NULL DEFAULT 'strict_whitelist',
                is_builtin INTEGER NOT NULL DEFAULT 0,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_skill_presets_category
                ON skill_presets (category);

            CREATE TABLE IF NOT EXISTS mcp_servers (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                transport TEXT NOT NULL DEFAULT 'stdio',
                command TEXT,
                args TEXT DEFAULT '[]',
                url TEXT,
                headers TEXT DEFAULT '{}',
                env TEXT DEFAULT '{}',
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS memory_entries (
                id TEXT PRIMARY KEY,
                session_id TEXT,
                workspace_id TEXT,
                kind TEXT NOT NULL,
                content TEXT NOT NULL,
                keywords TEXT,
                metadata TEXT DEFAULT '{}',
                created_at INTEGER NOT NULL,
                accessed_at INTEGER NOT NULL,
                access_count INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS task_inbox (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                message_id TEXT,
                content TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                consumed_by_turn_id TEXT,
                created_seq INTEGER DEFAULT NULL,
                created_at INTEGER NOT NULL,
                consumed_at INTEGER
            );

            CREATE INDEX IF NOT EXISTS idx_task_inbox_task_status
                ON task_inbox (task_id, status);

            CREATE INDEX IF NOT EXISTS idx_memory_kind
                ON memory_entries (kind);
            CREATE INDEX IF NOT EXISTS idx_memory_workspace
                ON memory_entries (workspace_id);
            CREATE INDEX IF NOT EXISTS idx_memory_session
                ON memory_entries (session_id);
            CREATE INDEX IF NOT EXISTS idx_memory_accessed
                ON memory_entries (accessed_at);

            CREATE INDEX IF NOT EXISTS idx_memory_kind_workspace_accessed
                ON memory_entries (kind, workspace_id, accessed_at);
            CREATE INDEX IF NOT EXISTS idx_memory_kind_session_accessed
                ON memory_entries (kind, session_id, accessed_at);

            CREATE TABLE IF NOT EXISTS trace_spans (
                trace_id TEXT NOT NULL,
                span_id TEXT PRIMARY KEY,
                parent_span_id TEXT,
                operation TEXT NOT NULL,
                started_at INTEGER NOT NULL,
                finished_at INTEGER,
                status TEXT NOT NULL DEFAULT 'in_progress',
                attributes TEXT NOT NULL DEFAULT '{}'
            );
            CREATE INDEX IF NOT EXISTS idx_spans_trace
                ON trace_spans (trace_id);
            CREATE INDEX IF NOT EXISTS idx_spans_parent
                ON trace_spans (parent_span_id);

            CREATE TABLE IF NOT EXISTS llm_cache (
                prompt_hash TEXT PRIMARY KEY,
                response TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                expires_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS skill_usage (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                skill_id TEXT NOT NULL,
                triggered_at INTEGER NOT NULL,
                triggered_by TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_skill_usage_skill_id
                ON skill_usage (skill_id);
            CREATE INDEX IF NOT EXISTS idx_skill_usage_task_id
                ON skill_usage (task_id);

            CREATE TABLE IF NOT EXISTS memory_recall_records (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                task_id TEXT,
                query TEXT NOT NULL,
                memory_ids TEXT NOT NULL DEFAULT '[]',
                scores TEXT NOT NULL DEFAULT '{}',
                injected INTEGER NOT NULL DEFAULT 0,
                created_at INTEGER NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_recall_session
                ON memory_recall_records (session_id, created_at DESC);

            CREATE TABLE IF NOT EXISTS session_rolling_summaries (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                summary TEXT NOT NULL,
                covered_message_ids TEXT NOT NULL DEFAULT '[]',
                token_estimate INTEGER NOT NULL DEFAULT 0,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            );

            CREATE UNIQUE INDEX IF NOT EXISTS idx_rolling_summary_session
                ON session_rolling_summaries (session_id);
            """
        )
        self._conn.commit()

        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS provider_turns (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                turn_index INTEGER NOT NULL,
                model TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                error_summary TEXT,
                request_message_count INTEGER,
                request_tool_count INTEGER,
                request_token_estimate INTEGER,
                response_finish_reason TEXT,
                response_usage_json TEXT,
                response_tool_call_count INTEGER,
                context_snapshot_id TEXT,
                turn_decision TEXT,
                thought_summary TEXT,
                created_at INTEGER NOT NULL,
                completed_at INTEGER
            )
        """)
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_provider_turns_task ON provider_turns(task_id)"
        )
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS context_snapshots (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                provider_turn_id TEXT,
                included_sections_json TEXT,
                trimmed_sections_json TEXT,
                dropped_sections_json TEXT,
                recent_message_ids_json TEXT,
                summarized_message_ids_json TEXT,
                memory_ids_json TEXT,
                supplement_inbox_ids_json TEXT,
                tool_count INTEGER,
                skill_id TEXT,
                token_estimate INTEGER,
                created_at INTEGER NOT NULL
            )
        """)
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_context_snapshots_task ON context_snapshots(task_id)"
        )
        self._conn.commit()

        # proposal_records table — P1 of llm-assisted-runtime-decision-todolist
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS proposal_records (
                id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                session_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                source_json TEXT NOT NULL DEFAULT '{}',
                input_summary TEXT,
                proposal_json TEXT NOT NULL DEFAULT '{}',
                status TEXT NOT NULL DEFAULT 'pending',
                validation_reasons_json TEXT NOT NULL DEFAULT '[]',
                applied_to_json TEXT NOT NULL DEFAULT '{}',
                model_id TEXT,
                turn_id TEXT,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            )
        """)
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_proposal_records_session ON proposal_records(session_id)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_proposal_records_task ON proposal_records(task_id)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_proposal_records_status ON proposal_records(status)"
        )
        self._conn.commit()

        # artifacts table — P3 of subagent-generation-todolist
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS artifacts (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                parent_task_id TEXT NOT NULL,
                producer_task_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'proposed',
                title TEXT,
                description TEXT,
                content_json TEXT NOT NULL DEFAULT '{}',
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            )
        """)
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_artifacts_session ON artifacts(session_id)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_artifacts_parent_task ON artifacts(parent_task_id)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_artifacts_producer_task ON artifacts(producer_task_id)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_artifacts_kind ON artifacts(kind)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_artifacts_status ON artifacts(status)"
        )
        self._conn.commit()

        # Ensure seq_counter table exists
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS seq_counter (
                id INTEGER PRIMARY KEY,
                val INTEGER NOT NULL DEFAULT 0
            )
        """)
        self._conn.execute(
            "INSERT OR IGNORE INTO seq_counter (id, val) VALUES (1, 0)"
        )
        self._conn.commit()

        self._ensure_workspace_columns()
        self._ensure_task_columns()
        self._ensure_message_columns()
        self._ensure_patch_columns()
        self._ensure_collaboration_task_columns()
        self._ensure_schedule_columns()
        self._ensure_compaction_columns()
        self._ensure_context_snapshot_columns()
        self._ensure_inbox_columns()
        self._ensure_mcp_server_columns()

    def _ensure_workspace_columns(self) -> None:
        columns = {
            row["name"]
            for row in self._conn.execute("PRAGMA table_info(workspaces)").fetchall()
        }
        if "focus" not in columns:
            self._conn.execute("ALTER TABLE workspaces ADD COLUMN focus TEXT")
        if "summary" not in columns:
            self._conn.execute("ALTER TABLE workspaces ADD COLUMN summary TEXT")
        self._conn.commit()

    def _ensure_task_columns(self) -> None:
        columns = {
            row["name"]
            for row in self._conn.execute("PRAGMA table_info(tasks)").fetchall()
        }
        expected = {
            "acceptance_criteria_json": "TEXT DEFAULT '[]'",
            "out_of_scope_json": "TEXT DEFAULT '[]'",
            "current_step": "TEXT",
            "changed_files_json": "TEXT DEFAULT '[]'",
            "commands_json": "TEXT DEFAULT '[]'",
            "verification_json": "TEXT DEFAULT '[]'",
            "reflection_json": "TEXT",
            "routing_json": "TEXT",
            "summary": "TEXT",
            "root_task_id": "TEXT DEFAULT NULL",
            "role": "TEXT DEFAULT 'root'",
            "active_assistant_message_id": "TEXT DEFAULT NULL",
            "created_seq": "INTEGER DEFAULT NULL",
            "tests_run_json": "TEXT DEFAULT NULL",
            "risks_json": "TEXT DEFAULT NULL",
            "structured_result_json": "TEXT DEFAULT NULL",
        }
        for column, definition in expected.items():
            if column not in columns:
                self._conn.execute(f"ALTER TABLE tasks ADD COLUMN {column} {definition}")
        self._conn.execute("UPDATE tasks SET root_task_id = id WHERE root_task_id IS NULL OR root_task_id = ''")
        self._conn.commit()

    def _ensure_message_columns(self) -> None:
        columns = {
            row["name"]
            for row in self._conn.execute("PRAGMA table_info(messages)").fetchall()
        }
        expected = {
            "client_message_id": "TEXT DEFAULT NULL",
            "kind": "TEXT DEFAULT 'normal'",
            "status": "TEXT DEFAULT 'completed'",
            "created_seq": "INTEGER DEFAULT NULL",
            "updated_at": "INTEGER",
        }
        for column, definition in expected.items():
            if column not in columns:
                self._conn.execute(f"ALTER TABLE messages ADD COLUMN {column} {definition}")
        self._conn.commit()

    def _ensure_patch_columns(self) -> None:
        columns = {
            row["name"]
            for row in self._conn.execute("PRAGMA table_info(patches)").fetchall()
        }
        if "summary" not in columns:
            self._conn.execute("ALTER TABLE patches ADD COLUMN summary TEXT")
        if "diff_text" not in columns:
            self._conn.execute("ALTER TABLE patches ADD COLUMN diff_text TEXT DEFAULT ''")
        if "status" not in columns:
            self._conn.execute("ALTER TABLE patches ADD COLUMN status TEXT DEFAULT 'proposed'")
        if "files_changed" not in columns:
            self._conn.execute("ALTER TABLE patches ADD COLUMN files_changed INTEGER DEFAULT 0")
        if "created_at" not in columns:
            self._conn.execute("ALTER TABLE patches ADD COLUMN created_at INTEGER DEFAULT 0")
        if "updated_at" not in columns:
            self._conn.execute("ALTER TABLE patches ADD COLUMN updated_at INTEGER DEFAULT 0")
        self._conn.commit()

    def _ensure_schedule_columns(self) -> None:
        columns = {
            row["name"]
            for row in self._conn.execute("PRAGMA table_info(scheduled_tasks)").fetchall()
        }
        expected = {
            "prompt": "TEXT DEFAULT ''",
            "schedule": "TEXT DEFAULT 'every 30 minutes'",
            "status": "TEXT DEFAULT 'active'",
            "enabled": "INTEGER DEFAULT 1",
            "created_at": "INTEGER DEFAULT 0",
            "updated_at": "INTEGER DEFAULT 0",
            "last_run_at": "INTEGER",
            "next_run_at": "INTEGER",
        }
        for column, definition in expected.items():
            if column not in columns:
                self._conn.execute(f"ALTER TABLE scheduled_tasks ADD COLUMN {column} {definition}")

        run_columns = {
            row["name"]
            for row in self._conn.execute("PRAGMA table_info(scheduled_task_runs)").fetchall()
        }
        run_expected = {
            "status": "TEXT DEFAULT 'completed'",
            "started_at": "INTEGER DEFAULT 0",
            "finished_at": "INTEGER",
            "summary": "TEXT",
            "error": "TEXT",
        }
        for column, definition in run_expected.items():
            if column not in run_columns:
                self._conn.execute(f"ALTER TABLE scheduled_task_runs ADD COLUMN {column} {definition}")
        self._conn.commit()

    def _ensure_compaction_columns(self) -> None:
        """Add traceability columns to compaction_records if missing."""
        columns = {
            row["name"]
            for row in self._conn.execute("PRAGMA table_info(compaction_records)").fetchall()
        }
        expected = {
            "covered_message_ids": "TEXT DEFAULT '[]'",
            "trimmed_sections": "TEXT DEFAULT '[]'",
            "task_id": "TEXT",
        }
        for column, definition in expected.items():
            if column not in columns:
                self._conn.execute(f"ALTER TABLE compaction_records ADD COLUMN {column} {definition}")
        self._conn.commit()

    def _ensure_context_snapshot_columns(self) -> None:
        """Add budget columns to context_snapshots if missing."""
        columns = {
            row["name"]
            for row in self._conn.execute("PRAGMA table_info(context_snapshots)").fetchall()
        }
        expected = {
            "max_context_tokens": "INTEGER",
            "prompt_layers_json": "TEXT",
        }
        for column, definition in expected.items():
            if column not in columns:
                self._conn.execute(f"ALTER TABLE context_snapshots ADD COLUMN {column} {definition}")
        self._conn.commit()

    def _ensure_inbox_columns(self) -> None:
        """Add created_seq column to task_inbox if missing."""
        columns = {
            row["name"]
            for row in self._conn.execute("PRAGMA table_info(task_inbox)").fetchall()
        }
        if "created_seq" not in columns:
            self._conn.execute("ALTER TABLE task_inbox ADD COLUMN created_seq INTEGER DEFAULT NULL")
        self._conn.commit()

    def _ensure_mcp_server_columns(self) -> None:
        """Ensure mcp_servers table has all required columns for migration."""
        columns = {
            row["name"]
            for row in self._conn.execute("PRAGMA table_info(mcp_servers)").fetchall()
        }
        expected = {
            "transport": "TEXT NOT NULL DEFAULT 'stdio'",
            "command": "TEXT",
            "args": "TEXT DEFAULT '[]'",
            "url": "TEXT",
            "headers": "TEXT DEFAULT '{}'",
            "env": "TEXT DEFAULT '{}'",
            "enabled": "INTEGER NOT NULL DEFAULT 1",
        }
        for column, definition in expected.items():
            if column not in columns:
                self._conn.execute(f"ALTER TABLE mcp_servers ADD COLUMN {column} {definition}")
        self._conn.commit()

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

    def _serialize_proposal(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": row["id"],
            "kind": row["kind"],
            "sessionId": row["session_id"],
            "taskId": row["task_id"],
            "source": json.loads(row.get("source_json") or "{}"),
            "inputSummary": row.get("input_summary"),
            "proposal": json.loads(row.get("proposal_json") or "{}"),
            "status": row["status"],
            "validationReasons": json.loads(row.get("validation_reasons_json") or "[]"),
            "appliedTo": json.loads(row.get("applied_to_json") or "{}"),
            "modelId": row.get("model_id"),
            "turnId": row.get("turn_id"),
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        }

    VALID_PROPOSAL_KINDS = frozenset({
        "intent_mode", "routing_strategy", "decomposition", "agent_profile", "model_policy",
        "skill_policy", "tool_policy", "mcp_policy", "context_policy",
        "memory_policy", "artifact_contract", "risk_policy", "approval_policy",
        "test_strategy", "failure_recovery", "event_presentation",
        "synthesis_strategy", "todo_maintenance",
        "react_turn_decision", "completion_decision",
    })

    VALID_PROPOSAL_STATUSES = frozenset({"pending", "accepted", "rejected", "applied"})

    def create_proposal(self, params: dict[str, Any]) -> dict[str, Any]:
        kind = self._require_non_empty(params, "kind")
        if kind not in self.VALID_PROPOSAL_KINDS:
            raise ValueError(f"Invalid proposal kind: {kind!r}")
        session_id = self._require_non_empty(params, "sessionId")
        task_id = self._require_non_empty(params, "taskId")
        proposal = self._dict_value(params.get("proposal", {}), "proposal")
        source = self._dict_value(params.get("source", {}), "source")
        input_summary = params.get("inputSummary")
        model_id = params.get("modelId")
        turn_id = params.get("turnId")
        proposal_id = self.new_id("prop")
        now = self.now()
        self._conn.execute(
            """
            INSERT INTO proposal_records (
                id, kind, session_id, task_id, source_json, input_summary,
                proposal_json, status, validation_reasons_json, applied_to_json,
                model_id, turn_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', '[]', '{}', ?, ?, ?, ?)
            """,
            (
                proposal_id, kind, session_id, task_id,
                json.dumps(source, ensure_ascii=False),
                input_summary,
                json.dumps(proposal, ensure_ascii=False),
                model_id, turn_id, now, now,
            ),
        )
        self._conn.commit()
        row = self._conn.execute(
            "SELECT * FROM proposal_records WHERE id = ?", (proposal_id,)
        ).fetchone()
        return {"proposal": self._serialize_proposal(dict(row))}

    def validate_proposal(self, params: dict[str, Any]) -> dict[str, Any]:
        proposal_id = self._require_non_empty(params, "proposalId")
        status = self._require_non_empty(params, "status")
        if status not in ("accepted", "rejected"):
            raise ValueError(f"Validation status must be 'accepted' or 'rejected', got {status!r}")
        reasons = self._string_list(params.get("reasons", []), "reasons")
        now = self.now()
        row = self._conn.execute(
            "SELECT * FROM proposal_records WHERE id = ?", (proposal_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"Proposal not found: {proposal_id}")
        current = dict(row)
        if current["status"] != "pending":
            raise ValueError(f"Proposal {proposal_id} has status {current['status']!r}, expected 'pending'")
        self._conn.execute(
            "UPDATE proposal_records SET status = ?, validation_reasons_json = ?, updated_at = ? WHERE id = ?",
            (status, json.dumps(reasons, ensure_ascii=False), now, proposal_id),
        )
        self._conn.commit()
        row = self._conn.execute(
            "SELECT * FROM proposal_records WHERE id = ?", (proposal_id,)
        ).fetchone()
        return {"proposal": self._serialize_proposal(dict(row))}

    def apply_proposal(self, params: dict[str, Any]) -> dict[str, Any]:
        proposal_id = self._require_non_empty(params, "proposalId")
        applied_to = self._dict_value(params.get("appliedTo", {}), "appliedTo")
        now = self.now()
        row = self._conn.execute(
            "SELECT * FROM proposal_records WHERE id = ?", (proposal_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"Proposal not found: {proposal_id}")
        current = dict(row)
        if current["status"] != "accepted":
            raise ValueError(f"Proposal {proposal_id} has status {current['status']!r}, expected 'accepted'")
        self._conn.execute(
            "UPDATE proposal_records SET status = 'applied', applied_to_json = ?, updated_at = ? WHERE id = ?",
            (json.dumps(applied_to, ensure_ascii=False), now, proposal_id),
        )
        self._conn.commit()
        row = self._conn.execute(
            "SELECT * FROM proposal_records WHERE id = ?", (proposal_id,)
        ).fetchone()
        return {"proposal": self._serialize_proposal(dict(row))}

    def list_proposals(self, params: dict[str, Any]) -> dict[str, Any]:
        filters = []
        args: list[Any] = []
        session_id = params.get("sessionId")
        if session_id:
            filters.append("session_id = ?")
            args.append(session_id)
        task_id = params.get("taskId")
        if task_id:
            filters.append("task_id = ?")
            args.append(task_id)
        kind = params.get("kind")
        if kind:
            filters.append("kind = ?")
            args.append(kind)
        status = params.get("status")
        if status:
            filters.append("status = ?")
            args.append(status)
        limit = min(int(params.get("limit") or 100), 500)
        where = f" WHERE {' AND '.join(filters)}" if filters else ""
        query = f"SELECT * FROM proposal_records{where} ORDER BY created_at DESC LIMIT ?"
        args.append(limit)
        rows = [dict(r) for r in self._conn.execute(query, args).fetchall()]
        return {"proposals": [self._serialize_proposal(r) for r in rows]}

    # -----------------------------------------------------------------------
    # Artifact Registry CRUD — P3 of subagent-generation-todolist
    # -----------------------------------------------------------------------

    VALID_ARTIFACT_KINDS = frozenset({"plan", "file", "patch", "review", "test_report", "asset"})
    VALID_ARTIFACT_STATUSES = frozenset({"proposed", "applied", "verified", "rejected"})

    def _serialize_artifact(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": row["id"],
            "sessionId": row["session_id"],
            "parentTaskId": row["parent_task_id"],
            "producerTaskId": row["producer_task_id"],
            "kind": row["kind"],
            "status": row["status"],
            "title": row.get("title"),
            "description": row.get("description"),
            "content": json.loads(row.get("content_json") or "{}"),
            "metadata": json.loads(row.get("metadata_json") or "{}"),
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        }

    def create_artifact(self, params: dict[str, Any]) -> dict[str, Any]:
        session_id = self._require_non_empty(params, "sessionId")
        parent_task_id = self._require_non_empty(params, "parentTaskId")
        producer_task_id = self._require_non_empty(params, "producerTaskId")
        kind = self._require_non_empty(params, "kind")
        if kind not in self.VALID_ARTIFACT_KINDS:
            raise ValueError(f"Invalid artifact kind: {kind!r}")
        title = params.get("title")
        description = params.get("description")
        content = self._dict_value(params.get("content", {}), "content")
        metadata = self._dict_value(params.get("metadata", {}), "metadata")
        artifact_id = self.new_id("art")
        now = self.now()
        self._conn.execute(
            """
            INSERT INTO artifacts (
                id, session_id, parent_task_id, producer_task_id,
                kind, status, title, description, content_json,
                metadata_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, 'proposed', ?, ?, ?, ?, ?, ?)
            """,
            (
                artifact_id, session_id, parent_task_id, producer_task_id,
                kind, title, description,
                json.dumps(content, ensure_ascii=False),
                json.dumps(metadata, ensure_ascii=False),
                now, now,
            ),
        )
        self._conn.commit()
        row = self._conn.execute(
            "SELECT * FROM artifacts WHERE id = ?", (artifact_id,)
        ).fetchone()
        return {"artifact": self._serialize_artifact(dict(row))}

    def update_artifact(self, params: dict[str, Any]) -> dict[str, Any]:
        artifact_id = self._require_non_empty(params, "artifactId")
        now = self.now()
        row = self._conn.execute(
            "SELECT * FROM artifacts WHERE id = ?", (artifact_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"Artifact not found: {artifact_id}")
        updates: list[str] = []
        args: list[Any] = []
        for field, col in [("status", "status"), ("title", "title"), ("description", "description")]:
            if field in params:
                val = params[field]
                if field == "status" and val not in self.VALID_ARTIFACT_STATUSES:
                    raise ValueError(f"Invalid artifact status: {val!r}")
                if field == "status" and row["status"] == "rejected" and val != "rejected":
                    raise ValueError("Rejected artifacts cannot transition to another status")
                updates.append(f"{col} = ?")
                args.append(val)
        if "content" in params:
            updates.append("content_json = ?")
            args.append(json.dumps(self._dict_value(params["content"], "content"), ensure_ascii=False))
        if "metadata" in params:
            updates.append("metadata_json = ?")
            args.append(json.dumps(self._dict_value(params["metadata"], "metadata"), ensure_ascii=False))
        if not updates:
            return {"artifact": self._serialize_artifact(dict(row))}
        updates.append("updated_at = ?")
        args.append(now)
        args.append(artifact_id)
        self._conn.execute(
            f"UPDATE artifacts SET {', '.join(updates)} WHERE id = ?", args
        )
        self._conn.commit()
        row = self._conn.execute(
            "SELECT * FROM artifacts WHERE id = ?", (artifact_id,)
        ).fetchone()
        return {"artifact": self._serialize_artifact(dict(row))}

    def list_artifacts(self, params: dict[str, Any]) -> dict[str, Any]:
        filters: list[str] = []
        args: list[Any] = []
        if params.get("sessionId"):
            filters.append("session_id = ?")
            args.append(params["sessionId"])
        if params.get("parentTaskId"):
            filters.append("parent_task_id = ?")
            args.append(params["parentTaskId"])
        if params.get("producerTaskId"):
            filters.append("producer_task_id = ?")
            args.append(params["producerTaskId"])
        if params.get("kind"):
            filters.append("kind = ?")
            args.append(params["kind"])
        if params.get("status"):
            filters.append("status = ?")
            args.append(params["status"])
        limit = min(int(params.get("limit") or 100), 500)
        where = f" WHERE {' AND '.join(filters)}" if filters else ""
        query = f"SELECT * FROM artifacts{where} ORDER BY created_at ASC LIMIT ?"
        args.append(limit)
        rows = [dict(r) for r in self._conn.execute(query, args).fetchall()]
        return {"artifacts": [self._serialize_artifact(r) for r in rows]}

    def _ensure_collaboration_task_columns_original(self) -> None:
        pass

    def _ensure_collaboration_task_columns(self) -> None:
        columns = {
            row["name"]
            for row in self._conn.execute("PRAGMA table_info(collaboration_tasks)").fetchall()
        }
        if "error_json" not in columns:
            self._conn.execute("ALTER TABLE collaboration_tasks ADD COLUMN error_json TEXT")

        # --- trace_events visibility column ---
        trace_columns = {
            row["name"]
            for row in self._conn.execute("PRAGMA table_info(trace_events)").fetchall()
        }
        if "visibility" not in trace_columns:
            self._conn.execute("ALTER TABLE trace_events ADD COLUMN visibility TEXT NOT NULL DEFAULT 'chat'")

        # --- proposal_records model_id/turn_id columns ---
        proposal_columns = {
            row["name"]
            for row in self._conn.execute("PRAGMA table_info(proposal_records)").fetchall()
        }
        if proposal_columns and "model_id" not in proposal_columns:
            self._conn.execute("ALTER TABLE proposal_records ADD COLUMN model_id TEXT")
            self._conn.execute("ALTER TABLE proposal_records ADD COLUMN turn_id TEXT")

        # provider_turns: add turn_decision and thought_summary columns
        pt_columns = {
            row["name"]
            for row in self._conn.execute("PRAGMA table_info(provider_turns)").fetchall()
        }
        if pt_columns and "turn_decision" not in pt_columns:
            self._conn.execute("ALTER TABLE provider_turns ADD COLUMN turn_decision TEXT")
            self._conn.execute("ALTER TABLE provider_turns ADD COLUMN thought_summary TEXT")

        self._conn.commit()
