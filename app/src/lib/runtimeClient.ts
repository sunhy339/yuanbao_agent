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
  MessageSendParams,
  MessageSendResult,
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
  SessionListResult,
  SessionRecord,
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
  WorkspaceOpenResult,
} from "@shared";
import {
  buildMockConfig,
  buildMockEvent,
  buildMockSession,
  buildMockTask,
  buildMockWorkspace,
} from "../state/mockData";

const EVENT_CHANNEL = "agent://event";
const browserEventTarget = new EventTarget();

export type RuntimeConfig = AppConfig & Required<Pick<AppConfig, "search">>;
export type RuntimeCommandLog = CommandLogRecord;

export interface CommandCancelResult {
  commandLog: CommandLogRecord;
  cancelled?: boolean;
}

interface MockState {
  config: RuntimeConfig;
  workspace: WorkspaceOpenResult["workspace"] | null;
  sessions: SessionRecord[];
  tasks: Record<string, TaskRecord>;
  patches: Record<string, PatchRecord>;
  approvals: Record<string, ApprovalRecord>;
  traces: TraceEventRecord[];
  scheduledTasks: Record<string, ScheduledTaskRecord>;
  scheduledRuns: ScheduledTaskRunRecord[];
}

const mockState: MockState = {
  config: buildMockRuntimeConfig(),
  workspace: null,
  sessions: [],
  tasks: {},
  patches: {},
  approvals: {},
  traces: [],
  scheduledTasks: {},
  scheduledRuns: [],
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
    throw new Error(`Task not found: ${taskId}`);
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
    throw new Error(`Command log not found: ${commandId}`);
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
    throw new Error(`Scheduled task not found: ${taskId}`);
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
      message: "Mock provider path is ready. No API key value is stored in app config.",
      profileId: providerProfileId,
      profileName: providerProfileName,
      providerMode: mode,
      model,
      baseUrl: provider.baseUrl,
      checkedEnvVarName: envVarName,
      envVarName,
      lastCheckedAt: checkedAt,
      lastStatus: "mocked",
      lastErrorSummary: "Mock mode does not contact a remote model.",
      source: "mock-fallback",
    };
  }

  const compatibleModes = new Set(["openai", "openai-compatible", "openai_compatible", "openai-compatible-chat"]);
  if (compatibleModes.has(String(mode).toLowerCase()) && !reason) {
    return {
      ok: false,
      status: "missing_env",
      message: `Runtime provider test cannot read ${envVarName} in browser/mock fallback.`,
      profileId: providerProfileId,
      profileName: providerProfileName,
      providerMode: mode,
      model,
      baseUrl: provider.baseUrl,
      checkedEnvVarName: envVarName,
      envVarName,
      lastCheckedAt: checkedAt,
      lastStatus: "missing_env",
      lastErrorSummary: `Set ${envVarName} in the desktop runtime environment, then run Test Connection from the Tauri app.`,
      source: "mock-fallback",
      details: {
        errorSummary: `Set ${envVarName} in the desktop runtime environment, then run Test Connection from the Tauri app.`,
      },
    };
  }

  const errorSummary = reason instanceof Error ? reason.message : "Provider test backend is not available yet.";
  return {
    ok: false,
    status: compatibleModes.has(String(mode).toLowerCase()) ? "failed" : "unsupported",
    message:
      reason instanceof Error
        ? `Provider test backend is not available yet: ${reason.message}`
        : "Provider test backend is not available yet.",
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
          detail: "Browser mock mode started a simulated task.",
        }),
      );
    }
  }, 60);

  window.setTimeout(() => {
    emitBrowserEvent(
      buildMockEvent(sessionId, task.id, "assistant.token", {
        delta: "Browser mock mode is active. ",
      }),
    );
  }, 140);

  window.setTimeout(() => {
    emitBrowserEvent(
      buildMockEvent(sessionId, task.id, "assistant.token", {
        delta: "Launch the desktop app through Tauri to talk to the Python runtime.",
      }),
    );
  }, 180);

  window.setTimeout(() => {
    emitBrowserEvent(
      buildMockEvent(sessionId, task.id, "assistant.message.completed", {
        summary: "Browser mock assistant response completed.",
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
            return { ...step, status: "completed", detail: "Mock request analysis completed." };
          }
          if (step.id === "prepare-next-step") {
            return { ...step, status: "active", detail: "Waiting for approval to run the command." };
          }
          return step;
        }) ?? current.plan,
    }));

    emitBrowserEvent(
      buildMockEvent(sessionId, task.id, "task.updated", {
        status: "waiting_approval",
        plan: mockState.tasks[task.id]?.plan,
        detail: "Waiting for approval to run the command.",
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
        detail: "Command requires approval before execution.",
      }),
    );
  }, 320);
}

