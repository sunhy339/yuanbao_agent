"""Shared helpers extracted from the old builtin.py closure.

Every tool file imports from here instead of closing over the same state.
"""

from __future__ import annotations

import difflib
import fnmatch
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from ..services.command_background import BackgroundCommandRequest, get_background_command_service
from ..services.command_execution import build_shell_command, run_shell_command

# ── default ignore set ──────────────────────────────────────────────

DEFAULT_IGNORED_DIR_NAMES = {
    ".git",
    ".venv",
    "__pycache__",
    "dist",
    "node_modules",
    "target",
}

# ── config accessors ────────────────────────────────────────────────


def current_runtime_config(store: Any) -> dict[str, Any]:
    return store.get_config({})["config"]


def current_command_policy(store: Any) -> dict[str, Any]:
    return current_runtime_config(store)["policy"]


def current_run_command_config(store: Any) -> dict[str, Any]:
    return current_runtime_config(store)["tools"]["runCommand"]


# ── search helpers ──────────────────────────────────────────────────


def split_search_terms(query: str) -> list[str]:
    return [term for term in re.findall(r"[\w.\-]+", query.lower()) if len(term) > 1]


# ── ignore / glob pattern helpers ──────────────────────────────────


def normalize_patterns(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        values = [value]
    else:
        try:
            values = list(value)
        except TypeError:
            values = [str(value)]
    return [str(item).strip() for item in values if str(item).strip()]


def expand_ignore_patterns(patterns: list[str]) -> list[str]:
    expanded: list[str] = []
    for pattern in patterns:
        expanded.append(pattern)
        if not any(token in pattern for token in "*?[]"):
            expanded.append(f"**/{pattern}")
            expanded.append(f"**/{pattern}/**")
    return list(dict.fromkeys(expanded))


_gitignore_cache: dict[str, list[str]] = {}


def load_gitignore_patterns(workspace_root: str) -> list[str]:
    if workspace_root in _gitignore_cache:
        return _gitignore_cache[workspace_root]
    gitignore_path = Path(workspace_root) / ".gitignore"
    patterns: list[str] = []
    if gitignore_path.is_file():
        try:
            for line in gitignore_path.read_text(encoding="utf-8", errors="replace").splitlines():
                stripped = line.rstrip()
                if stripped and not stripped.startswith("#"):
                    patterns.append(stripped)
        except OSError:
            pass
    _gitignore_cache[workspace_root] = patterns
    return patterns


def current_search_defaults(store: Any) -> tuple[list[str], list[str]]:
    config = store.get_config({})["config"]
    workspace_config = config.get("workspace") or {}
    search_config = config.get("search") or {}
    workspace_root = workspace_config.get("rootPath") or ""
    gitignore_patterns = load_gitignore_patterns(workspace_root) if workspace_root else []
    base_ignore_patterns = list(
        dict.fromkeys(
            [
                *DEFAULT_IGNORED_DIR_NAMES,
                *workspace_config.get("ignore", []),
                *search_config.get("ignore", []),
                *gitignore_patterns,
            ]
        )
    )
    base_glob_patterns = list(search_config.get("glob", []))
    return base_ignore_patterns, base_glob_patterns


def merged_ignore_patterns(store: Any, extra_patterns: Any = None) -> list[str]:
    base_ignore_patterns, _ = current_search_defaults(store)
    return expand_ignore_patterns([*base_ignore_patterns, *normalize_patterns(extra_patterns)])


def merged_glob_patterns(store: Any, extra_patterns: Any = None) -> list[str]:
    _, base_glob_patterns = current_search_defaults(store)
    return list(dict.fromkeys([*base_glob_patterns, *normalize_patterns(extra_patterns)]))


def matches_pattern(relative_path: str, part: str, pattern: str) -> bool:
    if fnmatch.fnmatch(relative_path, pattern):
        return True
    if fnmatch.fnmatch(part, pattern):
        return True
    return pattern == part


def matches_glob(relative_path: str, pattern: str) -> bool:
    if fnmatch.fnmatch(relative_path, pattern):
        return True
    if "**/" in pattern and fnmatch.fnmatch(relative_path, pattern.replace("**/", "")):
        return True
    return False


def is_ignored(
    path: Path,
    workspace_root: Path,
    store: Any,
    extra_patterns: Any = None,
) -> bool:
    try:
        relative_path = path.relative_to(workspace_root)
    except ValueError:
        return True

    relative_text = relative_path.as_posix()
    parts = relative_path.parts
    for pattern in merged_ignore_patterns(store, extra_patterns):
        if any(matches_pattern(relative_text, part, pattern) for part in parts):
            return True
    return False


# ── path helpers ────────────────────────────────────────────────────


def require_workspace_root(params: dict[str, Any]) -> Path:
    workspace_root = (
        params.get("workspaceRoot")
        or params.get("workspace_root")
        or params.get("rootPath")
    )
    if not workspace_root:
        raise ValueError("workspaceRoot is required")
    root = Path(workspace_root).resolve()
    if not root.exists() or not root.is_dir():
        raise ValueError(f"Workspace root does not exist: {workspace_root}")
    return root


def resolve_workspace_path(policy_guard: Any, workspace_root: Path, candidate_path: str | None) -> Path:
    relative_path = candidate_path or "."
    policy_guard.ensure_within_workspace(str(workspace_root), relative_path)
    return (workspace_root / relative_path).resolve()


def resolve_git_cwd(policy_guard: Any, workspace_root: Path, params: dict[str, Any]) -> Path:
    cwd_value = params.get("cwd") or "."
    cwd_path = resolve_workspace_path(policy_guard, workspace_root, cwd_value)
    if not cwd_path.is_dir():
        raise ValueError(f"cwd does not exist: {cwd_value}")
    return cwd_path


def resolve_git_pathspec(
    policy_guard: Any,
    workspace_root: Path,
    cwd: Path,
    candidate_path: str | None,
) -> str | None:
    if candidate_path is None or str(candidate_path).strip() == "":
        return None

    path = resolve_workspace_path(policy_guard, workspace_root, candidate_path)
    relative = os.path.relpath(path, start=cwd)
    if relative == ".":
        return "."
    return Path(relative).as_posix()


def to_relative_path(workspace_root: Path, path: Path) -> str:
    relative = path.relative_to(workspace_root)
    return "." if str(relative) == "." else relative.as_posix()


# ── directory walking / entry building ──────────────────────────────


def build_entry(workspace_root: Path, path: Path, depth: int) -> dict[str, Any]:
    stat = path.stat()
    entry_type = "directory" if path.is_dir() else "file"
    return {
        "name": path.name or workspace_root.name,
        "path": to_relative_path(workspace_root, path),
        "type": entry_type,
        "size": None if entry_type == "directory" else stat.st_size,
        "modifiedAt": int(stat.st_mtime),
        "depth": depth,
    }


def walk_directory(
    workspace_root: Path,
    base_path: Path,
    recursive: bool,
    max_depth: int,
    store: Any,
    ignore_patterns: Any = None,
) -> list[dict[str, Any]]:
    from collections import deque

    items: list[dict[str, Any]] = []
    queue: deque[tuple[Path, int]] = deque([(base_path, 0)])

    while queue:
        current_path, current_depth = queue.popleft()
        next_depth = current_depth + 1
        if next_depth > max_depth:
            continue

        children = sorted(
            current_path.iterdir(),
            key=lambda child: (not child.is_dir(), child.name.lower()),
        )
        for child in children:
            if is_ignored(child, workspace_root, store, ignore_patterns):
                continue
            items.append(build_entry(workspace_root, child, next_depth))
            if recursive and child.is_dir():
                queue.append((child, next_depth))

    return items


# ── search backends ─────────────────────────────────────────────────


def python_filename_search(
    workspace_root: Path,
    query: str,
    glob_patterns: list[str],
    max_results: int,
    store: Any,
    ignore_patterns: Any = None,
) -> list[dict[str, Any]]:
    search_terms = split_search_terms(query)
    if not search_terms:
        return []

    scored_matches: list[tuple[int, dict[str, Any]]] = []
    for path in workspace_root.rglob("*"):
        if not path.is_file():
            continue
        if is_ignored(path, workspace_root, store, ignore_patterns):
            continue
        relative_path = to_relative_path(workspace_root, path)
        if glob_patterns and not any(matches_glob(relative_path, pattern) for pattern in glob_patterns):
            continue
        normalized_name = path.name.lower()
        matched_terms = [term for term in search_terms if term in normalized_name]
        if not matched_terms:
            continue
        score = sum(len(term) for term in matched_terms)
        scored_matches.append(
            (
                score,
                {
                    "path": relative_path,
                    "name": path.name,
                    "type": "file",
                },
            )
        )

    scored_matches.sort(key=lambda item: (-item[0], item[1]["path"]))
    return [match for _, match in scored_matches[:max_results]]


def python_content_search(
    workspace_root: Path,
    query: str,
    glob_patterns: list[str],
    max_results: int,
    store: Any,
    ignore_patterns: Any = None,
) -> list[dict[str, Any]]:
    normalized_query = query.lower()
    matches: list[dict[str, Any]] = []
    for path in workspace_root.rglob("*"):
        if len(matches) >= max_results:
            break
        if not path.is_file():
            continue
        if is_ignored(path, workspace_root, store, ignore_patterns):
            continue
        relative_path = to_relative_path(workspace_root, path)
        if glob_patterns and not any(matches_glob(relative_path, pattern) for pattern in glob_patterns):
            continue
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        for line_number, line in enumerate(content.splitlines(), start=1):
            if normalized_query not in line.lower():
                continue
            column = line.lower().find(normalized_query) + 1
            matches.append(
                {
                    "path": relative_path,
                    "line": line_number,
                    "column": column,
                    "preview": line.strip(),
                }
            )
            if len(matches) >= max_results:
                break
    return matches


def rg_filename_search(
    workspace_root: Path,
    query: str,
    glob_patterns: list[str],
    max_results: int,
    store: Any,
    ignore_patterns: Any = None,
) -> list[dict[str, Any]]:
    search_terms = split_search_terms(query)
    if not search_terms:
        return []

    command = ["rg", "--files", str(workspace_root)]
    for pattern in glob_patterns:
        command.extend(["-g", pattern])
    for pattern in merged_ignore_patterns(store, ignore_patterns):
        command.extend(["-g", f"!{pattern}"])

    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=True,
    )

    scored_matches: list[tuple[int, dict[str, Any]]] = []
    for line in completed.stdout.splitlines():
        path = Path(line.strip())
        if not line.strip() or not path.is_file():
            continue
        if is_ignored(path.resolve(), workspace_root, store, ignore_patterns):
            continue
        normalized_name = path.name.lower()
        matched_terms = [term for term in search_terms if term in normalized_name]
        if not matched_terms:
            continue
        score = sum(len(term) for term in matched_terms)
        scored_matches.append(
            (
                score,
                {
                    "path": to_relative_path(workspace_root, path.resolve()),
                    "name": path.name,
                    "type": "file",
                },
            )
        )

    scored_matches.sort(key=lambda item: (-item[0], item[1]["path"]))
    return [match for _, match in scored_matches[:max_results]]


