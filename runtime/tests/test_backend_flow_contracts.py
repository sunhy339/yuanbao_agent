from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from local_agent_runtime.event_bus import EventBus
from local_agent_runtime.execution.tool_pipeline import _frontend_visible_tool_result
from local_agent_runtime.main import build_server
from local_agent_runtime.memory import MemoryManager, MemoryRetriever, MemoryStore
from local_agent_runtime.orchestration.types import OrchestrationResult
from local_agent_runtime.orchestrator.service import Orchestrator
from local_agent_runtime.planner.types import PlanResult, Subtask
from local_agent_runtime.policy.guard import PolicyGuard
from local_agent_runtime.policy.permission_engine import PermissionEngine
from local_agent_runtime.router.meta_router import MetaRouter
from local_agent_runtime.router.types import ExecutionStrategy, RoutingDecision, Scenario
from local_agent_runtime.rpc.server import JsonRpcServer
from local_agent_runtime.services import CollaborationService, SubagentService
from local_agent_runtime.store.sqlite_store import SQLiteStore
from local_agent_runtime.tools import build_builtin_tools
from local_agent_runtime.tools.registry import ToolRegistry


class ScriptedProvider:
    def __init__(self, responses: list[dict[str, Any]] | None = None, *, error: Exception | None = None) -> None:
        self._responses = list(responses or [])
        self._error = error
        self.calls: list[dict[str, Any]] = []

    def generate(self, prompt: str, context: dict[str, Any]) -> dict[str, Any]:
        self.calls.append({"prompt": prompt, "context": context})
        if self._error is not None:
            raise self._error
        if not self._responses:
            raise AssertionError("Provider called more times than scripted")
        return self._responses.pop(0)


class CancellingStreamProvider:
    def __init__(self) -> None:
        self.runtime: SimpleNamespace | None = None
        self.calls: list[dict[str, Any]] = []

    def generate(self, prompt: str, context: dict[str, Any]) -> dict[str, Any]:
        raise AssertionError("streaming provider should not fall back to generate")

    def stream(self, prompt: str, context: dict[str, Any]) -> Any:
        self.calls.append({"prompt": prompt, "context": context})
        yield {"type": "content_delta", "delta": "visible before cancel "}
        if self.runtime is None:
            raise AssertionError("runtime not attached")
        running_tasks = [
            task
            for task in self.runtime.store.list_tasks({})["tasks"]
            if task.get("status") == "running"
        ]
        assert running_tasks, "expected a running task to cancel"
        self.runtime.orchestrator.cancel_task({"taskId": running_tasks[0]["id"]})
        yield {"type": "content_delta", "delta": "late after cancel "}
        yield {
            "type": "final",
            "response": {
                "message": {"content": "visible before cancel late after cancel "},
                "finish_reason": "stop",
                "raw": {},
            },
        }


def _make_runtime(tmp_path: Path, provider: Any, tools: dict[str, Any] | None = None) -> SimpleNamespace:
    event_bus = EventBus()
    store = SQLiteStore(str(tmp_path / "runtime.sqlite3"))
    tool_registry = ToolRegistry(tools or {})
    orchestrator = Orchestrator(
        store=store,
        event_bus=event_bus,
        tool_registry=tool_registry,
        provider=provider,
        meta_router=MetaRouter(provider=None),
    )
    server = JsonRpcServer(orchestrator=orchestrator, store=store, event_bus=event_bus)
    events: list[dict[str, Any]] = []
    event_bus.subscribe(lambda event: events.append(event_bus.as_payload(event)))
    return SimpleNamespace(server=server, store=store, orchestrator=orchestrator, events=events)


def _make_memory_runtime(tmp_path: Path, provider: Any, tools: dict[str, Any] | None = None) -> SimpleNamespace:
    event_bus = EventBus()
    store = SQLiteStore(str(tmp_path / "runtime.sqlite3"))
    memory_store = MemoryStore(store)
    memory_manager = MemoryManager(
        store=memory_store,
        retriever=MemoryRetriever(memory_store),
    )
    tool_registry = ToolRegistry(tools or {})
    orchestrator = Orchestrator(
        store=store,
        event_bus=event_bus,
        tool_registry=tool_registry,
        provider=provider,
        meta_router=MetaRouter(provider=None),
        memory_manager=memory_manager,
    )
    server = JsonRpcServer(orchestrator=orchestrator, store=store, event_bus=event_bus)
    events: list[dict[str, Any]] = []
    event_bus.subscribe(lambda event: events.append(event_bus.as_payload(event)))
    return SimpleNamespace(server=server, store=store, orchestrator=orchestrator, events=events)


def _make_builtin_runtime(tmp_path: Path, provider: Any) -> SimpleNamespace:
    event_bus = EventBus()
    store = SQLiteStore(str(tmp_path / "runtime.sqlite3"))
    store.set_feature_flag("multiAgent", True)
    config = store.get_config({})["config"]
    policy_guard = PolicyGuard(approval_mode=config["policy"]["approvalMode"])
    permission_engine = PermissionEngine(config=config, store=store)
    collaboration = CollaborationService(store, event_bus)
    subagent_service = SubagentService(store, collaboration)
    tool_registry = ToolRegistry(
        build_builtin_tools(
            policy_guard=policy_guard,
            store=store,
            subagent_service=subagent_service,
            permission_engine=permission_engine,
        )
    )
    orchestrator = Orchestrator(
        store=store,
        event_bus=event_bus,
        tool_registry=tool_registry,
        provider=provider,
        meta_router=MetaRouter(provider=None),
    )
    server = JsonRpcServer(orchestrator=orchestrator, store=store, event_bus=event_bus)
    events: list[dict[str, Any]] = []
    event_bus.subscribe(lambda event: events.append(event_bus.as_payload(event)))
    return SimpleNamespace(server=server, store=store, orchestrator=orchestrator, events=events)


def _rpc(runtime: SimpleNamespace, method: str, params: dict[str, Any]) -> dict[str, Any]:
    request_id = f"req_{len(runtime.events)}_{method}"
    response = runtime.server.handle_line(json.dumps({
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
        "params": params,
    }, ensure_ascii=False))
    assert response["jsonrpc"] == "2.0"
    assert response["id"] == request_id
    return response


def _open_session(runtime: SimpleNamespace, tmp_path: Path) -> dict[str, Any]:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    workspace = _rpc(runtime, "workspace.open", {"path": str(workspace_root)})["result"]["workspace"]
    return _rpc(runtime, "session.create", {"workspaceId": workspace["id"], "title": "flow contract"})["result"]["session"]


def _event_types(runtime: SimpleNamespace) -> list[str]:
    return [event["type"] for event in runtime.events]


def _set_approval_mode(runtime: SimpleNamespace, mode: str) -> None:
    config = runtime.store.get_config({})["config"]
    config["policy"]["approvalMode"] = mode
    runtime.store.update_config({"config": config})


