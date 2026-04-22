import type { ApprovalMode, ProviderMode } from "./domain";

export type DeepPartial<T> = {
  [K in keyof T]?: T[K] extends (infer U)[] ? U[] : T[K] extends object ? DeepPartial<T[K]> : T[K];
};

export interface ProviderProfile {
  id: string;
  name: string;
  mode?: ProviderMode;
  baseUrl?: string;
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
}

export interface AppConfig {
  provider: ProviderConfig;
  workspace: WorkspaceConfig;
  search: SearchConfig;
  policy: PolicyConfig;
  tools: {
    runCommand: ToolRuntimeConfig;
  };
  ui: UiConfig;
}

export const defaultAppConfig: AppConfig = {
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
  },
};

export type ConfigPatch = DeepPartial<AppConfig>;
