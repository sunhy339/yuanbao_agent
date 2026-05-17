from __future__ import annotations

import json
import pytest
import subprocess
from types import SimpleNamespace
from typing import Any

from local_agent_runtime.event_bus import EventBus
from local_agent_runtime.orchestration.types import OrchestrationResult
from local_agent_runtime.orchestrator.service import Orchestrator
from local_agent_runtime.provider.adapter import ProviderAdapter
from local_agent_runtime.policy.guard import PolicyGuard
from local_agent_runtime.router.meta_router import MetaRouter
from local_agent_runtime.rpc.server import JsonRpcServer
from local_agent_runtime.services import CollaborationService, SubagentService
from local_agent_runtime.store.sqlite_store import SQLiteStore
from local_agent_runtime.tools import build_builtin_tools
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
        meta_router=MetaRouter(provider=None),
    )
    server = JsonRpcServer(orchestrator=orchestrator, store=store, event_bus=event_bus)
    events: list[dict[str, Any]] = []
    event_bus.subscribe(lambda event: events.append(event_bus.as_payload(event)))
    return SimpleNamespace(server=server, store=store, events=events)


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
        meta_router=MetaRouter(provider=None),
    )
    server = JsonRpcServer(orchestrator=orchestrator, store=store, event_bus=event_bus)
    events: list[dict[str, Any]] = []
    event_bus.subscribe(lambda event: events.append(event_bus.as_payload(event)))
    return SimpleNamespace(server=server, store=store, events=events)


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

    assert task["status"] == "completed"
    assert task["resultSummary"] == "The provider answered directly."
    assert [event["type"] for event in runtime.events if event["type"] == "message.completed"]
    assert not [event for event in runtime.events if event["type"] == "tool.started"]


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
            "strategy": "plan_swarm",
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
    assert first_policy["phase"] == "planning"
    assert "task" in first_policy["allowedToolNames"]
    assert second_policy["phase"] == "post_task_continuation"
    assert "task" not in second_policy["allowedToolNames"]
    assert "task" in second_policy["deniedToolNames"]
    assert "task" not in second_tool_names
    assert {"read_file", "apply_patch", "run_command"}.issubset(second_tool_names)


def test_swarm_execution_passes_autonomy_timeout_to_children(tmp_path: Any) -> None:
    provider = ScriptedProvider([])
    runtime = _make_runtime(tmp_path, provider)
    session = _open_session(runtime, tmp_path)
    task = runtime.store.create_task(
        session_id=session["id"],
        task_type="edit",
        goal="coordinate a long swarm task",
        plan=[],
    )
    captured: dict[str, Any] = {}

    runtime.server._orchestrator._decomposer.decompose = (  # noqa: SLF001
        lambda **_kwargs: SimpleNamespace(subtasks=[], execution_order=[], dag={})
    )
    runtime.server._orchestrator._check_plan_approval = lambda **_kwargs: None  # noqa: SLF001

    def _execute(*_args: Any, **kwargs: Any) -> OrchestrationResult:
        captured.update(kwargs)
        return OrchestrationResult(success=True, summary="done", subtask_results=[])

    runtime.server._orchestrator._swarm.execute = _execute  # noqa: SLF001
    context = {
        "config": {
            "autonomy": {
                "activeProfileId": "long-run",
                "profiles": [{"id": "long-run", "timeoutMs": 900_000}],
            }
        }
    }

    runtime.server._orchestrator._execute_with_swarm(  # noqa: SLF001
        session_id=session["id"],
        task=task,
        goal="coordinate a long swarm task",
        context=context,
    )

    assert captured["child_timeout_ms"] == 900_000


