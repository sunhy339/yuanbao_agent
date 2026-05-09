"""Tests for P7 Planner Contract + P8 DAG Scheduling.

Covers:
- Dynamic agent profile validation (required fields, baseType, tools, scopes, risk)
- Planner output validation (subtasks, dependencies, scopes, uniqueness)
- DAG execution order computation (layers, concurrency, cycle detection)
"""

from __future__ import annotations

import pytest

from local_agent_runtime.policy.planner_contract import (
    compute_blocked_downstream,
    compute_execution_order,
    validate_agent_profile,
    validate_planner_output,
)


# ---------------------------------------------------------------------------
# P7: Agent Profile Validation
# ---------------------------------------------------------------------------


class TestAgentProfileRequiredFields:
    def test_valid_minimal_profile(self):
        reasons = validate_agent_profile({
            "name": "Explorer",
            "baseType": "explorer",
            "mission": "Search workspace",
        })
        assert reasons == []

    def test_missing_name(self):
        reasons = validate_agent_profile({"baseType": "worker", "mission": "build"})
        assert any("name" in r for r in reasons)

    def test_missing_base_type(self):
        reasons = validate_agent_profile({"name": "X", "mission": "build"})
        assert any("baseType" in r for r in reasons)

    def test_missing_mission(self):
        reasons = validate_agent_profile({"name": "X", "baseType": "worker"})
        assert any("mission" in r for r in reasons)

    def test_empty_name(self):
        reasons = validate_agent_profile({"name": "", "baseType": "worker", "mission": "m"})
        assert any("name" in r for r in reasons)


class TestAgentProfileBaseType:
    @pytest.mark.parametrize("base_type", ["explorer", "worker", "reviewer", "verifier", "summarizer"])
    def test_valid_base_types(self, base_type: str):
        reasons = validate_agent_profile({
            "name": "X", "baseType": base_type, "mission": "m",
        })
        assert reasons == []

    def test_invalid_base_type(self):
        reasons = validate_agent_profile({
            "name": "X", "baseType": "manager", "mission": "m",
        })
        assert any("Invalid baseType" in r for r in reasons)


class TestAgentProfileTools:
    def test_valid_tools(self):
        reasons = validate_agent_profile({
            "name": "X", "baseType": "worker", "mission": "m",
            "allowedTools": ["read_file", "search_files"],
        })
        assert reasons == []

    def test_alias_resolved(self):
        reasons = validate_agent_profile({
            "name": "X", "baseType": "worker", "mission": "m",
            "allowedTools": ["rg", "cat"],
        })
        assert reasons == []

    def test_unsafe_tool_rejected(self):
        reasons = validate_agent_profile({
            "name": "X", "baseType": "worker", "mission": "m",
            "allowedTools": ["task"],
        })
        assert any("Unsafe tool" in r for r in reasons)

    def test_unknown_tool_rejected(self):
        reasons = validate_agent_profile({
            "name": "X", "baseType": "worker", "mission": "m",
            "allowedTools": ["nonexistent_tool"],
        })
        assert any("Unknown tool" in r for r in reasons)

    def test_tools_not_a_list(self):
        reasons = validate_agent_profile({
            "name": "X", "baseType": "worker", "mission": "m",
            "allowedTools": "read_file",
        })
        assert any("allowedTools must be a list" in r for r in reasons)

    def test_no_tools_field_passes(self):
        reasons = validate_agent_profile({
            "name": "X", "baseType": "explorer", "mission": "m",
        })
        assert reasons == []


class TestAgentProfileScope:
    def test_valid_scope_list(self):
        reasons = validate_agent_profile({
            "name": "X", "baseType": "worker", "mission": "m",
            "ownedScope": ["src/ui/"],
        })
        assert reasons == []

    def test_scope_as_string(self):
        reasons = validate_agent_profile({
            "name": "X", "baseType": "worker", "mission": "m",
            "ownedScope": "src/engine/",
        })
        assert reasons == []

    def test_invalid_scope(self):
        reasons = validate_agent_profile({
            "name": "X", "baseType": "worker", "mission": "m",
            "ownedScope": [""],
        })
        assert any("Invalid scope" in r for r in reasons)

    def test_scope_not_list_or_string(self):
        reasons = validate_agent_profile({
            "name": "X", "baseType": "worker", "mission": "m",
            "ownedScope": 42,
        })
        assert any("ownedScope must be" in r for r in reasons)


