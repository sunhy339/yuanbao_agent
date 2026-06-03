"""Lightweight span-based tracer for hierarchical execution tracing."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Span:
    """A single span within a trace."""

    trace_id: str
    span_id: str
    parent_span_id: str | None
    operation: str
    started_at: int
    finished_at: int | None = None
    status: str = "in_progress"
    attributes: dict[str, Any] = field(default_factory=dict)


def _row_to_span(row: dict[str, Any]) -> Span:
    attrs = row.get("attributes")
    if isinstance(attrs, str):
        attrs = json.loads(attrs)
    return Span(
        trace_id=row["trace_id"],
        span_id=row["span_id"],
        parent_span_id=row.get("parent_span_id"),
        operation=row["operation"],
        started_at=row["started_at"],
        finished_at=row.get("finished_at"),
        status=row.get("status", "in_progress"),
        attributes=attrs if isinstance(attrs, dict) else {},
    )


class Tracer:
    """Lightweight hierarchical tracer backed by SQLiteStore."""

    def __init__(self, store: Any) -> None:
        self._store = store

    def start_span(
        self,
        operation: str,
        trace_id: str | None = None,
        parent_span_id: str | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> Span:
        tid = trace_id or self._store.new_id("trace")
        sid = self._store.new_id("span")
        now = self._store.now()
        attrs = attributes or {}

        with self._store._conn._lock:
            self._store._conn.execute(
                """
                INSERT INTO trace_spans
                    (trace_id, span_id, parent_span_id, operation, started_at, status, attributes)
                VALUES (?, ?, ?, ?, ?, 'in_progress', ?)
                """,
                (
                    tid,
                    sid,
                    parent_span_id,
                    operation,
                    now,
                    json.dumps(attrs, ensure_ascii=False, sort_keys=True),
                ),
            )
            self._store._conn.commit()

        return Span(
            trace_id=tid,
            span_id=sid,
            parent_span_id=parent_span_id,
            operation=operation,
            started_at=now,
            status="in_progress",
            attributes=attrs,
        )

    def end_span(
        self,
        span_id: str,
        status: str = "ok",
        attributes: dict[str, Any] | None = None,
    ) -> Span:
        now = self._store.now()
        with self._store._conn._lock:
            if attributes:
                attrs_json = json.dumps(attributes, ensure_ascii=False, sort_keys=True)
                self._store._conn.execute(
                    """
                    UPDATE trace_spans
                    SET finished_at = ?, status = ?, attributes = ?
                    WHERE span_id = ?
                    """,
                    (now, status, attrs_json, span_id),
                )
            else:
                self._store._conn.execute(
                    """
                    UPDATE trace_spans
                    SET finished_at = ?, status = ?
                    WHERE span_id = ?
                    """,
                    (now, status, span_id),
                )
            self._store._conn.commit()

            row = self._store._conn.execute(
                "SELECT * FROM trace_spans WHERE span_id = ?",
                (span_id,),
            ).fetchone()
        if row is None:
            raise ValueError(f"Span not found: {span_id}")
        return _row_to_span(dict(row))

    def get_trace(self, trace_id: str) -> list[Span]:
        rows = self._store._conn.execute(
            "SELECT * FROM trace_spans WHERE trace_id = ? ORDER BY started_at",
            (trace_id,),
        ).fetchall()
        return [_row_to_span(dict(r)) for r in rows]

    def get_span(self, span_id: str) -> Span | None:
        row = self._store._conn.execute(
            "SELECT * FROM trace_spans WHERE span_id = ?",
            (span_id,),
        ).fetchone()
        if row is None:
            return None
        return _row_to_span(dict(row))
