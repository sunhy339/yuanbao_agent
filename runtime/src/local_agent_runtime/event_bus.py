from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .models import RuntimeEvent

EventSink = Callable[[RuntimeEvent], None]


class EventBus:
    """In-memory event fan-out.

    Tauri can later subscribe to this bus and forward events to React.
    """

    def __init__(self) -> None:
        self._subscribers: list[EventSink] = []

    def subscribe(self, sink: EventSink) -> None:
        self._subscribers.append(sink)

    def publish(self, event: RuntimeEvent) -> None:
        for sink in list(self._subscribers):
            try:
                sink(event)
            except Exception:
                continue

    def as_payload(self, event: RuntimeEvent) -> dict[str, Any]:
        return {
            "eventId": event.event_id,
            "sessionId": event.session_id,
            "taskId": event.task_id,
            "type": event.type,
            "ts": event.ts,
            "payload": event.payload,
        }
