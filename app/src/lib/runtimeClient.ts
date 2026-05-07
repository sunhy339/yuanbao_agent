import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import type {
  ApprovalRecord,
  ApprovalSubmitParams,
  ApprovalSubmitResult,
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
  WorkspaceFocusUpdateParams,
  WorkspaceFocusUpdateResult,
  WorkspaceMemoryClearParams,
  WorkspaceMemoryClearResult,
  WorkspaceOpenResult,
} from "@shared";
import {
  buildMockConfig,
  buildMockEvent,
  buildMockSession,
  buildMockTask,
  buildMockWorkspace,
} from "../state/mockData";

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
const browserEventTarget = new EventTarget();
const RUNTIME_BRIDGE_UNAVAILABLE_MESSAGE =
  "桌面运行时桥接不可用。请通过 Tauri 桌面应用打开 Yuanbao Agent，或为测试/预览显式启用浏览器预览模式。";

export type RuntimeConfig = AppConfig & Required<Pick<AppConfig, "search">>;
export type RuntimeCommandLog = CommandLogRecord;

export interface CommandCancelResult {
  commandLog: CommandLogRecord;
  cancelled?: boolean;
}

export type AppPathKind = "logs" | "data";

export interface AppPathOpenResult {
  path: string;
}

interface MockState {
  config: RuntimeConfig;
  workspace: WorkspaceOpenResult["workspace"] | null;
  sessions: SessionRecord[];
  messages: MessageRecord[];
  tasks: Record<string, TaskRecord>;
  patches: Record<string, PatchRecord>;
  approvals: Record<string, ApprovalRecord>;
  traces: TraceEventRecord[];
  scheduledTasks: Record<string, ScheduledTaskRecord>;
  scheduledRuns: ScheduledTaskRunRecord[];
  skills: Record<string, SkillPresetRecord>;
  mcpServers: Record<string, McpServerRecord>;
}

