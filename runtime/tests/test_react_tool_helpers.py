from __future__ import annotations

from local_agent_runtime.orchestrator.react_runner import ReactRunnerMixin
from local_agent_runtime.orchestrator.react_tool_helpers import ReactToolHelpersMixin


class _ToolHelperHarness(ReactToolHelpersMixin):
    def _child_subtask_timeout_ms(self, context: dict) -> int | None:
        return context.get("child_timeout_ms")


class _ReactRunnerHarness(ReactRunnerMixin):
    @staticmethod
    def _active_config_profile(config: dict, key: str) -> dict | None:
        group = config.get(key)
        if not isinstance(group, dict):
            return None
        profiles = group.get("profiles")
        active_id = group.get("activeProfileId")
        if isinstance(profiles, list):
            for profile in profiles:
                if isinstance(profile, dict) and profile.get("id") == active_id:
                    return profile
        return None


def test_workspace_tool_defaults_override_provider_workspace_root() -> None:
    arguments = {
        "workspaceRoot": r"D:\stale\or\model\invented",
        "workspace_root": r"D:\also-stale",
        "path": "orders.py",
    }
    context = {"workspace_root": r"D:\real\workspace"}

    _ToolHelperHarness()._fill_tool_defaults("read_file", arguments, context)

    assert arguments["workspaceRoot"] == context["workspace_root"]
    assert "workspace_root" not in arguments


def test_task_tool_defaults_inherit_child_timeout_budget() -> None:
    arguments = {"prompt": "Implement the module"}
    context = {"workspace_root": r"D:\real\workspace", "child_timeout_ms": 600_000}

    _ToolHelperHarness()._fill_tool_defaults("task", arguments, context)

    assert arguments["timeoutMs"] == 600_000


def test_task_tool_defaults_keep_explicit_timeout() -> None:
    arguments = {"prompt": "Implement the module", "timeoutMs": 120_000}
    context = {"workspace_root": r"D:\real\workspace", "child_timeout_ms": 600_000}

    _ToolHelperHarness()._fill_tool_defaults("task", arguments, context)

    assert arguments["timeoutMs"] == 120_000


def test_child_subtask_timeout_falls_back_to_policy_command_timeout() -> None:
    context = {
        "config": {
            "autonomy": {
                "activeProfileId": "long-run",
                "profiles": [{"id": "long-run", "maxSteps": 40}],
            },
            "policy": {"commandTimeoutMs": 900_000},
        }
    }

    assert _ReactRunnerHarness()._child_subtask_timeout_ms(context) == 900_000


def test_child_subtask_timeout_prefers_autonomy_profile_timeout() -> None:
    context = {
        "config": {
            "autonomy": {
                "activeProfileId": "long-run",
                "profiles": [{"id": "long-run", "timeoutMs": 700_000}],
            },
            "policy": {"commandTimeoutMs": 900_000},
        }
    }

    assert _ReactRunnerHarness()._child_subtask_timeout_ms(context) == 700_000
