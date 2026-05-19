from __future__ import annotations

from typing import Any

import pytest

from local_agent_runtime.orchestration.result_synthesizer import ResultSynthesizer


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class MockProvider:
    """Returns a fixed synthesis message."""

    def __init__(self, *, response: str = "Synthesized summary.") -> None:
        self._response = response
        self.calls: list[dict[str, Any]] = []

    def generate(self, prompt: str, context: dict[str, Any]) -> dict[str, Any]:
        self.calls.append({"prompt": prompt, "context": context})
        return {"message": self._response}


def _make_results(n: int) -> list[dict[str, Any]]:
    topics = [
        ("Search code", "Found relevant source files"),
        ("Run tests", "All unit tests passed"),
        ("Fix bugs", "Patched three issues"),
        ("Update docs", "Documentation refreshed"),
        ("Deploy changes", "Deployment succeeded"),
        ("Review PR", "Code review completed"),
        ("Benchmark", "Performance metrics gathered"),
        ("Cleanup", "Removed unused imports"),
    ]
    results = []
    for i in range(min(n, len(topics))):
        title, result = topics[i]
        results.append({"title": title, "result": result, "id": f"s{i}"})
    return results


# ---------------------------------------------------------------------------
# Concat mode
# ---------------------------------------------------------------------------


class TestConcatMode:
    def test_concat_basic(self) -> None:
        synth = ResultSynthesizer()
        results = [
            {"title": "Search code", "result": "Found relevant files"},
            {"title": "Run tests", "result": "All tests passed"},
        ]
        summary = synth.synthesize("goal", results, mode="concat")
        assert "Search code" in summary
        assert "Found relevant files" in summary
        assert "Run tests" in summary
        assert "All tests passed" in summary
        assert "2 sub-tasks" in summary

    def test_concat_empty(self) -> None:
        synth = ResultSynthesizer()
        assert "No sub-task results" in synth.synthesize("goal", [], mode="concat")

    def test_concat_single(self) -> None:
        synth = ResultSynthesizer()
        summary = synth.synthesize("goal", [{"title": "X", "result": "ok"}], mode="concat")
        assert "X" in summary


# ---------------------------------------------------------------------------
# Auto mode (threshold)
# ---------------------------------------------------------------------------


class TestAutoMode:
    def test_auto_uses_concat_for_few_results(self) -> None:
        synth = ResultSynthesizer()
        results = _make_results(2)
        summary = synth.synthesize("goal", results, mode="auto")
        # Should be concat (no LLM call)
        assert "sub-tasks" in summary

    def test_auto_uses_llm_for_many_results(self) -> None:
        provider = MockProvider(response="LLM summary")
        synth = ResultSynthesizer(provider=provider)
        results = _make_results(5)
        summary = synth.synthesize("goal", results, mode="auto")
        assert summary == "LLM summary"
        assert len(provider.calls) == 1

    def test_auto_falls_back_to_concat_without_provider(self) -> None:
        synth = ResultSynthesizer(provider=None)
        results = _make_results(5)
        summary = synth.synthesize("goal", results, mode="auto")
        # No provider -> falls back to concat even for > 3
        assert "sub-tasks" in summary


# ---------------------------------------------------------------------------
# LLM mode
# ---------------------------------------------------------------------------


class TestLLMMode:
    def test_llm_synthesis(self) -> None:
        provider = MockProvider(response="Merged result")
        synth = ResultSynthesizer(provider=provider)
        results = [
            {"title": "A", "result": "Did A"},
            {"title": "B", "result": "Did B"},
        ]
        summary = synth.synthesize("goal", results, mode="llm")
        assert summary == "Merged result"

    def test_llm_fallback_on_error(self) -> None:
        class FailingProvider:
            def generate(self, prompt, context):
                raise RuntimeError("LLM down")

        synth = ResultSynthesizer(provider=FailingProvider())
        results = [{"title": "A", "result": "ok"}]
        summary = synth.synthesize("goal", results, mode="llm")
        # Falls back to concat
        assert "A" in summary

    def test_llm_synthesis_forwards_provider_context(self) -> None:
        provider = MockProvider(response="Merged result")
        synth = ResultSynthesizer(provider=provider)
        results = [
            {"title": "A", "result": "Did A"},
            {"title": "B", "result": "Did B"},
        ]

        summary = synth.synthesize(
            "goal",
            results,
            mode="llm",
            provider_context={"config": {"provider": {"streamingEnabled": True, "model": "gpt-5.4"}}},
        )

        assert summary == "Merged result"
        assert provider.calls[0]["context"]["config"]["provider"]["streamingEnabled"] is True
        assert provider.calls[0]["context"]["config"]["provider"]["model"] == "gpt-5.4"


# ---------------------------------------------------------------------------
# Dedup
# ---------------------------------------------------------------------------


class TestDedup:
    def test_dedup_removes_near_duplicates(self) -> None:
        synth = ResultSynthesizer()
        results = [
            {"title": "Search code", "result": "Found foo.py and bar.py"},
            {"title": "Search codebase", "result": "Found foo.py and bar.py"},
        ]
        deduped = synth.dedup(results)
        assert len(deduped) == 1

    def test_dedup_keeps_distinct(self) -> None:
        synth = ResultSynthesizer()
        results = [
            {"title": "Search code", "result": "Found foo.py"},
            {"title": "Run tests", "result": "All tests passed"},
        ]
        deduped = synth.dedup(results)
        assert len(deduped) == 2

    def test_dedup_single(self) -> None:
        synth = ResultSynthesizer()
        results = [{"title": "A", "result": "ok"}]
        assert len(synth.dedup(results)) == 1

    def test_dedup_empty(self) -> None:
        synth = ResultSynthesizer()
        assert synth.dedup([]) == []
