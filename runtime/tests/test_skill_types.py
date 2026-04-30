"""Tests for SkillPreset types and BUILTIN_SKILLS definitions."""

from __future__ import annotations

from local_agent_runtime.skills.types import BUILTIN_SKILLS, SkillPreset


class TestSkillPreset:
    def test_create_skill_preset(self) -> None:
        skill = SkillPreset(
            id="test_skill",
            name="Test Skill",
            description="A test skill",
            system_prompt="You are a test assistant.",
            tool_whitelist=["read_file", "search_files"],
            parameter_constraints={"temperature": 0.5},
            category="testing",
        )
        assert skill.id == "test_skill"
        assert skill.name == "Test Skill"
        assert skill.is_builtin is True
        assert skill.created_at == 0

    def test_custom_skill_is_not_builtin(self) -> None:
        skill = SkillPreset(
            id="custom",
            name="Custom",
            description="Custom skill",
            system_prompt="prompt",
            tool_whitelist=[],
            parameter_constraints={},
            category="custom",
            is_builtin=False,
        )
        assert skill.is_builtin is False


class TestBuiltinSkills:
    def test_four_builtin_skills(self) -> None:
        assert len(BUILTIN_SKILLS) == 4

    def test_skill_ids_match_router_expectations(self) -> None:
        ids = {s.id for s in BUILTIN_SKILLS}
        assert ids == {"code_reviewer", "test_writer", "doc_writer", "debugger"}

    def test_each_skill_has_nonempty_tool_whitelist(self) -> None:
        for skill in BUILTIN_SKILLS:
            assert len(skill.tool_whitelist) > 0, f"Skill {skill.id} has empty tool_whitelist"

    def test_each_skill_has_system_prompt(self) -> None:
        for skill in BUILTIN_SKILLS:
            assert len(skill.system_prompt) > 50, f"Skill {skill.id} has short system_prompt"

    def test_each_skill_has_category(self) -> None:
        for skill in BUILTIN_SKILLS:
            assert skill.category in ("coding", "writing", "analysis"), f"Skill {skill.id} has unexpected category: {skill.category}"

    def test_tool_whitelist_contains_known_tools(self) -> None:
        """Verify whitelisted tool names reference actual built-in tools."""
        from local_agent_runtime.tools.registry import BUILTIN_TOOL_SCHEMAS
        known_tools = {s["name"] for s in BUILTIN_TOOL_SCHEMAS}
        for skill in BUILTIN_SKILLS:
            for tool_name in skill.tool_whitelist:
                assert tool_name in known_tools, f"Skill {skill.id} references unknown tool: {tool_name}"
