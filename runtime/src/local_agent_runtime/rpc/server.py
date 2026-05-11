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
from ..memory.store import MemoryStore
from ..memory.types import MemoryKind

RpcHandler = Callable[[dict[str, Any]], dict[str, Any]]


def _entry_to_dict(entry: Any) -> dict[str, Any]:
    """Serialize a MemoryEntry to a JSON-safe dict."""
    return {
        "id": entry.id,
        "kind": entry.kind.value,
        "content": entry.content,
        "createdAt": entry.created_at,
        "accessedAt": entry.accessed_at,
        "accessCount": entry.access_count,
        "sessionId": entry.session_id,
        "workspaceId": entry.workspace_id,
        "keywords": entry.keywords,
        "metadata": entry.metadata,
    }


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
            "session.compact": self._orchestrator.compact_session,
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
            "config.effective": self._orchestrator.config_effective,
            "prompt.preview": self._orchestrator.prompt_preview,
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
            "decision.list": self._store.list_decision_events,
            "proposal.list": self._store.list_proposals,
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
            "skill.import": self._orchestrator.skill_import,
            "mcp.server.list": self._orchestrator.mcp_server_list,
            "mcp.server.create": self._orchestrator.mcp_server_create,
            "mcp.server.update": self._orchestrator.mcp_server_update,
            "mcp.server.delete": self._orchestrator.mcp_server_delete,
            "mcp.tools.refresh": self._orchestrator.mcp_tools_refresh,
            "events.after": self._events_after,
            "provider_turn.list": self._provider_turn_list,
            "context_snapshot.list": self._context_snapshot_list,
            "context_snapshot.get": self._context_snapshot_get,
            "context.budget": self._store.get_context_budget,
            "autonomy.report": self._store.get_autonomy_report,
            "hook.create": self._store.create_hook,
            "hook.update": self._store.update_hook,
            "hook.delete": self._store.delete_hook,
            "hook.list": self._store.list_hooks,
            "hook.get": self._store.get_hook,
            "hook.listExecutions": self._store.list_hook_executions,
            "memory.list": self._memory_list,
            "memory.get": self._memory_get,
            "memory.edit": self._memory_edit,
            "memory.delete": self._memory_delete,
            "memory.pin": self._memory_pin,
            "memory.unpin": self._memory_unpin,
            "memory.promote": self._memory_promote,
            "memory.candidates": self._memory_candidates,
            "feature.list": self._feature_list,
            "feature.set": self._feature_set,
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

    def _events_after(self, params: dict[str, Any]) -> dict[str, Any]:
        """Fetch trace events after a given sequence number."""
        session_id = params.get("sessionId") or params.get("session_id", "")
        after_seq = int(params.get("afterSeq", params.get("after_seq", 0)))
        limit = int(params.get("limit", 500))
        return self._store.events_after(session_id, after_seq, limit=min(limit, 500))

    def _provider_turn_list(self, params: dict[str, Any]) -> dict[str, Any]:
        """List provider turns for a task."""
        task_id = params.get("taskId") or params.get("task_id", "")
        turns = self._store.list_provider_turns(task_id)
        return {"turns": turns}

    def _context_snapshot_list(self, params: dict[str, Any]) -> dict[str, Any]:
        """List context snapshots for a task."""
        task_id = params.get("taskId") or params.get("task_id", "")
        snapshots = self._store.list_context_snapshots(task_id)
        return {"snapshots": snapshots}

    def _context_snapshot_get(self, params: dict[str, Any]) -> dict[str, Any]:
        """Get a single context snapshot by ID."""
        snapshot_id = params.get("snapshotId") or params.get("snapshot_id", "")
        snapshot = self._store.get_context_snapshot(snapshot_id)
        return {"snapshot": snapshot}

    # -- Memory management RPCs --

    def _memory_store(self) -> MemoryStore:
        return MemoryStore(self._store)

    def _memory_list(self, params: dict[str, Any]) -> dict[str, Any]:
        """List memory entries with optional filters."""
        mem_store = self._memory_store()
        kind = params.get("kind")
        entries = mem_store.query_all(
            workspace_id=params.get("workspaceId") or None,
            session_id=params.get("sessionId") or None,
            kind=MemoryKind(kind) if kind else None,
            limit=int(params.get("limit", 100)),
        )
        return {"entries": [_entry_to_dict(e) for e in entries]}

    def _memory_get(self, params: dict[str, Any]) -> dict[str, Any]:
        """Retrieve a single memory entry by ID."""
        entry_id = params.get("entryId") or params.get("entry_id", "")
        entry = self._memory_store().retrieve(entry_id, touch=False)
        if entry is None:
            raise ValueError(f"Memory entry not found: {entry_id}")
        return {"entry": _entry_to_dict(entry)}

    def _memory_edit(self, params: dict[str, Any]) -> dict[str, Any]:
        """Edit a memory entry's content, keywords, metadata, or kind."""
        entry_id = params.get("entryId") or params.get("entry_id", "")
        if not entry_id:
            raise ValueError("entryId is required")
        kind_str = params.get("kind")
        updated = self._memory_store().update(
            entry_id,
            content=params.get("content"),
            keywords=params.get("keywords"),
            metadata=params.get("metadata"),
            kind=MemoryKind(kind_str) if kind_str else None,
        )
        if updated is None:
            raise ValueError(f"Memory entry not found: {entry_id}")
        return {"entry": _entry_to_dict(updated)}

    def _memory_delete(self, params: dict[str, Any]) -> dict[str, Any]:
        """Delete a memory entry."""
        entry_id = params.get("entryId") or params.get("entry_id", "")
        if not entry_id:
            raise ValueError("entryId is required")
        deleted = self._memory_store().delete(entry_id)
        return {"deleted": deleted}

    def _memory_pin(self, params: dict[str, Any]) -> dict[str, Any]:
        """Pin a memory entry for priority recall."""
        entry_id = params.get("entryId") or params.get("entry_id", "")
        if not entry_id:
            raise ValueError("entryId is required")
        updated = self._memory_store().toggle_pin(entry_id, pinned=True)
        if updated is None:
            raise ValueError(f"Memory entry not found: {entry_id}")
        return {"entry": _entry_to_dict(updated)}

    def _memory_unpin(self, params: dict[str, Any]) -> dict[str, Any]:
        """Unpin a memory entry."""
        entry_id = params.get("entryId") or params.get("entry_id", "")
        if not entry_id:
            raise ValueError("entryId is required")
        updated = self._memory_store().toggle_pin(entry_id, pinned=False)
        if updated is None:
            raise ValueError(f"Memory entry not found: {entry_id}")
        return {"entry": _entry_to_dict(updated)}

    def _memory_promote(self, params: dict[str, Any]) -> dict[str, Any]:
        """Promote a memory candidate to LONG_TERM.

        Optionally accepts a list of entryIds for batch promotion.
        """
        entry_ids = params.get("entryIds") or []
        single_id = params.get("entryId") or params.get("entry_id", "")
        if single_id:
            entry_ids = [single_id]
        if not entry_ids:
            raise ValueError("entryId or entryIds is required")

        mem_store = self._memory_store()
        target_kind = MemoryKind.LONG_TERM
        kind_str = params.get("targetKind")
        if kind_str:
            target_kind = MemoryKind(kind_str)

        if len(entry_ids) == 1:
            updated = mem_store.promote(entry_ids[0], target_kind)
            if updated is None:
                raise ValueError(f"Memory entry not found: {entry_ids[0]}")
            return {"entry": _entry_to_dict(updated)}

        count = mem_store.promote_batch(entry_ids, target_kind)
        return {"promoted": count}

    def _memory_candidates(self, params: dict[str, Any]) -> dict[str, Any]:
        """List memory candidates (WORKING/SESSION entries eligible for promotion).

        Candidates are entries with source=supplement or category in
        (user_preference, project_convention) that have not yet been promoted
        to LONG_TERM.
        """
        mem_store = self._memory_store()
        workspace_id = params.get("workspaceId") or None
        session_id = params.get("sessionId") or None
        limit = int(params.get("limit", 50))

        # Query WORKING + SESSION memories for the given scope
        candidates: list[MemoryEntry] = []
        for kind in (MemoryKind.WORKING, MemoryKind.SESSION):
            entries = mem_store.query_all(
                workspace_id=workspace_id,
                session_id=session_id,
                kind=kind,
                limit=limit,
            )
            candidates.extend(entries)

        # Filter to only include entries with candidate markers
        _CANDIDATE_SOURCES = {"supplement", "user_message"}
        _CANDIDATE_CATEGORIES = {"user_preference", "project_convention"}
        filtered: list[MemoryEntry] = []
        for entry in candidates:
            meta = entry.metadata or {}
            if meta.get("source") in _CANDIDATE_SOURCES or meta.get("category") in _CANDIDATE_CATEGORIES:
                filtered.append(entry)

        return {"entries": [_entry_to_dict(e) for e in filtered[:limit]]}

    # -- Feature flags --

    def _feature_list(self, _params: dict[str, Any]) -> dict[str, Any]:
        """List all feature flags and their values."""
        return self._store.list_feature_flags()

    def _feature_set(self, params: dict[str, Any]) -> dict[str, Any]:
        """Set a feature flag value."""
        key = params.get("key")
        if not isinstance(key, str) or not key.strip():
            raise ValueError("key is required")
        value = bool(params.get("value", False))
        return self._store.set_feature_flag(key.strip(), value)

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