class TestAgentProfileRisk:
    @pytest.mark.parametrize("risk", ["low", "medium", "high", "critical"])
    def test_valid_risk_levels(self, risk: str):
        reasons = validate_agent_profile({
            "name": "X", "baseType": "worker", "mission": "m",
            "riskLevel": risk,
        })
        assert reasons == []

    def test_invalid_risk(self):
        reasons = validate_agent_profile({
            "name": "X", "baseType": "worker", "mission": "m",
            "riskLevel": "extreme",
        })
        assert any("Invalid riskLevel" in r for r in reasons)

    def test_no_risk_passes(self):
        reasons = validate_agent_profile({
            "name": "X", "baseType": "worker", "mission": "m",
        })
        assert reasons == []


class TestAgentProfileDoneCriteria:
    def test_valid_done_criteria(self):
        reasons = validate_agent_profile({
            "name": "X", "baseType": "worker", "mission": "m",
            "doneCriteria": ["files written", "tests pass"],
        })
        assert reasons == []

    def test_empty_done_criteria(self):
        reasons = validate_agent_profile({
            "name": "X", "baseType": "worker", "mission": "m",
            "doneCriteria": [],
        })
        assert any("doneCriteria must be" in r for r in reasons)

    def test_done_criteria_not_list(self):
        reasons = validate_agent_profile({
            "name": "X", "baseType": "worker", "mission": "m",
            "doneCriteria": "done",
        })
        assert any("doneCriteria must be" in r for r in reasons)


# ---------------------------------------------------------------------------
# P7: Planner Output Validation
# ---------------------------------------------------------------------------


class TestPlannerOutput:
    def _valid_output(self) -> dict:
        return {
            "title": "Build music player",
            "subtasks": [
                {
                    "name": "explorer",
                    "baseType": "explorer",
                    "mission": "Explore workspace",
                    "dependencies": [],
                    "ownedScope": ["src/"],
                },
                {
                    "name": "ui_worker",
                    "baseType": "worker",
                    "mission": "Build UI",
                    "dependencies": ["explorer"],
                    "ownedScope": ["src/ui/"],
                    "allowedTools": ["read_file", "apply_patch"],
                },
            ],
        }

    def test_valid_output(self):
        reasons = validate_planner_output(self._valid_output())
        assert reasons == []

    def test_missing_title(self):
        output = self._valid_output()
        del output["title"]
        reasons = validate_planner_output(output)
        assert any("title" in r for r in reasons)

    def test_missing_subtasks(self):
        reasons = validate_planner_output({"title": "T"})
        assert any("subtasks" in r for r in reasons)

    def test_empty_subtasks(self):
        reasons = validate_planner_output({"title": "T", "subtasks": []})
        assert any("subtasks" in r for r in reasons)

    def test_subtask_not_dict(self):
        reasons = validate_planner_output({"title": "T", "subtasks": ["not a dict"]})
        assert any("must be a dict" in r for r in reasons)

    def test_duplicate_names(self):
        output = {
            "title": "T",
            "subtasks": [
                {"name": "a", "baseType": "worker", "mission": "m1", "dependencies": []},
                {"name": "a", "baseType": "worker", "mission": "m2", "dependencies": []},
            ],
        }
        reasons = validate_planner_output(output)
        assert any("duplicate" in r for r in reasons)

    def test_unknown_dependency(self):
        output = {
            "title": "T",
            "subtasks": [
                {"name": "a", "baseType": "worker", "mission": "m", "dependencies": ["nonexistent"]},
            ],
        }
        reasons = validate_planner_output(output)
        assert any("unknown dependency" in r for r in reasons)

    def test_self_dependency(self):
        output = {
            "title": "T",
            "subtasks": [
                {"name": "a", "baseType": "worker", "mission": "m", "dependencies": ["a"]},
            ],
        }
        reasons = validate_planner_output(output)
        assert any("self-dependency" in r for r in reasons)

    def test_overlapping_scopes(self):
        output = {
            "title": "T",
            "subtasks": [
                {"name": "a", "baseType": "worker", "mission": "m1", "ownedScope": ["src/"], "dependencies": []},
                {"name": "b", "baseType": "worker", "mission": "m2", "ownedScope": ["src/"], "dependencies": []},
            ],
        }
        reasons = validate_planner_output(output)
        assert any("Overlapping" in r for r in reasons)

    def test_unsafe_tools_in_subtask(self):
        output = {
            "title": "T",
            "subtasks": [
                {"name": "a", "baseType": "worker", "mission": "m", "allowedTools": ["task"], "dependencies": []},
            ],
        }
        reasons = validate_planner_output(output)
        assert any("Unsafe tool" in r for r in reasons)

    def test_multiple_errors(self):
        output = {
            "title": "T",
            "subtasks": [
                {"baseType": "invalid_type"},
                {"name": "a", "baseType": "worker", "mission": "m", "dependencies": ["missing"]},
            ],
        }
        reasons = validate_planner_output(output)
        assert len(reasons) >= 2  # missing fields + unknown dependency


