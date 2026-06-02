from __future__ import annotations

from typing import Any

from local_agent_runtime.tools.task import build_task_tool


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
        {"label": "child_task", "status": "completed", "summary": "task_child_1"},
    ]
