"""P4-P11: LLM Proposal Flow Tests.

Tests that validate the full proposal flow for each decision domain:
LLM proposes a value -> runtime validates -> accept/reject/repair.

These tests cover the "Let LLM propose X" todolist items by verifying
that the proposal validator system correctly handles LLM-generated proposals
across all decision domains.
"""

from __future__ import annotations

from local_agent_runtime.policy.planner_contract import (
    validate_agent_profile,
    validate_planner_output,
)
from local_agent_runtime.policy.proposal_validator import (
    validate_approval_gates,
    validate_context_budget,
    validate_frontend_visibility,
    validate_memory_source,
    validate_mode,
    validate_model_provider,
    validate_mcp_availability,
    validate_no_unsafe_tools,
    validate_proposal,
    validate_retry_budget,
    validate_risk_policy,
    validate_roadmap_edit,
    validate_session_task_state,
    validate_skill_availability,
    validate_test_strategy,
    validate_tool_allowlist,
)
from local_agent_runtime.policy.synthesis_contract import (
    validate_synthesis_output,
    validate_trace_summary_output,
)


# ---------------------------------------------------------------------------
# P4: Intent and Mode Routing
# ---------------------------------------------------------------------------


class TestIntentModeProposalFlow:
    """LLM proposes intent mode -> runtime validates."""

    def test_llm_proposes_direct_mode_accepted(self):
        """LLM can propose 'direct' mode for simple questions."""
        reasons = validate_proposal("intent_mode", {
            "mode": "direct",
        })
        assert reasons == []

    def test_llm_proposes_task_mode_accepted(self):
        """LLM can propose 'task' mode for complex requests."""
        reasons = validate_proposal("intent_mode", {
            "mode": "task",
            "taskState": "running",
        })
        assert reasons == []

    def test_llm_proposes_queued_mode_accepted(self):
        """LLM can propose 'queued' mode when a task is running."""
        reasons = validate_proposal("intent_mode", {
            "mode": "queued",
        })
        assert reasons == []

    def test_llm_proposes_supplement_mode_accepted(self):
        """LLM can propose 'supplement' mode for active tasks."""
        reasons = validate_proposal("intent_mode", {
            "mode": "supplement",
            "taskState": "running",
        })
        assert reasons == []

    def test_llm_proposes_clarification_mode_accepted(self):
        """LLM can propose 'clarification' mode when needed."""
        reasons = validate_proposal("intent_mode", {
            "mode": "clarification",
        })
        assert reasons == []

    def test_llm_proposes_invalid_mode_rejected(self):
        """Invalid mode proposal is rejected."""
        reasons = validate_proposal("intent_mode", {
            "mode": "teleport",
        })
        assert any("Invalid mode" in r for r in reasons)

    def test_llm_proposes_supplement_without_active_task_rejected(self):
        """Supplement mode without active task is rejected."""
        reasons = validate_proposal("intent_mode", {
            "mode": "supplement",
            "taskState": "completed",
        })
        assert any("Supplement mode requires" in r for r in reasons)

    def test_fallback_to_deterministic_routing(self):
        """When LLM fails to propose mode, system falls back to deterministic routing.
        The proposal schema requires 'mode' field, so an empty proposal is rejected.
        In practice, the planner catches this and uses rule-based routing instead.
        """
        reasons = validate_proposal("intent_mode", {})
        assert any("mode" in r for r in reasons)  # schema rejects missing mode

    def test_invalid_session_state_rejected(self):
        """Invalid session state in proposal is rejected."""
        reasons = validate_proposal("intent_mode", {
            "mode": "task",
            "sessionState": "exploding",
        })
        assert any("sessionState" in r for r in reasons)

    def test_invalid_task_state_rejected(self):
        """Invalid task state in proposal is rejected."""
        reasons = validate_proposal("intent_mode", {
            "mode": "task",
            "taskState": "vaporized",
        })
        assert any("taskState" in r for r in reasons)


# ---------------------------------------------------------------------------
# P5: Model and Provider Policy
# ---------------------------------------------------------------------------