# ---------------------------------------------------------------------------
# P8: DAG Scheduling — Execution Order
# ---------------------------------------------------------------------------


class TestDAGExecutionOrder:
    def test_single_task(self):
        layers = compute_execution_order([
            {"name": "a", "dependencies": []},
        ])
        assert layers == [["a"]]

    def test_serial_chain(self):
        layers = compute_execution_order([
            {"name": "a", "dependencies": []},
            {"name": "b", "dependencies": ["a"]},
            {"name": "c", "dependencies": ["b"]},
        ])
        assert layers == [["a"], ["b"], ["c"]]

    def test_independent_parallel(self):
        layers = compute_execution_order([
            {"name": "a", "dependencies": []},
            {"name": "b", "dependencies": []},
            {"name": "c", "dependencies": []},
        ])
        assert layers == [["a", "b", "c"]]

    def test_diamond_dependency(self):
        layers = compute_execution_order([
            {"name": "a", "dependencies": []},
            {"name": "b", "dependencies": ["a"]},
            {"name": "c", "dependencies": ["a"]},
            {"name": "d", "dependencies": ["b", "c"]},
        ])
        assert layers[0] == ["a"]
        assert set(layers[1]) == {"b", "c"}
        assert layers[2] == ["d"]

    def test_complex_dag(self):
        """
        a -> b -> d
        a -> c -> d
        e -> f
        """
        layers = compute_execution_order([
            {"name": "a", "dependencies": []},
            {"name": "b", "dependencies": ["a"]},
            {"name": "c", "dependencies": ["a"]},
            {"name": "d", "dependencies": ["b", "c"]},
            {"name": "e", "dependencies": []},
            {"name": "f", "dependencies": ["e"]},
        ])
        assert set(layers[0]) == {"a", "e"}
        assert set(layers[1]) == {"b", "c", "f"}
        assert layers[2] == ["d"]

    def test_cycle_detected(self):
        with pytest.raises(ValueError, match="[Cc]ircular|cycle"):
            compute_execution_order([
                {"name": "a", "dependencies": ["b"]},
                {"name": "b", "dependencies": ["a"]},
            ])

    def test_three_way_cycle(self):
        with pytest.raises(ValueError, match="[Cc]ircular|cycle"):
            compute_execution_order([
                {"name": "a", "dependencies": ["c"]},
                {"name": "b", "dependencies": ["a"]},
                {"name": "c", "dependencies": ["b"]},
            ])

    def test_self_cycle_detected(self):
        with pytest.raises(ValueError, match="[Cc]ircular|cycle"):
            compute_execution_order([
                {"name": "a", "dependencies": ["a"]},
            ])

    def test_unknown_deps_ignored(self):
        """Dependencies referencing non-existent task names are ignored."""
        layers = compute_execution_order([
            {"name": "a", "dependencies": ["ghost"]},
        ])
        assert layers == [["a"]]

    def test_empty_subtasks(self):
        layers = compute_execution_order([])
        assert layers == []

    def test_layers_are_sorted_for_determinism(self):
        layers = compute_execution_order([
            {"name": "c", "dependencies": []},
            {"name": "a", "dependencies": []},
            {"name": "b", "dependencies": []},
        ])
        assert layers[0] == ["a", "b", "c"]


