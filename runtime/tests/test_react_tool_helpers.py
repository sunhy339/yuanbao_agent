from __future__ import annotations

from local_agent_runtime.orchestrator.react_tool_helpers import ReactToolHelpersMixin


class _ToolHelperHarness(ReactToolHelpersMixin):
    def _child_subtask_timeout_ms(self, context: dict) -> int | None:
        return context.get("child_timeout_ms")


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
