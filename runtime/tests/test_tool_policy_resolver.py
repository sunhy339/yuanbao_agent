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
        context={"routing": {"strategy": "react_standard"}},
        tool_results=[{"name": "task", "result": {"status": "completed"}}],
        registered_tools=_tools("task", "read_file", "write_file", "run_command"),
    )

    assert decision.phase == "synthesis"
    assert decision.allowed_tool_names == []
    assert set(decision.denied_tool_names) == {"task", "read_file", "write_file", "run_command"}
    assert decision.role_snapshot["runtimeRole"] == "root"


def test_plan_strategy_continues_after_task_result_but_withholds_task() -> None:
    resolver = ToolPolicyResolver()
    decision = resolver.resolve(
        task={"id": "task_root", "role": "root"},
        context={"routing": {"strategy": "plan_swarm"}},
        tool_results=[{"name": "task", "result": {"status": "completed"}}],
        registered_tools=_tools("task", "read_file", "write_file", "run_command"),
    )

    assert decision.phase == "post_task_continuation"
    assert set(decision.allowed_tool_names) == {"read_file", "write_file", "run_command"}
    assert decision.denied_tool_names == ["task"]
    assert "withheld after a child result" in decision.reasons["task"]
    task_detail = next(item for item in decision.decision_details if item["toolName"] == "task")
    assert task_detail["continuationDecision"] == "denied"
    assert task_detail["toolContinuationPolicy"]["source"] == "strategy_fallback"


def test_plan_strategy_explicit_disable_synthesizes_after_task_result() -> None:
    resolver = ToolPolicyResolver()
    decision = resolver.resolve(
        task={"id": "task_root", "role": "root"},
        context={
            "routing": {
                "strategy": "plan_swarm",
                "toolContinuation": {"allowToolsAfterTaskResults": False},
            },
        },
        tool_results=[{"name": "task", "result": {"status": "completed"}}],
        registered_tools=_tools("task", "read_file"),
    )

    assert decision.phase == "synthesis"
    assert decision.allowed_tool_names == []
    assert set(decision.denied_tool_names) == {"task", "read_file"}


def test_advisor_continuation_can_allow_post_task_tools_for_standard_react() -> None:
    resolver = ToolPolicyResolver()
    decision = resolver.resolve(
        task={"id": "task_root", "role": "root"},
        context={
            "routing": {
                "strategy": "react_standard",
                "toolContinuation": {
                    "allowToolsAfterTaskResults": True,
                    "allowMoreSubtasksAfterTaskResults": False,
                    "maxTaskToolCalls": 1,
                    "source": "decision_advisor",
                    "rationale": "Parent should inspect and integrate a delegated result before final synthesis.",
                },
            },
        },
        tool_results=[{"name": "task", "result": {"status": "completed"}}],
        registered_tools=_tools("task", "read_file", "write_file", "run_command"),
    )

    assert decision.phase == "post_task_continuation"
    assert set(decision.allowed_tool_names) == {"read_file", "write_file", "run_command"}
    assert decision.denied_tool_names == ["task"]
    task_detail = next(item for item in decision.decision_details if item["toolName"] == "task")
    assert task_detail["toolContinuationPolicy"]["source"] == "decision_advisor"
    assert task_detail["toolContinuationPolicy"]["allowToolsAfterTaskResults"] is True
    assert "1/1" in task_detail["reason"]


def test_task_tool_budget_allows_bounded_follow_up_subtasks() -> None:
    resolver = ToolPolicyResolver()
    decision = resolver.resolve(
        task={"id": "task_root", "role": "root"},
        context={
            "routing": {
                "strategy": "plan_swarm",
                "toolContinuation": {
                    "allowToolsAfterTaskResults": True,
                    "maxTaskToolCalls": 2,
                },
            },
        },
        tool_results=[{"name": "task", "result": {"status": "completed"}}],
        registered_tools=_tools("task", "read_file"),
    )

    assert decision.phase == "post_task_continuation"
    assert set(decision.allowed_tool_names) == {"task", "read_file"}
    assert decision.denied_tool_names == []


