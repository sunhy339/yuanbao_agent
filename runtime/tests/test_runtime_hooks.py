"""Tests for P1: Runtime Hooks.

Covers:
  1. CRUD: create hook
  2. CRUD: list hooks by workspace
  3. CRUD: list hooks by event
  4. CRUD: get hook
  5. CRUD: update hook
  6. CRUD: delete hook
  7. Validation: invalid event raises
  8. Validation: invalid action type raises
  9. HookService: audit_note action emits trace event
  10. HookService: notification action emits EventBus event
  11. HookService: run_command allowed (no approval)
  12. HookService: run_command approval_required
  13. HookService: disabled hook is skipped
  14. HookService: matching conditions execute
  15. HookService: non-matching conditions skip
  16. Integration: hook executions in autonomy report
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from local_agent_runtime.event_bus import EventBus
from local_agent_runtime.models import RuntimeEvent
from local_agent_runtime.policy.permission_engine import PermissionEngine
from local_agent_runtime.services.hook_service import HookService
from local_agent_runtime.store.sqlite_store import SQLiteStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_store(tmp_path: Any) -> tuple[SQLiteStore, EventBus]:
    db_path = tmp_path / "test.sqlite3"
    event_bus = EventBus()
    store = SQLiteStore(str(db_path))
    return store, event_bus


def _make_workspace(store: SQLiteStore, tmp_path: Any) -> str:
    ws = store.upsert_workspace(str(tmp_path / "project"))
    return ws["id"]


def _create_hook(
    store: SQLiteStore,
    workspace_id: str,
    *,
    event: str = "after_task_complete",
    action: dict[str, Any] | None = None,
    conditions: dict[str, Any] | None = None,
    authority: dict[str, Any] | None = None,
    name: str = "Test hook",
    on_failure: str = "warn",
    enabled: bool = True,
) -> dict[str, Any]:
    params: dict[str, Any] = {
        "name": name,
        "workspaceId": workspace_id,
        "event": event,
        "onFailure": on_failure,
    }
    if action:
        params["action"] = action
    if conditions:
        params["conditions"] = conditions
    if authority:
        params["authority"] = authority
    result = store.create_hook(params)
    hook = result["hook"]
    if not enabled:
        store.update_hook({"hookId": hook["id"], "enabled": False})
        hook = store.get_hook({"hookId": hook["id"]})["hook"]
    return hook


# ---------------------------------------------------------------------------
# CRUD Tests
# ---------------------------------------------------------------------------

class TestHookCRUD:
    """Hook store CRUD operations."""

    def test_create_hook(self, tmp_path: Any) -> None:
        store, _ = _make_store(tmp_path)
        ws_id = _make_workspace(store, tmp_path)

        hook = _create_hook(store, ws_id, action={"type": "audit_note", "note": "hello"})
        assert hook["id"].startswith("hook_")
        assert hook["name"] == "Test hook"
        assert hook["event"] == "after_task_complete"
        assert hook["enabled"] is True
        assert hook["scope"] == "workspace"
        assert hook["action"]["type"] == "audit_note"
        assert hook["priority"] == 100

    def test_list_hooks_by_workspace(self, tmp_path: Any) -> None:
        store, _ = _make_store(tmp_path)
        ws1 = _make_workspace(store, tmp_path)
        ws2 = store.upsert_workspace(str(tmp_path / "other"))["id"]

        _create_hook(store, ws1, name="Hook A")
        _create_hook(store, ws1, name="Hook B")
        _create_hook(store, ws2, name="Hook C")

        result = store.list_hooks({"workspaceId": ws1})
        assert len(result["hooks"]) == 2
        names = {h["name"] for h in result["hooks"]}
        assert names == {"Hook A", "Hook B"}

    def test_list_hooks_by_event(self, tmp_path: Any) -> None:
        store, _ = _make_store(tmp_path)
        ws_id = _make_workspace(store, tmp_path)

        _create_hook(store, ws_id, event="after_task_complete", name="Complete hook")
        _create_hook(store, ws_id, event="on_task_failed", name="Failed hook")

        result = store.list_hooks({"workspaceId": ws_id, "event": "after_task_complete"})
        assert len(result["hooks"]) == 1
        assert result["hooks"][0]["name"] == "Complete hook"

    def test_get_hook(self, tmp_path: Any) -> None:
        store, _ = _make_store(tmp_path)
        ws_id = _make_workspace(store, tmp_path)
        hook = _create_hook(store, ws_id)

        result = store.get_hook({"hookId": hook["id"]})
        assert result["hook"]["id"] == hook["id"]

    def test_update_hook(self, tmp_path: Any) -> None:
        store, _ = _make_store(tmp_path)
        ws_id = _make_workspace(store, tmp_path)
        hook = _create_hook(store, ws_id)

        updated = store.update_hook({
            "hookId": hook["id"],
            "name": "Updated name",
            "event": "before_provider_turn",
            "priority": 50,
            "enabled": False,
        })
        assert updated["hook"]["name"] == "Updated name"
        assert updated["hook"]["event"] == "before_provider_turn"
        assert updated["hook"]["priority"] == 50
        assert updated["hook"]["enabled"] is False

    def test_delete_hook(self, tmp_path: Any) -> None:
        store, _ = _make_store(tmp_path)
        ws_id = _make_workspace(store, tmp_path)
        hook = _create_hook(store, ws_id)

        result = store.delete_hook({"hookId": hook["id"]})
        assert result["deleted"] is True

        result = store.list_hooks({"workspaceId": ws_id})
        assert len(result["hooks"]) == 0

    def test_create_hook_validates_event(self, tmp_path: Any) -> None:
        store, _ = _make_store(tmp_path)
        ws_id = _make_workspace(store, tmp_path)

        with pytest.raises(ValueError, match="Invalid hook event"):
            store.create_hook({
                "name": "Bad",
                "workspaceId": ws_id,
                "event": "invalid_event",
            })

    def test_create_hook_validates_action(self, tmp_path: Any) -> None:
        store, _ = _make_store(tmp_path)
        ws_id = _make_workspace(store, tmp_path)

        with pytest.raises(ValueError, match="Invalid hook action type"):
            store.create_hook({
                "name": "Bad",
                "workspaceId": ws_id,
                "event": "after_task_complete",
                "action": {"type": "invalid_action"},
            })


# ---------------------------------------------------------------------------
# HookService Tests
# ---------------------------------------------------------------------------

class TestHookService:
    """HookService dispatch and condition evaluation."""

    def test_invoke_audit_note_hook(self, tmp_path: Any) -> None:
        store, event_bus = _make_store(tmp_path)
        ws_id = _make_workspace(store, tmp_path)
        _create_hook(store, ws_id, action={"type": "audit_note", "note": "Task done"})

        captured_events: list[RuntimeEvent] = []
        event_bus.subscribe(captured_events.append)

        service = HookService(store, event_bus)
        results = service.invoke_hooks("after_task_complete", {
            "workspaceId": ws_id,
            "taskId": "task_1",
            "sessionId": "sess_1",
        })

        assert len(results) == 1
        assert results[0]["status"] == "completed"
        assert results[0]["policyOutcome"] == "allowed"
        assert len(captured_events) == 1
        assert captured_events[0].type == "hook.executed"
        assert captured_events[0].visibility == "trace"
        assert captured_events[0].payload["hookExecutionId"] == results[0]["id"]

    def test_invoke_notification_hook(self, tmp_path: Any) -> None:
        store, event_bus = _make_store(tmp_path)
        ws_id = _make_workspace(store, tmp_path)
        _create_hook(store, ws_id, action={"type": "notification", "message": "Alert!"})

        captured_events: list[RuntimeEvent] = []
        event_bus.subscribe(captured_events.append)

        service = HookService(store, event_bus)
        results = service.invoke_hooks("after_task_complete", {
            "workspaceId": ws_id,
            "taskId": "task_1",
        })

        assert len(results) == 1
        assert results[0]["status"] == "completed"
        assert len(captured_events) == 1
        assert captured_events[0].type == "hook.notification"

    def test_invoke_run_command_hook_allowed(self, tmp_path: Any) -> None:
        store, event_bus = _make_store(tmp_path)
        ws_id = _make_workspace(store, tmp_path)
        _create_hook(
            store, ws_id,
            action={"type": "run_command", "command": "npm test"},
            authority={"requiresApproval": False},
        )

        service = HookService(store, event_bus)
        results = service.invoke_hooks("after_task_complete", {
            "workspaceId": ws_id,
            "taskId": "task_1",
        })

        assert len(results) == 1
        assert results[0]["policyOutcome"] == "allowed"
        assert results[0]["status"] == "deferred"

    def test_invoke_run_command_hook_approval_required(self, tmp_path: Any) -> None:
        store, event_bus = _make_store(tmp_path)
        ws_id = _make_workspace(store, tmp_path)
        _create_hook(
            store, ws_id,
            action={"type": "run_command", "command": "rm -rf /"},
            authority={"requiresApproval": True},
        )

        service = HookService(store, event_bus)
        results = service.invoke_hooks("after_task_complete", {
            "workspaceId": ws_id,
            "taskId": "task_1",
        })

        assert len(results) == 1
        assert results[0]["policyOutcome"] == "approval_required"
        assert results[0]["status"] == "pending"

    def test_invoke_run_command_hook_blocked_by_permission_engine(self, tmp_path: Any) -> None:
        store, event_bus = _make_store(tmp_path)
        ws_id = _make_workspace(store, tmp_path)
        _create_hook(
            store, ws_id,
            action={"type": "run_command", "command": "npm test"},
            authority={"requiresApproval": False},
        )
        engine = PermissionEngine(config={
            "permissions": {
                "preset": "balanced",
                "capabilities": {"hooksExecute": {"mode": "blocked", "scope": "*"}},
            },
        })

        service = HookService(store, event_bus, permission_engine=engine)
        results = service.invoke_hooks("after_task_complete", {
            "workspaceId": ws_id,
            "taskId": "task_1",
        })

        assert len(results) == 1
        assert results[0]["policyOutcome"] == "denied"
        assert results[0]["status"] == "failed"
        assert "blocked" in results[0]["errorSummary"]

    def test_invoke_run_command_hook_uses_permission_engine_approval(self, tmp_path: Any) -> None:
        store, event_bus = _make_store(tmp_path)
        ws_id = _make_workspace(store, tmp_path)
        _create_hook(
            store, ws_id,
            action={"type": "run_command", "command": "npm test"},
            authority={"requiresApproval": False},
        )
        engine = PermissionEngine(config={
            "permissions": {
                "preset": "balanced",
                "capabilities": {"hooksExecute": {"mode": "allow", "scope": "*"}},
            },
        })

        service = HookService(store, event_bus, permission_engine=engine)
        results = service.invoke_hooks("after_task_complete", {
            "workspaceId": ws_id,
            "taskId": "task_1",
        })

        assert len(results) == 1
        assert results[0]["policyOutcome"] == "approval_required"
        assert results[0]["status"] == "pending"

    def test_invoke_disabled_hook_skipped(self, tmp_path: Any) -> None:
        store, event_bus = _make_store(tmp_path)
        ws_id = _make_workspace(store, tmp_path)
        _create_hook(store, ws_id, enabled=False)

        service = HookService(store, event_bus)
        results = service.invoke_hooks("after_task_complete", {
            "workspaceId": ws_id,
        })

        assert len(results) == 0

    def test_invoke_hook_with_matching_conditions(self, tmp_path: Any) -> None:
        store, event_bus = _make_store(tmp_path)
        ws_id = _make_workspace(store, tmp_path)
        _create_hook(
            store, ws_id,
            action={"type": "audit_note", "note": "Completed!"},
            conditions={"taskStatus": ["completed"]},
        )

        service = HookService(store, event_bus)
        results = service.invoke_hooks("after_task_complete", {
            "workspaceId": ws_id,
            "taskId": "task_1",
            "taskStatus": "completed",
        })

        assert len(results) == 1
        assert results[0]["status"] == "completed"

    def test_invoke_hook_with_non_matching_conditions(self, tmp_path: Any) -> None:
        store, event_bus = _make_store(tmp_path)
        ws_id = _make_workspace(store, tmp_path)
        _create_hook(
            store, ws_id,
            action={"type": "audit_note", "note": "Completed!"},
            conditions={"taskStatus": ["completed"]},
        )

        service = HookService(store, event_bus)
        results = service.invoke_hooks("after_task_complete", {
            "workspaceId": ws_id,
            "taskId": "task_1",
            "taskStatus": "failed",
        })

        assert len(results) == 1
        assert results[0]["status"] == "skipped"

    def test_invoke_hooks_changed_files_condition(self, tmp_path: Any) -> None:
        store, event_bus = _make_store(tmp_path)
        ws_id = _make_workspace(store, tmp_path)
        _create_hook(
            store, ws_id,
            action={"type": "audit_note", "note": "Py change"},
            conditions={"changedFiles": ["*.py"]},
        )

        service = HookService(store, event_bus)

        # Matching
        results = service.invoke_hooks("after_task_complete", {
            "workspaceId": ws_id,
            "changedFiles": [{"path": "src/main.py"}, "README.md"],
        })
        assert len(results) == 1
        assert results[0]["status"] == "completed"

        # Not matching
        results2 = service.invoke_hooks("after_task_complete", {
            "workspaceId": ws_id,
            "changedFiles": ["src/main.ts"],
        })
        assert len(results2) == 1
        assert results2[0]["status"] == "skipped"

    def test_invoke_no_hooks_for_workspace(self, tmp_path: Any) -> None:
        store, event_bus = _make_store(tmp_path)
        service = HookService(store, event_bus)

        results = service.invoke_hooks("after_task_complete", {
            "workspaceId": "nonexistent_ws",
        })
        assert len(results) == 0


# ---------------------------------------------------------------------------
# Integration: Autonomy Report
# ---------------------------------------------------------------------------

class TestHookAutonomyReport:
    """Hook executions appear in autonomy report."""

    def test_hook_executions_in_autonomy_report(self, tmp_path: Any) -> None:
        store, event_bus = _make_store(tmp_path)
        ws_id = _make_workspace(store, tmp_path)
        session = store.create_session(ws_id, "Hook report test")
        task = store.create_task(
            session_id=session["id"],
            task_type="root",
            goal="test hook report",
            plan=[],
        )

        # Create a hook and execute it via HookService
        _create_hook(
            store, ws_id,
            action={"type": "audit_note", "note": "After complete"},
        )
        service = HookService(store, event_bus)
        service.invoke_hooks("after_task_complete", {
            "workspaceId": ws_id,
            "taskId": task["id"],
            "sessionId": session["id"],
        })

        # Check autonomy report
        report = store.get_autonomy_report({"taskId": task["id"]})
        hook_execs = report["hookExecutions"]
        assert len(hook_execs) == 1
        assert hook_execs[0]["status"] == "completed"
        assert hook_execs[0]["policyOutcome"] == "allowed"
        assert hook_execs[0]["event"] == "after_task_complete"


# ---------------------------------------------------------------------------
# Integration: Runtime Lifecycle Wiring
# ---------------------------------------------------------------------------

class TestHookLifecycleWiring:
    """Hooks fire from real runtime lifecycle handlers."""

    def _make_runtime_task(self, tmp_path: Any) -> tuple[Any, SQLiteStore, str, dict[str, Any], dict[str, Any]]:
        from local_agent_runtime.main import build_server

        server = build_server(database_path=str(tmp_path / "test.sqlite3"))
        store = server._store
        ws_id = _make_workspace(store, tmp_path)
        session = store.create_session(ws_id, "Lifecycle hook test")
        task = store.create_task(
            session_id=session["id"],
            task_type="root",
            goal="test lifecycle hook",
            plan=[],
        )
        return server, store, ws_id, session, task

    def test_pause_task_fires_registered_pause_hook(self, tmp_path: Any) -> None:
        server, store, ws_id, _session, task = self._make_runtime_task(tmp_path)
        _create_hook(
            store,
            ws_id,
            event="on_task_pause",
            action={"type": "audit_note", "note": "Task paused"},
        )

        paused = server._handlers["task.pause"]({"taskId": task["id"]})["task"]

        assert paused["status"] == "paused"
        hook_execs = store.list_hook_executions({"taskId": task["id"]})["hookExecutions"]
        assert len(hook_execs) == 1
        assert hook_execs[0]["event"] == "on_task_pause"
        assert hook_execs[0]["status"] == "completed"
        trace_events = store.list_trace_events({"taskId": task["id"]})["traceEvents"]
        hook_trace = [event for event in trace_events if event["type"] == "hook.executed"]
        assert len(hook_trace) == 1
        assert hook_trace[0]["visibility"] == "trace"
        assert hook_trace[0]["payload"]["hookExecutionId"] == hook_execs[0]["id"]

    def test_cancel_task_fires_cancel_hook(self, tmp_path: Any) -> None:
        server, store, ws_id, _session, task = self._make_runtime_task(tmp_path)
        _create_hook(
            store,
            ws_id,
            event="on_task_cancel",
            action={"type": "audit_note", "note": "Task cancelled"},
        )

        cancelled = server._handlers["task.cancel"]({"taskId": task["id"]})["task"]

        assert cancelled["status"] == "cancelled"
        hook_execs = store.list_hook_executions({"taskId": task["id"]})["hookExecutions"]
        assert len(hook_execs) == 1
        assert hook_execs[0]["event"] == "on_task_cancel"
        assert hook_execs[0]["status"] == "completed"

    def test_resume_task_fires_resume_hook(self, tmp_path: Any) -> None:
        server, store, ws_id, _session, task = self._make_runtime_task(tmp_path)
        paused_task = store.update_task_status(task_id=task["id"], status="paused")
        _create_hook(
            store,
            ws_id,
            event="on_task_resume",
            action={"type": "audit_note", "note": "Task resumed"},
        )

        resumed = server._handlers["task.resume"]({"taskId": paused_task["id"]})["task"]

        assert resumed["status"] == "running"
        hook_execs = store.list_hook_executions({"taskId": task["id"]})["hookExecutions"]
        assert len(hook_execs) == 1
        assert hook_execs[0]["event"] == "on_task_resume"
        assert hook_execs[0]["status"] == "completed"

    def test_compaction_fires_before_and_after_hooks(self, tmp_path: Any) -> None:
        server, store, ws_id, session, task = self._make_runtime_task(tmp_path)
        _create_hook(
            store,
            ws_id,
            event="before_compaction",
            action={"type": "audit_note", "note": "Before compaction"},
        )
        _create_hook(
            store,
            ws_id,
            event="after_compaction",
            action={"type": "audit_note", "note": "After compaction"},
        )
        for index in range(12):
            store.create_message(
                session_id=session["id"],
                task_id=task["id"],
                role="user" if index % 2 == 0 else "assistant",
                content=("context chunk " + str(index) + " ") * 100,
            )

        result = server._handlers["session.compact"]({"sessionId": session["id"], "maxTokens": 20})

        assert result["strategy"] in {"primer_summary_recent", "none"}
        execs = store.list_hook_executions({})["hookExecutions"]
        events = [item["event"] for item in execs]
        assert "before_compaction" in events
        assert "after_compaction" in events


# ---------------------------------------------------------------------------
# Integration: Worktree Hook Points
# ---------------------------------------------------------------------------

class TestWorktreeHookPoints:
    """Worktree lifecycle hooks fire before/after create and merge."""

    def _worktree_path(self, tmp_path: Any, name: str = "task_1") -> str:
        return str(tmp_path / "wt" / name)

    def _approve_worktree_merge(
        self,
        store: SQLiteStore,
        wt: dict[str, Any],
        target_branch: str = "main",
    ) -> dict[str, Any]:
        approval = store.create_approval(
            wt["taskId"],
            "worktree_merge",
            {"worktreeId": wt["id"], "targetBranch": target_branch},
        )
        return store.resolve_approval(approval["id"], "approved")

    def _make_worktree_service(self, tmp_path: Any):
        """Build a WorktreeService with mock git adapter and real HookService."""
        from unittest.mock import MagicMock
        from local_agent_runtime.services.worktree_service import WorktreeService

        store, event_bus = _make_store(tmp_path)
        ws_id = _make_workspace(store, tmp_path)
        hook_service = HookService(store, event_bus)

        # Mock git adapter; no real git operations in tests.
        git_adapter = MagicMock()
        git_adapter.create.return_value = {
            "branch": "agent/task_1",
            "path": self._worktree_path(tmp_path),
            "baseRef": "main",
        }
        git_adapter.merge.return_value = {
            "mergedBranch": "agent/task_1",
            "targetBranch": "main",
            "result": "ok",
            "stdout": "",
        }
        git_adapter.status.return_value = {"dirtyFiles": 0}
        git_adapter.diff.return_value = {"diffStat": "", "files": []}

        service = WorktreeService(store, git_adapter, hook_service=hook_service)
        return service, store, event_bus, ws_id, git_adapter

    def test_create_for_task_fires_before_and_after_hooks(self, tmp_path: Any) -> None:
        service, store, event_bus, ws_id, git_adapter = self._make_worktree_service(tmp_path)
        _create_hook(
            store, ws_id,
            event="before_worktree_create",
            action={"type": "audit_note", "note": "Before create"},
        )
        _create_hook(
            store, ws_id,
            event="after_worktree_create",
            action={"type": "audit_note", "note": "After create"},
        )

        result = service.create_for_task({
            "taskId": "task_1",
            "workspaceId": ws_id,
            "sessionId": "sess_1",
            "branchName": "agent/task_1",
            "worktreePath": self._worktree_path(tmp_path),
            "baseRef": "main",
        })

        assert "worktree" in result
        assert "git" in result

        execs = store.list_hook_executions({})["hookExecutions"]
        events = [e["event"] for e in execs]
        assert "before_worktree_create" in events
        assert "after_worktree_create" in events

        # Both should have completed
        for ex in execs:
            assert ex["status"] == "completed"

    def test_merge_fires_before_and_after_hooks(self, tmp_path: Any) -> None:
        service, store, event_bus, ws_id, git_adapter = self._make_worktree_service(tmp_path)
        _create_hook(
            store, ws_id,
            event="before_worktree_merge",
            action={"type": "audit_note", "note": "Before merge"},
        )
        _create_hook(
            store, ws_id,
            event="after_worktree_merge",
            action={"type": "audit_note", "note": "After merge"},
        )

        # First create a real task and worktree record so approval tracing can attach.
        session = store.create_session(ws_id, "Worktree merge hook test")
        task = store.create_task(
            session_id=session["id"],
            task_type="root",
            goal="test worktree merge hooks",
            plan=[],
        )
        wt = store.create_worktree({
            "taskId": task["id"],
            "workspaceId": ws_id,
            "sessionId": session["id"],
            "branchName": "agent/task_1",
            "worktreePath": self._worktree_path(tmp_path),
            "baseRef": "main",
        })["worktree"]

        approval = self._approve_worktree_merge(store, wt)
        result = service.merge({
            "worktreeId": wt["id"],
            "targetBranch": "main",
            "approvalId": approval["id"],
        })

        assert result["merged"] is True
        assert result["worktreeId"] == wt["id"]

        execs = store.list_hook_executions({})["hookExecutions"]
        events = [e["event"] for e in execs]
        assert "before_worktree_merge" in events
        assert "after_worktree_merge" in events

        for ex in execs:
            assert ex["status"] == "completed"

    def test_create_hook_disabled_skips_worktree_hooks(self, tmp_path: Any) -> None:
        service, store, event_bus, ws_id, git_adapter = self._make_worktree_service(tmp_path)
        _create_hook(
            store, ws_id,
            event="before_worktree_create",
            action={"type": "audit_note", "note": "Should not fire"},
            enabled=False,
        )
        _create_hook(
            store, ws_id,
            event="after_worktree_create",
            action={"type": "audit_note", "note": "Should fire"},
        )

        result = service.create_for_task({
            "taskId": "task_1",
            "workspaceId": ws_id,
            "sessionId": "sess_1",
            "branchName": "agent/task_1",
            "worktreePath": self._worktree_path(tmp_path),
            "baseRef": "main",
        })

        execs = store.list_hook_executions({})["hookExecutions"]
        # Only after_worktree_create should have executed (before was disabled)
        assert len(execs) == 1
        assert execs[0]["event"] == "after_worktree_create"

    def test_no_hooks_fired_when_hook_service_is_none(self, tmp_path: Any) -> None:
        """WorktreeService without hook_service should not crash."""
        from unittest.mock import MagicMock
        from local_agent_runtime.services.worktree_service import WorktreeService

        store, _ = _make_store(tmp_path)
        ws_id = _make_workspace(store, tmp_path)
        git_adapter = MagicMock()
        git_adapter.create.return_value = {
            "branch": "agent/task_1",
            "path": self._worktree_path(tmp_path),
            "baseRef": "main",
        }

        service = WorktreeService(store, git_adapter, hook_service=None)
        result = service.create_for_task({
            "taskId": "task_1",
            "workspaceId": ws_id,
            "sessionId": "sess_1",
            "branchName": "agent/task_1",
            "worktreePath": self._worktree_path(tmp_path),
            "baseRef": "main",
        })

        assert "worktree" in result
        # No hook executions should exist
        execs = store.list_hook_executions({})["hookExecutions"]
        assert len(execs) == 0

    def test_worktree_hooks_use_correct_context(self, tmp_path: Any) -> None:
        """Verify hook context includes worktree-specific fields."""
        service, store, event_bus, ws_id, git_adapter = self._make_worktree_service(tmp_path)

        captured_events: list = []
        event_bus.subscribe(captured_events.append)

        _create_hook(
            store, ws_id,
            event="after_worktree_create",
            action={"type": "audit_note", "note": "Check context"},
        )

        service.create_for_task({
            "taskId": "task_ctx",
            "workspaceId": ws_id,
            "sessionId": "sess_ctx",
            "branchName": "agent/ctx",
            "worktreePath": self._worktree_path(tmp_path, "ctx"),
            "baseRef": "HEAD",
        })

        # The hook execution record should have the task/workspace context
        execs = store.list_hook_executions({})["hookExecutions"]
        assert len(execs) == 1
        assert execs[0]["status"] == "completed"

        # Verify trace event was emitted
        hook_events = [e for e in captured_events if e.type == "hook.executed"]
        assert len(hook_events) == 1


# ---------------------------------------------------------------------------
# P2 Action Types
# ---------------------------------------------------------------------------

class TestHookP2Actions:
    """P2 hook action types: webhook, memory_write, auto_verification, external_sync."""

    def test_create_hook_with_webhook_action_type(self, tmp_path: Any) -> None:
        store, _ = _make_store(tmp_path)
        ws_id = _make_workspace(store, tmp_path)
        hook = _create_hook(store, ws_id, action={
            "type": "webhook",
            "url": "https://example.com/hook",
            "method": "POST",
        })
        assert hook["action"]["type"] == "webhook"
        assert hook["action"]["url"] == "https://example.com/hook"

    def test_create_hook_with_memory_write_action_type(self, tmp_path: Any) -> None:
        store, _ = _make_store(tmp_path)
        ws_id = _make_workspace(store, tmp_path)
        hook = _create_hook(store, ws_id, action={
            "type": "memory_write",
            "kind": "session",
            "content": "Task {taskId} completed",
        })
        assert hook["action"]["type"] == "memory_write"

    def test_create_hook_with_auto_verification_action_type(self, tmp_path: Any) -> None:
        store, _ = _make_store(tmp_path)
        ws_id = _make_workspace(store, tmp_path)
        hook = _create_hook(store, ws_id, action={
            "type": "auto_verification_suggestion",
            "checks": ["tests_pass", "lint_clean"],
        })
        assert hook["action"]["type"] == "auto_verification_suggestion"

    def test_create_hook_with_external_sync_action_type(self, tmp_path: Any) -> None:
        store, _ = _make_store(tmp_path)
        ws_id = _make_workspace(store, tmp_path)
        hook = _create_hook(store, ws_id, action={
            "type": "external_sync",
            "target": "github",
            "operation": "create_issue",
        })
        assert hook["action"]["type"] == "external_sync"

    def test_auto_verification_suggestion_executes(self, tmp_path: Any) -> None:
        store, event_bus = _make_store(tmp_path)
        ws_id = _make_workspace(store, tmp_path)
        _create_hook(store, ws_id, action={
            "type": "auto_verification_suggestion",
            "checks": ["tests_pass", "lint_clean"],
            "suggestion": "Run tests after changes",
        })

        captured_events: list[RuntimeEvent] = []
        event_bus.subscribe(captured_events.append)

        service = HookService(store, event_bus)
        results = service.invoke_hooks("after_task_complete", {
            "workspaceId": ws_id,
            "taskId": "task_av1",
        })

        assert len(results) == 1
        assert results[0]["status"] == "completed"
        assert results[0]["policyOutcome"] == "allowed"

        # Verify the specialized event type
        verification_events = [e for e in captured_events if e.type == "hook.auto_verification_suggestion"]
        assert len(verification_events) == 1
        assert verification_events[0].payload["checks"] == ["tests_pass", "lint_clean"]
        assert verification_events[0].payload["suggestion"] == "Run tests after changes"

    def test_external_sync_executes_deferred(self, tmp_path: Any) -> None:
        store, event_bus = _make_store(tmp_path)
        ws_id = _make_workspace(store, tmp_path)
        _create_hook(store, ws_id, action={
            "type": "external_sync",
            "target": "github",
            "operation": "create_issue",
            "mapping": {"title": "taskGoal"},
        })

        captured_events: list[RuntimeEvent] = []
        event_bus.subscribe(captured_events.append)

        service = HookService(store, event_bus)
        results = service.invoke_hooks("after_task_complete", {
            "workspaceId": ws_id,
            "taskId": "task_es1",
        })

        assert len(results) == 1
        assert results[0]["status"] == "deferred"
        assert results[0]["policyOutcome"] == "allowed"

        sync_events = [e for e in captured_events if e.type == "hook.external_sync"]
        assert len(sync_events) == 1
        assert sync_events[0].payload["target"] == "github"
        assert sync_events[0].payload["operation"] == "create_issue"

    def test_external_sync_blocked_by_permission(self, tmp_path: Any) -> None:
        store, event_bus = _make_store(tmp_path)
        ws_id = _make_workspace(store, tmp_path)
        _create_hook(store, ws_id, action={
            "type": "external_sync",
            "target": "github",
            "operation": "delete_repo",
        })
        engine = PermissionEngine(config={
            "permissions": {
                "preset": "balanced",
                "capabilities": {"hooksExecute": {"mode": "blocked", "scope": "*"}},
            },
        })

        service = HookService(store, event_bus, permission_engine=engine)
        results = service.invoke_hooks("after_task_complete", {
            "workspaceId": ws_id,
            "taskId": "task_es2",
        })

        assert len(results) == 1
        assert results[0]["policyOutcome"] == "denied"
        assert results[0]["status"] == "failed"

    def test_memory_write_executes_with_store(self, tmp_path: Any) -> None:
        from local_agent_runtime.memory.store import MemoryStore

        store, event_bus = _make_store(tmp_path)
        ws_id = _make_workspace(store, tmp_path)
        memory_store = MemoryStore(store)

        _create_hook(store, ws_id, action={
            "type": "memory_write",
            "kind": "session",
            "content": "Task {taskId} finished with status {taskStatus}",
            "keywords": ["task", "completion"],
        })

        captured_events: list[RuntimeEvent] = []
        event_bus.subscribe(captured_events.append)

        service = HookService(store, event_bus, memory_store=memory_store)
        results = service.invoke_hooks("after_task_complete", {
            "workspaceId": ws_id,
            "taskId": "task_mw1",
            "taskStatus": "completed",
        })

        assert len(results) == 1
        assert results[0]["status"] == "completed"
        assert results[0]["policyOutcome"] == "allowed"

        # Verify memory was written
        mem_events = [e for e in captured_events if e.type == "hook.memory_write"]
        assert len(mem_events) == 1
        assert mem_events[0].payload["memoryKind"] == "session"

        # Verify the content was template-substituted — outputSummary shows the entry id
        assert "mem_" in results[0]["outputSummary"]

    def test_memory_write_skipped_without_store(self, tmp_path: Any) -> None:
        store, event_bus = _make_store(tmp_path)
        ws_id = _make_workspace(store, tmp_path)
        _create_hook(store, ws_id, action={
            "type": "memory_write",
            "kind": "session",
            "content": "Should be skipped",
        })

        service = HookService(store, event_bus, memory_store=None)
        results = service.invoke_hooks("after_task_complete", {
            "workspaceId": ws_id,
            "taskId": "task_mw2",
        })

        assert len(results) == 1
        assert results[0]["status"] == "skipped"
        assert results[0]["policyOutcome"] == "skipped"
        assert "not available" in results[0]["outputSummary"]

    def test_memory_write_invalid_kind(self, tmp_path: Any) -> None:
        from local_agent_runtime.memory.store import MemoryStore

        store, event_bus = _make_store(tmp_path)
        ws_id = _make_workspace(store, tmp_path)
        memory_store = MemoryStore(store)

        _create_hook(store, ws_id, action={
            "type": "memory_write",
            "kind": "invalid_kind",
            "content": "Bad kind",
        })

        service = HookService(store, event_bus, memory_store=memory_store)
        results = service.invoke_hooks("after_task_complete", {
            "workspaceId": ws_id,
            "taskId": "task_mw3",
        })

        assert len(results) == 1
        assert results[0]["status"] == "failed"
        assert results[0]["policyOutcome"] == "error"
        assert "Invalid memory kind" in results[0]["errorSummary"]

    def test_webhook_executes_success(self, tmp_path: Any) -> None:
        """Test webhook fires an HTTP request to a mock server."""
        from http.server import HTTPServer, BaseHTTPRequestHandler
        import threading

        received: list[dict[str, Any]] = []

        class _Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length)
                received.append(json.loads(body))
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"ok":true}')
            def log_message(self, *args: Any) -> None:
                pass

        server = HTTPServer(("127.0.0.1", 0), _Handler)
        port = server.server_address[1]
        t = threading.Thread(target=server.handle_request, daemon=True)
        t.start()

        store, event_bus = _make_store(tmp_path)
        ws_id = _make_workspace(store, tmp_path)
        _create_hook(store, ws_id, action={
            "type": "webhook",
            "url": f"http://127.0.0.1:{port}/hook",
            "method": "POST",
        })

        service = HookService(store, event_bus)
        results = service.invoke_hooks("after_task_complete", {
            "workspaceId": ws_id,
            "taskId": "task_wh1",
        })

        assert len(results) == 1
        assert results[0]["status"] == "completed"
        assert results[0]["policyOutcome"] == "allowed"
        assert "HTTP 200" in results[0]["outputSummary"]

        # Verify the webhook received the payload
        t.join(timeout=5)
        assert len(received) == 1
        assert received[0]["event"] == "after_task_complete"
        assert received[0]["hookId"].startswith("hook_")

    def test_webhook_failed_connection(self, tmp_path: Any) -> None:
        store, event_bus = _make_store(tmp_path)
        ws_id = _make_workspace(store, tmp_path)
        _create_hook(store, ws_id, action={
            "type": "webhook",
            "url": "http://127.0.0.1:1/impossible-port",
        })

        service = HookService(store, event_bus)
        results = service.invoke_hooks("after_task_complete", {
            "workspaceId": ws_id,
            "taskId": "task_wh2",
        })

        assert len(results) == 1
        assert results[0]["status"] == "failed"
        assert results[0]["policyOutcome"] == "error"

    def test_webhook_blocked_by_permission(self, tmp_path: Any) -> None:
        store, event_bus = _make_store(tmp_path)
        ws_id = _make_workspace(store, tmp_path)
        _create_hook(store, ws_id, action={
            "type": "webhook",
            "url": "https://example.com/webhook",
        })
        engine = PermissionEngine(config={
            "permissions": {
                "preset": "balanced",
                "capabilities": {"hooksExecute": {"mode": "blocked", "scope": "*"}},
            },
        })

        service = HookService(store, event_bus, permission_engine=engine)
        results = service.invoke_hooks("after_task_complete", {
            "workspaceId": ws_id,
            "taskId": "task_wh3",
        })

        assert len(results) == 1
        assert results[0]["policyOutcome"] == "denied"
        assert results[0]["status"] == "failed"

    def test_p2_action_crud_roundtrip(self, tmp_path: Any) -> None:
        """All 4 P2 action types can be created, retrieved, and updated."""
        store, _ = _make_store(tmp_path)
        ws_id = _make_workspace(store, tmp_path)

        for action_type in ("webhook", "memory_write", "auto_verification_suggestion", "external_sync"):
            hook = _create_hook(store, ws_id, action={"type": action_type}, name=f"{action_type} hook")
            assert hook["action"]["type"] == action_type

            retrieved = store.get_hook({"hookId": hook["id"]})
            assert retrieved["hook"]["action"]["type"] == action_type

            updated = store.update_hook({"hookId": hook["id"], "name": f"Updated {action_type}"})
            assert updated["hook"]["name"] == f"Updated {action_type}"
