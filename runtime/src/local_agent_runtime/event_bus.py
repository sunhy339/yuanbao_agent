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
                persisted = sink(event)
                if isinstance(persisted, dict):
                    persisted_id = persisted.get("id")
                    persisted_seq = persisted.get("sequence")
                    if isinstance(persisted_id, str) and persisted_id:
                        event.event_id = persisted_id
                    if isinstance(persisted_seq, (int, float)) and not isinstance(persisted_seq, bool):
                        event.seq = int(persisted_seq)
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
        yuanbao = None if _suppresses_realtime_flat_message(event) else to_yuanbao_server_message(event)
        if yuanbao is not None:
            payload["yuanbao"] = yuanbao
        return payload


def _suppresses_realtime_flat_message(event: RuntimeEvent) -> bool:
    if event.visibility == "trace":
        return True
    event_payload = event.payload if isinstance(event.payload, dict) else {}
    if event.type == "message.completed" and event_payload.get("_chatCompat") is True:
        return True
    bridge = event_payload.get("_bridge")
    return isinstance(bridge, dict) and bridge.get("suppressRealtimeFlat") is True
