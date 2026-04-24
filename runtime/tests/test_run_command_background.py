from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest

from local_agent_runtime.event_bus import EventBus
from local_agent_runtime.orchestrator.service import Orchestrator
from local_agent_runtime.policy.guard import PolicyGuard
from local_agent_runtime.provider.adapter import ProviderAdapter
from local_agent_runtime.rpc.server import JsonRpcServer
from local_agent_runtime.services import CollaborationService, SubagentService
from local_agent_runtime.services.command_background import get_background_command_event_bridge
from local_agent_runtime.policy.guard import PolicyGuard
from local_agent_runtime.store.sqlite_store import SQLiteStore
from local_agent_runtime.tools.builtin import build_builtin_tools
from local_agent_runtime.tools.registry import ToolRegistry


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


def _make_runtime(tmp_path: Path) -> tuple[SQLiteStore, JsonRpcServer, Any, dict[str, Any], list[dict[str, Any]]]:
    store = SQLiteStore(str(tmp_path / "runtime.sqlite3"))
    store.update_config({"config": {"policy": {"approvalMode": "never"}}})
    config = store.get_config({})["config"]
    event_bus = EventBus()
    events: list[dict[str, Any]] = []
    event_bus.subscribe(lambda event: events.append(event_bus.as_payload(event)))
    get_background_command_event_bridge(store.database_path).add_listener(lambda event: events.append(event))
    policy_guard = PolicyGuard(approval_mode=config["policy"]["approvalMode"])
    collaboration = CollaborationService(store, event_bus)
    subagent_service = SubagentService(store, collaboration)
    tools = build_builtin_tools(policy_guard=policy_guard, store=store, subagent_service=subagent_service)
    tool_registry = ToolRegistry(tools)
    orchestrator = Orchestrator(
        store=store,
        event_bus=event_bus,
        tool_registry=tool_registry,
        provider=ProviderAdapter(),
    )
    server = JsonRpcServer(orchestrator=orchestrator, store=store, event_bus=event_bus)
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    workspace = store.upsert_workspace(str(workspace_root))
    session = store.create_session(workspace_id=workspace["id"], title="Background runtime")
    task = store.create_task(session_id=session["id"], task_type="chat", goal="background runtime", plan=[])
    return store, server, tools["run_command"], {"workspace_root": workspace_root, "task_id": task["id"]}, events


def _wait_for_event(
    events: list[dict[str, Any]],
    predicate: Any,
    *,
    timeout: float = 5.0,
) -> dict[str, Any]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        for event in list(events):
            if predicate(event):
                return event
        time.sleep(0.05)
    raise AssertionError(f"Timed out waiting for event within {timeout} seconds")


def _rpc_call(server: JsonRpcServer, method: str, params: dict[str, Any]) -> dict[str, Any]:
    response = server.handle_line(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": f"{method}_test",
                "method": method,
                "params": params,
            },
            ensure_ascii=False,
        )
    )
    assert "error" not in response
    return response["result"]


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


def test_command_log_rpc_get_and_list_filters_logs(tmp_path: Path) -> None:
    store, server, _run_command, ctx, _events = _make_runtime(tmp_path)
    try:
        task = store.get_task({"taskId": ctx["task_id"]})["task"]
        workspace = store.upsert_workspace(str(ctx["workspace_root"]))
        second_task = store.create_task(session_id=task["sessionId"], task_type="chat", goal="second task", plan=[])
        other_session = store.create_session(workspace_id=workspace["id"], title="Other session")
        other_task = store.create_task(session_id=other_session["id"], task_type="chat", goal="other task", plan=[])

        first_log = store.create_command_log(
            task_id=task["id"],
            command="Write-Output first",
            cwd=str(ctx["workspace_root"]),
            shell="powershell",
        )
        first_log = store.update_command_log(
            first_log["id"],
            status="completed",
            exit_code=0,
            stdout_path=None,
            stderr_path=None,
        )
        second_log = store.create_command_log(
            task_id=second_task["id"],
            command="Write-Output second",
            cwd=str(ctx["workspace_root"]),
            shell="powershell",
        )
        other_log = store.create_command_log(
            task_id=other_task["id"],
            command="Write-Output other",
            cwd=str(ctx["workspace_root"]),
            shell="powershell",
        )

        get_result = _rpc_call(server, "command_log.get", {"commandId": first_log["id"]})
        assert get_result["commandLog"]["id"] == first_log["id"]
        assert get_result["commandLog"]["status"] == "completed"

        by_task = _rpc_call(server, "command_log.list", {"taskId": second_task["id"]})
        assert [log["id"] for log in by_task["commandLogs"]] == [second_log["id"]]

        by_session = _rpc_call(server, "command_log.list", {"sessionId": task["sessionId"]})
        assert {log["id"] for log in by_session["commandLogs"]} == {first_log["id"], second_log["id"]}
        assert other_log["id"] not in {log["id"] for log in by_session["commandLogs"]}

        by_status = _rpc_call(server, "command_log.list", {"status": "completed"})
        assert [log["id"] for log in by_status["commandLogs"]] == [first_log["id"]]

        limited = _rpc_call(server, "command_log.list", {"limit": 1})
        assert len(limited["commandLogs"]) == 1
    finally:
        store.close()


