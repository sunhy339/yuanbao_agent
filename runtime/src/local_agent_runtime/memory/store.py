"""Memory persistence layer backed by SQLite."""

from __future__ import annotations

import json
from typing import Any

from ..store.sqlite_store import SQLiteStore
from .types import MemoryEntry, MemoryKind


class MemoryStore:
    """Four-tier memory storage: working / session / long-term / semantic."""

    def __init__(self, store: SQLiteStore) -> None:
        self._store = store

    # -- CRUD --

    def create(
        self,
        *,
        kind: MemoryKind,
        content: str,
        session_id: str | None = None,
        workspace_id: str | None = None,
        keywords: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> MemoryEntry:
        """Insert a new memory entry and return it."""
        entry_id = self._store.new_id("mem")
        now = self._store.now()
        kw_json = json.dumps(keywords or [], ensure_ascii=False)
        meta_json = json.dumps(metadata or {}, ensure_ascii=False)

        self._store._conn.execute(
            """
            INSERT INTO memory_entries
                (id, session_id, workspace_id, kind, content,
                 keywords, metadata, created_at, accessed_at, access_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
            """,
            (entry_id, session_id, workspace_id, kind.value, content,
             kw_json, meta_json, now, now),
        )
        self._store._conn.commit()
        return self.retrieve(entry_id)  # type: ignore[return-value]

    def retrieve(self, entry_id: str) -> MemoryEntry | None:
        """Fetch a single entry by id. Touches accessed_at."""
        row = self._store._conn.execute(
            "SELECT * FROM memory_entries WHERE id = ?",
            (entry_id,),
        ).fetchone()
        if row is None:
            return None
        # Touch access stats
        now = self._store.now()
        self._store._conn.execute(
            "UPDATE memory_entries SET accessed_at = ?, access_count = access_count + 1 "
            "WHERE id = ?",
            (now, entry_id),
        )
        self._store._conn.commit()
        return self._row_to_entry(row)

    def update(
        self,
        entry_id: str,
        *,
        content: str | None = None,
        keywords: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        kind: MemoryKind | None = None,
    ) -> MemoryEntry | None:
        """Update fields of an existing entry."""
        existing = self._store._conn.execute(
            "SELECT * FROM memory_entries WHERE id = ?",
            (entry_id,),
        ).fetchone()
        if existing is None:
            return None

        sets: list[str] = []
        params: list[Any] = []
        if content is not None:
            sets.append("content = ?")
            params.append(content)
        if keywords is not None:
            sets.append("keywords = ?")
            params.append(json.dumps(keywords, ensure_ascii=False))
        if metadata is not None:
            sets.append("metadata = ?")
            params.append(json.dumps(metadata, ensure_ascii=False))
        if kind is not None:
            sets.append("kind = ?")
            params.append(kind.value)

        if sets:
            now = self._store.now()
            sets.append("accessed_at = ?")
            params.append(now)
            params.append(entry_id)
            self._store._conn.execute(
                f"UPDATE memory_entries SET {', '.join(sets)} WHERE id = ?",
                params,
            )
            self._store._conn.commit()
        return self.retrieve(entry_id)

    def delete(self, entry_id: str) -> bool:
        """Delete an entry. Returns True if something was removed."""
        cursor = self._store._conn.execute(
            "DELETE FROM memory_entries WHERE id = ?",
            (entry_id,),
        )
        self._store._conn.commit()
        return cursor.rowcount > 0

    # -- Query by type --

    def query_working(self, session_id: str) -> list[MemoryEntry]:
        """All WORKING memories for a session."""
        return self._query(kind=MemoryKind.WORKING, session_id=session_id)

    def query_session(self, workspace_id: str) -> list[MemoryEntry]:
        """All SESSION memories for a workspace."""
        return self._query(kind=MemoryKind.SESSION, workspace_id=workspace_id)

    def query_long_term(self, workspace_id: str, *, limit: int = 50) -> list[MemoryEntry]:
        """LONG_TERM memories for a workspace, most recently accessed first."""
        rows = self._store._conn.execute(
            "SELECT * FROM memory_entries "
            "WHERE workspace_id = ? AND kind = ? "
            "ORDER BY accessed_at DESC LIMIT ?",
            (workspace_id, MemoryKind.LONG_TERM.value, limit),
        ).fetchall()
        return [self._row_to_entry(r) for r in rows]

    def query_semantic(self, workspace_id: str, *, limit: int = 50) -> list[MemoryEntry]:
        """All SEMANTIC memories for a workspace."""
        return self._query(kind=MemoryKind.SEMANTIC, workspace_id=workspace_id, limit=limit)

    def query_recent(
        self, workspace_id: str, session_id: str, *, limit: int = 10
    ) -> list[MemoryEntry]:
        """Recent memories across all kinds (workspace + session scoped)."""
        rows = self._store._conn.execute(
            "SELECT * FROM memory_entries "
            "WHERE (workspace_id = ? OR session_id = ?) "
            "ORDER BY accessed_at DESC LIMIT ?",
            (workspace_id, session_id, limit),
        ).fetchall()
        return [self._row_to_entry(r) for r in rows]

    def query_all(
        self,
        *,
        workspace_id: str | None = None,
        session_id: str | None = None,
        kind: MemoryKind | None = None,
        limit: int = 100,
    ) -> list[MemoryEntry]:
        """Flexible query with optional filters."""
        clauses: list[str] = []
        params: list[Any] = []
        if workspace_id is not None:
            clauses.append("workspace_id = ?")
            params.append(workspace_id)
        if session_id is not None:
            clauses.append("session_id = ?")
            params.append(session_id)
        if kind is not None:
            clauses.append("kind = ?")
            params.append(kind.value)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        rows = self._store._conn.execute(
            f"SELECT * FROM memory_entries {where} ORDER BY accessed_at DESC LIMIT ?",
            params,
        ).fetchall()
        return [self._row_to_entry(r) for r in rows]

    # -- Bulk operations --

    def delete_by_session(self, session_id: str) -> int:
        """Remove all WORKING memories for a session."""
        cursor = self._store._conn.execute(
            "DELETE FROM memory_entries WHERE session_id = ? AND kind = ?",
            (session_id, MemoryKind.WORKING.value),
        )
        self._store._conn.commit()
        return cursor.rowcount

    def promote(
        self,
        entry_id: str,
        to_kind: MemoryKind,
    ) -> MemoryEntry | None:
        """Change the kind of an entry (e.g. WORKING → SESSION)."""
        return self.update(entry_id, kind=to_kind)

    # -- internals --

    def _query(
        self,
        *,
        kind: MemoryKind,
        workspace_id: str | None = None,
        session_id: str | None = None,
        limit: int = 100,
    ) -> list[MemoryEntry]:
        clauses = ["kind = ?"]
        params: list[Any] = [kind.value]
        if workspace_id is not None:
            clauses.append("workspace_id = ?")
            params.append(workspace_id)
        if session_id is not None:
            clauses.append("session_id = ?")
            params.append(session_id)
        params.append(limit)
        rows = self._store._conn.execute(
            f"SELECT * FROM memory_entries WHERE {' AND '.join(clauses)} "
            "ORDER BY accessed_at DESC LIMIT ?",
            params,
        ).fetchall()
        return [self._row_to_entry(r) for r in rows]

    @staticmethod
    def _row_to_entry(row: Any) -> MemoryEntry:
        d = dict(row)
        return MemoryEntry(
            id=d["id"],
            kind=MemoryKind(d["kind"]),
            content=d["content"],
            created_at=d["created_at"],
            accessed_at=d["accessed_at"],
            access_count=d["access_count"],
            session_id=d.get("session_id"),
            workspace_id=d.get("workspace_id"),
            keywords=json.loads(d.get("keywords") or "[]"),
            metadata=json.loads(d.get("metadata") or "{}"),
        )
