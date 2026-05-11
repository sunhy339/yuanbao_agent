"""Tests for P0: AutonomyProfile definitions and lookup."""

from __future__ import annotations

from local_agent_runtime.policy.autonomy_profile import (
    PROFILES,
    AutonomyProfile,
    default_autonomy_profile,
    get_autonomy_profile,
    list_autonomy_profiles,
    profile_from_dict,
)


class TestAutonomyProfileDefinitions:
    """All 5 built-in profiles are well-formed."""

    def test_all_five_profiles_exist(self) -> None:
        ids = set(PROFILES.keys())
        assert ids == {"locked_down", "conservative", "balanced", "extended", "autonomous"}

    def test_levels_are_0_through_4(self) -> None:
        levels = {p.level for p in PROFILES.values()}
        assert levels == {0, 1, 2, 3, 4}

    def test_each_profile_has_required_fields(self) -> None:
        for p in PROFILES.values():
            assert p.id
            assert p.name
            assert 0 <= p.level <= 4
            assert p.max_steps > 0
            assert p.context_budget > 0
            assert p.compaction_threshold > 0
            assert p.approval_mode in ("none", "high_risk", "write", "all")
            assert p.memory_recall_policy in ("none", "auto", "explicit")
            assert p.retry_limits >= 0
            assert p.timeout_seconds > 0

    def test_profiles_are_frozen(self) -> None:
        p = get_autonomy_profile("balanced")
        assert p is not None
        # Frozen dataclass should raise on attribute set
        attempted = False
        try:
            p.max_steps = 999  # type: ignore[misc]
        except AttributeError:
            attempted = True
        assert attempted

    def test_level_ordering_monotonic(self) -> None:
        """Higher level profiles have higher or equal capability limits."""
        profiles = list_autonomy_profiles()
        for i in range(len(profiles) - 1):
            assert profiles[i].level < profiles[i + 1].level
            assert profiles[i].max_steps <= profiles[i + 1].max_steps
            assert profiles[i].context_budget <= profiles[i + 1].context_budget
            assert profiles[i].retry_limits <= profiles[i + 1].retry_limits


class TestAutonomyProfileLookup:
    """Lookup helpers work correctly."""

    def test_get_known_profile(self) -> None:
        p = get_autonomy_profile("balanced")
        assert p is not None
        assert p.level == 2

    def test_get_unknown_returns_none(self) -> None:
        assert get_autonomy_profile("nonexistent") is None

    def test_list_profiles_sorted_by_level(self) -> None:
        profiles = list_autonomy_profiles()
        levels = [p.level for p in profiles]
        assert levels == [0, 1, 2, 3, 4]

    def test_default_is_balanced(self) -> None:
        p = default_autonomy_profile()
        assert p.id == "balanced"
        assert p.level == 2


class TestAutonomyProfileSerialization:
    """to_dict and profile_from_dict roundtrip correctly."""

    def test_roundtrip(self) -> None:
        original = get_autonomy_profile("autonomous")
        assert original is not None
        d = original.to_dict()
        restored = profile_from_dict(d)
        assert restored == original

    def test_to_dict_has_all_fields(self) -> None:
        p = get_autonomy_profile("conservative")
        assert p is not None
        d = p.to_dict()
        expected_keys = {
            "id", "name", "level", "description", "max_steps",
            "context_budget", "compaction_threshold", "background_execution",
            "subagents", "shell", "file_writes", "network", "approval_mode",
            "memory_recall_policy", "retry_limits", "timeout_seconds",
        }
        assert set(d.keys()) == expected_keys

    def test_from_dict_defaults_missing_fields(self) -> None:
        partial = {"id": "custom", "name": "Custom"}
        p = profile_from_dict(partial)
        assert p.id == "custom"
        assert p.name == "Custom"
        assert p.level == 0
        assert p.max_steps == 30  # default


class TestAutonomyProfileConstraints:
    """Profile-specific constraints are correct."""

    def test_locked_down_no_autonomous_actions(self) -> None:
        p = get_autonomy_profile("locked_down")
        assert p is not None
        assert p.background_execution is False
        assert p.subagents is False
        assert p.shell is False
        assert p.file_writes is False
        assert p.network is False
        assert p.approval_mode == "all"
        assert p.retry_limits == 0

    def test_autonomous_no_approvals(self) -> None:
        p = get_autonomy_profile("autonomous")
        assert p is not None
        assert p.approval_mode == "none"
        assert p.background_execution is True
        assert p.subagents is True
        assert p.shell is True
        assert p.file_writes is True
        assert p.network is True

    def test_conservative_no_writes_no_shell(self) -> None:
        p = get_autonomy_profile("conservative")
        assert p is not None
        assert p.file_writes is False
        assert p.shell is False
        assert p.subagents is False
        assert p.approval_mode == "write"

    def test_balanced_allows_writes(self) -> None:
        p = get_autonomy_profile("balanced")
        assert p is not None
        assert p.file_writes is True
        assert p.shell is True
        assert p.approval_mode == "high_risk"
