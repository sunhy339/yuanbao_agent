"""Tests for P9.3 release checks: event compat, MCP migration, feature flags."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from local_agent_runtime.event_bus import EventBus
from local_agent_runtime.models import RuntimeEvent
from local_agent_runtime.orchestrator.service import Orchestrator
from local_agent_runtime.rpc.server import JsonRpcServer
from local_agent_runtime.store.sqlite_store import SQLiteStore
from local_agent_runtime.tools.registry import ToolRegistry


# ── helpers ────────────────────────────────────────────────────────────────


def _make_runtime(
    tmp_path: Any,
    *,
    tools: dict[str, Any] | None = None,
    subagent_service: Any | None = None,
) -> SimpleNamespace:
    event_bus = EventBus()
    store = SQLiteStore(str(tmp_path / "runtime.sqlite3"))
    tool_registry = ToolRegistry(tools or {})

    class DummyProvider:
        def generate(self, prompt: str, context: dict[str, Any]) -> dict[str, Any]:
            return {"text": "ok", "status": "done"}

    orchestrator = Orchestrator(
        store=store, event_bus=event_bus,
        tool_registry=tool_registry, provider=DummyProvider(),
    )
    server = JsonRpcServer(orchestrator=orchestrator, store=store, event_bus=event_bus)
    if subagent_service is not None:
        orchestrator._subagent_service = subagent_service  # noqa: SLF001
    return SimpleNamespace(server=server, store=store, orchestrator=orchestrator, event_bus=event_bus)


def _rpc(runtime: SimpleNamespace, method: str, params: dict[str, Any]) -> dict[str, Any]:
    request_id = f"req_{method}"
    envelope = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
    return runtime.server.handle_line(json.dumps(envelope, ensure_ascii=False))


# ── 1. Event compatibility: assistant.token also emits message.delta ────────


class TestEventCompatAssistantToken:
    """assistant.token events must also emit message.delta for backward compat."""

    def test_assistant_token_emits_message_delta(self, tmp_path: Any) -> None:
        """When assistant.token is published, a message.delta event is also emitted."""
        runtime = _make_runtime(tmp_path)
        collected: list[RuntimeEvent] = []
        runtime.event_bus.subscribe(collected.append)

        task = {"id": "t1", "role": "root", "activeAssistantMessageId": "msg_1"}
        runtime.orchestrator._publish(
            session_id="s1",
            task=task,
            event_type="assistant.token",
            payload={"text": "hello"},
        )

        types = [e.type for e in collected]
        assert "assistant.token" in types, f"assistant.token not found in {types}"
        assert "message.delta" in types, f"message.delta not found in {types}"

    def test_message_delta_contains_message_id(self, tmp_path: Any) -> None:
        """message.delta event must include messageId from active assistant message."""
        runtime = _make_runtime(tmp_path)
        collected: list[RuntimeEvent] = []
        runtime.event_bus.subscribe(collected.append)

        task = {"id": "t1", "role": "root", "activeAssistantMessageId": "msg_42"}
        runtime.orchestrator._publish(
            session_id="s1",
            task=task,
            event_type="assistant.token",
            payload={"text": "world"},
        )

        delta_events = [e for e in collected if e.type == "message.delta"]
        assert len(delta_events) == 1
        assert delta_events[0].payload["messageId"] == "msg_42"

    def test_assistant_token_emits_chat_content_delta(self, tmp_path: Any) -> None:
        """assistant.token also emits the haha-cc style content_delta event."""
        runtime = _make_runtime(tmp_path)
        collected: list[RuntimeEvent] = []
        runtime.event_bus.subscribe(collected.append)

        task = {"id": "t1", "role": "root", "activeAssistantMessageId": "msg_42"}
        runtime.orchestrator._publish(
            session_id="s1",
            task=task,
            event_type="assistant.token",
            payload={"delta": "hello"},
        )

        content_events = [e for e in collected if e.type == "content_delta"]
        assert len(content_events) == 1
        assert content_events[0].payload["text"] == "hello"
        assert content_events[0].payload["messageId"] == "msg_42"
        assert content_events[0].payload["_chatCompat"] is True

    def test_non_token_events_no_extra_delta(self, tmp_path: Any) -> None:
        """Non-assistant.token events should NOT emit an extra message.delta."""
        runtime = _make_runtime(tmp_path)
        collected: list[RuntimeEvent] = []
        runtime.event_bus.subscribe(collected.append)

        task = {"id": "t1", "role": "root", "activeAssistantMessageId": "msg_1"}
        runtime.orchestrator._publish(
            session_id="s1",
            task=task,
            event_type="task.completed",
            payload={"status": "completed"},
        )

        types = [e.type for e in collected]
        assert "message.delta" not in types

    def test_tool_lifecycle_emits_chat_tool_blocks(self, tmp_path: Any) -> None:
        """Tool lifecycle events emit tool_use_complete and tool_result for chat rendering."""
        runtime = _make_runtime(tmp_path)
        collected: list[RuntimeEvent] = []
        runtime.event_bus.subscribe(collected.append)

        task = {"id": "t1", "role": "root", "activeAssistantMessageId": "msg_1"}
        runtime.orchestrator._publish(
            session_id="s1",
            task=task,
            event_type="tool.started",
            payload={
                "toolCallId": "tc_1",
                "parentToolUseId": "tc_parent",
                "toolGroupId": "tgrp_1",
                "toolIndex": 1,
                "toolTotal": 3,
                "toolCategory": "verification",
                "toolPhaseId": "verification",
                "toolPhaseLabel": "验证",
                "toolSemanticParentId": "phase:verification",
                "toolSemanticParentLabel": "验证",
                "toolName": "run_command",
                "arguments": {"command": "npm test"},
            },
        )
        runtime.orchestrator._publish(
            session_id="s1",
            task=task,
            event_type="tool.completed",
            payload={
                "toolCallId": "tc_1",
                "parentToolUseId": "tc_parent",
                "toolGroupId": "tgrp_1",
                "toolIndex": 1,
                "toolTotal": 3,
                "toolCategory": "verification",
                "toolPhaseId": "verification",
                "toolPhaseLabel": "验证",
                "toolSemanticParentId": "phase:verification",
                "toolSemanticParentLabel": "验证",
                "toolName": "run_command",
                "result": {"status": "completed"},
            },
        )

        types = [e.type for e in collected]
        assert "content_start" in types
        assert "tool_use_complete" in types
        assert "tool_result" in types
        chat_types = [e.type for e in collected if e.visibility == "chat"]
        assert chat_types.index("content_start") < chat_types.index("tool_use_complete")
        assert chat_types.index("tool_use_complete") < chat_types.index("tool_result")
        raw_started = next(e for e in collected if e.type == "tool.started")
        raw_completed = next(e for e in collected if e.type == "tool.completed")
        assert raw_started.visibility == "trace"
        assert raw_completed.visibility == "trace"
        tool_use = next(e for e in collected if e.type == "tool_use_complete")
        assert tool_use.payload["toolUseId"] == "tc_1"
        assert tool_use.payload["parentToolUseId"] == "tc_parent"
        assert tool_use.payload["toolGroupId"] == "tgrp_1"
        assert tool_use.payload["toolIndex"] == 1
        assert tool_use.payload["toolTotal"] == 3
        assert tool_use.payload["toolCategory"] == "verification"
        assert tool_use.payload["toolPhaseId"] == "verification"
        assert tool_use.payload["toolPhaseLabel"] == "验证"
        assert tool_use.payload["toolSemanticParentId"] == "phase:verification"
        assert tool_use.payload["toolSemanticParentLabel"] == tool_use.payload["toolPhaseLabel"]
        assert tool_use.payload["input"]["command"] == "npm test"
        tool_result = next(e for e in collected if e.type == "tool_result")
        assert tool_result.payload["toolUseId"] == "tc_1"
        assert tool_result.payload["parentToolUseId"] == "tc_parent"
        assert tool_result.payload["toolGroupId"] == "tgrp_1"
        assert tool_result.payload["toolIndex"] == 1
        assert tool_result.payload["toolTotal"] == 3
        assert tool_result.payload["toolCategory"] == "verification"
        assert tool_result.payload["toolPhaseLabel"] == "验证"
        assert tool_result.payload["toolSemanticParentId"] == "phase:verification"
        assert tool_result.payload["toolSemanticParentLabel"] == tool_result.payload["toolPhaseLabel"]
        assert tool_result.payload["isError"] is False

    def test_tool_lifecycle_sanitizes_large_visible_arguments(self, tmp_path: Any) -> None:
        """Large tool inputs stay out of chat-visible lifecycle payloads."""
        runtime = _make_runtime(tmp_path)
        collected: list[RuntimeEvent] = []
        runtime.event_bus.subscribe(collected.append)

        large_content = "# Snake docs\n" + ("body\n" * 400)
        task = {"id": "t1", "role": "root", "activeAssistantMessageId": "msg_1"}
        runtime.orchestrator._publish(
            session_id="s1",
            task=task,
            event_type="tool.started",
            payload={
                "toolCallId": "tc_1",
                "toolName": "write_file",
                "arguments": {
                    "path": "README.md",
                    "content": large_content,
                    "mode": "overwrite",
                },
            },
        )

        tool_started = next(event for event in collected if event.type == "tool.started")
        tool_use = next(event for event in collected if event.type == "tool_use_complete")
        assert tool_started.payload["arguments"]["path"] == "README.md"
        assert tool_started.payload["arguments"]["mode"] == "overwrite"
        assert tool_started.payload["arguments"]["content"]["omitted"] is True
        assert tool_started.payload["arguments"]["content"]["chars"] == len(large_content)
        assert tool_use.payload["input"]["content"]["omitted"] is True
        assert tool_use.payload["input"]["content"]["chars"] == len(large_content)
        assert large_content not in json.dumps(tool_started.payload, ensure_ascii=False)
        assert large_content not in json.dumps(tool_use.payload, ensure_ascii=False)

    def test_core_tools_emit_standard_chat_tool_sequence(self, tmp_path: Any) -> None:
        """Core tools emit content_start/tool_use_complete/content_delta/tool_result."""
        tool_results = {
            "run_command": {
                "status": "completed",
                "stdout": "ok\n",
                "stderr": "",
                "exitCode": 0,
                "durationMs": 12,
                "commandLog": {"id": "cmd_1", "command": "npm test", "cwd": "."},
            },
            "read_file": {"status": "completed", "path": "README.md", "content": "hello\n", "bytesRead": 6},
            "search_files": {
                "status": "completed",
                "query": "hello",
                "total": 1,
                "matches": [{"path": "README.md", "line": 1, "text": "hello"}],
            },
            "apply_patch": {
                "status": "completed",
                "ok": True,
                "filesChanged": 1,
                "changedPaths": ["src/app.py"],
                "summary": "Patched src/app.py",
            },
        }

        def _tool(name: str):
            def handler(args: dict[str, Any]) -> dict[str, Any]:
                return dict(tool_results[name])

            return handler

        class FakeSubagentService:
            def dispatch(self, args: dict[str, Any]) -> dict[str, Any]:
                return {
                    "status": "completed",
                    "childTaskId": "child_1",
                    "summary": "Reviewed the patch.",
                    "result": {"summary": "Reviewed the patch."},
                }

        runtime = _make_runtime(
            tmp_path,
            tools={name: _tool(name) for name in tool_results},
            subagent_service=FakeSubagentService(),
        )
        collected: list[RuntimeEvent] = []
        runtime.event_bus.subscribe(collected.append)
        task = runtime.store.create_task(session_id="s1", task_type="chat", goal="g", plan=[])
        task["role"] = "root"
        workspace_root = str(tmp_path)
        cases = [
            (
                "run_command",
                {
                    "workspaceRoot": workspace_root,
                    "command": "npm test",
                    "cwd": ".",
                    "shell": "powershell",
                    "toolGroupId": "group_1",
                    "toolIndex": 0,
                    "toolTotal": 5,
                },
            ),
            (
                "read_file",
                {
                    "workspaceRoot": workspace_root,
                    "path": "README.md",
                    "toolGroupId": "group_1",
                    "toolIndex": 1,
                    "toolTotal": 5,
                },
            ),
            (
                "search_files",
                {
                    "workspaceRoot": workspace_root,
                    "query": "hello",
                    "mode": "content",
                    "toolGroupId": "group_1",
                    "toolIndex": 2,
                    "toolTotal": 5,
                },
            ),
            (
                "apply_patch",
                {
                    "workspaceRoot": workspace_root,
                    "patchText": "*** Begin Patch\n*** Update File: src/app.py\n@@\n-print('old')\n+print('new')\n*** End Patch",
                    "toolGroupId": "group_1",
                    "toolIndex": 3,
                    "toolTotal": 5,
                },
            ),
            (
                "task",
                {
                    "prompt": "Review the patch",
                    "title": "reviewer",
                    "toolGroupId": "group_1",
                    "toolIndex": 4,
                    "toolTotal": 5,
                },
            ),
        ]

        for name, arguments in cases:
            batch_metadata = {
                key: arguments[key]
                for key in ("toolGroupId", "toolIndex", "toolTotal")
                if key in arguments
            }
            runtime.orchestrator._execute_tool(
                session_id="s1",
                task=task,
                tool_spec={
                    "id": f"call_{name}",
                    "name": name,
                    "arguments": arguments,
                    "parentToolUseId": "call_parent" if name == "read_file" else None,
                    **batch_metadata,
                },
            )

        for name, _arguments in cases:
            tool_use_id = f"call_{name}"
            tool_events = [event for event in collected if event.payload.get("toolUseId") == tool_use_id]
            tool_event_types = [event.type for event in tool_events if event.visibility == "chat"]
            assert "content_start" in tool_event_types, name
            assert "tool_use_complete" in tool_event_types, name
            assert "tool_result" in tool_event_types, name
            assert tool_event_types.index("content_start") < tool_event_types.index("tool_use_complete")
            assert tool_event_types.index("tool_use_complete") < tool_event_types.index("tool_result")

            tool_use = next(event for event in tool_events if event.type == "tool_use_complete")
            tool_result = next(event for event in tool_events if event.type == "tool_result")
            assert tool_use.payload["toolName"] == name
            assert tool_use.payload["input"]
            assert tool_result.payload["toolUseId"] == tool_use_id
            assert "content" in tool_result.payload
            assert tool_result.payload["isError"] is False
            assert tool_result.payload["toolGroupId"] == "group_1"
            assert isinstance(tool_result.payload["toolIndex"], int)
            assert tool_result.payload["toolTotal"] == 5
            assert tool_result.payload["toolCategory"]
            assert tool_result.payload["toolPhaseId"]
            assert tool_result.payload["toolPhaseLabel"]
            assert tool_result.payload["toolSemanticParentId"]
            assert tool_result.payload["toolSemanticParentLabel"] == tool_result.payload["toolPhaseLabel"]

        read_tool_result = next(
            event for event in collected
            if event.type == "tool_result" and event.payload.get("toolUseId") == "call_read_file"
        )
        assert read_tool_result.payload["parentToolUseId"] == "call_parent"
        assert any(
            event.type == "content_delta"
            and event.payload.get("toolUseId") == "call_run_command"
            and event.payload.get("toolOutput") == "ok\n"
            for event in collected
        )
        assert any(
            event.type == "content_delta"
            and event.payload.get("toolUseId") == "call_search_files"
            and event.payload.get("outputStream") == "result_preview"
            for event in collected
        )

    def test_large_tool_results_are_slimmed_for_model_and_frontend(self, tmp_path: Any) -> None:
        """Large results keep raw execution data but expose compact model/chat content."""
        large_content = "HEAD\n" + ("x" * 20_000) + "\nTAIL"

        def read_large(_args: dict[str, Any]) -> dict[str, Any]:
            return {
                "status": "completed",
                "path": "big.log",
                "content": large_content,
                "bytesRead": len(large_content),
            }

        runtime = _make_runtime(tmp_path, tools={"read_file": read_large})
        collected: list[RuntimeEvent] = []
        runtime.event_bus.subscribe(collected.append)
        task = runtime.store.create_task(session_id="s1", task_type="chat", goal="g", plan=[])
        task["role"] = "root"

        tool_result = runtime.orchestrator._execute_tool(
            session_id="s1",
            task=task,
            tool_spec={
                "id": "call_read",
                "name": "read_file",
                "arguments": {"workspaceRoot": str(tmp_path), "path": "big.log"},
            },
        )
        tool_message = runtime.orchestrator._tool_result_message(  # noqa: SLF001
            {"id": "call_read"},
            tool_result,
        )

        assert tool_result["result"]["content"] == large_content
        assert tool_result["modelVisibleResult"]["truncated"] is True
        assert large_content not in tool_message["content"]
        assert len(tool_message["content"]) < 5000

        raw_completed = next(event for event in collected if event.type == "tool.completed")
        chat_result = next(
            event for event in collected
            if event.type == "tool_result" and event.payload.get("toolUseId") == "call_read"
        )
        assert raw_completed.payload["result"]["content"] == large_content
        assert chat_result.payload["content"]["truncated"] is True
        assert chat_result.payload["content"]["content"]["head"].startswith("HEAD")
        assert chat_result.payload["content"]["content"]["tail"].endswith("TAIL")
        assert large_content not in json.dumps(chat_result.payload, ensure_ascii=False)

    def test_large_command_output_is_slimmed_for_chat_delta(self, tmp_path: Any) -> None:
        """Command stdout/stderr traces stay raw while chat deltas use compact head/tail."""
        large_stdout = "start\n" + ("o" * 20_000) + "\nend"

        def run_large(_args: dict[str, Any]) -> dict[str, Any]:
            return {
                "status": "completed",
                "stdout": large_stdout,
                "stderr": "",
                "exitCode": 0,
                "durationMs": 10,
                "shell": "powershell",
                "cwd": ".",
                "commandLog": {
                    "id": "cmd_big",
                    "command": "fake big output",
                    "cwd": ".",
                    "stdoutPath": str(tmp_path / "cmd_big_stdout.log"),
                    "stderrPath": str(tmp_path / "cmd_big_stderr.log"),
                },
            }

        runtime = _make_runtime(tmp_path, tools={"run_command": run_large})
        collected: list[RuntimeEvent] = []
        runtime.event_bus.subscribe(collected.append)
        task = runtime.store.create_task(session_id="s1", task_type="chat", goal="g", plan=[])
        task["role"] = "root"

        tool_result = runtime.orchestrator._execute_tool(
            session_id="s1",
            task=task,
            tool_spec={
                "id": "call_cmd",
                "name": "run_command",
                "arguments": {
                    "workspaceRoot": str(tmp_path),
                    "command": "fake big output",
                    "cwd": ".",
                    "shell": "powershell",
                },
            },
        )

        raw_command_output = next(event for event in collected if event.type == "command.output")
        chat_delta = next(
            event for event in collected
            if event.type == "content_delta"
            and event.payload.get("toolUseId") == "call_cmd"
            and event.payload.get("outputStream") == "stdout"
        )
        chat_result = next(
            event for event in collected
            if event.type == "tool_result" and event.payload.get("toolUseId") == "call_cmd"
        )

        assert tool_result["result"]["stdout"] == large_stdout
        assert raw_command_output.payload["chunk"] == large_stdout
        assert large_stdout not in chat_delta.payload["toolOutput"]
        assert "full output is stored in the command log" in chat_delta.payload["toolOutput"]
        assert chat_result.payload["content"]["truncated"] is True
        assert chat_result.payload["content"]["stdout"]["head"].startswith("start")
        assert chat_result.payload["content"]["stdout"]["tail"].endswith("end")
        assert chat_result.payload["content"]["fullResultRef"]["commandLogId"] == "cmd_big"

    def test_provider_request_emits_chat_thinking_status(self, tmp_path: Any) -> None:
        """Provider requests emit a haha-cc style thinking status before output."""
        runtime = _make_runtime(tmp_path)
        collected: list[RuntimeEvent] = []
        runtime.event_bus.subscribe(collected.append)

        task = runtime.store.create_task(session_id="s1", task_type="chat", goal="g", plan=[])
        runtime.orchestrator._request_provider_response(
            session_id="s1",
            task=task,
            goal="g",
            provider_context={"step": 2, "config": {}},
            budget=None,
        )

        status_events = [event for event in collected if event.type == "status"]
        assert status_events
        assert status_events[0].payload["state"] == "thinking"
        assert status_events[0].payload["verb"] == "model"
        assert status_events[0].payload["step"] == 2
        assert status_events[0].payload["_chatCompat"] is True

    def test_task_lifecycle_emits_ordered_status_updates(self, tmp_path: Any) -> None:
        """Root task lifecycle events provide a stable Yuanbao-style status sequence."""
        runtime = _make_runtime(tmp_path)
        collected: list[RuntimeEvent] = []
        runtime.event_bus.subscribe(collected.append)

        task = {"id": "t1", "role": "root", "goal": "g", "activeAssistantMessageId": "msg_1"}
        runtime.orchestrator._publish(
            session_id="s1",
            task=task,
            event_type="task.started",
            payload={"status": "running", "step": 1},
        )
        runtime.orchestrator._publish(
            session_id="s1",
            task=task,
            event_type="tool.started",
            payload={"toolCallId": "call_1", "toolName": "read_file", "arguments": {"path": "README.md"}},
        )
        runtime.orchestrator._publish(
            session_id="s1",
            task=task,
            event_type="approval.requested",
            payload={"approvalId": "approval_1", "kind": "write_file", "request": {"path": "src/new.ts"}},
        )
        runtime.orchestrator._publish(
            session_id="s1",
            task=task,
            event_type="message.completed",
            payload={"messageId": "msg_1", "content": "done"},
        )
        runtime.orchestrator._publish(
            session_id="s1",
            task=task,
            event_type="task.completed",
            payload={"status": "completed", "summary": "done"},
        )

        status_events = [event for event in collected if event.type == "status"]
        assert [event.payload["state"] for event in status_events] == [
            "thinking",
            "tool_executing",
            "permission_pending",
            "idle",
        ]
        assert status_events[0].payload["verb"] == "task"
        assert status_events[1].payload["verb"] == "read_file"
        assert status_events[2].payload["verb"] == "write_file"
        assert all(event.payload["_chatCompat"] is True for event in status_events)

    def test_task_failed_and_cancelled_emit_idle_status(self, tmp_path: Any) -> None:
        """Terminal failure and cancellation clear chat status while preserving error events."""
        runtime = _make_runtime(tmp_path)
        failed: list[RuntimeEvent] = []
        runtime.event_bus.subscribe(failed.append)

        failed_task = {"id": "t_failed", "role": "root", "goal": "g", "activeAssistantMessageId": "msg_1"}
        runtime.orchestrator._publish(
            session_id="s1",
            task=failed_task,
            event_type="message.failed",
            payload={"messageId": "msg_1", "content": "failed", "errorCode": "TEST_ERROR"},
        )
        runtime.orchestrator._publish(
            session_id="s1",
            task=failed_task,
            event_type="task.failed",
            payload={"status": "failed", "summary": "failed", "errorCode": "TEST_ERROR"},
        )

        failed_statuses = [event for event in failed if event.type == "status" and event.payload["state"] == "idle"]
        assert len(failed_statuses) == 1
        assert any(
            event.type in {"message.failed", "task.failed"}
            and runtime.event_bus.as_payload(event).get("yuanbao", {}).get("type") == "error"
            for event in failed
        )

        cancelled: list[RuntimeEvent] = []
        runtime.event_bus.subscribe(cancelled.append)
        cancelled_task = {"id": "t_cancelled", "role": "root", "goal": "g"}
        runtime.orchestrator._publish(
            session_id="s1",
            task=cancelled_task,
            event_type="task.cancelled",
            payload={"status": "cancelled"},
        )

        cancelled_statuses = [
            event for event in cancelled
            if event.task_id == "t_cancelled" and event.type == "status"
        ]
        assert len(cancelled_statuses) == 1
        assert cancelled_statuses[0].payload["state"] == "idle"

    def test_approval_resolved_emits_chat_idle_status(self, tmp_path: Any) -> None:
        """Approval resolution clears chat thinking state for pending permission blocks."""
        runtime = _make_runtime(tmp_path)
        collected: list[RuntimeEvent] = []
        runtime.event_bus.subscribe(collected.append)

        task = {"id": "t1", "role": "root", "activeAssistantMessageId": "msg_1"}
        runtime.orchestrator._publish(
            session_id="s1",
            task=task,
            event_type="approval.resolved",
            payload={"approvalId": "approval_1", "taskId": "t1", "decision": "approved"},
        )

        status_events = [event for event in collected if event.type == "status"]
        assert len(status_events) == 1
        assert status_events[0].payload["state"] == "idle"
        assert status_events[0].payload["_chatCompat"] is True

    def test_approval_resolved_emits_resolved_permission_request(self, tmp_path: Any) -> None:
        """Non-computer approvals resolve through chat-compat permission_request too."""
        runtime = _make_runtime(tmp_path)
        collected: list[RuntimeEvent] = []
        runtime.event_bus.subscribe(collected.append)

        workspace = runtime.store.upsert_workspace(str(tmp_path))
        session = runtime.store.create_session(workspace_id=workspace["id"], title="session")
        task = runtime.store.create_task(session_id=session["id"], task_type="chat", goal="g", plan=[])
        approval = runtime.store.create_approval(
            task_id=task["id"],
            kind="write_file",
            request={"path": "src/new.ts", "risk": "writes file"},
        )

        runtime.orchestrator._publish(
            session_id=session["id"],
            task=task,
            event_type="approval.resolved",
            payload={
                "approvalId": approval["id"],
                "taskId": task["id"],
                "kind": "write_file",
                "request": {"path": "src/new.ts", "risk": "writes file"},
                "preview": [{"label": "文件", "value": "src/new.ts"}],
                "filesChanged": 1,
                "changedPaths": ["src/new.ts"],
                "diffText": "--- /dev/null\n+++ b/src/new.ts\n",
                "decision": "approved",
                "decidedBy": "user",
                "decidedAt": 123,
            },
        )

        permission_events = [event for event in collected if event.type == "permission_request"]
        assert len(permission_events) == 1
        permission = permission_events[0].payload
        assert permission["_chatCompat"] is True
        assert permission["requestId"] == approval["id"]
        assert permission["toolName"] == "write_file"
        assert permission["input"]["path"] == "src/new.ts"
        assert permission["preview"] == [{"label": "文件", "value": "src/new.ts"}]
        assert permission["filesChanged"] == 1
        assert permission["changedPaths"] == ["src/new.ts"]
        assert permission["diffText"].startswith("--- /dev/null")
        assert permission["resolved"] is True
        assert permission["decision"] == "approved"
        assert permission["decidedBy"] == "user"
        assert permission["decidedAt"] == 123

    def test_approval_resolved_uses_payload_details_when_store_request_missing(self, tmp_path: Any) -> None:
        """Detailed resolved payloads can restore permission cards without a local approval record."""
        runtime = _make_runtime(tmp_path)
        collected: list[RuntimeEvent] = []
        runtime.event_bus.subscribe(collected.append)

        task = {"id": "t1", "role": "root", "activeAssistantMessageId": "msg_1"}
        runtime.orchestrator._publish(
            session_id="s1",
            task=task,
            event_type="approval.resolved",
            payload={
                "approvalId": "approval_missing",
                "taskId": "t1",
                "kind": "write_file",
                "request": {"path": "src/new.ts"},
                "preview": [{"label": "文件", "value": "src/new.ts"}],
                "decision": "approved",
            },
        )

        permission_events = [event for event in collected if event.type == "permission_request"]
        assert len(permission_events) == 1
        permission = permission_events[0].payload
        assert permission["requestId"] == "approval_missing"
        assert permission["toolName"] == "write_file"
        assert permission["input"] == {"path": "src/new.ts"}
        assert permission["preview"] == [{"label": "文件", "value": "src/new.ts"}]
        assert permission["resolved"] is True

    def test_chat_compat_events_are_not_trace_mirrored(self, tmp_path: Any) -> None:
        """Chat compatibility events are live UI protocol, not trace timeline noise."""
        runtime = _make_runtime(tmp_path)
        task = runtime.store.create_task(session_id="s1", task_type="chat", goal="g", plan=[])

        runtime.orchestrator._publish(
            session_id="s1",
            task=task,
            event_type="content_delta",
            payload={"text": "hello"},
        )

        traces = runtime.store.list_trace_events({"taskId": task["id"]})["traceEvents"]
        assert traces == []


# ── 2. MCP server migration ──────────────────────────────────────────────


class TestMcpServerMigration:
    """Ensure mcp_servers table has all required columns after migration."""

    def test_mcp_server_columns_exist(self, tmp_path: Any) -> None:
        """All expected columns exist in mcp_servers after init."""
        store = SQLiteStore(str(tmp_path / "test.sqlite3"))
        columns = {
            row["name"]
            for row in store._conn.execute("PRAGMA table_info(mcp_servers)").fetchall()
        }
        for col in ("id", "name", "transport", "command", "args", "url",
                     "headers", "env", "enabled", "created_at", "updated_at"):
            assert col in columns, f"Column {col} missing from mcp_servers"

    def test_mcp_server_defaults(self, tmp_path: Any) -> None:
        """New MCP server gets proper defaults for JSON fields."""
        store = SQLiteStore(str(tmp_path / "test.sqlite3"))
        result = store.create_mcp_server({
            "name": "test-server",
            "transport": "stdio",
            "command": "npx",
            "enabled": False,
        })
        server = result["server"]
        assert server["args"] == []
        assert server["env"] == {}
        assert server["headers"] == {}
        assert server["transport"] == "stdio"
        assert server["enabled"] in (False, 0)

    def test_mcp_server_round_trip_preserves_all_fields(self, tmp_path: Any) -> None:
        """Create and retrieve preserves all config fields."""
        store = SQLiteStore(str(tmp_path / "test.sqlite3"))
        created = store.create_mcp_server({
            "name": "full-server",
            "transport": "stdio",
            "command": "python",
            "args": ["-m", "server"],
            "env": {"KEY": "val"},
            "headers": {"Authorization": "Bearer x"},
            "enabled": True,
        })
        server_id = created["server"]["id"]

        retrieved = store.get_mcp_server({"serverId": server_id})
        s = retrieved["server"]
        assert s["name"] == "full-server"
        assert s["command"] == "python"
        assert s["args"] == ["-m", "server"]
        assert s["env"] == {"KEY": "val"}
        assert s["headers"] == {"Authorization": "Bearer x"}
        assert s["enabled"] in (True, 1)


# ── 3. Feature flags ──────────────────────────────────────────────────────


class TestFeatureFlags:
    """Feature flag CRUD via SQLiteStore."""

    def test_default_features_in_config(self, tmp_path: Any) -> None:
        """Default config includes features block with multiAgent=True."""
        store = SQLiteStore(str(tmp_path / "test.sqlite3"))
        config = store.get_config({})
        features = config["config"].get("features", {})
        assert "multiAgent" in features
        assert features["multiAgent"] is True

    def test_get_feature_flag(self, tmp_path: Any) -> None:
        """get_feature_flag reads from config.features."""
        store = SQLiteStore(str(tmp_path / "test.sqlite3"))
        assert store.get_feature_flag("multiAgent") is True
        assert store.get_feature_flag("nonexistent", default=True) is True

    def test_set_feature_flag(self, tmp_path: Any) -> None:
        """set_feature_flag persists and can be read back."""
        store = SQLiteStore(str(tmp_path / "test.sqlite3"))
        result = store.set_feature_flag("multiAgent", True)
        assert result["features"]["multiAgent"] is True
        # Read back
        assert store.get_feature_flag("multiAgent") is True

    def test_list_feature_flags(self, tmp_path: Any) -> None:
        """list_feature_flags returns all flags."""
        store = SQLiteStore(str(tmp_path / "test.sqlite3"))
        flags = store.list_feature_flags()
        assert "multiAgent" in flags["features"]
        assert "streamingDeltaPersist" in flags["features"]

    def test_feature_flag_persists_across_store_instances(self, tmp_path: Any) -> None:
        """Feature flag survives store re-initialization."""
        db_path = str(tmp_path / "test.sqlite3")
        store1 = SQLiteStore(db_path)
        store1.set_feature_flag("multiAgent", True)

        store2 = SQLiteStore(db_path)
        assert store2.get_feature_flag("multiAgent") is True


class TestFeatureFlagsRpc:
    """Feature flag access via RPC."""

    def test_feature_list_rpc(self, tmp_path: Any) -> None:
        runtime = _make_runtime(tmp_path)
        resp = _rpc(runtime, "feature.list", {})
        assert "result" in resp
        assert "multiAgent" in resp["result"]["features"]

    def test_feature_set_rpc(self, tmp_path: Any) -> None:
        runtime = _make_runtime(tmp_path)
        resp = _rpc(runtime, "feature.set", {"key": "multiAgent", "value": True})
        assert "result" in resp
        assert resp["result"]["features"]["multiAgent"] is True

    def test_feature_set_missing_key_raises(self, tmp_path: Any) -> None:
        runtime = _make_runtime(tmp_path)
        resp = _rpc(runtime, "feature.set", {"value": True})
        assert "error" in resp
