import type { AppConfig, CapabilityName, CapabilityRule, ConfigPatch, ProviderConfig } from "./config";
import type {
  AgentProfileRecord,
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
  RuntimeHookAction,
  RuntimeHookEvent,
  RuntimeHookExecutionRecord,
  RuntimeHookFailureMode,
  RuntimeHookRecord,
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
import type { HahaCcServerMessage, YuanbaoServerMessage } from "./events";

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
  | "workspace.memory.init"
  | "workspace.fileList"
  | "workspace.fileSearch"
  | "workspace.fileRead"
  | "session.create"
  | "session.get"
  | "session.list"
  | "session.update"
  | "session.delete"
  | "session.compact"
  | "session.branch"
  | "session.truncate"
  | "message.send"
  | "message.list"
  | "message.delete"
  | "task.get"
  | "task.cancel"
  | "task.pause"
  | "task.resume"
  | "task.revertChanges"
  | "approval.submit"
  | "approval.allowAlways"
  | "config.get"
  | "config.update"
  | "permission.rule.clear"
  | "provider.test"
  | "diff.get"
  | "command_log.get"
  | "trace.list"
  | "events.after"
  | "events.yuanbaoAfter"
  | "events.hahaCcAfter"
  | "events.yuanbaoTeamSnapshot"
  | "events.hahaCcTeamSnapshot"
  | "provider_turn.list"
  | "context_snapshot.list"
  | "context_snapshot.get"
  | "context.budget"
  | "autonomy.report"
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
  | "worktree.delete"
  | "agent.profile.list"
  | "agent.profile.create"
  | "agent.profile.update"
  | "agent.profile.delete"
  | "agent.profile.validate"
  | "agent.profile.previewTools"
  | "runtime.ping"
  | "hook.create"
  | "hook.update"
  | "hook.delete"
  | "hook.list"
  | "hook.get"
  | "hook.listExecutions";

export type WorktreeOperationStatus = "status" | "diff" | "requestMergeApproval" | "merge" | "cleanup";

export interface WorkspaceOpenParams {
  path: string;
}

export interface WorkspaceMemoryClearParams {
  workspaceId: Identifier;
}

export interface WorkspaceMemoryInitParams {
  workspaceId: Identifier;
}

export interface WorkspaceFocusUpdateParams {
  workspaceId: Identifier;
  focus?: string | null;
}

export type WorkspaceFileEntryKind = "file" | "directory";

export interface WorkspaceFileEntry {
  name: string;
  path: string;
  kind: WorkspaceFileEntryKind;
  size?: number;
  modifiedAt?: number | null;
}

export interface WorkspaceFileListParams {
  workspaceRoot: string;
  path?: string;
  maxEntries?: number;
}

export interface WorkspaceFileListResult {
  rootPath: string;
  path: string;
  entries: WorkspaceFileEntry[];
  truncated?: boolean;
}

export interface WorkspaceFileSearchParams {
  workspaceRoot: string;
  query?: string;
  maxEntries?: number;
}

export interface WorkspaceFileSearchResult {
  rootPath: string;
  query: string;
  entries: WorkspaceFileEntry[];
  truncated?: boolean;
  scanned?: number;
}

export interface WorkspaceFileReadParams {
  workspaceRoot: string;
  path: string;
  maxBytes?: number;
}

export interface WorkspaceFileReadResult {
  rootPath: string;
  path: string;
  content?: string;
  bytes: number;
  truncated: boolean;
  binary: boolean;
  encoding?: string;
}

export interface OpenPathParams {
  path: string;
}

export interface OpenPathResult {
  path: string;
}

export type TerminalShell = "powershell" | "pwsh" | "cmd" | "bash" | "zsh" | "sh" | string;
export type TerminalSessionStatus = "running" | "exited" | "failed";
export type TerminalEventKind = "output" | "exit" | "error";

export interface TerminalStartParams {
  cwd?: string;
  shell?: TerminalShell;
  cols?: number;
  rows?: number;
}

export interface TerminalSessionRecord {
  id: Identifier;
  cwd: string;
  shell: string;
  status: TerminalSessionStatus;
  startedAt: number;
}

export interface TerminalStartResult {
  terminal: TerminalSessionRecord;
}

export interface TerminalWriteParams {
  terminalId: Identifier;
  data: string;
}

export interface TerminalResizeParams {
  terminalId: Identifier;
  cols: number;
  rows: number;
}

export interface TerminalStopParams {
  terminalId: Identifier;
}

export interface TerminalControlResult {
  terminal: TerminalSessionRecord;
}

export interface TerminalEvent {
  terminalId: Identifier;
  kind: TerminalEventKind;
  chunk?: string;
  exitCode?: number | null;
  message?: string;
  cwd?: string;
  shell?: string;
  ts?: number;
}

export interface GitLocalParams {
  cwd: string;
}

export interface GitLocalDiffParams extends GitLocalParams {
  path?: string;
}

export interface GitLocalCheckoutParams extends GitLocalParams {
  branch: string;
}

export interface GitLocalCommitParams extends GitLocalParams {
  message: string;
}

export interface GitLocalBranchRecord {
  name: string;
  current: boolean;
  local?: boolean;
  remote?: boolean;
  remoteRef?: string | null;
  checkedOut?: boolean;
  worktreePath?: string | null;
}

export interface GitLocalStatusFile {
  path: string;
  status: string;
  raw: string;
}

export interface GitLocalStatusResult {
  cwd: string;
  repoRoot: string;
  repoName?: string | null;
  branch: string;
  defaultBranch?: string | null;
  upstream?: string;
  ahead?: number;
  behind?: number;
  dirtyFiles: number;
  files: GitLocalStatusFile[];
  branches: GitLocalBranchRecord[];
  worktrees?: Array<{
    path: string;
    branch?: string | null;
    current?: boolean;
  }>;
  clean: boolean;
  rawStatus: string;
}

export interface GitLocalDiffResult {
  cwd: string;
  repoRoot: string;
  diff: string;
  stat: string;
  files: string[];
  truncated: boolean;
}

export interface GitLocalCommandResult {
  cwd: string;
  repoRoot: string;
  branch?: string;
  stdout: string;
  stderr: string;
  status?: GitLocalStatusResult;
}

export interface SessionLaunchRepositoryOptions {
  branch?: string | null;
  worktree?: boolean;
}

export interface SessionLaunchOptions {
  workDir?: string;
  repository?: SessionLaunchRepositoryOptions;
  permissionMode?: string;
}

export interface SessionCreateParams extends SessionLaunchOptions {
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

export interface SessionBranchParams {
  sessionId: Identifier;
  messageId: Identifier;
  title?: string;
}

export interface SessionTruncateParams {
  sessionId: Identifier;
  messageId: Identifier;
}

export interface MessageSendParams {
  sessionId: Identifier;
  content: string;
  attachments: string[];
  fileReferences?: string[];
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

export interface MessageDeleteParams {
  sessionId: Identifier;
  messageId: Identifier;
}

export interface TaskGetParams {
  taskId: Identifier;
}

export interface TaskCancelParams {
  taskId: Identifier;
}

export type TaskPauseParams = TaskCancelParams;
export type TaskResumeParams = TaskCancelParams;
export type TaskRevertChangesParams = TaskCancelParams;

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

export interface ApprovalAllowAlwaysParams {
  approvalId: Identifier;
  scope?: "capability";
}

export interface PermissionRuleClearParams {
  capability: CapabilityName | string;
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

export interface EventsAfterParams {
  sessionId: Identifier;
  afterSeq?: number;
  limit?: number;
}

export interface YuanbaoEventsAfterParams extends EventsAfterParams {}

export interface HahaCcEventsAfterParams extends YuanbaoEventsAfterParams {}

export interface YuanbaoTeamSnapshotParams {
  sessionId: Identifier;
}

export interface HahaCcTeamSnapshotParams extends YuanbaoTeamSnapshotParams {}

export interface ProviderTurnListParams {
  taskId: Identifier;
}

export interface ContextSnapshotListParams {
  taskId: Identifier;
}

export interface ContextSnapshotGetParams {
  snapshotId: Identifier;
}

export interface ContextBudgetParams {
  taskId?: Identifier;
  sessionId?: Identifier;
}

export interface AutonomyReportParams {
  taskId: Identifier;
}

export interface RuntimePingParams {
  sessionId?: Identifier;
  taskId?: Identifier;
}

export interface RuntimePingResult {
  ok: boolean;
  transport: string;
  yuanbaoMessages: YuanbaoServerMessage[];
  hahaCcMessages: HahaCcServerMessage[];
  connected?: Extract<YuanbaoServerMessage, { type: "connected" }> | null;
  pong?: Extract<YuanbaoServerMessage, { type: "pong" }> | null;
}

export interface WorkspaceOpenResult {
  workspace: WorkspaceRef;
}

export type WorkspaceMemoryClearResult = WorkspaceOpenResult;
export interface WorkspaceMemoryInitResult extends WorkspaceOpenResult {
  createdFiles: string[];
  existingFiles: string[];
}
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

export interface SessionBranchResult {
  sourceSession: SessionRecord;
  session: SessionRecord;
  sourceMessage: MessageRecord;
  messages: MessageRecord[];
  copiedCount: number;
}

export interface SessionTruncateResult {
  session: SessionRecord;
  targetMessage: MessageRecord;
  deletedMessages: MessageRecord[];
  messages: MessageRecord[];
  deletedCount: number;
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

export interface MessageDeleteResult {
  session: SessionRecord;
  message: MessageRecord;
  messages: MessageRecord[];
  deleted: boolean;
}

export interface TaskGetResult {
  task: TaskRecord;
}

export interface TaskListResult {
  tasks: TaskRecord[];
}

export interface WorktreeGetParams {
  worktreeId: Identifier;
  full?: boolean;
  includeFullDiff?: boolean;
  maxDiffBytes?: number;
  diffPreviewBytes?: number;
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
  reviewStatus?: string;
  reviewerSummary?: string;
  reviewer?: string;
  multiAgentWorktreeStrategy?: string | Record<string, unknown>;
  diffPreviewBytes?: number;
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
  approvalSummary?: Record<string, unknown>;
  review?: Record<string, unknown>;
  diffSummary?: Record<string, unknown>;
  multiAgentWorktreeStrategy?: Record<string, unknown>;
}

export interface WorktreeCleanupResult {
  worktreeId: Identifier;
  cleaned: boolean;
}

export interface TaskControlResult {
  task: TaskRecord;
}

export interface TaskRevertChangesResult {
  task: TaskRecord;
  patches: PatchRecord[];
  changedPaths: string[];
  reverted: boolean;
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
  task?: TaskRecord;
  worktreeMerge?: WorktreeMergeResult;
}

export interface ApprovalAllowAlwaysResult extends ApprovalSubmitResult {
  config: AppConfig;
  capability: CapabilityName | string;
  rule: CapabilityRule;
  scope: "capability" | string;
}

export interface ConfigGetResult {
  config: AppConfig;
}

export interface ConfigUpdateResult {
  config: AppConfig;
}

export interface PermissionRuleClearResult {
  config: AppConfig;
  capability: CapabilityName | string;
  removed: boolean;
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

export interface EventsAfterResult {
  events: TraceEventRecord[];
  truncated: boolean;
}

export interface YuanbaoEventsAfterResult {
  messages: YuanbaoServerMessage[];
  lastSeq: number;
  truncated: boolean;
}

export interface HahaCcEventsAfterResult extends YuanbaoEventsAfterResult {}

export interface YuanbaoTeamSnapshotResult {
  teamName: string;
  messages: YuanbaoServerMessage[];
}

export interface HahaCcTeamSnapshotResult extends YuanbaoTeamSnapshotResult {}

export interface ProviderTurnRecord {
  id: Identifier;
  task_id?: Identifier;
  taskId?: Identifier;
  session_id?: Identifier;
  sessionId?: Identifier;
  turn_index?: number;
  turnIndex?: number;
  model?: string | null;
  status?: string;
  responseUsage?: Record<string, unknown>;
  cacheUsage?: {
    cacheHit?: boolean;
    cachedTokens?: number;
    cacheCreationTokens?: number;
    [key: string]: unknown;
  };
  toolPolicyDecision?: Record<string, unknown>;
  roleSnapshot?: Record<string, unknown>;
  failureRecovery?: Record<string, unknown>;
  toolPolicyExplanation?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface ProviderTurnListResult {
  turns: ProviderTurnRecord[];
}

export interface ContextSnapshotRecord {
  id: Identifier;
  taskId: Identifier;
  sessionId: Identifier;
  providerTurnId?: Identifier | null;
  tokenEstimate?: number | null;
  maxContextTokens?: number | null;
  includedSections?: string[];
  trimmedSections?: unknown[];
  droppedSections?: unknown[];
  memoryIds?: Identifier[];
  toolCount?: number | null;
  skillId?: Identifier | null;
  promptLayers?: Array<Record<string, unknown>>;
  toolPolicyDecision?: Record<string, unknown>;
  roleSnapshot?: Record<string, unknown>;
  activeWorktree?: Record<string, unknown>;
  createdAt?: number;
  [key: string]: unknown;
}

export interface ContextSnapshotListResult {
  snapshots: Array<ContextSnapshotRecord | Record<string, unknown>>;
}

export interface ContextSnapshotGetResult {
  snapshot: ContextSnapshotRecord | Record<string, unknown> | null;
}

export interface ContextBudgetResult {
  maxContextTokens: number;
  latestSnapshot: ContextSnapshotRecord | null;
  compactions: Array<Record<string, unknown>>;
  tokenTrend: Array<{
    snapshotId: Identifier;
    tokenEstimate?: number | null;
    createdAt?: number;
  }>;
  promptLayers: Array<Record<string, unknown>>;
}

export interface AutonomyReportResult {
  task: TaskRecord;
  autonomyProfile?: Record<string, unknown> | null;
  agentSoulProfile?: Record<string, unknown> | null;
  routing?: Record<string, unknown>;
  metrics?: Record<string, unknown> | null;
  decisions: TraceEventRecord[];
  approvals: ApprovalRecord[];
  policyGateOutcomes: Record<string, number>;
  patches: PatchRecord[];
  commands: CommandLogRecord[];
  compactions: Array<Record<string, unknown>>;
  subagents: Array<Record<string, unknown>>;
  artifacts: Array<Record<string, unknown>>;
  memoryRecall: {
    memoryIds: Identifier[];
    count: number;
  };
  contextBudget: ContextSnapshotRecord | null;
  hookExecutions: RuntimeHookExecutionRecord[];
  scopeConflicts: Array<Record<string, unknown>>;
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

// --- Agent Profile ---

export interface AgentProfileListParams {
  enabledOnly?: boolean;
}

export interface AgentProfileCreateParams {
  id?: Identifier;
  name: string;
  description?: string;
  role?: AgentProfileRecord["role"];
  cwd?: string;
  enabled?: boolean;
  permissionMode?: AgentProfileRecord["permissionMode"];
  providerProfileId?: string;
  model?: string;
  skillIds?: string[];
  mcpServerIds?: string[];
  toolPolicy?: AgentProfileRecord["toolPolicy"];
  systemPrompt?: string;
}

export interface AgentProfileUpdateParams extends Partial<AgentProfileCreateParams> {
  agentId: Identifier;
}

export interface AgentProfileDeleteParams {
  agentId: Identifier;
}

export interface AgentProfileValidateParams {
  name?: string;
  role?: string;
  toolPolicy?: AgentProfileRecord["toolPolicy"];
}

export interface AgentProfilePreviewToolsParams {
  role?: string;
  permissionMode?: string;
  toolPolicy?: AgentProfileRecord["toolPolicy"];
}

export interface AgentProfileListResult {
  agents: AgentProfileRecord[];
}

export interface AgentProfileResult {
  agent: AgentProfileRecord;
}

export interface AgentProfileDeleteResult {
  deleted: boolean;
  agentId: Identifier;
}

export interface AgentProfileValidateResult {
  valid: boolean;
  errors: string[];
}

export interface AgentProfilePreviewToolsResult {
  allowedTools: string[];
  deniedTools: string[];
}

// --- Runtime Hooks ---

export interface HookListParams {
  workspaceId: Identifier;
  event?: RuntimeHookEvent | string;
}

export interface HookCreateParams {
  workspaceId: Identifier;
  name: string;
  event: RuntimeHookEvent | string;
  enabled?: boolean;
  priority?: number;
  conditions?: Record<string, unknown>;
  action?: RuntimeHookAction;
  authority?: Record<string, unknown>;
  timeoutMs?: number;
  retry?: Record<string, unknown>;
  onFailure?: RuntimeHookFailureMode | string;
}

export interface HookUpdateParams extends Partial<Omit<HookCreateParams, "workspaceId">> {
  hookId: Identifier;
}

export interface HookDeleteParams {
  hookId: Identifier;
}

export type HookGetParams = HookDeleteParams;

export interface HookListExecutionsParams {
  hookId?: Identifier;
  taskId?: Identifier;
  limit?: number;
}

export interface HookListResult {
  hooks: RuntimeHookRecord[];
}

export interface HookResult {
  hook: RuntimeHookRecord;
}

export interface HookDeleteResult {
  deleted: boolean;
  hookId: Identifier;
}

export interface HookListExecutionsResult {
  hookExecutions: RuntimeHookExecutionRecord[];
}