def test_task_tool_budget_blocks_after_limit() -> None:
    resolver = ToolPolicyResolver()
    decision = resolver.resolve(
        task={"id": "task_root", "role": "root"},
        context={
            "routing": {
                "strategy": "plan_swarm",
                "toolContinuation": {
                    "allowToolsAfterTaskResults": True,
                    "allowMoreSubtasksAfterTaskResults": True,
                    "maxTaskToolCalls": 2,
                },
            },
        },
        tool_results=[
            {"name": "task", "result": {"status": "completed"}},
            {"name": "read_file", "result": {"ok": True}},
            {"name": "task", "result": {"status": "completed"}},
        ],
        registered_tools=_tools("task", "read_file"),
    )

    assert decision.phase == "post_task_continuation"
    assert decision.allowed_tool_names == ["read_file"]
    assert decision.denied_tool_names == ["task"]
    assert "2/2" in decision.reasons["task"]


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


def test_child_worker_explicit_write_allowlist_enters_execution_phase() -> None:
    resolver = ToolPolicyResolver()
    decision = resolver.resolve(
        task={"id": "task_child", "role": "worker"},
        context={
            "_child_worker": True,
            "agentType": "coder",
            "runtimeRole": "worker",
            "_child_tool_allowlist": ["read_file", "run_command", "apply_patch", "write_file"],
        },
        tool_results=[],
        registered_tools=_tools("read_file", "run_command", "apply_patch", "write_file", "task"),
    )

    assert decision.phase == "execution"
    assert set(decision.allowed_tool_names) == {"read_file", "run_command", "apply_patch", "write_file"}
    assert decision.denied_tool_names == ["task"]
    run_detail = next(item for item in decision.decision_details if item["toolName"] == "run_command")
    assert run_detail["phaseDecision"] == "allowed"
    assert run_detail["finalDecision"] == "allowed"


def test_child_worker_write_allowlist_adds_local_read_tools_only() -> None:
    resolver = ToolPolicyResolver()
    decision = resolver.resolve(
        task={"id": "task_child", "role": "worker"},
        context={
            "_child_worker": True,
            "runtimeRole": "worker",
            "_child_tool_allowlist": ["run_command"],
        },
        tool_results=[],
        registered_tools=_tools("list_dir", "read_file", "git_status", "git_diff", "code_search", "web_fetch", "run_command"),
    )

    assert decision.phase == "execution"
    assert set(decision.allowed_tool_names) == {
        "list_dir",
        "read_file",
        "git_status",
        "git_diff",
        "code_search",
        "run_command",
    }
    assert decision.denied_tool_names == ["web_fetch"]


