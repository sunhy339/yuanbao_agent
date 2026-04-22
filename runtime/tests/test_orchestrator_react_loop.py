from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

from local_agent_runtime.event_bus import EventBus
from local_agent_runtime.orchestrator.service import Orchestrator
from local_agent_runtime.rpc.server import JsonRpcServer
from local_agent_runtime.store.sqlite_store import SQLiteStore
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
    event_bus = EventBus()
    store = SQLiteStore(str(tmp_path / "runtime.sqlite3"))
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
