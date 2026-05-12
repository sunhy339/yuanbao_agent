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
            "priority": 50,
            "enabled": False,
        })
        assert updated["hook"]["name"] == "Updated name"
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
            "changedFiles": ["src/main.py", "README.md"],
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
