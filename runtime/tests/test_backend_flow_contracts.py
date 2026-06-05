from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from local_agent_runtime.event_bus import EventBus
from local_agent_runtime.orchestration.types import OrchestrationResult
from local_agent_runtime.orchestrator.service import Orchestrator
from local_agent_runtime.planner.types import PlanResult, Subtask
from local_agent_runtime.router.meta_router import MetaRouter
from local_agent_runtime.router.types import ExecutionStrategy, RoutingDecision, Scenario
from local_agent_runtime.rpc.server import JsonRpcServer
from local_agent_runtime.store.sqlite_store import SQLiteStore
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
    )


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
    assert not [
        event for event in runtime.events
        if event["type"] == "assistant_progress"
        and event["payload"].get("phase") == "workspace_evidence_required"
    ]
    assert len(provider.calls) == 1


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
        lambda event: event["type"] == "content_delta"
        and event["payload"].get("text") == "Found needle in alpha.txt."
    )
    message_complete = index_of(lambda event: event["type"] == "message_complete")

    assert first_thinking < tool_start < tool_result < second_thinking < final_delta < message_complete
    assert all(event["visibility"] == "chat" for event in events if event["type"] in {"thinking", "content_start", "tool_result", "content_delta", "message_complete"})


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
    runtime = _make_runtime(tmp_path, provider)
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
    goal_events = [event for event in runtime.events if event["type"] == "goal_event" and event["payload"].get("action") == "failed"]
    assert len(goal_events) == 1
    assert goal_events[0]["visibility"] == "panel"
    assert not [event for event in runtime.events if event["type"] == "memory_event"]
    assert not [event for event in runtime.events if event["type"] == "approval.requested"]
    assert not [event for event in runtime.events if event["type"] == "completion_review"]


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
        ("assistant_progress", {"summary": "late progress"}),
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
    planning_thinking = [
        event
        for event in runtime.events
        if event["type"] == "thinking"
        and str(event["payload"].get("source") or "").startswith("swarm_")
    ]
    assert planning_thinking
    assert {event["visibility"] for event in planning_thinking} == {"trace"}
    assert all(event["payload"].get("_bridge", {}).get("suppressRealtimeFlat") is True for event in planning_thinking)

    visible_progress = [
        event
        for event in runtime.events
        if event["type"] == "assistant_progress"
        and event["payload"].get("mode") == "swarm"
    ]
    assert visible_progress
    assert {event["visibility"] for event in visible_progress} == {"chat"}