def _force_route(runtime: SimpleNamespace, *, scenario: Scenario, strategy: ExecutionStrategy) -> None:
    runtime.orchestrator._meta_router.route = lambda _goal, context=None: RoutingDecision(
        scenario=scenario,
        strategy=strategy,
        confidence=0.95,
        max_steps=20,
        enable_reflection=False,
        enable_planning=strategy in {
            ExecutionStrategy.PLAN_THEN_EXECUTE,
            ExecutionStrategy.PLAN_SUPERVISE,
            ExecutionStrategy.PLAN_SWARM,
        },
        reasoning="forced backend flow contract",
        metadata={"legacyPlanExecution": True},
    )


def test_default_server_does_not_enable_backend_advisor_main_path(tmp_path: Path) -> None:
    server = build_server(database_path=str(tmp_path / "server.sqlite3"))
    try:
        orchestrator = server._orchestrator  # noqa: SLF001
        assert getattr(orchestrator, "_decision_advisor", None) is None
        router = getattr(orchestrator, "_meta_router")
        assert getattr(router, "_decision_advisor", None) is None
        assert getattr(router, "_provider", None) is None
    finally:
        server.graceful_shutdown()


def test_simple_chat_stays_model_first_without_tool_or_plan_flow(tmp_path: Path) -> None:
    provider = ScriptedProvider([{"final": "你好！"}])
    runtime = _make_runtime(tmp_path, provider)
    session = _open_session(runtime, tmp_path)

    response = _rpc(runtime, "message.send", {"sessionId": session["id"], "content": "你好"})

    assert response["result"]["task"]["status"] == "completed"
    types = _event_types(runtime)
    assert "message.completed" in types
    assert "message_complete" in types
    assert "tool.started" not in types
    assert "plan_update" not in types
    assert "approval.requested" not in types
    assert len(provider.calls) == 1


def test_document_style_prompt_does_not_invent_workspace_probe_or_review(tmp_path: Path) -> None:
    provider = ScriptedProvider([{"final": "Here is the requested project document."}])
    runtime = _make_runtime(tmp_path, provider)
    session = _open_session(runtime, tmp_path)

    response = _rpc(runtime, "message.send", {"sessionId": session["id"], "content": "generate a document"})

    assert response["result"]["task"]["status"] == "completed"
    types = _event_types(runtime)
    assert "tool.started" not in types
    assert "command.started" not in types
    assert "task.planning.started" not in types
    assert "approval.requested" not in types
    assert "completion_review" not in types
    assert "assistant_progress" not in types
    assert len(provider.calls) == 1


def test_model_tool_swarm_summary_only_does_not_request_completion_review(tmp_path: Path) -> None:
    runtime = _make_runtime(tmp_path, ScriptedProvider([]))
    session = _open_session(runtime, tmp_path)
    task = runtime.store.create_task(
        session_id=session["id"],
        task_type="agent",
        goal="Use multiple agents for read-only analysis",
        plan=[],
        routing={
            "scenario": "swarm_task",
            "strategy": "plan_swarm",
            "orchestrationMode": "model_tools",
        },
    )

    result = runtime.orchestrator._complete_task(  # noqa: SLF001
        session_id=session["id"],
        task=task,
        summary="Read-only agent synthesis completed.",
        context={
            "routing": {
                "scenario": "swarm_task",
                "strategy": "plan_swarm",
                "orchestrationMode": "model_tools",
            }
        },
        skip_reflection=True,
    )

    assert result["status"] == "completed"
    assert "completionGate" not in result.get("structuredResult", {})
    assert "completion_review" not in _event_types(runtime)
    assert not [
        event for event in runtime.events
        if event["type"] == "approval.requested"
        and event["payload"].get("kind") == "completion_review"
    ]


def test_subagent_visible_tool_result_hides_internal_completion_evidence() -> None:
    visible = _frontend_visible_tool_result(
        "agent",
        {
            "status": "completed",
            "summary": "Child analysis finished.",
            "childTaskId": "ctask_child",
            "workerId": "agent_worker",
            "result": {
                "summary": "Internal result summary.",
                "changedFiles": [{"path": "snake_game/game.py"}],
                "completionEvidence": {"advisorRequestedEvidence": [{"summary": "internal"}]},
                "completionGate": {"status": "needs_review"},
                "workspaceRoot": "D:/py/test_pro",
            },
            "message": {
                "id": "msg_child",
                "kind": "result",
                "body": "Child analysis finished.",
                "payload": {
                    "completionEvidence": {"status": "internal"},
                    "visible": "kept",
                },
            },
            "structuredResult": {
                "summary": "Structured summary.",
                "completionEvidence": {"status": "internal"},
                "completionReview": {"decision": "internal"},
            },
            "completionEvidence": {"status": "internal"},
            "workspaceRoot": "D:/py/test_pro",
        },
    )

    encoded = json.dumps(visible, ensure_ascii=False)
    assert visible["status"] == "completed"
    assert visible["childTaskId"] == "ctask_child"
    assert visible["message"]["body"] == "Child analysis finished."
    assert "completionEvidence" not in encoded
    assert "completionGate" not in encoded
    assert "completionReview" not in encoded
    assert "advisorRequestedEvidence" not in encoded
    assert "workspaceRoot" not in encoded


def test_small_visible_tool_result_is_public_scrubbed() -> None:
    visible = _frontend_visible_tool_result(
        "custom_tool",
        {
            "status": "completed",
            "summary": "Done",
            "workspaceRoot": "D:/py/test_pro",
            "requestJson": {"token": "secret"},
            "raw": {"providerRequest": {"apiKey": "sk-hidden"}},
            "items": [{"name": "public"}],
        },
    )

    encoded = json.dumps(visible, ensure_ascii=False)
    assert visible["status"] == "completed"
    assert visible["summary"] == "Done"
    assert visible["items"] == [{"name": "public"}]
    for marker in ("workspaceRoot", "requestJson", "providerRequest", "apiKey", "secret", "sk-hidden"):
        assert marker not in encoded


def test_file_change_visible_tool_result_hides_internal_patch_record() -> None:
    visible = _frontend_visible_tool_result(
        "apply_patch",
        {
            "status": "applied",
            "summary": "Update README.md",
            "filesChanged": 1,
            "changedPaths": ["README.md"],
            "diffText": "--- a/README.md\n+++ b/README.md\n@@\n-old\n+new",
            "patch": {
                "id": "patch_1",
                "workspaceId": "D:/py/test_pro",
                "taskId": "task_1",
                "diffText": "--- a/README.md\n+++ b/README.md",
            },
        },
    )

    encoded = json.dumps(visible, ensure_ascii=False)
    assert visible["status"] == "applied"
    assert visible["summary"] == "Update README.md"
    assert visible["changedPaths"] == ["README.md"]
    assert "diffText" not in visible
    assert "workspaceId" not in encoded
    assert '"patch"' not in encoded


