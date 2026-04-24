from __future__ import annotations

import json
import threading
from typing import Any, Callable, TextIO

from ..models import RpcEnvelope
from ..services.collaboration_service import CollaborationService
from ..services.command_background import get_background_command_event_bridge
from ..services.schedule_service import ScheduleService

RpcHandler = Callable[[dict[str, Any]], dict[str, Any]]


class JsonRpcServer:
    """Thin JSON-RPC 2.0 server over stdio.

    Responses and events share stdout as JSON lines. RPC responses carry the
    normal JSON-RPC envelope; event lines are wrapped as
    ``{"kind": "event", "payload": ...}``.
    """

    def __init__(self, orchestrator: Any, store: Any, event_bus: Any) -> None:
        self._orchestrator = orchestrator
        self._store = store
        self._event_bus = event_bus
        self._schedule = ScheduleService(store)
        self._collaboration = CollaborationService(store, event_bus)
        self._writer: TextIO | None = None
        self._writer_lock = threading.Lock()
        self._handlers: dict[str, RpcHandler] = {
            "workspace.open": self._orchestrator.open_workspace,
            "session.create": self._orchestrator.create_session,
            "session.get": self._store.get_session,
            "session.list": self._store.list_sessions,
            "message.send": self._orchestrator.send_message,
            "worker.run_child_task": self._orchestrator.run_child_task,
            "task.get": self._store.get_task,
            "task.list": self._store.list_tasks,
            "task.cancel": self._orchestrator.cancel_task,
            "task.pause": self._orchestrator.pause_task,
            "task.resume": self._orchestrator.resume_task,
            "approval.submit": self._orchestrator.submit_approval,
            "config.get": self._store.get_config,
            "config.update": self._store.update_config,
            "provider.test": self._orchestrator.test_provider,
            "diff.get": self._store.get_patch,
            "command_log.get": self._store.get_command_log,
            "trace.list": self._store.list_trace_events,
            "schedule.create": self._schedule.create,
            "schedule.list": self._schedule.list,
            "schedule.update": self._schedule.update,
            "schedule.toggle": self._schedule.toggle,
            "schedule.run_now": self._schedule.run_now,
            "schedule.logs": self._schedule.logs,
            "collab.task.create": self._collaboration.create_collaboration_task,
            "collab.task.get": self._collaboration.get_collaboration_task,
            "collab.task.list": self._collaboration.list_collaboration_tasks,
            "collab.task.update": self._collaboration.update_collaboration_task,
            "collab.task.claim": self._collaboration.claim_collaboration_task,
            "collab.task.complete": self._collaboration.complete_collaboration_task,
            "collab.task.fail": self._collaboration.fail_collaboration_task,
            "collab.task.release": self._collaboration.release_collaboration_task,
            "collab.worker.upsert": self._collaboration.upsert_agent_worker,
            "collab.worker.get": self._collaboration.get_agent_worker,
            "collab.worker.list": self._collaboration.list_agent_workers,
            "collab.worker.heartbeat": self._collaboration.heartbeat_agent_worker,
            "collab.message.send": self._collaboration.send_agent_message,
            "collab.message.list": self._collaboration.list_agent_messages,
        }
        if hasattr(self._store, "append_runtime_event"):
            self._event_bus.subscribe(self._store.append_runtime_event)
        bridge = get_background_command_event_bridge(getattr(self._store, "database_path", ":memory:"))
        bridge.add_listener(self._emit_bridge_event)

    def serve(self, stdin: TextIO, stdout: TextIO) -> None:
        self._writer = stdout
        self._event_bus.subscribe(self._emit_event)
        for raw_line in stdin:
            line = raw_line.strip()
            if not line:
                continue
            response = self.handle_line(line)
            with self._writer_lock:
                stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
                stdout.flush()

    def handle_line(self, line: str) -> dict[str, Any]:
        envelope = RpcEnvelope(**json.loads(line))
        handler = self._handlers.get(envelope.method)
        if handler is None:
            return self._error_response(
                envelope.id,
                code="NOT_FOUND",
                message=f"Unsupported method: {envelope.method}",
            )

        try:
            result = handler(envelope.params)
        except Exception as exc:  # noqa: BLE001
            return self._error_response(
                envelope.id,
                code=str(getattr(exc, "code", "INTERNAL_ERROR")),
                message=str(exc),
                retryable=bool(getattr(exc, "retryable", False)),
            )

        return {
            "jsonrpc": "2.0",
            "id": envelope.id,
            "result": result,
        }

    def _error_response(self, request_id: str, code: str, message: str, retryable: bool = False) -> dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {
                "code": code,
                "message": message,
                "retryable": retryable,
            },
        }

    def _emit_event(self, event: Any) -> None:
        self._write_event_payload(self._event_bus.as_payload(event))

    def _emit_bridge_event(self, event: dict[str, Any]) -> None:
        self._write_event_payload(event)

    def _write_event_payload(self, payload: dict[str, Any]) -> None:
        if self._writer is None:
            return

        with self._writer_lock:
            self._writer.write(
                json.dumps(
                    {
                        "kind": "event",
                        "payload": payload,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
            self._writer.flush()
