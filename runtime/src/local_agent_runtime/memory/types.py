"""Memory system type definitions."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class MemoryKind(str, Enum):
    """Classification of memory entries."""

    WORKING = "working"       # current-session temporary
    SESSION = "session"       # cross-session, workspace-scoped
    LONG_TERM = "long_term"  # persistent, workspace-scoped
    SEMANTIC = "semantic"     # keyword-indexed semantic memory


@dataclass(slots=True)
class MemoryEntry:
    """A single memory record."""

    id: str
    kind: MemoryKind
    content: str
    created_at: int
    accessed_at: int
    access_count: int = 0
    session_id: str | None = None
    workspace_id: str | None = None
    keywords: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