def test_trace_only_routing_event_does_not_emit_flat_chat_message(tmp_path: Path) -> None:
    provider = ScriptedProvider([{"final": "done"}])
    runtime = _make_runtime(tmp_path, provider)
    session = _open_session(runtime, tmp_path)

    response = _rpc(runtime, "message.send", {"sessionId": session["id"], "content": "read file.txt"})

    task = response["result"]["task"]
    routing_events = [event for event in runtime.events if event["type"] == "task.routing.decided"]
    assert routing_events
    assert all(event["visibility"] == "trace" for event in routing_events)
    assert all("yuanbao" not in event and "hahaCc" not in event for event in routing_events)
    persisted = runtime.store.list_trace_events({"taskId": task["id"], "limit": 100})["traceEvents"]
    persisted_routing = [event for event in persisted if event["type"] == "task.routing.decided"]
    assert persisted_routing
    assert all("yuanbao" not in event and "hahaCc" not in event for event in persisted_routing)
    replay = runtime.server._handlers["events.yuanbaoAfter"]({"sessionId": session["id"], "afterSeq": 0})["messages"]
    assert not any(
        message.get("type") == "task_update" and message.get("status") == "react_standard"
        for message in replay
    )


def test_provider_thinking_surrounds_tool_cycle_in_haha_order(tmp_path: Path) -> None:
    provider = ScriptedProvider([
        {
            "thought_summary": "Need to search before answering.",
            "message": "I will search the workspace.",
            "tool_calls": [
                {
                    "id": "call_search",
                    "name": "search_files",
                    "arguments": {"query": "needle"},
                }
            ],
        },
        {
            "thought_summary": "Search result is enough to answer.",
            "final": "Found needle in alpha.txt.",
        },
    ])

    runtime = _make_runtime(
        tmp_path,
        provider,
        {
            "search_files": lambda params: {
                "query": params["query"],
                "matches": [{"path": "alpha.txt", "preview": "needle"}],
                "total": 1,
            },
        },
    )
    session = _open_session(runtime, tmp_path)

    response = _rpc(runtime, "message.send", {"sessionId": session["id"], "content": "find needle"})

    assert response["result"]["task"]["status"] == "completed"
    events = runtime.events

    def index_of(predicate: Any) -> int:
        return next(index for index, event in enumerate(events) if predicate(event))

    first_thinking = index_of(
        lambda event: event["type"] == "thinking"
        and event["payload"].get("text") == "Need to search before answering."
    )
    tool_start = index_of(
        lambda event: event["type"] == "content_start"
        and event["payload"].get("blockType") == "tool_use"
        and event["payload"].get("toolUseId") == "call_search"
    )
    tool_result = index_of(
        lambda event: event["type"] == "tool_result"
        and event["payload"].get("toolUseId") == "call_search"
    )
    second_thinking = index_of(
        lambda event: event["type"] == "thinking"
        and event["payload"].get("text") == "Search result is enough to answer."
    )
    final_delta = index_of(
        lambda event: event["type"] == "message.delta"
        and event["payload"].get("delta") == "Found needle in alpha.txt."
        and event.get("yuanbao") == {"type": "content_delta", "text": "Found needle in alpha.txt."}
    )
    message_complete = index_of(lambda event: event["type"] == "message_complete")

    assert first_thinking < tool_start < tool_result < second_thinking < final_delta < message_complete
    assert all(event["visibility"] == "chat" for event in events if event["type"] in {"thinking", "content_start", "tool_result", "message.delta", "message_complete"})


def test_chat_compat_tool_frames_persist_for_session_replay(tmp_path: Path) -> None:
    provider = ScriptedProvider([
        {
            "thought_summary": "Need one workspace lookup.",
            "message": "I will read the note.",
            "tool_calls": [
                {"id": "call_read", "name": "read_file", "arguments": {"path": "README.md"}},
            ],
        },
        {"final": "The note says hello."},
    ])
    runtime = _make_runtime(
        tmp_path,
        provider,
        {
            "read_file": lambda params: {
                "path": params["path"],
                "content": "hello",
                "bytes": 5,
            },
        },
    )
    session = _open_session(runtime, tmp_path)

    response = _rpc(runtime, "message.send", {"sessionId": session["id"], "content": "read the note"})
    task = response["result"]["task"]

    trace = runtime.store.list_trace_events({"taskId": task["id"], "limit": 200})["traceEvents"]
    trace_types = [event["type"] for event in trace]

    for expected in ["thinking", "content_start", "tool_use_complete", "content_delta", "tool_result", "message_complete"]:
        assert expected in trace_types

    raw_tool_lifecycle = [event for event in trace if event["type"] in {"tool.started", "tool.completed"}]
    assert raw_tool_lifecycle
    assert all(event["visibility"] == "trace" for event in raw_tool_lifecycle)

    recoverable = [
        event
        for event in trace
        if event["type"] in {"thinking", "content_start", "tool_use_complete", "content_delta", "tool_result", "message_complete"}
    ]
    assert recoverable
    assert all(event["visibility"] == "chat" for event in recoverable)
    assert all(event["payload"].get("_chatCompat") is True for event in recoverable)
    assert all(event["payload"].get("_bridge", {}).get("persistTraceMirror") is True for event in recoverable)
    assert all(
        event.get("yuanbao")
        for event in recoverable
        if not (event["type"] == "content_delta" and event["payload"].get("toolOutput"))
    )

    tool_output_deltas = [
        event
        for event in recoverable
        if event["type"] == "content_delta" and event["payload"].get("toolOutput")
    ]
    assert tool_output_deltas
    assert not [
        event for event in trace
        if event["type"] == "content_delta" and event["payload"].get("text") == "The note says hello."
    ]

    recovered = runtime.store.events_after(session["id"], 0)["events"]
    recovered_types = [event["type"] for event in recovered]
    for expected in ["thinking", "content_start", "tool_use_complete", "tool_result", "message_complete"]:
        assert expected in recovered_types
    completed_raw = [event for event in recovered if event["type"] == "message.completed"]
    assert completed_raw
    assert all(event.get("yuanbao") is None for event in completed_raw)
    assert all(event["payload"].get("_bridge", {}).get("suppressRealtimeFlat") is True for event in completed_raw)


