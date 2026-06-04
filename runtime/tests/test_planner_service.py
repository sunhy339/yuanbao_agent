from __future__ import annotations

from local_agent_runtime.planner.service import Planner


def test_planner_skips_root_scaffold_for_plain_code_change_goal() -> None:
    planner = Planner()

    plan = planner.plan(
        "optimize snake game core",
        context={"workspace_name": "test_pro", "routing": {"scenario": "code_edit", "strategy": "react_standard"}},
    )

    assert plan == []


def test_planner_skips_root_plan_for_explicit_command_request() -> None:
    planner = Planner()

    plan = planner.plan("run command: npm test", context={"workspace_name": "app"})

    assert plan == []


def test_planner_skips_root_plan_for_explicit_patch_request() -> None:
    planner = Planner()

    plan = planner.plan("apply patch: *** Begin Patch", context={"workspace_name": "app"})

    assert plan == []


def test_planner_uses_lifecycle_plan_for_explicit_swarm_route() -> None:
    planner = Planner()

    plan = planner.plan(
        "use multiple agents to optimize this project",
        context={"workspace_name": "test_pro", "routing": {"scenario": "swarm_task", "strategy": "plan_swarm"}},
    )

    assert [step["id"] for step in plan] == ["decompose-work", "dispatch-work", "synthesize-results"]
    assert plan[0]["status"] == "active"
    assert "tools" not in plan[0]["detail"].lower()


def test_planner_uses_lifecycle_plan_for_explicit_plan_request() -> None:
    planner = Planner()

    plan = planner.plan(
        "create a roadmap for the snake game project",
        context={"workspace_name": "test_pro", "routing": {"scenario": "free_form", "strategy": "react_standard"}},
    )

    assert [step["id"] for step in plan] == ["clarify-goal", "draft-plan", "present-plan"]
