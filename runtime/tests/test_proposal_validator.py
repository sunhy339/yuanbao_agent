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
    validate_dependency_graph,
    validate_no_unsafe_tools,
    validate_proposal,
    validate_proposal_schema,
    validate_skill_availability,
    validate_skill_root_allowlist,
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
