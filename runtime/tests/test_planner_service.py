from __future__ import annotations

from local_agent_runtime.planner.service import Planner


def test_planner_skips_root_scaffold_for_plain_code_change_goal() -> None:
    planner = Planner()

    plan = planner.plan(
        "optimize snake game core",
        context={"workspace_name": "test_pro", "routing": {"mode": "model_first"}},
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


def test_planner_ignores_model_first_multi_agent_hint() -> None:
    planner = Planner()

    plan = planner.plan(
        "use multiple agents to optimize this project",
        context={
            "workspace_name": "test_pro",
            "routing": {"mode": "model_first"},
        },
    )

    assert plan == []


def test_planner_skips_lifecycle_plan_for_model_first_route() -> None:
    planner = Planner()

    plan = planner.plan(
        "use multiple agents to optimize this project",
        context={
            "workspace_name": "test_pro",
            "routing": {
                "mode": "model_first",
            },
        },
    )

    assert plan == []


def test_planner_accepts_explicit_plan_steps_only() -> None:
    planner = Planner()

    plan = planner.plan(
        "unused",
        context={
            "explicitPlan": {
                "steps": [
                    {"id": "inspect", "title": "Inspect files", "detail": "Read the relevant source files."},
                    {"title": "Patch code", "status": "pending"},
                ],
            },
        },
    )

    assert plan == [
        {
            "id": "inspect",
            "title": "Inspect files",
            "status": "active",
            "detail": "Read the relevant source files.",
        },
        {"id": "step-1", "title": "Patch code", "status": "pending"},
    ]


def test_planner_skips_root_plan_for_plain_text_roadmap_request() -> None:
    planner = Planner()

    plan = planner.plan(
        "create a roadmap for the snake game project",
        context={"workspace_name": "test_pro", "routing": {"mode": "model_first"}},
    )

    assert plan == []


def test_planner_does_not_treat_explaining_a_plan_as_plan_request() -> None:
    planner = Planner()

    plan = planner.plan(
        "explain a plan",
        context={"workspace_name": "test_pro", "routing": {"mode": "model_first"}},
    )

    assert plan == []
