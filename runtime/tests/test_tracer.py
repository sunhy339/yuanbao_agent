from __future__ import annotations

from pathlib import Path
from typing import Any

from local_agent_runtime.observability.tracer import Span, Tracer
from local_agent_runtime.store.sqlite_store import SQLiteStore


def _make_store(tmp_path: Path) -> SQLiteStore:
    return SQLiteStore(str(tmp_path / "runtime.sqlite3"))


class TestSpanLifecycle:
    def test_start_span_creates_in_progress_span(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        try:
            tracer = Tracer(store)
            span = tracer.start_span("react_loop")

            assert span.trace_id
            assert span.span_id
            assert span.operation == "react_loop"
            assert span.status == "in_progress"
            assert span.started_at > 0
            assert span.finished_at is None
            assert span.parent_span_id is None
            assert span.attributes == {}
        finally:
            store.close()

    def test_end_span_updates_status_and_finished_at(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        try:
            tracer = Tracer(store)
            span = tracer.start_span("react_loop")
            ended = tracer.end_span(span.span_id, status="ok")

            assert ended.status == "ok"
            assert ended.finished_at is not None
            assert ended.finished_at >= ended.started_at
        finally:
            store.close()

    def test_end_span_with_error_status(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        try:
            tracer = Tracer(store)
            span = tracer.start_span("tool_call")
            ended = tracer.end_span(span.span_id, status="error")

            assert ended.status == "error"
            assert ended.finished_at is not None
        finally:
            store.close()

    def test_end_span_with_attributes(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        try:
            tracer = Tracer(store)
            span = tracer.start_span("tool_call")
            ended = tracer.end_span(span.span_id, status="ok", attributes={"toolName": "read_file"})

            assert ended.attributes == {"toolName": "read_file"}
        finally:
            store.close()

    def test_end_nonexistent_span_raises(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        try:
            tracer = Tracer(store)
            import pytest

            with pytest.raises(ValueError, match="Span not found"):
                tracer.end_span("span_nonexistent")
        finally:
            store.close()


class TestParentChild:
    def test_child_span_references_parent(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        try:
            tracer = Tracer(store)
            parent = tracer.start_span("react_loop")
            child = tracer.start_span(
                "llm_generate",
                trace_id=parent.trace_id,
                parent_span_id=parent.span_id,
            )

            assert child.trace_id == parent.trace_id
            assert child.parent_span_id == parent.span_id
        finally:
            store.close()

    def test_explicit_trace_id_is_reused(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        try:
            tracer = Tracer(store)
            first = tracer.start_span("op1")
            second = tracer.start_span("op2", trace_id=first.trace_id)

            assert second.trace_id == first.trace_id
            assert second.span_id != first.span_id
        finally:
            store.close()


class TestGetTrace:
    def test_get_trace_returns_spans_sorted_by_time(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        try:
            tracer = Tracer(store)
            first = tracer.start_span("op_a")
            second = tracer.start_span("op_b", trace_id=first.trace_id)

            spans = tracer.get_trace(first.trace_id)

            assert len(spans) == 2
            assert spans[0].span_id == first.span_id
            assert spans[1].span_id == second.span_id
        finally:
            store.close()

    def test_get_trace_empty_for_unknown(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        try:
            tracer = Tracer(store)
            assert tracer.get_trace("trace_nonexistent") == []
        finally:
            store.close()


class TestGetSpan:
    def test_get_span_returns_span(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        try:
            tracer = Tracer(store)
            span = tracer.start_span("react_loop", attributes={"goal": "test"})
            fetched = tracer.get_span(span.span_id)

            assert fetched is not None
            assert fetched.span_id == span.span_id
            assert fetched.attributes == {"goal": "test"}
        finally:
            store.close()

    def test_get_span_returns_none_for_unknown(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        try:
            tracer = Tracer(store)
            assert tracer.get_span("span_nonexistent") is None
        finally:
            store.close()


class TestAttributesSerialization:
    def test_complex_attributes_round_trip(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        try:
            tracer = Tracer(store)
            attrs = {"toolName": "run_command", "nested": {"key": "val"}, "count": 42}
            span = tracer.start_span("tool_call", attributes=attrs)
            tracer.end_span(span.span_id)

            fetched = tracer.get_span(span.span_id)
            assert fetched is not None
            assert fetched.attributes["toolName"] == "run_command"
            assert fetched.attributes["nested"]["key"] == "val"
            assert fetched.attributes["count"] == 42
        finally:
            store.close()
