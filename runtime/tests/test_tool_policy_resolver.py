from __future__ import annotations

import json
from pathlib import Path

from local_agent_runtime.policy.tool_policy_resolver import ToolPolicyResolver
from local_agent_runtime.orchestrator.message_routing import MessageRoutingMixin
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


def test_root_continues_with_non_subagent_tools_after_task_result_by_default() -> None:
    resolver = ToolPolicyResolver()
    decision = resolver.resolve(
        task={"id": "task_root", "role": "root"},
        context={"routing": {"strategy": "react_standard"}},
        tool_results=[{"name": "task", "result": {"status": "completed"}}],
        registered_tools=_tools("task", "read_file", "write_file", "run_command"),
    )

    assert decision.phase == "post_task_continuation"
    assert set(decision.allowed_tool_names) == {"read_file", "write_file", "run_command"}
    assert decision.denied_tool_names == ["task"]
    assert decision.role_snapshot["runtimeRole"] == "root"


def test_plan_strategy_continues_with_non_subagent_tools_after_task_result_by_default() -> None:
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
    task_detail = next(item for item in decision.decision_details if item["toolName"] == "task")
    assert task_detail["toolContinuationPolicy"]["source"] == "strategy_default_post_task_continuation"
    assert "parent may continue with non-task tools" in task_detail["reason"]


def test_cleanup_noise_profile_limits_root_tools() -> None:
    resolver = ToolPolicyResolver()
    decision = resolver.resolve(
        task={"id": "task_root", "role": "root"},
        context={
            "routing": {
                "strategy": "react_standard",
                "profile": {"toolPolicy": "cleanup_noise"},
            },
        },
        tool_results=[],
        registered_tools=_tools(
            "git_status",
            "list_dir",
            "read_file",
            "search_files",
            "write_file",
            "apply_patch",
            "run_command",
            "ask_user_question",
        ),
    )

    assert set(decision.allowed_tool_names) == {"git_status", "list_dir", "run_command", "ask_user_question"}
    assert set(decision.denied_tool_names) == {"read_file", "search_files", "write_file", "apply_patch"}


def test_cleanup_goal_detector_matches_systemdrive_cleanup_request() -> None:
    assert MessageRoutingMixin._goal_mentions_generated_local_cleanup(
        "优化一下，systemdrive看看需要不需要，不需要删掉"
    )


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


def test_agent_result_continues_with_non_subagent_tools_by_default() -> None:
    resolver = ToolPolicyResolver()
    decision = resolver.resolve(
        task={"id": "task_root", "role": "root"},
        context={"routing": {"strategy": "react_standard"}},
        tool_results=[{"name": "agent", "result": {"status": "completed"}}],
        registered_tools=_tools("agent", "task", "read_file"),
    )

    assert decision.phase == "post_task_continuation"
    assert decision.allowed_tool_names == ["read_file"]
    assert set(decision.denied_tool_names) == {"agent", "task"}


def test_plan_strategy_exposes_agent_and_task_during_legacy_planning() -> None:
    resolver = ToolPolicyResolver()
    decision = resolver.resolve(
        task={"id": "task_root", "role": "root"},
        context={"routing": {"strategy": "plan_swarm", "legacyPlanExecution": True}},
        tool_results=[],
        registered_tools=_tools("agent", "task", "read_file", "write_file"),
    )

    assert decision.phase == "planning"
    assert {"agent", "task", "read_file"}.issubset(set(decision.allowed_tool_names))
    assert "write_file" in decision.allowed_tool_names


def test_model_tools_plan_strategy_stays_investigation_phase() -> None:
    resolver = ToolPolicyResolver()
    decision = resolver.resolve(
        task={"id": "task_root", "role": "root"},
        context={"routing": {"strategy": "plan_swarm", "orchestrationMode": "model_tools"}},
        tool_results=[],
        registered_tools=_tools("agent", "task", "read_file", "write_file"),
    )

    assert decision.phase == "investigation"
    assert {"agent", "task", "read_file"}.issubset(set(decision.allowed_tool_names))
    assert "write_file" in decision.allowed_tool_names


