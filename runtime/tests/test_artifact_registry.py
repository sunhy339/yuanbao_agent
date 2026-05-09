"""Tests for P3 Artifact Registry + P2 Generation Report + P4 Artifact Linking.

Covers:
- Artifact table migration (new DB + existing DB)
- create_artifact / update_artifact / list_artifacts
- Filters: session, parent task, producer task, kind, status
- Artifact kind validation (plan, file, patch, review, test_report, asset)
- Artifact status transitions (proposed -> applied -> verified, rejected)
- Generation report builder (children, artifacts, counts)
- Artifact contract validator
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from local_agent_runtime.models import ArtifactKind, ArtifactStatus
from local_agent_runtime.policy.proposal_validator import validate_artifact_contract
from local_agent_runtime.services.generation_report import build_generation_report
from local_agent_runtime.store.sqlite_store import SQLiteStore


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


# ---------------------------------------------------------------------------
# P3: Artifact Registry — Migration
# ---------------------------------------------------------------------------


class TestArtifactMigration:
    def test_migration_on_new_db(self, store: SQLiteStore):
        """artifacts table exists on a fresh database."""
        rows = store._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='artifacts'"
        ).fetchall()
        assert len(rows) == 1

    def test_migration_on_existing_db(self, store: SQLiteStore):
        """Bootstrap is idempotent — re-running doesn't fail."""
        store._bootstrap()
        rows = store._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='artifacts'"
        ).fetchall()
        assert len(rows) == 1

    def test_indexes_exist(self, store: SQLiteStore):
        indexes = {
            r["name"]
            for r in store._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            ).fetchall()
        }
        assert "idx_artifacts_session" in indexes
        assert "idx_artifacts_parent_task" in indexes
        assert "idx_artifacts_producer_task" in indexes
        assert "idx_artifacts_kind" in indexes
        assert "idx_artifacts_status" in indexes


# ---------------------------------------------------------------------------
# P3: Artifact Registry — CRUD
# ---------------------------------------------------------------------------


class TestCreateArtifact:
    def test_minimal_create(self, store: SQLiteStore):
        result = store.create_artifact({
            "sessionId": "s1",
            "parentTaskId": "t1",
            "producerTaskId": "ct1",
            "kind": "plan",
        })
        a = result["artifact"]
        assert a["id"].startswith("art_")
        assert a["sessionId"] == "s1"
        assert a["parentTaskId"] == "t1"
        assert a["producerTaskId"] == "ct1"
        assert a["kind"] == "plan"
        assert a["status"] == "proposed"
        assert a["title"] is None
        assert a["content"] == {}
        assert a["metadata"] == {}
        assert isinstance(a["createdAt"], int)
        assert isinstance(a["updatedAt"], int)

    def test_full_create(self, store: SQLiteStore):
        result = store.create_artifact({
            "sessionId": "s1",
            "parentTaskId": "t1",
            "producerTaskId": "ct1",
            "kind": "patch",
            "title": "UI component patch",
            "description": "Adds button component",
            "content": {"diff": "+button code"},
            "metadata": {"files": ["ui/button.tsx"]},
        })
        a = result["artifact"]
        assert a["title"] == "UI component patch"
        assert a["description"] == "Adds button component"
        assert a["content"] == {"diff": "+button code"}
        assert a["metadata"] == {"files": ["ui/button.tsx"]}

    def test_invalid_kind_rejected(self, store: SQLiteStore):
        with pytest.raises(ValueError, match="Invalid artifact kind"):
            store.create_artifact({
                "sessionId": "s1",
                "parentTaskId": "t1",
                "producerTaskId": "ct1",
                "kind": "invalid",
            })

    @pytest.mark.parametrize("kind", ["plan", "file", "patch", "review", "test_report", "asset"])
    def test_all_kinds_accepted(self, store: SQLiteStore, kind: str):
        result = store.create_artifact({
            "sessionId": "s1",
            "parentTaskId": "t1",
            "producerTaskId": "ct1",
            "kind": kind,
        })
        assert result["artifact"]["kind"] == kind


