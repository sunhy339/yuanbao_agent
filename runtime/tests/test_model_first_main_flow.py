from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from local_agent_runtime.event_bus import EventBus
from local_agent_runtime.orchestrator.service import Orchestrator
from local_agent_runtime.policy.tool_policy_resolver import ToolPolicyResolver
from local_agent_runtime.rpc.server import JsonRpcServer
from local_agent_runtime.store.sqlite_store import SQLiteStore
from local_agent_runtime.tools.registry import BUILTIN_TOOL_SCHEMAS, ToolRegistry


class ScriptedProvider:
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def generate(self, prompt: str, context: dict[str, Any]) -> dict[str, Any]:
        self.calls.append({"prompt": prompt, "context": context})
        if not self._responses:
            raise AssertionError("Provider called more times than scripted")
        return self._responses.pop(0)


def _make_runtime(tmp_path: Path, provider: Any) -> SimpleNamespace:
    event_bus = EventBus()
    store = SQLiteStore(str(tmp_path / "runtime.sqlite3"))
    orchestrator = Orchestrator(
        store=store,
        event_bus=event_bus,
        tool_registry=ToolRegistry({}),
        provider=provider,
    )
    server = JsonRpcServer(orchestrator=orchestrator, store=store, event_bus=event_bus)
    events: list[dict[str, Any]] = []
    event_bus.subscribe(lambda event: events.append(event_bus.as_payload(event)))
    return SimpleNamespace(server=server, store=store, orchestrator=orchestrator, events=events)


def _rpc(runtime: SimpleNamespace, method: str, params: dict[str, Any]) -> dict[str, Any]:
    response = runtime.server.handle_line(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": f"req_{len(runtime.events)}",
                "method": method,
                "params": params,
            },
            ensure_ascii=False,
        )
    )
    assert response["jsonrpc"] == "2.0"
    assert "result" in response, response
    return response["result"]


def _open_session(runtime: SimpleNamespace, tmp_path: Path) -> dict[str, Any]:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    workspace = _rpc(runtime, "workspace.open", {"path": str(workspace_root)})["workspace"]
    return _rpc(runtime, "session.create", {"workspaceId": workspace["id"], "title": "model first"})["session"]


def _event_types(runtime: SimpleNamespace) -> list[str]:
    return [event["type"] for event in runtime.events]


def _builtin_tools(*names: str) -> list[dict[str, Any]]:
    by_name = {schema["name"]: schema for schema in BUILTIN_TOOL_SCHEMAS}
    return [by_name[name] for name in names]


def test_default_message_send_uses_provider_loop_without_router_events(tmp_path: Path) -> None:
    provider = ScriptedProvider([{"final": "你好，我在。"}])
    runtime = _make_runtime(tmp_path, provider)
    session = _open_session(runtime, tmp_path)

    task = _rpc(runtime, "message.send", {"sessionId": session["id"], "content": "你好"})["task"]

    assert task["status"] == "completed"
    assert task["resultSummary"] == "你好，我在。"
    assert len(provider.calls) == 1
    provider_routing = provider.calls[0]["context"]["routing"]
    assert provider_routing["mode"] == "model_first"
    assert "scenario" not in provider_routing
    assert "strategy" not in provider_routing
    assert "enable_planning" not in provider_routing
    assert "enable_reflection" not in provider_routing
    assert "worktreeBindingRequired" not in provider_routing
    assert "intentConfidence" not in provider_routing["mainWorkflow"]

    types = _event_types(runtime)
    assert "runtime.context.prepared" in types
    assert "task.routing.decided" not in types
    assert "task.planning.started" not in types
    assert "task.planning.subtask.started" not in types
    prepared = next(event for event in runtime.events if event["type"] == "runtime.context.prepared")
    assert prepared["visibility"] == "trace"
    started = next(event for event in runtime.events if event["type"] == "task.started")
    assert started["visibility"] == "panel"
    assert "routing" not in started["payload"]


def test_old_plan_strategy_hint_cannot_enter_legacy_orchestration(tmp_path: Path) -> None:
    provider = ScriptedProvider([{"final": "The model answered without backend decomposition."}])
    runtime = _make_runtime(tmp_path, provider)
    session = _open_session(runtime, tmp_path)
    original = runtime.orchestrator._model_first_routing_context

    def forced_runtime_metadata(**kwargs: Any) -> dict[str, Any]:
        metadata = original(**kwargs)
        metadata.update({"strategy": "plan_swarm", "enable_planning": True})
        return metadata

    runtime.orchestrator._model_first_routing_context = forced_runtime_metadata

    assert not hasattr(runtime.orchestrator, "_decomposer")
    assert not hasattr(runtime.orchestrator, "_swarm")

    task = _rpc(
        runtime,
        "message.send",
        {"sessionId": session["id"], "content": "Use multiple agents to optimize this"},
    )["task"]

    assert task["status"] == "completed"
    assert task["resultSummary"] == "The model answered without backend decomposition."
    assert "task.planning.started" not in _event_types(runtime)


def test_summary_only_review_hint_does_not_create_completion_gate(tmp_path: Path) -> None:
    runtime = _make_runtime(tmp_path, ScriptedProvider([]))
    session = _open_session(runtime, tmp_path)
    task = runtime.store.create_task(
        session_id=session["id"],
        task_type="edit",
        goal="write a small file",
        plan=[],
        status="running",
        routing={"profile": {"expectedArtifacts": [{"path": "hello.txt", "kind": "file"}]}},
    )

    completed = runtime.orchestrator._complete_task(
        session_id=session["id"],
        task=task,
        summary="I would write the file.",
        context={"_require_summary_only_completion_review": True},
        tool_results=[],
        skip_reflection=True,
    )

    assert completed["status"] == "completed"
    event_types = _event_types(runtime)
    assert "approval.requested" not in event_types
    assert "task.runtime_work_waiting" not in event_types
    assert "agent.decision.completion" not in event_types
    assert "completionGate" not in completed["structuredResult"]
    assert "completionReview" not in completed["structuredResult"]


def test_tool_policy_ignores_legacy_plan_strategy_for_initial_phase() -> None:
    resolver = ToolPolicyResolver()
    decision = resolver.resolve(
        task={"id": "task_root", "role": "root"},
        context={"routing": {"strategy": "plan_swarm", "enable_planning": True}},
        tool_results=[],
        registered_tools=_builtin_tools("agent", "task", "read_file", "write_file"),
    )

    assert decision.phase == "investigation"
    assert {"agent", "task", "read_file", "write_file"}.issubset(set(decision.allowed_tool_names))
