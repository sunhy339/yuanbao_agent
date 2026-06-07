from __future__ import annotations

import json
from typing import Any

from local_agent_runtime.event_bus import EventBus
from local_agent_runtime.memory import MemoryManager, MemoryRetriever
from local_agent_runtime.memory.store import MemoryStore
from local_agent_runtime.memory.types import MemoryKind
from local_agent_runtime.orchestrator.service import Orchestrator
from local_agent_runtime.provider.adapter import ProviderAdapter
from local_agent_runtime.rpc.server import JsonRpcServer
from local_agent_runtime.services import CollaborationService, SubagentService
from local_agent_runtime.store.sqlite_store import SQLiteStore
from local_agent_runtime.tools import build_builtin_tools
from local_agent_runtime.tools.registry import ToolRegistry
from local_agent_runtime.policy.guard import PolicyGuard


def _make_harness(tmp_path: Any) -> tuple[JsonRpcServer, SQLiteStore]:
    db_path = tmp_path / "test.sqlite3"
    event_bus = EventBus()
    store = SQLiteStore(str(db_path))
    config = store.get_config({})["config"]
    policy_guard = PolicyGuard(approval_mode=config["policy"]["approvalMode"])
    collaboration = CollaborationService(store, event_bus)
    subagent_service = SubagentService(store, collaboration)
    tool_registry = ToolRegistry(
        build_builtin_tools(policy_guard=policy_guard, store=store, subagent_service=subagent_service)
    )
    provider = ProviderAdapter()
    orchestrator = Orchestrator(
        store=store,
        event_bus=event_bus,
        tool_registry=tool_registry,
        provider=provider,
    )
    server = JsonRpcServer(orchestrator=orchestrator, store=store, event_bus=event_bus)
    return server, store


def _call(server: JsonRpcServer, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    envelope = {
        "jsonrpc": "2.0",
        "id": "test-1",
        "method": method,
        "params": params or {},
    }
    response = server.handle_line(json.dumps(envelope, ensure_ascii=False))
    assert "error" not in response, f"RPC error: {response.get('error')}"
    return response["result"]


def test_runtime_status_reports_current_task_transport_worktree_and_memory(tmp_path: Any) -> None:
    server, store = _make_harness(tmp_path)
    workspace = store.upsert_workspace(str(tmp_path / "project"))
    session = store.create_session(workspace["id"], "Runtime status")
    task = store.create_task(
        session_id=session["id"],
        task_type="edit",
        goal="Fix the parser",
        plan=[],
        status="running",
        current_step="Run verification",
        routing={"scenario": "code_edit", "strategy": "react_standard"},
    )

    memory_store = MemoryStore(store)
    memory_manager = MemoryManager(memory_store, MemoryRetriever(memory_store))
    recalled = memory_manager.remember(
        content="Use python -m pytest in this repo",
        workspace_id=workspace["id"],
        kind=MemoryKind.LONG_TERM,
        metadata={"category": "project_convention", "confidence": 0.9},
    )
    memory_manager.remember(
        content="[Supplement] Always keep verification commands visible",
        session_id=session["id"],
        workspace_id=workspace["id"],
        metadata={"category": "user_preference", "source": "supplement", "confidence": 0.6},
    )

    worktree = store.create_worktree(
        {
            "taskId": task["id"],
            "sessionId": session["id"],
            "workspaceId": workspace["id"],
            "baseRef": "HEAD",
            "branchName": "agent/fix-parser",
            "worktreePath": str(tmp_path / "wt"),
            "cleanupPolicy": "ask_user",
            "mergePolicy": "approval_required",
        }
    )["worktree"]
    worktree = store.update_worktree(
        {
            "worktreeId": worktree["id"],
            "status": "active",
        }
    )["worktree"]

    command = store.create_command_log(
        task_id=task["id"],
        command="python -m pytest -q",
        cwd=str(tmp_path / "wt"),
        shell="powershell",
    )
    store.update_command_log(
        command["id"],
        status="completed",
        exit_code=0,
        finished_at=command["startedAt"] + 1250,
    )

    snapshot = store.create_context_snapshot(
        session_id=session["id"],
        task_id=task["id"],
        memory_ids=[recalled.id],
        token_estimate=1200,
        max_context_tokens=20000,
        tool_policy_decision={
            "phase": "execution",
            "decisionDetails": [
                {
                    "toolName": "run_command",
                    "requiresApproval": True,
                    "untrustedContentSignals": [
                        {"contentSource": "web", "contentTrust": "untrusted"},
                    ],
                }
            ],
        },
        active_worktree={"id": worktree["id"], "path": worktree["worktreePath"]},
    )
    turn = store.create_provider_turn(
        task_id=task["id"],
        session_id=session["id"],
        turn_index=1,
        model="gpt-5.4",
        request_message_count=8,
        request_tool_count=4,
        request_token_estimate=1200,
    )
    store.complete_provider_turn(
        turn_id=turn["id"],
        finish_reason="stop",
        usage={"input_tokens": 1200, "output_tokens": 240},
        tool_call_count=1,
        snapshot_id=snapshot["id"],
        turn_decision="tool_calls",
        response_transport="fallback_non_stream",
    )

    result = _call(server, "runtime.status", {"taskId": task["id"]})

    assert result["currentTask"]["id"] == task["id"]
    assert result["currentTask"]["currentStep"] == "Run verification"
    assert result["provider"]["latestTurn"]["responseTransport"] == "fallback_non_stream"
    assert result["provider"]["streaming"]["isStreaming"] is False
    assert result["provider"]["streaming"]["fellBackToNonStream"] is True
    assert result["worktree"]["active"]["id"] == worktree["id"]
    assert result["commands"][0]["command"] == "python -m pytest -q"
    assert result["memory"]["recalled"][0]["id"] == recalled.id
    assert result["memory"]["candidateCount"] >= 1
    assert result["signals"]["untrustedContent"] == [
        {"contentSource": "web", "contentTrust": "untrusted"},
    ]
    assert result["context"]["latestSnapshot"]["tokenEstimate"] == 1200


def test_runtime_status_prefers_active_task_for_session_and_exposes_controls(tmp_path: Any) -> None:
    server, store = _make_harness(tmp_path)
    workspace = store.upsert_workspace(str(tmp_path / "project"))
    session = store.create_session(workspace["id"], "Session status")
    store.create_task(
        session_id=session["id"],
        task_type="edit",
        goal="Older completed task",
        plan=[],
        status="completed",
    )
    active = store.create_task(
        session_id=session["id"],
        task_type="edit",
        goal="Paused task",
        plan=[],
        status="paused",
    )

    result = _call(server, "runtime.status", {"sessionId": session["id"]})

    assert result["session"]["id"] == session["id"]
    assert result["currentTask"]["id"] == active["id"]
    assert result["controls"]["canResume"] is True
    assert result["controls"]["canPause"] is False
    assert result["controls"]["canCancel"] is True
    assert result["permissions"]["approvalMode"] == "on_write_or_command"
    assert result["permissions"]["allowFileWrite"] == "approval_required"
    assert result["permissions"]["allowShell"] == "approval_required"
    assert result["worktree"]["autoBindWriteTasks"] is False
