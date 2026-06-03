from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from local_agent_runtime.policy.guard import PolicyGuard
from local_agent_runtime.policy.permission_engine import PermissionEngine
from local_agent_runtime.store.sqlite_store import SQLiteStore
from local_agent_runtime.tools import build_builtin_tools


def _make_run_command(
    tmp_path: Path,
    config_patch: dict[str, Any] | None = None,
    *,
    use_permission_engine: bool = False,
) -> tuple[SQLiteStore, Any, dict[str, Any]]:
    store = SQLiteStore(str(tmp_path / "runtime.sqlite3"))
    if config_patch:
        store.update_config({"config": config_patch})
    config = store.get_config({})["config"]
    policy_guard = PolicyGuard(approval_mode=config["policy"]["approvalMode"])
    permission_engine = PermissionEngine(config=config, store=store) if use_permission_engine else None
    tools = build_builtin_tools(policy_guard=policy_guard, store=store, permission_engine=permission_engine)
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


def test_run_command_default_policy_sends_unknown_command_to_approval(tmp_path: Path) -> None:
    store, run_command, ctx = _make_run_command(tmp_path)
    try:
        result = run_command(
            {
                "workspaceRoot": str(ctx["workspace_root"]),
                "taskId": ctx["task_id"],
                "command": "python main.py",
            }
        )
        assert result["status"] == "approval_required"
        assert result["command"] == "python main.py"
    finally:
        store.close()


def test_run_command_default_policy_sends_git_init_to_approval(tmp_path: Path) -> None:
    store, run_command, ctx = _make_run_command(tmp_path)
    try:
        result = run_command(
            {
                "workspaceRoot": str(ctx["workspace_root"]),
                "taskId": ctx["task_id"],
                "command": "git init",
            }
        )
        assert result["status"] == "approval_required"
        assert result["command"] == "git init"
    finally:
        store.close()


def test_run_command_configured_allowlist_routes_unmatched_command_to_approval(tmp_path: Path) -> None:
    store, run_command, ctx = _make_run_command(
        tmp_path,
        {"tools": {"runCommand": {"allowedCommands": ["python -m pytest*"]}}},
    )
    try:
        result = run_command(
            {
                "workspaceRoot": str(ctx["workspace_root"]),
                "taskId": ctx["task_id"],
                "command": "python main.py",
            }
        )
        assert result["status"] == "approval_required"
        assert result["command"] == "python main.py"
        request = json.loads(result["approval"]["requestJson"])
        assert request["policyAction"] == "approval_required"
        assert "allowlist" in request["policyReason"]
    finally:
        store.close()


def test_run_command_internal_validation_routes_allowlist_mismatch_to_approval(tmp_path: Path) -> None:
    store, run_command, ctx = _make_run_command(
        tmp_path,
        {"tools": {"runCommand": {"allowedCommands": ["python -m pytest*"]}}},
    )
    try:
        result = run_command(
            {
                "workspaceRoot": str(ctx["workspace_root"]),
                "taskId": ctx["task_id"],
                "command": "python main.py",
                "internalValidation": True,
            }
        )
        assert result["status"] == "approval_required"
        request = json.loads(result["approval"]["requestJson"])
        assert request["command"] == "python main.py"
        assert request["policyAction"] == "approval_required"
        assert "allowlist" in request["policyReason"]
    finally:
        store.close()


def test_run_command_default_allowlist_keeps_verification_command_available(tmp_path: Path) -> None:
    store, run_command, ctx = _make_run_command(tmp_path)
    try:
        result = run_command(
            {
                "workspaceRoot": str(ctx["workspace_root"]),
                "taskId": ctx["task_id"],
                "command": "python -m pytest -q",
            }
        )
        assert result["status"] == "approval_required"
    finally:
        store.close()


def test_run_command_default_allowlist_accepts_absolute_python_pytest(tmp_path: Path) -> None:
    store, run_command, ctx = _make_run_command(tmp_path)
    try:
        result = run_command(
            {
                "workspaceRoot": str(ctx["workspace_root"]),
                "taskId": ctx["task_id"],
                "command": r"C:\Python314\python.exe -m pytest -q",
            }
        )
        assert result["status"] == "approval_required"
    finally:
        store.close()


def test_run_command_default_allowlist_accepts_powershell_quoted_python_pytest(tmp_path: Path) -> None:
    store, run_command, ctx = _make_run_command(tmp_path)
    try:
        result = run_command(
            {
                "workspaceRoot": str(ctx["workspace_root"]),
                "taskId": ctx["task_id"],
                "command": r'& "C:\Python314\python.exe" -m pytest -q',
            }
        )
        assert result["status"] == "approval_required"
    finally:
        store.close()


def test_run_command_default_allowlist_keeps_python_c_with_quoted_semicolons_available(tmp_path: Path) -> None:
    store, run_command, ctx = _make_run_command(tmp_path)
    try:
        result = run_command(
            {
                "workspaceRoot": str(ctx["workspace_root"]),
                "taskId": ctx["task_id"],
                "command": "python -c \"import sys; print('bad'); sys.exit(3)\"",
            }
        )
        assert result["status"] == "approval_required"
    finally:
        store.close()


def test_run_command_permission_engine_allows_low_risk_verification_without_approval(tmp_path: Path) -> None:
    store, run_command, ctx = _make_run_command(tmp_path, use_permission_engine=True)
    try:
        source = ctx["workspace_root"] / "ok.py"
        source.write_text("VALUE = 1\n", encoding="utf-8")

        result = run_command(
            {
                "workspaceRoot": str(ctx["workspace_root"]),
                "taskId": ctx["task_id"],
                "command": "python -m py_compile ok.py",
            }
        )

        assert result["status"] == "completed"
        assert result["exitCode"] == 0
        approvals = store._conn.execute(  # noqa: SLF001
            "SELECT * FROM approvals WHERE task_id = ?",
            (ctx["task_id"],),
        ).fetchall()
        assert approvals == []
    finally:
        store.close()


def test_run_command_permission_engine_still_requires_approval_for_unknown_shell(tmp_path: Path) -> None:
    store, run_command, ctx = _make_run_command(tmp_path, use_permission_engine=True)
    try:
        result = run_command(
            {
                "workspaceRoot": str(ctx["workspace_root"]),
                "taskId": ctx["task_id"],
                "command": "python main.py",
            }
        )

        assert result["status"] == "approval_required"
        assert result["command"] == "python main.py"
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
