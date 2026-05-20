"""Normalize permission config from legacy + new sources into a unified structure."""
from __future__ import annotations

from typing import Any

from .presets import preset_to_config


def normalize_permissions(raw_config: dict[str, Any]) -> dict[str, Any]:
    """Produce a unified permission config from *raw_config*.

    Resolution order:
    1. If ``permissions.preset`` or ``permissions.capabilities`` is present → new-style
    2. Else map legacy ``policy.approvalMode`` to a preset
    3. Merge any active autonomy-profile overrides
    4. Default: ``balanced``
    """
    permissions = raw_config.get("permissions")
    if isinstance(permissions, dict):
        preset = permissions.get("preset", "balanced")
        caps = permissions.get("capabilities", {})
        if caps or preset != "balanced":
            return preset_to_config(preset, caps if caps else None)

    # Legacy path: derive preset from approvalMode
    approval_mode = ""
    policy = raw_config.get("policy")
    if isinstance(policy, dict):
        approval_mode = policy.get("approvalMode", "")

    legacy_preset_map = {
        "none": "autonomous",
        "never": "autonomous",
        "off": "autonomous",
        "strict": "safe",
        "on_write_or_command": "balanced",
        "relaxed": "autonomous",
    }
    preset_name = legacy_preset_map.get(approval_mode, "balanced")
    overrides: dict[str, Any] = {}

    # Merge autonomy profile overrides
    autonomy = raw_config.get("autonomy")
    if isinstance(autonomy, dict):
        active_id = autonomy.get("activeProfileId", "balanced")
        profiles = autonomy.get("profiles", [])
        active_profile = _find_profile(profiles, active_id)
        if active_profile:
            _apply_autonomy_overrides(active_profile, overrides)

    if overrides:
        return preset_to_config(preset_name, overrides)
    return preset_to_config(preset_name)


def _find_profile(profiles: list[dict[str, Any]], profile_id: str) -> dict[str, Any] | None:
    for p in profiles:
        if p.get("id") == profile_id:
            return p
    return None


def _apply_autonomy_overrides(profile: dict[str, Any], overrides: dict[str, Any]) -> None:
    """Translate legacy autonomy profile fields into capability overrides."""
    _map_tri_state(profile, "allowFileWrite", "writeFile", overrides)
    _map_tri_state(profile, "allowShell", "runCommand", overrides)
    if "allowNetwork" in profile:
        overrides["network"] = {"mode": "ask" if profile["allowNetwork"] else "blocked", "scope": "*"}


def _map_tri_state(
    profile: dict[str, Any],
    profile_key: str,
    capability: str,
    overrides: dict[str, Any],
) -> None:
    value = profile.get(profile_key)
    if value is None:
        return
    if isinstance(value, bool):
        overrides[capability] = {"mode": "allow" if value else "blocked", "scope": "*"}
    elif isinstance(value, str):
        mode_map = {
            "allowed": "allow",
            "approval_required": "ask",
            "blocked": "blocked",
        }
        overrides[capability] = {"mode": mode_map.get(value, "ask"), "scope": "*"}
