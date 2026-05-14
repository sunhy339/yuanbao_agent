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
  role: "user" | "assistant" | "system" | "tool";
  content: string;
  taskId?: string;
  streaming?: boolean;
  placeholder?: boolean;
  createdAt?: number;
  updatedAt?: number;
  toolName?: string;
  status?: string;
}

export interface SessionWorkspaceApproval {
  id: string;
  title: string;
  kind?: string;
  status: string;
  summary?: string;
  requestedAt?: number;
  risk?: "low" | "medium" | "high";
  parametersPreview?: string;
  fullInput?: string;
  command?: string;
  cwd?: string;
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
  agentType?: string;
}

export interface SessionWorkspaceToolCall {
  id: string;
  toolName: string;
  status: string;
  time?: number;
  resultSummary?: string;
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
    maxContextTokens?: number | null;
    droppedSections?: string[];
    trimmedSections?: string[];
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
  onReject?(approvalId: string): void | Promise<void>;
  onLoadPatch?(patchId: string): void | Promise<void>;
  onCopyPatchPath?(patchId: string, path: string): void | Promise<void>;
  onCopyRuntimeText?(label: string, text: string): void | Promise<void>;
  onRefreshCommandJob?(commandId: string): void | Promise<void>;
  onStopCommandJob?(commandId: string): void | Promise<void>;
  onRefreshTask?(): void | Promise<void>;
  onStopTask?(taskId: string): void | Promise<void>;
  onRefreshTrace?(): void | Promise<void>;
  taskBusyAction?: "refresh" | "stop" | null;
  busyId?: string | null;
  messagesLoading?: boolean;
}

export interface DiffLine {
  type: "add" | "remove" | "context" | "header";
  content: string;
}

export interface RuntimeTimelineItem {
  id: string;
  kind: "approval" | "patch" | "trace" | "tool" | "command" | "task" | "memory";
  sourceId?: string;
  title: string;
  status?: string;
  summary?: string;
  meta?: string[];
  riskLevel?: "low" | "medium" | "high";
  code?: string;
  rawDetail?: string;
  time?: number;
  durationMs?: number;
  diffLines?: DiffLine[];
  visibility?: "chat" | "panel" | "trace";
  taskId?: string;
  agentType?: string;
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
    };
