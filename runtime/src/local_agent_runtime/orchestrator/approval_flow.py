from __future__ import annotations

import json
import logging
from copy import deepcopy
from typing import Any

from .output_bridges import internal_completion_gate_bridge

logger = logging.getLogger(__name__)

_APPROVAL_KIND_CAPABILITY: dict[str, str] = {
    "apply_patch": "writeFile",
    "write_file": "writeFile",
    "delete_file": "writeFile",
    "run_command": "runCommand",
    "network_access": "webFetch",
    "computer_use": "computerUse",
    "subagent_dispatch": "subagents",
    "worktree_merge": "gitWrite",
}


def _approval_payload_text(value: Any, max_chars: int = 160) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = " ".join(text.split())
    return text if len(text) <= max_chars else text[: max_chars - 3].rstrip() + "..."


def _approval_payload_preview_row(label: str, value: Any, *, max_chars: int = 160) -> dict[str, str] | None:
    text = _approval_payload_text(value, max_chars=max_chars)
    return {"label": label, "value": text} if text else None


def _approval_payload_preview(kind: Any, request: dict[str, Any]) -> list[dict[str, str]]:
    approval_kind = str(kind or "")
    explicit_preview = request.get("previewRows")
    if isinstance(explicit_preview, list):
        rows = []
        for row in explicit_preview:
            if not isinstance(row, dict):
                continue
            label = _approval_payload_text(row.get("label"), max_chars=40)
            value = _approval_payload_text(row.get("value"), max_chars=220)
            if label and value:
                rows.append({"label": label, "value": value})
        if rows:
            return rows[:5]

    if approval_kind == "run_command":
        rows = [
            _approval_payload_preview_row("命令", request.get("command"), max_chars=220),
            _approval_payload_preview_row("目录", request.get("cwd") or request.get("workspaceRoot")),
            _approval_payload_preview_row("Shell", request.get("shell")),
            _approval_payload_preview_row("原因", request.get("policyReason") or request.get("risk") or request.get("reason")),
        ]
    elif approval_kind == "computer_use":
        rows = [
            _approval_payload_preview_row("应用", request.get("app") or request.get("target") or request.get("application")),
            _approval_payload_preview_row("动作", request.get("action")),
            _approval_payload_preview_row("目标", request.get("selector") or request.get("target")),
            _approval_payload_preview_row("权限", request.get("permission") or request.get("summary"), max_chars=220),
        ]
    elif approval_kind == "subagent_dispatch":
        rows = [
            _approval_payload_preview_row("子任务", request.get("prompt"), max_chars=240),
            _approval_payload_preview_row("原因", request.get("reason") or request.get("risk")),
        ]
    elif approval_kind == "plan":
        execution_order = request.get("executionOrder")
        execution_order_text = ""
        if isinstance(execution_order, list):
            execution_order_text = " -> ".join(str(item) for item in execution_order[:12] if str(item).strip())
        subtask_count = request.get("subtaskCount")
        if subtask_count is None and isinstance(request.get("subtasks"), list):
            subtask_count = len(request["subtasks"])
        rows = [
            _approval_payload_preview_row("目标", request.get("goal"), max_chars=220),
            _approval_payload_preview_row("模式", request.get("orchestrationMode") or request.get("mode") or "plan"),
            _approval_payload_preview_row("子任务", subtask_count),
            _approval_payload_preview_row("执行顺序", execution_order_text, max_chars=220),
        ]
    else:
        rows = [
            _approval_payload_preview_row("摘要", request.get("summary") or request.get("description")),
            _approval_payload_preview_row("目标", request.get("target") or request.get("path") or request.get("url")),
            _approval_payload_preview_row("原因", request.get("reason") or request.get("risk")),
        ]
    return [row for row in rows if row is not None][:5]


