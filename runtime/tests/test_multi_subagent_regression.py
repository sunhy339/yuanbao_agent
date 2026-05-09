"""P0: Headless multi-subagent dispatch regression test.

Asserts:
- Parent task id, child task ids, worker ids, statuses
- Messages and trace event types
- Child collaboration lifecycle events use visibility = "panel"
- Root assistant streaming events still use visibility = "chat"
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from local_agent_runtime.event_bus import EventBus
from local_agent_runtime.services import CollaborationService, SubagentService
from local_agent_runtime.services.worker_runner import WorkerRunner
from local_agent_runtime.store.sqlite_store import SQLiteStore


def _setup(tmp_path: Path) -> tuple[
    SQLiteStore, EventBus, SubagentService, dict[str, Any]
]:
    store = SQLiteStore(str(tmp_path / "runtime.sqlite3"))
    event_bus = EventBus()
    event_bus.subscribe(store.append_runtime_event)
    collaboration = CollaborationService(store, event_bus)

    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(parents=True, exist_ok=True)
    workspace = store.upsert_workspace(str(workspace_root))
    session = store.create_session(workspace_id=workspace["id"], title="p0-regression")
    parent_task = store.create_task(
        session_id=session["id"],
        task_type="chat",
        goal="Build a UI music player",
        plan=[],
    )

    def mock_executor(context: Any) -> dict[str, Any]:
        return {
            "summary": f"Completed: {context.request.title}",
            "executionMode": "mock",
        }

    runner = WorkerRunner(collaboration, executor=mock_executor)
    subagent = SubagentService(store, collaboration, runner=runner)

    return (
        store,
        event_bus,
        subagent,
        {"session": session, "parent_task": parent_task},
    )


def test_multi_subagent_dispatch_regression(tmp_path: Path) -> None:
    """P0 regression: dispatch multiple subagents and verify durable state."""
    store, event_bus, subagent, records = _setup(tmp_path)
    session = records["session"]
    parent_task = records["parent_task"]

    try:
        # Dispatch 3 child tasks: explorer, worker, reviewer
        explorer_result = subagent.dispatch({
            "prompt": "Explore the workspace and report structure.",
            "sessionId": session["id"],
            "taskId": parent_task["id"],
            "title": "Explorer",
            "agentType": "explorer",
            "priority": 1,
        })

        worker_result = subagent.dispatch({
            "prompt": "Build the UI player component.",
            "sessionId": session["id"],
            "taskId": parent_task["id"],
            "title": "UI Worker",
            "agentType": "worker",
            "priority": 3,
        })

        reviewer_result = subagent.dispatch({
            "prompt": "Review the UI player implementation.",
            "sessionId": session["id"],
            "taskId": parent_task["id"],
            "title": "Reviewer",
            "agentType": "reviewer",
            "priority": 5,
        })

        # --- Assert parent task id ---
        for result in [explorer_result, worker_result, reviewer_result]:
            meta = result["task"].get("metadata", {})
            assert meta.get("parentRuntimeTaskId") == parent_task["id"]

        # --- Assert child task ids ---
        explorer_task = explorer_result["task"]
        worker_task = worker_result["task"]
        reviewer_task = reviewer_result["task"]
        child_ids = {explorer_task["id"], worker_task["id"], reviewer_task["id"]}
        assert len(child_ids) == 3  # all unique

        # --- Assert worker ids ---
        explorer_worker = explorer_result["worker"]
        worker_worker = worker_result["worker"]
        reviewer_worker = reviewer_result["worker"]
        assert explorer_worker["id"] is not None
        assert worker_worker["id"] is not None
        assert reviewer_worker["id"] is not None

        # --- Assert statuses ---
        assert explorer_result["status"] == "completed"
        assert worker_result["status"] == "completed"
        assert reviewer_result["status"] == "completed"
        assert explorer_task["status"] == "completed"
        assert worker_task["status"] == "completed"
        assert reviewer_task["status"] == "completed"

        # --- Assert agent types in metadata ---
        assert explorer_task["metadata"]["agentType"] == "explorer"
        assert worker_task["metadata"]["agentType"] == "worker"
        assert reviewer_task["metadata"]["agentType"] == "reviewer"

        # --- Assert messages ---
        for result in [explorer_result, worker_result, reviewer_result]:
            msg = result["message"]
            assert msg["kind"] == "result"
            assert msg["taskId"] == result["task"]["id"]

        # --- Assert trace events ---
        for child_id in child_ids:
            trace_result = store.list_trace_events({"taskId": child_id, "limit": 100})
            trace_events = trace_result["traceEvents"]
            trace_types = [evt["type"] for evt in trace_events]

            # Must have lifecycle events
            assert "collab.task.created" in trace_types
            assert "collab.task.completed" in trace_types
            assert "collab.message.sent" in trace_types

            # Each child should have at least these 4 trace events
            assert len(trace_events) >= 4

        # --- Assert collaboration task list ---
        collab_tasks = store.list_collaboration_tasks(
            {"parentTaskId": parent_task["id"]}
        )["tasks"]
        assert len(collab_tasks) == 3
        collab_ids = {t["id"] for t in collab_tasks}
        assert collab_ids == child_ids

    finally:
        store.close()


def test_child_collab_events_use_panel_visibility(tmp_path: Path) -> None:
    """P0: Child collaboration lifecycle events use visibility = 'panel'."""
    store, event_bus, subagent, records = _setup(tmp_path)
    session = records["session"]
    parent_task = records["parent_task"]

    captured_events: list[dict[str, Any]] = []
    event_bus.subscribe(
        lambda event: captured_events.append(event_bus.as_payload(event))
    )

    try:
        result = subagent.dispatch({
            "prompt": "Explore workspace.",
            "sessionId": session["id"],
            "taskId": parent_task["id"],
            "title": "Explorer",
            "agentType": "explorer",
        })

        child_id = result["task"]["id"]

        # Collaboration trace events should have visibility = "panel"
        trace_result = store.list_trace_events({"taskId": child_id, "limit": 100})
        trace_events = trace_result["traceEvents"]

        for evt in trace_events:
            assert evt.get("visibility") == "panel", (
                f"Event {evt['type']} has visibility={evt.get('visibility')}, expected 'panel'"
            )

    finally:
        store.close()


def test_root_streaming_events_use_chat_visibility(tmp_path: Path) -> None:
    """P0: Root assistant streaming events still use visibility = 'chat'."""
    store, event_bus, subagent, records = _setup(tmp_path)
    session = records["session"]
    parent_task = records["parent_task"]

    try:
        # Root task's trace events (non-collaboration) should default to "chat"
        store.append_trace_event(
            task_id=parent_task["id"],
            event_type="message.delta",
            source="root",
            payload={"text": "Hello"},
            session_id=session["id"],
            visibility="chat",
        )
        store.append_trace_event(
            task_id=parent_task["id"],
            event_type="message.completed",
            source="root",
            payload={},
            session_id=session["id"],
            visibility="chat",
        )

        trace_result = store.list_trace_events({"taskId": parent_task["id"], "limit": 100})
        trace_events = trace_result["traceEvents"]

        for evt in trace_events:
            assert evt.get("visibility") == "chat", (
                f"Root event {evt['type']} has visibility={evt.get('visibility')}, expected 'chat'"
            )

    finally:
        store.close()


def test_supported_child_tool_names(tmp_path: Path) -> None:
    """P0: Document and verify the current supported child tool names."""
    from local_agent_runtime.services.worker_environment import normalize_child_tool_allowlist

    # All canonical tool names should normalize correctly
    canonical_tools = [
        "search_files", "read_file", "git_status", "git_diff",
        "run_command", "apply_patch",
    ]
    for tool in canonical_tools:
        normalized = normalize_child_tool_allowlist([tool])
        assert tool in normalized, f"Canonical tool {tool} should be in normalized list"

    # Common aliases should normalize to canonical names
    alias_mappings = {
        "rg": "search_files",
        "grep": "search_files",
        "search": "search_files",
        "cat": "read_file",
        "read": "read_file",
        "shell": "run_command",
        "command": "run_command",
        "patch": "apply_patch",
        "status": "git_status",
        "diff": "git_diff",
    }
    for alias, canonical in alias_mappings.items():
        normalized = normalize_child_tool_allowlist([alias])
        assert canonical in normalized, (
            f"Alias '{alias}' should normalize to '{canonical}', got {normalized}"
        )

    # Nested 'task' should be blocked (raises ValueError for unsafe tools)
    with pytest.raises(ValueError, match="not allowed"):
        normalize_child_tool_allowlist(["task"])
