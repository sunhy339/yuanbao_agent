from __future__ import annotations

import hashlib
import json
import logging
import re
import subprocess
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from ..policy.permission_engine import PermissionEngine, PermissionRequest
from ..policy.tool_policy_resolver import TOOL_CAPABILITIES, ToolPolicyResolver
from ..services.runtime_dependencies import resolve_node_executable
from ..tools._shared import approval_request, normalize_shell
from ..tools.run_command import _powershell_execution_command

logger = logging.getLogger(__name__)


class _StaticAssetReferenceParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.references: list[tuple[str, str]] = []
        self._text_depth = 0
        self.visible_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {name.casefold(): value for name, value in attrs if value}
        normalized_tag = tag.casefold()
        if normalized_tag in {"script", "style", "noscript"}:
            self._text_depth += 1
        if normalized_tag == "script" and attributes.get("src"):
            self.references.append(("script", attributes["src"] or ""))
        elif normalized_tag == "link":
            rel = str(attributes.get("rel") or "").casefold()
            href = attributes.get("href")
            if href and "stylesheet" in rel:
                self.references.append(("stylesheet", href))
        elif normalized_tag == "img" and attributes.get("src"):
            self.references.append(("image", attributes["src"] or ""))
        elif normalized_tag == "a" and attributes.get("href"):
            self.references.append(("route", attributes["href"] or ""))

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() in {"script", "style", "noscript"} and self._text_depth > 0:
            self._text_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._text_depth > 0:
            return
        text = " ".join(data.split())
        if text:
            self.visible_text.append(text)


