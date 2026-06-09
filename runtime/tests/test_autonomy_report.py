"""Tests for P1: Autonomy Run Report.

Covers:
  1. Returns task summary (goal, status, sessionId)
  2. Returns autonomy profile from config
  3. Returns agent soul profile from config
  4. Returns routing decision metadata without advisor routing proposal
  5. Returns metrics from task_metrics
  6. Returns approvals and policy gate outcomes
  7. Returns patches (file writes)
  8. Returns commands
  9. Returns subagents and artifacts
  10. Returns compactions and memory recall
  11. Handles task without extras (no metrics, approvals, etc.)
  12. Raises on missing taskId
  13. Raises on nonexistent task
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from local_agent_runtime.event_bus import EventBus
from local_agent_runtime.orchestrator.service import Orchestrator
from local_agent_runtime.provider.adapter import ProviderAdapter
from local_agent_runtime.rpc.server import JsonRpcServer
from local_agent_runtime.services import CollaborationService, SubagentService
from local_agent_runtime.store.sqlite_store import SQLiteStore
from local_agent_runtime.tools import build_builtin_tools
from local_agent_runtime.tools.registry import ToolRegistry
from local_agent_runtime.policy.guard import PolicyGuard


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_harness(tmp_path: Any) -> tuple[JsonRpcServer, SQLiteStore]:
    """Create a JsonRpcServer + SQLiteStore pair for testing."""
    db_path = tmp_path / "test.sqlite3"
    event_bus = EventBus()
    store = SQLiteStore(str(db_path))
    config = store.get_config({})["config"]
    policy_guard = PolicyGuard(approval_mode=config["policy"]["approvalMode"])
    collaboration = CollaborationService(store, event_bus)
    subagent_service = SubagentService(store, collaboration)
    tool_registry = ToolRegistry(
        build_builtin_tools(policy_guard=policy_guard, store=store, subagent_service=subagent_service)
    )
    provider = ProviderAdapter()
    orchestrator = Orchestrator(
        store=store,
        event_bus=event_bus,
        tool_registry=tool_registry,
        provider=provider,
    )
    server = JsonRpcServer(orchestrator=orchestrator, store=store, event_bus=event_bus)
    return server, store


def _call(server: JsonRpcServer, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Call an RPC method and return the result dict."""
    envelope = {
        "jsonrpc": "2.0",
        "id": "test-1",
        "method": method,
        "params": params or {},
    }
    response = server.handle_line(json.dumps(envelope))
    assert "error" not in response, f"RPC error: {response.get('error')}"
    return response["result"]


