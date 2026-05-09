"""Tests for Batch 5: extended P3 validators, P9 write safety, and extended generation report.

Covers:
- Session/task state validator
- Mode validator
- Model/provider validator
- MCP availability validator
- Context budget validator
- Memory source validator
- Retry budget validator
- Frontend visibility validator
- Roadmap edit validator
- P9 write safety: patch scope, conflict detection, reviewer gate
- Extended generation report fields
- Composite validator wiring for all new kinds
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from local_agent_runtime.policy.proposal_validator import (
    detect_patch_conflicts,
    validate_context_budget,
    validate_frontend_visibility,
    validate_memory_source,
    validate_mcp_availability,
    validate_mode,
    validate_model_provider,
    validate_patch_in_scope,
    validate_proposal,
    validate_retry_budget,
    validate_roadmap_edit,
    validate_reviewer_gate,
    validate_session_task_state,
    validate_write_scope_overlap,
)
from local_agent_runtime.services.generation_report import build_generation_report
from local_agent_runtime.store.sqlite_store import SQLiteStore


# ---------------------------------------------------------------------------
# Session/Task State Validator
# ---------------------------------------------------------------------------


class TestSessionTaskStateValidator:
    def test_valid_active_session(self):
        assert validate_session_task_state({"sessionState": "active"}) == []

    def test_valid_paused_session(self):
        assert validate_session_task_state({"sessionState": "paused"}) == []

    def test_valid_closed_session(self):
        assert validate_session_task_state({"sessionState": "closed"}) == []

    def test_invalid_session_state(self):
        reasons = validate_session_task_state({"sessionState": "destroyed"})
        assert any("Invalid sessionState" in r for r in reasons)

    def test_valid_running_task(self):
        assert validate_session_task_state({"taskState": "running"}) == []

    def test_valid_queued_task(self):
        assert validate_session_task_state({"taskState": "queued"}) == []

    def test_valid_completed_task(self):
        assert validate_session_task_state({"taskState": "completed"}) == []

    def test_valid_failed_task(self):
        assert validate_session_task_state({"taskState": "failed"}) == []

    def test_valid_cancelled_task(self):
        assert validate_session_task_state({"taskState": "cancelled"}) == []

    def test_valid_paused_task(self):
        assert validate_session_task_state({"taskState": "paused"}) == []

    def test_invalid_task_state(self):
        reasons = validate_session_task_state({"taskState": "zombie"})
        assert any("Invalid taskState" in r for r in reasons)

    def test_no_state_passes(self):
        assert validate_session_task_state({}) == []

    def test_both_valid(self):
        assert validate_session_task_state({"sessionState": "active", "taskState": "running"}) == []


# ---------------------------------------------------------------------------
# Mode Validator
# ---------------------------------------------------------------------------


class TestModeValidator:
    @pytest.mark.parametrize("mode", ["direct", "task", "queued", "supplement", "collaboration", "clarification"])
    def test_valid_modes(self, mode: str):
        assert validate_mode({"mode": mode}) == []

    def test_invalid_mode(self):
        reasons = validate_mode({"mode": "telepathy"})
        assert any("Invalid mode" in r for r in reasons)

    def test_supplement_with_running_task(self):
        assert validate_mode({"mode": "supplement", "taskState": "running"}) == []

    def test_supplement_with_paused_task(self):
        assert validate_mode({"mode": "supplement", "taskState": "paused"}) == []

    def test_supplement_without_active_task(self):
        reasons = validate_mode({"mode": "supplement", "taskState": "completed"})
        assert any("Supplement mode requires" in r for r in reasons)

    def test_supplement_no_task_state_passes(self):
        """Without explicit taskState, supplement mode is valid at the proposal level."""
        assert validate_mode({"mode": "supplement"}) == []

    def test_no_mode_passes(self):
        assert validate_mode({}) == []


# ---------------------------------------------------------------------------
# Model/Provider Validator
# ---------------------------------------------------------------------------


class TestModelProviderValidator:
    @pytest.mark.parametrize("model", [
        "claude-sonnet-4-6", "claude-opus-4-7", "claude-haiku-4-5",
        "gpt-4o", "glm-5.1",
    ])
    def test_valid_models(self, model: str):
        assert validate_model_provider({"model": model}) == []

    def test_unknown_model(self):
        reasons = validate_model_provider({"model": "gpt-6"})
        assert any("Unknown model" in r for r in reasons)

    @pytest.mark.parametrize("provider", ["anthropic", "openai", "zhipu", "azure", "local"])
    def test_valid_providers(self, provider: str):
        assert validate_model_provider({"provider": provider}) == []

    def test_unknown_provider(self):
        reasons = validate_model_provider({"provider": "google"})
        assert any("Unknown provider" in r for r in reasons)

    def test_valid_budget(self):
        assert validate_model_provider({"budget": 100}) == []

    def test_zero_budget(self):
        assert validate_model_provider({"budget": 0}) == []

    def test_negative_budget(self):
        reasons = validate_model_provider({"budget": -1})
        assert any("budget must be" in r for r in reasons)

    def test_string_budget(self):
        reasons = validate_model_provider({"budget": "100"})
        assert any("budget must be" in r for r in reasons)

    def test_no_fields_passes(self):
        assert validate_model_provider({}) == []


# ---------------------------------------------------------------------------
# MCP Availability Validator
# ---------------------------------------------------------------------------


class TestMCPAvailabilityValidator:
    def test_valid_server(self):
        assert validate_mcp_availability({"serverId": "mcp-filesystem"}) == []

    def test_empty_server_id(self):
        reasons = validate_mcp_availability({"serverId": ""})
        assert any("non-empty string" in r for r in reasons)

    def test_non_string_server_id(self):
        reasons = validate_mcp_availability({"serverId": 42})
        assert any("non-empty string" in r for r in reasons)

    def test_valid_tools(self):
        reasons = validate_mcp_availability({
            "serverId": "mcp-fs",
            "tools": [{"name": "read_file"}, {"name": "write_file"}],
        })
        assert reasons == []

    def test_tools_not_list(self):
        reasons = validate_mcp_availability({
            "serverId": "mcp-fs",
            "tools": "read_file",
        })
        assert any("tools must be a list" in r for r in reasons)

    def test_tool_not_dict(self):
        reasons = validate_mcp_availability({
            "serverId": "mcp-fs",
            "tools": ["read_file"],
        })
        assert any("must be a dict" in r for r in reasons)

    def test_tool_missing_name(self):
        reasons = validate_mcp_availability({
            "serverId": "mcp-fs",
            "tools": [{"description": "no name"}],
        })
        assert any("missing required field 'name'" in r for r in reasons)

    def test_no_server_id_passes(self):
        assert validate_mcp_availability({}) == []


# ---------------------------------------------------------------------------
# Context Budget Validator
# ---------------------------------------------------------------------------


class TestContextBudgetValidator:
    def test_valid_sections(self):
        reasons = validate_context_budget({
            "sections": [
                {"name": "system"},
                {"name": "safety"},
                {"name": "messages"},
            ],
        })
        assert reasons == []

    def test_missing_required_section_safety(self):
        reasons = validate_context_budget({
            "sections": [{"name": "system"}, {"name": "messages"}],
        })
        assert any("Required context section missing: 'safety'" in r for r in reasons)

    def test_missing_required_section_system(self):
        reasons = validate_context_budget({
            "sections": [{"name": "safety"}, {"name": "messages"}],
        })
        assert any("Required context section missing: 'system'" in r for r in reasons)

    def test_sections_not_list(self):
        reasons = validate_context_budget({"sections": "system"})
        assert any("sections must be a list" in r for r in reasons)

    def test_section_not_dict(self):
        reasons = validate_context_budget({"sections": ["system"]})
        assert any("must be a dict" in r for r in reasons)

    def test_section_missing_name(self):
        reasons = validate_context_budget({"sections": [{"content": "stuff"}]})
        assert any("missing required field 'name'" in r for r in reasons)

    def test_valid_token_budget(self):
        reasons = validate_context_budget({"tokenBudget": 4096})
        assert reasons == []

    def test_zero_token_budget(self):
        reasons = validate_context_budget({"tokenBudget": 0})
        assert any("tokenBudget must be" in r for r in reasons)

    def test_negative_token_budget(self):
        reasons = validate_context_budget({"tokenBudget": -100})
        assert any("tokenBudget must be" in r for r in reasons)

    def test_no_fields_passes(self):
        assert validate_context_budget({}) == []


# ---------------------------------------------------------------------------
# Memory Source Validator
# ---------------------------------------------------------------------------


class TestMemorySourceValidator:
    @pytest.mark.parametrize("action", ["recall", "extract", "invalidate", "update"])
    def test_valid_actions(self, action: str):
        assert validate_memory_source({"action": action}) == []

    def test_invalid_action(self):
        reasons = validate_memory_source({"action": "delete_all"})
        assert any("Invalid memory action" in r for r in reasons)

    def test_valid_source_ids(self):
        assert validate_memory_source({
            "action": "recall",
            "sourceIds": ["mem_1", "mem_2"],
        }) == []

    def test_source_ids_not_list(self):
        reasons = validate_memory_source({"action": "recall", "sourceIds": "mem_1"})
        assert any("sourceIds must be a list" in r for r in reasons)

    def test_source_id_not_string(self):
        reasons = validate_memory_source({"action": "recall", "sourceIds": [42]})
        assert any("must be a non-empty string" in r for r in reasons)

    def test_source_id_empty(self):
        reasons = validate_memory_source({"action": "recall", "sourceIds": [""]})
        assert any("must be a non-empty string" in r for r in reasons)

    def test_no_action_passes(self):
        assert validate_memory_source({}) == []


# ---------------------------------------------------------------------------
# Retry Budget Validator
# ---------------------------------------------------------------------------


class TestRetryBudgetValidator:
    def test_valid_retries(self):
        assert validate_retry_budget({"maxRetries": 3}) == []

    def test_zero_retries(self):
        assert validate_retry_budget({"maxRetries": 0}) == []

    def test_negative_retries(self):
        reasons = validate_retry_budget({"maxRetries": -1})
        assert any("maxRetries must be" in r for r in reasons)

    def test_excessive_retries(self):
        reasons = validate_retry_budget({"maxRetries": 11})
        assert any("must not exceed 10" in r for r in reasons)

    def test_valid_delay(self):
        assert validate_retry_budget({"retryDelayMs": 1000}) == []

    def test_negative_delay(self):
        reasons = validate_retry_budget({"retryDelayMs": -1})
        assert any("retryDelayMs must be" in r for r in reasons)

    @pytest.mark.parametrize("strategy", ["retry", "fallback", "skip", "abort", "ask_user"])
    def test_valid_strategies(self, strategy: str):
        assert validate_retry_budget({"strategy": strategy}) == []

    def test_invalid_strategy(self):
        reasons = validate_retry_budget({"strategy": "ignore"})
        assert any("Invalid recovery strategy" in r for r in reasons)

    def test_no_fields_passes(self):
        assert validate_retry_budget({}) == []


# ---------------------------------------------------------------------------
# Frontend Visibility Validator
# ---------------------------------------------------------------------------


class TestFrontendVisibilityValidator:
    @pytest.mark.parametrize("visibility", ["chat", "panel", "trace"])
    def test_valid_visibility(self, visibility: str):
        assert validate_frontend_visibility({"visibility": visibility}) == []

    def test_invalid_visibility(self):
        reasons = validate_frontend_visibility({"visibility": "hidden"})
        assert any("Invalid visibility" in r for r in reasons)

    def test_valid_grouping(self):
        reasons = validate_frontend_visibility({
            "grouping": [
                {"label": "Explorer phase", "eventRange": [0, 10]},
                {"label": "Worker phase", "eventRange": [11, 30]},
            ],
        })
        assert reasons == []

    def test_grouping_not_list(self):
        reasons = validate_frontend_visibility({"grouping": "phase 1"})
        assert any("grouping must be a list" in r for r in reasons)

    def test_grouping_item_not_dict(self):
        reasons = validate_frontend_visibility({"grouping": ["phase 1"]})
        assert any("must be a dict" in r for r in reasons)

    def test_grouping_missing_label(self):
        reasons = validate_frontend_visibility({"grouping": [{"range": [0, 10]}]})
        assert any("missing required field 'label'" in r for r in reasons)

    def test_no_fields_passes(self):
        assert validate_frontend_visibility({}) == []


# ---------------------------------------------------------------------------
# Roadmap Edit Validator
# ---------------------------------------------------------------------------


class TestRoadmapEditValidator:
    def test_valid_update(self):
        reasons = validate_roadmap_edit({
            "updates": [{"action": "check", "target": "P3 validators"}],
            "requiresApproval": True,
        })
        assert reasons == []

    @pytest.mark.parametrize("action", ["check", "add", "remove", "reorder", "update_status"])
    def test_valid_actions(self, action: str):
        reasons = validate_roadmap_edit({
            "updates": [{"action": action}],
            "requiresApproval": True,
        })
        assert reasons == []

    def test_updates_not_list(self):
        reasons = validate_roadmap_edit({"updates": "check P3"})
        assert any("updates must be a list" in r for r in reasons)

    def test_empty_updates(self):
        reasons = validate_roadmap_edit({"updates": []})
        assert any("updates must be non-empty" in r for r in reasons)

    def test_update_not_dict(self):
        reasons = validate_roadmap_edit({"updates": ["not a dict"], "requiresApproval": True})
        assert any("must be a dict" in r for r in reasons)

    def test_invalid_action(self):
        reasons = validate_roadmap_edit({
            "updates": [{"action": "nuke"}],
            "requiresApproval": True,
        })
        assert any("invalid action" in r for r in reasons)

    def test_no_approval_rejected(self):
        reasons = validate_roadmap_edit({
            "updates": [{"action": "check"}],
            "requiresApproval": False,
        })
        assert any("must require approval" in r for r in reasons)

    def test_default_requires_approval(self):
        reasons = validate_roadmap_edit({
            "updates": [{"action": "check"}],
        })
        assert reasons == []


# ---------------------------------------------------------------------------
# P9: Write Safety — Patch Scope Validator
# ---------------------------------------------------------------------------


class TestPatchScopeValidator:
    def test_patch_in_scope(self):
        reasons = validate_patch_in_scope(
            {"targetPath": "src/ui/Button.tsx"},
            allowed_scopes=["src/ui/"],
        )
        assert reasons == []

    def test_patch_exactly_matches_scope(self):
        reasons = validate_patch_in_scope(
            {"targetPath": "src/ui"},
            allowed_scopes=["src/ui"],
        )
        assert reasons == []

    def test_patch_out_of_scope(self):
        reasons = validate_patch_in_scope(
            {"targetPath": "src/core/engine.py"},
            allowed_scopes=["src/ui/"],
        )
        assert any("outside allowed write scopes" in r for r in reasons)

    def test_patch_no_scope(self):
        reasons = validate_patch_in_scope({"targetPath": "src/main.py"})
        assert any("no allowed write scope" in r for r in reasons)

    def test_patch_no_target(self):
        reasons = validate_patch_in_scope({}, allowed_scopes=["src/"])
        assert reasons == []

    def test_path_variant(self):
        reasons = validate_patch_in_scope(
            {"path": "src/ui/modal.tsx"},
            allowed_scopes=["src/ui/"],
        )
        assert reasons == []

    def test_multiple_scopes_one_matches(self):
        reasons = validate_patch_in_scope(
            {"targetPath": "tests/test_ui.py"},
            allowed_scopes=["src/ui/", "tests/"],
        )
        assert reasons == []


# ---------------------------------------------------------------------------
# P9: Write Safety — Patch Conflict Detection
# ---------------------------------------------------------------------------


class TestPatchConflictDetection:
    def test_no_conflicts(self):
        patches = [
            {"targetPath": "src/ui/Button.tsx", "producerTaskId": "worker_1"},
            {"targetPath": "src/core/engine.py", "producerTaskId": "worker_2"},
        ]
        assert detect_patch_conflicts(patches) == []

    def test_conflicting_targets(self):
        patches = [
            {"targetPath": "src/ui/Button.tsx", "producerTaskId": "worker_1"},
            {"targetPath": "src/ui/Button.tsx", "producerTaskId": "worker_2"},
        ]
        reasons = detect_patch_conflicts(patches)
        assert len(reasons) == 1
        assert "Conflicting patch target" in reasons[0]

    def test_empty_patches(self):
        assert detect_patch_conflicts([]) == []

    def test_non_list_patches(self):
        assert detect_patch_conflicts("not a list") == []

    def test_non_dict_patch_entry(self):
        patches = [
            {"targetPath": "src/a.tsx", "producerTaskId": "w1"},
            "not a dict",
        ]
        assert detect_patch_conflicts(patches) == []

    def test_patch_missing_target(self):
        patches = [
            {"producerTaskId": "w1"},
            {"targetPath": "src/b.tsx", "producerTaskId": "w2"},
        ]
        assert detect_patch_conflicts(patches) == []

    def test_three_way_conflict(self):
        patches = [
            {"targetPath": "src/main.py", "producerTaskId": "w1"},
            {"targetPath": "src/main.py", "producerTaskId": "w2"},
            {"targetPath": "src/main.py", "producerTaskId": "w3"},
        ]
        reasons = detect_patch_conflicts(patches)
        assert len(reasons) == 2


# ---------------------------------------------------------------------------
# P9: Write Safety — Reviewer Gate
# ---------------------------------------------------------------------------


class TestReviewerGateValidator:
    def test_approved_merge(self):
        reasons = validate_reviewer_gate({
            "reviewStatus": "approved",
            "mergeRequested": True,
        })
        assert reasons == []

    def test_pending_no_merge(self):
        reasons = validate_reviewer_gate({
            "reviewStatus": "pending",
        })
        assert reasons == []

    @pytest.mark.parametrize("status", ["rejected", "changes_requested"])
    def test_rejected_merge_blocked(self, status: str):
        reasons = validate_reviewer_gate({
            "reviewStatus": status,
            "mergeRequested": True,
        })
        assert any("Cannot merge" in r for r in reasons)

    def test_rejected_no_merge(self):
        reasons = validate_reviewer_gate({
            "reviewStatus": "rejected",
            "mergeRequested": False,
        })
        assert reasons == []

    def test_invalid_review_status(self):
        reasons = validate_reviewer_gate({"reviewStatus": "maybe"})
        assert any("Invalid reviewStatus" in r for r in reasons)

    def test_no_review_status(self):
        assert validate_reviewer_gate({}) == []

    def test_all_valid_statuses(self):
        for status in ["pending", "approved", "rejected", "changes_requested"]:
            assert validate_reviewer_gate({"reviewStatus": status}) == []


# ---------------------------------------------------------------------------
# P9: Write Scope Overlap
# ---------------------------------------------------------------------------


class TestWriteScopeOverlap:
    def test_no_overlap(self):
        subtasks = [
            {"id": "t1", "ownedScope": "src/ui/"},
            {"id": "t2", "ownedScope": "src/core/"},
        ]
        assert validate_write_scope_overlap(subtasks) == []

    def test_overlap_detected(self):
        subtasks = [
            {"id": "t1", "ownedScope": "src/ui/"},
            {"id": "t2", "ownedScope": "src/ui/"},
        ]
        reasons = validate_write_scope_overlap(subtasks)
        assert any("Overlapping write scope" in r for r in reasons)

    def test_write_scope_variant(self):
        subtasks = [
            {"id": "t1", "writeScope": "src/"},
            {"id": "t2", "writeScope": "src/"},
        ]
        reasons = validate_write_scope_overlap(subtasks)
        assert any("Overlapping write scope" in r for r in reasons)


# ---------------------------------------------------------------------------
# Composite Validator Wiring
# ---------------------------------------------------------------------------


class TestCompositeWiring:
    def test_intent_mode_wired(self):
        reasons = validate_proposal("intent_mode", {"mode": "telepathy"})
        assert any("Invalid mode" in r for r in reasons)

    def test_model_policy_wired(self):
        reasons = validate_proposal("model_policy", {"model": "unknown-model", "provider": "unknown"})
        assert any("Unknown model" in r for r in reasons)
        assert any("Unknown provider" in r for r in reasons)

    def test_mcp_policy_wired(self):
        reasons = validate_proposal("mcp_policy", {"serverId": "x", "tools": "not_list"})
        assert any("tools must be a list" in r for r in reasons)

    def test_context_policy_wired(self):
        reasons = validate_proposal("context_policy", {
            "sections": [{"name": "system"}],
            "tokenBudget": -1,
        })
        assert any("Required context section missing" in r for r in reasons)
        assert any("tokenBudget must be" in r for r in reasons)

    def test_memory_policy_wired(self):
        reasons = validate_proposal("memory_policy", {"action": "nuke", "sourceIds": "x"})
        assert any("Invalid memory action" in r for r in reasons)

    def test_failure_recovery_wired(self):
        reasons = validate_proposal("failure_recovery", {"maxRetries": -1, "strategy": "ignore"})
        assert any("maxRetries must be" in r for r in reasons)
        assert any("Invalid recovery strategy" in r for r in reasons)

    def test_event_presentation_wired(self):
        reasons = validate_proposal("event_presentation", {
            "grouping": "bad",
            "visibility": "hidden",
        })
        assert any("grouping must be a list" in r for r in reasons)
        assert any("Invalid visibility" in r for r in reasons)

    def test_todo_maintenance_wired(self):
        reasons = validate_proposal("todo_maintenance", {
            "updates": [{"action": "nuke"}],
            "requiresApproval": False,
        })
        assert any("must require approval" in r for r in reasons)


# ---------------------------------------------------------------------------
# Extended Generation Report
# ---------------------------------------------------------------------------


@pytest.fixture
def store():
    return SQLiteStore(Path(tempfile.mkdtemp()) / "test.db")


def _create_parent_task(store: SQLiteStore) -> dict:
    return store.create_task(session_id="s1", task_type="agent", goal="build player", plan=[])


def _create_collab_child(store: SQLiteStore, parent_task_id: str, **kwargs) -> dict:
    params = {
        "title": kwargs.get("title", "child task"),
        "parentTaskId": parent_task_id,
        "sessionId": "s1",
        "priority": kwargs.get("priority", 3),
    }
    if "dependencies" in kwargs:
        params["dependencies"] = kwargs["dependencies"]
    return store.create_collaboration_task(params)


class TestExtendedGenerationReport:
    def test_report_includes_execution_mode(self, store: SQLiteStore):
        parent = _create_parent_task(store)
        child = _create_collab_child(store, parent["id"])
        report = build_generation_report(store, parent_task_id=parent["id"])
        assert report["childTasks"][0]["executionMode"] == "default"
        assert report["childTasks"][0]["attemptCount"] == 1

    def test_report_includes_structured_error_fields(self, store: SQLiteStore):
        parent = _create_parent_task(store)
        child = _create_collab_child(store, parent["id"])
        worker = store.upsert_agent_worker({"name": "w1", "role": "worker"})
        store.claim_collaboration_task({
            "taskId": child["task"]["id"], "workerId": worker["worker"]["id"],
        })
        store.fail_collaboration_task({
            "taskId": child["task"]["id"],
            "workerId": worker["worker"]["id"],
            "error": {"code": "TIMEOUT", "message": "exceeded 30s", "retryable": True},
        })
        report = build_generation_report(store, parent_task_id=parent["id"])
        ct = report["childTasks"][0]
        assert ct["status"] == "failed"
        assert ct["errorCode"] == "TIMEOUT"
        assert ct["errorMessage"] == "exceeded 30s"
        assert ct["retryable"] is True

    def test_report_error_non_dict(self, store: SQLiteStore):
        parent = _create_parent_task(store)
        child = _create_collab_child(store, parent["id"])
        worker = store.upsert_agent_worker({"name": "w1", "role": "worker"})
        store.claim_collaboration_task({
            "taskId": child["task"]["id"], "workerId": worker["worker"]["id"],
        })
        store.fail_collaboration_task({
            "taskId": child["task"]["id"],
            "workerId": worker["worker"]["id"],
            "error": "string error",
        })
        report = build_generation_report(store, parent_task_id=parent["id"])
        ct = report["childTasks"][0]
        assert ct["errorCode"] is None
        assert ct["errorMessage"] == "string error"
        assert ct["retryable"] is False

    def test_report_includes_trace_event_counts(self, store: SQLiteStore):
        parent = _create_parent_task(store)
        # Use parent task id directly since append_trace_event requires tasks table entry
        # and collaboration tasks are in a separate table
        for etype in ["tool_call", "tool_call", "message.delta"]:
            store.append_trace_event(
                task_id=parent["id"],
                event_type=etype,
                source="test",
                payload={},
                session_id="s1",
                visibility="trace",
            )
        # Verify trace events are queryable for the parent task
        trace_result = store.list_trace_events({"taskId": parent["id"], "limit": 100})
        assert len(trace_result["traceEvents"]) == 3
        # Verify report can be built — child tasks won't have trace counts
        # since they're in the collaboration_tasks table
        report = build_generation_report(store, parent_task_id=parent["id"])
        assert report["parentTaskId"] == parent["id"]

    def test_report_includes_message_ids(self, store: SQLiteStore):
        parent = _create_parent_task(store)
        child = _create_collab_child(store, parent["id"])
        child_id = child["task"]["id"]
        # Create messages for the child task
        store.create_message(
            session_id="s1", task_id=child_id,
            role="user", content="hello",
        )
        store.create_message(
            session_id="s1", task_id=child_id,
            role="assistant", content="world",
        )
        report = build_generation_report(store, parent_task_id=parent["id"])
        ct = report["childTasks"][0]
        assert "messageIds" in ct
        assert len(ct["messageIds"]) == 2

    def test_report_includes_artifact_ids(self, store: SQLiteStore):
        parent = _create_parent_task(store)
        child = _create_collab_child(store, parent["id"])
        child_id = child["task"]["id"]
        store.create_artifact({
            "sessionId": "s1",
            "parentTaskId": parent["id"],
            "producerTaskId": child_id,
            "kind": "file",
            "title": "component",
        })
        store.create_artifact({
            "sessionId": "s1",
            "parentTaskId": parent["id"],
            "producerTaskId": "other_task",
            "kind": "plan",
        })
        report = build_generation_report(store, parent_task_id=parent["id"])
        ct = report["childTasks"][0]
        assert "artifactIds" in ct
        assert len(ct["artifactIds"]) == 1  # only the one from this child
        # Total report still shows 2 artifacts
        assert report["counts"]["artifacts"] == 2

    def test_report_completed_child_no_error_fields(self, store: SQLiteStore):
        parent = _create_parent_task(store)
        child = _create_collab_child(store, parent["id"])
        worker = store.upsert_agent_worker({"name": "w1", "role": "worker"})
        store.claim_collaboration_task({
            "taskId": child["task"]["id"], "workerId": worker["worker"]["id"],
        })
        store.complete_collaboration_task({
            "taskId": child["task"]["id"], "workerId": worker["worker"]["id"],
            "result": {"ok": True},
        })
        report = build_generation_report(store, parent_task_id=parent["id"])
        ct = report["childTasks"][0]
        assert ct["status"] == "completed"
        assert "errorCode" not in ct
        assert "errorMessage" not in ct
        assert "retryable" not in ct