def rg_content_search(
    workspace_root: Path,
    query: str,
    glob_patterns: list[str],
    max_results: int,
    store: Any,
    ignore_patterns: Any = None,
) -> list[dict[str, Any]]:
    command = [
        "rg",
        "--json",
        "--line-number",
        "--column",
        "--smart-case",
        "--max-count",
        "1",
    ]
    for pattern in glob_patterns:
        command.extend(["-g", pattern])
    for pattern in merged_ignore_patterns(store, ignore_patterns):
        command.extend(["-g", f"!{pattern}"])
    command.extend([query, str(workspace_root)])

    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=True,
    )

    matches: list[dict[str, Any]] = []
    for line in completed.stdout.splitlines():
        payload = json.loads(line)
        if payload.get("type") != "match":
            continue
        data = payload["data"]
        path = Path(data["path"]["text"]).resolve()
        if is_ignored(path, workspace_root, store, ignore_patterns):
            continue
        line_text = data["lines"]["text"].rstrip("\r\n")
        submatches = data.get("submatches", [])
        column = submatches[0]["start"] + 1 if submatches else 1
        matches.append(
            {
                "path": to_relative_path(workspace_root, path),
                "line": data["line_number"],
                "column": column,
                "preview": line_text.strip(),
            }
        )
        if len(matches) >= max_results:
            break
    return matches


