"""Worktree tools — enter_worktree and exit_worktree.

Expose the WorktreeService as model-callable tools, aligned with haha-cc's
EnterWorktreeTool and ExitWorktreeTool.
"""
from __future__ import annotations

from typing import Any


def _text(value: Any, *, limit: int, default: str = "") -> str:
    text = str(value or "").strip()
    if not text:
        text = default
    return text[:limit]


def build_enter_worktree_tool(worktree_service: Any, store: Any, *_: Any, **__: Any) -> dict[str, Any]:
    """Build the enter_worktree tool.

    Creates a git worktree for the current task, isolating file changes
    from the main working directory.
    """
    def handler(params: dict[str, Any]) -> dict[str, Any]:
        task_id = _text(params.get("taskId") or params.get("task_id"), limit=120)
        if not task_id:
            raise ValueError("taskId is required for enter_worktree")

        workspace_id = _text(params.get("workspaceId") or params.get("workspace_id"), limit=120)
        session_id = _text(params.get("sessionId") or params.get("session_id"), limit=120)
        branch_name = _text(params.get("branchName") or params.get("branch_name"), limit=240)
        if not branch_name:
            # Generate a default branch name from task_id
            short_id = task_id.replace("-", "_")[:16]
            branch_name = f"worktree-{short_id}"

        worktree_path = _text(params.get("worktreePath") or params.get("worktree_path"), limit=500)
        if not worktree_path:
            # Default path under .worktrees directory
            import os
            repo_root = os.environ.get("LOCAL_AGENT_REPO_ROOT", ".")
            worktree_path = os.path.join(repo_root, ".worktrees", task_id[:12])

        base_ref = _text(params.get("baseRef") or params.get("base_ref"), limit=120, default="HEAD")

        create_params = {
            "taskId": task_id,
            "workspaceId": workspace_id,
            "sessionId": session_id,
            "branchName": branch_name,
            "worktreePath": worktree_path,
            "baseRef": base_ref,
        }

        try:
            result = worktree_service.create_for_task(create_params)
        except ValueError as exc:
            return {
                "status": "failed",
                "toolName": "enter_worktree",
                "error": str(exc),
                "summary": f"Worktree creation failed: {exc}",
            }

        worktree = result.get("worktree", {})
        git_result = result.get("git", {})
        return {
            "status": "worktree_created",
            "toolName": "enter_worktree",
            "summary": f"Worktree created at {worktree.get('worktreePath', worktree_path)}",
            "worktreeId": worktree.get("id", ""),
            "branchName": worktree.get("branchName", branch_name),
            "worktreePath": worktree.get("worktreePath", worktree_path),
            "baseRef": worktree.get("baseRef", base_ref),
            "gitBranch": git_result.get("branch", ""),
            "steps": [
                {"label": "prepare", "status": "completed", "summary": "Validating worktree parameters"},
                {"label": "create", "status": "completed", "summary": f"Created worktree at {worktree.get('worktreePath', worktree_path)}"},
            ],
        }

    return {"handler": handler}