class TestModelProposalFlow:
    """LLM proposes model selection -> runtime validates."""

    def test_llm_proposes_strong_model_for_planning(self):
        """LLM can propose a strong model for planning tasks."""
        reasons = validate_proposal("model_policy", {
            "model": "claude-opus-4-7",
            "provider": "anthropic",
        })
        assert reasons == []

    def test_llm_proposes_cheaper_model_for_summary(self):
        """LLM can propose a cheaper model for summarization."""
        reasons = validate_proposal("model_policy", {
            "model": "claude-haiku-4-5",
            "provider": "anthropic",
        })
        assert reasons == []

    def test_llm_proposes_model_by_complexity(self):
        """LLM can select model class based on task complexity."""
        # Simple task -> haiku
        reasons = validate_proposal("model_policy", {
            "model": "claude-haiku-4-5",
        })
        assert reasons == []
        # Complex task -> opus
        reasons = validate_proposal("model_policy", {
            "model": "claude-opus-4-7",
        })
        assert reasons == []

    def test_llm_proposes_unknown_model_rejected(self):
        """Unknown model proposal is rejected."""
        reasons = validate_proposal("model_policy", {
            "model": "gpt-99-ultra",
        })
        assert any("Unknown model" in r for r in reasons)

    def test_llm_proposes_unknown_provider_rejected(self):
        """Unknown provider proposal is rejected."""
        reasons = validate_proposal("model_policy", {
            "provider": "deepmind",
        })
        assert any("Unknown provider" in r for r in reasons)

    def test_llm_proposes_over_budget_rejected(self):
        """Model proposal exceeding budget is rejected."""
        reasons = validate_proposal("model_policy", {
            "model": "claude-opus-4-7",
            "budget": -5,
        })
        assert any("budget" in r for r in reasons)

    def test_llm_proposes_valid_budget_accepted(self):
        """Model proposal within budget is accepted."""
        reasons = validate_proposal("model_policy", {
            "model": "claude-sonnet-4-6",
            "budget": 1000,
        })
        assert reasons == []


# ---------------------------------------------------------------------------
# P6: Skill, Tool, and MCP Policy
# ---------------------------------------------------------------------------


class TestSkillProposalFlow:
    """LLM proposes skill selection -> runtime validates."""

    def test_llm_proposes_installed_skill_accepted(self):
        """LLM can propose an installed skill."""
        reasons = validate_proposal("skill_policy", {
            "skillId": "commit",
            "skills": ["commit"],
            "installedSkills": ["commit", "review-pr"],
            "role": "child",
        })
        assert reasons == []

    def test_llm_proposes_no_skill(self):
        """LLM can propose no skill when none is needed."""
        reasons = validate_proposal("skill_policy", {
            "skillId": "",
        })
        assert all("not installed" not in r for r in reasons)

    def test_llm_proposes_uninstalled_skill_rejected(self):
        """LLM proposing an uninstalled skill is rejected."""
        reasons = validate_proposal("skill_policy", {
            "skillId": "nonexistent",
            "skills": ["nonexistent"],
            "installedSkills": ["commit"],
            "role": "child",
        })
        assert any("not installed" in r for r in reasons)


class TestToolProposalFlow:
    """LLM proposes tool selection -> runtime validates."""

    def test_llm_proposes_read_only_tools(self):
        """LLM can propose a minimal read-only tool set."""
        reasons = validate_proposal("tool_policy", {
            "allowedTools": ["read_file", "search_files"],
        })
        assert reasons == []

    def test_llm_proposes_write_capable_tools(self):
        """LLM can propose write-capable tools."""
        reasons = validate_proposal("tool_policy", {
            "allowedTools": ["read_file", "apply_patch", "run_command"],
        })
        assert reasons == []

    def test_llm_proposes_unsafe_tools_rejected(self):
        """LLM proposing unsafe tools is rejected."""
        reasons = validate_proposal("tool_policy", {
            "allowedTools": ["read_file", "task"],
        })
        assert any("Unsafe" in r or "disallowed" in r for r in reasons)

    def test_llm_proposes_minimal_required_tools(self):
        """LLM can propose a minimal set of required tools."""
        reasons = validate_proposal("tool_policy", {
            "allowedTools": ["read_file"],
        })
        assert reasons == []


