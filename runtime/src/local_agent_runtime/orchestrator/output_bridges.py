from __future__ import annotations

from typing import Any


def internal_completion_gate_bridge() -> dict[str, Any]:
    return {
        "internal": True,
        "kind": "completion_review",
        "suppressRealtimeFlat": True,
        "suppressChatReplay": True,
    }
