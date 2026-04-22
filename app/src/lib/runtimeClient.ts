import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import type {
  ApprovalRecord,
  ApprovalSubmitParams,
  ApprovalSubmitResult,
  AgentEventEnvelope,
  AppConfig,
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
  SessionCreateParams,
  SessionCreateResult,
  SessionListResult,
  SessionRecord,
  TaskGetResult,
  TaskListParams,
  TaskListResult,
  TaskRecord,
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

interface MockState {
  config: RuntimeConfig;
  workspace: WorkspaceOpenResult["workspace"] | null;
  sessions: SessionRecord[];
  tasks: Record<string, TaskRecord>;
  patches: Record<string, PatchRecord>;
  approvals: Record<string, ApprovalRecord>;
  traces: TraceEventRecord[];
}

const mockState: MockState = {
  config: buildMockRuntimeConfig(),
  workspace: null,
  sessions: [],
  tasks: {},
  patches: {},
  approvals: {},
  traces: [],
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
  return {
    ...config,
    provider: {
      mode: "mock",
      baseUrl: "https://api.openai.com/v1",
      model: config.provider.defaultModel,
      apiKeyEnvVarName: "LOCAL_AGENT_PROVIDER_API_KEY",
      maxTokens: config.provider.maxOutputTokens,
      maxContextTokens: 120000,
      timeout: 30,
      ...config.provider,
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
  const provider = {
    ...mockState.config.provider,
    ...params?.provider,
  };
  const mode = provider.mode ?? "mock";
  const model = provider.model ?? provider.defaultModel;
  const envVarName = provider.apiKeyEnvVarName ?? "LOCAL_AGENT_PROVIDER_API_KEY";

  if (mode === "mock") {
    return {
      ok: true,
      status: "mocked",
      message: "Mock provider path is ready. No API key value is stored in app config.",
      providerMode: mode,
      model,
      baseUrl: provider.baseUrl,
      checkedEnvVarName: envVarName,
      source: "mock-fallback",
    };
  }

  const compatibleModes = new Set(["openai", "openai-compatible", "openai_compatible", "openai-compatible-chat"]);
  if (compatibleModes.has(String(mode).toLowerCase()) && !reason) {
    return {
      ok: false,
      status: "missing_env",
      message: `Runtime provider test cannot read ${envVarName} in browser/mock fallback.`,
      providerMode: mode,
      model,
      baseUrl: provider.baseUrl,
      checkedEnvVarName: envVarName,
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
    providerMode: mode,
    model,
    baseUrl: provider.baseUrl,
    checkedEnvVarName: envVarName,
    source: "mock-fallback",
    details: {
      errorSummary,
    },
  };
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
      };

      emitBrowserEvent(
        buildMockEvent(task.sessionId, task.id, "approval.resolved", {
          approvalId: approval.id,
          taskId: task.id,
          decision: payload.decision,
        }),
      );

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

  async listTasks(payload: TaskListParams = {}): Promise<TaskListResult> {
    if (!isTauriBridgeAvailable()) {
      const tasks = Object.values(mockState.tasks)
        .filter((task) => !payload.sessionId || task.sessionId === payload.sessionId)
        .sort((left, right) => right.updatedAt - left.updatedAt);
      return { tasks };
    }

    return invokeOrReject<TaskListResult>("task_list", payload);
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
      return buildProviderTestFallback(payload);
    }

    try {
      return await invokeOrReject<ProviderTestResult>("provider_test", payload);
    } catch (reason) {
      return buildProviderTestFallback(payload, reason);
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
