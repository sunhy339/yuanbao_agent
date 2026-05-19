"""write_file tool - create or replace a workspace file."""

from __future__ import annotations

import json
from typing import Any

from ._shared import (
    approval_by_id_or_none,
    current_command_policy,
    require_workspace_root,
    resolve_workspace_path,
    to_relative_path,
)
from ..policy.permission_engine import PermissionRequest as PermRequest
from ..services.write_scope_enforcement import WriteScopeEnforcer


def build_write_file_tool(policy_guard: Any, store: Any, subagent_service: Any | None = None, *, permission_engine: Any | None = None) -> dict[str, Any]:
    def write_file(params: dict[str, Any]) -> dict[str, Any]:
        workspace_root = require_workspace_root(params)
        file_path = resolve_workspace_path(policy_guard, workspace_root, params["path"])
        task_id = str(params.get("taskId") or params.get("task_id") or "").strip()
        if not task_id:
            raise ValueError("taskId is required")
        approval_id = str(params.get("approvalId") or params.get("approval_id") or "").strip() or None
        relative_path = to_relative_path(workspace_root, file_path)
        reasons = WriteScopeEnforcer(store).check_patch_in_scope(task_id, relative_path)
        if reasons:
            raise ValueError("Write scope violation: " + "; ".join(reasons))
        content = params.get("content", "")
        encoding = params.get("encoding", "utf-8")
        create_dirs = params.get("create_dirs", True)
        overwrite = params.get("overwrite", True)

        if file_path.is_file() and not overwrite:
            raise ValueError(f"File already exists and overwrite is false: {relative_path}")

        request = {
            "taskId": task_id,
            "workspaceRoot": str(workspace_root),
            "path": relative_path,
            "content": content,
            "encoding": encoding,
            "create_dirs": bool(create_dirs),
            "overwrite": bool(overwrite),
        }

        approval = approval_by_id_or_none(store, approval_id)
        if approval is not None:
            if approval["taskId"] != task_id:
                raise ValueError("Approval does not belong to the active task")
            if approval["kind"] != "write_file":
                raise ValueError("Approval kind mismatch")
            stored_request = json.loads(approval["requestJson"])
            if stored_request != request:
                raise ValueError("Approval request does not match the write_file request")
            if approval.get("decision") != "approved":
                return {
                    "status": "approval_required",
                    "approval": approval,
                    "path": relative_path,
                    "bytesWritten": 0,
                    "created": not file_path.is_file(),
                    "encoding": encoding,
                }
        elif permission_engine is not None:
            decision = permission_engine.evaluate(PermRequest(capability="writeFile", tool_name="write_file"))
            if decision.decision == "deny":
                return {
                    "status": "blocked",
                    "error": decision.reason,
                    "path": relative_path,
                    "bytesWritten": 0,
                }
            if decision.decision == "approval_required":
                if not task_id:
                    raise ValueError("taskId is required when write approval is needed")
                approval = store.create_approval(
                    task_id=task_id,
                    kind="write_file",
                    request=request,
                )
                return {
                    "status": "approval_required",
                    "approval": approval,
                    "path": relative_path,
                    "bytesWritten": 0,
                    "created": not file_path.is_file(),
                    "encoding": encoding,
                }
        elif policy_guard.requires_approval(
            "write_file",
            approval_mode=current_command_policy(store)["approvalMode"],
        ):
            approval = store.create_approval(
                task_id=task_id,
                kind="write_file",
                request=request,
            )
            return {
                "status": "approval_required",
                "approval": approval,
                "path": relative_path,
                "bytesWritten": 0,
                "created": not file_path.is_file(),
                "encoding": encoding,
            }

        if create_dirs and not file_path.parent.exists():
            file_path.parent.mkdir(parents=True, exist_ok=True)

        existing = file_path.is_file()
        new_bytes = str(content).encode(str(encoding), errors="replace")
        file_path.write_bytes(new_bytes)

        return {
            "status": "written",
            "path": relative_path,
            "bytesWritten": len(new_bytes),
            "created": not existing,
            "encoding": encoding,
        }

    return {"handler": write_file}
