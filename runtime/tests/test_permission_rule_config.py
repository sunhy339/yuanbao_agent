from __future__ import annotations

from typing import Any

from local_agent_runtime.store.sqlite_store import SQLiteStore


def test_clear_permission_rule_removes_single_capability_and_persists(tmp_path: Any) -> None:
    database_path = str(tmp_path / "test.sqlite3")
    store = SQLiteStore(database_path)
    try:
        store.update_config(
            {
                "config": {
                    "permissions": {
                        "preset": "balanced",
                        "capabilities": {
                            "runCommand": {"mode": "allow", "scope": "*"},
                            "writeFile": {"mode": "allow", "scope": "*"},
                        },
                    },
                },
            }
        )

        result = store.clear_permission_rule({"capability": "runCommand"})

        assert result["removed"] is True
        assert result["capability"] == "runCommand"
        assert "runCommand" not in result["config"]["permissions"]["capabilities"]
        assert result["config"]["permissions"]["capabilities"]["writeFile"]["mode"] == "allow"
        store.close()

        reopened = SQLiteStore(database_path)
        try:
            config = reopened.get_config({})["config"]
            assert "runCommand" not in config["permissions"]["capabilities"]
            assert config["permissions"]["capabilities"]["writeFile"]["mode"] == "allow"
        finally:
            reopened.close()
    finally:
        try:
            store.close()
        except Exception:
            pass


def test_clear_permission_rule_reports_missing_rule_without_touching_preset(tmp_path: Any) -> None:
    store = SQLiteStore(str(tmp_path / "test.sqlite3"))
    try:
        store.update_config({"config": {"permissions": {"preset": "autonomous", "capabilities": {}}}})

        result = store.clear_permission_rule({"capability": "webFetch"})

        assert result["removed"] is False
        assert result["config"]["permissions"]["preset"] == "autonomous"
        assert result["config"]["permissions"]["capabilities"] == {}
    finally:
        store.close()


def test_update_config_replaces_permission_capabilities_when_explicit(tmp_path: Any) -> None:
    store = SQLiteStore(str(tmp_path / "test.sqlite3"))
    try:
        store.update_config(
            {
                "config": {
                    "permissions": {
                        "preset": "balanced",
                        "capabilities": {
                            "runCommand": {"mode": "ask", "scope": "*"},
                            "writeFile": {"mode": "ask", "scope": "*"},
                        },
                    },
                },
            }
        )

        result = store.update_config(
            {
                "config": {
                    "permissions": {
                        "preset": "autonomous",
                        "capabilities": {
                            "writeFile": {"mode": "allow", "scope": "*"},
                        },
                    },
                },
            }
        )

        assert result["config"]["permissions"]["preset"] == "autonomous"
        assert result["config"]["permissions"]["capabilities"] == {
            "writeFile": {"mode": "allow", "scope": "*"},
        }
    finally:
        store.close()
