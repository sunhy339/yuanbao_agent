from __future__ import annotations

import json
import re
import sqlite3
import time
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any

from ._constants import CONFIG_KEY, DEFAULT_AUTONOMY_PROFILE, DEFAULT_AGENT_SOUL_PROFILE, DEFAULT_CONFIG

class ConfigStoreMixin:
    def get_config(self, _params: dict[str, Any]) -> dict[str, Any]:
        if self._config_snapshot is None:
            self._config_snapshot = deepcopy(self._config)
        return {"config": self._config_snapshot}

    def update_config(self, params: dict[str, Any]) -> dict[str, Any]:
        patch = params.get("config", params)
        if not isinstance(patch, dict):
            raise ValueError("config.update expects an object payload")

        merged = self._merge_config(self._config, patch)
        provider_patch = patch.get("provider") if isinstance(patch.get("provider"), dict) else None
        if isinstance(provider_patch, dict) and "profiles" not in provider_patch:
            self._apply_provider_patch_to_active_profile(merged, provider_patch)
        self._config = self._normalize_config(merged)
        self._config_snapshot = None
        self._persist_config(self._config)
        return {"config": deepcopy(self._config)}

    # -- Feature flags --

    def get_feature_flag(self, key: str, default: bool = False) -> bool:
        """Read a feature flag value from config.features."""
        features = self._config.get("features")
        if not isinstance(features, dict):
            return default
        return bool(features.get(key, default))

    def set_feature_flag(self, key: str, value: bool) -> dict[str, Any]:
        """Set a feature flag value in config.features and persist."""
        features = self._config.get("features")
        if not isinstance(features, dict):
            features = deepcopy(DEFAULT_CONFIG.get("features", {}))
        features[key] = value
        self._config["features"] = features
        self._config_snapshot = None
        self._persist_config(self._config)
        return {"features": deepcopy(features)}

    def list_feature_flags(self) -> dict[str, Any]:
        """Return all feature flags with current values."""
        features = self._config.get("features")
        if not isinstance(features, dict):
            features = deepcopy(DEFAULT_CONFIG.get("features", {}))
        return {"features": deepcopy(features)}

    def update_provider_profile_health(
        self,
        profile_id: str,
        *,
        last_checked_at: int,
        last_status: str,
        last_error_summary: str | None,
    ) -> dict[str, Any]:
        provider = deepcopy(self._config.get("provider"))
        if not isinstance(provider, dict):
            return {"config": deepcopy(self._config)}

        profiles = provider.get("profiles")
        if not isinstance(profiles, list):
            return {"config": deepcopy(self._config)}

        updated = False
        for profile in profiles:
            if not isinstance(profile, dict) or profile.get("id") != profile_id:
                continue
            profile["lastCheckedAt"] = int(last_checked_at)
            profile["lastStatus"] = str(last_status)
            if isinstance(last_error_summary, str) and last_error_summary.strip():
                profile["lastErrorSummary"] = last_error_summary.strip()
            else:
                profile.pop("lastErrorSummary", None)
            updated = True
            break

        if not updated:
            return {"config": deepcopy(self._config)}

        provider["profiles"] = profiles
        self._config = self._normalize_config({
            **deepcopy(self._config),
            "provider": provider,
        })
        self._config_snapshot = None
        self._persist_config(self._config)
        return {"config": deepcopy(self._config)}

    def _merge_config(self, base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
        merged = deepcopy(base)
        for key, value in patch.items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = self._merge_config(merged[key], value)
            elif value is not None:
                merged[key] = deepcopy(value)
        return merged

    def _load_or_initialize_config(self) -> dict[str, Any]:
        row = self._conn.execute(
            "SELECT value FROM config WHERE key = ?",
            (CONFIG_KEY,),
        ).fetchone()
        if row is None:
            config = deepcopy(DEFAULT_CONFIG)
            self._persist_config(config)
            return config

        try:
            loaded = json.loads(row["value"])
        except json.JSONDecodeError:
            loaded = {}

        if not isinstance(loaded, dict):
            loaded = {}

        config = self._normalize_config(self._merge_config(DEFAULT_CONFIG, loaded))
        if config != loaded:
            self._persist_config(config)
        return config

    def _normalize_config(self, config: dict[str, Any]) -> dict[str, Any]:
        normalized = deepcopy(config)
        provider = normalized.get("provider")
        if not isinstance(provider, dict):
            normalized["provider"] = deepcopy(DEFAULT_CONFIG["provider"])
        else:
            normalized["provider"] = self._normalize_provider_config(provider)

        autonomy = normalized.get("autonomy")
        normalized["autonomy"] = self._normalize_autonomy_config(
            autonomy if isinstance(autonomy, dict) else {},
        )
        agent_soul = normalized.get("agentSoul")
        normalized["agentSoul"] = self._normalize_agent_soul_config(
            agent_soul if isinstance(agent_soul, dict) else {},
        )
        return normalized

    def _normalize_autonomy_config(self, autonomy: dict[str, Any]) -> dict[str, Any]:
        normalized = deepcopy(DEFAULT_CONFIG["autonomy"])
        normalized.update(deepcopy(autonomy))
        raw_profiles = normalized.get("profiles")
        profiles = [
            self._normalize_autonomy_profile(item)
            for item in raw_profiles or []
            if isinstance(item, dict)
        ]
        if not profiles:
            profiles = [self._normalize_autonomy_profile(DEFAULT_AUTONOMY_PROFILE)]
        profiles = self._dedupe_profiles(profiles, fallback_prefix="autonomy")
        active_profile_id = self._valid_active_profile_id(
            normalized.get("activeProfileId"),
            profiles,
        )
        normalized["activeProfileId"] = active_profile_id
        normalized["profiles"] = profiles
        return normalized

    def _normalize_autonomy_profile(self, profile: dict[str, Any]) -> dict[str, Any]:
        merged = deepcopy(DEFAULT_AUTONOMY_PROFILE)
        merged.update(deepcopy(profile))
        profile_id = self._safe_profile_id(merged.get("id"), "autonomy")
        name = self._safe_profile_name(merged.get("name"), profile_id)
        return {
            **merged,
            "id": profile_id,
            "name": name,
            "level": str(merged.get("level") or "L2"),
            "maxSteps": self._bounded_int(merged.get("maxSteps"), 1, 200, DEFAULT_AUTONOMY_PROFILE["maxSteps"]),
            "maxParallelSubtasks": self._bounded_int(
                merged.get("maxParallelSubtasks"), 1, 32, DEFAULT_AUTONOMY_PROFILE["maxParallelSubtasks"],
            ),
            "allowBackground": bool(merged.get("allowBackground")),
            "allowSubagents": bool(merged.get("allowSubagents")),
            "allowFileWrite": self._string_or_default(merged.get("allowFileWrite"), "approval_required"),
            "allowShell": self._string_or_default(merged.get("allowShell"), "approval_required"),
            "allowNetwork": bool(merged.get("allowNetwork")),
            "memoryRecallPolicy": self._string_or_default(
                merged.get("memoryRecallPolicy"), DEFAULT_AUTONOMY_PROFILE["memoryRecallPolicy"],
            ),
            "retryLimit": self._bounded_int(merged.get("retryLimit"), 0, 20, DEFAULT_AUTONOMY_PROFILE["retryLimit"]),
            "timeoutMs": self._bounded_int(merged.get("timeoutMs"), 1000, 86400000, DEFAULT_AUTONOMY_PROFILE["timeoutMs"]),
        }

    def _normalize_agent_soul_config(self, agent_soul: dict[str, Any]) -> dict[str, Any]:
        normalized = deepcopy(DEFAULT_CONFIG["agentSoul"])
        normalized.update(deepcopy(agent_soul))
        raw_profiles = normalized.get("profiles")
        profiles = [
            self._normalize_agent_soul_profile(item)
            for item in raw_profiles or []
            if isinstance(item, dict)
        ]
        if not profiles:
            profiles = [self._normalize_agent_soul_profile(DEFAULT_AGENT_SOUL_PROFILE)]
        profiles = self._dedupe_profiles(profiles, fallback_prefix="soul")
        active_profile_id = self._valid_active_profile_id(
            normalized.get("activeProfileId"),
            profiles,
        )
        workspace_instructions = normalized.get("workspaceInstructions")
        normalized["activeProfileId"] = active_profile_id
        normalized["workspaceInstructions"] = (
            workspace_instructions.strip() if isinstance(workspace_instructions, str) else ""
        )
        normalized["sessionOverrideEnabled"] = bool(normalized.get("sessionOverrideEnabled", False))
        normalized["profiles"] = profiles
        return normalized

    def _normalize_agent_soul_profile(self, profile: dict[str, Any]) -> dict[str, Any]:
        merged = deepcopy(DEFAULT_AGENT_SOUL_PROFILE)
        merged.update(deepcopy(profile))
        profile_id = self._safe_profile_id(merged.get("id"), "soul")
        name = self._safe_profile_name(merged.get("name"), profile_id)
        return {
            **merged,
            "id": profile_id,
            "name": name,
            "description": self._string_or_default(merged.get("description"), ""),
            "identity": self._string_or_default(merged.get("identity"), DEFAULT_AGENT_SOUL_PROFILE["identity"]),
            "principles": self._string_list(merged.get("principles")),
            "communicationStyle": self._string_or_default(merged.get("communicationStyle"), ""),
            "reasoningStyle": self._string_or_default(merged.get("reasoningStyle"), ""),
            "collaborationStyle": self._string_or_default(merged.get("collaborationStyle"), ""),
            "domainPreferences": self._string_list(merged.get("domainPreferences")),
            "customSystemPrompt": self._string_or_default(merged.get("customSystemPrompt"), ""),
            "enabled": bool(merged.get("enabled", True)),
            "scope": self._string_or_default(merged.get("scope"), "global"),
            "createdAt": self._bounded_int(merged.get("createdAt"), 0, 9999999999999, 0),
            "updatedAt": self._bounded_int(merged.get("updatedAt"), 0, 9999999999999, 0),
        }

    def _dedupe_profiles(self, profiles: list[dict[str, Any]], *, fallback_prefix: str) -> list[dict[str, Any]]:
        seen: set[str] = set()
        unique: list[dict[str, Any]] = []
        for index, profile in enumerate(profiles):
            profile_id = profile.get("id")
            if not isinstance(profile_id, str) or not profile_id:
                profile_id = f"{fallback_prefix}_{index + 1}"
                profile["id"] = profile_id
            if profile_id in seen:
                continue
            seen.add(profile_id)
            unique.append(profile)
        return unique

    def _valid_active_profile_id(self, value: Any, profiles: list[dict[str, Any]]) -> str:
        profile_ids = {profile["id"] for profile in profiles if isinstance(profile.get("id"), str)}
        if isinstance(value, str) and value in profile_ids:
            return value
        return profiles[0]["id"]

    def _safe_profile_id(self, value: Any, fallback_prefix: str) -> str:
        if isinstance(value, str) and value.strip():
            return value.strip()
        return f"{fallback_prefix}_{uuid.uuid4().hex[:8]}"

    def _safe_profile_name(self, value: Any, fallback: str) -> str:
        if isinstance(value, str) and value.strip():
            return value.strip()
        return fallback

    def _string_or_default(self, value: Any, default: str) -> str:
        if isinstance(value, str):
            return value.strip()
        return default

    def _string_list(self, value: Any, _key: str = "") -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item).strip() for item in value if str(item).strip()]

    def _bounded_int(self, value: Any, minimum: int, maximum: int, default: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            parsed = default
        return max(minimum, min(parsed, maximum))

    def _normalize_provider_config(self, provider: dict[str, Any]) -> dict[str, Any]:
        normalized = deepcopy(provider)
        legacy_profile = self._profile_from_provider(normalized)
        raw_profiles = normalized.get("profiles")
        profiles = [self._normalize_provider_profile(item, legacy_profile) for item in raw_profiles or [] if isinstance(item, dict)]
        if isinstance(raw_profiles, list) and not raw_profiles:
            default_profile = self._normalize_provider_profile(
                deepcopy(DEFAULT_CONFIG["provider"]["profiles"][0]),
                deepcopy(DEFAULT_CONFIG["provider"]["profiles"][0]),
            )
            profiles = [default_profile]
        if not profiles:
            profiles = [self._normalize_provider_profile(legacy_profile, legacy_profile)]

        seen: set[str] = set()
        unique_profiles: list[dict[str, Any]] = []
        for profile in profiles:
            profile_id = profile["id"]
            if profile_id in seen:
                continue
            seen.add(profile_id)
            unique_profiles.append(profile)

        active_profile_id = normalized.get("activeProfileId")
        if not isinstance(active_profile_id, str) or not active_profile_id.strip():
            active_profile_id = unique_profiles[0]["id"]
        elif active_profile_id not in {profile["id"] for profile in unique_profiles}:
            active_profile_id = unique_profiles[0]["id"]

        active_profile = next(profile for profile in unique_profiles if profile["id"] == active_profile_id)
        for key, value in active_profile.items():
            if key in {"id", "name"}:
                continue
            normalized[key] = deepcopy(value)
        normalized["activeProfileId"] = active_profile_id
        normalized["profiles"] = unique_profiles
        return normalized

    def _profile_from_provider(self, provider: dict[str, Any]) -> dict[str, Any]:
        profile: dict[str, Any] = {
            "id": provider.get("activeProfileId") if isinstance(provider.get("activeProfileId"), str) else "default",
            "name": provider.get("profileName") if isinstance(provider.get("profileName"), str) else "Default",
        }
        for key in (
            "mode",
            "baseUrl",
            "base_url",
            "model",
            "defaultModel",
            "fallbackModel",
            "apiKeyEnvVarName",
            "api_key_env_var_name",
            "envKey",
            "apiKey",
            "api_key",
            "temperature",
            "maxTokens",
            "max_tokens",
            "maxOutputTokens",
            "maxContextTokens",
            "promptCache",
            "timeout",
            "timeoutSeconds",
            "timeoutMs",
        ):
            if key in provider and provider[key] is not None:
                profile[key] = deepcopy(provider[key])
        return profile

    def _normalize_provider_profile(self, profile: dict[str, Any], defaults: dict[str, Any]) -> dict[str, Any]:
        merged = deepcopy(defaults)
        merged.update(deepcopy(profile))

        profile_id = merged.get("id")
        if not isinstance(profile_id, str) or not profile_id.strip():
            profile_id = f"profile_{uuid.uuid4().hex[:8]}"
        profile_name = merged.get("name")
        if not isinstance(profile_name, str) or not profile_name.strip():
            profile_name = profile_id

        model = merged.get("model") or merged.get("defaultModel") or DEFAULT_CONFIG["provider"]["model"]
        max_tokens = merged.get("maxTokens") or merged.get("maxOutputTokens") or DEFAULT_CONFIG["provider"]["maxTokens"]
        normalized = {
            **merged,
            "id": profile_id.strip(),
            "name": profile_name.strip(),
            "mode": merged.get("mode") or DEFAULT_CONFIG["provider"]["mode"],
            "baseUrl": merged.get("baseUrl") or merged.get("base_url") or DEFAULT_CONFIG["provider"]["baseUrl"],
            "model": model,
            "defaultModel": merged.get("defaultModel") or model,
            "apiKeyEnvVarName": merged.get("apiKeyEnvVarName")
            or merged.get("api_key_env_var_name")
            or merged.get("envKey")
            or DEFAULT_CONFIG["provider"]["apiKeyEnvVarName"],
            "temperature": merged.get("temperature", DEFAULT_CONFIG["provider"]["temperature"]),
            "maxTokens": max_tokens,
            "maxOutputTokens": merged.get("maxOutputTokens") or max_tokens,
            "maxContextTokens": merged.get("maxContextTokens", DEFAULT_CONFIG["provider"]["maxContextTokens"]),
            "timeout": merged.get("timeout", DEFAULT_CONFIG["provider"]["timeout"]),
        }
        prompt_cache = self._normalize_provider_prompt_cache(
            merged.get("promptCache"),
            defaults.get("promptCache") if isinstance(defaults, dict) else None,
        )
        if prompt_cache:
            normalized["promptCache"] = prompt_cache
        last_checked_at = merged.get("lastCheckedAt")
        if isinstance(last_checked_at, (int, float)):
            normalized["lastCheckedAt"] = int(last_checked_at)
        last_status = merged.get("lastStatus")
        if isinstance(last_status, str) and last_status.strip():
            normalized["lastStatus"] = last_status.strip()
        last_error_summary = merged.get("lastErrorSummary")
        if isinstance(last_error_summary, str) and last_error_summary.strip():
            normalized["lastErrorSummary"] = last_error_summary.strip()
        return normalized

    def _normalize_provider_prompt_cache(self, value: Any, defaults: Any = None) -> dict[str, Any]:
        default_policy = deepcopy(DEFAULT_CONFIG["provider"]["promptCache"])
        policy = deepcopy(value) if isinstance(value, dict) else {}
        default_source = defaults if isinstance(defaults, dict) else {}

        normalized = deepcopy(default_policy)
        normalized.update(policy)
        for key, old_value in (
            ("targetFillRatio", 0.75),
            ("maxStableContextTokens", 160000),
            ("recentMessages", 64),
            ("conversationMessageMaxChars", 6000),
        ):
            if policy.get(key) == old_value and default_source.get(key) == old_value:
                normalized[key] = default_policy[key]
        if "nearContextRatio" not in policy:
            normalized["nearContextRatio"] = default_policy["nearContextRatio"]
        return normalized

    def _apply_provider_patch_to_active_profile(
        self,
        config: dict[str, Any],
        provider_patch: dict[str, Any],
    ) -> None:
        provider = config.get("provider")
        if not isinstance(provider, dict):
            return
        profiles = provider.get("profiles")
        if not isinstance(profiles, list) or not profiles:
            return

        active_profile_id = provider.get("activeProfileId")
        active_profile = None
        if isinstance(active_profile_id, str):
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
        if active_profile is None:
            return

        for key, value in provider_patch.items():
            if key not in {"profiles", "activeProfileId"} and value is not None:
                active_profile[key] = deepcopy(value)

