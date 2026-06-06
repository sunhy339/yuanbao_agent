from __future__ import annotations

from local_agent_runtime.orchestrator.react_runner import ReactRunnerMixin
from local_agent_runtime.orchestrator.react_tool_helpers import ReactToolHelpersMixin


class _IdStore:
    def __init__(self) -> None:
        self._index = 0

    def new_id(self, prefix: str) -> str:
        self._index += 1
        return f"{prefix}_{self._index}"


class _ToolHelperHarness(ReactToolHelpersMixin):
    def __init__(self) -> None:
        self._store = _IdStore()

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


def test_provider_tool_call_to_spec_preserves_parent_tool_use_id() -> None:
    spec = _ToolHelperHarness()._provider_tool_call_to_spec(
        {
            "id": "call_child",
            "name": "read_file",
            "arguments": {"path": "README.md"},
            "parentToolUseId": "call_parent",
        },
        {"workspace_root": r"D:\real\workspace"},
    )

    assert spec["id"] == "call_child"
    assert spec["parentToolUseId"] == "call_parent"


def test_annotate_tool_call_batch_assigns_ids_without_parenting_context_read_to_search() -> None:
    helper = _ToolHelperHarness()

    annotated = helper._annotate_tool_call_batch(
        [
            {"name": "search_files", "arguments": {"query": "needle"}},
            {"name": "read_file", "arguments": {"path": "alpha.txt"}},
        ]
    )

    assert annotated[0]["id"].startswith("tc_")
    assert annotated[1]["id"].startswith("tc_")
    assert "parentToolUseId" not in annotated[1]
    assert annotated[0]["toolGroupId"] == annotated[1]["toolGroupId"]
    assert [call["toolIndex"] for call in annotated] == [0, 1]
    assert annotated[0]["toolOperationId"] == "context:search:needle"
    assert annotated[1]["toolOperationId"] == "context:path:alpha.txt"


def test_annotate_tool_call_batch_infers_file_change_follow_up_parents_without_ids() -> None:
    helper = _ToolHelperHarness()

    annotated = helper._annotate_tool_call_batch(
        [
            {"name": "write_file", "arguments": {"path": "alpha.txt", "content": "needle"}},
            {"name": "run_command", "arguments": {"command": "npm run typecheck"}},
            {"name": "git_status", "arguments": {}},
        ]
    )

    assert annotated[1]["parentToolUseId"] == annotated[0]["id"]
    assert annotated[2]["parentToolUseId"] == annotated[0]["id"]
    assert annotated[1]["toolOperationId"] == annotated[0]["toolOperationId"]
    assert annotated[2]["toolOperationId"] == annotated[0]["toolOperationId"]
    assert annotated[0]["toolOperationId"] == "file_change:alpha.txt"


def test_annotate_tool_call_batch_parents_read_of_changed_file() -> None:
    helper = _ToolHelperHarness()

    annotated = helper._annotate_tool_call_batch(
        [
            {"name": "write_file", "arguments": {"path": "alpha.txt", "content": "needle"}},
            {"name": "read_file", "arguments": {"path": "alpha.txt"}},
        ]
    )

    assert annotated[1]["parentToolUseId"] == annotated[0]["id"]


def test_annotate_tool_call_batch_does_not_parent_unrelated_read_to_file_change() -> None:
    helper = _ToolHelperHarness()

    annotated = helper._annotate_tool_call_batch(
        [
            {"name": "write_file", "arguments": {"path": "alpha.txt", "content": "needle"}},
            {"name": "read_file", "arguments": {"path": "beta.txt"}},
        ]
    )

    assert annotated[1].get("parentToolUseId") is None


def test_annotate_tool_spec_batch_assigns_ids_before_inferring_parents() -> None:
    helper = _ToolHelperHarness()

    annotated = helper._annotate_tool_spec_batch(
        [
            {"name": "write_file", "arguments": {"path": "alpha.txt", "content": "needle"}},
            {"name": "run_command", "arguments": {"command": "pytest -q"}},
            {"name": "git_diff", "arguments": {}},
        ]
    )

    assert annotated[0]["id"].startswith("tc_")
    assert annotated[1]["parentToolUseId"] == annotated[0]["id"]
    assert annotated[2]["parentToolUseId"] == annotated[0]["id"]


def test_annotate_tool_spec_batch_keeps_context_read_independent_from_search() -> None:
    helper = _ToolHelperHarness()

    annotated = helper._annotate_tool_spec_batch(
        [
            {"name": "search_files", "arguments": {"query": "needle"}},
            {"name": "read_file", "arguments": {"path": "alpha.txt"}},
        ]
    )

    assert annotated[0]["id"].startswith("tc_")
    assert annotated[1]["id"].startswith("tc_")
    assert "parentToolUseId" not in annotated[1]
    assert annotated[0]["toolGroupId"] == annotated[1]["toolGroupId"]
    assert [spec["toolIndex"] for spec in annotated] == [0, 1]
    assert annotated[0]["toolOperationId"] == "context:search:needle"
    assert annotated[1]["toolOperationId"] == "context:path:alpha.txt"


