from __future__ import annotations

from typing import Any

from .types import PlanResult, Subtask, normalize_subtask_agent_type


MAX_PREFLIGHT_SPLIT_SUBTASKS = 10


def extract_provider_preflight_subtasks(payload: dict[str, Any]) -> list[Any] | None:
    """Return advisor-supplied split subtasks from supported proposal shapes."""
    raw_split = payload.get("splitRecommendation")
    if isinstance(raw_split, dict):
        raw_subtasks = raw_split.get("subtasks")
    elif isinstance(raw_split, list):
        raw_subtasks = raw_split
    else:
        raw_subtasks = None
    if raw_subtasks is None:
        raw_subtasks = payload.get("subtasks")
    return raw_subtasks if isinstance(raw_subtasks, list) else None


def build_provider_preflight_split_plan_payload(*, advice: Any | None) -> dict[str, Any] | None:
    if advice is None or not bool(getattr(advice, "accepted", False)):
        return None
    payload = getattr(advice, "payload", {}) or {}
    if not isinstance(payload, dict) or payload.get("action") != "propose_split":
        return None
    plan = build_provider_preflight_plan_from_payload(payload)
    if plan is None:
        return None
    return serialize_provider_preflight_plan(
        plan,
        reason=payload.get("reason"),
        risk_level=payload.get("riskLevel"),
    )


def build_provider_preflight_plan_from_payload(payload: Any) -> PlanResult | None:
    if not isinstance(payload, dict):
        return None
    raw_subtasks = extract_provider_preflight_subtasks(payload)
    if raw_subtasks is None:
        return None
    return build_provider_preflight_plan_from_subtasks(raw_subtasks)


def build_provider_preflight_plan_from_subtasks(raw_subtasks: list[Any]) -> PlanResult | None:
    if len(raw_subtasks) < 2 or len(raw_subtasks) > MAX_PREFLIGHT_SPLIT_SUBTASKS:
        return None

    subtasks: list[Subtask] = []
    seen_ids: set[str] = set()
    for index, item in enumerate(raw_subtasks):
        if not isinstance(item, dict):
            return None
        raw_id = item.get("id") or item.get("taskId") or item.get("task_id")
        subtask_id = str(raw_id or "").strip()
        if not subtask_id or subtask_id in seen_ids:
            return None
        title = str(item.get("title") or "").strip()
        description = str(item.get("description") or item.get("prompt") or item.get("instructions") or "").strip()
        if not title or not description:
            return None
        raw_deps = item.get("dependencies") or []
        if not isinstance(raw_deps, list):
            return None
        dependencies = [str(dep).strip() for dep in raw_deps if str(dep).strip()]
        subtasks.append(
            Subtask(
                id=subtask_id,
                title=title[:80],
                description=description,
                dependencies=dependencies,
                agent_type=normalize_subtask_agent_type(item.get("agentType") or item.get("agent_type")),
            )
        )
        seen_ids.add(subtask_id)

    valid_ids = {subtask.id for subtask in subtasks}
    for subtask in subtasks:
        if any(dep not in valid_ids or dep == subtask.id for dep in subtask.dependencies):
            return None

    dag = {subtask.id: list(subtask.dependencies) for subtask in subtasks}
    execution_order = _topological_order(dag, [subtask.id for subtask in subtasks])
    if len(execution_order) != len(subtasks):
        return None
    return PlanResult(subtasks=subtasks, dag=dag, execution_order=execution_order)


def serialize_provider_preflight_plan(
    plan: PlanResult,
    *,
    reason: Any | None = None,
    risk_level: Any | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "subtasks": [
            {
                "id": subtask.id,
                "title": subtask.title,
                "description": subtask.description,
                "dependencies": list(subtask.dependencies),
                "agentType": normalize_subtask_agent_type(subtask.agent_type),
                "status": subtask.status,
                "result": subtask.result,
            }
            for subtask in plan.subtasks
        ],
        "dag": plan.dag,
        "execution_order": plan.execution_order,
        "source": "provider_preflight",
    }
    if isinstance(reason, str) and reason.strip():
        payload["reason"] = reason.strip()
    if isinstance(risk_level, str) and risk_level.strip():
        payload["riskLevel"] = risk_level.strip()
    return payload


def _topological_order(dag: dict[str, list[str]], all_ids: list[str]) -> list[str]:
    in_degree = {node: 0 for node in all_ids}
    dependents = {node: [] for node in all_ids}
    for node, deps in dag.items():
        for dep in deps:
            if dep not in in_degree:
                return []
            dependents[dep].append(node)
            in_degree[node] += 1
    queue = [node for node in all_ids if in_degree[node] == 0]
    order: list[str] = []
    while queue:
        node = queue.pop(0)
        order.append(node)
        for dependent in dependents.get(node, []):
            in_degree[dependent] -= 1
            if in_degree[dependent] == 0:
                queue.append(dependent)
    return order
