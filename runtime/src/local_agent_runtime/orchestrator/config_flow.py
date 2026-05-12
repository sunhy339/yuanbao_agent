"""Config Flow Mixin â extracted from Orchestrator.

Handles provider configuration, testing, and preview.
"""
from __future__ import annotations

import logging
import os
from copy import deepcopy
from typing import Any

logger = logging.getLogger(__name__)


class ConfigFlowMixin:
    """Mixin providing config/provider RPC methods."""

    def _redact_provider_secret(self, text: str, env_var_name: Any, direct_key: Any = None) -> str:
        redacted = text
        if direct_key:
            redacted = redacted.replace(str(direct_key), "[redacted]")
        if env_var_name:
            env_value = os.environ.get(str(env_var_name))
            if env_value:
                redacted = redacted.replace(env_value, "[redacted]")
        return redacted

    def config_effective(self, _params: dict[str, Any]) -> dict[str, Any]:
        """Return the fully resolved runtime configuration."""
        config = self._store.get_config({})["config"]
        provider_profile = self._active_config_profile(config, "provider")
        autonomy_profile = self._active_config_profile(config, "autonomy")
        agent_soul_profile = self._active_config_profile(config, "agentSoul")

        provider_config = config.get("provider") if isinstance(config, dict) else {}
        streaming_mode = (
            str((provider_config or {}).get("mode") or "").strip().lower()
            in {"openai", "openai-compatible", "openai_compatible", "openai-compatible-chat"}
            if hasattr(self._provider, "stream") else False
        )

        return {
            "providerProfile": provider_profile or {},
            "autonomyProfile": autonomy_profile,
            "agentSoulProfile": agent_soul_profile,
            "contextBudget": {
                "maxContextTokens": self._context_builder._max_context_tokens(config),
            },
            "toolPolicy": (config.get("policy") or {}).get("toolPolicy", "strict_whitelist"),
            "memoryPolicy": (config.get("memory") or {}).get("policy", {}),
            "searchMode": config.get("search", {}).get("mode", "hybrid"),
            "streamingEnabled": streaming_mode,
            "activeProfileIds": {
                "provider": (provider_config or {}).get("activeProfileId"),
                "autonomy": (config.get("autonomy") or {}).get("activeProfileId"),
                "agentSoul": (config.get("agentSoul") or {}).get("activeProfileId"),
            },
        }

    def prompt_preview(self, params: dict[str, Any]) -> dict[str, Any]:
        """Preview effective prompt layers without executing a task."""
        config = self._store.get_config({})["config"]
        # Resolve workspace_root: sessionId → workspace, or direct workspaceRoot param
        session_id = params.get("sessionId")
        if session_id:
            session = self._store.require_session(session_id)
            workspace = self._store.require_workspace(session["workspaceId"])
            workspace_root = workspace.get("rootPath", "")
        else:
            workspace_root = params.get("workspaceRoot", "/unknown")
        role = params.get("role")
        skill_id = params.get("skillId")
        skill_preset = self._context_builder._resolve_skill(skill_id) if skill_id else None

        system_text, prompt_layers = self._context_builder._compose_system_prompt(
            workspace_root=workspace_root,
            config=config,
            role=role,
            skill_preset=skill_preset,
        )

        # Mark runtime-owned layers as locked
        for layer in prompt_layers:
            if layer.get("name") == "runtime_safety":
                layer["locked"] = True
                layer["editable"] = False

        return {
            "systemPrompt": system_text,
            "layers": prompt_layers,
            "totalTokenEstimate": sum(l.get("tokenEstimate", 0) for l in prompt_layers),
        }

    def test_provider(self, params: dict[str, Any]) -> dict[str, Any]:
        params = params if isinstance(params, dict) else {}
        provider_patch = params.get("provider") if isinstance(params, dict) else None
        checked_at = self._store.now() if hasattr(self._store, "now") else 0
        config = deepcopy(self._store.get_config({}).get("config", {}))
        provider_root = deepcopy(config.get("provider") or {})
        provider_config, profile_id, profile_name = self._provider_config_for_test(
            provider_root=provider_root,
            profile_id=params.get("profileId"),
            provider_patch=provider_patch if isinstance(provider_patch, dict) else None,
        )
        config["provider"] = provider_config

        mode = str(provider_config.get("mode") or provider_config.get("providerMode") or "").strip()
        normalized_mode = mode.lower()
        model = provider_config.get("model") or provider_config.get("defaultModel")
        base_url = provider_config.get("baseUrl") or provider_config.get("base_url") or "https://api.openai.com/v1"
        env_var_name = (
            provider_config.get("apiKeyEnvVarName")
            or provider_config.get("api_key_env_var_name")
            or provider_config.get("envKey")
            or "LOCAL_AGENT_PROVIDER_API_KEY"
        )

        if normalized_mode in {"", "mock"}:
            result = {
                "ok": True,
                "status": "mocked",
                "message": "Provider is in mock mode; no network request was made.",
                "profileId": profile_id,
                "profileName": profile_name,
                "providerMode": mode or "mock",
                "model": model,
                "baseUrl": base_url,
                "checkedEnvVarName": env_var_name,
                "envVarName": env_var_name,
                "lastCheckedAt": checked_at,
                "lastStatus": "mocked",
                "lastErrorSummary": "Mock mode does not contact a remote model.",
                "source": "runtime",
                "details": {
                    "errorSummary": "Mock mode does not contact a remote model.",
                },
            }
            self._persist_provider_test_result(profile_id, result, persist=provider_patch is None)
            return result
        if normalized_mode not in {"openai", "openai-compatible", "openai_compatible", "openai-compatible-chat"}:
            result = {
                "ok": False,
                "status": "unsupported",
                "message": f"Unsupported provider mode: {mode}",
                "profileId": profile_id,
                "profileName": profile_name,
                "providerMode": mode,
                "model": model,
                "baseUrl": base_url,
                "checkedEnvVarName": env_var_name,
                "envVarName": env_var_name,
                "lastCheckedAt": checked_at,
                "lastStatus": "unsupported",
                "lastErrorSummary": f"Unsupported provider mode: {mode}",
                "source": "runtime",
                "details": {
                    "errorSummary": f"Unsupported provider mode: {mode}",
                },
            }
            self._persist_provider_test_result(profile_id, result, persist=provider_patch is None)
            return result
        direct_key = provider_config.get("apiKey") or provider_config.get("api_key")
        if not direct_key and env_var_name and not os.environ.get(str(env_var_name)):
            result = {
                "ok": False,
                "status": "missing_env",
                "message": f"Environment variable {env_var_name} is not set.",
                "profileId": profile_id,
                "profileName": profile_name,
                "providerMode": mode,
                "model": model,
                "baseUrl": base_url,
                "checkedEnvVarName": env_var_name,
                "envVarName": env_var_name,
                "lastCheckedAt": checked_at,
                "lastStatus": "missing_env",
                "lastErrorSummary": f"Set {env_var_name} in the runtime environment.",
                "source": "runtime",
                "details": {
                    "errorSummary": f"Set {env_var_name} in the runtime environment.",
                },
            }
            self._persist_provider_test_result(profile_id, result, persist=provider_patch is None)
            return result

        try:
            response = self._provider.chat(
                messages=[
                    {
                        "role": "user",
                        "content": "Reply with a short provider connectivity confirmation.",
                    }
                ],
                tools=None,
                context={"config": config},
            )
        except Exception as exc:  # noqa: BLE001
            error_summary = self._redact_provider_secret(str(exc), env_var_name, direct_key)
            result = {
                "ok": False,
                "status": "failed",
                "message": error_summary,
                "profileId": profile_id,
                "profileName": profile_name,
                "providerMode": mode,
                "model": model,
                "baseUrl": base_url,
                "checkedEnvVarName": env_var_name,
                "envVarName": env_var_name,
                "lastCheckedAt": checked_at,
                "lastStatus": "failed",
                "lastErrorSummary": error_summary,
                "source": "runtime",
                "details": {
                    "errorSummary": error_summary,
                    "errorType": type(exc).__name__,
                },
            }
            self._persist_provider_test_result(profile_id, result, persist=provider_patch is None)
            return result

        result = {
            "ok": True,
            "status": "ok",
            "message": "Provider connection succeeded.",
            "profileId": profile_id,
            "profileName": profile_name,
            "providerMode": mode,
            "model": response.get("raw", {}).get("model") or model,
            "baseUrl": base_url,
            "checkedEnvVarName": env_var_name,
            "envVarName": env_var_name,
            "lastCheckedAt": checked_at,
            "lastStatus": "ok",
            "lastErrorSummary": None,
            "source": "runtime",
            "details": {
                "finishReason": response.get("finish_reason"),
                "usage": response.get("raw", {}).get("usage"),
            },
        }
        self._persist_provider_test_result(profile_id, result, persist=provider_patch is None)
        return result

    def _persist_provider_test_result(
        self,
        profile_id: str | None,
        result: dict[str, Any],
        *,
        persist: bool,
    ) -> None:
        if not persist or not profile_id or not hasattr(self._store, "update_provider_profile_health"):
            return
        self._store.update_provider_profile_health(
            profile_id,
            last_checked_at=int(result.get("lastCheckedAt") or 0),
            last_status=str(result.get("lastStatus") or result.get("status") or "failed"),
            last_error_summary=result.get("lastErrorSummary"),
        )

    def _provider_config_for_test(
        self,
        *,
        provider_root: dict[str, Any],
        profile_id: Any,
        provider_patch: dict[str, Any] | None,
    ) -> tuple[dict[str, Any], str | None, str | None]:
        selected = self._select_provider_profile(provider_root, profile_id)
        provider_config = {
            key: deepcopy(value)
            for key, value in provider_root.items()
            if key not in {"profiles", "activeProfileId"}
        }
        selected_profile_id: str | None = None
        selected_profile_name: str | None = None

        if selected is not None:
            provider_config.update(deepcopy(selected))
            selected_profile_id = selected.get("id") if isinstance(selected.get("id"), str) else None
            selected_profile_name = selected.get("name") if isinstance(selected.get("name"), str) else None

        if provider_patch:
            patch_selected = self._select_provider_profile(
                provider_patch,
                provider_patch.get("activeProfileId") or provider_patch.get("profileId"),
            )
            if patch_selected is not None:
                provider_config.update(deepcopy(patch_selected))
                selected_profile_id = patch_selected.get("id") if isinstance(patch_selected.get("id"), str) else selected_profile_id
                selected_profile_name = patch_selected.get("name") if isinstance(patch_selected.get("name"), str) else selected_profile_name
            else:
                for key, value in provider_patch.items():
                    if key not in {"profiles", "activeProfileId", "profileId"}:
                        provider_config[key] = deepcopy(value)
                if isinstance(provider_patch.get("id"), str):
                    selected_profile_id = provider_patch["id"]
                if isinstance(provider_patch.get("name"), str):
                    selected_profile_name = provider_patch["name"]

        return provider_config, selected_profile_id, selected_profile_name

    def _select_provider_profile(self, provider_root: dict[str, Any], profile_id: Any) -> dict[str, Any] | None:
        profiles = provider_root.get("profiles")
        if not isinstance(profiles, list) or not profiles:
            return None

        requested_id = profile_id if isinstance(profile_id, str) and profile_id.strip() else provider_root.get("activeProfileId")
        if isinstance(requested_id, str) and requested_id.strip():
            for profile in profiles:
                if isinstance(profile, dict) and profile.get("id") == requested_id:
                    return profile
        return next((profile for profile in profiles if isinstance(profile, dict)), None)
