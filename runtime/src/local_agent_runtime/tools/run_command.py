"""run_command tool — shell command execution with approval flow."""

from __future__ import annotations

import re
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


def build_run_command_tool(policy_guard: Any, store: Any, subagent_service: Any | None = None, *, permission_engine: Any | None = None) -> dict[str, Any]:
    def run_command(params: dict[str, Any]) -> dict[str, Any]:
        workspace_root = require_workspace_root(params)
        command = str(params.get("command", "")).strip()
        if not command:
            raise ValueError("command is required")

        active_command_policy = current_command_policy(store)
        active_run_command_config = current_run_command_config(store)
        task_id = str(params.get("taskId") or params.get("task_id") or "").strip()
        approval_id = str(params.get("approvalId") or params.get("approval_id") or "").strip() or None
        internal_validation = bool(params.get("internalValidation") or params.get("internal_validation"))
        background = background_requested(params)
        cwd_rel = normalize_cwd(policy_guard, workspace_root, params, store)
        shell_name = normalize_shell(params.get("shell"), store)
        command = _powershell_execution_command(command, shell_name)
        timeout_ms = int(params.get("timeoutMs") or params.get("timeout_ms") or active_command_policy["commandTimeoutMs"])
        timeout_ms = max(1000, min(timeout_ms, 1_800_000))

        policy_guard.validate_command(command, active_run_command_config)

        # PermissionEngine gate (new path)
        if permission_engine is not None:
            decision = permission_engine.evaluate(PermRequest(capability="runCommand", tool_name="run_command"))
            if decision.decision == "deny":
                return {
                    "status": "blocked",
                    "error": decision.reason,
                    "command": command,
                    "cwd": cwd_rel,
                }

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
                command=command,
            )
            if scope_reasons:
                raise ValueError("Write scope violation: " + "; ".join(scope_reasons))
        request = approval_request(
            task_id=request_task_id or "",
            command=command,
            cwd=cwd_rel,
            shell=shell_name,
            timeout_ms=timeout_ms,
            workspace_root=str(workspace_root),
            background=background,
        )
        existing_approval = approval_for_request(store, request_task_id, request, approval_id)
        if existing_approval is not None:
            decision = existing_approval.get("decision")
            if decision == "approved":
                request_task_id = existing_approval["taskId"]
            else:
                return {
                    "status": "approval_required",
                    "approval": existing_approval,
                    "command": command,
                    "cwd": cwd_rel,
                    "shell": shell_name,
                    "timeoutMs": timeout_ms,
                    "background": background,
                }
        elif (
            not internal_validation
            and (
                (permission_engine is not None and permission_engine.evaluate(PermRequest(capability="runCommand", tool_name="run_command")).decision == "approval_required")
                or (permission_engine is None and policy_guard.requires_approval("run_command", approval_mode=active_command_policy["approvalMode"]))
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
                "approval": approval,
                "command": command,
                "cwd": cwd_rel,
                "shell": shell_name,
                "timeoutMs": timeout_ms,
                "background": background,
            }

        if not request_task_id:
            raise ValueError("taskId is required for command execution")

        command_log = store.create_command_log(
            task_id=request_task_id,
            command=command,
            cwd=cwd_rel,
            shell=shell_name,
        )

        if background:
            session_id = store.get_task({"taskId": request_task_id})["task"]["sessionId"]
            service = get_background_command_service(store.database_path)
            bg_request = BackgroundCommandRequest(
                database_path=store.database_path,
                command_log_id=command_log["id"],
                task_id=request_task_id,
                session_id=session_id,
                command=command,
                cwd=cwd_rel,
                shell=shell_name,
                timeout_ms=timeout_ms,
                workspace_root=str(workspace_root),
            )
            service.emit_started_event(bg_request)
            service.submit(bg_request)
            return {
                "status": "running",
                "background": True,
                "commandLog": command_log,
                "stdout": "",
                "stderr": "",
                "exitCode": None,
                "durationMs": None,
                "shell": shell_name,
                "cwd": cwd_rel,
            }

        stdout = ""
        stderr = ""
        exit_code: int | None = None
        status = "completed"
        duration_ms = 0
        command_error: Exception | None = None
        try:
            stdout, stderr, exit_code, status, duration_ms = run_shell(shell_name, command, cwd_abs, timeout_ms)
        except Exception as exc:  # noqa: BLE001
            command_error = exc
            stderr = str(exc)
            status = "failed"
        finally:
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
        if command_error is not None:
            raise command_error

        return {
            "status": status,
            "commandLog": command_log,
            "stdout": stdout,
            "stderr": stderr,
            "exitCode": exit_code,
            "durationMs": duration_ms,
            "shell": shell_name,
            "cwd": cwd_rel,
        }

    return {"handler": run_command}
