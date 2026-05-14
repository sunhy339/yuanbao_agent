"""Tests for P0: Worktree Isolation.

Covers:
  1. CRUD: create worktree
  2. CRUD: get worktree by id
  3. CRUD: get worktree by task
  4. CRUD: list worktrees by workspace
  5. CRUD: list worktrees by session
  6. CRUD: list worktrees by status
  7. CRUD: update worktree status
  8. CRUD: update worktree fields
  9. CRUD: delete worktree
  10. Validation: invalid cleanup policy raises
  11. Validation: invalid merge policy raises
  12. Validation: invalid status on update raises
  13. Validation: missing required fields raises
  14. RPC: worktree.* RPCs are registered
"""
from __future__ import annotations

from typing import Any

import pytest

from local_agent_runtime.git.worktree_adapter import GitWorktreeAdapter
from local_agent_runtime.orchestrator.approval_flow import ApprovalFlowMixin
from local_agent_runtime.services.worktree_service import WorktreeService
from local_agent_runtime.store.sqlite_store import SQLiteStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_store(tmp_path: Any) -> SQLiteStore:
    db_path = tmp_path / "test.sqlite3"
    return SQLiteStore(str(db_path))


def _make_workspace(store: SQLiteStore, tmp_path: Any) -> str:
    ws = store.upsert_workspace(str(tmp_path / "project"))
    return ws["id"]


def _create_task(store: SQLiteStore, workspace_id: str) -> dict[str, Any]:
    session = store.create_session(workspace_id, "Worktree merge gate")
    return store.create_task(
        session_id=session["id"],
        task_type="edit",
        goal="merge isolated worktree",
        plan=[],
        status="completed",
    )


def _create_worktree(
    store: SQLiteStore,
    workspace_id: str,
    *,
    task_id: str = "task_test1",
    branch_name: str = "agent/task_test1",
    worktree_path: str = "/tmp/wt/task_test1",
    base_ref: str = "main",
    session_id: str | None = "sess_1",
    cleanup_policy: str = "ask_user",
    merge_policy: str = "approval_required",
) -> dict[str, Any]:
    params: dict[str, Any] = {
        "taskId": task_id,
        "workspaceId": workspace_id,
        "branchName": branch_name,
        "worktreePath": worktree_path,
        "baseRef": base_ref,
        "cleanupPolicy": cleanup_policy,
        "mergePolicy": merge_policy,
    }
    if session_id is not None:
        params["sessionId"] = session_id
    result = store.create_worktree(params)
    return result["worktree"]


def _approve_worktree_merge(store: SQLiteStore, wt: dict[str, Any], target_branch: str = "main") -> dict[str, Any]:
    approval = store.create_approval(
        wt["taskId"],
        "worktree_merge",
        {"worktreeId": wt["id"], "targetBranch": target_branch},
    )
    return store.resolve_approval(approval["id"], "approved")


# ---------------------------------------------------------------------------
# CRUD Tests
# ---------------------------------------------------------------------------

