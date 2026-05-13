"""History/session-data section builders for ContextBuilder."""

from __future__ import annotations

import json
from typing import Any

from .token_budget import BudgetSection


class HistoryMixin:
    """Mixin that provides history and session-data budget sections.

    Expects ``self._store`` to be a ``SQLiteStore``-compatible instance.
    """

    def _history_sections(self, session: dict[str, Any]) -> list[BudgetSection]:
        sections: list[BudgetSection] = self._conversation_history_sections(session)

        tasks = self._recent_tasks(session["id"], limit=6)
        total_tasks = len(tasks)
        for index, task in enumerate(tasks):
            priority = 450 + (total_tasks - index)
            sections.append(
                BudgetSection(
                    name=f"task_history:{task['id']}",
                    text=self._task_summary(task),
                    priority=priority,
                    minimum_tokens=24 if index == 0 else 0,
                )
            )

        for patch in self._recent_patches(session["id"], limit=3):
            sections.append(
                BudgetSection(
                    name=f"patch_diff:{patch['id']}",
                    text=self._patch_summary(patch),
                    priority=250,
                    minimum_tokens=16,
                )
            )

        for command in self._recent_commands(session["id"], limit=3):
            sections.append(
                BudgetSection(
                    name=f"command_history:{command['id']}",
                    text=self._command_summary(command),
                    priority=350,
                    minimum_tokens=18,
                )
            )
        return sections

    def _conversation_history_sections(self, session: dict[str, Any]) -> list[BudgetSection]:
        sections: list[BudgetSection] = []
        if session.get("summary"):
            sections.append(
                BudgetSection(
                    name="session_summary",
                    text=f"Session summary:\n{session['summary']}",
                    priority=650,
                    minimum_tokens=32,
                )
            )

        recent_messages = self._recent_messages(session["id"], limit=8)
        if recent_messages:
            sections.append(
                BudgetSection(
                    name="recent_conversation",
                    text=self._conversation_summary(recent_messages),
                    priority=640,
                    minimum_tokens=32,
                )
            )
        return sections

    def _recent_messages(self, session_id: str, *, limit: int) -> list[dict[str, Any]]:
        rows = self._store._conn.execute(  # noqa: SLF001
            """
            SELECT *
            FROM messages
            WHERE session_id = ?
              AND role IN ('user', 'assistant')
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (session_id, limit),
        ).fetchall()
        return list(reversed([dict(row) for row in rows]))

    def _recent_tasks(self, session_id: str, *, limit: int) -> list[dict[str, Any]]:
        rows = self._store._conn.execute(  # noqa: SLF001
            """
            SELECT *
            FROM tasks
            WHERE session_id = ?
            ORDER BY updated_at DESC, created_at DESC
            LIMIT ?
            """,
            (session_id, limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def _recent_patches(self, session_id: str, *, limit: int) -> list[dict[str, Any]]:
        rows = self._store._conn.execute(  # noqa: SLF001
            """
            SELECT patches.*
            FROM patches
            JOIN tasks ON tasks.id = patches.task_id
            WHERE tasks.session_id = ?
            ORDER BY patches.updated_at DESC, patches.created_at DESC
            LIMIT ?
            """,
            (session_id, limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def _recent_commands(self, session_id: str, *, limit: int) -> list[dict[str, Any]]:
        rows = self._store._conn.execute(  # noqa: SLF001
            """
            SELECT command_logs.*
            FROM command_logs
            JOIN tasks ON tasks.id = command_logs.task_id
            WHERE tasks.session_id = ?
            ORDER BY command_logs.started_at DESC
            LIMIT ?
            """,
            (session_id, limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def _conversation_summary(self, messages: list[dict[str, Any]]) -> str:
        lines = ["Recent conversation:"]
        for message in messages:
            role = "User" if message.get("role") == "user" else "Assistant"
            lines.append(f"{role}: {self._single_line(message.get('content'), max_chars=900)}")
        return "\n".join(lines)

    def _task_summary(self, task: dict[str, Any]) -> str:
        lines = [
            f"Recent task event: task {task['status']}",
            f"- goal: {task['goal']}",
        ]
        acceptance_criteria = self._json_list(task.get("acceptance_criteria_json"))
        out_of_scope = self._json_list(task.get("out_of_scope_json"))
        changed_files = self._json_list(task.get("changed_files_json"))
        commands = self._json_list(task.get("commands_json"))
        verification = self._json_list(task.get("verification_json"))

        summary = task.get("summary") or task.get("result_json")
        if summary:
            lines.append(f"- result: {self._single_line(summary)}")
        if acceptance_criteria or out_of_scope:
            lines.append("Task focus:")
            if acceptance_criteria:
                lines.append(
                    "- acceptance: "
                    + "; ".join(self._single_line(item) for item in acceptance_criteria[:4])
                )
            if out_of_scope:
                lines.append(
                    "- out of scope: "
                    + "; ".join(self._single_line(item) for item in out_of_scope[:4])
                )
        if changed_files or commands or verification:
            lines.append("Task artifacts:")
            if changed_files:
                lines.append(
                    "- changed files: "
                    + ", ".join(
                        self._single_line(
                            f"{item.get('path')} ({item.get('status') or 'changed'})"
                        )
                        for item in changed_files[:8]
                        if isinstance(item, dict) and item.get("path")
                    )
                )
            if commands:
                lines.append(
                    "- commands: "
                    + "; ".join(
                        self._single_line(
                            f"{item.get('command')} -> {item.get('status') or 'recorded'}"
                            + (
                                f" exit {item.get('exitCode')}"
                                if item.get("exitCode") is not None
                                else ""
                            )
                        )
                        for item in commands[:5]
                        if isinstance(item, dict) and item.get("command")
                    )
                )
            if verification:
                lines.append(
                    "- verification: "
                    + "; ".join(
                        self._single_line(
                            f"{item.get('status') or 'recorded'}"
                            + (f" - {item.get('summary')}" if item.get("summary") else "")
                        )
                        for item in verification[:5]
                        if isinstance(item, dict)
                    )
                )
        if task.get("error_code"):
            lines.append(f"- error: {task['error_code']}")
        return "\n".join(lines)

    def _json_list(self, raw: Any) -> list[Any]:
        if not raw:
            return []
        try:
            parsed = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            return []
        return parsed if isinstance(parsed, list) else []

    def _single_line(self, value: Any, *, max_chars: int = 220) -> str:
        text = " ".join(str(value).split())
        if len(text) <= max_chars:
            return text
        return f"{text[: max_chars - 15].rstrip()} [truncated]"

    def _patch_summary(self, patch: dict[str, Any]) -> str:
        return "\n".join(
            [
                f"Recent patch diff: {patch['summary'] or patch['id']}",
                f"- status: {patch['status']}",
                f"- files changed: {patch['files_changed']}",
                "```diff",
                patch["diff_text"],
                "```",
            ]
        )

    def _command_summary(self, command: dict[str, Any]) -> str:
        return "\n".join(
            [
                f"Recent command event: command {command['status']}",
                f"- cwd: {command['cwd']}",
                f"- command: {command['command']}",
                f"- exit code: {command['exit_code']}",
            ]
        )