def test_agent_tool_budget_blocks_agent_and_task_after_limit() -> None:
    resolver = ToolPolicyResolver()
    decision = resolver.resolve(
        task={"id": "task_root", "role": "root"},
        context={
            "routing": {
                "strategy": "plan_swarm",
                "toolContinuation": {
                    "allowToolsAfterTaskResults": True,
                    "allowMoreSubtasksAfterTaskResults": True,
                    "maxTaskToolCalls": 1,
                },
            },
        },
        tool_results=[{"name": "agent", "result": {"status": "completed"}}],
        registered_tools=_tools("agent", "task", "read_file"),
    )

    assert decision.phase == "post_task_continuation"
    assert decision.allowed_tool_names == ["read_file"]
    assert set(decision.denied_tool_names) == {"agent", "task"}
    assert "1/1" in decision.reasons["agent"]
    assert "1/1" in decision.reasons["task"]


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


def test_plan_mode_exposes_only_read_tools_and_exit_tool() -> None:
    resolver = ToolPolicyResolver()
    decision = resolver.resolve(
        task={"id": "task_plan", "role": "root"},
        context={"_plan_mode": True},
        tool_results=[],
        registered_tools=_tools("read_file", "search_files", "exit_plan_mode", "write_file", "run_command", "task"),
    )

    assert decision.phase == "plan_mode"
    assert set(decision.allowed_tool_names) == {"read_file", "search_files", "exit_plan_mode"}
    assert set(decision.denied_tool_names) == {"write_file", "run_command", "task"}


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
        registered_tools=_tools("read_file", "git_status", "write_file", "agent", "task"),
    )

    assert decision.role_snapshot["runtimeRole"] == "worker"
    assert decision.role_snapshot["agentType"] == "structure-agent"
    assert set(decision.allowed_tool_names) == {"read_file", "git_status"}
    assert set(decision.denied_tool_names) == {"write_file", "agent", "task"}


def test_child_worker_explicit_write_allowlist_enters_execution_phase() -> None:
    resolver = ToolPolicyResolver()
    decision = resolver.resolve(
        task={"id": "task_child", "role": "worker"},
        context={
            "_child_worker": True,
            "agentType": "coder",
            "runtimeRole": "worker",
            "_child_tool_allowlist": ["read_file", "run_command", "apply_patch", "write_file", "agent", "task"],
        },
        tool_results=[],
        registered_tools=_tools("read_file", "run_command", "apply_patch", "write_file", "agent", "task"),
    )

    assert decision.phase == "execution"
    assert set(decision.allowed_tool_names) == {"read_file", "run_command", "apply_patch", "write_file"}
    assert set(decision.denied_tool_names) == {"agent", "task"}
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

    for command in (
        "Get-ChildItem -Force",
        "Get-Content README.md",
        "git diff --stat",
        "git rev-parse --show-toplevel",
        "where.exe python",
        "npm.cmd run typecheck",
        "npm run typecheck",
        "pnpm run lint",
        "python -m py_compile blog_service.py",
        r'& "C:\Python314\python.exe" -m pytest -q',
    ):
        decision = engine.evaluate(PermissionRequest(
            capability="runCommand",
            tool_name="run_command",
            context={"command": command},
        ))
        assert decision.decision == "allow"


def test_permission_engine_keeps_dangerous_or_chained_shell_approval_required() -> None:
    from local_agent_runtime.policy.permission_engine import PermissionEngine, PermissionRequest

    engine = PermissionEngine({
        "permissions": {
            "preset": "balanced",
            "capabilities": {
                "runCommand": {"mode": "ask", "scope": "*"},
            },
        },
    })

    for command in ("git status && Remove-Item build", "cat README.md > out.txt", "pytest | tee out.txt"):
        decision = engine.evaluate(PermissionRequest(
            capability="runCommand",
            tool_name="run_command",
            context={"command": command},
        ))
        assert decision.decision == "approval_required"


def test_permission_engine_accept_edits_allows_write_but_not_dangerous_shell() -> None:
    from local_agent_runtime.policy.permission_engine import PermissionEngine, PermissionRequest

    engine = PermissionEngine({"policy": {"approvalMode": "accept_edits"}})

    write_decision = engine.evaluate(PermissionRequest(
        capability="writeFile",
        tool_name="apply_patch",
        context={"changedPaths": ["src/app.ts"]},
    ))
    shell_decision = engine.evaluate(PermissionRequest(
        capability="runCommand",
        tool_name="run_command",
        context={"command": "python main.py"},
    ))

    assert write_decision.decision == "allow"
    assert shell_decision.decision == "approval_required"


