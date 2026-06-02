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
  | "assistant_progress"
  | "assistant.message.completed"
  | "content_start"
  | "content_delta"
  | "thinking"
  | "tool_use_complete"
  | "tool_result"
  | "permission_request"
  | "message_complete"
  | "status"
  | "api_retry"
  | "system_notification"
  | "compact_summary"
  | "goal_event"
  | "memory_event"
  | "background_task"
  | "task_summary"
  | "plan_update"
  | "ask_user_question"
  | "computer_use_permission_request"
  | "computer_use_permission"
  | "message.created"
  | "message.delta"
  | "message.completed"
  | "message.failed"
  | "tool.started"
  | "tool.progress"
  | "tool.output"
  | "tool.completed"
  | "tool.failed"
  | "tool.blocked"
  | "command.started"
  | "command.output"
  | "command.completed"
  | "command.cancelled"
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
  seq?: number;
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
    includedSections?: string[];
    droppedSections?: string[];
    trimmedSections?: string[];
    promptLayers?: Array<{
      name?: string;
      tokenEstimate?: number;
      [key: string]: unknown;
    }>;
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
  target?: string;
  inputSummary?: string;
  parentToolUseId?: Identifier;
  toolGroupId?: Identifier;
  toolIndex?: number;
  toolTotal?: number;
  toolOperationId?: Identifier;
  toolOperationLabel?: string;
  toolCategory?: string;
  toolPhaseId?: string;
  toolPhaseLabel?: string;
  toolSemanticParentId?: string;
  toolSemanticParentLabel?: string;
}

export interface ContentDeltaPayload {
  messageId?: Identifier;
  text?: string;
  toolInput?: string;
  toolOutput?: string;
  outputStream?: "stdout" | "stderr" | "activity" | string;
  toolUseId?: Identifier;
  toolName?: string;
  target?: string;
  inputSummary?: string;
  parentToolUseId?: Identifier;
  toolGroupId?: Identifier;
  toolIndex?: number;
  toolTotal?: number;
  toolOperationId?: Identifier;
  toolOperationLabel?: string;
  toolCategory?: string;
  toolPhaseId?: string;
  toolPhaseLabel?: string;
  toolSemanticParentId?: string;
  toolSemanticParentLabel?: string;
  step?: number;
}

export interface ThinkingPayload {
  text: string;
  messageId?: Identifier;
  source?: "reasoning_summary" | "status" | string;
}

export interface AssistantProgressPayload {
  text?: string;
  summary?: string;
  message?: string;
  title?: string;
  phase?: string;
  status?: string;
  step?: number | string;
  model?: string;
  source?: string;
  operation?: string;
  stream?: string;
  pressure?: string;
  consumedSteps?: number;
  remainingSteps?: number;
  maxSteps?: number;
  recommendedAction?: string;
  toolName?: string;
  toolUseId?: Identifier;
  target?: string;
  inputSummary?: string;
  resultSummary?: string;
  durationMs?: number;
  toolGroupId?: Identifier;
  toolIndex?: number;
  toolTotal?: number;
  toolOperationId?: Identifier;
  toolOperationLabel?: string;
  toolCategory?: string;
  toolPhaseId?: string;
  toolPhaseLabel?: string;
  toolSemanticParentId?: string;
  toolSemanticParentLabel?: string;
  reason?: string;
  parentToolUseId?: Identifier;
  isError?: boolean;
  resultPreview?: Array<{ label: string; value: string }>;
}

export interface ToolUseCompletePayload {
  toolName: string;
  toolUseId: Identifier;
  input: unknown;
  target?: string;
  inputSummary?: string;
  parentToolUseId?: Identifier;
  toolGroupId?: Identifier;
  toolIndex?: number;
  toolTotal?: number;
  toolOperationId?: Identifier;
  toolOperationLabel?: string;
  toolCategory?: string;
  toolPhaseId?: string;
  toolPhaseLabel?: string;
  toolSemanticParentId?: string;
  toolSemanticParentLabel?: string;
}

export interface ToolResultPayload {
  toolUseId: Identifier;
  toolName?: string;
  content: unknown;
  isError: boolean;
  parentToolUseId?: Identifier;
  target?: string;
  inputSummary?: string;
  resultSummary?: string;
  resultPreview?: Array<{ label: string; value: string }>;
  durationMs?: number;
  toolGroupId?: Identifier;
  toolIndex?: number;
  toolTotal?: number;
  toolOperationId?: Identifier;
  toolOperationLabel?: string;
  toolCategory?: string;
  toolPhaseId?: string;
  toolPhaseLabel?: string;
  toolSemanticParentId?: string;
  toolSemanticParentLabel?: string;
}

