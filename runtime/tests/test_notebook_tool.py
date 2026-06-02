from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from local_agent_runtime.policy.guard import PolicyGuard
from local_agent_runtime.store.sqlite_store import SQLiteStore
from local_agent_runtime.tools.notebook import build_notebook_tool


def _write_notebook(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "cells": [
                    {
                        "cell_type": "markdown",
                        "metadata": {},
                        "source": ["# Analysis\n"],
                    },
                    {
                        "cell_type": "code",
                        "execution_count": 3,
                        "metadata": {},
                        "outputs": [{"output_type": "stream", "text": ["rows\n"]}],
                        "source": ["print('rows')\n"],
                    },
                ],
                "metadata": {"kernelspec": {"display_name": "Python 3"}},
                "nbformat": 4,
                "nbformat_minor": 5,
            }
        ),
        encoding="utf-8",
    )


def _tool(tmp_path: Path) -> Any:
    return build_notebook_tool(PolicyGuard(approval_mode="none"), SQLiteStore(":memory:"))["handler"]


def test_notebook_list_cells_result_includes_runtime_steps(tmp_path: Path) -> None:
    notebook_path = tmp_path / "analysis.ipynb"
    _write_notebook(notebook_path)
    tool = _tool(tmp_path)

    result = tool({"workspaceRoot": str(tmp_path), "path": "analysis.ipynb", "action": "list_cells"})

    assert result["totalCells"] == 2
    assert [step["label"] for step in result["steps"]] == ["load", "parse", "list"]
    assert result["steps"][0]["summary"] == "analysis.ipynb"
    assert result["steps"][-1]["summary"] == "2 cell(s)"


def test_notebook_get_cell_result_includes_runtime_steps(tmp_path: Path) -> None:
    notebook_path = tmp_path / "analysis.ipynb"
    _write_notebook(notebook_path)
    tool = _tool(tmp_path)

    result = tool({"workspaceRoot": str(tmp_path), "path": "analysis.ipynb", "action": "get_cell", "cell_index": 1})

    assert result["cellType"] == "code"
    assert result["source"] == "print('rows')\n"
    assert [step["label"] for step in result["steps"]] == ["load", "parse", "read_cell"]
    assert result["steps"][-1]["summary"] == "cell 1 code"


def test_notebook_execute_cell_result_includes_runtime_steps(tmp_path: Path) -> None:
    notebook_path = tmp_path / "analysis.ipynb"
    _write_notebook(notebook_path)
    tool = _tool(tmp_path)

    result = tool({
        "workspaceRoot": str(tmp_path),
        "path": "analysis.ipynb",
        "action": "execute_cell",
        "cell_index": 1,
        "timeout": 5,
    })

    assert result["executionResult"]["exitCode"] == 0
    assert "rows" in result["executionResult"]["stdout"]
    assert [step["label"] for step in result["steps"]] == ["load", "parse", "read_cell", "approval", "execute"]
    assert result["steps"][-2]["summary"] == "execution allowed"
    assert result["steps"][-1]["summary"] == "exit 0"
