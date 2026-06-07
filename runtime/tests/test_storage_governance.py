from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from local_agent_runtime.event_bus import EventBus
from local_agent_runtime.memory import MemoryManager, MemoryRetriever
from local_agent_runtime.memory.store import MemoryStore
from local_agent_runtime.memory.types import MemoryKind
from local_agent_runtime.orchestrator.service import Orchestrator
from local_agent_runtime.provider.adapter import ProviderAdapter
from local_agent_runtime.provider.cache import LLMCache
from local_agent_runtime.rpc.server import JsonRpcServer
from local_agent_runtime.store.sqlite_store import SQLiteStore
from local_agent_runtime.tools.registry import ToolRegistry


def _make_runtime(tmp_path: Path) -> SimpleNamespace:
    event_bus = EventBus()
    store = SQLiteStore(str(tmp_path / "runtime.sqlite3"))
    memory_store = MemoryStore(store)
    orchestrator = Orchestrator(
        store=store,
        event_bus=event_bus,
        tool_registry=ToolRegistry({}),
        provider=ProviderAdapter(),
        memory_manager=MemoryManager(memory_store, MemoryRetriever(memory_store)),
    )
    server = JsonRpcServer(orchestrator=orchestrator, store=store, event_bus=event_bus)
    return SimpleNamespace(server=server, store=store, memory_store=memory_store)


def _rpc(runtime: SimpleNamespace, method: str, params: dict[str, Any]) -> dict[str, Any]:
    envelope = {
        "jsonrpc": "2.0",
        "id": f"req_{method}",
        "method": method,
        "params": params,
    }
    response = runtime.server.handle_line(json.dumps(envelope, ensure_ascii=False))
    assert "error" not in response, response.get("error")
    return response["result"]


def test_storage_config_migrates_legacy_trace_retention_without_overriding_custom_caps(tmp_path: Path) -> None:
    db_path = tmp_path / "config.sqlite3"
    store = SQLiteStore(str(db_path))
    store.update_config({"config": {"storage": {"retention": {"traceEventsMaxPerSession": 5000}}}})
    store.close()

    reopened = SQLiteStore(str(db_path))
    try:
        retention = reopened.get_config({})["config"]["storage"]["retention"]
        assert retention["traceEventsMaxPerSession"] == 50000

        reopened.update_config({"config": {"storage": {"retention": {"traceEventsMaxPerSession": 2}}}})
        assert reopened.get_config({})["config"]["storage"]["retention"]["traceEventsMaxPerSession"] == 2
    finally:
        reopened.close()


def test_session_delete_cascades_runtime_records_and_artifacts(tmp_path: Path) -> None:
    runtime = _make_runtime(tmp_path)
    store = runtime.store

    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    workspace = store.upsert_workspace(str(workspace_root))
    session = store.create_session(workspace["id"], "Cleanup me")
    task = store.create_task(
        session_id=session["id"],
        task_type="edit",
        goal="Generate runtime records",
        plan=[],
        status="completed",
    )
    store.create_message(session_id=session["id"], task_id=task["id"], role="user", content="hello")
    store.create_context_snapshot(session_id=session["id"], task_id=task["id"], memory_ids=["mem_demo"])
    turn = store.create_provider_turn(task_id=task["id"], session_id=session["id"], turn_index=1, model="gpt-5.4")
    store.complete_provider_turn(turn_id=turn["id"], finish_reason="stop", usage={"input_tokens": 10}, tool_call_count=0)
    command = store.create_command_log(task_id=task["id"], command="python -m pytest -q", cwd=str(workspace_root), shell="powershell")
    stdout_path = Path(store.write_command_artifact(command["id"], "stdout", "ok"))
    stderr_path = Path(store.write_command_artifact(command["id"], "stderr", "warn"))
    store.update_command_log(command["id"], status="completed", exit_code=0, stdout_path=str(stdout_path), stderr_path=str(stderr_path))
    runtime.memory_store.create(
        kind=MemoryKind.WORKING,
        content="Session memory",
        session_id=session["id"],
        workspace_id=workspace["id"],
    )
    runtime.memory_store.record_recall(
        session_id=session["id"],
        task_id=task["id"],
        query="pytest",
        memory_ids=["mem_demo"],
        scores={"mem_demo": 0.9},
    )

    assert stdout_path.exists()
    assert stderr_path.exists()
    assert store.get_task({"taskId": task["id"]})["task"]["id"] == task["id"]

    _rpc(runtime, "session.delete", {"sessionId": session["id"]})

    assert store._conn.execute("SELECT COUNT(*) FROM sessions WHERE id = ?", (session["id"],)).fetchone()[0] == 0  # noqa: SLF001
    assert store._conn.execute("SELECT COUNT(*) FROM tasks WHERE session_id = ?", (session["id"],)).fetchone()[0] == 0  # noqa: SLF001
    assert store._conn.execute("SELECT COUNT(*) FROM provider_turns WHERE session_id = ?", (session["id"],)).fetchone()[0] == 0  # noqa: SLF001
    assert store._conn.execute("SELECT COUNT(*) FROM context_snapshots WHERE session_id = ?", (session["id"],)).fetchone()[0] == 0  # noqa: SLF001
    assert store._conn.execute("SELECT COUNT(*) FROM command_logs WHERE task_id = ?", (task["id"],)).fetchone()[0] == 0  # noqa: SLF001
    assert store._conn.execute("SELECT COUNT(*) FROM memory_entries WHERE session_id = ?", (session["id"],)).fetchone()[0] == 0  # noqa: SLF001
    assert store._conn.execute("SELECT COUNT(*) FROM memory_recall_records WHERE session_id = ?", (session["id"],)).fetchone()[0] == 0  # noqa: SLF001
    assert not stdout_path.exists()
    assert not stderr_path.exists()


