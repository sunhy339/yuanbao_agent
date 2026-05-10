"""Tests for runtime proposal validators.

Covers:
- Schema validation for all proposal kinds
- Tool allowlist validation
- Unsafe tool rejection
- Dependency graph validation (missing refs, self-cycles)
- Write scope overlap detection
- Composite validate_proposal
"""

from __future__ import annotations

import pytest

from local_agent_runtime.policy.proposal_validator import (
    validate_approval_gates,
    validate_context_budget,
    validate_dependency_graph,
    validate_frontend_visibility,
    validate_memory_source,
    validate_mode,
    validate_model_provider,
    validate_no_unsafe_tools,
    validate_proposal,
    validate_proposal_schema,
    validate_retry_budget,
    validate_roadmap_edit,
    validate_risk_policy,
    validate_session_task_state,
    validate_skill_availability,
    validate_skill_root_allowlist,
    validate_test_strategy,
    validate_tool_allowlist,
    validate_write_scopes,
)


class TestSchemaValidator:
    def test_valid_tool_policy(self):
        reasons = validate_proposal_schema("tool_policy", {"allowedTools": ["read_file"]})
        assert reasons == []

    def test_missing_required_field(self):
        reasons = validate_proposal_schema("tool_policy", {})
        assert any("allowedTools" in r for r in reasons)

    def test_unknown_kind(self):
        reasons = validate_proposal_schema("not_a_kind", {})
        assert any("Unknown" in r for r in reasons)

    def test_decomposition_requires_subtasks(self):
        reasons = validate_proposal_schema("decomposition", {})
        assert any("subtasks" in r for r in reasons)

    def test_agent_profile_requires_fields(self):
        reasons = validate_proposal_schema("agent_profile", {})
        fields = {r.split(": ")[1].strip("'") for r in reasons}
        assert "name" in fields
        assert "baseType" in fields
        assert "mission" in fields

    def test_all_kinds_have_required_fields(self):
        """Every kind in REQUIRED_FIELDS_BY_KIND should validate."""
        from local_agent_runtime.policy.proposal_validator import REQUIRED_FIELDS_BY_KIND
        for kind, fields in REQUIRED_FIELDS_BY_KIND.items():
            payload = {f: f"value_{f}" for f in fields}
            reasons = validate_proposal_schema(kind, payload)
            assert reasons == [], f"Kind {kind!r} should pass with fields {fields}"


class TestToolAllowlistValidator:
    def test_valid_tools(self):
        reasons = validate_tool_allowlist({"allowedTools": ["read_file", "search_files"]})
        assert reasons == []

    def test_alias_resolved(self):
        # rg resolves to search_files which is safe
        reasons = validate_tool_allowlist({"allowedTools": ["rg"]})
        assert reasons == []

    def test_unknown_tool(self):
        reasons = validate_tool_allowlist({"allowedTools": ["unknown_tool"]})
        assert any("unknown_tool" in r for r in reasons)

    def test_not_a_list(self):
        reasons = validate_tool_allowlist({"allowedTools": "read_file"})
        assert any("must be a list" in r for r in reasons)


class TestUnsafeToolValidator:
    def test_no_unsafe_tools(self):
        reasons = validate_no_unsafe_tools({"allowedTools": ["read_file"]})
        assert reasons == []

    def test_task_is_unsafe(self):
        reasons = validate_no_unsafe_tools({"allowedTools": ["task"]})
        assert any("Unsafe" in r for r in reasons)

    def test_safe_tools_pass(self):
        reasons = validate_no_unsafe_tools({
            "allowedTools": ["read_file", "search_files", "apply_patch"]
        })
        assert reasons == []


