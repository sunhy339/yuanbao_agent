import type { ApprovalMode, ProviderMode } from "./domain";

export type DeepPartial<T> = {
  [K in keyof T]?: T[K] extends (infer U)[] ? U[] : T[K] extends object ? DeepPartial<T[K]> : T[K];
};

export interface ProviderProfile {
  id: string;
  name: string;
  mode?: ProviderMode;
  baseUrl?: string;
  apiFormat?: ProviderApiFormat | string;
  model?: string;
  defaultModel?: string;
  fallbackModel?: string;
  apiKeyEnvVarName?: string;
  apiKey?: string;
  temperature?: number;
  maxTokens?: number;
  maxOutputTokens?: number;
  maxContextTokens?: number;
  timeout?: number;
  lastCheckedAt?: number;
  lastStatus?: string;
  lastErrorSummary?: string;
}

export interface ProviderConfig {
  mode?: ProviderMode;
  baseUrl?: string;
  apiFormat?: ProviderApiFormat | string;
  model?: string;
  defaultModel: string;
  fallbackModel?: string;
  apiKeyEnvVarName?: string;
  temperature: number;
  maxTokens?: number;
  maxOutputTokens: number;
  maxContextTokens?: number;
  timeout?: number;
  activeProfileId?: string;
  profiles?: ProviderProfile[];
}

export type ProviderApiFormat =
  | "openai-chat"
  | "chat-completions"
  | "custom-openai-compatible"
  | "openai-responses"
  | "anthropic-messages";

export const DEFAULT_PROVIDER_API_FORMAT: ProviderApiFormat = "openai-chat";

export const SUPPORTED_PROVIDER_API_FORMATS = [
  "openai-chat",
  "chat-completions",
  "custom-openai-compatible",
] as const satisfies readonly ProviderApiFormat[];

export function normalizeProviderApiFormat(value?: string | null): ProviderApiFormat {
  const normalized = String(value || "").trim().toLowerCase().replace(/_/g, "-");
  if (normalized === "chat-completions" || normalized === "custom-openai-compatible") {
    return "openai-chat";
  }
  if (
    normalized === "openai-chat" ||
    normalized === "openai-responses" ||
    normalized === "anthropic-messages"
  ) {
    return normalized;
  }
  return DEFAULT_PROVIDER_API_FORMAT;
}

export function isProviderApiFormatSupported(value?: string | null): boolean {
  return normalizeProviderApiFormat(value) === "openai-chat";
}

export interface WorkspaceConfig {
  rootPath: string;
  ignore: string[];
  writableRoots: string[];
}

export interface SearchConfig {
  glob: string[];
  ignore: string[];
}

export interface PolicyConfig {
  approvalMode: ApprovalMode;
  commandTimeoutMs: number;
  maxTaskSteps: number;
  maxPatchRepairAttempts: number;
  maxFilesPerPatch: number;
  allowNetwork: boolean;
}

export type AutonomyLevel = "L0" | "L1" | "L2" | "L3" | "L4";
export type AuthorityPolicy = "blocked" | "approval_required" | "allowed";

export interface AutonomyProfile {
  id: string;
  name: string;
  level: AutonomyLevel | string;
  maxSteps: number;
  maxParallelSubtasks: number;
  allowBackground: boolean;
  allowSubagents: boolean;
  allowFileWrite: AuthorityPolicy | string;
  allowShell: AuthorityPolicy | string;
  allowNetwork: boolean;
  memoryRecallPolicy: string;
  retryLimit: number;
  timeoutMs: number;
}

export interface AutonomyConfig {
  activeProfileId: string;
  profiles: AutonomyProfile[];
}

export interface AgentSoulProfile {
  id: string;
  name: string;
  description?: string;
  identity: string;
  principles: string[];
  communicationStyle: string;
  reasoningStyle: string;
  collaborationStyle: string;
  domainPreferences: string[];
  customSystemPrompt: string;
  enabled: boolean;
  scope: string;
  createdAt?: number;
  updatedAt?: number;
}

export interface AgentSoulConfig {
  activeProfileId: string;
  workspaceInstructions: string;
  sessionOverrideEnabled?: boolean;
  profiles: AgentSoulProfile[];
}

export interface ToolRuntimeConfig {
  allowedShell: "powershell" | "bash" | "zsh";
  allowedCommands?: string[];
  allowlist?: string[];
  deniedCommands?: string[];
  denylist?: string[];
  blockedPatterns: string[];
  allowedCwdRoots?: string[];
}

export interface UiConfig {
  language: string;
  showRawEvents: boolean;
  theme?: "light" | "dark" | "system";
  density?: "comfortable" | "compact";
  radius?: "sm" | "md" | "lg";
  motion?: "reduced" | "subtle" | "expressive";
  accentColor?: "cyan" | "violet" | "green" | "amber" | "rose";
  transparency?: number;
  fontScale?: number;
  reasoningEffort?: "low" | "medium" | "high" | "max";
  webFetchPreflight?: boolean;
}

export type CapabilityMode = "allow" | "ask" | "blocked";
export type CapabilityName =
  | "readFile"
  | "writeFile"
  | "runCommand"
  | "webFetch"
  | "network"
  | "subagents"
  | "memoryWrite"
  | "gitWrite"
  | "browserAutomation"
  | "computerUse"
  | "hooksExecute";
