import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import type {
  ApprovalRecord,
  ApprovalSubmitParams,
  ApprovalSubmitResult,
  AgentEventEnvelope,
  AppConfig,
  ConfigGetResult,
  ConfigUpdateResult,
  MessageSendParams,
  MessageSendResult,
  SessionCreateParams,
  SessionCreateResult,
  SessionListResult,
  SessionRecord,
  TaskGetResult,
  TaskRecord,
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
  approvals: Record<string, ApprovalRecord>;
}

const mockState: MockState = {
  config: buildMockRuntimeConfig(),
  workspace: null,
  sessions: [],
  tasks: {},
  approvals: {},
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
  browserEventTarget.dispatchEvent(new CustomEvent(EVENT_CHANNEL, { detail: event }));
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
    search: config.search ?? {
      glob: [],
      ignore: config.workspace.ignore,
    },
  };
}

function sortSessions(sessions: SessionRecord[]): SessionRecord[] {
  return [...sessions].sort((left, right) => right.updatedAt - left.updatedAt);
}

function mergeRuntimeConfig(current: RuntimeConfig, next: Partial<RuntimeConfig>): RuntimeConfig {
  return {
    ...current,
    ...next,
    provider: {
      ...current.provider,
      ...next.provider,
    },
    workspace: {
      ...current.workspace,
      ...next.workspace,
    },
    search: {
      ...current.search,
      ...next.search,
    },
    policy: {
      ...current.policy,
      ...next.policy,
    },
    tools: {
      ...current.tools,
      ...next.tools,
      runCommand: {
        ...current.tools.runCommand,
        ...next.tools?.runCommand,
      },
    },
    ui: {
      ...current.ui,
      ...next.ui,
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

function getMockApprovalRequest(command: string, cwd: string, shell: string, timeoutMs: number): Record<string, unknown> {
  return {
    command,
    cwd,
    shell,
    timeoutMs,
    workspaceRoot: mockState.workspace?.rootPath ?? mockState.config.workspace.rootPath,
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
        delta: "Browser mock mode is active. Launch the desktop app through Tauri to talk to the Python runtime.",
      }),
    );
  }, 140);

  window.setTimeout(() => {
    emitBrowserEvent(
      buildMockEvent(sessionId, task.id, "tool.started", {
        toolCallId: `tool_${Date.now()}`,
        toolName: "search_files",
        arguments: {
          query: "pytest",
          mode: "content",
        },
      }),
    );
  }, 220);

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
      mockState.approvals = {};
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
    mockState.approvals = {};
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
          buildMockEvent(task.sessionId, task.id, "command.output", {
            commandId: `cmd_${approval.id}`,
            stream: "stdout",
            chunk: `Approved command finished successfully: ${request.command ?? "pytest"}\n`,
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
        }
      }

      return { approval };
    }

    return invokeOrReject<ApprovalSubmitResult>("approval_submit", payload);
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

  async updateConfig(payload: RuntimeConfig): Promise<ConfigUpdateResult> {
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
