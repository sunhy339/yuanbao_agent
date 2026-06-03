"""Tests for DecisionAdvisor-wired MetaRouter routing + proposal record creation.

Covers:
  1. MetaRouter with DecisionAdvisor returns routing from LLM advisory.
  2. MetaRouter falls back to rules when advisor rejects or has no provider.
  3. Advisor payload with scenario, strategy, or skill_id conversion.
  4. Orchestrator creates proposal records for routing decisions.
  5. Existing MetaRouter(provider=None) pattern unaffected.
  6. agent.decision.routing_strategy event is published.
"""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import pytest

from local_agent_runtime.event_bus import EventBus
from local_agent_runtime.orchestrator.service import Orchestrator
from local_agent_runtime.policy.decision_advisor import (
    AdviceResult,
    DecisionAdvisor,
    get_decision_kind,
)
from local_agent_runtime.policy.guard import PolicyGuard
from local_agent_runtime.router import MetaRouter, RoutingDecision
from local_agent_runtime.router.types import ExecutionStrategy, Scenario
from local_agent_runtime.rpc.server import JsonRpcServer
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


class FakeAdvisorProvider:
    """Provider that returns a scripted LLM response for DecisionAdvisor."""

    def __init__(self, response_text: str) -> None:
        self._response = response_text
        self.prompts: list[str] = []
        self.contexts: list[dict[str, Any]] = []

    def generate(self, prompt: str, context: dict[str, Any]) -> dict[str, Any]:
        self.prompts.append(prompt)
        self.contexts.append(context)
        return {"message": self._response}


class CapturingProvider:
    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    def generate(self, prompt: str, context: dict[str, Any]) -> dict[str, Any]:
        self.calls.append({"prompt": prompt, "context": context})
        return self.response


def _make_store(tmp_path: Any) -> SQLiteStore:
    import pathlib
    store = SQLiteStore(str(pathlib.Path(str(tmp_path)) / "test.sqlite3"))
    store.update_config({"config": {"provider": FAKE_PROVIDER_CONFIG}})
    return store


def _make_orchestrator(
    tmp_path: Any,
    provider: Any,
    meta_router: MetaRouter | None = None,
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
        meta_router=meta_router,
    )
    events: list[dict[str, Any]] = []
    event_bus.subscribe(lambda event: events.append(event_bus.as_payload(event)))
    return orchestrator, store, events


# ---------------------------------------------------------------------------
# Test: MetaRouter with DecisionAdvisor — accepted routing
# ---------------------------------------------------------------------------

