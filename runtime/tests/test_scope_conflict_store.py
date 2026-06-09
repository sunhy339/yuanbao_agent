from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any

from local_agent_runtime.rpc import server as server_mod
from local_agent_runtime.store.sqlite_store import SQLiteStore


def _store_with_context(tmp_path: Path) -> tuple[SQLiteStore, dict[str, Any]]:
    store = SQLiteStore(str(tmp_path / "runtime.sqlite3"))
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(parents=True, exist_ok=True)
    workspace = store.upsert_workspace(str(workspace_root))
    session = store.create_session(workspace_id=workspace["id"], title="scope-test")
    task = store.create_task(session_id=session["id"], task_type="chat", goal="test", plan=[])
    return store, {"session": session, "task": task}


def test_scope_conflict_check_records_overlap(tmp_path: Path) -> None:
    store, ctx = _store_with_context(tmp_path)
    try:
        result = store.check_dispatch_scope(
            {
                "taskId": ctx["task"]["id"],
                "sessionId": ctx["session"]["id"],
                "subtasks": [
                    {"id": "a", "ownedScope": ["shared.py"]},
                    {"id": "b", "ownedScope": ["shared.py"]},
                ],
            }
        )
        history = store.list_scope_conflict_checks({"taskId": ctx["task"]["id"]})
    finally:
        store.close()

    assert result["safe"] is False
    assert result["resolution"] == "serialized"
    assert result["overlaps"]
    assert "shared.py" in result["overlaps"][0]
    assert "'a'" in result["overlaps"][0]
    assert "'b'" in result["overlaps"][0]
    assert history["scopeConflictChecks"][0]["checkType"] == "pre_dispatch"


def test_scope_conflict_rpc_handlers_remain_registered() -> None:
    source = inspect.getsource(server_mod)
    assert '"scope.checkDispatch"' in source
    assert '"scope.conflictHistory"' in source
