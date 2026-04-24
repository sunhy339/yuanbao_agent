from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest

from local_agent_runtime.policy.guard import PolicyGuard
from local_agent_runtime.store.sqlite_store import SQLiteStore
from local_agent_runtime.tools.builtin import build_builtin_tools


def _make_run_command(
    tmp_path: Path,
    config_patch: dict[str, Any] | None = None,
) -> tuple[SQLiteStore, Any, dict[str, Any]]:
    store = SQLiteStore(str(tmp_path / "runtime.sqlite3"))
    if config_patch:
        store.update_config({"config": config_patch})
    config = store.get_config({})["config"]
    policy_guard = PolicyGuard(approval_mode=config["policy"]["approvalMode"])
    tools = build_builtin_tools(policy_guard=policy_guard, store=store)
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    workspace = store.upsert_workspace(str(workspace_root))
    session = store.create_session(workspace_id=workspace["id"], title="Background command")
    task = store.create_task(session_id=session["id"], task_type="chat", goal="background command", plan=[])
    return store, tools["run_command"], {"workspace_root": workspace_root, "task_id": task["id"]}


def _wait_for_command_log(store: SQLiteStore, command_id: str, *, timeout: float = 5.0) -> dict[str, Any]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        command_log = store.get_command_log({"commandId": command_id})["commandLog"]
        if command_log["status"] != "running":
            return command_log
        time.sleep(0.05)
    raise AssertionError(f"Command log {command_id} did not finish within {timeout} seconds")


def _command_artifact_path(store: SQLiteStore, command_id: str, stream_name: str) -> Path:
    assert store.database_path != ":memory:"
    return Path(store.database_path).resolve().parent / "runtime_artifacts" / f"{command_id}_{stream_name}.log"


@pytest.mark.parametrize("flag_name", ["background", "backgroundJob", "runInBackground"])
def test_run_command_background_returns_immediately_and_finishes(flag_name: str, tmp_path: Path) -> None:
    store, run_command, ctx = _make_run_command(
        tmp_path,
        {"policy": {"approvalMode": "never"}},
    )
    try:
        started = time.perf_counter()
        result = run_command(
            {
                "workspaceRoot": str(ctx["workspace_root"]),
                "taskId": ctx["task_id"],
                "command": "Start-Sleep -Milliseconds 250; Write-Output background-finished",
                "internalValidation": True,
                flag_name: True,
            }
        )
        elapsed = time.perf_counter() - started

        assert result["status"] == "running"
        assert result["commandLog"]["status"] == "running"
        assert result.get("stdout", "") == ""
        assert result.get("stderr", "") == ""
        assert result["exitCode"] is None
        assert elapsed < 0.2

        command_log = _wait_for_command_log(store, result["commandLog"]["id"])

        assert command_log["status"] == "completed"
        assert command_log["exitCode"] == 0
        assert command_log["finishedAt"] is not None
        assert Path(command_log["stdoutPath"]).read_text(encoding="utf-8").strip() == "background-finished"
        assert Path(command_log["stderrPath"]).read_text(encoding="utf-8") == ""
    finally:
        store.close()


def test_run_command_background_updates_log_on_timeout(tmp_path: Path) -> None:
    store, run_command, ctx = _make_run_command(
        tmp_path,
        {"policy": {"approvalMode": "never"}},
    )
    try:
        started = time.perf_counter()
        result = run_command(
            {
                "workspaceRoot": str(ctx["workspace_root"]),
                "taskId": ctx["task_id"],
                "command": "Start-Sleep -Milliseconds 2000; Write-Output too-late",
                "background": True,
                "internalValidation": True,
                "timeoutMs": 1000,
            }
        )
        elapsed = time.perf_counter() - started

        assert result["status"] == "running"
        assert result["commandLog"]["status"] == "running"
        assert elapsed < 0.2

        command_log = _wait_for_command_log(store, result["commandLog"]["id"])

        assert command_log["status"] == "timeout"
        assert command_log["exitCode"] is None
        assert command_log["finishedAt"] is not None
        assert Path(command_log["stdoutPath"]).read_text(encoding="utf-8") == ""
    finally:
        store.close()


def test_run_command_background_drains_output_while_process_is_still_running(tmp_path: Path) -> None:
    store, run_command, ctx = _make_run_command(
        tmp_path,
        {"policy": {"approvalMode": "never"}},
    )
    try:
        result = run_command(
            {
                "workspaceRoot": str(ctx["workspace_root"]),
                "taskId": ctx["task_id"],
                "command": 'Write-Output "alpha"; Start-Sleep -Milliseconds 400; Write-Output "omega"',
                "background": True,
                "internalValidation": True,
            }
        )

        command_id = result["commandLog"]["id"]
        stdout_path = _command_artifact_path(store, command_id, "stdout")
        deadline = time.time() + 5.0
        while time.time() < deadline:
            command_log = store.get_command_log({"commandId": command_id})["commandLog"]
            if stdout_path.exists() and "alpha" in stdout_path.read_text(encoding="utf-8", errors="replace"):
                if command_log["status"] == "running":
                    break
            time.sleep(0.05)
        else:
            raise AssertionError("background command did not expose partial stdout while still running")

        command_log = _wait_for_command_log(store, command_id)
        assert command_log["status"] == "completed"
        stdout_text = Path(command_log["stdoutPath"]).read_text(encoding="utf-8")
        assert "alpha" in stdout_text
        assert "omega" in stdout_text
        trace_events = store.list_trace_events({"taskId": ctx["task_id"]})["traceEvents"]
        assert any(
            event["type"] == "command.output"
            and event["payload"]["commandId"] == command_id
            and event["payload"]["stream"] == "stdout"
            and "alpha" in event["payload"]["chunk"]
            for event in trace_events
        )
    finally:
        store.close()


def test_run_command_background_preserves_approval_flow(tmp_path: Path) -> None:
    store, run_command, ctx = _make_run_command(tmp_path)
    try:
        result = run_command(
            {
                "workspaceRoot": str(ctx["workspace_root"]),
                "taskId": ctx["task_id"],
                "command": "Write-Output needs-approval",
                "background": True,
            }
        )

        assert result["status"] == "approval_required"
        assert result["background"] is True
        assert json.loads(result["approval"]["requestJson"])["background"] is True
    finally:
        store.close()
