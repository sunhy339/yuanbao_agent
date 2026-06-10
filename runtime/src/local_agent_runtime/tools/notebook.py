"""notebook tool — read and execute Jupyter notebook cells."""

from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from ..policy.permission_engine import PermissionRequest as PermRequest
from ._shared import (
    approval_by_id_or_none,
    approval_request,
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


def _execute_cell_command(path: str, cell_index: int) -> str:
    return f"notebook execute_cell {path} #cell {cell_index}"


def _source_sha256(source: str) -> str:
    return hashlib.sha256(source.encode("utf-8", errors="replace")).hexdigest()


def _step(label: str, status: str, summary: str) -> dict[str, str]:
    return {"label": label, "status": status, "summary": summary}


def _approval_for_notebook_request(
    store: Any,
    task_id: str | None,
    request: dict[str, Any],
    approval_id: str | None,
) -> dict[str, Any] | None:
    approval = approval_by_id_or_none(store, approval_id)
    if approval is not None:
        if task_id and approval["taskId"] != task_id:
            raise ValueError("Approval does not belong to the active task")
        if approval["kind"] != "run_command":
            raise ValueError("Approval kind mismatch")
        stored_request = json.loads(approval["requestJson"] or "{}")
        if stored_request != request:
            raise ValueError("Approval request does not match the notebook execution request")
        return approval

    if task_id is None or not hasattr(store, "find_approval"):
        return None
    return store.find_approval(
        task_id=task_id,
        kind="run_command",
        request=request,
    )


def build_notebook_tool(
    policy_guard: Any,
    store: Any,
    subagent_service: Any | None = None,
    *,
    permission_engine: Any | None = None,
) -> dict[str, Any]:
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
        relative_path = to_relative_path(workspace_root, file_path)
        steps = [
            _step("load", "completed", relative_path),
            _step("parse", "completed", f"{len(cells)} cell(s)"),
        ]

        if action == "list_cells":
            steps.append(_step("list", "completed", f"{len(cells)} cell(s)"))
            return {
                "status": "ok",
                "toolName": "notebook",
                "path": relative_path,
                "action": action,
                "kernel": nb.get("metadata", {}).get("kernelspec", {}).get("display_name", "unknown"),
                "totalCells": len(cells),
                "cells": [_cell_summary(i, c) for i, c in enumerate(cells)],
                "contentSource": "workspace_notebook",
                "contentTrust": "trusted",
                "steps": steps,
            }

        if action == "get_cell":
            cell_index = int(params.get("cell_index", -1))
            if cell_index < 0 or cell_index >= len(cells):
                raise ValueError(f"Invalid cell_index: {cell_index}. Notebook has {len(cells)} cells.")
            cell = cells[cell_index]
            source = "".join(cell.get("source", []))
            outputs = cell.get("outputs", [])
            steps.append(_step("read_cell", "completed", f"cell {cell_index} {cell.get('cell_type', 'unknown')}"))
            return {
                "status": "ok",
                "toolName": "notebook",
                "path": relative_path,
                "action": action,
                "index": cell_index,
                "cellType": cell.get("cell_type", "unknown"),
                "source": source,
                "outputs": outputs,
                "executionCount": cell.get("execution_count"),
                "contentSource": "workspace_notebook",
                "contentTrust": "trusted",
                "steps": steps,
            }

        if action == "execute_cell":
            cell_index = int(params.get("cell_index", -1))
            if cell_index < 0 or cell_index >= len(cells):
                raise ValueError(f"Invalid cell_index: {cell_index}. Notebook has {len(cells)} cells.")
            cell = cells[cell_index]
            source = "".join(cell.get("source", []))
            timeout = int(params.get("timeout", 60))
            timeout = max(5, min(timeout, 300))
            steps.append(_step("read_cell", "completed", f"cell {cell_index} {cell.get('cell_type', 'unknown')}"))
            task_id = str(params.get("taskId") or params.get("task_id") or "").strip()
            approval_id = str(params.get("approvalId") or params.get("approval_id") or "").strip() or None
            request = approval_request(
                task_id=task_id,
                command=_execute_cell_command(relative_path, cell_index),
                cwd=".",
                shell="python",
                timeout_ms=timeout * 1000,
                workspace_root=str(workspace_root),
                background=False,
            )
            request.update(
                {
                    "toolName": "notebook",
                    "notebookAction": "execute_cell",
                    "path": relative_path,
                    "cellIndex": cell_index,
                    "cellType": cell.get("cell_type", "unknown"),
                    "sourceSha256": _source_sha256(source),
                    "sourcePreview": source[:500] + ("..." if len(source) > 500 else ""),
                    "reason": "Notebook cell execution runs Python code in a subprocess.",
                }
            )
            approval = _approval_for_notebook_request(store, task_id or None, request, approval_id)
            if approval is not None:
                if approval.get("decision") != "approved":
                    return {
                        "status": "approval_required",
                        "toolName": "notebook",
                        "approval": approval,
                        "path": relative_path,
                        "action": action,
                        "index": cell_index,
                        "cellType": cell.get("cell_type", "unknown"),
                        "steps": [
                            *steps,
                            _step("approval", "blocked", "notebook execution approval required"),
                        ],
                    }
            elif permission_engine is not None:
                decision = permission_engine.evaluate(
                    PermRequest(
                        capability="runCommand",
                        tool_name="notebook",
                        context={
                            "path": relative_path,
                            "action": action,
                            "cellIndex": cell_index,
                            "untrustedContentSignals": params.get("untrustedContentSignals"),
                        },
                    )
                )
                if decision.decision == "deny":
                    return {
                        "status": "blocked",
                        "toolName": "notebook",
                        "error": decision.reason,
                        "path": relative_path,
                        "action": action,
                        "index": cell_index,
                        "steps": [
                            *steps,
                            _step("approval", "blocked", str(decision.reason)),
                        ],
                    }
                if decision.decision == "approval_required":
                    if not task_id:
                        raise ValueError("taskId is required when notebook execution approval is needed")
                    approval = store.create_approval(
                        task_id=task_id,
                        kind="run_command",
                        request=request,
                    )
                    return {
                        "status": "approval_required",
                        "toolName": "notebook",
                        "approval": approval,
                        "path": relative_path,
                        "action": action,
                        "index": cell_index,
                        "cellType": cell.get("cell_type", "unknown"),
                        "steps": [
                            *steps,
                            _step("approval", "blocked", "notebook execution approval required"),
                        ],
                    }
            elif policy_guard.requires_approval("run_command"):
                if not task_id:
                    raise ValueError("taskId is required when notebook execution approval is needed")
                approval = store.create_approval(
                    task_id=task_id,
                    kind="run_command",
                    request=request,
                )
                return {
                    "status": "approval_required",
                    "toolName": "notebook",
                    "approval": approval,
                    "path": relative_path,
                    "action": action,
                    "index": cell_index,
                    "cellType": cell.get("cell_type", "unknown"),
                    "steps": [
                        *steps,
                        _step("approval", "blocked", "notebook execution approval required"),
                    ],
                }
            steps.append(_step("approval", "completed", "execution allowed"))
            exec_result = _execute_cell(source, timeout=timeout)
            steps.append(_step("execute", "completed", f"exit {exec_result.get('exitCode')}"))
            return {
                "status": "ok",
                "toolName": "notebook",
                "path": relative_path,
                "action": action,
                "index": cell_index,
                "cellType": cell.get("cell_type", "unknown"),
                "source": source,
                "executionResult": exec_result,
                "contentSource": "workspace_notebook",
                "contentTrust": "trusted",
                "steps": steps,
            }

        raise ValueError(f"Unknown action: {action}. Use list_cells, get_cell, or execute_cell")

    return {"handler": notebook}