export type PermissionPreset = "safe" | "balanced" | "autonomous";
export interface CapabilityRule {
  mode: CapabilityMode;
  scope: string;
}
export interface PermissionsConfig {
  preset: PermissionPreset;
  capabilities: Partial<Record<CapabilityName, CapabilityRule>>;
}

export interface AppConfig {
  provider: ProviderConfig;
  workspace: WorkspaceConfig;
  search: SearchConfig;
  policy: PolicyConfig;
  autonomy: AutonomyConfig;
  agentSoul: AgentSoulConfig;
  permissions: PermissionsConfig;
  tools: {
    runCommand: ToolRuntimeConfig;
  };
  ui: UiConfig;
}

export const defaultAppConfig: AppConfig = {
  provider: {
    mode: "mock",
    baseUrl: "https://api.openai.com/v1",
    apiFormat: DEFAULT_PROVIDER_API_FORMAT,
    model: "gpt-5-codex",
    defaultModel: "gpt-5-codex",
    fallbackModel: "claude-sonnet",
    apiKeyEnvVarName: "LOCAL_AGENT_PROVIDER_API_KEY",
    temperature: 0.2,
    maxTokens: 4000,
    maxOutputTokens: 4000,
    maxContextTokens: 256000,
    timeout: 30,
    activeProfileId: "default",
    profiles: [
      {
        id: "default",
        name: "Default",
        mode: "mock",
        baseUrl: "https://api.openai.com/v1",
        apiFormat: DEFAULT_PROVIDER_API_FORMAT,
        model: "gpt-5-codex",
        defaultModel: "gpt-5-codex",
        fallbackModel: "claude-sonnet",
        apiKeyEnvVarName: "LOCAL_AGENT_PROVIDER_API_KEY",
        temperature: 0.2,
        maxTokens: 4000,
        maxOutputTokens: 4000,
        maxContextTokens: 256000,
        timeout: 30,
      },
    ],
  },
  workspace: {
    rootPath: "",
    ignore: [".git", "node_modules", "dist", ".venv"],
    writableRoots: [],
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
  autonomy: {
    activeProfileId: "balanced",
    profiles: [
      {
        id: "locked_down",
        name: "Locked Down",
        level: "L0",
        maxSteps: 4,
        maxParallelSubtasks: 1,
        allowBackground: false,
        allowSubagents: false,
        allowFileWrite: "blocked",
        allowShell: "blocked",
        allowNetwork: false,
        memoryRecallPolicy: "workspace_first_session_boosted",
        retryLimit: 0,
        timeoutMs: 600_000,
      },
      {
        id: "conservative",
        name: "Conservative",
        level: "L1",
        maxSteps: 10,
        maxParallelSubtasks: 2,
        allowBackground: false,
        allowSubagents: true,
        allowFileWrite: "approval_required",
        allowShell: "approval_required",
        allowNetwork: false,
        memoryRecallPolicy: "workspace_first_session_boosted",
        retryLimit: 1,
        timeoutMs: 600_000,
      },
      {
        id: "balanced",
        name: "Balanced",
        level: "L2",
        maxSteps: 20,
        maxParallelSubtasks: 4,
        allowBackground: true,
        allowSubagents: true,
        allowFileWrite: "approval_required",
        allowShell: "approval_required",
        allowNetwork: false,
        memoryRecallPolicy: "workspace_first_session_boosted",
        retryLimit: 2,
        timeoutMs: 600_000,
      },
      {
        id: "autonomous",
        name: "Autonomous",
        level: "L3",
        maxSteps: 40,
        maxParallelSubtasks: 6,
        allowBackground: true,
        allowSubagents: true,
        allowFileWrite: "approval_required",
        allowShell: "approval_required",
        allowNetwork: false,
        memoryRecallPolicy: "workspace_first_session_boosted",
        retryLimit: 2,
        timeoutMs: 600_000,
      },
    ],
  },
  agentSoul: {
    activeProfileId: "default",
    workspaceInstructions: "",
    sessionOverrideEnabled: false,
    profiles: [
      {
        id: "default",
        name: "Default",
        description: "Default local coding agent identity.",
        identity: "A capable local coding agent that works inside the user's desktop runtime.",
        principles: [
          "Be practical, careful, and transparent about uncertainty.",
          "Prefer existing project patterns over unnecessary new abstractions.",
          "Keep the user in control of risky actions.",
        ],
        communicationStyle: "Clear, concise, collaborative.",
        reasoningStyle: "Inspect the current workspace before making changes.",
        collaborationStyle: "Explain meaningful decisions and keep work scoped to the user's request.",
        domainPreferences: [],
        customSystemPrompt: "",
        enabled: true,
        scope: "global",
        createdAt: 0,
        updatedAt: 0,
      },
    ],
  },
  permissions: {
    preset: "balanced",
    capabilities: {},
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
    language: "zh-CN",
    showRawEvents: false,
    theme: "light",
    density: "comfortable",
    radius: "md",
    motion: "subtle",
    accentColor: "cyan",
    transparency: 0.78,
    fontScale: 1,
    reasoningEffort: "max",
    webFetchPreflight: true,
  },
};

export type ConfigPatch = DeepPartial<AppConfig>;
