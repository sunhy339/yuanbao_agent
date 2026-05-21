from __future__ import annotations

from pathlib import Path


def _rpc(runtime: object, method: str, params: dict) -> dict:
    return runtime.call(method, params)


def _result(response: dict, key: str):
    assert "error" not in response, response.get("error")
    return response["result"][key]


def test_workspace_memory_init_creates_canonical_files(runtime_harness, tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    (workspace_root / "README.md").parent.mkdir(parents=True, exist_ok=True)
    (workspace_root / "README.md").write_text(
        "# Demo App\n\nA small demo workspace used to validate init memory generation.\n",
        encoding="utf-8",
    )
    (workspace_root / "package.json").write_text(
        '{"scripts":{"test":"vitest run","build":"vite build"}}',
        encoding="utf-8",
    )
    workspace = _result(_rpc(runtime_harness, "workspace.open", {"path": str(workspace_root)}), "workspace")

    result = _rpc(runtime_harness, "workspace.memory.init", {"workspaceId": workspace["id"]})
    payload = result["result"]

    assert sorted(payload["createdFiles"]) == ["MEMORY.local.md", "MEMORY.md", "YUANBAO.md"]
    assert payload["existingFiles"] == []
    assert (workspace_root / "YUANBAO.md").exists()
    assert (workspace_root / "MEMORY.md").exists()
    assert (workspace_root / "MEMORY.local.md").exists()
    yuanbao_text = (workspace_root / "YUANBAO.md").read_text(encoding="utf-8")
    memory_text = (workspace_root / "MEMORY.md").read_text(encoding="utf-8")
    assert "Demo App" in yuanbao_text
    assert "A small demo workspace used to validate init memory generation." in yuanbao_text
    assert "`npm run test`" in yuanbao_text
    assert "vitest run" in memory_text


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


def test_workspace_memory_init_uses_python_workspace_signals(runtime_harness, tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(parents=True, exist_ok=True)
    (workspace_root / "pyproject.toml").write_text(
        """
[project]
name = "incident-engine"

[tool.pytest.ini_options]
testpaths = ["tests"]

[tool.ruff]
line-length = 100
""".strip(),
        encoding="utf-8",
    )
    (workspace_root / "tests").mkdir()

    workspace = _result(_rpc(runtime_harness, "workspace.open", {"path": str(workspace_root)}), "workspace")
    _rpc(runtime_harness, "workspace.memory.init", {"workspaceId": workspace["id"]})

    yuanbao_text = (workspace_root / "YUANBAO.md").read_text(encoding="utf-8")
    memory_text = (workspace_root / "MEMORY.md").read_text(encoding="utf-8")
    assert "incident-engine" in yuanbao_text
    assert "python -m pytest -q" in yuanbao_text
    assert "Repository appears to use pytest for verification." in memory_text
    assert "Ruff configuration detected." in memory_text