def test_late_tool_lifecycle_after_terminal_task_stays_trace_only(tmp_path: Path) -> None:
    runtime = _make_runtime(tmp_path, ScriptedProvider([]))
    session = _open_session(runtime, tmp_path)
    task = runtime.store.create_task(
        session_id=session["id"],
        task_type="chat",
        goal="already done",
        plan=[],
        status="completed",
    )

    start_index = len(runtime.events)
    runtime.orchestrator._publish(  # noqa: SLF001
        session["id"],
        task,
        "tool.started",
        {
            "toolCallId": "call_late",
            "toolName": "read_file",
            "arguments": {"path": "README.md"},
        },
    )
    runtime.orchestrator._publish(  # noqa: SLF001
        session["id"],
        task,
        "tool.progress",
        {
            "toolUseId": "call_late",
            "toolName": "read_file",
            "message": "late progress",
        },
    )
    runtime.orchestrator._publish(  # noqa: SLF001
        session["id"],
        task,
        "tool.completed",
        {
            "toolCallId": "call_late",
            "toolName": "read_file",
            "result": {"status": "completed", "summary": "late done"},
            "resultSummary": "late done",
        },
    )

    emitted = runtime.events[start_index:]
    assert [event["type"] for event in emitted] == ["tool.started", "tool.progress", "tool.completed"]
    assert all(event["visibility"] == "trace" for event in emitted)
    assert not any(
        event["type"] in {"content_start", "content_delta", "tool_use_complete", "tool_result"}
        for event in emitted
    )


def test_tool_progress_and_output_chat_deltas_hide_internal_payload_text(tmp_path: Path) -> None:
    runtime = _make_runtime(tmp_path, ScriptedProvider([]))
    session = _open_session(runtime, tmp_path)
    task = runtime.store.create_task(
        session_id=session["id"],
        task_type="chat",
        goal="inspect",
        plan=[],
    )

    runtime.orchestrator._publish(  # noqa: SLF001
        session["id"],
        task,
        "tool.progress",
        {
            "toolUseId": "call_safe",
            "toolName": "read_file",
            "message": "Checked the README summary.",
        },
    )
    runtime.orchestrator._publish(  # noqa: SLF001
        session["id"],
        task,
        "tool.progress",
        {
            "toolUseId": "call_internal",
            "toolName": "read_file",
            "message": '{"requestJson":{"workspaceRoot":"D:/py/test_pro","token":"secret"}}',
        },
    )
    runtime.orchestrator._publish(  # noqa: SLF001
        session["id"],
        task,
        "command.output",
        {
            "toolUseId": "call_command",
            "toolName": "run_command",
            "chunk": "workspaceRoot=D:/py/test_pro token=secret\n",
        },
    )
    runtime.orchestrator._publish(  # noqa: SLF001
        session["id"],
        task,
        "tool.completed",
        {
            "toolCallId": "call_result",
            "toolName": "custom_tool",
            "result": {
                "status": "completed",
                "steps": [
                    {"label": "public", "summary": "Public activity"},
                    {"label": "internal", "summary": "workspaceRoot D:/py/test_pro token secret"},
                ],
            },
            "resultPreview": [
                {"label": "requestJson", "value": '{"workspaceRoot":"D:/py/test_pro"}'},
            ],
            "resultSummary": "workspaceRoot D:/py/test_pro token secret",
        },
    )

    tool_output_text = "\n".join(
        str(event["payload"].get("toolOutput") or "")
        for event in runtime.events
        if event["type"] == "content_delta"
    )
    assert "Checked the README summary." in tool_output_text
    assert "Public activity" in tool_output_text
    assert "requestJson" not in tool_output_text
    assert "workspaceRoot" not in tool_output_text
    assert "D:/py/test_pro" not in tool_output_text
    assert "secret" not in tool_output_text
    assert "token" not in tool_output_text


def test_write_file_approval_is_waiting_node_without_raw_request_json(tmp_path: Path) -> None:
    provider = ScriptedProvider([
        {
            "thought_summary": "Need to create the requested file.",
            "message": "I will create the file.",
            "tool_calls": [
                {
                    "id": "call_write",
                    "name": "write_file",
                    "arguments": {
                        "path": "todo.html",
                        "content": "<h1>Todo</h1>",
                        "overwrite": True,
                    },
                }
            ],
        }
    ])
    runtime = _make_builtin_runtime(tmp_path, provider)
    session = _open_session(runtime, tmp_path)

    response = _rpc(runtime, "message.send", {"sessionId": session["id"], "content": "create todo"})

    assert response["result"]["task"]["status"] == "waiting_approval"
    types = _event_types(runtime)
    assert "approval.requested" in types
    assert "task.waiting_approval" in types
    assert "tool.blocked" in types
    assert "tool.completed" not in types

    tool_result = next(
        event["payload"]
        for event in runtime.events
        if event["type"] == "tool_result" and event["payload"].get("toolUseId") == "call_write"
    )
    content = tool_result["content"]
    assert content["status"] == "approval_required"
    assert content["summary"] == "approval required before writing todo.html"
    assert "requestJson" not in json.dumps(content, ensure_ascii=False)
    assert "workspaceRoot" not in json.dumps(content, ensure_ascii=False)
    assert "approval" not in content
    assert content["approvalStatus"] == "waiting"
    assert content["approvalKind"] == "write_file"

    flat_permission = next(
        event["yuanbao"]
        for event in runtime.events
        if event.get("yuanbao", {}).get("type") == "permission_request"
        and event["yuanbao"].get("toolName") == "write_file"
    )
    permission_input_json = json.dumps(flat_permission["input"], ensure_ascii=False)
    assert flat_permission["input"]["path"] == "todo.html"
    assert "requestJson" not in permission_input_json
    assert "workspaceRoot" not in permission_input_json
    assert "taskId" not in permission_input_json
    assert "sessionId" not in permission_input_json

    blocked = next(
        event["payload"]
        for event in runtime.events
        if event["type"] == "tool.blocked" and event["payload"].get("toolCallId") == "call_write"
    )
    assert blocked["resultSummary"] == "approval required before writing todo.html"
    blocked_json = json.dumps(blocked, ensure_ascii=False)
    assert "requestJson" not in blocked_json
    assert "workspaceRoot" not in blocked_json


def test_completion_review_approval_is_internal_and_idempotent(tmp_path: Path) -> None:
    runtime = _make_runtime(tmp_path, ScriptedProvider([]))
    session = _open_session(runtime, tmp_path)
    task = runtime.store.create_task(
        session_id=session["id"],
        task_type="edit",
        goal="modify implementation",
        plan=[],
        routing={"scenario": "code_edit"},
        status="waiting_approval",
    )
    approval = runtime.store.create_approval(
        task["id"],
        "completion_review",
        {
            "summary": "Internal completion review.",
            "structuredResult": {
                "summary": "Internal completion review.",
                "status": "needs_review",
                "completionEvidence": {"status": "summary_only", "evidenceLevel": "summary_only"},
            },
            "completionEvidence": {"status": "summary_only", "evidenceLevel": "summary_only"},
        },
    )

    first = runtime.orchestrator.submit_approval({"approvalId": approval["id"], "decision": "approved"})
    resolved_count_after_first = len([event for event in runtime.events if event["type"] == "approval.resolved"])
    second = runtime.orchestrator.submit_approval({"approvalId": approval["id"], "decision": "approved"})

    assert first["task"]["status"] == "completed"
    assert second["ignored"] is True
    assert len([event for event in runtime.events if event["type"] == "approval.resolved"]) == resolved_count_after_first
    assert all(
        event["payload"].get("kind") != "permission_request"
        for event in runtime.events
        if event["type"] in {"permission_request", "approval.resolved"}
    )
    review_events = [event for event in runtime.events if event["type"] == "approval.resolved"]
    assert review_events
    assert review_events[-1]["payload"]["kind"] == "completion_review"
    assert review_events[-1]["payload"]["internal"] is True
    assert review_events[-1]["payload"]["_bridge"]["suppressRealtimeFlat"] is True
    assert review_events[-1]["payload"]["_bridge"]["suppressChatReplay"] is True


