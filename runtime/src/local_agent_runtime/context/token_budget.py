from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Any


# Default ratio for Latin/ASCII text (~4 chars per token).
CHARS_PER_TOKEN = 4
# CJK characters are typically 2-3 tokens each; use 1.5 chars per token.
_CJK_CHARS_PER_TOKEN = 1.5

# Regex matching CJK Unified Ideographs, CJK Extension A/B, and common
# fullwidth punctuation / kana / hangul ranges that tokenizers encode densely.
_CJK_RE = re.compile(
    r"[\u2e80-\u2fff"   # CJK radicals, Kangxi radicals
    r"\u3000-\u303f"    # CJK symbols and punctuation
    r"\u3040-\u30ff"    # Hiragana + Katakana
    r"\u3400-\u4dbf"    # CJK Extension A
    r"\u4e00-\u9fff"    # CJK Unified Ideographs
    r"\uf900-\ufaff"    # CJK Compatibility Ideographs
    r"\ufe30-\ufe4f"    # CJK Compatibility Forms
    r"\U00020000-\U0002a6df"  # CJK Extension B
    r"\U0002a700-\U0002b73f"  # CJK Extension C
    r"\U0002b740-\U0002b81f"  # CJK Extension D
    r"\uac00-\ud7af"    # Hangul Syllables
    r"\uff00-\uffef"    # Fullwidth Forms
    r"]"
)


@lru_cache(maxsize=512)
def _count_cjk_chars(text: str) -> int:
    """Count the number of CJK / fullwidth characters in *text*."""
    return len(_CJK_RE.findall(text))


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


@lru_cache(maxsize=1024)
def _estimate_string_tokens(text: str) -> int:
    """Cached token estimation for string inputs."""
    if not text:
        return 0
    cjk_count = _count_cjk_chars(text)
    latin_count = len(text) - cjk_count
    cjk_tokens = int(cjk_count / _CJK_CHARS_PER_TOKEN + 0.5) if cjk_count else 0
    latin_tokens = (latin_count + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN if latin_count else 0
    return max(1, cjk_tokens + latin_tokens)


def estimate_tokens(value: Any) -> int:
    """Estimate token count using a CJK-aware char/token ratio.

    Latin/ASCII text uses ~4 chars per token; CJK characters use ~1.5 chars
    per token (each CJK char is typically 2-3 tokens in BPE tokenizers).
    """
    if value is None:
        return 0
    if isinstance(value, str):
        return _estimate_string_tokens(value)
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    if not text:
        return 0
    return _estimate_string_tokens(text)


def _effective_max_chars(text: str, max_tokens: int) -> int:
    """Calculate the effective character limit for *max_tokens* given the
    CJK ratio of *text*.  Falls back to the simple Latin ratio when the
    text contains no CJK characters."""
    total = len(text)
    if total == 0:
        return max(0, max_tokens * CHARS_PER_TOKEN)
    cjk_count = _count_cjk_chars(text)
    if cjk_count == 0:
        return max(0, max_tokens * CHARS_PER_TOKEN)
    # Weighted average chars-per-token based on the CJK ratio of the text.
    cjk_ratio = cjk_count / total
    weighted_cpt = cjk_ratio * _CJK_CHARS_PER_TOKEN + (1.0 - cjk_ratio) * CHARS_PER_TOKEN
    return max(0, int(max_tokens * weighted_cpt))


def trim_text_to_tokens(text: str, max_tokens: int) -> str:
    max_chars = _effective_max_chars(text, max_tokens)
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

        # Cache token counts to avoid O(N²) re-estimation
        token_cache: dict[int, int] = {id(s): estimate_tokens(s.text) for s in sections}
        running_total = fixed_tokens + sum(token_cache.values())

        for section in sorted(sections, key=lambda item: item.priority):
            if running_total <= self.max_context_tokens:
                break
            if section not in kept:
                continue

            current_tokens = token_cache.get(id(section), 0)
            overflow = running_total - self.max_context_tokens
            target_tokens = max(section.minimum_tokens, current_tokens - overflow)

            if not section.truncatable:
                continue

            if target_tokens <= 0:
                kept.remove(section)
                running_total -= current_tokens
                dropped_sections.append(section.name)
                continue

            trimmed_text = trim_text_to_tokens(section.text, target_tokens)
            if not trimmed_text:
                kept.remove(section)
                running_total -= current_tokens
                dropped_sections.append(section.name)
                continue

            new_tokens = estimate_tokens(trimmed_text)
            new_section = BudgetSection(
                name=section.name,
                text=trimmed_text,
                priority=section.priority,
                truncatable=section.truncatable,
                minimum_tokens=section.minimum_tokens,
            )
            kept[kept.index(section)] = new_section
            running_total += new_tokens - current_tokens
            token_cache[id(new_section)] = new_tokens
            trimmed_sections.append(section.name)

        while running_total > self.max_context_tokens and any(section.truncatable for section in kept):
            lowest = min((section for section in kept if section.truncatable), key=lambda item: item.priority)
            running_total -= token_cache.get(id(lowest), 0)
            kept.remove(lowest)
            dropped_sections.append(lowest.name)

        return BudgetResult(
            sections=kept,
            stats={
                "maxContextTokens": self.max_context_tokens,
                "estimatedTokens": running_total,
                "estimatedInputTokens": running_total,
                "fixedTokens": fixed_tokens,
                "trimmedSections": trimmed_sections,
                "droppedSections": dropped_sections,
            },
        )