class TestDependencyGraphValidator:
    def test_valid_deps(self):
        reasons = validate_dependency_graph([
            {"id": "a", "dependencies": []},
            {"id": "b", "dependencies": ["a"]},
        ])
        assert reasons == []

    def test_missing_dependency(self):
        reasons = validate_dependency_graph([
            {"id": "a", "dependencies": ["nonexistent"]},
        ])
        assert any("unknown dependency" in r for r in reasons)

    def test_self_dependency(self):
        reasons = validate_dependency_graph([
            {"id": "a", "dependencies": ["a"]},
        ])
        assert any("self-dependency" in r for r in reasons)

    def test_empty_subtasks(self):
        assert validate_dependency_graph([]) == []

    def test_not_a_list(self):
        reasons = validate_dependency_graph("not a list")
        assert any("must be a list" in r for r in reasons)

    def test_taskId_variant(self):
        # taskId is also accepted as identifier
        reasons = validate_dependency_graph([
            {"taskId": "t1", "dependencies": []},
            {"taskId": "t2", "dependencies": ["t1"]},
        ])
        assert reasons == []


class TestWriteScopeValidator:
    def test_no_overlap(self):
        reasons = validate_write_scopes([
            {"id": "a", "ownedScope": ["src/ui/"]},
            {"id": "b", "ownedScope": ["src/engine/"]},
        ])
        assert reasons == []

    def test_overlap_detected(self):
        reasons = validate_write_scopes([
            {"id": "a", "ownedScope": ["src/"]},
            {"id": "b", "ownedScope": ["src/"]},
        ])
        assert any("Overlapping" in r for r in reasons)

    def test_string_scope(self):
        reasons = validate_write_scopes([
            {"id": "a", "ownedScope": "src/"},
            {"id": "b", "ownedScope": ["src/"]},
        ])
        assert any("Overlapping" in r for r in reasons)

    def test_writeScope_field(self):
        reasons = validate_write_scopes([
            {"id": "a", "writeScope": ["src/"]},
            {"id": "b", "writeScope": ["src/"]},
        ])
        assert any("Overlapping" in r for r in reasons)

    def test_no_scope(self):
        reasons = validate_write_scopes([
            {"id": "a"},
            {"id": "b"},
        ])
        assert reasons == []


class TestCompositeValidator:
    def test_tool_policy_valid(self):
        reasons = validate_proposal("tool_policy", {
            "allowedTools": ["read_file", "search_files"],
        })
        assert reasons == []

    def test_tool_policy_unsafe(self):
        reasons = validate_proposal("tool_policy", {
            "allowedTools": ["task"],
        })
        assert any("Unsafe" in r for r in reasons)

    def test_decomposition_valid(self):
        reasons = validate_proposal("decomposition", {
            "subtasks": [
                {"id": "ui", "dependencies": [], "ownedScope": ["src/ui/"]},
                {"id": "engine", "dependencies": [], "ownedScope": ["src/engine/"]},
            ],
        })
        assert reasons == []

    def test_decomposition_invalid_deps_and_scopes(self):
        reasons = validate_proposal("decomposition", {
            "subtasks": [
                {"id": "a", "dependencies": ["missing"], "ownedScope": ["src/"]},
                {"id": "b", "dependencies": [], "ownedScope": ["src/"]},
            ],
        })
        assert any("unknown dependency" in r for r in reasons)
        assert any("Overlapping" in r for r in reasons)

    def test_other_kind_schema_only(self):
        reasons = validate_proposal("model_policy", {
            "model": "gpt-4o",
        })
        assert reasons == []

    def test_other_kind_missing_field(self):
        reasons = validate_proposal("model_policy", {})
        assert any("model" in r for r in reasons)

    def test_skill_policy_valid(self):
        reasons = validate_proposal("skill_policy", {
            "skillId": "commit",
            "skills": ["commit"],
            "installedSkills": ["commit", "review-pr"],
            "role": "root",
            "rootAllowedSkills": ["commit"],
        })
        assert reasons == []

    def test_skill_policy_missing_skill(self):
        reasons = validate_proposal("skill_policy", {
            "skillId": "commit",
            "skills": ["commit"],
            "installedSkills": [],
            "role": "root",
            "rootAllowedSkills": ["commit"],
        })
        assert any("not installed" in r for r in reasons)

    def test_skill_policy_root_not_allowed(self):
        reasons = validate_proposal("skill_policy", {
            "skillId": "dangerous-skill",
            "skills": ["dangerous-skill"],
            "installedSkills": ["dangerous-skill"],
            "role": "root",
            "rootAllowedSkills": ["commit"],
        })
        assert any("not allowed for root" in r for r in reasons)

    def test_skill_policy_non_root_skips_root_check(self):
        reasons = validate_proposal("skill_policy", {
            "skillId": "dangerous-skill",
            "skills": ["dangerous-skill"],
            "installedSkills": ["dangerous-skill"],
            "role": "child",
            "rootAllowedSkills": ["commit"],
        })
        assert reasons == []


