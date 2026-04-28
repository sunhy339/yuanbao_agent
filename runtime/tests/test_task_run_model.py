from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from local_agent_runtime.store.sqlite_store import SQLiteStore


def _session(store: SQLiteStore) -> dict[str, object]:
    workspace = store.upsert_workspace("D:/tmp/project")
    return store.create_session(workspace["id"], "Task run model")


def test_task_run_fields_round_trip(tmp_path: Path) -> None:
    store = SQLiteStore(str(tmp_path / "runtime.sqlite3"))
    try:
        session = _session(store)
        plan = [
            {"id": "inspect", "title": "Inspect code", "status": "completed"},
            {"id": "edit", "title": "Edit files", "status": "active"},
        ]

        task = store.create_task(
            session_id=session["id"],
            task_type="edit",
            goal="Fix the chat renderer",
            plan=plan,
            acceptance_criteria=["Messages persist", "Tests pass"],
            out_of_scope=["Provider settings"],
        )

        assert task["acceptanceCriteria"] == ["Messages persist", "Tests pass"]
        assert task["outOfScope"] == ["Provider settings"]
        assert task["currentStep"] == "Edit files"
        assert task["changedFiles"] == []
        assert task["commands"] == []
        assert task["verification"] == []
        assert task["summary"] is None

        updated = store.update_task(
            task_id=task["id"],
            status="verifying",
            changed_files=[
                {
                    "path": "app/src/App.tsx",
                    "status": "modified",
                    "additions": 12,
                    "deletions": 3,
                    "reason": "Merge task event fields",
                }
            ],
            commands=[
                {
                    "id": "cmd_1",
                    "command": "npm run build",
                    "cwd": "app",
                    "status": "completed",
                    "exitCode": 0,
                    "durationMs": 1200,
                    "summary": "Build passed",
                }
            ],
            verification=[
                {
                    "id": "verify_1",
                    "command": "npm run build",
                    "status": "passed",
                    "exitCode": 0,
                    "summary": "Build passed",
                }
            ],
            summary="Task run model fields persisted.",
        )

        assert updated["status"] == "verifying"
        assert updated["changedFiles"][0]["path"] == "app/src/App.tsx"
        assert updated["commands"][0]["command"] == "npm run build"
        assert updated["verification"][0]["status"] == "passed"
        assert updated["summary"] == "Task run model fields persisted."

        listed = store.list_tasks({"sessionId": session["id"]})["tasks"]
        assert listed[0]["changedFiles"] == updated["changedFiles"]
        assert listed[0]["commands"] == updated["commands"]
        assert listed[0]["verification"] == updated["verification"]
    finally:
        store.close()


def test_legacy_task_table_migrates_task_run_columns(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy.sqlite3"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE tasks (
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
        )
        """
    )
    conn.execute(
        """
        INSERT INTO tasks (id, session_id, type, status, goal, plan_json, result_json, error_code, created_at, updated_at)
        VALUES ('task_legacy', 'sess_legacy', 'edit', 'completed', 'legacy goal', ?, 'done', NULL, 1, 2)
        """,
        (json.dumps([{"id": "legacy", "title": "Legacy step", "status": "completed"}]),),
    )
    conn.commit()
    conn.close()

    store = SQLiteStore(str(db_path))
    try:
        task = store.get_task({"taskId": "task_legacy"})["task"]

        assert task["acceptanceCriteria"] == []
        assert task["outOfScope"] == []
        assert task["currentStep"] is None
        assert task["changedFiles"] == []
        assert task["commands"] == []
        assert task["verification"] == []
        assert task["summary"] is None

        updated = store.update_task(
            task_id="task_legacy",
            current_step="Verify migration",
            verification=[{"status": "passed", "summary": "Migration works"}],
        )

        assert updated["currentStep"] == "Verify migration"
        assert updated["verification"] == [{"status": "passed", "summary": "Migration works"}]
    finally:
        store.close()


def test_legacy_workspace_table_migrates_project_memory_column(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy_workspace.sqlite3"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE workspaces (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            root_path TEXT NOT NULL UNIQUE,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        )
        """
    )
    conn.execute(
        """
        INSERT INTO workspaces (id, name, root_path, created_at, updated_at)
        VALUES ('ws_legacy', 'legacy', 'D:/tmp/legacy', 1, 2)
        """
    )
    conn.commit()
    conn.close()

    store = SQLiteStore(str(db_path))
    try:
        workspace = store.require_workspace("ws_legacy")

        assert workspace["summary"] is None

        updated = store.update_workspace_summary(
            "ws_legacy",
            "Project memory:\n- completed: migrated workspace summary.",
        )

        assert "migrated workspace summary" in updated["summary"]
    finally:
        store.close()


def test_workspace_focus_round_trip_and_legacy_migration(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy_workspace_focus.sqlite3"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE workspaces (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            root_path TEXT NOT NULL UNIQUE,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        )
        """
    )
    conn.execute(
        """
        INSERT INTO workspaces (id, name, root_path, created_at, updated_at)
        VALUES ('ws_focus', 'legacy', 'D:/tmp/focus', 1, 2)
        """
    )
    conn.commit()
    conn.close()

    store = SQLiteStore(str(db_path))
    try:
        workspace = store.require_workspace("ws_focus")

        assert workspace["focus"] is None

        updated = store.update_workspace_focus(
            {
                "workspaceId": "ws_focus",
                "focus": "Stay focused on large-project iteration.",
            }
        )["workspace"]

        assert updated["focus"] == "Stay focused on large-project iteration."
    finally:
        store.close()