def test_storage_stats_and_cleanup_report_sizes_and_cache_cleanup(tmp_path: Path) -> None:
    runtime = _make_runtime(tmp_path)
    store = runtime.store

    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    workspace = store.upsert_workspace(str(workspace_root))
    session = store.create_session(workspace["id"], "Stats")
    task = store.create_task(session_id=session["id"], task_type="edit", goal="Inspect storage", plan=[], status="running")
    store.create_message(session_id=session["id"], task_id=task["id"], role="user", content="hi")
    store.create_context_snapshot(session_id=session["id"], task_id=task["id"], memory_ids=["mem_demo"])
    store.create_provider_turn(task_id=task["id"], session_id=session["id"], turn_index=1, model="gpt-5.4")
    command = store.create_command_log(task_id=task["id"], command="python -m pytest -q", cwd=str(workspace_root), shell="powershell")
    store.update_command_log(command["id"], status="completed", exit_code=0)

    cache = LLMCache(store)
    cache.put("expired", "data", ttl=0)
    cache.put("fresh", "data", ttl=3600)

    stats = _rpc(runtime, "storage.stats", {})
    assert stats["database"]["logicalBytes"] > 0
    assert stats["tables"]["sessions"] >= 1
    assert stats["tables"]["messages"] >= 1
    assert stats["tables"]["provider_turns"] >= 1
    assert stats["tables"]["context_snapshots"] >= 1
    assert stats["tables"]["command_logs"] >= 1

    cleanup = _rpc(runtime, "storage.cleanup", {"vacuum": False})
    assert cleanup["cleanup"]["expiredCacheEntriesRemoved"] >= 1
    assert cleanup["cleanup"]["vacuumRan"] is False
    assert cache.get("expired") is None
    assert cache.get("fresh") == "data"


