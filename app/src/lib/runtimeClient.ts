import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import type {
  ApprovalRecord,
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
  MessageListParams,
  MessageListResult,
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
  PatchRecord,
  ProviderTestParams,
  ProviderTestResult,
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
  SessionDeleteParams,
  SessionDeleteResult,
  SessionCompactParams,
  SessionCompactResult,
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
  TaskResumeParams,
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
  WorkspaceFocusUpdateParams,
  WorkspaceFocusUpdateResult,
  WorkspaceMemoryClearParams,
  WorkspaceMemoryClearResult,
  WorkspaceOpenResult,
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
  runtimeRunning: boolean;
  repoRoot: string;
  pythonModule: string;
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
      ...current.autonomy,
      ...patch.autonomy,
      activeProfileId: patch.autonomy?.activeProfileId ?? current.autonomy.activeProfileId,
      profiles: patch.autonomy?.profiles ?? current.autonomy.profiles,
    },
    agentSoul: {
      ...current.agentSoul,
      ...patch.agentSoul,
      activeProfileId: patch.agentSoul?.activeProfileId ?? current.agentSoul.activeProfileId,
      workspaceInstructions: patch.agentSoul?.workspaceInstructions ?? current.agentSoul.workspaceInstructions,
      sessionOverrideEnabled: patch.agentSoul?.sessionOverrideEnabled ?? current.agentSoul.sessionOverrideEnabled,
      profiles: patch.agentSoul?.profiles ?? current.agentSoul.profiles,
    },
    permissions: {
      preset: patch.permissions?.preset ?? current.permissions.preset,
      capabilities: {
        ...current.permissions.capabilities,
        ...patch.permissions?.capabilities,
      },
    },
    tools: {
      ...current.tools,
      ...patch.tools,
      runCommand: {
        ...current.tools.runCommand,
        ...patch.tools?.runCommand,
      },
    },
    worktree: {
      ...current.worktree,
      ...patch.worktree,
    },
    ui: {
      ...current.ui,
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

  async sendMessage(payload: MessageSendParams): Promise<MessageSendResult> {
    return invokePayloadOrReject<MessageSendResult>("message_send", payload);
  }

  async listMessages(payload: MessageListParams): Promise<MessageListResult> {
    return invokePayloadOrReject<MessageListResult>("message_list", payload);
  }

  async approvalSubmit(payload: ApprovalSubmitParams): Promise<ApprovalSubmitResult> {
    return invokePayloadOrReject<ApprovalSubmitResult>("approval_submit", payload);
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

  async testProvider(payload: ProviderTestParams = {}): Promise<ProviderTestResult> {
    const result = await withTimeout(
      invokePayloadOrReject<ProviderTestResult>("provider_test", payload),
      12_000,
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
}
