from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

JsonValue = dict[str, Any] | list[Any] | str | int | float | bool | None
TaskStatus = Literal[
    "queued",
    "planning",
    "running",
    "waiting_approval",
    "verifying",
    "paused",
    "completed",
    "failed",
    "cancelled",
]
CommandStatus = Literal["running", "completed", "failed", "timeout", "killed", "cancelled"]
PatchStatus = Literal["proposed", "approved", "applied", "rejected", "failed"]
ApprovalDecision = Literal["approved", "rejected"]
ApprovalKind = Literal[
    "apply_patch",
    "run_command",
    "delete_file",
    "network_access",
    "plan",
    "write_file",
    "worktree_merge",
    "completion_review",
    "advisor_tool",
]
ScheduledTaskStatus = Literal["active", "disabled"]
ScheduledRunStatus = Literal["queued", "running", "completed", "failed", "cancelled"]
CollaborationTaskStatus = Literal["queued", "claimed", "running", "blocked", "completed", "failed", "cancelled"]
AgentWorkerStatus = Literal["idle", "busy", "offline", "stopped", "failed"]
AgentMessageKind = Literal["note", "handoff", "broadcast", "result", "system"]
AgentRole = Literal["root", "planner", "worker", "reviewer", "summarizer"]
EventVisibility = Literal["chat", "panel", "trace"]

# Proposal kinds — P2 of llm-assisted-runtime-decision-todolist
ProposalKind = Literal[
    "intent_mode",
    "routing_strategy",
    "decomposition",
    "agent_profile",
    "model_policy",
    "skill_policy",
    "tool_policy",
    "mcp_policy",
    "context_policy",
    "memory_policy",
    "artifact_contract",
    "risk_policy",
    "approval_policy",
    "test_strategy",
    "failure_recovery",
    "event_presentation",
    "synthesis_strategy",
    "todo_maintenance",
    "react_turn_decision",
    "completion_decision",
    "product_surface_decision",
    "user_takeover",
]

ProposalStatus = Literal["pending", "accepted", "rejected", "applied"]

ArtifactKind = Literal["plan", "file", "patch", "review", "test_report", "asset"]
ArtifactStatus = Literal["proposed", "applied", "verified", "rejected"]


@dataclass(slots=True)
class ArtifactRecord:
    id: str
    session_id: str
    parent_task_id: str
    producer_task_id: str
    kind: ArtifactKind
    status: ArtifactStatus
    created_at: int
    updated_at: int
    title: str | None = None
    description: str | None = None
    content_json: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class Workspace:
    id: str
    name: str
    root_path: str
    created_at: int
    updated_at: int
    focus: str | None = None
    summary: str | None = None


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
    acceptance_criteria: list[str] = field(default_factory=list)
    out_of_scope: list[str] = field(default_factory=list)
    current_step: str | None = None
    plan: list[dict[str, Any]] = field(default_factory=list)
    changed_files: list[dict[str, Any]] = field(default_factory=list)
    commands: list[dict[str, Any]] = field(default_factory=list)
    verification: list[dict[str, Any]] = field(default_factory=list)
    summary: str | None = None
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
    seq: int = 0
    visibility: EventVisibility = "chat"


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


@dataclass(slots=True)
class ScheduledTask:
    id: str
    name: str
    prompt: str
    schedule: str
    status: ScheduledTaskStatus
    enabled: bool
    created_at: int
    updated_at: int
    last_run_at: int | None = None
    next_run_at: int | None = None


@dataclass(slots=True)
class ScheduledTaskRun:
    id: str
    task_id: str
    status: ScheduledRunStatus
    started_at: int
    finished_at: int | None = None
    summary: str | None = None
    error: str | None = None


@dataclass(slots=True)
class CollaborationTask:
    id: str
    title: str
    description: str | None
    status: CollaborationTaskStatus
    priority: int
    created_at: int
    updated_at: int
    session_id: str | None = None
    parent_task_id: str | None = None
    assigned_worker_id: str | None = None
    claimed_at: int | None = None
    completed_at: int | None = None
    dependencies: list[str] = field(default_factory=list)
    result: dict[str, Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AgentWorker:
    id: str
    name: str
    role: str
    status: AgentWorkerStatus
    created_at: int
    updated_at: int
    last_heartbeat_at: int
    current_task_id: str | None = None
    capabilities: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AgentMessage:
    id: str
    sender_worker_id: str
    kind: AgentMessageKind
    body: str
    created_at: int
    recipient_worker_id: str | None = None
    task_id: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    read_at: int | None = None


@dataclass(slots=True)
class ProposalRecord:
    id: str
    kind: ProposalKind
    session_id: str
    task_id: str
    proposal: dict[str, Any]
    status: ProposalStatus
    created_at: int
    updated_at: int
    source: dict[str, Any] = field(default_factory=dict)
    input_summary: str | None = None
    validation_reasons: list[str] = field(default_factory=list)
    applied_to: dict[str, Any] = field(default_factory=dict)
    model_id: str | None = None
    turn_id: str | None = None