const mockState: MockState = {
  config: buildMockRuntimeConfig(),
  workspace: null,
  sessions: [],
  messages: [],
  tasks: {},
  patches: {},
  approvals: {},
  traces: [],
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

function isBrowserMockEnabled(): boolean {
  const env =
    (import.meta as ImportMeta & { env?: Record<string, string | boolean | undefined> }).env ?? {};
  const windowFlag =
    typeof window !== "undefined" &&
    (window as Window & { __YUANBAO_ENABLE_BROWSER_MOCK__?: unknown }).__YUANBAO_ENABLE_BROWSER_MOCK__;

  return (
    windowFlag === true ||
    windowFlag === "1" ||
    env.VITE_YUANBAO_ENABLE_BROWSER_MOCK === "1" ||
    env.VITE_YUANBAO_ENABLE_BROWSER_MOCK === "true"
  );
}

function shouldUseBrowserMock(): boolean {
  return !isTauriBridgeAvailable() && isBrowserMockEnabled();
}

function assertRuntimeBridgeAvailable(command: string): void {
  if (!isTauriBridgeAvailable()) {
    throw new Error(`${RUNTIME_BRIDGE_UNAVAILABLE_MESSAGE} 命令：${command}。`);
  }
}

function emitBrowserEvent(event: AgentEventEnvelope): void {
  rememberMockTrace(event);
  browserEventTarget.dispatchEvent(new CustomEvent(EVENT_CHANNEL, { detail: event }));
}

function readPayloadId(payload: unknown, keys: string[]): string | null {
  if (!payload || typeof payload !== "object") {
    return null;
  }

  const record = payload as Record<string, unknown>;
  for (const key of keys) {
    const value = record[key];
    if (typeof value === "string" && value.trim()) {
      return value.trim();
    }
  }
  return null;
}

function getTraceSource(type: string): TraceEventRecord["source"] {
  const source = type.split(".")[0];
  if (
    source === "provider" ||
    source === "tool" ||
    source === "approval" ||
    source === "patch" ||
    source === "command" ||
    source === "task" ||
    source === "assistant"
  ) {
    return source;
  }
  return "runtime";
}

function rememberMockTrace(event: AgentEventEnvelope): void {
  const trace: TraceEventRecord = {
    id: event.eventId,
    taskId: event.taskId,
    sessionId: event.sessionId,
    type: event.type,
    source: getTraceSource(event.type),
    relatedId: readPayloadId(event.payload, [
      "toolCallId",
      "commandId",
      "approvalId",
      "patchId",
      "messageId",
    ]),
    payload: event.payload,
    createdAt: event.ts,
    sequence: mockState.traces.length + 1,
  };

  mockState.traces = [...mockState.traces, trace].slice(-400);
}

function applyMockTaskControl(
  taskId: string,
  nextStatus: TaskRecord["status"] | ((task: TaskRecord) => TaskRecord["status"]),
  eventType: AgentEventEnvelope["type"],
): TaskControlResult {
  const task = updateMockTask(taskId, (current) => ({
    ...current,
    status: typeof nextStatus === "function" ? nextStatus(current) : nextStatus,
    updatedAt: Date.now(),
  }));
  if (!task) {
    throw new Error(`未找到任务：${taskId}`);
  }
  emitBrowserEvent(buildMockEvent(task.sessionId, task.id, eventType, { status: task.status }));
  return { task };
}

function buildMockHostStatus(): HostStatus {
  return {
    runtimeTransport: "mock-browser",
    eventChannel: EVENT_CHANNEL,
    runtimeRunning: false,
    repoRoot: "browser-preview",
    pythonModule: "local_agent_runtime.main",
  };
}

function buildMockRuntimeConfig(): RuntimeConfig {
  const config = buildMockConfig();
  const model = config.provider.defaultModel;
  const activeProfile = {
    id: "default",
    name: "Default",
    mode: "mock" as const,
    baseUrl: "https://api.openai.com/v1",
    model,
    defaultModel: model,
    fallbackModel: config.provider.fallbackModel,
    apiKeyEnvVarName: "LOCAL_AGENT_PROVIDER_API_KEY",
    temperature: config.provider.temperature,
    maxTokens: config.provider.maxOutputTokens,
    maxOutputTokens: config.provider.maxOutputTokens,
    maxContextTokens: 120000,
    timeout: 30,
  };
  return {
    ...config,
    provider: {
      ...activeProfile,
      ...config.provider,
      activeProfileId: "default",
      profiles: [activeProfile],
    },
    search: config.search ?? {
      glob: [],
      ignore: config.workspace.ignore,
    },
  };
}

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

function buildMockSkill(payload: SkillCreateParams): SkillPresetRecord {
  const now = Date.now();
  return {
    id: payload.skillId ?? payload.id ?? `skill_${now}_${Math.random().toString(16).slice(2, 8)}`,
    name: payload.name.trim(),
    description: payload.description ?? "",
    systemPrompt: payload.systemPrompt ?? payload.system_prompt ?? "",
    toolWhitelist: payload.toolWhitelist ?? payload.tool_whitelist ?? [],
    parameterConstraints: payload.parameterConstraints ?? payload.parameter_constraints ?? {},
    category: payload.category ?? "custom",
    isBuiltin: false,
    createdAt: now,
    updatedAt: now,
  };
}

function buildMockMcpServer(payload: McpServerCreateParams): McpServerRecord {
  const now = Date.now();
  return {
    id: payload.serverId ?? payload.id ?? `mcp_${now}_${Math.random().toString(16).slice(2, 8)}`,
    name: payload.name.trim(),
    transport: payload.transport ?? "stdio",
    command: payload.command ?? "",
    args: payload.args ?? [],
    url: payload.url ?? "",
    headers: payload.headers ?? {},
    env: payload.env ?? {},
    enabled: payload.enabled ?? true,
    createdAt: now,
    updatedAt: now,
  };
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

function isCommandShell(value: string | undefined): CommandLogRecord["shell"] | undefined {
  if (value === "powershell" || value === "bash" || value === "zsh") {
    return value;
  }
  return undefined;
}

function isCommandLogStatus(value: string | undefined): CommandLogRecord["status"] | undefined {
  if (
    value === "running" ||
    value === "completed" ||
    value === "failed" ||
    value === "timeout" ||
    value === "killed"
  ) {
    return value;
  }
  if (value === "cancelled") {
    return "killed";
  }
  return undefined;
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" ? (value as Record<string, unknown>) : null;
}

function readRecordString(record: Record<string, unknown>, key: string): string | undefined {
  const value = record[key];
  return typeof value === "string" && value.length > 0 ? value : undefined;
}

function readRecordRawString(record: Record<string, unknown>, key: string): string | undefined {
  const value = record[key];
  return typeof value === "string" ? value : undefined;
}

function readRecordNumber(record: Record<string, unknown>, key: string): number | undefined {
  const value = record[key];
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

function appendOutputTail(current: string, chunk: string): string {
  return `${current}${chunk}`.slice(-12_000);
}

function getCommandStatusForEvent(
  type: string,
  payload: Record<string, unknown>,
): CommandLogRecord["status"] {
  const payloadStatus = isCommandLogStatus(readRecordString(payload, "status"));
  if (payloadStatus) {
    return payloadStatus;
  }

  if (type === "command.started") {
    return "running";
  }
  if (type === "command.completed") {
    return "completed";
  }
  if (type === "command.cancelled") {
    return "killed";
  }
  return "failed";
}

function buildMockCommandLogs(): CommandLogRecord[] {
  const logs = new Map<string, CommandLogRecord>();
  const sources = [...mockState.traces].sort((left, right) => left.sequence - right.sequence);

  for (const source of sources) {
    const payload = asRecord(source.payload);
    if (!payload || !source.type.startsWith("command.")) {
      continue;
    }

    const commandId = readRecordString(payload, "commandId");
    if (!commandId) {
      continue;
    }

    const current = logs.get(commandId);
    if (source.type === "command.output") {
      const stream = readRecordString(payload, "stream");
      const chunk = readRecordRawString(payload, "chunk");
      if (!stream || chunk === undefined) {
        continue;
      }

      const base = current ?? {
        id: commandId,
        taskId: source.taskId,
        command: commandId,
        cwd: ".",
        status: "running",
        startedAt: source.createdAt,
      };
      logs.set(commandId, {
        ...base,
        stdout: stream === "stdout" ? appendOutputTail(base.stdout ?? "", chunk) : base.stdout,
        stderr: stream === "stderr" ? appendOutputTail(base.stderr ?? "", chunk) : base.stderr,
      });
      continue;
    }

    if (
      source.type !== "command.started" &&
      source.type !== "command.completed" &&
      source.type !== "command.failed" &&
      source.type !== "command.cancelled"
    ) {
      continue;
    }

    const status = getCommandStatusForEvent(source.type, payload);
    logs.set(commandId, {
      id: commandId,
      taskId: source.taskId,
      command: readRecordString(payload, "command") ?? current?.command ?? commandId,
      cwd: readRecordString(payload, "cwd") ?? current?.cwd ?? ".",
      shell: isCommandShell(readRecordString(payload, "shell")) ?? current?.shell,
      status,
      exitCode:
        readRecordNumber(payload, "exitCode") ??
        (payload.exitCode === null ? undefined : current?.exitCode),
      startedAt:
        readRecordNumber(payload, "startedAt") ??
        current?.startedAt ??
        source.createdAt,
      finishedAt:
        readRecordNumber(payload, "finishedAt") ??
        (status === "running" ? current?.finishedAt : source.createdAt),
      durationMs: readRecordNumber(payload, "durationMs") ?? current?.durationMs,
      stdoutPath: readRecordString(payload, "stdoutPath") ?? current?.stdoutPath,
      stderrPath: readRecordString(payload, "stderrPath") ?? current?.stderrPath,
      stdout: readRecordRawString(payload, "stdout") ?? current?.stdout,
      stderr: readRecordRawString(payload, "stderr") ?? current?.stderr,
    });
  }

  return [...logs.values()].sort(
    (left, right) =>
      (right.finishedAt ?? right.startedAt) - (left.finishedAt ?? left.startedAt),
  );
}

function getMockCommandLog(commandId: string): CommandLogRecord {
  const commandLog = buildMockCommandLogs().find((log) => log.id === commandId);
  if (!commandLog) {
    throw new Error(`未找到命令日志：${commandId}`);
  }
  return commandLog;
}

function getMockCommandSessionId(commandLog: CommandLogRecord): string {
  const trace = mockState.traces.find((item) => {
    if (item.taskId !== commandLog.taskId) {
      return false;
    }
    const payload = asRecord(item.payload);
    return readRecordString(payload ?? {}, "commandId") === commandLog.id;
  });
  return trace?.sessionId ?? commandLog.taskId;
}

function filterMockCommandLogs(payload: CommandLogListParams = {}): CommandLogRecord[] {
  const limit = payload.limit ?? 100;
  return buildMockCommandLogs()
    .filter((log) => !payload.taskId || log.taskId === payload.taskId)
    .filter((log) => !payload.status || log.status === payload.status)
    .filter((log) => {
      if (!payload.sessionId) {
        return true;
      }
      return mockState.traces.some(
        (trace) => trace.sessionId === payload.sessionId && trace.taskId === log.taskId,
      );
    })
    .slice(0, limit);
}

function parseScheduleOffsetMs(schedule: string): number {
  const match = schedule.toLowerCase().match(/every\s+(\d+)\s*(minute|minutes|min|hour|hours|hr|hrs)/);
  if (!match) {
    return 30 * 60_000;
  }

  const amount = Math.max(1, Number.parseInt(match[1] ?? "30", 10));
  const unit = match[2] ?? "minutes";
  return amount * (unit.startsWith("hour") || unit.startsWith("hr") ? 60 : 1) * 60_000;
}

function buildMockScheduledTask(payload: ScheduledTaskCreateParams): ScheduledTaskRecord {
  const now = Date.now();
  const enabled = payload.enabled ?? true;
  return {
    id: `sched_${now}_${Math.random().toString(16).slice(2, 8)}`,
    name: payload.name.trim(),
    prompt: payload.prompt.trim(),
    schedule: payload.schedule.trim(),
    status: enabled ? "active" : "disabled",
    enabled,
    createdAt: now,
    updatedAt: now,
    lastRunAt: null,
    nextRunAt: enabled ? now + parseScheduleOffsetMs(payload.schedule) : null,
  };
}

function updateMockScheduledTask(
  taskId: string,
  updater: (task: ScheduledTaskRecord) => ScheduledTaskRecord,
): ScheduledTaskRecord {
  const current = mockState.scheduledTasks[taskId];
  if (!current) {
    throw new Error(`未找到定时任务：${taskId}`);
  }
  const next = updater(current);
  mockState.scheduledTasks[taskId] = next;
  return next;
}

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
    tools: {
      ...current.tools,
      ...patch.tools,
      runCommand: {
        ...current.tools.runCommand,
        ...patch.tools?.runCommand,
      },
    },
    ui: {
      ...current.ui,
      ...patch.ui,
    },
  };
}

function buildProviderTestFallback(
  params: ProviderTestParams | undefined,
  reason?: unknown,
): ProviderTestResult {
  const checkedAt = Date.now();
  const providerRoot = {
    ...mockState.config.provider,
    ...params?.provider,
  };
  const profiles = providerRoot.profiles ?? [];
  const profileId = params?.profileId ?? providerRoot.activeProfileId;
  const selectedProfile =
    profiles.find((profile) => profile.id === profileId) ??
    profiles[0] ??
    {};
  const provider = {
    ...providerRoot,
    ...selectedProfile,
  };
  const mode = provider.mode ?? "mock";
  const model = provider.model ?? provider.defaultModel;
  const envVarName = provider.apiKeyEnvVarName ?? "LOCAL_AGENT_PROVIDER_API_KEY";
  const providerProfileId = typeof provider.id === "string" ? provider.id : profileId;
  const providerProfileName = typeof provider.name === "string" ? provider.name : undefined;

  if (mode === "mock") {
    return {
      ok: true,
      status: "mocked",
      message: "本地预览供应商已就绪。应用配置中不会存储 API key 值。",
      profileId: providerProfileId,
      profileName: providerProfileName,
      providerMode: mode,
      model,
      baseUrl: provider.baseUrl,
      checkedEnvVarName: envVarName,
      envVarName,
      lastCheckedAt: checkedAt,
      lastStatus: "mocked",
      lastErrorSummary: "本地预览不会连接远程模型。",
      source: "mock-fallback",
    };
  }

  const compatibleModes = new Set(["openai", "openai-compatible", "openai_compatible", "openai-compatible-chat"]);
  if (compatibleModes.has(String(mode).toLowerCase()) && !reason) {
    return {
      ok: false,
      status: "missing_env",
      message: `浏览器预览 fallback 无法读取运行时环境变量 ${envVarName}。`,
      profileId: providerProfileId,
      profileName: providerProfileName,
      providerMode: mode,
      model,
      baseUrl: provider.baseUrl,
      checkedEnvVarName: envVarName,
      envVarName,
      lastCheckedAt: checkedAt,
      lastStatus: "missing_env",
      lastErrorSummary: `请在桌面运行时环境中设置 ${envVarName}，然后在 Tauri 应用中运行连接测试。`,
      source: "mock-fallback",
      details: {
        errorSummary: `请在桌面运行时环境中设置 ${envVarName}，然后在 Tauri 应用中运行连接测试。`,
      },
    };
  }

  const errorSummary = reason instanceof Error ? reason.message : "供应商测试后端尚不可用。";
  return {
    ok: false,
    status: compatibleModes.has(String(mode).toLowerCase()) ? "failed" : "unsupported",
    message:
      reason instanceof Error
        ? `供应商测试后端尚不可用：${reason.message}`
        : "供应商测试后端尚不可用。",
    profileId: providerProfileId,
    profileName: providerProfileName,
    providerMode: mode,
    model,
    baseUrl: provider.baseUrl,
    checkedEnvVarName: envVarName,
    envVarName,
    lastCheckedAt: checkedAt,
    lastStatus: compatibleModes.has(String(mode).toLowerCase()) ? "failed" : "unsupported",
    lastErrorSummary: errorSummary,
    source: "mock-fallback",
    details: {
      errorSummary,
    },
  };
}

function rememberProviderTestResult(result: ProviderTestResult): void {
  const profileId = result.profileId;
  if (!profileId || !mockState.config.provider.profiles?.length) {
    return;
  }

  const profiles = mockState.config.provider.profiles.map((profile) =>
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

  mockState.config = mergeRuntimeConfig(mockState.config, {
    provider: {
      profiles,
    },
  });
}

function rememberSession(session: SessionRecord): void {
  const withoutCurrent = mockState.sessions.filter((item) => item.id !== session.id);
  mockState.sessions = sortSessions([...withoutCurrent, session]);
}

function rememberApproval(approval: ApprovalRecord): void {
  mockState.approvals = {
    ...mockState.approvals,
    [approval.id]: approval,
  };
}

function rememberPatch(patch: PatchRecord): void {
  mockState.patches = {
    ...mockState.patches,
    [patch.id]: patch,
  };
}

function getMockApprovalRequest(command: string, cwd: string, shell: string, timeoutMs: number): Record<string, unknown> {
  return {
    command,
    cwd,
    shell,
    timeoutMs,
    workspaceRoot: mockState.workspace?.rootPath ?? mockState.config.workspace.rootPath,
  };
}

function getMockPatchApprovalRequest(patch: PatchRecord): Record<string, unknown> {
  return {
    patchId: patch.id,
    summary: patch.summary,
    filesChanged: patch.filesChanged,
    files: ["app/src/App.tsx", "app/src/lib/runtimeClient.ts"],
    risk: "writes workspace files through apply_patch",
    workspaceRoot: mockState.workspace?.rootPath ?? mockState.config.workspace.rootPath,
  };
}

function buildMockPatch(task: TaskRecord): PatchRecord {
  const patchId = `patch_${Date.now()}`;
  const filesChanged = 2;
  const summary = `Mock patch for: ${task.goal.slice(0, 72)}`;
  const diffText = [
    `diff --git a/app/src/App.tsx b/app/src/App.tsx`,
    `--- a/app/src/App.tsx`,
    `+++ b/app/src/App.tsx`,
    `@@ -1,3 +1,6 @@`,
    ` import { useEffect, useMemo, useState } from "react";`,
    `+// Mock patch content generated in browser fallback mode.`,
    `+const patchId = "${patchId}";`,
    ` export function App() {`,
    `   return <div data-patch-id={patchId} />;`,
  ].join("\n");

  return {
    id: patchId,
    taskId: task.id,
    workspaceId: mockState.workspace?.id ?? "workspace_mock",
    summary,
    diffText,
    status: "proposed",
    filesChanged,
    createdAt: Date.now(),
    updatedAt: Date.now(),
  };
}

function updateMockTask(taskId: string, updater: (task: TaskRecord) => TaskRecord): TaskRecord | null {
  const current = mockState.tasks[taskId];
  if (!current) {
    return null;
  }

  const next = updater(current);
  mockState.tasks[taskId] = next;
  return next;
}

function rememberMockMessage(message: MessageRecord): void {
  mockState.messages = [
    ...mockState.messages.filter((current) => current.id !== message.id),
    message,
  ].sort((left, right) => left.createdAt - right.createdAt || left.id.localeCompare(right.id));
}

function emitMockTaskSequence(sessionId: string, task: TaskRecord): void {
  const searchToolCallId = `tool_search_${Date.now()}`;

  window.setTimeout(() => {
    const patch = buildMockPatch(task);
    rememberPatch(patch);
    emitBrowserEvent(
      buildMockEvent(sessionId, task.id, "patch.proposed", {
        patchId: patch.id,
        summary: patch.summary,
        filesChanged: patch.filesChanged,
      }),
    );
    const approvalRequest = getMockPatchApprovalRequest(patch);
    const approval: ApprovalRecord = {
      id: `appr_patch_${Date.now()}`,
      taskId: task.id,
      kind: "apply_patch",
      requestJson: JSON.stringify(approvalRequest),
      createdAt: Date.now(),
    };
    rememberApproval(approval);
    emitBrowserEvent(
      buildMockEvent(sessionId, task.id, "approval.requested", {
        approvalId: approval.id,
        taskId: task.id,
        kind: "apply_patch",
        patchId: patch.id,
        request: approvalRequest,
      }),
    );
  }, 30);

  window.setTimeout(() => {
    const next = updateMockTask(task.id, (current) => ({
      ...current,
      status: "running",
      updatedAt: Date.now(),
      plan:
        current.plan?.map((step) =>
          step.id === "inspect-request" ? { ...step, status: "active" } : step,
        ) ?? current.plan,
    }));

    if (next) {
      emitBrowserEvent(
        buildMockEvent(sessionId, task.id, "task.started", {
          status: next.status,
          plan: next.plan,
          detail: "浏览器预览模式已启动模拟任务。",
        }),
      );
    }
  }, 60);

  window.setTimeout(() => {
    emitBrowserEvent(
      buildMockEvent(sessionId, task.id, "assistant.token", {
        delta: "浏览器预览模式已启用。 ",
      }),
    );
  }, 140);

  window.setTimeout(() => {
    emitBrowserEvent(
      buildMockEvent(sessionId, task.id, "assistant.token", {
        delta: "请通过 Tauri 启动桌面应用，以连接 Python 运行时。",
      }),
    );
  }, 180);

  window.setTimeout(() => {
    rememberMockMessage({
      id: `msg_assistant_${task.id}`,
      sessionId,
      taskId: task.id,
      role: "assistant",
      content: "浏览器预览助手响应已完成。",
      createdAt: Date.now(),
    });
    emitBrowserEvent(
      buildMockEvent(sessionId, task.id, "assistant.message.completed", {
        summary: "浏览器预览助手响应已完成。",
      }),
    );
  }, 205);

  window.setTimeout(() => {
    emitBrowserEvent(
      buildMockEvent(sessionId, task.id, "tool.started", {
        toolCallId: searchToolCallId,
        toolName: "search_files",
        arguments: {
          query: "pytest",
          mode: "content",
        },
      }),
    );
  }, 220);

  window.setTimeout(() => {
    emitBrowserEvent(
      buildMockEvent(sessionId, task.id, "tool.completed", {
        toolCallId: searchToolCallId,
        toolName: "search_files",
        result: {
          matches: 2,
          files: ["app/src/App.tsx", "runtime/src/local_agent_runtime/tools.py"],
        },
        durationMs: 64,
      }),
    );
  }, 285);

  window.setTimeout(() => {
    const approvalRequest = getMockApprovalRequest(
      "pytest",
      ".",
      "powershell",
      mockState.config.policy.commandTimeoutMs,
    );
    const approval: ApprovalRecord = {
      id: `appr_${Date.now()}`,
      taskId: task.id,
      kind: "run_command",
      requestJson: JSON.stringify(approvalRequest),
      createdAt: Date.now(),
    };

    rememberApproval(approval);
    updateMockTask(task.id, (current) => ({
      ...current,
      status: "waiting_approval",
      updatedAt: Date.now(),
      plan:
        current.plan?.map((step) => {
          if (step.id === "inspect-request") {
            return { ...step, status: "completed", detail: "模拟请求分析已完成。" };
          }
          if (step.id === "prepare-next-step") {
            return { ...step, status: "active", detail: "等待审批后运行命令。" };
          }
          return step;
        }) ?? current.plan,
    }));

    emitBrowserEvent(
      buildMockEvent(sessionId, task.id, "task.updated", {
        status: "waiting_approval",
        plan: mockState.tasks[task.id]?.plan,
        detail: "等待审批后运行命令。",
      }),
    );
    emitBrowserEvent(
      buildMockEvent(sessionId, task.id, "approval.requested", {
        approvalId: approval.id,
        taskId: task.id,
        kind: "run_command",
        request: approvalRequest,
      }),
    );
    emitBrowserEvent(
      buildMockEvent(sessionId, task.id, "task.waiting_approval", {
        status: "waiting_approval",
        detail: "命令执行前需要审批。",
      }),
    );
  }, 320);
}

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
    if (shouldUseBrowserMock()) {
      return buildMockHostStatus();
    }
    return invokeOrReject<HostStatus>("host_status");
  }

  canOpenLocalAppPaths(): boolean {
    return isTauriBridgeAvailable();
  }

  async openAppPath(kind: AppPathKind): Promise<AppPathOpenResult> {
    if (shouldUseBrowserMock()) {
      throw new Error(`${RUNTIME_BRIDGE_UNAVAILABLE_MESSAGE} 命令：open_app_path。`);
    }
    return invokeOrReject<AppPathOpenResult>("open_app_path", { kind });
  }

  async openWorkspace(path: string): Promise<WorkspaceOpenResult> {
    if (shouldUseBrowserMock()) {
      const result = {
        workspace: buildMockWorkspace(path),
      } satisfies WorkspaceOpenResult;
      mockState.workspace = result.workspace;
      mockState.sessions = [];
      mockState.messages = [];
      mockState.tasks = {};
      mockState.patches = {};
      mockState.approvals = {};
      mockState.traces = [];
      mockState.scheduledTasks = {};
      mockState.scheduledRuns = [];
      mockState.mcpServers = {};
      mockState.config = mergeRuntimeConfig(mockState.config, {
        workspace: {
          ignore: mockState.config.workspace.ignore,
          rootPath: path,
          writableRoots: [path],
        },
      });
      return result;
    }
    const result = await invokeOrReject<WorkspaceOpenResult>("workspace_open", { path });
    mockState.workspace = result.workspace;
    mockState.sessions = [];
    mockState.messages = [];
    mockState.tasks = {};
    mockState.patches = {};
    mockState.approvals = {};
    mockState.traces = [];
    mockState.scheduledTasks = {};
    mockState.scheduledRuns = [];
    mockState.mcpServers = {};
    mockState.config = mergeRuntimeConfig(mockState.config, {
      workspace: {
        ignore: mockState.config.workspace.ignore,
        rootPath: result.workspace.rootPath,
        writableRoots: [result.workspace.rootPath],
      },
    });
    return result;
  }

  async clearWorkspaceMemory(payload: WorkspaceMemoryClearParams): Promise<WorkspaceMemoryClearResult> {
    if (shouldUseBrowserMock()) {
      if (!mockState.workspace || mockState.workspace.id !== payload.workspaceId) {
        throw new Error(`未找到工作区：${payload.workspaceId}`);
      }
      mockState.workspace = { ...mockState.workspace, summary: null, updatedAt: Date.now() };
      return { workspace: mockState.workspace };
    }
    const result = await invokePayloadOrReject<WorkspaceMemoryClearResult>("workspace_memory_clear", payload);
    mockState.workspace = result.workspace;
    return result;
  }

  async updateWorkspaceFocus(payload: WorkspaceFocusUpdateParams): Promise<WorkspaceFocusUpdateResult> {
    if (shouldUseBrowserMock()) {
      if (!mockState.workspace || mockState.workspace.id !== payload.workspaceId) {
        throw new Error(`未找到工作区：${payload.workspaceId}`);
      }
      mockState.workspace = {
        ...mockState.workspace,
        focus: payload.focus?.trim() || null,
        updatedAt: Date.now(),
      };
      return { workspace: mockState.workspace };
    }
    const result = await invokePayloadOrReject<WorkspaceFocusUpdateResult>("workspace_focus_update", payload);
    mockState.workspace = result.workspace;
    return result;
  }

  async createSession(payload: SessionCreateParams): Promise<SessionCreateResult> {
    if (shouldUseBrowserMock()) {
      const result = {
        session: buildMockSession(
          payload.workspaceId,
          payload.title,
          mockState.workspace?.id === payload.workspaceId ? mockState.workspace : null,
        ),
      } satisfies SessionCreateResult;
      rememberSession(result.session);
      return result;
    }
    const result = await invokePayloadOrReject<SessionCreateResult>("session_create", payload);
    rememberSession(result.session);
    return result;
  }

  async listSessions(): Promise<SessionListResult> {
    if (shouldUseBrowserMock()) {
      return {
        sessions: sortSessions(mockState.sessions),
      };
    }

    const result = await invokeOrReject<SessionListResult>("session_list");
    mockState.sessions = sortSessions(result.sessions);
    return result;
  }

  async updateSession(payload: SessionUpdateParams): Promise<SessionUpdateResult> {
    if (shouldUseBrowserMock()) {
      const session = mockState.sessions.find((s) => s.id === payload.sessionId);
      if (!session) throw new Error(`未找到会话：${payload.sessionId}`);
      if (payload.title !== undefined) session.title = payload.title;
      if (payload.status !== undefined) session.status = payload.status as SessionRecord["status"];
      session.updatedAt = Date.now();
      return { session };
    }
    return invokePayloadOrReject<SessionUpdateResult>("session_update", payload);
  }

  async deleteSession(payload: SessionDeleteParams): Promise<SessionDeleteResult> {
    if (shouldUseBrowserMock()) {
      const idx = mockState.sessions.findIndex((s) => s.id === payload.sessionId);
      if (idx === -1) throw new Error(`未找到会话：${payload.sessionId}`);
      const [session] = mockState.sessions.splice(idx, 1);
      return { session };
    }
    return invokePayloadOrReject<SessionDeleteResult>("session_delete", payload);
  }

  async compactSession(payload: SessionCompactParams): Promise<SessionCompactResult> {
    return invokePayloadOrReject<SessionCompactResult>("session_compact", payload);
  }

  async sendMessage(payload: MessageSendParams): Promise<MessageSendResult> {
    if (shouldUseBrowserMock()) {
      if (payload.mode === "supplement" && payload.taskId && mockState.tasks[payload.taskId]) {
        const task = mockState.tasks[payload.taskId];
        rememberMockMessage({
          id: `msg_user_${Date.now()}`,
          sessionId: payload.sessionId,
          taskId: task.id,
          role: "user",
          content: payload.content,
          createdAt: Date.now(),
        });
        return { task };
      }
      const task = buildMockTask(payload.sessionId, payload.content);
      mockState.tasks[task.id] = task;
      rememberMockMessage({
        id: `msg_user_${task.id}`,
        sessionId: payload.sessionId,
        taskId: task.id,
        role: "user",
        content: payload.content,
        createdAt: Date.now(),
      });
      emitMockTaskSequence(payload.sessionId, task);
      return { task };
    }
    return invokePayloadOrReject<MessageSendResult>("message_send", payload);
  }

  async listMessages(payload: MessageListParams): Promise<MessageListResult> {
    if (shouldUseBrowserMock()) {
      const limit = payload.limit ?? 500;
      return {
        messages: mockState.messages
          .filter((message) => message.sessionId === payload.sessionId)
          .slice(-Math.max(1, Math.min(limit, 1000))),
      };
    }
    return invokePayloadOrReject<MessageListResult>("message_list", payload);
  }

  async approvalSubmit(payload: ApprovalSubmitParams): Promise<ApprovalSubmitResult> {
    if (shouldUseBrowserMock()) {
      const currentApproval = mockState.approvals[payload.approvalId];
      if (!currentApproval) {
        throw new Error(`未找到审批：${payload.approvalId}`);
      }

      if (currentApproval.decision && currentApproval.decision !== payload.decision) {
        throw new Error(`审批已处理为：${currentApproval.decision}`);
      }

      const now = Date.now();
      const approval: ApprovalRecord = {
        ...currentApproval,
        decision: payload.decision,
        decidedBy: "user",
        decidedAt: now,
      };
      rememberApproval(approval);

      const task = mockState.tasks[approval.taskId];
      if (!task) {
        throw new Error(`未找到任务：${approval.taskId}`);
      }

      const request = JSON.parse(approval.requestJson) as {
        command?: string;
        cwd?: string;
        shell?: string;
        timeoutMs?: number;
        patchId?: string;
      };

      emitBrowserEvent(
        buildMockEvent(task.sessionId, task.id, "approval.resolved", {
          approvalId: approval.id,
          taskId: task.id,
          decision: payload.decision,
        }),
      );

      if (approval.kind === "apply_patch") {
        const patchId = request.patchId;
        if (patchId && mockState.patches[patchId]) {
          rememberPatch({
            ...mockState.patches[patchId],
            status: payload.decision === "approved" ? "approved" : "rejected",
            updatedAt: now,
          });
        }
        return { approval };
      }

      if (payload.decision === "approved") {
        const commandId = `cmd_${approval.id}`;
        const runningTask = updateMockTask(task.id, (current) => ({
          ...current,
          status: "running",
          updatedAt: now,
          plan:
            current.plan?.map((step) =>
              step.id === "prepare-next-step"
                ? { ...step, status: "completed", detail: "审批已通过；命令正在运行。" }
                : step,
            ) ?? current.plan,
        }));

        if (runningTask) {
          emitBrowserEvent(
            buildMockEvent(task.sessionId, task.id, "task.updated", {
              status: "running",
              plan: runningTask.plan,
              detail: "审批已接受",
            }),
          );
        }

        emitBrowserEvent(
          buildMockEvent(task.sessionId, task.id, "command.started", {
            commandId,
            command: request.command ?? "pytest",
            cwd: request.cwd ?? ".",
            shell: request.shell ?? "powershell",
            status: "running",
          }),
        );

        emitBrowserEvent(
          buildMockEvent(task.sessionId, task.id, "command.output", {
            commandId,
            stream: "stdout",
            chunk: `已批准的命令成功完成：${request.command ?? "pytest"}\n`,
          }),
        );

        emitBrowserEvent(
          buildMockEvent(task.sessionId, task.id, "command.completed", {
            commandId,
            command: request.command ?? "pytest",
            cwd: request.cwd ?? ".",
            shell: request.shell ?? "powershell",
            status: "completed",
            exitCode: 0,
            durationMs: 240,
            summary: "模拟命令已成功完成。",
          }),
        );

        const completedTask = updateMockTask(task.id, (current) => ({
          ...current,
          status: "completed",
          resultSummary: "本地预览已完成批准的命令并发布输出。",
          updatedAt: Date.now(),
          plan:
            current.plan?.map((step) =>
              step.id === "prepare-next-step"
                ? { ...step, status: "completed", detail: "已批准命令已在本地预览中完成。" }
                : step,
            ) ?? current.plan,
        }));

        if (completedTask) {
          emitBrowserEvent(
            buildMockEvent(task.sessionId, task.id, "task.completed", {
              status: "completed",
              plan: completedTask.plan,
              detail: completedTask.resultSummary,
            }),
          );
        }
      } else {
        const cancelledTask = updateMockTask(task.id, (current) => ({
          ...current,
          status: "cancelled",
          updatedAt: now,
          plan:
            current.plan?.map((step) =>
              step.id === "prepare-next-step"
                ? { ...step, status: "failed", detail: "审批已被用户拒绝。" }
                : step,
            ) ?? current.plan,
        }));

        if (cancelledTask) {
          emitBrowserEvent(
            buildMockEvent(task.sessionId, task.id, "task.updated", {
              status: "cancelled",
              plan: cancelledTask.plan,
              detail: "Approval rejected by user.",
            }),
          );
          emitBrowserEvent(
            buildMockEvent(task.sessionId, task.id, "task.cancelled", {
              status: "cancelled",
              plan: cancelledTask.plan,
              detail: "Approval rejected by user.",
            }),
          );
        }
      }

      return { approval };
    }

    return invokePayloadOrReject<ApprovalSubmitResult>("approval_submit", payload);
  }

  async diffGet(payload: DiffGetParams): Promise<DiffGetResult> {
    if (shouldUseBrowserMock()) {
      const patch = mockState.patches[payload.patchId];
      if (!patch) {
        throw new Error(`未找到补丁：${payload.patchId}`);
      }
      return { patch, diffText: patch.diffText };
    }

    return invokePayloadOrReject<DiffGetResult>("diff_get", payload);
  }

  async commandLogList(payload: CommandLogListParams = {}): Promise<CommandLogListResult> {
    if (shouldUseBrowserMock()) {
      return {
        commandLogs: filterMockCommandLogs(payload),
      };
    }

    return invokePayloadOrReject<CommandLogListResult>("command_log_list", payload);
  }

  async commandLogGet(payload: CommandLogGetParams): Promise<CommandLogGetResult> {
    if (shouldUseBrowserMock()) {
      return {
        commandLog: getMockCommandLog(payload.commandId),
      };
    }

    return invokePayloadOrReject<CommandLogGetResult>("command_log_get", payload);
  }

  async commandCancel(payload: CommandCancelParams): Promise<CommandCancelResult> {
    if (shouldUseBrowserMock()) {
      const commandLog = getMockCommandLog(payload.commandId);
      const sessionId = getMockCommandSessionId(commandLog);
      const now = Date.now();
      emitBrowserEvent(
        buildMockEvent(sessionId, commandLog.taskId, "command.failed", {
          commandId: commandLog.id,
          command: commandLog.command,
          cwd: commandLog.cwd,
          shell: commandLog.shell,
          status: "killed",
          exitCode: commandLog.exitCode ?? null,
          durationMs: Math.max(0, now - commandLog.startedAt),
          stdoutPath: commandLog.stdoutPath,
          stderrPath: commandLog.stderrPath,
        }),
      );
      return {
        commandLog: getMockCommandLog(payload.commandId),
        cancelled: true,
      };
    }

    return invokePayloadOrReject<CommandCancelResult>("command_cancel", payload);
  }

  async getTask(taskId: string): Promise<TaskGetResult> {
    if (shouldUseBrowserMock()) {
      const task = mockState.tasks[taskId];
      if (!task) {
        throw new Error(`未找到任务：${taskId}`);
      }
      return { task };
    }
    return invokePayloadOrReject<TaskGetResult>("task_get", { taskId });
  }

  async cancelTask(payload: TaskCancelParams): Promise<TaskControlResult> {
    if (shouldUseBrowserMock()) {
      return applyMockTaskControl(payload.taskId, "cancelled", "task.cancelled");
    }
    return invokePayloadOrReject<TaskControlResult>("task_cancel", payload);
  }

  async pauseTask(payload: TaskPauseParams): Promise<TaskControlResult> {
    if (shouldUseBrowserMock()) {
      return applyMockTaskControl(payload.taskId, "paused", "task.paused");
    }
    return invokePayloadOrReject<TaskControlResult>("task_pause", payload);
  }

  async resumeTask(payload: TaskResumeParams): Promise<TaskControlResult> {
    if (shouldUseBrowserMock()) {
      return applyMockTaskControl(
        payload.taskId,
        (current) => (current.status === "paused" ? "waiting_approval" : current.status),
        "task.resumed",
      );
    }
    return invokePayloadOrReject<TaskControlResult>("task_resume", payload);
  }

  async listTasks(payload: TaskListParams = {}): Promise<TaskListResult> {
    if (shouldUseBrowserMock()) {
      const tasks = Object.values(mockState.tasks)
        .filter((task) => !payload.sessionId || task.sessionId === payload.sessionId)
        .sort((left, right) => right.updatedAt - left.updatedAt);
      return { tasks };
    }

    return invokePayloadOrReject<TaskListResult>("task_list", payload);
  }

  async createScheduledTask(payload: ScheduledTaskCreateParams): Promise<ScheduledTaskResult> {
    if (shouldUseBrowserMock()) {
      const task = buildMockScheduledTask(payload);
      mockState.scheduledTasks[task.id] = task;
      return { task };
    }

    const result = await invokePayloadOrReject<ScheduledTaskResult>("schedule_create", payload);
    mockState.scheduledTasks[result.task.id] = result.task;
    return result;
  }

  async listScheduledTasks(): Promise<ScheduledTaskListResult> {
    if (shouldUseBrowserMock()) {
      return {
        tasks: sortScheduledTasks(Object.values(mockState.scheduledTasks)),
      };
    }

    const result = await invokeOrReject<ScheduledTaskListResult>("schedule_list");
    mockState.scheduledTasks = Object.fromEntries(result.tasks.map((task) => [task.id, task]));
    return result;
  }

  async updateScheduledTask(payload: ScheduledTaskUpdateParams): Promise<ScheduledTaskResult> {
    if (shouldUseBrowserMock()) {
      const task = updateMockScheduledTask(payload.taskId, (current) => {
        const enabled = payload.enabled ?? current.enabled;
        const schedule = payload.schedule?.trim() || current.schedule;
        return {
          ...current,
          name: payload.name?.trim() || current.name,
          prompt: payload.prompt?.trim() || current.prompt,
          schedule,
          enabled,
          status: enabled ? "active" : "disabled",
          updatedAt: Date.now(),
          nextRunAt: enabled ? Date.now() + parseScheduleOffsetMs(schedule) : null,
        };
      });
      return { task };
    }

    const result = await invokePayloadOrReject<ScheduledTaskResult>("schedule_update", payload);
    mockState.scheduledTasks[result.task.id] = result.task;
    return result;
  }

  async toggleScheduledTask(payload: ScheduledTaskToggleParams): Promise<ScheduledTaskResult> {
    if (shouldUseBrowserMock()) {
      const task = updateMockScheduledTask(payload.taskId, (current) => ({
        ...current,
        enabled: payload.enabled,
        status: payload.enabled ? "active" : "disabled",
        updatedAt: Date.now(),
        nextRunAt: payload.enabled ? Date.now() + parseScheduleOffsetMs(current.schedule) : null,
      }));
      return { task };
    }

    const result = await invokePayloadOrReject<ScheduledTaskResult>("schedule_toggle", payload);
    mockState.scheduledTasks[result.task.id] = result.task;
    return result;
  }

  async runScheduledTaskNow(payload: ScheduledTaskRunNowParams): Promise<ScheduledTaskRunNowResult> {
    if (shouldUseBrowserMock()) {
      const startedAt = Date.now();
      const task = updateMockScheduledTask(payload.taskId, (current) => ({
        ...current,
        lastRunAt: startedAt,
        nextRunAt: current.enabled ? startedAt + parseScheduleOffsetMs(current.schedule) : null,
        updatedAt: startedAt,
      }));
      const run: ScheduledTaskRunRecord = {
        id: `schedrun_${startedAt}_${Math.random().toString(16).slice(2, 8)}`,
        taskId: task.id,
        status: "completed",
        startedAt,
        finishedAt: startedAt,
        durationMs: 0,
        summary: "浏览器 fallback 已记录立即运行；真实定时任务由桌面运行时执行。",
        error: null,
      };
      mockState.scheduledRuns = sortScheduledRuns([run, ...mockState.scheduledRuns]).slice(0, 500);
      return { run, task };
    }

    const result = await invokePayloadOrReject<ScheduledTaskRunNowResult>("schedule_run_now", payload);
    if (result.task) {
      mockState.scheduledTasks[result.task.id] = result.task;
    }
    mockState.scheduledRuns = sortScheduledRuns([result.run, ...mockState.scheduledRuns]).slice(0, 500);
    return result;
  }

  async listScheduledTaskLogs(payload: ScheduledTaskLogsParams = {}): Promise<ScheduledTaskLogsResult> {
    if (shouldUseBrowserMock()) {
      const logs = sortScheduledRuns(mockState.scheduledRuns)
        .filter((run) => !payload.taskId || run.taskId === payload.taskId)
        .slice(0, payload.limit ?? 100);
      return { logs };
    }

    const result = await invokePayloadOrReject<ScheduledTaskLogsResult>("schedule_logs", payload);
    mockState.scheduledRuns = sortScheduledRuns(result.logs);
    return result;
  }

  async listTrace(payload: TraceListParams): Promise<TraceListResult> {
    if (shouldUseBrowserMock()) {
      const limit = payload.limit ?? 50;
      return {
        traceEvents: mockState.traces
          .filter((trace) => trace.taskId === payload.taskId)
          .sort((left, right) => right.sequence - left.sequence)
          .slice(0, limit),
      };
    }

    return invokePayloadOrReject<TraceListResult>("trace_list", payload);
  }

  async getConfig(): Promise<ConfigGetResult> {
    if (shouldUseBrowserMock()) {
      return {
        config: mockState.config,
      };
    }
    const result = await invokeOrReject<ConfigGetResult>("config_get");
    mockState.config = mergeRuntimeConfig(mockState.config, result.config as RuntimeConfig);
    return result;
  }

  async updateConfig(payload: ConfigUpdateParams): Promise<ConfigUpdateResult> {
    if (shouldUseBrowserMock()) {
      mockState.config = mergeRuntimeConfig(mockState.config, payload);
      return {
        config: mockState.config,
      };
    }

    const result = await invokePayloadOrReject<ConfigUpdateResult>("config_update", payload);
    mockState.config = mergeRuntimeConfig(mockState.config, result.config as RuntimeConfig);
    return result;
  }

  async testProvider(payload: ProviderTestParams = {}): Promise<ProviderTestResult> {
    if (shouldUseBrowserMock()) {
      const result = buildProviderTestFallback(payload);
      rememberProviderTestResult(result);
      return result;
    }

    const result = await withTimeout(
      invokePayloadOrReject<ProviderTestResult>("provider_test", payload),
      12_000,
          "供应商测试超时。请检查 API 密钥、基础 URL 和网络连接。",
    );
    rememberProviderTestResult(result);
    return result;
  }

  async exportLogs(payload?: { sessionId?: string }): Promise<Record<string, unknown>> {
    if (shouldUseBrowserMock()) {
      return {
        exportedAt: Date.now(),
        sessions: [], tasks: [], messages: [], commandLogs: [],
        patches: [], approvals: [], traceEvents: [], config: mockState.config,
      };
    }
    return invokePayloadOrReject<Record<string, unknown>>("log_export", payload ?? {});
  }

  async listErrors(payload?: { sessionId?: string; taskId?: string; source?: string; limit?: number }) {
    if (shouldUseBrowserMock()) {
      return { errors: [], summary: { totalErrors: 0, bySource: {} } };
    }
    return invokePayloadOrReject<{ errors: unknown[]; summary: { totalErrors: number; bySource: Record<string, number> } }>(
      "errors_list", payload ?? {}
    );
  }

  async listMetrics(payload?: { sessionId?: string; limit?: number }) {
    if (shouldUseBrowserMock()) {
      return { metrics: [] };
    }
    return invokePayloadOrReject<{ metrics: unknown[] }>("metrics_list", payload ?? {});
  }

  async listSkills(payload: SkillListParams = {}): Promise<SkillListResult> {
    if (shouldUseBrowserMock()) {
      return {
        skills: sortSkills(
          Object.values(mockState.skills)
            .map(normalizeSkillRecord)
            .filter((skill) => !payload.category || skill.category === payload.category),
        ),
      };
    }

    const result = await invokePayloadOrReject<SkillListResult>("skill_list", payload);
    const skills = sortSkills(result.skills.map(normalizeSkillRecord));
    mockState.skills = Object.fromEntries(skills.map((skill) => [skill.id, skill]));
    return { skills };
  }

  async createSkill(payload: SkillCreateParams): Promise<SkillResult> {
    if (shouldUseBrowserMock()) {
      const skill = buildMockSkill(payload);
      mockState.skills[skill.id] = skill;
      return { skill };
    }

    const result = await invokePayloadOrReject<SkillResult>("skill_create", payload);
    const skill = normalizeSkillRecord(result.skill);
    mockState.skills[skill.id] = skill;
    return { skill };
  }

  async updateSkill(payload: SkillUpdateParams): Promise<SkillResult> {
    if (shouldUseBrowserMock()) {
      const current = mockState.skills[payload.skillId];
      if (!current) {
        throw new Error(`未找到技能：${payload.skillId}`);
      }
      const skill = normalizeSkillRecord({
        ...current,
        ...payload,
        id: current.id,
        systemPrompt: payload.systemPrompt ?? payload.system_prompt ?? current.systemPrompt,
        toolWhitelist: payload.toolWhitelist ?? payload.tool_whitelist ?? current.toolWhitelist,
        parameterConstraints:
          payload.parameterConstraints ?? payload.parameter_constraints ?? current.parameterConstraints,
        updatedAt: Date.now(),
      });
      mockState.skills[skill.id] = skill;
      return { skill };
    }

    const result = await invokePayloadOrReject<SkillResult>("skill_update", payload);
    const skill = normalizeSkillRecord(result.skill);
    mockState.skills[skill.id] = skill;
    return { skill };
  }

  async deleteSkill(payload: SkillDeleteParams): Promise<SkillDeleteResult> {
    if (shouldUseBrowserMock()) {
      delete mockState.skills[payload.skillId];
      return { deleted: true, skillId: payload.skillId };
    }

    const result = await invokePayloadOrReject<SkillDeleteResult>("skill_delete", payload);
    delete mockState.skills[payload.skillId];
    return result;
  }

  async importSkills(payload: SkillImportParams): Promise<SkillImportResult> {
    if (shouldUseBrowserMock()) {
      return { imported: [], skipped: [], errors: [] };
    }

    const result = await invokePayloadOrReject<SkillImportResult>("skill_import", payload);
    return result;
  }

  async listMcpServers(payload: McpServerListParams = {}): Promise<McpServerListResult> {
    if (shouldUseBrowserMock()) {
      return {
        servers: sortMcpServers(
          Object.values(mockState.mcpServers)
            .map(normalizeMcpServerRecord)
            .filter((server) => !payload.enabledOnly || server.enabled),
        ),
      };
    }

    const result = await invokePayloadOrReject<McpServerListResult>("mcp_server_list", payload);
    const servers = sortMcpServers(result.servers.map(normalizeMcpServerRecord));
    mockState.mcpServers = Object.fromEntries(servers.map((server) => [server.id, server]));
    return { servers };
  }

  async createMcpServer(payload: McpServerCreateParams): Promise<McpServerResult> {
    if (shouldUseBrowserMock()) {
      const server = buildMockMcpServer(payload);
      mockState.mcpServers[server.id] = server;
      return { server };
    }

    const result = await invokePayloadOrReject<McpServerResult>("mcp_server_create", payload);
    const server = normalizeMcpServerRecord(result.server);
    mockState.mcpServers[server.id] = server;
    return { server };
  }

  async updateMcpServer(payload: McpServerUpdateParams): Promise<McpServerResult> {
    if (shouldUseBrowserMock()) {
      const current = mockState.mcpServers[payload.serverId];
      if (!current) {
        throw new Error(`未找到 MCP 服务器：${payload.serverId}`);
      }
      const server = normalizeMcpServerRecord({
        ...current,
        ...payload,
        id: current.id,
        updatedAt: Date.now(),
      });
      mockState.mcpServers[server.id] = server;
      return { server };
    }

    const result = await invokePayloadOrReject<McpServerResult>("mcp_server_update", payload);
    const server = normalizeMcpServerRecord(result.server);
    mockState.mcpServers[server.id] = server;
    return { server };
  }

  async deleteMcpServer(payload: McpServerDeleteParams): Promise<McpServerDeleteResult> {
    if (shouldUseBrowserMock()) {
      delete mockState.mcpServers[payload.serverId];
      return { deleted: true, serverId: payload.serverId };
    }

    const result = await invokePayloadOrReject<McpServerDeleteResult>("mcp_server_delete", payload);
    delete mockState.mcpServers[payload.serverId];
    return result;
  }

  async refreshMcpTools(payload: McpToolsRefreshParams = {}): Promise<McpToolsRefreshRpcResult> {
    if (shouldUseBrowserMock()) {
      const serverIds = payload.serverId ? [payload.serverId] : Object.keys(mockState.mcpServers);
      return {
        refreshed: serverIds.length,
        tools: serverIds.flatMap((serverId) => [
          `mcp__${serverId}__inspect`,
          `mcp__${serverId}__run`,
        ]),
      };
    }

    return invokePayloadOrReject<McpToolsRefreshRpcResult>("mcp_tools_refresh", payload);
  }

  async subscribeEvents(handler: (event: AgentEventEnvelope) => void): Promise<() => void> {
    if (shouldUseBrowserMock()) {
      const listener = (event: Event) => {
        handler((event as CustomEvent<AgentEventEnvelope>).detail);
      };

      browserEventTarget.addEventListener(EVENT_CHANNEL, listener);
      return () => {
        browserEventTarget.removeEventListener(EVENT_CHANNEL, listener);
      };
    }

    assertRuntimeBridgeAvailable("agent_event_subscribe");
    const unlisten = await listen<AgentEventEnvelope>(EVENT_CHANNEL, (event) => {
      handler(event.payload);
    });
    return () => {
      unlisten();
    };
  }
}