class TestAdvisorRoutingAccepted:
    """When DecisionAdvisor accepts a routing proposal, MetaRouter uses it."""

    def test_advisor_returns_code_edit_scenario(self) -> None:
        advisor = DecisionAdvisor(
            provider=FakeAdvisorProvider(
                '{"proposal": {"scenario": "code_edit", "strategy": "react_standard"}, '
                '"confidence": 0.92, "rationale": "file edit task"}'
            )
        )
        router = MetaRouter(provider=None, decision_advisor=advisor)
        # Use a goal that doesn't match any high-confidence keyword
        result = router.route("configure the settings module")
        assert result.scenario == Scenario.CODE_EDIT
        assert result.confidence == 0.7  # advisor-accepted default
        assert "advisor-match" in result.reasoning
        assert router.last_advice is not None
        assert router.last_advice.accepted is True
        assert router.last_advice.source == "llm"

    def test_advisor_returns_strategy(self) -> None:
        advisor = DecisionAdvisor(
            provider=FakeAdvisorProvider(
                '{"proposal": {"strategy": "react_reflect"}, '
                '"confidence": 0.85, "rationale": "needs reflection"}'
            )
        )
        router = MetaRouter(provider=None, decision_advisor=advisor)
        result = router.route("analyze the architecture thoroughly")
        assert result.strategy == ExecutionStrategy.REACT_WITH_REFLECTION
        assert result.confidence == 0.7

    def test_advisor_returns_skill_id(self) -> None:
        advisor = DecisionAdvisor(
            provider=FakeAdvisorProvider(
                '{"proposal": {"scenario": "code_edit", "strategy": "react_standard", "skill_id": "edit_skill"}, '
                '"confidence": 0.88, "rationale": "skill-based edit"}'
            )
        )
        router = MetaRouter(provider=None, decision_advisor=advisor)
        result = router.route("adjust the main configuration")
        assert result.skill_id == "edit_skill"

    def test_advisor_returns_tool_continuation_policy(self, tmp_path: Any) -> None:
        advisor = DecisionAdvisor(
            provider=FakeAdvisorProvider(
                '{"proposal": {"scenario": "code_edit", "strategy": "react_standard", '
                '"tool_continuation": {"allow_tools_after_task_results": true, '
                '"allow_more_subtasks_after_task_results": false, "max_task_tool_calls": 1, '
                '"rationale": "integrate delegated result before final synthesis"}}, '
                '"confidence": 0.86, "rationale": "parent should continue after child result"}'
            )
        )
        router = MetaRouter(provider=None, decision_advisor=advisor)

        result = router.route("Delegate one implementation slice, then integrate the returned changes")

        assert result.scenario == Scenario.CODE_EDIT
        assert result.strategy == ExecutionStrategy.REACT_STANDARD
        assert result.metadata["toolContinuation"]["allowToolsAfterTaskResults"] is True
        assert result.metadata["toolContinuation"]["source"] == "decision_advisor"
        assert result.metadata["toolContinuation"]["maxTaskToolCalls"] == 1
        orchestrator, _, _ = _make_orchestrator(tmp_path, provider=MagicMock(), meta_router=router)
        routing_dict = orchestrator._routing_dict_from_decision(result)
        assert routing_dict["toolContinuation"]["source"] == "decision_advisor"
        assert routing_dict["toolContinuation"]["allowToolsAfterTaskResults"] is True

    def test_advisor_returns_workspace_evidence_contract(self, tmp_path: Any) -> None:
        advisor = DecisionAdvisor(
            provider=FakeAdvisorProvider(
                '{"proposal": {"scenario": "doc_write", "strategy": "react_standard", '
                '"workspace_evidence_required": {"required": true, '
                '"required_tools": ["read_file", "search_files"], '
                '"reason": "answer must be grounded in repository docs"}}, '
                '"confidence": 0.86, "rationale": "project progress requires workspace evidence"}'
            )
        )
        router = MetaRouter(provider=None, decision_advisor=advisor)

        result = router.route("summarize the current project progress")

        assert result.scenario == Scenario.DOC_WRITE
        assert result.metadata["workspaceEvidenceRequired"]["required"] is True
        orchestrator, _, _ = _make_orchestrator(tmp_path, provider=MagicMock(), meta_router=router)
        routing_dict = orchestrator._routing_dict_from_decision(result)
        contract = routing_dict["profile"]["workspaceEvidenceRequired"]
        assert contract["required"] is True
        assert contract["requiredTools"] == ["read_file", "search_files"]
        assert contract["source"] == "decision_advisor"


# ---------------------------------------------------------------------------
# Test: MetaRouter with DecisionAdvisor — rejected / fallback
# ---------------------------------------------------------------------------

