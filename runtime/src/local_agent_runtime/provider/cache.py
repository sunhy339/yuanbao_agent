"""LLM response cache backed by SQLiteStore."""

from __future__ import annotations

import hashlib
import json
from typing import Any


class LLMCache:
    """Prompt-hash based LLM response cache with TTL support."""

    def __init__(self, store: Any) -> None:
        self._store = store

    @staticmethod
    def hash_prompt(
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
    ) -> str:
        """Compute a deterministic SHA-256 hash for a prompt signature."""
        canonical = json.dumps(
            {"model": model or "", "messages": messages, "tools": tools or []},
            ensure_ascii=False,
            sort_keys=True,
        )
        return hashlib.sha256(canonical.encode()).hexdigest()

    def get(self, prompt_hash: str) -> str | None:
        """Retrieve cached response. Returns None if missing or expired."""
        now = self._store.now()
        row = self._store._conn.execute(
            "SELECT response, expires_at FROM llm_cache WHERE prompt_hash = ?",
            (prompt_hash,),
        ).fetchone()
        if row is None:
            return None
        if row["expires_at"] < now:
            self._store._conn.execute(
                "DELETE FROM llm_cache WHERE prompt_hash = ?",
                (prompt_hash,),
            )
            self._store._conn.commit()
            return None
        return row["response"]

    def put(self, prompt_hash: str, response: str, ttl: int = 3600) -> None:
        """Store a response with TTL (seconds)."""
        now = self._store.now()
        expires_at = now + ttl * 1000
        self._store._conn.execute(
            """
            INSERT OR REPLACE INTO llm_cache (prompt_hash, response, created_at, expires_at)
            VALUES (?, ?, ?, ?)
            """,
            (prompt_hash, response, now, expires_at),
        )
        self._store._conn.commit()

    def invalidate(self, prompt_hash: str) -> None:
        """Delete a single cache entry."""
        self._store._conn.execute(
            "DELETE FROM llm_cache WHERE prompt_hash = ?",
            (prompt_hash,),
        )
        self._store._conn.commit()

    def cleanup_expired(self) -> int:
        """Remove all expired entries. Returns count of deleted rows."""
        now = self._store.now()
        cursor = self._store._conn.execute(
            "DELETE FROM llm_cache WHERE expires_at < ?",
            (now,),
        )
        self._store._conn.commit()
        return cursor.rowcount
