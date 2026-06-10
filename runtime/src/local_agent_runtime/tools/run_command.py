"""run_command tool — shell command execution with approval flow."""

from __future__ import annotations

import re
import os
import shutil
from pathlib import Path
from typing import Any

from ._shared import (
    approval_by_id_or_none,
    approval_for_request,
    approval_request,
    background_requested,
    current_command_policy,
    current_run_command_config,
    normalize_cwd,
    normalize_shell,
    require_workspace_root,
    run_shell,
)
from .command_compat import CommandCompatAdapter
from ..policy.permission_engine import PermissionRequest as PermRequest
from ..services.command_background import BackgroundCommandRequest, get_background_command_service
from ..services.runtime_dependencies import resolve_node_executable
from ..services.write_scope_enforcement import WriteScopeEnforcer


_POWERSHELL_QUOTED_EXECUTABLE_RE = re.compile(r"""^(\s*)(["'])([^"']+\.(?:exe|cmd|bat|ps1))\2(\s+.*)?$""", re.IGNORECASE)
_POWERSHELL_NODE_EXECUTABLE_RE = re.compile(
    r"""(?P<prefix>(?:^|\s)&\s*)(?P<quote>["'])(?P<path>[^"']*\\node(?:\.exe)?)(?P=quote)""",
    re.IGNORECASE,
)
_POWERSHELL_BARE_NODE_RE = re.compile(r"""^(?P<prefix>\s*)(?P<command>node)(?P<suffix>(?:\s+.*)?)$""", re.IGNORECASE)


def _powershell_execution_command(command: str, shell_name: str) -> str:
    if shell_name != "powershell":
        return command
    command = CommandCompatAdapter().adapt(command, shell_name).adapted
    command = _rewrite_missing_node_executable(command)
    if command.lstrip().startswith("&"):
        return command
    match = _POWERSHELL_QUOTED_EXECUTABLE_RE.match(command)
    if not match:
        return command
    return f"{match.group(1)}& {command[len(match.group(1)):]}"


def _available_execution_shell(shell_name: str) -> str:
    if os.name == "nt" and shell_name in {"bash", "zsh"} and shutil.which(shell_name) is None:
        return "powershell"
    return shell_name


def _rewrite_missing_node_executable(command: str) -> str:
    match = _POWERSHELL_NODE_EXECUTABLE_RE.search(command)
    replacement = _available_node_executable()
    if replacement is None:
        return command
    if match:
        requested = Path(match.group("path"))
        if requested.is_file():
            return command
        quote = match.group("quote")
        replacement_text = f"{match.group('prefix')}{quote}{replacement}{quote}"
        return command[:match.start()] + replacement_text + command[match.end():]
    bare = _POWERSHELL_BARE_NODE_RE.match(command)
    if not bare:
        return command
    prefix = bare.group("prefix") or ""
    suffix = bare.group("suffix") or ""
    return f'{prefix}& "{replacement}"{suffix}'


def _available_node_executable() -> str | None:
    return resolve_node_executable()


def _step(label: str, status: str, summary: str) -> dict[str, str]:
    return {"label": label, "status": status, "summary": summary}