class TestSkillAvailabilityValidator:
    def test_all_installed(self):
        reasons = validate_skill_availability({
            "skills": ["commit", "review-pr"],
            "installedSkills": ["commit", "review-pr", "pdf"],
        })
        assert reasons == []

    def test_missing_skill(self):
        reasons = validate_skill_availability({
            "skills": ["commit", "missing-skill"],
            "installedSkills": ["commit"],
        })
        assert any("missing-skill" in r and "not installed" in r for r in reasons)

    def test_no_skills_field(self):
        assert validate_skill_availability({}) == []

    def test_empty_skills(self):
        assert validate_skill_availability({"skills": []}) == []

    def test_no_installed_skills(self):
        assert validate_skill_availability({"skills": ["commit"]}) == []


class TestSkillRootAllowlistValidator:
    def test_root_allowed(self):
        reasons = validate_skill_root_allowlist({
            "role": "root",
            "skills": ["commit"],
            "rootAllowedSkills": ["commit", "review-pr"],
        })
        assert reasons == []

    def test_root_not_allowed(self):
        reasons = validate_skill_root_allowlist({
            "role": "root",
            "skills": ["danger"],
            "rootAllowedSkills": ["commit"],
        })
        assert any("danger" in r and "not allowed" in r for r in reasons)

    def test_child_skips_check(self):
        reasons = validate_skill_root_allowlist({
            "role": "child",
            "skills": ["anything"],
        })
        assert reasons == []

    def test_no_root_allowed_skills(self):
        reasons = validate_skill_root_allowlist({
            "role": "root",
            "skills": ["commit"],
        })
        assert reasons == []


# ---------------------------------------------------------------------------
# Batch 11: Additional validator tests
# ---------------------------------------------------------------------------


class TestRiskPolicyValidator:
    def test_no_risk_level_passes(self):
        assert validate_risk_policy({}) == []

    def test_valid_low_risk(self):
        assert validate_risk_policy({"riskLevel": "low"}) == []

    def test_valid_medium_risk(self):
        assert validate_risk_policy({"riskLevel": "medium"}) == []

    def test_high_risk_without_gates(self):
        reasons = validate_risk_policy({"riskLevel": "high"})
        assert any("requires approval gate" in r for r in reasons)

    def test_critical_risk_without_gates(self):
        reasons = validate_risk_policy({"riskLevel": "critical"})
        assert any("requires approval gate" in r for r in reasons)

    def test_high_risk_with_required_gates(self):
        reasons = validate_risk_policy({
            "riskLevel": "high",
            "approvalGates": ["reviewer"],
        })
        assert reasons == []

    def test_critical_risk_with_all_gates(self):
        reasons = validate_risk_policy({
            "riskLevel": "critical",
            "approvalGates": ["reviewer", "verifier", "user"],
        })
        assert reasons == []

    def test_invalid_risk_level(self):
        reasons = validate_risk_policy({"riskLevel": "extreme"})
        assert any("Invalid riskLevel" in r for r in reasons)


