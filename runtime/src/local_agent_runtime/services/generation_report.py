"""Build a generation report for a parent task from durable state.

P2 of subagent-generation-todolist: Dispatch Summary and Report.
"""

from __future__ import annotations

from typing import Any

from ..store.sqlite_store import SQLiteStore


def build_generation_report(
    store: SQLiteStore,
    *,
    parent_task_id: str,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Build a generation report summarizing child task execution.

    Returns a structured report with:
    - parent task id and session id
    - child task summaries (id, title, status, worker, timing, result/error)
    - artifact summaries (id, kind, status, producer)
    - aggregate counts
    """
    # Resolve session_id from parent task if not given
    if session_id is None:
        task_result = store.get_task({"taskId": parent_task_id})
        task = task_result.get("task", {})
        session_id = task.get("sessionId", "")

    # Gather child collaboration tasks
    collab_result = store.list_collaboration_tasks({
        "parentTaskId": parent_task_id,
    })
    children = collab_result.get("tasks", [])

    child_reports: list[dict[str, Any]] = []
    status_counts: dict[str, int] = {}
    for child in children:
        st = child.get("status", "unknown")
        status_counts[st] = status_counts.get(st, 0) + 1
        report_entry: dict[str, Any] = {
            "taskId": child["id"],
            "title": child.get("title"),
            "status": st,
            "priority": child.get("priority"),
            "workerId": child.get("assignedWorkerId"),
            "dependencies": child.get("dependencies", []),
            "claimedAt": child.get("claimedAt"),
            "completedAt": child.get("completedAt"),
            "result": child.get("result"),
            "error": child.get("error"),
        }
        # Compute duration if possible
        claimed = child.get("claimedAt")
        completed = child.get("completedAt")
        if claimed and completed:
            report_entry["durationMs"] = completed - claimed
        child_reports.append(report_entry)

    # Gather artifacts
    artifacts_result = store.list_artifacts({
        "parentTaskId": parent_task_id,
    })
    artifacts = artifacts_result.get("artifacts", [])

    artifact_summaries: list[dict[str, Any]] = []
    artifact_kind_counts: dict[str, int] = {}
    for art in artifacts:
        ak = art.get("kind", "unknown")
        artifact_kind_counts[ak] = artifact_kind_counts.get(ak, 0) + 1
        artifact_summaries.append({
            "id": art["id"],
            "kind": ak,
            "status": art.get("status"),
            "producerTaskId": art.get("producerTaskId"),
            "title": art.get("title"),
        })

    return {
        "parentTaskId": parent_task_id,
        "sessionId": session_id,
        "childTasks": child_reports,
        "artifacts": artifact_summaries,
        "counts": {
            "children": len(children),
            "childrenByStatus": status_counts,
            "artifacts": len(artifacts),
            "artifactsByKind": artifact_kind_counts,
        },
    }
