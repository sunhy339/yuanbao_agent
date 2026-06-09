"""Tests for the Structured ReAct Turn Contract.

Covers:
  1. TurnDecision enum values and membership.
  2. ProviderTurnResult dataclass structure.
  3. _parse_turn_result inference (continue_with_tools, final_answer, failed).
  4. _parse_turn_result explicit decision detection (ask_user, needs_approval, blocked_by_policy).
  5. _parse_provider_response backward compatibility.
  6. Store schema: provider_turns has turn_decision and thought_summary columns.
  7. agent.decision.react_turn event published during ReAct loop.
"""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import pytest

from local_agent_runtime.event_bus import EventBus
from local_agent_runtime.orchestrator.service import Orchestrator
from local_agent_runtime.policy.guard import PolicyGuard
from local_agent_runtime.react.types import ProviderTurnResult, TurnDecision
from local_agent_runtime.services import CollaborationService, SubagentService
from local_agent_runtime.store.sqlite_store import SQLiteStore
from local_agent_runtime.tools import build_builtin_tools
from local_agent_runtime.tools.registry import ToolRegistry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FAKE_PROVIDER_CONFIG = {
    "mode": "mock",
    "apiKey": "sk-test",
    "baseUrl": "https://fake.local/v1",
    "model": "test-model",
}


def _make_store(tmp_path: Any) -> SQLiteStore:
    import pathlib
    store = SQLiteStore(str(pathlib.Path(str(tmp_path)) / "test.sqlite3"))
    store.update_config({"config": {"provider": FAKE_PROVIDER_CONFIG}})
    return store


def _make_orchestrator(
    tmp_path: Any,
    provider: Any,
) -> tuple[Orchestrator, SQLiteStore, list[dict[str, Any]]]:
    store = _make_store(tmp_path)
    event_bus = EventBus()
    config = store.get_config({})["config"]
    policy_guard = PolicyGuard(approval_mode=config["policy"]["approvalMode"])
    collaboration = CollaborationService(store, event_bus)
    subagent_service = SubagentService(store, collaboration)
    tool_registry = ToolRegistry(
        build_builtin_tools(policy_guard=policy_guard, store=store, subagent_service=subagent_service)
    )
    orchestrator = Orchestrator(
        store=store,
        event_bus=event_bus,
        tool_registry=tool_registry,
        provider=provider,
    )
    events: list[dict[str, Any]] = []
    event_bus.subscribe(lambda event: events.append(event_bus.as_payload(event)))
    return orchestrator, store, events


# ---------------------------------------------------------------------------
# Test: TurnDecision enum
# ---------------------------------------------------------------------------

class TestTurnDecision:
    """Verify TurnDecision enum values."""

    def test_all_six_decisions_exist(self) -> None:
        values = {d.value for d in TurnDecision}
        assert values == {
            "continue_with_tools", "final_answer", "ask_user",
            "needs_approval", "blocked_by_policy", "failed",
        }

    def test_string_comparison(self) -> None:
        assert TurnDecision.FINAL_ANSWER == "final_answer"
        assert TurnDecision.CONTINUE_WITH_TOOLS == "continue_with_tools"


# ---------------------------------------------------------------------------
# Test: ProviderTurnResult dataclass
# ---------------------------------------------------------------------------

class TestProviderTurnResult:
    """Verify ProviderTurnResult structure."""

    def test_defaults(self) -> None:
        result = ProviderTurnResult(
            decision=TurnDecision.FINAL_ANSWER,
            thought_summary="done",
            message="All good",
        )
        assert result.tool_calls == []
        assert result.final_answer is None
        assert result.why_complete is None
        assert result.remaining_risks == []
        assert result.policy_needs is None
        assert result.raw_response is None

    def test_full_construction(self) -> None:
        result = ProviderTurnResult(
            decision=TurnDecision.ASK_USER,
            thought_summary="need clarification",
            message="What do you mean?",
            tool_calls=[],
            final_answer=None,
            why_complete="User input required",
            remaining_risks=["incomplete spec"],
            policy_needs={"approval": "user_input"},
            raw_response={"decision": "ask_user"},
        )
        assert result.decision == TurnDecision.ASK_USER
        assert result.remaining_risks == ["incomplete spec"]


