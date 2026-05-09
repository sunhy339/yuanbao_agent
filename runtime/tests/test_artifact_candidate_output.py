"""P4: Child executor artifact candidate output and linking.

Tests:
- Executor output with `artifacts` list registers artifacts in the store
- Registered artifact IDs are attached to the result message payload
- Registered artifact IDs appear in the generation report
- Invalid artifact candidates are skipped gracefully
- No artifacts field means no artifacts registered
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from local_agent_runtime.event_bus import EventBus
from local_agent_runtime.services import CollaborationService, SubagentService
from local_agent_runtime.services.worker_runner import WorkerRunner
from local_agent_runtime.store.sqlite_store import SQLiteStore


def _setup(tmp_path: Path) -> tuple[
    SQLiteStore, EventBus, SubagentService, dict[str, Any]
]:
    store = SQLiteStore(str(tmp_path / "runtime.sqlite3"))
    event_bus = EventBus()
    event_bus.subscribe(store.append_runtime_event)
    collaboration = CollaborationService(store, event_bus)

    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(parents=True, exist_ok=True)
    workspace = store.upsert_workspace(str(workspace_root))
    session = store.create_session(workspace_id=workspace["id"], title="p4-test")
    parent_task = store.create_task(
        session_id=session["id"],
        task_type="chat",
        goal="Build a UI music player",
        plan=[],
    )

    return (
        store,
        event_bus,
        None,  # executor placeholder
        {"session": session, "parent_task": parent_task},
    )


def _subagent_with_executor(
    store: SQLiteStore,
    event_bus: EventBus,
    executor: Any,
) -> SubagentService:
    collaboration = CollaborationService(store, event_bus)
    runner = WorkerRunner(collaboration, executor=executor)
    return SubagentService(store, collaboration, runner=runner)


def test_executor_artifacts_registered_in_store(tmp_path: Path) -> None:
    """P4: Child executor returns artifact candidates, they are registered."""
    store, event_bus, _, records = _setup(tmp_path)
    session = records["session"]
    parent_task = records["parent_task"]

    def executor(context: Any) -> dict[str, Any]:
        return {
            "summary": "Found 3 source files",
            "executionMode": "mock",
            "artifacts": [
                {
                    "kind": "plan",
                    "title": "Exploration Plan",
                    "description": "Workspace exploration results",
                    "content": {"files": ["a.py", "b.py", "c.py"]},
                },
                {
                    "kind": "file",
                    "title": "Config Found",
                    "content": {"path": "config.json"},
                },
            ],
        }

    subagent = _subagent_with_executor(store, event_bus, executor)

    result = subagent.dispatch({
        "prompt": "Explore workspace.",
        "sessionId": session["id"],
        "taskId": parent_task["id"],
        "title": "Explorer",
        "agentType": "explorer",
    })

    child_id = result["task"]["id"]

    # Verify artifacts were created in the store
    artifacts_result = store.list_artifacts({
        "parentTaskId": parent_task["id"],
    })
    artifacts = artifacts_result["artifacts"]
    assert len(artifacts) == 2

    # Verify artifact fields
    kinds = {a["kind"] for a in artifacts}
    assert kinds == {"plan", "file"}

    # Verify producer task IDs
    for art in artifacts:
        assert art["producerTaskId"] == child_id

    # Verify status is "proposed" by default
    for art in artifacts:
        assert art["status"] == "proposed"

    store.close()


def test_artifact_ids_in_message_payload(tmp_path: Path) -> None:
    """P4: Artifact IDs are attached to the collab.message.sent payload."""
    store, event_bus, _, records = _setup(tmp_path)
    session = records["session"]
    parent_task = records["parent_task"]

    def executor(context: Any) -> dict[str, Any]:
        return {
            "summary": "Found files",
            "executionMode": "mock",
            "artifacts": [
                {"kind": "plan", "title": "Plan artifact"},
            ],
        }

    subagent = _subagent_with_executor(store, event_bus, executor)

    result = subagent.dispatch({
        "prompt": "Explore workspace.",
        "sessionId": session["id"],
        "taskId": parent_task["id"],
        "title": "Explorer",
        "agentType": "explorer",
    })

    message = result["message"]
    payload = message.get("payload", {})
    artifact_ids = payload.get("artifactIds", [])

    assert len(artifact_ids) == 1
    assert isinstance(artifact_ids[0], str)
    assert artifact_ids[0].startswith("art_")

    store.close()


def test_artifact_ids_in_generation_report(tmp_path: Path) -> None:
    """P4: Registered artifact IDs appear in the generation report."""
    from local_agent_runtime.services.generation_report import build_generation_report

    store, event_bus, _, records = _setup(tmp_path)
    session = records["session"]
    parent_task = records["parent_task"]

    def executor(context: Any) -> dict[str, Any]:
        return {
            "summary": "Found files",
            "executionMode": "mock",
            "artifacts": [
                {"kind": "plan", "title": "Plan A"},
                {"kind": "file", "title": "File B"},
            ],
        }

    subagent = _subagent_with_executor(store, event_bus, executor)

    result = subagent.dispatch({
        "prompt": "Explore workspace.",
        "sessionId": session["id"],
        "taskId": parent_task["id"],
        "title": "Explorer",
        "agentType": "explorer",
    })

    report = build_generation_report(store, parent_task_id=parent_task["id"])
    assert len(report["childTasks"]) == 1
    child_report = report["childTasks"][0]
    assert len(child_report["artifactIds"]) == 2
    assert report["counts"]["artifacts"] == 2

    store.close()


def test_invalid_artifact_candidates_skipped(tmp_path: Path) -> None:
    """P4: Invalid artifact candidates are skipped gracefully."""
    store, event_bus, _, records = _setup(tmp_path)
    session = records["session"]
    parent_task = records["parent_task"]

    def executor(context: Any) -> dict[str, Any]:
        return {
            "summary": "Mixed output",
            "executionMode": "mock",
            "artifacts": [
                {"kind": "plan", "title": "Valid artifact"},
                {"kind": "", "title": "Empty kind"},  # invalid: empty kind
                {"title": "No kind"},  # invalid: missing kind
                "not a dict",  # invalid: not a dict
                {"kind": "invalid_kind_xyz", "title": "Bad kind"},  # invalid: unknown kind
            ],
        }

    subagent = _subagent_with_executor(store, event_bus, executor)

    result = subagent.dispatch({
        "prompt": "Explore workspace.",
        "sessionId": session["id"],
        "taskId": parent_task["id"],
        "title": "Explorer",
        "agentType": "explorer",
    })

    # Only the valid artifact should be registered
    artifacts = store.list_artifacts({
        "parentTaskId": parent_task["id"],
    })["artifacts"]
    assert len(artifacts) == 1
    assert artifacts[0]["kind"] == "plan"

    store.close()


def test_no_artifacts_field_means_none_registered(tmp_path: Path) -> None:
    """P4: Executor without artifacts field creates no artifacts."""
    store, event_bus, _, records = _setup(tmp_path)
    session = records["session"]
    parent_task = records["parent_task"]

    def executor(context: Any) -> dict[str, Any]:
        return {
            "summary": "No artifacts here",
            "executionMode": "mock",
        }

    subagent = _subagent_with_executor(store, event_bus, executor)

    result = subagent.dispatch({
        "prompt": "Explore workspace.",
        "sessionId": session["id"],
        "taskId": parent_task["id"],
        "title": "Explorer",
        "agentType": "explorer",
    })

    artifacts = store.list_artifacts({
        "parentTaskId": parent_task["id"],
    })["artifacts"]
    assert len(artifacts) == 0

    # Message payload should not have artifactIds
    message = result["message"]
    payload = message.get("payload", {})
    assert "artifactIds" not in payload or len(payload.get("artifactIds", [])) == 0

    store.close()
