from __future__ import annotations

from collections.abc import Callable
import threading
from typing import Any

from .models import RuntimeEvent
from .yuanbao_event_adapter import to_yuanbao_server_message

EventSink = Callable[[RuntimeEvent], None]


class EventBus:
    """In-memory event fan-out.

    Tauri can later subscribe to this bus and forward events to React.
    """

    def __init__(self) -> None:
        self._subscribers: list[EventSink] = []
        self._seq_counter: int = 0
        self._last_ts: int = 0
        self._lock = threading.RLock()

    def subscribe(self, sink: EventSink) -> None:
        with self._lock:
            self._subscribers.append(sink)

    def publish(self, event: RuntimeEvent) -> None:
        with self._lock:
            self._seq_counter += 1
            event.seq = self._seq_counter
            if event.ts <= self._last_ts:
                event.ts = self._last_ts + 1
            self._last_ts = event.ts
            subscribers = list(self._subscribers)
        for sink in subscribers:
            try:
                sink(event)
            except Exception:
                continue

    def as_payload(self, event: RuntimeEvent) -> dict[str, Any]:
        payload = {
            "eventId": event.event_id,
            "seq": event.seq,
            "sessionId": event.session_id,
            "taskId": event.task_id,
            "type": event.type,
            "ts": event.ts,
            "payload": event.payload,
            "visibility": event.visibility,
        }
        yuanbao = to_yuanbao_server_message(event)
        if yuanbao is not None:
            payload["yuanbao"] = yuanbao
            payload["hahaCc"] = yuanbao
        return payload
