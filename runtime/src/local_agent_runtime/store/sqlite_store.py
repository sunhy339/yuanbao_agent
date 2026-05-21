from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any

from ._constants import CONFIG_KEY, DEFAULT_CONFIG
from .agent_store import AgentStoreMixin
from .config_store import ConfigStoreMixin
from .event_store import EventStoreMixin
from .agent_profile_store import AgentProfileStoreMixin
from .extension_store import ExtensionStoreMixin
from .proposal_store import ProposalStoreMixin
from .repositories.hook_repository import HookStoreMixin
from .repositories.worktree_repository import WorktreeStoreMixin
from .session_store import SessionStoreMixin
from .task_store import TaskStoreMixin
from ._schema import SchemaBootstrapMixin


class _LockedCursor:
    def __init__(self, cursor: sqlite3.Cursor, lock: threading.RLock) -> None:
        self._cursor = cursor
        self._lock = lock

    def fetchone(self) -> Any:
        with self._lock:
            return self._cursor.fetchone()

    def fetchall(self) -> list[Any]:
        with self._lock:
            return self._cursor.fetchall()

    def fetchmany(self, size: int | None = None) -> list[Any]:
        with self._lock:
            if size is None:
                return self._cursor.fetchmany()
            return self._cursor.fetchmany(size)

    def __iter__(self) -> Any:
        with self._lock:
            rows = list(self._cursor)
        return iter(rows)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._cursor, name)


class _LockedConnection:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._lock = threading.RLock()

    @property
    def row_factory(self) -> Any:
        return self._conn.row_factory

    @row_factory.setter
    def row_factory(self, value: Any) -> None:
        self._conn.row_factory = value

    def execute(self, *args: Any, **kwargs: Any) -> _LockedCursor:
        with self._lock:
            return _LockedCursor(self._conn.execute(*args, **kwargs), self._lock)

    def executemany(self, *args: Any, **kwargs: Any) -> _LockedCursor:
        with self._lock:
            return _LockedCursor(self._conn.executemany(*args, **kwargs), self._lock)

    def executescript(self, *args: Any, **kwargs: Any) -> _LockedCursor:
        with self._lock:
            return _LockedCursor(self._conn.executescript(*args, **kwargs), self._lock)

    def commit(self) -> None:
        with self._lock:
            self._conn.commit()

    def rollback(self) -> None:
        with self._lock:
            self._conn.rollback()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._conn, name)


class SQLiteStore(
    SchemaBootstrapMixin,
    SessionStoreMixin,
    TaskStoreMixin,
    AgentStoreMixin,
    AgentProfileStoreMixin,
    EventStoreMixin,
    ConfigStoreMixin,
    ProposalStoreMixin,
    ExtensionStoreMixin,
    HookStoreMixin,
    WorktreeStoreMixin,
):
    """Small SQLite wrapper for the MVP scaffold."""

    def __init__(self, database_path: str) -> None:
        self._database_path = database_path
        database_file = Path(database_path)
        if database_path != ":memory:":
            database_file.expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        self._conn = _LockedConnection(sqlite3.connect(database_path, check_same_thread=False))
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
        with self._conn._lock:
            self._conn.execute(
                "UPDATE seq_counter SET val = val + 1 WHERE id = 1"
            )
            row = self._conn.execute(
                "SELECT val FROM seq_counter WHERE id = 1"
            ).fetchone()
            self._conn.commit()
            return int(row["val"])

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

    def storage_stats(self, _params: dict[str, Any] | None = None) -> dict[str, Any]:
        database_path = self.database_path
        db_file = Path(database_path).expanduser().resolve() if database_path != ":memory:" else None
        artifact_dir = self._artifact_dir

        page_count = int(self._conn.execute("PRAGMA page_count").fetchone()[0])
        page_size = int(self._conn.execute("PRAGMA page_size").fetchone()[0])
        freelist_count = int(self._conn.execute("PRAGMA freelist_count").fetchone()[0])

        def _count(table: str) -> int:
            return int(self._conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])

        table_counts = {
            "sessions": _count("sessions"),
            "messages": _count("messages"),
            "tasks": _count("tasks"),
            "trace_events": _count("trace_events"),
            "provider_turns": _count("provider_turns"),
            "context_snapshots": _count("context_snapshots"),
            "command_logs": _count("command_logs"),
            "memory_entries": _count("memory_entries"),
            "memory_recall_records": _count("memory_recall_records"),
            "artifacts": _count("artifacts"),
            "hook_executions": _count("hook_executions"),
            "proposal_records": _count("proposal_records"),
            "task_worktrees": _count("task_worktrees"),
        }

        artifact_files = list(artifact_dir.glob("*")) if artifact_dir.exists() else []
        artifact_bytes = sum(path.stat().st_size for path in artifact_files if path.is_file())

        return {
            "database": {
                "path": str(db_file) if db_file else ":memory:",
                "exists": bool(db_file.exists()) if db_file else True,
                "fileBytes": int(db_file.stat().st_size) if db_file and db_file.exists() else 0,
                "logicalBytes": page_count * page_size,
                "pageCount": page_count,
                "pageSize": page_size,
                "freelistCount": freelist_count,
                "freeBytesEstimate": freelist_count * page_size,
            },
            "artifacts": {
                "path": str(artifact_dir),
                "fileCount": len([path for path in artifact_files if path.is_file()]),
                "totalBytes": artifact_bytes,
            },
            "tables": table_counts,
        }

    def storage_cleanup(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        vacuum = bool((params or {}).get("vacuum", False))
        removed_expired_cache = 0
        try:
            from ..provider.cache import LLMCache

            removed_expired_cache = int(LLMCache(self).cleanup_expired())
        except Exception:
            removed_expired_cache = 0

        if vacuum and self.database_path != ":memory:":
            self._conn.execute("VACUUM")
            self._conn.commit()

        result = self.storage_stats({})
        result["cleanup"] = {
            "expiredCacheEntriesRemoved": removed_expired_cache,
            "vacuumRan": vacuum and self.database_path != ":memory:",
        }
        return result

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


