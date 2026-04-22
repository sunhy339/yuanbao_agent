import type { ApprovalMode } from "./domain";

export interface ProviderConfig {
  defaultModel: string;
  fallbackModel?: string;
  temperature: number;
  maxOutputTokens: number;
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
  search?: SearchConfig;
  policy: PolicyConfig;
  tools: {
    runCommand: ToolRuntimeConfig;
  };
  ui: UiConfig;
}

export const defaultAppConfig: AppConfig = {
  provider: {
    defaultModel: "gpt-5-codex",
    fallbackModel: "claude-sonnet",
    temperature: 0.2,
    maxOutputTokens: 4000,
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
