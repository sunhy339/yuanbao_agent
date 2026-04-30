from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import pytest

from local_agent_runtime.reflection.evaluator import ReflectionEvaluator, _DEFAULT_EVALUATION_PROMPT
from local_agent_runtime.reflection.types import ReflectionConfig, ReflectionIteration, ReflectionResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class MockProvider:
    """Minimal provider stub that returns canned ``generate()`` responses."""

    def __init__(
        self,
        *,
        response: str | None = None,
        responses: list[str] | None = None,
    ) -> None:
        if responses is not None:
            self._responses = list(responses)
            self._default = responses[-1]  # repeat last response when exhausted
        elif response is not None:
            self._responses = []
            self._default = response
        else:
            self._default = '{"score": 0.5, "feedback": "default"}'
            self._responses = []

    def generate(self, prompt: str, context: dict[str, Any]) -> dict[str, Any]:
        text = self._responses.pop(0) if self._responses else self._default
        return {"message": text, "prompt": prompt}


# ---------------------------------------------------------------------------
# Unit tests — ReflectionEvaluator
# ---------------------------------------------------------------------------


class TestReflectionEvaluatorEvaluate:
    def test_parses_score_and_feedback_from_json(self) -> None:
        provider = MockProvider(response='{"score": 0.85, "feedback": "Well done"}')
        evaluator = ReflectionEvaluator(provider, ReflectionConfig())
        result = evaluator.evaluate(goal="fix bug", output="fixed", context="")
        assert result.quality_score == 0.85
        assert result.feedback == "Well done"
        assert result.accepted is True  # 0.85 >= default 0.7

    def test_below_threshold_not_accepted(self) -> None:
        provider = MockProvider(response='{"score": 0.3, "feedback": "Incomplete"}')
        evaluator = ReflectionEvaluator(provider, ReflectionConfig(confidence_threshold=0.7))
        result = evaluator.evaluate(goal="fix bug", output="partial", context="")
        assert result.accepted is False
        assert result.quality_score == 0.3

    def test_score_exactly_at_threshold(self) -> None:
        provider = MockProvider(response='{"score": 0.7, "feedback": "Borderline"}')
        evaluator = ReflectionEvaluator(provider, ReflectionConfig(confidence_threshold=0.7))
        result = evaluator.evaluate(goal="fix", output="ok", context="")
        assert result.accepted is True

    def test_iteration_number_propagated(self) -> None:
        provider = MockProvider(response='{"score": 0.9, "feedback": "Good"}')
        evaluator = ReflectionEvaluator(provider, ReflectionConfig())
        result = evaluator.evaluate(goal="x", output="y", context="", iteration=3)
        assert result.iteration == 3

    def test_handles_fenced_json_block(self) -> None:
        raw = 'Here is my evaluation:\n```json\n{"score": 0.8, "feedback": "Nice"}\n```\nDone.'
        provider = MockProvider(response=raw)
        evaluator = ReflectionEvaluator(provider, ReflectionConfig())
        result = evaluator.evaluate(goal="x", output="y", context="")
        assert result.quality_score == 0.8

    def test_handles_malformed_json_fallback(self) -> None:
        raw = "The score is 0.5 and the work needs improvement."
        provider = MockProvider(response=raw)
        evaluator = ReflectionEvaluator(provider, ReflectionConfig())
        result = evaluator.evaluate(goal="x", output="y", context="")
        assert result.quality_score == 0.5  # fallback extraction

    def test_no_score_in_json_defaults_to_zero(self) -> None:
        raw = '{"feedback": "missing score field"}'
        provider = MockProvider(response=raw)
        evaluator = ReflectionEvaluator(provider, ReflectionConfig())
        result = evaluator.evaluate(goal="x", output="y", context="")
        # _try_parse_json returns None (no score key), fallback finds no float, returns 0.0
        assert result.quality_score == 0.0

    def test_context_included_in_prompt(self) -> None:
        provider = MockProvider(response='{"score": 0.9, "feedback": "Good"}')
        evaluator = ReflectionEvaluator(provider, ReflectionConfig())

        captured: dict[str, Any] = {}

        def capture_generate(prompt: str, context: dict[str, Any]) -> dict[str, Any]:
            captured["prompt"] = prompt
            return {"message": '{"score": 0.9, "feedback": "ok"}'}

        evaluator._provider.generate = capture_generate  # type: ignore[assignment]
        evaluator.evaluate(goal="my goal", output="my output", context="extra context")
        assert "my goal" in captured["prompt"]
        assert "my output" in captured["prompt"]
        assert "extra context" in captured["prompt"]

    def test_custom_evaluation_prompt_used(self) -> None:
        custom = "Rate this security review:\ngoal={goal}\noutput={output}\n{context_section}"
        config = ReflectionConfig(evaluation_prompt=custom)
        provider = MockProvider(response='{"score": 0.9, "feedback": "Custom"}')
        evaluator = ReflectionEvaluator(provider, config)

        captured: dict[str, Any] = {}

        def capture_generate(prompt: str, context: dict[str, Any]) -> dict[str, Any]:
            captured["prompt"] = prompt
            return {"message": '{"score": 0.9, "feedback": "ok"}'}

        evaluator._provider.generate = capture_generate  # type: ignore[assignment]
        evaluator.evaluate(goal="g", output="o", context="c")
        assert captured["prompt"].startswith("Rate this security review:")