def test_child_worker_explicit_write_allowlist_still_honors_permission_engine() -> None:
    resolver = ToolPolicyResolver()
    decision = resolver.resolve(
        task={"id": "task_child", "role": "worker"},
        context={
            "_child_worker": True,
            "runtimeRole": "worker",
            "_child_tool_allowlist": ["read_file", "run_command"],
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

    assert decision.phase == "execution"
    assert decision.allowed_tool_names == ["read_file"]
    assert decision.denied_tool_names == ["run_command"]
    run_detail = next(item for item in decision.decision_details if item["toolName"] == "run_command")
    assert run_detail["permissionDecision"] == "deny"
    assert run_detail["finalDecision"] == "denied"


def test_child_worker_synthesizes_after_successful_verification_command() -> None:
    resolver = ToolPolicyResolver()
    decision = resolver.resolve(
        task={"id": "task_child", "role": "worker"},
        context={
            "_child_worker": True,
            "runtimeRole": "worker",
            "_child_tool_allowlist": ["read_file", "apply_patch", "run_command"],
        },
        tool_results=[
            {"name": "apply_patch", "result": {"status": "applied"}},
            {
                "name": "run_command",
                "result": {
                    "status": "completed",
                    "exitCode": 0,
                    "commandLog": {"command": "python -m pytest -q"},
                },
            },
        ],
        registered_tools=_tools("read_file", "apply_patch", "run_command"),
    )

    assert decision.phase == "synthesis"
    assert decision.allowed_tool_names == []
    assert set(decision.denied_tool_names) == {"read_file", "apply_patch", "run_command"}


def test_child_worker_keeps_execution_after_successful_diagnostic_command() -> None:
    resolver = ToolPolicyResolver()
    decision = resolver.resolve(
        task={"id": "task_child", "role": "worker"},
        context={
            "_child_worker": True,
            "runtimeRole": "worker",
            "_child_tool_allowlist": ["read_file", "apply_patch", "run_command"],
        },
        tool_results=[
            {
                "name": "run_command",
                "result": {
                    "status": "completed",
                    "exitCode": 0,
                    "commandLog": {"command": "where.exe python"},
                },
            },
        ],
        registered_tools=_tools("read_file", "apply_patch", "run_command"),
    )

    assert decision.phase == "execution"
    assert set(decision.allowed_tool_names) == {"read_file", "apply_patch", "run_command"}


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


def test_permission_engine_requires_approval_for_high_risk_tools_after_untrusted_content() -> None:
    resolver = ToolPolicyResolver()
    decision = resolver.resolve(
        task={"id": "task_root", "role": "root"},
        context={
            "config": {
                "permissions": {
                    "preset": "autonomous",
                    "capabilities": {
                        "runCommand": {"mode": "allow", "scope": "*"},
                        "writeFile": {"mode": "allow", "scope": "*"},
                        "subagents": {"mode": "allow", "scope": "*"},
                    },
                },
            },
        },
        tool_results=[
            {
                "name": "web_fetch",
                "result": {
                    "status": "ok",
                    "contentTrust": "untrusted",
                    "contentSource": "web",
                    "contentTrustReason": "web page",
                    "url": "https://example.com",
                },
            }
        ],
        registered_tools=_tools("read_file", "run_command", "write_file", "task"),
    )

    assert set(decision.allowed_tool_names) == {"read_file", "run_command", "write_file", "task"}
    run_detail = next(item for item in decision.decision_details if item["toolName"] == "run_command")
    write_detail = next(item for item in decision.decision_details if item["toolName"] == "write_file")
    task_detail = next(item for item in decision.decision_details if item["toolName"] == "task")
    assert run_detail["permissionDecision"] == "approval_required"
    assert write_detail["permissionDecision"] == "approval_required"
    assert task_detail["permissionDecision"] == "approval_required"
    assert run_detail["requiresApproval"] is True


def test_permission_engine_keeps_low_risk_verification_command_allowed_after_untrusted_content() -> None:
    from local_agent_runtime.policy.permission_engine import PermissionEngine, PermissionRequest

    engine = PermissionEngine({
        "permissions": {
            "preset": "autonomous",
            "capabilities": {
                "runCommand": {"mode": "allow", "scope": "*"},
            },
        },
    })
    decision = engine.evaluate(PermissionRequest(
        capability="runCommand",
        tool_name="run_command",
        context={
            "command": "python -m pytest -q",
            "untrustedContentSignals": [{"source": "web", "toolName": "web_fetch"}],
        },
    ))

    assert decision.decision == "allow"


def test_permission_engine_allows_low_risk_read_and_verification_commands_when_shell_asks() -> None:
    from local_agent_runtime.policy.permission_engine import PermissionEngine, PermissionRequest

    engine = PermissionEngine({
        "permissions": {
            "preset": "balanced",
            "capabilities": {
                "runCommand": {"mode": "ask", "scope": "*"},
            },
        },
    })

    for command in ("Get-ChildItem -Force", "git diff --stat", "python -m py_compile blog_service.py"):
        decision = engine.evaluate(PermissionRequest(
            capability="runCommand",
            tool_name="run_command",
            context={"command": command},
        ))
        assert decision.decision == "allow"


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


def test_child_allowlist_mcp_wildcard_allows_mcp_tools() -> None:
    resolver = ToolPolicyResolver()
    decision = resolver.resolve(
        task={"id": "task_child_mcp", "role": "worker", "status": "running"},
        context={
            "_child_worker": True,
            "_child_tool_allowlist": ["read_file", "mcp__*"],
            "skillPolicy": {
                "skillId": "mcp_reader",
                "toolPolicy": "inherit_mcp",
                "toolWhitelist": ["read_file"],
            },
            "mcpPolicy": {"mode": "allow", "allowedServers": ["docs"]},
            "config": {"permissions": {"preset": "autonomous"}},
        },
        tool_results=[],
        registered_tools=[*_tools("read_file", "web_fetch"), _mcp_tool("docs", "lookup")],
    )

    assert set(decision.allowed_tool_names) == {"read_file", "mcp__docs__lookup"}
    assert "web_fetch" in decision.denied_tool_names


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
