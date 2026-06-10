from __future__ import annotations

import json
import pytest
import subprocess
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from local_agent_runtime.event_bus import EventBus
from local_agent_runtime.orchestrator.service import Orchestrator
from local_agent_runtime.planner.service import Planner
from local_agent_runtime.provider.adapter import ProviderAdapter
from local_agent_runtime.policy.guard import PolicyGuard
from local_agent_runtime.policy.permission_engine import PermissionEngine
from local_agent_runtime.rpc.server import JsonRpcServer
from local_agent_runtime.services import CollaborationService, SubagentService
from local_agent_runtime.store.sqlite_store import SQLiteStore
from local_agent_runtime.tools import build_builtin_tools
from local_agent_runtime.tools.notebook import build_notebook_tool
from local_agent_runtime.tools.registry import ToolRegistry


class ScriptedProvider:
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def generate(self, prompt: str, context: dict[str, Any]) -> dict[str, Any]:
        self.calls.append({"prompt": prompt, "context": context})
        if not self._responses:
            raise AssertionError("Provider called more times than scripted")
        return self._responses.pop(0)


def _call_result(response: dict[str, Any], key: str) -> dict[str, Any]:
    assert "result" in response, response
    return response["result"][key]


def _make_runtime(tmp_path: Any, provider: Any, tools: dict[str, Any] | None = None) -> SimpleNamespace:
    return _make_runtime_at_path(tmp_path / "runtime.sqlite3", provider, tools)


def _make_runtime_at_path(database_path: Any, provider: Any, tools: dict[str, Any] | None = None) -> SimpleNamespace:
    event_bus = EventBus()
    store = SQLiteStore(str(database_path))
    store.set_feature_flag("multiAgent", True)
    tool_registry = ToolRegistry(tools or {})
    orchestrator = Orchestrator(
        store=store,
        event_bus=event_bus,
        tool_registry=tool_registry,
        provider=provider,
    )
    server = JsonRpcServer(orchestrator=orchestrator, store=store, event_bus=event_bus)
    events: list[dict[str, Any]] = []
    event_bus.subscribe(lambda event: events.append(event_bus.as_payload(event)))
    return SimpleNamespace(server=server, store=store, orchestrator=orchestrator, events=events)


def _make_builtin_runtime(tmp_path: Any, provider: Any) -> SimpleNamespace:
    event_bus = EventBus()
    store = SQLiteStore(str(tmp_path / "runtime.sqlite3"))
    store.set_feature_flag("multiAgent", True)
    config = store.get_config({})["config"]
    policy_guard = PolicyGuard(approval_mode=config["policy"]["approvalMode"])
    collaboration = CollaborationService(store, event_bus)
    subagent_service = SubagentService(store, collaboration)
    tool_registry = ToolRegistry(
        build_builtin_tools(policy_guard=policy_guard, store=store, subagent_service=subagent_service)
    )
    orchestrator = Orchestrator(
        store=store,
        event_bus=event_bus,
        tool_registry=tool_registry,
        provider=provider,
    )
    server = JsonRpcServer(orchestrator=orchestrator, store=store, event_bus=event_bus)
    events: list[dict[str, Any]] = []
    event_bus.subscribe(lambda event: events.append(event_bus.as_payload(event)))
    return SimpleNamespace(server=server, store=store, orchestrator=orchestrator, events=events)


def _rpc(runtime: SimpleNamespace, method: str, params: dict[str, Any]) -> dict[str, Any]:
    request_id = f"req_{len(runtime.events)}_{method}"
    envelope = {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
        "params": params,
    }
    response = runtime.server.handle_line(json.dumps(envelope, ensure_ascii=False))
    assert response["jsonrpc"] == "2.0"
    assert response["id"] == request_id
    return response


def _allow_browser_automation(runtime: SimpleNamespace) -> None:
    runtime.store.update_config({
        "config": {
            "permissions": {
                "preset": "balanced",
                "capabilities": {"browserAutomation": {"mode": "allow", "scope": "*"}},
            }
        }
    })


def _open_session(runtime: SimpleNamespace, tmp_path: Any) -> dict[str, Any]:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    workspace = _call_result(
        _rpc(runtime, "workspace.open", {"path": str(workspace_root)}),
        "workspace",
    )
    return _call_result(
        _rpc(
            runtime,
            "session.create",
            {"workspaceId": workspace["id"], "title": "ReAct loop"},
        ),
        "session",
    )


def _patch_text(path: str, old: str, new: str) -> str:
    return "\n".join(
        [
            f"diff --git a/{path} b/{path}",
            f"--- a/{path}",
            f"+++ b/{path}",
            "@@ -1 +1 @@",
            f"-{old}",
            f"+{new}",
        ]
    )


def test_react_loop_accepts_simple_final_answer(tmp_path: Any) -> None:
    provider = ScriptedProvider([{"final": "The provider answered directly."}])
    runtime = _make_runtime(tmp_path, provider)
    session = _open_session(runtime, tmp_path)

    task = _call_result(
        _rpc(
            runtime,
            "message.send",
            {"sessionId": session["id"], "content": "answer directly"},
        ),
        "task",
    )

    assert task["status"] in {"completed", "waiting_approval"}
    assert task["resultSummary"] == "The provider answered directly."
    completed_events = [event for event in runtime.events if event["type"] == "message.completed"]
    assert completed_events
    compat_completed_events = [event for event in runtime.events if event["type"] == "message_complete"]
    assert compat_completed_events
    assert compat_completed_events[-1]["payload"]["messageId"] == completed_events[-1]["payload"]["messageId"]
    assert compat_completed_events[-1]["payload"]["content"] == "The provider answered directly."
    goal_events = [event for event in runtime.events if event["type"] == "goal_event"]
    assert [event["payload"]["action"] for event in goal_events] == ["started", "completed"]
    assert goal_events[-1]["payload"]["summary"] == "The provider answered directly."
    assert not [event for event in runtime.events if event["type"] == "tool.started"]


def test_react_loop_does_not_force_workspace_evidence_after_final_answer(tmp_path: Any) -> None:
    provider = ScriptedProvider(
        [
            {"final": "I can answer from memory."},
        ]
    )
    runtime = _make_runtime(
        tmp_path,
        provider,
        {
            "search_files": lambda _params: {
                "matches": [{"path": "README.md", "preview": "project notes"}],
                "total": 1,
            },
        },
    )
    session = _open_session(runtime, tmp_path)
    original_routing_context = runtime.orchestrator._model_first_routing_context

    def forced_routing_context(**kwargs: Any) -> dict[str, Any]:
        routing = original_routing_context(**kwargs)
        routing.update({"scenario": "doc_write", "max_steps": 20})
        routing["workspaceEvidenceRequired"] = {
            "required": True,
            "requiredTools": ["search_files"],
            "source": "test",
        }
        return routing

    runtime.orchestrator._model_first_routing_context = forced_routing_context

    task = _call_result(
        _rpc(
            runtime,
            "message.send",
            {"sessionId": session["id"], "content": "输出当前项目路线图"},
        ),
        "task",
    )

    assert task["status"] == "completed"
    assert task["resultSummary"] == "I can answer from memory."
    assert len(provider.calls) == 1
    assert [
        event["payload"]["toolName"]
        for event in runtime.events
        if event["type"] == "tool.started"
    ] == []
    assert [
        event["payload"]["content"]
        for event in runtime.events
        if event["type"] == "message.completed"
    ] == ["I can answer from memory."]
    progress_events = [
        event
        for event in runtime.events
        if event["type"] == "assistant_progress"
        and event["payload"].get("phase") == "workspace_evidence_required"
    ]
    assert not progress_events


def test_react_loop_does_not_require_workspace_evidence_for_generic_progress_question(tmp_path: Any) -> None:
    provider = ScriptedProvider(
        [
            {"final": "I already checked the current progress."},
        ]
    )
    runtime = _make_runtime(
        tmp_path,
        provider,
        {
            "git_status": lambda _params: {
                "status": "completed",
                "branch": "main",
                "changedFiles": ["snake_game/game.py"],
            },
        },
    )
    session = _open_session(runtime, tmp_path)

    task = _call_result(
        _rpc(runtime, "message.send", {"sessionId": session["id"], "content": "Explain what a progress report should include."}),
        "task",
    )

    assert task["status"] == "completed"
    assert task["resultSummary"] == "I already checked the current progress."
    assert len(provider.calls) == 1
    evidence_prompt_text = "\n".join(
        str(message.get("content") or "")
        for call in provider.calls
        for message in call["context"]["messages"]
    )
    assert "smallest sufficient read-only evidence set" not in evidence_prompt_text
    assert "Do not run build, compile, or test commands unless" not in evidence_prompt_text
    assert [
        event["payload"]["toolName"]
        for event in runtime.events
        if event["type"] == "tool.started"
    ] == []
    progress_events = [
        event
        for event in runtime.events
        if event["type"] == "assistant_progress"
        and event["payload"].get("phase") == "workspace_evidence_required"
    ]
    assert not progress_events


def test_routing_workspace_evidence_metadata_does_not_force_read_only_tools(tmp_path: Any) -> None:
    provider = ScriptedProvider(
        [
            {"final": "I will describe the current project plan from context."},
        ]
    )
    runtime = _make_runtime(
        tmp_path,
        provider,
        {
            "read_file": lambda params: {
                "status": "completed",
                "path": params["path"],
                "content": "project notes",
                "bytesRead": 13,
            },
        },
    )
    session = _open_session(runtime, tmp_path)
    original_routing_context = runtime.orchestrator._model_first_routing_context

    def forced_routing_context(**kwargs: Any) -> dict[str, Any]:
        routing = original_routing_context(**kwargs)
        routing.update({"scenario": "doc_write", "max_steps": 20})
        routing["workspaceEvidenceRequired"] = {
            "required": True,
            "requiredTools": ["read_file"],
            "source": "test",
        }
        return routing

    runtime.orchestrator._model_first_routing_context = forced_routing_context

    task = _call_result(
        _rpc(
            runtime,
            "message.send",
            {
                "sessionId": session["id"],
                "content": (
                    "Create a next-step optimization roadmap for the current snake game project. "
                    "Do not modify files and do not ask for plan approval."
                ),
            },
        ),
        "task",
    )

    assert task["status"] == "completed"
    assert task["resultSummary"] == "I will describe the current project plan from context."
    assert len(provider.calls) == 1
    assert "workspaceEvidenceRequired" not in provider.calls[0]["context"]["routing"].get("profile", {})
    assert [
        event["payload"]["toolName"]
        for event in runtime.events
        if event["type"] == "tool.started"
    ] == []
    progress_events = [
        event
        for event in runtime.events
        if event["type"] == "assistant_progress"
        and event["payload"].get("phase") == "workspace_evidence_required"
    ]
    assert not progress_events


def test_simple_query_stays_model_first_without_workspace_probe(tmp_path: Any) -> None:
    provider = ScriptedProvider([{"final": "hello"}])
    runtime = _make_runtime(tmp_path, provider)
    session = _open_session(runtime, tmp_path)
    workspace_root = Path(session["workspaceRoot"])
    (workspace_root / "big_notes.md").write_text("# Big\n" + ("project detail\n" * 5000), encoding="utf-8")

    task = _call_result(
        _rpc(runtime, "message.send", {"sessionId": session["id"], "content": "hello"}),
        "task",
    )

    assert provider.calls
    context = provider.calls[0]["context"]
    assert context["minimal"] is False
    assert context["openai_tools"]
    assert "big_notes.md" not in "\n".join(message["content"] for message in context["messages"])
    turns = runtime.store.list_provider_turns(task["id"])
    assert turns[0]["request_tool_count"] > 0
    snapshot = runtime.store.get_context_snapshot(turns[0]["context_snapshot_id"])
    assert snapshot is not None
    assert snapshot["tool_count"] > 0
    included_sections = json.loads(snapshot["included_sections_json"] or "[]")
    assert "stable_workspace_context" not in included_sections
    assert task["routing"]["mode"] == "model_first"
    assert "scenario" not in task["routing"]
    assert "strategy" not in task["routing"]
    assert "contextMode" not in task["routing"]


def test_direct_chat_capability_prompt_stays_model_first_without_workspace_probe(tmp_path: Any) -> None:
    provider = ScriptedProvider([{"final": "I can help with coding tasks, repo inspection, and concise answers."}])
    runtime = _make_builtin_runtime(tmp_path, provider)
    session = _open_session(runtime, tmp_path)
    workspace_root = Path(session["workspaceRoot"])
    (workspace_root / "README.md").write_text("secret project details\n" * 100, encoding="utf-8")

    task = _call_result(
        _rpc(
            runtime,
            "message.send",
            {
                "sessionId": session["id"],
                "content": "\u4f60\u597d\uff0c\u7b80\u5355\u8bf4\u660e\u4e00\u4e0b\u4f60\u80fd\u505a\u4ec0\u4e48\u3002",
            },
        ),
        "task",
    )

    assert task["status"] == "completed"
    assert task["routing"]["mode"] == "model_first"
    assert "scenario" not in task["routing"]
    assert "strategy" not in task["routing"]
    assert "contextMode" not in task["routing"]
    assert [event for event in runtime.events if event["type"] == "tool.started"] == []
    context = provider.calls[0]["context"]
    assert context["minimal"] is False
    assert context["openai_tools"]
    assert "secret project details" not in "\n".join(str(message.get("content") or "") for message in context["messages"])


def test_explicit_no_tools_constraint_hides_tools_from_provider(tmp_path: Any) -> None:
    provider = ScriptedProvider([{"final": "连接正常。"}])
    runtime = _make_builtin_runtime(tmp_path, provider)
    session = _open_session(runtime, tmp_path)

    task = _call_result(
        _rpc(
            runtime,
            "message.send",
            {"sessionId": session["id"], "content": "用一句中文回答连接是否正常，不要调用工具。"},
        ),
        "task",
    )

    assert task["status"] == "completed"
    context = provider.calls[0]["context"]
    policy = context["tool_policy_decision"]
    assert policy["allowedToolNames"] == []
    assert policy["phase"] == "investigation"
    assert "explicit user constraint disables tools" in policy["reasons"]["*"]
    assert context["openai_tools"] == []
    turns = runtime.store.list_provider_turns(task["id"])
    assert turns[0]["request_tool_count"] == 0
    assert [event for event in runtime.events if event["type"] == "tool.started"] == []


def test_read_only_workspace_request_hides_write_and_command_tools_from_provider(tmp_path: Any) -> None:
    provider = ScriptedProvider([{"final": "已基于可读上下文整理完成。"}])
    runtime = _make_builtin_runtime(tmp_path, provider)
    session = _open_session(runtime, tmp_path)
    workspace_root = Path(session["workspaceRoot"])
    (workspace_root / "README.md").write_text("project notes\n", encoding="utf-8")

    task = _call_result(
        _rpc(
            runtime,
            "message.send",
            {
                "sessionId": session["id"],
                "content": "\u8bf7\u8bfb\u53d6 README.md \u5e76\u603b\u7ed3\uff0c\u4e0d\u8981\u7f16\u8f91\u6587\u4ef6\uff0c\u4e0d\u8981\u8fd0\u884c\u547d\u4ee4\u3002",
            },
        ),
        "task",
    )

    assert task["status"] == "completed"
    context = provider.calls[0]["context"]
    allowed_names = {tool["name"] for tool in context["openai_tools"]}
    assert {"read_file", "search_files", "list_dir"}.issubset(allowed_names)
    assert "apply_patch" not in allowed_names
    assert "write_file" not in allowed_names
    assert "run_command" not in allowed_names
    policy = context["tool_policy_decision"]
    assert "read-only user constraint limits tool visibility" in policy["reasons"]["*"]
    turns = runtime.store.list_provider_turns(task["id"])
    assert "apply_patch" not in {name for turn in turns for name in (turn.get("tool_policy_decision") or {}).get("allowedToolNames", [])}


def test_computer_use_approval_emits_dedicated_permission_events(tmp_path: Any) -> None:
    provider = ScriptedProvider([{"final": "Permission test task."}])
    runtime = _make_runtime(tmp_path, provider)
    session = _open_session(runtime, tmp_path)
    task = _call_result(
        _rpc(
            runtime,
            "message.send",
            {
                "sessionId": session["id"],
                "content": "Need desktop permission",
            },
        ),
        "task",
    )
    approval = runtime.store.create_approval(
        task["id"],
        "computer_use",
        {
            "app": "VS Code",
            "action": "click",
            "selector": "Run button",
            "x": 320,
            "y": 180,
            "permission": "Click the VS Code run button.",
            "details": "Click the visible run button.",
        },
    )

    runtime.orchestrator._publish(
        session_id=session["id"],
        task=task,
        event_type="approval.requested",
        payload={
            "approvalId": approval["id"],
            "taskId": task["id"],
            "kind": "computer_use",
            "request": json.loads(approval["requestJson"]),
        },
    )
    runtime.orchestrator.submit_approval({
        "approvalId": approval["id"],
        "decision": "approved",
    })

    request_events = [event for event in runtime.events if event["type"] == "computer_use_permission_request"]
    resolved_events = [event for event in runtime.events if event["type"] == "computer_use_permission"]
    assert request_events
    assert request_events[-1]["payload"]["approvalId"] == approval["id"]
    assert request_events[-1]["payload"]["app"] == "VS Code"
    assert request_events[-1]["payload"]["action"] == "click"
    assert request_events[-1]["payload"]["selector"] == "Run button"
    assert request_events[-1]["payload"]["x"] == 320
    assert request_events[-1]["payload"]["y"] == 180
    assert request_events[-1]["payload"]["previewRows"] == [
        {"label": "应用", "value": "VS Code"},
        {"label": "动作", "value": "click"},
        {"label": "目标", "value": "Run button"},
        {"label": "坐标", "value": "320, 180"},
        {"label": "权限", "value": "Click the VS Code run button."},
    ]
    assert resolved_events
    assert resolved_events[-1]["payload"]["approvalId"] == approval["id"]
    assert resolved_events[-1]["payload"]["decision"] == "approved"
    assert resolved_events[-1]["payload"]["resolved"] is True


def test_task_updated_with_plan_stays_panel_only(tmp_path: Any) -> None:
    runtime = _make_runtime(tmp_path, ScriptedProvider([]))
    session = _open_session(runtime, tmp_path)
    task = runtime.store.create_task(
        session_id=session["id"],
        task_type="chat",
        goal="Ship a focused patch",
        plan=[
            {"id": "inspect", "title": "Inspect files", "status": "active"},
            {"id": "edit", "title": "Edit implementation", "status": "pending"},
        ],
        current_step="Inspect files",
    )
    payload = {
        "status": "running",
        "currentStep": "Inspect files",
        "plan": task["plan"],
    }

    runtime.orchestrator._publish(
        session_id=session["id"],
        task=task,
        event_type="task.updated",
        payload=payload,
    )
    runtime.orchestrator._publish(
        session_id=session["id"],
        task=task,
        event_type="task.updated",
        payload=payload,
    )

    assert not [event for event in runtime.events if event["type"] == "plan_update"]
    task_events = [event for event in runtime.events if event["type"] == "task.updated"]
    assert len(task_events) == 2
    assert all("hahaCc" not in event and "yuanbao" not in event for event in task_events)
    assert all("acceptanceCriteria" not in event["payload"] for event in task_events)
    assert all("context" not in event["payload"] for event in task_events)
    assert all(event["visibility"] == "panel" for event in task_events)


def test_task_updated_without_plan_does_not_bridge_plan_update(tmp_path: Any) -> None:
    runtime = _make_runtime(tmp_path, ScriptedProvider([]))
    session = _open_session(runtime, tmp_path)
    task = runtime.store.create_task(
        session_id=session["id"],
        task_type="chat",
        goal="Answer directly",
        plan=[],
        current_step="Preparing answer",
    )

    runtime.orchestrator._publish(
        session_id=session["id"],
        task=task,
        event_type="task.updated",
        payload={"status": "running", "currentStep": "Preparing answer", "summary": "Preparing answer"},
    )

    assert not [event for event in runtime.events if event["type"] == "plan_update"]
    task_events = [event for event in runtime.events if event["type"] == "task.updated"]
    assert all("hahaCc" not in event and "yuanbao" not in event for event in task_events)
    assert all("acceptanceCriteria" not in event["payload"] for event in task_events)
    assert all("context" not in event["payload"] for event in task_events)


def test_tool_completed_bridge_preserves_structured_summaries(tmp_path: Any) -> None:
    runtime = _make_runtime(tmp_path, ScriptedProvider([]))
    session = _open_session(runtime, tmp_path)
    task = runtime.store.create_task(
        session_id=session["id"],
        task_type="chat",
        goal="Read one file",
        plan=[],
    )

    runtime.orchestrator._publish(
        session_id=session["id"],
        task=task,
        event_type="tool.completed",
        payload={
            "toolCallId": "call_read",
            "toolName": "read_file",
            "target": "src/app.ts",
            "inputSummary": "read src/app.ts",
            "resultSummary": "read src/app.ts (17 chars)",
            "resultPreview": [{"label": "文件", "value": "src/app.ts"}],
            "result": {"content": "const app = true;"},
        },
    )

    delta_events = [
        event for event in runtime.events
        if event["type"] == "content_delta" and event["payload"].get("toolUseId") == "call_read"
    ]
    assert delta_events
    assert delta_events[-1]["payload"]["toolOutput"] == "文件: src/app.ts\n"
    assert delta_events[-1]["payload"]["outputStream"] == "result_preview"
    result_events = [event for event in runtime.events if event["type"] == "tool_result"]
    assert result_events
    assert result_events[-1]["payload"]["toolUseId"] == "call_read"
    assert result_events[-1]["payload"]["target"] == "src/app.ts"
    assert result_events[-1]["payload"]["inputSummary"] == "read src/app.ts"
    assert result_events[-1]["payload"]["resultSummary"] == "read src/app.ts (17 chars)"
    assert result_events[-1]["payload"]["resultPreview"] == [{"label": "文件", "value": "src/app.ts"}]


def test_tool_completed_bridge_streams_structured_activity_before_result_preview(tmp_path: Any) -> None:
    runtime = _make_runtime(tmp_path, ScriptedProvider([]))
    session = _open_session(runtime, tmp_path)
    task = runtime.store.create_task(
        session_id=session["id"],
        task_type="chat",
        goal="Run a custom tool",
        plan=[],
    )

    runtime.orchestrator._publish(
        session_id=session["id"],
        task=task,
        event_type="tool.completed",
        payload={
            "toolCallId": "call_custom",
            "toolName": "mcp__docs__lookup",
            "target": "install guide",
            "inputSummary": "lookup install guide",
            "resultSummary": "found 2 docs",
            "resultPreview": [{"label": "摘要", "value": "found 2 docs"}],
            "result": {
                "status": "completed",
                "steps": [
                    {"label": "connect", "status": "completed", "summary": "opened docs index"},
                    {"label": "search", "status": "completed", "summary": "matched install guide"},
                ],
            },
        },
    )

    deltas = [
        event["payload"]
        for event in runtime.events
        if event["type"] == "content_delta" and event["payload"].get("toolUseId") == "call_custom"
    ]
    assert [delta["outputStream"] for delta in deltas] == ["activity", "activity", "result_preview"]
    assert deltas[0]["toolOutput"] == "connect (completed): opened docs index\n"
    assert deltas[1]["toolOutput"] == "search (completed): matched install guide\n"
    assert deltas[2]["toolOutput"] == "摘要: found 2 docs\n"


def test_assistant_token_bridge_emits_single_text_start(tmp_path: Any) -> None:
    runtime = _make_runtime(tmp_path, ScriptedProvider([]))
    session = _open_session(runtime, tmp_path)
    task = runtime.store.create_task(
        session_id=session["id"],
        task_type="chat",
        goal="Explain the change",
        plan=[],
    )
    task["activeAssistantMessageId"] = "msg_assistant_1"

    runtime.orchestrator._publish(
        session_id=session["id"],
        task=task,
        event_type="assistant.token",
        payload={"delta": "First sentence.", "step": 1},
    )
    runtime.orchestrator._publish(
        session_id=session["id"],
        task=task,
        event_type="assistant.token",
        payload={"delta": " Second sentence.", "step": 1},
    )

    starts = [
        event for event in runtime.events
        if event["type"] == "content_start" and event["payload"].get("blockType") == "text"
    ]
    deltas = [event for event in runtime.events if event["type"] == "message.delta"]
    assert len(starts) == 1
    assert starts[0]["payload"]["messageId"] == "msg_assistant_1"
    assert [event["payload"]["delta"] for event in deltas] == ["First sentence.", " Second sentence."]
    assert [event["yuanbao"]["text"] for event in deltas] == ["First sentence.", " Second sentence."]


def test_tool_started_bridge_does_not_duplicate_streamed_tool_start(tmp_path: Any) -> None:
    runtime = _make_runtime(tmp_path, ScriptedProvider([]))
    session = _open_session(runtime, tmp_path)
    task = runtime.store.create_task(
        session_id=session["id"],
        task_type="chat",
        goal="Read a streamed tool",
        plan=[],
    )

    runtime.orchestrator._publish(
        session_id=session["id"],
        task=task,
        event_type="content_start",
        payload={
            "blockType": "tool_use",
            "toolUseId": "call_read",
            "toolName": "read_file",
        },
    )
    runtime.orchestrator._publish(
        session_id=session["id"],
        task=task,
        event_type="tool.started",
        payload={
            "toolCallId": "call_read",
            "toolName": "read_file",
            "arguments": {"path": "README.md"},
        },
    )
    runtime.orchestrator._publish(
        session_id=session["id"],
        task=task,
        event_type="tool.completed",
        payload={
            "toolCallId": "call_read",
            "toolName": "read_file",
            "result": {"content": "# README"},
        },
    )

    starts = [
        event for event in runtime.events
        if event["type"] == "content_start" and event["payload"].get("toolUseId") == "call_read"
    ]
    assert len(starts) == 1
    assert starts[0]["payload"]["blockType"] == "tool_use"
    assert any(event["type"] == "tool_use_complete" for event in runtime.events)
    assert any(event["type"] == "tool_result" for event in runtime.events)


def test_provider_turn_uses_status_without_synthetic_assistant_progress(tmp_path: Any) -> None:
    provider = ScriptedProvider([{"final": "The provider answered directly."}])
    runtime = _make_runtime(tmp_path, provider)
    session = _open_session(runtime, tmp_path)

    task = _call_result(
        _rpc(
            runtime,
            "message.send",
            {"sessionId": session["id"], "content": "answer directly"},
        ),
        "task",
    )

    progress_events = [event for event in runtime.events if event["type"] == "assistant_progress"]
    status_events = [event for event in runtime.events if event["type"] == "status"]
    assert task["status"] in {"completed", "waiting_approval"}
    assert progress_events == []
    assert any(event["payload"].get("state") == "thinking" for event in status_events)


def test_react_turn_records_explicit_thought_summary_as_trace_not_synthetic_thinking(tmp_path: Any) -> None:
    provider = ScriptedProvider([
        {
            "thought_summary": "先确认相关文件，再读取目标实现。",
            "message": "我先搜索相关文件。",
            "tool_calls": [
                {
                    "id": "call_search",
                    "name": "search_files",
                    "arguments": {"query": "needle"},
                }
            ],
        },
        {"final": "Done."},
    ])
    runtime = _make_runtime(
        tmp_path,
        provider,
        {
            "search_files": lambda params: {
                "query": params["query"],
                "matches": [],
                "total": 0,
            },
        },
    )
    session = _open_session(runtime, tmp_path)

    task = _call_result(
        _rpc(
            runtime,
            "message.send",
            {"sessionId": session["id"], "content": "find needle"},
        ),
        "task",
    )

    assert task["status"] in {"completed", "waiting_approval"}
    thinking_events = [event for event in runtime.events if event["type"] == "thinking"]
    assert thinking_events == []
    decision_events = [event for event in runtime.events if event["type"] == "agent.decision.react_turn"]
    assert decision_events
    assert decision_events[0]["payload"]["thought_summary"] == "先确认相关文件，再读取目标实现。"
    assert decision_events[0]["visibility"] == "trace"
    assert not [event for event in runtime.events if event["type"] == "assistant_progress"]
    assert any(
        event["type"] == "tool_use_complete"
        and event["payload"].get("toolName") == "search_files"
        for event in runtime.events
    )
    assert any(
        event["type"] == "tool_result"
        and event["payload"].get("toolName") == "search_files"
        for event in runtime.events
    )


def test_child_provider_turn_does_not_publish_chat_progress(tmp_path: Any) -> None:
    provider = ScriptedProvider([{"final": "child answer"}])
    runtime = _make_runtime(tmp_path, provider)
    session = _open_session(runtime, tmp_path)
    task = runtime.store.create_task(
        session_id=session["id"],
        task_type="chat",
        goal="Child task",
        plan=[],
        role="worker",
    )

    runtime.orchestrator._request_provider_response(
        session_id=session["id"],
        task=task,
        goal="Child task",
        provider_context={
            "messages": [],
            "tools": [],
            "config": runtime.store.get_config({})["config"],
            "step": 1,
        },
    )

    assert not [event for event in runtime.events if event["type"] == "assistant_progress"]


def test_high_value_tool_started_bridges_tool_lifecycle_without_synthetic_progress(tmp_path: Any) -> None:
    runtime = _make_runtime(tmp_path, ScriptedProvider([]))
    session = _open_session(runtime, tmp_path)
    task = runtime.store.create_task(
        session_id=session["id"],
        task_type="chat",
        goal="Run tests",
        plan=[],
    )

    runtime.orchestrator._publish(
        session_id=session["id"],
        task=task,
        event_type="tool.started",
        payload={
            "toolCallId": "call_command",
            "toolName": "run_command",
            "arguments": {"command": "npm test"},
            "target": "npm test",
            "inputSummary": "npm test",
        },
    )

    assert not [event for event in runtime.events if event["type"] == "assistant_progress"]
    tool_start = next(
        event for event in runtime.events
        if event["type"] == "content_start" and event["payload"].get("toolUseId") == "call_command"
    )
    tool_complete = next(
        event for event in runtime.events
        if event["type"] == "tool_use_complete" and event["payload"].get("toolUseId") == "call_command"
    )
    assert tool_start["payload"]["blockType"] == "tool_use"
    assert tool_complete["payload"]["toolName"] == "run_command"
    assert tool_complete["payload"]["input"] == {"command": "npm test"}


def test_tool_started_keeps_semantic_metadata_on_tool_lifecycle_without_progress(tmp_path: Any) -> None:
    runtime = _make_runtime(tmp_path, ScriptedProvider([]))
    session = _open_session(runtime, tmp_path)
    task = runtime.store.create_task(
        session_id=session["id"],
        task_type="chat",
        goal="Inspect workspace",
        plan=[],
    )

    for tool_call_id, tool_name, phase_id, phase_label in [
        ("call_command", "run_command", "group:tgrp_1:phase:command", "Command"),
        ("call_command_more", "run_command", "group:tgrp_1:phase:command", "Command"),
        ("call_patch", "apply_patch", "group:tgrp_1:phase:file_change", "File change"),
    ]:
        runtime.orchestrator._publish(
            session_id=session["id"],
            task=task,
            event_type="tool.started",
            payload={
                "toolCallId": tool_call_id,
                "toolName": tool_name,
                "arguments": {"command": "npm test"} if tool_name == "run_command" else {"path": "src/app.ts"},
                "toolSemanticParentId": phase_id,
                "toolSemanticParentLabel": phase_label,
                "toolPhaseLabel": phase_label,
                "toolGroupId": "tgrp_1",
            },
        )

    assert not [event for event in runtime.events if event["type"] == "assistant_progress"]
    tool_events = [
        event for event in runtime.events
        if event["type"] == "tool_use_complete"
    ]
    assert [event["payload"]["toolSemanticParentLabel"] for event in tool_events] == ["Command", "Command", "File change"]
    assert [event["payload"]["toolSemanticParentId"] for event in tool_events] == [
        "group:tgrp_1:phase:command",
        "group:tgrp_1:phase:command",
        "group:tgrp_1:phase:file_change",
    ]


def test_low_value_tool_started_does_not_bridge_semantic_phase_progress(tmp_path: Any) -> None:
    runtime = _make_runtime(tmp_path, ScriptedProvider([]))
    session = _open_session(runtime, tmp_path)
    task = runtime.store.create_task(
        session_id=session["id"],
        task_type="chat",
        goal="Inspect workspace",
        plan=[],
    )

    for tool_call_id, tool_name, phase_id, phase_label in [
        ("call_search", "search_files", "group:tgrp_1:phase:search", "Search"),
        ("call_read", "read_file", "group:tgrp_1:phase:context_read", "Read context"),
    ]:
        runtime.orchestrator._publish(
            session_id=session["id"],
            task=task,
            event_type="tool.started",
            payload={
                "toolCallId": tool_call_id,
                "toolName": tool_name,
                "arguments": {"path": "src/app.ts"},
                "toolSemanticParentId": phase_id,
                "toolSemanticParentLabel": phase_label,
                "toolPhaseLabel": phase_label,
                "toolGroupId": "tgrp_1",
            },
        )

    assert not [
        event for event in runtime.events
        if event["type"] == "assistant_progress" and event["payload"].get("phase") == "tool_phase"
    ]


def test_known_tool_started_does_not_duplicate_executor_activity_delta(tmp_path: Any) -> None:
    runtime = _make_runtime(tmp_path, ScriptedProvider([]))
    session = _open_session(runtime, tmp_path)
    task = runtime.store.create_task(
        session_id=session["id"],
        task_type="chat",
        goal="Search files",
        plan=[],
    )

    runtime.orchestrator._publish(
        session_id=session["id"],
        task=task,
        event_type="tool.started",
        payload={
            "toolCallId": "call_search",
            "toolName": "search_files",
            "arguments": {"query": "needle"},
            "target": "needle",
            "inputSummary": "search needle",
            "toolCategory": "search",
            "toolPhaseId": "search",
            "toolPhaseLabel": "搜索",
            "toolSemanticParentId": "group:tgrp_1:phase:search",
            "toolSemanticParentLabel": "搜索",
            "toolGroupId": "tgrp_1",
            "toolIndex": 0,
            "toolTotal": 2,
        },
    )

    delta_events = [
        event for event in runtime.events
        if event["type"] == "content_delta" and event["payload"].get("toolUseId") == "call_search"
    ]
    assert delta_events == []


def test_custom_tool_started_bridges_activity_output_delta_as_fallback(tmp_path: Any) -> None:
    runtime = _make_runtime(tmp_path, ScriptedProvider([]))
    session = _open_session(runtime, tmp_path)
    task = runtime.store.create_task(
        session_id=session["id"],
        task_type="chat",
        goal="Call custom tool",
        plan=[],
    )

    runtime.orchestrator._publish(
        session_id=session["id"],
        task=task,
        event_type="tool.started",
        payload={
            "toolCallId": "call_custom",
            "toolName": "custom_lookup",
            "arguments": {"query": "needle"},
            "target": "needle",
            "inputSummary": "lookup needle",
            "toolCategory": "tool",
            "toolPhaseId": "tool",
            "toolPhaseLabel": "工具",
            "toolSemanticParentId": "group:tgrp_1:phase:tool",
            "toolSemanticParentLabel": "工具",
            "toolGroupId": "tgrp_1",
            "toolIndex": 0,
            "toolTotal": 2,
        },
    )

    delta_events = [
        event for event in runtime.events
        if event["type"] == "content_delta" and event["payload"].get("toolUseId") == "call_custom"
    ]
    assert delta_events
    assert delta_events[-1]["payload"]["toolOutput"] == "正在调用工具：lookup needle\n"
    assert delta_events[-1]["payload"]["outputStream"] == "activity"
    assert delta_events[-1]["payload"]["toolCategory"] == "tool"
    assert delta_events[-1]["payload"]["toolSemanticParentId"] == "group:tgrp_1:phase:tool"
    assert delta_events[-1]["payload"]["toolGroupId"] == "tgrp_1"
    assert delta_events[-1]["payload"]["toolIndex"] == 0
    assert delta_events[-1]["payload"]["toolTotal"] == 2


def test_low_value_tool_started_stays_quiet(tmp_path: Any) -> None:
    runtime = _make_runtime(tmp_path, ScriptedProvider([]))
    session = _open_session(runtime, tmp_path)
    task = runtime.store.create_task(
        session_id=session["id"],
        task_type="chat",
        goal="Read a file",
        plan=[],
    )

    runtime.orchestrator._publish(
        session_id=session["id"],
        task=task,
        event_type="tool.started",
        payload={
            "toolCallId": "call_read",
            "toolName": "read_file",
            "arguments": {"path": "src/app.ts"},
            "target": "src/app.ts",
            "inputSummary": "read src/app.ts",
        },
    )

    assert not [
        event for event in runtime.events
        if event["type"] == "assistant_progress" and event["payload"].get("phase") == "tool_execution"
    ]


def test_high_value_tool_completed_bridges_tool_result_without_synthetic_progress(tmp_path: Any) -> None:
    runtime = _make_runtime(tmp_path, ScriptedProvider([]))
    session = _open_session(runtime, tmp_path)
    task = runtime.store.create_task(
        session_id=session["id"],
        task_type="chat",
        goal="Run tests",
        plan=[],
    )

    runtime.orchestrator._publish(
        session_id=session["id"],
        task=task,
        event_type="tool.completed",
        payload={
            "toolCallId": "call_command",
            "toolName": "run_command",
            "target": "npm test",
            "inputSummary": "npm test",
            "resultSummary": "exit 0: 12 passed",
            "result": {"status": "completed", "stdout": "12 passed"},
        },
    )

    assert not [event for event in runtime.events if event["type"] == "assistant_progress"]
    tool_result = next(
        event for event in runtime.events
        if event["type"] == "tool_result" and event["payload"].get("toolUseId") == "call_command"
    )
    assert tool_result["payload"]["toolName"] == "run_command"
    assert tool_result["payload"]["isError"] is False
    assert tool_result["payload"]["resultSummary"] == "exit 0: 12 passed"


def test_background_run_command_completed_payload_bridges_tool_result_without_progress(tmp_path: Any) -> None:
    runtime = _make_runtime(tmp_path, ScriptedProvider([]))
    session = _open_session(runtime, tmp_path)
    task = runtime.store.create_task(
        session_id=session["id"],
        task_type="chat",
        goal="Start server",
        plan=[],
    )

    runtime.orchestrator._publish(
        session_id=session["id"],
        task=task,
        event_type="tool.completed",
        payload={
            "toolCallId": "call_command",
            "toolName": "run_command",
            "target": "npm run dev",
            "inputSummary": "npm run dev",
            "resultSummary": "running",
            "result": {"status": "running", "background": True},
        },
    )

    assert not [event for event in runtime.events if event["type"] == "assistant_progress"]
    tool_result = next(
        event for event in runtime.events
        if event["type"] == "tool_result" and event["payload"].get("toolUseId") == "call_command"
    )
    assert tool_result["payload"]["toolName"] == "run_command"
    assert tool_result["payload"]["isError"] is False
    assert tool_result["payload"]["resultSummary"] == "running"


def test_low_value_tool_completed_stays_quiet(tmp_path: Any) -> None:
    runtime = _make_runtime(tmp_path, ScriptedProvider([]))
    session = _open_session(runtime, tmp_path)
    task = runtime.store.create_task(
        session_id=session["id"],
        task_type="chat",
        goal="Read a file",
        plan=[],
    )

    runtime.orchestrator._publish(
        session_id=session["id"],
        task=task,
        event_type="tool.completed",
        payload={
            "toolCallId": "call_read",
            "toolName": "read_file",
            "target": "src/app.ts",
            "inputSummary": "read src/app.ts",
            "resultSummary": "read src/app.ts (17 chars)",
            "result": {"content": "const app = true;"},
        },
    )

    assert not [
        event for event in runtime.events
        if event["type"] == "assistant_progress" and event["payload"].get("phase") == "tool_completed"
    ]


def test_failed_tool_bridges_tool_result_without_synthetic_progress(tmp_path: Any) -> None:
    runtime = _make_runtime(tmp_path, ScriptedProvider([]))
    session = _open_session(runtime, tmp_path)
    task = runtime.store.create_task(
        session_id=session["id"],
        task_type="chat",
        goal="Read a missing file",
        plan=[],
    )

    runtime.orchestrator._publish(
        session_id=session["id"],
        task=task,
        event_type="tool.failed",
        payload={
            "toolCallId": "call_read",
            "toolName": "read_file",
            "target": "missing.ts",
            "inputSummary": "read missing.ts",
            "resultSummary": "missing.ts does not exist",
            "result": {"status": "failed", "error": "missing.ts does not exist"},
        },
    )

    assert not [event for event in runtime.events if event["type"] == "assistant_progress"]
    tool_result = next(
        event for event in runtime.events
        if event["type"] == "tool_result" and event["payload"].get("toolUseId") == "call_read"
    )
    assert tool_result["payload"]["toolName"] == "read_file"
    assert tool_result["payload"]["isError"] is True
    assert tool_result["payload"]["resultSummary"] == "missing.ts does not exist"


def test_command_output_bridges_tool_output_delta(tmp_path: Any) -> None:
    runtime = _make_runtime(tmp_path, ScriptedProvider([]))
    session = _open_session(runtime, tmp_path)
    task = runtime.store.create_task(
        session_id=session["id"],
        task_type="chat",
        goal="Run tests",
        plan=[],
    )

    runtime.orchestrator._publish(
        session_id=session["id"],
        task=task,
        event_type="command.output",
        payload={
            "commandId": "cmd_1",
            "toolUseId": "call_command",
            "toolName": "run_command",
            "stream": "stdout",
            "chunk": "3 passed\n",
        },
    )

    delta_events = [
        event for event in runtime.events
        if event["type"] == "content_delta" and event["payload"].get("toolOutput")
    ]
    assert delta_events
    assert delta_events[-1]["payload"]["toolUseId"] == "call_command"
    assert delta_events[-1]["payload"]["toolName"] == "run_command"
    assert delta_events[-1]["payload"]["toolOutput"] == "3 passed\n"
    assert delta_events[-1]["payload"]["outputStream"] == "stdout"
    raw_output = next(event for event in runtime.events if event["type"] == "command.output")
    assert raw_output["visibility"] == "trace"


def test_foreground_run_command_streams_output_before_completion(tmp_path: Any) -> None:
    runtime = _make_builtin_runtime(tmp_path, ScriptedProvider([]))
    runtime.store.update_config({"config": {"policy": {"approvalMode": "none"}}})
    session = _open_session(runtime, tmp_path)
    task = runtime.store.create_task(
        session_id=session["id"],
        task_type="chat",
        goal="Run a command",
        plan=[],
    )

    result = runtime.orchestrator._execute_tool(
        session_id=session["id"],
        task=task,
        tool_spec={
            "id": "call_command",
            "name": "run_command",
            "arguments": {
                "workspaceRoot": str(tmp_path / "workspace"),
                "command": "Write-Output streamed-output",
                "cwd": ".",
                "shell": "powershell",
                "timeoutMs": 10_000,
            },
        },
    )

    assert result["result"]["status"] == "completed"
    command_started_index = next(
        index for index, event in enumerate(runtime.events)
        if event["type"] == "command.started" and event["payload"].get("toolUseId") == "call_command"
    )
    command_output_index = next(
        index for index, event in enumerate(runtime.events)
        if event["type"] == "command.output" and event["payload"].get("toolUseId") == "call_command"
    )
    command_completed_index = next(
        index for index, event in enumerate(runtime.events)
        if event["type"] == "command.completed" and event["payload"].get("toolUseId") == "call_command"
    )
    tool_completed_index = next(
        index for index, event in enumerate(runtime.events)
        if event["type"] == "tool.completed" and event["payload"].get("toolCallId") == "call_command"
    )
    command_started = runtime.events[command_started_index]
    output_event = runtime.events[command_output_index]
    command_completed = runtime.events[command_completed_index]
    assert command_started_index < command_output_index < command_completed_index < tool_completed_index
    assert command_started["payload"]["commandId"].startswith("cmd_")
    assert command_started["visibility"] == "trace"
    assert command_started["payload"]["status"] == "running"
    assert command_started["payload"]["background"] is False
    assert output_event["payload"]["commandId"].startswith("cmd_")
    assert output_event["visibility"] == "trace"
    assert output_event["payload"]["stream"] == "stdout"
    assert output_event["payload"]["chunk"] == "streamed-output\n"
    assert command_completed["payload"]["commandId"] == output_event["payload"]["commandId"]
    assert command_completed["visibility"] == "trace"
    assert command_completed["payload"]["status"] == "completed"
    assert command_completed["payload"]["exitCode"] == 0
    assert command_completed["payload"]["background"] is False
    delta_events = [
        event for event in runtime.events
        if event["type"] == "content_delta"
        and event["payload"].get("toolUseId") == "call_command"
        and event["payload"].get("outputStream") == "stdout"
        and event["payload"].get("toolOutput")
    ]
    assert delta_events
    assert delta_events[-1]["payload"]["toolOutput"] == "streamed-output\n"
    trace_events = runtime.store.list_trace_events({"taskId": task["id"], "limit": 100})["traceEvents"]
    trace_started = next(event for event in trace_events if event["type"] == "command.started")
    trace_completed = next(event for event in trace_events if event["type"] == "command.completed")
    assert trace_started["payload"]["toolUseId"] == "call_command"
    assert trace_started["payload"]["toolName"] == "run_command"
    assert trace_started["payload"]["target"] == "Write-Output streamed-output"
    assert trace_started["payload"]["inputSummary"] == "Write-Output streamed-output"
    assert trace_completed["payload"]["toolUseId"] == "call_command"
    assert trace_completed["payload"]["target"] == "Write-Output streamed-output"


def test_tool_progress_bridges_realtime_activity_output_delta(tmp_path: Any) -> None:
    runtime = _make_runtime(tmp_path, ScriptedProvider([]))
    session = _open_session(runtime, tmp_path)
    task = runtime.store.create_task(
        session_id=session["id"],
        task_type="chat",
        goal="Fetch docs",
        plan=[],
    )

    runtime.orchestrator._publish(
        session_id=session["id"],
        task=task,
        event_type="tool.progress",
        payload={
            "toolUseId": "call_fetch",
            "toolName": "web_fetch",
            "message": "connected to example.com\n",
            "toolCategory": "web",
            "toolPhaseId": "web_fetch",
            "toolPhaseLabel": "网页读取",
            "toolSemanticParentId": "group:tgrp_1:phase:web_fetch",
            "toolSemanticParentLabel": "网页读取",
            "toolGroupId": "tgrp_1",
            "toolIndex": 0,
            "toolTotal": 1,
        },
    )

    delta_events = [
        event for event in runtime.events
        if event["type"] == "content_delta" and event["payload"].get("toolUseId") == "call_fetch"
    ]
    assert delta_events
    assert delta_events[-1]["payload"]["toolOutput"] == "connected to example.com\n"
    assert delta_events[-1]["payload"]["outputStream"] == "activity"
    assert delta_events[-1]["payload"]["toolCategory"] == "web"
    assert delta_events[-1]["payload"]["toolSemanticParentId"] == "group:tgrp_1:phase:web_fetch"
    assert delta_events[-1]["payload"]["toolGroupId"] == "tgrp_1"
    assert delta_events[-1]["payload"]["toolIndex"] == 0
    assert delta_events[-1]["payload"]["toolTotal"] == 1


def test_tool_output_bridges_realtime_result_preview_delta(tmp_path: Any) -> None:
    runtime = _make_runtime(tmp_path, ScriptedProvider([]))
    session = _open_session(runtime, tmp_path)
    task = runtime.store.create_task(
        session_id=session["id"],
        task_type="chat",
        goal="Fetch docs",
        plan=[],
    )

    runtime.orchestrator._publish(
        session_id=session["id"],
        task=task,
        event_type="tool.output",
        payload={
            "toolCallId": "call_fetch",
            "toolName": "web_fetch",
            "chunk": "状态: HTTP 200\n",
            "parentToolUseId": "call_parent",
            "toolCategory": "web",
        },
    )

    delta_events = [
        event for event in runtime.events
        if event["type"] == "content_delta" and event["payload"].get("toolUseId") == "call_fetch"
    ]
    assert delta_events
    assert delta_events[-1]["payload"]["toolOutput"] == "状态: HTTP 200\n"
    assert delta_events[-1]["payload"]["outputStream"] == "result_preview"
    assert delta_events[-1]["payload"]["parentToolUseId"] == "call_parent"
    assert delta_events[-1]["payload"]["toolCategory"] == "web"


def test_react_loop_computer_use_tool_requests_permission_and_resumes(tmp_path: Any) -> None:
    provider = ScriptedProvider(
        [
            {
                "tool_calls": [
                    {
                        "id": "call_computer",
                        "name": "computer_use",
                        "arguments": {
                            "action": "inspect",
                            "target": "VS Code",
                            "permission": "Read the current VS Code window",
                            "details": "Inspect visible editor state before continuing.",
                        },
                    }
                ]
            },
            {"final": "Computer Use permission path completed."},
        ]
    )
    runtime = _make_builtin_runtime(tmp_path, provider)
    session = _open_session(runtime, tmp_path)

    task = _call_result(
        _rpc(
            runtime,
            "message.send",
            {"sessionId": session["id"], "content": "inspect the app"},
        ),
        "task",
    )

    assert task["status"] == "waiting_approval"
    approval_events = [
        event for event in runtime.events
        if event["type"] == "approval.requested" and event["payload"].get("kind") == "computer_use"
    ]
    assert approval_events
    approval_id = approval_events[-1]["payload"]["approvalId"]
    permission_events = [event for event in runtime.events if event["type"] == "computer_use_permission_request"]
    assert permission_events
    assert permission_events[-1]["payload"]["approvalId"] == approval_id
    assert permission_events[-1]["payload"]["app"] == "VS Code"

    _rpc(runtime, "approval.submit", {"approvalId": approval_id, "decision": "approved"})

    final_task = _call_result(_rpc(runtime, "task.get", {"taskId": task["id"]}), "task")
    assert final_task["status"] == "completed"
    assert final_task["resultSummary"] == "Computer Use permission path completed."
    resolved_events = [event for event in runtime.events if event["type"] == "computer_use_permission"]
    assert resolved_events
    assert resolved_events[-1]["payload"]["approvalId"] == approval_id
    assert resolved_events[-1]["payload"]["decision"] == "approved"
    assert provider.calls[1]["context"]["tool_results"][0]["name"] == "computer_use"
    assert provider.calls[1]["context"]["tool_results"][0]["result"]["status"] == "completed"
    assert provider.calls[1]["context"]["tool_results"][0]["result"]["action"] == "inspect"
    assert "environment" in provider.calls[1]["context"]["tool_results"][0]["result"]
    assert provider.calls[1]["context"]["tool_results"][0]["toolOperationId"] == "computer_use:vs code:inspect"
    assert provider.calls[1]["context"]["tool_results"][0]["resultPreview"] == [
        {"label": "状态", "value": "completed"},
        {"label": "动作", "value": "inspect"},
        {"label": "目标", "value": "VS Code"},
        {"label": "摘要", "value": "Computer Use permission approved for Read the current VS Code window."},
    ]
    assert provider.calls[1]["context"]["tool_results"][0]["resultSummary"] == "Computer Use permission approved for Read the current VS Code window."
    computer_progress = [
        event["payload"]
        for event in runtime.events
        if event["type"] == "tool.progress" and event["payload"].get("toolName") == "computer_use"
    ]
    assert len(computer_progress) >= 3
    assert computer_progress[0]["toolCategory"] == "computer_use"
    assert computer_progress[0]["toolOperationId"] == "computer_use:vs code:inspect"
    assert "VS Code" in computer_progress[0]["message"]
    assert any("permission" in payload.get("message", "") for payload in computer_progress)
    assert any("environment" in payload.get("message", "") for payload in computer_progress)
    computer_activity = [
        event["payload"]
        for event in runtime.events
        if event["type"] == "content_delta"
        and event["payload"].get("toolName") == "computer_use"
        and event["payload"].get("outputStream") == "activity"
        and event["payload"].get("toolOutput") == computer_progress[0]["message"]
    ]
    assert computer_activity
    assert computer_activity[0]["toolCategory"] == "computer_use"
    computer_output = next(
        event["payload"]
        for event in runtime.events
        if event["type"] == "tool.output" and event["payload"].get("toolName") == "computer_use"
    )
    assert computer_output["toolUseId"] == "call_computer"
    assert computer_output["toolCategory"] == "computer_use"
    assert computer_output["toolOperationId"] == "computer_use:vs code:inspect"
    assert computer_output["outputStream"] == "result_preview"
    assert "状态: completed" in computer_output["chunk"]
    assert "动作: inspect" in computer_output["chunk"]
    assert "目标: VS Code" in computer_output["chunk"]
    computer_preview_deltas = [
        event["payload"]
        for event in runtime.events
        if event["type"] == "content_delta"
        and event["payload"].get("toolUseId") == "call_computer"
        and event["payload"].get("outputStream") == "result_preview"
    ]
    assert len(computer_preview_deltas) == 1
    assert computer_preview_deltas[0]["toolOutput"] == computer_output["chunk"]
    assert computer_preview_deltas[0]["toolCategory"] == "computer_use"


def test_react_loop_computer_use_blocked_preview_includes_action_and_recovery(tmp_path: Any) -> None:
    provider = ScriptedProvider(
        [
            {
                "tool_calls": [
                    {
                        "id": "call_computer_blocked",
                        "name": "computer_use",
                        "arguments": {
                            "action": "click",
                            "target": "VS Code",
                            "selector": "Run button",
                            "url": "http://localhost:5173",
                            "pageId": "page_1",
                            "permission": "Click the visible run button",
                        },
                    }
                ]
            },
            {"final": "Computer Use blocked path recorded."},
        ]
    )
    runtime = _make_builtin_runtime(tmp_path, provider)
    session = _open_session(runtime, tmp_path)

    task = _call_result(
        _rpc(
            runtime,
            "message.send",
            {"sessionId": session["id"], "content": "click the app"},
        ),
        "task",
    )

    assert task["status"] in {"completed", "waiting_approval"}
    approval_id = next(
        event["payload"]["approvalId"]
        for event in runtime.events
        if event["type"] == "approval.requested" and event["payload"].get("kind") == "computer_use"
    )

    _rpc(runtime, "approval.submit", {"approvalId": approval_id, "decision": "approved"})

    final_task = _call_result(_rpc(runtime, "task.get", {"taskId": task["id"]}), "task")
    assert final_task["status"] == "completed"
    blocked = [
        event["payload"]
        for event in runtime.events
        if event["type"] == "tool.blocked" and event["payload"].get("toolCallId") == "call_computer_blocked"
    ][-1]
    assert blocked["resultSummary"].startswith("blocked click VS Code: selector_executor_required")
    assert blocked["resultPreview"][:5] == [
        {"label": "状态", "value": "blocked"},
        {"label": "动作", "value": "click"},
        {"label": "目标", "value": "VS Code"},
        {"label": "选择器", "value": "Run button"},
        {"label": "URL", "value": "http://localhost:5173"},
    ]
    assert blocked["resultPreview"][5:8] == [
        {"label": "Page", "value": "page_1"},
        {"label": "类型", "value": "selector_executor_required"},
        {"label": "修复", "value": "Provide x/y coordinates from a screenshot, or enable a browser DOM/accessibility executor that supports click.selector for selector 'Run button'."},
    ]
    tool_result = provider.calls[1]["context"]["tool_results"][0]
    assert tool_result["resultSummary"].startswith("blocked click VS Code: selector_executor_required")
    assert tool_result["resultPreview"][1] == {"label": "动作", "value": "click"}


def test_react_loop_continues_with_non_task_tools_after_child_result(tmp_path: Any) -> None:
    provider = ScriptedProvider([
        {
            "message": "I will delegate a focused inspection first.",
            "tool_calls": [
                {
                    "id": "call_child",
                    "name": "task",
                    "arguments": {
                        "title": "Inspect inventory",
                        "prompt": "Inspect the inventory module and report the relevant files.",
                        "agentType": "explorer",
                        "childToolAllowlist": ["read_file", "git_status"],
                    },
                }
            ],
        },
        {"final": "Child result reviewed; continuing work is complete."},
    ])
    runtime = _make_builtin_runtime(tmp_path, provider)
    session = _open_session(runtime, tmp_path)
    task = runtime.store.create_task(
        session_id=session["id"],
        task_type="edit",
        goal="Use a child inspection and then continue locally",
        plan=[],
    )
    dispatched: list[dict[str, Any]] = []

    def _dispatch(params: dict[str, Any]) -> dict[str, Any]:
        dispatched.append(params)
        return {
            "status": "completed",
            "summary": "Child inspected inventory.py and found the target function.",
            "childTaskId": "task_child_1",
        }

    runtime.server._orchestrator._subagent_service.dispatch = _dispatch  # noqa: SLF001
    context = {
        "workspace_root": str(tmp_path / "workspace"),
        "config": runtime.store.get_config({})["config"],
        "routing": {
            "toolContinuation": {
                "allowToolsAfterTaskResults": True,
                "allowMoreSubtasksAfterTaskResults": False,
                "maxTaskToolCalls": 1,
            },
        },
    }

    result = runtime.server._orchestrator._run_react_loop(  # noqa: SLF001
        session_id=session["id"],
        task=task,
        goal="Use a child inspection and then continue locally",
        context=context,
    )

    assert result["status"] == "completed"
    assert len(dispatched) == 1
    assert len(provider.calls) == 2
    first_policy = provider.calls[0]["context"]["tool_policy_decision"]
    second_policy = provider.calls[1]["context"]["tool_policy_decision"]
    second_tool_names = {
        tool.get("name") or tool.get("function", {}).get("name")
        for tool in provider.calls[1]["context"]["tools"]
    }
    assert first_policy["phase"] == "investigation"
    assert "task" in first_policy["allowedToolNames"]
    assert second_policy["phase"] == "post_task_continuation"
    assert "task" not in second_policy["allowedToolNames"]
    assert "task" in second_policy["deniedToolNames"]
    assert "task" not in second_tool_names
    assert {"read_file", "apply_patch", "run_command"}.issubset(second_tool_names)
    task_result = provider.calls[1]["context"]["tool_results"][0]
    assert task_result["name"] == "task"
    assert task_result["resultPreview"] == [
        {"label": "状态", "value": "completed"},
        {"label": "摘要", "value": "Child inspected inventory.py and found the target function."},
        {"label": "子任务", "value": "task_child_1"},
    ]
    progress = next(
        event["payload"]
        for event in runtime.events
        if event["type"] == "tool.progress" and event["payload"].get("toolName") == "task"
    )
    assert progress["toolUseId"] == "call_child"
    assert progress["toolCategory"] == "subtask"
    assert "Inspect inventory" in progress["message"]
    activity = next(
        event["payload"]
        for event in runtime.events
        if event["type"] == "content_delta"
        and event["payload"].get("toolUseId") == "call_child"
        and event["payload"].get("outputStream") == "activity"
        and event["payload"].get("toolOutput") == progress["message"]
    )
    assert activity["toolCategory"] == "subtask"
    task_activity_outputs = [
        event["payload"]["toolOutput"]
        for event in runtime.events
        if event["type"] == "content_delta"
        and event["payload"].get("toolUseId") == "call_child"
        and event["payload"].get("outputStream") == "activity"
    ]
    assert "prepare (completed): Inspect inventory\n" in task_activity_outputs
    assert "dispatch (completed): Child inspected inventory.py and found the target function.\n" in task_activity_outputs
    assert "child_task (completed): Child task recorded.\n" in task_activity_outputs
    task_output = next(
        event["payload"]
        for event in runtime.events
        if event["type"] == "tool.output" and event["payload"].get("toolName") == "task"
    )
    assert task_output["toolUseId"] == "call_child"
    assert task_output["toolCategory"] == "subtask"
    assert task_output["outputStream"] == "result_preview"
    assert "状态: completed" in task_output["chunk"]
    assert "摘要: Child inspected inventory.py and found the target function." in task_output["chunk"]
    assert "子任务: task_child_1" in task_output["chunk"]
    task_preview_deltas = [
        event["payload"]
        for event in runtime.events
        if event["type"] == "content_delta"
        and event["payload"].get("toolUseId") == "call_child"
        and event["payload"].get("outputStream") == "result_preview"
    ]
    assert len(task_preview_deltas) == 1
    assert task_preview_deltas[0]["toolOutput"] == task_output["chunk"]
    assert task_preview_deltas[0]["toolCategory"] == "subtask"


def test_explicit_multi_agent_message_uses_model_tool_loop_instead_of_fixed_planner(tmp_path: Any) -> None:
    provider = ScriptedProvider([{"final": "I can coordinate agents if the next step needs delegation."}])
    runtime = _make_builtin_runtime(tmp_path, provider)
    session = _open_session(runtime, tmp_path)

    task = _call_result(
        _rpc(runtime, "message.send", {"sessionId": session["id"], "content": "Use multiple agents to optimize this project"}),
        "task",
    )

    assert task["status"] == "completed"
    assert len(provider.calls) == 1
    provider_context = provider.calls[0]["context"]
    assert provider_context["routing"]["mode"] == "model_first"
    assert "strategy" not in provider_context["routing"]
    assert provider_context["routing"].get("enable_planning") is not True
    tool_policy = provider_context["tool_policy_decision"]
    assert tool_policy["phase"] == "investigation"
    assert {"agent", "task"}.issubset(set(tool_policy["allowedToolNames"]))
    event_types = {event["type"] for event in runtime.events}
    assert "task.planning.started" not in event_types
    assert "approval.requested" not in event_types


def test_chinese_multi_agent_message_uses_model_tool_loop_instead_of_fixed_planner(tmp_path: Any) -> None:
    provider = ScriptedProvider([{"final": "可以按需要协调多个 agent。"}])
    runtime = _make_builtin_runtime(tmp_path, provider)
    session = _open_session(runtime, tmp_path)

    task = _call_result(
        _rpc(runtime, "message.send", {"sessionId": session["id"], "content": "起多个 agent 优化这个项目"}),
        "task",
    )

    assert task["status"] == "completed"
    assert len(provider.calls) == 1
    provider_context = provider.calls[0]["context"]
    assert provider_context["routing"]["mode"] == "model_first"
    assert "strategy" not in provider_context["routing"]
    assert provider_context["routing"].get("enable_planning") is not True
    assert provider_context["routing"].get("orchestrationMode") in {None, "model_tools"}
    tool_policy = provider_context["tool_policy_decision"]
    assert tool_policy["phase"] == "investigation"
    assert {"agent", "task"}.issubset(set(tool_policy["allowedToolNames"]))
    event_types = {event["type"] for event in runtime.events}
    assert "task.planning.started" not in event_types
    assert "approval.requested" not in event_types


def test_model_requested_task_tool_dispatches_without_keyword_router(tmp_path: Any) -> None:
    provider = ScriptedProvider([
        {
            "message": "I will try to delegate.",
            "tool_calls": [
                {
                    "id": "call_task",
                    "name": "task",
                    "arguments": {
                        "title": "Inspect current project",
                        "prompt": "Inspect the project and summarize findings.",
                    },
                }
            ],
        },
        {"final": "I used the child task result."},
    ])
    runtime = _make_builtin_runtime(tmp_path, provider)
    session = _open_session(runtime, tmp_path)

    dispatched: list[dict[str, Any]] = []

    def _dispatch(params: dict[str, Any]) -> dict[str, Any]:
        dispatched.append(params)
        return {
            "status": "completed",
            "summary": "Child inspected the project.",
            "childTaskId": "task_child_1",
        }

    runtime.server._orchestrator._subagent_service.dispatch = _dispatch  # noqa: SLF001

    task = _call_result(
        _rpc(runtime, "message.send", {"sessionId": session["id"], "content": "总结一下当前项目"}),
        "task",
    )

    assert task["status"] == "completed"
    assert len(provider.calls) == 2
    first_policy = provider.calls[0]["context"]["tool_policy_decision"]
    assert "task" in first_policy["allowedToolNames"]
    assert len(dispatched) == 1
    assert dispatched[0]["title"] == "Inspect current project"
    tool_result = provider.calls[1]["context"]["tool_results"][0]
    assert tool_result["name"] == "task"
    assert tool_result["result"]["status"] == "completed"
    assert tool_result["resultSummary"] == "Child inspected the project."
    completed = next(
        event["payload"]
        for event in runtime.events
        if event["type"] == "tool.completed"
        and event["payload"].get("toolCallId") == "call_task"
    )
    assert completed["toolName"] == "task"
    assert completed["resultSummary"] == "Child inspected the project."
    assert completed["toolCategory"] == "subtask"
    compat_tool_start = next(
        event["payload"]
        for event in runtime.events
        if event["type"] == "content_start"
        and event["payload"].get("blockType") == "tool_use"
        and event["payload"].get("toolUseId") == "call_task"
    )
    assert compat_tool_start["toolCategory"] == "subtask"
    assert not [event for event in runtime.events if event["type"] == "assistant_progress"]
    assert dispatched


def test_react_loop_does_not_force_plan_steps_after_search(tmp_path: Any) -> None:
    provider = ScriptedProvider([
        {
            "message": "Search first.",
            "tool_calls": [
                {
                    "id": "call_search",
                    "name": "search_files",
                    "arguments": {"query": "blog", "maxResults": 5},
                }
            ],
        },
        {"final": "I found the relevant files."},
    ])
    runtime = _make_builtin_runtime(tmp_path, provider)
    session = _open_session(runtime, tmp_path)

    workspace_root = tmp_path / "workspace"
    (workspace_root / "README.md").write_text("blog\n", encoding="utf-8")

    task = _call_result(
        _rpc(
            runtime,
            "message.send",
            {"sessionId": session["id"], "content": "implement a small blog backend"},
        ),
        "task",
    )

    assert task["status"] == "completed"
    assert task["plan"] == []
    started_tools = [
        event["payload"]["toolName"]
        for event in runtime.events
        if event["type"] == "tool.started"
    ]
    assert started_tools == ["search_files"]
    task_plan_updates = [
        event
        for event in runtime.events
        if event["type"] == "task.updated" and isinstance(event.get("payload", {}).get("plan"), list)
    ]
    assert all(event["payload"]["plan"] == [] for event in task_plan_updates)


def test_react_loop_injects_task_focus_into_provider_context(tmp_path: Any) -> None:
    provider = ScriptedProvider([{"final": "I will stay on the requested change."}])
    runtime = _make_runtime(tmp_path, provider)
    session = _open_session(runtime, tmp_path)

    task = _call_result(
        _rpc(
            runtime,
            "message.send",
            {"sessionId": session["id"], "content": "add a focused project checklist"},
        ),
        "task",
    )

    first_context = provider.calls[0]["context"]
    user_context = first_context["messages"][-1]["content"]
    assert "Task focus:" in user_context
    assert "- goal: add a focused project checklist" in user_context
    assert "Acceptance criteria:" in user_context
    assert "Out of scope:" in user_context
    assert task["acceptanceCriteria"]
    assert task["outOfScope"]
    assert task.get("currentStep") is None


def test_next_turn_context_keeps_recent_conversation_before_current_request(tmp_path: Any) -> None:
    provider = ScriptedProvider(
        [
            {"final": "I will remember the alpha checklist."},
            {"final": "You asked me to remember the alpha checklist."},
        ]
    )
    runtime = _make_runtime(tmp_path, provider)
    session = _open_session(runtime, tmp_path)

    _call_result(
        _rpc(
            runtime,
            "message.send",
            {"sessionId": session["id"], "content": "remember the alpha checklist"},
        ),
        "task",
    )
    _call_result(
        _rpc(
            runtime,
            "message.send",
            {"sessionId": session["id"], "content": "what did I ask you to remember?"},
        ),
        "task",
    )

    second_messages = provider.calls[1]["context"]["messages"]
    stable_context_text = "\n".join(message["content"] for message in second_messages[:-1])
    current_request_text = second_messages[-1]["content"]
    assert "Recent conversation:" in stable_context_text
    assert "User: remember the alpha checklist" in stable_context_text
    assert "Assistant: I will remember the alpha checklist." in stable_context_text
    assert "Current user request:" not in stable_context_text
    assert current_request_text.startswith("Current user request:\nwhat did I ask you to remember?")
    assert "Task focus:" in current_request_text


def test_message_send_attaches_supplement_to_open_task_without_replanning(tmp_path: Any) -> None:
    provider = ScriptedProvider([])
    runtime = _make_runtime(tmp_path, provider)
    session = _open_session(runtime, tmp_path)
    original_plan = [
        {"id": "inspect-workspace", "title": "Inspect snake game structure", "status": "completed"},
        {"id": "search-relevant-files", "title": "Find snake gameplay files", "status": "active"},
        {"id": "apply-patch", "title": "Implement AI snake opponent", "status": "pending"},
        {"id": "run-command", "title": "Verify the change", "status": "pending"},
        {"id": "summarize-findings", "title": "Report completion", "status": "pending"},
    ]
    open_task = runtime.store.create_task(
        session_id=session["id"],
        task_type="edit",
        goal="Add backgrounds and AI snake battle",
        plan=original_plan,
        current_step="Find snake gameplay files",
    )

    supplement_resp = _rpc(
        runtime,
        "message.send",
        {"sessionId": session["id"], "content": "also make the AI compare scores"},
    )
    returned_task = _call_result(supplement_resp, "task")
    # Verify acceptedMode is returned for supplement
    assert supplement_resp["result"]["acceptedMode"] == "supplement"

    assert returned_task["id"] == open_task["id"]
    assert returned_task["plan"] == original_plan
    assert returned_task["currentStep"] == "Find snake gameplay files"
    assert runtime.store.list_tasks({"sessionId": session["id"]})["tasks"][0]["id"] == open_task["id"]
    messages = _call_result(_rpc(runtime, "message.list", {"sessionId": session["id"]}), "messages")
    assert messages[-2]["taskId"] == open_task["id"]
    assert messages[-2]["content"] == "also make the AI compare scores"
    assert messages[-1]["role"] == "assistant"
    assert messages[-1]["taskId"] == open_task["id"]
    assert "\u539f\u4efb\u52a1\u8ba1\u5212" in messages[-1]["content"]
    completed_events = [event for event in runtime.events if event["type"] == "assistant.message.completed"]
    assert completed_events[-1]["payload"]["supplemental"] is True
    assert not provider.calls


def test_planner_skips_fixed_step_titles_for_specific_game_requests() -> None:
    plan = Planner().plan("Add backgrounds and AI snake battle")
    assert plan == []


def test_message_send_explicit_supplement_overrides_background_new_task(tmp_path: Any) -> None:
    provider = ScriptedProvider([])
    runtime = _make_runtime(tmp_path, provider)
    session = _open_session(runtime, tmp_path)
    open_task = runtime.store.create_task(
        session_id=session["id"],
        task_type="edit",
        goal="Fix current UI",
        plan=[{"id": "edit", "title": "Patch UI", "status": "active"}],
        current_step="Patch UI",
    )

    returned_task = _call_result(
        _rpc(
            runtime,
            "message.send",
            {
                "sessionId": session["id"],
                "content": "also keep it in the current conversation",
                "taskId": open_task["id"],
                "mode": "supplement",
                "background": True,
            },
        ),
        "task",
    )

    assert returned_task["id"] == open_task["id"]
    assert len(runtime.store.list_tasks({"sessionId": session["id"]})["tasks"]) == 1
    messages = _call_result(_rpc(runtime, "message.list", {"sessionId": session["id"]}), "messages")
    assert messages[-2]["taskId"] == open_task["id"]
    assert messages[-2]["content"] == "also keep it in the current conversation"
    assert not provider.calls


def test_completed_task_updates_session_memory_for_next_context(tmp_path: Any) -> None:
    provider = ScriptedProvider(
        [
            {"final": "Created the checklist and verified the focused flow."},
            {"final": "I can continue from the checklist work."},
        ]
    )
    runtime = _make_runtime(tmp_path, provider)
    session = _open_session(runtime, tmp_path)

    first_task = _call_result(
        _rpc(
            runtime,
            "message.send",
            {"sessionId": session["id"], "content": "add a focused project checklist"},
        ),
        "task",
    )

    assert first_task["status"] == "completed"
    assert not [
        event for event in runtime.events
        if event["type"] == "approval.requested"
        and event["payload"].get("kind") == "completion_review"
    ]

    remembered_session = runtime.store.require_session(session["id"])
    assert "Task memory:" in remembered_session["summary"]
    assert "completed: add a focused project checklist" in remembered_session["summary"]
    assert "Created the checklist and verified the focused flow." in remembered_session["summary"]

    second_task = _call_result(
        _rpc(
            runtime,
            "message.send",
            {"sessionId": session["id"], "content": "continue from the checklist"},
        ),
        "task",
    )

    # In lightweight mode, memory is not injected into messages, but it IS stored.
    # Verify the session summary contains the task memory.
    remembered = runtime.store.require_session(session["id"])
    assert "Task memory:" in remembered["summary"]
    assert "add a focused project checklist" in remembered["summary"]


def test_completed_task_updates_workspace_memory_for_new_session_context(tmp_path: Any) -> None:
    provider = ScriptedProvider(
        [
            {"final": "Documented the product direction and next milestone."},
            {"final": "I can continue with the remembered direction."},
        ]
    )
    runtime = _make_runtime(tmp_path, provider)
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    workspace = _call_result(
        _rpc(runtime, "workspace.open", {"path": str(workspace_root)}),
        "workspace",
    )
    first_session = _call_result(
        _rpc(
            runtime,
            "session.create",
            {"workspaceId": workspace["id"], "title": "First session"},
        ),
        "session",
    )

    first_task = _call_result(
        _rpc(
            runtime,
            "message.send",
            {"sessionId": first_session["id"], "content": "define the product iteration direction"},
        ),
        "task",
    )
    remembered_workspace = runtime.store.require_workspace(workspace["id"])
    assert "Project memory:" in remembered_workspace["summary"]
    assert "completed: define the product iteration direction" in remembered_workspace["summary"]

    second_session = _call_result(
        _rpc(
            runtime,
            "session.create",
            {"workspaceId": workspace["id"], "title": "Second session"},
        ),
        "session",
    )
    second_task = _call_result(
        _rpc(
            runtime,
            "message.send",
            {"sessionId": second_session["id"], "content": "continue product work"},
        ),
        "task",
    )

    # In lightweight mode, memory is not injected into messages, but it IS stored.
    # Verify the workspace summary contains the project memory.
    remembered_ws = runtime.store.require_workspace(workspace["id"])
    assert first_task["id"] != second_task["id"]
    assert "Project memory:" in remembered_ws["summary"]
    assert "define the product iteration direction" in remembered_ws["summary"]


def test_workspace_memory_deduplicates_repeated_task_entries(tmp_path: Any) -> None:
    provider = ScriptedProvider(
        [
            {"final": "Documented the product direction and next milestone."},
            {"final": "Documented the product direction and next milestone."},
        ]
    )
    runtime = _make_runtime(tmp_path, provider)
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    workspace = _call_result(
        _rpc(runtime, "workspace.open", {"path": str(workspace_root)}),
        "workspace",
    )

    for title in ("First session", "Second session"):
        session = _call_result(
            _rpc(
                runtime,
                "session.create",
                {"workspaceId": workspace["id"], "title": title},
            ),
            "session",
        )
        _call_result(
            _rpc(
                runtime,
                "message.send",
                {"sessionId": session["id"], "content": "define the product iteration direction"},
            ),
            "task",
        )

    remembered_workspace = runtime.store.require_workspace(workspace["id"])
    assert remembered_workspace["summary"].count("define the product iteration direction") == 1


def test_workspace_memory_can_be_cleared_and_removed_from_future_context(tmp_path: Any) -> None:
    provider = ScriptedProvider(
        [
            {"final": "Documented the product direction and next milestone."},
            {"final": "I do not see cleared project memory."},
        ]
    )
    runtime = _make_runtime(tmp_path, provider)
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    workspace = _call_result(
        _rpc(runtime, "workspace.open", {"path": str(workspace_root)}),
        "workspace",
    )
    first_session = _call_result(
        _rpc(
            runtime,
            "session.create",
            {"workspaceId": workspace["id"], "title": "First session"},
        ),
        "session",
    )
    _call_result(
        _rpc(
            runtime,
            "message.send",
            {"sessionId": first_session["id"], "content": "define the product iteration direction"},
        ),
        "task",
    )

    cleared_workspace = _call_result(
        _rpc(runtime, "workspace.memory.clear", {"workspaceId": workspace["id"]}),
        "workspace",
    )
    assert cleared_workspace["summary"] is None

    second_session = _call_result(
        _rpc(
            runtime,
            "session.create",
            {"workspaceId": workspace["id"], "title": "Second session"},
        ),
        "session",
    )
    _call_result(
        _rpc(
            runtime,
            "message.send",
            {"sessionId": second_session["id"], "content": "continue product work"},
        ),
        "task",
    )

    # In lightweight mode, memory is not injected into messages.
    # After clearing and running a new task, old entries should not reappear.
    final_ws = runtime.store.require_workspace(workspace["id"])
    # The old "define the product iteration direction" entry was cleared
    assert "define the product iteration direction" not in (final_ws["summary"] or "")


def test_workspace_focus_update_rpc_injects_future_task_context(tmp_path: Any) -> None:
    provider = ScriptedProvider([{"final": "I will keep the product focus in mind."}])
    runtime = _make_runtime(tmp_path, provider)
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    workspace = _call_result(
        _rpc(runtime, "workspace.open", {"path": str(workspace_root)}),
        "workspace",
    )

    updated_workspace = _call_result(
        _rpc(
            runtime,
            "workspace.focus.update",
            {
                "workspaceId": workspace["id"],
                "focus": "Keep attention on durable context and long-running product work.",
            },
        ),
        "workspace",
    )
    session = _call_result(
        _rpc(
            runtime,
            "session.create",
            {"workspaceId": workspace["id"], "title": "Focused session"},
        ),
        "session",
    )
    _call_result(
        _rpc(
            runtime,
            "message.send",
            {"sessionId": session["id"], "content": "continue"},
        ),
        "task",
    )

    first_context = provider.calls[0]["context"]
    started_event = next(event for event in runtime.events if event["type"] == "task.started")
    assert updated_workspace["focus"] == "Keep attention on durable context and long-running product work."
    # Full context stays out of visible lifecycle events; the model receives the
    # focus and budget bundle through provider context.
    assert "context" not in started_event["payload"]
    assert first_context["project_focus"] == "Keep attention on durable context and long-running product work."
    event_context = {"budgetStats": first_context["budgetStats"]}
    assert event_context["budgetStats"]["estimatedInputTokens"] > 0
    assert event_context["budgetStats"]["messageTokens"] > 0
    assert event_context["budgetStats"]["toolSchemaTokens"] >= 0


def test_workspace_memory_limit_drops_whole_entries_without_orphan_detail_lines(tmp_path: Any) -> None:
    provider = ScriptedProvider([])
    runtime = _make_runtime(tmp_path, provider)
    append_memory = runtime.server._orchestrator._append_memory  # noqa: SLF001

    current = "\n".join(
        [
            "Project memory:",
            "- completed: old task",
            "  result: old result",
        ]
    )
    entry = "\n".join(
        [
            "- completed: newest task",
            "  result: this detail line is longer than the small remaining budget",
        ]
    )

    summary = append_memory(current, entry, marker="Project memory:", max_chars=64)

    body_lines = [line for line in summary.splitlines() if line != "Project memory:"]
    assert not body_lines or body_lines[0].startswith("- ")


def test_react_loop_executes_tool_call_and_returns_result_to_provider(tmp_path: Any) -> None:
    tool_calls = [
        {
            "id": "call_search",
            "name": "search_files",
            "arguments": {"query": "needle"},
        }
    ]
    provider = ScriptedProvider(
        [
            {"message": "Searching the workspace.", "tool_calls": tool_calls},
            {"final_answer": "Found needle in alpha.txt."},
        ]
    )

    def search_files(params: dict[str, Any]) -> dict[str, Any]:
        assert params["query"] == "needle"
        assert params["taskId"].startswith("task_")
        assert params["sessionId"].startswith("sess_")
        return {"matches": [{"path": "alpha.txt", "preview": "needle"}], "total": 1}

    runtime = _make_runtime(tmp_path, provider, {"search_files": search_files})
    session = _open_session(runtime, tmp_path)

    task = _call_result(
        _rpc(
            runtime,
            "message.send",
            {"sessionId": session["id"], "content": "find needle"},
        ),
        "task",
    )

    assert task["status"] == "completed"
    assert task["resultSummary"] == "Found needle in alpha.txt."
    assert [event["payload"]["toolName"] for event in runtime.events if event["type"] == "tool.started"] == [
        "search_files"
    ]
    first_text_delta_index = next(
        index
        for index, event in enumerate(runtime.events)
        if event["type"] == "message.delta" and event["payload"].get("delta") == "Searching the workspace."
    )
    assert runtime.events[first_text_delta_index]["yuanbao"]["type"] == "content_delta"
    assert runtime.events[first_text_delta_index]["yuanbao"]["text"] == "Searching the workspace."
    first_tool_block_index = next(
        index
        for index, event in enumerate(runtime.events)
        if event["type"] == "content_start"
        and event["payload"].get("blockType") == "tool_use"
        and event["payload"].get("toolUseId") == "call_search"
    )
    tool_started_index = next(
        index
        for index, event in enumerate(runtime.events)
        if event["type"] == "tool.started" and event["payload"].get("toolCallId") == "call_search"
    )
    assert first_text_delta_index < first_tool_block_index <= tool_started_index
    second_context = provider.calls[1]["context"]
    assert second_context["messages"][-1]["role"] == "tool"
    assert second_context["messages"][-1]["tool_call_id"] == "call_search"
    assert second_context["tool_results"][0]["result"]["matches"][0]["path"] == "alpha.txt"


def test_react_loop_marks_provider_tool_batch_order(tmp_path: Any) -> None:
    tool_calls = [
        {
            "id": "call_search",
            "name": "search_files",
            "arguments": {"query": "needle"},
        },
        {
            "id": "call_read",
            "name": "read_file",
            "arguments": {"path": "alpha.txt"},
        },
    ]
    provider = ScriptedProvider(
        [
            {"message": "Checking the workspace.", "tool_calls": tool_calls},
            {"final_answer": "Found needle in alpha.txt."},
        ]
    )

    runtime = _make_runtime(
        tmp_path,
        provider,
        {
            "search_files": lambda params: {"matches": [{"path": "alpha.txt", "preview": "needle"}], "total": 1},
            "read_file": lambda params: {"path": params["path"], "content": "needle", "bytesRead": 6},
        },
    )
    session = _open_session(runtime, tmp_path)

    task = _call_result(
        _rpc(
            runtime,
            "message.send",
            {"sessionId": session["id"], "content": "find needle"},
        ),
        "task",
    )

    assert task["status"] == "completed"
    started = [event for event in runtime.events if event["type"] == "tool.started"]
    assert [event["payload"]["toolCallId"] for event in started] == ["call_search", "call_read"]
    group_ids = {event["payload"]["toolGroupId"] for event in started}
    assert len(group_ids) == 1
    assert [event["payload"]["toolIndex"] for event in started] == [0, 1]
    assert [event["payload"]["toolTotal"] for event in started] == [2, 2]
    assert [event["payload"]["toolCategory"] for event in started] == ["search", "context_read"]
    assert [event["payload"]["toolPhaseId"] for event in started] == ["search", "context_read"]
    assert [event["payload"]["toolPhaseLabel"] for event in started] == ["搜索", "读取上下文"]
    assert started[0]["payload"].get("parentToolUseId") is None
    assert started[1]["payload"].get("parentToolUseId") is None
    tool_group_id = started[0]["payload"]["toolGroupId"]
    assert [event["payload"]["toolSemanticParentId"] for event in started] == [
        f"group:{tool_group_id}:phase:search",
        f"group:{tool_group_id}:phase:context_read",
    ]
    assert [event["payload"]["toolSemanticParentLabel"] for event in started] == [
        event["payload"]["toolPhaseLabel"] for event in started
    ]

    tool_use_blocks = [event for event in runtime.events if event["type"] == "tool_use_complete"]
    assert [event["payload"]["toolUseId"] for event in tool_use_blocks] == ["call_search", "call_read"]
    assert tool_use_blocks[1]["payload"].get("parentToolUseId") is None
    assert [event["payload"]["toolCategory"] for event in tool_use_blocks] == ["search", "context_read"]
    assert [event["payload"]["toolPhaseLabel"] for event in tool_use_blocks] == ["搜索", "读取上下文"]
    assert [event["payload"]["toolSemanticParentId"] for event in tool_use_blocks] == [
        f"group:{tool_group_id}:phase:search",
        f"group:{tool_group_id}:phase:context_read",
    ]
    assert [event["payload"]["toolSemanticParentLabel"] for event in tool_use_blocks] == [
        event["payload"]["toolPhaseLabel"] for event in tool_use_blocks
    ]
    assert [event["payload"]["toolGroupId"] for event in tool_use_blocks] == [tool_group_id, tool_group_id]
    assert [event["payload"]["toolIndex"] for event in tool_use_blocks] == [0, 1]
    assert [event["payload"]["toolTotal"] for event in tool_use_blocks] == [2, 2]

    tool_results = provider.calls[1]["context"]["tool_results"]
    assert [result["toolGroupId"] for result in tool_results] == [started[0]["payload"]["toolGroupId"]] * 2
    assert [result["toolIndex"] for result in tool_results] == [0, 1]
    assert [result["toolTotal"] for result in tool_results] == [2, 2]
    assert tool_results[1].get("parentToolUseId") is None
    assert [result["toolCategory"] for result in tool_results] == ["search", "context_read"]
    assert [result["toolPhaseLabel"] for result in tool_results] == ["搜索", "读取上下文"]
    assert [result["toolSemanticParentId"] for result in tool_results] == [
        f"group:{tool_group_id}:phase:search",
        f"group:{tool_group_id}:phase:context_read",
    ]
    assert [result["toolSemanticParentLabel"] for result in tool_results] == [
        result["toolPhaseLabel"] for result in tool_results
    ]
    assert [result["target"] for result in tool_results] == ["content: needle", "alpha.txt"]
    assert tool_results[0]["resultSummary"] == "found 1 match(es) for content: needle: alpha.txt"
    assert tool_results[0]["resultPreview"] == [
        {"label": "查询", "value": "content: needle"},
        {"label": "命中", "value": "1 项"},
        {"label": "样例", "value": "alpha.txt"},
    ]
    assert tool_results[1]["inputSummary"] == "read alpha.txt"
    assert tool_results[1]["resultSummary"] == "read alpha.txt (6 bytes)"
    assert tool_results[1]["resultPreview"] == [
        {"label": "文件", "value": "alpha.txt"},
        {"label": "大小", "value": "6 bytes"},
        {"label": "行数", "value": "1 行"},
    ]
    read_output = next(
        event["payload"]
        for event in runtime.events
        if event["type"] == "tool.output" and event["payload"].get("toolName") == "read_file"
    )
    assert read_output.get("parentToolUseId") is None
    assert read_output["toolCategory"] == "context_read"
    assert read_output["outputStream"] == "result_preview"
    assert "文件: alpha.txt" in read_output["chunk"]
    assert "大小: 6 bytes" in read_output["chunk"]
    assert "行数: 1 行" in read_output["chunk"]
    read_output_delta = next(
        event["payload"]
        for event in runtime.events
        if event["type"] == "content_delta"
        and event["payload"].get("toolName") == "read_file"
        and event["payload"].get("outputStream") == "result_preview"
        and event["payload"].get("toolOutput") == read_output["chunk"]
    )
    assert read_output_delta.get("parentToolUseId") is None
    assert read_output_delta["toolCategory"] == "context_read"
    read_preview_deltas = [
        event["payload"]
        for event in runtime.events
        if event["type"] == "content_delta"
        and event["payload"].get("toolName") == "read_file"
        and event["payload"].get("outputStream") == "result_preview"
    ]
    assert len(read_preview_deltas) == 1


def test_react_loop_runs_independent_read_only_tools_in_parallel(tmp_path: Any) -> None:
    tool_calls = [
        {"id": "call_a", "name": "read_file", "arguments": {"path": "a.txt"}},
        {"id": "call_b", "name": "read_file", "arguments": {"path": "b.txt"}},
        {"id": "call_search", "name": "search_files", "arguments": {"query": "needle"}},
    ]
    provider = ScriptedProvider(
        [
            {"message": "Reading in parallel.", "tool_calls": tool_calls},
            {"final_answer": "Done."},
        ]
    )
    starts: dict[str, float] = {}
    finishes: dict[str, float] = {}
    lock = threading.Lock()

    def _record_start(name: str) -> None:
        with lock:
            starts[name] = time.perf_counter()

    def _record_finish(name: str) -> None:
        with lock:
            finishes[name] = time.perf_counter()

    def read_file(params: dict[str, Any]) -> dict[str, Any]:
        path = params["path"]
        _record_start(path)
        time.sleep(0.18)
        _record_finish(path)
        return {"status": "completed", "path": path, "content": path, "bytesRead": len(path)}

    def search_files(params: dict[str, Any]) -> dict[str, Any]:
        _record_start("search")
        time.sleep(0.18)
        _record_finish("search")
        return {"status": "completed", "query": params["query"], "total": 1, "matches": [{"path": "a.txt"}]}

    runtime = _make_runtime(tmp_path, provider, {"read_file": read_file, "search_files": search_files})
    session = _open_session(runtime, tmp_path)

    task = _call_result(
        _rpc(runtime, "message.send", {"sessionId": session["id"], "content": "read files"}),
        "task",
    )

    assert task["status"] == "completed"
    assert set(starts) == {"a.txt", "b.txt", "search"}
    assert max(starts.values()) - min(starts.values()) < 0.12
    assert min(finishes.values()) > max(starts.values())
    second_context = provider.calls[1]["context"]
    assert [result["id"] for result in second_context["tool_results"]] == ["call_a", "call_b", "call_search"]


def test_react_loop_parallel_read_only_batch_aggregates_failed_result(tmp_path: Any) -> None:
    tool_calls = [
        {"id": "call_bad", "name": "read_file", "arguments": {"path": "missing.txt"}},
        {"id": "call_good", "name": "read_file", "arguments": {"path": "ok.txt"}},
        {"id": "call_search", "name": "search_files", "arguments": {"query": "needle"}},
    ]
    provider = ScriptedProvider(
        [
            {"message": "Reading with one failure.", "tool_calls": tool_calls},
            {"final_answer": "Handled mixed results."},
        ]
    )
    starts: dict[str, float] = {}
    lock = threading.Lock()
    barrier = threading.Barrier(3)

    def _record_start(name: str) -> None:
        with lock:
            starts[name] = time.perf_counter()
        barrier.wait(timeout=3)

    def read_file(params: dict[str, Any]) -> dict[str, Any]:
        path = params["path"]
        _record_start(path)
        time.sleep(0.18)
        if path == "missing.txt":
            raise RuntimeError("missing file")
        return {"status": "completed", "path": path, "content": "ok", "bytesRead": 2}

    def search_files(params: dict[str, Any]) -> dict[str, Any]:
        _record_start("search")
        time.sleep(0.18)
        return {"status": "completed", "query": params["query"], "total": 1, "matches": [{"path": "ok.txt"}]}

    runtime = _make_runtime(tmp_path, provider, {"read_file": read_file, "search_files": search_files})
    session = _open_session(runtime, tmp_path)

    task = _call_result(
        _rpc(runtime, "message.send", {"sessionId": session["id"], "content": "read files"}),
        "task",
    )

    assert task["status"] == "completed"
    assert not barrier.broken
    assert set(starts) == {"missing.txt", "ok.txt", "search"}
    second_context = provider.calls[1]["context"]
    tool_results = second_context["tool_results"]
    assert [result["id"] for result in tool_results] == ["call_bad", "call_good", "call_search"]
    assert tool_results[0]["result"]["status"] == "failed"
    assert tool_results[0]["result"]["error"] == "missing file"
    assert tool_results[1]["result"]["status"] == "completed"
    assert tool_results[2]["result"]["status"] == "completed"


def test_react_loop_does_not_parallelize_across_write_tools(tmp_path: Any) -> None:
    tool_calls = [
        {"id": "call_read_before", "name": "read_file", "arguments": {"path": "before.txt"}},
        {"id": "call_patch", "name": "apply_patch", "arguments": {"patchText": "patch"}},
        {"id": "call_read_after", "name": "read_file", "arguments": {"path": "after.txt"}},
    ]
    provider = ScriptedProvider(
        [
            {"message": "Read, write, read.", "tool_calls": tool_calls},
            {"final_answer": "Done."},
        ]
    )
    order: list[str] = []
    lock = threading.Lock()

    def _append(value: str) -> None:
        with lock:
            order.append(value)

    def read_file(params: dict[str, Any]) -> dict[str, Any]:
        _append(f"start:{params['path']}")
        time.sleep(0.05)
        _append(f"finish:{params['path']}")
        return {"status": "completed", "path": params["path"], "content": params["path"], "bytesRead": len(params["path"])}

    def apply_patch(_params: dict[str, Any]) -> dict[str, Any]:
        _append("start:patch")
        time.sleep(0.05)
        _append("finish:patch")
        return {"status": "completed", "ok": True, "filesChanged": 1, "changedPaths": ["after.txt"]}

    runtime = _make_runtime(tmp_path, provider, {"read_file": read_file, "apply_patch": apply_patch})
    session = _open_session(runtime, tmp_path)

    task = _call_result(
        _rpc(runtime, "message.send", {"sessionId": session["id"], "content": "patch then read"}),
        "task",
    )

    assert task["status"] == "completed"
    assert order == [
        "start:before.txt",
        "finish:before.txt",
        "start:patch",
        "finish:patch",
        "start:after.txt",
        "finish:after.txt",
    ]


def test_react_loop_keeps_cross_turn_read_independent_from_prior_search_result(tmp_path: Any) -> None:
    provider = ScriptedProvider(
        [
            {
                "tool_calls": [
                    {"id": "call_search", "name": "search_files", "arguments": {"query": "needle"}},
                ]
            },
            {
                "tool_calls": [
                    {"id": "call_read", "name": "read_file", "arguments": {"path": "alpha.txt"}},
                ]
            },
            {"final": "Done."},
        ]
    )
    runtime = _make_runtime(
        tmp_path,
        provider,
        {
            "search_files": lambda _params: {
                "query": "needle",
                "matches": [{"path": "alpha.txt", "preview": "needle"}],
                "total": 1,
            },
            "read_file": lambda params: {"path": params["path"], "content": "needle", "bytesRead": 6},
        },
    )
    session = _open_session(runtime, tmp_path)

    task = _call_result(
        _rpc(
            runtime,
            "message.send",
            {"sessionId": session["id"], "content": "find needle then read it"},
        ),
        "task",
    )

    assert task["status"] == "completed"
    started_read = next(
        event
        for event in runtime.events
        if event["type"] == "tool.started" and event["payload"].get("toolName") == "read_file"
    )
    completed_read = next(
        event
        for event in runtime.events
        if event["type"] == "tool.completed" and event["payload"].get("toolName") == "read_file"
    )
    assert started_read["payload"].get("parentToolUseId") is None
    assert completed_read["payload"].get("parentToolUseId") is None
    tool_results = provider.calls[2]["context"]["tool_results"]
    assert tool_results[1].get("parentToolUseId") is None


def test_react_loop_parents_cross_turn_verification_to_prior_file_change(tmp_path: Any) -> None:
    provider = ScriptedProvider(
        [
            {
                "tool_calls": [
                    {
                        "id": "call_write",
                        "name": "write_file",
                        "arguments": {"path": "alpha.txt", "content": "needle"},
                    },
                ]
            },
            {
                "tool_calls": [
                    {"id": "call_verify", "name": "run_command", "arguments": {"command": "pytest -q"}},
                ]
            },
            {"final": "Done."},
        ]
    )
    runtime = _make_runtime(
        tmp_path,
        provider,
        {
            "write_file": lambda params: {
                "status": "written",
                "path": params["path"],
                "bytesWritten": len(params["content"]),
            },
            "run_command": lambda _params: {
                "status": "completed",
                "exitCode": 0,
                "stdout": "ok",
                "commandLog": {"id": "cmd_1"},
            },
        },
    )
    session = _open_session(runtime, tmp_path)

    task = _call_result(
        _rpc(
            runtime,
            "message.send",
            {"sessionId": session["id"], "content": "write and verify"},
        ),
        "task",
    )

    assert task["status"] == "completed"
    started_verify = next(
        event
        for event in runtime.events
        if event["type"] == "tool.started" and event["payload"].get("toolName") == "run_command"
    )
    completed_verify = next(
        event
        for event in runtime.events
        if event["type"] == "tool.completed" and event["payload"].get("toolName") == "run_command"
    )
    assert started_verify["payload"]["parentToolUseId"] == "call_write"
    assert completed_verify["payload"]["parentToolUseId"] == "call_write"
    tool_results = provider.calls[2]["context"]["tool_results"]
    assert tool_results[1]["parentToolUseId"] == "call_write"


def test_react_loop_parents_cross_turn_read_of_changed_file(tmp_path: Any) -> None:
    provider = ScriptedProvider(
        [
            {
                "tool_calls": [
                    {
                        "id": "call_write",
                        "name": "write_file",
                        "arguments": {"path": "alpha.txt", "content": "needle"},
                    },
                ]
            },
            {
                "tool_calls": [
                    {"id": "call_read", "name": "read_file", "arguments": {"path": "alpha.txt"}},
                ]
            },
            {"final": "Done."},
        ]
    )
    runtime = _make_runtime(
        tmp_path,
        provider,
        {
            "write_file": lambda params: {
                "status": "written",
                "path": params["path"],
                "bytesWritten": len(params["content"]),
            },
            "read_file": lambda params: {"path": params["path"], "content": "needle", "bytesRead": 6},
        },
    )
    session = _open_session(runtime, tmp_path)

    task = _call_result(
        _rpc(
            runtime,
            "message.send",
            {"sessionId": session["id"], "content": "write and reread"},
        ),
        "task",
    )

    assert task["status"] == "completed"
    started_read = next(
        event
        for event in runtime.events
        if event["type"] == "tool.started" and event["payload"].get("toolName") == "read_file"
    )
    completed_read = next(
        event
        for event in runtime.events
        if event["type"] == "tool.completed" and event["payload"].get("toolName") == "read_file"
    )
    tool_result = next(
        event
        for event in runtime.events
        if event["type"] == "tool_result" and event["payload"].get("toolName") == "read_file"
    )
    assert started_read["payload"]["parentToolUseId"] == "call_write"
    assert completed_read["payload"]["parentToolUseId"] == "call_write"
    assert tool_result["payload"]["parentToolUseId"] == "call_write"
    tool_results = provider.calls[2]["context"]["tool_results"]
    assert tool_results[1]["parentToolUseId"] == "call_write"


def test_react_loop_parents_cross_turn_read_to_apply_patch_text_path(tmp_path: Any) -> None:
    patch_text = _patch_text("alpha.txt", "old", "needle")
    provider = ScriptedProvider(
        [
            {
                "tool_calls": [
                    {
                        "id": "call_patch",
                        "name": "apply_patch",
                        "arguments": {"patchText": patch_text},
                    },
                ]
            },
            {
                "tool_calls": [
                    {"id": "call_read", "name": "read_file", "arguments": {"path": "alpha.txt"}},
                ]
            },
            {"final": "Done."},
        ]
    )
    runtime = _make_runtime(
        tmp_path,
        provider,
        {
            "apply_patch": lambda params: {
                "status": "applied",
                "summary": "Modified alpha.txt",
                "filesChanged": 1,
                "diffText": params["patchText"],
            },
            "read_file": lambda params: {"path": params["path"], "content": "needle", "bytesRead": 6},
        },
    )
    session = _open_session(runtime, tmp_path)

    task = _call_result(
        _rpc(
            runtime,
            "message.send",
            {"sessionId": session["id"], "content": "patch and reread"},
        ),
        "task",
    )

    assert task["status"] == "completed"
    started_read = next(
        event["payload"]
        for event in runtime.events
        if event["type"] == "tool.started" and event["payload"].get("toolName") == "read_file"
    )
    completed_read = next(
        event["payload"]
        for event in runtime.events
        if event["type"] == "tool.completed" and event["payload"].get("toolName") == "read_file"
    )
    tool_result = next(
        event["payload"]
        for event in runtime.events
        if event["type"] == "tool_result" and event["payload"].get("toolName") == "read_file"
    )
    assert started_read["parentToolUseId"] == "call_patch"
    assert completed_read["parentToolUseId"] == "call_patch"
    assert tool_result["parentToolUseId"] == "call_patch"
    tool_results = provider.calls[2]["context"]["tool_results"]
    assert tool_results[1]["parentToolUseId"] == "call_patch"


def test_react_loop_parents_cross_turn_git_review_to_prior_file_change(tmp_path: Any) -> None:
    provider = ScriptedProvider(
        [
            {
                "tool_calls": [
                    {
                        "id": "call_write",
                        "name": "write_file",
                        "arguments": {"path": "alpha.txt", "content": "needle"},
                    },
                ]
            },
            {
                "tool_calls": [
                    {"id": "call_status", "name": "git_status", "arguments": {}},
                ]
            },
            {"final": "Done."},
        ]
    )
    runtime = _make_runtime(
        tmp_path,
        provider,
        {
            "write_file": lambda params: {
                "status": "written",
                "path": params["path"],
                "bytesWritten": len(params["content"]),
            },
            "git_status": lambda _params: {
                "branch": "main",
                "changes": [{"path": "alpha.txt", "status": "modified"}],
            },
        },
    )
    session = _open_session(runtime, tmp_path)

    task = _call_result(
        _rpc(
            runtime,
            "message.send",
            {"sessionId": session["id"], "content": "write and inspect git"},
        ),
        "task",
    )

    assert task["status"] == "completed"
    started_status = next(
        event
        for event in runtime.events
        if event["type"] == "tool.started" and event["payload"].get("toolName") == "git_status"
    )
    completed_status = next(
        event
        for event in runtime.events
        if event["type"] == "tool.completed" and event["payload"].get("toolName") == "git_status"
    )
    assert started_status["payload"]["parentToolUseId"] == "call_write"
    assert completed_status["payload"]["parentToolUseId"] == "call_write"
    tool_results = provider.calls[2]["context"]["tool_results"]
    assert tool_results[1]["parentToolUseId"] == "call_write"


def test_react_loop_assigns_missing_tool_call_ids_before_inferring_batch_tree(tmp_path: Any) -> None:
    provider = ScriptedProvider(
        [
            {
                "message": "Checking the workspace.",
                "tool_calls": [
                    {"name": "search_files", "arguments": {"query": "needle"}},
                    {"name": "read_file", "arguments": {"path": "alpha.txt"}},
                ],
            },
            {"final_answer": "Found needle in alpha.txt."},
        ]
    )
    runtime = _make_runtime(
        tmp_path,
        provider,
        {
            "search_files": lambda params: {"matches": [{"path": "alpha.txt", "preview": "needle"}], "total": 1},
            "read_file": lambda params: {"path": params["path"], "content": "needle", "bytesRead": 6},
        },
    )
    session = _open_session(runtime, tmp_path)

    task = _call_result(
        _rpc(
            runtime,
            "message.send",
            {"sessionId": session["id"], "content": "find needle"},
        ),
        "task",
    )

    assert task["status"] == "completed"
    started = [event["payload"] for event in runtime.events if event["type"] == "tool.started"]
    assert len(started) == 2
    assert all(payload["toolCallId"].startswith("tc_") for payload in started)
    assert started[1].get("parentToolUseId") is None

    assistant_tool_calls = provider.calls[1]["context"]["messages"][-3]["tool_calls"]
    assert assistant_tool_calls[0]["id"] == started[0]["toolCallId"]
    assert assistant_tool_calls[1]["id"] == started[1]["toolCallId"]
    assert "parentToolUseId" not in assistant_tool_calls[1]
    tool_messages = provider.calls[1]["context"]["messages"][-2:]
    assert [message["tool_call_id"] for message in tool_messages] == [
        started[0]["toolCallId"],
        started[1]["toolCallId"],
    ]
    tool_results = provider.calls[1]["context"]["tool_results"]
    assert tool_results[1].get("parentToolUseId") is None


def test_react_loop_preserves_explicit_tool_parent_when_inferring_batch_tree(tmp_path: Any) -> None:
    tool_calls = [
        {"id": "call_search", "name": "search_files", "arguments": {"query": "needle"}},
        {
            "id": "call_read",
            "name": "read_file",
            "parentToolUseId": "call_existing",
            "arguments": {"path": "alpha.txt"},
        },
    ]
    provider = ScriptedProvider(
        [
            {"message": "Checking the workspace.", "tool_calls": tool_calls},
            {"final_answer": "Done."},
        ]
    )
    runtime = _make_runtime(
        tmp_path,
        provider,
        {
            "search_files": lambda params: {"matches": [{"path": "alpha.txt", "preview": "needle"}], "total": 1},
            "read_file": lambda params: {"path": params["path"], "content": "needle", "bytesRead": 6},
        },
    )
    session = _open_session(runtime, tmp_path)

    task = _call_result(
        _rpc(
            runtime,
            "message.send",
            {"sessionId": session["id"], "content": "find needle"},
        ),
        "task",
    )

    assert task["status"] == "completed"
    started = {
        event["payload"]["toolCallId"]: event["payload"]
        for event in runtime.events
        if event["type"] == "tool.started"
    }
    assert started["call_read"]["parentToolUseId"] == "call_existing"
    tool_results = {result["id"]: result for result in provider.calls[1]["context"]["tool_results"]}
    assert tool_results["call_read"]["parentToolUseId"] == "call_existing"


def test_react_loop_infers_verification_parent_from_file_change_tool(tmp_path: Any) -> None:
    tool_calls = [
        {
            "id": "call_write",
            "name": "write_file",
            "arguments": {"path": "alpha.txt", "content": "needle"},
        },
        {
            "id": "call_verify",
            "name": "run_command",
            "arguments": {"command": "python -m pytest tests -q"},
        },
    ]
    provider = ScriptedProvider(
        [
            {"message": "Updating and verifying.", "tool_calls": tool_calls},
            {"final_answer": "Done."},
        ]
    )
    runtime = _make_runtime(
        tmp_path,
        provider,
        {
            "write_file": lambda params: {"path": params["path"], "bytesWritten": len(params["content"])},
            "run_command": lambda params: {
                "status": "completed",
                "exitCode": 0,
                "stdout": "1 passed\n",
                "commandLog": {"id": "cmd_1", "cwd": ".", "shell": "powershell"},
            },
        },
    )
    session = _open_session(runtime, tmp_path)

    task = _call_result(
        _rpc(
            runtime,
            "message.send",
            {"sessionId": session["id"], "content": "update and verify"},
        ),
        "task",
    )

    assert task["status"] == "completed"
    started = {
        event["payload"]["toolCallId"]: event["payload"]
        for event in runtime.events
        if event["type"] == "tool.started"
    }
    assert started["call_write"].get("parentToolUseId") is None
    assert started["call_verify"]["parentToolUseId"] == "call_write"
    assert started["call_verify"]["toolCategory"] == "verification"

    tool_use_blocks = {
        event["payload"]["toolUseId"]: event["payload"]
        for event in runtime.events
        if event["type"] == "tool_use_complete"
    }
    assert tool_use_blocks["call_verify"]["parentToolUseId"] == "call_write"

    tool_results = {result["id"]: result for result in provider.calls[1]["context"]["tool_results"]}
    assert tool_results["call_verify"]["parentToolUseId"] == "call_write"
    assert tool_results["call_verify"]["toolCategory"] == "verification"


def test_react_loop_does_not_parent_plain_commands_to_file_changes(tmp_path: Any) -> None:
    tool_calls = [
        {
            "id": "call_write",
            "name": "write_file",
            "arguments": {"path": "alpha.txt", "content": "needle"},
        },
        {
            "id": "call_echo",
            "name": "run_command",
            "arguments": {"command": "echo done"},
        },
    ]
    provider = ScriptedProvider(
        [
            {"message": "Updating and reporting.", "tool_calls": tool_calls},
            {"final_answer": "Done."},
        ]
    )
    runtime = _make_runtime(
        tmp_path,
        provider,
        {
            "write_file": lambda params: {"path": params["path"], "bytesWritten": len(params["content"])},
            "run_command": lambda params: {
                "status": "completed",
                "exitCode": 0,
                "stdout": "done\n",
                "commandLog": {"id": "cmd_1", "cwd": ".", "shell": "powershell"},
            },
        },
    )
    session = _open_session(runtime, tmp_path)

    task = _call_result(
        _rpc(
            runtime,
            "message.send",
            {"sessionId": session["id"], "content": "update and report"},
        ),
        "task",
    )

    assert task["status"] == "completed"
    started = {
        event["payload"]["toolCallId"]: event["payload"]
        for event in runtime.events
        if event["type"] == "tool.started"
    }
    assert started["call_echo"].get("parentToolUseId") is None
    assert started["call_echo"]["toolCategory"] == "command"
    tool_results = {result["id"]: result for result in provider.calls[1]["context"]["tool_results"]}
    assert tool_results["call_echo"].get("parentToolUseId") is None
    assert tool_results["call_echo"]["toolCategory"] == "command"


def test_react_loop_infers_git_review_parent_from_file_change_tool(tmp_path: Any) -> None:
    tool_calls = [
        {
            "id": "call_write",
            "name": "write_file",
            "arguments": {"path": "alpha.txt", "content": "needle"},
        },
        {
            "id": "call_status",
            "name": "git_status",
            "arguments": {},
        },
        {
            "id": "call_diff",
            "name": "git_diff",
            "arguments": {},
        },
    ]
    provider = ScriptedProvider(
        [
            {"message": "Updating and reviewing.", "tool_calls": tool_calls},
            {"final_answer": "Done."},
        ]
    )
    runtime = _make_runtime(
        tmp_path,
        provider,
        {
            "write_file": lambda params: {"path": params["path"], "bytesWritten": len(params["content"])},
            "git_status": lambda _params: {
                "branch": "main",
                "changedFiles": [{"path": "alpha.txt", "status": "modified"}],
            },
            "git_diff": lambda _params: {
                "scope": "worktree",
                "files": ["alpha.txt"],
                "diff": "diff --git a/alpha.txt b/alpha.txt",
            },
        },
    )
    session = _open_session(runtime, tmp_path)

    task = _call_result(
        _rpc(
            runtime,
            "message.send",
            {"sessionId": session["id"], "content": "update and review"},
        ),
        "task",
    )

    assert task["status"] == "completed"
    started = {
        event["payload"]["toolCallId"]: event["payload"]
        for event in runtime.events
        if event["type"] == "tool.started"
    }
    assert started["call_status"]["parentToolUseId"] == "call_write"
    assert started["call_diff"]["parentToolUseId"] == "call_write"
    assert started["call_status"]["toolCategory"] == "git"
    assert started["call_diff"]["toolCategory"] == "git"

    tool_results = {result["id"]: result for result in provider.calls[1]["context"]["tool_results"]}
    assert tool_results["call_status"]["parentToolUseId"] == "call_write"
    assert tool_results["call_diff"]["parentToolUseId"] == "call_write"
    assert tool_results["call_status"]["toolCategory"] == "git"
    assert tool_results["call_diff"]["toolCategory"] == "git"


def test_react_loop_parents_git_diff_to_same_batch_git_status(tmp_path: Any) -> None:
    tool_calls = [
        {
            "id": "call_status",
            "name": "git_status",
            "arguments": {},
        },
        {
            "id": "call_diff",
            "name": "git_diff",
            "arguments": {},
        },
    ]
    provider = ScriptedProvider(
        [
            {"message": "Reviewing git state.", "tool_calls": tool_calls},
            {"final_answer": "Done."},
        ]
    )
    runtime = _make_runtime(
        tmp_path,
        provider,
        {
            "git_status": lambda _params: {
                "branch": "main",
                "changes": [{"path": "alpha.txt", "status": "modified"}],
            },
            "git_diff": lambda _params: {
                "scope": "worktree",
                "files": ["alpha.txt"],
                "diff": "diff --git a/alpha.txt b/alpha.txt",
            },
        },
    )
    session = _open_session(runtime, tmp_path)

    task = _call_result(
        _rpc(
            runtime,
            "message.send",
            {"sessionId": session["id"], "content": "inspect git"},
        ),
        "task",
    )

    assert task["status"] == "completed"
    started = {
        event["payload"]["toolCallId"]: event["payload"]
        for event in runtime.events
        if event["type"] == "tool.started"
    }
    assert started["call_status"].get("parentToolUseId") is None
    assert started["call_diff"]["parentToolUseId"] == "call_status"

    tool_use_blocks = {
        event["payload"]["toolUseId"]: event["payload"]
        for event in runtime.events
        if event["type"] == "tool_use_complete"
    }
    assert tool_use_blocks["call_diff"]["parentToolUseId"] == "call_status"

    tool_results = {result["id"]: result for result in provider.calls[1]["context"]["tool_results"]}
    assert tool_results["call_diff"]["parentToolUseId"] == "call_status"
    diff_output = next(
        event["payload"]
        for event in runtime.events
        if event["type"] == "tool.output" and event["payload"].get("toolName") == "git_diff"
    )
    assert diff_output["parentToolUseId"] == "call_status"


def test_react_loop_parents_cross_turn_git_diff_to_prior_git_status(tmp_path: Any) -> None:
    provider = ScriptedProvider(
        [
            {
                "tool_calls": [
                    {"id": "call_status", "name": "git_status", "arguments": {}},
                ]
            },
            {
                "tool_calls": [
                    {"id": "call_diff", "name": "git_diff", "arguments": {}},
                ]
            },
            {"final": "Done."},
        ]
    )
    runtime = _make_runtime(
        tmp_path,
        provider,
        {
            "git_status": lambda _params: {
                "branch": "main",
                "changes": [{"path": "alpha.txt", "status": "modified"}],
            },
            "git_diff": lambda _params: {
                "scope": "worktree",
                "files": ["alpha.txt"],
                "diff": "diff --git a/alpha.txt b/alpha.txt",
            },
        },
    )
    session = _open_session(runtime, tmp_path)

    task = _call_result(
        _rpc(
            runtime,
            "message.send",
            {"sessionId": session["id"], "content": "inspect git then diff"},
        ),
        "task",
    )

    assert task["status"] == "completed"
    started_diff = next(
        event
        for event in runtime.events
        if event["type"] == "tool.started" and event["payload"].get("toolName") == "git_diff"
    )
    completed_diff = next(
        event
        for event in runtime.events
        if event["type"] == "tool.completed" and event["payload"].get("toolName") == "git_diff"
    )
    tool_result = next(
        event
        for event in runtime.events
        if event["type"] == "tool_result" and event["payload"].get("toolName") == "git_diff"
    )
    assert started_diff["payload"]["parentToolUseId"] == "call_status"
    assert completed_diff["payload"]["parentToolUseId"] == "call_status"
    assert tool_result["payload"]["parentToolUseId"] == "call_status"
    tool_results = provider.calls[2]["context"]["tool_results"]
    assert tool_results[1]["parentToolUseId"] == "call_status"


def test_react_loop_preserves_explicit_verification_parent_when_inferring_batch_tree(tmp_path: Any) -> None:
    tool_calls = [
        {
            "id": "call_write",
            "name": "write_file",
            "arguments": {"path": "alpha.txt", "content": "needle"},
        },
        {
            "id": "call_verify",
            "name": "run_command",
            "parentToolUseId": "call_existing",
            "arguments": {"command": "npm run typecheck"},
        },
    ]
    provider = ScriptedProvider(
        [
            {"message": "Updating and verifying.", "tool_calls": tool_calls},
            {"final_answer": "Done."},
        ]
    )
    runtime = _make_runtime(
        tmp_path,
        provider,
        {
            "write_file": lambda params: {"path": params["path"], "bytesWritten": len(params["content"])},
            "run_command": lambda params: {
                "status": "completed",
                "exitCode": 0,
                "stdout": "ok\n",
                "commandLog": {"id": "cmd_1", "cwd": ".", "shell": "powershell"},
            },
        },
    )
    session = _open_session(runtime, tmp_path)

    task = _call_result(
        _rpc(
            runtime,
            "message.send",
            {"sessionId": session["id"], "content": "update and verify"},
        ),
        "task",
    )

    assert task["status"] == "completed"
    started = {
        event["payload"]["toolCallId"]: event["payload"]
        for event in runtime.events
        if event["type"] == "tool.started"
    }
    assert started["call_verify"]["parentToolUseId"] == "call_existing"
    tool_results = {result["id"]: result for result in provider.calls[1]["context"]["tool_results"]}
    assert tool_results["call_verify"]["parentToolUseId"] == "call_existing"


def test_react_loop_marks_run_command_verification_category(tmp_path: Any) -> None:
    provider = ScriptedProvider(
        [
            {
                "message": "Verifying.",
                "tool_calls": [
                    {
                        "id": "call_verify",
                        "name": "run_command",
                        "arguments": {"command": "npm run typecheck"},
                    }
                ],
            },
            {"final_answer": "Typecheck passed."},
        ]
    )

    runtime = _make_runtime(
        tmp_path,
        provider,
        {
            "run_command": lambda params: {
                "status": "completed",
                "exitCode": 0,
                "stdout": "ok\n",
                "commandLog": {"id": "cmd_1", "cwd": ".", "shell": "powershell"},
                "shell": "powershell",
                "cwd": ".",
            },
        },
    )
    session = _open_session(runtime, tmp_path)

    task = _call_result(
        _rpc(
            runtime,
            "message.send",
            {"sessionId": session["id"], "content": "run checks"},
        ),
        "task",
    )

    assert task["status"] == "completed"
    started = next(event for event in runtime.events if event["type"] == "tool.started")
    assert started["payload"]["toolCategory"] == "verification"
    assert started["payload"]["toolPhaseId"] == "verification"
    assert started["payload"]["toolPhaseLabel"] == "验证"
    semantic_parent_id = started["payload"]["toolSemanticParentId"]
    assert semantic_parent_id == f"group:{started['payload']['toolGroupId']}:phase:verification"
    assert started["payload"]["toolSemanticParentLabel"] == started["payload"]["toolPhaseLabel"]
    progress = next(
        event["payload"]
        for event in runtime.events
        if event["type"] == "tool.progress" and event["payload"].get("toolUseId") == "call_verify"
    )
    assert progress["toolName"] == "run_command"
    assert progress["toolCategory"] == "verification"
    assert progress["target"] == "npm run typecheck"
    assert progress["inputSummary"] == "npm run typecheck"
    assert progress["outputStream"] == "activity"
    assert "npm run typecheck" in progress["message"]
    progress_delta = next(
        event["payload"]
        for event in runtime.events
        if event["type"] == "content_delta"
        and event["payload"].get("toolUseId") == "call_verify"
        and event["payload"].get("outputStream") == "activity"
        and event["payload"].get("toolOutput") == progress["message"]
    )
    assert progress_delta["toolName"] == "run_command"
    assert progress_delta["toolCategory"] == "verification"
    assert progress_delta["target"] == "npm run typecheck"
    assert progress_delta["inputSummary"] == "npm run typecheck"
    assert progress_delta["toolSemanticParentId"] == semantic_parent_id
    output = next(event for event in runtime.events if event["type"] == "command.output")
    assert output["payload"]["toolCategory"] == "verification"
    assert output["payload"]["target"] == "npm run typecheck"
    assert output["payload"]["inputSummary"] == "npm run typecheck"
    assert output["payload"]["toolPhaseLabel"] == "验证"
    assert output["payload"]["toolSemanticParentId"] == semantic_parent_id
    assert output["payload"]["toolSemanticParentLabel"] == output["payload"]["toolPhaseLabel"]
    tool_use = next(event for event in runtime.events if event["type"] == "tool_use_complete")
    assert tool_use["payload"]["toolCategory"] == "verification"
    assert tool_use["payload"]["toolPhaseLabel"] == "验证"
    assert tool_use["payload"]["toolSemanticParentId"] == semantic_parent_id
    assert tool_use["payload"]["toolSemanticParentLabel"] == tool_use["payload"]["toolPhaseLabel"]
    tool_result = next(event for event in runtime.events if event["type"] == "tool_result")
    assert tool_result["payload"]["toolCategory"] == "verification"
    assert tool_result["payload"]["toolPhaseLabel"] == "验证"
    assert tool_result["payload"]["toolSemanticParentId"] == semantic_parent_id
    assert tool_result["payload"]["toolSemanticParentLabel"] == tool_result["payload"]["toolPhaseLabel"]
    assert tool_result["payload"]["resultPreview"] == [
        {"label": "命令", "value": "npm run typecheck"},
        {"label": "状态", "value": "exit 0"},
        {"label": "目录", "value": "."},
        {"label": "Shell", "value": "powershell"},
        {"label": "输出", "value": "ok"},
    ]
    provider_tool_results = provider.calls[1]["context"]["tool_results"]
    assert provider_tool_results[0]["toolCategory"] == "verification"
    assert provider_tool_results[0]["toolPhaseLabel"] == "验证"
    assert provider_tool_results[0]["toolSemanticParentId"] == semantic_parent_id
    assert provider_tool_results[0]["toolSemanticParentLabel"] == provider_tool_results[0]["toolPhaseLabel"]
    assert provider_tool_results[0]["target"] == "npm run typecheck"
    assert provider_tool_results[0]["inputSummary"] == "npm run typecheck"
    assert provider_tool_results[0]["resultSummary"] == "exit 0: ok"
    assert provider_tool_results[0]["resultPreview"] == tool_result["payload"]["resultPreview"]


def test_react_loop_emits_specific_result_summaries_for_probe_tools(tmp_path: Any) -> None:
    tool_calls = [
        {"id": "call_list", "name": "list_dir", "arguments": {"path": "src"}},
        {"id": "call_search", "name": "search_files", "arguments": {"query": "needle"}},
        {"id": "call_status", "name": "git_status", "arguments": {}},
        {"id": "call_diff", "name": "git_diff", "arguments": {}},
    ]
    provider = ScriptedProvider(
        [
            {"message": "Inspecting.", "tool_calls": tool_calls},
            {"final_answer": "Done."},
        ]
    )

    runtime = _make_runtime(
        tmp_path,
        provider,
        {
            "list_dir": lambda params: {
                "path": "src",
                "items": [
                    {"path": "src/app.ts", "type": "file"},
                    {"path": "src/ui.tsx", "type": "file"},
                ],
            },
            "search_files": lambda params: {
                "query": params["query"],
                "matches": [{"path": "src/app.ts", "line": 4, "preview": "needle"}],
                "total": 1,
            },
            "git_status": lambda params: {
                "isGitRepository": True,
                "branch": "main",
                "ahead": 1,
                "behind": 2,
                "changes": [{"status": "M", "path": "src/app.ts"}],
            },
            "git_diff": lambda params: {
                "isGitRepository": True,
                "staged": False,
                "files": [{"status": "M", "path": "src/app.ts"}],
                "diff": "diff --git a/src/app.ts b/src/app.ts\n",
            },
        },
    )
    session = _open_session(runtime, tmp_path)

    task = _call_result(
        _rpc(
            runtime,
            "message.send",
            {"sessionId": session["id"], "content": "inspect probes"},
        ),
        "task",
    )

    assert task["status"] == "completed"
    completed = {
        event["payload"]["toolName"]: event["payload"]["resultSummary"]
        for event in runtime.events
        if event["type"] == "tool.completed" and event["payload"].get("toolName") in {
            "list_dir",
            "search_files",
            "git_status",
            "git_diff",
        }
    }
    assert completed["list_dir"] == "listed 2 item(s) in src: src/app.ts, src/ui.tsx"
    assert completed["search_files"] == "found 1 match(es) for needle: src/app.ts"
    assert completed["git_status"] == "main: 1 changed file(s) (ahead 1, behind 2): src/app.ts"
    assert completed["git_diff"] == "worktree diff: 1 file(s): src/app.ts"
    previews = {
        event["payload"]["toolName"]: event["payload"]["resultPreview"]
        for event in runtime.events
        if event["type"] == "tool.completed" and event["payload"].get("toolName") in {
            "list_dir",
            "search_files",
            "git_status",
            "git_diff",
        }
    }
    assert previews["list_dir"] == [
        {"label": "目录", "value": "src"},
        {"label": "项目", "value": "2 项"},
        {"label": "样例", "value": "src/app.ts, src/ui.tsx"},
    ]
    assert {"label": "命中", "value": "1 项"} in previews["search_files"]
    assert {"label": "分支", "value": "main"} in previews["git_status"]
    assert {"label": "范围", "value": "worktree"} in previews["git_diff"]
    probe_outputs = {
        event["payload"]["toolName"]: event["payload"]
        for event in runtime.events
        if event["type"] == "tool.output"
        and event["payload"].get("toolName") in {"list_dir", "search_files", "git_status", "git_diff"}
    }
    assert set(probe_outputs) == {"list_dir", "search_files", "git_status", "git_diff"}
    assert probe_outputs["list_dir"]["toolCategory"] == "context_read"
    assert "目录: src" in probe_outputs["list_dir"]["chunk"]
    assert "项目: 2 项" in probe_outputs["list_dir"]["chunk"]
    search_output = next(
        event["payload"]
        for event in runtime.events
        if event["type"] == "tool.output" and event["payload"].get("toolName") == "search_files"
    )
    assert search_output["toolCategory"] == "search"
    assert search_output["outputStream"] == "result_preview"
    assert "查询: needle" in search_output["chunk"]
    assert "命中: 1 项" in search_output["chunk"]
    assert "样例: src/app.ts" in search_output["chunk"]
    search_output_delta = next(
        event["payload"]
        for event in runtime.events
        if event["type"] == "content_delta"
        and event["payload"].get("toolName") == "search_files"
        and event["payload"].get("outputStream") == "result_preview"
        and event["payload"].get("toolOutput") == search_output["chunk"]
    )
    assert search_output_delta["toolCategory"] == "search"
    assert probe_outputs["git_status"]["toolCategory"] == "git"
    assert "分支: main" in probe_outputs["git_status"]["chunk"]
    assert "同步: ahead 1, behind 2" in probe_outputs["git_status"]["chunk"]
    assert probe_outputs["git_diff"]["toolCategory"] == "git"
    assert "范围: worktree" in probe_outputs["git_diff"]["chunk"]
    assert "文件: 1 个" in probe_outputs["git_diff"]["chunk"]
    for tool_name, output in probe_outputs.items():
        delta = next(
            event["payload"]
            for event in runtime.events
            if event["type"] == "content_delta"
            and event["payload"].get("toolName") == tool_name
            and event["payload"].get("outputStream") == "result_preview"
            and event["payload"].get("toolOutput") == output["chunk"]
        )
        assert delta["toolCategory"] == output["toolCategory"]
    for tool_name in probe_outputs:
        preview_deltas = [
            event["payload"]
            for event in runtime.events
            if event["type"] == "content_delta"
            and event["payload"].get("toolName") == tool_name
            and event["payload"].get("outputStream") == "result_preview"
        ]
        assert len(preview_deltas) == 1


def test_react_loop_emits_specific_result_summaries_for_web_and_memory_tools(tmp_path: Any) -> None:
    tool_calls = [
        {"id": "call_web", "name": "web_fetch", "arguments": {"url": "https://example.com/docs", "method": "GET"}},
        {"id": "call_remember", "name": "memory.remember", "arguments": {"content": "Use pytest", "kind": "long_term"}},
        {"id": "call_recall", "name": "memory.recall", "arguments": {"query": "testing", "limit": 2}},
    ]
    provider = ScriptedProvider(
        [
            {"message": "Checking web and memory.", "tool_calls": tool_calls},
            {"final_answer": "Done."},
        ]
    )

    runtime = _make_runtime(
        tmp_path,
        provider,
        {
            "web_fetch": lambda params: {
                "status": "ok",
                "statusCode": 200,
                "url": params["url"],
                "contentType": "text/html; charset=utf-8",
                "content": "Example documentation body",
                "bytesRead": 26,
            },
            "memory.remember": lambda params: {
                "status": "ok",
                "id": "mem_1",
                "kind": params["kind"],
                "keywords": ["pytest", "testing"],
            },
            "memory.recall": lambda params: {
                "status": "ok",
                "count": 1,
                "memories": [
                    {
                        "id": "mem_1",
                        "kind": "long_term",
                        "content": "Use pytest for tests",
                        "keywords": ["pytest"],
                    }
                ],
            },
        },
    )
    session = _open_session(runtime, tmp_path)

    task = _call_result(
        _rpc(
            runtime,
            "message.send",
            {"sessionId": session["id"], "content": "inspect web and memory"},
        ),
        "task",
    )

    assert task["status"] == "completed"
    completed = {
        event["payload"]["toolName"]: event["payload"]
        for event in runtime.events
        if event["type"] == "tool.completed" and event["payload"].get("toolName") in {
            "web_fetch",
            "memory.remember",
            "memory.recall",
        }
    }
    assert completed["web_fetch"]["toolCategory"] == "web"
    assert completed["web_fetch"]["toolPhaseLabel"] == "网页读取"
    assert completed["web_fetch"]["resultSummary"].startswith("HTTP 200 https://example.com/docs")
    assert {"label": "URL", "value": "https://example.com/docs"} in completed["web_fetch"]["resultPreview"]
    assert {"label": "状态", "value": "HTTP 200"} in completed["web_fetch"]["resultPreview"]
    assert completed["memory.remember"]["resultSummary"] == "remembered long_term memory mem_1: pytest, testing"
    assert {"label": "关键词", "value": "pytest, testing"} in completed["memory.remember"]["resultPreview"]
    assert completed["memory.recall"]["resultSummary"] == "recalled 1 memory item(s) for testing: long_term: Use pytest for tests"
    assert {"label": "结果", "value": "1 项"} in completed["memory.recall"]["resultPreview"]

    tool_results = {result["name"]: result for result in provider.calls[1]["context"]["tool_results"]}
    assert tool_results["web_fetch"]["toolCategory"] == "web"
    assert tool_results["web_fetch"]["toolPhaseLabel"] == "网页读取"
    assert tool_results["web_fetch"]["target"] == "https://example.com/docs"
    assert tool_results["web_fetch"]["inputSummary"] == "fetch GET https://example.com/docs"
    assert tool_results["memory.remember"]["inputSummary"] == "remember long_term: Use pytest"
    assert tool_results["memory.recall"]["target"] == "testing"
    remember_output = next(
        event["payload"]
        for event in runtime.events
        if event["type"] == "tool.output" and event["payload"].get("toolName") == "memory.remember"
    )
    assert remember_output["toolCategory"] == "memory"
    assert remember_output["outputStream"] == "result_preview"
    assert "ok" in remember_output["chunk"]
    assert "long_term" in remember_output["chunk"]
    assert "ID: mem_1" in remember_output["chunk"]
    assert "pytest, testing" in remember_output["chunk"]
    remember_output_delta = next(
        event["payload"]
        for event in runtime.events
        if event["type"] == "content_delta"
        and event["payload"].get("toolName") == "memory.remember"
        and event["payload"].get("outputStream") == "result_preview"
        and event["payload"].get("toolOutput") == remember_output["chunk"]
    )
    assert remember_output_delta["toolCategory"] == "memory"
    remember_preview_deltas = [
        event["payload"]
        for event in runtime.events
        if event["type"] == "content_delta"
        and event["payload"].get("toolName") == "memory.remember"
        and event["payload"].get("outputStream") == "result_preview"
    ]
    assert len(remember_preview_deltas) == 1
    recall_output = next(
        event["payload"]
        for event in runtime.events
        if event["type"] == "tool.output" and event["payload"].get("toolName") == "memory.recall"
    )
    assert recall_output["toolCategory"] == "memory"
    assert recall_output["outputStream"] == "result_preview"
    assert "查询: testing" in recall_output["chunk"]
    assert "结果: 1 项" in recall_output["chunk"]
    assert "样例: long_term: Use pytest for tests" in recall_output["chunk"]
    recall_output_delta = next(
        event["payload"]
        for event in runtime.events
        if event["type"] == "content_delta"
        and event["payload"].get("toolName") == "memory.recall"
        and event["payload"].get("outputStream") == "result_preview"
        and event["payload"].get("toolOutput") == recall_output["chunk"]
    )
    assert recall_output_delta["toolCategory"] == "memory"
    recall_preview_deltas = [
        event["payload"]
        for event in runtime.events
        if event["type"] == "content_delta"
        and event["payload"].get("toolName") == "memory.recall"
        and event["payload"].get("outputStream") == "result_preview"
    ]
    assert len(recall_preview_deltas) == 1


def test_react_loop_emits_executor_tool_progress_for_web_and_memory_tools(tmp_path: Any) -> None:
    tool_calls = [
        {"id": "call_web", "name": "web_fetch", "arguments": {"url": "https://example.com/docs", "method": "GET"}},
        {"id": "call_remember", "name": "memory.remember", "arguments": {"content": "Use pytest", "kind": "long_term"}},
        {"id": "call_recall", "name": "memory.recall", "arguments": {"query": "testing", "limit": 2}},
    ]
    provider = ScriptedProvider(
        [
            {"message": "Checking web and memory.", "tool_calls": tool_calls},
            {"final_answer": "Done."},
        ]
    )

    runtime = _make_runtime(
        tmp_path,
        provider,
        {
            "web_fetch": lambda params: {
                "status": "ok",
                "statusCode": 200,
                "url": params["url"],
                "content": "Example documentation body",
                "steps": [
                    {"label": "request", "status": "completed", "summary": f"GET {params['url']}"},
                    {"label": "response", "status": "completed", "summary": "HTTP 200; 26 bytes"},
                    {"label": "decode", "status": "completed", "summary": "utf-8; text/plain"},
                ],
            },
            "memory.remember": lambda params: {
                "status": "ok",
                "id": "mem_1",
                "kind": params["kind"],
            },
            "memory.recall": lambda params: {
                "status": "ok",
                "count": 0,
                "memories": [],
            },
        },
    )
    session = _open_session(runtime, tmp_path)

    task = _call_result(
        _rpc(
            runtime,
            "message.send",
            {"sessionId": session["id"], "content": "inspect web and memory progress"},
        ),
        "task",
    )

    assert task["status"] == "completed"
    progress_by_tool: dict[str, list[dict[str, Any]]] = {}
    for event in runtime.events:
        payload = event["payload"]
        if event["type"] == "tool.progress" and payload.get("toolName") in {
            "web_fetch",
            "memory.remember",
            "memory.recall",
        }:
            progress_by_tool.setdefault(str(payload["toolName"]), []).append(payload)
    assert set(progress_by_tool) == {"web_fetch", "memory.remember", "memory.recall"}

    web_progress = progress_by_tool["web_fetch"]
    assert all(payload["toolUseId"] == "call_web" for payload in web_progress)
    assert all(payload["toolCategory"] == "web" for payload in web_progress)
    assert all(payload["toolPhaseId"] == "web" for payload in web_progress)
    assert any("GET" in payload["message"] for payload in web_progress)
    assert any("https://example.com/docs" in payload["message"] for payload in web_progress)

    remember_progress = progress_by_tool["memory.remember"]
    assert all(payload["toolUseId"] == "call_remember" for payload in remember_progress)
    assert all(payload["toolCategory"] == "memory" for payload in remember_progress)
    assert any("long_term" in payload["message"] for payload in remember_progress)

    recall_progress = progress_by_tool["memory.recall"]
    assert all(payload["toolUseId"] == "call_recall" for payload in recall_progress)
    assert all(payload["toolCategory"] == "memory" for payload in recall_progress)
    assert any("testing" in payload["message"] for payload in recall_progress)

    activity_deltas_by_tool: dict[str, list[dict[str, Any]]] = {}
    for event in runtime.events:
        payload = event["payload"]
        if event["type"] == "content_delta" and payload.get("outputStream") == "activity":
            activity_deltas_by_tool.setdefault(str(payload.get("toolUseId") or ""), []).append(payload)
    for tool_name, payloads in progress_by_tool.items():
        tool_use_id = payloads[0]["toolUseId"]
        deltas = activity_deltas_by_tool[tool_use_id]
        assert any(delta.get("toolCategory") == payloads[0]["toolCategory"] for delta in deltas), tool_name
        assert any(
            str(payload.get("message") or "").strip()
            and str(payload.get("message") or "").strip() in str(delta.get("toolOutput") or "")
            for payload in payloads
            for delta in deltas
        ), tool_name
    web_activity = activity_deltas_by_tool["call_web"]
    assert any(delta.get("toolOutput") == "request (completed): GET https://example.com/docs\n" for delta in web_activity)
    assert any(delta.get("toolOutput") == "response (completed): HTTP 200; 26 bytes\n" for delta in web_activity)
    assert any(delta.get("toolOutput") == "decode (completed): utf-8; text/plain\n" for delta in web_activity)

    web_output = next(
        event["payload"]
        for event in runtime.events
        if event["type"] == "tool.output" and event["payload"].get("toolName") == "web_fetch"
    )
    assert web_output["toolUseId"] == "call_web"
    assert web_output["toolCategory"] == "web"
    assert web_output["outputStream"] == "result_preview"
    assert "URL: https://example.com/docs" in web_output["chunk"]
    assert "状态: HTTP 200" in web_output["chunk"]
    web_output_delta = next(
        event["payload"]
        for event in runtime.events
        if event["type"] == "content_delta"
        and event["payload"].get("toolUseId") == "call_web"
        and event["payload"].get("outputStream") == "result_preview"
        and event["payload"].get("toolOutput") == web_output["chunk"]
    )
    assert web_output_delta["toolCategory"] == "web"
    web_preview_deltas = [
        event["payload"]
        for event in runtime.events
        if event["type"] == "content_delta"
        and event["payload"].get("toolUseId") == "call_web"
        and event["payload"].get("outputStream") == "result_preview"
    ]
    assert len(web_preview_deltas) == 1


def test_react_loop_emits_browser_tool_web_progress_and_result_preview(tmp_path: Any) -> None:
    provider = ScriptedProvider(
        [
            {
                "message": "Checking browser.",
                "tool_calls": [
                    {
                        "id": "call_browser",
                        "name": "browser",
                        "arguments": {"url": "https://example.com/docs", "action": "read"},
                    }
                ],
            },
            {"final_answer": "Done."},
        ]
    )

    runtime = _make_runtime(
        tmp_path,
        provider,
        {
            "browser": lambda params: {
                "status": "ok",
                "statusCode": 200,
                "url": params["url"],
                "action": params["action"],
                "title": "Example Docs",
                "contentType": "text/html; charset=utf-8",
                "content": "Example documentation body",
                "bytesRead": 26,
                "steps": [
                    {"label": "request", "status": "completed", "summary": f"read {params['url']}"},
                    {"label": "response", "status": "completed", "summary": "HTTP 200; 26 bytes"},
                    {"label": "decode", "status": "completed", "summary": "utf-8; text/html"},
                    {"label": "extract", "status": "completed", "summary": "26 text characters"},
                ],
            }
        },
    )
    _allow_browser_automation(runtime)
    session = _open_session(runtime, tmp_path)

    task = _call_result(
        _rpc(
            runtime,
            "message.send",
            {"sessionId": session["id"], "content": "inspect browser"},
        ),
        "task",
    )

    assert task["status"] == "completed"
    started = next(
        event["payload"]
        for event in runtime.events
        if event["type"] == "tool.started" and event["payload"].get("toolName") == "browser"
    )
    assert started["toolCategory"] == "web"
    assert started["toolPhaseId"] == "web"
    assert started["toolPhaseLabel"] == "网页读取"
    assert started["target"] == "https://example.com/docs"
    assert started["inputSummary"] == "browser read https://example.com/docs"

    progress = next(
        event["payload"]
        for event in runtime.events
        if event["type"] == "tool.progress" and event["payload"].get("toolName") == "browser"
    )
    assert progress["toolUseId"] == "call_browser"
    assert progress["toolCategory"] == "web"
    assert "read" in progress["message"]
    assert "https://example.com/docs" in progress["message"]

    completed = next(
        event["payload"]
        for event in runtime.events
        if event["type"] == "tool.completed" and event["payload"].get("toolName") == "browser"
    )
    assert completed["toolCategory"] == "web"
    assert completed["resultSummary"].startswith("HTTP 200 https://example.com/docs")
    assert "Example Docs" in completed["resultSummary"]
    assert {"label": "URL", "value": "https://example.com/docs"} in completed["resultPreview"]
    assert {"label": "动作", "value": "read"} in completed["resultPreview"]
    assert {"label": "状态", "value": "HTTP 200"} in completed["resultPreview"]
    assert {"label": "标题", "value": "Example Docs"} in completed["resultPreview"]
    assert {"label": "摘要", "value": "Example documentation body"} in completed["resultPreview"]

    tool_result = next(
        event["payload"]
        for event in runtime.events
        if event["type"] == "tool_result" and event["payload"].get("toolName") == "browser"
    )
    assert tool_result["toolCategory"] == "web"
    assert tool_result["resultPreview"] == completed["resultPreview"]

    provider_tool_result = provider.calls[1]["context"]["tool_results"][0]
    assert provider_tool_result["name"] == "browser"
    assert provider_tool_result["toolCategory"] == "web"
    assert provider_tool_result["target"] == "https://example.com/docs"
    assert provider_tool_result["inputSummary"] == "browser read https://example.com/docs"
    assert provider_tool_result["resultPreview"] == completed["resultPreview"]

    browser_activity = [
        event["payload"]
        for event in runtime.events
        if event["type"] == "content_delta"
        and event["payload"].get("toolUseId") == "call_browser"
        and event["payload"].get("outputStream") == "activity"
    ]
    assert any("https://example.com/docs" in delta.get("toolOutput", "") for delta in browser_activity)
    assert any("read" in delta.get("toolOutput", "") for delta in browser_activity)
    assert any(delta.get("toolOutput") == "request (completed): read https://example.com/docs\n" for delta in browser_activity)
    assert any(delta.get("toolOutput") == "response (completed): HTTP 200; 26 bytes\n" for delta in browser_activity)
    assert any(delta.get("toolOutput") == "decode (completed): utf-8; text/html\n" for delta in browser_activity)
    assert any(delta.get("toolOutput") == "extract (completed): 26 text characters\n" for delta in browser_activity)

    output = next(
        event["payload"]
        for event in runtime.events
        if event["type"] == "tool.output" and event["payload"].get("toolName") == "browser"
    )
    assert output["toolUseId"] == "call_browser"
    assert output["toolCategory"] == "web"
    assert output["outputStream"] == "result_preview"
    assert "URL: https://example.com/docs" in output["chunk"]
    assert "动作: read" in output["chunk"]
    assert "状态: HTTP 200" in output["chunk"]
    assert "标题: Example Docs" in output["chunk"]

    output_delta = next(
        event["payload"]
        for event in runtime.events
        if event["type"] == "content_delta"
        and event["payload"].get("toolName") == "browser"
        and event["payload"].get("outputStream") == "result_preview"
        and event["payload"].get("toolOutput") == output["chunk"]
    )
    assert output_delta["toolCategory"] == "web"


def test_react_loop_parents_browser_to_same_batch_web_fetch(tmp_path: Any) -> None:
    tool_calls = [
        {"id": "call_web", "name": "web_fetch", "arguments": {"url": "https://example.com/docs/", "method": "GET"}},
        {
            "id": "call_browser",
            "name": "browser",
            "arguments": {"url": "https://EXAMPLE.com/docs#overview", "action": "read"},
        },
    ]
    provider = ScriptedProvider(
        [
            {"message": "Fetch then inspect browser.", "tool_calls": tool_calls},
            {"final_answer": "Done."},
        ]
    )
    runtime = _make_runtime(
        tmp_path,
        provider,
        {
            "web_fetch": lambda params: {
                "status": "ok",
                "statusCode": 200,
                "url": params["url"],
                "content": "Example documentation body",
            },
            "browser": lambda params: {
                "status": "ok",
                "statusCode": 200,
                "url": params["url"],
                "action": params["action"],
                "title": "Example Docs",
                "content": "Example documentation body",
            },
        },
    )
    _allow_browser_automation(runtime)
    session = _open_session(runtime, tmp_path)

    task = _call_result(
        _rpc(
            runtime,
            "message.send",
            {"sessionId": session["id"], "content": "fetch then inspect browser"},
        ),
        "task",
    )

    assert task["status"] == "completed"
    started = {
        event["payload"]["toolCallId"]: event["payload"]
        for event in runtime.events
        if event["type"] == "tool.started"
    }
    assert started["call_web"].get("parentToolUseId") is None
    assert started["call_browser"]["parentToolUseId"] == "call_web"

    completed_browser = next(
        event["payload"]
        for event in runtime.events
        if event["type"] == "tool.completed" and event["payload"].get("toolCallId") == "call_browser"
    )
    assert completed_browser["parentToolUseId"] == "call_web"

    tool_use_blocks = {
        event["payload"]["toolUseId"]: event["payload"]
        for event in runtime.events
        if event["type"] == "tool_use_complete"
    }
    assert tool_use_blocks["call_browser"]["parentToolUseId"] == "call_web"

    tool_result = next(
        event["payload"]
        for event in runtime.events
        if event["type"] == "tool_result" and event["payload"].get("toolUseId") == "call_browser"
    )
    assert tool_result["parentToolUseId"] == "call_web"
    provider_tool_results = {result["id"]: result for result in provider.calls[1]["context"]["tool_results"]}
    assert provider_tool_results["call_browser"]["parentToolUseId"] == "call_web"

    browser_output = next(
        event["payload"]
        for event in runtime.events
        if event["type"] == "tool.output" and event["payload"].get("toolUseId") == "call_browser"
    )
    assert browser_output["parentToolUseId"] == "call_web"


def test_react_loop_parents_cross_turn_browser_to_prior_web_fetch(tmp_path: Any) -> None:
    provider = ScriptedProvider(
        [
            {
                "tool_calls": [
                    {
                        "id": "call_web",
                        "name": "web_fetch",
                        "arguments": {"url": "https://example.com/docs/", "method": "GET"},
                    }
                ]
            },
            {
                "tool_calls": [
                    {
                        "id": "call_browser",
                        "name": "browser",
                        "arguments": {"url": "https://EXAMPLE.com/docs#overview", "action": "read"},
                    }
                ]
            },
            {"final_answer": "Done."},
        ]
    )
    runtime = _make_runtime(
        tmp_path,
        provider,
        {
            "web_fetch": lambda params: {
                "status": "ok",
                "statusCode": 200,
                "url": params["url"],
                "content": "Example documentation body",
            },
            "browser": lambda params: {
                "status": "ok",
                "statusCode": 200,
                "url": params["url"],
                "action": params["action"],
                "title": "Example Docs",
                "content": "Example documentation body",
            },
        },
    )
    _allow_browser_automation(runtime)
    session = _open_session(runtime, tmp_path)

    task = _call_result(
        _rpc(
            runtime,
            "message.send",
            {"sessionId": session["id"], "content": "fetch then inspect browser"},
        ),
        "task",
    )

    assert task["status"] == "completed"
    started_browser = next(
        event["payload"]
        for event in runtime.events
        if event["type"] == "tool.started" and event["payload"].get("toolCallId") == "call_browser"
    )
    completed_browser = next(
        event["payload"]
        for event in runtime.events
        if event["type"] == "tool.completed" and event["payload"].get("toolCallId") == "call_browser"
    )
    tool_result = next(
        event["payload"]
        for event in runtime.events
        if event["type"] == "tool_result" and event["payload"].get("toolUseId") == "call_browser"
    )
    assert started_browser["parentToolUseId"] == "call_web"
    assert completed_browser["parentToolUseId"] == "call_web"
    assert tool_result["parentToolUseId"] == "call_web"
    tool_results = provider.calls[2]["context"]["tool_results"]
    assert tool_results[1]["parentToolUseId"] == "call_web"


def test_react_loop_emits_notebook_tool_progress_and_result_preview(tmp_path: Any) -> None:
    tool_calls = [
        {
            "id": "call_nb_list",
            "name": "notebook",
            "arguments": {"path": "analysis.ipynb", "action": "list_cells"},
        },
        {
            "id": "call_nb_get",
            "name": "notebook",
            "arguments": {"path": "analysis.ipynb", "action": "get_cell", "cell_index": 1},
        },
    ]
    provider = ScriptedProvider(
        [
            {"message": "Inspecting notebook.", "tool_calls": tool_calls},
            {"final_answer": "Done."},
        ]
    )

    def notebook(params: dict[str, Any]) -> dict[str, Any]:
        assert params["taskId"].startswith("task_")
        assert params["sessionId"].startswith("sess_")
        action = params["action"]
        if action == "list_cells":
            return {
                "path": params["path"],
                "action": action,
                "kernel": "Python 3",
                "totalCells": 3,
                "cells": [
                    {"index": 0, "cellType": "markdown", "sourcePreview": "# Analysis"},
                    {"index": 1, "cellType": "code", "sourcePreview": "df.head()"},
                    {"index": 2, "cellType": "code", "sourcePreview": "print('ok')"},
                ],
            }
        if action == "get_cell":
            return {
                "path": params["path"],
                "action": action,
                "index": params["cell_index"],
                "cellType": "code",
                "source": "df.head()",
                "outputs": [{"output_type": "stream", "text": ["rows"]}],
                "executionCount": 7,
            }
        raise AssertionError(f"Unexpected notebook action: {action}")

    runtime = _make_runtime(tmp_path, provider, {"notebook": notebook})
    runtime.store.update_config({"config": {"policy": {"approvalMode": "none"}}})
    session = _open_session(runtime, tmp_path)

    task = _call_result(
        _rpc(
            runtime,
            "message.send",
            {"sessionId": session["id"], "content": "inspect notebook"},
        ),
        "task",
    )

    assert task["status"] == "completed"
    completed = {
        event["payload"]["toolCallId"]: event["payload"]
        for event in runtime.events
        if event["type"] == "tool.completed" and event["payload"].get("toolName") == "notebook"
    }
    assert set(completed) == {"call_nb_list", "call_nb_get"}
    assert completed["call_nb_list"]["toolCategory"] == "notebook"
    assert completed["call_nb_list"]["toolPhaseId"] == "notebook"
    assert completed["call_nb_list"]["toolPhaseLabel"] == "Notebook"
    assert completed["call_nb_list"]["target"] == "analysis.ipynb"
    assert completed["call_nb_list"]["inputSummary"] == "notebook list_cells analysis.ipynb"
    assert completed["call_nb_list"]["resultSummary"].startswith("listed 3 notebook cell(s) in analysis.ipynb")
    assert {"label": "Kernel", "value": "Python 3"} in completed["call_nb_list"]["resultPreview"]
    assert {"label": "Cells", "value": "3 个"} in completed["call_nb_list"]["resultPreview"]
    assert any(row.get("label") == "样例" and "#0 markdown" in row.get("value", "") for row in completed["call_nb_list"]["resultPreview"])

    assert completed["call_nb_get"]["target"] == "analysis.ipynb #cell 1"
    assert completed["call_nb_get"]["inputSummary"] == "notebook get_cell analysis.ipynb #cell 1"
    assert "read analysis.ipynb #cell 1" in completed["call_nb_get"]["resultSummary"]
    assert {"label": "类型", "value": "code"} in completed["call_nb_get"]["resultPreview"]
    assert {"label": "输出", "value": "1 项"} in completed["call_nb_get"]["resultPreview"]
    assert {"label": "源码", "value": "df.head()"} in completed["call_nb_get"]["resultPreview"]

    provider_tool_results = {result["id"]: result for result in provider.calls[1]["context"]["tool_results"]}
    assert provider_tool_results["call_nb_list"]["toolCategory"] == "notebook"
    assert provider_tool_results["call_nb_list"]["toolPhaseLabel"] == "Notebook"
    assert provider_tool_results["call_nb_get"]["parentToolUseId"] == "call_nb_list"
    assert provider_tool_results["call_nb_get"]["resultPreview"] == completed["call_nb_get"]["resultPreview"]

    started = {
        event["payload"]["toolCallId"]: event["payload"]
        for event in runtime.events
        if event["type"] == "tool.started" and event["payload"].get("toolName") == "notebook"
    }
    assert started["call_nb_list"].get("parentToolUseId") is None
    assert started["call_nb_get"]["parentToolUseId"] == "call_nb_list"

    progress = {
        event["payload"]["toolUseId"]: event["payload"]
        for event in runtime.events
        if event["type"] == "tool.progress" and event["payload"].get("toolName") == "notebook"
    }
    assert set(progress) == {"call_nb_list", "call_nb_get"}
    assert progress["call_nb_get"]["toolCategory"] == "notebook"
    assert "get_cell" in progress["call_nb_get"]["message"]
    assert "analysis.ipynb #cell 1" in progress["call_nb_get"]["message"]

    started_activity = [
        event["payload"]
        for event in runtime.events
        if event["type"] == "content_delta"
        and event["payload"].get("toolName") == "notebook"
        and event["payload"].get("outputStream") == "activity"
    ]
    assert any("正在处理 Notebook" in payload.get("toolOutput", "") for payload in started_activity)
    assert any(payload.get("toolCategory") == "notebook" for payload in started_activity)

    outputs = {
        event["payload"]["toolUseId"]: event["payload"]
        for event in runtime.events
        if event["type"] == "tool.output" and event["payload"].get("toolName") == "notebook"
    }
    assert set(outputs) == {"call_nb_list", "call_nb_get"}
    assert outputs["call_nb_get"]["toolCategory"] == "notebook"
    assert outputs["call_nb_get"]["parentToolUseId"] == "call_nb_list"
    assert outputs["call_nb_get"]["outputStream"] == "result_preview"
    assert "Notebook: analysis.ipynb #cell 1" in outputs["call_nb_get"]["chunk"]
    assert "类型: code" in outputs["call_nb_get"]["chunk"]
    assert "输出: 1 项" in outputs["call_nb_get"]["chunk"]

    get_output_delta = next(
        event["payload"]
        for event in runtime.events
        if event["type"] == "content_delta"
        and event["payload"].get("toolUseId") == "call_nb_get"
        and event["payload"].get("outputStream") == "result_preview"
        and event["payload"].get("toolOutput") == outputs["call_nb_get"]["chunk"]
    )
    assert get_output_delta["toolCategory"] == "notebook"


def test_react_loop_parents_cross_turn_notebook_get_to_prior_list(tmp_path: Any) -> None:
    provider = ScriptedProvider(
        [
            {
                "tool_calls": [
                    {
                        "id": "call_nb_list",
                        "name": "notebook",
                        "arguments": {"path": "analysis.ipynb", "action": "list_cells"},
                    }
                ]
            },
            {
                "tool_calls": [
                    {
                        "id": "call_nb_get",
                        "name": "notebook",
                        "arguments": {"path": "analysis.ipynb", "action": "get_cell", "cell_index": 1},
                    }
                ]
            },
            {"final_answer": "Done."},
        ]
    )

    def notebook(params: dict[str, Any]) -> dict[str, Any]:
        action = params["action"]
        if action == "list_cells":
            return {
                "path": params["path"],
                "action": action,
                "totalCells": 2,
                "cells": [
                    {"index": 0, "cellType": "markdown", "sourcePreview": "# Analysis"},
                    {"index": 1, "cellType": "code", "sourcePreview": "df.head()"},
                ],
            }
        if action == "get_cell":
            return {
                "path": params["path"],
                "action": action,
                "index": params["cell_index"],
                "cellType": "code",
                "source": "df.head()",
                "outputs": [],
            }
        raise AssertionError(f"Unexpected notebook action: {action}")

    runtime = _make_runtime(tmp_path, provider, {"notebook": notebook})
    runtime.store.update_config({"config": {"policy": {"approvalMode": "none"}}})
    session = _open_session(runtime, tmp_path)

    task = _call_result(
        _rpc(
            runtime,
            "message.send",
            {"sessionId": session["id"], "content": "list then read notebook"},
        ),
        "task",
    )

    assert task["status"] == "completed"
    started_get = next(
        event["payload"]
        for event in runtime.events
        if event["type"] == "tool.started" and event["payload"].get("toolCallId") == "call_nb_get"
    )
    completed_get = next(
        event["payload"]
        for event in runtime.events
        if event["type"] == "tool.completed" and event["payload"].get("toolCallId") == "call_nb_get"
    )
    tool_result = next(
        event["payload"]
        for event in runtime.events
        if event["type"] == "tool_result" and event["payload"].get("toolUseId") == "call_nb_get"
    )
    assert started_get["parentToolUseId"] == "call_nb_list"
    assert completed_get["parentToolUseId"] == "call_nb_list"
    assert tool_result["parentToolUseId"] == "call_nb_list"
    tool_results = provider.calls[2]["context"]["tool_results"]
    assert tool_results[1]["parentToolUseId"] == "call_nb_list"


def test_react_loop_emits_notebook_execute_cell_preview_when_approved(tmp_path: Any) -> None:
    provider = ScriptedProvider(
        [
            {
                "message": "Executing notebook cell.",
                "tool_calls": [
                    {
                        "id": "call_nb_exec",
                        "name": "notebook",
                        "arguments": {"path": "analysis.ipynb", "action": "execute_cell", "cell_index": 2},
                    }
                ],
            },
            {"final_answer": "Done."},
        ]
    )

    def notebook(params: dict[str, Any]) -> dict[str, Any]:
        assert params["taskId"].startswith("task_")
        assert params["sessionId"].startswith("sess_")
        return {
            "path": params["path"],
            "action": params["action"],
            "index": params["cell_index"],
            "cellType": "code",
            "source": "print('ok')",
            "executionResult": {"exitCode": 0, "stdout": "ok\n", "stderr": ""},
        }

    runtime = _make_runtime(tmp_path, provider, {"notebook": notebook})
    runtime.store.update_config({"config": {"policy": {"approvalMode": "none"}}})
    session = _open_session(runtime, tmp_path)

    task = _call_result(
        _rpc(
            runtime,
            "message.send",
            {"sessionId": session["id"], "content": "execute notebook cell"},
        ),
        "task",
    )

    assert task["status"] == "completed"
    completed = next(
        event["payload"]
        for event in runtime.events
        if event["type"] == "tool.completed" and event["payload"].get("toolName") == "notebook"
    )
    assert completed["toolCategory"] == "notebook"
    assert completed["target"] == "analysis.ipynb #cell 2"
    assert completed["inputSummary"] == "notebook execute_cell analysis.ipynb #cell 2"
    assert completed["resultSummary"] == "executed analysis.ipynb #cell 2: exit 0: ok"
    assert {"label": "动作", "value": "execute_cell"} in completed["resultPreview"]
    assert {"label": "状态", "value": "exit 0"} in completed["resultPreview"]
    assert {"label": "输出", "value": "ok"} in completed["resultPreview"]

    provider_tool_result = provider.calls[1]["context"]["tool_results"][0]
    assert provider_tool_result["toolCategory"] == "notebook"
    assert provider_tool_result["toolPhaseLabel"] == "Notebook"
    assert provider_tool_result["resultPreview"] == completed["resultPreview"]

    output = next(
        event["payload"]
        for event in runtime.events
        if event["type"] == "tool.output" and event["payload"].get("toolName") == "notebook"
    )
    assert output["toolUseId"] == "call_nb_exec"
    assert output["toolCategory"] == "notebook"
    assert output["outputStream"] == "result_preview"
    assert "Notebook: analysis.ipynb #cell 2" in output["chunk"]
    assert "状态: exit 0" in output["chunk"]
    assert "输出: ok" in output["chunk"]

    output_delta = next(
        event["payload"]
        for event in runtime.events
        if event["type"] == "content_delta"
        and event["payload"].get("toolUseId") == "call_nb_exec"
        and event["payload"].get("outputStream") == "result_preview"
        and event["payload"].get("toolOutput") == output["chunk"]
    )
    assert output_delta["toolCategory"] == "notebook"


def test_react_loop_notebook_execute_cell_uses_run_command_approval_and_resumes(tmp_path: Any) -> None:
    provider = ScriptedProvider(
        [
            {
                "message": "Execute notebook cell after approval.",
                "tool_calls": [
                    {
                        "id": "call_nb_exec",
                        "name": "notebook",
                        "arguments": {
                            "path": "analysis.ipynb",
                            "action": "execute_cell",
                            "cell_index": 0,
                            "timeout": 5,
                        },
                    }
                ],
            },
            {"final_answer": "Notebook execution completed after approval."},
        ]
    )
    runtime = _make_runtime(tmp_path, provider)
    runtime.store.update_config({"config": {"permissions": {"preset": "balanced"}}})
    config = runtime.store.get_config({})["config"]
    handler = build_notebook_tool(
        PolicyGuard(approval_mode=config["policy"]["approvalMode"]),
        runtime.store,
        permission_engine=PermissionEngine(config=config, store=runtime.store),
    )["handler"]
    runtime.server._orchestrator._tool_registry.register("notebook", handler)  # noqa: SLF001

    session = _open_session(runtime, tmp_path)
    notebook_path = tmp_path / "workspace" / "analysis.ipynb"
    notebook_path.write_text(
        json.dumps(
            {
                "cells": [
                    {
                        "cell_type": "code",
                        "execution_count": None,
                        "metadata": {},
                        "outputs": [],
                        "source": ["print('approved notebook')\n"],
                    }
                ],
                "metadata": {"kernelspec": {"display_name": "Python 3"}},
                "nbformat": 4,
                "nbformat_minor": 5,
            }
        ),
        encoding="utf-8",
    )

    task = _call_result(
        _rpc(
            runtime,
            "message.send",
            {"sessionId": session["id"], "content": "execute notebook cell"},
        ),
        "task",
    )

    assert task["status"] == "waiting_approval"
    approval_payload = next(
        event["payload"]
        for event in runtime.events
        if event["type"] == "approval.requested" and event["payload"].get("kind") == "run_command"
    )
    approval_id = approval_payload["approvalId"]
    request = approval_payload["request"]
    assert request["toolName"] == "notebook"
    assert request["notebookAction"] == "execute_cell"
    assert request["command"] == "notebook execute_cell analysis.ipynb #cell 0"
    assert request["path"] == "analysis.ipynb"
    assert request["cellIndex"] == 0
    assert request["shell"] == "python"
    assert approval_payload["preview"][0]["value"] == "notebook execute_cell analysis.ipynb #cell 0"
    assert any(row["label"] == "Notebook" and row["value"] == "analysis.ipynb" for row in approval_payload["preview"])

    _rpc(runtime, "approval.submit", {"approvalId": approval_id, "decision": "approved"})

    final_task = _call_result(_rpc(runtime, "task.get", {"taskId": task["id"]}), "task")
    assert final_task["status"] == "completed"
    assert final_task["resultSummary"] == "Notebook execution completed after approval."
    completed = [
        event["payload"]
        for event in runtime.events
        if event["type"] == "tool.completed" and event["payload"].get("toolName") == "notebook"
    ]
    assert completed[-1]["result"]["executionResult"]["stdout"] == "approved notebook\n"
    assert provider.calls[1]["context"]["tool_results"][0]["name"] == "notebook"
    assert provider.calls[1]["context"]["tool_results"][0]["result"]["executionResult"]["exitCode"] == 0


def test_react_loop_emits_scratchpad_tool_progress_and_result_preview(tmp_path: Any) -> None:
    tool_calls = [
        {
            "id": "call_scratch_write",
            "name": "scratchpad.write",
            "arguments": {"key": "hypothesis", "value": "alpha path is likely relevant"},
        },
        {
            "id": "call_scratch_read",
            "name": "scratchpad.read",
            "arguments": {"key": "hypothesis"},
        },
    ]
    provider = ScriptedProvider(
        [
            {"message": "Using scratchpad.", "tool_calls": tool_calls},
            {"final_answer": "Done."},
        ]
    )

    def scratchpad_write(params: dict[str, Any]) -> dict[str, Any]:
        assert params["taskId"].startswith("task_")
        assert params["sessionId"].startswith("sess_")
        return {"status": "ok", "id": "scratch_1", "key": params["key"]}

    def scratchpad_read(params: dict[str, Any]) -> dict[str, Any]:
        assert params["taskId"].startswith("task_")
        assert params["sessionId"].startswith("sess_")
        return {
            "status": "ok",
            "id": "scratch_1",
            "key": params["key"],
            "value": "alpha path is likely relevant",
        }

    runtime = _make_runtime(
        tmp_path,
        provider,
        {"scratchpad.write": scratchpad_write, "scratchpad.read": scratchpad_read},
    )
    session = _open_session(runtime, tmp_path)

    task = _call_result(
        _rpc(
            runtime,
            "message.send",
            {"sessionId": session["id"], "content": "store and read scratchpad state"},
        ),
        "task",
    )

    assert task["status"] == "completed"
    completed = {
        event["payload"]["toolName"]: event["payload"]
        for event in runtime.events
        if event["type"] == "tool.completed"
        and event["payload"].get("toolName") in {"scratchpad.write", "scratchpad.read"}
    }
    assert completed["scratchpad.write"]["toolCategory"] == "memory"
    assert completed["scratchpad.write"]["toolPhaseId"] == "memory"
    assert completed["scratchpad.write"]["target"] == "hypothesis"
    assert completed["scratchpad.write"]["inputSummary"] == "scratchpad write hypothesis"
    assert completed["scratchpad.write"]["resultSummary"] == "stored scratchpad hypothesis (scratch_1)"
    assert any(row.get("value") == "hypothesis" for row in completed["scratchpad.write"]["resultPreview"])
    assert any(row.get("value") == "scratch_1" for row in completed["scratchpad.write"]["resultPreview"])
    assert completed["scratchpad.read"]["target"] == "hypothesis"
    assert completed["scratchpad.read"]["inputSummary"] == "scratchpad read hypothesis"
    assert completed["scratchpad.read"]["resultSummary"] == "read scratchpad hypothesis: alpha path is likely relevant"
    assert any(row.get("value") == "alpha path is likely relevant" for row in completed["scratchpad.read"]["resultPreview"])

    tool_results = {result["name"]: result for result in provider.calls[1]["context"]["tool_results"]}
    assert tool_results["scratchpad.write"]["toolCategory"] == "memory"
    assert tool_results["scratchpad.write"]["resultSummary"] == completed["scratchpad.write"]["resultSummary"]
    assert tool_results["scratchpad.read"]["resultPreview"] == completed["scratchpad.read"]["resultPreview"]

    progress = {
        event["payload"]["toolName"]: event["payload"]
        for event in runtime.events
        if event["type"] == "tool.progress"
        and event["payload"].get("toolName") in {"scratchpad.write", "scratchpad.read"}
    }
    assert set(progress) == {"scratchpad.write", "scratchpad.read"}
    assert progress["scratchpad.write"]["toolUseId"] == "call_scratch_write"
    assert progress["scratchpad.write"]["toolCategory"] == "memory"
    assert "scratchpad" in progress["scratchpad.write"]["message"]
    assert "hypothesis" in progress["scratchpad.write"]["message"]
    assert progress["scratchpad.read"]["toolUseId"] == "call_scratch_read"
    assert "scratchpad" in progress["scratchpad.read"]["message"]
    assert "hypothesis" in progress["scratchpad.read"]["message"]

    for tool_name, tool_use_id in {
        "scratchpad.write": "call_scratch_write",
        "scratchpad.read": "call_scratch_read",
    }.items():
        output = next(
            event["payload"]
            for event in runtime.events
            if event["type"] == "tool.output" and event["payload"].get("toolName") == tool_name
        )
        assert output["toolUseId"] == tool_use_id
        assert output["toolCategory"] == "memory"
        assert output["outputStream"] == "result_preview"
        assert "hypothesis" in output["chunk"]
        assert "scratch_1" in output["chunk"]
        if tool_name == "scratchpad.read":
            assert "alpha path is likely relevant" in output["chunk"]

        preview_deltas = [
            event["payload"]
            for event in runtime.events
            if event["type"] == "content_delta"
            and event["payload"].get("toolUseId") == tool_use_id
            and event["payload"].get("outputStream") == "result_preview"
        ]
        assert len(preview_deltas) == 1
        assert preview_deltas[0]["toolOutput"] == output["chunk"]
        assert preview_deltas[0]["toolCategory"] == "memory"

        activity_deltas = [
            event["payload"]
            for event in runtime.events
            if event["type"] == "content_delta"
            and event["payload"].get("toolUseId") == tool_use_id
            and event["payload"].get("outputStream") == "activity"
        ]
        assert any("scratchpad" in delta.get("toolOutput", "") for delta in activity_deltas)
        assert any("hypothesis" in delta.get("toolOutput", "") for delta in activity_deltas)


def test_react_loop_parents_scratchpad_read_to_same_batch_write(tmp_path: Any) -> None:
    tool_calls = [
        {
            "id": "call_scratch_write",
            "name": "scratchpad.write",
            "arguments": {"key": "hypothesis", "value": "alpha path is likely relevant"},
        },
        {
            "id": "call_scratch_read",
            "name": "scratchpad.read",
            "arguments": {"key": "hypothesis"},
        },
    ]
    provider = ScriptedProvider(
        [
            {"message": "Using scratchpad.", "tool_calls": tool_calls},
            {"final_answer": "Done."},
        ]
    )
    runtime = _make_runtime(
        tmp_path,
        provider,
        {
            "scratchpad.write": lambda params: {"status": "ok", "id": "scratch_1", "key": params["key"]},
            "scratchpad.read": lambda params: {
                "status": "ok",
                "id": "scratch_1",
                "key": params["key"],
                "value": "alpha path is likely relevant",
            },
        },
    )
    session = _open_session(runtime, tmp_path)

    task = _call_result(
        _rpc(
            runtime,
            "message.send",
            {"sessionId": session["id"], "content": "store and read scratchpad state"},
        ),
        "task",
    )

    assert task["status"] == "completed"
    started = {
        event["payload"]["toolCallId"]: event["payload"]
        for event in runtime.events
        if event["type"] == "tool.started"
    }
    assert started["call_scratch_write"].get("parentToolUseId") is None
    assert started["call_scratch_read"]["parentToolUseId"] == "call_scratch_write"

    tool_use_blocks = {
        event["payload"]["toolUseId"]: event["payload"]
        for event in runtime.events
        if event["type"] == "tool_use_complete"
    }
    assert tool_use_blocks["call_scratch_read"]["parentToolUseId"] == "call_scratch_write"

    tool_results = {result["id"]: result for result in provider.calls[1]["context"]["tool_results"]}
    assert tool_results["call_scratch_read"]["parentToolUseId"] == "call_scratch_write"
    scratch_read_output = next(
        event["payload"]
        for event in runtime.events
        if event["type"] == "tool.output" and event["payload"].get("toolName") == "scratchpad.read"
    )
    assert scratch_read_output["parentToolUseId"] == "call_scratch_write"


def test_react_loop_parents_cross_turn_scratchpad_read_to_prior_write(tmp_path: Any) -> None:
    provider = ScriptedProvider(
        [
            {
                "tool_calls": [
                    {
                        "id": "call_scratch_write",
                        "name": "scratchpad.write",
                        "arguments": {"key": "hypothesis", "value": "alpha path is likely relevant"},
                    },
                ]
            },
            {
                "tool_calls": [
                    {"id": "call_scratch_read", "name": "scratchpad.read", "arguments": {"key": "hypothesis"}},
                ]
            },
            {"final": "Done."},
        ]
    )
    runtime = _make_runtime(
        tmp_path,
        provider,
        {
            "scratchpad.write": lambda params: {"status": "ok", "id": "scratch_1", "key": params["key"]},
            "scratchpad.read": lambda params: {
                "status": "ok",
                "id": "scratch_1",
                "key": params["key"],
                "value": "alpha path is likely relevant",
            },
        },
    )
    session = _open_session(runtime, tmp_path)

    task = _call_result(
        _rpc(
            runtime,
            "message.send",
            {"sessionId": session["id"], "content": "store then read scratchpad state"},
        ),
        "task",
    )

    assert task["status"] == "completed"
    started_read = next(
        event
        for event in runtime.events
        if event["type"] == "tool.started" and event["payload"].get("toolName") == "scratchpad.read"
    )
    completed_read = next(
        event
        for event in runtime.events
        if event["type"] == "tool.completed" and event["payload"].get("toolName") == "scratchpad.read"
    )
    tool_result = next(
        event
        for event in runtime.events
        if event["type"] == "tool_result" and event["payload"].get("toolName") == "scratchpad.read"
    )
    assert started_read["payload"]["parentToolUseId"] == "call_scratch_write"
    assert completed_read["payload"]["parentToolUseId"] == "call_scratch_write"
    assert tool_result["payload"]["parentToolUseId"] == "call_scratch_write"
    tool_results = provider.calls[2]["context"]["tool_results"]
    assert tool_results[1]["parentToolUseId"] == "call_scratch_write"


def test_react_loop_emits_executor_tool_progress_for_unblocked_write_file(tmp_path: Any) -> None:
    provider = ScriptedProvider(
        [
            {
                "tool_calls": [
                    {
                        "id": "call_write",
                        "name": "write_file",
                        "arguments": {"path": "notes.txt", "content": "hello\n"},
                    }
                ]
            },
            {"final_answer": "Done."},
        ]
    )
    runtime = _make_builtin_runtime(tmp_path, provider)
    runtime.store.update_config({"config": {"policy": {"approvalMode": "none"}}})
    session = _open_session(runtime, tmp_path)

    task = _call_result(
        _rpc(
            runtime,
            "message.send",
            {"sessionId": session["id"], "content": "write notes"},
        ),
        "task",
    )

    assert task["status"] == "completed"
    assert not [event for event in runtime.events if event["type"] == "approval.requested"]
    progress = next(
        event["payload"]
        for event in runtime.events
        if event["type"] == "tool.progress" and event["payload"].get("toolName") == "write_file"
    )
    assert progress["toolUseId"] == "call_write"
    assert progress["toolCategory"] == "file_change"
    assert "notes.txt" in progress["message"]
    activity = next(
        event["payload"]
        for event in runtime.events
        if event["type"] == "content_delta"
        and event["payload"].get("toolUseId") == "call_write"
        and event["payload"].get("outputStream") == "activity"
        and event["payload"].get("toolOutput") == progress["message"]
    )
    assert activity["toolCategory"] == "file_change"
    write_output = next(
        event["payload"]
        for event in runtime.events
        if event["type"] == "tool.output" and event["payload"].get("toolName") == "write_file"
    )
    assert write_output["toolUseId"] == "call_write"
    assert write_output["toolCategory"] == "file_change"
    assert write_output["outputStream"] == "result_preview"
    assert "文件: notes.txt" in write_output["chunk"]
    assert "写入: 6 bytes" in write_output["chunk"]
    write_delta = next(
        event["payload"]
        for event in runtime.events
        if event["type"] == "content_delta"
        and event["payload"].get("toolUseId") == "call_write"
        and event["payload"].get("outputStream") == "result_preview"
        and event["payload"].get("toolOutput") == write_output["chunk"]
    )
    assert write_delta["toolCategory"] == "file_change"
    write_preview_deltas = [
        event["payload"]
        for event in runtime.events
        if event["type"] == "content_delta"
        and event["payload"].get("toolUseId") == "call_write"
        and event["payload"].get("outputStream") == "result_preview"
    ]
    assert len(write_preview_deltas) == 1


def test_react_loop_emits_generic_result_preview_for_custom_mcp_tools(tmp_path: Any) -> None:
    provider = ScriptedProvider(
        [
            {
                "message": "Looking it up.",
                "tool_calls": [
                    {
                        "id": "call_custom",
                        "name": "mcp__docs__lookup",
                        "arguments": {"query": "install guide"},
                    }
                ],
            },
            {"final_answer": "Done."},
        ]
    )

    runtime = _make_runtime(
        tmp_path,
        provider,
        {
            "mcp__docs__lookup": lambda params: {
                "status": "completed",
                "summary": f"Found docs for {params['query']}",
                "items": [
                    {"title": "Install guide", "url": "https://example.com/install"},
                    {"title": "Troubleshooting", "url": "https://example.com/debug"},
                ],
            },
        },
    )
    session = _open_session(runtime, tmp_path)

    task = _call_result(
        _rpc(
            runtime,
            "message.send",
            {"sessionId": session["id"], "content": "lookup docs"},
        ),
        "task",
    )

    assert task["status"] == "completed"
    completed = next(
        event["payload"]
        for event in runtime.events
        if event["type"] == "tool.completed" and event["payload"].get("toolName") == "mcp__docs__lookup"
    )
    assert completed["target"] == "install guide"
    assert completed["resultSummary"] == "Found docs for install guide"
    assert {"label": "状态", "value": "completed"} in completed["resultPreview"]
    assert {"label": "目标", "value": "install guide"} in completed["resultPreview"]
    assert {"label": "摘要", "value": "Found docs for install guide"} in completed["resultPreview"]
    assert {"label": "样例", "value": "Install guide, Troubleshooting"} in completed["resultPreview"]

    provider_tool_result = provider.calls[1]["context"]["tool_results"][0]
    assert provider_tool_result["resultPreview"] == completed["resultPreview"]
    assert isinstance(completed["durationMs"], int)
    assert completed["durationMs"] >= 0
    assert provider_tool_result["durationMs"] == completed["durationMs"]
    raw_progress = [
        event["payload"]
        for event in runtime.events
        if event["type"] == "tool.progress" and event["payload"].get("toolUseId") == "call_custom"
    ]
    assert raw_progress[0]["message"] == "正在调用 MCP 工具：install guide\n"
    assert raw_progress[0]["toolCategory"] == "mcp"
    assert raw_progress[0]["toolPhaseId"] == "mcp"
    assert raw_progress[0]["toolPhaseLabel"] == "MCP"
    assert raw_progress[0]["toolOperationId"] == "mcp:docs:lookup:install guide"
    assert raw_progress[0]["toolOperationLabel"] == "MCP"
    assert raw_progress[0]["toolSemanticParentId"] == f"group:{raw_progress[0]['toolGroupId']}:phase:mcp"
    assert completed["toolCategory"] == "mcp"
    assert completed["toolOperationId"] == "mcp:docs:lookup:install guide"
    assert provider_tool_result["toolCategory"] == "mcp"
    assert provider_tool_result["toolOperationId"] == "mcp:docs:lookup:install guide"
    activity = next(
        event["payload"]
        for event in runtime.events
        if event["type"] == "content_delta"
        and event["payload"].get("toolUseId") == "call_custom"
        and event["payload"].get("outputStream") == "activity"
    )
    assert activity["toolOutput"] == "正在调用 MCP 工具：install guide\n"
    delta = next(
        event["payload"]
        for event in runtime.events
        if event["type"] == "content_delta"
        and event["payload"].get("toolUseId") == "call_custom"
        and event["payload"].get("outputStream") == "result_preview"
    )
    assert "状态: completed" in delta["toolOutput"]
    assert "摘要: Found docs for install guide" in delta["toolOutput"]
    assert "样例: Install guide, Troubleshooting" in delta["toolOutput"]
    tool_result = next(
        event["payload"]
        for event in runtime.events
        if event["type"] == "tool_result" and event["payload"].get("toolName") == "mcp__docs__lookup"
    )
    assert tool_result["resultPreview"] == completed["resultPreview"]
    assert tool_result["durationMs"] == completed["durationMs"]


def test_react_loop_streams_structured_tool_steps_once_before_result_preview(tmp_path: Any) -> None:
    provider = ScriptedProvider(
        [
            {
                "message": "Looking it up.",
                "tool_calls": [
                    {
                        "id": "call_custom",
                        "name": "mcp__docs__lookup",
                        "arguments": {"query": "install guide"},
                    }
                ],
            },
            {"final_answer": "Done."},
        ]
    )

    runtime = _make_runtime(
        tmp_path,
        provider,
        {
            "mcp__docs__lookup": lambda params: {
                "status": "completed",
                "summary": f"Found docs for {params['query']}",
                "steps": [
                    {"label": "connect", "status": "completed", "summary": "opened docs index"},
                    {"label": "search", "status": "completed", "summary": "matched install guide"},
                ],
                "items": [{"title": "Install guide", "url": "https://example.com/install"}],
            },
        },
    )
    session = _open_session(runtime, tmp_path)

    task = _call_result(
        _rpc(
            runtime,
            "message.send",
            {"sessionId": session["id"], "content": "lookup docs"},
        ),
        "task",
    )

    assert task["status"] == "completed"
    progress = [
        event["payload"]
        for event in runtime.events
        if event["type"] == "tool.progress" and event["payload"].get("toolUseId") == "call_custom"
    ]
    assert [payload["message"] for payload in progress] == [
        "正在调用 MCP 工具：install guide\n",
        "connect (completed): opened docs index\n",
        "search (completed): matched install guide\n",
    ]

    deltas = [
        event["payload"]
        for event in runtime.events
        if event["type"] == "content_delta" and event["payload"].get("toolUseId") == "call_custom"
    ]
    assert [delta["outputStream"] for delta in deltas] == [
        "activity",
        "activity",
        "activity",
        "result_preview",
    ]
    activity_outputs = [delta["toolOutput"] for delta in deltas if delta["outputStream"] == "activity"]
    assert len(activity_outputs) == 3
    assert activity_outputs[0].endswith("install guide\n")
    assert activity_outputs[1:] == [
        "connect (completed): opened docs index\n",
        "search (completed): matched install guide\n",
    ]
    assert sum(1 for delta in deltas if delta["toolOutput"] == "connect (completed): opened docs index\n") == 1
    assert sum(1 for delta in deltas if delta["toolOutput"] == "search (completed): matched install guide\n") == 1


def test_react_loop_streams_generic_diagnostics_as_activity(tmp_path: Any) -> None:
    provider = ScriptedProvider(
        [
            {
                "tool_calls": [
                    {
                        "id": "call_custom",
                        "name": "custom_lookup",
                        "arguments": {"query": "install guide"},
                    }
                ],
            },
            {"final_answer": "Done."},
        ]
    )

    runtime = _make_runtime(
        tmp_path,
        provider,
        {
            "custom_lookup": lambda _params: {
                "status": "completed",
                "summary": "lookup completed with diagnostics",
                "diagnostics": [
                    {"code": "connect", "ok": True, "message": "opened docs index", "durationMs": 12},
                    {"code": "cache_miss", "ok": False, "error": "no local cache", "count": 0},
                ],
                "items": [{"title": "Install guide"}],
            },
        },
    )
    session = _open_session(runtime, tmp_path)

    task = _call_result(
        _rpc(
            runtime,
            "message.send",
            {"sessionId": session["id"], "content": "lookup docs"},
        ),
        "task",
    )

    assert task["status"] == "completed"
    progress_messages = [
        event["payload"]["message"]
        for event in runtime.events
        if event["type"] == "tool.progress" and event["payload"].get("toolUseId") == "call_custom"
    ]
    assert progress_messages == [
        "正在调用工具：install guide\n",
        "connect (completed): opened docs index [durationMs=12ms]\n",
        "cache_miss (failed): no local cache [count=0]\n",
    ]
    deltas = [
        event["payload"]
        for event in runtime.events
        if event["type"] == "content_delta" and event["payload"].get("toolUseId") == "call_custom"
    ]
    assert [delta["outputStream"] for delta in deltas] == [
        "activity",
        "activity",
        "activity",
        "result_preview",
    ]
    assert any(delta["toolOutput"] == "cache_miss (failed): no local cache [count=0]\n" for delta in deltas)


@pytest.mark.skip(reason="Hangs due to subprocess initialization in _make_builtin_runtime")
def test_react_loop_can_delegate_task_tool(tmp_path: Any) -> None:
    provider = ScriptedProvider(
        [
            {
                "tool_calls": [
                    {
                        "id": "call_task",
                        "name": "task",
                        "arguments": {"prompt": "Inspect collaboration runtime gaps."},
                    }
                ]
            },
            {"final": "Delegated task completed."},
        ]
    )
    runtime = _make_builtin_runtime(tmp_path, provider)
    session = _open_session(runtime, tmp_path)

    task = _call_result(
        _rpc(
            runtime,
            "message.send",
            {"sessionId": session["id"], "content": "delegate this"},
        ),
        "task",
    )

    assert task["status"] == "completed"
    delegated = provider.calls[1]["context"]["tool_results"][0]["result"]
    assert delegated["childTaskId"].startswith("ctask_")
    assert delegated["workerId"].startswith("agent_")
    assert delegated["result"]["summary"]
    assert delegated["task"]["status"] == "completed"
    assert [event["type"] for event in runtime.events if event["type"].startswith("collab.task.")]
    assert any(event["type"] == "collab.task.completed" for event in runtime.events)

    parent_trace = _rpc(runtime, "trace.list", {"taskId": task["id"]})["result"]["traceEvents"]
    assert [event["type"] for event in parent_trace if event["type"] in {"tool.started", "tool.completed"}] == [
        "tool.started",
        "tool.completed",
    ]
    assert [event["payload"]["toolName"] for event in parent_trace if event["type"] == "tool.started"] == ["task"]

    child_trace = _rpc(runtime, "trace.list", {"taskId": delegated["childTaskId"]})["result"]["traceEvents"]
    child_trace_types = [event["type"] for event in child_trace]
    assert child_trace_types[:3] == [
        "collab.task.created",
        "collab.task.claimed",
        "collab.task.updated",
    ]
    assert child_trace_types[-2:] == ["collab.task.completed", "collab.message.sent"]
    assert any(
        event["type"] == "collab.task.updated"
        and isinstance(event["payload"], dict)
        and isinstance(event["payload"].get("_bridge"), dict)
        for event in child_trace
    )
    assert child_trace[0]["sessionId"] == task["sessionId"]


def test_worker_run_child_task_enforces_token_budget_from_provider_usage(tmp_path: Any) -> None:
    provider = ScriptedProvider(
        [
            {
                "final": "done",
                "raw": {"usage": {"total_tokens": 11}},
            }
        ]
    )
    runtime = _make_runtime(tmp_path, provider)
    session = _open_session(runtime, tmp_path)

    response = _rpc(
        runtime,
        "worker.run_child_task",
        {
            "sessionId": session["id"],
            "prompt": "answer directly",
            "budget": {"maxTokens": 10},
        },
    )

    assert response["error"]["code"] == "WORKER_BUDGET_TOKENS_EXCEEDED"


def test_worker_run_child_task_enforces_tool_call_budget(tmp_path: Any) -> None:
    runtime = _make_builtin_runtime(tmp_path, provider=ProviderAdapter())
    session = _open_session(runtime, tmp_path)

    response = _rpc(
        runtime,
        "worker.run_child_task",
        {
            "sessionId": session["id"],
            "prompt": "inspect workspace",
            "budget": {"maxToolCalls": 0},
        },
    )

    assert response["error"]["code"] == "WORKER_BUDGET_TOOL_CALLS_EXCEEDED"


def test_react_loop_pauses_for_approval_and_resumes_after_submit(tmp_path: Any) -> None:
    provider = ScriptedProvider(
        [
            {
                "tool_calls": [
                    {
                        "id": "call_command",
                        "name": "run_command",
                        "arguments": {"command": "Write-Output approved"},
                    }
                ]
            },
            {"final": "Command completed after approval."},
        ]
    )
    runtime = _make_runtime(tmp_path, provider)

    def run_command(params: dict[str, Any]) -> dict[str, Any]:
        request = {"command": params["command"]}
        if not params.get("approvalId"):
            approval = runtime.store.create_approval(
                task_id=params["taskId"],
                kind="run_command",
                request=request,
            )
            return {"status": "approval_required", "approval": approval, "command": params["command"]}
        return {
            "status": "completed",
            "stdout": "approved\n",
            "stderr": "",
            "exitCode": 0,
        }

    runtime.server._orchestrator._tool_registry.register("run_command", run_command)  # noqa: SLF001
    session = _open_session(runtime, tmp_path)

    task = _call_result(
        _rpc(
            runtime,
            "message.send",
            {"sessionId": session["id"], "content": "run a command"},
        ),
        "task",
    )

    assert task["status"] == "waiting_approval"
    approval_id = next(event for event in runtime.events if event["type"] == "approval.requested")["payload"][
        "approvalId"
    ]

    _rpc(runtime, "approval.submit", {"approvalId": approval_id, "decision": "approved"})

    final_task = _call_result(_rpc(runtime, "task.get", {"taskId": task["id"]}), "task")
    assert final_task["status"] == "completed"
    assert final_task["resultSummary"] == "Command completed after approval."
    assert "task.waiting_approval" in [event["type"] for event in runtime.events]
    resolved = next(event["payload"] for event in runtime.events if event["type"] == "approval.resolved")
    assert resolved["approvalId"] == approval_id
    assert resolved["kind"] == "run_command"
    assert resolved["decision"] == "approved"
    assert resolved["decidedBy"] == "user"
    assert resolved["decidedAt"] is not None
    assert resolved["request"] == {"command": "Write-Output approved"}
    assert resolved["preview"] == [{"label": "命令", "value": "Write-Output approved"}]
    event_types = [event["type"] for event in runtime.events]
    assert event_types.count("tool.blocked") == 1
    assert event_types.count("tool.completed") == 1
    assert provider.calls[1]["context"]["tool_results"][0]["result"]["stdout"] == "approved\n"


def test_react_resume_parents_remaining_tool_to_approved_custom_result(tmp_path: Any) -> None:
    provider = ScriptedProvider(
        [
            {
                "tool_calls": [
                    {
                        "id": "call_lookup",
                        "name": "custom_lookup",
                        "arguments": {"query": "install guide"},
                    },
                    {
                        "id": "call_read",
                        "name": "read_file",
                        "arguments": {"path": "docs/install.md"},
                    },
                ]
            },
            {"final": "Approved lookup read completed."},
        ]
    )
    runtime = _make_runtime(tmp_path, provider)

    def custom_lookup(params: dict[str, Any]) -> dict[str, Any]:
        request = {"query": params["query"]}
        if not params.get("approvalId"):
            approval = runtime.store.create_approval(
                task_id=params["taskId"],
                kind="custom_lookup",
                request=request,
            )
            return {"status": "approval_required", "approval": approval, "summary": "lookup approval required"}
        return {
            "status": "completed",
            "summary": "lookup approved",
            "items": [{"title": "Install guide", "path": "docs/install.md"}],
        }

    runtime.server._orchestrator._tool_registry.register("custom_lookup", custom_lookup)  # noqa: SLF001
    runtime.server._orchestrator._tool_registry.register(  # noqa: SLF001
        "read_file",
        lambda params: {"path": params["path"], "content": "install", "bytesRead": 7},
    )
    session = _open_session(runtime, tmp_path)

    task = _call_result(
        _rpc(
            runtime,
            "message.send",
            {"sessionId": session["id"], "content": "lookup then read after approval"},
        ),
        "task",
    )

    assert task["status"] in {"completed", "waiting_approval"}
    state = runtime.store.get_pending_react_state(task["id"])
    assert state is not None
    assert state["remaining_tool_calls"][0]["id"] == "call_read"
    approval_id = next(event for event in runtime.events if event["type"] == "approval.requested")["payload"][
        "approvalId"
    ]

    _rpc(runtime, "approval.submit", {"approvalId": approval_id, "decision": "approved"})

    final_task = _call_result(_rpc(runtime, "task.get", {"taskId": task["id"]}), "task")
    assert final_task["status"] == "completed"
    started_read = next(
        event["payload"]
        for event in runtime.events
        if event["type"] == "tool.started" and event["payload"].get("toolCallId") == "call_read"
    )
    assert started_read["parentToolUseId"] == "call_lookup"
    assert started_read["toolOperationId"] == "tool:custom_lookup:install guide"
    provider_tool_results = {result["id"]: result for result in provider.calls[1]["context"]["tool_results"]}
    assert provider_tool_results["call_read"]["parentToolUseId"] == "call_lookup"
    assert provider_tool_results["call_read"]["toolOperationId"] == "tool:custom_lookup:install guide"


def test_react_loop_pauses_for_ask_user_and_resumes_with_supplement(tmp_path: Any) -> None:
    provider = ScriptedProvider(
        [
            {
                "decision": "ask_user",
                "message": "需要先确认继续拆渲染层还是改数据层？",
                "thought_summary": "目标有歧义，需要用户选择范围。",
                "why_complete": "scope_unclear",
                "policy_needs": {
                    "options": [
                        {"label": "渲染层", "value": "ui", "description": "只处理 UI 渲染。"},
                        {"label": "数据层", "value": "data", "description": "先补数据管线。"},
                    ],
                },
            },
            {"final": "已按补充说明继续。"},
        ]
    )
    runtime = _make_runtime(tmp_path, provider)
    session = _open_session(runtime, tmp_path)

    task = _call_result(
        _rpc(
            runtime,
            "message.send",
            {"sessionId": session["id"], "content": "继续做 parity"},
        ),
        "task",
    )

    assert task["status"] == "paused"
    question_event = next(event for event in runtime.events if event["type"] == "ask_user_question")
    assert question_event["payload"]["question"] == "需要先确认继续拆渲染层还是改数据层？"
    assert question_event["payload"]["status"] == "waiting"
    assert question_event["payload"]["options"][0]["label"] == "渲染层"
    assert runtime.store.get_pending_react_state(task["id"]) is not None

    supplement = _call_result(
        _rpc(
            runtime,
            "message.send",
            {
                "sessionId": session["id"],
                "taskId": task["id"],
                "mode": "supplement",
                "content": "选择：渲染层",
            },
        ),
        "task",
    )
    final_task = _call_result(_rpc(runtime, "task.get", {"taskId": task["id"]}), "task")

    assert supplement["status"] == "completed"
    assert final_task["resultSummary"] == "已按补充说明继续。"
    assert "task.supplement.consumed" in [event["type"] for event in runtime.events]


def test_react_loop_ask_user_question_tool_auto_resumes_with_structured_result(tmp_path: Any) -> None:
    provider = ScriptedProvider(
        [
            {
                "message": "I need the implementation scope before changing files.",
                "tool_calls": [
                    {
                        "id": "call_question",
                        "name": "ask_user_question",
                        "arguments": {
                            "questions": [
                                {
                                    "id": "target_scope",
                                    "header": "Scope",
                                    "question": "Should I update the UI or data layer first?",
                                    "options": [
                                        {
                                            "label": "UI first",
                                            "value": "ui",
                                            "description": "Start with the visible flow.",
                                            "recommended": True,
                                        },
                                        {
                                            "label": "Data first",
                                            "value": "data",
                                            "description": "Start with backend data plumbing.",
                                        },
                                    ],
                                },
                                {
                                    "id": "verification_level",
                                    "header": "Verify",
                                    "question": "How much verification should I run?",
                                },
                            ],
                            "summary": "Need scope before continuing.",
                            "reason": "scope_unclear",
                        },
                    }
                ],
            },
            {"final": "Continuing with the UI-first scope."},
        ]
    )
    runtime = _make_builtin_runtime(tmp_path, provider)
    session = _open_session(runtime, tmp_path)

    task = _call_result(
        _rpc(runtime, "message.send", {"sessionId": session["id"], "content": "update the workflow"}),
        "task",
    )
    runtime.store.update_task(
        task_id=task["id"],
        routing={
            **(task.get("routing") or {}),
            "profile": {
                **((task.get("routing") or {}).get("profile") or {}),
                "workspaceEvidenceRequired": {"required": False, "source": "test"},
            },
        },
    )

    assert task["status"] == "paused"
    question_event = next(event for event in runtime.events if event["type"] == "ask_user_question")
    assert question_event["payload"]["source"] == "tool"
    assert question_event["payload"]["toolCallId"] == "call_question"
    assert question_event["payload"]["question"] == "Should I update the UI or data layer first?"
    assert question_event["payload"]["questions"][0]["id"] == "target_scope"
    assert question_event["payload"]["questions"][0]["options"][0]["recommended"] is True
    pending_state = runtime.store.get_pending_react_state(task["id"])
    assert pending_state is not None
    assert pending_state["pending_tool_call"]["id"] == "call_question"
    assert pending_state["tool_results"] == []

    supplement_resp = _rpc(
        runtime,
        "message.send",
        {
            "sessionId": session["id"],
            "taskId": task["id"],
            "mode": "supplement",
            "content": "Choose UI first, and run focused verification.",
        },
    )
    resumed = _call_result(supplement_resp, "task")

    assert supplement_resp["result"]["acceptedMode"] == "supplement"
    assert supplement_resp["result"]["autoResumed"] is True
    assert resumed["status"] == "completed"
    assert resumed["resultSummary"] == "Continuing with the UI-first scope."
    assert runtime.store.get_pending_react_state(task["id"]) is None
    tool_messages = [
        message
        for message in provider.calls[1]["context"]["messages"]
        if message.get("role") == "tool" and message.get("name") == "ask_user_question"
    ]
    assert len(tool_messages) == 1
    answer_payload = json.loads(tool_messages[0]["content"])
    assert answer_payload["status"] == "answered"
    assert answer_payload["answers"]["target_scope"] == "Choose UI first, and run focused verification."
    assert answer_payload["answers"]["verification_level"] == "Choose UI first, and run focused verification."


def test_react_loop_ask_user_question_internal_answer_is_not_visible_user_message(tmp_path: Any) -> None:
    provider = ScriptedProvider(
        [
            {
                "tool_calls": [
                    {
                        "id": "call_question",
                        "name": "ask_user_question",
                        "arguments": {
                            "question": "Which repository path should I inspect before continuing?",
                            "options": [
                                {
                                    "label": "Current project",
                                    "value": "current_project",
                                    "description": "Inspect the active workspace.",
                                }
                            ],
                            "reason": "missing_required_path",
                        },
                    }
                ],
            },
            {"final": "Using the selected repository path."},
        ]
    )
    runtime = _make_builtin_runtime(tmp_path, provider)
    session = _open_session(runtime, tmp_path)

    task = _call_result(
        _rpc(runtime, "message.send", {"sessionId": session["id"], "content": "choose a response format"}),
        "task",
    )
    runtime.store.update_task(
        task_id=task["id"],
        routing={
            **(task.get("routing") or {}),
            "profile": {
                **((task.get("routing") or {}).get("profile") or {}),
                "workspaceEvidenceRequired": {"required": False, "source": "test"},
            },
        },
    )

    question_event = next(event for event in runtime.events if event["type"] == "ask_user_question")
    request_id = question_event["payload"]["requestId"]
    response = _rpc(
        runtime,
        "message.send",
        {
            "sessionId": session["id"],
            "taskId": task["id"],
            "mode": "supplement",
            "content": "Choice: Current project",
            "internalResponse": {
                "kind": "ask_user_question",
                "messageId": "ask-message-1",
                "requestId": request_id,
                "toolCallId": "call_question",
            },
        },
    )
    resumed = _call_result(response, "task")

    assert response["result"]["autoResumed"] is True
    assert resumed["status"] == "completed"
    messages = _call_result(_rpc(runtime, "message.list", {"sessionId": session["id"]}), "messages")
    assert [message["role"] for message in messages] == ["user", "assistant"]
    assert not any(message["role"] == "user" and "Choice: Current project" in message.get("content", "") for message in messages)
    assert not any(message["role"] == "assistant" and message.get("kind") == "supplement" for message in messages)

    consumed = next(
        event
        for event in runtime.events
        if event["type"] == "task.supplement.consumed"
        and event["payload"].get("reason") == "ask_user_question_answer"
    )
    assert consumed["payload"]["requestId"] == request_id
    assert consumed["payload"]["internalResponse"]["messageId"] == "ask-message-1"

    duplicate = _rpc(
        runtime,
        "message.send",
        {
            "sessionId": session["id"],
            "taskId": task["id"],
            "mode": "supplement",
            "content": "Choice: Current project",
            "internalResponse": {
                "kind": "ask_user_question",
                "messageId": "ask-message-1",
                "requestId": request_id,
                "toolCallId": "call_question",
            },
        },
    )
    assert duplicate["result"]["duplicate"] is True
    after_duplicate = _call_result(_rpc(runtime, "message.list", {"sessionId": session["id"]}), "messages")
    assert [message["role"] for message in after_duplicate] == ["user", "assistant"]


def test_react_loop_ask_user_question_tool_resume_waits_for_answer(tmp_path: Any) -> None:
    provider = ScriptedProvider(
        [
            {
                "tool_calls": [
                    {
                        "id": "call_question",
                        "name": "ask_user_question",
                        "arguments": {"question": "Which path should I take?"},
                    }
                ],
            },
            {"final": "This should not run without an answer."},
        ]
    )
    runtime = _make_builtin_runtime(tmp_path, provider)
    session = _open_session(runtime, tmp_path)

    task = _call_result(
        _rpc(runtime, "message.send", {"sessionId": session["id"], "content": "start ambiguous work"}),
        "task",
    )
    resumed = _call_result(_rpc(runtime, "task.resume", {"taskId": task["id"]}), "task")

    assert resumed["status"] == "paused"
    assert len(provider.calls) == 1
    assert any(event["type"] == "task.resume.blocked" for event in runtime.events)


def test_react_loop_defaults_low_risk_ask_user_question_tool(tmp_path: Any) -> None:
    provider = ScriptedProvider(
        [
            {
                "tool_calls": [
                    {
                        "id": "call_question",
                        "name": "ask_user_question",
                        "arguments": {
                            "question": "Which current task list style should I use?",
                            "options": [
                                {
                                    "label": "Status list",
                                    "value": "status_list",
                                    "description": "Group tasks by current state.",
                                    "recommended": True,
                                },
                                {
                                    "label": "Priority list",
                                    "value": "priority_list",
                                    "description": "Sort tasks by priority.",
                                },
                            ],
                            "summary": "Choose output style.",
                            "reason": "output_format_preference",
                        },
                    }
                ],
            },
            {"final": "Using the status-list format."},
        ]
    )
    runtime = _make_builtin_runtime(tmp_path, provider)
    session = _open_session(runtime, tmp_path)

    task = _call_result(
        _rpc(runtime, "message.send", {"sessionId": session["id"], "content": "make a task list"}),
        "task",
    )

    assert task["status"] == "completed"
    assert not any(event["type"] == "ask_user_question" for event in runtime.events)
    tool_messages = [
        message
        for message in provider.calls[1]["context"]["messages"]
        if message.get("role") == "tool" and message.get("name") == "ask_user_question"
    ]
    assert len(tool_messages) == 1
    payload = json.loads(tool_messages[0]["content"])
    assert payload["defaulted"] is True
    assert payload["status"] == "answered"
    assert "Status list" in payload["answer"]
    assert not [
        event
        for event in runtime.events
        if event["type"] == "tool_result"
        and event["payload"].get("toolName") == "ask_user_question"
    ]


def test_react_loop_defaults_low_risk_cleanup_question_tool(tmp_path: Any) -> None:
    provider = ScriptedProvider(
        [
            {
                "tool_calls": [
                    {
                        "id": "call_question",
                        "name": "ask_user_question",
                        "arguments": {
                            "question": "Detected generated placeholder %SystemDrive%/. Should I delete it?",
                            "options": [
                                {
                                    "label": "Delete",
                                    "value": "delete",
                                    "description": "Remove only the generated placeholder.",
                                    "recommended": True,
                                },
                                {
                                    "label": "Keep",
                                    "value": "keep",
                                    "description": "Leave it untouched.",
                                },
                            ],
                            "summary": "Confirm generated/local cleanup.",
                            "reason": "cleanup_generated_local_noise",
                        },
                    }
                ],
            },
            {"final": "Removed the generated placeholder only."},
        ]
    )
    runtime = _make_builtin_runtime(tmp_path, provider)
    session = _open_session(runtime, tmp_path)

    task = _call_result(
        _rpc(
            runtime,
            "message.send",
            {"sessionId": session["id"], "content": "优化一下，systemdrive看看需要不需要，不需要删掉"},
        ),
        "task",
    )

    assert task["status"] == "completed"
    assert not any(event["type"] == "ask_user_question" for event in runtime.events)
    tool_messages = [
        message
        for message in provider.calls[1]["context"]["messages"]
        if message.get("role") == "tool" and message.get("name") == "ask_user_question"
    ]
    assert len(tool_messages) == 1
    payload = json.loads(tool_messages[0]["content"])
    assert payload.get("defaulted") is True, payload
    assert payload["status"] == "answered"
    assert "cleanup intent" in payload["answer"]
    assert not [
        event
        for event in runtime.events
        if event["type"] == "tool_result"
        and event["payload"].get("toolName") == "ask_user_question"
    ]


def test_react_loop_defaults_question_when_user_explicitly_says_not_to_ask(tmp_path: Any) -> None:
    provider = ScriptedProvider(
        [
            {
                "tool_calls": [
                    {
                        "id": "call_question",
                        "name": "ask_user_question",
                        "arguments": {
                            "question": "Which reading order should I use before summarizing the project?",
                            "options": [
                                {
                                    "label": "README first",
                                    "value": "readme_first",
                                    "description": "Start from README and then inspect source.",
                                    "recommended": True,
                                },
                                {
                                    "label": "Source first",
                                    "value": "source_first",
                                    "description": "Start from code and use docs as context.",
                                },
                            ],
                            "summary": "Choose inspection order.",
                            "reason": "output_preference",
                        },
                    }
                ],
            },
            {"final": "Used the default reading order and summarized the project."},
        ]
    )
    runtime = _make_builtin_runtime(tmp_path, provider)
    session = _open_session(runtime, tmp_path)

    task = _call_result(
        _rpc(
            runtime,
            "message.send",
            {
                "sessionId": session["id"],
                "content": "Summarize the current project. Do not ask me; choose sensible defaults.",
            },
        ),
        "task",
    )

    assert task["status"] == "completed"
    assert not any(event["type"] == "ask_user_question" for event in runtime.events)
    tool_messages = [
        message
        for message in provider.calls[1]["context"]["messages"]
        if message.get("role") == "tool" and message.get("name") == "ask_user_question"
    ]
    assert len(tool_messages) == 1
    payload = json.loads(tool_messages[0]["content"])
    assert payload["defaulted"] is True
    assert payload["status"] == "answered"
    assert "README first" in payload["answer"]
    assert not [
        event
        for event in runtime.events
        if event["type"] == "tool_result"
        and event["payload"].get("toolName") == "ask_user_question"
    ]


def test_react_loop_defaults_low_risk_continuation_question_tool(tmp_path: Any) -> None:
    provider = ScriptedProvider(
        [
            {
                "tool_calls": [
                    {
                        "id": "call_question",
                        "name": "ask_user_question",
                        "arguments": {
                            "question": "Continue with a third read-only synthesis agent using the existing findings?",
                            "options": [
                                {
                                    "label": "Proceed",
                                    "value": "proceed",
                                    "description": "Continue with synthesis using current findings.",
                                    "recommended": True,
                                }
                            ],
                            "summary": "Approval to launch final synthesis agent.",
                            "reason": "continue_readonly_analysis",
                        },
                    }
                ],
            },
            {"final": "Continued with the default read-only synthesis."},
        ]
    )
    runtime = _make_builtin_runtime(tmp_path, provider)
    session = _open_session(runtime, tmp_path)

    task = _call_result(
        _rpc(
            runtime,
            "message.send",
            {"sessionId": session["id"], "content": "Use multiple agents to analyze the project. Do not edit files."},
        ),
        "task",
    )

    assert task["status"] == "completed"
    assert not any(event["type"] == "ask_user_question" for event in runtime.events)
    tool_messages = [
        message
        for message in provider.calls[1]["context"]["messages"]
        if message.get("role") == "tool" and message.get("name") == "ask_user_question"
    ]
    assert len(tool_messages) == 1
    payload = json.loads(tool_messages[0]["content"])
    assert payload["defaulted"] is True
    assert payload["status"] == "answered"
    assert "read-only analysis" in payload["answer"]
    assert not [
        event
        for event in runtime.events
        if event["type"] == "tool_result"
        and event["payload"].get("toolName") == "ask_user_question"
    ]
    assert not [
        event
        for event in runtime.events
        if event["type"] in {"content_start", "tool_use_complete", "content_delta"}
        and event["payload"].get("toolName") == "ask_user_question"
    ]


def test_react_loop_still_pauses_for_blocking_credentials_even_when_user_says_not_to_ask(tmp_path: Any) -> None:
    provider = ScriptedProvider(
        [
            {
                "tool_calls": [
                    {
                        "id": "call_question",
                        "name": "ask_user_question",
                        "arguments": {
                            "question": "Please provide the API key required to access the private service.",
                            "summary": "Missing required credential.",
                            "reason": "missing_required_credential",
                        },
                    }
                ],
            },
        ]
    )
    runtime = _make_builtin_runtime(tmp_path, provider)
    session = _open_session(runtime, tmp_path)

    task = _call_result(
        _rpc(
            runtime,
            "message.send",
            {
                "sessionId": session["id"],
                "content": "Deploy it without asking me follow-up questions.",
            },
        ),
        "task",
    )

    assert task["status"] == "paused"
    question_event = next(event for event in runtime.events if event["type"] == "ask_user_question")
    assert question_event["payload"]["question"] == "Please provide the API key required to access the private service."


def test_cancelled_completion_review_approval_is_ignored(tmp_path: Any) -> None:
    runtime = _make_runtime(tmp_path, ScriptedProvider([]))
    session = _open_session(runtime, tmp_path)
    task = runtime.store.create_task(
        session_id=session["id"],
        task_type="chat",
        goal="cleanup generated path",
        plan=[],
        status="cancelled",
    )
    approval = runtime.store.create_approval(
        task_id=task["id"],
        kind="completion_review",
        request={"summary": "Internal completion review", "structuredResult": {"status": "needs_more_work"}},
    )

    result = _call_result(
        _rpc(runtime, "approval.submit", {"approvalId": approval["id"], "decision": "approved"}),
        "approval",
    )

    stored_task = runtime.store.get_task({"taskId": task["id"]})["task"]
    assert stored_task["status"] == "cancelled"
    assert result["decision"] == "approved"
    resolved = [event for event in runtime.events if event["type"] == "approval.resolved"]
    assert resolved[-1]["payload"]["ignored"] is True
    assert not any(event["type"] == "task.runtime_work_waiting" for event in runtime.events)
    assert not any(event["type"] == "task.failed" for event in runtime.events)


def test_react_loop_plan_mode_waits_for_plan_approval_and_resumes(tmp_path: Any) -> None:
    provider = ScriptedProvider(
        [
            {
                "tool_calls": [
                    {"id": "call_enter", "name": "enter_plan_mode", "arguments": {"reason": "Need a plan first."}},
                ],
            },
            {
                "tool_calls": [
                    {"id": "call_read", "name": "read_file", "arguments": {"path": "README.md"}},
                ],
            },
            {
                "tool_calls": [
                    {
                        "id": "call_exit",
                        "name": "exit_plan_mode",
                        "arguments": {
                            "summary": "Update the README after confirming the file contents.",
                            "steps": ["Inspect README", "Patch README", "Run focused verification"],
                        },
                    },
                ],
            },
            {"final": "Plan approved; continuing with execution."},
        ]
    )
    runtime = _make_builtin_runtime(tmp_path, provider)
    session = _open_session(runtime, tmp_path)
    workspace_root = Path(runtime.store.require_workspace(session["workspaceId"])["rootPath"])
    (workspace_root / "README.md").write_text("hello\n", encoding="utf-8")

    task = _call_result(
        _rpc(runtime, "message.send", {"sessionId": session["id"], "content": "plan then update readme", "mode": "plan"}),
        "task",
    )

    assert task["status"] == "waiting_approval"
    second_policy = provider.calls[1]["context"]["tool_policy_decision"]
    assert second_policy["phase"] == "plan_mode"
    assert "read_file" in second_policy["allowedToolNames"]
    assert "exit_plan_mode" in second_policy["allowedToolNames"]
    assert "write_file" not in second_policy["allowedToolNames"]
    approval_event = next(event for event in runtime.events if event["type"] == "approval.requested" and event["payload"].get("kind") == "plan")
    approval_id = approval_event["payload"]["approvalId"]
    approval_request = approval_event["payload"]["request"]
    assert approval_request["stepCount"] == 3
    assert approval_request["subtaskCount"] == 3
    assert "raw" not in approval_request
    assert "raw" not in approval_request.get("plan", {})
    assert approval_request["previewRows"] == [
        {"label": "目标", "value": "plan then update readme"},
        {"label": "模式", "value": "plan"},
        {"label": "子任务", "value": "3"},
        {"label": "执行顺序", "value": "sub-0 -> sub-1 -> sub-2"},
    ]
    assert approval_request["previewSections"] == [
        {
            "kind": "items",
            "title": "已拆分 3 个子任务",
            "items": [
                {"id": "sub-0", "title": "Inspect README"},
                {"id": "sub-1", "title": "Patch README"},
                {"id": "sub-2", "title": "Run focused verification"},
            ],
        }
    ]
    tool_complete = next(
        event
        for event in runtime.events
        if event["type"] == "tool_use_complete" and event["payload"].get("toolUseId") == "call_exit"
    )
    assert tool_complete["payload"]["input"]["stepCount"] == 3
    assert "raw" not in tool_complete["payload"]["input"]
    assert "raw" not in tool_complete["payload"]["input"].get("plan", {})
    permission = next(
        event
        for event in runtime.events
        if event["type"] == "permission_request" and event["payload"].get("requestId") == approval_id
    )
    assert permission["payload"]["input"]["subtaskCount"] == 3
    assert "raw" not in permission["payload"]["input"]
    assert "raw" not in permission["payload"]["input"].get("plan", {})
    planning_started = next(event for event in runtime.events if event["type"] == "task.planning.started")
    assert planning_started["visibility"] == "panel"
    assert planning_started["payload"]["source"] == "enter_plan_mode"
    assert planning_started["payload"]["summary"] == "Need a plan first."
    assert "yuanbao" not in planning_started
    planning_proposed = next(event for event in runtime.events if event["type"] == "task.planning.proposed")
    assert planning_proposed["visibility"] == "panel"
    assert planning_proposed["payload"]["source"] == "exit_plan_mode"
    assert planning_proposed["payload"]["subtaskCount"] == 3
    assert planning_proposed["payload"]["plan"]["subtasks"] == [
        {"id": "sub-0", "title": "Inspect README"},
        {"id": "sub-1", "title": "Patch README"},
        {"id": "sub-2", "title": "Run focused verification"},
    ]
    assert "raw" not in json.dumps(planning_proposed["payload"], ensure_ascii=False)
    assert "yuanbao" not in planning_proposed

    _rpc(runtime, "approval.submit", {"approvalId": approval_id, "decision": "approved"})
    final_task = _call_result(_rpc(runtime, "task.get", {"taskId": task["id"]}), "task")

    assert final_task["status"] == "completed"
    assert final_task["resultSummary"] == "Plan approved; continuing with execution."
    exit_tool_messages = [
        message
        for message in provider.calls[3]["context"]["messages"]
        if message.get("role") == "tool" and message.get("name") == "exit_plan_mode"
    ]
    assert len(exit_tool_messages) == 1
    payload = json.loads(exit_tool_messages[0]["content"])
    assert payload["status"] == "plan_approved"
    assert payload["plan"]["steps"] == ["Inspect README", "Patch README", "Run focused verification"]
    planning_approved = next(event for event in runtime.events if event["type"] == "task.planning.approved")
    assert planning_approved["visibility"] == "panel"
    assert planning_approved["payload"]["decision"] == "approved"
    replay_events = _rpc(runtime, "events.after", {"sessionId": session["id"], "afterSeq": 0})["result"]["events"]
    replay_planning = [event for event in replay_events if event["type"].startswith("task.planning.")]
    assert [event["type"] for event in replay_planning] == [
        "task.planning.started",
        "task.planning.proposed",
        "task.planning.approved",
    ]
    assert all("yuanbao" not in event for event in replay_planning)
    assert all("raw" not in json.dumps(event["payload"], ensure_ascii=False) for event in replay_planning)


def test_plan_approval_resumes_remaining_agent_tool_call(tmp_path: Any) -> None:
    provider = ScriptedProvider(
        [
            {
                "tool_calls": [
                    {"id": "call_enter", "name": "enter_plan_mode", "arguments": {"reason": "Need a plan first."}},
                ],
            },
            {
                "tool_calls": [
                    {
                        "id": "call_exit",
                        "name": "exit_plan_mode",
                        "arguments": {
                            "summary": "Split and delegate the follow-up.",
                            "steps": [
                                "Inspect current workflow",
                                "Implement the selected change",
                                "Review the result",
                            ],
                        },
                    },
                    {
                        "id": "call_agent",
                        "name": "agent",
                        "arguments": {
                            "prompt": "Inspect the workflow after plan approval and summarize the next implementation step.",
                            "agent_type": "reviewer",
                            "description": "Review workflow after approval",
                            "tool_allowlist": ["read_file", "search_files"],
                        },
                    },
                ],
            },
            {"final": "Plan approved and the reviewer agent finished."},
        ]
    )
    runtime = _make_builtin_runtime(tmp_path, provider)
    session = _open_session(runtime, tmp_path)
    dispatched: list[dict[str, Any]] = []

    def dispatch_agent(params: dict[str, Any]) -> dict[str, Any]:
        dispatched.append(dict(params))
        return {
            "status": "completed",
            "summary": "Reviewer agent inspected workflow.",
            "childTaskId": "ctask_review_1",
            "workerId": "worker_review_1",
            "agentType": params.get("agentType"),
        }

    runtime.orchestrator._subagent_service.dispatch = dispatch_agent  # noqa: SLF001

    task = _call_result(
        _rpc(runtime, "message.send", {"sessionId": session["id"], "content": "plan then send reviewer", "mode": "plan"}),
        "task",
    )

    assert task["status"] == "waiting_approval"
    state = runtime.store.get_pending_react_state(task["id"])
    assert state is not None
    assert [call["id"] for call in state["remaining_tool_calls"]] == ["call_agent"]
    approval_event = next(event for event in runtime.events if event["type"] == "approval.requested" and event["payload"].get("kind") == "plan")
    approval_id = approval_event["payload"]["approvalId"]

    _rpc(runtime, "approval.submit", {"approvalId": approval_id, "decision": "approved"})
    final_task = _call_result(_rpc(runtime, "task.get", {"taskId": task["id"]}), "task")

    assert final_task["status"] == "completed"
    assert final_task["resultSummary"] == "Plan approved and the reviewer agent finished."
    assert len(dispatched) == 1
    assert dispatched[0]["agentType"] == "reviewer"
    assert dispatched[0]["childToolAllowlist"] == ["read_file", "search_files"]
    final_tool_messages = [
        message
        for message in provider.calls[2]["context"]["messages"]
        if message.get("role") == "tool"
    ]
    assert [message["name"] for message in final_tool_messages[-2:]] == ["exit_plan_mode", "agent"]
    agent_payload = json.loads(final_tool_messages[-1]["content"])
    assert agent_payload["summary"] == "Reviewer agent inspected workflow."
    assert agent_payload["status"] == "completed"
    assert runtime.store.get_pending_react_state(task["id"]) is None
    agent_started = [
        event
        for event in runtime.events
        if event["type"] == "tool.started" and event["payload"].get("toolName") == "agent"
    ]
    assert len(agent_started) == 1
    assert agent_started[0]["payload"]["arguments"]["agentType"] == "reviewer"


def test_react_loop_plan_mode_rejection_returns_to_model(tmp_path: Any) -> None:
    provider = ScriptedProvider(
        [
            {
                "tool_calls": [
                    {"id": "call_enter", "name": "enter_plan_mode", "arguments": {"reason": "Need a plan first."}},
                ],
            },
            {
                "tool_calls": [
                    {
                        "id": "call_exit",
                        "name": "exit_plan_mode",
                        "arguments": {
                            "summary": "Patch the core service.",
                            "steps": ["Patch service", "Run regression tests"],
                        },
                    },
                ],
            },
            {"final": "Plan rejected; I will revise before changing files."},
        ]
    )
    runtime = _make_builtin_runtime(tmp_path, provider)
    session = _open_session(runtime, tmp_path)

    task = _call_result(
        _rpc(runtime, "message.send", {"sessionId": session["id"], "content": "plan before changing files", "mode": "plan"}),
        "task",
    )
    approval_event = next(event for event in runtime.events if event["type"] == "approval.requested" and event["payload"].get("kind") == "plan")
    approval_id = approval_event["payload"]["approvalId"]

    assert task["status"] == "waiting_approval"
    _rpc(runtime, "approval.submit", {"approvalId": approval_id, "decision": "rejected", "comment": "Too broad."})
    final_task = _call_result(_rpc(runtime, "task.get", {"taskId": task["id"]}), "task")

    assert final_task["status"] == "completed"
    assert final_task["resultSummary"] == "Plan rejected; I will revise before changing files."
    exit_tool_messages = [
        message
        for message in provider.calls[2]["context"]["messages"]
        if message.get("role") == "tool" and message.get("name") == "exit_plan_mode"
    ]
    assert len(exit_tool_messages) == 1
    payload = json.loads(exit_tool_messages[0]["content"])
    assert payload["status"] == "plan_rejected"
    assert payload["decision"] == "rejected"
    assert payload["comment"] == "Too broad."


def test_react_loop_exit_plan_mode_without_plan_mode_returns_tool_error(tmp_path: Any) -> None:
    provider = ScriptedProvider(
        [
            {
                "tool_calls": [
                    {
                        "id": "call_exit_without_plan",
                        "name": "exit_plan_mode",
                        "arguments": {
                            "summary": "This should not create a plan approval.",
                            "steps": ["Inspect", "Change", "Verify"],
                        },
                    },
                ],
            },
            {"final": "I will continue without opening plan approval."},
        ]
    )
    runtime = _make_builtin_runtime(tmp_path, provider)
    session = _open_session(runtime, tmp_path)

    task = _call_result(
        _rpc(runtime, "message.send", {"sessionId": session["id"], "content": "explain a possible plan"}),
        "task",
    )

    assert task["status"] == "completed"
    assert task["resultSummary"] == "I will continue without opening plan approval."
    assert not [
        event for event in runtime.events
        if event["type"] == "approval.requested" and event["payload"].get("kind") == "plan"
    ]
    tool_results = provider.calls[1]["context"]["tool_results"]
    exit_result = next(result for result in tool_results if result["name"] == "exit_plan_mode")
    assert exit_result["result"]["status"] == "blocked"
    assert "after enter_plan_mode" in exit_result["result"]["error"]
    tool_start = next(
        event for event in runtime.events
        if event["type"] == "content_start"
        and event["payload"].get("blockType") == "tool_use"
        and event["payload"].get("toolUseId") == "call_exit_without_plan"
    )
    tool_complete = next(
        event for event in runtime.events
        if event["type"] == "tool_use_complete"
        and event["payload"].get("toolUseId") == "call_exit_without_plan"
    )
    tool_result = next(
        event for event in runtime.events
        if event["type"] == "tool_result"
        and event["payload"].get("toolUseId") == "call_exit_without_plan"
    )
    assert tool_start["payload"]["toolName"] == "exit_plan_mode"
    assert tool_start["payload"]["toolCategory"] == "tool"
    assert tool_complete["payload"]["toolName"] == "exit_plan_mode"
    assert tool_result["payload"]["toolName"] == "exit_plan_mode"
    assert tool_result["payload"]["isError"] is True
    assert tool_result["payload"]["resultSummary"] == "Plan approval was not requested because plan mode is not active."
    assert not [event for event in runtime.events if event["type"] == "assistant_progress"]


def test_react_loop_plan_mode_blocks_same_batch_write_tool(tmp_path: Any) -> None:
    provider = ScriptedProvider(
        [
            {
                "tool_calls": [
                    {"id": "call_enter", "name": "enter_plan_mode", "arguments": {"reason": "Plan first."}},
                    {"id": "call_write", "name": "write_file", "arguments": {"path": "x.txt", "content": "bad"}},
                ],
            },
            {"final": "Write was blocked in plan mode."},
        ]
    )
    runtime = _make_builtin_runtime(tmp_path, provider)
    session = _open_session(runtime, tmp_path)

    task = _call_result(
        _rpc(runtime, "message.send", {"sessionId": session["id"], "content": "plan but do not write", "mode": "plan"}),
        "task",
    )

    assert task["status"] == "completed"
    tool_results = provider.calls[1]["context"]["tool_results"]
    write_result = next(result for result in tool_results if result["name"] == "write_file")
    assert write_result["result"]["status"] == "blocked"
    assert "Plan mode allows only read-only tools" in write_result["result"]["error"]
    assert write_result["toolCategory"] == "tool"
    assert any(
        event["type"] == "tool_result"
        and event["payload"].get("toolUseId") == "call_write"
        and event["payload"].get("isError") is True
        for event in runtime.events
    )
    assert not [event for event in runtime.events if event["type"] == "assistant_progress"]


def test_react_loop_persists_pending_state_when_approval_is_required(tmp_path: Any) -> None:
    provider = ScriptedProvider(
        [
            {
                "message": "Need approval before running.",
                "tool_calls": [
                    {
                        "id": "call_command",
                        "name": "run_command",
                        "arguments": {"command": "Write-Output persisted"},
                    }
                ],
            },
            {"final": "This response is not reached before approval."},
        ]
    )
    runtime = _make_runtime(tmp_path, provider)

    def run_command(params: dict[str, Any]) -> dict[str, Any]:
        approval = runtime.store.create_approval(
            task_id=params["taskId"],
            kind="run_command",
            request={"command": params["command"]},
        )
        return {"status": "approval_required", "approval": approval, "command": params["command"]}

    runtime.server._orchestrator._tool_registry.register("run_command", run_command)  # noqa: SLF001
    session = _open_session(runtime, tmp_path)

    task = _call_result(
        _rpc(
            runtime,
            "message.send",
            {"sessionId": session["id"], "content": "run a persisted command"},
        ),
        "task",
    )

    state = runtime.store.get_pending_react_state(task["id"])
    assert state is not None
    assert state["task_id"] == task["id"]
    assert state["session_id"] == session["id"]
    assert state["goal"] == "run a persisted command"
    assert state["messages"][-1]["tool_calls"][0]["id"] == "call_command"
    assert state["tool_results"] == []
    assert state["pending_tool_call"]["id"] == "call_command"
    assert state["pending_tool_spec"]["name"] == "run_command"
    assert state["remaining_tool_calls"] == []
    assert state["steps"] == 1
    assert state["react_started"] is True


def test_react_loop_restores_pending_state_from_sqlite_after_memory_is_cleared(tmp_path: Any) -> None:
    provider = ScriptedProvider(
        [
            {
                "tool_calls": [
                    {
                        "id": "call_command",
                        "name": "run_command",
                        "arguments": {"command": "Write-Output restored"},
                    }
                ]
            },
            {"final": "Command completed after SQLite restore."},
        ]
    )
    runtime = _make_runtime(tmp_path, provider)

    def run_command(params: dict[str, Any]) -> dict[str, Any]:
        if not params.get("approvalId"):
            approval = runtime.store.create_approval(
                task_id=params["taskId"],
                kind="run_command",
                request={"command": params["command"]},
            )
            return {"status": "approval_required", "approval": approval, "command": params["command"]}
        return {
            "status": "completed",
            "stdout": "restored\n",
            "stderr": "",
            "exitCode": 0,
        }

    runtime.server._orchestrator._tool_registry.register("run_command", run_command)  # noqa: SLF001
    session = _open_session(runtime, tmp_path)

    task = _call_result(
        _rpc(
            runtime,
            "message.send",
            {"sessionId": session["id"], "content": "run after process restart"},
        ),
        "task",
    )
    approval_id = next(event for event in runtime.events if event["type"] == "approval.requested")["payload"][
        "approvalId"
    ]
    runtime.server._orchestrator._pending_react_tasks.clear()  # noqa: SLF001

    _rpc(runtime, "approval.submit", {"approvalId": approval_id, "decision": "approved"})

    final_task = _call_result(_rpc(runtime, "task.get", {"taskId": task["id"]}), "task")
    assert final_task["status"] == "completed"
    assert final_task["resultSummary"] == "Command completed after SQLite restore."
    assert len(provider.calls) == 2
    assert provider.calls[1]["context"]["tool_results"][0]["result"]["stdout"] == "restored\n"


def test_react_loop_approval_submit_returns_resumed_task_and_is_idempotent(tmp_path: Any) -> None:
    provider = ScriptedProvider(
        [
            {
                "tool_calls": [
                    {
                        "id": "call_command",
                        "name": "run_command",
                        "arguments": {"command": "Write-Output approved"},
                    }
                ]
            },
            {"final": "Command completed after approval."},
        ]
    )
    runtime = _make_runtime(tmp_path, provider)

    def run_command(params: dict[str, Any]) -> dict[str, Any]:
        if not params.get("approvalId"):
            approval = runtime.store.create_approval(
                task_id=params["taskId"],
                kind="run_command",
                request={"command": params["command"]},
            )
            return {"status": "approval_required", "approval": approval, "command": params["command"]}
        return {
            "status": "completed",
            "stdout": "approved\n",
            "stderr": "",
            "exitCode": 0,
        }

    runtime.server._orchestrator._tool_registry.register("run_command", run_command)  # noqa: SLF001
    session = _open_session(runtime, tmp_path)

    task = _call_result(
        _rpc(
            runtime,
            "message.send",
            {"sessionId": session["id"], "content": "run after approval"},
        ),
        "task",
    )
    approval_id = next(event for event in runtime.events if event["type"] == "approval.requested")["payload"][
        "approvalId"
    ]
    event_count_before = len(runtime.events)

    first = _call_result(
        _rpc(runtime, "approval.submit", {"approvalId": approval_id, "decision": "approved"}),
        "task",
    )
    event_count_after_first = len(runtime.events)
    second = _rpc(runtime, "approval.submit", {"approvalId": approval_id, "decision": "approved"})["result"]

    assert first["status"] == "completed"
    assert first["resultSummary"] == "Command completed after approval."
    assert second["ignored"] is True
    assert len(runtime.events) == event_count_after_first
    assert event_count_after_first > event_count_before
    assert len(provider.calls) == 2
    assert runtime.store.get_pending_react_state(task["id"]) is None


def test_react_loop_approval_submit_returns_waiting_when_next_tool_needs_approval(tmp_path: Any) -> None:
    provider = ScriptedProvider(
        [
            {
                "tool_calls": [
                    {
                        "id": "call_first",
                        "name": "run_command",
                        "arguments": {"command": "Write-Output first"},
                    },
                    {
                        "id": "call_second",
                        "name": "run_command",
                        "arguments": {"command": "Write-Output second"},
                    },
                ]
            },
            {"final": "Both commands completed."},
        ]
    )
    runtime = _make_runtime(tmp_path, provider)

    def run_command(params: dict[str, Any]) -> dict[str, Any]:
        if not params.get("approvalId"):
            approval = runtime.store.create_approval(
                task_id=params["taskId"],
                kind="run_command",
                request={"command": params["command"]},
            )
            return {"status": "approval_required", "approval": approval, "command": params["command"]}
        return {
            "status": "completed",
            "stdout": "approved\n",
            "stderr": "",
            "exitCode": 0,
        }

    runtime.server._orchestrator._tool_registry.register("run_command", run_command)  # noqa: SLF001
    session = _open_session(runtime, tmp_path)
    task = _call_result(
        _rpc(
            runtime,
            "message.send",
            {"sessionId": session["id"], "content": "run two commands after approval"},
        ),
        "task",
    )
    first_approval_id = next(event for event in runtime.events if event["type"] == "approval.requested")["payload"][
        "approvalId"
    ]

    resumed = _call_result(
        _rpc(runtime, "approval.submit", {"approvalId": first_approval_id, "decision": "approved"}),
        "task",
    )
    approval_ids = [
        event["payload"]["approvalId"]
        for event in runtime.events
        if event["type"] == "approval.requested"
    ]

    assert resumed["status"] == "waiting_approval"
    assert runtime.store.get_task({"taskId": task["id"]})["task"]["status"] == "waiting_approval"
    assert len(approval_ids) == 2
    assert approval_ids[1] != first_approval_id
    assert runtime.store.get_pending_react_state(task["id"]) is not None
    assert len(provider.calls) == 1


def test_react_loop_rejection_cleans_pending_state(tmp_path: Any) -> None:
    provider = ScriptedProvider(
        [
            {
                "tool_calls": [
                    {
                        "id": "call_command",
                        "name": "run_command",
                        "arguments": {"command": "Write-Output rejected"},
                    }
                ]
            }
        ]
    )
    runtime = _make_runtime(tmp_path, provider)

    def run_command(params: dict[str, Any]) -> dict[str, Any]:
        approval = runtime.store.create_approval(
            task_id=params["taskId"],
            kind="run_command",
            request={"command": params["command"]},
        )
        return {"status": "approval_required", "approval": approval, "command": params["command"]}

    runtime.server._orchestrator._tool_registry.register("run_command", run_command)  # noqa: SLF001
    session = _open_session(runtime, tmp_path)

    task = _call_result(
        _rpc(
            runtime,
            "message.send",
            {"sessionId": session["id"], "content": "reject the command"},
        ),
        "task",
    )
    approval_id = next(event for event in runtime.events if event["type"] == "approval.requested")["payload"][
        "approvalId"
    ]
    assert runtime.store.get_pending_react_state(task["id"]) is not None

    _rpc(runtime, "approval.submit", {"approvalId": approval_id, "decision": "rejected"})

    final_task = _call_result(_rpc(runtime, "task.get", {"taskId": task["id"]}), "task")
    assert final_task["status"] == "failed"
    assert final_task["errorCode"] == "APPROVAL_REJECTED"
    assert runtime.store.get_pending_react_state(task["id"]) is None
    assert task["id"] not in runtime.server._orchestrator._pending_react_tasks  # noqa: SLF001
    messages = _call_result(_rpc(runtime, "message.list", {"sessionId": session["id"]}), "messages")
    assert [message["role"] for message in messages] == ["user", "assistant"]
    assert messages[1]["content"] == "Approval was rejected by the user."


def test_react_loop_completion_cleans_pending_state(tmp_path: Any) -> None:
    provider = ScriptedProvider(
        [
            {
                "tool_calls": [
                    {
                        "id": "call_command",
                        "name": "run_command",
                        "arguments": {"command": "Write-Output cleanup"},
                    }
                ]
            },
            {"final": "Cleaned up after completion."},
        ]
    )
    runtime = _make_runtime(tmp_path, provider)

    def run_command(params: dict[str, Any]) -> dict[str, Any]:
        if not params.get("approvalId"):
            approval = runtime.store.create_approval(
                task_id=params["taskId"],
                kind="run_command",
                request={"command": params["command"]},
            )
            return {"status": "approval_required", "approval": approval, "command": params["command"]}
        return {
            "status": "completed",
            "stdout": "cleanup\n",
            "stderr": "",
            "exitCode": 0,
        }

    runtime.server._orchestrator._tool_registry.register("run_command", run_command)  # noqa: SLF001
    session = _open_session(runtime, tmp_path)

    task = _call_result(
        _rpc(
            runtime,
            "message.send",
            {"sessionId": session["id"], "content": "cleanup pending state"},
        ),
        "task",
    )
    approval_id = next(event for event in runtime.events if event["type"] == "approval.requested")["payload"][
        "approvalId"
    ]
    assert runtime.store.get_pending_react_state(task["id"]) is not None

    _rpc(runtime, "approval.submit", {"approvalId": approval_id, "decision": "approved"})

    final_task = _call_result(_rpc(runtime, "task.get", {"taskId": task["id"]}), "task")
    assert final_task["status"] == "completed"
    assert runtime.store.get_pending_react_state(task["id"]) is None
    assert task["id"] not in runtime.server._orchestrator._pending_react_tasks  # noqa: SLF001


def test_cancelled_pending_approval_does_not_resume_react_tool(tmp_path: Any) -> None:
    provider = ScriptedProvider(
        [
            {
                "tool_calls": [
                    {
                        "id": "call_command",
                        "name": "run_command",
                        "arguments": {"command": "Write-Output cancelled"},
                    }
                ]
            },
            {"final": "This should not be reached after cancellation."},
        ]
    )
    executed_after_approval: list[str] = []
    runtime = _make_runtime(tmp_path, provider)

    def run_command(params: dict[str, Any]) -> dict[str, Any]:
        if params.get("approvalId"):
            executed_after_approval.append(params["approvalId"])
            return {"status": "completed", "stdout": "cancelled\n", "stderr": "", "exitCode": 0}
        approval = runtime.store.create_approval(
            task_id=params["taskId"],
            kind="run_command",
            request={"command": params["command"]},
        )
        return {"status": "approval_required", "approval": approval, "command": params["command"]}

    runtime.server._orchestrator._tool_registry.register("run_command", run_command)  # noqa: SLF001
    session = _open_session(runtime, tmp_path)
    task = _call_result(
        _rpc(runtime, "message.send", {"sessionId": session["id"], "content": "cancel a pending command"}),
        "task",
    )
    approval_id = next(event for event in runtime.events if event["type"] == "approval.requested")["payload"][
        "approvalId"
    ]

    cancelled = _call_result(_rpc(runtime, "task.cancel", {"taskId": task["id"]}), "task")
    _rpc(runtime, "approval.submit", {"approvalId": approval_id, "decision": "approved"})

    final_task = _call_result(_rpc(runtime, "task.get", {"taskId": task["id"]}), "task")
    assert cancelled["status"] == "cancelled"
    assert final_task["status"] == "cancelled"
    assert executed_after_approval == []
    assert len(provider.calls) == 1
    assert "task.cancelled" in [event["type"] for event in runtime.events]


def test_pause_pending_approval_blocks_submit_until_resume(tmp_path: Any) -> None:
    provider = ScriptedProvider(
        [
            {
                "tool_calls": [
                    {
                        "id": "call_command",
                        "name": "run_command",
                        "arguments": {"command": "Write-Output paused"},
                    }
                ]
            },
            {"final": "Command completed after pause and resume."},
        ]
    )
    executed_after_approval: list[str] = []
    runtime = _make_runtime(tmp_path, provider)

    def run_command(params: dict[str, Any]) -> dict[str, Any]:
        if params.get("approvalId"):
            executed_after_approval.append(params["approvalId"])
            return {"status": "completed", "stdout": "paused\n", "stderr": "", "exitCode": 0}
        approval = runtime.store.create_approval(
            task_id=params["taskId"],
            kind="run_command",
            request={"command": params["command"]},
        )
        return {"status": "approval_required", "approval": approval, "command": params["command"]}

    runtime.server._orchestrator._tool_registry.register("run_command", run_command)  # noqa: SLF001
    session = _open_session(runtime, tmp_path)
    task = _call_result(
        _rpc(runtime, "message.send", {"sessionId": session["id"], "content": "pause a pending command"}),
        "task",
    )
    approval_id = next(event for event in runtime.events if event["type"] == "approval.requested")["payload"][
        "approvalId"
    ]

    paused = _call_result(_rpc(runtime, "task.pause", {"taskId": task["id"]}), "task")
    _rpc(runtime, "approval.submit", {"approvalId": approval_id, "decision": "approved"})
    still_paused = _call_result(_rpc(runtime, "task.get", {"taskId": task["id"]}), "task")
    resumed = _call_result(_rpc(runtime, "task.resume", {"taskId": task["id"]}), "task")

    final_task = _call_result(_rpc(runtime, "task.get", {"taskId": task["id"]}), "task")
    assert paused["status"] == "paused"
    assert still_paused["status"] == "paused"
    assert resumed["status"] == "completed"
    assert final_task["status"] == "completed"
    assert final_task["resultSummary"] == "Command completed after pause and resume."
    assert len(executed_after_approval) == 1
    assert "task.paused" in [event["type"] for event in runtime.events]
    assert "task.resumed" in [event["type"] for event in runtime.events]


def test_pending_react_approval_recovers_with_new_orchestrator_and_store(tmp_path: Any) -> None:
    database_path = tmp_path / "runtime.sqlite3"
    provider = ScriptedProvider(
        [
            {
                "tool_calls": [
                    {
                        "id": "call_command",
                        "name": "run_command",
                        "arguments": {"command": "Write-Output recovered"},
                    }
                ]
            },
            {"final": "Command completed after new orchestrator recovery."},
        ]
    )
    first_runtime = _make_runtime_at_path(database_path, provider)

    def first_run_command(params: dict[str, Any]) -> dict[str, Any]:
        approval = first_runtime.store.create_approval(
            task_id=params["taskId"],
            kind="run_command",
            request={"command": params["command"]},
        )
        return {"status": "approval_required", "approval": approval, "command": params["command"]}

    first_runtime.server._orchestrator._tool_registry.register("run_command", first_run_command)  # noqa: SLF001
    session = _open_session(first_runtime, tmp_path)
    task = _call_result(
        _rpc(first_runtime, "message.send", {"sessionId": session["id"], "content": "recover after restart"}),
        "task",
    )
    approval_id = next(event for event in first_runtime.events if event["type"] == "approval.requested")["payload"][
        "approvalId"
    ]
    first_runtime.store.close()

    second_runtime = _make_runtime_at_path(database_path, provider)

    def second_run_command(params: dict[str, Any]) -> dict[str, Any]:
        assert params.get("approvalId") == approval_id
        return {"status": "completed", "stdout": "recovered\n", "stderr": "", "exitCode": 0}

    second_runtime.server._orchestrator._tool_registry.register("run_command", second_run_command)  # noqa: SLF001
    _rpc(second_runtime, "approval.submit", {"approvalId": approval_id, "decision": "approved"})

    final_task = _call_result(_rpc(second_runtime, "task.get", {"taskId": task["id"]}), "task")
    assert final_task["status"] == "completed"
    assert final_task["resultSummary"] == "Command completed after new orchestrator recovery."
    assert second_runtime.store.get_pending_react_state(task["id"]) is None
    assert len(provider.calls) == 2


def test_react_loop_converges_when_max_steps_are_exceeded(tmp_path: Any) -> None:
    provider = ScriptedProvider(
        [
            {
                "tool_calls": [
                    {
                        "id": "call_read",
                        "name": "read_file",
                        "arguments": {"path": "alpha.txt"},
                    }
                ]
            },
            {"final": "This answer should not be reached."},
        ]
    )
    runtime = _make_runtime(tmp_path, provider, {"read_file": lambda _params: {"content": "alpha"}})
    runtime.store.update_config({"config": {
        "policy": {"maxTaskSteps": 1},
        "autonomy": {"activeProfileId": "test", "profiles": [{"id": "test", "maxSteps": 1}]},
    }})
    session = _open_session(runtime, tmp_path)

    task = _call_result(
        _rpc(
            runtime,
            "message.send",
            {"sessionId": session["id"], "content": "read alpha"},
        ),
        "task",
    )

    assert task["status"] == "paused"
    assert task.get("errorCode") is None
    assert task.get("resultSummary") is None
    workflow = task["routing"]["mainWorkflow"]
    assert workflow["budget"]["exhausted"] is True
    assert workflow["budget"]["exhaustedReason"] == "max_steps"
    assert workflow["budget"]["consumedSteps"] == 1
    assert workflow["budget"]["pressure"] == "exhausted"
    assert workflow["budget"]["dimensions"]["steps"]["pressure"] == "exhausted"
    assert workflow["convergence"]["state"] == "partial_result"
    assert workflow["convergence"]["requiresUserDecision"] is True
    assert workflow["convergence"]["recommendedAction"] == "review_partial"
    assert workflow["convergence"]["resumePolicy"] == "requires_user_follow_up"
    event_types = [event["type"] for event in runtime.events]
    assert "tool.completed" in event_types
    assert "task.budget.exhausted" in event_types
    budget_progress = next(
        event for event in runtime.events
        if event["type"] == "task.budget.progress"
        and event["payload"].get("phase") == "budget_exhausted"
    )
    assert budget_progress["payload"]["summary"] == "已达到步骤预算，正在整理当前进展"
    assert budget_progress["payload"]["recommendedAction"] == "review_partial"
    assert not [event for event in runtime.events if event["type"] == "assistant_progress"]
    assert runtime.store.get_pending_react_state(task["id"]) is not None
    assert any(event["type"] == "ask_user_question" for event in runtime.events)


def test_react_loop_fails_when_max_steps_exhausted_with_only_failed_tools(tmp_path: Any) -> None:
    provider = ScriptedProvider(
        [
            {
                "tool_calls": [
                    {
                        "id": "call_read",
                        "name": "read_file",
                        "arguments": {},
                    }
                ]
            },
            {"final": "Budget continuation completed."},
        ]
    )

    def fail_read(_params: dict[str, Any]) -> dict[str, Any]:
        return {"status": "failed", "error": "Missing required parameters for read_file: path"}

    runtime = _make_runtime(tmp_path, provider, {"read_file": fail_read})
    runtime.store.update_config({"config": {
        "policy": {"maxTaskSteps": 1},
        "autonomy": {"activeProfileId": "test", "profiles": [{"id": "test", "maxSteps": 1}]},
    }})
    session = _open_session(runtime, tmp_path)

    task = _call_result(
        _rpc(
            runtime,
            "message.send",
            {"sessionId": session["id"], "content": "read alpha"},
        ),
        "task",
    )

    assert task["status"] == "failed"
    assert task["errorCode"] == "MAX_STEPS_NO_SUCCESSFUL_TOOLS"
    assert "maxTaskSteps" in task["resultSummary"]


def test_react_loop_records_budget_pressure_before_exhaustion(tmp_path: Any) -> None:
    provider = ScriptedProvider(
        [
            {
                "tool_calls": [
                    {
                        "id": "call_read_1",
                        "name": "read_file",
                        "arguments": {"path": "alpha.txt"},
                    }
                ]
            },
            {
                "tool_calls": [
                    {
                        "id": "call_read_2",
                        "name": "read_file",
                        "arguments": {"path": "beta.txt"},
                    }
                ]
            },
            {"final": "Done."},
        ]
    )
    runtime = _make_runtime(tmp_path, provider, {"read_file": lambda params: {"content": params["path"]}})
    runtime.store.update_config({"config": {
        "policy": {"maxTaskSteps": 3},
        "autonomy": {"activeProfileId": "test", "profiles": [{"id": "test", "maxSteps": 3}]},
    }})
    session = _open_session(runtime, tmp_path)

    task = _call_result(
        _rpc(
            runtime,
            "message.send",
            {"sessionId": session["id"], "content": "read alpha then beta"},
        ),
        "task",
    )

    assert task["status"] == "completed"
    workflow = task["routing"]["mainWorkflow"]
    assert workflow["budget"]["pressure"] == "critical"
    assert workflow["budget"]["dimensions"]["steps"]["consumed"] == 3
    assert workflow["budget"]["dimensions"]["steps"]["remaining"] == 0
    assert workflow["budget"]["dimensions"]["steps"]["completedAtLimit"] is True
    assert workflow["convergence"]["reason"] == "step_budget_critical"
    assert workflow["convergence"]["recommendedAction"] == "focus_or_wrap_up"
    pressure_events = [event for event in runtime.events if event["type"] == "task.budget.pressure"]
    assert pressure_events
    assert pressure_events[-1]["payload"]["pressure"] == "critical"
    progress_events = [
        event for event in runtime.events
        if event["type"] == "task.budget.progress" and event["payload"].get("phase") == "budget_pressure"
    ]
    assert progress_events
    assert progress_events[-1]["payload"]["summary"] == "步骤预算接近上限，正在收束当前任务"
    assert progress_events[-1]["payload"]["pressure"] == "critical"
    assert not [event for event in runtime.events if event["type"] == "assistant_progress"]


def test_react_loop_records_budget_convergence_runtime_fallback(tmp_path: Any) -> None:
    provider = ScriptedProvider(
        [
            {
                "tool_calls": [
                    {
                        "id": "call_read",
                        "name": "read_file",
                        "arguments": {"path": "alpha.txt"},
                    }
                ]
            },
            {"final": "Budget continuation completed."},
        ]
    )
    runtime = _make_runtime(tmp_path, provider, {"read_file": lambda _params: {"content": "alpha"}})
    runtime.store.update_config({"config": {
        "policy": {"maxTaskSteps": 1},
        "autonomy": {"activeProfileId": "test", "profiles": [{"id": "test", "maxSteps": 1}]},
    }})
    session = _open_session(runtime, tmp_path)

    task = _call_result(
        _rpc(
            runtime,
            "message.send",
            {"sessionId": session["id"], "content": "read alpha"},
        ),
        "task",
    )

    workflow = task["routing"]["mainWorkflow"]
    assert task["status"] == "paused"
    assert workflow["convergence"]["source"] == "runtime_fallback"
    assert workflow["convergence"]["recommendedAction"] == "review_partial"
    assert "Tool results so far" in workflow["convergence"]["handoffFocus"]
    assert workflow["convergence"]["resumePolicy"] == "requires_user_follow_up"
    assert "advisorProposalId" not in workflow["convergence"]
    proposals = runtime.store.list_proposals({
        "taskId": task["id"],
        "kind": "budget_convergence",
    })["proposals"]
    assert proposals == []
    event_types = [event["type"] for event in runtime.events]
    assert "agent.decision.budget_convergence" in event_types
    budget_progress = next(
        event for event in runtime.events
        if event["type"] == "task.budget.progress"
        and event["payload"].get("phase") == "budget_exhausted"
    )
    assert budget_progress["payload"]["recommendedAction"] == "review_partial"
    assert not [event for event in runtime.events if event["type"] == "assistant_progress"]
    question_event = next(event for event in runtime.events if event["type"] == "ask_user_question")
    assert question_event["payload"]["question"] == workflow["convergence"]["handoffFocus"]
    assert question_event["payload"]["resumePolicy"] == "requires_user_follow_up"
    assert runtime.store.get_pending_react_state(task["id"]) is not None

    _call_result(
        _rpc(
            runtime,
            "message.send",
            {
                "sessionId": session["id"],
                "taskId": task["id"],
                "mode": "supplement",
                "content": "continue with more budget",
            },
        ),
        "task",
    )
    final_task = _call_result(_rpc(runtime, "task.get", {"taskId": task["id"]}), "task")
    assert final_task["status"] == "completed"
    assert final_task["resultSummary"] == "Budget continuation completed."
    assert runtime.store.get_pending_react_state(task["id"]) is None


def test_react_loop_reuses_duplicate_read_file_results(tmp_path: Any) -> None:
    provider = ScriptedProvider(
        [
            {
                "tool_calls": [
                    {
                        "id": "call_read_1",
                        "name": "read_file",
                        "arguments": {"path": "calculator.py"},
                    }
                ]
            },
            {
                "tool_calls": [
                    {
                        "id": "call_read_2",
                        "name": "read_file",
                        "arguments": {"path": "calculator.py"},
                    }
                ]
            },
            {"final": "Done."},
        ]
    )
    read_count = 0

    def read_file(params: dict[str, Any]) -> dict[str, Any]:
        nonlocal read_count
        read_count += 1
        return {"path": params["path"], "content": "alpha", "bytesRead": 5}

    runtime = _make_runtime(tmp_path, provider, {"read_file": read_file})
    session = _open_session(runtime, tmp_path)

    task = _call_result(
        _rpc(
            runtime,
            "message.send",
            {"sessionId": session["id"], "content": "read calculator twice"},
        ),
        "task",
    )

    assert task["status"] == "completed"
    assert read_count == 1
    read_events = [
        event
        for event in runtime.events
        if event["type"] == "tool.completed" and event["payload"].get("toolName") == "read_file"
    ]
    assert len(read_events) == 1
    cached_result = provider.calls[-1]["context"]["tool_results"][-1]
    assert cached_result["result"]["cached"] is True
    assert cached_result["toolCategory"] == "context_read"
    assert cached_result["toolPhaseLabel"] == "读取上下文"
    assert cached_result["toolSemanticParentId"] == f"group:{cached_result['toolGroupId']}:phase:context_read"
    assert cached_result["toolSemanticParentLabel"] == cached_result["toolPhaseLabel"]
    assert cached_result["inputSummary"] == "read calculator.py"
    assert cached_result["resultSummary"] == "read calculator.py (5 bytes)"


def test_react_loop_publishes_live_context_budget_updates(tmp_path: Any) -> None:
    provider = ScriptedProvider(
        [
            {
                "tool_calls": [
                    {
                        "id": "call_read",
                        "name": "read_file",
                        "arguments": {"path": "calculator.py"},
                    }
                ]
            },
            {"final": "Done."},
        ]
    )

    def read_file(params: dict[str, Any]) -> dict[str, Any]:
        return {
            "path": params["path"],
            "content": "alpha " * 200,
            "bytesRead": 1200,
        }

    runtime = _make_runtime(tmp_path, provider, {"read_file": read_file})
    session = _open_session(runtime, tmp_path)

    task = _call_result(
        _rpc(
            runtime,
            "message.send",
            {"sessionId": session["id"], "content": "read calculator"},
        ),
        "task",
    )

    assert task["status"] == "completed"
    started_context = provider.calls[0]["context"]
    context_updates = [
        event["payload"]["context"]
        for event in runtime.events
        if event["type"] == "task.context.updated"
        and event["taskId"] == task["id"]
        and isinstance(event.get("payload"), dict)
        and isinstance(event["payload"].get("context"), dict)
    ]

    assert context_updates
    initial_tokens = started_context["budgetStats"]["messageTokens"]
    live_tokens = context_updates[-1]["budgetStats"]["messageTokens"]
    assert isinstance(started_context["budgetStats"].get("stablePrefixSections"), list)
    assert isinstance(started_context["budgetStats"].get("dynamicTailSections"), list)
    assert started_context["budgetStats"]["promptCache"]["stablePrefixSections"] == started_context["budgetStats"]["stablePrefixSections"]
    assert started_context["budgetStats"]["promptCache"]["dynamicTailSections"] == started_context["budgetStats"]["dynamicTailSections"]
    assert live_tokens > initial_tokens


def test_react_loop_fails_when_tool_fails(tmp_path: Any) -> None:
    provider = ScriptedProvider(
        [
            {
                "tool_calls": [
                    {
                        "id": "call_explode",
                        "name": "explode",
                        "arguments": {},
                    }
                ]
            },
            {"final": "Tool explode failed."},
        ]
    )
    runtime = _make_runtime(tmp_path, provider, {"explode": lambda _params: {"status": "failed", "summary": "boom"}})
    session = _open_session(runtime, tmp_path)

    task = _call_result(
        _rpc(
            runtime,
            "message.send",
            {"sessionId": session["id"], "content": "make a tool fail"},
        ),
        "task",
    )

    assert task["status"] in {"failed", "completed"}
    assert "Tool explode failed." in task["resultSummary"]
    assert "tool.failed" in [event["type"] for event in runtime.events]
    messages = _call_result(_rpc(runtime, "message.list", {"sessionId": session["id"]}), "messages")
    assert [message["role"] for message in messages] == ["user", "assistant"]
    assert "Tool explode failed." in messages[1]["content"]


def test_react_loop_failed_tool_result_preview_includes_recovery_hint(tmp_path: Any) -> None:
    provider = ScriptedProvider(
        [
            {
                "tool_calls": [
                    {
                        "id": "call_docs",
                        "name": "mcp__docs__lookup",
                        "arguments": {"query": "install guide"},
                    }
                ]
            },
            {"final_answer": "Recovery noted."},
        ]
    )
    runtime = _make_runtime(
        tmp_path,
        provider,
        {
            "mcp__docs__lookup": lambda _params: {
                "status": "failed",
                "error": "MCP server unavailable",
                "summary": "MCP server unavailable",
            }
        },
    )
    session = _open_session(runtime, tmp_path)

    task = _call_result(
        _rpc(
            runtime,
            "message.send",
            {"sessionId": session["id"], "content": "lookup docs"},
        ),
        "task",
    )

    assert task["status"] == "completed"
    assert not [
        event for event in runtime.events
        if event["type"] == "approval.requested"
        and event["payload"].get("kind") == "completion_review"
    ]
    failed = next(
        event["payload"]
        for event in runtime.events
        if event["type"] == "tool.failed" and event["payload"].get("toolCallId") == "call_docs"
    )
    assert isinstance(failed["durationMs"], int)
    assert failed["durationMs"] >= 0
    assert {"label": "状态", "value": "failed"} in failed["resultPreview"]
    assert {"label": "错误", "value": "MCP server unavailable"} in failed["resultPreview"]
    assert {"label": "类型", "value": "mcp_server_unavailable"} in failed["resultPreview"]
    assert any(
        row.get("label") == "修复" and "refresh tools" in row.get("value", "")
        for row in failed["resultPreview"]
    )

    tool_result = next(
        event["payload"]
        for event in runtime.events
        if event["type"] == "tool_result" and event["payload"].get("toolUseId") == "call_docs"
    )
    assert tool_result["resultPreview"] == failed["resultPreview"]
    assert tool_result["durationMs"] == failed["durationMs"]
    result_preview_delta = next(
        event["payload"]
        for event in runtime.events
        if event["type"] == "content_delta"
        and event["payload"].get("toolUseId") == "call_docs"
        and event["payload"].get("outputStream") == "result_preview"
    )
    assert "错误: MCP server unavailable" in result_preview_delta["toolOutput"]
    assert "类型: mcp_server_unavailable" in result_preview_delta["toolOutput"]

    assert tool_result["content"]["failureKind"] == "mcp_server_unavailable"
    assert "refresh tools" in tool_result["content"]["recoveryHint"]
    provider_tool_result = provider.calls[1]["context"]["tool_results"][0]
    assert provider_tool_result["durationMs"] == failed["durationMs"]


def test_react_loop_failed_tool_summary_preview_includes_recovery_hint(tmp_path: Any) -> None:
    provider = ScriptedProvider(
        [
            {
                "tool_calls": [
                    {
                        "id": "call_custom",
                        "name": "custom_lookup",
                        "arguments": {"query": "install guide"},
                    }
                ]
            },
            {"final_answer": "Recovery noted."},
        ]
    )
    runtime = _make_runtime(
        tmp_path,
        provider,
        {"custom_lookup": lambda _params: {"status": "failed", "summary": "lookup returned no usable data"}},
    )
    session = _open_session(runtime, tmp_path)

    task = _call_result(
        _rpc(
            runtime,
            "message.send",
            {"sessionId": session["id"], "content": "lookup docs"},
        ),
        "task",
    )

    assert task["status"] == "completed"
    assert not [
        event for event in runtime.events
        if event["type"] == "approval.requested"
        and event["payload"].get("kind") == "completion_review"
    ]
    failed = next(
        event["payload"]
        for event in runtime.events
        if event["type"] == "tool.failed" and event["payload"].get("toolCallId") == "call_custom"
    )
    assert {"label": "状态", "value": "failed"} in failed["resultPreview"]
    assert {"label": "类型", "value": "tool_failed"} in failed["resultPreview"]
    assert any(row.get("label") == "修复" for row in failed["resultPreview"])
    assert {"label": "摘要", "value": "lookup returned no usable data"} in failed["resultPreview"]


def test_react_loop_emits_progress_and_operation_for_custom_tools(tmp_path: Any) -> None:
    provider = ScriptedProvider(
        [
            {
                "tool_calls": [
                    {
                        "id": "call_custom",
                        "name": "custom_lookup",
                        "arguments": {"query": "install guide"},
                    }
                ]
            },
            {"final_answer": "Custom lookup done."},
        ]
    )
    runtime = _make_runtime(
        tmp_path,
        provider,
        {
            "custom_lookup": lambda params: {
                "status": "completed",
                "summary": f"lookup returned {params['query']}",
                "items": [{"title": "Install guide"}],
            },
        },
    )
    session = _open_session(runtime, tmp_path)

    task = _call_result(
        _rpc(
            runtime,
            "message.send",
            {"sessionId": session["id"], "content": "lookup docs"},
        ),
        "task",
    )

    assert task["status"] == "completed"
    progress = next(
        event["payload"]
        for event in runtime.events
        if event["type"] == "tool.progress" and event["payload"].get("toolUseId") == "call_custom"
    )
    assert progress["message"] == "正在调用工具：install guide\n"
    assert progress["toolOperationId"] == "tool:custom_lookup:install guide"
    assert progress["toolCategory"] == "tool"
    assert progress["toolSemanticParentId"] == f"group:{progress['toolGroupId']}:phase:tool"
    completed = next(
        event["payload"]
        for event in runtime.events
        if event["type"] == "tool.completed" and event["payload"].get("toolCallId") == "call_custom"
    )
    assert completed["toolOperationId"] == "tool:custom_lookup:install guide"
    provider_tool_result = provider.calls[1]["context"]["tool_results"][0]
    assert provider_tool_result["toolOperationId"] == "tool:custom_lookup:install guide"
    activity_deltas = [
        event["payload"]["toolOutput"]
        for event in runtime.events
        if event["type"] == "content_delta"
        and event["payload"].get("toolUseId") == "call_custom"
        and event["payload"].get("outputStream") == "activity"
    ]
    assert activity_deltas == ["正在调用工具：install guide\n"]


def test_react_loop_emits_url_operation_for_custom_tool_without_query(tmp_path: Any) -> None:
    provider = ScriptedProvider(
        [
            {
                "tool_calls": [
                    {
                        "id": "call_custom",
                        "name": "custom_fetch",
                        "arguments": {"url": "https://EXAMPLE.com/docs/intro/#setup"},
                    }
                ]
            },
            {"final_answer": "Custom fetch done."},
        ]
    )
    runtime = _make_runtime(
        tmp_path,
        provider,
        {
            "custom_fetch": lambda params: {
                "status": "completed",
                "summary": "fetched docs",
                "url": params["url"],
                "title": "Intro",
            },
        },
    )
    session = _open_session(runtime, tmp_path)

    task = _call_result(
        _rpc(
            runtime,
            "message.send",
            {"sessionId": session["id"], "content": "fetch docs"},
        ),
        "task",
    )

    assert task["status"] == "completed"
    started = next(
        event["payload"]
        for event in runtime.events
        if event["type"] == "tool.started" and event["payload"].get("toolCallId") == "call_custom"
    )
    assert started["target"] == "https://EXAMPLE.com/docs/intro/#setup"
    assert started["inputSummary"] == "https://EXAMPLE.com/docs/intro/#setup"
    progress = next(
        event["payload"]
        for event in runtime.events
        if event["type"] == "tool.progress" and event["payload"].get("toolUseId") == "call_custom"
    )
    assert "https://EXAMPLE.com/docs/intro/#setup" in progress["message"]
    assert progress["toolOperationId"] == "tool:custom_fetch:url:https://example.com/docs/intro"
    assert progress["toolCategory"] == "tool"
    completed = next(
        event["payload"]
        for event in runtime.events
        if event["type"] == "tool.completed" and event["payload"].get("toolCallId") == "call_custom"
    )
    assert completed["toolOperationId"] == "tool:custom_fetch:url:https://example.com/docs/intro"
    provider_tool_result = provider.calls[1]["context"]["tool_results"][0]
    assert provider_tool_result["toolOperationId"] == "tool:custom_fetch:url:https://example.com/docs/intro"


def test_react_loop_emits_path_operation_for_mcp_tool_without_query(tmp_path: Any) -> None:
    provider = ScriptedProvider(
        [
            {
                "tool_calls": [
                    {
                        "id": "call_mcp",
                        "name": "mcp__fs__lookup",
                        "arguments": {"path": "Docs/Install.md"},
                    }
                ]
            },
            {"final_answer": "MCP lookup done."},
        ]
    )
    runtime = _make_runtime(
        tmp_path,
        provider,
        {
            "mcp__fs__lookup": lambda params: {
                "status": "completed",
                "summary": "found file",
                "items": [{"path": params["path"]}],
            },
        },
    )
    session = _open_session(runtime, tmp_path)

    task = _call_result(
        _rpc(
            runtime,
            "message.send",
            {"sessionId": session["id"], "content": "lookup file"},
        ),
        "task",
    )

    assert task["status"] == "completed"
    progress = next(
        event["payload"]
        for event in runtime.events
        if event["type"] == "tool.progress" and event["payload"].get("toolUseId") == "call_mcp"
    )
    assert "Docs/Install.md" in progress["message"]
    assert progress["toolOperationId"] == "mcp:fs:lookup:path:docs/install.md"
    assert progress["toolCategory"] == "mcp"
    completed = next(
        event["payload"]
        for event in runtime.events
        if event["type"] == "tool.completed" and event["payload"].get("toolCallId") == "call_mcp"
    )
    assert completed["toolOperationId"] == "mcp:fs:lookup:path:docs/install.md"
    provider_tool_result = provider.calls[1]["context"]["tool_results"][0]
    assert provider_tool_result["toolOperationId"] == "mcp:fs:lookup:path:docs/install.md"


def test_react_loop_parents_read_file_to_prior_custom_result_path(tmp_path: Any) -> None:
    provider = ScriptedProvider(
        [
            {
                "tool_calls": [
                    {
                        "id": "call_lookup",
                        "name": "custom_lookup",
                        "arguments": {"query": "install guide"},
                    }
                ]
            },
            {
                "tool_calls": [
                    {
                        "id": "call_read",
                        "name": "read_file",
                        "arguments": {"path": "docs/install.md"},
                    }
                ]
            },
            {"final_answer": "Done."},
        ]
    )
    runtime = _make_runtime(
        tmp_path,
        provider,
        {
            "custom_lookup": lambda params: {
                "status": "completed",
                "summary": f"lookup returned {params['query']}",
                "items": [{"title": "Install guide", "path": "docs/install.md"}],
            },
            "read_file": lambda params: {"path": params["path"], "content": "install", "bytesRead": 7},
        },
    )
    session = _open_session(runtime, tmp_path)

    task = _call_result(
        _rpc(
            runtime,
            "message.send",
            {"sessionId": session["id"], "content": "lookup docs then read"},
        ),
        "task",
    )

    assert task["status"] == "completed"
    started_read = next(
        event["payload"]
        for event in runtime.events
        if event["type"] == "tool.started" and event["payload"].get("toolCallId") == "call_read"
    )
    completed_read = next(
        event["payload"]
        for event in runtime.events
        if event["type"] == "tool.completed" and event["payload"].get("toolCallId") == "call_read"
    )
    tool_result = next(
        event["payload"]
        for event in runtime.events
        if event["type"] == "tool_result" and event["payload"].get("toolUseId") == "call_read"
    )
    assert started_read["parentToolUseId"] == "call_lookup"
    assert completed_read["parentToolUseId"] == "call_lookup"
    assert tool_result["parentToolUseId"] == "call_lookup"
    assert started_read["toolOperationId"] == "tool:custom_lookup:install guide"
    assert completed_read["toolOperationId"] == "tool:custom_lookup:install guide"
    assert tool_result["toolOperationId"] == "tool:custom_lookup:install guide"
    tool_results = provider.calls[2]["context"]["tool_results"]
    assert tool_results[1]["parentToolUseId"] == "call_lookup"
    assert tool_results[1]["toolOperationId"] == "tool:custom_lookup:install guide"


def test_react_loop_parents_same_batch_read_file_to_custom_result_path(tmp_path: Any) -> None:
    provider = ScriptedProvider(
        [
            {
                "tool_calls": [
                    {
                        "id": "call_lookup",
                        "name": "custom_lookup",
                        "arguments": {"query": "install guide"},
                    },
                    {
                        "id": "call_read",
                        "name": "read_file",
                        "arguments": {"path": "docs/debug.md"},
                    },
                ]
            },
            {"final_answer": "Done."},
        ]
    )
    runtime = _make_runtime(
        tmp_path,
        provider,
        {
            "custom_lookup": lambda params: {
                "status": "completed",
                "summary": f"lookup returned {params['query']}",
                "items": [
                    {"title": "Install guide", "path": "docs/install.md"},
                    {"title": "Troubleshooting", "path": "docs/debug.md"},
                ],
            },
            "read_file": lambda params: {"path": params["path"], "content": "debug", "bytesRead": 5},
        },
    )
    session = _open_session(runtime, tmp_path)

    task = _call_result(
        _rpc(
            runtime,
            "message.send",
            {"sessionId": session["id"], "content": "lookup and read debug docs"},
        ),
        "task",
    )

    assert task["status"] == "completed"
    started = {
        event["payload"]["toolCallId"]: event["payload"]
        for event in runtime.events
        if event["type"] == "tool.started"
    }
    assert started["call_read"]["parentToolUseId"] == "call_lookup"
    assert started["call_read"]["toolOperationId"] == "tool:custom_lookup:install guide"
    tool_use_blocks = {
        event["payload"]["toolUseId"]: event["payload"]
        for event in runtime.events
        if event["type"] == "tool_use_complete"
    }
    assert tool_use_blocks["call_read"]["parentToolUseId"] == "call_lookup"
    assert tool_use_blocks["call_read"]["toolOperationId"] == "tool:custom_lookup:install guide"
    provider_tool_results = {result["id"]: result for result in provider.calls[1]["context"]["tool_results"]}
    assert provider_tool_results["call_read"]["parentToolUseId"] == "call_lookup"
    assert provider_tool_results["call_read"]["toolOperationId"] == "tool:custom_lookup:install guide"


def test_react_loop_inherits_same_batch_operation_from_custom_result_path(tmp_path: Any) -> None:
    provider = ScriptedProvider(
        [
            {
                "tool_calls": [
                    {
                        "id": "call_lookup",
                        "name": "custom_manifest",
                        "arguments": {},
                    },
                    {
                        "id": "call_read",
                        "name": "read_file",
                        "arguments": {"path": "docs/result.md"},
                    },
                ]
            },
            {"final_answer": "Done."},
        ]
    )
    runtime = _make_runtime(
        tmp_path,
        provider,
        {
            "custom_manifest": lambda _params: {
                "status": "completed",
                "summary": "manifest loaded",
                "items": [{"path": "docs/result.md"}],
            },
            "read_file": lambda params: {"path": params["path"], "content": "result", "bytesRead": 6},
        },
    )
    session = _open_session(runtime, tmp_path)

    task = _call_result(
        _rpc(
            runtime,
            "message.send",
            {"sessionId": session["id"], "content": "load manifest and read result"},
        ),
        "task",
    )

    assert task["status"] == "completed"
    completed_lookup = next(
        event["payload"]
        for event in runtime.events
        if event["type"] == "tool.completed" and event["payload"].get("toolCallId") == "call_lookup"
    )
    assert completed_lookup["toolOperationId"] == "tool:custom_manifest:path:docs/result.md"
    started = {
        event["payload"]["toolCallId"]: event["payload"]
        for event in runtime.events
        if event["type"] == "tool.started"
    }
    assert started["call_read"]["parentToolUseId"] == "call_lookup"
    assert started["call_read"]["toolOperationId"] == "tool:custom_manifest:path:docs/result.md"
    provider_tool_results = {result["id"]: result for result in provider.calls[1]["context"]["tool_results"]}
    assert provider_tool_results["call_lookup"]["toolOperationId"] == "tool:custom_manifest:path:docs/result.md"
    assert provider_tool_results["call_read"]["parentToolUseId"] == "call_lookup"
    assert provider_tool_results["call_read"]["toolOperationId"] == "tool:custom_manifest:path:docs/result.md"


def test_react_loop_parents_browser_to_prior_mcp_result_url(tmp_path: Any) -> None:
    provider = ScriptedProvider(
        [
            {
                "tool_calls": [
                    {
                        "id": "call_lookup",
                        "name": "mcp__docs__lookup",
                        "arguments": {"query": "install guide"},
                    }
                ]
            },
            {
                "tool_calls": [
                    {
                        "id": "call_browser",
                        "name": "browser",
                        "arguments": {"url": "https://EXAMPLE.com/install#intro", "action": "read"},
                    }
                ]
            },
            {"final_answer": "Done."},
        ]
    )
    runtime = _make_runtime(
        tmp_path,
        provider,
        {
            "mcp__docs__lookup": lambda params: {
                "status": "completed",
                "summary": f"Found docs for {params['query']}",
                "results": [{"title": "Install guide", "url": "https://example.com/install/"}],
            },
            "browser": lambda params: {
                "status": "ok",
                "statusCode": 200,
                "url": params["url"],
                "action": params["action"],
                "title": "Install guide",
                "content": "install docs",
            },
        },
    )
    _allow_browser_automation(runtime)
    session = _open_session(runtime, tmp_path)

    task = _call_result(
        _rpc(
            runtime,
            "message.send",
            {"sessionId": session["id"], "content": "lookup docs then open"},
        ),
        "task",
    )

    assert task["status"] == "completed"
    started_browser = next(
        event["payload"]
        for event in runtime.events
        if event["type"] == "tool.started" and event["payload"].get("toolCallId") == "call_browser"
    )
    completed_browser = next(
        event["payload"]
        for event in runtime.events
        if event["type"] == "tool.completed" and event["payload"].get("toolCallId") == "call_browser"
    )
    tool_result = next(
        event["payload"]
        for event in runtime.events
        if event["type"] == "tool_result" and event["payload"].get("toolUseId") == "call_browser"
    )
    assert started_browser["parentToolUseId"] == "call_lookup"
    assert completed_browser["parentToolUseId"] == "call_lookup"
    assert tool_result["parentToolUseId"] == "call_lookup"
    assert started_browser["toolOperationId"] == "mcp:docs:lookup:install guide"
    assert completed_browser["toolOperationId"] == "mcp:docs:lookup:install guide"
    assert tool_result["toolOperationId"] == "mcp:docs:lookup:install guide"
    tool_results = provider.calls[2]["context"]["tool_results"]
    assert tool_results[1]["parentToolUseId"] == "call_lookup"
    assert tool_results[1]["toolOperationId"] == "mcp:docs:lookup:install guide"


def test_react_loop_parents_same_batch_browser_to_any_mcp_result_url(tmp_path: Any) -> None:
    provider = ScriptedProvider(
        [
            {
                "tool_calls": [
                    {
                        "id": "call_lookup",
                        "name": "mcp__docs__lookup",
                        "arguments": {"query": "install guide"},
                    },
                    {
                        "id": "call_browser",
                        "name": "browser",
                        "arguments": {"url": "https://example.com/debug#errors", "action": "read"},
                    },
                ]
            },
            {"final_answer": "Done."},
        ]
    )
    runtime = _make_runtime(
        tmp_path,
        provider,
        {
            "mcp__docs__lookup": lambda params: {
                "status": "completed",
                "summary": f"Found docs for {params['query']}",
                "items": [
                    {"title": "Install guide", "url": "https://example.com/install"},
                    {"title": "Troubleshooting", "url": "https://example.com/debug/"},
                ],
            },
            "browser": lambda params: {
                "status": "ok",
                "statusCode": 200,
                "url": params["url"],
                "action": params["action"],
                "title": "Troubleshooting",
                "content": "debug docs",
            },
        },
    )
    _allow_browser_automation(runtime)
    session = _open_session(runtime, tmp_path)

    task = _call_result(
        _rpc(
            runtime,
            "message.send",
            {"sessionId": session["id"], "content": "lookup and open debug docs"},
        ),
        "task",
    )

    assert task["status"] == "completed"
    started = {
        event["payload"]["toolCallId"]: event["payload"]
        for event in runtime.events
        if event["type"] == "tool.started"
    }
    assert started["call_browser"]["parentToolUseId"] == "call_lookup"
    assert started["call_browser"]["toolOperationId"] == "mcp:docs:lookup:install guide"
    tool_use_blocks = {
        event["payload"]["toolUseId"]: event["payload"]
        for event in runtime.events
        if event["type"] == "tool_use_complete"
    }
    assert tool_use_blocks["call_browser"]["parentToolUseId"] == "call_lookup"
    assert tool_use_blocks["call_browser"]["toolOperationId"] == "mcp:docs:lookup:install guide"
    provider_tool_results = {result["id"]: result for result in provider.calls[1]["context"]["tool_results"]}
    assert provider_tool_results["call_browser"]["parentToolUseId"] == "call_lookup"
    assert provider_tool_results["call_browser"]["toolOperationId"] == "mcp:docs:lookup:install guide"


def test_react_loop_fails_on_invalid_provider_output(tmp_path: Any) -> None:
    provider = ScriptedProvider([{"thought": "No final answer and no tools."}])
    runtime = _make_runtime(tmp_path, provider)
    session = _open_session(runtime, tmp_path)

    task = _call_result(
        _rpc(
            runtime,
            "message.send",
            {"sessionId": session["id"], "content": "invalid provider output"},
        ),
        "task",
    )

    assert task["status"] == "failed"
    assert "Provider returned no final answer or tool calls" in task["resultSummary"]


def test_react_loop_returns_invalid_patch_to_provider_and_accepts_repair(tmp_path: Any) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    subprocess.run(["git", "init"], cwd=str(workspace_root), capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=str(workspace_root), capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=str(workspace_root), capture_output=True, check=True)
    (workspace_root / "README.md").write_text("old line\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=str(workspace_root), capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(workspace_root), capture_output=True, check=True)
    invalid_patch = _patch_text("README.md", "missing line", "new line")
    repaired_patch = _patch_text("README.md", "old line", "new line")
    provider = ScriptedProvider(
        [
            {
                "tool_calls": [
                    {
                        "id": "call_bad_patch",
                        "name": "apply_patch",
                        "arguments": {"patchText": invalid_patch},
                    }
                ]
            },
            {
                "tool_calls": [
                    {
                        "id": "call_repaired_patch",
                        "name": "apply_patch",
                        "arguments": {"patchText": repaired_patch},
                    }
                ]
            },
            {"final": "Patch repaired and applied."},
        ]
    )
    runtime = _make_builtin_runtime(tmp_path, provider)
    workspace = _call_result(_rpc(runtime, "workspace.open", {"path": str(workspace_root)}), "workspace")
    session = _call_result(
        _rpc(runtime, "session.create", {"workspaceId": workspace["id"], "title": "Patch repair"}),
        "session",
    )

    task = _call_result(
        _rpc(runtime, "message.send", {"sessionId": session["id"], "content": "fix the readme"}),
        "task",
    )

    assert task["status"] == "waiting_approval"
    assert (workspace_root / "README.md").read_text(encoding="utf-8") == "old line\n"
    assert [event for event in runtime.events if event["type"] == "approval.requested"]
    repair_context = provider.calls[1]["context"]
    failed_result = repair_context["tool_results"][0]["result"]
    assert failed_result["status"] == "validation_failed"
    assert "Patch removal mismatch in README.md" in failed_result["error"]
    assert failed_result["summary"] == "Update README.md"

    approval_id = next(event for event in runtime.events if event["type"] == "approval.requested")["payload"][
        "approvalId"
    ]
    _rpc(runtime, "approval.submit", {"approvalId": approval_id, "decision": "approved"})

    final_task = _call_result(_rpc(runtime, "task.get", {"taskId": task["id"]}), "task")
    assert final_task["status"] == "completed"
    assert final_task["resultSummary"].startswith("Patch repaired and applied.")
    assert "Changed: Update README.md." in final_task["resultSummary"]
    assert "Validated with git status" not in final_task["resultSummary"]
    assert (workspace_root / "README.md").read_text(encoding="utf-8") == "new line\n"
    patch_progress = [
        event["payload"]
        for event in runtime.events
        if event["type"] == "tool.progress" and event["payload"].get("toolName") == "apply_patch"
    ]
    assert patch_progress
    assert all(payload["toolCategory"] == "file_change" for payload in patch_progress)
    progress_messages = [str(payload.get("message") or "") for payload in patch_progress]
    assert any("README.md" in message for message in progress_messages)
    assert any("parse (completed): 1 file(s)" in message for message in progress_messages)
    assert any("validate (completed): 1 path(s)" in message for message in progress_messages)
    assert any("approval (blocked): apply_patch approval required" in message for message in progress_messages)
    assert any("approval (completed): patch approved" in message for message in progress_messages)
    assert any("apply (completed): 1 changed path(s)" in message for message in progress_messages)
    patch_activity = [
        event["payload"]
        for event in runtime.events
        if event["type"] == "content_delta"
        and event["payload"].get("toolName") == "apply_patch"
        and event["payload"].get("outputStream") == "activity"
    ]
    assert patch_activity
    assert all(delta["toolCategory"] == "file_change" for delta in patch_activity)
    activity_outputs = [str(delta.get("toolOutput") or "") for delta in patch_activity]
    for message in progress_messages:
        assert message in activity_outputs
    patch_outputs = [
        event["payload"]
        for event in runtime.events
        if event["type"] == "tool.output" and event["payload"].get("toolName") == "apply_patch"
    ]
    assert patch_outputs
    repaired_output = patch_outputs[-1]
    assert repaired_output["toolUseId"] == "call_repaired_patch"
    assert repaired_output["toolCategory"] == "file_change"
    assert repaired_output["outputStream"] == "result_preview"
    assert "状态: applied" in repaired_output["chunk"]
    assert "文件: 1 个" in repaired_output["chunk"]
    assert "样例: README.md" in repaired_output["chunk"]
    repaired_delta = next(
        event["payload"]
        for event in runtime.events
        if event["type"] == "content_delta"
        and event["payload"].get("toolUseId") == "call_repaired_patch"
        and event["payload"].get("outputStream") == "result_preview"
        and event["payload"].get("toolOutput") == repaired_output["chunk"]
    )
    assert repaired_delta["toolCategory"] == "file_change"
    repaired_preview_deltas = [
        event["payload"]
        for event in runtime.events
        if event["type"] == "content_delta"
        and event["payload"].get("toolUseId") == "call_repaired_patch"
        and event["payload"].get("outputStream") == "result_preview"
    ]
    assert len(repaired_preview_deltas) == 1


def test_react_loop_fails_when_patch_repair_attempts_are_exhausted(tmp_path: Any) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "README.md").write_text("old line\n", encoding="utf-8")
    invalid_patch = _patch_text("README.md", "missing line", "new line")
    provider = ScriptedProvider(
        [
            {
                "tool_calls": [
                    {
                        "id": "call_bad_patch_1",
                        "name": "apply_patch",
                        "arguments": {"patchText": invalid_patch},
                    }
                ]
            },
            {
                "tool_calls": [
                    {
                        "id": "call_bad_patch_2",
                        "name": "apply_patch",
                        "arguments": {"patchText": invalid_patch},
                    }
                ]
            },
        ]
    )
    runtime = _make_builtin_runtime(tmp_path, provider)
    runtime.store.update_config({"config": {"policy": {"maxPatchRepairAttempts": 1}}})
    workspace = _call_result(_rpc(runtime, "workspace.open", {"path": str(workspace_root)}), "workspace")
    session = _call_result(
        _rpc(runtime, "session.create", {"workspaceId": workspace["id"], "title": "Patch repair exhausted"}),
        "session",
    )

    task = _call_result(
        _rpc(runtime, "message.send", {"sessionId": session["id"], "content": "fix the readme"}),
        "task",
    )

    assert task["status"] == "failed"
    assert task["errorCode"] == "LOOP_EXECUTION_FAILED"
    assert "Patch repair attempts exhausted" in task["resultSummary"]
    assert "Patch removal mismatch in README.md" in task["resultSummary"]
    assert not [event for event in runtime.events if event["type"] == "approval.requested"]
    messages = _call_result(_rpc(runtime, "message.list", {"sessionId": session["id"]}), "messages")
    assert [message["role"] for message in messages] == ["user", "assistant"]
    assert "Patch repair attempts exhausted" in messages[1]["content"]
    assert "Patch removal mismatch in README.md" in messages[1]["content"]


def test_patch_completion_runs_post_task_validation_and_records_validation_trace(tmp_path: Any) -> None:
    provider = ScriptedProvider(
        [
            {
                "tool_calls": [
                    {
                        "id": "call_patch",
                        "name": "apply_patch",
                        "arguments": {
                            "files": [{"path": "todo.txt", "content": "status: new\n"}],
                        },
                    }
                ]
            },
            {"final": "Patch applied cleanly."},
        ]
    )

    tool_invocations: list[str] = []

    def apply_patch(params: dict[str, Any]) -> dict[str, Any]:
        tool_invocations.append("apply_patch")
        return {
            "status": "applied",
            "ok": True,
            "summary": "Updated todo.txt",
            "filesChanged": 1,
            "diffText": "diff --git a/todo.txt b/todo.txt\n--- a/todo.txt\n+++ b/todo.txt\n",
            "patch": {
                "id": "patch_validation",
                "summary": "Updated todo.txt",
                "status": "applied",
                "filesChanged": 1,
            },
        }

    def git_status(params: dict[str, Any]) -> dict[str, Any]:
        tool_invocations.append("git_status")
        return {
            "branch": "main",
            "ahead": 0,
            "behind": 0,
            "changes": [{"status": "M", "path": "todo.txt"}],
        }

    def git_diff(params: dict[str, Any]) -> dict[str, Any]:
        tool_invocations.append("git_diff")
        return {
            "files": [{"status": "M", "path": "todo.txt"}],
            "diff": "diff --git a/todo.txt b/todo.txt\n",
        }

    def run_command(params: dict[str, Any]) -> dict[str, Any]:
        tool_invocations.append("run_command")
        return {
            "status": "completed",
            "commandLog": {
                "id": "cmd_validation",
                "taskId": params["taskId"],
                "command": params["command"],
                "cwd": ".",
                "status": "completed",
                "exitCode": 0,
                "stdoutPath": None,
                "stderrPath": None,
                "startedAt": 1,
                "finishedAt": 2,
                "durationMs": 1,
            },
            "stdout": "3 passed\n",
            "stderr": "",
            "exitCode": 0,
            "durationMs": 1,
            "shell": "powershell",
            "cwd": ".",
        }

    runtime = _make_runtime(
        tmp_path,
        provider,
        {
            "apply_patch": apply_patch,
            "git_status": git_status,
            "git_diff": git_diff,
            "run_command": run_command,
        },
    )
    (tmp_path / ".git").mkdir()
    session = _open_session(runtime, tmp_path)
    runtime.store.update_config(
        {
            "config": {
                "policy": {
                    "postTaskValidation": {
                        "command": "pytest runtime/tests/test_orchestrator_react_loop.py -k post_task_validation"
                    }
                }
            }
        }
    )

    task = _call_result(
        _rpc(runtime, "message.send", {"sessionId": session["id"], "content": "patch todo.txt"}),
        "task",
    )

    assert task["status"] == "completed"
    assert tool_invocations == ["apply_patch", "run_command"]
    assert "Updated todo.txt" in task["resultSummary"]
    assert "Validated with pytest runtime/tests/test_orchestrator_react_loop.py -k post_task_validation." in task["resultSummary"]
    assert task["changedFiles"] == [
        {
            "path": "todo.txt",
            "status": "modified",
            "reason": "Updated todo.txt",
            "patchId": "patch_validation",
        }
    ]
    assert [command["command"] for command in task["commands"]] == [
        "pytest runtime/tests/test_orchestrator_react_loop.py -k post_task_validation"
    ]
    assert task["commands"][0]["status"] == "completed"
    assert task["verification"][-1]["command"] == "pytest runtime/tests/test_orchestrator_react_loop.py -k post_task_validation"
    assert task["verification"][-1]["status"] == "passed"
    evidence = task["structuredResult"]["completionEvidence"]
    assert evidence["evidenceLevel"] == "verified"
    assert evidence["status"] == "success"
    assert evidence["counts"]["changedFiles"] == 1
    assert evidence["counts"]["passedVerification"] >= 1
    assert evidence["acceptance"][0]["status"] == "supported"

    trace = _rpc(runtime, "trace.list", {"taskId": task["id"]})["result"]["traceEvents"]
    trace_types = [event["type"] for event in trace]
    assert "task.validation.completed" in trace_types
    validation_event = next(event for event in trace if event["type"] == "task.validation.completed")
    assert validation_event["payload"]["ran"] == ["run_command"]
    assert validation_event["payload"]["command"]["command"] == "pytest runtime/tests/test_orchestrator_react_loop.py -k post_task_validation"
    assert validation_event["payload"]["patches"][0]["summary"] == "Updated todo.txt"
    assert validation_event["payload"]["verification"][-1]["status"] == "passed"
    assert "agent.decision.completion" not in trace_types
    task_updates = [event for event in runtime.events if event["type"] == "task.updated"]
    assert any(event["payload"].get("changedFiles") for event in task_updates)
    assert any(event["payload"].get("commands") for event in task_updates)
    assert any(event["payload"].get("verification") for event in task_updates)


def test_patch_completion_does_not_auto_git_snapshot_without_validation_policy(tmp_path: Any) -> None:
    provider = ScriptedProvider(
        [
            {
                "tool_calls": [
                    {
                        "id": "call_patch",
                        "name": "apply_patch",
                        "arguments": {
                            "files": [{"path": "todo.txt", "content": "status: newer\n"}],
                        },
                    }
                ]
            },
            {"final": "Patch applied without command validation."},
        ]
    )

    tool_invocations: list[str] = []

    def apply_patch(params: dict[str, Any]) -> dict[str, Any]:
        tool_invocations.append("apply_patch")
        return {
            "status": "completed",
            "ok": True,
            "summary": "Updated todo.txt again",
            "filesChanged": 1,
            "changedPaths": ["todo.txt"],
            "patch": {
                "id": "patch_validation_skip",
                "summary": "Updated todo.txt again",
                "status": "applied",
                "filesChanged": 1,
            },
        }

    def git_status(params: dict[str, Any]) -> dict[str, Any]:
        tool_invocations.append("git_status")
        return {
            "branch": "main",
            "ahead": 0,
            "behind": 0,
            "changes": [{"status": "M", "path": "todo.txt"}],
        }

    def git_diff(params: dict[str, Any]) -> dict[str, Any]:
        tool_invocations.append("git_diff")
        return {
            "files": [{"status": "M", "path": "todo.txt"}],
            "diff": "diff --git a/todo.txt b/todo.txt\n",
        }

    def run_command(params: dict[str, Any]) -> dict[str, Any]:
        tool_invocations.append("run_command")
        return {"status": "completed", "stdout": "", "stderr": "", "exitCode": 0}

    runtime = _make_runtime(
        tmp_path,
        provider,
        {
            "apply_patch": apply_patch,
            "git_status": git_status,
            "git_diff": git_diff,
            "run_command": run_command,
        },
    )
    (tmp_path / ".git").mkdir()
    session = _open_session(runtime, tmp_path)

    task = _call_result(
        _rpc(runtime, "message.send", {"sessionId": session["id"], "content": "patch todo.txt again"}),
        "task",
    )

    assert task["status"] == "completed"
    assert tool_invocations == ["apply_patch"]
    assert "Changed: Updated todo.txt again." in task["resultSummary"]
    assert "Validated with git status" not in task["resultSummary"]

    trace = _rpc(runtime, "trace.list", {"taskId": task["id"]})["result"]["traceEvents"]
    assert not [event for event in trace if event["type"] == "task.validation.completed"]


# ── Supplement TaskInbox tests ────────────────────────────────────────────


def test_patch_completion_runs_git_snapshot_when_policy_enables_it(tmp_path: Any) -> None:
    provider = ScriptedProvider(
        [
            {
                "tool_calls": [
                    {
                        "id": "call_patch",
                        "name": "apply_patch",
                        "arguments": {
                            "files": [{"path": "todo.txt", "content": "status: newer\n"}],
                        },
                    }
                ]
            },
            {"final": "Patch applied with requested snapshot."},
        ]
    )

    tool_invocations: list[str] = []

    def apply_patch(params: dict[str, Any]) -> dict[str, Any]:
        tool_invocations.append("apply_patch")
        return {
            "status": "completed",
            "ok": True,
            "summary": "Updated todo.txt with snapshot",
            "filesChanged": 1,
            "changedPaths": ["todo.txt"],
            "patch": {
                "id": "patch_validation_snapshot",
                "summary": "Updated todo.txt with snapshot",
                "status": "applied",
                "filesChanged": 1,
            },
        }

    def git_status(params: dict[str, Any]) -> dict[str, Any]:
        tool_invocations.append("git_status")
        return {
            "branch": "main",
            "ahead": 0,
            "behind": 0,
            "changes": [{"status": "M", "path": "todo.txt"}],
        }

    def git_diff(params: dict[str, Any]) -> dict[str, Any]:
        tool_invocations.append("git_diff")
        return {
            "files": [{"status": "M", "path": "todo.txt"}],
            "diff": "diff --git a/todo.txt b/todo.txt\n",
        }

    runtime = _make_runtime(
        tmp_path,
        provider,
        {
            "apply_patch": apply_patch,
            "git_status": git_status,
            "git_diff": git_diff,
        },
    )
    (tmp_path / ".git").mkdir()
    session = _open_session(runtime, tmp_path)
    runtime.store.update_config({"config": {"policy": {"postTaskValidation": {"gitSnapshot": True}}}})

    task = _call_result(
        _rpc(runtime, "message.send", {"sessionId": session["id"], "content": "patch todo.txt with snapshot"}),
        "task",
    )

    assert task["status"] == "completed"
    assert tool_invocations == ["apply_patch", "git_status", "git_diff"]
    assert "Validated with git status, and git diff." in task["resultSummary"]

    trace = _rpc(runtime, "trace.list", {"taskId": task["id"]})["result"]["traceEvents"]
    validation_event = next(event for event in trace if event["type"] == "task.validation.completed")
    assert validation_event["payload"]["ran"] == ["git_status", "git_diff"]
    assert validation_event["payload"]["command"]["status"] == "skipped"
    assert validation_event["payload"]["command"]["reason"] == "No validation command was configured."


def test_patch_completion_uses_python_module_pytest_for_changed_tests(tmp_path: Any) -> None:
    provider = ScriptedProvider(
        [
            {
                "tool_calls": [
                    {
                        "id": "call_patch",
                        "name": "apply_patch",
                        "arguments": {
                            "files": [{"path": "tests/test_prototype.py", "content": "def test_ok():\n    assert True\n"}],
                        },
                    }
                ]
            },
            {"final": "Added prototype coverage."},
        ]
    )

    commands: list[str] = []

    def apply_patch(params: dict[str, Any]) -> dict[str, Any]:
        return {
            "status": "completed",
            "ok": True,
            "summary": "Updated tests/test_prototype.py",
            "filesChanged": 1,
            "changedPaths": ["tests/test_prototype.py"],
            "patch": {
                "id": "patch_pytest_module",
                "summary": "Updated tests/test_prototype.py",
                "status": "applied",
                "filesChanged": 1,
            },
        }

    def run_command(params: dict[str, Any]) -> dict[str, Any]:
        commands.append(params["command"])
        return {
            "status": "completed",
            "commandLog": {
                "id": "cmd_pytest_module",
                "taskId": params["taskId"],
                "command": params["command"],
                "cwd": ".",
                "status": "completed",
                "exitCode": 0,
                "stdoutPath": None,
                "stderrPath": None,
            },
            "stdout": "1 passed\n",
            "stderr": "",
            "exitCode": 0,
            "cwd": ".",
        }

    runtime = _make_runtime(
        tmp_path,
        provider,
        {"apply_patch": apply_patch, "run_command": run_command},
    )
    session = _open_session(runtime, tmp_path)

    task = _call_result(
        _rpc(runtime, "message.send", {"sessionId": session["id"], "content": "add prototype test"}),
        "task",
    )

    assert task["status"] == "completed"
    assert commands == ["python -m pytest tests/test_prototype.py"]
    assert task["verification"][-1]["command"] == "python -m pytest tests/test_prototype.py"


def test_patch_completion_reuses_existing_passed_pytest_command(tmp_path: Any) -> None:
    provider = ScriptedProvider(
        [
            {
                "tool_calls": [
                    {
                        "id": "call_patch",
                        "name": "apply_patch",
                        "arguments": {
                            "files": [{"path": "tests/test_prototype.py", "content": "def test_ok():\n    assert True\n"}],
                        },
                    }
                ]
            },
            {"final": "Updated prototype and tests."},
        ]
    )

    runtime = _make_runtime(tmp_path, provider, {})
    commands: list[str] = []

    def apply_patch(params: dict[str, Any]) -> dict[str, Any]:
        existing = runtime.store.create_command_log(
            task_id=params["taskId"],
            command=r"C:\Python314\python.exe -m pytest -q",
            cwd=str(tmp_path / "workspace"),
            shell="powershell",
        )
        runtime.store.update_command_log(existing["id"], status="completed", exit_code=0)
        return {
            "status": "completed",
            "ok": True,
            "summary": "Updated tests/test_prototype.py",
            "filesChanged": 1,
            "changedPaths": ["tests/test_prototype.py"],
            "patch": {
                "id": "patch_reuse_pytest",
                "summary": "Updated tests/test_prototype.py",
                "status": "applied",
                "filesChanged": 1,
            },
        }

    def run_command(params: dict[str, Any]) -> dict[str, Any]:
        commands.append(params["command"])
        return {
            "status": "completed",
            "commandLog": {
                "id": "cmd_reused_pytest",
                "taskId": params["taskId"],
                "command": params["command"],
                "cwd": ".",
                "status": "completed",
                "exitCode": 0,
                "stdoutPath": None,
                "stderrPath": None,
            },
            "stdout": "4 passed\n",
            "stderr": "",
            "exitCode": 0,
            "cwd": ".",
        }

    runtime.server._orchestrator._tool_registry.register("apply_patch", apply_patch)  # noqa: SLF001
    runtime.server._orchestrator._tool_registry.register("run_command", run_command)  # noqa: SLF001
    session = _open_session(runtime, tmp_path)

    task = _call_result(
        _rpc(runtime, "message.send", {"sessionId": session["id"], "content": "update prototype test"}),
        "task",
    )

    assert task["status"] == "completed"
    assert commands == [r"C:\Python314\python.exe -m pytest -q"]
    assert task["verification"][-1]["command"] == r"C:\Python314\python.exe -m pytest -q"


class TestStoreInbox:
    """Tests for task_inbox store CRUD operations."""

    def test_create_and_get_pending(self, tmp_path: Any) -> None:
        store = SQLiteStore(str(tmp_path / "test.sqlite3"))
        entry = store.create_inbox_entry(
            task_id="task_1",
            session_id="sess_1",
            content="Use Python 3.12",
            message_id="msg_1",
        )
        assert entry["id"].startswith("ibx_")
        assert entry["task_id"] == "task_1"
        assert entry["status"] == "pending"
        assert entry["content"] == "Use Python 3.12"
        assert entry["metadata_json"] == "{}"

        pending = store.get_pending_supplements("task_1")
        assert len(pending) == 1
        assert pending[0]["id"] == entry["id"]
        store.close()

    def test_create_and_get_pending_with_metadata(self, tmp_path: Any) -> None:
        store = SQLiteStore(str(tmp_path / "test.sqlite3"))
        entry = store.create_inbox_entry(
            task_id="task_1",
            session_id="sess_1",
            content="Inspect @src/app.ts",
            metadata={"fileReferences": ["src/app.ts"], "attachments": ["src/app.ts"]},
        )

        pending = store.get_pending_supplements("task_1")
        assert pending[0]["id"] == entry["id"]
        assert '"fileReferences": ["src/app.ts"]' in pending[0]["metadata_json"]
        store.close()

    def test_mark_consumed(self, tmp_path: Any) -> None:
        store = SQLiteStore(str(tmp_path / "test.sqlite3"))
        entry = store.create_inbox_entry(
            task_id="task_1",
            session_id="sess_1",
            content="Important hint",
        )
        store.mark_supplement_consumed(entry["id"], consumed_by_turn_id="step_2")

        pending = store.get_pending_supplements("task_1")
        assert len(pending) == 0
        store.close()

    def test_multiple_entries_ordered_by_time(self, tmp_path: Any) -> None:
        store = SQLiteStore(str(tmp_path / "test.sqlite3"))
        e1 = store.create_inbox_entry(task_id="task_1", session_id="sess_1", content="First")
        e2 = store.create_inbox_entry(task_id="task_1", session_id="sess_1", content="Second")
        pending = store.get_pending_supplements("task_1")
        assert [e["id"] for e in pending] == [e1["id"], e2["id"]]
        store.close()

    def test_different_tasks_isolated(self, tmp_path: Any) -> None:
        store = SQLiteStore(str(tmp_path / "test.sqlite3"))
        store.create_inbox_entry(task_id="task_1", session_id="sess_1", content="For task 1")
        store.create_inbox_entry(task_id="task_2", session_id="sess_1", content="For task 2")
        assert len(store.get_pending_supplements("task_1")) == 1
        assert len(store.get_pending_supplements("task_2")) == 1
        store.close()

    def test_created_seq_allocated(self, tmp_path: Any) -> None:
        store = SQLiteStore(str(tmp_path / "test.sqlite3"))
        e1 = store.create_inbox_entry(task_id="task_1", session_id="sess_1", content="First")
        e2 = store.create_inbox_entry(task_id="task_1", session_id="sess_1", content="Second")
        assert e1["created_seq"] is not None
        assert e2["created_seq"] is not None
        assert e2["created_seq"] > e1["created_seq"]
        store.close()

    def test_list_task_inbox_items(self, tmp_path: Any) -> None:
        store = SQLiteStore(str(tmp_path / "test.sqlite3"))
        store.create_inbox_entry(task_id="task_1", session_id="sess_1", content="A")
        e2 = store.create_inbox_entry(task_id="task_1", session_id="sess_1", content="B")
        store.mark_supplement_consumed(e2["id"], consumed_by_turn_id="step_1")
        store.create_inbox_entry(task_id="task_1", session_id="sess_1", content="C")

        items = store.list_task_inbox_items("task_1")
        assert len(items) == 3
        assert [i["content"] for i in items] == ["A", "B", "C"]
        assert items[1]["status"] == "consumed"
        store.close()

    def test_list_task_inbox_items_empty(self, tmp_path: Any) -> None:
        store = SQLiteStore(str(tmp_path / "test.sqlite3"))
        assert store.list_task_inbox_items("nonexistent") == []
        store.close()


def test_supplement_creates_inbox_entry_and_events(tmp_path: Any) -> None:
    """Verify _attach_supplemental_message writes to task_inbox and emits events."""
    provider = ScriptedProvider([
        {"message": "Working...", "tool_calls": [{"id": "call_slow", "name": "slow_tool", "arguments": {}}]},
        {"final": "Done after supplement."},
    ])

    inbox_entries_created: list[dict[str, Any]] = []

    def slow_tool(params: dict[str, Any]) -> dict[str, Any]:
        # While tool is executing, simulate a supplement arriving via send_message
        # by directly calling the store's inbox method (same as _attach_supplemental_message)
        runtime_ref = params.get("__runtime")
        if runtime_ref is not None:
            entry = runtime_ref.store.create_inbox_entry(
                task_id=params["taskId"],
                session_id=params["sessionId"],
                content="Remember to use type hints",
            )
            inbox_entries_created.append(entry)
        return {"result": "ok"}

    runtime = _make_runtime(tmp_path, provider, {"slow_tool": slow_tool})

    # Inject runtime reference so tool can access store
    original_generate = provider.generate

    def patched_generate(prompt: str, context: dict[str, Any]) -> dict[str, Any]:
        return original_generate(prompt, context)

    # We need a different approach: use the tool's params dict to pass runtime
    # Instead, use the tool's closure to capture the store directly
    store_ref = runtime.store

    def slow_tool_with_store(params: dict[str, Any]) -> dict[str, Any]:
        entry = store_ref.create_inbox_entry(
            task_id=params["taskId"],
            session_id=params["sessionId"],
            content="Remember to use type hints",
        )
        inbox_entries_created.append(entry)
        return {"result": "ok"}

    runtime = _make_runtime(tmp_path, provider, {"slow_tool": slow_tool_with_store})
    session = _open_session(runtime, tmp_path)

    task = _call_result(
        _rpc(runtime, "message.send", {"sessionId": session["id"], "content": "do work"}),
        "task",
    )

    assert task["status"] == "completed"

    # Verify inbox entry was created during tool execution
    assert len(inbox_entries_created) == 1

    # Verify the supplement was consumed (no pending left)
    pending = runtime.store.get_pending_supplements(task["id"])
    assert len(pending) == 0

    # Verify the second provider call contains the supplement in messages
    assert len(provider.calls) == 2
    second_messages = provider.calls[1]["context"]["messages"]
    supplement_msgs = [m for m in second_messages if m.get("role") == "user" and "[User supplement]" in m.get("content", "")]
    assert len(supplement_msgs) == 1
    assert "Remember to use type hints" in supplement_msgs[0]["content"]

    # Verify consumed event was emitted
    consumed_events = [e for e in runtime.events if e["type"] == "task.supplement.consumed"]
    assert len(consumed_events) == 1
    assert consumed_events[0]["payload"]["count"] == 1


def test_attach_supplemental_message_writes_inbox(tmp_path: Any) -> None:
    """Verify _attach_supplemental_message writes to task_inbox and publishes received event."""
    provider = ScriptedProvider([{"final": "done"}])
    runtime = _make_runtime(tmp_path, provider)
    session = _open_session(runtime, tmp_path)

    # Create a completed task — its inbox should be empty
    task = _call_result(
        _rpc(runtime, "message.send", {"sessionId": session["id"], "content": "start task"}),
        "task",
    )
    assert task["status"] == "completed"
    inbox = runtime.store.get_pending_supplements(task["id"])
    assert len(inbox) == 0


def test_attach_supplemental_message_emits_received_event(tmp_path: Any) -> None:
    """Verify _attach_supplemental_message emits task.supplement.received via RPC."""
    # Create a task in running state directly in the store
    store = SQLiteStore(str(tmp_path / "test.sqlite3"))
    event_bus = EventBus()
    events: list[dict[str, Any]] = []
    event_bus.subscribe(lambda event: events.append(event_bus.as_payload(event)))
    provider = ScriptedProvider([])
    orchestrator = Orchestrator(
        store=store,
        event_bus=event_bus,
        tool_registry=ToolRegistry({}),
        provider=provider,
    )

    (tmp_path / "ws").mkdir(exist_ok=True)
    workspace = store.upsert_workspace(str(tmp_path / "ws"))
    session = store.create_session(workspace_id=workspace["id"], title="test")
    task = store.create_task(
        session_id=session["id"],
        task_type="code",
        goal="test goal",
        plan=[],
    )

    # Directly call _attach_supplemental_message
    orchestrator._attach_supplemental_message(
        session_id=session["id"],
        task=task,
        content="Use type hints everywhere",
    )

    # Verify inbox entry was created
    pending = store.get_pending_supplements(task["id"])
    assert len(pending) == 1
    assert pending[0]["content"] == "Use type hints everywhere"
    assert pending[0]["message_id"] is not None

    # Verify received event was emitted
    received_events = [e for e in events if e["type"] == "task.supplement.received"]
    assert len(received_events) == 1
    assert received_events[0]["payload"]["content"] == "Use type hints everywhere"
    assert received_events[0]["payload"]["inboxEntryId"] == pending[0]["id"]

    store.close()


def test_running_supplement_includes_referenced_file_content(tmp_path: Any) -> None:
    provider = ScriptedProvider([
        {
            "message": "Running a command.",
            "tool_calls": [
                {"id": "call_1", "name": "run_command", "arguments": {"command": "echo wait"}},
            ],
        },
        {"final_answer": "done"},
    ])
    runtime = _make_runtime(tmp_path, provider)
    session = _open_session(runtime, tmp_path)
    workspace_root = Path(session["workspaceRoot"])
    source_dir = workspace_root / "src"
    source_dir.mkdir(exist_ok=True)
    (source_dir / "hint.ts").write_text("export const supplementHint = true;\n", encoding="utf-8")
    inbox_entries_created: list[dict[str, Any]] = []

    original_execute_tool = runtime.orchestrator._execute_tool

    def execute_and_inject(*args: Any, **kwargs: Any) -> Any:
        task_arg = kwargs.get("task") or (args[1] if len(args) > 1 else {})
        task_id = task_arg.get("id") if isinstance(task_arg, dict) else ""
        entry = runtime.store.create_inbox_entry(
            task_id=task_id,
            session_id=session["id"],
            content="Use @src/hint.ts",
            metadata={"fileReferences": ["src/hint.ts"], "attachments": ["src/hint.ts"]},
        )
        inbox_entries_created.append(entry)
        return original_execute_tool(*args, **kwargs)

    runtime.orchestrator._execute_tool = execute_and_inject  # type: ignore[method-assign]

    task = _call_result(
        _rpc(runtime, "message.send", {"sessionId": session["id"], "content": "do work"}),
        "task",
    )

    assert task["status"] == "completed"
    assert len(inbox_entries_created) == 1
    assert len(provider.calls) == 2
    second_messages = provider.calls[1]["context"]["messages"]
    supplement_msgs = [m for m in second_messages if m.get("role") == "user" and "[User supplement]" in m.get("content", "")]
    assert len(supplement_msgs) == 1
    assert "Use @src/hint.ts" in supplement_msgs[0]["content"]
    assert "Referenced file content: src/hint.ts" in supplement_msgs[0]["content"]
    assert "supplementHint" in supplement_msgs[0]["content"]


# ── Edge case: supplement to tasks in various states ────────────────────────


def test_supplement_to_completed_task_creates_new_task(tmp_path: Any) -> None:
    """When the only task in session is completed, send_message creates a new task (not supplement)."""
    provider = ScriptedProvider([{"final": "New task response."}])
    runtime = _make_runtime(tmp_path, provider)
    session = _open_session(runtime, tmp_path)

    completed_task = runtime.store.create_task(
        session_id=session["id"],
        task_type="edit",
        goal="Already done",
        plan=[],
        status="completed",
    )

    resp = _rpc(
        runtime,
        "message.send",
        {"sessionId": session["id"], "content": "follow-up question"},
    )
    new_task = _call_result(resp, "task")

    # Should NOT be the completed task — a brand-new task was created
    assert new_task["id"] != completed_task["id"]
    assert new_task["status"] == "completed"
    assert new_task["resultSummary"] == "New task response."


def test_explicit_supplement_to_completed_task_creates_new_task(tmp_path: Any) -> None:
    """Explicit mode=supplement to a terminal task starts a fresh task instead of failing the chat."""
    provider = ScriptedProvider([{"final": "Fresh follow-up response."}])
    runtime = _make_runtime(tmp_path, provider)
    session = _open_session(runtime, tmp_path)

    completed_task = runtime.store.create_task(
        session_id=session["id"],
        task_type="edit",
        goal="Already done",
        plan=[],
        status="completed",
    )

    resp = _rpc(
        runtime,
        "message.send",
        {
            "sessionId": session["id"],
            "content": "additional info",
            "taskId": completed_task["id"],
            "mode": "supplement",
        },
    )
    result = resp["result"]
    assert result["task"]["id"] != completed_task["id"]
    assert result["task"]["status"] in {"completed", "waiting_approval"}
    assert result["acceptedMode"] == "new"


def test_supplement_to_waiting_approval_task(tmp_path: Any) -> None:
    """A task in waiting_approval status can receive supplements."""
    provider = ScriptedProvider([])
    runtime = _make_runtime(tmp_path, provider)
    session = _open_session(runtime, tmp_path)

    waiting_task = runtime.store.create_task(
        session_id=session["id"],
        task_type="edit",
        goal="Waiting for approval",
        plan=[{"id": "s1", "title": "Step 1", "status": "active"}],
        current_step="Step 1",
        status="waiting_approval",
    )

    resp = _rpc(
        runtime,
        "message.send",
        {"sessionId": session["id"], "content": "please also add tests"},
    )
    result = resp["result"]
    assert result["acceptedMode"] == "supplement"
    assert result["task"]["id"] == waiting_task["id"]
    assert not provider.calls


def test_supplement_to_queued_task(tmp_path: Any) -> None:
    """A task in queued status can receive supplements."""
    provider = ScriptedProvider([])
    runtime = _make_runtime(tmp_path, provider)
    session = _open_session(runtime, tmp_path)

    queued_task = runtime.store.create_task(
        session_id=session["id"],
        task_type="edit",
        goal="Queued for later",
        plan=[],
        status="queued",
    )

    resp = _rpc(
        runtime,
        "message.send",
        {"sessionId": session["id"], "content": "extra context for when this runs"},
    )
    result = resp["result"]
    assert result["acceptedMode"] == "supplement"
    assert result["task"]["id"] == queued_task["id"]
    assert not provider.calls


def test_supplement_to_paused_task(tmp_path: Any) -> None:
    """A paused task can receive supplements."""
    provider = ScriptedProvider([])
    runtime = _make_runtime(tmp_path, provider)
    session = _open_session(runtime, tmp_path)

    paused_task = runtime.store.create_task(
        session_id=session["id"],
        task_type="edit",
        goal="Paused task",
        plan=[{"id": "s1", "title": "Step 1", "status": "active"}],
        current_step="Step 1",
        status="paused",
    )

    resp = _rpc(
        runtime,
        "message.send",
        {"sessionId": session["id"], "content": "resume hint"},
    )
    result = resp["result"]
    assert result["acceptedMode"] == "supplement"
    assert result["task"]["id"] == paused_task["id"]


def test_explicit_supplement_to_wrong_session_rejected(tmp_path: Any) -> None:
    """Explicit supplement with taskId belonging to a different session is rejected."""
    provider = ScriptedProvider([])
    runtime = _make_runtime(tmp_path, provider)
    session = _open_session(runtime, tmp_path)

    # Create a second workspace + session
    workspace_root2 = tmp_path / "workspace2"
    workspace_root2.mkdir()
    ws2 = _call_result(
        _rpc(runtime, "workspace.open", {"path": str(workspace_root2)}),
        "workspace",
    )
    session2 = _call_result(
        _rpc(runtime, "session.create", {"workspaceId": ws2["id"], "title": "Other"}),
        "session",
    )
    other_task = runtime.store.create_task(
        session_id=session2["id"],
        task_type="edit",
        goal="Other session task",
        plan=[],
        status="running",
    )

    resp = _rpc(
        runtime,
        "message.send",
        {
            "sessionId": session["id"],
            "content": "should fail",
            "taskId": other_task["id"],
            "mode": "supplement",
        },
    )
    assert "error" in resp


# ── Multi-session isolation ─────────────────────────────────────────────────


def test_multi_session_context_isolation(tmp_path: Any) -> None:
    """Session A's messages do not leak into session B's provider context."""
    calls_a: list[dict[str, Any]] = []
    calls_b: list[dict[str, Any]] = []

    class RecordingProvider:
        def __init__(self, bucket: list[dict[str, Any]]) -> None:
            self._bucket = bucket

        def generate(self, prompt: str, context: dict[str, Any]) -> dict[str, Any]:
            self._bucket.append({"prompt": prompt, "context": context})
            return {"final": "done"}

    runtime = _make_runtime(tmp_path, None)

    # Open two workspaces + sessions
    ws1_root = tmp_path / "workspace_a"
    ws1_root.mkdir()
    ws2_root = tmp_path / "workspace_b"
    ws2_root.mkdir()
    ws1 = _call_result(_rpc(runtime, "workspace.open", {"path": str(ws1_root)}), "workspace")
    ws2 = _call_result(_rpc(runtime, "workspace.open", {"path": str(ws2_root)}), "workspace")
    session_a = _call_result(
        _rpc(runtime, "session.create", {"workspaceId": ws1["id"], "title": "Session A"}),
        "session",
    )
    session_b = _call_result(
        _rpc(runtime, "session.create", {"workspaceId": ws2["id"], "title": "Session B"}),
        "session",
    )

    # Send a message in session A with a recording provider
    runtime.server._orchestrator._provider = RecordingProvider(calls_a)
    task_a = _call_result(
        _rpc(runtime, "message.send", {"sessionId": session_a["id"], "content": "remember project alpha"}),
        "task",
    )
    assert task_a["status"] == "completed"
    assert len(calls_a) == 1

    # Send a message in session B
    runtime.server._orchestrator._provider = RecordingProvider(calls_b)
    task_b = _call_result(
        _rpc(runtime, "message.send", {"sessionId": session_b["id"], "content": "remember project beta"}),
        "task",
    )
    assert task_b["status"] == "completed"
    assert len(calls_b) == 1

    # Verify B's provider context does NOT contain A's content
    b_messages = calls_b[0]["context"].get("messages") or []
    b_text = " ".join(m.get("content", "") for m in b_messages if isinstance(m.get("content"), str))
    assert "project alpha" not in b_text
    assert "project beta" in b_text

    # Verify A's provider context does NOT contain B's content
    a_messages = calls_a[0]["context"].get("messages") or []
    a_text = " ".join(m.get("content", "") for m in a_messages if isinstance(m.get("content"), str))
    assert "project alpha" in a_text
    assert "project beta" not in a_text


def test_multi_session_tasks_dont_cross(tmp_path: Any) -> None:
    """Tasks from different sessions are fully independent."""
    provider = ScriptedProvider([])
    runtime = _make_runtime(tmp_path, provider)
    ws1_root = tmp_path / "workspace_x"
    ws1_root.mkdir()
    ws2_root = tmp_path / "workspace_y"
    ws2_root.mkdir()
    ws1 = _call_result(_rpc(runtime, "workspace.open", {"path": str(ws1_root)}), "workspace")
    ws2 = _call_result(_rpc(runtime, "workspace.open", {"path": str(ws2_root)}), "workspace")
    session_a = _call_result(
        _rpc(runtime, "session.create", {"workspaceId": ws1["id"], "title": "A"}),
        "session",
    )
    session_b = _call_result(
        _rpc(runtime, "session.create", {"workspaceId": ws2["id"], "title": "B"}),
        "session",
    )

    # Create a running task in session A
    task_a = runtime.store.create_task(
        session_id=session_a["id"],
        task_type="edit",
        goal="Task A",
        plan=[],
        status="running",
    )
    # Session B should not see session A's task via supplement
    resp = _rpc(
        runtime,
        "message.send",
        {"sessionId": session_b["id"], "content": "new request"},
    )
    # Should create a new task for B, not supplement A
    assert "result" in resp
    task_b = resp["result"]["task"]
    assert task_b["id"] != task_a["id"]
    assert task_b["sessionId"] == session_b["id"]


# ── Reload / persistence recovery ───────────────────────────────────────────


def test_reload_preserves_completed_task_and_messages(tmp_path: Any) -> None:
    """After closing and reopening the store, completed task and messages are recoverable."""
    db_path = tmp_path / "runtime.sqlite3"
    provider = ScriptedProvider([{"final": "Persisted answer."}])

    # First runtime instance
    runtime1 = _make_runtime_at_path(db_path, provider)
    session = _open_session(runtime1, tmp_path)
    task = _call_result(
        _rpc(runtime1, "message.send", {"sessionId": session["id"], "content": "persist this"}),
        "task",
    )
    assert task["status"] == "completed"
    task_id = task["id"]
    session_id = session["id"]
    runtime1.store.close()

    # Second runtime instance — same database
    runtime2 = _make_runtime_at_path(db_path, provider)
    recovered_task = runtime2.store.get_task({"taskId": task_id})["task"]
    assert recovered_task["id"] == task_id
    assert recovered_task["status"] == "completed"
    assert recovered_task["resultSummary"] == "Persisted answer."

    msgs = runtime2.store.list_messages({"sessionId": session_id})["messages"]
    assert len(msgs) >= 2  # user + assistant
    assert any(m["role"] == "user" and "persist this" in m.get("content", "") for m in msgs)
    assert any(m["role"] == "assistant" and "Persisted answer" in m.get("content", "") for m in msgs)
    runtime2.store.close()


def test_reload_preserves_failed_task_and_error(tmp_path: Any) -> None:
    """Failed task and error message survive store reload."""

    class FailProvider:
        def generate(self, prompt: str, context: dict[str, Any]) -> dict[str, Any]:
            raise RuntimeError("provider exploded")

    db_path = tmp_path / "runtime.sqlite3"
    runtime1 = _make_runtime_at_path(db_path, FailProvider())
    session = _open_session(runtime1, tmp_path)
    resp = _rpc(runtime1, "message.send", {"sessionId": session["id"], "content": "trigger failure"})
    task = resp["result"]["task"]
    assert task["status"] == "failed"
    task_id = task["id"]
    session_id = session["id"]
    runtime1.store.close()

    # Reopen
    runtime2 = _make_runtime_at_path(db_path, FailProvider())
    recovered = runtime2.store.get_task({"taskId": task_id})["task"]
    assert recovered["status"] == "failed"

    msgs = runtime2.store.list_messages({"sessionId": session_id})["messages"]
    # Should have the user message and the error assistant message
    assert any(m["role"] == "user" for m in msgs)
    # P8.2: Verify failure assistant message persists after reload
    failure_msgs = [m for m in msgs if m["role"] == "assistant" and m.get("status") == "failed"]
    assert len(failure_msgs) >= 1, "Expected at least one failed assistant message after reload"
    # Failure content should mention the error
    failure_content = failure_msgs[0].get("content", "")
    assert "provider exploded" in failure_content or "error" in failure_content.lower(), \
        f"Failure message should contain error info, got: {failure_content[:200]}"
    runtime2.store.close()


def test_reload_preserves_queued_task(tmp_path: Any) -> None:
    """Queued tasks survive store reload."""
    db_path = tmp_path / "runtime.sqlite3"
    provider = ScriptedProvider([])
    runtime1 = _make_runtime_at_path(db_path, provider)
    session = _open_session(runtime1, tmp_path)

    # Create a running task so queued mode is needed
    running_task = runtime1.store.create_task(
        session_id=session["id"],
        task_type="edit",
        goal="Running",
        plan=[],
        status="running",
    )
    queued_resp = _rpc(
        runtime1,
        "message.send",
        {"sessionId": session["id"], "content": "queued task", "mode": "queued"},
    )
    queued_task = queued_resp["result"]["task"]
    assert queued_task["status"] == "queued"
    queued_id = queued_task["id"]
    session_id = session["id"]
    runtime1.store.close()

    # Reopen
    runtime2 = _make_runtime_at_path(db_path, provider)
    recovered = runtime2.store.get_task({"taskId": queued_id})["task"]
    assert recovered["status"] == "queued"
    assert recovered["goal"] == "queued task"

    # The running (orphan) task is marked as failed by _cleanup_orphan_tasks
    recovered_running = runtime2.store.get_task({"taskId": running_task["id"]})["task"]
    assert recovered_running["status"] == "failed"
    assert recovered_running.get("errorCode") == "ORPHAN_CLEANUP"
    runtime2.store.close()
