from __future__ import annotations

from pathlib import Path
from typing import Any

from local_agent_runtime.event_bus import EventBus
from local_agent_runtime.services import CollaborationService
from local_agent_runtime.store.sqlite_store import SQLiteStore


def _result(response: dict[str, Any], key: str) -> dict[str, Any]:
    assert key in response, response
    return response[key]


def test_worker_health_is_derived_on_get_and_list(tmp_path: Path) -> None:
    store = SQLiteStore(str(tmp_path / "runtime.sqlite3"))
    event_bus = EventBus()
    collaboration = CollaborationService(store, event_bus)
    now = 1_000_000
    store.now = lambda: now  # type: ignore[method-assign]

    try:
        workspace = store.upsert_workspace(str(tmp_path / "workspace"))
        session = store.create_session(workspace_id=workspace["id"], title="worker health")
        task = collaboration.create_collaboration_task(
            {
                "sessionId": session["id"],
                "title": "Monitor worker",
                "priority": 1,
            }
        )["task"]

        healthy_worker = collaboration.upsert_agent_worker(
            {
                "workerId": "agent_healthy",
                "name": "Healthy Worker",
                "role": "worker",
                "status": "busy",
                "currentTaskId": task["id"],
            }
        )["worker"]
        offline_worker = collaboration.upsert_agent_worker(
            {
                "workerId": "agent_offline",
                "name": "Offline Worker",
                "role": "worker",
                "status": "offline",
            }
        )["worker"]

        assert healthy_worker["healthState"] == "healthy"
        assert healthy_worker["health"]["state"] == "healthy"
        assert healthy_worker["health"]["heartbeatAgeMs"] == 0
        assert offline_worker["healthState"] == "offline"
        assert offline_worker["health"]["reason"] == "worker_status_offline"

        healthy_view = collaboration.get_agent_worker({"workerId": healthy_worker["id"]})["worker"]
        assert healthy_view["healthState"] == "healthy"

        listed = collaboration.list_agent_workers()
        assert listed["healthSummary"] == {
            "healthy": 1,
            "stale": 0,
            "offline": 1,
            "total": 2,
            "assessedAt": now,
        }

        now += 30_001
        stale_view = collaboration.get_agent_worker({"workerId": healthy_worker["id"]})["worker"]
        assert stale_view["healthState"] == "stale"
        assert stale_view["health"]["reason"] == "heartbeat_stale"
        assert stale_view["health"]["heartbeatAgeMs"] == 30_001

        now += 90_000
        offline_view = collaboration.get_agent_worker({"workerId": healthy_worker["id"]})["worker"]
        assert offline_view["healthState"] == "offline"
        assert offline_view["health"]["reason"] == "heartbeat_timeout"

        listed = collaboration.list_agent_workers()
        assert listed["healthSummary"] == {
            "healthy": 0,
            "stale": 0,
            "offline": 2,
            "total": 2,
            "assessedAt": now,
        }
    finally:
        store.close()


def test_worker_health_change_event_emits_on_recovery(tmp_path: Path) -> None:
    store = SQLiteStore(str(tmp_path / "runtime.sqlite3"))
    event_bus = EventBus()
    events: list[dict[str, Any]] = []
    event_bus.subscribe(lambda event: events.append(event_bus.as_payload(event)))
    collaboration = CollaborationService(store, event_bus)
    now = 2_000_000
    store.now = lambda: now  # type: ignore[method-assign]

    try:
        workspace_root = tmp_path / "workspace"
        workspace_root.mkdir()
        workspace = store.upsert_workspace(str(workspace_root))
        session = store.create_session(workspace_id=workspace["id"], title="health events")
        task = collaboration.create_collaboration_task(
            {
                "sessionId": session["id"],
                "title": "Recover worker",
                "priority": 1,
            }
        )["task"]

        worker = collaboration.upsert_agent_worker(
            {
                "workerId": "agent_recovery",
                "name": "Recovery Worker",
                "role": "worker",
                "status": "busy",
                "currentTaskId": task["id"],
            }
        )["worker"]
        assert worker["healthState"] == "healthy"

        now += 120_001
        heartbeat = collaboration.heartbeat_agent_worker(
            {
                "workerId": worker["id"],
                "status": "busy",
                "currentTaskId": task["id"],
            }
        )["worker"]

        assert heartbeat["healthState"] == "healthy"

        health_events = [event for event in events if event["type"] == "collab.worker.health.changed"]
        assert len(health_events) == 1
        event = health_events[0]
        assert event["sessionId"] == session["id"]
        assert event["taskId"] == task["id"]
        assert event["payload"]["worker"]["id"] == worker["id"]
        assert event["payload"]["previousHealth"]["state"] == "offline"
        assert event["payload"]["health"]["state"] == "healthy"
        assert event["payload"]["transition"] == "offline->healthy"
    finally:
        store.close()
