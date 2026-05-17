"""Tests for P1: Context Budget Panel.

Covers:
  1. context.budget returns maxContextTokens from config
  2. context.budget returns latest snapshot by taskId
  3. context.budget returns latest snapshot by sessionId
  4. context.budget returns empty when no snapshots exist
  5. context.budget returns tokenTrend across turns
  6. context.budget returns compaction records
  7. context.budget returns promptLayers from snapshot
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


def _setup_session(store: SQLiteStore, tmp_path: Any) -> dict[str, Any]:
    """Create workspace + session and return the session dict."""
    workspace = store.upsert_workspace(str(tmp_path / "project"))
    session = store.create_session(workspace["id"], "Context budget test")
    return session


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestContextBudgetRpc:
    """context.budget RPC method tests."""

    def test_returns_max_context_tokens_from_config(self, tmp_path: Any) -> None:
        """context.budget returns maxContextTokens from config."""
        server, store = _make_harness(tmp_path)
        # Default config should return 256000
        result = _call(server, "context.budget")
        assert result["maxContextTokens"] == 256000
        assert result["latestSnapshot"] is None
        assert result["compactions"] == []
        assert result["tokenTrend"] == []
        assert result["promptLayers"] == []

    def test_returns_max_context_tokens_from_provider_config(self, tmp_path: Any) -> None:
        """context.budget returns maxContextTokens from provider config."""
        server, store = _make_harness(tmp_path)
        store.update_config({"config": {
            "provider": {"mode": "mock", "maxContextTokens": 128000},
        }})
        result = _call(server, "context.budget")
        assert result["maxContextTokens"] == 128000

    def test_returns_latest_snapshot_by_task_id(self, tmp_path: Any) -> None:
        """context.budget returns latest snapshot when filtered by taskId."""
        server, store = _make_harness(tmp_path)
        session = _setup_session(store, tmp_path)

        # Create a task and snapshot
        task = store.create_task(
            session_id=session["id"],
            task_type="root",
            goal="test budget",
            plan=[],
        )
        store.create_context_snapshot(
            session_id=session["id"],
            task_id=task["id"],
            token_estimate=5000,
            max_context_tokens=256000,
            prompt_layers=[{"name": "role", "tokenEstimate": 120}],
            included_sections=["system_prompt", "workspace_summary"],
            trimmed_sections=[{"name": "key_files", "tokensTrimmed": 200}],
            dropped_sections=[],
            memory_ids=["mem_1", "mem_2"],
            tool_count=15,
        )

        result = _call(server, "context.budget", {"taskId": task["id"]})
        assert result["latestSnapshot"] is not None
        snap = result["latestSnapshot"]
        assert snap["taskId"] == task["id"]
        assert snap["tokenEstimate"] == 5000
        assert snap["maxContextTokens"] == 256000
        assert "system_prompt" in snap["includedSections"]
        assert len(snap["trimmedSections"]) == 1
        assert snap["memoryIds"] == ["mem_1", "mem_2"]
        assert snap["toolCount"] == 15

    def test_returns_latest_snapshot_by_session_id(self, tmp_path: Any) -> None:
        """context.budget returns latest snapshot when filtered by sessionId."""
        server, store = _make_harness(tmp_path)
        session = _setup_session(store, tmp_path)

        # Create a task and snapshot
        task = store.create_task(
            session_id=session["id"],
            task_type="root",
            goal="test budget",
            plan=[],
        )
        store.create_context_snapshot(
            session_id=session["id"],
            task_id=task["id"],
            token_estimate=3000,
            max_context_tokens=128000,
        )

        result = _call(server, "context.budget", {"sessionId": session["id"]})
        assert result["latestSnapshot"] is not None
        assert result["latestSnapshot"]["tokenEstimate"] == 3000
        assert result["latestSnapshot"]["maxContextTokens"] == 128000

    def test_returns_empty_when_no_snapshots(self, tmp_path: Any) -> None:
        """context.budget returns null snapshot when no snapshots exist."""
        server, store = _make_harness(tmp_path)

        result = _call(server, "context.budget", {"taskId": "nonexistent"})
        assert result["maxContextTokens"] == 256000
        assert result["latestSnapshot"] is None
        assert result["tokenTrend"] == []

    def test_returns_token_trend_across_turns(self, tmp_path: Any) -> None:
        """context.budget returns tokenTrend with per-turn estimates."""
        server, store = _make_harness(tmp_path)
        session = _setup_session(store, tmp_path)

        task = store.create_task(
            session_id=session["id"],
            task_type="root",
            goal="test trend",
            plan=[],
        )
        # Create multiple snapshots simulating turns
        store.create_context_snapshot(
            session_id=session["id"], task_id=task["id"], token_estimate=2000,
        )
        store.create_context_snapshot(
            session_id=session["id"], task_id=task["id"], token_estimate=3500,
        )
        store.create_context_snapshot(
            session_id=session["id"], task_id=task["id"], token_estimate=5000,
        )

        result = _call(server, "context.budget", {"taskId": task["id"]})
        trend = result["tokenTrend"]
        assert len(trend) == 3
        assert trend[0]["tokenEstimate"] == 2000
        assert trend[1]["tokenEstimate"] == 3500
        assert trend[2]["tokenEstimate"] == 5000
        # Should be ordered ASC by created_at
        assert trend[0]["createdAt"] <= trend[1]["createdAt"] <= trend[2]["createdAt"]

    def test_returns_compaction_records(self, tmp_path: Any) -> None:
        """context.budget returns compaction records for the session."""
        server, store = _make_harness(tmp_path)
        session = _setup_session(store, tmp_path)

        task = store.create_task(
            session_id=session["id"],
            task_type="root",
            goal="test compaction",
            plan=[],
        )

        # Insert a compaction record directly
        store._conn.execute(
            """INSERT INTO compaction_records (id, session_id, strategy, tokens_before, tokens_after, summary, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            ("comp_1", session["id"], "three_segment", 10000, 5000, "Summarized old messages", store.now()),
        )
        store._conn.commit()

        result = _call(server, "context.budget", {"taskId": task["id"]})
        compactions = result["compactions"]
        assert len(compactions) >= 1
        c = compactions[0]
        assert c["session_id"] == session["id"]
        assert c["strategy"] == "three_segment"
        assert c["tokens_before"] == 10000
        assert c["tokens_after"] == 5000

    def test_returns_structured_compaction_handoff(self, tmp_path: Any) -> None:
        """context.budget exposes parsed handoffSummary for recovery UI and follow-up turns."""
        server, store = _make_harness(tmp_path)
        session = _setup_session(store, tmp_path)
        task = store.create_task(
            session_id=session["id"],
            task_type="root",
            goal="recover from compaction",
            plan=[],
        )
        handoff = {
            "version": 1,
            "objective": "recover from compaction",
            "verificationStatus": "failed",
            "nextCommand": "Fix or rerun failed command: pytest",
        }
        store._conn.execute(
            """INSERT INTO compaction_records (
                   id, session_id, task_id, strategy, tokens_before, tokens_after,
                   summary, handoff_summary_json, created_at
               )
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "comp_handoff",
                session["id"],
                task["id"],
                "primer_summary_recent",
                10000,
                6000,
                "summary",
                json.dumps(handoff),
                store.now(),
            ),
        )
        store._conn.commit()

        result = _call(server, "context.budget", {"taskId": task["id"]})

        assert result["compactions"][0]["id"] == "comp_handoff"
        assert result["compactions"][0]["handoffSummary"]["objective"] == "recover from compaction"
        assert result["compactions"][0]["handoffSummary"]["verificationStatus"] == "failed"

    def test_returns_prompt_layers_from_snapshot(self, tmp_path: Any) -> None:
        """context.budget returns promptLayers from latest snapshot."""
        server, store = _make_harness(tmp_path)
        session = _setup_session(store, tmp_path)

        task = store.create_task(
            session_id=session["id"],
            task_type="root",
            goal="test layers",
            plan=[],
        )
        layers = [
            {"name": "role", "tokenEstimate": 120},
            {"name": "agent_soul", "tokenEstimate": 85, "profileId": "soul-1"},
            {"name": "runtime_safety", "tokenEstimate": 200, "locked": True},
        ]
        store.create_context_snapshot(
            session_id=session["id"],
            task_id=task["id"],
            token_estimate=1000,
            prompt_layers=layers,
        )

        result = _call(server, "context.budget", {"taskId": task["id"]})
        assert len(result["promptLayers"]) == 3
        assert result["promptLayers"][0]["name"] == "role"
        assert result["promptLayers"][2]["name"] == "runtime_safety"
        assert result["promptLayers"][2]["locked"] is True

    def test_resolves_session_from_task_id_for_compactions(self, tmp_path: Any) -> None:
        """context.budget resolves sessionId from taskId to fetch compaction records."""
        server, store = _make_harness(tmp_path)
        session = _setup_session(store, tmp_path)

        task = store.create_task(
            session_id=session["id"],
            task_type="root",
            goal="test session resolution",
            plan=[],
        )

        # Insert a compaction record for this session
        store._conn.execute(
            """INSERT INTO compaction_records (id, session_id, strategy, tokens_before, tokens_after, summary, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            ("comp_2", session["id"], "three_segment", 8000, 4000, "Test", store.now()),
        )
        store._conn.commit()

        # Query by taskId only — should find compaction via session resolution
        result = _call(server, "context.budget", {"taskId": task["id"]})
        assert len(result["compactions"]) >= 1
        assert result["compactions"][0]["id"] == "comp_2"
