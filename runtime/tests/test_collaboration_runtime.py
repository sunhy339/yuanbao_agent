from __future__ import annotations

from pathlib import Path
from typing import Any

from local_agent_runtime.event_bus import EventBus
from local_agent_runtime.services import CollaborationService, SubagentService
from local_agent_runtime.store.sqlite_store import SQLiteStore


def _result(response: dict[str, Any], key: str) -> dict[str, Any]:
    assert "result" in response, response
    return response["result"][key]


def test_subagent_dispatch_records_child_collaboration_trace(tmp_path: Path) -> None:
    store = SQLiteStore(str(tmp_path / "runtime.sqlite3"))
    event_bus = EventBus()
    events: list[dict[str, Any]] = []
    event_bus.subscribe(lambda event: events.append(event_bus.as_payload(event)))
    event_bus.subscribe(store.append_runtime_event)
    collaboration = CollaborationService(store, event_bus)
    subagent_service = SubagentService(store, collaboration)

    try:
        workspace_root = tmp_path / "workspace"
        workspace_root.mkdir(parents=True, exist_ok=True)
        workspace = store.upsert_workspace(str(workspace_root))
        session = store.create_session(workspace_id=workspace["id"], title="subagent dispatch")
        parent_task = store.create_task(session_id=session["id"], task_type="chat", goal="delegate", plan=[])

        result = subagent_service.dispatch(
            {
                "prompt": "Inspect the runtime and report the missing pieces.",
                "sessionId": session["id"],
                "taskId": parent_task["id"],
                "title": "Inspect runtime gaps",
                "agentType": "explorer",
                "priority": 7,
            }
        )

        child_task = result["task"]
        worker = result["worker"]

        assert result["status"] == "completed"
        assert result["summary"]
        assert result["subagent"]["executionMode"] == "process-rpc"
        assert child_task["sessionId"] == session["id"]
        assert child_task["title"] == "Inspect runtime gaps"
        assert child_task["description"] == "Inspect the runtime and report the missing pieces."
        assert child_task["status"] == "completed"
        assert child_task["assignedWorkerId"] == worker["id"]
        assert child_task["metadata"]["agentType"] == "explorer"
        assert child_task["metadata"]["parentRuntimeTaskId"] == parent_task["id"]
        assert worker["status"] == "idle"
        assert worker["currentTaskId"] is None
        assert worker["metadata"]["parentRuntimeTaskId"] == parent_task["id"]
        assert worker["capabilities"] == ["subagent", "collaboration"]
        event_types = [event["type"] for event in events]
        assert event_types[:3] == [
            "collab.task.created",
            "collab.task.claimed",
            "collab.task.updated",
        ]
        assert event_types[-2:] == ["collab.task.completed", "collab.message.sent"]
        assert any(
            event["type"] == "collab.task.updated"
            and isinstance(event["payload"], dict)
            and isinstance(event["payload"].get("_bridge"), dict)
            for event in events
        )

        trace_events = store.list_trace_events({"taskId": child_task["id"]})["traceEvents"]
        trace_types = [event["type"] for event in trace_events]
        assert trace_types[:3] == [
            "collab.task.created",
            "collab.task.claimed",
            "collab.task.updated",
        ]
        assert trace_types[-2:] == ["collab.task.completed", "collab.message.sent"]
        assert any(
            event["type"] == "collab.task.updated"
            and isinstance(event["payload"], dict)
            and isinstance(event["payload"].get("_bridge"), dict)
            for event in trace_events
        )
        assert trace_events[0]["sessionId"] == session["id"]
        assert trace_events[-1]["payload"]["message"]["taskId"] == child_task["id"]
        assert trace_events[-1]["payload"]["message"]["payload"]["executionMode"] == "process-rpc"

        messages = store.list_agent_messages({"taskId": child_task["id"]})["messages"]
        assert len(messages) == 1
        assert messages[0]["kind"] == "result"
        assert messages[0]["payload"]["executionMode"] == "process-rpc"
    finally:
        store.close()


