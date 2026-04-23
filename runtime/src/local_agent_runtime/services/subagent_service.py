from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from .worker_environment import normalize_child_tool_allowlist
from .worker_runner import ChildTaskRequest, WorkerRunner


class SubagentService:
    """Runtime-native subagent dispatch facade.

    The dispatch method normalizes tool parameters and hands off child-task
    execution to a dedicated runner boundary.
    """

    def __init__(self, store: Any, collaboration: Any, runner: WorkerRunner | None = None) -> None:
        self._store = store
        self._collaboration = collaboration
        self._worker_runner = runner or WorkerRunner(collaboration)

    def dispatch(self, params: dict[str, Any]) -> dict[str, Any]:
        prompt = self._require_non_empty(params, "prompt")
        request = ChildTaskRequest(
            prompt=prompt,
            title=self._optional_string(params, "title") or self._title_from_prompt(prompt),
            agent_type=self._optional_string(params, "agentType")
            or self._optional_string(params, "agent_type")
            or "explorer",
            priority=self._priority(params.get("priority", 3)),
            session_id=self._optional_string(params, "sessionId"),
            parent_runtime_task_id=self._optional_string(params, "taskId"),
            timeout_ms=self._optional_timeout_ms(params),
            retry=self._optional_object(params, "retry"),
            cancellation=self._optional_object(params, "cancellation"),
            budget=self._budget_with_child_tool_allowlist(params),
        )
        return self._worker_runner.run_child_task(request)

    def _title_from_prompt(self, prompt: str) -> str:
        normalized = " ".join(prompt.split())
        return normalized[:80] if normalized else "Subagent task"

    def _require_non_empty(self, params: dict[str, Any], key: str) -> str:
        value = params.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{key} is required")
        return value.strip()

    def _optional_string(self, params: dict[str, Any], key: str) -> str | None:
        value = params.get(key)
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError(f"{key} must be a string")
        stripped = value.strip()
        return stripped or None

    def _optional_object(self, params: dict[str, Any], key: str) -> dict[str, Any] | None:
        value = params.get(key)
        if value is None:
            return None
        if not isinstance(value, dict):
            raise ValueError(f"{key} must be an object")
        return dict(value)

    def _budget_with_child_tool_allowlist(self, params: dict[str, Any]) -> dict[str, Any] | None:
        budget = self._optional_object(params, "budget") or {}
        raw_allowlist = self._first_present(
            params,
            "childToolAllowlist",
            "child_tool_allowlist",
        )
        if raw_allowlist is None:
            raw_allowlist = self._pop_first_present(
                budget,
                "childToolAllowlist",
                "child_tool_allowlist",
                "toolAllowlist",
                "tool_allowlist",
            )
        else:
            self._remove_allowlist_aliases(budget)

        if raw_allowlist is not None:
            budget["childToolAllowlist"] = list(self._normalize_child_tool_allowlist(raw_allowlist))
        return budget or None

    def _normalize_child_tool_allowlist(self, value: Any) -> tuple[str, ...]:
        if isinstance(value, str):
            return normalize_child_tool_allowlist(value)
        if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
            return normalize_child_tool_allowlist(value)
        raise ValueError("childToolAllowlist must be an array of tool names or a comma-separated string")

    def _first_present(self, params: dict[str, Any], *keys: str) -> Any:
        for key in keys:
            if key in params and params[key] is not None:
                return params[key]
        return None

    def _pop_first_present(self, params: dict[str, Any], *keys: str) -> Any:
        found = None
        found_set = False
        for key in keys:
            if key not in params:
                continue
            value = params.pop(key)
            if not found_set and value is not None:
                found = value
                found_set = True
        return found

    def _remove_allowlist_aliases(self, params: dict[str, Any]) -> None:
        for key in ("childToolAllowlist", "child_tool_allowlist", "toolAllowlist", "tool_allowlist"):
            params.pop(key, None)

    def _optional_timeout_ms(self, params: dict[str, Any]) -> int | None:
        value = params.get("timeoutMs")
        if value is None:
            return None
        try:
            timeout_ms = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("timeoutMs must be an integer") from exc
        if timeout_ms < 1:
            raise ValueError("timeoutMs must be positive")
        return timeout_ms

    def _priority(self, value: Any) -> int:
        try:
            return max(0, min(int(value), 9))
        except (TypeError, ValueError) as exc:
            raise ValueError("priority must be an integer") from exc
