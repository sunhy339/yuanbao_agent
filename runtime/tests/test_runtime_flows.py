from __future__ import annotations

from pathlib import Path
from typing import Any

import subprocess


def _event_types(events: list[dict[str, Any]]) -> list[str]:
    return [event["type"] for event in events]


def _call_result(response: dict[str, Any], key: str) -> dict[str, Any]:
    assert "result" in response, response
    return response["result"][key]


def test_workspace_session_message_tool_flow(runtime_harness: Any, tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "alpha.txt").write_text("needle in a haystack\n", encoding="utf-8")
    (workspace_root / "notes.md").write_text("plain notes\n", encoding="utf-8")

    workspace = _call_result(
        runtime_harness.call("workspace.open", {"path": str(workspace_root)}),
        "workspace",
    )
    session = _call_result(
        runtime_harness.call(
            "session.create",
            {"workspaceId": workspace["id"], "title": "Search the workspace"},
        ),
        "session",
    )

    task = _call_result(
        runtime_harness.call(
            "message.send",
            {"sessionId": session["id"], "content": "needle"},
        ),
        "task",
    )

    assert task["status"] == "completed"
    event_types = _event_types(runtime_harness.events)
    assert event_types[0] == "task.started"
    assert event_types[1] == "assistant.token"
    assert event_types[-1] == "task.completed"
    assert event_types.count("tool.started") == 3
    assert event_types.count("tool.completed") == 3
    assert [event["payload"]["toolName"] for event in runtime_harness.events if event["type"] == "tool.started"] == [
        "list_dir",
        "search_files",
        "read_file",
    ]

    task_from_store = _call_result(
        runtime_harness.call("task.get", {"taskId": task["id"]}),
        "task",
    )
    assert task_from_store["status"] == "completed"
    assert task_from_store["id"] == task["id"]
    assert task_from_store["plan"][-1]["status"] == "completed"


def test_run_command_approval_closure(runtime_harness: Any, monkeypatch: Any, tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    workspace = _call_result(
        runtime_harness.call("workspace.open", {"path": str(workspace_root)}),
        "workspace",
    )
    session = _call_result(
        runtime_harness.call(
            "session.create",
            {"workspaceId": workspace["id"], "title": "Approval flow"},
        ),
        "session",
    )

    def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        assert command[0] == "powershell.exe"
        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout="command ok\n",
            stderr="",
        )

    monkeypatch.setattr("local_agent_runtime.tools.builtin.subprocess.run", fake_run)

    send_response = runtime_harness.call(
        "message.send",
        {"sessionId": session["id"], "content": "run command: Write-Output approval-flow"},
    )
    task = _call_result(send_response, "task")
    assert task["status"] == "waiting_approval"

    approval_requested = next(event for event in runtime_harness.events if event["type"] == "approval.requested")
    approval = approval_requested["payload"]["approvalId"]
    runtime_harness.call(
        "approval.submit",
        {"approvalId": approval, "decision": "approved"},
    )

    command_output_events = [event for event in runtime_harness.events if event["type"] == "command.output"]
    assert command_output_events, runtime_harness.events
    assert command_output_events[-1]["payload"]["chunk"] == "command ok\n"

    completed_event = next(event for event in runtime_harness.events if event["type"] == "task.completed")
    assert completed_event["payload"]["status"] == "completed"
    assert completed_event["payload"]["detail"].startswith("Approved command finished")

    final_task = _call_result(
        runtime_harness.call("task.get", {"taskId": task["id"]}),
        "task",
    )
    assert final_task["status"] == "completed"
    assert final_task["resultSummary"].startswith("Approved command finished")


def test_search_config_is_applied(runtime_harness: Any, monkeypatch: Any, tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "keep.py").write_text("needle\n", encoding="utf-8")
    (workspace_root / "ignored.py").write_text("needle\n", encoding="utf-8")
    (workspace_root / "readme.md").write_text("needle\n", encoding="utf-8")

    monkeypatch.setattr("local_agent_runtime.tools.builtin.shutil.which", lambda _name: None)

    workspace = _call_result(
        runtime_harness.call("workspace.open", {"path": str(workspace_root)}),
        "workspace",
    )
    session = _call_result(
        runtime_harness.call(
            "session.create",
            {"workspaceId": workspace["id"], "title": "Search config"},
        ),
        "session",
    )

    runtime_harness.call(
        "config.update",
        {
            "config": {
                "search": {
                    "glob": ["**/*.py"],
                    "ignore": ["ignored.py"],
                }
            }
        },
    )

    task = _call_result(
        runtime_harness.call(
            "message.send",
            {"sessionId": session["id"], "content": "needle"},
        ),
        "task",
    )
    assert task["status"] == "completed"

    search_event = next(event for event in runtime_harness.events if event["type"] == "tool.started" and event["payload"]["toolName"] == "search_files")
    assert search_event["payload"]["arguments"]["glob"] == ["**/*.py"]
    assert "ignored.py" in search_event["payload"]["arguments"]["ignore"]
    assert ".git" in search_event["payload"]["arguments"]["ignore"]

    search_completed = next(event for event in runtime_harness.events if event["type"] == "tool.completed" and event["payload"]["toolName"] == "search_files")
    matches = search_completed["payload"]["result"]["matches"]
    assert [match["path"] for match in matches] == ["keep.py"]
