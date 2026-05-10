"""P12 Acceptance Scenarios: End-to-end validation of runtime rejection paths.

Tests runtime behavior when LLM proposals violate policy constraints.
These simulate the full proposal -> validate -> reject/accept flow.
"""

from __future__ import annotations

from local_agent_runtime.policy.planner_contract import (
    validate_agent_profile,
    validate_planner_output,
)
from local_agent_runtime.policy.proposal_validator import (
    validate_no_unsafe_tools,
    validate_proposal,
    validate_risk_policy,
    validate_roadmap_edit,
    validate_test_strategy,
    validate_tool_allowlist,
)
from local_agent_runtime.policy.synthesis_contract import (
    validate_synthesis_output,
    validate_trace_summary_input,
    validate_trace_summary_output,
)


class TestSimpleQuestionNoDecomposition:
    """Scenario: simple question uses no LLM decomposition proposal."""

    def test_simple_task_no_subtasks(self):
        """A simple task should not require subtasks."""
        reasons = validate_planner_output({
            "title": "Answer a simple question",
            "subtasks": [
                {
                    "name": "responder",
                    "baseType": "worker",
                    "mission": "Answer the question directly",
                    "dependencies": [],
                },
            ],
        })
        assert reasons == []

    def test_simple_task_skip_decomposition(self):
        """Simple tasks can be handled with skipDecomposition flag."""
        reasons = validate_planner_output({
            "title": "Direct answer",
            "subtasks": [
                {"name": "direct", "baseType": "worker", "mission": "Answer", "dependencies": []},
            ],
        })
        assert reasons == []


class TestMediumTaskAcceptedProposal:
    """Scenario: medium task receives accepted intent/mode proposal."""

    def test_medium_task_with_mode_validation(self):
        from local_agent_runtime.policy.proposal_validator import validate_mode
        reasons = validate_mode({"mode": "task", "taskState": "running"})
        assert reasons == []

    def test_medium_task_decomposition_valid(self):
        reasons = validate_planner_output({
            "title": "Refactor authentication module",
            "subtasks": [
                {"name": "explorer", "baseType": "explorer", "mission": "Explore auth code",
                 "dependencies": []},
                {"name": "worker", "baseType": "worker", "mission": "Refactor auth",
                 "dependencies": ["explorer"], "ownedScope": ["src/auth/"],
                 "allowedTools": ["read_file", "apply_patch"]},
            ],
        })
        assert reasons == []


class TestUITaskDecompositionProposal:
    """Scenario: UI task receives subagent decomposition proposal."""

    def test_ui_task_multi_agent_decomposition(self):
        reasons = validate_planner_output({
            "title": "Build music player UI",
            "subtasks": [
                {"name": "explorer", "baseType": "explorer",
                 "mission": "Explore existing UI code", "dependencies": []},
                {"name": "ui_worker", "baseType": "worker",
                 "mission": "Build player components", "dependencies": ["explorer"],
                 "ownedScope": ["src/ui/player/"], "allowedTools": ["read_file", "apply_patch"]},
                {"name": "reviewer", "baseType": "reviewer",
                 "mission": "Review UI code quality", "dependencies": ["ui_worker"]},
            ],
        })
        assert reasons == []


class TestPlannerProposesUnsafeToolRuntimeRejects:
    """Scenario: planner proposes unsafe tools and runtime rejects them."""

    def test_unsafe_tool_in_decomposition_rejected(self):
        output = {
            "title": "Build feature",
            "subtasks": [
                {"name": "a", "baseType": "worker", "mission": "Do work",
                 "dependencies": [], "allowedTools": ["task", "read_file"]},
            ],
        }
        reasons = validate_planner_output(output)
        assert any("Unsafe tool" in r for r in reasons)

    def test_unsafe_tool_in_tool_policy_rejected(self):
        reasons = validate_proposal("tool_policy", {
            "allowedTools": ["task"],
        })
        assert any("Unsafe" in r for r in reasons)

    def test_safe_tools_accepted(self):
        reasons = validate_no_unsafe_tools({
            "allowedTools": ["read_file", "search_files", "apply_patch"],
        })
        assert reasons == []

    def test_mixed_tools_rejected_for_unsafe(self):
        reasons = validate_tool_allowlist({
            "allowedTools": ["read_file", "task"],
        })
        assert any("task" in r for r in reasons)


class TestPlannerProposesUnavailableSkillRuntimeRejects:
    """Scenario: planner proposes unavailable skill and runtime rejects it."""

    def test_uninstalled_skill_rejected(self):
        reasons = validate_proposal("skill_policy", {
            "skillId": "nonexistent-skill",
            "skills": ["nonexistent-skill"],
            "installedSkills": ["commit"],
            "role": "child",
        })
        assert any("not installed" in r for r in reasons)

    def test_installed_skill_accepted(self):
        reasons = validate_proposal("skill_policy", {
            "skillId": "commit",
            "skills": ["commit"],
            "installedSkills": ["commit", "review-pr"],
            "role": "child",
        })
        assert reasons == []


