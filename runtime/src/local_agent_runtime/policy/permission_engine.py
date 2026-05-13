"""PermissionEngine: unified policy evaluator for tool capabilities."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .config_normalizer import normalize_permissions
from .presets import preset_to_config


@dataclass
class PermissionRequest:
    capability: str
    tool_name: str = ""
    context: dict[str, Any] = field(default_factory=dict)


@dataclass
class PermissionDecision:
    decision: str  # "allow" | "deny" | "approval_required"
    capability: str
    reason: str = ""
    approval_kind: str = ""


# Capability → default ApprovalKind mapping
_CAPABILITY_APPROVAL_KIND: dict[str, str] = {
    "writeFile": "apply_patch",
    "runCommand": "run_command",
    "webFetch": "network_access",
    "network": "network_access",
    "subagents": "subagent_dispatch",
    "gitWrite": "apply_patch",
    "hooksExecute": "run_command",
}


class PermissionEngine:
    """Evaluates tool capability requests against the active permission config."""

    def __init__(self, config: dict[str, Any], store: Any = None) -> None:
        normalized = normalize_permissions(config)
        self._preset: str = normalized.get("preset", "balanced")
        self._capabilities: dict[str, Any] = normalized.get("capabilities", {})
        self._store = store

    def evaluate(self, request: PermissionRequest) -> PermissionDecision:
        cap = request.capability
        rule = self._capabilities.get(cap)
        if rule is None:
            # Unknown capability defaults to ask for safety
            return PermissionDecision(
                decision="approval_required",
                capability=cap,
                reason=f"Capability {cap!r} has no rule; defaulting to approval_required.",
                approval_kind=_CAPABILITY_APPROVAL_KIND.get(cap, cap),
            )

        mode = rule.get("mode", "ask") if isinstance(rule, dict) else str(rule)

        if mode == "blocked":
            return PermissionDecision(
                decision="deny",
                capability=cap,
                reason=f"Capability {cap!r} is blocked by policy preset {self._preset!r}.",
            )

        if mode == "allow":
            return PermissionDecision(
                decision="allow",
                capability=cap,
            )

        # mode == "ask"
        approval_kind = _CAPABILITY_APPROVAL_KIND.get(cap, cap)
        # Refine writeFile approval kind based on tool_name
        if cap == "writeFile" and request.tool_name == "write_file":
            approval_kind = "apply_patch"
        elif cap == "writeFile":
            approval_kind = "apply_patch"

        return PermissionDecision(
            decision="approval_required",
            capability=cap,
            reason=f"Capability {cap!r} requires approval (preset: {self._preset!r}).",
            approval_kind=approval_kind,
        )

    def effective_config(self) -> dict[str, Any]:
        return {"preset": self._preset, "capabilities": dict(self._capabilities)}

    def is_allowed(self, capability: str) -> bool:
        rule = self._capabilities.get(capability)
        if rule is None:
            return False
        mode = rule.get("mode", "ask") if isinstance(rule, dict) else str(rule)
        return mode == "allow"
