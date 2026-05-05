"""Unified memory system entry point.

Provides ``remember()``, ``recall()`` and ``consolidate()`` as the single
facade for all memory operations.
"""

from __future__ import annotations

from .retriever import MemoryRetriever, extract_keywords
from .store import MemoryStore
from .types import MemoryEntry, MemoryKind


class MemoryManager:
    """High-level memory API: remember / recall / consolidate."""

    def __init__(self, store: MemoryStore, retriever: MemoryRetriever) -> None:
        self._store = store
        self._retriever = retriever

    # -- Public API --

    def remember(
        self,
        *,
        session_id: str | None = None,
        workspace_id: str | None = None,
        content: str,
        kind: MemoryKind = MemoryKind.WORKING,
        metadata: dict | None = None,
    ) -> MemoryEntry:
        """Store a memory entry with auto-extracted keywords."""
        keywords = extract_keywords(content)
        return self._store.create(
            kind=kind,
            content=content,
            session_id=session_id,
            workspace_id=workspace_id,
            keywords=keywords,
            metadata=metadata,
        )

    def recall(
        self,
        *,
        workspace_id: str | None = None,
        session_id: str | None = None,
        query: str,
        limit: int = 10,
    ) -> list[MemoryEntry]:
        """Three-path recall: recent + semantic search.

        1. **Recent**: most recently accessed memories for the workspace/session.
        2. **Semantic**: keyword similarity search against all memory entries.

        Results are deduplicated and returned in relevance order.
        """
        seen_ids: set[str] = set()
        results: list[MemoryEntry] = []

        # Path 1: semantic search (if workspace available)
        if workspace_id:
            scored = self._retriever.search(
                workspace_id=workspace_id,
                query=query,
                session_id=session_id,
                limit=limit,
            )
            for entry, _score in scored:
                if entry.id not in seen_ids:
                    seen_ids.add(entry.id)
                    results.append(entry)

        # Path 2: recent memories (fill remaining slots)
        if workspace_id or session_id:
            recent = self._store.query_recent(
                workspace_id=workspace_id or "",
                session_id=session_id or "",
                limit=limit,
            )
            for entry in recent:
                if entry.id not in seen_ids:
                    seen_ids.add(entry.id)
                    results.append(entry)

        return results[:limit]

    def consolidate(self, session_id: str) -> int:
        """Promote WORKING memories to SESSION after session ends.

        Returns the number of entries promoted.
        """
        working = self._store.query_working(session_id)
        if not working:
            return 0
        entry_ids = [entry.id for entry in working]
        return self._store.promote_batch(entry_ids, MemoryKind.SESSION)

    def forget_working(self, session_id: str) -> int:
        """Discard all WORKING memories for a session (e.g. on discard)."""
        return self._store.delete_by_session(session_id)

    def retrieve(self, entry_id: str) -> MemoryEntry | None:
        """Fetch a single entry by id."""
        return self._store.retrieve(entry_id)

    def delete(self, entry_id: str) -> bool:
        """Delete a memory entry."""
        return self._store.delete(entry_id)
