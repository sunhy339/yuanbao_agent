"""Agent Profile Store Mixin — CRUD for agent_profiles table."""
from __future__ import annotations

import json
from typing import Any

VALID_ROLES = frozenset({"planner", "builder", "reviewer", "researcher", "custom"})
VALID_PERMISSION_MODES = frozenset({"ask", "plan", "auto", "skip"})


class AgentProfileStoreMixin:
    """SQLite CRUD for agent profiles."""

    def list_agent_profiles(self, params: dict[str, Any]) -> dict[str, Any]:
        enabled_only = params.get("enabledOnly", False)
        if enabled_only:
            rows = self._conn.execute(
                "SELECT * FROM agent_profiles WHERE enabled = 1 ORDER BY created_at DESC"
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM agent_profiles ORDER BY created_at DESC"
            ).fetchall()
        return {"agents": [self._serialize_agent_profile(dict(r)) for r in rows]}

    def get_agent_profile(self, params: dict[str, Any]) -> dict[str, Any]:
        agent_id = params.get("agentId") or params.get("agent_id")
        if not isinstance(agent_id, str) or not agent_id.strip():
            raise ValueError("agentId is required")
        row = self._conn.execute(
            "SELECT * FROM agent_profiles WHERE id = ?",
            (agent_id.strip(),),
        ).fetchone()
        if row is None:
            raise ValueError(f"Agent profile not found: {agent_id}")
        return {"agent": self._serialize_agent_profile(dict(row))}

    def create_agent_profile(self, params: dict[str, Any]) -> dict[str, Any]:
        agent_id = params.get("id") or params.get("agentId") or self.new_id("ap")
        if not isinstance(agent_id, str) or not agent_id.strip():
            raise ValueError("id is required")
        agent_id = agent_id.strip()

        name = params.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("name is required")

        role = params.get("role", "custom")
        if role not in VALID_ROLES:
            raise ValueError(f"Invalid role: {role}. Valid: {', '.join(sorted(VALID_ROLES))}")

        permission_mode = params.get("permissionMode") or params.get("permission_mode")
        if permission_mode and permission_mode not in VALID_PERMISSION_MODES:
            raise ValueError(f"Invalid permissionMode: {permission_mode}")

        now = self.now()
        self._conn.execute(
            """INSERT INTO agent_profiles
               (id, name, description, role, cwd, enabled, permission_mode,
                provider_profile_id, model, skill_ids_json, mcp_server_ids_json,
                tool_policy_json, system_prompt, is_builtin, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)""",
            (
                agent_id,
                name.strip(),
                params.get("description"),
                role,
                params.get("cwd"),
                1 if params.get("enabled", True) else 0,
                permission_mode,
                params.get("providerProfileId") or params.get("provider_profile_id"),
                params.get("model"),
                json.dumps(params.get("skillIds") or params.get("skill_ids") or [], ensure_ascii=False),
                json.dumps(params.get("mcpServerIds") or params.get("mcp_server_ids") or [], ensure_ascii=False),
                json.dumps(params.get("toolPolicy") or params.get("tool_policy"), ensure_ascii=False) if params.get("toolPolicy") or params.get("tool_policy") else None,
                params.get("systemPrompt") or params.get("system_prompt"),
                now,
                now,
            ),
        )
        self._conn.commit()
        return self.get_agent_profile({"agentId": agent_id})

    def update_agent_profile(self, params: dict[str, Any]) -> dict[str, Any]:
        agent_id = params.get("agentId") or params.get("agent_id")
        if not isinstance(agent_id, str) or not agent_id.strip():
            raise ValueError("agentId is required")
        agent_id = agent_id.strip()

        existing = self._conn.execute(
            "SELECT * FROM agent_profiles WHERE id = ?", (agent_id,)
        ).fetchone()
        if existing is None:
            raise ValueError(f"Agent profile not found: {agent_id}")

        updates: dict[str, Any] = {}
        for field in ("name", "description", "cwd", "model", "system_prompt"):
            if field in params:
                updates[field] = params[field]

        if "role" in params:
            role = params["role"]
            if role not in VALID_ROLES:
                raise ValueError(f"Invalid role: {role}")
            updates["role"] = role

        permission_mode = params.get("permissionMode") or params.get("permission_mode")
        if permission_mode is not None:
            if permission_mode and permission_mode not in VALID_PERMISSION_MODES:
                raise ValueError(f"Invalid permissionMode: {permission_mode}")
            updates["permission_mode"] = permission_mode

        if "enabled" in params:
            updates["enabled"] = 1 if params["enabled"] else 0

        if "providerProfileId" in params or "provider_profile_id" in params:
            updates["provider_profile_id"] = params.get("providerProfileId") or params.get("provider_profile_id")

        for json_key, param_keys in [
            ("skill_ids_json", ("skillIds", "skill_ids")),
            ("mcp_server_ids_json", ("mcpServerIds", "mcp_server_ids")),
        ]:
            for pk in param_keys:
                if pk in params:
                    updates[json_key] = json.dumps(params[pk] or [], ensure_ascii=False)
                    break

        if "toolPolicy" in params or "tool_policy" in params:
            tp = params.get("toolPolicy") or params.get("tool_policy")
            updates["tool_policy_json"] = json.dumps(tp, ensure_ascii=False) if tp else None

        if "systemPrompt" in params or "system_prompt" in params:
            updates["system_prompt"] = params.get("systemPrompt") or params.get("system_prompt")

        if not updates:
            return self.get_agent_profile({"agentId": agent_id})

        updates["updated_at"] = self.now()
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        self._conn.execute(
            f"UPDATE agent_profiles SET {set_clause} WHERE id = ?",
            (*updates.values(), agent_id),
        )
        self._conn.commit()
        return self.get_agent_profile({"agentId": agent_id})

    def delete_agent_profile(self, params: dict[str, Any]) -> dict[str, Any]:
        agent_id = params.get("agentId") or params.get("agent_id")
        if not isinstance(agent_id, str) or not agent_id.strip():
            raise ValueError("agentId is required")
        agent_id = agent_id.strip()

        existing = self._conn.execute(
            "SELECT * FROM agent_profiles WHERE id = ?", (agent_id,)
        ).fetchone()
        if existing is None:
            raise ValueError(f"Agent profile not found: {agent_id}")
        if dict(existing).get("is_builtin"):
            raise ValueError("Cannot delete built-in agent profiles")

        self._conn.execute("DELETE FROM agent_profiles WHERE id = ?", (agent_id,))
        self._conn.commit()
        return {"deleted": True, "agentId": agent_id}

    def _serialize_agent_profile(self, row: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {
            "id": row["id"],
            "name": row["name"],
            "description": row.get("description"),
            "role": row.get("role", "custom"),
            "enabled": bool(row.get("enabled", 1)),
            "isBuiltin": bool(row.get("is_builtin", 0)),
            "is_builtin": row.get("is_builtin", 0),
        }
        optional_fields = {
            "cwd": "cwd",
            "permission_mode": "permissionMode",
            "provider_profile_id": "providerProfileId",
            "model": "model",
            "system_prompt": "systemPrompt",
        }
        for db_key, ts_key in optional_fields.items():
            val = row.get(db_key)
            if val is not None:
                result[ts_key] = val

        for json_field, ts_key, default in [
            ("skill_ids_json", "skillIds", []),
            ("mcp_server_ids_json", "mcpServerIds", []),
        ]:
            raw = row.get(json_field)
            if isinstance(raw, str):
                try:
                    result[ts_key] = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    result[ts_key] = default
            else:
                result[ts_key] = default

        tool_policy_raw = row.get("tool_policy_json")
        if isinstance(tool_policy_raw, str):
            try:
                result["toolPolicy"] = json.loads(tool_policy_raw)
            except (json.JSONDecodeError, TypeError):
                pass

        for ts_key, db_key in [("createdAt", "created_at"), ("updatedAt", "updated_at")]:
            val = row.get(db_key)
            if val is not None:
                result[ts_key] = val
                result[db_key] = val

        return result
