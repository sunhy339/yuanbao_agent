"""search_files tool — content and filename search."""

from __future__ import annotations

import shutil
import subprocess
import json
from typing import Any

from ._shared import (
    merged_glob_patterns,
    python_content_search,
    python_filename_search,
    require_workspace_root,
    rg_content_search,
    rg_filename_search,
)


def _step(label: str, status: str, summary: str) -> dict[str, str]:
    return {"label": label, "status": status, "summary": summary}


def build_search_files_tool(policy_guard: Any, store: Any, subagent_service: Any | None = None) -> dict[str, Any]:
    def search_files(params: dict[str, Any]) -> dict[str, Any]:
        workspace_root = require_workspace_root(params)
        query = str(params.get("query", "")).strip()
        if not query:
            raise ValueError("query is required")

        mode = params.get("mode", "content")
        if mode not in {"content", "filename"}:
            raise ValueError(f"Unsupported search mode: {mode}")

        glob_patterns = merged_glob_patterns(store, params.get("glob"))
        ignore_patterns = params.get("ignore")
        max_results = int(params.get("max_results", 20))
        max_results = max(1, min(max_results, 200))

        rg_available = shutil.which("rg") is not None
        backend = "python"
        steps = [
            _step("prepare", "completed", f"{mode} search for {query}"),
            _step("backend", "completed", "rg" if rg_available else "python"),
        ]

        if rg_available:
            try:
                if mode == "filename":
                    matches = rg_filename_search(
                        workspace_root,
                        query,
                        glob_patterns,
                        max_results,
                        store,
                        ignore_patterns,
                    )
                else:
                    matches = rg_content_search(
                        workspace_root,
                        query,
                        glob_patterns,
                        max_results,
                        store,
                        ignore_patterns,
                    )
                backend = "rg"
            except (
                subprocess.CalledProcessError,
                FileNotFoundError,
                PermissionError,
                json.JSONDecodeError,
            ):
                steps.append(_step("fallback", "completed", "python search"))
                if mode == "filename":
                    matches = python_filename_search(
                        workspace_root,
                        query,
                        glob_patterns,
                        max_results,
                        store,
                        ignore_patterns,
                    )
                else:
                    matches = python_content_search(
                        workspace_root,
                        query,
                        glob_patterns,
                        max_results,
                        store,
                        ignore_patterns,
                    )
        else:
            if mode == "filename":
                matches = python_filename_search(
                    workspace_root,
                    query,
                    glob_patterns,
                    max_results,
                    store,
                    ignore_patterns,
                )
            else:
                matches = python_content_search(
                    workspace_root,
                    query,
                    glob_patterns,
                    max_results,
                    store,
                    ignore_patterns,
                )

        steps.append(_step("search", "completed", f"{len(matches)} match(es)"))
        return {
            "query": query,
            "mode": mode,
            "backend": backend,
            "matches": matches,
            "total": len(matches),
            "steps": steps,
        }

    return {"handler": search_files}
