import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import type {
  ApprovalRecord,
  ApprovalAllowAlwaysParams,
  ApprovalAllowAlwaysResult,
  ApprovalSubmitParams,
  ApprovalSubmitResult,
  AgentProfileCreateParams,
  AgentProfileDeleteParams,
  AgentProfileDeleteResult,
  AgentProfileListParams,
  AgentProfileListResult,
  AgentProfilePreviewToolsParams,
  AgentProfilePreviewToolsResult,
  AgentProfileResult,
  AgentProfileUpdateParams,
  AgentProfileValidateParams,
  AgentProfileValidateResult,
  AgentEventEnvelope,
  AppConfig,
  CommandCancelParams,
  CommandLogGetParams,
  CommandLogGetResult,
  CommandLogListParams,
  CommandLogListResult,
  CommandLogRecord,
  ConfigGetResult,
  ConfigUpdateParams,
  ConfigUpdateResult,
  DiffGetParams,
  DiffGetResult,
  GitLocalCheckoutParams,
  GitLocalCommandResult,
  GitLocalCommitParams,
  GitLocalDiffParams,
  GitLocalDiffResult,
  GitLocalParams,
  GitLocalStatusResult,
  HookCreateParams,
  HookDeleteParams,
  HookDeleteResult,
  HookGetParams,
  HookListExecutionsParams,
  HookListExecutionsResult,
  HookListParams,
  HookListResult,
  HookResult,
  HookUpdateParams,
  EventsAfterParams,
  EventsAfterResult,
  YuanbaoEventsAfterParams,
  YuanbaoEventsAfterResult,
  YuanbaoTeamSnapshotParams,
  YuanbaoTeamSnapshotResult,
  MessageListParams,
  MessageListResult,
  MessageDeleteParams,
  MessageDeleteResult,
  MessageSendParams,
  MessageSendResult,
  MessageRecord,
  McpServerCreateParams,
  McpServerDeleteParams,
  McpServerDeleteResult,
  McpServerListParams,
  McpServerListResult,
  McpServerRecord,
  McpServerResult,
  McpServerUpdateParams,
  McpToolsRefreshParams,
  McpToolsRefreshRpcResult,
  OpenPathParams,
  OpenPathResult,
  PatchRecord,
  PermissionRuleClearParams,
  PermissionRuleClearResult,
  ProviderTestParams,
  ProviderTestResult,
  RuntimePingParams,
  RuntimePingResult,
  ScheduledTaskCreateParams,
  ScheduledTaskListResult,
  ScheduledTaskLogsParams,
  ScheduledTaskLogsResult,
  ScheduledTaskRecord,
  ScheduledTaskResult,
  ScheduledTaskRunNowParams,
  ScheduledTaskRunNowResult,
  ScheduledTaskRunRecord,
  ScheduledTaskToggleParams,
  ScheduledTaskUpdateParams,
  SessionCreateParams,
  SessionCreateResult,
  SessionBranchParams,
  SessionBranchResult,
  SessionDeleteParams,
  SessionDeleteResult,
  SessionCompactParams,
  SessionCompactResult,
  SessionTruncateParams,
  SessionTruncateResult,
  SessionListResult,
  SessionUpdateParams,
  SessionUpdateResult,
  SessionRecord,
  SkillCreateParams,
  SkillDeleteParams,
  SkillDeleteResult,
  SkillImportParams,
  SkillImportResult,
  SkillListParams,
  SkillListResult,
  SkillPresetRecord,
  SkillResult,
  SkillUpdateParams,
  TaskCancelParams,
  TaskControlResult,
  TaskGetResult,
  TaskPauseParams,
  TaskListParams,
  TaskListResult,
  TaskRecord,
  TaskRevertChangesParams,
  TaskRevertChangesResult,
  TaskResumeParams,
  TerminalControlResult,
  TerminalEvent,
  TerminalResizeParams,
  TerminalStartParams,
  TerminalStartResult,
  TerminalStopParams,
  TerminalWriteParams,
  TraceEventRecord,
  TraceListParams,
  TraceListResult,
  WorktreeCleanupParams,
  WorktreeCleanupResult,
  WorktreeDiffResult,
  WorktreeGetByTaskParams,
  WorktreeGetParams,
  WorktreeGetResult,
  WorktreeMergeApprovalResult,
  WorktreeMergeParams,
  WorktreeMergeResult,
  WorktreeStatusResult,
  WorkspaceFileListParams,
  WorkspaceFileListResult,
  WorkspaceFileSearchParams,
  WorkspaceFileSearchResult,
  WorkspaceFileReadParams,
  WorkspaceFileReadResult,
  WorkspaceFocusUpdateParams,
  WorkspaceFocusUpdateResult,
  WorkspaceMemoryInitParams,
  WorkspaceMemoryInitResult,
  WorkspaceMemoryClearParams,
  WorkspaceMemoryClearResult,
  WorkspaceOpenResult,
  YuanbaoServerMessage,
} from "@shared";