# ---------------------------------------------------------------------------
# P7 Extended: Additional profile fields
# ---------------------------------------------------------------------------


class TestAgentProfileExpectedArtifacts:
    def test_valid_expected_artifacts(self):
        reasons = validate_agent_profile({
            "name": "X", "baseType": "worker", "mission": "m",
            "expectedArtifacts": [{"kind": "file", "path": "src/main.py"}],
        })
        assert reasons == []

    def test_expected_artifacts_not_list(self):
        reasons = validate_agent_profile({
            "name": "X", "baseType": "worker", "mission": "m",
            "expectedArtifacts": "file",
        })
        assert any("expectedArtifacts must be a list" in r for r in reasons)

    def test_expected_artifacts_missing_kind(self):
        reasons = validate_agent_profile({
            "name": "X", "baseType": "worker", "mission": "m",
            "expectedArtifacts": [{"path": "src/main.py"}],
        })
        assert any("missing required field 'kind'" in r for r in reasons)

    def test_expected_artifacts_not_dict(self):
        reasons = validate_agent_profile({
            "name": "X", "baseType": "worker", "mission": "m",
            "expectedArtifacts": ["not a dict"],
        })
        assert any("must be a dict" in r for r in reasons)

    def test_no_expected_artifacts_passes(self):
        reasons = validate_agent_profile({
            "name": "X", "baseType": "worker", "mission": "m",
        })
        assert reasons == []


class TestAgentProfilePrompt:
    def test_valid_prompt(self):
        reasons = validate_agent_profile({
            "name": "Explorer", "baseType": "explorer", "mission": "m",
            "prompt": "Search for all Python files in the src/ directory.",
        })
        assert reasons == []

    def test_prompt_not_string(self):
        reasons = validate_agent_profile({
            "name": "Explorer", "baseType": "explorer", "mission": "m",
            "prompt": 42,
        })
        assert any("prompt must be a string" in r for r in reasons)

    def test_no_prompt_passes(self):
        reasons = validate_agent_profile({
            "name": "Explorer", "baseType": "explorer", "mission": "m",
        })
        assert reasons == []

    def test_empty_prompt_passes(self):
        reasons = validate_agent_profile({
            "name": "Explorer", "baseType": "explorer", "mission": "m",
            "prompt": "",
        })
        assert reasons == []


class TestAgentProfileHandoffNotes:
    def test_valid_handoff_notes(self):
        reasons = validate_agent_profile({
            "name": "X", "baseType": "worker", "mission": "m",
            "handoffNotes": "Explore src/ directory structure",
        })
        assert reasons == []

    def test_handoff_notes_not_string(self):
        reasons = validate_agent_profile({
            "name": "X", "baseType": "worker", "mission": "m",
            "handoffNotes": 42,
        })
        assert any("handoffNotes must be a string" in r for r in reasons)


class TestAgentProfilePriority:
    def test_valid_priority(self):
        reasons = validate_agent_profile({
            "name": "X", "baseType": "worker", "mission": "m",
            "priority": 5,
        })
        assert reasons == []

    def test_priority_zero(self):
        reasons = validate_agent_profile({
            "name": "X", "baseType": "worker", "mission": "m",
            "priority": 0,
        })
        assert reasons == []

    def test_negative_priority(self):
        reasons = validate_agent_profile({
            "name": "X", "baseType": "worker", "mission": "m",
            "priority": -1,
        })
        assert any("priority must be" in r for r in reasons)

    def test_priority_not_int(self):
        reasons = validate_agent_profile({
            "name": "X", "baseType": "worker", "mission": "m",
            "priority": "high",
        })
        assert any("priority must be" in r for r in reasons)


class TestAgentProfileVerificationRequirements:
    def test_valid_verification(self):
        reasons = validate_agent_profile({
            "name": "X", "baseType": "worker", "mission": "m",
            "verificationRequirements": ["tests pass", "no lint errors"],
        })
        assert reasons == []

    def test_empty_verification(self):
        reasons = validate_agent_profile({
            "name": "X", "baseType": "worker", "mission": "m",
            "verificationRequirements": [],
        })
        assert any("verificationRequirements must be non-empty" in r for r in reasons)

    def test_verification_not_list(self):
        reasons = validate_agent_profile({
            "name": "X", "baseType": "worker", "mission": "m",
            "verificationRequirements": "tests pass",
        })
        assert any("verificationRequirements must be a list" in r for r in reasons)


