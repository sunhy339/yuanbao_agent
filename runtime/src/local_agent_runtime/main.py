from __future__ import annotations

import os
import sys

from .event_bus import EventBus
from .git.worktree_adapter import GitWorktreeAdapter
from .memory import MemoryManager, MemoryRetriever, MemoryStore
from .orchestrator.service import Orchestrator
from .policy.decision_advisor import DecisionAdvisor
from .policy.guard import PolicyGuard
from .policy.permission_engine import PermissionEngine
from .provider.adapter import ProviderAdapter
from .router import MetaRouter
from .rpc.server import JsonRpcServer
from .services import CollaborationService, SubagentService
from .services.hook_service import HookService
from .services.worktree_service import WorktreeService
from .store.sqlite_store import SQLiteStore
from .tools import build_builtin_tools
from .tools.registry import ToolRegistry


def _configure_stdio() -> None:
    for stream_name in ("stdin", "stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace", write_through=True)


def build_server(database_path: str = ":memory:") -> JsonRpcServer:
    event_bus = EventBus()
    store = SQLiteStore(database_path)
    config = store.get_config({})["config"]
    policy_guard = PolicyGuard(approval_mode=config["policy"]["approvalMode"])
    permission_engine = PermissionEngine(config=config, store=store)
    collaboration = CollaborationService(store, event_bus)
    subagent_service = SubagentService(store, collaboration)
    provider = ProviderAdapter()
    decision_advisor = DecisionAdvisor(provider=provider)
    meta_router = MetaRouter(provider=provider, decision_advisor=decision_advisor)
    memory_store = MemoryStore(store)
    memory_manager = MemoryManager(
        store=memory_store,
        retriever=MemoryRetriever(memory_store),
    )
    from .context.scratchpad import Scratchpad
    scratchpad = Scratchpad(store)
    tool_registry = ToolRegistry(
        build_builtin_tools(
            policy_guard=policy_guard,
            store=store,
            subagent_service=subagent_service,
            memory_manager=memory_manager,
            scratchpad=scratchpad,
            permission_engine=permission_engine,
        )
    )
    hook_service = HookService(
        store,
        event_bus,
        permission_engine=permission_engine,
        memory_store=memory_store,
        refresh_permission_engine=True,
    )
    # WorktreeService requires a git repo root; only create when env var is set.
    worktree_service = None
    repo_root = os.environ.get("LOCAL_AGENT_REPO_ROOT")
    if repo_root:
        worktree_service = WorktreeService(
            store,
            GitWorktreeAdapter(repo_root),
            hook_service=hook_service,
            policy_guard=policy_guard,
            permission_engine=permission_engine,
        )
    orchestrator = Orchestrator(
        store=store,
        event_bus=event_bus,
        tool_registry=tool_registry,
        provider=provider,
        meta_router=meta_router,
        memory_manager=memory_manager,
        hook_service=hook_service,
        decision_advisor=decision_advisor,
        worktree_service=worktree_service,
        _skip_orphan_cleanup=os.environ.get("LOCAL_AGENT_CHILD_WORKER") == "1",
    )
    return JsonRpcServer(
        orchestrator=orchestrator,
        store=store,
        event_bus=event_bus,
        worktree_service=worktree_service,
    )


def main() -> int:
    _configure_stdio()
    server = build_server(database_path=os.environ.get("LOCAL_AGENT_DB_PATH", ":memory:"))
    server.initialize_mcp_servers()
    try:
        server.serve(stdin=sys.stdin, stdout=sys.stdout)
    finally:
        server.graceful_shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