def test_internal_question_answer_is_one_shot_and_not_user_message(tmp_path: Path) -> None:
    runtime = _make_runtime(tmp_path, ScriptedProvider([]))
    session = _open_session(runtime, tmp_path)
    task = runtime.store.create_task(
        session_id=session["id"],
        task_type="chat",
        goal="needs missing info",
        plan=[],
        status="paused",
    )
    internal_response = {
        "kind": "ask_user_question",
        "messageId": "ask_user_question:ask_1",
        "requestId": "ask_1",
        "toolCallId": "tool_ask_1",
    }

    first = runtime.orchestrator._attach_supplemental_message(
        session_id=session["id"],
        task=task,
        content="Use the status list.",
        internal_response=internal_response,
    )
    second = runtime.orchestrator._attach_supplemental_message(
        session_id=session["id"],
        task=task,
        content="Use the status list.",
        internal_response=internal_response,
    )

    assert first["acceptedMode"] == "supplement"
    assert second["duplicate"] is True
    messages = runtime.store.list_messages({"sessionId": session["id"], "limit": 100})["messages"]
    assert not [message for message in messages if message.get("role") == "user"]
    inbox_items = runtime.store.list_task_inbox_items(task["id"])
    assert len(inbox_items) == 1


def test_transient_provider_failure_has_single_user_failure_surface(tmp_path: Path) -> None:
    from local_agent_runtime.provider.openai_compatible import ProviderAdapterError

    provider = ScriptedProvider(error=ProviderAdapterError("Concurrency limit exceeded for account, please retry later"))
    runtime = _make_memory_runtime(tmp_path, provider)
    session = _open_session(runtime, tmp_path)
    runtime.orchestrator._meta_router.route = lambda _goal, context=None: RoutingDecision(
        scenario=Scenario.SIMPLE_QUERY,
        strategy=ExecutionStrategy.REACT_FAST,
        confidence=0.95,
        max_steps=3,
        enable_reflection=False,
        enable_planning=False,
        reasoning="simple provider failure test",
    )

    response = _rpc(runtime, "message.send", {"sessionId": session["id"], "content": "你好"})

    assert response["result"]["task"]["status"] == "failed"
    types = _event_types(runtime)
    assert types.count("message.failed") == 1
    assert types.count("task.failed") == 1
    assert not [event for event in runtime.events if event["type"] == "goal_event" and event["payload"].get("action") == "failed"]
    assert not [event for event in runtime.events if event["type"] == "memory_event"]
    assert not [
        event for event in runtime.events
        if event["type"] == "agent.decision.failure_recovery"
        and event["visibility"] != "trace"
    ]
    assert not [event for event in runtime.events if event["type"] == "approval.requested"]
    assert not [event for event in runtime.events if event["type"] == "completion_review"]
    assert MemoryStore(runtime.store).query_all(session_id=session["id"], workspace_id=session["workspaceId"]) == []


def test_provider_auth_failure_does_not_write_visible_memory_events(tmp_path: Path) -> None:
    from local_agent_runtime.provider.openai_compatible import ProviderAdapterError

    provider = ScriptedProvider(error=ProviderAdapterError("Provider request failed with HTTP 401: unknown provider error"))
    runtime = _make_memory_runtime(tmp_path, provider)
    session = _open_session(runtime, tmp_path)
    runtime.orchestrator._meta_router.route = lambda _goal, context=None: RoutingDecision(
        scenario=Scenario.SIMPLE_QUERY,
        strategy=ExecutionStrategy.REACT_FAST,
        confidence=0.95,
        max_steps=3,
        enable_reflection=False,
        enable_planning=False,
        reasoning="simple provider auth failure test",
    )

    response = _rpc(runtime, "message.send", {"sessionId": session["id"], "content": "你好"})

    assert response["result"]["task"]["status"] == "failed"
    task = response["result"]["task"]
    assert task["errorCode"] == "FAST_LOOP_FAILED"
    assert task["structuredResult"]["failureRecovery"]["category"] == "auth"
    types = _event_types(runtime)
    assert types.count("message.failed") == 1
    assert types.count("task.failed") == 1
    assert not [event for event in runtime.events if event["type"] == "goal_event" and event["payload"].get("action") == "failed"]
    assert not [event for event in runtime.events if event["type"] == "memory_event"]
    assert not [
        event for event in runtime.events
        if event["type"] == "agent.decision.failure_recovery"
        and event["visibility"] != "trace"
    ]
    assert not [event for event in runtime.events if event["type"] == "approval.requested"]
    assert not [event for event in runtime.events if event["type"] == "completion_review"]
    assert MemoryStore(runtime.store).query_all(session_id=session["id"], workspace_id=session["workspaceId"]) == []


def test_terminal_cancel_absorbs_late_visible_runtime_events(tmp_path: Path) -> None:
    runtime = _make_runtime(tmp_path, ScriptedProvider([]))
    session = _open_session(runtime, tmp_path)
    task = runtime.store.create_task(
        session_id=session["id"],
        task_type="chat",
        goal="stop this run",
        plan=[],
        status="running",
    )

    runtime.orchestrator.cancel_task({"taskId": task["id"]})
    cancelled_event_count = len(runtime.events)
    cancelled_trace_count = len(runtime.store.list_trace_events({"taskId": task["id"]})["traceEvents"])
    cancelled_task = runtime.store.get_task({"taskId": task["id"]})["task"]

    for event_type, payload in [
        (
            "tool.started",
            {
                "toolCallId": "call_late",
                "toolName": "read_file",
                "arguments": {"path": "README.md"},
                "target": "README.md",
            },
        ),
        (
            "tool.completed",
            {
                "toolCallId": "call_late",
                "toolName": "read_file",
                "result": {"content": "late"},
                "target": "README.md",
            },
        ),
        ("thinking", {"text": "late provider thinking"}),
        ("message.completed", {"messageId": "msg_late", "content": "late answer"}),
        (
            "approval.resolved",
            {
                "approvalId": "approval_late",
                "kind": "completion_review",
                "decision": "approved",
                "summary": "late review",
            },
        ),
    ]:
        runtime.orchestrator._publish(
            session_id=session["id"],
            task=cancelled_task,
            event_type=event_type,
            payload=payload,
        )

    runtime.orchestrator._publish(
        session_id=session["id"],
        task=cancelled_task,
        event_type="command.cancelled",
        payload={
            "status": "cancelled",
            "toolUseId": "cmd_cancelled",
            "toolName": "run_command",
            "command": "long-running",
        },
    )

    emitted_after_cancel = runtime.events[cancelled_event_count:]
    assert [event["type"] for event in emitted_after_cancel] == ["command.cancelled"]
    persisted_after_cancel = runtime.store.list_trace_events({"taskId": task["id"]})["traceEvents"][cancelled_trace_count:]
    assert persisted_after_cancel == []
    assert all(event["type"] != "message_complete" for event in runtime.events[cancelled_event_count:])
    assert all(event["type"] != "tool_result" for event in runtime.events[cancelled_event_count:])


