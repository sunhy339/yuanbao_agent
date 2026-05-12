"""Worktree Service — orchestrates worktree lifecycle via store + git adapter.

Higher-level operations that coordinate the SQLite worktree records with actual
git worktree operations. Does not auto-route tasks.
"""
from __future__ import annotations

import logging
from typing import Any

from ..git.worktree_adapter import GitWorktreeAdapter
from ..store.sqlite_store import SQLiteStore

logger = logging.getLogger(__name__)


class WorktreeService:
    """Orchestrates worktree create / status / diff / cleanup."""

    def __init__(self, store: SQLiteStore, git_adapter: GitWorktreeAdapter) -> None:
        self._store = store
        self._git = git_adapter

    # -- High-level operations --------------------------------------------------

    def create_for_task(self, params: dict[str, Any]) -> dict[str, Any]:
        """Create a worktree record AND a git worktree for a task.

        Steps:
        1. Validate no existing worktree for this task.
        2. Create git worktree via adapter.
        3. Create store record.
        """
        # Check for existing allocation
        existing = self._store.get_worktree_by_task({"taskId": params["taskId"]})
        if existing.get("worktree") is not None:
            raise ValueError(f"Task {params['taskId']} already has a worktree")

        # Create git worktree
        git_result = self._git.create(
            branch_name=params["branchName"],
            target_path=params["worktreePath"],
            base_ref=params.get("baseRef", "HEAD"),
        )

        # Create store record
        record = self._store.create_worktree(params)
        return {**record, "git": git_result}

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
