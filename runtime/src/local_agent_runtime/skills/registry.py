"""Skill registry: CRUD operations backed by SQLiteStore."""

from __future__ import annotations

from typing import Any

from ..store.sqlite_store import SQLiteStore
from .types import BUILTIN_SKILLS, SkillPreset


class SkillRegistry:
    """Manages skill presets with SQLite-backed persistence.

    Built-in skills are automatically upserted on initialization.
    Custom skills can be created, updated, and deleted at runtime.
    """

    def __init__(self, store: SQLiteStore) -> None:
        self._store = store
        self._load_builtin_skills()

    def _load_builtin_skills(self) -> None:
        """Upsert all built-in skills into the store."""
        for skill in BUILTIN_SKILLS:
            self._store.upsert_skill(
                skill.id,
                name=skill.name,
                description=skill.description,
                system_prompt=skill.system_prompt,
                tool_whitelist=skill.tool_whitelist,
                parameter_constraints=skill.parameter_constraints,
                category=skill.category,
                is_builtin=True,
            )

    def get(self, skill_id: str) -> SkillPreset | None:
        """Return a SkillPreset by id, or None if not found."""
        try:
            result = self._store.get_skill({"skillId": skill_id})
        except ValueError:
            return None
        return self._row_to_preset(result["skill"])

    def list_skills(self, category: str | None = None) -> list[SkillPreset]:
        """Return all skills, optionally filtered by category."""
        params: dict[str, Any] = {}
        if category is not None:
            params["category"] = category
        result = self._store.list_skills(params)
        return [self._row_to_preset(r) for r in result["skills"]]

    def create_custom_skill(self, params: dict[str, Any]) -> SkillPreset:
        """Create a new custom skill preset."""
        result = self._store.create_skill(params)
        return self._row_to_preset(result["skill"])

    def update_skill(self, skill_id: str, params: dict[str, Any]) -> SkillPreset:
        """Update an existing skill preset (built-in or custom)."""
        result = self._store.update_skill({"skillId": skill_id, **params})
        return self._row_to_preset(result["skill"])

    def delete_skill(self, skill_id: str) -> None:
        """Delete a custom skill. Built-in skills cannot be deleted."""
        self._store.delete_skill({"skillId": skill_id})

    @staticmethod
    def _row_to_preset(row: dict[str, Any]) -> SkillPreset:
        return SkillPreset(
            id=row["id"],
            name=row["name"],
            description=row.get("description", ""),
            system_prompt=row.get("system_prompt", ""),
            tool_whitelist=row.get("tool_whitelist", []),
            parameter_constraints=row.get("parameter_constraints", {}),
            category=row.get("category", "custom"),
            is_builtin=bool(row.get("is_builtin", False)),
            created_at=row.get("created_at", 0),
            updated_at=row.get("updated_at", 0),
        )