def test_provider_stream_cancel_stops_late_message_persistence(tmp_path: Path) -> None:
    provider = CancellingStreamProvider()
    runtime = _make_runtime(tmp_path, provider)
    provider.runtime = runtime
    config = runtime.store.get_config({})["config"]
    config["provider"]["streamingEnabled"] = True
    config["provider"]["apiFormat"] = "openai-chat"
    runtime.store.update_config({"config": config})
    runtime.orchestrator._meta_router.route = lambda _goal, context=None: RoutingDecision(
        scenario=Scenario.SIMPLE_QUERY,
        strategy=ExecutionStrategy.REACT_FAST,
        confidence=0.95,
        max_steps=3,
        enable_reflection=False,
        enable_planning=False,
        reasoning="stream cancel regression",
    )
    session = _open_session(runtime, tmp_path)

    response = _rpc(runtime, "message.send", {"sessionId": session["id"], "content": "stream then stop"})

    task = response["result"]["task"]
    assert task["status"] == "cancelled"
    assistant_messages = [
        message
        for message in runtime.store.list_messages({"sessionId": session["id"]})["messages"]
        if message["role"] == "assistant" and message.get("taskId") == task["id"]
    ]
    provider_turns = runtime.store.list_provider_turns(task["id"])
    assert provider_turns[-1]["status"] == "cancelled"
    assert provider_turns[-1]["completed_at"] is not None
    assert assistant_messages
    assert "late after cancel" not in (assistant_messages[0].get("content") or "")
    assert "message.completed" not in _event_types(runtime)
    assert "message_complete" not in _event_types(runtime)
    assert not [
        event
        for event in runtime.events
        if event["type"] == "assistant.token"
        and event["payload"].get("delta") == "late after cancel "
    ]


def test_strict_swarm_plan_approval_uses_structured_preview_and_flat_sections(tmp_path: Path) -> None:
    subtasks = [
        {
            "id": "sub-0",
            "title": "Inspect routing decisions",
            "description": "Review how chat requests choose normal, plan, and swarm execution.",
            "dependencies": [],
            "agentType": "planner",
        },
        {
            "id": "sub-1",
            "title": "Repair replay ordering",
            "description": "Adjust live and replay event ordering for plan and team panels.",
            "dependencies": ["sub-0"],
            "agentType": "worker",
        },
    ]
    provider = ScriptedProvider([{"message": json.dumps(subtasks)}])
    runtime = _make_runtime(tmp_path, provider)
    _set_approval_mode(runtime, "strict")
    _force_route(runtime, scenario=Scenario.SWARM_TASK, strategy=ExecutionStrategy.PLAN_SWARM)
    session = _open_session(runtime, tmp_path)

    response = _rpc(runtime, "message.send", {"sessionId": session["id"], "content": "Use multiple agents to optimize output flow"})

    assert response["result"]["task"]["status"] == "waiting_approval"
    approval_events = [
        event for event in runtime.events
        if event["type"] == "approval.requested" and event["payload"].get("kind") == "plan"
    ]
    assert len(approval_events) == 1
    request = approval_events[0]["payload"]["request"]
    assert request["orchestrationMode"] == "swarm"
    assert request["subtaskCount"] == 2
    assert request["previewRows"]
    assert request["previewSections"][0]["kind"] == "items"
    assert request["previewSections"][0]["items"][0]["title"] == "Inspect routing decisions"
    assert request["subtasks"][1]["dependencies"] == ["sub-0"]

    flat_permissions = [event for event in runtime.events if event["type"] == "permission_request"]
    assert flat_permissions
    flat = flat_permissions[-1]
    assert flat["payload"]["toolName"] == "plan"
    assert flat["payload"]["previewSections"][0]["items"][1]["title"] == "Repair replay ordering"
    assert flat["yuanbao"]["type"] == "permission_request"
    assert flat["yuanbao"]["previewSections"][0]["items"][0]["title"] == "Inspect routing decisions"


def test_plan_approval_resolution_keeps_structured_preview_sections(tmp_path: Path) -> None:
    runtime = _make_runtime(tmp_path, ScriptedProvider([]))
    session = _open_session(runtime, tmp_path)
    task = runtime.store.create_task(
        session_id=session["id"],
        task_type="edit",
        goal="use several agents",
        plan=[],
        status="completed",
    )
    preview_sections = [
        {
            "kind": "items",
            "title": "Subtasks",
            "items": [
                {"id": "sub-0", "title": "Inspect routing", "description": "Check routing output."},
                {"id": "sub-1", "title": "Repair replay", "description": "Keep live and replay aligned."},
            ],
        }
    ]
    approval = runtime.store.create_approval(
        task["id"],
        "plan",
        {
            "goal": "use several agents",
            "orchestrationMode": "swarm",
            "subtaskCount": 2,
            "executionOrder": ["sub-0", "sub-1"],
            "previewSections": preview_sections,
        },
    )

    result = runtime.orchestrator.submit_approval({"approvalId": approval["id"], "decision": "approved"})

    assert result["ignored"] is True
    resolved = [event for event in runtime.events if event["type"] == "approval.resolved"][-1]
    assert resolved["payload"]["previewSections"] == preview_sections
    flat = [event for event in runtime.events if event["type"] == "permission_request"][-1]
    assert flat["payload"]["resolved"] is True
    assert flat["payload"]["previewSections"] == preview_sections
    persisted = runtime.store.list_trace_events({"taskId": task["id"]})["traceEvents"]
    persisted_resolved = [event for event in persisted if event["type"] == "approval.resolved"][-1]
    assert persisted_resolved["payload"]["previewSections"] == preview_sections


