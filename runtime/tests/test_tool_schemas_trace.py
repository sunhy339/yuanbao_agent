from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from local_agent_runtime.context.builder import ContextBuilder
from local_agent_runtime.models import RuntimeEvent
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
    "ask_user_question",
    "enter_plan_mode",
    "exit_plan_mode",
    "agent",
    "run_command",
    "apply_patch",
    "git_status",
    "git_diff",
    "task",
    "write_file",
    "web_fetch",
    "code_search",
    "notebook",
    "browser",
    "computer_use",
    "memory.remember",
    "memory.recall",
    "scratchpad.write",
    "scratchpad.read",
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
        assert isinstance(schema["safety"], dict), f"{name}: safety should be dict, got {type(schema['safety'])}"
        assert "level" in schema["safety"]
        assert "category" in schema["safety"]
        assert "notes" in schema["safety"]
        assert "requires_approval" in schema["safety"]
        assert isinstance(schema["safety"]["notes"], list)
        assert schema["safety"]["level"] in {"safe", "medium", "dangerous"}

        # Metadata validation
        assert "metadata" in schema, f"{name}: missing metadata field"
        assert isinstance(schema["metadata"], dict)
        assert "cost_per_use" in schema["metadata"]
        assert "estimated_duration_ms" in schema["metadata"]
        assert schema["metadata"]["cost_per_use"] >= 1
        assert schema["metadata"]["estimated_duration_ms"] >= 0

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
                "cancellation",
                "childToolAllowlist",
                "child_tool_allowlist",
            }
            assert task_properties["timeoutMs"]["type"] == "integer"
            assert task_properties["retry"]["type"] == "object"
            assert "budget" not in task_properties
            assert task_properties["cancellation"]["type"] == "object"
            assert set(task_properties["childToolAllowlist"]["items"]["enum"]) >= {
                "run_command",
                "apply_patch",
                "write_file",
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
        elif name in {"web_fetch", "browser"}:
            assert "url" in input_schema["required"]
        elif name in {
            "ask_user_question",
            "enter_plan_mode",
            "exit_plan_mode",
            "agent",
            "computer_use",
            "memory.remember",
            "memory.recall",
            "scratchpad.write",
            "scratchpad.read",
        }:
            pass  # session/task-scoped tools do not require workspaceRoot
        else:
            assert "workspaceRoot" in input_schema["required"]
        assert json.loads(json.dumps(input_schema)) == input_schema

        if name in {"run_command", "apply_patch"}:
            assert schema["safety"]["level"] == "dangerous"
            assert schema["safety"]["requires_approval"] is True
            safety_text = " ".join(schema["safety"]["notes"]).lower()
            assert "approval" in safety_text
            assert "destructive" in safety_text or "modify" in safety_text

        if name in {"write_file"}:
            assert schema["safety"]["level"] == "dangerous"
            assert schema["safety"]["requires_approval"] is True
            safety_text = " ".join(schema["safety"]["notes"]).lower()
            assert "overwrite" in safety_text or "create" in safety_text

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


def test_builtin_tool_schemas_do_not_encode_fixed_probe_workflow() -> None:
    serialized = json.dumps(BUILTIN_TOOL_SCHEMAS, ensure_ascii=False).lower()

    fixed_workflow_markers = [
        "use this first",
        "use after list_dir",
        "quick top-level inventory",
        "agent loops",
        "before making changes when",
        "task needs exploration before execution",
        "prefer read-only commands first",
        "use before editing",
        "list_cells first",
    ]

    for marker in fixed_workflow_markers:
        assert marker not in serialized


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


def test_trace_append_list_orders_by_sequence_for_live_replay_parity(tmp_path: Path) -> None:
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
            visibility="trace",
        )

        response = store.list_trace_events({"taskId": task["id"]})

        assert [event["id"] for event in response["traceEvents"]] == [third["id"], first["id"], second["id"]]
        assert response["traceEvents"][2]["sessionId"] == session["id"]
        assert response["traceEvents"][2]["type"] == "tool.started"
        assert response["traceEvents"][2]["relatedId"] == "call_1"
        assert response["traceEvents"][2]["visibility"] == "trace"
        assert response["traceEvents"][2]["payload"] == {"toolName": "list_dir"}
    finally:
        store.close()


