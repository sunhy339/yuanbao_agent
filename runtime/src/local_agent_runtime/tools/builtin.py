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

DEFAULT_IGNORED_DIR_NAMES = {
    ".git",
    ".venv",
    "__pycache__",
    "dist",
    "node_modules",
    "target",
}


def build_builtin_tools(policy_guard: Any, store: Any) -> dict[str, Any]:
    """Return the initial tool map described in the MVP tech spec."""

    runtime_config = store.get_config({})["config"]
    command_policy = runtime_config["policy"]
    run_command_config = runtime_config["tools"]["runCommand"]

    def split_search_terms(query: str) -> list[str]:
        return [term for term in re.findall(r"[\w.\-]+", query.lower()) if len(term) > 1]

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

    def current_search_defaults() -> tuple[list[str], list[str]]:
        config = store.get_config({})["config"]
        workspace_config = config.get("workspace") or {}
        search_config = config.get("search") or {}
        base_ignore_patterns = list(
            dict.fromkeys(
                [
                    *DEFAULT_IGNORED_DIR_NAMES,
                    *workspace_config.get("ignore", []),
                    *search_config.get("ignore", []),
                ]
            )
        )
        base_glob_patterns = list(search_config.get("glob", []))
        return base_ignore_patterns, base_glob_patterns

    def merged_ignore_patterns(extra_patterns: Any = None) -> list[str]:
        base_ignore_patterns, _ = current_search_defaults()
        return expand_ignore_patterns([*base_ignore_patterns, *normalize_patterns(extra_patterns)])

    def merged_glob_patterns(extra_patterns: Any = None) -> list[str]:
        _, base_glob_patterns = current_search_defaults()
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

    def is_ignored(path: Path, workspace_root: Path, extra_patterns: Any = None) -> bool:
        try:
            relative_path = path.relative_to(workspace_root)
        except ValueError:
            return True

        relative_text = relative_path.as_posix()
        parts = relative_path.parts
        for pattern in merged_ignore_patterns(extra_patterns):
            if any(matches_pattern(relative_text, part, pattern) for part in parts):
                return True
        return False

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

    def resolve_workspace_path(workspace_root: Path, candidate_path: str | None) -> Path:
        relative_path = candidate_path or "."
        policy_guard.ensure_within_workspace(str(workspace_root), relative_path)
        return (workspace_root / relative_path).resolve()

    def resolve_git_cwd(workspace_root: Path, params: dict[str, Any]) -> Path:
        cwd_value = params.get("cwd") or "."
        cwd_path = resolve_workspace_path(workspace_root, cwd_value)
        if not cwd_path.is_dir():
            raise ValueError(f"cwd does not exist: {cwd_value}")
        return cwd_path

    def resolve_git_pathspec(workspace_root: Path, cwd: Path, candidate_path: str | None) -> str | None:
        if candidate_path is None or str(candidate_path).strip() == "":
            return None

        path = resolve_workspace_path(workspace_root, candidate_path)
        relative = os.path.relpath(path, start=cwd)
        if relative == ".":
            return "."
        return Path(relative).as_posix()

    def to_relative_path(workspace_root: Path, path: Path) -> str:
        relative = path.relative_to(workspace_root)
        return "." if str(relative) == "." else relative.as_posix()

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
        ignore_patterns: Any = None,
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        queue: list[tuple[Path, int]] = [(base_path, 0)]

        while queue:
            current_path, current_depth = queue.pop(0)
            next_depth = current_depth + 1
            if next_depth > max_depth:
                continue

            children = sorted(
                current_path.iterdir(),
                key=lambda child: (not child.is_dir(), child.name.lower()),
            )
            for child in children:
                if is_ignored(child, workspace_root, ignore_patterns):
                    continue
                items.append(build_entry(workspace_root, child, next_depth))
                if recursive and child.is_dir():
                    queue.append((child, next_depth))

        return items

    def python_filename_search(
        workspace_root: Path,
        query: str,
        glob_patterns: list[str],
        max_results: int,
        ignore_patterns: Any = None,
    ) -> list[dict[str, Any]]:
        search_terms = split_search_terms(query)
        if not search_terms:
            return []

        scored_matches: list[tuple[int, dict[str, Any]]] = []
        for path in workspace_root.rglob("*"):
            if not path.is_file():
                continue
            if is_ignored(path, workspace_root, ignore_patterns):
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
        ignore_patterns: Any = None,
    ) -> list[dict[str, Any]]:
        normalized_query = query.lower()
        matches: list[dict[str, Any]] = []
        for path in workspace_root.rglob("*"):
            if len(matches) >= max_results:
                break
            if not path.is_file():
                continue
            if is_ignored(path, workspace_root, ignore_patterns):
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
        ignore_patterns: Any = None,
    ) -> list[dict[str, Any]]:
        search_terms = split_search_terms(query)
        if not search_terms:
            return []

        command = ["rg", "--files", str(workspace_root)]
        for pattern in glob_patterns:
            command.extend(["-g", pattern])
        for pattern in merged_ignore_patterns(ignore_patterns):
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
            if is_ignored(path.resolve(), workspace_root, ignore_patterns):
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
        for pattern in merged_ignore_patterns(ignore_patterns):
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
            if is_ignored(path, workspace_root, ignore_patterns):
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

    def list_dir(params: dict[str, Any]) -> dict[str, Any]:
        workspace_root = require_workspace_root(params)
        recursive = bool(params.get("recursive", False))
        max_depth = int(params.get("max_depth", 1 if recursive else 1))
        max_depth = max(1, min(max_depth, 8))
        ignore_patterns = params.get("ignore")
        directory = resolve_workspace_path(workspace_root, params.get("path"))
        if not directory.is_dir():
            raise ValueError(f"Directory does not exist: {params.get('path', '.')}")

        return {
            "path": to_relative_path(workspace_root, directory),
            "recursive": recursive,
            "maxDepth": max_depth,
            "items": walk_directory(
                workspace_root=workspace_root,
                base_path=directory,
                recursive=recursive,
                max_depth=max_depth,
                ignore_patterns=ignore_patterns,
            ),
        }

    def search_files(params: dict[str, Any]) -> dict[str, Any]:
        workspace_root = require_workspace_root(params)
        query = str(params.get("query", "")).strip()
        if not query:
            raise ValueError("query is required")

        mode = params.get("mode", "content")
        if mode not in {"content", "filename"}:
            raise ValueError(f"Unsupported search mode: {mode}")

        glob_patterns = merged_glob_patterns(params.get("glob"))
        ignore_patterns = params.get("ignore")
        max_results = int(params.get("max_results", 20))
        max_results = max(1, min(max_results, 200))

        rg_available = shutil.which("rg") is not None
        backend = "python"

        if rg_available:
            try:
                if mode == "filename":
                    matches = rg_filename_search(
                        workspace_root,
                        query,
                        glob_patterns,
                        max_results,
                        ignore_patterns,
                    )
                else:
                    matches = rg_content_search(
                        workspace_root,
                        query,
                        glob_patterns,
                        max_results,
                        ignore_patterns,
                    )
                backend = "rg"
            except (
                subprocess.CalledProcessError,
                FileNotFoundError,
                PermissionError,
                json.JSONDecodeError,
            ):
                if mode == "filename":
                    matches = python_filename_search(
                        workspace_root,
                        query,
                        glob_patterns,
                        max_results,
                        ignore_patterns,
                    )
                else:
                    matches = python_content_search(
                        workspace_root,
                        query,
                        glob_patterns,
                        max_results,
                        ignore_patterns,
                    )
        else:
            if mode == "filename":
                matches = python_filename_search(
                    workspace_root,
                    query,
                    glob_patterns,
                    max_results,
                    ignore_patterns,
                )
            else:
                matches = python_content_search(
                    workspace_root,
                    query,
                    glob_patterns,
                    max_results,
                    ignore_patterns,
                )

        return {
            "query": query,
            "mode": mode,
            "backend": backend,
            "matches": matches,
            "total": len(matches),
        }

    def read_file(params: dict[str, Any]) -> dict[str, Any]:
        workspace_root = require_workspace_root(params)
        file_path = resolve_workspace_path(workspace_root, params["path"])
        if not file_path.is_file():
            raise ValueError(f"File does not exist: {params['path']}")
        if is_ignored(file_path, workspace_root, params.get("ignore")):
            raise ValueError(f"File is ignored by current search rules: {params['path']}")

        encoding = params.get("encoding", "utf-8")
        max_bytes = params.get("max_bytes")
        raw = file_path.read_bytes()
        truncated = False
        if max_bytes is not None:
            limit = max(1, int(max_bytes))
            truncated = len(raw) > limit
            raw = raw[:limit]

        return {
            "path": to_relative_path(workspace_root, file_path),
            "content": raw.decode(encoding, errors="replace"),
            "encoding": encoding,
            "truncated": truncated,
            "bytesRead": len(raw),
            "totalBytes": file_path.stat().st_size,
        }

    def _normalize_cwd(workspace_root: Path, params: dict[str, Any]) -> str:
        cwd_value = params.get("cwd") or "."
        cwd_path = resolve_workspace_path(workspace_root, cwd_value)
        if not cwd_path.is_dir():
            raise ValueError(f"cwd does not exist: {cwd_value}")
        return to_relative_path(workspace_root, cwd_path)

    def _normalize_shell(raw_shell: str | None) -> str:
        shell_name = (raw_shell or run_command_config["allowedShell"]).strip().lower()
        if shell_name not in {"powershell", "bash", "zsh"}:
            raise ValueError(f"Unsupported shell: {raw_shell}")
        return shell_name

    def _blocked(command: str) -> str | None:
        lowered = command.lower()
        for pattern in run_command_config["blockedPatterns"]:
            if pattern.lower() in lowered:
                return pattern
        return None

    def _build_shell_command(shell_name: str, command: str) -> list[str]:
        if shell_name == "bash":
            return ["bash", "-lc", command]
        if shell_name == "zsh":
            return ["zsh", "-lc", command]
        return ["powershell.exe", "-NoLogo", "-NoProfile", "-Command", command]

    def _normalize_patch_path(workspace_root: Path, candidate_path: str) -> tuple[str, Path]:
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

    def _strip_git_prefix(path_text: str) -> str:
        text = path_text.strip()
        if text in {"/dev/null", "dev/null"}:
            return "/dev/null"
        if text.startswith("a/") or text.startswith("b/"):
            return text[2:]
        return text

    def _parse_hunk_header(header: str) -> tuple[int, int, int, int]:
        match = re.match(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", header)
        if match is None:
            raise ValueError(f"Invalid hunk header: {header}")
        old_start = int(match.group(1))
        old_count = int(match.group(2) or "1")
        new_start = int(match.group(3))
        new_count = int(match.group(4) or "1")
        return old_start, old_count, new_start, new_count

    def _parse_unified_diff(diff_text: str) -> list[dict[str, Any]]:
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
                old_path = _strip_git_prefix(parts[2]) if len(parts) > 2 else ""
                new_path = _strip_git_prefix(parts[3]) if len(parts) > 3 else ""
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
                current["old_path"] = _strip_git_prefix(line[4:].strip().split("\t", 1)[0])
                index += 1
                if index >= len(lines) or not lines[index].startswith("+++ "):
                    raise ValueError("Invalid unified diff: missing +++ header")
                current["new_path"] = _strip_git_prefix(lines[index][4:].strip().split("\t", 1)[0])
                index += 1
                continue

            if line.startswith("@@ "):
                old_start, old_count, new_start, new_count = _parse_hunk_header(line)
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

    def _apply_unified_diff_to_file(
        workspace_root: Path,
        file_patch: dict[str, Any],
        *,
        dry_run: bool = False,
    ) -> tuple[str | None, bool]:
        old_path = str(file_patch.get("old_path") or "")
        new_path = str(file_patch.get("new_path") or "")
        hunks = list(file_patch.get("hunks") or [])

        if old_path == "/dev/null":
            relative_path, absolute_path = _normalize_patch_path(workspace_root, new_path)
            original_lines: list[str] = []
            is_new_file = True
        elif new_path == "/dev/null":
            relative_path, absolute_path = _normalize_patch_path(workspace_root, old_path)
            if not absolute_path.exists():
                raise ValueError(f"File does not exist: {relative_path}")
            original_lines = absolute_path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
            is_new_file = False
        else:
            relative_path, absolute_path = _normalize_patch_path(workspace_root, new_path or old_path)
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

    def _validate_patch_request(workspace_root: Path, diff_text: str) -> list[str]:
        applied_paths: list[str] = []
        parsed_patch = _parse_unified_diff(diff_text)
        for file_patch in parsed_patch:
            if not file_patch.get("hunks"):
                candidate = file_patch.get("new_path") or file_patch.get("old_path") or "unknown file"
                raise ValueError(f"Invalid unified diff: no hunks for {candidate}")
            relative_path, changed = _apply_unified_diff_to_file(workspace_root, file_patch, dry_run=True)
            if changed and relative_path and relative_path not in applied_paths:
                applied_paths.append(relative_path)
        if not applied_paths:
            raise ValueError("No file changes detected")
        return applied_paths

    def _build_patch_summary(paths: list[str]) -> str:
        if not paths:
            return "No file changes detected."
        if len(paths) == 1:
            return f"Update {paths[0]}"
        if len(paths) == 2:
            return f"Update {paths[0]} and {paths[1]}"
        return f"Update {paths[0]}, {paths[1]} and {len(paths) - 2} more file(s)"

    def _build_patch_from_files(
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
            relative_path, absolute_path = _normalize_patch_path(workspace_root, str(item.get("path") or ""))
            delete_file = bool(item.get("delete", False))
            if delete_file:
                original_text = absolute_path.read_text(encoding="utf-8", errors="replace") if absolute_path.exists() else ""
                new_text = ""
            else:
                if "content" not in item:
                    raise ValueError("files items require content unless delete is true")
                original_text = absolute_path.read_text(encoding="utf-8", errors="replace") if absolute_path.exists() else ""
                new_text = str(item.get("content") or "")

            if original_text == new_text and not delete_file:
                continue

            original_lines = original_text.splitlines()
            new_lines = new_text.splitlines()
            diff_chunks.extend(
                difflib.unified_diff(
                    original_lines,
                    new_lines,
                    fromfile=f"a/{relative_path}",
                    tofile="/dev/null" if delete_file else f"b/{relative_path}",
                    lineterm="",
                    n=3,
                )
            )
            changed_paths.append(relative_path)

        if not diff_chunks:
            raise ValueError("No file changes detected")

        return "\n".join(diff_chunks), changed_paths, len(changed_paths)

    def _build_patch_request(params: dict[str, Any], workspace_root: Path) -> dict[str, Any]:
        patch_text = params.get("patchText") or params.get("patch_text")
        files = params.get("files")
        if patch_text and files:
            raise ValueError("Provide either patchText or files, not both")
        if not patch_text and not files:
            raise ValueError("patchText or files is required")

        if patch_text:
            diff_text = str(patch_text)
            parsed = _parse_unified_diff(diff_text)
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

        diff_text, changed_paths, files_changed = _build_patch_from_files(workspace_root, files)
        return {
            "diffText": diff_text,
            "filesChanged": files_changed,
            "changedPaths": changed_paths,
            "patchMode": "files",
        }

    def _build_patch_request_payload(
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

    def _approval_request(
        *,
        task_id: str,
        command: str,
        cwd: str,
        shell: str,
        timeout_ms: int,
        workspace_root: str,
    ) -> dict[str, Any]:
        return {
            "taskId": task_id,
            "command": command,
            "cwd": cwd,
            "shell": shell,
            "timeoutMs": timeout_ms,
            "workspaceRoot": workspace_root,
        }

    def _approval_for_request(task_id: str | None, request: dict[str, Any], approval_id: str | None) -> dict[str, Any] | None:
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

    def _run_shell(shell_name: str, command: str, cwd: Path, timeout_ms: int) -> tuple[str, str, int | None, str, int]:
        started = time.perf_counter()
        try:
            completed = subprocess.run(
                _build_shell_command(shell_name, command),
                cwd=str(cwd),
                capture_output=True,
                text=True,
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

    def run_command(params: dict[str, Any]) -> dict[str, Any]:
        workspace_root = require_workspace_root(params)
        command = str(params.get("command", "")).strip()
        if not command:
            raise ValueError("command is required")

        task_id = str(params.get("taskId") or params.get("task_id") or "").strip()
        approval_id = str(params.get("approvalId") or params.get("approval_id") or "").strip() or None
        cwd_rel = _normalize_cwd(workspace_root, params)
        shell_name = _normalize_shell(params.get("shell"))
        timeout_ms = int(params.get("timeoutMs") or params.get("timeout_ms") or command_policy["commandTimeoutMs"])
        timeout_ms = max(1000, min(timeout_ms, 1_800_000))

        blocked = _blocked(command)
        if blocked:
            raise ValueError(f"Command contains a blocked pattern: {blocked}")

        cwd_abs = (workspace_root / cwd_rel).resolve()
        request_task_id = task_id or None
        if approval_id and not request_task_id:
            request_task_id = store.get_approval({"approvalId": approval_id})["approval"]["taskId"]
        request = _approval_request(
            task_id=request_task_id or "",
            command=command,
            cwd=cwd_rel,
            shell=shell_name,
            timeout_ms=timeout_ms,
            workspace_root=str(workspace_root),
        )
        existing_approval = _approval_for_request(request_task_id, request, approval_id)
        if existing_approval is not None:
            decision = existing_approval.get("decision")
            if decision == "approved":
                request_task_id = existing_approval["taskId"]
            else:
                return {
                    "status": "approval_required",
                    "approval": existing_approval,
                    "command": command,
                    "cwd": cwd_rel,
                    "shell": shell_name,
                    "timeoutMs": timeout_ms,
                }
        elif policy_guard.requires_approval("run_command", approval_mode=command_policy["approvalMode"]):
            if not request_task_id:
                raise ValueError("taskId is required when command approval is needed")
            approval = store.create_approval(
                task_id=request_task_id,
                kind="run_command",
                request=request,
            )
            return {
                "status": "approval_required",
                "approval": approval,
                "command": command,
                "cwd": cwd_rel,
                "shell": shell_name,
                "timeoutMs": timeout_ms,
            }

        if not request_task_id:
            raise ValueError("taskId is required for command execution")

        command_log = store.create_command_log(
            task_id=request_task_id,
            command=command,
            cwd=cwd_rel,
            shell=shell_name,
        )

        stdout = ""
        stderr = ""
        exit_code: int | None = None
        status = "completed"
        duration_ms = 0
        command_error: Exception | None = None
        try:
            stdout, stderr, exit_code, status, duration_ms = _run_shell(shell_name, command, cwd_abs, timeout_ms)
        except Exception as exc:  # noqa: BLE001
            command_error = exc
            stderr = str(exc)
            status = "failed"
        finally:
            finished_at = store.now()
            stdout_path = store.write_command_artifact(command_log["id"], "stdout", stdout)
            stderr_path = store.write_command_artifact(command_log["id"], "stderr", stderr)
            command_log = store.update_command_log(
                command_log["id"],
                status=status,
                exit_code=exit_code,
                stdout_path=stdout_path,
                stderr_path=stderr_path,
                finished_at=finished_at,
            )
        if command_error is not None:
            raise command_error

        return {
            "status": status,
            "commandLog": command_log,
            "stdout": stdout,
            "stderr": stderr,
            "exitCode": exit_code,
            "durationMs": duration_ms,
            "shell": shell_name,
            "cwd": cwd_rel,
        }

    def apply_patch(params: dict[str, Any]) -> dict[str, Any]:
        workspace_root = require_workspace_root(params)
        task_id = str(params.get("taskId") or params.get("task_id") or "").strip()
        if not task_id:
            raise ValueError("taskId is required")

        approval_id = str(params.get("approvalId") or params.get("approval_id") or "").strip() or None
        dry_run = bool(params.get("dry_run", params.get("dryRun", False)))

        try:
            patch_request = _build_patch_request(params, workspace_root)
        except Exception as exc:  # noqa: BLE001
            patch_text = str(params.get("patchText") or params.get("patch_text") or "")
            return {
                "status": "validation_failed",
                "ok": False,
                "error": str(exc),
                "summary": "Patch validation failed.",
                "filesChanged": 0,
                "diffText": patch_text,
                "dryRun": True,
            }
        patch_text = str(params.get("patchText") or params.get("patch_text") or patch_request["diffText"])
        files = params.get("files")
        request_payload = _build_patch_request_payload(
            task_id=task_id,
            workspace_root=workspace_root,
            diff_text=patch_request["diffText"],
            files_changed=patch_request["filesChanged"],
            dry_run=dry_run,
            patch_mode=patch_request["patchMode"],
            patch_text=patch_text if patch_request["patchMode"] == "patchText" else None,
            files=files if patch_request["patchMode"] == "files" else None,
        )
        summary = _build_patch_summary(patch_request["changedPaths"])

        try:
            validated_paths = _validate_patch_request(workspace_root, patch_request["diffText"])
        except Exception as exc:  # noqa: BLE001
            return {
                "status": "validation_failed",
                "ok": False,
                "error": str(exc),
                "summary": summary,
                "filesChanged": patch_request["filesChanged"],
                "changedPaths": patch_request["changedPaths"],
                "diffText": patch_request["diffText"],
                "dryRun": True,
            }

        approval: dict[str, Any] | None = None
        patch: dict[str, Any] | None = None

        if approval_id is not None:
            approval = store.get_approval({"approvalId": approval_id})["approval"]
            if approval["taskId"] != task_id:
                raise ValueError("Approval does not belong to the active task")
            if approval["kind"] != "apply_patch":
                raise ValueError("Approval kind mismatch")
            stored_request = json.loads(approval["requestJson"])
            if stored_request != request_payload:
                raise ValueError("Approval request does not match the patch")

            patch = store.find_patch(
                task_id=task_id,
                workspace_id=str(workspace_root),
                diff_text=patch_request["diffText"],
            )
            if patch is None:
                patch = store.create_patch(
                    task_id=task_id,
                    workspace_id=str(workspace_root),
                    summary=summary,
                    diff_text=patch_request["diffText"],
                    files_changed=patch_request["filesChanged"],
                    status="approved" if approval.get("decision") == "approved" else "proposed",
                )
            elif approval.get("decision") == "approved" and patch["status"] == "proposed":
                patch = store.update_patch(patch["id"], status="approved")

            if approval.get("decision") != "approved":
                return {
                    "status": "approval_required",
                    "approval": approval,
                    "patch": patch,
                    "patchId": patch["id"],
                    "summary": patch["summary"],
                    "filesChanged": patch["filesChanged"],
                    "diffText": patch["diffText"],
                    "dryRun": dry_run,
                }
        else:
            if not policy_guard.requires_approval("apply_patch", approval_mode=command_policy["approvalMode"]):
                patch = store.create_patch(
                    task_id=task_id,
                    workspace_id=str(workspace_root),
                    summary=summary,
                    diff_text=patch_request["diffText"],
                    files_changed=patch_request["filesChanged"],
                    status="approved",
                )
            else:
                patch = store.create_patch(
                    task_id=task_id,
                    workspace_id=str(workspace_root),
                    summary=summary,
                    diff_text=patch_request["diffText"],
                    files_changed=patch_request["filesChanged"],
                    status="proposed",
                )
                approval = store.create_approval(
                    task_id=task_id,
                    kind="apply_patch",
                    request=request_payload,
                )
                return {
                    "status": "approval_required",
                    "approval": approval,
                    "patch": patch,
                    "patchId": patch["id"],
                    "summary": patch["summary"],
                    "filesChanged": patch["filesChanged"],
                    "diffText": patch["diffText"],
                    "dryRun": dry_run,
                }

        if patch is None:
            raise ValueError("Failed to resolve patch state")
        if dry_run:
            return {
                "status": "dry_run",
                "patch": patch,
                "patchId": patch["id"],
                "summary": patch["summary"],
                "filesChanged": patch["filesChanged"],
                "diffText": patch["diffText"],
                "dryRun": True,
            }

        applied_paths: list[str] = []
        parsed_patch = _parse_unified_diff(patch["diffText"])
        for file_patch in parsed_patch:
            relative_path, changed = _apply_unified_diff_to_file(workspace_root, file_patch)
            if changed and relative_path not in applied_paths:
                applied_paths.append(relative_path)

        patch = store.update_patch(
            patch["id"],
            status="applied",
            summary=_build_patch_summary(applied_paths or validated_paths),
            diff_text=patch["diffText"],
            files_changed=len(applied_paths) or patch["filesChanged"],
        )
        return {
            "status": "applied",
            "patch": patch,
            "patchId": patch["id"],
            "summary": patch["summary"],
            "filesChanged": patch["filesChanged"],
            "diffText": patch["diffText"],
            "dryRun": False,
        }

    def _run_git_command(cwd: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
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

    def _parse_git_status_header(line: str) -> tuple[str | None, str | None, int, int]:
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

    def _parse_name_status_line(line: str) -> dict[str, Any]:
        parts = line.split("\t")
        status = parts[0].strip()
        entry: dict[str, Any] = {"status": status}
        if len(parts) > 1:
            entry["path"] = parts[-1].strip()
        if len(parts) > 2:
            entry["originalPath"] = parts[1].strip()
        return entry

    def git_status(_params: dict[str, Any]) -> dict[str, Any]:
        params = _params
        workspace_root = require_workspace_root(params)
        cwd = resolve_git_cwd(workspace_root, params)
        completed = _run_git_command(cwd, ["status", "--short", "--branch"])

        stdout_lines = [line for line in (completed.stdout or "").splitlines() if line.strip()]
        branch = None
        upstream = None
        ahead = 0
        behind = 0
        changes: list[dict[str, Any]] = []

        if stdout_lines:
            branch, upstream, ahead, behind = _parse_git_status_header(stdout_lines[0])
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
            "workspaceRoot": to_relative_path(workspace_root, workspace_root),
            "cwd": to_relative_path(workspace_root, cwd),
            "branch": branch,
            "upstream": upstream,
            "ahead": ahead,
            "behind": behind,
            "changes": changes,
        }

    def git_diff(params: dict[str, Any]) -> dict[str, Any]:
        workspace_root = require_workspace_root(params)
        cwd = resolve_git_cwd(workspace_root, params)
        staged = bool(params.get("staged", False))
        pathspec = resolve_git_pathspec(workspace_root, cwd, params.get("path"))

        git_args = ["diff"]
        if staged:
            git_args.append("--staged")
        if pathspec is not None:
            git_args.extend(["--", pathspec])

        diff_completed = _run_git_command(cwd, git_args)

        name_status_args = ["diff"]
        if staged:
            name_status_args.append("--staged")
        name_status_args.append("--name-status")
        if pathspec is not None:
            name_status_args.extend(["--", pathspec])
        files_completed = _run_git_command(cwd, name_status_args)

        files = [
            _parse_name_status_line(line)
            for line in (files_completed.stdout or "").splitlines()
            if line.strip()
        ]

        return {
            "workspaceRoot": str(workspace_root),
            "cwd": to_relative_path(workspace_root, cwd),
            "staged": staged,
            "path": pathspec,
            "files": files,
            "diff": diff_completed.stdout or "",
        }

    return {
        "list_dir": list_dir,
        "search_files": search_files,
        "read_file": read_file,
        "run_command": run_command,
        "apply_patch": apply_patch,
        "git_status": git_status,
        "git_diff": git_diff,
    }
