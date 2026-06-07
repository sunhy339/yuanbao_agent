from __future__ import annotations

import json
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
    canonical_created_event = _event(runtime_harness, "task.created")
    claimed_event = _event(runtime_harness, "collab.task.claimed")
    canonical_updated_event = _event(runtime_harness, "task.updated")
    message_event = _event(runtime_harness, "collab.message.sent")
    completed_event = _event(runtime_harness, "collab.task.completed")

    assert _event_index(runtime_harness, "task.created") < _event_index(
        runtime_harness,
        "collab.task.created",
    ) < _event_index(
        runtime_harness,
        "task.updated",
    ) < _event_index(
        runtime_harness,
        "collab.task.claimed",
    ) < _event_index(runtime_harness, "collab.message.sent") < _event_index(runtime_harness, "collab.task.completed")

    assert canonical_created_event["sessionId"] == session["id"]
    assert canonical_created_event["taskId"] == task["id"]
    assert canonical_created_event["visibility"] == "panel"
    assert canonical_created_event["payload"]["source"] == "collaboration"
    assert canonical_created_event["payload"]["taskKind"] == "collaboration_child"
    assert canonical_created_event["payload"]["title"] == "Publish collaboration events"
    assert canonical_created_event["hahaCc"] == {
        "type": "task_update",
        "taskId": task["id"],
        "status": "queued",
        "progress": "Publish collaboration events",
    }

    assert canonical_updated_event["payload"]["source"] == "collaboration"
    assert canonical_updated_event["payload"]["taskKind"] == "collaboration_child"
    assert canonical_updated_event["payload"]["workerId"] == worker["id"]
    assert canonical_updated_event["hahaCc"] == {
        "type": "task_update",
        "taskId": task["id"],
        "status": "claimed",
        "progress": "Publish collaboration events",
    }

    assert created_event["sessionId"] == session["id"]
    assert created_event["taskId"] == task["id"]
    assert created_event["visibility"] == "panel"
    assert created_event["payload"]["task"]["id"] == task["id"]
    assert created_event["payload"]["task"]["title"] == "Publish collaboration events"
    assert created_event["payload"]["team"]["teamName"] == session["id"]
    assert created_event["payload"]["team"]["tasks"][0]["id"] == task["id"]
    assert created_event["hahaCc"] == {
        "type": "team_update",
        "teamName": session["id"],
        "members": [
            {
                "agentId": task["id"],
                "role": "worker",
                "status": "running",
                "currentTask": "Publish collaboration events",
            }
        ],
    }

    assert claimed_event["sessionId"] == session["id"]
    assert claimed_event["taskId"] == task["id"]
    assert claimed_event["visibility"] == "panel"
    assert claimed_event["payload"]["task"]["id"] == claimed["task"]["id"]
    assert claimed_event["payload"]["task"]["assignedWorkerId"] == worker["id"]
    assert claimed_event["payload"]["worker"]["id"] == worker["id"]
    assert claimed_event["payload"]["worker"]["currentTaskId"] == task["id"]
    assert claimed_event["payload"]["team"]["teamName"] == session["id"]
    assert claimed_event["hahaCc"] == {
        "type": "team_update",
        "teamName": session["id"],
        "members": [
            {
                "agentId": worker["id"],
                "role": "worker",
                "status": "running",
                "currentTask": "Publish collaboration events",
            }
        ],
    }

    assert message_event["sessionId"] == session["id"]
    assert message_event["taskId"] == task["id"]
    assert message_event["visibility"] == "panel"
    assert message_event["payload"]["message"]["id"] == message["id"]
    assert message_event["payload"]["message"]["senderWorkerId"] == worker["id"]
    assert message_event["payload"]["message"]["taskId"] == task["id"]
    assert message_event["payload"]["message"]["payload"]["confidence"] == 0.95
    assert message_event["payload"]["team"]["teamName"] == session["id"]
    assert message_event["hahaCc"] == {
        "type": "team_update",
        "teamName": session["id"],
        "members": [
            {
                "agentId": worker["id"],
                "role": "worker",
                "status": "running",
                "currentTask": "Collaboration event emitted.",
            }
        ],
    }
    assert completed_event["sessionId"] == session["id"]
    assert completed_event["taskId"] == task["id"]
    assert completed_event["visibility"] == "panel"
    assert completed_event["payload"]["task"]["id"] == completed["id"]
    assert completed_event["payload"]["task"]["status"] == "completed"
    assert completed_event["payload"]["worker"]["status"] == "idle"
    assert completed_event["payload"]["team"]["teamName"] == session["id"]
    assert completed_event["hahaCc"] == {
        "type": "team_update",
        "teamName": session["id"],
        "members": [
            {
                "agentId": worker["id"],
                "role": "worker",
                "status": "completed",
                "currentTask": "Task completed.",
            }
        ],
    }

    trace_events = runtime_harness.call("trace.list", {"taskId": task["id"]})["result"]["traceEvents"]
    trace_types = [event["type"] for event in trace_events]
    assert trace_types == [
        "task.created",
        "collab.task.created",
        "task.updated",
        "collab.task.claimed",
        "collab.message.sent",
        "task.updated",
        "collab.task.completed",
    ]
    assert trace_events[0]["sessionId"] == session["id"]
    assert trace_events[0]["taskId"] == task["id"]
    assert {event["visibility"] for event in trace_events} == {"panel"}
    assert trace_events[0]["hahaCc"] == canonical_created_event["hahaCc"]
    assert trace_events[2]["hahaCc"] == canonical_updated_event["hahaCc"]
    assert trace_events[-2]["hahaCc"]["type"] == "task_update"
    assert trace_events[-2]["hahaCc"]["status"] == "completed"

    yuanbao_after = runtime_harness.call(
        "events.yuanbaoAfter",
        {"sessionId": session["id"], "afterSeq": 0},
    )["result"]["messages"]
    task_updates = [message for message in yuanbao_after if message.get("type") == "task_update"]
    assert task_updates[-3:] == [
        canonical_created_event["hahaCc"],
        canonical_updated_event["hahaCc"],
        trace_events[-2]["hahaCc"],
    ]
    team_updates = [message for message in yuanbao_after if message.get("type") == "team_update"]
    assert team_updates[-4:] == [
        created_event["hahaCc"],
        claimed_event["hahaCc"],
        message_event["hahaCc"],
        completed_event["hahaCc"],
    ]

    team_snapshot = runtime_harness.call("events.yuanbaoTeamSnapshot", {"sessionId": session["id"]})["result"]
    assert team_snapshot == {
        "teamName": session["id"],
        "messages": [
            {"type": "team_created", "teamName": session["id"]},
            completed_event["hahaCc"],
        ],
    }
    legacy_team_snapshot = runtime_harness.call("events.hahaCcTeamSnapshot", {"sessionId": session["id"]})["result"]
    assert legacy_team_snapshot == team_snapshot


