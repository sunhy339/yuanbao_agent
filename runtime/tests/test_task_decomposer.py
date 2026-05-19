from __future__ import annotations

import json
from typing import Any

import pytest

from local_agent_runtime.planner.decomposer import TaskDecomposer
from local_agent_runtime.planner.types import Subtask, looks_like_shell_command, normalize_subtask_verification_requirements


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class MockProvider:
    """Minimal provider stub that returns canned ``generate()`` responses."""

    def __init__(self, *, response: str | None = None) -> None:
        self._response = response or "[]"
        self.contexts: list[dict[str, Any]] = []

    def generate(self, prompt: str, context: dict[str, Any]) -> dict[str, Any]:
        self.contexts.append(context)
        return {"message": self._response, "prompt": prompt}


class FailingProvider:
    def generate(self, prompt: str, context: dict[str, Any]) -> dict[str, Any]:
        raise TimeoutError("provider timed out")


# ---------------------------------------------------------------------------
# Decomposition tests
# ---------------------------------------------------------------------------


class TestTaskDecomposerDecompose:
    def test_parses_subtasks_from_json_array(self) -> None:
        raw = json.dumps([
            {"id": "sub-0", "title": "Step A", "description": "Do A", "dependencies": []},
            {"id": "sub-1", "title": "Step B", "description": "Do B", "dependencies": ["sub-0"]},
        ])
        provider = MockProvider(response=raw)
        decomposer = TaskDecomposer(provider)
        result = decomposer.decompose(goal="refactor module")
        assert len(result.subtasks) == 2
        assert result.subtasks[0].id == "sub-0"
        assert result.subtasks[1].dependencies == ["sub-0"]
        assert result.subtasks[0].agent_type == "worker"

    def test_parses_llm_selected_agent_types(self) -> None:
        raw = json.dumps([
            {
                "id": "sub-0",
                "title": "Plan data model",
                "description": "Assess schema risks.",
                "dependencies": [],
                "agentType": "planner",
            },
            {
                "id": "sub-1",
                "title": "Implement modules",
                "description": "Create backend files and tests.",
                "dependencies": ["sub-0"],
                "agentType": "worker",
            },
            {
                "id": "sub-2",
                "title": "Review result",
                "description": "Review produced changes.",
                "dependencies": ["sub-1"],
                "agentType": "reviewer",
            },
        ])
        provider = MockProvider(response=raw)
        decomposer = TaskDecomposer(provider)

        result = decomposer.decompose(goal="build a small full-stack app")

        assert [subtask.agent_type for subtask in result.subtasks] == ["planner", "worker", "reviewer"]
        prompt = provider.contexts[0]["messages"][0]["content"]
        assert '"agentType"' in prompt
        assert 'Use "worker" for implementation' in prompt
        assert "Preserve explicit artifact names" in prompt

    def test_parses_llm_structured_subtask_contract_fields(self) -> None:
        raw = json.dumps([
            {
                "id": "sub-0",
                "title": "Implement frontend slice",
                "description": "Build the static app files.",
                "dependencies": [],
                "agentType": "worker",
                "ownedScope": ["web/index.html", "web/app.js"],
                "expectedArtifacts": [
                    {"kind": "file", "path": "web/index.html"},
                    {"kind": "file", "path": "web/app.js"},
                ],
                "verificationRequirements": [
                    {"kind": "command", "command": "node --check web/app.js"},
                ],
            }
        ])
        provider = MockProvider(response=raw)
        decomposer = TaskDecomposer(provider)

        result = decomposer.decompose(goal="build the web UI")

        assert result.subtasks[0].owned_scope == ["web/index.html", "web/app.js"]
        assert result.subtasks[0].expected_artifacts == [
            {"kind": "file", "path": "web/index.html"},
            {"kind": "file", "path": "web/app.js"},
        ]
        assert result.subtasks[0].verification_requirements == [
            {"kind": "command", "command": "node --check web/app.js"},
        ]

    def test_parses_fenced_json_block(self) -> None:
        raw = 'Here is the plan:\n```json\n[\n  {"id": "sub-0", "title": "Analyze", "description": "Read files", "dependencies": []}\n]\n```\nDone.'
        provider = MockProvider(response=raw)
        decomposer = TaskDecomposer(provider)
        result = decomposer.decompose(goal="migrate database")
        assert len(result.subtasks) == 1
        assert result.subtasks[0].title == "Analyze"

    def test_fallback_to_single_task_on_invalid_json(self) -> None:
        raw = "I cannot decompose this task."
        provider = MockProvider(response=raw)
        decomposer = TaskDecomposer(provider)
        result = decomposer.decompose(goal="simple task")
        assert len(result.subtasks) == 1
        assert result.subtasks[0].id == "sub-0"
        assert result.subtasks[0].description == "simple task"

    def test_fallback_on_empty_array(self) -> None:
        provider = MockProvider(response="[]")
        decomposer = TaskDecomposer(provider)
        result = decomposer.decompose(goal="do stuff")
        assert len(result.subtasks) == 1  # fallback

    def test_provider_failure_falls_back_to_execution_plan(self) -> None:
        decomposer = TaskDecomposer(FailingProvider())

        result = decomposer.decompose(goal="build modules, write pytest tests, and verify py_compile")

        assert [subtask.agent_type for subtask in result.subtasks] == ["planner", "worker", "worker"]
        assert result.execution_order == ["sub-0", "sub-1", "sub-2"]
        assert result.dag == {"sub-0": [], "sub-1": ["sub-0"], "sub-2": ["sub-1"]}
        assert "LLM decomposition was unavailable" in result.subtasks[0].description

    def test_provider_failure_keeps_simple_read_task_single_step(self) -> None:
        decomposer = TaskDecomposer(FailingProvider())

        result = decomposer.decompose(goal="explain the current status")

        assert len(result.subtasks) == 1
        assert result.subtasks[0].description == "explain the current status"

    def test_subtasks_missing_id_get_generated(self) -> None:
        raw = json.dumps([
            {"title": "Step A", "description": "Do A", "dependencies": []},
            {"title": "Step B", "description": "Do B", "dependencies": []},
        ])
        provider = MockProvider(response=raw)
        decomposer = TaskDecomposer(provider)
        result = decomposer.decompose(goal="test")
        assert len(result.subtasks) == 2
        assert result.subtasks[0].id == "sub-0"
        assert result.subtasks[1].id == "sub-1"

    def test_context_included_in_prompt(self) -> None:
        captured: dict[str, Any] = {}

        def capture_generate(prompt: str, context: dict[str, Any]) -> dict[str, Any]:
            captured["prompt"] = prompt
            return {"message": json.dumps([{"id": "sub-0", "title": "t", "description": "d", "dependencies": []}])}

        provider = MockProvider()
        provider.generate = capture_generate  # type: ignore[assignment]
        decomposer = TaskDecomposer(provider)
        decomposer.decompose(goal="goal", context="extra info")
        assert "extra info" in captured["prompt"]

    def test_provider_context_is_forwarded(self) -> None:
        raw = json.dumps([{"id": "sub-0", "title": "t", "description": "d", "dependencies": []}])
        provider = MockProvider(response=raw)
        decomposer = TaskDecomposer(provider)

        decomposer.decompose(
            goal="goal",
            provider_context={"config": {"provider": {"timeout": 120}}},
        )

        assert provider.contexts[0]["config"]["provider"]["timeout"] == 120
        assert provider.contexts[0]["messages"][0]["role"] == "user"

    def test_decompose_returns_plan_result(self) -> None:
        raw = json.dumps([
            {"id": "s0", "title": "A", "description": "a", "dependencies": []},
            {"id": "s1", "title": "B", "description": "b", "dependencies": ["s0"]},
        ])
        provider = MockProvider(response=raw)
        decomposer = TaskDecomposer(provider)
        result = decomposer.decompose(goal="test")
        assert result.dag == {"s0": [], "s1": ["s0"]}
        assert result.execution_order == ["s0", "s1"]

    def test_preserves_llm_plan_without_hardcoded_fullstack_expansion(self) -> None:
        raw = json.dumps([
            {
                "id": "sub-0",
                "title": "Analyze codebase",
                "description": "Inspect the current project.",
                "dependencies": [],
                "agentType": "planner",
            },
            {
                "id": "sub-1",
                "title": "Implement changes",
                "description": "Implement backend, frontend, tests, and README changes.",
                "dependencies": ["sub-0"],
                "agentType": "worker",
            },
            {
                "id": "sub-2",
                "title": "Verify results",
                "description": "Run tests and verify everything.",
                "dependencies": ["sub-1"],
                "agentType": "worker",
            },
        ])
        provider = MockProvider(response=raw)
        decomposer = TaskDecomposer(provider)

        result = decomposer.decompose(
            goal=(
                "Build feedback_models.py, feedback_storage.py, feedback_api.py, "
                "feedback_analytics.py, feedback_import_export.py, index.html, app.js, "
                "styles.css, two pytest tests, README docs, py_compile, node --check, and SQLite."
            )
        )

        titles = [task.title for task in result.subtasks]
        assert titles == ["Analyze codebase", "Implement changes", "Verify results"]
        implement = next(task for task in result.subtasks if task.title == "Implement changes")
        verify = next(task for task in result.subtasks if task.title == "Verify results")
        assert {"kind": "file", "path": "feedback_models.py"} in implement.expected_artifacts
        assert {"kind": "file", "path": "index.html"} in implement.expected_artifacts
        assert {"kind": "command", "command": "python -m pytest tests", "family": "python"} not in verify.verification_requirements

    def test_goal_contract_assigns_explicit_frontend_artifacts_without_hardcoded_frontend_rule(self) -> None:
        raw = json.dumps([
            {
                "id": "sub-0",
                "title": "Implement static frontend",
                "description": "Build the frontend UI.",
                "dependencies": [],
                "agentType": "worker",
            }
        ])
        provider = MockProvider(response=raw)
        decomposer = TaskDecomposer(provider)

        result = decomposer.decompose(
            goal="Implement a static frontend with index.html, app.js, styles.css and run node --check app.js",
        )

        frontend = result.subtasks[0]
        assert frontend.owned_scope == ["index.html", "app.js", "styles.css"]
        assert frontend.expected_artifacts == [
            {"kind": "file", "path": "index.html"},
            {"kind": "file", "path": "app.js"},
            {"kind": "file", "path": "styles.css"},
        ]
        assert frontend.verification_requirements == [
            {"kind": "command", "command": "node --check app.js", "family": "javascript"},
        ]

    def test_goal_contract_adds_document_and_verify_when_explicit_requirements_are_unclaimed(self) -> None:
        raw = json.dumps([
            {
                "id": "sub-0",
                "title": "Implement backend",
                "description": "Build the Python backend files.",
                "dependencies": [],
                "agentType": "worker",
            },
            {
                "id": "sub-1",
                "title": "Implement static frontend",
                "description": "Build the static frontend UI.",
                "dependencies": ["sub-0"],
                "agentType": "worker",
            },
        ])
        provider = MockProvider(response=raw)
        decomposer = TaskDecomposer(provider)

        result = decomposer.decompose(
            goal=(
                "Build blog_models.py, blog_api.py, index.html, app.js, styles.css, update README.md, "
                "and run python -m pytest -q plus node --check app.js."
            ),
        )

        titles = [task.title for task in result.subtasks]
        assert "Document and verify deliverables" in titles
        verify = next(task for task in result.subtasks if task.title == "Document and verify deliverables")
        frontend = next(task for task in result.subtasks if task.title == "Implement static frontend")
        assert {"kind": "file", "path": "README.md"} in verify.expected_artifacts
        assert {"kind": "command", "command": "node --check app.js", "family": "javascript"} in frontend.verification_requirements
        assert verify.verification_requirements == []

    def test_goal_contract_routes_test_files_and_commands_to_test_worker(self) -> None:
        raw = json.dumps([
            {
                "id": "sub-0",
                "title": "Implement backend",
                "description": "Build the Python backend files.",
                "dependencies": [],
                "agentType": "worker",
            },
            {
                "id": "sub-1",
                "title": "Add pytest coverage for backend and API",
                "description": "Write backend tests.",
                "dependencies": ["sub-0"],
                "agentType": "worker",
            },
        ])
        provider = MockProvider(response=raw)
        decomposer = TaskDecomposer(provider)

        result = decomposer.decompose(
            goal="Build blog_service.py, tests/test_blog_api.py, and run python -m pytest -q.",
        )

        backend = next(task for task in result.subtasks if task.title == "Implement backend")
        tests_task = next(task for task in result.subtasks if task.title == "Add pytest coverage for backend and API")
        assert {"kind": "file", "path": "blog_service.py"} in backend.expected_artifacts
        assert {"kind": "file", "path": "tests/test_blog_api.py"} in tests_task.expected_artifacts
        assert {"kind": "command", "command": "python -m pytest -q", "family": "python"} in tests_task.verification_requirements

    def test_goal_contract_does_not_assign_artifacts_to_planner(self) -> None:
        raw = json.dumps([
            {
                "id": "sub-0",
                "title": "Analyze current architecture",
                "description": "Inspect the project before implementation.",
                "dependencies": [],
                "agentType": "planner",
            },
            {
                "id": "sub-1",
                "title": "Implement requested files",
                "description": "Create the requested source files.",
                "dependencies": ["sub-0"],
                "agentType": "worker",
            },
        ])
        provider = MockProvider(response=raw)
        decomposer = TaskDecomposer(provider)

        result = decomposer.decompose(goal="Create README.md and blog_api.py.")

        planner = next(task for task in result.subtasks if task.agent_type == "planner")
        worker = next(task for task in result.subtasks if task.agent_type == "worker")
        assert planner.expected_artifacts == []
        assert {"kind": "file", "path": "blog_api.py"} in worker.expected_artifacts

    def test_goal_contract_extracts_only_explicit_command_phrases(self) -> None:
        provider = MockProvider(response="[]")
        decomposer = TaskDecomposer(provider)

        commands = decomposer._extract_explicit_commands(
            "Write at least two pytest files. You must run `python -m pytest -q`, "
            "`python -m py_compile blog_models.py`, and `node --check app.js`."
        )

        assert commands == [
            "python -m pytest -q",
            "python -m py_compile blog_models.py",
            "node --check app.js",
        ]

    def test_goal_contract_adds_missing_frontend_and_test_subtasks_when_llm_omits_categories(self) -> None:
        raw = json.dumps([
            {
                "id": "sub-0",
                "title": "Review backend, data model, and API risks",
                "description": "Read-only backend review.",
                "dependencies": [],
                "agentType": "planner",
            },
            {
                "id": "sub-1",
                "title": "Implement Python backend blog system",
                "description": "Build the backend modules.",
                "dependencies": ["sub-0"],
                "agentType": "worker",
            },
        ])
        provider = MockProvider(response=raw)
        decomposer = TaskDecomposer(provider)

        result = decomposer.decompose(
            goal=(
                "Build blog_models.py, blog_api.py, index.html, app.js, styles.css, "
                "write at least two pytest files, and run python -m pytest -q plus node --check app.js."
            ),
        )

        titles = [task.title for task in result.subtasks]
        assert "Implement remaining frontend deliverables" in titles
        assert "Add remaining test coverage" in titles
        frontend = next(task for task in result.subtasks if task.title == "Implement remaining frontend deliverables")
        tests_task = next(task for task in result.subtasks if task.title == "Add remaining test coverage")
        assert "index.html" in frontend.description
        assert "python -m pytest -q" in tests_task.description

    def test_goal_contract_still_adds_frontend_and_test_subtasks_when_backend_worker_mentions_them_only_as_context(self) -> None:
        raw = json.dumps([
            {
                "id": "sub-0",
                "title": "Implement Python backend and README",
                "description": (
                    "Build the Python backend modules and README. Mention how the frontend will later use "
                    "index.html/app.js/styles.css and how pytest should validate tmp_path isolation."
                ),
                "dependencies": [],
                "agentType": "worker",
            },
        ])
        provider = MockProvider(response=raw)
        decomposer = TaskDecomposer(provider)

        result = decomposer.decompose(
            goal=(
                "Build blog_models.py, blog_api.py, index.html, app.js, styles.css, "
                "write at least two pytest files, and run python -m pytest -q plus node --check app.js."
            ),
        )

        titles = [task.title for task in result.subtasks]
        assert "Implement remaining frontend deliverables" in titles
        assert "Add remaining test coverage" in titles

    def test_goal_contract_keeps_frontend_node_check_off_tests_worker(self) -> None:
        raw = json.dumps([
            {
                "id": "sub-0",
                "title": "Implement Python backend blog system",
                "description": "Build the backend modules and API.",
                "dependencies": [],
                "agentType": "worker",
            },
            {
                "id": "sub-1",
                "title": "Write pytest coverage for backend and API",
                "description": "Add the backend pytest suite and verify behavior.",
                "dependencies": ["sub-0"],
                "agentType": "worker",
            },
            {
                "id": "sub-2",
                "title": "Implement frontend static app",
                "description": "Create index.html, app.js, and styles.css for the blog UI.",
                "dependencies": ["sub-0"],
                "agentType": "worker",
            },
        ])
        provider = MockProvider(response=raw)
        decomposer = TaskDecomposer(provider)

        result = decomposer.decompose(
            goal=(
                "Build blog_models.py, blog_storage.py, blog_service.py, blog_api.py, blog_server.py, "
                "index.html, app.js, styles.css, tests/test_blog_service.py, tests/test_blog_api.py, "
                "README.md, run python -m pytest -q, run python -m py_compile blog_models.py blog_storage.py "
                "blog_service.py blog_api.py blog_server.py, and run node --check app.js."
            ),
        )

        tests_task = next(task for task in result.subtasks if "pytest coverage" in task.title.casefold())
        frontend_task = next(task for task in result.subtasks if "frontend" in task.title.casefold())

        assert {"kind": "command", "command": "python -m pytest -q", "family": "python"} in tests_task.verification_requirements
        assert {"kind": "command", "command": "node --check app.js", "family": "javascript"} not in tests_task.verification_requirements
        assert {"kind": "command", "command": "node --check app.js", "family": "javascript"} in frontend_task.verification_requirements

    def test_normalize_verification_requirements_filters_non_command_sentences(self) -> None:
        requirements = normalize_subtask_verification_requirements([
            {"kind": "command", "command": "pytest tmp_path databases remain isolated. Implement a static frontend with index.html"},
            {"kind": "command", "command": "python -m pytest -q"},
            {"kind": "command", "command": "`node --check app.js`"},
            {"kind": "note", "message": "keep anchors in README"},
        ])

        assert requirements == [
            {"kind": "command", "command": "python -m pytest -q"},
            {"kind": "command", "command": "node --check app.js"},
            {"kind": "note", "message": "keep anchors in README"},
        ]

    def test_looks_like_shell_command_rejects_natural_language_python_and_pytest_phrases(self) -> None:
        assert looks_like_shell_command("python -m pytest -q")
        assert looks_like_shell_command("python -m py_compile blog_models.py")
        assert not looks_like_shell_command("Python backend with close equivalents of models")
        assert not looks_like_shell_command("pytest tmp_path databases remain isolated")
        assert not looks_like_shell_command("python -m py_compile for the backend Python modules")

    def test_goal_contract_extracts_commands_without_swallowing_following_sentences(self) -> None:
        provider = MockProvider(response="[]")
        decomposer = TaskDecomposer(provider)

        commands = decomposer._extract_explicit_commands(
            "Update README.md with architecture. "
            "You must run python -m pytest -q. "
            "Implement a static frontend with index.html, app.js, and styles.css. "
            "Then run node --check app.js."
        )

        assert commands == [
            "python -m pytest -q",
            "node --check app.js",
        ]


