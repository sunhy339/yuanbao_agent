from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import local_agent_runtime.services.worker_runner as worker_runner_module
from local_agent_runtime.event_bus import EventBus
from local_agent_runtime.orchestrator.service import Orchestrator
from local_agent_runtime.rpc.server import JsonRpcServer
from local_agent_runtime.services.collaboration_service import CollaborationService
from local_agent_runtime.services.worker_runner import ChildTaskRequest, WorkerRunner
from local_agent_runtime.store.sqlite_store import SQLiteStore
from local_agent_runtime.tools.registry import ToolRegistry


class _FakeTransport:
    def __init__(self, events: list[dict[str, Any]], response: dict[str, Any]) -> None:
        self._events = events
        self._response = response

    def __enter__(self) -> "_FakeTransport":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None

    def request(
        self,
        method: str,
        params: dict[str, Any],
        *,
        timeout: float,
        event_callback: Any | None = None,
    ) -> dict[str, Any]:
        assert method == "worker.run_child_task"
        assert timeout > 0
        assert params["sessionId"]
        if event_callback is not None:
            for event in self._events:
                event_callback(event)
        return self._response


class _FinalProvider:
    def __init__(self, final: str) -> None:
        self.final = final
        self.calls: list[dict[str, Any]] = []

    def generate(self, prompt: str, context: dict[str, Any]) -> dict[str, Any]:
        self.calls.append({"prompt": prompt, "context": context})
        return {"final": self.final}


class _WaitingApprovalTransport:
    def __init__(
        self,
        *,
        store: SQLiteStore,
        workspace_root: Path,
        approval_request: dict[str, Any],
    ) -> None:
        self._store = store
        self._workspace_root = workspace_root
        self._approval_request = approval_request
        self.requests: list[dict[str, Any]] = []
        self.child_runtime_task: dict[str, Any] | None = None
        self.approval: dict[str, Any] | None = None

    def __enter__(self) -> "_WaitingApprovalTransport":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None

    def request(
        self,
        method: str,
        params: dict[str, Any],
        *,
        timeout: float,
        event_callback: Any | None = None,
    ) -> dict[str, Any]:
        assert method == "worker.run_child_task"
        assert timeout > 0
        self.requests.append({"method": method, "params": deepcopy(params)})

        child_task = self._store.create_task(
            session_id=params["sessionId"],
            task_type="subagent",
            goal=params["prompt"],
            plan=[],
        )
        child_task = self._store.update_task_status(child_task["id"], "waiting_approval")
        approval = self._store.create_approval(
            task_id=child_task["id"],
            kind="run_command",
            request=self._approval_request,
        )
        self._store.upsert_pending_react_state(
            task_id=child_task["id"],
            session_id=params["sessionId"],
            goal=params["prompt"],
            context={
                "workspace_root": str(self._workspace_root),
                "workspace_name": self._workspace_root.name,
                "config": self._store.get_config({})["config"],
                "openai_tools": [],
            },
            messages=[
                {
                    "role": "assistant",
                    "content": "Need approval before running the child command.",
                    "tool_calls": [
                        {
                            "id": "call_child_command",
                            "name": "run_command",
                            "arguments": deepcopy(self._approval_request),
                        }
                    ],
                }
            ],
            tool_results=[],
            pending_tool_call={
                "id": "call_child_command",
                "name": "run_command",
                "arguments": deepcopy(self._approval_request),
            },
            pending_tool_spec={
                "id": "call_child_command",
                "name": "run_command",
                "arguments": deepcopy(self._approval_request),
                "plan_step_id": "run-command",
                "start_token": "Running approved child command",
            },
            remaining_tool_calls=[],
            steps=1,
            react_started=True,
        )

        approval_payload = {
            **deepcopy(approval),
            "approvalId": approval["id"],
            "request": deepcopy(self._approval_request),
        }
        if event_callback is not None:
            event_callback(
                {
                    "sessionId": params["sessionId"],
                    "taskId": child_task["id"],
                    "type": "approval.requested",
                    "payload": {
                        "approvalId": approval["id"],
                        "taskId": child_task["id"],
                        "kind": "run_command",
                        "request": deepcopy(self._approval_request),
                    },
                }
            )
        self.child_runtime_task = child_task
        self.approval = approval_payload
        return {
            "jsonrpc": "2.0",
            "id": "rpc_1",
            "result": {
                "status": "waiting_approval",
                "summary": "Child worker is waiting for parent approval.",
                "task": child_task,
                "approval": approval_payload,
                "budget": {},
            },
        }


def _rpc(server: JsonRpcServer, method: str, params: dict[str, Any]) -> dict[str, Any]:
    envelope = {
        "jsonrpc": "2.0",
        "id": f"req_{method}",
        "method": method,
        "params": params,
    }
    response = server.handle_line(json.dumps(envelope, ensure_ascii=False))
    assert response["jsonrpc"] == "2.0"
    assert response["id"] == envelope["id"]
    return response


