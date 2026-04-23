from __future__ import annotations

import pytest

from local_agent_runtime.services.worker_policy import (
    WorkerTimeoutError,
    classify_retryable_error,
    normalize_worker_policy,
)


def test_worker_policy_defaults_to_one_attempt_and_no_timeouts() -> None:
    policy = normalize_worker_policy(None)

    assert policy.retry.max_attempts == 1
    assert policy.timeout.per_attempt_seconds is None
    assert policy.timeout.total_seconds is None
    assert policy.timeout.deadline_from(100.0).expires_at is None
    assert not policy.retry.should_retry(attempt_number=1, error={"type": "timeout"})


def test_worker_policy_normalizes_retry_and_timeout_inputs() -> None:
    policy = normalize_worker_policy(
        {
            "retry": {
                "maxAttempts": "3",
                "retryableErrors": ["timeout", "worker-unavailable"],
            },
            "timeout": {
                "perAttemptSeconds": "2.5",
                "totalSeconds": 10,
            },
        }
    )

    assert policy.retry.max_attempts == 3
    assert policy.retry.retryable_errors == frozenset({"timeout", "worker_unavailable"})
    assert policy.timeout.per_attempt_seconds == 2.5
    assert policy.timeout.total_seconds == 10.0

    deadline = policy.timeout.deadline_from(100.0)
    assert deadline.expires_at == 110.0
    assert deadline.remaining(103.0) == 7.0
    assert deadline.remaining(111.0) == 0.0
    assert deadline.expired(111.0)
    assert policy.retry.should_retry(attempt_number=2, error={"type": "worker_unavailable"})
    assert not policy.retry.should_retry(attempt_number=3, error={"type": "timeout"})


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ({"retry": {"maxAttempts": 0}}, "retry.maxAttempts must be at least 1"),
        ({"timeout": {"perAttemptSeconds": -1}}, "timeout.perAttemptSeconds must be greater than 0"),
        ({"timeout": {"totalSeconds": 0}}, "timeout.totalSeconds must be greater than 0"),
        ({"retry": {"retryableErrors": [""]}}, "retry.retryableErrors entries must be non-empty"),
    ],
)
def test_worker_policy_rejects_invalid_inputs(raw: dict[str, object], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        normalize_worker_policy(raw)


def test_retryable_error_classification_handles_common_transient_failures() -> None:
    assert classify_retryable_error(WorkerTimeoutError("attempt timed out"))
    assert classify_retryable_error(TimeoutError("attempt timed out"))
    assert classify_retryable_error(ConnectionError("connection reset"))
    assert classify_retryable_error({"retryable": True})
    assert classify_retryable_error({"code": "RATE_LIMIT"})
    assert classify_retryable_error("worker-unavailable")

    assert not classify_retryable_error(ValueError("bad request"))
    assert not classify_retryable_error({"retryable": False, "type": "timeout"})
    assert not classify_retryable_error({"type": "validation_error"})

