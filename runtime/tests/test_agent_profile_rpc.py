from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

from local_agent_runtime.event_bus import EventBus
from local_agent_runtime.orchestrator.service import Orchestrator
from local_agent_runtime.rpc.server import JsonRpcServer
from local_agent_runtime.store.sqlite_store import SQLiteStore
from local_agent_runtime.tools.registry import ToolRegistry


def _make_runtime(tmp_path: Any) -> SimpleNamespace:
    event_bus = EventBus()
    store = SQLiteStore(str(tmp_path / "runtime.sqlite3"))
    tool_registry = ToolRegistry(
        tools={
            "read_file": lambda _params: {"content": ""},
            "run_command": lambda _params: {"exitCode": 0},
            "apply_patch": lambda _params: {"ok": True},
        }
    )
    orchestrator = Orchestrator(
        store=store,
        event_bus=event_bus,
        tool_registry=tool_registry,
        provider=None,
    )
    server = JsonRpcServer(orchestrator=orchestrator, store=store, event_bus=event_bus)
    return SimpleNamespace(server=server, store=store)


def _rpc(runtime: SimpleNamespace, method: str, params: dict[str, Any]) -> dict[str, Any]:
    request_id = f"req_{method}"
    envelope = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
    return runtime.server.handle_line(json.dumps(envelope, ensure_ascii=False))


def test_agent_profile_crud_round_trip(tmp_path: Any) -> None:
    runtime = _make_runtime(tmp_path)

    created = _rpc(
        runtime,
        "agent.profile.create",
        {
            "id": "profile_builder",
            "name": "Builder",
            "description": "Builds scoped changes",
            "role": "builder",
            "cwd": "packages/app",
            "enabled": True,
            "permissionMode": "ask",
            "providerProfileId": "remote",
            "model": "gpt-test",
            "skillIds": ["frontend"],
            "mcpServerIds": ["docs"],
            "toolPolicy": {
                "allowedTools": ["read_file", "apply_patch"],
                "deniedTools": ["run_command"],
            },
            "systemPrompt": "Be precise.",
        },
    )["result"]["agent"]

    assert created["id"] == "profile_builder"
    assert created["role"] == "builder"
    assert created["permissionMode"] == "ask"
    assert created["skillIds"] == ["frontend"]
    assert created["mcpServerIds"] == ["docs"]
    assert created["toolPolicy"]["deniedTools"] == ["run_command"]

    listed = _rpc(runtime, "agent.profile.list", {})["result"]["agents"]
    assert [item["id"] for item in listed] == ["profile_builder"]

    updated = _rpc(
        runtime,
        "agent.profile.update",
        {
            "agentId": "profile_builder",
            "name": "Builder v2",
            "enabled": False,
            "permissionMode": "plan",
            "skillIds": ["frontend", "tests"],
            "toolPolicy": {"allowedTools": ["read_file"]},
        },
    )["result"]["agent"]

    assert updated["name"] == "Builder v2"
    assert updated["enabled"] is False
    assert updated["permissionMode"] == "plan"
    assert updated["skillIds"] == ["frontend", "tests"]
    assert updated["toolPolicy"] == {"allowedTools": ["read_file"]}

    enabled_only = _rpc(runtime, "agent.profile.list", {"enabledOnly": True})["result"]["agents"]
    assert enabled_only == []

    deleted = _rpc(runtime, "agent.profile.delete", {"agentId": "profile_builder"})["result"]
    assert deleted == {"deleted": True, "agentId": "profile_builder"}
    assert _rpc(runtime, "agent.profile.list", {})["result"]["agents"] == []


def test_agent_profile_validate_reports_errors(tmp_path: Any) -> None:
    runtime = _make_runtime(tmp_path)

    result = _rpc(
        runtime,
        "agent.profile.validate",
        {
            "name": "",
            "role": "wizard",
            "toolPolicy": {"allowedTools": "read_file"},
        },
    )["result"]

    assert result["valid"] is False
    assert "name must be a non-empty string" in result["errors"]
    assert any("Invalid role" in error for error in result["errors"])
    assert "toolPolicy.allowedTools must be an array" in result["errors"]


def test_agent_profile_preview_tools_uses_registry_and_policy(tmp_path: Any) -> None:
    runtime = _make_runtime(tmp_path)

    result = _rpc(
        runtime,
        "agent.profile.previewTools",
        {
            "role": "reviewer",
            "toolPolicy": {
                "allowedTools": ["read_file", "run_command"],
                "deniedTools": ["apply_patch"],
            },
        },
    )["result"]

    assert result["allowedTools"] == ["read_file"]
    assert "run_command" in result["deniedTools"]
    assert "apply_patch" in result["deniedTools"]
