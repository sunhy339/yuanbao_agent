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

_HANDOFF_PROMPT = """\
You are a swarm coordinator. A sub-task has just been completed.

**Goal**: {goal}
**Completed sub-tasks**: {completed_list}
**Remaining sub-tasks**: {remaining_list}
**Last result** ({last_title}): {last_result}

Decide the next action. Respond with a JSON object:
- "next_subtask_id": the ID of the next sub-task to execute (from remaining list), or null if done
- "handoff_prompt": optional modified prompt for the next sub-task (include context from previous results), or null to use original description
- "done": true if all remaining work is covered by completed results, false otherwise

Respond ONLY with valid JSON, no other text.
"""


class SwarmOrchestrator:
    """Execute sub-tasks with dynamic handoff decisions between agents.

    After each sub-task completes, an LLM decides which sub-task to execute
    next and can modify the prompt based on accumulated context.
    """

    def __init__(
        self,
        provider: ProviderAdapter,
        subagent_service: SubagentService,
    ) -> None:
        self._provider = provider
        self._subagent = subagent_service
        self._decomposer = TaskDecomposer(provider=provider)
        self._synthesizer = ResultSynthesizer(provider=provider)
        self._last_handoff_prompt: str | None = None

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
    ) -> OrchestrationResult:
        """Decompose goal, execute sub-tasks with handoff, synthesize results."""
        provider_context = context.get("_provider_context") if isinstance(context.get("_provider_context"), dict) else None
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
        handoff_count = 0
        parent_task_id = task.get("id", "")
        continuation_attempted: dict[str, int] = {}

        # Build quick lookup
        subtask_map = {s.id: s for s in plan.subtasks}
        pending = set(plan.execution_order) - completed - failed

        # Find starting point
        current_id = self._pick_start(plan, completed, failed)

        while current_id is not None:
            subtask = subtask_map.get(current_id)
            if subtask is None:
                break

            # Check dependency
            if self._has_failed_dependency(subtask, failed):
                subtask.status = "skipped"
                failed.add(subtask.id)
                pending.discard(subtask.id)
                results[subtask.id] = "Skipped: dependency failed"
                subtask_results.append(self._subtask_to_dict(subtask))
                current_id = self._pick_next_available(pending, completed, failed, subtask_map)
                continue

            if not self._check_dependencies(subtask, completed):
                subtask.status = "skipped"
                failed.add(subtask.id)
                pending.discard(subtask.id)
                results[subtask.id] = "Skipped: dependencies not met"
                subtask_results.append(self._subtask_to_dict(subtask))
                current_id = self._pick_next_available(pending, completed, failed, subtask_map)
                continue

            # Execute
            subtask.status = "running"
            prompt_override = self._last_handoff_prompt
            try:
                dispatch_result = self._subagent.dispatch({
                    "prompt": build_subtask_prompt(
                        parent_goal=goal,
                        subtask=subtask,
                        prompt_override=prompt_override,
                        completed_context=results,
                        compact=True,
                    ),
                    "planningPrompt": prompt_override or subtask.description,
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
                if str(dispatch_result.get("status") or "").strip().lower() == "failed":
                    error = dispatch_result.get("error") if isinstance(dispatch_result.get("error"), dict) else {}
                    partial_handoff = partial_handoff_from_dispatch_result(dispatch_result)
                    if partial_handoff:
                        partial_handoff["subtaskId"] = subtask.id
                        partial_handoff["subtaskTitle"] = subtask.title
                        partial_handoffs.append(partial_handoff)
                        attempts = continuation_attempted.get(subtask.id, 0)
                        if handoff_allows_continuation(partial_handoff) and attempts < 2:
                            continuation_attempted[subtask.id] = attempts + 1
                            self._last_handoff_prompt = build_continuation_prompt(
                                original_description=subtask.description,
                                handoff=partial_handoff,
                            )
                            current_id = subtask.id
                            continue
                    message = str(
                        error.get("message")
                        or dispatch_result.get("summary")
                        or "Child subtask failed."
                    ).strip()
                    handoff_summary = summarize_partial_handoff(partial_handoff)
                    subtask.status = "failed"
                    subtask.result = f"{message}\n{handoff_summary}" if handoff_summary else message
                    failed.add(subtask.id)
                    results[subtask.id] = f"Failed: {subtask.result}"
                else:
                    subtask.status = "completed"
                    subtask.result = dispatch_result.get("summary") or "Completed"
                    completed.add(subtask.id)
                    results[subtask.id] = subtask.result
                    self._last_handoff_prompt = None
            except Exception as exc:  # noqa: BLE001
                subtask.status = "failed"
                subtask.result = str(exc)
                failed.add(subtask.id)
                results[subtask.id] = f"Failed: {exc}"

            pending.discard(subtask.id)
            subtask_results.append(self._subtask_to_dict(subtask))

            # Cooperative pause
            if is_paused_fn is not None and is_paused_fn():
                return OrchestrationResult(
                    success=None,
                    summary="",
                    subtask_results=subtask_results,
                    handoff_count=handoff_count,
                    paused=True,
                    completed=list(completed),
                    failed=list(failed),
                    results=dict(results),
                    partial_handoffs=list(partial_handoffs),
                )

            # Handoff decision
            if subtask.status == "completed" and pending:
                handoff_count += 1
                done, next_id, handoff_prompt = self._handoff_decision(
                    goal, subtask, completed, pending, subtask_map, provider_context=provider_context,
                )
                self._last_handoff_prompt = handoff_prompt
                if done:
                    break
                current_id = next_id
            else:
                current_id = self._pick_next_available(pending, completed, failed, subtask_map)

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
            handoff_count=handoff_count,
            completed=list(completed),
            failed=list(failed),
            results=dict(results),
            partial_handoffs=list(partial_handoffs),
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _handoff_decision(
        self,
        goal: str,
        last_subtask: Subtask,
        completed: set[str],
        pending: set[str],
        subtask_map: dict[str, Subtask],
        *,
        provider_context: dict[str, Any] | None = None,
    ) -> tuple[bool, str | None, str | None]:
        """Ask LLM which sub-task to execute next.

        Returns (done, next_subtask_id, handoff_prompt).
        """
        completed_list = ", ".join(
            f"{sid}: {subtask_map[sid].title}" for sid in sorted(completed) if sid in subtask_map
        )
        remaining_list = ", ".join(
            f"{sid}: {subtask_map[sid].title}" for sid in sorted(pending) if sid in subtask_map
        )

        prompt = _HANDOFF_PROMPT.format(
            goal=goal,
            completed_list=completed_list,
            remaining_list=remaining_list,
            last_title=last_subtask.title,
            last_result=last_subtask.result or "Done",
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
            return self._parse_handoff(message, pending)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Handoff call failed (%s), picking next available", exc)
            next_id = self._pick_next_available(pending, completed, set(), subtask_map)
            return False, next_id, None

    @staticmethod
    def _parse_handoff(
        text: str, pending: set[str],
    ) -> tuple[bool, str | None, str | None]:
        """Parse handoff response into (done, next_id, handoff_prompt)."""
        try:
            fence_match = re.search(r"```(?:json)?\s*\n?(.*?)```", text, re.DOTALL)
            json_text = fence_match.group(1).strip() if fence_match else text
            data = json.loads(json_text)

            done = bool(data.get("done", False))
            if done:
                return True, None, None

            next_id = data.get("next_subtask_id")
            if next_id and str(next_id) in pending:
                handoff_prompt = data.get("handoff_prompt")
                return False, str(next_id), handoff_prompt
        except (json.JSONDecodeError, ValueError, AttributeError):
            pass

        # Fallback: pick first pending
        if pending:
            return False, sorted(pending)[0], None
        return True, None, None

    @staticmethod
    def _pick_start(
        plan: PlanResult,
        completed: set[str],
        failed: set[str],
    ) -> str | None:
        """Find the first unprocessed sub-task in execution order."""
        for sid in plan.execution_order:
            if sid not in completed and sid not in failed:
                return sid
        return None

    @staticmethod
    def _pick_next_available(
        pending: set[str],
        completed: set[str],
        failed: set[str],
        subtask_map: dict[str, Subtask],
    ) -> str | None:
        """Pick the first pending sub-task whose dependencies are met or failed."""
        for sid in sorted(pending):
            subtask = subtask_map.get(sid)
            if subtask is None:
                continue
            if all(dep in completed or dep in failed for dep in subtask.dependencies):
                return sid
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
