"""P0: AutonomyProfile — configurable, auditable agent autonomy levels.

Defines autonomy profiles L0 through L4, each specifying bounds for:
- max_steps: ReAct loop iteration limit
- context_budget: maximum context tokens
- compaction_threshold: tokens before compaction triggers
- background_execution: allow background commands
- subagents: allow subagent dispatch
- shell: allow shell command execution
- file_writes: allow file modifications
- network: allow network access (web fetch)
- approval_mode: "none", "high_risk", "write", or "all"
- memory_recall_policy: "none", "auto", or "explicit"
- retry_limits: max retries on failure
- timeout_seconds: task timeout

Usage:
    profile = AutonomyProfiles.get("balanced")
    task["autonomy_snapshot"] = profile.to_dict()
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal


ApprovalMode = Literal["none", "high_risk", "write", "all"]
MemoryRecallPolicy = Literal["none", "auto", "explicit"]


@dataclass(frozen=True)
class AutonomyProfile:
    """A single autonomy level configuration."""

    id: str
    name: str
    level: int  # 0-4
    description: str
    max_steps: int
    context_budget: int
    compaction_threshold: int
    background_execution: bool
    subagents: bool
    shell: bool
    file_writes: bool
    network: bool
    approval_mode: ApprovalMode
    memory_recall_policy: MemoryRecallPolicy
    retry_limits: int
    timeout_seconds: int

    def to_dict(self) -> dict[str, Any]:
        """Serialize for task snapshot persistence."""
        return asdict(self)


# ---------------------------------------------------------------------------
# Built-in profiles
# ---------------------------------------------------------------------------

PROFILES: dict[str, AutonomyProfile] = {
    "locked_down": AutonomyProfile(
        id="locked_down",
        name="Locked Down",
        level=0,
        description="No autonomous actions. Every step requires explicit user approval.",
        max_steps=5,
        context_budget=64000,
        compaction_threshold=32000,
        background_execution=False,
        subagents=False,
        shell=False,
        file_writes=False,
        network=False,
        approval_mode="all",
        memory_recall_policy="explicit",
        retry_limits=0,
        timeout_seconds=120,
    ),
    "conservative": AutonomyProfile(
        id="conservative",
        name="Conservative",
        level=1,
        description="Read-only analysis with minimal tool use. Writes and shell require approval.",
        max_steps=15,
        context_budget=128000,
        compaction_threshold=60000,
        background_execution=False,
        subagents=False,
        shell=False,
        file_writes=False,
        network=True,
        approval_mode="write",
        memory_recall_policy="auto",
        retry_limits=1,
        timeout_seconds=300,
    ),
    "balanced": AutonomyProfile(
        id="balanced",
        name="Balanced",
        level=2,
        description="Standard execution. Writes allowed, high-risk actions need approval.",
        max_steps=30,
        context_budget=256000,
        compaction_threshold=120000,
        background_execution=True,
        subagents=True,
        shell=True,
        file_writes=True,
        network=True,
        approval_mode="high_risk",
        memory_recall_policy="auto",
        retry_limits=3,
        timeout_seconds=600,
    ),
    "extended": AutonomyProfile(
        id="extended",
        name="Extended",
        level=3,
        description="Extended autonomy. More steps and budget. Only critical risks need approval.",
        max_steps=50,
        context_budget=512000,
        compaction_threshold=200000,
        background_execution=True,
        subagents=True,
        shell=True,
        file_writes=True,
        network=True,
        approval_mode="high_risk",
        memory_recall_policy="auto",
        retry_limits=5,
        timeout_seconds=1200,
    ),
    "autonomous": AutonomyProfile(
        id="autonomous",
        name="Fully Autonomous",
        level=4,
        description="Full autonomy. No approval gates. Use only in trusted environments.",
        max_steps=100,
        context_budget=1000000,
        compaction_threshold=400000,
        background_execution=True,
        subagents=True,
        shell=True,
        file_writes=True,
        network=True,
        approval_mode="none",
        memory_recall_policy="auto",
        retry_limits=10,
        timeout_seconds=3600,
    ),
}


# ---------------------------------------------------------------------------
# Lookup helpers
# ---------------------------------------------------------------------------

def get_autonomy_profile(profile_id: str) -> AutonomyProfile | None:
    """Look up a profile by id."""
    return PROFILES.get(profile_id)


def list_autonomy_profiles() -> list[AutonomyProfile]:
    """Return all profiles sorted by level."""
    return sorted(PROFILES.values(), key=lambda p: p.level)


def default_autonomy_profile() -> AutonomyProfile:
    """Return the default profile (balanced)."""
    return PROFILES["balanced"]


def profile_from_dict(data: dict[str, Any]) -> AutonomyProfile:
    """Reconstruct an AutonomyProfile from a persisted dict."""
    return AutonomyProfile(
        id=data.get("id", "unknown"),
        name=data.get("name", "Unknown"),
        level=data.get("level", 0),
        description=data.get("description", ""),
        max_steps=data.get("max_steps", 30),
        context_budget=data.get("context_budget", 256000),
        compaction_threshold=data.get("compaction_threshold", 120000),
        background_execution=data.get("background_execution", False),
        subagents=data.get("subagents", True),
        shell=data.get("shell", True),
        file_writes=data.get("file_writes", True),
        network=data.get("network", True),
        approval_mode=data.get("approval_mode", "high_risk"),
        memory_recall_policy=data.get("memory_recall_policy", "auto"),
        retry_limits=data.get("retry_limits", 3),
        timeout_seconds=data.get("timeout_seconds", 600),
    )