class TaskLifecycleMixin:
    def _complete_task(
        self,
        session_id: str,
        task: dict[str, Any],
        summary: str,
        *,
        context: dict[str, Any] | None = None,
        tool_results: list[dict[str, Any]] | None = None,
        skip_reflection: bool = False,
        skip_drain: bool = False,
        force_complete_after_review: bool = False,
    ) -> dict[str, Any]:
        validation = self._run_post_task_validation(
            session_id=session_id,
            task=task,
            context=context or {},
            tool_results=tool_results or [],
        )
        final_summary = self._merge_completion_summary(summary=summary, validation=validation)
        final_summary = self._completion_summary_with_required_anchors(
            summary=final_summary,
            task=task,
            context=context or {},
        )
        completion_evidence = self._build_completion_evidence(
            task=task,
            summary=final_summary,
            validation=validation,
            tool_results=tool_results or [],
            context=context or {},
        )
        completion_review = self._completion_review_conclusion(context or {})
        if completion_review:
            completion_evidence["reviewConclusion"] = completion_review
        completion_audit = self._completion_audit_context(
            task=task,
            completion_evidence=completion_evidence,
        )
        if completion_audit:
            completion_evidence["audit"] = completion_audit

        # --- Reflection phase ---
        reflection_data = None
        reflection_result = None if skip_reflection else self._reflect_on_result(
            session_id=session_id,
            task=task,
            goal=task.get("goal", ""),
            summary=final_summary,
            context=context or {},
        )
        if reflection_result is not None:
            reflection_data = self._reflector.to_dict(reflection_result)
            if reflection_result.improved_summary:
                final_summary = self._completion_summary_with_required_anchors(
                    summary=reflection_result.improved_summary,
                    task=task,
                    context=context or {},
                )
                completion_evidence["summaryPreview"] = final_summary[:500]
        # --- End reflection ---

        task["plan"] = self._planner.advance(
            task.get("plan") or [],
            "summarize-findings",
            final_status="completed",
        )
        self._validate_task_transition(task["status"], "completed", task["id"], silent=True)
        if task["status"] in {"completed", "failed", "cancelled"}:
            return {**task, "resultSummary": final_summary}
        # --- Product surface advisory ---
        self._apply_product_surface_advisor(
            session_id=session_id,
            task=task,
            summary=final_summary,
            context=context or {},
            completion_evidence=completion_evidence,
            tool_results=tool_results or [],
        )
        # --- Completion decision advisory ---
        completion_advice = self._consult_completion_advisor(
            task,
            final_summary,
            context or {},
            completion_evidence=completion_evidence,
        )
        if completion_advice is not None:
            self._record_completion_advisor_proposal(
                session_id=session_id,
                task=task,
                summary=final_summary,
                completion_advice=completion_advice,
                completion_evidence=completion_evidence,
            )
            completion_evidence["completionAdvisor"] = completion_advice
            refreshed_audit = self._completion_audit_context(
                task=task,
                completion_evidence=completion_evidence,
            )
            if refreshed_audit:
                completion_evidence["audit"] = refreshed_audit
        # Build structured result from task fields
        structured_result = {
            "summary": final_summary,
            "status": completion_evidence["status"],
            "changedFiles": task.get("changedFiles") or [],
            "testsRun": task.get("testsRun") or [],
            "risks": task.get("risks") or [],
            "keyFindings": [],
            "completionEvidence": completion_evidence,
        }
        if completion_review:
            structured_result["completionReview"] = completion_review
        completion_gate = self._completion_gate_decision(
            task=task,
            context=context or {},
            completion_evidence=completion_evidence,
            force_complete_after_review=force_complete_after_review,
        )
        if completion_gate["action"] == "review":
            pending_evidence_approvals = self._pending_advisor_evidence_approval_ids(completion_evidence)
            if completion_gate.get("decision") == "advisor_evidence_requested" and pending_evidence_approvals:
                return self._request_advisor_evidence_execution_approval(
                    session_id=session_id,
                    task=task,
                    summary=final_summary,
                    structured_result=structured_result,
                    completion_evidence=completion_evidence,
                    reason=completion_gate["reason"],
                    gate_status=completion_gate.get("gateStatus"),
                    decision=completion_gate.get("decision"),
                    approval_ids=pending_evidence_approvals,
                    skip_drain=skip_drain,
                )
            return self._request_completion_review(
                session_id=session_id,
                task=task,
                summary=final_summary,
                structured_result=structured_result,
                completion_evidence=completion_evidence,
                reason=completion_gate["reason"],
                risk=completion_gate.get("risk"),
                gate_status=completion_gate.get("gateStatus"),
                decision=completion_gate.get("decision"),
                skip_drain=skip_drain,
            )
        if completion_gate["action"] == "fail":
            return self._fail_task(
                session_id=session_id,
                task=task,
                summary=completion_gate["reason"],
                error_code="COMPLETION_EVIDENCE_INSUFFICIENT",
                structured_result=structured_result,
                skip_drain=skip_drain,
            )
        completed_task = self._store.update_task(
            task_id=task["id"],
            status="completed",
            plan=task["plan"],
            summary=final_summary,
            result_summary=final_summary,
            reflection=reflection_data,
            structured_result=structured_result,
        )
        runtime_task = {
            **completed_task,
            "plan": task["plan"],
            "resultSummary": final_summary,
        }
        logger.info("Task %s completed: summary_len=%d", task["id"], len(final_summary))
        # Update existing active assistant message or create a new one
        active_msg_id = runtime_task.get("activeAssistantMessageId")
        if active_msg_id:
            completed_msg = self._store.update_message(
                active_msg_id,
                content=final_summary,
                status="completed",
            )
        else:
            completed_msg = self._store.create_message(
                session_id=session_id,
                task_id=runtime_task["id"],
                role="assistant",
                content=final_summary,
                kind="normal",
                status="completed",
            )
        self._remember_task_result(session_id=session_id, task=runtime_task)
        self._promote_scratchpad_to_memory(session_id)
        self._consolidate_working_memories(session_id)
        self._clear_pending_react_state(task["id"])
        self._record_task_metrics(session_id=session_id, task=runtime_task, tool_results=tool_results, task_status="completed")
        # --- Decision trace: completion ---
        completion_payload = {
            "decision": "completed",
            "whyComplete": final_summary[:500],
            "completionEvidence": completion_evidence,
            "changedFiles": runtime_task.get("changedFiles") or [],
            "commands": runtime_task.get("commands") or [],
            "testsRun": runtime_task.get("verification") or [],
            "reflection": reflection_data,
            "remainingRisks": runtime_task.get("risks") or [],
        }
        if completion_advice is not None:
            completion_payload["advisorOutcome"] = completion_advice["source"]
            completion_payload["advisorAccepted"] = completion_advice["accepted"]
            if completion_advice.get("proposalRecordId"):
                completion_payload["advisorProposalId"] = completion_advice["proposalRecordId"]
            if completion_advice.get("rationale"):
                completion_payload["advisorRationale"] = completion_advice["rationale"]
            if completion_advice.get("fallback_reason"):
                completion_payload["advisorFallbackReason"] = completion_advice["fallback_reason"]
        final_completion_audit = (
            completion_evidence.get("audit")
            if isinstance(completion_evidence.get("audit"), dict)
            else completion_audit
        )
        if final_completion_audit:
            completion_payload["audit"] = final_completion_audit
        self._publish(
            session_id=session_id,
            task=runtime_task,
            event_type="agent.decision.completion",
            payload=completion_payload,
        )
        self._publish(
            session_id=session_id,
            task=runtime_task,
            event_type="message.completed",
            payload={"messageId": completed_msg["id"], "content": final_summary},
        )
        self._publish(
            session_id=session_id,
            task=runtime_task,
            event_type="task.completed",
            payload={
                "status": runtime_task["status"],
                "plan": runtime_task["plan"],
                "currentStep": runtime_task.get("currentStep"),
                "changedFiles": runtime_task.get("changedFiles") or [],
                "commands": runtime_task.get("commands") or [],
                "verification": runtime_task.get("verification") or [],
                "reflection": reflection_data,
                "summary": final_summary,
                "resultSummary": final_summary,
                "completionEvidence": completion_evidence,
                "detail": final_summary,
            },
        )
        # Fire after_task_complete hooks
        self._fire_hooks("after_task_complete", session_id, runtime_task)
        # Worker role validation: testsRun and risks should be present
        self._validate_worker_output(session_id=session_id, task=runtime_task)
        if not skip_drain:
            self._drain_session_queue(session_id)
        return runtime_task

    def _completion_gate_decision(
        self,
        *,
        task: dict[str, Any],
        context: dict[str, Any],
        completion_evidence: dict[str, Any],
        force_complete_after_review: bool,
    ) -> dict[str, str]:
        if force_complete_after_review:
            return {"action": "complete", "reason": "Completion review was approved."}
        if context.get("_allow_summary_only_completion") is True:
            return {"action": "complete", "reason": "Summary-only completion explicitly allowed."}
        counts = completion_evidence.get("counts") if isinstance(completion_evidence.get("counts"), dict) else {}
        failed_verification_count = self._completion_evidence_count(counts, "failedVerification")
        if completion_evidence.get("evidenceLevel") == "failed_verification" or failed_verification_count > 0:
            return {
                "action": "fail",
                "reason": (
                    "Completion blocked because verification failed. "
                    "Fix the failed checks before marking the task completed."
                ),
            }
        reviews_disabled = self._completion_reviews_disabled(context)
        is_write_or_verification_task = self._is_write_or_verification_task(task=task, context=context)
        if not is_write_or_verification_task:
            return {"action": "complete", "reason": "Read-only completion is allowed."}
        tool_failure_gate = self._completion_tool_failure_gate(completion_evidence)
        if tool_failure_gate is not None:
            if reviews_disabled and tool_failure_gate.get("action") == "review":
                return self._completion_reviews_disabled_failure(
                    tool_failure_gate.get("reason") or "Completion evidence requires review."
                )
            return tool_failure_gate
        acceptance_gate = self._completion_acceptance_gate(completion_evidence)
        if acceptance_gate is not None:
            if reviews_disabled and acceptance_gate.get("action") == "review":
                return self._completion_reviews_disabled_failure(
                    acceptance_gate.get("reason") or "Completion evidence requires review."
                )
            return acceptance_gate
        verification_match_gate = self._completion_verification_match_gate(completion_evidence)
        if verification_match_gate is not None:
            if reviews_disabled and verification_match_gate.get("action") == "review":
                return self._completion_reviews_disabled_failure(
                    verification_match_gate.get("reason") or "Completion evidence requires review."
                )
            return verification_match_gate
        advisor_gate = self._completion_advisor_gate(completion_evidence)
        if advisor_gate is not None:
            if reviews_disabled and advisor_gate.get("action") == "review":
                return self._completion_reviews_disabled_failure(
                    advisor_gate.get("reason") or "Completion evidence requires review."
                )
            return advisor_gate
        advisor_evidence_gate = self._completion_advisor_evidence_gate(completion_evidence)
        if advisor_evidence_gate is not None:
            if reviews_disabled and advisor_evidence_gate.get("action") == "review":
                return self._completion_reviews_disabled_failure(
                    advisor_evidence_gate.get("reason") or "Completion evidence requires review."
                )
            return advisor_evidence_gate
        if self._completion_needs_verification_review(completion_evidence):
            if reviews_disabled:
                return self._completion_reviews_disabled_failure(
                    "Write-oriented task produced runtime evidence but no passing verification."
                )
            return {
                "action": "review",
                "decision": "needs_verification",
                "gateStatus": "needs_verification",
                "risk": "write-oriented task has runtime evidence without passing verification",
                "reason": (
                    "Write-oriented task produced runtime evidence but no passing verification. "
                    "Run verification or approve the completion evidence before marking it completed."
                ),
            }
        if completion_evidence.get("evidenceLevel") != "summary_only":
            return {"action": "complete", "reason": "Runtime evidence is present."}
        if reviews_disabled:
            return self._completion_reviews_disabled_failure(
                "Write-oriented task produced only a summary without verification."
            )
        return {
            "action": "review",
            "decision": "needs_user_review",
            "gateStatus": "needs_user_review",
            "risk": "summary-only completion for write-oriented task",
            "reason": (
                "Write-oriented task produced only a natural-language summary. "
                "Verification or user review is required before marking it completed."
            ),
        }

    def _completion_reviews_disabled_failure(self, reason: str) -> dict[str, str]:
        clean_reason = reason.strip().rstrip(".")
        if not clean_reason:
            clean_reason = "Completion review is required"
        return {
            "action": "fail",
            "reason": (
                f"{clean_reason}. Completion reviews are disabled, so this write-oriented task "
                "cannot be auto-completed without passing verification or user approval."
            ),
        }

    def _completion_advisor_gate(self, completion_evidence: dict[str, Any]) -> dict[str, str] | None:
        advice = completion_evidence.get("completionAdvisor")
        if not isinstance(advice, dict) or advice.get("accepted") is not True:
            return None
        payload = advice.get("payload") if isinstance(advice.get("payload"), dict) else {}
        if payload.get("is_complete") is not False:
            return None
        confidence = advice.get("confidence")
        if not isinstance(confidence, (int, float)) or confidence < 0.75:
            return None
        blocking = [
            str(item).strip()
            for item in (payload.get("blocking_issues") or payload.get("remaining_risks") or [])
            if str(item).strip()
        ]
        reason = (
            "Completion advisor judged this task likely incomplete"
            f" (confidence {confidence:.2f})."
        )
        if blocking:
            reason = f"{reason} Blocking issue(s): {'; '.join(blocking[:3])}"
        return {
            "action": "review",
            "decision": "advisor_needs_review",
            "gateStatus": "advisor_needs_review",
            "risk": "LLM completion advisor judged task incomplete",
            "reason": reason,
        }

    def _completion_advisor_evidence_gate(self, completion_evidence: dict[str, Any]) -> dict[str, str] | None:
        requested = completion_evidence.get("advisorRequestedEvidence")
        if not isinstance(requested, list):
            return None
        missing = [
            item for item in requested
            if isinstance(item, dict)
            and item.get("blocking") is True
            and item.get("status") == "missing"
        ]
        executor_records = completion_evidence.get("advisorEvidenceExecutor")
        blocked_executors = [
            item for item in executor_records
            if isinstance(item, dict)
            and item.get("blocking") is True
            and str(item.get("status") or "").strip() in {"failed", "rejected", "blocked"}
        ] if isinstance(executor_records, list) else []
        if not missing and not blocked_executors:
            return None
        details = [
            str(item.get("summary") or item.get("kind") or "advisor-requested evidence").strip()
            for item in missing
            if str(item.get("summary") or item.get("kind") or "").strip()
        ]
        reason = "A product-surface advisor marked evidence as blocking before completion."
        if details:
            reason = f"{reason} Missing evidence: {'; '.join(details[:3])}"
        if blocked_executors:
            blocked_details = [
                str(item.get("summary") or item.get("requestKind") or item.get("toolName") or "evidence executor").strip()
                for item in blocked_executors
                if str(item.get("summary") or item.get("requestKind") or item.get("toolName") or "").strip()
            ]
            if blocked_details:
                reason = f"{reason} Blocked/failed executor: {'; '.join(blocked_details[:3])}"
        return {
            "action": "review",
            "decision": "advisor_evidence_requested",
            "gateStatus": "advisor_evidence_requested",
            "risk": "LLM product-surface advisor requested blocking evidence",
            "reason": reason,
        }

    def _completion_reviews_disabled(self, context: dict[str, Any]) -> bool:
        config = context.get("config") if isinstance(context, dict) else {}
        policy = config.get("policy") if isinstance(config, dict) else {}
        mode = str(policy.get("approvalMode") or "").strip().lower() if isinstance(policy, dict) else ""
        return mode in {"none", "never", "off"}

    def _completion_tool_failure_gate(self, completion_evidence: dict[str, Any]) -> dict[str, str] | None:
        counts = completion_evidence.get("counts") if isinstance(completion_evidence.get("counts"), dict) else {}
        failed_tool_count = self._completion_evidence_count(counts, "failedToolResults")
        if failed_tool_count <= 0:
            return None
        return {
            "action": "review",
            "decision": "needs_tool_review",
            "gateStatus": "needs_tool_review",
            "risk": "tool results include unresolved failures",
            "reason": (
                "Completion blocked because tool results include unresolved failures. "
                "Resolve the failed tool result or approve the completion evidence before marking it completed."
            ),
        }

    def _completion_acceptance_gate(self, completion_evidence: dict[str, Any]) -> dict[str, str] | None:
        counts = completion_evidence.get("counts") if isinstance(completion_evidence.get("counts"), dict) else {}
        failed_count = self._completion_evidence_count(counts, "failedAcceptanceCriteria")
        unverified_count = self._completion_evidence_count(counts, "unverifiedAcceptanceCriteria")
        if failed_count <= 0 and unverified_count <= 0:
            return None
        if failed_count > 0:
            reason = (
                "Completion blocked because explicit acceptance evidence reports unmet criteria. "
                "Resolve or review the failed criteria before marking the task completed."
            )
        else:
            reason = (
                "Completion blocked because explicit acceptance evidence does not cover every criterion. "
                "Provide coverage or approve the completion evidence before marking the task completed."
            )
        return {
            "action": "review",
            "decision": "needs_acceptance_review",
            "gateStatus": "needs_acceptance_review",
            "risk": "acceptance criteria require review",
            "reason": reason,
        }

    def _completion_verification_match_gate(self, completion_evidence: dict[str, Any]) -> dict[str, str] | None:
        if not self._completion_has_code_or_test_changes(completion_evidence):
            return None
        if not self._completion_has_any_passing_verification_signal(completion_evidence):
            return None
        requirements = completion_evidence.get("verificationRequirements")
        if isinstance(requirements, dict):
            requirement_status = str(requirements.get("status") or "").strip().casefold()
            if requirement_status == "not_required":
                return None
        if isinstance(requirements, dict):
            missing = [
                str(item)
                for item in (requirements.get("missing") or [])
                if str(item).strip()
            ]
            if missing:
                if self._completion_static_frontend_structural_check_satisfies(
                    completion_evidence,
                    missing,
                ):
                    return None
                if self._completion_advisor_accepts_verification_gap(
                    completion_evidence=completion_evidence,
                    gap_kind="framework_mismatch",
                    missing=missing,
                ):
                    return None
                return {
                    "action": "review",
                    "decision": "needs_verification",
                    "gateStatus": "needs_verification",
                    "risk": "code changes lack framework-matched verification",
                    "reason": (
                        "Completion blocked because code or test files changed, but passing verification "
                        f"does not cover required framework signal(s): {', '.join(missing)}."
                    ),
                }
        if self._completion_has_targeted_verification(completion_evidence):
            return None
        if self._completion_advisor_accepts_verification_gap(
            completion_evidence=completion_evidence,
            gap_kind="targeted_signal_missing",
            missing=[],
        ):
            return None
        return {
            "action": "review",
            "decision": "needs_verification",
            "gateStatus": "needs_verification",
            "risk": "code changes lack targeted test or build verification",
            "reason": (
                "Completion blocked because code or test files changed, but the passing verification "
                "does not include a targeted test, build, or typecheck signal."
            ),
        }

    def _completion_advisor_accepts_verification_gap(
        self,
        *,
        completion_evidence: dict[str, Any],
        gap_kind: str,
        missing: list[str],
    ) -> bool:
        if not self._completion_has_any_passing_verification_signal(completion_evidence):
            return False
        advice = completion_evidence.get("completionAdvisor")
        if not isinstance(advice, dict) or advice.get("accepted") is not True:
            return False
        payload = advice.get("payload") if isinstance(advice.get("payload"), dict) else {}
        if payload.get("is_complete") is not True:
            return False
        confidence = advice.get("confidence")
        if not isinstance(confidence, (int, float)) or confidence < 0.8:
            return False
        blocking = [
            str(item).strip()
            for item in (payload.get("blocking_issues") or [])
            if str(item).strip()
        ]
        if blocking:
            return False
        assessment = payload.get("verification_assessment")
        assessment_status = ""
        assessment_reason = ""
        if isinstance(assessment, dict):
            assessment_status = str(assessment.get("status") or "").strip().casefold()
            assessment_reason = str(assessment.get("reason") or assessment.get("summary") or "").strip()
        advisor_says_sufficient = payload.get("verification_sufficient") is True or assessment_status in {
            "sufficient",
            "acceptable",
            "covered",
            "satisfied",
            "not_required",
            "domain_sufficient",
        }
        if not advisor_says_sufficient:
            return False
        requirements = completion_evidence.get("verificationRequirements")
        if isinstance(requirements, dict):
            resolution = {
                "status": "advisor_accepted",
                "gapKind": gap_kind,
                "missing": missing,
                "confidence": confidence,
                "source": advice.get("source"),
                "proposalRecordId": advice.get("proposalRecordId"),
                "rationale": str(advice.get("rationale") or "")[:500],
            }
            if assessment_status:
                resolution["assessmentStatus"] = assessment_status
            if assessment_reason:
                resolution["assessmentReason"] = assessment_reason[:500]
            requirements["advisorResolution"] = {
                key: value
                for key, value in resolution.items()
                if value not in (None, "", [])
            }
            if requirements.get("status") == "missing":
                requirements["status"] = "advisor_accepted"
        return True

    def _completion_needs_verification_review(self, completion_evidence: dict[str, Any]) -> bool:
        if completion_evidence.get("evidenceLevel") != "runtime_evidence":
            return False
        requirements = completion_evidence.get("verificationRequirements")
        if isinstance(requirements, dict) and str(requirements.get("status") or "").strip().casefold() == "not_required":
            return False
        counts = completion_evidence.get("counts") if isinstance(completion_evidence.get("counts"), dict) else {}
        if self._completion_has_any_passing_verification_signal(completion_evidence):
            return False
        if self._completion_evidence_count(counts, "failedVerification") > 0:
            return False
        workspace_evidence_count = sum(
            self._completion_evidence_count(counts, key)
            for key in ("changedFiles", "patches")
        )
        return workspace_evidence_count > 0 or self._completion_has_workspace_tool_evidence(completion_evidence)

    def _completion_evidence_count(self, counts: dict[str, Any], key: str) -> int:
        value = counts.get(key)
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        return 0

    def _completion_has_workspace_tool_evidence(self, completion_evidence: dict[str, Any]) -> bool:
        tool_results = completion_evidence.get("toolResults")
        if not isinstance(tool_results, list):
            return False
        for item in tool_results:
            if not isinstance(item, dict):
                continue
            if item.get("name") in {"apply_patch", "write_file"}:
                changed_paths = item.get("changedPaths")
                if item["name"] == "write_file":
                    return True
                if isinstance(changed_paths, list) and changed_paths:
                    return True
        return False

    def _completion_has_code_or_test_changes(self, completion_evidence: dict[str, Any]) -> bool:
        changed_files = completion_evidence.get("changedFiles")
        if not isinstance(changed_files, list):
            return False
        return any(
            self._completion_path_requires_targeted_verification(self._completion_changed_file_path(item))
            for item in changed_files
            if isinstance(item, dict)
        )

    def _completion_changed_file_path(self, item: dict[str, Any]) -> str:
        value = item.get("path") or item.get("file") or item.get("name")
        return str(value or "").replace("\\", "/").strip()

    @staticmethod
    def _completion_changed_file_basename(item: dict[str, Any]) -> str:
        path = str(item.get("path") or item.get("file") or item.get("name") or "").replace("\\", "/").strip()
        return path.rsplit("/", 1)[-1].casefold() if path else ""

    def _completion_verification_requirements(
        self,
        *,
        task: dict[str, Any],
        context: dict[str, Any],
        changed_files: list[dict[str, Any]],
        verification: list[dict[str, Any]],
        tests_run: list[dict[str, Any]],
    ) -> dict[str, Any]:
        matched: set[str] = set()
        for item in verification:
            if item.get("status") != "passed":
                continue
            matched.update(self._completion_verification_item_families(item))
        for item in tests_run:
            if item.get("status") not in {"passed", "success", "completed"}:
                continue
            matched.update(self._completion_verification_item_families(item))
        contract = self._completion_contract_profile(task=task, context=context)
        if contract is not None and isinstance(contract.get("verificationRequirements"), list):
            required = sorted(self._completion_contract_verification_families(contract))
            if not required:
                return {
                    "required": [],
                    "matched": sorted(matched),
                    "missing": [],
                    "status": "not_required",
                    "source": "planner_profile",
                }
            missing = [family for family in required if family not in matched]
            return {
                "required": required,
                "matched": sorted(matched),
                "missing": missing,
                "status": "satisfied" if not missing else "missing",
                "source": "planner_profile",
            }
        required = sorted({
            family
            for item in changed_files
            for family in self._completion_path_verification_families(
                self._completion_changed_file_path(item)
            )
        })
        if not required:
            return {"required": [], "matched": sorted(matched), "missing": [], "status": "not_required"}
        missing = [family for family in required if family not in matched]
        return {
            "required": required,
            "matched": sorted(matched),
            "missing": missing,
            "status": "satisfied" if not missing else "missing",
        }

    def _completion_path_verification_families(self, path: str) -> set[str]:
        if not path:
            return set()
        normalized = path.casefold()
        filename = normalized.rsplit("/", 1)[-1]
        if filename in {"package.json", "package-lock.json", "pnpm-lock.yaml", "yarn.lock", "bun.lockb"}:
            return {"javascript"}
        if filename in {"vite.config.ts", "vite.config.js", "next.config.js", "next.config.ts", "tsconfig.json"}:
            return {"javascript"}
        if filename in {"pyproject.toml", "requirements.txt", "setup.py", "setup.cfg", "tox.ini", "pytest.ini"}:
            return {"python"}
        if filename in {"cargo.toml", "cargo.lock"}:
            return {"rust"}
        if filename in {"go.mod", "go.sum"}:
            return {"go"}
        if filename in {"pom.xml", "build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts"}:
            return {"jvm"}
        if filename.endswith((".csproj", ".sln")):
            return {"dotnet"}
        if not self._completion_path_requires_targeted_verification(path):
            return set()
        test_family = self._completion_test_artifact_verification_family(normalized)
        if test_family:
            return {test_family}
        if normalized.endswith((".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".vue", ".svelte")):
            return {"javascript"}
        if normalized.endswith((".py", ".pyw")):
            return {"python"}
        if normalized.endswith(".rs"):
            return {"rust"}
        if normalized.endswith(".go"):
            return {"go"}
        if normalized.endswith((".java", ".kt", ".kts")):
            return {"jvm"}
        if normalized.endswith(".cs"):
            return {"dotnet"}
        if normalized.endswith(".rb"):
            return {"ruby"}
        if normalized.endswith(".php"):
            return {"php"}
        if normalized.endswith(".swift"):
            return {"swift"}
        if normalized.endswith((".c", ".cc", ".cpp", ".h", ".hpp")):
            return {"native"}
        return {"generic"}

    @staticmethod
    def _completion_test_artifact_verification_family(normalized_path: str) -> str:
        filename = normalized_path.rsplit("/", 1)[-1]
        is_test_path = (
            normalized_path.startswith(("test/", "tests/"))
            or "/test/" in normalized_path
            or "/tests/" in normalized_path
            or filename.startswith("test_")
            or normalized_path.endswith("_test.py")
            or normalized_path.endswith("_tests.py")
            or filename.endswith((
                "_test.go",
                ".test.js",
                ".test.jsx",
                ".test.ts",
                ".test.tsx",
                ".spec.js",
                ".spec.jsx",
                ".spec.ts",
                ".spec.tsx",
            ))
        )
        if not is_test_path:
            return ""
        if normalized_path.endswith((".py", ".pyw")):
            return "python:test"
        if normalized_path.endswith((".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".vue", ".svelte")):
            return "javascript:test"
        if normalized_path.endswith(".rs"):
            return "rust:test"
        if normalized_path.endswith(".go"):
            return "go:test"
        if normalized_path.endswith((".java", ".kt", ".kts")):
            return "jvm:test"
        if normalized_path.endswith(".cs"):
            return "dotnet:test"
        if normalized_path.endswith(".rb"):
            return "ruby:test"
        if normalized_path.endswith(".php"):
            return "php:test"
        if normalized_path.endswith(".swift"):
            return "swift:test"
        if normalized_path.endswith((".c", ".cc", ".cpp", ".h", ".hpp")):
            return "native:test"
        return "generic:test"

    def _completion_verification_item_families(self, item: dict[str, Any]) -> set[str]:
        text = " ".join(
            str(item.get(key) or "")
            for key in ("command", "name", "summary", "suite")
        ).casefold()
        families: set[str] = set()
        for key in self._completion_verification_resolution_keys(item):
            families.add(key)
            broad_family = key.split(":", 1)[0]
            if broad_family:
                families.add(broad_family)
        token_map = {
            "python": ("pytest", "unittest", "tox", "mypy", "pyright", "ruff", "py_compile", "compileall", "python -m"),
            "javascript": (
                "jest",
                "vitest",
                "playwright",
                "cypress",
                "npm test",
                "npm run test",
                "pnpm test",
                "pnpm run test",
                "yarn test",
                "yarn run test",
                "bun test",
                "tsc",
                "typecheck",
                "type check",
                "node --check",
                "eslint",
                "npm run build",
                "pnpm build",
                "yarn build",
                "vite",
                "next build",
            ),
            "rust": ("cargo test", "cargo check", "cargo build", "cargo clippy"),
            "go": ("go test", "go build", "go vet"),
            "jvm": ("mvn test", "maven test", "gradle test", "./gradlew test", "gradlew test"),
            "dotnet": ("dotnet test", "dotnet build"),
            "ruby": ("rspec", "bundle exec", "ruby test"),
            "php": ("phpunit", "composer test"),
            "swift": ("swift test", "xcodebuild test"),
            "native": ("cmake", "make test", "ctest", "ninja test"),
        }
        for family, tokens in token_map.items():
            if any(token in text for token in tokens):
                families.add(family)
        if "--check" in text and re.search(r"(?<![a-z0-9_])node(?:\.exe)?(?![a-z0-9_])", text):
            families.add("javascript")
            families.add("javascript:typecheck")
        if self._completion_text_mentions_targeted_verification(text) and not families:
            families.add("generic")
        return families

    def _completion_path_requires_targeted_verification(self, path: str) -> bool:
        if not path:
            return False
        normalized = path.casefold()
        if normalized.endswith((".md", ".mdx", ".txt", ".rst", ".adoc")):
            return False
        if normalized.startswith(("docs/", "doc/", "documentation/")):
            return False
        filename = normalized.rsplit("/", 1)[-1]
        if filename in {
            "package.json",
            "package-lock.json",
            "pnpm-lock.yaml",
            "yarn.lock",
            "bun.lockb",
            "vite.config.ts",
            "vite.config.js",
            "next.config.js",
            "next.config.ts",
            "tsconfig.json",
            "pyproject.toml",
            "requirements.txt",
            "setup.py",
            "setup.cfg",
            "tox.ini",
            "pytest.ini",
            "cargo.toml",
            "cargo.lock",
            "go.mod",
            "go.sum",
            "pom.xml",
            "build.gradle",
            "build.gradle.kts",
            "settings.gradle",
            "settings.gradle.kts",
        } or filename.endswith((".csproj", ".sln")):
            return True
        return normalized.endswith(
            (
                ".py",
                ".pyw",
                ".js",
                ".jsx",
                ".ts",
                ".tsx",
                ".mjs",
                ".cjs",
                ".java",
                ".kt",
                ".kts",
                ".go",
                ".rs",
                ".c",
                ".cc",
                ".cpp",
                ".h",
                ".hpp",
                ".cs",
                ".rb",
                ".php",
                ".swift",
                ".vue",
                ".svelte",
            )
        ) or "/test" in normalized or normalized.startswith(("test/", "tests/"))

    def _completion_has_any_passing_verification_signal(self, completion_evidence: dict[str, Any]) -> bool:
        counts = completion_evidence.get("counts") if isinstance(completion_evidence.get("counts"), dict) else {}
        return (
            self._completion_evidence_count(counts, "passedVerification") > 0
            or self._completion_evidence_count(counts, "passedTestsRun") > 0
            or self._completion_has_passing_structural_file_check(completion_evidence)
        )

    def _completion_has_passing_structural_file_check(self, completion_evidence: dict[str, Any]) -> bool:
        changed_files = completion_evidence.get("changedFiles")
        if not isinstance(changed_files, list) or not changed_files:
            return False
        changed_names = {
            self._completion_changed_file_path(item).casefold().rsplit("/", 1)[-1]
            for item in changed_files
            if isinstance(item, dict) and self._completion_changed_file_path(item)
        }
        if not changed_names:
            return False
        commands = completion_evidence.get("commands")
        if not isinstance(commands, list):
            return False
        for item in commands:
            if not isinstance(item, dict):
                continue
            status = str(item.get("status") or "").strip().casefold()
            exit_code = item.get("exitCode")
            if status not in {"completed", "passed", "success"} or exit_code not in (0, "0", None):
                continue
            key = self._structural_command_resolution_key(item.get("command"))
            if not key:
                continue
            checked_names = {path.rsplit("/", 1)[-1] for path in key[1]}
            if changed_names.issubset(checked_names):
                return True
        return False

    def _completion_static_frontend_structural_check_satisfies(
        self,
        completion_evidence: dict[str, Any],
        missing_families: list[str],
    ) -> bool:
        missing = {item.casefold() for item in missing_families}
        if missing != {"javascript"}:
            return False
        changed_files = completion_evidence.get("changedFiles")
        if not isinstance(changed_files, list) or not changed_files:
            return False
        paths = [
            self._completion_changed_file_path(item).casefold()
            for item in changed_files
            if isinstance(item, dict) and self._completion_changed_file_path(item)
        ]
        if not paths:
            return False
        frontend_suffixes = (".html", ".css", ".js", ".mjs", ".md", ".txt")
        if any(not path.endswith(frontend_suffixes) for path in paths):
            return False
        manifest_names = {"package.json", "package-lock.json", "pnpm-lock.yaml", "yarn.lock", "bun.lockb"}
        if any(path.rsplit("/", 1)[-1] in manifest_names for path in paths):
            return False
        return self._completion_has_passing_structural_file_check(completion_evidence)

    def _completion_has_python_layout_coverage(
        self,
        completion_evidence: dict[str, Any],
        *,
        expected_paths: list[str] | None = None,
        test_expectation: int | None = None,
    ) -> bool:
        changed_files = completion_evidence.get("changedFiles")
        if not isinstance(changed_files, list) or not changed_files:
            return False
        changed_paths = [
            self._completion_changed_file_path(item)
            for item in changed_files
            if isinstance(item, dict) and self._completion_changed_file_path(item)
        ]
        if not changed_paths:
            return False
        changed_basenames = {path.rsplit("/", 1)[-1].casefold() for path in changed_paths}
        package_python = any("/" in path and path.casefold().endswith(".py") for path in changed_paths)
        if not package_python:
            return False
        if expected_paths:
            unresolved = []
            for expected in expected_paths:
                expected_base = expected.rsplit("/", 1)[-1].casefold()
                if expected_base not in changed_basenames:
                    unresolved.append(expected)
            if unresolved:
                return False
        if test_expectation is not None:
            found = len([
                path for path in changed_paths
                if path.casefold().endswith(".py")
                and self._completion_test_artifact_verification_family(path.casefold()) == "python:test"
            ])
            if found < test_expectation:
                return False
        verification_items = []
        for source_name in ("verification", "testsRun"):
            items = completion_evidence.get(source_name)
            if isinstance(items, list):
                verification_items.extend(item for item in items if isinstance(item, dict))
        observed_families: set[str] = set()
        for item in verification_items:
            status = str(item.get("status") or "").strip().lower()
            if status not in {"passed", "success", "completed"}:
                continue
            observed_families.update(self._completion_verification_item_families(item))
        return "python" in observed_families or "python:test" in observed_families or "python:syntax" in observed_families

    def _completion_has_targeted_verification(self, completion_evidence: dict[str, Any]) -> bool:
        verification_items = completion_evidence.get("verification")
        if isinstance(verification_items, list):
            for item in verification_items:
                if not isinstance(item, dict) or item.get("status") != "passed":
                    continue
                if self._completion_verification_item_is_targeted(item):
                    return True
        tests_run = completion_evidence.get("testsRun")
        if isinstance(tests_run, list):
            for item in tests_run:
                if not isinstance(item, dict) or item.get("status") not in {"passed", "success", "completed"}:
                    continue
                if self._completion_tests_run_item_is_targeted(item):
                    return True
        return False

    def _completion_verification_item_is_targeted(self, item: dict[str, Any]) -> bool:
        command = " ".join(
            str(item.get(key) or "")
            for key in ("command", "name", "summary")
        ).casefold()
        if not command.strip():
            return False
        return self._completion_text_mentions_targeted_verification(command)

    def _completion_tests_run_item_is_targeted(self, item: dict[str, Any]) -> bool:
        text = " ".join(
            str(item.get(key) or "")
            for key in ("command", "name", "summary", "suite")
        ).casefold()
        return self._completion_text_mentions_targeted_verification(text) or bool(text.strip())

    def _completion_text_mentions_targeted_verification(self, text: str) -> bool:
        normalized = text.casefold()
        if not normalized.strip():
            return False
        structural_only = {"git status", "git_status", "git diff", "git_diff"}
        if normalized.strip() in structural_only:
            return False
        if "--check" in normalized and re.search(r"(?<![a-z0-9_])node(?:\.exe)?(?![a-z0-9_])", normalized):
            return True
        targeted_tokens = (
            "pytest",
            "unittest",
            "tox",
            "jest",
            "vitest",
            "playwright",
            "cypress",
            "npm test",
            "pnpm test",
            "yarn test",
            "bun test",
            "bun run test",
            "npm run test",
            "npm run test:",
            "pnpm run test",
            "pnpm run test:",
            "yarn run test",
            "yarn run test:",
            "cargo test",
            "go test",
            "mvn test",
            "gradle test",
            "dotnet test",
            "test:",
            "typecheck",
            "type check",
            "tsc",
            "mypy",
            "pyright",
            "py_compile",
            "compileall",
            "ruff",
            "eslint",
            "npm run lint",
            "pnpm run lint",
            "yarn run lint",
            "node --check",
            "cargo check",
            "npm run build",
            "pnpm build",
            "pnpm run build",
            "yarn build",
            "yarn run build",
            "bun run build",
            "cargo build",
            "go build",
            "dotnet build",
            "cmake",
            "cmake --build",
            "ctest",
            "ninja",
            "ninja test",
            "make test",
        )
        return any(token in normalized for token in targeted_tokens)

    def _completion_review_conclusion(self, context: dict[str, Any]) -> dict[str, Any]:
        raw = context.get("completionReviewConclusion")
        if not isinstance(raw, dict):
            return {}
        conclusion = {
            "approvalId": str(raw.get("approvalId") or "").strip(),
            "decision": str(raw.get("decision") or "").strip(),
            "decidedBy": str(raw.get("decidedBy") or "").strip(),
            "summary": str(raw.get("summary") or "").strip(),
        }
        decided_at = raw.get("decidedAt")
        if isinstance(decided_at, (int, float)):
            conclusion["decidedAt"] = int(decided_at)
        gate_status = str(raw.get("gateStatus") or "").strip()
        if gate_status:
            conclusion["gateStatus"] = gate_status
        return {key: value for key, value in conclusion.items() if value not in ("", None)}

    def _completion_audit_context(
        self,
        *,
        task: dict[str, Any],
        completion_evidence: dict[str, Any],
    ) -> dict[str, Any]:
        approvals = self._completion_approval_audit(task.get("id"))
        review_conclusion = (
            completion_evidence.get("reviewConclusion")
            if isinstance(completion_evidence.get("reviewConclusion"), dict)
            else {}
        )
        audit: dict[str, Any] = {
            "approvals": approvals,
            "approvalCounts": self._completion_approval_counts(approvals),
        }
        executor_records = completion_evidence.get("advisorEvidenceExecutor")
        if isinstance(executor_records, list) and executor_records:
            audit["advisorEvidenceExecutor"] = [
                record for record in executor_records
                if isinstance(record, dict)
            ][:20]
        if review_conclusion:
            audit["reviewConclusion"] = review_conclusion
        if completion_evidence.get("completionAdvisor"):
            advisor = completion_evidence["completionAdvisor"]
            if isinstance(advisor, dict):
                audit["completionAdvisor"] = {
                    key: advisor.get(key)
                    for key in ("accepted", "source", "confidence", "proposalRecordId", "fallback_reason")
                    if advisor.get(key) not in (None, "", [])
                }
        return {key: value for key, value in audit.items() if value not in (None, "", [], {})}

    def _completion_approval_audit(self, task_id: Any) -> list[dict[str, Any]]:
        if not isinstance(task_id, str) or not task_id.strip():
            return []
        try:
            rows = self._store._conn.execute(  # noqa: SLF001
                "SELECT * FROM approvals WHERE task_id = ? ORDER BY created_at ASC",
                (task_id,),
            ).fetchall()
            approvals = [self._store._serialize_approval(dict(row)) for row in rows]  # noqa: SLF001
        except Exception:  # noqa: BLE001
            logger.debug("Failed to load approvals for completion audit", exc_info=True)
            return []
        result: list[dict[str, Any]] = []
        for approval in approvals:
            item = self._completion_approval_audit_item(approval)
            if item:
                result.append(item)
        return result[-20:]

    def _completion_approval_audit_item(self, approval: dict[str, Any]) -> dict[str, Any]:
        try:
            request = json.loads(approval.get("requestJson") or "{}")
        except json.JSONDecodeError:
            request = {}
        if not isinstance(request, dict):
            request = {}
        kind = str(approval.get("kind") or request.get("kind") or "").strip()
        item: dict[str, Any] = {
            "approvalId": approval.get("id"),
            "kind": kind,
            "decision": approval.get("decision") or "pending",
            "decidedBy": approval.get("decidedBy"),
            "createdAt": approval.get("createdAt"),
            "decidedAt": approval.get("decidedAt"),
        }
        for key in ("reason", "risk", "gateStatus", "source"):
            value = request.get(key)
            if isinstance(value, str) and value.strip():
                item[key] = value.strip()
        if kind == "completion_review":
            evidence = request.get("completionEvidence") if isinstance(request.get("completionEvidence"), dict) else {}
            item["evidenceLevel"] = evidence.get("evidenceLevel")
            structured = request.get("structuredResult") if isinstance(request.get("structuredResult"), dict) else {}
            gate = structured.get("completionGate") if isinstance(structured.get("completionGate"), dict) else {}
            item["gateStatus"] = item.get("gateStatus") or gate.get("status")
            item["summary"] = str(request.get("summary") or "").strip()[:500]
        elif kind == "worktree_merge":
            review = request.get("review") if isinstance(request.get("review"), dict) else {}
            item["reviewStatus"] = review.get("status") or request.get("reviewStatus")
            item["reviewer"] = review.get("reviewer") or request.get("reviewer")
            item["reviewerSummary"] = review.get("summary") or request.get("reviewerSummary")
            item["verificationStatus"] = request.get("verificationStatus")
            item["targetBranch"] = request.get("targetBranch")
        elif kind in {"run_command", "advisor_tool"}:
            advisor_evidence = request.get("advisorEvidence") if isinstance(request.get("advisorEvidence"), dict) else {}
            if advisor_evidence:
                item["advisorEvidence"] = {
                    key: advisor_evidence.get(key)
                    for key in ("summary", "requestKind", "target", "surfaceType", "proposalRecordId", "executorId")
                    if advisor_evidence.get(key) not in (None, "", [])
                }
        return {key: value for key, value in item.items() if value not in (None, "", [], {})}

    @staticmethod
    def _completion_approval_counts(approvals: list[dict[str, Any]]) -> dict[str, int]:
        counts = {
            "total": len(approvals),
            "approved": 0,
            "rejected": 0,
            "pending": 0,
        }
        for approval in approvals:
            decision = str(approval.get("decision") or "pending").strip().lower()
            if decision == "approved":
                counts["approved"] += 1
            elif decision == "rejected":
                counts["rejected"] += 1
            else:
                counts["pending"] += 1
        return counts

    def _is_write_or_verification_task(self, *, task: dict[str, Any], context: dict[str, Any]) -> bool:
        routing = task.get("routing") if isinstance(task.get("routing"), dict) else {}
        context_routing = context.get("routing") if isinstance(context.get("routing"), dict) else {}
        runtime_role = str(
            routing.get("runtimeRole")
            or context_routing.get("runtimeRole")
            or task.get("role")
            or ""
        ).strip().lower()
        contract = self._completion_contract_profile(task=task, context=context)
        if self._completion_contract_role_is_read_only(runtime_role=runtime_role):
            if isinstance(contract, dict):
                expected_artifacts = contract.get("expectedArtifacts")
                if isinstance(expected_artifacts, list) and expected_artifacts:
                    return True
                verification_requirements = contract.get("verificationRequirements")
                if isinstance(verification_requirements, list) and verification_requirements:
                    return True
            return bool(task.get("changedFiles") or task.get("commands") or task.get("verification"))
        scenario = str(routing.get("scenario") or context_routing.get("scenario") or "").strip().lower()
        if scenario in {"code_edit", "debug", "test_write", "doc_write", "multi_step_task", "supervised_task", "swarm_task"}:
            return True
        if task.get("type") == "validate":
            return True
        if routing.get("activeWorktree") or context_routing.get("activeWorktree"):
            return True
        if isinstance(contract, dict):
            owned_scope = contract.get("ownedScope")
            if isinstance(owned_scope, str):
                owned_scope = [owned_scope]
            if isinstance(owned_scope, list) and runtime_role not in {"planner", "reviewer", "explorer", "summarizer"}:
                for item in owned_scope:
                    path_text = self._completion_contract_normalize_path(item)
                    if path_text:
                        return True
            expected_artifacts = contract.get("expectedArtifacts")
            if isinstance(expected_artifacts, list) and expected_artifacts:
                return True
            verification_requirements = contract.get("verificationRequirements")
            if isinstance(verification_requirements, list) and verification_requirements:
                return True
        return bool(task.get("changedFiles") or task.get("commands") or task.get("verification"))

    def _request_completion_review(
        self,
        *,
        session_id: str,
        task: dict[str, Any],
        summary: str,
        structured_result: dict[str, Any],
        completion_evidence: dict[str, Any],
        reason: str,
        risk: str | None,
        gate_status: str | None,
        decision: str | None,
        skip_drain: bool,
    ) -> dict[str, Any]:
        advisor_requested_evidence = completion_evidence.get("advisorRequestedEvidence")
        approval_request = {
            "summary": summary,
            "risk": risk or "completion evidence requires review",
            "reason": reason,
            "completionEvidence": completion_evidence,
            "structuredResult": structured_result,
        }
        if isinstance(advisor_requested_evidence, list):
            approval_request["advisorRequestedEvidence"] = advisor_requested_evidence
        approval = self._store.create_approval(
            task["id"],
            "completion_review",
            approval_request,
        )
        review_structured_result = {
            **structured_result,
            "status": "needs_review",
            "completionGate": {
                "status": gate_status or "needs_user_review",
                "reason": reason,
                "approvalId": approval["id"],
            },
        }
        if isinstance(advisor_requested_evidence, list):
            review_structured_result["completionGate"]["advisorRequestedEvidence"] = advisor_requested_evidence
        self._validate_task_transition(task["status"], "waiting_approval", task["id"], silent=True)
        review_task = self._store.update_task(
            task_id=task["id"],
            status="waiting_approval",
            plan=task.get("plan") or [],
            summary=summary,
            result_summary=summary,
            structured_result=review_structured_result,
        )
        runtime_task = {
            **review_task,
            "plan": task.get("plan") or [],
            "resultSummary": summary,
        }
        active_msg_id = runtime_task.get("activeAssistantMessageId")
        if active_msg_id:
            self._store.update_message(
                active_msg_id,
                content=summary,
                status="streaming",
            )
        self._publish(
            session_id=session_id,
            task=runtime_task,
            event_type="approval.requested",
            payload={
                "approvalId": approval["id"],
                "taskId": task["id"],
                "kind": "completion_review",
                "request": json.loads(approval.get("requestJson") or "{}"),
            },
        )
        self._fire_hooks(
            "on_approval_required",
            session_id,
            runtime_task,
            extra_context={"approvalId": approval["id"], "kind": "completion_review"},
        )
        self._publish(
            session_id=session_id,
            task=runtime_task,
            event_type="agent.decision.completion",
            payload={
                "decision": decision or "needs_user_review",
                "whyBlocked": reason,
                "completionEvidence": completion_evidence,
                "approvalId": approval["id"],
            },
        )
        self._publish(
            session_id=session_id,
            task=runtime_task,
            event_type="task.waiting_approval",
            payload={
                "status": "waiting_approval",
                "detail": reason,
                "approvalId": approval["id"],
                "completionEvidence": completion_evidence,
            },
        )
        self._record_task_metrics(session_id=session_id, task=runtime_task, task_status="waiting_approval")
        if not skip_drain:
            self._drain_session_queue(session_id)
        return runtime_task

    def _request_advisor_evidence_execution_approval(
        self,
        *,
        session_id: str,
        task: dict[str, Any],
        summary: str,
        structured_result: dict[str, Any],
        completion_evidence: dict[str, Any],
        reason: str,
        gate_status: str | None,
        decision: str | None,
        approval_ids: list[str],
        skip_drain: bool,
    ) -> dict[str, Any]:
        advisor_requested_evidence = completion_evidence.get("advisorRequestedEvidence")
        advisor_approvals = completion_evidence.get("advisorEvidenceApprovals")
        wait_structured_result = {
            **structured_result,
            "status": "needs_review",
            "completionGate": {
                "status": gate_status or "advisor_evidence_requested",
                "reason": reason,
                "approvalIds": approval_ids,
            },
        }
        if isinstance(advisor_requested_evidence, list):
            wait_structured_result["completionGate"]["advisorRequestedEvidence"] = advisor_requested_evidence
        if isinstance(advisor_approvals, list):
            wait_structured_result["completionGate"]["advisorEvidenceApprovals"] = advisor_approvals
        if task.get("status") != "waiting_approval":
            self._validate_task_transition(task["status"], "waiting_approval", task["id"], silent=True)
        wait_task = self._store.update_task(
            task_id=task["id"],
            status="waiting_approval",
            plan=task.get("plan") or [],
            summary=summary,
            result_summary=summary,
            structured_result=wait_structured_result,
        )
        runtime_task = {
            **wait_task,
            "plan": task.get("plan") or [],
            "resultSummary": summary,
        }
        self._publish_task_run_snapshot(session_id=session_id, task=runtime_task)
        self._publish(
            session_id=session_id,
            task=runtime_task,
            event_type="agent.decision.completion",
            payload={
                "decision": decision or "advisor_evidence_requested",
                "whyBlocked": reason,
                "completionEvidence": completion_evidence,
                "approvalIds": approval_ids,
            },
        )
        self._publish(
            session_id=session_id,
            task=runtime_task,
            event_type="task.waiting_approval",
            payload={
                "status": "waiting_approval",
                "detail": reason,
                "approvalIds": approval_ids,
                "completionEvidence": completion_evidence,
            },
        )
        self._record_task_metrics(session_id=session_id, task=runtime_task, task_status="waiting_approval")
        if not skip_drain:
            self._drain_session_queue(session_id)
        return runtime_task

    def _apply_product_surface_advisor(
        self,
        *,
        session_id: str,
        task: dict[str, Any],
        summary: str,
        context: dict[str, Any],
        completion_evidence: dict[str, Any],
        tool_results: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        advice = self._consult_product_surface_advisor(
            task=task,
            summary=summary,
            context=context,
            completion_evidence=completion_evidence,
        )
        if advice is None:
            return None
        self._record_product_surface_advisor_proposal(
            session_id=session_id,
            task=task,
            summary=summary,
            product_surface_advice=advice,
        )
        completion_evidence["productSurfaceAdvisor"] = advice
        requested_evidence = self._advisor_requested_evidence_from_advice(
            advice=advice,
            completion_evidence=completion_evidence,
            tool_results=tool_results,
        )
        execution_suggestions = self._advisor_evidence_execution_suggestions(
            task=task,
            context=context,
            advice=advice,
            requested_evidence=requested_evidence,
        )
        adapter_summary = self._advisor_evidence_adapter_summary(
            requested_evidence=requested_evidence,
            execution_suggestions=execution_suggestions,
        )
        executor_records = self._advisor_evidence_executor_records(
            requested_evidence=requested_evidence,
            execution_suggestions=execution_suggestions,
        )
        if requested_evidence:
            completion_evidence["advisorRequestedEvidence"] = requested_evidence
            completion_evidence["advisorEvidenceAdapters"] = adapter_summary
        if execution_suggestions:
            advisor_approvals = self._create_advisor_evidence_execution_approvals(
                session_id=session_id,
                task=task,
                context=context,
                advice=advice,
                requested_evidence=requested_evidence,
                execution_suggestions=execution_suggestions,
            )
            if advisor_approvals:
                completion_evidence["advisorEvidenceApprovals"] = advisor_approvals
            completion_evidence["advisorEvidenceExecutionSuggestions"] = execution_suggestions
            executor_records = self._advisor_evidence_executor_records(
                requested_evidence=requested_evidence,
                execution_suggestions=execution_suggestions,
            )
            adapter_summary = self._advisor_evidence_adapter_summary(
                requested_evidence=requested_evidence,
                execution_suggestions=execution_suggestions,
            )
            completion_evidence["advisorEvidenceAdapters"] = adapter_summary
        if executor_records:
            completion_evidence["advisorEvidenceExecutor"] = executor_records
        advisory = self._product_surface_advisory_from_advice(advice)
        if advisory is not None:
            completion_evidence.setdefault("productAdvisories", []).append(advisory)
        if requested_evidence:
            self._publish_advisor_evidence_requests(
                session_id=session_id,
                task=task,
                advice=advice,
                requested_evidence=requested_evidence,
                execution_suggestions=execution_suggestions,
                executor_records=executor_records,
                adapter_summary=adapter_summary,
                advisory=advisory,
            )
        return advice

    def _publish_advisor_evidence_requests(
        self,
        *,
        session_id: str,
        task: dict[str, Any],
        advice: dict[str, Any],
        requested_evidence: list[dict[str, Any]],
        execution_suggestions: list[dict[str, Any]] | None,
        executor_records: list[dict[str, Any]] | None,
        adapter_summary: dict[str, Any] | None,
        advisory: dict[str, Any] | None,
    ) -> None:
        payload = advice.get("payload") if isinstance(advice.get("payload"), dict) else {}
        verification_intents = payload.get("verification_intents") if isinstance(payload.get("verification_intents"), list) else []
        execution_suggestions = execution_suggestions or []
        executor_records = executor_records or []
        blocking_count = len([
            item for item in requested_evidence
            if isinstance(item, dict) and item.get("blocking") is True
        ])
        missing_blocking_count = len([
            item for item in requested_evidence
            if isinstance(item, dict)
            and item.get("blocking") is True
            and item.get("status") == "missing"
        ])
        event_payload = {
            "source": "llm_product_surface_advisor",
            "surfaceType": payload.get("surface_type") or (advisory or {}).get("surfaceType"),
            "proposalRecordId": advice.get("proposalRecordId"),
            "confidence": advice.get("confidence"),
            "rationale": advice.get("rationale"),
            "recommendedVerification": self._completion_string_list(payload.get("recommended_verification"))[:10],
            "verificationIntents": verification_intents[:10],
            "evidenceRequests": requested_evidence[:20],
            "blockingCount": blocking_count,
            "missingBlockingCount": missing_blocking_count,
            "hasBlockingEvidenceRequest": blocking_count > 0,
        }
        if execution_suggestions:
            event_payload["executionSuggestions"] = execution_suggestions[:20]
        if executor_records:
            event_payload["executor"] = executor_records[:20]
        if adapter_summary:
            event_payload["adapterSummary"] = adapter_summary
        self._publish(
            session_id=session_id,
            task=task,
            event_type="agent.evidence.requested",
            payload=event_payload,
        )
        self._fire_hooks(
            "on_evidence_requested",
            session_id,
            task,
            extra_context={
                "surfaceType": event_payload["surfaceType"],
                "proposalRecordId": event_payload["proposalRecordId"],
                "evidenceRequests": requested_evidence[:20],
                "executionSuggestions": execution_suggestions[:20],
                "executor": executor_records[:20],
                "adapterSummary": adapter_summary or {},
                "verificationIntents": verification_intents[:10],
                "blockingCount": blocking_count,
                "missingBlockingCount": missing_blocking_count,
            },
        )

    def _pending_advisor_evidence_approval_ids(self, completion_evidence: dict[str, Any]) -> list[str]:
        approvals = completion_evidence.get("advisorEvidenceApprovals")
        if not isinstance(approvals, list):
            return []
        ids: list[str] = []
        for item in approvals:
            if not isinstance(item, dict):
                continue
            if item.get("decision") not in (None, "", "pending"):
                continue
            approval_id = str(item.get("approvalId") or "").strip()
            if approval_id:
                ids.append(approval_id)
        return ids

    def _advisor_evidence_adapter_summary(
        self,
        *,
        requested_evidence: list[dict[str, Any]],
        execution_suggestions: list[dict[str, Any]],
    ) -> dict[str, Any]:
        suggestions_by_index: dict[int, list[dict[str, Any]]] = {}
        for suggestion in execution_suggestions:
            if not isinstance(suggestion, dict):
                continue
            index = suggestion.get("requestIndex")
            if isinstance(index, int):
                suggestions_by_index.setdefault(index, []).append(suggestion)

        adapters: list[dict[str, Any]] = []
        counts = {
            "total": 0,
            "ready": 0,
            "approvalRequired": 0,
            "blocked": 0,
            "missingAdapter": 0,
            "satisfied": 0,
        }
        for index, request in enumerate(requested_evidence):
            if not isinstance(request, dict):
                continue
            counts["total"] += 1
            request_suggestions = suggestions_by_index.get(index) or []
            if request.get("status") == "satisfied":
                status = "satisfied"
                adapter_kind = self._advisor_evidence_inferred_adapter_kind(request)
                reason = "Evidence is already satisfied by existing runtime signals."
            elif request_suggestions:
                primary = request_suggestions[0]
                adapter_kind = str(primary.get("adapterKind") or primary.get("type") or primary.get("toolName") or "tool")
                status = self._advisor_evidence_adapter_status(primary)
                reason = str(primary.get("permissionReason") or "").strip()
                if not reason:
                    reason = self._advisor_evidence_adapter_reason(status=status, suggestion=primary)
            else:
                adapter_kind = self._advisor_evidence_inferred_adapter_kind(request)
                status = "missing_adapter"
                reason = (
                    "Advisor requested semantic evidence, but no executable command/tool/MCP adapter "
                    "is available yet. Track this as concrete adapter expansion, not a hard-coded gate."
                )

            if status == "satisfied":
                counts["satisfied"] += 1
            elif status == "blocked":
                counts["blocked"] += 1
            elif status == "approval_required":
                counts["approvalRequired"] += 1
            elif status == "missing_adapter":
                counts["missingAdapter"] += 1
            else:
                counts["ready"] += 1

            adapters.append({
                "requestIndex": index,
                "requestKind": request.get("kind"),
                "target": request.get("target"),
                "blocking": request.get("blocking") is True,
                "adapterKind": adapter_kind,
                "status": status,
                "reason": reason,
                "suggestionCount": len(request_suggestions),
            })
        return {
            "status": self._advisor_evidence_adapter_summary_status(counts),
            "counts": counts,
            "adapters": [
                {key: value for key, value in item.items() if value not in (None, "", [], {})}
                for item in adapters[:20]
            ],
            "principle": "advisor_selected_permission_gated",
        }

    @staticmethod
    def _advisor_evidence_adapter_status(suggestion: dict[str, Any]) -> str:
        state = str(suggestion.get("executionState") or "").strip()
        decision = str(suggestion.get("permissionDecision") or "").strip()
        if state in {"approval_pending", "approval_required"}:
            return "approval_required"
        if state in {"approval_approved", "ready_for_executor"}:
            return "ready"
        if state in {"denied_by_policy", "approval_rejected", "approval_unavailable"} or decision == "deny":
            return "blocked"
        return state or "ready"

    @staticmethod
    def _advisor_evidence_adapter_reason(*, status: str, suggestion: dict[str, Any]) -> str:
        if status == "approval_required":
            return "Executable evidence is available and awaits explicit approval."
        if status == "blocked":
            return str(suggestion.get("permissionReason") or "Executable evidence is blocked by policy.")
        if status == "ready":
            return "Executable evidence is available through the normal runtime tool pipeline."
        return "Executable evidence adapter state is recorded for audit."

    @staticmethod
    def _advisor_evidence_adapter_summary_status(counts: dict[str, int]) -> str:
        if counts.get("total", 0) <= 0:
            return "none"
        if counts.get("blocked", 0) > 0:
            return "blocked"
        if counts.get("approvalRequired", 0) > 0:
            return "approval_required"
        if counts.get("missingAdapter", 0) > 0:
            return "partial"
        if counts.get("ready", 0) > 0:
            return "ready"
        return "satisfied"

    @staticmethod
    def _advisor_evidence_inferred_adapter_kind(request: dict[str, Any]) -> str:
        if request.get("suggestedCommand"):
            return "run_command"
        suggested_tool = request.get("suggestedTool")
        if isinstance(suggested_tool, dict):
            name = str(suggested_tool.get("name") or suggested_tool.get("toolName") or "").strip()
            if name.startswith("mcp__"):
                return "mcp_tool"
            if name:
                return "tool"
        kind = str(request.get("kind") or "").strip().casefold()
        target = str(request.get("target") or "").strip().casefold()
        summary = str(request.get("summary") or "").strip().casefold()
        text = f"{kind} {target} {summary}"
        if "browser" in text or "page" in text:
            return "browser_inspection_adapter"
        if "document" in text or "render" in text or "docx" in text or "pdf" in text:
            return "document_render_adapter"
        if "migration" in text or "dry-run" in text or "dry run" in text:
            return "migration_dry_run_adapter"
        if "benchmark" in text or "performance" in text:
            return "benchmark_adapter"
        if "service" in text or "api" in text or "probe" in text:
            return "external_service_probe_adapter"
        return "semantic_evidence_adapter"

    def _advisor_evidence_executor_records(
        self,
        *,
        requested_evidence: list[dict[str, Any]],
        execution_suggestions: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        suggestions_by_index: dict[int, list[dict[str, Any]]] = {}
        for suggestion in execution_suggestions:
            if not isinstance(suggestion, dict):
                continue
            index = suggestion.get("requestIndex")
            if isinstance(index, int):
                suggestions_by_index.setdefault(index, []).append(suggestion)

        for index, request in enumerate(requested_evidence):
            if not isinstance(request, dict):
                continue
            suggestions = suggestions_by_index.get(index) or []
            if not suggestions:
                status = "satisfied" if request.get("status") == "satisfied" else "requested"
                reason = "Advisor requested semantic evidence without a runnable suggestion."
                if request.get("status") == "satisfied":
                    reason = "Advisor-requested evidence is already satisfied by available runtime evidence."
                records.append(self._advisor_evidence_executor_record(
                    request=request,
                    request_index=index,
                    suggestion=None,
                    status=status,
                    reason=reason,
                ))
                continue
            for suggestion in suggestions:
                records.append(self._advisor_evidence_executor_record(
                    request=request,
                    request_index=index,
                    suggestion=suggestion,
                ))
        return records[:20]

    def _advisor_evidence_executor_record(
        self,
        *,
        request: dict[str, Any],
        request_index: int,
        suggestion: dict[str, Any] | None,
        status: str | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        suggestion = suggestion if isinstance(suggestion, dict) else {}
        status = status or self._advisor_evidence_executor_status(request=request, suggestion=suggestion)
        record: dict[str, Any] = {
            "id": self._advisor_evidence_executor_id(request=request, request_index=request_index, suggestion=suggestion),
            "status": status,
            "source": request.get("source") or suggestion.get("source") or "llm_product_surface_advisor",
            "requestIndex": request_index,
            "requestKind": request.get("kind") or suggestion.get("requestKind"),
            "summary": request.get("summary") or suggestion.get("summary"),
            "target": request.get("target") or suggestion.get("target"),
            "blocking": request.get("blocking") is True,
            "evidenceStatus": request.get("status"),
            "proposalRecordId": request.get("proposalRecordId") or suggestion.get("proposalRecordId"),
            "reason": reason or self._advisor_evidence_executor_reason(status=status, suggestion=suggestion),
        }
        if suggestion:
            execution_type = suggestion.get("type") or suggestion.get("toolName")
            record.update({
                "executionType": execution_type,
                "toolName": suggestion.get("toolName"),
                "executionMode": suggestion.get("executionMode"),
                "permissionDecision": suggestion.get("permissionDecision"),
                "permissionReason": suggestion.get("permissionReason"),
                "requiresApproval": suggestion.get("requiresApproval") is True,
                "approvalId": suggestion.get("approvalId"),
                "approvalKind": suggestion.get("approvalKind"),
                "approvalDecision": suggestion.get("approvalDecision"),
                "capability": suggestion.get("capability"),
                "adapterKind": suggestion.get("adapterKind"),
                "adapterConfidence": suggestion.get("adapterConfidence"),
            })
            if suggestion.get("type") == "run_command":
                record["command"] = suggestion.get("command")
            elif isinstance(suggestion.get("arguments"), dict):
                record["arguments"] = suggestion.get("arguments")
        if isinstance(request.get("suggestedTool"), dict) and not record.get("toolName"):
            record["toolName"] = request["suggestedTool"].get("name") or request["suggestedTool"].get("toolName")
        if request.get("suggestedCommand") and not record.get("command"):
            record["command"] = request.get("suggestedCommand")
            record.setdefault("toolName", "run_command")
            record.setdefault("executionType", "run_command")
        return {key: value for key, value in record.items() if value not in (None, "", [], {})}

    def _advisor_evidence_executor_status(
        self,
        *,
        request: dict[str, Any],
        suggestion: dict[str, Any],
    ) -> str:
        if request.get("status") == "satisfied":
            return "satisfied"
        state = str(suggestion.get("executionState") or "").strip()
        if state == "approval_pending":
            return "approval_requested"
        if state == "approval_approved":
            return "approved"
        if state == "approval_rejected":
            return "rejected"
        if state == "approval_unavailable":
            return "failed"
        if state == "denied_by_policy":
            return "blocked"
        if state in {"approval_required", "ready_for_executor"}:
            return state
        if not suggestion:
            return "requested"
        return state or "requested"

    @staticmethod
    def _advisor_evidence_executor_reason(status: str, suggestion: dict[str, Any]) -> str:
        if status == "satisfied":
            return "Evidence is satisfied by a matching successful runtime result."
        if status == "approval_requested":
            return "Runtime created an explicit approval before executing advisor-requested evidence."
        if status == "approved":
            return "Evidence execution was approved and is ready to resume."
        if status == "rejected":
            return "Evidence execution approval was rejected."
        if status == "blocked":
            return str(suggestion.get("permissionReason") or "Evidence execution is blocked by policy.")
        if status == "failed":
            return str(suggestion.get("executionError") or "Evidence executor setup failed.")
        if status == "approval_required":
            return "Advisor suggested executable evidence; runtime requires approval before running it."
        if status == "ready_for_executor":
            return "Advisor suggested executable evidence and policy permits it; runtime will still gate blocking evidence through approval."
        return "Advisor requested semantic evidence for runtime tracking."

    @staticmethod
    def _advisor_evidence_executor_id(
        *,
        request: dict[str, Any],
        request_index: int,
        suggestion: dict[str, Any],
    ) -> str:
        raw = {
            "requestIndex": request_index,
            "kind": request.get("kind"),
            "target": request.get("target"),
            "summary": request.get("summary"),
            "command": suggestion.get("command") or request.get("suggestedCommand"),
            "toolName": suggestion.get("toolName") or (
                request.get("suggestedTool", {}).get("name")
                if isinstance(request.get("suggestedTool"), dict)
                else None
            ),
            "arguments": suggestion.get("arguments") or (
                request.get("suggestedTool", {}).get("arguments")
                if isinstance(request.get("suggestedTool"), dict)
                else None
            ),
            "proposalRecordId": request.get("proposalRecordId") or suggestion.get("proposalRecordId"),
        }
        encoded = json.dumps(raw, ensure_ascii=False, sort_keys=True, default=str)
        return "evexec_" + hashlib.sha1(encoded.encode("utf-8")).hexdigest()[:16]

    def _publish_advisor_evidence_executor_event(
        self,
        *,
        session_id: str,
        task: dict[str, Any],
        record: dict[str, Any],
        transition: str | None = None,
    ) -> None:
        payload = {
            "executor": record,
            "transition": transition or record.get("status"),
            "source": record.get("source") or "llm_product_surface_advisor",
        }
        self._publish(
            session_id=session_id,
            task=task,
            event_type="agent.evidence.executor.updated",
            payload={key: value for key, value in payload.items() if value not in (None, "", [], {})},
            visibility="panel",
        )

    def _publish_advisor_evidence_executor_event_from_approval(
        self,
        *,
        session_id: str,
        task: dict[str, Any],
        approval: dict[str, Any],
        request: dict[str, Any],
        status: str,
        transition: str | None = None,
        result: dict[str, Any] | None = None,
        reason: str | None = None,
    ) -> None:
        advisor_evidence = request.get("advisorEvidence") if isinstance(request.get("advisorEvidence"), dict) else {}
        if not advisor_evidence:
            return
        executor_id = str(advisor_evidence.get("executorId") or "").strip()
        if not executor_id:
            return
        tool_name = str(request.get("toolName") or request.get("name") or "").strip()
        if not tool_name and approval.get("kind") == "run_command":
            tool_name = "run_command"
        record: dict[str, Any] = {
            "id": executor_id,
            "status": status,
            "source": advisor_evidence.get("source") or "llm_product_surface_advisor",
            "requestIndex": advisor_evidence.get("requestIndex"),
            "requestKind": advisor_evidence.get("requestKind"),
            "summary": advisor_evidence.get("summary"),
            "target": advisor_evidence.get("target"),
            "blocking": advisor_evidence.get("blocking") is True,
            "proposalRecordId": advisor_evidence.get("proposalRecordId"),
            "approvalId": approval.get("id"),
            "approvalKind": approval.get("kind"),
            "approvalDecision": approval.get("decision"),
            "toolName": tool_name,
            "reason": reason or self._advisor_evidence_executor_result_reason(status=status, result=result),
        }
        if approval.get("kind") == "run_command":
            record.update({
                "executionType": "run_command",
                "command": request.get("command"),
                "cwd": request.get("cwd"),
                "shell": request.get("shell"),
            })
        else:
            record.update({
                "executionType": "tool",
                "arguments": request.get("arguments") if isinstance(request.get("arguments"), dict) else None,
                "capability": request.get("capability"),
            })
        if isinstance(result, dict):
            record["result"] = {
                key: result.get(key)
                for key in ("status", "exitCode", "durationMs", "commandLogId", "toolCallId", "failureKind", "recoveryHint")
                if result.get(key) not in (None, "", [], {})
            }
            command_log = result.get("commandLog") if isinstance(result.get("commandLog"), dict) else {}
            if command_log.get("id"):
                record.setdefault("result", {})["commandLogId"] = command_log.get("id")
        self._publish_advisor_evidence_executor_event(
            session_id=session_id,
            task=task,
            record={key: value for key, value in record.items() if value not in (None, "", [], {})},
            transition=transition or status,
        )

    @staticmethod
    def _advisor_evidence_executor_result_reason(
        *,
        status: str,
        result: dict[str, Any] | None,
    ) -> str:
        result = result if isinstance(result, dict) else {}
        if status == "running":
            return "Approved advisor-requested evidence is now executing."
        if status == "satisfied":
            return "Advisor-requested evidence executed successfully."
        if status == "failed":
            result_status = str(result.get("status") or "").strip()
            if result_status:
                return f"Advisor-requested evidence execution failed with status {result_status}."
            return "Advisor-requested evidence execution failed."
        if status == "rejected":
            return "Advisor-requested evidence execution was rejected by the user."
        return "Advisor-requested evidence executor state changed."

    def _create_advisor_evidence_execution_approvals(
        self,
        *,
        session_id: str,
        task: dict[str, Any],
        context: dict[str, Any],
        advice: dict[str, Any],
        requested_evidence: list[dict[str, Any]],
        execution_suggestions: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        approvals: list[dict[str, Any]] = []
        for suggestion in execution_suggestions:
            if not isinstance(suggestion, dict):
                continue
            request = self._advisor_evidence_request_for_suggestion(
                requested_evidence=requested_evidence,
                suggestion=suggestion,
            )
            if request is None or not self._advisor_evidence_suggestion_needs_approval(suggestion, request):
                continue
            try:
                request_payload, execution_request, approval_kind = self._advisor_evidence_execution_approval_request(
                    task=task,
                    context=context,
                    advice=advice,
                    request=request,
                    suggestion=suggestion,
                )
            except Exception as exc:  # noqa: BLE001
                suggestion["executionState"] = "approval_unavailable"
                suggestion["executionError"] = str(exc)
                self._publish_advisor_evidence_executor_event(
                    session_id=session_id,
                    task=task,
                    record=self._advisor_evidence_executor_record(
                        request=request,
                        request_index=int(suggestion.get("requestIndex") or 0),
                        suggestion=suggestion,
                    ),
                    transition="approval_unavailable",
                )
                continue

            approval = None
            if hasattr(self._store, "find_approval_by_request_fields"):
                approval = self._store.find_approval_by_request_fields(
                    task_id=task["id"],
                    kind=approval_kind,
                    fields=execution_request,
                )
            created = False
            if approval is None:
                approval = self._store.create_approval(task["id"], approval_kind, request_payload)
                created = True

            decision = approval.get("decision")
            execution_state = "approval_pending"
            if decision == "approved":
                execution_state = "approval_approved"
            elif decision == "rejected":
                execution_state = "approval_rejected"
            elif decision not in (None, ""):
                execution_state = "approval_resolved"

            suggestion.update({
                "approvalId": approval["id"],
                "approvalKind": approval_kind,
                "approvalDecision": decision or "pending",
                "executionMode": "approval_then_run_command" if suggestion.get("type") == "run_command" else "approval_then_tool",
                "executionState": execution_state,
                "requiresApproval": True,
                "approvalRequest": execution_request,
            })
            record = {
                "approvalId": approval["id"],
                "kind": approval_kind,
                "decision": decision,
                "executionState": execution_state,
                "requestIndex": suggestion.get("requestIndex"),
                "requestKind": suggestion.get("requestKind"),
                "workspaceRoot": execution_request["workspaceRoot"],
                "created": created,
            }
            if suggestion.get("type") == "run_command":
                record.update({
                    "command": execution_request["command"],
                    "cwd": execution_request["cwd"],
                    "shell": execution_request["shell"],
                    "timeoutMs": execution_request["timeoutMs"],
                })
            else:
                record.update({
                    "toolName": execution_request["toolName"],
                    "arguments": execution_request["arguments"],
                    "capability": execution_request.get("capability"),
                })
            approvals.append({key: value for key, value in record.items() if value not in ("", None)})
            executor_record = self._advisor_evidence_executor_record(
                request=request,
                request_index=int(suggestion.get("requestIndex") or 0),
                suggestion=suggestion,
            )
            self._publish_advisor_evidence_executor_event(
                session_id=session_id,
                task=task,
                record=executor_record,
                transition=execution_state,
            )
            if created:
                self._publish_advisor_evidence_approval_requested(
                    session_id=session_id,
                    task=task,
                    approval=approval,
                    advisor_evidence=request_payload.get("advisorEvidence"),
                )
        return approvals

    def _advisor_evidence_request_for_suggestion(
        self,
        *,
        requested_evidence: list[dict[str, Any]],
        suggestion: dict[str, Any],
    ) -> dict[str, Any] | None:
        index = suggestion.get("requestIndex")
        if not isinstance(index, int) or index < 0 or index >= len(requested_evidence):
            return None
        request = requested_evidence[index]
        return request if isinstance(request, dict) else None

    @staticmethod
    def _advisor_evidence_suggestion_needs_approval(
        suggestion: dict[str, Any],
        request: dict[str, Any],
    ) -> bool:
        permission_decision = str(suggestion.get("permissionDecision") or "").strip()
        return (
            suggestion.get("type") in {"run_command", "tool"}
            and suggestion.get("blocking") is True
            and permission_decision in {"allow", "approval_required"}
            and request.get("status") == "missing"
        )

    def _advisor_evidence_execution_approval_request(
        self,
        *,
        task: dict[str, Any],
        context: dict[str, Any],
        advice: dict[str, Any],
        request: dict[str, Any],
        suggestion: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any], str]:
        if suggestion.get("type") == "run_command":
            payload, execution_request = self._advisor_evidence_run_command_approval_request(
                task=task,
                context=context,
                advice=advice,
                request=request,
                suggestion=suggestion,
            )
            return payload, execution_request, "run_command"
        payload, execution_request = self._advisor_evidence_tool_approval_request(
            task=task,
            context=context,
            advice=advice,
            request=request,
            suggestion=suggestion,
        )
        return payload, execution_request, "advisor_tool"

    def _advisor_evidence_run_command_approval_request(
        self,
        *,
        task: dict[str, Any],
        context: dict[str, Any],
        advice: dict[str, Any],
        request: dict[str, Any],
        suggestion: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        command = str(suggestion.get("command") or "").strip()
        if not command:
            raise ValueError("Advisor evidence command is empty")
        workspace_root = self._advisor_evidence_workspace_root(task=task, context=context, tool_name="run_command")
        shell_name = normalize_shell(
            str(request.get("shell")).strip() if isinstance(request.get("shell"), str) else None,
            self._store,
        )
        execution_request = approval_request(
            task_id=str(task.get("id") or ""),
            command=_powershell_execution_command(command, shell_name),
            cwd=self._advisor_evidence_normalized_cwd(workspace_root, request.get("cwd")),
            shell=shell_name,
            timeout_ms=self._advisor_evidence_timeout_ms(request),
            workspace_root=str(workspace_root),
            background=request.get("background") is True,
        )
        payload = advice.get("payload") if isinstance(advice.get("payload"), dict) else {}
        advisor_metadata = {
            "source": "llm_product_surface_advisor",
            "proposalRecordId": request.get("proposalRecordId") or advice.get("proposalRecordId"),
            "executorId": self._advisor_evidence_executor_id(
                request=request,
                request_index=int(suggestion.get("requestIndex") or 0),
                suggestion=suggestion,
            ),
            "requestIndex": suggestion.get("requestIndex"),
            "requestKind": suggestion.get("requestKind"),
            "summary": suggestion.get("summary"),
            "target": suggestion.get("target"),
            "blocking": True,
            "surfaceType": payload.get("surface_type"),
            "rationale": request.get("rationale") or advice.get("rationale"),
        }
        approval_payload = {
            **execution_request,
            "advisorEvidence": {
                key: value for key, value in advisor_metadata.items() if value not in ("", None)
            },
        }
        return approval_payload, execution_request

    def _advisor_evidence_tool_approval_request(
        self,
        *,
        task: dict[str, Any],
        context: dict[str, Any],
        advice: dict[str, Any],
        request: dict[str, Any],
        suggestion: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        tool_name = str(suggestion.get("toolName") or "").strip()
        if not tool_name:
            raise ValueError("Advisor evidence tool name is empty")
        if tool_name == "run_command":
            raise ValueError("Advisor evidence run_command must use suggestedCommand approval")
        raw_arguments = suggestion.get("arguments")
        arguments = dict(raw_arguments) if isinstance(raw_arguments, dict) else {}
        workspace_root = self._advisor_evidence_workspace_root(task=task, context=context, tool_name=tool_name)
        tool_context = {
            **(context if isinstance(context, dict) else {}),
            "workspace_root": str(workspace_root),
        }
        tool_context.setdefault("search_config", {})
        tool_context.setdefault("search_mode", "content")
        self._fill_tool_defaults(tool_name, arguments, tool_context)
        execution_request = {
            "taskId": str(task.get("id") or ""),
            "toolName": tool_name,
            "arguments": arguments,
            "workspaceRoot": str(workspace_root),
            "capability": suggestion.get("capability"),
        }
        payload = advice.get("payload") if isinstance(advice.get("payload"), dict) else {}
        advisor_metadata = {
            "source": "llm_product_surface_advisor",
            "proposalRecordId": request.get("proposalRecordId") or advice.get("proposalRecordId"),
            "executorId": self._advisor_evidence_executor_id(
                request=request,
                request_index=int(suggestion.get("requestIndex") or 0),
                suggestion=suggestion,
            ),
            "requestIndex": suggestion.get("requestIndex"),
            "requestKind": suggestion.get("requestKind"),
            "summary": suggestion.get("summary"),
            "target": suggestion.get("target"),
            "blocking": True,
            "surfaceType": payload.get("surface_type"),
            "rationale": request.get("rationale") or advice.get("rationale"),
        }
        approval_payload = {
            **execution_request,
            "advisorEvidence": {
                key: value for key, value in advisor_metadata.items() if value not in ("", None)
            },
        }
        return approval_payload, execution_request

    def _advisor_evidence_workspace_root(
        self,
        *,
        task: dict[str, Any],
        context: dict[str, Any],
        tool_name: str = "run_command",
    ) -> Path:
        raw_root = ""
        if isinstance(context, dict):
            raw_root = str(context.get("workspace_root") or context.get("workspaceRoot") or "").strip()
        if not raw_root:
            session_id = str(task.get("sessionId") or "").strip()
            if session_id:
                session = self._store.get_session({"sessionId": session_id})["session"]
                raw_root = str(session.get("workspaceRoot") or "").strip()
        if not raw_root:
            raise ValueError("workspaceRoot is required for advisor evidence execution approval")

        root = Path(raw_root).resolve()
        if hasattr(self, "_apply_task_worktree_to_tool_arguments"):
            try:
                bound = self._apply_task_worktree_to_tool_arguments(
                    task_id=task["id"],
                    tool_name=tool_name,
                    arguments={"workspaceRoot": str(root), "cwd": "."},
                )
                bound_root = bound.get("workspaceRoot") if isinstance(bound, dict) else None
                if isinstance(bound_root, str) and bound_root.strip():
                    root = Path(bound_root).resolve()
            except Exception:  # noqa: BLE001
                logger.debug("Failed to bind advisor evidence execution to task worktree", exc_info=True)
        if not root.exists() or not root.is_dir():
            raise ValueError(f"Workspace root does not exist: {root}")
        return root

    def _advisor_evidence_normalized_cwd(self, workspace_root: Path, raw_cwd: Any) -> str:
        cwd_text = str(raw_cwd or ".").strip() or "."
        cwd_candidate = Path(cwd_text)
        cwd_path = cwd_candidate.resolve() if cwd_candidate.is_absolute() else (workspace_root / cwd_candidate).resolve()
        if not cwd_path.is_dir():
            raise ValueError(f"Advisor evidence command cwd does not exist: {cwd_text}")
        try:
            relative = cwd_path.relative_to(workspace_root.resolve())
        except ValueError as exc:
            raise ValueError(f"Advisor evidence command cwd is outside workspace: {cwd_text}") from exc
        return "." if str(relative) == "." else relative.as_posix()

    def _advisor_evidence_timeout_ms(self, request: dict[str, Any]) -> int:
        raw_timeout = request.get("timeoutMs") or request.get("timeout_ms")
        if raw_timeout is None:
            config_result = self._store.get_config({}) if hasattr(self._store, "get_config") else {}
            config = config_result.get("config") if isinstance(config_result, dict) else {}
            policy = config.get("policy") if isinstance(config, dict) else {}
            raw_timeout = policy.get("commandTimeoutMs") if isinstance(policy, dict) else None
        try:
            timeout_ms = int(raw_timeout or 600_000)
        except (TypeError, ValueError):
            timeout_ms = 600_000
        return max(1000, min(timeout_ms, 1_800_000))

    def _publish_advisor_evidence_approval_requested(
        self,
        *,
        session_id: str,
        task: dict[str, Any],
        approval: dict[str, Any],
        advisor_evidence: Any,
    ) -> None:
        try:
            request = json.loads(approval.get("requestJson") or "{}")
        except json.JSONDecodeError:
            request = {}
        kind = str(approval.get("kind") or request.get("kind") or "advisor_tool")
        self._publish(
            session_id=session_id,
            task=task,
            event_type="approval.requested",
            payload={
                "approvalId": approval["id"],
                "taskId": task["id"],
                "kind": kind,
                "request": request,
                "source": "llm_product_surface_advisor",
                "advisorEvidence": advisor_evidence,
            },
        )
        self._fire_hooks(
            "on_approval_required",
            session_id,
            task,
            extra_context={
                "approvalId": approval["id"],
                "kind": kind,
                "source": "llm_product_surface_advisor",
                "advisorEvidence": advisor_evidence,
            },
        )

    def _advisor_evidence_execution_suggestions(
        self,
        *,
        task: dict[str, Any],
        context: dict[str, Any],
        advice: dict[str, Any],
        requested_evidence: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        suggestions: list[dict[str, Any]] = []
        for index, request in enumerate(requested_evidence):
            if not isinstance(request, dict):
                continue
            if request.get("status") == "satisfied":
                continue
            command = request.get("suggestedCommand")
            if isinstance(command, str) and command.strip():
                permission = self._advisor_evidence_permission_summary(
                    task=task,
                    request=request,
                    command=command.strip(),
                )
                decision = str(permission.get("decision") or "approval_required")
                execution_state = "ready_for_executor"
                if decision == "approval_required":
                    execution_state = "approval_required"
                elif decision == "deny":
                    execution_state = "denied_by_policy"
                suggestion: dict[str, Any] = {
                    "type": "run_command",
                    "toolName": "run_command",
                    "command": command.strip(),
                    "requestIndex": index,
                    "requestKind": request.get("kind"),
                    "summary": request.get("summary"),
                    "target": request.get("target"),
                    "blocking": request.get("blocking") is True,
                    "source": "llm_product_surface_advisor",
                    "proposalRecordId": request.get("proposalRecordId") or advice.get("proposalRecordId"),
                    "executionState": execution_state,
                    "executionMode": "suggestion_only",
                    "permissionDecision": decision,
                    "requiresApproval": decision == "approval_required",
                }
                if request.get("domain") not in (None, ""):
                    suggestion["domain"] = request.get("domain")
                suggestion["adapterKind"] = "run_command"
                suggestion["adapterConfidence"] = 1.0
                if permission.get("reason"):
                    suggestion["permissionReason"] = permission["reason"]
                if permission.get("approvalKind"):
                    suggestion["approvalKind"] = permission["approvalKind"]
                suggestions.append({key: value for key, value in suggestion.items() if value not in (None, "")})

            suggested_tool = request.get("suggestedTool")
            if not isinstance(suggested_tool, dict):
                continue
            tool_name = str(suggested_tool.get("name") or suggested_tool.get("toolName") or "").strip()
            if not tool_name or tool_name == "run_command":
                continue
            raw_arguments = suggested_tool.get("arguments")
            arguments = dict(raw_arguments) if isinstance(raw_arguments, dict) else {}
            permission = self._advisor_evidence_tool_permission_summary(
                task=task,
                context=context,
                request=request,
                tool_name=tool_name,
                arguments=arguments,
            )
            decision = str(permission.get("decision") or "approval_required")
            execution_state = "ready_for_executor"
            if decision == "approval_required":
                execution_state = "approval_required"
            elif decision == "deny":
                execution_state = "denied_by_policy"
            suggestion: dict[str, Any] = {
                "type": "tool",
                "toolName": tool_name,
                "arguments": arguments,
                "requestIndex": index,
                "requestKind": request.get("kind"),
                "summary": request.get("summary"),
                "target": request.get("target"),
                "blocking": request.get("blocking") is True,
                "source": "llm_product_surface_advisor",
                "proposalRecordId": request.get("proposalRecordId") or advice.get("proposalRecordId"),
                "executionState": execution_state,
                "executionMode": "suggestion_only",
                "permissionDecision": decision,
                "requiresApproval": decision == "approval_required",
            }
            if permission.get("capability"):
                suggestion["capability"] = permission["capability"]
            if permission.get("adapterKind"):
                suggestion["adapterKind"] = permission["adapterKind"]
            if permission.get("adapterConfidence") is not None:
                suggestion["adapterConfidence"] = permission["adapterConfidence"]
            if request.get("domain") not in (None, ""):
                suggestion["domain"] = request.get("domain")
            if permission.get("reason"):
                suggestion["permissionReason"] = permission["reason"]
            if permission.get("approvalKind"):
                suggestion["approvalKind"] = permission["approvalKind"]
            suggestions.append({key: value for key, value in suggestion.items() if value not in (None, "")})
        return suggestions[:20]

    def _advisor_evidence_permission_summary(
        self,
        *,
        task: dict[str, Any],
        request: dict[str, Any],
        command: str,
    ) -> dict[str, Any]:
        try:
            config_result = self._store.get_config({}) if hasattr(self._store, "get_config") else {}
            config = config_result.get("config") if isinstance(config_result, dict) else {}
            if not isinstance(config, dict):
                config = {}
            decision = PermissionEngine(config).evaluate(PermissionRequest(
                capability="runCommand",
                tool_name="advisor.evidence.run_command",
                context={
                    "taskId": task.get("id"),
                    "sessionId": task.get("sessionId"),
                    "evidenceKind": request.get("kind"),
                    "target": request.get("target"),
                    "command": command,
                    "source": "llm_product_surface_advisor",
                },
            ))
            return {
                "decision": decision.decision,
                "capability": decision.capability,
                "reason": decision.reason,
                "approvalKind": decision.approval_kind,
            }
        except Exception as exc:  # noqa: BLE001
            logger.debug("Failed to evaluate advisor evidence permission", exc_info=True)
            return {
                "decision": "approval_required",
                "capability": "runCommand",
                "reason": f"Permission evaluation failed; approval required before execution: {exc}",
                "approvalKind": "run_command",
            }

    def _advisor_evidence_tool_permission_summary(
        self,
        *,
        task: dict[str, Any],
        context: dict[str, Any],
        request: dict[str, Any],
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        adapter_match = self._advisor_evidence_tool_adapter_match(request=request, tool_name=tool_name)
        if not getattr(self._tool_registry, "has_tool", lambda _name: False)(tool_name):
            return {
                "decision": "deny",
                "capability": TOOL_CAPABILITIES.get(tool_name, "unknown"),
                "reason": f"Tool {tool_name!r} is not registered in this runtime.",
                "adapterKind": adapter_match["adapterKind"],
                "adapterConfidence": adapter_match["confidence"],
            }
        policy_summary = self._advisor_evidence_tool_policy_summary(
            task=task,
            context=context,
            tool_name=tool_name,
        )
        if policy_summary.get("decision") == "deny":
            if adapter_match:
                policy_summary["adapterKind"] = adapter_match["adapterKind"]
                policy_summary["adapterConfidence"] = adapter_match["confidence"]
            return policy_summary
        capability = TOOL_CAPABILITIES.get(tool_name)
        if not capability:
            result = {
                "decision": "approval_required",
                "capability": "mcpTool" if tool_name.startswith("mcp__") else "customTool",
                "reason": (
                    "Registered dynamic tool requires explicit advisor evidence approval."
                    if tool_name.startswith("mcp__")
                    else "Registered custom tool requires explicit advisor evidence approval."
                ),
                "approvalKind": "advisor_tool",
            }
            if adapter_match:
                result["adapterKind"] = adapter_match["adapterKind"]
                result["adapterConfidence"] = adapter_match["confidence"]
            return result
        try:
            config_result = self._store.get_config({}) if hasattr(self._store, "get_config") else {}
            config = config_result.get("config") if isinstance(config_result, dict) else {}
            if not isinstance(config, dict):
                config = {}
            decision = PermissionEngine(config).evaluate(PermissionRequest(
                capability=capability,
                tool_name=tool_name,
                context={
                    "taskId": task.get("id"),
                    "sessionId": task.get("sessionId"),
                    "evidenceKind": request.get("kind"),
                    "target": request.get("target"),
                    "toolName": tool_name,
                    "arguments": arguments,
                    "source": "llm_product_surface_advisor",
                },
            ))
            approval_kind = decision.approval_kind
            if decision.decision == "approval_required":
                approval_kind = "advisor_tool"
            result = {
                "decision": decision.decision,
                "capability": decision.capability,
                "reason": decision.reason,
                "approvalKind": approval_kind,
            }
            if adapter_match:
                result["adapterKind"] = adapter_match["adapterKind"]
                result["adapterConfidence"] = adapter_match["confidence"]
            return result
        except Exception as exc:  # noqa: BLE001
            logger.debug("Failed to evaluate advisor evidence tool permission", exc_info=True)
            return {
                "decision": "approval_required",
                "capability": capability,
                "reason": f"Permission evaluation failed; approval required before tool execution: {exc}",
                "approvalKind": "advisor_tool",
            }

    @staticmethod
    def _advisor_evidence_tool_adapter_match(*, request: dict[str, Any], tool_name: str) -> dict[str, Any]:
        inferred = TaskLifecycleMixin._advisor_evidence_inferred_adapter_kind(request)
        if tool_name == "browser":
            confidence = 0.95 if inferred == "browser_inspection_adapter" else 0.65
            return {"adapterKind": "browser_inspection_adapter", "confidence": confidence}
        if tool_name.startswith("mcp__"):
            return {"adapterKind": "mcp_tool", "confidence": 0.9}
        if tool_name == "web_fetch":
            return {"adapterKind": "external_service_probe_adapter", "confidence": 0.75}
        return {"adapterKind": inferred if inferred != "semantic_evidence_adapter" else "tool", "confidence": 0.6}

    def _advisor_evidence_tool_policy_summary(
        self,
        *,
        task: dict[str, Any],
        context: dict[str, Any],
        tool_name: str,
    ) -> dict[str, Any]:
        policy_context = dict(context) if isinstance(context, dict) else {}
        if not isinstance(policy_context.get("config"), dict):
            try:
                config_result = self._store.get_config({}) if hasattr(self._store, "get_config") else {}
                config = config_result.get("config") if isinstance(config_result, dict) else {}
                if isinstance(config, dict):
                    policy_context["config"] = config
            except Exception:  # noqa: BLE001
                logger.debug("Failed to load config for advisor evidence tool policy", exc_info=True)
        decision = ToolPolicyResolver().resolve(
            task=task,
            context=policy_context,
            tool_results=[],
            registered_tools=[{"name": tool_name}],
        )
        if tool_name in decision.denied_tool_names:
            return {
                "decision": "deny",
                "capability": TOOL_CAPABILITIES.get(tool_name, "mcpTool" if tool_name.startswith("mcp__") else "customTool"),
                "reason": decision.reasons.get(tool_name) or f"Tool {tool_name!r} is denied by tool policy.",
            }
        return {"decision": "allow"}

    def _consult_product_surface_advisor(
        self,
        *,
        task: dict[str, Any],
        summary: str,
        context: dict[str, Any],
        completion_evidence: dict[str, Any],
    ) -> dict[str, Any] | None:
        advisor = getattr(self, "_decision_advisor", None)
        if advisor is None:
            return None
        if self._should_skip_product_surface_advisor(task, context, completion_evidence):
            return None
        try:
            input_context: dict[str, Any] = {
                "goal": task.get("goal", ""),
                "summary": summary[:2000],
                "acceptance_criteria": task.get("acceptanceCriteria") or [],
                "changed_files": [
                    {
                        "path": f.get("path", ""),
                        "summary": f.get("summary") or f.get("description") or "",
                    }
                    for f in (task.get("changedFiles") or [])
                    if isinstance(f, dict)
                ][:20],
                "objective_signals": self._product_surface_objective_signals(completion_evidence),
            }
            config = context.get("config") if isinstance(context, dict) else None
            if isinstance(config, dict):
                input_context["config"] = config
            adapters = completion_evidence.get("advisorEvidenceAdapters")
            if isinstance(adapters, dict):
                input_context["advisor_evidence_adapters"] = adapters
            result = advisor.advise("product_surface_decision", input_context)
            return {
                "accepted": result.accepted,
                "source": result.source,
                "rationale": result.rationale,
                "fallback_reason": result.fallback_reason,
                "proposal_id": result.proposal_id,
                "model_id": getattr(result, "model_id", None),
                "validation_reasons": list(getattr(result, "validation_reasons", []) or []),
                "confidence": result.confidence,
                "payload": result.payload,
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning("Product surface advisor call failed for task %s: %s", task.get("id"), exc)
            return None

    def _should_skip_product_surface_advisor(
        self,
        task: dict[str, Any],
        context: dict[str, Any],
        completion_evidence: dict[str, Any],
    ) -> bool:
        if context.get("_skip_product_surface_advisor") is True:
            return True
        if self._should_skip_completion_advisor(task, context):
            return True
        advisor_config = self._advisor_config(context)
        if advisor_config.get("enableProductSurfaceAdvisor") is False:
            return True
        if context.get("_child_worker") is True or task.get("role", "root") != "root":
            return not bool(advisor_config.get("enableChildProductSurfaceAdvisor"))
        if not self._is_write_or_verification_task(task=task, context=context):
            return True
        return not bool(
            completion_evidence.get("changedFiles")
            or completion_evidence.get("commands")
            or completion_evidence.get("verification")
            or completion_evidence.get("testsRun")
            or completion_evidence.get("productAdvisories")
            or completion_evidence.get("acceptance")
        )

    def _product_surface_objective_signals(self, completion_evidence: dict[str, Any]) -> dict[str, Any]:
        acceptance = [
            {
                "criterion": item.get("criterion"),
                "status": item.get("status"),
                "source": item.get("source"),
                "apiMethod": item.get("apiMethod"),
                "apiPath": item.get("apiPath"),
                "issues": item.get("issues") or [],
            }
            for item in (completion_evidence.get("acceptance") or [])[:30]
            if isinstance(item, dict)
        ]
        verification = [
            {
                "command": item.get("command") or item.get("name"),
                "status": item.get("status"),
                "summary": item.get("summary"),
            }
            for item in (completion_evidence.get("verification") or [])[:20]
            if isinstance(item, dict)
        ]
        return {
            "evidence_level": completion_evidence.get("evidenceLevel"),
            "counts": completion_evidence.get("counts") or {},
            "acceptance": acceptance,
            "objective_product_advisories": completion_evidence.get("productAdvisories") or [],
            "verification": verification,
            "tests_run": completion_evidence.get("testsRun") or [],
            "advisor_evidence_adapters": completion_evidence.get("advisorEvidenceAdapters") or {},
        }

    def _record_product_surface_advisor_proposal(
        self,
        *,
        session_id: str,
        task: dict[str, Any],
        summary: str,
        product_surface_advice: dict[str, Any],
    ) -> str | None:
        try:
            payload = product_surface_advice.get("payload")
            proposal_payload = payload if isinstance(payload, dict) else {}
            source = {
                "type": product_surface_advice.get("source") or "unknown",
                "confidence": product_surface_advice.get("confidence"),
                "rationale": product_surface_advice.get("rationale") or "",
                "advisorProposalId": product_surface_advice.get("proposal_id"),
            }
            if product_surface_advice.get("fallback_reason"):
                source["fallbackReason"] = product_surface_advice["fallback_reason"]
            if product_surface_advice.get("validation_reasons"):
                source["validationReasons"] = product_surface_advice["validation_reasons"]
            if product_surface_advice.get("model_id"):
                source["model_id"] = product_surface_advice["model_id"]
            goal = str(task.get("goal") or "")
            input_summary = f"goal: {goal}\nsummary: {summary}"[:500]
            record = self._store.create_proposal({
                "kind": "product_surface_decision",
                "sessionId": session_id,
                "taskId": task["id"],
                "proposal": proposal_payload,
                "source": source,
                "inputSummary": input_summary,
                "modelId": product_surface_advice.get("model_id"),
            })
            proposal_record_id = record["proposal"]["id"]
            if product_surface_advice.get("accepted") is True:
                self._store.validate_proposal({
                    "proposalId": proposal_record_id,
                    "status": "accepted",
                    "reasons": [],
                })
                outcome = "accepted"
            else:
                reasons = [
                    str(item).strip()
                    for item in (product_surface_advice.get("validation_reasons") or [])
                    if str(item).strip()
                ]
                fallback = str(product_surface_advice.get("fallback_reason") or "").strip()
                if not reasons and fallback:
                    reasons.append(fallback)
                self._store.validate_proposal({
                    "proposalId": proposal_record_id,
                    "status": "rejected",
                    "reasons": reasons or ["product surface advisor rejected"],
                })
                outcome = "rejected"
            product_surface_advice["proposalRecordId"] = proposal_record_id
            payload_dict = proposal_payload if isinstance(proposal_payload, dict) else {}
            self._publish(
                session_id=session_id,
                task=task,
                event_type="agent.decision.product_surface",
                payload={
                    "proposalId": proposal_record_id,
                    "outcome": outcome,
                    "surfaceType": payload_dict.get("surface_type"),
                    "source": product_surface_advice.get("source"),
                    "confidence": product_surface_advice.get("confidence"),
                    "rationale": product_surface_advice.get("rationale"),
                },
            )
            return proposal_record_id
        except Exception:  # noqa: BLE001
            logger.debug("Failed to record product surface advisor proposal", exc_info=True)
            return None

    def _product_surface_advisory_from_advice(self, advice: dict[str, Any]) -> dict[str, Any] | None:
        if advice.get("accepted") is not True:
            return None
        payload = advice.get("payload") if isinstance(advice.get("payload"), dict) else {}
        surface_type = str(payload.get("surface_type") or "").strip()
        if not surface_type:
            return None
        recommended = self._completion_string_list(payload.get("recommended_verification"))
        evidence_requests = self._normalize_advisor_evidence_requests(payload.get("evidence_requests"))
        verification_intents = payload.get("verification_intents") if isinstance(payload.get("verification_intents"), list) else []
        has_blocking_request = any(item.get("blocking") is True for item in evidence_requests)
        summary = str(advice.get("rationale") or "").strip()
        if not summary:
            summary = f"LLM classified the task artifact surface as {surface_type}."
        return {
            "kind": "llm_product_surface",
            "surfaceType": surface_type,
            "severity": "suggestion" if recommended or verification_intents or evidence_requests else "info",
            "source": "llm_product_surface_advisor",
            "summary": summary[:500],
            "recommendedVerification": recommended,
            "verificationIntents": verification_intents[:10],
            "evidenceRequests": evidence_requests[:10],
            "hasBlockingEvidenceRequest": has_blocking_request,
            "confidence": advice.get("confidence"),
            "proposalRecordId": advice.get("proposalRecordId"),
        }

    def _advisor_requested_evidence_from_advice(
        self,
        *,
        advice: dict[str, Any],
        completion_evidence: dict[str, Any],
        tool_results: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        if advice.get("accepted") is not True:
            return []
        payload = advice.get("payload") if isinstance(advice.get("payload"), dict) else {}
        requests = self._normalize_advisor_evidence_requests(payload.get("evidence_requests"))
        if not requests:
            return []
        return [
            {
                **request,
                "status": self._advisor_evidence_request_status(
                    request=request,
                    completion_evidence=completion_evidence,
                    tool_results=tool_results or [],
                ),
                "source": "llm_product_surface_advisor",
                "proposalRecordId": advice.get("proposalRecordId"),
            }
            for request in requests
        ]

    def _normalize_advisor_evidence_requests(self, value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        requests: list[dict[str, Any]] = []
        for item in value:
            if isinstance(item, str):
                text = item.strip()
                if text:
                    requests.append({
                        "kind": "evidence",
                        "summary": text,
                        "blocking": False,
                    })
                continue
            if not isinstance(item, dict):
                continue
            kind = str(item.get("kind") or "evidence").strip() or "evidence"
            summary = str(
                item.get("summary")
                or item.get("description")
                or item.get("target")
                or item.get("why")
                or kind
            ).strip()
            request: dict[str, Any] = {
                "kind": kind,
                "summary": summary,
                "blocking": bool(item.get("blocking")) if isinstance(item.get("blocking"), bool) else False,
            }
            for key in ("target", "rationale", "suggestedCommand", "domain", "cwd", "shell"):
                if item.get(key) not in (None, ""):
                    request[key] = item[key]
            if request.get("suggestedCommand") in (None, "") and item.get("suggested_command") not in (None, ""):
                request["suggestedCommand"] = item.get("suggested_command")
            suggested_tool = self._normalize_advisor_evidence_tool_spec(
                item.get("suggestedTool") if item.get("suggestedTool") is not None else item.get("suggested_tool")
            )
            if suggested_tool:
                request["suggestedTool"] = suggested_tool
            for key in ("timeoutMs", "timeout_ms", "background"):
                if key in item and item.get(key) not in (None, ""):
                    request[key] = item[key]
            if isinstance(item.get("satisfied"), bool):
                request["satisfied"] = item["satisfied"]
            if isinstance(item.get("status"), str) and item["status"].strip():
                request["advisorStatus"] = item["status"].strip()
            expanded = self._expand_advisor_evidence_execution_requests(request=request, source=item)
            requests.extend(expanded or [request])
        return requests[:20]

    def _expand_advisor_evidence_execution_requests(
        self,
        *,
        request: dict[str, Any],
        source: dict[str, Any],
    ) -> list[dict[str, Any]]:
        commands = self._normalize_advisor_evidence_command_list(
            source.get("suggestedCommands") if source.get("suggestedCommands") is not None else source.get("suggested_commands")
        )
        tools = self._normalize_advisor_evidence_tool_list(
            source.get("suggestedTools") if source.get("suggestedTools") is not None else source.get("suggested_tools")
        )
        if not commands and not tools:
            return []
        expanded: list[dict[str, Any]] = []
        for command in commands:
            item = {key: value for key, value in request.items() if key != "suggestedTool"}
            item.update(command)
            expanded.append(item)
        for tool in tools:
            item = {key: value for key, value in request.items() if key != "suggestedCommand"}
            item["suggestedTool"] = tool
            expanded.append(item)
        return expanded

    @staticmethod
    def _normalize_advisor_evidence_command_list(value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        commands: list[dict[str, Any]] = []
        for item in value:
            if isinstance(item, str):
                command = item.strip()
                if command:
                    commands.append({"suggestedCommand": command})
                continue
            if not isinstance(item, dict):
                continue
            command = str(item.get("suggestedCommand") or item.get("suggested_command") or item.get("command") or "").strip()
            if not command:
                continue
            normalized: dict[str, Any] = {"suggestedCommand": command}
            for key in ("cwd", "shell", "timeoutMs", "timeout_ms", "background", "target", "rationale"):
                if item.get(key) not in (None, ""):
                    normalized[key] = item[key]
            commands.append(normalized)
        return commands

    def _normalize_advisor_evidence_tool_list(self, value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        tools: list[dict[str, Any]] = []
        for item in value:
            normalized = self._normalize_advisor_evidence_tool_spec(item)
            if normalized:
                tools.append(normalized)
        return tools

    @staticmethod
    def _normalize_advisor_evidence_tool_spec(value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        tool_name = value.get("name") or value.get("toolName")
        arguments = value.get("arguments")
        normalized_tool: dict[str, Any] = {}
        if isinstance(tool_name, str) and tool_name.strip():
            normalized_tool["name"] = tool_name.strip()
        if isinstance(arguments, dict):
            normalized_tool["arguments"] = dict(arguments)
        return normalized_tool

    def _advisor_evidence_request_status(
        self,
        *,
        request: dict[str, Any],
        completion_evidence: dict[str, Any],
        tool_results: list[dict[str, Any]] | None = None,
    ) -> str:
        if request.get("satisfied") is True:
            return "satisfied"
        if request.get("satisfied") is False:
            return "missing"
        advisor_status = str(request.get("advisorStatus") or "").strip().casefold()
        if advisor_status in {"satisfied", "present", "covered", "verified", "passed"}:
            return "satisfied"
        if advisor_status in {"missing", "absent", "unverified", "needed", "required"}:
            return "missing"
        if self._advisor_evidence_suggested_command_satisfied(
            request=request,
            completion_evidence=completion_evidence,
        ):
            return "satisfied"
        if self._advisor_evidence_suggested_tool_satisfied(
            request=request,
            tool_results=tool_results or [],
        ):
            return "satisfied"
        return "missing" if request.get("blocking") is True else "requested"

    def _advisor_evidence_suggested_command_satisfied(
        self,
        *,
        request: dict[str, Any],
        completion_evidence: dict[str, Any],
    ) -> bool:
        command = str(request.get("suggestedCommand") or "").strip()
        if not command:
            return False
        candidates = {self._advisor_evidence_command_key(command)}
        try:
            shell_name = normalize_shell(
                str(request.get("shell")).strip() if isinstance(request.get("shell"), str) else None,
                self._store,
            )
            candidates.add(self._advisor_evidence_command_key(_powershell_execution_command(command, shell_name)))
        except Exception:  # noqa: BLE001
            logger.debug("Failed to normalize advisor evidence command for satisfaction check", exc_info=True)

        commands = completion_evidence.get("commands")
        if not isinstance(commands, list):
            return False
        for item in commands:
            if not isinstance(item, dict) or not self._completion_item_passed(item):
                continue
            command_text = item.get("command")
            if self._advisor_evidence_command_key(command_text) in candidates:
                return True
        return False

    @staticmethod
    def _advisor_evidence_command_key(command: Any) -> str:
        return " ".join(str(command or "").strip().split()).casefold()

    def _advisor_evidence_suggested_tool_satisfied(
        self,
        *,
        request: dict[str, Any],
        tool_results: list[dict[str, Any]],
    ) -> bool:
        suggested_tool = request.get("suggestedTool")
        if not isinstance(suggested_tool, dict):
            return False
        tool_name = str(suggested_tool.get("name") or suggested_tool.get("toolName") or "").strip()
        if not tool_name:
            return False
        expected_arguments = suggested_tool.get("arguments")
        expected_subset = expected_arguments if isinstance(expected_arguments, dict) else {}
        for tool_result in tool_results:
            if not isinstance(tool_result, dict):
                continue
            if str(tool_result.get("name") or "").strip() != tool_name:
                continue
            result = tool_result.get("result") if isinstance(tool_result.get("result"), dict) else {}
            if self._completion_tool_result_failed(result):
                continue
            arguments = tool_result.get("arguments") if isinstance(tool_result.get("arguments"), dict) else {}
            if self._advisor_evidence_arguments_match_subset(arguments, expected_subset):
                return True
        return False

    def _advisor_evidence_arguments_match_subset(
        self,
        actual: dict[str, Any],
        expected: dict[str, Any],
    ) -> bool:
        for key, expected_value in expected.items():
            if key not in actual:
                return False
            if not self._advisor_evidence_argument_values_equal(actual.get(key), expected_value):
                return False
        return True

    def _advisor_evidence_argument_values_equal(self, actual: Any, expected: Any) -> bool:
        if isinstance(actual, dict) and isinstance(expected, dict):
            return self._advisor_evidence_arguments_match_subset(actual, expected)
        if isinstance(actual, list) and isinstance(expected, list):
            return actual == expected
        if isinstance(actual, str) and isinstance(expected, str):
            return actual.replace("\\", "/").strip() == expected.replace("\\", "/").strip()
        return actual == expected

    @staticmethod
    def _completion_string_list(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return [
            str(item).strip()
            for item in value
            if str(item).strip()
        ]

    def _consult_completion_advisor(
        self,
        task: dict[str, Any],
        summary: str,
        context: dict[str, Any] | None = None,
        *,
        completion_evidence: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Ask DecisionAdvisor whether the task is truly complete.

        Returns a dict with advisor result fields, or None if no advisor is configured.
        The advisor proposes, but runtime always proceeds with completion — the result
        is recorded in the decision trace for audit.
        """
        advisor = getattr(self, "_decision_advisor", None)
        if advisor is None:
            return None
        if self._should_skip_completion_advisor(task, context or {}):
            return None
        try:
            evidence = completion_evidence or self._build_completion_evidence(
                task=task,
                summary=summary,
                validation=None,
                tool_results=[],
                context=context or {},
            )
            input_context: dict[str, Any] = {
                "goal": task.get("goal", ""),
                "summary": summary[:2000],
                "acceptance_criteria": task.get("acceptanceCriteria") or [],
                "completion_evidence": evidence,
                "completion_audit": evidence.get("audit") or {},
                "product_advisories": evidence.get("productAdvisories") or [],
                "advisor_evidence_adapters": evidence.get("advisorEvidenceAdapters") or {},
                "changed_files": [
                    f.get("path", "") for f in (task.get("changedFiles") or [])
                    if isinstance(f, dict)
                ][:20],
            }
            config = (context or {}).get("config") if isinstance(context, dict) else None
            if isinstance(config, dict):
                input_context["config"] = config
            result = advisor.advise("completion_decision", input_context)
            return {
                "accepted": result.accepted,
                "source": result.source,
                "rationale": result.rationale,
                "fallback_reason": result.fallback_reason,
                "proposal_id": result.proposal_id,
                "model_id": getattr(result, "model_id", None),
                "validation_reasons": list(getattr(result, "validation_reasons", []) or []),
                "confidence": result.confidence,
                "payload": result.payload,
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning("Completion advisor call failed for task %s: %s", task.get("id"), exc)
            return None

    def _record_completion_advisor_proposal(
        self,
        *,
        session_id: str,
        task: dict[str, Any],
        summary: str,
        completion_advice: dict[str, Any],
        completion_evidence: dict[str, Any] | None = None,
    ) -> str | None:
        try:
            payload = completion_advice.get("payload")
            proposal_payload = payload if isinstance(payload, dict) else {}
            source = {
                "type": completion_advice.get("source") or "unknown",
                "confidence": completion_advice.get("confidence"),
                "rationale": completion_advice.get("rationale") or "",
                "advisorProposalId": completion_advice.get("proposal_id"),
            }
            if completion_advice.get("fallback_reason"):
                source["fallbackReason"] = completion_advice["fallback_reason"]
            if completion_advice.get("validation_reasons"):
                source["validationReasons"] = completion_advice["validation_reasons"]
            if completion_advice.get("model_id"):
                source["model_id"] = completion_advice["model_id"]
            if isinstance(completion_evidence, dict) and isinstance(completion_evidence.get("audit"), dict):
                source["completionAudit"] = completion_evidence["audit"]
            goal = str(task.get("goal") or "")
            audit = completion_evidence.get("audit") if isinstance(completion_evidence, dict) else {}
            audit_counts = audit.get("approvalCounts") if isinstance(audit, dict) else {}
            audit_summary = ""
            if isinstance(audit_counts, dict) and audit_counts:
                audit_summary = (
                    "\napprovals: "
                    f"total={audit_counts.get('total', 0)} "
                    f"approved={audit_counts.get('approved', 0)} "
                    f"rejected={audit_counts.get('rejected', 0)} "
                    f"pending={audit_counts.get('pending', 0)}"
                )
            input_summary = f"goal: {goal}\nsummary: {summary}{audit_summary}"[:500]
            record = self._store.create_proposal({
                "kind": "completion_decision",
                "sessionId": session_id,
                "taskId": task["id"],
                "proposal": proposal_payload,
                "source": source,
                "inputSummary": input_summary,
                "modelId": completion_advice.get("model_id"),
            })
            proposal_record_id = record["proposal"]["id"]
            if completion_advice.get("accepted") is True:
                self._store.validate_proposal({
                    "proposalId": proposal_record_id,
                    "status": "accepted",
                    "reasons": [],
                })
            else:
                reasons = [
                    str(item).strip()
                    for item in (completion_advice.get("validation_reasons") or [])
                    if str(item).strip()
                ]
                fallback = str(completion_advice.get("fallback_reason") or "").strip()
                if not reasons and fallback:
                    reasons.append(fallback)
                self._store.validate_proposal({
                    "proposalId": proposal_record_id,
                    "status": "rejected",
                    "reasons": reasons or ["completion advisor rejected"],
                })
            completion_advice["proposalRecordId"] = proposal_record_id
            return proposal_record_id
        except Exception:  # noqa: BLE001
            logger.debug("Failed to record completion advisor proposal", exc_info=True)
            return None

    def _should_skip_completion_advisor(self, task: dict[str, Any], context: dict[str, Any]) -> bool:
        if context.get("_skip_completion_advisor") is True:
            return True
        if context.get("_child_worker") is True or task.get("role", "root") != "root":
            return not bool(self._advisor_config(context).get("enableChildCompletionAdvisor"))
        return False

    def _build_completion_evidence(
        self,
        *,
        task: dict[str, Any],
        summary: str,
        validation: dict[str, Any] | None,
        tool_results: list[dict[str, Any]],
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        criteria = [
            str(item).strip()
            for item in (task.get("acceptanceCriteria") or [])
            if str(item).strip()
        ]
        changed_files = [
            dict(item) for item in (task.get("changedFiles") or [])
            if isinstance(item, dict)
        ]
        commands = [
            dict(item) for item in (task.get("commands") or [])
            if isinstance(item, dict)
        ]
        child_evidence = self._completion_child_task_evidence(task)
        changed_files = self._merge_completion_changed_files(
            changed_files,
            child_evidence["changedFiles"],
        )
        commands = self._merge_completion_commands(
            commands,
            child_evidence["commands"],
        )
        commands = self._merge_completion_commands(
            commands,
            self._completion_commands_from_store(task.get("id")),
        )
        verification = [
            dict(item) for item in (task.get("verification") or [])
            if isinstance(item, dict)
        ]
        verification = self._merge_completion_verification_records(
            verification,
            child_evidence["verification"],
        )
        tests_run = [
            dict(item) for item in (task.get("testsRun") or [])
            if isinstance(item, dict)
        ]
        tests_run = self._merge_completion_tests_run(
            tests_run,
            child_evidence["testsRun"],
        )
        patches = self._completed_patch_results(tool_results)
        tool_evidence = self._completion_tool_evidence(tool_results)
        tool_evidence.extend(child_evidence["toolResults"])
        changed_files = self._merge_completion_changed_files(
            changed_files,
            self._completion_changed_files_from_tool_evidence(tool_evidence),
        )
        commands = self._merge_completion_commands(
            commands,
            self._completion_commands_from_tool_evidence(tool_evidence),
        )
        command_verification = self._completion_tests_run_from_commands(commands, context=context or {})
        tests_run = self._merge_completion_tests_run(tests_run, command_verification)
        failed_tool_results = self._unresolved_failed_tool_results(
            tool_evidence,
            changed_files=changed_files,
            verification=verification,
            tests_run=tests_run,
        )
        resolved_failed_tool_results = [
            item for item in tool_evidence
            if item.get("failed") is True and item not in failed_tool_results
        ]
        validation_checks = [
            dict(item) for item in ((validation or {}).get("checks") or [])
            if isinstance(item, dict)
        ]

        failed_verification = [
            item for item in verification
            if item.get("status") in {"failed", "timeout", "killed", "validation_failed"}
        ]
        passed_verification = [item for item in verification if item.get("status") == "passed"]
        unresolved_failed_verification = self._unresolved_failed_verification_items(
            verification,
            passed_statuses={"passed", "success", "completed"},
            failed_statuses={"failed", "timeout", "killed", "validation_failed"},
        )
        passed_tests_run = [
            item for item in tests_run
            if item.get("status") in {"passed", "success", "completed"}
        ]
        failed_tests_run = [
            item for item in tests_run
            if item.get("status") in {"failed", "timeout", "killed", "validation_failed"}
        ]
        unresolved_failed_tests_run = self._unresolved_failed_verification_items(
            tests_run,
            passed_statuses={"passed", "success", "completed"},
            failed_statuses={"failed", "timeout", "killed", "validation_failed"},
        )
        resolved_failed_tests_run = [
            item for item in failed_tests_run
            if item not in unresolved_failed_tests_run
        ]
        failed_validation_checks = [
            item for item in validation_checks
            if item.get("status") in {"failed", "timeout", "killed", "validation_failed"}
        ]
        verification_for_requirements = [
            *verification,
            *command_verification,
        ]
        verification_requirements = self._completion_verification_requirements(
            task=task,
            context=context or {},
            changed_files=changed_files,
            verification=verification_for_requirements,
            tests_run=tests_run,
        )

        has_workspace_evidence = bool(changed_files or patches)
        has_command_evidence = bool(commands)
        has_verification_evidence = bool(passed_verification or passed_tests_run)
        has_failed_evidence = bool(unresolved_failed_verification or failed_validation_checks)
        if has_failed_evidence:
            status = "needs_attention"
            evidence_level = "failed_verification"
        elif has_verification_evidence:
            status = "success"
            evidence_level = "verified"
        elif has_workspace_evidence or has_command_evidence or tool_evidence:
            status = "success"
            evidence_level = "runtime_evidence"
        else:
            status = "unverified"
            evidence_level = "summary_only"

        product_advisories = self._completion_product_advisories(
            task=task,
            context=context or {},
            verification=verification,
            tests_run=tests_run,
            commands=commands,
        )

        acceptance = self._completion_acceptance_evidence(
            criteria=criteria,
            evidence_level=evidence_level,
            task=task,
            context=context or {},
            validation=validation or {},
            tool_results=tool_results,
        )
        failed_acceptance = [
            item for item in acceptance
            if item.get("status") in {"failed", "unsupported"}
        ]
        unverified_acceptance = [
            item for item in acceptance
            if item.get("status") == "unverified" and item.get("source") != "inferred_summary_only"
        ]

        return {
            "status": status,
            "evidenceLevel": evidence_level,
            "summaryOnly": evidence_level == "summary_only",
            "acceptance": acceptance,
            "acceptanceCriteria": criteria,
            "changedFiles": changed_files,
            "commands": commands,
            "verification": verification,
            "verificationRequirements": verification_requirements,
            "testsRun": tests_run,
            "patches": patches,
            "toolResults": tool_evidence,
            "childTasks": child_evidence["childTasks"],
            "unresolvedToolFailures": failed_tool_results,
            "productAdvisories": product_advisories,
            "validation": {
                "checks": validation_checks,
                "summary": (validation or {}).get("summary"),
                "ran": (validation or {}).get("ran") or [],
            },
            "counts": {
                "acceptanceCriteria": len(criteria),
                "changedFiles": len(changed_files),
                "commands": len(commands),
                "verification": len(verification),
                "passedVerification": len(passed_verification),
                "failedVerification": len(unresolved_failed_verification) + len(failed_validation_checks) + len(unresolved_failed_tests_run),
                "requiredVerificationFamilies": len(verification_requirements.get("required") or []),
                "missingVerificationFamilies": len(verification_requirements.get("missing") or []),
                "testsRun": len(tests_run),
                "passedTestsRun": len(passed_tests_run),
                "failedTestsRun": len(unresolved_failed_tests_run),
                "resolvedFailedTestsRun": len(resolved_failed_tests_run),
                "patches": len(patches),
                "toolResults": len(tool_evidence),
                "childTasks": len(child_evidence["childTasks"]),
                "failedToolResults": len(failed_tool_results),
                "resolvedFailedToolResults": len(resolved_failed_tool_results),
                "acceptedAcceptanceCriteria": len([
                    item for item in acceptance
                    if item.get("status") == "supported" and item.get("source") != "inferred_runtime_evidence"
                ]),
                "failedAcceptanceCriteria": len(failed_acceptance),
                "unverifiedAcceptanceCriteria": len(unverified_acceptance),
                "explicitAcceptanceCriteria": len([
                    item for item in acceptance
                    if str(item.get("source") or "").startswith("explicit")
                ]),
            },
            "summaryPreview": summary[:500],
        }

    def _completion_tests_run_from_commands(
        self,
        commands: list[dict[str, Any]],
        *,
        context: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        tests_run: list[dict[str, Any]] = []
        for command_record in commands:
            if not isinstance(command_record, dict):
                continue
            command = str(command_record.get("command") or "").strip()
            if not command or not self._completion_text_mentions_targeted_verification(command):
                continue
            status = str(command_record.get("status") or "").strip().lower()
            exit_code = command_record.get("exitCode")
            passed = status in {"completed", "passed", "success"} and exit_code in (0, "0", None)
            failed = status in {"failed", "timeout", "killed", "validation_failed"} or (
                isinstance(exit_code, int) and exit_code != 0
            )
            normalized_status = "passed" if passed else "failed" if failed else status or "unknown"
            tests_run.append({
                "id": command_record.get("id"),
                "name": command,
                "command": command,
                "cwd": command_record.get("cwd"),
                "status": normalized_status,
                "exitCode": exit_code,
                "durationMs": command_record.get("durationMs"),
                "summary": command_record.get("summary") or f"Command {normalized_status}",
                "source": "run_command",
                "startedAt": command_record.get("startedAt"),
                "finishedAt": command_record.get("finishedAt"),
            })
            recovered = self._completion_recover_failed_command_verification(
                command_record=command_record,
                context=context or {},
            )
            if recovered is not None:
                tests_run.append(recovered)
        return [
            {key: value for key, value in item.items() if value not in (None, "", [])}
            for item in tests_run
        ]

    def _completion_recover_failed_command_verification(
        self,
        *,
        command_record: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any] | None:
        command = str(command_record.get("command") or "").strip()
        status = str(command_record.get("status") or "").strip().lower()
        exit_code = command_record.get("exitCode")
        failed = status in {"failed", "timeout", "killed", "validation_failed"} or (
            isinstance(exit_code, int) and exit_code != 0
        )
        if not failed or not self._completion_command_is_node_check(command):
            return None
        if not self._completion_command_failure_looks_environmental(command_record):
            return None
        script_path, run_cwd = self._completion_node_check_command_target(command=command, context=context, cwd=command_record.get("cwd"))
        if script_path is None or run_cwd is None:
            return None
        node_executable = resolve_node_executable()
        if not node_executable:
            return None
        try:
            proc = subprocess.run(
                [node_executable, "--check", str(script_path)],
                cwd=run_cwd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
            )
        except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as exc:
            summary = str(exc)
            normalized_status = "failed"
            recovered_exit_code: int | None = 1
        else:
            summary = (proc.stderr or proc.stdout or "").strip()
            normalized_status = "passed" if proc.returncode == 0 else "failed"
            recovered_exit_code = proc.returncode
        return {
            "id": f"{command_record.get('id') or 'command'}:recovery",
            "name": command,
            "command": command,
            "cwd": command_record.get("cwd"),
            "status": normalized_status,
            "exitCode": recovered_exit_code,
            "summary": summary or ("Command passed after runtime recovery." if normalized_status == "passed" else "Command failed after runtime recovery."),
            "source": "run_command_recovery",
        }

    @staticmethod
    def _completion_command_is_node_check(command: str) -> bool:
        return "node --check" in command.casefold()

    @staticmethod
    def _completion_command_failure_looks_environmental(command_record: dict[str, Any]) -> bool:
        summary = str(command_record.get("summary") or "").casefold()
        markers = (
            "无法将“node”项识别为",
            "无法将'node'项识别为",
            "not recognized",
            "command not found",
            "no such file",
            "file not found",
        )
        return any(marker in summary for marker in markers)

    def _completion_node_check_command_target(
        self,
        *,
        command: str,
        context: dict[str, Any],
        cwd: Any,
    ) -> tuple[Path | None, Path | None]:
        match = re.match(r"""^\s*node\s+--check\s+(?P<target>.+?)\s*$""", command, re.IGNORECASE)
        if not match:
            return None, None
        target_text = str(match.group("target") or "").strip().strip("\"'")
        if not target_text:
            return None, None
        workspace_root = str(context.get("workspace_root") or context.get("workspaceRoot") or "").strip()
        if not workspace_root:
            return None, None
        root = Path(workspace_root).resolve()
        run_cwd = root
        cwd_text = str(cwd or "").strip()
        if cwd_text and cwd_text != ".":
            candidate_cwd = Path(cwd_text)
            run_cwd = candidate_cwd.resolve() if candidate_cwd.is_absolute() else (root / candidate_cwd).resolve()
        target_path = Path(target_text)
        script_path = target_path.resolve() if target_path.is_absolute() else (run_cwd / target_path).resolve()
        return script_path, run_cwd

    def _completion_commands_from_tool_evidence(self, tool_evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
        commands: list[dict[str, Any]] = []
        for item in tool_evidence:
            if item.get("name") != "run_command":
                continue
            if item.get("failed") is True:
                continue
            command = str(item.get("command") or "").strip()
            if not command:
                continue
            commands.append({
                "command": command,
                "status": item.get("status"),
                "exitCode": item.get("exitCode"),
                "summary": item.get("summary"),
            })
        return commands

    def _completion_commands_from_store(self, task_id: Any) -> list[dict[str, Any]]:
        task_id_text = str(task_id or "").strip()
        if not task_id_text or not hasattr(self._store, "list_command_logs"):
            return []
        try:
            records = self._store.list_command_logs({"taskId": task_id_text, "limit": 100}).get("commandLogs", [])
        except Exception:  # noqa: BLE001
            return []
        commands: list[dict[str, Any]] = []
        for record in records:
            if not isinstance(record, dict):
                continue
            command = str(record.get("command") or "").strip()
            if not command:
                continue
            summary = record.get("summary")
            if not summary:
                stderr_path = str(record.get("stderrPath") or "").strip()
                if stderr_path:
                    try:
                        summary = Path(stderr_path).read_text(encoding="utf-8", errors="replace").strip()
                    except OSError:
                        summary = None
            commands.append({
                "id": record.get("id"),
                "command": command,
                "cwd": record.get("cwd"),
                "status": record.get("status"),
                "exitCode": record.get("exitCode"),
                "summary": summary,
                "startedAt": record.get("startedAt"),
                "finishedAt": record.get("finishedAt"),
            })
        return commands

    def _completion_child_task_evidence(self, task: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
        empty: dict[str, list[dict[str, Any]]] = {
            "changedFiles": [],
            "commands": [],
            "verification": [],
            "testsRun": [],
            "toolResults": [],
            "childTasks": [],
        }
        task_id = str(task.get("id") or "").strip()
        root_task_id = str(task.get("rootTaskId") or task_id).strip()
        session_id = str(task.get("sessionId") or "").strip()
        if not task_id or not session_id:
            return empty

        for child in self._completion_child_runtime_tasks(task_id=task_id, root_task_id=root_task_id, session_id=session_id):
            child_id = str(child.get("id") or "").strip()
            if not child_id:
                continue
            child_status = str(child.get("status") or "").strip().lower()
            child_summary = str(child.get("resultSummary") or child.get("summary") or "").strip()
            child_record = {
                "id": child_id,
                "status": child_status,
                "role": child.get("role") or "root",
                "summary": child_summary[:500],
                "source": "child_runtime_task",
            }
            empty["childTasks"].append({key: value for key, value in child_record.items() if value not in (None, "", [])})
            empty["changedFiles"].extend(self._completion_child_items(child, "changedFiles", child_id))
            empty["commands"].extend(self._completion_child_items(child, "commands", child_id))
            empty["verification"].extend(self._completion_child_items(child, "verification", child_id))
            empty["testsRun"].extend(self._completion_child_items(child, "testsRun", child_id))
            empty["commands"] = self._merge_completion_commands(
                empty["commands"],
                [
                    {**item, "source": "child_command_log", "childTaskId": child_id}
                    for item in self._completion_commands_from_store(child_id)
                ],
            )
            child_tool_result = {
                "name": "child_task",
                "status": child_status,
                "failed": child_status in {"failed", "cancelled"},
                "summary": child_summary or f"Child task {child_status}",
                "childTaskId": child_id,
                "source": "child_runtime_task",
                "role": child.get("role") or "root",
            }
            routing = child.get("routing") if isinstance(child.get("routing"), dict) else {}
            profile = routing.get("profile") if isinstance(routing.get("profile"), dict) else {}
            if profile:
                if isinstance(profile.get("ownedScope"), list):
                    child_tool_result["ownedScope"] = [
                        str(item).strip() for item in profile.get("ownedScope") if str(item).strip()
                    ]
                if isinstance(profile.get("expectedArtifacts"), list):
                    child_tool_result["expectedArtifacts"] = profile.get("expectedArtifacts")
                if isinstance(profile.get("verificationRequirements"), list):
                    child_tool_result["verificationRequirements"] = profile.get("verificationRequirements")
            if child_status in {"completed", "success"}:
                child_tool_result["failed"] = False
            empty["toolResults"].append({
                key: value for key, value in child_tool_result.items() if value not in (None, "", [])
            })

        for collab in self._completion_collaboration_tasks(parent_task_id=task_id, session_id=session_id):
            collab_id = str(collab.get("id") or "").strip()
            if not collab_id:
                continue
            status = str(collab.get("status") or "").strip().lower()
            result = collab.get("result") if isinstance(collab.get("result"), dict) else {}
            summary = str(result.get("summary") or collab.get("title") or "").strip()
            empty["childTasks"].append({
                "id": collab_id,
                "status": status,
                "title": collab.get("title"),
                "agentType": (collab.get("metadata") or {}).get("agentType") if isinstance(collab.get("metadata"), dict) else None,
                "summary": summary[:500],
                "source": "collaboration_task",
            })
            empty["changedFiles"].extend(self._completion_child_items(result, "changedFiles", collab_id))
            empty["commands"].extend(self._completion_child_items(result, "commands", collab_id))
            empty["verification"].extend(self._completion_child_items(result, "verification", collab_id))
            empty["testsRun"].extend(self._completion_child_items(result, "testsRun", collab_id))
            collab_tool_result = {
                "name": "child_task",
                "status": status,
                "failed": status in {"failed", "cancelled"},
                "summary": summary or f"Collaboration task {status}",
                "childTaskId": collab_id,
                "source": "collaboration_task",
                "title": collab.get("title"),
                "agentType": (collab.get("metadata") or {}).get("agentType") if isinstance(collab.get("metadata"), dict) else None,
            }
            if isinstance(result.get("changedFiles"), list):
                collab_tool_result["changedFiles"] = result.get("changedFiles")
            if isinstance(result.get("verification"), list):
                collab_tool_result["verification"] = result.get("verification")
            if isinstance(result.get("testsRun"), list):
                collab_tool_result["testsRun"] = result.get("testsRun")
            if status in {"completed", "success"}:
                collab_tool_result["failed"] = False
            empty["toolResults"].append({
                key: value for key, value in collab_tool_result.items() if value not in (None, "", [])
            })
        empty["childTasks"] = self._dedupe_completion_child_tasks(empty["childTasks"])
        return empty

    def _completion_child_runtime_tasks(
        self,
        *,
        task_id: str,
        root_task_id: str,
        session_id: str,
    ) -> list[dict[str, Any]]:
        if not hasattr(self._store, "list_tasks"):
            return []
        try:
            tasks = self._store.list_tasks({"sessionId": session_id}).get("tasks", [])
        except Exception:  # noqa: BLE001
            return []
        children: list[dict[str, Any]] = []
        for candidate in tasks:
            if not isinstance(candidate, dict):
                continue
            candidate_id = str(candidate.get("id") or "").strip()
            if not candidate_id or candidate_id == task_id:
                continue
            candidate_root = str(candidate.get("rootTaskId") or candidate_id).strip()
            routing = candidate.get("routing") if isinstance(candidate.get("routing"), dict) else {}
            parent_id = str(routing.get("parentRuntimeTaskId") or "").strip()
            if candidate_root == root_task_id or parent_id == task_id:
                children.append(candidate)
        return children

    def _completion_collaboration_tasks(self, *, parent_task_id: str, session_id: str) -> list[dict[str, Any]]:
        if not hasattr(self._store, "list_collaboration_tasks"):
            return []
        try:
            tasks = self._store.list_collaboration_tasks({"parentTaskId": parent_task_id}).get("tasks", [])
        except Exception:  # noqa: BLE001
            tasks = []
        if not tasks:
            try:
                tasks = self._store.list_collaboration_tasks({"sessionId": session_id}).get("tasks", [])
            except Exception:  # noqa: BLE001
                tasks = []
            tasks = [
                item for item in tasks
                if isinstance(item, dict) and str(item.get("parentTaskId") or "").strip() == parent_task_id
            ]
        return [item for item in tasks if isinstance(item, dict)]

    def _completion_child_items(self, source: dict[str, Any], key: str, child_id: str) -> list[dict[str, Any]]:
        value = source.get(key)
        if not isinstance(value, list):
            return []
        items: list[dict[str, Any]] = []
        for item in value:
            if not isinstance(item, dict):
                continue
            record = dict(item)
            record.setdefault("childTaskId", child_id)
            record.setdefault("source", "child_task")
            items.append(record)
        return items

    def _dedupe_completion_child_tasks(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        seen: set[tuple[str, str]] = set()
        deduped: list[dict[str, Any]] = []
        for item in items:
            item_id = str(item.get("id") or "").strip()
            source = str(item.get("source") or "").strip()
            key = (item_id, source)
            if not item_id or key in seen:
                continue
            seen.add(key)
            deduped.append({k: v for k, v in item.items() if v not in (None, "", [])})
        return deduped

    def _merge_completion_commands(
        self,
        current: list[dict[str, Any]],
        additions: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        merged: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for item in [*current, *additions]:
            command = str(item.get("command") or "").strip()
            status = str(item.get("status") or "").strip()
            key = (command, status)
            if not command or key in seen:
                continue
            seen.add(key)
            merged.append(dict(item))
        return merged

    def _merge_completion_verification_records(
        self,
        current: list[dict[str, Any]],
        additions: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        merged: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str]] = set()
        for item in [*current, *additions]:
            if not isinstance(item, dict):
                continue
            key = (
                str(item.get("id") or "").strip(),
                str(item.get("command") or item.get("name") or item.get("suite") or "").strip(),
                str(item.get("status") or "").strip(),
            )
            if key in seen:
                continue
            seen.add(key)
            merged.append(dict(item))
        return merged

    def _completion_changed_files_from_tool_evidence(self, tool_evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
        changed_files: list[dict[str, Any]] = []
        for item in tool_evidence:
            if item.get("name") not in {"apply_patch", "write_file"}:
                continue
            for path in item.get("changedPaths") or []:
                if isinstance(path, str) and path.strip():
                    changed_files.append({"path": path.strip(), "source": item.get("name")})
        return changed_files

    def _merge_completion_changed_files(
        self,
        current: list[dict[str, Any]],
        additions: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        merged: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in [*current, *additions]:
            path = self._completion_changed_file_path(item)
            if not path or path in seen:
                continue
            seen.add(path)
            merged.append(dict(item))
        return merged

    def _merge_completion_tests_run(
        self,
        current: list[dict[str, Any]],
        additions: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        merged: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for item in [*current, *additions]:
            if not isinstance(item, dict):
                continue
            key = (
                str(item.get("id") or "").strip(),
                str(item.get("command") or item.get("name") or "").strip(),
            )
            if key in seen:
                continue
            seen.add(key)
            merged.append(dict(item))
        return merged

    def _unresolved_failed_verification_items(
        self,
        items: list[dict[str, Any]],
        *,
        passed_statuses: set[str],
        failed_statuses: set[str],
    ) -> list[dict[str, Any]]:
        unresolved: list[dict[str, Any]] = []
        normalized_items = self._completion_chronological_items([
            item for item in items
            if isinstance(item, dict)
        ])
        passed_items = [
            item for item in normalized_items
            if str(item.get("status") or "").strip().lower() in passed_statuses
        ]
        for index, item in enumerate(normalized_items):
            status = str(item.get("status") or "").strip().lower()
            if status not in failed_statuses:
                continue
            has_later_success = False
            for later in normalized_items[index + 1 :]:
                if self._completion_verification_items_match(item, later):
                    has_later_success = True
                    break
            if not has_later_success and self._completion_item_timestamp(item) is None:
                has_later_success = any(
                    self._completion_verification_items_match(item, passed_item)
                    for passed_item in passed_items
                )
            if not has_later_success:
                unresolved.append(item)
        return unresolved

    def _completion_verification_items_match(
        self,
        failed_item: dict[str, Any],
        passed_item: dict[str, Any],
    ) -> bool:
        passed_status = str(passed_item.get("status") or "").strip().lower()
        if passed_status not in {"passed", "success", "completed"}:
            return False
        failed_keys = self._completion_verification_resolution_keys(failed_item)
        passed_keys = self._completion_verification_resolution_keys(passed_item)
        if failed_keys and passed_keys:
            return not failed_keys.isdisjoint(passed_keys)
        failed_families = self._completion_verification_item_families(failed_item)
        passed_families = self._completion_verification_item_families(passed_item)
        if failed_families and passed_families:
            return not failed_families.isdisjoint(passed_families)
        failed_identity = self._completion_verification_identity(failed_item)
        passed_identity = self._completion_verification_identity(passed_item)
        if failed_identity and passed_identity:
            return failed_identity == passed_identity
        return bool(failed_keys or passed_keys or failed_families or passed_families)

    def _completion_chronological_items(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        indexed = list(enumerate(items))
        if not any(self._completion_item_timestamp(item) is not None for _, item in indexed):
            return items
        return [
            item for index, item in sorted(
                indexed,
                key=lambda pair: (
                    self._completion_item_timestamp(pair[1]) is None,
                    self._completion_item_timestamp(pair[1]) or 0,
                    pair[0],
                ),
            )
        ]

    def _completion_item_timestamp(self, item: dict[str, Any]) -> float | None:
        for key in ("startedAt", "createdAt", "finishedAt", "completedAt", "updatedAt"):
            value = item.get(key)
            if isinstance(value, (int, float)):
                return float(value)
            if isinstance(value, str) and value.strip():
                try:
                    return float(value)
                except ValueError:
                    continue
        return None

    def _completion_verification_resolution_keys(self, item: dict[str, Any]) -> set[str]:
        text = " ".join(
            str(item.get(key) or "")
            for key in ("command", "name", "summary", "suite")
        ).casefold()
        token_map = {
            "python:test": ("pytest", "unittest", "tox"),
            "python:typecheck": ("mypy", "pyright"),
            "python:syntax": ("py_compile", "compileall"),
            "python:lint": ("ruff",),
            "javascript:test": (
                "jest",
                "vitest",
                "playwright",
                "cypress",
                "npm test",
                "npm run test",
                "npm run test:",
                "pnpm test",
                "pnpm run test",
                "pnpm run test:",
                "yarn test",
                "yarn run test",
                "yarn run test:",
                "bun test",
                "bun run test",
            ),
            "javascript:typecheck": (
                "tsc",
                "typecheck",
                "type check",
                "node --check",
                "npm run tsc",
                "pnpm run tsc",
                "yarn run tsc",
            ),
            "javascript:lint": ("eslint", "npm run lint", "pnpm run lint", "yarn run lint"),
            "javascript:build": (
                "npm run build",
                "pnpm build",
                "pnpm run build",
                "yarn build",
                "yarn run build",
                "bun run build",
                "vite",
                "next build",
            ),
            "rust:test": ("cargo test",),
            "rust:check": ("cargo check", "cargo clippy"),
            "rust:build": ("cargo build",),
            "go:test": ("go test",),
            "go:build": ("go build",),
            "go:lint": ("go vet",),
            "jvm:test": ("mvn test", "maven test", "gradle test", "./gradlew test", "gradlew test"),
            "dotnet:test": ("dotnet test",),
            "dotnet:build": ("dotnet build",),
            "ruby:test": ("rspec", "ruby test"),
            "php:test": ("phpunit", "composer test"),
            "swift:test": ("swift test", "xcodebuild test"),
            "native:test": ("make test", "ctest", "ninja test"),
            "native:build": ("cmake",),
        }
        keys = {
            key
            for key, tokens in token_map.items()
            if any(token in text for token in tokens)
        }
        if "python -m pytest" in text:
            keys.add("python:test")
        return keys

    def _completion_verification_identity(self, item: dict[str, Any]) -> str:
        text = " ".join(
            str(item.get(key) or "")
            for key in ("command", "name", "suite")
        ).casefold()
        text = re.sub(r'cd\s+["\']?[^;&|]+["\']?\s*(?:&&|;)?', " ", text)
        text = re.sub(r'["\'][a-z]:[^"\']+["\']', " ", text)
        text = re.sub(r'(?<![a-z0-9_])[a-z]:[/\\][^ ]*python(?:\.exe)?', " python", text)
        text = re.sub(r'(?<![a-z0-9_])[a-z]:[/\\][^ ]*node(?:\.exe)?', " node", text)
        text = re.sub(r'&\s*"[^"]*python(?:\.exe)?"', " python", text)
        text = re.sub(r'&\s*"[^"]*node(?:\.exe)?"', " node", text)
        text = re.sub(r"\s+", " ", text)
        return text.strip()

    def _completion_acceptance_evidence(
        self,
        *,
        criteria: list[str],
        evidence_level: str,
        task: dict[str, Any],
        context: dict[str, Any],
        validation: dict[str, Any],
        tool_results: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        structural_acceptance = self._completion_structural_acceptance_records(task=task, context=context)
        if not criteria and not structural_acceptance:
            return []

        explicit_records = self._completion_explicit_acceptance_records(
            task=task,
            validation=validation,
            tool_results=tool_results,
        )
        has_explicit_acceptance = bool(explicit_records)
        matched: dict[int, dict[str, Any]] = {}
        for record in explicit_records:
            match_index = self._completion_acceptance_match_index(record, criteria)
            if match_index is None or match_index in matched:
                continue
            matched[match_index] = record

        acceptance: list[dict[str, Any]] = []
        for index, criterion in enumerate(criteria):
            record = matched.get(index)
            if record is not None:
                acceptance.append(
                    {
                        "criterion": criterion,
                        "status": record["status"],
                        "evidenceLevel": evidence_level,
                        "source": record["source"],
                    }
                )
            elif has_explicit_acceptance:
                acceptance.append(
                    {
                        "criterion": criterion,
                        "status": "unverified",
                        "evidenceLevel": evidence_level,
                        "source": "explicit_missing",
                    }
                )
            else:
                acceptance.append(
                    {
                        "criterion": criterion,
                        "status": "supported" if evidence_level != "summary_only" else "unverified",
                        "evidenceLevel": evidence_level,
                        "source": "inferred_runtime_evidence" if evidence_level != "summary_only" else "inferred_summary_only",
                    }
                )
        acceptance.extend(structural_acceptance)
        return acceptance

    def _completion_structural_acceptance_records(
        self,
        *,
        task: dict[str, Any],
        context: dict[str, Any],
    ) -> list[dict[str, Any]]:
        workspace_root = str(context.get("workspace_root") or context.get("workspaceRoot") or "").strip()
        if not workspace_root:
            return []
        root = Path(workspace_root)
        if not root.exists() or not root.is_dir():
            return []
        contract = self._completion_contract_profile(task=task, context=context)
        expected_paths = self._completion_contract_expected_artifact_paths(
            contract,
            include_owned_scope_fallback=not self._completion_contract_role_is_read_only(task=task, context=context),
        )
        if expected_paths:
            test_expectation = None
        else:
            task_role = str(task.get("role") or "").strip().lower()
            if task_role in {"planner", "worker", "reviewer", "summarizer"}:
                return []
            text = "\n".join(
                str(value or "")
                for value in [
                    task.get("goal"),
                    task.get("resultSummary"),
                    "\n".join(str(item) for item in (task.get("acceptanceCriteria") or [])),
                ]
            )
            expected_paths = self._completion_expected_artifact_paths(text)
            test_expectation = self._completion_expected_pytest_file_count(text)
        records: list[dict[str, Any]] = []
        completion_evidence_stub = {
            "changedFiles": task.get("changedFiles") or [],
            "verification": task.get("verification") or [],
            "testsRun": task.get("testsRun") or [],
        }
        for path in expected_paths:
            exists = (root / path).exists()
            if not exists and self._completion_has_python_layout_coverage(
                completion_evidence_stub,
                expected_paths=[path],
            ):
                exists = True
            records.append({
                "criterion": f"Expected artifact exists: {path}",
                "status": "supported" if exists else "failed",
                "evidenceLevel": "structural",
                "source": "structural_file_check",
            })
        records.extend(self._completion_product_readability_records(root=root, task=task))
        records.extend(self._completion_static_frontend_asset_records(root=root, task=task))
        if test_expectation is not None:
            found = self._completion_python_test_file_count(root)
            if found < test_expectation and self._completion_has_python_layout_coverage(
                completion_evidence_stub,
                test_expectation=test_expectation,
            ):
                found = test_expectation
            records.append({
                "criterion": f"Expected pytest file count >= {test_expectation}",
                "status": "supported" if found >= test_expectation else "failed",
                "evidenceLevel": "structural",
                "source": "structural_test_file_count",
            })
        return records

    def _completion_product_advisories(
        self,
        *,
        task: dict[str, Any],
        context: dict[str, Any],
        verification: list[dict[str, Any]],
        tests_run: list[dict[str, Any]],
        commands: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        workspace_root = str(context.get("workspace_root") or context.get("workspaceRoot") or "").strip()
        if not workspace_root:
            return []
        root = Path(workspace_root)
        if not root.exists() or not root.is_dir():
            return []
        advisories: list[dict[str, Any]] = []
        references = self._completion_frontend_api_references(root=root, task=task)
        if references:
            backend_routes = self._completion_backend_api_routes(root=root, task=task)
            api_observations = self._completion_api_contract_observations(
                references=references,
                backend_routes=backend_routes,
            )
            signals = self._completion_product_advisory_signals(
                verification=verification,
                tests_run=tests_run,
                commands=commands,
            )
            matched = [item for item in api_observations if item.get("backendRoute")]
            advisories.append({
                "kind": "api_reference_observation",
                "severity": "info" if signals else "suggestion",
                "source": "objective_surface_scan",
                "summary": (
                    "Changed files include API calls and runtime verification evidence was found."
                    if signals
                    else "Changed files include API calls; ask the product-surface advisor what evidence matters for this task."
                ),
                "recommendedVerification": [],
                "apiReferences": api_observations[:10],
                "localBackendRouteMatches": len(matched),
                "signals": signals[:10],
            })
        docs_observations = self._completion_docs_quality_records(root=root, task=task)
        if docs_observations:
            issue_count = sum(len(item.get("issues") or []) for item in docs_observations)
            advisories.append({
                "kind": "docs_quality_observation",
                "severity": "suggestion" if issue_count else "info",
                "source": "objective_surface_scan",
                "summary": (
                    f"{len(docs_observations)} changed docs or README file(s) include {issue_count} structure issue(s); "
                    "ask the product-surface advisor what evidence matters for this task."
                    if issue_count
                    else f"{len(docs_observations)} changed docs or README file(s) look structurally healthy."
                ),
                "recommendedVerification": [],
                "documents": docs_observations[:10],
                "issueCount": issue_count,
                "signals": [
                    {
                        "path": item.get("path"),
                        "kind": item.get("kind"),
                        "issueCount": len(item.get("issues") or []),
                        "headingCount": item.get("headingCount"),
                        "codeFenceCount": item.get("codeFenceCount"),
                        "relativeLinkCount": item.get("relativeLinkCount"),
                    }
                    for item in docs_observations[:10]
                ],
            })
        surface_observations = self._completion_changed_surface_records(root=root, task=task)
        if surface_observations:
            role_counts: dict[str, int] = {}
            for item in surface_observations:
                for role in item.get("roles") or []:
                    role_text = str(role or "").strip()
                    if role_text:
                        role_counts[role_text] = role_counts.get(role_text, 0) + 1
            advisories.append({
                "kind": "changed_surface_observation",
                "severity": "info",
                "source": "objective_surface_scan",
                "summary": (
                    f"{len(surface_observations)} changed artifact surface(s) were classified from paths and file structure; "
                    "ask the product-surface advisor what evidence matters for this task."
                ),
                "recommendedVerification": [],
                "surfaces": surface_observations[:20],
                "roleCounts": dict(sorted(role_counts.items())),
            })
        return advisories

    def _completion_changed_surface_records(
        self,
        *,
        root: Path,
        task: dict[str, Any],
    ) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in task.get("changedFiles") or []:
            if not isinstance(item, dict):
                continue
            path_text = self._completion_changed_file_path(item)
            if not path_text or path_text in seen:
                continue
            seen.add(path_text)
            record = self._completion_changed_surface_record(root=root, path_text=path_text)
            if record is not None:
                records.append(record)
        return records[:30]

    def _completion_changed_surface_record(self, *, root: Path, path_text: str) -> dict[str, Any] | None:
        roles = set(self._completion_path_surface_roles(path_text))
        families = sorted(self._completion_path_verification_families(path_text))
        path = self._completion_safe_workspace_path(root=root, relative_path=path_text)
        exists = path is not None and path.is_file()
        record: dict[str, Any] = {
            "path": path_text,
            "exists": exists,
            "roles": sorted(roles),
            "verificationFamilies": families,
        }
        suffix = Path(path_text).suffix.casefold()
        if suffix:
            record["extension"] = suffix
        content = ""
        if exists and path is not None:
            try:
                size = path.stat().st_size
                record["sizeBytes"] = size
                if size <= 500_000 and self._completion_path_is_text_surface_candidate(path_text):
                    content = path.read_text(encoding="utf-8", errors="replace")
                    record["lineCount"] = content.count("\n") + (1 if content else 0)
            except OSError:
                content = ""
        if content:
            content_signals = self._completion_content_surface_signals(path_text=path_text, content=content)
            if content_signals:
                record["contentSignals"] = content_signals[:10]
                roles.update(
                    str(signal.get("role") or "")
                    for signal in content_signals
                    if str(signal.get("role") or "").strip()
                )
            api_routes = self._completion_backend_routes_from_text(content)
            if api_routes:
                roles.add("backend_api_route")
                record["apiRoutes"] = api_routes[:10]
            if self._completion_path_may_contain_frontend_api_reference(path_text):
                api_references = self._completion_api_references_from_text(content)
                if api_references:
                    roles.add("frontend_api_client")
                    record["apiReferences"] = api_references[:10]
            package_scripts = self._completion_package_json_scripts(path_text=path_text, content=content)
            if package_scripts:
                roles.add("package_manifest")
                record["packageScripts"] = package_scripts[:20]
        record["roles"] = sorted(role for role in roles if role)
        if not record["roles"] and not families:
            return None
        return {
            key: value
            for key, value in record.items()
            if value not in ("", None, [], {})
        }

    def _completion_path_surface_roles(self, path_text: str) -> list[str]:
        normalized = path_text.casefold().replace("\\", "/")
        filename = normalized.rsplit("/", 1)[-1]
        roles: list[str] = []
        if self._completion_path_requires_docs_quality_check(path_text):
            roles.append("documentation")
        if self._completion_path_may_contain_frontend_api_reference(path_text) or normalized.endswith((".css", ".scss", ".sass")):
            roles.append("ui_or_client_artifact")
        if self._completion_path_may_contain_backend_route(path_text):
            roles.append("code_artifact")
        if self._completion_path_is_test_artifact(normalized):
            roles.append("test_artifact")
        if self._completion_path_is_persistence_artifact(normalized):
            roles.append("persistence_or_migration")
        if filename in {
            "package.json",
            "pyproject.toml",
            "requirements.txt",
            "setup.py",
            "cargo.toml",
            "go.mod",
            "pom.xml",
        } or filename.endswith((".csproj", ".sln")):
            roles.append("manifest_or_dependency")
        if normalized.endswith((".csv", ".tsv", ".json", ".jsonl", ".yaml", ".yml", ".toml", ".ini")):
            roles.append("data_or_config")
        return list(dict.fromkeys(roles))

    @staticmethod
    def _completion_path_is_test_artifact(normalized_path: str) -> bool:
        filename = normalized_path.rsplit("/", 1)[-1]
        return (
            normalized_path.startswith(("test/", "tests/"))
            or "/test/" in normalized_path
            or "/tests/" in normalized_path
            or filename.startswith("test_")
            or filename.endswith((".test.js", ".test.ts", ".spec.js", ".spec.ts", "_test.go"))
        )

    @staticmethod
    def _completion_path_is_persistence_artifact(normalized_path: str) -> bool:
        filename = normalized_path.rsplit("/", 1)[-1]
        return (
            "migration" in normalized_path
            or "schema" in filename
            or "database" in normalized_path
            or "/db/" in normalized_path
            or normalized_path.endswith((".sql", ".sqlite", ".sqlite3"))
        )

    @staticmethod
    def _completion_path_is_text_surface_candidate(path_text: str) -> bool:
        normalized = path_text.casefold().replace("\\", "/")
        return normalized.endswith((
            ".adoc",
            ".asciidoc",
            ".c",
            ".cc",
            ".cpp",
            ".css",
            ".go",
            ".h",
            ".hpp",
            ".html",
            ".ini",
            ".java",
            ".js",
            ".json",
            ".jsonl",
            ".jsx",
            ".kt",
            ".kts",
            ".md",
            ".mdx",
            ".mjs",
            ".php",
            ".py",
            ".pyw",
            ".rb",
            ".rs",
            ".rst",
            ".scss",
            ".sql",
            ".svelte",
            ".swift",
            ".toml",
            ".ts",
            ".tsx",
            ".txt",
            ".vue",
            ".yaml",
            ".yml",
        ))

    def _completion_content_surface_signals(self, *, path_text: str, content: str) -> list[dict[str, Any]]:
        normalized = path_text.casefold().replace("\\", "/")
        lowered = content.casefold()
        signals: list[dict[str, Any]] = []
        if normalized.endswith((".py", ".pyw")) and (
            "argparse" in lowered
            or "typer." in lowered
            or "click." in lowered
            or re.search(r"if\s+__name__\s*==\s*['\"]__main__['\"]", content)
        ):
            signals.append({"kind": "python_entrypoint", "role": "cli_or_entrypoint"})
        if normalized.endswith((".js", ".ts", ".mjs", ".cjs")) and re.search(r"\bprocess\.argv\b|\bcommander\b|\byargs\b", content):
            signals.append({"kind": "javascript_entrypoint", "role": "cli_or_entrypoint"})
        if normalized.endswith(".sql") or re.search(r"\b(create|alter|drop)\s+table\b", lowered):
            signals.append({"kind": "schema_statement", "role": "persistence_or_migration"})
        if re.search(r"\b(localstorage|indexeddb|sqlite|postgres|mysql|redis)\b", lowered):
            signals.append({"kind": "persistence_reference", "role": "persistence_or_migration"})
        return signals

    @staticmethod
    def _completion_package_json_scripts(*, path_text: str, content: str) -> list[str]:
        if path_text.casefold().replace("\\", "/").rsplit("/", 1)[-1] != "package.json":
            return []
        try:
            payload = json.loads(content)
        except json.JSONDecodeError:
            return []
        scripts = payload.get("scripts") if isinstance(payload, dict) else None
        if not isinstance(scripts, dict):
            return []
        return [
            str(name)
            for name in scripts.keys()
            if isinstance(name, str) and name.strip()
        ]

    def _completion_api_contract_observations(
        self,
        *,
        references: list[dict[str, str]],
        backend_routes: list[dict[str, str]],
    ) -> list[dict[str, Any]]:
        observations: list[dict[str, Any]] = []
        for reference in references:
            match = self._completion_match_backend_api_route(reference, backend_routes)
            observations.append({
                "method": str(reference.get("method") or "GET").upper(),
                "path": str(reference.get("path") or ""),
                "sourcePath": str(reference.get("sourcePath") or ""),
                "backendRoute": match,
                "localRouteMatched": match is not None,
            })
        return observations

    def _completion_product_advisory_signals(
        self,
        *,
        verification: list[dict[str, Any]],
        tests_run: list[dict[str, Any]],
        commands: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        signals: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for source_name, items in (
            ("verification", verification),
            ("testsRun", tests_run),
            ("commands", commands),
        ):
            for item in items:
                if not isinstance(item, dict) or not self._completion_item_passed(item):
                    continue
                command = str(item.get("command") or item.get("name") or item.get("summary") or "").strip()
                key = (source_name, command.casefold())
                if key in seen:
                    continue
                seen.add(key)
                signals.append({
                    "source": source_name,
                    "command": command[:300] if command else None,
                    "summary": str(item.get("summary") or "").strip()[:300] or None,
                })
        return [
            {key: value for key, value in signal.items() if value not in ("", None)}
            for signal in signals
        ]

    @staticmethod
    def _completion_item_passed(item: dict[str, Any]) -> bool:
        status = str(item.get("status") or "").strip().casefold()
        exit_code = item.get("exitCode")
        return status in {"passed", "success", "completed", "ok"} and exit_code in (0, "0", None)

    def _completion_docs_quality_records(
        self,
        *,
        root: Path,
        task: dict[str, Any],
    ) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for path_text in self._completion_docs_artifact_paths(task):
            path = self._completion_safe_workspace_path(root=root, relative_path=path_text)
            if path is None or not path.is_file():
                continue
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            records.append(self._completion_docs_quality_record(root=root, path_text=path_text, content=content))
        return records

    def _completion_docs_quality_record(
        self,
        *,
        root: Path,
        path_text: str,
        content: str,
    ) -> dict[str, Any]:
        word_count = len(re.findall(r"\b\w+\b", content))
        heading_count = len(re.findall(r"(?m)^\s{0,3}#{1,6}\s+\S", content))
        code_fence_count = len(re.findall(r"(?m)^\s*(```|~~~)", content))
        relative_links = self._completion_docs_relative_link_targets(content)
        issues = self._completion_docs_quality_issues(
            root=root,
            path_text=path_text,
            content=content,
            word_count=word_count,
            heading_count=heading_count,
            code_fence_count=code_fence_count,
        )
        return {
            "path": path_text,
            "kind": self._completion_docs_artifact_kind(path_text),
            "lineCount": content.count("\n") + (1 if content else 0),
            "wordCount": word_count,
            "headingCount": heading_count,
            "codeFenceCount": code_fence_count,
            "relativeLinkCount": len(relative_links),
            "issues": issues[:10],
        }

    def _completion_docs_quality_issues(
        self,
        *,
        root: Path,
        path_text: str,
        content: str,
        word_count: int,
        heading_count: int,
        code_fence_count: int,
    ) -> list[str]:
        issues: list[str] = []
        for pattern, label in (
            (r"\bTODO\b", "visible TODO placeholder"),
            (r"\bTBD\b", "visible TBD placeholder"),
            (r"\bFIXME\b", "visible FIXME placeholder"),
            (r"\blorem ipsum\b", "placeholder lorem ipsum copy"),
            (r"\bplaceholder\b", "placeholder copy"),
        ):
            if re.search(pattern, content, flags=re.IGNORECASE):
                issues.append(label)
        if code_fence_count % 2 == 1:
            issues.append("unbalanced fenced code blocks")
        if heading_count == 0 and (self._completion_docs_artifact_kind(path_text) == "readme" or word_count >= 40):
            issues.append("missing heading structure")
        issues.extend(self._completion_docs_relative_link_issues(root=root, path_text=path_text, content=content))
        return list(dict.fromkeys(issues))

    @staticmethod
    def _completion_docs_relative_link_targets(content: str) -> list[str]:
        targets: list[str] = []
        seen: set[str] = set()
        for match in re.finditer(r"!?\[[^\]]*\]\((?P<target>[^)]+)\)", content):
            target = (match.group("target") or "").strip().strip("'\"")
            if not target or target.startswith(("#", "http://", "https://", "//", "mailto:", "tel:", "data:", "javascript:")):
                continue
            target = target.split("#", 1)[0].split("?", 1)[0].strip()
            if not target or target in seen:
                continue
            seen.add(target)
            targets.append(target)
        return targets

    def _completion_docs_relative_link_issues(
        self,
        *,
        root: Path,
        path_text: str,
        content: str,
    ) -> list[str]:
        doc_path = self._completion_safe_workspace_path(root=root, relative_path=path_text)
        if doc_path is None:
            return []
        issues: list[str] = []
        for target in self._completion_docs_relative_link_targets(content):
            candidate = (doc_path.parent / target).resolve()
            try:
                candidate.relative_to(root.resolve())
            except ValueError:
                issues.append(f"relative link escapes workspace: {target}")
                continue
            fallback_candidates = [candidate]
            if not candidate.suffix:
                fallback_candidates.extend([
                    candidate.with_suffix(".md"),
                    candidate.with_suffix(".mdx"),
                    candidate / "README.md",
                    candidate / "index.md",
                ])
            if not any(option.exists() for option in fallback_candidates):
                issues.append(f"missing relative link target: {target}")
        return issues

    def _completion_docs_artifact_paths(self, task: dict[str, Any]) -> list[str]:
        paths: list[str] = []
        for item in task.get("changedFiles") or []:
            if not isinstance(item, dict):
                continue
            path = self._completion_changed_file_path(item)
            if path and self._completion_path_requires_docs_quality_check(path):
                paths.append(path)
        return list(dict.fromkeys(paths))

    @staticmethod
    def _completion_path_requires_docs_quality_check(path: str) -> bool:
        normalized = path.casefold().replace("\\", "/")
        if normalized.endswith((".md", ".mdx", ".rst", ".adoc", ".asciidoc", ".txt")):
            return True
        basename = normalized.rsplit("/", 1)[-1]
        return basename.startswith("readme")

    @staticmethod
    def _completion_docs_artifact_kind(path_text: str) -> str:
        basename = path_text.casefold().replace("\\", "/").rsplit("/", 1)[-1]
        return "readme" if basename.startswith("readme") else "docs"

    def _completion_frontend_api_references(
        self,
        *,
        root: Path,
        task: dict[str, Any],
    ) -> list[dict[str, str]]:
        references: list[dict[str, str]] = []
        seen: set[tuple[str, str, str]] = set()
        for path_text in self._completion_frontend_api_artifact_paths(task):
            path = self._completion_safe_workspace_path(root=root, relative_path=path_text)
            if path is None or not path.is_file():
                continue
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for item in self._completion_api_references_from_text(content):
                key = (path_text, item["method"], item["path"])
                if key in seen:
                    continue
                seen.add(key)
                references.append({"sourcePath": path_text, **item})
        return references

    def _completion_frontend_api_artifact_paths(self, task: dict[str, Any]) -> list[str]:
        paths: list[str] = []
        for item in task.get("changedFiles") or []:
            if not isinstance(item, dict):
                continue
            path = self._completion_changed_file_path(item)
            if path and self._completion_path_may_contain_frontend_api_reference(path):
                paths.append(path)
        return list(dict.fromkeys(paths))

    @staticmethod
    def _completion_path_may_contain_frontend_api_reference(path: str) -> bool:
        normalized = path.casefold().replace("\\", "/")
        return normalized.endswith((
            ".html",
            ".js",
            ".jsx",
            ".ts",
            ".tsx",
            ".mjs",
            ".cjs",
            ".vue",
            ".svelte",
        ))

    def _completion_api_references_from_text(self, content: str) -> list[dict[str, str]]:
        references: list[dict[str, str]] = []
        for match in re.finditer(
            r"fetch\s*\(\s*['\"](?P<url>/?api/[^'\"]+)['\"](?P<args>[^)]*)\)",
            content,
            flags=re.IGNORECASE | re.DOTALL,
        ):
            method_match = re.search(
                r"method\s*:\s*['\"](?P<method>[A-Za-z]+)['\"]",
                match.group("args") or "",
                flags=re.IGNORECASE,
            )
            references.append({
                "method": (method_match.group("method") if method_match else "GET").upper(),
                "path": self._completion_normalize_api_path(match.group("url")),
            })
        for match in re.finditer(
            r"axios(?:\s*\.\s*(?P<method>get|post|put|patch|delete|head|options))?"
            r"\s*\(\s*['\"](?P<url>/?api/[^'\"]+)['\"]",
            content,
            flags=re.IGNORECASE,
        ):
            method = match.group("method") or "GET"
            references.append({
                "method": method.upper(),
                "path": self._completion_normalize_api_path(match.group("url")),
            })
        return [
            item for item in references
            if item.get("path") and str(item.get("path")).startswith("/api/")
        ]

    def _completion_backend_api_routes(
        self,
        *,
        root: Path,
        task: dict[str, Any],
    ) -> list[dict[str, str]]:
        routes: list[dict[str, str]] = []
        seen: set[tuple[str, str, str]] = set()
        for path in self._completion_backend_route_candidate_files(root=root, task=task):
            try:
                if path.stat().st_size > 2_000_000:
                    continue
                content = path.read_text(encoding="utf-8", errors="replace")
                display = path.relative_to(root.resolve()).as_posix()
            except (OSError, ValueError):
                continue
            for item in self._completion_backend_routes_from_text(content):
                key = (item["method"], item["path"], display)
                if key in seen:
                    continue
                seen.add(key)
                routes.append({"sourcePath": display, **item})
        return routes

    def _completion_backend_route_candidate_files(
        self,
        *,
        root: Path,
        task: dict[str, Any],
    ) -> list[Path]:
        files: list[Path] = []
        seen: set[Path] = set()
        for item in task.get("changedFiles") or []:
            if not isinstance(item, dict):
                continue
            path_text = self._completion_changed_file_path(item)
            if not self._completion_path_may_contain_backend_route(path_text):
                continue
            path = self._completion_safe_workspace_path(root=root, relative_path=path_text)
            if path is not None and path.is_file():
                seen.add(path)
                files.append(path)
        for path in root.rglob("*"):
            if len(files) >= 500:
                break
            if path in seen or not path.is_file():
                continue
            if self._completion_path_is_in_ignored_tree(path):
                continue
            try:
                display = path.relative_to(root.resolve()).as_posix()
            except ValueError:
                continue
            if not self._completion_path_may_contain_backend_route(display):
                continue
            seen.add(path)
            files.append(path)
        return files

    @staticmethod
    def _completion_path_is_in_ignored_tree(path: Path) -> bool:
        ignored = {
            ".git",
            ".hg",
            ".svn",
            "__pycache__",
            ".pytest_cache",
            ".mypy_cache",
            ".venv",
            "venv",
            "node_modules",
            "dist",
            "build",
            ".next",
            ".nuxt",
        }
        return any(part in ignored for part in path.parts)

    @staticmethod
    def _completion_path_may_contain_backend_route(path: str) -> bool:
        normalized = path.casefold().replace("\\", "/")
        return normalized.endswith((".py", ".js", ".ts", ".mjs", ".cjs"))

    def _completion_backend_routes_from_text(self, content: str) -> list[dict[str, str]]:
        routes: list[dict[str, str]] = []
        for match in re.finditer(
            r"@[\w.]+\.(?P<method>get|post|put|patch|delete|head|options)"
            r"\s*\(\s*['\"](?P<path>/api/[^'\"]*)['\"]",
            content,
            flags=re.IGNORECASE,
        ):
            routes.append({
                "method": match.group("method").upper(),
                "path": self._completion_normalize_api_path(match.group("path")),
            })
        for match in re.finditer(
            r"@[\w.]+\.route\s*\(\s*['\"](?P<path>/api/[^'\"]*)['\"](?P<args>[^)]*)\)",
            content,
            flags=re.IGNORECASE | re.DOTALL,
        ):
            methods = re.findall(r"['\"]([A-Za-z]+)['\"]", match.group("args") or "")
            if methods:
                for method in methods:
                    routes.append({
                        "method": method.upper(),
                        "path": self._completion_normalize_api_path(match.group("path")),
                    })
            else:
                routes.append({
                    "method": "*",
                    "path": self._completion_normalize_api_path(match.group("path")),
                })
        for match in re.finditer(
            r"\b(?:app|router|server)\s*\.\s*(?P<method>get|post|put|patch|delete|head|options|all|use)"
            r"\s*\(\s*['\"](?P<path>/api/[^'\"]*)['\"]",
            content,
            flags=re.IGNORECASE,
        ):
            method = match.group("method").upper()
            routes.append({
                "method": "*" if method in {"ALL", "USE"} else method,
                "path": self._completion_normalize_api_path(match.group("path")),
            })
        return [
            route for route in routes
            if route.get("path") and str(route.get("path")).startswith("/api/")
        ]

    def _completion_match_backend_api_route(
        self,
        reference: dict[str, str],
        routes: list[dict[str, str]],
    ) -> dict[str, str] | None:
        method = str(reference.get("method") or "GET").upper()
        path = str(reference.get("path") or "")
        for route in routes:
            route_method = str(route.get("method") or "").upper()
            if route_method not in {"*", method}:
                continue
            if self._completion_api_route_path_matches(str(route.get("path") or ""), path):
                return route
        return None

    def _completion_api_route_path_matches(self, route_path: str, reference_path: str) -> bool:
        route = self._completion_normalize_api_path(route_path)
        reference = self._completion_normalize_api_path(reference_path)
        if route == reference:
            return True
        pattern = self._completion_api_route_pattern(route)
        return bool(re.fullmatch(pattern, reference))

    @staticmethod
    def _completion_api_route_pattern(route_path: str) -> str:
        segments = route_path.strip("/").split("/")
        pattern_segments: list[str] = []
        for segment in segments:
            if not segment:
                continue
            if (
                segment.startswith(":")
                or (segment.startswith("{") and segment.endswith("}"))
                or (segment.startswith("<") and segment.endswith(">"))
            ):
                pattern_segments.append(r"[^/]+")
            elif segment == "*":
                pattern_segments.append(r".*")
            else:
                pattern_segments.append(re.escape(segment))
        return "/" + "/".join(pattern_segments)

    @staticmethod
    def _completion_normalize_api_path(value: str) -> str:
        path = str(value or "").split("#", 1)[0].split("?", 1)[0].strip()
        if not path:
            return ""
        if not path.startswith("/"):
            path = "/" + path
        if len(path) > 1:
            path = path.rstrip("/")
        return path

    def _completion_static_frontend_asset_records(
        self,
        *,
        root: Path,
        task: dict[str, Any],
    ) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for html_path_text in self._completion_static_html_artifact_paths(task):
            html_path = self._completion_safe_workspace_path(root=root, relative_path=html_path_text)
            if html_path is None or not html_path.is_file():
                continue
            try:
                content = html_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            records.append(self._completion_html_route_health_record(html_path_text=html_path_text, content=content))
            for kind, reference in self._completion_html_asset_references(content):
                asset_path = self._completion_resolve_html_asset(
                    root=root,
                    html_path=html_path,
                    reference=reference,
                    kind=kind,
                )
                if asset_path is None:
                    continue
                try:
                    display = asset_path.relative_to(root.resolve()).as_posix()
                except ValueError:
                    continue
                exists = asset_path.is_file()
                records.append({
                    "criterion": f"Static frontend asset reachable: {html_path_text} -> {reference}",
                    "status": "supported" if exists else "failed",
                    "evidenceLevel": "product_quality",
                    "source": "static_asset_reachability",
                    "path": display,
                    "assetType": kind,
                })
                if exists and kind == "script" and self._completion_path_requires_node_check(display):
                    records.append(
                        self._completion_node_check_record(
                            root=root,
                            script_path=asset_path,
                            task=task,
                        )
                    )
        return records

    def _completion_static_html_artifact_paths(self, task: dict[str, Any]) -> list[str]:
        paths: list[str] = []
        for item in task.get("changedFiles") or []:
            if not isinstance(item, dict):
                continue
            path = self._completion_changed_file_path(item)
            if path.casefold().replace("\\", "/").endswith(".html"):
                paths.append(path)
        return list(dict.fromkeys(paths))

    def _completion_html_asset_references(self, content: str) -> list[tuple[str, str]]:
        parser = _StaticAssetReferenceParser()
        try:
            parser.feed(content)
        except Exception:  # noqa: BLE001
            return []
        return [
            (kind, reference)
            for kind, reference in parser.references
            if self._completion_is_local_static_reference(reference)
        ]

    def _completion_html_route_health_record(self, *, html_path_text: str, content: str) -> dict[str, Any]:
        parser = _StaticAssetReferenceParser()
        issues: list[str] = []
        try:
            parser.feed(content)
        except Exception as exc:  # noqa: BLE001
            issues.append(f"html parse warning: {exc}")
        visible_text = " ".join(parser.visible_text).strip()
        if len(visible_text) < 3:
            issues.append("no visible text found")
        lowered = visible_text.casefold()
        if "lorem ipsum" in lowered:
            issues.append("placeholder lorem ipsum copy")
        if "[todo]" in lowered or "todo:" in lowered:
            issues.append("visible TODO placeholder")
        return {
            "criterion": f"Static frontend route opens: {html_path_text}",
            "status": "failed" if issues else "supported",
            "evidenceLevel": "product_quality",
            "source": "static_frontend_route_health",
            "issues": issues[:5],
        }

    @staticmethod
    def _completion_is_local_static_reference(reference: str) -> bool:
        value = reference.strip()
        if not value or value.startswith(("#", "/", "\\", "data:", "mailto:", "tel:")):
            return False
        lowered = value.casefold()
        if lowered.startswith(("http://", "https://", "//", "javascript:")):
            return False
        return True

    def _completion_resolve_html_asset(
        self,
        *,
        root: Path,
        html_path: Path,
        reference: str,
        kind: str,
    ) -> Path | None:
        clean_reference = reference.split("#", 1)[0].split("?", 1)[0].replace("\\", "/").strip()
        if not clean_reference:
            return None
        candidate = (html_path.parent / clean_reference).resolve()
        try:
            candidate.relative_to(root.resolve())
        except ValueError:
            return None
        if kind == "route" and candidate.is_dir():
            return candidate / "index.html"
        if kind == "route" and not candidate.suffix:
            html_candidate = candidate.with_suffix(".html")
            if html_candidate.exists():
                return html_candidate
            index_candidate = candidate / "index.html"
            if index_candidate.exists():
                return index_candidate
        return candidate

    def _completion_safe_workspace_path(self, *, root: Path, relative_path: str) -> Path | None:
        candidate = (root / relative_path).resolve()
        try:
            candidate.relative_to(root.resolve())
        except ValueError:
            return None
        return candidate

    @staticmethod
    def _completion_path_requires_node_check(path: str) -> bool:
        return path.casefold().replace("\\", "/").endswith((".js", ".mjs", ".cjs"))

    def _completion_node_check_record(
        self,
        *,
        root: Path,
        script_path: Path,
        task: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            display = script_path.relative_to(root.resolve()).as_posix()
        except ValueError:
            display = script_path.name
        existing = self._completion_existing_node_check_record(task=task, display=display)
        if existing is not None:
            if self._completion_node_check_record_needs_retry(existing):
                retried = self._completion_run_node_check(root=root, script_path=script_path, display=display)
                if retried is not None and retried.get("status") == "supported":
                    return retried
            return existing
        retried = self._completion_run_node_check(root=root, script_path=script_path, display=display)
        if retried is not None:
            return retried
        return {
            "criterion": f"Static frontend script syntax: {display}",
            "status": "unverified",
            "evidenceLevel": "product_quality",
            "source": "static_frontend_node_check",
            "command": f"node --check {display}",
            "summary": "No existing node --check verification was recorded, and no Node runtime was available for a recovery attempt.",
        }

    def _completion_existing_node_check_record(self, *, task: dict[str, Any], display: str) -> dict[str, Any] | None:
        normalized_display = display.casefold()
        command_candidates = [
            dict(item) for item in (task.get("commands") or [])
            if isinstance(item, dict)
        ]
        for item in self._completion_commands_from_store(task.get("id")):
            if isinstance(item, dict):
                command_candidates.append(dict(item))
        for item in command_candidates:
            command_text = str(item.get("command") or "").strip()
            command_key = self._completion_node_check_command_key(command_text)
            if command_key is None:
                continue
            if normalized_display and command_key != normalized_display:
                continue
            status = str(item.get("status") or "").strip().casefold()
            exit_code = item.get("exitCode")
            supported = status in {"completed", "passed", "success"} and exit_code in (0, "0", None)
            failed = status in {"failed", "timeout", "killed", "validation_failed"} or (
                isinstance(exit_code, int) and exit_code != 0
            )
            summary = str(item.get("summary") or "").strip()
            return {
                "criterion": f"Static frontend script syntax: {display}",
                "status": "supported" if supported else "failed" if failed else "unverified",
                "evidenceLevel": "product_quality",
                "source": "static_frontend_node_check",
                "command": f"node --check {display}",
                "exitCode": exit_code,
                "summary": summary or ("node --check passed" if supported else "node --check did not pass"),
            }
        return None

    @staticmethod
    def _completion_node_check_command_key(command: str) -> str | None:
        text = str(command or "").strip()
        if not text:
            return None
        match = re.search(
            r"""(?i)(?:^|[;&]\s*|&&\s*|\|\|\s*|^\s*&\s*['"]?[^'"]*node(?:\.exe)?['"]?\s*)node(?:\.exe)?\s+--check\s+(?P<target>.+?)\s*$""",
            text,
        )
        if not match:
            match = re.search(
                r"""(?i)(?:(?:['"]?[^'"]*node(?:\.exe)?['"]?)|node(?:\.exe)?)\s+--check\s+(?P<target>.+?)\s*$""",
                text,
            )
        if not match:
            return None
        target = str(match.group("target") or "").strip().strip("\"'")
        if not target:
            return None
        return target.replace("\\", "/").casefold()

    @staticmethod
    def _completion_node_check_record_needs_retry(record: dict[str, Any]) -> bool:
        if str(record.get("status") or "").strip().casefold() != "failed":
            return False
        summary = str(record.get("summary") or "").casefold()
        command = str(record.get("command") or "").casefold()
        if "node --check" not in command:
            return False
        retry_markers = (
            "无法将“node”项识别为",
            "无法将'node'项识别为",
            "not recognized",
            "command not found",
            "no such file",
            "file not found",
        )
        return any(marker in summary for marker in retry_markers)

    def _completion_run_node_check(
        self,
        *,
        root: Path,
        script_path: Path,
        display: str,
    ) -> dict[str, Any] | None:
        node_executable = resolve_node_executable()
        if not node_executable:
            return None
        try:
            proc = subprocess.run(
                [node_executable, "--check", str(script_path)],
                cwd=root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
            )
            output = (proc.stderr or proc.stdout or "").strip()
            return {
                "criterion": f"Static frontend script syntax: {display}",
                "status": "supported" if proc.returncode == 0 else "failed",
                "evidenceLevel": "product_quality",
                "source": "static_frontend_node_check",
                "command": f"node --check {display}",
                "exitCode": proc.returncode,
                "summary": output.splitlines()[0][:200] if output else "node --check passed",
            }
        except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as exc:
            return {
                "criterion": f"Static frontend script syntax: {display}",
                "status": "unverified",
                "evidenceLevel": "product_quality",
                "source": "static_frontend_node_check",
                "command": f"node --check {display}",
                "summary": str(exc),
            }

    def _completion_product_readability_records(
        self,
        *,
        root: Path,
        task: dict[str, Any],
    ) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for path_text in self._completion_readable_artifact_paths(task):
            path = (root / path_text).resolve()
            try:
                path.relative_to(root.resolve())
            except ValueError:
                continue
            if not path.exists() or not path.is_file():
                continue
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            issues = self._completion_readability_issues(content)
            records.append({
                "criterion": f"Readable artifact copy: {path_text}",
                "status": "failed" if issues else "supported",
                "evidenceLevel": "product_quality",
                "source": "product_readability_check",
                "issues": issues[:5],
            })
        return records

    def _completion_readable_artifact_paths(self, task: dict[str, Any]) -> list[str]:
        paths: list[str] = []
        for item in task.get("changedFiles") or []:
            if not isinstance(item, dict):
                continue
            path = self._completion_changed_file_path(item)
            if path and self._completion_path_requires_readability_check(path):
                paths.append(path)
        return list(dict.fromkeys(paths))

    @staticmethod
    def _completion_path_requires_readability_check(path: str) -> bool:
        normalized = path.casefold().replace("\\", "/")
        return normalized.endswith((
            ".html",
            ".md",
            ".txt",
            ".css",
            ".js",
            ".jsx",
            ".ts",
            ".tsx",
            ".vue",
            ".svelte",
        ))

    @staticmethod
    def _completion_readability_issues(content: str) -> list[str]:
        checks = [
            ("replacement character", "\ufffd"),
            ("common mojibake token", "鈥"),
            ("common mojibake token", "鏂"),
            ("common mojibake token", "涓"),
            ("common mojibake token", "浜"),
            ("common mojibake token", "鍙"),
            ("common mojibake token", "绋"),
        ]
        issues: list[str] = []
        for label, token in checks:
            if token in content:
                issues.append(f"{label}: {token}")
        return issues

    def _completion_expected_artifact_paths(self, text: str) -> list[str]:
        path_pattern = re.compile(
            r"(?<![\w./\\-])((?:[\w.-]+[/\\])*[\w.-]+\.(?:py|js|css|html|md|json|toml|yaml|yml|csv|ts|tsx|jsx))(?![\w.-])",
            re.IGNORECASE,
        )
        negative_window_tokens = (
            "delete",
            "remove",
            "removed",
            "not required",
            "out-of-scope",
            "out of scope",
            "do not keep",
            "should not exist",
            "if not required",
            "suggested filename",
            "suggested filenames",
            "filenames include",
            "for example",
            "close equivalent",
            "close equivalents",
            "equivalent acceptable",
            "equivalents are acceptable",
            "不需要",
            "删除",
            "移除",
            "多余",
            "越界",
            "非前端",
            "不是交付物",
        )
        paths: list[str] = []
        seen: set[str] = set()
        for match in path_pattern.finditer(text or ""):
            raw = match.group(1).strip("`'\".,;:()[]{}")
            normalized = raw.replace("\\", "/").lstrip("./")
            if not normalized or normalized.startswith(("%", "$")):
                continue
            window_start = max(0, match.start() - 120)
            window_end = min(len(text or ""), match.end() + 120)
            context_window = (text or "")[window_start:window_end].casefold()
            if any(token in context_window for token in negative_window_tokens):
                continue
            if normalized.casefold() in seen:
                continue
            seen.add(normalized.casefold())
            paths.append(normalized)
        return paths

    @staticmethod
    def _completion_expected_artifact_basenames(text: str) -> set[str]:
        return {
            path.rsplit("/", 1)[-1].casefold()
            for path in TaskLifecycleMixin._completion_expected_artifact_paths(TaskLifecycleMixin, text)  # type: ignore[misc]
        }

    def _completion_expected_pytest_file_count(self, text: str) -> int | None:
        lowered = (text or "").casefold()
        patterns = [
            r"(?:at least|minimum of|>=)\s*(\d+)\s+(?:pytest\s+)?(?:test\s+)?files?",
            r"(?:至少|不少于)\s*(\d+|一|二|两|三|四|五)\s*(?:个|份)?\s*(?:pytest|测试).{0,8}(?:文件)?",
        ]
        chinese_digits = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5}
        for pattern in patterns:
            match = re.search(pattern, lowered)
            if not match:
                continue
            value = match.group(1)
            if value.isdigit():
                return int(value)
            if value in chinese_digits:
                return chinese_digits[value]
        return None

    @staticmethod
    def _completion_python_test_file_count(root: Path) -> int:
        count = 0
        for path in root.rglob("*.py"):
            if not path.is_file() or ".git" in path.parts:
                continue
            normalized = path.as_posix().casefold()
            filename = path.name.casefold()
            if (
                normalized.startswith(("tests/", "test/"))
                or "/tests/" in normalized
                or "/test/" in normalized
                or filename.startswith("test_")
                or filename.endswith("_test.py")
            ):
                count += 1
        return count

    def _completion_contract_profile(
        self,
        *,
        task: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any] | None:
        profile = context.get("_child_profile")
        if isinstance(profile, dict):
            return profile
        routing = task.get("routing")
        if not isinstance(routing, dict):
            return None
        profile = routing.get("profile")
        return profile if isinstance(profile, dict) else None

    def _completion_contract_role_is_read_only(
        self,
        *,
        task: dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
        runtime_role: str | None = None,
    ) -> bool:
        role = str(runtime_role or "").strip().lower()
        if not role and isinstance(task, dict):
            routing = task.get("routing")
            if isinstance(routing, dict):
                role = str(routing.get("runtimeRole") or "").strip().lower()
            if not role and isinstance(context, dict):
                context_routing = context.get("routing")
                if isinstance(context_routing, dict):
                    role = str(context_routing.get("runtimeRole") or "").strip().lower()
            if not role:
                role = str(task.get("role") or "").strip().lower()
        return role in {"planner", "reviewer", "explorer", "summarizer"}

    def _completion_contract_expected_artifact_paths(
        self,
        contract: dict[str, Any] | None,
        *,
        include_owned_scope_fallback: bool = True,
    ) -> list[str]:
        if not isinstance(contract, dict):
            return []
        paths: list[str] = []
        seen: set[str] = set()
        expected_artifacts = contract.get("expectedArtifacts")
        if isinstance(expected_artifacts, list):
            for item in expected_artifacts:
                if not isinstance(item, dict):
                    continue
                path_text = self._completion_contract_normalize_path(
                    item.get("path") or item.get("file") or item.get("name")
                )
                if not path_text:
                    continue
                if any(ch in path_text for ch in "*?[]"):
                    continue
                key = path_text.casefold()
                if key in seen:
                    continue
                seen.add(key)
                paths.append(path_text)
        if paths:
            return paths
        if not include_owned_scope_fallback:
            return []
        owned_scope = contract.get("ownedScope")
        if isinstance(owned_scope, str):
            owned_scope = [owned_scope]
        if not isinstance(owned_scope, list):
            return []
        for item in owned_scope:
            path_text = self._completion_contract_normalize_path(item)
            if not path_text or path_text.endswith("/"):
                continue
            if any(ch in path_text for ch in "*?[]"):
                continue
            if "." not in Path(path_text).name:
                continue
            key = path_text.casefold()
            if key in seen:
                continue
            seen.add(key)
            paths.append(path_text)
        return paths

    def _completion_contract_verification_families(self, contract: dict[str, Any]) -> set[str]:
        families: set[str] = set()
        requirements = contract.get("verificationRequirements")
        if not isinstance(requirements, list):
            return families
        for item in requirements:
            if not isinstance(item, dict):
                continue
            family = str(item.get("family") or item.get("framework") or "").strip().casefold()
            if family:
                families.add(family)
            path_text = self._completion_contract_normalize_path(
                item.get("path") or item.get("file") or item.get("target")
            )
            if path_text:
                families.update(self._completion_path_verification_families(path_text))
            command = str(item.get("command") or item.get("name") or "").strip()
            if command:
                families.update(self._completion_verification_item_families({"command": command, "status": "passed"}))
        return {item for item in families if item}

    @staticmethod
    def _completion_contract_normalize_path(value: Any) -> str:
        return str(value or "").strip().replace("\\", "/").lstrip("./").strip()

    def _completion_explicit_acceptance_records(
        self,
        *,
        task: dict[str, Any],
        validation: dict[str, Any],
        tool_results: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for source_name, source in (
            ("explicit_task", task),
            ("explicit_validation", validation),
        ):
            records.extend(self._completion_acceptance_records_from_source(source, source_name))
        for tool_result in tool_results:
            if not isinstance(tool_result, dict):
                continue
            tool_name = str(tool_result.get("name") or "tool")
            result = tool_result.get("result") if isinstance(tool_result.get("result"), dict) else {}
            records.extend(self._completion_acceptance_records_from_source(result, f"explicit_tool:{tool_name}"))
        return records

    def _completion_acceptance_records_from_source(self, source: Any, source_name: str) -> list[dict[str, Any]]:
        if not isinstance(source, dict):
            return []
        raw_items: Any = None
        for key in ("acceptance", "acceptanceResults", "acceptanceCriteriaResults", "criteriaResults"):
            if key in source:
                raw_items = source.get(key)
                break
        if raw_items is None:
            return []
        if isinstance(raw_items, dict):
            raw_iterable = [
                {**value, "criterion": value.get("criterion") or raw_key}
                if isinstance(value, dict)
                else {"criterion": raw_key, "status": value}
                for raw_key, value in raw_items.items()
            ]
        elif isinstance(raw_items, list):
            raw_iterable = raw_items
        else:
            return []

        records: list[dict[str, Any]] = []
        for item in raw_iterable:
            if not isinstance(item, dict):
                continue
            status = self._normalize_acceptance_status(item.get("status") or item.get("result") or item.get("state"))
            if status is None:
                continue
            criterion = (
                item.get("criterion")
                or item.get("criteria")
                or item.get("text")
                or item.get("name")
            )
            index_value = item.get("index") if "index" in item else item.get("criterionIndex")
            index = index_value if isinstance(index_value, int) else None
            records.append(
                {
                    "criterion": str(criterion).strip() if criterion is not None else "",
                    "index": index,
                    "status": status,
                    "source": source_name,
                }
            )
        return records

    def _completion_acceptance_match_index(self, record: dict[str, Any], criteria: list[str]) -> int | None:
        index = record.get("index")
        if isinstance(index, int) and 0 <= index < len(criteria):
            return index
        criterion = str(record.get("criterion") or "").strip()
        if not criterion:
            return None
        normalized = self._normalize_acceptance_text(criterion)
        for idx, candidate in enumerate(criteria):
            if self._normalize_acceptance_text(candidate) == normalized:
                return idx
        return None

    def _normalize_acceptance_status(self, value: Any) -> str | None:
        normalized = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
        if not normalized:
            return None
        if normalized in {"supported", "passed", "pass", "complete", "completed", "verified", "satisfied", "success", "ok"}:
            return "supported"
        if normalized in {"failed", "fail", "unsupported", "unmet", "not_met", "missing", "blocked"}:
            return "failed"
        if normalized in {"unverified", "unknown", "pending", "not_run", "skipped", "incomplete", "partial"}:
            return "unverified"
        return None

    def _normalize_acceptance_text(self, value: str) -> str:
        return " ".join(value.split()).casefold()

    def _completion_tool_evidence(self, tool_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
        evidence: list[dict[str, Any]] = []
        for tool_result in tool_results:
            if not isinstance(tool_result, dict):
                continue
            name = tool_result.get("name")
            if not isinstance(name, str) or not name:
                continue
            result = tool_result.get("result") if isinstance(tool_result.get("result"), dict) else None
            if result is None:
                normalized = {
                    key: value
                    for key, value in tool_result.items()
                    if key not in {"name", "source"}
                }
                result = normalized if normalized else {}
            status = result.get("status") or ("completed" if result else "unknown")
            item = {
                "name": name,
                "status": status,
                "ok": result.get("ok"),
                "summary": result.get("summary") or result.get("error"),
                "failed": self._completion_tool_result_failed(result),
            }
            if name == "run_command":
                item["exitCode"] = result.get("exitCode")
                item["command"] = result.get("command")
            elif name in {"apply_patch", "write_file"}:
                item["changedPaths"] = (
                    self._changed_paths_from_patch_result(result)
                    if name == "apply_patch"
                    else [result.get("path")] if isinstance(result.get("path"), str) else []
                )
            elif name in {"task", "child_task"}:
                item["childStatus"] = result.get("status")
                subagent = result.get("subagent") if isinstance(result.get("subagent"), dict) else {}
                item["agentType"] = result.get("agentType") or subagent.get("agentType")
                if isinstance(result.get("ownedScope"), list):
                    item["ownedScope"] = result.get("ownedScope")
                if isinstance(result.get("expectedArtifacts"), list):
                    item["expectedArtifacts"] = result.get("expectedArtifacts")
                if isinstance(result.get("verificationRequirements"), list):
                    item["verificationRequirements"] = result.get("verificationRequirements")
                if isinstance(result.get("changedFiles"), list):
                    item["changedFiles"] = result.get("changedFiles")
                if isinstance(result.get("verification"), list):
                    item["verification"] = result.get("verification")
                if isinstance(result.get("testsRun"), list):
                    item["testsRun"] = result.get("testsRun")
            evidence.append({key: value for key, value in item.items() if value not in (None, [], "")})
        return evidence

    def _unresolved_failed_tool_results(
        self,
        tool_evidence: list[dict[str, Any]],
        *,
        changed_files: list[dict[str, Any]] | None = None,
        verification: list[dict[str, Any]] | None = None,
        tests_run: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        unresolved: list[dict[str, Any]] = []
        for index, item in enumerate(tool_evidence):
            if item.get("failed") is not True:
                continue
            if self._failed_tool_result_has_equivalent_success(
                item,
                tool_evidence,
                changed_files=changed_files,
                verification=verification,
                tests_run=tests_run,
            ):
                continue
            tool_name = item.get("name")
            has_later_success = any(
                later.get("name") == tool_name and later.get("failed") is not True
                for later in tool_evidence[index + 1 :]
            )
            if not has_later_success:
                unresolved.append(item)
        return unresolved

    def _failed_tool_result_has_equivalent_success(
        self,
        failed_item: dict[str, Any],
        tool_evidence: list[dict[str, Any]],
        *,
        changed_files: list[dict[str, Any]] | None = None,
        verification: list[dict[str, Any]] | None = None,
        tests_run: list[dict[str, Any]] | None = None,
    ) -> bool:
        if failed_item.get("name") == "child_task":
            return self._failed_child_task_has_equivalent_success(
                failed_item,
                tool_evidence,
                changed_files=changed_files,
                verification=verification,
                tests_run=tests_run,
            )
        if failed_item.get("name") in {"apply_patch", "write_file"}:
            return self._failed_workspace_write_has_equivalent_success(
                failed_item,
                tool_evidence,
                changed_files=changed_files,
                verification=verification,
                tests_run=tests_run,
            )
        if failed_item.get("name") != "run_command":
            return False
        failed_key = self._structural_command_resolution_key(failed_item.get("command"))
        if not failed_key:
            return False
        for item in tool_evidence:
            if item is failed_item or item.get("name") != "run_command" or item.get("failed") is True:
                continue
            if self._structural_command_resolution_key(item.get("command")) == failed_key:
                return True
        return False

    def _failed_workspace_write_has_equivalent_success(
        self,
        failed_item: dict[str, Any],
        tool_evidence: list[dict[str, Any]],
        *,
        changed_files: list[dict[str, Any]] | None = None,
        verification: list[dict[str, Any]] | None = None,
        tests_run: list[dict[str, Any]] | None = None,
    ) -> bool:
        failed_paths = {
            self._completion_contract_normalize_path(path)
            for path in (failed_item.get("changedPaths") or [])
            if self._completion_contract_normalize_path(path)
        }
        has_passing_signal = any(
            str(item.get("status") or "").strip().lower() in {"passed", "success", "completed"}
            for item in [*(verification or []), *(tests_run or [])]
            if isinstance(item, dict)
        )
        changed_paths = {
            self._completion_changed_file_path(item)
            for item in (changed_files or [])
            if self._completion_changed_file_path(item)
        }
        if not failed_paths:
            if failed_item.get("name") != "apply_patch" or not has_passing_signal or not changed_paths:
                return False
            for item in tool_evidence:
                if item is failed_item or item.get("failed") is True:
                    continue
                if item.get("name") not in {"apply_patch", "write_file"}:
                    continue
                success_paths = {
                    self._completion_contract_normalize_path(path)
                    for path in (item.get("changedPaths") or [])
                    if self._completion_contract_normalize_path(path)
                }
                if success_paths and not success_paths.isdisjoint(changed_paths):
                    return True
            return False
        for item in tool_evidence:
            if item is failed_item or item.get("failed") is True:
                continue
            if item.get("name") not in {"apply_patch", "write_file"}:
                continue
            success_paths = {
                self._completion_contract_normalize_path(path)
                for path in (item.get("changedPaths") or [])
                if self._completion_contract_normalize_path(path)
            }
            if success_paths and not failed_paths.isdisjoint(success_paths):
                return True

        if changed_paths and not failed_paths.isdisjoint(changed_paths):
            if has_passing_signal:
                return True
        return False

    def _failed_child_task_has_equivalent_success(
        self,
        failed_item: dict[str, Any],
        tool_evidence: list[dict[str, Any]],
        *,
        changed_files: list[dict[str, Any]] | None = None,
        verification: list[dict[str, Any]] | None = None,
        tests_run: list[dict[str, Any]] | None = None,
    ) -> bool:
        failed_title = str(failed_item.get("summary") or "").strip().casefold()
        failed_kind = self._completion_child_task_kind(failed_item)
        failed_tokens = self._completion_child_task_identity_tokens(failed_item)
        if self._failed_child_task_contract_is_satisfied(
            failed_item,
            changed_files=changed_files or [],
            verification=verification or [],
            tests_run=tests_run or [],
        ):
            return True
        if not failed_title and not failed_kind:
            return False
        for item in tool_evidence:
            if item is failed_item or item.get("name") != "child_task" or item.get("failed") is True:
                continue
            success_kind = self._completion_child_task_kind(item)
            if failed_kind and success_kind and failed_kind == success_kind:
                return True
            success_title = str(item.get("summary") or "").strip().casefold()
            if failed_title and success_title and failed_title == success_title:
                return True
            if failed_title and success_title:
                failed_words = {word for word in re.findall(r"[a-z0-9_]+", failed_title) if len(word) > 2}
                success_words = {word for word in re.findall(r"[a-z0-9_]+", success_title) if len(word) > 2}
                overlap = failed_words & success_words
                if len(overlap) >= 2 and not overlap.isdisjoint({"frontend", "backend", "pytest", "tests"}):
                    return True
            success_tokens = self._completion_child_task_identity_tokens(item)
            if failed_tokens and success_tokens:
                core_overlap = failed_tokens & success_tokens
                if len(core_overlap) >= 3:
                    return True
        return False

    def _failed_child_task_contract_is_satisfied(
        self,
        failed_item: dict[str, Any],
        *,
        changed_files: list[dict[str, Any]],
        verification: list[dict[str, Any]],
        tests_run: list[dict[str, Any]],
    ) -> bool:
        expected_paths = self._completion_contract_expected_artifact_paths({
            "expectedArtifacts": failed_item.get("expectedArtifacts"),
        })
        if not expected_paths:
            changed_expectations = failed_item.get("changedFiles")
            if isinstance(changed_expectations, list):
                expected_paths = [
                    self._completion_contract_normalize_path(item.get("path"))
                    for item in changed_expectations
                    if isinstance(item, dict) and self._completion_contract_normalize_path(item.get("path"))
                ]
        if not expected_paths:
            expected_paths = self._completion_contract_expected_artifact_paths({
                "ownedScope": failed_item.get("ownedScope"),
            })
        changed_paths = {
            self._completion_changed_file_path(item)
            for item in changed_files
            if self._completion_changed_file_path(item)
        }
        if expected_paths:
            for expected_path in expected_paths:
                if expected_path.endswith("/"):
                    if not any(path.startswith(expected_path) for path in changed_paths):
                        return False
                elif expected_path not in changed_paths:
                    return False
        verification_families = self._completion_contract_verification_families({
            "verificationRequirements": failed_item.get("verificationRequirements"),
        })
        if verification_families:
            observed_families: set[str] = set()
            for item in [*verification, *tests_run]:
                status = str(item.get("status") or "").strip().lower()
                if status not in {"passed", "success", "completed"}:
                    continue
                observed_families.update(self._completion_verification_item_families(item))
            if verification_families.isdisjoint(observed_families):
                return False
        return bool(expected_paths or verification_families)

    def _completion_child_task_kind(self, item: dict[str, Any]) -> str:
        text = " ".join(
            str(item.get(key) or "")
            for key in ("summary", "title", "agentType", "childTaskId")
        ).casefold()
        if any(token in text for token in ("frontend", "index.html", "app.js", "styles.css")):
            return "frontend"
        if any(token in text for token in ("pytest", "tests/", "test_", "test coverage")):
            return "tests"
        if any(token in text for token in ("backend", "blog_models.py", "blog_service.py", "blog_api.py", "blog_storage.py")):
            return "backend"
        return ""

    def _completion_child_task_identity_tokens(self, item: dict[str, Any]) -> set[str]:
        text = " ".join(
            str(item.get(key) or "")
            for key in ("summary", "title")
        ).casefold()
        stop_words = {
            "the", "and", "for", "with", "from", "into", "after", "before", "task", "child",
            "status", "runtime", "continue", "previous", "partial", "handoff", "instead",
            "restarting", "repair", "fix", "failed", "failure", "blocked", "completion",
            "because", "verification", "review", "worker", "planner",
        }
        tokens = {
            token for token in re.findall(r"[a-z0-9_]+", text)
            if len(token) > 2 and token not in stop_words
        }
        return tokens

    def _structural_command_resolution_key(self, command: Any) -> tuple[str, tuple[str, ...]] | None:
        text = str(command or "").strip().casefold()
        if not text or self._completion_text_mentions_targeted_verification(text):
            return None
        if not re.search(r"\b(?:get-childitem|ls|dir)\b", text):
            return None
        paths = tuple(sorted(set(re.findall(
            r"(?<![\w.-])[\w./\\-]+\.(?:html|css|md|txt|json|js|ts|py)(?![\w.-])",
            text,
            flags=re.IGNORECASE,
        ))))
        if not paths:
            return None
        return ("file_listing", paths)

    def _completion_tool_result_failed(self, result: dict[str, Any]) -> bool:
        status = str(result.get("status") or "").strip().lower()
        if status in {"failed", "timeout", "killed", "validation_failed"}:
            return True
        if result.get("ok") is False:
            return True
        exit_code = result.get("exitCode")
        return isinstance(exit_code, int) and exit_code != 0

    def _validate_worker_output(self, *, session_id: str, task: dict[str, Any]) -> None:
        """Warn if a worker task completes without testsRun or risks."""
        role = task.get("role", "root")
        if role == "root":
            return
        root_task_id = task.get("rootTaskId")
        if not root_task_id or root_task_id == task.get("id"):
            return
        missing: list[str] = []
        if not task.get("testsRun"):
            missing.append("testsRun")
        if not task.get("risks"):
            missing.append("risks")
        if missing:
            self._publish(
                session_id=session_id,
                task=task,
                event_type="task.worker.validation",
                payload={
                    "taskId": task["id"],
                    "missingFields": missing,
                    "warning": f"Worker task completed without required fields: {', '.join(missing)}",
                },
            )
            logger.warning(
                "Worker task %s completed without %s",
                task["id"], ", ".join(missing),
            )

    def _reflect_on_result(
        self,
        *,
        session_id: str,
        task: dict[str, Any],
        goal: str,
        summary: str,
        context: dict[str, Any],
    ) -> ReflectionResult | None:
        """Conditionally trigger reflection: routing decision + global config enabled."""
        if self._reflector is None:
            return None
        routing = context.get("routing", {})
        if not routing.get("enable_reflection"):
            return None

        self._store.update_task_status(task["id"], "verifying")
        self._publish(
            session_id=session_id,
            task=task,
            event_type="task.reflection.started",
            payload={"goal": goal},
        )

        refl_span = self._tracer.start_span(
            "reflection",
            trace_id=getattr(self, "_active_trace_id", None),
            attributes={"taskId": task["id"]},
        )

        tool_output = json.dumps(
            context.get("tool_results", []), ensure_ascii=False,
        )[:2000]

        # Construct retry_fn so the reflection loop can re-generate improved output
        def _retry_fn(feedback: str) -> str:
            retry_prompt = (
                f"你之前的回答存在以下问题:\n{feedback}\n\n"
                f"原始目标: {goal}\n"
                f"请基于以上反馈，重新生成一个改进版的回答。"
            )
            try:
                retry_response = self._provider.generate(
                    retry_prompt,
                    {
                        **(context.get("_provider_context") if isinstance(context.get("_provider_context"), dict) else {}),
                        "messages": [{"role": "user", "content": retry_prompt}],
                    },
                )
                return retry_response.get("message") or retry_response.get("final_answer") or summary
            except Exception:  # noqa: BLE001
                return summary

        result = self._reflector.reflect(
            goal=goal,
            output=summary,
            context=tool_output,
            retry_fn=_retry_fn,
            provider_context=context.get("_provider_context") if isinstance(context.get("_provider_context"), dict) else None,
        )

        self._publish(
            session_id=session_id,
            task=task,
            event_type="task.reflection.completed",
            payload={
                "accepted": result.accepted,
                "finalScore": result.final_score,
                "iterations": len(result.iterations),
            },
        )
        self._tracer.end_span(
            refl_span.span_id, status="ok",
            attributes={"accepted": result.accepted, "iterations": len(result.iterations)},
        )
        return result

    def _fail_task(
        self,
        session_id: str,
        task: dict[str, Any],
        summary: str,
        error_code: str,
        *,
        skip_drain: bool = False,
        structured_result: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        logger.warning("Task %s failed: error_code=%s summary=%s", task["id"], error_code, summary[:200])
        task_plan = task.get("plan") or []
        self._validate_task_transition(task["status"], "failed", task["id"], silent=True)
        if task["status"] in {"completed", "failed", "cancelled"}:
            return {**task, "errorCode": error_code, "resultSummary": summary}
        failed_task = self._store.update_task(
            task_id=task["id"],
            status="failed",
            plan=task_plan,
            summary=summary,
            result_summary=summary,
            error_code=error_code,
            structured_result=structured_result,
        )
        runtime_task = {
            **failed_task,
            "plan": task_plan,
            "errorCode": error_code,
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
                session_id=session_id,
                task_id=runtime_task["id"],
                role="assistant",
                content=summary,
                kind="failure",
                status="failed",
            )
        self._remember_task_result(session_id=session_id, task=runtime_task)
        self._promote_scratchpad_to_memory(session_id)
        self._clear_pending_react_state(task["id"])
        self._record_task_metrics(session_id=session_id, task=runtime_task, task_status="failed")
        # --- Decision trace: failure ---
        self._publish(
            session_id=session_id,
            task=runtime_task,
            event_type="agent.decision.completion",
            payload={
                "decision": "failed",
                "whyFailed": runtime_task.get("failureReason") or summary[:500],
                "errorCode": error_code,
            },
        )
        self._publish(
            session_id=session_id,
            task=runtime_task,
            event_type="message.failed",
            payload={"messageId": failed_msg["id"], "content": summary, "errorCode": error_code},
        )
        self._publish(
            session_id=session_id,
            task=runtime_task,
            event_type="task.failed",
            payload={
                "status": runtime_task["status"],
                "plan": runtime_task["plan"],
                "currentStep": runtime_task.get("currentStep"),
                "changedFiles": runtime_task.get("changedFiles") or [],
                "commands": runtime_task.get("commands") or [],
                "verification": runtime_task.get("verification") or [],
                "summary": summary,
                "resultSummary": summary,
                "detail": summary,
                "errorCode": error_code,
                **({"structuredResult": structured_result} if structured_result is not None else {}),
            },
        )
        # Fire on_task_failed hooks
        self._fire_hooks("on_task_failed", session_id, runtime_task, extra_context={"errorCode": error_code})
        if not skip_drain:
            self._drain_session_queue(session_id)
        return runtime_task

    def _record_task_metrics(
        self,
        *,
        session_id: str,
        task: dict[str, Any],
        tool_results: list[dict[str, Any]] | None = None,
        task_status: str = "completed",
    ) -> None:
        tool_results = tool_results or []
        duration_ms = (task.get("updated_at") or 0) - (task.get("created_at") or 0)
        if duration_ms < 0:
            duration_ms = 0
        command_count = 0
        patch_count = 0
        command_success = 0
        command_failure = 0
        patch_success = 0
        patch_failure = 0
        for tr in tool_results:
            name = tr.get("name", "")
            result = tr.get("result", {})
            if name == "run_command":
                command_count += 1
                status = result.get("status") or result.get("exitCode")
                if status in ("completed", 0):
                    command_success += 1
                else:
                    command_failure += 1
            elif name == "apply_patch":
                patch_count += 1
                if result.get("patch_id") or result.get("applied"):
                    patch_success += 1
                else:
                    patch_failure += 1
        self._store.record_task_metrics({
            "taskId": task["id"],
            "sessionId": session_id,
            "durationMs": duration_ms,
            "toolCallCount": len(tool_results),
            "commandCount": command_count,
            "patchCount": patch_count,
            "commandSuccessCount": command_success,
            "commandFailureCount": command_failure,
            "patchSuccessCount": patch_success,
            "patchFailureCount": patch_failure,
            "taskStatus": task_status,
            "wasCancelled": task_status == "cancelled",
        })

    def _run_post_task_validation(
        self,
        *,
        session_id: str,
        task: dict[str, Any],
        context: dict[str, Any],
        tool_results: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        patches = self._completed_patch_results(tool_results)
        if not patches:
            return None

        checks: list[dict[str, Any]] = []
        ran: list[str] = []

        if self._workspace_has_git_root(context.get("workspace_root")):
            for tool_name, start_token in (
                ("git_status", "Running post-task git status validation..."),
                ("git_diff", "Running post-task git diff validation..."),
            ):
                check = self._run_validation_tool(
                    session_id=session_id,
                    task=task,
                    tool_name=tool_name,
                    arguments={"workspaceRoot": context.get("workspace_root")},
                    start_token=start_token,
                )
                checks.append(check)
                if check["status"] == "completed":
                    ran.append(tool_name)
        else:
            checks.extend(
                [
                    {
                        "name": "git_status",
                        "status": "skipped",
                        "reason": "Workspace is not a Git repository.",
                    },
                    {
                        "name": "git_diff",
                        "status": "skipped",
                        "reason": "Workspace is not a Git repository.",
                    },
                ]
            )

        validation_command = self._resolve_validation_command(context=context, patches=patches)
        if validation_command:
            command_check = self._run_validation_tool(
                session_id=session_id,
                task=task,
                tool_name="run_command",
                arguments={
                    "workspaceRoot": context.get("workspace_root"),
                    "cwd": ".",
                    "command": validation_command,
                    "internalValidation": True,
                },
                start_token=f"Running post-task validation command: {validation_command}",
            )
            if command_check["status"] == "completed":
                ran.append("run_command")
        else:
            command_check = {
                "name": "run_command",
                "status": "skipped",
                "reason": "No validation command was configured.",
            }
        checks.append(command_check)

        summary = self._format_validation_summary(patches=patches, checks=checks, validation_command=validation_command)
        payload = {
            "patches": patches,
            "checks": checks,
            "ran": ran,
            "command": command_check if command_check["name"] == "run_command" else None,
            "summary": summary,
        }
        self._record_task_verification(session_id=session_id, task=task, validation=payload)
        payload["verification"] = task.get("verification") or []
        self._publish(
            session_id=session_id,
            task=task,
            event_type="task.validation.completed",
            payload=payload,
        )
        return payload

    def _workspace_has_git_root(self, workspace_root: Any) -> bool:
        if not isinstance(workspace_root, str) or not workspace_root.strip():
            return False
        root = Path(workspace_root)
        if not root.exists():
            return False
        for candidate in (root, *root.parents):
            if (candidate / ".git").exists():
                return True
        return False

    def _check_git_diff_before_merge(
        self,
        *,
        session_id: str,
        task: dict[str, Any],
        execution: dict[str, Any],
        workspace_root: str | None = None,
    ) -> dict[str, Any]:
        """Check git diff for conflicts before merging child results.

        Returns a dict with keys:
          - changed_files: list of file paths changed by child tasks
          - conflict_markers: list of files with conflict markers
          - scope_overlaps: list of file paths modified by multiple children
          - warnings: list of human-readable warning strings
          - safe: bool — True if no issues detected
        """
        import subprocess

        result: dict[str, Any] = {
            "changed_files": [],
            "conflict_markers": [],
            "scope_overlaps": [],
            "warnings": [],
            "safe": True,
        }

        # 1. Collect changed files from child task metadata
        child_changed_files: dict[str, list[str]] = {}  # file -> [task_ids]
        for subtask in execution.get("subtasks", []):
            task_id = getattr(subtask, "id", None) or (subtask.get("id") if isinstance(subtask, dict) else None)
            changed = getattr(subtask, "changed_files", None) or (subtask.get("changed_files") if isinstance(subtask, dict) else None) or []
            if isinstance(changed, str):
                try:
                    changed = json.loads(changed)
                except (json.JSONDecodeError, TypeError):
                    changed = []
            for f in changed:
                result["changed_files"].append(f)
                child_changed_files.setdefault(f, []).append(str(task_id))

        # 2. Check scope overlaps (same file modified by multiple children)
        for filepath, task_ids in child_changed_files.items():
            if len(task_ids) > 1:
                result["scope_overlaps"].append(filepath)
                result["warnings"].append(
                    f"File {filepath} was modified by multiple child tasks: {', '.join(task_ids)}"
                )
                result["safe"] = False

        # 2b. Record scope conflict check when overlaps detected
        if result["scope_overlaps"]:
            task_id_val = task.get("id", "")
            overlap_subtask_ids = sorted({tid for f, tids in child_changed_files.items() if len(tids) > 1 for tid in tids})
            scope_map = {tid: [f for f, tids in child_changed_files.items() if tid in tids and len(tids) > 1] for tid in overlap_subtask_ids}
            try:
                self._store.create_scope_conflict_check({
                    "taskId": task_id_val,
                    "sessionId": session_id,
                    "checkType": "pre_merge",
                    "subtaskIds": overlap_subtask_ids,
                    "scopeMap": scope_map,
                    "overlaps": result["scope_overlaps"],
                    "resolution": "merge_required",
                    "safe": False,
                })
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to record scope_conflict_check: %s", exc)
            self._publish(
                session_id=session_id, task=task,
                event_type="task.scope.merge.warning",
                payload={
                    "overlaps": result["scope_overlaps"],
                    "overlapCount": len(result["scope_overlaps"]),
                    "resolution": "merge_required",
                },
            )

        # 3. Run git diff checks if workspace root is available
        if workspace_root and self._workspace_has_git_root(workspace_root):
            try:
                # Check for conflict markers
                check_proc = subprocess.run(
                    ["git", "diff", "--check"],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=10,
                    cwd=workspace_root,
                )
                if check_proc.stdout.strip():
                    conflict_files = set()
                    for line in check_proc.stdout.strip().splitlines():
                        parts = line.split(":", 1)
                        if parts:
                            conflict_files.add(parts[0])
                    result["conflict_markers"] = sorted(conflict_files)
                    result["warnings"].append(
                        f"Git conflict markers detected in: {', '.join(sorted(conflict_files))}"
                    )
                    result["safe"] = False
            except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
                logger.warning("git diff --check failed: %s", exc)

        # 4. Publish merge check event
        if result["warnings"]:
            self._publish(
                session_id=session_id, task=task,
                event_type="task.merge.check",
                payload={
                    "safe": result["safe"],
                    "warnings": result["warnings"],
                    "changedFileCount": len(result["changed_files"]),
                    "overlapCount": len(result["scope_overlaps"]),
                },
            )

        return result

    def _completed_patch_results(self, tool_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
        patches: list[dict[str, Any]] = []
        for tool_result in tool_results:
            if tool_result.get("name") != "apply_patch":
                continue
            result = tool_result.get("result", {})
            if not isinstance(result, dict) or result.get("status") not in {"applied", "completed"}:
                continue
            patch_record = result.get("patch", {}) if isinstance(result.get("patch"), dict) else {}
            patches.append(
                {
                    "summary": result.get("summary") or patch_record.get("summary") or "Updated files",
                    "filesChanged": result.get("filesChanged") or patch_record.get("filesChanged") or 0,
                    "changedPaths": self._changed_paths_from_patch_result(result),
                    "patchId": patch_record.get("id"),
                }
            )
        return patches

    def _changed_paths_from_patch_result(self, result: dict[str, Any]) -> list[str]:
        changed_paths = result.get("changedPaths")
        if isinstance(changed_paths, list):
            return [str(path) for path in changed_paths if str(path).strip()]
        patch_record = result.get("patch")
        if isinstance(patch_record, dict):
            patch_paths = patch_record.get("changedPaths")
            if isinstance(patch_paths, list):
                return [str(path) for path in patch_paths if str(path).strip()]
        diff_text = result.get("diffText")
        if isinstance(diff_text, str):
            return self._changed_paths_from_diff_text(diff_text)
        return []

    def _changed_paths_from_diff_text(self, diff_text: str) -> list[str]:
        paths: list[str] = []
        for line in diff_text.splitlines():
            if line.startswith("+++ "):
                path = line[4:].strip()
                if path == "/dev/null":
                    continue
                paths.append(path[2:] if path.startswith("b/") else path)
            elif line.startswith("--- "):
                path = line[4:].strip()
                if path == "/dev/null":
                    continue
                normalized = path[2:] if path.startswith("a/") else path
                if normalized not in paths:
                    paths.append(normalized)
        return list(dict.fromkeys(path for path in paths if path))

    def _run_validation_tool(
        self,
        *,
        session_id: str,
        task: dict[str, Any],
        tool_name: str,
        arguments: dict[str, Any],
        start_token: str,
    ) -> dict[str, Any]:
        try:
            tool_result = self._execute_tool(
                session_id=session_id,
                task=task,
                tool_spec={
                    "name": tool_name,
                    "arguments": arguments,
                    "plan_step_id": f"validation-{tool_name.replace('_', '-')}",
                    "start_token": start_token,
                },
            )
        except Exception as exc:  # noqa: BLE001
            return {
                "name": tool_name,
                "status": "failed",
                "error": str(exc),
            }

        result = tool_result.get("result", {})
        status = result.get("status")
        if not isinstance(status, str) or not status:
            status = "completed"
        check = {
            "name": tool_name,
            "status": status,
            "result": result,
        }
        if tool_name == "run_command" and isinstance(arguments.get("command"), str):
            check["command"] = arguments["command"]
        return check

    def _record_task_verification(
        self,
        *,
        session_id: str,
        task: dict[str, Any],
        validation: dict[str, Any],
    ) -> None:
        records = self._verification_records_from_validation(validation)
        if not records:
            return
        updated_task = self._store.update_task(
            task_id=task["id"],
            verification=records,
        )
        task.update(updated_task)
        self._publish_task_run_snapshot(session_id=session_id, task=task)

    def _verification_records_from_validation(self, validation: dict[str, Any]) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for check in validation.get("checks", []):
            if not isinstance(check, dict):
                continue
            result = check.get("result") if isinstance(check.get("result"), dict) else {}
            command_log = result.get("commandLog") if isinstance(result.get("commandLog"), dict) else {}
            command = check.get("command")
            if not isinstance(command, str):
                command = result.get("command") if isinstance(result.get("command"), str) else None
            records.append(
                {
                    "id": command_log.get("id"),
                    "command": command,
                    "status": self._verification_status(check.get("status")),
                    "exitCode": result.get("exitCode") if isinstance(result, dict) else None,
                    "durationMs": result.get("durationMs") if isinstance(result, dict) else None,
                    "summary": self._validation_check_summary(check),
                    "startedAt": command_log.get("startedAt"),
                    "finishedAt": command_log.get("finishedAt"),
                }
            )
        return records

    def _verification_status(self, status: Any) -> str:
        if status == "completed":
            return "passed"
        if status in {"failed", "timeout", "killed", "validation_failed"}:
            return "failed"
        if status == "skipped":
            return "skipped"
        if status == "running":
            return "running"
        return str(status or "not_run")

    def _validation_check_summary(self, check: dict[str, Any]) -> str:
        if isinstance(check.get("reason"), str):
            return check["reason"]
        if isinstance(check.get("error"), str):
            return check["error"]
        result = check.get("result") if isinstance(check.get("result"), dict) else {}
        if isinstance(result.get("summary"), str):
            return result["summary"]
        if isinstance(result.get("stderr"), str) and result["stderr"].strip():
            return result["stderr"].strip().splitlines()[0]
        if isinstance(result.get("stdout"), str) and result["stdout"].strip():
            return result["stdout"].strip().splitlines()[0]
        return f"{check.get('name', 'validation')} {check.get('status', 'not_run')}"

    def _resolve_validation_command(self, *, context: dict[str, Any], patches: list[dict[str, Any]]) -> str | None:
        validation = context.get("post_task_validation")
        if isinstance(validation, dict):
            command = validation.get("command")
            if isinstance(command, str) and command.strip():
                return command.strip()

        changed_test_paths: list[str] = []
        for patch in patches:
            for path in patch.get("changedPaths", []):
                normalized = str(path).replace("\\", "/")
                if normalized.endswith(".py") and ("/tests/" in normalized or normalized.startswith("tests/")):
                    changed_test_paths.append(normalized)
        if changed_test_paths:
            ordered_paths = list(dict.fromkeys(changed_test_paths))
            return "pytest " + " ".join(ordered_paths)
        return None

    def _format_validation_summary(
        self,
        *,
        patches: list[dict[str, Any]],
        checks: list[dict[str, Any]],
        validation_command: str | None,
    ) -> str:
        changed_summaries = list(dict.fromkeys(str(patch["summary"]).strip() for patch in patches if str(patch["summary"]).strip()))
        changed_text = f"Changed: {'; '.join(changed_summaries)}." if changed_summaries else ""

        completed_names: list[str] = []
        for check in checks:
            if check.get("status") != "completed":
                continue
            if check["name"] == "git_status":
                completed_names.append("git status")
            elif check["name"] == "git_diff":
                completed_names.append("git diff")
            elif check["name"] == "run_command" and validation_command:
                completed_names.append(validation_command)

        validation_text = ""
        if completed_names:
            if len(completed_names) == 1:
                validation_text = f"Validated with {completed_names[0]}."
            else:
                validation_text = f"Validated with {', '.join(completed_names[:-1])}, and {completed_names[-1]}."

        failed_checks = [check for check in checks if check.get("status") == "failed"]
        failure_text = ""
        if failed_checks:
            failure_text = " Validation issues: " + " ".join(
                f"{check['name']} failed: {check.get('error', 'unknown error')}." for check in failed_checks
            )

        return " ".join(part for part in (changed_text, validation_text) if part).strip() + failure_text

    def _record_task_run_tool_result(
        self,
        *,
        session_id: str,
        task: dict[str, Any],
        tool_result: dict[str, Any],
    ) -> None:
        tool_name = tool_result.get("name")
        result = tool_result.get("result")
        if not isinstance(result, dict):
            return

        update: dict[str, Any] = {}
        if tool_name == "apply_patch":
            changed_files = self._merge_changed_files(
                task.get("changedFiles") or [],
                self._changed_files_from_patch_result(result),
            )
            if changed_files != (task.get("changedFiles") or []):
                update["changed_files"] = changed_files
        elif tool_name == "write_file":
            changed_files = self._merge_changed_files(
                task.get("changedFiles") or [],
                self._changed_files_from_write_file_result(result),
            )
            if changed_files != (task.get("changedFiles") or []):
                update["changed_files"] = changed_files
        elif tool_name == "run_command":
            arguments = tool_result.get("arguments") if isinstance(tool_result.get("arguments"), dict) else {}
            commands = self._merge_command_records(
                task.get("commands") or [],
                self._command_record_from_result(result, arguments),
            )
            if commands != (task.get("commands") or []):
                update["commands"] = commands

        if not update:
            return

        updated_task = self._store.update_task(task_id=task["id"], **update)
        task.update(updated_task)
        self._publish_task_run_snapshot(session_id=session_id, task=task)

    def _changed_files_from_patch_result(self, result: dict[str, Any]) -> list[dict[str, Any]]:
        if result.get("status") not in {"applied", "completed"}:
            return []
        summary = result.get("summary")
        patch = result.get("patch") if isinstance(result.get("patch"), dict) else {}
        patch_id = result.get("patchId") or patch.get("id")
        return [
            {
                "path": path,
                "status": self._patch_file_status(result.get("diffText"), path),
                "reason": summary if isinstance(summary, str) else None,
                "patchId": patch_id,
            }
            for path in self._changed_paths_from_patch_result(result)
        ]

    def _changed_files_from_write_file_result(self, result: dict[str, Any]) -> list[dict[str, Any]]:
        if result.get("status") != "written":
            return []
        path = result.get("path")
        if not isinstance(path, str) or not path.strip():
            return []
        return [
            {
                "path": path,
                "status": "added" if result.get("created") is True else "modified",
                "reason": f"write_file wrote {result.get('bytesWritten', 0)} byte(s)",
            }
        ]

    def _patch_file_status(self, diff_text: Any, path: str) -> str:
        if not isinstance(diff_text, str):
            return "modified"
        normalized = path.replace("\\", "/")
        for section in diff_text.split("diff --git "):
            if not section.strip() or normalized not in section.replace("\\", "/"):
                continue
            if "\n--- /dev/null" in section:
                return "added"
            if "\n+++ /dev/null" in section:
                return "deleted"
            if "\nrename from " in section and "\nrename to " in section:
                return "renamed"
        return "modified"

    def _merge_changed_files(
        self,
        current: list[dict[str, Any]],
        additions: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        merged: dict[str, dict[str, Any]] = {}
        for item in current:
            if isinstance(item, dict) and isinstance(item.get("path"), str):
                merged[item["path"]] = dict(item)
        for item in additions:
            path = item.get("path")
            if isinstance(path, str) and path.strip():
                merged[path] = {**merged.get(path, {}), **item}
        return list(merged.values())

    def _command_record_from_result(
        self,
        result: dict[str, Any],
        arguments: dict[str, Any],
    ) -> dict[str, Any] | None:
        if result.get("status") == "approval_required":
            return None
        command_log = result.get("commandLog") if isinstance(result.get("commandLog"), dict) else {}
        command = command_log.get("command") or result.get("command") or arguments.get("command")
        if not isinstance(command, str) or not command.strip():
            return None
        return {
            "id": command_log.get("id"),
            "command": command.strip(),
            "cwd": command_log.get("cwd") or result.get("cwd") or arguments.get("cwd"),
            "shell": result.get("shell") or arguments.get("shell"),
            "status": command_log.get("status") or result.get("status"),
            "exitCode": command_log.get("exitCode") if command_log.get("exitCode") is not None else result.get("exitCode"),
            "durationMs": command_log.get("durationMs") if command_log.get("durationMs") is not None else result.get("durationMs"),
            "summary": self._command_result_summary(result),
            "startedAt": command_log.get("startedAt"),
            "finishedAt": command_log.get("finishedAt"),
            "stdoutPath": command_log.get("stdoutPath"),
            "stderrPath": command_log.get("stderrPath"),
            "background": result.get("background") is True,
        }

    def _merge_command_records(
        self,
        current: list[dict[str, Any]],
        addition: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        if not addition:
            return current
        merged: list[dict[str, Any]] = []
        replaced = False
        addition_id = addition.get("id")
        for item in current:
            if addition_id and isinstance(item, dict) and item.get("id") == addition_id:
                merged.append({**item, **addition})
                replaced = True
            else:
                merged.append(item)
        if not replaced:
            merged.append(addition)
        return merged

    def _command_result_summary(self, result: dict[str, Any]) -> str:
        status = result.get("status") or "completed"
        stderr = result.get("stderr")
        stdout = result.get("stdout")
        if isinstance(stderr, str) and stderr.strip():
            return stderr.strip().splitlines()[0]
        if isinstance(stdout, str) and stdout.strip():
            return stdout.strip().splitlines()[0]
        exit_code = result.get("exitCode")
        return f"Command {status}" + (f" with exit {exit_code}" if exit_code is not None else "")