def test_store_appends_trace_for_approval_patch_and_command_lifecycle(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    try:
        workspace = store.upsert_workspace(str(tmp_path))
        session = store.create_session(workspace_id=workspace["id"], title="Lifecycle trace")
        task = store.create_task(session_id=session["id"], task_type="chat", goal="trace lifecycle", plan=[])

        approval = store.create_approval(
            task["id"],
            "apply_patch",
            {
                "summary": "Update README.md",
                "filesChanged": 1,
                "changedPaths": ["README.md"],
                "diffText": "diff --git a/README.md b/README.md\n",
            },
        )
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
            tool_metadata={
                "toolUseId": "call_command",
                "toolName": "run_command",
                "target": "python --version",
                "inputSummary": "python --version",
                "toolGroupId": "tgrp_1",
                "toolIndex": 0,
                "toolTotal": 1,
                "toolCategory": "verification",
            },
        )
        assert command["toolUseId"] == "call_command"
        assert command["toolName"] == "run_command"
        assert command["target"] == "python --version"
        assert command["inputSummary"] == "python --version"
        assert command["toolGroupId"] == "tgrp_1"
        assert command["toolIndex"] == 0
        assert command["toolTotal"] == 1
        assert command["toolCategory"] == "verification"
        assert command["shell"] == "powershell"
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
        assert events[0]["payload"]["taskId"] == task["id"]
        assert events[0]["payload"]["filesChanged"] == 1
        assert events[0]["payload"]["changedPaths"] == ["README.md"]
        assert events[0]["payload"]["diffText"].startswith("diff --git a/README.md")
        assert events[0]["payload"]["preview"][0] == {"label": "摘要", "value": "Update README.md"}
        assert events[1]["payload"]["taskId"] == task["id"]
        assert events[1]["payload"]["request"]["summary"] == "Update README.md"
        assert events[1]["payload"]["filesChanged"] == 1
        assert events[1]["payload"]["changedPaths"] == ["README.md"]
        assert events[1]["payload"]["diffText"].startswith("diff --git a/README.md")
        assert events[1]["payload"]["preview"][0] == {"label": "摘要", "value": "Update README.md"}
        assert events[1]["payload"]["decision"] == "approved"
        assert events[1]["payload"]["decidedAt"] is not None
        assert events[2]["payload"]["filesChanged"] == 1
        assert events[2]["payload"]["changedPaths"] == ["README.md"]
        assert events[2]["payload"]["diffText"].startswith("diff --git a/README.md")
        assert "workspaceId" not in events[2]["payload"]
        assert "workspaceId" not in events[3]["payload"]
        assert events[4]["payload"]["toolUseId"] == "call_command"
        assert events[4]["payload"]["target"] == "python --version"
        assert events[4]["payload"]["inputSummary"] == "python --version"
        assert events[4]["payload"]["toolGroupId"] == "tgrp_1"
        assert events[4]["payload"]["toolIndex"] == 0
        assert events[4]["payload"]["toolTotal"] == 1
        assert events[4]["payload"]["toolCategory"] == "verification"
        assert events[-1]["payload"]["toolUseId"] == "call_command"
        assert events[-1]["payload"]["target"] == "python --version"
        assert events[-1]["payload"]["exitCode"] == 0

        listed_command = store.list_command_logs({"taskId": task["id"]})["commandLogs"][0]
        fetched_command = store.get_command_log({"commandId": command["id"]})["commandLog"]
        for command_log in (listed_command, fetched_command):
            assert command_log["toolUseId"] == "call_command"
            assert command_log["toolName"] == "run_command"
            assert command_log["target"] == "python --version"
            assert command_log["inputSummary"] == "python --version"
            assert command_log["toolGroupId"] == "tgrp_1"
            assert command_log["toolIndex"] == 0
            assert command_log["toolTotal"] == 1
            assert command_log["toolCategory"] == "verification"
            assert command_log["shell"] == "powershell"
    finally:
        store.close()