function withTimeout<T>(promise: Promise<T>, ms: number, message: string): Promise<T> {
  let timer: ReturnType<typeof setTimeout>;
  return Promise.race([
    promise,
    new Promise<never>(
      (_, reject) =>
        (timer = setTimeout(() => reject(new Error(message)), ms)),
    ),
  ]).finally(() => clearTimeout(timer));
}

const EVENT_CHANNEL = "agent://event";
const YUANBAO_EVENT_CHANNEL = "yuanbao://message";
const TERMINAL_EVENT_CHANNEL = "terminal://event";
const RUNTIME_BRIDGE_UNAVAILABLE_MESSAGE =
  "桌面运行时桥接不可用。请通过 Tauri 桌面应用打开 Yuanbao Agent，或为测试/预览显式启用浏览器预览模式。";

export type RuntimeConfig = AppConfig & Required<Pick<AppConfig, "search">>;
export type RuntimeCommandLog = CommandLogRecord;

export interface CommandCancelResult {
  commandLog: CommandLogRecord;
  cancelled?: boolean;
}

export type AppPathKind = "logs" | "data" | "skills";

export interface AppPathOpenResult {
  path: string;
}

export interface YuanbaoConnectionOptions extends RuntimePingParams {
  keepAliveMs?: number;
  onKeepAliveError?: (reason: unknown) => void;
}

// Client-side cache for Tauri results — used for optimistic updates and local reads
interface ClientCache {
  config: RuntimeConfig | null;
  workspace: WorkspaceOpenResult["workspace"] | null;
  sessions: SessionRecord[];
  scheduledTasks: Record<string, ScheduledTaskRecord>;
  scheduledRuns: ScheduledTaskRunRecord[];
  skills: Record<string, SkillPresetRecord>;
  mcpServers: Record<string, McpServerRecord>;
}

const clientCache: ClientCache = {
  config: null,
  workspace: null,
  sessions: [],
  scheduledTasks: {},
  scheduledRuns: [],
  skills: {},
  mcpServers: {},
};

export interface HostStatus {
  runtimeTransport: string;
  eventChannel: string;
  yuanbaoEventChannel: string;
  runtimeRunning: boolean;
  repoRoot: string;
  pythonModule: string;
}

export type ComputerUseCapabilityState = "ready" | "partial" | "guarded" | "pending" | "disabled" | "blocked";

export interface ComputerUseProbeCapability {
  id: string;
  label: string;
  state: ComputerUseCapabilityState;
  detail: string;
}

export interface ComputerUseProbeResult {
  status: string;
  checkedAt: number;
  platform: string;
  desktopBridge: boolean;
  runtimeRunning: boolean;
  capabilities: ComputerUseProbeCapability[];
}

function isTauriBridgeAvailable(): boolean {
  return typeof window !== "undefined" && ("__TAURI_INTERNALS__" in window || "__TAURI__" in window);
}

function assertRuntimeBridgeAvailable(command: string): void {
  if (!isTauriBridgeAvailable()) {
    throw new Error(`${RUNTIME_BRIDGE_UNAVAILABLE_MESSAGE} 命令：${command}。`);
  }
}

// --- Normalizers ---

function normalizeBoolean(value: unknown): boolean {
  return value === true || value === 1 || value === "1";
}

function normalizeSkillRecord(raw: SkillPresetRecord): SkillPresetRecord {
  return {
    ...raw,
    systemPrompt: raw.systemPrompt ?? raw.system_prompt,
    toolWhitelist: raw.toolWhitelist ?? raw.tool_whitelist ?? [],
    parameterConstraints: raw.parameterConstraints ?? raw.parameter_constraints ?? {},
    isBuiltin: raw.isBuiltin ?? normalizeBoolean(raw.is_builtin),
    createdAt: raw.createdAt ?? raw.created_at,
    updatedAt: raw.updatedAt ?? raw.updated_at,
  };
}

function normalizeMcpServerRecord(raw: McpServerRecord): McpServerRecord {
  return {
    ...raw,
    enabled: normalizeBoolean(raw.enabled),
    args: raw.args ?? [],
    headers: raw.headers ?? {},
    env: raw.env ?? {},
    createdAt: raw.createdAt ?? raw.created_at,
    updatedAt: raw.updatedAt ?? raw.updated_at,
  };
}

function sortSkills(skills: SkillPresetRecord[]): SkillPresetRecord[] {
  return [...skills].sort((left, right) => (right.updatedAt ?? 0) - (left.updatedAt ?? 0));
}

