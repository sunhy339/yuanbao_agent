from __future__ import annotations

import json
import re
from collections import deque
from typing import Any

from ..provider.adapter import ProviderAdapter
from .types import (
    PlanResult,
    Subtask,
    normalize_subtask_agent_type,
    normalize_subtask_expected_artifacts,
    normalize_subtask_object_list,
    normalize_subtask_owned_scope,
    normalize_subtask_verification_requirements,
)

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
- "ownedScope": optional array of file or path scopes owned by this sub-task
- "expectedArtifacts": optional array describing concrete outputs this sub-task should produce
- "verificationRequirements": optional array describing the checks or evidence this sub-task should satisfy

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

_FILE_PATH_RE = re.compile(
    r"""(?:^|[\s`"'(])(?P<path>[\w./\\-]+\.(?:py|ts|tsx|js|jsx|json|yaml|yml|toml|md|rs|go|java|c|cpp|h|hpp|cs|rb|php|sh|bash|sql|html|css|scss|vue|svelte|graphql|proto))(?=[\s`"')\],;:.!?]|$)""",
    re.IGNORECASE,
)
_COMMAND_RE = re.compile(
    r"""(?P<command>(?:python(?:3)?|py|pytest|node|npm|pnpm|yarn|bun|go|cargo|dotnet|javac|java|tsc|mypy|ruff|eslint)\b(?:\s+(?!(?:and|plus|then)\b)[^,\n;]+)+)""",
    re.IGNORECASE,
)
_COMMAND_BLOCK_RE = re.compile(r"`([^`\n]+)`")
_COMMAND_TRIGGER_RE = re.compile(
    r"""(?ix)
    \b(?:run|execute|invoke|check|verify|using|use|command(?:s)?(?:\s+such\s+as)?|must\s+run)\b
    (?P<tail>[^\n]+)
    """
)
_COMMAND_SENTENCE_BREAK_RE = re.compile(r"[.!?](?:\s+|$)")


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
        subtasks = self._enforce_goal_contract(subtasks, goal=goal)
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

    def _enforce_goal_contract(self, subtasks: list[Subtask], *, goal: str) -> list[Subtask]:
        explicit_paths = self._extract_explicit_artifact_paths(goal)
        explicit_commands = self._extract_explicit_commands(goal)
        subtasks = self._ensure_missing_category_subtasks(
            subtasks,
            goal=goal,
            explicit_paths=explicit_paths,
            explicit_commands=explicit_commands,
        )
        claimed_paths: set[str] = set()
        claimed_commands: set[str] = set()
        for task in subtasks:
            relevant_paths = [path for path in explicit_paths if self._path_belongs_to_task(task, path)]
            if relevant_paths:
                self._merge_task_paths(task, relevant_paths)
                claimed_paths.update(path.casefold() for path in relevant_paths)
            relevant_commands = [
                command
                for command in explicit_commands
                if self._command_belongs_to_task(task, command, relevant_paths)
            ]
            if relevant_commands:
                self._merge_task_commands(task, relevant_commands)
                claimed_commands.update(command.casefold() for command in relevant_commands)
        for path in explicit_paths:
            key = path.casefold()
            if key in claimed_paths:
                continue
            target = self._best_task_for_path(subtasks, path)
            if target is None:
                continue
            self._merge_task_paths(target, [path])
            claimed_paths.add(key)
        for command in explicit_commands:
            key = command.casefold()
            if key in claimed_commands:
                continue
            target = self._best_task_for_command(subtasks, command)
            if target is None:
                continue
            self._merge_task_commands(target, [command])
            claimed_commands.add(key)
        unclaimed_paths = [path for path in explicit_paths if path.casefold() not in claimed_paths]
        unclaimed_commands = [command for command in explicit_commands if command.casefold() not in claimed_commands]
        if self._goal_needs_document_and_verify(unclaimed_paths, unclaimed_commands):
            subtasks.append(self._build_document_and_verify_subtask(subtasks, unclaimed_paths, unclaimed_commands))
        return subtasks

    def _extract_explicit_artifact_paths(self, text: str) -> list[str]:
        seen: set[str] = set()
        paths: list[str] = []
        for match in _FILE_PATH_RE.finditer(text):
            candidate = str(match.group("path") or "").strip().rstrip(".,:;!?").replace("\\", "/")
            if not candidate:
                continue
            key = candidate.casefold()
            if key in seen:
                continue
            seen.add(key)
            paths.append(candidate)
        return paths

    def _extract_explicit_commands(self, text: str) -> list[str]:
        seen: set[str] = set()
        commands: list[str] = []
        segments: list[str] = []
        segments.extend(match.group(1) for match in _COMMAND_BLOCK_RE.finditer(text))
        segments.extend(str(match.group("tail") or "") for match in _COMMAND_TRIGGER_RE.finditer(text))
        for segment in segments:
            clauses = [clause.strip() for clause in _COMMAND_SENTENCE_BREAK_RE.split(str(segment or "")) if clause.strip()]
            for clause in clauses:
                for subsegment in re.split(r"\b(?:and|plus|then)\b", clause, flags=re.IGNORECASE):
                    for match in _COMMAND_RE.finditer(subsegment):
                        command = " ".join(str(match.group("command") or "").split()).strip("`\"' \t.,:;!?")
                        if not command:
                            continue
                        key = command.casefold()
                        if key in seen:
                            continue
                        seen.add(key)
                        commands.append(command)
        return commands

    def _path_belongs_to_task(self, task: Subtask, path: str) -> bool:
        if normalize_subtask_agent_type(task.agent_type) != "worker":
            return False
        task_text = f"{task.title} {task.description}".casefold()
        normalized_path = path.casefold()
        basename = normalized_path.rsplit("/", 1)[-1]
        if normalized_path in task_text or basename in task_text:
            return True
        if self._task_is_docs(task):
            return basename == "readme.md" or normalized_path.startswith("docs/")
        if self._task_is_tests(task):
            return normalized_path.startswith("tests/") or basename.startswith("test_")
        if self._task_is_frontend(task):
            return any(normalized_path.endswith(ext) for ext in (".html", ".css", ".js", ".jsx", ".ts", ".tsx"))
        if self._task_is_backend(task):
            return normalized_path.endswith(".py") and not normalized_path.startswith("tests/") and basename != "readme.md"
        return False

    def _command_belongs_to_task(self, task: Subtask, command: str, candidate_paths: list[str]) -> bool:
        if normalize_subtask_agent_type(task.agent_type) != "worker":
            return False
        normalized = command.casefold()
        if candidate_paths and any(
            path.casefold() in normalized or path.casefold().rsplit("/", 1)[-1] in normalized
            for path in candidate_paths
        ):
            return True
        if self._task_is_tests(task):
            return any(token in normalized for token in ("pytest", "unittest", "tox")) and not any(
                token in normalized for token in ("node", "npm", "pnpm", "yarn", "bun", "eslint", "tsc")
            )
        if self._task_is_frontend(task):
            return any(token in normalized for token in ("node", "npm", "pnpm", "yarn", "bun", "eslint", "tsc"))
        if self._task_is_docs_or_verify(task) and not any(
            checker(task) for checker in (self._task_is_tests, self._task_is_frontend, self._task_is_backend)
        ):
            return True
        if self._task_is_backend(task):
            return any(token in normalized for token in ("py_compile", "compileall", "mypy", "ruff"))
        return False

    def _merge_task_paths(self, task: Subtask, paths: list[str]) -> None:
        existing_scope = {item.casefold() for item in task.owned_scope}
        existing_artifacts = {
            str(item.get("path") or item.get("file") or "").strip().casefold()
            for item in task.expected_artifacts
            if isinstance(item, dict)
        }
        for path in paths:
            if path.casefold() not in existing_scope:
                task.owned_scope.append(path)
                existing_scope.add(path.casefold())
            if path.casefold() not in existing_artifacts:
                task.expected_artifacts.append({"kind": "file", "path": path})
                existing_artifacts.add(path.casefold())

    def _merge_task_commands(self, task: Subtask, commands: list[str]) -> None:
        existing = {
            str(item.get("command") or item.get("name") or "").strip().casefold()
            for item in task.verification_requirements
            if isinstance(item, dict)
        }
        for command in commands:
            family = self._command_family(command)
            key = command.casefold()
            if key in existing:
                continue
            record: dict[str, Any] = {"kind": "command", "command": command}
            if family:
                record["family"] = family
            task.verification_requirements.append(record)
            existing.add(key)

    def _best_task_for_path(self, subtasks: list[Subtask], path: str) -> Subtask | None:
        best: tuple[int, int, Subtask] | None = None
        for index, task in enumerate(subtasks):
            score = self._score_task_for_path(task, path)
            if score <= 0:
                continue
            candidate = (score, -index, task)
            if best is None or candidate > best:
                best = candidate
        return best[2] if best is not None else None

    def _best_task_for_command(self, subtasks: list[Subtask], command: str) -> Subtask | None:
        best: tuple[int, int, Subtask] | None = None
        for index, task in enumerate(subtasks):
            score = self._score_task_for_command(task, command)
            if score <= 0:
                continue
            candidate = (score, -index, task)
            if best is None or candidate > best:
                best = candidate
        return best[2] if best is not None else None

    def _score_task_for_path(self, task: Subtask, path: str) -> int:
        if normalize_subtask_agent_type(task.agent_type) != "worker":
            return 0
        text = f"{task.title} {task.description}".casefold()
        normalized_path = path.casefold()
        basename = normalized_path.rsplit("/", 1)[-1]
        category = self._artifact_category_for_path(path)
        explicitly_named = normalized_path in text or basename in text
        score = 0
        if normalized_path in text:
            score += 100
        if basename in text:
            score += 80
        if category == "docs" and not explicitly_named and not self._task_is_docs(task):
            return 0
        if category != "docs" and explicitly_named:
            score += 20
        if category == "docs" and self._task_is_docs(task):
            score += 40
        elif category == "tests" and self._task_is_tests(task):
            score += 40
        elif category == "frontend" and self._task_is_frontend(task):
            score += 40
        elif category == "source" and self._task_is_backend(task):
            score += 30
        elif category == "javascript" and self._task_is_backend(task):
            score += 25
        elif category == "source" and self._task_is_generic_implementation(task):
            score += 20
        if score > 0 and any(token in text for token in ("implement", "build", "create", "write", "update", "fix")):
            score += 10
        if self._task_is_docs_or_verify(task) and category in {"docs", "tests"}:
            score += 10
        return score

    def _score_task_for_command(self, task: Subtask, command: str) -> int:
        if normalize_subtask_agent_type(task.agent_type) != "worker":
            return 0
        text = f"{task.title} {task.description}".casefold()
        normalized = command.casefold()
        family = self._command_family(command)
        score = 0
        if normalized in text:
            score += 100
        if family == "javascript" and self._task_is_frontend(task):
            score += 40
        if family == "python" and self._task_is_tests(task):
            score += 45
        elif family == "python" and self._task_is_backend(task) and "py_compile" in normalized:
            score += 35
        if "pytest" in normalized and self._task_is_tests(task):
            score += 25
        if self._task_is_docs_or_verify(task):
            score += 20
        if score > 0 and any(token in text for token in ("verify", "validation", "check", "tests", "coverage")):
            score += 10
        return score

    def _artifact_category_for_path(self, path: str) -> str:
        normalized = path.casefold().replace("\\", "/")
        basename = normalized.rsplit("/", 1)[-1]
        if normalized.startswith("tests/") or basename.startswith("test_"):
            return "tests"
        if basename in {"readme.md", "readme.rst", "readme.txt"} or normalized.startswith("docs/"):
            return "docs"
        if normalized.endswith((".md", ".rst", ".adoc", ".txt")):
            return "docs"
        if normalized.endswith((".html", ".css", ".scss", ".vue", ".svelte")):
            return "frontend"
        if normalized.endswith((".js", ".jsx", ".ts", ".tsx")):
            if any(token in basename for token in ("server", "api", "backend", "service", "worker")):
                return "javascript"
            return "frontend"
        if normalized.endswith((".py", ".go", ".rs", ".java", ".c", ".cpp", ".h", ".hpp", ".cs", ".rb", ".php", ".sql", ".graphql", ".proto")):
            return "source"
        return "other"

    def _goal_needs_document_and_verify(self, unclaimed_paths: list[str], unclaimed_commands: list[str]) -> bool:
        if unclaimed_commands:
            return True
        return any(self._artifact_category_for_path(path) == "docs" for path in unclaimed_paths)

    def _ensure_missing_category_subtasks(
        self,
        subtasks: list[Subtask],
        *,
        goal: str,
        explicit_paths: list[str],
        explicit_commands: list[str],
    ) -> list[Subtask]:
        if not subtasks:
            return subtasks
        worker_tasks = [task for task in subtasks if normalize_subtask_agent_type(task.agent_type) == "worker"]
        if not worker_tasks:
            return subtasks
        worker_ids = [task.id for task in worker_tasks]
        requested = self._requested_goal_categories(goal, explicit_paths, explicit_commands)
        covered = {category for category in requested if any(self._task_matches_category(task, category) for task in worker_tasks)}
        missing = requested - covered
        next_index = len(subtasks)
        for category in ("frontend", "tests"):
            if category not in missing:
                continue
            if any(self._task_explicitly_owns_category(task, category) for task in worker_tasks):
                continue
            title, description = self._category_subtask_template(category, explicit_paths, explicit_commands)
            dependencies = list(worker_ids)
            subtasks.append(
                Subtask(
                    id=f"sub-{next_index}",
                    title=title,
                    description=description,
                    dependencies=dependencies,
                    agent_type="worker",
                )
            )
            worker_ids.append(f"sub-{next_index}")
            next_index += 1
        return subtasks

    def _requested_goal_categories(self, goal: str, explicit_paths: list[str], explicit_commands: list[str]) -> set[str]:
        normalized_goal = goal.casefold()
        categories: set[str] = set()
        if any(self._artifact_category_for_path(path) == "frontend" for path in explicit_paths) or any(
            token in normalized_goal for token in ("frontend", "index.html", "app.js", "styles.css", "static ui", "static frontend")
        ):
            categories.add("frontend")
        if any(self._artifact_category_for_path(path) == "tests" for path in explicit_paths) or any(
            token in normalized_goal for token in ("pytest", "tests", "test coverage", "test files", "unittest")
        ):
            categories.add("tests")
        if any(self._artifact_category_for_path(path) == "docs" for path in explicit_paths) or any(
            token in normalized_goal for token in ("readme", "documentation", "docs")
        ):
            categories.add("docs")
        if any(command.casefold().startswith(("python ", "py ", "pytest", "node ", "npm ", "pnpm ", "yarn ", "bun ")) for command in explicit_commands):
            categories.add("verification")
        return categories

    def _task_matches_category(self, task: Subtask, category: str) -> bool:
        if normalize_subtask_agent_type(task.agent_type) != "worker":
            return False
        if category == "frontend":
            return self._task_contract_mentions_category(task, category)
        if category == "tests":
            return self._task_contract_mentions_category(task, category)
        if category == "docs":
            return self._task_contract_mentions_category(task, category)
        if category == "verification":
            return self._task_is_docs_or_verify(task) or bool(task.verification_requirements)
        return False

    def _task_contract_mentions_category(self, task: Subtask, category: str) -> bool:
        paths = list(task.owned_scope)
        paths.extend(
            str(item.get("path") or item.get("file") or "").strip()
            for item in task.expected_artifacts
            if isinstance(item, dict)
        )
        if category == "frontend":
            return any(self._artifact_category_for_path(path) == "frontend" for path in paths if path)
        if category == "tests":
            return any(self._artifact_category_for_path(path) == "tests" for path in paths if path) or any(
                "pytest" in str(item.get("command") or "").casefold()
                for item in task.verification_requirements
                if isinstance(item, dict)
            )
        if category == "docs":
            return any(self._artifact_category_for_path(path) == "docs" for path in paths if path)
        return False

    def _task_semantically_mentions_category(self, task: Subtask, category: str) -> bool:
        if category == "frontend":
            return self._task_is_frontend(task)
        if category == "tests":
            return self._task_is_tests(task)
        if category == "docs":
            return self._task_is_docs(task)
        return False

    def _task_explicitly_owns_category(self, task: Subtask, category: str) -> bool:
        if self._task_contract_mentions_category(task, category):
            return True
        text = f"{task.title} {task.description}".casefold()
        if category == "frontend":
            return self._matches_category_execution_intent(
                text,
                nouns=("frontend", "ui", "client", "browser"),
                artifacts=("index.html", "app.js", "styles.css"),
                action_terms=("implement", "build", "create", "add", "write", "update", "develop"),
            )
        if category == "tests":
            return self._matches_category_execution_intent(
                text,
                nouns=("pytest", "tests", "test coverage"),
                artifacts=("tests/", "test_", "pytest"),
                action_terms=("implement", "build", "create", "add", "write", "run", "cover"),
            )
        if category == "docs":
            return self._matches_category_execution_intent(
                text,
                nouns=("readme", "docs", "documentation"),
                artifacts=("readme.md",),
                action_terms=("write", "update", "document", "add", "create"),
            )
        return False

    def _matches_category_execution_intent(
        self,
        text: str,
        *,
        nouns: tuple[str, ...],
        artifacts: tuple[str, ...] = (),
        action_terms: tuple[str, ...] | None = None,
    ) -> bool:
        active_terms = action_terms or ("implement", "build", "create", "add", "write", "update", "develop", "run", "verify", "validate")
        fragments = [fragment for fragment in re.split(r"[.!?\n;]+", text) if fragment.strip()]
        for fragment in fragments:
            has_action = any(self._text_has_term(fragment, action) for action in active_terms)
            if not has_action:
                continue
            if any(self._text_has_term(fragment, noun) for noun in nouns):
                return True
            if any(artifact.casefold() in fragment for artifact in artifacts):
                return True
        return False

    def _category_subtask_template(
        self,
        category: str,
        explicit_paths: list[str],
        explicit_commands: list[str],
    ) -> tuple[str, str]:
        if category == "frontend":
            paths = [path for path in explicit_paths if self._artifact_category_for_path(path) == "frontend"]
            command_text = ", ".join(command for command in explicit_commands if self._command_family(command) == "javascript")
            description = (
                "Implement the remaining frontend/client deliverables named in the parent task. "
                f"Produce these files if requested: {', '.join(paths) if paths else 'the remaining frontend assets'}. "
                "Keep the UI static and dependency-light, aligned with the intended backend contract."
            )
            if command_text:
                description += f" Preserve the explicit frontend verification commands: {command_text}."
            return "Implement remaining frontend deliverables", description
        if category == "tests":
            command_text = ", ".join(command for command in explicit_commands if "pytest" in command.casefold())
            description = (
                "Add the remaining automated Python test coverage required by the parent task. "
                "Create the requested pytest files or equivalent focused tests, cover the named acceptance scenarios, "
                "and keep tmp_path or injected storage/database usage isolated."
            )
            if command_text:
                description += f" Preserve the explicit test verification commands: {command_text}."
            return "Add remaining test coverage", description
        return "Address remaining deliverables", "Address the remaining explicit deliverables from the parent task."

    def _build_document_and_verify_subtask(
        self,
        subtasks: list[Subtask],
        explicit_paths: list[str],
        explicit_commands: list[str],
    ) -> Subtask:
        next_id = f"sub-{len(subtasks)}"
        dependencies = [task.id for task in subtasks if normalize_subtask_agent_type(task.agent_type) == "worker"]
        task = Subtask(
            id=next_id,
            title="Document and verify deliverables",
            description=(
                "Handle any remaining documentation deliverables and run any explicit verification commands that were "
                "named in the parent task but not yet owned by another worker. Capture real command outcomes and file "
                "existence evidence for the final summary."
            ),
            dependencies=dependencies or [subtasks[-1].id] if subtasks else [],
            agent_type="worker",
        )
        relevant_paths = [path for path in explicit_paths if self._path_belongs_to_task(task, path)]
        if relevant_paths:
            self._merge_task_paths(task, relevant_paths)
        if explicit_commands:
            self._merge_task_commands(task, explicit_commands)
        return task

    def _task_is_frontend(self, task: Subtask) -> bool:
        text = f"{task.title} {task.description}".casefold()
        return self._matches_category_execution_intent(
            text,
            nouns=("frontend", "ui", "static app", "web", "browser", "client"),
            artifacts=("index.html", "app.js", "styles.css"),
            action_terms=("implement", "build", "create", "add", "write", "update", "develop"),
        )

    def _task_is_tests(self, task: Subtask) -> bool:
        text = f"{task.title} {task.description}".casefold()
        return self._matches_category_execution_intent(
            text,
            nouns=("pytest", "tests", "test coverage", "coverage"),
            artifacts=("tests/", "test_", "pytest"),
            action_terms=("implement", "build", "create", "add", "write", "run", "cover"),
        )

    def _task_is_docs(self, task: Subtask) -> bool:
        text = f"{task.title} {task.description}".casefold()
        return any(self._text_has_term(text, token) for token in ("readme", "docs", "documentation"))

    def _task_is_docs_or_verify(self, task: Subtask) -> bool:
        text = f"{task.title} {task.description}".casefold()
        return self._task_is_docs(task) or any(self._text_has_term(text, token) for token in ("verify", "verification", "validate"))

    def _task_is_backend(self, task: Subtask) -> bool:
        text = f"{task.title} {task.description}".casefold()
        return any(self._text_has_term(text, token) for token in ("backend", "python", "api", "server", "storage", "repository", "model"))

    def _task_is_generic_implementation(self, task: Subtask) -> bool:
        text = f"{task.title} {task.description}".casefold()
        return any(self._text_has_term(text, token) for token in ("implement", "implementation", "requested files", "source files", "create"))

    def _text_has_term(self, text: str, term: str) -> bool:
        escaped = re.escape(term.casefold())
        if any(ch.isalnum() for ch in term):
            return re.search(rf"(?<![a-z0-9_]){escaped}(?![a-z0-9_])", text) is not None
        return term.casefold() in text

    def _command_family(self, command: str) -> str | None:
        normalized = command.casefold()
        if any(token in normalized for token in ("node", "npm", "pnpm", "yarn", "bun", "eslint", "tsc")):
            return "javascript"
        if any(token in normalized for token in ("pytest", "py_compile", "compileall", "python", "mypy", "ruff")):
            return "python"
        return None

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
                        owned_scope = normalize_subtask_owned_scope(item.get("ownedScope") or item.get("owned_scope") or item.get("writeScope") or item.get("write_scope"))
                        expected_artifacts = normalize_subtask_expected_artifacts(item.get("expectedArtifacts") or item.get("expected_artifacts"))
                        verification_requirements = normalize_subtask_verification_requirements(item.get("verificationRequirements") or item.get("verification_requirements"))
                        subtasks.append(Subtask(
                            id=sub_id, title=title,
                            description=desc, dependencies=deps, agent_type=agent_type,
                            owned_scope=owned_scope,
                            expected_artifacts=expected_artifacts,
                            verification_requirements=verification_requirements,
                        ))
                    return subtasks if subtasks else None
        return None
