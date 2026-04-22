from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

JsonValue = dict[str, Any] | list[Any] | str | int | float | bool | None
TaskStatus = Literal[
    "queued",
    "running",
    "waiting_approval",
    "paused",
    "completed",
    "failed",
    "cancelled",
]
CommandStatus = Literal["running", "completed", "failed", "timeout", "killed"]
PatchStatus = Literal["proposed", "approved", "applied", "rejected", "failed"]
ApprovalDecision = Literal["approved", "rejected"]
ApprovalKind = Literal["apply_patch", "run_command", "delete_file", "network_access"]


@dataclass(slots=True)
class Workspace:
    id: str
    name: str
    root_path: str
    created_at: int
    updated_at: int


@dataclass(slots=True)
class Session:
    id: str
    workspace_id: str
    title: str
    status: str
    created_at: int
    updated_at: int
    summary: str | None = None


@dataclass(slots=True)
class Task:
    id: str
    session_id: str
    type: str
    status: TaskStatus
    goal: str
    created_at: int
    updated_at: int
    plan: list[dict[str, Any]] = field(default_factory=list)
    result: dict[str, Any] | None = None
    error_code: str | None = None


@dataclass(slots=True)
class RpcEnvelope:
    jsonrpc: str
    id: str
    method: str
    params: dict[str, Any]


@dataclass(slots=True)
class RuntimeEvent:
    event_id: str
    session_id: str
    task_id: str
    type: str
    ts: int
    payload: dict[str, Any]


@dataclass(slots=True)
class ApprovalRecord:
    id: str
    task_id: str
    kind: ApprovalKind
    request_json: str
    created_at: int
    decision: ApprovalDecision | None = None
    decided_by: str | None = None
    decided_at: int | None = None


@dataclass(slots=True)
class PatchRecord:
    id: str
    task_id: str
    workspace_id: str
    summary: str
    diff_text: str
    status: PatchStatus
    files_changed: int
    created_at: int
    updated_at: int


@dataclass(slots=True)
class CommandLogRecord:
    id: str
    task_id: str
    command: str
    cwd: str
    status: CommandStatus
    started_at: int
    exit_code: int | None = None
    finished_at: int | None = None
    stdout_path: str | None = None
    stderr_path: str | None = None