class TestAdvisorRoutingFallback:
    """When DecisionAdvisor rejects, MetaRouter falls back to rules."""

    def test_advisor_malformed_response_falls_back(self) -> None:
        advisor = DecisionAdvisor(
            provider=FakeAdvisorProvider("not json at all")
        )
        router = MetaRouter(provider=None, decision_advisor=advisor)
        result = router.route("something ambiguous")
        # Should fall back to rule-based routing (FREE_FORM since no keyword match)
        assert result.scenario == Scenario.FREE_FORM
        assert router.last_advice is not None
        assert router.last_advice.accepted is False

    def test_advisor_no_provider_falls_back(self) -> None:
        advisor = DecisionAdvisor(provider=None)
        router = MetaRouter(provider=None, decision_advisor=advisor)
        result = router.route("something random xyz")
        assert result.scenario == Scenario.FREE_FORM
        assert router.last_advice is not None
        assert router.last_advice.source == "rule_fallback"

    def test_high_confidence_rule_still_consults_advisor_by_default(self) -> None:
        """High-confidence rules become advisor context instead of bypassing LLM."""
        advisor = DecisionAdvisor(
            provider=FakeAdvisorProvider(
                '{"proposal": {"scenario": "debug", "strategy": "skill_based"}, '
                '"confidence": 0.99, "rationale": "debug intent confirmed"}'
            )
        )
        router = MetaRouter(provider=None, decision_advisor=advisor)
        result = router.route("debug the error")

        assert result.scenario == Scenario.DEBUG
        assert "advisor-match" in result.reasoning
        assert result.metadata["rule_candidate"]["scenario"] == "debug"
        assert router.last_advice is not None
        assert router.last_advice.accepted is True

    def test_greeting_only_rule_skips_advisor_to_keep_minimal_context(self) -> None:
        advisor = DecisionAdvisor(
            provider=FakeAdvisorProvider(
                '{"proposal": {"scenario": "code_edit", "strategy": "react_standard"}, '
                '"confidence": 0.99, "rationale": "bad overroute"}'
            )
        )
        router = MetaRouter(provider=None, decision_advisor=advisor)

        result = router.route("\u4f60\u597d")

        assert result.scenario == Scenario.SIMPLE_QUERY
        assert result.strategy == ExecutionStrategy.REACT_FAST
        assert "greeting-only" in result.reasoning
        assert router.last_advice is None

    def test_high_confidence_rule_can_skip_advisor_by_config(self) -> None:
        """A config flag preserves the old low-cost high-confidence rule path."""
        advisor = DecisionAdvisor(
            provider=FakeAdvisorProvider(
                '{"proposal": {"scenario": "debug", "strategy": "skill_based"}, '
                '"confidence": 0.99, "rationale": "should not be used"}'
            )
        )
        router = MetaRouter(provider=None, decision_advisor=advisor)

        result = router.route(
            "debug the error",
            {"config": {"advisor": {"routingStrategyUseForHighConfidence": False}}},
        )

        assert result.scenario == Scenario.DEBUG
        assert "rule-match" in result.reasoning
        assert router.last_advice is None

    def test_mixed_doc_and_frontend_signals_defer_to_advisor(self) -> None:
        provider = FakeAdvisorProvider(
            '{"proposal": {"scenario": "code_edit", "strategy": "react_standard"}, '
            '"confidence": 0.93, "rationale": "static website task with README as supporting docs"}'
        )
        advisor = DecisionAdvisor(provider=provider)
        router = MetaRouter(provider=None, decision_advisor=advisor)

        result = router.route(
            "Generate a technical blog website with index.html, styles.css, and README.md. "
            "Run a lightweight check after creating the files."
        )

        assert result.scenario == Scenario.CODE_EDIT
        assert result.skill_id is None
        assert "advisor-match" in result.reasoning
        assert result.metadata["rule_candidate"]["scenario"] in {"doc_write", "code_edit"}
        assert router.last_advice is not None
        assert router.last_advice.accepted is True
        assert provider.contexts
        prompt_context = "\n\n".join(message["content"] for message in provider.contexts[0]["messages"])
        assert "rule_candidate" in prompt_context
        assert "Do not choose multi_step_task just because" in prompt_context
        assert "explicitly requires multi-agent work" in prompt_context

    def test_advisor_overplanned_simple_artifact_generation_is_guarded(self) -> None:
        advisor = DecisionAdvisor(
            provider=FakeAdvisorProvider(
                '{"proposal": {"scenario": "multi_step_task", "strategy": "plan_execute"}, '
                '"confidence": 0.95, "rationale": "multiple files and verification"}'
            )
        )
        router = MetaRouter(provider=None, decision_advisor=advisor)

        result = router.route(
            "Generate a technical blog website with index.html, styles.css, and README.md. "
            "Run a lightweight check after creating the files."
        )

        assert result.scenario == Scenario.CODE_EDIT
        assert result.strategy == ExecutionStrategy.REACT_STANDARD
        assert result.metadata["advisor_candidate"]["scenario"] == "multi_step_task"
        assert "overplanned" in result.reasoning

    def test_explicit_planning_signal_keeps_advisor_multi_step(self) -> None:
        advisor = DecisionAdvisor(
            provider=FakeAdvisorProvider(
                '{"proposal": {"scenario": "multi_step_task", "strategy": "plan_execute"}, '
                '"confidence": 0.95, "rationale": "user requested planning and phases"}'
            )
        )
        router = MetaRouter(provider=None, decision_advisor=advisor)

        result = router.route(
            "Plan and build a technical blog website with index.html, styles.css, README.md, "
            "then break down the work into phases."
        )

        assert result.scenario == Scenario.MULTI_STEP_TASK
        assert result.strategy == ExecutionStrategy.PLAN_THEN_EXECUTE