class TestUpdateArtifact:
    def _create(self, store: SQLiteStore) -> str:
        result = store.create_artifact({
            "sessionId": "s1",
            "parentTaskId": "t1",
            "producerTaskId": "ct1",
            "kind": "file",
        })
        return result["artifact"]["id"]

    def test_update_status(self, store: SQLiteStore):
        aid = self._create(store)
        result = store.update_artifact({"artifactId": aid, "status": "applied"})
        assert result["artifact"]["status"] == "applied"

    def test_status_transitions(self, store: SQLiteStore):
        aid = self._create(store)
        store.update_artifact({"artifactId": aid, "status": "applied"})
        result = store.update_artifact({"artifactId": aid, "status": "verified"})
        assert result["artifact"]["status"] == "verified"

    def test_reject_status(self, store: SQLiteStore):
        aid = self._create(store)
        result = store.update_artifact({"artifactId": aid, "status": "rejected"})
        assert result["artifact"]["status"] == "rejected"

    def test_invalid_status_rejected(self, store: SQLiteStore):
        aid = self._create(store)
        with pytest.raises(ValueError, match="Invalid artifact status"):
            store.update_artifact({"artifactId": aid, "status": "invalid"})

    def test_update_title_and_description(self, store: SQLiteStore):
        aid = self._create(store)
        result = store.update_artifact({
            "artifactId": aid,
            "title": "Updated title",
            "description": "Updated desc",
        })
        assert result["artifact"]["title"] == "Updated title"
        assert result["artifact"]["description"] == "Updated desc"

    def test_update_content_and_metadata(self, store: SQLiteStore):
        aid = self._create(store)
        result = store.update_artifact({
            "artifactId": aid,
            "content": {"path": "src/main.py"},
            "metadata": {"size": 42},
        })
        assert result["artifact"]["content"] == {"path": "src/main.py"}
        assert result["artifact"]["metadata"] == {"size": 42}

    def test_not_found_raises(self, store: SQLiteStore):
        with pytest.raises(ValueError, match="Artifact not found"):
            store.update_artifact({"artifactId": "art_nonexistent", "status": "applied"})

    def test_empty_update_returns_unchanged(self, store: SQLiteStore):
        aid = self._create(store)
        result = store.update_artifact({"artifactId": aid})
        assert result["artifact"]["status"] == "proposed"


class TestListArtifacts:
    def _seed(self, store: SQLiteStore):
        a1 = store.create_artifact({
            "sessionId": "s1", "parentTaskId": "t1",
            "producerTaskId": "ct1", "kind": "plan",
        })
        store.update_artifact({"artifactId": a1["artifact"]["id"], "status": "applied"})
        store.create_artifact({
            "sessionId": "s1", "parentTaskId": "t1",
            "producerTaskId": "ct2", "kind": "patch",
        })
        a3 = store.create_artifact({
            "sessionId": "s1", "parentTaskId": "t2",
            "producerTaskId": "ct3", "kind": "file",
        })
        store.update_artifact({"artifactId": a3["artifact"]["id"], "status": "verified"})

    def test_list_all(self, store: SQLiteStore):
        self._seed(store)
        result = store.list_artifacts({"sessionId": "s1"})
        assert len(result["artifacts"]) == 3

    def test_filter_by_parent_task(self, store: SQLiteStore):
        self._seed(store)
        result = store.list_artifacts({"parentTaskId": "t1"})
        assert len(result["artifacts"]) == 2

    def test_filter_by_producer_task(self, store: SQLiteStore):
        self._seed(store)
        result = store.list_artifacts({"producerTaskId": "ct2"})
        assert len(result["artifacts"]) == 1
        assert result["artifacts"][0]["kind"] == "patch"

    def test_filter_by_kind(self, store: SQLiteStore):
        self._seed(store)
        result = store.list_artifacts({"kind": "patch"})
        assert len(result["artifacts"]) == 1

    def test_filter_by_status(self, store: SQLiteStore):
        self._seed(store)
        result = store.list_artifacts({"status": "proposed"})
        assert len(result["artifacts"]) == 1

    def test_combined_filters(self, store: SQLiteStore):
        self._seed(store)
        result = store.list_artifacts({
            "parentTaskId": "t1", "kind": "plan",
        })
        assert len(result["artifacts"]) == 1

    def test_empty_result(self, store: SQLiteStore):
        self._seed(store)
        result = store.list_artifacts({"sessionId": "s_nonexistent"})
        assert result["artifacts"] == []

    def test_limit(self, store: SQLiteStore):
        self._seed(store)
        result = store.list_artifacts({"sessionId": "s1", "limit": 2})
        assert len(result["artifacts"]) == 2


# ---------------------------------------------------------------------------
# P3: Artifact Contract Validator
# ---------------------------------------------------------------------------