class TestApprovalGatesValidator:
    def test_valid_gates(self):
        reasons = validate_approval_gates({
            "gates": [{"type": "reviewer"}, {"type": "user"}],
        })
        assert reasons == []

    def test_gates_not_list(self):
        reasons = validate_approval_gates({"gates": "reviewer"})
        assert any("gates must be a list" in r for r in reasons)

    def test_gates_empty(self):
        reasons = validate_approval_gates({"gates": []})
        assert any("gates must be non-empty" in r for r in reasons)

    def test_gate_not_dict(self):
        reasons = validate_approval_gates({"gates": ["not a dict"]})
        assert any("gates[0] must be a dict" in r for r in reasons)

    def test_gate_missing_type(self):
        reasons = validate_approval_gates({"gates": [{"condition": "always"}]})
        assert any("missing required field 'type'" in r for r in reasons)

    def test_invalid_gate_type(self):
        reasons = validate_approval_gates({"gates": [{"type": "unknown"}]})
        assert any("invalid gate type" in r for r in reasons)

    def test_all_valid_gate_types(self):
        for gt in ["reviewer", "verifier", "user", "automated"]:
            reasons = validate_approval_gates({"gates": [{"type": gt}]})
            assert reasons == [], f"Gate type {gt!r} should be valid"

    def test_condition_empty_string(self):
        reasons = validate_approval_gates({
            "gates": [{"type": "reviewer", "condition": "  "}],
        })
        assert any("condition must be a non-empty string" in r for r in reasons)

    def test_condition_valid(self):
        reasons = validate_approval_gates({
            "gates": [{"type": "reviewer", "condition": "all tests pass"}],
        })
        assert reasons == []


class TestTestStrategyValidator:
    def test_valid_commands(self):
        reasons = validate_test_strategy({
            "commands": ["python -m pytest", "npm test"],
        })
        assert reasons == []

    def test_commands_not_list(self):
        reasons = validate_test_strategy({"commands": "pytest"})
        assert any("commands must be a list" in r for r in reasons)

    def test_commands_empty(self):
        reasons = validate_test_strategy({"commands": []})
        assert any("commands must be non-empty" in r for r in reasons)

    def test_command_not_string(self):
        reasons = validate_test_strategy({"commands": [42]})
        assert any("commands[0] must be a string" in r for r in reasons)

    def test_command_empty_string(self):
        reasons = validate_test_strategy({"commands": ["  "]})
        assert any("commands[0] must be a non-empty string" in r for r in reasons)

    def test_dangerous_rm_command(self):
        reasons = validate_test_strategy({"commands": ["rm -rf /"]})
        assert any("dangerous pattern" in r for r in reasons)

    def test_dangerous_drop_command(self):
        reasons = validate_test_strategy({"commands": ["drop table users"]})
        assert any("dangerous pattern" in r for r in reasons)

    def test_safe_command_passes(self):
        for cmd in ["pytest", "python -m pytest", "go test ./...", "cargo test"]:
            reasons = validate_test_strategy({"commands": [cmd]})
            assert reasons == [], f"Safe command {cmd!r} should pass"


class TestSessionTaskStateValidator:
    def test_valid_states(self):
        reasons = validate_session_task_state({
            "sessionState": "active",
            "taskState": "running",
        })
        assert reasons == []

    def test_invalid_session_state(self):
        reasons = validate_session_task_state({"sessionState": "destroyed"})
        assert any("Invalid sessionState" in r for r in reasons)

    def test_invalid_task_state(self):
        reasons = validate_session_task_state({"taskState": "exploding"})
        assert any("Invalid taskState" in r for r in reasons)

    def test_no_states_passes(self):
        assert validate_session_task_state({}) == []

    def test_all_valid_session_states(self):
        for s in ["active", "paused", "closed"]:
            reasons = validate_session_task_state({"sessionState": s})
            assert reasons == [], f"sessionState {s!r} should be valid"

    def test_all_valid_task_states(self):
        for s in ["running", "queued", "completed", "failed", "cancelled", "paused"]:
            reasons = validate_session_task_state({"taskState": s})
            assert reasons == [], f"taskState {s!r} should be valid"


class TestModeValidator:
    def test_valid_modes(self):
        for mode in ["direct", "task", "queued", "supplement", "collaboration", "clarification"]:
            reasons = validate_mode({"mode": mode})
            assert reasons == [], f"mode {mode!r} should be valid"

    def test_invalid_mode(self):
        reasons = validate_mode({"mode": "teleport"})
        assert any("Invalid mode" in r for r in reasons)

    def test_no_mode_passes(self):
        assert validate_mode({}) == []

    def test_supplement_with_active_task(self):
        reasons = validate_mode({"mode": "supplement", "taskState": "running"})
        assert reasons == []

    def test_supplement_with_paused_task(self):
        reasons = validate_mode({"mode": "supplement", "taskState": "paused"})
        assert reasons == []

    def test_supplement_with_completed_task_rejected(self):
        reasons = validate_mode({"mode": "supplement", "taskState": "completed"})
        assert any("Supplement mode requires" in r for r in reasons)


