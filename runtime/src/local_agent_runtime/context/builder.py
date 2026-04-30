from __future__ import annotations

import json
import subprocess
from pathlib import Path
import re
import time
from typing import Any

from local_agent_runtime.tools.registry import BUILTIN_TOOL_SCHEMAS, to_openai_function_tools

from .compactor import ContextCompactor
from .scratchpad import Scratchpad
from .token_budget import BudgetSection, TokenBudget, estimate_tokens


# JIT injection triggers: keyword → section label
_JIT_TRIGGERS: dict[str, str] = {
    "test": "testing_patterns",
    "测试": "testing_patterns",
    "debug": "debug_logs",
    "调试": "debug_logs",
    "deploy": "deployment_config",
    "部署": "deployment_config",
    "security": "security_policy",
    "安全": "security_policy",
}

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

DEFAULT_TOOL_SCHEMAS = BUILTIN_TOOL_SCHEMAS


class ContextBuilder:
    """Build a small deterministic context bundle for the first tool loop."""

    def __init__(
        self,
        store: Any,
        tool_schemas: list[dict[str, Any]] | None = None,
        *,
        compactor: ContextCompactor | None = None,
        scratchpad: Scratchpad | None = None,
        skill_registry: Any | None = None,
    ) -> None:
        self._store = store
        self._tool_schemas = tool_schemas
        self._compactor = compactor
        self._scratchpad = scratchpad
        self._skill_registry = skill_registry
        self._git_cache: dict[str, tuple[float, str]] = {}
        self._cached_openai_tools: list[dict[str, Any]] | None = None
        self._cached_tool_schema_tokens: int | None = None
        self._cached_tool_schemas_id: int | None = None

    def build(self, session_id: str, goal: str, *, lightweight: bool = True, skill_id: str | None = None) -> dict[str, object]:
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
        tools = DEFAULT_TOOL_SCHEMAS if self._tool_schemas is None else self._tool_schemas

        # Resolve skill preset if skill_id is provided
        skill_preset = self._resolve_skill(skill_id)
        if skill_preset is not None:
            whitelist = set(skill_preset.tool_whitelist)
            # Always include memory and scratchpad tools if present
            for t in tools:
                if t.get("name", "").startswith(("memory.", "scratchpad.")):
                    whitelist.add(t["name"])
            tools = [t for t in tools if t.get("name") in whitelist]

        tools_id = id(tools)
        if self._cached_tool_schemas_id == tools_id and self._cached_openai_tools is not None:
            openai_tools = self._cached_openai_tools
            tool_schema_tokens = self._cached_tool_schema_tokens or 0
        else:
            tool_schema_tokens = estimate_tokens(tools)
            openai_tools = to_openai_function_tools(tools)
            self._cached_openai_tools = openai_tools
            self._cached_tool_schema_tokens = tool_schema_tokens
            self._cached_tool_schemas_id = tools_id
        messages, budget_stats = self._build_messages(
            session=session,
            workspace=workspace,
            config=config,
            goal=goal,
            tool_schema_tokens=tool_schema_tokens,
            lightweight=lightweight,
            skill_preset=skill_preset,
        )
        return {
            "session_id": session_id,
            "workspace_id": session["workspaceId"],
            "workspace_name": workspace["name"],
            "workspace_root": workspace["rootPath"],
            "project_focus": workspace.get("focus"),
            "project_memory": workspace.get("summary") if not lightweight else None,
            "config": config,
            "post_task_validation": self._post_task_validation_config(config),
            "search_config": search_config_bundle,
            "goal": goal,
            "search_query": self._derive_search_query(goal),
            "search_mode": self._choose_search_mode(goal),
            "files": [],
            "searches": [],
            "recent_commands": [],
            "messages": messages,
            "tools": tools,
            "openai_tools": openai_tools,
            "budgetStats": budget_stats,
            "lightweight": lightweight,
        }

    def _post_task_validation_config(self, config: dict[str, Any]) -> dict[str, Any]:
        policy = config.get("policy") if isinstance(config, dict) else {}
        validation = policy.get("postTaskValidation") if isinstance(policy, dict) else {}
        if not isinstance(validation, dict):
            validation = {}
        command = validation.get("command")
        return {
            "command": command.strip() if isinstance(command, str) and command.strip() else None,
        }

    def _build_messages(
        self,
        *,
        session: dict[str, Any],
        workspace: dict[str, Any],
        config: dict[str, Any],
        goal: str,
        tool_schema_tokens: int,
        lightweight: bool = True,
        skill_preset: Any | None = None,
    ) -> tuple[list[dict[str, str]], dict[str, Any]]:
        max_context_tokens = self._max_context_tokens(config)
        # Use skill's system_prompt if available, otherwise default
        if skill_preset is not None and skill_preset.system_prompt:
            system_text = self._skill_system_prompt(skill_preset, workspace_root=workspace["rootPath"])
        else:
            system_text = self._system_prompt(workspace_root=workspace["rootPath"])
        sections = [
            BudgetSection(
                name="system_prompt",
                text=system_text,
                priority=1000,
                truncatable=False,
            ),
            BudgetSection(
                name="workspace_summary",
                text=self._workspace_summary_basic(workspace),
                priority=800,
                minimum_tokens=30,
            ),
            BudgetSection(
                name="user_message",
                text=f"User request:\n{goal}",
                priority=900,
                truncatable=False,
            ),
        ]

        # In lightweight mode, skip heavy context sections (project memory, git,
        # key files) so the LLM can decide through tool calls if it needs them.
        if not lightweight:
            project_focus = self._project_focus_summary(workspace, max_chars=min(1200, max_context_tokens * 2))
            if project_focus:
                sections.append(
                    BudgetSection(
                        name="project_focus",
                        text=project_focus,
                        priority=950,
                        truncatable=False,
                    )
                )
            project_memory = self._workspace_memory_section(workspace)
            if project_memory:
                sections.append(project_memory)
            sections.extend(self._key_file_sections(workspace["rootPath"]))
            sections.append(
                BudgetSection(
                    name="git_status",
                    text=self._git_summary(workspace["rootPath"]),
                    priority=700,
                    minimum_tokens=24,
                )
            )
            sections.extend(self._history_sections(session))

        # Scratchpad: inject intermediate reasoning state if available
        scratchpad_section = self._scratchpad_section(session["id"])
        if scratchpad_section is not None:
            sections.append(scratchpad_section)

        budget = TokenBudget(max_context_tokens)
        budget_result = budget.fit(sections, fixed_tokens=tool_schema_tokens)
        kept_sections = budget_result.sections
        system_section = next((section for section in kept_sections if section.name == "system_prompt"), sections[0])
        user_context = "\n\n".join(section.text for section in kept_sections if section.name != "system_prompt")
        if not user_context:
            user_context = f"User request:\n{goal}"

        messages: list[dict[str, str]] = [
            {"role": "system", "content": system_section.text},
            {"role": "user", "content": user_context},
        ]

        # Compaction: if messages exceed budget, compress via three-segment strategy
        if self._compactor is not None:
            total_msg_tokens = sum(estimate_tokens(m.get("content", "")) for m in messages)
            if total_msg_tokens > max_context_tokens:
                result = self._compactor.compact(
                    session_id=session["id"],
                    messages=messages,
                    max_tokens=max_context_tokens,
                )
                messages = result.kept_messages  # type: ignore[assignment]

        message_tokens = max(0, budget_result.stats["estimatedTokens"] - tool_schema_tokens)
        stats = {
            **budget_result.stats,
            "toolSchemaTokens": tool_schema_tokens,
            "messageTokens": message_tokens,
        }
        return (
            messages,
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

    _KEY_FILE_NAMES = (
        "README.md",
        "README",
        "README.txt",
        "package.json",
        "pyproject.toml",
        "Cargo.toml",
        "Makefile",
        "makefile",
        "go.mod",
    )

    _KEY_FILE_MAX_BYTES = 4000

    def _key_file_sections(self, workspace_root: str) -> list[BudgetSection]:
        root = Path(workspace_root)
        if not root.exists() or not root.is_dir():
            return []

        sections: list[BudgetSection] = []
        max_bytes = self._KEY_FILE_MAX_BYTES
        candidate_names = list(self._KEY_FILE_NAMES) + [".claude/CLAUDE.md"]

        for filename in candidate_names:
            filepath = root / filename
            if not filepath.exists() or not filepath.is_file():
                continue
            try:
                raw = filepath.read_bytes()
            except OSError:
                continue
            if not raw:
                continue

            truncated = len(raw) > max_bytes
            content = raw[:max_bytes]
            try:
                text = content.decode("utf-8")
            except UnicodeDecodeError:
                try:
                    text = content.decode("latin-1")
                except UnicodeDecodeError:
                    continue

            if truncated:
                text = text.rstrip() + f"\n[truncated at {max_bytes} bytes]"

            sections.append(
                BudgetSection(
                    name=f"key_file:{filename}",
                    text=f"--- {filename} ---\n{text}",
                    priority=850,
                    minimum_tokens=24,
                )
            )

        return sections

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

    def _resolve_skill(self, skill_id: str | None) -> Any | None:
        """Look up a SkillPreset by id via the skill registry."""
        if skill_id is None or self._skill_registry is None:
            return None
        return self._skill_registry.get(skill_id)

    def _skill_system_prompt(self, skill: Any, *, workspace_root: str) -> str:
        """Build a system prompt that combines the skill's role with safety boundaries."""
        return "\n".join(
            [
                skill.system_prompt,
                "",
                f"Workspace root: {workspace_root}",
                "Safety boundaries:",
                "- stay within the workspace root for file and git operations.",
                "- write files only through apply_patch and wait for explicit approval before changes are applied.",
                "- run commands only through run_command and wait for explicit approval before execution.",
                "- do not bypass the provided tools or approval workflow.",
                "- do not read secrets or operate outside the workspace unless the user explicitly provides content.",
            ]
        )

    def _workspace_summary_basic(self, workspace: dict[str, Any]) -> str:
        """Lightweight workspace summary without project memory."""
        root = Path(workspace["rootPath"])
        lines = [
            "Workspace summary:",
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

    def _workspace_summary(self, workspace: dict[str, Any]) -> str:
        """Full workspace summary including project memory (used in non-lightweight mode)."""
        basic = self._workspace_summary_basic(workspace)
        summary = str(workspace.get("summary") or "").strip()
        if summary:
            memory_text = summary if summary.startswith("Project memory:") else f"Project memory:\n{summary}"
            return f"{basic}\n{memory_text}"
        return basic

    def _workspace_memory_section(self, workspace: dict[str, Any]) -> BudgetSection | None:
        """Build a separate budget section for project memory."""
        summary = str(workspace.get("summary") or "").strip()
        if not summary:
            return None
        memory_text = summary if summary.startswith("Project memory:") else f"Project memory:\n{summary}"
        return BudgetSection(
            name="project_memory",
            text=memory_text,
            priority=750,
            minimum_tokens=30,
        )

    def _project_focus_summary(self, workspace: dict[str, Any], *, max_chars: int) -> str:
        focus = str(workspace.get("focus") or "").strip()
        if not focus:
            return ""

        normalized = "\n".join(line.rstrip() for line in focus.splitlines()).strip()
        max_chars = max(1, int(max_chars))
        if len(normalized) > max_chars:
            marker = " [truncated]"
            normalized = normalized[: max(1, max_chars - len(marker))].rstrip() + marker
        return f"Project focus:\n{normalized}"

    _GIT_CACHE_TTL = 5.0  # seconds

    def _git_summary(self, workspace_root: str) -> str:
        root = Path(workspace_root)
        if not root.exists() or not root.is_dir():
            return "Git status summary:\n- unavailable: workspace root is not accessible."

        # Check cache
        cache_key = str(root)
        now = time.monotonic()
        cached = self._git_cache.get(cache_key)
        if cached is not None:
            cached_time, cached_text = cached
            if now - cached_time < self._GIT_CACHE_TTL:
                return cached_text

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
        result = "\n".join(summary)
        self._git_cache[cache_key] = (now, result)
        return result

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
            "focus": workspace.get("focus"),
            "summary": workspace.get("summary"),
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

    # ------------------------------------------------------------------
    # Context refresh (lightweight update of volatile sections)
    # ------------------------------------------------------------------

    # Tools whose execution can change the workspace state that the context
    # captures (git status, directory listings, etc.).
    _STATE_MUTATING_TOOLS = frozenset({
        "apply_patch",
        "run_command",
        "write_file",
    })

    def should_refresh(self, tool_name: str) -> bool:
        """Return *True* if executing *tool_name* might invalidate cached
        context sections (e.g. git status after a patch)."""
        return tool_name in self._STATE_MUTATING_TOOLS

    def refresh_context(
        self,
        context: dict[str, Any],
        *,
        tool_name: str | None = None,
        tool_result: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Return an updated copy of *context* with volatile sections refreshed.

        Only the cheap, fast sections are refreshed:
        - Git status & diff stat
        - Top-level directory listing

        The existing ``messages`` list is **not** rebuilt — it preserves the
        ongoing conversation.  Call this after a state-mutating tool execution
        in the ReAct loop to keep the model's view of the workspace current.
        """
        workspace_root = context.get("workspace_root")
        if not isinstance(workspace_root, str) or not workspace_root.strip():
            return context

        updated = dict(context)  # shallow copy — messages list is shared

        # 1. Refresh git status summary and inject as a system-level hint
        git_text = self._git_summary(workspace_root)
        updated["_refreshed_git"] = git_text

        # 2. Refresh top-level directory snapshot
        workspace_summary = self._refresh_workspace_listing(workspace_root, context)
        if workspace_summary:
            updated["_refreshed_workspace_listing"] = workspace_summary

        # 3. Inject a concise refresh hint into the messages so the model
        #    sees the updated state on the next turn.
        refresh_hint = self._build_refresh_hint(
            git_text=git_text,
            workspace_listing=workspace_summary,
            tool_name=tool_name,
            tool_result=tool_result,
        )
        if refresh_hint:
            messages = list(updated.get("messages") or [])
            messages.append({
                "role": "system",
                "content": refresh_hint,
            })
            updated["messages"] = messages

        return updated

    def _refresh_workspace_listing(self, workspace_root: str, context: dict[str, Any]) -> str:
        """Regenerate the top-level directory listing for the workspace."""
        root = Path(workspace_root)
        if not root.exists() or not root.is_dir():
            return ""
        try:
            children = sorted(root.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except OSError:
            return ""
        if not children:
            return ""
        entries = []
        for child in children[:12]:
            suffix = "/" if child.is_dir() else ""
            entries.append(f"{child.name}{suffix}")
        extra = "" if len(children) <= 12 else f" (+{len(children) - 12} more)"
        return f"top-level entries: {', '.join(entries)}{extra}"

    def _build_refresh_hint(
        self,
        *,
        git_text: str,
        workspace_listing: str,
        tool_name: str | None,
        tool_result: dict[str, Any] | None,
    ) -> str:
        """Build a concise system message summarizing workspace changes."""
        parts: list[str] = ["[Context refresh after tool execution]"]
        if tool_name:
            parts.append(f"Tool executed: {tool_name}")
            if isinstance(tool_result, dict):
                status = tool_result.get("status") or "completed"
                parts.append(f"Result status: {status}")
        if git_text and "unavailable" not in git_text:
            parts.append(git_text)
        if workspace_listing:
            parts.append(f"Workspace {workspace_listing}")
        # Only inject if there is meaningful content beyond the header.
        if len(parts) <= 1:
            return ""
        return "\n".join(parts)

    # ------------------------------------------------------------------
    # JIT context injection
    # ------------------------------------------------------------------

    def inject_context(
        self,
        context: dict[str, Any],
        *,
        trigger: str,
    ) -> dict[str, Any]:
        """Dynamically inject additional context based on a trigger keyword.

        Called from the ReAct loop after tool execution.  Returns a new
        context dict with the injected section appended.
        """
        section_label = _JIT_TRIGGERS.get(trigger.lower())
        if section_label is None:
            return context

        session_id = context.get("session_id", "")
        injected_text = self._resolve_jit_section(session_id, section_label)
        if not injected_text:
            return context

        messages = list(context.get("messages", []))
        # Insert before the last user message
        injected_msg = {"role": "system", "content": f"[{section_label}]\n{injected_text}"}
        if messages and messages[-1].get("role") == "user":
            messages.insert(-1, injected_msg)
        else:
            messages.append(injected_msg)

        return {**context, "messages": messages}

    def _resolve_jit_section(self, session_id: str, label: str) -> str:
        """Build the text for a JIT section.  Currently uses scratchpad if available."""
        if self._scratchpad is None or not session_id:
            return ""
        entries = self._scratchpad.list_entries(session_id)
        relevant = [e for e in entries if label in e.key or e.key.startswith("jit_")]
        if not relevant:
            return ""
        lines = [f"- {e.key}: {e.value}" for e in relevant]
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Scratchpad integration
    # ------------------------------------------------------------------

    def _scratchpad_section(self, session_id: str) -> BudgetSection | None:
        """Build a budget section from scratchpad entries, if any."""
        if self._scratchpad is None:
            return None
        keys = self._scratchpad.list_keys(session_id)
        if not keys:
            return None
        lines: list[str] = ["Scratchpad (intermediate reasoning state):"]
        for key in keys[:20]:
            entry = self._scratchpad.read(session_id, key)
            if entry:
                lines.append(f"- {key}: {entry.value[:200]}")
        text = "\n".join(lines)
        return BudgetSection(
            name="scratchpad",
            text=text,
            priority=660,
            minimum_tokens=24,
        )
