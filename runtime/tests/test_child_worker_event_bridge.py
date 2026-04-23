from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

from local_agent_runtime.event_bus import EventBus
from local_agent_runtime.orchestrator.service import Orchestrator
from local_agent_runtime.policy.guard import PolicyGuard
from local_agent_runtime.rpc.server import JsonRpcServer
from local_agent_runtime.services import CollaborationService, SubagentService
from local_agent_runtime.store.sqlite_store import SQLiteStore
from local_agent_runtime.tools.builtin import build_builtin_tools
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


def _make_runtime(tmp_path: Any, provider: Any) -> SimpleNamespace:
    event_bus = EventBus()
    store = SQLiteStore(str(tmp_path / "runtime.sqlite3"))
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
    return SimpleNamespace(server=server, store=store, events=events)


def _call_result(response: dict[str, Any], key: str) -> dict[str, Any]:
    assert "result" in response, response
    return response["result"][key]


def _rpc(runtime: SimpleNamespace, method: str, params: dict[str, Any]) -> dict[str, Any]:
    envelope = {
        "jsonrpc": "2.0",
        "id": f"req_{len(runtime.events)}_{method}",
        "method": method,
        "params": params,
    }
    response = runtime.server.handle_line(json.dumps(envelope, ensure_ascii=False))
    assert response["jsonrpc"] == "2.0"
    assert response["id"] == envelope["id"]
    return response


def _open_session(runtime: SimpleNamespace, tmp_path: Any) -> dict[str, Any]:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    workspace = _call_result(_rpc(runtime, "workspace.open", {"path": str(workspace_root)}), "workspace")
    return _call_result(
        _rpc(runtime, "session.create", {"workspaceId": workspace["id"], "title": "child bridge"}),
        "session",
    )


def test_parent_session_bus_receives_live_child_worker_progress_events_during_task_delegation(tmp_path: Any) -> None:
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
    runtime = _make_runtime(tmp_path, provider)
    session = _open_session(runtime, tmp_path)

    parent_task = _call_result(
        _rpc(runtime, "message.send", {"sessionId": session["id"], "content": "delegate this"}),
        "task",
    )

    delegated = provider.calls[1]["context"]["tool_results"][0]["result"]
    child_task_id = delegated["childTaskId"]
    assert isinstance(child_task_id, str) and child_task_id.startswith("ctask_")

    parent_tool_started_index = next(
        index
        for index, event in enumerate(runtime.events)
        if event["type"] == "tool.started"
        and event["taskId"] == parent_task["id"]
        and event["payload"]["toolName"] == "task"
    )
    parent_tool_completed_index = next(
        index
        for index, event in enumerate(runtime.events)
        if event["type"] == "tool.completed"
        and event["taskId"] == parent_task["id"]
        and event["payload"]["toolName"] == "task"
    )

    bridged_child_events = [
        event
        for event in runtime.events[parent_tool_started_index + 1 : parent_tool_completed_index]
        if event["taskId"] == child_task_id
        and event["type"] == "collab.task.updated"
        and isinstance(event["payload"], dict)
        and isinstance(event["payload"].get("_bridge"), dict)
    ]

    assert bridged_child_events
    assert all(event["sessionId"] == session["id"] for event in bridged_child_events)
    assert all(event["payload"]["_bridge"]["source"] == "child-worker" for event in bridged_child_events)
    assert all(event["payload"]["_bridge"]["parentRuntimeTaskId"] == parent_task["id"] for event in bridged_child_events)
    child_event_types = [
        event["payload"]["_bridge"]["childEvent"]["type"]
        for event in bridged_child_events
        if isinstance(event["payload"]["_bridge"].get("childEvent"), dict)
    ]
    assert "tool.started" in child_event_types
