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
import json
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


@dataclass(slots=True)
class CompactionDecision:
    """Decision about whether a context should be compacted."""

    should_compact: bool
    reason: str
    tokens_before: int
    max_tokens: int
    source: str = "rule"
    force: bool = False


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
        force: bool = False,
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
        if tokens_before <= max_tokens and not force:
            return CompactionResult(
                kept_messages=list(messages),
                tokens_before=tokens_before,
                tokens_after=tokens_before,
            )

        # --- Split into segments ---
        primers, history, recents = self._split_segments(messages)
        history, recents = self._repair_recent_tool_call_pairs(history, recents)

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

    def should_compact(
        self,
        messages: list[dict[str, Any]],
        max_tokens: int,
        *,
        near_budget_ratio: float = 0.80,
        force_budget_ratio: float = 1.0,
    ) -> CompactionDecision:
        """Decide whether to compact now.

        Runtime rules keep hard safety boundaries deterministic:
        over budget always compacts, well below budget never asks the model,
        and near-budget contexts may use the provider as an advisory signal.
        """
        tokens_before = sum(estimate_tokens(m.get("content", "")) for m in messages)
        if max_tokens <= 0:
            return CompactionDecision(
                should_compact=True,
                reason="invalid or exhausted token budget",
                tokens_before=tokens_before,
                max_tokens=max_tokens,
                source="rule",
                force=True,
            )
        if tokens_before >= int(max_tokens * force_budget_ratio):
            return CompactionDecision(
                should_compact=True,
                reason="context exceeds token budget",
                tokens_before=tokens_before,
                max_tokens=max_tokens,
                source="rule",
            )
        if tokens_before < int(max_tokens * near_budget_ratio):
            return CompactionDecision(
                should_compact=False,
                reason="context is comfortably within token budget",
                tokens_before=tokens_before,
                max_tokens=max_tokens,
                source="rule",
            )

        llm_decision = self._llm_compaction_decision(messages, tokens_before, max_tokens)
        if llm_decision is not None:
            return llm_decision

        return CompactionDecision(
            should_compact=tokens_before >= int(max_tokens * 0.90),
            reason="near token budget; provider unavailable or undecidable",
            tokens_before=tokens_before,
            max_tokens=max_tokens,
            source="rule_fallback",
            force=tokens_before < max_tokens,
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

    def _repair_recent_tool_call_pairs(
        self,
        history: list[dict[str, Any]],
        recents: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Keep strict tool-call request/response pairs together.

        Some provider APIs reject a tool/function output unless the matching
        assistant tool call is still present in the request history. Recency
        splitting can otherwise leave a tail like ``tool(call-1)`` while the
        preceding assistant message that created ``call-1`` was summarized.
        """
        if not history or not recents:
            return history, recents

        repaired_history = list(history)
        repaired_recents = list(recents)
        while True:
            recent_call_ids = self._assistant_tool_call_ids(repaired_recents)
            orphan_tool_ids = [
                tool_id
                for tool_id in (self._tool_result_id(message) for message in repaired_recents)
                if tool_id and tool_id not in recent_call_ids
            ]
            if not orphan_tool_ids:
                return repaired_history, repaired_recents

            orphan_set = set(orphan_tool_ids)
            boundary: int | None = None
            for index in range(len(repaired_history) - 1, -1, -1):
                if self._message_tool_call_ids(repaired_history[index]) & orphan_set:
                    boundary = index
                    break
            if boundary is None:
                return repaired_history, repaired_recents

            repaired_recents = repaired_history[boundary:] + repaired_recents
            repaired_history = repaired_history[:boundary]

    @staticmethod
    def _assistant_tool_call_ids(messages: list[dict[str, Any]]) -> set[str]:
        ids: set[str] = set()
        for message in messages:
            ids.update(ContextCompactor._message_tool_call_ids(message))
        return ids

    @staticmethod
    def _message_tool_call_ids(message: dict[str, Any]) -> set[str]:
        if message.get("role") != "assistant":
            return set()
        tool_calls = message.get("tool_calls")
        if not isinstance(tool_calls, list):
            return set()
        ids: set[str] = set()
        for item in tool_calls:
            if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"]:
                ids.add(item["id"])
        return ids

    @staticmethod
    def _tool_result_id(message: dict[str, Any]) -> str | None:
        if message.get("role") != "tool":
            return None
        tool_call_id = message.get("tool_call_id")
        if isinstance(tool_call_id, str) and tool_call_id:
            return tool_call_id
        return None

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

    def _llm_compaction_decision(
        self,
        messages: list[dict[str, Any]],
        tokens_before: int,
        max_tokens: int,
    ) -> CompactionDecision | None:
        if self._provider is None:
            return None
        preview = "\n".join(
            f"[{m.get('role', '?')}] {str(m.get('content', ''))[:500]}"
            for m in messages[-8:]
        )
        prompt = (
            "Decide whether to compact this conversation context before the next model call.\n"
            "Return ONLY JSON: {\"shouldCompact\": true|false, \"reason\": \"short reason\"}.\n"
            "Prefer compaction when older details can be summarized safely; avoid compaction "
            "when exact recent tool outputs or code snippets are still critical.\n\n"
            f"Estimated tokens: {tokens_before}\n"
            f"Max tokens: {max_tokens}\n"
            f"Recent context preview:\n{preview}"
        )
        try:
            result = self._provider.generate(prompt, {})
            raw = result.get("message") or result.get("content") or ""
            match_start = raw.find("{")
            match_end = raw.rfind("}")
            if match_start < 0 or match_end <= match_start:
                return None
            payload = json.loads(raw[match_start:match_end + 1])
            should = bool(payload.get("shouldCompact", False))
            reason = str(payload.get("reason") or "provider compaction proposal").strip()
            return CompactionDecision(
                should_compact=should,
                reason=reason[:240],
                tokens_before=tokens_before,
                max_tokens=max_tokens,
                source="llm",
                force=should and tokens_before < max_tokens,
            )
        except Exception:
            return None

    @staticmethod
    def _hash_primers(primers: list[dict]) -> str:
        content = "|".join(m.get("content", "") for m in primers)
        return hashlib.sha256(content.encode()).hexdigest()[:16]
