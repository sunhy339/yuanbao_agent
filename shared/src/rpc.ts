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
  McpServerRecord,
  McpToolRefreshResult,
  ScheduledTaskRecord,
  ScheduledTaskRunRecord,
  SessionRecord,
  SkillPresetRecord,
  SkillUsageRecord,
  TaskRecord,
  TaskVerificationRecord,
  TraceEventRecord,
  WorktreeGitDiffRecord,
  WorktreeGitStatusRecord,
  WorktreeRecord,
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
  | "session.update"
  | "session.delete"
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
  | "errors.list"
  | "metrics.list"
  | "skill.list"
  | "skill.create"
  | "skill.update"
  | "skill.delete"
  | "mcp.server.list"
  | "mcp.server.create"
  | "mcp.server.update"
  | "mcp.server.delete"
  | "mcp.tools.refresh"
  | "skill.usage"
  | "command.status"
  | "command.list"
  | "command.cancel"
  | "stats.summary"
  | "stats.trace"
  | "worker.run_child_task"
  | "worktree.get"
  | "worktree.getByTask"
  | "worktree.list"
  | "worktree.status"
  | "worktree.diff"
  | "worktree.requestMergeApproval"
  | "worktree.merge"
  | "worktree.cleanup"
  | "worktree.update"
  | "worktree.delete";

export type WorktreeOperationStatus = "status" | "diff" | "requestMergeApproval" | "merge" | "cleanup";

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

export interface SessionUpdateParams {
  sessionId: Identifier;
  title?: string;
  status?: string;
}

export interface SessionDeleteParams {
  sessionId: Identifier;
}

export interface MessageSendParams {
  sessionId: Identifier;
  content: string;
  attachments: string[];
  taskId?: Identifier;
  mode?: "new" | "supplement" | "queued";
  newTask?: boolean;
  background?: boolean;
  clientMessageId?: string;
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

export interface SessionUpdateResult {
  session: SessionRecord;
}

export interface SessionDeleteResult {
  session: SessionRecord;
}

export interface SessionCompactParams {
  sessionId: string;
  maxTokens?: number;
}

export interface SessionCompactResult {
  tokensBefore: number;
  tokensAfter: number;
  summary: string | null;
  strategy: string;
  compactionId?: string;
}

export interface MessageSendResult {
  task: TaskRecord;
  userMessage?: MessageRecord;
  assistantMessage?: MessageRecord;
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

export interface WorktreeGetParams {
  worktreeId: Identifier;
}

export interface WorktreeGetByTaskParams {
  taskId: Identifier;
}

export interface WorktreeCleanupParams {
  worktreeId: Identifier;
  force?: boolean;
}

export interface WorktreeMergeParams {
  worktreeId: Identifier;
  approved?: boolean;
  approvalId?: Identifier;
  targetBranch?: string;
  verificationCommands?: string[];
  verificationTimeoutMs?: number;
}

export interface WorktreeGetResult {
  worktree?: WorktreeRecord | null;
}

export interface WorktreeStatusResult {
  worktree: WorktreeRecord;
  gitStatus?: WorktreeGitStatusRecord | GitStatusRecord | { error?: string };
}

export interface WorktreeDiffResult {
  worktree: WorktreeRecord;
  diff?: WorktreeGitDiffRecord | GitDiffRecord | { error?: string; diff?: string; files?: unknown[] };
}

export interface WorktreeMergeApprovalResult {
  approval: ApprovalRecord;
  worktree: WorktreeRecord;
  gitStatus?: WorktreeGitStatusRecord | GitStatusRecord | { error?: string };
  diff?: WorktreeGitDiffRecord | GitDiffRecord | { error?: string; diff?: string; files?: unknown[] };
  verification?: TaskVerificationRecord[];
}

export interface WorktreeMergeResult {
  worktreeId?: Identifier | null;
  merged: boolean;
  branchName?: string;
  targetBranch?: string;
  error?: string;
  result?: Record<string, unknown>;
  verification?: TaskVerificationRecord[];
}

export interface WorktreeCleanupResult {
  worktreeId: Identifier;
  cleaned: boolean;
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
  worktreeMerge?: WorktreeMergeResult;
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

export interface MetricsListParams {
  sessionId?: Identifier;
  limit?: number;
}

export interface MetricsListResult {
  metrics: import("./domain").TaskMetricRecord[];
}

export interface SkillListParams {
  category?: string;
}

export interface SkillCreateParams {
  id?: Identifier;
  skillId?: Identifier;
  name: string;
  description?: string;
  system_prompt?: string;
  systemPrompt?: string;
  tool_whitelist?: string[];
  toolWhitelist?: string[];
  parameter_constraints?: Record<string, unknown>;
  parameterConstraints?: Record<string, unknown>;
  category?: string;
}

export interface SkillUpdateParams extends Partial<SkillCreateParams> {
  skillId: Identifier;
}

export interface SkillDeleteParams {
  skillId: Identifier;
}

export interface SkillListResult {
  skills: SkillPresetRecord[];
}

export interface SkillResult {
  skill: SkillPresetRecord;
}

export interface SkillDeleteResult {
  deleted: boolean;
  skillId: Identifier;
}

export interface McpServerListParams {
  enabledOnly?: boolean;
}

export interface McpServerCreateParams {
  id?: Identifier;
  serverId?: Identifier;
  name: string;
  transport?: McpServerRecord["transport"];
  command?: string | null;
  args?: string[];
  url?: string | null;
  headers?: Record<string, string>;
  env?: Record<string, string>;
  enabled?: boolean;
}

export interface McpServerUpdateParams extends Partial<McpServerCreateParams> {
  serverId: Identifier;
}

export interface McpServerDeleteParams {
  serverId: Identifier;
}

export interface McpToolsRefreshParams {
  serverId?: Identifier;
}

export interface McpServerListResult {
  servers: McpServerRecord[];
}

export interface McpServerResult {
  server: McpServerRecord;
}

export interface McpServerDeleteResult {
  deleted: boolean;
  serverId: Identifier;
}

export type McpToolsRefreshRpcResult = McpToolRefreshResult;

// --- Skill Import ---

export interface SkillImportParams {
  filePath: string;
}

export interface SkillImportResult {
  imported: SkillPresetRecord[];
  skipped: string[];
  errors: Array<{ name: string; error: string }>;
}

// --- Skill Usage ---

export interface SkillUsageParams {
  skillId?: Identifier;
  taskId?: Identifier;
  sessionId?: Identifier;
  limit?: number;
  offset?: number;
}

export interface SkillUsageResult {
  usage: SkillUsageRecord[];
  total: number;
}

// --- Command Status / List ---

export interface CommandStatusParams {
  commandId: Identifier;
}

export interface CommandStatusResult {
  commandLog: CommandLogRecord;
  isRunning: boolean;
}

export interface CommandListResult {
  runningCommandIds: Identifier[];
  count: number;
}

// --- Stats ---

export interface StatsSummaryParams {
  sessionId?: Identifier;
  limit?: number;
}

export interface StatsSummaryResult {
  workerHealth?: Record<string, unknown>;
  toolCallStats?: Record<string, number>;
  errorDistribution?: Record<string, number>;
}

export interface StatsTraceParams {
  traceId: Identifier;
}

export interface StatsTraceResult {
  spans: TraceEventRecord[];
}

// --- Worker Child Task ---

export interface WorkerRunChildTaskParams {
  parentTaskId: Identifier;
  sessionId: Identifier;
  prompt: string;
  toolWhitelist?: string[];
}

export interface WorkerRunChildTaskResult {
  task: TaskRecord;
}
