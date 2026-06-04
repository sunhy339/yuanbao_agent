from __future__ import annotations

from typing import Any

from ..models import RuntimeEvent
from ..yuanbao_event_adapter import to_yuanbao_server_message
from .worker_health import (
    DEFAULT_WORKER_HEALTH_POLICY,
    WorkerHealthPolicy,
    assess_worker_health,
    enrich_worker,
    summarize_worker_health,
)


class CollaborationService:
    """Thin event-publishing wrapper around collaboration store methods."""

    def __init__(
        self,
        store: Any,
        event_bus: Any,
        worker_health_policy: WorkerHealthPolicy = DEFAULT_WORKER_HEALTH_POLICY,
    ) -> None:
        self._store = store
        self._event_bus = event_bus
        self._worker_health_policy = worker_health_policy

    @property
    def store(self) -> Any:
        return self._store

    @property
    def event_bus(self) -> Any:
        return self._event_bus

    def create_collaboration_task(self, params: dict[str, Any]) -> dict[str, Any]:
        result = self._store.create_collaboration_task(params)
        self._publish_task_event(result, "collab.task.created")
        return result

    def get_collaboration_task(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._store.get_collaboration_task(params)

    def list_collaboration_tasks(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._store.list_collaboration_tasks(params)

    def update_collaboration_task(self, params: dict[str, Any]) -> dict[str, Any]:
        result = self._store.update_collaboration_task(params)
        self._publish_task_event(result, "collab.task.updated")
        return result

    def claim_collaboration_task(self, params: dict[str, Any]) -> dict[str, Any]:
        result = self._store.claim_collaboration_task(params)
        self._publish_task_event(result, "collab.task.claimed")
        return result

    def complete_collaboration_task(self, params: dict[str, Any]) -> dict[str, Any]:
        result = self._store.complete_collaboration_task(params)
        self._publish_task_event(result, "collab.task.completed")
        return result

    def fail_collaboration_task(self, params: dict[str, Any]) -> dict[str, Any]:
        result = self._store.fail_collaboration_task(params)
        self._publish_task_event(result, "collab.task.failed")
        return result

    def release_collaboration_task(self, params: dict[str, Any]) -> dict[str, Any]:
        result = self._store.release_collaboration_task(params)
        self._publish_task_event(result, "collab.task.released")
        return result

    def upsert_agent_worker(self, params: dict[str, Any]) -> dict[str, Any]:
        previous_worker = self._existing_worker(self._worker_id_from_params(params))
        result = self._store.upsert_agent_worker(params)
        assessed_at = self._store.now()
        result = self._enrich_worker_result(result, now_ms=assessed_at)
        self._publish_worker_event(result, "collab.worker.upserted")
        self._publish_worker_health_change(previous_worker, result, assessed_at=assessed_at)
        return result

    def get_agent_worker(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._enrich_worker_result(self._store.get_agent_worker(params))

    def list_agent_workers(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._enrich_worker_list_result(self._store.list_agent_workers(params))

    def heartbeat_agent_worker(self, params: dict[str, Any]) -> dict[str, Any]:
        previous_worker = self._existing_worker(self._worker_id_from_params(params))
        result = self._store.heartbeat_agent_worker(params)
        assessed_at = self._store.now()
        result = self._enrich_worker_result(result, now_ms=assessed_at)
        self._publish_worker_event(result, "collab.worker.heartbeat")
        self._publish_worker_health_change(previous_worker, result, assessed_at=assessed_at)
        return result

    def send_agent_message(self, params: dict[str, Any]) -> dict[str, Any]:
        result = self._store.send_agent_message(params)
        self._publish_message_event(result, "collab.message.sent")
        return result

    def list_agent_messages(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._store.list_agent_messages(params)

    def publish_runtime_event(
        self,
        *,
        session_id: str,
        task_id: str,
        event_type: str,
        payload: dict[str, Any],
        visibility: str = "panel",
    ) -> None:
        self._publish(
            session_id=session_id,
            task_id=task_id,
            event_type=event_type,
            payload=payload,
            visibility=visibility,
        )

    def team_snapshot_messages(self, params: dict[str, Any]) -> dict[str, Any]:
        session_id = (
            self._string_or_none(params.get("sessionId"))
            or self._string_or_none(params.get("session_id"))
            or self._string_or_none(params.get("teamName"))
            or self._string_or_none(params.get("team_name"))
        )
        if session_id is None:
            return {"teamName": "", "messages": []}
        snapshot = self._team_snapshot(session_id)
        created = to_yuanbao_server_message(
            RuntimeEvent(
                event_id=self._store.new_id("evt"),
                session_id=session_id,
                task_id=session_id,
                type="collab.team.created",
                ts=self._store.now(),
                payload={"teamName": session_id, "team": snapshot},
                visibility="panel",
            )
        )
        updated = to_yuanbao_server_message(
            RuntimeEvent(
                event_id=self._store.new_id("evt"),
                session_id=session_id,
                task_id=session_id,
                type="collab.task.updated",
                ts=self._store.now(),
                payload={"team": snapshot},
                visibility="panel",
            )
        )
        return {
            "teamName": session_id,
            "messages": [message for message in (created, updated) if isinstance(message, dict)],
        }

    def _publish_task_event(self, result: dict[str, Any], event_type: str) -> None:
        task = result.get("task")
        if not isinstance(task, dict):
            return
        task_id = self._string_or_none(task.get("id"))
        if task_id is None:
            return
        payload = self._with_team_snapshot(result, session_id=self._string_or_empty(task.get("sessionId")))
        session_id = self._string_or_empty(task.get("sessionId"))
        self._publish(
            session_id=session_id,
            task_id=task_id,
            event_type=self._canonical_task_event_type(event_type),
            payload=self._canonical_task_payload(payload, event_type=event_type),
        )
        self._publish(
            session_id=session_id,
            task_id=task_id,
            event_type=event_type,
            payload=payload,
        )

    def _canonical_task_event_type(self, event_type: str) -> str:
        return "task.created" if event_type == "collab.task.created" else "task.updated"

    def _canonical_task_payload(self, payload: dict[str, Any], *, event_type: str) -> dict[str, Any]:
        task = payload.get("task")
        task_record = task if isinstance(task, dict) else {}
        worker = payload.get("worker")
        worker_record = worker if isinstance(worker, dict) else {}
        metadata = task_record.get("metadata") if isinstance(task_record.get("metadata"), dict) else {}
        result = task_record.get("result") if isinstance(task_record.get("result"), dict) else {}
        error = task_record.get("error") if isinstance(task_record.get("error"), dict) else {}
        summary = self._string_or_none(result.get("summary")) or self._string_or_none(result.get("resultSummary"))
        error_message = self._string_or_none(error.get("message")) or self._string_or_none(error.get("summary"))
        title = self._string_or_none(task_record.get("title")) or self._string_or_none(task_record.get("description"))
        status = self._string_or_none(task_record.get("status"))
        progress = summary or error_message or title or status or event_type.removeprefix("collab.task.")
        parent_task_id = self._string_or_none(task_record.get("parentTaskId"))
        session_id = self._string_or_none(task_record.get("sessionId"))
        task_id = self._string_or_none(task_record.get("id")) or ""
        worker_id = self._string_or_none(task_record.get("assignedWorkerId")) or self._string_or_none(worker_record.get("id"))
        payload_out: dict[str, Any] = {
            "source": "collaboration",
            "taskKind": "collaboration_child",
            "taskId": task_id,
            "status": status or event_type.removeprefix("collab.task."),
            "progress": progress,
            "currentStep": progress,
            "title": title,
            "summary": summary or error_message,
            "resultSummary": summary or error_message,
            "description": self._string_or_none(task_record.get("description")),
            "parentTaskId": parent_task_id,
            "sessionId": session_id,
            "agentType": self._string_or_none(metadata.get("agentType")) or self._string_or_none(task_record.get("agentType")),
            "workerId": worker_id,
            "workerName": self._string_or_none(worker_record.get("name")),
            "collaborationEventType": event_type,
            "collaborationTask": task_record,
        }
        if worker_record:
            payload_out["worker"] = worker_record
        return {key: value for key, value in payload_out.items() if value is not None}

    def _publish_worker_event(self, result: dict[str, Any], event_type: str) -> None:
        worker = result.get("worker")
        if not isinstance(worker, dict):
            return
        task = self._task_for_id(worker.get("currentTaskId"))
        session_id = self._string_or_empty(task.get("sessionId")) if isinstance(task, dict) else self._session_id_from_worker(worker)
        if not session_id:
            return
        task_id = task["id"] if isinstance(task, dict) else session_id
        payload = self._with_team_snapshot(result, session_id=session_id)
        self._publish(
            session_id=session_id,
            task_id=task_id,
            event_type=event_type,
            payload=payload,
        )

    def _publish_message_event(self, result: dict[str, Any], event_type: str) -> None:
        message = result.get("message")
        if not isinstance(message, dict):
            return
        task = self._task_for_id(message.get("taskId"))
        if task is None:
            return
        payload = self._with_team_snapshot(result, session_id=self._string_or_empty(task.get("sessionId")))
        self._publish(
            session_id=self._string_or_empty(task.get("sessionId")),
            task_id=task["id"],
            event_type=event_type,
            payload=payload,
        )

    def _task_for_id(self, value: Any) -> dict[str, Any] | None:
        task_id = self._string_or_none(value)
        if task_id is None:
            return None
        try:
            task = self._store.get_collaboration_task({"taskId": task_id}).get("task")
        except ValueError:
            return None
        return task if isinstance(task, dict) else None

    def _with_team_snapshot(self, result: dict[str, Any], *, session_id: str) -> dict[str, Any]:
        if not session_id:
            return result
        snapshot = self._team_snapshot(session_id)
        if not snapshot:
            return result
        payload = dict(result)
        payload["team"] = snapshot
        return payload

    def _team_snapshot(self, session_id: str) -> dict[str, Any]:
        tasks = self._tasks_for_session(session_id)
        workers = self._workers_for_tasks(tasks)
        return {
            "teamName": session_id,
            "sessionId": session_id,
            "tasks": tasks,
            "workers": workers,
        }

    def _tasks_for_session(self, session_id: str) -> list[dict[str, Any]]:
        try:
            result = self._store.list_collaboration_tasks({"sessionId": session_id})
        except (AttributeError, ValueError):
            return []
        tasks = result.get("tasks")
        return [task for task in tasks if isinstance(task, dict)] if isinstance(tasks, list) else []

    def _workers_for_tasks(self, tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        worker_ids = {
            worker_id
            for worker_id in (self._string_or_none(task.get("assignedWorkerId")) for task in tasks)
            if worker_id is not None
        }
        workers: list[dict[str, Any]] = []
        for worker_id in sorted(worker_ids):
            try:
                worker = self._store.get_agent_worker({"workerId": worker_id}).get("worker")
            except (AttributeError, ValueError):
                continue
            if isinstance(worker, dict):
                workers.append(self._enrich_worker(worker, now_ms=self._store.now()))
        return workers

    def _existing_worker(self, worker_id: str | None) -> dict[str, Any] | None:
        if worker_id is None:
            return None
        try:
            worker = self._store.get_agent_worker({"workerId": worker_id}).get("worker")
        except ValueError:
            return None
        return worker if isinstance(worker, dict) else None

    def _worker_id_from_params(self, params: dict[str, Any]) -> str | None:
        return self._string_or_none(params.get("workerId")) or self._string_or_none(params.get("id"))

    def _session_id_from_worker(self, worker: dict[str, Any]) -> str:
        for key in ("sessionId", "session_id", "teamName", "team"):
            value = self._string_or_none(worker.get(key))
            if value:
                return value
        metadata = worker.get("metadata")
        if isinstance(metadata, dict):
            for key in ("sessionId", "session_id", "teamName", "team"):
                value = self._string_or_none(metadata.get(key))
                if value:
                    return value
        return ""

    def _enrich_worker_result(self, result: dict[str, Any], *, now_ms: int | None = None) -> dict[str, Any]:
        worker = result.get("worker")
        if not isinstance(worker, dict):
            return result
        assessed_at = self._store.now() if now_ms is None else now_ms
        enriched = dict(result)
        enriched["worker"] = self._enrich_worker(worker, now_ms=assessed_at)
        return enriched

    def _enrich_worker_list_result(self, result: dict[str, Any], *, now_ms: int | None = None) -> dict[str, Any]:
        workers = result.get("workers")
        if not isinstance(workers, list):
            return result
        assessed_at = self._store.now() if now_ms is None else now_ms
        enriched_workers = [
            self._enrich_worker(worker, now_ms=assessed_at) for worker in workers if isinstance(worker, dict)
        ]
        enriched = dict(result)
        enriched["workers"] = enriched_workers
        health_summary = summarize_worker_health(enriched_workers)
        health_summary["assessedAt"] = assessed_at
        enriched["healthSummary"] = health_summary
        return enriched

    def _enrich_worker(self, worker: dict[str, Any], *, now_ms: int) -> dict[str, Any]:
        return enrich_worker(worker, now_ms=now_ms, policy=self._worker_health_policy)

    def _publish_worker_health_change(
        self,
        previous_worker: dict[str, Any] | None,
        result: dict[str, Any],
        *,
        assessed_at: int,
    ) -> None:
        worker = result.get("worker")
        if previous_worker is None or not isinstance(worker, dict):
            return
        previous_health = assess_worker_health(
            previous_worker,
            now_ms=assessed_at,
            policy=self._worker_health_policy,
        )
        current_health = worker.get("health")
        if not isinstance(current_health, dict):
            return
        previous_state = previous_health.get("state")
        current_state = current_health.get("state")
        if previous_state == current_state:
            return
        task = self._task_for_worker_transition(previous_worker, worker)
        if task is None:
            return
        payload = {
            "worker": worker,
            "previousHealth": previous_health,
            "health": current_health,
            "transition": f"{previous_state}->{current_state}",
        }
        self._publish(
            session_id=self._string_or_empty(task.get("sessionId")),
            task_id=task["id"],
            event_type="collab.worker.health.changed",
            payload=payload,
        )

    def _task_for_worker_transition(
        self,
        previous_worker: dict[str, Any] | None,
        worker: dict[str, Any],
    ) -> dict[str, Any] | None:
        for candidate in (worker, previous_worker):
            if not isinstance(candidate, dict):
                continue
            task = self._task_for_id(candidate.get("currentTaskId"))
            if task is not None:
                return task
        return None

    def _publish(
        self,
        *,
        session_id: str,
        task_id: str,
        event_type: str,
        payload: dict[str, Any],
        visibility: str = "panel",
    ) -> None:
        event = RuntimeEvent(
            event_id=self._store.new_id("evt"),
            session_id=session_id,
            task_id=task_id,
            type=event_type,
            ts=self._store.now(),
            payload=payload,
            visibility=visibility,
        )
        self._event_bus.publish(event)

    def _string_or_none(self, value: Any) -> str | None:
        return value if isinstance(value, str) and value else None

    def _string_or_empty(self, value: Any) -> str:
        return value if isinstance(value, str) else ""
