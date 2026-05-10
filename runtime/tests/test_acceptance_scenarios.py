"""P11/P12 Acceptance Scenarios: End-to-end validation of runtime paths.

P12: Tests runtime behavior when LLM proposals violate policy constraints.
     These simulate the full proposal -> validate -> reject/accept flow.

P11: Tests subagent generation acceptance scenarios:
     - Multi-agent generation report
     - Explorer/worker/reviewer artifact flows
     - Failed child visibility
     - Event visibility routing
     - Event persistence for refresh recovery
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import pytest

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
from local_agent_runtime.services.generation_report import build_generation_report
from local_agent_runtime.store.sqlite_store import SQLiteStore


def _make_store() -> SQLiteStore:
    return SQLiteStore(Path(tempfile.mkdtemp()) / "acceptance.db")


def _setup_multi_agent_scenario(store: SQLiteStore) -> dict[str, Any]:
    """Set up a "Build a UI music player" scenario with 3 child agents."""
    workspace = store.upsert_workspace("/fake/workspace")
    session = store.create_session(workspace_id=workspace["id"], title="music-player")
    parent_task = store.create_task(
        session_id=session["id"],
        task_type="agent",
        goal="Build a UI music player with audio engine, player UI, and playlist state management",
        plan=[],
    )

    explorer = store.create_collaboration_task({
        "title": "Audio Engine Agent",
        "parentTaskId": parent_task["id"],
        "sessionId": session["id"],
        "priority": 1,
        "metadata": {
            "agentType": "explorer",
            "profile": {"name": "Audio Engine Agent", "baseType": "explorer"},
        },
    })

    worker = store.create_collaboration_task({
        "title": "Player UI Agent",
        "parentTaskId": parent_task["id"],
        "sessionId": session["id"],
        "priority": 2,
        "dependencies": [explorer["task"]["id"]],
        "metadata": {
            "agentType": "worker",
            "profile": {"name": "Player UI Agent", "baseType": "worker"},
        },
    })

    reviewer = store.create_collaboration_task({
        "title": "Playlist State Agent",
        "parentTaskId": parent_task["id"],
        "sessionId": session["id"],
        "priority": 3,
        "dependencies": [worker["task"]["id"]],
        "metadata": {
            "agentType": "worker",
            "profile": {"name": "Playlist State Agent", "baseType": "worker"},
        },
    })

    w1 = store.upsert_agent_worker({"name": "explorer-1", "role": "explorer"})["worker"]
    w2 = store.upsert_agent_worker({"name": "worker-1", "role": "worker"})["worker"]
    w3 = store.upsert_agent_worker({"name": "worker-2", "role": "worker"})["worker"]

    return {
        "store": store,
        "session": session,
        "parent_task": parent_task,
        "explorer": explorer,
        "worker": worker,
        "reviewer": reviewer,
        "workers": [w1, w2, w3],
    }


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


# ── P11 Acceptance Scenarios ─────────────────────────────────────────────


class TestMusicPlayerGenerationReport:
    """P11: 'Build a UI music player' creates a parent generation report."""

    def test_full_report_structure(self):
        store = _make_store()
        data = _setup_multi_agent_scenario(store)

        report = build_generation_report(
            store,
            parent_task_id=data["parent_task"]["id"],
            session_id=data["session"]["id"],
        )

        assert report["parentTaskId"] == data["parent_task"]["id"]
        assert report["sessionId"] == data["session"]["id"]
        assert report["planningMode"] in {"llm", "rule_fallback", "manual"}

        child_tasks = report["childTasks"]
        assert len(child_tasks) == 3

        # Verify explorer
        explorer_entry = child_tasks[0]
        assert explorer_entry["profileName"] == "Audio Engine Agent"
        assert explorer_entry["profileBaseType"] == "explorer"

        # Verify worker
        worker_entry = child_tasks[1]
        assert worker_entry["profileName"] == "Player UI Agent"
        assert worker_entry["profileBaseType"] == "worker"
        assert worker_entry["dependencies"] == [data["explorer"]["task"]["id"]]

        # Verify reviewer
        reviewer_entry = child_tasks[2]
        assert reviewer_entry["profileName"] == "Playlist State Agent"
        assert reviewer_entry["profileBaseType"] == "worker"

    def test_report_includes_child_task_ids(self):
        store = _make_store()
        data = _setup_multi_agent_scenario(store)

        report = build_generation_report(
            store,
            parent_task_id=data["parent_task"]["id"],
            session_id=data["session"]["id"],
        )

        reported_ids = {c["taskId"] for c in report["childTasks"]}
        expected_ids = {
            data["explorer"]["task"]["id"],
            data["worker"]["task"]["id"],
            data["reviewer"]["task"]["id"],
        }
        assert reported_ids == expected_ids


class TestExplorerScopeArtifact:
    """P11: Explorer child produces a scope/plan artifact."""

    def test_explorer_creates_plan_artifact(self):
        store = _make_store()
        data = _setup_multi_agent_scenario(store)
        explorer_task_id = data["explorer"]["task"]["id"]
        session_id = data["session"]["id"]
        parent_task_id = data["parent_task"]["id"]

        artifact = store.create_artifact({
            "sessionId": session_id,
            "parentTaskId": parent_task_id,
            "producerTaskId": explorer_task_id,
            "kind": "plan",
            "title": "Audio Engine Exploration",
            "description": "Explored existing audio code structure",
            "content": {
                "filesScanned": ["src/audio/engine.ts", "src/audio/player.ts"],
                "findings": "Existing audio engine supports basic playback",
            },
        })

        assert artifact["artifact"]["kind"] == "plan"
        assert artifact["artifact"]["status"] == "proposed"
        assert artifact["artifact"]["producerTaskId"] == explorer_task_id

        # Artifact appears in generation report
        report = build_generation_report(
            store,
            parent_task_id=parent_task_id,
            session_id=session_id,
        )
        assert any(
            a["kind"] == "plan" and a["producerTaskId"] == explorer_task_id
            for a in report["artifacts"]
        )


class TestWorkerPatchArtifacts:
    """P11: Worker child proposes file or patch artifacts."""

    def test_worker_creates_patch_artifact(self):
        store = _make_store()
        data = _setup_multi_agent_scenario(store)
        worker_task_id = data["worker"]["task"]["id"]
        session_id = data["session"]["id"]
        parent_task_id = data["parent_task"]["id"]

        patch_artifact = store.create_artifact({
            "sessionId": session_id,
            "parentTaskId": parent_task_id,
            "producerTaskId": worker_task_id,
            "kind": "patch",
            "title": "Player UI component",
            "content": {
                "filePath": "src/ui/player/Player.tsx",
                "patch": "--- a/src/ui/player/Player.tsx\n+++ b/src/ui/player/Player.tsx\n",
            },
        })

        assert patch_artifact["artifact"]["kind"] == "patch"
        assert patch_artifact["artifact"]["producerTaskId"] == worker_task_id

    def test_worker_creates_file_artifact(self):
        store = _make_store()
        data = _setup_multi_agent_scenario(store)
        worker_task_id = data["reviewer"]["task"]["id"]
        session_id = data["session"]["id"]
        parent_task_id = data["parent_task"]["id"]

        file_artifact = store.create_artifact({
            "sessionId": session_id,
            "parentTaskId": parent_task_id,
            "producerTaskId": worker_task_id,
            "kind": "file",
            "title": "Playlist state manager",
            "content": {
                "filePath": "src/ui/player/PlaylistState.ts",
            },
        })

        assert file_artifact["artifact"]["kind"] == "file"

    def test_artifacts_linked_to_worker_in_report(self):
        store = _make_store()
        data = _setup_multi_agent_scenario(store)
        worker_task_id = data["worker"]["task"]["id"]
        session_id = data["session"]["id"]
        parent_task_id = data["parent_task"]["id"]

        store.create_artifact({
            "sessionId": session_id,
            "parentTaskId": parent_task_id,
            "producerTaskId": worker_task_id,
            "kind": "patch",
            "title": "UI patch",
        })
        store.create_artifact({
            "sessionId": session_id,
            "parentTaskId": parent_task_id,
            "producerTaskId": worker_task_id,
            "kind": "file",
            "title": "UI file",
        })

        report = build_generation_report(
            store,
            parent_task_id=parent_task_id,
            session_id=session_id,
        )

        worker_entry = next(c for c in report["childTasks"] if c["taskId"] == worker_task_id)
        assert len(worker_entry["artifactIds"]) == 2


class TestReviewerAcceptsRejectsArtifacts:
    """P11: Reviewer child accepts or rejects artifacts."""

    def test_reviewer_approves_artifact(self):
        store = _make_store()
        data = _setup_multi_agent_scenario(store)
        session_id = data["session"]["id"]
        parent_task_id = data["parent_task"]["id"]
        worker_task_id = data["worker"]["task"]["id"]
        reviewer_task_id = data["reviewer"]["task"]["id"]

        # Worker creates artifact
        artifact = store.create_artifact({
            "sessionId": session_id,
            "parentTaskId": parent_task_id,
            "producerTaskId": worker_task_id,
            "kind": "patch",
            "title": "Player UI patch",
        })
        artifact_id = artifact["artifact"]["id"]

        # Reviewer approves
        updated = store.update_artifact({
            "artifactId": artifact_id,
            "status": "verified",
        })
        assert updated["artifact"]["status"] == "verified"

    def test_reviewer_rejects_artifact(self):
        store = _make_store()
        data = _setup_multi_agent_scenario(store)
        session_id = data["session"]["id"]
        parent_task_id = data["parent_task"]["id"]
        worker_task_id = data["worker"]["task"]["id"]

        artifact = store.create_artifact({
            "sessionId": session_id,
            "parentTaskId": parent_task_id,
            "producerTaskId": worker_task_id,
            "kind": "patch",
            "title": "Bad patch",
        })
        artifact_id = artifact["artifact"]["id"]

        # Reviewer rejects
        updated = store.update_artifact({
            "artifactId": artifact_id,
            "status": "rejected",
        })
        assert updated["artifact"]["status"] == "rejected"

    def test_rejected_artifact_cannot_be_re_approved(self):
        store = _make_store()
        data = _setup_multi_agent_scenario(store)
        session_id = data["session"]["id"]
        parent_task_id = data["parent_task"]["id"]
        worker_task_id = data["worker"]["task"]["id"]

        artifact = store.create_artifact({
            "sessionId": session_id,
            "parentTaskId": parent_task_id,
            "producerTaskId": worker_task_id,
            "kind": "patch",
            "title": "Bad patch",
        })
        artifact_id = artifact["artifact"]["id"]

        store.update_artifact({"artifactId": artifact_id, "status": "rejected"})

        with pytest.raises(ValueError, match="Rejected artifacts cannot transition"):
            store.update_artifact({"artifactId": artifact_id, "status": "verified"})


class TestFailedChildVisibleInReport:
    """P11: Failed child task remains visible in report."""

    def test_failed_child_appears_in_report(self):
        store = _make_store()
        data = _setup_multi_agent_scenario(store)
        session_id = data["session"]["id"]
        parent_task_id = data["parent_task"]["id"]

        # Claim then fail explorer
        explorer_id = data["explorer"]["task"]["id"]
        store.claim_collaboration_task({"taskId": explorer_id, "workerId": data["workers"][0]["id"]})
        store.fail_collaboration_task({
            "taskId": explorer_id,
            "workerId": data["workers"][0]["id"],
            "error": {"code": "CHILD_TASK_TIMEOUT", "message": "Explorer timed out"},
        })

        report = build_generation_report(
            store,
            parent_task_id=parent_task_id,
            session_id=session_id,
        )

        explorer_entry = next(c for c in report["childTasks"] if c["taskId"] == explorer_id)
        assert explorer_entry["status"] == "failed"
        assert explorer_entry["error"]["code"] == "CHILD_TASK_TIMEOUT"

    def test_mixed_completed_failed_in_report(self):
        store = _make_store()
        data = _setup_multi_agent_scenario(store)
        session_id = data["session"]["id"]
        parent_task_id = data["parent_task"]["id"]

        explorer_id = data["explorer"]["task"]["id"]
        worker_id = data["worker"]["task"]["id"]
        reviewer_id = data["reviewer"]["task"]["id"]

        # Explorer: claim + complete
        store.claim_collaboration_task({"taskId": explorer_id, "workerId": data["workers"][0]["id"]})
        store.complete_collaboration_task({
            "taskId": explorer_id,
            "workerId": data["workers"][0]["id"],
            "result": {"status": "explored"},
        })
        # Worker: claim + fail
        store.claim_collaboration_task({"taskId": worker_id, "workerId": data["workers"][1]["id"]})
        store.fail_collaboration_task({
            "taskId": worker_id,
            "workerId": data["workers"][1]["id"],
            "error": {"code": "CHILD_TASK_FAILED", "message": "Patch conflict"},
        })
        # Reviewer: cancel
        store.update_collaboration_task({"taskId": reviewer_id, "status": "cancelled"})

        report = build_generation_report(
            store,
            parent_task_id=parent_task_id,
            session_id=session_id,
        )

        by_id = {c["taskId"]: c for c in report["childTasks"]}
        assert by_id[explorer_id]["status"] == "completed"
        assert by_id[worker_id]["status"] == "failed"
        assert by_id[reviewer_id]["status"] == "cancelled"


class TestEventVisibilityRouting:
    """P11: Event visibility routing - chat/panel/trace."""

    def test_root_streaming_is_chat_visibility(self):
        store = _make_store()
        data = _setup_multi_agent_scenario(store)

        from local_agent_runtime.orchestrator.service import Orchestrator
        task = {"id": data["parent_task"]["id"], "role": "root"}
        vis = Orchestrator._infer_event_visibility("assistant.token", task)
        assert vis == "chat"

    def test_root_message_delta_is_chat(self):
        from local_agent_runtime.orchestrator.service import Orchestrator
        task = {"id": "t-1", "role": "root"}
        vis = Orchestrator._infer_event_visibility("message.delta", task)
        assert vis == "chat"

    def test_child_streaming_is_trace_visibility(self):
        from local_agent_runtime.orchestrator.service import Orchestrator
        task = {"id": "t-child", "role": "worker"}
        vis = Orchestrator._infer_event_visibility("assistant.token", task)
        assert vis == "trace"

    def test_collab_events_are_panel_visibility(self):
        from local_agent_runtime.orchestrator.service import Orchestrator
        task = {"id": "t-1", "role": "root"}
        for event_type in ["collab.task.created", "collab.task.completed", "collab.message.sent"]:
            vis = Orchestrator._infer_event_visibility(event_type, task)
            assert vis == "panel", f"{event_type} should be panel, got {vis}"

    def test_tool_calls_are_trace_visibility(self):
        from local_agent_runtime.orchestrator.service import Orchestrator
        task = {"id": "t-1", "role": "root"}
        for event_type in ["tool.call.started", "tool.call.completed", "tool.call.failed"]:
            vis = Orchestrator._infer_event_visibility(event_type, task)
            assert vis == "trace"


class TestEventPersistenceForRefresh:
    """P11: Event persistence enables refresh recovery."""

    def test_events_after_recovers_missed_events(self):
        store = _make_store()
        data = _setup_multi_agent_scenario(store)
        session_id = data["session"]["id"]
        parent_task_id = data["parent_task"]["id"]

        # Simulate trace events
        store.append_trace_event(
            task_id=parent_task_id,
            event_type="task.started",
            source="planner",
            payload={"goal": "Build music player"},
            session_id=session_id,
            visibility="chat",
        )
        store.append_trace_event(
            task_id=parent_task_id,
            event_type="collab.task.created",
            source="dispatcher",
            payload={"childTaskId": data["explorer"]["task"]["id"]},
            session_id=session_id,
            visibility="panel",
        )
        store.append_trace_event(
            task_id=parent_task_id,
            event_type="tool.call.started",
            source="worker",
            payload={"tool": "read_file"},
            session_id=session_id,
            visibility="trace",
        )

        # Recovery: get events after sequence 0
        result = store.events_after(session_id, 0)
        assert len(result["events"]) == 3
        assert not result["truncated"]

    def test_events_after_with_sequence_offset(self):
        store = _make_store()
        data = _setup_multi_agent_scenario(store)
        session_id = data["session"]["id"]
        parent_task_id = data["parent_task"]["id"]

        evt1 = store.append_trace_event(
            task_id=parent_task_id,
            event_type="task.started",
            source="planner",
            payload={},
            session_id=session_id,
        )
        evt2 = store.append_trace_event(
            task_id=parent_task_id,
            event_type="task.completed",
            source="planner",
            payload={},
            session_id=session_id,
        )

        # Get events after first one
        seq1 = evt1["sequence"]
        result = store.events_after(session_id, seq1)
        assert len(result["events"]) == 1
        assert result["events"][0]["sequence"] == evt2["sequence"]

    def test_events_persisted_with_correct_visibility(self):
        store = _make_store()
        data = _setup_multi_agent_scenario(store)
        session_id = data["session"]["id"]
        parent_task_id = data["parent_task"]["id"]

        store.append_trace_event(
            task_id=parent_task_id,
            event_type="task.started",
            source="planner",
            payload={},
            session_id=session_id,
            visibility="chat",
        )
        store.append_trace_event(
            task_id=parent_task_id,
            event_type="collab.task.created",
            source="dispatcher",
            payload={},
            session_id=session_id,
            visibility="panel",
        )
        store.append_trace_event(
            task_id=parent_task_id,
            event_type="tool.call.started",
            source="worker",
            payload={},
            session_id=session_id,
            visibility="trace",
        )

        result = store.events_after(session_id, 0)
        events = result["events"]
        assert events[0]["visibility"] == "chat"
        assert events[1]["visibility"] == "panel"
        assert events[2]["visibility"] == "trace"