def test_collaboration_task_worker_and_message_flow(runtime_harness: Any, tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    workspace = _result(runtime_harness.call("workspace.open", {"path": str(workspace_root)}), "workspace")
    session = _result(
        runtime_harness.call("session.create", {"workspaceId": workspace["id"], "title": "multi-agent"}),
        "session",
    )

    worker = _result(
        runtime_harness.call(
            "collab.worker.upsert",
            {
                "workerId": "agent_explorer",
                "name": "Explorer",
                "role": "explorer",
                "capabilities": ["read", "search"],
            },
        ),
        "worker",
    )
    assert worker["status"] == "idle"
    assert worker["capabilities"] == ["read", "search"]

    task = _result(
        runtime_harness.call(
            "collab.task.create",
            {
                "sessionId": session["id"],
                "title": "Map runtime collaboration gaps",
                "description": "Inspect the runtime and report missing pieces.",
                "priority": 1,
                "dependencies": ["docs-ready"],
                "metadata": {"source": "test"},
            },
        ),
        "task",
    )
    assert task["status"] == "queued"
    assert task["priority"] == 1
    assert task["dependencies"] == ["docs-ready"]

    claimed = runtime_harness.call(
        "collab.task.claim",
        {"taskId": task["id"], "workerId": worker["id"]},
    )["result"]
    assert claimed["task"]["status"] == "claimed"
    assert claimed["task"]["assignedWorkerId"] == worker["id"]
    assert claimed["worker"]["status"] == "busy"
    assert claimed["worker"]["currentTaskId"] == task["id"]

    message = _result(
        runtime_harness.call(
            "collab.message.send",
            {
                "senderWorkerId": worker["id"],
                "taskId": task["id"],
                "kind": "result",
                "body": "Found the first slice.",
                "payload": {"confidence": 0.9},
            },
        ),
        "message",
    )
    assert message["kind"] == "result"
    assert message["payload"]["confidence"] == 0.9

    updated = _result(
        runtime_harness.call(
            "collab.task.update",
            {"taskId": task["id"], "status": "completed", "result": {"summary": "Done"}},
        ),
        "task",
    )
    assert updated["status"] == "completed"
    assert updated["result"]["summary"] == "Done"
    assert updated["completedAt"] is not None

    task_list = runtime_harness.call("collab.task.list", {"sessionId": session["id"]})["result"]["tasks"]
    assert [item["id"] for item in task_list] == [task["id"]]

    messages = runtime_harness.call("collab.message.list", {"taskId": task["id"]})["result"]["messages"]
    assert [item["id"] for item in messages] == [message["id"]]


def test_collaboration_task_complete_releases_worker(runtime_harness: Any, tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    workspace = _result(runtime_harness.call("workspace.open", {"path": str(workspace_root)}), "workspace")
    session = _result(
        runtime_harness.call("session.create", {"workspaceId": workspace["id"], "title": "completion"}),
        "session",
    )
    worker = _result(
        runtime_harness.call(
            "collab.worker.upsert",
            {"workerId": "worker_complete", "name": "Completer", "role": "worker"},
        ),
        "worker",
    )
    task = _result(
        runtime_harness.call(
            "collab.task.create",
            {
                "sessionId": session["id"],
                "title": "Complete collaboration task",
                "priority": 1,
            },
        ),
        "task",
    )
    runtime_harness.call("collab.task.claim", {"taskId": task["id"], "workerId": worker["id"]})

    completed = runtime_harness.call(
        "collab.task.complete",
        {
            "taskId": task["id"],
            "workerId": worker["id"],
            "result": {"summary": "Finished"},
        },
    )["result"]

    assert completed["task"]["status"] == "completed"
    assert completed["task"]["result"]["summary"] == "Finished"
    assert completed["worker"]["status"] == "idle"
    assert completed["worker"]["currentTaskId"] is None

    reloaded_task = _result(runtime_harness.call("collab.task.get", {"taskId": task["id"]}), "task")
    assert reloaded_task["status"] == "completed"
    assert reloaded_task["completedAt"] is not None


def test_collaboration_claim_requires_queueable_status(runtime_harness: Any) -> None:
    runtime_harness.call(
        "collab.worker.upsert",
        {"workerId": "agent_worker", "name": "Worker", "role": "worker"},
    )
    task = _result(
        runtime_harness.call(
            "collab.task.create",
            {"title": "Non-queueable task", "priority": 5},
        ),
        "task",
    )
    runtime_harness.call("collab.task.update", {"taskId": task["id"], "status": "running"})

    response = runtime_harness.call(
        "collab.task.claim",
        {"taskId": task["id"], "workerId": "agent_worker"},
    )

    assert response["error"]["code"] == "INTERNAL_ERROR"
    assert "cannot be claimed" in response["error"]["message"]
