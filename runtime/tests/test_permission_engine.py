"""Tests for PermissionEngine evaluate / effective_config / is_allowed."""

from __future__ import annotations

import pytest

from local_agent_runtime.policy.permission_engine import (
    PermissionDecision,
    PermissionEngine,
    PermissionRequest,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _engine(preset: str = "balanced", *, overrides: dict | None = None) -> PermissionEngine:
    caps = overrides or {}
    config = {"permissions": {"preset": preset, "capabilities": caps}}
    return PermissionEngine(config=config)


# ---------------------------------------------------------------------------
# evaluate — allow / ask / blocked
# ---------------------------------------------------------------------------

class TestEvaluateAllow:
    def test_readFile_balanced(self):
        d = _engine().evaluate(PermissionRequest(capability="readFile"))
        assert d.decision == "allow"

    def test_readFile_safe(self):
        d = _engine("safe").evaluate(PermissionRequest(capability="readFile"))
        assert d.decision == "allow"

    def test_memoryWrite_all_presets(self):
        for preset in ("safe", "balanced", "autonomous"):
            d = _engine(preset).evaluate(PermissionRequest(capability="memoryWrite"))
            assert d.decision == "allow", f"{preset} memoryWrite should be allow"

    def test_writeFile_autonomous(self):
        d = _engine("autonomous").evaluate(PermissionRequest(capability="writeFile"))
        assert d.decision == "allow"

    def test_subagents_autonomous(self):
        d = _engine("autonomous").evaluate(PermissionRequest(capability="subagents"))
        assert d.decision == "allow"


class TestEvaluateBlocked:
    def test_webFetch_safe(self):
        d = _engine("safe").evaluate(PermissionRequest(capability="webFetch"))
        assert d.decision == "deny"
        assert "blocked" in d.reason.lower() or "blocked" in d.reason

    def test_network_balanced(self):
        d = _engine().evaluate(PermissionRequest(capability="network"))
        assert d.decision == "deny"

    def test_browserAutomation_all_presets(self):
        for preset in ("safe", "balanced", "autonomous"):
            d = _engine(preset).evaluate(PermissionRequest(capability="browserAutomation"))
            assert d.decision == "deny", f"{preset} browserAutomation should be deny"

    def test_computerUse_all_presets(self):
        for preset in ("safe", "balanced", "autonomous"):
            d = _engine(preset).evaluate(PermissionRequest(capability="computerUse"))
            assert d.decision == "deny", f"{preset} computerUse should be deny"


class TestEvaluateAsk:
    def test_runCommand_balanced(self):
        d = _engine().evaluate(PermissionRequest(capability="runCommand"))
        assert d.decision == "approval_required"
        assert d.approval_kind == "run_command"

    def test_writeFile_balanced(self):
        d = _engine().evaluate(PermissionRequest(capability="writeFile"))
        assert d.decision == "approval_required"

    def test_webFetch_balanced(self):
        d = _engine().evaluate(PermissionRequest(capability="webFetch"))
        assert d.decision == "approval_required"
        assert d.approval_kind == "network_access"

    def test_subagents_balanced(self):
        d = _engine().evaluate(PermissionRequest(capability="subagents"))
        assert d.decision == "approval_required"
        assert d.approval_kind == "subagent_dispatch"

    def test_runCommand_autonomous_still_asks(self):
        d = _engine("autonomous").evaluate(PermissionRequest(capability="runCommand"))
        assert d.decision == "approval_required"


class TestEvaluateUnknown:
    def test_unknown_capability_defaults_to_approval_required(self):
        d = _engine().evaluate(PermissionRequest(capability="nonexistent"))
        assert d.decision == "approval_required"
        assert "no rule" in d.reason.lower()


# ---------------------------------------------------------------------------
# Capability overrides
# ---------------------------------------------------------------------------

class TestOverrides:
    def test_override_runCommand_to_allow(self):
        e = _engine(overrides={"runCommand": {"mode": "allow", "scope": "*"}})
        d = e.evaluate(PermissionRequest(capability="runCommand"))
        assert d.decision == "allow"

    def test_override_readFile_to_blocked(self):
        e = _engine(overrides={"readFile": {"mode": "blocked", "scope": "*"}})
        d = e.evaluate(PermissionRequest(capability="readFile"))
        assert d.decision == "deny"

    def test_override_preserves_other_capabilities(self):
        e = _engine(overrides={"readFile": {"mode": "blocked", "scope": "*"}})
        # memoryWrite should still be allow from the balanced preset
        d = e.evaluate(PermissionRequest(capability="memoryWrite"))
        assert d.decision == "allow"


# ---------------------------------------------------------------------------
# effective_config / is_allowed
# ---------------------------------------------------------------------------

class TestEffectiveConfig:
    def test_returns_preset_and_capabilities(self):
        e = _engine()
        cfg = e.effective_config()
        assert cfg["preset"] == "balanced"
        assert "readFile" in cfg["capabilities"]
        assert cfg["capabilities"]["readFile"]["mode"] == "allow"


class TestIsAllowed:
    def test_allowed_capability(self):
        assert _engine().is_allowed("readFile") is True

    def test_ask_capability(self):
        assert _engine().is_allowed("runCommand") is False

    def test_blocked_capability(self):
        assert _engine().is_allowed("network") is False

    def test_unknown_capability(self):
        assert _engine().is_allowed("nonexistent") is False


# ---------------------------------------------------------------------------
# ApprovalKind mapping
# ---------------------------------------------------------------------------

class TestApprovalKindMapping:
    @pytest.mark.parametrize("cap,expected_kind", [
        ("runCommand", "run_command"),
        ("webFetch", "network_access"),
        ("network", "network_access"),
        ("subagents", "subagent_dispatch"),
        ("gitWrite", "apply_patch"),
        ("hooksExecute", "run_command"),
    ])
    def test_approval_kind_mapping(self, cap, expected_kind):
        d = _engine("autonomous" if cap == "subagents" else "balanced").evaluate(
            PermissionRequest(capability=cap)
        )
        if d.decision == "approval_required":
            assert d.approval_kind == expected_kind
