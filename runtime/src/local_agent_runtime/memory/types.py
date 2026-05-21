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


class MemoryCategory(str, Enum):
    """Semantic category of a memory entry."""

    USER_PREFERENCE = "user_preference"
    PROJECT_CONVENTION = "project_convention"
    WORKSPACE_FACT = "workspace_fact"
    TASK_LEARNING = "task_learning"
    DECISION = "decision"
    OPEN_ISSUE = "open_issue"
    TOOLING = "tooling"
    IMPLEMENTATION_NOTE = "implementation_note"
    RUNTIME_INVARIANT = "runtime_invariant"
    FAILURE_RECOVERY_PATTERN = "failure_recovery_pattern"
    VERIFIED_CAPABILITY = "verified_capability"


class MemoryScope(str, Enum):
    """Scope of a memory entry."""

    SESSION = "session"
    WORKSPACE = "workspace"
    USER = "user"
    GLOBAL = "global"


class MemorySource(str, Enum):
    """Origin of a memory entry."""

    TASK_RESULT = "task_result"
    USER_MESSAGE = "user_message"
    ASSISTANT_SUMMARY = "assistant_summary"
    MANUAL = "manual"
    SUPPLEMENT = "supplement"


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
