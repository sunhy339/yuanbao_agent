from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


CHARS_PER_TOKEN = 4


@dataclass(frozen=True)
class BudgetSection:
    name: str
    text: str
    priority: int
    truncatable: bool = True
    minimum_tokens: int = 0


@dataclass(frozen=True)
class BudgetResult:
    sections: list[BudgetSection]
    stats: dict[str, Any]


def estimate_tokens(value: Any) -> int:
    """Estimate token count cheaply using a conservative char/token ratio."""

    if value is None:
        return 0
    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    if not text:
        return 0
    return max(1, (len(text) + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN)


def trim_text_to_tokens(text: str, max_tokens: int) -> str:
    max_chars = max(0, max_tokens * CHARS_PER_TOKEN)
    if len(text) <= max_chars:
        return text
    if max_chars <= 0:
        return ""

    omitted = len(text) - max_chars
    marker = f"\n[truncated {omitted} chars]"
    if len(marker) >= max_chars:
        return marker[-max_chars:]
    return text[: max_chars - len(marker)].rstrip() + marker


class TokenBudget:
    def __init__(self, max_context_tokens: int) -> None:
        self.max_context_tokens = max(1, int(max_context_tokens))

    def fit(self, sections: list[BudgetSection], *, fixed_tokens: int = 0) -> BudgetResult:
        kept = list(sections)
        fixed_tokens = max(0, int(fixed_tokens))
        trimmed_sections: list[str] = []
        dropped_sections: list[str] = []

        def total_tokens() -> int:
            return fixed_tokens + sum(estimate_tokens(section.text) for section in kept)

        for section in sorted(sections, key=lambda item: item.priority):
            current_total = total_tokens()
            if current_total <= self.max_context_tokens:
                break
            if section not in kept:
                continue

            current_tokens = estimate_tokens(section.text)
            overflow = current_total - self.max_context_tokens
            target_tokens = max(section.minimum_tokens, current_tokens - overflow)

            if not section.truncatable:
                continue

            if target_tokens <= 0:
                kept.remove(section)
                dropped_sections.append(section.name)
                continue

            trimmed_text = trim_text_to_tokens(section.text, target_tokens)
            if not trimmed_text:
                kept.remove(section)
                dropped_sections.append(section.name)
                continue

            kept[kept.index(section)] = BudgetSection(
                name=section.name,
                text=trimmed_text,
                priority=section.priority,
                truncatable=section.truncatable,
                minimum_tokens=section.minimum_tokens,
            )
            trimmed_sections.append(section.name)

        while total_tokens() > self.max_context_tokens and any(section.truncatable for section in kept):
            lowest = min((section for section in kept if section.truncatable), key=lambda item: item.priority)
            kept.remove(lowest)
            dropped_sections.append(lowest.name)

        estimated_tokens = total_tokens()
        return BudgetResult(
            sections=kept,
            stats={
                "maxContextTokens": self.max_context_tokens,
                "estimatedTokens": estimated_tokens,
                "estimatedInputTokens": estimated_tokens,
                "fixedTokens": fixed_tokens,
                "trimmedSections": trimmed_sections,
                "droppedSections": dropped_sections,
            },
        )
