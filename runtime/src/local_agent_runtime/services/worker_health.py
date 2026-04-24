from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

WorkerHealthState = Literal["healthy", "stale", "offline"]

_OFFLINE_STATUSES = {"offline", "stopped", "failed"}


@dataclass(frozen=True, slots=True)
class WorkerHealthPolicy:
    stale_after_ms: int = 30_000
    offline_after_ms: int = 120_000

    def __post_init__(self) -> None:
        if self.stale_after_ms < 0:
            raise ValueError("stale_after_ms must be non-negative")
        if self.offline_after_ms < self.stale_after_ms:
            raise ValueError("offline_after_ms must be greater than or equal to stale_after_ms")


DEFAULT_WORKER_HEALTH_POLICY = WorkerHealthPolicy()


def assess_worker_health(
    worker: dict[str, Any],
    *,
    now_ms: int,
    policy: WorkerHealthPolicy = DEFAULT_WORKER_HEALTH_POLICY,
) -> dict[str, Any]:
    status = worker.get("status") if isinstance(worker.get("status"), str) else ""
    last_heartbeat_at = _int_or_none(worker.get("lastHeartbeatAt"))
    heartbeat_age_ms = None if last_heartbeat_at is None else max(0, now_ms - last_heartbeat_at)

    if status in _OFFLINE_STATUSES:
        state: WorkerHealthState = "offline"
        reason = f"worker_status_{status}"
    elif last_heartbeat_at is None:
        state = "offline"
        reason = "heartbeat_missing"
    elif heartbeat_age_ms is not None and heartbeat_age_ms >= policy.offline_after_ms:
        state = "offline"
        reason = "heartbeat_timeout"
    elif heartbeat_age_ms is not None and heartbeat_age_ms >= policy.stale_after_ms:
        state = "stale"
        reason = "heartbeat_stale"
    else:
        state = "healthy"
        reason = "heartbeat_recent"

    return {
        "state": state,
        "reason": reason,
        "assessedAt": now_ms,
        "lastHeartbeatAt": last_heartbeat_at,
        "heartbeatAgeMs": heartbeat_age_ms,
        "staleAfterMs": policy.stale_after_ms,
        "offlineAfterMs": policy.offline_after_ms,
    }


def enrich_worker(
    worker: dict[str, Any],
    *,
    now_ms: int,
    policy: WorkerHealthPolicy = DEFAULT_WORKER_HEALTH_POLICY,
) -> dict[str, Any]:
    enriched = dict(worker)
    health = assess_worker_health(enriched, now_ms=now_ms, policy=policy)
    enriched["healthState"] = health["state"]
    enriched["health"] = health
    return enriched


def summarize_worker_health(workers: list[dict[str, Any]]) -> dict[str, int]:
    summary = {"healthy": 0, "stale": 0, "offline": 0, "total": len(workers)}
    for worker in workers:
        state = worker.get("healthState")
        if state in ("healthy", "stale", "offline"):
            summary[state] += 1
    return summary


def _int_or_none(value: Any) -> int | None:
    return value if isinstance(value, int) else None
