export interface SessionWorkspaceSession {
  id: string;
  title: string;
  status?: string;
  summary?: string | null;
  updatedAt?: number;
  messageCount?: number;
  tokenCount?: number;
}

export interface SessionWorkspacePlanStep {
  id: string;
  title: string;
  status?: string;
  summary?: string;
  detail?: string;
  durationMs?: number;
}

export interface SessionWorkspaceWorktree {
  id: string;
  taskId?: string;
  baseRef?: string;
  branchName?: string;
  worktreePath: string;
  status?: string;
  cleanupPolicy?: string | null;
  mergePolicy?: string | null;
  lastStatus?: {
    mergeVerification?: Array<{
      command?: string;
      status?: string;
      exitCode?: number | null;
      summary?: string;
      durationMs?: number | null;
    }>;
    [key: string]: unknown;
  } | null;
  createdAt?: number;
  updatedAt?: number;
}

export interface SessionWorkspaceWorktreeStatus {
  dirtyFiles?: number;
  files?: string[];
  branch?: string | null;
  upstream?: string | null;
  ahead?: number;
  behind?: number;
  clean?: boolean;
  rawStatus?: string;
  error?: string;
}

export interface SessionWorkspaceWorktreeDiff {
  diffStat?: string;
  diff?: string;
  mode?: string;
  preview?: string;
  bytes?: number;
  previewBytes?: number;
  truncated?: boolean;
  fullDiffAvailable?: boolean;
  files?: unknown[];
  error?: string;
}

export interface SessionWorkspaceActiveTask {
  id: string;
  status?: string;
  goal?: string;
  createdAt?: number;
  updatedAt?: number;
  acceptanceCriteria?: string[];
  outOfScope?: string[];
  currentStep?: string;
  changedFiles?: Array<{
    path: string;
    status?: string;
    additions?: number;
    deletions?: number;
    reason?: string;
    patchId?: string | null;
  }>;
  commands?: Array<{
    id?: string;
    command: string;
    cwd?: string;
    shell?: string;
    status?: string;
    exitCode?: number | null;
    durationMs?: number | null;
    summary?: string;
    stdoutPath?: string | null;
    stderrPath?: string | null;
    background?: boolean;
  }>;
  verification?: Array<{
    id?: string;
    command?: string;
    status: string;
    exitCode?: number | null;
    durationMs?: number | null;
    summary?: string;
  }>;
  summary?: string;
  resultSummary?: string;
  planSteps?: SessionWorkspacePlanStep[];
  activeWorktree?: SessionWorkspaceWorktree | null;
  mainWorkflow?: {
    automation?: Record<string, unknown>;
    budgets?: Record<string, unknown>;
    convergence?: Record<string, unknown>;
    userTakeover?: Record<string, unknown>;
    [key: string]: unknown;
  } | null;
}

export interface SessionWorkspaceCollaborator {
  id: string;
  name: string;
  status?: string;
  mode?: string;
  healthState?: string;
  healthReason?: string;
  heartbeatAgeMs?: number;
  lastHeartbeatAt?: number;
  claimedTaskId?: string;
  summary?: string;
  updatedAt?: number;
}

export interface SessionWorkspaceChildTask {
  id: string;
  title: string;
  status?: string;
  workerId?: string;
  workerName?: string;
  summary?: string;
  attention?: string;
  updatedAt?: number;
  createdAt?: number;
  completedAt?: number;
  durationMs?: number;
  agentType?: string;
  artifactCount?: number;
  errorMessage?: string;
}

export interface SessionWorkspaceChildTaskResult {
  id: string;
  taskId?: string;
  title?: string;
  status?: string;
  summary?: string;
  updatedAt?: number;
}

export interface SessionWorkspaceCollaboration {
  workers?: SessionWorkspaceCollaborator[];
  childTasks?: SessionWorkspaceChildTask[];
  results?: SessionWorkspaceChildTaskResult[];
  healthSummary?: {
    healthy: number;
    stale: number;
    offline: number;
    total: number;
  };
}