function sortMcpServers(servers: McpServerRecord[]): McpServerRecord[] {
  return [...servers].sort((left, right) => (right.updatedAt ?? 0) - (left.updatedAt ?? 0));
}

function sortSessions(sessions: SessionRecord[]): SessionRecord[] {
  return [...sessions].sort((left, right) => right.updatedAt - left.updatedAt);
}

function sortScheduledTasks(tasks: ScheduledTaskRecord[]): ScheduledTaskRecord[] {
  return [...tasks].sort((left, right) => right.createdAt - left.createdAt);
}

function sortScheduledRuns(runs: ScheduledTaskRunRecord[]): ScheduledTaskRunRecord[] {
  return [...runs].sort((left, right) => right.startedAt - left.startedAt);
}

// --- Config merge ---

function mergeRuntimeConfig(current: RuntimeConfig, next: ConfigUpdateParams): RuntimeConfig {
  const patch = "config" in next && next.config ? next.config : next;
  const curAutonomy = current.autonomy ?? {};
  const curAgentSoul = current.agentSoul ?? {};
  const curPermissions = current.permissions ?? { preset: undefined, capabilities: {} };
  const curTools = current.tools ?? {};
  const curWorktree = current.worktree ?? {};
  const curUi = current.ui ?? {};
  return {
    ...current,
    ...patch,
    provider: {
      ...current.provider,
      ...patch.provider,
    },
    workspace: {
      ...current.workspace,
      ...patch.workspace,
    },
    search: {
      ...current.search,
      ...patch.search,
    },
    policy: {
      ...current.policy,
      ...patch.policy,
    },
    autonomy: {
      ...curAutonomy,
      ...patch.autonomy,
      activeProfileId: patch.autonomy?.activeProfileId ?? curAutonomy.activeProfileId,
      profiles: patch.autonomy?.profiles ?? curAutonomy.profiles,
    },
    agentSoul: {
      ...curAgentSoul,
      ...patch.agentSoul,
      activeProfileId: patch.agentSoul?.activeProfileId ?? curAgentSoul.activeProfileId,
      workspaceInstructions: patch.agentSoul?.workspaceInstructions ?? curAgentSoul.workspaceInstructions,
      sessionOverrideEnabled: patch.agentSoul?.sessionOverrideEnabled ?? curAgentSoul.sessionOverrideEnabled,
      profiles: patch.agentSoul?.profiles ?? curAgentSoul.profiles,
    },
    permissions: {
      preset: patch.permissions?.preset ?? curPermissions.preset,
      capabilities: patch.permissions && "capabilities" in patch.permissions
        ? { ...(patch.permissions.capabilities ?? {}) }
        : { ...curPermissions.capabilities },
    },
    tools: {
      ...curTools,
      ...patch.tools,
      runCommand: {
        ...curTools.runCommand,
        ...patch.tools?.runCommand,
      },
    },
    worktree: {
      ...curWorktree,
      ...patch.worktree,
    },
    ui: {
      ...curUi,
      ...patch.ui,
    },
  };
}

// --- Cache helpers ---

function rememberSession(session: SessionRecord): void {
  const withoutCurrent = clientCache.sessions.filter((item) => item.id !== session.id);
  clientCache.sessions = sortSessions([...withoutCurrent, session]);
}

function rememberProviderTestResult(result: ProviderTestResult): void {
  const profileId = result.profileId;
  if (!profileId || !clientCache.config?.provider.profiles?.length) {
    return;
  }

  const profiles = clientCache.config.provider.profiles.map((profile) =>
    profile.id === profileId
      ? {
          ...profile,
          lastCheckedAt: result.lastCheckedAt,
          lastStatus: result.lastStatus ?? result.status,
          lastErrorSummary:
            typeof result.lastErrorSummary === "string" ? result.lastErrorSummary : undefined,
        }
      : profile,
  );

  clientCache.config = mergeRuntimeConfig(clientCache.config, {
    provider: {
      profiles,
    },
  });
}

function resetClientCache(): void {
  clientCache.sessions = [];
  clientCache.scheduledTasks = {};
  clientCache.scheduledRuns = [];
  clientCache.skills = {};
  clientCache.mcpServers = {};
}

// --- Tauri invoke wrappers ---

async function invokeOrReject<T>(command: string, payload?: unknown): Promise<T> {
  assertRuntimeBridgeAvailable(command);
  try {
    return await invoke<T>(command, payload as Record<string, unknown> | undefined);
  } catch (reason) {
    throw reason instanceof Error ? reason : new Error(String(reason));
  }
}

function invokePayloadOrReject<T>(command: string, payload: unknown): Promise<T> {
  return invokeOrReject<T>(command, { payload });
}

