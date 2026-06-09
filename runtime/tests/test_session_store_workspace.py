from __future__ import annotations

from pathlib import Path

from local_agent_runtime.store.sqlite_store import SQLiteStore


def test_upsert_workspace_creates_missing_root(tmp_path: Path) -> None:
    store = SQLiteStore(str(tmp_path / "runtime.sqlite3"))
    try:
        workspace_root = tmp_path / "generated-audio-tuner" / "workspace"

        workspace = store.upsert_workspace(str(workspace_root))

        assert workspace_root.is_dir()
        assert Path(workspace["rootPath"]) == workspace_root.resolve()
    finally:
        store.close()
