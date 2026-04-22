from __future__ import annotations

import json
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
    },
    "tools": {
        "runCommand": {
            "allowedShell": "powershell",
            "blockedPatterns": ["rm -rf", "shutdown", "format"],
        }
    },
    "ui": {
        "language": "zh-CN",
        "showRawEvents": False,
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
            INSERT INTO workspaces (id, name, root_path, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
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

    def create_task(self, session_id: str, task_type: str, goal: str, plan: list[dict[str, Any]]) -> dict[str, Any]:
        task_id = self.new_id("task")
        now = self.now()
        self._conn.execute(
            """
            INSERT INTO tasks (id, session_id, type, status, goal, plan_json, result_json, error_code, created_at, updated_at)
            VALUES (?, ?, ?, 'running', ?, ?, NULL, NULL, ?, ?)
            """,
            (task_id, session_id, task_type, goal, json.dumps(plan, ensure_ascii=False), now, now),
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
                trace_session_id,
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

        self._config = self._merge_config(self._config, patch)
        self._persist_config(self._config)
        return {"config": deepcopy(self._config)}

    def _serialize_workspace(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": row["id"],
            "name": row["name"],
            "rootPath": row["root_path"],
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

    def _serialize_task(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": row["id"],
            "sessionId": row["session_id"],
            "type": row["type"],
            "status": row["status"],
            "goal": row["goal"],
            "plan": json.loads(row["plan_json"] or "[]"),
            "resultSummary": row["result_json"],
            "errorCode": row["error_code"],
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
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

        config = self._merge_config(DEFAULT_CONFIG, loaded)
        if config != loaded:
            self._persist_config(config)
        return config

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
                plan_json TEXT,
                result_json TEXT,
                error_code TEXT,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
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

            CREATE INDEX IF NOT EXISTS idx_trace_events_task_order
                ON trace_events (task_id, created_at, sequence);
            """
        )
        self._conn.commit()
        self._ensure_patch_columns()

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
