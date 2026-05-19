"""Comprehensive tests for command_compat adapter."""

from __future__ import annotations

import time
import re

import pytest

from local_agent_runtime.tools.command_compat import CommandCompatAdapter, AdaptationResult, TransformRecord
from local_agent_runtime.tools.run_command import _powershell_execution_command


# ── Fixtures ──────────────────────────────────────────────────────────

@pytest.fixture
def adapter() -> CommandCompatAdapter:
    return CommandCompatAdapter()


# ── A. && chain operators ────────────────────────────────────────────

class TestChainAnd:
    def test_simple_and(self, adapter: CommandCompatAdapter) -> None:
        r = adapter.adapt("cd dir && npm test", "powershell")
        assert "if ($?)" in r.adapted
        assert r.shell_target == "powershell"

    def test_triple_and(self, adapter: CommandCompatAdapter) -> None:
        r = adapter.adapt("a && b && c", "powershell")
        # Should wrap both right-hand segments
        assert r.adapted.count("if ($?)") >= 2
        assert r.shell_target == "powershell"

    def test_and_with_flags(self, adapter: CommandCompatAdapter) -> None:
        r = adapter.adapt("cd src && ls -la", "powershell")
        assert "if ($?)" in r.adapted

    def test_and_with_pipes(self, adapter: CommandCompatAdapter) -> None:
        # && combined with | (pipe) should only adapt &&, leave | alone
        r = adapter.adapt("cd dir && ls | grep foo", "powershell")
        assert "if ($?)" in r.adapted
        assert "|" in r.adapted  # pipe still present


# ── B. || fallback operators ──────────────────────────────────────────

class TestChainOr:
    def test_simple_or(self, adapter: CommandCompatAdapter) -> None:
        r = adapter.adapt("test -f x || echo missing", "powershell")
        assert "if (-not $?)" in r.adapted

    def test_triple_or(self, adapter: CommandCompatAdapter) -> None:
        r = adapter.adapt("a || b || c", "powershell")
        assert r.adapted.count("if (-not $?)") >= 2

    def test_or_with_flags(self, adapter: CommandCompatAdapter) -> None:
        r = adapter.adapt("npm test || echo failed", "powershell")
        assert "if (-not $?)" in r.adapted


# ── C. Mixed chains ──────────────────────────────────────────────────

class TestMixedChains:
    def test_and_or_mixed(self, adapter: CommandCompatAdapter) -> None:
        r = adapter.adapt("a && b || c", "powershell")
        assert "if ($?)" in r.adapted
        assert "if (-not $?)" in r.adapted

    def test_complex_chain(self, adapter: CommandCompatAdapter) -> None:
        r = adapter.adapt("build && test || echo fail", "powershell")
        # build; if ($?) { test } ; if (-not $?) { echo fail }
        assert "if ($?)" in r.adapted
        assert "if (-not $?)" in r.adapted


# ── D. Redirections ──────────────────────────────────────────────────

class TestRedirections:
    def test_stderr_to_null(self, adapter: CommandCompatAdapter) -> None:
        r = adapter.adapt("cmd 2>/dev/null", "powershell")
        assert "2>$null" in r.adapted

    def test_stdout_to_null(self, adapter: CommandCompatAdapter) -> None:
        r = adapter.adapt("cmd 1>/dev/null", "powershell")
        assert "1>$null" in r.adapted

    def test_all_to_null(self, adapter: CommandCompatAdapter) -> None:
        r = adapter.adapt("cmd &>/dev/null", "powershell")
        assert "*>$null" in r.adapted

    def test_redirect_with_chain(self, adapter: CommandCompatAdapter) -> None:
        r = adapter.adapt("cd .. && ls 2>/dev/null", "powershell")
        assert "if ($?)" in r.adapted
        assert "2>$null" in r.adapted


# ── E. Environment variables ──────────────────────────────────────────

