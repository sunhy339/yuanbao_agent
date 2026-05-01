from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

from .types import Subtask

# Common Chinese/English stop words for keyword filtering
_STOP_WORDS: frozenset[str] = frozenset({
    # English
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "can", "need", "dare", "ought",
    "used", "to", "of", "in", "for", "on", "with", "at", "by", "from",
    "as", "into", "through", "during", "before", "after", "above", "below",
    "between", "out", "off", "over", "under", "again", "further", "then",
    "once", "here", "there", "when", "where", "why", "how", "all", "each",
    "every", "both", "few", "more", "most", "other", "some", "such", "no",
    "not", "only", "own", "same", "so", "than", "too", "very", "just",
    "because", "but", "and", "or", "if", "while", "about", "up", "its",
    "it", "this", "that", "these", "those", "i", "me", "my", "we", "our",
    "you", "your", "he", "him", "his", "she", "her", "they", "them", "their",
    "what", "which", "who", "whom", "whose",
    # Chinese
    "的", "了", "在", "是", "我", "有", "和", "就", "不", "人", "都", "一",
    "一个", "上", "也", "很", "到", "说", "要", "去", "你", "会", "着", "没有",
    "看", "好", "自己", "这", "他", "她", "它", "们", "那", "些", "把", "被",
    "让", "给", "对", "吗", "吧", "呢", "啊", "哦", "嗯",
    # Common verbs with low information value
    "make", "get", "go", "come", "take", "give", "use", "find", "tell",
    "ask", "work", "seem", "feel", "try", "leave", "call",
})

_CJK_PATTERN = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")


class CoverageEvaluator:
    """Evaluate how well sub-tasks cover the original goal using keyword overlap."""

    def evaluate(self, goal: str, subtasks: list[Subtask]) -> float:
        """Return a coverage score between 0.0 and 1.0.

        Compares keywords extracted from the goal against keywords from
        all subtask titles and descriptions.
        """
        if not goal or not goal.strip():
            return 1.0

        goal_keywords = self._extract_keywords(goal)
        if not goal_keywords:
            return 1.0

        combined_text = " ".join(
            f"{s.title} {s.description}" for s in subtasks
        )
        subtask_keywords = self._extract_keywords(combined_text)

        if not subtask_keywords:
            return 0.0

        covered = goal_keywords & subtask_keywords
        score = len(covered) / len(goal_keywords)
        logger.debug("Coverage: %d/%d keywords covered (%.2f)", len(covered), len(goal_keywords), score)
        return score

    def find_gaps(self, goal: str, subtasks: list[Subtask]) -> list[str]:
        """Return goal keywords not covered by any subtask."""
        if not goal or not goal.strip():
            return []
        goal_keywords = self._extract_keywords(goal)
        if not goal_keywords:
            return []
        combined_text = " ".join(f"{s.title} {s.description}" for s in subtasks)
        subtask_keywords = self._extract_keywords(combined_text)
        gaps = goal_keywords - subtask_keywords
        return sorted(gaps)

    def _extract_keywords(self, text: str) -> set[str]:
        """Extract meaningful keywords from text.

        Handles both CJK characters (split individually) and
        Latin words (split by whitespace/punctuation).
        """
        keywords: set[str] = set()

        # Extract CJK characters as individual tokens
        cjk_chars = _CJK_PATTERN.findall(text)
        for ch in cjk_chars:
            if ch not in _STOP_WORDS:
                keywords.add(ch)

        # Extract Latin words
        latin_text = _CJK_PATTERN.sub(" ", text)
        words = re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*", latin_text.lower())
        for word in words:
            if word not in _STOP_WORDS and len(word) > 1:
                keywords.add(word)

        return keywords
