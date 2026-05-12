from __future__ import annotations

import json
from typing import Any


class WorktreeStoreMixin:
    # -----------------------------------------------------------------------
    # Worktree Isolation — extracted from ExtensionStoreMixin
    # -----------------------------------------------------------------------

    VALID_WORKTREE_STATUSES = frozenset({
        "creating", "active", "paused", "ready_for_review",
        "merged", "cancelled", "cleanup_pending", "cleaned", "failed",
    })
    VALID_CLEANUP_POLICIES = frozenset({"ask_user", "keep", "delete_if_clean", "archive_then_delete"})
    VALID_MERGE_POLICIES = frozenset({"approval_required", "auto_merge", "manual_only"})

    def _serialize_worktree(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": row["id"],
            "taskId": row["task_id"],
            "sessionId": row.get("session_id"),
            "workspaceId": row["workspace_id"],
            "baseRef": row["base_ref"],
            "branchName": row["branch_name"],
            "worktreePath": row["worktree_path"],
            "status": row["status"],
            "cleanupPolicy": row["cleanup_policy"],
            "mergePolicy": row["merge_policy"],
            "lastStatus": json.loads(row.get("last_status_json") or "{}"),
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
            "cleanedAt": row.get("cleaned_at"),
        }

    def create_worktree(self, params: dict[str, Any]) -> dict[str, Any]:
        """Create a worktree allocation record."""
        task_id = self._require_non_empty(params, "taskId")
        workspace_id = self._require_non_empty(params, "workspaceId")
        base_ref = params.get("baseRef", "HEAD")
        branch_name = self._require_non_empty(params, "branchName")
        worktree_path = self._require_non_empty(params, "worktreePath")

        cleanup_policy = params.get("cleanupPolicy", "ask_user")
        if cleanup_policy not in self.VALID_CLEANUP_POLICIES:
            raise ValueError(f"Invalid cleanup policy: {cleanup_policy}")
        merge_policy = params.get("mergePolicy", "approval_required")
        if merge_policy not in self.VALID_MERGE_POLICIES:
            raise ValueError(f"Invalid merge policy: {merge_policy}")

        now = self.now()
        wt_id = self.new_id("wt")
        self._conn.execute(
            """INSERT INTO task_worktrees
               (id, task_id, session_id, workspace_id, base_ref, branch_name,
                worktree_path, status, cleanup_policy, merge_policy,
                last_status_json, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'creating', ?, ?, '{}', ?, ?)""",
            (wt_id, task_id, params.get("sessionId"), workspace_id,
             base_ref, branch_name, worktree_path,
             cleanup_policy, merge_policy, now, now),
        )
        self._conn.commit()
        row = dict(self._conn.execute("SELECT * FROM task_worktrees WHERE id = ?", (wt_id,)).fetchone())
        return {"worktree": self._serialize_worktree(row)}

    def get_worktree(self, params: dict[str, Any]) -> dict[str, Any]:
        """Fetch a single worktree allocation."""
        wt_id = self._require_non_empty(params, "worktreeId")
        row = self._conn.execute("SELECT * FROM task_worktrees WHERE id = ?", (wt_id,)).fetchone()
        if row is None:
            raise ValueError(f"Worktree not found: {wt_id}")
        return {"worktree": self._serialize_worktree(dict(row))}

    def get_worktree_by_task(self, params: dict[str, Any]) -> dict[str, Any]:
        """Fetch worktree allocation for a specific task."""
        task_id = self._require_non_empty(params, "taskId")
        row = self._conn.execute("SELECT * FROM task_worktrees WHERE task_id = ?", (task_id,)).fetchone()
        if row is None:
            return {"worktree": None}
        return {"worktree": self._serialize_worktree(dict(row))}

    def list_worktrees(self, params: dict[str, Any]) -> dict[str, Any]:
        """List worktree allocations, optionally filtered."""
        conditions: list[str] = []
        args: list[Any] = []

        if "workspaceId" in params:
            conditions.append("workspace_id = ?")
            args.append(params["workspaceId"])
        if "sessionId" in params:
            conditions.append("session_id = ?")
            args.append(params["sessionId"])
        if "taskId" in params:
            conditions.append("task_id = ?")
            args.append(params["taskId"])
        if "status" in params:
            conditions.append("status = ?")
            args.append(params["status"])

        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        rows = self._conn.execute(
            f"SELECT * FROM task_worktrees{where} ORDER BY created_at DESC",
            args,
        ).fetchall()
        return {"worktrees": [self._serialize_worktree(dict(r)) for r in rows]}

    def update_worktree(self, params: dict[str, Any]) -> dict[str, Any]:
        """Update a worktree allocation."""
        wt_id = self._require_non_empty(params, "worktreeId")
        updates: list[str] = []
        args: list[Any] = []

        for col, key in [
            ("status", "status"),
            ("cleanup_policy", "cleanupPolicy"),
            ("merge_policy", "mergePolicy"),
            ("last_status_json", "lastStatus"),
            ("cleaned_at", "cleanedAt"),
            ("worktree_path", "worktreePath"),
            ("branch_name", "branchName"),
            ("base_ref", "baseRef"),
        ]:
            if key in params:
                val = params[key]
                if key == "status" and val not in self.VALID_WORKTREE_STATUSES:
                    raise ValueError(f"Invalid worktree status: {val}")
                if key == "cleanupPolicy" and val not in self.VALID_CLEANUP_POLICIES:
                    raise ValueError(f"Invalid cleanup policy: {val}")
                if key == "mergePolicy" and val not in self.VALID_MERGE_POLICIES:
                    raise ValueError(f"Invalid merge policy: {val}")
                if key == "lastStatus":
                    val = json.dumps(val, ensure_ascii=False)
                updates.append(f"{col} = ?")
                args.append(val)

        if not updates:
            raise ValueError("No fields to update")

        updates.append("updated_at = ?")
        args.append(self.now())
        args.append(wt_id)
        self._conn.execute(
            f"UPDATE task_worktrees SET {', '.join(updates)} WHERE id = ?", args,
        )
        self._conn.commit()
        row = dict(self._conn.execute("SELECT * FROM task_worktrees WHERE id = ?", (wt_id,)).fetchone())
        return {"worktree": self._serialize_worktree(row)}

    def delete_worktree(self, params: dict[str, Any]) -> dict[str, Any]:
        """Delete a worktree allocation record (does not remove filesystem worktree)."""
        wt_id = self._require_non_empty(params, "worktreeId")
        cursor = self._conn.execute("DELETE FROM task_worktrees WHERE id = ?", (wt_id,))
        self._conn.commit()
        return {"deleted": cursor.rowcount > 0}
