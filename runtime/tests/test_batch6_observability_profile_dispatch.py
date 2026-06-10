"""Batch 6 tests: P6 failure observability, P7 dynamic profile, model-first dispatch.

Covers:
- Retry trace events (child.retry.attempt)
- Timeout/cancellation error codes in collaboration tasks
- Attempt count persistence in task metadata
- Dynamic profile persistence on collab tasks
- Profile name in generation reports
- Legacy proposal/planning fields no longer control subagent dispatch
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from local_agent_runtime.event_bus import EventBus
from local_agent_runtime.services.collaboration_service import CollaborationService
from local_agent_runtime.services.generation_report import build_generation_report
from local_agent_runtime.services.subagent_service import SubagentService
from local_agent_runtime.services.worker_runner import (
    ChildTaskRequest,
    ChildTaskTimeoutError,
    WorkerRunner,
)
from local_agent_runtime.store.sqlite_store import SQLiteStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _runner_context(
    tmp_path: Path, executor: Any | None = None
) -> tuple[SQLiteStore, WorkerRunner, EventBus, dict[str, Any]]:
    store = SQLiteStore(str(tmp_path / "runtime.sqlite3"))
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(parents=True, exist_ok=True)
    workspace = store.upsert_workspace(str(workspace_root))
    session = store.create_session(workspace_id=workspace["id"], title="batch6")
    parent_task = store.create_task(session_id=session["id"], task_type="chat", goal="parent", plan=[])
    event_bus = EventBus()
    collaboration = CollaborationService(store, event_bus)
    return (
        store,
        WorkerRunner(collaboration, executor=executor),
        event_bus,
        {"session": session, "parent_task": parent_task},
    )


class RecordingRunner:
    def __init__(self) -> None:
        self.requests: list[ChildTaskRequest] = []

    def run_child_task(self, request: ChildTaskRequest) -> dict[str, Any]:
        self.requests.append(request)
        return {
            "status": "completed",
            "childTaskId": "ctask_child",
            "workerId": "agent_child",
            "result": {"summary": "done"},
            "subagent": {"agentType": request.agent_type, "executionMode": "inline-skeleton"},
            "task": {"id": "ctask_child"},
            "worker": {"id": "agent_child"},
            "message": {"id": "msg_child"},
            "summary": "done",
        }


# ===========================================================================
# P6: Failure Observability
# ===========================================================================


class TestRetryTraceEvents:
    def test_retry_emits_trace_event(self, tmp_path: Path) -> None:
        """When a retry occurs, a child.retry.attempt trace event is published."""
        attempts: list[int] = []

        def executor(context: Any) -> dict[str, Any]:
            attempts.append(context.attempt_number)
            if context.attempt_number == 1:
                raise TimeoutError("temporary timeout")
            return {"summary": "recovered", "executionMode": "test"}

        store, runner, event_bus, records = _runner_context(tmp_path, executor=executor)
        events: list[dict[str, Any]] = []
        event_bus.subscribe(lambda e: events.append(event_bus.as_payload(e)))
        try:
            response = runner.run_child_task(
                ChildTaskRequest(
                    prompt="retry-trace",
                    title="Retry Trace",
                    agent_type="coder",
                    session_id=records["session"]["id"],
                    parent_runtime_task_id=records["parent_task"]["id"],
                    retry={"maxAttempts": 2},
                )
            )
            assert response["status"] == "completed"

            retry_events = [
                e for e in events
                if e.get("type") == "child.retry.attempt"
            ]
            assert len(retry_events) >= 1
            evt = retry_events[0]
            assert evt["payload"]["attemptNumber"] == 1
            assert evt["payload"]["nextAttempt"] == 2
            assert "TimeoutError" in evt["payload"]["errorCode"]
        finally:
            store.close()

    def test_no_retry_trace_when_no_retry(self, tmp_path: Path) -> None:
        """When execution succeeds on first attempt, no retry trace event."""
        def executor(_context: Any) -> dict[str, Any]:
            return {"summary": "immediate success", "executionMode": "test"}

        store, runner, event_bus, records = _runner_context(tmp_path, executor=executor)
        events: list[dict[str, Any]] = []
        event_bus.subscribe(lambda e: events.append(event_bus.as_payload(e)))
        try:
            runner.run_child_task(
                ChildTaskRequest(
                    prompt="no-retry",
                    title="No Retry",
                    agent_type="coder",
                    session_id=records["session"]["id"],
                    parent_runtime_task_id=records["parent_task"]["id"],
                    retry={"maxAttempts": 2},
                )
            )
            retry_events = [
                e for e in events
                if e.get("type") == "child.retry.attempt"
            ]
            assert len(retry_events) == 0
        finally:
            store.close()


class TestErrorCodePersistence:
    def test_timeout_error_code_in_failed_task(self, tmp_path: Path) -> None:
        """Timeout produces CHILD_TASK_TIMEOUT error code in the collaboration task."""

        def executor(_context: Any) -> dict[str, Any]:
            raise ChildTaskTimeoutError(0.001)

        store, runner, _, records = _runner_context(tmp_path, executor=executor)
        try:
            response = runner.run_child_task(
                ChildTaskRequest(
                    prompt="timeout",
                    title="Timeout",
                    agent_type="coder",
                    session_id=records["session"]["id"],
                    parent_runtime_task_id=records["parent_task"]["id"],
                )
            )
            assert response["status"] == "failed"
            assert response["error"]["code"] == "CHILD_TASK_TIMEOUT"

            task = store.get_collaboration_task({"taskId": response["childTaskId"]})["task"]
            assert task["status"] == "failed"
            err = task.get("error")
            assert isinstance(err, dict)
            assert err.get("code") == "CHILD_TASK_TIMEOUT"
        finally:
            store.close()

    def test_cancellation_error_code(self, tmp_path: Path) -> None:
        """Pre-cancelled tasks produce CHILD_TASK_CANCELLED error code."""
        store, runner, _, records = _runner_context(tmp_path)
        try:
            response = runner.run_child_task(
                ChildTaskRequest(
                    prompt="cancel",
                    title="Cancel",
                    agent_type="coder",
                    session_id=records["session"]["id"],
                    parent_runtime_task_id=records["parent_task"]["id"],
                    cancellation={"cancelRequested": True},
                )
            )
            assert response["status"] == "failed"
            assert response["error"]["code"] == "CHILD_TASK_CANCELLED"
        finally:
            store.close()

    def test_execution_error_code(self, tmp_path: Path) -> None:
        """Generic executor error produces CHILD_TASK_EXECUTION_FAILED."""
        def executor(_context: Any) -> dict[str, Any]:
            raise RuntimeError("Something went wrong")

        store, runner, _, records = _runner_context(tmp_path, executor=executor)
        try:
            response = runner.run_child_task(
                ChildTaskRequest(
                    prompt="error",
                    title="Error",
                    agent_type="coder",
                    session_id=records["session"]["id"],
                    parent_runtime_task_id=records["parent_task"]["id"],
                )
            )
            assert response["status"] == "failed"
            assert response["error"]["code"] == "CHILD_TASK_EXECUTION_FAILED"
            assert "Something went wrong" in response["error"]["message"]
        finally:
            store.close()


class TestAttemptCountPersistence:
    def test_attempt_count_in_task_metadata(self, tmp_path: Path) -> None:
        """Retry attempt count is persisted in the collaboration task metadata."""
        attempts: list[int] = []

        def executor(context: Any) -> dict[str, Any]:
            attempts.append(context.attempt_number)
            if context.attempt_number == 1:
                raise TimeoutError("temporary")
            return {"summary": "recovered", "executionMode": "test"}

        store, runner, _, records = _runner_context(tmp_path, executor=executor)
        try:
            response = runner.run_child_task(
                ChildTaskRequest(
                    prompt="attempt-count",
                    title="Attempt Count",
                    agent_type="coder",
                    session_id=records["session"]["id"],
                    parent_runtime_task_id=records["parent_task"]["id"],
                    retry={"maxAttempts": 3},
                )
            )
            assert response["status"] == "completed"
            assert response["result"]["attempts"] == 2

            task = store.get_collaboration_task({"taskId": response["childTaskId"]})["task"]
            assert task["result"]["attempts"] == 2
        finally:
            store.close()


# ===========================================================================
# P7: Dynamic Profile Persistence
# ===========================================================================


class TestDynamicProfilePersistence:
    def test_profile_persisted_in_task_metadata(self, tmp_path: Path) -> None:
        """Dynamic profile metadata is stored in collaboration task metadata."""
        def executor(_ctx: Any) -> dict[str, Any]:
            return {"summary": "done", "executionMode": "test"}

        store, runner, _, records = _runner_context(tmp_path, executor=executor)
        profile = {
            "name": "Audio Engine Agent",
            "baseType": "coder",
            "mission": "Implement audio engine",
        }
        try:
            response = runner.run_child_task(
                ChildTaskRequest(
                    prompt="audio",
                    title="Audio Engine",
                    agent_type="coder",
                    session_id=records["session"]["id"],
                    parent_runtime_task_id=records["parent_task"]["id"],
                    profile=profile,
                )
            )
            task = store.get_collaboration_task({"taskId": response["childTaskId"]})["task"]
            meta = task.get("metadata", {})
            assert meta.get("profile") == profile
        finally:
            store.close()

    def test_no_profile_in_metadata_when_absent(self, tmp_path: Path) -> None:
        """When no profile is provided, metadata has no profile key."""
        def executor(_ctx: Any) -> dict[str, Any]:
            return {"summary": "done", "executionMode": "test"}

        store, runner, _, records = _runner_context(tmp_path, executor=executor)
        try:
            response = runner.run_child_task(
                ChildTaskRequest(
                    prompt="no-profile",
                    title="No Profile",
                    agent_type="explorer",
                    session_id=records["session"]["id"],
                    parent_runtime_task_id=records["parent_task"]["id"],
                )
            )
            task = store.get_collaboration_task({"taskId": response["childTaskId"]})["task"]
            meta = task.get("metadata", {})
            assert "profile" not in meta
        finally:
            store.close()


class TestProfileInReport:
    def test_report_shows_profile_name(self, tmp_path: Path) -> None:
        """Generation report includes dynamic profile name for child tasks."""
        store = SQLiteStore(str(tmp_path / "runtime.sqlite3"))
        workspace_root = tmp_path / "workspace"
        workspace_root.mkdir(parents=True, exist_ok=True)
        workspace = store.upsert_workspace(str(workspace_root))
        session = store.create_session(workspace_id=workspace["id"], title="report profile")
        parent_task = store.create_task(
            session_id=session["id"],
            task_type="chat",
            goal="parent",
            plan=[],
        )

        # Create a child task with profile metadata
        child = store.create_collaboration_task({
            "sessionId": session["id"],
            "parentTaskId": parent_task["id"],
            "title": "Audio Engine",
            "description": "Implement audio engine",
            "metadata": {
                "profile": {
                    "name": "Audio Engine Agent",
                    "baseType": "coder",
                    "mission": "Implement audio engine",
                },
            },
        })["task"]

        # Create worker, claim task, then complete
        worker = store.upsert_agent_worker({
            "workerId": "w1",
            "name": "Test Worker",
            "role": "agent",
        })["worker"]
        store.claim_collaboration_task({"taskId": child["id"], "workerId": worker["id"]})
        store.complete_collaboration_task({
            "taskId": child["id"],
            "workerId": worker["id"],
            "result": {"summary": "done"},
        })

        report = build_generation_report(store, parent_task_id=parent_task["id"])
        assert len(report["childTasks"]) == 1
        child_report = report["childTasks"][0]
        assert child_report["profileName"] == "Audio Engine Agent"
        assert child_report["profileBaseType"] == "coder"
        store.close()

    def test_report_no_profile_when_absent(self, tmp_path: Path) -> None:
        """Report omits profile fields when child has no profile metadata."""
        store = SQLiteStore(str(tmp_path / "runtime2.sqlite3"))
        workspace_root = tmp_path / "workspace2"
        workspace_root.mkdir(parents=True, exist_ok=True)
        workspace = store.upsert_workspace(str(workspace_root))
        session = store.create_session(workspace_id=workspace["id"], title="no profile")
        parent_task = store.create_task(
            session_id=session["id"],
            task_type="chat",
            goal="parent",
            plan=[],
        )
        child = store.create_collaboration_task({
            "sessionId": session["id"],
            "parentTaskId": parent_task["id"],
            "title": "Plain",
            "description": "No profile",
        })["task"]
        worker = store.upsert_agent_worker({
            "workerId": "w1",
            "name": "Test Worker",
            "role": "agent",
        })["worker"]
        store.claim_collaboration_task({"taskId": child["id"], "workerId": worker["id"]})
        store.complete_collaboration_task({
            "taskId": child["id"],
            "workerId": worker["id"],
            "result": {"summary": "done"},
        })

        report = build_generation_report(store, parent_task_id=parent_task["id"])
        assert len(report["childTasks"]) == 1
        child_report = report["childTasks"][0]
        assert "profileName" not in child_report
        assert "profileBaseType" not in child_report
        store.close()


# ===========================================================================
# Model-first subagent dispatch
# ===========================================================================


class TestModelFirstDispatch:
    def test_legacy_invalid_proposal_is_metadata_not_a_dispatch_gate(self) -> None:
        """Subagent dispatch follows the model tool call, not a proposal gate."""
        runner = RecordingRunner()
        service = SubagentService(object(), object(), runner=runner)
        result = service.dispatch({
            "prompt": "test",
            "proposal": {
                "kind": "tool_policy",
                "payload": {"allowedTools": ["task"]},
            },
        })
        assert result["status"] == "completed"
        assert len(runner.requests) == 1

    def test_legacy_unknown_proposal_is_not_authoritative(self) -> None:
        """Unknown proposal records are not part of the AgentTool execution path."""
        runner = RecordingRunner()
        service = SubagentService(object(), object(), runner=runner)
        result = service.dispatch({
            "prompt": "test",
            "proposal": {
                "kind": "not_a_real_kind",
                "payload": {},
            },
        })
        assert result["status"] == "completed"
        assert len(runner.requests) == 1

    def test_valid_legacy_proposal_is_ignored_as_control_flow(self) -> None:
        """A valid proposal may be persisted elsewhere, but dispatch does not depend on it."""
        runner = RecordingRunner()
        service = SubagentService(object(), object(), runner=runner)
        result = service.dispatch({
            "prompt": "test valid proposal",
            "proposal": {
                "kind": "tool_policy",
                "payload": {"allowedTools": ["read_file", "search_files"]},
            },
        })
        assert result["status"] == "completed"
        assert len(runner.requests) == 1

    def test_legacy_planning_mode_is_not_returned(self) -> None:
        """planningMode is no longer surfaced as subagent execution state."""
        runner = RecordingRunner()
        service = SubagentService(object(), object(), runner=runner)
        result = service.dispatch({
            "prompt": "test",
            "planningMode": "rule_fallback",
        })
        assert result["status"] == "completed"
        assert "planningMode" not in result

    def test_no_planning_mode_when_not_provided(self) -> None:
        """Result has no planningMode when dispatch has no planningMode param."""
        runner = RecordingRunner()
        service = SubagentService(object(), object(), runner=runner)
        result = service.dispatch({
            "prompt": "test",
        })
        assert "planningMode" not in result


class TestGenerationReportNoPlanningMode:
    def test_report_omits_planning_mode_even_when_parent_has_legacy_routing(self, tmp_path: Path) -> None:
        """Generation reports describe durable child work, not router/planner mode."""
        store = SQLiteStore(str(tmp_path / "runtime3.sqlite3"))
        workspace_root = tmp_path / "workspace3"
        workspace_root.mkdir(parents=True, exist_ok=True)
        workspace = store.upsert_workspace(str(workspace_root))
        session = store.create_session(workspace_id=workspace["id"], title="planning mode")
        parent_task = store.create_task(
            session_id=session["id"],
            task_type="chat",
            goal="parent",
            plan=[],
            routing={"planningMode": "llm"},
        )

        report = build_generation_report(store, parent_task_id=parent_task["id"])
        assert "planningMode" not in report
        store.close()

    def test_report_without_legacy_routing_omits_planning_mode(self, tmp_path: Path) -> None:
        """No implicit rule_fallback is created for reports."""
        store = SQLiteStore(str(tmp_path / "runtime4.sqlite3"))
        workspace_root = tmp_path / "workspace4"
        workspace_root.mkdir(parents=True, exist_ok=True)
        workspace = store.upsert_workspace(str(workspace_root))
        session = store.create_session(workspace_id=workspace["id"], title="default mode")
        parent_task = store.create_task(
            session_id=session["id"],
            task_type="chat",
            goal="parent",
            plan=[],
        )

        report = build_generation_report(store, parent_task_id=parent_task["id"])
        assert "planningMode" not in report
        store.close()


# ===========================================================================
# SubagentService profile forwarding
# ===========================================================================


class TestSubagentProfileForwarding:
    def test_dispatch_forwards_profile(self) -> None:
        """SubagentService.dispatch passes profile to ChildTaskRequest."""
        runner = RecordingRunner()
        service = SubagentService(object(), object(), runner=runner)
        profile = {"name": "Player UI Agent", "baseType": "explorer", "mission": "Build UI"}
        service.dispatch({
            "prompt": "build UI",
            "profile": profile,
        })
        assert len(runner.requests) == 1
        assert runner.requests[0].profile == profile

    def test_dispatch_no_profile(self) -> None:
        """SubagentService.dispatch creates request without profile when not provided."""
        runner = RecordingRunner()
        service = SubagentService(object(), object(), runner=runner)
        service.dispatch({
            "prompt": "explore",
        })
        assert len(runner.requests) == 1
        assert runner.requests[0].profile is None


# ===========================================================================
# Legacy planner controls are inert for AgentTool dispatch
# ===========================================================================


class TestLegacyPlannerControls:
    def test_missing_proposal_does_not_fallback_to_rule_fallback(self) -> None:
        """No backend fallback planner is injected before dispatch."""
        runner = RecordingRunner()
        service = SubagentService(object(), object(), runner=runner)
        result = service.dispatch({
            "prompt": "Explore workspace",
            "planningMode": "llm",
        })
        assert result["status"] == "completed"
        assert "planningMode" not in result

    def test_valid_proposal_does_not_mark_llm_planning_mode(self) -> None:
        """Proposal metadata does not become a child execution mode."""
        runner = RecordingRunner()
        service = SubagentService(object(), object(), runner=runner)
        result = service.dispatch({
            "prompt": "Explore workspace",
            "planningMode": "llm",
            "proposal": {
                "kind": "tool_policy",
                "payload": {"allowedTools": ["read_file"]},
            },
        })
        assert result["status"] == "completed"
        assert "planningMode" not in result

    def test_skip_decomposition_does_not_force_rule_fallback(self) -> None:
        """skipDecomposition is legacy metadata and no longer changes dispatch."""
        runner = RecordingRunner()
        service = SubagentService(object(), object(), runner=runner)
        result = service.dispatch({
            "prompt": "Simple question",
            "planningMode": "llm",
            "skipDecomposition": True,
        })
        assert result["status"] == "completed"
        assert "planningMode" not in result

    def test_skip_decomposition_without_planning_mode_is_ignored(self) -> None:
        """No implicit planning mode is synthesized from skipDecomposition."""
        runner = RecordingRunner()
        service = SubagentService(object(), object(), runner=runner)
        result = service.dispatch({
            "prompt": "Simple task",
            "skipDecomposition": True,
        })
        assert result["status"] == "completed"
        assert "planningMode" not in result

    def test_rule_fallback_mode_is_not_returned(self) -> None:
        """Explicit legacy fallback mode is not part of public child results."""
        runner = RecordingRunner()
        service = SubagentService(object(), object(), runner=runner)
        result = service.dispatch({
            "prompt": "Explore workspace",
            "planningMode": "rule_fallback",
        })
        assert result["status"] == "completed"
        assert "planningMode" not in result