# ── run_command helpers ─────────────────────────────────────────────


def _run_command_allowed_roots(workspace_root: Path, config: dict[str, Any]) -> list[Path]:
    configured_roots = config.get("allowedCwdRoots") or []
    if isinstance(configured_roots, str):
        configured_roots = [configured_roots]
    roots: list[Path] = []
    for root_value in configured_roots:
        root_text = str(root_value).strip()
        if not root_text:
            continue
        root_path = Path(root_text)
        if not root_path.is_absolute():
            root_path = workspace_root / root_path
        roots.append(root_path.resolve())
    return roots or [workspace_root.resolve()]


def _format_cwd(workspace_root: Path, cwd_path: Path) -> str:
    try:
        return to_relative_path(workspace_root, cwd_path)
    except ValueError:
        return str(cwd_path)


def normalize_cwd(
    policy_guard: Any,
    workspace_root: Path,
    params: dict[str, Any],
    store: Any,
) -> str:
    config = current_run_command_config(store)
    cwd_value = params.get("cwd") or "."
    cwd_candidate = Path(str(cwd_value))
    cwd_path = cwd_candidate.resolve() if cwd_candidate.is_absolute() else (workspace_root / cwd_candidate).resolve()
    policy_guard.ensure_within_roots(cwd_path, _run_command_allowed_roots(workspace_root, config))
    if not cwd_path.is_dir():
        raise ValueError(f"cwd does not exist: {cwd_value}")
    return _format_cwd(workspace_root, cwd_path)


