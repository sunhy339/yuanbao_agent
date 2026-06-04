from __future__ import annotations

from local_agent_runtime.planner.service import Planner


def test_planner_creates_goal_specific_chinese_code_change_steps() -> None:
    planner = Planner()

    goal = (
        "\u591a agent \u534f\u4f5c\u5b8c\u6210\uff0c"
        "\u5c06\u8d2a\u5403\u86c7\u7684\u80cc\u666f\u6dfb\u52a0\u591a\u4e2a\uff0c"
        "\u7136\u540e\u6dfb\u52a0ai \u8d2a\u5403\u86c7\uff0c\u4e0e\u6211\u8fdb\u884c\u5bf9\u6bd4"
    )
    plan = planner.plan(goal, context={"workspace_name": "test_pro"})

    assert [step["id"] for step in plan] == [
        "inspect-workspace",
        "search-relevant-files",
        "apply-patch",
        "run-command",
        "summarize-findings",
    ]
    assert plan[0]["title"] == "\u7406\u89e3\u4efb\u52a1\u76ee\u6807"
    assert plan[1]["title"] == "\u5b9a\u4f4d\u76f8\u5173\u6587\u4ef6"
    assert plan[2]["title"] == "\u5b8c\u6210\u76ee\u6807\u6539\u52a8"
    assert plan[3]["title"] == "\u9a8c\u8bc1\u6539\u52a8\u6548\u679c"
    assert plan[0]["status"] == "active"
    assert plan[2]["status"] == "pending"
    assert "test_pro" in plan[0]["detail"]


def test_planner_keeps_command_plan_specific_to_command_execution() -> None:
    planner = Planner()

    plan = planner.plan("run command: npm test", context={"workspace_name": "app"})

    assert [step["id"] for step in plan] == ["inspect-workspace", "run-command", "summarize-findings"]
    assert plan[1]["title"] == "Run approved command"
    assert "npm test" in plan[1]["detail"]


def test_planner_skips_scaffold_for_free_form_status_question() -> None:
    planner = Planner()

    plan = planner.plan(
        "检查一下当前的进展吧",
        context={"workspace_name": "test_pro", "routing": {"scenario": "free_form", "strategy": "react_standard"}},
    )

    assert plan == []


def test_planner_keeps_plan_for_free_form_code_change_goal() -> None:
    planner = Planner()

    plan = planner.plan(
        "优化一下 snake game core",
        context={"workspace_name": "test_pro", "routing": {"scenario": "free_form", "strategy": "react_standard"}},
    )

    assert [step["id"] for step in plan] == [
        "inspect-workspace",
        "search-relevant-files",
        "apply-patch",
        "run-command",
        "summarize-findings",
    ]
