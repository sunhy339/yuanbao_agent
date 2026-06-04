from __future__ import annotations

import json
import logging
import re
from typing import Any, Callable

from ..planner.decomposer import TaskDecomposer
from ..planner.types import (
    PlanResult,
    Subtask,
    build_subtask_prompt,
    child_tool_allowlist_for_agent,
    normalize_subtask_agent_type,
)
from ..provider.adapter import ProviderAdapter
from ..services.subagent_service import SubagentService
from .partial_handoff import (
    build_continuation_prompt,
    handoff_allows_continuation,
    partial_handoff_from_dispatch_result,
    summarize_partial_handoff,
)
from .result_synthesizer import ResultSynthesizer
from .types import OrchestrationResult

logger = logging.getLogger(__name__)

_REVIEW_PROMPT = """\
You are a supervisor reviewing a sub-task result. Evaluate whether it satisfies the requirement.

**Sub-task**: {title}
**Description**: {description}
**Result**: {result}
{structured_context}
{pre_check_warnings}
Respond with a JSON object:
- "approved": true or false
- "feedback": if not approved, explain what needs to be improved; if approved, leave empty or write "ok"

Additional review criteria:
- If changed files are listed, verify they are within the expected scope of the task.
- If no test files are mentioned in changed files, consider flagging as a risk.
- If risks are listed, evaluate their severity and whether they are acceptable.
- If pre-check warnings are shown above, evaluate their severity and whether the result is still acceptable.

Respond ONLY with valid JSON, no other text.
"""


# Paths that indicate a test file
_TEST_PATH_PATTERNS = (
    "/test_", "\\test_",
    "/tests/", "\\tests\\",
    "/__tests__/", "\\__tests__\\",
    "_test.py", "_test.ts", "_test.tsx", "_test.js",
    ".test.py", ".test.ts", ".test.tsx", ".test.js",
    ".spec.ts", ".spec.tsx", ".spec.js", ".spec.py",
)


def _is_test_path(path: str) -> bool:
    """Return True if the path looks like a test file."""
    return any(p in path for p in _TEST_PATH_PATTERNS)


