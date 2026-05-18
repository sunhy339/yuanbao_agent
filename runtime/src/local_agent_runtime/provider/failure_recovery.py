from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


_HTTP_STATUS_RE = re.compile(r"\bHTTP\s+(\d{3})\b", re.IGNORECASE)
_PLAIN_STATUS_RE = re.compile(r"\b(400|401|403|408|413|429|500|502|503|504)\b")


@dataclass(frozen=True, slots=True)
class ProviderFailureRecovery:
    category: str
    retryable: bool
    recoverable: bool
    recommended_action: str
    reason: str
    user_message: str
    http_status: int | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "category": self.category,
            "retryable": self.retryable,
            "recoverable": self.recoverable,
            "recommendedAction": self.recommended_action,
            "reason": self.reason,
            "userMessage": self.user_message,
        }
        if self.http_status is not None:
            payload["httpStatus"] = self.http_status
        return payload


def classify_provider_failure(error: BaseException | str) -> ProviderFailureRecovery:
    """Classify a provider failure into a conservative recovery decision."""
    message = str(error or "")
    lowered = message.casefold()
    http_status = _http_status(message)

    if _has_any(lowered, ("environment variable", "api key", "apikey", "auth token", "not set")):
        return _decision(
            "auth",
            http_status,
            retryable=False,
            recoverable=False,
            action="fix_provider_credentials",
            reason="provider credentials are missing or invalid",
            message=message,
        )
    if http_status in {401, 403} or _has_any(lowered, ("unauthorized", "authentication", "forbidden", "permission denied")):
        return _decision(
            "auth",
            http_status,
            retryable=False,
            recoverable=False,
            action="fix_provider_credentials",
            reason="provider rejected authentication or authorization",
            message=message,
        )
    if http_status == 429 or _has_any(lowered, ("rate limit", "too many requests", "throttle", "throttled")):
        return _decision(
            "rate_limit",
            http_status,
            retryable=True,
            recoverable=True,
            action="retry_with_backoff",
            reason="provider rate limited the request",
            message=message,
        )
    if http_status in {408, 504} or _has_any(
        lowered,
        (
            "timed out",
            "timeout",
            "deadline exceeded",
            "read timed out",
            "streaming response exceeded",
            "before completion",
        ),
    ):
        return _decision(
            "timeout",
            http_status,
            retryable=True,
            recoverable=True,
            action="retry",
            reason="provider request timed out before a complete response",
            message=message,
        )
    if http_status == 413 or _has_any(
        lowered,
        (
            "context length",
            "context_length_exceeded",
            "maximum context",
            "token limit",
            "too many tokens",
            "input is too long",
            "input too long",
            "payload too large",
            "request too large",
            "tool call arguments exceeded",
            "maximum request length",
        ),
    ):
        return _decision(
            "context_too_large",
            http_status,
            retryable=False,
            recoverable=True,
            action="compact_or_split_context",
            reason="request context or tool arguments exceed provider limits",
            message=message,
        )
    if _has_any(lowered, ("refused", "refusal", "content filter", "safety", "policy_violation", "not allowed")):
        return _decision(
            "refusal",
            http_status,
            retryable=False,
            recoverable=False,
            action="ask_user_or_change_request",
            reason="provider refused the request",
            message=message,
        )
    if http_status in {500, 502, 503} or _has_any(lowered, ("internal server error", "bad gateway", "service unavailable")):
        return _decision(
            "server_error",
            http_status,
            retryable=True,
            recoverable=True,
            action="retry_with_backoff",
            reason="provider returned a transient server failure",
            message=message,
        )
    if _has_any(lowered, ("connection", "reset", "unreachable", "dns", "name resolution", "temporary failure", "connection refused")):
        return _decision(
            "network",
            http_status,
            retryable=True,
            recoverable=True,
            action="retry",
            reason="network path to provider failed",
            message=message,
        )
    if _has_any(lowered, ("api format", "not supported", "unsupported")):
        return _decision(
            "unsupported_format",
            http_status,
            retryable=False,
            recoverable=True,
            action="fix_provider_api_format",
            reason="provider API format or feature is unsupported",
            message=message,
        )
    if _has_any(lowered, ("bad request", "invalid model", "invalid request", "unknown parameter", "validation", "parameter")) or http_status == 400:
        return _decision(
            "request_validation",
            http_status,
            retryable=False,
            recoverable=True,
            action="fix_provider_request",
            reason="provider rejected the request parameters",
            message=message,
        )
    if _has_any(lowered, ("invalid json", "non-utf-8", "invalid response", "missing choices", "invalid sse")):
        return _decision(
            "invalid_response",
            http_status,
            retryable=True,
            recoverable=True,
            action="retry",
            reason="provider returned a malformed response that may be transient",
            message=message,
        )
    return _decision(
        "unknown",
        http_status,
        retryable=False,
        recoverable=False,
        action="surface_error",
        reason="provider failure did not match a known recoverable pattern",
        message=message,
    )


def _decision(
    category: str,
    http_status: int | None,
    *,
    retryable: bool,
    recoverable: bool,
    action: str,
    reason: str,
    message: str,
) -> ProviderFailureRecovery:
    preview = " ".join(message.split())[:400] or reason
    return ProviderFailureRecovery(
        category=category,
        retryable=retryable,
        recoverable=recoverable,
        recommended_action=action,
        reason=reason,
        user_message=preview,
        http_status=http_status,
    )


def _http_status(message: str) -> int | None:
    match = _HTTP_STATUS_RE.search(message)
    if match:
        return int(match.group(1))
    match = _PLAIN_STATUS_RE.search(message)
    return int(match.group(1)) if match else None


def _has_any(text: str, markers: tuple[str, ...]) -> bool:
    return any(marker in text for marker in markers)