class TestMCPProposalFlow:
    """LLM proposes MCP server selection -> runtime validates."""

    def test_llm_proposes_mcp_server(self):
        """LLM can propose a relevant MCP server."""
        reasons = validate_proposal("mcp_policy", {
            "serverId": "github-mcp",
        })
        assert reasons == []

    def test_llm_proposes_mcp_with_tools(self):
        """LLM can propose MCP server with tool schemas."""
        reasons = validate_proposal("mcp_policy", {
            "serverId": "github-mcp",
            "tools": [
                {"name": "create_issue", "description": "Create a GitHub issue"},
                {"name": "list_prs", "description": "List pull requests"},
            ],
        })
        assert reasons == []

    def test_llm_proposes_mcp_invalid_schema_rejected(self):
        """LLM proposing MCP tool without name is rejected."""
        reasons = validate_proposal("mcp_policy", {
            "serverId": "github-mcp",
            "tools": [{"description": "No name"}],
        })
        assert any("name" in r for r in reasons)

    def test_llm_proposes_empty_server_id_rejected(self):
        """LLM proposing empty server ID is rejected."""
        reasons = validate_proposal("mcp_policy", {
            "serverId": "",
        })
        assert any("serverId" in r for r in reasons)


# ---------------------------------------------------------------------------
# P7: Context and Memory Policy
# ---------------------------------------------------------------------------


class TestContextProposalFlow:
    """LLM proposes context assembly -> runtime validates."""

    def test_llm_proposes_task_relevant_sections(self):
        """LLM can propose task-relevant context sections."""
        reasons = validate_proposal("context_policy", {
            "sections": [
                {"name": "system"},
                {"name": "safety"},
                {"name": "memory"},
                {"name": "workspace_files"},
            ],
            "tokenBudget": 8000,
        })
        assert reasons == []

    def test_llm_proposes_sections_to_summarize(self):
        """LLM can propose context sections to summarize (lower budget)."""
        reasons = validate_proposal("context_policy", {
            "sections": [
                {"name": "system"},
                {"name": "safety"},
                {"name": "long_history"},
            ],
            "tokenBudget": 2000,
        })
        assert reasons == []

    def test_llm_proposes_over_budget_rejected(self):
        """LLM proposing over budget (zero/negative) is rejected."""
        reasons = validate_proposal("context_policy", {
            "sections": [{"name": "system"}, {"name": "safety"}],
            "tokenBudget": 0,
        })
        assert any("tokenBudget" in r for r in reasons)

    def test_llm_drops_required_section_rejected(self):
        """LLM dropping required system/safety sections is rejected."""
        reasons = validate_proposal("context_policy", {
            "sections": [{"name": "memory"}],
        })
        assert any("Required context section" in r for r in reasons)


class TestMemoryProposalFlow:
    """LLM proposes memory operations -> runtime validates."""

    def test_llm_proposes_memory_recall(self):
        """LLM can propose focused memory recall."""
        reasons = validate_proposal("memory_policy", {
            "action": "recall",
            "sourceIds": ["mem-1", "mem-2"],
        })
        assert reasons == []

    def test_llm_proposes_memory_extraction(self):
        """LLM can propose memory extraction from completed work."""
        reasons = validate_proposal("memory_policy", {
            "action": "extract",
        })
        assert reasons == []

    def test_llm_proposes_memory_invalidation(self):
        """LLM can propose stale memory invalidation."""
        reasons = validate_proposal("memory_policy", {
            "action": "invalidate",
            "sourceIds": ["old-mem-1"],
        })
        assert reasons == []

    def test_llm_proposes_invalid_source_id_rejected(self):
        """LLM proposing invalid memory source IDs is rejected."""
        reasons = validate_proposal("memory_policy", {
            "action": "recall",
            "sourceIds": ["", 42],
        })
        assert any("sourceIds" in r for r in reasons)

    def test_llm_proposes_invalid_action_rejected(self):
        """LLM proposing invalid memory action is rejected."""
        reasons = validate_proposal("memory_policy", {
            "action": "delete_all",
        })
        assert any("Invalid memory action" in r for r in reasons)


# ---------------------------------------------------------------------------
# P9: Risk, Approval, Test, and Recovery
# ---------------------------------------------------------------------------


