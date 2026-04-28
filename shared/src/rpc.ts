import type { AppConfig, ConfigPatch, ProviderConfig } from "./config";
import type {
  ApprovalRecord,
  CommandLogRecord,
  GitDiffRecord,
  GitStatusRecord,
  Identifier,
  MessageRecord,
  PatchRecord,
  ProviderMode,
  ScheduledTaskRecord,
  ScheduledTaskRunRecord,
  SessionRecord,
  TaskRecord,
  TraceEventRecord,
  WorkspaceRef,
} from "./domain";

export interface JsonRpcRequest<TParams = unknown> {
  jsonrpc: "2.0";
  id: Identifier;
  method: RpcMethod;
  params: TParams;
}

export interface JsonRpcResponse<TResult = unknown> {
  jsonrpc: "2.0";
  id: Identifier;
  result?: TResult;
  error?: RpcError;
}

export interface RpcError {
  code:
    | "INVALID_ARGUMENT"
    | "NOT_FOUND"
    | "PATH_OUT_OF_SCOPE"
    | "PERMISSION_DENIED"
    | "APPROVAL_REQUIRED"
    | "TOOL_EXECUTION_FAILED"
    | "COMMAND_TIMEOUT"
    | "PATCH_APPLY_FAILED"
    | "MODEL_PROVIDER_ERROR"
    | "TOKEN_BUDGET_EXCEEDED"
    | "TASK_CANCELLED"
    | "DB_ERROR"
    | "INTERNAL_ERROR";
  message: string;
  details?: Record<string, unknown>;
  retryable: boolean;
}

export type RpcMethod =
  | "workspace.open"
  | "workspace.focus.update"
  | "workspace.memory.clear"
  | "session.create"
  | "session.get"
  | "session.list"
  | "message.send"
  | "message.list"
  | "task.get"
  | "task.cancel"
  | "task.pause"
  | "task.resume"
  | "approval.submit"
  | "config.get"
  | "config.update"
  | "provider.test"
  | "diff.get"
  | "command_log.get"
  | "trace.list"
  | "schedule.create"
  | "schedule.list"
  | "schedule.update"
  | "schedule.toggle"
  | "schedule.run_now"
  | "schedule.logs"
  | "task.list"
  | "log.export"
  | "errors.list";

export interface WorkspaceOpenParams {
  path: string;
}

export interface WorkspaceMemoryClearParams {
  workspaceId: Identifier;
}

export interface WorkspaceFocusUpdateParams {
  workspaceId: Identifier;
  focus?: string | null;
}

export interface SessionCreateParams {
  workspaceId: Identifier;
  title: string;
}

export interface MessageSendParams {
  sessionId: Identifier;
  content: string;
  attachments: string[];
}

export interface MessageListParams {
  sessionId: Identifier;
  limit?: number;
}

export interface TaskGetParams {
  taskId: Identifier;
}

export interface TaskCancelParams {
  taskId: Identifier;
}

export type TaskPauseParams = TaskCancelParams;
export type TaskResumeParams = TaskCancelParams;

export interface TaskListParams {
  sessionId?: Identifier;
}

export interface ScheduledTaskCreateParams {
  name: string;
  prompt: string;
  schedule: string;
  enabled?: boolean;
  status?: ScheduledTaskRecord["status"];
}

export interface ScheduledTaskUpdateParams {
  taskId: Identifier;
  name?: string;
  prompt?: string;
  schedule?: string;
  enabled?: boolean;
  status?: ScheduledTaskRecord["status"];
}

export interface ScheduledTaskToggleParams {
  taskId: Identifier;
  enabled: boolean;
}

export interface ScheduledTaskRunNowParams {
  taskId: Identifier;
}

export interface ScheduledTaskLogsParams {
  taskId?: Identifier;
  limit?: number;
}

export interface ApprovalSubmitParams {
  approvalId: Identifier;
  decision: "approved" | "rejected";
}

