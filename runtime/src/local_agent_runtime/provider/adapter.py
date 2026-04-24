from __future__ import annotations

import os
from urllib.parse import urlsplit, urlunsplit
from collections.abc import Iterator
from typing import Any

from .openai_compatible import (
    HttpPost,
    HttpStream,
    OpenAICompatibleChatClient,
    OpenAICompatibleSettings,
    ProviderAdapterError,
)


OPENAI_COMPATIBLE_MODES = {
    "openai",
    "openai-compatible",
    "openai_compatible",
    "openai-compatible-chat",
}


class ProviderAdapter:
    """Provider facade with deterministic fallback for local/test flows."""

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        *,
        http_post: HttpPost | None = None,
        http_stream: HttpStream | None = None,
        environ: dict[str, str] | None = None,
    ) -> None:
        self._config = config or {}
        self._environ = environ if environ is not None else os.environ
        self._openai_client = OpenAICompatibleChatClient(http_post=http_post, http_stream=http_stream)

    def generate(self, prompt: str, context: dict[str, Any]) -> dict[str, Any]:
        if self._real_provider_enabled(context):
            messages = context.get("messages")
            if not isinstance(messages, list) or not messages:
                messages = [{"role": "user", "content": prompt}]
            tools = self._normalize_tools(context.get("openai_tools") or context.get("tools"))
            provider_response = self.chat(
                messages=messages,
                tools=tools,
                context=context,
            )
            assistant_message = provider_response["message"]
            response = {
                "message": provider_response["message"]["content"],
                "assistant_message": provider_response["message"],
                "tool_calls": assistant_message["tool_calls"],
                "finish_reason": provider_response["finish_reason"],
                "raw": provider_response["raw"],
                "prompt": prompt,
                "context": context,
            }
            if not assistant_message["tool_calls"]:
                response["final"] = assistant_message["content"]
                response["final_answer"] = assistant_message["content"]
            return response

        return {
            "message": self.summarize_findings(
                goal=prompt,
                context=context,
                tool_results=[],
            ),
            "prompt": prompt,
            "context": context,
        }

    def chat(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        settings = self._resolve_settings(context)
        if settings is None:
            goal = self._last_user_message(messages)
            fallback_context = context or self._fallback_context()
            return {
                "message": {
                    "role": "assistant",
                    "content": self.summarize_findings(
                        goal=goal,
                        context=fallback_context,
                        tool_results=[],
                    ),
                    "tool_calls": [],
                },
                "finish_reason": "fallback",
                "raw": {"id": None, "model": None, "usage": None},
            }

        return self._openai_client.chat(
            settings=settings,
            messages=messages,
            tools=tools,
        )

    def chat_stream(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        context: dict[str, Any] | None = None,
    ) -> Iterator[dict[str, Any]]:
        settings = self._resolve_settings(context)
        if settings is None:
            response = self.chat(messages=messages, tools=tools, context=context)
            content = response["message"]["content"]
            if content:
                yield {"type": "content_delta", "delta": content}
            yield {"type": "finish_reason", "finish_reason": response["finish_reason"]}
            yield {"type": "final", "response": response}
            return

        yield from self._openai_client.stream(
            settings=settings,
            messages=messages,
            tools=tools,
        )

    def stream(self, prompt: str, context: dict[str, Any]) -> Iterator[dict[str, Any]]:
        messages = context.get("messages")
        if not isinstance(messages, list) or not messages:
            messages = [{"role": "user", "content": prompt}]
        tools = self._normalize_tools(context.get("openai_tools") or context.get("tools"))
        yield from self.chat_stream(messages=messages, tools=tools, context=context)

    def _normalize_tools(self, tools: Any) -> list[dict[str, Any]] | None:
        if not isinstance(tools, list):
            return None

        normalized: list[dict[str, Any]] = []
        for tool in tools:
            if not isinstance(tool, dict):
                continue
            if tool.get("type") == "function" and isinstance(tool.get("function"), dict):
                normalized.append(tool)
                continue
            name = tool.get("name")
            if not isinstance(name, str) or not name:
                continue
            normalized.append(
                {
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": tool.get("description") or "",
                        "parameters": tool.get("input_schema") or tool.get("parameters") or {"type": "object"},
                    },
                }
            )
        return normalized or None

    def choose_tool_sequence(self, goal: str, context: dict[str, Any]) -> list[dict[str, Any]]:
        search_config = context.get("search_config", {})
        route = self._route_goal(goal)
        sequence: list[dict[str, Any]] = [
            {
                "name": "list_dir",
                "arguments": {
                    "workspaceRoot": context["workspace_root"],
                    "path": ".",
                    "recursive": False,
                    "max_depth": 2,
                    "ignore": search_config.get("ignore", []),
                },
                "plan_step_id": "inspect-workspace",
                "start_token": f"Inspecting the top-level structure of {context['workspace_name']}...",
            }
        ]

        if route["kind"] == "run_command":
            sequence.append(
                {
                    "name": "run_command",
                    "arguments": {
                        "workspaceRoot": context["workspace_root"],
                        "cwd": ".",
                        "command": route["value"],
                    },
                    "plan_step_id": "run-command",
                    "start_token": f"Preparing to run command: {route['value']}",
                }
            )
            return sequence

        if route["kind"] == "apply_patch":
            sequence.append(
                {
                    "name": "apply_patch",
                    "arguments": {
                        "workspaceRoot": context["workspace_root"],
                        "patchText": route["value"],
                        "dry_run": False,
                    },
                    "plan_step_id": "apply-patch",
                    "start_token": "Preparing to apply the explicit patch...",
                }
            )
            return sequence

        if route["kind"] == "git_status":
            sequence.append(
                {
                    "name": "git_status",
                    "arguments": {
                        "workspaceRoot": context["workspace_root"],
                    },
                    "plan_step_id": "git-status",
                    "start_token": "Checking git status...",
                }
            )
            return sequence

        if route["kind"] == "git_diff":
            sequence.append(
                {
                    "name": "git_diff",
                    "arguments": {
                        "workspaceRoot": context["workspace_root"],
                    },
                    "plan_step_id": "git-diff",
                    "start_token": "Inspecting git diff...",
                }
            )
            return sequence

        search_query = context.get("search_query", "")
        if search_query:
            sequence.append(
                {
                    "name": "search_files",
                    "arguments": {
                        "workspaceRoot": context["workspace_root"],
                        "query": search_query,
                        "mode": context.get("search_mode", "content"),
                        "glob": search_config.get("glob", []),
                        "ignore": search_config.get("ignore", []),
                        "max_results": 8,
                    },
                    "plan_step_id": "search-relevant-files",
                    "start_token": f"Searching for files related to: {search_query}",
                }
            )
        return sequence

    def summarize_findings(
        self,
        goal: str,
        context: dict[str, Any],
        tool_results: list[dict[str, Any]],
    ) -> str:
        list_dir_result = self._find_tool_result(tool_results, "list_dir")
        search_result = self._find_tool_result(tool_results, "search_files")
        read_result = self._find_tool_result(tool_results, "read_file")
        command_result = self._find_tool_result(tool_results, "run_command")
        patch_result = self._find_tool_result(tool_results, "apply_patch")
        git_status_result = self._find_tool_result(tool_results, "git_status")
        git_diff_result = self._find_tool_result(tool_results, "git_diff")

        directory_hint = self._describe_directory(list_dir_result)
        search_hint = self._describe_search(search_result)
        read_hint = self._describe_file(read_result)
        command_hint = self._describe_command(command_result)
        patch_hint = self._describe_patch(patch_result)
        git_status_hint = self._describe_git_status(git_status_result)
        git_diff_hint = self._describe_git_diff(git_diff_result)

        parts = [
            f"Completed an initial pass over workspace {context['workspace_name']}.",
            directory_hint,
        ]
        if search_hint:
            parts.append(search_hint)
        if read_hint:
            parts.append(read_hint)
        if command_hint:
            parts.append(command_hint)
            parts.append("Next step: review command output and decide whether a follow-up code change is needed.")
        elif patch_hint:
            parts.append(patch_hint)
            parts.append("Next step: confirm the patch outcome and check whether another edit is needed.")
        elif git_status_hint:
            parts.append(git_status_hint)
            parts.append("Next step: inspect the modified files if the status needs follow-up.")
        elif git_diff_hint:
            parts.append(git_diff_hint)
            parts.append("Next step: inspect the diff for correctness or missing edits.")
        else:
            parts.append(f"Next step: inspect the most relevant implementation file for goal '{goal}'.")
        return " ".join(part for part in parts if part)

    def pick_follow_up_tool(
        self,
        context: dict[str, Any],
        tool_results: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        search_result = self._find_tool_result(tool_results, "search_files")
        if not search_result:
            return None

        matches = search_result.get("result", {}).get("matches", [])
        if not matches:
            return None

        first_match = matches[0]
        path = first_match.get("path")
        if not path:
            return None

        return {
            "name": "read_file",
            "arguments": {
                "workspaceRoot": context["workspace_root"],
                "path": path,
                "max_bytes": 4000,
            },
            "plan_step_id": "search-relevant-files",
            "start_token": f"Reading the first relevant file: {path}",
        }

    def _find_tool_result(self, tool_results: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
        for item in tool_results:
            if item["name"] == name:
                return item
        return None

    def _real_provider_enabled(self, context: dict[str, Any] | None) -> bool:
        return self._resolve_settings(context) is not None

    def _resolve_settings(self, context: dict[str, Any] | None) -> OpenAICompatibleSettings | None:
        provider_config = self._merged_provider_config(context)
        mode = self._string_value(provider_config, "mode", "providerMode") or self._env(
            "LOCAL_AGENT_PROVIDER_MODE",
            "YUANBAO_PROVIDER_MODE",
        )
        configured_env_var_name = self._string_value(
            provider_config,
            "apiKeyEnvVarName",
            "api_key_env_var_name",
            "envKey",
            "env_key",
        )
        uses_anthropic_env = self._uses_anthropic_env(configured_env_var_name)
        if self._normalize_mode(mode) not in OPENAI_COMPATIBLE_MODES:
            if not self._anthropic_env_available():
                return None
            uses_anthropic_env = True

        api_key = self._string_value(provider_config, "apiKey", "api_key")
        if not api_key and configured_env_var_name:
            api_key = self._env(configured_env_var_name)
        if not api_key:
            api_key = self._env(
                "LOCAL_AGENT_PROVIDER_API_KEY",
                "LOCAL_AGENT_OPENAI_API_KEY",
                "OPENAI_API_KEY",
                "ANTHROPIC_AUTH_TOKEN",
            )
        if not api_key:
            return None

        raw_base_url = self._string_value(provider_config, "baseUrl", "base_url")
        base_url_source = "config" if raw_base_url else None
        if not raw_base_url:
            raw_base_url = self._env("LOCAL_AGENT_PROVIDER_BASE_URL", "OPENAI_BASE_URL")
            base_url_source = "openai_env" if raw_base_url else base_url_source
        if not raw_base_url:
            raw_base_url = self._env("ANTHROPIC_BASE_URL")
            base_url_source = "anthropic_env" if raw_base_url else base_url_source
        base_url = self._normalize_base_url(
            raw_base_url or "https://api.openai.com/v1",
            append_v1=uses_anthropic_env or base_url_source == "anthropic_env",
        )
        model = (
            self._string_value(provider_config, "model", "defaultModel")
            or self._env(
                "LOCAL_AGENT_PROVIDER_MODEL",
                "OPENAI_MODEL",
                "ANTHROPIC_MODEL",
                "ANTHROPIC_DEFAULT_SONNET_MODEL",
                "ANTHROPIC_DEFAULT_OPUS_MODEL",
                "ANTHROPIC_DEFAULT_HAIKU_MODEL",
            )
            or "gpt-5-codex"
        )
        temperature = self._float_value(provider_config, "temperature")
        if temperature is None:
            temperature = self._float_env("LOCAL_AGENT_PROVIDER_TEMPERATURE", "OPENAI_TEMPERATURE")
        max_tokens = self._int_value(provider_config, "maxTokens", "max_tokens", "maxOutputTokens")
        if max_tokens is None:
            max_tokens = self._int_env("LOCAL_AGENT_PROVIDER_MAX_TOKENS", "OPENAI_MAX_TOKENS")
        return OpenAICompatibleSettings(
            base_url=base_url,
            api_key=api_key,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=self._timeout_value(provider_config),
        )

    def _anthropic_env_available(self) -> bool:
        return bool(self._env("ANTHROPIC_AUTH_TOKEN") and self._env("ANTHROPIC_BASE_URL"))

    def _uses_anthropic_env(self, configured_env_var_name: str | None) -> bool:
        return bool(configured_env_var_name and configured_env_var_name.upper().startswith("ANTHROPIC_"))

    def _normalize_base_url(self, base_url: str, *, append_v1: bool = False) -> str:
        trimmed = base_url.rstrip("/")
        if not append_v1:
            return trimmed
        if trimmed.endswith("/v1") or trimmed.endswith("/chat/completions"):
            return trimmed
        parsed = urlsplit(trimmed)
        if parsed.path in {"", "/"}:
            return urlunsplit((parsed.scheme, parsed.netloc, "/v1", "", ""))
        return trimmed

    def _merged_provider_config(self, context: dict[str, Any] | None) -> dict[str, Any]:
        merged = self._resolve_active_provider_config(self._config.get("provider", self._config))
        adapter_provider = self._config.get("provider", self._config)
        if isinstance(adapter_provider, dict):
            merged.update(self._resolve_active_provider_config(adapter_provider))
        context_config = context.get("config") if context else None
        if isinstance(context_config, dict):
            context_provider = context_config.get("provider", {})
            if isinstance(context_provider, dict):
                merged.update(self._resolve_active_provider_config(context_provider))
        return merged

    def _resolve_active_provider_config(self, provider_config: Any) -> dict[str, Any]:
        if not isinstance(provider_config, dict):
            return {}

        ignored_keys = {
            "profiles",
            "activeProfileId",
            "lastCheckedAt",
            "lastStatus",
            "lastErrorSummary",
        }
        resolved = {
            key: value
            for key, value in provider_config.items()
            if key not in ignored_keys
        }
        profiles = provider_config.get("profiles")
        active_profile_id = provider_config.get("activeProfileId")
        if isinstance(profiles, list) and profiles:
            active_profile = None
            if isinstance(active_profile_id, str) and active_profile_id:
                active_profile = next(
                    (
                        profile
                        for profile in profiles
                        if isinstance(profile, dict) and profile.get("id") == active_profile_id
                    ),
                    None,
                )
            if active_profile is None:
                active_profile = next((profile for profile in profiles if isinstance(profile, dict)), None)
            if isinstance(active_profile, dict):
                resolved.update({
                    key: value
                    for key, value in active_profile.items()
                    if key not in ignored_keys
                })
        return resolved

    def _env(self, *names: str) -> str | None:
        for name in names:
            value = self._environ.get(name)
            if value:
                return value
        return None

    def _string_value(self, source: dict[str, Any], *names: str) -> str | None:
        for name in names:
            value = source.get(name)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    def _float_value(self, source: dict[str, Any], *names: str) -> float | None:
        for name in names:
            value = source.get(name)
            if value is None:
                continue
            try:
                return float(value)
            except (TypeError, ValueError) as exc:
                raise ProviderAdapterError(f"Invalid provider setting {name}: expected number") from exc
        return None

    def _float_env(self, *names: str) -> float | None:
        value = self._env(*names)
        if value is None:
            return None
        try:
            return float(value)
        except ValueError as exc:
            raise ProviderAdapterError(f"Invalid provider env {names[0]}: expected number") from exc

    def _int_value(self, source: dict[str, Any], *names: str) -> int | None:
        for name in names:
            value = source.get(name)
            if value is None:
                continue
            try:
                return int(value)
            except (TypeError, ValueError) as exc:
                raise ProviderAdapterError(f"Invalid provider setting {name}: expected integer") from exc
        return None

    def _int_env(self, *names: str) -> int | None:
        value = self._env(*names)
        if value is None:
            return None
        try:
            return int(value)
        except ValueError as exc:
            raise ProviderAdapterError(f"Invalid provider env {names[0]}: expected integer") from exc

    def _timeout_value(self, source: dict[str, Any]) -> float:
        seconds = self._float_value(source, "timeout", "timeoutSeconds")
        if seconds is not None:
            return seconds
        timeout_ms = self._float_value(source, "timeoutMs")
        if timeout_ms is not None:
            return timeout_ms / 1000
        env_timeout = self._env("LOCAL_AGENT_PROVIDER_TIMEOUT", "OPENAI_TIMEOUT")
        if env_timeout:
            try:
                return float(env_timeout)
            except ValueError as exc:
                raise ProviderAdapterError("Invalid provider timeout env: expected number") from exc
        return 30.0

    def _normalize_mode(self, mode: str | None) -> str:
        return (mode or "").strip().lower()

    def _last_user_message(self, messages: list[dict[str, Any]]) -> str:
        for message in reversed(messages):
            if message.get("role") == "user":
                content = message.get("content")
                if isinstance(content, str):
                    return content
        return ""

    def _fallback_context(self) -> dict[str, Any]:
        return {
            "workspace_name": "workspace",
            "workspace_root": ".",
            "search_config": {"ignore": []},
        }

    def _route_goal(self, goal: str) -> dict[str, str]:
        lowered = goal.lower().strip()
        for kind, prefixes in (
            ("run_command", ("run command:", "execute command:", "cmd:")),
            ("apply_patch", ("apply patch:",)),
            ("git_status", ("show git status", "git status:")),
            ("git_diff", ("show git diff", "git diff:")),
        ):
            for prefix in prefixes:
                if lowered.startswith(prefix):
                    value = goal[len(prefix) :].strip()
                    return {"kind": kind, "value": value}
        return {"kind": "search", "value": ""}

    def _describe_directory(self, result: dict[str, Any] | None) -> str:
        if not result:
            return "Directory structure was not collected."
        items = result.get("result", {}).get("items", [])
        if not items:
            return "The top-level directory appears empty or has no visible entries."

        labels = [item.get("path") or item.get("name") for item in items[:5]]
        visible = [label for label in labels if label]
        if not visible:
            return "Collected directory metadata, but there are no visible entries to display."
        return f"Top-level entries include: {', '.join(visible)}."

    def _describe_search(self, result: dict[str, Any] | None) -> str:
        if not result:
            return ""
        matches = result.get("result", {}).get("matches", [])
        query = result.get("arguments", {}).get("query", "")
        if not matches:
            return f"No direct matches were found for query '{query}'."
        paths = [match.get("path") for match in matches[:3] if match.get("path")]
        return f"Search for '{query}' returned {len(matches)} match(es), prioritizing: {', '.join(paths)}."

    def _describe_file(self, result: dict[str, Any] | None) -> str:
        if not result:
            return ""
        file_result = result.get("result", {})
        content = (file_result.get("content") or "").strip()
        preview = content.splitlines()[0].strip() if content else ""
        path = file_result.get("path", "unknown file")
        if not preview:
            return f"Read {path}, but the file is empty or returned no preview."
        return f"Read {path}; first line preview: {preview[:120]}."

    def _describe_command(self, result: dict[str, Any] | None) -> str:
        if not result:
            return ""
        command_result = result.get("result", {})
        status = command_result.get("status", "unknown")
        if status == "approval_required":
            approval = command_result.get("approval", {})
            return f"Command is waiting for approval: {approval.get('id', 'unknown approval')}."
        stdout = (command_result.get("stdout") or "").strip()
        stderr = (command_result.get("stderr") or "").strip()
        exit_code = command_result.get("exitCode")
        output_preview = stdout.splitlines()[0] if stdout else stderr.splitlines()[0] if stderr else "no output"
        return f"Command finished with status {status} and exit code {exit_code}; first output: {output_preview[:120]}."

    def _describe_patch(self, result: dict[str, Any] | None) -> str:
        if not result:
            return ""
        patch_result = result.get("result", {})
        summary = (patch_result.get("summary") or "").strip()
        files_changed = patch_result.get("files_changed")
        patch_id = patch_result.get("patch_id", "unknown patch")
        if summary:
            return f"Patch tool reported {patch_id} with {files_changed} file(s) changed: {summary}"
        return f"Patch tool reported {patch_id} with {files_changed} file(s) changed."

    def _describe_git_status(self, result: dict[str, Any] | None) -> str:
        if not result:
            return ""
        git_result = result.get("result", {})
        branch = git_result.get("branch") or "unknown branch"
        changes = git_result.get("changes") or []
        return f"Git status on {branch} reported {len(changes)} change(s)."

    def _describe_git_diff(self, result: dict[str, Any] | None) -> str:
        if not result:
            return ""
        git_result = result.get("result", {})
        diff_text = (git_result.get("diff") or "").strip()
        if not diff_text:
            return "Git diff returned no patch content."
        line_count = len(diff_text.splitlines())
        return f"Git diff returned {line_count} line(s) of patch content."