class TestRiskProposalFlow:
    """LLM proposes risk level -> runtime validates."""

    def test_llm_proposes_low_risk(self):
        """LLM can propose low risk level."""
        reasons = validate_proposal("risk_policy", {
            "riskLevel": "low",
        })
        assert reasons == []

    def test_llm_proposes_high_risk_with_gates(self):
        """LLM can propose high risk with required approval gates."""
        reasons = validate_proposal("risk_policy", {
            "riskLevel": "high",
            "approvalGates": ["reviewer"],
        })
        assert reasons == []

    def test_llm_proposes_critical_risk_with_all_gates(self):
        """LLM can propose critical risk with all required gates."""
        reasons = validate_proposal("risk_policy", {
            "riskLevel": "critical",
            "approvalGates": ["reviewer", "verifier"],
        })
        assert reasons == []

    def test_llm_proposes_critical_risk_without_gates_rejected(self):
        """Critical risk without required gates is rejected."""
        reasons = validate_proposal("risk_policy", {
            "riskLevel": "critical",
        })
        assert any("requires approval gate" in r for r in reasons)

    def test_llm_proposes_reviewer_requirements(self):
        """LLM can propose reviewer requirements via planner output."""
        reasons = validate_planner_output({
            "title": "Refactor core module",
            "subtasks": [
                {"name": "worker", "baseType": "worker", "mission": "Refactor",
                 "dependencies": [], "ownedScope": ["src/core/"],
                 "allowedTools": ["read_file", "apply_patch"]},
                {"name": "reviewer", "baseType": "reviewer",
                 "mission": "Review changes", "dependencies": ["worker"]},
            ],
        })
        assert reasons == []

    def test_llm_proposes_test_strategy(self):
        """LLM can propose test strategy based on changed files and risk."""
        reasons = validate_proposal("test_strategy", {
            "commands": ["python -m pytest tests/test_core.py -v"],
        })
        assert reasons == []

    def test_llm_proposes_dangerous_test_rejected(self):
        """LLM proposing dangerous test commands is rejected."""
        reasons = validate_proposal("test_strategy", {
            "commands": ["rm -rf / && pytest"],
        })
        assert any("dangerous" in r for r in reasons)


class TestRetryRecoveryProposalFlow:
    """LLM proposes retry/fallback strategy -> runtime validates."""

    def test_llm_proposes_retry_strategy(self):
        """LLM can propose a retry strategy."""
        reasons = validate_proposal("failure_recovery", {
            "strategy": "retry",
            "maxRetries": 3,
            "retryDelayMs": 1000,
        })
        assert reasons == []

    def test_llm_proposes_fallback_strategy(self):
        """LLM can propose a fallback strategy."""
        reasons = validate_proposal("failure_recovery", {
            "strategy": "fallback",
            "maxRetries": 1,
        })
        assert reasons == []

    def test_llm_proposes_skip_strategy(self):
        """LLM can propose to skip on failure."""
        reasons = validate_proposal("failure_recovery", {
            "strategy": "skip",
        })
        assert reasons == []

    def test_llm_proposes_ask_user_on_ambiguous_failure(self):
        """LLM can propose asking user for clarification on ambiguous failure."""
        reasons = validate_proposal("failure_recovery", {
            "strategy": "ask_user",
        })
        assert reasons == []

    def test_llm_proposes_retry_budget_exhaustion(self):
        """LLM proposing retries exceeding limit is rejected."""
        reasons = validate_proposal("failure_recovery", {
            "strategy": "retry",
            "maxRetries": 15,
        })
        assert any("maxRetries must not exceed" in r for r in reasons)

    def test_llm_proposes_negative_retry_delay_rejected(self):
        """LLM proposing negative retry delay is rejected."""
        reasons = validate_proposal("failure_recovery", {
            "strategy": "retry",
            "retryDelayMs": -100,
        })
        assert any("retryDelayMs" in r for r in reasons)

    def test_llm_proposes_invalid_strategy_rejected(self):
        """LLM proposing invalid strategy is rejected."""
        reasons = validate_proposal("failure_recovery", {
            "strategy": "explode",
        })
        assert any("Invalid recovery strategy" in r for r in reasons)

    def test_retry_budget_exhaustion_scenario(self):
        """Simulate retry budget exhaustion: max retries reached, all failed."""
        # Simulate 3 retries exhausted
        budget = {"maxRetries": 3, "retryDelayMs": 1000, "strategy": "retry"}
        reasons = validate_proposal("failure_recovery", budget)
        assert reasons == []

        # After exhaustion, system should propose fallback
        fallback = {"strategy": "fallback", "maxRetries": 0}
        reasons = validate_proposal("failure_recovery", fallback)
        assert reasons == []

    def test_zero_retries_means_no_retry(self):
        """Zero retries means immediate fallback/skip."""
        reasons = validate_proposal("failure_recovery", {
            "strategy": "skip",
            "maxRetries": 0,
        })
        assert reasons == []