async function invokeOrReject<T>(command: string, payload?: unknown): Promise<T> {
  try {
    return await invoke<T>(command, payload as Record<string, unknown> | undefined);
  } catch (reason) {
    throw reason instanceof Error ? reason : new Error(String(reason));
  }
}

export class RuntimeClient {
  async getHostStatus(): Promise<HostStatus> {
    if (!isTauriBridgeAvailable()) {
      return buildMockHostStatus();
    }
    return invokeOrReject<HostStatus>("host_status");
  }

  async openWorkspace(path: string): Promise<WorkspaceOpenResult> {
    if (!isTauriBridgeAvailable()) {
      const result = {
        workspace: buildMockWorkspace(path),
      } satisfies WorkspaceOpenResult;
      mockState.workspace = result.workspace;
      mockState.sessions = [];
      mockState.tasks = {};
      mockState.patches = {};
      mockState.approvals = {};
      mockState.traces = [];
      mockState.scheduledTasks = {};
      mockState.scheduledRuns = [];
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
    mockState.tasks = {};
    mockState.patches = {};
    mockState.approvals = {};
    mockState.traces = [];
    mockState.scheduledTasks = {};
    mockState.scheduledRuns = [];
    mockState.config = mergeRuntimeConfig(mockState.config, {
      workspace: {
        ignore: mockState.config.workspace.ignore,
        rootPath: result.workspace.rootPath,
        writableRoots: [result.workspace.rootPath],
      },
    });
    return result;
  }

  async createSession(payload: SessionCreateParams): Promise<SessionCreateResult> {
    if (!isTauriBridgeAvailable()) {
      const result = {
        session: buildMockSession(payload.workspaceId, payload.title),
      } satisfies SessionCreateResult;
      rememberSession(result.session);
      return result;
    }
    const result = await invokeOrReject<SessionCreateResult>("session_create", payload);
    rememberSession(result.session);
    return result;
  }

  async listSessions(): Promise<SessionListResult> {
    if (!isTauriBridgeAvailable()) {
      return {
        sessions: sortSessions(mockState.sessions),
      };
    }

    try {
      const result = await invokeOrReject<SessionListResult>("session_list");
      mockState.sessions = sortSessions(result.sessions);
      return result;
    } catch {
      return {
        sessions: sortSessions(mockState.sessions),
      };
    }
  }

  async sendMessage(payload: MessageSendParams): Promise<MessageSendResult> {
    if (!isTauriBridgeAvailable()) {
      const task = buildMockTask(payload.sessionId, payload.content);
      mockState.tasks[task.id] = task;
      emitMockTaskSequence(payload.sessionId, task);
      return { task };
    }
    return invokeOrReject<MessageSendResult>("message_send", payload);
  }

  async approvalSubmit(payload: ApprovalSubmitParams): Promise<ApprovalSubmitResult> {
    if (!isTauriBridgeAvailable()) {
      const currentApproval = mockState.approvals[payload.approvalId];
      if (!currentApproval) {
        throw new Error(`Approval not found: ${payload.approvalId}`);
      }

      if (currentApproval.decision && currentApproval.decision !== payload.decision) {
        throw new Error(`Approval already resolved as ${currentApproval.decision}`);
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
        throw new Error(`Task not found: ${approval.taskId}`);
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
                ? { ...step, status: "completed", detail: "Approval granted; command is running." }
                : step,
            ) ?? current.plan,
        }));

