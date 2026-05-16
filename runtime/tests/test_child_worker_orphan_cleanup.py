from __future__ import annotations

from pathlib import Path
from typing import Any

from local_agent_runtime.main import build_server
from local_agent_runtime.store.sqlite_store import SQLiteStore


def test_child_worker_server_does_not_orphan_cleanup_parent_task(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    db_path = tmp_path / "runtime.sqlite3"
    store = SQLiteStore(str(db_path))
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    workspace = store.upsert_workspace(str(workspace_root))
    session = store.create_session(workspace_id=workspace["id"], title="parent")
    parent_task = store.create_task(
        session_id=session["id"],
        task_type="edit",
        goal="parent still running while child worker starts",
        plan=[],
        status="running",
    )
    store.close()

    monkeypatch.setenv("LOCAL_AGENT_CHILD_WORKER", "1")
    server = build_server(database_path=str(db_path))
    try:
        recovered = server._store.get_task({"taskId": parent_task["id"]})["task"]  # noqa: SLF001
        assert recovered["status"] == "running"
        assert recovered.get("errorCode") is None
    finally:
        server.graceful_shutdown()

