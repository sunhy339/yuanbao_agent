from __future__ import annotations

import json
import threading
import time
from typing import Any, Callable, TextIO

from ..models import RpcEnvelope
from ..services.collaboration_service import CollaborationService
from ..services.command_background import cancel_background_command, get_background_command_event_bridge, get_background_command_service
from ..services.schedule_service import ScheduleService
from ..store.sqlite_store import SQLiteStore

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
            "workspace.focus.update": self._store.update_workspace_focus,
            "workspace.memory.clear": self._store.clear_workspace_memory,
            "session.create": self._orchestrator.create_session,
            "session.get": self._store.get_session,
            "session.list": self._store.list_sessions,
            "session.update": self._store.update_session,
            "session.delete": self._delete_session,
            "message.send": self._orchestrator.send_message,
            "message.list": self._store.list_messages,
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
            "command_log.list": self._store.list_command_logs,
            "command.cancel": self._cancel_command,
            "command.status": self._command_status,
            "command.list": self._command_list,
            "stats.summary": self._store.get_stats_summary,
            "stats.trace": self._store.get_trace_spans,
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
            "log.export": self._store.export_logs,
            "errors.list": self._store.list_errors,
            "metrics.list": self._store.list_metrics,
            "skill.list": self._orchestrator.skill_list,
            "skill.create": self._orchestrator.skill_create,
            "skill.update": self._orchestrator.skill_update,
            "skill.delete": self._orchestrator.skill_delete,
            "skill.usage": self._orchestrator.skill_usage,
            "mcp.server.list": self._orchestrator.mcp_server_list,
            "mcp.server.create": self._orchestrator.mcp_server_create,
            "mcp.server.update": self._orchestrator.mcp_server_update,
            "mcp.server.delete": self._orchestrator.mcp_server_delete,
            "mcp.tools.refresh": self._orchestrator.mcp_tools_refresh,
        }
        self._runtime_event_store_path = str(getattr(self._store, "database_path", ":memory:"))
        self._runtime_event_trace_store: SQLiteStore | None = None
        if hasattr(self._store, "append_runtime_event"):
            if self._runtime_event_store_path == ":memory:":
                self._event_bus.subscribe(self._store.append_runtime_event)
            else:
                self._runtime_event_trace_store = SQLiteStore(self._runtime_event_store_path)
                self._event_bus.subscribe(self._append_runtime_event)
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

    def initialize_mcp_servers(self) -> None:
        self._orchestrator.initialize_mcp_servers()

    def shutdown_mcp(self) -> None:
        self._orchestrator.shutdown_mcp()

    def graceful_shutdown(self, timeout: float = 10.0) -> None:
        self._orchestrator.graceful_shutdown(timeout=timeout)

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

    def _append_runtime_event(self, event: Any) -> dict[str, Any] | None:
        trace_store = self._runtime_event_trace_store
        if trace_store is None:
            return None
        return trace_store.append_runtime_event(event)

    def _delete_session(self, params: dict[str, Any]) -> dict[str, Any]:
        """Consolidate working memory, then delete the session."""
        session_id = params.get("sessionId") or params.get("session_id")
        if session_id and getattr(self._orchestrator, "_memory_manager", None) is not None:
            self._orchestrator._memory_manager.consolidate(session_id)
            self._orchestrator._memory_manager.forget_working(session_id)
        return self._store.delete_session(params)

    def _cancel_command(self, params: dict[str, Any]) -> dict[str, Any]:
        command_id = params.get("commandId") or params.get("command_id")
        if not isinstance(command_id, str) or not command_id.strip():
            raise ValueError("commandId is required")
        command_id = command_id.strip()
        command_log = self._store.get_command_log({"commandId": command_id})["commandLog"]
        if command_log["status"] != "running":
            return {"commandLog": command_log, "cancelled": False}

        cancelled = cancel_background_command(
            database_path=getattr(self._store, "database_path", ":memory:"),
            command_log_id=command_id,
        )
        if cancelled:
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                command_log = self._store.get_command_log({"commandId": command_id})["commandLog"]
                if command_log["status"] != "running":
                    break
                time.sleep(0.05)
        else:
            command_log = self._store.get_command_log({"commandId": command_id})["commandLog"]
        return {"commandLog": command_log, "cancelled": cancelled}

    def _command_status(self, params: dict[str, Any]) -> dict[str, Any]:
        """Query current status of a background command."""
        command_id = params.get("commandId") or params.get("command_id")
        if not isinstance(command_id, str) or not command_id.strip():
            raise ValueError("commandId is required")
        command_id = command_id.strip()

        db_path = getattr(self._store, "database_path", ":memory:")
        service = get_background_command_service(db_path)
        is_running = command_id in service.active_command_ids()

        command_log = self._store.get_command_log({"commandId": command_id})["commandLog"]
        return {"commandLog": command_log, "isRunning": is_running}

    def _command_list(self, _params: dict[str, Any]) -> dict[str, Any]:
        """List all currently running background commands."""
        db_path = getattr(self._store, "database_path", ":memory:")
        service = get_background_command_service(db_path)
        running_ids = service.active_command_ids()
        return {"runningCommandIds": running_ids, "count": len(running_ids)}

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
