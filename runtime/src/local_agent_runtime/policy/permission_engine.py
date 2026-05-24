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

_HIGH_RISK_CAPABILITIES = {"writeFile", "runCommand", "subagents"}
_LOW_RISK_COMMAND_MARKERS = (
    "pytest",
    "py_compile",
    "unittest",
    "node --check",
    "tsc",
    "npm test",
    "pnpm test",
    "yarn test",
    "git status",
    "git diff",
    "git rev-parse",
    "git branch",
    "get-childitem",
    "get-content",
    "get-location",
)
_LOW_RISK_COMMAND_PREFIXES = (
    "ls",
    "dir",
    "pwd",
    "cat",
    "type",
    "rg",
    "findstr",
)


def collect_untrusted_content_signals(context: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(context, dict):
        return []
    raw = context.get("untrustedContentSignals")
    if isinstance(raw, list):
        return [dict(item) for item in raw if isinstance(item, dict)]
    tool_results = context.get("tool_results")
    if not isinstance(tool_results, list):
        return []
    signals: list[dict[str, Any]] = []
    for item in tool_results:
        if not isinstance(item, dict):
            continue
        result = item.get("result") if isinstance(item.get("result"), dict) else {}
        trust = str(result.get("contentTrust") or "").strip().lower()
        if trust != "untrusted":
            continue
        signal = {
            "toolName": str(item.get("name") or ""),
            "source": str(result.get("contentSource") or "external"),
            "reason": str(result.get("contentTrustReason") or "Untrusted external content was introduced earlier in this task."),
        }
        path = result.get("path")
        url = result.get("url")
        if isinstance(path, str) and path.strip():
            signal["path"] = path
        if isinstance(url, str) and url.strip():
            signal["url"] = url
        signals.append(signal)
    return signals


def _is_low_risk_command(command: str) -> bool:
    normalized = " ".join(command.strip().lower().replace("\\", "/").split())
    if not normalized:
        return False
    lowered = f" {normalized} "
    if any(marker in lowered for marker in _LOW_RISK_COMMAND_MARKERS):
        return True
    return any(normalized == prefix or normalized.startswith(f"{prefix} ") for prefix in _LOW_RISK_COMMAND_PREFIXES)


def _untrusted_content_guard(request: PermissionRequest) -> PermissionDecision | None:
    if request.capability not in _HIGH_RISK_CAPABILITIES:
        return None
    signals = collect_untrusted_content_signals(request.context)
    if not signals:
        return None
    if request.capability == "runCommand":
        command = str(request.context.get("command") or "").strip()
        if command and _is_low_risk_command(command):
            return None
    sources = ", ".join(sorted({str(item.get("source") or "external") for item in signals}))
    tool_label = request.tool_name or request.capability
    return PermissionDecision(
        decision="approval_required",
        capability=request.capability,
        reason=(
            f"Tool {tool_label!r} requires approval because this turn includes untrusted content "
            f"from {sources}. Review the requested action before allowing it."
        ),
        approval_kind=_CAPABILITY_APPROVAL_KIND.get(request.capability, request.capability),
    )


class PermissionEngine:
    """Evaluates tool capability requests against the active permission config."""

    def __init__(self, config: dict[str, Any], store: Any = None) -> None:
        normalized = normalize_permissions(config)
        self._preset: str = normalized.get("preset", "balanced")
        self._capabilities: dict[str, Any] = normalized.get("capabilities", {})
        self._store = store

    def evaluate(self, request: PermissionRequest) -> PermissionDecision:
        cap = request.capability
        untrusted_guard = _untrusted_content_guard(request)
        rule = self._capabilities.get(cap)
        if rule is None:
            # Unknown capability defaults to ask for safety
            return untrusted_guard or PermissionDecision(
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
            return untrusted_guard or PermissionDecision(
                decision="allow",
                capability=cap,
            )

        # mode == "ask"
        if cap == "runCommand":
            command = str(request.context.get("command") or "").strip()
            if command and _is_low_risk_command(command):
                return PermissionDecision(
                    decision="allow",
                    capability=cap,
                    reason="Low-risk inspection or verification command is allowed without an approval prompt.",
                )

        approval_kind = _CAPABILITY_APPROVAL_KIND.get(cap, cap)
        # Refine writeFile approval kind based on tool_name
        if cap == "writeFile" and request.tool_name == "write_file":
            approval_kind = "apply_patch"
        elif cap == "writeFile":
            approval_kind = "apply_patch"

        return untrusted_guard or PermissionDecision(
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
