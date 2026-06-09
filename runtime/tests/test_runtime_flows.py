from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from local_agent_runtime.provider.adapter import ProviderAdapter


def _call_result(response: dict[str, Any], key: str) -> dict[str, Any]:
    assert "result" in response, response
    return response["result"][key]


def _event_types(events: list[dict[str, Any]]) -> list[str]:
    return [event["type"] for event in events]


def _script_provider_responses(monkeypatch: pytest.MonkeyPatch, responses: list[dict[str, Any]]) -> None:
    queued = list(responses)

    def generate(self: ProviderAdapter, prompt: str, context: dict[str, Any]) -> dict[str, Any]:
        if not queued:
            raise AssertionError("Provider called more times than scripted")
        return queued.pop(0)

    monkeypatch.setattr(ProviderAdapter, "generate", generate)


def _open_session(runtime_harness: Any, tmp_path: Path, *, title: str = "Flow") -> dict[str, Any]:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(exist_ok=True)
    workspace = _call_result(
        runtime_harness.call("workspace.open", {"path": str(workspace_root)}),
        "workspace",
    )
    return _call_result(
        runtime_harness.call(
            "session.create",
            {"workspaceId": workspace["id"], "title": title},
        ),
        "session",
    )


def test_simple_message_flow_is_model_first_without_workspace_probe(
    runtime_harness: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _script_provider_responses(monkeypatch, [{"final": "Hello. I can help."}])
    session = _open_session(runtime_harness, tmp_path, title="Simple")

    task = _call_result(
        runtime_harness.call(
            "message.send",
            {"sessionId": session["id"], "content": "hello"},
        ),
        "task",
    )

    event_types = _event_types(runtime_harness.events)
    assert task["status"] == "completed"
    assert task["plan"] == []
    assert "task.routing.decided" not in event_types
    assert "tool.started" not in event_types
    assert "tool.completed" not in event_types
    assert "message.created" in event_types
    assert "message.delta" in event_types
    assert event_types[-1] == "task.completed"


def test_message_list_returns_persisted_conversation(
    runtime_harness: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _script_provider_responses(
        monkeypatch,
        [
            {"final": "I will remember this."},
            {"final": "Other session response."},
        ],
    )
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    workspace = _call_result(
        runtime_harness.call("workspace.open", {"path": str(workspace_root)}),
        "workspace",
    )
    session = _call_result(
        runtime_harness.call(
            "session.create",
            {"workspaceId": workspace["id"], "title": "Persisted conversation"},
        ),
        "session",
    )
    other_session = _call_result(
        runtime_harness.call(
            "session.create",
            {"workspaceId": workspace["id"], "title": "Other conversation"},
        ),
        "session",
    )

    runtime_harness.call("message.send", {"sessionId": session["id"], "content": "remember this"})
    runtime_harness.call("message.send", {"sessionId": other_session["id"], "content": "do not include this"})

    messages = runtime_harness.call("message.list", {"sessionId": session["id"]})["result"]["messages"]

    assert [message["role"] for message in messages] == ["user", "assistant"]
    assert messages[0]["sessionId"] == session["id"]
    assert messages[0]["content"] == "remember this"
    assert messages[1]["sessionId"] == session["id"]
    assert messages[1]["content"]
    assert "do not include this" not in [message["content"] for message in messages]
    assert messages[0]["createdAt"] <= messages[1]["createdAt"]


def test_provider_test_reports_mock_and_missing_env(runtime_harness: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("YUANBAO_TEST_MISSING_ANTHROPIC_KEY", raising=False)
    mocked = runtime_harness.call("provider.test", {"provider": {"mode": "mock"}})["result"]
    assert mocked["ok"] is True
    assert mocked["status"] == "mocked"
    assert mocked["apiFormat"] == "openai-chat"
    assert mocked["requestPath"] == "/v1/chat/completions"

    missing_env = runtime_harness.call(
        "provider.test",
        {
            "provider": {
                "mode": "openai-compatible",
                "apiKeyEnvVarName": "YUANBAO_TEST_MISSING_KEY",
                "model": "test-chat",
            }
        },
    )["result"]
    assert missing_env["ok"] is False
    assert missing_env["status"] == "missing_env"
    assert missing_env["checkedEnvVarName"] == "YUANBAO_TEST_MISSING_KEY"
    assert missing_env["apiFormat"] == "openai-chat"
    assert missing_env["requestPath"] == "/v1/chat/completions"
    assert missing_env["failureReason"] == "missing_env"


def test_search_config_is_applied(
    runtime_harness: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "keep.py").write_text("needle\n", encoding="utf-8")
    (workspace_root / "ignored.py").write_text("needle\n", encoding="utf-8")
    (workspace_root / "readme.md").write_text("needle\n", encoding="utf-8")
    monkeypatch.setattr("local_agent_runtime.tools._shared.shutil.which", lambda _name: None)

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
        {"config": {"search": {"glob": ["**/*.py"], "ignore": ["ignored.py"]}}},
    )
    _script_provider_responses(
        monkeypatch,
        [
            {
                "message": "Searching for the requested text.",
                "tool_calls": [
                    {
                        "id": "call_search",
                        "name": "search_files",
                        "arguments": {"query": "needle"},
                    }
                ],
            },
            {"final": "Search complete."},
        ],
    )

    task = _call_result(
        runtime_harness.call("message.send", {"sessionId": session["id"], "content": "needle"}),
        "task",
    )

    assert task["status"] == "completed"
    search_started = next(
        event
        for event in runtime_harness.events
        if event["type"] == "tool.started" and event["payload"]["toolName"] == "search_files"
    )
    assert search_started["payload"]["arguments"]["glob"] == ["**/*.py"]
    assert "ignored.py" in search_started["payload"]["arguments"]["ignore"]
    assert ".git" in search_started["payload"]["arguments"]["ignore"]
    search_completed = next(
        event
        for event in runtime_harness.events
        if event["type"] == "tool.completed" and event["payload"]["toolName"] == "search_files"
    )
    assert [match["path"] for match in search_completed["payload"]["result"]["matches"]] == ["keep.py"]


def test_run_command_approval_resumes_same_task_once(
    runtime_harness: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    session = _open_session(runtime_harness, tmp_path, title="Approval flow")

    def fake_run_shell_command(
        shell_name: str,
        command: str,
        cwd: Path,
        timeout_ms: int,
        *,
        stdout_callback: Any | None = None,
        stderr_callback: Any | None = None,
    ) -> tuple[str, str, int, str, int]:
        assert shell_name == "powershell"
        assert "approval-flow" in command
        if stdout_callback is not None:
            stdout_callback("command ok\n")
        return "command ok\n", "", 0, "completed", 1

    monkeypatch.setattr("local_agent_runtime.tools._shared.run_shell_command", fake_run_shell_command)
    _script_provider_responses(
        monkeypatch,
        [
            {
                "message": "I will run the requested command.",
                "tool_calls": [
                    {
                        "id": "call_command",
                        "name": "run_command",
                        "arguments": {"command": "Write-Output approval-flow"},
                    }
                ],
            },
            {"final": "Approved command finished."},
        ],
    )

    task = _call_result(
        runtime_harness.call(
            "message.send",
            {"sessionId": session["id"], "content": "Please run the approval-flow command."},
        ),
        "task",
    )
    assert task["status"] == "waiting_approval"

    approval_requested = next(event for event in runtime_harness.events if event["type"] == "approval.requested")
    approval_id = approval_requested["payload"]["approvalId"]
    runtime_harness.call("approval.submit", {"approvalId": approval_id, "decision": "approved"})
    runtime_harness.call("approval.submit", {"approvalId": approval_id, "decision": "approved"})

    final_task = _call_result(runtime_harness.call("task.get", {"taskId": task["id"]}), "task")
    assert final_task["status"] == "completed"
    assert final_task["resultSummary"].startswith("Approved command finished")
    assert len([event for event in runtime_harness.events if event["type"] == "command.started"]) == 1
    assert len([event for event in runtime_harness.events if event["type"] == "task.completed"]) == 1

