from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


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
        completion_evidence = self._build_completion_evidence(
            task=task,
            summary=final_summary,
            validation=validation,
            tool_results=tool_results or [],
        )
        completion_review = self._completion_review_conclusion(context or {})
        if completion_review:
            completion_evidence["reviewConclusion"] = completion_review

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
                final_summary = reflection_result.improved_summary
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
        # --- Completion decision advisory ---
        completion_advice = self._consult_completion_advisor(task, final_summary, context or {})
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
            if completion_advice.get("rationale"):
                completion_payload["advisorRationale"] = completion_advice["rationale"]
            if completion_advice.get("fallback_reason"):
                completion_payload["advisorFallbackReason"] = completion_advice["fallback_reason"]
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
        is_write_or_verification_task = self._is_write_or_verification_task(task=task, context=context)
        if not is_write_or_verification_task:
            return {"action": "complete", "reason": "Read-only completion is allowed."}
        tool_failure_gate = self._completion_tool_failure_gate(completion_evidence)
        if tool_failure_gate is not None:
            return tool_failure_gate
        acceptance_gate = self._completion_acceptance_gate(completion_evidence)
        if acceptance_gate is not None:
            return acceptance_gate
        verification_match_gate = self._completion_verification_match_gate(completion_evidence)
        if verification_match_gate is not None:
            return verification_match_gate
        if self._completion_needs_verification_review(completion_evidence):
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
            missing = [
                str(item)
                for item in (requirements.get("missing") or [])
                if str(item).strip()
            ]
            if missing:
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

    def _completion_needs_verification_review(self, completion_evidence: dict[str, Any]) -> bool:
        if completion_evidence.get("evidenceLevel") != "runtime_evidence":
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

    def _completion_verification_requirements(
        self,
        *,
        changed_files: list[dict[str, Any]],
        verification: list[dict[str, Any]],
        tests_run: list[dict[str, Any]],
    ) -> dict[str, Any]:
        required = sorted({
            family
            for item in changed_files
            for family in self._completion_path_verification_families(
                self._completion_changed_file_path(item)
            )
        })
        if not required:
            return {"required": [], "matched": [], "missing": [], "status": "not_required"}
        matched: set[str] = set()
        for item in verification:
            if item.get("status") != "passed":
                continue
            matched.update(self._completion_verification_item_families(item))
        for item in tests_run:
            if item.get("status") not in {"passed", "success", "completed"}:
                continue
            matched.update(self._completion_verification_item_families(item))
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

    def _completion_verification_item_families(self, item: dict[str, Any]) -> set[str]:
        text = " ".join(
            str(item.get(key) or "")
            for key in ("command", "name", "summary", "suite")
        ).casefold()
        families: set[str] = set()
        token_map = {
            "python": ("pytest", "unittest", "tox", "mypy", "pyright", "ruff", "python -m"),
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
        )

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
            "npm run test",
            "pnpm run test",
            "yarn run test",
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
            "ruff",
            "eslint",
            "cargo check",
            "npm run build",
            "pnpm build",
            "yarn build",
            "cargo build",
            "go build",
            "dotnet build",
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

    def _is_write_or_verification_task(self, *, task: dict[str, Any], context: dict[str, Any]) -> bool:
        routing = task.get("routing") if isinstance(task.get("routing"), dict) else {}
        context_routing = context.get("routing") if isinstance(context.get("routing"), dict) else {}
        scenario = str(routing.get("scenario") or context_routing.get("scenario") or "").strip().lower()
        if scenario in {"code_edit", "debug", "test_write", "doc_write"}:
            return True
        if task.get("type") == "validate":
            return True
        if routing.get("activeWorktree") or context_routing.get("activeWorktree"):
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
        approval = self._store.create_approval(
            task["id"],
            "completion_review",
            {
                "summary": summary,
                "risk": risk or "completion evidence requires review",
                "reason": reason,
                "completionEvidence": completion_evidence,
                "structuredResult": structured_result,
            },
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

    def _consult_completion_advisor(
        self,
        task: dict[str, Any],
        summary: str,
        context: dict[str, Any] | None = None,
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
            input_context: dict[str, Any] = {
                "goal": task.get("goal", ""),
                "summary": summary[:2000],
                "acceptance_criteria": task.get("acceptanceCriteria") or [],
                "completion_evidence": self._build_completion_evidence(
                    task=task,
                    summary=summary,
                    validation=None,
                    tool_results=[],
                ),
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
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning("Completion advisor call failed for task %s: %s", task.get("id"), exc)
            return None

    def _should_skip_completion_advisor(self, task: dict[str, Any], context: dict[str, Any]) -> bool:
        if context.get("_skip_completion_advisor") is True:
            return True
        if context.get("_child_worker") is True or task.get("role") != "root":
            return not bool(self._advisor_config(context).get("enableChildCompletionAdvisor"))
        return False

    def _build_completion_evidence(
        self,
        *,
        task: dict[str, Any],
        summary: str,
        validation: dict[str, Any] | None,
        tool_results: list[dict[str, Any]],
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
        verification = [
            dict(item) for item in (task.get("verification") or [])
            if isinstance(item, dict)
        ]
        tests_run = [
            dict(item) for item in (task.get("testsRun") or [])
            if isinstance(item, dict)
        ]
        command_verification = self._completion_tests_run_from_commands(commands)
        tests_run = self._merge_completion_tests_run(tests_run, command_verification)
        patches = self._completed_patch_results(tool_results)
        tool_evidence = self._completion_tool_evidence(tool_results)
        failed_tool_results = self._unresolved_failed_tool_results(tool_evidence)
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
            changed_files=changed_files,
            verification=verification_for_requirements,
            tests_run=tests_run,
        )

        has_workspace_evidence = bool(changed_files or patches)
        has_command_evidence = bool(commands)
        has_verification_evidence = bool(passed_verification or passed_tests_run)
        has_failed_evidence = bool(failed_verification or failed_validation_checks)
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

        acceptance = self._completion_acceptance_evidence(
            criteria=criteria,
            evidence_level=evidence_level,
            task=task,
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
            "unresolvedToolFailures": failed_tool_results,
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
                "failedVerification": len(failed_verification) + len(failed_validation_checks) + len(unresolved_failed_tests_run),
                "requiredVerificationFamilies": len(verification_requirements.get("required") or []),
                "missingVerificationFamilies": len(verification_requirements.get("missing") or []),
                "testsRun": len(tests_run),
                "passedTestsRun": len(passed_tests_run),
                "failedTestsRun": len(unresolved_failed_tests_run),
                "resolvedFailedTestsRun": len(resolved_failed_tests_run),
                "patches": len(patches),
                "toolResults": len(tool_evidence),
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

    def _completion_tests_run_from_commands(self, commands: list[dict[str, Any]]) -> list[dict[str, Any]]:
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
        return [
            {key: value for key, value in item.items() if value not in (None, "", [])}
            for item in tests_run
        ]

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
        for index, item in enumerate(normalized_items):
            status = str(item.get("status") or "").strip().lower()
            if status not in failed_statuses:
                continue
            keys = self._completion_verification_resolution_keys(item)
            families = self._completion_verification_item_families(item)
            identity = self._completion_verification_identity(item)
            has_later_success = False
            for later in normalized_items[index + 1 :]:
                later_status = str(later.get("status") or "").strip().lower()
                if later_status not in passed_statuses:
                    continue
                later_keys = self._completion_verification_resolution_keys(later)
                if keys and later_keys:
                    if keys.isdisjoint(later_keys):
                        continue
                    has_later_success = True
                    break
                later_families = self._completion_verification_item_families(later)
                if families and later_families and families.isdisjoint(later_families):
                    continue
                if not families and not later_families:
                    later_identity = self._completion_verification_identity(later)
                    if identity and later_identity and identity != later_identity:
                        continue
                has_later_success = True
                break
            if not has_later_success:
                unresolved.append(item)
        return unresolved

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
            "python:lint": ("ruff",),
            "javascript:test": (
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
            ),
            "javascript:typecheck": ("tsc", "typecheck", "type check"),
            "javascript:lint": ("eslint",),
            "javascript:build": ("npm run build", "pnpm build", "yarn build", "vite", "next build"),
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
        text = re.sub(r"\s+", " ", text)
        return text.strip()

    def _completion_acceptance_evidence(
        self,
        *,
        criteria: list[str],
        evidence_level: str,
        task: dict[str, Any],
        validation: dict[str, Any],
        tool_results: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if not criteria:
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
        return acceptance

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
            result = tool_result.get("result") if isinstance(tool_result.get("result"), dict) else {}
            if not isinstance(name, str) or not name:
                continue
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
            elif name == "task":
                item["childStatus"] = result.get("status")
                subagent = result.get("subagent") if isinstance(result.get("subagent"), dict) else {}
                item["agentType"] = result.get("agentType") or subagent.get("agentType")
            evidence.append({key: value for key, value in item.items() if value not in (None, [], "")})
        return evidence

    def _unresolved_failed_tool_results(self, tool_evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
        unresolved: list[dict[str, Any]] = []
        for index, item in enumerate(tool_evidence):
            if item.get("failed") is not True:
                continue
            tool_name = item.get("name")
            has_later_success = any(
                later.get("name") == tool_name and later.get("failed") is not True
                for later in tool_evidence[index + 1 :]
            )
            if not has_later_success:
                unresolved.append(item)
        return unresolved

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
                    {"messages": [{"role": "user", "content": retry_prompt}]},
                )
                return retry_response.get("message") or retry_response.get("final_answer") or summary
            except Exception:  # noqa: BLE001
                return summary

        result = self._reflector.reflect(
            goal=goal,
            output=summary,
            context=tool_output,
            retry_fn=_retry_fn,
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
