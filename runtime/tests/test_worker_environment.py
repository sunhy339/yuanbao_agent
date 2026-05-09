from __future__ import annotations

import os
from pathlib import Path

import pytest

from local_agent_runtime.services.worker_environment import (
    TOOL_ALIAS_MAP,
    build_child_worker_env,
    normalize_child_tool_allowlist,
    resolve_tool_alias,
)


def test_build_child_worker_env_keeps_only_runtime_provider_and_platform_vars(tmp_path: Path) -> None:
    runtime_src = tmp_path / "runtime" / "src"
    db_path = tmp_path / "runtime.sqlite3"
    parent_env = {
        "LOCAL_AGENT_PROVIDER_API_KEY": "sk-local-agent",
        "LOCAL_AGENT_PROVIDER_MODEL": "gpt-test",
        "LOCAL_AGENT_OPENAI_API_KEY": "sk-local-openai",
        "LOCAL_AGENT_UNRELATED_SECRET": "do-not-copy-local-secret",
        "OPENAI_API_KEY": "sk-openai",
        "ANTHROPIC_API_KEY": "sk-anthropic",
        "SECRET_TOKEN": "do-not-copy",
        "HOME": "/home/user",
        "PYTHONPATH": "parent-pythonpath",
        "PYTHONUNBUFFERED": "0",
    }
    if os.name == "nt":
        parent_env.update(
            {
                "PATH": "C:\\Python;C:\\Windows\\System32",
                "SystemRoot": "C:\\Windows",
                "TEMP": "C:\\Temp",
                "TMP": "C:\\Tmp",
                "COMSPEC": "C:\\Windows\\System32\\cmd.exe",
            }
        )
    else:
        parent_env["PATH"] = "/usr/bin"

    env = build_child_worker_env(
        parent_env=parent_env,
        db_path=db_path,
        runtime_src=runtime_src,
    )

    assert env["LOCAL_AGENT_PROVIDER_API_KEY"] == "sk-local-agent"
    assert env["LOCAL_AGENT_PROVIDER_MODEL"] == "gpt-test"
    assert env["LOCAL_AGENT_OPENAI_API_KEY"] == "sk-local-openai"
    assert env["OPENAI_API_KEY"] == "sk-openai"
    assert env["ANTHROPIC_API_KEY"] == "sk-anthropic"
    assert env["LOCAL_AGENT_DB_PATH"] == str(db_path)
    assert env["PYTHONUNBUFFERED"] == "1"
    assert env["PYTHONPATH"] == os.pathsep.join([str(runtime_src), "parent-pythonpath"])
    assert "SECRET_TOKEN" not in env
    assert "LOCAL_AGENT_UNRELATED_SECRET" not in env
    assert "HOME" not in env
    assert "COMSPEC" not in env

    if os.name == "nt":
        assert env["PATH"] == "C:\\Python;C:\\Windows\\System32"
        assert env["SystemRoot"] == "C:\\Windows"
        assert env["TEMP"] == "C:\\Temp"
        assert env["TMP"] == "C:\\Tmp"
    else:
        assert "PATH" not in env


def test_build_child_worker_env_honors_extra_env_allowlist_without_empty_values(tmp_path: Path) -> None:
    env = build_child_worker_env(
        parent_env={
            "CUSTOM_CA_BUNDLE": "D:\\certs\\ca.pem",
            "EMPTY_VALUE": "",
            "PATH": "ignored-on-non-windows",
        },
        db_path=str(tmp_path / "runtime.sqlite3"),
        runtime_src=str(tmp_path / "src"),
        allowlist=["CUSTOM_CA_BUNDLE", "EMPTY_VALUE", "MISSING_VALUE"],
    )

    assert env["CUSTOM_CA_BUNDLE"] == "D:\\certs\\ca.pem"
    assert "EMPTY_VALUE" not in env
    assert "MISSING_VALUE" not in env


def test_build_child_worker_env_rejects_missing_db_path(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="db_path is required"):
        build_child_worker_env(parent_env={}, db_path="", runtime_src=tmp_path)