export interface SessionWorkspaceMessage {
  id: string;
  sessionId?: string;
  role: "user" | "assistant" | "system" | "tool";
  content: string;
  taskId?: string;
  kind?: string;
  streaming?: boolean;
  placeholder?: boolean;
  createdAt?: number;
  updatedAt?: number;
  toolName?: string;
  status?: string;
  metadata?: Record<string, unknown>;
}

export interface SessionWorkspaceApproval {
  id: string;
  title: string;
  kind?: string;
  patchId?: string;
  status: string;
  summary?: string;
  filesChanged?: number;
  changedPaths?: string[];
  diff?: string;
  requestedAt?: number;
  risk?: "low" | "medium" | "high";
  parametersPreview?: string;
  fullInput?: string;
  previewRows?: Array<{ label: string; value: string }>;
  previewSections?: PreviewSectionView[];
  command?: string;
  cwd?: string;
  completionEvidence?: {
    gateStatus?: string;
    evidenceLevel?: string;
    status?: string;
    summary: string;
    metrics: Array<{ label: string; value: string }>;
    issues: string[];
    advisorEvidenceAdapters?: {
      status?: string;
      counts?: {
        ready?: number;
        approvalRequired?: number;
        blocked?: number;
        missingAdapter?: number;
        satisfied?: number;
        total?: number;
      };
      adapters: Array<{
        adapterKind?: string;
        status?: string;
        executorState?: string;
        summary?: string;
      }>;
    };
    audit?: {
      approvalCounts?: {
        total?: number;
        approved?: number;
        rejected?: number;
        pending?: number;
      };
      approvals?: Array<{
        approvalId?: string;
        kind?: string;
        decision?: string;
        decidedBy?: string;
        gateStatus?: string;
        evidenceLevel?: string;
        summary?: string;
        reviewStatus?: string;
        verificationStatus?: string;
      }>;
      completionAdvisor?: {
        accepted?: boolean;
        source?: string;
        confidence?: number;
        proposalRecordId?: string;
        fallback_reason?: string;
      };
    };
    reviewConclusion?: {
      approvalId?: string;
      decision?: string;
      decidedBy?: string;
      decidedAt?: number;
      gateStatus?: string;
      summary?: string;
    };
  };
}

export interface PreviewSectionItemView {
  id: string;
  title: string;
  description?: string;
  meta?: string[];
}

export interface PreviewSectionView {
  kind: "items";
  title: string;
  items: PreviewSectionItemView[];
}

export interface SessionWorkspacePatchFile {
  path: string;
  status?: string;
  additions?: number;
  deletions?: number;
  diff?: string;
}

export interface SessionWorkspacePatch {
  id: string;
  taskId?: string;
  summary: string;
  status: string;
  filesChanged?: number;
  additions?: number;
  deletions?: number;
  updatedAt?: number;
  files?: SessionWorkspacePatchFile[];
  diff?: string;
}

export interface SessionWorkspaceTrace {
  id: string;
  type: string;
  source?: string;
  payload?: unknown;
  time?: number;
  title?: string;
  summary?: string;
  detail?: string;
  status?: string;
  durationMs?: number;
  tokenCount?: number;
  stdout?: string;
  stderr?: string;
  visibility?: "chat" | "panel" | "trace";
  taskId?: string;
  toolName?: string;
  agentType?: string;
}

export interface SessionWorkspaceToolCall {
  id: string;
  toolUseId?: string;
  parentToolUseId?: string;
  toolGroupId?: string;
  toolIndex?: number;
  toolTotal?: number;
  toolOperationId?: string;
  toolOperationLabel?: string;
  toolCategory?: string;
  toolPhaseId?: string;
  toolPhaseLabel?: string;
  toolSemanticParentId?: string;
  toolSemanticParentLabel?: string;
  toolName: string;
  status: string;
  target?: string;
  displayTitle?: string;
  displaySummary?: string;
  displayTarget?: string;
  displayKind?: string;
  inputSummary?: string;
  taskId?: string;
  time?: number;
  resultSummary?: string;
  resultPreview?: Array<{ label: string; value: string }>;
  durationMs?: number;
  tokenCount?: number;
  argsPreview?: string;
  input?: string;
  output?: string;
  rawInput?: string;
  rawOutput?: string;
  stdout?: string;
  stderr?: string;
}

