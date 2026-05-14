"""P2 Replay And Dry-Run Replay tests.

Covers:
- replay_sessions table CRUD (create, get, list, update)
- ReplayService audit_replay: empty task, full timeline, warnings for unavailable
- ReplayService dry_run_replay: gate re-evaluation, config overrides
- RPC endpoint registration
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from local_agent_runtime.services.replay_service import ReplayService
from local_agent_runtime.store.sqlite_store import SQLiteStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_store(tmp_path: Path) -> SQLiteStore:
    return SQLiteStore(str(tmp_path / "runtime.sqlite3"))


def _store_with_context(tmp_path: Path) -> tuple[SQLiteStore, dict[str, Any]]:
    store = _make_store(tmp_path)
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(parents=True, exist_ok=True)
    workspace = store.upsert_workspace(str(workspace_root))
    session = store.create_session(workspace_id=workspace["id"], title="replay-test")
    task = store.create_task(session_id=session["id"], task_type="chat", goal="test replay", plan=[])
    return store, {"session": session, "task": task}


def _seed_trace_events(store: SQLiteStore, task: dict[str, Any], session: dict[str, Any], count: int = 3) -> None:
    """Insert trace events for a task."""
    for i in range(count):
        store.append_trace_event(
            task_id=task["id"],
            session_id=session["id"],
            event_type=f"agent.decision.test_{i}",
            source="test",
            payload={"index": i},
        )


def _seed_proposal(store: SQLiteStore, task: dict[str, Any], session: dict[str, Any], kind: str = "decomposition",
                   proposal_data: dict | None = None) -> str:
    """Insert a proposal record."""
    result = store.create_proposal({
        "kind": kind,
        "sessionId": session["id"],
        "taskId": task["id"],
        "proposal": proposal_data or {"subtasks": []},
    })
    return result["proposal"]["id"]


def _seed_provider_turn(
    store: SQLiteStore,
    task: dict[str, Any],
    session: dict[str, Any],
    *,
    tool_policy_decision: dict[str, Any] | None = None,
    role_snapshot: dict[str, Any] | None = None,
) -> str:
    """Insert a provider turn record."""
    result = store.create_provider_turn(
        task_id=task["id"],
        session_id=session["id"],
        turn_index=0,
        model="test-model",
        tool_policy_decision=tool_policy_decision,
        role_snapshot=role_snapshot,
    )
    turn_id = result["id"]
    # Complete it to set status/thought
    store.complete_provider_turn(
        turn_id=turn_id,
        finish_reason="stop",
        thought_summary="test thought",
        turn_decision="continue",
    )
    return turn_id


# ---------------------------------------------------------------------------
# replay_sessions CRUD
# ---------------------------------------------------------------------------


class TestReplaySessionCRUD:
    def test_create_replay_session(self, tmp_path: Path) -> None:
        store, ctx = _store_with_context(tmp_path)
        result = store.create_replay_session({
            "sourceTaskId": ctx["task"]["id"],
            "mode": "audit",
            "status": "completed",
            "timeline": [{"step": "trace_event", "type": "test"}],
            "warnings": [],
            "summary": "Audit replay: 1 steps",
        })
        rs = result["replaySession"]
        assert rs["sourceTaskId"] == ctx["task"]["id"]
        assert rs["mode"] == "audit"
        assert rs["status"] == "completed"
        assert rs["timeline"] == [{"step": "trace_event", "type": "test"}]
        assert rs["createdAt"] > 0

    def test_get_replay_session(self, tmp_path: Path) -> None:
        store, ctx = _store_with_context(tmp_path)
        created = store.create_replay_session({
            "sourceTaskId": ctx["task"]["id"],
            "mode": "audit",
            "status": "pending",
        })
        replay_id = created["replaySession"]["id"]

        result = store.get_replay_session({"replayId": replay_id})
        assert result["replaySession"]["id"] == replay_id
        assert result["replaySession"]["mode"] == "audit"

    def test_list_replay_sessions(self, tmp_path: Path) -> None:
        store, ctx = _store_with_context(tmp_path)
        task_id = ctx["task"]["id"]
        store.create_replay_session({"sourceTaskId": task_id, "mode": "audit"})
        store.create_replay_session({"sourceTaskId": task_id, "mode": "dry_run"})

        # List all
        all_sessions = store.list_replay_sessions({"limit": 10})
        assert len(all_sessions["replaySessions"]) == 2

        # Filter by taskId
        task_sessions = store.list_replay_sessions({"sourceTaskId": task_id})
        assert len(task_sessions["replaySessions"]) == 2

        # Filter by mode
        audit_sessions = store.list_replay_sessions({"mode": "audit"})
        assert len(audit_sessions["replaySessions"]) == 1
        assert audit_sessions["replaySessions"][0]["mode"] == "audit"

    def test_update_replay_session(self, tmp_path: Path) -> None:
        store, ctx = _store_with_context(tmp_path)
        created = store.create_replay_session({
            "sourceTaskId": ctx["task"]["id"],
            "mode": "audit",
            "status": "pending",
        })
        replay_id = created["replaySession"]["id"]

        updated = store.update_replay_session({
            "replayId": replay_id,
            "status": "completed",
            "summary": "Done",
        })
        assert updated["replaySession"]["status"] == "completed"
        assert updated["replaySession"]["summary"] == "Done"


# ---------------------------------------------------------------------------
# ReplayService — audit_replay
# ---------------------------------------------------------------------------


class TestAuditReplay:
    def test_empty_task_timeline(self, tmp_path: Path) -> None:
        store, ctx = _store_with_context(tmp_path)
        service = ReplayService(store)

        result = service.audit_replay({"taskId": ctx["task"]["id"]})
        assert result["timeline"] == []
        assert result["warnings"] == []
        assert "replaySession" in result
        assert result["replaySession"]["mode"] == "audit"
        assert result["replaySession"]["status"] == "completed"

    def test_full_timeline_with_trace_events(self, tmp_path: Path) -> None:
        store, ctx = _store_with_context(tmp_path)
        _seed_trace_events(store, ctx["task"], ctx["session"], count=5)
        service = ReplayService(store)

        result = service.audit_replay({"taskId": ctx["task"]["id"]})
        assert len(result["timeline"]) == 5
        assert all(e["step"] == "trace_event" for e in result["timeline"])
        assert all(e["replayable"] is True for e in result["timeline"])

    def test_timeline_sorted_by_timestamp(self, tmp_path: Path) -> None:
        store, ctx = _store_with_context(tmp_path)
        _seed_trace_events(store, ctx["task"], ctx["session"], count=3)
        _seed_proposal(store, ctx["task"], ctx["session"])
        service = ReplayService(store)

        result = service.audit_replay({"taskId": ctx["task"]["id"]})
        timestamps = [e["timestamp"] for e in result["timeline"]]
        assert timestamps == sorted(timestamps)

    def test_provider_turns_marked_unavailable(self, tmp_path: Path) -> None:
        store, ctx = _store_with_context(tmp_path)
        _seed_trace_events(store, ctx["task"], ctx["session"], count=1)
        _seed_provider_turn(store, ctx["task"], ctx["session"])
        service = ReplayService(store)

        result = service.audit_replay({"taskId": ctx["task"]["id"]})
        assert len(result["timeline"]) == 2
        # Provider turn should be marked not replayable
        pt = [e for e in result["timeline"] if e["step"] == "provider_turn"]
        assert len(pt) == 1
        assert pt[0]["replayable"] is False
        assert len(result["warnings"]) == 1

    def test_provider_turn_timeline_includes_tool_policy_explanation(self, tmp_path: Path) -> None:
        store, ctx = _store_with_context(tmp_path)
        _seed_provider_turn(
            store,
            ctx["task"],
            ctx["session"],
            tool_policy_decision={
                "phase": "investigation",
                "runtimeRole": "root",
                "agentType": "root",
                "allowedToolNames": ["read_file"],
                "deniedToolNames": ["run_command"],
                "reasons": {"run_command": "blocked by PermissionEngine"},
                "decisionDetails": [
                    {"toolName": "read_file", "finalDecision": "allowed"},
                    {"toolName": "run_command", "finalDecision": "denied", "reason": "blocked by PermissionEngine"},
                ],
                "policyVersion": "tool-policy-v2",
            },
            role_snapshot={"runtimeRole": "root", "agentType": "root"},
        )
        service = ReplayService(store)

        result = service.audit_replay({"taskId": ctx["task"]["id"]})
        provider_turn = next(entry for entry in result["timeline"] if entry["step"] == "provider_turn")

        assert provider_turn["toolPolicyDecision"]["allowedToolNames"] == ["read_file"]
        assert provider_turn["roleSnapshot"]["runtimeRole"] == "root"
        explanation = provider_turn["toolPolicyExplanation"]
        assert explanation["phase"] == "investigation"
        assert explanation["allowedTools"] == ["read_file"]
        assert explanation["deniedTools"] == ["run_command"]
        assert explanation["denyReasons"] == [
            {"toolName": "run_command", "reason": "blocked by PermissionEngine"}
        ]

    def test_summary_format(self, tmp_path: Path) -> None:
        store, ctx = _store_with_context(tmp_path)
        _seed_trace_events(store, ctx["task"], ctx["session"], count=2)
        _seed_proposal(store, ctx["task"], ctx["session"])
        service = ReplayService(store)

        result = service.audit_replay({"taskId": ctx["task"]["id"]})
        assert "Audit replay: 3 steps" in result["summary"]


# ---------------------------------------------------------------------------
# ReplayService — dry_run_replay
# ---------------------------------------------------------------------------


class TestDryRunReplay:
    def test_dry_run_basic(self, tmp_path: Path) -> None:
        store, ctx = _store_with_context(tmp_path)
        _seed_trace_events(store, ctx["task"], ctx["session"], count=2)
        service = ReplayService(store)

        result = service.dry_run_replay({"taskId": ctx["task"]["id"]})
        assert "replaySession" in result
        assert result["replaySession"]["mode"] == "dry_run"
        assert result["gateEvaluations"] == []
        assert "Dry-run replay:" in result["summary"]

    def test_dry_run_with_decomposition_proposal(self, tmp_path: Path) -> None:
        store, ctx = _store_with_context(tmp_path)
        # Create a decomposition proposal with subtasks that have overlapping scopes
        _seed_proposal(
            store, ctx["task"], ctx["session"],
            kind="decomposition",
            proposal_data={
                "subtasks": [
                    {"id": "s1", "ownedScope": ["src/a.py"]},
                    {"id": "s2", "ownedScope": ["src/a.py"]},
                ],
            },
        )
        service = ReplayService(store)

        result = service.dry_run_replay({
            "taskId": ctx["task"]["id"],
            "configOverrides": {"strictWriteScopes": True},
        })
        assert len(result["gateEvaluations"]) == 1
        gate = result["gateEvaluations"][0]
        assert gate["step"] == "proposal"
        assert gate["kind"] == "decomposition"
        # With overlapping scopes, should have validation reasons
        assert len(gate["newValidationReasons"]) > 0

    def test_dry_run_config_overrides_stored(self, tmp_path: Path) -> None:
        store, ctx = _store_with_context(tmp_path)
        service = ReplayService(store)
        overrides = {"strictWriteScopes": True, "maxParallelSubtasks": 1}

        result = service.dry_run_replay({
            "taskId": ctx["task"]["id"],
            "configOverrides": overrides,
        })
        assert result["replaySession"]["configOverrides"] == overrides

    def test_dry_run_no_config_no_scope_revalidation(self, tmp_path: Path) -> None:
        store, ctx = _store_with_context(tmp_path)
        _seed_proposal(
            store, ctx["task"], ctx["session"],
            kind="decomposition",
            proposal_data={
                "subtasks": [
                    {"id": "s1", "ownedScope": ["src/a.py"]},
                    {"id": "s2", "ownedScope": ["src/b.py"]},
                ],
            },
        )
        service = ReplayService(store)

        # Without strictWriteScopes override, no scope re-validation
        result = service.dry_run_replay({"taskId": ctx["task"]["id"]})
        gate = result["gateEvaluations"][0]
        assert gate["newValidationReasons"] == []


# ---------------------------------------------------------------------------
# RPC Registration
# ---------------------------------------------------------------------------


class TestReplayRPC:
    def test_rpc_handlers_registered(self) -> None:
        from local_agent_runtime.rpc.server import JsonRpcServer
        import inspect
        src = inspect.getsource(JsonRpcServer)
        assert '"replay.audit"' in src
        assert '"replay.dryRun"' in src

    def test_task_id_required(self, tmp_path: Path) -> None:
        store, ctx = _store_with_context(tmp_path)
        service = ReplayService(store)

        with pytest.raises(ValueError, match="taskId is required"):
            service.audit_replay({})
        with pytest.raises(ValueError, match="taskId is required"):
            service.dry_run_replay({})
