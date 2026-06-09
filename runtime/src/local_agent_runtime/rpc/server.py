from __future__ import annotations

import json
import logging
import queue
import threading
import time
from typing import Any, Callable, TextIO

from ..models import RpcEnvelope, RuntimeEvent
from ..yuanbao_event_adapter import (
    to_yuanbao_output_frames,
    to_yuanbao_server_message,
    yuanbao_message_from_event_payload,
)
from ..services.collaboration_service import CollaborationService
from ..services.replay_service import ReplayService
from ..services.command_background import cancel_background_command, get_background_command_event_bridge, get_background_command_service
from ..services.schedule_service import ScheduleService
from ..services.task_revert_service import revert_task_changes
from ..store.sqlite_store import SQLiteStore
from ..memory.store import MemoryStore
from ..memory.types import MemoryKind

RpcHandler = Callable[[dict[str, Any]], dict[str, Any]]
logger = logging.getLogger(__name__)


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

    Responses and Yuanbao-compatible event frames share stdout as JSON lines.
    RPC responses carry the normal JSON-RPC envelope; event lines expose flat
    ``{"kind": "yuanbao_message", "payload": ...}`` frames.
    """

    def __init__(
        self,
        orchestrator: Any,
        store: Any,
        event_bus: Any,
        *,
        worktree_service: Any = None,
        shutdown_callbacks: list[Callable[[], None]] | None = None,
    ) -> None:
        self._orchestrator = orchestrator
        self._store = store
        self._event_bus = event_bus
        self._worktree_service = worktree_service
        self._shutdown_callbacks = list(shutdown_callbacks or [])
        self._schedule = ScheduleService(store)
        self._collaboration = CollaborationService(store, event_bus)
        self._replay = ReplayService(store)
        self._writer: TextIO | None = None
        self._writer_lock = threading.Lock()
        self._event_write_queue: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=1000)
        self._event_writer_thread: threading.Thread | None = None
        self._handlers: dict[str, RpcHandler] = {
            "workspace.open": self._orchestrator.open_workspace,
            "workspace.focus.update": self._store.update_workspace_focus,
            "workspace.memory.clear": self._store.clear_workspace_memory,
            "workspace.memory.init": self._store.init_workspace_memory,
            "session.create": self._orchestrator.create_session,
            "session.get": self._store.get_session,
            "session.list": self._store.list_sessions,
            "session.update": self._session_update,
            "session.delete": self._delete_session,
            "session.compact": self._orchestrator.compact_session,
            "session.branch": self._store.branch_session,
            "session.truncate": self._store.truncate_session,
            "message.send": self._orchestrator.send_message,
            "message.list": self._store.list_messages,
            "message.delete": self._store.delete_message,
            "worker.run_child_task": self._orchestrator.run_child_task,
            "task.get": self._store.get_task,
            "task.list": self._store.list_tasks,
            "task.cancel": self._orchestrator.cancel_task,
            "task.pause": self._orchestrator.pause_task,
            "task.resume": self._orchestrator.resume_task,
            "task.revertChanges": self._task_revert_changes,
            "approval.submit": self._orchestrator.submit_approval,
            "approval.allowAlways": self._orchestrator.allow_approval_always,
            "config.get": self._store.get_config,
            "config.update": self._store.update_config,
            "permission.rule.clear": self._store.clear_permission_rule,
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
            "storage.stats": self._store.storage_stats,
            "storage.cleanup": self._store.storage_cleanup,
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
            "agent.profile.list": self._orchestrator.agent_profile_list,
            "agent.profile.create": self._orchestrator.agent_profile_create,
            "agent.profile.update": self._orchestrator.agent_profile_update,
            "agent.profile.delete": self._orchestrator.agent_profile_delete,
            "agent.profile.validate": self._orchestrator.agent_profile_validate,
            "agent.profile.previewTools": self._orchestrator.agent_profile_preview_tools,
            "mcp.server.list": self._orchestrator.mcp_server_list,
            "mcp.server.create": self._orchestrator.mcp_server_create,
            "mcp.server.update": self._orchestrator.mcp_server_update,
            "mcp.server.delete": self._orchestrator.mcp_server_delete,
            "mcp.tools.refresh": self._orchestrator.mcp_tools_refresh,
            "events.after": self._events_after,
            "events.yuanbaoAfter": self._yuanbao_events_after,
            "events.yuanbaoTeamSnapshot": self._yuanbao_team_snapshot,
            "provider_turn.list": self._provider_turn_list,
            "context_snapshot.list": self._context_snapshot_list,
            "context_snapshot.get": self._context_snapshot_get,
            "context.budget": self._store.get_context_budget,
            "autonomy.report": self._store.get_autonomy_report,
            "runtime.ping": self._runtime_ping,
            "runtime.status": self._runtime_status,
            "hook.create": self._store.create_hook,
            "hook.update": self._store.update_hook,
            "hook.delete": self._store.delete_hook,
            "hook.list": self._store.list_hooks,
            "hook.get": self._store.get_hook,
            "hook.listExecutions": self._store.list_hook_executions,
            "worktree.create": self._worktree_create,
            "worktree.get": self._store.get_worktree,
            "worktree.getByTask": self._store.get_worktree_by_task,
            "worktree.list": self._store.list_worktrees,
            "worktree.requestMergeApproval": self._worktree_request_merge_approval,
            "worktree.status": self._worktree_status,
            "worktree.diff": self._worktree_diff,
            "worktree.cleanup": self._worktree_cleanup,
            "worktree.merge": self._worktree_merge,
            "worktree.update": self._store.update_worktree,
            "worktree.delete": self._store.delete_worktree,
            "scope.checkDispatch": self._store.check_dispatch_scope,
            "scope.conflictHistory": self._store.list_scope_conflict_checks,
            "replay.audit": self._replay.audit_replay,
            "replay.dryRun": self._replay.dry_run_replay,
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
        self._start_event_writer()
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
        try:
            self._orchestrator.graceful_shutdown(timeout=timeout)
        finally:
            callbacks, self._shutdown_callbacks = self._shutdown_callbacks, []
            for callback in callbacks:
                try:
                    callback()
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Shutdown callback failed: %s", exc, exc_info=True)

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
        self._queue_event_payload(self._event_bus.as_payload(event))

    def _emit_bridge_event(self, event: dict[str, Any]) -> None:
        self._queue_event_payload(event)

    def _queue_event_payload(self, event: dict[str, Any]) -> None:
        try:
            self._event_write_queue.put_nowait(event)
        except queue.Full:
            # UI event delivery must never block the agent runtime. The durable
            # trace mirror still records events for replay/inspection.
            return

    def _start_event_writer(self) -> None:
        if self._event_writer_thread is not None:
            return

        def _worker() -> None:
            while True:
                event = self._event_write_queue.get()
                if event is None:
                    return
                self._write_event_payload(event)

        self._event_writer_thread = threading.Thread(
            target=_worker,
            name="runtime-event-writer",
            daemon=True,
        )
        self._event_writer_thread.start()

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

    def _session_update(self, params: dict[str, Any]) -> dict[str, Any]:
        before: dict[str, Any] | None = None
        session_id = params.get("sessionId") or params.get("session_id")
        if isinstance(session_id, str) and session_id.strip():
            try:
                before = self._store.get_session({"sessionId": session_id.strip()})["session"]
            except Exception:  # noqa: BLE001
                before = None
        result = self._store.update_session(params)
        session = result.get("session")
        changed_fields = self._session_update_changed_fields(before, session) if isinstance(session, dict) else []
        if isinstance(session, dict) and changed_fields:
            event = RuntimeEvent(
                event_id=self._store.new_id("evt"),
                session_id=str(session.get("id") or session_id or ""),
                task_id=str(session.get("id") or session_id or ""),
                type="session.updated",
                ts=self._store.now(),
                payload={
                    "sessionId": session.get("id"),
                    "title": session.get("title"),
                    "status": session.get("status"),
                    "summary": session.get("summary"),
                    "changedFields": changed_fields,
                },
                visibility="panel",
            )
            self._event_bus.publish(event)
        return result

    def _session_update_changed_fields(self, before: dict[str, Any] | None, after: dict[str, Any]) -> list[str]:
        if before is None:
            return ["title", "status", "summary"]
        fields: list[str] = []
        for field in ("title", "status", "summary"):
            if before.get(field) != after.get(field):
                fields.append(field)
        return fields

    # -- Worktree RPCs ---------------------------------------------------------

    def _require_worktree_service(self) -> Any:
        if self._worktree_service is None:
            raise ValueError("WorktreeService not configured")
        return self._worktree_service

    def _worktree_create(self, params: dict[str, Any]) -> dict[str, Any]:
        if self._worktree_service is not None:
            return self._worktree_service.create_for_task(params)
        return self._store.create_worktree(params)

    def _worktree_status(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._require_worktree_service().get_status(params)

    def _worktree_diff(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._require_worktree_service().get_diff(params)

    def _worktree_request_merge_approval(self, params: dict[str, Any]) -> dict[str, Any]:
        if hasattr(self._orchestrator, "request_worktree_merge_approval"):
            return self._orchestrator.request_worktree_merge_approval(params)
        return self._require_worktree_service().request_merge_approval(params)

    def _worktree_cleanup(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._require_worktree_service().cleanup(params)

    def _worktree_merge(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._require_worktree_service().merge(params)

    # -- Task mutation RPCs ----------------------------------------------------

    def _task_revert_changes(self, params: dict[str, Any]) -> dict[str, Any]:
        return revert_task_changes(self._store, params)

    # -- Command RPCs ----------------------------------------------------------

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

    def _flat_messages_after(
        self,
        params: dict[str, Any],
        *,
        extractor: Callable[[Any], dict[str, Any] | None],
    ) -> dict[str, Any]:
        session_id = params.get("sessionId") or params.get("session_id", "")
        after_seq = int(params.get("afterSeq", params.get("after_seq", 0)))
        message_limit = max(1, min(int(params.get("limit", 500)), 5000))
        event_page_limit = max(1, min(int(params.get("eventLimit", params.get("event_limit", 500))), 500))
        max_pages = max(1, min(int(params.get("maxPages", params.get("max_pages", 40))), 100))
        messages: list[dict[str, Any]] = []
        last_seq = after_seq
        truncated = False

        for _page in range(max_pages):
            result = self._store.events_after(session_id, last_seq, limit=event_page_limit)
            events = result.get("events") if isinstance(result, dict) else []
            if not isinstance(events, list) or not events:
                truncated = False
                break
            for event in events:
                if not isinstance(event, dict):
                    continue
                sequence = event.get("sequence")
                if isinstance(sequence, (int, float)) and not isinstance(sequence, bool):
                    last_seq = max(last_seq, int(sequence))
                message = extractor(event)
                if message is None:
                    continue
                messages.append(message)
                if len(messages) >= message_limit:
                    return {
                        "messages": messages,
                        "lastSeq": last_seq,
                        "truncated": True,
                    }
            truncated = bool(result.get("truncated")) if isinstance(result, dict) else False
            if not truncated:
                break
        else:
            truncated = True

        return {
            "messages": messages,
            "lastSeq": last_seq,
            "truncated": truncated,
        }

    def _yuanbao_events_after(self, params: dict[str, Any]) -> dict[str, Any]:
        """Fetch Yuanbao flat ServerMessages after a trace sequence."""

        return self._flat_messages_after(params, extractor=yuanbao_message_from_event_payload)

    def _yuanbao_team_snapshot(self, params: dict[str, Any]) -> dict[str, Any]:
        """Fetch current team flat ServerMessages for adapter reconnects."""
        return self._collaboration.team_snapshot_messages(params)

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

    def _runtime_ping(self, params: dict[str, Any]) -> dict[str, Any]:
        """Return Yuanbao-compatible liveness messages for stdio adapters."""
        session_id = params.get("sessionId") or params.get("session_id") or "runtime"
        task_id = params.get("taskId") or params.get("task_id") or "runtime"
        if not isinstance(session_id, str) or not session_id.strip():
            session_id = "runtime"
        if not isinstance(task_id, str) or not task_id.strip():
            task_id = "runtime"
        session_id = session_id.strip()
        task_id = task_id.strip()
        now = int(time.time() * 1000)
        ping_events = [
            RuntimeEvent(
                event_id=f"evt_ping_connected_{now}",
                session_id=session_id,
                task_id=task_id,
                type="connected",
                ts=now,
                payload={"sessionId": session_id},
            ),
            RuntimeEvent(
                event_id=f"evt_ping_pong_{now}",
                session_id=session_id,
                task_id=task_id,
                type="pong",
                ts=now,
                payload={},
            ),
        ]
        yuanbao_messages = [
            message for event in ping_events if (message := to_yuanbao_server_message(event)) is not None
        ]
        return {
            "ok": True,
            "transport": "json-rpc-stdio",
            "yuanbaoMessages": yuanbao_messages,
            "connected": yuanbao_messages[0] if yuanbao_messages else None,
            "pong": yuanbao_messages[1] if len(yuanbao_messages) > 1 else None,
        }

    def _runtime_status(self, params: dict[str, Any]) -> dict[str, Any]:
        """Return a UI-friendly snapshot of the current runtime state."""
        task_id = params.get("taskId") or params.get("task_id")
        session_id = params.get("sessionId") or params.get("session_id")
        if not isinstance(task_id, str) and not isinstance(session_id, str):
            raise ValueError("taskId or sessionId is required")

        task: dict[str, Any] | None = None
        if isinstance(task_id, str) and task_id.strip():
            task = self._store.get_task({"taskId": task_id.strip()})["task"]
            session_id = task["sessionId"]
        elif isinstance(session_id, str) and session_id.strip():
            tasks = self._store.list_tasks({"sessionId": session_id.strip()}).get("tasks", [])
            task = self._select_runtime_status_task(tasks)
            if task is None:
                session = self._store.get_session({"sessionId": session_id.strip()})["session"]
                return {
                    "session": session,
                    "currentTask": None,
                    "permissions": self._runtime_permissions(),
                    "provider": {
                        "latestTurn": None,
                        "streaming": {
                            "isStreaming": False,
                            "fellBackToNonStream": False,
                        },
                    },
                    "commands": [],
                    "worktree": {
                        "autoBindWriteTasks": bool((self._store.get_config({})["config"].get("worktree") or {}).get("autoBindWriteTasks")),
                        "active": None,
                    },
                    "memory": {
                        "recalled": [],
                        "candidates": [],
                        "candidateCount": 0,
                    },
                    "signals": {
                        "untrustedContent": [],
                    },
                    "context": {
                        "latestSnapshot": None,
                    },
                    "controls": {
                        "canPause": False,
                        "canResume": False,
                        "canCancel": False,
                    },
                }
            session_id = task["sessionId"]
        else:
            raise ValueError("taskId or sessionId is required")

        assert task is not None
        session = self._store.get_session({"sessionId": session_id})["session"]
        commands = self._store.list_command_logs({"taskId": task["id"], "limit": 5}).get("commandLogs", [])
        turns = self._store.list_provider_turns(task["id"])
        latest_turn = turns[-1] if turns else None
        snapshots = self._store.list_context_snapshots(task["id"])
        latest_snapshot = self._store._serialize_context_snapshot(snapshots[-1]) if snapshots else None  # noqa: SLF001
        worktree = self._store.get_worktree_by_task({"taskId": task["id"]}).get("worktree")
        memory_entries = self._runtime_recalled_memory_entries(latest_snapshot)
        memory_candidates = self._memory_candidates({"sessionId": session_id, "workspaceId": session.get("workspaceId"), "limit": 10}).get("entries", [])
        config = self._store.get_config({})["config"]
        worktree_config = config.get("worktree") or {}

        transport = str((latest_turn or {}).get("response_transport") or "")
        return {
            "session": session,
            "currentTask": task,
            "permissions": self._runtime_permissions(),
            "provider": {
                "latestTurn": self._runtime_turn_summary(latest_turn),
                "streaming": {
                    "isStreaming": transport == "stream",
                    "fellBackToNonStream": transport == "fallback_non_stream",
                },
            },
            "commands": commands,
            "worktree": {
                "autoBindWriteTasks": bool(worktree_config.get("autoBindWriteTasks")),
                "active": worktree,
            },
            "memory": {
                "recalled": memory_entries,
                "candidates": memory_candidates,
                "candidateCount": len(memory_candidates),
            },
            "signals": {
                "untrustedContent": self._extract_untrusted_signals(latest_snapshot),
            },
            "context": {
                "latestSnapshot": latest_snapshot,
            },
            "controls": {
                "canPause": task.get("status") == "running",
                "canResume": task.get("status") == "paused",
                "canCancel": task.get("status") in {"running", "paused", "queued", "waiting_approval"},
            },
        }

    def _select_runtime_status_task(self, tasks: list[dict[str, Any]]) -> dict[str, Any] | None:
        if not tasks:
            return None
        for status in ("running", "paused", "waiting_approval", "queued"):
            for task in tasks:
                if task.get("status") == status:
                    return task
        return tasks[0]

    def _runtime_permissions(self) -> dict[str, Any]:
        config = self._store.get_config({})["config"]
        policy = config.get("policy") or {}
        autonomy = self._store._active_profile_from_config(config, "autonomy") or {}  # noqa: SLF001
        return {
            "approvalMode": str(policy.get("approvalMode") or "on_write_or_command"),
            "allowFileWrite": autonomy.get("allowFileWrite"),
            "allowShell": autonomy.get("allowShell"),
            "allowNetwork": bool(autonomy.get("allowNetwork")),
            "autonomyProfileId": autonomy.get("id"),
            "autonomyLevel": autonomy.get("level"),
        }

    def _runtime_turn_summary(self, turn: dict[str, Any] | None) -> dict[str, Any] | None:
        if turn is None:
            return None
        return {
            "id": turn.get("id"),
            "turnIndex": turn.get("turn_index"),
            "status": turn.get("status"),
            "model": turn.get("model"),
            "responseTransport": turn.get("response_transport"),
            "responseFinishReason": turn.get("response_finish_reason"),
            "toolCallCount": turn.get("response_tool_call_count"),
            "cacheUsage": turn.get("cacheUsage") or {"cacheHit": False, "cachedTokens": 0},
            "toolPolicyExplanation": turn.get("toolPolicyExplanation"),
            "completedAt": turn.get("completed_at"),
        }

    def _runtime_recalled_memory_entries(self, snapshot: dict[str, Any] | None) -> list[dict[str, Any]]:
        if not isinstance(snapshot, dict):
            return []
        memory_ids = snapshot.get("memoryIds")
        if not isinstance(memory_ids, list):
            return []
        entries: list[dict[str, Any]] = []
        for memory_id in memory_ids[:10]:
            if not isinstance(memory_id, str) or not memory_id.strip():
                continue
            entry = self._memory_store().retrieve(memory_id, touch=False)
            if entry is None:
                continue
            entries.append(_entry_to_dict(entry))
        return entries

    def _extract_untrusted_signals(self, snapshot: dict[str, Any] | None) -> list[dict[str, Any]]:
        if not isinstance(snapshot, dict):
            return []
        tool_policy = snapshot.get("toolPolicyDecision")
        if not isinstance(tool_policy, dict):
            return []
        details = tool_policy.get("decisionDetails")
        if not isinstance(details, list):
            return []
        seen: set[tuple[str, str]] = set()
        signals: list[dict[str, Any]] = []
        for item in details:
            if not isinstance(item, dict):
                continue
            raw_signals = item.get("untrustedContentSignals")
            if not isinstance(raw_signals, list):
                continue
            for signal in raw_signals:
                if not isinstance(signal, dict):
                    continue
                source = signal.get("contentSource")
                trust = signal.get("contentTrust")
                if not isinstance(source, str) or not isinstance(trust, str):
                    continue
                key = (source, trust)
                if key in seen:
                    continue
                seen.add(key)
                signals.append({
                    "contentSource": source,
                    "contentTrust": trust,
                })
        return signals

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
            for frame in to_yuanbao_output_frames(payload):
                self._writer.write(json.dumps(frame, ensure_ascii=False) + "\n")
            self._writer.flush()
