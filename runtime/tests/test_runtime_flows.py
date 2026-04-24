from __future__ import annotations

from pathlib import Path
from typing import Any

import subprocess
import pytest


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


def test_provider_test_reports_mock_and_missing_env(runtime_harness: Any) -> None:
    mocked = runtime_harness.call("provider.test", {"provider": {"mode": "mock"}})["result"]
    assert mocked["ok"] is True
    assert mocked["status"] == "mocked"

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


def test_config_get_normalizes_legacy_provider_into_active_profile(runtime_harness: Any) -> None:
    config = runtime_harness.call("config.get", {})["result"]["config"]
    provider = config["provider"]

    assert provider["activeProfileId"]
    assert provider["profiles"]
    active_profile = next(item for item in provider["profiles"] if item["id"] == provider["activeProfileId"])
    assert active_profile["name"]
    assert active_profile["mode"] == provider["mode"]
    assert active_profile["baseUrl"] == provider["baseUrl"]
    assert active_profile["model"] == provider["model"]
    assert active_profile["apiKeyEnvVarName"] == provider["apiKeyEnvVarName"]
    assert "apiKey" not in active_profile


def test_provider_test_uses_profile_id_and_redacts_direct_api_key(runtime_harness: Any) -> None:
    runtime_harness.call(
        "config.update",
        {
            "config": {
                "provider": {
                    "activeProfileId": "primary",
                    "profiles": [
                        {
                            "id": "primary",
                            "name": "Primary mock",
                            "mode": "mock",
                            "baseUrl": "https://primary.example.test/v1",
                            "model": "primary-chat",
                            "apiKeyEnvVarName": "PRIMARY_KEY",
                        },
                        {
                            "id": "remote",
                            "name": "Remote",
                            "mode": "not-supported",
                            "baseUrl": "https://remote.example.test/v1",
                            "model": "remote-chat",
                            "apiKey": "sk-secret-profile",
                        },
                    ],
                }
            }
        },
    )

    result = runtime_harness.call("provider.test", {"profileId": "remote"})["result"]

    assert result["profileId"] == "remote"
    assert result["profileName"] == "Remote"
    assert result["status"] == "unsupported"
    assert result["baseUrl"] == "https://remote.example.test/v1"
    assert result["model"] == "remote-chat"
    assert "sk-secret-profile" not in str(result)


def test_provider_test_patch_reaches_adapter_without_active_profile_override(runtime_harness: Any) -> None:
    class CapturingProvider:
        def __init__(self) -> None:
            self.contexts: list[dict[str, Any]] = []

        def chat(self, *, messages: list[dict[str, Any]], tools: Any = None, context: dict[str, Any] | None = None) -> dict[str, Any]:
            self.contexts.append(context or {})
            return {
                "message": {"role": "assistant", "content": "provider ok", "tool_calls": []},
                "finish_reason": "stop",
                "raw": {"model": "patched-chat", "usage": {"total_tokens": 1}},
            }

    runtime_harness.call(
        "config.update",
        {
            "config": {
                "provider": {
                    "activeProfileId": "default",
                    "profiles": [
                        {
                            "id": "default",
                            "name": "Default mock",
                            "mode": "mock",
                            "baseUrl": "https://default.example.test/v1",
                            "model": "default-chat",
                        }
                    ],
                }
            }
        },
    )
    provider = CapturingProvider()
    runtime_harness.server._orchestrator._provider = provider

    result = runtime_harness.call(
        "provider.test",
        {
            "provider": {
                "mode": "openai-compatible",
                "baseUrl": "https://patched.example.test/v1",
                "model": "patched-chat",
                "apiKey": "sk-patched",
            }
        },
    )["result"]

    assert result["ok"] is True
    assert result["status"] == "ok"
    provider_config = provider.contexts[0]["config"]["provider"]
    assert provider_config["mode"] == "openai-compatible"
    assert provider_config["model"] == "patched-chat"
    assert provider_config["baseUrl"] == "https://patched.example.test/v1"
    assert "profiles" not in provider_config
    assert "sk-patched" not in str(result)


