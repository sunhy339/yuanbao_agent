"""Patch parsing and application helpers for builtin tools.

Extracted from _shared.py. These operate as pure functions with explicit parameters.
"""
from __future__ import annotations

import difflib
import re
from pathlib import Path
from typing import Any


def normalize_patch_path(
    policy_guard: Any,
    workspace_root: Path,
    candidate_path: str,
) -> tuple[str, Path]:
    raw_path = str(candidate_path).strip()
    if not raw_path:
        raise ValueError("Patch path is required")

    normalized = raw_path.replace("\\", "/")
    if normalized.startswith("/"):
        raise ValueError(f"Path escapes workspace: {candidate_path}")
    if re.match(r"^[a-zA-Z]:/", normalized):
        raise ValueError(f"Path escapes workspace: {candidate_path}")

    relative = Path(normalized)
    policy_guard.ensure_within_workspace(str(workspace_root), relative.as_posix())
    absolute = (workspace_root / relative).resolve()
    return relative.as_posix(), absolute


def strip_git_prefix(path_text: str) -> str:
    text = path_text.strip()
    if text in {"/dev/null", "dev/null"}:
        return "/dev/null"
    if text.startswith("a/") or text.startswith("b/"):
        return text[2:]
    return text


def parse_hunk_header(header: str) -> tuple[int, int, int, int]:
    match = re.match(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", header)
    if match is None:
        raise ValueError(f"Invalid hunk header: {header}")
    old_start = int(match.group(1))
    old_count = int(match.group(2) or "1")
    new_start = int(match.group(3))
    new_count = int(match.group(4) or "1")
    return old_start, old_count, new_start, new_count


def parse_unified_diff(diff_text: str) -> list[dict[str, Any]]:
    lines = diff_text.splitlines()
    file_patches: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    index = 0

    while index < len(lines):
        line = lines[index]
        if line.startswith("diff --git "):
            if current is not None:
                file_patches.append(current)
            parts = line.split()
            old_path = strip_git_prefix(parts[2]) if len(parts) > 2 else ""
            new_path = strip_git_prefix(parts[3]) if len(parts) > 3 else ""
            current = {
                "old_path": old_path,
                "new_path": new_path,
                "hunks": [],
            }
            index += 1
            continue
        if current is None:
            if line.startswith("--- "):
                current = {"old_path": "", "new_path": "", "hunks": []}
            else:
                index += 1
                continue

        if line.startswith("--- "):
            if (
                current is not None
                and current.get("old_path")
                and current.get("new_path")
                and current.get("hunks")
            ):
                file_patches.append(current)
                current = {"old_path": "", "new_path": "", "hunks": []}
            current["old_path"] = strip_git_prefix(line[4:].strip().split("\t", 1)[0])
            index += 1
            if index >= len(lines) or not lines[index].startswith("+++ "):
                raise ValueError("Invalid unified diff: missing +++ header")
            current["new_path"] = strip_git_prefix(lines[index][4:].strip().split("\t", 1)[0])
            index += 1
            continue

        if line.startswith("@@ "):
            old_start, old_count, new_start, new_count = parse_hunk_header(line)
            hunk_lines: list[str] = []
            index += 1
            while index < len(lines):
                next_line = lines[index]
                if next_line.startswith("@@ ") or next_line.startswith("--- ") or next_line.startswith("diff --git "):
                    break
                if next_line.startswith("\\ No newline at end of file"):
                    index += 1
                    continue
                hunk_lines.append(next_line)
                index += 1
            current["hunks"].append(
                {
                    "old_start": old_start,
                    "old_count": old_count,
                    "new_start": new_start,
                    "new_count": new_count,
                    "lines": hunk_lines,
                }
            )
            continue

        index += 1

    if current is not None:
        file_patches.append(current)

    normalized: list[dict[str, Any]] = []
    for item in file_patches:
        old_path = str(item.get("old_path") or "").strip()
        new_path = str(item.get("new_path") or "").strip()
        hunks = item.get("hunks") or []
        if not old_path and not new_path:
            raise ValueError("Invalid unified diff: missing file headers")
        normalized.append(
            {
                "old_path": old_path,
                "new_path": new_path,
                "hunks": hunks,
            }
        )
    return normalized


def apply_unified_diff_to_file(
    policy_guard: Any,
    workspace_root: Path,
    file_patch: dict[str, Any],
    *,
    dry_run: bool = False,
) -> tuple[str | None, bool]:
    old_path = str(file_patch.get("old_path") or "")
    new_path = str(file_patch.get("new_path") or "")
    hunks = list(file_patch.get("hunks") or [])

    if old_path == "/dev/null":
        relative_path, absolute_path = normalize_patch_path(policy_guard, workspace_root, new_path)
        original_lines: list[str] = []
        is_new_file = True
    elif new_path == "/dev/null":
        relative_path, absolute_path = normalize_patch_path(policy_guard, workspace_root, old_path)
        if not absolute_path.exists():
            raise ValueError(f"File does not exist: {relative_path}")
        original_lines = absolute_path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
        is_new_file = False
    else:
        relative_path, absolute_path = normalize_patch_path(policy_guard, workspace_root, new_path or old_path)
        if not absolute_path.exists():
            raise ValueError(f"File does not exist: {relative_path}")
        original_lines = absolute_path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
        is_new_file = False

    output_lines: list[str] = []
    source_index = 0

    for hunk in hunks:
        old_start = int(hunk["old_start"])
        hunk_lines = [str(line) for line in hunk.get("lines") or []]
        target_index = max(0, old_start - 1)
        if target_index > len(original_lines):
            raise ValueError(f"Patch hunk starts past end of file: {relative_path}")
        output_lines.extend(original_lines[source_index:target_index])
        source_index = target_index

        for raw_line in hunk_lines:
            if not raw_line:
                raise ValueError("Invalid unified diff: empty hunk line")
            marker = raw_line[0]
            payload = raw_line[1:]
            if marker == " ":
                if source_index >= len(original_lines):
                    raise ValueError(f"Patch context mismatch in {relative_path}")
                current_line = original_lines[source_index].rstrip("\r\n")
                if current_line != payload:
                    raise ValueError(f"Patch context mismatch in {relative_path}")
                output_lines.append(original_lines[source_index])
                source_index += 1
            elif marker == "-":
                if source_index >= len(original_lines):
                    raise ValueError(f"Patch removal mismatch in {relative_path}")
                current_line = original_lines[source_index].rstrip("\r\n")
                if current_line != payload:
                    raise ValueError(f"Patch removal mismatch in {relative_path}")
                source_index += 1
            elif marker == "+":
                output_lines.append(payload + "\n")
            else:
                raise ValueError(f"Invalid unified diff marker: {marker}")

    output_lines.extend(original_lines[source_index:])
    new_content = "".join(output_lines)
    if new_path == "/dev/null":
        if absolute_path.exists() and not dry_run:
            absolute_path.unlink()
        return relative_path, True

    if not is_new_file and new_content == "".join(original_lines):
        return relative_path, False

    if not dry_run:
        absolute_path.parent.mkdir(parents=True, exist_ok=True)
        absolute_path.write_text(new_content, encoding="utf-8", errors="replace")
    return relative_path, True


def validate_patch_request(
    policy_guard: Any,
    workspace_root: Path,
    diff_text: str,
) -> list[str]:
    applied_paths: list[str] = []
    parsed_patch = parse_unified_diff(diff_text)
    for file_patch in parsed_patch:
        if not file_patch.get("hunks"):
            candidate = file_patch.get("new_path") or file_patch.get("old_path") or "unknown file"
            raise ValueError(f"Invalid unified diff: no hunks for {candidate}")
        relative_path, changed = apply_unified_diff_to_file(policy_guard, workspace_root, file_patch, dry_run=True)
        if changed and relative_path and relative_path not in applied_paths:
            applied_paths.append(relative_path)
    if not applied_paths:
        raise ValueError("No file changes detected")
    return applied_paths


def build_patch_summary(paths: list[str]) -> str:
    if not paths:
        return "No file changes detected."
    if len(paths) == 1:
        return f"Update {paths[0]}"
    if len(paths) == 2:
        return f"Update {paths[0]} and {paths[1]}"
    return f"Update {paths[0]}, {paths[1]} and {len(paths) - 2} more file(s)"


def build_patch_from_files(
    policy_guard: Any,
    workspace_root: Path,
    files: Any,
) -> tuple[str, list[str], int]:
    if not isinstance(files, list) or not files:
        raise ValueError("files must be a non-empty array")

    diff_chunks: list[str] = []
    changed_paths: list[str] = []
    for item in files:
        if not isinstance(item, dict):
            raise ValueError("files must contain objects")
        relative_path, absolute_path = normalize_patch_path(policy_guard, workspace_root, str(item.get("path") or ""))
        delete_file = bool(item.get("delete", False))
        if delete_file:
            original_text = absolute_path.read_text(encoding="utf-8", errors="replace") if absolute_path.exists() else ""
            new_text = ""
        else:
            if "content" not in item:
                raise ValueError("files items require content unless delete is true")
            original_text = absolute_path.read_text(encoding="utf-8", errors="replace") if absolute_path.exists() else ""
            new_text = str(item.get("content") or "")

        is_new_file = not absolute_path.exists()
        if original_text == new_text and not delete_file:
            continue

        original_lines = original_text.splitlines()
        new_lines = new_text.splitlines()
        diff_chunks.extend(
            difflib.unified_diff(
                original_lines,
                new_lines,
                fromfile="/dev/null" if is_new_file and not delete_file else f"a/{relative_path}",
                tofile="/dev/null" if delete_file else f"b/{relative_path}",
                lineterm="",
                n=3,
            )
        )
        changed_paths.append(relative_path)

    if not diff_chunks:
        raise ValueError("No file changes detected")

    return "\n".join(diff_chunks), changed_paths, len(changed_paths)


def build_patch_request(
    policy_guard: Any,
    params: dict[str, Any],
    workspace_root: Path,
) -> dict[str, Any]:
    patch_text = params.get("patchText") or params.get("patch_text")
    files = params.get("files")
    if patch_text and files:
        raise ValueError("Provide either patchText or files, not both")
    if not patch_text and not files:
        raise ValueError("patchText or files is required")

    if patch_text:
        diff_text = str(patch_text)
        parsed = parse_unified_diff(diff_text)
        changed_paths: list[str] = []
        for file_patch in parsed:
            candidate = file_patch["new_path"] if file_patch["new_path"] != "/dev/null" else file_patch["old_path"]
            if candidate and candidate not in changed_paths:
                changed_paths.append(candidate)
        if not changed_paths:
            raise ValueError("No file changes detected")
        return {
            "diffText": diff_text,
            "filesChanged": len(changed_paths),
            "changedPaths": changed_paths,
            "patchMode": "patchText",
        }

    diff_text, changed_paths, files_changed = build_patch_from_files(policy_guard, workspace_root, files)
    return {
        "diffText": diff_text,
        "filesChanged": files_changed,
        "changedPaths": changed_paths,
        "patchMode": "files",
    }


def build_patch_request_payload(
    *,
    task_id: str,
    workspace_root: Path,
    diff_text: str,
    files_changed: int,
    changed_paths: list[str],
    dry_run: bool,
    patch_mode: str,
    patch_text: str | None = None,
    files: Any = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "taskId": task_id,
        "workspaceRoot": str(workspace_root),
        "dryRun": dry_run,
        "patchMode": patch_mode,
        "filesChanged": files_changed,
        "changedPaths": changed_paths,
    }
    if patch_text is not None:
        payload["patchText"] = patch_text
    if files is not None:
        payload["files"] = files
    payload["diffText"] = diff_text
    return payload