# ---------------------------------------------------------------------------
# Test: _parse_turn_result inference
# ---------------------------------------------------------------------------

class TestParseTurnResultInference:
    """_parse_turn_result infers decision from response shape."""

    @pytest.fixture
    def orchestrator(self, tmp_path: Any) -> Orchestrator:
        mock_provider = MagicMock()
        mock_provider.generate.return_value = {"message": "ok"}
        orc, _, _ = _make_orchestrator(tmp_path, mock_provider)
        return orc

    def test_tool_calls_inferred_as_continue(self, orchestrator: Orchestrator) -> None:
        response = {
            "message": "I'll read the file",
            "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}],
        }
        result = orchestrator._parse_turn_result(
            response, allow_fallback=False, allow_plain_message_final=True,
        )
        assert result.decision == TurnDecision.CONTINUE_WITH_TOOLS
        assert result.tool_calls == response["tool_calls"]

    def test_final_answer_inferred_from_final_key(self, orchestrator: Orchestrator) -> None:
        response = {"message": "The file has been fixed.", "final": "The file has been fixed."}
        result = orchestrator._parse_turn_result(
            response, allow_fallback=False, allow_plain_message_final=False,
        )
        assert result.decision == TurnDecision.FINAL_ANSWER
        assert result.final_answer == "The file has been fixed."

    def test_final_answer_inferred_from_plain_message(self, orchestrator: Orchestrator) -> None:
        response = {"message": "All done."}
        result = orchestrator._parse_turn_result(
            response, allow_fallback=False, allow_plain_message_final=True,
        )
        assert result.decision == TurnDecision.FINAL_ANSWER
        assert result.final_answer == "All done."

    def test_plain_message_final_is_not_reused_as_thought_summary(self, orchestrator: Orchestrator) -> None:
        response = {"message": "# Final\n\n- **Done**"}
        result = orchestrator._parse_turn_result(
            response, allow_fallback=False, allow_plain_message_final=True,
        )
        assert result.decision == TurnDecision.FINAL_ANSWER
        assert result.final_answer == "# Final\n\n- **Done**"
        assert result.thought_summary == ""

    def test_empty_response_fails_even_when_legacy_fallback_flag_is_allowed(self, orchestrator: Orchestrator) -> None:
        response = {"message": ""}
        result = orchestrator._parse_turn_result(
            response, allow_fallback=True, allow_plain_message_final=False,
        )
        assert result.decision == TurnDecision.FAILED

    def test_failed_inferred_no_fallback(self, orchestrator: Orchestrator) -> None:
        response = {"message": ""}
        result = orchestrator._parse_turn_result(
            response, allow_fallback=False, allow_plain_message_final=False,
        )
        assert result.decision == TurnDecision.FAILED

    def test_non_dict_response_is_failed(self, orchestrator: Orchestrator) -> None:
        result = orchestrator._parse_turn_result(
            "not a dict", allow_fallback=False, allow_plain_message_final=False,
        )
        assert result.decision == TurnDecision.FAILED


# ---------------------------------------------------------------------------
# Test: _parse_turn_result explicit decision
# ---------------------------------------------------------------------------

