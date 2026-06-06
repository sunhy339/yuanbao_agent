"""Tests for opt-in DecisionAdvisor routing and proposal records.

Default message routing is model-first ReAct. DecisionAdvisor remains available
as an explicit routing-advisor mode, but it must not silently rewrite ordinary
turns into fixed scenarios, skills, workspace evidence gates, or planners.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

from local_agent_runtime.event_bus import EventBus
from local_agent_runtime.orchestrator.service import Orchestrator
from local_agent_runtime.policy.decision_advisor import DecisionAdvisor, get_decision_kind
from local_agent_runtime.policy.guard import PolicyGuard
from local_agent_runtime.router import MetaRouter, RoutingDecision
from local_agent_runtime.router.types import ExecutionStrategy, Scenario
from local_agent_runtime.rpc.server import JsonRpcServer
from local_agent_runtime.services import CollaborationService, SubagentService
from local_agent_runtime.store.sqlite_store import SQLiteStore
from local_agent_runtime.tools import build_builtin_tools
from local_agent_runtime.tools.registry import ToolRegistry


FAKE_PROVIDER_CONFIG = {
    "mode": "mock",
    "apiKey": "sk-test",
    "baseUrl": "https://fake.local/v1",
    "model": "test-model",
}

ADVISOR_ON_CONTEXT = {
    "config": {
        "advisor": {
            "routingStrategyUseForHighConfidence": True,
        },
    },
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


def _make_store(tmp_path: Any, *, advisor_enabled: bool = False) -> SQLiteStore:
    import pathlib

    store = SQLiteStore(str(pathlib.Path(str(tmp_path)) / "test.sqlite3"))
    config: dict[str, Any] = {"provider": FAKE_PROVIDER_CONFIG}
    if advisor_enabled:
        config["advisor"] = {"routingStrategyUseForHighConfidence": True}
    store.update_config({"config": config})
    return store


def _make_orchestrator(
    tmp_path: Any,
    provider: Any,
    meta_router: MetaRouter | None = None,
    *,
    advisor_enabled: bool = False,
) -> tuple[Orchestrator, SQLiteStore, list[dict[str, Any]]]:
    store = _make_store(tmp_path, advisor_enabled=advisor_enabled)
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


def _assert_model_first(result: RoutingDecision) -> None:
    assert result.scenario == Scenario.FREE_FORM
    assert result.strategy == ExecutionStrategy.REACT_STANDARD
    assert result.skill_id is None
    assert result.enable_planning is False
    assert result.reasoning.startswith("model-first-default:")


class TestDefaultAdvisorBoundary:
    """Advisor exists, but normal routing still stays model-first."""

    def test_advisor_is_not_called_by_default(self) -> None:
        provider = FakeAdvisorProvider(
            '{"proposal": {"scenario": "code_edit", "strategy": "react_standard"}, '
            '"confidence": 0.92, "rationale": "file edit task"}'
        )
        advisor = DecisionAdvisor(provider=provider)
        router = MetaRouter(provider=None, decision_advisor=advisor)

        result = router.route("configure the settings module")

        _assert_model_first(result)
        assert router.last_advice is None
        assert provider.contexts == []
        assert result.metadata["intentHints"]["ruleCandidate"]["scenario"] == Scenario.FREE_FORM.value

    def test_high_confidence_rule_is_hint_not_skill_route_by_default(self) -> None:
        provider = FakeAdvisorProvider(
            '{"proposal": {"scenario": "debug", "strategy": "skill_based"}, '
            '"confidence": 0.99, "rationale": "debug intent confirmed"}'
        )
        advisor = DecisionAdvisor(provider=provider)
        router = MetaRouter(provider=None, decision_advisor=advisor)

        result = router.route("debug the error")

        _assert_model_first(result)
        assert provider.contexts == []
        assert router.last_advice is None
        candidate = result.metadata["intentHints"]["ruleCandidate"]
        assert candidate["scenario"] == Scenario.DEBUG.value
        assert candidate["skill_id"] == "debugger"

    def test_read_only_doc_goal_is_hint_not_doc_write_by_default(self) -> None:
        provider = FakeAdvisorProvider(
            '{"proposal": {"scenario": "doc_write", "strategy": "skill_based"}, '
            '"confidence": 0.96, "rationale": "README mentioned"}'
        )
        advisor = DecisionAdvisor(provider=provider)
        router = MetaRouter(provider=None, decision_advisor=advisor)

        result = router.route("Read README.md and summarize it in one sentence")

        _assert_model_first(result)
        assert provider.contexts == []
        assert router.last_advice is None
        candidate = result.metadata["intentHints"]["ruleCandidate"]
        assert candidate["scenario"] == Scenario.CODE_SEARCH.value

    def test_greeting_still_uses_minimal_fast_path(self) -> None:
        provider = FakeAdvisorProvider(
            '{"proposal": {"scenario": "code_edit", "strategy": "react_standard"}, '
            '"confidence": 0.99, "rationale": "bad overroute"}'
        )
        advisor = DecisionAdvisor(provider=provider)
        router = MetaRouter(provider=None, decision_advisor=advisor)

        result = router.route("\u4f60\u597d")

        assert result.scenario == Scenario.SIMPLE_QUERY
        assert result.strategy == ExecutionStrategy.REACT_FAST
        assert "greeting-only" in result.reasoning
        assert provider.contexts == []
        assert router.last_advice is None


class TestOptInAdvisorRouting:
    """When explicitly enabled, DecisionAdvisor can still advise routing."""

    def test_advisor_returns_code_edit_scenario_when_enabled(self) -> None:
        advisor = DecisionAdvisor(
            provider=FakeAdvisorProvider(
                '{"proposal": {"scenario": "code_edit", "strategy": "react_standard"}, '
                '"confidence": 0.92, "rationale": "file edit task"}'
            )
        )
        router = MetaRouter(provider=None, decision_advisor=advisor)

        result = router.route("configure the settings module", ADVISOR_ON_CONTEXT)

        assert result.scenario == Scenario.CODE_EDIT
        assert result.confidence == 0.7
        assert "advisor-match" in result.reasoning
        assert router.last_advice is not None
        assert router.last_advice.accepted is True
        assert router.last_advice.source == "llm"

    def test_advisor_returns_strategy_when_enabled(self) -> None:
        advisor = DecisionAdvisor(
            provider=FakeAdvisorProvider(
                '{"proposal": {"strategy": "react_reflect"}, '
                '"confidence": 0.85, "rationale": "needs reflection"}'
            )
        )
        router = MetaRouter(provider=None, decision_advisor=advisor)

        result = router.route("analyze the architecture thoroughly", ADVISOR_ON_CONTEXT)

        assert result.strategy == ExecutionStrategy.REACT_WITH_REFLECTION
        assert result.confidence == 0.7

    def test_advisor_returns_skill_id_when_enabled(self) -> None:
        advisor = DecisionAdvisor(
            provider=FakeAdvisorProvider(
                '{"proposal": {"scenario": "code_edit", "strategy": "react_standard", "skill_id": "edit_skill"}, '
                '"confidence": 0.88, "rationale": "skill-based edit"}'
            )
        )
        router = MetaRouter(provider=None, decision_advisor=advisor)

        result = router.route("adjust the main configuration", ADVISOR_ON_CONTEXT)

        assert result.skill_id == "edit_skill"

    def test_advisor_returns_tool_continuation_policy_when_enabled(self, tmp_path: Any) -> None:
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

        result = router.route(
            "Delegate one implementation slice, then integrate the returned changes",
            ADVISOR_ON_CONTEXT,
        )

        assert result.scenario == Scenario.CODE_EDIT
        assert result.strategy == ExecutionStrategy.REACT_STANDARD
        assert result.metadata["toolContinuation"]["allowToolsAfterTaskResults"] is True
        assert result.metadata["toolContinuation"]["source"] == "decision_advisor"
        assert result.metadata["toolContinuation"]["maxTaskToolCalls"] == 1
        orchestrator, _, _ = _make_orchestrator(tmp_path, provider=MagicMock(), meta_router=router)
        routing_dict = orchestrator._routing_dict_from_decision(result)
        assert routing_dict["toolContinuation"]["source"] == "decision_advisor"
        assert routing_dict["toolContinuation"]["allowToolsAfterTaskResults"] is True

    def test_advisor_workspace_evidence_payload_is_ignored_for_model_first_routing(self, tmp_path: Any) -> None:
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

        result = router.route("summarize the current project progress", ADVISOR_ON_CONTEXT)

        assert result.scenario == Scenario.DOC_WRITE
        assert "workspaceEvidenceRequired" not in result.metadata
        assert "workspace_evidence_required" not in result.metadata
        orchestrator, _, _ = _make_orchestrator(tmp_path, provider=MagicMock(), meta_router=router)
        routing_dict = orchestrator._routing_dict_from_decision(result)
        assert "workspaceEvidenceRequired" not in routing_dict.get("profile", {})
        assert "workspace_evidence_required" not in routing_dict.get("profile", {})

    def test_malformed_advisor_response_falls_back_to_model_first_when_enabled(self) -> None:
        advisor = DecisionAdvisor(provider=FakeAdvisorProvider("not json at all"))
        router = MetaRouter(provider=None, decision_advisor=advisor)

        result = router.route("something ambiguous", ADVISOR_ON_CONTEXT)

        _assert_model_first(result)
        assert router.last_advice is not None
        assert router.last_advice.accepted is False

    def test_advisor_without_provider_falls_back_to_model_first_when_enabled(self) -> None:
        advisor = DecisionAdvisor(provider=None)
        router = MetaRouter(provider=None, decision_advisor=advisor)

        result = router.route("something random xyz", ADVISOR_ON_CONTEXT)

        _assert_model_first(result)
        assert router.last_advice is not None
        assert router.last_advice.source == "rule_fallback"

    def test_overplanned_simple_artifact_generation_is_guarded_when_enabled(self) -> None:
        advisor = DecisionAdvisor(
            provider=FakeAdvisorProvider(
                '{"proposal": {"scenario": "multi_step_task", "strategy": "plan_execute"}, '
                '"confidence": 0.95, "rationale": "multiple files and verification"}'
            )
        )
        router = MetaRouter(provider=None, decision_advisor=advisor)

        result = router.route(
            "Generate a technical blog website with index.html, styles.css, and README.md. "
            "Run a lightweight check after creating the files.",
            ADVISOR_ON_CONTEXT,
        )

        assert result.scenario == Scenario.CODE_EDIT
        assert result.strategy == ExecutionStrategy.REACT_STANDARD
        assert result.metadata["advisor_candidate"]["scenario"] == "multi_step_task"
        assert "overplanned" in result.reasoning

    def test_explicit_planning_signal_keeps_advisor_multi_step_as_model_tools_when_enabled(self) -> None:
        advisor = DecisionAdvisor(
            provider=FakeAdvisorProvider(
                '{"proposal": {"scenario": "multi_step_task", "strategy": "plan_execute"}, '
                '"confidence": 0.95, "rationale": "user requested planning and phases"}'
            )
        )
        router = MetaRouter(provider=None, decision_advisor=advisor)

        result = router.route(
            "Plan and build a technical blog website with index.html, styles.css, README.md, "
            "then break down the work into phases.",
            ADVISOR_ON_CONTEXT,
        )

        assert result.scenario == Scenario.MULTI_STEP_TASK
        assert result.strategy == ExecutionStrategy.PLAN_THEN_EXECUTE
        assert result.enable_planning is False
        assert result.metadata["orchestrationMode"] == "model_tools"
        assert result.metadata["runtime"] == "react_tool_loop"

    def test_legacy_plan_execute_requires_explicit_advisor_flag(self) -> None:
        advisor = DecisionAdvisor(
            provider=FakeAdvisorProvider(
                '{"proposal": {"scenario": "multi_step_task", "strategy": "plan_execute", '
                '"legacyPlanExecution": true}, '
                '"confidence": 0.95, "rationale": "legacy planner explicitly requested"}'
            )
        )
        router = MetaRouter(provider=None, decision_advisor=advisor)

        result = router.route(
            "Plan and build a technical blog website with index.html, styles.css, README.md, "
            "then break down the work into phases.",
            ADVISOR_ON_CONTEXT,
        )

        assert result.scenario == Scenario.MULTI_STEP_TASK
        assert result.strategy == ExecutionStrategy.PLAN_THEN_EXECUTE
        assert result.enable_planning is True
        assert result.metadata["legacyPlanExecution"] is True


class TestRoutingProfiles:
    """Workspace evidence stays explicit; plan defaults keep model-directed continuation."""

    def test_plan_strategy_defaults_to_non_recursive_continuation_after_child_results(self, tmp_path: Any) -> None:
        result = RoutingDecision(
            scenario=Scenario.SWARM_TASK,
            strategy=ExecutionStrategy.PLAN_SWARM,
            confidence=0.9,
            max_steps=100,
            enable_reflection=True,
            enable_planning=True,
            reasoning="explicit multi-agent request",
            metadata={},
        )
        orchestrator, _, _ = _make_orchestrator(tmp_path, provider=MagicMock())

        routing_dict = orchestrator._routing_dict_from_decision(result)

        assert routing_dict["toolContinuation"] == {
            "allowToolsAfterTaskResults": True,
            "allowMoreSubtasksAfterTaskResults": False,
            "source": "strategy_default_post_task_continuation",
        }

    def test_workspace_grounded_query_does_not_get_implicit_evidence_contract(self, tmp_path: Any) -> None:
        router = MetaRouter(provider=None)

        result = router.route("current project task list")

        orchestrator, _, _ = _make_orchestrator(tmp_path, provider=MagicMock(), meta_router=router)
        routing_dict = orchestrator._routing_dict_from_decision(result, context={"goal": "current project task list"})
        profile = routing_dict.get("profile", {})
        assert "workspaceEvidenceRequired" not in profile


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


class TestOrchestratorProposalRecords:
    """Proposal records are created only when routing advisory is enabled."""

    def test_routing_creates_proposal_record_when_enabled(self, tmp_path: Any) -> None:
        advisor = DecisionAdvisor(
            provider=FakeAdvisorProvider(
                '{"proposal": {"scenario": "code_edit", "strategy": "react_standard"}, '
                '"confidence": 0.90, "rationale": "file edit task"}'
            )
        )
        router = MetaRouter(provider=None, decision_advisor=advisor)

        mock_provider = MagicMock()
        mock_provider.generate.side_effect = [
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
            {
                "final": "Done.",
                "usage": {"total_tokens": 60},
            },
        ]
        mock_provider.stream = MagicMock(side_effect=AttributeError("no stream"))

        orchestrator, store, events = _make_orchestrator(
            tmp_path,
            mock_provider,
            meta_router=router,
            advisor_enabled=True,
        )
        try:
            import pathlib
            import subprocess

            ws = pathlib.Path(str(tmp_path)) / "workspace"
            ws.mkdir()
            subprocess.run(["git", "init", str(ws)], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(ws), "config", "user.email", "t@t.com"], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(ws), "config", "user.name", "T"], check=True, capture_output=True)
            (ws / "config.txt").write_text("hello", encoding="utf-8")

            workspace = store.upsert_workspace(str(ws))
            session = store.create_session(workspace_id=workspace["id"], title="proposal-check")

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

            proposals = store.list_proposals({"sessionId": session["id"], "taskId": task_id})
            routing_proposal = next(
                (p for p in proposals.get("proposals", []) if p["kind"] == "routing_strategy"),
                None,
            )
            assert routing_proposal is not None
            assert routing_proposal["status"] == "accepted"
            assert routing_proposal["proposal"]["scenario"] == "code_edit"

            decision_events = [e for e in events if e.get("type") == "agent.decision.routing_strategy"]
            assert decision_events
            assert decision_events[0]["payload"]["outcome"] == "accepted"
            assert decision_events[0]["payload"]["scenario"] == "code_edit"
        finally:
            store.close()

    def test_no_routing_proposal_record_by_default(self, tmp_path: Any) -> None:
        advisor = DecisionAdvisor(
            provider=FakeAdvisorProvider(
                '{"proposal": {"scenario": "code_edit", "strategy": "react_standard"}, '
                '"confidence": 0.90, "rationale": "file edit task"}'
            )
        )
        router = MetaRouter(provider=None, decision_advisor=advisor)
        provider = CapturingProvider({"final": "Done."})
        orchestrator, store, events = _make_orchestrator(tmp_path, provider=provider, meta_router=router)
        try:
            workspace_root = tmp_path / "workspace"
            workspace_root.mkdir(exist_ok=True)
            workspace = store.upsert_workspace(str(workspace_root))
            session = store.create_session(workspace_id=workspace["id"], title="proposal-default")
            server = JsonRpcServer(orchestrator=orchestrator, store=store, event_bus=EventBus())

            response = server.handle_line(json.dumps({
                "jsonrpc": "2.0",
                "id": "req_default",
                "method": "message.send",
                "params": {"sessionId": session["id"], "content": "adjust the main configuration"},
            }))
            task_id = response["result"]["task"]["id"]

            assert provider.calls
            proposals = store.list_proposals({"sessionId": session["id"], "taskId": task_id})
            assert proposals["proposals"] == []
            assert not [e for e in events if e.get("type") == "agent.decision.routing_strategy"]
        finally:
            store.close()

    def test_greeting_send_with_advisor_still_uses_minimal_context(self, tmp_path: Any) -> None:
        advisor_provider = FakeAdvisorProvider(
            '{"proposal": {"scenario": "code_edit", "strategy": "react_standard"}, '
            '"confidence": 0.99, "rationale": "bad overroute"}'
        )
        advisor = DecisionAdvisor(provider=advisor_provider)
        router = MetaRouter(provider=None, decision_advisor=advisor)
        provider = CapturingProvider({"final": "hello"})
        orchestrator, store, _events = _make_orchestrator(tmp_path, provider=provider, meta_router=router)
        try:
            workspace_root = tmp_path / "workspace"
            workspace_root.mkdir(exist_ok=True)
            (workspace_root / "large_notes.md").write_text(
                "# Notes\n" + ("workspace detail\n" * 5000),
                encoding="utf-8",
            )
            workspace = store.upsert_workspace(str(workspace_root))
            session = store.create_session(workspace_id=workspace["id"], title="advisor greeting")
            server = JsonRpcServer(orchestrator=orchestrator, store=store, event_bus=EventBus())

            response = server.handle_line(json.dumps({
                "jsonrpc": "2.0",
                "id": "req_greeting",
                "method": "message.send",
                "params": {"sessionId": session["id"], "content": "\u4f60\u597d"},
            }))

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

    def test_rejected_routing_proposal_records_advisor_payload_when_enabled(self, tmp_path: Any) -> None:
        advisor = DecisionAdvisor(
            provider=FakeAdvisorProvider(
                '{"proposal": {"scenario": "code_edit", "strategy": "teleport"}, '
                '"confidence": 0.72, "rationale": "bad strategy"}'
            )
        )
        router = MetaRouter(provider=None, decision_advisor=advisor)
        routing = router.route("adjust the main configuration", ADVISOR_ON_CONTEXT)
        orchestrator, store, _events = _make_orchestrator(
            tmp_path,
            provider=MagicMock(),
            meta_router=router,
            advisor_enabled=True,
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

            proposals = store.list_proposals({"taskId": task["id"], "kind": "routing_strategy"})["proposals"]
            assert len(proposals) == 1
            proposal = proposals[0]
            assert proposal["status"] == "rejected"
            assert proposal["proposal"]["strategy"] == "teleport"
            assert proposal["proposal"]["scenario"] == "code_edit"
            assert proposal["validationReasons"] == ["Invalid routing strategy: 'teleport'"]
        finally:
            store.close()
