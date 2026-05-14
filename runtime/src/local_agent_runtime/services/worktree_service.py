"""Worktree Service — orchestrates worktree lifecycle via store + git adapter.

Higher-level operations that coordinate the SQLite worktree records with actual
git worktree operations. Supports hook firing for worktree lifecycle events.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from ..git.worktree_adapter import GitWorktreeAdapter
from ..store.sqlite_store import SQLiteStore

logger = logging.getLogger(__name__)


class WorktreeService:
    """Orchestrates worktree create / merge / status / diff / cleanup."""

    def __init__(
        self,
        store: SQLiteStore,
        git_adapter: GitWorktreeAdapter,
        hook_service: Any | None = None,
    ) -> None:
        self._store = store
        self._git = git_adapter
        self._hook_service = hook_service

    def _fire_hooks(self, event: str, context: dict[str, Any]) -> list[dict[str, Any]]:
        """Fire hooks for a worktree lifecycle event. No-op if no hook service."""
        if self._hook_service is None:
            return []
        try:
            return self._hook_service.invoke_hooks(event, context)
        except Exception:
            logger.warning("Worktree hook execution failed for %s", event, exc_info=True)
            return []

    # -- High-level operations --------------------------------------------------

    def request_merge_approval(self, params: dict[str, Any]) -> dict[str, Any]:
        """Create a formal approval record for a worktree merge request."""
        record = self._store.get_worktree(params)
        wt = record["worktree"]
        target_branch = params.get("targetBranch", "main")
        if wt.get("mergePolicy") == "manual_only":
            raise ValueError("Worktree merge policy is manual_only")

        status = self._git.status(wt["worktreePath"])
        if status.get("dirtyFiles", 0):
            raise ValueError("Worktree has uncommitted changes; review and commit or clean before merge")
        diff = self._git.diff(wt["worktreePath"], wt.get("baseRef", "HEAD"))
        request = {
            "worktreeId": wt["id"],
            "taskId": wt.get("taskId", ""),
            "branchName": wt.get("branchName", ""),
            "targetBranch": target_branch,
            "baseRef": wt.get("baseRef", "HEAD"),
            "worktreePath": wt.get("worktreePath", ""),
            "diffStat": diff.get("diffStat") or "",
            "dirtyFiles": status.get("dirtyFiles", 0),
            "files": status.get("files") or diff.get("files") or [],
            "risk": "write merge worktree changes into target branch",
        }

        existing = self._store.find_approval(
            task_id=wt["taskId"],
            kind="worktree_merge",
            request=request,
        )
        if existing is not None and existing.get("decision") is None:
            approval = existing
        else:
            approval = self._store.create_approval(wt["taskId"], "worktree_merge", request)

        return {
            "approval": approval,
            "worktree": wt,
            "gitStatus": status,
            "diff": diff,
        }

    def create_for_task(self, params: dict[str, Any]) -> dict[str, Any]:
        """Create a worktree record AND a git worktree for a task.

        Steps:
        1. Validate no existing worktree for this task.
        2. Fire before_worktree_create hooks.
        3. Create git worktree via adapter.
        4. Create store record.
        5. Fire after_worktree_create hooks.
        """
        task_id = params.get("taskId", "")
        workspace_id = params.get("workspaceId", "")
        session_id = params.get("sessionId", "")

        # Check for existing allocation
        existing = self._store.get_worktree_by_task({"taskId": task_id})
        if existing.get("worktree") is not None:
            raise ValueError(f"Task {task_id} already has a worktree")

        hook_context = {
            "workspaceId": workspace_id,
            "sessionId": session_id,
            "taskId": task_id,
            "branchName": params.get("branchName", ""),
            "worktreePath": params.get("worktreePath", ""),
            "baseRef": params.get("baseRef", "HEAD"),
        }

        # Fire before hooks
        self._fire_hooks("before_worktree_create", hook_context)

        # Create git worktree
        Path(params["worktreePath"]).parent.mkdir(parents=True, exist_ok=True)
        git_result = self._git.create(
            branch_name=params["branchName"],
            target_path=params["worktreePath"],
            base_ref=params.get("baseRef", "HEAD"),
        )

        # Create store record
        record = self._store.create_worktree(params)
        worktree_id = record.get("worktree", {}).get("id")
        if worktree_id:
            record = self._store.update_worktree({
                "worktreeId": worktree_id,
                "status": "active",
            })

        # Fire after hooks
        hook_context["worktreeId"] = record.get("worktree", {}).get("id", "")
        hook_context["gitBranch"] = git_result.get("branch", "")
        self._fire_hooks("after_worktree_create", hook_context)

        return {**record, "git": git_result}

    def merge(self, params: dict[str, Any]) -> dict[str, Any]:
        """Merge a worktree branch back into the target branch.

        Steps:
        1. Get worktree record.
        2. Fire before_worktree_merge hooks.
        3. Merge via git adapter.
        4. Update store record status to ``merged``.
        5. Fire after_worktree_merge hooks.
        """
        record = self._store.get_worktree(params)
        wt = record["worktree"]
        target_branch = params.get("targetBranch", "main")
        branch_name = wt.get("branchName", "")
        if wt.get("mergePolicy") == "manual_only":
            raise ValueError("Worktree merge policy is manual_only")
        if wt.get("mergePolicy") == "approval_required":
            self._require_approved_merge_record(wt, params, target_branch)
        status = self._git.status(wt["worktreePath"])
        if status.get("dirtyFiles", 0):
            raise ValueError("Worktree has uncommitted changes; review and commit or clean before merge")
        diff = self._git.diff(wt["worktreePath"], wt.get("baseRef", "HEAD"))

        hook_context = {
            "workspaceId": wt.get("workspaceId", ""),
            "taskId": wt.get("taskId", ""),
            "worktreeId": wt["id"],
            "branchName": branch_name,
            "targetBranch": target_branch,
            "diff": diff,
        }

        # Fire before hooks
        self._fire_hooks("before_worktree_merge", hook_context)

        # Merge via git adapter
        merge_result = self._git.merge(
            branch_name=branch_name,
            target_branch=target_branch,
        )
        if merge_result.get("result") != "ok":
            self._store.update_worktree({
                "worktreeId": wt["id"],
                "status": "failed",
                "lastStatus": {
                    "mergeResult": merge_result,
                    "dirtyFiles": status.get("dirtyFiles", 0),
                },
            })
            hook_context["mergeResult"] = merge_result.get("result", "")
            self._fire_hooks("after_worktree_merge", hook_context)
            return {
                "worktreeId": wt["id"],
                "merged": False,
                "branchName": branch_name,
                "targetBranch": target_branch,
                "result": merge_result,
            }

        # Update store record
        self._store.update_worktree({
            "worktreeId": wt["id"],
            "status": "merged",
            "lastStatus": {
                "mergeResult": merge_result,
                "dirtyFiles": status.get("dirtyFiles", 0),
            },
        })

        # Fire after hooks
        hook_context["mergeResult"] = merge_result.get("result", "")
        self._fire_hooks("after_worktree_merge", hook_context)

        return {
            "worktreeId": wt["id"],
            "merged": True,
            "branchName": branch_name,
            "targetBranch": target_branch,
            "result": merge_result,
        }

    def _require_approved_merge_record(
        self,
        wt: dict[str, Any],
        params: dict[str, Any],
        target_branch: str,
    ) -> dict[str, Any]:
        approval_id = params.get("approvalId")
        if not isinstance(approval_id, str) or not approval_id.strip():
            raise ValueError("Worktree merge requires approved approval record")
        approval = self._store.get_approval({"approvalId": approval_id})["approval"]
        if approval.get("kind") != "worktree_merge":
            raise ValueError("Approval record is not for worktree merge")
        if approval.get("decision") != "approved":
            raise ValueError("Worktree merge approval has not been approved")
        if approval.get("taskId") != wt.get("taskId"):
            raise ValueError("Worktree merge approval does not belong to this task")
        try:
            request = json.loads(approval.get("requestJson") or "{}")
        except json.JSONDecodeError as exc:
            raise ValueError("Worktree merge approval request is invalid") from exc
        if request.get("worktreeId") != wt.get("id"):
            raise ValueError("Worktree merge approval does not match this worktree")
        approved_target = request.get("targetBranch") or "main"
        if approved_target != target_branch:
            raise ValueError("Worktree merge approval target branch does not match")
        return approval

    def get_status(self, params: dict[str, Any]) -> dict[str, Any]:
        """Get worktree record + live git status."""
        record = self._store.get_worktree(params)
        wt = record["worktree"]
        try:
            git_status = self._git.status(wt["worktreePath"])
        except Exception:
            git_status = {"error": "worktree path not accessible"}
        return {"worktree": wt, "gitStatus": git_status}

    def get_diff(self, params: dict[str, Any]) -> dict[str, Any]:
        """Get diff between worktree and its base ref."""
        record = self._store.get_worktree(params)
        wt = record["worktree"]
        git_diff = self._git.diff(wt["worktreePath"], wt["baseRef"])
        return {"worktree": wt, "diff": git_diff}

    def cleanup(self, params: dict[str, Any]) -> dict[str, Any]:
        """Remove git worktree and mark store record as cleaned.

        Steps:
        1. Check if worktree is clean (no uncommitted changes) or force=True.
        2. Remove git worktree.
        3. Update store record status to ``cleaned``.
        """
        record = self._store.get_worktree(params)
        wt = record["worktree"]
        force = params.get("force", False)

        if not force and not self._git.has_clean_branch(wt["worktreePath"]):
            raise ValueError("Worktree has uncommitted changes; use force=True to override")

        self._git.remove(wt["worktreePath"], force=force)

        self._store.update_worktree({
            "worktreeId": wt["id"],
            "status": "cleaned",
        })
        return {"cleaned": True, "worktreeId": wt["id"]}
