from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from local_agent_runtime.context.builder import ContextBuilder
from local_agent_runtime.store.sqlite_store import SQLiteStore
from local_agent_runtime.tools.registry import (
    BUILTIN_TOOL_SCHEMAS,
    ToolRegistry,
    to_openai_function_tools,
)


EXPECTED_TOOL_NAMES = {
    "list_dir",
    "search_files",
    "read_file",
    "run_command",
    "apply_patch",
    "git_status",
    "git_diff",
    "task",
}


def _make_store(tmp_path: Path) -> SQLiteStore:
    return SQLiteStore(str(tmp_path / "runtime.sqlite3"))


def test_builtin_tool_schemas_are_complete_and_openai_convertible() -> None:
    tool_names = [schema["name"] for schema in BUILTIN_TOOL_SCHEMAS]

    assert len(tool_names) == len(set(tool_names))

    schemas_by_name = {schema["name"]: schema for schema in BUILTIN_TOOL_SCHEMAS}

    assert set(schemas_by_name) == EXPECTED_TOOL_NAMES
    for name, schema in schemas_by_name.items():
        assert isinstance(schema["description"], str)
        assert len(schema["description"]) > 40
        assert schema["hints"]
        assert schema["safety"]

        input_schema = schema["input_schema"]
        assert input_schema["type"] == "object"
        assert input_schema["additionalProperties"] is False
        assert isinstance(input_schema["properties"], dict)
        assert input_schema["properties"]
        if name == "task":
            assert input_schema["required"] == ["prompt"]
            task_properties = input_schema["properties"]
            assert set(task_properties) >= {
                "prompt",
                "title",
                "agentType",
                "priority",
                "sessionId",
                "taskId",
                "timeoutMs",
                "retry",
                "budget",
                "cancellation",
                "childToolAllowlist",
                "child_tool_allowlist",
            }
            assert task_properties["timeoutMs"]["type"] == "integer"
            assert task_properties["retry"]["type"] == "object"
            assert task_properties["budget"]["type"] == "object"
            assert task_properties["cancellation"]["type"] == "object"
            assert set(task_properties["childToolAllowlist"]["items"]["enum"]) >= {
                "run_command",
                "apply_patch",
            }
            assert task_properties["child_tool_allowlist"] == task_properties["childToolAllowlist"]
        elif name == "run_command":
            run_command_properties = input_schema["properties"]
            assert set(run_command_properties) >= {
                "background",
                "backgroundJob",
                "runInBackground",
            }
            assert run_command_properties["background"]["type"] == "boolean"
            assert run_command_properties["runInBackground"]["type"] == "boolean"
            assert run_command_properties["backgroundJob"]["oneOf"][0]["type"] == "boolean"
        else:
            assert "workspaceRoot" in input_schema["required"]
        assert json.loads(json.dumps(input_schema)) == input_schema

        if name in {"run_command", "apply_patch"}:
            safety_text = " ".join(schema["safety"]).lower()
            assert "approval" in safety_text
            assert "destructive" in safety_text or "modify" in safety_text

    registry = ToolRegistry({name: lambda _params: {} for name in EXPECTED_TOOL_NAMES})
    assert {schema["name"] for schema in registry.schemas} == EXPECTED_TOOL_NAMES
    assert registry.schemas == [schemas_by_name[schema["name"]] for schema in registry.schemas]

    openai_tools = to_openai_function_tools(registry.schemas)
    assert len(openai_tools) == len(EXPECTED_TOOL_NAMES)
    for tool in openai_tools:
        function = tool["function"]
        source_schema = schemas_by_name[function["name"]]
        assert tool["type"] == "function"
        assert function["description"] == source_schema["description"]
        assert function["parameters"] == source_schema["input_schema"]