class SupervisorOrchestrator:
    """Execute sub-tasks with supervisor review after each completion.

    For each sub-task:
    1. Dispatch via SubagentService
    2. Review the result via LLM
    3. If rejected, re-dispatch with feedback (up to max_retries)
    """

    def __init__(
        self,
        provider: ProviderAdapter,
        subagent_service: SubagentService,
        *,
        max_retries: int = 2,
    ) -> None:
        self._provider = provider
        self._subagent = subagent_service
        self._max_retries = max_retries
        self._decomposer = TaskDecomposer(provider=provider)
        self._synthesizer = ResultSynthesizer(provider=provider)
        self._last_review_count = 0

    def execute(
        self,
        goal: str,
        context: dict[str, Any],
        *,
        session_id: str,
        task: dict[str, Any],
        child_timeout_ms: int | None = None,
        is_paused_fn: Callable[[], bool] | None = None,
        completed_ids: set[str] | None = None,
        failed_ids: set[str] | None = None,
        prior_results: dict[str, str] | None = None,
        plan: PlanResult | None = None,
    ) -> OrchestrationResult:
        """Decompose goal, execute sub-tasks with review, synthesize results."""
        provider_context = context.get("_provider_context") if isinstance(context.get("_provider_context"), dict) else None
        if plan is None:
            plan = self._decomposer.decompose(
                goal,
                context.get("description", ""),
                provider_context=provider_context,
            )

        completed: set[str] = set(completed_ids or ())
        failed: set[str] = set(failed_ids or ())
        results: dict[str, str] = dict(prior_results or ())
        subtask_results: list[dict[str, Any]] = []
        partial_handoffs: list[dict[str, Any]] = []
        review_count = 0
        parent_task_id = task.get("id", "")

        for subtask_id in plan.execution_order:
            subtask = self._find_subtask(plan.subtasks, subtask_id)
            if subtask is None:
                continue

            # Skip already completed/failed (resume scenario)
            if subtask_id in completed:
                subtask.status = "completed"
                subtask.result = results.get(subtask_id, "Completed")
                subtask_results.append(self._subtask_to_dict(subtask))
                continue
            if subtask_id in failed:
                subtask.status = "failed"
                subtask.result = results.get(subtask_id, "Failed")
                subtask_results.append(self._subtask_to_dict(subtask))
                continue

            # Check dependency failures
            if self._has_failed_dependency(subtask, failed):
                subtask.status = "skipped"
                failed.add(subtask.id)
                results[subtask.id] = "Skipped: dependency failed"
                subtask_results.append(self._subtask_to_dict(subtask))
                continue

            if not self._check_dependencies(subtask, completed):
                subtask.status = "skipped"
                failed.add(subtask.id)
                results[subtask.id] = "Skipped: dependencies not met"
                subtask_results.append(self._subtask_to_dict(subtask))
                continue

            # Execute with review loop
            subtask.status = "running"
            success = self._execute_with_review(
                subtask,
                session_id=session_id,
                parent_task_id=parent_task_id,
                parent_goal=goal,
                child_timeout_ms=child_timeout_ms,
                provider_context=provider_context,
                partial_handoffs=partial_handoffs,
            )
            review_count += self._last_review_count

            if success:
                completed.add(subtask.id)
                results[subtask.id] = subtask.result or "Completed"
            else:
                failed.add(subtask.id)
                results[subtask.id] = f"Failed: {subtask.result}"

            subtask_results.append(self._subtask_to_dict(subtask))

            # Cooperative pause
            if is_paused_fn is not None and is_paused_fn():
                return OrchestrationResult(
                    success=None,
                    summary="",
                    subtask_results=subtask_results,
                    review_count=review_count,
                    paused=True,
                    completed=list(completed),
                    failed=list(failed),
                    results=dict(results),
                    partial_handoffs=list(partial_handoffs),
                )

        # Synthesize
        success = len(failed) == 0
        summary = self._synthesizer.synthesize(
            goal,
            subtask_results,
            provider_context=provider_context,
        )

        return OrchestrationResult(
            success=success,
            summary=summary,
            subtask_results=subtask_results,
            review_count=review_count,
            completed=list(completed),
            failed=list(failed),
            results=dict(results),
            partial_handoffs=list(partial_handoffs),
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _execute_with_review(
        self,
        subtask: Subtask,
        *,
        session_id: str,
        parent_task_id: str,
        parent_goal: str | None = None,
        child_timeout_ms: int | None = None,
        provider_context: dict[str, Any] | None = None,
        partial_handoffs: list[dict[str, Any]] | None = None,
    ) -> bool:
        """Execute a sub-task with supervisor review and retry loop."""
        self._last_review_count = 0
        description = subtask.description
        max_continuations = self._max_retries
        continuation_attempts = 0

        for attempt in range(self._max_retries + 1):
            # Dispatch
            try:
                dispatch_result = self._subagent.dispatch({
                    "prompt": build_subtask_prompt(
                        parent_goal=parent_goal,
                        subtask=subtask,
                        prompt_override=description,
                        compact=True,
                    ),
                    "planningPrompt": description,
                    "title": subtask.title,
                    "sessionId": session_id,
                    "taskId": parent_task_id,
                    "agentType": normalize_subtask_agent_type(subtask.agent_type),
                    "childToolAllowlist": child_tool_allowlist_for_agent(subtask.agent_type),
                    "profile": {
                        "ownedScope": list(subtask.owned_scope),
                        "expectedArtifacts": [dict(item) for item in subtask.expected_artifacts],
                        "verificationRequirements": [dict(item) for item in subtask.verification_requirements],
                    },
                    **({"timeoutMs": child_timeout_ms} if child_timeout_ms is not None else {}),
                })
                result_text = dispatch_result.get("summary") or "Completed"
                if str(dispatch_result.get("status") or "").strip().lower() == "failed":
                    error = dispatch_result.get("error") if isinstance(dispatch_result.get("error"), dict) else {}
                    partial_handoff = partial_handoff_from_dispatch_result(dispatch_result)
                    if partial_handoff:
                        partial_handoff["subtaskId"] = subtask.id
                        partial_handoff["subtaskTitle"] = subtask.title
                        if partial_handoffs is not None:
                            partial_handoffs.append(partial_handoff)
                        handoff_summary = summarize_partial_handoff(partial_handoff)
                        if handoff_summary:
                            result_text = f"{result_text}\n{handoff_summary}"
                        if (
                            continuation_attempts < max_continuations
                            and attempt < self._max_retries
                            and handoff_allows_continuation(partial_handoff)
                        ):
                            description = build_continuation_prompt(
                                original_description=subtask.description,
                                handoff=partial_handoff,
                            )
                            continuation_attempts += 1
                            continue
                    message = str(
                        error.get("message")
                        or dispatch_result.get("summary")
                        or "Child subtask failed."
                    ).strip()
                    subtask.status = "failed"
                    subtask.result = message
                    return False
            except Exception as exc:  # noqa: BLE001
                subtask.status = "failed"
                subtask.result = str(exc)
                return False

            # Review
            approved, feedback = self._review_result(
                subtask.title, description, result_text,
                dispatch_result=dispatch_result,
                provider_context=provider_context,
            )
            self._last_review_count += 1

            if approved:
                subtask.status = "completed"
                subtask.result = result_text
                return True

            # Prepare retry with feedback
            if attempt < self._max_retries:
                logger.info(
                    "Supervisor rejected subtask %s (attempt %d): %s",
                    subtask.id, attempt + 1, feedback,
                )
                description = (
                    f"{subtask.description}\n\n"
                    f"[Supervisor feedback]: {feedback}\n"
                    f"Please address the feedback and retry."
                )
            else:
                # Exhausted retries
                subtask.status = "failed"
                subtask.result = f"Rejected after {self._max_retries + 1} attempts: {feedback}"
                return False

        return False  # unreachable, but satisfies type checker

    def _review_result(
        self, title: str, description: str, result: str,
        *,
        dispatch_result: dict[str, Any] | None = None,
        provider_context: dict[str, Any] | None = None,
    ) -> tuple[bool, str]:
        """Ask LLM to review a sub-task result. Returns (approved, feedback)."""
        structured_context = self._build_structured_context(dispatch_result or {})
        pre_check_warnings = self._pre_check_warnings(dispatch_result or {})
        prompt = _REVIEW_PROMPT.format(
            title=title, description=description, result=result,
            structured_context=structured_context,
            pre_check_warnings=pre_check_warnings,
        )
        try:
            response = self._provider.generate(
                prompt,
                {
                    **(provider_context or {}),
                    "messages": [{"role": "user", "content": prompt}],
                },
            )
            message = response.get("message") or ""
            return self._parse_review(message)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Review call failed (%s), auto-approving", exc)
            return True, ""

    @staticmethod
    def _build_structured_context(dispatch_result: dict[str, Any]) -> str:
        """Build structured context section from dispatch result artifacts."""
        parts: list[str] = []
        result_data = dispatch_result.get("result") or {}
        if isinstance(result_data, dict):
            changed_files = result_data.get("changedFiles")
            if changed_files:
                parts.append(f"**Changed files**: {json.dumps(changed_files)}")
            tests_run = result_data.get("testsRun")
            if tests_run:
                parts.append(f"**Tests run**: {json.dumps(tests_run)}")
            risks = result_data.get("risks")
            if risks:
                parts.append(f"**Risks**: {json.dumps(risks)}")
        artifacts = dispatch_result.get("artifacts")
        if artifacts:
            parts.append(f"**Artifacts**: {json.dumps(artifacts)}")
        return "\n".join(parts)

    @staticmethod
    def _pre_check_warnings(dispatch_result: dict[str, Any]) -> str:
        """Run programmatic pre-checks and return warning text for the review prompt.

        Checks:
        1. User change overwrite: worker changed files that user had uncommitted.
        2. Test gap: changedFiles present but testsRun empty.
        3. Protocol consistency: structured result missing required fields.
        """
        warnings: list[str] = []
        result_data = dispatch_result.get("result")
        if not isinstance(result_data, dict) or not result_data:
            return ""

        changed_files = result_data.get("changedFiles") or []
        tests_run = result_data.get("testsRun") or []

        # --- Check 1: User change overwrite ---
        user_modified_paths = set(
            f.get("path", "") for f in changed_files
            if isinstance(f, dict) and f.get("userModified")
        )
        if user_modified_paths:
            paths_str = ", ".join(sorted(user_modified_paths))
            warnings.append(
                f"WARNING: Worker overwrites user's uncommitted changes in: {paths_str}. "
                "Review whether the worker preserved the user's intent."
            )

        # --- Check 2: Test gap ---
        code_files = [
            f for f in changed_files
            if isinstance(f, dict) and not _is_test_path(f.get("path", ""))
        ]
        test_files = [
            f for f in changed_files
            if isinstance(f, dict) and _is_test_path(f.get("path", ""))
        ]
        if code_files and not tests_run and not test_files:
            warnings.append(
                "WARNING: Changed files found but no tests were run and no test files were changed. "
                "Consider flagging as a test coverage gap."
            )

        # --- Check 3: Protocol consistency ---
        required_fields = ("summary", "status", "changedFiles", "testsRun", "risks", "keyFindings")
        missing = [f for f in required_fields if f not in result_data]
        if missing:
            warnings.append(
                f"WARNING: Structured result is missing required fields: {', '.join(missing)}. "
                "Protocol consistency check failed."
            )

        if not warnings:
            return ""
        return "\n**Pre-check warnings:**\n" + "\n".join(f"- {w}" for w in warnings) + "\n"

    @staticmethod
    def _parse_review(text: str) -> tuple[bool, str]:
        """Parse LLM review response into (approved, feedback)."""
        # Try JSON parse
        try:
            fence_match = re.search(r"```(?:json)?\s*\n?(.*?)```", text, re.DOTALL)
            json_text = fence_match.group(1).strip() if fence_match else text
            data = json.loads(json_text)
            approved = bool(data.get("approved", True))
            feedback = str(data.get("feedback", ""))
            return approved, feedback
        except (json.JSONDecodeError, ValueError, AttributeError):
            pass

        # Fallback: look for keywords
        lower = text.lower()
        if '"approved": false' in lower or '"approved":false' in lower:
            return False, text
        if '"approved": true' in lower or '"approved":true' in lower:
            return True, ""

        # Default: approve
        return True, ""

    @staticmethod
    def _find_subtask(subtasks: list[Subtask], subtask_id: str) -> Subtask | None:
        for s in subtasks:
            if s.id == subtask_id:
                return s
        return None

    @staticmethod
    def _check_dependencies(subtask: Subtask, completed: set[str]) -> bool:
        return all(dep in completed for dep in subtask.dependencies)

    @staticmethod
    def _has_failed_dependency(subtask: Subtask, failed: set[str]) -> bool:
        return bool(set(subtask.dependencies) & failed)

    @staticmethod
    def _subtask_to_dict(subtask: Subtask) -> dict[str, Any]:
        return {
            "id": subtask.id,
            "title": subtask.title,
            "agentType": subtask.agent_type,
            "ownedScope": list(subtask.owned_scope),
            "expectedArtifacts": [dict(item) for item in subtask.expected_artifacts],
            "verificationRequirements": [dict(item) for item in subtask.verification_requirements],
            "status": subtask.status,
            "result": subtask.result,
        }
