export type Identifier = string;

export type ApprovalMode = "strict" | "on_write_or_command" | "relaxed";
export type ProviderMode = "mock" | "openai-compatible";
export type SessionStatus = "active" | "archived" | "failed";
export type TaskStatus =
  | "queued"
  | "planning"
  | "running"
  | "waiting_approval"
  | "verifying"
  | "paused"
  | "completed"
  | "failed"
  | "cancelled";
export type TaskType = "chat" | "plan" | "edit" | "validate";
export type PatchStatus =
  | "proposed"
  | "approved"
  | "applied"
  | "rejected"
  | "failed";
export type ApprovalKind =
  | "apply_patch"
  | "run_command"
  | "delete_file"
  | "network_access";
export type ApprovalDecision = "approved" | "rejected";
export type ToolCallStatus = "started" | "completed" | "failed";
export type CommandStatus =
  | "running"
  | "completed"
  | "failed"
  | "timeout"
  | "cancelled"
  | "killed";
export type ScheduledTaskStatus = "active" | "disabled";
export type ScheduledRunStatus =
  | "queued"
  | "running"
  | "completed"
  | "failed"
  | "cancelled";
export type MessageRole = "user" | "assistant" | "system" | "tool";
export type TraceEventSource =
  | "provider"
  | "tool"
  | "approval"
  | "patch"
  | "command"
  | "task"
  | "assistant"
  | "runtime";
export type TraceEventType =
  | "provider.request"
  | "provider.response"
  | "tool.started"
  | "tool.completed"
  | "tool.failed"
  | "approval.requested"
  | "approval.resolved"
  | "patch.proposed"
  | "patch.approved"
  | "patch.applied"
  | "patch.failed"
  | "command.started"
  | "command.output"
  | "command.completed"
  | "command.failed"
  | string;

export interface GitStatusChange {
  status: string;
  path: string;
  originalPath?: string;
  raw: string;
}

export interface GitStatusRecord {
  workspaceRoot: string;
  cwd: string;
  branch?: string | null;
  upstream?: string | null;
  ahead: number;
  behind: number;
  changes: GitStatusChange[];
}

export interface GitDiffFile {
  status: string;
  path?: string;
  originalPath?: string;
}

export interface GitDiffRecord {
  workspaceRoot: string;
  cwd: string;
  staged: boolean;
  path?: string | null;
  files: GitDiffFile[];
  diff: string;
}

export interface WorkspaceRef {
  id: Identifier;
  name: string;
  rootPath: string;
  focus?: string | null;
  summary?: string | null;
  createdAt: number;
  updatedAt: number;
}

export interface SessionRecord {
  id: Identifier;
  workspaceId: Identifier;
  title: string;
  status: SessionStatus;
  summary?: string;
  createdAt: number;
  updatedAt: number;
}

export interface MessageRecord {
  id: Identifier;
  sessionId: Identifier;
  taskId?: Identifier;
  role: MessageRole;
  content: string;
  createdAt: number;
}

export interface PlanStep {
  id: Identifier;
  title: string;
  status: "pending" | "active" | "completed" | "failed";
  detail?: string;
}

export interface TaskChangedFile {
  path: string;
  status?: "added" | "modified" | "deleted" | "renamed" | string;
  additions?: number;
  deletions?: number;
  reason?: string;
  patchId?: Identifier | string | null;
}

export interface TaskCommandRun {
  id?: Identifier;
  command: string;
  cwd?: string;
  shell?: string;
  status?: CommandStatus | string;
  exitCode?: number | null;
  durationMs?: number | null;
  summary?: string;
  startedAt?: number;
  finishedAt?: number | null;
  stdoutPath?: string | null;
  stderrPath?: string | null;
  background?: boolean;
}

export interface TaskVerificationRecord {
  id?: Identifier;
  command?: string;
  status: "not_run" | "running" | "passed" | "failed" | "skipped" | string;
  exitCode?: number | null;
  durationMs?: number | null;
  summary?: string;
  startedAt?: number;
  finishedAt?: number | null;
}

export interface TaskRecord {
  id: Identifier;
  sessionId: Identifier;
  type: TaskType;
  status: TaskStatus;
  goal: string;
  acceptanceCriteria?: string[];
  outOfScope?: string[];
  currentStep?: string;
  plan?: PlanStep[];
  changedFiles?: TaskChangedFile[];
  commands?: TaskCommandRun[];
  verification?: TaskVerificationRecord[];
  summary?: string;
  resultSummary?: string;
  errorCode?: string;
  createdAt: number;
  updatedAt: number;
}

export interface ScheduledTaskRecord {
  id: Identifier;
  name: string;
  prompt: string;
  schedule: string;
  status: ScheduledTaskStatus;
  enabled: boolean;
  createdAt: number;
  updatedAt: number;
  lastRunAt?: number | null;
  nextRunAt?: number | null;
}

export interface ScheduledTaskRunRecord {
  id: Identifier;
  taskId: Identifier;
  status: ScheduledRunStatus;
  startedAt: number;
  finishedAt?: number | null;
  durationMs?: number | null;
  summary?: string | null;
  error?: string | null;
}

export interface ToolCallRecord {
  id: Identifier;
  taskId: Identifier;
  toolName: string;
  argumentsJson: string;
  status: ToolCallStatus;
  resultJson?: string;
  errorJson?: string;
  startedAt: number;
  finishedAt?: number;
}

export interface PatchRecord {
  id: Identifier;
  taskId: Identifier;
  workspaceId: Identifier;
  summary: string;
  diffText: string;
  status: PatchStatus;
  filesChanged: number;
  createdAt: number;
  updatedAt: number;
}

export interface CommandLogRecord {
  id: Identifier;
  taskId: Identifier;
  command: string;
  cwd: string;
  shell?: "powershell" | "bash" | "zsh";
  status: CommandStatus;
  exitCode?: number;
  startedAt: number;
  finishedAt?: number;
  durationMs?: number;
  stdoutPath?: string;
  stderrPath?: string;
  stdout?: string;
  stderr?: string;
}

export interface ApprovalRecord {
  id: Identifier;
  taskId: Identifier;
  kind: ApprovalKind;
  requestJson: string;
  decision?: ApprovalDecision;
  decidedBy?: string;
  createdAt: number;
  decidedAt?: number;
}

export interface TraceEventRecord<TPayload = unknown> {
  id: Identifier;
  taskId: Identifier;
  sessionId: Identifier;
  type: TraceEventType;
  source: TraceEventSource | string;
  relatedId?: Identifier | null;
  payload: TPayload;
  createdAt: number;
  sequence: number;
}

export type ErrorSource = "task" | "command" | "patch" | "provider" | "tool";

export interface ErrorRecord {
  id: Identifier;
  source: ErrorSource;
  sessionId: Identifier;
  taskId: Identifier;
  errorCode: string | null;
  errorMessage: string;
  timestamp: number;
  metadata: Record<string, unknown>;
}
