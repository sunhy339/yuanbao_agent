from __future__ import annotations

from typing import Any

from ..models import RuntimeEvent


class CollaborationService:
    """Thin event-publishing wrapper around collaboration store methods."""

    def __init__(self, store: Any, event_bus: Any) -> None:
        self._store = store
        self._event_bus = event_bus

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
        result = self._store.upsert_agent_worker(params)
        self._publish_worker_event(result, "collab.worker.upserted")
        return result

    def get_agent_worker(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._store.get_agent_worker(params)

    def list_agent_workers(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._store.list_agent_workers(params)

    def heartbeat_agent_worker(self, params: dict[str, Any]) -> dict[str, Any]:
        result = self._store.heartbeat_agent_worker(params)
        self._publish_worker_event(result, "collab.worker.heartbeat")
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
