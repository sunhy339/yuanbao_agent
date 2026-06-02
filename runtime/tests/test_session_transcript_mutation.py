from __future__ import annotations

from pathlib import Path

import pytest

from local_agent_runtime.event_bus import EventBus
from local_agent_runtime.rpc.server import JsonRpcServer
from local_agent_runtime.store.sqlite_store import SQLiteStore


class _RpcOrchestratorStub:
    _memory_manager = None

    def __getattr__(self, name: str):
        def _handler(_params: dict):
            raise NotImplementedError(name)

        return _handler


@pytest.fixture
def store(tmp_path: Path) -> SQLiteStore:
    runtime_store = SQLiteStore(str(tmp_path / "runtime.sqlite3"))
    try:
        yield runtime_store
    finally:
        runtime_store.close()


def _session_with_messages(store: SQLiteStore, tmp_path: Path) -> tuple[dict, list[dict]]:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    workspace = store.upsert_workspace(str(workspace_root))
    session = store.create_session(workspace_id=workspace["id"], title="Transcript")
    messages = [
        store.create_message(session_id=session["id"], role="user", content="第一条"),
        store.create_message(session_id=session["id"], role="assistant", content="第一条回复"),
        store.create_message(session_id=session["id"], role="user", content="第二条"),
    ]
    return session, messages


def test_branch_session_copies_messages_through_target(store: SQLiteStore, tmp_path: Path) -> None:
    session, messages = _session_with_messages(store, tmp_path)

    result = store.branch_session({
        "sessionId": session["id"],
        "messageId": messages[1]["id"],
        "title": "分支",
    })

    assert result["sourceSession"]["id"] == session["id"]
    assert result["session"]["id"] != session["id"]
    assert result["session"]["title"] == "分支"
    assert result["sourceMessage"]["id"] == messages[1]["id"]
    assert result["copiedCount"] == 2
    assert [message["content"] for message in result["messages"]] == ["第一条", "第一条回复"]
    assert all(message.get("taskId") is None for message in result["messages"])


def test_truncate_session_keeps_target_and_deletes_later_messages(store: SQLiteStore, tmp_path: Path) -> None:
    session, messages = _session_with_messages(store, tmp_path)

    result = store.truncate_session({
        "sessionId": session["id"],
        "messageId": messages[0]["id"],
    })

    assert result["targetMessage"]["id"] == messages[0]["id"]
    assert result["deletedCount"] == 2
    assert [message["id"] for message in result["deletedMessages"]] == [messages[1]["id"], messages[2]["id"]]
    assert [message["content"] for message in result["messages"]] == ["第一条"]


def test_delete_message_removes_only_target_message(store: SQLiteStore, tmp_path: Path) -> None:
    session, messages = _session_with_messages(store, tmp_path)

    result = store.delete_message({
        "sessionId": session["id"],
        "messageId": messages[1]["id"],
    })

    assert result["deleted"] is True
    assert result["message"]["content"] == "第一条回复"
    assert [message["content"] for message in result["messages"]] == ["第一条", "第二条"]


def test_create_message_allocates_stable_created_seq(store: SQLiteStore, tmp_path: Path) -> None:
    session, messages = _session_with_messages(store, tmp_path)
    seqs = [message.get("createdSeq") for message in messages]

    assert all(isinstance(seq, int) for seq in seqs)
    assert seqs == sorted(seqs)
    assert len(set(seqs)) == len(seqs)
    listed = store.list_messages({"sessionId": session["id"]})["messages"]
    assert [message["id"] for message in listed] == [message["id"] for message in messages]


def test_branch_and_truncate_use_created_seq_when_timestamps_tie(store: SQLiteStore, tmp_path: Path) -> None:
    session, messages = _session_with_messages(store, tmp_path)
    tied_at = messages[0]["createdAt"]
    store._conn.execute(
        "UPDATE messages SET created_at = ?, updated_at = ? WHERE session_id = ?",
        (tied_at, tied_at, session["id"]),
    )
    store._conn.commit()

    branch = store.branch_session({
        "sessionId": session["id"],
        "messageId": messages[1]["id"],
        "title": "分支",
    })
    assert [message["content"] for message in branch["messages"]] == ["第一条", "第一条回复"]

    truncate = store.truncate_session({
        "sessionId": session["id"],
        "messageId": messages[1]["id"],
    })
    assert [message["content"] for message in truncate["deletedMessages"]] == ["第二条"]
    assert [message["content"] for message in truncate["messages"]] == ["第一条", "第一条回复"]


def test_session_transcript_mutations_are_available_over_rpc(store: SQLiteStore, tmp_path: Path) -> None:
    session, messages = _session_with_messages(store, tmp_path)
    server = JsonRpcServer(
        orchestrator=_RpcOrchestratorStub(),
        store=store,
        event_bus=EventBus(),
    )

    response = server.handle_line(
        """
        {"jsonrpc":"2.0","id":"req_1","method":"session.branch","params":{"sessionId":"%s","messageId":"%s"}}
        """
        % (session["id"], messages[0]["id"])
    )

    assert response["result"]["copiedCount"] == 1
    assert response["result"]["messages"][0]["content"] == "第一条"

    delete_response = server.handle_line(
        """
        {"jsonrpc":"2.0","id":"req_2","method":"message.delete","params":{"sessionId":"%s","messageId":"%s"}}
        """
        % (session["id"], messages[2]["id"])
    )

    assert delete_response["result"]["deleted"] is True
    assert [message["content"] for message in delete_response["result"]["messages"]] == ["第一条", "第一条回复"]
