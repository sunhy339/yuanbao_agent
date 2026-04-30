"""Tests for SkillRegistry CRUD operations."""

from __future__ import annotations

import pytest

from local_agent_runtime.skills.registry import SkillRegistry
from local_agent_runtime.skills.types import BUILTIN_SKILLS
from local_agent_runtime.store.sqlite_store import SQLiteStore


@pytest.fixture
def store(tmp_path) -> SQLiteStore:
    db = SQLiteStore(str(tmp_path / "test.sqlite3"))
    yield db
    db.close()


@pytest.fixture
def registry(store: SQLiteStore) -> SkillRegistry:
    return SkillRegistry(store)


class TestSkillRegistryBuiltin:
    def test_builtin_skills_loaded(self, registry: SkillRegistry) -> None:
        for builtin in BUILTIN_SKILLS:
            skill = registry.get(builtin.id)
            assert skill is not None, f"Built-in skill {builtin.id} not found"
            assert skill.name == builtin.name
            assert skill.system_prompt == builtin.system_prompt
            assert set(skill.tool_whitelist) == set(builtin.tool_whitelist)

    def test_get_nonexistent_returns_none(self, registry: SkillRegistry) -> None:
        assert registry.get("nonexistent") is None

    def test_list_all_returns_builtins(self, registry: SkillRegistry) -> None:
        skills = registry.list_skills()
        ids = {s.id for s in skills}
        assert "code_reviewer" in ids
        assert "debugger" in ids
        assert "test_writer" in ids
        assert "doc_writer" in ids

    def test_list_filter_by_category(self, registry: SkillRegistry) -> None:
        coding_skills = registry.list_skills(category="coding")
        coding_ids = {s.id for s in coding_skills}
        assert "code_reviewer" in coding_ids
        assert "debugger" in coding_ids
        assert "test_writer" in coding_ids
        assert "doc_writer" not in coding_ids

        writing_skills = registry.list_skills(category="writing")
        writing_ids = {s.id for s in writing_skills}
        assert "doc_writer" in writing_ids
        assert "code_reviewer" not in writing_ids


class TestSkillRegistryCRUD:
    def test_create_custom_skill(self, registry: SkillRegistry) -> None:
        skill = registry.create_custom_skill({
            "id": "custom_reviewer",
            "name": "Custom Reviewer",
            "description": "A custom code reviewer",
            "system_prompt": "You review code.",
            "tool_whitelist": ["read_file"],
            "parameter_constraints": {"temperature": 0.2},
            "category": "custom",
        })
        assert skill.id == "custom_reviewer"
        assert skill.name == "Custom Reviewer"
        assert skill.is_builtin is False

    def test_create_and_get(self, registry: SkillRegistry) -> None:
        registry.create_custom_skill({
            "id": "my_skill",
            "name": "My Skill",
            "description": "desc",
            "system_prompt": "prompt",
            "tool_whitelist": ["read_file", "search_files"],
            "parameter_constraints": {},
            "category": "testing",
        })
        skill = registry.get("my_skill")
        assert skill is not None
        assert skill.name == "My Skill"

    def test_update_skill(self, registry: SkillRegistry) -> None:
        registry.create_custom_skill({
            "id": "to_update",
            "name": "Original",
            "description": "desc",
            "system_prompt": "prompt",
            "tool_whitelist": ["read_file"],
            "parameter_constraints": {},
            "category": "custom",
        })
        updated = registry.update_skill("to_update", {"name": "Updated", "description": "new desc"})
        assert updated.name == "Updated"
        assert updated.description == "new desc"

    def test_update_builtin_skill(self, registry: SkillRegistry) -> None:
        updated = registry.update_skill("code_reviewer", {"name": "Modified Name"})
        assert updated.name == "Modified Name"

    def test_delete_custom_skill(self, registry: SkillRegistry) -> None:
        registry.create_custom_skill({
            "id": "deletable",
            "name": "Deletable",
            "description": "desc",
            "system_prompt": "prompt",
            "tool_whitelist": [],
            "parameter_constraints": {},
            "category": "custom",
        })
        registry.delete_skill("deletable")
        assert registry.get("deletable") is None

    def test_delete_builtin_skill_raises(self, registry: SkillRegistry) -> None:
        with pytest.raises(ValueError, match="Cannot delete built-in"):
            registry.delete_skill("code_reviewer")

    def test_list_includes_custom_skills(self, registry: SkillRegistry) -> None:
        registry.create_custom_skill({
            "id": "custom_1",
            "name": "Custom 1",
            "description": "desc",
            "system_prompt": "prompt",
            "tool_whitelist": [],
            "parameter_constraints": {},
            "category": "custom",
        })
        skills = registry.list_skills()
        ids = {s.id for s in skills}
        assert "custom_1" in ids
        assert "code_reviewer" in ids  # builtins still present

    def test_custom_skill_appears_in_category_filter(self, registry: SkillRegistry) -> None:
        registry.create_custom_skill({
            "id": "my_testing",
            "name": "My Testing",
            "description": "desc",
            "system_prompt": "prompt",
            "tool_whitelist": [],
            "parameter_constraints": {},
            "category": "testing",
        })
        testing_skills = registry.list_skills(category="testing")
        ids = {s.id for s in testing_skills}
        assert "my_testing" in ids
        assert "code_reviewer" not in ids