export interface SessionWorkspaceBackgroundJob {
  id: string;
  toolUseId?: string;
  parentToolUseId?: string;
  toolGroupId?: string;
  toolIndex?: number;
  toolTotal?: number;
  toolOperationId?: string;
  toolOperationLabel?: string;
  toolCategory?: string;
  toolPhaseId?: string;
  toolPhaseLabel?: string;
  toolSemanticParentId?: string;
  toolSemanticParentLabel?: string;
  target?: string;
  inputSummary?: string;
  command: string;
  status: string;
  cwd?: string;
  shell?: string;
  summary?: string;
  startedAt?: number;
  finishedAt?: number;
  durationMs?: number;
  exitCode?: number | null;
  stdout?: string;
  stderr?: string;
  stdoutPath?: string;
  stderrPath?: string;
  isBackground?: boolean;
}

export interface SessionWorkspaceComposerContext {
  cwd?: string;
  repo?: string;
  branch?: string;
  model?: string;
  permissionMode?: string;
}

export interface SessionWorkspaceContextPreview {
  projectFocus?: string | null;
  projectMemory?: string | null;
  workspaceRoot?: string | null;
  searchQuery?: string | null;
  searchMode?: string | null;
  toolCount?: number | null;
  budgetStats?: {
    estimatedTokens?: number | null;
    estimatedInputTokens?: number | null;
    messageTokens?: number | null;
    toolSchemaTokens?: number | null;
    stablePrefixTokens?: number | null;
    inputTokens?: number | null;
    outputTokens?: number | null;
    cacheReadTokens?: number | null;
    updatedAt?: number | null;
    estimated?: boolean | null;
    promptCache?: {
      enabled?: boolean | null;
      targetFillRatio?: number | null;
      targetContextTokens?: number | null;
      maxStableContextTokens?: number | null;
      stablePrefixTokens?: number | null;
    } | null;
    maxContextTokens?: number | null;
    includedSections?: string[];
    stablePrefixSections?: string[];
    dynamicTailSections?: string[];
    droppedSections?: string[];
    trimmedSections?: string[];
    promptLayers?: Array<{
      name?: string;
      tokenEstimate?: number | null;
      [key: string]: unknown;
    }>;
  } | null;
  taskFocus?: {
    currentStep?: string | null;
    acceptanceCriteriaCount?: number | null;
    outOfScopeCount?: number | null;
  } | null;
}