export interface DiffGetParams {
  patchId: Identifier;
}

export interface ProviderTestParams {
  provider?: ConfigPatch["provider"] | ProviderConfig;
  profileId?: string;
}

export interface CommandLogGetParams {
  commandId: Identifier;
}

export interface CommandLogListParams {
  taskId?: Identifier;
  sessionId?: Identifier;
  status?: CommandLogRecord["status"];
  limit?: number;
}

export type CommandCancelParams = CommandLogGetParams;

export interface TraceListParams {
  taskId: Identifier;
  limit?: number;
}

export interface WorkspaceOpenResult {
  workspace: WorkspaceRef;
}

export type WorkspaceMemoryClearResult = WorkspaceOpenResult;
export type WorkspaceFocusUpdateResult = WorkspaceOpenResult;

export interface SessionCreateResult {
  session: SessionRecord;
}

export interface SessionGetResult {
  session: SessionRecord;
}

export interface SessionListResult {
  sessions: SessionRecord[];
}

export interface MessageSendResult {
  task: TaskRecord;
}

export interface MessageListResult {
  messages: MessageRecord[];
}

export interface TaskGetResult {
  task: TaskRecord;
}

export interface TaskListResult {
  tasks: TaskRecord[];
}

export interface TaskControlResult {
  task: TaskRecord;
}

export interface ScheduledTaskResult {
  task: ScheduledTaskRecord;
}

export interface ScheduledTaskListResult {
  tasks: ScheduledTaskRecord[];
}

export interface ScheduledTaskRunNowResult {
  run: ScheduledTaskRunRecord;
  task?: ScheduledTaskRecord;
}

export interface ScheduledTaskLogsResult {
  logs: ScheduledTaskRunRecord[];
}

export interface ApprovalSubmitResult {
  approval: ApprovalRecord;
}

export interface ConfigGetResult {
  config: AppConfig;
}

export interface ConfigUpdateResult {
  config: AppConfig;
}

export type ConfigUpdateParams = ConfigPatch & {
  config?: ConfigPatch;
};

export interface ProviderTestResult {
  ok: boolean;
  status: "ok" | "mocked" | "not_configured" | "missing_env" | "unsupported" | "failed";
  message: string;
  profileId?: string;
  profileName?: string;
  providerMode?: ProviderMode | string;
  model?: string;
  baseUrl?: string;
  checkedEnvVarName?: string;
  envVarName?: string;
  lastCheckedAt?: number;
  lastStatus?: string;
  lastErrorSummary?: string | null;
  source: "runtime" | "mock-fallback";
  details?: Record<string, unknown>;
}

export interface DiffGetResult {
  patch: PatchRecord;
  diffText: string;
}

export interface CommandLogGetResult {
  commandLog: CommandLogRecord;
}

export interface CommandLogListResult {
  commandLogs: CommandLogRecord[];
}

export interface CommandCancelResult {
  commandLog: CommandLogRecord;
  cancelled: boolean;
}

export interface TraceListResult {
  traceEvents: TraceEventRecord[];
}

export type GitStatusResult = GitStatusRecord;
export type GitDiffResult = GitDiffRecord;

export interface LogExportParams {
  sessionId?: Identifier;
}

export interface LogExportResult {
  exportedAt: number;
  sessions: SessionRecord[];
  tasks: TaskRecord[];
  messages: MessageRecord[];
  commandLogs: CommandLogRecord[];
  patches: PatchRecord[];
  approvals: ApprovalRecord[];
  traceEvents: TraceEventRecord[];
  config: Record<string, unknown>;
}

export interface ErrorsListParams {
  sessionId?: Identifier;
  taskId?: Identifier;
  source?: string;
  limit?: number;
}

export interface ErrorsListResult {
  errors: import("./domain").ErrorRecord[];
  summary: {
    totalErrors: number;
    bySource: Record<string, number>;
  };
}
