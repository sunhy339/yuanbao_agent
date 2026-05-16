"""code_search tool — semantic/structural code search with AST-aware chunking."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ._shared import (
    matches_glob,
    merged_ignore_patterns,
    require_workspace_root,
    resolve_workspace_path,
    to_relative_path,
    walk_directory,
)


def _chunk_by_lines(text: str, chunk_size: int = 50, overlap: int = 5) -> list[dict[str, Any]]:
    """Split text into overlapping line-based chunks."""
    lines = text.splitlines()
    if not lines:
        return []
    chunks: list[dict[str, Any]] = []
    start = 0
    while start < len(lines):
        end = min(start + chunk_size, len(lines))
        chunk_lines = lines[start:end]
        chunks.append({
            "startLine": start + 1,
            "endLine": end,
            "content": "\n".join(chunk_lines),
        })
        start += chunk_size - overlap
    return chunks


def _find_definitions(content: str, query: str, file_path: str) -> list[dict[str, Any]]:
    """Find function/class definitions matching the query."""
    results: list[dict[str, Any]] = []
    pattern = re.compile(
        r"^(class |def |async def |function |const |let |var |public |private |protected |static )"
        r"(\w+)",
        re.MULTILINE,
    )
    for match in pattern.finditer(content):
        kind = match.group(1).strip()
        name = match.group(2)
        if query.lower() in name.lower():
            start_pos = match.start()
            line_no = content[:start_pos].count("\n") + 1
            end_line = min(line_no + 30, content.count("\n") + 1)
            snippet_lines = content.splitlines()[line_no - 1:end_line]
            results.append({
                "path": file_path,
                "line": line_no,
                "endLine": end_line,
                "kind": kind,
                "name": name,
                "snippet": "\n".join(snippet_lines[:30]),
            })
    return results


def _find_references(content: str, query: str, file_path: str) -> list[dict[str, Any]]:
    """Find references/usage of a symbol."""
    results: list[dict[str, Any]] = []
    try:
        pattern = re.compile(r"\b" + re.escape(query) + r"\b")
    except re.error:
        return results
    lines = content.splitlines()
    for i, line in enumerate(lines):
        if pattern.search(line):
            results.append({
                "path": file_path,
                "line": i + 1,
                "content": line.strip(),
            })
    return results


def build_code_search_tool(policy_guard: Any, store: Any, subagent_service: Any | None = None) -> dict[str, Any]:
    def code_search(params: dict[str, Any]) -> dict[str, Any]:
        workspace_root = require_workspace_root(params)
        query = str(params.get("query", "")).strip()
        if not query:
            raise ValueError("query is required")

        mode = str(params.get("mode", "definition"))
        if mode not in ("definition", "reference", "symbol", "chunk"):
            raise ValueError(f"Invalid mode: {mode}. Must be definition, reference, symbol, or chunk")

        glob_patterns = params.get("glob") or ["**/*.py", "**/*.ts", "**/*.tsx", "**/*.js", "**/*.rs", "**/*.go"]
        if isinstance(glob_patterns, str):
            glob_patterns = [glob_patterns]

        max_results = int(params.get("max_results", 30))
        max_results = max(1, min(max_results, 100))

        search_root = resolve_workspace_path(policy_guard, workspace_root, params.get("path"))
        if not search_root.is_dir():
            raise ValueError(f"Directory does not exist: {params.get('path', '.')}")

        ignore_patterns = merged_ignore_patterns(store, params.get("ignore"))
        entries = walk_directory(
            workspace_root=workspace_root,
            base_path=search_root,
            recursive=True,
            store=store,
            max_depth=max(1, min(int(params.get("max_depth", 6)), 8)),
            ignore_patterns=ignore_patterns,
        )

        results: list[dict[str, Any]] = []
        for entry in entries:
            if len(results) >= max_results:
                break
            entry_path = workspace_root / entry["path"]
            if not entry_path.is_file():
                continue
            if glob_patterns and not any(matches_glob(entry["path"], pattern) for pattern in glob_patterns):
                continue
            try:
                content = entry_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue

            rel_path = to_relative_path(workspace_root, entry_path)

            if mode == "definition":
                defs = _find_definitions(content, query, rel_path)
                results.extend(defs[:max_results - len(results)])
            elif mode == "reference":
                refs = _find_references(content, query, rel_path)
                results.extend(refs[:max_results - len(results)])
            elif mode == "symbol":
                defs = _find_definitions(content, query, rel_path)
                refs = _find_references(content, query, rel_path)
                combined = defs + refs
                results.extend(combined[:max_results - len(results)])
            elif mode == "chunk":
                chunks = _chunk_by_lines(content)
                query_lower = query.lower()
                for chunk in chunks:
                    if len(results) >= max_results:
                        break
                    if query_lower in chunk["content"].lower():
                        results.append({
                            "path": rel_path,
                            "startLine": chunk["startLine"],
                            "endLine": chunk["endLine"],
                            "content": chunk["content"],
                        })

        return {
            "query": query,
            "mode": mode,
            "results": results[:max_results],
            "totalMatches": len(results),
            "truncated": len(results) > max_results,
        }

    return {"handler": code_search}
