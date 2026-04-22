from __future__ import annotations

import json
from typing import Any, Callable, TextIO

from ..models import RpcEnvelope

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
        self._writer: TextIO | None = None
        self._handlers: dict[str, RpcHandler] = {
            "workspace.open": self._orchestrator.open_workspace,
            "session.create": self._orchestrator.create_session,
            "session.get": self._store.get_session,
            "session.list": self._store.list_sessions,
            "message.send": self._orchestrator.send_message,
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
        }
        if hasattr(self._store, "append_runtime_event"):
            self._event_bus.subscribe(self._store.append_runtime_event)

    def serve(self, stdin: TextIO, stdout: TextIO) -> None:
        self._writer = stdout
        self._event_bus.subscribe(self._emit_event)
        for raw_line in stdin:
            line = raw_line.strip()
            if not line:
                continue
            response = self.handle_line(line)
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
                code="INTERNAL_ERROR",
                message=str(exc),
            )

        return {
            "jsonrpc": "2.0",
            "id": envelope.id,
            "result": result,
        }

    def _error_response(self, request_id: str, code: str, message: str) -> dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {
                "code": code,
                "message": message,
                "retryable": False,
            },
        }

    def _emit_event(self, event: Any) -> None:
        if self._writer is None:
            return

        self._writer.write(
            json.dumps(
                {
                    "kind": "event",
                    "payload": self._event_bus.as_payload(event),
                },
                ensure_ascii=False,
            )
            + "\n"
        )
        self._writer.flush()
