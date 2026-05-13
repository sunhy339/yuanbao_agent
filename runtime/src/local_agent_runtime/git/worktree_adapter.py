"""Git Worktree Adapter — low-level git worktree operations.

Wraps `git worktree` CLI commands for create, status, diff, merge, and cleanup.
This adapter does not know about tasks or the store; it only manages git state.
"""
from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class GitWorktreeAdapter:
    """Low-level git worktree operations via CLI."""

    def __init__(self, repo_root: str | Path) -> None:
        self._repo_root = str(repo_root)

    def _run_git(self, *args: str, cwd: str | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
        cmd = ["git", *args]
        return subprocess.run(
            cmd,
            cwd=cwd or self._repo_root,
            capture_output=True,
            text=True,
            check=check,
        )

    # -- Create / Remove -------------------------------------------------------

    def create(self, branch_name: str, target_path: str, base_ref: str = "HEAD") -> dict[str, Any]:
        """Create a git worktree at *target_path* from *base_ref*.

        Returns dict with ``branch``, ``path``, ``base_ref``.
        """
        self._run_git("worktree", "add", "-b", branch_name, target_path, base_ref)
        return {"branch": branch_name, "path": target_path, "baseRef": base_ref}

    def remove(self, target_path: str, force: bool = False) -> dict[str, Any]:
        """Remove a git worktree."""
        args = ["worktree", "remove", target_path]
        if force:
            args.append("--force")
        self._run_git(*args)
        return {"removed": target_path}

    # -- Status / Diff ---------------------------------------------------------

    def status(self, target_path: str) -> dict[str, Any]:
        """Return ``git status --porcelain`` for the worktree."""
        result = self._run_git("status", "--porcelain", cwd=target_path, check=False)
        files = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        return {"dirtyFiles": len(files), "files": files}

    def diff(self, target_path: str, base_ref: str = "HEAD") -> dict[str, Any]:
        """Return diff summary between worktree and *base_ref*."""
        result = self._run_git("diff", "--stat", base_ref, cwd=target_path, check=False)
        return {"diffStat": result.stdout.strip()}

    # -- Branch helpers --------------------------------------------------------

    def list_worktrees(self) -> list[dict[str, str]]:
        """List all worktrees (``git worktree list --porcelain``)."""
        result = self._run_git("worktree", "list", "--porcelain")
        entries: list[dict[str, str]] = []
        current: dict[str, str] = {}
        for line in result.stdout.splitlines():
            if line.startswith("worktree "):
                if current:
                    entries.append(current)
                current = {"path": line.split(" ", 1)[1]}
            elif line.startswith("branch "):
                current["branch"] = line.split(" ", 1)[1]
            elif not line.strip():
                if current:
                    entries.append(current)
                    current = {}
        if current:
            entries.append(current)
        return entries

    def merge(self, branch_name: str, target_branch: str = "main") -> dict[str, Any]:
        """Merge *branch_name* into *target_branch* in the main repo.

        Returns dict with ``mergedBranch``, ``targetBranch``, ``result``.
        """
        self._run_git("checkout", target_branch)
        result = self._run_git("merge", branch_name, check=False)
        return {
            "mergedBranch": branch_name,
            "targetBranch": target_branch,
            "result": "ok" if result.returncode == 0 else "conflict",
            "stdout": result.stdout.strip(),
        }

    def has_clean_branch(self, target_path: str) -> bool:
        """Return True if the worktree has no uncommitted changes."""
        return self.status(target_path)["dirtyFiles"] == 0
