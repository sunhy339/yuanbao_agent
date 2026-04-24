from __future__ import annotations

from typing import Any

from ..models import RuntimeEvent
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
    ) -> None:
        self._publish(
            session_id=session_id,
            task_id=task_id,
            event_type=event_type,
            payload=payload,
        )

    def _publish_task_event(self, result: dict[str, Any], event_type: str) -> None:
        task = result.get("task")
        if not isinstance(task, dict):
            return
        task_id = self._string_or_none(task.get("id"))
        if task_id is None:
            return
        self._publish(
            session_id=self._string_or_empty(task.get("sessionId")),
            task_id=task_id,
            event_type=event_type,
            payload=result,
        )

    def _publish_worker_event(self, result: dict[str, Any], event_type: str) -> None:
        worker = result.get("worker")
        if not isinstance(worker, dict):
            return
        task = self._task_for_id(worker.get("currentTaskId"))
        if task is None:
            return
        self._publish(
            session_id=self._string_or_empty(task.get("sessionId")),
            task_id=task["id"],
            event_type=event_type,
            payload=result,
        )

    def _publish_message_event(self, result: dict[str, Any], event_type: str) -> None:
        message = result.get("message")
        if not isinstance(message, dict):
            return
        task = self._task_for_id(message.get("taskId"))
        if task is None:
            return
        self._publish(
            session_id=self._string_or_empty(task.get("sessionId")),
            task_id=task["id"],
            event_type=event_type,
            payload=result,
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

    def _publish(self, *, session_id: str, task_id: str, event_type: str, payload: dict[str, Any]) -> None:
        event = RuntimeEvent(
            event_id=self._store.new_id("evt"),
            session_id=session_id,
            task_id=task_id,
            type=event_type,
            ts=self._store.now(),
            payload=payload,
        )
        self._event_bus.publish(event)

    def _string_or_none(self, value: Any) -> str | None:
        return value if isinstance(value, str) and value else None

    def _string_or_empty(self, value: Any) -> str:
        return value if isinstance(value, str) else ""
