"""write_file tool — create or replace a workspace file."""

from __future__ import annotations

from typing import Any

from ._shared import (
    require_workspace_root,
    resolve_workspace_path,
    to_relative_path,
)


def build_write_file_tool(policy_guard: Any, store: Any, subagent_service: Any | None = None) -> dict[str, Any]:
    def write_file(params: dict[str, Any]) -> dict[str, Any]:
        workspace_root = require_workspace_root(params)
        file_path = resolve_workspace_path(policy_guard, workspace_root, params["path"])
        content = params.get("content", "")
        encoding = params.get("encoding", "utf-8")
        create_dirs = params.get("create_dirs", True)

        if not file_path.is_file() and not params.get("overwrite", False):
            # New file creation — always allowed
            pass

        if create_dirs and not file_path.parent.exists():
            file_path.parent.mkdir(parents=True, exist_ok=True)

        existing = file_path.is_file()
        old_bytes = file_path.read_bytes() if existing else b""
        new_bytes = content.encode(encoding, errors="replace")

        file_path.write_bytes(new_bytes)

        return {
            "path": to_relative_path(workspace_root, file_path),
            "bytesWritten": len(new_bytes),
            "created": not existing,
            "encoding": encoding,
        }

    return {"handler": write_file}
