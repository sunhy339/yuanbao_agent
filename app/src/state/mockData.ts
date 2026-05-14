import type { AppConfig } from "@shared";
import { defaultAppConfig } from "@shared";

// Test-only mock config builder — used by App.test.tsx
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
      maxContextTokens: 256000,
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
          maxContextTokens: 256000,
          timeout: 30,
          lastCheckedAt: Date.now(),
          lastStatus: "mocked",
          lastErrorSummary: "Local preview does not contact a remote model.",
        },
      ],
    },
    autonomy: defaultAppConfig.autonomy,
    agentSoul: defaultAppConfig.agentSoul,
    permissions: defaultAppConfig.permissions,
    workspace: {
      rootPath: "D:/py/yuanbao_agent",
      ignore: [".git", "node_modules", "dist", ".venv", "target"],
      writableRoots: ["D:/py/yuanbao_agent"],
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
