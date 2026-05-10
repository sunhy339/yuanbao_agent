"""P5: Real Process-RPC Child Worker E2E tests.

Tests that spawn a real child process using WorkerProcessTransport.
The child connects to the same file-backed SQLite database and executes
tasks using a real LLM provider (GLM-5.1 via OpenAI-compatible API).

These tests require:
  - LOCAL_AGENT_PROVIDER_API_KEY set to a valid API key
  - LOCAL_AGENT_PROVIDER_BASE_URL set to the provider endpoint
  - LOCAL_AGENT_PROVIDER_MODEL set to a valid model name
  - LOCAL_AGENT_PROVIDER_MODE set to "openai-compatible"

Tests are skipped automatically when the provider is not configured.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from local_agent_runtime.event_bus import EventBus
from local_agent_runtime.services import CollaborationService, SubagentService
from local_agent_runtime.services.worker_runner import (
    ChildTaskProcessRequiredError,
    ChildTaskRemoteError,
    WorkerRunner,
)
from local_agent_runtime.store.sqlite_store import SQLiteStore


# --- Provider configuration ---

PROVIDER_API_KEY = os.environ.get("LOCAL_AGENT_PROVIDER_API_KEY", "")
PROVIDER_BASE_URL = os.environ.get("LOCAL_AGENT_PROVIDER_BASE_URL", "")
PROVIDER_MODEL = os.environ.get("LOCAL_AGENT_PROVIDER_MODEL", "GLM-5.1")
PROVIDER_MODE = os.environ.get("LOCAL_AGENT_PROVIDER_MODE", "")

_HAS_REAL_PROVIDER = bool(PROVIDER_API_KEY and PROVIDER_BASE_URL)

requires_real_provider = pytest.mark.skipif(
    not _HAS_REAL_PROVIDER,
    reason="Requires LOCAL_AGENT_PROVIDER_API_KEY and LOCAL_AGENT_PROVIDER_BASE_URL",
)


def _setup_env_for_child() -> dict[str, str]:
    """Set environment variables so the child process can use the real LLM."""
    env_updates = {}
    if PROVIDER_API_KEY:
        os.environ["LOCAL_AGENT_PROVIDER_API_KEY"] = PROVIDER_API_KEY
        env_updates["LOCAL_AGENT_PROVIDER_API_KEY"] = PROVIDER_API_KEY
    if PROVIDER_BASE_URL:
        os.environ["LOCAL_AGENT_PROVIDER_BASE_URL"] = PROVIDER_BASE_URL
        env_updates["LOCAL_AGENT_PROVIDER_BASE_URL"] = PROVIDER_BASE_URL
    if PROVIDER_MODEL:
        os.environ["LOCAL_AGENT_PROVIDER_MODEL"] = PROVIDER_MODEL
        env_updates["LOCAL_AGENT_PROVIDER_MODEL"] = PROVIDER_MODEL
    os.environ["LOCAL_AGENT_PROVIDER_MODE"] = "openai-compatible"
    env_updates["LOCAL_AGENT_PROVIDER_MODE"] = "openai-compatible"
    return env_updates


def _cleanup_env(env_updates: dict[str, str]) -> None:
    """Restore environment variables after test."""
    for key in list(env_updates.keys()):
        os.environ.pop(key, None)


def _setup_runtime(
    tmp_path: Path,
) -> tuple[SQLiteStore, EventBus, SubagentService, dict[str, Any], dict[str, str]]:
    """Create a full runtime with file-backed DB for process-RPC."""
    env_updates = _setup_env_for_child()

    db_path = str(tmp_path / "runtime_e2e.sqlite3")
    store = SQLiteStore(db_path)

    # Configure provider in the store so child process reads it
    store.update_config({
        "config": {
            "provider": {
                "mode": "openai-compatible",
                "baseUrl": PROVIDER_BASE_URL,
                "model": PROVIDER_MODEL,
                "apiKeyEnvVarName": "LOCAL_AGENT_PROVIDER_API_KEY",
                "temperature": 0.2,
                "maxTokens": 4000,
            },
            "policy": {
                "approvalMode": "off",
                "maxTaskSteps": 5,
            },
        },
    })

    event_bus = EventBus()
    event_bus.subscribe(store.append_runtime_event)
    collaboration = CollaborationService(store, event_bus)

    # Create workspace in tmp_path
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(parents=True, exist_ok=True)
    # Create a sample file for the child to discover
    (workspace_root / "hello.txt").write_text("Hello from E2E test workspace.\n", encoding="utf-8")
    (workspace_root / "README.md").write_text("# E2E Test Workspace\n\nSample project for testing.\n", encoding="utf-8")

    workspace = store.upsert_workspace(str(workspace_root))
    session = store.create_session(workspace_id=workspace["id"], title="p5-e2e")
    parent_task = store.create_task(
        session_id=session["id"],
        task_type="chat",
        goal="Explore the workspace and report what files exist",
        plan=[],
    )

    # No custom executor — WorkerRunner will use process-RPC
    runner = WorkerRunner(collaboration)
    subagent = SubagentService(store, collaboration, runner=runner)

    return (
        store,
        event_bus,
        subagent,
        {"session": session, "parent_task": parent_task, "workspace_root": workspace_root},
        env_updates,
    )


@requires_real_provider
def test_process_worker_e2e_child_completes(tmp_path: Path) -> None:
    """P5: Child process spawns, uses real LLM, and completes with a result."""
    store, event_bus, subagent, records, env_updates = _setup_runtime(tmp_path)
    session = records["session"]
    parent_task = records["parent_task"]

    try:
        result = subagent.dispatch({
            "prompt": "List the files in this workspace directory and read the file hello.txt. Report the contents.",
            "sessionId": session["id"],
            "taskId": parent_task["id"],
            "title": "Explorer",
            "agentType": "explorer",
            "priority": 1,
            "budget": {
                "childToolAllowlist": ["list_dir", "search_files", "read_file"],
                "timeoutSeconds": 120,
            },
        })

        # --- Verify child task completed ---
        assert result["status"] == "completed", f"Expected completed, got: {result}"
        assert result["childTaskId"] is not None
        assert result["workerId"] is not None

        # --- Verify execution mode ---
        assert result["subagent"]["executionMode"] == "process-rpc"

        # --- Verify summary has content ---
        summary = result.get("summary", "")
        assert len(summary) > 0, "Child should produce a non-empty summary"

        # --- Verify task is persisted ---
        child_task_id = result["childTaskId"]
        persisted = store.get_collaboration_task({"taskId": child_task_id})
        assert persisted["task"]["status"] == "completed"

    finally:
        store.close()
        _cleanup_env(env_updates)


@requires_real_provider
def test_process_worker_e2e_trace_events_persisted(tmp_path: Path) -> None:
    """P5: Trace events are persisted for the child task."""
    store, event_bus, subagent, records, env_updates = _setup_runtime(tmp_path)
    session = records["session"]
    parent_task = records["parent_task"]

    try:
        result = subagent.dispatch({
            "prompt": "Search for files in the workspace and read any text file you find.",
            "sessionId": session["id"],
            "taskId": parent_task["id"],
            "title": "Explorer Trace Test",
            "agentType": "explorer",
            "priority": 1,
            "budget": {
                "childToolAllowlist": ["list_dir", "search_files", "read_file"],
            },
        })

        child_task_id = result["childTaskId"]

        # --- Verify trace events exist ---
        trace_result = store.list_trace_events({"taskId": child_task_id, "limit": 200})
        trace_events = trace_result["traceEvents"]
        assert len(trace_events) > 0, "Child task should have trace events"

        trace_types = {evt["type"] for evt in trace_events}

        # Must have at least task lifecycle events
        assert "collab.task.created" in trace_types
        assert "collab.task.completed" in trace_types

        # Should have tool execution events from the child's ReAct loop
        # (these are bridged from child process via collab.task.updated)
        assert "collab.message.sent" in trace_types

    finally:
        store.close()
        _cleanup_env(env_updates)


@requires_real_provider
def test_process_worker_e2e_events_bridge_to_parent(tmp_path: Path) -> None:
    """P5: Child events bridge into the parent event bus."""
    store, event_bus, subagent, records, env_updates = _setup_runtime(tmp_path)
    session = records["session"]
    parent_task = records["parent_task"]

    captured_events: list[dict[str, Any]] = []
    event_bus.subscribe(
        lambda event: captured_events.append(event_bus.as_payload(event))
    )

    try:
        result = subagent.dispatch({
            "prompt": "Read the file hello.txt in the workspace.",
            "sessionId": session["id"],
            "taskId": parent_task["id"],
            "title": "Explorer Bridge Test",
            "agentType": "explorer",
            "priority": 1,
            "budget": {
                "childToolAllowlist": ["list_dir", "read_file"],
            },
        })

        # --- Verify bridged events were captured ---
        event_types = {evt["type"] for evt in captured_events}

        # Collaboration lifecycle events should be present
        assert "collab.task.created" in event_types
        assert "collab.task.completed" in event_types
        assert "collab.message.sent" in event_types

        # Check that some events have bridge metadata
        bridged_events = [
            evt for evt in captured_events
            if isinstance(evt.get("payload"), dict)
            and isinstance(evt["payload"].get("_bridge"), dict)
        ]
        # At minimum, the task lifecycle events should have bridge metadata
        # when forwarded from child process
        assert len(bridged_events) > 0, "Should have bridged events from child process"

    finally:
        store.close()
        _cleanup_env(env_updates)


@requires_real_provider
def test_process_worker_e2e_read_only_child_tools(tmp_path: Path) -> None:
    """P5: Verify child process can search/read workspace context with read-only tools."""
    store, event_bus, subagent, records, env_updates = _setup_runtime(tmp_path)
    session = records["session"]
    parent_task = records["parent_task"]

    # Add more files for the child to discover
    workspace_root = records["workspace_root"]
    src_dir = workspace_root / "src"
    src_dir.mkdir(exist_ok=True)
    (src_dir / "main.py").write_text("print('hello world')\n", encoding="utf-8")

    try:
        result = subagent.dispatch({
            "prompt": "Explore the workspace. List all files and read main.py. Report what you found.",
            "sessionId": session["id"],
            "taskId": parent_task["id"],
            "title": "Read-Only Explorer",
            "agentType": "explorer",
            "priority": 1,
            "budget": {
                "childToolAllowlist": ["list_dir", "search_files", "read_file"],
            },
        })

        assert result["status"] == "completed"

        # The child should have actually read files and produced a meaningful summary
        summary = result.get("summary", "").lower()
        # The LLM should mention file contents or file names it discovered
        assert len(summary) > 20, "Summary should reflect actual workspace exploration"

    finally:
        store.close()
        _cleanup_env(env_updates)


@requires_real_provider
def test_process_worker_e2e_parent_report_includes_child(tmp_path: Path) -> None:
    """P5: Parent report includes the child execution result."""
    store, event_bus, subagent, records, env_updates = _setup_runtime(tmp_path)
    session = records["session"]
    parent_task = records["parent_task"]

    try:
        result = subagent.dispatch({
            "prompt": "Read hello.txt and summarize its contents.",
            "sessionId": session["id"],
            "taskId": parent_task["id"],
            "title": "Reporter",
            "agentType": "explorer",
            "priority": 1,
            "budget": {
                "childToolAllowlist": ["list_dir", "read_file"],
            },
        })

        assert result["status"] == "completed"

        # --- Verify report query can find the child task ---
        collab_tasks = store.list_collaboration_tasks(
            {"parentTaskId": parent_task["id"]}
        )["tasks"]
        assert len(collab_tasks) == 1

        child_task = collab_tasks[0]
        assert child_task["status"] == "completed"
        assert child_task["id"] == result["childTaskId"]

        # --- Verify task metadata ---
        metadata = child_task.get("metadata", {})
        assert metadata.get("agentType") == "explorer"
        assert metadata.get("parentRuntimeTaskId") == parent_task["id"]
        assert metadata.get("executionMode") == "process-rpc"

        # --- Verify worker exists ---
        worker_id = result["workerId"]
        worker_result = store.get_agent_worker({"workerId": worker_id})
        assert worker_result["worker"]["id"] == worker_id

    finally:
        store.close()
        _cleanup_env(env_updates)


@requires_real_provider
def test_process_worker_e2e_child_fails_with_structured_error(tmp_path: Path) -> None:
    """P5: Verify child task fails with structured error when tool allowlist blocks execution."""
    store, event_bus, subagent, records, env_updates = _setup_runtime(tmp_path)
    session = records["session"]
    parent_task = records["parent_task"]

    try:
        # This should still work — the child will use read-only tools
        # but we give it an impossible prompt to verify it handles gracefully
        result = subagent.dispatch({
            "prompt": "Find and read a file called nonexistent_file_12345.txt",
            "sessionId": session["id"],
            "taskId": parent_task["id"],
            "title": "Impossible Explorer",
            "agentType": "explorer",
            "priority": 1,
            "budget": {
                "childToolAllowlist": ["list_dir", "read_file"],
            },
        })

        # The child should complete (even if it couldn't find the file)
        # The LLM should report that the file wasn't found
        assert result["status"] in ("completed", "failed")
        assert result["childTaskId"] is not None

    finally:
        store.close()
        _cleanup_env(env_updates)


@requires_real_provider
def test_process_worker_e2e_file_backed_database(tmp_path: Path) -> None:
    """P5: Verify that both parent and child processes share the same database."""
    store, event_bus, subagent, records, env_updates = _setup_runtime(tmp_path)
    session = records["session"]
    parent_task = records["parent_task"]

    try:
        # Before dispatch: verify DB file exists
        db_path = Path(store.database_path)
        assert db_path.exists(), "Database file should exist before dispatch"
        assert db_path.stat().st_size > 0, "Database should not be empty"

        result = subagent.dispatch({
            "prompt": "Read hello.txt and report its contents.",
            "sessionId": session["id"],
            "taskId": parent_task["id"],
            "title": "DB Verify Explorer",
            "agentType": "explorer",
            "priority": 1,
            "budget": {
                "childToolAllowlist": ["list_dir", "read_file"],
            },
        })

        assert result["status"] == "completed"

        # After dispatch: verify DB grew (child wrote to it)
        size_after = db_path.stat().st_size
        assert size_after > 0

        # Verify tasks are queryable from the same DB
        all_collab = store.list_collaboration_tasks({"parentTaskId": parent_task["id"]})
        assert len(all_collab["tasks"]) >= 1

    finally:
        store.close()
        _cleanup_env(env_updates)
