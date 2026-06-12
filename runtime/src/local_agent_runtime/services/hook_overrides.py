"""HookOverrides — aggregated hook control-flow results.

Mirrors haha-cc's `runPreToolUseHooks` / `runPostToolUseHooks` output
(see docs/cc-haha-main/src/services/tools/toolHooks.ts:435-650).

Aggregation rules:
- permission_decision: first-wins (first deny short-circuits)
- updated_input / replace_output: last-wins (priority order, last overwrites)
- additional_contexts: merge (append)
- prevent_continuation / blocking_error: first-wins (any hook setting it terminates)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass
class HookOverrides:
    """Aggregated hook control-flow decisions that can alter the tool pipeline."""

    permission_decision: Literal["allow", "deny", "ask"] | None = None
    permission_reason: str | None = None
    updated_input: dict[str, Any] | None = None
    replace_output: dict[str, Any] | None = None
    additional_contexts: list[str] = field(default_factory=list)
    prevent_continuation: bool = False
    stop_reason: str | None = None
    retry: bool = False
    blocking_error: str | None = None

    def merge(self, other: HookOverrides) -> HookOverrides:
        """Merge another HookOverrides into this one following aggregation rules.

        Returns self for chaining.
        """
        # permission_decision: first-wins (deny beats allow, first non-None wins)
        if self.permission_decision is None and other.permission_decision is not None:
            self.permission_decision = other.permission_decision
            if other.permission_reason is not None:
                self.permission_reason = other.permission_reason
        elif other.permission_decision == "deny" and self.permission_decision != "deny":
            # deny always wins
            self.permission_decision = "deny"
            if other.permission_reason is not None:
                self.permission_reason = other.permission_reason

        # updated_input / replace_output: last-wins (deep merge for dicts)
        if other.updated_input is not None:
            if self.updated_input is None:
                self.updated_input = dict(other.updated_input)
            else:
                self.updated_input.update(other.updated_input)

        if other.replace_output is not None:
            if self.replace_output is None:
                self.replace_output = dict(other.replace_output)
            else:
                self.replace_output.update(other.replace_output)

        # additional_contexts: merge (append, dedupe)
        for ctx in other.additional_contexts:
            if ctx not in self.additional_contexts:
                self.additional_contexts.append(ctx)

        # prevent_continuation / blocking_error: first-wins
        if other.prevent_continuation and not self.prevent_continuation:
            self.prevent_continuation = True
        if self.blocking_error is None and other.blocking_error is not None:
            self.blocking_error = other.blocking_error

        # stop_reason: last-wins if set
        if other.stop_reason is not None:
            self.stop_reason = other.stop_reason

        # retry: first-wins
        if other.retry and not self.retry:
            self.retry = True

        return self

    @property
    def denied(self) -> bool:
        return self.permission_decision == "deny"

    @property
    def allowed(self) -> bool:
        return self.permission_decision == "allow"

    @property
    def should_ask(self) -> bool:
        return self.permission_decision == "ask"

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        if self.permission_decision is not None:
            result["permissionDecision"] = self.permission_decision
        if self.permission_reason is not None:
            result["permissionReason"] = self.permission_reason
        if self.updated_input is not None:
            result["updatedInput"] = self.updated_input
        if self.replace_output is not None:
            result["replaceOutput"] = self.replace_output
        if self.additional_contexts:
            result["additionalContexts"] = list(self.additional_contexts)
        if self.prevent_continuation:
            result["preventContinuation"] = True
        if self.stop_reason is not None:
            result["stopReason"] = self.stop_reason
        if self.retry:
            result["retry"] = True
        if self.blocking_error is not None:
            result["blockingError"] = self.blocking_error
        return result
