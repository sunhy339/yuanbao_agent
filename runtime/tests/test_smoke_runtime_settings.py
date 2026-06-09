from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType


def _load_long_smoke_module() -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "smoke_runs" / "long_running_fullstack_smoke.py"
    spec = importlib.util.spec_from_file_location("long_running_fullstack_smoke_for_tests", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_smoke_runtime_settings_can_shorten_child_timeout(monkeypatch) -> None:
    module = _load_long_smoke_module()
    monkeypatch.setenv("YUANBAO_SMOKE_CHILD_TIMEOUT_MS", "120000")
    monkeypatch.setenv("YUANBAO_SMOKE_COMMAND_TIMEOUT_MS", "45000")
    monkeypatch.setenv("YUANBAO_SMOKE_MAX_TASK_STEPS", "18")
    monkeypatch.setenv("YUANBAO_SMOKE_MAX_PARALLEL_SUBTASKS", "2")
    monkeypatch.setenv("YUANBAO_SMOKE_COMPACTION_THRESHOLD", "800")
    monkeypatch.setenv("YUANBAO_SMOKE_MAX_CONTEXT_TOKENS", "20000")

    settings = module.smoke_runtime_settings()

    assert settings["childTimeoutMs"] == 120000
    assert settings["commandTimeoutMs"] == 45000
    assert settings["maxSteps"] == 18
    assert settings["maxParallelSubtasks"] == 2
    assert settings["compactionThreshold"] == 800
    assert settings["maxContextTokens"] == 20000
    assert "routingStrategyTimeoutSeconds" not in settings


def test_build_runtime_applies_smoke_timeout_settings(tmp_path, monkeypatch) -> None:
    module = _load_long_smoke_module()
    monkeypatch.setenv("YUANBAO_SMOKE_CHILD_TIMEOUT_MS", "90000")
    monkeypatch.setenv("YUANBAO_SMOKE_COMMAND_TIMEOUT_MS", "30000")
    monkeypatch.setenv("YUANBAO_SMOKE_MAX_TASK_STEPS", "12")
    monkeypatch.setenv("YUANBAO_SMOKE_MAX_CONTEXT_TOKENS", "20000")
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    _server, store, _events, _memory = module.build_runtime(
        tmp_path / "runtime.sqlite3",
        workspace,
        {"mode": "mock", "model": "mock"},
    )
    try:
        config = store.get_config({})["config"]
    finally:
        store.close()

    profile = config["autonomy"]["profiles"][0]
    assert profile["timeoutMs"] == 90000
    assert profile["childTaskTimeoutMs"] == 90000
    assert profile["maxSteps"] == 12
    assert profile["compactionThreshold"] == 20000
    assert config["provider"]["maxContextTokens"] == 20000
    assert config["provider"]["streamingEnabled"] is True
    assert config["maxContextTokens"] == 20000
    assert config["policy"]["childTaskTimeoutMs"] == 90000
    assert config["policy"]["commandTimeoutMs"] == 30000
    assert config["policy"]["maxTaskSteps"] == 12


def test_smoke_runtime_settings_default_compaction_threshold_matches_context_limit(monkeypatch) -> None:
    module = _load_long_smoke_module()
    monkeypatch.delenv("YUANBAO_SMOKE_COMPACTION_THRESHOLD", raising=False)
    monkeypatch.setenv("YUANBAO_SMOKE_MAX_CONTEXT_TOKENS", "20000")

    settings = module.smoke_runtime_settings()

    assert settings["maxContextTokens"] == 20000
    assert settings["compactionThreshold"] == 20000