class TestModelProviderValidator:
    def test_valid_model_and_provider(self):
        reasons = validate_model_provider({
            "model": "claude-sonnet-4-6",
            "provider": "anthropic",
        })
        assert reasons == []

    def test_unknown_model(self):
        reasons = validate_model_provider({"model": "gpt-99"})
        assert any("Unknown model" in r for r in reasons)

    def test_unknown_provider(self):
        reasons = validate_model_provider({"provider": "aliens"})
        assert any("Unknown provider" in r for r in reasons)

    def test_no_model_or_provider_passes(self):
        assert validate_model_provider({}) == []

    def test_valid_budget(self):
        reasons = validate_model_provider({"budget": 100})
        assert reasons == []

    def test_negative_budget(self):
        reasons = validate_model_provider({"budget": -1})
        assert any("budget must be a non-negative number" in r for r in reasons)

    def test_zero_budget(self):
        reasons = validate_model_provider({"budget": 0})
        assert reasons == []

    def test_budget_string_rejected(self):
        reasons = validate_model_provider({"budget": "100"})
        assert any("budget must be" in r for r in reasons)


class TestContextBudgetValidator:
    def test_valid_sections_and_budget(self):
        reasons = validate_context_budget({
            "sections": [{"name": "system"}, {"name": "safety"}, {"name": "memory"}],
            "tokenBudget": 8000,
        })
        assert reasons == []

    def test_missing_required_section(self):
        reasons = validate_context_budget({
            "sections": [{"name": "system"}],
        })
        assert any("Required context section missing: 'safety'" in r for r in reasons)

    def test_sections_not_list(self):
        reasons = validate_context_budget({"sections": "system"})
        assert any("sections must be a list" in r for r in reasons)

    def test_section_not_dict(self):
        reasons = validate_context_budget({"sections": ["not a dict"]})
        assert any("sections[0] must be a dict" in r for r in reasons)

    def test_section_missing_name(self):
        reasons = validate_context_budget({"sections": [{"content": "hi"}]})
        assert any("sections[0] missing required field 'name'" in r for r in reasons)

    def test_zero_budget_rejected(self):
        reasons = validate_context_budget({"tokenBudget": 0})
        assert any("tokenBudget must be a positive number" in r for r in reasons)

    def test_negative_budget_rejected(self):
        reasons = validate_context_budget({"tokenBudget": -100})
        assert any("tokenBudget must be a positive number" in r for r in reasons)

    def test_no_fields_passes(self):
        assert validate_context_budget({}) == []


class TestMemorySourceValidator:
    def test_valid_recall(self):
        reasons = validate_memory_source({
            "action": "recall",
            "sourceIds": ["mem-1", "mem-2"],
        })
        assert reasons == []

    def test_all_valid_actions(self):
        for action in ["recall", "extract", "invalidate", "update"]:
            reasons = validate_memory_source({"action": action})
            assert reasons == [], f"action {action!r} should be valid"

    def test_invalid_action(self):
        reasons = validate_memory_source({"action": "delete"})
        assert any("Invalid memory action" in r for r in reasons)

    def test_no_action_passes(self):
        assert validate_memory_source({}) == []

    def test_source_ids_not_list(self):
        reasons = validate_memory_source({"action": "recall", "sourceIds": "mem-1"})
        assert any("sourceIds must be a list" in r for r in reasons)

    def test_source_id_not_string(self):
        reasons = validate_memory_source({"action": "recall", "sourceIds": [42]})
        assert any("sourceIds[0] must be a non-empty string" in r for r in reasons)

    def test_source_id_empty_string(self):
        reasons = validate_memory_source({"action": "recall", "sourceIds": ["  "]})
        assert any("sourceIds[0] must be a non-empty string" in r for r in reasons)


