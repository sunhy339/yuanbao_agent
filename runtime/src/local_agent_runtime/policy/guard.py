from __future__ import annotations

from pathlib import Path


class PolicyGuard:
    """Applies coarse safety checks before tools run."""

    def __init__(self, approval_mode: str = "on_write_or_command") -> None:
        self._approval_mode = approval_mode

    def ensure_within_workspace(self, workspace_root: str, candidate_path: str) -> None:
        root = Path(workspace_root).resolve()
        candidate = (root / candidate_path).resolve()
        if root not in candidate.parents and candidate != root:
            raise ValueError(f"Path escapes workspace: {candidate_path}")

    def requires_approval(self, tool_name: str, *, approval_mode: str | None = None) -> bool:
        mode = approval_mode or self._approval_mode
        if mode == "relaxed":
            return tool_name in {"apply_patch", "write_file"}
        if mode == "strict":
            return tool_name in {"apply_patch", "write_file", "run_command"}
        return tool_name in {"apply_patch", "write_file", "run_command"}
