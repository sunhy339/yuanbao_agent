"""Three-segment context compaction for long conversations.

The compactor splits conversation history into three segments:

1. **Primers** – system prompts and tool definitions (always kept in full).
2. **Summary** – an LLM-generated summary of the middle history.
3. **Recents** – the last *N* turns, kept verbatim.

When the total token estimate exceeds the budget, the compactor generates a
summary of the older turns and retains only the recent tail verbatim.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Protocol

from ..store.sqlite_store import SQLiteStore
from .token_budget import estimate_tokens


# ---------------------------------------------------------------------------
# Public data types
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class CompactionResult:
    """Outcome of a compaction pass."""

    kept_messages: list[dict[str, Any]]
    tokens_before: int
    tokens_after: int
    summary: str | None = None
    strategy: str = "primer_summary_recent"
    compaction_id: str | None = None


# ---------------------------------------------------------------------------
# Provider protocol (minimal interface for summary generation)
# ---------------------------------------------------------------------------

class _CanGenerate(Protocol):
    def generate(self, prompt: str, context: dict) -> dict: ...


# ---------------------------------------------------------------------------
# Compactor
# ---------------------------------------------------------------------------

_RECENT_TURN_DEFAULT = 6
_SUMMARY_MAX_CHARS = 4000


class ContextCompactor:
    """Compresses a message list to fit within a token budget."""

    def __init__(
        self,
        store: SQLiteStore,
        *,
        provider: _CanGenerate | None = None,
        recent_turns: int = _RECENT_TURN_DEFAULT,
    ) -> None:
        self._store = store
        self._provider = provider
        self._recent_turns = recent_turns

    # -- public API --

    def compact(
        self,
        session_id: str,
        messages: list[dict[str, Any]],
        max_tokens: int,
        *,
        message_ids: list[str] | None = None,
        task_id: str | None = None,
    ) -> CompactionResult:
        """Compact *messages* to fit within *max_tokens*.

        Returns a ``CompactionResult`` with the retained message list and
        bookkeeping metadata.  When the messages already fit, no summary is
        generated.

        *message_ids* maps to the input messages for traceability.
        *task_id* is the task that triggered this compaction.
        """
        # Compute per-message tokens once
        msg_tokens = [estimate_tokens(m.get("content", "")) for m in messages]
        tokens_before = sum(msg_tokens)
        if tokens_before <= max_tokens:
            return CompactionResult(
                kept_messages=list(messages),
                tokens_before=tokens_before,
                tokens_after=tokens_before,
            )

        # --- Split into segments ---
        primers, history, recents = self._split_segments(messages)

        primer_tokens = sum(estimate_tokens(m.get("content", "")) for m in primers)
        recent_tokens = sum(estimate_tokens(m.get("content", "")) for m in recents)
        budget_for_summary = max_tokens - primer_tokens - recent_tokens

        # Generate summary of the history segment
        summary = self._generate_summary(history, budget_for_summary)
        summary_tokens = estimate_tokens(summary) if summary else 0

        # Reassemble
        kept: list[dict[str, Any]] = list(primers)
        if summary:
            kept.append({
                "role": "system",
                "content": f"[Conversation summary]\n{summary}",
            })
        kept.extend(recents)

        # Compute tokens_after from pre-computed values + summary
        tokens_after = primer_tokens + recent_tokens + summary_tokens
        primer_hash = self._hash_primers(primers)

        # Persist record
        import json as _json
        compaction_id = self._store.new_id("cmp")
        now = self._store.now()

        # Determine which message IDs were covered (history segment)
        covered_ids: list[str] = []
        if message_ids:
            msg_count = len(messages)
            ids_count = len(message_ids)
            # History messages are the ones not in primers or recents
            primer_count = len(primers)
            body_count = msg_count - primer_count
            split_point = max(0, body_count - self._recent_turns)
            # History segment covers from primer_count to primer_count + split_point
            for idx in range(primer_count, primer_count + split_point):
                if idx < ids_count:
                    covered_ids.append(message_ids[idx])

        self._store._conn.execute(
            """
            INSERT INTO compaction_records
                (id, session_id, task_id, strategy, tokens_before, tokens_after,
                 summary, primer_hash, covered_message_ids, trimmed_sections, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                compaction_id,
                session_id,
                task_id,
                "primer_summary_recent",
                tokens_before,
                tokens_after,
                summary,
                primer_hash,
                _json.dumps(covered_ids, ensure_ascii=False),
                _json.dumps([], ensure_ascii=False),
                now,
            ),
        )
        self._store._conn.commit()

        return CompactionResult(
            kept_messages=kept,
            tokens_before=tokens_before,
            tokens_after=tokens_after,
            summary=summary,
            strategy="primer_summary_recent",
            compaction_id=compaction_id,
        )

    # -- internals --

    def _split_segments(
        self, messages: list[dict[str, Any]]
    ) -> tuple[list[dict], list[dict], list[dict]]:
        """Split into (primers, history, recents).

        *Primers* are leading system messages.
        *Recents* are the last ``_recent_turns`` non-system messages.
        *History* is everything in between.
        """
        primers: list[dict] = []
        body_start = 0
        for i, msg in enumerate(messages):
            if msg.get("role") == "system":
                primers.append(msg)
                body_start = i + 1
            else:
                break

        body = messages[body_start:]
        split_point = max(0, len(body) - self._recent_turns)
        history = body[:split_point]
        recents = body[split_point:]
        return primers, history, recents

    def _generate_summary(self, history: list[dict], token_budget: int) -> str | None:
        """Ask the LLM to summarise *history*, or return a heuristic summary."""
        if not history:
            return None

        history_text = "\n".join(
            f"[{m.get('role', '?')}] {m.get('content', '')}" for m in history
        )

        # If we have a provider, use it; otherwise fall back to a heuristic truncation
        if self._provider is not None:
            try:
                prompt = (
                    "Summarize the following conversation history concisely. "
                    "Preserve key decisions, facts, and any code references.\n\n"
                    f"{history_text[:8000]}"
                )
                result = self._provider.generate(prompt, {})
                raw = result.get("message", "") or result.get("content", "")
                if raw:
                    max_chars = max(token_budget * 4, 500) if token_budget > 0 else _SUMMARY_MAX_CHARS
                    return raw[:max_chars]
            except Exception:
                pass  # fall through to heuristic

        # Heuristic: keep first and last portion of history
        max_chars = max(token_budget * 4, 500) if token_budget > 0 else _SUMMARY_MAX_CHARS
        if len(history_text) <= max_chars:
            return history_text
        head = history_text[: max_chars // 2]
        tail = history_text[len(history_text) - max_chars // 2 :]
        return f"{head}\n…[truncated]…\n{tail}"

    @staticmethod
    def _hash_primers(primers: list[dict]) -> str:
        content = "|".join(m.get("content", "") for m in primers)
        return hashlib.sha256(content.encode()).hexdigest()[:16]