class TestEnvVars:
    def test_dollar_var(self, adapter: CommandCompatAdapter) -> None:
        r = adapter.adapt("echo $HOME", "powershell")
        assert "$env:HOME" in r.adapted

    def test_braced_var(self, adapter: CommandCompatAdapter) -> None:
        r = adapter.adapt("echo ${PATH}", "powershell")
        assert "$env:PATH" in r.adapted

    def test_export_simple(self, adapter: CommandCompatAdapter) -> None:
        r = adapter.adapt("export FOO=bar", "powershell")
        assert '$env:FOO = "bar"' in r.adapted

    def test_export_with_value(self, adapter: CommandCompatAdapter) -> None:
        r = adapter.adapt("export NODE_ENV=production", "powershell")
        assert '$env:NODE_ENV = "production"' in r.adapted

    def test_env_medium_confidence(self, adapter: CommandCompatAdapter) -> None:
        r = adapter.adapt("echo $HOME", "powershell")
        assert r.confidence == "medium"  # env var transforms are medium


# ── F. source ─────────────────────────────────────────────────────────

class TestSource:
    def test_source_file(self, adapter: CommandCompatAdapter) -> None:
        r = adapter.adapt("source .env", "powershell")
        assert ". " in r.adapted
        assert "source" not in r.adapted

    def test_source_with_path(self, adapter: CommandCompatAdapter) -> None:
        r = adapter.adapt("source /path/to/script.sh", "powershell")
        assert ". " in r.adapted


# ── G. Line continuation ─────────────────────────────────────────────

class TestLineContinuation:
    def test_backslash_continuation(self, adapter: CommandCompatAdapter) -> None:
        r = adapter.adapt("echo hello \\\nworld", "powershell")
        assert "`\n" in r.adapted


# ── H. Passthrough — PowerShell native ───────────────────────────────

class TestPassthrough:
    def test_bash_shell_passthrough(self, adapter: CommandCompatAdapter) -> None:
        r = adapter.adapt("cd dir && npm test", "bash")
        assert r.shell_target == "passthrough"
        assert r.adapted == "cd dir && npm test"

    def test_zsh_passthrough(self, adapter: CommandCompatAdapter) -> None:
        r = adapter.adapt("a && b", "zsh")
        assert r.shell_target == "passthrough"

    def test_powershell_native_passthrough(self, adapter: CommandCompatAdapter) -> None:
        # Commands with no bash idioms should pass through unchanged
        r = adapter.adapt("Get-ChildItem", "powershell")
        assert r.adapted == "Get-ChildItem"
        assert r.shell_target == "passthrough"


# ── I. Edge cases ────────────────────────────────────────────────────

class TestEdgeCases:
    def test_empty_command(self, adapter: CommandCompatAdapter) -> None:
        r = adapter.adapt("", "powershell")
        assert r.adapted == ""

    def test_single_word(self, adapter: CommandCompatAdapter) -> None:
        r = adapter.adapt("ls", "powershell")
        assert r.adapted == "ls"

    def test_cd_drive_flag(self, adapter: CommandCompatAdapter) -> None:
        r = adapter.adapt('cd /d "C:\\tmp\\project"', "powershell")
        assert r.adapted == 'Set-Location -LiteralPath "C:\\tmp\\project"'
        assert r.shell_target == "powershell"

    def test_multi_path_ls(self, adapter: CommandCompatAdapter) -> None:
        r = adapter.adapt('ls "index.html" "styles.css" "app.js" "README.md"', "powershell")
        assert r.adapted == "Get-ChildItem -LiteralPath 'index.html','styles.css','app.js','README.md'"
        assert r.shell_target == "powershell"

    def test_multi_path_ls_with_bash_flags_and_redirect(self, adapter: CommandCompatAdapter) -> None:
        r = adapter.adapt("ls -la index.html styles.css app.js README.md 2>&1", "powershell")
        assert r.adapted == "Get-ChildItem -LiteralPath 'index.html','styles.css','app.js','README.md'"
        assert r.shell_target == "powershell"

    def test_cd_drive_flag_with_dir_chain(self, adapter: CommandCompatAdapter) -> None:
        r = adapter.adapt('cd /d "C:\\tmp\\project" && dir index.html styles.css app.js README.md', "powershell")
        assert 'Set-Location -LiteralPath "C:\\tmp\\project"' in r.adapted
        assert "Get-ChildItem -LiteralPath 'index.html','styles.css','app.js','README.md'" in r.adapted
        assert "if ($?)" in r.adapted

    def test_powershell_automatic_variables_not_env_rewritten(self, adapter: CommandCompatAdapter) -> None:
        r = adapter.adapt("powershell -Command \"Get-ChildItem | ForEach-Object { $_.Name }; $matches.Count\"", "powershell")
        assert "$_.Name" in r.adapted
        assert "$matches.Count" in r.adapted
        assert "$env:_" not in r.adapted
        assert "$env:matches" not in r.adapted

    def test_quoted_string(self, adapter: CommandCompatAdapter) -> None:
        r = adapter.adapt('echo "hello world"', "powershell")
        assert r.adapted == 'echo "hello world"'

    def test_no_transforms_means_passthrough(self, adapter: CommandCompatAdapter) -> None:
        r = adapter.adapt("git status", "powershell")
        assert r.shell_target == "passthrough"

    def test_comment_in_command(self, adapter: CommandCompatAdapter) -> None:
        r = adapter.adapt("echo test # comment", "powershell")
        # Should not crash
        assert "echo test" in r.adapted

    def test_missing_program_files_node_rewrites_to_configured_node(self, tmp_path, monkeypatch) -> None:
        node_dir = tmp_path / "node" / "bin"
        node_dir.mkdir(parents=True)
        node = node_dir / "node.exe"
        node.write_text("", encoding="utf-8")
        monkeypatch.setenv("LOCAL_AGENT_NODE_EXECUTABLE", str(node))

        command = _powershell_execution_command(
            '& "C:\\Program Files\\nodejs\\node.exe" --check app.js',
            "powershell",
        )

        assert str(node) in command
        assert "C:\\Program Files\\nodejs\\node.exe" not in command

    def test_bare_node_command_rewrites_to_configured_node(self, tmp_path, monkeypatch) -> None:
        node_dir = tmp_path / "node" / "bin"
        node_dir.mkdir(parents=True)
        node = node_dir / "node.exe"
        node.write_text("", encoding="utf-8")
        monkeypatch.setenv("LOCAL_AGENT_NODE_EXECUTABLE", str(node))

        command = _powershell_execution_command("node --check app.js", "powershell")

        assert command.startswith(f'& "{node}"')
        assert command.endswith(" --check app.js")


