from __future__ import annotations

from typing import Any

from local_agent_runtime.tools import build_builtin_tools
from local_agent_runtime.tools._schemas import BUILTIN_TOOL_SCHEMAS_BY_NAME
from local_agent_runtime.tools.task import build_agent_tool, build_task_tool, normalize_agent_tool_params


class StubSubagentService:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def dispatch(self, params: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(params)
        return {
            "status": "completed",
            "summary": "Inspected module.",
            "childTaskId": "task_child_1",
        }


def test_task_result_includes_runtime_steps() -> None:
    service = StubSubagentService()
    task_tool = build_task_tool(
        policy_guard=None,
        store=None,
        subagent_service=service,
    )["handler"]

    result = task_tool(
        {
            "taskId": "task_parent",
            "title": "Inspect inventory",
            "prompt": "Inspect the inventory module.",
        }
    )

    assert service.calls
    assert result["status"] == "completed"
    assert result["steps"] == [
        {"label": "prepare", "status": "completed", "summary": "Inspect inventory"},
        {"label": "dispatch", "status": "completed", "summary": "Inspected module."},
        {"label": "child_task", "status": "completed", "summary": "Inspected module."},
    ]


def test_agent_tool_normalizes_claude_code_style_arguments() -> None:
    service = StubSubagentService()
    agent_tool = build_agent_tool(
        policy_guard=None,
        store=None,
        subagent_service=service,
    )["handler"]

    result = agent_tool(
        {
            "taskId": "task_parent",
            "prompt": "Review the Snake docs and report gaps.",
            "agent_type": "reviewer",
            "tool_allowlist": ["read_file", "search_files"],
            "cwd": "D:/py/yuanbao_agent",
            "mode": "read_only",
            "plan_mode_required": True,
            "budget": {"maxTokens": 2000},
        }
    )

    assert result["status"] == "completed"
    assert service.calls
    params = service.calls[0]
    assert params["agentType"] == "reviewer"
    assert params["agent_type"] == "reviewer"
    assert params["title"] == "reviewer"
    assert params["childToolAllowlist"] == ["read_file", "search_files"]
    assert params["child_tool_allowlist"] == ["read_file", "search_files"]
    assert params["budget"]["maxTokens"] == 2000
    assert params["budget"]["childToolAllowlist"] == ["read_file", "search_files"]
    assert params["profile"] == {
        "cwd": "D:/py/yuanbao_agent",
        "mode": "read_only",
        "planModeRequired": True,
        "source": "agent_tool",
    }


def test_normalize_agent_tool_params_supports_aliases_without_mutating_input() -> None:
    original = {
        "prompt": "Inspect docs.",
        "agentType": "analyst",
        "toolAllowlist": "read_file,search_files",
        "planModeRequired": False,
    }

    normalized = normalize_agent_tool_params(original)

    assert original == {
        "prompt": "Inspect docs.",
        "agentType": "analyst",
        "toolAllowlist": "read_file,search_files",
        "planModeRequired": False,
    }
    assert normalized["agentType"] == "analyst"
    assert normalized["agent_type"] == "analyst"
    assert normalized["childToolAllowlist"] == "read_file,search_files"
    assert normalized["child_tool_allowlist"] == "read_file,search_files"
    assert normalized["profile"]["planModeRequired"] is False


def test_agent_tool_is_registered_with_builtin_schema() -> None:
    tools = build_builtin_tools(
        policy_guard=None,
        store=None,
        subagent_service=StubSubagentService(),
    )

    assert "agent" in tools
    assert "task" in tools
    schema = BUILTIN_TOOL_SCHEMAS_BY_NAME["agent"]
    assert schema["input_schema"]["required"] == ["prompt"]
    assert "agent_type" in schema["input_schema"]["properties"]
    assert "tool_allowlist" in schema["input_schema"]["properties"]
    assert "budget" not in schema["input_schema"]["properties"]
    task_schema = BUILTIN_TOOL_SCHEMAS_BY_NAME["task"]
    assert task_schema["input_schema"]["required"] == ["prompt"]
    assert "budget" not in task_schema["input_schema"]["properties"]