# ---------------------------------------------------------------------------
# Test: MetaRouter(provider=None) legacy pattern unaffected
# ---------------------------------------------------------------------------

class TestLegacyProviderNonePattern:
    """MetaRouter(provider=None) without advisor still works as before."""

    def test_no_provider_no_advisor_returns_rules(self) -> None:
        router = MetaRouter(provider=None)
        result = router.route("adjust the configuration module")
        assert result.scenario == Scenario.FREE_FORM  # no keyword match
        assert router.last_advice is None

    def test_no_provider_no_advisor_free_form(self) -> None:
        router = MetaRouter(provider=None)
        result = router.route("something weird xyz")
        assert result.scenario == Scenario.FREE_FORM


# ---------------------------------------------------------------------------
# Test: DecisionAdvisor routing_strategy registry entry
# ---------------------------------------------------------------------------

class TestRoutingStrategyRegistry:
    """Verify routing_strategy decision kind is properly registered."""

    def test_routing_strategy_registered(self) -> None:
        entry = get_decision_kind("routing_strategy")
        assert entry is not None
        assert "goal" in entry.required_input_fields
        assert "strategy" in entry.allowed_proposal_schema
        assert "scenario" in entry.allowed_proposal_schema
        assert "tool_continuation" in entry.allowed_proposal_schema
        assert entry.trace_event == "agent.decision.routing_strategy"


# ---------------------------------------------------------------------------
# Test: Orchestrator creates proposal records
# ---------------------------------------------------------------------------