# ── J. Mixed scenario ────────────────────────────────────────────────

class TestMixedScenario:
    def test_chain_with_redirect(self, adapter: CommandCompatAdapter) -> None:
        r = adapter.adapt("cd ../.. && ls -la 2>/dev/null", "powershell")
        assert "if ($?)" in r.adapted
        assert "2>$null" in r.adapted

    def test_chain_with_env_and_redirect(self, adapter: CommandCompatAdapter) -> None:
        r = adapter.adapt("export FOO=bar && echo $FOO 2>/dev/null", "powershell")
        assert "if ($?)" in r.adapted
        assert "2>$null" in r.adapted

    def test_source_and_chain(self, adapter: CommandCompatAdapter) -> None:
        r = adapter.adapt("source .env && npm test", "powershell")
        assert ". " in r.adapted
        assert "if ($?)" in r.adapted


# ── K. Performance ────────────────────────────────────────────────────

class TestPerformance:
    def test_1000_commands_under_threshold(self, adapter: CommandCompatAdapter) -> None:
        commands = [f"cmd{i} && step2_{i}" for i in range(1000)]
        start = time.perf_counter()
        for cmd in commands:
            adapter.adapt(cmd, "powershell")
        elapsed = time.perf_counter() - start
        assert elapsed < 2.0, f"1000 adaptations took {elapsed:.3f}s"

    def test_single_command_latency(self, adapter: CommandCompatAdapter) -> None:
        start = time.perf_counter()
        adapter.adapt("cd dir && npm test 2>/dev/null", "powershell")
        elapsed = time.perf_counter() - start
        assert elapsed < 0.1, f"Single adaptation took {elapsed:.3f}s"


# ── L. Confidence levels ─────────────────────────────────────────────

class TestConfidence:
    def test_high_confidence_when_no_medium_transforms(self, adapter: CommandCompatAdapter) -> None:
        r = adapter.adapt("cd dir && npm test", "powershell")
        # && is high confidence; no env vars → overall high
        # BUT env var transform with medium confidence may apply to some commands
        # This specific command has no $ so all transforms are high
        assert r.confidence == "high"

    def test_medium_confidence_with_env_var(self, adapter: CommandCompatAdapter) -> None:
        r = adapter.adapt("echo $HOME && cd dir", "powershell")
        assert r.confidence == "medium"
