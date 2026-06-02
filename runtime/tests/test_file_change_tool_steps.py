from __future__ import annotations

from pathlib import Path
from typing import Any

from local_agent_runtime.policy.guard import PolicyGuard
from local_agent_runtime.store.sqlite_store import SQLiteStore
from local_agent_runtime.tools.apply_patch import build_apply_patch_tool
from local_agent_runtime.tools.write_file import build_write_file_tool


def _runtime_context(tmp_path: Path) -> tuple[SQLiteStore, Path, str]:
    store = SQLiteStore(":memory:")
    store.update_config({"config": {"policy": {"approvalMode": "none"}}})
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    workspace = store.upsert_workspace(str(workspace_root))
    session = store.create_session(workspace_id=workspace["id"], title="file steps")
    task = store.create_task(session_id=session["id"], task_type="chat", goal="change files", plan=[])
    return store, workspace_root, task["id"]


def _step_labels(result: dict[str, Any]) -> list[str]:
    return [step["label"] for step in result.get("steps", [])]


def test_write_file_result_includes_runtime_steps(tmp_path: Path) -> None:
    store, workspace_root, task_id = _runtime_context(tmp_path)
    tool = build_write_file_tool(PolicyGuard(approval_mode="none"), store)["handler"]

    result = tool({
        "workspaceRoot": str(workspace_root),
        "taskId": task_id,
        "path": "notes/todo.txt",
        "content": "hello\n",
    })

    assert result["status"] == "written"
    assert result["bytesWritten"] == 6
    assert (workspace_root / "notes" / "todo.txt").read_text(encoding="utf-8") == "hello\n"
    assert _step_labels(result) == ["resolve", "scope", "diff", "approval", "mkdir", "write"]
    assert result["steps"][0]["summary"] == "notes/todo.txt"
    assert result["steps"][-1]["summary"] == "6 byte(s)"


def test_apply_patch_result_includes_runtime_steps(tmp_path: Path) -> None:
    store, workspace_root, task_id = _runtime_context(tmp_path)
    target = workspace_root / "README.md"
    target.write_text("old line\n", encoding="utf-8")
    tool = build_apply_patch_tool(PolicyGuard(approval_mode="none"), store)["handler"]
    patch_text = "\n".join(
        [
            "diff --git a/README.md b/README.md",
            "--- a/README.md",
            "+++ b/README.md",
            "@@ -1 +1 @@",
            "-old line",
            "+new line",
        ]
    )

    result = tool({
        "workspaceRoot": str(workspace_root),
        "taskId": task_id,
        "patchText": patch_text,
    })

    assert result["status"] == "applied"
    assert target.read_text(encoding="utf-8") == "new line\n"
    assert _step_labels(result) == ["parse", "validate", "approval", "apply"]
    assert result["steps"][0]["summary"] == "1 file(s)"
    assert result["steps"][-1]["summary"] == "1 changed path(s)"
