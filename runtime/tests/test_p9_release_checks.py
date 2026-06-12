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

    def test_message_delta_persists_for_events_after_without_flat_replay(self, tmp_path: Any) -> None:
        """message.delta is the recoverable React event source, not a second flat replay stream."""
        runtime = _make_runtime(tmp_path)
        workspace_root = tmp_path / "workspace"
        workspace_root.mkdir()
        workspace = runtime.store.upsert_workspace(str(workspace_root))
        session = runtime.store.create_session(workspace_id=workspace["id"], title="replay")
        task = runtime.store.create_task(
            session_id=session["id"],
            task_type="chat",
            goal="stream",
            plan=[],
            status="running",
        )
        task["activeAssistantMessageId"] = "msg_42"

        runtime.orchestrator._publish(
            session_id=session["id"],
            task=task,
            event_type="assistant.token",
            payload={"delta": "hello"},
        )

        raw_events = runtime.store.events_after(session["id"], 0)["events"]
        token_event = next(event for event in raw_events if event["type"] == "assistant.token")
        assert token_event["visibility"] == "trace"
        assert "yuanbao" not in token_event
        delta_event = next(event for event in raw_events if event["type"] == "message.delta")
        assert delta_event["visibility"] == "chat"
        assert delta_event["payload"]["messageId"] == "msg_42"
        assert delta_event["payload"]["delta"] == "hello"
        assert delta_event["payload"]["_bridge"]["persistTraceMirror"] is True
        assert delta_event["payload"]["_bridge"]["suppressRealtimeFlat"] is True
        assert delta_event["payload"]["_bridge"]["suppressChatReplay"] is True
        assert "yuanbao" not in delta_event
        assert "hahaCc" not in delta_event

        replay = _rpc(
            runtime,
            "events.yuanbaoAfter",
            {"sessionId": session["id"], "afterSeq": 0},
        )["result"]
        assert replay["messages"] == [{"type": "content_start", "blockType": "text"}]

    def test_live_and_replay_flat_text_sequence_match(self, tmp_path: Any) -> None:
        """Refreshing must not introduce a second text stream that live never showed."""
        runtime = _make_runtime(tmp_path)
        workspace_root = tmp_path / "workspace"
        workspace_root.mkdir()
        workspace = runtime.store.upsert_workspace(str(workspace_root))
        session = runtime.store.create_session(workspace_id=workspace["id"], title="live replay")
        task = runtime.store.create_task(
            session_id=session["id"],
            task_type="chat",
            goal="stream",
            plan=[],
            status="running",
        )
        task["activeAssistantMessageId"] = "msg_42"
        live: list[dict[str, Any]] = []
        runtime.event_bus.subscribe(lambda event: live.append(runtime.event_bus.as_payload(event)))

        runtime.orchestrator._publish(
            session_id=session["id"],
            task=task,
            event_type="assistant.token",
            payload={"delta": "hello"},
        )

        replay_events = runtime.store.events_after(session["id"], 0)["events"]
        live_flat = [
            (event["type"], event["yuanbao"]["type"], event["yuanbao"].get("text"))
            for event in live
            if isinstance(event.get("yuanbao"), dict)
        ]
        replay_flat = [
            (event["type"], event["yuanbao"]["type"], event["yuanbao"].get("text"))
            for event in replay_events
            if isinstance(event.get("yuanbao"), dict)
        ]
        assert [
            event["visibility"]
            for event in replay_events
            if event["type"] == "assistant.token"
        ] == ["trace"]
        assert live_flat == [
            ("content_start", "content_start", None),
            ("message.delta", "content_delta", "hello"),
        ]
        assert replay_flat == [("content_start", "content_start", None)]
        replay_delta = next(event for event in replay_events if event["type"] == "message.delta")
        assert replay_delta["payload"]["delta"] == "hello"
        assert replay_delta["payload"]["_bridge"]["persistTraceMirror"] is True

    def test_live_event_ids_and_sequences_match_persisted_replay(self, tmp_path: Any) -> None:
        """Live envelopes use the same cursor identity as events.after replay."""
        runtime = _make_runtime(tmp_path)
        workspace_root = tmp_path / "workspace"
        workspace_root.mkdir()
        workspace = runtime.store.upsert_workspace(str(workspace_root))
        session = runtime.store.create_session(workspace_id=workspace["id"], title="live cursor")
        task = runtime.store.create_task(
            session_id=session["id"],
            task_type="chat",
            goal="stream",
            plan=[],
            status="running",
        )
        task["activeAssistantMessageId"] = "msg_42"
        live: list[dict[str, Any]] = []
        runtime.event_bus.subscribe(lambda event: live.append(runtime.event_bus.as_payload(event)))

        runtime.orchestrator._publish(
            session_id=session["id"],
            task=task,
            event_type="assistant.token",
            payload={"delta": "hello"},
        )

        replay_events = runtime.store.events_after(session["id"], 0)["events"]
        replay_by_id = {event["id"]: event for event in replay_events}
        for event in live:
            replay = replay_by_id.get(event["eventId"])
            assert replay is not None
            assert event["seq"] == replay["sequence"]

    def test_task_planning_progress_panel_events_do_not_emit_flat_chat_messages(self, tmp_path: Any) -> None:
        """Task progress panels survive refresh as panel events, not flat chat protocol."""
        runtime = _make_runtime(tmp_path)
        workspace_root = tmp_path / "workspace"
        workspace_root.mkdir()
        workspace = runtime.store.upsert_workspace(str(workspace_root))
        session = runtime.store.create_session(workspace_id=workspace["id"], title="progress")
        task = runtime.store.create_task(
            session_id=session["id"],
            task_type="chat",
            goal="stream",
            plan=[],
            status="running",
        )

        runtime.orchestrator._publish(
            session_id=session["id"],
            task=task,
            event_type="task.planning.progress",
            payload={
                "summary": "Inspecting repo",
                "phase": "inspect",
                "mode": "planning",
                "status": "running",
            },
            visibility="panel",
        )

        raw_events = runtime.store.events_after(session["id"], 0)["events"]
        progress_event = next(event for event in raw_events if event["type"] == "task.planning.progress")
        assert progress_event["visibility"] == "panel"
        assert "_chatCompat" not in progress_event["payload"]
        assert "yuanbao" not in progress_event
        assert "hahaCc" not in progress_event

        replay = _rpc(
            runtime,
            "events.yuanbaoAfter",
            {"sessionId": session["id"], "afterSeq": 0},
        )["result"]
        assert replay["messages"] == []

    def test_assistant_token_emits_single_chat_message_delta(self, tmp_path: Any) -> None:
        """assistant.token emits one haha-cc style text delta via message.delta."""
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

        content_events = [e for e in collected if e.type == "content_delta" and e.payload.get("text") == "hello"]
        assert content_events == []
        delta_events = [e for e in collected if e.type == "message.delta"]
        assert len(delta_events) == 1
        assert delta_events[0].payload["delta"] == "hello"
        assert delta_events[0].payload["messageId"] == "msg_42"
        assert delta_events[0].payload["_chatCompat"] is True
        assert runtime.event_bus.as_payload(delta_events[0])["yuanbao"] == {
            "type": "content_delta",
            "text": "hello",
        }

    def test_text_blocks_keep_monotonic_ids_across_tool_calls(self, tmp_path: Any) -> None:
        """Text chunks before and after tool calls must not overwrite the first assistant block."""
        runtime = _make_runtime(tmp_path)
        collected: list[RuntimeEvent] = []
        runtime.event_bus.subscribe(collected.append)

        task = {"id": "t1", "role": "root", "activeAssistantMessageId": "msg_42"}
        runtime.orchestrator._publish(
            session_id="s1",
            task=task,
            event_type="assistant.token",
            payload={"delta": "first"},
        )
        runtime.orchestrator._publish(
            session_id="s1",
            task=task,
            event_type="tool.started",
            payload={"toolCallId": "call_1", "toolName": "read_file", "arguments": {"path": "a.md"}, "target": "a.md"},
        )
        runtime.orchestrator._publish(
            session_id="s1",
            task=task,
            event_type="tool.completed",
            payload={"toolCallId": "call_1", "toolName": "read_file", "result": {"content": "ok"}, "target": "a.md"},
        )
        runtime.orchestrator._publish(
            session_id="s1",
            task=task,
            event_type="assistant.token",
            payload={"delta": "second"},
        )
        runtime.orchestrator._publish(
            session_id="s1",
            task=task,
            event_type="tool.started",
            payload={"toolCallId": "call_2", "toolName": "read_file", "arguments": {"path": "b.md"}, "target": "b.md"},
        )
        runtime.orchestrator._publish(
            session_id="s1",
            task=task,
            event_type="tool.completed",
            payload={"toolCallId": "call_2", "toolName": "read_file", "result": {"content": "ok"}, "target": "b.md"},
        )
        runtime.orchestrator._publish(
            session_id="s1",
            task=task,
            event_type="assistant.token",
            payload={"delta": "third"},
        )

        text_starts = [
            event.payload
            for event in collected
            if event.type == "content_start" and event.payload.get("blockType") == "text"
        ]
        deltas = [event.payload for event in collected if event.type == "message.delta"]

        assert [payload.get("blockIndex") for payload in text_starts] == [0, 1, 2]
        assert [payload.get("contentBlockId") for payload in text_starts] == [
            "msg_42:text:0",
            "msg_42:text:1",
            "msg_42:text:2",
        ]
        assert [payload.get("blockIndex") for payload in deltas] == [0, 1, 2]
        assert [payload.get("contentBlockId") for payload in deltas] == [
            "msg_42:text:0",
            "msg_42:text:1",
            "msg_42:text:2",
        ]

    def test_text_block_state_recovers_after_resume(self, tmp_path: Any) -> None:
        """Approval/resume paths reload tasks, so block numbering must recover from trace history."""
        runtime = _make_runtime(tmp_path)
        workspace_root = tmp_path / "workspace"
        workspace_root.mkdir()
        workspace = runtime.store.upsert_workspace(str(workspace_root))
        session = runtime.store.create_session(workspace_id=workspace["id"], title="resume")
        task = runtime.store.create_task(
            session_id=session["id"],
            task_type="chat",
            goal="stream",
            plan=[],
            status="running",
        )
        task["activeAssistantMessageId"] = "msg_42"

        runtime.orchestrator._publish(
            session_id=session["id"],
            task=task,
            event_type="assistant.token",
            payload={"delta": "first"},
        )
        runtime.orchestrator._publish(
            session_id=session["id"],
            task=task,
            event_type="tool.started",
            payload={"toolCallId": "call_1", "toolName": "write_file", "arguments": {"path": "a.md"}, "target": "a.md"},
        )
        runtime.orchestrator._publish(
            session_id=session["id"],
            task=task,
            event_type="tool.completed",
            payload={"toolCallId": "call_1", "toolName": "write_file", "result": {"content": "ok"}, "target": "a.md"},
        )
        runtime.orchestrator._chat_text_stream_state = {}  # noqa: SLF001

        collected: list[RuntimeEvent] = []
        runtime.event_bus.subscribe(collected.append)
        reloaded_task = runtime.store.get_task({"taskId": task["id"]})["task"]
        reloaded_task["activeAssistantMessageId"] = "msg_42"
        runtime.orchestrator._publish(
            session_id=session["id"],
            task=reloaded_task,
            event_type="assistant.token",
            payload={"delta": "second"},
        )

        text_start = next(
            event.payload
            for event in collected
            if event.type == "content_start" and event.payload.get("blockType") == "text"
        )
        delta = next(event.payload for event in collected if event.type == "message.delta")
        assert text_start["blockIndex"] == 1
        assert text_start["contentBlockId"] == "msg_42:text:1"
        assert delta["blockIndex"] == 1
        assert delta["contentBlockId"] == "msg_42:text:1"

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
        assert tool_started.payload["arguments"]["content"] == large_content
        assert "content" not in tool_use.payload["input"]
        assert tool_use.payload["input"]["contentChars"] == len(large_content)
        assert large_content not in json.dumps(tool_use.payload, ensure_ascii=False)

    def test_tool_use_complete_hides_runtime_bound_arguments(self, tmp_path: Any) -> None:
        """Chat-visible tool inputs keep model intent, not runtime worktree bindings."""
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
                "toolName": "read_file",
                "arguments": {
                    "path": "README.md",
                    "workspaceRoot": str(tmp_path),
                    "originalWorkspaceRoot": "D:/source/project",
                    "activeWorktreeId": "wt_123",
                    "ignore": ["node_modules", ".git"],
                    "max_bytes": 12000,
                    "encoding": "utf-8",
                },
            },
        )

        raw_started = next(event for event in collected if event.type == "tool.started")
        tool_use = next(event for event in collected if event.type == "tool_use_complete")
        assert raw_started.payload["arguments"]["workspaceRoot"] == str(tmp_path)
        assert tool_use.payload["input"] == {"path": "README.md"}
        encoded = json.dumps(tool_use.payload, ensure_ascii=False)
        for key in ("workspaceRoot", "originalWorkspaceRoot", "activeWorktreeId", "ignore", "max_bytes", "encoding"):
            assert key not in encoded

    def test_tool_result_hides_runtime_bound_result_fields(self, tmp_path: Any) -> None:
        """Chat-visible tool outputs stay public even when the raw trace result is small."""
        runtime = _make_runtime(tmp_path)
        collected: list[RuntimeEvent] = []
        runtime.event_bus.subscribe(collected.append)

        task = {"id": "t1", "role": "root", "activeAssistantMessageId": "msg_1"}
        runtime.orchestrator._publish(
            session_id="s1",
            task=task,
            event_type="tool.completed",
            payload={
                "toolCallId": "tc_1",
                "toolName": "git_status",
                "target": ".",
                "result": {
                    "status": "completed",
                    "branch": "main",
                    "changes": [],
                    "workspaceRoot": str(tmp_path),
                    "originalWorkspaceRoot": "D:/source/project",
                    "activeWorktreeId": "wt_123",
                    "ignore": ["node_modules", ".git"],
                    "max_bytes": 12000,
                    "sessionId": "s1",
                    "taskId": "t1",
                    "steps": [
                        {
                            "label": "resolve",
                            "status": "completed",
                            "summary": str(tmp_path),
                            "workspaceRoot": str(tmp_path),
                        }
                    ],
                },
            },
        )

        raw_completed = next(event for event in collected if event.type == "tool.completed")
        chat_result = next(event for event in collected if event.type == "tool_result")
        assert raw_completed.payload["result"]["workspaceRoot"] == str(tmp_path)
        assert chat_result.payload["content"]["status"] == "completed"
        assert chat_result.payload["content"]["summary"]
        assert isinstance(chat_result.payload["content"].get("preview"), list)
        encoded = json.dumps(chat_result.payload, ensure_ascii=False)
        for key in (
            "workspaceRoot",
            "originalWorkspaceRoot",
            "activeWorktreeId",
            "ignore",
            "max_bytes",
            "sessionId",
            "taskId",
        ):
            assert key not in encoded

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
            assert tool_result.payload["toolIndex"] == _arguments["toolIndex"]
            assert tool_result.payload["toolTotal"] == _arguments["toolTotal"]
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
        task_tool_result = next(
            event for event in collected
            if event.type == "tool_result" and event.payload.get("toolUseId") == "call_task"
        )
        encoded_task_tool_result = json.dumps(task_tool_result.payload, ensure_ascii=False)
        assert "Reviewed the patch." in encoded_task_tool_result
        for internal_key in ("childTaskId", "workerId", "senderWorkerId"):
            assert internal_key not in encoded_task_tool_result

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
        assert chat_result.payload["content"]["status"] == "completed"
        assert chat_result.payload["content"]["summary"]
        assert chat_result.payload["content"]["target"] == "big.log"
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
        assert chat_result.payload["content"]["status"] == "completed"
        assert chat_result.payload["content"]["command"] == "fake big output"
        encoded_visible = json.dumps(chat_result.payload["content"], ensure_ascii=False)
        assert "fullResultRef" not in encoded_visible
        assert "rawResultStored" not in encoded_visible
        assert "rawResultSizeChars" not in encoded_visible

    def test_provider_request_emits_haha_cc_thinking_status(self, tmp_path: Any) -> None:
        """Provider requests emit haha-cc status without turning it into assistant thinking."""
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
        assert status_events[0].visibility == "chat"
        assert runtime.event_bus.as_payload(status_events[0])["yuanbao"] == {
            "type": "status",
            "state": "thinking",
            "verb": "model",
        }

    def test_task_lifecycle_emits_ordered_status_updates(self, tmp_path: Any) -> None:
        """Root lifecycle stays panel-only while status follows the haha-cc flat protocol."""
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
            "tool_executing",
            "permission_pending",
            "idle",
        ]
        assert status_events[0].payload["verb"] == "read_file"
        assert status_events[1].payload["verb"] == "write_file"
        assert all(event.visibility == "chat" for event in status_events)
        assert [runtime.event_bus.as_payload(event)["yuanbao"] for event in status_events] == [
            {"type": "status", "state": "tool_executing", "verb": "read_file"},
            {"type": "status", "state": "permission_pending", "verb": "write_file"},
            {"type": "status", "state": "idle"},
        ]
        assert next(event for event in collected if event.type == "task.started").visibility == "panel"
        assert next(event for event in collected if event.type == "task.completed").visibility == "panel"
        assert next(event for event in collected if event.type == "tool.started").visibility == "trace"
        assert next(event for event in collected if event.type == "approval.requested").visibility == "panel"
        assert next(event for event in collected if event.type == "message.completed").visibility == "trace"

    def test_root_task_lifecycle_panel_payloads_are_lightweight(self, tmp_path: Any) -> None:
        """Root task lifecycle panels should not leak heavy audit JSON into the chat transcript."""
        runtime = _make_runtime(tmp_path)
        collected: list[RuntimeEvent] = []
        runtime.event_bus.subscribe(collected.append)

        task = {
            "id": "t1",
            "role": "root",
            "goal": "g",
            "acceptanceCriteria": ["Resolve g"],
            "outOfScope": ["x"],
            "currentStep": "Answer",
        }
        runtime.orchestrator._publish(
            session_id="s1",
            task=task,
            event_type="task.completed",
            payload={
                "status": "completed",
                "summary": "done",
                "context": {"large": True},
                "completionEvidence": {"audit": {"raw": True}},
                "acceptanceCriteria": ["Resolve g"],
            },
        )

        completed = next(event for event in collected if event.type == "task.completed")
        assert completed.visibility == "panel"
        assert completed.payload == {
            "status": "completed",
            "goal": "g",
            "currentStep": "Answer",
            "summary": "done",
        }

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
        failed_flat = {
            event.type: runtime.event_bus.as_payload(event).get("yuanbao")
            for event in failed
            if event.type in {"message.failed", "task.failed"}
        }
        assert failed_flat["message.failed"]["type"] == "error"
        assert failed_flat["task.failed"] == {
            "type": "task_update",
            "taskId": "g",
            "taskLabel": "g",
            "status": "failed",
            "progress": "failed",
        }
        assert next(event for event in failed if event.type == "message.failed").visibility == "chat"
        assert next(event for event in failed if event.type == "task.failed").visibility == "panel"

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
        cancelled_raw = next(event for event in cancelled if event.task_id == "t_cancelled" and event.type == "task.cancelled")
        assert cancelled_raw.visibility == "panel"

    def test_internal_root_events_do_not_default_to_chat(self, tmp_path: Any) -> None:
        """Root internal lifecycle events stay in panel/trace instead of leaking into chat."""
        runtime = _make_runtime(tmp_path)
        collected: list[RuntimeEvent] = []
        runtime.event_bus.subscribe(collected.append)

        task = {"id": "t1", "role": "root", "goal": "g", "activeAssistantMessageId": "msg_1"}
        cases = [
            ("agent.decision.completion", {"decision": "complete"}, "trace"),
            ("runtime.context.prepared", {"mode": "model_first"}, "trace"),
            ("memory_event", {"summary": "remembered"}, "panel"),
            ("task.planning.decomposed", {"subtaskCount": 2}, "panel"),
            ("runtime.error", {"summary": "diagnostic only"}, "trace"),
        ]
        for event_type, payload, _visibility in cases:
            runtime.orchestrator._publish(
                session_id="s1",
                task=task,
                event_type=event_type,
                payload=payload,
            )

        raw_events = {
            event.type: event
            for event in collected
            if event.type in {event_type for event_type, _payload, _visibility in cases}
        }
        for event_type, _payload, visibility in cases:
            assert raw_events[event_type].visibility == visibility

    def test_approval_resolved_emits_trace_only_idle_status(self, tmp_path: Any) -> None:
        """Approval resolution records haha-cc idle status without rendering chat text."""
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
        assert status_events[0].visibility == "chat"
        assert runtime.event_bus.as_payload(status_events[0])["yuanbao"] == {
            "type": "status",
            "state": "idle",
        }

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
        diff_text = permission["diffText"]
        assert diff_text["preview"].startswith("--- /dev/null") if isinstance(diff_text, dict) else diff_text.startswith("--- /dev/null")
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
