from __future__ import annotations

import json
from typing import Any

import pytest

from local_agent_runtime.orchestration.swarm import SwarmOrchestrator
from local_agent_runtime.orchestration.types import OrchestrationResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class MockSubagentService:
    """Records calls and returns fixed results."""

    def __init__(self, *, results: dict[str, str] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._results = results or {}
        self._call_count = 0

    def dispatch(self, params: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(params)
        self._call_count += 1
        title = params.get("title", "")
        summary = self._results.get(title, f"Result for {title}")
        return {"summary": summary, "status": "completed"}


class MockProvider:
    """Returns configurable decompose + handoff responses."""

    def __init__(
        self,
        *,
        subtasks: list[dict] | None = None,
        handoffs: list[str] | None = None,
    ) -> None:
        self._subtasks = subtasks or [
            {"id": "sub-0", "title": "Step A", "description": "Do A", "dependencies": []},
            {"id": "sub-1", "title": "Step B", "description": "Do B", "dependencies": ["sub-0"]},
            {"id": "sub-2", "title": "Step C", "description": "Do C", "dependencies": ["sub-1"]},
        ]
        self._handoffs = handoffs or []
        self._handoff_idx = 0
        self._decompose_called = False

    def generate(self, prompt: str, context: dict[str, Any]) -> dict[str, Any]:
        if "task decomposition specialist" in prompt and not self._decompose_called:
            self._decompose_called = True
            return {"message": json.dumps(self._subtasks)}

        if "swarm coordinator" in prompt:
            if self._handoff_idx < len(self._handoffs):
                resp = self._handoffs[self._handoff_idx]
                self._handoff_idx += 1
            else:
                resp = json.dumps({"done": True, "next_subtask_id": None, "handoff_prompt": None})
            return {"message": resp}

        return {"message": "Synthesized result"}


def _make_task() -> dict[str, Any]:
    return {"id": "task-1", "status": "running"}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSwarmSequential:
    def test_sequential_handoff(self) -> None:
        mock_sub = MockSubagentService()
        mock_prov = MockProvider(
            handoffs=[
                # After sub-0: go to sub-1
                json.dumps({"next_subtask_id": "sub-1", "handoff_prompt": None, "done": False}),
                # After sub-1: go to sub-2
                json.dumps({"next_subtask_id": "sub-2", "handoff_prompt": None, "done": False}),
                # After sub-2: done
                json.dumps({"done": True}),
            ],
        )
        swarm = SwarmOrchestrator(provider=mock_prov, subagent_service=mock_sub)
        result = swarm.execute(
            "Do A, B, C", {},
            session_id="sess-1", task=_make_task(),
        )

        assert isinstance(result, OrchestrationResult)
        assert result.success is True
        assert result.handoff_count == 2  # handoff after sub-0 and sub-1
        assert len(mock_sub.calls) == 3
        assert mock_sub.calls[0]["agentType"] == "worker"
        assert "apply_patch" in mock_sub.calls[0]["childToolAllowlist"]

    def test_passes_parent_timeout_budget_to_child_dispatch(self) -> None:
        mock_sub = MockSubagentService()
        mock_prov = MockProvider(handoffs=[json.dumps({"done": True})])
        swarm = SwarmOrchestrator(provider=mock_prov, subagent_service=mock_sub)

        swarm.execute(
            "Do A",
            {},
            session_id="sess-1",
            task=_make_task(),
            child_timeout_ms=600_000,
        )

        assert mock_sub.calls[0]["timeoutMs"] == 600_000

    def test_respects_agent_type_from_decomposition(self) -> None:
        mock_sub = MockSubagentService()
        mock_prov = MockProvider(
            subtasks=[
                {
                    "id": "sub-0",
                    "title": "Risk pass",
                    "description": "Read files and assess risk.",
                    "dependencies": [],
                    "agentType": "planner",
                },
                {
                    "id": "sub-1",
                    "title": "Implement pass",
                    "description": "Write the implementation.",
                    "dependencies": ["sub-0"],
                    "agentType": "worker",
                },
            ],
            handoffs=[
                json.dumps({"next_subtask_id": "sub-1", "handoff_prompt": None, "done": False}),
                json.dumps({"done": True}),
            ],
        )
        swarm = SwarmOrchestrator(provider=mock_prov, subagent_service=mock_sub)

        swarm.execute("Plan, then implement", {}, session_id="sess-1", task=_make_task())

        assert mock_sub.calls[0]["agentType"] == "planner"
        assert "apply_patch" not in mock_sub.calls[0]["childToolAllowlist"]
        assert mock_sub.calls[1]["agentType"] == "worker"
        assert "apply_patch" in mock_sub.calls[1]["childToolAllowlist"]


class TestSwarmEarlyDone:
    def test_early_completion(self) -> None:
        mock_sub = MockSubagentService()
        mock_prov = MockProvider(
            subtasks=[
                {"id": "sub-0", "title": "Step A", "description": "Do A", "dependencies": []},
                {"id": "sub-1", "title": "Step B", "description": "Do B", "dependencies": []},
                {"id": "sub-2", "title": "Step C", "description": "Do C", "dependencies": []},
            ],
            handoffs=[
                # After sub-0: LLM says done
                json.dumps({"done": True}),
            ],
        )
        swarm = SwarmOrchestrator(provider=mock_prov, subagent_service=mock_sub)
        result = swarm.execute(
            "Do A, B, C", {},
            session_id="sess-1", task=_make_task(),
        )

        assert result.success is True
        assert len(mock_sub.calls) == 1  # only sub-0 executed


class TestSwarmPause:
    def test_cooperative_pause(self) -> None:
        pause_counter = {"count": 0}

        def is_paused():
            pause_counter["count"] += 1
            return pause_counter["count"] > 1

        mock_sub = MockSubagentService()
        mock_prov = MockProvider(
            handoffs=[
                json.dumps({"next_subtask_id": "sub-1", "handoff_prompt": None, "done": False}),
            ],
        )
        swarm = SwarmOrchestrator(provider=mock_prov, subagent_service=mock_sub)
        result = swarm.execute(
            "Do A, B, C", {},
            session_id="sess-1", task=_make_task(),
            is_paused_fn=is_paused,
        )

        assert result.paused is True
        assert len(result.completed) >= 1


class TestSwarmHandoffPrompt:
    def test_uses_handoff_prompt(self) -> None:
        mock_sub = MockSubagentService()
        mock_prov = MockProvider(
            subtasks=[
                {"id": "sub-0", "title": "Step A", "description": "Do A", "dependencies": []},
                {"id": "sub-1", "title": "Step B", "description": "Do B", "dependencies": []},
            ],
            handoffs=[
                json.dumps({
                    "next_subtask_id": "sub-1",
                    "handoff_prompt": "Custom context for B based on A's result",
                    "done": False,
                }),
                json.dumps({"done": True}),
            ],
        )
        swarm = SwarmOrchestrator(provider=mock_prov, subagent_service=mock_sub)
        result = swarm.execute(
            "Do A then B", {},
            session_id="sess-1", task=_make_task(),
        )

        assert result.success is True
        # Second dispatch should use handoff prompt
        assert len(mock_sub.calls) == 2
        assert "Custom context" in mock_sub.calls[1]["prompt"]


class TestSwarmInstanceIsolation:
    def test_handoff_prompt_not_shared_across_instances(self) -> None:
        """Two SwarmOrchestrator instances must have independent handoff prompts."""
        # Instance 1: sets a handoff prompt
        mock_sub1 = MockSubagentService()
        mock_prov1 = MockProvider(
            subtasks=[
                {"id": "sub-0", "title": "Step A", "description": "Do A", "dependencies": []},
                {"id": "sub-1", "title": "Step B", "description": "Do B", "dependencies": []},
            ],
            handoffs=[
                json.dumps({
                    "next_subtask_id": "sub-1",
                    "handoff_prompt": "Context from instance 1",
                    "done": False,
                }),
                json.dumps({"done": True}),
            ],
        )
        s1 = SwarmOrchestrator(provider=mock_prov1, subagent_service=mock_sub1)
        s1.execute("Task 1", {}, session_id="s1", task=_make_task())

        # Instance 2: no handoff prompts, should NOT inherit from instance 1
        mock_sub2 = MockSubagentService()
        mock_prov2 = MockProvider(
            subtasks=[
                {"id": "sub-0", "title": "Task X", "description": "Do X", "dependencies": []},
            ],
            handoffs=[],
        )
        s2 = SwarmOrchestrator(provider=mock_prov2, subagent_service=mock_sub2)
        result2 = s2.execute("Task 2", {}, session_id="s2", task=_make_task())

        assert result2.success is True
        assert len(mock_sub2.calls) == 1
        # Should use original description, NOT leaked handoff prompt from s1
        assert "Do X" in mock_sub2.calls[0]["prompt"]
        assert "Context from instance 1" not in mock_sub2.calls[0]["prompt"]


class TestSwarmProviderContext:
    def test_decompose_and_handoff_receive_provider_context(self) -> None:
        seen_contexts: list[dict[str, Any]] = []

        class CapturingProvider:
            def __init__(self) -> None:
                self._decompose_called = False
                self._handoff_called = False

            def generate(self, prompt: str, context: dict[str, Any]) -> dict[str, Any]:
                seen_contexts.append(context)
                if "task decomposition specialist" in prompt and not self._decompose_called:
                    self._decompose_called = True
                    return {"message": json.dumps([
                        {"id": "sub-0", "title": "Step A", "description": "Do A", "dependencies": []},
                        {"id": "sub-1", "title": "Step B", "description": "Do B", "dependencies": []},
                    ])}
                if "swarm coordinator" in prompt and not self._handoff_called:
                    self._handoff_called = True
                    return {"message": json.dumps({"done": True})}
                return {"message": "Synthesized result"}

        swarm = SwarmOrchestrator(
            provider=CapturingProvider(),
            subagent_service=MockSubagentService(),
        )

        swarm.execute(
            "Do A then B",
            {"_provider_context": {"config": {"provider": {"streamingEnabled": True, "model": "gpt-5.4"}}}},
            session_id="sess-1",
            task=_make_task(),
        )

        assert len(seen_contexts) >= 2
        for context in seen_contexts[:2]:
            assert context["config"]["provider"]["streamingEnabled"] is True
            assert context["config"]["provider"]["model"] == "gpt-5.4"
