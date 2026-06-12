"""Tool Use Summary Generator — produces concise git-commit-style summaries
for completed tool calls, similar to haha-cc's toolUseSummaryGenerator.

The summary is used for:
1. UI collapsed-state rendering (replaces truncated tool_result)
2. Context compaction (summary replaces full tool output in compressed context)
3. Event stream enrichment (tool.summary_ready event for deferred rendering)
"""
from __future__ import annotations

import logging
from typing import Any, Protocol

logger = logging.getLogger(__name__)

_SUMMARY_SYSTEM_PROMPT = (
    "You are a tool-use summarizer. Given a tool name, its input, and its output, "
    "produce a very short summary (max 30 chars) in git-commit style. "
    "Examples: 'Searched in auth/', 'Read 42 lines from main.py', 'Patched 3 files', "
    "'Ran npm test — 12 passed', 'Fetched https://example.com — 200'. "
    "Reply with ONLY the summary, no punctuation at end, no quotes."
)

_SUMMARY_MAX_CHARS = 30
_FALLBACK_SUMMARY_MAX_CHARS = 80


class _CanGenerate(Protocol):
    def generate(self, prompt: str, context: dict) -> dict: ...


class ToolUseSummaryGenerator:
    """Generates concise summaries for completed tool calls.

    Uses a provider (LLM) to generate a ≤30 char summary. Falls back to a
    heuristic summary if the provider is unavailable or fails.
    """

    def __init__(
        self,
        provider: _CanGenerate | None = None,
        *,
        provider_context: dict[str, Any] | None = None,
        timeout_ms: int = 5000,
    ) -> None:
        self._provider = provider
        self._provider_context = provider_context or {}
        self._timeout_ms = timeout_ms

    async def generate(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
        tool_result: dict[str, Any],
    ) -> str | None:
        """Generate a summary for a completed tool call.

        Returns a short string (≤80 chars) or None on failure.
        """
        # Fast path: use heuristic for well-known tools
        heuristic = self._heuristic_summary(tool_name, tool_input, tool_result)
        if heuristic:
            return heuristic

        # LLM path: ask provider for a concise summary
        if self._provider is not None:
            try:
                return await self._llm_summary(tool_name, tool_input, tool_result)
            except Exception:
                logger.debug("LLM summary generation failed for %s, using heuristic", tool_name)

        return heuristic

    def _heuristic_summary(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
        tool_result: dict[str, Any],
    ) -> str:
        """Produce a heuristic summary without LLM involvement."""
        status = str(tool_result.get("status") or "").strip().lower()
        error = tool_result.get("error")

        if error:
            return _truncate(f"failed: {error}", _FALLBACK_SUMMARY_MAX_CHARS)

        if tool_name == "read_file":
            path = tool_input.get("path") or tool_result.get("path") or "file"
            lines = tool_result.get("linesRead")
            suffix = f" ({lines} lines)" if isinstance(lines, int) else ""
            return _truncate(f"Read {path}{suffix}", _FALLBACK_SUMMARY_MAX_CHARS)

        if tool_name == "write_file":
            path = tool_input.get("path") or tool_result.get("path") or "file"
            return _truncate(f"Wrote {path}", _FALLBACK_SUMMARY_MAX_CHARS)

        if tool_name in {"list_dir", "list_directory"}:
            path = tool_input.get("path") or tool_result.get("path") or "."
            items = tool_result.get("items")
            count = len(items) if isinstance(items, list) else 0
            return _truncate(f"Listed {count} items in {path}", _FALLBACK_SUMMARY_MAX_CHARS)

        if tool_name in {"search_files", "code_search"}:
            query = tool_input.get("query") or tool_input.get("pattern") or "query"
            total = tool_result.get("total") or tool_result.get("totalMatches")
            matches = tool_result.get("matches") or tool_result.get("results")
            count = total if isinstance(total, int) else len(matches) if isinstance(matches, list) else 0
            return _truncate(f"Found {count} matches for {query}", _FALLBACK_SUMMARY_MAX_CHARS)

        if tool_name == "run_command":
            cmd = tool_input.get("command") or "command"
            exit_code = tool_result.get("exitCode")
            if isinstance(exit_code, int) and exit_code != 0:
                return _truncate(f"Ran {cmd} — exit {exit_code}", _FALLBACK_SUMMARY_MAX_CHARS)
            return _truncate(f"Ran {cmd}", _FALLBACK_SUMMARY_MAX_CHARS)

        if tool_name == "apply_patch":
            files = tool_result.get("filesChanged")
            return _truncate(f"Patched {files or '?'} file(s)", _FALLBACK_SUMMARY_MAX_CHARS)

        if tool_name in {"web_fetch", "browser"}:
            url = tool_input.get("url") or tool_result.get("url") or "url"
            code = tool_result.get("statusCode")
            suffix = f" — {code}" if isinstance(code, int) else ""
            return _truncate(f"Fetched {url}{suffix}", _FALLBACK_SUMMARY_MAX_CHARS)

        if tool_name == "git_status":
            branch = tool_result.get("branch") or "detached"
            changes = tool_result.get("changes")
            count = len(changes) if isinstance(changes, list) else 0
            return _truncate(f"git status: {branch}, {count} changes", _FALLBACK_SUMMARY_MAX_CHARS)

        if tool_name == "git_diff":
            files = tool_result.get("files")
            count = len(files) if isinstance(files, list) else 0
            scope = "staged" if tool_result.get("staged") else "worktree"
            return _truncate(f"git diff ({scope}): {count} files", _FALLBACK_SUMMARY_MAX_CHARS)

        if tool_name == "notebook":
            path = tool_input.get("path") or tool_result.get("path") or "notebook"
            return _truncate(f"Notebook {path}", _FALLBACK_SUMMARY_MAX_CHARS)

        if tool_name == "memory.remember":
            return "Saved memory"

        if tool_name == "memory.recall":
            count = tool_result.get("count", 0)
            return _truncate(f"Recalled {count} memories", _FALLBACK_SUMMARY_MAX_CHARS)

        if tool_name == "scratchpad.write":
            key = tool_input.get("key") or "key"
            return _truncate(f"Wrote scratchpad {key}", _FALLBACK_SUMMARY_MAX_CHARS)

        if tool_name == "scratchpad.read":
            key = tool_input.get("key") or "key"
            return _truncate(f"Read scratchpad {key}", _FALLBACK_SUMMARY_MAX_CHARS)

        # Generic fallback
        target = tool_input.get("path") or tool_input.get("url") or tool_input.get("command") or ""
        if target:
            return _truncate(f"{tool_name} {target}", _FALLBACK_SUMMARY_MAX_CHARS)

        summary = tool_result.get("summary") or tool_result.get("resultSummary") or status
        return _truncate(str(summary or tool_name), _FALLBACK_SUMMARY_MAX_CHARS)

    async def _llm_summary(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
        tool_result: dict[str, Any],
    ) -> str | None:
        """Ask the LLM for a concise summary."""
        import json

        # Truncate large inputs/outputs to keep the prompt small
        input_preview = json.dumps(tool_input, ensure_ascii=False, default=str)[:500]
        result_preview = json.dumps(tool_result, ensure_ascii=False, default=str)[:500]

        prompt = (
            f"Tool: {tool_name}\n"
            f"Input: {input_preview}\n"
            f"Output: {result_preview}\n\n"
            f"Summary (max {_SUMMARY_MAX_CHARS} chars):"
        )

        result = self._provider.generate(  # type: ignore[union-attr]
            prompt,
            {
                **self._provider_context,
                "messages": [
                    {"role": "system", "content": _SUMMARY_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                "max_tokens": 64,
            },
        )

        raw = result.get("message", "") or result.get("content", "")
        if raw:
            return _truncate(raw.strip().strip('"').strip(".").strip(), _FALLBACK_SUMMARY_MAX_CHARS)
        return None


def _truncate(text: str, max_len: int) -> str:
    """Truncate text to max_len, adding ellipsis if needed."""
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"
