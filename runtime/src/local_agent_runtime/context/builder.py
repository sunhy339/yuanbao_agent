from __future__ import annotations

import base64
import logging
import mimetypes
import subprocess
from pathlib import Path
import re
import time
from collections import deque
from typing import Any

logger = logging.getLogger(__name__)

from local_agent_runtime.tools.registry import BUILTIN_TOOL_SCHEMAS, to_openai_function_tools

from .compactor import ContextCompactor
from ._history_mixin import HistoryMixin
from .scratchpad import Scratchpad
from .token_budget import BudgetSection, TokenBudget, estimate_tokens, trim_text_to_tokens


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
CONTROL_FLOW_TOOL_NAMES = frozenset({"ask_user_question", "enter_plan_mode", "exit_plan_mode"})
DEFAULT_CONTROL_FLOW_TOOL_NAMES = frozenset({"ask_user_question"})
DEFAULT_CANONICAL_MEMORY_FILES = (
    "YUANBAO.md",
    "MEMORY.md",
    "MEMORY.local.md",
)
IMAGE_ATTACHMENT_MAX_COUNT = 4
IMAGE_ATTACHMENT_MAX_BYTES = 5 * 1024 * 1024
EXTERNAL_ATTACHMENT_MAX_BYTES = 16_000
EXTERNAL_ATTACHMENT_TOTAL_MAX_BYTES = 48_000
IMAGE_ATTACHMENT_MIME_TYPES = {
    ".gif": "image/gif",
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}
IMAGE_ATTACHMENT_METADATA_KEYS = ("attachments", "images", "imagePaths")

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
    """Build a deterministic context bundle for the first tool loop."""

    _STABLE_CONTEXT_MARKER = "Stable context prefix:"
    _DYNAMIC_CONTEXT_MARKER = "Dynamic context tail:"
    _CURRENT_REQUEST_MARKER = "Current user request:"
    _STABLE_PREFIX_SECTION_NAMES = {
        "workspace_summary",
        "project_focus",
        "canonical_memory",
        "project_memory",
        "stable_workspace_context",
        "runtime_role",
    }

    _DEFAULT_PROMPT_CACHE_POLICY = {
        "enabled": True,
        "includeKeyFiles": False,
        "includeStableWorkspaceContext": False,
        "targetFillRatio": 0.92,
        "maxStableContextTokens": 240000,
        "recentMessages": 256,
        "recentTasks": 24,
        "recentPatches": 8,
        "recentCommands": 12,
        "conversationMessageMaxChars": 24000,
        "taskSummaryMaxChars": 1600,
        "keyFileMaxBytes": 24000,
        "canonicalMemoryMaxChars": 24000,
    }

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
        minimal: bool = False,
        current_message_metadata: dict[str, Any] | None = None,
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
                    if t.get("name") in DEFAULT_CONTROL_FLOW_TOOL_NAMES:
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
                    if t.get("name") in DEFAULT_CONTROL_FLOW_TOOL_NAMES:
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
        if minimal:
            openai_tools = []
            tool_schema_tokens = 0
            tools = []
        elif self._cached_tool_schemas_id == tools_id and self._cached_openai_tools is not None:
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
            cache_policy=self._prompt_cache_policy(config, lightweight=lightweight),
            lightweight=lightweight,
            skill_preset=skill_preset,
            role=role,
            include_history=include_history and not minimal,
            include_scratchpad=include_scratchpad and not minimal,
            minimal=minimal,
            current_message_metadata=current_message_metadata,
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
            "minimal": minimal,
        }

    def _post_task_validation_config(self, config: dict[str, Any]) -> dict[str, Any]:
        policy = config.get("policy") if isinstance(config, dict) else {}
        validation = policy.get("postTaskValidation") if isinstance(policy, dict) else {}
        if not isinstance(validation, dict):
            validation = {}
        command = validation.get("command")
        git_snapshot = validation.get("gitSnapshot")
        if not isinstance(git_snapshot, bool):
            git_snapshot = validation.get("git_snapshot")
        if not isinstance(git_snapshot, bool):
            git_snapshot = validation.get("includeGitSnapshot")
        if not isinstance(git_snapshot, bool):
            git_snapshot = validation.get("include_git_snapshot")
        return {
            "command": command.strip() if isinstance(command, str) and command.strip() else None,
            "gitSnapshot": git_snapshot if isinstance(git_snapshot, bool) else False,
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
        cache_policy: dict[str, Any],
        lightweight: bool = True,
        skill_preset: Any | None = None,
        role: str | None = None,
        include_history: bool = True,
        include_scratchpad: bool = True,
        minimal: bool = False,
        current_message_metadata: dict[str, Any] | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        max_context_tokens = self._max_context_tokens(config)
        system_text, prompt_layers, role_text = self._compose_system_prompt(
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

        project_focus = "" if minimal else self._project_focus_summary(workspace, max_chars=min(1200, max_context_tokens * 2))
        if project_focus:
            sections.append(
                BudgetSection(
                    name="project_focus",
                    text=project_focus,
                    priority=950,
                    truncatable=False,
                )
            )
        canonical_memory = None if minimal else self._canonical_memory_section(workspace["rootPath"], cache_policy=cache_policy)
        if canonical_memory:
            sections.append(canonical_memory)
        project_memory = None if minimal else self._workspace_memory_section(workspace)
        if project_memory:
            sections.append(project_memory)
        if not minimal and cache_policy.get("includeKeyFiles") is True:
            sections.extend(self._key_file_sections(workspace["rootPath"], cache_policy=cache_policy))
        stable_pack = None
        if not minimal and cache_policy.get("includeStableWorkspaceContext") is True:
            stable_pack = self._stable_workspace_context_pack(
                workspace["rootPath"],
                cache_policy=cache_policy,
                reserved_tokens=tool_schema_tokens + estimate_tokens(goal) + 2000,
            )
        if stable_pack is not None:
            sections.append(stable_pack)
        if role_text:
            sections.append(
                BudgetSection(
                    name="runtime_role",
                    text=role_text,
                    priority=900,
                    truncatable=False,
                )
            )

        recent_messages: list[dict[str, Any]] = []
        if include_history:
            recent_messages = self._recent_messages(
                session["id"],
                limit=self._policy_int(cache_policy, "recentMessages", 8),
            )
            if lightweight:
                sections.extend(
                    self._conversation_history_sections(
                        session,
                        current_message_metadata=current_message_metadata,
                    )
                )
            else:
                sections.extend(
                    self._history_sections(
                        session,
                        policy=cache_policy,
                        current_message_metadata=current_message_metadata,
                    )
                )
        sections.extend(
            self._current_external_attachment_sections(
                workspace_root=workspace["rootPath"],
                metadata=current_message_metadata,
                policy=cache_policy,
            )
        )
        if not lightweight:
            sections.append(
                BudgetSection(
                    name="git_status",
                    text=self._git_summary(workspace["rootPath"]),
                    priority=260,
                    minimum_tokens=16,
                )
            )

        # Scratchpad is volatile, so keep it in the dynamic tail before the
        # current request.
        if include_scratchpad:
            scratchpad_section = self._scratchpad_section(session["id"])
            if scratchpad_section is not None:
                sections.append(scratchpad_section)

        sections.append(
            BudgetSection(
                name="user_message",
                text=f"Current user request:\n{goal}",
                priority=900,
                truncatable=False,
            )
        )

        budget = TokenBudget(max_context_tokens)
        budget_result = budget.fit(sections, fixed_tokens=tool_schema_tokens)
        kept_sections = budget_result.sections
        system_section = next((section for section in kept_sections if section.name == "system_prompt"), sections[0])
        non_system_sections = [section for section in kept_sections if section.name != "system_prompt"]
        context_sections = [section for section in non_system_sections if section.name != "user_message"]
        stable_context_sections = [section for section in context_sections if self._is_stable_prefix_section(section)]
        dynamic_context_sections = [section for section in context_sections if not self._is_stable_prefix_section(section)]
        stable_context = self._context_layer_text(self._STABLE_CONTEXT_MARKER, stable_context_sections)
        dynamic_context = self._context_layer_text(self._DYNAMIC_CONTEXT_MARKER, dynamic_context_sections)
        user_message_section = next((section for section in non_system_sections if section.name == "user_message"), None)
        current_request = user_message_section.text if user_message_section is not None else f"Current user request:\n{goal}"

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_section.text},
        ]
        if stable_context:
            messages.append({"role": "user", "content": stable_context})
        if dynamic_context:
            messages.append({"role": "user", "content": dynamic_context})
        messages.append({"role": "user", "content": current_request})

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

        messages = self._attach_image_attachments(
            messages,
            workspace_root=workspace["rootPath"],
            current_message_metadata=current_message_metadata,
            recent_messages=recent_messages if include_history else [],
        )

        stable_prefix_tokens = self._stable_prefix_tokens(messages)
        message_tokens = max(0, budget_result.stats["estimatedTokens"] - tool_schema_tokens)
        stats = {
            **budget_result.stats,
            "toolSchemaTokens": tool_schema_tokens,
            "messageTokens": message_tokens,
            "stablePrefixTokens": stable_prefix_tokens,
            "stablePrefixSections": [section.name for section in stable_context_sections],
            "dynamicTailSections": [section.name for section in dynamic_context_sections],
            "promptCache": {
                "enabled": bool(cache_policy.get("enabled")),
                "includeKeyFiles": bool(cache_policy.get("includeKeyFiles")),
                "includeStableWorkspaceContext": bool(cache_policy.get("includeStableWorkspaceContext")),
                "targetFillRatio": cache_policy.get("targetFillRatio"),
                "targetContextTokens": cache_policy.get("targetContextTokens"),
                "maxStableContextTokens": cache_policy.get("maxStableContextTokens"),
                "stablePrefixTokens": stable_prefix_tokens,
                "stablePrefixSections": [section.name for section in stable_context_sections],
                "dynamicTailSections": [section.name for section in dynamic_context_sections],
            },
            "promptLayers": prompt_layers,
        }
        return (
            messages,
            stats,
        )

    def _attach_image_attachments(
        self,
        messages: list[dict[str, Any]],
        *,
        workspace_root: str,
        current_message_metadata: dict[str, Any] | None,
        recent_messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        image_attachments = self._current_message_image_attachments(
            workspace_root=workspace_root,
            metadata=current_message_metadata,
        )
        history_image_attachments = self._historical_message_image_attachments(
            workspace_root=workspace_root,
            messages=recent_messages,
            exclude_paths={str(attachment.get("path") or "") for attachment in image_attachments},
        )
        if not image_attachments and not history_image_attachments:
            return messages
        next_messages = list(messages)
        if history_image_attachments:
            self._attach_images_to_first_context_message(next_messages, history_image_attachments)
        if image_attachments:
            self._attach_images_to_last_user_message(next_messages, image_attachments)
        return next_messages

    @staticmethod
    def _attach_images_to_first_context_message(
        messages: list[dict[str, Any]],
        image_attachments: list[dict[str, Any]],
    ) -> None:
        for index, message in enumerate(messages):
            if message.get("role") != "user":
                continue
            if "Recent conversation:" not in str(message.get("content") or ""):
                continue
            next_message = dict(message)
            next_message["imageAttachments"] = image_attachments
            messages[index] = next_message
            return

    @staticmethod
    def _attach_images_to_last_user_message(
        messages: list[dict[str, Any]],
        image_attachments: list[dict[str, Any]],
    ) -> None:
        for index in range(len(messages) - 1, -1, -1):
            if messages[index].get("role") != "user":
                continue
            message = dict(messages[index])
            message["imageAttachments"] = image_attachments
            messages[index] = message
            return

    def _current_message_image_attachments(
        self,
        *,
        workspace_root: str,
        metadata: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        if not isinstance(metadata, dict):
            return []
        root = Path(workspace_root).resolve()
        inline_references = set(self._metadata_string_references(metadata, key="fileReferences"))
        values = self._image_reference_values(metadata)
        attachments: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in values:
            if len(attachments) >= IMAGE_ATTACHMENT_MAX_COUNT:
                break
            if not isinstance(item, str):
                continue
            reference = self._clean_file_reference(item)
            if not reference or reference in seen:
                continue
            seen.add(reference)
            attachment = self._image_attachment_from_reference(
                root,
                reference,
                allow_external=reference not in inline_references,
            )
            if attachment is not None:
                attachments.append(attachment)
        return attachments

    def _historical_message_image_attachments(
        self,
        *,
        workspace_root: str,
        messages: list[dict[str, Any]],
        exclude_paths: set[str],
    ) -> list[dict[str, Any]]:
        root = Path(workspace_root).resolve()
        attachments: list[dict[str, Any]] = []
        seen = {path for path in exclude_paths if path}
        for message in reversed(messages):
            if message.get("role") != "user":
                continue
            metadata = self._message_metadata(message)
            for reference in self._image_reference_values(metadata):
                if len(attachments) >= IMAGE_ATTACHMENT_MAX_COUNT:
                    return list(reversed(attachments))
                if not isinstance(reference, str):
                    continue
                cleaned = self._clean_file_reference(reference)
                if not cleaned:
                    continue
                attachment = self._image_attachment_from_reference(root, cleaned)
                if attachment is None:
                    continue
                path = str(attachment.get("path") or "")
                if path in seen:
                    continue
                seen.add(path)
                attachments.append(attachment)
        return list(reversed(attachments))

    @staticmethod
    def _image_reference_values(metadata: dict[str, Any]) -> list[Any]:
        values: list[Any] = []
        for key in IMAGE_ATTACHMENT_METADATA_KEYS:
            value = metadata.get(key)
            if isinstance(value, list):
                values.extend(value)
            elif isinstance(value, str):
                values.append(value)
        return values

    def _current_external_attachment_sections(
        self,
        *,
        workspace_root: str,
        metadata: dict[str, Any] | None,
        policy: dict[str, Any],
    ) -> list[BudgetSection]:
        if not isinstance(metadata, dict):
            return []
        root = Path(workspace_root).resolve()
        references = self._current_external_attachment_references(metadata)
        if not references:
            return []
        max_files = self._policy_int(policy, "externalAttachments", 4)
        max_bytes = self._policy_int(policy, "externalAttachmentMaxBytes", EXTERNAL_ATTACHMENT_MAX_BYTES)
        max_total = self._policy_int(policy, "externalAttachmentsTotalMaxBytes", EXTERNAL_ATTACHMENT_TOTAL_MAX_BYTES)
        sections: list[BudgetSection] = []
        total_bytes = 0
        for reference in references:
            if len(sections) >= max_files or total_bytes >= max_total:
                break
            resolved = self._resolve_external_attachment(root, reference)
            if resolved is None or self._image_mime_type(resolved.name) is not None:
                continue
            remaining = max_total - total_bytes
            text = self._read_referenced_file(resolved, max_bytes=min(max_bytes, remaining))
            if not text:
                continue
            total_bytes += len(text.encode("utf-8", errors="replace"))
            display_path = resolved.as_posix()
            sections.append(
                BudgetSection(
                    name=f"external_attachment:{display_path}",
                    text="\n".join(
                        [
                            f"Attached external file content: {display_path}",
                            "```text",
                            text,
                            "```",
                        ]
                    ),
                    priority=935,
                    minimum_tokens=48,
                )
            )
        return sections

    def _current_external_attachment_references(self, metadata: dict[str, Any]) -> list[str]:
        inline_references = set(self._metadata_string_references(metadata, key="fileReferences"))
        references: list[str] = []
        for reference in self._metadata_string_references(metadata, key="attachments"):
            if reference in inline_references or reference in references:
                continue
            references.append(reference)
        return references

    def _resolve_external_attachment(self, root: Path, reference: str) -> Path | None:
        try:
            candidate = Path(reference)
            if not candidate.is_absolute():
                return None
            resolved = candidate.resolve()
        except (OSError, RuntimeError):
            return None
        try:
            resolved.relative_to(root)
        except ValueError:
            pass
        else:
            return None
        if not resolved.is_file():
            return None
        return resolved

    def _image_attachment_from_reference(
        self,
        root: Path,
        reference: str,
        *,
        allow_external: bool = False,
    ) -> dict[str, Any] | None:
        try:
            candidate = Path(reference)
            resolved = candidate.resolve() if candidate.is_absolute() else (root / reference).resolve()
        except (OSError, RuntimeError):
            return None
        inside_workspace = True
        try:
            resolved.relative_to(root)
        except ValueError:
            inside_workspace = False
        if not inside_workspace and not allow_external:
            return None
        if not resolved.is_file():
            return None
        mime_type = self._image_mime_type(resolved.name)
        if mime_type is None:
            return None
        try:
            size = resolved.stat().st_size
        except OSError:
            return None
        if size <= 0 or size > IMAGE_ATTACHMENT_MAX_BYTES:
            return None
        try:
            data = base64.b64encode(resolved.read_bytes()).decode("ascii")
        except OSError:
            return None
        try:
            display_path = resolved.relative_to(root).as_posix()
        except ValueError:
            display_path = resolved.as_posix()
        return {
            "source": "base64",
            "path": display_path,
            "mimeType": mime_type,
            "data": data,
            "sizeBytes": size,
        }

    @staticmethod
    def _image_mime_type(name: str) -> str | None:
        suffix = Path(name).suffix.lower()
        if suffix in IMAGE_ATTACHMENT_MIME_TYPES:
            return IMAGE_ATTACHMENT_MIME_TYPES[suffix]
        guessed, _encoding = mimetypes.guess_type(name)
        return guessed if guessed in IMAGE_ATTACHMENT_MIME_TYPES.values() else None

    def _metadata_string_references(self, metadata: dict[str, Any], *, key: str) -> list[str]:
        value = metadata.get(key)
        values = value if isinstance(value, list) else [value] if isinstance(value, str) else []
        references: list[str] = []
        for item in values:
            if not isinstance(item, str):
                continue
            reference = self._clean_file_reference(item)
            if reference and reference not in references:
                references.append(reference)
        return references

    def _max_context_tokens(self, config: dict[str, Any]) -> int:
        provider_config = config.get("provider") or {}
        value = provider_config.get("maxContextTokens") or config.get("maxContextTokens")
        if value is None:
            return DEFAULT_MAX_CONTEXT_TOKENS
        try:
            return max(1, int(value))
        except (TypeError, ValueError):
            return DEFAULT_MAX_CONTEXT_TOKENS

    def _prompt_cache_policy(self, config: dict[str, Any], *, lightweight: bool) -> dict[str, Any]:
        provider_config = config.get("provider") if isinstance(config.get("provider"), dict) else {}
        raw = provider_config.get("promptCache")
        raw = raw if isinstance(raw, dict) else {}
        merged = {**self._DEFAULT_PROMPT_CACHE_POLICY, **raw}
        max_context_tokens = self._max_context_tokens(config)
        enabled = bool(merged.get("enabled", True))
        ratio = self._bounded_float(merged.get("targetFillRatio"), 0.0, 0.98, 0.92)
        max_stable = self._bounded_int(
            merged.get("maxStableContextTokens"),
            0,
            max_context_tokens,
            min(240000, max_context_tokens),
        )
        target_context_tokens = min(max_context_tokens, int(max_context_tokens * ratio))
        policy = {
            **merged,
            "enabled": enabled,
            "targetFillRatio": ratio,
            "targetContextTokens": target_context_tokens,
            "maxStableContextTokens": max_stable,
            "lightweight": lightweight,
        }
        for key in (
            "recentMessages",
            "recentTasks",
            "recentPatches",
            "recentCommands",
            "conversationMessageMaxChars",
            "taskSummaryMaxChars",
            "keyFileMaxBytes",
            "canonicalMemoryMaxChars",
        ):
            policy[key] = self._bounded_int(policy.get(key), 1, 1_000_000, self._DEFAULT_PROMPT_CACHE_POLICY[key])
        return policy

    @staticmethod
    def _bounded_int(value: Any, minimum: int, maximum: int, default: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            parsed = default
        return max(minimum, min(maximum, parsed))

    @staticmethod
    def _bounded_float(value: Any, minimum: float, maximum: float, default: float) -> float:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            parsed = default
        return max(minimum, min(maximum, parsed))

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

    def _key_file_sections(
        self,
        workspace_root: str,
        *,
        cache_policy: dict[str, Any] | None = None,
    ) -> list[BudgetSection]:
        root = Path(workspace_root)
        if not root.exists() or not root.is_dir():
            return []

        sections: list[BudgetSection] = []
        max_bytes = self._KEY_FILE_MAX_BYTES
        if isinstance(cache_policy, dict) and cache_policy.get("enabled"):
            max_bytes = self._bounded_int(
                cache_policy.get("keyFileMaxBytes"),
                self._KEY_FILE_MAX_BYTES,
                256000,
                self._KEY_FILE_MAX_BYTES,
            )
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

    @classmethod
    def _is_stable_prefix_section(cls, section: BudgetSection) -> bool:
        return section.name in cls._STABLE_PREFIX_SECTION_NAMES or section.name.startswith("key_file:")

    @staticmethod
    def _context_layer_text(marker: str, sections: list[BudgetSection]) -> str:
        text = "\n\n".join(section.text for section in sections if section.text)
        return f"{marker}\n{text}" if text else ""

    @classmethod
    def _stable_prefix_tokens(cls, messages: list[dict[str, Any]]) -> int:
        if not messages:
            return 0
        total = 0
        for index, message in enumerate(messages):
            content = str(message.get("content") or "")
            if index > 0 and (
                content.startswith(cls._DYNAMIC_CONTEXT_MARKER) or content.startswith(cls._CURRENT_REQUEST_MARKER)
            ):
                break
            total += estimate_tokens(content)
        return total

    def _stable_workspace_context_pack(
        self,
        workspace_root: str,
        *,
        cache_policy: dict[str, Any],
        reserved_tokens: int,
    ) -> BudgetSection | None:
        if not cache_policy.get("enabled"):
            return None
        root = Path(workspace_root)
        if not root.exists() or not root.is_dir():
            return None
        target = int(cache_policy.get("targetContextTokens") or 0)
        max_stable = int(cache_policy.get("maxStableContextTokens") or 0)
        allowance = min(max_stable, max(0, target - max(0, reserved_tokens)))
        if allowance < 512:
            return None

        ignore_names = {
            ".git",
            ".venv",
            "__pycache__",
            "node_modules",
            "dist",
            "build",
            ".pytest_cache",
        }
        ignored_file_names = {
            ".env",
            ".env.local",
            ".env.production",
            ".npmrc",
            ".pypirc",
            "id_rsa",
            "id_dsa",
            "id_ecdsa",
            "id_ed25519",
        }
        file_limit = min(80, max(8, allowance // 500))
        bytes_per_file = min(16000, max(1200, (allowance * 4) // max(1, file_limit)))
        candidates: list[Path] = []
        try:
            queue: deque[Path] = deque([root])
            while queue and len(candidates) < file_limit * 4:
                current = queue.popleft()
                try:
                    children = sorted(current.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower()))
                except OSError:
                    continue
                for path in children:
                    if path.name in ignore_names or path.name.startswith(".pytest"):
                        continue
                    if path.is_dir():
                        queue.append(path)
                        continue
                    if len(candidates) >= file_limit * 4:
                        break
                    if not path.is_file():
                        continue
                    relative = path.relative_to(root)
                    if any(part in ignore_names or part.startswith(".pytest") for part in relative.parts):
                        continue
                    if path.name.lower() in ignored_file_names:
                        continue
                    lowered_relative = relative.as_posix().lower()
                    if any(marker in lowered_relative for marker in ("secret", "credential", "private_key", "apikey", "api_key")):
                        continue
                    if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".lock", ".db", ".sqlite", ".pyc"}:
                        continue
                    try:
                        size = path.stat().st_size
                    except OSError:
                        continue
                    if size <= 0 or size > 250000:
                        continue
                    candidates.append(path)
        except OSError:
            return None

        if not candidates:
            return None
        candidates.sort(key=lambda item: self._stable_file_rank(root, item))
        parts = ["Stable workspace context pack:"]
        used_tokens = estimate_tokens(parts[0])
        for path in candidates[:file_limit]:
            try:
                raw = path.read_bytes()
            except OSError:
                continue
            content = raw[:bytes_per_file]
            try:
                text = content.decode("utf-8")
            except UnicodeDecodeError:
                text = content.decode("utf-8", errors="replace")
            if not text.strip():
                continue
            if len(raw) > len(content):
                text = text.rstrip() + f"\n[truncated at {len(content)} bytes]"
            relative = path.relative_to(root).as_posix()
            item_text = f"--- {relative} ---\n{text.strip()}"
            item_tokens = estimate_tokens(item_text)
            if used_tokens + item_tokens > allowance:
                remaining = allowance - used_tokens
                if remaining < 128:
                    break
                item_text = trim_text_to_tokens(item_text, remaining)
                item_tokens = estimate_tokens(item_text)
            parts.append(item_text)
            used_tokens += item_tokens
            if used_tokens >= allowance:
                break

        if len(parts) <= 1:
            return None
        return BudgetSection(
            name="stable_workspace_context",
            text="\n\n".join(parts),
            priority=320,
            minimum_tokens=128,
        )

    @staticmethod
    def _stable_file_rank(root: Path, path: Path) -> tuple[int, int, str]:
        relative = path.relative_to(root).as_posix()
        name = path.name.lower()
        suffix = path.suffix.lower()
        priority = 50
        if name in {"readme.md", "readme", "pyproject.toml", "package.json", "cargo.toml", "go.mod", "makefile"}:
            priority = 0
        elif relative.startswith(("docs/", "runtime/src/", "runtime/tests/", "src/", "app/src/", "shared/src/")):
            priority = 10
        elif suffix in {".py", ".ts", ".tsx", ".js", ".jsx", ".rs", ".go", ".md", ".toml", ".json", ".yaml", ".yml"}:
            priority = 20
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        return (priority, size, relative.lower())

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
            self._base_role_prompt(),
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
    ) -> tuple[str, list[dict[str, Any]], str]:
        """Compose runtime-owned safety with user-configurable soul layers."""
        sections: list[tuple[str, str, dict[str, Any]]] = []
        effective_role = (role or "root").lower()
        role_prompt = self._role_prompt(effective_role)
        sections.append(("role", self._base_role_prompt(), {"role": "base"}))

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
        if effective_role != "root" and role_prompt:
            prompt_layers.append({
                "name": "runtime_role",
                "tokenEstimate": estimate_tokens(role_prompt),
                "role": effective_role,
                "dynamic": True,
            })
        return "\n\n".join(text for _name, text, _metadata in sections if text), prompt_layers, role_prompt if effective_role != "root" else ""

    def _base_role_prompt(self) -> str:
        return "You are a local coding agent operating in a user-controlled desktop runtime."

    def _role_prompt(self, role: str | None = None) -> str:
        effective_role = (role or "root").lower()
        lines: list[str] = []
        if effective_role == "root":
            lines.append(self._base_role_prompt())
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
        """Lightweight workspace summary without project memory.

        Keep this section stable across turns so provider-side prefix caching
        has a better chance to hit. Volatile top-level listings belong in
        explicit tools or refresh hints rather than the base prompt prefix.
        """
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
            has_entries = any(root.iterdir())
        except OSError as exc:
            lines.append(f"- status: Workspace root is not accessible: {exc}")
            return "\n".join(lines)

        if not has_entries:
            lines.append("- status: Workspace root is accessible but empty.")
            return "\n".join(lines)

        lines.append("- status: Workspace root is accessible and non-empty.")
        lines.append("- note: inspect concrete files via tools when needed.")
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

    def _canonical_memory_section(
        self,
        workspace_root: str,
        *,
        cache_policy: dict[str, Any] | None = None,
    ) -> BudgetSection | None:
        root = Path(workspace_root)
        if not root.exists() or not root.is_dir():
            return None

        file_names = list(DEFAULT_CANONICAL_MEMORY_FILES)
        claude_file = root / ".claude" / "CLAUDE.md"
        files: list[Path] = []
        files.extend(root / name for name in file_names)
        files.append(claude_file)

        sections: list[str] = []
        max_chars = 3000
        if isinstance(cache_policy, dict) and cache_policy.get("enabled"):
            max_chars = self._bounded_int(
                cache_policy.get("canonicalMemoryMaxChars"),
                3000,
                256000,
                3000,
            )
        for path in files:
            if not path.exists() or not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                try:
                    text = path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
            normalized = text.strip()
            if not normalized:
                continue
            if len(normalized) > max_chars:
                normalized = normalized[: max(1, max_chars - 50)].rstrip() + "\n[truncated]"
            sections.append(f"--- {path.relative_to(root).as_posix()} ---\n{normalized}")

        if not sections:
            return None

        return BudgetSection(
            name="canonical_memory",
            text="Canonical memory:\n" + "\n\n".join(sections),
            priority=975,
            truncatable=False,
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
        if not self._is_git_repository(root):
            return "Git status summary:\n- unavailable: not a git repository."

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

    def _is_git_repository(self, root: Path) -> bool:
        # Treat only the workspace root itself as the Git boundary. A scratch
        # workspace can live under this repo during tests or local runs, and it
        # should not inherit the parent repo's status.
        return (root / ".git").exists()

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
