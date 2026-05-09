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
    parent_task: dict[str, Any] = {}
    if session_id is None:
        task_result = store.get_task({"taskId": parent_task_id})
        parent_task = task_result.get("task", {})
        session_id = parent_task.get("sessionId", "")
    else:
        try:
            parent_task = store.get_task({"taskId": parent_task_id}).get("task", {})
        except Exception:
            pass

    # P0.6: Derive planning mode from parent task routing
    parent_routing = parent_task.get("routing") or {}
    planning_mode = parent_routing.get("planningMode", "rule_fallback")

    # Gather child collaboration tasks
    collab_result = store.list_collaboration_tasks({
        "parentTaskId": parent_task_id,
    })
    children = collab_result.get("tasks", [])

    # Gather artifacts early so child entries can reference them
    artifacts_result = store.list_artifacts({
        "parentTaskId": parent_task_id,
    })
    artifacts = artifacts_result.get("artifacts", [])

    child_reports: list[dict[str, Any]] = []
    status_counts: dict[str, int] = {}
    for child in children:
        st = child.get("status", "unknown")
        status_counts[st] = status_counts.get(st, 0) + 1
        child_id = child["id"]
        report_entry: dict[str, Any] = {
            "taskId": child_id,
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

        # P2 extended: execution mode and attempt count
        meta = child.get("metadata") or {}
        report_entry["executionMode"] = meta.get("executionMode", "default")
        report_entry["attemptCount"] = meta.get("attemptCount", 1)

        # P7: Dynamic profile name from metadata
        profile = meta.get("profile")
        if isinstance(profile, dict):
            report_entry["profileName"] = profile.get("name")
            report_entry["profileBaseType"] = profile.get("baseType")

        # P2 extended: structured error fields for failures
        if st == "failed":
            err = child.get("error") or {}
            report_entry["errorCode"] = err.get("code") if isinstance(err, dict) else None
            report_entry["errorMessage"] = err.get("message") if isinstance(err, dict) else str(err) if err else None
            report_entry["retryable"] = err.get("retryable", False) if isinstance(err, dict) else False

        # P2 extended: trace event counts by type and visibility
        try:
            trace_result = store.list_trace_events({"taskId": child_id, "limit": 5000})
            trace_events = trace_result.get("traceEvents", [])
            event_type_counts: dict[str, int] = {}
            event_visibility_counts: dict[str, int] = {}
            for evt in trace_events:
                et = evt.get("type", "unknown")
                event_type_counts[et] = event_type_counts.get(et, 0) + 1
                ev = evt.get("visibility", "chat")
                event_visibility_counts[ev] = event_visibility_counts.get(ev, 0) + 1
            report_entry["traceEventCounts"] = event_type_counts
            report_entry["traceVisibilityCounts"] = event_visibility_counts
        except Exception:
            pass

        # P2 extended: message IDs and artifact IDs for the child task
        try:
            msgs_result = store.list_messages_by_task(child_id)
            report_entry["messageIds"] = [m["id"] for m in msgs_result]
        except Exception:
            report_entry["messageIds"] = []

        child_artifacts = [
            a for a in artifacts
            if a.get("producerTaskId") == child_id
        ]
        report_entry["artifactIds"] = [a["id"] for a in child_artifacts]

        child_reports.append(report_entry)

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

    # P8: DAG dependency and execution order from persisted DAG plan
    report: dict[str, Any] = {
        "parentTaskId": parent_task_id,
        "sessionId": session_id,
        "planningMode": planning_mode,
        "childTasks": child_reports,
        "artifacts": artifact_summaries,
        "counts": {
            "children": len(children),
            "childrenByStatus": status_counts,
            "artifacts": len(artifacts),
            "artifactsByKind": artifact_kind_counts,
        },
    }

    dag_state = store.get_pending_dag_state(parent_task_id)
    if dag_state is not None:
        plan_data = dag_state.get("plan")
        if isinstance(plan_data, dict):
            execution_order = plan_data.get("execution_order")
            if isinstance(execution_order, list):
                report["executionOrder"] = execution_order

            subtasks = plan_data.get("subtasks")
            if isinstance(subtasks, list):
                dependency_order: list[dict[str, Any]] = []
                for st in subtasks:
                    if isinstance(st, dict):
                        dependency_order.append({
                            "id": st.get("id"),
                            "title": st.get("title"),
                            "dependencies": st.get("dependencies", []),
                        })
                if dependency_order:
                    report["dependencyOrder"] = dependency_order

    return report
