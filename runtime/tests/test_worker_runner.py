from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import local_agent_runtime.services.worker_runner as worker_runner_module
from local_agent_runtime.event_bus import EventBus
from local_agent_runtime.services.collaboration_service import CollaborationService
from local_agent_runtime.services.subagent_service import SubagentService
from local_agent_runtime.services.worker_runner import ChildTaskRequest, WorkerRunner
from local_agent_runtime.store.sqlite_store import SQLiteStore


def _runner_context(tmp_path: Path, executor: Any | None = None) -> tuple[SQLiteStore, WorkerRunner, dict[str, Any]]:
    store = SQLiteStore(str(tmp_path / "runtime.sqlite3"))
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(parents=True, exist_ok=True)
    workspace = store.upsert_workspace(str(workspace_root))
    session = store.create_session(workspace_id=workspace["id"], title="child runner")
    parent_task = store.create_task(session_id=session["id"], task_type="chat", goal="parent", plan=[])
    event_bus = EventBus()
    collaboration = CollaborationService(store, event_bus)
    return store, WorkerRunner(collaboration, executor=executor), {"session": session, "parent_task": parent_task}


class RecordingRunner:
    def __init__(self) -> None:
        self.requests: list[ChildTaskRequest] = []

    def run_child_task(self, request: ChildTaskRequest) -> dict[str, Any]:
        self.requests.append(request)
        return {
            "status": "completed",
            "childTaskId": "ctask_child",
            "workerId": "agent_child",
            "result": {"summary": "done"},
            "subagent": {
                "agentType": request.agent_type,
                "executionMode": "inline-skeleton",
            },
            "task": {"id": "ctask_child"},
            "worker": {"id": "agent_child"},
            "message": {"id": "msg_child"},
            "summary": "done",
        }


def test_subagent_service_dispatch_forwards_normalized_request_to_runner() -> None:
    runner = RecordingRunner()
    service = SubagentService(object(), object(), runner=runner)

    response = service.dispatch(
        {
            "prompt": "  Inspect runtime child execution  ",
            "agentType": "coder",
            "title": "  ",
            "sessionId": "sess_123",
            "taskId": "task_123",
            "priority": "4",
            "timeoutMs": "120000",
            "retry": {"maxAttempts": 3, "backoff": "exponential"},
            "cancellation": {"reason": "manual"},
            "budget": {"maxTokens": 256},
        }
    )

    assert runner.requests == [
        ChildTaskRequest(
            prompt="Inspect runtime child execution",
            title="Inspect runtime child execution",
            agent_type="coder",
            priority=4,
            session_id="sess_123",
            parent_runtime_task_id="task_123",
            timeout_ms=120000,
            retry={"maxAttempts": 3, "backoff": "exponential"},
            cancellation={"reason": "manual"},
            budget={"maxTokens": 256},
        )
    ]
    assert response["childTaskId"] == "ctask_child"
    assert response["subagent"]["executionMode"] == "inline-skeleton"


def test_subagent_service_dispatch_accepts_prompt_only() -> None:
    runner = RecordingRunner()
    service = SubagentService(object(), object(), runner=runner)

    response = service.dispatch({"prompt": "Inspect runtime child execution"})

    assert runner.requests == [
        ChildTaskRequest(
            prompt="Inspect runtime child execution",
            title="Inspect runtime child execution",
        )
    ]
    assert response["childTaskId"] == "ctask_child"


def test_subagent_service_dispatch_normalizes_child_tool_allowlist_into_budget() -> None:
    runner = RecordingRunner()
    service = SubagentService(object(), object(), runner=runner)

    service.dispatch(
        {
            "prompt": "Run focused child tests",
            "budget": {"maxTokens": 256},
            "child_tool_allowlist": [" read_file ", "run_command", "apply_patch", "read_file"],
        }
    )

    assert runner.requests == [
        ChildTaskRequest(
            prompt="Run focused child tests",
            title="Run focused child tests",
            budget={
                "maxTokens": 256,
                "childToolAllowlist": ["read_file", "run_command", "apply_patch"],
            },
        )
    ]