def _command_tool_metadata(
    *,
    tool_use_id: str | None,
    parent_tool_use_id: str | None,
    tool_group_id: str | None,
    tool_index: Any,
    tool_total: Any,
    tool_operation_id: str | None,
    tool_operation_label: str | None,
    tool_category: str | None,
    tool_phase_id: str | None,
    tool_phase_label: str | None,
    tool_semantic_parent_id: str | None,
    tool_semantic_parent_label: str | None,
    tool_target: str | None,
    tool_input_summary: str | None,
    display_title: str | None = None,
    display_summary: str | None = None,
    display_target: str | None = None,
    display_kind: str | None = None,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {"toolName": "run_command"}
    for key, value in {
        "toolUseId": tool_use_id,
        "parentToolUseId": parent_tool_use_id,
        "toolGroupId": tool_group_id,
        "toolOperationId": tool_operation_id,
        "toolOperationLabel": tool_operation_label,
        "toolCategory": tool_category,
        "toolPhaseId": tool_phase_id,
        "toolPhaseLabel": tool_phase_label,
        "toolSemanticParentId": tool_semantic_parent_id,
        "toolSemanticParentLabel": tool_semantic_parent_label,
        "target": tool_target,
        "inputSummary": tool_input_summary,
        "displayTitle": display_title,
        "displaySummary": display_summary,
        "displayTarget": display_target,
        "displayKind": display_kind,
    }.items():
        if isinstance(value, str) and value.strip():
            metadata[key] = value.strip()
    if isinstance(tool_index, int) and not isinstance(tool_index, bool):
        metadata["toolIndex"] = tool_index
    if isinstance(tool_total, int) and not isinstance(tool_total, bool):
        metadata["toolTotal"] = tool_total
    return metadata


def build_run_command_tool(policy_guard: Any, store: Any, subagent_service: Any | None = None, *, permission_engine: Any | None = None) -> dict[str, Any]:
    def run_command(params: dict[str, Any]) -> dict[str, Any]:
        workspace_root = require_workspace_root(params)
        command = str(params.get("command", "")).strip()
        if not command:
            raise ValueError("command is required")
        requested_command = command

        active_command_policy = current_command_policy(store)
        active_run_command_config = current_run_command_config(store)
        task_id = str(params.get("taskId") or params.get("task_id") or "").strip()
        tool_use_id = str(params.get("toolUseId") or params.get("tool_use_id") or "").strip() or None
        parent_tool_use_id = str(params.get("parentToolUseId") or params.get("parent_tool_use_id") or "").strip() or None
        tool_group_id = str(params.get("toolGroupId") or params.get("tool_group_id") or "").strip() or None
        tool_operation_id = str(params.get("toolOperationId") or params.get("tool_operation_id") or "").strip() or None
        tool_operation_label = str(params.get("toolOperationLabel") or params.get("tool_operation_label") or "").strip() or None
        tool_category = str(params.get("toolCategory") or params.get("tool_category") or "").strip() or None
        tool_phase_id = str(params.get("toolPhaseId") or params.get("tool_phase_id") or "").strip() or None
        tool_phase_label = str(params.get("toolPhaseLabel") or params.get("tool_phase_label") or "").strip() or None
        tool_semantic_parent_id = str(params.get("toolSemanticParentId") or params.get("tool_semantic_parent_id") or "").strip() or None
        tool_semantic_parent_label = str(params.get("toolSemanticParentLabel") or params.get("tool_semantic_parent_label") or "").strip() or None
        tool_target = str(params.get("target") or params.get("toolTarget") or params.get("tool_target") or "").strip() or None
        tool_input_summary = str(params.get("inputSummary") or params.get("input_summary") or "").strip() or None
        display_title = str(params.get("displayTitle") or params.get("display_title") or "").strip() or None
        display_summary = str(params.get("displaySummary") or params.get("display_summary") or "").strip() or None
        display_target = str(params.get("displayTarget") or params.get("display_target") or "").strip() or None
        display_kind = str(params.get("displayKind") or params.get("display_kind") or "").strip() or None
        tool_index = params.get("toolIndex", params.get("tool_index"))
        tool_total = params.get("toolTotal", params.get("tool_total"))
        stdout_callback = params.get("_stdoutCallback")
        stderr_callback = params.get("_stderrCallback")
        command_started_callback = params.get("_commandStartedCallback")
        command_log_id_sink = params.get("_commandLogIdSink")
        approval_id = str(params.get("approvalId") or params.get("approval_id") or "").strip() or None
        internal_validation = bool(params.get("internalValidation") or params.get("internal_validation"))
        background = background_requested(params)
        cwd_rel = normalize_cwd(policy_guard, workspace_root, params, store)
        shell_name = _available_execution_shell(normalize_shell(params.get("shell"), store))
        execution_command = _powershell_execution_command(requested_command, shell_name)
        timeout_ms = int(params.get("timeoutMs") or params.get("timeout_ms") or active_command_policy["commandTimeoutMs"])
        timeout_ms = max(1000, min(timeout_ms, 1_800_000))
        steps = [
            _step("resolve", "completed", f"cwd={cwd_rel}; shell={shell_name}; timeout={timeout_ms}ms"),
        ]

        allowlist_review_reason: str | None = None
        try:
            policy_guard.validate_command(requested_command, active_run_command_config)
            steps.append(_step("validate", "completed", "command policy allowed"))
        except ValueError as exc:
            reason = str(exc)
            if "allowlist" not in reason.casefold():
                raise
            allowlist_review_reason = reason
            steps.append(_step("validate", "completed", "allowlist review required"))
        permission_decision = None

        # PermissionEngine gate (new path)
        if permission_engine is not None:
            permission_decision = permission_engine.evaluate(PermRequest(
                capability="runCommand",
                tool_name="run_command",
                context={
                    "command": command,
                    "cwd": cwd_rel,
                    "untrustedContentSignals": params.get("untrustedContentSignals"),
                },
            ))
            if permission_decision.decision == "deny":
                return {
                    "status": "blocked",
                    "toolName": "run_command",
                    "error": permission_decision.reason,
                    "command": command,
                    "cwd": cwd_rel,
                    "shell": shell_name,
                    "timeoutMs": timeout_ms,
                    "background": background,
                    "steps": [
                        *steps,
                        _step("permission", "blocked", str(permission_decision.reason)),
                    ],
                }
            steps.append(_step("permission", "completed", str(permission_decision.decision)))

        cwd_path = Path(cwd_rel)
        cwd_abs = cwd_path.resolve() if cwd_path.is_absolute() else (workspace_root / cwd_path).resolve()
        request_task_id = task_id or None
        if approval_id and not request_task_id:
            approval = approval_by_id_or_none(store, approval_id)
            if approval is not None:
                request_task_id = approval["taskId"]
        if request_task_id:
            scope_reasons = WriteScopeEnforcer(store).check_command_allowed(
                request_task_id,
                command_scope=cwd_rel,
                command=requested_command,
            )
            if scope_reasons:
                raise ValueError("Write scope violation: " + "; ".join(scope_reasons))
            steps.append(_step("scope", "completed", "command scope allowed"))
        request = approval_request(
            task_id=request_task_id or "",
            command=requested_command,
            cwd=cwd_rel,
            shell=shell_name,
            timeout_ms=timeout_ms,
            workspace_root=str(workspace_root),
            background=background,
        )
        if allowlist_review_reason:
            request["policyReason"] = allowlist_review_reason
            request["policyAction"] = "approval_required"
        request.update(_command_tool_metadata(
            tool_use_id=tool_use_id,
            parent_tool_use_id=parent_tool_use_id,
            tool_group_id=tool_group_id,
            tool_index=tool_index,
            tool_total=tool_total,
            tool_operation_id=tool_operation_id,
            tool_operation_label=tool_operation_label,
            tool_category=tool_category,
            tool_phase_id=tool_phase_id,
            tool_phase_label=tool_phase_label,
            tool_semantic_parent_id=tool_semantic_parent_id,
            tool_semantic_parent_label=tool_semantic_parent_label,
            tool_target=tool_target,
            tool_input_summary=tool_input_summary,
            display_title=display_title,
            display_summary=display_summary,
            display_target=display_target,
            display_kind=display_kind,
        ))
        existing_approval = approval_for_request(store, request_task_id, request, approval_id)
        if existing_approval is not None:
            decision = existing_approval.get("decision")
            if decision == "approved":
                request_task_id = existing_approval["taskId"]
            else:
                return {
                    "status": "approval_required",
                    "toolName": "run_command",
                    "approval": existing_approval,
                    "command": command,
                    "cwd": cwd_rel,
                    "shell": shell_name,
                    "timeoutMs": timeout_ms,
                    "background": background,
                    "steps": [
                        *steps,
                        _step("approval", "blocked", "run_command approval required"),
                    ],
                }
        elif (
            allowlist_review_reason is not None
            or (
                not internal_validation
                and (
                    (permission_decision is not None and permission_decision.decision == "approval_required")
                    or (permission_engine is None and policy_guard.requires_approval("run_command", approval_mode=active_command_policy["approvalMode"]))
                )
            )
        ):
            if not request_task_id:
                raise ValueError("taskId is required when command approval is needed")
            approval = store.create_approval(
                task_id=request_task_id,
                kind="run_command",
                request=request,
            )
            return {
                "status": "approval_required",
                "toolName": "run_command",
                "approval": approval,
                "command": command,
                "cwd": cwd_rel,
                "shell": shell_name,
                "timeoutMs": timeout_ms,
                "background": background,
                "steps": [
                    *steps,
                    _step("approval", "blocked", "run_command approval required"),
                ],
            }
        steps.append(_step("approval", "completed", "command approved" if approval_id else "not required"))

        if not request_task_id:
            raise ValueError("taskId is required for command execution")

        command_log = store.create_command_log(
            task_id=request_task_id,
            command=command,
            cwd=cwd_rel,
            shell=shell_name,
            tool_metadata=_command_tool_metadata(
                tool_use_id=tool_use_id,
                parent_tool_use_id=parent_tool_use_id,
                tool_group_id=tool_group_id,
                tool_index=tool_index,
                tool_total=tool_total,
                tool_operation_id=tool_operation_id,
                tool_operation_label=tool_operation_label,
                tool_category=tool_category,
                tool_phase_id=tool_phase_id,
                tool_phase_label=tool_phase_label,
                tool_semantic_parent_id=tool_semantic_parent_id,
                tool_semantic_parent_label=tool_semantic_parent_label,
                tool_target=tool_target,
                tool_input_summary=tool_input_summary,
                display_title=display_title,
                display_summary=display_summary,
                display_target=display_target,
                display_kind=display_kind,
            ),
        )
        params["commandLogId"] = command_log["id"]
        if isinstance(command_log_id_sink, dict):
            command_log_id_sink["value"] = command_log["id"]
        steps.append(_step("log", "completed", command_log["id"]))
        if not background and callable(command_started_callback):
            command_started_callback(command_log)

        if background:
            session_id = store.get_task({"taskId": request_task_id})["task"]["sessionId"]
            service = get_background_command_service(store.database_path)
            normalized_tool_index = tool_index if isinstance(tool_index, int) and not isinstance(tool_index, bool) else None
            normalized_tool_total = tool_total if isinstance(tool_total, int) and not isinstance(tool_total, bool) else None
            bg_request = BackgroundCommandRequest(
                database_path=store.database_path,
                command_log_id=command_log["id"],
                task_id=request_task_id,
                session_id=session_id,
                tool_use_id=tool_use_id,
                parent_tool_use_id=parent_tool_use_id,
                tool_group_id=tool_group_id,
                tool_index=normalized_tool_index,
                tool_total=normalized_tool_total,
                tool_operation_id=tool_operation_id,
                tool_operation_label=tool_operation_label,
                tool_category=tool_category,
                tool_phase_id=tool_phase_id,
                tool_phase_label=tool_phase_label,
                tool_semantic_parent_id=tool_semantic_parent_id,
                tool_semantic_parent_label=tool_semantic_parent_label,
                target=tool_target,
                input_summary=tool_input_summary,
                display_title=display_title,
                display_summary=display_summary,
                display_target=display_target,
                display_kind=display_kind,
                command=command,
                cwd=cwd_rel,
                shell=shell_name,
                timeout_ms=timeout_ms,
                workspace_root=str(workspace_root),
            )
            service.emit_started_event(bg_request)
            service.submit(bg_request)
            steps.append(_step("execute", "running", "background command started"))
            return {
                "status": "running",
                "toolName": "run_command",
                "background": True,
                "commandLog": command_log,
                "stdout": "",
                "stderr": "",
                "exitCode": None,
                "durationMs": None,
                "shell": shell_name,
                "cwd": cwd_rel,
                "steps": steps,
            }

        stdout = ""
        stderr = ""
        exit_code: int | None = None
        status = "completed"
        duration_ms = 0
        command_error: Exception | None = None
        steps.append(_step("execute", "running", command))
        try:
            stdout, stderr, exit_code, status, duration_ms = run_shell(
                shell_name,
                execution_command,
                cwd_abs,
                timeout_ms,
                stdout_callback=stdout_callback if callable(stdout_callback) else None,
                stderr_callback=stderr_callback if callable(stderr_callback) else None,
            )
        except Exception as exc:  # noqa: BLE001
            command_error = exc
            stderr = str(exc)
            status = "failed"
        finally:
            steps[-1] = _step(
                "execute",
                "completed" if status == "completed" else status,
                f"exit {exit_code}" if exit_code is not None else status,
            )
            finished_at = store.now()
            stdout_path = store.write_command_artifact(command_log["id"], "stdout", stdout)
            stderr_path = store.write_command_artifact(command_log["id"], "stderr", stderr)
            command_log = store.update_command_log(
                command_log["id"],
                status=status,
                exit_code=exit_code,
                stdout_path=stdout_path,
                stderr_path=stderr_path,
                finished_at=finished_at,
            )
            steps.append(_step("artifacts", "completed", "stdout/stderr saved"))
        if command_error is not None:
            raise command_error

        return {
            "status": status,
            "toolName": "run_command",
            "commandLog": command_log,
            "stdout": stdout,
            "stderr": stderr,
            "exitCode": exit_code,
            "durationMs": duration_ms,
            "shell": shell_name,
            "cwd": cwd_rel,
            "steps": steps,
            **({"executedCommand": execution_command} if execution_command != requested_command else {}),
        }

    return {"handler": run_command}
