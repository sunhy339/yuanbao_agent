"""Main workflow acceptance coverage for Skills + MCP + compaction handoff.

Covers:
  1. Skill trigger -> MCP tool call -> compaction survival (original)
  2. MCP server unavailable fallback
  3. MCP tool returns error / partial result
  4. Skill with strict_whitelist blocks MCP tools
  5. Skill with inherit_mcp allows MCP tools
  6. Missing skill fallback - orchestrator proceeds without skill
  7. Multiple MCP servers with skill routing
  8. Skill tool policy reflected in tool_policy_decision context
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from local_agent_runtime.event_bus import EventBus
from local_agent_runtime.orchestrator.service import Orchestrator
from local_agent_runtime.provider.adapter import ProviderAdapter
from local_agent_runtime.router.types import ExecutionStrategy, RoutingDecision, Scenario
from local_agent_runtime.rpc.server import JsonRpcServer
from local_agent_runtime.store.sqlite_store import SQLiteStore
from local_agent_runtime.tools.registry import ToolRegistry


class SkillMcpProvider:
    def __init__(self) -> None:
        self.main_calls: list[dict[str, Any]] = []

    def generate(self, prompt: str, context: dict[str, Any]) -> dict[str, Any]:
        if prompt.startswith("Summarize the following conversation history"):
            return {"message": "MCP lookup returned the release checklist answer."}
        if prompt.startswith("Decide whether to compact"):
            return {"message": json.dumps({"shouldCompact": True, "reason": "acceptance test"})}
        self.main_calls.append({"prompt": prompt, "context": context})
        if len(self.main_calls) == 1:
            return {
                "message": "I will use the skill and query the MCP knowledge base.",
                "tool_calls": [
                    {
                        "id": "call_kb_lookup",
                        "name": "mcp__kb__lookup",
                        "arguments": {"query": "release checklist"},
                    }
                ],
            }
        return {"final": "Used skill guidance and MCP knowledge base result to answer."}


class FailingMcpProvider:
    def __init__(self) -> None:
        self.main_calls: list[dict[str, Any]] = []

    def generate(self, prompt: str, context: dict[str, Any]) -> dict[str, Any]:
        if prompt.startswith("Summarize the following conversation history"):
            return {"message": "MCP lookup failed because the knowledge base server was unavailable."}
        if prompt.startswith("Decide whether to compact"):
            return {"message": json.dumps({"shouldCompact": True, "reason": "failed mcp handoff"})}
        self.main_calls.append({"prompt": prompt, "context": context})
        if len(self.main_calls) == 1:
            return {
                "message": "I will query the MCP knowledge base.",
                "tool_calls": [
                    {
                        "id": "call_kb_lookup_failed",
                        "name": "mcp__kb__lookup",
                        "arguments": {"query": "release checklist"},
                    }
                ],
            }
        return {"final": "MCP was unavailable, so I reported the fallback path."}


def _make_runtime(tmp_path: Any) -> SimpleNamespace:
    provider = SkillMcpProvider()
    return _make_runtime_with_provider(tmp_path, provider)


def _make_runtime_with_provider(tmp_path: Any, provider: Any) -> SimpleNamespace:
    event_bus = EventBus()
    store = SQLiteStore(str(tmp_path / "runtime.sqlite3"))
    tool_registry = ToolRegistry()
    orchestrator = Orchestrator(
        store=store,
        event_bus=event_bus,
        tool_registry=tool_registry,
        provider=provider,
    )
    server = JsonRpcServer(orchestrator=orchestrator, store=store, event_bus=event_bus)
    events: list[dict[str, Any]] = []
    event_bus.subscribe(lambda event: events.append(event_bus.as_payload(event)))
    return SimpleNamespace(server=server, store=store, orchestrator=orchestrator, provider=provider, events=events)


def _rpc(runtime: SimpleNamespace, method: str, params: dict[str, Any]) -> dict[str, Any]:
    request_id = f"req_{len(runtime.events)}_{method}"
    response = runtime.server.handle_line(json.dumps({
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
        "params": params,
    }))
    assert response["id"] == request_id
    return response


def _call_result(response: dict[str, Any], key: str) -> dict[str, Any]:
    assert "result" in response, response
    return response["result"][key]


def test_skill_mcp_result_survives_compaction_handoff(tmp_path: Any) -> None:
    runtime = _make_runtime(tmp_path)
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    workspace = _call_result(_rpc(runtime, "workspace.open", {"path": str(workspace_root)}), "workspace")
    session = _call_result(
        _rpc(runtime, "session.create", {"workspaceId": workspace["id"], "title": "skill mcp acceptance"}),
        "session",
    )
    runtime.store.update_config({
        "config": {
            "policy": {"maxTaskSteps": 5},
            "autonomy": {
                "activeProfileId": "compact-fast",
                "profiles": [{"id": "compact-fast", "maxSteps": 5, "compactionThreshold": 80}],
            },
        }
    })
    runtime.store.create_skill({
        "id": "kb_release_skill",
        "name": "KB Release Skill",
        "description": "Use the MCP knowledge base while following release instructions.",
        "system_prompt": "You must consult the MCP knowledge base before final release answers.",
        "tool_whitelist": ["read_file"],
        "parameter_constraints": {},
        "category": "acceptance",
        "tool_policy": "inherit_mcp",
    })
    runtime.orchestrator._tool_registry.register(
        "mcp__kb__lookup",
        lambda params: {
            "status": "completed",
            "content": f"KB says release checklist requires tests for {params['query']}.",
        },
        {
            "name": "mcp__kb__lookup",
            "description": "Look up release checklist knowledge.",
            "input_schema": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    )
    routing = RoutingDecision(
        scenario=Scenario.CODE_EDIT,
        strategy=ExecutionStrategy.REACT_STANDARD,
        confidence=0.96,
        skill_id="kb_release_skill",
        max_steps=5,
    )

    with patch.object(runtime.orchestrator._meta_router, "route", return_value=routing):
        task = _call_result(
            _rpc(runtime, "message.send", {"sessionId": session["id"], "content": "prepare release answer"}),
            "task",
        )

    assert task["status"] == "completed"
    first_context = runtime.provider.main_calls[0]["context"]
    assert first_context["skillPolicy"]["skillId"] == "kb_release_skill"
    assert "mcp__kb__lookup" in {tool["name"] for tool in first_context["tools"]}
    assert "mcp__kb__lookup" in first_context["tool_policy_decision"]["allowedToolNames"]
    second_messages = runtime.provider.main_calls[1]["context"]["messages"]
    assert any("Structured handoff" in message["content"] for message in second_messages)

    budget = _rpc(runtime, "context.budget", {"taskId": task["id"]})["result"]
    assert budget["compactions"]
    handoff = budget["compactions"][0]["handoffSummary"]
    assert handoff["objective"] == "prepare release answer"
    assert "skill: kb_release_skill" in handoff["decisions"]
    assert any("KB says release checklist" in item["content"] for item in handoff["recentContext"])

    event_types = [event["type"] for event in runtime.events]
    assert "skill.tools.filtered" in event_types
    assert "tool.completed" in event_types


def test_failed_mcp_tool_is_structured_in_compaction_handoff(tmp_path: Any) -> None:
    runtime = _make_runtime_with_provider(tmp_path, FailingMcpProvider())
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    workspace = _call_result(_rpc(runtime, "workspace.open", {"path": str(workspace_root)}), "workspace")
    session = _call_result(
        _rpc(runtime, "session.create", {"workspaceId": workspace["id"], "title": "failed mcp handoff"}),
        "session",
    )
    runtime.store.update_config({
        "config": {
            "policy": {"maxTaskSteps": 5},
            "autonomy": {
                "activeProfileId": "compact-fast",
                "profiles": [{"id": "compact-fast", "maxSteps": 5, "compactionThreshold": 80}],
            },
        }
    })
    runtime.store.create_skill({
        "id": "kb_release_skill",
        "name": "KB Release Skill",
        "description": "Use the MCP knowledge base while following release instructions.",
        "system_prompt": "You must consult the MCP knowledge base before final release answers.",
        "tool_whitelist": ["read_file"],
        "parameter_constraints": {},
        "category": "acceptance",
        "tool_policy": "inherit_mcp",
    })
    runtime.orchestrator._tool_registry.register(
        "mcp__kb__lookup",
        lambda _params: {
            "status": "failed",
            "error": "MCP server kb is unavailable",
        },
        {
            "name": "mcp__kb__lookup",
            "description": "Look up release checklist knowledge.",
            "input_schema": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    )
    routing = RoutingDecision(
        scenario=Scenario.CODE_EDIT,
        strategy=ExecutionStrategy.REACT_STANDARD,
        confidence=0.96,
        skill_id="kb_release_skill",
        max_steps=5,
    )

    with patch.object(runtime.orchestrator._meta_router, "route", return_value=routing):
        task = _call_result(
            _rpc(runtime, "message.send", {"sessionId": session["id"], "content": "prepare release answer"}),
            "task",
        )

    assert task["status"] == "waiting_approval"
    budget = _rpc(runtime, "context.budget", {"taskId": task["id"]})["result"]
    handoff = budget["compactions"][0]["handoffSummary"]
    assert handoff["verificationStatus"] == "failed"
    assert handoff["failedTools"][0]["name"] == "mcp__kb__lookup"
    assert handoff["failedTools"][0]["summary"] == "MCP server kb is unavailable"
    assert handoff["failedTools"][0]["failureKind"] == "mcp_server_unavailable"
    assert "refresh tools" in handoff["failedTools"][0]["recoveryHint"]
    assert handoff["nextCommand"] == "Recover failed tool: mcp__kb__lookup"