class TestWorktreeCRUD:
    """Worktree store CRUD operations."""

    def test_create_worktree(self, tmp_path: Any) -> None:
        store = _make_store(tmp_path)
        ws_id = _make_workspace(store, tmp_path)

        wt = _create_worktree(store, ws_id)
        assert wt["id"].startswith("wt_")
        assert wt["taskId"] == "task_test1"
        assert wt["status"] == "creating"
        assert wt["branchName"] == "agent/task_test1"
        assert wt["worktreePath"] == "/tmp/wt/task_test1"
        assert wt["baseRef"] == "main"
        assert wt["cleanupPolicy"] == "ask_user"
        assert wt["mergePolicy"] == "approval_required"
        assert wt["sessionId"] == "sess_1"

    def test_get_worktree_by_id(self, tmp_path: Any) -> None:
        store = _make_store(tmp_path)
        ws_id = _make_workspace(store, tmp_path)
        wt = _create_worktree(store, ws_id)

        result = store.get_worktree({"worktreeId": wt["id"]})
        assert result["worktree"]["id"] == wt["id"]
        assert result["worktree"]["taskId"] == "task_test1"

    def test_get_worktree_by_task(self, tmp_path: Any) -> None:
        store = _make_store(tmp_path)
        ws_id = _make_workspace(store, tmp_path)
        wt = _create_worktree(store, ws_id, task_id="task_special")

        result = store.get_worktree_by_task({"taskId": "task_special"})
        assert result["worktree"]["id"] == wt["id"]

        # Non-existent task returns None
        result2 = store.get_worktree_by_task({"taskId": "task_nonexistent"})
        assert result2["worktree"] is None

    def test_list_worktrees_by_workspace(self, tmp_path: Any) -> None:
        store = _make_store(tmp_path)
        ws1 = _make_workspace(store, tmp_path)
        ws2 = store.upsert_workspace(str(tmp_path / "other"))["id"]

        _create_worktree(store, ws1, task_id="task_a", branch_name="agent/a", worktree_path="/tmp/a")
        _create_worktree(store, ws1, task_id="task_b", branch_name="agent/b", worktree_path="/tmp/b")
        _create_worktree(store, ws2, task_id="task_c", branch_name="agent/c", worktree_path="/tmp/c")

        result = store.list_worktrees({"workspaceId": ws1})
        assert len(result["worktrees"]) == 2

    def test_list_worktrees_by_session(self, tmp_path: Any) -> None:
        store = _make_store(tmp_path)
        ws_id = _make_workspace(store, tmp_path)

        _create_worktree(store, ws_id, task_id="task_a", session_id="sess_x")
        _create_worktree(store, ws_id, task_id="task_b", session_id="sess_y")

        result = store.list_worktrees({"sessionId": "sess_x"})
        assert len(result["worktrees"]) == 1
        assert result["worktrees"][0]["taskId"] == "task_a"

    def test_list_worktrees_by_status(self, tmp_path: Any) -> None:
        store = _make_store(tmp_path)
        ws_id = _make_workspace(store, tmp_path)

        wt1 = _create_worktree(store, ws_id, task_id="task_a")
        store.update_worktree({"worktreeId": wt1["id"], "status": "active"})
        _create_worktree(store, ws_id, task_id="task_b", branch_name="agent/b", worktree_path="/tmp/b")

        result = store.list_worktrees({"status": "active"})
        assert len(result["worktrees"]) == 1
        assert result["worktrees"][0]["taskId"] == "task_a"

    def test_update_worktree_status(self, tmp_path: Any) -> None:
        store = _make_store(tmp_path)
        ws_id = _make_workspace(store, tmp_path)
        wt = _create_worktree(store, ws_id)

        updated = store.update_worktree({"worktreeId": wt["id"], "status": "active"})
        assert updated["worktree"]["status"] == "active"

        updated2 = store.update_worktree({"worktreeId": wt["id"], "status": "merged"})
        assert updated2["worktree"]["status"] == "merged"

    def test_update_worktree_fields(self, tmp_path: Any) -> None:
        store = _make_store(tmp_path)
        ws_id = _make_workspace(store, tmp_path)
        wt = _create_worktree(store, ws_id)

        updated = store.update_worktree({
            "worktreeId": wt["id"],
            "status": "active",
            "lastStatus": {"dirty": False, "ahead": 3},
            "cleanupPolicy": "keep",
        })
        assert updated["worktree"]["status"] == "active"
        assert updated["worktree"]["lastStatus"] == {"dirty": False, "ahead": 3}
        assert updated["worktree"]["cleanupPolicy"] == "keep"

    def test_delete_worktree(self, tmp_path: Any) -> None:
        store = _make_store(tmp_path)
        ws_id = _make_workspace(store, tmp_path)
        wt = _create_worktree(store, ws_id)

        result = store.delete_worktree({"worktreeId": wt["id"]})
        assert result["deleted"] is True

        result2 = store.list_worktrees({"workspaceId": ws_id})
        assert len(result2["worktrees"]) == 0

    def test_delete_nonexistent_worktree(self, tmp_path: Any) -> None:
        store = _make_store(tmp_path)
        result = store.delete_worktree({"worktreeId": "wt_nonexistent"})
        assert result["deleted"] is False


# ---------------------------------------------------------------------------
# Validation Tests
# ---------------------------------------------------------------------------

