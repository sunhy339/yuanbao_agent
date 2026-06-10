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

_DIFF_ARTIFACT_MIN_CHARS = 8000


def _step(label: str, status: str, summary: str) -> dict[str, str]:
    return {"label": label, "status": status, "summary": summary}


def _maybe_create_diff_artifact(
    store: Any,
    params: dict[str, Any],
    *,
    diff_text: str,
    files: list[dict[str, Any]],
    staged: bool,
    pathspec: str | None,
) -> str:
    if len(diff_text) <= _DIFF_ARTIFACT_MIN_CHARS or not hasattr(store, "create_artifact"):
        return ""
    session_id = str(params.get("sessionId") or "").strip()
    task_id = str(params.get("taskId") or "").strip()
    if not session_id or not task_id:
        return ""
    try:
        artifact = store.create_artifact({
            "sessionId": session_id,
            "parentTaskId": task_id,
            "producerTaskId": task_id,
            "kind": "patch",
            "title": "git diff",
            "description": "Large git diff captured for traceable review.",
            "content": {
                "diff": diff_text,
                "files": files,
                "staged": staged,
                "path": pathspec,
            },
            "metadata": {
                "source": "git_diff",
                "chars": len(diff_text),
            },
        }).get("artifact", {})
    except Exception:  # noqa: BLE001
        return ""
    artifact_id = artifact.get("id") if isinstance(artifact, dict) else None
    return str(artifact_id or "")


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
                "status": "ok",
                "toolName": "git_diff",
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
        diff_text = diff_completed.stdout or ""

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

        artifact_id = _maybe_create_diff_artifact(
            store,
            params,
            diff_text=diff_text,
            files=files,
            staged=staged,
            pathspec=pathspec,
        )

        return {
            "status": "ok",
            "toolName": "git_diff",
            "workspaceRoot": str(workspace_root),
            "cwd": relative_cwd,
            "isGitRepository": True,
            "staged": staged,
            "path": pathspec,
            "files": files,
            "diff": diff_text,
            **({"artifactId": artifact_id} if artifact_id else {}),
            "steps": [
                *steps,
                _step("diff", "completed", f"{len(diff_text)} character(s)"),
                _step("files", "completed", f"{len(files)} file(s)"),
            ],
        }

    return {"handler": git_diff}