def normalize_shell(raw_shell: str | None, store: Any) -> str:
    shell_name = (raw_shell or current_run_command_config(store)["allowedShell"]).strip().lower()
    if shell_name not in {"powershell", "bash", "zsh"}:
        raise ValueError(f"Unsupported shell: {raw_shell}")
    return shell_name


def background_requested(params: dict[str, Any]) -> bool:
    raw_value = params.get("background")
    if raw_value is None:
        raw_value = params.get("backgroundJob")
    if raw_value is None:
        raw_value = params.get("runInBackground")
    if isinstance(raw_value, dict):
        enabled = raw_value.get("enabled")
        return True if enabled is None else bool(enabled)
    return bool(raw_value)


def run_shell(
    shell_name: str,
    command: str,
    cwd: Path,
    timeout_ms: int,
) -> tuple[str, str, int | None, str, int]:
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            build_shell_command(shell_name, command),
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_ms / 1000 if timeout_ms else None,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout if isinstance(exc.stdout, str) else (exc.stdout or "")
        stderr = exc.stderr if isinstance(exc.stderr, str) else (exc.stderr or "")
        duration_ms = int((time.perf_counter() - started) * 1000)
        return stdout, stderr, None, "timeout", duration_ms

    duration_ms = int((time.perf_counter() - started) * 1000)
    stdout = completed.stdout or ""
    stderr = completed.stderr or ""
    status = "completed" if completed.returncode == 0 else "failed"
    if completed.returncode < 0:
        status = "killed"
    elif completed.returncode != 0:
        status = "failed"
    return stdout, stderr, completed.returncode, status, duration_ms


def approval_request(
    *,
    task_id: str,
    command: str,
    cwd: str,
    shell: str,
    timeout_ms: int,
    workspace_root: str,
    background: bool,
) -> dict[str, Any]:
    return {
        "taskId": task_id,
        "command": command,
        "cwd": cwd,
        "shell": shell,
        "timeoutMs": timeout_ms,
        "workspaceRoot": workspace_root,
        "background": background,
    }


def approval_for_request(
    store: Any,
    task_id: str | None,
    request: dict[str, Any],
    approval_id: str | None,
) -> dict[str, Any] | None:
    if approval_id:
        approval = store.get_approval({"approvalId": approval_id})["approval"]
        if task_id and approval["taskId"] != task_id:
            raise ValueError("Approval does not belong to the active task")
        if approval["kind"] != "run_command":
            raise ValueError("Approval kind mismatch")
        stored_request = json.loads(approval["requestJson"])
        if stored_request != request:
            raise ValueError("Approval request does not match the command")
        return approval

    if task_id is None:
        return None
    return store.find_approval(
        task_id=task_id,
        kind="run_command",
        request=request,
    )


# ── patch helpers ───────────────────────────────────────────────────


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
    }
    if patch_text is not None:
        payload["patchText"] = patch_text
    if files is not None:
        payload["files"] = files
    payload["diffText"] = diff_text
    return payload


# ── git helpers ─────────────────────────────────────────────────────


def run_git_command(cwd: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        stderr = (completed.stderr or completed.stdout or "").strip()
        raise ValueError(stderr or "git command failed")
    return completed


def parse_git_status_header(line: str) -> tuple[str | None, str | None, int, int]:
    branch: str | None = None
    upstream: str | None = None
    ahead = 0
    behind = 0

    if not line.startswith("## "):
        return branch, upstream, ahead, behind

    summary = line[3:].strip()
    if summary.startswith("HEAD "):
        return None, None, ahead, behind
    if summary.startswith("No commits yet on "):
        return summary.removeprefix("No commits yet on ").strip() or None, None, ahead, behind
    if summary.startswith("Initial commit on "):
        return summary.removeprefix("Initial commit on ").strip() or None, None, ahead, behind

    tracking_part = ""
    if "..." in summary:
        branch_part, remainder = summary.split("...", 1)
        branch = branch_part.strip() or None
        if " [" in remainder:
            upstream_part, tracking_part = remainder.split(" [", 1)
            tracking_part = "[" + tracking_part
        else:
            upstream_part = remainder
        upstream = upstream_part.strip() or None
    elif " [" in summary:
        branch_part, tracking_part = summary.split(" [", 1)
        branch = branch_part.strip() or None
        tracking_part = "[" + tracking_part
    else:
        branch = summary or None

    for direction, value in re.findall(r"\b(ahead|behind) (\d+)\b", tracking_part):
        if direction == "ahead":
            ahead = int(value)
        else:
            behind = int(value)

    return branch, upstream, ahead, behind


def parse_name_status_line(line: str) -> dict[str, Any]:
    parts = line.split("\t")
    status = parts[0].strip()
    entry: dict[str, Any] = {"status": status}
    if len(parts) > 1:
        entry["path"] = parts[-1].strip()
    if len(parts) > 2:
        entry["originalPath"] = parts[1].strip()
    return entry