export interface PermissionRequestPayload {
  requestId: Identifier;
  toolName: string;
  toolUseId?: Identifier;
  input: unknown;
  description?: string;
  preview?: Array<{ label: string; value: string }>;
  filesChanged?: number;
  changedPaths?: string[];
  diffText?: string;
  resolved?: boolean;
  decision?: "approved" | "rejected" | string;
  decidedBy?: string;
  decidedAt?: number;
}

export interface ComputerUsePermissionPayload {
  approvalId: Identifier;
  requestId?: Identifier;
  app?: string;
  action?: string;
  permission?: string;
  summary?: string;
  details?: string;
  status?: string;
  resolved?: boolean;
  decision?: "approved" | "rejected" | string;
  decidedBy?: string;
  decidedAt?: number;
  request?: Record<string, unknown>;
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
  parentToolUseId?: Identifier;
  toolGroupId?: Identifier;
  toolIndex?: number;
  toolTotal?: number;
  toolOperationId?: Identifier;
  toolOperationLabel?: string;
  toolName: string;
  arguments?: Record<string, unknown>;
  target?: string;
  inputSummary?: string;
  result?: unknown;
  resultSummary?: string;
  resultPreview?: Array<{ label: string; value: string }>;
  resultPreviewStreamed?: boolean;
  durationMs?: number;
  reason?: string;
  error?: unknown;
  failureKind?: string;
  recoveryHint?: string;
  recoveryDecision?: unknown;
  toolCategory?: string;
  toolPhaseId?: string;
  toolPhaseLabel?: string;
  toolSemanticParentId?: string;
  toolSemanticParentLabel?: string;
}

export interface ToolOutputPayload {
  toolCallId?: Identifier;
  toolUseId?: Identifier;
  parentToolUseId?: Identifier;
  toolGroupId?: Identifier;
  toolIndex?: number;
  toolTotal?: number;
  toolOperationId?: Identifier;
  toolOperationLabel?: string;
  toolName?: string;
  target?: string;
  inputSummary?: string;
  toolCategory?: string;
  toolPhaseId?: string;
  toolPhaseLabel?: string;
  toolSemanticParentId?: string;
  toolSemanticParentLabel?: string;
  chunk?: string;
  delta?: string;
  toolOutput?: string;
  message?: string;
  summary?: string;
  text?: string;
  outputStream?: "stdout" | "stderr" | "activity" | "result_preview" | string;
  stream?: "stdout" | "stderr" | "activity" | "result_preview" | string;
}

export interface CommandOutputPayload {
  commandId: Identifier;
  toolUseId?: Identifier;
  toolName?: string;
  target?: string;
  inputSummary?: string;
  parentToolUseId?: Identifier;
  toolGroupId?: Identifier;
  toolIndex?: number;
  toolTotal?: number;
  toolOperationId?: Identifier;
  toolOperationLabel?: string;
  toolCategory?: string;
  toolPhaseId?: string;
  toolPhaseLabel?: string;
  toolSemanticParentId?: string;
  toolSemanticParentLabel?: string;
  stream: "stdout" | "stderr";
  chunk: string;
}

export interface CommandLifecyclePayload {
  commandId: Identifier;
  toolUseId?: Identifier;
  toolName?: string;
  target?: string;
  inputSummary?: string;
  parentToolUseId?: Identifier;
  toolGroupId?: Identifier;
  toolIndex?: number;
  toolTotal?: number;
  toolOperationId?: Identifier;
  toolOperationLabel?: string;
  toolCategory?: string;
  toolPhaseId?: string;
  toolPhaseLabel?: string;
  toolSemanticParentId?: string;
  toolSemanticParentLabel?: string;
  command?: string;
  cwd?: string;
  shell?: string;
  status?: "running" | "completed" | "failed" | "timeout" | "killed" | "cancelled";
  exitCode?: number;
  durationMs?: number;
  stdoutPath?: string;
  stderrPath?: string;
  background?: boolean;
  summary?: string;
  error?: unknown;
}

export interface PatchProposedPayload {
  patchId: Identifier;
  summary: string;
  filesChanged: number;
  changedPaths?: string[];
  diffText?: string;
}

export interface ApprovalRequestedPayload {
  approvalId: Identifier;
  taskId: Identifier;
  kind: ApprovalKind;
  request: Record<string, unknown>;
  preview?: Array<{ label: string; value: string }>;
  patchId?: Identifier;
  filesChanged?: number;
  changedPaths?: string[];
  diffText?: string;
}

export interface ApprovalResolvedPayload {
  approvalId: Identifier;
  taskId: Identifier;
  kind?: ApprovalKind;
  request?: Record<string, unknown>;
  preview?: Array<{ label: string; value: string }>;
  filesChanged?: number;
  changedPaths?: string[];
  diffText?: string;
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
