"""P9: Multi-agent write scope enforcement.

Enforces that child tasks only write within their declared write scopes.
"""

from __future__ import annotations

import re
from typing import Any

from ..policy.proposal_validator import (
    detect_patch_conflicts,
    validate_patch_in_scope,
    validate_reviewer_gate,
    validate_write_scope_overlap,
)


class WriteScopeEnforcer:
    """Check write operations against declared write scopes."""

    _PATH_TOKEN_RE = re.compile(
        r"""\.(?:py|js|ts|tsx|jsx|css|html|json|md|txt|toml|yaml|yml|ini|cfg)$""",
        re.IGNORECASE,
    )
    _TOKEN_RE = re.compile(r'''"[^"]+"|'[^']+'|[^\s,]+''')

    def __init__(self, store: Any) -> None:
        self._store = store

    def get_task_write_scope(self, task_id: str) -> list[str]:
        """Return the write scope for a collaboration task, empty if unrestricted."""
        task = self._resolve_collaboration_task(task_id)
        if task is None:
            return []
        return self._scope_from_task(task)

    def _resolve_collaboration_task(self, task_id: str) -> dict[str, Any] | None:
        try:
            return self._store.require_collaboration_task(task_id)
        except Exception:
            pass

        try:
            runtime_task = self._store.get_task({"taskId": task_id}).get("task", {})
        except Exception:
            return None

        routing = runtime_task.get("routing") or {}
        if not isinstance(routing, dict):
            return None
        collaboration_task_id = (
            routing.get("childCollaborationTaskId")
            or routing.get("collaborationTaskId")
            or routing.get("child_task_id")
            or routing.get("collaboration_task_id")
        )
        if not isinstance(collaboration_task_id, str) or not collaboration_task_id.strip():
            return None
        try:
            return self._store.require_collaboration_task(collaboration_task_id)
        except Exception:
            return None

    def _scope_from_task(self, task: dict[str, Any]) -> list[str]:
        metadata = task.get("metadata") or {}
        # Check top-level writeScope (set by worker_runner from profile.ownedScope)
        write_scope = metadata.get("writeScope")
        if isinstance(write_scope, list):
            return [str(s) for s in write_scope if self._looks_like_filesystem_scope(s)]
        if isinstance(write_scope, str):
            return [write_scope] if self._looks_like_filesystem_scope(write_scope) else []
        # Fallback: check profile.ownedScope
        profile = metadata.get("profile")
        if isinstance(profile, dict):
            owned = profile.get("ownedScope")
            if isinstance(owned, list):
                return [str(s) for s in owned if self._looks_like_filesystem_scope(s)]
            if isinstance(owned, str):
                return [owned] if self._looks_like_filesystem_scope(owned) else []
        return []

    def check_patch_in_scope(
        self,
        task_id: str,
        target_path: str,
    ) -> list[str]:
        """Validate that a patch target is within the task's write scope.

        Returns a list of rejection reasons. Empty means allowed.
        """
        scope = self.get_task_write_scope(task_id)
        if not scope:
            # No scope declared — unrestricted
            return []
        return validate_patch_in_scope(
            {"targetPath": target_path, "path": target_path},
            allowed_scopes=scope,
        )

    def check_command_allowed(
        self,
        task_id: str,
        command_scope: str | None = None,
        command: str | None = None,
    ) -> list[str]:
        """Validate that a run_command is allowed for the task.

        Tasks with write scopes require the command target to match.
        Without a scope, run_command requires explicit opt-in via metadata.
        """
        scope = self.get_task_write_scope(task_id)
        if not scope and command_scope is None:
            # No scope restriction, no target — allow
            return []
        command_targets = self._command_scope_targets(command)
        if scope and command_targets:
            command_reasons = [
                reason
                for target in command_targets
                for reason in validate_patch_in_scope(
                    {"targetPath": target, "path": target},
                    allowed_scopes=scope,
                )
            ]
            if command_reasons:
                return command_reasons
            return []
        if command_scope and scope:
            return validate_patch_in_scope(
                {"targetPath": command_scope, "path": command_scope},
                allowed_scopes=scope,
            )
        if scope and command_scope is None:
            return ["command scope is required for tasks with declared write scopes"]
        return []

    def _command_scope_targets(self, command: str | None) -> list[str]:
        text = str(command or "").strip()
        if not text:
            return []
        normalized = text.replace("\\", "/")
        literal_path_targets = self._literal_path_targets(normalized)
        if literal_path_targets:
            return literal_path_targets
        if not self._command_looks_target_scoped(normalized):
            return []
        explicit_targets = self._explicit_path_targets(normalized)
        if explicit_targets:
            return explicit_targets
        return []

    def _command_looks_target_scoped(self, normalized_command: str) -> bool:
        tokens = self._command_tokens(normalized_command)
        if not tokens:
            return False
        command_tokens = self._meaningful_command_tokens(tokens)
        if not command_tokens:
            return False
        lowered = [token.casefold() for token in command_tokens]
        executable = self._command_executable_name(command_tokens[0])
        if executable in {"get-childitem", "ls", "dir", "get-content", "cat", "type", "rg", "ripgrep", "findstr", "pytest"}:
            return True
        if executable == "node":
            return "--check" in lowered[1:]
        if executable in {"python", "py"} and len(lowered) >= 3 and lowered[1] == "-m":
            return lowered[2] in {"pytest", "py_compile", "compileall"}
        return False

    def _explicit_path_targets(self, normalized_command: str) -> list[str]:
        targets: list[str] = []
        for token in self._command_tokens(normalized_command):
            candidate = token.strip().strip("\"'").rstrip(",")
            if not self._looks_like_path_target(candidate):
                continue
            if candidate not in targets:
                targets.append(candidate)
        return targets

    def _literal_path_targets(self, normalized_command: str) -> list[str]:
        match = re.search(
            r"""(?:^|\s)-literalpath\s+(?P<targets>.+?)(?:\s*(?:\||;|&&|\|\|)\s*.*)?$""",
            normalized_command,
            re.IGNORECASE,
        )
        if not match:
            return []
        targets: list[str] = []
        for token in self._command_tokens(match.group("targets")):
            candidate = token.strip().strip("\"'").rstrip(",")
            if not self._looks_like_path_target(candidate):
                continue
            if candidate not in targets:
                targets.append(candidate)
        return targets

    def _command_tokens(self, text: str) -> list[str]:
        return [token for token in self._TOKEN_RE.findall(text) if token]

    def _meaningful_command_tokens(self, tokens: list[str]) -> list[str]:
        meaningful = [token for token in tokens if token not in {"&", ";"}]
        return meaningful

    def _command_executable_name(self, token: str) -> str:
        candidate = token.strip().strip("\"'").replace("\\", "/").rstrip("/")
        if not candidate:
            return ""
        leaf = candidate.rsplit("/", 1)[-1]
        stem = leaf[:-4] if leaf.lower().endswith(".exe") else leaf
        return stem.casefold()

    def _looks_like_path_target(self, candidate: str) -> bool:
        normalized = candidate.strip().replace("\\", "/")
        if not normalized or normalized in {".", ".."}:
            return False
        if normalized.startswith("-") or "://" in normalized:
            return False
        if normalized == "tests" or normalized.startswith("tests/"):
            return True
        if normalized.endswith("/"):
            return True
        return bool(self._PATH_TOKEN_RE.search(normalized))

    def _looks_like_filesystem_scope(self, candidate: object) -> bool:
        normalized = str(candidate or "").strip().replace("\\", "/")
        if not normalized:
            return False
        if normalized == ".":
            return True
        if normalized.startswith("-") or "://" in normalized:
            return False
        if normalized == "tests" or normalized.startswith("tests/"):
            return True
        if normalized.endswith("/"):
            return True
        if "/" in normalized:
            return True
        return bool(self._PATH_TOKEN_RE.search(normalized))

    def check_overlap_before_dispatch(
        self,
        subtasks: list[dict[str, Any]],
    ) -> list[str]:
        """Detect overlapping write scopes before dispatching child tasks."""
        return validate_write_scope_overlap(subtasks)

    def check_patch_conflicts(
        self,
        patches: list[dict[str, Any]],
    ) -> list[str]:
        """Detect conflicting patch targets across tasks."""
        return detect_patch_conflicts(patches)

    def check_reviewer_gate(self, payload: dict[str, Any]) -> list[str]:
        """Validate reviewer gate — reject merge when review is negative."""
        return validate_reviewer_gate(payload)
