"""list_dir tool — directory listing with ignore patterns."""

from __future__ import annotations

from typing import Any

from ._shared import (
    require_workspace_root,
    resolve_workspace_path,
    to_relative_path,
    walk_directory,
)


def _step(label: str, status: str, summary: str) -> dict[str, str]:
    return {"label": label, "status": status, "summary": summary}


def build_list_dir_tool(policy_guard: Any, store: Any, subagent_service: Any | None = None) -> dict[str, Any]:
    def list_dir(params: dict[str, Any]) -> dict[str, Any]:
        workspace_root = require_workspace_root(params)
        recursive = bool(params.get("recursive", False))
        max_depth = int(params.get("max_depth", 1 if recursive else 1))
        max_depth = max(1, min(max_depth, 8))
        ignore_patterns = params.get("ignore")
        directory = resolve_workspace_path(policy_guard, workspace_root, params.get("path"))
        if not directory.is_dir():
            raise ValueError(f"Directory does not exist: {params.get('path', '.')}")
        relative_path = to_relative_path(workspace_root, directory)
        items = walk_directory(
            workspace_root=workspace_root,
            base_path=directory,
            recursive=recursive,
            max_depth=max_depth,
            store=store,
            ignore_patterns=ignore_patterns,
        )

        return {
            "path": relative_path,
            "recursive": recursive,
            "maxDepth": max_depth,
            "items": items,
            "steps": [
                _step("resolve", "completed", relative_path),
                _step("walk", "completed", f"{len(items)} item(s)"),
            ],
        }

    return {"handler": list_dir}