# ---------------------------------------------------------------------------
# P7 Extended: Planner output approval gate check
# ---------------------------------------------------------------------------


class TestPlannerOutputApprovalGates:
    def test_high_risk_without_approval_gates(self):
        output = {
            "title": "T",
            "subtasks": [
                {
                    "name": "a", "baseType": "worker", "mission": "m",
                    "dependencies": [], "riskLevel": "high",
                },
            ],
        }
        reasons = validate_planner_output(output)
        assert any("requires approval gates" in r for r in reasons)

    def test_high_risk_with_approval_gates(self):
        output = {
            "title": "T",
            "subtasks": [
                {
                    "name": "a", "baseType": "worker", "mission": "m",
                    "dependencies": [], "riskLevel": "high",
                    "approvalGates": ["reviewer"],
                },
            ],
        }
        reasons = validate_planner_output(output)
        assert all("approval" not in r for r in reasons)

    def test_critical_risk_without_approval_gates(self):
        output = {
            "title": "T",
            "subtasks": [
                {
                    "name": "a", "baseType": "worker", "mission": "m",
                    "dependencies": [], "riskLevel": "critical",
                },
            ],
        }
        reasons = validate_planner_output(output)
        assert any("requires approval gates" in r for r in reasons)

    def test_low_risk_no_gates_needed(self):
        output = {
            "title": "T",
            "subtasks": [
                {
                    "name": "a", "baseType": "worker", "mission": "m",
                    "dependencies": [], "riskLevel": "low",
                },
            ],
        }
        reasons = validate_planner_output(output)
        assert all("approval" not in r for r in reasons)


# ---------------------------------------------------------------------------
# P8 Extended: Blocked downstream computation
# ---------------------------------------------------------------------------


class TestBlockedDownstream:
    def test_no_downstream(self):
        blocked = compute_blocked_downstream(
            [{"name": "a", "dependencies": []}],
            "a",
        )
        assert blocked == []

    def test_direct_downstream(self):
        blocked = compute_blocked_downstream([
            {"name": "a", "dependencies": []},
            {"name": "b", "dependencies": ["a"]},
        ], "a")
        assert blocked == ["b"]

    def test_transitive_downstream(self):
        """
        a -> b -> c
        a -> d
        """
        blocked = compute_blocked_downstream([
            {"name": "a", "dependencies": []},
            {"name": "b", "dependencies": ["a"]},
            {"name": "c", "dependencies": ["b"]},
            {"name": "d", "dependencies": ["a"]},
        ], "a")
        assert set(blocked) == {"b", "c", "d"}

    def test_diamond_downstream(self):
        """
        a -> b -> d
        a -> c -> d
        """
        blocked = compute_blocked_downstream([
            {"name": "a", "dependencies": []},
            {"name": "b", "dependencies": ["a"]},
            {"name": "c", "dependencies": ["a"]},
            {"name": "d", "dependencies": ["b", "c"]},
        ], "a")
        assert set(blocked) == {"b", "c", "d"}

    def test_partial_failure(self):
        """
        a -> b
        c -> d
        If b fails, only b's downstream is blocked (none).
        If a fails, b is blocked but c, d are not.
        """
        subtasks = [
            {"name": "a", "dependencies": []},
            {"name": "b", "dependencies": ["a"]},
            {"name": "c", "dependencies": []},
            {"name": "d", "dependencies": ["c"]},
        ]
        blocked_a = compute_blocked_downstream(subtasks, "a")
        assert blocked_a == ["b"]

        blocked_b = compute_blocked_downstream(subtasks, "b")
        assert blocked_b == []

        blocked_c = compute_blocked_downstream(subtasks, "c")
        assert blocked_c == ["d"]

    def test_nonexistent_task(self):
        blocked = compute_blocked_downstream(
            [{"name": "a", "dependencies": []}],
            "nonexistent",
        )
        assert blocked == []

    def test_results_are_sorted(self):
        blocked = compute_blocked_downstream([
            {"name": "a", "dependencies": []},
            {"name": "d", "dependencies": ["a"]},
            {"name": "b", "dependencies": ["a"]},
            {"name": "c", "dependencies": ["a"]},
        ], "a")
        assert blocked == ["b", "c", "d"]


