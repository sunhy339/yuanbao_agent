"""Permission presets mapping capabilities to modes for each safety tier."""
from __future__ import annotations

from typing import Any

# Each capability maps to a rule dict: {"mode": CapabilityMode, "scope": str}
# scope is reserved for future path/pattern filtering; currently always "*".

_RULE = lambda mode: {"mode": mode, "scope": "*"}  # noqa: E731

SAFE_PRESET: dict[str, Any] = {
    "readFile": _RULE("allow"),
    "writeFile": _RULE("ask"),
    "runCommand": _RULE("ask"),
    "webFetch": _RULE("blocked"),
    "network": _RULE("blocked"),
    "subagents": _RULE("ask"),
    "memoryWrite": _RULE("allow"),
    "gitWrite": _RULE("ask"),
    "browserAutomation": _RULE("blocked"),
    "computerUse": _RULE("ask"),
    "hooksExecute": _RULE("ask"),
}

BALANCED_PRESET: dict[str, Any] = {
    "readFile": _RULE("allow"),
    "writeFile": _RULE("ask"),
    "runCommand": _RULE("ask"),
    "webFetch": _RULE("ask"),
    "network": _RULE("blocked"),
    "subagents": _RULE("ask"),
    "memoryWrite": _RULE("allow"),
    "gitWrite": _RULE("ask"),
    "browserAutomation": _RULE("blocked"),
    "computerUse": _RULE("ask"),
    "hooksExecute": _RULE("ask"),
}

AUTONOMOUS_PRESET: dict[str, Any] = {
    "readFile": _RULE("allow"),
    "writeFile": _RULE("allow"),
    "runCommand": _RULE("ask"),
    "webFetch": _RULE("ask"),
    "network": _RULE("ask"),
    "subagents": _RULE("allow"),
    "memoryWrite": _RULE("allow"),
    "gitWrite": _RULE("ask"),
    "browserAutomation": _RULE("blocked"),
    "computerUse": _RULE("ask"),
    "hooksExecute": _RULE("ask"),
}

PRESETS: dict[str, dict[str, Any]] = {
    "safe": SAFE_PRESET,
    "balanced": BALANCED_PRESET,
    "autonomous": AUTONOMOUS_PRESET,
}


def preset_to_config(
    preset_name: str,
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a full capabilities dict for *preset_name*, applying *overrides* on top.

    *overrides* keys are capability names; values are rule dicts or mode strings.
    """
    base = PRESETS.get(preset_name)
    if base is None:
        raise ValueError(f"Unknown permission preset: {preset_name!r}")
    capabilities: dict[str, Any] = {k: dict(v) for k, v in base.items()}
    if overrides:
        for cap, rule in overrides.items():
            if isinstance(rule, str):
                rule = _RULE(rule)
            capabilities[cap] = dict(rule)
    return {"preset": preset_name, "capabilities": capabilities}
