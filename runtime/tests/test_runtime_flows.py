from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import os
import subprocess
import time
import pytest

from local_agent_runtime.policy.guard import PolicyGuard
from local_agent_runtime.router import ExecutionStrategy, RoutingDecision, Scenario
from local_agent_runtime.tools import build_builtin_tools


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
    assert "message.created" in event_types
    assert "task.started" in event_types
    assert "task.created" in event_types
    assert "task.routing.decided" in event_types
    assert "assistant.token" in event_types
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


def test_message_list_returns_persisted_conversation(runtime_harness: Any, tmp_path: Path) -> None:
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

    runtime_harness.call(
        "message.send",
        {"sessionId": session["id"], "content": "remember this"},
    )
    runtime_harness.call(
        "message.send",
        {"sessionId": other_session["id"], "content": "do not include this"},
    )

    messages = runtime_harness.call(
        "message.list",
        {"sessionId": session["id"]},
    )["result"]["messages"]

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

    anthropic_missing_env = runtime_harness.call(
        "provider.test",
        {
            "provider": {
                "mode": "anthropic",
                "apiKeyEnvVarName": "YUANBAO_TEST_MISSING_ANTHROPIC_KEY",
                "model": "claude-test",
            }
        },
    )["result"]
    assert anthropic_missing_env["ok"] is False
    assert anthropic_missing_env["status"] == "missing_env"
    assert anthropic_missing_env["apiFormat"] == "anthropic-messages"
    assert anthropic_missing_env["baseUrl"] == "https://api.anthropic.com"
    assert anthropic_missing_env["requestPath"] == "/v1/messages"
    assert anthropic_missing_env["checkedEnvVarName"] == "YUANBAO_TEST_MISSING_ANTHROPIC_KEY"
    assert anthropic_missing_env["failureReason"] == "missing_env"


