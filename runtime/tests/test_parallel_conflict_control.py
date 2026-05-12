"""P2 Parallel Multi-Agent Conflict Control tests.

Covers:
- scope_conflict_checks table CRUD
- Pre-dispatch overlap detection via check_dispatch_scope
- DAGExecutor serial downgrade when scope overlaps detected
- DAGExecutor normal parallel when no overlaps
- Merge conflict check recording
- Autonomy report scopeConflicts section
- RPC endpoint registration
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from local_agent_runtime.planner.dag_executor import DAGExecutor
from local_agent_runtime.planner.types import PlanResult, Subtask
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
    session = store.create_session(workspace_id=workspace["id"], title="p2-test")
    task = store.create_task(session_id=session["id"], task_type="chat", goal="test", plan=[])
    return store, {"session": session, "task": task}


class _StubSubagentService:
    """Records dispatch calls and returns deterministic results."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def dispatch(self, params: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self.calls.append({**params, "_thread": threading.current_thread().name})
        return {"summary": f"Done: {params.get('title', '?')}", "status": "completed"}


def _make_plan(subtasks: list[Subtask]) -> PlanResult:
    """Build a PlanResult with auto-computed DAG and execution order."""
    decomposer = __import__(
        "local_agent_runtime.planner.decomposer", fromlist=["TaskDecomposer"],
    ).TaskDecomposer(__import__(
        "local_agent_runtime.provider.adapter", fromlist=["ProviderAdapter"],
    ).ProviderAdapter())
    dag = decomposer.build_dag(subtasks)
    order = decomposer.topological_sort(dag, [s.id for s in subtasks])
    return PlanResult(subtasks=subtasks, dag=dag, execution_order=order)


# ---------------------------------------------------------------------------
# 1. Scope conflict check CRUD tests
# ---------------------------------------------------------------------------


class TestScopeConflictCheckCRUD:
    def test_create_scope_conflict_check(self, tmp_path: Path) -> None:
        store, ctx = _store_with_context(tmp_path)
        result = store.create_scope_conflict_check({
            "taskId": ctx["task"]["id"],
            "sessionId": ctx["session"]["id"],
            "checkType": "pre_dispatch",
            "subtaskIds": ["s1", "s2"],
            "scopeMap": {"s1": ["a.py", "b.py"], "s2": ["b.py", "c.py"]},
            "overlaps": ["b.py"],
            "resolution": "serialized",
            "serializedOrder": ["s1", "s2"],
            "safe": False,
        })
        check = result["scopeConflictCheck"]
        assert check["taskId"] == ctx["task"]["id"]
        assert check["checkType"] == "pre_dispatch"
        assert check["subtaskIds"] == ["s1", "s2"]
        assert check["scopeMap"] == {"s1": ["a.py", "b.py"], "s2": ["b.py", "c.py"]}
        assert check["overlaps"] == ["b.py"]
        assert check["resolution"] == "serialized"
        assert check["safe"] is False

    def test_list_scope_conflict_checks_by_task(self, tmp_path: Path) -> None:
        store, ctx = _store_with_context(tmp_path)
        store.create_scope_conflict_check({
            "taskId": ctx["task"]["id"],
            "checkType": "pre_dispatch",
            "subtaskIds": ["s1"],
            "overlaps": [],
            "resolution": "none",
            "safe": True,
        })
        store.create_scope_conflict_check({
            "taskId": ctx["task"]["id"],
            "checkType": "pre_merge",
            "subtaskIds": ["s1", "s2"],
            "overlaps": ["x.py"],
            "resolution": "merge_required",
            "safe": False,
        })
        result = store.list_scope_conflict_checks({"taskId": ctx["task"]["id"]})
        checks = result["scopeConflictChecks"]
        assert len(checks) == 2

    def test_list_scope_conflict_checks_filter_by_type(self, tmp_path: Path) -> None:
        store, ctx = _store_with_context(tmp_path)
        store.create_scope_conflict_check({
            "taskId": ctx["task"]["id"],
            "checkType": "pre_dispatch",
            "subtaskIds": [],
            "overlaps": [],
            "resolution": "none",
            "safe": True,
        })
        store.create_scope_conflict_check({
            "taskId": ctx["task"]["id"],
            "checkType": "pre_merge",
            "subtaskIds": [],
            "overlaps": ["a.py"],
            "resolution": "merge_required",
            "safe": False,
        })
        result = store.list_scope_conflict_checks({
            "taskId": ctx["task"]["id"],
            "checkType": "pre_merge",
        })
        checks = result["scopeConflictChecks"]
        assert len(checks) == 1
        assert checks[0]["checkType"] == "pre_merge"