# ---------------------------------------------------------------------------
# DAG builder tests
# ---------------------------------------------------------------------------


class TestBuildDAG:
    def test_builds_adjacency_list(self) -> None:
        subtasks = [
            Subtask(id="a", title="A", description="a", dependencies=[]),
            Subtask(id="b", title="B", description="b", dependencies=["a"]),
            Subtask(id="c", title="C", description="c", dependencies=["a", "b"]),
        ]
        decomposer = TaskDecomposer(MockProvider())
        dag = decomposer.build_dag(subtasks)
        assert dag == {"a": [], "b": ["a"], "c": ["a", "b"]}

    def test_empty_dependencies(self) -> None:
        subtasks = [
            Subtask(id="x", title="X", description="x", dependencies=[]),
            Subtask(id="y", title="Y", description="y", dependencies=[]),
        ]
        decomposer = TaskDecomposer(MockProvider())
        dag = decomposer.build_dag(subtasks)
        assert dag == {"x": [], "y": []}


# ---------------------------------------------------------------------------
# Topological sort tests
# ---------------------------------------------------------------------------


class TestTopologicalSort:
    def test_linear_chain(self) -> None:
        decomposer = TaskDecomposer(MockProvider())
        dag = {"a": [], "b": ["a"], "c": ["b"]}
        order = decomposer.topological_sort(dag, ["a", "b", "c"])
        assert order.index("a") < order.index("b") < order.index("c")

    def test_independent_tasks(self) -> None:
        decomposer = TaskDecomposer(MockProvider())
        dag = {"a": [], "b": [], "c": []}
        order = decomposer.topological_sort(dag, ["a", "b", "c"])
        assert set(order) == {"a", "b", "c"}

    def test_diamond_dependency(self) -> None:
        # a → b, a → c, b → d, c → d
        decomposer = TaskDecomposer(MockProvider())
        dag = {"a": [], "b": ["a"], "c": ["a"], "d": ["b", "c"]}
        order = decomposer.topological_sort(dag, ["a", "b", "c", "d"])
        assert order.index("a") < order.index("b")
        assert order.index("a") < order.index("c")
        assert order.index("b") < order.index("d")
        assert order.index("c") < order.index("d")

    def test_detects_cycle(self) -> None:
        decomposer = TaskDecomposer(MockProvider())
        dag = {"a": ["b"], "b": ["a"]}
        with pytest.raises(ValueError, match="cycle"):
            decomposer.topological_sort(dag, ["a", "b"])

    def test_self_cycle(self) -> None:
        decomposer = TaskDecomposer(MockProvider())
        dag = {"a": ["a"]}
        with pytest.raises(ValueError, match="cycle"):
            decomposer.topological_sort(dag, ["a"])

    def test_three_node_cycle(self) -> None:
        decomposer = TaskDecomposer(MockProvider())
        dag = {"a": ["c"], "b": ["a"], "c": ["b"]}
        with pytest.raises(ValueError, match="cycle"):
            decomposer.topological_sort(dag, ["a", "b", "c"])

    def test_unknown_dependency_ignored(self) -> None:
        """Dependencies referencing non-existent IDs are ignored."""
        decomposer = TaskDecomposer(MockProvider())
        dag = {"a": ["nonexistent"]}
        order = decomposer.topological_sort(dag, ["a"])
        assert order == ["a"]
