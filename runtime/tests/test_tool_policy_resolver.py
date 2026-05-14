from __future__ import annotations

import json
from pathlib import Path

from local_agent_runtime.policy.tool_policy_resolver import ToolPolicyResolver
from local_agent_runtime.store.sqlite_store import SQLiteStore
from local_agent_runtime.tools.registry import BUILTIN_TOOL_SCHEMAS


def _tools(*names: str) -> list[dict]:
    schemas = {schema["name"]: schema for schema in BUILTIN_TOOL_SCHEMAS}
    return [schemas[name] for name in names]


def _mcp_tool(server_id: str, tool_name: str) -> dict:
    return {
        "name": f"mcp__{server_id}__{tool_name}",
        "description": "mock MCP tool",
        "input_schema": {"type": "object", "properties": {}},
        "_mcp_server_id": server_id,
        "_mcp_tool_name": tool_name,
    }


def test_root_synthesis_after_task_result_exposes_no_tools() -> None:
    resolver = ToolPolicyResolver()
    decision = resolver.resolve(
        task={"id": "task_root", "role": "root"},
        context={"routing": {"strategy": "plan_swarm"}},
        tool_results=[{"name": "task", "result": {"status": "completed"}}],
        registered_tools=_tools("task", "read_file", "write_file", "run_command"),
    )

    assert decision.phase == "synthesis"
    assert decision.allowed_tool_names == []
    assert set(decision.denied_tool_names) == {"task", "read_file", "write_file", "run_command"}
    assert decision.role_snapshot["runtimeRole"] == "root"


def test_reviewer_role_is_read_only() -> None:
    resolver = ToolPolicyResolver()
    decision = resolver.resolve(
        task={"id": "task_review", "role": "reviewer"},
        context={},
        tool_results=[],
        registered_tools=_tools("read_file", "git_diff", "write_file", "run_command"),
    )

    assert decision.phase == "review"
    assert set(decision.allowed_tool_names) == {"read_file", "git_diff"}
    assert set(decision.denied_tool_names) == {"write_file", "run_command"}


def test_child_worker_uses_agent_type_metadata_and_allowlist() -> None:
    resolver = ToolPolicyResolver()
    decision = resolver.resolve(
        task={"id": "task_child", "role": "worker"},
        context={
            "_child_worker": True,
            "agentType": "structure-agent",
            "runtimeRole": "worker",
            "_child_tool_allowlist": ["read_file", "git_status"],
        },
        tool_results=[],
        registered_tools=_tools("read_file", "git_status", "write_file", "task"),
    )

    assert decision.role_snapshot["runtimeRole"] == "worker"
    assert decision.role_snapshot["agentType"] == "structure-agent"
    assert set(decision.allowed_tool_names) == {"read_file", "git_status"}
    assert set(decision.denied_tool_names) == {"write_file", "task"}


def test_permission_engine_denies_blocked_tool_exposure() -> None:
    resolver = ToolPolicyResolver()
    decision = resolver.resolve(
        task={"id": "task_root", "role": "root"},
        context={
            "config": {
                "permissions": {
                    "preset": "balanced",
                    "capabilities": {"runCommand": {"mode": "blocked", "scope": "*"}},
                },
            },
        },
        tool_results=[],
        registered_tools=_tools("read_file", "run_command"),
    )

    assert "read_file" in decision.allowed_tool_names
    assert "run_command" in decision.denied_tool_names
    assert "blocked by policy" in decision.reasons["run_command"]
    run_detail = next(item for item in decision.decision_details if item["toolName"] == "run_command")
    assert run_detail["permissionDecision"] == "deny"
    assert run_detail["finalDecision"] == "denied"


def test_skill_strict_whitelist_filters_provider_tools() -> None:
    resolver = ToolPolicyResolver()
    decision = resolver.resolve(
        task={"id": "task_skill", "role": "root"},
        context={
            "skillPolicy": {
                "skillId": "docs_only",
                "toolPolicy": "strict_whitelist",
                "toolWhitelist": ["read_file"],
            },
        },
        tool_results=[],
        registered_tools=[*_tools("read_file", "write_file"), _mcp_tool("docs", "lookup")],
    )

    assert decision.allowed_tool_names == ["read_file"]
    assert set(decision.denied_tool_names) == {"write_file", "mcp__docs__lookup"}
    assert "skill=docs_only" in decision.reasons["write_file"]
    assert "strict_whitelist" in decision.reasons["mcp__docs__lookup"]


