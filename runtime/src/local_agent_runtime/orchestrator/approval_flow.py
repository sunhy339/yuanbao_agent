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
        if task["status"] in {"cancelled", "completed", "failed"}:
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
            return self._submit_completion_review_approval(approval=approval, task=task)
        if approval.get("kind") == "advisor_tool" and self._is_tool_recovery_approval(approval):
            self._publish(
                session_id=task["sessionId"],
                task=task,
                event_type="approval.resolved",
                payload=self._approval_resolved_payload(approval=approval, task=task),
            )
            if approval["decision"] == "approved":
                task = self._resume_approved_advisor_tool(task=task, approval=approval)
            return {"approval": approval, "task": task}
        if approval["decision"] == "approved":
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
        pending_state = self._load_pending_react_state(approval["taskId"])
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
            child_task = self._blocked_child_collaboration_for_runtime_task(approval=approval, runtime_task=task)
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
            else:
                failed_task = self._fail_task(
                    session_id=task["sessionId"],
                    task=task,
                    summary="Approval was rejected by the user.",
                    error_code="APPROVAL_REJECTED",
                )
                self._finalize_child_collaboration_after_approval(approval=approval, runtime_task=failed_task)
            return {"approval": approval}
        if approval["decision"] == "rejected" and approval["kind"] in {"run_command", "advisor_tool"}:
            try:
                request = json.loads(approval.get("requestJson") or "{}")
            except json.JSONDecodeError:
                request = {}
            advisor_evidence = request.get("advisorEvidence") if isinstance(request, dict) else None
            if not isinstance(advisor_evidence, dict):
                return {"approval": approval, "task": task}
            self._publish_advisor_evidence_executor_event_from_approval(
                session_id=task["sessionId"],
                task=task,
                approval=approval,
                request=request,
                status="rejected",
                transition="approval_rejected",
            )
            summary = (
                "Advisor-requested evidence command was rejected by the user."
                if approval["kind"] == "run_command"
                else "Advisor-requested evidence tool was rejected by the user."
            )
            task = self._fail_task(
                session_id=task["sessionId"],
                task=task,
                summary=summary,
                error_code="APPROVAL_REJECTED",
            )
            return {"approval": approval, "task": task}
        if approval["decision"] == "approved" and approval["kind"] == "plan":
            task = self._resume_approved_plan(task=task, approval=approval)
        if approval["decision"] == "approved" and approval["kind"] == "run_command":
            task = self._resume_approved_command(task=task, approval=approval)
        if approval["decision"] == "approved" and approval["kind"] == "advisor_tool":
            task = self._resume_approved_advisor_tool(task=task, approval=approval)
        if approval["decision"] == "approved" and approval["kind"] == "apply_patch":
            task = self._resume_approved_patch(task=task, approval=approval)
        if approval["decision"] == "approved" and approval["kind"] == "write_file":
            task = self._resume_approved_write_file(task=task, approval=approval)
        if approval["decision"] == "rejected" and approval["kind"] == "plan":
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

    @staticmethod
    def _is_tool_recovery_approval(approval: dict[str, Any]) -> bool:
        try:
            request = json.loads(approval.get("requestJson") or "{}")
        except json.JSONDecodeError:
            return False
        return isinstance(request, dict) and bool(request.get("toolRecoveryAction"))

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

    def _submit_completion_review_approval(
        self,
        *,
        approval: dict[str, Any],
        task: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            request = json.loads(approval.get("requestJson") or "{}")
        except json.JSONDecodeError:
            request = {}
        conclusion = self._completion_review_conclusion_payload(approval=approval, request=request)
        self._publish(
            session_id=task["sessionId"],
            task=task,
            event_type="approval.resolved",
            payload=self._approval_resolved_payload(
                approval=approval,
                task=task,
                extra={
                    "completionReviewConclusion": conclusion,
                },
            ),
        )
        summary = str(request.get("summary") or task.get("resultSummary") or task.get("summary") or "")
        if approval.get("decision") != "approved":
            failed_task = self._fail_task(
                session_id=task["sessionId"],
                task=task,
                summary="Completion review was rejected by the user.",
                error_code="COMPLETION_REVIEW_REJECTED",
                structured_result=self._completion_review_structured_result(
                    request=request,
                    conclusion=conclusion,
                    status="rejected",
                ),
            )
            return {"approval": approval, "task": failed_task}

        if task.get("status") != "waiting_approval":
            return {"approval": approval, "task": task}

        task = self._ensure_task_running_after_approval(
            task=task,
            detail="Completion review approved",
        )
        if self._completion_review_should_continue(request=request, conclusion=conclusion):
            continued_task = self._continue_after_blocking_completion_review(
                task=task,
                request=request,
                conclusion=conclusion,
                summary=summary,
            )
            return {"approval": approval, "task": continued_task}

        completed_task = self._complete_task(
            session_id=task["sessionId"],
            task=task,
            summary=summary or "Completion review approved.",
            context={
                "_allow_summary_only_completion": True,
                "completionReviewConclusion": conclusion,
            },
            tool_results=[],
            skip_reflection=True,
            force_complete_after_review=True,
        )
        return {"approval": approval, "task": completed_task}

    @staticmethod
    def _completion_review_gate_status(request: dict[str, Any]) -> str:
        structured = request.get("structuredResult") if isinstance(request.get("structuredResult"), dict) else {}
        gate = structured.get("completionGate") if isinstance(structured.get("completionGate"), dict) else {}
        return str(gate.get("status") or request.get("gateStatus") or "").strip()

    @staticmethod
    def _completion_review_advisor_says_incomplete(request: dict[str, Any]) -> bool:
        evidence = request.get("completionEvidence") if isinstance(request.get("completionEvidence"), dict) else {}
        advisor = evidence.get("completionAdvisor") if isinstance(evidence.get("completionAdvisor"), dict) else {}
        payload = advisor.get("payload") if isinstance(advisor.get("payload"), dict) else {}
        return advisor.get("accepted") is True and payload.get("is_complete") is False

    def _completion_review_should_continue(self, *, request: dict[str, Any], conclusion: dict[str, Any]) -> bool:
        gate_status = (
            self._completion_review_gate_status(request)
            or str(conclusion.get("gateStatus") or "").strip()
        )
        blocking_gate_statuses = {
            "advisor_needs_review",
            "advisor_evidence_requested",
            "needs_workspace_evidence",
            "waiting_runtime_work",
        }
        if gate_status in blocking_gate_statuses:
            return True
        return self._completion_review_advisor_says_incomplete(request)

    def _continue_after_blocking_completion_review(
        self,
        *,
        task: dict[str, Any],
        request: dict[str, Any],
        conclusion: dict[str, Any],
        summary: str,
    ) -> dict[str, Any]:
        structured_result = self._completion_review_structured_result(
            request=request,
            conclusion=conclusion,
            status="waiting_runtime_work",
        )
        gate_status = (
            self._completion_review_gate_status(request)
            or str(conclusion.get("gateStatus") or "").strip()
            or "completion_review_requires_continuation"
        )
        if isinstance(structured_result, dict):
            gate = structured_result.get("completionGate") if isinstance(structured_result.get("completionGate"), dict) else {}
            evidence = (
                structured_result.get("completionEvidence")
                if isinstance(structured_result.get("completionEvidence"), dict)
                else {}
            )
            structured_result = {
                **structured_result,
                "status": "waiting_runtime_work",
                "completionReview": conclusion,
                "completionGate": {
                    **gate,
                    "status": gate_status,
                    "decision": "continue_after_review",
                    "reason": (
                        gate.get("reason")
                        or request.get("reason")
                        or "Completion review confirmed more runtime work is required."
                    ),
                    "reviewConclusion": conclusion,
                },
                "completionEvidence": {
                    **evidence,
                    "reviewConclusion": conclusion,
                },
            }
        continued = self._store.update_task(
            task_id=task["id"],
            status="running",
            plan=task.get("plan") or [],
            summary=summary,
            result_summary=summary,
            structured_result=structured_result,
        )
        runtime_task = {
            **continued,
            "plan": task.get("plan") or [],
            "resultSummary": summary,
        }
        self._publish(
            session_id=runtime_task["sessionId"],
            task=runtime_task,
            event_type="agent.decision.completion",
            payload={
                "decision": "continue_after_review",
                "completionReviewConclusion": conclusion,
                "gateStatus": gate_status,
                "reason": (
                    request.get("reason")
                    or "Completion review approved, but the completion gate still requires more work."
                ),
            },
        )
        self._publish(
            session_id=runtime_task["sessionId"],
            task=runtime_task,
            event_type="task.runtime_work_waiting",
            payload={
                "status": "running",
                "detail": "Completion review approved, continuing instead of finalizing because required evidence/work is still missing.",
                "completionGate": (structured_result or {}).get("completionGate") if isinstance(structured_result, dict) else {
                    "status": gate_status,
                    "decision": "continue_after_review",
                },
            },
        )
        self._schedule_completion_review_continuation(
            task=runtime_task,
            request=request,
            conclusion=conclusion,
            summary=summary,
            gate_status=gate_status,
        )
        return runtime_task

    def _schedule_completion_review_continuation(
        self,
        *,
        task: dict[str, Any],
        request: dict[str, Any],
        conclusion: dict[str, Any],
        summary: str,
        gate_status: str,
    ) -> bool:
        if self._completion_review_waits_for_external_runtime_work(request):
            self._publish(
                session_id=task["sessionId"],
                task=task,
                event_type="task.continuation.deferred",
                payload={
                    "status": task.get("status"),
                    "reason": "waiting_for_pending_runtime_work",
                    "gateStatus": gate_status,
                },
            )
            return False

        continuation_goal = self._completion_review_continuation_goal(
            task=task,
            request=request,
            conclusion=conclusion,
            summary=summary,
            gate_status=gate_status,
        )
        routing = self._completion_review_continuation_routing(
            task=task,
            request=request,
            conclusion=conclusion,
            gate_status=gate_status,
        )
        try:
            context = self._context_builder.build(
                session_id=task["sessionId"],
                goal=continuation_goal,
                skill_id=routing.get("skill_id") if isinstance(routing.get("skill_id"), str) else None,
                lightweight=True,
            )
            context["routing"] = routing
            context["completionReviewContinuation"] = {
                "approvalId": conclusion.get("approvalId"),
                "gateStatus": gate_status,
                "decision": conclusion.get("decision"),
                "summary": summary,
            }
            updated = self._store.update_task(
                task_id=task["id"],
                status=task.get("status") or "running",
                plan=task.get("plan") or [],
                current_step=task.get("currentStep"),
                routing=routing,
            )
            runtime_task = {**task, **updated, "routing": routing, "plan": task.get("plan") or []}
            self._publish(
                session_id=runtime_task["sessionId"],
                task=runtime_task,
                event_type="task.continuation.started",
                payload={
                    "status": runtime_task.get("status"),
                    "reason": "completion_review_requires_more_work",
                    "gateStatus": gate_status,
                    "approvalId": conclusion.get("approvalId"),
                },
            )
            publish_progress = getattr(self, "_publish_assistant_progress", None)
            if callable(publish_progress):
                publish_progress(
                    session_id=runtime_task["sessionId"],
                    task=runtime_task,
                    text="继续补齐 completion review 要求的证据和后续动作。",
                    phase="completion_review_continuation",
                    payload={
                        "gateStatus": gate_status,
                        "approvalId": conclusion.get("approvalId"),
                    },
                )
            self._start_background_message(
                session_id=runtime_task["sessionId"],
                task=runtime_task,
                goal=continuation_goal,
                context=context,
                routing=routing,
                skill_id=routing.get("skill_id") if isinstance(routing.get("skill_id"), str) else None,
            )
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to schedule completion review continuation for task=%s: %s", task.get("id"), exc)
            self._publish(
                session_id=task["sessionId"],
                task=task,
                event_type="task.continuation.failed",
                payload={
                    "status": task.get("status"),
                    "reason": str(exc),
                    "gateStatus": gate_status,
                },
            )
            return False

    def _completion_review_waits_for_external_runtime_work(self, request: dict[str, Any]) -> bool:
        evidence = self._completion_review_request_evidence(request)
        pending_approvals = self._completion_pending_approval_items(evidence)
        unresolved_children = self._completion_unresolved_child_tasks(evidence)
        return bool(pending_approvals or unresolved_children)

    @staticmethod
    def _completion_review_request_evidence(request: dict[str, Any]) -> dict[str, Any]:
        evidence = request.get("completionEvidence")
        if isinstance(evidence, dict):
            return evidence
        structured = request.get("structuredResult") if isinstance(request.get("structuredResult"), dict) else {}
        evidence = structured.get("completionEvidence") if isinstance(structured.get("completionEvidence"), dict) else {}
        return evidence if isinstance(evidence, dict) else {}

    def _completion_review_continuation_routing(
        self,
        *,
        task: dict[str, Any],
        request: dict[str, Any],
        conclusion: dict[str, Any],
        gate_status: str,
    ) -> dict[str, Any]:
        routing = deepcopy(task.get("routing") or {})
        routing["strategy"] = "react_standard"
        routing["enable_planning"] = False
        routing["continuation"] = {
            "kind": "completion_review",
            "approvalId": conclusion.get("approvalId"),
            "gateStatus": gate_status,
            "decision": conclusion.get("decision"),
        }
        workflow = dict(routing.get("mainWorkflow") or {})
        workflow["continuation"] = routing["continuation"]
        workflow.setdefault("convergence", {})
        routing["mainWorkflow"] = workflow
        structured = request.get("structuredResult") if isinstance(request.get("structuredResult"), dict) else {}
        evidence = self._completion_review_request_evidence(request)
        if evidence:
            routing["completionEvidenceSnapshot"] = {
                "status": evidence.get("status"),
                "evidenceLevel": evidence.get("evidenceLevel"),
                "counts": evidence.get("counts") if isinstance(evidence.get("counts"), dict) else {},
            }
        if structured:
            routing["completionGateSnapshot"] = structured.get("completionGate") if isinstance(structured.get("completionGate"), dict) else {}
        return routing

    @staticmethod
    def _completion_review_continuation_goal(
        *,
        task: dict[str, Any],
        request: dict[str, Any],
        conclusion: dict[str, Any],
        summary: str,
        gate_status: str,
    ) -> str:
        reason = str(request.get("reason") or "").strip()
        advisor_requested = request.get("advisorRequestedEvidence")
        if not isinstance(advisor_requested, list):
            evidence = request.get("completionEvidence") if isinstance(request.get("completionEvidence"), dict) else {}
            advisor_requested = evidence.get("advisorRequestedEvidence") if isinstance(evidence.get("advisorRequestedEvidence"), list) else []
        requested_lines: list[str] = []
        for item in advisor_requested[:5]:
            if not isinstance(item, dict):
                continue
            item_summary = str(item.get("summary") or item.get("kind") or "").strip()
            status = str(item.get("status") or "").strip()
            if item_summary:
                requested_lines.append(f"- {item_summary}" + (f" ({status})" if status else ""))
        requested_text = "\n".join(requested_lines) if requested_lines else "- Continue with the smallest sufficient runtime evidence or next action."
        original_goal = str(task.get("goal") or "").strip()
        approved = str(conclusion.get("decision") or "approved")
        return (
            "Continue the existing task after completion review.\n"
            f"Original user goal: {original_goal}\n"
            f"Review decision: {approved}\n"
            f"Completion gate: {gate_status}\n"
            f"Reason: {reason or 'The completion gate still requires more work.'}\n"
            f"Previous summary:\n{summary.strip()}\n\n"
            "Required follow-up:\n"
            f"{requested_text}\n\n"
            "Do not treat this as a new user request. Do not repeat the final answer. "
            "Use only the minimal necessary tools, then update the same task with the missing evidence or finish the work."
        )

    def _completion_review_conclusion_payload(
        self,
        *,
        approval: dict[str, Any],
        request: dict[str, Any],
    ) -> dict[str, Any]:
        structured = request.get("structuredResult") if isinstance(request.get("structuredResult"), dict) else {}
        gate = structured.get("completionGate") if isinstance(structured.get("completionGate"), dict) else {}
        completion_evidence = request.get("completionEvidence") if isinstance(request.get("completionEvidence"), dict) else {}
        return {
            "approvalId": approval.get("id"),
            "decision": approval.get("decision"),
            "decidedBy": approval.get("decidedBy") or "user",
            "decidedAt": approval.get("decidedAt"),
            "gateStatus": gate.get("status") or request.get("gateStatus"),
            "evidenceLevel": completion_evidence.get("evidenceLevel"),
            "summary": (
                "Completion review approved by user."
                if approval.get("decision") == "approved"
                else "Completion review rejected by user."
            ),
        }

    def _completion_review_structured_result(
        self,
        *,
        request: dict[str, Any],
        conclusion: dict[str, Any],
        status: str,
    ) -> dict[str, Any] | None:
        structured = request.get("structuredResult") if isinstance(request.get("structuredResult"), dict) else None
        if structured is None:
            return None
        gate = structured.get("completionGate") if isinstance(structured.get("completionGate"), dict) else {}
        evidence = structured.get("completionEvidence") if isinstance(structured.get("completionEvidence"), dict) else {}
        return {
            **structured,
            "status": status,
            "completionReview": conclusion,
            "completionGate": {
                **gate,
                "reviewConclusion": conclusion,
            },
            "completionEvidence": {
                **evidence,
                "reviewConclusion": conclusion,
            },
        }

    def _should_resume_child_approval_in_process(self, params: dict[str, Any], child_task: dict[str, Any]) -> bool:
        if params.get("_childWorkerApprovalResume") is True:
            return False
        metadata = child_task.get("metadata") if isinstance(child_task.get("metadata"), dict) else {}
        return metadata.get("executionMode") == "process-rpc"

    def _resume_approved_plan(self, task: dict[str, Any], approval: dict[str, Any]) -> dict[str, Any]:
        """Resume plan execution after approval, dispatching by orchestration mode."""
        state = self._load_pending_dag_state(task["id"])
        if state is None:
            logger.warning("No pending DAG state for approved plan task=%s", task["id"])
            return task
        mode = state.get("context", {}).get("orchestration_mode", "dag")
        if mode == "supervisor":
            return self._resume_supervisor_execution(task, state)
        if mode == "swarm":
            return self._resume_swarm_execution(task, state)
        return self._resume_dag_execution(task, state)

    def _resume_supervisor_execution(self, task: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
        """Resume supervisor execution after plan approval."""
        session_id = state["session_id"]
        try:
            from ..planner.types import plan_result_from_dict, plan_result_to_dict

            plan = plan_result_from_dict(state.get("plan") or {})
            result = self._supervisor.execute(
                state["goal"], state["context"],
                session_id=session_id, task=task,
                child_timeout_ms=self._child_subtask_timeout_ms(state["context"]),
                is_paused_fn=lambda: self._store.get_task({"taskId": task["id"]})["task"]["status"] == "paused",
                completed_ids=set(state["completed"]),
                failed_ids=set(state["failed"]),
                prior_results=state["results"],
                plan=plan,
            )

            if result.paused:
                if hasattr(self._store, "upsert_pending_dag_state"):
                    self._store.upsert_pending_dag_state(
                        task_id=task["id"],
                        session_id=session_id,
                        goal=state["goal"],
                        context=state["context"],
                        plan_json=json.dumps(plan_result_to_dict(plan), ensure_ascii=False),
                        completed_ids=result.completed or list(state["completed"]),
                        failed_ids=result.failed or list(state["failed"]),
                        results=result.results or state["results"],
                    )
                return task

            self._clear_pending_dag_state(task["id"])
            self._publish(
                session_id=session_id, task=task,
                event_type="task.planning.completed",
                payload={"mode": "supervisor", "reviews": result.review_count},
            )
            return self._complete_task(
                session_id=session_id, task=task,
                summary=result.summary, context=state["context"],
                force_complete_after_review=True,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("Supervisor resume failed for task=%s: %s", task["id"], exc, exc_info=True)
            self._clear_pending_dag_state(task["id"])
            return self._fail_task(
                session_id=session_id, task=task,
                summary=str(exc), error_code="SUPERVISOR_RESUME_FAILED",
            )

    def _resume_swarm_execution(self, task: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
        """Resume swarm execution after plan approval."""
        session_id = state["session_id"]
        try:
            from ..planner.types import plan_result_from_dict, plan_result_to_dict

            plan = plan_result_from_dict(state.get("plan") or {})
            publish_planning_thinking = getattr(self, "_publish_planning_thinking", None)
            if callable(publish_planning_thinking):
                publish_planning_thinking(
                    session_id=session_id,
                    task=task,
                    text=f"计划已批准，正在派发 {len(plan.subtasks)} 个 swarm 子任务。",
                    phase="subtasks_started",
                    mode="swarm",
                    payload={"subtaskCount": len(plan.subtasks)},
                )
            publish_swarm_subtask_event = getattr(self, "_publish_swarm_subtask_event", None)
            result = self._swarm.execute(
                state["goal"], state["context"],
                session_id=session_id, task=task,
                child_timeout_ms=self._child_subtask_timeout_ms(state["context"]),
                is_paused_fn=lambda: self._store.get_task({"taskId": task["id"]})["task"]["status"] == "paused",
                completed_ids=set(state["completed"]),
                failed_ids=set(state["failed"]),
                prior_results=state["results"],
                plan=plan,
                on_subtask_callback=(
                    lambda subtask_id, event, details: publish_swarm_subtask_event(
                        session_id=session_id,
                        task=task,
                        subtask_id=subtask_id,
                        event=event,
                        details=details,
                    )
                    if callable(publish_swarm_subtask_event)
                    else None
                ),
            )

            if result.paused:
                if hasattr(self._store, "upsert_pending_dag_state"):
                    self._store.upsert_pending_dag_state(
                        task_id=task["id"],
                        session_id=session_id,
                        goal=state["goal"],
                        context=state["context"],
                        plan_json=json.dumps(plan_result_to_dict(plan), ensure_ascii=False),
                        completed_ids=result.completed or list(state["completed"]),
                        failed_ids=result.failed or list(state["failed"]),
                        results=result.results or state["results"],
                    )
                return task

            self._clear_pending_dag_state(task["id"])
            self._publish(
                session_id=session_id, task=task,
                event_type="task.planning.completed",
                payload={"mode": "swarm", "handoffs": result.handoff_count},
            )
            if callable(publish_planning_thinking):
                publish_planning_thinking(
                    session_id=session_id,
                    task=task,
                    text="swarm 子任务已执行完成，正在合并结果。",
                    phase="synthesizing",
                    mode="swarm",
                    payload={"handoffs": result.handoff_count},
                )
            return self._complete_task(
                session_id=session_id, task=task,
                summary=result.summary, context=state["context"],
                force_complete_after_review=True,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("Swarm resume failed for task=%s: %s", task["id"], exc, exc_info=True)
            self._clear_pending_dag_state(task["id"])
            return self._fail_task(
                session_id=session_id, task=task,
                summary=str(exc), error_code="SWARM_RESUME_FAILED",
            )

    def _resume_approved_command(self, task: dict[str, Any], approval: dict[str, Any]) -> dict[str, Any]:
        request = json.loads(approval.get("requestJson") or "{}")
        self._publish_advisor_evidence_executor_event_from_approval(
            session_id=task["sessionId"],
            task=task,
            approval=approval,
            request=request,
            status="running",
            transition="execution_started",
        )
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
                self._publish_advisor_evidence_executor_event_from_approval(
                    session_id=task["sessionId"],
                    task=runtime_task,
                    approval=approval,
                    request=request,
                    status="failed",
                    transition="execution_failed",
                    result=command_result,
                )
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
            self._publish_advisor_evidence_executor_event_from_approval(
                session_id=task["sessionId"],
                task=runtime_task,
                approval=approval,
                request=request,
                status="satisfied",
                transition="execution_succeeded",
                result=command_result,
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
                force_complete_after_review=True,
            )
            return runtime_task
        except Exception as exc:  # noqa: BLE001
            self._publish_advisor_evidence_executor_event_from_approval(
                session_id=task["sessionId"],
                task=runtime_task,
                approval=approval,
                request=request,
                status="failed",
                transition="execution_failed",
                result={"status": "failed", "error": str(exc)},
            )
            return self._fail_task(
                session_id=task["sessionId"],
                task={**runtime_task, "sessionId": task["sessionId"]},
                summary=str(exc),
                error_code="COMMAND_EXECUTION_FAILED",
            )

    def _resume_approved_advisor_tool(self, task: dict[str, Any], approval: dict[str, Any]) -> dict[str, Any]:
        request = json.loads(approval.get("requestJson") or "{}")
        recovery_action = str(request.get("toolRecoveryAction") or "").strip()
        if recovery_action == "refresh_mcp_tools":
            return self._resume_approved_mcp_refresh_recovery(task=task, approval=approval, request=request)
        tool_name = str(request.get("toolName") or request.get("name") or "").strip()
        if not tool_name:
            return self._fail_task(
                session_id=task["sessionId"],
                task=task,
                summary="Advisor evidence tool approval request is missing toolName.",
                error_code="ADVISOR_TOOL_APPROVAL_INVALID",
            )
        if tool_name == "run_command":
            return self._fail_task(
                session_id=task["sessionId"],
                task=task,
                summary="Advisor evidence run_command approvals must use the run_command approval flow.",
                error_code="ADVISOR_TOOL_APPROVAL_INVALID",
            )
        self._publish_advisor_evidence_executor_event_from_approval(
            session_id=task["sessionId"],
            task=task,
            approval=approval,
            request=request,
            status="running",
            transition="execution_started",
        )
        raw_arguments = request.get("arguments")
        arguments = dict(raw_arguments) if isinstance(raw_arguments, dict) else {}
        if not self._advisor_tool_arguments_are_executable(tool_name, arguments):
            summary = f"Advisor evidence tool {tool_name} approval request is missing required arguments."
            self._publish_advisor_evidence_executor_event_from_approval(
                session_id=task["sessionId"],
                task=task,
                approval=approval,
                request=request,
                status="blocked",
                transition="execution_blocked",
                result={"status": "blocked", "error": summary},
            )
            return self._fail_task(
                session_id=task["sessionId"],
                task=task,
                summary=summary,
                error_code="ADVISOR_TOOL_APPROVAL_INVALID",
            )
        workspace_root = str(request.get("workspaceRoot") or arguments.get("workspaceRoot") or "").strip()
        context = self._context_builder.build(
            session_id=task["sessionId"],
            goal=task.get("goal") or "",
        )
        if workspace_root:
            context["workspace_root"] = workspace_root
        context.setdefault("search_config", {})
        context.setdefault("search_mode", "content")
        self._fill_tool_defaults(tool_name, arguments, context)
        arguments["approvalId"] = approval["id"]
        tool_spec = {
            "name": tool_name,
            "arguments": arguments,
            "plan_step_id": self._plan_step_for_tool(tool_name),
            "start_token": f"Approval accepted. Running advisor evidence tool: {tool_name}",
        }
        runtime_task = {**task, "plan": task.get("plan") or []}
        try:
            tool_result = self._execute_tool(
                session_id=task["sessionId"],
                task=runtime_task,
                tool_spec=tool_spec,
            )
            advisor_evidence = request.get("advisorEvidence") if isinstance(request.get("advisorEvidence"), dict) else {}
            if advisor_evidence:
                tool_result["advisorEvidence"] = {
                    key: value for key, value in advisor_evidence.items() if value not in (None, "", [], {})
                }
                tool_result["approvalId"] = approval["id"]
            result = tool_result.get("result") if isinstance(tool_result.get("result"), dict) else {}
            status = str(result.get("status") or "").strip()
            if status in {"blocked", "approval_required"} or self._tool_failed(tool_name, result):
                summary = (
                    f"Advisor evidence tool {tool_name} could not complete"
                    f" with status {status or 'failed'}."
                )
                self._publish_advisor_evidence_executor_event_from_approval(
                    session_id=task["sessionId"],
                    task=runtime_task,
                    approval=approval,
                    request=request,
                    status="failed",
                    transition="execution_failed",
                    result=result,
                )
                return self._fail_task(
                    session_id=task["sessionId"],
                    task={**runtime_task, "sessionId": task["sessionId"]},
                    summary=summary,
                    error_code="ADVISOR_TOOL_EXECUTION_FAILED",
                )
            summary = f"Approved advisor evidence tool {tool_name} finished."
            self._publish_advisor_evidence_executor_event_from_approval(
                session_id=task["sessionId"],
                task=runtime_task,
                approval=approval,
                request=request,
                status="satisfied",
                transition="execution_succeeded",
                result={
                    **result,
                    "toolCallId": tool_result.get("toolCallId") or tool_result.get("id"),
                },
            )
            return self._complete_task(
                session_id=task["sessionId"],
                task=runtime_task,
                summary=summary,
                context=context,
                tool_results=[tool_result],
                skip_reflection=True,
            )
        except Exception as exc:  # noqa: BLE001
            self._publish_advisor_evidence_executor_event_from_approval(
                session_id=task["sessionId"],
                task=runtime_task,
                approval=approval,
                request=request,
                status="failed",
                transition="execution_failed",
                result={"status": "failed", "error": str(exc)},
            )
            return self._fail_task(
                session_id=task["sessionId"],
                task={**runtime_task, "sessionId": task["sessionId"]},
                summary=str(exc),
                error_code="ADVISOR_TOOL_EXECUTION_FAILED",
            )

    @staticmethod
    def _advisor_tool_arguments_are_executable(tool_name: str, arguments: dict[str, Any]) -> bool:
        if tool_name == "write_file":
            return bool(str(arguments.get("path") or "").strip()) and "content" in arguments
        if tool_name == "apply_patch":
            return bool(str(arguments.get("patchText") or arguments.get("patch_text") or "").strip())
        return True

    def _resume_approved_mcp_refresh_recovery(
        self,
        *,
        task: dict[str, Any],
        approval: dict[str, Any],
        request: dict[str, Any],
    ) -> dict[str, Any]:
        server_id = str(request.get("serverId") or "").strip()
        params = {"serverId": server_id} if server_id and server_id != "all" else {}
        try:
            result = self.mcp_tools_refresh(params)
            self._publish(
                session_id=task["sessionId"],
                task=task,
                event_type="tool.recovery.executed",
                payload={
                    "approvalId": approval["id"],
                    "toolRecoveryAction": "refresh_mcp_tools",
                    "failedToolName": request.get("failedToolName"),
                    "toolCallId": request.get("toolCallId"),
                    "serverId": server_id or "all",
                    "result": result,
                    "advisorEvidence": request.get("advisorEvidence"),
                },
            )
            return task
        except Exception as exc:  # noqa: BLE001
            self._publish(
                session_id=task["sessionId"],
                task=task,
                event_type="tool.recovery.failed",
                payload={
                    "approvalId": approval["id"],
                    "toolRecoveryAction": "refresh_mcp_tools",
                    "failedToolName": request.get("failedToolName"),
                    "toolCallId": request.get("toolCallId"),
                    "serverId": server_id or "all",
                    "error": str(exc),
                    "advisorEvidence": request.get("advisorEvidence"),
                },
            )
            return self._fail_task(
                session_id=task["sessionId"],
                task=task,
                summary=f"Approved MCP tool refresh recovery failed: {exc}",
                error_code="TOOL_RECOVERY_EXECUTION_FAILED",
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
                force_complete_after_review=True,
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
                force_complete_after_review=True,
            )
        except Exception as exc:  # noqa: BLE001
            return self._fail_task(
                session_id=task["sessionId"],
                task={**runtime_task, "sessionId": task["sessionId"]},
                summary=str(exc),
                error_code="WRITE_FILE_FAILED",
            )

