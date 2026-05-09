"""Tests for extended validators: risk policy, approval gates, test strategy.

Covers:
- Risk policy validation (risk levels, approval requirements)
- Approval gate validation (gate types, conditions)
- Test strategy validation (command safety, dangerous patterns)
"""

from __future__ import annotations

import pytest

from local_agent_runtime.policy.proposal_validator import (
    validate_approval_gates,
    validate_risk_policy,
    validate_test_strategy,
)


# ---------------------------------------------------------------------------
# Risk Policy Validator
# ---------------------------------------------------------------------------


class TestRiskPolicyValidator:
    def test_valid_low_risk(self):
        assert validate_risk_policy({"riskLevel": "low"}) == []

    def test_valid_medium_risk(self):
        assert validate_risk_policy({"riskLevel": "medium"}) == []

    def test_valid_high_risk_with_reviewer(self):
        assert validate_risk_policy({
            "riskLevel": "high",
            "approvalGates": ["reviewer"],
        }) == []

    def test_valid_critical_risk_with_all_gates(self):
        assert validate_risk_policy({
            "riskLevel": "critical",
            "approvalGates": ["reviewer", "verifier"],
        }) == []

    def test_high_risk_missing_reviewer(self):
        reasons = validate_risk_policy({
            "riskLevel": "high",
            "approvalGates": [],
        })
        assert any("reviewer" in r for r in reasons)

    def test_critical_risk_missing_verifier(self):
        reasons = validate_risk_policy({
            "riskLevel": "critical",
            "approvalGates": ["reviewer"],
        })
        assert any("verifier" in r for r in reasons)

    def test_invalid_risk_level(self):
        reasons = validate_risk_policy({"riskLevel": "extreme"})
        assert any("Invalid riskLevel" in r for r in reasons)

    def test_no_risk_level_passes(self):
        assert validate_risk_policy({}) == []

    def test_no_risk_level_but_gates_present_passes(self):
        assert validate_risk_policy({"approvalGates": ["reviewer"]}) == []


# ---------------------------------------------------------------------------
# Approval Gate Validator
# ---------------------------------------------------------------------------


class TestApprovalGateValidator:
    def test_valid_gates(self):
        reasons = validate_approval_gates({
            "gates": [
                {"type": "reviewer", "condition": "all patches reviewed"},
                {"type": "verifier"},
            ],
        })
        assert reasons == []

    @pytest.mark.parametrize("gate_type", ["reviewer", "verifier", "user", "automated"])
    def test_valid_gate_types(self, gate_type: str):
        reasons = validate_approval_gates({"gates": [{"type": gate_type}]})
        assert reasons == []

    def test_empty_gates(self):
        reasons = validate_approval_gates({"gates": []})
        assert any("non-empty" in r for r in reasons)

    def test_gates_not_list(self):
        reasons = validate_approval_gates({"gates": "reviewer"})
        assert any("must be a list" in r for r in reasons)

    def test_gate_not_dict(self):
        reasons = validate_approval_gates({"gates": ["not a dict"]})
        assert any("must be a dict" in r for r in reasons)

    def test_gate_missing_type(self):
        reasons = validate_approval_gates({"gates": [{"condition": "test"}]})
        assert any("missing required field 'type'" in r for r in reasons)

    def test_invalid_gate_type(self):
        reasons = validate_approval_gates({"gates": [{"type": "invalid"}]})
        assert any("invalid gate type" in r for r in reasons)

    def test_empty_condition(self):
        reasons = validate_approval_gates({"gates": [{"type": "reviewer", "condition": ""}]})
        assert any("condition must be" in r for r in reasons)

    def test_condition_not_string(self):
        reasons = validate_approval_gates({"gates": [{"type": "reviewer", "condition": 42}]})
        assert any("condition must be" in r for r in reasons)

    def test_no_condition_passes(self):
        reasons = validate_approval_gates({"gates": [{"type": "reviewer"}]})
        assert reasons == []


# ---------------------------------------------------------------------------
# Test Strategy Validator
# ---------------------------------------------------------------------------


class TestTestStrategyValidator:
    def test_valid_commands(self):
        reasons = validate_test_strategy({
            "commands": ["python -m pytest", "npm test"],
        })
        assert reasons == []

    def test_commands_not_list(self):
        reasons = validate_test_strategy({"commands": "pytest"})
        assert any("must be a list" in r for r in reasons)

    def test_empty_commands(self):
        reasons = validate_test_strategy({"commands": []})
        assert any("non-empty" in r for r in reasons)

    def test_command_not_string(self):
        reasons = validate_test_strategy({"commands": [42]})
        assert any("must be a string" in r for r in reasons)

    def test_empty_command(self):
        reasons = validate_test_strategy({"commands": [""]})
        assert any("non-empty string" in r for r in reasons)

    @pytest.mark.parametrize("dangerous_cmd", [
        "rm -rf /",
        "del /s /q C:\\",
        "format C:",
        "shutdown -h now",
        "DROP TABLE users",
        "DELETE FROM users",
        "TRUNCATE TABLE data",
    ])
    def test_dangerous_commands_rejected(self, dangerous_cmd: str):
        reasons = validate_test_strategy({"commands": [dangerous_cmd]})
        assert len(reasons) > 0

    def test_safe_commands_pass(self):
        reasons = validate_test_strategy({
            "commands": [
                "python -m pytest tests/ -v",
                "go test ./... -count=1",
                "cargo test --all",
                "make test",
            ],
        })
        assert reasons == []

    def test_multiple_commands_one_dangerous(self):
        reasons = validate_test_strategy({
            "commands": ["pytest", "rm -rf /"],
        })
        assert len(reasons) == 1
        assert any("dangerous" in r for r in reasons)


# ---------------------------------------------------------------------------
# Composite validator integration
# ---------------------------------------------------------------------------


class TestCompositeValidatorIntegration:
    def test_risk_policy_wired(self):
        from local_agent_runtime.policy.proposal_validator import validate_proposal

        reasons = validate_proposal("risk_policy", {"riskLevel": "extreme"})
        assert any("Invalid riskLevel" in r for r in reasons)

    def test_approval_policy_wired(self):
        from local_agent_runtime.policy.proposal_validator import validate_proposal

        reasons = validate_proposal("approval_policy", {"gates": []})
        assert any("non-empty" in r for r in reasons)

    def test_test_strategy_wired(self):
        from local_agent_runtime.policy.proposal_validator import validate_proposal

        reasons = validate_proposal("test_strategy", {"commands": "pytest"})
        assert any("must be a list" in r for r in reasons)
