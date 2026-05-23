"""Tests for ContextBuilder skill integration."""

from __future__ import annotations

import pytest

from local_agent_runtime.context.builder import ContextBuilder
from local_agent_runtime.skills.registry import SkillRegistry
from local_agent_runtime.store.sqlite_store import SQLiteStore


@pytest.fixture
def store(tmp_path) -> SQLiteStore:
    db = SQLiteStore(str(tmp_path / "test.sqlite3"))
    yield db
    db.close()


@pytest.fixture
def workspace(store: SQLiteStore, tmp_path) -> dict:
    ws = store.upsert_workspace(path=str(tmp_path))
    return ws


@pytest.fixture
def session(store: SQLiteStore, workspace: dict) -> dict:
    return store.create_session(workspace_id=workspace["id"], title="test")


@pytest.fixture
def skill_registry(store: SQLiteStore) -> SkillRegistry:
    return SkillRegistry(store)


@pytest.fixture
def builder_with_skills(store: SQLiteStore, skill_registry: SkillRegistry) -> ContextBuilder:
    return ContextBuilder(store, skill_registry=skill_registry)


@pytest.fixture
def builder_without_skills(store: SQLiteStore) -> ContextBuilder:
    return ContextBuilder(store, skill_registry=None)


class TestContextBuilderSkillSystemPrompt:
    def test_skill_replaces_system_prompt(
        self, store: SQLiteStore, session: dict, builder_with_skills: ContextBuilder
    ) -> None:
        context = builder_with_skills.build(
            session_id=session["id"],
            goal="review this code",
            skill_id="code_reviewer",
        )
        messages = context["messages"]
        system_msg = next(m for m in messages if m["role"] == "system")
        # Should contain the code reviewer skill prompt, not the default
        assert "代码审计专家" in system_msg["content"] or "资深代码审计专家" in system_msg["content"]
        assert "Workspace root:" in system_msg["content"]  # safety boundaries preserved

    def test_no_skill_uses_default_prompt(
        self, store: SQLiteStore, session: dict, builder_with_skills: ContextBuilder
    ) -> None:
        context = builder_with_skills.build(
            session_id=session["id"],
            goal="help me code",
            skill_id=None,
        )
        messages = context["messages"]
        system_msg = next(m for m in messages if m["role"] == "system")
        assert "local coding agent" in system_msg["content"]

    def test_invalid_skill_id_falls_back_to_default(
        self, store: SQLiteStore, session: dict, builder_with_skills: ContextBuilder
    ) -> None:
        context = builder_with_skills.build(
            session_id=session["id"],
            goal="help me",
            skill_id="nonexistent_skill",
        )
        messages = context["messages"]
        system_msg = next(m for m in messages if m["role"] == "system")
        assert "local coding agent" in system_msg["content"]
        assert context["skillPolicy"] is None
        assert context["skillFallback"] == {
            "requestedSkillId": "nonexistent_skill",
            "reason": "skill_not_found",
            "fallback": "default_prompt_and_tools",
            "status": "active",
        }
        assert context["snapshot_metadata"]["skillFallback"]["reason"] == "skill_not_found"


class TestContextBuilderSkillToolFilter:
    def test_skill_filters_tools(
        self, store: SQLiteStore, session: dict, builder_with_skills: ContextBuilder
    ) -> None:
        context = builder_with_skills.build(
            session_id=session["id"],
            goal="review this code",
            skill_id="code_reviewer",
        )
        tool_names = {t["name"] for t in context["tools"]}
        # code_reviewer whitelist: read_file, search_files, code_search, git_diff, git_status
        assert "read_file" in tool_names
        assert "search_files" in tool_names
        assert "code_search" in tool_names
        assert "git_diff" in tool_names
        assert "git_status" in tool_names
        # These should be filtered out
        assert "run_command" not in tool_names
        assert "apply_patch" not in tool_names
        assert "write_file" not in tool_names

    def test_no_skill_returns_all_tools(
        self, store: SQLiteStore, session: dict, builder_with_skills: ContextBuilder
    ) -> None:
        context = builder_with_skills.build(
            session_id=session["id"],
            goal="help me code",
            skill_id=None,
        )
        tool_names = {t["name"] for t in context["tools"]}
        assert "run_command" in tool_names
        assert "apply_patch" in tool_names
        assert "write_file" in tool_names
        assert "read_file" in tool_names

    def test_openai_tools_match_filtered_tools(
        self, store: SQLiteStore, session: dict, builder_with_skills: ContextBuilder
    ) -> None:
        context = builder_with_skills.build(
            session_id=session["id"],
            goal="review this code",
            skill_id="code_reviewer",
        )
        tool_names = {t["name"] for t in context["tools"]}
        openai_names = {t["function"]["name"] for t in context["openai_tools"]}
        assert tool_names == openai_names


class TestContextBuilderSkillDebug:
    def test_debugger_skill_has_run_command(
        self, store: SQLiteStore, session: dict, builder_with_skills: ContextBuilder
    ) -> None:
        context = builder_with_skills.build(
            session_id=session["id"],
            goal="debug this error",
            skill_id="debugger",
        )
        tool_names = {t["name"] for t in context["tools"]}
        assert "run_command" in tool_names
        assert "read_file" in tool_names
        assert "write_file" in tool_names
        assert "apply_patch" in tool_names

    def test_debugger_system_prompt(
        self, store: SQLiteStore, session: dict, builder_with_skills: ContextBuilder
    ) -> None:
        context = builder_with_skills.build(
            session_id=session["id"],
            goal="debug this error",
            skill_id="debugger",
        )
        messages = context["messages"]
        system_msg = next(m for m in messages if m["role"] == "system")
        assert "调试专家" in system_msg["content"]


class TestContextBuilderNoSkillRegistry:
    def test_works_without_skill_registry(
        self, store: SQLiteStore, session: dict, builder_without_skills: ContextBuilder
    ) -> None:
        context = builder_without_skills.build(
            session_id=session["id"],
            goal="help me",
            skill_id="code_reviewer",
        )
        # Falls back to default - skill_id is ignored
        messages = context["messages"]
        system_msg = next(m for m in messages if m["role"] == "system")
        assert "local coding agent" in system_msg["content"]
        # All tools present
        tool_names = {t["name"] for t in context["tools"]}
        assert "run_command" in tool_names
