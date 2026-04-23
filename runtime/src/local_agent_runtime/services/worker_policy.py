from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any


DEFAULT_RETRYABLE_ERRORS = frozenset(
    {
        "connection",
        "rate_limit",
        "timeout",
        "transient",
        "worker_unavailable",
    }
)


class WorkerTimeoutError(RuntimeError):
    """Raised by future runner integration when a worker attempt exceeds policy."""


@dataclass(frozen=True, slots=True)
class WorkerDeadline:
    expires_at: float | None = None

    def remaining(self, now: float) -> float | None:
        if self.expires_at is None:
            return None
        return max(0.0, self.expires_at - now)

    def expired(self, now: float) -> bool:
        return self.expires_at is not None and now >= self.expires_at


@dataclass(frozen=True, slots=True)
class WorkerRetryPolicy:
    max_attempts: int = 1
    retryable_errors: frozenset[str] = field(default_factory=lambda: DEFAULT_RETRYABLE_ERRORS)

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("retry.maxAttempts must be at least 1")
        if not self.retryable_errors:
            raise ValueError("retry.retryableErrors must include at least one entry")

    def should_retry(self, *, attempt_number: int, error: Any) -> bool:
        if attempt_number < 1:
            raise ValueError("attempt_number must be at least 1")
        return attempt_number < self.max_attempts and classify_retryable_error(
            error,
            retryable_errors=self.retryable_errors,
        )


@dataclass(frozen=True, slots=True)
class WorkerTimeoutPolicy:
    per_attempt_seconds: float | None = None
    total_seconds: float | None = None

    def __post_init__(self) -> None:
        _validate_optional_seconds(self.per_attempt_seconds, "timeout.perAttemptSeconds")
        _validate_optional_seconds(self.total_seconds, "timeout.totalSeconds")

    def deadline_from(self, now: float) -> WorkerDeadline:
        if self.total_seconds is None:
            return WorkerDeadline()
        return WorkerDeadline(expires_at=now + self.total_seconds)


@dataclass(frozen=True, slots=True)
class WorkerRunPolicy:
    retry: WorkerRetryPolicy = field(default_factory=WorkerRetryPolicy)
    timeout: WorkerTimeoutPolicy = field(default_factory=WorkerTimeoutPolicy)


def normalize_worker_policy(raw: Mapping[str, Any] | WorkerRunPolicy | None) -> WorkerRunPolicy:
    if isinstance(raw, WorkerRunPolicy):
        return raw
    if raw is None:
        return WorkerRunPolicy()
    if not isinstance(raw, Mapping):
        raise ValueError("worker policy must be a mapping")

    retry_raw = _first_present(raw, "retry", "retryPolicy")
    timeout_raw = _first_present(raw, "timeout", "timeoutPolicy")
    return WorkerRunPolicy(
        retry=normalize_retry_policy(retry_raw),
        timeout=normalize_timeout_policy(timeout_raw),
    )


def normalize_retry_policy(raw: Mapping[str, Any] | int | str | WorkerRetryPolicy | None) -> WorkerRetryPolicy:
    if isinstance(raw, WorkerRetryPolicy):
        return raw
    if raw is None:
        return WorkerRetryPolicy()
    if isinstance(raw, int | str) and not isinstance(raw, bool):
        return WorkerRetryPolicy(max_attempts=_coerce_positive_int(raw, "retry.maxAttempts"))
    if not isinstance(raw, Mapping):
        raise ValueError("retry policy must be a mapping or max attempts value")

    max_attempts = _coerce_positive_int(
        _first_present(raw, "maxAttempts", "max_attempts", "attempts", default=1),
        "retry.maxAttempts",
    )
    retryable_errors = _normalize_retryable_errors(
        _first_present(raw, "retryableErrors", "retryable_errors", default=DEFAULT_RETRYABLE_ERRORS)
    )
    return WorkerRetryPolicy(max_attempts=max_attempts, retryable_errors=retryable_errors)