def test_normalize_child_tool_allowlist_defaults_to_read_only_tools() -> None:
    assert normalize_child_tool_allowlist(None) == (
        "list_dir",
        "search_files",
        "read_file",
        "git_status",
        "git_diff",
        "code_search",
        "web_fetch",
        "browser",
    )


def test_normalize_child_tool_allowlist_dedupes_and_requires_known_explicit_tools() -> None:
    assert normalize_child_tool_allowlist([" read_file ", "git_status", "read_file"]) == (
        "read_file",
        "git_status",
    )
    assert normalize_child_tool_allowlist(["read_file", "run_command", "apply_patch"]) == (
        "read_file",
        "run_command",
        "apply_patch",
    )

    with pytest.raises(ValueError, match="not allowed for child workers: task"):
        normalize_child_tool_allowlist(["read_file", "task"])


# --- Tool Alias Normalization tests (P1 subagent-generation-todolist) ---


class TestToolAliasNormalization:
    """Alias resolution in normalize_child_tool_allowlist."""

    def test_rg_resolves_to_search_files(self):
        assert normalize_child_tool_allowlist(["rg"]) == ("search_files",)

    def test_grep_resolves_to_search_files(self):
        assert normalize_child_tool_allowlist(["grep"]) == ("search_files",)

    def test_search_resolves_to_search_files(self):
        assert normalize_child_tool_allowlist(["search"]) == ("search_files",)

    def test_cat_resolves_to_read_file(self):
        assert normalize_child_tool_allowlist(["cat"]) == ("read_file",)

    def test_read_resolves_to_read_file(self):
        assert normalize_child_tool_allowlist(["read"]) == ("read_file",)

    def test_git_status_alias(self):
        assert normalize_child_tool_allowlist(["git status"]) == ("git_status",)

    def test_status_alias(self):
        assert normalize_child_tool_allowlist(["status"]) == ("git_status",)

    def test_git_diff_alias(self):
        assert normalize_child_tool_allowlist(["git diff"]) == ("git_diff",)

    def test_diff_alias(self):
        assert normalize_child_tool_allowlist(["diff"]) == ("git_diff",)

    def test_shell_alias(self):
        assert normalize_child_tool_allowlist(["shell"]) == ("run_command",)

    def test_command_alias(self):
        assert normalize_child_tool_allowlist(["command"]) == ("run_command",)

    def test_patch_alias(self):
        assert normalize_child_tool_allowlist(["patch"]) == ("apply_patch",)

    def test_duplicate_aliases_collapse(self):
        # rg and grep both resolve to search_files, should dedupe
        assert normalize_child_tool_allowlist(["rg", "grep", "search_files"]) == (
            "search_files",
        )

    def test_string_input_with_aliases(self):
        result = normalize_child_tool_allowlist("rg,cat,shell")
        assert result == ("search_files", "read_file", "run_command")

    def test_unsafe_alias_task_still_blocked(self):
        # 'task' is not in the alias map, so it hits the unsafe check
        with pytest.raises(ValueError, match="not allowed for child workers: task"):
            normalize_child_tool_allowlist(["task"])

    def test_unknown_tool_still_rejected(self):
        with pytest.raises(ValueError, match="not allowed for child workers"):
            normalize_child_tool_allowlist(["completely_unknown_tool"])

    def test_resolve_tool_alias_passthrough(self):
        # canonical names pass through unchanged
        assert resolve_tool_alias("search_files") == "search_files"
        assert resolve_tool_alias("read_file") == "read_file"

    def test_alias_map_completeness(self):
        # Every value in alias map must be a known child tool
        for alias, canonical in TOOL_ALIAS_MAP.items():
            assert canonical in {"list_dir", "search_files", "read_file", "git_status",
                                 "git_diff", "code_search", "web_fetch", "browser",
                                 "run_command", "apply_patch"}, \
                f"Alias {alias!r} maps to unknown tool {canonical!r}"
