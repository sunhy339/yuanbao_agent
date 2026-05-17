from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


class ApprovalFlowMixin:
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
        approval = self._store.resolve_approval(
            approval_id=params["approvalId"],
            decision=params["decision"],
        )
        task = self._store.get_task({"taskId": approval["taskId"]})["task"]
        if approval.get("kind") == "worktree_merge":
            return self._submit_worktree_merge_approval(approval=approval, task=task)
        if approval.get("kind") == "completion_review":
            return self._submit_completion_review_approval(approval=approval, task=task)
        if task["status"] in {"cancelled", "completed", "failed"}:
            self._publish(
                session_id=task["sessionId"],
                task=task,
                event_type="approval.resolved",
                payload={
                    "approvalId": approval["id"],
                    "taskId": task["id"],
                    "decision": approval["decision"],
                    "ignored": True,
                    "taskStatus": task["status"],
                },
            )
            return {"approval": approval}
        if task["status"] == "paused":
            self._publish(
                session_id=task["sessionId"],
                task=task,
                event_type="approval.resolved",
                payload={
                    "approvalId": approval["id"],
                    "taskId": task["id"],
                    "decision": approval["decision"],
                    "deferred": True,
                    "taskStatus": task["status"],
                },
            )
            return {"approval": approval}
        if approval["decision"] == "approved":
            self._validate_task_transition(task["status"], "running", task["id"])
            task = self._store.update_task_status(task_id=approval["taskId"], status="running")
            self._publish(
                session_id=task["sessionId"],
                task=task,
                event_type="task.updated",
                payload={"status": "running", "detail": "Approval accepted"},
            )
        self._publish(
            session_id=task["sessionId"],
            task=task,
            event_type="approval.resolved",
            payload={
                "approvalId": approval["id"],
                "taskId": task["id"],
                "decision": approval["decision"],
            },
        )
        pending_state = self._load_pending_react_state(approval["taskId"])
        if pending_state is not None:
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
            payload={
                "approvalId": approval["id"],
                "taskId": task["id"],
                "decision": approval["decision"],
                "decidedBy": approval.get("decidedBy"),
                "decidedAt": approval.get("decidedAt"),
            },
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
            payload={
                "approvalId": approval["id"],
                "taskId": task["id"],
                "decision": approval["decision"],
                "decidedBy": approval.get("decidedBy"),
                "decidedAt": approval.get("decidedAt"),
                "completionReviewConclusion": conclusion,
            },
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

        self._validate_task_transition(task["status"], "running", task["id"])
        task = self._store.update_task_status(task_id=task["id"], status="running")
        self._publish(
            session_id=task["sessionId"],
            task=task,
            event_type="task.updated",
            payload={"status": "running", "detail": "Completion review approved"},
        )
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
            result = self._supervisor.execute(
                state["goal"], state["context"],
                session_id=session_id, task=task,
                child_timeout_ms=self._child_subtask_timeout_ms(state["context"]),
                is_paused_fn=lambda: self._store.get_task({"taskId": task["id"]})["task"]["status"] == "paused",
                completed_ids=set(state["completed"]),
                failed_ids=set(state["failed"]),
                prior_results=state["results"],
            )

            if result.paused:
                if hasattr(self._store, "upsert_pending_dag_state"):
                    self._store.upsert_pending_dag_state(
                        task_id=task["id"],
                        session_id=session_id,
                        goal=state["goal"],
                        context=state["context"],
                        plan_json=json.dumps({"subtasks": [], "dag": {}, "execution_order": []}, ensure_ascii=False),
                        completed_ids=list(state["completed"]),
                        failed_ids=list(state["failed"]),
                        results=state["results"],
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
            result = self._swarm.execute(
                state["goal"], state["context"],
                session_id=session_id, task=task,
                child_timeout_ms=self._child_subtask_timeout_ms(state["context"]),
                is_paused_fn=lambda: self._store.get_task({"taskId": task["id"]})["task"]["status"] == "paused",
                completed_ids=set(state["completed"]),
                failed_ids=set(state["failed"]),
                prior_results=state["results"],
            )

            if result.paused:
                if hasattr(self._store, "upsert_pending_dag_state"):
                    self._store.upsert_pending_dag_state(
                        task_id=task["id"],
                        session_id=session_id,
                        goal=state["goal"],
                        context=state["context"],
                        plan_json=json.dumps({"subtasks": [], "dag": {}, "execution_order": []}, ensure_ascii=False),
                        completed_ids=list(state["completed"]),
                        failed_ids=list(state["failed"]),
                        results=state["results"],
                    )
                return task

            self._clear_pending_dag_state(task["id"])
            self._publish(
                session_id=session_id, task=task,
                event_type="task.planning.completed",
                payload={"mode": "swarm", "handoffs": result.handoff_count},
            )
            return self._complete_task(
                session_id=session_id, task=task,
                summary=result.summary, context=state["context"],
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

    def _resume_approved_advisor_tool(self, task: dict[str, Any], approval: dict[str, Any]) -> dict[str, Any]:
        request = json.loads(approval.get("requestJson") or "{}")
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
        raw_arguments = request.get("arguments")
        arguments = dict(raw_arguments) if isinstance(raw_arguments, dict) else {}
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
            result = tool_result.get("result") if isinstance(tool_result.get("result"), dict) else {}
            status = str(result.get("status") or "").strip()
            if status in {"blocked", "approval_required"} or self._tool_failed(tool_name, result):
                summary = (
                    f"Advisor evidence tool {tool_name} could not complete"
                    f" with status {status or 'failed'}."
                )
                return self._fail_task(
                    session_id=task["sessionId"],
                    task={**runtime_task, "sessionId": task["sessionId"]},
                    summary=summary,
                    error_code="ADVISOR_TOOL_EXECUTION_FAILED",
                )
            summary = f"Approved advisor evidence tool {tool_name} finished."
            return self._complete_task(
                session_id=task["sessionId"],
                task=runtime_task,
                summary=summary,
                context=context,
                tool_results=[tool_result],
                skip_reflection=True,
            )
        except Exception as exc:  # noqa: BLE001
            return self._fail_task(
                session_id=task["sessionId"],
                task={**runtime_task, "sessionId": task["sessionId"]},
                summary=str(exc),
                error_code="ADVISOR_TOOL_EXECUTION_FAILED",
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
                force_complete_after_review=True,
            )
        except Exception as exc:  # noqa: BLE001
            return self._fail_task(
                session_id=task["sessionId"],
                task={**runtime_task, "sessionId": task["sessionId"]},
                summary=str(exc),
                error_code="WRITE_FILE_FAILED",
            )

