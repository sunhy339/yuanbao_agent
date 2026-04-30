from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class ReflectionConfig:
    """Controls the reflection loop behaviour."""

    max_retries: int = 2
    confidence_threshold: float = 0.7
    enabled: bool = False
    evaluation_prompt: str = ""


@dataclass(slots=True)
class ReflectionIteration:
    """One evaluation pass inside the reflection loop."""

    iteration: int
    quality_score: float
    feedback: str
    accepted: bool


@dataclass(slots=True)
class ReflectionResult:
    """Aggregate result of the full reflection loop."""

    iterations: list[ReflectionIteration]
    accepted: bool
    final_score: float
    improved_summary: str | None = None
