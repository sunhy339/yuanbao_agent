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
import json
import subprocess

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
        self.diff_full_result: dict[str, Any] = {
            "diff": "diff --git a/file.py b/file.py\n+print('ok')\n",
            "returnCode": 0,
            "stderr": "",
        }
        self.merge_result: dict[str, Any] = {"result": "ok", "returnCode": 0}
        self.merged: list[tuple[str, str]] = []
        self.created: list[dict[str, Any]] = []
        self.resolved_base_ref = "abc123"

    def create(self, branch_name: str, target_path: str, base_ref: str = "HEAD") -> dict[str, Any]:
        self.created.append({"branchName": branch_name, "targetPath": target_path, "baseRef": base_ref})
        return {
            "branch": branch_name,
            "path": target_path,
            "baseRef": self.resolved_base_ref,
            "requestedBaseRef": base_ref,
        }

    def status(self, target_path: str) -> dict[str, Any]:
        return self.status_result

    def diff(self, target_path: str, base_ref: str = "HEAD") -> dict[str, Any]:
        return self.diff_result

    def diff_full(self, target_path: str, base_ref: str = "HEAD") -> dict[str, Any]:
        return self.diff_full_result

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


def _run_git(cwd: Any, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


class TestWorktreeServiceMergeGate:
    def test_create_for_task_persists_resolved_base_ref(self, tmp_path: Any) -> None:
        store = _make_store(tmp_path)
        ws_id = _make_workspace(store, tmp_path)
        task = _create_task(store, ws_id)
        git = FakeGitWorktreeAdapter()
        service = WorktreeService(store, git)

        result = service.create_for_task({
            "workspaceId": ws_id,
            "sessionId": task["sessionId"],
            "taskId": task["id"],
            "baseRef": "HEAD",
            "branchName": f"agent/{task['id']}",
            "worktreePath": str(tmp_path / "worktree"),
            "cleanupPolicy": "ask_user",
            "mergePolicy": "approval_required",
        })

        wt = result["worktree"]
        assert git.created[0]["baseRef"] == "HEAD"
        assert wt["baseRef"] == "abc123"
        assert wt["lastStatus"]["requestedBaseRef"] == "HEAD"
        assert wt["lastStatus"]["resolvedBaseRef"] == "abc123"

    def test_create_for_task_uses_workspace_root_without_global_adapter(self, tmp_path: Any) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        _run_git(repo, "init")
        _run_git(repo, "checkout", "-b", "main")
        _run_git(repo, "config", "user.email", "test@example.com")
        _run_git(repo, "config", "user.name", "Test User")
        (repo / "README.md").write_text("# Project\n", encoding="utf-8")
        _run_git(repo, "add", "README.md")
        _run_git(repo, "commit", "-m", "init")

        store = _make_store(tmp_path)
        workspace = store.upsert_workspace(str(repo))
        session = store.create_session(workspace["id"], "workspace-root worktree")
        task = store.create_task(
            session_id=session["id"],
            task_type="edit",
            goal="edit in child worktree",
            plan=[],
            status="running",
        )
        worktree_path = tmp_path / "worktrees" / task["id"]
        service = WorktreeService(store)

        result = service.create_for_task({
            "workspaceId": workspace["id"],
            "sessionId": session["id"],
            "taskId": task["id"],
            "baseRef": "HEAD",
            "branchName": f"agent/{task['id']}",
            "worktreePath": str(worktree_path),
            "cleanupPolicy": "ask_user",
            "mergePolicy": "approval_required",
        })

        wt = result["worktree"]
        assert wt["status"] == "active"
        assert wt["worktreePath"] == str(worktree_path)
        assert (worktree_path / "README.md").read_text(encoding="utf-8") == "# Project\n"
        assert _run_git(worktree_path, "rev-parse", "--abbrev-ref", "HEAD") == f"agent/{task['id']}"

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
        request_json = json.loads(request)
        assert approval["kind"] == "worktree_merge"
        assert approval["decision"] is None
        assert wt["id"] in request
        assert result["diff"]["diffStat"] == "file.py | 1 +"
        assert request_json["diffSummary"]["diffStat"] == "file.py | 1 +"
        assert request_json["diffSummary"]["preview"].startswith("diff --git")
        assert request_json["review"]["status"] == "pending"
        assert request_json["multiAgentWorktreeStrategy"]["strategy"] == "root_worktree"
        stored = store.get_worktree({"worktreeId": wt["id"]})["worktree"]
        assert stored["lastStatus"]["mergeDiff"]["bytes"] == request_json["diffBytes"]

    def test_real_git_merge_approval_diff_uses_stable_base_after_worktree_commit(self, tmp_path: Any) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        _run_git(repo, "init")
        _run_git(repo, "checkout", "-b", "main")
        _run_git(repo, "config", "user.email", "test@example.com")
        _run_git(repo, "config", "user.name", "Test User")
        (repo / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
        _run_git(repo, "add", "calc.py")
        _run_git(repo, "commit", "-m", "init")

        store = _make_store(tmp_path)
        workspace = store.upsert_workspace(str(repo))
        session = store.create_session(workspace["id"], "real git worktree")
        task = store.create_task(
            session_id=session["id"],
            task_type="edit",
            goal="fix add",
            plan=[],
            status="completed",
        )
        worktree_path = tmp_path / "worktree"
        service = WorktreeService(store, GitWorktreeAdapter(repo))
        created = service.create_for_task({
            "workspaceId": workspace["id"],
            "sessionId": session["id"],
            "taskId": task["id"],
            "baseRef": "HEAD",
            "branchName": f"agent/{task['id']}",
            "worktreePath": str(worktree_path),
            "cleanupPolicy": "ask_user",
            "mergePolicy": "approval_required",
        })
        wt = created["worktree"]
        assert wt["baseRef"] != "HEAD"

        (worktree_path / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
        _run_git(worktree_path, "add", "calc.py")
        _run_git(worktree_path, "commit", "-m", "fix add")

        result = service.request_merge_approval({
            "worktreeId": wt["id"],
            "targetBranch": "main",
            "reviewStatus": "approved",
            "reviewerSummary": "Looks good.",
            "verificationCommands": ["python -c \"print('merge ok')\""],
        })
        request = json.loads(result["approval"]["requestJson"])

        assert "calc.py" in request["diffStat"]
        assert "return a + b" in request["diffPreview"]
        assert request["verificationStatus"] == "passed"
        assert result["verification"][0]["status"] == "passed"

    def test_real_git_child_worktrees_report_isolated_strategy_and_conflict_context(self, tmp_path: Any) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        _run_git(repo, "init")
        _run_git(repo, "checkout", "-b", "main")
        _run_git(repo, "config", "user.email", "test@example.com")
        _run_git(repo, "config", "user.name", "Test User")
        (repo / "feature.txt").write_text("base\n", encoding="utf-8")
        _run_git(repo, "add", "feature.txt")
        _run_git(repo, "commit", "-m", "init")

        store = _make_store(tmp_path)
        workspace = store.upsert_workspace(str(repo))
        session = store.create_session(workspace["id"], "multi child worktrees")
        root_task = store.create_task(
            session_id=session["id"],
            task_type="edit",
            goal="coordinate child worktrees",
            plan=[],
            status="completed",
        )
        child_a = store.create_task(
            session_id=session["id"],
            task_type="edit",
            goal="child a",
            plan=[],
            status="completed",
            root_task_id=root_task["id"],
            role="worker",
            routing={
                "rootTaskId": root_task["id"],
                "parentTaskId": root_task["id"],
                "childCollaborationTaskId": "ctask_a",
            },
        )
        child_b = store.create_task(
            session_id=session["id"],
            task_type="edit",
            goal="child b",
            plan=[],
            status="completed",
            root_task_id=root_task["id"],
            role="worker",
            routing={
                "rootTaskId": root_task["id"],
                "parentTaskId": root_task["id"],
                "childCollaborationTaskId": "ctask_b",
            },
        )
        service = WorktreeService(store, GitWorktreeAdapter(repo))

        child_a_path = tmp_path / "child-a"
        child_b_path = tmp_path / "child-b"
        child_a_wt = service.create_for_task({
            "workspaceId": workspace["id"],
            "sessionId": session["id"],
            "taskId": child_a["id"],
            "baseRef": "HEAD",
            "branchName": f"agent/{child_a['id']}",
            "worktreePath": str(child_a_path),
            "cleanupPolicy": "ask_user",
            "mergePolicy": "approval_required",
        })["worktree"]
        child_b_wt = service.create_for_task({
            "workspaceId": workspace["id"],
            "sessionId": session["id"],
            "taskId": child_b["id"],
            "baseRef": "HEAD",
            "branchName": f"agent/{child_b['id']}",
            "worktreePath": str(child_b_path),
            "cleanupPolicy": "ask_user",
            "mergePolicy": "approval_required",
        })["worktree"]

        (child_a_path / "feature.txt").write_text("child-a\n", encoding="utf-8")
        _run_git(child_a_path, "add", "feature.txt")
        _run_git(child_a_path, "commit", "-m", "child a update")
        (child_b_path / "feature.txt").write_text("child-b\n", encoding="utf-8")
        _run_git(child_b_path, "add", "feature.txt")
        _run_git(child_b_path, "commit", "-m", "child b update")

        approval_a = service.request_merge_approval({
            "worktreeId": child_a_wt["id"],
            "targetBranch": "main",
            "reviewStatus": "approved",
            "reviewerSummary": "Child A scope approved.",
            "verificationCommands": ["python -c \"print('child a ready')\""],
        })["approval"]
        request_a = json.loads(approval_a["requestJson"])
        assert request_a["multiAgentWorktreeStrategy"]["strategy"] == "isolated_child_worktrees"
        assert request_a["multiAgentWorktreeStrategy"]["isolation"] == "isolated"
        assert request_a["multiAgentWorktreeStrategy"]["parentTaskId"] == root_task["id"]
        assert request_a["multiAgentWorktreeStrategy"]["childCollaborationTaskId"] == "ctask_a"
        store.resolve_approval(approval_a["id"], "approved")
        merge_a = service.merge({
            "worktreeId": child_a_wt["id"],
            "targetBranch": "main",
            "approvalId": approval_a["id"],
        })
        assert merge_a["merged"] is True

        approval_b = service.request_merge_approval({
            "worktreeId": child_b_wt["id"],
            "targetBranch": "main",
            "reviewStatus": "approved",
            "reviewerSummary": "Child B scope approved.",
        })["approval"]
        store.resolve_approval(approval_b["id"], "approved")
        merge_b = service.merge({
            "worktreeId": child_b_wt["id"],
            "targetBranch": "main",
            "approvalId": approval_b["id"],
        })

        assert merge_b["merged"] is False
        assert merge_b["result"]["result"] == "conflict"
        assert merge_b["multiAgentWorktreeStrategy"]["strategy"] == "isolated_child_worktrees"
        assert merge_b["multiAgentWorktreeStrategy"]["conflictHandling"].startswith("merge conflicts")
        stored_b = store.get_worktree({"worktreeId": child_b_wt["id"]})["worktree"]
        assert stored_b["status"] == "failed"
        assert stored_b["lastStatus"]["mergeResult"]["result"] == "conflict"
        assert stored_b["lastStatus"]["multiAgentWorktreeStrategy"]["childCollaborationTaskId"] == "ctask_b"
        assert stored_b["lastStatus"]["mergeApproval"]["decision"] == "approved"

    def test_request_merge_approval_truncates_large_diff_preview(self, tmp_path: Any) -> None:
        store = _make_store(tmp_path)
        ws_id = _make_workspace(store, tmp_path)
        task = _create_task(store, ws_id)
        wt = _create_worktree(store, ws_id, task_id=task["id"], session_id=task["sessionId"])
        git = FakeGitWorktreeAdapter()
        git.diff_full_result = {"diff": "x" * 2000, "returnCode": 0, "stderr": ""}
        service = WorktreeService(store, git)

        result = service.request_merge_approval({"worktreeId": wt["id"], "diffPreviewBytes": 1000})

        request = json.loads(result["approval"]["requestJson"])
        assert request["diffSummary"]["bytes"] == 2000
        assert request["diffSummary"]["previewBytes"] == 1000
        assert request["diffSummary"]["truncated"] is True
        assert len(request["diffPreview"]) == 1000

    def test_get_diff_can_return_full_diff(self, tmp_path: Any) -> None:
        store = _make_store(tmp_path)
        ws_id = _make_workspace(store, tmp_path)
        wt = _create_worktree(store, ws_id)
        git = FakeGitWorktreeAdapter()
        git.diff_full_result = {"diff": "full diff body", "returnCode": 0, "stderr": ""}
        service = WorktreeService(store, git)

        result = service.get_diff({"worktreeId": wt["id"], "full": True})

        assert result["diff"]["mode"] == "full"
        assert result["diff"]["diff"] == "full diff body"
        assert result["diff"]["truncated"] is False
        assert result["diff"]["bytes"] == len("full diff body")

    def test_request_merge_approval_blocks_negative_review(self, tmp_path: Any) -> None:
        store = _make_store(tmp_path)
        ws_id = _make_workspace(store, tmp_path)
        task = _create_task(store, ws_id)
        wt = _create_worktree(store, ws_id, task_id=task["id"], session_id=task["sessionId"])
        git = FakeGitWorktreeAdapter()
        service = WorktreeService(store, git)

        with pytest.raises(ValueError, match="Cannot merge when reviewStatus"):
            service.request_merge_approval({
                "worktreeId": wt["id"],
                "reviewStatus": "changes_requested",
                "reviewerSummary": "Needs another pass.",
            })

        assert store.find_latest_approval(task_id=task["id"]) is None

    def test_request_merge_approval_runs_verification_commands(self, tmp_path: Any) -> None:
        store = _make_store(tmp_path)
        ws_id = _make_workspace(store, tmp_path)
        task = _create_task(store, ws_id)
        worktree_path = tmp_path / "worktree"
        worktree_path.mkdir()
        wt = _create_worktree(
            store,
            ws_id,
            task_id=task["id"],
            session_id=task["sessionId"],
            worktree_path=str(worktree_path),
        )
        git = FakeGitWorktreeAdapter()
        service = WorktreeService(store, git)

        result = service.request_merge_approval({
            "worktreeId": wt["id"],
            "verificationCommands": ["python -c \"print('merge ok')\""],
        })

        verification = result["verification"]
        assert verification[0]["status"] == "passed"
        assert verification[0]["command"].startswith("python -c")
        assert verification[0]["cwd"] == str(worktree_path)
        assert verification[0]["id"].startswith("cmd_")
        request = json.loads(result["approval"]["requestJson"])
        assert request["verificationStatus"] == "passed"
        assert request["verification"][0]["status"] == "passed"
        stored = store.get_worktree({"worktreeId": wt["id"]})["worktree"]
        assert stored["lastStatus"]["mergeVerification"][0]["status"] == "passed"
        command_logs = store.list_command_logs({"taskId": task["id"]})["commandLogs"]
        assert command_logs[0]["command"].startswith("python -c")
        assert command_logs[0]["status"] == "completed"

    def test_request_merge_approval_reuses_pending_approval_after_rerun_verification(self, tmp_path: Any) -> None:
        store = _make_store(tmp_path)
        ws_id = _make_workspace(store, tmp_path)
        task = _create_task(store, ws_id)
        worktree_path = tmp_path / "worktree"
        worktree_path.mkdir()
        wt = _create_worktree(
            store,
            ws_id,
            task_id=task["id"],
            session_id=task["sessionId"],
            worktree_path=str(worktree_path),
        )
        git = FakeGitWorktreeAdapter()
        service = WorktreeService(store, git)
        params = {
            "worktreeId": wt["id"],
            "verificationCommands": ["python -c \"print('merge ok')\""],
        }

        first = service.request_merge_approval(params)
        git.diff_full_result = {"diff": "updated approval diff", "returnCode": 0, "stderr": ""}
        second = service.request_merge_approval(params)

        assert second["approval"]["id"] == first["approval"]["id"]
        assert len(store.list_command_logs({"taskId": task["id"]})["commandLogs"]) == 2
        request = json.loads(second["approval"]["requestJson"])
        assert request["diffPreview"] == "updated approval diff"

    def test_request_merge_approval_applies_command_policy(self, tmp_path: Any) -> None:
        store = _make_store(tmp_path)
        store.update_config({
            "tools": {
                "runCommand": {
                    "blockedPatterns": ["python -c*"],
                },
            },
        })
        ws_id = _make_workspace(store, tmp_path)
        task = _create_task(store, ws_id)
        worktree_path = tmp_path / "worktree"
        worktree_path.mkdir()
        wt = _create_worktree(
            store,
            ws_id,
            task_id=task["id"],
            session_id=task["sessionId"],
            worktree_path=str(worktree_path),
        )
        git = FakeGitWorktreeAdapter()
        service = WorktreeService(store, git)

        with pytest.raises(ValueError, match="Blocked dangerous command pattern"):
            service.request_merge_approval({
                "worktreeId": wt["id"],
                "verificationCommands": ["python -c \"print('merge ok')\""],
            })

        assert store.list_command_logs({"taskId": task["id"]})["commandLogs"] == []

    def test_request_merge_approval_blocks_failed_verification(self, tmp_path: Any) -> None:
        store = _make_store(tmp_path)
        ws_id = _make_workspace(store, tmp_path)
        task = _create_task(store, ws_id)
        worktree_path = tmp_path / "worktree"
        worktree_path.mkdir()
        wt = _create_worktree(
            store,
            ws_id,
            task_id=task["id"],
            session_id=task["sessionId"],
            worktree_path=str(worktree_path),
        )
        git = FakeGitWorktreeAdapter()
        service = WorktreeService(store, git)

        with pytest.raises(ValueError, match="verification failed"):
            service.request_merge_approval({
                "worktreeId": wt["id"],
                "verificationCommands": ["python -c \"import sys; print('bad'); sys.exit(3)\""],
            })

        assert store.find_latest_approval(task_id=task["id"]) is None
        stored = store.get_worktree({"worktreeId": wt["id"]})["worktree"]
        assert stored["lastStatus"]["mergeVerification"][0]["status"] == "failed"

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
        assert merge_payload["approvalSummary"]["approvalId"] == approval["id"]
        assert merge_payload["review"]["status"] == "pending"
        assert merge_payload["diffSummary"]["diffStat"] == "file.py | 1 +"
        assert merge_payload["multiAgentWorktreeStrategy"]["strategy"] == "root_worktree"

    def test_submit_worktree_merge_approval_publishes_verification(self, tmp_path: Any) -> None:
        store = _make_store(tmp_path)
        ws_id = _make_workspace(store, tmp_path)
        task = _create_task(store, ws_id)
        worktree_path = tmp_path / "worktree"
        worktree_path.mkdir()
        wt = _create_worktree(
            store,
            ws_id,
            task_id=task["id"],
            session_id=task["sessionId"],
            worktree_path=str(worktree_path),
        )
        git = FakeGitWorktreeAdapter()
        service = WorktreeService(store, git)
        orchestrator = FakeApprovalOrchestrator(store, service)
        approval = service.request_merge_approval({
            "worktreeId": wt["id"],
            "verificationCommands": ["python -c \"print('ready')\""],
        })["approval"]

        result = orchestrator.submit_approval({"approvalId": approval["id"], "decision": "approved"})

        assert result["worktreeMerge"]["verification"][0]["status"] == "passed"
        merge_payload = orchestrator.published[-1]["payload"]
        assert merge_payload["verification"][0]["status"] == "passed"

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
