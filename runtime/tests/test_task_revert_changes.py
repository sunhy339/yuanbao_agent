from __future__ import annotations

from pathlib import Path

import pytest

from local_agent_runtime.event_bus import EventBus
from local_agent_runtime.rpc.server import JsonRpcServer
from local_agent_runtime.store.sqlite_store import SQLiteStore


class _RpcOrchestratorStub:
    _memory_manager = None

    def __getattr__(self, name: str):
        def _handler(_params: dict):
            raise NotImplementedError(name)

        return _handler


@pytest.fixture
def store(tmp_path: Path) -> SQLiteStore:
    runtime_store = SQLiteStore(str(tmp_path / "runtime.sqlite3"))
    try:
        yield runtime_store
    finally:
        runtime_store.close()


def _task_with_applied_patch(store: SQLiteStore, tmp_path: Path, *, drift: bool = False) -> tuple[dict, dict, Path]:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    target = workspace_root / "src" / "app.py"
    target.parent.mkdir()
    target.write_text("print('new')\n", encoding="utf-8")
    if drift:
        target.write_text("print('newer')\n", encoding="utf-8")

    workspace = store.upsert_workspace(str(workspace_root))
    session = store.create_session(workspace_id=workspace["id"], title="Revert")
    task = store.create_task(session["id"], "edit", "Update app", [])
    diff_text = "\n".join(
        [
            "--- a/src/app.py",
            "+++ b/src/app.py",
            "@@ -1 +1 @@",
            "-print('old')",
            "+print('new')",
            "",
        ]
    )
    patch = store.create_patch(
        task_id=task["id"],
        workspace_id=str(workspace_root),
        summary="Update app.py",
        diff_text=diff_text,
        files_changed=1,
        status="applied",
    )
    task = store.update_task(
        task["id"],
        changed_files=[
            {
                "path": "src/app.py",
                "status": "modified",
                "patchId": patch["id"],
            }
        ],
    )
    return task, patch, target


def test_task_revert_changes_reverse_applies_saved_patch(store: SQLiteStore, tmp_path: Path) -> None:
    task, patch, target = _task_with_applied_patch(store, tmp_path)
    server = JsonRpcServer(
        orchestrator=_RpcOrchestratorStub(),
        store=store,
        event_bus=EventBus(),
    )

    response = server.handle_line(
        """
        {"jsonrpc":"2.0","id":"req_1","method":"task.revertChanges","params":{"taskId":"%s"}}
        """
        % task["id"]
    )

    assert "error" not in response
    assert response["result"]["reverted"] is True
    assert response["result"]["changedPaths"] == ["src/app.py"]
    assert response["result"]["patches"][0]["id"] == patch["id"]
    assert response["result"]["patches"][0]["status"] == "reverted"
    assert response["result"]["task"]["changedFiles"] == []
    assert target.read_text(encoding="utf-8") == "print('old')\n"


def test_task_revert_changes_refuses_when_patch_no_longer_matches(store: SQLiteStore, tmp_path: Path) -> None:
    task, _patch, target = _task_with_applied_patch(store, tmp_path, drift=True)
    server = JsonRpcServer(
        orchestrator=_RpcOrchestratorStub(),
        store=store,
        event_bus=EventBus(),
    )

    response = server.handle_line(
        """
        {"jsonrpc":"2.0","id":"req_1","method":"task.revertChanges","params":{"taskId":"%s"}}
        """
        % task["id"]
    )

    assert response["error"]["code"] == "PATCH_APPLY_FAILED"
    assert "patch does not apply" in response["error"]["message"]
    assert target.read_text(encoding="utf-8") == "print('newer')\n"


def test_task_revert_changes_handles_sequential_patches_on_same_file(store: SQLiteStore, tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    target = workspace_root / "src" / "app.py"
    target.parent.mkdir()
    target.write_text("print('three')\n", encoding="utf-8")

    workspace = store.upsert_workspace(str(workspace_root))
    session = store.create_session(workspace_id=workspace["id"], title="Revert")
    task = store.create_task(session["id"], "edit", "Update app twice", [])
    first = store.create_patch(
        task_id=task["id"],
        workspace_id=str(workspace_root),
        summary="one to two",
        diff_text="\n".join([
            "--- a/src/app.py",
            "+++ b/src/app.py",
            "@@ -1 +1 @@",
            "-print('one')",
            "+print('two')",
            "",
        ]),
        files_changed=1,
        status="applied",
    )
    second = store.create_patch(
        task_id=task["id"],
        workspace_id=str(workspace_root),
        summary="two to three",
        diff_text="\n".join([
            "--- a/src/app.py",
            "+++ b/src/app.py",
            "@@ -1 +1 @@",
            "-print('two')",
            "+print('three')",
            "",
        ]),
        files_changed=1,
        status="applied",
    )
    store.update_task(
        task["id"],
        changed_files=[
            {"path": "src/app.py", "patchId": first["id"]},
            {"path": "src/app.py", "patchId": second["id"]},
        ],
    )
    server = JsonRpcServer(
        orchestrator=_RpcOrchestratorStub(),
        store=store,
        event_bus=EventBus(),
    )

    response = server.handle_line(
        """
        {"jsonrpc":"2.0","id":"req_1","method":"task.revertChanges","params":{"taskId":"%s"}}
        """
        % task["id"]
    )

    assert "error" not in response
    assert target.read_text(encoding="utf-8") == "print('one')\n"
    assert [patch["status"] for patch in response["result"]["patches"]] == ["reverted", "reverted"]
