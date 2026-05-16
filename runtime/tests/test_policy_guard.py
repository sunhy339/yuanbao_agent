from __future__ import annotations

from local_agent_runtime.policy.guard import PolicyGuard


def test_off_approval_mode_allows_write_and_command_tools() -> None:
    guard = PolicyGuard(approval_mode="on_write_or_command")

    assert guard.requires_approval("write_file", approval_mode="off") is False
    assert guard.requires_approval("apply_patch", approval_mode="never") is False
    assert guard.requires_approval("run_command", approval_mode="none") is False