def test_context_builder_outputs_openai_function_tools(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    try:
        workspace_root = tmp_path / "workspace"
        workspace_root.mkdir()
        workspace = store.upsert_workspace(str(workspace_root))
        session = store.create_session(workspace_id=workspace["id"], title="Schema context")

        context = ContextBuilder(store).build(session_id=session["id"], goal="inspect")

        assert {tool["name"] for tool in context["tools"]} == EXPECTED_TOOL_NAMES
        assert len(context["openai_tools"]) == len(context["tools"])
        first = context["openai_tools"][0]
        assert first["type"] == "function"
        assert first["function"]["parameters"]["type"] == "object"
    finally:
        store.close()


def test_task_schema_is_exposed_through_registry_and_context(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    try:
        workspace_root = tmp_path / "workspace"
        workspace_root.mkdir()
        workspace = store.upsert_workspace(str(workspace_root))
        session = store.create_session(workspace_id=workspace["id"], title="Task schema")

        context = ContextBuilder(store).build(session_id=session["id"], goal="delegate work")
        registry = ToolRegistry({schema["name"]: lambda _params: {} for schema in context["tools"]})

        assert "task" in {schema["name"] for schema in context["tools"]}
        assert "task" in {tool["function"]["name"] for tool in context["openai_tools"]}
        assert "task" in {schema["name"] for schema in registry.schemas}
    finally:
        store.close()


def test_trace_append_list_orders_by_time_and_sequence(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    try:
        workspace = store.upsert_workspace(str(tmp_path))
        session = store.create_session(workspace_id=workspace["id"], title="Trace")
        task = store.create_task(session_id=session["id"], task_type="chat", goal="trace", plan=[])

        third = store.append_trace_event(
            task_id=task["id"],
            event_type="provider.response",
            source="provider",
            payload={"status": 200},
            created_at=200,
        )
        first = store.append_trace_event(
            task_id=task["id"],
            event_type="provider.request",
            source="provider",
            payload={"model": "test-model"},
            created_at=100,
        )
        second = store.append_trace_event(
            task_id=task["id"],
            event_type="tool.started",
            source="tool",
            payload={"toolName": "list_dir"},
            related_id="call_1",
            created_at=100,
        )

        response = store.list_trace_events({"taskId": task["id"]})

        assert [event["id"] for event in response["traceEvents"]] == [first["id"], second["id"], third["id"]]
        assert response["traceEvents"][1]["sessionId"] == session["id"]
        assert response["traceEvents"][1]["type"] == "tool.started"
        assert response["traceEvents"][1]["relatedId"] == "call_1"
        assert response["traceEvents"][1]["payload"] == {"toolName": "list_dir"}
    finally:
        store.close()


def test_store_appends_trace_for_approval_patch_and_command_lifecycle(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    try:
        workspace = store.upsert_workspace(str(tmp_path))
        session = store.create_session(workspace_id=workspace["id"], title="Lifecycle trace")
        task = store.create_task(session_id=session["id"], task_type="chat", goal="trace lifecycle", plan=[])

        approval = store.create_approval(task["id"], "run_command", {"command": "python --version"})
        store.resolve_approval(approval["id"], "approved")
        patch = store.create_patch(
            task_id=task["id"],
            workspace_id=workspace["id"],
            summary="Update README.md",
            diff_text="diff --git a/README.md b/README.md\n",
            files_changed=1,
        )
        store.update_patch(patch["id"], status="applied")
        command = store.create_command_log(
            task_id=task["id"],
            command="python --version",
            cwd=".",
            shell="powershell",
        )
        store.update_command_log(command["id"], status="completed", exit_code=0)

        events = store.list_trace_events({"taskId": task["id"]})["traceEvents"]

        assert [event["type"] for event in events] == [
            "approval.requested",
            "approval.resolved",
            "patch.proposed",
            "patch.applied",
            "command.started",
            "command.completed",
        ]
        assert events[0]["relatedId"] == approval["id"]
        assert events[2]["payload"]["filesChanged"] == 1
        assert events[-1]["payload"]["exitCode"] == 0
    finally:
        store.close()


def test_trace_list_rpc(runtime_harness: Any) -> None:
    workspace = runtime_harness.store.upsert_workspace(str(Path.cwd()))
    session = runtime_harness.store.create_session(workspace_id=workspace["id"], title="Trace RPC")
    task = runtime_harness.store.create_task(session_id=session["id"], task_type="chat", goal="trace rpc", plan=[])
    event = runtime_harness.store.append_trace_event(
        task_id=task["id"],
        event_type="approval.requested",
        source="approval",
        payload={"approvalId": "appr_test", "kind": "run_command"},
        related_id="appr_test",
        created_at=300,
    )

    response = runtime_harness.call("trace.list", {"taskId": task["id"]})

    assert "result" in response, response
    assert response["result"]["traceEvents"][0]["id"] == event["id"]
