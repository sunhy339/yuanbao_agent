"""git_diff tool — git diff with staged/pathspec support."""

from __future__ import annotations

from typing import Any

from ._shared import (
    is_git_repository,
    parse_name_status_line,
    require_workspace_root,
    resolve_git_cwd,
    resolve_git_pathspec,
    run_git_command,
    to_relative_path,
)


def _step(label: str, status: str, summary: str) -> dict[str, str]:
    return {"label": label, "status": status, "summary": summary}


def build_git_diff_tool(policy_guard: Any, store: Any, subagent_service: Any | None = None) -> dict[str, Any]:
    def git_diff(params: dict[str, Any]) -> dict[str, Any]:
        workspace_root = require_workspace_root(params)
        cwd = resolve_git_cwd(policy_guard, workspace_root, params)
        staged = bool(params.get("staged", False))
        pathspec = resolve_git_pathspec(policy_guard, workspace_root, cwd, params.get("path"))
        relative_cwd = to_relative_path(workspace_root, cwd)
        steps = [
            _step("resolve", "completed", relative_cwd),
            _step("repository", "completed", "git" if is_git_repository(cwd) else "not git"),
        ]
        if steps[-1]["summary"] == "not git":
            return {
                "workspaceRoot": str(workspace_root),
                "cwd": relative_cwd,
                "isGitRepository": False,
                "staged": staged,
                "path": pathspec,
                "files": [],
                "diff": "",
                "summary": "Not a git repository.",
                "steps": steps,
            }

        git_args = ["diff"]
        if staged:
            git_args.append("--staged")
        if pathspec is not None:
            git_args.extend(["--", pathspec])

        diff_completed = run_git_command(cwd, git_args)

        name_status_args = ["diff"]
        if staged:
            name_status_args.append("--staged")
        name_status_args.append("--name-status")
        if pathspec is not None:
            name_status_args.extend(["--", pathspec])
        files_completed = run_git_command(cwd, name_status_args)

        files = [
            parse_name_status_line(line)
            for line in (files_completed.stdout or "").splitlines()
            if line.strip()
        ]

        return {
            "workspaceRoot": str(workspace_root),
            "cwd": relative_cwd,
            "isGitRepository": True,
            "staged": staged,
            "path": pathspec,
            "files": files,
            "diff": diff_completed.stdout or "",
            "steps": [
                *steps,
                _step("diff", "completed", f"{len(diff_completed.stdout or '')} character(s)"),
                _step("files", "completed", f"{len(files)} file(s)"),
            ],
        }

    return {"handler": git_diff}
