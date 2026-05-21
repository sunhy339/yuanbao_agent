from __future__ import annotations

from pathlib import Path


def _rpc(runtime: object, method: str, params: dict) -> dict:
    return runtime.call(method, params)


def _result(response: dict, key: str):
    assert "error" not in response, response.get("error")
    return response["result"][key]


def test_workspace_memory_init_creates_canonical_files(runtime_harness, tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace = _result(_rpc(runtime_harness, "workspace.open", {"path": str(workspace_root)}), "workspace")

    result = _rpc(runtime_harness, "workspace.memory.init", {"workspaceId": workspace["id"]})
    payload = result["result"]

    assert sorted(payload["createdFiles"]) == ["MEMORY.local.md", "MEMORY.md", "YUANBAO.md"]
    assert payload["existingFiles"] == []
    assert (workspace_root / "YUANBAO.md").exists()
    assert (workspace_root / "MEMORY.md").exists()
    assert (workspace_root / "MEMORY.local.md").exists()


def test_workspace_memory_init_preserves_existing_files(runtime_harness, tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(parents=True, exist_ok=True)
    existing = workspace_root / "MEMORY.md"
    existing.write_text("# Existing memory\n", encoding="utf-8")

    workspace = _result(_rpc(runtime_harness, "workspace.open", {"path": str(workspace_root)}), "workspace")
    result = _rpc(runtime_harness, "workspace.memory.init", {"workspaceId": workspace["id"]})
    payload = result["result"]

    assert "MEMORY.md" in payload["existingFiles"]
    assert existing.read_text(encoding="utf-8") == "# Existing memory\n"
    assert (workspace_root / "YUANBAO.md").exists()
    assert (workspace_root / "MEMORY.local.md").exists()