# ---------------------------------------------------------------------------
# P7: Planner proposal repair after rejection
# ---------------------------------------------------------------------------


class TestPlannerProposalRepair:
    """Tests for repairing planner output after initial rejection.

    Simulates: planner proposes → validator rejects → planner repairs → accepted.
    """

    def _repairable_output(self) -> dict:
        """Output with fixable issues: unsafe tool and unknown dependency."""
        return {
            "title": "Build music player",
            "subtasks": [
                {
                    "name": "explorer",
                    "baseType": "explorer",
                    "mission": "Explore workspace",
                    "dependencies": [],
                    "allowedTools": ["task"],  # unsafe
                },
                {
                    "name": "worker",
                    "baseType": "worker",
                    "mission": "Build UI",
                    "dependencies": ["nonexistent"],  # unknown dep
                    "ownedScope": ["src/"],
                },
            ],
        }

    def test_initial_rejection_reasons(self):
        """First proposal has issues."""
        reasons = validate_planner_output(self._repairable_output())
        assert len(reasons) >= 2
        assert any("Unsafe tool" in r for r in reasons)
        assert any("unknown dependency" in r for r in reasons)

    def test_repair_unsafe_tool(self):
        """Replace unsafe tool with safe one → one less rejection."""
        output = self._repairable_output()
        output["subtasks"][0]["allowedTools"] = ["read_file", "search_files"]
        reasons = validate_planner_output(output)
        assert not any("Unsafe tool" in r for r in reasons)
        assert any("unknown dependency" in r for r in reasons)

    def test_repair_unknown_dependency(self):
        """Fix unknown dependency → one less rejection."""
        output = self._repairable_output()
        output["subtasks"][1]["dependencies"] = ["explorer"]
        reasons = validate_planner_output(output)
        assert any("Unsafe tool" in r for r in reasons)
        assert not any("unknown dependency" in r for r in reasons)

    def test_fully_repaired_output(self):
        """Fix all issues → proposal accepted."""
        output = {
            "title": "Build music player",
            "subtasks": [
                {
                    "name": "explorer",
                    "baseType": "explorer",
                    "mission": "Explore workspace",
                    "dependencies": [],
                    "allowedTools": ["read_file", "search_files"],
                    "prompt": "Search all Python files.",
                },
                {
                    "name": "worker",
                    "baseType": "worker",
                    "mission": "Build UI",
                    "dependencies": ["explorer"],
                    "ownedScope": ["src/ui/"],
                    "allowedTools": ["read_file", "apply_patch"],
                },
            ],
        }
        reasons = validate_planner_output(output)
        assert reasons == []

    def test_repair_overlapping_scopes(self):
        """Fix overlapping scopes and get accepted."""
        output = {
            "title": "T",
            "subtasks": [
                {"name": "a", "baseType": "worker", "mission": "m1",
                 "ownedScope": ["src/"], "dependencies": []},
                {"name": "b", "baseType": "worker", "mission": "m2",
                 "ownedScope": ["src/"], "dependencies": []},
            ],
        }
        # Initially rejected for overlapping scopes
        reasons = validate_planner_output(output)
        assert any("Overlapping" in r for r in reasons)

        # Repair: assign distinct scopes
        output["subtasks"][0]["ownedScope"] = ["src/ui/"]
        output["subtasks"][1]["ownedScope"] = ["src/engine/"]
        reasons = validate_planner_output(output)
        assert reasons == []

    def test_repair_high_risk_without_approval(self):
        """Fix missing approval gates for high-risk task."""
        output = {
            "title": "T",
            "subtasks": [
                {"name": "a", "baseType": "worker", "mission": "m",
                 "dependencies": [], "riskLevel": "high"},
            ],
        }
        reasons = validate_planner_output(output)
        assert any("approval gates" in r for r in reasons)

        # Repair: add approval gates
        output["subtasks"][0]["approvalGates"] = ["reviewer"]
        reasons = validate_planner_output(output)
        assert all("approval" not in r for r in reasons)
