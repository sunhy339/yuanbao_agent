from __future__ import annotations

import fnmatch
import re
from pathlib import Path
from typing import Any


class PolicyGuard:
    """Applies coarse safety checks before tools run."""

    def __init__(self, approval_mode: str = "on_write_or_command") -> None:
        self._approval_mode = approval_mode

    def ensure_within_workspace(self, workspace_root: str, candidate_path: str) -> None:
        root = Path(workspace_root).resolve()
        candidate = (root / candidate_path).resolve()
        if root not in candidate.parents and candidate != root:
            raise ValueError(f"Path escapes workspace: {candidate_path}")

    def ensure_within_roots(self, candidate_path: str | Path, allowed_roots: list[str | Path]) -> None:
        candidate = Path(candidate_path).resolve()
        roots = [Path(root).resolve() for root in allowed_roots]
        if not roots:
            raise ValueError("No allowed roots configured")
        for root in roots:
            if candidate == root or root in candidate.parents:
                return
        root_text = ", ".join(str(root) for root in roots)
        raise ValueError(f"cwd is outside allowed roots: {candidate} (allowed: {root_text})")

    def validate_command(self, command: str, run_command_config: dict[str, Any]) -> None:
        deny_match = self._first_command_match(command, run_command_config, "deniedCommands", "denylist")
        if deny_match is not None:
            raise ValueError(f"Command matches denied command policy: {deny_match}")

        allow_patterns = self._command_patterns(run_command_config, "allowedCommands", "allowlist")
        if allow_patterns and self._first_match(command, allow_patterns) is None:
            raise ValueError("Command is not allowed by command allowlist")

        blocked_match = self._first_blocked_pattern(command, run_command_config)
        if blocked_match is not None:
            raise ValueError(f"Blocked dangerous command pattern: {blocked_match}")

        dangerous_match = self._dangerous_command_match(command)
        if dangerous_match is not None:
            raise ValueError(f"Blocked dangerous command: {dangerous_match}")

    def requires_approval(self, tool_name: str, *, approval_mode: str | None = None) -> bool:
        mode = approval_mode or self._approval_mode
        if mode == "relaxed":
            return tool_name in {"apply_patch", "write_file"}
        if mode == "strict":
            return tool_name in {"apply_patch", "write_file", "run_command"}
        return tool_name in {"apply_patch", "write_file", "run_command"}

    def _command_patterns(self, config: dict[str, Any], *keys: str) -> list[str]:
        patterns: list[str] = []
        for key in keys:
            value = config.get(key)
            if value is None:
                continue
            if isinstance(value, str):
                candidates = [value]
            else:
                try:
                    candidates = list(value)
                except TypeError:
                    candidates = [str(value)]
            patterns.extend(str(candidate).strip() for candidate in candidates if str(candidate).strip())
        return patterns

    def _first_command_match(self, command: str, config: dict[str, Any], *keys: str) -> str | None:
        return self._first_match(command, self._command_patterns(config, *keys))

    def _first_match(self, command: str, patterns: list[str]) -> str | None:
        normalized_command = " ".join(command.casefold().split())
        executable = self._command_executable(normalized_command)
        for pattern in patterns:
            normalized_pattern = " ".join(pattern.casefold().split())
            if fnmatch.fnmatch(normalized_command, normalized_pattern):
                return pattern
            if executable and fnmatch.fnmatch(executable, normalized_pattern):
                return pattern
        return None

    def _first_blocked_pattern(self, command: str, config: dict[str, Any]) -> str | None:
        normalized_command = command.casefold()
        for pattern in self._command_patterns(config, "blockedPatterns"):
            normalized_pattern = pattern.casefold()
            if fnmatch.fnmatch(normalized_command, normalized_pattern) or normalized_pattern in normalized_command:
                return pattern
        return None

    def _command_executable(self, normalized_command: str) -> str:
        stripped = normalized_command.strip()
        if not stripped:
            return ""
        first = re.split(r"\s+|&&|\|\||;|\|", stripped, maxsplit=1)[0]
        return first.strip("\"'")

    def _dangerous_command_match(self, command: str) -> str | None:
        normalized = " ".join(command.casefold().split())
        dangerous_patterns = [
            (r"\brm\s+(-[^\s]*r[^\s]*f|-[^\s]*f[^\s]*r)\s+(/|\.{1,2})(\s|$)", "rm -rf root or parent"),
            (r"\bremove-item\b(?=.*-recurse\b)(?=.*-force\b).*(^|\s)(/|\\|[a-z]:\\?|\.{1,2})(\s|$)", "Remove-Item -Recurse -Force root or parent"),
            (r"\bdel\s+/s\b", "del /s"),
            (r"\bformat(\.com|\.exe)?\b", "format"),
            (r"\bshutdown(\.exe)?\b", "shutdown"),
        ]
        for pattern, label in dangerous_patterns:
            if re.search(pattern, normalized):
                return label
        return None