def test_permission_engine_untrusted_content_overrides_accept_edits_write_allow() -> None:
    from local_agent_runtime.policy.permission_engine import PermissionEngine, PermissionRequest

    engine = PermissionEngine({"policy": {"approvalMode": "accept_edits"}})
    decision = engine.evaluate(PermissionRequest(
        capability="writeFile",
        tool_name="write_file",
        context={
            "path": "src/app.ts",
            "untrustedContentSignals": [{"source": "web", "toolName": "web_fetch"}],
        },
    ))

    assert decision.decision == "approval_required"
    assert decision.approval_kind == "apply_patch"


def test_skill_strict_whitelist_filters_provider_tools_but_keeps_control_flow() -> None:
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
        registered_tools=[
            *_tools("read_file", "write_file", "ask_user_question", "enter_plan_mode", "exit_plan_mode"),
            _mcp_tool("docs", "lookup"),
        ],
    )

    assert set(decision.allowed_tool_names) == {"ask_user_question", "read_file"}
    assert set(decision.denied_tool_names) == {
        "enter_plan_mode",
        "exit_plan_mode",
        "write_file",
        "mcp__docs__lookup",
    }
    assert "skill=docs_only" in decision.reasons["write_file"]
    assert "strict_whitelist" in decision.reasons["mcp__docs__lookup"]


def test_default_root_turn_hides_plan_mode_tools_unless_explicit() -> None:
    resolver = ToolPolicyResolver()
    decision = resolver.resolve(
        task={"id": "task_root", "role": "root"},
        context={"routing": {"strategy": "react_standard"}},
        tool_results=[],
        registered_tools=_tools("read_file", "ask_user_question", "enter_plan_mode", "exit_plan_mode"),
    )

    assert set(decision.allowed_tool_names) == {"ask_user_question", "read_file"}
    assert {"enter_plan_mode", "exit_plan_mode"}.issubset(set(decision.denied_tool_names))

    explicit = resolver.resolve(
        task={"id": "task_root", "role": "root"},
        context={"routing": {"strategy": "react_standard", "planModeToolsEnabled": True}},
        tool_results=[],
        registered_tools=_tools("read_file", "ask_user_question", "enter_plan_mode", "exit_plan_mode"),
    )

    assert set(explicit.allowed_tool_names) == {
        "ask_user_question",
        "enter_plan_mode",
        "exit_plan_mode",
        "read_file",
    }


def test_read_only_user_constraint_hides_write_capable_tools() -> None:
    resolver = ToolPolicyResolver()
    decision = resolver.resolve(
        task={"id": "task_root", "role": "root"},
        context={
            "goal": "Create a next-step optimization plan. Do not modify files and do not run write commands.",
            "routing": {"strategy": "react_standard"},
        },
        tool_results=[],
        registered_tools=_tools(
            "read_file",
            "search_files",
            "git_status",
            "write_file",
            "apply_patch",
            "run_command",
            "ask_user_question",
        ),
    )

    assert set(decision.allowed_tool_names) == {
        "ask_user_question",
        "git_status",
        "read_file",
        "search_files",
    }
    assert {"write_file", "apply_patch", "run_command"}.issubset(set(decision.denied_tool_names))
    assert "read-only user constraint" in decision.reasons["*"]


def test_read_only_user_constraint_can_explicitly_allow_user_question() -> None:
    resolver = ToolPolicyResolver()
    decision = resolver.resolve(
        task={"id": "task_root", "role": "root"},
        context={
            "goal": "Read-only analysis. Do not modify files.",
            "routing": {"strategy": "react_standard", "requiresUserInput": True},
        },
        tool_results=[],
        registered_tools=_tools("read_file", "write_file", "ask_user_question"),
    )

    assert set(decision.allowed_tool_names) == {"ask_user_question", "read_file"}
    assert set(decision.denied_tool_names) == {"write_file"}


def test_read_only_user_constraint_can_come_from_main_workflow_preview() -> None:
    resolver = ToolPolicyResolver()
    decision = resolver.resolve(
        task={"id": "task_root", "role": "root"},
        context={
            "routing": {
                "strategy": "react_standard",
                "mainWorkflow": {
                    "userTakeover": {
                        "latestUserMessagePreview": "只读分析当前项目，不要修改文件。",
                    },
                },
            },
        },
        tool_results=[],
        registered_tools=_tools("read_file", "write_file", "run_command", "ask_user_question"),
    )

    assert set(decision.allowed_tool_names) == {"ask_user_question", "read_file"}
    assert set(decision.denied_tool_names) == {"write_file", "run_command"}


