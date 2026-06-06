from __future__ import annotations

import json
import re
import sqlite3
import time
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any

_COMMAND_TRACE_METADATA_STRING_KEYS = (
    "toolUseId",
    "toolName",
    "parentToolUseId",
    "toolGroupId",
    "toolOperationId",
    "toolOperationLabel",
    "toolCategory",
    "toolPhaseId",
    "toolPhaseLabel",
    "toolSemanticParentId",
    "toolSemanticParentLabel",
    "target",
    "inputSummary",
)
_COMMAND_TRACE_METADATA_INT_KEYS = ("toolIndex", "toolTotal")


def _command_trace_metadata(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    metadata: dict[str, Any] = {}
    for key in _COMMAND_TRACE_METADATA_STRING_KEYS:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            metadata[key] = value.strip()
    for key in _COMMAND_TRACE_METADATA_INT_KEYS:
        value = payload.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            metadata[key] = value
    return metadata


def _normalize_diff_path(path: str) -> str:
    path = path.strip()
    if path.startswith(("a/", "b/")):
        path = path[2:]
    return path


def _append_changed_path(paths: list[str], path: str) -> None:
    normalized = _normalize_diff_path(path)
    if normalized and normalized != "/dev/null" and normalized not in paths:
        paths.append(normalized)


def _changed_paths_from_diff_text(diff_text: str) -> list[str]:
    paths: list[str] = []
    old_path = ""
    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            parts = line.split()
            if len(parts) >= 4:
                _append_changed_path(paths, parts[3])
            continue
        if line.startswith("--- "):
            old_path = line[4:].strip()
            continue
        if line.startswith("+++ "):
            new_path = line[4:].strip()
            _append_changed_path(paths, new_path if new_path != "/dev/null" else old_path)
    return paths


def _normalize_changed_paths(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    paths: list[str] = []
    for item in value:
        path = str(item).strip().replace("\\", "/") if isinstance(item, str) else ""
        if path and path not in paths:
            paths.append(path)
    return paths


def _approval_text(value: Any, max_chars: int = 160) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    text = re.sub(r"\s+", " ", text)
    return text if len(text) <= max_chars else text[: max_chars - 3].rstrip() + "..."


def _approval_preview_row(label: str, value: Any, *, max_chars: int = 160) -> dict[str, str] | None:
    text = _approval_text(value, max_chars=max_chars)
    if not text:
        return None
    return {"label": label, "value": text}


def _approval_preview(kind: str, request: dict[str, Any]) -> list[dict[str, str]]:
    rows: list[dict[str, str] | None]
    explicit_preview = request.get("previewRows")
    if isinstance(explicit_preview, list):
        rows = []
        for row in explicit_preview:
            if not isinstance(row, dict):
                continue
            label = _approval_text(row.get("label"), max_chars=40)
            value = _approval_text(row.get("value"), max_chars=220)
            if label and value:
                rows.append({"label": label, "value": value})
        if rows:
            return rows[:6]

    if kind == "run_command":
        rows = [_approval_preview_row("命令", request.get("command"), max_chars=220)]
        if request.get("toolName") == "notebook" or request.get("notebookAction") == "execute_cell":
            rows.extend(
                [
                    _approval_preview_row("Notebook", request.get("path")),
                    _approval_preview_row("Cell", request.get("cellIndex")),
                ]
            )
        rows.extend(
            [
                _approval_preview_row("目录", request.get("cwd") or request.get("workspaceRoot")),
                _approval_preview_row("Shell", request.get("shell")),
                _approval_preview_row("原因", request.get("policyReason") or request.get("risk") or request.get("reason")),
            ]
        )
    elif kind == "network_access":
        rows = [
            _approval_preview_row("方法", request.get("method") or "GET"),
            _approval_preview_row("URL", request.get("url"), max_chars=220),
            _approval_preview_row("原因", request.get("reason") or request.get("risk")),
        ]
    elif kind == "computer_use":
        rows = [
            _approval_preview_row("应用", request.get("app") or request.get("target") or request.get("application")),
            _approval_preview_row("动作", request.get("action")),
            _approval_preview_row("权限", request.get("permission") or request.get("summary"), max_chars=220),
            _approval_preview_row("详情", request.get("details"), max_chars=220),
        ]
    elif kind == "subagent_dispatch":
        rows = [
            _approval_preview_row("子任务", request.get("prompt"), max_chars=240),
            _approval_preview_row("原因", request.get("reason") or request.get("risk")),
        ]
    elif kind == "plan":
        execution_order = request.get("executionOrder") or request.get("execution_order")
        order_text = ""
        if isinstance(execution_order, list):
            order_text = " -> ".join(str(item) for item in execution_order[:12] if str(item).strip())
        subtask_count = request.get("subtaskCount")
        if subtask_count is None and isinstance(request.get("subtasks"), list):
            subtask_count = len(request["subtasks"])
        rows = [
            _approval_preview_row("目标", request.get("goal"), max_chars=220),
            _approval_preview_row("模式", request.get("orchestrationMode") or request.get("mode") or "plan"),
            _approval_preview_row("子任务", subtask_count),
            _approval_preview_row("执行顺序", order_text, max_chars=220),
        ]
    else:
        rows = [
            _approval_preview_row("摘要", request.get("summary") or request.get("description")),
            _approval_preview_row("目标", request.get("target") or request.get("path") or request.get("url")),
            _approval_preview_row("原因", request.get("reason") or request.get("risk")),
        ]
    return [row for row in rows if row is not None][:5]


def _compact_approval_value(value: Any, *, max_chars: int = 1000) -> Any:
    if isinstance(value, str):
        if len(value) <= max_chars:
            return value
        return {"chars": len(value), "omitted": True, "preview": value[:240]}
    if isinstance(value, dict):
        return {
            str(key): _compact_approval_value(child)
            for key, child in value.items()
            if str(key) not in {"workspaceRoot", "workspace_root", "requestJson"}
        }
    if isinstance(value, list):
        return [_compact_approval_value(item) for item in value[:20]]
    return value


def _public_plan_approval_request(request: dict[str, Any]) -> dict[str, Any]:
    plan = request.get("plan") if isinstance(request.get("plan"), dict) else {}
    steps = request.get("steps") if isinstance(request.get("steps"), list) else plan.get("steps")
    subtasks = request.get("subtasks") if isinstance(request.get("subtasks"), list) else plan.get("subtasks")
    risks = request.get("risks") if isinstance(request.get("risks"), list) else plan.get("risks")
    summary = request.get("summary") or plan.get("summary") or plan.get("title")
    public: dict[str, Any] = {}
    for key in (
        "goal",
        "mode",
        "orchestrationMode",
        "source",
        "decompositionFallback",
        "decompositionFallbackReason",
    ):
        value = request.get(key)
        if value not in (None, "", [], {}):
            public[key] = _compact_approval_value(value)
    if summary not in (None, ""):
        public["summary"] = _compact_approval_value(summary)
    if isinstance(steps, list) and steps:
        public["steps"] = _compact_approval_value(steps)
        public["stepCount"] = request.get("stepCount") if request.get("stepCount") is not None else len(steps)
    elif request.get("stepCount") is not None:
        public["stepCount"] = request.get("stepCount")
    if isinstance(subtasks, list) and subtasks:
        public["subtasks"] = _compact_approval_value(subtasks)
        public["subtaskCount"] = request.get("subtaskCount") if request.get("subtaskCount") is not None else len(subtasks)
    elif request.get("subtaskCount") is not None:
        public["subtaskCount"] = request.get("subtaskCount")
    if isinstance(risks, list) and risks:
        public["risks"] = _compact_approval_value(risks)
    for key in ("executionOrder", "previewRows", "previewSections"):
        value = request.get(key)
        if value not in (None, "", [], {}):
            public[key] = _compact_approval_value(value)
    public_plan = {
        key: value
        for key, value in {
            "summary": summary,
            "steps": steps,
            "subtasks": subtasks,
            "risks": risks,
        }.items()
        if value not in (None, "", [], {})
    }
    if public_plan:
        public["plan"] = _compact_approval_value(public_plan)
    return public


def _public_approval_request(kind: str, request: dict[str, Any]) -> dict[str, Any]:
    if kind == "plan":
        return _public_plan_approval_request(request)
    public: dict[str, Any] = {}
    allowed_keys = (
        "command",
        "cwd",
        "shell",
        "reason",
        "risk",
        "policyReason",
        "summary",
        "description",
        "target",
        "path",
        "url",
        "method",
        "action",
        "permission",
        "app",
        "application",
        "selector",
        "changedPaths",
        "filesChanged",
        "patchMode",
        "dryRun",
        "previewRows",
        "previewSections",
    )
    for key in allowed_keys:
        value = request.get(key)
        if value in (None, "", [], {}):
            continue
        public[key] = _compact_approval_value(value)
    diff_text = request.get("diffText") or request.get("patchText")
    if isinstance(diff_text, str) and diff_text.strip():
        public["diffText"] = _compact_approval_value(diff_text)
    files = request.get("files")
    if isinstance(files, list):
        public["files"] = [
            {
                key: _compact_approval_value(value)
                for key, value in item.items()
                if isinstance(item, dict) and key in {"path", "operation", "summary"}
            }
            for item in files[:20]
            if isinstance(item, dict)
        ]
    return public


def _public_patch_trace_payload(patch: dict[str, Any], changed_paths: list[str]) -> dict[str, Any]:
    return {
        "patchId": patch["id"],
        "summary": patch["summary"],
        "status": patch["status"],
        "filesChanged": patch["filesChanged"],
        "changedPaths": changed_paths,
        "diffText": patch["diffText"],
    }


class ProposalStoreMixin:
    def resolve_approval(self, approval_id: str, decision: str) -> dict[str, Any]:
        now = self.now()
        self._conn.execute(
            """
            UPDATE approvals
            SET decision = ?, decided_by = 'user', decided_at = ?
            WHERE id = ?
            """,
            (decision, now, approval_id),
        )
        self._conn.commit()
        row = self._conn.execute(
            "SELECT * FROM approvals WHERE id = ?",
            (approval_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"Approval not found: {approval_id}")
        approval = self._serialize_approval(dict(row))
        task_row = self._conn.execute(
            "SELECT status FROM tasks WHERE id = ?",
            (approval["taskId"],),
        ).fetchone()
        task_status = str(task_row["status"] or "").strip() if task_row is not None else ""
        try:
            request = json.loads(approval.get("requestJson") or "{}")
        except json.JSONDecodeError:
            request = {}
        if not isinstance(request, dict):
            request = {}
        changed_paths = _normalize_changed_paths(request.get("changedPaths"))
        if not changed_paths:
            changed_paths = _changed_paths_from_diff_text(str(request.get("diffText") or request.get("patchText") or ""))
        diff_text = request.get("diffText")
        files_changed = request.get("filesChanged")
        public_request = _public_approval_request(str(approval["kind"] or ""), request)
        resolved_payload = {
            "approvalId": approval["id"],
            "taskId": approval["taskId"],
            "kind": approval["kind"],
            "request": public_request,
            "filesChanged": files_changed if isinstance(files_changed, int) else len(changed_paths),
            "changedPaths": changed_paths,
            "diffText": _compact_approval_value(diff_text) if isinstance(diff_text, str) and diff_text else "",
            "preview": _approval_preview(approval["kind"], request),
            "previewSections": request.get("previewSections") if isinstance(request.get("previewSections"), list) else [],
            "decision": approval["decision"],
            "decidedBy": approval["decidedBy"],
            "decidedAt": approval["decidedAt"],
        }
        if task_status:
            resolved_payload["taskStatus"] = task_status
        if task_status in {"cancelled", "canceled", "completed", "failed"}:
            resolved_payload["ignored"] = True
        elif task_status == "paused":
            resolved_payload["deferred"] = True
        self.append_trace_event(
            task_id=approval["taskId"],
            event_type="approval.resolved",
            source="approval",
            related_id=approval["id"],
            payload=resolved_payload,
            created_at=approval["decidedAt"],
        )
        return approval

    def create_patch(
        self,
        *,
        task_id: str,
        workspace_id: str,
        summary: str,
        diff_text: str,
        files_changed: int,
        status: str = "proposed",
    ) -> dict[str, Any]:
        patch_id = self.new_id("patch")
        now = self.now()
        self._conn.execute(
            """
            INSERT INTO patches (
                id, task_id, workspace_id, summary, diff_text, status, files_changed, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                patch_id,
                task_id,
                workspace_id,
                summary,
                diff_text,
                status,
                files_changed,
                now,
                now,
            ),
        )
        self._conn.commit()
        row = self._conn.execute("SELECT * FROM patches WHERE id = ?", (patch_id,)).fetchone()
        if row is None:
            raise ValueError(f"Patch not found: {patch_id}")
        patch = self._serialize_patch(dict(row))
        changed_paths = _changed_paths_from_diff_text(patch["diffText"])
        self.append_trace_event(
            task_id=patch["taskId"],
            event_type="patch.proposed" if patch["status"] == "proposed" else f"patch.{patch['status']}",
            source="patch",
            related_id=patch["id"],
            payload=_public_patch_trace_payload(patch, changed_paths),
            created_at=patch["createdAt"],
        )
        patch["changedPaths"] = changed_paths
        return patch

    def update_patch(
        self,
        patch_id: str,
        *,
        status: str | None = None,
        summary: str | None = None,
        diff_text: str | None = None,
        files_changed: int | None = None,
    ) -> dict[str, Any]:
        assignments: list[str] = ["updated_at = ?"]
        values: list[Any] = [self.now()]

        if status is not None:
            assignments.append("status = ?")
            values.append(status)
        if summary is not None:
            assignments.append("summary = ?")
            values.append(summary)
        if diff_text is not None:
            assignments.append("diff_text = ?")
            values.append(diff_text)
        if files_changed is not None:
            assignments.append("files_changed = ?")
            values.append(files_changed)

        values.append(patch_id)
        self._conn.execute(
            f"UPDATE patches SET {', '.join(assignments)} WHERE id = ?",
            values,
        )
        self._conn.commit()
        row = self._conn.execute("SELECT * FROM patches WHERE id = ?", (patch_id,)).fetchone()
        if row is None:
            raise ValueError(f"Patch not found: {patch_id}")
        patch = self._serialize_patch(dict(row))
        changed_paths = _changed_paths_from_diff_text(patch["diffText"])
        self.append_trace_event(
            task_id=patch["taskId"],
            event_type=f"patch.{patch['status']}",
            source="patch",
            related_id=patch["id"],
            payload=_public_patch_trace_payload(patch, changed_paths),
            created_at=patch["updatedAt"],
        )
        patch["changedPaths"] = changed_paths
        return patch

    def get_approval(self, params: dict[str, Any]) -> dict[str, Any]:
        row = self._conn.execute(
            "SELECT * FROM approvals WHERE id = ?",
            (params["approvalId"],),
        ).fetchone()
        if row is None:
            raise ValueError(f"Approval not found: {params['approvalId']}")
        return {"approval": self._serialize_approval(dict(row))}

    def create_approval(self, task_id: str, kind: str, request: dict[str, Any]) -> dict[str, Any]:
        approval_id = self.new_id("appr")
        now = self.now()
        request_json = json.dumps(request, ensure_ascii=False, sort_keys=True)
        self._conn.execute(
            """
            INSERT INTO approvals (id, task_id, kind, request_json, decision, decided_by, created_at, decided_at)
            VALUES (?, ?, ?, ?, NULL, NULL, ?, NULL)
            """,
            (approval_id, task_id, kind, request_json, now),
        )
        self._conn.commit()
        row = self._conn.execute("SELECT * FROM approvals WHERE id = ?", (approval_id,)).fetchone()
        if row is None:
            raise ValueError(f"Approval not found: {approval_id}")
        approval = self._serialize_approval(dict(row))
        changed_paths = _normalize_changed_paths(request.get("changedPaths"))
        if not changed_paths:
            changed_paths = _changed_paths_from_diff_text(str(request.get("diffText") or request.get("patchText") or ""))
        diff_text = request.get("diffText")
        files_changed = request.get("filesChanged")
        public_request = _public_approval_request(str(approval["kind"] or ""), request)
        self.append_trace_event(
            task_id=approval["taskId"],
            event_type="approval.requested",
            source="approval",
            related_id=approval["id"],
            payload={
                "approvalId": approval["id"],
                "taskId": approval["taskId"],
                "kind": approval["kind"],
                "request": public_request,
                "filesChanged": files_changed if isinstance(files_changed, int) else len(changed_paths),
                "changedPaths": changed_paths,
                "diffText": _compact_approval_value(diff_text) if isinstance(diff_text, str) and diff_text else "",
                "preview": _approval_preview(approval["kind"], request),
                "previewSections": request.get("previewSections") if isinstance(request.get("previewSections"), list) else [],
            },
            created_at=approval["createdAt"],
        )
        return approval

    def update_approval_request(self, approval_id: str, request: dict[str, Any]) -> dict[str, Any]:
        request_json = json.dumps(request, ensure_ascii=False, sort_keys=True)
        cursor = self._conn.execute(
            "UPDATE approvals SET request_json = ? WHERE id = ? AND decision IS NULL",
            (request_json, approval_id),
        )
        self._conn.commit()
        if cursor.rowcount == 0:
            raise ValueError(f"Pending approval not found: {approval_id}")
        row = self._conn.execute("SELECT * FROM approvals WHERE id = ?", (approval_id,)).fetchone()
        if row is None:
            raise ValueError(f"Approval not found: {approval_id}")
        return self._serialize_approval(dict(row))

    def find_approval(
        self,
        *,
        task_id: str,
        kind: str,
        request: dict[str, Any],
        decision: str | None = None,
    ) -> dict[str, Any] | None:
        request_json = json.dumps(request, ensure_ascii=False, sort_keys=True)
        query = ["SELECT * FROM approvals WHERE task_id = ? AND kind = ? AND request_json = ?"]
        values: list[Any] = [task_id, kind, request_json]
        if decision is not None:
            query.append("AND decision = ?")
            values.append(decision)
        query.append("ORDER BY created_at DESC LIMIT 1")
        row = self._conn.execute(" ".join(query), values).fetchone()
        if row is None:
            return None
        return self._serialize_approval(dict(row))

    def find_approval_by_request_fields(
        self,
        *,
        task_id: str,
        kind: str,
        fields: dict[str, Any],
        decision: str | None = None,
    ) -> dict[str, Any] | None:
        query = ["SELECT * FROM approvals WHERE task_id = ? AND kind = ?"]
        values: list[Any] = [task_id, kind]
        if decision is not None:
            query.append("AND decision = ?")
            values.append(decision)
        query.append("ORDER BY created_at DESC")
        rows = self._conn.execute(" ".join(query), values).fetchall()
        for row in rows:
            approval = self._serialize_approval(dict(row))
            try:
                request = json.loads(approval.get("requestJson") or "{}")
            except json.JSONDecodeError:
                continue
            if not isinstance(request, dict):
                continue
            if all(request.get(key) == value for key, value in fields.items()):
                return approval
        return None

    def find_latest_approval(self, *, task_id: str, decision: str | None = None) -> dict[str, Any] | None:
        query = ["SELECT * FROM approvals WHERE task_id = ?"]
        values: list[Any] = [task_id]
        if decision is not None:
            query.append("AND decision = ?")
            values.append(decision)
        query.append("ORDER BY created_at DESC LIMIT 1")
        row = self._conn.execute(" ".join(query), values).fetchone()
        if row is None:
            return None
        return self._serialize_approval(dict(row))

    def get_patch(self, params: dict[str, Any]) -> dict[str, Any]:
        row = self._conn.execute(
            "SELECT * FROM patches WHERE id = ?",
            (params["patchId"],),
        ).fetchone()
        if row is None:
            raise ValueError(f"Patch not found: {params['patchId']}")
        patch = self._serialize_patch(dict(row))
        patch["changedPaths"] = _changed_paths_from_diff_text(patch["diffText"])
        return {"patch": patch, "diffText": patch["diffText"]}

    def list_patches_for_task(self, task_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            """
            SELECT *
            FROM patches
            WHERE task_id = ?
            ORDER BY created_at ASC, updated_at ASC, id ASC
            """,
            (task_id,),
        ).fetchall()
        patches = [self._serialize_patch(dict(row)) for row in rows]
        for patch in patches:
            patch["changedPaths"] = _changed_paths_from_diff_text(patch["diffText"])
        return patches

    def find_patch(
        self,
        *,
        task_id: str,
        workspace_id: str,
        diff_text: str,
    ) -> dict[str, Any] | None:
        row = self._conn.execute(
            """
            SELECT * FROM patches
            WHERE task_id = ? AND workspace_id = ? AND diff_text = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (task_id, workspace_id, diff_text),
        ).fetchone()
        if row is None:
            return None
        patch = self._serialize_patch(dict(row))
        patch["changedPaths"] = _changed_paths_from_diff_text(patch["diffText"])
        return patch

    def get_command_log(self, params: dict[str, Any]) -> dict[str, Any]:
        row = self._conn.execute(
            "SELECT * FROM command_logs WHERE id = ?",
            (params["commandId"],),
        ).fetchone()
        if row is None:
            raise ValueError(f"Command log not found: {params['commandId']}")
        return {"commandLog": self._serialize_command_log(dict(row))}

    def list_command_logs(self, params: dict[str, Any]) -> dict[str, Any]:
        task_id = params.get("taskId") or params.get("task_id")
        session_id = params.get("sessionId") or params.get("session_id")
        status = params.get("status")
        limit = int(params.get("limit", 100))
        limit = max(1, min(limit, 1000))

        query = [
            """
            SELECT command_logs.*
            FROM command_logs
            JOIN tasks ON tasks.id = command_logs.task_id
            """
        ]
        where: list[str] = []
        values: list[Any] = []
        if isinstance(task_id, str) and task_id.strip():
            where.append("command_logs.task_id = ?")
            values.append(task_id.strip())
        if isinstance(session_id, str) and session_id.strip():
            where.append("tasks.session_id = ?")
            values.append(session_id.strip())
        if isinstance(status, str) and status.strip():
            where.append("command_logs.status = ?")
            values.append(status.strip())
        if where:
            query.append("WHERE " + " AND ".join(where))
        query.append("ORDER BY command_logs.started_at DESC LIMIT ?")
        values.append(limit)

        rows = self._conn.execute("\n".join(query), values).fetchall()
        return {"commandLogs": [self._serialize_command_log(dict(row)) for row in rows]}

    def create_command_log(
        self,
        *,
        task_id: str,
        command: str,
        cwd: str,
        shell: str,
        tool_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        command_id = self.new_id("cmd")
        now = self.now()
        self._conn.execute(
            """
            INSERT INTO command_logs (
                id, task_id, command, cwd, exit_code, status, stdout_path, stderr_path, started_at, finished_at
            )
            VALUES (?, ?, ?, ?, NULL, 'running', NULL, NULL, ?, NULL)
            """,
            (command_id, task_id, command, cwd, now),
        )
        self._conn.commit()
        row = self._conn.execute("SELECT * FROM command_logs WHERE id = ?", (command_id,)).fetchone()
        if row is None:
            raise ValueError(f"Command log not found: {command_id}")
        record = self._serialize_command_log(dict(row))
        record["shell"] = shell
        record.update(_command_trace_metadata(tool_metadata))
        self.append_trace_event(
            task_id=record["taskId"],
            event_type="command.started",
            source="command",
            related_id=record["id"],
            payload={
                "commandId": record["id"],
                "command": record["command"],
                "cwd": record["cwd"],
                "shell": shell,
                "status": record["status"],
                **_command_trace_metadata(tool_metadata),
            },
            created_at=record["startedAt"],
        )
        return record

    def update_command_log(
        self,
        command_id: str,
        *,
        status: str,
        exit_code: int | None,
        stdout_path: str | None = None,
        stderr_path: str | None = None,
        finished_at: int | None = None,
    ) -> dict[str, Any]:
        finished = finished_at if finished_at is not None else self.now()
        self._conn.execute(
            """
            UPDATE command_logs
            SET status = ?, exit_code = ?, stdout_path = ?, stderr_path = ?, finished_at = ?
            WHERE id = ?
            """,
            (status, exit_code, stdout_path, stderr_path, finished, command_id),
        )
        self._conn.commit()
        row = self._conn.execute("SELECT * FROM command_logs WHERE id = ?", (command_id,)).fetchone()
        if row is None:
            raise ValueError(f"Command log not found: {command_id}")
        command_log = self._serialize_command_log(dict(row))
        if command_log["status"] == "completed":
            event_type = "command.completed"
        elif command_log["status"] == "cancelled":
            event_type = "command.cancelled"
        else:
            event_type = "command.failed"
        started_metadata: dict[str, Any] = {}
        started_trace = self._conn.execute(
            """
            SELECT payload_json FROM trace_events
            WHERE related_id = ? AND type = 'command.started'
            ORDER BY created_at DESC, sequence DESC
            LIMIT 1
            """,
            (command_log["id"],),
        ).fetchone()
        if started_trace is not None:
            try:
                started_metadata = _command_trace_metadata(json.loads(started_trace["payload_json"] or "{}"))
            except (TypeError, json.JSONDecodeError):
                started_metadata = {}
        self.append_trace_event(
            task_id=command_log["taskId"],
            event_type=event_type,
            source="command",
            related_id=command_log["id"],
            payload={
                "commandId": command_log["id"],
                "command": command_log["command"],
                "cwd": command_log["cwd"],
                "status": command_log["status"],
                "exitCode": command_log["exitCode"],
                "durationMs": command_log["durationMs"],
                "stdoutPath": command_log["stdoutPath"],
                "stderrPath": command_log["stderrPath"],
                **started_metadata,
            },
            created_at=command_log["finishedAt"],
        )
        return command_log

    def write_command_artifact(self, command_id: str, stream_name: str, content: str) -> str:
        artifact_path = self._artifact_dir / f"{command_id}_{stream_name}.log"
        artifact_path.write_text(content, encoding="utf-8", errors="replace")
        return str(artifact_path)

    def _serialize_proposal(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": row["id"],
            "kind": row["kind"],
            "sessionId": row["session_id"],
            "taskId": row["task_id"],
            "source": json.loads(row.get("source_json") or "{}"),
            "inputSummary": row.get("input_summary"),
            "proposal": json.loads(row.get("proposal_json") or "{}"),
            "status": row["status"],
            "validationReasons": json.loads(row.get("validation_reasons_json") or "[]"),
            "appliedTo": json.loads(row.get("applied_to_json") or "{}"),
            "modelId": row.get("model_id"),
            "turnId": row.get("turn_id"),
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        }

    VALID_PROPOSAL_KINDS = frozenset({
        "intent_mode", "routing_strategy", "decomposition", "agent_profile", "model_policy",
        "skill_policy", "tool_policy", "mcp_policy", "context_policy",
        "memory_policy", "artifact_contract", "risk_policy", "approval_policy",
        "test_strategy", "failure_recovery", "provider_preflight", "event_presentation",
        "synthesis_strategy", "todo_maintenance",
        "react_turn_decision", "completion_decision", "product_surface_decision",
        "user_takeover", "tool_recovery", "budget_convergence",
    })

    VALID_PROPOSAL_STATUSES = frozenset({"pending", "accepted", "rejected", "applied"})

    def create_proposal(self, params: dict[str, Any]) -> dict[str, Any]:
        kind = self._require_non_empty(params, "kind")
        if kind not in self.VALID_PROPOSAL_KINDS:
            raise ValueError(f"Invalid proposal kind: {kind!r}")
        session_id = self._require_non_empty(params, "sessionId")
        task_id = self._require_non_empty(params, "taskId")
        proposal = self._dict_value(params.get("proposal", {}), "proposal")
        source = self._dict_value(params.get("source", {}), "source")
        input_summary = params.get("inputSummary")
        model_id = params.get("modelId")
        turn_id = params.get("turnId")
        proposal_id = self.new_id("prop")
        now = self.now()
        self._conn.execute(
            """
            INSERT INTO proposal_records (
                id, kind, session_id, task_id, source_json, input_summary,
                proposal_json, status, validation_reasons_json, applied_to_json,
                model_id, turn_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', '[]', '{}', ?, ?, ?, ?)
            """,
            (
                proposal_id, kind, session_id, task_id,
                json.dumps(source, ensure_ascii=False),
                input_summary,
                json.dumps(proposal, ensure_ascii=False),
                model_id, turn_id, now, now,
            ),
        )
        self._conn.commit()
        row = self._conn.execute(
            "SELECT * FROM proposal_records WHERE id = ?", (proposal_id,)
        ).fetchone()
        return {"proposal": self._serialize_proposal(dict(row))}

    def validate_proposal(self, params: dict[str, Any]) -> dict[str, Any]:
        proposal_id = self._require_non_empty(params, "proposalId")
        status = self._require_non_empty(params, "status")
        if status not in ("accepted", "rejected"):
            raise ValueError(f"Validation status must be 'accepted' or 'rejected', got {status!r}")
        reasons = self._string_list(params.get("reasons", []), "reasons")
        now = self.now()
        row = self._conn.execute(
            "SELECT * FROM proposal_records WHERE id = ?", (proposal_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"Proposal not found: {proposal_id}")
        current = dict(row)
        if current["status"] != "pending":
            raise ValueError(f"Proposal {proposal_id} has status {current['status']!r}, expected 'pending'")
        self._conn.execute(
            "UPDATE proposal_records SET status = ?, validation_reasons_json = ?, updated_at = ? WHERE id = ?",
            (status, json.dumps(reasons, ensure_ascii=False), now, proposal_id),
        )
        self._conn.commit()
        row = self._conn.execute(
            "SELECT * FROM proposal_records WHERE id = ?", (proposal_id,)
        ).fetchone()
        return {"proposal": self._serialize_proposal(dict(row))}

    def apply_proposal(self, params: dict[str, Any]) -> dict[str, Any]:
        proposal_id = self._require_non_empty(params, "proposalId")
        applied_to = self._dict_value(params.get("appliedTo", {}), "appliedTo")
        now = self.now()
        row = self._conn.execute(
            "SELECT * FROM proposal_records WHERE id = ?", (proposal_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"Proposal not found: {proposal_id}")
        current = dict(row)
        if current["status"] != "accepted":
            raise ValueError(f"Proposal {proposal_id} has status {current['status']!r}, expected 'accepted'")
        self._conn.execute(
            "UPDATE proposal_records SET status = 'applied', applied_to_json = ?, updated_at = ? WHERE id = ?",
            (json.dumps(applied_to, ensure_ascii=False), now, proposal_id),
        )
        self._conn.commit()
        row = self._conn.execute(
            "SELECT * FROM proposal_records WHERE id = ?", (proposal_id,)
        ).fetchone()
        return {"proposal": self._serialize_proposal(dict(row))}

    def list_proposals(self, params: dict[str, Any]) -> dict[str, Any]:
        filters = []
        args: list[Any] = []
        session_id = params.get("sessionId")
        if session_id:
            filters.append("session_id = ?")
            args.append(session_id)
        task_id = params.get("taskId")
        if task_id:
            filters.append("task_id = ?")
            args.append(task_id)
        kind = params.get("kind")
        if kind:
            filters.append("kind = ?")
            args.append(kind)
        status = params.get("status")
        if status:
            filters.append("status = ?")
            args.append(status)
        limit = min(int(params.get("limit") or 100), 500)
        where = f" WHERE {' AND '.join(filters)}" if filters else ""
        query = f"SELECT * FROM proposal_records{where} ORDER BY created_at DESC LIMIT ?"
        args.append(limit)
        rows = [dict(r) for r in self._conn.execute(query, args).fetchall()]
        return {"proposals": [self._serialize_proposal(r) for r in rows]}

    # -----------------------------------------------------------------------
    # Artifact Registry CRUD — P3 of subagent-generation-todolist
    # -----------------------------------------------------------------------

    VALID_ARTIFACT_KINDS = frozenset({"plan", "file", "patch", "review", "test_report", "asset"})
    VALID_ARTIFACT_STATUSES = frozenset({"proposed", "applied", "verified", "rejected"})

    def _serialize_artifact(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": row["id"],
            "sessionId": row["session_id"],
            "parentTaskId": row["parent_task_id"],
            "producerTaskId": row["producer_task_id"],
            "kind": row["kind"],
            "status": row["status"],
            "title": row.get("title"),
            "description": row.get("description"),
            "content": json.loads(row.get("content_json") or "{}"),
            "metadata": json.loads(row.get("metadata_json") or "{}"),
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        }

    def create_artifact(self, params: dict[str, Any]) -> dict[str, Any]:
        session_id = self._require_non_empty(params, "sessionId")
        parent_task_id = self._require_non_empty(params, "parentTaskId")
        producer_task_id = self._require_non_empty(params, "producerTaskId")
        kind = self._require_non_empty(params, "kind")
        if kind not in self.VALID_ARTIFACT_KINDS:
            raise ValueError(f"Invalid artifact kind: {kind!r}")
        title = params.get("title")
        description = params.get("description")
        content = self._dict_value(params.get("content", {}), "content")
        metadata = self._dict_value(params.get("metadata", {}), "metadata")
        artifact_id = self.new_id("art")
        now = self.now()
        self._conn.execute(
            """
            INSERT INTO artifacts (
                id, session_id, parent_task_id, producer_task_id,
                kind, status, title, description, content_json,
                metadata_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, 'proposed', ?, ?, ?, ?, ?, ?)
            """,
            (
                artifact_id, session_id, parent_task_id, producer_task_id,
                kind, title, description,
                json.dumps(content, ensure_ascii=False),
                json.dumps(metadata, ensure_ascii=False),
                now, now,
            ),
        )
        self._conn.commit()
        row = self._conn.execute(
            "SELECT * FROM artifacts WHERE id = ?", (artifact_id,)
        ).fetchone()
        return {"artifact": self._serialize_artifact(dict(row))}

    def update_artifact(self, params: dict[str, Any]) -> dict[str, Any]:
        artifact_id = self._require_non_empty(params, "artifactId")
        now = self.now()
        row = self._conn.execute(
            "SELECT * FROM artifacts WHERE id = ?", (artifact_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"Artifact not found: {artifact_id}")
        updates: list[str] = []
        args: list[Any] = []
        for field, col in [("status", "status"), ("title", "title"), ("description", "description")]:
            if field in params:
                val = params[field]
                if field == "status" and val not in self.VALID_ARTIFACT_STATUSES:
                    raise ValueError(f"Invalid artifact status: {val!r}")
                if field == "status" and row["status"] == "rejected" and val != "rejected":
                    raise ValueError("Rejected artifacts cannot transition to another status")
                updates.append(f"{col} = ?")
                args.append(val)
        if "content" in params:
            updates.append("content_json = ?")
            args.append(json.dumps(self._dict_value(params["content"], "content"), ensure_ascii=False))
        if "metadata" in params:
            updates.append("metadata_json = ?")
            args.append(json.dumps(self._dict_value(params["metadata"], "metadata"), ensure_ascii=False))
        if not updates:
            return {"artifact": self._serialize_artifact(dict(row))}
        updates.append("updated_at = ?")
        args.append(now)
        args.append(artifact_id)
        self._conn.execute(
            f"UPDATE artifacts SET {', '.join(updates)} WHERE id = ?", args
        )
        self._conn.commit()
        row = self._conn.execute(
            "SELECT * FROM artifacts WHERE id = ?", (artifact_id,)
        ).fetchone()
        return {"artifact": self._serialize_artifact(dict(row))}

    def list_artifacts(self, params: dict[str, Any]) -> dict[str, Any]:
        filters: list[str] = []
        args: list[Any] = []
        if params.get("sessionId"):
            filters.append("session_id = ?")
            args.append(params["sessionId"])
        if params.get("parentTaskId"):
            filters.append("parent_task_id = ?")
            args.append(params["parentTaskId"])
        if params.get("producerTaskId"):
            filters.append("producer_task_id = ?")
            args.append(params["producerTaskId"])
        if params.get("kind"):
            filters.append("kind = ?")
            args.append(params["kind"])
        if params.get("status"):
            filters.append("status = ?")
            args.append(params["status"])
        limit = min(int(params.get("limit") or 100), 500)
        where = f" WHERE {' AND '.join(filters)}" if filters else ""
        query = f"SELECT * FROM artifacts{where} ORDER BY created_at ASC LIMIT ?"
        args.append(limit)
        rows = [dict(r) for r in self._conn.execute(query, args).fetchall()]
        return {"artifacts": [self._serialize_artifact(r) for r in rows]}