def test_worker_runner_threads_metadata_into_child_task_records(tmp_path: Path) -> None:
    store, runner, records = _runner_context(tmp_path)
    try:
        response = runner.run_child_task(
            ChildTaskRequest(
                prompt="Inspect the runtime slice",
                title="Inspect runtime slice",
                agent_type="analyst",
                priority=2,
                session_id=records["session"]["id"],
                parent_runtime_task_id=records["parent_task"]["id"],
                cancellation={"reason": "manual"},
                budget={"maxTokens": 256, "remainingTokens": 192},
            )
        )

        task = store.get_collaboration_task({"taskId": response["childTaskId"]})["task"]
        worker = store.get_agent_worker({"workerId": response["workerId"]})["worker"]

        assert response["status"] == "completed"
        assert response["subagent"] == {
            "agentType": "analyst",
            "executionMode": "process-rpc",
        }
        assert task["status"] == "completed"
        assert task["metadata"]["parentRuntimeTaskId"] == records["parent_task"]["id"]
        assert task["metadata"]["cancellation"] == {"reason": "manual"}
        assert task["metadata"]["budget"] == {"maxTokens": 256, "remainingTokens": 192}
        assert worker["metadata"]["mode"] == "process-rpc"
        assert worker["metadata"]["cancellation"] == {"reason": "manual"}
        assert worker["metadata"]["budget"] == {"maxTokens": 256, "remainingTokens": 192}
    finally:
        store.close()


def test_worker_runner_uses_injected_executor_and_completes_child_task(tmp_path: Path) -> None:
    def executor(context: Any) -> dict[str, Any]:
        assert context.task["status"] == "running"
        assert context.worker["status"] == "busy"
        return {
            "summary": f"handled {context.request.prompt}",
            "executionMode": "test-executor",
            "result": {"detail": "custom result"},
        }

    store, runner, records = _runner_context(tmp_path, executor=executor)
    try:
        response = runner.run_child_task(
            ChildTaskRequest(
                prompt="executor boundary",
                title="Executor boundary",
                agent_type="coder",
                session_id=records["session"]["id"],
                parent_runtime_task_id=records["parent_task"]["id"],
            )
        )

        task = store.get_collaboration_task({"taskId": response["childTaskId"]})["task"]
        worker = store.get_agent_worker({"workerId": response["workerId"]})["worker"]

        assert response["status"] == "completed"
        assert response["summary"] == "handled executor boundary"
        assert response["result"] == {
            "summary": "handled executor boundary",
            "agentType": "coder",
            "executionMode": "test-executor",
            "detail": "custom result",
            "attempts": 1,
        }
        assert response["subagent"] == {"agentType": "coder", "executionMode": "test-executor"}
        assert task["status"] == "completed"
        assert worker["status"] == "idle"
        assert worker["currentTaskId"] is None
        assert response["message"]["body"] == "handled executor boundary"
    finally:
        store.close()


def test_worker_runner_fails_child_task_and_cleans_worker_when_executor_raises(tmp_path: Path) -> None:
    def executor(_context: Any) -> dict[str, Any]:
        raise RuntimeError("model loop exploded")

    store, runner, records = _runner_context(tmp_path, executor=executor)
    try:
        response = runner.run_child_task(
            ChildTaskRequest(
                prompt="explode",
                title="Explode",
                agent_type="coder",
                session_id=records["session"]["id"],
                parent_runtime_task_id=records["parent_task"]["id"],
            )
        )

        task = store.get_collaboration_task({"taskId": response["childTaskId"]})["task"]
        worker = store.get_agent_worker({"workerId": response["workerId"]})["worker"]

        assert response["status"] == "failed"
        assert response["error"]["code"] == "CHILD_TASK_EXECUTION_FAILED"
        assert response["error"]["message"] == "model loop exploded"
        assert response["error"]["type"] == "RuntimeError"
        assert task["status"] == "failed"
        assert task["error"] == response["error"]
        assert worker["status"] == "failed"
        assert worker["currentTaskId"] is None
        assert response["message"]["payload"]["error"] == response["error"]
    finally:
        store.close()