class TestArtifactContractValidator:
    def test_valid_kind_and_status(self):
        reasons = validate_artifact_contract({"kind": "patch", "status": "proposed"})
        assert reasons == []

    def test_invalid_kind(self):
        reasons = validate_artifact_contract({"kind": "invalid_kind"})
        assert any("Invalid artifact kind" in r for r in reasons)

    def test_invalid_status(self):
        reasons = validate_artifact_contract({"status": "invalid_status"})
        assert any("Invalid artifact status" in r for r in reasons)

    def test_no_kind_no_status_passes(self):
        reasons = validate_artifact_contract({})
        assert reasons == []

    def test_valid_kind_only(self):
        reasons = validate_artifact_contract({"kind": "review"})
        assert reasons == []

    def test_all_valid_kinds(self):
        for kind in ["plan", "file", "patch", "review", "test_report", "asset"]:
            reasons = validate_artifact_contract({"kind": kind})
            assert reasons == [], f"Kind {kind!r} should pass"

    def test_all_valid_statuses(self):
        for status in ["proposed", "applied", "verified", "rejected"]:
            reasons = validate_artifact_contract({"status": status})
            assert reasons == [], f"Status {status!r} should pass"


# ---------------------------------------------------------------------------
# P2: Generation Report
# ---------------------------------------------------------------------------


class TestGenerationReport:
    def test_empty_report(self, store: SQLiteStore):
        task = _create_parent_task(store)
        report = build_generation_report(store, parent_task_id=task["id"])
        assert report["parentTaskId"] == task["id"]
        assert report["sessionId"] == "s1"
        assert report["childTasks"] == []
        assert report["artifacts"] == []
        assert report["counts"]["children"] == 0
        assert report["counts"]["artifacts"] == 0

    def test_report_with_completed_child(self, store: SQLiteStore):
        parent = _create_parent_task(store)
        child = _create_collab_child(store, parent["id"], title="UI Worker")
        worker = store.upsert_agent_worker({"name": "w1", "role": "worker"})
        store.claim_collaboration_task({
            "taskId": child["task"]["id"],
            "workerId": worker["worker"]["id"],
        })
        store.complete_collaboration_task({
            "taskId": child["task"]["id"],
            "workerId": worker["worker"]["id"],
            "result": {"filesChanged": 3},
        })

        report = build_generation_report(store, parent_task_id=parent["id"])
        assert report["counts"]["children"] == 1
        ct = report["childTasks"][0]
        assert ct["status"] == "completed"
        assert ct["taskId"] == child["task"]["id"]
        assert ct["title"] == "UI Worker"
        assert ct["workerId"] == worker["worker"]["id"]
        assert ct["result"]["filesChanged"] == 3
        assert ct["durationMs"] is not None
        assert isinstance(ct["durationMs"], int)

    def test_report_with_failed_child(self, store: SQLiteStore):
        parent = _create_parent_task(store)
        child = _create_collab_child(store, parent["id"])
        worker = store.upsert_agent_worker({"name": "w1", "role": "worker"})
        store.claim_collaboration_task({
            "taskId": child["task"]["id"],
            "workerId": worker["worker"]["id"],
        })
        store.fail_collaboration_task({
            "taskId": child["task"]["id"],
            "workerId": worker["worker"]["id"],
            "error": {"code": "TIMEOUT", "message": "exceeded 30s"},
        })

        report = build_generation_report(store, parent_task_id=parent["id"])
        ct = report["childTasks"][0]
        assert ct["status"] == "failed"
        assert ct["error"]["code"] == "TIMEOUT"
        assert report["counts"]["childrenByStatus"]["failed"] == 1

    def test_report_with_mixed_children(self, store: SQLiteStore):
        parent = _create_parent_task(store)
        c1 = _create_collab_child(store, parent["id"], title="Explorer")
        c2 = _create_collab_child(store, parent["id"], title="Worker")
        worker = store.upsert_agent_worker({"name": "w1", "role": "worker"})
        store.claim_collaboration_task({
            "taskId": c1["task"]["id"], "workerId": worker["worker"]["id"],
        })
        store.complete_collaboration_task({
            "taskId": c1["task"]["id"], "workerId": worker["worker"]["id"],
            "result": {"scope": "src/"},
        })
        store.claim_collaboration_task({
            "taskId": c2["task"]["id"], "workerId": worker["worker"]["id"],
        })
        store.fail_collaboration_task({
            "taskId": c2["task"]["id"], "workerId": worker["worker"]["id"],
            "error": {"code": "CRASH"},
        })

        report = build_generation_report(store, parent_task_id=parent["id"])
        assert report["counts"]["children"] == 2
        assert report["counts"]["childrenByStatus"]["completed"] == 1
        assert report["counts"]["childrenByStatus"]["failed"] == 1

    def test_report_with_artifacts(self, store: SQLiteStore):
        parent = _create_parent_task(store)
        _create_collab_child(store, parent["id"])
        store.create_artifact({
            "sessionId": "s1",
            "parentTaskId": parent["id"],
            "producerTaskId": "ct_child1",
            "kind": "plan",
            "title": "Scope plan",
        })
        store.create_artifact({
            "sessionId": "s1",
            "parentTaskId": parent["id"],
            "producerTaskId": "ct_child2",
            "kind": "patch",
            "title": "UI patch",
        })

        report = build_generation_report(store, parent_task_id=parent["id"])
        assert report["counts"]["artifacts"] == 2
        assert report["counts"]["artifactsByKind"]["plan"] == 1
        assert report["counts"]["artifactsByKind"]["patch"] == 1
        art_kinds = {a["kind"] for a in report["artifacts"]}
        assert art_kinds == {"plan", "patch"}

    def test_report_with_session_id_override(self, store: SQLiteStore):
        parent = _create_parent_task(store)
        report = build_generation_report(
            store, parent_task_id=parent["id"], session_id="override_s"
        )
        assert report["sessionId"] == "override_s"

    def test_report_child_with_dependencies(self, store: SQLiteStore):
        parent = _create_parent_task(store)
        _create_collab_child(store, parent["id"], title="Explorer")
        _create_collab_child(store, parent["id"], title="Worker", dependencies=["dep_1"])
        report = build_generation_report(store, parent_task_id=parent["id"])
        workers = [c for c in report["childTasks"] if c["title"] == "Worker"]
        assert workers[0]["dependencies"] == ["dep_1"]