class TestReflectionEvaluatorReflect:
    def test_single_pass_accepted(self) -> None:
        provider = MockProvider(response='{"score": 0.9, "feedback": "Great"}')
        evaluator = ReflectionEvaluator(provider, ReflectionConfig())
        result = evaluator.reflect(goal="fix", output="done", context="")
        assert result.accepted is True
        assert len(result.iterations) == 1
        assert result.final_score == 0.9
        assert result.improved_summary == "done"

    def test_retries_until_accepted(self) -> None:
        provider = MockProvider(responses=[
            '{"score": 0.3, "feedback": "Bad"}',
            '{"score": 0.6, "feedback": "Better"}',
            '{"score": 0.8, "feedback": "Good"}',
        ])
        retry_fn = MagicMock(return_value="improved output")
        evaluator = ReflectionEvaluator(provider, ReflectionConfig(max_retries=3))
        result = evaluator.reflect(goal="fix", output="draft", context="", retry_fn=retry_fn)
        assert result.accepted is True
        assert len(result.iterations) == 3
        assert retry_fn.call_count == 2
        assert result.improved_summary == "improved output"

    def test_exhausts_retries(self) -> None:
        provider = MockProvider(response='{"score": 0.3, "feedback": "Still bad"}')
        evaluator = ReflectionEvaluator(
            provider, ReflectionConfig(max_retries=2, confidence_threshold=0.7),
        )
        result = evaluator.reflect(goal="fix", output="bad", context="")
        assert result.accepted is False
        assert len(result.iterations) == 3  # initial + 2 retries
        assert result.final_score == 0.3
        assert result.improved_summary is None  # no retry_fn

    def test_no_retry_fn_only_evaluates(self) -> None:
        """Without retry_fn, reflect only evaluates — no re-generation."""
        provider = MockProvider(responses=[
            '{"score": 0.3, "feedback": "Not good"}',
            '{"score": 0.3, "feedback": "Still not good"}',
            '{"score": 0.3, "feedback": "Nope"}',
        ])
        evaluator = ReflectionEvaluator(
            provider, ReflectionConfig(max_retries=2, confidence_threshold=0.7),
        )
        result = evaluator.reflect(goal="fix", output="bad", context="")
        assert result.accepted is False
        assert len(result.iterations) == 3
        assert result.improved_summary is None

    def test_retry_fn_feeds_back_feedback(self) -> None:
        provider = MockProvider(responses=[
            '{"score": 0.4, "feedback": "Add tests"}',
            '{"score": 0.9, "feedback": "Perfect"}',
        ])

        def retry(feedback: str) -> str:
            return f"added {feedback.lower()}"

        evaluator = ReflectionEvaluator(provider, ReflectionConfig(max_retries=1))
        result = evaluator.reflect(goal="code", output="draft", context="", retry_fn=retry)
        assert result.accepted is True
        assert result.improved_summary == "added add tests"


