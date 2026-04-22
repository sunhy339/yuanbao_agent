from __future__ import annotations

import fnmatch
import json
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
        return {
            "ok": True,
            "patch_id": "patch_placeholder",
            "files_changed": 0,
            "summary": "Patch execution is not implemented in the scaffold.",
            "dry_run": params.get("dry_run", False),
        }

    def git_status(_params: dict[str, Any]) -> dict[str, Any]:
        return {"branch": None, "changes": []}

    def git_diff(_params: dict[str, Any]) -> dict[str, Any]:
        return {"diff": ""}

    return {
        "list_dir": list_dir,
        "search_files": search_files,
        "read_file": read_file,
        "run_command": run_command,
        "apply_patch": apply_patch,
        "git_status": git_status,
        "git_diff": git_diff,
    }