export class RuntimeClient {
  async getHostStatus(): Promise<HostStatus> {
    return invokeOrReject<HostStatus>("host_status");
  }

  async probeComputerUse(): Promise<ComputerUseProbeResult> {
    return invokeOrReject<ComputerUseProbeResult>("computer_use_probe");
  }

  async runtimePing(payload: RuntimePingParams = {}): Promise<RuntimePingResult> {
    return invokePayloadOrReject<RuntimePingResult>("runtime_ping", payload);
  }

  canOpenLocalAppPaths(): boolean {
    return isTauriBridgeAvailable();
  }

  async openAppPath(kind: AppPathKind): Promise<AppPathOpenResult> {
    return invokeOrReject<AppPathOpenResult>("open_app_path", { kind });
  }

  async openWorkspace(path: string): Promise<WorkspaceOpenResult> {
    const result = await invokeOrReject<WorkspaceOpenResult>("workspace_open", { path });
    clientCache.workspace = result.workspace;
    resetClientCache();
    clientCache.config = clientCache.config
      ? mergeRuntimeConfig(clientCache.config, {
          workspace: {
            ignore: clientCache.config.workspace.ignore,
            rootPath: result.workspace.rootPath,
            writableRoots: [result.workspace.rootPath],
          },
        })
      : clientCache.config;
    return result;
  }

  async clearWorkspaceMemory(payload: WorkspaceMemoryClearParams): Promise<WorkspaceMemoryClearResult> {
    const result = await invokePayloadOrReject<WorkspaceMemoryClearResult>("workspace_memory_clear", payload);
    clientCache.workspace = result.workspace;
    return result;
  }

  async initWorkspaceMemory(payload: WorkspaceMemoryInitParams): Promise<WorkspaceMemoryInitResult> {
    const result = await invokePayloadOrReject<WorkspaceMemoryInitResult>("workspace_memory_init", payload);
    clientCache.workspace = result.workspace;
    return result;
  }

  async updateWorkspaceFocus(payload: WorkspaceFocusUpdateParams): Promise<WorkspaceFocusUpdateResult> {
    const result = await invokePayloadOrReject<WorkspaceFocusUpdateResult>("workspace_focus_update", payload);
    clientCache.workspace = result.workspace;
    return result;
  }

  async createSession(payload: SessionCreateParams): Promise<SessionCreateResult> {
    const result = await invokePayloadOrReject<SessionCreateResult>("session_create", payload);
    rememberSession(result.session);
    return result;
  }

  async listSessions(): Promise<SessionListResult> {
    const result = await invokeOrReject<SessionListResult>("session_list");
    clientCache.sessions = sortSessions(result.sessions);
    return result;
  }

  async updateSession(payload: SessionUpdateParams): Promise<SessionUpdateResult> {
    return invokePayloadOrReject<SessionUpdateResult>("session_update", payload);
  }

  async deleteSession(payload: SessionDeleteParams): Promise<SessionDeleteResult> {
    return invokePayloadOrReject<SessionDeleteResult>("session_delete", payload);
  }

  async compactSession(payload: SessionCompactParams): Promise<SessionCompactResult> {
    return invokePayloadOrReject<SessionCompactResult>("session_compact", payload);
  }

  async branchSession(payload: SessionBranchParams): Promise<SessionBranchResult> {
    const result = await invokePayloadOrReject<SessionBranchResult>("session_branch", payload);
    rememberSession(result.session);
    return result;
  }

  async truncateSession(payload: SessionTruncateParams): Promise<SessionTruncateResult> {
    const result = await invokePayloadOrReject<SessionTruncateResult>("session_truncate", payload);
    rememberSession(result.session);
    return result;
  }

  async sendMessage(payload: MessageSendParams): Promise<MessageSendResult> {
    return invokePayloadOrReject<MessageSendResult>("message_send", payload);
  }

  async listMessages(payload: MessageListParams): Promise<MessageListResult> {
    return invokePayloadOrReject<MessageListResult>("message_list", payload);
  }

  async deleteMessage(payload: MessageDeleteParams): Promise<MessageDeleteResult> {
    const result = await invokePayloadOrReject<MessageDeleteResult>("message_delete", payload);
    rememberSession(result.session);
    return result;
  }

  async workspaceFileList(payload: WorkspaceFileListParams): Promise<WorkspaceFileListResult> {
    return invokePayloadOrReject<WorkspaceFileListResult>("workspace_file_list", payload);
  }

  async workspaceFileSearch(payload: WorkspaceFileSearchParams): Promise<WorkspaceFileSearchResult> {
    return invokePayloadOrReject<WorkspaceFileSearchResult>("workspace_file_search", payload);
  }