def test_collaboration_visible_events_hide_internal_completion_evidence(runtime_harness: Any, tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    workspace = _result(runtime_harness.call("workspace.open", {"path": str(workspace_root)}), "workspace")
    session = _result(
        runtime_harness.call("session.create", {"workspaceId": workspace["id"], "title": "clean panels"}),
        "session",
    )
    worker = _result(
        runtime_harness.call(
            "collab.worker.upsert",
            {
                "workerId": "agent_clean_worker",
                "name": "Clean Worker",
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
                "title": "Inspect output",
                "description": "Inspect public event projection.",
                "metadata": {"agentType": "planner"},
            },
        ),
        "task",
    )

    runtime_harness.call(
        "collab.message.send",
        {
            "senderWorkerId": worker["id"],
            "taskId": task["id"],
            "kind": "result",
            "body": "Output inspected.",
            "payload": {
                "structuredResult": {
                    "summary": "Output inspected.",
                    "completionEvidence": {"status": "internal"},
                    "completionGate": {"status": "needs_review"},
                    "workspaceRoot": "D:/py/test_pro",
                },
                "completionEvidence": {"status": "internal"},
                "visible": "kept",
            },
        },
    )

    message_event = [event for event in runtime_harness.events if event["type"] == "collab.message.sent"][-1]
    encoded = json.dumps(message_event["payload"], ensure_ascii=False)
    assert message_event["visibility"] == "panel"
    assert message_event["payload"]["message"]["payload"]["visible"] == "kept"
    assert "completionEvidence" not in encoded
    assert "completionGate" not in encoded
    assert "workspaceRoot" not in encoded

    yuanbao_after = runtime_harness.call(
        "events.yuanbaoAfter",
        {"sessionId": session["id"], "afterSeq": 0},
    )["result"]["messages"]
    encoded_replay = json.dumps(yuanbao_after, ensure_ascii=False)
    assert "completionEvidence" not in encoded_replay
    assert "completionGate" not in encoded_replay
    assert "workspaceRoot" not in encoded_replay


def test_collaboration_worker_heartbeat_and_failed_task_emit_team_updates(runtime_harness: Any, tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    workspace = _result(runtime_harness.call("workspace.open", {"path": str(workspace_root)}), "workspace")
    session = _result(
        runtime_harness.call("session.create", {"workspaceId": workspace["id"], "title": "failed child task"}),
        "session",
    )
    worker = _result(
        runtime_harness.call(
            "collab.worker.upsert",
            {
                "workerId": "agent_failure_worker",
                "name": "Failure Worker",
                "role": "reviewer",
                "status": "idle",
                "sessionId": session["id"],
                "capabilities": ["collab"],
            },
        ),
        "worker",
    )
    assert worker["metadata"]["sessionId"] == session["id"]

    worker_event = _event(runtime_harness, "collab.worker.upserted")
    assert worker_event["sessionId"] == session["id"]
    assert worker_event["taskId"] == session["id"]
    assert worker_event["hahaCc"] == {
        "type": "team_update",
        "teamName": session["id"],
        "members": [
            {
                "agentId": worker["id"],
                "role": "reviewer",
                "status": "idle",
            }
        ],
    }

    task = _result(
        runtime_harness.call(
            "collab.task.create",
            {
                "sessionId": session["id"],
                "title": "Review risky migration",
                "description": "Find problems before merge.",
                "metadata": {"agentType": "reviewer"},
            },
        ),
        "task",
    )
    runtime_harness.call("collab.task.claim", {"taskId": task["id"], "workerId": worker["id"]})
    heartbeat = _result(
        runtime_harness.call(
            "collab.worker.heartbeat",
            {
                "workerId": worker["id"],
                "currentTaskId": task["id"],
                "status": "busy",
            },
        ),
        "worker",
    )
    failed = _result(
        runtime_harness.call(
            "collab.task.fail",
            {
                "taskId": task["id"],
                "workerId": worker["id"],
                "error": {"message": "Validation failed."},
            },
        ),
        "task",
    )

    heartbeat_event = _event(runtime_harness, "collab.worker.heartbeat")
    failed_event = _event(runtime_harness, "collab.task.failed")

    assert heartbeat["currentTaskId"] == task["id"]
    assert heartbeat_event["hahaCc"] == {
        "type": "team_update",
        "teamName": session["id"],
        "members": [
            {
                "agentId": worker["id"],
                "role": "reviewer",
                "status": "running",
                "currentTask": "Review risky migration",
            }
        ],
    }
    assert failed["status"] == "failed"
    assert failed_event["hahaCc"] == {
        "type": "team_update",
        "teamName": session["id"],
        "members": [
            {
                "agentId": worker["id"],
                "role": "reviewer",
                "status": "error",
                "currentTask": "Validation failed.",
            }
        ],
    }

    session_events = runtime_harness.call("events.after", {"sessionId": session["id"], "afterSeq": 0})["result"]["events"]
    assert any(event["type"] == "collab.worker.upserted" for event in session_events)
    assert any(event.get("yuanbao") == failed_event["hahaCc"] for event in session_events)


def test_session_update_emits_haha_cc_title_event(runtime_harness: Any, tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    workspace = _result(runtime_harness.call("workspace.open", {"path": str(workspace_root)}), "workspace")
    session = _result(
        runtime_harness.call("session.create", {"workspaceId": workspace["id"], "title": "old title"}),
        "session",
    )

    updated = _result(
        runtime_harness.call(
            "session.update",
            {
                "sessionId": session["id"],
                "title": "new title",
            },
        ),
        "session",
    )

    assert updated["title"] == "new title"
    event = _event(runtime_harness, "session.updated")
    assert event["sessionId"] == session["id"]
    assert event["taskId"] == session["id"]
    assert event["payload"]["title"] == "new title"
    assert event["payload"]["changedFields"] == ["title"]
    assert event["hahaCc"] == {
        "type": "session_title_updated",
        "sessionId": session["id"],
        "title": "new title",
    }

    trace_events = runtime_harness.call("events.after", {"sessionId": session["id"], "afterSeq": 0})["result"]["events"]
    session_trace = [item for item in trace_events if item["type"] == "session.updated"]
    assert session_trace[-1]["hahaCc"] == event["hahaCc"]


def test_session_create_emits_lifecycle_event_without_flat_chat_message(runtime_harness: Any, tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    workspace = _result(runtime_harness.call("workspace.open", {"path": str(workspace_root)}), "workspace")

    session = _result(
        runtime_harness.call("session.create", {"workspaceId": workspace["id"], "title": "new session"}),
        "session",
    )

    event = _event(runtime_harness, "session.created")
    assert event["sessionId"] == session["id"]
    assert event["taskId"] == session["id"]
    assert event["visibility"] == "panel"
    assert event["payload"]["session"]["id"] == session["id"]
    assert "yuanbao" not in event
    assert "hahaCc" not in event
