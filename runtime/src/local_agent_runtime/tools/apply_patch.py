"""apply_patch tool — file patching with approval flow."""

from __future__ import annotations

import json
from typing import Any

from ._shared import (
    approval_by_id_or_none,
    apply_unified_diff_to_file,
    build_patch_request,
    build_patch_request_payload,
    build_patch_summary,
    current_command_policy,
    parse_unified_diff,
    require_workspace_root,
    validate_patch_request,
)
from ..policy.permission_engine import PermissionRequest as PermRequest
from ..services.write_scope_enforcement import WriteScopeEnforcer


def _step(label: str, status: str, summary: str) -> dict[str, str]:
    return {"label": label, "status": status, "summary": summary}


def build_apply_patch_tool(policy_guard: Any, store: Any, subagent_service: Any | None = None, *, permission_engine: Any | None = None) -> dict[str, Any]:
    def apply_patch(params: dict[str, Any]) -> dict[str, Any]:
        workspace_root = require_workspace_root(params)
        active_command_policy = current_command_policy(store)
        task_id = str(params.get("taskId") or params.get("task_id") or "").strip()
        if not task_id:
            raise ValueError("taskId is required")

        approval_id = str(params.get("approvalId") or params.get("approval_id") or "").strip() or None
        dry_run = bool(params.get("dry_run", params.get("dryRun", False)))

        try:
            patch_request = build_patch_request(policy_guard, params, workspace_root)
        except Exception as exc:  # noqa: BLE001
            patch_text = str(params.get("patchText") or params.get("patch_text") or "")
            return {
                "status": "validation_failed",
                "toolName": "apply_patch",
                "ok": False,
                "error": str(exc),
                "summary": "Patch validation failed.",
                "filesChanged": 0,
                "diffText": patch_text,
                "dryRun": True,
                "steps": [
                    _step("parse", "blocked", str(exc)),
                ],
            }
        patch_text = str(params.get("patchText") or params.get("patch_text") or patch_request["diffText"])
        steps = [
            _step("parse", "completed", f"{patch_request['filesChanged']} file(s)"),
        ]
        files = params.get("files")
        request_payload = build_patch_request_payload(
            task_id=task_id,
            workspace_root=workspace_root,
            diff_text=patch_request["diffText"],
            files_changed=patch_request["filesChanged"],
            changed_paths=patch_request["changedPaths"],
            dry_run=dry_run,
            patch_mode=patch_request["patchMode"],
            patch_text=patch_text if patch_request["patchMode"] == "patchText" else None,
            files=files if patch_request["patchMode"] == "files" else None,
        )
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
                request_payload[key] = value
        summary = build_patch_summary(patch_request["changedPaths"])

        try:
            validated_paths = validate_patch_request(policy_guard, workspace_root, patch_request["diffText"])
        except Exception as exc:  # noqa: BLE001
            return {
                "status": "validation_failed",
                "toolName": "apply_patch",
                "ok": False,
                "error": str(exc),
                "summary": summary,
                "filesChanged": patch_request["filesChanged"],
                "changedPaths": patch_request["changedPaths"],
                "diffText": patch_request["diffText"],
                "dryRun": True,
                "steps": [
                    *steps,
                    _step("validate", "blocked", str(exc)),
                ],
            }
        steps.append(_step("validate", "completed", f"{len(validated_paths)} path(s)"))
        scope_reasons: list[str] = []
        enforcer = WriteScopeEnforcer(store)
        for changed_path in validated_paths:
            scope_reasons.extend(enforcer.check_patch_in_scope(task_id, changed_path))
        if scope_reasons:
            raise ValueError("Write scope violation: " + "; ".join(scope_reasons))

        approval: dict[str, Any] | None = None
        patch: dict[str, Any] | None = None

        approval = approval_by_id_or_none(store, approval_id)
        if approval is not None:
            if approval["taskId"] != task_id:
                raise ValueError("Approval does not belong to the active task")
            if approval["kind"] != "apply_patch":
                raise ValueError("Approval kind mismatch")
            stored_request = json.loads(approval["requestJson"])
            if stored_request != request_payload:
                raise ValueError("Approval request does not match the patch")

            patch = store.find_patch(
                task_id=task_id,
                workspace_id=str(workspace_root),
                diff_text=patch_request["diffText"],
            )
            if patch is None:
                patch = store.create_patch(
                    task_id=task_id,
                    workspace_id=str(workspace_root),
                    summary=summary,
                    diff_text=patch_request["diffText"],
                    files_changed=patch_request["filesChanged"],
                    status="approved" if approval.get("decision") == "approved" else "proposed",
                )
            elif approval.get("decision") == "approved" and patch["status"] == "proposed":
                patch = store.update_patch(patch["id"], status="approved")

            if approval.get("decision") != "approved":
                return {
                    "status": "approval_required",
                    "toolName": "apply_patch",
                    "approval": approval,
                    "patch": patch,
                    "patchId": patch["id"],
                    "summary": patch["summary"],
                    "filesChanged": patch["filesChanged"],
                    "changedPaths": patch.get("changedPaths") or validated_paths,
                    "diffText": patch["diffText"],
                    "dryRun": dry_run,
                    "steps": [
                        *steps,
                        _step("approval", "blocked", "apply_patch approval required"),
                    ],
                }
        else:
            # PermissionEngine path (new) or legacy PolicyGuard path
            _skip_approval = False
            if permission_engine is not None:
                decision = permission_engine.evaluate(PermRequest(
                    capability="writeFile",
                    tool_name="apply_patch",
                    context={
                        "changedPaths": validated_paths,
                        "untrustedContentSignals": params.get("untrustedContentSignals"),
                    },
                ))
                if decision.decision == "deny":
                    return {
                        "status": "blocked",
                        "toolName": "apply_patch",
                        "error": decision.reason,
                        "summary": summary,
                        "filesChanged": patch_request["filesChanged"],
                        "changedPaths": validated_paths,
                        "diffText": patch_request["diffText"],
                        "dryRun": dry_run,
                        "steps": [
                            *steps,
                            _step("approval", "blocked", str(decision.reason)),
                        ],
                    }
                _skip_approval = decision.decision == "allow"
            else:
                _skip_approval = not policy_guard.requires_approval("apply_patch", approval_mode=active_command_policy["approvalMode"])

            if _skip_approval:
                patch = store.create_patch(
                    task_id=task_id,
                    workspace_id=str(workspace_root),
                    summary=summary,
                    diff_text=patch_request["diffText"],
                    files_changed=patch_request["filesChanged"],
                    status="approved",
                )
            else:
                patch = store.create_patch(
                    task_id=task_id,
                    workspace_id=str(workspace_root),
                    summary=summary,
                    diff_text=patch_request["diffText"],
                    files_changed=patch_request["filesChanged"],
                    status="proposed",
                )
                approval = store.create_approval(
                    task_id=task_id,
                    kind="apply_patch",
                    request=request_payload,
                )
                return {
                    "status": "approval_required",
                    "toolName": "apply_patch",
                    "approval": approval,
                    "patch": patch,
                    "patchId": patch["id"],
                    "summary": patch["summary"],
                    "filesChanged": patch["filesChanged"],
                    "changedPaths": patch.get("changedPaths") or validated_paths,
                    "diffText": patch["diffText"],
                    "dryRun": dry_run,
                    "steps": [
                        *steps,
                        _step("approval", "blocked", "apply_patch approval required"),
                    ],
                }
        steps.append(_step("approval", "completed", "patch approved"))

        if patch is None:
            raise ValueError("Failed to resolve patch state")
        if dry_run:
            return {
                "status": "dry_run",
                "toolName": "apply_patch",
                "patch": patch,
                "patchId": patch["id"],
                "summary": patch["summary"],
                "filesChanged": patch["filesChanged"],
                "changedPaths": patch.get("changedPaths") or validated_paths,
                "diffText": patch["diffText"],
                "dryRun": True,
                "steps": [
                    *steps,
                    _step("dry_run", "completed", f"{patch['filesChanged']} file(s)"),
                ],
            }

        applied_paths: list[str] = []
        parsed_patch = parse_unified_diff(patch["diffText"])
        steps.append(_step("apply", "running", f"{len(parsed_patch)} file patch(es)"))
        for file_patch in parsed_patch:
            relative_path, changed = apply_unified_diff_to_file(policy_guard, workspace_root, file_patch)
            if changed and relative_path not in applied_paths:
                applied_paths.append(relative_path)
        steps[-1] = _step("apply", "completed", f"{len(applied_paths)} changed path(s)")

        patch = store.update_patch(
            patch["id"],
            status="applied",
            summary=build_patch_summary(applied_paths or validated_paths),
            diff_text=patch["diffText"],
            files_changed=len(applied_paths) or patch["filesChanged"],
        )
        return {
            "status": "applied",
            "toolName": "apply_patch",
            "patch": patch,
            "patchId": patch["id"],
            "summary": patch["summary"],
            "filesChanged": patch["filesChanged"],
            "changedPaths": patch.get("changedPaths") or applied_paths or validated_paths,
            "diffText": patch["diffText"],
            "dryRun": False,
            "steps": steps,
        }

    return {"handler": apply_patch}