def test_cancelled_task_completion_review_submit_emits_ignored_resolution(tmp_path: Path) -> None:
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
        task["id"],
        "completion_review",
        {
            "summary": "Internal completion review",
            "structuredResult": {"status": "needs_more_work"},
        },
    )

    result = runtime.orchestrator.submit_approval({"approvalId": approval["id"], "decision": "approved"})

    assert result["ignored"] is True
    assert result["task"]["status"] == "cancelled"
    resolved = [event for event in runtime.events if event["type"] == "approval.resolved"][-1]
    assert resolved["payload"]["ignored"] is True
    assert resolved["payload"]["kind"] == "completion_review"
    assert resolved["payload"]["taskStatus"] == "cancelled"
    assert "yuanbao" not in resolved
    assert not [event for event in runtime.events if event["type"] == "permission_request"]
    persisted = runtime.store.list_trace_events({"taskId": task["id"]})["traceEvents"]
    persisted_resolved = [event for event in persisted if event["type"] == "approval.resolved"][-1]
    assert persisted_resolved["payload"]["ignored"] is True


def test_swarm_decomposition_failure_falls_back_to_original_request_not_fixed_template(tmp_path: Path) -> None:
    provider = ScriptedProvider(error=RuntimeError("planner unavailable"))
    runtime = _make_runtime(tmp_path, provider)
    _set_approval_mode(runtime, "strict")
    _force_route(runtime, scenario=Scenario.SWARM_TASK, strategy=ExecutionStrategy.PLAN_SWARM)
    session = _open_session(runtime, tmp_path)
    goal = "Use multiple agents to optimize the backend output flow"

    response = _rpc(runtime, "message.send", {"sessionId": session["id"], "content": goal})

    assert response["result"]["task"]["status"] == "waiting_approval"
    decomposed = [event for event in runtime.events if event["type"] == "task.planning.decomposed"]
    assert decomposed
    assert decomposed[-1]["payload"]["decompositionFallback"] is True
    assert decomposed[-1]["payload"]["decompositionFallbackReason"] == "provider_decomposition_failed"

    approval = [
        event for event in runtime.events
        if event["type"] == "approval.requested" and event["payload"].get("kind") == "plan"
    ][-1]
    request = approval["payload"]["request"]
    assert request["decompositionFallback"] is True
    assert request["subtaskCount"] == 1
    titles = [item["title"] for item in request["subtasks"]]
    assert titles == [goal[:80]]
    generic_titles = {"Analyze codebase", "Implement changes", "Verify results", "Summarize outcome and next steps"}
    assert not (set(titles) & generic_titles)


def test_non_strict_swarm_executes_with_ordered_panel_events_not_raw_plan_json(tmp_path: Path) -> None:
    plan = PlanResult(
        subtasks=[
            Subtask(
                id="sub-0",
                title="Inspect current flow",
                description="Inspect how backend events are emitted.",
                agent_type="planner",
            ),
            Subtask(
                id="sub-1",
                title="Patch event ordering",
                description="Patch the event ordering contract.",
                dependencies=["sub-0"],
                agent_type="worker",
            ),
        ],
        dag={"sub-0": [], "sub-1": ["sub-0"]},
        execution_order=["sub-0", "sub-1"],
    )
    runtime = _make_runtime(tmp_path, ScriptedProvider([]))
    _force_route(runtime, scenario=Scenario.SWARM_TASK, strategy=ExecutionStrategy.PLAN_SWARM)
    session = _open_session(runtime, tmp_path)
    runtime.orchestrator._decomposer.decompose = lambda **_kwargs: plan

    def _execute_swarm(*_args: Any, **kwargs: Any) -> OrchestrationResult:
        callback = kwargs["on_subtask_callback"]
        callback("sub-0", "started", {"subtaskId": "sub-0", "subtaskTitle": "Inspect current flow"})
        callback(
            "sub-0",
            "completed",
            {
                "subtaskId": "sub-0",
                "subtaskTitle": "Inspect current flow",
                "status": "completed",
                "summary": "Routing inspected.",
                "workerName": "Planner",
                "childTaskId": "ctask_plan",
            },
        )
        callback("sub-1", "started", {"subtaskId": "sub-1", "subtaskTitle": "Patch event ordering"})
        callback(
            "sub-1",
            "completed",
            {
                "subtaskId": "sub-1",
                "subtaskTitle": "Patch event ordering",
                "status": "completed",
                "summary": "Events patched.",
                "workerName": "Worker",
                "childTaskId": "ctask_worker",
            },
        )
        return OrchestrationResult(
            success=True,
            summary="Swarm execution finished with structured event panels.",
            subtask_results=[
                {"id": "sub-0", "title": "Inspect current flow", "status": "completed"},
                {"id": "sub-1", "title": "Patch event ordering", "status": "completed"},
            ],
            handoff_count=1,
            completed=["sub-0", "sub-1"],
            results={"sub-0": "Routing inspected.", "sub-1": "Events patched."},
        )

    runtime.orchestrator._swarm.execute = _execute_swarm

    response = _rpc(runtime, "message.send", {"sessionId": session["id"], "content": "Use multiple agents to optimize output flow"})

    assert response["result"]["task"]["status"] == "completed"
    assert not [event for event in runtime.events if event["type"] == "approval.requested"]
    types = _event_types(runtime)
    assert types.index("task.planning.started") < types.index("task.planning.decomposed")
    assert types.index("task.planning.decomposed") < types.index("task.planning.subtask.started")
    assert types.index("task.planning.subtask.completed") < types.index("task.planning.completed")
    assert types.index("task.planning.completed") < types.index("message.completed")
    assert "message_complete" in types

    subtask_events = [event for event in runtime.events if event["type"].startswith("task.planning.subtask.")]
    assert [event["payload"]["subtaskId"] for event in subtask_events] == ["sub-0", "sub-0", "sub-1", "sub-1"]
    assert subtask_events[1]["payload"]["childTaskId"] == "ctask_plan"
    progress_events = [event for event in runtime.events if event["type"] == "task.subtask.progress"]
    assert [event["payload"]["event"] for event in progress_events] == ["started", "completed", "started", "completed"]
    assert all(event["visibility"] == "panel" for event in progress_events)

    assistant_messages = [
        message for message in runtime.store.list_messages({"sessionId": session["id"], "limit": 20})["messages"]
        if message["role"] == "assistant"
    ]
    assert assistant_messages[-1]["content"] == "Swarm execution finished with structured event panels."
    raw_plan_fragments = ['"executionOrder"', '"subtasks"', '"dag"']
    assert not any(fragment in assistant_messages[-1]["content"] for fragment in raw_plan_fragments)


