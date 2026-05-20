"""Tests for config normalizer — legacy mapping, autonomy overrides, defaults."""

from __future__ import annotations

import pytest

from local_agent_runtime.policy.config_normalizer import normalize_permissions


# ---------------------------------------------------------------------------
# New-style config passthrough
# ---------------------------------------------------------------------------

class TestNewStyleConfig:
    def test_preset_passthrough(self):
        result = normalize_permissions({"permissions": {"preset": "safe"}})
        assert result["preset"] == "safe"
        assert result["capabilities"]["webFetch"]["mode"] == "blocked"

    def test_capabilities_passthrough(self):
        result = normalize_permissions({
            "permissions": {
                "preset": "balanced",
                "capabilities": {"readFile": {"mode": "blocked", "scope": "*"}},
            }
        })
        assert result["capabilities"]["readFile"]["mode"] == "blocked"
        # Other capabilities should come from balanced preset
        assert result["capabilities"]["memoryWrite"]["mode"] == "allow"

    def test_autonomous_preset(self):
        result = normalize_permissions({"permissions": {"preset": "autonomous"}})
        assert result["capabilities"]["writeFile"]["mode"] == "allow"
        assert result["capabilities"]["subagents"]["mode"] == "allow"


# ---------------------------------------------------------------------------
# Legacy approvalMode mapping
# ---------------------------------------------------------------------------

class TestLegacyApprovalMode:
    def test_strict_maps_to_safe(self):
        result = normalize_permissions({"policy": {"approvalMode": "strict"}})
        assert result["preset"] == "safe"
        assert result["capabilities"]["webFetch"]["mode"] == "blocked"

    def test_on_write_or_command_maps_to_balanced(self):
        result = normalize_permissions({"policy": {"approvalMode": "on_write_or_command"}})
        assert result["preset"] == "balanced"
        assert result["capabilities"]["webFetch"]["mode"] == "ask"

    def test_relaxed_maps_to_autonomous(self):
        result = normalize_permissions({"policy": {"approvalMode": "relaxed"}})
        assert result["preset"] == "autonomous"
        assert result["capabilities"]["writeFile"]["mode"] == "allow"

    def test_unknown_approval_mode_defaults_to_balanced(self):
        result = normalize_permissions({"policy": {"approvalMode": "custom_mode"}})
        assert result["preset"] == "balanced"

    def test_none_approval_mode_maps_to_autonomous(self):
        result = normalize_permissions({"policy": {"approvalMode": "none"}})
        assert result["preset"] == "autonomous"
        assert result["capabilities"]["writeFile"]["mode"] == "allow"


# ---------------------------------------------------------------------------
# Autonomy profile overrides
# ---------------------------------------------------------------------------

class TestAutonomyOverrides:
    def _profile(self, **overrides) -> dict:
        base = {
            "id": "test-profile",
            "allowFileWrite": None,
            "allowShell": None,
        }
        base.update(overrides)
        return base

    def test_allowFileWrite_true_overrides_writeFile(self):
        result = normalize_permissions({
            "policy": {"approvalMode": "strict"},
            "autonomy": {
                "activeProfileId": "test-profile",
                "profiles": [self._profile(allowFileWrite=True)],
            },
        })
        assert result["capabilities"]["writeFile"]["mode"] == "allow"

    def test_allowFileWrite_false_overrides_writeFile(self):
        result = normalize_permissions({
            "policy": {"approvalMode": "relaxed"},
            "autonomy": {
                "activeProfileId": "test-profile",
                "profiles": [self._profile(allowFileWrite=False)],
            },
        })
        assert result["capabilities"]["writeFile"]["mode"] == "blocked"

    def test_allowShell_true_overrides_runCommand(self):
        result = normalize_permissions({
            "autonomy": {
                "activeProfileId": "test-profile",
                "profiles": [self._profile(allowShell=True)],
            },
        })
        assert result["capabilities"]["runCommand"]["mode"] == "allow"

    def test_allowNetwork_false_overrides_network(self):
        result = normalize_permissions({
            "autonomy": {
                "activeProfileId": "test-profile",
                "profiles": [self._profile(allowNetwork=False)],
            },
        })
        assert result["capabilities"]["network"]["mode"] == "blocked"

    def test_allowNetwork_true_overrides_network(self):
        result = normalize_permissions({
            "autonomy": {
                "activeProfileId": "test-profile",
                "profiles": [self._profile(allowNetwork=True)],
            },
        })
        assert result["capabilities"]["network"]["mode"] == "ask"

    def test_string_values_mapped(self):
        result = normalize_permissions({
            "autonomy": {
                "activeProfileId": "test-profile",
                "profiles": [self._profile(allowFileWrite="approval_required")],
            },
        })
        assert result["capabilities"]["writeFile"]["mode"] == "ask"

    def test_non_active_profile_ignored(self):
        result = normalize_permissions({
            "autonomy": {
                "activeProfileId": "other",
                "profiles": [self._profile(allowFileWrite=True)],
            },
        })
        # writeFile should stay at balanced default
        assert result["capabilities"]["writeFile"]["mode"] == "ask"


# ---------------------------------------------------------------------------
# Empty config → balanced default
# ---------------------------------------------------------------------------

class TestDefaults:
    def test_empty_config_balanced(self):
        result = normalize_permissions({})
        assert result["preset"] == "balanced"
        assert result["capabilities"]["readFile"]["mode"] == "allow"
        assert result["capabilities"]["runCommand"]["mode"] == "ask"

    def test_none_values(self):
        result = normalize_permissions({"permissions": None, "policy": None})
        assert result["preset"] == "balanced"


# ---------------------------------------------------------------------------
# New config takes priority over legacy
# ---------------------------------------------------------------------------

class TestPriority:
    def test_new_config_overrides_legacy(self):
        result = normalize_permissions({
            "permissions": {"preset": "safe"},
            "policy": {"approvalMode": "relaxed"},
        })
        # Should use safe, not relaxed→autonomous
        assert result["preset"] == "safe"
        assert result["capabilities"]["writeFile"]["mode"] == "ask"
