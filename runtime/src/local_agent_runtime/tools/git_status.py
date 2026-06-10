"""git_status tool — git repository status."""

from __future__ import annotations

from typing import Any

from ._shared import (
    is_git_repository,
    parse_git_status_header,
    require_workspace_root,
    resolve_git_cwd,
    run_git_command,
    to_relative_path,
)


def _step(label: str, status: str, summary: str) -> dict[str, str]:
    return {"label": label, "status": status, "summary": summary}


def build_git_status_tool(policy_guard: Any, store: Any, subagent_service: Any | None = None) -> dict[str, Any]:
    def git_status(_params: dict[str, Any]) -> dict[str, Any]:
        params = _params
        workspace_root = require_workspace_root(params)
        cwd = resolve_git_cwd(policy_guard, workspace_root, params)
        relative_cwd = to_relative_path(workspace_root, cwd)
        steps = [
            _step("resolve", "completed", relative_cwd),
            _step("repository", "completed", "git" if is_git_repository(cwd) else "not git"),
        ]
        if steps[-1]["summary"] == "not git":
            return {
                "status": "ok",
                "toolName": "git_status",
                "workspaceRoot": to_relative_path(workspace_root, workspace_root),
                "cwd": relative_cwd,
                "isGitRepository": False,
                "branch": None,
                "upstream": None,
                "ahead": 0,
                "behind": 0,
                "changes": [],
                "summary": "Not a git repository.",
                "steps": steps,
            }
        completed = run_git_command(cwd, ["status", "--short", "--branch"])

        stdout_lines = [line for line in (completed.stdout or "").splitlines() if line.strip()]
        branch = None
        upstream = None
        ahead = 0
        behind = 0
        changes: list[dict[str, Any]] = []

        if stdout_lines:
            branch, upstream, ahead, behind = parse_git_status_header(stdout_lines[0])
            for line in stdout_lines[1:]:
                if line.startswith("## "):
                    continue
                status_code = line[:2].strip() or line[:2]
                path_text = line[3:].strip() if len(line) > 3 else ""
                entry: dict[str, Any] = {
                    "status": status_code,
                    "path": path_text,
                    "raw": line,
                }
                if " -> " in path_text and status_code[:1] in {"R", "C"}:
                    original_path, new_path = path_text.split(" -> ", 1)
                    entry["originalPath"] = original_path.strip()
                    entry["path"] = new_path.strip()
                changes.append(entry)

        return {
            "status": "ok",
            "toolName": "git_status",
            "workspaceRoot": to_relative_path(workspace_root, workspace_root),
            "cwd": relative_cwd,
            "isGitRepository": True,
            "branch": branch,
            "upstream": upstream,
            "ahead": ahead,
            "behind": behind,
            "changes": changes,
            "steps": [
                *steps,
                _step("status", "completed", f"{len(changes)} change(s)"),
            ],
        }

    return {"handler": git_status}
