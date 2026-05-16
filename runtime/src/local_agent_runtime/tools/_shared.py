"""Shared helpers extracted from the old builtin.py closure.

Every tool file imports from here instead of closing over the same state.
"""

from __future__ import annotations

import fnmatch
import json
import locale
import os
import re
import shlex
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from ..services.command_background import BackgroundCommandRequest, get_background_command_service
from ..services.command_execution import build_shell_command, run_shell_command
from ._patch import (
    apply_unified_diff_to_file,
    build_patch_from_files,
    build_patch_request,
    build_patch_request_payload,
    build_patch_summary,
    normalize_patch_path,
    parse_hunk_header,
    parse_unified_diff,
    strip_git_prefix,
    validate_patch_request,
)

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


_gitignore_cache: dict[str, tuple[float, list[str]]] = {}
_GITIGNORE_CACHE_TTL = 300.0  # seconds


def load_gitignore_patterns(workspace_root: str) -> list[str]:
    import time

    now = time.monotonic()
    cached = _gitignore_cache.get(workspace_root)
    if cached is not None:
        cached_time, cached_patterns = cached
        if now - cached_time < _GITIGNORE_CACHE_TTL:
            return cached_patterns
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
    _gitignore_cache[workspace_root] = (now, patterns)
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
    *,
    _expanded_patterns: list[str] | None = None,
) -> bool:
    try:
        relative_path = path.relative_to(workspace_root)
    except ValueError:
        return True

    relative_text = relative_path.as_posix()
    parts = relative_path.parts
    patterns = _expanded_patterns if _expanded_patterns is not None else merged_ignore_patterns(store, extra_patterns)
    for pattern in patterns:
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
    max_items: int = 2000,
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
            if len(items) >= max_items:
                return items
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

    expanded = merged_ignore_patterns(store, ignore_patterns)
    scored_matches: list[tuple[int, dict[str, Any]]] = []
    overfetch = max_results * 5
    for path in workspace_root.rglob("*"):
        if not path.is_file():
            continue
        if is_ignored(path, workspace_root, store, ignore_patterns, _expanded_patterns=expanded):
            continue
        normalized_name = path.name.lower()
        matched_terms = [term for term in search_terms if term in normalized_name]
        if not matched_terms:
            continue
        relative_path = to_relative_path(workspace_root, path)
        if glob_patterns and not any(matches_glob(relative_path, pattern) for pattern in glob_patterns):
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
        if len(scored_matches) >= overfetch:
            break

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
    expanded = merged_ignore_patterns(store, ignore_patterns)
    for path in workspace_root.rglob("*"):
        if len(matches) >= max_results:
            break
        if not path.is_file():
            continue
        if is_ignored(path, workspace_root, store, ignore_patterns, _expanded_patterns=expanded):
            continue
        relative_path = to_relative_path(workspace_root, path)
        if glob_patterns and not any(matches_glob(relative_path, pattern) for pattern in glob_patterns):
            continue
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        for line_number, line in enumerate(content.splitlines(), start=1):
            lowered = line.lower()
            if normalized_query not in lowered:
                continue
            column = lowered.find(normalized_query) + 1
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
        encoding="utf-8",
        errors="replace",
    )
    if completed.returncode not in (0, 1):
        raise subprocess.CalledProcessError(completed.returncode, command, completed.stdout, completed.stderr)

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
        encoding="utf-8",
        errors="replace",
    )
    if completed.returncode not in (0, 1):
        raise subprocess.CalledProcessError(completed.returncode, command, completed.stdout, completed.stderr)

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


_FILE_DUMP_COMMAND_RE = re.compile(
    r"""^\s*(?P<cmd>type|cat|gc|get-content)(?:\s+-Raw)?(?:\s+-Encoding\s+\S+)?\s+(?P<path>"[^"]+"|'[^']+'|[^\s|;&<>]+)\s*$""",
    re.IGNORECASE,
)
_NATIVE_EXE_COMMAND_RE = re.compile(
    r"""^\s*&?\s*(?:"(?P<quoted>[^"]+\.exe)"|'(?P<single>[^']+\.exe)'|(?P<bare>[A-Za-z]:\\[^\s]+\.exe))(?P<args>.*)$""",
    re.IGNORECASE,
)
_POWERSHELL_INVOKABLE_SUFFIXES = (".exe", ".cmd", ".bat", ".ps1")


