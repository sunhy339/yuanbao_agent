"""Tests for MCP tool lifecycle events: mcp.tool.started/completed/failed."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from local_agent_runtime.event_bus import EventBus
from local_agent_runtime.policy.decision_advisor import AdviceResult
from local_agent_runtime.orchestrator.service import Orchestrator
from local_agent_runtime.rpc.server import JsonRpcServer
from local_agent_runtime.store.sqlite_store import SQLiteStore
from local_agent_runtime.tools.registry import ToolRegistry


# ── helpers ────────────────────────────────────────────────────────────────


def _make_runtime(
    tmp_path: Any,
    tools: dict[str, Any] | None = None,
    *,
    decision_advisor: Any | None = None,
) -> SimpleNamespace:
    event_bus = EventBus()
    store = SQLiteStore(str(tmp_path / "runtime.sqlite3"))
    tool_registry = ToolRegistry(tools or {})
    orchestrator = Orchestrator(
        store=store,
        event_bus=event_bus,
        tool_registry=tool_registry,
        provider=None,
        decision_advisor=decision_advisor,
    )
    server = JsonRpcServer(orchestrator=orchestrator, store=store, event_bus=event_bus)
    events: list[dict[str, Any]] = []
    event_bus.subscribe(lambda event: events.append(event_bus.as_payload(event)))
    return SimpleNamespace(
        orchestrator=orchestrator, store=store, events=events,
        event_bus=event_bus, server=server,
    )


def _open_session(runtime: SimpleNamespace, tmp_path: Any) -> dict[str, Any]:
    import json
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(exist_ok=True)
    ws_resp = runtime.server.handle_line(json.dumps({
        "jsonrpc": "2.0", "id": "ws1", "method": "workspace.open",
        "params": {"path": str(workspace_root)},
    }))
    ws = ws_resp["result"]["workspace"]
    sess_resp = runtime.server.handle_line(json.dumps({
        "jsonrpc": "2.0", "id": "s1", "method": "session.create",
        "params": {"workspaceId": ws["id"], "title": "Test"},
    }))
    return sess_resp["result"]["session"]


# ── tests ──────────────────────────────────────────────────────────────────


class TestMcpToolLifecycleEvents:
    """Verify MCP-specific tool lifecycle events are published."""

    def test_mcp_tool_started_completed(self, tmp_path: Any) -> None:
        """mcp__ prefixed tool triggers mcp.tool.started + mcp.tool.completed."""
        def _mcp_tool(args: dict[str, Any]) -> dict[str, Any]:
            return {"status": "ok", "ok": True, "output": "done"}

        runtime = _make_runtime(tmp_path, tools={"mcp__postgres__query": _mcp_tool})
        session = _open_session(runtime, tmp_path)
        tool_spec = {
            "name": "mcp__postgres__query",
            "arguments": {"sql": "SELECT 1"},
            "start_token": "<tool_call_begin>",
            "end_token": "<tool_call_end>",
        }
        task = {"id": runtime.store.new_id("tsk"), "status": "running"}

        runtime.orchestrator._execute_tool(session["id"], task, tool_spec)

        mcp_started = [e for e in runtime.events if e["type"] == "mcp.tool.started"]
        mcp_completed = [e for e in runtime.events if e["type"] == "mcp.tool.completed"]
        assert len(mcp_started) == 1
        assert mcp_started[0]["payload"]["toolName"] == "mcp__postgres__query"
        assert mcp_started[0]["payload"]["serverId"] == "postgres"
        assert len(mcp_completed) == 1
        assert mcp_completed[0]["payload"]["serverId"] == "postgres"
        assert mcp_completed[0]["payload"]["ok"] is True

    def test_mcp_tool_failed_via_registry(self, tmp_path: Any) -> None:
        """Tool handler raising exception is caught by registry, returns failed result.

        ToolRegistry.execute catches exceptions and returns {"status": "failed", ...}.
        _execute_tool's _tool_failed branch detects this and emits mcp.tool.failed.
        """

        def _failing_tool(args: dict[str, Any]) -> dict[str, Any]:
            raise RuntimeError("connection refused")

        runtime = _make_runtime(tmp_path, tools={"mcp__redis__get": _failing_tool})
        session = _open_session(runtime, tmp_path)
        tool_spec = {
            "name": "mcp__redis__get",
            "arguments": {"key": "foo"},
            "start_token": "<tool_call_begin>",
            "end_token": "<tool_call_end>",
        }
        task = {"id": runtime.store.new_id("tsk"), "status": "running"}

        runtime.orchestrator._execute_tool(session["id"], task, tool_spec)

        mcp_started = [e for e in runtime.events if e["type"] == "mcp.tool.started"]
        mcp_failed = [e for e in runtime.events if e["type"] == "mcp.tool.failed"]
        mcp_completed = [e for e in runtime.events if e["type"] == "mcp.tool.completed"]
        assert len(mcp_started) == 1
        assert mcp_started[0]["payload"]["serverId"] == "redis"
        # Registry catches exception → _tool_failed returns True → mcp.tool.failed
        assert len(mcp_failed) == 1
        assert mcp_failed[0]["payload"]["serverId"] == "redis"
        assert "connection refused" in mcp_failed[0]["payload"]["error"]
        # No completed event for failed tool
        assert len(mcp_completed) == 0

    def test_mcp_tool_failure_records_advisor_recovery_decision(self, tmp_path: Any) -> None:
        """Failed MCP tools ask advisor for a bounded recovery action."""

        class Advisor:
            def __init__(self) -> None:
                self.calls: list[tuple[str, dict[str, Any]]] = []

            def advise(self, kind: str, context: dict[str, Any]) -> AdviceResult:
                self.calls.append((kind, context))
                return AdviceResult(
                    proposal_id="tool_recovery_1",
                    kind=kind,
                    payload={
                        "action": "refresh_mcp_tools",
                        "refreshMcpTools": True,
                        "reason": "Refresh MCP tools, then retry the lookup through the normal tool pipeline.",
                    },
                    confidence=0.88,
                    rationale="The MCP server appears disconnected.",
                    source="llm",
                    accepted=True,
                )

        def _failing_tool(args: dict[str, Any]) -> dict[str, Any]:
            return {
                "status": "failed",
                "ok": False,
                "error": "MCP server kb is unavailable",
            }

        advisor = Advisor()
        runtime = _make_runtime(
            tmp_path,
            tools={"mcp__kb__lookup": _failing_tool},
            decision_advisor=advisor,
        )
        session = _open_session(runtime, tmp_path)
        task = runtime.store.create_task(
            session_id=session["id"],
            task_type="main",
            goal="Use KB evidence",
            plan=[],
        )
        tool_spec = {
            "name": "mcp__kb__lookup",
            "arguments": {"query": "release checklist", "apiToken": "secret"},
            "start_token": "<tool_call_begin>",
            "end_token": "<tool_call_end>",
        }

        tool_result = runtime.orchestrator._execute_tool(session["id"], task, tool_spec)

        assert advisor.calls[0][0] == "tool_recovery"
        advisor_context = advisor.calls[0][1]
        assert advisor_context["tool_failure"]["failureKind"] == "mcp_server_unavailable"
        assert advisor_context["tool_failure"]["mcpServerId"] == "kb"
        assert advisor_context["tool_failure"]["arguments"]["apiToken"] == "<redacted>"
        result = tool_result["result"]
        assert result["failureKind"] == "mcp_server_unavailable"
        assert result["recoveryDecision"]["action"] == "refresh_mcp_tools"
        assert result["recoveryDecision"]["advisorAccepted"] is True
        assert result["recoveryDecision"]["execution"] == "not_auto_executed"
        recovery_events = [e for e in runtime.events if e["type"] == "agent.decision.tool_recovery"]
        assert len(recovery_events) == 1
        assert recovery_events[0]["payload"]["decision"]["action"] == "refresh_mcp_tools"
        proposals = runtime.store.list_proposals({"taskId": task["id"], "kind": "tool_recovery"})["proposals"]
        assert len(proposals) == 2
        assert {proposal["source"]["type"] for proposal in proposals} == {"llm", "runtime_bounded_recovery"}

    def test_partial_tool_result_uses_recovery_advisor(self, tmp_path: Any) -> None:
        """Partial responses are recovery decisions, not silent success."""

        class Advisor:
            def __init__(self) -> None:
                self.calls: list[tuple[str, dict[str, Any]]] = []

            def advise(self, kind: str, context: dict[str, Any]) -> AdviceResult:
                self.calls.append((kind, context))
                return AdviceResult(
                    proposal_id="tool_recovery_partial",
                    kind=kind,
                    payload={
                        "action": "use_partial_evidence",
                        "usePartialEvidence": True,
                        "reason": "The partial result is enough for this task.",
                    },
                    confidence=0.76,
                    rationale="Partial response contains usable evidence.",
                    source="llm",
                    accepted=True,
                )

        def _partial_tool(args: dict[str, Any]) -> dict[str, Any]:
            return {
                "status": "partial",
                "ok": True,
                "summary": "Only 3 of 10 records returned.",
            }

        advisor = Advisor()
        runtime = _make_runtime(
            tmp_path,
            tools={"mcp__kb__lookup": _partial_tool},
            decision_advisor=advisor,
        )
        session = _open_session(runtime, tmp_path)
        task = runtime.store.create_task(
            session_id=session["id"],
            task_type="main",
            goal="Use partial KB evidence",
            plan=[],
        )
        tool_spec = {
            "name": "mcp__kb__lookup",
            "arguments": {"query": "release checklist"},
            "start_token": "<tool_call_begin>",
            "end_token": "<tool_call_end>",
        }

        tool_result = runtime.orchestrator._execute_tool(session["id"], task, tool_spec)

        assert advisor.calls[0][0] == "tool_recovery"
        assert advisor.calls[0][1]["tool_failure"]["failureKind"] == "partial_response"
        assert tool_result["result"]["recoveryDecision"]["action"] == "use_partial_evidence"
        assert [e for e in runtime.events if e["type"] == "tool.failed"]
        assert [e for e in runtime.events if e["type"] == "agent.decision.tool_recovery"]

    def test_non_mcp_tool_no_mcp_events(self, tmp_path: Any) -> None:
        """Non-MCP tool should not emit any mcp.tool.* events."""

        def _custom_tool(args: dict[str, Any]) -> dict[str, Any]:
            return {"status": "ok", "ok": True, "output": "content"}

        runtime = _make_runtime(tmp_path, tools={"custom_tool": _custom_tool})
        session = _open_session(runtime, tmp_path)
        tool_spec = {
            "name": "custom_tool",
            "arguments": {"key": "value"},
            "start_token": "<tool_call_begin>",
            "end_token": "<tool_call_end>",
        }
        task = {"id": runtime.store.new_id("tsk"), "status": "running"}

        runtime.orchestrator._execute_tool(session["id"], task, tool_spec)

        mcp_events = [e for e in runtime.events if e["type"].startswith("mcp.tool.")]
        assert len(mcp_events) == 0
        # Generic events should still fire
        assert any(e["type"] == "tool.started" for e in runtime.events)
        assert any(e["type"] == "tool.completed" for e in runtime.events)

    def test_mcp_tool_server_id_parsed(self, tmp_path: Any) -> None:
        """Server ID is correctly parsed from mcp__{server}__{tool} pattern."""

        def _tool(args: dict[str, Any]) -> dict[str, Any]:
            return {"ok": True}

        runtime = _make_runtime(tmp_path, tools={"mcp__my_server__my_tool": _tool})
        session = _open_session(runtime, tmp_path)
        tool_spec = {
            "name": "mcp__my_server__my_tool",
            "arguments": {},
            "start_token": "<t>",
            "end_token": "</t>",
        }
        task = {"id": runtime.store.new_id("tsk"), "status": "running"}

        runtime.orchestrator._execute_tool(session["id"], task, tool_spec)

        started = [e for e in runtime.events if e["type"] == "mcp.tool.started"]
        assert started[0]["payload"]["serverId"] == "my_server"
        assert started[0]["payload"]["toolName"] == "mcp__my_server__my_tool"


class TestMcpToolTimeout:
    """Verify mcp.tool.timeout event is published when tool call times out."""

    def test_mcp_tool_timeout_event(self, tmp_path: Any) -> None:
        """MCP tool returning timeout=True triggers mcp.tool.timeout event."""

        def _slow_tool(args: dict[str, Any]) -> dict[str, Any]:
            return {
                "status": "failed",
                "ok": False,
                "error": "MCP tool mcp__db__query timed out after 120s",
                "timeout": True,
            }

        runtime = _make_runtime(tmp_path, tools={"mcp__db__query": _slow_tool})
        session = _open_session(runtime, tmp_path)
        tool_spec = {
            "name": "mcp__db__query",
            "arguments": {"sql": "SELECT SLEEP(999)"},
            "start_token": "<t>",
            "end_token": "</t>",
        }
        task = {"id": runtime.store.new_id("tsk"), "status": "running"}

        runtime.orchestrator._execute_tool(session["id"], task, tool_spec)

        timeout_events = [e for e in runtime.events if e["type"] == "mcp.tool.timeout"]
        assert len(timeout_events) == 1
        payload = timeout_events[0]["payload"]
        assert payload["toolName"] == "mcp__db__query"
        assert payload["serverId"] == "db"
        assert payload["timeout"] is True
        assert "timed out" in payload["error"]

    def test_mcp_tool_timeout_payload_readable(self, tmp_path: Any) -> None:
        """Timeout error message is human-readable with duration info."""

        def _timeout_tool(args: dict[str, Any]) -> dict[str, Any]:
            return {
                "status": "failed",
                "ok": False,
                "error": "MCP tool mcp__api__fetch timed out after 60s",
                "timeout": True,
            }

        runtime = _make_runtime(tmp_path, tools={"mcp__api__fetch": _timeout_tool})
        session = _open_session(runtime, tmp_path)
        tool_spec = {
            "name": "mcp__api__fetch",
            "arguments": {"url": "https://slow.example.com"},
            "start_token": "<t>",
            "end_token": "</t>",
        }
        task = {"id": runtime.store.new_id("tsk"), "status": "running"}

        runtime.orchestrator._execute_tool(session["id"], task, tool_spec)

        timeout_events = [e for e in runtime.events if e["type"] == "mcp.tool.timeout"]
        assert len(timeout_events) == 1
        error_msg = timeout_events[0]["payload"]["error"]
        assert "timed out" in error_msg
        assert "60s" in error_msg
