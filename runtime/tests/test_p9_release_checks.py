"""Tests for P9.3 release checks: event compat, MCP migration, feature flags."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from local_agent_runtime.event_bus import EventBus
from local_agent_runtime.models import RuntimeEvent
from local_agent_runtime.orchestrator.service import Orchestrator
from local_agent_runtime.rpc.server import JsonRpcServer
from local_agent_runtime.store.sqlite_store import SQLiteStore
from local_agent_runtime.tools.registry import ToolRegistry


# ── helpers ────────────────────────────────────────────────────────────────


def _make_runtime(tmp_path: Any) -> SimpleNamespace:
    event_bus = EventBus()
    store = SQLiteStore(str(tmp_path / "runtime.sqlite3"))
    tool_registry = ToolRegistry()

    class DummyProvider:
        def generate(self, prompt: str, context: dict[str, Any]) -> dict[str, Any]:
            return {"text": "ok", "status": "done"}

    orchestrator = Orchestrator(
        store=store, event_bus=event_bus,
        tool_registry=tool_registry, provider=DummyProvider(),
    )
    server = JsonRpcServer(orchestrator=orchestrator, store=store, event_bus=event_bus)
    return SimpleNamespace(server=server, store=store, orchestrator=orchestrator, event_bus=event_bus)


def _rpc(runtime: SimpleNamespace, method: str, params: dict[str, Any]) -> dict[str, Any]:
    request_id = f"req_{method}"
    envelope = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
    return runtime.server.handle_line(json.dumps(envelope, ensure_ascii=False))


# ── 1. Event compatibility: assistant.token also emits message.delta ────────


class TestEventCompatAssistantToken:
    """assistant.token events must also emit message.delta for backward compat."""

    def test_assistant_token_emits_message_delta(self, tmp_path: Any) -> None:
        """When assistant.token is published, a message.delta event is also emitted."""
        runtime = _make_runtime(tmp_path)
        collected: list[RuntimeEvent] = []
        runtime.event_bus.subscribe(collected.append)

        task = {"id": "t1", "role": "root", "activeAssistantMessageId": "msg_1"}
        runtime.orchestrator._publish(
            session_id="s1",
            task=task,
            event_type="assistant.token",
            payload={"text": "hello"},
        )

        types = [e.type for e in collected]
        assert "assistant.token" in types, f"assistant.token not found in {types}"
        assert "message.delta" in types, f"message.delta not found in {types}"

    def test_message_delta_contains_message_id(self, tmp_path: Any) -> None:
        """message.delta event must include messageId from active assistant message."""
        runtime = _make_runtime(tmp_path)
        collected: list[RuntimeEvent] = []
        runtime.event_bus.subscribe(collected.append)

        task = {"id": "t1", "role": "root", "activeAssistantMessageId": "msg_42"}
        runtime.orchestrator._publish(
            session_id="s1",
            task=task,
            event_type="assistant.token",
            payload={"text": "world"},
        )

        delta_events = [e for e in collected if e.type == "message.delta"]
        assert len(delta_events) == 1
        assert delta_events[0].payload["messageId"] == "msg_42"

    def test_non_token_events_no_extra_delta(self, tmp_path: Any) -> None:
        """Non-assistant.token events should NOT emit an extra message.delta."""
        runtime = _make_runtime(tmp_path)
        collected: list[RuntimeEvent] = []
        runtime.event_bus.subscribe(collected.append)

        task = {"id": "t1", "role": "root", "activeAssistantMessageId": "msg_1"}
        runtime.orchestrator._publish(
            session_id="s1",
            task=task,
            event_type="task.completed",
            payload={"status": "completed"},
        )

        types = [e.type for e in collected]
        assert "message.delta" not in types


# ── 2. MCP server migration ──────────────────────────────────────────────


class TestMcpServerMigration:
    """Ensure mcp_servers table has all required columns after migration."""

    def test_mcp_server_columns_exist(self, tmp_path: Any) -> None:
        """All expected columns exist in mcp_servers after init."""
        store = SQLiteStore(str(tmp_path / "test.sqlite3"))
        columns = {
            row["name"]
            for row in store._conn.execute("PRAGMA table_info(mcp_servers)").fetchall()
        }
        for col in ("id", "name", "transport", "command", "args", "url",
                     "headers", "env", "enabled", "created_at", "updated_at"):
            assert col in columns, f"Column {col} missing from mcp_servers"

    def test_mcp_server_defaults(self, tmp_path: Any) -> None:
        """New MCP server gets proper defaults for JSON fields."""
        store = SQLiteStore(str(tmp_path / "test.sqlite3"))
        result = store.create_mcp_server({
            "name": "test-server",
            "transport": "stdio",
            "command": "npx",
            "enabled": False,
        })
        server = result["server"]
        assert server["args"] == []
        assert server["env"] == {}
        assert server["headers"] == {}
        assert server["transport"] == "stdio"
        assert server["enabled"] in (False, 0)

    def test_mcp_server_round_trip_preserves_all_fields(self, tmp_path: Any) -> None:
        """Create and retrieve preserves all config fields."""
        store = SQLiteStore(str(tmp_path / "test.sqlite3"))
        created = store.create_mcp_server({
            "name": "full-server",
            "transport": "stdio",
            "command": "python",
            "args": ["-m", "server"],
            "env": {"KEY": "val"},
            "headers": {"Authorization": "Bearer x"},
            "enabled": True,
        })
        server_id = created["server"]["id"]

        retrieved = store.get_mcp_server({"serverId": server_id})
        s = retrieved["server"]
        assert s["name"] == "full-server"
        assert s["command"] == "python"
        assert s["args"] == ["-m", "server"]
        assert s["env"] == {"KEY": "val"}
        assert s["headers"] == {"Authorization": "Bearer x"}
        assert s["enabled"] in (True, 1)


# ── 3. Feature flags ──────────────────────────────────────────────────────


class TestFeatureFlags:
    """Feature flag CRUD via SQLiteStore."""

    def test_default_features_in_config(self, tmp_path: Any) -> None:
        """Default config includes features block with multiAgent=False."""
        store = SQLiteStore(str(tmp_path / "test.sqlite3"))
        config = store.get_config({})
        features = config["config"].get("features", {})
        assert "multiAgent" in features
        assert features["multiAgent"] is False

    def test_get_feature_flag(self, tmp_path: Any) -> None:
        """get_feature_flag reads from config.features."""
        store = SQLiteStore(str(tmp_path / "test.sqlite3"))
        assert store.get_feature_flag("multiAgent") is False
        assert store.get_feature_flag("nonexistent", default=True) is True

    def test_set_feature_flag(self, tmp_path: Any) -> None:
        """set_feature_flag persists and can be read back."""
        store = SQLiteStore(str(tmp_path / "test.sqlite3"))
        result = store.set_feature_flag("multiAgent", True)
        assert result["features"]["multiAgent"] is True
        # Read back
        assert store.get_feature_flag("multiAgent") is True

    def test_list_feature_flags(self, tmp_path: Any) -> None:
        """list_feature_flags returns all flags."""
        store = SQLiteStore(str(tmp_path / "test.sqlite3"))
        flags = store.list_feature_flags()
        assert "multiAgent" in flags["features"]
        assert "streamingDeltaPersist" in flags["features"]

    def test_feature_flag_persists_across_store_instances(self, tmp_path: Any) -> None:
        """Feature flag survives store re-initialization."""
        db_path = str(tmp_path / "test.sqlite3")
        store1 = SQLiteStore(db_path)
        store1.set_feature_flag("multiAgent", True)

        store2 = SQLiteStore(db_path)
        assert store2.get_feature_flag("multiAgent") is True


class TestFeatureFlagsRpc:
    """Feature flag access via RPC."""

    def test_feature_list_rpc(self, tmp_path: Any) -> None:
        runtime = _make_runtime(tmp_path)
        resp = _rpc(runtime, "feature.list", {})
        assert "result" in resp
        assert "multiAgent" in resp["result"]["features"]

    def test_feature_set_rpc(self, tmp_path: Any) -> None:
        runtime = _make_runtime(tmp_path)
        resp = _rpc(runtime, "feature.set", {"key": "multiAgent", "value": True})
        assert "result" in resp
        assert resp["result"]["features"]["multiAgent"] is True

    def test_feature_set_missing_key_raises(self, tmp_path: Any) -> None:
        runtime = _make_runtime(tmp_path)
        resp = _rpc(runtime, "feature.set", {"value": True})
        assert "error" in resp


class TestMultiAgentFeatureGate:
    """run_child_task is blocked when multiAgent feature is disabled."""

    def test_child_task_blocked_when_disabled(self, tmp_path: Any) -> None:
        """run_child_task raises when multiAgent is False (default)."""
        runtime = _make_runtime(tmp_path)
        with pytest.raises(ValueError, match="multiAgent"):
            runtime.orchestrator.run_child_task({
                "sessionId": "s_fake",
                "prompt": "do something",
            })

    def test_child_task_allowed_when_enabled(self, tmp_path: Any) -> None:
        """run_child_task proceeds past feature gate when multiAgent is enabled."""
        runtime = _make_runtime(tmp_path)
        runtime.store.set_feature_flag("multiAgent", True)
        # Should not raise ValueError about multiAgent
        # (will likely fail for other reasons like missing session, but that's fine)
        try:
            runtime.orchestrator.run_child_task({
                "sessionId": "s_fake",
                "prompt": "do something",
            })
        except ValueError as e:
            assert "multiAgent" not in str(e)