def _decode_command_bytes(value: bytes | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    candidates = [
        "utf-8-sig",
        "utf-8",
        locale.getpreferredencoding(False),
        "cp936",
        "gbk",
        "cp950",
        "big5",
    ]
    seen: set[str] = set()
    for encoding in candidates:
        normalized = encoding.lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        try:
            return value.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return value.decode("utf-8", errors="replace")


def _strip_command_path_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _run_simple_file_dump(command: str, cwd: Path) -> str | None:
    match = _FILE_DUMP_COMMAND_RE.match(command)
    if not match:
        return None
    raw_path = _strip_command_path_quotes(match.group("path"))
    file_path = Path(raw_path)
    if not file_path.is_absolute():
        file_path = cwd / file_path
    try:
        resolved = file_path.resolve()
    except OSError:
        return None
    if not resolved.is_file():
        return None
    return _decode_command_bytes(resolved.read_bytes())


def _normalize_powershell_invocation(command: str) -> str:
    stripped = command.lstrip()
    leading = command[: len(command) - len(stripped)]
    if not stripped or stripped[0] not in {"'", '"'}:
        return command
    quote = stripped[0]
    end = stripped.find(quote, 1)
    if end <= 0:
        return command
    target = stripped[1:end].lower()
    if target.endswith(_POWERSHELL_INVOKABLE_SUFFIXES):
        return f"{leading}& {stripped}"
    return command


def _run_simple_native_exe(command: str, cwd: Path, timeout_ms: int) -> tuple[str, str, int | None, str, int] | None:
    match = _NATIVE_EXE_COMMAND_RE.match(command)
    if not match:
        return None
    executable = match.group("quoted") or match.group("single") or match.group("bare")
    if not executable:
        return None
    args_text = (match.group("args") or "").strip()
    try:
        args = shlex.split(args_text, posix=True) if args_text else []
    except ValueError:
        return None
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            [executable, *args],
            cwd=str(cwd),
            capture_output=True,
            text=False,
            timeout=timeout_ms / 1000 if timeout_ms else None,
            check=False,
        )
    except (OSError, ValueError):
        return None
    except subprocess.TimeoutExpired as exc:
        stdout = _decode_command_bytes(exc.stdout)
        stderr = _decode_command_bytes(exc.stderr)
        duration_ms = int((time.perf_counter() - started) * 1000)
        return stdout, stderr, None, "timeout", duration_ms

    duration_ms = int((time.perf_counter() - started) * 1000)
    stdout = _decode_command_bytes(completed.stdout)
    stderr = _decode_command_bytes(completed.stderr)
    status = "completed" if completed.returncode == 0 else "failed"
    if completed.returncode < 0:
        status = "killed"
    return stdout, stderr, completed.returncode, status, duration_ms


def run_shell(
    shell_name: str,
    command: str,
    cwd: Path,
    timeout_ms: int,
) -> tuple[str, str, int | None, str, int]:
    started = time.perf_counter()
    if shell_name == "powershell":
        dumped = _run_simple_file_dump(command, cwd)
        if dumped is not None:
            duration_ms = int((time.perf_counter() - started) * 1000)
            return dumped, "", 0, "completed", duration_ms
        command = _normalize_powershell_invocation(command)
        native_result = _run_simple_native_exe(command, cwd, timeout_ms)
        if native_result is not None:
            return native_result

    try:
        completed = subprocess.run(
            build_shell_command(shell_name, command),
            cwd=str(cwd),
            capture_output=True,
            text=False,
            timeout=timeout_ms / 1000 if timeout_ms else None,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = _decode_command_bytes(exc.stdout)
        stderr = _decode_command_bytes(exc.stderr)
        duration_ms = int((time.perf_counter() - started) * 1000)
        return stdout, stderr, None, "timeout", duration_ms

    duration_ms = int((time.perf_counter() - started) * 1000)
    stdout = _decode_command_bytes(completed.stdout)
    stderr = _decode_command_bytes(completed.stderr)
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


# ── git helpers ─────────────────────────────────────────────────────


def run_git_command(cwd: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
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