def test_run_command_background_emits_realtime_runtime_events(tmp_path: Path) -> None:
    store, _server, run_command, ctx, events = _make_runtime(tmp_path)
    try:
        result = run_command(
            {
                "workspaceRoot": str(ctx["workspace_root"]),
                "taskId": ctx["task_id"],
                "command": 'Write-Output "alpha"; Start-Sleep -Milliseconds 500; Write-Output "omega"',
                "background": True,
                "internalValidation": True,
            }
        )

        command_id = result["commandLog"]["id"]
        started_event = _wait_for_event(
            events,
            lambda event: event["type"] == "command.started" and event["payload"]["commandId"] == command_id,
        )
        assert started_event["taskId"] == ctx["task_id"]

        output_event = _wait_for_event(
            events,
            lambda event: (
                event["type"] == "command.output"
                and event["payload"]["commandId"] == command_id
                and event["payload"]["stream"] == "stdout"
                and "alpha" in event["payload"]["chunk"]
            ),
        )
        assert output_event["taskId"] == ctx["task_id"]
        assert store.get_command_log({"commandId": command_id})["commandLog"]["status"] == "running"

        completed_event = _wait_for_event(
            events,
            lambda event: (
                event["type"] in {"command.completed", "command.failed"}
                and event["payload"]["commandId"] == command_id
            ),
        )
        assert completed_event["payload"]["status"] == "completed"
    finally:
        store.close()


def test_command_cancel_stops_only_requested_background_command(tmp_path: Path) -> None:
    store, server, run_command, ctx, events = _make_runtime(tmp_path)
    try:
        first = run_command(
            {
                "workspaceRoot": str(ctx["workspace_root"]),
                "taskId": ctx["task_id"],
                "command": 'Write-Output "first-begin"; Start-Sleep -Seconds 15; Write-Output "first-never"',
                "background": True,
                "internalValidation": True,
            }
        )
        second = run_command(
            {
                "workspaceRoot": str(ctx["workspace_root"]),
                "taskId": ctx["task_id"],
                "command": 'Write-Output "second-begin"; Start-Sleep -Milliseconds 300; Write-Output "second-done"',
                "background": True,
                "internalValidation": True,
            }
        )
        first_command_id = first["commandLog"]["id"]
        second_command_id = second["commandLog"]["id"]

        _wait_for_event(
            events,
            lambda event: (
                event["type"] == "command.output"
                and event["payload"]["commandId"] == first_command_id
                and "first-begin" in event["payload"]["chunk"]
            ),
        )
        _wait_for_event(
            events,
            lambda event: (
                event["type"] == "command.output"
                and event["payload"]["commandId"] == second_command_id
                and "second-begin" in event["payload"]["chunk"]
            ),
        )

        cancel_result = _rpc_call(server, "command.cancel", {"commandId": first_command_id})
        assert cancel_result["cancelled"] is True
        assert cancel_result["commandLog"]["id"] == first_command_id
        assert cancel_result["commandLog"]["status"] == "cancelled"

        first_log = _wait_for_command_log(store, first_command_id)
        second_log = _wait_for_command_log(store, second_command_id)

        assert first_log["status"] == "cancelled"
        assert second_log["status"] == "completed"
        terminal_event = _wait_for_event(
            events,
            lambda event: (
                event["type"] == "command.failed"
                and event["payload"]["commandId"] == first_command_id
                and event["payload"]["status"] == "cancelled"
            ),
        )
        assert terminal_event["taskId"] == ctx["task_id"]
    finally:
        store.close()


def test_task_cancel_stops_running_background_command(tmp_path: Path) -> None:
    store, server, run_command, ctx, events = _make_runtime(tmp_path)
    try:
        result = run_command(
            {
                "workspaceRoot": str(ctx["workspace_root"]),
                "taskId": ctx["task_id"],
                "command": 'Write-Output "begin"; Start-Sleep -Seconds 15; Write-Output "never"',
                "background": True,
                "internalValidation": True,
            }
        )
        command_id = result["commandLog"]["id"]

        _wait_for_event(
            events,
            lambda event: (
                event["type"] == "command.output"
                and event["payload"]["commandId"] == command_id
                and "begin" in event["payload"]["chunk"]
            ),
        )

        response = server.handle_line(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": "cancel_1",
                    "method": "task.cancel",
                    "params": {"taskId": ctx["task_id"]},
                },
                ensure_ascii=False,
            )
        )
        assert response["result"]["task"]["status"] == "cancelled"

        command_log = _wait_for_command_log(store, command_id)
        assert command_log["status"] == "cancelled"
        assert command_log["finishedAt"] is not None
        terminal_event = _wait_for_event(
            events,
            lambda event: (
                event["type"] == "command.failed"
                and event["payload"]["commandId"] == command_id
                and event["payload"]["status"] == "cancelled"
            ),
        )
        assert terminal_event["taskId"] == ctx["task_id"]
        trace_events = store.list_trace_events({"taskId": ctx["task_id"]})["traceEvents"]
        assert any(
            event["type"] == "command.failed"
            and event["payload"]["commandId"] == command_id
            and event["payload"]["status"] == "cancelled"
            for event in trace_events
        )
    finally:
        store.close()
