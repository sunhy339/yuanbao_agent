export type Identifier = string;

export type ApprovalMode = "strict" | "on_write_or_command" | "relaxed";
export type SessionStatus = "active" | "archived" | "failed";
export type TaskStatus =
  | "queued"
  | "running"
  | "waiting_approval"
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
  | "killed";
export type MessageRole = "user" | "assistant" | "system" | "tool";

export interface WorkspaceRef {
  id: Identifier;
  name: string;
  rootPath: string;
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

export interface TaskRecord {
  id: Identifier;
  sessionId: Identifier;
  type: TaskType;
  status: TaskStatus;
  goal: string;
  plan?: PlanStep[];
  resultSummary?: string;
  errorCode?: string;
  createdAt: number;
  updatedAt: number;
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
