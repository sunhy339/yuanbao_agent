from __future__ import annotations

import json
import uuid
from typing import Any

class ExtensionStoreMixin:
    def get_skill(self, params: dict[str, Any]) -> dict[str, Any]:
        skill_id = params.get("skillId") or params.get("skill_id")
        if not isinstance(skill_id, str) or not skill_id.strip():
            raise ValueError("skillId is required")
        row = self._conn.execute(
            "SELECT * FROM skill_presets WHERE id = ?",
            (skill_id.strip(),),
        ).fetchone()
        if row is None:
            raise ValueError(f"Skill not found: {skill_id}")
        return {"skill": self._serialize_skill(dict(row))}

    def list_skills(self, params: dict[str, Any]) -> dict[str, Any]:
        category = params.get("category")
        if isinstance(category, str) and category.strip():
            rows = self._conn.execute(
                "SELECT * FROM skill_presets WHERE category = ? ORDER BY created_at DESC",
                (category.strip(),),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM skill_presets ORDER BY created_at DESC"
            ).fetchall()
        return {"skills": [self._serialize_skill(dict(r)) for r in rows]}

    def create_skill(self, params: dict[str, Any]) -> dict[str, Any]:
        skill_id = params.get("id") or params.get("skillId") or self.new_id("skill")
        if not isinstance(skill_id, str) or not skill_id.strip():
            raise ValueError("id is required")
        skill_id = skill_id.strip()
        name = params.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("name is required")
        description = params.get("description", "")
        system_prompt = params.get("system_prompt", "")
        tool_whitelist = params.get("tool_whitelist", [])
        parameter_constraints = params.get("parameter_constraints", {})
        category = params.get("category", "custom")
        tool_policy = params.get("tool_policy", "strict_whitelist")
        now = self.now()
        self._conn.execute(
            """
            INSERT INTO skill_presets (id, name, description, system_prompt, tool_whitelist,
                                        parameter_constraints, category, tool_policy, is_builtin, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
            """,
            (
                skill_id,
                name.strip(),
                description,
                system_prompt,
                json.dumps(tool_whitelist, ensure_ascii=False),
                json.dumps(parameter_constraints, ensure_ascii=False),
                category,
                tool_policy,
                now,
                now,
            ),
        )
        self._conn.commit()
        return self.get_skill({"skillId": skill_id})

    def update_skill(self, params: dict[str, Any]) -> dict[str, Any]:
        skill_id = params.get("skillId") or params.get("skill_id")
        if not isinstance(skill_id, str) or not skill_id.strip():
            raise ValueError("skillId is required")
        skill_id = skill_id.strip()
        existing = self._conn.execute(
            "SELECT * FROM skill_presets WHERE id = ?", (skill_id,)
        ).fetchone()
        if existing is None:
            raise ValueError(f"Skill not found: {skill_id}")
        existing = dict(existing)
        updates: dict[str, Any] = {}
        for field_name in ("name", "description", "system_prompt", "category"):
            if field_name in params:
                updates[field_name] = params[field_name]
        if "tool_whitelist" in params:
            updates["tool_whitelist"] = json.dumps(params["tool_whitelist"], ensure_ascii=False)
        if "parameter_constraints" in params:
            updates["parameter_constraints"] = json.dumps(params["parameter_constraints"], ensure_ascii=False)
        if "tool_policy" in params:
            updates["tool_policy"] = params["tool_policy"]
        if not updates:
            return {"skill": self._serialize_skill(existing)}
        updates["updated_at"] = self.now()
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        self._conn.execute(
            f"UPDATE skill_presets SET {set_clause} WHERE id = ?",
            (*updates.values(), skill_id),
        )
        self._conn.commit()
        return self.get_skill({"skillId": skill_id})

    def delete_skill(self, params: dict[str, Any]) -> dict[str, Any]:
        skill_id = params.get("skillId") or params.get("skill_id")
        if not isinstance(skill_id, str) or not skill_id.strip():
            raise ValueError("skillId is required")
        skill_id = skill_id.strip()
        existing = self._conn.execute(
            "SELECT * FROM skill_presets WHERE id = ?", (skill_id,)
        ).fetchone()
        if existing is None:
            raise ValueError(f"Skill not found: {skill_id}")
        if dict(existing).get("is_builtin"):
            raise ValueError("Cannot delete built-in skills")
        self._conn.execute("DELETE FROM skill_presets WHERE id = ?", (skill_id,))
        self._conn.commit()
        return {"deleted": True, "skillId": skill_id}

    def upsert_skill(self, skill_id: str, *, name: str, description: str,
                     system_prompt: str, tool_whitelist: list[str],
                     parameter_constraints: dict[str, Any], category: str,
                     tool_policy: str = "strict_whitelist",
                     is_builtin: bool = True) -> dict[str, Any]:
        """Upsert a skill preset (used for loading built-in skills)."""
        now = self.now()
        self._conn.execute(
            """
            INSERT INTO skill_presets (id, name, description, system_prompt, tool_whitelist,
                                        parameter_constraints, category, tool_policy, is_builtin, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name,
                description = excluded.description,
                system_prompt = excluded.system_prompt,
                tool_whitelist = excluded.tool_whitelist,
                parameter_constraints = excluded.parameter_constraints,
                category = excluded.category,
                tool_policy = excluded.tool_policy,
                updated_at = excluded.updated_at
            """,
            (
                skill_id,
                name,
                description,
                system_prompt,
                json.dumps(tool_whitelist, ensure_ascii=False),
                json.dumps(parameter_constraints, ensure_ascii=False),
                category,
                tool_policy,
                1 if is_builtin else 0,
                now,
                now,
            ),
        )
        self._conn.commit()
        row = self._conn.execute(
            "SELECT * FROM skill_presets WHERE id = ?", (skill_id,)
        ).fetchone()
        return self._serialize_skill(dict(row))

    def _serialize_skill(self, row: dict[str, Any]) -> dict[str, Any]:
        result = dict(row)
        for json_field in ("tool_whitelist", "parameter_constraints"):
            raw = result.get(json_field)
            if isinstance(raw, str):
                try:
                    result[json_field] = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    result[json_field] = [] if json_field == "tool_whitelist" else {}
        return result

    # ── skill_usage audit ────────────────────────────────────────────

    def record_skill_usage(
        self, *, task_id: str, session_id: str, skill_id: str, triggered_by: str = "routing",
    ) -> dict[str, Any]:
        usage_id = str(uuid.uuid4())
        now = self.now()
        self._conn.execute(
            "INSERT INTO skill_usage (id, task_id, session_id, skill_id, triggered_at, triggered_by) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (usage_id, task_id, session_id, skill_id, now, triggered_by),
        )
        self._conn.commit()
        return {
            "id": usage_id, "taskId": task_id, "sessionId": session_id,
            "skillId": skill_id, "triggeredAt": now, "triggeredBy": triggered_by,
        }

    def list_skill_usage(self, params: dict[str, Any]) -> dict[str, Any]:
        """Query skill usage history. Supports filtering by skillId, taskId, sessionId."""
        clauses: list[str] = []
        values: list[Any] = []
        for key, col in [("skillId", "skill_id"), ("taskId", "task_id"), ("sessionId", "session_id")]:
            val = params.get(key)
            if val is not None:
                clauses.append(f"{col} = ?")
                values.append(val)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        limit = min(int(params.get("limit", 100)), 500)
        offset = int(params.get("offset", 0))
        rows = self._conn.execute(
            f"SELECT * FROM skill_usage{where} ORDER BY triggered_at DESC LIMIT ? OFFSET ?",
            (*values, limit, offset),
        ).fetchall()
        total = self._conn.execute(
            f"SELECT COUNT(*) FROM skill_usage{where}", values,
        ).fetchone()[0]
        return {
            "usage": [dict(r) for r in rows],
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    # ── mcp_servers CRUD ────────────────────────────────────────────

    def get_mcp_server(self, params: dict[str, Any]) -> dict[str, Any]:
        server_id = params.get("serverId") or params.get("server_id")
        if not isinstance(server_id, str) or not server_id.strip():
            raise ValueError("serverId is required")
        row = self._conn.execute(
            "SELECT * FROM mcp_servers WHERE id = ?", (server_id.strip(),)
        ).fetchone()
        if row is None:
            raise ValueError(f"MCP server not found: {server_id}")
        return {"server": self._serialize_mcp_server(dict(row))}

    def list_mcp_servers(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        enabled_only = (params or {}).get("enabledOnly", False)
        if enabled_only:
            rows = self._conn.execute(
                "SELECT * FROM mcp_servers WHERE enabled = 1 ORDER BY created_at DESC"
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM mcp_servers ORDER BY created_at DESC"
            ).fetchall()
        return {"servers": [self._serialize_mcp_server(dict(r)) for r in rows]}

    def create_mcp_server(self, params: dict[str, Any]) -> dict[str, Any]:
        server_id = params.get("id") or params.get("serverId") or self.new_id("mcp")
        if not isinstance(server_id, str) or not server_id.strip():
            raise ValueError("id is required")
        server_id = server_id.strip()
        name = params.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("name is required")
        transport = params.get("transport", "stdio")
        command = params.get("command")
        args = params.get("args", [])
        url = params.get("url")
        headers = params.get("headers", {})
        env = params.get("env", {})
        enabled = 1 if params.get("enabled", True) else 0
        now = self.now()
        self._conn.execute(
            """
            INSERT INTO mcp_servers (id, name, transport, command, args, url, headers, env,
                                      enabled, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                server_id,
                name.strip(),
                transport,
                command,
                json.dumps(args, ensure_ascii=False),
                url,
                json.dumps(headers, ensure_ascii=False),
                json.dumps(env, ensure_ascii=False),
                enabled,
                now,
                now,
            ),
        )
        self._conn.commit()
        return self.get_mcp_server({"serverId": server_id})

    def update_mcp_server(self, params: dict[str, Any]) -> dict[str, Any]:
        server_id = params.get("serverId") or params.get("server_id")
        if not isinstance(server_id, str) or not server_id.strip():
            raise ValueError("serverId is required")
        server_id = server_id.strip()
        existing = self._conn.execute(
            "SELECT * FROM mcp_servers WHERE id = ?", (server_id,)
        ).fetchone()
        if existing is None:
            raise ValueError(f"MCP server not found: {server_id}")
        updates: dict[str, Any] = {}
        for field_name in ("name", "transport", "command", "url"):
            if field_name in params:
                updates[field_name] = params[field_name]
        if "args" in params:
            updates["args"] = json.dumps(params["args"], ensure_ascii=False)
        if "headers" in params:
            updates["headers"] = json.dumps(params["headers"], ensure_ascii=False)
        if "env" in params:
            updates["env"] = json.dumps(params["env"], ensure_ascii=False)
        if "enabled" in params:
            updates["enabled"] = 1 if params["enabled"] else 0
        if not updates:
            return {"server": self._serialize_mcp_server(dict(existing))}
        updates["updated_at"] = self.now()
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        self._conn.execute(
            f"UPDATE mcp_servers SET {set_clause} WHERE id = ?",
            (*updates.values(), server_id),
        )
        self._conn.commit()
        return self.get_mcp_server({"serverId": server_id})

    def delete_mcp_server(self, params: dict[str, Any]) -> dict[str, Any]:
        server_id = params.get("serverId") or params.get("server_id")
        if not isinstance(server_id, str) or not server_id.strip():
            raise ValueError("serverId is required")
        server_id = server_id.strip()
        existing = self._conn.execute(
            "SELECT * FROM mcp_servers WHERE id = ?", (server_id,)
        ).fetchone()
        if existing is None:
            raise ValueError(f"MCP server not found: {server_id}")
        self._conn.execute("DELETE FROM mcp_servers WHERE id = ?", (server_id,))
        self._conn.commit()
        return {"deleted": True, "serverId": server_id}

    def _serialize_mcp_server(self, row: dict[str, Any]) -> dict[str, Any]:
        result = dict(row)
        for json_field in ("args", "headers", "env"):
            raw = result.get(json_field)
            if isinstance(raw, str):
                try:
                    result[json_field] = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    result[json_field] = [] if json_field == "args" else {}
        return result

    def _ensure_collaboration_task_columns_original(self) -> None:
        pass

    def _ensure_collaboration_task_columns(self) -> None:
        columns = {
            row["name"]
            for row in self._conn.execute("PRAGMA table_info(collaboration_tasks)").fetchall()
        }
        if "error_json" not in columns:
            self._conn.execute("ALTER TABLE collaboration_tasks ADD COLUMN error_json TEXT")

        # --- trace_events visibility column ---
        trace_columns = {
            row["name"]
            for row in self._conn.execute("PRAGMA table_info(trace_events)").fetchall()
        }
        if "visibility" not in trace_columns:
            self._conn.execute("ALTER TABLE trace_events ADD COLUMN visibility TEXT NOT NULL DEFAULT 'chat'")

        # --- proposal_records model_id/turn_id columns ---
        proposal_columns = {
            row["name"]
            for row in self._conn.execute("PRAGMA table_info(proposal_records)").fetchall()
        }
        if proposal_columns and "model_id" not in proposal_columns:
            self._conn.execute("ALTER TABLE proposal_records ADD COLUMN model_id TEXT")
            self._conn.execute("ALTER TABLE proposal_records ADD COLUMN turn_id TEXT")

        # provider_turns: add turn_decision, thought_summary and recovery columns
        pt_columns = {
            row["name"]
            for row in self._conn.execute("PRAGMA table_info(provider_turns)").fetchall()
        }
        if pt_columns and "turn_decision" not in pt_columns:
            self._conn.execute("ALTER TABLE provider_turns ADD COLUMN turn_decision TEXT")
            self._conn.execute("ALTER TABLE provider_turns ADD COLUMN thought_summary TEXT")
        if pt_columns and "failure_recovery_json" not in pt_columns:
            self._conn.execute("ALTER TABLE provider_turns ADD COLUMN failure_recovery_json TEXT")

        self._conn.commit()

    # ------------------------------------------------------------------
    # ScopeConflictCheck
    # ------------------------------------------------------------------

    def _serialize_scope_conflict_check(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": row["id"],
            "taskId": row["task_id"],
            "sessionId": row.get("session_id"),
            "checkType": row["check_type"],
            "subtaskIds": json.loads(row.get("subtask_ids_json") or "[]"),
            "scopeMap": json.loads(row.get("scope_map_json") or "{}"),
            "overlaps": json.loads(row.get("overlaps_json") or "[]"),
            "resolution": row["resolution"],
            "serializedOrder": json.loads(row["serialized_order_json"]) if row.get("serialized_order_json") else None,
            "safe": bool(row["safe"]),
            "createdAt": row["created_at"],
        }

    def create_scope_conflict_check(self, params: dict[str, Any]) -> dict[str, Any]:
        task_id = self._require_non_empty(params, "taskId")
        check_type = self._require_non_empty(params, "checkType")
        now = self.now()
        check_id = self.new_id("scc")
        self._conn.execute(
            """INSERT INTO scope_conflict_checks
               (id, task_id, session_id, check_type, subtask_ids_json,
                scope_map_json, overlaps_json, resolution, serialized_order_json,
                safe, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (check_id, task_id,
             params.get("sessionId"), check_type,
             json.dumps(params.get("subtaskIds") or [], ensure_ascii=False),
             json.dumps(params.get("scopeMap") or {}, ensure_ascii=False),
             json.dumps(params.get("overlaps") or [], ensure_ascii=False),
             params.get("resolution", "none"),
             json.dumps(params["serializedOrder"], ensure_ascii=False) if params.get("serializedOrder") else None,
             1 if params.get("safe", True) else 0,
             now),
        )
        self._conn.commit()
        row = dict(self._conn.execute("SELECT * FROM scope_conflict_checks WHERE id = ?", (check_id,)).fetchone())
        return {"scopeConflictCheck": self._serialize_scope_conflict_check(row)}

    def list_scope_conflict_checks(self, params: dict[str, Any]) -> dict[str, Any]:
        filters: list[str] = []
        args: list[Any] = []
        if params.get("taskId"):
            filters.append("task_id = ?")
            args.append(params["taskId"])
        if params.get("checkType"):
            filters.append("check_type = ?")
            args.append(params["checkType"])
        limit = min(int(params.get("limit", 100)), 500)
        where = f" WHERE {' AND '.join(filters)}" if filters else ""
        rows = [dict(r) for r in self._conn.execute(
            f"SELECT * FROM scope_conflict_checks{where} ORDER BY created_at ASC LIMIT ?", args + [limit]
        ).fetchall()]
        return {"scopeConflictChecks": [self._serialize_scope_conflict_check(r) for r in rows]}

    def check_dispatch_scope(self, params: dict[str, Any]) -> dict[str, Any]:
        """Pre-dispatch scope overlap check for a set of subtasks.

        Returns {overlaps: [...], safe: bool, resolution: str, checkId: str}.
        Records the check in scope_conflict_checks table.
        """
        from ..policy.proposal_validator import validate_write_scope_overlap
        subtasks = params.get("subtasks", [])
        task_id = params.get("taskId", "")
        session_id = params.get("sessionId")
        overlaps = validate_write_scope_overlap(subtasks) if subtasks else []
        safe = len(overlaps) == 0
        resolution = "none" if safe else "serialized"
        serialized_order = None
        if not safe:
            serialized_order = [st.get("id") or st.get("taskId") or f"sub_{i}" for i, st in enumerate(subtasks)]

        check = self.create_scope_conflict_check({
            "taskId": task_id,
            "sessionId": session_id,
            "checkType": "pre_dispatch",
            "subtaskIds": serialized_order or [],
            "scopeMap": {
                (st.get("id") or st.get("taskId") or f"sub_{i}"): (st.get("ownedScope") or st.get("writeScope") or [])
                for i, st in enumerate(subtasks) if isinstance(st, dict)
            },
            "overlaps": overlaps,
            "resolution": resolution,
            "serializedOrder": serialized_order,
            "safe": safe,
        })
        return {
            "overlaps": overlaps,
            "safe": safe,
            "resolution": resolution,
            "serializedOrder": serialized_order,
            "checkId": check["scopeConflictCheck"]["id"],
        }

    # ------------------------------------------------------------------
    # ReplaySession
    # ------------------------------------------------------------------

    def _serialize_replay_session(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": row["id"],
            "sourceTaskId": row["source_task_id"],
            "mode": row["mode"],
            "configOverrides": json.loads(row.get("config_overrides_json") or "{}"),
            "status": row["status"],
            "timeline": json.loads(row["timeline_json"]) if row.get("timeline_json") else None,
            "warnings": json.loads(row.get("warnings_json") or "[]"),
            "gateEvaluations": json.loads(row["gate_evaluations_json"]) if row.get("gate_evaluations_json") else None,
            "summary": row.get("summary"),
            "errorSummary": row.get("error_summary"),
            "startedAt": row.get("started_at"),
            "completedAt": row.get("completed_at"),
            "createdAt": row["created_at"],
        }

    def create_replay_session(self, params: dict[str, Any]) -> dict[str, Any]:
        """Create a replay session record."""
        replay_id = self.new_id("replay")
        task_id = self._require_non_empty(params, "sourceTaskId")
        mode = params.get("mode", "audit")
        now_ms = self.now()

        config_overrides = params.get("configOverrides") or {}
        status = params.get("status", "pending")
        timeline = params.get("timeline")
        warnings = params.get("warnings") or []
        gate_evaluations = params.get("gateEvaluations")
        summary = params.get("summary")
        error_summary = params.get("errorSummary")
        started_at = params.get("startedAt")
        completed_at = params.get("completedAt")

        self._conn.execute(
            """INSERT INTO replay_sessions
               (id, source_task_id, mode, config_overrides_json, status,
                timeline_json, warnings_json, gate_evaluations_json,
                summary, error_summary, started_at, completed_at, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                replay_id, task_id, mode,
                json.dumps(config_overrides, ensure_ascii=False),
                status,
                json.dumps(timeline, ensure_ascii=False) if timeline is not None else None,
                json.dumps(warnings, ensure_ascii=False),
                json.dumps(gate_evaluations, ensure_ascii=False) if gate_evaluations is not None else None,
                summary, error_summary, started_at, completed_at, now_ms,
            ),
        )
        self._conn.commit()
        row = dict(self._conn.execute("SELECT * FROM replay_sessions WHERE id = ?", (replay_id,)).fetchone())
        return {"replaySession": self._serialize_replay_session(row)}

    def get_replay_session(self, params: dict[str, Any]) -> dict[str, Any]:
        """Get a single replay session by ID."""
        replay_id = self._require_non_empty(params, "replayId")
        row = self._conn.execute("SELECT * FROM replay_sessions WHERE id = ?", (replay_id,)).fetchone()
        if row is None:
            raise ValueError(f"Replay session not found: {replay_id}")
        return {"replaySession": self._serialize_replay_session(dict(row))}

    def list_replay_sessions(self, params: dict[str, Any]) -> dict[str, Any]:
        """List replay sessions, optionally filtered by sourceTaskId or mode."""
        conditions: list[str] = []
        args: list[Any] = []
        task_id = params.get("sourceTaskId") or params.get("taskId")
        mode = params.get("mode")
        if task_id:
            conditions.append("source_task_id = ?")
            args.append(task_id)
        if mode:
            conditions.append("mode = ?")
            args.append(mode)

        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        limit = min(int(params.get("limit") or 100), 500)
        rows = [
            dict(r) for r in self._conn.execute(
                f"SELECT * FROM replay_sessions{where} ORDER BY created_at DESC LIMIT ?", args + [limit],
            ).fetchall()
        ]
        return {"replaySessions": [self._serialize_replay_session(r) for r in rows]}

    def update_replay_session(self, params: dict[str, Any]) -> dict[str, Any]:
        """Update a replay session's mutable fields."""
        replay_id = self._require_non_empty(params, "replayId")
        updates: list[str] = []
        args: list[Any] = []

        for col, key in [
            ("status", "status"),
            ("timeline_json", "timeline"),
            ("warnings_json", "warnings"),
            ("gate_evaluations_json", "gateEvaluations"),
            ("summary", "summary"),
            ("error_summary", "errorSummary"),
            ("started_at", "startedAt"),
            ("completed_at", "completedAt"),
        ]:
            if key in params:
                val = params[key]
                if key in ("timeline", "gateEvaluations", "warnings"):
                    val = json.dumps(val, ensure_ascii=False)
                updates.append(f"{col} = ?")
                args.append(val)

        if not updates:
            raise ValueError("No fields to update")

        args.append(replay_id)
        self._conn.execute(
            f"UPDATE replay_sessions SET {', '.join(updates)} WHERE id = ?", args,
        )
        self._conn.commit()
        row = dict(self._conn.execute("SELECT * FROM replay_sessions WHERE id = ?", (replay_id,)).fetchone())
        return {"replaySession": self._serialize_replay_session(row)}