class TestReflectionEvaluatorToDict:
    def test_serialises_result(self) -> None:
        provider = MockProvider(response='{"score": 0.9, "feedback": "ok"}')
        evaluator = ReflectionEvaluator(provider, ReflectionConfig())
        result = evaluator.reflect(goal="g", output="o", context="")
        d = evaluator.to_dict(result)
        assert d["accepted"] is True
        assert d["finalScore"] == 0.9
        assert len(d["iterations"]) == 1
        assert d["iterations"][0]["iteration"] == 0
        assert d["iterations"][0]["qualityScore"] == 0.9
        assert d["iterations"][0]["feedback"] == "ok"
        assert d["iterations"][0]["accepted"] is True

    def test_json_roundtrip(self) -> None:
        provider = MockProvider(response='{"score": 0.8, "feedback": "nice"}')
        evaluator = ReflectionEvaluator(provider, ReflectionConfig())
        result = evaluator.reflect(goal="g", output="o", context="")
        d = evaluator.to_dict(result)
        text = json.dumps(d, ensure_ascii=False)
        parsed = json.loads(text)
        assert parsed["finalScore"] == 0.8


# ---------------------------------------------------------------------------
# Store-level tests — reflection config & persistence
# ---------------------------------------------------------------------------


class TestReflectionStore:
    def test_default_config_includes_reflection(self, tmp_path: Any) -> None:
        from local_agent_runtime.store.sqlite_store import SQLiteStore

        store = SQLiteStore(str(tmp_path / "test.db"))
        config = store.get_config({})["config"]
        assert "reflection" in config
        rc = config["reflection"]
        assert rc["enabled"] is False
        assert rc["maxRetries"] == 2
        assert rc["confidenceThreshold"] == 0.7
        assert rc["evaluationPrompt"] == ""
        store.close()

    def test_task_reflection_json_roundtrip(self, tmp_path: Any) -> None:
        from local_agent_runtime.store.sqlite_store import SQLiteStore

        store = SQLiteStore(str(tmp_path / "test.db"))
        session = store.create_session(workspace_id="w1", title="t")
        task = store.create_task(session_id=session["id"], task_type="generic", goal="g", plan=[])

        reflection_data = {
            "accepted": True,
            "finalScore": 0.85,
            "iterations": [
                {"iteration": 0, "qualityScore": 0.85, "feedback": "Good", "accepted": True},
            ],
        }
        updated = store.update_task(task_id=task["id"], status="completed", reflection=reflection_data)
        assert updated["reflection"] == reflection_data
        store.close()

    def test_task_reflection_none_when_not_set(self, tmp_path: Any) -> None:
        from local_agent_runtime.store.sqlite_store import SQLiteStore

        store = SQLiteStore(str(tmp_path / "test.db"))
        session = store.create_session(workspace_id="w1", title="t")
        task = store.create_task(session_id=session["id"], task_type="generic", goal="g", plan=[])
        updated = store.update_task(task_id=task["id"], status="completed")
        assert updated.get("reflection") is None
        store.close()


# ---------------------------------------------------------------------------
# Integration tests — Orchestrator._reflect_on_result
# ---------------------------------------------------------------------------


