from __future__ import annotations

from pathlib import Path
from typing import Any


def _result(response: dict[str, Any], key: str) -> dict[str, Any]:
    assert "result" in response, response
    return response["result"][key]


def _event(runtime_harness: Any, event_type: str) -> dict[str, Any]:
    matches = [event for event in runtime_harness.events if event["type"] == event_type]
    assert matches, [event["type"] for event in runtime_harness.events]
    return matches[0]


def _event_index(runtime_harness: Any, event_type: str) -> int:
    for index, event in enumerate(runtime_harness.events):
        if event["type"] == event_type:
            return index
    raise AssertionError([event["type"] for event in runtime_harness.events])


def test_collaboration_rpc_emits_task_claim_and_message_events(runtime_harness: Any, tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    workspace = _result(runtime_harness.call("workspace.open", {"path": str(workspace_root)}), "workspace")
    session = _result(
        runtime_harness.call("session.create", {"workspaceId": workspace["id"], "title": "event stream"}),
        "session",
    )
    worker = _result(
        runtime_harness.call(
            "collab.worker.upsert",
            {
                "workerId": "agent_event_worker",
                "name": "Event Worker",
                "role": "worker",
                "capabilities": ["collab"],
            },
        ),
        "worker",
    )

    task = _result(
        runtime_harness.call(
            "collab.task.create",
            {
                "sessionId": session["id"],
                "title": "Publish collaboration events",
                "description": "Exercise collaboration event fan-out from RPC.",
                "priority": 2,
                "metadata": {"source": "test"},
            },
        ),
        "task",
    )
    claimed = runtime_harness.call(
        "collab.task.claim",
        {"taskId": task["id"], "workerId": worker["id"]},
    )["result"]
    message = _result(
        runtime_harness.call(
            "collab.message.send",
            {
                "senderWorkerId": worker["id"],
                "taskId": task["id"],
                "kind": "result",
                "body": "Collaboration event emitted.",
                "payload": {"confidence": 0.95},
            },
        ),
        "message",
    )
    completed = _result(
        runtime_harness.call(
            "collab.task.complete",
            {
                "taskId": task["id"],
                "workerId": worker["id"],
                "result": {"summary": "Task completed."},
            },
        ),
        "task",
    )

    created_event = _event(runtime_harness, "collab.task.created")
    claimed_event = _event(runtime_harness, "collab.task.claimed")
    message_event = _event(runtime_harness, "collab.message.sent")
    completed_event = _event(runtime_harness, "collab.task.completed")

    assert _event_index(runtime_harness, "collab.task.created") < _event_index(
        runtime_harness,
        "collab.task.claimed",
    ) < _event_index(runtime_harness, "collab.message.sent") < _event_index(runtime_harness, "collab.task.completed")

    assert created_event["sessionId"] == session["id"]
    assert created_event["taskId"] == task["id"]
    assert created_event["payload"]["task"]["id"] == task["id"]
    assert created_event["payload"]["task"]["title"] == "Publish collaboration events"

    assert claimed_event["sessionId"] == session["id"]
    assert claimed_event["taskId"] == task["id"]
    assert claimed_event["payload"]["task"]["id"] == claimed["task"]["id"]
    assert claimed_event["payload"]["task"]["assignedWorkerId"] == worker["id"]
    assert claimed_event["payload"]["worker"]["id"] == worker["id"]
    assert claimed_event["payload"]["worker"]["currentTaskId"] == task["id"]

    assert message_event["sessionId"] == session["id"]
    assert message_event["taskId"] == task["id"]
    assert message_event["payload"]["message"]["id"] == message["id"]
    assert message_event["payload"]["message"]["senderWorkerId"] == worker["id"]
    assert message_event["payload"]["message"]["taskId"] == task["id"]
    assert message_event["payload"]["message"]["payload"]["confidence"] == 0.95
    assert completed_event["sessionId"] == session["id"]
    assert completed_event["taskId"] == task["id"]
    assert completed_event["payload"]["task"]["id"] == completed["id"]
    assert completed_event["payload"]["task"]["status"] == "completed"
    assert completed_event["payload"]["worker"]["status"] == "idle"

    trace_events = runtime_harness.call("trace.list", {"taskId": task["id"]})["result"]["traceEvents"]
    trace_types = [event["type"] for event in trace_events]
    assert trace_types == [
        "collab.task.created",
        "collab.task.claimed",
        "collab.message.sent",
        "collab.task.completed",
    ]
    assert trace_events[0]["sessionId"] == session["id"]
    assert trace_events[0]["taskId"] == task["id"]
