import type {
  ApprovalKind,
  Identifier,
  PlanStep,
  TaskChangedFile,
  TaskCommandRun,
  TaskVerificationRecord,
  TaskStatus,
} from "./domain";

export type AgentEventType =
  | "session.updated"
  | "task.queued"
  | "task.started"
  | "task.updated"
  | "task.waiting_approval"
  | "task.completed"
  | "task.failed"
  | "task.cancelled"
  | "task.paused"
  | "task.resumed"
  | "provider.request"
  | "provider.response"
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
  acceptanceCriteria?: string[];
  outOfScope?: string[];
  currentStep?: string;
  changedFiles?: TaskChangedFile[];
  commands?: TaskCommandRun[];
  verification?: TaskVerificationRecord[];
  summary?: string;
  resultSummary?: string;
  errorCode?: string;
  context?: TaskContextPreviewPayload;
}

export interface TaskContextPreviewPayload {
  workspaceId?: string;
  workspaceName?: string;
  workspaceRoot?: string;
  projectFocus?: string | null;
  projectMemory?: string | null;
  searchQuery?: string;
  searchMode?: string;
  toolCount?: number;
  budgetStats?: {
    estimatedTokens?: number;
    estimatedInputTokens?: number;
    messageTokens?: number;
    toolSchemaTokens?: number;
    maxContextTokens?: number;
    droppedSections?: string[];
    trimmedSections?: string[];
  };
  taskFocus?: {
    taskId?: string;
    currentStep?: string | null;
    acceptanceCriteriaCount?: number;
    outOfScopeCount?: number;
  };
}

export interface SessionUpdatedPayload {
  summary?: string | null;
  title?: string;
  status?: string;
}

export interface AssistantTokenPayload {
  delta: string;
}

export interface ProviderTracePayload {
  providerRequestId?: Identifier;
  model?: string;
  request?: Record<string, unknown>;
  response?: Record<string, unknown>;
  status?: number;
  error?: unknown;
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