def test_message_send_fails_when_openai_provider_key_is_missing(
    runtime_harness: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("YUANBAO_TEST_MISSING_KEY", raising=False)
    monkeypatch.delenv("LOCAL_AGENT_PROVIDER_API_KEY", raising=False)
    monkeypatch.delenv("LOCAL_AGENT_OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    workspace = _call_result(
        runtime_harness.call("workspace.open", {"path": str(workspace_root)}),
        "workspace",
    )
    session = _call_result(
        runtime_harness.call(
            "session.create",
            {"workspaceId": workspace["id"], "title": "Greeting"},
        ),
        "session",
    )
    runtime_harness.call(
        "config.update",
        {
            "config": {
                "provider": {
                    "mode": "openai-compatible",
                    "baseUrl": "https://api.example.test/v1",
                    "model": "real-model",
                    "apiKeyEnvVarName": "YUANBAO_TEST_MISSING_KEY",
                }
            }
        },
    )

    task = _call_result(
        runtime_harness.call(
            "message.send",
            {"sessionId": session["id"], "content": "你好"},
        ),
        "task",
    )

    assert task["status"] == "failed"
    assert "YUANBAO_TEST_MISSING_KEY" in task["resultSummary"]
    assert "Completed an initial pass" not in task["resultSummary"]
    assert [event for event in runtime_harness.events if event["type"] == "message.failed"]
    messages = runtime_harness.call(
        "message.list",
        {"sessionId": session["id"]},
    )["result"]["messages"]
    assert [message["role"] for message in messages] == ["user", "assistant"]
    assert "YUANBAO_TEST_MISSING_KEY" in messages[1]["content"]


def test_background_message_send_returns_before_agent_loop_completes(
    runtime_harness: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from local_agent_runtime.provider.adapter import ProviderAdapter

    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    workspace = _call_result(
        runtime_harness.call("workspace.open", {"path": str(workspace_root)}),
        "workspace",
    )
    session = _call_result(
        runtime_harness.call(
            "session.create",
            {"workspaceId": workspace["id"], "title": "Slow task"},
        ),
        "session",
    )

    def slow_generate(self: ProviderAdapter, prompt: str, context: dict[str, Any]) -> dict[str, Any]:
        time.sleep(0.5)
        return {
            "final": "Slow answer completed.",
            "prompt": prompt,
            "context": context,
        }

    monkeypatch.setattr(ProviderAdapter, "generate", slow_generate)

    started_at = time.monotonic()
    task = _call_result(
        runtime_harness.call(
            "message.send",
            {"sessionId": session["id"], "content": "slow answer", "background": True},
        ),
        "task",
    )

    assert time.monotonic() - started_at < 3.0
    assert task["status"] == "running"

    deadline = time.monotonic() + 2
    completed_task = task
    while time.monotonic() < deadline:
        completed_task = _call_result(runtime_harness.call("task.get", {"taskId": task["id"]}), "task")
        if completed_task["status"] == "completed":
            break
        time.sleep(0.05)

    assert completed_task["status"] == "completed"
    assert completed_task["resultSummary"] == "Slow answer completed."


def test_background_message_send_persists_user_before_completion(
    runtime_harness: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from local_agent_runtime.provider.adapter import ProviderAdapter

    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    workspace = _call_result(
        runtime_harness.call("workspace.open", {"path": str(workspace_root)}),
        "workspace",
    )
    session = _call_result(
        runtime_harness.call(
            "session.create",
            {"workspaceId": workspace["id"], "title": "Background history"},
        ),
        "session",
    )

    def slow_generate(self: ProviderAdapter, prompt: str, context: dict[str, Any]) -> dict[str, Any]:
        time.sleep(0.5)
        return {"final": "Background answer completed.", "prompt": prompt, "context": context}

    monkeypatch.setattr(ProviderAdapter, "generate", slow_generate)

    task = _call_result(
        runtime_harness.call(
            "message.send",
            {"sessionId": session["id"], "content": "persist immediately", "background": True},
        ),
        "task",
    )
    assert task["status"] == "running"

    immediate_messages = runtime_harness.call(
        "message.list",
        {"sessionId": session["id"]},
    )["result"]["messages"]
    assert [(message["role"], message["content"]) for message in immediate_messages] == [
        ("user", "persist immediately"),
        ("assistant", ""),
    ]

    messages = immediate_messages
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        messages = runtime_harness.call(
            "message.list",
            {"sessionId": session["id"]},
        )["result"]["messages"]
        if (
            [message["role"] for message in messages] == ["user", "assistant"]
            and messages[1]["content"]
        ):
            break
        time.sleep(0.05)

    assert [message["role"] for message in messages] == ["user", "assistant"]
    assert messages[1]["content"] == "Background answer completed."


def test_background_message_send_returns_before_context_build_completes(
    runtime_harness: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from local_agent_runtime.context.builder import ContextBuilder

    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    workspace = _call_result(
        runtime_harness.call("workspace.open", {"path": str(workspace_root)}),
        "workspace",
    )
    session = _call_result(
        runtime_harness.call(
            "session.create",
            {"workspaceId": workspace["id"], "title": "Slow context"},
        ),
        "session",
    )
    original_build = ContextBuilder.build

    def slow_build(self: ContextBuilder, session_id: str, goal: str, **kwargs: Any) -> dict[str, object]:
        time.sleep(0.5)
        return original_build(self, session_id, goal, **kwargs)

    monkeypatch.setattr(ContextBuilder, "build", slow_build)

    started_at = time.monotonic()
    task = _call_result(
        runtime_harness.call(
            "message.send",
            {"sessionId": session["id"], "content": "slow context", "background": True},
        ),
        "task",
    )

    assert time.monotonic() - started_at < 3.0
    assert task["status"] == "running"


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

    monkeypatch.setattr("local_agent_runtime.tools._shared.subprocess.run", fake_run)

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


def test_apply_patch_files_can_create_new_file_after_approval(runtime_harness: Any, tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    workspace = runtime_harness.store.upsert_workspace(str(workspace_root))
    session = runtime_harness.store.create_session(workspace_id=workspace["id"], title="Create file")
    task = runtime_harness.store.create_task(session_id=session["id"], task_type="chat", goal="create file", plan=[])
    tools = build_builtin_tools(policy_guard=PolicyGuard(), store=runtime_harness.store)
    files = [{"path": "hello_world.py", "content": "print('Hello, World!')\n"}]

    proposed = tools["apply_patch"](
        {
            "workspaceRoot": str(workspace_root),
            "taskId": task["id"],
            "files": files,
        }
    )

    assert proposed["status"] == "approval_required"
    approval_id = proposed["approval"]["id"]
    runtime_harness.store.resolve_approval(approval_id, "approved")

    applied = tools["apply_patch"](
        {
            "workspaceRoot": str(workspace_root),
            "taskId": task["id"],
            "approvalId": approval_id,
            "files": files,
        }
    )

    assert applied["status"] == "applied"
    assert (workspace_root / "hello_world.py").read_text(encoding="utf-8") == "print('Hello, World!')\n"


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

    monkeypatch.setattr("local_agent_runtime.tools._shared.subprocess.run", fake_run)

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

    messages = runtime_harness.call(
        "message.list",
        {"sessionId": session["id"]},
    )["result"]["messages"]
    assert [message["role"] for message in messages] == ["user", "assistant"]
    assert "Command failed with status failed" in messages[1]["content"]


def test_plan_approval_strict_mode(runtime_harness: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """In strict approval mode, plan decomposition requires user approval before DAG execution."""
    from local_agent_runtime.planner.types import PlanResult, Subtask

    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "app.py").write_text("print('hello')\n", encoding="utf-8")

    # Set approval mode to strict
    config = runtime_harness.store.get_config({})["config"]
    config["policy"]["approvalMode"] = "strict"
    runtime_harness.store.update_config({"config": config})

    # Patch decomposer to return a fixed plan (mock provider can't handle planning)
    fake_plan = PlanResult(
        subtasks=[
            Subtask(id="sub-0", title="Analyze", description="Analyze code"),
            Subtask(id="sub-1", title="Refactor", description="Refactor code", dependencies=["sub-0"]),
        ],
        dag={"sub-0": [], "sub-1": ["sub-0"]},
        execution_order=["sub-0", "sub-1"],
    )
    monkeypatch.setattr(
        runtime_harness.server._orchestrator._decomposer,
        "decompose",
        lambda **_kwargs: fake_plan,
    )

    workspace = _call_result(
        runtime_harness.call("workspace.open", {"path": str(workspace_root)}),
        "workspace",
    )
    session = _call_result(
        runtime_harness.call(
            "session.create",
            {"workspaceId": workspace["id"], "title": "Plan approval test"},
        ),
        "session",
    )

    # "重构" triggers MULTI_STEP_TASK → enable_planning=True → _execute_with_planning
    send_response = runtime_harness.call(
        "message.send",
        {"sessionId": session["id"], "content": "重构 app.py 的代码结构"},
    )
    task = _call_result(send_response, "task")

    # Task should be waiting for plan approval
    assert task["status"] == "waiting_approval"

    # Find the plan approval event
    approval_events = [
        event for event in runtime_harness.events
        if event["type"] == "approval.requested" and event["payload"].get("kind") == "plan"
    ]
    assert len(approval_events) == 1
    approval_id = approval_events[0]["payload"]["approvalId"]

    # Verify the approval request contains plan details
    request_payload = approval_events[0]["payload"]["request"]
    assert "subtaskCount" in request_payload
    assert request_payload["subtaskCount"] == 2

    # Reject the plan
    runtime_harness.call(
        "approval.submit",
        {"approvalId": approval_id, "decision": "rejected"},
    )

    final_task = _call_result(
        runtime_harness.call("task.get", {"taskId": task["id"]}),
        "task",
    )
    assert final_task["status"] == "failed"


def test_plan_approval_approved_resumes_dag(runtime_harness: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When a plan is approved in strict mode, DAG execution proceeds normally."""
    from local_agent_runtime.planner.types import PlanResult, Subtask

    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "app.py").write_text("print('hello')\n", encoding="utf-8")

    # Set approval mode to strict
    config = runtime_harness.store.get_config({})["config"]
    config["policy"]["approvalMode"] = "strict"
    runtime_harness.store.update_config({"config": config})

    # Patch decomposer to return a fixed plan
    fake_plan = PlanResult(
        subtasks=[
            Subtask(id="sub-0", title="Analyze", description="Analyze code"),
            Subtask(id="sub-1", title="Refactor", description="Refactor code", dependencies=["sub-0"]),
        ],
        dag={"sub-0": [], "sub-1": ["sub-0"]},
        execution_order=["sub-0", "sub-1"],
    )
    monkeypatch.setattr(
        runtime_harness.server._orchestrator._decomposer,
        "decompose",
        lambda **_kwargs: fake_plan,
    )

    # Patch DAG executor to return success immediately (mock the full subagent pipeline)
    def fake_execute(*_args, **_kwargs):
        return {
            "success": True,
            "completed": ["sub-0", "sub-1"],
            "failed": [],
            "results": {"sub-0": "Analyzed", "sub-1": "Refactored"},
            "subtasks": [
                Subtask(id="sub-0", title="Analyze", description="Analyze code", status="completed", result="Analyzed"),
                Subtask(id="sub-1", title="Refactor", description="Refactor code", status="completed", result="Refactored"),
            ],
            "summary": "Plan executed successfully",
        }
    monkeypatch.setattr(
        runtime_harness.server._orchestrator._dag_executor,
        "execute",
        fake_execute,
    )

    workspace = _call_result(
        runtime_harness.call("workspace.open", {"path": str(workspace_root)}),
        "workspace",
    )
    session = _call_result(
        runtime_harness.call(
            "session.create",
            {"workspaceId": workspace["id"], "title": "Plan approval approve"},
        ),
        "session",
    )

    send_response = runtime_harness.call(
        "message.send",
        {"sessionId": session["id"], "content": "重构 app.py 的代码结构"},
    )
    task = _call_result(send_response, "task")
    assert task["status"] == "waiting_approval"

    approval_events = [
        event for event in runtime_harness.events
        if event["type"] == "approval.requested" and event["payload"].get("kind") == "plan"
    ]
    assert len(approval_events) == 1
    approval_id = approval_events[0]["payload"]["approvalId"]

    # Approve the plan
    runtime_harness.call(
        "approval.submit",
        {"approvalId": approval_id, "decision": "approved"},
    )

    # After approval, task should complete since DAG executor is mocked
    final_task = _call_result(
        runtime_harness.call("task.get", {"taskId": task["id"]}),
        "task",
    )
    assert final_task["status"] == "completed"


def test_plan_execute_recovers_failed_verification_subtask_with_parent_check(
    runtime_harness: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from local_agent_runtime.planner.types import PlanResult, Subtask

    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "pyproject.toml").write_text(
        "[tool.pytest.ini_options]\npythonpath = [\".\"]\n",
        encoding="utf-8",
    )
    tests_dir = workspace_root / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_recovered.py").write_text(
        "def test_recovered():\n    assert True\n",
        encoding="utf-8",
    )

    fake_plan = PlanResult(
        subtasks=[
            Subtask(id="sub-0", title="Implement backend", description="Implement backend"),
            Subtask(id="sub-1", title="Run tests", description="Run tests", dependencies=["sub-0"]),
        ],
        dag={"sub-0": [], "sub-1": ["sub-0"]},
        execution_order=["sub-0", "sub-1"],
    )
    monkeypatch.setattr(
        runtime_harness.server._orchestrator._decomposer,
        "decompose",
        lambda **_kwargs: fake_plan,
    )
    monkeypatch.setattr(
        runtime_harness.server._orchestrator,
        "_route_goal",
        lambda _goal: RoutingDecision(
            scenario=Scenario.MULTI_STEP_TASK,
            strategy=ExecutionStrategy.PLAN_THEN_EXECUTE,
            confidence=0.99,
            enable_planning=True,
            enable_reflection=True,
            reasoning="forced plan_execute for test",
        ),
    )

    def fake_execute(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        failed = Subtask(
            id="sub-1",
            title="Run tests",
            description="Run tests",
            status="failed",
            result=(
                "Completion blocked because verification failed. "
                "Partial handoff: pendingVerification=python -m pytest -q"
            ),
        )
        completed = Subtask(
            id="sub-0",
            title="Implement backend",
            description="Implement backend",
            status="completed",
            result="Implemented backend files.",
        )
        return {
            "success": False,
            "completed": ["sub-0"],
            "failed": ["sub-1"],
            "results": {"sub-0": "Implemented backend files.", "sub-1": "Failed verification"},
            "subtasks": [completed, failed],
            "summary": "Plan execution completed (1/2 succeeded)",
            "partialHandoffs": [
                {
                    "subtaskId": "sub-1",
                    "subtaskTitle": "Run tests",
                    "status": "CHILD_TASK_EXECUTION_FAILED",
                    "pendingVerification": ["python -m pytest -q"],
                }
            ],
        }

    monkeypatch.setattr(
        runtime_harness.server._orchestrator._dag_executor,
        "execute",
        fake_execute,
    )

    workspace = _call_result(
        runtime_harness.call("workspace.open", {"path": str(workspace_root)}),
        "workspace",
    )
    session = _call_result(
        runtime_harness.call(
            "session.create",
            {"workspaceId": workspace["id"], "title": "Planning recovery"},
        ),
        "session",
    )

    task = _call_result(
        runtime_harness.call(
            "message.send",
            {"sessionId": session["id"], "content": "build and test a full project"},
        ),
        "task",
    )

    assert task["status"] == "completed"
    assert "Planning recovery" in task["resultSummary"]
    evidence = task["structuredResult"]["completionEvidence"]
    assert evidence["evidenceLevel"] == "verified"
    assert evidence["counts"]["passedVerification"] >= 1

    recovered_events = [
        event for event in runtime_harness.events
        if event["type"] == "task.planning.recovered"
    ]
    assert recovered_events
    command_logs = runtime_harness.call(
        "command_log.list",
        {"sessionId": session["id"], "limit": 20},
    )["result"]["commandLogs"]
    assert any(command["command"] == "python -m pytest -q" and command["status"] == "completed" for command in command_logs)


def test_planning_recovery_normalizes_node_command(
    runtime_harness: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if os.name != "nt":
        pytest.skip("Node command rewriting is only needed for PowerShell recovery on Windows")

    from local_agent_runtime.orchestrator import message_execution

    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    node = tmp_path / "node" / "bin" / "node.exe"
    node.parent.mkdir(parents=True)
    node.write_text("", encoding="utf-8")
    monkeypatch.setenv("LOCAL_AGENT_NODE_EXECUTABLE", str(node))

    workspace = runtime_harness.store.upsert_workspace(str(workspace_root))
    session = runtime_harness.store.create_session(workspace_id=workspace["id"], title="node recovery")
    task = runtime_harness.store.create_task(session_id=session["id"], task_type="chat", goal="recover node check", plan=[])

    captured: dict[str, Any] = {}

    def fake_run_shell_command(
        shell_name: str,
        command: str,
        cwd: Path,
        timeout_ms: int,
    ) -> tuple[str, str, int, str, int]:
        captured.update({
            "shell": shell_name,
            "command": command,
            "cwd": cwd,
            "timeoutMs": timeout_ms,
        })
        return "", "", 0, "completed", 1

    monkeypatch.setattr(message_execution, "run_shell_command", fake_run_shell_command)

    result = runtime_harness.server._orchestrator._run_planning_recovery_command(
        session_id=session["id"],
        task=task,
        workspace=workspace_root,
        command="node --check app.js",
    )

    expected = f'& "{node}" --check app.js'
    assert result["command"] == expected
    assert captured["command"] == expected
    command_logs = runtime_harness.call(
        "command_log.list",
        {"sessionId": session["id"], "limit": 20},
    )["result"]["commandLogs"]
    assert any(command["command"] == expected and command["status"] == "completed" for command in command_logs)


def test_plan_execute_repairs_failed_verification_subtask_before_recovery(
    runtime_harness: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from local_agent_runtime.planner.types import PlanResult, Subtask

    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "pyproject.toml").write_text(
        "[tool.pytest.ini_options]\npythonpath = [\".\"]\n",
        encoding="utf-8",
    )
    tests_dir = workspace_root / "tests"
    tests_dir.mkdir()
    test_file = tests_dir / "test_repaired.py"
    test_file.write_text(
        "def test_repaired():\n    assert False\n",
        encoding="utf-8",
    )

    fake_plan = PlanResult(
        subtasks=[
            Subtask(id="sub-0", title="Implement backend", description="Implement backend"),
            Subtask(id="sub-1", title="Write tests", description="Write tests", dependencies=["sub-0"]),
            Subtask(id="sub-2", title="Document and summarize", description="Document and summarize", dependencies=["sub-1"]),
        ],
        dag={"sub-0": [], "sub-1": ["sub-0"], "sub-2": ["sub-1"]},
        execution_order=["sub-0", "sub-1", "sub-2"],
    )
    monkeypatch.setattr(
        runtime_harness.server._orchestrator._decomposer,
        "decompose",
        lambda **_kwargs: fake_plan,
    )
    monkeypatch.setattr(
        runtime_harness.server._orchestrator,
        "_route_goal",
        lambda _goal: RoutingDecision(
            scenario=Scenario.MULTI_STEP_TASK,
            strategy=ExecutionStrategy.PLAN_THEN_EXECUTE,
            confidence=0.99,
            enable_planning=True,
            enable_reflection=True,
            reasoning="forced plan_execute for repair recovery test",
        ),
    )

    def fake_execute(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        completed = Subtask(
            id="sub-0",
            title="Implement backend",
            description="Implement backend",
            status="completed",
            result="Implemented backend files.",
        )
        failed = Subtask(
            id="sub-1",
            title="Write tests",
            description="Write tests",
            status="failed",
            result=(
                "Completion blocked because verification failed. "
                "Partial handoff: changedFiles=tests/test_repaired.py | "
                "pendingVerification=python -m pytest -q"
            ),
        )
        skipped = Subtask(
            id="sub-2",
            title="Document and summarize",
            description="Document and summarize",
            status="skipped",
            result="Skipped: dependency failed",
        )
        return {
            "success": False,
            "completed": ["sub-0"],
            "failed": ["sub-1", "sub-2"],
            "results": {
                "sub-0": "Implemented backend files.",
                "sub-1": "Failed verification",
                "sub-2": "Skipped: dependency failed",
            },
            "subtasks": [completed, failed, skipped],
            "summary": "Plan execution completed (1/3 succeeded)",
            "partialHandoffs": [
                {
                    "subtaskId": "sub-1",
                    "subtaskTitle": "Write tests",
                    "status": "CHILD_TASK_EXECUTION_FAILED",
                    "changedFiles": ["tests/test_repaired.py"],
                    "commands": [{"command": "python -m pytest -q", "status": "failed"}],
                    "pendingVerification": ["python -m pytest -q"],
                    "nextAction": "Repair failed verification and rerun pytest.",
                }
            ],
        }

    monkeypatch.setattr(
        runtime_harness.server._orchestrator._dag_executor,
        "execute",
        fake_execute,
    )

    original_dispatch = runtime_harness.server._orchestrator._subagent_service.dispatch
    repair_calls: list[dict[str, Any]] = []

    def dispatch_with_repair(params: dict[str, Any]) -> dict[str, Any]:
        if params.get("title") == "Repair failed planning verification":
            repair_calls.append(params)
            test_file.write_text(
                "def test_repaired():\n    assert True\n",
                encoding="utf-8",
            )
            return {"status": "completed", "summary": "Repaired failing verification and finished skipped docs."}
        return original_dispatch(params)

    monkeypatch.setattr(
        runtime_harness.server._orchestrator._subagent_service,
        "dispatch",
        dispatch_with_repair,
    )

    workspace = _call_result(
        runtime_harness.call("workspace.open", {"path": str(workspace_root)}),
        "workspace",
    )
    session = _call_result(
        runtime_harness.call(
            "session.create",
            {"workspaceId": workspace["id"], "title": "Planning repair recovery"},
        ),
        "session",
    )

    task = _call_result(
        runtime_harness.call(
            "message.send",
            {"sessionId": session["id"], "content": "build and test a full project"},
        ),
        "task",
    )

    assert repair_calls
    assert "Failed verification commands and observed output" in repair_calls[0]["prompt"]
    assert task["status"] == "completed"
    assert "Planning recovery" in task["resultSummary"]
    evidence = task["structuredResult"]["completionEvidence"]
    assert evidence["evidenceLevel"] == "verified"
    assert evidence["counts"]["passedVerification"] >= 1
    recovered_events = [
        event for event in runtime_harness.events
        if event["type"] == "task.planning.recovered"
    ]
    assert recovered_events
    command_logs = runtime_harness.call(
        "command_log.list",
        {"sessionId": session["id"], "limit": 20},
    )["result"]["commandLogs"]
    assert any(command["command"] == "python -m pytest -q" and command["status"] == "failed" for command in command_logs)
    assert any(command["command"] == "python -m pytest -q" and command["status"] == "completed" for command in command_logs)


def test_plan_execute_recovers_failed_subtask_from_root_completion_evidence(
    runtime_harness: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from local_agent_runtime.planner.types import PlanResult, Subtask

    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "incident_models.py").write_text("VALUE = 1\n", encoding="utf-8")
    tests_dir = workspace_root / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_incident_core.py").write_text(
        "def test_incident_core():\n    assert True\n",
        encoding="utf-8",
    )

    fake_plan = PlanResult(
        subtasks=[
            Subtask(id="sub-0", title="Implement backend", description="Implement backend"),
            Subtask(id="sub-1", title="Write pytest coverage", description="Write pytest coverage", dependencies=["sub-0"]),
        ],
        dag={"sub-0": [], "sub-1": ["sub-0"]},
        execution_order=["sub-0", "sub-1"],
    )
    monkeypatch.setattr(
        runtime_harness.server._orchestrator._decomposer,
        "decompose",
        lambda **_kwargs: fake_plan,
    )
    monkeypatch.setattr(
        runtime_harness.server._orchestrator,
        "_route_goal",
        lambda _goal: RoutingDecision(
            scenario=Scenario.MULTI_STEP_TASK,
            strategy=ExecutionStrategy.PLAN_THEN_EXECUTE,
            confidence=0.99,
            enable_planning=True,
            enable_reflection=True,
            reasoning="forced plan_execute for test",
        ),
    )

    def fake_execute(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        completed = Subtask(
            id="sub-0",
            title="Implement backend",
            description="Implement backend",
            status="completed",
            result="Implemented backend files.",
        )
        failed = Subtask(
            id="sub-1",
            title="Write pytest coverage",
            description="Write pytest coverage",
            status="failed",
            result="Completion blocked because verification failed. Fix the failed checks before marking the task completed.",
        )
        return {
            "success": False,
            "completed": ["sub-0"],
            "failed": ["sub-1"],
            "results": {"sub-0": "Implemented backend files.", "sub-1": "Failed verification"},
            "subtasks": [completed, failed],
            "summary": "Plan execution completed (1/2 succeeded)",
            "partialHandoffs": [],
        }

    monkeypatch.setattr(
        runtime_harness.server._orchestrator._dag_executor,
        "execute",
        fake_execute,
    )

    workspace = _call_result(
        runtime_harness.call("workspace.open", {"path": str(workspace_root)}),
        "workspace",
    )
    session = _call_result(
        runtime_harness.call(
            "session.create",
            {"workspaceId": workspace["id"], "title": "Planning completion recovery"},
        ),
        "session",
    )

    original_complete_task = runtime_harness.server._orchestrator._complete_task

    def instrumented_complete_task(*args: Any, **kwargs: Any) -> dict[str, Any]:
        task_arg = kwargs.get("task")
        if task_arg is None and len(args) >= 2:
            task_arg = args[1]
        if isinstance(task_arg, dict) and task_arg.get("goal") == "build and test a full project":
            runtime_harness.store.update_task(
                task_id=task_arg["id"],
                changed_files=[
                    {"path": "incident_models.py", "summary": "implemented incident models"},
                    {"path": "tests/test_incident_core.py", "summary": "added pytest coverage"},
                ],
            )
            command_log = runtime_harness.store.create_command_log(
                task_id=task_arg["id"],
                command=r"C:\Python314\python.exe -m pytest -q",
                cwd=str(workspace_root),
                shell="powershell",
            )
            runtime_harness.store.update_command_log(
                command_log["id"],
                status="completed",
                exit_code=0,
                stdout_path="",
                stderr_path="",
            )
        return original_complete_task(*args, **kwargs)

    monkeypatch.setattr(
        runtime_harness.server._orchestrator,
        "_complete_task",
        instrumented_complete_task,
    )

    task = _call_result(
        runtime_harness.call(
            "message.send",
            {"sessionId": session["id"], "content": "build and test a full project"},
        ),
        "task",
    )

    assert task["status"] == "completed"
    evidence = task["structuredResult"]["completionEvidence"]
    assert evidence["evidenceLevel"] == "verified"
    assert evidence["counts"]["failedToolResults"] == 0
    assert evidence["counts"]["resolvedFailedToolResults"] >= 1
    recovered_events = [
        event for event in runtime_harness.events
        if event["type"] == "task.planning.recovered"
    ]
    assert any(event["payload"].get("mode") == "completion_evidence" for event in recovered_events)


def test_react_loop_provider_empty_final_recovers_from_completion_evidence(
    runtime_harness: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "blog_models.py").write_text("VALUE = 1\n", encoding="utf-8")
    tests_dir = workspace_root / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_blog_api.py").write_text(
        "def test_blog_api():\n    assert True\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        runtime_harness.server._orchestrator,
        "_route_goal",
        lambda _goal: RoutingDecision(
            scenario=Scenario.CODE_EDIT,
            strategy=ExecutionStrategy.REACT_STANDARD,
            confidence=0.99,
            enable_planning=False,
            enable_reflection=False,
            reasoning="forced react_standard for completion recovery test",
        ),
    )
    active_task_id = {"value": None}

    original_create_task = runtime_harness.store.create_task

    def instrumented_create_task(*args: Any, **kwargs: Any) -> dict[str, Any]:
        created = original_create_task(*args, **kwargs)
        if created.get("goal") == "repair blog follow-up":
            active_task_id["value"] = created["id"]
        return created

    monkeypatch.setattr(runtime_harness.store, "create_task", instrumented_create_task)

    def fake_run_react_loop(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        task_id = active_task_id["value"]
        assert task_id
        runtime_harness.store.update_task(
            task_id=task_id,
            changed_files=[
                {"path": "blog_models.py", "summary": "fixed status transition handling"},
                {"path": "tests/test_blog_api.py", "summary": "verified API behavior"},
            ],
        )
        command_log = runtime_harness.store.create_command_log(
            task_id=task_id,
            command=r"C:\Python314\python.exe -m pytest -q",
            cwd=str(workspace_root),
            shell="powershell",
        )
        runtime_harness.store.update_command_log(
            command_log["id"],
            status="completed",
            exit_code=0,
            stdout_path="",
            stderr_path="",
        )
        node_log = runtime_harness.store.create_command_log(
            task_id=task_id,
            command=r"C:\Program Files\nodejs\node.exe --check app.js",
            cwd=str(workspace_root),
            shell="powershell",
        )
        runtime_harness.store.update_command_log(
            node_log["id"],
            status="completed",
            exit_code=0,
            stdout_path="",
            stderr_path="",
        )
        raise RuntimeError("Provider returned no final answer or tool calls.")

    monkeypatch.setattr(
        runtime_harness.server._orchestrator,
        "_run_react_loop",
        fake_run_react_loop,
    )

    workspace = _call_result(
        runtime_harness.call("workspace.open", {"path": str(workspace_root)}),
        "workspace",
    )
    session = _call_result(
        runtime_harness.call(
            "session.create",
            {"workspaceId": workspace["id"], "title": "react completion recovery"},
        ),
        "session",
    )

    task = _call_result(
        runtime_harness.call(
            "message.send",
            {"sessionId": session["id"], "content": "repair blog follow-up"},
        ),
        "task",
    )
    assert task["status"] == "completed"
    evidence = task["structuredResult"]["completionEvidence"]
    assert evidence["evidenceLevel"] == "verified"
    assert evidence["counts"]["failedToolResults"] == 0
    assert evidence["counts"]["passedTestsRun"] >= 1
    assert "Recovered after provider returned no final answer or tool calls." in task["resultSummary"]
    assert any(event["type"] == "task.loop_failure.recovered" for event in runtime_harness.events)


def test_supervisor_plan_approval_reject(runtime_harness: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """In strict mode, supervisor plan requires approval; rejection fails the task."""
    from local_agent_runtime.orchestration.types import OrchestrationResult
    from local_agent_runtime.planner.types import PlanResult, Subtask

    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "app.py").write_text("print('hello')\n", encoding="utf-8")

    config = runtime_harness.store.get_config({})["config"]
    config["policy"]["approvalMode"] = "strict"
    runtime_harness.store.update_config({"config": config})

    fake_plan = PlanResult(
        subtasks=[
            Subtask(id="sub-0", title="Analyze", description="Analyze code"),
            Subtask(id="sub-1", title="Refactor", description="Refactor code", dependencies=["sub-0"]),
        ],
        dag={"sub-0": [], "sub-1": ["sub-0"]},
        execution_order=["sub-0", "sub-1"],
    )
    monkeypatch.setattr(
        runtime_harness.server._orchestrator._decomposer,
        "decompose",
        lambda **_kwargs: fake_plan,
    )

    workspace = _call_result(
        runtime_harness.call("workspace.open", {"path": str(workspace_root)}),
        "workspace",
    )
    session = _call_result(
        runtime_harness.call(
            "session.create",
            {"workspaceId": workspace["id"], "title": "Supervisor plan approval"},
        ),
        "session",
    )

    # "监督" triggers SUPERVISED_TASK → plan_supervise → _execute_with_supervisor
    send_response = runtime_harness.call(
        "message.send",
        {"sessionId": session["id"], "content": "监督执行这个任务的优化"},
    )
    task = _call_result(send_response, "task")
    assert task["status"] == "waiting_approval"

    approval_events = [
        event for event in runtime_harness.events
        if event["type"] == "approval.requested" and event["payload"].get("kind") == "plan"
    ]
    assert len(approval_events) == 1
    assert approval_events[0]["payload"]["request"].get("orchestrationMode") == "supervisor"
    approval_id = approval_events[0]["payload"]["approvalId"]

    runtime_harness.call(
        "approval.submit",
        {"approvalId": approval_id, "decision": "rejected"},
    )

    final_task = _call_result(
        runtime_harness.call("task.get", {"taskId": task["id"]}),
        "task",
    )
    assert final_task["status"] == "failed"


def test_supervisor_plan_approval_approved_resumes(runtime_harness: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When a supervisor plan is approved, execution proceeds normally."""
    from local_agent_runtime.orchestration.types import OrchestrationResult
    from local_agent_runtime.planner.types import PlanResult, Subtask

    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "app.py").write_text("print('hello')\n", encoding="utf-8")

    config = runtime_harness.store.get_config({})["config"]
    config["policy"]["approvalMode"] = "strict"
    runtime_harness.store.update_config({"config": config})

    fake_plan = PlanResult(
        subtasks=[
            Subtask(id="sub-0", title="Analyze", description="Analyze code"),
        ],
        dag={"sub-0": []},
        execution_order=["sub-0"],
    )
    monkeypatch.setattr(
        runtime_harness.server._orchestrator._decomposer,
        "decompose",
        lambda **_kwargs: fake_plan,
    )

    # Mock supervisor.execute to return success
    monkeypatch.setattr(
        runtime_harness.server._orchestrator._supervisor,
        "execute",
        lambda *args, **_kwargs: OrchestrationResult(
            success=True, summary="Supervisor completed", review_count=1, subtask_results=[], paused=False,
        ),
    )

    workspace = _call_result(
        runtime_harness.call("workspace.open", {"path": str(workspace_root)}),
        "workspace",
    )
    session = _call_result(
        runtime_harness.call(
            "session.create",
            {"workspaceId": workspace["id"], "title": "Supervisor plan approve"},
        ),
        "session",
    )

    send_response = runtime_harness.call(
        "message.send",
        {"sessionId": session["id"], "content": "监督执行这个任务"},
    )
    task = _call_result(send_response, "task")
    assert task["status"] == "waiting_approval"

    approval_events = [
        event for event in runtime_harness.events
        if event["type"] == "approval.requested" and event["payload"].get("kind") == "plan"
    ]
    assert len(approval_events) == 1
    approval_id = approval_events[0]["payload"]["approvalId"]

    runtime_harness.call(
        "approval.submit",
        {"approvalId": approval_id, "decision": "approved"},
    )

    final_task = _call_result(
        runtime_harness.call("task.get", {"taskId": task["id"]}),
        "task",
    )
    assert final_task["status"] == "completed"


def test_swarm_plan_approval_reject(runtime_harness: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """In strict mode, swarm plan requires approval; rejection fails the task."""
    from local_agent_runtime.planner.types import PlanResult, Subtask

    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "app.py").write_text("print('hello')\n", encoding="utf-8")

    config = runtime_harness.store.get_config({})["config"]
    config["policy"]["approvalMode"] = "strict"
    runtime_harness.store.update_config({"config": config})

    fake_plan = PlanResult(
        subtasks=[
            Subtask(id="sub-0", title="Analyze", description="Analyze code"),
        ],
        dag={"sub-0": []},
        execution_order=["sub-0"],
    )
    monkeypatch.setattr(
        runtime_harness.server._orchestrator._decomposer,
        "decompose",
        lambda **_kwargs: fake_plan,
    )

    workspace = _call_result(
        runtime_harness.call("workspace.open", {"path": str(workspace_root)}),
        "workspace",
    )
    session = _call_result(
        runtime_harness.call(
            "session.create",
            {"workspaceId": workspace["id"], "title": "Swarm plan approval"},
        ),
        "session",
    )

    # "swarm" triggers SWARM_TASK → plan_swarm → _execute_with_swarm
    send_response = runtime_harness.call(
        "message.send",
        {"sessionId": session["id"], "content": "swarm 协作完成这个任务"},
    )
    task = _call_result(send_response, "task")
    assert task["status"] == "waiting_approval"

    approval_events = [
        event for event in runtime_harness.events
        if event["type"] == "approval.requested" and event["payload"].get("kind") == "plan"
    ]
    assert len(approval_events) == 1
    assert approval_events[0]["payload"]["request"].get("orchestrationMode") == "swarm"
    approval_id = approval_events[0]["payload"]["approvalId"]

    runtime_harness.call(
        "approval.submit",
        {"approvalId": approval_id, "decision": "rejected"},
    )

    final_task = _call_result(
        runtime_harness.call("task.get", {"taskId": task["id"]}),
        "task",
    )
    assert final_task["status"] == "failed"


def test_graceful_shutdown_rejects_new_tasks(runtime_harness: Any, tmp_path: Path) -> None:
    """After graceful_shutdown is called, send_message should reject new tasks."""
    runtime_harness.server.graceful_shutdown(timeout=0.1)

    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    workspace = _call_result(
        runtime_harness.call("workspace.open", {"path": str(workspace_root)}),
        "workspace",
    )
    session = _call_result(
        runtime_harness.call(
            "session.create",
            {"workspaceId": workspace["id"], "title": "Shutdown test"},
        ),
        "session",
    )

    response = runtime_harness.call(
        "message.send",
        {"sessionId": session["id"], "content": "hello"},
    )
    assert "error" in response
    assert "服务正在关闭" in response["error"]["message"]


def test_graceful_shutdown_cancels_running_tasks(runtime_harness: Any, tmp_path: Path) -> None:
    """Running tasks should be cancelled when graceful_shutdown times out."""
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    workspace = _call_result(
        runtime_harness.call("workspace.open", {"path": str(workspace_root)}),
        "workspace",
    )
    session = _call_result(
        runtime_harness.call(
            "session.create",
            {"workspaceId": workspace["id"], "title": "Shutdown cancel"},
        ),
        "session",
    )

    # Create a task (completed by mock provider), then set back to running
    task = _call_result(
        runtime_harness.call(
            "message.send",
            {"sessionId": session["id"], "content": "simple task"},
        ),
        "task",
    )
    runtime_harness.store.update_task(task_id=task["id"], status="running")

    runtime_harness.server.graceful_shutdown(timeout=0.1)

    final_task = _call_result(
        runtime_harness.call("task.get", {"taskId": task["id"]}),
        "task",
    )
    assert final_task["status"] == "cancelled"


def test_skill_usage_is_recorded_when_skill_triggered(runtime_harness: Any, tmp_path: Path) -> None:
    """When a message triggers a skill-based scenario, usage should be recorded."""
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    workspace = _call_result(
        runtime_harness.call("workspace.open", {"path": str(workspace_root)}),
        "workspace",
    )
    session = _call_result(
        runtime_harness.call(
            "session.create",
            {"workspaceId": workspace["id"], "title": "Skill usage test"},
        ),
        "session",
    )

    # "review" keyword triggers CODE_REVIEW scenario with skill_id="code_reviewer"
    task = _call_result(
        runtime_harness.call(
            "message.send",
            {"sessionId": session["id"], "content": "review the code"},
        ),
        "task",
    )

    # Query skill usage
    usage_resp = runtime_harness.call("skill.usage", {"skillId": "code_reviewer"})
    assert "result" in usage_resp
    usage_list = usage_resp["result"]["usage"]
    assert len(usage_list) >= 1
    entry = usage_list[0]
    assert entry["skill_id"] == "code_reviewer"
    assert entry["task_id"] == task["id"]
    assert entry["session_id"] == session["id"]
    assert entry["triggered_by"] == "routing"


def test_skill_usage_empty_for_no_skill(runtime_harness: Any, tmp_path: Path) -> None:
    """When a message doesn't trigger any skill, no usage should be recorded."""
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    workspace = _call_result(
        runtime_harness.call("workspace.open", {"path": str(workspace_root)}),
        "workspace",
    )
    session = _call_result(
        runtime_harness.call(
            "session.create",
            {"workspaceId": workspace["id"], "title": "No skill test"},
        ),
        "session",
    )

    # "hello" won't match any skill-based scenario
    runtime_harness.call(
        "message.send",
        {"sessionId": session["id"], "content": "hello"},
    )

    usage_resp = runtime_harness.call("skill.usage", {})
    assert usage_resp["result"]["total"] == 0


def test_explicit_skill_id_is_recorded_even_when_router_does_not_supply_one(
    runtime_harness: Any,
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    workspace = _call_result(
        runtime_harness.call("workspace.open", {"path": str(workspace_root)}),
        "workspace",
    )
    session = _call_result(
        runtime_harness.call(
            "session.create",
            {"workspaceId": workspace["id"], "title": "Explicit skill test"},
        ),
        "session",
    )

    runtime_harness.call(
        "skill.create",
        {
            "id": "explicit_reader",
            "name": "Explicit Reader",
            "description": "Read with explicit skill selection.",
            "system_prompt": "Use the explicit skill.",
            "tool_whitelist": ["read_file"],
            "parameter_constraints": {},
            "category": "test",
        },
    )

    task = _call_result(
        runtime_harness.call(
            "message.send",
            {
                "sessionId": session["id"],
                "content": "hello",
                "skillId": "explicit_reader",
            },
        ),
        "task",
    )

    assert task["routing"]["skill_id"] == "explicit_reader"
    usage_resp = runtime_harness.call("skill.usage", {"skillId": "explicit_reader"})
    usage_list = usage_resp["result"]["usage"]
    assert len(usage_list) >= 1
    assert usage_list[0]["task_id"] == task["id"]


def test_session_summary_generated_after_task_completion(runtime_harness: Any, tmp_path: Path) -> None:
    """Session summary should be populated via _remember_task_result after completion."""
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    workspace = _call_result(
        runtime_harness.call("workspace.open", {"path": str(workspace_root)}),
        "workspace",
    )
    session = _call_result(
        runtime_harness.call(
            "session.create",
            {"workspaceId": workspace["id"], "title": "Summary test"},
        ),
        "session",
    )

    # Complete a task
    runtime_harness.call(
        "message.send",
        {"sessionId": session["id"], "content": "needle"},
    )

    # Check session summary was updated with Task memory marker
    updated_session = _call_result(
        runtime_harness.call("session.get", {"sessionId": session["id"]}),
        "session",
    )
    assert updated_session.get("summary") is not None
    assert "Task memory:" in updated_session["summary"]


def test_background_task_preserves_routing_fields(
    runtime_harness: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Background and foreground tasks should store identical routing metadata."""
    from local_agent_runtime.provider.adapter import ProviderAdapter

    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    workspace = _call_result(
        runtime_harness.call("workspace.open", {"path": str(workspace_root)}),
        "workspace",
    )
    session = _call_result(
        runtime_harness.call(
            "session.create",
            {"workspaceId": workspace["id"], "title": "Routing alignment"},
        ),
        "session",
    )

    def mock_generate(self: ProviderAdapter, prompt: str, context: dict[str, Any]) -> dict[str, Any]:
        return {"final": "done", "prompt": prompt, "context": context}

    monkeypatch.setattr(ProviderAdapter, "generate", mock_generate)

    # Foreground task
    fg_task = _call_result(
        runtime_harness.call(
            "message.send",
            {"sessionId": session["id"], "content": "foreground routing test"},
        ),
        "task",
    )

    # Background task
    bg_task = _call_result(
        runtime_harness.call(
            "message.send",
            {"sessionId": session["id"], "content": "background routing test", "background": True},
        ),
        "task",
    )

    # Both tasks should have routing stored
    fg_routing = fg_task.get("routing")
    bg_routing = bg_task.get("routing")
    assert fg_routing is not None, "Foreground task missing routing"
    assert bg_routing is not None, "Background task missing routing"

    # Routing keys should be identical
    routing_keys = {"scenario", "strategy", "confidence", "max_steps", "enable_reflection", "enable_planning"}
    assert routing_keys.issubset(set(fg_routing.keys())), f"FG routing missing keys: {routing_keys - set(fg_routing.keys())}"
    assert routing_keys.issubset(set(bg_routing.keys())), f"BG routing missing keys: {routing_keys - set(bg_routing.keys())}"

    # Both should have the same scenario and strategy (same MetaRouter rules)
    assert fg_routing["scenario"] == bg_routing["scenario"]
    assert fg_routing["strategy"] == bg_routing["strategy"]
    assert fg_routing["max_steps"] == bg_routing["max_steps"]
    assert fg_routing["enable_planning"] == bg_routing["enable_planning"]
    assert fg_routing["enable_reflection"] == bg_routing["enable_reflection"]

    for routing in (fg_routing, bg_routing):
        workflow = routing.get("mainWorkflow")
        assert isinstance(workflow, dict)
        assert workflow["intentConfidence"]["band"] in {"low", "medium", "high"}
        assert workflow["automation"]["level"] in {"assist", "auto", "full-auto"}
        assert workflow["automation"]["controls"]["pause"] is True
        assert workflow["automation"]["controls"]["continueAfterBudgetExhaustion"] == "requires_user_action"
        assert workflow["automation"]["convergencePolicy"]["advisorKind"] == "budget_convergence"
        assert workflow["budget"]["maxSteps"] == routing["max_steps"]
        assert workflow["budget"]["childTaskTimeoutMs"] >= 1
        assert workflow["budget"]["commandTimeoutMs"] >= 1
        assert workflow["budget"]["pressure"] in {"normal", "watch", "critical"}
        assert workflow["budget"]["dimensions"]["steps"]["consumed"] >= 0
        assert workflow["convergence"]["state"] in {"active", "budget_pressure"}
        assert workflow["convergence"]["recommendedAction"] in {"continue", "continue_with_focus", "focus_or_wrap_up"}
        assert workflow["convergence"]["resumable"] is True
        assert workflow["workspaceSnapshot"]["workspaceId"] == workspace["id"]
        assert workflow["workspaceSnapshot"]["exists"] is True
        assert workflow["userTakeover"]["state"] == "none"


def test_planning_provider_context_respects_configured_short_timeout(runtime_harness: Any) -> None:
    context = {
        "config": {
            "provider": {
                "timeout": 45,
                "streamTimeout": 90,
                "profiles": [
                    {
                        "id": "primary",
                        "timeout": 30,
                    }
                ],
            }
        }
    }

    planning_context = runtime_harness.server._orchestrator._planning_provider_context(context)  # noqa: SLF001
    provider = planning_context["config"]["provider"]

    assert provider["timeout"] == 45
    assert provider["timeoutSeconds"] == 45
    assert provider["streamTimeout"] == 90
    assert provider["profiles"][0]["timeout"] == 30
    assert provider["profiles"][0]["timeoutSeconds"] == 30


def test_queued_task_records_main_workflow_state(runtime_harness: Any, tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    workspace = _call_result(
        runtime_harness.call("workspace.open", {"path": str(workspace_root)}),
        "workspace",
    )
    session = _call_result(
        runtime_harness.call("session.create", {"workspaceId": workspace["id"], "title": "queued workflow"}),
        "session",
    )
    active = runtime_harness.store.create_task(
        session_id=session["id"],
        task_type="edit",
        goal="active task",
        plan=[],
        status="running",
        routing={"scenario": "code_edit", "strategy": "react_standard"},
    )

    queued = _call_result(
        runtime_harness.call(
            "message.send",
            {"sessionId": session["id"], "content": "queued follow-up update", "mode": "queued"},
        ),
        "task",
    )

    assert active["id"] != queued["id"]
    assert queued["status"] == "queued"
    workflow = queued["routing"]["mainWorkflow"]
    assert workflow["userTakeover"]["mode"] == "queued"
    assert workflow["workspaceSnapshot"]["workspaceId"] == workspace["id"]
    assert workflow["budget"]["maxSteps"] == queued["routing"]["max_steps"]


def test_supplement_records_user_takeover_state(runtime_harness: Any, tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    workspace = _call_result(
        runtime_harness.call("workspace.open", {"path": str(workspace_root)}),
        "workspace",
    )
    session = _call_result(
        runtime_harness.call("session.create", {"workspaceId": workspace["id"], "title": "takeover"}),
        "session",
    )
    active = runtime_harness.store.create_task(
        session_id=session["id"],
        task_type="edit",
        goal="long task",
        plan=[],
        status="running",
        routing={
            "scenario": "code_edit",
            "strategy": "react_standard",
            "mainWorkflow": {"userTakeover": {"state": "none"}},
        },
    )

    result = runtime_harness.call(
        "message.send",
        {"sessionId": session["id"], "content": "不用了我看过了，停止吧"},
    )
    assert result["result"]["acceptedMode"] == "supplement"
    updated = _call_result(
        runtime_harness.call("task.get", {"taskId": active["id"]}),
        "task",
    )

    takeover = updated["routing"]["mainWorkflow"]["userTakeover"]
    assert takeover["state"] == "stop_requested"
    assert takeover["taskStatusAtReceipt"] == "running"
    assert updated["routing"]["mainWorkflow"]["takeoverHistory"][-1]["state"] == "stop_requested"
    assert updated["status"] == "cancelled"
    event_types = _event_types(runtime_harness.events)
    assert "task.user_takeover.received" in event_types
    assert "task.cancelled" in event_types


def test_supplement_pause_takeover_pauses_task(runtime_harness: Any, tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    workspace = _call_result(
        runtime_harness.call("workspace.open", {"path": str(workspace_root)}),
        "workspace",
    )
    session = _call_result(
        runtime_harness.call("session.create", {"workspaceId": workspace["id"], "title": "pause takeover"}),
        "session",
    )
    active = runtime_harness.store.create_task(
        session_id=session["id"],
        task_type="edit",
        goal="long task",
        plan=[],
        status="running",
        routing={
            "scenario": "code_edit",
            "strategy": "react_standard",
            "mainWorkflow": {"userTakeover": {"state": "none"}},
        },
    )

    result = runtime_harness.call(
        "message.send",
        {"sessionId": session["id"], "content": "pause for a moment"},
    )
    assert result["result"]["acceptedMode"] == "supplement"
    updated = _call_result(
        runtime_harness.call("task.get", {"taskId": active["id"]}),
        "task",
    )

    takeover = updated["routing"]["mainWorkflow"]["userTakeover"]
    assert takeover["state"] == "pause_requested"
    assert takeover["taskStatusAtReceipt"] == "running"
    assert updated["status"] == "paused"
    event_types = _event_types(runtime_harness.events)
    assert "task.user_takeover.received" in event_types
    assert "task.paused" in event_types


def test_supplement_continue_takeover_resumes_paused_task(runtime_harness: Any, tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    workspace = _call_result(
        runtime_harness.call("workspace.open", {"path": str(workspace_root)}),
        "workspace",
    )
    session = _call_result(
        runtime_harness.call("session.create", {"workspaceId": workspace["id"], "title": "continue takeover"}),
        "session",
    )
    active = runtime_harness.store.create_task(
        session_id=session["id"],
        task_type="edit",
        goal="long task",
        plan=[],
        status="paused",
        routing={
            "scenario": "code_edit",
            "strategy": "react_standard",
            "mainWorkflow": {"userTakeover": {"state": "pause_requested"}},
        },
    )

    result = runtime_harness.call(
        "message.send",
        {"sessionId": session["id"], "content": "continue now"},
    )
    assert result["result"]["acceptedMode"] == "supplement"
    updated = _call_result(
        runtime_harness.call("task.get", {"taskId": active["id"]}),
        "task",
    )

    takeover = updated["routing"]["mainWorkflow"]["userTakeover"]
    assert takeover["state"] == "continue_requested"
    assert takeover["taskStatusAtReceipt"] == "paused"
    assert updated["status"] == "running"
    event_types = _event_types(runtime_harness.events)
    assert "task.user_takeover.received" in event_types
    assert "task.resumed" in event_types


# ── Observability tests ──────────────────────────────────────────────────


def test_supplement_wrap_up_takeover_records_resumable_convergence(runtime_harness: Any, tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    workspace = _call_result(
        runtime_harness.call("workspace.open", {"path": str(workspace_root)}),
        "workspace",
    )
    session = _call_result(
        runtime_harness.call("session.create", {"workspaceId": workspace["id"], "title": "wrap takeover"}),
        "session",
    )
    active = runtime_harness.store.create_task(
        session_id=session["id"],
        task_type="edit",
        goal="long task",
        plan=[],
        status="running",
        routing={
            "scenario": "code_edit",
            "strategy": "react_standard",
            "mainWorkflow": {"userTakeover": {"state": "none"}},
        },
    )

    result = runtime_harness.call(
        "message.send",
        {"sessionId": session["id"], "content": "wrap up with the current results"},
    )
    assert result["result"]["acceptedMode"] == "supplement"
    updated = _call_result(
        runtime_harness.call("task.get", {"taskId": active["id"]}),
        "task",
    )

    workflow = updated["routing"]["mainWorkflow"]
    assert workflow["userTakeover"]["state"] == "wrap_up_requested"
    assert workflow["convergence"]["state"] == "wrap_up_requested"
    assert workflow["convergence"]["resumable"] is True
    assert updated["status"] == "running"
    event_types = _event_types(runtime_harness.events)
    assert "task.user_takeover.received" in event_types
    assert "task.user_takeover.convergence" in event_types


def test_supplement_change_takeover_uses_advisor_and_records_proposal(runtime_harness: Any, tmp_path: Path) -> None:
    class Advisor:
        def advise(self, kind: str, input_context: dict[str, Any]) -> Any:
            assert kind == "user_takeover"
            assert input_context["task_status"] == "running"
            return SimpleNamespace(
                accepted=True,
                source="llm",
                rationale="The user is changing the target of the active task.",
                fallback_reason=None,
                proposal_id="takeover_1",
                model_id="test-model",
                validation_reasons=[],
                confidence=0.91,
                payload={
                    "state": "change_requested",
                    "intent": "redirect active work",
                    "target_goal": "Implement the API contract first.",
                    "handoff_focus": "Preserve current UI notes for later.",
                },
            )

    runtime_harness.server._orchestrator._decision_advisor = Advisor()
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    workspace = _call_result(
        runtime_harness.call("workspace.open", {"path": str(workspace_root)}),
        "workspace",
    )
    session = _call_result(
        runtime_harness.call("session.create", {"workspaceId": workspace["id"], "title": "change takeover"}),
        "session",
    )
    active = runtime_harness.store.create_task(
        session_id=session["id"],
        task_type="edit",
        goal="long task",
        plan=[],
        status="running",
        routing={
            "scenario": "code_edit",
            "strategy": "react_standard",
            "mainWorkflow": {"userTakeover": {"state": "none"}},
        },
    )

    result = runtime_harness.call(
        "message.send",
        {"sessionId": session["id"], "content": "actually change to the API contract first"},
    )
    assert result["result"]["acceptedMode"] == "supplement"
    updated = _call_result(
        runtime_harness.call("task.get", {"taskId": active["id"]}),
        "task",
    )

    takeover = updated["routing"]["mainWorkflow"]["userTakeover"]
    assert takeover["state"] == "change_requested"
    assert takeover["source"] == "llm"
    assert takeover["targetGoal"] == "Implement the API contract first."
    convergence = updated["routing"]["mainWorkflow"]["convergence"]
    assert convergence["state"] == "change_requested"
    assert convergence["targetGoal"] == "Implement the API contract first."
    proposals = runtime_harness.store.list_proposals({
        "taskId": active["id"],
        "kind": "user_takeover",
    })["proposals"]
    assert len(proposals) == 1
    assert proposals[0]["status"] == "accepted"
    assert proposals[0]["proposal"]["state"] == "change_requested"
    assert takeover["proposalRecordId"] == proposals[0]["id"]
    event_types = _event_types(runtime_harness.events)
    assert "task.user_takeover.convergence" in event_types


def test_routing_emits_decided_event(runtime_harness: Any, tmp_path: Path) -> None:
    """task.routing.decided event should contain routing metadata and latency."""
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    workspace = _call_result(
        runtime_harness.call("workspace.open", {"path": str(workspace_root)}),
        "workspace",
    )
    session = _call_result(
        runtime_harness.call("session.create", {"workspaceId": workspace["id"], "title": "routing obs"}),
        "session",
    )
    _call_result(
        runtime_harness.call("message.send", {"sessionId": session["id"], "content": "read file.txt"}),
        "task",
    )

    routing_events = [e for e in runtime_harness.events if e["type"] == "task.routing.decided"]
    assert len(routing_events) >= 1, f"No task.routing.decided events; got {_event_types(runtime_harness.events)}"
    payload = routing_events[0]["payload"]
    assert "scenario" in payload
    assert "strategy" in payload
    assert "confidence" in payload
    assert "latency_ms" in payload
    assert isinstance(payload["latency_ms"], int)
    assert payload["latency_ms"] >= 0


def test_routing_creates_trace_span(runtime_harness: Any, tmp_path: Path) -> None:
    """Routing decision should produce a routing_decision trace span."""
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    workspace = _call_result(
        runtime_harness.call("workspace.open", {"path": str(workspace_root)}),
        "workspace",
    )
    session = _call_result(
        runtime_harness.call("session.create", {"workspaceId": workspace["id"], "title": "routing trace"}),
        "session",
    )
    task = _call_result(
        runtime_harness.call("message.send", {"sessionId": session["id"], "content": "list files"}),
        "task",
    )

    # Use the event's routing info to verify the span was created
    routing_events = [e for e in runtime_harness.events if e["type"] == "task.routing.decided"]
    assert len(routing_events) >= 1
    payload = routing_events[0]["payload"]
    # The routing event payload should contain all expected fields
    assert payload["scenario"] in (
        "simple_query", "code_search", "code_edit", "code_review", "debug",
        "test_write", "doc_write", "multi_step_task", "supervised_task",
        "swarm_task", "free_form",
    )
    assert payload["strategy"] in (
        "react_fast", "react_standard", "react_reflect", "skill_based",
        "plan_execute", "plan_supervise", "plan_swarm",
    )
    assert isinstance(payload["confidence"], (int, float))
    assert isinstance(payload["latency_ms"], int)


def test_mcp_server_list_includes_connection_status(runtime_harness: Any) -> None:
    """mcp.server.list should include connected and toolCount fields."""
    result = runtime_harness.call("mcp.server.list", {})
    servers = result["result"]["servers"]
    # Even with no MCP servers configured, the response shape should be correct
    assert isinstance(servers, list)
    for server in servers:
        assert "connected" in server
        assert "toolCount" in server
        assert isinstance(server["connected"], bool)
        assert isinstance(server["toolCount"], int)


def test_streaming_emits_message_delta_with_message_id(runtime_harness: Any, tmp_path: Path) -> None:
    """Streaming token events should emit message.delta with messageId alongside legacy assistant.token."""
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    workspace = _call_result(
        runtime_harness.call("workspace.open", {"path": str(workspace_root)}),
        "workspace",
    )
    session = _call_result(
        runtime_harness.call(
            "session.create",
            {"workspaceId": workspace["id"], "title": "Delta event test"},
        ),
        "session",
    )

    send_response = runtime_harness.call(
        "message.send",
        {"sessionId": session["id"], "content": "hello"},
    )
    task = _call_result(send_response, "task")

    # Collect message.delta and assistant.token events
    delta_events = [e for e in runtime_harness.events if e["type"] == "message.delta"]
    token_events = [e for e in runtime_harness.events if e["type"] == "assistant.token"]

    # Both event types should have been emitted (legacy compat)
    assert len(token_events) > 0, "Expected at least one assistant.token event"
    assert len(delta_events) > 0, "Expected at least one message.delta event"
    assert {event["visibility"] for event in delta_events} == {"chat"}
    assert {event["visibility"] for event in token_events} == {"chat"}

    # message.delta events should carry messageId matching the task's active assistant message
    active_msg_id = task.get("activeAssistantMessageId")
    if active_msg_id:
        for delta_event in delta_events:
            assert delta_event["payload"]["messageId"] == active_msg_id

    # assistant.token should also now include messageId
    if active_msg_id:
        for token_event in token_events:
            assert token_event["payload"]["messageId"] == active_msg_id


def test_provider_http_400_produces_persistent_failure_message(
    runtime_harness: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When provider returns HTTP 400, a persistent failure message should be created."""
    from local_agent_runtime.provider.openai_compatible import ProviderAdapterError

    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    workspace = _call_result(
        runtime_harness.call("workspace.open", {"path": str(workspace_root)}),
        "workspace",
    )
    session = _call_result(
        runtime_harness.call(
            "session.create",
            {"workspaceId": workspace["id"], "title": "HTTP 400 test"},
        ),
        "session",
    )

    def _raise_400(*args: Any, **kwargs: Any) -> None:
        raise ProviderAdapterError("Provider returned error: HTTP 400 Bad Request - invalid model")

    monkeypatch.setattr(runtime_harness.server._orchestrator._provider, "generate", _raise_400)

    send_response = runtime_harness.call(
        "message.send",
        {"sessionId": session["id"], "content": "hello"},
    )
    task = _call_result(send_response, "task")

    assert task["status"] == "failed"
    assert "HTTP 400" in task["resultSummary"]

    # Verify failure message is persisted
    messages = runtime_harness.call(
        "message.list",
        {"sessionId": session["id"]},
    )["result"]["messages"]
    assert [message["role"] for message in messages] == ["user", "assistant"]
    assert "HTTP 400" in messages[1]["content"]
    assert messages[1]["status"] == "failed"

    # Verify failure events were emitted
    assert [e for e in runtime_harness.events if e["type"] == "message.failed"]
    assert [e for e in runtime_harness.events if e["type"] == "task.failed"]


def test_provider_timeout_produces_persistent_failure_message(
    runtime_harness: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When provider times out, a persistent failure message should be created."""
    from local_agent_runtime.provider.openai_compatible import ProviderAdapterError

    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    workspace = _call_result(
        runtime_harness.call("workspace.open", {"path": str(workspace_root)}),
        "workspace",
    )
    session = _call_result(
        runtime_harness.call(
            "session.create",
            {"workspaceId": workspace["id"], "title": "Timeout test"},
        ),
        "session",
    )

    def _raise_timeout(*args: Any, **kwargs: Any) -> None:
        raise ProviderAdapterError("Provider request timed out after 30.0s")

    monkeypatch.setattr(runtime_harness.server._orchestrator._provider, "generate", _raise_timeout)

    send_response = runtime_harness.call(
        "message.send",
        {"sessionId": session["id"], "content": "hello"},
    )
    task = _call_result(send_response, "task")

    assert task["status"] == "failed"
    assert "timed out" in task["resultSummary"].lower()

    # Verify failure message is persisted
    messages = runtime_harness.call(
        "message.list",
        {"sessionId": session["id"]},
    )["result"]["messages"]
    assert [message["role"] for message in messages] == ["user", "assistant"]
    assert "timed out" in messages[1]["content"].lower()
    assert messages[1]["status"] == "failed"

    # Verify failure events were emitted
    assert [e for e in runtime_harness.events if e["type"] == "message.failed"]
    assert [e for e in runtime_harness.events if e["type"] == "task.failed"]