        if (runningTask) {
          emitBrowserEvent(
            buildMockEvent(task.sessionId, task.id, "task.updated", {
              status: "running",
              plan: runningTask.plan,
              detail: "Approval accepted",
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
            chunk: `Approved command finished successfully: ${request.command ?? "pytest"}\n`,
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
            summary: "Mock command completed successfully.",
          }),
        );

        const completedTask = updateMockTask(task.id, (current) => ({
          ...current,
          status: "completed",
          resultSummary: "Mock mode completed the approved command and published output.",
          updatedAt: Date.now(),
          plan:
            current.plan?.map((step) =>
              step.id === "prepare-next-step"
                ? { ...step, status: "completed", detail: "Approved command finished in mock mode." }
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
                ? { ...step, status: "failed", detail: "Approval was rejected by the user." }
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

    return invokeOrReject<ApprovalSubmitResult>("approval_submit", payload);
  }

  async diffGet(payload: DiffGetParams): Promise<DiffGetResult> {
    if (!isTauriBridgeAvailable()) {
      const patch = mockState.patches[payload.patchId];
      if (!patch) {
        throw new Error(`Patch not found: ${payload.patchId}`);
      }
      return { patch, diffText: patch.diffText };
    }

    try {
      return await invokeOrReject<DiffGetResult>("diff_get", payload);
    } catch {
      const patch = mockState.patches[payload.patchId];
      if (!patch) {
        throw new Error(`Patch not found: ${payload.patchId}`);
      }
      return { patch, diffText: patch.diffText };
    }
  }

  async commandLogList(payload: CommandLogListParams = {}): Promise<CommandLogListResult> {
    if (!isTauriBridgeAvailable()) {
      return {
        commandLogs: filterMockCommandLogs(payload),
      };
    }

    try {
      return await invokeOrReject<CommandLogListResult>("command_log_list", payload);
    } catch {
      return {
        commandLogs: filterMockCommandLogs(payload),
      };
    }
  }

  async commandLogGet(payload: CommandLogGetParams): Promise<CommandLogGetResult> {
    if (!isTauriBridgeAvailable()) {
      return {
        commandLog: getMockCommandLog(payload.commandId),
      };
    }

    try {
      return await invokeOrReject<CommandLogGetResult>("command_log_get", payload);
    } catch {
      return {
        commandLog: getMockCommandLog(payload.commandId),
      };
    }
  }

  async commandCancel(payload: CommandCancelParams): Promise<CommandCancelResult> {
    if (!isTauriBridgeAvailable()) {
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

    try {
      return await invokeOrReject<CommandCancelResult>("command_cancel", payload);
    } catch {
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
  }

  async getTask(taskId: string): Promise<TaskGetResult> {
    if (!isTauriBridgeAvailable()) {
      const task = mockState.tasks[taskId];
      if (!task) {
        throw new Error(`Task not found: ${taskId}`);
      }
      return { task };
    }
    return invokeOrReject<TaskGetResult>("task_get", { taskId });
  }

  async cancelTask(payload: TaskCancelParams): Promise<TaskControlResult> {
    if (!isTauriBridgeAvailable()) {
      return applyMockTaskControl(payload.taskId, "cancelled", "task.cancelled");
    }
    try {
      return await invokeOrReject<TaskControlResult>("task_cancel", payload);
    } catch {
      return applyMockTaskControl(payload.taskId, "cancelled", "task.cancelled");
    }
  }

  async pauseTask(payload: TaskPauseParams): Promise<TaskControlResult> {
    if (!isTauriBridgeAvailable()) {
      return applyMockTaskControl(payload.taskId, "paused", "task.paused");
    }
    try {
      return await invokeOrReject<TaskControlResult>("task_pause", payload);
    } catch {
      return applyMockTaskControl(payload.taskId, "paused", "task.paused");
    }
  }

  async resumeTask(payload: TaskResumeParams): Promise<TaskControlResult> {
    if (!isTauriBridgeAvailable()) {
      return applyMockTaskControl(
        payload.taskId,
        (current) => (current.status === "paused" ? "waiting_approval" : current.status),
        "task.resumed",
      );
    }
    try {
      return await invokeOrReject<TaskControlResult>("task_resume", payload);
    } catch {
      return applyMockTaskControl(
        payload.taskId,
        (current) => (current.status === "paused" ? "waiting_approval" : current.status),
        "task.resumed",
      );
    }
  }

  async listTasks(payload: TaskListParams = {}): Promise<TaskListResult> {
    if (!isTauriBridgeAvailable()) {
      const tasks = Object.values(mockState.tasks)
        .filter((task) => !payload.sessionId || task.sessionId === payload.sessionId)
        .sort((left, right) => right.updatedAt - left.updatedAt);
      return { tasks };
    }

    return invokeOrReject<TaskListResult>("task_list", payload);
  }

  async createScheduledTask(payload: ScheduledTaskCreateParams): Promise<ScheduledTaskResult> {
    if (!isTauriBridgeAvailable()) {
      const task = buildMockScheduledTask(payload);
      mockState.scheduledTasks[task.id] = task;
      return { task };
    }

    const result = await invokeOrReject<ScheduledTaskResult>("schedule_create", payload);
    mockState.scheduledTasks[result.task.id] = result.task;
    return result;
  }

  async listScheduledTasks(): Promise<ScheduledTaskListResult> {
    if (!isTauriBridgeAvailable()) {
      return {
        tasks: sortScheduledTasks(Object.values(mockState.scheduledTasks)),
      };
    }

    const result = await invokeOrReject<ScheduledTaskListResult>("schedule_list");
    mockState.scheduledTasks = Object.fromEntries(result.tasks.map((task) => [task.id, task]));
    return result;
  }

  async updateScheduledTask(payload: ScheduledTaskUpdateParams): Promise<ScheduledTaskResult> {
    if (!isTauriBridgeAvailable()) {
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

    const result = await invokeOrReject<ScheduledTaskResult>("schedule_update", payload);
    mockState.scheduledTasks[result.task.id] = result.task;
    return result;
  }

  async toggleScheduledTask(payload: ScheduledTaskToggleParams): Promise<ScheduledTaskResult> {
    if (!isTauriBridgeAvailable()) {
      const task = updateMockScheduledTask(payload.taskId, (current) => ({
        ...current,
        enabled: payload.enabled,
        status: payload.enabled ? "active" : "disabled",
        updatedAt: Date.now(),
        nextRunAt: payload.enabled ? Date.now() + parseScheduleOffsetMs(current.schedule) : null,
      }));
      return { task };
    }

    const result = await invokeOrReject<ScheduledTaskResult>("schedule_toggle", payload);
    mockState.scheduledTasks[result.task.id] = result.task;
    return result;
  }

  async runScheduledTaskNow(payload: ScheduledTaskRunNowParams): Promise<ScheduledTaskRunNowResult> {
    if (!isTauriBridgeAvailable()) {
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
        summary: "Run now recorded in browser fallback; desktop runtime executes real scheduled jobs.",
        error: null,
      };
      mockState.scheduledRuns = sortScheduledRuns([run, ...mockState.scheduledRuns]).slice(0, 500);
      return { run, task };
    }

    const result = await invokeOrReject<ScheduledTaskRunNowResult>("schedule_run_now", payload);
    if (result.task) {
      mockState.scheduledTasks[result.task.id] = result.task;
    }
    mockState.scheduledRuns = sortScheduledRuns([result.run, ...mockState.scheduledRuns]).slice(0, 500);
    return result;
  }

  async listScheduledTaskLogs(payload: ScheduledTaskLogsParams = {}): Promise<ScheduledTaskLogsResult> {
    if (!isTauriBridgeAvailable()) {
      const logs = sortScheduledRuns(mockState.scheduledRuns)
        .filter((run) => !payload.taskId || run.taskId === payload.taskId)
        .slice(0, payload.limit ?? 100);
      return { logs };
    }

    const result = await invokeOrReject<ScheduledTaskLogsResult>("schedule_logs", payload);
    mockState.scheduledRuns = sortScheduledRuns(result.logs);
    return result;
  }

  async listTrace(payload: TraceListParams): Promise<TraceListResult> {
    if (!isTauriBridgeAvailable()) {
      const limit = payload.limit ?? 50;
      return {
        traceEvents: mockState.traces
          .filter((trace) => trace.taskId === payload.taskId)
          .sort((left, right) => right.sequence - left.sequence)
          .slice(0, limit),
      };
    }

    try {
      return await invokeOrReject<TraceListResult>("trace_list", payload);
    } catch {
      const limit = payload.limit ?? 50;
      return {
        traceEvents: mockState.traces
          .filter((trace) => trace.taskId === payload.taskId)
          .sort((left, right) => right.sequence - left.sequence)
          .slice(0, limit),
      };
    }
  }

  async getConfig(): Promise<ConfigGetResult> {
    if (!isTauriBridgeAvailable()) {
      return {
        config: mockState.config,
      };
    }
    const result = await invokeOrReject<ConfigGetResult>("config_get");
    mockState.config = mergeRuntimeConfig(mockState.config, result.config as RuntimeConfig);
    return result;
  }

  async updateConfig(payload: ConfigUpdateParams): Promise<ConfigUpdateResult> {
    if (!isTauriBridgeAvailable()) {
      mockState.config = mergeRuntimeConfig(mockState.config, payload);
      return {
        config: mockState.config,
      };
    }

    try {
      const result = await invokeOrReject<ConfigUpdateResult>("config_update", payload);
      mockState.config = mergeRuntimeConfig(mockState.config, result.config as RuntimeConfig);
      return result;
    } catch {
      mockState.config = mergeRuntimeConfig(mockState.config, payload);
      return {
        config: mockState.config,
      };
    }
  }

  async testProvider(payload: ProviderTestParams = {}): Promise<ProviderTestResult> {
    if (!isTauriBridgeAvailable()) {
      const result = buildProviderTestFallback(payload);
      rememberProviderTestResult(result);
      return result;
    }

    try {
      const result = await invokeOrReject<ProviderTestResult>("provider_test", payload);
      rememberProviderTestResult(result);
      return result;
    } catch (reason) {
      const result = buildProviderTestFallback(payload, reason);
      rememberProviderTestResult(result);
      return result;
    }
  }

  async subscribeEvents(handler: (event: AgentEventEnvelope) => void): Promise<() => void> {
    if (!isTauriBridgeAvailable()) {
      const listener = (event: Event) => {
        handler((event as CustomEvent<AgentEventEnvelope>).detail);
      };

      browserEventTarget.addEventListener(EVENT_CHANNEL, listener);
      return () => {
        browserEventTarget.removeEventListener(EVENT_CHANNEL, listener);
      };
    }

    const unlisten = await listen<AgentEventEnvelope>(EVENT_CHANNEL, (event) => {
      handler(event.payload);
    });
    return () => {
      unlisten();
    };
  }
}
