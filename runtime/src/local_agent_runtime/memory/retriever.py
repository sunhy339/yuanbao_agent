"""Lightweight keyword-based semantic retrieval for memory entries.

Uses Jaccard similarity with TF-IDF-inspired weighting — no external
vector libraries required.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

from .store import MemoryStore
from .types import MemoryEntry, MemoryKind

# ---------------------------------------------------------------------------
# Tokenizer
# ---------------------------------------------------------------------------

# English stop words (top 40)
_EN_STOP: frozenset[str] = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "by", "do", "for",
    "from", "has", "have", "he", "how", "i", "in", "is", "it", "its",
    "just", "me", "my", "no", "not", "of", "on", "or", "our", "out",
    "she", "so", "that", "the", "them", "then", "this", "to", "up",
    "was", "we", "what", "when", "which", "who", "will", "with", "you",
})

# Chinese stop characters (common structural particles)
_ZH_STOP: frozenset[str] = frozenset({
    "的", "了", "在", "是", "我", "有", "和", "就", "不", "人",
    "都", "一", "一个", "上", "也", "很", "到", "说", "要", "去",
    "你", "会", "着", "没有", "看", "好", "自己", "这",
})

_WORD_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9_]*")
# Broad CJK range matching Unified Ideographs + Extension A + Compatibility + radicals
_ZH_CHAR_RE = re.compile(
    r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+"
)


def _tokenize(text: str) -> list[str]:
    """Tokenize into English words + Chinese bigrams + individual CJK chars."""
    tokens: list[str] = []
    lowered = text.lower()

    # English words
    for m in _WORD_RE.finditer(lowered):
        tokens.append(m.group())

    # Chinese: extract runs, build unigrams + bigrams
    for m in _ZH_CHAR_RE.finditer(text):
        run = m.group()
        for ch in run:
            tokens.append(ch)
        for i in range(len(run) - 1):
            tokens.append(run[i : i + 2])

    return tokens


def _filter_stop(tokens: list[str]) -> list[str]:
    """Remove stop words, keep tokens of length >= 2 for English."""
    result: list[str] = []
    for t in tokens:
        if t in _EN_STOP or t in _ZH_STOP:
            continue
        # Keep single CJK chars (they're meaningful) but skip single Latin chars
        if len(t) == 1 and t.isascii():
            continue
        result.append(t)
    return result


def extract_keywords(text: str) -> list[str]:
    """Extract deduplicated, stop-filtered keywords from text."""
    raw = _tokenize(text)
    filtered = _filter_stop(raw)
    # Deduplicate while preserving order
    seen: set[str] = set()
    result: list[str] = []
    for t in filtered:
        if t not in seen:
            seen.add(t)
            result.append(t)
    return result


# ---------------------------------------------------------------------------
# Similarity
# ---------------------------------------------------------------------------

def _jaccard_weighted(
    query_tokens: list[str],
    doc_tokens: list[str],
) -> float:
    """Jaccard similarity with term-frequency weighting."""
    if not query_tokens or not doc_tokens:
        return 0.0

    q_counts = Counter(query_tokens)
    d_counts = Counter(doc_tokens)

    # Intersection: sum of min freqs
    intersection = sum(min(q_counts[t], d_counts[t]) for t in q_counts if t in d_counts)
    # Union: sum of max freqs
    union = sum(max(q_counts[t], d_counts.get(t, 0)) for t in set(q_counts) | set(d_counts))

    if union == 0:
        return 0.0
    return intersection / union


# ---------------------------------------------------------------------------
# Retriever
# ---------------------------------------------------------------------------

class MemoryRetriever:
    """Keyword-based memory search using Jaccard + TF weighting."""

    def __init__(self, memory_store: MemoryStore) -> None:
        self._store = memory_store

    def search(
        self,
        workspace_id: str,
        query: str,
        *,
        kind: MemoryKind | None = None,
        session_id: str | None = None,
        limit: int = 10,
        threshold: float = 0.15,
        session_boost: float = 0.05,
    ) -> list[tuple[MemoryEntry, float]]:
        """Search workspace memories and return (entry, score) pairs above *threshold*.

        Workspace scope is primary. ``session_id`` is only used as a relevance
        boost for memories from the active session, not as a hard filter.
        """
        query_tokens = _filter_stop(_tokenize(query))
        if not query_tokens:
            return []

        candidates = self._store.query_all(
            workspace_id=workspace_id,
            kind=kind,
            limit=50,
        )

        scored: list[tuple[MemoryEntry, float]] = []
        for entry in candidates:
            # Combine content + keywords for matching
            doc_text = entry.content
            if entry.keywords:
                doc_text += " " + " ".join(entry.keywords)
            doc_tokens = _filter_stop(_tokenize(doc_text))
            score = _jaccard_weighted(query_tokens, doc_tokens)
            if session_id and entry.session_id == session_id:
                score += session_boost
            if score >= threshold:
                scored.append((entry, score))

        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:limit]
