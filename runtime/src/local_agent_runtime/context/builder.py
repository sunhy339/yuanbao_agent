from __future__ import annotations

import subprocess
from pathlib import Path
import re
from typing import Any

from .token_budget import BudgetSection, TokenBudget, estimate_tokens


COMMON_GOAL_TERMS = {
    "a",
    "an",
    "and",
    "build",
    "code",
    "current",
    "fix",
    "for",
    "help",
    "me",
    "please",
    "project",
    "repo",
    "repository",
    "the",
    "this",
    "with",
}

DEFAULT_MAX_CONTEXT_TOKENS = 8000

DEFAULT_TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "list_dir",
        "description": "List files and directories inside the active workspace.",
        "input_schema": {
            "type": "object",
            "properties": {
                "workspaceRoot": {"type": "string"},
                "path": {"type": "string"},
                "recursive": {"type": "boolean"},
                "max_depth": {"type": "integer"},
                "ignore": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["workspaceRoot"],
        },
    },
    {
        "name": "search_files",
        "description": "Search workspace files by content or filename.",
        "input_schema": {
            "type": "object",
            "properties": {
                "workspaceRoot": {"type": "string"},
                "query": {"type": "string"},
                "mode": {"type": "string", "enum": ["content", "filename"]},
                "glob": {"type": "array", "items": {"type": "string"}},
                "ignore": {"type": "array", "items": {"type": "string"}},
                "max_results": {"type": "integer"},
            },
            "required": ["workspaceRoot", "query"],
        },
    },
    {
        "name": "read_file",
        "description": "Read a file inside the active workspace.",
        "input_schema": {
            "type": "object",
            "properties": {
                "workspaceRoot": {"type": "string"},
                "path": {"type": "string"},
                "encoding": {"type": "string"},
                "max_bytes": {"type": "integer"},
            },
            "required": ["workspaceRoot", "path"],
        },
    },
    {
        "name": "run_command",
        "description": "Request approval, then run a shell command inside the workspace.",
        "input_schema": {
            "type": "object",
            "properties": {
                "workspaceRoot": {"type": "string"},
                "cwd": {"type": "string"},
                "command": {"type": "string"},
                "shell": {"type": "string"},
                "timeoutMs": {"type": "integer"},
            },
            "required": ["workspaceRoot", "command"],
        },
    },
    {
        "name": "apply_patch",
        "description": "Request approval, then apply a unified diff or file patch inside the workspace.",
        "input_schema": {
            "type": "object",
            "properties": {
                "workspaceRoot": {"type": "string"},
                "patchText": {"type": "string"},
                "files": {"type": "array"},
                "dry_run": {"type": "boolean"},
            },
            "required": ["workspaceRoot"],
        },
    },
    {
        "name": "git_status",
        "description": "Read git branch and working tree status for the workspace.",
        "input_schema": {
            "type": "object",
            "properties": {
                "workspaceRoot": {"type": "string"},
                "cwd": {"type": "string"},
            },
            "required": ["workspaceRoot"],
        },
    },
    {
        "name": "git_diff",
        "description": "Read git diff for the workspace or a path inside it.",
        "input_schema": {
            "type": "object",
            "properties": {
                "workspaceRoot": {"type": "string"},
                "cwd": {"type": "string"},
                "path": {"type": "string"},
                "staged": {"type": "boolean"},
            },
            "required": ["workspaceRoot"],
        },
    },
]