class TestWorktreeValidation:
    """Worktree field validation."""

    def test_invalid_cleanup_policy(self, tmp_path: Any) -> None:
        store = _make_store(tmp_path)
        ws_id = _make_workspace(store, tmp_path)

        with pytest.raises(ValueError, match="Invalid cleanup policy"):
            _create_worktree(store, ws_id, cleanup_policy="bad_policy")

    def test_invalid_merge_policy(self, tmp_path: Any) -> None:
        store = _make_store(tmp_path)
        ws_id = _make_workspace(store, tmp_path)

        with pytest.raises(ValueError, match="Invalid merge policy"):
            _create_worktree(store, ws_id, merge_policy="auto")

    def test_invalid_status_on_update(self, tmp_path: Any) -> None:
        store = _make_store(tmp_path)
        ws_id = _make_workspace(store, tmp_path)
        wt = _create_worktree(store, ws_id)

        with pytest.raises(ValueError, match="Invalid worktree status"):
            store.update_worktree({"worktreeId": wt["id"], "status": "unknown"})

    def test_missing_required_task_id(self, tmp_path: Any) -> None:
        store = _make_store(tmp_path)
        ws_id = _make_workspace(store, tmp_path)

        with pytest.raises(ValueError):
            store.create_worktree({
                "workspaceId": ws_id,
                "branchName": "b",
                "worktreePath": "/p",
            })

    def test_get_nonexistent_worktree(self, tmp_path: Any) -> None:
        store = _make_store(tmp_path)

        with pytest.raises(ValueError, match="Worktree not found"):
            store.get_worktree({"worktreeId": "wt_nonexistent"})


# ---------------------------------------------------------------------------
# RPC Registration Tests
# ---------------------------------------------------------------------------

class TestWorktreeRPC:
    """Verify worktree.* RPCs are registered."""

    def test_worktree_rpcs_registered(self, tmp_path: Any) -> None:
        from local_agent_runtime.main import build_server

        server = build_server(database_path=str(tmp_path / "test.sqlite3"))
        handlers = server._handlers

        expected = [
            "worktree.create",
            "worktree.get",
            "worktree.getByTask",
            "worktree.list",
            "worktree.status",
            "worktree.diff",
            "worktree.requestMergeApproval",
            "worktree.merge",
            "worktree.cleanup",
            "worktree.update",
            "worktree.delete",
        ]
        for rpc_name in expected:
            assert rpc_name in handlers, f"Missing RPC: {rpc_name}"


# ---------------------------------------------------------------------------
# All Valid Statuses
# ---------------------------------------------------------------------------

class TestWorktreeStatusTransitions:
    """Verify all valid statuses are accepted."""

    @pytest.mark.parametrize("status", [
        "creating", "active", "paused", "ready_for_review",
        "merged", "cancelled", "cleanup_pending", "cleaned", "failed",
    ])
    def test_all_valid_statuses(self, tmp_path: Any, status: str) -> None:
        store = _make_store(tmp_path)
        ws_id = _make_workspace(store, tmp_path)
        wt = _create_worktree(store, ws_id)

        updated = store.update_worktree({"worktreeId": wt["id"], "status": status})
        assert updated["worktree"]["status"] == status


class FakeGitWorktreeAdapter(GitWorktreeAdapter):
    def __init__(self) -> None:
        self.status_result: dict[str, Any] = {"dirtyFiles": 0, "files": []}
        self.diff_result: dict[str, Any] = {"diffStat": "file.py | 1 +"}
        self.merge_result: dict[str, Any] = {"result": "ok", "returnCode": 0}
        self.merged: list[tuple[str, str]] = []

    def status(self, target_path: str) -> dict[str, Any]:
        return self.status_result

    def diff(self, target_path: str, base_ref: str = "HEAD") -> dict[str, Any]:
        return self.diff_result

    def merge(self, branch_name: str, target_branch: str = "main") -> dict[str, Any]:
        self.merged.append((branch_name, target_branch))
        return self.merge_result


class FakeApprovalOrchestrator(ApprovalFlowMixin):
    def __init__(self, store: SQLiteStore, worktree_service: WorktreeService) -> None:
        self._store = store
        self._worktree_service = worktree_service
        self.published: list[dict[str, Any]] = []
        self.hooks: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def _publish(self, **kwargs: Any) -> None:
        self.published.append(kwargs)

    def _fire_hooks(self, *args: Any, **kwargs: Any) -> None:
        self.hooks.append((args, kwargs))


