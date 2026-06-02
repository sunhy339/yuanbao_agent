from __future__ import annotations

from pathlib import Path
from typing import Any

from local_agent_runtime.event_bus import EventBus
from local_agent_runtime.rpc.server import JsonRpcServer
from local_agent_runtime.store.sqlite_store import SQLiteStore


class _Orchestrator:
    def __init__(self) -> None:
        self.shutdown_calls: list[float] = []

    def graceful_shutdown(self, timeout: float = 10.0) -> None:
        self.shutdown_calls.append(timeout)

    def __getattr__(self, _name: str) -> Any:
        return lambda _params=None: {}


def test_graceful_shutdown_runs_resource_callbacks_once(tmp_path: Path) -> None:
    store = SQLiteStore(str(tmp_path / "runtime.sqlite3"))
    orchestrator = _Orchestrator()
    callback_calls: list[str] = []
    server = JsonRpcServer(
        orchestrator=orchestrator,
        store=store,
        event_bus=EventBus(),
        shutdown_callbacks=[lambda: callback_calls.append("closed")],
    )

    try:
        server.graceful_shutdown(timeout=0.1)
        server.graceful_shutdown(timeout=0.2)
    finally:
        store.close()

    assert orchestrator.shutdown_calls == [0.1, 0.2]
    assert callback_calls == ["closed"]
