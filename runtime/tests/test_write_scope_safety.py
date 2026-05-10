"""P9 Multi-Agent Write Safety tests.

Covers:
- Write scope metadata persisted on collaboration tasks
- Patch target validated against declared write scope
- Out-of-scope patch rejection
- Patch conflict detection across tasks
- Write scope overlap detection before dispatch
- Reviewer gate validation
- Reviewer rejection blocking finalization
- Command scope checking
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from local_agent_runtime.event_bus import EventBus
from local_agent_runtime.policy.guard import PolicyGuard
from local_agent_runtime.services.collaboration_service import CollaborationService
from local_agent_runtime.services.write_scope_enforcement import WriteScopeEnforcer
from local_agent_runtime.services.worker_runner import ChildTaskRequest, WorkerRunner
from local_agent_runtime.store.sqlite_store import SQLiteStore
from local_agent_runtime.tools import build_builtin_tools


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _store_context(tmp_path: Path) -> tuple[SQLiteStore, dict[str, Any]]:
    store = SQLiteStore(str(tmp_path / "runtime.sqlite3"))
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(parents=True, exist_ok=True)
    workspace = store.upsert_workspace(str(workspace_root))
    session = store.create_session(workspace_id=workspace["id"], title="p9")
    parent_task = store.create_task(session_id=session["id"], task_type="chat", goal="parent", plan=[])
    return store, {"session": session, "parent_task": parent_task, "workspace_root": workspace_root}


def _create_child_with_scope(
    store: SQLiteStore,
    collab: CollaborationService,
    *,
    parent_task_id: str,
    session_id: str,
    title: str,
    write_scope: list[str] | None = None,
    profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {"agentType": "worker"}
    if write_scope is not None:
        metadata["writeScope"] = write_scope
    if profile is not None:
        metadata["profile"] = profile
    return collab.create_collaboration_task({
        "sessionId": session_id,
        "parentTaskId": parent_task_id,
        "title": title,
        "priority": 3,
        "metadata": metadata,
    })["task"]


def _create_runtime_task_for_child(
    store: SQLiteStore,
    *,
    session_id: str,
    child_task_id: str,
) -> dict[str, Any]:
    return store.create_task(
        session_id=session_id,
        task_type="subagent",
        goal="runtime child",
        plan=[],
        routing={"childCollaborationTaskId": child_task_id},
    )


class DummyExecutor:
    """Minimal executor that records context."""

    def __init__(self) -> None:
        self.calls: list[Any] = []

    def __call__(self, ctx: Any) -> Any:
        self.calls.append(ctx)
        return "done"


# ---------------------------------------------------------------------------
# P9.1: Write scope metadata on collaboration tasks
# ---------------------------------------------------------------------------


class TestWriteScopeMetadata:
    def test_write_scope_from_profile_owned_scope(self, tmp_path: Path) -> None:
        """Enforcer reads writeScope from profile.ownedScope when no top-level writeScope."""
        store, ctx = _store_context(tmp_path)
        event_bus = EventBus()
        collab = CollaborationService(store, event_bus)
        enforcer = WriteScopeEnforcer(store)
        child = _create_child_with_scope(
            store, collab,
            parent_task_id=ctx["parent_task"]["id"],
            session_id=ctx["session"]["id"],
            title="scoped worker",
            profile={"name": "UI Agent", "baseType": "worker", "mission": "build UI", "ownedScope": ["src/ui/"]},
        )
        # Enforcer should read ownedScope from profile when no top-level writeScope
        scope = enforcer.get_task_write_scope(child["id"])
        assert scope == ["src/ui/"]

    def test_write_scope_from_explicit_metadata(self, tmp_path: Path) -> None:
        store, ctx = _store_context(tmp_path)
        event_bus = EventBus()
        collab = CollaborationService(store, event_bus)
        child = _create_child_with_scope(
            store, collab,
            parent_task_id=ctx["parent_task"]["id"],
            session_id=ctx["session"]["id"],
            title="scoped worker",
            write_scope=["src/engine/", "src/audio/"],
        )
        task = store.require_collaboration_task(child["id"])
        meta = task.get("metadata", {})
        assert meta["writeScope"] == ["src/engine/", "src/audio/"]

    def test_worker_runner_persists_write_scope(self, tmp_path: Path) -> None:
        """WorkerRunner extracts ownedScope from profile and persists writeScope in task metadata."""
        store, ctx = _store_context(tmp_path)
        event_bus = EventBus()
        collab = CollaborationService(store, event_bus)
        executor = DummyExecutor()
        runner = WorkerRunner(collab, executor=executor)

        request = ChildTaskRequest(
            prompt="Build UI",
            title="UI Worker",
            agent_type="worker",
            session_id=ctx["session"]["id"],
            parent_runtime_task_id=ctx["parent_task"]["id"],
            profile={
                "name": "UI Agent",
                "baseType": "worker",
                "mission": "Build the UI layer",
                "ownedScope": ["src/ui/", "src/components/"],
                "allowedTools": ["read_file", "apply_patch"],
            },
        )
        result = runner.run_child_task(request)
        # The child task should have writeScope in metadata
        child_task = store.require_collaboration_task(result["childTaskId"])
        meta = child_task.get("metadata", {})
        assert meta.get("writeScope") == ["src/ui/", "src/components/"]


# ---------------------------------------------------------------------------
# P9.2 & P9.8: Patch in-scope / out-of-scope enforcement
# ---------------------------------------------------------------------------


class TestPatchScopeEnforcement:
    def test_patch_in_scope_allowed(self, tmp_path: Path) -> None:
        store, ctx = _store_context(tmp_path)
        event_bus = EventBus()
        collab = CollaborationService(store, event_bus)
        enforcer = WriteScopeEnforcer(store)

        child = _create_child_with_scope(
            store, collab,
            parent_task_id=ctx["parent_task"]["id"],
            session_id=ctx["session"]["id"],
            title="scoped worker",
            write_scope=["src/ui/"],
        )
        reasons = enforcer.check_patch_in_scope(child["id"], "src/ui/Button.tsx")
        assert reasons == []

    def test_patch_out_of_scope_rejected(self, tmp_path: Path) -> None:
        store, ctx = _store_context(tmp_path)
        event_bus = EventBus()
        collab = CollaborationService(store, event_bus)
        enforcer = WriteScopeEnforcer(store)

        child = _create_child_with_scope(
            store, collab,
            parent_task_id=ctx["parent_task"]["id"],
            session_id=ctx["session"]["id"],
            title="scoped worker",
            write_scope=["src/ui/"],
        )
        reasons = enforcer.check_patch_in_scope(child["id"], "src/engine/audio.py")
        assert len(reasons) == 1
        assert "outside" in reasons[0].lower() or "out of" in reasons[0].lower()

    def test_patch_no_scope_unrestricted(self, tmp_path: Path) -> None:
        store, ctx = _store_context(tmp_path)
        event_bus = EventBus()
        collab = CollaborationService(store, event_bus)
        enforcer = WriteScopeEnforcer(store)

        child = _create_child_with_scope(
            store, collab,
            parent_task_id=ctx["parent_task"]["id"],
            session_id=ctx["session"]["id"],
            title="unscoped worker",
        )
        # No scope declared — patch to any path should be allowed
        reasons = enforcer.check_patch_in_scope(child["id"], "src/any/path.py")
        assert reasons == []

    def test_patch_exact_scope_match(self, tmp_path: Path) -> None:
        store, ctx = _store_context(tmp_path)
        event_bus = EventBus()
        collab = CollaborationService(store, event_bus)
        enforcer = WriteScopeEnforcer(store)

        child = _create_child_with_scope(
            store, collab,
            parent_task_id=ctx["parent_task"]["id"],
            session_id=ctx["session"]["id"],
            title="scoped worker",
            write_scope=["src/ui/"],
        )
        # Exact match of scope prefix
        reasons = enforcer.check_patch_in_scope(child["id"], "src/ui/")
        assert reasons == []

    def test_patch_traversal_out_of_scope_rejected(self, tmp_path: Path) -> None:
        store, ctx = _store_context(tmp_path)
        event_bus = EventBus()
        collab = CollaborationService(store, event_bus)
        enforcer = WriteScopeEnforcer(store)

        child = _create_child_with_scope(
            store, collab,
            parent_task_id=ctx["parent_task"]["id"],
            session_id=ctx["session"]["id"],
            title="scoped worker",
            write_scope=["src/ui/"],
        )
        reasons = enforcer.check_patch_in_scope(child["id"], "src/ui/../engine/audio.py")
        assert len(reasons) == 1
        assert "outside" in reasons[0].lower()

    def test_runtime_task_id_resolves_child_write_scope(self, tmp_path: Path) -> None:
        store, ctx = _store_context(tmp_path)
        event_bus = EventBus()
        collab = CollaborationService(store, event_bus)
        enforcer = WriteScopeEnforcer(store)

        child = _create_child_with_scope(
            store, collab,
            parent_task_id=ctx["parent_task"]["id"],
            session_id=ctx["session"]["id"],
            title="scoped worker",
            write_scope=["src/ui/"],
        )
        runtime_task = _create_runtime_task_for_child(
            store,
            session_id=ctx["session"]["id"],
            child_task_id=child["id"],
        )

        assert enforcer.get_task_write_scope(runtime_task["id"]) == ["src/ui/"]
        reasons = enforcer.check_patch_in_scope(runtime_task["id"], "src/engine/audio.py")
        assert len(reasons) == 1


# ---------------------------------------------------------------------------
# P9.3: Command scope enforcement
# ---------------------------------------------------------------------------


class TestCommandScopeEnforcement:
    def test_command_in_scope(self, tmp_path: Path) -> None:
        store, ctx = _store_context(tmp_path)
        event_bus = EventBus()
        collab = CollaborationService(store, event_bus)
        enforcer = WriteScopeEnforcer(store)

        child = _create_child_with_scope(
            store, collab,
            parent_task_id=ctx["parent_task"]["id"],
            session_id=ctx["session"]["id"],
            title="scoped worker",
            write_scope=["src/ui/"],
        )
        reasons = enforcer.check_command_allowed(child["id"], command_scope="src/ui/build.sh")
        assert reasons == []

    def test_command_out_of_scope(self, tmp_path: Path) -> None:
        store, ctx = _store_context(tmp_path)
        event_bus = EventBus()
        collab = CollaborationService(store, event_bus)
        enforcer = WriteScopeEnforcer(store)

        child = _create_child_with_scope(
            store, collab,
            parent_task_id=ctx["parent_task"]["id"],
            session_id=ctx["session"]["id"],
            title="scoped worker",
            write_scope=["src/ui/"],
        )
        reasons = enforcer.check_command_allowed(child["id"], command_scope="src/engine/build.sh")
        assert len(reasons) == 1

    def test_command_no_scope_unrestricted(self, tmp_path: Path) -> None:
        store, ctx = _store_context(tmp_path)
        event_bus = EventBus()
        collab = CollaborationService(store, event_bus)
        enforcer = WriteScopeEnforcer(store)

        child = _create_child_with_scope(
            store, collab,
            parent_task_id=ctx["parent_task"]["id"],
            session_id=ctx["session"]["id"],
            title="unscoped worker",
        )
        reasons = enforcer.check_command_allowed(child["id"])
        assert reasons == []

    def test_scoped_command_without_target_rejected(self, tmp_path: Path) -> None:
        store, ctx = _store_context(tmp_path)
        event_bus = EventBus()
        collab = CollaborationService(store, event_bus)
        enforcer = WriteScopeEnforcer(store)

        child = _create_child_with_scope(
            store, collab,
            parent_task_id=ctx["parent_task"]["id"],
            session_id=ctx["session"]["id"],
            title="scoped worker",
            write_scope=["src/ui/"],
        )
        reasons = enforcer.check_command_allowed(child["id"])
        assert len(reasons) == 1


# ---------------------------------------------------------------------------
# P9.4 & P9.9: Overlap and conflict detection
# ---------------------------------------------------------------------------


class TestOverlapAndConflictDetection:
    def test_overlap_detected_before_dispatch(self, tmp_path: Path) -> None:
        store, ctx = _store_context(tmp_path)
        enforcer = WriteScopeEnforcer(store)

        subtasks = [
            {"id": "ui_agent", "ownedScope": ["src/"]},
            {"id": "engine_agent", "ownedScope": ["src/"]},
        ]
        reasons = enforcer.check_overlap_before_dispatch(subtasks)
        assert len(reasons) > 0
        assert any("Overlapping" in r for r in reasons)

    def test_no_overlap_passes(self, tmp_path: Path) -> None:
        store, ctx = _store_context(tmp_path)
        enforcer = WriteScopeEnforcer(store)

        subtasks = [
            {"id": "ui_agent", "ownedScope": ["src/ui/"]},
            {"id": "engine_agent", "ownedScope": ["src/engine/"]},
        ]
        reasons = enforcer.check_overlap_before_dispatch(subtasks)
        assert reasons == []

    def test_nested_overlap_detected_before_dispatch(self, tmp_path: Path) -> None:
        store, ctx = _store_context(tmp_path)
        enforcer = WriteScopeEnforcer(store)

        subtasks = [
            {"id": "all_src", "ownedScope": ["src/"]},
            {"id": "ui_agent", "ownedScope": ["src/ui/"]},
        ]
        reasons = enforcer.check_overlap_before_dispatch(subtasks)
        assert len(reasons) > 0
        assert any("Overlapping" in r for r in reasons)

    def test_patch_conflict_detected(self, tmp_path: Path) -> None:
        store, ctx = _store_context(tmp_path)
        enforcer = WriteScopeEnforcer(store)

        patches = [
            {"targetPath": "src/ui/Button.tsx", "producerTaskId": "ui_agent"},
            {"targetPath": "src/ui/Button.tsx", "producerTaskId": "theme_agent"},
        ]
        reasons = enforcer.check_patch_conflicts(patches)
        assert len(reasons) == 1
        assert "Conflicting" in reasons[0]

    def test_no_patch_conflict(self, tmp_path: Path) -> None:
        store, ctx = _store_context(tmp_path)
        enforcer = WriteScopeEnforcer(store)

        patches = [
            {"targetPath": "src/ui/Button.tsx", "producerTaskId": "ui_agent"},
            {"targetPath": "src/engine/audio.py", "producerTaskId": "engine_agent"},
        ]
        reasons = enforcer.check_patch_conflicts(patches)
        assert reasons == []


# ---------------------------------------------------------------------------
# P9.5: Patch artifact recording before application
# ---------------------------------------------------------------------------


class TestPatchArtifactRecording:
    def test_patch_artifact_recorded_before_apply(self, tmp_path: Path) -> None:
        """Patch artifacts should be recorded in 'proposed' status before applying."""
        store, ctx = _store_context(tmp_path)

        # Create a patch artifact in proposed status
        artifact = store.create_artifact({
            "sessionId": ctx["session"]["id"],
            "parentTaskId": ctx["parent_task"]["id"],
            "producerTaskId": ctx["parent_task"]["id"],
            "kind": "patch",
            "title": "UI button fix",
            "status": "proposed",
            "metadata": {
                "targetPath": "src/ui/Button.tsx",
                "diff": "--- a/src/ui/Button.tsx\n+++ b/src/ui/Button.tsx\n@@ -1 +1 @@\n-old\n+new",
            },
        })
        assert artifact["artifact"]["status"] == "proposed"
        assert artifact["artifact"]["kind"] == "patch"

        # After review, update to applied
        updated = store.update_artifact({"artifactId": artifact["artifact"]["id"], "status": "applied"})
        assert updated["artifact"]["status"] == "applied"


# ---------------------------------------------------------------------------
# P9.6 & P9.7: Reviewer gate validation and rejection blocking
# ---------------------------------------------------------------------------


class TestReviewerGate:
    def test_review_approved_allows_merge(self, tmp_path: Path) -> None:
        store, ctx = _store_context(tmp_path)
        enforcer = WriteScopeEnforcer(store)

        reasons = enforcer.check_reviewer_gate({
            "reviewStatus": "approved",
            "mergeRequested": True,
        })
        assert reasons == []

    def test_review_rejected_blocks_merge(self, tmp_path: Path) -> None:
        store, ctx = _store_context(tmp_path)
        enforcer = WriteScopeEnforcer(store)

        reasons = enforcer.check_reviewer_gate({
            "reviewStatus": "rejected",
            "mergeRequested": True,
        })
        assert len(reasons) == 1
        assert "Cannot merge" in reasons[0]

    def test_changes_requested_blocks_merge(self, tmp_path: Path) -> None:
        store, ctx = _store_context(tmp_path)
        enforcer = WriteScopeEnforcer(store)

        reasons = enforcer.check_reviewer_gate({
            "reviewStatus": "changes_requested",
            "mergeRequested": True,
        })
        assert len(reasons) == 1
        assert "Cannot merge" in reasons[0]

    def test_invalid_review_status(self, tmp_path: Path) -> None:
        store, ctx = _store_context(tmp_path)
        enforcer = WriteScopeEnforcer(store)

        reasons = enforcer.check_reviewer_gate({
            "reviewStatus": "unknown_status",
        })
        assert any("Invalid" in r for r in reasons)

    def test_pending_review_no_block(self, tmp_path: Path) -> None:
        store, ctx = _store_context(tmp_path)
        enforcer = WriteScopeEnforcer(store)

        reasons = enforcer.check_reviewer_gate({
            "reviewStatus": "pending",
        })
        assert reasons == []

    def test_review_rejection_prevents_artifact_finalization(self, tmp_path: Path) -> None:
        """Reviewer rejection should block artifact from reaching 'verified' status."""
        store, ctx = _store_context(tmp_path)

        # Create patch artifact
        artifact = store.create_artifact({
            "sessionId": ctx["session"]["id"],
            "parentTaskId": ctx["parent_task"]["id"],
            "producerTaskId": ctx["parent_task"]["id"],
            "kind": "patch",
            "title": "Engine fix",
            "status": "proposed",
        })
        art_id = artifact["artifact"]["id"]

        # Simulate reviewer rejection — artifact should be marked rejected
        updated = store.update_artifact({"artifactId": art_id, "status": "rejected"})
        assert updated["artifact"]["status"] == "rejected"

        # A rejected artifact should not be allowed to move to verified
        # (this is a policy check at application level)
        enforcer = WriteScopeEnforcer(store)
        reasons = enforcer.check_reviewer_gate({
            "reviewStatus": "rejected",
            "mergeRequested": True,
        })
        assert len(reasons) > 0
        with pytest.raises(ValueError, match="Rejected artifacts cannot transition"):
            store.update_artifact({"artifactId": art_id, "status": "verified"})

    def test_review_approval_allows_artifact_finalization(self, tmp_path: Path) -> None:
        """Reviewer approval should allow artifact to reach 'verified' status."""
        store, ctx = _store_context(tmp_path)

        artifact = store.create_artifact({
            "sessionId": ctx["session"]["id"],
            "parentTaskId": ctx["parent_task"]["id"],
            "producerTaskId": ctx["parent_task"]["id"],
            "kind": "patch",
            "title": "UI fix",
            "status": "applied",
        })
        art_id = artifact["artifact"]["id"]

        # Reviewer approves → artifact goes to verified
        verified = store.update_artifact({"artifactId": art_id, "status": "verified"})
        assert verified["artifact"]["status"] == "verified"


class TestWriteScopeToolIntegration:
    def _scoped_runtime_task(self, tmp_path: Path) -> tuple[SQLiteStore, dict[str, Any]]:
        store, ctx = _store_context(tmp_path)
        event_bus = EventBus()
        collab = CollaborationService(store, event_bus)
        child = _create_child_with_scope(
            store, collab,
            parent_task_id=ctx["parent_task"]["id"],
            session_id=ctx["session"]["id"],
            title="scoped worker",
            write_scope=["src/ui/"],
        )
        runtime_task = _create_runtime_task_for_child(
            store,
            session_id=ctx["session"]["id"],
            child_task_id=child["id"],
        )
        ctx["runtime_task"] = runtime_task
        return store, ctx

    def test_write_file_rejects_out_of_scope_runtime_task(self, tmp_path: Path) -> None:
        store, ctx = self._scoped_runtime_task(tmp_path)
        tools = build_builtin_tools(policy_guard=PolicyGuard(), store=store)

        with pytest.raises(ValueError, match="Write scope violation"):
            tools["write_file"]({
                "workspaceRoot": str(ctx["workspace_root"]),
                "taskId": ctx["runtime_task"]["id"],
                "path": "src/engine/audio.py",
                "content": "x = 1\n",
            })

    def test_write_file_requires_approval_before_writing(self, tmp_path: Path) -> None:
        store, ctx = self._scoped_runtime_task(tmp_path)
        tools = build_builtin_tools(policy_guard=PolicyGuard(), store=store)
        target = ctx["workspace_root"] / "src" / "ui" / "Button.tsx"
        params = {
            "workspaceRoot": str(ctx["workspace_root"]),
            "taskId": ctx["runtime_task"]["id"],
            "path": "src/ui/Button.tsx",
            "content": "export const Button = () => null;\n",
        }

        result = tools["write_file"](params)

        assert result["status"] == "approval_required"
        assert not target.exists()
        approval_id = result["approval"]["id"]

        store.resolve_approval(approval_id, "approved")
        approved = tools["write_file"]({**params, "approvalId": approval_id})

        assert approved["status"] == "written"
        assert target.read_text(encoding="utf-8") == params["content"]

    def test_write_file_overwrite_false_rejects_existing_file(self, tmp_path: Path) -> None:
        store, ctx = self._scoped_runtime_task(tmp_path)
        tools = build_builtin_tools(policy_guard=PolicyGuard(), store=store)
        target = ctx["workspace_root"] / "src" / "ui" / "Button.tsx"
        target.parent.mkdir(parents=True)
        target.write_text("existing\n", encoding="utf-8")

        with pytest.raises(ValueError, match="overwrite is false"):
            tools["write_file"]({
                "workspaceRoot": str(ctx["workspace_root"]),
                "taskId": ctx["runtime_task"]["id"],
                "path": "src/ui/Button.tsx",
                "content": "replacement\n",
                "overwrite": False,
            })

        assert target.read_text(encoding="utf-8") == "existing\n"

    def test_apply_patch_rejects_out_of_scope_runtime_task(self, tmp_path: Path) -> None:
        store, ctx = self._scoped_runtime_task(tmp_path)
        tools = build_builtin_tools(policy_guard=PolicyGuard(), store=store)

        with pytest.raises(ValueError, match="Write scope violation"):
            tools["apply_patch"]({
                "workspaceRoot": str(ctx["workspace_root"]),
                "taskId": ctx["runtime_task"]["id"],
                "files": [{"path": "src/engine/audio.py", "content": "x = 1\n"}],
            })

    def test_run_command_rejects_out_of_scope_cwd_runtime_task(self, tmp_path: Path) -> None:
        store, ctx = self._scoped_runtime_task(tmp_path)
        tools = build_builtin_tools(policy_guard=PolicyGuard(), store=store)
        (ctx["workspace_root"] / "src" / "engine").mkdir(parents=True)

        with pytest.raises(ValueError, match="Write scope violation"):
            tools["run_command"]({
                "workspaceRoot": str(ctx["workspace_root"]),
                "taskId": ctx["runtime_task"]["id"],
                "cwd": "src/engine",
                "command": "Write-Output safe",
            })
