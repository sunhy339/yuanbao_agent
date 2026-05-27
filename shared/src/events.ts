import type {
  ApprovalKind,
  Identifier,
  MessageKind,
  MessageRecord,
  MessageStatus,
  PlanStep,
  TaskChangedFile,
  TaskCommandRun,
  TaskVerificationRecord,
  TaskStatus,
  EventVisibility,
  WorktreeRecord,
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
  | "task.created"
  | "task.routing.decided"
  | "task.worktree.bound"
  | "task.worktree.bind_failed"
  | "task.worktree.merged"
  | "task.worktree.merge_failed"
  | "task.budget.pressure"
  | "task.budget.exhausted"
  | "agent.decision.budget_convergence"
  | "task.planning.subtask.started"
  | "task.planning.subtask.completed"
  | "provider.request"
  | "provider.response"
  | "assistant.token"
  | "assistant.message.completed"
  | "content_start"
  | "content_delta"
  | "thinking"
  | "tool_use_complete"
  | "tool_result"
  | "permission_request"
  | "message_complete"
  | "status"
  | "message.created"
  | "message.delta"
  | "message.completed"
  | "message.failed"
  | "tool.started"
  | "tool.completed"
  | "tool.failed"
  | "command.started"
  | "command.output"
  | "command.completed"
  | "command.failed"
  | "patch.proposed"
  | "approval.requested"
  | "approval.resolved"
  | "task.supplement.received"
  | "task.supplement.consumed";

export interface AgentEventEnvelope<TPayload = unknown> {
  eventId: Identifier;
  sessionId: Identifier;
  taskId: Identifier;
  type: AgentEventType;
  ts: number;
  payload: TPayload;
  visibility?: EventVisibility;
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
  routing?: {
    activeWorktree?: WorktreeRecord | null;
    [key: string]: unknown;
  } | null;
  activeWorktree?: WorktreeRecord | null;
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
    stablePrefixTokens?: number;
    promptCache?: {
      enabled?: boolean;
      targetFillRatio?: number;
      targetContextTokens?: number;
      maxStableContextTokens?: number;
      stablePrefixTokens?: number;
    };
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
  messageId?: Identifier;
}

export interface TokenUsage {
  inputTokens?: number;
  outputTokens?: number;
  totalTokens?: number;
  promptTokens?: number;
  completionTokens?: number;
  cachedTokens?: number;
  [key: string]: unknown;
}

export interface ContentStartPayload {
  blockType: "text" | "tool_use";
  messageId?: Identifier;
  toolName?: string;
  toolUseId?: Identifier;
  parentToolUseId?: Identifier;
}

export interface ContentDeltaPayload {
  messageId?: Identifier;
  text?: string;
  toolInput?: string;
  toolUseId?: Identifier;
  toolName?: string;
  step?: number;
}

export interface ThinkingPayload {
  text: string;
  messageId?: Identifier;
}

export interface ToolUseCompletePayload {
  toolName: string;
  toolUseId: Identifier;
  input: unknown;
  parentToolUseId?: Identifier;
}

export interface ToolResultPayload {
  toolUseId: Identifier;
  toolName?: string;
  content: unknown;
  isError: boolean;
  parentToolUseId?: Identifier;
}

export interface PermissionRequestPayload {
  requestId: Identifier;
  toolName: string;
  toolUseId?: Identifier;
  input: unknown;
  description?: string;
}

export interface ChatMessageCompletePayload {
  messageId?: Identifier;
  usage?: TokenUsage | null;
  content?: string;
}

export interface ChatStatusPayload {
  state: "thinking" | "tool_executing" | "permission_pending" | "idle" | "streaming" | string;
  verb?: string;
  elapsed?: number;
  tokens?: number;
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
  decidedBy?: string;
  decidedAt?: number;
  completionReviewConclusion?: Record<string, unknown>;
}

// --- Message lifecycle events (P1.3 / P1.4) ---

export interface MessageCreatedPayload {
  message: MessageRecord;
}

export interface MessageDeltaPayload {
  delta: string;
  messageId: Identifier;
  taskId?: Identifier;
}

export interface MessageCompletedPayload {
  messageId: Identifier;
  content?: string;
}

export interface MessageFailedPayload {
  messageId: Identifier;
  content: string;
  errorCode?: string;
}

// --- Supplement inbox events ---

export interface TaskSupplementReceivedPayload {
  inboxEntryId: Identifier;
  messageId: Identifier;
  content: string;
}

export interface TaskSupplementConsumedPayload {
  count: number;
  entryIds: Identifier[];
  step: number;
}
