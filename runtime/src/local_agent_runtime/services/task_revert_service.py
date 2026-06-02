from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any


class TaskRevertError(RuntimeError):
    def __init__(self, message: str, *, code: str = "PATCH_APPLY_FAILED") -> None:
        super().__init__(message)
        self.code = code
        self.retryable = False


def _changed_paths_from_diff_text(diff_text: str) -> list[str]:
    paths: list[str] = []
    old_path = ""
    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            parts = line.split()
            if len(parts) >= 4:
                _append_changed_path(paths, parts[3])
            continue
        if line.startswith("--- "):
            old_path = line[4:].strip()
            continue
        if line.startswith("+++ "):
            new_path = line[4:].strip()
            _append_changed_path(paths, new_path if new_path != "/dev/null" else old_path)
    return paths


def _append_changed_path(paths: list[str], path: str) -> None:
    normalized = path.strip().replace("\\", "/")
    if normalized.startswith(("a/", "b/")):
        normalized = normalized[2:]
    if normalized and normalized != "/dev/null" and normalized not in paths:
        paths.append(normalized)


def _resolved_workspace_root(task: dict[str, Any], session: dict[str, Any]) -> Path:
    worktree = task.get("routing", {}).get("activeWorktree") if isinstance(task.get("routing"), dict) else None
    worktree_path = worktree.get("worktreePath") if isinstance(worktree, dict) else None
    root_value = worktree_path if isinstance(worktree_path, str) and worktree_path.strip() else session.get("workspaceRoot")
    if not isinstance(root_value, str) or not root_value.strip():
        raise TaskRevertError("Workspace root is unavailable for this task", code="INVALID_ARGUMENT")
    root = Path(root_value).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        raise TaskRevertError(f"Workspace root does not exist: {root_value}", code="INVALID_ARGUMENT")
    return root


def _revertable_patches(store: Any, task_id: str) -> list[dict[str, Any]]:
    patches = [
        patch
        for patch in store.list_patches_for_task(task_id)
        if patch.get("status") == "applied" and isinstance(patch.get("diffText"), str) and patch.get("diffText", "").strip()
    ]
    if not patches:
        raise TaskRevertError("No applied patch diff is available for this task", code="INVALID_ARGUMENT")
    return patches


def _patch_changed_paths(patch: dict[str, Any]) -> list[str]:
    paths: list[str] = []
    for path in patch.get("changedPaths") or _changed_paths_from_diff_text(str(patch.get("diffText") or "")):
        _append_changed_path(paths, str(path))
    return paths


def _all_changed_paths(patches: list[dict[str, Any]]) -> list[str]:
    paths: list[str] = []
    for patch in patches:
        for path in _patch_changed_paths(patch):
            _append_changed_path(paths, path)
    return paths


def _copy_changed_files(workspace_root: Path, changed_paths: list[str]) -> tempfile.TemporaryDirectory[str]:
    temp_dir = tempfile.TemporaryDirectory()
    simulation_root = Path(temp_dir.name)
    for path in changed_paths:
        source = (workspace_root / path).resolve()
        try:
            source.relative_to(workspace_root)
        except ValueError as exc:
            temp_dir.cleanup()
            raise TaskRevertError(f"Patch path escapes workspace: {path}", code="PATH_OUT_OF_SCOPE") from exc
        if not source.is_file():
            continue
        destination = simulation_root / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    return temp_dir


def _run_git_apply(workspace_root: Path, diff_text: str, *, check: bool, reverse: bool) -> subprocess.CompletedProcess[str]:
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="\n", suffix=".diff", delete=False) as handle:
        handle.write(diff_text)
        patch_path = handle.name
    try:
        command = ["git", "-C", str(workspace_root), "apply", "--whitespace=nowarn"]
        if reverse:
            command.append("-R")
        if check:
            command.append("--check")
        command.append(patch_path)
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    finally:
        try:
            os.unlink(patch_path)
        except OSError:
            pass


def _raise_apply_error(result: subprocess.CompletedProcess[str], *, phase: str) -> None:
    detail = (result.stderr or result.stdout or "").strip()
    if not detail:
        detail = f"git apply -R {phase} failed with exit code {result.returncode}"
    raise TaskRevertError(detail)


def _validate_reverse_sequence(workspace_root: Path, patches: list[dict[str, Any]]) -> list[str]:
    changed_paths = _all_changed_paths(patches)
    temp_dir = _copy_changed_files(workspace_root, changed_paths)
    try:
        simulation_root = Path(temp_dir.name)
        for patch in reversed(patches):
            diff_text = str(patch["diffText"]).strip("\n") + "\n"
            check = _run_git_apply(simulation_root, diff_text, check=True, reverse=True)
            if check.returncode != 0:
                _raise_apply_error(check, phase="check")
            applied = _run_git_apply(simulation_root, diff_text, check=False, reverse=True)
            if applied.returncode != 0:
                _raise_apply_error(applied, phase="simulation")
        return changed_paths
    finally:
        temp_dir.cleanup()


def _apply_reverse_sequence(workspace_root: Path, patches: list[dict[str, Any]]) -> None:
    applied: list[dict[str, Any]] = []
    for patch in reversed(patches):
        diff_text = str(patch["diffText"]).strip("\n") + "\n"
        check = _run_git_apply(workspace_root, diff_text, check=True, reverse=True)
        if check.returncode != 0:
            _rollback_reverse_sequence(workspace_root, applied)
            _raise_apply_error(check, phase="check")
        result = _run_git_apply(workspace_root, diff_text, check=False, reverse=True)
        if result.returncode != 0:
            _rollback_reverse_sequence(workspace_root, applied)
            _raise_apply_error(result, phase="apply")
        applied.append(patch)


def _rollback_reverse_sequence(workspace_root: Path, applied: list[dict[str, Any]]) -> None:
    for patch in reversed(applied):
        diff_text = str(patch["diffText"]).strip("\n") + "\n"
        check = _run_git_apply(workspace_root, diff_text, check=True, reverse=False)
        if check.returncode == 0:
            _run_git_apply(workspace_root, diff_text, check=False, reverse=False)


def revert_task_changes(store: Any, params: dict[str, Any]) -> dict[str, Any]:
    task_id = str(params.get("taskId") or params.get("task_id") or "").strip()
    if not task_id:
        raise TaskRevertError("taskId is required", code="INVALID_ARGUMENT")

    task = store.get_task({"taskId": task_id})["task"]
    session = store.require_session(task["sessionId"])
    workspace_root = _resolved_workspace_root(task, session)
    patches = _revertable_patches(store, task_id)
    changed_paths = _validate_reverse_sequence(workspace_root, patches)
    _apply_reverse_sequence(workspace_root, patches)

    updated_patches: list[dict[str, Any]] = []
    for patch in patches:
        updated_patches.append(store.update_patch(patch["id"], status="reverted"))

    remaining_changed_files = [
        dict(item)
        for item in (task.get("changedFiles") or [])
        if not (isinstance(item, dict) and item.get("patchId") in {patch["id"] for patch in patches})
    ]
    task = store.update_task(task_id, changed_files=remaining_changed_files)
    store.append_trace_event(
        task_id=task_id,
        event_type="task.changes_reverted",
        source="task",
        related_id=task_id,
        payload={
            "taskId": task_id,
            "workspaceRoot": str(workspace_root),
            "patchIds": [patch["id"] for patch in patches],
            "changedPaths": changed_paths,
            "filesChanged": len(changed_paths),
            "status": "reverted",
        },
    )
    return {
        "task": task,
        "patches": updated_patches,
        "changedPaths": changed_paths,
        "reverted": True,
    }