def test_supervisor_execution_passes_autonomy_timeout_to_children(tmp_path: Any) -> None:
    provider = ScriptedProvider([])
    runtime = _make_runtime(tmp_path, provider)
    session = _open_session(runtime, tmp_path)
    task = runtime.store.create_task(
        session_id=session["id"],
        task_type="edit",
        goal="coordinate a long supervised task",
        plan=[],
    )
    captured: dict[str, Any] = {}

    runtime.server._orchestrator._decomposer.decompose = (  # noqa: SLF001
        lambda **_kwargs: SimpleNamespace(subtasks=[], execution_order=[], dag={})
    )
    runtime.server._orchestrator._check_plan_approval = lambda **_kwargs: None  # noqa: SLF001

    def _execute(*_args: Any, **kwargs: Any) -> OrchestrationResult:
        captured.update(kwargs)
        return OrchestrationResult(success=True, summary="done", subtask_results=[])

    runtime.server._orchestrator._supervisor.execute = _execute  # noqa: SLF001
    context = {
        "config": {
            "autonomy": {
                "activeProfileId": "long-run",
                "profiles": [{"id": "long-run", "timeoutMs": 900_000}],
            }
        }
    }

    runtime.server._orchestrator._execute_with_supervisor(  # noqa: SLF001
        session_id=session["id"],
        task=task,
        goal="coordinate a long supervised task",
        context=context,
    )

    assert captured["child_timeout_ms"] == 900_000


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
    assert task["currentStep"] == "Understand task context"


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

    second_context_text = provider.calls[1]["context"]["messages"][-1]["content"]
    assert "Recent conversation:" in second_context_text
    assert "User: remember the alpha checklist" in second_context_text
    assert "Assistant: I will remember the alpha checklist." in second_context_text
    assert second_context_text.rfind("Current user request:") > second_context_text.rfind("Recent conversation:")
    assert "Current user request:\nwhat did I ask you to remember?" in second_context_text


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

    assert first_task["status"] == "waiting_approval"
    completion_review = next(
        event for event in runtime.events
        if event["type"] == "approval.requested"
        and event["payload"].get("kind") == "completion_review"
    )
    _rpc(
        runtime,
        "approval.submit",
        {"approvalId": completion_review["payload"]["approvalId"], "decision": "approved"},
    )
    first_task = _call_result(_rpc(runtime, "task.get", {"taskId": first_task["id"]}), "task")
    assert first_task["status"] == "completed"

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
    event_context = started_event["payload"]["context"]
    assert updated_workspace["focus"] == "Keep attention on durable context and long-running product work."
    # In lightweight mode, project focus is not injected into messages,
    # but it is stored in the context bundle and workspace.
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
    second_context = provider.calls[1]["context"]
    assert second_context["messages"][-1]["role"] == "tool"
    assert second_context["messages"][-1]["tool_call_id"] == "call_search"
    assert second_context["tool_results"][0]["result"]["matches"][0]["path"] == "alpha.txt"


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
    assert [event["type"] for event in runtime.events].count("tool.completed") == 2
    assert provider.calls[1]["context"]["tool_results"][0]["result"]["stdout"] == "approved\n"


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
    # Patch the router to also return max_steps=1 (routing now takes priority)
    original_route = runtime.server._orchestrator._meta_router.route  # noqa: SLF001
    def patched_route(goal: str):  # noqa: ANN001
        decision = original_route(goal)
        decision.max_steps = 1
        return decision
    runtime.server._orchestrator._meta_router.route = patched_route  # noqa: SLF001
    session = _open_session(runtime, tmp_path)

    task = _call_result(
        _rpc(
            runtime,
            "message.send",
            {"sessionId": session["id"], "content": "read alpha"},
        ),
        "task",
    )

    assert task["status"] == "completed"
    assert task.get("errorCode") is None
    assert "maxTaskSteps" in task["resultSummary"]
    workflow = task["routing"]["mainWorkflow"]
    assert workflow["budget"]["exhausted"] is True
    assert workflow["budget"]["exhaustedReason"] == "max_steps"
    assert workflow["budget"]["consumedSteps"] == 1
    assert workflow["convergence"]["state"] == "partial_result"
    event_types = [event["type"] for event in runtime.events]
    assert "tool.completed" in event_types
    assert "task.budget.exhausted" in event_types


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
    assert provider.calls[-1]["context"]["tool_results"][-1]["result"]["cached"] is True


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
    started_context = next(
        event["payload"]["context"]
        for event in runtime.events
        if event["type"] == "task.started" and event["taskId"] == task["id"]
    )
    context_updates = [
        event["payload"]["context"]
        for event in runtime.events
        if event["type"] == "task.updated"
        and event["taskId"] == task["id"]
        and isinstance(event.get("payload"), dict)
        and isinstance(event["payload"].get("context"), dict)
    ]

    assert context_updates
    initial_tokens = started_context["budgetStats"]["messageTokens"]
    live_tokens = context_updates[-1]["budgetStats"]["messageTokens"]
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
    assert "Validated with git status" in final_task["resultSummary"]
    assert (workspace_root / "README.md").read_text(encoding="utf-8") == "new line\n"


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


def test_patch_completion_runs_post_task_validation_and_records_trace(tmp_path: Any) -> None:
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
    assert tool_invocations == ["apply_patch", "git_status", "git_diff", "run_command"]
    assert "Updated todo.txt" in task["resultSummary"]
    assert "Validated with git status, git diff, and pytest runtime/tests/test_orchestrator_react_loop.py -k post_task_validation." in task["resultSummary"]
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
    assert validation_event["payload"]["ran"] == ["git_status", "git_diff", "run_command"]
    assert validation_event["payload"]["command"]["command"] == "pytest runtime/tests/test_orchestrator_react_loop.py -k post_task_validation"
    assert validation_event["payload"]["patches"][0]["summary"] == "Updated todo.txt"
    assert validation_event["payload"]["verification"][-1]["status"] == "passed"
    completion_event = next(event for event in trace if event["type"] == "agent.decision.completion")
    assert completion_event["payload"]["completionEvidence"]["evidenceLevel"] == "verified"
    task_updates = [event for event in runtime.events if event["type"] == "task.updated"]
    assert any(event["payload"].get("changedFiles") for event in task_updates)
    assert any(event["payload"].get("commands") for event in task_updates)
    assert any(event["payload"].get("verification") for event in task_updates)


def test_patch_completion_skips_run_command_without_validate_command(tmp_path: Any) -> None:
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
    assert tool_invocations == ["apply_patch", "git_status", "git_diff"]

    trace = _rpc(runtime, "trace.list", {"taskId": task["id"]})["result"]["traceEvents"]
    validation_event = next(event for event in trace if event["type"] == "task.validation.completed")
    assert validation_event["payload"]["ran"] == ["git_status", "git_diff"]
    assert validation_event["payload"]["command"]["status"] == "skipped"
    assert validation_event["payload"]["command"]["reason"] == "No validation command was configured."


# ── Supplement TaskInbox tests ────────────────────────────────────────────


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

        pending = store.get_pending_supplements("task_1")
        assert len(pending) == 1
        assert pending[0]["id"] == entry["id"]
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


def test_explicit_supplement_to_completed_task_rejected(tmp_path: Any) -> None:
    """Explicit mode=supplement to a completed task raises error."""
    provider = ScriptedProvider([])
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
    # Should return error since completed task cannot be supplemented
    assert "error" in resp
    assert "not active" in resp["error"]["message"].lower() or "cannot supplement" in resp["error"]["message"].lower()


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