class TestApprovalProposalFlow:
    """LLM proposes approval gates -> runtime validates."""

    def test_llm_proposes_reviewer_gate(self):
        """LLM can propose a reviewer approval gate."""
        reasons = validate_proposal("approval_policy", {
            "gates": [{"type": "reviewer"}],
        })
        assert reasons == []

    def test_llm_proposes_verifier_gate(self):
        """LLM can propose a verifier approval gate."""
        reasons = validate_proposal("approval_policy", {
            "gates": [{"type": "verifier"}],
        })
        assert reasons == []

    def test_llm_proposes_user_gate(self):
        """LLM can propose a user approval gate."""
        reasons = validate_proposal("approval_policy", {
            "gates": [{"type": "user"}],
        })
        assert reasons == []

    def test_llm_proposes_conditional_gate(self):
        """LLM can propose a conditional approval gate."""
        reasons = validate_proposal("approval_policy", {
            "gates": [{"type": "reviewer", "condition": "All tests pass"}],
        })
        assert reasons == []

    def test_llm_proposes_empty_gates_rejected(self):
        """Empty gates list is rejected."""
        reasons = validate_proposal("approval_policy", {
            "gates": [],
        })
        assert any("non-empty" in r for r in reasons)

    def test_llm_proposes_invalid_gate_type_rejected(self):
        """Invalid gate type is rejected."""
        reasons = validate_proposal("approval_policy", {
            "gates": [{"type": "ceo_approval"}],
        })
        assert any("invalid gate type" in r for r in reasons)


# ---------------------------------------------------------------------------
# P10: Event Presentation
# ---------------------------------------------------------------------------


class TestEventPresentationProposalFlow:
    """LLM proposes trace summary grouping -> runtime validates."""

    def test_llm_proposes_trace_summary_grouping(self):
        """LLM can propose trace summary grouping."""
        reasons = validate_proposal("event_presentation", {
            "grouping": [
                {"label": "Planning Phase"},
                {"label": "Execution Phase"},
                {"label": "Review Phase"},
            ],
        })
        assert reasons == []

    def test_llm_proposes_panel_labels(self):
        """LLM can propose panel summary labels."""
        reasons = validate_proposal("event_presentation", {
            "grouping": [{"label": "Child Tasks"}, {"label": "Artifacts"}],
        })
        assert reasons == []

    def test_llm_proposes_event_visibility(self):
        """LLM can propose event visibility routing."""
        for visibility in ("chat", "panel", "trace"):
            reasons = validate_proposal("event_presentation", {
                "grouping": [{"label": "Events"}],
                "visibility": visibility,
            })
            assert reasons == [], f"visibility {visibility!r} should be valid"

    def test_llm_proposes_invalid_visibility_rejected(self):
        """Invalid visibility is rejected."""
        reasons = validate_proposal("event_presentation", {
            "grouping": [{"label": "Events"}],
            "visibility": "secret",
        })
        assert any("Invalid visibility" in r for r in reasons)


class TestTraceSummaryProposalFlow:
    """LLM proposes trace summary -> runtime validates."""

    def test_llm_proposes_trace_summary(self):
        """LLM can propose a trace summary linked to raw events."""
        reasons = validate_trace_summary_output({
            "parentTaskId": "pt-1",
            "eventRange": {"afterSeq": 0, "beforeSeq": 500},
            "summary": "Completed planning and execution phases.",
            "failures": [],
            "retries": ["retry-1"],
            "generatedArtifacts": ["art-1"],
            "reviewDecisions": ["approved"],
        })
        assert reasons == []

    def test_trace_summary_preserves_raw_event_links(self):
        """Trace summary output preserves event range for raw access."""
        payload = {
            "parentTaskId": "pt-1",
            "eventRange": {"afterSeq": 10, "beforeSeq": 200},
            "summary": "Summarized events 10-200.",
        }
        reasons = validate_trace_summary_output(payload)
        assert reasons == []
        # Verify the range is preserved for raw trace access
        assert payload["eventRange"]["afterSeq"] == 10
        assert payload["eventRange"]["beforeSeq"] == 200


