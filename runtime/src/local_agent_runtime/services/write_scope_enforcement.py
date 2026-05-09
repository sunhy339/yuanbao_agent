"""P9: Multi-agent write scope enforcement.

Enforces that child tasks only write within their declared write scopes.
"""

from __future__ import annotations

from typing import Any

from ..policy.proposal_validator import (
    detect_patch_conflicts,
    validate_patch_in_scope,
    validate_reviewer_gate,
    validate_write_scope_overlap,
)


class WriteScopeEnforcer:
    """Check write operations against declared write scopes."""

    def __init__(self, store: Any) -> None:
        self._store = store

    def get_task_write_scope(self, task_id: str) -> list[str]:
        """Return the write scope for a collaboration task, empty if unrestricted."""
        try:
            task = self._store.require_collaboration_task(task_id)
        except Exception:
            return []
        metadata = task.get("metadata") or {}
        # Check top-level writeScope (set by worker_runner from profile.ownedScope)
        write_scope = metadata.get("writeScope")
        if isinstance(write_scope, list):
            return [str(s) for s in write_scope]
        if isinstance(write_scope, str):
            return [write_scope]
        # Fallback: check profile.ownedScope
        profile = metadata.get("profile")
        if isinstance(profile, dict):
            owned = profile.get("ownedScope")
            if isinstance(owned, list):
                return [str(s) for s in owned]
            if isinstance(owned, str):
                return [owned]
        return []

    def check_patch_in_scope(
        self,
        task_id: str,
        target_path: str,
    ) -> list[str]:
        """Validate that a patch target is within the task's write scope.

        Returns a list of rejection reasons. Empty means allowed.
        """
        scope = self.get_task_write_scope(task_id)
        if not scope:
            # No scope declared — unrestricted
            return []
        return validate_patch_in_scope(
            {"targetPath": target_path, "path": target_path},
            allowed_scopes=scope,
        )

    def check_command_allowed(
        self,
        task_id: str,
        command_scope: str | None = None,
    ) -> list[str]:
        """Validate that a run_command is allowed for the task.

        Tasks with write scopes require the command target to match.
        Without a scope, run_command requires explicit opt-in via metadata.
        """
        scope = self.get_task_write_scope(task_id)
        if not scope and command_scope is None:
            # No scope restriction, no target — allow
            return []
        if command_scope and scope:
            return validate_patch_in_scope(
                {"targetPath": command_scope, "path": command_scope},
                allowed_scopes=scope,
            )
        return []

    def check_overlap_before_dispatch(
        self,
        subtasks: list[dict[str, Any]],
    ) -> list[str]:
        """Detect overlapping write scopes before dispatching child tasks."""
        return validate_write_scope_overlap(subtasks)

    def check_patch_conflicts(
        self,
        patches: list[dict[str, Any]],
    ) -> list[str]:
        """Detect conflicting patch targets across tasks."""
        return detect_patch_conflicts(patches)

    def check_reviewer_gate(self, payload: dict[str, Any]) -> list[str]:
        """Validate reviewer gate — reject merge when review is negative."""
        return validate_reviewer_gate(payload)