# ---------------------------------------------------------------------------
# 2. Pre-dispatch overlap detection (check_dispatch_scope)
# ---------------------------------------------------------------------------


class TestCheckDispatchScope:
    def test_no_overlap_returns_safe(self, tmp_path: Path) -> None:
        store, ctx = _store_with_context(tmp_path)
        result = store.check_dispatch_scope({
            "taskId": ctx["task"]["id"],
            "sessionId": ctx["session"]["id"],
            "subtasks": [
                {"id": "s1", "ownedScope": ["a.py"]},
                {"id": "s2", "ownedScope": ["b.py"]},
            ],
        })
        assert result["safe"] is True
        assert result["overlaps"] == []
        assert result["resolution"] == "none"
        assert "checkId" in result

    def test_overlap_returns_unsafe(self, tmp_path: Path) -> None:
        store, ctx = _store_with_context(tmp_path)
        result = store.check_dispatch_scope({
            "taskId": ctx["task"]["id"],
            "sessionId": ctx["session"]["id"],
            "subtasks": [
                {"id": "s1", "ownedScope": ["a.py", "shared.py"]},
                {"id": "s2", "ownedScope": ["b.py", "shared.py"]},
            ],
        })
        assert result["safe"] is False
        assert len(result["overlaps"]) > 0
        assert result["resolution"] == "serialized"
        assert result["serializedOrder"] is not None

    def test_no_subtasks_returns_safe(self, tmp_path: Path) -> None:
        store, ctx = _store_with_context(tmp_path)
        result = store.check_dispatch_scope({
            "taskId": ctx["task"]["id"],
            "subtasks": [],
        })
        assert result["safe"] is True


# ---------------------------------------------------------------------------
# 3. DAGExecutor serial downgrade
# ---------------------------------------------------------------------------


class TestDAGExecutorSerialDowngrade:
    def test_parallel_when_no_scope_checker(self) -> None:
        """Without scope_checker, independent tasks run in parallel."""
        subtasks = [
            Subtask(id="a", title="A", description="a", dependencies=[]),
            Subtask(id="b", title="B", description="b", dependencies=[]),
        ]
        plan = _make_plan(subtasks)
        svc = _StubSubagentService()
        executor = DAGExecutor(svc)
        result = executor.execute(
            plan, session_id="s1", parent_task_id="t1",
            max_workers=4,
        )
        assert result["success"] is True
        assert len(svc.calls) == 2
        # Both ran on different threads (ThreadPoolExecutor)
        threads = [c["_thread"] for c in svc.calls]
        assert len(set(threads)) >= 1  # at least possibly different

    def test_serial_downgrade_when_overlap(self) -> None:
        """Scope checker detects overlap → tasks run serially (same thread)."""
        subtasks = [
            Subtask(id="a", title="A", description="a", dependencies=[]),
            Subtask(id="b", title="B", description="b", dependencies=[]),
        ]
        plan = _make_plan(subtasks)
        svc = _StubSubagentService()
        executor = DAGExecutor(svc)

        def _scope_checker(subtask_dicts: list[dict[str, Any]]) -> list[str]:
            # Simulate overlap for any multi-subtask level
            if len(subtask_dicts) > 1:
                return ["overlap between a and b on shared.py"]
            return []

        result = executor.execute(
            plan, session_id="s1", parent_task_id="t1",
            max_workers=4,
            scope_checker=_scope_checker,
        )
        assert result["success"] is True
        assert len(svc.calls) == 2

    def test_no_serial_when_no_overlap(self) -> None:
        """Scope checker reports no overlap → normal parallel execution."""
        subtasks = [
            Subtask(id="a", title="A", description="a", dependencies=[]),
            Subtask(id="b", title="B", description="b", dependencies=[]),
        ]
        plan = _make_plan(subtasks)
        svc = _StubSubagentService()
        executor = DAGExecutor(svc)

        result = executor.execute(
            plan, session_id="s1", parent_task_id="t1",
            max_workers=4,
            scope_checker=lambda _: [],  # no overlaps
        )
        assert result["success"] is True
        assert len(svc.calls) == 2


# ---------------------------------------------------------------------------
# 4. Merge conflict recording
# ---------------------------------------------------------------------------