# ---------------------------------------------------------------------------
# P11: TODO and Roadmap Maintenance
# ---------------------------------------------------------------------------


class TestTODOMaintenanceProposalFlow:
    """LLM proposes TODO/roadmap updates -> runtime validates."""

    def test_llm_proposes_todo_check(self):
        """LLM can propose checking TODO status."""
        reasons = validate_proposal("todo_maintenance", {
            "updates": [{"action": "check", "todoId": "todo-1"}],
        })
        assert reasons == []

    def test_llm_proposes_todo_update_with_approval(self):
        """LLM can propose TODO updates requiring approval."""
        reasons = validate_proposal("todo_maintenance", {
            "updates": [{"action": "update_status", "todoId": "todo-1", "status": "done"}],
            "requiresApproval": True,
        })
        assert reasons == []

    def test_llm_proposes_roadmap_edit_without_approval_rejected(self):
        """Roadmap edit without approval is rejected."""
        reasons = validate_proposal("todo_maintenance", {
            "updates": [{"action": "add", "text": "New feature"}],
            "requiresApproval": False,
        })
        assert any("must require approval" in r for r in reasons)

    def test_llm_proposes_new_todo_from_reviewer(self):
        """LLM can propose new TODOs from reviewer findings."""
        reasons = validate_proposal("todo_maintenance", {
            "updates": [
                {"action": "add", "text": "Fix security vulnerability found in review"},
            ],
        })
        assert reasons == []

    def test_llm_proposes_next_batch_priority(self):
        """LLM can propose priority reordering."""
        reasons = validate_proposal("todo_maintenance", {
            "updates": [
                {"action": "reorder", "todoId": "todo-1", "position": 0},
            ],
        })
        assert reasons == []

    def test_roadmap_evidence_linking(self):
        """Roadmap suggestions can reference trace/artifact evidence."""
        reasons = validate_proposal("todo_maintenance", {
            "updates": [
                {
                    "action": "update_status",
                    "todoId": "todo-1",
                    "evidence": {
                        "artifactId": "art-1",
                        "traceRange": {"afterSeq": 10, "beforeSeq": 50},
                    },
                },
            ],
        })
        assert reasons == []


# ---------------------------------------------------------------------------
# P12: Synthesis
# ---------------------------------------------------------------------------


class TestSynthesisProposalFlow:
    """LLM proposes final synthesis -> runtime validates."""

    def test_llm_proposes_synthesis_structure(self):
        """LLM can propose final synthesis structure."""
        reasons = validate_synthesis_output({
            "parentTaskId": "pt-1",
            "completedWork": [
                {"artifactIds": ["art-1"], "verifiedArtifactIds": ["art-1"]},
            ],
            "failedWork": [{"taskId": "ct-2", "reason": "timeout"}],
            "skippedWork": [],
        })
        assert reasons == []

    def test_llm_proposes_mixed_outcomes(self):
        """LLM can propose synthesis with mixed child outcomes."""
        reasons = validate_synthesis_output({
            "parentTaskId": "pt-1",
            "completedWork": [
                {"artifactIds": ["art-1", "art-2"], "verifiedArtifactIds": ["art-1", "art-2"]},
            ],
            "failedWork": [{"taskId": "ct-3"}],
            "skippedWork": [{"taskId": "ct-4"}],
        })
        assert reasons == []

    def test_llm_cannot_claim_unverified_artifacts(self):
        """LLM cannot claim unverified artifacts as complete."""
        reasons = validate_synthesis_output({
            "parentTaskId": "pt-1",
            "completedWork": [
                {"artifactIds": ["art-1", "art-2"], "verifiedArtifactIds": ["art-1"]},
            ],
            "failedWork": [],
            "skippedWork": [],
        })
        assert any("unverified" in r for r in reasons)
