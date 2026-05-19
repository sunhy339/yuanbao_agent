from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

from ..provider.adapter import ProviderAdapter
from .types import ReflectionConfig, ReflectionIteration, ReflectionResult

_DEFAULT_EVALUATION_PROMPT = """\
You are a quality evaluator for an AI agent's output. Score the output against the goal.

**Goal**: {goal}

**Agent output**:
{output}

{context_section}

Respond with a JSON object:
```json
{{"score": <0.0-1.0>, "feedback": "<brief feedback>"}}
```

Scoring guidelines:
- 1.0: Fully addresses the goal, no issues
- 0.7-0.9: Mostly correct with minor gaps
- 0.4-0.6: Partially addresses the goal, significant gaps
- 0.0-0.3: Does not address the goal or has major errors
"""


class ReflectionEvaluator:
    """Quality assessment → feedback generation → re-generation loop."""

    def __init__(self, provider: ProviderAdapter, config: ReflectionConfig) -> None:
        self._provider = provider
        self._config = config

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def evaluate(
        self,
        *,
        goal: str,
        output: str,
        context: str = "",
        iteration: int = 0,
        provider_context: dict[str, Any] | None = None,
    ) -> ReflectionIteration:
        """Call the LLM to score *output* against *goal*."""
        prompt = self._build_evaluation_prompt(goal, output, context)
        response = self._provider.generate(
            prompt,
            {
                **(provider_context or {}),
                "messages": [{"role": "user", "content": prompt}],
            },
        )
        raw_text = response.get("message") or ""
        score, feedback = self._parse_evaluation(raw_text)
        return ReflectionIteration(
            iteration=iteration,
            quality_score=score,
            feedback=feedback,
            accepted=score >= self._config.confidence_threshold,
        )

    def reflect(
        self,
        *,
        goal: str,
        output: str,
        context: str = "",
        retry_fn: Callable[[str], str] | None = None,
        provider_context: dict[str, Any] | None = None,
    ) -> ReflectionResult:
        """Run the full reflection loop.

        1. ``evaluate()`` to get an initial score.
        2. If below threshold, optionally call *retry_fn(feedback)* to get
           an improved output and re-evaluate.
        3. Repeat until accepted or *max_retries* exhausted.
        """
        iterations: list[ReflectionIteration] = []
        current_output = output

        for i in range(self._config.max_retries + 1):
            iteration = self.evaluate(
                goal=goal, output=current_output, context=context, iteration=i, provider_context=provider_context,
            )
            iterations.append(iteration)

            if iteration.accepted:
                return ReflectionResult(
                    iterations=iterations,
                    accepted=True,
                    final_score=iteration.quality_score,
                    improved_summary=current_output,
                )

            # Not accepted — retry if we have a callback and retries left.
            if retry_fn is not None and i < self._config.max_retries:
                current_output = retry_fn(iteration.feedback)

        last = iterations[-1]
        return ReflectionResult(
            iterations=iterations,
            accepted=False,
            final_score=last.quality_score,
            improved_summary=current_output if retry_fn is not None else None,
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _build_evaluation_prompt(
        self, goal: str, output: str, context: str,
    ) -> str:
        template = self._config.evaluation_prompt or _DEFAULT_EVALUATION_PROMPT
        context_section = ""
        if context:
            context_section = f"**Context**:\n{context[:1500]}"
        return template.format(
            goal=goal,
            output=output,
            context_section=context_section,
        )

    def _parse_evaluation(
        self, response_text: str,
    ) -> tuple[float, str]:
        """Extract (score, feedback) from the LLM response.

        Tries in order:
        1. JSON inside a fenced code block.
        2. Raw JSON anywhere in the text.
        3. Fallback: extract first decimal number as score.
        """
        # 1. Fenced code block
        fence_match = re.search(
            r"```(?:json)?\s*\n?(.*?)```", response_text, re.DOTALL,
        )
        if fence_match:
            parsed = self._try_parse_json(fence_match.group(1).strip())
            if parsed is not None:
                return parsed

        # 2. Raw JSON
        parsed = self._try_parse_json(response_text)
        if parsed is not None:
            return parsed

        # 3. Fallback — extract first float that looks like a score.
        score = self._extract_score_fallback(response_text)
        return score, response_text.strip()[:500]

    @staticmethod
    def _try_parse_json(text: str) -> tuple[float, str] | None:
        """Attempt to find a JSON object with 'score' and 'feedback'."""
        # Find the first '{' … '}' pair.
        start = text.find("{")
        if start == -1:
            return None
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(text[start : i + 1])
                    except (json.JSONDecodeError, ValueError):
                        return None
                    if isinstance(obj, dict):
                        score = obj.get("score")
                        feedback = obj.get("feedback", "")
                        if isinstance(score, (int, float)):
                            return float(score), str(feedback)
                    return None
        return None

    @staticmethod
    def _extract_score_fallback(text: str) -> float:
        """Last resort: pull the first 0.x or 1.0 number from *text*."""
        match = re.search(r"\b([01](?:\.\d+)?)\b", text)
        if match:
            return float(match.group(1))
        return 0.0

    def to_dict(self, result: ReflectionResult) -> dict[str, Any]:
        """Serialise a *ReflectionResult* for storage."""
        return {
            "accepted": result.accepted,
            "finalScore": result.final_score,
            "iterations": [
                {
                    "iteration": it.iteration,
                    "qualityScore": it.quality_score,
                    "feedback": it.feedback,
                    "accepted": it.accepted,
                }
                for it in result.iterations
            ],
        }