class TestOrchestratorProposalRecords:
    """When Orchestrator routes via DecisionAdvisor, it creates proposal records."""

    def test_routing_creates_proposal_record(self, tmp_path: Any) -> None:
        # Advisor returns code_edit routing (must include "strategy" for validation)
        advisor = DecisionAdvisor(
            provider=FakeAdvisorProvider(
                '{"proposal": {"scenario": "code_edit", "strategy": "react_standard"}, '
                '"confidence": 0.90, "rationale": "file edit task"}'
            )
        )
        router = MetaRouter(provider=None, decision_advisor=advisor)

        # Provider returns tool call then final answer
        mock_provider = MagicMock()
        mock_provider.generate.side_effect = [
            # ReAct loop: tool call
            {
                "message": json.dumps({
                    "role": "assistant",
                    "content": "I will help.",
                    "tool_calls": [{
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "read_file",
                            "arguments": json.dumps({"path": "config.txt"}),
                        },
                    }],
                }),
                "usage": {"total_tokens": 50},
            },
            # ReAct loop: final answer
            {
                "message": json.dumps({
                    "role": "assistant",
                    "content": "Done.",
                }),
                "usage": {"total_tokens": 60},
            },
        ]
        mock_provider.stream = MagicMock(side_effect=AttributeError("no stream"))

        orchestrator, store, events = _make_orchestrator(
            tmp_path, mock_provider, meta_router=router,
        )
        try:
            # Create workspace + session using store direct API
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
            session = store.create_session(
                workspace_id=workspace["id"], title="proposal-check",
            )

            # Send message (triggers routing via advisor)
            # Use a goal that does NOT match any high-confidence keyword rules,
            # so the advisor path is exercised.
            from local_agent_runtime.rpc.server import JsonRpcServer
            rpc_bus = EventBus()
            server = JsonRpcServer(orchestrator=orchestrator, store=store, event_bus=rpc_bus)
            envelope = {
                "jsonrpc": "2.0",
                "id": "req_1",
                "method": "message.send",
                "params": {"sessionId": session["id"], "content": "adjust the main configuration"},
            }
            response = server.handle_line(json.dumps(envelope))
            task_id = response["result"]["task"]["id"]

            # Check proposal record was created
            proposals = store.list_proposals({
                "sessionId": session["id"],
                "taskId": task_id,
            })
            assert len(proposals.get("proposals", [])) >= 1
            routing_proposal = next(
                (p for p in proposals["proposals"] if p["kind"] == "routing_strategy"),
                None,
            )
            assert routing_proposal is not None
            assert routing_proposal["status"] == "accepted"
            assert routing_proposal["proposal"]["scenario"] == "code_edit"

            # Check agent.decision event was published
            decision_events = [
                e for e in events
                if e.get("type") == "agent.decision.routing_strategy"
            ]
            assert len(decision_events) >= 1
            assert decision_events[0]["payload"]["outcome"] == "accepted"
            assert decision_events[0]["payload"]["scenario"] == "code_edit"
        finally:
            store.close()

    def test_greeting_send_with_advisor_still_uses_minimal_context(self, tmp_path: Any) -> None:
        advisor_provider = FakeAdvisorProvider(
            '{"proposal": {"scenario": "code_edit", "strategy": "react_standard"}, '
            '"confidence": 0.99, "rationale": "bad overroute"}'
        )
        advisor = DecisionAdvisor(
            provider=advisor_provider
        )
        router = MetaRouter(provider=None, decision_advisor=advisor)
        provider = CapturingProvider({"final": "hello"})
        orchestrator, store, _events = _make_orchestrator(
            tmp_path, provider=provider, meta_router=router,
        )
        try:
            workspace_root = tmp_path / "workspace"
            workspace_root.mkdir(exist_ok=True)
            (workspace_root / "large_notes.md").write_text(
                "# Notes\n" + ("workspace detail\n" * 5000),
                encoding="utf-8",
            )
            workspace = store.upsert_workspace(str(workspace_root))
            session = store.create_session(workspace_id=workspace["id"], title="advisor greeting")
            rpc_bus = EventBus()
            server = JsonRpcServer(orchestrator=orchestrator, store=store, event_bus=rpc_bus)
            envelope = {
                "jsonrpc": "2.0",
                "id": "req_greeting",
                "method": "message.send",
                "params": {"sessionId": session["id"], "content": "\u4f60\u597d"},
            }

            response = server.handle_line(json.dumps(envelope))

            assert "result" in response, response
            task = response["result"]["task"]
            assert task["routing"]["contextMode"] == "minimal"
            assert provider.calls
            context = provider.calls[0]["context"]
            assert context["minimal"] is True
            assert context["openai_tools"] == []
            assert "large_notes.md" not in "\n".join(message["content"] for message in context["messages"])
            turns = store.list_provider_turns(task["id"])
            assert turns[0]["request_tool_count"] == 0
            assert turns[0]["request_token_estimate"] < 1000
            assert advisor_provider.contexts == []
        finally:
            store.close()

    def test_rejected_routing_proposal_records_advisor_payload(self, tmp_path: Any) -> None:
        advisor = DecisionAdvisor(
            provider=FakeAdvisorProvider(
                '{"proposal": {"scenario": "code_edit", "strategy": "teleport"}, '
                '"confidence": 0.72, "rationale": "bad strategy"}'
            )
        )
        router = MetaRouter(provider=None, decision_advisor=advisor)
        routing = router.route("adjust the main configuration")
        orchestrator, store, _events = _make_orchestrator(
            tmp_path, provider=MagicMock(), meta_router=router,
        )
        try:
            workspace = store.upsert_workspace(str(tmp_path))
            session = store.create_session(workspace_id=workspace["id"], title="routing rejected")
            task = store.create_task(
                session_id=session["id"],
                task_type="chat",
                goal="adjust the main configuration",
                plan=[],
            )
            routing_dict = orchestrator._routing_dict_from_decision(routing)

            orchestrator._record_routing_proposal(
                session_id=session["id"],
                task_id=task["id"],
                goal="adjust the main configuration",
                routing=routing,
                routing_dict=routing_dict,
            )

            proposals = store.list_proposals({
                "taskId": task["id"],
                "kind": "routing_strategy",
            })["proposals"]
            assert len(proposals) == 1
            proposal = proposals[0]
            assert proposal["status"] == "rejected"
            assert proposal["proposal"]["strategy"] == "teleport"
            assert proposal["proposal"]["scenario"] == "code_edit"
            assert proposal["validationReasons"] == ["Invalid routing strategy: 'teleport'"]
        finally:
            store.close()
