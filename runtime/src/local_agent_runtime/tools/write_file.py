"""write_file tool - create or replace a workspace file."""

from __future__ import annotations

import difflib
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


def _step(label: str, status: str, summary: str) -> dict[str, str]:
    return {"label": label, "status": status, "summary": summary}


def _build_write_file_diff(relative_path: str, original_text: str, new_text: str, *, is_new_file: bool) -> str:
    return "\n".join(
        difflib.unified_diff(
            original_text.splitlines(),
            new_text.splitlines(),
            fromfile="/dev/null" if is_new_file else f"a/{relative_path}",
            tofile=f"b/{relative_path}",
            lineterm="",
            n=3,
        )
    )


def build_write_file_tool(policy_guard: Any, store: Any, subagent_service: Any | None = None, *, permission_engine: Any | None = None) -> dict[str, Any]:
    def write_file(params: dict[str, Any]) -> dict[str, Any]:
        workspace_root = require_workspace_root(params)
        file_path = resolve_workspace_path(policy_guard, workspace_root, params["path"])
        task_id = str(params.get("taskId") or params.get("task_id") or "").strip()
        if not task_id:
            raise ValueError("taskId is required")
        approval_id = str(params.get("approvalId") or params.get("approval_id") or "").strip() or None
        relative_path = to_relative_path(workspace_root, file_path)
        ensure_write_path_allowed = getattr(policy_guard, "ensure_write_path_allowed", None)
        if callable(ensure_write_path_allowed):
            ensure_write_path_allowed(relative_path, operation="write_file")
        reasons = WriteScopeEnforcer(store).check_patch_in_scope(task_id, relative_path)
        if reasons:
            raise ValueError("Write scope violation: " + "; ".join(reasons))
        steps = [
            _step("resolve", "completed", relative_path),
            _step("scope", "completed", "write scope allowed"),
        ]
        content = params.get("content", "")
        encoding = params.get("encoding", "utf-8")
        create_dirs = params.get("create_dirs", True)
        overwrite = params.get("overwrite", True)
        content_text = str(content)
        existing_text = file_path.read_text(encoding=str(encoding), errors="replace") if file_path.is_file() else ""
        is_new_file = not file_path.is_file()
        diff_text = _build_write_file_diff(relative_path, existing_text, content_text, is_new_file=is_new_file)
        changed_paths = [relative_path]
        steps.append(_step("diff", "completed", f"{len(diff_text)} character(s)"))

        if file_path.is_file() and not overwrite:
            raise ValueError(f"File already exists and overwrite is false: {relative_path}")

        if not is_new_file and not diff_text:
            return {
                "status": "unchanged",
                "toolName": "write_file",
                "path": relative_path,
                "filesChanged": 0,
                "changedPaths": [],
                "diffText": "",
                "bytesWritten": 0,
                "created": False,
                "encoding": encoding,
                "steps": [
                    *steps,
                    _step("write", "skipped", "content already matches"),
                ],
            }

        request = {
            "taskId": task_id,
            "workspaceRoot": str(workspace_root),
            "path": relative_path,
            "content": content,
            "encoding": encoding,
            "create_dirs": bool(create_dirs),
            "overwrite": bool(overwrite),
            "filesChanged": 1,
            "changedPaths": changed_paths,
            "diffText": diff_text,
        }
        for key in (
            "toolUseId",
            "parentToolUseId",
            "toolGroupId",
            "toolIndex",
            "toolTotal",
            "toolOperationId",
            "toolOperationLabel",
            "toolCategory",
            "toolPhaseId",
            "toolPhaseLabel",
            "toolSemanticParentId",
            "toolSemanticParentLabel",
            "target",
            "inputSummary",
            "displayTitle",
            "displaySummary",
            "displayTarget",
            "displayKind",
        ):
            value = params.get(key)
            if value not in (None, "", [], {}):
                request[key] = value

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
                    "toolName": "write_file",
                    "approval": approval,
                    "path": relative_path,
                    "filesChanged": 1,
                    "changedPaths": changed_paths,
                    "diffText": diff_text,
                    "bytesWritten": 0,
                    "created": not file_path.is_file(),
                    "encoding": encoding,
                    "steps": [
                        *steps,
                        _step("approval", "blocked", "write_file approval required"),
                    ],
                }
        elif permission_engine is not None:
            decision = permission_engine.evaluate(PermRequest(
                capability="writeFile",
                tool_name="write_file",
                context={
                    "path": relative_path,
                    "untrustedContentSignals": params.get("untrustedContentSignals"),
                },
            ))
            if decision.decision == "deny":
                return {
                    "status": "blocked",
                    "toolName": "write_file",
                    "error": decision.reason,
                    "path": relative_path,
                    "filesChanged": 1,
                    "changedPaths": changed_paths,
                    "diffText": diff_text,
                    "bytesWritten": 0,
                    "steps": [
                        *steps,
                        _step("approval", "blocked", str(decision.reason)),
                    ],
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
                    "toolName": "write_file",
                    "approval": approval,
                    "path": relative_path,
                    "filesChanged": 1,
                    "changedPaths": changed_paths,
                    "diffText": diff_text,
                    "bytesWritten": 0,
                    "created": not file_path.is_file(),
                    "encoding": encoding,
                    "steps": [
                        *steps,
                        _step("approval", "blocked", "write_file approval required"),
                    ],
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
                "toolName": "write_file",
                "approval": approval,
                "path": relative_path,
                "filesChanged": 1,
                "changedPaths": changed_paths,
                "diffText": diff_text,
                "bytesWritten": 0,
                "created": not file_path.is_file(),
                "encoding": encoding,
                "steps": [
                    *steps,
                    _step("approval", "blocked", "write_file approval required"),
                ],
            }
        steps.append(_step("approval", "completed", "write allowed"))

        if create_dirs and not file_path.parent.exists():
            file_path.parent.mkdir(parents=True, exist_ok=True)
            steps.append(_step("mkdir", "completed", to_relative_path(workspace_root, file_path.parent)))

        existing = file_path.is_file()
        new_bytes = content_text.encode(str(encoding), errors="replace")
        file_path.write_bytes(new_bytes)
        steps.append(_step("write", "completed", f"{len(new_bytes)} byte(s)"))

        return {
            "status": "written",
            "toolName": "write_file",
            "path": relative_path,
            "filesChanged": 1,
            "changedPaths": changed_paths,
            "diffText": diff_text,
            "bytesWritten": len(new_bytes),
            "created": not existing,
            "encoding": encoding,
            "steps": steps,
        }

    return {"handler": write_file}
