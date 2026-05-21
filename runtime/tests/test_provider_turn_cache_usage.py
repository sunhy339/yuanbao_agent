from __future__ import annotations

from pathlib import Path

from local_agent_runtime.event_bus import EventBus
from local_agent_runtime.orchestrator.service import Orchestrator
from local_agent_runtime.policy.guard import PolicyGuard
from local_agent_runtime.provider.adapter import ProviderAdapter
from local_agent_runtime.rpc.server import JsonRpcServer
from local_agent_runtime.services import CollaborationService, SubagentService
from local_agent_runtime.store.sqlite_store import SQLiteStore
from local_agent_runtime.tools import build_builtin_tools
from local_agent_runtime.tools.registry import ToolRegistry


def _make_server(store: SQLiteStore) -> JsonRpcServer:
    event_bus = EventBus()
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
    return JsonRpcServer(orchestrator=orchestrator, store=store, event_bus=event_bus)


def test_provider_turn_exposes_cache_usage(tmp_path: Path) -> None:
    store = SQLiteStore(str(tmp_path / "runtime.sqlite3"))
    try:
        workspace = store.upsert_workspace(str(tmp_path / "workspace"))
        session = store.create_session(workspace_id=workspace["id"], title="cache")
        task = store.create_task(session_id=session["id"], task_type="edit", goal="cache", plan=[])
        turn = store.create_provider_turn(task_id=task["id"], session_id=session["id"], turn_index=0, model="gpt-5.4")
        store.complete_provider_turn(
            turn_id=turn["id"],
            finish_reason="stop",
            usage={
                "prompt_tokens": 1000,
                "completion_tokens": 100,
                "total_tokens": 1100,
                "prompt_tokens_details": {"cached_tokens": 768},
            },
            tool_call_count=0,
            response_transport="stream",
        )

        turns = store.list_provider_turns(task["id"])
        assert turns[0]["cacheUsage"] == {"cacheHit": True, "cachedTokens": 768}
    finally:
        store.close()


def test_runtime_status_includes_turn_cache_usage(tmp_path: Path) -> None:
    store = SQLiteStore(str(tmp_path / "runtime.sqlite3"))
    try:
        workspace = store.upsert_workspace(str(tmp_path / "workspace"))
        session = store.create_session(workspace_id=workspace["id"], title="cache")
        task = store.create_task(session_id=session["id"], task_type="edit", goal="cache", plan=[])
        turn = store.create_provider_turn(task_id=task["id"], session_id=session["id"], turn_index=0, model="gpt-5.4")
        store.complete_provider_turn(
            turn_id=turn["id"],
            finish_reason="stop",
            usage={
                "prompt_tokens_details": {"cached_tokens": 256},
            },
            tool_call_count=0,
            response_transport="stream",
        )
        rpc = _make_server(store)
        status = rpc._runtime_status({"taskId": task["id"]})
        assert status["provider"]["latestTurn"]["cacheUsage"] == {"cacheHit": True, "cachedTokens": 256}
    finally:
        store.close()
