from __future__ import annotations

import json
import re
import sqlite3
import time
import textwrap
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any
try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None  # type: ignore[assignment]

_README_CANDIDATES = ("README.md", "README.txt", "README", "readme.md")
_INSTRUCTION_CANDIDATES = ("AGENTS.md", ".cursorrules", ".windsurfrules", ".claude/CLAUDE.md")
_DEFAULT_TEST_COMMANDS = (
    "python -m pytest -q",
    "npm test",
    "cargo test",
)


def _normalize_bullets(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for raw in values:
        item = str(raw).strip()
        if not item:
            continue
        key = item.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _read_small_text_file(path: Path, *, max_chars: int = 4000) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
    normalized = text.strip()
    if len(normalized) > max_chars:
        return normalized[:max_chars].rstrip()
    return normalized


def _extract_readme_summary(workspace_root: Path) -> tuple[str | None, str | None]:
    for name in _README_CANDIDATES:
        path = workspace_root / name
        if not path.exists() or not path.is_file():
            continue
        text = _read_small_text_file(path, max_chars=3000)
        if not text:
            continue
        title: str | None = None
        summary: str | None = None
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if title is None and stripped.startswith("#"):
                title = stripped.lstrip("#").strip() or None
                continue
            if stripped.startswith(("-", "*", "#")):
                continue
            summary = stripped
            break
        return title, summary
    return None, None


def _detect_package_json_commands(path: Path) -> tuple[list[str], list[str]]:
    if not path.exists():
        return [], []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return [], []
    scripts = payload.get("scripts")
    if not isinstance(scripts, dict):
        return [], []
    conventions: list[str] = []
    commands: list[str] = []
    for key in ("dev", "build", "test", "lint"):
        value = scripts.get(key)
        if not isinstance(value, str) or not value.strip():
            continue
        commands.append(f"npm run {key}")
        conventions.append(f"`npm run {key}` -> {value.strip()}")
    return conventions, commands


def _detect_pyproject_commands(path: Path) -> tuple[list[str], list[str]]:
    if not path.exists() or tomllib is None:
        return [], []
    try:
        payload = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return [], []
    conventions: list[str] = []
    commands: list[str] = []
    project = payload.get("project")
    if isinstance(project, dict):
        name = project.get("name")
        if isinstance(name, str) and name.strip():
            conventions.append(f"Python project name: `{name.strip()}`")
    tool = payload.get("tool")
    if isinstance(tool, dict):
        if "pytest" in tool:
            conventions.append("Repository appears to use pytest for verification.")
            commands.append("python -m pytest -q")
        if "ruff" in tool:
            conventions.append("Ruff configuration detected.")
        if "black" in tool:
            conventions.append("Black formatting configuration detected.")
    return conventions, commands


def _detect_cargo_commands(path: Path) -> tuple[list[str], list[str]]:
    if not path.exists():
        return [], []
    text = _read_small_text_file(path, max_chars=2500)
    if not text:
        return [], []
    conventions = ["Rust workspace detected from `Cargo.toml`."]
    commands = ["cargo test", "cargo build"]
    match = re.search(r'(?m)^\s*name\s*=\s*"([^"]+)"', text)
    if match:
        conventions.append(f"Rust package name: `{match.group(1).strip()}`")
    return conventions, commands


def _detect_instruction_sources(workspace_root: Path) -> list[str]:
    sources: list[str] = []
    for relative in _INSTRUCTION_CANDIDATES:
        path = workspace_root / relative
        if path.exists() and path.is_file():
            sources.append(relative.replace("\\", "/"))
    return sources


def _build_workspace_memory_templates(workspace_root: Path, workspace_name: str) -> dict[str, str]:
    title, readme_summary = _extract_readme_summary(workspace_root)
    project_name = title or workspace_name or workspace_root.name
    conventions: list[str] = []
    suggested_commands: list[str] = []

    package_conventions, package_commands = _detect_package_json_commands(workspace_root / "package.json")
    conventions.extend(package_conventions)
    suggested_commands.extend(package_commands)

    pyproject_conventions, pyproject_commands = _detect_pyproject_commands(workspace_root / "pyproject.toml")
    conventions.extend(pyproject_conventions)
    suggested_commands.extend(pyproject_commands)

    cargo_conventions, cargo_commands = _detect_cargo_commands(workspace_root / "Cargo.toml")
    conventions.extend(cargo_conventions)
    suggested_commands.extend(cargo_commands)

    if (workspace_root / "pytest.ini").exists() or (workspace_root / "tests").exists():
        conventions.append("Test-oriented layout detected (`pytest.ini` or `tests/`).")
        suggested_commands.append("python -m pytest -q")

    instruction_sources = _detect_instruction_sources(workspace_root)
    suggestions = _normalize_bullets(suggested_commands)
    conventions = _normalize_bullets(conventions)

    summary = readme_summary or "Fill in the main product purpose and key workflows for this workspace."
    if len(summary) > 240:
        summary = summary[:237].rstrip() + "..."

    goals_lines = [
        f"- Project: `{project_name}`",
        f"- Summary: {summary}",
    ]
    if suggestions:
        goals_lines.append(f"- Suggested verification commands: {', '.join(f'`{cmd}`' for cmd in suggestions[:4])}")

    guardrail_lines = [
        "- Keep edits scoped to the active workspace and avoid changing files outside the requested task.",
        "- Prefer verifying changes with the repository's own test/build commands before declaring work complete.",
    ]
    if instruction_sources:
        guardrail_lines.append(
            "- Existing instruction sources detected: "
            + ", ".join(f"`{source}`" for source in instruction_sources)
        )

    ways_of_working = conventions or [
        "Record preferred build, test, and review conventions here after the first successful task run.",
    ]

    convention_lines = conventions or [
        "Add stable repository conventions here after confirming them from the workspace.",
    ]
    verified_lines = []
    if suggestions:
        verified_lines.append(
            "Candidate verification commands to confirm and keep updated:\n"
            + "\n".join(f"- `{cmd}`" for cmd in suggestions[:5])
        )
    else:
        verified_lines.append("- Add the main verification command(s) once they are confirmed.")
    open_questions = [
        "- Is there a canonical build/test command the team wants every agent session to reuse?",
        "- Are there workspace-specific guardrails or review rules that should be promoted into YUANBAO.md?",
    ]

    yuanbao = "\n".join(
        [
            f"# {project_name} Workspace Guide",
            "",
            "## Project Goals",
            *goals_lines,
            "",
            "## Guardrails",
            *guardrail_lines,
            "",
            "## Ways of Working",
            *[f"- {line}" for line in ways_of_working],
            "",
        ]
    )
    memory = "\n".join(
        [
            f"# {project_name} Memory",
            "",
            "## Conventions",
            *[f"- {line}" for line in convention_lines],
            "",
            "## Verified Patterns",
            *verified_lines,
            "",
            "## Open Questions",
            *open_questions,
            "",
        ]
    )
    memory_local = textwrap.dedent(
        f"""\
        # Local Memory Overrides

        ## Personal Notes
        - Keep machine-local or user-specific reminders for `{project_name}` here.

        ## Environment Details
        - Record local-only setup notes that should not be treated as shared project conventions.

        ## Private Verification Notes
        - Capture personal shortcuts, temporary local paths, or experimental commands that should stay local.
        """
    ).strip() + "\n"

    return {
        "YUANBAO.md": yuanbao,
        "MEMORY.md": memory,
        "MEMORY.local.md": memory_local,
    }


class SessionStoreMixin:
    def upsert_workspace(self, path: str) -> dict[str, Any]:
        root = str(Path(path))
        workspace_id = self.new_id("ws")
        now = self.now()
        self._conn.execute(
            """
            INSERT INTO workspaces (id, name, root_path, focus, summary, created_at, updated_at)
            VALUES (?, ?, ?, NULL, NULL, ?, ?)
            ON CONFLICT(root_path) DO UPDATE SET updated_at = excluded.updated_at
            """,
            (workspace_id, Path(root).name or root, root, now, now),
        )
        self._conn.commit()
        row = self._conn.execute(
            "SELECT * FROM workspaces WHERE root_path = ?",
            (root,),
        ).fetchone()
        return self._serialize_workspace(dict(row))

    def require_workspace(self, workspace_id: str) -> dict[str, Any]:
        row = self._conn.execute(
            "SELECT * FROM workspaces WHERE id = ?",
            (workspace_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"Workspace not found: {workspace_id}")
        return self._serialize_workspace(dict(row))

    def update_workspace_summary(self, workspace_id: str, summary: str | None) -> dict[str, Any]:
        now = self.now()
        self._conn.execute(
            """
            UPDATE workspaces
            SET summary = ?, updated_at = ?
            WHERE id = ?
            """,
            (summary, now, workspace_id),
        )
        self._conn.commit()
        return self.require_workspace(workspace_id)

    def update_workspace_focus(self, params: dict[str, Any]) -> dict[str, Any]:
        workspace_id = params.get("workspaceId") or params.get("workspace_id")
        if not isinstance(workspace_id, str) or not workspace_id.strip():
            raise ValueError("workspaceId is required")
        focus = params.get("focus")
        if focus is not None:
            focus = str(focus).strip() or None
        now = self.now()
        self._conn.execute(
            """
            UPDATE workspaces
            SET focus = ?, updated_at = ?
            WHERE id = ?
            """,
            (focus, now, workspace_id.strip()),
        )
        self._conn.commit()
        return {"workspace": self.require_workspace(workspace_id.strip())}

    def clear_workspace_memory(self, params: dict[str, Any]) -> dict[str, Any]:
        workspace_id = params.get("workspaceId") or params.get("workspace_id")
        if not isinstance(workspace_id, str) or not workspace_id.strip():
            raise ValueError("workspaceId is required")
        workspace = self.update_workspace_summary(workspace_id.strip(), None)
        return {"workspace": workspace}

    def init_workspace_memory(self, params: dict[str, Any]) -> dict[str, Any]:
        workspace_id = params.get("workspaceId") or params.get("workspace_id")
        if not isinstance(workspace_id, str) or not workspace_id.strip():
            raise ValueError("workspaceId is required")
        workspace = self.require_workspace(workspace_id.strip())
        root_path = workspace.get("rootPath")
        if not isinstance(root_path, str) or not root_path.strip():
            raise ValueError("Workspace rootPath is required")

        workspace_root = Path(root_path)
        workspace_root.mkdir(parents=True, exist_ok=True)
        templates = _build_workspace_memory_templates(
            workspace_root,
            str(workspace.get("name") or workspace_root.name),
        )

        created_files: list[str] = []
        existing_files: list[str] = []
        for filename, content in templates.items():
            target = workspace_root / filename
            if target.exists():
                existing_files.append(filename)
                continue
            target.write_text(content, encoding="utf-8")
            created_files.append(filename)

        return {
            "workspace": workspace,
            "createdFiles": created_files,
            "existingFiles": existing_files,
        }

    def create_session(self, workspace_id: str, title: str) -> dict[str, Any]:
        session_id = self.new_id("sess")
        now = self.now()
        self._conn.execute(
            """
            INSERT INTO sessions (id, workspace_id, title, status, summary, created_at, updated_at)
            VALUES (?, ?, ?, 'active', NULL, ?, ?)
            """,
            (session_id, workspace_id, title, now, now),
        )
        self._conn.commit()
        return self.require_session(session_id)

    def require_session(self, session_id: str) -> dict[str, Any]:
        row = self._conn.execute(
            """
            SELECT s.*, w.name AS workspace_name, w.root_path AS workspace_root
            FROM sessions s
            LEFT JOIN workspaces w ON w.id = s.workspace_id
            WHERE s.id = ?
            """,
            (session_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"Session not found: {session_id}")
        return self._serialize_session(dict(row))

    def get_session(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"session": self.require_session(params["sessionId"])}

    def list_sessions(self, _params: dict[str, Any]) -> dict[str, Any]:
        rows = self._conn.execute(
            """
            SELECT s.*, w.name AS workspace_name, w.root_path AS workspace_root
            FROM sessions s
            LEFT JOIN workspaces w ON w.id = s.workspace_id
            ORDER BY s.updated_at DESC
            """
        ).fetchall()
        return {"sessions": [self._serialize_session(dict(row)) for row in rows]}

    def create_message(
        self,
        *,
        session_id: str,
        role: str,
        content: str,
        task_id: str | None = None,
        client_message_id: str | None = None,
        kind: str | None = None,
        status: str | None = None,
        created_seq: int | None = None,
    ) -> dict[str, Any]:
        if role not in {"user", "assistant", "system", "tool"}:
            raise ValueError(f"Unsupported message role: {role}")
        message_id = self.new_id("msg")
        now = self.now()
        self._conn.execute(
            """
            INSERT INTO messages (id, session_id, task_id, role, content, created_at,
                                  client_message_id, kind, status, created_seq, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                message_id, session_id, task_id, role, content, now,
                client_message_id,
                kind or "normal",
                status or "completed",
                created_seq,
                now,
            ),
        )
        self._conn.execute(
            """
            UPDATE sessions
            SET updated_at = ?
            WHERE id = ?
            """,
            (now, session_id),
        )
        self._conn.commit()
        row = self._conn.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
        if row is None:
            raise ValueError(f"Message not found: {message_id}")
        return self._serialize_message(dict(row))

    def update_message(
        self,
        message_id: str,
        *,
        content: str | None = None,
        status: str | None = None,
        kind: str | None = None,
    ) -> dict[str, Any] | None:
        assignments: list[str] = ["updated_at = ?"]
        values: list[Any] = [self.now()]
        if content is not None:
            assignments.append("content = ?")
            values.append(content)
        if status is not None:
            assignments.append("status = ?")
            values.append(status)
        if kind is not None:
            assignments.append("kind = ?")
            values.append(kind)
        values.append(message_id)
        self._conn.execute(
            f"UPDATE messages SET {', '.join(assignments)} WHERE id = ?",
            values,
        )
        self._conn.commit()
        row = self._conn.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
        return self._serialize_message(dict(row)) if row else None

    def list_messages(self, params: dict[str, Any]) -> dict[str, Any]:
        session_id = self._require_non_empty(params, "sessionId")
        limit = int(params.get("limit") or 500)
        rows = self._conn.execute(
            """
            SELECT *
            FROM messages
            WHERE session_id = ?
            ORDER BY created_at ASC, id ASC
            LIMIT ?
            """,
            (session_id, max(1, min(limit, 1000))),
        ).fetchall()
        return {"messages": [self._serialize_message(dict(row)) for row in rows]}

    def list_messages_by_task(self, task_id: str) -> list[dict[str, Any]]:
        """Return all messages for a task, ordered by created_at."""
        rows = self._conn.execute(
            """
            SELECT * FROM messages
            WHERE task_id = ?
            ORDER BY created_at ASC, id ASC
            """,
            (task_id,),
        ).fetchall()
        return [self._serialize_message(dict(row)) for row in rows]

    # ── task_inbox ──────────────────────────────────────────────────────

    def create_inbox_entry(
        self,
        *,
        task_id: str,
        session_id: str,
        content: str,
        message_id: str | None = None,
    ) -> dict[str, Any]:
        entry_id = self.new_id("ibx")
        now = self.now()
        seq = self.next_seq()
        self._conn.execute(
            """
            INSERT INTO task_inbox (id, task_id, session_id, message_id, content, status, created_seq, created_at)
            VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)
            """,
            (entry_id, task_id, session_id, message_id, content, seq, now),
        )
        self._conn.commit()
        row = self._conn.execute("SELECT * FROM task_inbox WHERE id = ?", (entry_id,)).fetchone()
        return dict(row) if row else {}

    def get_pending_supplements(self, task_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            """
            SELECT * FROM task_inbox
            WHERE task_id = ? AND status = 'pending'
            ORDER BY created_at ASC, id ASC
            """,
            (task_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def mark_supplement_consumed(
        self,
        entry_id: str,
        *,
        consumed_by_turn_id: str | None = None,
    ) -> dict[str, Any]:
        now = self.now()
        self._conn.execute(
            """
            UPDATE task_inbox
            SET status = 'consumed', consumed_by_turn_id = ?, consumed_at = ?
            WHERE id = ?
            """,
            (consumed_by_turn_id, now, entry_id),
        )
        self._conn.commit()
        row = self._conn.execute("SELECT * FROM task_inbox WHERE id = ?", (entry_id,)).fetchone()
        return dict(row) if row else {}

    def list_task_inbox_items(self, task_id: str) -> list[dict[str, Any]]:
        """Return all inbox items for a task, ordered by created_seq then created_at."""
        rows = self._conn.execute(
            """
            SELECT * FROM task_inbox
            WHERE task_id = ?
            ORDER BY COALESCE(created_seq, 0) ASC, created_at ASC, id ASC
            """,
            (task_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def update_session(self, params: dict[str, Any]) -> dict[str, Any]:
        session_id = self._require_non_empty(params, "sessionId")
        now = self.now()
        updates: list[str] = []
        values: list[Any] = []
        if "title" in params:
            updates.append("title = ?")
            values.append(params["title"])
        if "status" in params:
            updates.append("status = ?")
            values.append(params["status"])
        if not updates:
            return {"session": self.require_session(session_id)}
        updates.append("updated_at = ?")
        values.append(now)
        values.append(session_id)
        self._conn.execute(
            f"UPDATE sessions SET {', '.join(updates)} WHERE id = ?",
            values,
        )
        self._conn.commit()
        return {"session": self.require_session(session_id)}

    def delete_session(self, params: dict[str, Any]) -> dict[str, Any]:
        session_id = self._require_non_empty(params, "sessionId")
        session = self.require_session(session_id)
        self._delete_session_related_rows(session_id)
        self._conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
        self._conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        self._conn.commit()
        self.maybe_auto_storage_cleanup(reason="session_delete")
        return {"session": session}

    def _delete_session_related_rows(self, session_id: str) -> None:
        task_rows = self._conn.execute(
            "SELECT id FROM tasks WHERE session_id = ?",
            (session_id,),
        ).fetchall()
        task_ids = [str(row["id"]) for row in task_rows]

        if task_ids:
            self._delete_task_related_rows(task_ids)

        self._conn.execute("DELETE FROM memory_recall_records WHERE session_id = ?", (session_id,))
        self._conn.execute("DELETE FROM session_rolling_summaries WHERE session_id = ?", (session_id,))
        self._conn.execute("DELETE FROM memory_entries WHERE session_id = ?", (session_id,))
        self._conn.execute("DELETE FROM task_inbox WHERE session_id = ?", (session_id,))
        self._conn.execute("DELETE FROM agent_messages WHERE task_id IN (SELECT id FROM collaboration_tasks WHERE session_id = ?)", (session_id,))
        self._conn.execute("DELETE FROM collaboration_tasks WHERE session_id = ?", (session_id,))
        self._conn.execute("DELETE FROM agent_workers WHERE current_task_id IS NULL")

    def _delete_task_related_rows(self, task_ids: list[str]) -> None:
        if not task_ids:
            return

        for task_id in task_ids:
            self._delete_command_artifacts_for_task(task_id)

        placeholders = ", ".join("?" for _ in task_ids)
        values: list[Any] = list(task_ids)

        self._conn.execute(f"DELETE FROM patches WHERE task_id IN ({placeholders})", values)
        self._conn.execute(f"DELETE FROM approvals WHERE task_id IN ({placeholders})", values)
        self._conn.execute(f"DELETE FROM pending_react_tasks WHERE task_id IN ({placeholders})", values)
        self._conn.execute(f"DELETE FROM pending_dag_tasks WHERE task_id IN ({placeholders})", values)
        self._conn.execute(f"DELETE FROM trace_events WHERE task_id IN ({placeholders})", values)
        self._conn.execute(f"DELETE FROM provider_turns WHERE task_id IN ({placeholders})", values)
        self._conn.execute(f"DELETE FROM context_snapshots WHERE task_id IN ({placeholders})", values)
        self._conn.execute(f"DELETE FROM command_logs WHERE task_id IN ({placeholders})", values)
        self._conn.execute(f"DELETE FROM task_metrics WHERE task_id IN ({placeholders})", values)
        self._conn.execute(f"DELETE FROM compaction_records WHERE task_id IN ({placeholders})", values)
        self._conn.execute(f"DELETE FROM proposal_records WHERE task_id IN ({placeholders})", values)
        self._conn.execute(f"DELETE FROM artifacts WHERE parent_task_id IN ({placeholders}) OR producer_task_id IN ({placeholders})", values + values)
        self._conn.execute(f"DELETE FROM hook_executions WHERE task_id IN ({placeholders})", values)
        self._conn.execute(f"DELETE FROM scope_conflict_checks WHERE task_id IN ({placeholders})", values)
        self._conn.execute(f"DELETE FROM replay_sessions WHERE source_task_id IN ({placeholders})", values)
        self._conn.execute(f"DELETE FROM task_worktrees WHERE task_id IN ({placeholders})", values)
        self._conn.execute(f"DELETE FROM messages WHERE task_id IN ({placeholders})", values)
        self._conn.execute(f"DELETE FROM task_inbox WHERE task_id IN ({placeholders})", values)
        self._conn.execute(f"DELETE FROM memory_recall_records WHERE task_id IN ({placeholders})", values)
        self._conn.execute(f"DELETE FROM collaboration_tasks WHERE parent_task_id IN ({placeholders})", values)
        self._conn.execute(f"DELETE FROM agent_messages WHERE task_id IN ({placeholders})", values)
        self._conn.execute(f"DELETE FROM tasks WHERE id IN ({placeholders})", values)

    def _delete_command_artifacts_for_task(self, task_id: str) -> None:
        rows = self._conn.execute(
            "SELECT stdout_path, stderr_path FROM command_logs WHERE task_id = ?",
            (task_id,),
        ).fetchall()
        for row in rows:
            for key in ("stdout_path", "stderr_path"):
                path = row[key]
                if not isinstance(path, str) or not path.strip():
                    continue
                try:
                    artifact_path = Path(path)
                    if artifact_path.exists():
                        artifact_path.unlink()
                except OSError:
                    continue

    def update_session_summary(self, session_id: str, summary: str | None) -> dict[str, Any]:
        now = self.now()
        self._conn.execute(
            """
            UPDATE sessions
            SET summary = ?, updated_at = ?
            WHERE id = ?
            """,
            (summary, now, session_id),
        )
        self._conn.commit()
        return self.require_session(session_id)

    # -- Rolling session summary --

    def get_rolling_summary(self, session_id: str) -> dict[str, Any] | None:
        """Get the current rolling summary for a session."""
        row = self._conn.execute(
            "SELECT * FROM session_rolling_summaries WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        if row is None:
            return None
        import json
        d = dict(row)
        d["covered_message_ids"] = json.loads(d.get("covered_message_ids") or "[]")
        return d

    def upsert_rolling_summary(
        self,
        session_id: str,
        summary: str,
        covered_message_ids: list[str],
        token_estimate: int,
    ) -> dict[str, Any]:
        """Insert or update the rolling summary for a session."""
        import json
        now = self.now()
        existing = self._conn.execute(
            "SELECT id FROM session_rolling_summaries WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        if existing:
            self._conn.execute(
                """
                UPDATE session_rolling_summaries
                SET summary = ?, covered_message_ids = ?, token_estimate = ?, updated_at = ?
                WHERE session_id = ?
                """,
                (
                    summary,
                    json.dumps(covered_message_ids, ensure_ascii=False),
                    token_estimate,
                    now,
                    session_id,
                ),
            )
        else:
            rid = self.new_id("rs")
            self._conn.execute(
                """
                INSERT INTO session_rolling_summaries
                    (id, session_id, summary, covered_message_ids, token_estimate, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    rid,
                    session_id,
                    summary,
                    json.dumps(covered_message_ids, ensure_ascii=False),
                    token_estimate,
                    now,
                    now,
                ),
            )
        self._conn.commit()
        return self.get_rolling_summary(session_id)  # type: ignore[return-value]