class ContextBuilder:
    """Build a small deterministic context bundle for the first tool loop."""

    def __init__(self, store: Any, tool_schemas: list[dict[str, Any]] | None = None) -> None:
        self._store = store
        self._tool_schemas = tool_schemas

    def build(self, session_id: str, goal: str) -> dict[str, object]:
        session = self._store.require_session(session_id)
        workspace = self._load_workspace(session["workspaceId"])
        config = self._load_config()
        search_config = config.get("search") or {}
        workspace_ignore = list(config.get("workspace", {}).get("ignore", []))
        search_ignore = list(search_config.get("ignore", []))
        search_glob = list(search_config.get("glob", []))
        search_config_bundle = {
            "glob": search_glob,
            "ignore": list(dict.fromkeys([*workspace_ignore, *search_ignore])),
        }
        tools = self._tool_schemas or DEFAULT_TOOL_SCHEMAS
        messages, budget_stats = self._build_messages(
            session=session,
            workspace=workspace,
            config=config,
            goal=goal,
            tools=tools,
        )
        return {
            "session_id": session_id,
            "workspace_id": session["workspaceId"],
            "workspace_name": workspace["name"],
            "workspace_root": workspace["rootPath"],
            "config": config,
            "search_config": search_config_bundle,
            "goal": goal,
            "search_query": self._derive_search_query(goal),
            "search_mode": self._choose_search_mode(goal),
            "files": [],
            "searches": [],
            "recent_commands": [],
            "messages": messages,
            "tools": tools,
            "budgetStats": budget_stats,
        }

    def _build_messages(
        self,
        *,
        session: dict[str, Any],
        workspace: dict[str, Any],
        config: dict[str, Any],
        goal: str,
        tools: list[dict[str, Any]],
    ) -> tuple[list[dict[str, str]], dict[str, Any]]:
        max_context_tokens = self._max_context_tokens(config)
        sections = [
            BudgetSection(
                name="system_prompt",
                text=self._system_prompt(workspace_root=workspace["rootPath"]),
                priority=1000,
                truncatable=False,
            ),
            BudgetSection(
                name="workspace_summary",
                text=self._workspace_summary(workspace),
                priority=800,
                minimum_tokens=30,
            ),
            BudgetSection(
                name="git_status",
                text=self._git_summary(workspace["rootPath"]),
                priority=700,
                minimum_tokens=24,
            ),
            *self._history_sections(session),
            BudgetSection(
                name="user_message",
                text=f"User request:\n{goal}",
                priority=900,
                truncatable=False,
            ),
        ]

        budget = TokenBudget(max_context_tokens)
        budget_result = budget.fit(sections)
        kept_sections = budget_result.sections
        system_section = next((section for section in kept_sections if section.name == "system_prompt"), sections[0])
        user_context = "\n\n".join(section.text for section in kept_sections if section.name != "system_prompt")
        if not user_context:
            user_context = f"User request:\n{goal}"

        tool_schema_tokens = estimate_tokens(tools)
        stats = {
            **budget_result.stats,
            "toolSchemaTokens": tool_schema_tokens,
            "messageTokens": budget_result.stats["estimatedTokens"],
        }
        return (
            [
                {"role": "system", "content": system_section.text},
                {"role": "user", "content": user_context},
            ],
            stats,
        )

    def _max_context_tokens(self, config: dict[str, Any]) -> int:
        provider_config = config.get("provider") or {}
        value = provider_config.get("maxContextTokens") or config.get("maxContextTokens")
        if value is None:
            return DEFAULT_MAX_CONTEXT_TOKENS
        try:
            return max(1, int(value))
        except (TypeError, ValueError):
            return DEFAULT_MAX_CONTEXT_TOKENS

    def _system_prompt(self, *, workspace_root: str) -> str:
        return "\n".join(
            [
                "You are a local coding agent operating in a user-controlled desktop runtime.",
                f"Workspace root: {workspace_root}",
                "Safety boundaries:",
                "- stay within the workspace root for file and git operations.",
                "- write files only through apply_patch and wait for explicit approval before changes are applied.",
                "- run commands only through run_command and wait for explicit approval before execution.",
                "- do not bypass the provided tools or approval workflow.",
                "- do not read secrets or operate outside the workspace unless the user explicitly provides content.",
            ]
        )

    def _workspace_summary(self, workspace: dict[str, Any]) -> str:
        root = Path(workspace["rootPath"])
        lines = [
            "Workspace summary:",
            f"- id: {workspace['id']}",
            f"- name: {workspace['name']}",
            f"- root: {workspace['rootPath']}",
        ]
        if not root.exists() or not root.is_dir():
            lines.append("- status: Workspace root is not accessible.")
            return "\n".join(lines)

        try:
            children = sorted(root.iterdir(), key=lambda path: (not path.is_dir(), path.name.lower()))
        except OSError as exc:
            lines.append(f"- status: Workspace root is not accessible: {exc}")
            return "\n".join(lines)

        if not children:
            lines.append("- status: Workspace root is accessible but empty.")
            return "\n".join(lines)

        entries = []
        for child in children[:12]:
            suffix = "/" if child.is_dir() else ""
            entries.append(f"{child.name}{suffix}")
        extra = "" if len(children) <= 12 else f" (+{len(children) - 12} more)"
        lines.append(f"- top-level entries: {', '.join(entries)}{extra}")
        return "\n".join(lines)

    def _git_summary(self, workspace_root: str) -> str:
        root = Path(workspace_root)
        if not root.exists() or not root.is_dir():
            return "Git status summary:\n- unavailable: workspace root is not accessible."

        status = self._run_git(root, ["status", "--short", "--branch"])
        if status is None:
            return "Git status summary:\n- unavailable: not a git repository or git command failed."

        lines = [line for line in status.splitlines() if line.strip()]
        if not lines:
            return "Git status summary:\n- working tree appears clean."

        summary = ["Git status summary:", f"- {lines[0]}"]
        changes = lines[1:]
        if not changes:
            summary.append("- no changed files reported.")
        else:
            summary.append(f"- changed files: {len(changes)}")
            for line in changes[:10]:
                summary.append(f"  {line}")

        diff_stat = self._run_git(root, ["diff", "--stat"])
        if diff_stat:
            summary.append("Git diff stat:")
            summary.extend(diff_stat.splitlines()[:12])
        return "\n".join(summary)

    def _run_git(self, root: Path, args: list[str]) -> str | None:
        try:
            process = subprocess.Popen(
                ["git", "-C", str(root), *args],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            stdout, _stderr = process.communicate(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
            return None
        except (OSError, subprocess.SubprocessError):
            return None
        if process.returncode != 0:
            return None
        return stdout or ""

    def _history_sections(self, session: dict[str, Any]) -> list[BudgetSection]:
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

    def _task_summary(self, task: dict[str, Any]) -> str:
        lines = [
            f"Recent task event: task {task['status']}",
            f"- goal: {task['goal']}",
        ]
        if task.get("result_json"):
            lines.append(f"- result: {task['result_json']}")
        if task.get("error_code"):
            lines.append(f"- error: {task['error_code']}")
        return "\n".join(lines)

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

    def _load_workspace(self, workspace_id: str) -> dict[str, Any]:
        row = self._store._conn.execute(  # noqa: SLF001
            "SELECT * FROM workspaces WHERE id = ?",
            (workspace_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"Workspace not found: {workspace_id}")

        workspace = dict(row)
        return {
            "id": workspace["id"],
            "name": workspace["name"],
            "rootPath": workspace["root_path"],
        }

    def _load_config(self) -> dict[str, Any]:
        return self._store.get_config({}).get("config", {})

    def _derive_search_query(self, goal: str) -> str:
        tokens = re.findall(r"[\w.\-/:]+", goal.lower())
        filtered = [token for token in tokens if len(token) > 1 and token not in COMMON_GOAL_TERMS]
        if not filtered:
            return ""
        return " ".join(filtered[:6])

    def _choose_search_mode(self, goal: str) -> str:
        lowered = goal.lower()
        if any(marker in lowered for marker in (".py", ".ts", ".tsx", ".js", ".jsx", ".rs", ".md")):
            return "filename"
        if any(marker in lowered for marker in ("file", "module", "folder", "directory")):
            return "filename"
        return "content"
