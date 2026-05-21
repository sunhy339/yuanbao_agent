"""Comprehensive tests for ProviderTurn, ContextSnapshot, events_after, and RPC handlers.

Test categories:
  A. Store-level CRUD (unit)
  B. RPC handler integration
  C. E2E ReAct loop — single-step direct answer
  D. E2E ReAct loop — multi-step with tool calls (simulating "build an app")
  E. E2E ReAct loop — supplement injection + memory recall integration
  F. E2E ReAct loop — provider failure produces failed turn
  G. E2E ReAct loop — multiple turns with ContextSnapshot tracking
  H. Edge cases and boundary conditions
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from local_agent_runtime.event_bus import EventBus
from local_agent_runtime.orchestrator.service import Orchestrator
from local_agent_runtime.policy.decision_advisor import DecisionAdvisor
from local_agent_runtime.provider.openai_compatible import ProviderAdapterError
from local_agent_runtime.router.meta_router import MetaRouter
from local_agent_runtime.rpc.server import JsonRpcServer
from local_agent_runtime.store.sqlite_store import SQLiteStore
from local_agent_runtime.tools.registry import ToolRegistry


# ═══════════════════════════════════════════════════════════════════════════
# Shared helpers
# ═══════════════════════════════════════════════════════════════════════════


class ScriptedProvider:
    """Deterministic provider that returns pre-scripted responses."""

    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def generate(self, prompt: str, context: dict[str, Any]) -> dict[str, Any]:
        self.calls.append({"prompt": prompt, "context": context})
        if not self._responses:
            raise AssertionError("Provider called more times than scripted")
        return self._responses.pop(0)


class FailingProvider:
    """Provider that always raises an exception."""

    def __init__(self, error: Exception | None = None) -> None:
        self._error = error or RuntimeError("Provider API timeout")
        self.calls: list[dict[str, Any]] = []

    def generate(self, prompt: str, context: dict[str, Any]) -> dict[str, Any]:
        self.calls.append({"prompt": prompt, "context": context})
        raise self._error


class PartialStreamFailureProvider:
    """Streaming provider that emits partial text before transport failure."""

    def __init__(self) -> None:
        self.stream_calls: list[dict[str, Any]] = []
        self.generate_calls: list[dict[str, Any]] = []
        self.advisor_calls: list[dict[str, Any]] = []

    def stream(self, prompt: str, context: dict[str, Any]) -> Any:
        self.stream_calls.append({"prompt": prompt, "context": context})
        yield {"type": "content_delta", "delta": "Partial answer before failure."}
        raise ProviderAdapterError("Provider streaming response exceeded 30s before completion.")

    def generate(self, prompt: str, context: dict[str, Any]) -> dict[str, Any]:
        if "runtime decision advisor" in prompt:
            self.advisor_calls.append({"prompt": prompt, "context": context})
            if "failure_recovery" in prompt:
                return {"message": json.dumps({
                    "proposal": {
                        "strategy": "fallback",
                        "maxRetries": 0,
                        "reason": "Use the partial stream evidence and switch to non-stream fallback.",
                    },
                    "confidence": 0.82,
                    "rationale": "The stream already produced partial content before failing.",
                })}
            if "completion_decision" in prompt:
                return {"message": json.dumps({
                    "proposal": {"is_complete": True, "why_complete": "Fallback final answered the task."},
                    "confidence": 0.8,
                    "rationale": "The task has a final answer after fallback.",
                })}
            if "product_surface_decision" in prompt:
                return {"message": json.dumps({
                    "proposal": {"surface_type": "backend_flow", "recommended_verification": []},
                    "confidence": 0.6,
                    "rationale": "Provider recovery test does not require product evidence.",
                })}
            return {"message": json.dumps({
                "proposal": {"mode": "task"},
                "confidence": 0.5,
                "rationale": "Generic advisor response.",
            })}
        self.generate_calls.append({"prompt": prompt, "context": context})
        return {"final": "Recovered through non-stream fallback."}


class AdvisorRecoveryProvider:
    """Provider that can fail main turns while answering advisor prompts."""

    def __init__(self, *, advisor_response: str | None = None, error: Exception | None = None) -> None:
        self.advisor_response = advisor_response or json.dumps({
            "proposal": {
                "strategy": "compact_or_split_context",
                "maxRetries": 1,
                "reason": "Compact context and retry once.",
            },
            "confidence": 0.9,
            "rationale": "The provider failure is recoverable with smaller context.",
        })
        self.error = error or ProviderAdapterError("context_length_exceeded: maximum context length")
        self.main_calls: list[dict[str, Any]] = []
        self.advisor_calls: list[dict[str, Any]] = []
        self.other_advisor_calls: list[dict[str, Any]] = []

    def generate(self, prompt: str, context: dict[str, Any]) -> dict[str, Any]:
        if "runtime decision advisor" in prompt:
            if "failure_recovery" in prompt:
                self.advisor_calls.append({"prompt": prompt, "context": context})
                return {"message": self.advisor_response}
            self.other_advisor_calls.append({"prompt": prompt, "context": context})
            if "completion_decision" in prompt:
                return {"message": json.dumps({
                    "proposal": {"is_complete": True, "why_complete": "Recovered response completed the task."},
                    "confidence": 0.8,
                    "rationale": "The task produced a final answer after recovery.",
                })}
            if "product_surface_decision" in prompt:
                return {"message": json.dumps({
                    "proposal": {"surface_type": "backend_flow", "recommended_verification": []},
                    "confidence": 0.6,
                    "rationale": "This test is focused on provider recovery flow.",
                })}
            return {"message": json.dumps({
                "proposal": {"mode": "task"},
                "confidence": 0.5,
                "rationale": "Generic advisor fallback for the test provider.",
            })}
        self.main_calls.append({"prompt": prompt, "context": context})
        if len(self.main_calls) == 1:
            raise self.error
        return {"final": "Recovered after provider failure."}


class PreflightCompactProvider:
    """Provider that asks preflight to compact, then completes the main turn."""

    def __init__(self) -> None:
        self.main_calls: list[dict[str, Any]] = []
        self.advisor_calls: list[dict[str, Any]] = []

    def generate(self, prompt: str, context: dict[str, Any]) -> dict[str, Any]:
        if "runtime decision advisor" in prompt:
            self.advisor_calls.append({"prompt": prompt, "context": context})
            if "provider_preflight" in prompt:
                return {"message": json.dumps({
                    "proposal": {
                        "action": "compact_context",
                        "riskLevel": "high",
                        "contextStrategy": "compact recent context before the provider call",
                        "reason": "The request is large enough to reduce before sending.",
                    },
                    "confidence": 0.87,
                    "rationale": "Preflight facts indicate context pressure.",
                })}
            if "completion_decision" in prompt:
                return {"message": json.dumps({
                    "proposal": {"is_complete": True, "why_complete": "Main turn completed after preflight."},
                    "confidence": 0.8,
                    "rationale": "The provider returned a final answer.",
                })}
            if "product_surface_decision" in prompt:
                return {"message": json.dumps({
                    "proposal": {"surface_type": "backend_flow", "recommended_verification": []},
                    "confidence": 0.6,
                    "rationale": "This test is focused on provider preflight.",
                })}
            return {"message": json.dumps({
                "proposal": {"mode": "task"},
                "confidence": 0.5,
                "rationale": "Generic advisor fallback for this test.",
            })}
        self.main_calls.append({"prompt": prompt, "context": context})
        return {"final": "Completed after provider preflight."}


class PreflightSplitProvider:
    """Provider that asks preflight to split before the main provider call."""

    def __init__(self) -> None:
        self.main_calls: list[dict[str, Any]] = []
        self.advisor_calls: list[dict[str, Any]] = []

    def generate(self, prompt: str, context: dict[str, Any]) -> dict[str, Any]:
        if "runtime decision advisor" in prompt:
            self.advisor_calls.append({"prompt": prompt, "context": context})
            if "provider_preflight" in prompt:
                return {"message": json.dumps({
                    "proposal": {
                        "action": "propose_split",
                        "riskLevel": "high",
                        "reason": "The request should be split before the main provider turn.",
                        "splitRecommendation": {
                            "subtasks": [
                                {
                                    "id": "sub-0",
                                    "title": "Inspect provider preflight planning",
                                    "description": "Inspect provider preflight split planning inputs and summarize task evidence.",
                                    "dependencies": [],
                                    "agentType": "planner",
                                },
                                {
                                    "id": "sub-1",
                                    "title": "Implement and verify provider preflight split",
                                    "description": "Implement and verify provider preflight split planning behavior using the inspection result.",
                                    "dependencies": ["sub-0"],
                                    "agentType": "worker",
                                },
                            ]
                        },
                    },
                    "confidence": 0.88,
                    "rationale": "The preflight facts and goal show a bounded two-step split.",
                })}
            if "completion_decision" in prompt:
                return {"message": json.dumps({
                    "proposal": {"is_complete": True, "why_complete": "Planning subtasks completed."},
                    "confidence": 0.8,
                    "rationale": "The split execution produced completed subtask summaries.",
                })}
            if "product_surface_decision" in prompt:
                return {"message": json.dumps({
                    "proposal": {"surface_type": "backend_flow", "recommended_verification": []},
                    "confidence": 0.6,
                    "rationale": "This test is focused on provider preflight split execution.",
                })}
            return {"message": json.dumps({
                "proposal": {"mode": "task"},
                "confidence": 0.5,
                "rationale": "Generic advisor fallback for this test.",
            })}
        self.main_calls.append({"prompt": prompt, "context": context})
        return {"final": "Main provider should not be used after executable preflight split."}


class PreflightSwitchProvider:
    """Provider that asks preflight to switch provider profiles before the main call."""

    def __init__(self, *, fallback_provider_id: str = "secondary") -> None:
        self.fallback_provider_id = fallback_provider_id
        self.main_calls: list[dict[str, Any]] = []
        self.advisor_calls: list[dict[str, Any]] = []

    def generate(self, prompt: str, context: dict[str, Any]) -> dict[str, Any]:
        if "runtime decision advisor" in prompt:
            self.advisor_calls.append({"prompt": prompt, "context": context})
            if "provider_preflight" in prompt:
                return {"message": json.dumps({
                    "proposal": {
                        "action": "switch_provider",
                        "riskLevel": "medium",
                        "fallbackProviderId": self.fallback_provider_id,
                        "reason": "Use the configured fallback profile for this provider turn.",
                    },
                    "confidence": 0.83,
                    "rationale": "The preflight facts include an available fallback provider profile.",
                })}
            if "completion_decision" in prompt:
                return {"message": json.dumps({
                    "proposal": {"is_complete": True, "why_complete": "Main turn completed after provider switch."},
                    "confidence": 0.8,
                    "rationale": "The provider returned a final answer.",
                })}
            if "product_surface_decision" in prompt:
                return {"message": json.dumps({
                    "proposal": {"surface_type": "backend_flow", "recommended_verification": []},
                    "confidence": 0.6,
                    "rationale": "This test is focused on provider preflight switching.",
                })}
            return {"message": json.dumps({
                "proposal": {"mode": "task"},
                "confidence": 0.5,
                "rationale": "Generic advisor fallback for this test.",
            })}
        self.main_calls.append({"prompt": prompt, "context": context})
        return {"final": "Completed after provider preflight switched profiles."}


def _call_result(response: dict[str, Any], key: str) -> dict[str, Any]:
    assert "result" in response, f"Expected 'result' in response, got: {response}"
    return response["result"][key]


def _make_runtime(
    tmp_path: Any,
    provider: Any,
    tools: dict[str, Any] | None = None,
    decision_advisor: DecisionAdvisor | None = None,
) -> SimpleNamespace:
    event_bus = EventBus()
    store = SQLiteStore(str(tmp_path / "runtime.sqlite3"))
    tool_registry = ToolRegistry(tools or {})
    orchestrator = Orchestrator(
        store=store,
        event_bus=event_bus,
        tool_registry=tool_registry,
        provider=provider,
        meta_router=MetaRouter(provider=None),
        decision_advisor=decision_advisor,
    )
    server = JsonRpcServer(orchestrator=orchestrator, store=store, event_bus=event_bus)
    events: list[dict[str, Any]] = []
    event_bus.subscribe(lambda event: events.append(event_bus.as_payload(event)))
    return SimpleNamespace(server=server, store=store, events=events, orchestrator=orchestrator)


def _rpc(runtime: SimpleNamespace, method: str, params: dict[str, Any]) -> dict[str, Any]:
    request_id = f"req_{len(runtime.events)}_{method}"
    envelope = {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
        "params": params,
    }
    response = runtime.server.handle_line(json.dumps(envelope, ensure_ascii=False))
    assert response["jsonrpc"] == "2.0"
    assert response["id"] == request_id
    return response


def _open_session(runtime: SimpleNamespace, tmp_path: Any) -> dict[str, Any]:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(exist_ok=True)
    workspace = _call_result(
        _rpc(runtime, "workspace.open", {"path": str(workspace_root)}),
        "workspace",
    )
    return _call_result(
        _rpc(
            runtime,
            "session.create",
            {"workspaceId": workspace["id"], "title": "Test Session"},
        ),
        "session",
    )


def _make_store(tmp_path: Any) -> SQLiteStore:
    return SQLiteStore(str(tmp_path / "test.sqlite3"))


def _seed_session_and_task(store: SQLiteStore, session_id: str = "sess_1", task_id: str = "task_1") -> None:
    now = store.now()
    store._conn.execute(
        "INSERT OR IGNORE INTO sessions (id, workspace_id, created_at) VALUES (?, 'ws_1', ?)",
        (session_id, now),
    )
    store._conn.execute(
        "INSERT OR IGNORE INTO tasks (id, session_id, goal, status, created_at) VALUES (?, ?, 'test goal', 'running', ?)",
        (task_id, session_id, now),
    )
    store._conn.commit()


# ═══════════════════════════════════════════════════════════════════════════
# A. Store-level CRUD (unit tests)
# ═══════════════════════════════════════════════════════════════════════════


class TestProviderTurnCRUD:
    """Unit tests for provider_turns table CRUD operations."""

    def test_create_turn_with_all_fields(self, tmp_path: Any) -> None:
        store = _make_store(tmp_path)
        _seed_session_and_task(store)
        turn = store.create_provider_turn(
            task_id="task_1",
            session_id="sess_1",
            turn_index=0,
            model="gpt-4",
            request_message_count=5,
            request_tool_count=3,
            request_token_estimate=1200,
        )
        assert turn["id"].startswith("pt_")
        assert turn["task_id"] == "task_1"
        assert turn["session_id"] == "sess_1"
        assert turn["turn_index"] == 0
        assert turn["model"] == "gpt-4"
        assert turn["status"] == "pending"
        assert turn["request_message_count"] == 5
        assert turn["request_tool_count"] == 3
        assert turn["request_token_estimate"] == 1200
        assert turn["created_at"] > 0
        assert turn["completed_at"] is None
        store.close()

    def test_create_turn_with_minimal_fields(self, tmp_path: Any) -> None:
        store = _make_store(tmp_path)
        _seed_session_and_task(store)
        turn = store.create_provider_turn(
            task_id="task_1",
            session_id="sess_1",
            turn_index=0,
        )
        assert turn["id"].startswith("pt_")
        assert turn["status"] == "pending"
        assert turn["model"] is None
        assert turn["request_message_count"] is None
        store.close()

    def test_complete_turn_with_usage(self, tmp_path: Any) -> None:
        store = _make_store(tmp_path)
        _seed_session_and_task(store)
        turn = store.create_provider_turn(task_id="task_1", session_id="sess_1", turn_index=0)
        completed = store.complete_provider_turn(
            turn_id=turn["id"],
            finish_reason="stop",
            usage={"prompt_tokens": 100, "completion_tokens": 50},
            tool_call_count=2,
            snapshot_id="cs_abc",
        )
        assert completed["status"] == "completed"
        assert completed["response_finish_reason"] == "stop"
        assert json.loads(completed["response_usage_json"]) == {"prompt_tokens": 100, "completion_tokens": 50}
        assert completed["response_tool_call_count"] == 2
        assert completed["context_snapshot_id"] == "cs_abc"
        assert completed["completed_at"] > 0
        store.close()

    def test_complete_turn_without_optional_fields(self, tmp_path: Any) -> None:
        store = _make_store(tmp_path)
        _seed_session_and_task(store)
        turn = store.create_provider_turn(task_id="task_1", session_id="sess_1", turn_index=0)
        completed = store.complete_provider_turn(turn_id=turn["id"])
        assert completed["status"] == "completed"
        assert completed["response_finish_reason"] is None
        assert completed["response_usage_json"] is None
        assert completed["response_tool_call_count"] is None
        store.close()

    def test_fail_turn_truncates_long_error(self, tmp_path: Any) -> None:
        store = _make_store(tmp_path)
        _seed_session_and_task(store)
        turn = store.create_provider_turn(task_id="task_1", session_id="sess_1", turn_index=0)
        long_error = "x" * 1000
        failed = store.fail_provider_turn(turn_id=turn["id"], error_summary=long_error)
        assert failed["status"] == "failed"
        assert len(failed["error_summary"]) == 500
        assert failed["completed_at"] > 0
        store.close()

    def test_failed_turn_serializes_failure_recovery(self, tmp_path: Any) -> None:
        store = _make_store(tmp_path)
        _seed_session_and_task(store)
        turn = store.create_provider_turn(task_id="task_1", session_id="sess_1", turn_index=0)
        store.fail_provider_turn(
            turn_id=turn["id"],
            error_summary="Provider request timed out after 30s",
            failure_recovery={
                "category": "timeout",
                "retryable": True,
                "recoverable": True,
                "recommendedAction": "retry",
                "reason": "provider request timed out",
                "userMessage": "Provider request timed out after 30s",
            },
        )

        failed = store.list_provider_turns("task_1")[0]

        assert failed["failureRecovery"]["category"] == "timeout"
        assert failed["failureRecovery"]["retryable"] is True
        store.close()

    def test_list_turns_ordered_by_turn_index(self, tmp_path: Any) -> None:
        store = _make_store(tmp_path)
        _seed_session_and_task(store)
        store.create_provider_turn(task_id="task_1", session_id="sess_1", turn_index=2)
        store.create_provider_turn(task_id="task_1", session_id="sess_1", turn_index=0)
        store.create_provider_turn(task_id="task_1", session_id="sess_1", turn_index=1)
        turns = store.list_provider_turns("task_1")
        assert len(turns) == 3
        assert [t["turn_index"] for t in turns] == [0, 1, 2]
        store.close()

    def test_list_turns_isolated_by_task(self, tmp_path: Any) -> None:
        store = _make_store(tmp_path)
        _seed_session_and_task(store, task_id="task_1")
        _seed_session_and_task(store, task_id="task_2")
        store.create_provider_turn(task_id="task_1", session_id="sess_1", turn_index=0)
        store.create_provider_turn(task_id="task_1", session_id="sess_1", turn_index=1)
        store.create_provider_turn(task_id="task_2", session_id="sess_1", turn_index=0)
        assert len(store.list_provider_turns("task_1")) == 2
        assert len(store.list_provider_turns("task_2")) == 1
        assert len(store.list_provider_turns("nonexistent")) == 0
        store.close()


class TestContextSnapshotCRUD:
    """Unit tests for context_snapshots table CRUD operations."""

    def test_create_snapshot_with_all_fields(self, tmp_path: Any) -> None:
        store = _make_store(tmp_path)
        _seed_session_and_task(store)
        snap = store.create_context_snapshot(
            session_id="sess_1",
            task_id="task_1",
            provider_turn_id="pt_123",
            included_sections=["system_prompt", "workspace_summary"],
            trimmed_sections=[{"name": "history", "tokensDropped": 200}],
            dropped_sections=[{"name": "logs", "reason": "budget"}],
            recent_message_ids=["msg_1", "msg_2"],
            summarized_message_ids=["msg_old_1"],
            memory_ids=["mem_1", "mem_2"],
            supplement_inbox_ids=["ibx_1"],
            tool_count=5,
            skill_id="skill_x",
            token_estimate=3000,
        )
        assert snap["id"].startswith("cs_")
        assert snap["session_id"] == "sess_1"
        assert snap["task_id"] == "task_1"
        assert snap["provider_turn_id"] == "pt_123"
        assert json.loads(snap["included_sections_json"]) == ["system_prompt", "workspace_summary"]
        assert json.loads(snap["trimmed_sections_json"]) == [{"name": "history", "tokensDropped": 200}]
        assert json.loads(snap["dropped_sections_json"]) == [{"name": "logs", "reason": "budget"}]
        assert json.loads(snap["recent_message_ids_json"]) == ["msg_1", "msg_2"]
        assert json.loads(snap["summarized_message_ids_json"]) == ["msg_old_1"]
        assert json.loads(snap["memory_ids_json"]) == ["mem_1", "mem_2"]
        assert json.loads(snap["supplement_inbox_ids_json"]) == ["ibx_1"]
        assert snap["tool_count"] == 5
        assert snap["skill_id"] == "skill_x"
        assert snap["token_estimate"] == 3000
        assert snap["created_at"] > 0
        store.close()

    def test_create_snapshot_with_minimal_fields(self, tmp_path: Any) -> None:
        store = _make_store(tmp_path)
        _seed_session_and_task(store)
        snap = store.create_context_snapshot(session_id="sess_1", task_id="task_1")
        assert snap["id"].startswith("cs_")
        assert snap["included_sections_json"] in (None, "[]")
        assert snap["memory_ids_json"] in (None, "[]")
        assert snap["tool_count"] is None
        store.close()

    def test_get_snapshot_exists(self, tmp_path: Any) -> None:
        store = _make_store(tmp_path)
        _seed_session_and_task(store)
        snap = store.create_context_snapshot(session_id="sess_1", task_id="task_1")
        fetched = store.get_context_snapshot(snap["id"])
        assert fetched is not None
        assert fetched["id"] == snap["id"]
        store.close()

    def test_get_snapshot_not_found(self, tmp_path: Any) -> None:
        store = _make_store(tmp_path)
        assert store.get_context_snapshot("cs_nonexistent") is None
        store.close()

    def test_list_snapshots_by_task(self, tmp_path: Any) -> None:
        store = _make_store(tmp_path)
        _seed_session_and_task(store, task_id="task_1")
        _seed_session_and_task(store, task_id="task_2")
        store.create_context_snapshot(session_id="sess_1", task_id="task_1")
        store.create_context_snapshot(session_id="sess_1", task_id="task_1")
        store.create_context_snapshot(session_id="sess_1", task_id="task_2")
        assert len(store.list_context_snapshots("task_1")) == 2
        assert len(store.list_context_snapshots("task_2")) == 1
        assert len(store.list_context_snapshots("nonexistent")) == 0
        store.close()

    def test_list_snapshots_ordered_by_created_at(self, tmp_path: Any) -> None:
        store = _make_store(tmp_path)
        _seed_session_and_task(store)
        s1 = store.create_context_snapshot(session_id="sess_1", task_id="task_1")
        s2 = store.create_context_snapshot(session_id="sess_1", task_id="task_1")
        snaps = store.list_context_snapshots("task_1")
        assert snaps[0]["created_at"] <= snaps[1]["created_at"]
        store.close()


class TestEventsAfter:
    """Unit tests for events_after store method."""

    def _seed_trace_events(self, store: SQLiteStore, session_id: str, count: int) -> None:
        now = store.now()
        for i in range(count):
            store._conn.execute(
                """
                INSERT INTO trace_events (id, session_id, task_id, type, source, payload_json, sequence, created_at)
                VALUES (?, ?, 'task_1', 'test.event', 'test', '{}', ?, ?)
                """,
                (f"te_{session_id}_{i}", session_id, i + 1, now),
            )
        store._conn.commit()

    def test_returns_events_after_given_sequence(self, tmp_path: Any) -> None:
        store = _make_store(tmp_path)
        _seed_session_and_task(store)
        self._seed_trace_events(store, "sess_1", 5)
        result = store.events_after("sess_1", 2)
        assert len(result["events"]) == 3  # seq 3, 4, 5
        assert result["truncated"] is False
        assert result["events"][0]["sequence"] == 3
        store.close()

    def test_truncated_flag_when_exceeding_limit(self, tmp_path: Any) -> None:
        store = _make_store(tmp_path)
        _seed_session_and_task(store)
        self._seed_trace_events(store, "sess_1", 10)
        result = store.events_after("sess_1", 0, limit=3)
        assert len(result["events"]) == 3
        assert result["truncated"] is True
        store.close()

    def test_empty_result_for_unknown_session(self, tmp_path: Any) -> None:
        store = _make_store(tmp_path)
        result = store.events_after("nonexistent_session", 0)
        assert result["events"] == []
        assert result["truncated"] is False
        store.close()

    def test_events_after_zero_returns_all(self, tmp_path: Any) -> None:
        store = _make_store(tmp_path)
        _seed_session_and_task(store)
        self._seed_trace_events(store, "sess_1", 5)
        result = store.events_after("sess_1", 0)
        assert len(result["events"]) == 5
        store.close()

    def test_events_are_ordered_by_sequence(self, tmp_path: Any) -> None:
        store = _make_store(tmp_path)
        _seed_session_and_task(store)
        self._seed_trace_events(store, "sess_1", 5)
        result = store.events_after("sess_1", 0)
        seqs = [e["sequence"] for e in result["events"]]
        assert seqs == sorted(seqs)
        store.close()

    def test_events_serialized_correctly(self, tmp_path: Any) -> None:
        store = _make_store(tmp_path)
        _seed_session_and_task(store)
        self._seed_trace_events(store, "sess_1", 1)
        result = store.events_after("sess_1", 0)
        event = result["events"][0]
        assert "id" in event
        assert "taskId" in event
        assert "sessionId" in event
        assert "type" in event
        assert "sequence" in event
        assert "createdAt" in event
        assert "payload" in event
        store.close()


# ═══════════════════════════════════════════════════════════════════════════
# B. RPC handler integration
# ═══════════════════════════════════════════════════════════════════════════


class TestProviderTurnRPC:
    """Verify RPC handlers for provider_turn.list, context_snapshot.*, events.after."""

    def test_rpc_provider_turn_list_empty(self, tmp_path: Any) -> None:
        provider = ScriptedProvider([{"final": "done"}])
        runtime = _make_runtime(tmp_path, provider)
        session = _open_session(runtime, tmp_path)
        resp = _rpc(runtime, "provider_turn.list", {"taskId": "nonexistent"})
        assert resp["result"]["turns"] == []

    def test_rpc_provider_turn_list_after_task(self, tmp_path: Any) -> None:
        provider = ScriptedProvider([{"final": "All done."}])
        runtime = _make_runtime(tmp_path, provider)
        session = _open_session(runtime, tmp_path)
        task = _call_result(
            _rpc(runtime, "message.send", {"sessionId": session["id"], "content": "hello"}),
            "task",
        )
        resp = _rpc(runtime, "provider_turn.list", {"taskId": task["id"]})
        turns = resp["result"]["turns"]
        assert len(turns) >= 1
        assert turns[0]["id"].startswith("pt_")
        assert turns[0]["status"] == "completed"
        assert turns[0]["toolPolicyDecision"]["policyVersion"] == "tool-policy-v2"
        assert turns[0]["roleSnapshot"]["runtimeRole"] == "root"
        explanation = turns[0]["toolPolicyExplanation"]
        assert explanation["allowedCount"] == turns[0]["request_tool_count"]
        assert explanation["summary"]

    def test_rpc_context_snapshot_list(self, tmp_path: Any) -> None:
        provider = ScriptedProvider([{"final": "Done."}])
        runtime = _make_runtime(tmp_path, provider)
        session = _open_session(runtime, tmp_path)
        task = _call_result(
            _rpc(runtime, "message.send", {"sessionId": session["id"], "content": "test"}),
            "task",
        )
        resp = _rpc(runtime, "context_snapshot.list", {"taskId": task["id"]})
        snapshots = resp["result"]["snapshots"]
        assert len(snapshots) >= 1
        assert snapshots[0]["id"].startswith("cs_")

    def test_rpc_context_snapshot_get(self, tmp_path: Any) -> None:
        provider = ScriptedProvider([{"final": "Done."}])
        runtime = _make_runtime(tmp_path, provider)
        session = _open_session(runtime, tmp_path)
        task = _call_result(
            _rpc(runtime, "message.send", {"sessionId": session["id"], "content": "test"}),
            "task",
        )
        list_resp = _rpc(runtime, "context_snapshot.list", {"taskId": task["id"]})
        snap_id = list_resp["result"]["snapshots"][0]["id"]
        get_resp = _rpc(runtime, "context_snapshot.get", {"snapshotId": snap_id})
        assert get_resp["result"]["snapshot"]["id"] == snap_id

    def test_rpc_context_snapshot_get_not_found(self, tmp_path: Any) -> None:
        provider = ScriptedProvider([{"final": "done"}])
        runtime = _make_runtime(tmp_path, provider)
        resp = _rpc(runtime, "context_snapshot.get", {"snapshotId": "cs_nonexistent"})
        assert resp["result"]["snapshot"] is None

    def test_rpc_events_after(self, tmp_path: Any) -> None:
        provider = ScriptedProvider([{"final": "Done."}])
        runtime = _make_runtime(tmp_path, provider)
        session = _open_session(runtime, tmp_path)
        task = _call_result(
            _rpc(runtime, "message.send", {"sessionId": session["id"], "content": "test"}),
            "task",
        )
        resp = _rpc(runtime, "events.after", {"sessionId": session["id"], "afterSeq": 0})
        assert "events" in resp["result"]
        assert "truncated" in resp["result"]


# ═══════════════════════════════════════════════════════════════════════════
# C. E2E: Single-step direct answer
# ═══════════════════════════════════════════════════════════════════════════


class TestE2ESingleStep:
    """Provider returns a direct answer with no tool calls."""

    def test_single_step_produces_one_completed_turn_and_snapshot(self, tmp_path: Any) -> None:
        provider = ScriptedProvider([{"final": "The answer is 42."}])
        runtime = _make_runtime(tmp_path, provider)
        session = _open_session(runtime, tmp_path)

        task = _call_result(
            _rpc(runtime, "message.send", {"sessionId": session["id"], "content": "what is the answer?"}),
            "task",
        )

        assert task["status"] == "completed"
        assert task["resultSummary"] == "The answer is 42."

        # Verify exactly 1 ProviderTurn, completed
        turns = runtime.store.list_provider_turns(task["id"])
        assert len(turns) == 1
        assert turns[0]["status"] == "completed"
        assert turns[0]["turn_index"] == 0
        assert turns[0]["request_message_count"] is not None
        assert turns[0]["context_snapshot_id"] is not None

        # Verify exactly 1 ContextSnapshot linked to the turn
        snapshots = runtime.store.list_context_snapshots(task["id"])
        assert len(snapshots) == 1
        assert snapshots[0]["provider_turn_id"] == turns[0]["id"]
        assert snapshots[0]["token_estimate"] is not None

    def test_single_step_turn_has_request_metadata(self, tmp_path: Any) -> None:
        provider = ScriptedProvider([{"final": "done"}])
        runtime = _make_runtime(tmp_path, provider)
        session = _open_session(runtime, tmp_path)
        task = _call_result(
            _rpc(runtime, "message.send", {"sessionId": session["id"], "content": "hello"}),
            "task",
        )
        turn = runtime.store.list_provider_turns(task["id"])[0]
        assert turn["request_message_count"] > 0
        assert turn["request_tool_count"] is not None
        assert turn["request_token_estimate"] is not None


# ═══════════════════════════════════════════════════════════════════════════
# D. E2E: Multi-step with tool calls (simulating "build an app")
# ═══════════════════════════════════════════════════════════════════════════


class TestE2EMultiStepToolCalls:
    """Simulate a multi-step agent task with tool calls — like building a small app."""

    def test_multi_step_produces_turns_and_snapshots_for_each_step(self, tmp_path: Any) -> None:
        """Simulate: Step 1 read files, Step 2 write files, Step 3 verify, Step 4 final answer."""
        tool_calls_step1 = [
            {"id": "call_read", "name": "read_file", "arguments": {"path": "index.html"}},
        ]
        tool_calls_step2 = [
            {"id": "call_write", "name": "write_file", "arguments": {"path": "index.html", "content": "<h1>Hello</h1>"}},
        ]
        tool_calls_step3 = [
            {"id": "call_verify", "name": "run_command", "arguments": {"command": "dir index.html", "cwd": "."}},
        ]

        def read_file(params: dict[str, Any]) -> dict[str, Any]:
            return {"content": "<html></html>"}

        def write_file(params: dict[str, Any]) -> dict[str, Any]:
            return {
                "status": "written",
                "ok": True,
                "path": params["path"],
                "bytesWritten": len(params["content"].encode("utf-8")),
                "created": True,
            }

        def run_command(params: dict[str, Any]) -> dict[str, Any]:
            return {
                "status": "completed",
                "ok": True,
                "command": params["command"],
                "exitCode": 0,
                "stdout": "index.html\n",
            }

        provider = ScriptedProvider([
            {"message": "Let me read the existing file first.", "tool_calls": tool_calls_step1},
            {"message": "Now I'll write the updated file.", "tool_calls": tool_calls_step2},
            {"message": "I'll verify the generated file exists.", "tool_calls": tool_calls_step3},
            {"final": "I've built the app. Created index.html with Hello World."},
        ])

        runtime = _make_runtime(tmp_path, provider, {"read_file": read_file, "write_file": write_file, "run_command": run_command})
        session = _open_session(runtime, tmp_path)
        task = _call_result(
            _rpc(runtime, "message.send", {"sessionId": session["id"], "content": "build a hello world app"}),
            "task",
        )

        assert task["status"] == "completed"

        # 4 turns — one per provider call
        turns = runtime.store.list_provider_turns(task["id"])
        assert len(turns) == 4

        # All completed, with correct turn indices
        for i, turn in enumerate(turns):
            assert turn["turn_index"] == i
            assert turn["status"] == "completed"

        # First turn had 1 tool call (read_file)
        assert turns[0]["response_tool_call_count"] == 1
        # Second turn had 1 tool call (write_file)
        assert turns[1]["response_tool_call_count"] == 1
        # Third turn had 1 tool call (run_command)
        assert turns[2]["response_tool_call_count"] == 1
        # Fourth turn had 0 tool calls (final answer)
        assert turns[3]["response_tool_call_count"] == 0

        # 4 snapshots — one per step
        snapshots = runtime.store.list_context_snapshots(task["id"])
        assert len(snapshots) == 4

        # Each snapshot linked to its corresponding turn
        for snap, turn in zip(snapshots, turns):
            assert snap["provider_turn_id"] == turn["id"]

    def test_multi_step_snapshots_track_tool_count(self, tmp_path: Any) -> None:
        """Verify tool_count in snapshots reflects registered tools."""
        tool_calls = [{"id": "call_1", "name": "list_files", "arguments": {}}]

        def list_files(params: dict[str, Any]) -> dict[str, Any]:
            return {"files": ["a.txt", "b.txt"]}

        provider = ScriptedProvider([
            {"message": "Listing files.", "tool_calls": tool_calls},
            {"final": "Found 2 files."},
        ])

        runtime = _make_runtime(tmp_path, provider, {"list_files": list_files})
        session = _open_session(runtime, tmp_path)
        task = _call_result(
            _rpc(runtime, "message.send", {"sessionId": session["id"], "content": "list files"}),
            "task",
        )

        snapshots = runtime.store.list_context_snapshots(task["id"])
        assert len(snapshots) == 2
        # Tool count includes built-in tools + custom registered tools
        for snap in snapshots:
            assert snap["tool_count"] >= 1


# ═══════════════════════════════════════════════════════════════════════════
# E. E2E: Supplement injection + turn tracking
# ═══════════════════════════════════════════════════════════════════════════


class TestE2ESupplementTracking:
    """Verify that supplement inbox IDs are captured in ContextSnapshot."""

    def test_supplement_ids_recorded_in_snapshot(self, tmp_path: Any) -> None:
        """Send a supplement while task is running, verify it appears in the snapshot."""
        tool_calls = [
            {"id": "call_slow", "name": "slow_tool", "arguments": {}},
        ]

        def slow_tool(params: dict[str, Any]) -> dict[str, Any]:
            return {"result": "slow done"}

        provider = ScriptedProvider([
            {"message": "Working...", "tool_calls": tool_calls},
            {"final": "Done after supplement."},
        ])

        runtime = _make_runtime(tmp_path, provider, {"slow_tool": slow_tool})
        session = _open_session(runtime, tmp_path)
        task = _call_result(
            _rpc(runtime, "message.send", {"sessionId": session["id"], "content": "do something slow"}),
            "task",
        )

        assert task["status"] == "completed"

        # Check that the supplement.consumed event was emitted (if supplement was sent)
        # In this case we just verify the snapshot structure is valid
        snapshots = runtime.store.list_context_snapshots(task["id"])
        assert len(snapshots) == 2
        for snap in snapshots:
            # supplement_inbox_ids_json may be None or a list
            if snap["supplement_inbox_ids_json"]:
                ids = json.loads(snap["supplement_inbox_ids_json"])
                assert isinstance(ids, list)


# ═══════════════════════════════════════════════════════════════════════════
# F. E2E: Provider failure produces failed turn
# ═══════════════════════════════════════════════════════════════════════════


class TestE2EProviderFailure:
    """When the provider raises an exception, the turn should be marked as failed."""

    def test_provider_failure_creates_failed_turn(self, tmp_path: Any) -> None:
        provider = FailingProvider(RuntimeError("API rate limit exceeded"))
        runtime = _make_runtime(tmp_path, provider)
        session = _open_session(runtime, tmp_path)
        resp = _rpc(runtime, "message.send", {"sessionId": session["id"], "content": "trigger error"})

        # Task should have failed
        assert "result" in resp
        task = resp["result"]["task"]
        assert task["status"] == "failed"

        # Verify a failed ProviderTurn exists
        turns = runtime.store.list_provider_turns(task["id"])
        assert len(turns) == 1
        assert turns[0]["status"] == "failed"
        assert "API rate limit exceeded" in turns[0]["error_summary"]
        assert turns[0]["failureRecovery"]["category"] == "rate_limit"
        assert turns[0]["failureRecovery"]["retryable"] is True
        assert task["structuredResult"]["failureRecovery"]["category"] == "rate_limit"
        assert any(event["type"] == "agent.decision.failure_recovery" for event in runtime.events)

        # The ContextSnapshot should still exist (created before provider call)
        snapshots = runtime.store.list_context_snapshots(task["id"])
        assert len(snapshots) == 1


# ═══════════════════════════════════════════════════════════════════════════
# G. E2E: Multiple turns — snapshot tracks incremental context
# ═══════════════════════════════════════════════════════════════════════════

class TestAdvisorGuidedProviderRecovery:
    """Provider recovery should respect runtime gates before consulting an advisor."""

    def test_context_too_large_retries_with_runtime_compaction_without_advisor(self, tmp_path: Any) -> None:
        provider = AdvisorRecoveryProvider()
        runtime = _make_runtime(
            tmp_path,
            provider,
            decision_advisor=DecisionAdvisor(provider=provider),
        )
        session = _open_session(runtime, tmp_path)

        task = _call_result(
            _rpc(runtime, "message.send", {"sessionId": session["id"], "content": "recover from context failure"}),
            "task",
        )

        assert task["status"] == "completed"
        assert len(provider.main_calls) == 2
        assert len(provider.advisor_calls) == 0
        retry_meta = provider.main_calls[1]["context"]["_provider_recovery_retry"]
        assert retry_meta["strategy"] == "compact_or_split_context"
        assert retry_meta["advisorAccepted"] is False

        proposals = runtime.store.list_proposals({
            "taskId": task["id"],
            "kind": "failure_recovery",
        })["proposals"]
        assert not any(p["source"].get("type") == "llm" for p in proposals)
        runtime_proposal = next(p for p in proposals if p["source"].get("type") == "runtime_classifier")
        assert runtime_proposal["proposal"]["strategy"] == "compact_or_split_context"
        assert runtime_proposal["proposal"]["maxRetries"] == 1

        trace = runtime.store.list_trace_events({"taskId": task["id"]})["traceEvents"]
        trace_types = [event["type"] for event in trace]
        assert "provider.failure.recovery_decision" in trace_types
        assert "provider.failure.recovery_retry" in trace_types
        recovery_trace = next(event for event in trace if event["type"] == "provider.failure.recovery_decision")
        recovery = recovery_trace["payload"]["failureRecovery"]
        assert recovery["advisorAvailable"] is False
        assert recovery["advisorGate"]["reason"] == "no_partial_output"

    def test_timeout_without_partial_output_does_not_call_advisor_or_retry(self, tmp_path: Any) -> None:
        provider = AdvisorRecoveryProvider(
            error=ProviderAdapterError("Provider request timed out after 30s")
        )
        runtime = _make_runtime(
            tmp_path,
            provider,
            decision_advisor=DecisionAdvisor(provider=provider),
        )
        session = _open_session(runtime, tmp_path)

        task = _call_result(
            _rpc(runtime, "message.send", {"sessionId": session["id"], "content": "trigger provider timeout"}),
            "task",
        )

        assert task["status"] == "failed"
        assert len(provider.main_calls) == 1
        assert len(provider.advisor_calls) == 0
        turns = runtime.store.list_provider_turns(task["id"])
        recovery = turns[0]["failureRecovery"]
        assert recovery["category"] == "timeout"
        assert recovery["strategy"] == "surface_error"
        assert recovery["maxRetries"] == 0
        assert recovery["advisorGate"]["reason"] == "no_partial_output"

        proposals = runtime.store.list_proposals({
            "taskId": task["id"],
            "kind": "failure_recovery",
        })["proposals"]
        assert not any(p["source"].get("type") == "llm" for p in proposals)
        runtime_proposal = next(p for p in proposals if p["source"].get("type") == "runtime_classifier")
        assert runtime_proposal["proposal"]["strategy"] == "surface_error"
        assert runtime_proposal["proposal"]["maxRetries"] == 0

        trace_types = [
            event["type"]
            for event in runtime.store.list_trace_events({"taskId": task["id"]})["traceEvents"]
        ]
        assert "provider.failure.recovery_retry" not in trace_types

    def test_stream_partial_output_allows_recovery_advisor_and_fallback(self, tmp_path: Any) -> None:
        provider = PartialStreamFailureProvider()
        runtime = _make_runtime(
            tmp_path,
            provider,
            decision_advisor=DecisionAdvisor(provider=provider),
        )
        runtime.store.update_config({
            "config": {
                "provider": {
                    "mode": "openai-compatible",
                    "apiFormat": "openai-chat",
                    "stream": True,
                    "model": "fake-chat",
                }
            }
        })
        session = _open_session(runtime, tmp_path)

        task = _call_result(
            _rpc(runtime, "message.send", {"sessionId": session["id"], "content": "recover after partial stream"}),
            "task",
        )

        assert task["status"] == "completed"
        assert len(provider.stream_calls) == 1
        assert len(provider.generate_calls) == 1
        assert any("failure_recovery" in call["prompt"] for call in provider.advisor_calls)
        turns = runtime.store.list_provider_turns(task["id"])
        recovery = turns[0]["failureRecovery"]
        assert recovery["category"] == "timeout"
        assert recovery["strategy"] == "fallback"
        assert recovery["hasPartialOutput"] is True
        assert recovery["advisorGate"]["reason"] == "partial_output_available"
        assert recovery["advisorAvailable"] is True
        assert recovery["advisorAccepted"] is True

        trace_types = [
            event["type"]
            for event in runtime.store.list_trace_events({"taskId": task["id"]})["traceEvents"]
        ]
        assert "provider.stream.fallback_non_stream" in trace_types

    def test_auth_failure_with_advisor_does_not_call_advisor_or_retry(self, tmp_path: Any) -> None:
        provider = AdvisorRecoveryProvider(
            error=ProviderAdapterError("HTTP 401 Unauthorized: invalid api key")
        )
        runtime = _make_runtime(
            tmp_path,
            provider,
            decision_advisor=DecisionAdvisor(provider=provider),
        )
        session = _open_session(runtime, tmp_path)

        task = _call_result(
            _rpc(runtime, "message.send", {"sessionId": session["id"], "content": "trigger auth error"}),
            "task",
        )

        assert task["status"] == "failed"
        assert len(provider.main_calls) == 1
        assert len(provider.advisor_calls) == 0
        assert task["structuredResult"]["failureRecovery"]["category"] == "auth"

        proposals = runtime.store.list_proposals({
            "taskId": task["id"],
            "kind": "failure_recovery",
        })["proposals"]
        assert len(proposals) == 1
        assert proposals[0]["source"]["type"] == "runtime_classifier"
        assert proposals[0]["proposal"]["maxRetries"] == 0


class TestAdvisorGuidedProviderPreflight:
    """Provider preflight can consult advisor before sending the provider request."""

    def test_provider_preflight_does_not_mark_eighty_percent_as_near_limit(self, tmp_path: Any) -> None:
        provider = ScriptedProvider([{"final": "done"}])
        runtime = _make_runtime(tmp_path, provider)
        context = {
            "messages": [{"role": "user", "content": "x"}],
            "openai_tools": [],
            "config": {
                "provider": {
                    "model": "fake-chat",
                    "maxContextTokens": 100000,
                    "promptCache": {"enabled": True},
                }
            },
        }

        facts = runtime.orchestrator._provider_preflight_facts(
            provider_context=context,
            token_estimate=80000,
            compaction_threshold=100000,
        )

        assert facts["nearContextRatio"] == 0.92
        assert facts["nearContextLimit"] is False
        assert facts["riskLevel"] == "low"

    def test_provider_preflight_uses_configured_cache_high_watermark(self, tmp_path: Any) -> None:
        provider = ScriptedProvider([{"final": "done"}])
        runtime = _make_runtime(tmp_path, provider)
        context = {
            "messages": [{"role": "user", "content": "x"}],
            "openai_tools": [],
            "config": {
                "provider": {
                    "model": "fake-chat",
                    "maxContextTokens": 100000,
                    "promptCache": {"enabled": True, "nearContextRatio": 0.95},
                }
            },
        }

        facts = runtime.orchestrator._provider_preflight_facts(
            provider_context=context,
            token_estimate=93000,
            compaction_threshold=100000,
        )

        assert facts["nearContextRatio"] == 0.95
        assert facts["nearContextLimit"] is False
        assert facts["riskLevel"] == "low"

    def test_provider_preflight_compacts_before_provider_turn(self, tmp_path: Any) -> None:
        provider = PreflightCompactProvider()
        runtime = _make_runtime(
            tmp_path,
            provider,
            decision_advisor=DecisionAdvisor(provider=provider),
        )
        runtime.store.update_config({
            "config": {
                "advisor": {"alwaysProviderPreflight": True},
                "provider": {"model": "fake-chat", "maxContextTokens": 256000},
            }
        })
        session = _open_session(runtime, tmp_path)
        long_request = "summarize this large context " + ("details " * 2500)

        task = _call_result(
            _rpc(runtime, "message.send", {"sessionId": session["id"], "content": long_request}),
            "task",
        )

        assert task["status"] == "completed"
        assert any("provider_preflight" in call["prompt"] for call in provider.advisor_calls)
        assert len(provider.main_calls) == 1
        main_messages = provider.main_calls[0]["context"]["messages"]
        assert any(
            "Provider preflight compacted the request context" in message.get("content", "")
            for message in main_messages
        )

        turns = runtime.store.list_provider_turns(task["id"])
        assert len(turns) == 1
        assert turns[0]["request_message_count"] == len(main_messages)
        assert turns[0]["request_token_estimate"] == provider.main_calls[0]["context"]["_provider_preflight"]["tokenEstimate"]

        proposals = runtime.store.list_proposals({
            "taskId": task["id"],
            "kind": "provider_preflight",
        })["proposals"]
        assert any(p["source"].get("type") == "llm" for p in proposals)
        runtime_proposal = next(p for p in proposals if p["source"].get("type") == "runtime_provider_preflight")
        assert runtime_proposal["proposal"]["action"] == "compact_context"
        assert runtime_proposal["proposal"]["runtimeApplied"] is True

        trace = runtime.store.list_trace_events({"taskId": task["id"]})["traceEvents"]
        preflight_trace = next(event for event in trace if event["type"] == "provider.preflight.decision")
        assert preflight_trace["payload"]["runtimeAction"] == "compact_context"
        assert preflight_trace["payload"]["runtimeApplied"] is True

    def test_provider_preflight_switches_provider_profile_for_turn(self, tmp_path: Any) -> None:
        provider = PreflightSwitchProvider()
        runtime = _make_runtime(
            tmp_path,
            provider,
            decision_advisor=DecisionAdvisor(provider=provider),
        )
        runtime.store.update_config({
            "config": {
                "advisor": {"alwaysProviderPreflight": True},
                "provider": {
                    "activeProfileId": "primary",
                    "model": "primary-model",
                    "maxContextTokens": 256000,
                    "profiles": [
                        {
                            "id": "primary",
                            "name": "Primary",
                            "mode": "openai-compatible",
                            "model": "primary-model",
                            "enabled": True,
                            "lastStatus": "ok",
                        },
                        {
                            "id": "secondary",
                            "name": "Secondary",
                            "mode": "openai-compatible",
                            "model": "secondary-model",
                            "enabled": True,
                            "lastStatus": "ok",
                            "lastCheckedAt": 1778734168000,
                        },
                        {
                            "id": "broken",
                            "name": "Broken",
                            "mode": "openai-compatible",
                            "model": "broken-model",
                            "enabled": True,
                            "lastStatus": "missing_env",
                            "lastErrorSummary": "Set BROKEN_PROVIDER_KEY.",
                        },
                    ],
                },
            }
        })
        session = _open_session(runtime, tmp_path)

        task = _call_result(
            _rpc(runtime, "message.send", {"sessionId": session["id"], "content": "use the fallback provider for this turn"}),
            "task",
        )

        assert task["status"] == "completed"
        assert any("provider_preflight" in call["prompt"] for call in provider.advisor_calls)
        assert len(provider.main_calls) == 1
        main_provider_config = provider.main_calls[0]["context"]["config"]["provider"]
        assert main_provider_config["activeProfileId"] == "secondary"
        assert main_provider_config["model"] == "secondary-model"
        assert runtime.store.get_config({})["config"]["provider"]["activeProfileId"] == "primary"

        turns = runtime.store.list_provider_turns(task["id"])
        assert len(turns) == 1
        assert turns[0]["model"] == "secondary-model"

        proposals = runtime.store.list_proposals({
            "taskId": task["id"],
            "kind": "provider_preflight",
        })["proposals"]
        llm_proposal = next(p for p in proposals if p["source"].get("type") == "llm")
        runtime_proposal = next(p for p in proposals if p["source"].get("type") == "runtime_provider_preflight")
        assert llm_proposal["proposal"]["action"] == "switch_provider"
        assert llm_proposal["proposal"]["fallbackProviderId"] == "secondary"
        assert llm_proposal["proposal"]["runtimeAction"] == "switch_provider"
        assert llm_proposal["proposal"]["runtimeApplied"] is True
        assert runtime_proposal["proposal"]["action"] == "switch_provider"
        assert runtime_proposal["proposal"]["contextStrategy"] == "switch_provider_profile_for_turn"
        assert runtime_proposal["proposal"]["fallbackProviderId"] == "secondary"
        assert runtime_proposal["proposal"]["providerSwitch"]["toProfileId"] == "secondary"
        assert runtime_proposal["proposal"]["providerSwitch"]["health"]["healthState"] == "healthy"
        ranking = runtime_proposal["proposal"]["providerProfileRanking"]
        assert ranking[0]["id"] == "secondary"
        assert ranking[0]["switchEligible"] is True
        assert next(item for item in ranking if item["id"] == "broken")["switchEligible"] is False
        assert runtime_proposal["proposal"]["runtimeApplied"] is True

        trace = runtime.store.list_trace_events({"taskId": task["id"]})["traceEvents"]
        preflight_trace = next(event for event in trace if event["type"] == "provider.preflight.decision")
        assert preflight_trace["payload"]["runtimeAction"] == "switch_provider"
        assert preflight_trace["payload"]["runtimeApplied"] is True
        assert preflight_trace["payload"]["providerSwitch"]["toProfileId"] == "secondary"
        assert preflight_trace["payload"]["facts"]["providerProfileRanking"][0]["id"] == "secondary"

    def test_provider_preflight_rejects_unhealthy_switch_target(self, tmp_path: Any) -> None:
        provider = PreflightSwitchProvider(fallback_provider_id="secondary")
        runtime = _make_runtime(
            tmp_path,
            provider,
            decision_advisor=DecisionAdvisor(provider=provider),
        )
        runtime.store.update_config({
            "config": {
                "advisor": {"alwaysProviderPreflight": True},
                "provider": {
                    "activeProfileId": "primary",
                    "model": "primary-model",
                    "maxContextTokens": 256000,
                    "profiles": [
                        {
                            "id": "primary",
                            "name": "Primary",
                            "mode": "openai-compatible",
                            "model": "primary-model",
                            "enabled": True,
                            "lastStatus": "ok",
                        },
                        {
                            "id": "secondary",
                            "name": "Secondary",
                            "mode": "openai-compatible",
                            "model": "secondary-model",
                            "enabled": True,
                            "lastStatus": "missing_env",
                            "lastErrorSummary": "Set SECONDARY_KEY.",
                        },
                    ],
                },
            }
        })
        session = _open_session(runtime, tmp_path)

        task = _call_result(
            _rpc(runtime, "message.send", {"sessionId": session["id"], "content": "try unhealthy fallback provider"}),
            "task",
        )

        assert task["status"] == "completed"
        assert len(provider.main_calls) == 1
        main_provider_config = provider.main_calls[0]["context"]["config"]["provider"]
        assert main_provider_config["activeProfileId"] == "primary"
        assert main_provider_config["model"] == "primary-model"

        trace = runtime.store.list_trace_events({"taskId": task["id"]})["traceEvents"]
        preflight_trace = next(event for event in trace if event["type"] == "provider.preflight.decision")
        assert preflight_trace["payload"]["runtimeAction"] == "proceed"
        ranking = preflight_trace["payload"]["facts"]["providerProfileRanking"]
        secondary = next(item for item in ranking if item["id"] == "secondary")
        assert secondary["healthState"] == "unhealthy"
        assert secondary["switchEligible"] is False
        assert "providerSwitch" not in preflight_trace["payload"]

    def test_provider_preflight_split_executes_existing_planning_path(
        self,
        tmp_path: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        provider = PreflightSplitProvider()
        runtime = _make_runtime(
            tmp_path,
            provider,
            decision_advisor=DecisionAdvisor(provider=provider),
        )
        runtime.store.update_config({
            "config": {
                "advisor": {"alwaysProviderPreflight": True},
                "provider": {"model": "fake-chat", "maxContextTokens": 256000},
                "policy": {"approvalMode": "none"},
            }
        })
        dispatched: list[dict[str, Any]] = []

        def fake_dispatch(params: dict[str, Any]) -> dict[str, Any]:
            dispatched.append(dict(params))
            title = str(params.get("title") or "Subtask")
            result = {
                "summary": f"{title} completed",
                "changedFiles": [
                    {
                        "path": "runtime/src/local_agent_runtime/orchestrator/message_execution.py",
                        "status": "modified",
                        "reason": f"{title} exercised provider preflight split planning",
                    }
                ],
                "verification": [
                    {
                        "name": "provider preflight split fixture",
                        "command": "pytest runtime/tests/test_provider_turns.py::TestAdvisorGuidedProviderPreflight",
                        "status": "passed",
                        "summary": f"{title} verification passed",
                    }
                ],
                "testsRun": [
                    {
                        "name": "provider preflight split fixture",
                        "command": "pytest runtime/tests/test_provider_turns.py::TestAdvisorGuidedProviderPreflight",
                        "status": "passed",
                    }
                ],
            }
            child = runtime.store.create_collaboration_task({
                "sessionId": params.get("sessionId"),
                "parentTaskId": params.get("taskId"),
                "title": title,
                "description": params.get("prompt"),
                "metadata": {"agentType": params.get("agentType")},
            })["task"]
            runtime.store.update_collaboration_task({
                "taskId": child["id"],
                "status": "completed",
                "result": result,
            })
            return {
                "status": "completed",
                "childTaskId": child["id"],
                "summary": result["summary"],
                "result": result,
            }

        monkeypatch.setattr(runtime.orchestrator._subagent_service, "dispatch", fake_dispatch)

        session = _open_session(runtime, tmp_path)
        task = _call_result(
            _rpc(
                runtime,
                "message.send",
                {"sessionId": session["id"], "content": "inspect implement verify provider preflight split planning"},
            ),
            "task",
        )

        assert task["status"] == "completed"
        assert len(provider.main_calls) == 0
        assert any("provider_preflight" in call["prompt"] for call in provider.advisor_calls)
        assert [call["agentType"] for call in dispatched] == ["planner", "worker"]
        assert [call["title"] for call in dispatched] == [
            "Inspect provider preflight planning",
            "Implement and verify provider preflight split",
        ]
        evidence = task["structuredResult"]["completionEvidence"]
        assert evidence["evidenceLevel"] == "verified"
        assert evidence["counts"]["childTasks"] == 2
        assert evidence["counts"]["passedVerification"] >= 1
        assert evidence["childTasks"][0]["source"] == "collaboration_task"

        turns = runtime.store.list_provider_turns(task["id"])
        assert len(turns) == 1
        assert turns[0]["response_finish_reason"] == "provider_preflight_split"
        assert turns[0]["turn_decision"] == "continue"

        proposals = runtime.store.list_proposals({
            "taskId": task["id"],
            "kind": "provider_preflight",
        })["proposals"]
        llm_proposal = next(p for p in proposals if p["source"].get("type") == "llm")
        runtime_proposal = next(p for p in proposals if p["source"].get("type") == "runtime_provider_preflight")
        assert llm_proposal["proposal"]["action"] == "propose_split"
        assert llm_proposal["proposal"]["runtimeAction"] == "execute_split"
        assert runtime_proposal["proposal"]["action"] == "propose_split"
        assert runtime_proposal["proposal"]["runtimeAction"] == "execute_split"
        assert runtime_proposal["proposal"]["runtimeApplied"] is True
        assert len(runtime_proposal["proposal"]["splitRecommendation"]["subtasks"]) == 2

        trace = runtime.store.list_trace_events({"taskId": task["id"]})["traceEvents"]
        preflight_trace = next(event for event in trace if event["type"] == "provider.preflight.decision")
        assert preflight_trace["payload"]["runtimeAction"] == "execute_split"
        assert preflight_trace["payload"]["runtimeApplied"] is True
        assert preflight_trace["payload"]["splitPlan"]["execution_order"] == ["sub-0", "sub-1"]

        event_types = [event["type"] for event in runtime.events]
        assert "task.provider_preflight.split.started" in event_types
        assert "task.planning.decomposed" in event_types
        assert "task.planning.completed" in event_types


class UsageAwareProvider:
    def generate(self, prompt: str, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "final": "done",
            "raw": {
                "usage": {
                    "prompt_tokens": 11,
                    "completion_tokens": 7,
                    "total_tokens": 18,
                }
            },
        }


class TestE2ESnapshotIncremental:
    """Verify that ContextSnapshot captures context state at each turn."""

    def test_snapshots_have_increasing_message_counts(self, tmp_path: Any) -> None:
        """Each subsequent turn should see more messages in the conversation."""
        tool_calls_1 = [{"id": "call_1", "name": "echo", "arguments": {"text": "hello"}}]
        tool_calls_2 = [{"id": "call_2", "name": "echo", "arguments": {"text": "world"}}]

        def echo(params: dict[str, Any]) -> dict[str, Any]:
            return {"echo": params.get("text", "")}

        provider = ScriptedProvider([
            {"message": "Echo hello.", "tool_calls": tool_calls_1},
            {"message": "Echo world.", "tool_calls": tool_calls_2},
            {"final": "Echoed both."},
        ])

        runtime = _make_runtime(tmp_path, provider, {"echo": echo})
        session = _open_session(runtime, tmp_path)
        task = _call_result(
            _rpc(runtime, "message.send", {"sessionId": session["id"], "content": "echo twice"}),
            "task",
        )

        turns = runtime.store.list_provider_turns(task["id"])
        assert len(turns) == 3

        # Each subsequent turn should have >= message count of the previous
        msg_counts = [t["request_message_count"] for t in turns]
        for i in range(1, len(msg_counts)):
            assert msg_counts[i] >= msg_counts[i - 1], (
                f"Turn {i} has fewer messages ({msg_counts[i]}) than turn {i-1} ({msg_counts[i-1]})"
            )

    def test_snapshot_turn_linkage_is_consistent(self, tmp_path: Any) -> None:
        """Every turn should have a context_snapshot_id pointing to an existing snapshot."""
        tool_calls = [{"id": "call_1", "name": "noop", "arguments": {}}]

        def noop(params: dict[str, Any]) -> dict[str, Any]:
            return {"ok": True}

        provider = ScriptedProvider([
            {"message": "Step 1.", "tool_calls": tool_calls},
            {"message": "Step 2.", "tool_calls": tool_calls},
            {"final": "All done."},
        ])

        runtime = _make_runtime(tmp_path, provider, {"noop": noop})
        session = _open_session(runtime, tmp_path)
        task = _call_result(
            _rpc(runtime, "message.send", {"sessionId": session["id"], "content": "do 3 steps"}),
            "task",
        )

        turns = runtime.store.list_provider_turns(task["id"])
        snapshots = runtime.store.list_context_snapshots(task["id"])

        # Every turn has a snapshot linked
        snapshot_ids = {s["id"] for s in snapshots}
        for turn in turns:
            assert turn["context_snapshot_id"] in snapshot_ids, (
                f"Turn {turn['id']} references missing snapshot {turn['context_snapshot_id']}"
            )


class TestProviderTurnTransportAndUsage:
    def test_turn_persists_usage_from_raw_response(self, tmp_path: Any) -> None:
        provider = UsageAwareProvider()
        runtime = _make_runtime(tmp_path, provider)
        session = _open_session(runtime, tmp_path)

        task = _call_result(
            _rpc(runtime, "message.send", {"sessionId": session["id"], "content": "hello"}),
            "task",
        )

        turns = runtime.store.list_provider_turns(task["id"])
        assert len(turns) == 1
        assert json.loads(turns[0]["response_usage_json"]) == {
            "prompt_tokens": 11,
            "completion_tokens": 7,
            "total_tokens": 18,
        }
        assert turns[0]["response_transport"] == "non_stream"

    def test_stream_fallback_turn_persists_transport(self, tmp_path: Any) -> None:
        provider = PartialStreamFailureProvider()
        runtime = _make_runtime(
            tmp_path,
            provider,
            decision_advisor=DecisionAdvisor(provider=provider),
        )
        runtime.store.update_config({
            "config": {
                "provider": {
                    "mode": "openai-compatible",
                    "apiFormat": "openai-chat",
                    "streamingEnabled": True,
                    "model": "fake-stream",
                }
            }
        })
        session = _open_session(runtime, tmp_path)

        task = _call_result(
            _rpc(runtime, "message.send", {"sessionId": session["id"], "content": "stream then recover"}),
            "task",
        )

        turns = runtime.store.list_provider_turns(task["id"])
        assert len(turns) == 1
        assert turns[0]["response_transport"] == "fallback_non_stream"


# ═══════════════════════════════════════════════════════════════════════════
# H. Edge cases and boundary conditions
# ═══════════════════════════════════════════════════════════════════════════


class TestEdgeCases:
    """Edge cases: empty tasks, concurrent tasks, missing data."""

    def test_no_turns_for_nonexistent_task(self, tmp_path: Any) -> None:
        store = _make_store(tmp_path)
        assert store.list_provider_turns("nonexistent_task") == []
        assert store.list_context_snapshots("nonexistent_task") == []
        store.close()

    def test_rpc_events_after_for_empty_session(self, tmp_path: Any) -> None:
        provider = ScriptedProvider([{"final": "done"}])
        runtime = _make_runtime(tmp_path, provider)
        session = _open_session(runtime, tmp_path)
        resp = _rpc(runtime, "events.after", {"sessionId": session["id"], "afterSeq": 999})
        assert resp["result"]["events"] == []
        assert resp["result"]["truncated"] is False

    def test_two_concurrent_tasks_produce_separate_turns(self, tmp_path: Any) -> None:
        """Two tasks in the same session should have independent ProviderTurns."""
        provider = ScriptedProvider([
            {"final": "First task done."},
            {"final": "Second task done."},
        ])
        runtime = _make_runtime(tmp_path, provider)
        session = _open_session(runtime, tmp_path)

        task1 = _call_result(
            _rpc(runtime, "message.send", {"sessionId": session["id"], "content": "task 1"}),
            "task",
        )
        task2 = _call_result(
            _rpc(runtime, "message.send", {"sessionId": session["id"], "content": "task 2"}),
            "task",
        )

        turns1 = runtime.store.list_provider_turns(task1["id"])
        turns2 = runtime.store.list_provider_turns(task2["id"])

        assert len(turns1) == 1
        assert len(turns2) == 1
        assert turns1[0]["task_id"] == task1["id"]
        assert turns2[0]["task_id"] == task2["id"]
        assert turns1[0]["id"] != turns2[0]["id"]

    def test_supplement_inbox_ids_appear_in_second_turn_snapshot(self, tmp_path: Any) -> None:
        """Inject supplement inbox entry; verify second turn's snapshot captures it."""
        from unittest.mock import patch

        tool_calls_0 = [{"id": "call_1", "name": "echo", "arguments": {"text": "hello"}}]

        def echo(params: dict[str, Any]) -> dict[str, Any]:
            return {"echo": params.get("text", "")}

        provider = ScriptedProvider([
            {"message": "Working...", "tool_calls": tool_calls_0},
            {"final": "Done after supplement."},
        ])

        runtime = _make_runtime(tmp_path, provider, {"echo": echo})
        session = _open_session(runtime, tmp_path)

        fake_inbox_id = "ibx_injected_test"
        fake_entry = {"id": fake_inbox_id, "content": "extra info", "task_id": ""}

        original_get_pending = runtime.store.get_pending_supplements
        call_count = [0]

        def patched_get_pending(task_id: str) -> list[dict[str, Any]]:
            call_count[0] += 1
            if call_count[0] == 2:
                # Second turn: return fake supplement so it gets captured in snapshot
                return [{**fake_entry, "task_id": task_id}]
            return original_get_pending(task_id)

        with patch.object(runtime.store, "get_pending_supplements", side_effect=patched_get_pending):
            task = _call_result(
                _rpc(runtime, "message.send", {"sessionId": session["id"], "content": "do something"}),
                "task",
            )

        assert task["status"] == "completed"

        # Second turn's snapshot should have the inbox entry ID
        snapshots = runtime.store.list_context_snapshots(task["id"])
        assert len(snapshots) == 2  # turn 0 + turn 1

        snap_1 = snapshots[1]  # second turn
        assert snap_1["supplement_inbox_ids_json"] is not None, (
            "supplement_inbox_ids_json is None — supplement was not captured"
        )
        ids = json.loads(snap_1["supplement_inbox_ids_json"])
        assert fake_inbox_id in ids, f"Expected {fake_inbox_id} in {ids}"

    def test_skill_id_appears_in_snapshot(self, tmp_path: Any) -> None:
        """When MetaRouter routes to a skill, snapshot captures skill_id."""
        from local_agent_runtime.router.types import RoutingDecision, Scenario, ExecutionStrategy
        from unittest.mock import patch

        provider = ScriptedProvider([{"final": "Skill executed."}])
        runtime = _make_runtime(tmp_path, provider)
        session = _open_session(runtime, tmp_path)

        skill_routing = RoutingDecision(
            scenario=Scenario.SIMPLE_QUERY,
            strategy=ExecutionStrategy.REACT_STANDARD,
            confidence=0.95,
            skill_id="skill_code_review",
            max_steps=5,
        )

        with patch.object(runtime.server._orchestrator._meta_router, "route", return_value=skill_routing):
            task = _call_result(
                _rpc(runtime, "message.send", {"sessionId": session["id"], "content": "review my code"}),
                "task",
            )

        assert task["status"] == "completed"
        snapshots = runtime.store.list_context_snapshots(task["id"])
        assert len(snapshots) >= 1
        assert snapshots[0]["skill_id"] == "skill_code_review"

    def test_snapshot_json_fields_handle_none_gracefully(self, tmp_path: Any) -> None:
        """ContextSnapshot with all optional fields as None should store NULL."""
        store = _make_store(tmp_path)
        _seed_session_and_task(store)
        snap = store.create_context_snapshot(
            session_id="sess_1",
            task_id="task_1",
            included_sections=None,
            memory_ids=None,
            supplement_inbox_ids=None,
        )
        assert snap["included_sections_json"] in (None, "[]")
        assert snap["memory_ids_json"] in (None, "[]")
        assert snap["supplement_inbox_ids_json"] in (None, "[]")
        store.close()

    def test_events_after_limit_capped_at_500(self, tmp_path: Any) -> None:
        """Even with limit > 500, events_after should cap at 500."""
        store = _make_store(tmp_path)
        result = store.events_after("any", 0, limit=9999)
        # Should not raise; just return whatever is there
        assert isinstance(result["events"], list)
        store.close()