def test_worker_runner_retries_retryable_executor_errors(tmp_path: Path) -> None:
    attempts: list[int] = []

    def executor(context: Any) -> dict[str, Any]:
        attempts.append(context.attempt_number)
        if context.attempt_number == 1:
            raise TimeoutError("temporary timeout")
        assert not context.cancellation_event.is_set()
        return {
            "summary": "recovered on retry",
            "executionMode": "test-executor",
        }

    store, runner, records = _runner_context(tmp_path, executor=executor)
    try:
        response = runner.run_child_task(
            ChildTaskRequest(
                prompt="retry",
                title="Retry",
                agent_type="coder",
                session_id=records["session"]["id"],
                parent_runtime_task_id=records["parent_task"]["id"],
                retry={"maxAttempts": 2},
            )
        )

        task = store.get_collaboration_task({"taskId": response["childTaskId"]})["task"]

        assert attempts == [1, 2]
        assert response["status"] == "completed"
        assert response["result"]["summary"] == "recovered on retry"
        assert response["result"]["attempts"] == 2
        assert task["result"]["attempts"] == 2
    finally:
        store.close()


def test_worker_runner_times_out_executor_from_request_metadata(tmp_path: Path) -> None:
    def executor(_context: Any) -> dict[str, Any]:
        time.sleep(0.05)
        return {"summary": "too late"}

    store, runner, records = _runner_context(tmp_path, executor=executor)
    try:
        response = runner.run_child_task(
            ChildTaskRequest(
                prompt="slow",
                title="Slow",
                agent_type="coder",
                session_id=records["session"]["id"],
                parent_runtime_task_id=records["parent_task"]["id"],
                budget={"timeoutSeconds": 0.001},
            )
        )

        task = store.get_collaboration_task({"taskId": response["childTaskId"]})["task"]
        worker = store.get_agent_worker({"workerId": response["workerId"]})["worker"]

        assert response["status"] == "failed"
        assert response["error"]["code"] == "CHILD_TASK_TIMEOUT"
        assert "timed out" in response["error"]["message"]
        assert task["status"] == "failed"
        assert worker["status"] == "failed"
        assert worker["currentTaskId"] is None
    finally:
        store.close()


def test_worker_runner_enforces_total_timeout_across_retries(tmp_path: Path) -> None:
    attempts: list[int] = []

    def executor(context: Any) -> dict[str, Any]:
        attempts.append(context.attempt_number)
        time.sleep(0.05)
        return {"summary": "too late"}

    store, runner, records = _runner_context(tmp_path, executor=executor)
    try:
        response = runner.run_child_task(
            ChildTaskRequest(
                prompt="total timeout",
                title="Total timeout",
                agent_type="coder",
                session_id=records["session"]["id"],
                parent_runtime_task_id=records["parent_task"]["id"],
                retry={"maxAttempts": 2},
                budget={"totalTimeoutSeconds": 0.001},
            )
        )

        assert attempts == [1]
        assert response["status"] == "failed"
        assert response["error"]["code"] == "CHILD_TASK_TIMEOUT"
        assert response["error"]["attempts"] == 2
    finally:
        store.close()


class _FakeTransport:
    def __init__(self, events: list[dict[str, Any]], response: dict[str, Any]) -> None:
        self._events = events
        self._response = response

    def __enter__(self) -> _FakeTransport:
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


