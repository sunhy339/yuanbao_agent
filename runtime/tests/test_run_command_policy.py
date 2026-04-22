from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from local_agent_runtime.policy.guard import PolicyGuard
from local_agent_runtime.store.sqlite_store import SQLiteStore
from local_agent_runtime.tools.builtin import build_builtin_tools


def _make_run_command(tmp_path: Path, config_patch: dict[str, Any] | None = None) -> tuple[SQLiteStore, Any, dict[str, Any]]:
    store = SQLiteStore(str(tmp_path / "runtime.sqlite3"))
    if config_patch:
        store.update_config({"config": config_patch})
    config = store.get_config({})["config"]
    policy_guard = PolicyGuard(approval_mode=config["policy"]["approvalMode"])
    tools = build_builtin_tools(policy_guard=policy_guard, store=store)
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    workspace = store.upsert_workspace(str(workspace_root))
    session = store.create_session(workspace_id=workspace["id"], title="Command policy")
    task = store.create_task(session_id=session["id"], task_type="chat", goal="command policy", plan=[])
    return store, tools["run_command"], {"workspace_root": workspace_root, "task_id": task["id"]}


def test_run_command_allowlist_permits_matching_command_for_approval(tmp_path: Path) -> None:
    store, run_command, ctx = _make_run_command(
        tmp_path,
        {
            "tools": {
                "runCommand": {
                    "allowedCommands": ["Write-Output *"],
                    "deniedCommands": ["python *"],
                }
            }
        },
    )
    try:
        result = run_command(
            {
                "workspaceRoot": str(ctx["workspace_root"]),
                "taskId": ctx["task_id"],
                "command": "Write-Output safe",
            }
        )

        assert result["status"] == "approval_required"
        assert result["command"] == "Write-Output safe"
    finally:
        store.close()


def test_run_command_denylist_rejects_before_approval(tmp_path: Path) -> None:
    store, run_command, ctx = _make_run_command(
        tmp_path,
        {"tools": {"runCommand": {"deniedCommands": ["python *"]}}},
    )
    try:
        with pytest.raises(ValueError, match="denied command"):
            run_command(
                {
                    "workspaceRoot": str(ctx["workspace_root"]),
                    "taskId": ctx["task_id"],
                    "command": "python -m pytest",
                }
            )

        approvals = store.list_trace_events({"taskId": ctx["task_id"]})["traceEvents"]
        assert not [event for event in approvals if event["type"] == "approval.requested"]
    finally:
        store.close()


def test_run_command_rejects_cwd_outside_allowed_roots(tmp_path: Path) -> None:
    allowed = tmp_path / "workspace" / "safe"
    store, run_command, ctx = _make_run_command(
        tmp_path,
        {"tools": {"runCommand": {"allowedCwdRoots": ["safe"]}}},
    )
    allowed.mkdir()
    (ctx["workspace_root"] / "unsafe").mkdir()
    try:
        with pytest.raises(ValueError, match="cwd is outside allowed roots"):
            run_command(
                {
                    "workspaceRoot": str(ctx["workspace_root"]),
                    "taskId": ctx["task_id"],
                    "cwd": "unsafe",
                    "command": "Write-Output safe",
                }
            )
    finally:
        store.close()


@pytest.mark.parametrize(
    "command",
    [
        "Remove-Item -Recurse -Force ..",
        "format C:",
        "shutdown /s /t 0",
        "del /s *",
        "rm -rf /",
    ],
)
def test_run_command_rejects_dangerous_patterns_before_approval(tmp_path: Path, command: str) -> None:
    store, run_command, ctx = _make_run_command(tmp_path)
    try:
        with pytest.raises(ValueError, match="dangerous command"):
            run_command(
                {
                    "workspaceRoot": str(ctx["workspace_root"]),
                    "taskId": ctx["task_id"],
                    "command": command,
                }
            )

        events = store.list_trace_events({"taskId": ctx["task_id"]})["traceEvents"]
        assert not [event for event in events if event["type"] == "approval.requested"]
    finally:
        store.close()