  async workspaceFileRead(payload: WorkspaceFileReadParams): Promise<WorkspaceFileReadResult> {
    return invokePayloadOrReject<WorkspaceFileReadResult>("workspace_file_read", payload);
  }

  async openPath(payload: OpenPathParams): Promise<OpenPathResult> {
    return invokePayloadOrReject<OpenPathResult>("open_path", payload);
  }

  async terminalStart(payload: TerminalStartParams = {}): Promise<TerminalStartResult> {
    return invokePayloadOrReject<TerminalStartResult>("terminal_start", payload);
  }

  async terminalWrite(payload: TerminalWriteParams): Promise<TerminalControlResult> {
    return invokePayloadOrReject<TerminalControlResult>("terminal_write", payload);
  }

  async terminalResize(payload: TerminalResizeParams): Promise<TerminalControlResult> {
    return invokePayloadOrReject<TerminalControlResult>("terminal_resize", payload);
  }

  async terminalStop(payload: TerminalStopParams): Promise<TerminalControlResult> {
    return invokePayloadOrReject<TerminalControlResult>("terminal_stop", payload);
  }

  async gitLocalStatus(payload: GitLocalParams): Promise<GitLocalStatusResult> {
    return invokePayloadOrReject<GitLocalStatusResult>("git_local_status", payload);
  }

  async gitLocalDiff(payload: GitLocalDiffParams): Promise<GitLocalDiffResult> {
    return invokePayloadOrReject<GitLocalDiffResult>("git_local_diff", payload);
  }

  async gitLocalInit(payload: GitLocalParams): Promise<GitLocalCommandResult> {
    return invokePayloadOrReject<GitLocalCommandResult>("git_local_init", payload);
  }

  async gitLocalCheckout(payload: GitLocalCheckoutParams): Promise<GitLocalCommandResult> {
    return invokePayloadOrReject<GitLocalCommandResult>("git_local_checkout", payload);
  }

  async gitLocalCommit(payload: GitLocalCommitParams): Promise<GitLocalCommandResult> {
    return invokePayloadOrReject<GitLocalCommandResult>("git_local_commit", payload);
  }

  async approvalSubmit(payload: ApprovalSubmitParams): Promise<ApprovalSubmitResult> {
    return invokePayloadOrReject<ApprovalSubmitResult>("approval_submit", payload);
  }

  async approvalAllowAlways(payload: ApprovalAllowAlwaysParams): Promise<ApprovalAllowAlwaysResult> {
    return invokePayloadOrReject<ApprovalAllowAlwaysResult>("approval_allow_always", payload);
  }

  async diffGet(payload: DiffGetParams): Promise<DiffGetResult> {
    return invokePayloadOrReject<DiffGetResult>("diff_get", payload);
  }

  async commandLogList(payload: CommandLogListParams = {}): Promise<CommandLogListResult> {
    return invokePayloadOrReject<CommandLogListResult>("command_log_list", payload);
  }

  async commandLogGet(payload: CommandLogGetParams): Promise<CommandLogGetResult> {
    return invokePayloadOrReject<CommandLogGetResult>("command_log_get", payload);
  }

  async commandCancel(payload: CommandCancelParams): Promise<CommandCancelResult> {
    return invokePayloadOrReject<CommandCancelResult>("command_cancel", payload);
  }

  async getTask(taskId: string): Promise<TaskGetResult> {
    return invokePayloadOrReject<TaskGetResult>("task_get", { taskId });
  }

  async cancelTask(payload: TaskCancelParams): Promise<TaskControlResult> {
    return invokePayloadOrReject<TaskControlResult>("task_cancel", payload);
  }

  async pauseTask(payload: TaskPauseParams): Promise<TaskControlResult> {
    return invokePayloadOrReject<TaskControlResult>("task_pause", payload);
  }

  async resumeTask(payload: TaskResumeParams): Promise<TaskControlResult> {
    return invokePayloadOrReject<TaskControlResult>("task_resume", payload);
  }

  async revertTaskChanges(payload: TaskRevertChangesParams): Promise<TaskRevertChangesResult> {
    return invokePayloadOrReject<TaskRevertChangesResult>("task_revert_changes", payload);
  }

  async listTasks(payload: TaskListParams = {}): Promise<TaskListResult> {
    return invokePayloadOrReject<TaskListResult>("task_list", payload);
  }

  async worktreeGet(payload: WorktreeGetParams): Promise<WorktreeGetResult> {
    return invokePayloadOrReject<WorktreeGetResult>("worktree_get", payload);
  }

  async worktreeGetByTask(payload: WorktreeGetByTaskParams): Promise<WorktreeGetResult> {
    return invokePayloadOrReject<WorktreeGetResult>("worktree_get_by_task", payload);
  }

