from __future__ import annotations

from typing import Any

from .models import RuntimeEvent
from .yuanbao_event_adapter import normalize_yuanbao_usage, to_yuanbao_server_message


def to_haha_cc_server_message(event: RuntimeEvent) -> dict[str, Any] | None:
    """Return the legacy haha-cc-compatible alias of a Yuanbao ServerMessage."""

    return to_yuanbao_server_message(event)


def normalize_haha_cc_usage(usage: Any) -> dict[str, int]:
    """Normalize token usage using the Yuanbao-compatible ServerMessage contract."""

    return normalize_yuanbao_usage(usage)


__all__ = [
    "normalize_haha_cc_usage",
    "normalize_yuanbao_usage",
    "to_haha_cc_server_message",
    "to_yuanbao_server_message",
]