def test_provider_test_persists_profile_health_metadata(runtime_harness: Any) -> None:
    runtime_harness.call(
        "config.update",
        {
            "config": {
                "provider": {
                    "activeProfileId": "remote",
                    "profiles": [
                        {
                            "id": "default",
                            "name": "Default",
                            "mode": "mock",
                            "baseUrl": "https://default.example.test/v1",
                            "model": "default-chat",
                        },
                        {
                            "id": "remote",
                            "name": "Remote",
                            "mode": "openai-compatible",
                            "baseUrl": "https://remote.example.test/v1",
                            "model": "remote-chat",
                            "apiKeyEnvVarName": "YUANBAO_TEST_REMOTE_KEY",
                        },
                    ],
                }
            }
        },
    )

    before = runtime_harness.call("config.get", {})["result"]["config"]["provider"]
    remote_before = next(item for item in before["profiles"] if item["id"] == "remote")
    assert "lastCheckedAt" not in remote_before

    result = runtime_harness.call("provider.test", {"profileId": "remote"})["result"]

    assert result["ok"] is False
    assert result["status"] == "missing_env"

    provider = runtime_harness.call("config.get", {})["result"]["config"]["provider"]
    remote = next(item for item in provider["profiles"] if item["id"] == "remote")
    assert isinstance(remote["lastCheckedAt"], int)
    assert remote["lastCheckedAt"] > 0
    assert remote["lastStatus"] == "missing_env"
    assert remote["lastErrorSummary"] == "Set YUANBAO_TEST_REMOTE_KEY in the runtime environment."


def test_deleting_active_or_last_profile_has_reasonable_fallback(runtime_harness: Any) -> None:
    runtime_harness.call(
        "config.update",
        {
            "config": {
                "provider": {
                    "activeProfileId": "primary",
                    "profiles": [
                        {
                            "id": "primary",
                            "name": "Primary",
                            "mode": "mock",
                            "baseUrl": "https://primary.example.test/v1",
                            "model": "primary-chat",
                        },
                        {
                            "id": "secondary",
                            "name": "Secondary",
                            "mode": "openai-compatible",
                            "baseUrl": "https://secondary.example.test/v1",
                            "model": "secondary-chat",
                            "apiKeyEnvVarName": "SECONDARY_KEY",
                        },
                    ],
                }
            }
        },
    )

    updated = runtime_harness.call(
        "config.update",
        {
            "config": {
                "provider": {
                    "activeProfileId": "primary",
                    "profiles": [
                        {
                            "id": "secondary",
                            "name": "Secondary",
                            "mode": "openai-compatible",
                            "baseUrl": "https://secondary.example.test/v1",
                            "model": "secondary-chat",
                            "apiKeyEnvVarName": "SECONDARY_KEY",
                        }
                    ],
                }
            }
        },
    )["result"]["config"]["provider"]

    assert updated["activeProfileId"] == "secondary"
    assert [profile["id"] for profile in updated["profiles"]] == ["secondary"]
    assert updated["mode"] == "openai-compatible"
    assert updated["baseUrl"] == "https://secondary.example.test/v1"
    assert updated["model"] == "secondary-chat"

    fallback = runtime_harness.call(
        "config.update",
        {
            "config": {
                "provider": {
                    "profiles": [],
                }
            }
        },
    )["result"]["config"]["provider"]

    assert fallback["activeProfileId"] == "default"
    assert len(fallback["profiles"]) == 1
    assert fallback["profiles"][0]["id"] == "default"
    assert fallback["profiles"][0]["name"] == "Default"
    assert fallback["profiles"][0]["mode"] == "mock"


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

