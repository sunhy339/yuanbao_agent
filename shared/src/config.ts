import type { ApprovalMode, ProviderMode } from "./domain";

export type DeepPartial<T> = {
  [K in keyof T]?: T[K] extends (infer U)[] ? U[] : T[K] extends object ? DeepPartial<T[K]> : T[K];
};

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
  maxFilesPerPatch: number;
  allowNetwork: boolean;
}

export interface ToolRuntimeConfig {
  allowedShell: "powershell" | "bash" | "zsh";
  blockedPatterns: string[];
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
    maxFilesPerPatch: 20,
    allowNetwork: false,
  },
  tools: {
    runCommand: {
      allowedShell: "powershell",
      blockedPatterns: ["rm -rf", "shutdown", "format"],
    },
  },
  ui: {
    language: "zh-CN",
    showRawEvents: false,
  },
};

export type ConfigPatch = DeepPartial<AppConfig>;
