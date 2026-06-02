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
    "computerUse": "computer_use",
    "subagents": "subagent_dispatch",
    "gitWrite": "apply_patch",
    "hooksExecute": "run_command",
}

_HIGH_RISK_CAPABILITIES = {"writeFile", "runCommand", "subagents"}

_VERIFICATION_COMMAND_MARKERS = (
    "pytest",
    "unittest",
    "npm test",
    "npm run test",
    "pnpm test",
    "yarn test",
    "cargo test",
    "go test",
    "mvn test",
    "gradle test",
    " tsc",
    "tsc ",
    "npm run build",
    "pnpm build",
    "yarn build",
    "python -m py_compile",
    "python -m compileall",
)
_READ_ONLY_COMMAND_PREFIXES = (
    "git status",
    "git diff",
    "git log",
    "git show",
    "get-childitem",
    "ls ",
    "dir ",
    "pwd",
)
_COMMAND_CHAIN_OR_REDIRECT_MARKERS = ("&&", "||", ";", ">", "<", "|")


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


def _untrusted_content_guard(request: PermissionRequest) -> PermissionDecision | None:
    if request.capability not in _HIGH_RISK_CAPABILITIES:
        return None
    signals = collect_untrusted_content_signals(request.context)
    if not signals:
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


def _is_low_risk_command(request: PermissionRequest) -> bool:
    if request.capability != "runCommand":
        return False
    command = str(request.context.get("command") or "").strip()
    if not command:
        return False
    command_lower = " ".join(command.casefold().split())
    padded = f" {command_lower} "
    if any(marker in command_lower for marker in _COMMAND_CHAIN_OR_REDIRECT_MARKERS):
        return False
    if any(command_lower == prefix.strip() or command_lower.startswith(prefix) for prefix in _READ_ONLY_COMMAND_PREFIXES):
        return True
    return any(marker in padded for marker in _VERIFICATION_COMMAND_MARKERS)


class PermissionEngine:
    """Evaluates tool capability requests against the active permission config."""

    def __init__(self, config: dict[str, Any], store: Any = None) -> None:
        self._store = store
        self._apply_config(config)

    def _apply_config(self, config: dict[str, Any]) -> None:
        policy = config.get("policy") if isinstance(config, dict) else {}
        self._approval_mode = (
            str(policy.get("approvalMode") or "").strip().lower()
            if isinstance(policy, dict)
            else ""
        )
        normalized = normalize_permissions(config)
        self._preset: str = normalized.get("preset", "balanced")
        self._capabilities: dict[str, Any] = normalized.get("capabilities", {})

    def _approvals_disabled(self) -> bool:
        return self._approval_mode in {"none", "never", "off", "no", "false"}

    def _refresh_from_store(self) -> None:
        if self._store is None:
            return
        try:
            result = self._store.get_config({})
        except Exception:  # noqa: BLE001
            return
        config = result.get("config") if isinstance(result, dict) else None
        if isinstance(config, dict):
            self._apply_config(config)

    def evaluate(self, request: PermissionRequest) -> PermissionDecision:
        self._refresh_from_store()
        cap = request.capability
        untrusted_guard = _untrusted_content_guard(request)
        low_risk_command = _is_low_risk_command(request)
        rule = self._capabilities.get(cap)
        if rule is None:
            # Unknown capability defaults to ask for safety
            if self._approvals_disabled():
                return PermissionDecision(decision="allow", capability=cap)
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
            if self._approvals_disabled() or low_risk_command:
                return PermissionDecision(decision="allow", capability=cap)
            return untrusted_guard or PermissionDecision(
                decision="allow",
                capability=cap,
            )

        # mode == "ask"
        if self._approvals_disabled() or low_risk_command:
            return PermissionDecision(decision="allow", capability=cap)

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
        self._refresh_from_store()
        return {"preset": self._preset, "capabilities": dict(self._capabilities)}

    def is_allowed(self, capability: str) -> bool:
        self._refresh_from_store()
        rule = self._capabilities.get(capability)
        if rule is None:
            return False
        mode = rule.get("mode", "ask") if isinstance(rule, dict) else str(rule)
        return mode == "allow"
