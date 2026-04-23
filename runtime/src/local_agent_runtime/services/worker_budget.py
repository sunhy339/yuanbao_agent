from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


TOKEN_DIMENSION = "tokens"
TOOL_CALL_DIMENSION = "tool_calls"


class WorkerBudgetExceededError(RuntimeError):
    """Raised when a worker attempts to consume beyond an enforced budget."""

    def __init__(
        self,
        *,
        dimension: str,
        limit: int,
        attempted: int,
        consumed: int,
    ) -> None:
        self.dimension = dimension
        self.limit = limit
        self.attempted = attempted
        self.consumed = consumed
        self.code = _budget_error_code(dimension)
        label = "tokens" if dimension == TOKEN_DIMENSION else "tool calls"
        super().__init__(
            f"Worker budget exceeded for {label}: attempted {attempted}, limit {limit}, consumed {consumed}."
        )


@dataclass(frozen=True, slots=True)
class BudgetQuota:
    limit: int | None = None
    consumed: int = 0

    def __post_init__(self) -> None:
        if self.limit is not None and self.limit < 0:
            raise ValueError("budget limit must be greater than or equal to 0")
        if self.consumed < 0:
            raise ValueError("budget consumed must be greater than or equal to 0")

    @property
    def remaining(self) -> int | None:
        if self.limit is None:
            return None
        return max(0, self.limit - self.consumed)

    @property
    def exhausted(self) -> bool:
        return self.limit is not None and self.consumed >= self.limit

    def consume(self, amount: int, *, dimension: str) -> BudgetQuota:
        if amount < 0:
            raise ValueError("budget consumption must be greater than or equal to 0")
        attempted = self.consumed + amount
        if self.limit is not None and attempted > self.limit:
            raise WorkerBudgetExceededError(
                dimension=dimension,
                limit=self.limit,
                attempted=attempted,
                consumed=self.consumed,
            )
        return BudgetQuota(limit=self.limit, consumed=attempted)


@dataclass(slots=True)
class WorkerBudget:
    tokens: BudgetQuota = BudgetQuota()
    tool_calls: BudgetQuota = BudgetQuota()

    @classmethod
    def from_metadata(cls, *metadata_items: Any) -> WorkerBudget:
        sources = _iter_budget_sources(metadata_items)
        return cls(
            tokens=_quota_from_metadata(
                sources,
                max_keys=("maxTokens", "max_tokens", "tokenLimit", "token_limit"),
                consumed_keys=("consumedTokens", "consumed_tokens", "tokenUsage", "token_usage"),
                remaining_keys=("remainingTokens", "remaining_tokens"),
            ),
            tool_calls=_quota_from_metadata(
                sources,
                max_keys=("maxToolCalls", "max_tool_calls", "toolCallLimit", "tool_call_limit"),
                consumed_keys=("consumedToolCalls", "consumed_tool_calls", "toolCallCount", "tool_call_count"),
                remaining_keys=("remainingToolCalls", "remaining_tool_calls"),
            ),
        )

    @property
    def consumed(self) -> dict[str, int]:
        return {
            "tokens": self.tokens.consumed,
            "toolCalls": self.tool_calls.consumed,
        }

    @property
    def remaining(self) -> dict[str, int | None]:
        return {
            "tokens": self.tokens.remaining,
            "toolCalls": self.tool_calls.remaining,
        }

    @property
    def exhausted(self) -> dict[str, bool]:
        return {
            "tokens": self.tokens.exhausted,
            "toolCalls": self.tool_calls.exhausted,
        }

    def consume_provider_usage(self, usage: Any) -> int:
        total_tokens = _usage_total_tokens(usage)
        self.tokens = self.tokens.consume(total_tokens, dimension=TOKEN_DIMENSION)
        return total_tokens

    def consume_tool_call(self, *, count: int = 1) -> int:
        count = _coerce_non_negative_int(count)
        self.tool_calls = self.tool_calls.consume(count, dimension=TOOL_CALL_DIMENSION)
        return count

    def to_metadata(self) -> dict[str, int | bool]:
        metadata: dict[str, int | bool] = {
            "consumedTokens": self.tokens.consumed,
            "tokensExhausted": self.tokens.exhausted,
            "consumedToolCalls": self.tool_calls.consumed,
            "toolCallsExhausted": self.tool_calls.exhausted,
        }
        if self.tokens.limit is not None:
            metadata["maxTokens"] = self.tokens.limit
            metadata["remainingTokens"] = self.tokens.remaining or 0
        if self.tool_calls.limit is not None:
            metadata["maxToolCalls"] = self.tool_calls.limit
            metadata["remainingToolCalls"] = self.tool_calls.remaining or 0
        return metadata


def _iter_budget_sources(metadata_items: tuple[Any, ...]) -> list[Mapping[str, Any]]:
    sources: list[Mapping[str, Any]] = []
    for item in metadata_items:
        if not isinstance(item, Mapping):
            continue
        sources.append(item)
        nested = item.get("budget")
        if isinstance(nested, Mapping):
            sources.append(nested)
    return sources


def _quota_from_metadata(
    sources: list[Mapping[str, Any]],
    *,
    max_keys: tuple[str, ...],
    consumed_keys: tuple[str, ...],
    remaining_keys: tuple[str, ...],
) -> BudgetQuota:
    limit = _last_present_int(sources, max_keys)
    consumed_candidates = _all_present_ints(sources, consumed_keys)
    remaining = _last_present_int(sources, remaining_keys)

    if limit is not None and remaining is not None:
        consumed_candidates.append(max(0, limit - remaining))

    consumed = max(consumed_candidates, default=0)
    return BudgetQuota(limit=limit, consumed=consumed)


def _usage_total_tokens(usage: Any) -> int:
    if usage is None:
        return 0
    if isinstance(usage, Mapping):
        value = usage.get("total_tokens")
    else:
        value = getattr(usage, "total_tokens", None)
    if value is None:
        return 0
    return _coerce_non_negative_int(value)


def _all_present_ints(sources: list[Mapping[str, Any]], keys: tuple[str, ...]) -> list[int]:
    values: list[int] = []
    for source in sources:
        for key in keys:
            if key not in source:
                continue
            raw_value = source.get(key)
            value = _coerce_optional_non_negative_int(raw_value)
            if value is not None:
                values.append(value)
    return values


def _last_present_int(sources: list[Mapping[str, Any]], keys: tuple[str, ...]) -> int | None:
    values = _all_present_ints(sources, keys)
    if not values:
        return None
    return values[-1]


def _coerce_optional_non_negative_int(value: Any) -> int | None:
    if value is None:
        return None
    return _coerce_non_negative_int(value)


def _coerce_non_negative_int(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("budget values must be integers")
    try:
        normalized = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("budget values must be integers") from exc
    if normalized < 0:
        raise ValueError("budget values must be greater than or equal to 0")
    return normalized


def _budget_error_code(dimension: str) -> str:
    if dimension == TOKEN_DIMENSION:
        return "WORKER_BUDGET_TOKENS_EXCEEDED"
    if dimension == TOOL_CALL_DIMENSION:
        return "WORKER_BUDGET_TOOL_CALLS_EXCEEDED"
    return "WORKER_BUDGET_EXCEEDED"