def normalize_timeout_policy(
    raw: Mapping[str, Any] | int | float | str | WorkerTimeoutPolicy | None,
) -> WorkerTimeoutPolicy:
    if isinstance(raw, WorkerTimeoutPolicy):
        return raw
    if raw is None:
        return WorkerTimeoutPolicy()
    if isinstance(raw, int | float | str) and not isinstance(raw, bool):
        return WorkerTimeoutPolicy(total_seconds=_coerce_positive_seconds(raw, "timeout.totalSeconds"))
    if not isinstance(raw, Mapping):
        raise ValueError("timeout policy must be a mapping or total seconds value")

    per_attempt_seconds = _coerce_optional_seconds(
        _first_present(
            raw,
            "perAttemptSeconds",
            "per_attempt_seconds",
            "perAttemptTimeoutSeconds",
            "per_attempt_timeout_seconds",
            default=None,
        ),
        "timeout.perAttemptSeconds",
    )
    total_seconds = _coerce_optional_seconds(
        _first_present(
            raw,
            "totalSeconds",
            "total_seconds",
            "totalTimeoutSeconds",
            "total_timeout_seconds",
            default=None,
        ),
        "timeout.totalSeconds",
    )
    return WorkerTimeoutPolicy(
        per_attempt_seconds=per_attempt_seconds,
        total_seconds=total_seconds,
    )


def classify_retryable_error(
    error: Any,
    *,
    retryable_errors: Iterable[str] = DEFAULT_RETRYABLE_ERRORS,
) -> bool:
    retryable = frozenset(_normalize_token(entry) for entry in retryable_errors)
    if error is None:
        return False
    if isinstance(error, Mapping):
        explicit_retryable = error.get("retryable")
        if isinstance(explicit_retryable, bool):
            return explicit_retryable
        for key in ("type", "errorType", "error_type", "code", "status", "reason", "kind"):
            value = error.get(key)
            if value is not None and _normalize_token(str(value)) in retryable:
                return True
        return False
    if isinstance(error, str):
        return _normalize_token(error) in retryable
    if isinstance(error, WorkerTimeoutError | TimeoutError):
        return "timeout" in retryable
    if isinstance(error, ConnectionError):
        return "connection" in retryable
    if isinstance(error, BaseException):
        class_token = _normalize_token(error.__class__.__name__)
        if class_token in retryable:
            return True
        for attr in ("code", "status", "reason", "kind"):
            value = getattr(error, attr, None)
            if value is not None and _normalize_token(str(value)) in retryable:
                return True
    return False


def _first_present(raw: Mapping[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in raw:
            return raw[key]
    return default


def _normalize_retryable_errors(raw: Any) -> frozenset[str]:
    if isinstance(raw, str):
        entries = [raw]
    elif isinstance(raw, Iterable):
        entries = list(raw)
    else:
        raise ValueError("retry.retryableErrors must be a string or iterable")

    normalized = frozenset(_normalize_retryable_error(entry) for entry in entries)
    if not normalized:
        raise ValueError("retry.retryableErrors must include at least one entry")
    return normalized


def _normalize_retryable_error(raw: Any) -> str:
    token = _normalize_token(str(raw))
    if not token:
        raise ValueError("retry.retryableErrors entries must be non-empty")
    return token


def _normalize_token(raw: str) -> str:
    token = raw.strip().lower().replace("-", "_").replace(" ", "_")
    return "".join(character for character in token if character.isalnum() or character == "_")


def _coerce_positive_int(raw: Any, field_name: str) -> int:
    if isinstance(raw, bool):
        raise ValueError(f"{field_name} must be an integer")
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be an integer") from exc
    if value < 1:
        raise ValueError(f"{field_name} must be at least 1")
    return value


def _coerce_optional_seconds(raw: Any, field_name: str) -> float | None:
    if raw is None:
        return None
    return _coerce_positive_seconds(raw, field_name)


def _coerce_positive_seconds(raw: Any, field_name: str) -> float:
    if isinstance(raw, bool):
        raise ValueError(f"{field_name} must be greater than 0")
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be greater than 0") from exc
    if value <= 0:
        raise ValueError(f"{field_name} must be greater than 0")
    return value


def _validate_optional_seconds(value: float | None, field_name: str) -> None:
    if value is not None and value <= 0:
        raise ValueError(f"{field_name} must be greater than 0")

