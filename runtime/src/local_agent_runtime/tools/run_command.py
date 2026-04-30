"""run_command tool — shell command execution with approval flow."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ._shared import (
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
from ..services.command_background import BackgroundCommandRequest, get_background_command_service


def build_run_command_tool(policy_guard: Any, store: Any, subagent_service: Any | None = None) -> dict[str, Any]:
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
        timeout_ms = int(params.get("timeoutMs") or params.get("timeout_ms") or active_command_policy["commandTimeoutMs"])
        timeout_ms = max(1000, min(timeout_ms, 1_800_000))

        policy_guard.validate_command(command, active_run_command_config)

        cwd_path = Path(cwd_rel)
        cwd_abs = cwd_path.resolve() if cwd_path.is_absolute() else (workspace_root / cwd_path).resolve()
        request_task_id = task_id or None
        if approval_id and not request_task_id:
            request_task_id = store.get_approval({"approvalId": approval_id})["approval"]["taskId"]
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
            and policy_guard.requires_approval("run_command", approval_mode=active_command_policy["approvalMode"])
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
