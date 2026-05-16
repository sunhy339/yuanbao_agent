"""notebook tool — read and execute Jupyter notebook cells."""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from ._shared import (
    require_workspace_root,
    resolve_workspace_path,
    to_relative_path,
)


def _load_notebook(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8", errors="replace")
    return json.loads(text)


def _cell_summary(index: int, cell: dict[str, Any]) -> dict[str, Any]:
    source = "".join(cell.get("source", []))
    return {
        "index": index,
        "cellType": cell.get("cell_type", "unknown"),
        "sourcePreview": source[:200] + ("..." if len(source) > 200 else ""),
        "sourceLength": len(source),
        "executionCount": cell.get("execution_count"),
        "hasOutput": bool(cell.get("outputs")),
    }


def _execute_cell(source: str, timeout: int = 60) -> dict[str, Any]:
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False, encoding="utf-8"
    ) as tmp:
        tmp.write(source)
        tmp_path = tmp.name
    try:
        result = subprocess.run(
            ["python", tmp_path],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
        return {
            "exitCode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }
    except subprocess.TimeoutExpired:
        return {"exitCode": -1, "stdout": "", "stderr": "Execution timed out"}
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def build_notebook_tool(policy_guard: Any, store: Any, subagent_service: Any | None = None) -> dict[str, Any]:
    def notebook(params: dict[str, Any]) -> dict[str, Any]:
        workspace_root = require_workspace_root(params)
        path = params.get("path", "")
        if not path:
            raise ValueError("path is required")

        file_path = resolve_workspace_path(policy_guard, workspace_root, path)
        if not file_path.is_file():
            raise ValueError(f"Notebook not found: {path}")

        if not file_path.suffix == ".ipynb":
            raise ValueError(f"Not a notebook file: {path}")

        action = str(params.get("action", "list_cells"))
        nb = _load_notebook(file_path)
        cells = nb.get("cells", [])

        if action == "list_cells":
            return {
                "path": to_relative_path(workspace_root, file_path),
                "kernel": nb.get("metadata", {}).get("kernelspec", {}).get("display_name", "unknown"),
                "totalCells": len(cells),
                "cells": [_cell_summary(i, c) for i, c in enumerate(cells)],
            }

        if action == "get_cell":
            cell_index = int(params.get("cell_index", -1))
            if cell_index < 0 or cell_index >= len(cells):
                raise ValueError(f"Invalid cell_index: {cell_index}. Notebook has {len(cells)} cells.")
            cell = cells[cell_index]
            source = "".join(cell.get("source", []))
            outputs = cell.get("outputs", [])
            return {
                "path": to_relative_path(workspace_root, file_path),
                "index": cell_index,
                "cellType": cell.get("cell_type", "unknown"),
                "source": source,
                "outputs": outputs,
                "executionCount": cell.get("execution_count"),
            }

        if action == "execute_cell":
            cell_index = int(params.get("cell_index", -1))
            if cell_index < 0 or cell_index >= len(cells):
                raise ValueError(f"Invalid cell_index: {cell_index}. Notebook has {len(cells)} cells.")
            cell = cells[cell_index]
            source = "".join(cell.get("source", []))
            timeout = int(params.get("timeout", 60))
            timeout = max(5, min(timeout, 300))
            exec_result = _execute_cell(source, timeout=timeout)
            return {
                "path": to_relative_path(workspace_root, file_path),
                "index": cell_index,
                "cellType": cell.get("cell_type", "unknown"),
                "source": source,
                "executionResult": exec_result,
            }

        raise ValueError(f"Unknown action: {action}. Use list_cells, get_cell, or execute_cell")

    return {"handler": notebook}
