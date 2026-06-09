from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

from local_agent_runtime.event_bus import EventBus
from local_agent_runtime.orchestrator.service import Orchestrator
from local_agent_runtime.policy.guard import PolicyGuard
from local_agent_runtime.rpc.server import JsonRpcServer
from local_agent_runtime.store.sqlite_store import SQLiteStore
from local_agent_runtime.tools import build_builtin_tools
from local_agent_runtime.tools.registry import ToolRegistry


class RecordingSubagentService:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def dispatch(self, params: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(dict(params))
        return {
            "status": "completed",
            "summary": "Agent reviewed docs.",
            "childTaskId": "ctask_agent_1",
            "workerId": "agent_reviewer_1",
        }


def _make_runtime(tmp_path: Any) -> SimpleNamespace:
    event_bus = EventBus()
    store = SQLiteStore(str(tmp_path / "runtime.sqlite3"))
    tool_registry = ToolRegistry({})
    orchestrator = Orchestrator(
        store=store,
        event_bus=event_bus,
        tool_registry=tool_registry,
        provider=None,
    )
    server = JsonRpcServer(orchestrator=orchestrator, store=store, event_bus=event_bus)
    events: list[dict[str, Any]] = []
    event_bus.subscribe(lambda event: events.append(event_bus.as_payload(event)))
    return SimpleNamespace(orchestrator=orchestrator, store=store, server=server, events=events)


def _open_session(runtime: SimpleNamespace, tmp_path: Any) -> dict[str, Any]:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(exist_ok=True)
    workspace_response = runtime.server.handle_line(json.dumps({
        "jsonrpc": "2.0",
        "id": "workspace",
        "method": "workspace.open",
        "params": {"path": str(workspace_root)},
    }))
    workspace = workspace_response["result"]["workspace"]
    session_response = runtime.server.handle_line(json.dumps({
        "jsonrpc": "2.0",
        "id": "session",
        "method": "session.create",
        "params": {"workspaceId": workspace["id"], "title": "Agent tool"},
    }))
    return session_response["result"]["session"]


def test_execute_tool_normalizes_agent_before_subagent_dispatch(tmp_path: Any) -> None:
    runtime = _make_runtime(tmp_path)
    session = _open_session(runtime, tmp_path)
    task = runtime.store.create_task(
        session_id=session["id"],
        task_type="chat",
        goal="Delegate review",
        plan=[],
    )
    recorder = RecordingSubagentService()
    runtime.orchestrator._subagent_service = recorder  # noqa: SLF001

    tool_result = runtime.orchestrator._execute_tool(  # noqa: SLF001
        session_id=session["id"],
        task=task,
        tool_spec={
            "id": "call_agent",
            "name": "agent",
            "arguments": {
                "prompt": "Review the docs for missing Snake project sections.",
                "agent_type": "reviewer",
                "tool_allowlist": ["read_file", "search_files"],
                "cwd": "D:/py/yuanbao_agent",
                "mode": "read_only",
                "plan_mode_required": True,
            },
        },
    )

    assert tool_result["name"] == "agent"
    assert tool_result["toolCategory"] == "subtask"
    assert tool_result["arguments"]["agentType"] == "reviewer"
    assert tool_result["arguments"]["childToolAllowlist"] == ["read_file", "search_files"]
    assert recorder.calls
    dispatched = recorder.calls[0]
    assert dispatched["agentType"] == "reviewer"
    assert dispatched["agent_type"] == "reviewer"
    assert dispatched["childToolAllowlist"] == ["read_file", "search_files"]
    assert dispatched["child_tool_allowlist"] == ["read_file", "search_files"]
    assert dispatched["budget"]["childToolAllowlist"] == ["read_file", "search_files"]
    assert dispatched["profile"]["cwd"] == "D:/py/yuanbao_agent"
    assert dispatched["profile"]["mode"] == "read_only"
    assert dispatched["profile"]["planModeRequired"] is True

    started = next(event for event in runtime.events if event["type"] == "tool.started")
    completed = next(event for event in runtime.events if event["type"] == "tool.completed")
    assert started["payload"]["toolName"] == "agent"
    assert started["payload"]["arguments"]["agentType"] == "reviewer"
    assert started["payload"]["arguments"]["childToolAllowlist"] == ["read_file", "search_files"]
    assert completed["payload"]["result"]["childTaskId"] == "ctask_agent_1"


def test_execute_tool_accepts_haha_style_agent_fields(tmp_path: Any) -> None:
    runtime = _make_runtime(tmp_path)
    session = _open_session(runtime, tmp_path)
    task = runtime.store.create_task(
        session_id=session["id"],
        task_type="chat",
        goal="Delegate review",
        plan=[],
    )
    recorder = RecordingSubagentService()
    runtime.orchestrator._subagent_service = recorder  # noqa: SLF001

    runtime.orchestrator._execute_tool(  # noqa: SLF001
        session_id=session["id"],
        task=task,
        tool_spec={
            "id": "call_agent",
            "name": "agent",
            "arguments": {
                "description": "Inspect project docs",
                "prompt": "Read the project docs and report missing sections.",
                "subagent_type": "reviewer",
            },
        },
    )

    dispatched = recorder.calls[0]
    assert dispatched["agentType"] == "reviewer"
    assert dispatched["agent_type"] == "reviewer"
    assert dispatched["subagent_type"] == "reviewer"
    assert dispatched["title"] == "Inspect project docs"


def test_execute_tool_lifts_tiny_model_supplied_agent_token_budget(tmp_path: Any) -> None:
    runtime = _make_runtime(tmp_path)
    session = _open_session(runtime, tmp_path)
    task = runtime.store.create_task(
        session_id=session["id"],
        task_type="chat",
        goal="Delegate readonly analysis",
        plan=[],
    )
    recorder = RecordingSubagentService()
    runtime.orchestrator._subagent_service = recorder  # noqa: SLF001

    runtime.orchestrator._execute_tool(  # noqa: SLF001
        session_id=session["id"],
        task=task,
        tool_spec={
            "id": "call_agent",
            "name": "agent",
            "arguments": {
                "prompt": "Read the project docs and summarize optimization opportunities.",
                "agent_type": "analysis",
                "tool_allowlist": ["read_file", "search_files"],
                "budget": {
                    "maxTokens": 1200,
                    "remainingTokens": 1200,
                    "maxToolCalls": 3,
                    "remainingToolCalls": 3,
                },
            },
        },
    )

    dispatched = recorder.calls[0]
    assert dispatched["budget"]["maxTokens"] >= 16000
    assert dispatched["budget"]["remainingTokens"] >= 16000
    assert dispatched["budget"]["maxToolCalls"] >= 12
    assert dispatched["budget"]["remainingToolCalls"] >= 12
    assert dispatched["budget"]["normalizedByRuntime"] is True
    assert dispatched["budget"]["childToolAllowlist"] == ["read_file", "search_files"]


def test_child_agent_profile_drives_context_hints_and_plan_mode(tmp_path: Any, monkeypatch: Any) -> None:
    runtime = _make_runtime(tmp_path)
    session = _open_session(runtime, tmp_path)
    captured: dict[str, Any] = {}

    def fake_run_react_loop(**kwargs: Any) -> dict[str, Any]:
        captured["context"] = kwargs["context"]
        return {"status": "completed", "summary": "child done", "tool_results": []}

    monkeypatch.setattr(runtime.orchestrator, "_run_react_loop", fake_run_react_loop)
    result = runtime.orchestrator.run_child_task({
        "sessionId": session["id"],
        "prompt": "Review docs before editing.",
        "agentType": "reviewer",
        "childToolAllowlist": ["read_file", "search_files"],
        "profile": {
            "cwd": "D:/py/yuanbao_agent/docs",
            "planModeRequired": True,
        },
    })

    assert result["status"] == "completed"
    context = captured["context"]
    assert context["_plan_mode"] is True
    assert context["_plan_mode_reason"] == "child agent profile requires plan mode before execution"
    assert context["cwd"] == "D:/py/yuanbao_agent/docs"
    assert context["preferredCwd"] == "D:/py/yuanbao_agent/docs"
    assert context["childRuntimeHints"]["preferredCwd"] == "D:/py/yuanbao_agent/docs"
    hint_text = "\n".join(
        str(message.get("content") or "") for message in context["messages"]
    )
    assert "Preferred child cwd: D:/py/yuanbao_agent/docs" in hint_text
    assert "Preferred pytest command" not in hint_text


def test_provider_tools_are_model_first_and_child_allowlist_is_enforced(tmp_path: Any) -> None:
    runtime = _make_runtime(tmp_path)
    tools = build_builtin_tools(
        policy_guard=PolicyGuard(),
        store=runtime.store,
        subagent_service=RecordingSubagentService(),
    )
    runtime.orchestrator._tool_registry = ToolRegistry(tools)  # noqa: SLF001

    simple_tools = runtime.orchestrator._provider_tools({"routing": {"mode": "model_first"}})  # noqa: SLF001
    simple_names = {tool["name"] for tool in simple_tools}
    assert {"agent", "task"}.issubset(simple_names)
    simple_decision = runtime.orchestrator._tool_policy_decision_for_turn(  # noqa: SLF001
        task={"role": "root"},
        context={"routing": {"mode": "model_first"}},
        tool_results=[],
        cached_provider_tools=simple_tools,
    )
    assert {"agent", "task"}.issubset(set(simple_decision.allowed_tool_names))
    assert "enter_plan_mode" not in simple_decision.allowed_tool_names
    assert "exit_plan_mode" not in simple_decision.allowed_tool_names

    explicit_plan_tools = runtime.orchestrator._provider_tools({  # noqa: SLF001
        "routing": {"mode": "model_first", "planModeToolsEnabled": True},
    })
    explicit_decision = runtime.orchestrator._tool_policy_decision_for_turn(  # noqa: SLF001
        task={"role": "root"},
        context={"routing": {"mode": "model_first", "planModeToolsEnabled": True}},
        tool_results=[],
        cached_provider_tools=explicit_plan_tools,
    )
    assert {"enter_plan_mode", "exit_plan_mode"}.issubset(set(explicit_decision.allowed_tool_names))

    child_tools = runtime.orchestrator._provider_tools({  # noqa: SLF001
        "_child_worker": True,
        "_child_tool_allowlist": ["read_file", "agent", "task"],
        "runtimeRole": "worker",
        "routing": {"mode": "model_first"},
    })
    child_decision = runtime.orchestrator._tool_policy_decision_for_turn(  # noqa: SLF001
        task={"role": "worker"},
        context={
            "_child_worker": True,
            "_child_tool_allowlist": ["read_file", "agent", "task"],
            "runtimeRole": "worker",
        },
        tool_results=[],
        cached_provider_tools=child_tools,
    )
    assert child_decision.allowed_tool_names == ["read_file"]


def test_provider_tools_empty_for_minimal_simple_context(tmp_path: Any) -> None:
    runtime = _make_runtime(tmp_path)
    tools = build_builtin_tools(
        policy_guard=PolicyGuard(),
        store=runtime.store,
        subagent_service=RecordingSubagentService(),
    )
    runtime.orchestrator._tool_registry = ToolRegistry(tools)  # noqa: SLF001

    names = runtime.orchestrator._provider_tools({  # noqa: SLF001
        "minimal": True,
        "routing": {"scenario": "simple_query", "strategy": "react_fast", "contextMode": "minimal"},
    })

    assert names == []


def test_agent_result_uses_task_result_synthesis_gate(tmp_path: Any) -> None:
    runtime = _make_runtime(tmp_path)

    assert runtime.orchestrator._should_synthesize_after_task_results(  # noqa: SLF001
        {"routing": {"toolContinuation": {"allowToolsAfterTaskResults": False}}},
        [{"name": "agent", "result": {"status": "completed"}}],
    ) is True
    assert runtime.orchestrator._should_synthesize_after_task_results(  # noqa: SLF001
        {},
        [{"name": "agent", "result": {"status": "completed"}}],
    ) is False
    assert runtime.orchestrator._should_synthesize_after_task_results(  # noqa: SLF001
        {},
        [{"name": "agent", "result": {"status": "waiting_approval"}}],
    ) is False