class TestRetryBudgetValidator:
    def test_valid_all_fields(self):
        reasons = validate_retry_budget({
            "maxRetries": 3,
            "retryDelayMs": 1000,
            "strategy": "retry",
        })
        assert reasons == []

    def test_no_fields_passes(self):
        assert validate_retry_budget({}) == []

    def test_max_retries_negative(self):
        reasons = validate_retry_budget({"maxRetries": -1})
        assert any("maxRetries must be a non-negative integer" in r for r in reasons)

    def test_max_retries_exceeds_limit(self):
        reasons = validate_retry_budget({"maxRetries": 15})
        assert any("maxRetries must not exceed 10" in r for r in reasons)

    def test_max_retries_zero_ok(self):
        assert validate_retry_budget({"maxRetries": 0}) == []

    def test_max_retries_float_rejected(self):
        reasons = validate_retry_budget({"maxRetries": 3.5})
        assert any("maxRetries must be a non-negative integer" in r for r in reasons)

    def test_retry_delay_negative(self):
        reasons = validate_retry_budget({"retryDelayMs": -100})
        assert any("retryDelayMs must be a non-negative number" in r for r in reasons)

    def test_invalid_strategy(self):
        reasons = validate_retry_budget({"strategy": "explode"})
        assert any("Invalid recovery strategy" in r for r in reasons)

    def test_all_valid_strategies(self):
        for s in ["retry", "fallback", "skip", "abort", "ask_user"]:
            reasons = validate_retry_budget({"strategy": s})
            assert reasons == [], f"strategy {s!r} should be valid"


class TestFrontendVisibilityValidator:
    def test_valid_visibility(self):
        for v in ["chat", "panel", "trace"]:
            reasons = validate_frontend_visibility({"visibility": v})
            assert reasons == [], f"visibility {v!r} should be valid"

    def test_invalid_visibility(self):
        reasons = validate_frontend_visibility({"visibility": "hologram"})
        assert any("Invalid visibility" in r for r in reasons)

    def test_no_visibility_passes(self):
        assert validate_frontend_visibility({}) == []

    def test_grouping_not_list(self):
        reasons = validate_frontend_visibility({"grouping": "label"})
        assert any("grouping must be a list" in r for r in reasons)

    def test_grouping_item_not_dict(self):
        reasons = validate_frontend_visibility({"grouping": ["not a dict"]})
        assert any("grouping[0] must be a dict" in r for r in reasons)

    def test_grouping_item_missing_label(self):
        reasons = validate_frontend_visibility({"grouping": [{"name": "x"}]})
        assert any("grouping[0] missing required field 'label'" in r for r in reasons)

    def test_valid_grouping(self):
        reasons = validate_frontend_visibility({
            "grouping": [{"label": "Steps"}, {"label": "Artifacts"}],
        })
        assert reasons == []


class TestRoadmapEditValidator:
    def test_valid_updates(self):
        reasons = validate_roadmap_edit({
            "updates": [{"action": "check"}, {"action": "add"}],
        })
        assert reasons == []

    def test_updates_not_list(self):
        reasons = validate_roadmap_edit({"updates": "check"})
        assert any("updates must be a list" in r for r in reasons)

    def test_updates_empty(self):
        reasons = validate_roadmap_edit({"updates": []})
        assert any("updates must be non-empty" in r for r in reasons)

    def test_update_not_dict(self):
        reasons = validate_roadmap_edit({"updates": ["not a dict"]})
        assert any("updates[0] must be a dict" in r for r in reasons)

    def test_invalid_action(self):
        reasons = validate_roadmap_edit({"updates": [{"action": "nuke"}]})
        assert any("invalid action" in r for r in reasons)

    def test_all_valid_actions(self):
        for action in ["check", "add", "remove", "reorder", "update_status"]:
            reasons = validate_roadmap_edit({"updates": [{"action": action}]})
            assert reasons == [], f"action {action!r} should be valid"

    def test_requires_approval_default_true(self):
        reasons = validate_roadmap_edit({"updates": [{"action": "check"}]})
        assert reasons == []

    def test_requires_approval_false_rejected(self):
        reasons = validate_roadmap_edit({
            "updates": [{"action": "check"}],
            "requiresApproval": False,
        })
        assert any("must require approval" in r for r in reasons)

    def test_no_action_passes(self):
        """update item without 'action' field is fine."""
        reasons = validate_roadmap_edit({"updates": [{}]})
        assert reasons == []
