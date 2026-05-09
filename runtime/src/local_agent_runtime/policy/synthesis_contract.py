"""P12: Trace Summarizer and Parent Synthesis Contracts.

Defines the input/output schemas for:
- Trace summarizer: condenses raw trace events into a concise summary
- Parent synthesis: combines child task outcomes into a final result

These contracts ensure summaries preserve evidence links and synthesis
does not claim unverified artifacts as complete.
"""

from __future__ import annotations

from typing import Any


# ---------------------------------------------------------------------------
# Trace Summarizer Input Contract
# ---------------------------------------------------------------------------

REQUIRED_TRACE_SUMMARY_INPUT_FIELDS = frozenset({
    "parentTaskId",
    "sessionId",
    "eventRange",
})


def validate_trace_summary_input(payload: dict[str, Any]) -> list[str]:
    """Validate trace summarizer input contract.

    Required fields:
    - parentTaskId: the parent generation task id
    - sessionId: the session id
    - eventRange: { "afterSeq": int, "beforeSeq": int } bounding the events
    """
    reasons: list[str] = []

    for field in sorted(REQUIRED_TRACE_SUMMARY_INPUT_FIELDS):
        if field not in payload or payload[field] is None:
            reasons.append(f"Trace summary input missing required field: {field!r}")

    # Validate eventRange structure
    event_range = payload.get("eventRange")
    if isinstance(event_range, dict):
        if "afterSeq" not in event_range:
            reasons.append("eventRange missing 'afterSeq'")
        if "beforeSeq" not in event_range:
            reasons.append("eventRange missing 'beforeSeq'")
        after = event_range.get("afterSeq")
        before = event_range.get("beforeSeq")
        if isinstance(after, int) and isinstance(before, int) and after > before:
            reasons.append("eventRange afterSeq must be <= beforeSeq")

    # Optional field validation
    child_task_ids = payload.get("childTaskIds")
    if child_task_ids is not None and not isinstance(child_task_ids, list):
        reasons.append("childTaskIds must be a list")

    return reasons


# ---------------------------------------------------------------------------
# Trace Summarizer Output Contract
# ---------------------------------------------------------------------------

REQUIRED_TRACE_SUMMARY_OUTPUT_FIELDS = frozenset({
    "parentTaskId",
    "eventRange",
    "summary",
})


def validate_trace_summary_output(payload: dict[str, Any]) -> list[str]:
    """Validate trace summarizer output contract.

    Required fields:
    - parentTaskId: links back to the parent task
    - eventRange: the event sequence range covered
    - summary: structured summary text

    Optional fields:
    - failures: list of failure descriptions
    - retries: list of retry attempt descriptions
    - generatedArtifacts: list of artifact references
    - reviewDecisions: list of review outcome descriptions
    """
    reasons: list[str] = []

    for field in sorted(REQUIRED_TRACE_SUMMARY_OUTPUT_FIELDS):
        if field not in payload or payload[field] is None:
            reasons.append(f"Trace summary output missing required field: {field!r}")

    # Summary must be a non-empty string
    summary = payload.get("summary")
    if summary is not None and not isinstance(summary, str):
        reasons.append("summary must be a string")

    # Optional lists must be lists
    for optional_list_field in ("failures", "retries", "generatedArtifacts", "reviewDecisions"):
        value = payload.get(optional_list_field)
        if value is not None and not isinstance(value, list):
            reasons.append(f"{optional_list_field} must be a list")

    return reasons


# ---------------------------------------------------------------------------
# Parent Synthesis Input Contract
# ---------------------------------------------------------------------------

REQUIRED_SYNTHESIS_INPUT_FIELDS = frozenset({
    "parentTaskId",
    "sessionId",
    "childOutcomes",
})


def validate_synthesis_input(payload: dict[str, Any]) -> list[str]:
    """Validate parent synthesis input contract.

    Required fields:
    - parentTaskId: the parent generation task id
    - sessionId: the session id
    - childOutcomes: list of child task outcome summaries
    """
    reasons: list[str] = []

    for field in sorted(REQUIRED_SYNTHESIS_INPUT_FIELDS):
        if field not in payload or payload[field] is None:
            reasons.append(f"Synthesis input missing required field: {field!r}")

    # Validate childOutcomes is a list
    outcomes = payload.get("childOutcomes")
    if outcomes is not None:
        if not isinstance(outcomes, list):
            reasons.append("childOutcomes must be a list")
        elif len(outcomes) == 0:
            reasons.append("childOutcomes must be non-empty")
        else:
            for i, outcome in enumerate(outcomes):
                if not isinstance(outcome, dict):
                    reasons.append(f"childOutcomes[{i}] must be a dict")
                elif "taskId" not in outcome:
                    reasons.append(f"childOutcomes[{i}] missing 'taskId'")
                elif "status" not in outcome:
                    reasons.append(f"childOutcomes[{i}] missing 'status'")

    return reasons


# ---------------------------------------------------------------------------
# Parent Synthesis Output Contract
# ---------------------------------------------------------------------------

REQUIRED_SYNTHESIS_OUTPUT_FIELDS = frozenset({
    "parentTaskId",
    "completedWork",
    "failedWork",
    "skippedWork",
})


def validate_synthesis_output(payload: dict[str, Any]) -> list[str]:
    """Validate parent synthesis output contract.

    Required fields:
    - parentTaskId: links back to the parent task
    - completedWork: list of verified completed work items
    - failedWork: list of failed work items
    - skippedWork: list of skipped work items

    Ensures:
    - No unverified artifacts are claimed as complete
    - Each completed work item has artifactIds that are verified
    """
    reasons: list[str] = []

    for field in sorted(REQUIRED_SYNTHESIS_OUTPUT_FIELDS):
        if field not in payload or payload[field] is None:
            reasons.append(f"Synthesis output missing required field: {field!r}")

    # Validate completedWork items
    completed = payload.get("completedWork")
    if isinstance(completed, list):
        for i, item in enumerate(completed):
            if not isinstance(item, dict):
                reasons.append(f"completedWork[{i}] must be a dict")
                continue
            # Claimed artifacts must be verified
            artifact_ids = item.get("artifactIds", [])
            verified = item.get("verifiedArtifactIds", [])
            if isinstance(artifact_ids, list) and isinstance(verified, list):
                claimed_set = set(artifact_ids)
                verified_set = set(verified)
                unverified = claimed_set - verified_set
                if unverified:
                    reasons.append(
                        f"completedWork[{i}]: unverified artifacts claimed as complete: "
                        f"{sorted(unverified)}"
                    )

    # Validate failedWork and skippedWork are lists
    for list_field in ("failedWork", "skippedWork"):
        value = payload.get(list_field)
        if value is not None and not isinstance(value, list):
            reasons.append(f"{list_field} must be a list")

    return reasons
