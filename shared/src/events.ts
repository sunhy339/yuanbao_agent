import type {
  ApprovalKind,
  Identifier,
  PlanStep,
  TaskStatus,
} from "./domain";

export type AgentEventType =
  | "task.queued"
  | "task.started"
  | "task.updated"
  | "task.waiting_approval"
  | "task.completed"
  | "task.failed"
  | "task.cancelled"
  | "assistant.token"
  | "assistant.message.completed"
  | "tool.started"
  | "tool.completed"
  | "tool.failed"
  | "command.started"
  | "command.output"
  | "command.completed"
  | "command.failed"
  | "patch.proposed"
  | "approval.requested"
  | "approval.resolved";

export interface AgentEventEnvelope<TPayload = unknown> {
  eventId: Identifier;
  sessionId: Identifier;
  taskId: Identifier;
  type: AgentEventType;
  ts: number;
  payload: TPayload;
}

export interface TaskUpdatedPayload {
  status: TaskStatus;
  plan?: PlanStep[];
  detail?: string;
}

export interface AssistantTokenPayload {
  delta: string;
}

export interface ToolLifecyclePayload {
  toolCallId: Identifier;
  toolName: string;
  arguments?: Record<string, unknown>;
}

export interface CommandOutputPayload {
  commandId: Identifier;
  stream: "stdout" | "stderr";
  chunk: string;
}

export interface CommandLifecyclePayload {
  commandId: Identifier;
  command?: string;
  cwd?: string;
  shell?: string;
  status?: "running" | "completed" | "failed" | "timeout" | "killed";
  exitCode?: number;
  durationMs?: number;
  summary?: string;
  error?: unknown;
}

export interface PatchProposedPayload {
  patchId: Identifier;
  summary: string;
  filesChanged: number;
}

export interface ApprovalRequestedPayload {
  approvalId: Identifier;
  taskId: Identifier;
  kind: ApprovalKind;
  request: Record<string, unknown>;
  patchId?: Identifier;
}

export interface ApprovalResolvedPayload {
  approvalId: Identifier;
  taskId: Identifier;
  decision: "approved" | "rejected";
}
