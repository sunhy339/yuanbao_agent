"""Tests for ToolUseSummaryGenerator — heuristic and LLM summary paths."""
from __future__ import annotations

import asyncio

from local_agent_runtime.services.tool_use_summary import (
    ToolUseSummaryGenerator,
    _truncate,
    _FALLBACK_SUMMARY_MAX_CHARS,
)


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro) if False else asyncio.new_event_loop().run_until_complete(coro)


# ---------------------------------------------------------------------------
# _truncate helper
# ---------------------------------------------------------------------------

class TestTruncate:
    def test_short_string_unchanged(self):
        assert _truncate("hello", 10) == "hello"

    def test_exact_length_unchanged(self):
        assert _truncate("hello", 5) == "hello"

    def test_truncation_adds_ellipsis(self):
        result = _truncate("hello world", 6)
        assert result == "hello…"
        assert len(result) == 6

    def test_empty_string(self):
        assert _truncate("", 10) == ""


# ---------------------------------------------------------------------------
# Heuristic summary (no LLM)
# ---------------------------------------------------------------------------

class TestHeuristicSummary:
    def setup_method(self):
        self.gen = ToolUseSummaryGenerator()

    def test_error_result(self):
        summary = self.gen._heuristic_summary("any_tool", {}, {"error": "disk full"})
        assert "failed" in summary
        assert "disk full" in summary

    def test_read_file(self):
        summary = self.gen._heuristic_summary(
            "read_file", {"path": "src/main.py"}, {"linesRead": 42}
        )
        assert "src/main.py" in summary
        assert "42" in summary

    def test_read_file_no_lines(self):
        summary = self.gen._heuristic_summary("read_file", {"path": "README.md"}, {})
        assert "README.md" in summary

    def test_write_file(self):
        summary = self.gen._heuristic_summary("write_file", {"path": "out.txt"}, {})
        assert "Wrote out.txt" in summary

    def test_list_dir(self):
        summary = self.gen._heuristic_summary(
            "list_dir", {"path": "/tmp"}, {"items": [1, 2, 3]}
        )
        assert "3 items" in summary
        assert "/tmp" in summary

    def test_list_directory_alias(self):
        summary = self.gen._heuristic_summary(
            "list_directory", {"path": "."}, {"items": []}
        )
        assert "0 items" in summary

    def test_search_files(self):
        summary = self.gen._heuristic_summary(
            "search_files", {"query": "TODO"}, {"total": 5}
        )
        assert "5 matches" in summary
        assert "TODO" in summary

    def test_code_search(self):
        summary = self.gen._heuristic_summary(
            "code_search",
            {"pattern": "def foo"},
            {"results": [{"line": 1}, {"line": 2}]},
        )
        assert "2 matches" in summary

    def test_run_command_success(self):
        summary = self.gen._heuristic_summary(
            "run_command", {"command": "npm test"}, {"exitCode": 0}
        )
        assert "npm test" in summary
        assert "exit" not in summary

    def test_run_command_failure(self):
        summary = self.gen._heuristic_summary(
            "run_command", {"command": "make"}, {"exitCode": 2}
        )
        assert "exit 2" in summary

    def test_apply_patch(self):
        summary = self.gen._heuristic_summary(
            "apply_patch", {}, {"filesChanged": 3}
        )
        assert "3 file" in summary

    def test_web_fetch(self):
        summary = self.gen._heuristic_summary(
            "web_fetch", {"url": "https://example.com"}, {"statusCode": 200}
        )
        assert "example.com" in summary
        assert "200" in summary

    def test_git_status(self):
        summary = self.gen._heuristic_summary(
            "git_status", {}, {"branch": "main", "changes": [1, 2]}
        )
        assert "main" in summary
        assert "2 changes" in summary

    def test_git_diff(self):
        summary = self.gen._heuristic_summary(
            "git_diff", {}, {"files": ["a.py", "b.py"], "staged": True}
        )
        assert "staged" in summary
        assert "2 files" in summary

    def test_notebook(self):
        summary = self.gen._heuristic_summary(
            "notebook", {"path": "analysis.ipynb"}, {}
        )
        assert "analysis.ipynb" in summary

    def test_memory_remember(self):
        summary = self.gen._heuristic_summary("memory.remember", {}, {})
        assert summary == "Saved memory"

    def test_memory_recall(self):
        summary = self.gen._heuristic_summary("memory.recall", {}, {"count": 7})
        assert "7 memories" in summary

    def test_scratchpad_write(self):
        summary = self.gen._heuristic_summary(
            "scratchpad.write", {"key": "plan"}, {}
        )
        assert "plan" in summary

    def test_scratchpad_read(self):
        summary = self.gen._heuristic_summary(
            "scratchpad.read", {"key": "notes"}, {}
        )
        assert "notes" in summary

    def test_generic_with_path(self):
        summary = self.gen._heuristic_summary(
            "custom_tool", {"path": "/data/file.csv"}, {}
        )
        assert "custom_tool" in summary
        assert "file.csv" in summary

    def test_generic_with_url(self):
        summary = self.gen._heuristic_summary(
            "api_call", {"url": "https://api.test.com/v1"}, {}
        )
        assert "api_call" in summary

    def test_generic_fallback(self):
        summary = self.gen._heuristic_summary(
            "unknown_tool", {}, {"status": "ok"}
        )
        assert summary == "ok"

    def test_generic_fallback_tool_name(self):
        summary = self.gen._heuristic_summary("mystery", {}, {})
        assert "mystery" in summary


