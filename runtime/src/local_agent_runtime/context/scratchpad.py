"""Per-session key-value scratchpad for intermediate reasoning state."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..store.sqlite_store import SQLiteStore


@dataclass(slots=True)
class ScratchpadEntry:
    """A single scratchpad entry."""

    id: str
    session_id: str
    key: str
    value: str
    created_at: int
    updated_at: int


class Scratchpad:
    """Cross-turn persistent key-value store scoped to a session.

    The scratchpad lets the agent persist intermediate reasoning results
    (partial plans, hypothesis lists, deduced facts) across ReAct loop
    iterations without consuming context-window space until explicitly read.
    """

    def __init__(self, store: SQLiteStore) -> None:
        self._store = store

    # -- CRUD --

    def write(self, session_id: str, key: str, value: str) -> ScratchpadEntry:
        """Upsert a scratchpad entry.  Returns the persisted entry."""
        now = self._store.now()
        entry_id = self._store.new_id("sp")
        self._store._conn.execute(
            """
            INSERT INTO scratchpad_entries (id, session_id, key, value, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_id, key) DO UPDATE SET
                value = excluded.value,
                updated_at = excluded.updated_at
            """,
            (entry_id, session_id, key, value, now, now),
        )
        self._store._conn.commit()
        return self.read(session_id, key)  # type: ignore[return-value]

    def read(self, session_id: str, key: str) -> ScratchpadEntry | None:
        """Read a single entry by key.  Returns *None* if missing."""
        row = self._store._conn.execute(
            "SELECT id, session_id, key, value, created_at, updated_at "
            "FROM scratchpad_entries WHERE session_id = ? AND key = ?",
            (session_id, key),
        ).fetchone()
        if row is None:
            return None
        return ScratchpadEntry(**dict(row))

    def list_keys(self, session_id: str) -> list[str]:
        """Return all keys for a session, ordered by most-recently-updated."""
        rows = self._store._conn.execute(
            "SELECT key FROM scratchpad_entries "
            "WHERE session_id = ? ORDER BY updated_at DESC",
            (session_id,),
        ).fetchall()
        return [r["key"] for r in rows]

    def list_entries(self, session_id: str) -> list[ScratchpadEntry]:
        """Return all entries for a session, ordered by most-recently-updated."""
        rows = self._store._conn.execute(
            "SELECT id, session_id, key, value, created_at, updated_at "
            "FROM scratchpad_entries WHERE session_id = ? ORDER BY updated_at DESC",
            (session_id,),
        ).fetchall()
        return [ScratchpadEntry(**dict(r)) for r in rows]

    def delete(self, session_id: str, key: str) -> bool:
        """Delete a single entry.  Returns *True* if something was removed."""
        cursor = self._store._conn.execute(
            "DELETE FROM scratchpad_entries WHERE session_id = ? AND key = ?",
            (session_id, key),
        )
        self._store._conn.commit()
        return cursor.rowcount > 0

    def clear(self, session_id: str) -> int:
        """Delete **all** entries for a session.  Returns count removed."""
        cursor = self._store._conn.execute(
            "DELETE FROM scratchpad_entries WHERE session_id = ?",
            (session_id,),
        )
        self._store._conn.commit()
        return cursor.rowcount
