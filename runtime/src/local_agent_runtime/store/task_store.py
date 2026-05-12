from __future__ import annotations

import json
import re
import sqlite3
import time
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any

class TaskStoreMixin:
    def create_scheduled_task(self, params: dict[str, Any]) -> dict[str, Any]:
        name = self._require_non_empty(params, "name")
        prompt = self._require_non_empty(params, "prompt")
        schedule = self._require_non_empty(params, "schedule")
        enabled = bool(params.get("enabled", True))
        status = self._normalize_scheduled_status(params.get("status"), enabled)
        enabled = status == "active"
        now = self.now()
        task_id = self.new_id("sched")
        next_run_at = self._next_scheduled_run_at(schedule, now) if enabled else None

        self._conn.execute(
            """
            INSERT INTO scheduled_tasks (
                id, name, prompt, schedule, status, enabled, created_at, updated_at, last_run_at, next_run_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)
            """,
            (task_id, name, prompt, schedule, status, int(enabled), now, now, next_run_at),
        )
        self._conn.commit()
        return {"task": self.require_scheduled_task(task_id)}

    def list_scheduled_tasks(self, _params: dict[str, Any] | None = None) -> dict[str, Any]:
        rows = self._conn.execute(
            """
            SELECT *
            FROM scheduled_tasks
            ORDER BY created_at DESC
            """
        ).fetchall()
        return {"tasks": [self._serialize_scheduled_task(dict(row)) for row in rows]}

    def require_scheduled_task(self, task_id: str) -> dict[str, Any]:
        row = self._conn.execute(
            "SELECT * FROM scheduled_tasks WHERE id = ?",
            (task_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"Scheduled task not found: {task_id}")
        return self._serialize_scheduled_task(dict(row))

    def update_scheduled_task(self, params: dict[str, Any]) -> dict[str, Any]:
        task_id = self._require_non_empty(params, "taskId")
        current = self.require_scheduled_task(task_id)
        name = self._optional_non_empty(params, "name", current["name"])
        prompt = self._optional_non_empty(params, "prompt", current["prompt"])
        schedule = self._optional_non_empty(params, "schedule", current["schedule"])
        enabled = bool(params.get("enabled", current["enabled"]))
        status = self._normalize_scheduled_status(params.get("status"), enabled)
        enabled = status == "active"
        now = self.now()
        next_run_at = self._next_scheduled_run_at(schedule, now) if enabled else None

        self._conn.execute(
            """
            UPDATE scheduled_tasks
            SET name = ?, prompt = ?, schedule = ?, status = ?, enabled = ?, updated_at = ?, next_run_at = ?
            WHERE id = ?
            """,
            (name, prompt, schedule, status, int(enabled), now, next_run_at, task_id),
        )
        self._conn.commit()
        return {"task": self.require_scheduled_task(task_id)}

    def toggle_scheduled_task(self, params: dict[str, Any]) -> dict[str, Any]:
        task_id = self._require_non_empty(params, "taskId")
        enabled = bool(params.get("enabled", True))
        current = self.require_scheduled_task(task_id)
        return self.update_scheduled_task(
            {
                "taskId": task_id,
                "name": current["name"],
                "prompt": current["prompt"],
                "schedule": current["schedule"],
                "enabled": enabled,
            }
        )

    def create_scheduled_task_run(
        self,
        *,
        task_id: str,
        status: str,
        started_at: int,
        finished_at: int | None,
        summary: str | None,
        error: str | None = None,
    ) -> dict[str, Any]:
        task = self.require_scheduled_task(task_id)
        run_id = self.new_id("schedrun")
        self._conn.execute(
            """
            INSERT INTO scheduled_task_runs (
                id, task_id, status, started_at, finished_at, summary, error
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (run_id, task_id, status, started_at, finished_at, summary, error),
        )

        next_run_at = self._next_scheduled_run_at(task["schedule"], finished_at or started_at) if task["enabled"] else None
        self._conn.execute(
            """
            UPDATE scheduled_tasks
            SET last_run_at = ?, next_run_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (started_at, next_run_at, finished_at or started_at, task_id),
        )
        self._conn.commit()

        row = self._conn.execute("SELECT * FROM scheduled_task_runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            raise ValueError(f"Scheduled run not found: {run_id}")
        return self._serialize_scheduled_task_run(dict(row))

    def list_scheduled_task_runs(self, params: dict[str, Any]) -> dict[str, Any]:
        task_id = params.get("taskId")
        limit = int(params.get("limit", 100))
        limit = max(1, min(limit, 1000))
        if task_id:
            rows = self._conn.execute(
                """
                SELECT *
                FROM scheduled_task_runs
                WHERE task_id = ?
                ORDER BY started_at DESC
                LIMIT ?
                """,
                (task_id, limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                """
                SELECT *
                FROM scheduled_task_runs
                ORDER BY started_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return {"logs": [self._serialize_scheduled_task_run(dict(row)) for row in rows]}

    def create_task(
        self,
        session_id: str,
        task_type: str,
        goal: str,
        plan: list[dict[str, Any]],
        *,
        acceptance_criteria: list[str] | None = None,
        out_of_scope: list[str] | None = None,
        current_step: str | None = None,
        routing: dict[str, Any] | None = None,
        root_task_id: str | None = None,
        role: str | None = None,
        created_seq: int | None = None,
        status: str = "running",
    ) -> dict[str, Any]:
        task_id = self.new_id("task")
        now = self.now()
        current_step = current_step or self._current_step_from_plan(plan)
        valid_roles = {"root", "planner", "worker", "reviewer", "summarizer"}
        effective_role = role or "root"
        if effective_role not in valid_roles:
            raise ValueError(f"Invalid task role: {effective_role!r}. Must be one of {sorted(valid_roles)}")
        effective_root_task_id = root_task_id or task_id
        self._conn.execute(
            """
            INSERT INTO tasks (
                id, session_id, type, status, goal, acceptance_criteria_json, out_of_scope_json,
                current_step, plan_json, changed_files_json, commands_json, verification_json,
                reflection_json, routing_json, summary, result_json, error_code,
                created_at, updated_at, root_task_id, role, created_seq
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '[]', '[]', '[]', NULL, ?, NULL, NULL, NULL, ?, ?, ?, ?, ?)
            """,
            (
                task_id,
                session_id,
                task_type,
                status,
                goal,
                json.dumps(acceptance_criteria or [], ensure_ascii=False),
                json.dumps(out_of_scope or [], ensure_ascii=False),
                current_step,
                json.dumps(plan, ensure_ascii=False),
                json.dumps(routing, ensure_ascii=False) if routing else None,
                now,
                now,
                effective_root_task_id,
                effective_role,
                created_seq,
            ),
        )
        self._conn.commit()
        row = self._conn.execute(
            "SELECT * FROM tasks WHERE id = ?",
            (task_id,),
        ).fetchone()
        return self._serialize_task(dict(row))

    def update_task_status(self, task_id: str, status: str) -> dict[str, Any]:
        now = self.now()
        self._conn.execute(
            "UPDATE tasks SET status = ?, updated_at = ? WHERE id = ?",
            (status, now, task_id),
        )
        self._conn.commit()
        return self.get_task({"taskId": task_id})["task"]

    def update_task(
        self,
        task_id: str,
        *,
        status: str | None = None,
        plan: list[dict[str, Any]] | None = None,
        acceptance_criteria: list[str] | None = None,
        out_of_scope: list[str] | None = None,
        current_step: str | None = None,
        changed_files: list[dict[str, Any]] | None = None,
        commands: list[dict[str, Any]] | None = None,
        verification: list[dict[str, Any]] | None = None,
        reflection: dict[str, Any] | None = None,
        summary: str | None = None,
        result_summary: str | None = None,
        error_code: str | None = None,
        active_assistant_message_id: str | None = None,
        tests_run: list[dict[str, Any]] | None = None,
        risks: list[dict[str, Any]] | None = None,
        structured_result: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        assignments: list[str] = ["updated_at = ?"]
        values: list[Any] = [self.now()]

        if status is not None:
            assignments.append("status = ?")
            values.append(status)
        if plan is not None:
            assignments.append("plan_json = ?")
            values.append(json.dumps(plan, ensure_ascii=False))
            if current_step is None:
                current_step = self._current_step_from_plan(plan)
        if acceptance_criteria is not None:
            assignments.append("acceptance_criteria_json = ?")
            values.append(json.dumps(acceptance_criteria, ensure_ascii=False))
        if out_of_scope is not None:
            assignments.append("out_of_scope_json = ?")
            values.append(json.dumps(out_of_scope, ensure_ascii=False))
        if current_step is not None:
            assignments.append("current_step = ?")
            values.append(current_step)
        if changed_files is not None:
            assignments.append("changed_files_json = ?")
            values.append(json.dumps(changed_files, ensure_ascii=False))
        if commands is not None:
            assignments.append("commands_json = ?")
            values.append(json.dumps(commands, ensure_ascii=False))
        if verification is not None:
            assignments.append("verification_json = ?")
            values.append(json.dumps(verification, ensure_ascii=False))
        if reflection is not None:
            assignments.append("reflection_json = ?")
            values.append(json.dumps(reflection, ensure_ascii=False))
        if summary is not None:
            assignments.append("summary = ?")
            values.append(summary)
        if result_summary is not None:
            assignments.append("result_json = ?")
            values.append(result_summary)
        if error_code is not None:
            assignments.append("error_code = ?")
            values.append(error_code)
        if active_assistant_message_id is not None:
            assignments.append("active_assistant_message_id = ?")
            values.append(active_assistant_message_id)
        if tests_run is not None:
            assignments.append("tests_run_json = ?")
            values.append(json.dumps(tests_run, ensure_ascii=False))
        if risks is not None:
            assignments.append("risks_json = ?")
            values.append(json.dumps(risks, ensure_ascii=False))
        if structured_result is not None:
            assignments.append("structured_result_json = ?")
            values.append(json.dumps(structured_result, ensure_ascii=False))

        values.append(task_id)
        self._conn.execute(
            f"UPDATE tasks SET {', '.join(assignments)} WHERE id = ?",
            values,
        )
        self._conn.commit()
        return self.get_task({"taskId": task_id})["task"]

    def get_task(self, params: dict[str, Any]) -> dict[str, Any]:
        row = self._conn.execute(
            "SELECT * FROM tasks WHERE id = ?",
            (params["taskId"],),
        ).fetchone()
        if row is None:
            raise ValueError(f"Task not found: {params['taskId']}")
        return {"task": self._serialize_task(dict(row))}

    def list_tasks(self, params: dict[str, Any]) -> dict[str, Any]:
        session_id = params.get("sessionId") or params.get("session_id")
        if session_id:
            rows = self._conn.execute(
                "SELECT * FROM tasks WHERE session_id = ? ORDER BY updated_at DESC, created_at DESC",
                (session_id,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM tasks ORDER BY updated_at DESC, created_at DESC",
            ).fetchall()
        return {"tasks": [self._serialize_task(dict(row)) for row in rows]}

    def create_collaboration_task(self, params: dict[str, Any]) -> dict[str, Any]:
        title = self._require_non_empty(params, "title")
        description = params.get("description")
        if description is not None and not isinstance(description, str):
            raise ValueError("description must be a string")
        session_id = self._optional_string(params, "sessionId")
        parent_task_id = self._optional_string(params, "parentTaskId")
        assigned_worker_id = self._optional_string(params, "assignedWorkerId")
        dependencies = self._string_list(params.get("dependencies", []), "dependencies")
        priority = self._normalize_priority(params.get("priority", 3))
        metadata = self._dict_value(params.get("metadata", {}), "metadata")
        task_id = self.new_id("ctask")
        now = self.now()
        self._conn.execute(
            """
            INSERT INTO collaboration_tasks (
                id, session_id, parent_task_id, title, description, status, priority,
                assigned_worker_id, dependencies_json, result_json, error_json, metadata_json,
                claimed_at, completed_at, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, 'queued', ?, ?, ?, NULL, NULL, ?, NULL, NULL, ?, ?)
            """,
            (
                task_id,
                session_id,
                parent_task_id,
                title,
                description.strip() if isinstance(description, str) else None,
                priority,
                assigned_worker_id,
                json.dumps(dependencies, ensure_ascii=False),
                json.dumps(metadata, ensure_ascii=False, sort_keys=True),
                now,
                now,
            ),
        )
        self._conn.commit()
        return {"task": self.require_collaboration_task(task_id)}

    def complete_collaboration_task(self, params: dict[str, Any]) -> dict[str, Any]:
        task_id = self._require_non_empty(params, "taskId")
        worker_id = self._require_non_empty(params, "workerId")
        result = self._dict_value(params.get("result"), "result")
        return self._finalize_collaboration_task(
            task_id=task_id,
            worker_id=worker_id,
            task_status="completed",
            worker_status="idle",
            result=result,
            error=None,
        )

    def fail_collaboration_task(self, params: dict[str, Any]) -> dict[str, Any]:
        task_id = self._require_non_empty(params, "taskId")
        worker_id = self._require_non_empty(params, "workerId")
        if "error" not in params:
            raise ValueError("error is required")
        error = self._json_value(params.get("error"), "error")
        return self._finalize_collaboration_task(
            task_id=task_id,
            worker_id=worker_id,
            task_status="failed",
            worker_status="failed",
            result=None,
            error=error,
        )

    def require_collaboration_task(self, task_id: str) -> dict[str, Any]:
        row = self._conn.execute(
            "SELECT * FROM collaboration_tasks WHERE id = ?",
            (task_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"Collaboration task not found: {task_id}")
        return self._serialize_collaboration_task(dict(row))

    def get_collaboration_task(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"task": self.require_collaboration_task(self._require_non_empty(params, "taskId"))}

    def list_collaboration_tasks(self, params: dict[str, Any]) -> dict[str, Any]:
        clauses: list[str] = []
        values: list[Any] = []
        for param_name, column_name in (
            ("sessionId", "session_id"),
            ("parentTaskId", "parent_task_id"),
            ("assignedWorkerId", "assigned_worker_id"),
            ("status", "status"),
        ):
            value = self._optional_string(params, param_name)
            if value is not None:
                clauses.append(f"{column_name} = ?")
                values.append(value)
        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._conn.execute(
            f"""
            SELECT *
            FROM collaboration_tasks
            {where_sql}
            ORDER BY priority ASC, updated_at DESC, created_at DESC
            """,
            values,
        ).fetchall()
        return {"tasks": [self._serialize_collaboration_task(dict(row)) for row in rows]}

    def update_collaboration_task(self, params: dict[str, Any]) -> dict[str, Any]:
        task_id = self._require_non_empty(params, "taskId")
        current = self.require_collaboration_task(task_id)
        assignments: list[str] = ["updated_at = ?"]
        values: list[Any] = [self.now()]

        if "title" in params:
            assignments.append("title = ?")
            values.append(self._require_non_empty(params, "title"))
        if "description" in params:
            description = params.get("description")
            if description is not None and not isinstance(description, str):
                raise ValueError("description must be a string")
            assignments.append("description = ?")
            values.append(description.strip() if isinstance(description, str) else None)
        if "status" in params:
            status = self._normalize_collaboration_task_status(params.get("status"))
            assignments.append("status = ?")
            values.append(status)
            if status in {"completed", "failed", "cancelled"} and current["completedAt"] is None:
                assignments.append("completed_at = ?")
                values.append(self.now())
        if "priority" in params:
            assignments.append("priority = ?")
            values.append(self._normalize_priority(params.get("priority")))
        if "assignedWorkerId" in params:
            assignments.append("assigned_worker_id = ?")
            values.append(self._optional_string(params, "assignedWorkerId"))
        if "dependencies" in params:
            assignments.append("dependencies_json = ?")
            values.append(json.dumps(self._string_list(params.get("dependencies"), "dependencies"), ensure_ascii=False))
        if "result" in params:
            assignments.append("result_json = ?")
            values.append(json.dumps(self._dict_value(params.get("result"), "result"), ensure_ascii=False, sort_keys=True))
        if "metadata" in params:
            assignments.append("metadata_json = ?")
            values.append(json.dumps(self._dict_value(params.get("metadata"), "metadata"), ensure_ascii=False, sort_keys=True))

        values.append(task_id)
        self._conn.execute(f"UPDATE collaboration_tasks SET {', '.join(assignments)} WHERE id = ?", values)
        self._conn.commit()
        return {"task": self.require_collaboration_task(task_id)}

    def claim_collaboration_task(self, params: dict[str, Any]) -> dict[str, Any]:
        task_id = self._require_non_empty(params, "taskId")
        worker_id = self._require_non_empty(params, "workerId")
        task = self.require_collaboration_task(task_id)
        if task["status"] not in {"queued", "blocked"}:
            raise ValueError(f"Task cannot be claimed from status: {task['status']}")
        if task["assignedWorkerId"] and task["assignedWorkerId"] != worker_id:
            raise ValueError(f"Task is already assigned to {task['assignedWorkerId']}")
        self.require_agent_worker(worker_id)
        now = self.now()
        self._conn.execute(
            """
            UPDATE collaboration_tasks
            SET status = 'claimed', assigned_worker_id = ?, claimed_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (worker_id, now, now, task_id),
        )
        self._conn.execute(
            """
            UPDATE agent_workers
            SET status = 'busy', current_task_id = ?, last_heartbeat_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (task_id, now, now, worker_id),
        )
        self._conn.commit()
        return {"task": self.require_collaboration_task(task_id), "worker": self.require_agent_worker(worker_id)}

    def release_collaboration_task(self, params: dict[str, Any]) -> dict[str, Any]:
        task_id = self._require_non_empty(params, "taskId")
        worker_id = self._optional_string(params, "workerId")
        task = self.require_collaboration_task(task_id)
        if worker_id is not None and task["assignedWorkerId"] != worker_id:
            raise ValueError(f"Task is assigned to {task['assignedWorkerId']}, not {worker_id}")
        previous_worker_id = task["assignedWorkerId"]
        now = self.now()
        self._conn.execute(
            """
            UPDATE collaboration_tasks
            SET status = 'queued', assigned_worker_id = NULL, claimed_at = NULL, updated_at = ?
            WHERE id = ?
            """,
            (now, task_id),
        )
        if previous_worker_id is not None:
            self._conn.execute(
                """
                UPDATE agent_workers
                SET status = 'idle', current_task_id = NULL, last_heartbeat_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (now, now, previous_worker_id),
            )
        self._conn.commit()
        return {"task": self.require_collaboration_task(task_id)}

    def _finalize_collaboration_task(
        self,
        *,
        task_id: str,
        worker_id: str,
        task_status: str,
        worker_status: str,
        result: dict[str, Any] | None,
        error: Any,
    ) -> dict[str, Any]:
        if task_status not in {"completed", "failed"}:
            raise ValueError(f"Unsupported final collaboration task status: {task_status}")
        if worker_status not in {"idle", "failed"}:
            raise ValueError(f"Unsupported final worker status: {worker_status}")

        now = self.now()
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            task_row = self._conn.execute(
                "SELECT * FROM collaboration_tasks WHERE id = ?",
                (task_id,),
            ).fetchone()
            if task_row is None:
                raise ValueError(f"Collaboration task not found: {task_id}")
            worker_row = self._conn.execute(
                "SELECT * FROM agent_workers WHERE id = ?",
                (worker_id,),
            ).fetchone()
            if worker_row is None:
                raise ValueError(f"Agent worker not found: {worker_id}")

            task = self._serialize_collaboration_task(dict(task_row))
            worker = self._serialize_agent_worker(dict(worker_row))
            if task["assignedWorkerId"] != worker_id:
                raise ValueError(f"Task is assigned to {task['assignedWorkerId']}, not {worker_id}")
            if worker["currentTaskId"] != task_id:
                raise ValueError(f"Worker is not assigned to task {task_id}")
            if task["status"] not in {"claimed", "running", "blocked"}:
                raise ValueError(f"Task cannot be finalized from status: {task['status']}")

            self._conn.execute(
                """
                UPDATE collaboration_tasks
                SET status = ?, result_json = ?, error_json = ?, completed_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    task_status,
                    json.dumps(result, ensure_ascii=False, sort_keys=True) if result is not None else None,
                    json.dumps(error, ensure_ascii=False, sort_keys=True) if error is not None else None,
                    now,
                    now,
                    task_id,
                ),
            )
            self._conn.execute(
                """
                UPDATE agent_workers
                SET status = ?, current_task_id = NULL, last_heartbeat_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (worker_status, now, now, worker_id),
            )
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

        return {
            "task": self.require_collaboration_task(task_id),
            "worker": self.require_agent_worker(worker_id),
        }

    def upsert_pending_react_state(
        self,
        *,
        task_id: str,
        session_id: str,
        goal: str,
        context: dict[str, Any],
        messages: list[dict[str, Any]],
        tool_results: list[dict[str, Any]],
        pending_tool_call: dict[str, Any],
        pending_tool_spec: dict[str, Any],
        remaining_tool_calls: list[dict[str, Any]],
        steps: int,
        react_started: bool,
    ) -> dict[str, Any]:
        now = self.now()
        self._conn.execute(
            """
            INSERT INTO pending_react_tasks (
                task_id, session_id, goal, context_json, messages_json, tool_results_json,
                pending_tool_call_json, pending_tool_spec_json, remaining_tool_calls_json,
                steps, react_started, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(task_id) DO UPDATE SET
                session_id = excluded.session_id,
                goal = excluded.goal,
                context_json = excluded.context_json,
                messages_json = excluded.messages_json,
                tool_results_json = excluded.tool_results_json,
                pending_tool_call_json = excluded.pending_tool_call_json,
                pending_tool_spec_json = excluded.pending_tool_spec_json,
                remaining_tool_calls_json = excluded.remaining_tool_calls_json,
                steps = excluded.steps,
                react_started = excluded.react_started,
                updated_at = excluded.updated_at
            """,
            (
                task_id,
                session_id,
                goal,
                json.dumps(context, ensure_ascii=False),
                json.dumps(messages, ensure_ascii=False),
                json.dumps(tool_results, ensure_ascii=False),
                json.dumps(pending_tool_call, ensure_ascii=False),
                json.dumps(pending_tool_spec, ensure_ascii=False),
                json.dumps(remaining_tool_calls, ensure_ascii=False),
                int(steps),
                1 if react_started else 0,
                now,
                now,
            ),
        )
        self._conn.commit()
        state = self.get_pending_react_state(task_id)
        if state is None:
            raise ValueError(f"Pending ReAct state not found after upsert: {task_id}")
        return state

    def get_pending_react_state(self, task_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM pending_react_tasks WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        if row is None:
            return None
        return self._serialize_pending_react_state(dict(row))

    def delete_pending_react_state(self, task_id: str) -> None:
        self._conn.execute("DELETE FROM pending_react_tasks WHERE task_id = ?", (task_id,))
        self._conn.commit()

    # ------------------------------------------------------------------
    # Pending DAG state
    # ------------------------------------------------------------------

    def upsert_pending_dag_state(
        self,
        *,
        task_id: str,
        session_id: str,
        goal: str,
        context: dict[str, Any],
        plan_json: str,
        completed_ids: list[str],
        failed_ids: list[str],
        results: dict[str, str],
    ) -> dict[str, Any]:
        now = self.now()
        self._conn.execute(
            """
            INSERT INTO pending_dag_tasks (
                task_id, session_id, goal, context_json, plan_json,
                completed_ids_json, failed_ids_json, results_json,
                created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(task_id) DO UPDATE SET
                session_id = excluded.session_id,
                goal = excluded.goal,
                context_json = excluded.context_json,
                plan_json = excluded.plan_json,
                completed_ids_json = excluded.completed_ids_json,
                failed_ids_json = excluded.failed_ids_json,
                results_json = excluded.results_json,
                updated_at = excluded.updated_at
            """,
            (
                task_id,
                session_id,
                goal,
                json.dumps(context, ensure_ascii=False),
                plan_json,
                json.dumps(completed_ids, ensure_ascii=False),
                json.dumps(failed_ids, ensure_ascii=False),
                json.dumps(results, ensure_ascii=False),
                now,
                now,
            ),
        )
        self._conn.commit()
        state = self.get_pending_dag_state(task_id)
        if state is None:
            raise ValueError(f"Pending DAG state not found after upsert: {task_id}")
        return state

    def get_pending_dag_state(self, task_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM pending_dag_tasks WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        if row is None:
            return None
        d = dict(row)
        return {
            "task_id": d["task_id"],
            "session_id": d["session_id"],
            "goal": d["goal"],
            "context": json.loads(d["context_json"]),
            "plan": json.loads(d["plan_json"]),
            "completed": json.loads(d["completed_ids_json"]),
            "failed": json.loads(d["failed_ids_json"]),
            "results": json.loads(d["results_json"]),
        }

    def delete_pending_dag_state(self, task_id: str) -> None:
        self._conn.execute("DELETE FROM pending_dag_tasks WHERE task_id = ?", (task_id,))
        self._conn.commit()

    def list_tasks_by_status(self, statuses: list[str]) -> list[dict[str, Any]]:
        """Return tasks matching any of the given statuses."""
        placeholders = ",".join("?" for _ in statuses)
        rows = self._conn.execute(
            f"SELECT * FROM tasks WHERE status IN ({placeholders})",
            tuple(statuses),
        ).fetchall()
        return [self._serialize_task(dict(r)) for r in rows]

    def list_tasks_by_session_and_status(self, session_id: str, status: str) -> list[dict[str, Any]]:
        """Return tasks matching session_id and status, ordered by created_at."""
        rows = self._conn.execute(
            "SELECT * FROM tasks WHERE session_id = ? AND status = ? ORDER BY created_at ASC",
            (session_id, status),
        ).fetchall()
        return [self._serialize_task(dict(r)) for r in rows]

