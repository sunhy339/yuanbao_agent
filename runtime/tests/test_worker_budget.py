from __future__ import annotations

import pytest

from local_agent_runtime.services.worker_budget import (
    BudgetQuota,
    WorkerBudget,
    WorkerBudgetExceededError,
)


def test_worker_budget_consumes_provider_tokens_and_tool_calls() -> None:
    budget = WorkerBudget.from_metadata(
        {
            "maxTokens": 100,
            "maxToolCalls": 3,
        }
    )

    budget.consume_provider_usage({"total_tokens": 24})
    budget.consume_tool_call()

    assert budget.tokens == BudgetQuota(limit=100, consumed=24)
    assert budget.tokens.remaining == 76
    assert budget.tokens.exhausted is False
    assert budget.tool_calls == BudgetQuota(limit=3, consumed=1)
    assert budget.tool_calls.remaining == 2
    assert budget.tool_calls.exhausted is False
    assert budget.consumed == {"tokens": 24, "toolCalls": 1}
    assert budget.remaining == {"tokens": 76, "toolCalls": 2}
    assert budget.exhausted == {"tokens": False, "toolCalls": False}


def test_worker_budget_supports_repeated_consumption() -> None:
    budget = WorkerBudget.from_metadata(
        {
            "maxTokens": 12,
            "maxToolCalls": 2,
        }
    )

    budget.consume_provider_usage({"total_tokens": 5})
    budget.consume_provider_usage({"total_tokens": 4})
    budget.consume_tool_call()
    budget.consume_tool_call()

    assert budget.tokens.consumed == 9
    assert budget.tokens.remaining == 3
    assert budget.tokens.exhausted is False
    assert budget.tool_calls.consumed == 2
    assert budget.tool_calls.remaining == 0
    assert budget.tool_calls.exhausted is True


def test_worker_budget_raises_explicit_error_when_limit_is_exceeded() -> None:
    budget = WorkerBudget.from_metadata({"maxTokens": 10, "maxToolCalls": 1})

    with pytest.raises(WorkerBudgetExceededError) as token_error:
        budget.consume_provider_usage({"total_tokens": 11})

    assert token_error.value.code == "WORKER_BUDGET_TOKENS_EXCEEDED"
    assert token_error.value.dimension == "tokens"
    assert token_error.value.limit == 10
    assert token_error.value.attempted == 11

    with pytest.raises(WorkerBudgetExceededError) as tool_error:
        budget.consume_tool_call(count=2)

    assert tool_error.value.code == "WORKER_BUDGET_TOOL_CALLS_EXCEEDED"
    assert tool_error.value.dimension == "tool_calls"
    assert tool_error.value.limit == 1
    assert tool_error.value.attempted == 2


def test_worker_budget_defaults_to_unlimited_when_budget_is_missing() -> None:
    budget = WorkerBudget.from_metadata(None, {})

    budget.consume_provider_usage({"total_tokens": 42})
    budget.consume_tool_call()

    assert budget.tokens.limit is None
    assert budget.tokens.consumed == 42
    assert budget.tokens.remaining is None
    assert budget.tokens.exhausted is False
    assert budget.tool_calls.limit is None
    assert budget.tool_calls.consumed == 1
    assert budget.tool_calls.remaining is None
    assert budget.tool_calls.exhausted is False


def test_worker_budget_merges_metadata_inputs_and_reconstructs_consumption() -> None:
    budget = WorkerBudget.from_metadata(
        {
            "maxTokens": 100,
            "remainingTokens": 60,
            "consumedToolCalls": 1,
        },
        {
            "maxToolCalls": 4,
            "remainingToolCalls": 2,
        },
    )

    assert budget.tokens.limit == 100
    assert budget.tokens.consumed == 40
    assert budget.tokens.remaining == 60
    assert budget.tokens.exhausted is False
    assert budget.tool_calls.limit == 4
    assert budget.tool_calls.consumed == 2
    assert budget.tool_calls.remaining == 2
    assert budget.tool_calls.exhausted is False

    snapshot = budget.to_metadata()

    assert snapshot == {
        "maxTokens": 100,
        "consumedTokens": 40,
        "remainingTokens": 60,
        "tokensExhausted": False,
        "maxToolCalls": 4,
        "consumedToolCalls": 2,
        "remainingToolCalls": 2,
        "toolCallsExhausted": False,
    }