class TestWorktreeServiceMergeGate:
    def test_merge_requires_explicit_approval(self, tmp_path: Any) -> None:
        store = _make_store(tmp_path)
        ws_id = _make_workspace(store, tmp_path)
        wt = _create_worktree(store, ws_id)
        git = FakeGitWorktreeAdapter()
        service = WorktreeService(store, git)

        with pytest.raises(ValueError, match="approved approval record"):
            service.merge({"worktreeId": wt["id"]})

        assert git.merged == []

    def test_request_merge_approval_creates_formal_approval(self, tmp_path: Any) -> None:
        store = _make_store(tmp_path)
        ws_id = _make_workspace(store, tmp_path)
        task = _create_task(store, ws_id)
        wt = _create_worktree(store, ws_id, task_id=task["id"], session_id=task["sessionId"])
        git = FakeGitWorktreeAdapter()
        service = WorktreeService(store, git)

        result = service.request_merge_approval({"worktreeId": wt["id"]})

        approval = result["approval"]
        request = approval["requestJson"]
        assert approval["kind"] == "worktree_merge"
        assert approval["decision"] is None
        assert wt["id"] in request
        assert result["diff"]["diffStat"] == "file.py | 1 +"

    def test_merge_rejects_dirty_worktree(self, tmp_path: Any) -> None:
        store = _make_store(tmp_path)
        ws_id = _make_workspace(store, tmp_path)
        task = _create_task(store, ws_id)
        wt = _create_worktree(store, ws_id, task_id=task["id"], session_id=task["sessionId"])
        approval = _approve_worktree_merge(store, wt)
        git = FakeGitWorktreeAdapter()
        git.status_result = {"dirtyFiles": 1, "files": ["M file.py"]}
        service = WorktreeService(store, git)

        with pytest.raises(ValueError, match="uncommitted changes"):
            service.merge({"worktreeId": wt["id"], "approvalId": approval["id"]})

        assert git.merged == []

    def test_merge_conflict_does_not_mark_merged(self, tmp_path: Any) -> None:
        store = _make_store(tmp_path)
        ws_id = _make_workspace(store, tmp_path)
        task = _create_task(store, ws_id)
        wt = _create_worktree(store, ws_id, task_id=task["id"], session_id=task["sessionId"])
        approval = _approve_worktree_merge(store, wt)
        git = FakeGitWorktreeAdapter()
        git.merge_result = {"result": "conflict", "returnCode": 1, "stdout": "conflict"}
        service = WorktreeService(store, git)

        result = service.merge({"worktreeId": wt["id"], "approvalId": approval["id"]})

        assert result["merged"] is False
        assert store.get_worktree({"worktreeId": wt["id"]})["worktree"]["status"] == "failed"

    def test_submit_worktree_merge_approval_runs_merge(self, tmp_path: Any) -> None:
        store = _make_store(tmp_path)
        ws_id = _make_workspace(store, tmp_path)
        task = _create_task(store, ws_id)
        wt = _create_worktree(
            store,
            ws_id,
            task_id=task["id"],
            session_id=task["sessionId"],
            branch_name=f"agent/{task['id']}",
        )
        git = FakeGitWorktreeAdapter()
        service = WorktreeService(store, git)
        orchestrator = FakeApprovalOrchestrator(store, service)
        approval = service.request_merge_approval({"worktreeId": wt["id"]})["approval"]

        result = orchestrator.submit_approval({"approvalId": approval["id"], "decision": "approved"})

        assert result["worktreeMerge"]["merged"] is True
        assert git.merged == [(f"agent/{task['id']}", "main")]
        assert store.get_worktree({"worktreeId": wt["id"]})["worktree"]["status"] == "merged"
        assert [event["event_type"] for event in orchestrator.published] == [
            "approval.resolved",
            "task.worktree.merged",
        ]
        merge_payload = orchestrator.published[-1]["payload"]
        assert merge_payload["worktreeId"] == wt["id"]
        assert merge_payload["routing"]["activeWorktree"]["id"] == wt["id"]

    def test_submit_worktree_merge_approval_reports_failed_merge(self, tmp_path: Any) -> None:
        store = _make_store(tmp_path)
        ws_id = _make_workspace(store, tmp_path)
        task = _create_task(store, ws_id)
        wt = _create_worktree(store, ws_id, task_id=task["id"], session_id=task["sessionId"])
        git = FakeGitWorktreeAdapter()
        service = WorktreeService(store, git)
        orchestrator = FakeApprovalOrchestrator(store, service)
        approval = service.request_merge_approval({"worktreeId": wt["id"]})["approval"]
        git.status_result = {"dirtyFiles": 1, "files": ["M file.py"]}

        result = orchestrator.submit_approval({"approvalId": approval["id"], "decision": "approved"})

        assert result["worktreeMerge"]["merged"] is False
        assert "uncommitted changes" in result["worktreeMerge"]["error"]
        assert git.merged == []
        assert orchestrator.published[-1]["event_type"] == "task.worktree.merge_failed"
