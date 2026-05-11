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

    # Negation patterns that flip meaning when present in one text but not the other
    _NEGATION_PATTERNS: list[str] = [
        "don't ", "do not ", "never ", "not ", "avoid ", "no ",
    ]

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
        dedup: bool = False,
        dedup_threshold: float = 0.5,
    ) -> MemoryEntry:
        """Store a memory entry with auto-extracted keywords.

        When *dedup* is True, checks for similar existing entries and merges
        with the best match instead of creating a duplicate.  If the content
        conflicts with an existing entry (similar topic but opposite meaning),
        both entries are marked with ``conflictingIds`` in their metadata.
        """
        keywords = extract_keywords(content)

        if dedup and workspace_id:
            similar = self._retriever.search(
                workspace_id=workspace_id,
                query=content,
                session_id=session_id,
                limit=3,
                threshold=dedup_threshold,
            )
            if similar:
                best_entry, best_score = similar[0]
                # Detect conflict before merge
                if self._detect_conflict(content, best_entry.content):
                    self._mark_conflict(best_entry.id, content, metadata)
                # Merge: update content and metadata, keep higher confidence
                existing_meta = best_entry.metadata or {}
                new_meta = metadata or {}
                merged_meta = {**existing_meta, **new_meta}
                # Keep the higher confidence
                if "confidence" in existing_meta and "confidence" in new_meta:
                    merged_meta["confidence"] = max(
                        float(existing_meta["confidence"]),
                        float(new_meta["confidence"]),
                    )
                # Merge sourceTaskIds lists
                existing_ids = set(existing_meta.get("sourceTaskIds") or [])
                new_ids = new_meta.get("sourceTaskIds") or []
                merged_ids = list(existing_ids | set(new_ids))
                if merged_ids:
                    merged_meta["sourceTaskIds"] = merged_ids
                # Merge sourceMessageIds lists
                existing_msg_ids = set(existing_meta.get("sourceMessageIds") or [])
                new_msg_ids = new_meta.get("sourceMessageIds") or []
                merged_msg_ids = list(existing_msg_ids | set(new_msg_ids))
                if merged_msg_ids:
                    merged_meta["sourceMessageIds"] = merged_msg_ids
                return self._store.update(
                    best_entry.id,
                    content=content,
                    keywords=keywords,
                    metadata=merged_meta,
                )

        new_entry = self._store.create(
            kind=kind,
            content=content,
            session_id=session_id,
            workspace_id=workspace_id,
            keywords=keywords,
            metadata=metadata,
        )

        # Even when not merging, check for conflicts with existing entries
        if workspace_id:
            self._check_and_mark_conflicts(new_entry, workspace_id)

        return new_entry

    def recall(
        self,
        *,
        workspace_id: str | None = None,
        session_id: str | None = None,
        query: str,
        limit: int = 10,
    ) -> list[MemoryEntry]:
        """Recall relevant memories with workspace-first semantic search.

        1. **Semantic**: keyword search across the workspace, with current
           session memories boosted but not used as a hard filter.
        2. **Recent**: most recently accessed memories for the workspace/session.

        Results are deduplicated, boosted by pinned status and confidence,
        then returned in relevance order.
        """
        scored = self.recall_with_scores(
            workspace_id=workspace_id,
            session_id=session_id,
            query=query,
            limit=limit,
        )
        return [entry for entry, _score in scored]

    def recall_with_scores(
        self,
        *,
        workspace_id: str | None = None,
        session_id: str | None = None,
        query: str,
        limit: int = 10,
    ) -> list[tuple[MemoryEntry, float]]:
        """Recall memories with adjusted relevance scores.

        Returns ``(entry, adjusted_score)`` pairs sorted by score descending.
        Adjustments:
        - pinned memories get +0.3 boost
        - low confidence (<0.4) memories get -0.2 penalty
        - failed-task memories (open_issue with confidence <0.5) get -0.1 penalty
        """
        seen_ids: set[str] = set()
        results: list[tuple[MemoryEntry, float]] = []

        # Path 1: semantic search (if workspace available)
        if workspace_id:
            scored = self._retriever.search(
                workspace_id=workspace_id,
                query=query,
                session_id=session_id,
                limit=limit,
            )
            for entry, score in scored:
                if entry.id not in seen_ids:
                    seen_ids.add(entry.id)
                    results.append((entry, self._adjust_score(entry, score)))

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
                    # Recent path gets a base score that decays with position
                    base_score = 0.3
                    results.append((entry, self._adjust_score(entry, base_score)))

        # Sort by adjusted score descending
        results.sort(key=lambda x: x[1], reverse=True)
        return results[:limit]

    @staticmethod
    def _adjust_score(entry: MemoryEntry, raw_score: float) -> float:
        """Apply confidence and pinned adjustments to a recall score."""
        meta = entry.metadata or {}
        score = raw_score

        # Pinned boost
        if meta.get("pinned"):
            score += 0.3

        # Low confidence penalty
        confidence = float(meta.get("confidence", 0.7))
        if confidence < 0.4:
            score -= 0.2

        # Failed-task memory penalty
        category = meta.get("category", "")
        if category == "open_issue" and confidence < 0.5:
            score -= 0.1

        return max(score, 0.0)

    @classmethod
    def _detect_conflict(cls, text_a: str, text_b: str) -> bool:
        """Detect if two texts express conflicting preferences.

        Returns True when one text contains negation patterns while the
        other does not, suggesting opposite meaning on the same topic.
        """
        a_lower = text_a.lower()
        b_lower = text_b.lower()
        a_has_negation = any(p in a_lower for p in cls._NEGATION_PATTERNS)
        b_has_negation = any(p in b_lower for p in cls._NEGATION_PATTERNS)
        return a_has_negation != b_has_negation

    def _mark_conflict(
        self,
        existing_id: str,
        new_content: str,
        new_metadata: dict | None,
    ) -> None:
        """Add conflictingIds to the existing entry's metadata."""
        existing = self._store.retrieve(existing_id, touch=False)
        if existing is None:
            return
        meta = dict(existing.metadata or {})
        conflict_ids: list[str] = list(meta.get("conflictingIds") or [])
        # Store a placeholder; the new entry's ID is not yet known
        meta["conflictingIds"] = conflict_ids
        meta["hasConflict"] = True
        self._store.update(existing_id, metadata=meta)

    def _check_and_mark_conflicts(
        self,
        new_entry: MemoryEntry,
        workspace_id: str,
    ) -> None:
        """Search for conflicting existing entries and mark both sides."""
        similar = self._retriever.search(
            workspace_id=workspace_id,
            query=new_entry.content,
            limit=5,
            threshold=0.3,
        )
        for existing, score in similar:
            if existing.id == new_entry.id:
                continue
            if self._detect_conflict(new_entry.content, existing.content):
                # Mark existing entry
                existing_meta = dict(existing.metadata or {})
                existing_conflicts = list(existing_meta.get("conflictingIds") or [])
                if new_entry.id not in existing_conflicts:
                    existing_conflicts.append(new_entry.id)
                existing_meta["conflictingIds"] = existing_conflicts
                existing_meta["hasConflict"] = True
                self._store.update(existing.id, metadata=existing_meta)

                # Mark new entry
                new_meta = dict(new_entry.metadata or {})
                new_conflicts = list(new_meta.get("conflictingIds") or [])
                if existing.id not in new_conflicts:
                    new_conflicts.append(existing.id)
                new_meta["conflictingIds"] = new_conflicts
                new_meta["hasConflict"] = True
                self._store.update(new_entry.id, metadata=new_meta)

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