def test_annotate_follow_up_tool_spec_keeps_read_independent_from_prior_search_result() -> None:
    helper = _ToolHelperHarness()

    follow_up = helper._annotate_follow_up_tool_spec(
        {"name": "read_file", "arguments": {"path": "alpha.txt"}},
        [
            {
                "id": "tc_search",
                "name": "search_files",
                "toolOperationId": "context:search:needle",
                "toolOperationLabel": "读取上下文",
                "result": {"matches": []},
            },
        ],
    )

    assert follow_up is not None
    assert "parentToolUseId" not in follow_up
    assert follow_up["id"].startswith("tc_")
    assert follow_up["toolOperationId"] == "context:path:alpha.txt"
    assert follow_up["toolOperationLabel"] == "读取上下文"


def test_annotate_tool_call_batch_with_history_keeps_cross_turn_read_independent_from_search() -> None:
    helper = _ToolHelperHarness()

    annotated = helper._annotate_tool_call_batch_with_history(
        [{"name": "read_file", "arguments": {"path": "alpha.txt"}}],
        [
            {
                "id": "tc_search",
                "name": "search_files",
                "toolOperationId": "context:search:needle",
                "result": {"matches": []},
            },
        ],
    )

    assert "parentToolUseId" not in annotated[0]
    assert annotated[0]["id"].startswith("tc_")
    assert annotated[0]["toolOperationId"] == "context:path:alpha.txt"


def test_annotate_tool_call_batch_with_history_parents_cross_turn_verification() -> None:
    helper = _ToolHelperHarness()

    annotated = helper._annotate_tool_call_batch_with_history(
        [{"name": "run_command", "arguments": {"command": "pytest -q"}}],
        [
            {
                "id": "tc_write",
                "name": "write_file",
                "toolOperationId": "file_change:alpha.txt",
                "result": {"status": "written", "path": "alpha.txt"},
            },
        ],
    )

    assert annotated[0]["parentToolUseId"] == "tc_write"
    assert annotated[0]["toolOperationId"] == "file_change:alpha.txt"


def test_annotate_tool_call_batch_with_history_parents_cross_turn_git_review() -> None:
    helper = _ToolHelperHarness()

    annotated = helper._annotate_tool_call_batch_with_history(
        [{"name": "git_diff", "arguments": {}}],
        [
            {"id": "tc_patch", "name": "apply_patch", "result": {"status": "applied"}},
        ],
    )

    assert annotated[0]["parentToolUseId"] == "tc_patch"


def test_annotate_tool_call_batch_with_history_inherits_explicit_parent_operation() -> None:
    helper = _ToolHelperHarness()

    annotated = helper._annotate_tool_call_batch_with_history(
        [{"name": "git_status", "parentToolUseId": "tc_verify", "arguments": {}}],
        [
            {
                "id": "tc_write",
                "name": "write_file",
                "toolOperationId": "file_change:alpha.txt",
                "toolOperationLabel": "文件改动",
                "result": {"status": "written", "path": "alpha.txt"},
            },
            {
                "id": "tc_verify",
                "name": "run_command",
                "parentToolUseId": "tc_write",
                "result": {"status": "completed"},
            },
        ],
    )

    assert annotated[0]["parentToolUseId"] == "tc_verify"
    assert annotated[0]["toolOperationId"] == "file_change:alpha.txt"
    assert annotated[0]["toolOperationLabel"] == "文件改动"


def test_annotate_tool_call_batch_inherits_explicit_same_batch_parent_operation() -> None:
    helper = _ToolHelperHarness()

    annotated = helper._annotate_tool_call_batch(
        [
            {"id": "call_write", "name": "write_file", "arguments": {"path": "alpha.txt", "content": "needle"}},
            {
                "id": "call_verify",
                "name": "run_command",
                "parentToolUseId": "call_write",
                "arguments": {"command": "npm run typecheck"},
            },
        ]
    )

    assert annotated[1]["parentToolUseId"] == "call_write"
    assert annotated[1]["toolOperationId"] == "file_change:alpha.txt"
    assert annotated[1]["toolOperationLabel"] == "文件改动"


def test_annotate_tool_call_batch_with_history_parents_read_of_changed_file() -> None:
    helper = _ToolHelperHarness()

    annotated = helper._annotate_tool_call_batch_with_history(
        [{"name": "read_file", "arguments": {"path": "alpha.txt"}}],
        [
            {
                "id": "tc_patch",
                "name": "apply_patch",
                "result": {"status": "applied", "changedPaths": ["alpha.txt"]},
            },
        ],
    )

    assert annotated[0]["parentToolUseId"] == "tc_patch"