def test_internal_routing_reasoning_does_not_create_read_only_constraint() -> None:
    resolver = ToolPolicyResolver()
    decision = resolver.resolve(
        task={"id": "task_root", "role": "root"},
        context={
            "routing": {
                "strategy": "react_standard",
                "reasoning": "model-first-default: rule-match: keyword='read-only-doc-overrides-write:doc'",
            },
        },
        tool_results=[],
        registered_tools=_tools("read_file", "write_file", "run_command", "ask_user_question"),
    )

    assert set(decision.allowed_tool_names) == {"ask_user_question", "read_file", "write_file", "run_command"}
    assert "read-only user constraint" not in decision.reasons.get("*", "")


def test_read_only_multi_agent_keeps_subagent_tools_without_write_tools() -> None:
    resolver = ToolPolicyResolver()
    decision = resolver.resolve(
        task={"id": "task_root", "role": "root"},
        context={
            "goal": "Use multiple agents for read-only analysis. Do not modify files.",
            "routing": {"strategy": "plan_swarm", "orchestrationMode": "model_tools"},
        },
        tool_results=[],
        registered_tools=_tools("agent", "task", "read_file", "write_file", "run_command", "ask_user_question"),
    )

    assert set(decision.allowed_tool_names) == {"agent", "ask_user_question", "read_file", "task"}
    assert set(decision.denied_tool_names) == {"write_file", "run_command"}


def test_low_risk_defaulted_ask_user_question_is_not_reoffered_by_default() -> None:
    resolver = ToolPolicyResolver()
    decision = resolver.resolve(
        task={"id": "task_root", "role": "root"},
        context={
            "goal": "Use multiple agents for read-only analysis. Do not modify files.",
            "routing": {"strategy": "plan_swarm", "orchestrationMode": "model_tools"},
        },
        tool_results=[
            {
                "name": "ask_user_question",
                "result": {
                    "status": "answered",
                    "defaulted": True,
                    "reason": "low_risk_preference_defaulted",
                },
            },
        ],
        registered_tools=_tools("agent", "task", "read_file", "write_file", "run_command", "ask_user_question"),
    )

    assert set(decision.allowed_tool_names) == {"agent", "read_file", "task"}
    assert set(decision.denied_tool_names) == {"ask_user_question", "write_file", "run_command"}
    assert "low-risk question was defaulted" in decision.reasons["ask_user_question"]


def test_explicit_user_input_requirement_reoffers_ask_user_question_after_default() -> None:
    resolver = ToolPolicyResolver()
    decision = resolver.resolve(
        task={"id": "task_root", "role": "root"},
        context={
            "goal": "Read-only analysis. Ask only if a blocking scope choice is missing.",
            "routing": {
                "strategy": "react_standard",
                "requiresUserInput": True,
            },
        },
        tool_results=[
            {
                "name": "ask_user_question",
                "result": {
                    "status": "answered",
                    "defaulted": True,
                    "reason": "low_risk_preference_defaulted",
                },
            },
        ],
        registered_tools=_tools("read_file", "ask_user_question"),
    )

    assert set(decision.allowed_tool_names) == {"ask_user_question", "read_file"}
    assert decision.denied_tool_names == []


def test_explicit_plan_mode_keeps_plan_tools_under_read_only_constraint() -> None:
    resolver = ToolPolicyResolver()
    decision = resolver.resolve(
        task={"id": "task_root", "role": "root"},
        context={
            "goal": "Inspect read-only context first, then request explicit plan approval before any edit.",
            "routing": {"strategy": "react_standard", "planModeToolsEnabled": True},
        },
        tool_results=[],
        registered_tools=_tools(
            "enter_plan_mode",
            "exit_plan_mode",
            "read_file",
            "write_file",
            "run_command",
            "ask_user_question",
        ),
    )

    assert set(decision.allowed_tool_names) == {
        "ask_user_question",
        "enter_plan_mode",
        "exit_plan_mode",
        "read_file",
    }
    assert set(decision.denied_tool_names) == {"write_file", "run_command"}


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