def test_explicit_apply_patch_routes_to_patch_tool(runtime_harness: Any, tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    workspace = _call_result(
        runtime_harness.call("workspace.open", {"path": str(workspace_root)}),
        "workspace",
    )
    session = _call_result(
        runtime_harness.call(
            "session.create",
            {"workspaceId": workspace["id"], "title": "Patch route"},
        ),
        "session",
    )
    (workspace_root / "README.md").write_text("old line\n", encoding="utf-8")
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

    task = _call_result(
        runtime_harness.call(
            "message.send",
            {"sessionId": session["id"], "content": f"apply patch: {patch_text}"},
        ),
        "task",
    )

    assert task["status"] == "waiting_approval"
    approval_requested = next(event for event in runtime_harness.events if event["type"] == "approval.requested")
    approval_id = approval_requested["payload"]["approvalId"]
    runtime_harness.call(
        "approval.submit",
        {"approvalId": approval_id, "decision": "approved"},
    )

    final_task = _call_result(
        runtime_harness.call("task.get", {"taskId": task["id"]}),
        "task",
    )
    assert final_task["status"] == "completed"
    started_tools = [event["payload"]["toolName"] for event in runtime_harness.events if event["type"] == "tool.started"]
    assert started_tools[0] == "list_dir"
    assert started_tools.count("apply_patch") == 2
    assert final_task["plan"][1]["id"] == "apply-patch"
    assert final_task["plan"][1]["status"] == "completed"


def test_explicit_apply_patch_rejects_invalid_patch_before_approval(runtime_harness: Any, tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    workspace = _call_result(
        runtime_harness.call("workspace.open", {"path": str(workspace_root)}),
        "workspace",
    )
    session = _call_result(
        runtime_harness.call(
            "session.create",
            {"workspaceId": workspace["id"], "title": "Invalid patch route"},
        ),
        "session",
    )
    (workspace_root / "README.md").write_text("old line\n", encoding="utf-8")
    patch_text = "\n".join(
        [
            "diff --git a/README.md b/README.md",
            "--- a/README.md",
            "+++ b/README.md",
            "@@ -1 +1 @@",
            "-missing line",
            "+new line",
        ]
    )

    task = _call_result(
        runtime_harness.call(
            "message.send",
            {"sessionId": session["id"], "content": f"apply patch: {patch_text}"},
        ),
        "task",
    )

    assert task["status"] == "failed"
    assert task["errorCode"] == "LOOP_EXECUTION_FAILED"
    assert "Patch removal mismatch in README.md" in task["resultSummary"]
    assert not [event for event in runtime_harness.events if event["type"] == "approval.requested"]
    assert (workspace_root / "README.md").read_text(encoding="utf-8") == "old line\n"


@pytest.mark.parametrize(
    ("content", "tool_name", "plan_step_id"),
    [
        ("show git status", "git_status", "git-status"),
        ("show git diff", "git_diff", "git-diff"),
    ],
)
def test_explicit_git_routes_use_read_only_tools(
    runtime_harness: Any,
    tmp_path: Path,
    content: str,
    tool_name: str,
    plan_step_id: str,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    subprocess.run(["git", "init"], cwd=workspace_root, check=True, capture_output=True, text=True)
    workspace = _call_result(
        runtime_harness.call("workspace.open", {"path": str(workspace_root)}),
        "workspace",
    )
    session = _call_result(
        runtime_harness.call(
            "session.create",
            {"workspaceId": workspace["id"], "title": "Git route"},
        ),
        "session",
    )

    task = _call_result(
        runtime_harness.call(
            "message.send",
            {"sessionId": session["id"], "content": content},
        ),
        "task",
    )

    assert task["status"] == "completed"
    assert [event["payload"]["toolName"] for event in runtime_harness.events if event["type"] == "tool.started"] == [
        "list_dir",
        tool_name,
    ]
    assert task["plan"][1]["id"] == plan_step_id
    assert task["plan"][1]["status"] == "completed"


def test_failed_tool_surfaces_clear_task_summary(runtime_harness: Any, monkeypatch: Any, tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    workspace = _call_result(
        runtime_harness.call("workspace.open", {"path": str(workspace_root)}),
        "workspace",
    )
    session = _call_result(
        runtime_harness.call(
            "session.create",
            {"workspaceId": workspace["id"], "title": "Failure summary"},
        ),
        "session",
    )

    def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        assert command[0] == "powershell.exe"
        return subprocess.CompletedProcess(
            args=command,
            returncode=1,
            stdout="",
            stderr="boom\n",
        )

    monkeypatch.setattr("local_agent_runtime.tools.builtin.subprocess.run", fake_run)

    send_response = runtime_harness.call(
        "message.send",
        {"sessionId": session["id"], "content": "run command: Write-Output failure-case"},
    )
    task = _call_result(send_response, "task")
    assert task["status"] == "waiting_approval"

    approval_requested = next(event for event in runtime_harness.events if event["type"] == "approval.requested")
    approval = approval_requested["payload"]["approvalId"]
    runtime_harness.call(
        "approval.submit",
        {"approvalId": approval, "decision": "approved"},
    )

    failed_event = next(event for event in runtime_harness.events if event["type"] == "task.failed")
    assert "Command failed with status failed" in failed_event["payload"]["detail"]

    final_task = _call_result(
        runtime_harness.call("task.get", {"taskId": task["id"]}),
        "task",
    )
    assert final_task["status"] == "failed"
    assert "Command failed with status failed" in final_task["resultSummary"]