def build_exit_worktree_tool(worktree_service: Any, store: Any, *_: Any, **__: Any) -> dict[str, Any]:
    """Build the exit_worktree tool.

    Handles worktree exit: either cleanup (remove worktree) or merge (merge
    changes back to target branch). Requires approval for merge operations.
    """
    def handler(params: dict[str, Any]) -> dict[str, Any]:
        task_id = _text(params.get("taskId") or params.get("task_id"), limit=120)
        if not task_id:
            raise ValueError("taskId is required for exit_worktree")

        action = _text(
            params.get("action") or params.get("mode"),
            limit=80,
            default="cleanup",
        ).lower()

        worktree_id = _text(params.get("worktreeId") or params.get("worktree_id"), limit=120)

        # Resolve worktree record
        lookup_params: dict[str, Any] = {}
        if worktree_id:
            lookup_params["worktreeId"] = worktree_id
        else:
            lookup_params["taskId"] = task_id

        try:
            status_result = worktree_service.get_status(lookup_params)
        except ValueError as exc:
            return {
                "status": "failed",
                "toolName": "exit_worktree",
                "error": str(exc),
                "summary": f"Worktree not found: {exc}",
            }

        worktree = status_result.get("worktree", {})
        resolved_worktree_id = worktree.get("id", worktree_id)
        worktree_path = worktree.get("worktreePath", "")

        if action in ("cleanup", "clean", "remove", "discard"):
            force = bool(params.get("force", False))
            try:
                cleanup_result = worktree_service.cleanup({
                    "worktreeId": resolved_worktree_id,
                    "force": force,
                })
            except ValueError as exc:
                return {
                    "status": "failed",
                    "toolName": "exit_worktree",
                    "error": str(exc),
                    "summary": f"Worktree cleanup failed: {exc}",
                }
            return {
                "status": "worktree_cleaned",
                "toolName": "exit_worktree",
                "summary": f"Worktree cleaned: {worktree_path}",
                "worktreeId": resolved_worktree_id,
                "cleaned": cleanup_result.get("cleaned", True),
                "steps": [
                    {"label": "prepare", "status": "completed", "summary": "Validating cleanup request"},
                    {"label": "cleanup", "status": "completed", "summary": f"Removed worktree at {worktree_path}"},
                ],
            }

        if action in ("merge", "integrate"):
            target_branch = _text(params.get("targetBranch") or params.get("target_branch"), limit=120, default="main")
            approval_id = _text(params.get("approvalId") or params.get("approval_id"), limit=120)

            merge_params: dict[str, Any] = {
                "worktreeId": resolved_worktree_id,
                "targetBranch": target_branch,
            }
            if approval_id:
                merge_params["approvalId"] = approval_id

            # If no approval yet, request one
            if not approval_id:
                try:
                    approval_result = worktree_service.request_merge_approval({
                        "worktreeId": resolved_worktree_id,
                        "targetBranch": target_branch,
                    })
                except ValueError as exc:
                    return {
                        "status": "failed",
                        "toolName": "exit_worktree",
                        "error": str(exc),
                        "summary": f"Worktree merge approval request failed: {exc}",
                    }

                approval = approval_result.get("approval", {})
                return {
                    "status": "approval_required",
                    "toolName": "exit_worktree",
                    "summary": "Worktree merge requires approval.",
                    "approval": approval,
                    "worktreeId": resolved_worktree_id,
                    "targetBranch": target_branch,
                    "diffStat": approval_result.get("diff", {}).get("diffStat", ""),
                    "steps": [
                        {"label": "prepare", "status": "completed", "summary": "Preparing merge request"},
                        {"label": "approval", "status": "blocked", "summary": "Worktree merge approval required"},
                    ],
                }

            # Execute merge with approved approval
            try:
                merge_result = worktree_service.merge(merge_params)
            except ValueError as exc:
                return {
                    "status": "failed",
                    "toolName": "exit_worktree",
                    "error": str(exc),
                    "summary": f"Worktree merge failed: {exc}",
                }

            merged = merge_result.get("merged", False)
            return {
                "status": "worktree_merged" if merged else "merge_failed",
                "toolName": "exit_worktree",
                "summary": merge_result.get("result", {}).get("message", "") or (
                    f"Worktree merged into {target_branch}" if merged else "Merge failed"
                ),
                "worktreeId": resolved_worktree_id,
                "merged": merged,
                "branchName": merge_result.get("branchName", ""),
                "targetBranch": target_branch,
                "steps": [
                    {"label": "prepare", "status": "completed", "summary": "Preparing merge"},
                    {"label": "merge", "status": "completed" if merged else "failed", "summary": str(merge_result.get("result", ""))[:180]},
                ],
            }

        return {
            "status": "failed",
            "toolName": "exit_worktree",
            "error": f"Unknown exit_worktree action: {action}. Use 'cleanup' or 'merge'.",
        }

    return {"handler": handler}