def test_storage_cleanup_applies_retention_caps(tmp_path: Path) -> None:
    runtime = _make_runtime(tmp_path)
    store = runtime.store

    store.update_config(
        {
            "config": {
                "storage": {
                    "retention": {
                        "enabled": True,
                        "traceEventsMaxPerSession": 2,
                        "providerTurnsMaxPerTask": 2,
                        "contextSnapshotsMaxPerTask": 2,
                        "commandLogsMaxPerTask": 2,
                        "memoryRecallRecordsMaxPerSession": 2,
                        "artifactFilesMaxAgeDays": 365,
                    }
                }
            }
        }
    )

    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    workspace = store.upsert_workspace(str(workspace_root))
    session = store.create_session(workspace["id"], "Retention")
    task = store.create_task(session_id=session["id"], task_type="edit", goal="Trim old records", plan=[], status="running")

    for index in range(5):
        store.append_trace_event(
            task_id=task["id"],
            session_id=session["id"],
            event_type=f"task.note.{index}",
            source="test",
            payload={"index": index},
        )
        turn = store.create_provider_turn(
            task_id=task["id"],
            session_id=session["id"],
            turn_index=index,
            model="gpt-5.4",
        )
        store.complete_provider_turn(turn_id=turn["id"], finish_reason="stop", usage={"input_tokens": index + 1}, tool_call_count=0)
        store.create_context_snapshot(
            session_id=session["id"],
            task_id=task["id"],
            token_estimate=100 + index,
            memory_ids=[f"mem_{index}"],
        )
        command = store.create_command_log(
            task_id=task["id"],
            command=f"python -m pytest -q #{index}",
            cwd=str(workspace_root),
            shell="powershell",
        )
        stdout_path = store.write_command_artifact(command["id"], "stdout", f"out-{index}")
        stderr_path = store.write_command_artifact(command["id"], "stderr", f"err-{index}")
        store.update_command_log(command["id"], status="completed", exit_code=0, stdout_path=stdout_path, stderr_path=stderr_path)
        runtime.memory_store.record_recall(
            session_id=session["id"],
            task_id=task["id"],
            query=f"query-{index}",
            memory_ids=[f"mem_{index}"],
            scores={f"mem_{index}": 0.5},
        )

    cleanup = _rpc(runtime, "storage.cleanup", {"vacuum": False, "applyRetention": True})
    retention_removed = cleanup["cleanup"]["retentionRemoved"]

    assert retention_removed["traceEvents"] >= 3
    assert retention_removed["providerTurns"] >= 3
    assert retention_removed["contextSnapshots"] >= 3
    assert retention_removed["commandLogs"] >= 3
    assert retention_removed["memoryRecallRecords"] >= 3

    assert store._conn.execute("SELECT COUNT(*) FROM trace_events WHERE session_id = ?", (session["id"],)).fetchone()[0] == 2  # noqa: SLF001
    assert store._conn.execute("SELECT COUNT(*) FROM provider_turns WHERE task_id = ?", (task["id"],)).fetchone()[0] == 2  # noqa: SLF001
    assert store._conn.execute("SELECT COUNT(*) FROM context_snapshots WHERE task_id = ?", (task["id"],)).fetchone()[0] == 2  # noqa: SLF001
    assert store._conn.execute("SELECT COUNT(*) FROM command_logs WHERE task_id = ?", (task["id"],)).fetchone()[0] == 2  # noqa: SLF001
    assert store._conn.execute("SELECT COUNT(*) FROM memory_recall_records WHERE session_id = ?", (session["id"],)).fetchone()[0] == 2  # noqa: SLF001


def test_auto_storage_cleanup_runs_on_terminal_task_update(tmp_path: Path) -> None:
    runtime = _make_runtime(tmp_path)
    store = runtime.store

    store.update_config(
        {
            "config": {
                "storage": {
                    "retention": {
                        "enabled": True,
                        "traceEventsMaxPerSession": 2,
                        "providerTurnsMaxPerTask": 2,
                        "contextSnapshotsMaxPerTask": 2,
                        "commandLogsMaxPerTask": 2,
                        "memoryRecallRecordsMaxPerSession": 2,
                        "artifactFilesMaxAgeDays": 365,
                    }
                }
            }
        }
    )

    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    workspace = store.upsert_workspace(str(workspace_root))
    session = store.create_session(workspace["id"], "Auto cleanup")
    task = store.create_task(session_id=session["id"], task_type="edit", goal="Trigger auto cleanup", plan=[], status="running")

    for index in range(5):
        store.create_context_snapshot(
            session_id=session["id"],
            task_id=task["id"],
            token_estimate=200 + index,
            memory_ids=[f"mem_{index}"],
        )

    store._last_storage_cleanup_at = 0  # noqa: SLF001
    store.update_task(task["id"], status="completed", summary="done")

    assert store._conn.execute("SELECT COUNT(*) FROM context_snapshots WHERE task_id = ?", (task["id"],)).fetchone()[0] == 2  # noqa: SLF001