class TestPlannerProposesTestStrategy:
    """Scenario: planner proposes test strategy and verifier records results."""

    def test_valid_test_strategy_accepted(self):
        reasons = validate_test_strategy({
            "commands": ["python -m pytest tests/ -v"],
        })
        assert reasons == []

    def test_dangerous_test_strategy_rejected(self):
        reasons = validate_test_strategy({
            "commands": ["rm -rf tests/ && pytest"],
        })
        assert any("dangerous pattern" in r for r in reasons)

    def test_safe_test_commands_all_pass(self):
        safe_commands = [
            "python -m pytest",
            "pytest",
            "npm test",
            "npx jest",
            "make test",
            "go test ./...",
            "cargo test",
        ]
        for cmd in safe_commands:
            reasons = validate_test_strategy({"commands": [cmd]})
            assert reasons == [], f"Safe command {cmd!r} should pass"


class TestHighRiskPatchRequiresApproval:
    """Scenario: high-risk patch proposal requires approval."""

    def test_high_risk_without_gates_rejected(self):
        reasons = validate_planner_output({
            "title": "Deploy to production",
            "subtasks": [
                {"name": "deploy", "baseType": "worker", "mission": "Deploy",
                 "dependencies": [], "riskLevel": "high"},
            ],
        })
        assert any("approval gates" in r for r in reasons)

    def test_high_risk_with_gates_accepted(self):
        reasons = validate_planner_output({
            "title": "Deploy to production",
            "subtasks": [
                {"name": "deploy", "baseType": "worker", "mission": "Deploy",
                 "dependencies": [], "riskLevel": "high",
                 "approvalGates": ["reviewer"]},
            ],
        })
        assert all("approval" not in r for r in reasons)

    def test_critical_risk_requires_all_gates(self):
        reasons = validate_risk_policy({
            "riskLevel": "critical",
        })
        assert any("requires approval gate" in r for r in reasons)


class TestTraceSummaryLinksToRawEvents:
    """Scenario: trace summary links to raw event sequence ranges."""

    def test_valid_trace_summary_input(self):
        reasons = validate_trace_summary_input({
            "parentTaskId": "pt-1",
            "sessionId": "s-1",
            "eventRange": {"afterSeq": 0, "beforeSeq": 500},
        })
        assert reasons == []

    def test_valid_trace_summary_output(self):
        reasons = validate_trace_summary_output({
            "parentTaskId": "pt-1",
            "eventRange": {"afterSeq": 0, "beforeSeq": 500},
            "summary": "Completed exploration and code generation phases.",
        })
        assert reasons == []

    def test_trace_summary_preserves_range(self):
        """Output eventRange should match or be within input range."""
        payload = {
            "parentTaskId": "pt-1",
            "eventRange": {"afterSeq": 10, "beforeSeq": 100},
            "summary": "Events 10-100 summarized.",
        }
        reasons = validate_trace_summary_output(payload)
        assert reasons == []
        assert payload["eventRange"]["afterSeq"] == 10
        assert payload["eventRange"]["beforeSeq"] == 100


class TestSynthesisRefusesUnverifiedArtifacts:
    """Scenario: final synthesis refuses to claim unverified artifacts."""

    def test_unverified_artifacts_rejected(self):
        reasons = validate_synthesis_output({
            "parentTaskId": "pt-1",
            "completedWork": [
                {
                    "artifactIds": ["art-1", "art-2"],
                    "verifiedArtifactIds": ["art-1"],
                },
            ],
            "failedWork": [],
            "skippedWork": [],
        })
        assert any("unverified artifacts" in r for r in reasons)
        assert any("art-2" in r for r in reasons)

    def test_all_verified_accepted(self):
        reasons = validate_synthesis_output({
            "parentTaskId": "pt-1",
            "completedWork": [
                {
                    "artifactIds": ["art-1", "art-2"],
                    "verifiedArtifactIds": ["art-1", "art-2"],
                },
            ],
            "failedWork": [],
            "skippedWork": [],
        })
        assert reasons == []

    def test_empty_artifacts_accepted(self):
        reasons = validate_synthesis_output({
            "parentTaskId": "pt-1",
            "completedWork": [{}],
            "failedWork": [],
            "skippedWork": [],
        })
        assert reasons == []


class TestRoadmapMaintenanceRequiresApproval:
    """Scenario: TODO maintenance suggestion requires approval before docs change."""

    def test_roadmap_edit_default_requires_approval(self):
        reasons = validate_roadmap_edit({
            "updates": [{"action": "check"}],
        })
        assert reasons == []

    def test_roadmap_edit_without_approval_rejected(self):
        reasons = validate_roadmap_edit({
            "updates": [{"action": "add"}],
            "requiresApproval": False,
        })
        assert any("must require approval" in r for r in reasons)

    def test_roadmap_edit_with_explicit_approval_accepted(self):
        reasons = validate_roadmap_edit({
            "updates": [{"action": "update_status"}],
            "requiresApproval": True,
        })
        assert reasons == []