class TestParseTurnResultExplicit:
    """_parse_turn_result detects explicit decision field."""

    @pytest.fixture
    def orchestrator(self, tmp_path: Any) -> Orchestrator:
        mock_provider = MagicMock()
        orc, _, _ = _make_orchestrator(tmp_path, mock_provider)
        return orc

    def test_explicit_ask_user(self, orchestrator: Orchestrator) -> None:
        response = {
            "message": "I need clarification.",
            "decision": "ask_user",
        }
        result = orchestrator._parse_turn_result(
            response, allow_fallback=False, allow_plain_message_final=True,
        )
        assert result.decision == TurnDecision.ASK_USER
        assert result.message == "I need clarification."

    def test_explicit_needs_approval(self, orchestrator: Orchestrator) -> None:
        response = {
            "message": "This action requires approval.",
            "decision": "needs_approval",
            "policy_needs": {"tool": "shell", "reason": "shell execution"},
        }
        result = orchestrator._parse_turn_result(
            response, allow_fallback=False, allow_plain_message_final=True,
        )
        assert result.decision == TurnDecision.NEEDS_APPROVAL
        assert result.policy_needs == {"tool": "shell", "reason": "shell execution"}

    def test_explicit_blocked_by_policy(self, orchestrator: Orchestrator) -> None:
        response = {
            "message": "Cannot proceed.",
            "decision": "blocked_by_policy",
        }
        result = orchestrator._parse_turn_result(
            response, allow_fallback=False, allow_plain_message_final=True,
        )
        assert result.decision == TurnDecision.BLOCKED_BY_POLICY

    def test_explicit_decision_overrides_tool_calls(self, orchestrator: Orchestrator) -> None:
        """Even with tool_calls present, explicit decision takes priority."""
        response = {
            "message": "I should ask first.",
            "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}],
            "decision": "ask_user",
        }
        result = orchestrator._parse_turn_result(
            response, allow_fallback=False, allow_plain_message_final=True,
        )
        assert result.decision == TurnDecision.ASK_USER

    def test_explicit_decision_with_thought_summary(self, orchestrator: Orchestrator) -> None:
        response = {
            "message": "Task complete.",
            "decision": "final_answer",
            "thought_summary": "All files edited and tests pass",
            "why_complete": "Goal achieved",
            "remaining_risks": ["edge case in prod"],
        }
        result = orchestrator._parse_turn_result(
            response, allow_fallback=False, allow_plain_message_final=True,
        )
        assert result.decision == TurnDecision.FINAL_ANSWER
        assert result.thought_summary == "All files edited and tests pass"
        assert result.why_complete == "Goal achieved"
        assert result.remaining_risks == ["edge case in prod"]

    def test_unknown_explicit_decision_falls_back(self, orchestrator: Orchestrator) -> None:
        """Unknown decision string falls back to inference."""
        response = {
            "message": "Here's the answer.",
            "final": "Done",
            "decision": "something_unknown",
        }
        result = orchestrator._parse_turn_result(
            response, allow_fallback=False, allow_plain_message_final=True,
        )
        # Falls back to inference: has final_answer â?FINAL_ANSWER
        assert result.decision == TurnDecision.FINAL_ANSWER

    def test_camel_case_fields_recognized(self, orchestrator: Orchestrator) -> None:
        response = {
            "message": "Done",
            "decision": "final_answer",
            "thoughtSummary": "Quick summary",
            "whyComplete": "All done",
            "remainingRisks": ["minor risk"],
        }
        result = orchestrator._parse_turn_result(
            response, allow_fallback=False, allow_plain_message_final=True,
        )
        assert result.thought_summary == "Quick summary"
        assert result.why_complete == "All done"
        assert result.remaining_risks == ["minor risk"]


# ---------------------------------------------------------------------------
# Test: Store schema
# ---------------------------------------------------------------------------

class TestProviderTurnSchema:
    """Verify provider_turns table has turn_decision and thought_summary columns."""

    def test_columns_exist(self, tmp_path: Any) -> None:
        store = _make_store(tmp_path)
        try:
            columns = {
                row["name"]
                for row in store._conn.execute("PRAGMA table_info(provider_turns)").fetchall()
            }
            assert "turn_decision" in columns
            assert "thought_summary" in columns
        finally:
            store.close()

    def test_complete_turn_stores_decision(self, tmp_path: Any) -> None:
        store = _make_store(tmp_path)
        try:
            import pathlib, subprocess
            ws = pathlib.Path(str(tmp_path)) / "workspace"
            ws.mkdir()
            subprocess.run(["git", "init", str(ws)], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(ws), "config", "user.email", "t@t.com"],
                           check=True, capture_output=True)
            subprocess.run(["git", "-C", str(ws), "config", "user.name", "T"],
                           check=True, capture_output=True)
            workspace = store.upsert_workspace(str(ws))
            session = store.create_session(workspace_id=workspace["id"], title="test")
            task = store.create_task(
                session_id=session["id"],
                task_type="edit",
                goal="test goal",
                plan=[{"step": "do it"}],
            )

            turn = store.create_provider_turn(
                task_id=task["id"],
                session_id=session["id"],
                turn_index=0,
                model="test-model",
                request_message_count=1,
                request_tool_count=3,
                request_token_estimate=100,
            )
            completed = store.complete_provider_turn(
                turn_id=turn["id"],
                finish_reason="stop",
                usage={"total_tokens": 50},
                tool_call_count=0,
                turn_decision="final_answer",
                thought_summary="Task completed successfully",
            )
            assert completed["turn_decision"] == "final_answer"
            assert completed["thought_summary"] == "Task completed successfully"
        finally:
            store.close()


