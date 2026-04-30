from __future__ import annotations

import json
import re
from typing import Any

from ..provider.adapter import ProviderAdapter
from .types import PlanResult, Subtask

_DECOMPOSITION_PROMPT = """\
You are a task decomposition specialist. Break the following goal into concrete, ordered sub-tasks.

**Goal**: {goal}

{context_section}

Respond with a JSON array of sub-tasks. Each sub-task MUST have:
- "id": a unique identifier like "sub-0", "sub-1", etc.
- "title": a short title (under 80 characters)
- "description": a detailed description of what to do (this will be used as the prompt for a sub-agent)
- "dependencies": an array of sub-task IDs that must complete before this one can start (use [] for tasks with no dependencies)

Guidelines:
- Each sub-task should be independently executable
- Use dependencies to express ordering constraints
- Keep the number of sub-tasks between 2 and 8
- Make descriptions specific and actionable

Example response:
```json
[
  {{"id": "sub-0", "title": "Analyze codebase", "description": "Search and analyze the relevant source files...", "dependencies": []}},
  {{"id": "sub-1", "title": "Implement changes", "description": "Apply the required modifications...", "dependencies": ["sub-0"]}},
  {{"id": "sub-2", "title": "Verify results", "description": "Run tests and verify...", "dependencies": ["sub-1"]}}
]
```
"""


class TaskDecomposer:
    """Decompose a complex goal into a DAG of sub-tasks via LLM."""

    def __init__(self, provider: ProviderAdapter) -> None:
        self._provider = provider

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def decompose(self, goal: str, context: str = "") -> PlanResult:
        """Call the LLM to decompose *goal* into sub-tasks, build DAG, and sort."""
        prompt = self._build_prompt(goal, context)
        response = self._provider.generate(
            prompt,
            {"messages": [{"role": "user", "content": prompt}]},
        )
        raw_text = response.get("message") or ""
        subtasks = self._parse_subtasks(raw_text, fallback_goal=goal)
        dag = self.build_dag(subtasks)
        all_ids = [s.id for s in subtasks]
        execution_order = self.topological_sort(dag, all_ids)
        return PlanResult(subtasks=subtasks, dag=dag, execution_order=execution_order)

    def build_dag(self, subtasks: list[Subtask]) -> dict[str, list[str]]:
        """Build an adjacency list {id: [dependency IDs]} from subtask dependencies."""
        return {s.id: list(s.dependencies) for s in subtasks}

    def topological_sort(
        self,
        dag: dict[str, list[str]],
        all_ids: list[str],
    ) -> list[str]:
        """Kahn's algorithm topological sort.

        Returns a valid execution order. Raises ``ValueError`` if a cycle
        is detected.
        """
        in_degree: dict[str, int] = {nid: 0 for nid in all_ids}
        # Reverse adjacency: node -> nodes that depend on it
        dependents: dict[str, list[str]] = {nid: [] for nid in all_ids}

        for node, deps in dag.items():
            for dep in deps:
                if dep not in in_degree:
                    continue  # ignore unknown dependency
                dependents[dep].append(node)
                in_degree[node] += 1

        queue = [nid for nid in all_ids if in_degree[nid] == 0]
        order: list[str] = []

        while queue:
            node = queue.pop(0)
            order.append(node)
            for dependent in dependents.get(node, []):
                in_degree[dependent] -= 1
                if in_degree[dependent] == 0:
                    queue.append(dependent)

        if len(order) != len(all_ids):
            raise ValueError(
                f"Dependency cycle detected among subtasks: "
                f"sorted {len(order)}/{len(all_ids)} nodes"
            )

        return order

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _build_prompt(self, goal: str, context: str) -> str:
        context_section = ""
        if context:
            context_section = f"**Additional context**:\n{context[:1500]}"
        return _DECOMPOSITION_PROMPT.format(
            goal=goal,
            context_section=context_section,
        )

    def _parse_subtasks(self, response_text: str, *, fallback_goal: str) -> list[Subtask]:
        """Extract sub-tasks from LLM response.

        Tries in order:
        1. JSON inside a fenced code block.
        2. Raw JSON array anywhere in the text.
        3. Fallback: single sub-task equal to the original goal.
        """
        # 1. Fenced code block
        fence_match = re.search(
            r"```(?:json)?\s*\n?(.*?)```", response_text, re.DOTALL,
        )
        if fence_match:
            parsed = self._try_parse_array(fence_match.group(1).strip())
            if parsed is not None:
                return parsed

        # 2. Raw JSON array
        parsed = self._try_parse_array(response_text)
        if parsed is not None:
            return parsed

        # 3. Fallback
        return [Subtask(id="sub-0", title=fallback_goal[:80], description=fallback_goal)]

    @staticmethod
    def _try_parse_array(text: str) -> list[Subtask] | None:
        """Attempt to find and parse a JSON array of sub-task objects."""
        start = text.find("[")
        if start == -1:
            return None

        # Find matching bracket
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "[":
                depth += 1
            elif text[i] == "]":
                depth -= 1
                if depth == 0:
                    try:
                        arr = json.loads(text[start : i + 1])
                    except (json.JSONDecodeError, ValueError):
                        return None
                    if not isinstance(arr, list) or len(arr) == 0:
                        return None
                    subtasks: list[Subtask] = []
                    for idx, item in enumerate(arr):
                        if not isinstance(item, dict):
                            continue
                        sub_id = str(item.get("id") or f"sub-{idx}")
                        title = str(item.get("title") or f"Sub-task {idx}")
                        desc = str(item.get("description") or title)
                        deps = item.get("dependencies", [])
                        if not isinstance(deps, list):
                            deps = []
                        deps = [str(d) for d in deps if isinstance(d, str)]
                        subtasks.append(Subtask(
                            id=sub_id, title=title,
                            description=desc, dependencies=deps,
                        ))
                    return subtasks if subtasks else None
        return None
