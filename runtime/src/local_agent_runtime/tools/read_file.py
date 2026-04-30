"""read_file tool — read a workspace file."""

from __future__ import annotations

from typing import Any

from ._shared import (
    is_ignored,
    require_workspace_root,
    resolve_workspace_path,
    to_relative_path,
)


def build_read_file_tool(policy_guard: Any, store: Any, subagent_service: Any | None = None) -> dict[str, Any]:
    def read_file(params: dict[str, Any]) -> dict[str, Any]:
        workspace_root = require_workspace_root(params)
        file_path = resolve_workspace_path(policy_guard, workspace_root, params["path"])
        if not file_path.is_file():
            raise ValueError(f"File does not exist: {params['path']}")
        if is_ignored(file_path, workspace_root, store, params.get("ignore")):
            raise ValueError(f"File is ignored by current search rules: {params['path']}")

        encoding = params.get("encoding", "utf-8")
        max_bytes = params.get("max_bytes")
        raw = file_path.read_bytes()
        truncated = False
        if max_bytes is not None:
            limit = max(1, int(max_bytes))
            truncated = len(raw) > limit
            raw = raw[:limit]

        return {
            "path": to_relative_path(workspace_root, file_path),
            "content": raw.decode(encoding, errors="replace"),
            "encoding": encoding,
            "truncated": truncated,
            "bytesRead": len(raw),
            "totalBytes": file_path.stat().st_size,
        }

    return {"handler": read_file}
