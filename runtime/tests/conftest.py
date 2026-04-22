from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from local_agent_runtime.event_bus import EventBus
from local_agent_runtime.orchestrator.service import Orchestrator
from local_agent_runtime.provider.adapter import ProviderAdapter
from local_agent_runtime.rpc.server import JsonRpcServer
from local_agent_runtime.store.sqlite_store import SQLiteStore
from local_agent_runtime.tools.builtin import build_builtin_tools
from local_agent_runtime.tools.registry import ToolRegistry
from local_agent_runtime.policy.guard import PolicyGuard


class RuntimeHarness:
    def __init__(self, server: JsonRpcServer, store: SQLiteStore, events: list[dict[str, Any]]) -> None:
        self.server = server
        self.store = store
        self.events = events
        self._counter = 0

    def call(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self._counter += 1
        envelope = {
            "jsonrpc": "2.0",
            "id": f"req_{self._counter}",
            "method": method,
            "params": params,
        }
        response = self.server.handle_line(json.dumps(envelope, ensure_ascii=False))
        assert response["jsonrpc"] == "2.0"
        assert response["id"] == envelope["id"]
        return response


@pytest.fixture
def runtime_harness(tmp_path: Path) -> Iterator[RuntimeHarness]:
    db_path = tmp_path / "runtime.sqlite3"
    event_bus = EventBus()
    store = SQLiteStore(str(db_path))
    config = store.get_config({})["config"]
    policy_guard = PolicyGuard(approval_mode=config["policy"]["approvalMode"])
    tool_registry = ToolRegistry(build_builtin_tools(policy_guard=policy_guard, store=store))
    provider = ProviderAdapter()
    orchestrator = Orchestrator(
        store=store,
        event_bus=event_bus,
        tool_registry=tool_registry,
        provider=provider,
    )
    server = JsonRpcServer(orchestrator=orchestrator, store=store, event_bus=event_bus)

    events: list[dict[str, Any]] = []
    event_bus.subscribe(lambda event: events.append(event_bus.as_payload(event)))

    harness = RuntimeHarness(server=server, store=store, events=events)
    try:
        yield harness
    finally:
        store.close()
