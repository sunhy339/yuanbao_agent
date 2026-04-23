from __future__ import annotations

import os
import sys

from .event_bus import EventBus
from .orchestrator.service import Orchestrator
from .policy.guard import PolicyGuard
from .provider.adapter import ProviderAdapter
from .rpc.server import JsonRpcServer
from .services import CollaborationService, SubagentService
from .store.sqlite_store import SQLiteStore
from .tools.builtin import build_builtin_tools
from .tools.registry import ToolRegistry


def build_server(database_path: str = ":memory:") -> JsonRpcServer:
    event_bus = EventBus()
    store = SQLiteStore(database_path)
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


def main() -> int:
    server = build_server(database_path=os.environ.get("LOCAL_AGENT_DB_PATH", ":memory:"))
    server.serve(stdin=sys.stdin, stdout=sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
