from __future__ import annotations

from typing import Any


def _result(response: dict[str, Any]) -> dict[str, Any]:
    assert "result" in response, response
    return response["result"]


def test_schedule_rpc_persists_tasks_toggles_runs_and_lists_logs(runtime_harness: Any) -> None:
    created = _result(
        runtime_harness.call(
            "schedule.create",
            {
                "name": "Morning check",
                "prompt": "Summarize changed files",
                "schedule": "every 30 minutes",
                "enabled": True,
            },
        )
    )["task"]

    assert created["id"].startswith("sched_")
    assert created["name"] == "Morning check"
    assert created["prompt"] == "Summarize changed files"
    assert created["schedule"] == "every 30 minutes"
    assert created["status"] == "active"
    assert created["enabled"] is True
    assert created["createdAt"] > 0
    assert created["updatedAt"] >= created["createdAt"]
    assert created["lastRunAt"] is None
    assert created["nextRunAt"] is not None

    listed = _result(runtime_harness.call("schedule.list", {}))["tasks"]
    assert [task["id"] for task in listed] == [created["id"]]

    disabled = _result(
        runtime_harness.call(
            "schedule.toggle",
            {"taskId": created["id"], "enabled": False},
        )
    )["task"]
    assert disabled["enabled"] is False
    assert disabled["status"] == "disabled"
    assert disabled["nextRunAt"] is None

    updated = _result(
        runtime_harness.call(
            "schedule.update",
            {
                "taskId": created["id"],
                "name": "Evening check",
                "schedule": "every 2 hours",
                "enabled": True,
            },
        )
    )["task"]
    assert updated["name"] == "Evening check"
    assert updated["schedule"] == "every 2 hours"
    assert updated["enabled"] is True
    assert updated["status"] == "active"
    assert updated["nextRunAt"] is not None

    run = _result(runtime_harness.call("schedule.run_now", {"taskId": created["id"]}))["run"]
    assert run["taskId"] == created["id"]
    assert run["status"] == "completed"
    assert run["startedAt"] > 0
    assert run["finishedAt"] >= run["startedAt"]
    assert run["durationMs"] >= 0
    assert "recorded" in run["summary"]

    logs = _result(runtime_harness.call("schedule.logs", {"taskId": created["id"]}))["logs"]
    assert [log["id"] for log in logs] == [run["id"]]

    after_run = _result(runtime_harness.call("schedule.list", {}))["tasks"][0]
    assert after_run["lastRunAt"] == run["startedAt"]
    assert after_run["nextRunAt"] is not None


def test_schedule_create_requires_name_and_prompt(runtime_harness: Any) -> None:
    response = runtime_harness.call(
        "schedule.create",
        {"name": " ", "prompt": "", "schedule": "every 30 minutes"},
    )

    assert response["error"]["code"] == "INTERNAL_ERROR"
    assert "name is required" in response["error"]["message"]