def test_store_adds_structured_preview_for_non_file_approvals(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    try:
        workspace = store.upsert_workspace(str(tmp_path))
        session = store.create_session(workspace_id=workspace["id"], title="Approval preview")
        task = store.create_task(session_id=session["id"], task_type="chat", goal="preview approval", plan=[])

        approval = store.create_approval(
            task["id"],
            "run_command",
            {
                "command": "npm run typecheck",
                "cwd": "app",
                "shell": "powershell",
                "policyReason": "Command needs approval outside allowlist.",
            },
        )

        event = store.list_trace_events({"taskId": task["id"]})["traceEvents"][0]

        assert event["relatedId"] == approval["id"]
        assert event["payload"]["preview"] == [
            {"label": "命令", "value": "npm run typecheck"},
            {"label": "目录", "value": "app"},
            {"label": "Shell", "value": "powershell"},
            {"label": "原因", "value": "Command needs approval outside allowlist."},
        ]

        store.resolve_approval(approval["id"], "approved")
        resolved_event = store.list_trace_events({"taskId": task["id"]})["traceEvents"][1]
        assert resolved_event["payload"]["taskId"] == task["id"]
        assert resolved_event["payload"]["request"]["command"] == "npm run typecheck"
        assert resolved_event["payload"]["preview"] == event["payload"]["preview"]
        assert resolved_event["payload"]["decision"] == "approved"
        assert resolved_event["payload"]["decidedBy"] == "user"
        assert resolved_event["payload"]["decidedAt"] is not None
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


def test_trace_replay_uses_sequence_order_when_timestamps_arrive_out_of_order(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    try:
        workspace = store.upsert_workspace(str(tmp_path))
        session = store.create_session(workspace_id=workspace["id"], title="Trace ordering")
        task = store.create_task(session_id=session["id"], task_type="chat", goal="trace ordering", plan=[])

        first = store.append_trace_event(
            task_id=task["id"],
            session_id=session["id"],
            event_type="content_start",
            source="assistant",
            payload={"blockType": "tool_use", "toolName": "search_files"},
            created_at=200,
        )
        second = store.append_trace_event(
            task_id=task["id"],
            session_id=session["id"],
            event_type="tool_use_complete",
            source="tool",
            payload={"toolName": "search_files", "toolUseId": "call_1"},
            created_at=100,
        )

        replayed = store.list_trace_events({"taskId": task["id"]})["traceEvents"]
        recovered = store.events_after(session["id"], 0)["events"]

        assert [event["id"] for event in replayed] == [first["id"], second["id"]]
        assert [event["id"] for event in recovered] == [first["id"], second["id"]]
    finally:
        store.close()


def test_trace_replay_keeps_chat_compat_message_delta_flat_frames_for_adapter_history(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    try:
        workspace = store.upsert_workspace(str(tmp_path))
        session = store.create_session(workspace_id=workspace["id"], title="Trace replay")
        task = store.create_task(session_id=session["id"], task_type="chat", goal="trace replay", plan=[])

        store.append_trace_event(
            task_id=task["id"],
            event_type="message.delta",
            source="message",
            payload={"messageId": "msg_1", "delta": "hello", "_chatCompat": True},
            session_id=session["id"],
        )
        store.append_trace_event(
            task_id=task["id"],
            event_type="message.completed",
            source="message",
            payload={"messageId": "msg_1", "content": "hello", "_chatCompat": True},
            session_id=session["id"],
        )
        store.append_trace_event(
            task_id=task["id"],
            event_type="content_delta",
            source="assistant",
            payload={"text": "direct flat text"},
            session_id=session["id"],
        )

        events = store.list_trace_events({"taskId": task["id"]})["traceEvents"]

        assert events[0]["yuanbao"] == {"type": "content_delta", "text": "hello"}
        assert "hahaCc" not in events[0]
        assert events[1]["yuanbao"] == {
            "type": "message_complete",
            "usage": {"input_tokens": 0, "output_tokens": 0},
        }
        assert "hahaCc" not in events[1]
        assert events[2]["yuanbao"] == {"type": "content_delta", "text": "direct flat text"}
        assert "hahaCc" not in events[2]
    finally:
        store.close()


def test_trace_replay_respects_suppress_chat_replay_bridge(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    try:
        workspace = store.upsert_workspace(str(tmp_path))
        session = store.create_session(workspace_id=workspace["id"], title="Trace replay")
        task = store.create_task(session_id=session["id"], task_type="chat", goal="trace replay", plan=[])

        store.append_trace_event(
            task_id=task["id"],
            event_type="thinking",
            source="assistant",
            payload={
                "text": "internal only",
                "_bridge": {"suppressChatReplay": True},
            },
            session_id=session["id"],
        )

        event = store.list_trace_events({"taskId": task["id"]})["traceEvents"][0]

        assert "hahaCc" not in event
        assert "yuanbao" not in event
    finally:
        store.close()


def test_runtime_trace_mirror_converts_realtime_flat_suppression_to_chat_replay_suppression(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    try:
        workspace = store.upsert_workspace(str(tmp_path))
        session = store.create_session(workspace_id=workspace["id"], title="Trace replay")
        task = store.create_task(session_id=session["id"], task_type="chat", goal="trace replay", plan=[])

        store.append_runtime_event(
            RuntimeEvent(
                event_id="evt_internal_progress",
                session_id=session["id"],
                task_id=task["id"],
                type="assistant_progress",
                ts=1,
                payload={
                    "text": "internal phase",
                    "_bridge": {"suppressRealtimeFlat": True},
                },
                visibility="chat",
            )
        )

        event = store.list_trace_events({"taskId": task["id"]})["traceEvents"][0]

        assert event["payload"]["_bridge"]["suppressRealtimeFlat"] is True
        assert event["payload"]["_bridge"]["suppressChatReplay"] is True
        assert "hahaCc" not in event
        assert "yuanbao" not in event
    finally:
        store.close()


def test_completion_review_internal_bridge_trace_events_do_not_emit_flat_history(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    try:
        workspace = store.upsert_workspace(str(tmp_path))
        session = store.create_session(workspace_id=workspace["id"], title="Completion review")
        task = store.create_task(session_id=session["id"], task_type="edit", goal="change code", plan=[])
        bridge = {
            "internal": True,
            "kind": "completion_review",
            "suppressRealtimeFlat": True,
            "suppressChatReplay": True,
        }

        store.append_trace_event(
            task_id=task["id"],
            event_type="approval.requested",
            source="approval",
            payload={
                "approvalId": "appr_review",
                "taskId": task["id"],
                "kind": "completion_review",
                "internal": True,
                "_bridge": bridge,
                "request": {"summary": "internal gate"},
            },
            session_id=session["id"],
            visibility="trace",
        )
        store.append_trace_event(
            task_id=task["id"],
            event_type="task.waiting_approval",
            source="task",
            payload={
                "status": "waiting_approval",
                "internalGate": "completion_review",
                "_bridge": bridge,
            },
            session_id=session["id"],
            visibility="trace",
        )

        events = store.list_trace_events({"taskId": task["id"]})["traceEvents"]

        assert [event["type"] for event in events] == ["approval.requested", "task.waiting_approval"]
        assert all(event["visibility"] == "trace" for event in events)
        assert all("hahaCc" not in event for event in events)
        assert all("yuanbao" not in event for event in events)
    finally:
        store.close()


def test_approval_trace_uses_public_request_payload(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    try:
        workspace = store.upsert_workspace(str(tmp_path))
        session = store.create_session(workspace_id=workspace["id"], title="Approval public payload")
        task = store.create_task(session_id=session["id"], task_type="edit", goal="approval safety", plan=[])

        patch_approval = store.create_approval(
            task["id"],
            "apply_patch",
            {
                "changedPaths": ["README.md"],
                "diffText": "diff --git a/README.md b/README.md\n+" + ("x" * 1400),
                "files": [{"path": "README.md", "content": "secret body" * 100}],
                "filesChanged": 1,
                "workspaceRoot": str(tmp_path),
            },
        )
        store.resolve_approval(patch_approval["id"], "approved")
        plan_approval = store.create_approval(
            task["id"],
            "plan",
            {
                "goal": "split work",
                "plan": {
                    "summary": "Plan summary",
                    "steps": ["Inspect", "Patch"],
                    "raw": {"providerOnly": True},
                },
                "steps": ["Inspect", "Patch"],
                "subtasks": [{"id": "sub-0", "title": "Inspect"}],
                "previewSections": [{"kind": "items", "title": "Subtasks", "items": [{"id": "sub-0", "title": "Inspect"}]}],
            },
        )

        events = store.list_trace_events({"taskId": task["id"]})["traceEvents"]
        patch_requested = next(
            event for event in events
            if event["type"] == "approval.requested" and event["relatedId"] == patch_approval["id"]
        )
        patch_resolved = next(
            event for event in events
            if event["type"] == "approval.resolved" and event["relatedId"] == patch_approval["id"]
        )
        plan_requested = next(
            event for event in events
            if event["type"] == "approval.requested" and event["relatedId"] == plan_approval["id"]
        )

        for event in (patch_requested, patch_resolved):
            request = event["payload"]["request"]
            request_json = json.dumps(request, ensure_ascii=False)
            assert "workspaceRoot" not in request
            assert "secret body" not in request_json
            assert isinstance(event["payload"]["diffText"], str)
            assert event["payload"]["diffText"].startswith("diff --git a/README.md")
            assert request["diffText"]["omitted"] is True
            assert request["files"] == [{"path": "README.md"}]

        plan_request_json = json.dumps(plan_requested["payload"]["request"], ensure_ascii=False)
        assert '"raw"' not in plan_request_json
        assert plan_requested["payload"]["request"]["plan"]["steps"] == ["Inspect", "Patch"]
        assert plan_requested["payload"]["request"]["previewSections"][0]["items"][0]["title"] == "Inspect"
    finally:
        store.close()