def test_skill_inherit_mcp_allows_mcp_but_filters_builtin_tools() -> None:
    resolver = ToolPolicyResolver()
    decision = resolver.resolve(
        task={"id": "task_skill_mcp", "role": "root"},
        context={
            "skillPolicy": {
                "skillId": "mcp_reader",
                "toolPolicy": "inherit_mcp",
                "toolWhitelist": ["read_file"],
            },
        },
        tool_results=[],
        registered_tools=[*_tools("read_file", "write_file"), _mcp_tool("docs", "lookup")],
    )

    assert set(decision.allowed_tool_names) == {"read_file", "mcp__docs__lookup"}
    assert decision.denied_tool_names == ["write_file"]


def test_mcp_server_policy_filters_mcp_tools() -> None:
    resolver = ToolPolicyResolver()
    decision = resolver.resolve(
        task={"id": "task_mcp", "role": "root"},
        context={
            "mcpPolicy": {
                "allowedServers": ["docs"],
                "blockedTools": ["danger"],
            },
        },
        tool_results=[],
        registered_tools=[
            _mcp_tool("docs", "lookup"),
            _mcp_tool("docs", "danger"),
            _mcp_tool("private", "lookup"),
        ],
    )

    assert decision.allowed_tool_names == ["mcp__docs__lookup"]
    assert set(decision.denied_tool_names) == {"mcp__docs__danger", "mcp__private__lookup"}
    assert "blocked by MCP policy" in decision.reasons["mcp__docs__danger"]
    assert "not in MCP server allowlist" in decision.reasons["mcp__private__lookup"]


def test_context_snapshot_persists_tool_policy_and_role_snapshot(tmp_path: Path) -> None:
    store = SQLiteStore(str(tmp_path / "runtime.sqlite3"))
    try:
        workspace = store.upsert_workspace(str(tmp_path / "workspace"))
        session = store.create_session(workspace_id=workspace["id"], title="tool policy")
        task = store.create_task(session_id=session["id"], task_type="chat", goal="inspect", plan=[])
        snapshot = store.create_context_snapshot(
            session_id=session["id"],
            task_id=task["id"],
            tool_policy_decision={
                "phase": "synthesis",
                "allowedToolNames": [],
                "deniedToolNames": ["task"],
            },
            role_snapshot={
                "runtimeRole": "root",
                "agentType": "root",
            },
        )

        serialized = store._serialize_context_snapshot(snapshot)  # noqa: SLF001
        assert serialized["toolPolicyDecision"]["phase"] == "synthesis"
        assert serialized["toolPolicyDecision"]["deniedToolNames"] == ["task"]
        assert serialized["roleSnapshot"]["runtimeRole"] == "root"
    finally:
        store.close()


def test_provider_turn_persists_tool_policy_for_replay(tmp_path: Path) -> None:
    store = SQLiteStore(str(tmp_path / "runtime.sqlite3"))
    try:
        workspace = store.upsert_workspace(str(tmp_path / "workspace"))
        session = store.create_session(workspace_id=workspace["id"], title="provider turn policy")
        task = store.create_task(session_id=session["id"], task_type="chat", goal="inspect", plan=[])
        turn = store.create_provider_turn(
            task_id=task["id"],
            session_id=session["id"],
            turn_index=0,
            tool_policy_decision={
                "phase": "investigation",
                "allowedToolNames": ["read_file"],
                "deniedToolNames": ["run_command"],
                "decisionDetails": [{"toolName": "run_command", "finalDecision": "denied"}],
            },
            role_snapshot={"runtimeRole": "root", "agentType": "root"},
        )
        turns = store.list_provider_turns(task["id"])

        assert turns[0]["id"] == turn["id"]
        policy = json.loads(turns[0]["tool_policy_decision_json"])
        role = json.loads(turns[0]["role_snapshot_json"])
        assert policy["deniedToolNames"] == ["run_command"]
        assert policy["decisionDetails"][0]["finalDecision"] == "denied"
        assert role["runtimeRole"] == "root"
    finally:
        store.close()