def test_process_child_worker_approval_required_hands_off_via_collaboration_progress(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    store = SQLiteStore(str(tmp_path / "runtime.sqlite3"))
    event_bus = EventBus()
    events: list[dict[str, Any]] = []
    event_bus.subscribe(lambda event: events.append(event_bus.as_payload(event)))
    event_bus.subscribe(store.append_runtime_event)
    collaboration = CollaborationService(store, event_bus)
    runner = WorkerRunner(collaboration)

    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(parents=True, exist_ok=True)
    workspace = store.upsert_workspace(str(workspace_root))
    session = store.create_session(workspace_id=workspace["id"], title="bridge approval handoff")
    parent_task = store.create_task(session_id=session["id"], task_type="chat", goal="delegate", plan=[])

    child_approval = {
        "approvalId": "appr_child_1",
        "taskId": "task_child_runtime",
        "kind": "run_command",
        "request": {"command": "python -V", "cwd": "."},
    }
    fake_transport = _FakeTransport(
        events=[
            {
                "sessionId": session["id"],
                "taskId": "task_child_runtime",
                "type": "approval.requested",
                "payload": child_approval,
            }
        ],
        response={
            "jsonrpc": "2.0",
            "id": "rpc_1",
            "error": {
                "code": "CHILD_WORKER_APPROVAL_REQUIRED",
                "message": "Child worker is waiting for approval.",
                "retryable": False,
                "approval": child_approval,
            },
        },
    )

    monkeypatch.setattr(
        worker_runner_module.WorkerProcessTransport,
        "for_python_module",
        classmethod(lambda cls, *args, **kwargs: fake_transport),
    )

    try:
        response = runner.run_child_task(
            ChildTaskRequest(
                prompt="run child command",
                title="Run child command",
                agent_type="coder",
                session_id=session["id"],
                parent_runtime_task_id=parent_task["id"],
            )
        )

        assert response["status"] == "failed"
        assert response["error"]["code"] == "CHILD_WORKER_APPROVAL_REQUIRED"

        bridged = [
            event
            for event in events
            if event["type"] == "collab.task.updated"
            and isinstance(event["payload"], dict)
            and isinstance(event["payload"].get("_bridge"), dict)
        ]
        assert len(bridged) == 1
        assert bridged[0]["taskId"] == response["childTaskId"]
        assert bridged[0]["payload"]["task"]["status"] == "blocked"
        assert bridged[0]["payload"]["task"]["result"]["summary"] == "Waiting for approval"
        assert bridged[0]["payload"]["_bridge"]["source"] == "child-worker"
        assert bridged[0]["payload"]["_bridge"]["childTaskId"] == "task_child_runtime"
        assert bridged[0]["payload"]["_bridge"]["parentRuntimeTaskId"] == parent_task["id"]
        assert bridged[0]["payload"]["_bridge"]["childEvent"] == {
            "taskId": "task_child_runtime",
            "type": "approval.requested",
            "payload": child_approval,
        }

        parent_approval_events = [
            event
            for event in events
            if event["type"] == "approval.requested" and event["taskId"] == response["childTaskId"]
        ]
        assert parent_approval_events == []

        assert response["error"].get("approval") == child_approval
        assert response["task"]["error"].get("approval") == child_approval
        assert response["message"]["payload"]["error"].get("approval") == child_approval
    finally:
        store.close()


def test_parent_approval_submit_resumes_waiting_process_child_and_completes_collaboration(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    store = SQLiteStore(str(tmp_path / "runtime.sqlite3"))
    event_bus = EventBus()
    events: list[dict[str, Any]] = []
    event_bus.subscribe(lambda event: events.append(event_bus.as_payload(event)))
    collaboration = CollaborationService(store, event_bus)
    runner = WorkerRunner(collaboration)

    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(parents=True, exist_ok=True)
    workspace = store.upsert_workspace(str(workspace_root))
    session = store.create_session(workspace_id=workspace["id"], title="child approval resume")
    parent_task = store.create_task(session_id=session["id"], task_type="chat", goal="delegate", plan=[])

    approval_request = {"command": "Write-Output child-approved", "cwd": "."}
    transport = _WaitingApprovalTransport(
        store=store,
        workspace_root=workspace_root,
        approval_request=approval_request,
    )
    monkeypatch.setattr(
        worker_runner_module.WorkerProcessTransport,
        "for_python_module",
        classmethod(lambda cls, *args, **kwargs: transport),
    )

    executed_after_approval: list[dict[str, Any]] = []

    def run_command(params: dict[str, Any]) -> dict[str, Any]:
        executed_after_approval.append(deepcopy(params))
        return {
            "status": "completed",
            "stdout": "child-approved\n",
            "stderr": "",
            "exitCode": 0,
        }

    orchestrator = Orchestrator(
        store=store,
        event_bus=event_bus,
        tool_registry=ToolRegistry({"run_command": run_command}),
        provider=_FinalProvider("Child completed after approval."),
    )
    server = JsonRpcServer(orchestrator=orchestrator, store=store, event_bus=event_bus)

    try:
        response = runner.run_child_task(
            ChildTaskRequest(
                prompt="run child command",
                title="Run child command",
                agent_type="coder",
                session_id=session["id"],
                parent_runtime_task_id=parent_task["id"],
            )
        )
        assert response["status"] == "waiting_approval"
        assert response["task"]["status"] == "blocked"
        assert response["worker"]["status"] == "idle"
        assert response["worker"]["currentTaskId"] is None
        assert transport.approval is not None
        approval_id = transport.approval["id"]

        submit = _rpc(server, "approval.submit", {"approvalId": approval_id, "decision": "approved"})
        assert "result" in submit, submit

        child_runtime_task = store.get_task({"taskId": transport.child_runtime_task["id"]})["task"]
        child_collaboration_task = store.get_collaboration_task({"taskId": response["childTaskId"]})["task"]
        child_worker = store.get_agent_worker({"workerId": response["workerId"]})["worker"]

        assert executed_after_approval == [
            {
                **approval_request,
                "approvalId": approval_id,
                "taskId": transport.child_runtime_task["id"],
                "sessionId": session["id"],
            }
        ]
        assert child_runtime_task["status"] == "completed"
        assert child_runtime_task["resultSummary"] == "Child completed after approval."
        assert child_collaboration_task["status"] == "completed"
        assert child_collaboration_task["result"]["summary"] == "Child completed after approval."
        assert child_collaboration_task["result"]["runtimeTaskId"] == transport.child_runtime_task["id"]
        assert child_collaboration_task["result"]["runtimeTaskStatus"] == "completed"
        assert child_collaboration_task["result"]["approval"]["id"] == approval_id
        assert child_worker["status"] == "idle"
        assert child_worker["currentTaskId"] is None
        assert store.get_pending_react_state(transport.child_runtime_task["id"]) is None
        assert "approval.resolved" in [event["type"] for event in events]
        assert "collab.task.completed" in [event["type"] for event in events]
    finally:
        store.close()


def test_parent_approval_submit_fails_child_collaboration_when_resume_fails(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    store = SQLiteStore(str(tmp_path / "runtime.sqlite3"))
    event_bus = EventBus()
    collaboration = CollaborationService(store, event_bus)
    runner = WorkerRunner(collaboration)

    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(parents=True, exist_ok=True)
    workspace = store.upsert_workspace(str(workspace_root))
    session = store.create_session(workspace_id=workspace["id"], title="child approval resume failure")
    parent_task = store.create_task(session_id=session["id"], task_type="chat", goal="delegate", plan=[])

    approval_request = {"command": "Write-Output child-fails", "cwd": "."}
    transport = _WaitingApprovalTransport(
        store=store,
        workspace_root=workspace_root,
        approval_request=approval_request,
    )
    monkeypatch.setattr(
        worker_runner_module.WorkerProcessTransport,
        "for_python_module",
        classmethod(lambda cls, *args, **kwargs: transport),
    )

    executed_after_approval: list[str] = []

    def run_command(params: dict[str, Any]) -> dict[str, Any]:
        executed_after_approval.append(params["approvalId"])
        raise RuntimeError("child command failed after approval")

    orchestrator = Orchestrator(
        store=store,
        event_bus=event_bus,
        tool_registry=ToolRegistry({"run_command": run_command}),
        provider=_FinalProvider("This final answer should not be used."),
    )
    server = JsonRpcServer(orchestrator=orchestrator, store=store, event_bus=event_bus)

    try:
        response = runner.run_child_task(
            ChildTaskRequest(
                prompt="run failing child command",
                title="Run failing child command",
                agent_type="coder",
                session_id=session["id"],
                parent_runtime_task_id=parent_task["id"],
            )
        )
        assert response["status"] == "waiting_approval"
        assert response["task"]["status"] == "blocked"
        assert transport.approval is not None
        approval_id = transport.approval["id"]

        submit = _rpc(server, "approval.submit", {"approvalId": approval_id, "decision": "approved"})
        assert "result" in submit, submit

        child_runtime_task = store.get_task({"taskId": transport.child_runtime_task["id"]})["task"]
        child_collaboration_task = store.get_collaboration_task({"taskId": response["childTaskId"]})["task"]
        child_worker = store.get_agent_worker({"workerId": response["workerId"]})["worker"]

        assert executed_after_approval == [approval_id]
        assert child_runtime_task["status"] == "failed"
        assert child_runtime_task["errorCode"] == "LOOP_EXECUTION_FAILED"
        assert child_collaboration_task["status"] == "failed"
        assert child_collaboration_task["error"]["code"] == "LOOP_EXECUTION_FAILED"
        assert child_collaboration_task["error"]["approval"]["id"] == approval_id
        assert child_collaboration_task["error"]["runtimeTaskId"] == transport.child_runtime_task["id"]
        assert child_worker["status"] == "failed"
        assert child_worker["currentTaskId"] is None
        assert store.get_pending_react_state(transport.child_runtime_task["id"]) is None
    finally:
        store.close()
