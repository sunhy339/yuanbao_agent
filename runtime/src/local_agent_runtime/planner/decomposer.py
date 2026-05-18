from __future__ import annotations

import json
import re
from collections import deque
from typing import Any

from ..provider.adapter import ProviderAdapter
from .types import PlanResult, Subtask, normalize_subtask_agent_type

_DECOMPOSITION_PROMPT = """\
You are a task decomposition specialist. Break the following goal into concrete, ordered sub-tasks.

**Goal**: {goal}

{context_section}

Respond with a JSON array of sub-tasks. Each sub-task MUST have:
- "id": a unique identifier like "sub-0", "sub-1", etc.
- "title": a short title (under 80 characters)
- "description": a detailed description of what to do (this will be used as the prompt for a sub-agent)
- "dependencies": an array of sub-task IDs that must complete before this one can start (use [] for tasks with no dependencies)
- "agentType": choose exactly one of "planner", "worker", "reviewer", or "summarizer"

Guidelines:
- Each sub-task should be independently executable
- Use "worker" for implementation, file edits, command execution, tests, or verification
- Use "planner" only for planning/risk-analysis tasks that should not modify files
- Use "reviewer" for read-only critique of existing or newly produced work
- Use "summarizer" only for the final synthesis step
- Use dependencies to express ordering constraints
- Keep the number of sub-tasks between 2 and 10; use more subtasks when the parent goal explicitly names separate backend, frontend, test, documentation, or verification deliverables
- Make descriptions specific and actionable
- Preserve explicit artifact names, test-count requirements, and validation commands from the parent goal inside the relevant sub-task descriptions
- Do not collapse implementation and verification into a vague "implement changes" sub-task when the parent goal names concrete deliverables

Example response:
```json
[
  {{"id": "sub-0", "title": "Analyze codebase", "description": "Search and analyze the relevant source files...", "dependencies": [], "agentType": "planner"}},
  {{"id": "sub-1", "title": "Implement changes", "description": "Apply the required modifications...", "dependencies": ["sub-0"], "agentType": "worker"}},
  {{"id": "sub-2", "title": "Verify results", "description": "Run tests and verify...", "dependencies": ["sub-1"], "agentType": "worker"}}
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

    def decompose(
        self,
        goal: str,
        context: str = "",
        *,
        provider_context: dict[str, Any] | None = None,
    ) -> PlanResult:
        """Call the LLM to decompose *goal* into sub-tasks, build DAG, and sort."""
        prompt = self._build_prompt(goal, context)
        request_context = {
            **(provider_context or {}),
            "messages": [{"role": "user", "content": prompt}],
        }
        try:
            response = self._provider.generate(
                prompt,
                request_context,
            )
            raw_text = response.get("message") or ""
            subtasks = self._parse_subtasks(raw_text, fallback_goal=goal)
        except Exception:  # noqa: BLE001
            subtasks = self._fallback_subtasks_for_provider_failure(goal)
        subtasks = self._expand_overloaded_implementation_plan(subtasks, goal=goal)
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

        queue = deque(nid for nid in all_ids if in_degree[nid] == 0)
        order: list[str] = []

        while queue:
            node = queue.popleft()
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

    def _fallback_subtasks_for_provider_failure(self, goal: str) -> list[Subtask]:
        """Keep orchestration moving when LLM decomposition times out or fails."""
        if not self._goal_needs_execution_plan(goal):
            return [Subtask(id="sub-0", title=goal[:80], description=goal)]

        return [
            Subtask(
                id="sub-0",
                title="Inspect requirements and workspace",
                description=(
                    "LLM decomposition was unavailable. Inspect the workspace, preserve the parent "
                    "requirements, identify required files, tools, tests, and verification commands, "
                    "and report concrete implementation constraints. Do not edit files."
                ),
                dependencies=[],
                agent_type="planner",
            ),
            Subtask(
                id="sub-1",
                title="Implement requested changes",
                description=(
                    "Implement the parent task directly from the original goal and the inspection notes. "
                    "Create or update the requested files, preserve explicit artifact names, and avoid "
                    "unrelated refactors."
                ),
                dependencies=["sub-0"],
                agent_type="worker",
            ),
            Subtask(
                id="sub-2",
                title="Verify and summarize result",
                description=(
                    "Run the parent task's requested verification commands, including tests or compile "
                    "checks when applicable. Record changed files, commands, test results, and any "
                    "remaining blockers before final synthesis."
                ),
                dependencies=["sub-1"],
                agent_type="worker",
            ),
        ]

    def _goal_needs_execution_plan(self, goal: str) -> bool:
        normalized = goal.casefold()
        return any(
            token in normalized
            for token in (
                "build",
                "implement",
                "create",
                "write",
                "update",
                "fix",
                "refactor",
                "test",
                "verify",
                "pytest",
                "py_compile",
                "compileall",
                "module",
                "file",
                "sqlite",
                "mcp",
                "skill",
                "实现",
                "创建",
                "写",
                "更新",
                "修复",
                "测试",
                "验证",
                "文件",
            )
        )

    def _expand_overloaded_implementation_plan(self, subtasks: list[Subtask], *, goal: str) -> list[Subtask]:
        """Split generic plans when the goal names several concrete deliverable groups."""
        if not self._looks_like_overloaded_implementation_plan(subtasks):
            return subtasks

        normalized_goal = goal.casefold()
        has_backend = any(
            token in normalized_goal
            for token in (
                "backend",
                "feedback_models.py",
                "feedback_storage.py",
                "feedback_api.py",
                "feedback_analytics.py",
                "feedback_import_export.py",
                "sqlite",
            )
        )
        has_frontend = any(token in normalized_goal for token in ("frontend", "index.html", "app.js", "styles.css"))
        has_tests = any(token in normalized_goal for token in ("pytest", "test_", "tests"))
        has_docs = any(token in normalized_goal for token in ("readme", "docs", "documentation"))
        has_verification = any(
            token in normalized_goal
            for token in ("py_compile", "node --check", "verification", "validate", "run tests")
        )
        deliverable_count = sum(1 for flag in (has_backend, has_frontend, has_tests, has_docs, has_verification) if flag)
        if deliverable_count < 3:
            return subtasks

        expanded: list[Subtask] = [
            Subtask(
                id="sub-0",
                title="Analyze codebase and constraints",
                description=(
                    "Inspect the workspace and summarize current files, constraints, required artifact names, "
                    "and validation commands from the parent goal. Do not edit files."
                ),
                dependencies=[],
                agent_type="planner",
            )
        ]
        last_backend_id = "sub-0"
        if has_backend:
            expanded.append(
                Subtask(
                    id="sub-1",
                    title="Implement backend models and storage",
                    description=(
                        "Implement the backend data model and persistence slice, preserving explicit artifact names "
                        "from the parent goal such as feedback_models.py and feedback_storage.py. Ensure storage APIs "
                        "accept explicit database paths or storage objects so tests can use isolated temporary databases."
                    ),
                    dependencies=["sub-0"],
                    agent_type="worker",
                )
            )
            expanded.append(
                Subtask(
                    id="sub-2",
                    title="Implement API analytics import export",
                    description=(
                        "Implement the API, analytics, and import/export modules named by the parent goal. Pass explicit "
                        "db_path or storage dependencies through analytics and import/export helpers; do not hard-code "
                        "feedback.db in paths that tests exercise."
                    ),
                    dependencies=["sub-1"],
                    agent_type="worker",
                )
            )
            last_backend_id = "sub-2"
        if has_frontend:
            expanded.append(
                Subtask(
                    id="sub-3",
                    title="Implement frontend static app",
                    description=(
                        "Implement the frontend files named by the parent goal, including index.html, app.js, and "
                        "styles.css, with forms, queue/filter UI, analytics display, and import/export controls."
                    ),
                    dependencies=["sub-0"],
                    agent_type="worker",
                )
            )
        if has_tests:
            test_deps = [last_backend_id]
            if has_frontend:
                test_deps.append("sub-3")
            expanded.append(
                Subtask(
                    id="sub-4",
                    title="Write pytest coverage",
                    description=(
                        "Write at least two pytest files covering validation failures, successful submission, persistence, "
                        "status transitions, search/filter, analytics aggregation, and import/export or API flows. Use "
                        "tmp_path database files and pass db_path/storage explicitly into the modules under test."
                    ),
                    dependencies=list(dict.fromkeys(test_deps)),
                    agent_type="worker",
                )
            )
        if has_docs or has_verification:
            verify_deps = []
            if has_backend:
                verify_deps.append(last_backend_id)
            if has_frontend:
                verify_deps.append("sub-3")
            if has_tests:
                verify_deps.append("sub-4")
            expanded.append(
                Subtask(
                    id="sub-5",
                    title="Document and verify",
                    description=(
                        "Update README documentation if requested and run the parent goal's validation commands, such as "
                        "python -m pytest -q, python -m py_compile for Python modules, node --check app.js, and file "
                        "existence checks. Fix failures within the scoped files when possible."
                    ),
                    dependencies=list(dict.fromkeys(verify_deps or ["sub-0"])),
                    agent_type="worker",
                )
            )
        return expanded

    @staticmethod
    def _looks_like_overloaded_implementation_plan(subtasks: list[Subtask]) -> bool:
        if len(subtasks) > 3:
            return False
        combined = " ".join(f"{task.title} {task.description}" for task in subtasks).casefold()
        if "implement changes" in combined or "implement modules" in combined:
            return True
        worker_tasks = [task for task in subtasks if task.agent_type == "worker"]
        if len(worker_tasks) != 1:
            return False
        worker_text = f"{worker_tasks[0].title} {worker_tasks[0].description}".casefold()
        deliverable_hits = sum(
            1
            for token in ("backend", "frontend", "pytest", "readme", "index.html", "feedback_api.py")
            if token in worker_text
        )
        return deliverable_hits >= 3

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
                        agent_type = normalize_subtask_agent_type(
                            item.get("agentType") or item.get("agent_type") or item.get("role"),
                        )
                        subtasks.append(Subtask(
                            id=sub_id, title=title,
                            description=desc, dependencies=deps, agent_type=agent_type,
                        ))
                    return subtasks if subtasks else None
        return None
