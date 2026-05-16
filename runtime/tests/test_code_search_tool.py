from __future__ import annotations

from pathlib import Path

import pytest

from local_agent_runtime.policy.guard import PolicyGuard
from local_agent_runtime.store.sqlite_store import SQLiteStore
from local_agent_runtime.tools import build_builtin_tools


def _make_code_search(tmp_path: Path):
    store = SQLiteStore(str(tmp_path / "runtime.sqlite3"))
    config = store.get_config({})["config"]
    policy_guard = PolicyGuard(approval_mode=config["policy"]["approvalMode"])
    tools = build_builtin_tools(policy_guard=policy_guard, store=store)
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    return store, tools["code_search"], workspace_root


def test_code_search_scopes_path_and_finds_definitions(tmp_path: Path) -> None:
    store, code_search, workspace_root = _make_code_search(tmp_path)
    try:
        src = workspace_root / "src"
        src.mkdir()
        (src / "cart.py").write_text(
            "def calculate_total(items):\n"
            "    return sum(item['price'] for item in items)\n",
            encoding="utf-8",
        )
        (workspace_root / "outside.py").write_text("def calculate_total():\n    return 0\n", encoding="utf-8")

        result = code_search({
            "workspaceRoot": str(workspace_root),
            "path": "src",
            "query": "calculate_total",
            "mode": "definition",
            "glob": ["**/*.py"],
        })

        assert result["totalMatches"] == 1
        assert result["results"][0]["path"] == "src/cart.py"
        assert result["results"][0]["line"] == 1
    finally:
        store.close()


def test_code_search_rejects_paths_outside_workspace(tmp_path: Path) -> None:
    store, code_search, workspace_root = _make_code_search(tmp_path)
    try:
        with pytest.raises(ValueError, match="escapes workspace"):
            code_search({
                "workspaceRoot": str(workspace_root),
                "path": "..",
                "query": "anything",
            })
    finally:
        store.close()