def test_planning_progress_is_visible_but_synthetic_thinking_stays_trace_only(tmp_path: Path) -> None:
    plan = PlanResult(
        subtasks=[
            Subtask(
                id="sub-0",
                title="Inspect current flow",
                description="Inspect how backend events are emitted.",
                agent_type="planner",
            ),
        ],
        dag={"sub-0": []},
        execution_order=["sub-0"],
    )
    runtime = _make_runtime(tmp_path, ScriptedProvider([]))
    _force_route(runtime, scenario=Scenario.SWARM_TASK, strategy=ExecutionStrategy.PLAN_SWARM)
    session = _open_session(runtime, tmp_path)
    runtime.orchestrator._decomposer.decompose = lambda **_kwargs: plan
    runtime.orchestrator._swarm.execute = lambda *_args, **_kwargs: OrchestrationResult(
        success=True,
        summary="Structured planning complete.",
        subtask_results=[{"id": "sub-0", "title": "Inspect current flow", "status": "completed"}],
        handoff_count=0,
        completed=["sub-0"],
        results={"sub-0": "done"},
    )

    response = _rpc(runtime, "message.send", {"sessionId": session["id"], "content": "Use multiple agents to inspect flow"})

    assert response["result"]["task"]["status"] == "completed"
    planning_progress = [
        event
        for event in runtime.events
        if event["type"] == "task.planning.progress"
        and event["payload"].get("mode") == "swarm"
    ]
    assert planning_progress
    assert {event["visibility"] for event in planning_progress} == {"panel"}
    assert not [event for event in runtime.events if event["type"] == "assistant_progress"]


def test_visible_tool_input_and_result_strip_internal_payload_fields(tmp_path: Path) -> None:
    runtime = _make_runtime(tmp_path, ScriptedProvider([]))
    session = _open_session(runtime, tmp_path)
    task = runtime.store.create_task(session_id=session["id"], task_type="chat", goal="tool contract", plan=[])
    task["role"] = "root"

    runtime.orchestrator._publish(
        session["id"],
        task,
        "tool.started",
        {
            "toolCallId": "call_custom",
            "toolName": "custom_tool",
            "arguments": {
                "query": "needle",
                "requestJson": '{"workspaceRoot":"D:/py/test_pro"}',
                "approval": {"id": "appr_1"},
                "workspaceRoot": "D:/py/test_pro",
            },
            "target": "custom_tool",
        },
    )
    runtime.orchestrator._publish(
        session["id"],
        task,
        "tool.completed",
        {
            "toolCallId": "call_custom",
            "toolName": "custom_tool",
            "result": {
                "status": "completed",
                "summary": "ok",
                "requestJson": '{"workspaceRoot":"D:/py/test_pro"}',
                "approval": {"id": "appr_1"},
                "workspaceRoot": "D:/py/test_pro",
                "diffText": "--- a/file\n+++ b/file",
            },
        },
    )

    tool_input = next(
        event["payload"]["input"]
        for event in runtime.events
        if event["type"] == "tool_use_complete" and event["payload"].get("toolUseId") == "call_custom"
    )
    tool_result = next(
        event["payload"]["content"]
        for event in runtime.events
        if event["type"] == "tool_result" and event["payload"].get("toolUseId") == "call_custom"
    )

    assert tool_input["query"] == "needle"
    encoded_input = json.dumps(tool_input, ensure_ascii=False)
    encoded_result = json.dumps(tool_result, ensure_ascii=False)
    for marker in ("requestJson", "workspaceRoot", '"approval"', "appr_1", "D:/py/test_pro"):
        assert marker not in encoded_input
        assert marker not in encoded_result
    assert tool_result["approvalStatus"] == "waiting"
    assert tool_result["approvalKind"] == "custom_tool"
    assert "diffText" not in encoded_result


def test_tool_activity_delta_omits_internal_json_logs(tmp_path: Path) -> None:
    runtime = _make_runtime(tmp_path, ScriptedProvider([]))
    session = _open_session(runtime, tmp_path)
    task = runtime.store.create_task(session_id=session["id"], task_type="chat", goal="tool logs", plan=[])
    task["role"] = "root"

    runtime.orchestrator._publish(
        session["id"],
        task,
        "tool.completed",
        {
            "toolCallId": "call_logs",
            "toolName": "custom_tool",
            "result": {
                "status": "completed",
                "logs": [
                    '{"requestJson":{"workspaceRoot":"D:/py/test_pro"}}',
                    {"label": "Inspect", "status": "done", "summary": "Checked public state."},
                ],
            },
        },
    )

    deltas = [
        event["payload"].get("toolOutput", "")
        for event in runtime.events
        if event["type"] == "content_delta" and event["payload"].get("toolUseId") == "call_logs"
    ]
    assert any("Checked public state." in delta for delta in deltas)
    assert not any("requestJson" in delta or "workspaceRoot" in delta for delta in deltas)


def test_run_command_permission_preview_does_not_fallback_to_workspace_root(tmp_path: Path) -> None:
    runtime = _make_runtime(tmp_path, ScriptedProvider([]))
    session = _open_session(runtime, tmp_path)
    task = runtime.store.create_task(session_id=session["id"], task_type="chat", goal="approval", plan=[])
    task["role"] = "root"

    runtime.orchestrator._publish(
        session["id"],
        task,
        "approval.requested",
        {
            "approvalId": "appr_cmd",
            "kind": "run_command",
            "request": {
                "command": "pytest -q",
                "workspaceRoot": "D:/py/test_pro",
                "shell": "powershell",
            },
            "preview": [
                {"label": "命令", "value": "pytest -q"},
                {"label": "目录", "value": "D:/py/test_pro"},
            ],
        },
    )

    flat = next(event for event in runtime.events if event["type"] == "permission_request")
    encoded = json.dumps(flat["payload"], ensure_ascii=False)
    assert "workspaceRoot" not in encoded
    assert "D:/py/test_pro" not in encoded


def test_computer_use_permission_request_strips_internal_request_fields(tmp_path: Path) -> None:
    runtime = _make_runtime(tmp_path, ScriptedProvider([]))
    session = _open_session(runtime, tmp_path)
    task = runtime.store.create_task(session_id=session["id"], task_type="chat", goal="computer use", plan=[])
    task["role"] = "root"

    runtime.orchestrator._publish(
        session["id"],
        task,
        "approval.requested",
        {
            "approvalId": "appr_screen",
            "kind": "computer_use",
            "request": {
                "action": "click",
                "target": "Submit",
                "x": 10,
                "y": 20,
                "requestJson": "{}",
                "workspaceRoot": "D:/py/test_pro",
            },
        },
    )

    flat = next(event for event in runtime.events if event["type"] == "computer_use_permission_request")
    encoded = json.dumps(flat["payload"], ensure_ascii=False)
    assert flat["payload"]["request"]["action"] == "click"
    assert flat["payload"]["target"] == "Submit"
    assert "requestJson" not in encoded
    assert "workspaceRoot" not in encoded
    assert "D:/py/test_pro" not in encoded