# ---------------------------------------------------------------------------
# Test: provider failure recovery retry
# ---------------------------------------------------------------------------

class TestProviderRecoveryRetry:
    def test_recoverable_provider_failure_retries_with_compacted_context(self, tmp_path: Any) -> None:
        from local_agent_runtime.provider.openai_compatible import ProviderAdapterError

        class FailsThenRecoversProvider:
            def __init__(self) -> None:
                self.contexts: list[dict[str, Any]] = []

            def generate(self, _goal: str, context: dict[str, Any]) -> dict[str, Any]:
                self.contexts.append(context)
                if len(self.contexts) == 1:
                    raise ProviderAdapterError("context_length_exceeded: maximum context length")
                return {"message": "Recovered", "final": "Recovered"}

        provider = FailsThenRecoversProvider()
        orchestrator, store, _events = _make_orchestrator(tmp_path, provider)
        workspace = store.upsert_workspace(str(tmp_path / "project"))
        session = store.create_session(workspace_id=workspace["id"], title="provider recovery")
        task = store.create_task(
            session_id=session["id"],
            task_type="chat",
            goal="Recover provider failure",
            plan=[],
            routing={"scenario": "provider_recovery"},
        )
        messages = [
            {"role": "system", "content": "You are helpful."},
            *[
                {"role": "user", "content": f"message {index} " + ("x" * 8000)}
                for index in range(10)
            ],
        ]

        response = orchestrator._request_non_streaming_provider_response(
            session_id=session["id"],
            task=task,
            goal="Recover provider failure",
            provider_context={"messages": messages, "config": {"provider": FAKE_PROVIDER_CONFIG}},
            budget=None,
        )

        assert response["final"] == "Recovered"
        assert len(provider.contexts) == 2
        retry_context = provider.contexts[1]
        assert retry_context["_provider_recovery_retry"]["category"] == "context_too_large"
        assert len(retry_context["messages"]) < len(messages)
        assert "provider recovery compacted middle" in retry_context["messages"][-1]["content"]
        trace_types = [
            event["type"]
            for event in store.list_trace_events({"taskId": task["id"]})["traceEvents"]
        ]
        assert "provider.failure.classified" in trace_types
        assert "provider.failure.recovery_retry" in trace_types
        assert trace_types[-1] == "provider.response"

    def test_auth_provider_failure_does_not_retry(self, tmp_path: Any) -> None:
        from local_agent_runtime.provider.openai_compatible import ProviderAdapterError

        class AuthFailureProvider:
            def __init__(self) -> None:
                self.calls = 0

            def generate(self, _goal: str, _context: dict[str, Any]) -> dict[str, Any]:
                self.calls += 1
                raise ProviderAdapterError("HTTP 401 Unauthorized: invalid api key")

        provider = AuthFailureProvider()
        orchestrator, store, _events = _make_orchestrator(tmp_path, provider)
        workspace = store.upsert_workspace(str(tmp_path / "project"))
        session = store.create_session(workspace_id=workspace["id"], title="provider recovery")
        task = store.create_task(
            session_id=session["id"],
            task_type="chat",
            goal="Recover provider failure",
            plan=[],
            routing={"scenario": "provider_recovery"},
        )

        with pytest.raises(ProviderAdapterError, match="401"):
            orchestrator._request_non_streaming_provider_response(
                session_id=session["id"],
                task=task,
                goal="Recover provider failure",
                provider_context={
                    "messages": [{"role": "user", "content": "hi"}],
                    "config": {"provider": FAKE_PROVIDER_CONFIG},
                },
                budget=None,
            )

        assert provider.calls == 1
        traces = store.list_trace_events({"taskId": task["id"]})["traceEvents"]
        trace_types = [event["type"] for event in traces]
        assert trace_types == [
            "provider.request",
            "provider.failure.recovery_decision",
            "provider.failure.classified",
            "provider.response",
        ]
        assert traces[-1]["payload"]["status"] == "failed"
        assert traces[-1]["payload"]["failureRecovery"]["category"] == "auth"