  async worktreeStatus(payload: WorktreeGetParams): Promise<WorktreeStatusResult> {
    return invokePayloadOrReject<WorktreeStatusResult>("worktree_status", payload);
  }

  async worktreeDiff(payload: WorktreeGetParams): Promise<WorktreeDiffResult> {
    return invokePayloadOrReject<WorktreeDiffResult>("worktree_diff", payload);
  }

  async worktreeMerge(payload: WorktreeMergeParams): Promise<WorktreeMergeResult> {
    return invokePayloadOrReject<WorktreeMergeResult>("worktree_merge", payload);
  }

  async worktreeRequestMergeApproval(payload: WorktreeMergeParams): Promise<WorktreeMergeApprovalResult> {
    return invokePayloadOrReject<WorktreeMergeApprovalResult>("worktree_request_merge_approval", payload);
  }

  async worktreeCleanup(payload: WorktreeCleanupParams): Promise<WorktreeCleanupResult> {
    return invokePayloadOrReject<WorktreeCleanupResult>("worktree_cleanup", payload);
  }

  async createScheduledTask(payload: ScheduledTaskCreateParams): Promise<ScheduledTaskResult> {
    const result = await invokePayloadOrReject<ScheduledTaskResult>("schedule_create", payload);
    clientCache.scheduledTasks[result.task.id] = result.task;
    return result;
  }

  async listScheduledTasks(): Promise<ScheduledTaskListResult> {
    const result = await invokeOrReject<ScheduledTaskListResult>("schedule_list");
    clientCache.scheduledTasks = Object.fromEntries(result.tasks.map((task) => [task.id, task]));
    return result;
  }

  async updateScheduledTask(payload: ScheduledTaskUpdateParams): Promise<ScheduledTaskResult> {
    const result = await invokePayloadOrReject<ScheduledTaskResult>("schedule_update", payload);
    clientCache.scheduledTasks[result.task.id] = result.task;
    return result;
  }

  async toggleScheduledTask(payload: ScheduledTaskToggleParams): Promise<ScheduledTaskResult> {
    const result = await invokePayloadOrReject<ScheduledTaskResult>("schedule_toggle", payload);
    clientCache.scheduledTasks[result.task.id] = result.task;
    return result;
  }

  async runScheduledTaskNow(payload: ScheduledTaskRunNowParams): Promise<ScheduledTaskRunNowResult> {
    const result = await invokePayloadOrReject<ScheduledTaskRunNowResult>("schedule_run_now", payload);
    if (result.task) {
      clientCache.scheduledTasks[result.task.id] = result.task;
    }
    clientCache.scheduledRuns = sortScheduledRuns([result.run, ...clientCache.scheduledRuns]).slice(0, 500);
    return result;
  }

  async listScheduledTaskLogs(payload: ScheduledTaskLogsParams = {}): Promise<ScheduledTaskLogsResult> {
    const result = await invokePayloadOrReject<ScheduledTaskLogsResult>("schedule_logs", payload);
    clientCache.scheduledRuns = sortScheduledRuns(result.logs);
    return result;
  }

  async listTrace(payload: TraceListParams): Promise<TraceListResult> {
    return invokePayloadOrReject<TraceListResult>("trace_list", payload);
  }

  async eventsAfter(payload: EventsAfterParams): Promise<EventsAfterResult> {
    return invokePayloadOrReject<EventsAfterResult>("events_after", payload);
  }

  async yuanbaoEventsAfter(payload: YuanbaoEventsAfterParams): Promise<YuanbaoEventsAfterResult> {
    return invokePayloadOrReject<YuanbaoEventsAfterResult>("yuanbao_events_after", payload);
  }

  async yuanbaoTeamSnapshot(payload: YuanbaoTeamSnapshotParams): Promise<YuanbaoTeamSnapshotResult> {
    return invokePayloadOrReject<YuanbaoTeamSnapshotResult>("yuanbao_team_snapshot", payload);
  }

  async getConfig(): Promise<ConfigGetResult> {
    const result = await invokeOrReject<ConfigGetResult>("config_get");
    clientCache.config = mergeRuntimeConfig(clientCache.config ?? result.config as RuntimeConfig, result.config as RuntimeConfig);
    return result;
  }

  async updateConfig(payload: ConfigUpdateParams): Promise<ConfigUpdateResult> {
    const result = await invokePayloadOrReject<ConfigUpdateResult>("config_update", payload);
    clientCache.config = mergeRuntimeConfig(clientCache.config ?? result.config as RuntimeConfig, result.config as RuntimeConfig);
    return result;
  }