class ApprovalFlowMixin:
    def _approval_resolved_payload(
        self,
        *,
        approval: dict[str, Any],
        task: dict[str, Any],
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            request = json.loads(approval.get("requestJson") or "{}")
        except json.JSONDecodeError:
            request = {}
        payload = {
            "approvalId": approval["id"],
            "taskId": task["id"],
            "kind": approval.get("kind"),
            "request": request,
            "preview": _approval_payload_preview(approval.get("kind"), request),
            "previewSections": request.get("previewSections") if isinstance(request.get("previewSections"), list) else [],
            "decision": approval.get("decision"),
            "decidedBy": approval.get("decidedBy"),
            "decidedAt": approval.get("decidedAt"),
        }
        if approval.get("kind") == "completion_review":
            payload["internal"] = True
            payload["_bridge"] = internal_completion_gate_bridge()
        comment = str(approval.get("comment") or "").strip()
        if comment:
            payload["comment"] = comment
        if extra:
            payload.update(extra)
        return payload

    def _ensure_task_running_after_approval(
        self,
        *,
        task: dict[str, Any],
        detail: str,
    ) -> dict[str, Any]:
        if task.get("status") == "running":
            return task
        self._validate_task_transition(task["status"], "running", task["id"])
        task = self._store.update_task_status(task_id=task["id"], status="running")
        self._publish(
            session_id=task["sessionId"],
            task=task,
            event_type="task.updated",
            payload={"status": "running", "detail": detail},
        )
        return task

    def request_worktree_merge_approval(self, params: dict[str, Any]) -> dict[str, Any]:
        worktree_service = getattr(self, "_worktree_service", None)
        if worktree_service is None:
            raise ValueError("WorktreeService not configured")
        result = worktree_service.request_merge_approval(params)
        approval = result["approval"]
        task = self._store.get_task({"taskId": approval["taskId"]})["task"]
        request = json.loads(approval.get("requestJson") or "{}")
        self._publish(
            session_id=task["sessionId"],
            task=task,
            event_type="approval.requested",
            payload={
                "approvalId": approval["id"],
                "taskId": task["id"],
                "kind": approval["kind"],
                "request": request,
            },
        )
        self._fire_hooks(
            "on_approval_required",
            task["sessionId"],
            task,
            extra_context={"approvalId": approval["id"], "kind": approval["kind"]},
        )
        return result

    def submit_approval(self, params: dict[str, Any]) -> dict[str, Any]:
        existing_approval = self._store.get_approval({"approvalId": params["approvalId"]})["approval"]
        existing_decision = str(existing_approval.get("decision") or "").strip()
        if existing_decision:
            task = self._store.get_task({"taskId": existing_approval["taskId"]})["task"]
            return {"approval": existing_approval, "task": task, "ignored": True}

        approval = self._store.resolve_approval(
            approval_id=params["approvalId"],
            decision=params["decision"],
        )
        comment = str(params.get("comment") or params.get("reason") or params.get("message") or "").strip()
        if comment:
            approval = {**approval, "comment": comment}
        task = self._store.get_task({"taskId": approval["taskId"]})["task"]
        if (
            task["status"] in {"cancelled", "completed", "failed"}
            and approval.get("kind") != "worktree_merge"
        ):
            self._publish(
                session_id=task["sessionId"],
                task=task,
                event_type="approval.resolved",
                payload=self._approval_resolved_payload(
                    approval=approval,
                    task=task,
                    extra={
                        "ignored": True,
                        "taskStatus": task["status"],
                    },
                ),
            )
            return {"approval": approval, "task": task, "ignored": True}
        if task["status"] == "paused":
            self._publish(
                session_id=task["sessionId"],
                task=task,
                event_type="approval.resolved",
                payload=self._approval_resolved_payload(
                    approval=approval,
                    task=task,
                    extra={
                        "deferred": True,
                        "taskStatus": task["status"],
                    },
                ),
            )
            return {"approval": approval, "task": task, "deferred": True}
        if approval.get("kind") == "worktree_merge":
            return self._submit_worktree_merge_approval(approval=approval, task=task)
        if approval.get("kind") == "completion_review":
            self._publish(
                session_id=task["sessionId"],
                task=task,
                event_type="approval.resolved",
                payload=self._approval_resolved_payload(
                    approval=approval,
                    task=task,
                    extra={
                        "ignored": True,
                        "internal": True,
                        "taskStatus": task.get("status"),
                    },
                ),
                visibility="trace",
            )
            return {"approval": approval, "task": task, "ignored": True}
        pending_state = self._load_pending_react_state(approval["taskId"])
        child_task = self._blocked_child_collaboration_for_runtime_task(approval=approval, runtime_task=task)
        pending_dag_state = self._load_pending_dag_state(task["id"])
        if (
            pending_state is None
            and child_task is None
            and pending_dag_state is None
            and approval.get("kind") in {"plan", "run_command", "apply_patch", "write_file"}
        ):
            self._publish(
                session_id=task["sessionId"],
                task=task,
                event_type="approval.resolved",
                payload=self._approval_resolved_payload(
                    approval=approval,
                    task=task,
                    extra={
                        "ignored": True,
                        "taskStatus": task.get("status"),
                        "reason": "No pending ReAct state exists for this legacy approval.",
                    },
                ),
                visibility="trace",
            )
            return {"approval": approval, "task": task, "ignored": True}
        if approval["decision"] == "approved" and child_task is None:
            task = self._ensure_task_running_after_approval(
                task=task,
                detail="Approval accepted",
            )
        self._publish(
            session_id=task["sessionId"],
            task=task,
            event_type="approval.resolved",
            payload=self._approval_resolved_payload(approval=approval, task=task),
        )
        if pending_state is not None:
            if approval.get("kind") == "plan" and self._pending_plan_approval_state(pending_state) is not None:
                if task.get("status") != "running":
                    self._validate_task_transition(task["status"], "running", task["id"])
                    running_task = self._store.update_task_status(task_id=task["id"], status="running")
                    self._publish(
                        session_id=running_task["sessionId"],
                        task=running_task,
                        event_type="task.updated",
                        payload={"status": "running", "detail": "Plan approval resolved"},
                    )
                else:
                    running_task = task
                answered_state = self._inject_plan_approval_result(
                    session_id=running_task["sessionId"],
                    task=running_task,
                    state=pending_state,
                    approval=approval,
                )
                resumed_task = self._resume_cooperative_react(task=running_task, state=answered_state)
                return {"approval": approval, "task": resumed_task}
            if child_task is not None and self._should_resume_child_approval_in_process(params, child_task):
                try:
                    runtime_task = self._worker_runner.resume_child_approval(
                        approval=approval,
                        child_task=child_task,
                    )
                except Exception as exc:  # noqa: BLE001
                    runtime_task = self._fail_task(
                        session_id=task["sessionId"],
                        task=task,
                        summary=str(exc),
                        error_code=str(getattr(exc, "code", None) or "CHILD_WORKER_APPROVAL_RESUME_FAILED"),
                    )
                self._finalize_child_collaboration_after_approval(approval=approval, runtime_task=runtime_task)
                return {"approval": approval}
            if approval["decision"] == "approved":
                resumed_task = self._resume_react_after_approval(task=task, approval=approval)
                self._finalize_child_collaboration_after_approval(approval=approval, runtime_task=resumed_task)
                return {"approval": approval, "task": resumed_task}
            else:
                self._terminal_pending_react_tool_result(
                    session_id=task["sessionId"],
                    task=task,
                    status="rejected",
                    summary="Approval was rejected by the user.",
                    reason=comment or "The user rejected the requested action.",
                    approval=approval,
                    error_code="APPROVAL_REJECTED",
                )
                failed_task = self._fail_task(
                    session_id=task["sessionId"],
                    task=task,
                    summary="Approval was rejected by the user.",
                    error_code="APPROVAL_REJECTED",
                )
                self._finalize_child_collaboration_after_approval(approval=approval, runtime_task=failed_task)
                return {"approval": approval, "task": failed_task}
        if child_task is not None and self._should_resume_child_approval_in_process(params, child_task):
            try:
                runtime_task = self._worker_runner.resume_child_approval(
                    approval=approval,
                    child_task=child_task,
                )
            except Exception as exc:  # noqa: BLE001
                runtime_task = self._fail_task(
                    session_id=task["sessionId"],
                    task=task,
                    summary=str(exc),
                    error_code=str(getattr(exc, "code", None) or "CHILD_WORKER_APPROVAL_RESUME_FAILED"),
                )
            self._finalize_child_collaboration_after_approval(approval=approval, runtime_task=runtime_task)
            return {"approval": approval}
        if approval["decision"] == "approved" and approval["kind"] == "plan":
            task = self._resume_approved_plan(task=task, approval=approval)
        if approval["decision"] == "approved" and approval["kind"] == "run_command":
            task = self._resume_approved_command(task=task, approval=approval)
        if approval["decision"] == "approved" and approval["kind"] == "apply_patch":
            task = self._resume_approved_patch(task=task, approval=approval)
        if approval["decision"] == "approved" and approval["kind"] == "write_file":
            task = self._resume_approved_write_file(task=task, approval=approval)
        if approval["decision"] == "rejected" and approval["kind"] == "plan":
            self._terminal_pending_react_tool_result(
                session_id=task["sessionId"],
                task=task,
                status="rejected",
                summary="Plan was rejected by the user.",
                reason=comment or "The user rejected the proposed plan.",
                approval=approval,
                error_code="PLAN_REJECTED",
            )
            task = self._fail_task(
                session_id=task["sessionId"],
                task=task,
                summary="Plan was rejected by the user.",
                error_code="PLAN_REJECTED",
            )
        return {"approval": approval, "task": task}

    def allow_approval_always(self, params: dict[str, Any]) -> dict[str, Any]:
        approval_id = str(params.get("approvalId") or params.get("approval_id") or "").strip()
        if not approval_id:
            raise ValueError("approvalId is required")
        scope = str(params.get("scope") or "capability").strip() or "capability"
        if scope != "capability":
            raise ValueError("Only capability-scoped allow rules are currently supported")

        approval = self._store.get_approval({"approvalId": approval_id})["approval"]
        capability = self._capability_for_approval(approval)
        rule = {"mode": "allow", "scope": "*"}
        config_result = self._store.update_config(
            {
                "permissions": {
                    **self._merged_permissions_patch(capability=capability, rule=rule),
                },
            },
        )
        task = self._store.get_task({"taskId": approval["taskId"]})["task"]
        self._publish(
            session_id=task["sessionId"],
            task=task,
            event_type="permission.rule.created",
            payload={
                "approvalId": approval["id"],
                "taskId": task["id"],
                "approvalKind": approval.get("kind"),
                "capability": capability,
                "rule": rule,
                "scope": scope,
            },
        )

        result = self.submit_approval({"approvalId": approval_id, "decision": "approved"})
        return {
            **result,
            "config": config_result["config"],
            "capability": capability,
            "rule": rule,
            "scope": scope,
        }

    def _merged_permissions_patch(
        self,
        *,
        capability: str,
        rule: dict[str, str],
    ) -> dict[str, Any]:
        config = self._store.get_config({}).get("config", {})
        permissions = config.get("permissions") if isinstance(config, dict) else {}
        if not isinstance(permissions, dict):
            permissions = {}
        capabilities = permissions.get("capabilities")
        if not isinstance(capabilities, dict):
            capabilities = {}
        return {
            "preset": permissions.get("preset") or "balanced",
            "capabilities": {
                **deepcopy(capabilities),
                capability: deepcopy(rule),
            },
        }

    @staticmethod
    def _capability_for_approval(approval: dict[str, Any]) -> str:
        kind = str(approval.get("kind") or "").strip()
        capability = _APPROVAL_KIND_CAPABILITY.get(kind)
        if capability:
            return capability
        raise ValueError(f"Approval kind {kind!r} cannot be converted into an always-allow rule")

    def _submit_worktree_merge_approval(
        self,
        *,
        approval: dict[str, Any],
        task: dict[str, Any],
    ) -> dict[str, Any]:
        self._publish(
            session_id=task["sessionId"],
            task=task,
            event_type="approval.resolved",
            payload=self._approval_resolved_payload(approval=approval, task=task),
        )
        if approval.get("decision") != "approved":
            return {"approval": approval}

        try:
            request = json.loads(approval.get("requestJson") or "{}")
        except json.JSONDecodeError as exc:
            return {
                "approval": approval,
                "worktreeMerge": {
                    "merged": False,
                    "error": f"Worktree merge approval request is invalid: {exc}",
                },
            }

        worktree_id = request.get("worktreeId")
        target_branch = request.get("targetBranch") or "main"
        request_verification = request.get("verification") if isinstance(request.get("verification"), list) else []
        worktree_service = getattr(self, "_worktree_service", None)
        if worktree_service is None:
            merge_result = {
                "worktreeId": worktree_id,
                "merged": False,
                "targetBranch": target_branch,
                "error": "WorktreeService not configured",
                "verification": request_verification,
            }
        else:
            try:
                merge_result = worktree_service.merge({
                    "worktreeId": worktree_id,
                    "targetBranch": target_branch,
                    "approvalId": approval["id"],
                })
            except Exception as exc:  # noqa: BLE001
                merge_result = {
                    "worktreeId": worktree_id,
                    "merged": False,
                    "targetBranch": target_branch,
                    "error": str(exc),
                    "verification": request_verification,
                }

        worktree = None
        if merge_result.get("worktreeId"):
            try:
                worktree = self._store.get_worktree({"worktreeId": merge_result["worktreeId"]})["worktree"]
            except Exception:  # noqa: BLE001
                worktree = None
        event_type = "task.worktree.merged" if merge_result.get("merged") else "task.worktree.merge_failed"
        payload = {
            "status": task.get("status"),
            "worktreeId": merge_result.get("worktreeId"),
            "mergeResult": merge_result.get("result"),
            "error": merge_result.get("error"),
            "targetBranch": merge_result.get("targetBranch") or target_branch,
            "branchName": merge_result.get("branchName"),
            "verification": merge_result.get("verification") or request_verification,
            "approvalSummary": merge_result.get("approvalSummary"),
            "review": merge_result.get("review") or request.get("review"),
            "diffSummary": merge_result.get("diffSummary") or request.get("diffSummary"),
            "multiAgentWorktreeStrategy": merge_result.get("multiAgentWorktreeStrategy") or request.get("multiAgentWorktreeStrategy"),
        }
        if worktree is not None:
            payload.update({
                "activeWorktree": worktree,
                "routing": {
                    **(task.get("routing") or {}),
                    "activeWorktree": worktree,
                },
                "branchName": merge_result.get("branchName") or worktree.get("branchName"),
            })
        self._publish(
            session_id=task["sessionId"],
            task=task,
            event_type=event_type,
            payload=payload,
        )
        return {"approval": approval, "worktreeMerge": merge_result}

    def _should_resume_child_approval_in_process(self, params: dict[str, Any], child_task: dict[str, Any]) -> bool:
        if params.get("_childWorkerApprovalResume") is True:
            return False
        metadata = child_task.get("metadata") if isinstance(child_task.get("metadata"), dict) else {}
        return metadata.get("executionMode") == "process-rpc"

    def _resume_approved_plan(self, task: dict[str, Any], approval: dict[str, Any]) -> dict[str, Any]:
        """Resolve legacy plan approvals without resuming fixed orchestration."""
        state = self._load_pending_dag_state(task["id"])
        if state is None:
            logger.warning("No pending DAG state for approved plan task=%s", task["id"])
            return task
        return self._resume_dag_execution(task, state)

    def _resume_approved_command(self, task: dict[str, Any], approval: dict[str, Any]) -> dict[str, Any]:
        request = json.loads(approval.get("requestJson") or "{}")
        tool_spec = {
            "name": "run_command",
            "arguments": {
                **request,
                "approvalId": approval["id"],
            },
            "plan_step_id": "run-command",
            "start_token": "Approval accepted. Running the command now...",
        }
        runtime_task = {**task, "plan": task.get("plan") or []}
        try:
            tool_result = self._execute_tool(
                session_id=task["sessionId"],
                task=runtime_task,
                tool_spec=tool_spec,
            )
            runtime_task["plan"] = self._planner.advance(
                runtime_task["plan"],
                "run-command",
                next_step_id="summarize-findings",
            )
            runtime_task["plan"] = self._planner.advance(
                runtime_task["plan"],
                "summarize-findings",
                final_status="completed",
            )
            command_result = tool_result.get("result", {})
            command_log = command_result.get("commandLog", {})
            cmd_status = command_result.get("status")
            exit_code = command_result.get("exitCode")

            if cmd_status == "failed":
                summary = f"Command failed with status {cmd_status} and exit code {exit_code}."
                runtime_task["plan"] = self._planner.advance(
                    runtime_task["plan"],
                    "run-command",
                    final_status="failed",
                )
                runtime_task["plan"] = self._planner.advance(
                    runtime_task["plan"],
                    "summarize-findings",
                    final_status="failed",
                )
                failed_task = self._store.update_task(
                    task_id=task["id"],
                    status="failed",
                    plan=runtime_task["plan"],
                    result_summary=summary,
                    error_code="COMMAND_EXECUTION_FAILED",
                )
                runtime_task = {
                    **failed_task,
                    "plan": runtime_task["plan"],
                    "resultSummary": summary,
                }
                # Update existing active assistant message or create a failure message
                active_msg_id = runtime_task.get("activeAssistantMessageId")
                if active_msg_id:
                    failed_msg = self._store.update_message(
                        active_msg_id,
                        content=summary,
                        status="failed",
                        kind="failure",
                    )
                else:
                    failed_msg = self._store.create_message(
                        session_id=task["sessionId"],
                        task_id=runtime_task["id"],
                        role="assistant",
                        content=summary,
                        kind="failure",
                        status="failed",
                    )
                self._publish(
                    session_id=task["sessionId"],
                    task=runtime_task,
                    event_type="message.failed",
                    payload={"messageId": failed_msg["id"], "content": summary, "errorCode": "COMMAND_EXECUTION_FAILED"},
                )
                self._publish(
                    session_id=task["sessionId"],
                    task=runtime_task,
                    event_type="task.failed",
                    payload={
                        "status": "failed",
                        "plan": runtime_task["plan"],
                        "detail": summary,
                        "error_code": "COMMAND_EXECUTION_FAILED",
                        "commandLogId": command_log.get("id"),
                    },
                )
                return runtime_task

            summary = (
                f"Approved command finished with status {cmd_status} "
                f"and exit code {exit_code}."
            )
            runtime_task = self._complete_task(
                session_id=task["sessionId"],
                task=runtime_task,
                summary=summary,
                context=self._context_builder.build(
                    session_id=task["sessionId"],
                    goal=task.get("goal") or summary,
                ),
                tool_results=[tool_result],
            )
            return runtime_task
        except Exception as exc:  # noqa: BLE001
            return self._fail_task(
                session_id=task["sessionId"],
                task={**runtime_task, "sessionId": task["sessionId"]},
                summary=str(exc),
                error_code="COMMAND_EXECUTION_FAILED",
            )

    def _resume_approved_patch(self, task: dict[str, Any], approval: dict[str, Any]) -> dict[str, Any]:
        request = json.loads(approval.get("requestJson") or "{}")
        tool_spec = {
            "name": "apply_patch",
            "arguments": {
                **request,
                "approvalId": approval["id"],
            },
            "plan_step_id": "apply-patch",
            "start_token": "Approval accepted. Applying the patch now...",
        }
        runtime_task = {**task, "plan": task.get("plan") or []}
        try:
            tool_result = self._execute_tool(
                session_id=task["sessionId"],
                task=runtime_task,
                tool_spec=tool_spec,
            )
            runtime_task["plan"] = self._planner.advance(
                runtime_task["plan"],
                "apply-patch",
                next_step_id="summarize-findings",
            )
            runtime_task["plan"] = self._planner.advance(
                runtime_task["plan"],
                "summarize-findings",
                final_status="completed",
            )
            patch_result = tool_result.get("result", {})
            summary = (
                f"Approved patch finished with status {patch_result.get('status')} "
                f"and {patch_result.get('filesChanged')} file(s) changed."
            )
            runtime_task = self._complete_task(
                session_id=task["sessionId"],
                task=runtime_task,
                summary=summary,
                context=self._context_builder.build(
                    session_id=task["sessionId"],
                    goal=task.get("goal") or summary,
                ),
                tool_results=[tool_result],
            )
            return runtime_task
        except Exception as exc:  # noqa: BLE001
            return self._fail_task(
                session_id=task["sessionId"],
                task={**runtime_task, "sessionId": task["sessionId"]},
                summary=str(exc),
                error_code="PATCH_APPLY_FAILED",
            )

    def _resume_approved_write_file(self, task: dict[str, Any], approval: dict[str, Any]) -> dict[str, Any]:
        request = json.loads(approval.get("requestJson") or "{}")
        tool_spec = {
            "name": "write_file",
            "arguments": {
                **request,
                "approvalId": approval["id"],
            },
            "plan_step_id": "write-file",
            "start_token": "Approval accepted. Writing the file now...",
        }
        runtime_task = {**task, "plan": task.get("plan") or []}
        try:
            tool_result = self._execute_tool(
                session_id=task["sessionId"],
                task=runtime_task,
                tool_spec=tool_spec,
            )
            write_result = tool_result.get("result", {})
            summary = (
                f"Approved file write finished for {write_result.get('path')} "
                f"with {write_result.get('bytesWritten')} byte(s) written."
            )
            return self._complete_task(
                session_id=task["sessionId"],
                task=runtime_task,
                summary=summary,
                context=self._context_builder.build(
                    session_id=task["sessionId"],
                    goal=task.get("goal") or summary,
                ),
                tool_results=[tool_result],
            )
        except Exception as exc:  # noqa: BLE001
            return self._fail_task(
                session_id=task["sessionId"],
                task={**runtime_task, "sessionId": task["sessionId"]},
                summary=str(exc),
                error_code="WRITE_FILE_FAILED",
            )

