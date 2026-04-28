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
        "maxContextTokens": 120000,
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
                "maxContextTokens": 120000,
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
    "ui": {
        "language": "zh-CN",
        "showRawEvents": False,
        "theme": "light",
        "reasoningEffort": "max",
        "webFetchPreflight": True,
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

    def close(self) -> None:
        self._conn.close()

    @property
    def database_path(self) -> str:
        return self._database_path

    def now(self) -> int:
        return int(time.time() * 1000)

    def new_id(self, prefix: str) -> str:
        return f"{prefix}_{uuid.uuid4().hex[:12]}"

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
            "SELECT * FROM sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"Session not found: {session_id}")
        return self._serialize_session(dict(row))

    def get_session(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"session": self.require_session(params["sessionId"])}

    def list_sessions(self, _params: dict[str, Any]) -> dict[str, Any]:
        rows = self._conn.execute("SELECT * FROM sessions ORDER BY updated_at DESC").fetchall()
        return {"sessions": [self._serialize_session(dict(row)) for row in rows]}

    def create_message(
        self,
        *,
        session_id: str,
        role: str,
        content: str,
        task_id: str | None = None,
    ) -> dict[str, Any]:
        if role not in {"user", "assistant", "system", "tool"}:
            raise ValueError(f"Unsupported message role: {role}")
        message_id = self.new_id("msg")
        now = self.now()
        self._conn.execute(
            """
            INSERT INTO messages (id, session_id, task_id, role, content, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (message_id, session_id, task_id, role, content, now),
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
    ) -> dict[str, Any]:
        task_id = self.new_id("task")
        now = self.now()
        current_step = current_step or self._current_step_from_plan(plan)
        self._conn.execute(
            """
            INSERT INTO tasks (
                id, session_id, type, status, goal, acceptance_criteria_json, out_of_scope_json,
                current_step, plan_json, changed_files_json, commands_json, verification_json,
                summary, result_json, error_code, created_at, updated_at
            )
            VALUES (?, ?, ?, 'running', ?, ?, ?, ?, ?, '[]', '[]', '[]', NULL, NULL, NULL, ?, ?)
            """,
            (
                task_id,
                session_id,
                task_type,
                goal,
                json.dumps(acceptance_criteria or [], ensure_ascii=False),
                json.dumps(out_of_scope or [], ensure_ascii=False),
                current_step,
                json.dumps(plan, ensure_ascii=False),
                now,
                now,
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
        summary: str | None = None,
        result_summary: str | None = None,
        error_code: str | None = None,
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
        if summary is not None:
            assignments.append("summary = ?")
            values.append(summary)
        if result_summary is not None:
            assignments.append("result_json = ?")
            values.append(result_summary)
        if error_code is not None:
            assignments.append("error_code = ?")
            values.append(error_code)

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
    ) -> dict[str, Any]:
        trace_id = self.new_id("trace")
        timestamp = self.now() if created_at is None else int(created_at)
        sequence_row = self._conn.execute("SELECT COALESCE(MAX(sequence), 0) + 1 FROM trace_events").fetchone()
        sequence = int(sequence_row[0])
        payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        self._conn.execute(
            """
            INSERT INTO trace_events (
                id, task_id, session_id, type, source, related_id, payload_json, created_at, sequence
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            )
        return self.append_trace_event(
            task_id=task_id,
            session_id=getattr(event, "session_id", None),
            event_type=normalized_type,
            source=self._trace_source(normalized_type),
            related_id=self._trace_related_id(payload),
            payload=payload,
            created_at=getattr(event, "ts", None),
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
        return {"config": deepcopy(self._config)}

    def update_config(self, params: dict[str, Any]) -> dict[str, Any]:
        patch = params.get("config", params)
        if not isinstance(patch, dict):
            raise ValueError("config.update expects an object payload")

        merged = self._merge_config(self._config, patch)
        provider_patch = patch.get("provider") if isinstance(patch.get("provider"), dict) else None
        if isinstance(provider_patch, dict) and "profiles" not in provider_patch:
            self._apply_provider_patch_to_active_profile(merged, provider_patch)
        self._config = self._normalize_config(merged)
        self._persist_config(self._config)
        return {"config": deepcopy(self._config)}

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
        return {
            "id": row["id"],
            "workspaceId": row["workspace_id"],
            "title": row["title"],
            "status": row["status"],
            "summary": row["summary"],
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        }

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
        return {
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
            "summary": row.get("summary"),
            "resultSummary": row["result_json"],
            "errorCode": row["error_code"],
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        }

    def _json_list(self, raw: Any) -> list[Any]:
        if not raw:
            return []
        try:
            parsed = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            return []
        return parsed if isinstance(parsed, list) else []

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

    def _string_list(self, value: Any, key: str) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError(f"{key} must be a list")
        result: list[str] = []
        for item in value:
            if not isinstance(item, str) or not item.strip():
                raise ValueError(f"{key} must contain non-empty strings")
            result.append(item.strip())
        return result

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
            return normalized

        normalized["provider"] = self._normalize_provider_config(provider)
        return normalized

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
                summary TEXT,
                result_json TEXT,
                error_code TEXT,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                task_id TEXT,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at INTEGER NOT NULL
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

            CREATE TABLE IF NOT EXISTS trace_events (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                type TEXT NOT NULL,
                source TEXT NOT NULL,
                related_id TEXT,
                payload_json TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                sequence INTEGER NOT NULL
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
            """
        )
        self._conn.commit()
        self._ensure_workspace_columns()
        self._ensure_task_columns()
        self._ensure_patch_columns()
        self._ensure_collaboration_task_columns()
        self._ensure_schedule_columns()

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
            "summary": "TEXT",
        }
        for column, definition in expected.items():
            if column not in columns:
                self._conn.execute(f"ALTER TABLE tasks ADD COLUMN {column} {definition}")
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

    def _ensure_collaboration_task_columns(self) -> None:
        columns = {
            row["name"]
            for row in self._conn.execute("PRAGMA table_info(collaboration_tasks)").fetchall()
        }
        if "error_json" not in columns:
            self._conn.execute("ALTER TABLE collaboration_tasks ADD COLUMN error_json TEXT")
        self._conn.commit()