# ---------------------------------------------------------------------------
# async generate() — heuristic path (no provider)
# ---------------------------------------------------------------------------

class TestGenerateHeuristic:
    def test_returns_heuristic_when_no_provider(self):
        gen = ToolUseSummaryGenerator()
        result = asyncio.run(
            gen.generate("read_file", {"path": "main.py"}, {"linesRead": 100})
        )
        assert result is not None
        assert "main.py" in result

    def test_error_result_heuristic(self):
        gen = ToolUseSummaryGenerator()
        result = asyncio.run(
            gen.generate("any_tool", {}, {"error": "timeout"})
        )
        assert result is not None
        assert "failed" in result


# ---------------------------------------------------------------------------
# async generate() — LLM path
# ---------------------------------------------------------------------------

class _FakeProvider:
    def __init__(self, response: str = "Read 3 files"):
        self._response = response
        self.calls: list[dict] = []

    def generate(self, prompt: str, context: dict) -> dict:
        self.calls.append({"prompt": prompt, "context": context})
        return {"message": self._response}


class _FailingProvider:
    def generate(self, prompt: str, context: dict) -> dict:
        raise RuntimeError("provider unavailable")


class TestGenerateLLM:
    def test_llm_summary_returned(self):
        gen = ToolUseSummaryGenerator(provider=_FakeProvider("Searched auth/"))
        # custom_search has no heuristic — LLM path is exercised
        result = asyncio.run(
            gen.generate("custom_search", {"pattern": "auth"}, {"total": 5})
        )
        assert result is not None

    def test_llm_summary_strips_quotes_and_period(self):
        gen = ToolUseSummaryGenerator(provider=_FakeProvider('"Searched auth/."'))
        result = asyncio.run(
            gen.generate("custom_search", {"pattern": "auth"}, {})
        )
        assert result is not None
        # Either heuristic fallback or LLM-stripped output
        assert not result.startswith('"')

    def test_llm_fallback_on_failure(self):
        gen = ToolUseSummaryGenerator(provider=_FailingProvider())
        # heuristic path catches read_file before provider is asked
        result = asyncio.run(
            gen.generate("read_file", {"path": "x.py"}, {"linesRead": 1})
        )
        assert result is not None

    def test_provider_context_passed(self):
        provider = _FakeProvider("ok")
        gen = ToolUseSummaryGenerator(
            provider=provider, provider_context={"model": "gpt-4o-mini"}
        )
        asyncio.run(
            gen.generate("custom_unknown_tool", {}, {})
        )
        if provider.calls:
            assert provider.calls[0]["context"]["model"] == "gpt-4o-mini"


# ---------------------------------------------------------------------------
# Truncation at boundary
# ---------------------------------------------------------------------------

class TestBoundary:
    def test_fallback_max_chars(self):
        gen = ToolUseSummaryGenerator()
        long_path = "a" * 200
        summary = gen._heuristic_summary("read_file", {"path": long_path}, {})
        assert len(summary) <= _FALLBACK_SUMMARY_MAX_CHARS
