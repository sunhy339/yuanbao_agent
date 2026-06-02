"""read_file tool — read a workspace file."""

from __future__ import annotations

from typing import Any
from pathlib import PurePosixPath

from ._shared import (
    is_ignored,
    require_workspace_root,
    resolve_workspace_path,
    to_relative_path,
)


def _step(label: str, status: str, summary: str) -> dict[str, str]:
    return {"label": label, "status": status, "summary": summary}


def build_read_file_tool(policy_guard: Any, store: Any, subagent_service: Any | None = None) -> dict[str, Any]:
    def read_file(params: dict[str, Any]) -> dict[str, Any]:
        workspace_root = require_workspace_root(params)
        file_path = resolve_workspace_path(policy_guard, workspace_root, params["path"])
        if not file_path.is_file():
            raise ValueError(f"File does not exist: {params['path']}")
        if is_ignored(file_path, workspace_root, store, params.get("ignore")):
            raise ValueError(f"File is ignored by current search rules: {params['path']}")

        relative_path = to_relative_path(workspace_root, file_path)
        encoding = params.get("encoding", "utf-8")
        max_bytes = params.get("max_bytes")
        raw = file_path.read_bytes()
        total_bytes = len(raw)
        steps = [
            _step("resolve", "completed", relative_path),
            _step("read", "completed", f"{total_bytes} byte(s)"),
        ]
        truncated = False
        if max_bytes is not None:
            limit = max(1, int(max_bytes))
            truncated = len(raw) > limit
            raw = raw[:limit]
            if truncated:
                steps.append(_step("truncate", "completed", f"limited to {limit} byte(s)"))
        steps.append(_step("decode", "completed", str(encoding)))

        lowered_name = PurePosixPath(relative_path).name.lower()
        trust = "trusted"
        trust_reason = "Workspace source file."
        if lowered_name in {"readme.md", "readme", "instructions.md", "contributing.md"} or lowered_name.endswith((".md", ".rst", ".txt")):
            trust = "untrusted"
            trust_reason = "Document-style workspace content can contain untrusted instructions and should not directly trigger high-risk tools."
        return {
            "path": relative_path,
            "content": raw.decode(encoding, errors="replace"),
            "encoding": encoding,
            "truncated": truncated,
            "bytesRead": len(raw),
            "totalBytes": total_bytes,
            "contentSource": "workspace_document" if trust == "untrusted" else "workspace_file",
            "contentTrust": trust,
            "contentTrustReason": trust_reason,
            "steps": steps,
        }

    return {"handler": read_file}
