from __future__ import annotations

import os
import sys

from .event_bus import EventBus
from .memory import MemoryManager, MemoryRetriever, MemoryStore
from .orchestrator.service import Orchestrator
from .policy.guard import PolicyGuard
from .provider.adapter import ProviderAdapter
from .router import MetaRouter
from .rpc.server import JsonRpcServer
from .services import CollaborationService, SubagentService
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
    collaboration = CollaborationService(store, event_bus)
    subagent_service = SubagentService(store, collaboration)
    provider = ProviderAdapter()
    meta_router = MetaRouter(provider=provider)
    memory_manager = MemoryManager(
        store=MemoryStore(store),
        retriever=MemoryRetriever(MemoryStore(store)),
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
        )
    )
    orchestrator = Orchestrator(
        store=store,
        event_bus=event_bus,
        tool_registry=tool_registry,
        provider=provider,
        meta_router=meta_router,
        memory_manager=memory_manager,
    )
    return JsonRpcServer(orchestrator=orchestrator, store=store, event_bus=event_bus)


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