  async clearPermissionRule(payload: PermissionRuleClearParams): Promise<PermissionRuleClearResult> {
    const result = await invokePayloadOrReject<PermissionRuleClearResult>("permission_rule_clear", payload);
    clientCache.config = mergeRuntimeConfig(clientCache.config ?? result.config as RuntimeConfig, result.config as RuntimeConfig);
    return result;
  }

  async testProvider(payload: ProviderTestParams = {}): Promise<ProviderTestResult> {
    const providerTimeoutSecondsRaw =
      payload.provider && typeof payload.provider === "object" && "timeout" in payload.provider
        ? Number((payload.provider as { timeout?: unknown }).timeout)
        : undefined;
    const timeoutMs = typeof providerTimeoutSecondsRaw === "number" &&
      Number.isFinite(providerTimeoutSecondsRaw) &&
      providerTimeoutSecondsRaw > 0
      ? Math.max(12_000, providerTimeoutSecondsRaw * 1_000 + 5_000)
      : 65_000;
    const result = await withTimeout(
      invokePayloadOrReject<ProviderTestResult>("provider_test", payload),
      timeoutMs,
      "供应商测试超时。请检查 API 密钥、基础 URL 和网络连接。",
    );
    rememberProviderTestResult(result);
    return result;
  }

  async exportLogs(payload?: { sessionId?: string }): Promise<Record<string, unknown>> {
    return invokePayloadOrReject<Record<string, unknown>>("log_export", payload ?? {});
  }

  async listErrors(payload?: { sessionId?: string; taskId?: string; source?: string; limit?: number }) {
    return invokePayloadOrReject<{ errors: unknown[]; summary: { totalErrors: number; bySource: Record<string, number> } }>(
      "errors_list", payload ?? {}
    );
  }

  async listMetrics(payload?: { sessionId?: string; limit?: number }) {
    return invokePayloadOrReject<{ metrics: unknown[] }>("metrics_list", payload ?? {});
  }

  async listSkills(payload: SkillListParams = {}): Promise<SkillListResult> {
    const result = await invokePayloadOrReject<SkillListResult>("skill_list", payload);
    const skills = sortSkills(result.skills.map(normalizeSkillRecord));
    clientCache.skills = Object.fromEntries(skills.map((skill) => [skill.id, skill]));
    return { skills };
  }

  async createSkill(payload: SkillCreateParams): Promise<SkillResult> {
    const result = await invokePayloadOrReject<SkillResult>("skill_create", payload);
    const skill = normalizeSkillRecord(result.skill);
    clientCache.skills[skill.id] = skill;
    return { skill };
  }

  async updateSkill(payload: SkillUpdateParams): Promise<SkillResult> {
    const result = await invokePayloadOrReject<SkillResult>("skill_update", payload);
    const skill = normalizeSkillRecord(result.skill);
    clientCache.skills[skill.id] = skill;
    return { skill };
  }

  async deleteSkill(payload: SkillDeleteParams): Promise<SkillDeleteResult> {
    const result = await invokePayloadOrReject<SkillDeleteResult>("skill_delete", payload);
    delete clientCache.skills[payload.skillId];
    return result;
  }

  async importSkills(payload: SkillImportParams): Promise<SkillImportResult> {
    return invokePayloadOrReject<SkillImportResult>("skill_import", payload);
  }

  async listMcpServers(payload: McpServerListParams = {}): Promise<McpServerListResult> {
    const result = await invokePayloadOrReject<McpServerListResult>("mcp_server_list", payload);
    const servers = sortMcpServers(result.servers.map(normalizeMcpServerRecord));
    clientCache.mcpServers = Object.fromEntries(servers.map((server) => [server.id, server]));
    return { servers };
  }

  async listAgentProfiles(payload: AgentProfileListParams = {}): Promise<AgentProfileListResult> {
    return invokePayloadOrReject<AgentProfileListResult>("agent_profile_list", payload);
  }

  async createAgentProfile(payload: AgentProfileCreateParams): Promise<AgentProfileResult> {
    return invokePayloadOrReject<AgentProfileResult>("agent_profile_create", payload);
  }

  async updateAgentProfile(payload: AgentProfileUpdateParams): Promise<AgentProfileResult> {
    return invokePayloadOrReject<AgentProfileResult>("agent_profile_update", payload);
  }

  async deleteAgentProfile(payload: AgentProfileDeleteParams): Promise<AgentProfileDeleteResult> {
    return invokePayloadOrReject<AgentProfileDeleteResult>("agent_profile_delete", payload);
  }

  async validateAgentProfile(payload: AgentProfileValidateParams): Promise<AgentProfileValidateResult> {
    return invokePayloadOrReject<AgentProfileValidateResult>("agent_profile_validate", payload);
  }

