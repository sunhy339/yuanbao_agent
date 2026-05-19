from __future__ import annotations

import logging
import re
from typing import Any

from ..provider.adapter import ProviderAdapter

logger = logging.getLogger(__name__)

_SYNTHESIS_PROMPT = """\
You are a result synthesis specialist. Merge the following sub-task results into
a single coherent summary for the original goal.

**Goal**: {goal}

**Sub-task results**:
{results_text}

Guidelines:
- Deduplicate overlapping information
- Preserve all unique findings and details
- Organize into a clear, structured summary
- Keep the summary concise but comprehensive
- Use the same language as the original goal (Chinese or English)

Output a single consolidated summary paragraph.
"""

# Reuse stop-word list pattern from coverage evaluator
_STOP_WORDS: frozenset[str] = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "can", "need", "to", "of", "in",
    "for", "on", "with", "at", "by", "from", "as", "into", "through",
    "during", "before", "after", "and", "or", "not", "but", "if",
    "it", "this", "that", "these", "those", "its",
})

_CJK_PATTERN = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")


class ResultSynthesizer:
    """Merge and deduplicate sub-task results into a coherent summary."""

    def __init__(self, provider: ProviderAdapter | None = None) -> None:
        self._provider = provider

    def synthesize(
        self,
        goal: str,
        subtask_results: list[dict[str, Any]],
        *,
        mode: str = "auto",
        provider_context: dict[str, Any] | None = None,
    ) -> str:
        """Synthesize sub-task results into a final summary.

        Args:
            goal: The original goal/task description.
            subtask_results: List of dicts, each with at least "title" and "result" keys.
            mode: "auto" (<=3 concat, >3 llm), "concat", or "llm".
        """
        if not subtask_results:
            return "No sub-task results to synthesize."

        # Deduplicate first
        deduped = self.dedup(subtask_results)

        if mode == "auto":
            mode = "concat" if len(deduped) <= 3 else "llm"

        if mode == "llm" and self._provider is not None:
            return self._synthesize_llm(goal, deduped, provider_context=provider_context)

        return self._synthesize_concat(deduped)

    def dedup(self, results: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Remove results with high keyword overlap (Jaccard similarity > 0.7)."""
        if len(results) <= 1:
            return list(results)

        keyword_sets: list[frozenset[str]] = [
            frozenset(self._extract_keywords(
                f"{r.get('title', '')} {r.get('result', '')}",
            ))
            for r in results
        ]

        kept_indices: list[int] = []
        for i in range(len(results)):
            overlap = any(
                self._jaccard(keyword_sets[i], keyword_sets[j]) > 0.7
                for j in kept_indices
            )
            if not overlap:
                kept_indices.append(i)

        return [results[i] for i in kept_indices]

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _synthesize_concat(self, results: list[dict[str, Any]]) -> str:
        """Rule-based concatenation (same pattern as DAGExecutor.synthesize_results)."""
        parts: list[str] = []
        for r in results:
            title = r.get("title", "Untitled")
            result_text = r.get("result", "Done")
            parts.append(f"- {title}: {result_text}")

        header = f"Plan execution completed ({len(results)} sub-tasks)"
        return header + "\n" + "\n".join(parts)

    def _synthesize_llm(
        self,
        goal: str,
        results: list[dict[str, Any]],
        *,
        provider_context: dict[str, Any] | None = None,
    ) -> str:
        """LLM-based synthesis via ProviderAdapter."""
        results_text = "\n".join(
            f"- {r.get('title', 'Untitled')}: {r.get('result', 'Done')}"
            for r in results
        )
        prompt = _SYNTHESIS_PROMPT.format(goal=goal, results_text=results_text)
        try:
            response = self._provider.generate(
                prompt,
                {
                    **(provider_context or {}),
                    "messages": [{"role": "user", "content": prompt}],
                },
            )
            return response.get("message") or self._synthesize_concat(results)
        except Exception as exc:  # noqa: BLE001
            logger.warning("LLM synthesis failed (%s), falling back to concat", exc)
            return self._synthesize_concat(results)

    @staticmethod
    def _extract_keywords(text: str) -> set[str]:
        keywords: set[str] = set()
        cjk_chars = _CJK_PATTERN.findall(text)
        for ch in cjk_chars:
            if ch not in _STOP_WORDS:
                keywords.add(ch)
        latin_text = _CJK_PATTERN.sub(" ", text)
        words = re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*", latin_text.lower())
        for word in words:
            if word not in _STOP_WORDS and len(word) > 1:
                keywords.add(word)
        return keywords

    @staticmethod
    def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
        if not a and not b:
            return 1.0
        if not a or not b:
            return 0.0
        return len(a & b) / len(a | b)
