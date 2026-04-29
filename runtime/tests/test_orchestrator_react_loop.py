from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace
from typing import Any

from local_agent_runtime.event_bus import EventBus
from local_agent_runtime.orchestrator.service import Orchestrator
from local_agent_runtime.provider.adapter import ProviderAdapter
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


def _call_result(response: dict[str, Any], key: str) -> dict[str, Any]:
    assert "result" in response, response
    return response["result"][key]


def _make_runtime(tmp_path: Any, provider: Any, tools: dict[str, Any] | None = None) -> SimpleNamespace:
    return _make_runtime_at_path(tmp_path / "runtime.sqlite3", provider, tools)


def _make_runtime_at_path(database_path: Any, provider: Any, tools: dict[str, Any] | None = None) -> SimpleNamespace:
    event_bus = EventBus()
    store = SQLiteStore(str(database_path))
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
    return SimpleNamespace(server=server, store=store, events=events)


def _make_builtin_runtime(tmp_path: Any, provider: Any) -> SimpleNamespace:
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
    assert [event["type"] for event in runtime.events if event["type"] == "assistant.message.completed"]
    assert not [event for event in runtime.events if event["type"] == "tool.started"]


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
    assert task["currentStep"] == "Inspect workspace"


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

    second_context = provider.calls[1]["context"]["messages"][-1]["content"]
    assert first_task["id"] != second_task["id"]
    assert "Task memory:" in second_context
    assert "add a focused project checklist" in second_context


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

    second_context = provider.calls[1]["context"]["messages"][-1]["content"]
    assert first_task["id"] != second_task["id"]
    assert "Project memory:" in second_context
    assert "define the product iteration direction" in second_context


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

    second_context = provider.calls[1]["context"]["messages"][-1]["content"]
    assert "Project memory:" not in second_context
    assert "define the product iteration direction" not in second_context


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

    first_context = provider.calls[0]["context"]["messages"][-1]["content"]
    started_event = next(event for event in runtime.events if event["type"] == "task.started")
    event_context = started_event["payload"]["context"]
    assert updated_workspace["focus"] == "Keep attention on durable context and long-running product work."
    assert "Project focus:" in first_context
    assert "durable context and long-running product work" in first_context
    assert event_context["projectFocus"] == "Keep attention on durable context and long-running product work."
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


def test_react_loop_fails_when_max_steps_are_exceeded(tmp_path: Any) -> None:
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
    runtime.store.update_config({"config": {"policy": {"maxTaskSteps": 1}}})
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
    assert task["errorCode"] == "LOOP_EXECUTION_FAILED"
    assert "maxTaskSteps" in task["resultSummary"]
    assert "tool.completed" in [event["type"] for event in runtime.events]


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
            }
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

    assert task["status"] == "failed"
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

    trace = _rpc(runtime, "trace.list", {"taskId": task["id"]})["result"]["traceEvents"]
    trace_types = [event["type"] for event in trace]
    assert "task.validation.completed" in trace_types
    validation_event = next(event for event in trace if event["type"] == "task.validation.completed")
    assert validation_event["payload"]["ran"] == ["git_status", "git_diff", "run_command"]
    assert validation_event["payload"]["command"]["command"] == "pytest runtime/tests/test_orchestrator_react_loop.py -k post_task_validation"
    assert validation_event["payload"]["patches"][0]["summary"] == "Updated todo.txt"
    assert validation_event["payload"]["verification"][-1]["status"] == "passed"
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