class TestOrchestratorReflection:
    def _make_orchestrator(
        self,
        tmp_path: Any,
        *,
        reflection_enabled: bool = False,
    ) -> tuple[Any, Any, list[dict[str, Any]]]:
        """Build an Orchestrator with optional reflection config."""
        from local_agent_runtime.event_bus import EventBus
        from local_agent_runtime.orchestrator.service import Orchestrator
        from local_agent_runtime.provider.adapter import ProviderAdapter
        from local_agent_runtime.store.sqlite_store import SQLiteStore
        from local_agent_runtime.tools import build_builtin_tools
        from local_agent_runtime.tools.registry import ToolRegistry
        from local_agent_runtime.policy.guard import PolicyGuard
        from local_agent_runtime.services import CollaborationService, SubagentService

        store = SQLiteStore(str(tmp_path / "test.db"))
        if reflection_enabled:
            config = store.get_config({})["config"]
            config["reflection"]["enabled"] = True
            store.update_config(config)

        event_bus = EventBus()
        config = store.get_config({})["config"]
        policy_guard = PolicyGuard(approval_mode=config["policy"]["approvalMode"])
        collaboration = CollaborationService(store, event_bus)
        subagent_service = SubagentService(store, collaboration)
        tool_registry = ToolRegistry(
            build_builtin_tools(policy_guard=policy_guard, store=store, subagent_service=subagent_service),
        )
        provider = ProviderAdapter()
        orchestrator = Orchestrator(
            store=store, event_bus=event_bus,
            tool_registry=tool_registry, provider=provider,
        )

        events: list[dict[str, Any]] = []
        event_bus.subscribe(lambda event: events.append(event_bus.as_payload(event)))
        return orchestrator, store, events

    def test_reflector_none_when_global_disabled(self, tmp_path: Any) -> None:
        orch, store, _ = self._make_orchestrator(tmp_path, reflection_enabled=False)
        assert orch._reflector is None
        store.close()

    def test_reflector_created_when_global_enabled(self, tmp_path: Any) -> None:
        orch, store, _ = self._make_orchestrator(tmp_path, reflection_enabled=True)
        assert orch._reflector is not None
        store.close()

    def test_reflect_returns_none_when_reflector_is_none(self, tmp_path: Any) -> None:
        orch, store, _ = self._make_orchestrator(tmp_path, reflection_enabled=False)
        session = store.create_session(workspace_id="w1", title="t")
        task = store.create_task(session_id=session["id"], task_type="generic", goal="g", plan=[])
        result = orch._reflect_on_result(
            session_id=session["id"], task=task,
            goal="g", summary="s", context={"routing": {"enable_reflection": True}},
        )
        assert result is None
        store.close()

    def test_reflect_returns_none_when_routing_disabled(self, tmp_path: Any) -> None:
        orch, store, _ = self._make_orchestrator(tmp_path, reflection_enabled=True)
        session = store.create_session(workspace_id="w1", title="t")
        task = store.create_task(session_id=session["id"], task_type="generic", goal="g", plan=[])
        result = orch._reflect_on_result(
            session_id=session["id"], task=task,
            goal="g", summary="s", context={"routing": {"enable_reflection": False}},
        )
        assert result is None
        store.close()

    def test_reflect_returns_none_when_no_routing(self, tmp_path: Any) -> None:
        orch, store, _ = self._make_orchestrator(tmp_path, reflection_enabled=True)
        session = store.create_session(workspace_id="w1", title="t")
        task = store.create_task(session_id=session["id"], task_type="generic", goal="g", plan=[])
        result = orch._reflect_on_result(
            session_id=session["id"], task=task,
            goal="g", summary="s", context={},
        )
        assert result is None
        store.close()

    def test_reflect_triggers_when_both_enabled(self, tmp_path: Any) -> None:
        orch, store, events = self._make_orchestrator(tmp_path, reflection_enabled=True)
        session = store.create_session(workspace_id="w1", title="t")
        task = store.create_task(session_id=session["id"], task_type="generic", goal="g", plan=[])

        # Mock the reflector's provider to return a valid evaluation
        def mock_generate(prompt: str, context: dict[str, Any]) -> dict[str, Any]:
            return {"message": '{"score": 0.9, "feedback": "Looks good"}'}

        orch._reflector._provider.generate = mock_generate  # type: ignore[assignment]

        result = orch._reflect_on_result(
            session_id=session["id"], task=task,
            goal="fix the bug", summary="I fixed the bug by...",
            context={"routing": {"enable_reflection": True}},
        )
        # With mock provider, the evaluator gets a deterministic response
        assert result is not None
        assert isinstance(result, ReflectionResult)

        # Check events were published
        event_types = [e["type"] for e in events]
        assert "task.reflection.started" in event_types
        assert "task.reflection.completed" in event_types

        # Task status should have been set to "verifying"
        store.close()

    def test_reflection_events_contain_payload(self, tmp_path: Any) -> None:
        orch, store, events = self._make_orchestrator(tmp_path, reflection_enabled=True)
        session = store.create_session(workspace_id="w1", title="t")
        task = store.create_task(session_id=session["id"], task_type="generic", goal="g", plan=[])

        def mock_generate(prompt: str, context: dict[str, Any]) -> dict[str, Any]:
            return {"message": '{"score": 0.85, "feedback": "Acceptable"}'}

        orch._reflector._provider.generate = mock_generate  # type: ignore[assignment]

        orch._reflect_on_result(
            session_id=session["id"], task=task,
            goal="review code", summary="Code looks good",
            context={"routing": {"enable_reflection": True}},
        )

        started = next(e for e in events if e["type"] == "task.reflection.started")
        assert started["payload"]["goal"] == "review code"

        completed = next(e for e in events if e["type"] == "task.reflection.completed")
        assert "accepted" in completed["payload"]
        assert "finalScore" in completed["payload"]
        assert "iterations" in completed["payload"]
        store.close()