def test_annotate_tool_call_batch_parents_computer_use_browser_action_to_observation() -> None:
    helper = _ToolHelperHarness()

    annotated = helper._annotate_tool_call_batch(
        [
            {
                "name": "computer_use",
                "arguments": {
                    "action": "inspect",
                    "target": "browser",
                    "permission": "Inspect page",
                    "url": "http://localhost:5173/",
                },
            },
            {
                "name": "computer_use",
                "arguments": {
                    "action": "click",
                    "target": "browser",
                    "permission": "Click Save",
                    "selector": "button.save",
                    "url": "http://localhost:5173/#main",
                },
            },
        ]
    )

    assert annotated[1]["parentToolUseId"] == annotated[0]["id"]
    assert annotated[1]["toolOperationId"] == annotated[0]["toolOperationId"]
    assert annotated[0]["toolOperationId"] == "computer_use:browser:url:http://localhost:5173"


def test_annotate_tool_call_batch_with_history_parents_computer_use_browser_action() -> None:
    helper = _ToolHelperHarness()

    annotated = helper._annotate_tool_call_batch_with_history(
        [
            {
                "name": "computer_use",
                "arguments": {
                    "action": "type",
                    "target": "browser",
                    "permission": "Fill search",
                    "selector": "input[name='q']",
                    "text": "yuanbao",
                    "browserContextId": "ctx_1",
                    "pageId": "page_1",
                },
            }
        ],
        [
            {
                "id": "tc_observe",
                "name": "computer_use",
                "toolOperationId": "computer_use:browser:page:ctx_1:page_1",
                "toolOperationLabel": "桌面操作",
                "result": {
                    "status": "completed",
                    "action": "inspect",
                    "url": "",
                    "browserContextId": "ctx_1",
                    "pageId": "page_1",
                },
            },
        ],
    )

    assert annotated[0]["parentToolUseId"] == "tc_observe"
    assert annotated[0]["toolOperationId"] == "computer_use:browser:page:ctx_1:page_1"
    assert annotated[0]["toolOperationLabel"] == "桌面操作"


def test_annotate_tool_call_batch_with_history_prefers_changed_file_parent_over_search() -> None:
    helper = _ToolHelperHarness()

    annotated = helper._annotate_tool_call_batch_with_history(
        [{"name": "read_file", "arguments": {"path": "alpha.txt"}}],
        [
            {"id": "tc_write", "name": "write_file", "result": {"status": "written", "path": "alpha.txt"}},
            {"id": "tc_search", "name": "search_files", "result": {"matches": [{"path": "alpha.txt"}]}},
        ],
    )

    assert annotated[0]["parentToolUseId"] == "tc_write"


def test_annotate_follow_up_tool_spec_preserves_explicit_parent() -> None:
    helper = _ToolHelperHarness()

    follow_up = helper._annotate_follow_up_tool_spec(
        {"name": "read_file", "arguments": {"path": "alpha.txt"}, "parentToolUseId": "tc_explicit"},
        [
            {"id": "tc_search", "name": "search_files", "result": {"matches": []}},
        ],
    )

    assert follow_up is not None
    assert follow_up["parentToolUseId"] == "tc_explicit"


def test_annotate_follow_up_tool_spec_parents_verification_to_prior_file_change() -> None:
    helper = _ToolHelperHarness()

    follow_up = helper._annotate_follow_up_tool_spec(
        {"name": "run_command", "arguments": {"command": "npm run typecheck"}},
        [
            {"id": "tc_write", "name": "write_file", "result": {"status": "written"}},
        ],
    )

    assert follow_up is not None
    assert follow_up["parentToolUseId"] == "tc_write"


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


def test_child_subtask_timeout_prefers_main_workflow_budget() -> None:
    context = {
        "routing": {
            "mainWorkflow": {
                "budget": {
                    "childTaskTimeoutMs": 45_000,
                }
            }
        },
        "config": {
            "autonomy": {
                "activeProfileId": "long-run",
                "profiles": [{"id": "long-run", "childTaskTimeoutMs": 700_000}],
            },
            "policy": {"childTaskTimeoutMs": 300_000, "commandTimeoutMs": 900_000},
        },
    }

    assert _ReactRunnerHarness()._child_subtask_timeout_ms(context) == 45_000


def test_child_subtask_timeout_prefers_child_specific_policy_over_command_timeout() -> None:
    context = {
        "config": {
            "autonomy": {
                "activeProfileId": "long-run",
                "profiles": [{"id": "long-run", "maxSteps": 40}],
            },
            "policy": {"childTaskTimeoutMs": 120_000, "commandTimeoutMs": 900_000},
        }
    }

    assert _ReactRunnerHarness()._child_subtask_timeout_ms(context) == 120_000


def test_child_subtask_timeout_prefers_autonomy_profile_timeout() -> None:
    context = {
        "config": {
            "autonomy": {
                "activeProfileId": "long-run",
                "profiles": [{"id": "long-run", "childTaskTimeoutMs": 700_000, "timeoutMs": 800_000}],
            },
            "policy": {"commandTimeoutMs": 900_000},
        }
    }

    assert _ReactRunnerHarness()._child_subtask_timeout_ms(context) == 700_000
