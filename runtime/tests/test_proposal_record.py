"""Tests for P1 Proposal Record Foundation + P2 Proposal Kinds.

Covers:
- proposal_records table migration
- create_proposal / validate_proposal / apply_proposal / list_proposals
- all 17 ProposalKind values accepted
- kind validation rejects invalid kinds
- status transitions: pending -> accepted/rejected, accepted -> applied
- invalid transitions rejected
- list filters by session, task, kind, status
- serialization roundtrip
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from local_agent_runtime.models import ProposalKind, ProposalRecord, ProposalStatus
from local_agent_runtime.store.sqlite_store import SQLiteStore


@pytest.fixture
def store():
    return SQLiteStore(Path(tempfile.mkdtemp()) / "test.db")


def _create_task(store: SQLiteStore) -> dict:
    return store.create_task(session_id="s1", task_type="agent", goal="g", plan=[])


class TestProposalKindType:
    """P2: All 17 proposal kinds are defined in the ProposalKind literal."""

    EXPECTED_KINDS = [
        "intent_mode", "decomposition", "agent_profile", "model_policy",
        "skill_policy", "tool_policy", "mcp_policy", "context_policy",
        "memory_policy", "artifact_contract", "risk_policy", "approval_policy",
        "test_strategy", "failure_recovery", "event_presentation",
        "synthesis_strategy", "todo_maintenance",
    ]

    def test_all_kinds_accepted(self, store: SQLiteStore):
        _create_task(store)
        for kind in self.EXPECTED_KINDS:
            result = store.create_proposal({
                "kind": kind,
                "sessionId": "s1",
                "taskId": "t1",
                "proposal": {"test": kind},
            })
            assert result["proposal"]["kind"] == kind

    def test_invalid_kind_rejected(self, store: SQLiteStore):
        with pytest.raises(ValueError, match="Invalid proposal kind"):
            store.create_proposal({
                "kind": "invalid_kind",
                "sessionId": "s1",
                "taskId": "t1",
                "proposal": {},
            })


class TestProposalStatusType:
    """ProposalStatus literal has the expected values."""

    def test_statuses(self):
        # ProposalStatus is a Literal; just verify the import works
        assert ProposalStatus is not None


class TestCreateProposal:
    """create_proposal with various field combinations."""

    def test_minimal_create(self, store: SQLiteStore):
        _create_task(store)
        result = store.create_proposal({
            "kind": "tool_policy",
            "sessionId": "s1",
            "taskId": "t1",
            "proposal": {"allowedTools": ["read_file"]},
        })
        p = result["proposal"]
        assert p["kind"] == "tool_policy"
        assert p["status"] == "pending"
        assert p["proposal"] == {"allowedTools": ["read_file"]}
        assert p["source"] == {}
        assert p["validationReasons"] == []
        assert p["appliedTo"] == {}
        assert p["modelId"] is None
        assert p["turnId"] is None

    def test_full_create(self, store: SQLiteStore):
        _create_task(store)
        result = store.create_proposal({
            "kind": "decomposition",
            "sessionId": "s1",
            "taskId": "t1",
            "source": {"proposer": "planner", "version": 1},
            "inputSummary": "Build a music player",
            "proposal": {"subtasks": [{"goal": "UI", "agentType": "worker"}]},
            "modelId": "gpt-4",
            "turnId": "turn_123",
        })
        p = result["proposal"]
        assert p["source"] == {"proposer": "planner", "version": 1}
        assert p["inputSummary"] == "Build a music player"
        assert p["modelId"] == "gpt-4"
        assert p["turnId"] == "turn_123"

    def test_id_is_prefixed(self, store: SQLiteStore):
        _create_task(store)
        result = store.create_proposal({
            "kind": "intent_mode",
            "sessionId": "s1",
            "taskId": "t1",
            "proposal": {},
        })
        assert result["proposal"]["id"].startswith("prop_")

    def test_missing_kind_raises(self, store: SQLiteStore):
        with pytest.raises(ValueError):
            store.create_proposal({
                "sessionId": "s1",
                "taskId": "t1",
                "proposal": {},
            })


class TestValidateProposal:
    """validate_proposal transitions pending -> accepted or rejected."""

    def _make_proposal(self, store: SQLiteStore) -> str:
        _create_task(store)
        result = store.create_proposal({
            "kind": "tool_policy",
            "sessionId": "s1",
            "taskId": "t1",
            "proposal": {"allowedTools": ["read_file"]},
        })
        return result["proposal"]["id"]

    def test_accept(self, store: SQLiteStore):
        pid = self._make_proposal(store)
        result = store.validate_proposal({
            "proposalId": pid,
            "status": "accepted",
        })
        assert result["proposal"]["status"] == "accepted"
        assert result["proposal"]["validationReasons"] == []

    def test_reject_with_reasons(self, store: SQLiteStore):
        pid = self._make_proposal(store)
        result = store.validate_proposal({
            "proposalId": pid,
            "status": "rejected",
            "reasons": ["unsafe tool: delete_file", "missing dependency"],
        })
        assert result["proposal"]["status"] == "rejected"
        assert result["proposal"]["validationReasons"] == [
            "unsafe tool: delete_file", "missing dependency"
        ]

    def test_reject_twice_raises(self, store: SQLiteStore):
        pid = self._make_proposal(store)
        store.validate_proposal({"proposalId": pid, "status": "rejected"})
        with pytest.raises(ValueError, match="expected 'pending'"):
            store.validate_proposal({"proposalId": pid, "status": "accepted"})

    def test_invalid_status_raises(self, store: SQLiteStore):
        pid = self._make_proposal(store)
        with pytest.raises(ValueError, match="accepted.*rejected"):
            store.validate_proposal({"proposalId": pid, "status": "applied"})

    def test_not_found_raises(self, store: SQLiteStore):
        with pytest.raises(ValueError, match="Proposal not found"):
            store.validate_proposal({"proposalId": "prop_nonexistent", "status": "accepted"})


class TestApplyProposal:
    """apply_proposal transitions accepted -> applied."""

    def _make_accepted(self, store: SQLiteStore) -> str:
        _create_task(store)
        result = store.create_proposal({
            "kind": "model_policy",
            "sessionId": "s1",
            "taskId": "t1",
            "proposal": {"model": "gpt-4"},
        })
        pid = result["proposal"]["id"]
        store.validate_proposal({"proposalId": pid, "status": "accepted"})
        return pid

    def test_apply_with_refs(self, store: SQLiteStore):
        pid = self._make_accepted(store)
        result = store.apply_proposal({
            "proposalId": pid,
            "appliedTo": {"taskId": "t_child_1", "routingId": "r1"},
        })
        assert result["proposal"]["status"] == "applied"
        assert result["proposal"]["appliedTo"] == {
            "taskId": "t_child_1", "routingId": "r1"
        }

    def test_apply_pending_raises(self, store: SQLiteStore):
        _create_task(store)
        result = store.create_proposal({
            "kind": "intent_mode",
            "sessionId": "s1",
            "taskId": "t1",
            "proposal": {},
        })
        with pytest.raises(ValueError, match="expected 'accepted'"):
            store.apply_proposal({
                "proposalId": result["proposal"]["id"],
                "appliedTo": {},
            })

    def test_apply_rejected_raises(self, store: SQLiteStore):
        _create_task(store)
        result = store.create_proposal({
            "kind": "intent_mode",
            "sessionId": "s1",
            "taskId": "t1",
            "proposal": {},
        })
        pid = result["proposal"]["id"]
        store.validate_proposal({"proposalId": pid, "status": "rejected"})
        with pytest.raises(ValueError, match="expected 'accepted'"):
            store.apply_proposal({"proposalId": pid, "appliedTo": {}})


class TestListProposals:
    """list_proposals with various filters."""

    def _seed(self, store: SQLiteStore):
        _create_task(store)
        for kind, status_suffix in [
            ("tool_policy", "a"),
            ("tool_policy", "b"),
            ("model_policy", "c"),
        ]:
            r = store.create_proposal({
                "kind": kind,
                "sessionId": "s1",
                "taskId": "t1",
                "proposal": {"suffix": status_suffix},
            })
            if status_suffix == "a":
                store.validate_proposal({"proposalId": r["proposal"]["id"], "status": "accepted"})
                store.apply_proposal({"proposalId": r["proposal"]["id"], "appliedTo": {}})
            elif status_suffix == "b":
                store.validate_proposal({"proposalId": r["proposal"]["id"], "status": "rejected"})

    def test_list_all(self, store: SQLiteStore):
        self._seed(store)
        result = store.list_proposals({"sessionId": "s1"})
        assert len(result["proposals"]) == 3

    def test_filter_by_kind(self, store: SQLiteStore):
        self._seed(store)
        result = store.list_proposals({"sessionId": "s1", "kind": "tool_policy"})
        assert len(result["proposals"]) == 2

    def test_filter_by_status(self, store: SQLiteStore):
        self._seed(store)
        result = store.list_proposals({"sessionId": "s1", "status": "applied"})
        assert len(result["proposals"]) == 1
        assert result["proposals"][0]["status"] == "applied"

    def test_filter_by_task(self, store: SQLiteStore):
        self._seed(store)
        result = store.list_proposals({"taskId": "t1"})
        assert len(result["proposals"]) == 3

    def test_limit(self, store: SQLiteStore):
        self._seed(store)
        result = store.list_proposals({"sessionId": "s1", "limit": 1})
        assert len(result["proposals"]) == 1

    def test_empty_result(self, store: SQLiteStore):
        self._seed(store)
        result = store.list_proposals({"sessionId": "s_nonexistent"})
        assert len(result["proposals"]) == 0


class TestProposalSerializationRoundtrip:
    """All fields survive a create -> read roundtrip."""

    def test_roundtrip(self, store: SQLiteStore):
        _create_task(store)
        created = store.create_proposal({
            "kind": "decomposition",
            "sessionId": "s1",
            "taskId": "t1",
            "source": {"proposer": "planner"},
            "inputSummary": "Build a music player",
            "proposal": {"subtasks": [{"goal": "UI"}]},
            "modelId": "claude-4",
            "turnId": "turn_abc",
        })
        pid = created["proposal"]["id"]

        # Accept and apply
        store.validate_proposal({"proposalId": pid, "status": "accepted", "reasons": []})
        store.apply_proposal({"proposalId": pid, "appliedTo": {"childTaskId": "ct_1"}})

        listed = store.list_proposals({"taskId": "t1"})
        p = listed["proposals"][0]
        assert p["id"] == pid
        assert p["kind"] == "decomposition"
        assert p["sessionId"] == "s1"
        assert p["taskId"] == "t1"
        assert p["source"] == {"proposer": "planner"}
        assert p["inputSummary"] == "Build a music player"
        assert p["proposal"] == {"subtasks": [{"goal": "UI"}]}
        assert p["status"] == "applied"
        assert p["validationReasons"] == []
        assert p["appliedTo"] == {"childTaskId": "ct_1"}
        assert p["modelId"] == "claude-4"
        assert p["turnId"] == "turn_abc"
        assert isinstance(p["createdAt"], int)
        assert isinstance(p["updatedAt"], int)
        assert p["updatedAt"] >= p["createdAt"]