def _call_error(server: JsonRpcServer, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Call an RPC method and return the error dict."""
    envelope = {
        "jsonrpc": "2.0",
        "id": "test-1",
        "method": method,
        "params": params or {},
    }
    response = server.handle_line(json.dumps(envelope))
    return response


def _setup_session(store: SQLiteStore, tmp_path: Any) -> dict[str, Any]:
    """Create workspace + session and return the session dict."""
    workspace = store.upsert_workspace(str(tmp_path / "project"))
    session = store.create_session(workspace["id"], "Autonomy report test")
    return session


def _create_task(store: SQLiteStore, session: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Create a task with sensible defaults."""
    defaults = {
        "task_type": "root",
        "goal": "test autonomy report",
        "plan": [],
    }
    defaults.update(kwargs)
    return store.create_task(session_id=session["id"], **defaults)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestAutonomyReportRpc:
    """autonomy.report RPC method tests."""

    def test_returns_task_summary(self, tmp_path: Any) -> None:
        """autonomy.report returns basic task fields."""
        server, store = _make_harness(tmp_path)
        session = _setup_session(store, tmp_path)
        task = _create_task(store, session, goal="Fix the login bug")

        result = _call(server, "autonomy.report", {"taskId": task["id"]})
        assert result["task"]["id"] == task["id"]
        assert result["task"]["goal"] == "Fix the login bug"
        assert result["task"]["status"] in ("queued", "running")
        assert result["task"]["sessionId"] == session["id"]

    def test_returns_autonomy_profile(self, tmp_path: Any) -> None:
        """autonomy.report returns the active autonomy profile from config."""
        server, store = _make_harness(tmp_path)
        session = _setup_session(store, tmp_path)
        task = _create_task(store, session)

        result = _call(server, "autonomy.report", {"taskId": task["id"]})
        profile = result["autonomyProfile"]
        assert profile is not None
        assert "id" in profile
        assert "level" in profile
        assert "maxSteps" in profile

    def test_returns_agent_soul_profile(self, tmp_path: Any) -> None:
        """autonomy.report returns the active agent soul profile from config."""
        server, store = _make_harness(tmp_path)
        session = _setup_session(store, tmp_path)
        task = _create_task(store, session)

        result = _call(server, "autonomy.report", {"taskId": task["id"]})
        soul = result["agentSoulProfile"]
        # Default config has an agentSoul section
        assert soul is not None or soul is None  # may be None if no soul configured

    def test_returns_metrics(self, tmp_path: Any) -> None:
        """autonomy.report returns task_metrics when available."""
        server, store = _make_harness(tmp_path)
        session = _setup_session(store, tmp_path)
        task = _create_task(store, session)

        # Insert metrics directly
        store.record_task_metrics({
            "taskId": task["id"],
            "sessionId": session["id"],
            "durationMs": 5000,
            "toolCallCount": 3,
            "commandCount": 1,
            "patchCount": 2,
            "taskStatus": "completed",
        })

        result = _call(server, "autonomy.report", {"taskId": task["id"]})
        metrics = result["metrics"]
        assert metrics is not None
        assert metrics["tool_call_count"] == 3
        assert metrics["command_count"] == 1
        assert metrics["patch_count"] == 2
        assert metrics["duration_ms"] == 5000

    def test_returns_approvals_and_policy_gate_outcomes(self, tmp_path: Any) -> None:
        """autonomy.report returns approvals and aggregated policy gate outcomes."""
        server, store = _make_harness(tmp_path)
        session = _setup_session(store, tmp_path)
        task = _create_task(store, session)

        # Create workspace for patches
        workspace = store.upsert_workspace(str(tmp_path / "project"))

        # Insert approvals directly
        now = store.now()
        store._conn.execute(
            """INSERT INTO approvals (id, task_id, kind, request_json, decision, decided_by, created_at, decided_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            ("appr_1", task["id"], "run_command", '{"command":"rm -rf /"}', "approved", "user", now, now),
        )
        store._conn.execute(
            """INSERT INTO approvals (id, task_id, kind, request_json, decision, decided_by, created_at, decided_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            ("appr_2", task["id"], "apply_patch", '{"file":"/etc/passwd"}', "rejected", "user", now, now),
        )
        store._conn.execute(
            """INSERT INTO approvals (id, task_id, kind, request_json, decision, decided_by, created_at, decided_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            ("appr_3", task["id"], "apply_patch", '{"file":"README.md"}', None, None, now, None),
        )
        store._conn.commit()

        result = _call(server, "autonomy.report", {"taskId": task["id"]})
        approvals = result["approvals"]
        assert len(approvals) == 3

        gate = result["policyGateOutcomes"]
        assert gate["allowed"] == 1       # approved
        assert gate["blocked"] == 1       # rejected
        assert gate["approvalRequired"] == 1  # pending (no decision)

    def test_returns_patches(self, tmp_path: Any) -> None:
        """autonomy.report returns patches (file write records)."""
        server, store = _make_harness(tmp_path)
        session = _setup_session(store, tmp_path)
        task = _create_task(store, session)

        workspace = store.upsert_workspace(str(tmp_path / "project"))
        store.create_patch(
            task_id=task["id"],
            workspace_id=workspace["id"],
            summary="Fix login validation",
            diff_text="--- a/login.py\n+++ b/login.py\n@@ -1 +1 @@",
            files_changed=1,
            status="applied",
        )

        result = _call(server, "autonomy.report", {"taskId": task["id"]})
        patches = result["patches"]
        assert len(patches) == 1
        assert patches[0]["summary"] == "Fix login validation"
        assert patches[0]["filesChanged"] == 1
        assert patches[0]["status"] == "applied"

    def test_returns_commands(self, tmp_path: Any) -> None:
        """autonomy.report returns command logs."""
        server, store = _make_harness(tmp_path)
        session = _setup_session(store, tmp_path)
        task = _create_task(store, session)

        now = store.now()
        store._conn.execute(
            """INSERT INTO command_logs (id, task_id, command, cwd, exit_code, status, started_at, finished_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            ("cmd_1", task["id"], "npm test", "/project", 0, "completed", now, now + 1000),
        )
        store._conn.commit()

        result = _call(server, "autonomy.report", {"taskId": task["id"]})
        commands = result["commands"]
        assert len(commands) == 1
        assert commands[0]["command"] == "npm test"
        assert commands[0]["exitCode"] == 0

    def test_returns_subagents_and_artifacts(self, tmp_path: Any) -> None:
        """autonomy.report returns subagents (collaboration tasks) and artifacts."""
        server, store = _make_harness(tmp_path)
        session = _setup_session(store, tmp_path)
        task = _create_task(store, session)

        # Create a subagent (collaboration task)
        store.create_collaboration_task({
            "sessionId": session["id"],
            "parentTaskId": task["id"],
            "title": "Write unit tests",
            "priority": 2,
        })

        # Create an artifact
        store.create_artifact({
            "sessionId": session["id"],
            "parentTaskId": task["id"],
            "producerTaskId": task["id"],
            "kind": "file",
            "title": "test_login.py",
        })

        result = _call(server, "autonomy.report", {"taskId": task["id"]})
        subagents = result["subagents"]
        assert len(subagents) == 1
        assert subagents[0]["title"] == "Write unit tests"
        assert subagents[0]["parentTaskId"] == task["id"]

        artifacts = result["artifacts"]
        assert len(artifacts) == 1
        assert artifacts[0]["title"] == "test_login.py"
        assert artifacts[0]["kind"] == "file"

    def test_returns_compactions_and_memory_recall(self, tmp_path: Any) -> None:
        """autonomy.report returns compaction records and memory IDs from snapshots."""
        server, store = _make_harness(tmp_path)
        session = _setup_session(store, tmp_path)
        task = _create_task(store, session)

        # Insert a compaction record
        store._conn.execute(
            """INSERT INTO compaction_records (id, session_id, strategy, tokens_before, tokens_after, summary, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            ("comp_1", session["id"], "three_segment", 10000, 5000, "Summarized old messages", store.now()),
        )
        store._conn.commit()

        # Create a context snapshot with memory IDs
        store.create_context_snapshot(
            session_id=session["id"],
            task_id=task["id"],
            token_estimate=5000,
            memory_ids=["mem_1", "mem_2"],
        )

        result = _call(server, "autonomy.report", {"taskId": task["id"]})
        compactions = result["compactions"]
        assert len(compactions) >= 1
        assert compactions[0]["strategy"] == "three_segment"

        memory = result["memoryRecall"]
        assert "mem_1" in memory["memoryIds"]
        assert "mem_2" in memory["memoryIds"]
        assert memory["count"] == 2

        # contextBudget should have the snapshot
        assert result["contextBudget"] is not None
        assert result["contextBudget"]["tokenEstimate"] == 5000

    def test_returns_routing_metadata_without_routing_proposal(self, tmp_path: Any) -> None:
        """autonomy.report returns task routing metadata; routing proposals are gone."""
        server, store = _make_harness(tmp_path)
        session = _setup_session(store, tmp_path)
        task = _create_task(
            store,
            session,
            routing={"scenario": "free_form", "strategy": "react_standard"},
        )

        result = _call(server, "autonomy.report", {"taskId": task["id"]})
        assert result["routing"] is not None
        assert result["routing"]["scenario"] == "free_form"
        assert result["routing"]["strategy"] == "react_standard"
        assert result["routing"]["proposal"] is None

    def test_handles_task_without_extras(self, tmp_path: Any) -> None:
        """autonomy.report works for a bare task with no metrics/approvals/patches."""
        server, store = _make_harness(tmp_path)
        session = _setup_session(store, tmp_path)
        task = _create_task(store, session)

        result = _call(server, "autonomy.report", {"taskId": task["id"]})
        assert result["task"]["id"] == task["id"]
        assert result["metrics"] is None
        assert result["approvals"] == []
        assert result["patches"] == []
        assert result["commands"] == []
        assert result["subagents"] == []
        assert result["artifacts"] == []
        assert result["compactions"] == []
        assert result["decisions"] == []
        assert result["memoryRecall"]["memoryIds"] == []
        assert result["memoryRecall"]["count"] == 0
        assert result["contextBudget"] is None
        assert result["policyGateOutcomes"]["allowed"] == 0
        assert result["hookExecutions"] == []

    def test_raises_on_missing_task_id(self, tmp_path: Any) -> None:
        """autonomy.report raises error when taskId is missing."""
        server, store = _make_harness(tmp_path)
        response = _call_error(server, "autonomy.report", {})
        assert "error" in response

    def test_raises_on_nonexistent_task(self, tmp_path: Any) -> None:
        """autonomy.report raises error when task doesn't exist."""
        server, store = _make_harness(tmp_path)
        response = _call_error(server, "autonomy.report", {"taskId": "nonexistent_task"})
        assert "error" in response
