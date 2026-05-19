from __future__ import annotations

import logging
import subprocess
from pathlib import Path
import re
import time
from typing import Any

logger = logging.getLogger(__name__)

from local_agent_runtime.tools.registry import BUILTIN_TOOL_SCHEMAS, to_openai_function_tools

from .compactor import ContextCompactor
from ._history_mixin import HistoryMixin
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

DEFAULT_MAX_CONTEXT_TOKENS = 256000

DEFAULT_TOOL_SCHEMAS = BUILTIN_TOOL_SCHEMAS

DEFAULT_AGENT_SOUL_BASELINE = {
    "id": "default",
    "name": "Default",
    "identity": "A capable local coding agent that works inside the user's desktop runtime.",
    "principles": [
        "Be practical, careful, and transparent about uncertainty.",
        "Prefer existing project patterns over unnecessary new abstractions.",
        "Keep the user in control of risky actions.",
    ],
    "communicationStyle": "Clear, concise, collaborative.",
    "reasoningStyle": "Inspect the current workspace before making changes.",
    "collaborationStyle": "Explain meaningful decisions and keep work scoped to the user's request.",
    "domainPreferences": [],
    "customSystemPrompt": "",
}


class ContextBuilder(HistoryMixin):
    """Build a small deterministic context bundle for the first tool loop."""

    def __init__(
        self,
        store: Any,
        tool_schemas: list[dict[str, Any]] | None = None,
        *,
        tool_schema_provider: Any | None = None,
        compactor: ContextCompactor | None = None,
        scratchpad: Scratchpad | None = None,
        skill_registry: Any | None = None,
    ) -> None:
        self._store = store
        self._tool_schemas = tool_schemas
        self._tool_schema_provider = tool_schema_provider
        self._compactor = compactor
        self._scratchpad = scratchpad
        self._skill_registry = skill_registry
        self._git_cache: dict[str, tuple[float, str]] = {}
        self._cached_openai_tools: list[dict[str, Any]] | None = None
        self._cached_tool_schema_tokens: int | None = None
        self._cached_tool_schemas_id: int | None = None

    def build(
        self,
        session_id: str,
        goal: str,
        *,
        lightweight: bool = True,
        skill_id: str | None = None,
        role: str | None = None,
        include_history: bool = True,
        include_scratchpad: bool = True,
    ) -> dict[str, object]:
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
        tools = self._resolve_tool_schemas()

        # Resolve skill preset if skill_id is provided
        skill_preset = self._resolve_skill(skill_id)
        skill_fallback = self._skill_fallback_context(skill_id=skill_id, skill_preset=skill_preset)
        skill_policy_context: dict[str, Any] | None = None
        filtered_tool_names: list[str] | None = None
        original_tool_names: list[str] | None = None
        if skill_preset is not None:
            skill_policy_context = {
                "skillId": getattr(skill_preset, "id", skill_id),
                "toolPolicy": getattr(skill_preset.tool_policy, "value", str(skill_preset.tool_policy)),
                "toolWhitelist": list(skill_preset.tool_whitelist),
            }
            original_tool_names = [t.get("name", "") for t in tools]
            from ..skills.types import ToolPolicy
            policy = skill_preset.tool_policy

            if policy == ToolPolicy.INHERIT_ALL:
                # All tools available — no filtering
                pass
            elif policy == ToolPolicy.INHERIT_MCP:
                # Whitelist for built-in tools, but all MCP tools pass through
                whitelist = set(skill_preset.tool_whitelist)
                for t in tools:
                    if t.get("name", "").startswith(("memory.", "scratchpad.", "mcp__")):
                        whitelist.add(t["name"])
                filtered_tool_names = [t.get("name", "") for t in tools if t.get("name") in whitelist]
                tools = [t for t in tools if t.get("name") in whitelist]
            else:
                # strict_whitelist (default)
                whitelist = set(skill_preset.tool_whitelist)
                # Always include memory and scratchpad tools if present
                for t in tools:
                    if t.get("name", "").startswith(("memory.", "scratchpad.")):
                        whitelist.add(t["name"])
                filtered_tool_names = [t.get("name", "") for t in tools if t.get("name") in whitelist]
                tools = [t for t in tools if t.get("name") in whitelist]

            # Inject skill parameter constraints into provider config
            if skill_preset.parameter_constraints:
                provider_overrides = dict(config.get("provider", {})) if isinstance(config.get("provider"), dict) else {}
                for k, v in skill_preset.parameter_constraints.items():
                    if k not in provider_overrides:
                        provider_overrides[k] = v
                config = {**config, "provider": provider_overrides}
                logger.debug("Skill %s: injected parameter_constraints %s", skill_id, skill_preset.parameter_constraints)

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
            role=role,
            include_history=include_history,
            include_scratchpad=include_scratchpad,
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
            "skillPolicy": skill_policy_context,
            "skillFallback": skill_fallback,
            "budgetStats": budget_stats,
            "snapshot_metadata": {
                "included_sections": budget_stats.get("includedSections", []),
                "trimmed_sections": budget_stats.get("trimmedSections", []),
                "dropped_sections": budget_stats.get("droppedSections", []),
                "tool_count": len(tools),
                "skill_id": skill_id,
                "token_estimate": budget_stats.get("estimatedTokens", 0),
                "filtered_tool_names": filtered_tool_names,
                "original_tool_names": original_tool_names,
                "skillPolicy": skill_policy_context,
                "skillFallback": skill_fallback,
                "autonomy_profile": self._active_autonomy_profile(config),
                "agent_soul_profile": self._active_agent_soul_profile(config),
                "prompt_layers": budget_stats.get("promptLayers", []),
            },
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

    def _resolve_tool_schemas(self) -> list[dict[str, Any]]:
        if self._tool_schemas is not None:
            return list(self._tool_schemas)
        if self._tool_schema_provider is not None:
            provided = self._tool_schema_provider()
            if isinstance(provided, list):
                return list(provided)
        return list(DEFAULT_TOOL_SCHEMAS)

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
        role: str | None = None,
        include_history: bool = True,
        include_scratchpad: bool = True,
    ) -> tuple[list[dict[str, str]], dict[str, Any]]:
        max_context_tokens = self._max_context_tokens(config)
        system_text, prompt_layers = self._compose_system_prompt(
            workspace_root=workspace["rootPath"],
            config=config,
            role=role,
            skill_preset=skill_preset,
        )
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
            if include_history:
                sections.extend(self._history_sections(session))
        elif include_history:
            sections.extend(self._conversation_history_sections(session))

        sections.append(
            BudgetSection(
                name="user_message",
                text=f"Current user request:\n{goal}",
                priority=900,
                truncatable=False,
            )
        )

        # Scratchpad: inject intermediate reasoning state if available
        if include_scratchpad:
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
            decision = self._compactor.should_compact(messages, max_context_tokens)
            if decision.should_compact:
                result = self._compactor.compact(
                    session_id=session["id"],
                    messages=messages,
                    max_tokens=max_context_tokens,
                    force=decision.force,
                )
                messages = result.kept_messages  # type: ignore[assignment]

        message_tokens = max(0, budget_result.stats["estimatedTokens"] - tool_schema_tokens)
        stats = {
            **budget_result.stats,
            "toolSchemaTokens": tool_schema_tokens,
            "messageTokens": message_tokens,
            "promptLayers": prompt_layers,
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

    _ROLE_INSTRUCTIONS: dict[str, list[str]] = {
        "worker": [
            "You are a worker agent. Your role is to implement changes within your assigned scope.",
            "Guidelines:",
            "- Focus only on the files and directories in your assigned scope.",
            "- Do NOT commit changes. The root agent will handle merging and committing.",
            "- After completing your work, report: changed files, tests run, and risks found.",
            "- Keep your changes minimal and focused on the assigned task.",
        ],
        "reviewer": [
            "You are a reviewer agent. Your role is to review changes made by worker agents.",
            "Guidelines:",
            "- Read-only access: do NOT modify any files.",
            "- Check for: correctness, scope compliance, test coverage, and potential risks.",
            "- Report findings as a structured review with approved/feedback status.",
            "- Flag any files changed outside the worker's assigned scope.",
        ],
        "planner": [
            "You are a planner agent. Your role is to analyze tasks and create execution plans.",
            "Guidelines:",
            "- Break down complex tasks into well-defined subtasks.",
            "- Assign appropriate roles (worker/reviewer) to each subtask.",
            "- Define clear scope boundaries for each subtask to prevent conflicts.",
            "- Consider dependencies between subtasks and order them appropriately.",
        ],
        "summarizer": [
            "You are a summarizer agent. Your role is to synthesize results from multiple agents.",
            "Guidelines:",
            "- Combine outputs from all subtasks into a coherent final summary.",
            "- Highlight key decisions, changes made, and any remaining issues.",
            "- Keep the summary concise but complete.",
        ],
    }

    def _system_prompt(self, *, workspace_root: str, role: str | None = None) -> str:
        return "\n".join([
            self._role_prompt(role),
            "",
            self._safety_prompt(workspace_root=workspace_root),
        ])

    def _compose_system_prompt(
        self,
        *,
        workspace_root: str,
        config: dict[str, Any],
        role: str | None = None,
        skill_preset: Any | None = None,
    ) -> tuple[str, list[dict[str, Any]]]:
        """Compose runtime-owned safety with user-configurable soul layers."""
        sections: list[tuple[str, str, dict[str, Any]]] = []
        sections.append(("role", self._role_prompt(role), {"role": (role or "root").lower()}))

        soul_profile = self._active_agent_soul_profile(config)
        soul_prompt = self._agent_soul_prompt(soul_profile)
        if soul_prompt:
            sections.append((
                "agent_soul",
                soul_prompt,
                {
                    "profileId": soul_profile.get("id"),
                    "profileName": soul_profile.get("name"),
                },
            ))

        workspace_instructions = self._workspace_instructions(config)
        if workspace_instructions:
            sections.append(("workspace_instructions", workspace_instructions, {}))

        if skill_preset is not None and getattr(skill_preset, "system_prompt", None):
            sections.append((
                "skill",
                str(skill_preset.system_prompt).strip(),
                {
                    "skillId": getattr(skill_preset, "id", None),
                    "skillName": getattr(skill_preset, "name", None),
                },
            ))

        sections.append(("runtime_safety", self._safety_prompt(workspace_root=workspace_root), {"locked": True}))
        prompt_layers: list[dict[str, Any]] = [
            {
                "name": name,
                "tokenEstimate": estimate_tokens(text),
                **metadata,
            }
            for name, text, metadata in sections
            if text
        ]
        return "\n\n".join(text for _name, text, _metadata in sections if text), prompt_layers

    def _role_prompt(self, role: str | None = None) -> str:
        effective_role = (role or "root").lower()
        lines: list[str] = []
        if effective_role == "root":
            lines.append("You are a local coding agent operating in a user-controlled desktop runtime.")
        else:
            role_instructions = self._ROLE_INSTRUCTIONS.get(effective_role)
            if role_instructions:
                lines.extend(role_instructions)
            else:
                lines.append("You are a local coding agent operating in a user-controlled desktop runtime.")
        return "\n".join(lines)

    def _safety_prompt(self, *, workspace_root: str) -> str:
        return "\n".join([
            f"Workspace root: {workspace_root}",
            "Safety boundaries:",
            "- stay within the workspace root for file and git operations.",
            "- write files only through apply_patch or write_file; use write_file for new/full files and apply_patch for small edits.",
            "- run commands only through run_command; if the runtime requests approval, wait for approval before execution.",
            "- do not bypass the provided tools or approval workflow.",
            "- do not read secrets or operate outside the workspace unless the user explicitly provides content.",
        ])

    def _active_autonomy_profile(self, config: dict[str, Any]) -> dict[str, Any] | None:
        autonomy = config.get("autonomy") if isinstance(config, dict) else None
        if not isinstance(autonomy, dict):
            return None
        profiles = autonomy.get("profiles")
        if not isinstance(profiles, list):
            return None
        active_id = autonomy.get("activeProfileId")
        for profile in profiles:
            if isinstance(profile, dict) and profile.get("id") == active_id:
                return dict(profile)
        for profile in profiles:
            if isinstance(profile, dict):
                return dict(profile)
        return None

    def _active_agent_soul_profile(self, config: dict[str, Any]) -> dict[str, Any]:
        agent_soul = config.get("agentSoul") if isinstance(config, dict) else None
        if not isinstance(agent_soul, dict):
            return {}
        profiles = agent_soul.get("profiles")
        if not isinstance(profiles, list):
            return {}
        active_id = agent_soul.get("activeProfileId")
        for profile in profiles:
            if isinstance(profile, dict) and profile.get("id") == active_id:
                return dict(profile)
        for profile in profiles:
            if isinstance(profile, dict):
                return dict(profile)
        return {}

    def _agent_soul_prompt(self, profile: dict[str, Any]) -> str:
        if not profile or profile.get("enabled") is False:
            return ""
        if self._is_default_agent_soul_baseline(profile):
            return ""
        lines = ["Agent soul:"]
        for label, key in (
            ("Identity", "identity"),
            ("Communication style", "communicationStyle"),
            ("Reasoning style", "reasoningStyle"),
            ("Collaboration style", "collaborationStyle"),
        ):
            value = profile.get(key)
            if isinstance(value, str) and value.strip():
                lines.append(f"- {label}: {value.strip()}")
        principles = profile.get("principles")
        if isinstance(principles, list):
            cleaned = [str(item).strip() for item in principles if str(item).strip()]
            if cleaned:
                lines.append("- Principles:")
                lines.extend(f"  - {item}" for item in cleaned)
        domain_preferences = profile.get("domainPreferences")
        if isinstance(domain_preferences, list):
            cleaned = [str(item).strip() for item in domain_preferences if str(item).strip()]
            if cleaned:
                lines.append(f"- Domain preferences: {', '.join(cleaned)}")
        custom_prompt = profile.get("customSystemPrompt")
        if isinstance(custom_prompt, str) and custom_prompt.strip():
            lines.append("Custom system prompt:")
            lines.append(custom_prompt.strip())
        return "\n".join(lines) if len(lines) > 1 else ""

    def _is_default_agent_soul_baseline(self, profile: dict[str, Any]) -> bool:
        if profile.get("id") != DEFAULT_AGENT_SOUL_BASELINE["id"]:
            return False
        for key, expected in DEFAULT_AGENT_SOUL_BASELINE.items():
            value = profile.get(key)
            if isinstance(expected, list):
                normalized = [str(item).strip() for item in value or [] if str(item).strip()] if isinstance(value, list) else []
                if normalized != expected:
                    return False
                continue
            if str(value or "").strip() != str(expected).strip():
                return False
        return True

    def _workspace_instructions(self, config: dict[str, Any]) -> str:
        agent_soul = config.get("agentSoul") if isinstance(config, dict) else None
        if not isinstance(agent_soul, dict):
            return ""
        value = agent_soul.get("workspaceInstructions")
        if not isinstance(value, str) or not value.strip():
            return ""
        return f"Workspace instructions:\n{value.strip()}"

    def _resolve_skill(self, skill_id: str | None) -> Any | None:
        """Look up a SkillPreset by id via the skill registry."""
        if skill_id is None or self._skill_registry is None:
            return None
        return self._skill_registry.get(skill_id)

    def _skill_fallback_context(self, *, skill_id: str | None, skill_preset: Any | None) -> dict[str, Any] | None:
        requested = str(skill_id or "").strip()
        if not requested or skill_preset is not None:
            return None
        reason = "skill_registry_unavailable" if self._skill_registry is None else "skill_not_found"
        return {
            "requestedSkillId": requested,
            "reason": reason,
            "fallback": "default_prompt_and_tools",
            "status": "active",
        }

    def _skill_system_prompt(self, skill: Any, *, workspace_root: str) -> str:
        """Build a system prompt that combines the skill's role with safety boundaries."""
        return "\n".join(
            [
                skill.system_prompt,
                "",
                f"Workspace root: {workspace_root}",
                "Safety boundaries:",
                "- stay within the workspace root for file and git operations.",
                "- write files only through apply_patch or write_file; use write_file for new/full files and apply_patch for small edits.",
                "- run commands only through run_command; if the runtime requests approval, wait for approval before execution.",
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
                encoding="utf-8",
                errors="replace",
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