  async previewAgentProfileTools(payload: AgentProfilePreviewToolsParams): Promise<AgentProfilePreviewToolsResult> {
    return invokePayloadOrReject<AgentProfilePreviewToolsResult>("agent_profile_preview_tools", payload);
  }

  async listHooks(payload: HookListParams): Promise<HookListResult> {
    return invokePayloadOrReject<HookListResult>("hook_list", payload);
  }

  async createHook(payload: HookCreateParams): Promise<HookResult> {
    return invokePayloadOrReject<HookResult>("hook_create", payload);
  }

  async updateHook(payload: HookUpdateParams): Promise<HookResult> {
    return invokePayloadOrReject<HookResult>("hook_update", payload);
  }

  async deleteHook(payload: HookDeleteParams): Promise<HookDeleteResult> {
    return invokePayloadOrReject<HookDeleteResult>("hook_delete", payload);
  }

  async getHook(payload: HookGetParams): Promise<HookResult> {
    return invokePayloadOrReject<HookResult>("hook_get", payload);
  }

  async listHookExecutions(payload: HookListExecutionsParams = {}): Promise<HookListExecutionsResult> {
    return invokePayloadOrReject<HookListExecutionsResult>("hook_list_executions", payload);
  }

  async createMcpServer(payload: McpServerCreateParams): Promise<McpServerResult> {
    const result = await invokePayloadOrReject<McpServerResult>("mcp_server_create", payload);
    const server = normalizeMcpServerRecord(result.server);
    clientCache.mcpServers[server.id] = server;
    return { server };
  }

  async updateMcpServer(payload: McpServerUpdateParams): Promise<McpServerResult> {
    const result = await invokePayloadOrReject<McpServerResult>("mcp_server_update", payload);
    const server = normalizeMcpServerRecord(result.server);
    clientCache.mcpServers[server.id] = server;
    return { server };
  }

  async deleteMcpServer(payload: McpServerDeleteParams): Promise<McpServerDeleteResult> {
    const result = await invokePayloadOrReject<McpServerDeleteResult>("mcp_server_delete", payload);
    delete clientCache.mcpServers[payload.serverId];
    return result;
  }

  async refreshMcpTools(payload: McpToolsRefreshParams = {}): Promise<McpToolsRefreshRpcResult> {
    return invokePayloadOrReject<McpToolsRefreshRpcResult>("mcp_tools_refresh", payload);
  }

  async subscribeEvents(handler: (event: AgentEventEnvelope) => void): Promise<() => void> {
    assertRuntimeBridgeAvailable("agent_event_subscribe");
    const unlisten = await listen<AgentEventEnvelope>(EVENT_CHANNEL, (event) => {
      handler(event.payload);
    });
    return () => {
      unlisten();
    };
  }

  async subscribeYuanbaoMessages(handler: (message: YuanbaoServerMessage) => void): Promise<() => void> {
    assertRuntimeBridgeAvailable("yuanbao_event_subscribe");
    const unlisten = await listen<YuanbaoServerMessage>(YUANBAO_EVENT_CHANNEL, (event) => {
      handler(event.payload);
    });
    return () => {
      unlisten();
    };
  }

  async connectYuanbaoMessages(
    handler: (message: YuanbaoServerMessage) => void,
    options: YuanbaoConnectionOptions = {},
  ): Promise<() => void> {
    const { keepAliveMs = 30_000, onKeepAliveError, ...pingPayload } = options;
    const unlisten = await this.subscribeYuanbaoMessages(handler);
    let stopped = false;
    let keepAliveTimer: ReturnType<typeof setInterval> | undefined;

    const emitPing = async (includeConnected: boolean): Promise<void> => {
      const result = await this.runtimePing(pingPayload);
      if (stopped) {
        return;
      }
      for (const message of result.yuanbaoMessages) {
        if (!includeConnected && message.type === "connected") {
          continue;
        }
        handler(message);
      }
    };

    try {
      await emitPing(true);
    } catch (reason) {
      stopped = true;
      unlisten();
      throw reason;
    }

    if (keepAliveMs > 0) {
      keepAliveTimer = setInterval(() => {
        void emitPing(false).catch((reason) => {
          onKeepAliveError?.(reason);
        });
      }, keepAliveMs);
    }

    return () => {
      stopped = true;
      if (keepAliveTimer !== undefined) {
        clearInterval(keepAliveTimer);
      }
      unlisten();
    };
  }

  async subscribeTerminalEvents(handler: (event: TerminalEvent) => void): Promise<() => void> {
    assertRuntimeBridgeAvailable("terminal_event_subscribe");
    const unlisten = await listen<TerminalEvent>(TERMINAL_EVENT_CHANNEL, (event) => {
      handler(event.payload);
    });
    return () => {
      unlisten();
    };
  }
}
