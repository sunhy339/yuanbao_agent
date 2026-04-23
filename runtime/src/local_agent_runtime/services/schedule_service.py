from __future__ import annotations

from typing import Any


class ScheduleService:
    """Coordinates persisted scheduled tasks and run records."""

    def __init__(self, store: Any) -> None:
        self._store = store

    def create(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._store.create_scheduled_task(params)

    def list(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._store.list_scheduled_tasks(params)

    def update(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._store.update_scheduled_task(params)

    def toggle(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._store.toggle_scheduled_task(params)

    def logs(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._store.list_scheduled_task_runs(params)

    def run_now(self, params: dict[str, Any]) -> dict[str, Any]:
        task_id = params.get("taskId")
        if not isinstance(task_id, str) or not task_id.strip():
            raise ValueError("taskId is required")

        task = self._store.require_scheduled_task(task_id.strip())
        started_at = self._store.now()
        finished_at = self._store.now()
        run = self._store.create_scheduled_task_run(
            task_id=task["id"],
            status="completed",
            started_at=started_at,
            finished_at=finished_at,
            summary=(
                "Run now recorded for scheduled task; "
                "agent execution is not wired to scheduled jobs in this runtime yet."
            ),
        )
        return {"run": run, "task": self._store.require_scheduled_task(task["id"])}