# ---------------------------------------------------------------------------
# Test: agent.decision.react_turn event during ReAct loop
# ---------------------------------------------------------------------------

class TestReactTurnEvent:
    """Verify agent.decision.react_turn event is published during ReAct execution."""

    def test_event_published_on_completion(self, tmp_path: Any) -> None:
        mock_provider = MagicMock()
        # Call #0: ReAct step 0 - tool call (read_file)
        # Call #1: ReAct step 0 â?tool call (read_file)
        # Call #2: ReAct step 1 â?final answer
        mock_provider.generate.side_effect = [
            {
                "message": "I'll read the file.",
                "tool_calls": [{
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "read_file",
                        "arguments": json.dumps({"path": "config.txt"}),
                    },
                }],
                "usage": {"total_tokens": 50},
            },
            {
                "message": "Done.",
                "final": "Done.",
                "usage": {"total_tokens": 60},
            },
        ]
        mock_provider.stream = MagicMock(side_effect=AttributeError("no stream"))

        orchestrator, store, events = _make_orchestrator(tmp_path, mock_provider)
        try:
            import pathlib, subprocess
            ws = pathlib.Path(str(tmp_path)) / "workspace"
            ws.mkdir()
            subprocess.run(["git", "init", str(ws)], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(ws), "config", "user.email", "t@t.com"],
                           check=True, capture_output=True)
            subprocess.run(["git", "-C", str(ws), "config", "user.name", "T"],
                           check=True, capture_output=True)
            (ws / "config.txt").write_text("hello", encoding="utf-8")

            workspace = store.upsert_workspace(str(ws))
            session = store.create_session(workspace_id=workspace["id"], title="turn-event-test")

            from local_agent_runtime.rpc.server import JsonRpcServer
            rpc_bus = EventBus()
            server = JsonRpcServer(orchestrator=orchestrator, store=store, event_bus=rpc_bus)
            envelope = {
                "jsonrpc": "2.0",
                "id": "req_1",
                "method": "message.send",
                "params": {"sessionId": session["id"], "content": "read and report config"},
            }
            response = server.handle_line(json.dumps(envelope))

            # Check that agent.decision.react_turn events were published
            decision_events = [
                e for e in events
                if e.get("type") == "agent.decision.react_turn"
            ]
            assert len(decision_events) >= 2  # One per step

            # First step: continue_with_tools
            first_event = decision_events[0]
            assert first_event["payload"]["decision"] == "continue_with_tools"

            # Second step: final_answer
            second_event = decision_events[1]
            assert second_event["payload"]["decision"] == "final_answer"
            assert "turn_id" in second_event["payload"]

            # Verify provider_turn has turn_decision stored
            task_id = response["result"]["task"]["id"]
            turns = store.list_provider_turns(task_id)
            assert len(turns) >= 2
            assert turns[0]["turn_decision"] == "continue_with_tools"
            assert turns[1]["turn_decision"] == "final_answer"
        finally:
            store.close()


# ---------------------------------------------------------------------------
# Test: Backward compatibility
# ---------------------------------------------------------------------------

class TestBackwardCompatibility:
    """_parse_provider_response still returns dict as before."""

    @pytest.fixture
    def orchestrator(self, tmp_path: Any) -> Orchestrator:
        mock_provider = MagicMock()
        orc, _, _ = _make_orchestrator(tmp_path, mock_provider)
        return orc

    def test_parse_provider_response_unchanged(self, orchestrator: Orchestrator) -> None:
        response = {
            "message": "I'll do it.",
            "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}],
        }
        parsed = orchestrator._parse_provider_response(
            response, allow_fallback=False, allow_plain_message_final=True,
        )
        assert parsed["status"] == "tool_calls"
        assert len(parsed["tool_calls"]) == 1

    def test_parse_provider_response_completed(self, orchestrator: Orchestrator) -> None:
        response = {"message": "Done.", "final": "Done."}
        parsed = orchestrator._parse_provider_response(
            response, allow_fallback=False, allow_plain_message_final=False,
        )
        assert parsed["status"] == "completed"
        assert parsed["summary"] == "Done."