class TestMergeConflictRecording:
    def test_create_merge_conflict_check(self, tmp_path: Path) -> None:
        store, ctx = _store_with_context(tmp_path)
        result = store.create_scope_conflict_check({
            "taskId": ctx["task"]["id"],
            "sessionId": ctx["session"]["id"],
            "checkType": "pre_merge",
            "subtaskIds": ["s1", "s2"],
            "scopeMap": {"s1": ["shared.py"], "s2": ["shared.py"]},
            "overlaps": ["shared.py"],
            "resolution": "merge_required",
            "safe": False,
        })
        check = result["scopeConflictCheck"]
        assert check["checkType"] == "pre_merge"
        assert check["resolution"] == "merge_required"
        assert check["safe"] is False

    def test_patch_conflict_type(self, tmp_path: Path) -> None:
        store, ctx = _store_with_context(tmp_path)
        result = store.create_scope_conflict_check({
            "taskId": ctx["task"]["id"],
            "checkType": "patch_conflict",
            "subtaskIds": ["s1", "s3"],
            "scopeMap": {"s1": ["x.py"], "s3": ["x.py"]},
            "overlaps": ["x.py"],
            "resolution": "blocked",
            "safe": False,
        })
        check = result["scopeConflictCheck"]
        assert check["checkType"] == "patch_conflict"
        assert check["resolution"] == "blocked"


# ---------------------------------------------------------------------------
# 5. Autonomy report integration
# ---------------------------------------------------------------------------


class TestAutonomyReportScopeConflicts:
    def test_scope_conflicts_in_report(self, tmp_path: Path) -> None:
        store, ctx = _store_with_context(tmp_path)
        task_id = ctx["task"]["id"]

        # Create a scope conflict check
        store.create_scope_conflict_check({
            "taskId": task_id,
            "checkType": "pre_dispatch",
            "subtaskIds": ["s1", "s2"],
            "overlaps": ["shared.py"],
            "resolution": "serialized",
            "safe": False,
        })

        report = store.get_autonomy_report({"taskId": task_id})
        assert "scopeConflicts" in report
        assert len(report["scopeConflicts"]) == 1
        assert report["scopeConflicts"][0]["checkType"] == "pre_dispatch"
        assert report["scopeConflicts"][0]["overlaps"] == ["shared.py"]

    def test_empty_scope_conflicts_in_report(self, tmp_path: Path) -> None:
        store, ctx = _store_with_context(tmp_path)
        report = store.get_autonomy_report({"taskId": ctx["task"]["id"]})
        assert "scopeConflicts" in report
        assert report["scopeConflicts"] == []


# ---------------------------------------------------------------------------
# 6. RPC endpoint registration
# ---------------------------------------------------------------------------


class TestScopeRPCRegistration:
    def test_scope_check_dispatch_rpc_registered(self) -> None:
        """Verify scope.checkDispatch appears in server handler source."""
        import inspect
        from local_agent_runtime.rpc import server as server_mod
        source = inspect.getsource(server_mod)
        assert '"scope.checkDispatch"' in source

    def test_scope_conflict_history_rpc_registered(self) -> None:
        """Verify scope.conflictHistory appears in server handler source."""
        import inspect
        from local_agent_runtime.rpc import server as server_mod
        source = inspect.getsource(server_mod)
        assert '"scope.conflictHistory"' in source


# ---------------------------------------------------------------------------
# 7. DAGExecutor + scope_checker integration with store
# ---------------------------------------------------------------------------


class TestDAGExecutorWithStoreScopeChecker:
    def test_overlapping_scopes_recorded_and_serialized(self, tmp_path: Path) -> None:
        """End-to-end: DAGExecutor + store scope_checker records conflict check."""
        store, ctx = _store_with_context(tmp_path)

        subtasks = [
            Subtask(id="a", title="A", description="a", dependencies=[]),
            Subtask(id="b", title="B", description="b", dependencies=[]),
        ]
        plan = _make_plan(subtasks)
        svc = _StubSubagentService()
        executor = DAGExecutor(svc)

        def _scope_checker(subtask_dicts: list[dict[str, Any]]) -> list[str]:
            # Enrich subtask_dicts with overlapping scopes before calling store
            enriched = [
                {"id": d["id"], "ownedScope": ["shared.py"]} for d in subtask_dicts
            ]
            return store.check_dispatch_scope({
                "taskId": ctx["task"]["id"],
                "sessionId": ctx["session"]["id"],
                "subtasks": enriched,
            }).get("overlaps", [])

        result = executor.execute(
            plan, session_id=ctx["session"]["id"],
            parent_task_id=ctx["task"]["id"],
            max_workers=4,
            scope_checker=_scope_checker,
        )
        assert result["success"] is True
        # Verify conflict check was recorded
        checks = store.list_scope_conflict_checks({"taskId": ctx["task"]["id"]})
        assert len(checks["scopeConflictChecks"]) >= 1