def test_worker_runner_file_backed_session_uses_process_rpc(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    store, runner, records = _runner_context(tmp_path)
    fake_transport = _FakeTransport(
        events=[],
        response={
            "jsonrpc": "2.0",
            "id": "rpc_1",
            "result": {
                "status": "completed",
                "summary": "child finished through process rpc",
                "task": {"id": "task_child_runtime", "status": "completed"},
                "budget": {"maxTokens": 40, "remainingTokens": 32},
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
                prompt="run in a child process",
                title="Run in a child process",
                agent_type="coder",
                session_id=records["session"]["id"],
                parent_runtime_task_id=records["parent_task"]["id"],
            )
        )

        task = store.get_collaboration_task({"taskId": response["childTaskId"]})["task"]
        worker = store.get_agent_worker({"workerId": response["workerId"]})["worker"]

        assert response["status"] == "completed"
        assert response["summary"] == "child finished through process rpc"
        assert response["subagent"] == {"agentType": "coder", "executionMode": "process-rpc"}
        assert response["result"]["runtimeTaskId"] == "task_child_runtime"
        assert response["result"]["runtimeTaskStatus"] == "completed"
        assert response["result"]["budget"] == {"maxTokens": 40, "remainingTokens": 32}
        assert task["metadata"]["executionMode"] == "process-rpc"
        assert worker["metadata"]["mode"] == "process-rpc"
    finally:
        store.close()


def test_worker_runner_missing_session_id_fails_process_required_without_inline_fallback(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    store, runner, _records = _runner_context(tmp_path)
    monkeypatch.setattr(
        worker_runner_module.WorkerProcessTransport,
        "for_python_module",
        classmethod(
            lambda cls, *args, **kwargs: (_ for _ in ()).throw(
                AssertionError("missing sessionId must fail before spawning a child process")
            )
        ),
    )

    try:
        response = runner.run_child_task(
            ChildTaskRequest(
                prompt="missing session",
                title="Missing session",
                agent_type="coder",
            )
        )

        assert response["status"] == "failed"
        assert response["error"]["code"] == "CHILD_WORKER_PROCESS_REQUIRED"
        assert response["error"]["type"] == "ChildTaskProcessRequiredError"
        assert "sessionId" in response["error"]["message"]
        assert "file-backed runtime database" in response["error"]["message"]
        assert response["summary"] == response["error"]["message"]
        assert response["task"]["status"] == "failed"
        assert response["task"]["error"] == response["error"]
        assert response["message"]["payload"]["error"] == response["error"]
    finally:
        store.close()


def test_worker_runner_unshareable_database_fails_process_required_without_inline_fallback(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    store = SQLiteStore(":memory:")
    event_bus = EventBus()
    collaboration = CollaborationService(store, event_bus)
    runner = WorkerRunner(collaboration)
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(parents=True, exist_ok=True)
    workspace = store.upsert_workspace(str(workspace_root))
    session = store.create_session(workspace_id=workspace["id"], title="memory database child")
    parent_task = store.create_task(session_id=session["id"], task_type="chat", goal="parent", plan=[])
    monkeypatch.setattr(
        worker_runner_module.WorkerProcessTransport,
        "for_python_module",
        classmethod(
            lambda cls, *args, **kwargs: (_ for _ in ()).throw(
                AssertionError(":memory: database must fail before spawning a child process")
            )
        ),
    )

    try:
        response = runner.run_child_task(
            ChildTaskRequest(
                prompt="memory database",
                title="Memory database",
                agent_type="coder",
                session_id=session["id"],
                parent_runtime_task_id=parent_task["id"],
            )
        )

        assert response["status"] == "failed"
        assert response["error"]["code"] == "CHILD_WORKER_PROCESS_REQUIRED"
        assert response["error"]["type"] == "ChildTaskProcessRequiredError"
        assert "sessionId" in response["error"]["message"]
        assert "file-backed runtime database" in response["error"]["message"]
        assert response["summary"] == response["error"]["message"]
        assert response["task"]["status"] == "failed"
        assert response["task"]["error"] == response["error"]
        assert response["message"]["payload"]["error"] == response["error"]
    finally:
        store.close()


def test_worker_runner_bridges_child_tool_events_into_child_collaboration_progress(
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
    session = store.create_session(workspace_id=workspace["id"], title="bridge tool events")
    parent_task = store.create_task(session_id=session["id"], task_type="chat", goal="delegate", plan=[])

    fake_transport = _FakeTransport(
        events=[
            {
                "sessionId": session["id"],
                "taskId": "task_child_runtime",
                "type": "tool.started",
                "payload": {
                    "toolCallId": "call_child_1",
                    "toolName": "run_command",
                    "arguments": {"command": "python -V"},
                },
            },
            {
                "sessionId": session["id"],
                "taskId": "task_child_runtime",
                "type": "tool.completed",
                "payload": {
                    "toolCallId": "call_child_1",
                    "toolName": "run_command",
                    "result": {"stdout": "Python 3.14.0"},
                },
            },
        ],
        response={
            "jsonrpc": "2.0",
            "id": "rpc_1",
            "result": {
                "status": "completed",
                "summary": "child finished",
                "task": {"id": "task_child_runtime", "status": "completed"},
                "budget": {"maxTokens": 20, "remainingTokens": 15},
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
                prompt="run child tool",
                title="Run child tool",
                agent_type="coder",
                session_id=session["id"],
                parent_runtime_task_id=parent_task["id"],
            )
        )
        assert response["status"] == "completed"

        bridged = [
            event
            for event in events
            if event["type"] == "collab.task.updated"
            and isinstance(event["payload"], dict)
            and isinstance(event["payload"].get("_bridge"), dict)
        ]
        assert len(bridged) == 2
        assert all(event["payload"]["_bridge"]["childTaskId"] == "task_child_runtime" for event in bridged)
        assert all(event["payload"]["_bridge"]["parentRuntimeTaskId"] == parent_task["id"] for event in bridged)
        assert all(event["taskId"].startswith("ctask_") for event in bridged)
        assert bridged[0]["payload"]["task"]["result"]["summary"] == "Running run_command"
        assert bridged[1]["payload"]["task"]["result"]["summary"] == "run_command completed"

        child_task_id = bridged[0]["taskId"]
        child_trace = store.list_trace_events({"taskId": child_task_id})["traceEvents"]
        bridged_trace = [event for event in child_trace if isinstance(event["payload"].get("_bridge"), dict)]
        assert [event["type"] for event in bridged_trace] == ["collab.task.updated", "collab.task.updated"]
        assert bridged_trace[0]["payload"]["_bridge"]["childEvent"]["type"] == "tool.started"
    finally:
        store.close()


def test_worker_runner_bridges_child_collab_events_live_without_mirroring_trace(
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
    session = store.create_session(workspace_id=workspace["id"], title="bridge collab events")
    parent_task = store.create_task(session_id=session["id"], task_type="chat", goal="delegate", plan=[])

    fake_transport = _FakeTransport(
        events=[
            {
                "sessionId": session["id"],
                "taskId": "ctask_child_1",
                "type": "collab.task.created",
                "payload": {
                    "task": {"id": "ctask_child_1", "sessionId": session["id"], "title": "child collab"},
                    "worker": {"id": "agent_child_1"},
                },
            }
        ],
        response={
            "jsonrpc": "2.0",
            "id": "rpc_1",
            "result": {
                "status": "completed",
                "summary": "child finished",
                "task": {"id": "task_child_runtime", "status": "completed"},
                "budget": {},
            },
        },
    )

    monkeypatch.setattr(
        worker_runner_module.WorkerProcessTransport,
        "for_python_module",
        classmethod(lambda cls, *args, **kwargs: fake_transport),
    )

    try:
        runner.run_child_task(
            ChildTaskRequest(
                prompt="emit child collab event",
                title="Emit child collab event",
                agent_type="coder",
                session_id=session["id"],
                parent_runtime_task_id=parent_task["id"],
            )
        )

        collab_event = next(
            event
            for event in events
            if event["type"] == "collab.task.created"
            and isinstance(event["payload"], dict)
            and isinstance(event["payload"].get("_bridge"), dict)
        )
        assert collab_event["taskId"] == "ctask_child_1"
        assert collab_event["payload"]["_bridge"]["skipTraceMirror"] is True

        child_trace = store.list_trace_events({"taskId": "ctask_child_1"})["traceEvents"]
        assert child_trace == []
    finally:
        store.close()