# ---------------------------------------------------------------------------
# P4: Result Message and Artifact Linking (via metadata)
# ---------------------------------------------------------------------------


class TestArtifactLinking:
    def test_artifact_with_producer_task_reference(self, store: SQLiteStore):
        parent = _create_parent_task(store)
        child = _create_collab_child(store, parent["id"])
        child_id = child["task"]["id"]

        # Worker produces an artifact
        art = store.create_artifact({
            "sessionId": "s1",
            "parentTaskId": parent["id"],
            "producerTaskId": child_id,
            "kind": "file",
            "title": "Generated component",
            "content": {"path": "src/Button.tsx"},
        })

        # Verify lookup by producer task works
        found = store.list_artifacts({"producerTaskId": child_id})
        assert len(found["artifacts"]) == 1
        assert found["artifacts"][0]["title"] == "Generated component"

        # Verify lookup by parent task also works
        by_parent = store.list_artifacts({"parentTaskId": parent["id"]})
        assert len(by_parent["artifacts"]) == 1

    def test_artifact_linked_in_generation_report(self, store: SQLiteStore):
        parent = _create_parent_task(store)
        child = _create_collab_child(store, parent["id"])
        child_id = child["task"]["id"]

        store.create_artifact({
            "sessionId": "s1",
            "parentTaskId": parent["id"],
            "producerTaskId": child_id,
            "kind": "patch",
            "title": "Fix bug",
        })

        report = build_generation_report(store, parent_task_id=parent["id"])
        assert len(report["artifacts"]) == 1
        assert report["artifacts"][0]["producerTaskId"] == child_id
        assert report["artifacts"][0]["kind"] == "patch"

    def test_multiple_artifacts_from_same_producer(self, store: SQLiteStore):
        parent = _create_parent_task(store)
        child = _create_collab_child(store, parent["id"])
        child_id = child["task"]["id"]

        for kind in ["plan", "file", "patch"]:
            store.create_artifact({
                "sessionId": "s1",
                "parentTaskId": parent["id"],
                "producerTaskId": child_id,
                "kind": kind,
            })

        found = store.list_artifacts({"producerTaskId": child_id})
        assert len(found["artifacts"]) == 3
        kinds = {a["kind"] for a in found["artifacts"]}
        assert kinds == {"plan", "file", "patch"}

    def test_artifact_metadata_contains_message_id(self, store: SQLiteStore):
        """Artifacts can reference message IDs via metadata."""
        parent = _create_parent_task(store)
        art = store.create_artifact({
            "sessionId": "s1",
            "parentTaskId": parent["id"],
            "producerTaskId": "ct1",
            "kind": "file",
            "metadata": {"messageIds": ["msg_1", "msg_2"]},
        })
        assert art["artifact"]["metadata"]["messageIds"] == ["msg_1", "msg_2"]
