"""Skill tools — expose SkillRegistry as model-callable tools.

Two tools are provided:
- ``skill``: Execute a skill preset by id (inline execution within the current agent).
- ``discover_skills``: List available skill presets with descriptions.

These correspond to haha-cc's SkillTool and DiscoverSkillsTool.
"""

from __future__ import annotations

from typing import Any


def _text(value: Any, *, limit: int, default: str = "") -> str:
    text = str(value or "").strip()
    if not text:
        text = default
    return text[:limit]


def build_skill_tool(
    policy_guard: Any,
    store: Any,
    subagent_service: Any | None = None,
    *,
    skill_registry: Any | None = None,
    permission_engine: Any | None = None,
) -> dict[str, Any]:
    """Build the ``skill`` tool handler.

    Parameters
    ----------
    skill_registry :
        A :class:`SkillRegistry` instance.  When *None* the tool raises
        ``ValueError`` on every call.
    """

    def handler(params: dict[str, Any]) -> dict[str, Any]:
        if skill_registry is None:
            raise ValueError("skill tool is not configured (no SkillRegistry)")

        skill_id = _text(
            params.get("skillId")
            or params.get("skill_id")
            or params.get("skill")
            or params.get("name"),
            limit=120,
        )
        if not skill_id:
            raise ValueError("skillId is required")

        preset = skill_registry.get(skill_id)
        if preset is None:
            raise ValueError(f"Skill not found: {skill_id}")

        # Build execution context from the preset
        tool_whitelist = list(preset.tool_whitelist) if preset.tool_whitelist else []
        parameter_constraints = dict(preset.parameter_constraints) if preset.parameter_constraints else {}

        # Inline execution: return the skill configuration so the orchestrator
        # can apply it to the current agent context.
        mode = _text(
            params.get("mode") or params.get("executionMode"),
            limit=40,
            default="inline",
        )

        # If fork mode is requested and subagent_service is available,
        # delegate to a child agent with the skill's system prompt and tools.
        if mode == "fork" and subagent_service is not None:
            child_params = {
                "prompt": _text(
                    params.get("prompt") or params.get("input"),
                    limit=4000,
                    default=preset.system_prompt[:500],
                ),
                "description": _text(
                    params.get("description") or preset.name,
                    limit=240,
                ),
                "agentType": skill_id,
                "subagent_type": skill_id,
                "systemPrompt": preset.system_prompt,
                "childToolAllowlist": tool_whitelist,
                "parameterConstraints": parameter_constraints,
            }
            if permission_engine is not None:
                child_params["profile"] = {"source": "skill_tool", "skillId": skill_id}
            result = subagent_service.dispatch(child_params)
            return {
                "status": "forked",
                "toolName": "skill",
                "skillId": skill_id,
                "skillName": preset.name,
                "mode": "fork",
                "summary": _text(
                    result.get("summary") or result.get("resultSummary") or "Skill forked to child agent",
                    limit=600,
                ),
                "childTaskId": result.get("childTaskId") or result.get("taskId"),
            }

        # Inline mode: return skill metadata for the orchestrator to apply
        user_input = _text(
            params.get("prompt") or params.get("input") or params.get("query"),
            limit=4000,
        )

        return {
            "status": "inline",
            "toolName": "skill",
            "skillId": preset.id,
            "skillName": preset.name,
            "description": preset.description,
            "systemPrompt": preset.system_prompt,
            "toolWhitelist": tool_whitelist,
            "toolPolicy": preset.tool_policy.value if preset.tool_policy else "strict_whitelist",
            "parameterConstraints": parameter_constraints,
            "category": preset.category,
            "mode": "inline",
            **({"input": user_input} if user_input else {}),
        }

    return {"handler": handler}


def build_discover_skills_tool(
    skill_registry: Any | None = None,
    *_: Any,
    **__: Any,
) -> dict[str, Any]:
    """Build the ``discover_skills`` tool handler.

    Lists available skill presets, optionally filtered by category.
    """

    def handler(params: dict[str, Any]) -> dict[str, Any]:
        if skill_registry is None:
            raise ValueError("discover_skills tool is not configured (no SkillRegistry)")

        category = _text(params.get("category"), limit=80)
        skills = skill_registry.list_skills(category=category or None)

        items = []
        for skill in skills:
            item: dict[str, Any] = {
                "skillId": skill.id,
                "name": skill.name,
                "description": skill.description,
                "category": skill.category,
                "toolPolicy": skill.tool_policy.value if skill.tool_policy else "strict_whitelist",
                "isBuiltin": skill.is_builtin,
            }
            if skill.tool_whitelist:
                item["toolWhitelist"] = skill.tool_whitelist
            items.append(item)

        return {
            "status": "ok",
            "toolName": "discover_skills",
            "skills": items,
            "count": len(items),
        }

    return {"handler": handler}
