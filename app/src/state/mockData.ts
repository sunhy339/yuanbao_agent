import type {
  AgentEventEnvelope,
  AppConfig,
  PlanStep,
  SessionRecord,
  TaskRecord,
  WorkspaceRef,
} from "@shared";

const now = Date.now();

export const DEFAULT_WORKSPACE_PATH = "D:/py/yuanbao_agent";
export const DEFAULT_SESSION_TITLE = "New Session";
export const DEFAULT_PROMPT = "";

export function buildMockWorkspace(path: string): WorkspaceRef {
  return {
    id: `ws_${Date.now()}`,
    name: path.split(/[\\/]/).filter(Boolean).pop() ?? "workspace",
    rootPath: path,
    focus: null,
    summary: null,
    createdAt: Date.now(),
    updatedAt: Date.now(),
  };
}

export function buildMockSession(workspaceId: string, title: string): SessionRecord {
  return {
    id: `sess_${Date.now()}`,
    workspaceId,
    title,
    status: "active",
    summary: "Browser mock session for the local coding agent shell.",
    createdAt: Date.now(),
    updatedAt: Date.now(),
  };
}

export function buildMockPlan(goal: string): PlanStep[] {
  return [
    {
      id: "collect-context",
      title: "Collect workspace context",
      status: "completed",
      detail: "Read the workspace shell and the initial configuration payload.",
    },
    {
      id: "inspect-request",
      title: "Inspect the task request",
      status: "active",
      detail: `Analyze the request: ${goal}`,
    },
    {
      id: "prepare-next-step",
      title: "Prepare the next action",
      status: "pending",
      detail: "Wait for tool results or more runtime events.",
    },
  ];
}

export function buildMockTask(sessionId: string, content: string): TaskRecord {
  const plan = buildMockPlan(content);
  return {
    id: `task_${Date.now()}`,
    sessionId,
    type: "edit",
    status: "queued",
    goal: content,
    acceptanceCriteria: ["Understand the request", "Make a focused change", "Report verification results"],
    outOfScope: ["Unrelated refactors", "Provider configuration changes"],
    currentStep: plan.find((step) => step.status === "active")?.title ?? plan[0]?.title,
    changedFiles: [],
    commands: [],
    verification: [],
    createdAt: Date.now(),
    updatedAt: Date.now(),
    plan,
  };
}

export function buildMockConfig(): AppConfig {
  return {
    provider: {
      mode: "mock",
      baseUrl: "https://api.openai.com/v1",
      model: "gpt-5-codex",
      defaultModel: "gpt-5-codex",
      fallbackModel: "claude-sonnet",
      apiKeyEnvVarName: "LOCAL_AGENT_PROVIDER_API_KEY",
      temperature: 0.2,
      maxTokens: 4000,
      maxOutputTokens: 4000,
      maxContextTokens: 120000,
      timeout: 30,
      activeProfileId: "default",
      profiles: [
        {
          id: "default",
          name: "Default",
          mode: "mock",
          baseUrl: "https://api.openai.com/v1",
          model: "gpt-5-codex",
          defaultModel: "gpt-5-codex",
          fallbackModel: "claude-sonnet",
          apiKeyEnvVarName: "LOCAL_AGENT_PROVIDER_API_KEY",
          temperature: 0.2,
          maxTokens: 4000,
          maxOutputTokens: 4000,
          maxContextTokens: 120000,
          timeout: 30,
          lastCheckedAt: now,
          lastStatus: "mocked",
          lastErrorSummary: "Mock mode does not contact a remote model.",
        },
      ],
    },
    workspace: {
      rootPath: DEFAULT_WORKSPACE_PATH,
      ignore: [".git", "node_modules", "dist", ".venv", "target"],
      writableRoots: [DEFAULT_WORKSPACE_PATH],
    },
    search: {
      glob: [],
      ignore: [".git", "node_modules", "dist", ".venv", "target", "__pycache__"],
    },
    policy: {
      approvalMode: "on_write_or_command",
      commandTimeoutMs: 600_000,
      maxTaskSteps: 20,
      maxPatchRepairAttempts: 2,
      maxFilesPerPatch: 20,
      allowNetwork: false,
    },
    tools: {
      runCommand: {
        allowedShell: "powershell",
        allowedCommands: [],
        allowlist: [],
        deniedCommands: [],
        denylist: [],
        blockedPatterns: ["rm -rf", "shutdown", "format"],
        allowedCwdRoots: [],
      },
    },
    ui: {
      language: "en",
      showRawEvents: false,
    },
  };
}

export function buildMockEvent<TPayload>(
  sessionId: string,
  taskId: string,
  type: AgentEventEnvelope<TPayload>["type"],
  payload: TPayload,
): AgentEventEnvelope<TPayload> {
  return {
    eventId: `evt_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`,
    sessionId,
    taskId,
    type,
    ts: Date.now(),
    payload,
  };
}

export const seedSession: SessionRecord = {
  id: "sess_demo",
  workspaceId: "ws_demo",
  title: "Fix pytest failure",
  status: "active",
  summary: "Browser mock demo session.",
  createdAt: now,
  updatedAt: now,
};

export const seedTask: TaskRecord = {
  id: "task_demo",
  sessionId: seedSession.id,
  type: "edit",
  status: "running",
  goal: "Run tests, inspect key files, then prepare a reviewable patch.",
  acceptanceCriteria: ["Inspect relevant files", "Prepare a reviewable patch", "Run the configured verification"],
  outOfScope: ["Rewrite unrelated UI"],
  currentStep: "Inspect the task request",
  changedFiles: [],
  commands: [],
  verification: [],
  createdAt: now,
  updatedAt: now,
  plan: buildMockPlan("Run tests, inspect key files, then prepare a reviewable patch."),
};
