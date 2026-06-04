from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


def _text(value: Any, *, limit: int = 240) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    return text[:limit]


def build_plan_preview_rows(request: Mapping[str, Any]) -> list[dict[str, str]]:
    """Return compact rows for any plan-like approval request."""
    rows = [
        {"label": "\u76ee\u6807", "value": _text(request.get("goal"), limit=220)},
        {"label": "\u6a21\u5f0f", "value": _text(request.get("orchestrationMode") or request.get("mode") or "plan", limit=80)},
        {"label": "\u5b50\u4efb\u52a1", "value": str(request.get("subtaskCount") or len(request.get("subtasks") or []))},
    ]
    execution_order = request.get("executionOrder") or request.get("execution_order")
    if isinstance(execution_order, Sequence) and not isinstance(execution_order, (str, bytes, bytearray)):
        ordered = [_text(item, limit=80) for item in execution_order[:12]]
        order_text = " -> ".join(item for item in ordered if item)
        if order_text:
            rows.append({"label": "\u6267\u884c\u987a\u5e8f", "value": order_text})
    return [row for row in rows if row["value"]]


def build_subtask_preview_sections(subtasks: Sequence[Any] | None) -> list[dict[str, Any]]:
    """Return generic preview sections for user-visible subtask lists."""
    items: list[dict[str, Any]] = []
    for index, raw_subtask in enumerate((subtasks or [])[:20]):
        if isinstance(raw_subtask, Mapping):
            subtask = raw_subtask
            item = {
                "id": _text(subtask.get("id") or subtask.get("subtaskId") or f"sub-{index}", limit=80),
                "title": _text(
                    subtask.get("title")
                    or subtask.get("subtaskTitle")
                    or subtask.get("summary")
                    or subtask.get("description"),
                    limit=240,
                ),
            }
            description = _text(subtask.get("description") or subtask.get("summary"), limit=500)
            dependencies = subtask.get("dependencies")
            meta = [
                _text(subtask.get("agentType") or subtask.get("agent_type") or subtask.get("role"), limit=80),
                (
                    "\u4f9d\u8d56 " + ", ".join(_text(dep, limit=80) for dep in dependencies[:10] if _text(dep, limit=80))
                    if isinstance(dependencies, Sequence) and not isinstance(dependencies, (str, bytes, bytearray)) and dependencies
                    else ""
                ),
            ]
            if description and description != item["title"]:
                item["description"] = description
            clean_meta = [part for part in meta if part]
            if clean_meta:
                item["meta"] = clean_meta
        else:
            item = {
                "id": f"sub-{index}",
                "title": _text(raw_subtask, limit=240),
            }
        if item.get("id") and item.get("title"):
            items.append(item)

    if not items:
        return []
    return [{"kind": "items", "title": f"\u5df2\u62c6\u5206 {len(items)} \u4e2a\u5b50\u4efb\u52a1", "items": items}]


def attach_plan_preview(request: dict[str, Any]) -> dict[str, Any]:
    """Attach previewRows/previewSections to a mutable plan approval request."""
    request["previewRows"] = build_plan_preview_rows(request)
    request["previewSections"] = build_subtask_preview_sections(
        request.get("subtasks") if isinstance(request.get("subtasks"), list) else []
    )
    return request
