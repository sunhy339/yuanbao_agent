"""Skill Flow Mixin â extracted from Orchestrator.

Handles skill CRUD, import, and usage recording.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class SkillFlowMixin:
    """Mixin providing skill management RPC methods."""

    # Skill presets
    # ------------------------------------------------------------------

    def skill_list(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._store.list_skills(params)

    def skill_create(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._store.create_skill(params)

    def skill_update(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._store.update_skill(params)

    def skill_delete(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._store.delete_skill(params)

    def skill_usage(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._store.list_skill_usage(params)

    def skill_import(self, params: dict[str, Any]) -> dict[str, Any]:
        """Import skills from a JSON file, folder, or .zip archive."""
        import json
        import zipfile
        import tempfile
        from pathlib import Path

        source = params.get("filePath", "")
        if not source:
            return {"imported": [], "skipped": [], "errors": [{"name": "", "error": "filePath is required"}]}

        source_path = Path(source)
        if not source_path.exists():
            return {"imported": [], "skipped": [], "errors": [{"name": "", "error": f"Path not found: {source}"}]}

        # Collect all .json files to import
        json_files: list[Path] = []

        if source_path.is_file():
            if source_path.suffix.lower() == ".zip":
                # Extract zip to temp dir and scan for .json
                try:
                    with zipfile.ZipFile(source_path, "r") as zf:
                        json_names = [n for n in zf.namelist() if n.lower().endswith(".json") and not n.startswith("__MACOSX")]
                        if not json_names:
                            return {"imported": [], "skipped": [], "errors": [{"name": "", "error": "ZIP 中未找到 .json 文件"}]}
                        tmp_dir = tempfile.mkdtemp(prefix="skill_import_")
                        zf.extractall(tmp_dir, members=json_names)
                        for name in json_names:
                            extracted = Path(tmp_dir) / name
                            if extracted.is_file():
                                json_files.append(extracted)
                except zipfile.BadZipFile as exc:
                    return {"imported": [], "skipped": [], "errors": [{"name": "", "error": f"无效的 ZIP 文件: {exc}"}]}
                except OSError as exc:
                    return {"imported": [], "skipped": [], "errors": [{"name": "", "error": str(exc)}]}
            else:
                # Single JSON file
                json_files.append(source_path)
        elif source_path.is_dir():
            # Recursively find .json files
            json_files = sorted(source_path.rglob("*.json"))
            if not json_files:
                return {"imported": [], "skipped": [], "errors": [{"name": "", "error": "文件夹中未找到 .json 文件"}]}
        else:
            return {"imported": [], "skipped": [], "errors": [{"name": "", "error": f"不支持的路径类型: {source}"}]}

        imported: list[dict] = []
        skipped: list[str] = []
        errors: list[dict] = []

        for json_path in json_files:
            try:
                raw = json_path.read_text(encoding="utf-8")
            except OSError as exc:
                errors.append({"name": json_path.name, "error": str(exc)})
                continue

            try:
                data = json.loads(raw)
            except json.JSONDecodeError as exc:
                errors.append({"name": json_path.name, "error": f"Invalid JSON: {exc}"})
                continue

            # Normalize to list
            if isinstance(data, dict):
                items = [data]
            elif isinstance(data, list):
                items = data
            else:
                errors.append({"name": json_path.name, "error": "Expected JSON object or array"})
                continue

            for item in items:
                name = item.get("name", "")
                if not name:
                    errors.append({"name": "", "error": f"Missing required field: name (in {json_path.name})"})
                    continue

                create_params = {
                    "name": name,
                    "description": item.get("description"),
                    "system_prompt": item.get("system_prompt"),
                    "tool_whitelist": item.get("tool_whitelist"),
                    "parameter_constraints": item.get("parameter_constraints"),
                    "category": item.get("category"),
                }
                create_params = {k: v for k, v in create_params.items() if v is not None}

                try:
                    self._store.create_skill(create_params)
                    imported.append(create_params)
                except Exception:
                    skipped.append(name)

        return {"imported": imported, "skipped": skipped, "errors": errors}

    def _record_skill_usage(
        self, *, task_id: str, session_id: str, skill_id: str | None,
    ) -> None:
        if skill_id and hasattr(self._store, "record_skill_usage"):
            self._store.record_skill_usage(
                task_id=task_id, session_id=session_id, skill_id=skill_id,
            )