export interface SessionWorkspaceProps {
  session: SessionWorkspaceSession | null;
  activeTask: SessionWorkspaceActiveTask | null;
  messages: SessionWorkspaceMessage[];
  taskCount?: number;
  collaboration?: SessionWorkspaceCollaboration;
  backgroundJobs?: SessionWorkspaceBackgroundJob[];
  approvals?: SessionWorkspaceApproval[];
  patches?: SessionWorkspacePatch[];
  traces?: SessionWorkspaceTrace[];
  toolCalls?: SessionWorkspaceToolCall[];
  composerContext?: SessionWorkspaceComposerContext;
  contextPreview?: SessionWorkspaceContextPreview;
  onApprove?(approvalId: string): void | Promise<void>;
  onApproveForSession?(approvalId: string): void | Promise<void>;
  onApproveAlways?(approvalId: string): void | Promise<void>;
  onReject?(approvalId: string): void | Promise<void>;
  onLoadPatch?(patchId: string): void | Promise<void>;
  onCopyPatchPath?(patchId: string, path: string): void | Promise<void>;
  onCopyRuntimeText?(label: string, text: string): void | Promise<void>;
  onRevertTaskChanges?(taskId: string): void | Promise<void>;
  onSubmitUserQuestionAnswer?(message: SessionWorkspaceMessage, answer: string): void | Promise<void>;
  onQuoteMessage?(text: string): void;
  onContinueFromMessage?(message: SessionWorkspaceMessage): void | Promise<void>;
  onBranchFromMessage?(message: SessionWorkspaceMessage): void | Promise<void>;
  onDeleteMessage?(message: SessionWorkspaceMessage): void | Promise<void>;
  onRefreshCommandJob?(commandId: string): void | Promise<void>;
  onStopCommandJob?(commandId: string): void | Promise<void>;
  onRefreshTask?(): void | Promise<void>;
  onPauseTask?(taskId: string): void | Promise<void>;
  onResumeTask?(taskId: string): void | Promise<void>;
  onStopTask?(taskId: string): void | Promise<void>;
  onRefreshTrace?(): void | Promise<void>;
  onRefreshWorktree?(worktreeId: string): void | Promise<void>;
  onLoadWorktreeDiff?(worktreeId: string, full?: boolean): void | Promise<void>;
  onMergeWorktree?(worktreeId: string): void | Promise<void>;
  onCleanupWorktree?(worktreeId: string, force?: boolean): void | Promise<void>;
  worktreeStatus?: SessionWorkspaceWorktreeStatus | null;
  worktreeDiff?: SessionWorkspaceWorktreeDiff | null;
  taskBusyAction?: "refresh" | "stop" | "pause" | "resume" | null;
  busyId?: string | null;
  patchBusyId?: string | null;
  worktreeBusyAction?: "status" | "diff" | "requestMergeApproval" | "merge" | "cleanup" | null;
  worktreeError?: string | null;
  messagesLoading?: boolean;
}

export interface DiffLine {
  type: "add" | "remove" | "context" | "header";
  content: string;
}

export interface RuntimeTimelineItem {
  id: string;
  kind: "approval" | "patch" | "trace" | "tool" | "command" | "task" | "memory" | "completion";
  sourceId?: string;
  patchId?: string;
  toolUseId?: string;
  parentToolUseId?: string;
  toolGroupId?: string;
  toolIndex?: number;
  toolTotal?: number;
  toolOperationId?: string;
  toolOperationLabel?: string;
  toolCategory?: string;
  toolPhaseId?: string;
  toolPhaseLabel?: string;
  toolSemanticParentId?: string;
  toolSemanticParentLabel?: string;
  title: string;
  status?: string;
  summary?: string;
  meta?: string[];
  riskLevel?: "low" | "medium" | "high";
  code?: string;
  rawDetail?: string;
  completionEvidence?: SessionWorkspaceApproval["completionEvidence"];
  previewRows?: Array<{ label: string; value: string }>;
  previewSections?: PreviewSectionView[];
  supportsAlwaysAllow?: boolean;
  time?: number;
  durationMs?: number;
  diffLines?: DiffLine[];
  visibility?: "chat" | "panel" | "trace";
  taskId?: string;
  toolName?: string;
  agentType?: string;
  groupKey?: string;
  superseded?: boolean;
  syntheticTime?: boolean;
}

export interface ToolRuntimePresentation {
  kind: RuntimeTimelineItem["kind"];
  title: string;
  summary?: string;
  meta: string[];
  code?: string;
  durationMs?: number;
}

export type ConversationActivityItem =
  | {
      id: string;
      kind: "message";
      order: number;
      time?: number;
      message: SessionWorkspaceMessage;
    }
  | {
      id: string;
      kind: "runtime";
      order: number;
      time?: number;
      runtime: RuntimeTimelineItem;
    }
  | {
      id: string;
      kind: "worklog";
      groupKind?: "tool_group";
      order: number;
      time?: number;
      runtimeItems: RuntimeTimelineItem[];
    };
