import { useEffect, useMemo, useRef, useState } from "react";
import type {
  ApprovalRequestedPayload,
  ApprovalResolvedPayload,
  AgentEventEnvelope,
  AppConfig,
  AssistantTokenPayload,
  CommandLogRecord,
  McpServerRecord,
  PatchRecord,
  PatchProposedPayload,
  ProviderMode,
  ProviderProfile,
  ProviderTestResult,
  ScheduledTaskRecord,
  ScheduledTaskRunRecord,
  SessionRecord,
  SessionUpdatedPayload,
  SkillPresetRecord,
  TaskRecord,
  TaskContextPreviewPayload,
  TaskUpdatedPayload,
  ToolRuntimeConfig,
  ToolLifecyclePayload,
  TraceEventRecord,
  WorkspaceRef,
} from "@shared";
import { RuntimeClient, type HostStatus, type RuntimeConfig } from "./lib/runtimeClient";
import {
  appendAssistantPlaceholder,
  appendUserMessage,
  failAssistantMessage,
  getVisibleChatMessages,
  isOperationalAssistantDelta,
  replaceSessionMessages,
  stopStreamingMessages,
  updatePendingMessageTask,
  type ChatMessageView,
} from "./state/chatMessages";
import {
  DEFAULT_PROMPT,
  DEFAULT_SESSION_TITLE,
  DEFAULT_WORKSPACE_PATH,
} from "./state/mockData";
import { dispatchSlashCommand, SLASH_COMMANDS } from "./state/slashCommands";
import { AppShell } from "./ui/workbench/AppShell";
import { getSidebarActiveSessionId, resolveSessionForTab } from "./ui/workbench/sessionRouting";
import {
  closeOtherTabs,
  closeTab,
  getInitialTabs,
  openSessionTab,
  openSystemTab,
} from "./ui/workbench/tabModel";
import type { SystemWorkspaceKind, WorkbenchTab, WorkbenchSession } from "./ui/workbench/types";
import { ToastContainer, createToast, type ToastEntry } from "./ui/workbench/Toast";
import { NewSessionWorkspace } from "./ui/workbench/workspaces/NewSessionWorkspace";
import {
  ScheduledWorkspace,
  type ExecutionLog,
  type ScheduledTask,
  type ScheduledTaskDraft,
} from "./ui/workbench/workspaces/scheduled/ScheduledWorkspace";
import {
  McpWorkspace,
  type McpServerDraft,
} from "./ui/workbench/workspaces/mcp/McpWorkspace";
import { AppearanceWorkspace } from "./ui/workbench/workspaces/appearance/AppearanceWorkspace";
import { ComponentPlaygroundWorkspace } from "./ui/workbench/workspaces/playground/ComponentPlaygroundWorkspace";
import {
  SkillsWorkspace,
  type SkillDraft,
} from "./ui/workbench/workspaces/skills/SkillsWorkspace";
import { WorkbenchOverviewPage } from "./ui/v2/pages/WorkbenchOverviewPage";
import {
  SessionWorkspace,
  type SessionWorkspaceBackgroundJob,
  type SessionWorkspaceCollaboration,
  type SessionWorkspaceContextPreview,
} from "./ui/workbench/workspaces/session/SessionWorkspace";
import type { ComposerRuntimeChildTask } from "./ui/workbench/ComposerDock";
import {
  SettingsWorkspace,
  type SettingsComputerUseConfig,
  type SettingsGeneralConfig,
  type SettingsIMConfig,
  type SettingsProvider,
  type SettingsProviderFeedback,
  type SettingsProviderPayload,
  type SettingsProviderTestResult,
  type SettingsSkillConfig,
} from "./ui/workbench/workspaces/settings/SettingsWorkspace";
import { formatRuntimeModeLabel, formatStatusLabel } from "./ui/copy";

const runtimeClient = new RuntimeClient();
const DEFAULT_SEARCH_GLOB_TEXT = "";
const DEFAULT_PROVIDER_MODE: ProviderMode = "mock";
const DEFAULT_PROVIDER_BASE_URL = "https://api.openai.com/v1";
const DEFAULT_PROVIDER_API_FORMAT = "openai-chat";
const DEFAULT_PROVIDER_MODEL = "gpt-5-codex";
const DEFAULT_PROVIDER_API_KEY_ENV_VAR = "LOCAL_AGENT_PROVIDER_API_KEY";
const DEFAULT_PROVIDER_TEMPERATURE = 0.2;
const DEFAULT_PROVIDER_MAX_TOKENS = 4000;
const DEFAULT_PROVIDER_MAX_CONTEXT_TOKENS = 120000;
const DEFAULT_PROVIDER_TIMEOUT = 30;
const DEFAULT_ALLOWED_SHELL: ToolRuntimeConfig["allowedShell"] = "powershell";
const TRACE_LIMIT = 50;
const TRACE_AUTO_REFRESH_STATUSES = new Set<TaskRecord["status"]>([
  "completed",
  "failed",
  "waiting_approval",
]);
type TaskControlAction = "cancel" | "pause" | "resume";

function isTaskControllable(status?: string) {
  return Boolean(status && ["running", "planning", "verifying", "waiting_approval", "queued"].includes(status));
}

function normalizeWorkspacePathForCompare(path: string) {
  return path.trim().replace(/\\/g, "/").replace(/\/+$/, "").toLowerCase();
}

function workspaceNameFromPath(path?: string | null) {
  return path?.split(/[\\/]/).filter(Boolean).pop() || undefined;
}

interface ProviderSettingsForm {
  name: string;
  mode: ProviderMode;
  baseUrl: string;
  model: string;
  apiKeyEnvVarName: string;
  temperature: string;
  maxTokens: string;
  maxContextTokens: string;
  timeout: string;
}

interface CommandPolicyForm {
  allowedShell: ToolRuntimeConfig["allowedShell"];
  allowedCommands: string;
  deniedCommands: string;
  blockedPatterns: string;
  allowedCwdRoots: string;
}

type ProviderStatusBadge = "preview" | "configured" | "missing env" | "failed" | "ok";

interface ProviderStatusView {
  label: ProviderStatusBadge;
  badgeClass: "ok" | "warn" | "error" | "info" | "neutral";
}

interface ProviderHealthView {
  checkedAtText: string;
  statusText: string;
  summaryText: string;
  badgeClass: "ok" | "warn" | "error" | "info" | "neutral";
}

function formatTimestamp(timestamp?: number): string {
  if (!timestamp) {
    return "not recorded";
  }

  return new Date(timestamp).toLocaleString("en-US", {
    hour12: false,
  });
}

function formatDuration(durationMs?: number): string {
  if (typeof durationMs !== "number" || !Number.isFinite(durationMs)) {
    return "in progress";
  }

  if (durationMs < 1000) {
    return `${Math.max(0, Math.round(durationMs))} ms`;
  }

  return `${(durationMs / 1000).toFixed(1)} s`;
}

function formatCompactCount(value?: number | null): string {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return "--";
  }
  if (value >= 1_000_000) {
    return `${(value / 1_000_000).toFixed(1)}m`;
  }
  if (value >= 1_000) {
    return `${Math.round(value / 100) / 10}k`;
  }
  return String(Math.max(0, Math.round(value)));
}

function normalizeRuntimeConfig(config: AppConfig | RuntimeConfig): RuntimeConfig {
  return {
    ...config,
    provider: normalizeProviderConfig(config.provider),
    search: config.search ?? {
      glob: [],
      ignore: config.workspace.ignore,
    },
    tools: {
      ...config.tools,
      runCommand: normalizeRunCommandConfig(config.tools.runCommand),
    },
  };
}

function normalizeProviderConfig(provider: AppConfig["provider"]): AppConfig["provider"] {
  const profiles = normalizeProviderProfiles(provider);
  const activeProfileId = provider.activeProfileId && profiles.some((item) => item.id === provider.activeProfileId)
    ? provider.activeProfileId
    : profiles[0]?.id;
  const activeProfile = profiles.find((item) => item.id === activeProfileId) ?? profiles[0];
  const model = activeProfile?.model || provider.model || provider.defaultModel || DEFAULT_PROVIDER_MODEL;
  const maxTokens =
    activeProfile?.maxTokens ?? provider.maxTokens ?? provider.maxOutputTokens ?? DEFAULT_PROVIDER_MAX_TOKENS;
  return {
    ...provider,
    ...activeProfile,
    mode: activeProfile?.mode ?? provider.mode ?? DEFAULT_PROVIDER_MODE,
    baseUrl: activeProfile?.baseUrl ?? provider.baseUrl ?? DEFAULT_PROVIDER_BASE_URL,
    apiFormat: activeProfile?.apiFormat ?? provider.apiFormat ?? DEFAULT_PROVIDER_API_FORMAT,
    model,
    defaultModel: activeProfile?.defaultModel || provider.defaultModel || model,
    apiKeyEnvVarName:
      activeProfile?.apiKeyEnvVarName ?? provider.apiKeyEnvVarName ?? DEFAULT_PROVIDER_API_KEY_ENV_VAR,
    temperature: activeProfile?.temperature ?? provider.temperature ?? DEFAULT_PROVIDER_TEMPERATURE,
    maxTokens,
    maxOutputTokens: activeProfile?.maxOutputTokens ?? provider.maxOutputTokens ?? maxTokens,
    maxContextTokens:
      activeProfile?.maxContextTokens ?? provider.maxContextTokens ?? DEFAULT_PROVIDER_MAX_CONTEXT_TOKENS,
    timeout: activeProfile?.timeout ?? provider.timeout ?? DEFAULT_PROVIDER_TIMEOUT,
    activeProfileId,
    profiles,
  };
}

function normalizeProviderProfiles(provider: AppConfig["provider"]): ProviderProfile[] {
  const legacyProfile = providerToProfile(provider, provider.activeProfileId || "default", "Default");
  const rawProfiles = provider.profiles?.length ? provider.profiles : [legacyProfile];
  return rawProfiles.map((profile, index) =>
    normalizeProviderProfile(profile, legacyProfile, index),
  );
}

function normalizeProviderProfile(
  profile: Partial<ProviderProfile>,
  fallback: ProviderProfile,
  index: number,
): ProviderProfile {
  const merged = { ...fallback, ...profile };
  const model = merged.model || merged.defaultModel || DEFAULT_PROVIDER_MODEL;
  const maxTokens = merged.maxTokens ?? merged.maxOutputTokens ?? DEFAULT_PROVIDER_MAX_TOKENS;
  return {
    ...merged,
    id: merged.id?.trim() || `profile_${index + 1}`,
    name: merged.name?.trim() || `Profile ${index + 1}`,
    mode: merged.mode ?? DEFAULT_PROVIDER_MODE,
    baseUrl: merged.baseUrl ?? DEFAULT_PROVIDER_BASE_URL,
    apiFormat: merged.apiFormat ?? DEFAULT_PROVIDER_API_FORMAT,
    model,
    defaultModel: merged.defaultModel || model,
    apiKeyEnvVarName: merged.apiKeyEnvVarName ?? DEFAULT_PROVIDER_API_KEY_ENV_VAR,
    temperature: merged.temperature ?? DEFAULT_PROVIDER_TEMPERATURE,
    maxTokens,
    maxOutputTokens: merged.maxOutputTokens ?? maxTokens,
    maxContextTokens: merged.maxContextTokens ?? DEFAULT_PROVIDER_MAX_CONTEXT_TOKENS,
    timeout: merged.timeout ?? DEFAULT_PROVIDER_TIMEOUT,
  };
}

function providerToProfile(provider: AppConfig["provider"], id: string, name: string): ProviderProfile {
  const model = provider.model || provider.defaultModel || DEFAULT_PROVIDER_MODEL;
  const maxTokens = provider.maxTokens ?? provider.maxOutputTokens ?? DEFAULT_PROVIDER_MAX_TOKENS;
  return {
    id,
    name,
    mode: provider.mode ?? DEFAULT_PROVIDER_MODE,
    baseUrl: provider.baseUrl ?? DEFAULT_PROVIDER_BASE_URL,
    apiFormat: provider.apiFormat ?? DEFAULT_PROVIDER_API_FORMAT,
    model,
    defaultModel: provider.defaultModel || model,
    fallbackModel: provider.fallbackModel,
    apiKeyEnvVarName: provider.apiKeyEnvVarName ?? DEFAULT_PROVIDER_API_KEY_ENV_VAR,
    temperature: provider.temperature ?? DEFAULT_PROVIDER_TEMPERATURE,
    maxTokens,
    maxOutputTokens: provider.maxOutputTokens ?? maxTokens,
    maxContextTokens: provider.maxContextTokens ?? DEFAULT_PROVIDER_MAX_CONTEXT_TOKENS,
    timeout: provider.timeout ?? DEFAULT_PROVIDER_TIMEOUT,
  };
}

function buildProviderSettingsForm(config: RuntimeConfig | null): ProviderSettingsForm {
  const provider = config?.provider
    ? normalizeProviderConfig(config.provider)
    : {
        defaultModel: DEFAULT_PROVIDER_MODEL,
        temperature: DEFAULT_PROVIDER_TEMPERATURE,
        maxOutputTokens: DEFAULT_PROVIDER_MAX_TOKENS,
      };
  const normalized = normalizeProviderConfig(provider);

  return {
    name:
      normalized.profiles?.find((item) => item.id === normalized.activeProfileId)?.name ??
      "Default",
    mode: normalized.mode ?? DEFAULT_PROVIDER_MODE,
    baseUrl: normalized.baseUrl ?? DEFAULT_PROVIDER_BASE_URL,
    model: normalized.model ?? normalized.defaultModel,
    apiKeyEnvVarName: normalized.apiKeyEnvVarName ?? DEFAULT_PROVIDER_API_KEY_ENV_VAR,
    temperature: String(normalized.temperature),
    maxTokens: String(normalized.maxTokens ?? normalized.maxOutputTokens),
    maxContextTokens: String(normalized.maxContextTokens ?? DEFAULT_PROVIDER_MAX_CONTEXT_TOKENS),
    timeout: String(normalized.timeout ?? DEFAULT_PROVIDER_TIMEOUT),
  };
}

function normalizeRunCommandConfig(runCommand?: Partial<ToolRuntimeConfig>): ToolRuntimeConfig {
  const allowedCommands = runCommand?.allowedCommands ?? runCommand?.allowlist ?? [];
  const deniedCommands = runCommand?.deniedCommands ?? runCommand?.denylist ?? [];
  return {
    allowedShell: runCommand?.allowedShell ?? DEFAULT_ALLOWED_SHELL,
    allowedCommands,
    allowlist: runCommand?.allowlist ?? allowedCommands,
    deniedCommands,
    denylist: runCommand?.denylist ?? deniedCommands,
    blockedPatterns: runCommand?.blockedPatterns ?? [],
    allowedCwdRoots: runCommand?.allowedCwdRoots ?? [],
  };
}

function buildCommandPolicyForm(config: RuntimeConfig | null): CommandPolicyForm {
  const runCommand = normalizeRunCommandConfig(config?.tools.runCommand);
  return {
    allowedShell: runCommand.allowedShell,
    allowedCommands: serializePatternList(runCommand.allowedCommands ?? []),
    deniedCommands: serializePatternList(runCommand.deniedCommands ?? []),
    blockedPatterns: serializePatternList(runCommand.blockedPatterns),
    allowedCwdRoots: serializePatternList(runCommand.allowedCwdRoots ?? []),
  };
}

function parseProviderNumber(
  value: string,
  label: string,
  options: { integer?: boolean; min?: number; max?: number } = {},
): number {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) {
    throw new Error(`${label} must be a number.`);
  }
  if (options.integer && !Number.isInteger(parsed)) {
    throw new Error(`${label} must be an integer.`);
  }
  if (typeof options.min === "number" && parsed < options.min) {
    throw new Error(`${label} must be at least ${options.min}.`);
  }
  if (typeof options.max === "number" && parsed > options.max) {
    throw new Error(`${label} must be at most ${options.max}.`);
  }
  return parsed;
}

function parsePatternText(value: string): string[] {
  return value
    .split(/[\n,]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function serializePatternList(values: string[]): string {
  return values.join("\n");
}

function getProviderStatusView(
  settings: ProviderSettingsForm,
  result: ProviderTestResult | null,
): ProviderStatusView {
  if (!result) {
    return settings.mode === "mock"
      ? { label: "preview", badgeClass: "info" }
      : { label: "configured", badgeClass: "neutral" };
  }

  if (result.status === "ok") {
    return { label: "ok", badgeClass: "ok" };
  }
  if (result.status === "mocked") {
    return { label: "preview", badgeClass: "info" };
  }
  if (result.status === "missing_env" || result.status === "not_configured") {
    return { label: "missing env", badgeClass: "warn" };
  }

  return { label: "failed", badgeClass: "error" };
}

function getProviderRuntimeNotice(
  settings: ProviderSettingsForm,
  result: ProviderTestResult | null,
): string | null {
  const envVarName =
    result?.checkedEnvVarName ?? (settings.apiKeyEnvVarName || DEFAULT_PROVIDER_API_KEY_ENV_VAR);

  if (settings.mode === "mock" || result?.status === "mocked") {
    return "当前模型供应商处于本地预览模式。现在发送的任务会使用确定性的本地行为，不会调用远程模型。";
  }
  if (result?.status === "missing_env" || result?.status === "not_configured") {
    return `当前模型供应商缺少 ${envVarName}，无法连接真实模型。请设置环境变量并重新测试后再发送真实模型任务。`;
  }

  return null;
}

function getProviderErrorSummary(result: ProviderTestResult): string {
  const detail =
    result.lastErrorSummary ?? result.details?.errorSummary ?? result.details?.error ?? result.details?.summary;
  return typeof detail === "string" && detail.trim() ? detail.trim() : result.message;
}

function getProviderHealthBadge(status?: string): ProviderHealthView["badgeClass"] {
  if (status === "ok") {
    return "ok";
  }
  if (status === "mocked") {
    return "info";
  }
  if (status === "missing_env" || status === "not_configured") {
    return "warn";
  }
  if (status === "failed" || status === "unsupported") {
    return "error";
  }
  return "neutral";
}

function getProviderHealthView(
  profile: ProviderProfile | undefined,
  result: ProviderTestResult | null,
): ProviderHealthView {
  const status = result?.lastStatus ?? profile?.lastStatus;
  const summary =
    (result ? getProviderErrorSummary(result) : undefined) ??
    profile?.lastErrorSummary ??
    "该配置还没有记录过健康检查。";

  return {
    checkedAtText: formatTimestamp(result?.lastCheckedAt ?? profile?.lastCheckedAt),
    statusText: status ?? "not_recorded",
    summaryText: summary,
    badgeClass: getProviderHealthBadge(status),
  };
}

function readProviderTestDetail(
  result: ProviderTestResult | null | undefined,
  ...keys: string[]
): string | undefined {
  if (!result?.details) {
    return undefined;
  }

  for (const key of keys) {
    const value = result.details[key];
    if (typeof value === "string" && value.trim()) {
      return value.trim();
    }
  }

  return undefined;
}

function buildSettingsProviderLastTest(
  profile: ProviderProfile,
  result: ProviderTestResult | null,
  activeProfileId?: string,
): SettingsProviderTestResult | undefined {
  const matchesResult =
    result &&
    (result.profileId === profile.id ||
      (!result.profileId && activeProfileId === profile.id));

  if (matchesResult) {
    return {
      ok: result.ok,
      status: result.lastStatus ?? result.status,
      message: result.message,
      model: result.model ?? profile.model,
      finishReason: readProviderTestDetail(result, "finishReason", "finish_reason", "stopReason", "stop_reason"),
      checkedAt: result.lastCheckedAt,
      errorSummary: result.lastErrorSummary ?? getProviderErrorSummary(result),
      checkedEnvVarName: result.checkedEnvVarName ?? result.envVarName,
      details: result.details,
    };
  }

  if (!profile.lastStatus) {
    return undefined;
  }

  return {
    ok: profile.lastStatus === "ok",
    status: profile.lastStatus,
    message: profile.lastErrorSummary ?? profile.lastStatus,
    model: profile.model,
    checkedAt: profile.lastCheckedAt,
    errorSummary: profile.lastErrorSummary,
  };
}

function buildDefaultProviderProfile(): ProviderProfile {
  return {
    id: "default",
    name: "Default",
    mode: DEFAULT_PROVIDER_MODE,
    baseUrl: DEFAULT_PROVIDER_BASE_URL,
    apiFormat: DEFAULT_PROVIDER_API_FORMAT,
    model: DEFAULT_PROVIDER_MODEL,
    defaultModel: DEFAULT_PROVIDER_MODEL,
    apiKeyEnvVarName: DEFAULT_PROVIDER_API_KEY_ENV_VAR,
    temperature: DEFAULT_PROVIDER_TEMPERATURE,
    maxTokens: DEFAULT_PROVIDER_MAX_TOKENS,
    maxOutputTokens: DEFAULT_PROVIDER_MAX_TOKENS,
    maxContextTokens: DEFAULT_PROVIDER_MAX_CONTEXT_TOKENS,
    timeout: DEFAULT_PROVIDER_TIMEOUT,
  };
}

function normalizeSkillForSettings(skill: SkillPresetRecord): SettingsSkillConfig {
  const toolWhitelist = skill.toolWhitelist ?? skill.tool_whitelist ?? [];
  const systemPrompt = skill.systemPrompt ?? skill.system_prompt;
  const isBuiltin = Boolean(skill.isBuiltin ?? skill.is_builtin);
  return {
    id: skill.id,
    name: skill.name,
    description:
      skill.description ||
      (toolWhitelist.length ? `工具：${toolWhitelist.join(", ")}` : "运行时技能预设"),
    path: skill.category ? `category:${skill.category}` : undefined,
    systemPrompt,
    toolWhitelist,
    isBuiltin,
    enabled: true,
    updateAvailable: false,
  };
}

function stripWrappingShellQuotes(value: string): string {
  const token = value.trim();
  if (token.length >= 2 && token[0] === token[token.length - 1] && (token[0] === '"' || token[0] === "'")) {
    return token.slice(1, -1);
  }
  return token;
}

function splitShellLikeArgs(value: string): string[] {
  const args: string[] = [];
  let current = "";
  let quote: '"' | "'" | null = null;
  for (let index = 0; index < value.length; index += 1) {
    const char = value[index];
    if (quote) {
      if (char === quote) {
        quote = null;
      } else {
        current += char;
      }
      continue;
    }
    if (char === '"' || char === "'") {
      quote = char;
      continue;
    }
    if (/\s/.test(char)) {
      if (current) {
        args.push(current);
        current = "";
      }
      continue;
    }
    current += char;
  }
  if (current) {
    args.push(current);
  }
  return args;
}

function parseMcpArgs(value: string): string[] {
  const trimmed = value.trim();
  if (!trimmed) {
    return [];
  }
  if (trimmed.startsWith("[")) {
    try {
      const parsed = JSON.parse(trimmed) as unknown;
      if (Array.isArray(parsed)) {
        return parsed.map((item) => stripWrappingShellQuotes(String(item))).filter(Boolean);
      }
    } catch {
      // Fall through to line/comma parsing.
    }
  }
  const rawParts = /\r?\n|,/.test(trimmed) ? trimmed.split(/\r?\n|,/) : splitShellLikeArgs(trimmed);
  return rawParts.map(stripWrappingShellQuotes).filter(Boolean);
}

function parseMcpKeyValuePairs(value: string): Record<string, string> {
  const trimmed = value.trim();
  if (!trimmed) {
    return {};
  }
  if (trimmed.startsWith("{")) {
    try {
      const parsed = JSON.parse(trimmed) as unknown;
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
        return Object.fromEntries(
          Object.entries(parsed as Record<string, unknown>)
            .filter(([key]) => key.trim())
            .map(([key, item]) => [key.trim(), String(item)]),
        );
      }
    } catch {
      // Fall through to KEY=value parsing.
    }
  }
  return Object.fromEntries(
    trimmed
      .split(/\r?\n/)
      .map((line) => line.trim())
      .filter(Boolean)
      .map((line) => {
        const separator = line.indexOf("=");
        if (separator === -1) {
          return [line, ""] as const;
        }
        return [line.slice(0, separator).trim(), line.slice(separator + 1).trim()] as const;
      })
      .filter(([key]) => Boolean(key)),
  );
}

function buildMcpServerPayload(draft: McpServerDraft) {
  return {
    name: draft.name.trim(),
    transport: draft.transport,
    command: draft.transport === "stdio" ? stripWrappingShellQuotes(draft.command) : undefined,
    args: draft.transport === "stdio" ? parseMcpArgs(draft.args) : [],
    url: draft.transport === "stdio" ? undefined : draft.url.trim(),
    headers: draft.transport === "stdio" ? {} : parseMcpKeyValuePairs(draft.headers),
    env: parseMcpKeyValuePairs(draft.env),
    enabled: draft.enabled,
  };
}

function parseSkillToolWhitelist(value: string): string[] {
  return value
    .split(/\r?\n|,/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function buildSkillPayload(draft: SkillDraft) {
  return {
    name: draft.name.trim(),
    description: draft.description.trim(),
    system_prompt: draft.systemPrompt.trim(),
    tool_whitelist: parseSkillToolWhitelist(draft.toolWhitelist),
    category: draft.category.trim() || "custom",
  };
}

function getModelFromProviderPayload(payload: SettingsProviderPayload): string {
  const jsonConfig = readProviderJsonConfig(payload);
  if (jsonConfig.model) {
    return jsonConfig.model;
  }
  if (payload.mainModel.trim()) {
    return payload.mainModel.trim();
  }

  const firstMappingLine = payload.modelMapping
    .split(/\r?\n/)
    .map((line) => line.trim())
    .find(Boolean);

  if (!firstMappingLine) {
    return DEFAULT_PROVIDER_MODEL;
  }

  const [, mappedValue] = firstMappingLine.split(/[=:]/, 2);
  return (mappedValue ?? firstMappingLine).trim() || DEFAULT_PROVIDER_MODEL;
}

function getProviderEnvVarName(payload: SettingsProviderPayload): string {
  if (payload.apiKeyEnvVarName?.trim()) {
    return payload.apiKeyEnvVarName.trim();
  }
  const raw = payload.apiKey.trim();
  if (raw && !raw.startsWith("sk-")) {
    return raw;
  }
  const jsonConfig = readProviderJsonConfig(payload);
  if (jsonConfig.apiKeyEnvVarName) {
    return jsonConfig.apiKeyEnvVarName;
  }

  const normalizedName = (payload.name || "provider")
    .replace(/[^a-z0-9]+/gi, "_")
    .replace(/^_+|_+$/g, "")
    .toUpperCase();
  return `${normalizedName || "PROVIDER"}_API_KEY`;
}

interface ProviderJsonConfig {
  endpoint?: string;
  apiFormat?: string;
  model?: string;
  apiKeyEnvVarName?: string;
  maxTokens?: number;
  timeout?: number;
}

const providerApiKeyEnvKeys = [
  "ANTHROPIC_AUTH_TOKEN",
  "ANTHROPIC_API_KEY",
  "OPENAI_API_KEY",
  "LOCAL_AGENT_PROVIDER_API_KEY",
  "LOCAL_AGENT_OPENAI_API_KEY",
  "DEEPSEEK_API_KEY",
  "MOONSHOT_API_KEY",
  "MINIMAX_API_KEY",
  "ZHIPU_API_KEY",
];

function readProviderJsonConfig(payload: SettingsProviderPayload): ProviderJsonConfig {
  if (!payload.jsonConfig.trim()) {
    return payload.apiKeyEnvVarName ? { apiKeyEnvVarName: payload.apiKeyEnvVarName } : {};
  }

  let parsed: unknown;
  try {
    parsed = JSON.parse(payload.jsonConfig);
  } catch {
    const env = readEnvConfigText(payload.jsonConfig);
    return {
      endpoint: readEnvString(env, "LOCAL_AGENT_PROVIDER_BASE_URL", "OPENAI_BASE_URL", "ANTHROPIC_BASE_URL"),
      apiFormat: readEnvString(env, "LOCAL_AGENT_PROVIDER_API_FORMAT", "API_FORMAT"),
      model: readEnvString(
        env,
        "LOCAL_AGENT_PROVIDER_MODEL",
        "OPENAI_MODEL",
        "ANTHROPIC_MODEL",
        "ANTHROPIC_DEFAULT_SONNET_MODEL",
        "ANTHROPIC_DEFAULT_OPUS_MODEL",
        "ANTHROPIC_DEFAULT_HAIKU_MODEL",
      ),
      apiKeyEnvVarName: payload.apiKeyEnvVarName || providerApiKeyEnvKeys.find((key) => key in env),
      maxTokens: readEnvNumber(
        env,
        "LOCAL_AGENT_PROVIDER_MAX_TOKENS",
        "OPENAI_MAX_TOKENS",
        "ANTHROPIC_MAX_TOKENS",
      ),
      timeout: readEnvNumber(env, "LOCAL_AGENT_PROVIDER_TIMEOUT", "OPENAI_TIMEOUT", "ANTHROPIC_TIMEOUT"),
    };
  }
  if (!parsed || typeof parsed !== "object") {
    return {};
  }

  const record = parsed as Record<string, unknown>;
  const env = record.env && typeof record.env === "object"
    ? (record.env as Record<string, unknown>)
    : {};
  const readString = (...keys: string[]) => {
    for (const key of keys) {
      const value = env[key] ?? record[key];
      if (typeof value === "string" && value.trim()) {
        return value.trim();
      }
    }
    return undefined;
  };
  const readNumber = (...keys: string[]) => {
    const value = readString(...keys);
    if (!value) {
      return undefined;
    }
    const parsedValue = Number(value);
    return Number.isFinite(parsedValue) && parsedValue > 0 ? parsedValue : undefined;
  };
  const explicitApiKeyEnvVarName = readString("apiKeyEnvVarName", "api_key_env_var", "apiKeyEnv");
  const apiFormat = readString("apiFormat", "api_format", "LOCAL_AGENT_PROVIDER_API_FORMAT", "API_FORMAT");
  const apiKeyEnvVarName =
    payload.apiKeyEnvVarName ||
    explicitApiKeyEnvVarName ||
    providerApiKeyEnvKeys.find((key) => typeof env[key] === "string" && String(env[key]).trim());

  return {
    endpoint: readString("LOCAL_AGENT_PROVIDER_BASE_URL", "OPENAI_BASE_URL", "ANTHROPIC_BASE_URL"),
    apiFormat,
    model: readString(
      "LOCAL_AGENT_PROVIDER_MODEL",
      "OPENAI_MODEL",
      "ANTHROPIC_MODEL",
      "ANTHROPIC_DEFAULT_SONNET_MODEL",
      "ANTHROPIC_DEFAULT_OPUS_MODEL",
      "ANTHROPIC_DEFAULT_HAIKU_MODEL",
    ),
    apiKeyEnvVarName,
    maxTokens: readNumber("LOCAL_AGENT_PROVIDER_MAX_TOKENS", "OPENAI_MAX_TOKENS", "ANTHROPIC_MAX_TOKENS"),
    timeout: readNumber("LOCAL_AGENT_PROVIDER_TIMEOUT", "OPENAI_TIMEOUT", "ANTHROPIC_TIMEOUT"),
  };
}

function readEnvConfigText(value: string): Record<string, string> {
  return Object.fromEntries(
    value
      .split(/\r?\n/)
      .map((line) => line.trim())
      .filter((line) => line && !line.startsWith("#") && line.includes("="))
      .map((line) => {
        const separatorIndex = line.indexOf("=");
        return [
          line.slice(0, separatorIndex).trim(),
          line.slice(separatorIndex + 1).trim().replace(/^["']|["']$/g, ""),
        ];
      })
      .filter(([key]) => key),
  );
}

function readEnvString(env: Record<string, string>, ...keys: string[]) {
  for (const key of keys) {
    const value = env[key];
    if (value?.trim()) {
      return value.trim();
    }
  }
  return undefined;
}

function readEnvNumber(env: Record<string, string>, ...keys: string[]) {
  const value = readEnvString(env, ...keys);
  if (!value) {
    return undefined;
  }
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : undefined;
}

function buildProviderProfileFromPayload(
  payload: SettingsProviderPayload,
  profileId: string,
  config: RuntimeConfig,
  existingProfile?: ProviderProfile,
): ProviderProfile {
  const jsonConfig = readProviderJsonConfig(payload);
  const model = getModelFromProviderPayload(payload);
  const apiKeyInput = payload.apiKey.trim();
  const directApiKey = apiKeyInput.startsWith("sk-") ? apiKeyInput : undefined;
  const hasApiKeyInput = apiKeyInput.length > 0;
  const apiKeyEnvVarName = hasApiKeyInput || jsonConfig.apiKeyEnvVarName
    ? getProviderEnvVarName(payload)
    : existingProfile?.apiKeyEnvVarName ?? getProviderEnvVarName(payload);
  return {
    ...existingProfile,
    id: profileId,
    name: payload.name.trim() || existingProfile?.name || "供应商配置",
    mode: "openai-compatible",
    baseUrl: jsonConfig.endpoint || payload.endpoint.trim() || existingProfile?.baseUrl || DEFAULT_PROVIDER_BASE_URL,
    apiFormat: jsonConfig.apiFormat || payload.apiFormat || existingProfile?.apiFormat || DEFAULT_PROVIDER_API_FORMAT,
    model,
    defaultModel: model,
    fallbackModel: payload.opusModel.trim() || existingProfile?.fallbackModel || config.provider.fallbackModel,
    apiKey: directApiKey ?? (hasApiKeyInput ? undefined : existingProfile?.apiKey),
    apiKeyEnvVarName,
    temperature: existingProfile?.temperature ?? DEFAULT_PROVIDER_TEMPERATURE,
    maxTokens: jsonConfig.maxTokens ?? existingProfile?.maxTokens ?? DEFAULT_PROVIDER_MAX_TOKENS,
    maxOutputTokens: jsonConfig.maxTokens ?? existingProfile?.maxOutputTokens ?? DEFAULT_PROVIDER_MAX_TOKENS,
    maxContextTokens: existingProfile?.maxContextTokens ?? DEFAULT_PROVIDER_MAX_CONTEXT_TOKENS,
    timeout: jsonConfig.timeout ?? existingProfile?.timeout ?? DEFAULT_PROVIDER_TIMEOUT,
    lastCheckedAt: existingProfile?.lastCheckedAt,
    lastStatus: existingProfile?.lastStatus,
    lastErrorSummary: existingProfile?.lastErrorSummary,
  };
}

function buildSettingsGeneralConfig(config: RuntimeConfig): SettingsGeneralConfig {
  const ui = config.ui;
  const language = ui.language.toLowerCase().startsWith("zh")
    ? "zh"
    : ui.language.toLowerCase().startsWith("en")
      ? "en"
      : "auto";
  return {
    theme: ui.theme ?? "light",
    density: ui.density ?? "comfortable",
    radius: ui.radius ?? "md",
    motion: ui.motion ?? "subtle",
    accentColor: ui.accentColor ?? "cyan",
    transparency: clampAppearanceNumber(ui.transparency, 0.58, 0.96, 0.78),
    fontScale: clampAppearanceNumber(ui.fontScale, 0.92, 1.12, 1),
    language,
    reasoningEffort: ui.reasoningEffort ?? "max",
    webFetchPreflight: ui.webFetchPreflight ?? true,
  };
}

function buildComputerUseStatus(): string {
  const clipboardAvailable =
    typeof navigator !== "undefined" &&
    typeof navigator.clipboard?.writeText === "function";
  const desktopBridgeAvailable = runtimeClient.canOpenLocalAppPaths();
  const ready = [
    clipboardAvailable ? "剪贴板" : null,
    desktopBridgeAvailable ? "桌面 shell 桥接" : null,
    "敏感动作确认",
  ].filter(Boolean);
  const pending = [
    "屏幕观察",
    "浏览器自动化",
    "系统快捷键",
  ];

  return `${new Date().toLocaleTimeString("zh-CN", { hour12: false })} 已检查：${ready.join("、")} 可用；${pending.join("、")} 的权限探测尚未接入。`;
}

function clampAppearanceNumber(
  value: number | undefined,
  min: number,
  max: number,
  fallback: number,
): number {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return fallback;
  }
  return Math.min(max, Math.max(min, value));
}

function settingsLanguageToConfig(language: SettingsGeneralConfig["language"]): string {
  if (language === "zh") {
    return "zh-CN";
  }
  if (language === "en") {
    return "en-US";
  }
  return "auto";
}

function settingsModeToApprovalMode(mode: string): AppConfig["policy"]["approvalMode"] {
  if (mode === "skip") {
    return "relaxed";
  }
  if (mode === "edits") {
    return "on_write_or_command";
  }
  return "strict";
}

function approvalModeToSettingsMode(mode?: AppConfig["policy"]["approvalMode"]): string {
  if (mode === "relaxed") {
    return "skip";
  }
  if (mode === "on_write_or_command") {
    return "edits";
  }
  return "ask";
}

function taskStatusToScheduledStatus(status: TaskRecord["status"]): ScheduledTask["status"] {
  if (status === "completed") {
    return "completed";
  }
  if (status === "failed" || status === "cancelled") {
    return "failed";
  }
  return "active";
}

function scheduledRecordToWorkspaceTask(record: ScheduledTaskRecord): ScheduledTask {
  return {
    id: record.id,
    title: record.name,
    description: record.prompt,
    status: record.enabled ? record.status : "disabled",
    scheduleText: record.schedule || "未设置计划",
    lastRunText: record.lastRunAt ? `上次运行：${formatTimestamp(record.lastRunAt)}` : "尚未运行",
  };
}

function scheduledRunToExecutionLog(run: ScheduledTaskRunRecord): ExecutionLog {
  return {
    id: run.id,
    taskId: run.taskId,
    time: formatTimestamp(run.startedAt),
    result: run.status === "completed" ? "completed" : "failed",
    message: run.summary ?? run.error ?? `运行状态：${run.status}`,
  };
}

function readRequestText(request: Record<string, unknown>, key: string, fallback: string): string {
  const value = request[key];
  if (typeof value === "string" && value.trim()) {
    return value.trim();
  }
  if (typeof value === "number" && Number.isFinite(value)) {
    return String(value);
  }
  return fallback;
}

function readRequestNumber(request: Record<string, unknown>, key: string, fallback: number): number {
  const value = request[key];
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }
  if (typeof value === "string" && value.trim()) {
    const parsed = Number(value);
    if (Number.isFinite(parsed)) {
      return parsed;
    }
  }
  return fallback;
}

function readRequestPatchId(request: Record<string, unknown>): string | undefined {
  const value = request.patch_id ?? request.patchId;
  if (typeof value === "string" && value.trim()) {
    return value.trim();
  }
  return undefined;
}

function readEventText(payload: unknown, key: string): string | undefined {
  if (!payload || typeof payload !== "object") {
    return undefined;
  }

  const value = (payload as Record<string, unknown>)[key];
  return typeof value === "string" && value.trim() ? value.trim() : undefined;
}

function readEventNumber(payload: unknown, key: string): number | undefined {
  if (!payload || typeof payload !== "object") {
    return undefined;
  }

  const value = (payload as Record<string, unknown>)[key];
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }
  if (typeof value === "string" && value.trim()) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : undefined;
  }
  return undefined;
}

function summarizeValue(value: unknown, fallback = "not recorded", maxLength = 180): string {
  if (value === undefined || value === null) {
    return fallback;
  }

  const raw =
    typeof value === "string"
      ? value
      : typeof value === "number" || typeof value === "boolean"
        ? String(value)
        : (() => {
            try {
              return JSON.stringify(value);
            } catch {
              return fallback;
            }
          })();
  const compact = raw.replace(/\s+/g, " ").trim();
  if (!compact) {
    return fallback;
  }
  return compact.length > maxLength ? `${compact.slice(0, maxLength - 1)}...` : compact;
}

function riskToLevel(value: string): "low" | "medium" | "high" {
  const normalized = value.toLowerCase();
  if (normalized.includes("delete") || normalized.includes("danger") || normalized.includes("network")) {
    return "high";
  }
  if (normalized.includes("write") || normalized.includes("command") || normalized.includes("patch")) {
    return "medium";
  }
  return "low";
}

function countAddedLines(diffText: string): number {
  return diffText
    .split(/\r?\n/)
    .filter((line) => line.startsWith("+") && !line.startsWith("+++")).length;
}

function countDeletedLines(diffText: string): number {
  return diffText
    .split(/\r?\n/)
    .filter((line) => line.startsWith("-") && !line.startsWith("---")).length;
}

function parsePatchFiles(diffText = "") {
  if (!diffText.trim()) {
    return [];
  }

  const sections = diffText.split(/^diff --git /m).filter(Boolean);
  return sections.map((section) => {
    const header = section.split(/\r?\n/, 1)[0] ?? "";
    const match = header.match(/^a\/(.+?) b\/(.+)$/);
    const path = match?.[2] ?? header.trim() ?? "unknown file";
    return {
      path,
      status: "modified",
      additions: countAddedLines(section),
      deletions: countDeletedLines(section),
      diff: `diff --git ${section}`.trim(),
    };
  });
}

function getPayloadValue(payload: unknown, keys: string[]): unknown {
  if (!payload || typeof payload !== "object") {
    return undefined;
  }

  const record = payload as Record<string, unknown>;
  for (const key of keys) {
    if (record[key] !== undefined) {
      return record[key];
    }
  }
  return undefined;
}

function truncateText(value: string, maxLength: number): string {
  const text = value.trim();
  return text.length > maxLength ? `${text.slice(0, maxLength - 1)}...` : text;
}

function formatRawValue(value: unknown, maxLength = 1800): string | undefined {
  if (value === undefined || value === null) {
    return undefined;
  }

  try {
    const raw = typeof value === "string" ? value : JSON.stringify(value, null, 2);
    return raw && raw.trim() ? truncateText(raw, maxLength) : undefined;
  } catch {
    return summarizeValue(value, "", maxLength) || undefined;
  }
}

function parseToolValue(value: unknown): unknown {
  if (typeof value !== "string") {
    return value;
  }

  const trimmed = value.trim();
  if (!trimmed || (!trimmed.startsWith("{") && !trimmed.startsWith("["))) {
    return value;
  }

  try {
    return JSON.parse(trimmed);
  } catch {
    return value;
  }
}

function toolString(record: Record<string, unknown> | null, keys: string[]): string | undefined {
  if (!record) {
    return undefined;
  }

  for (const key of keys) {
    const value = record[key];
    if (typeof value === "string" && value.trim()) {
      return value.trim();
    }
    if (typeof value === "number" && Number.isFinite(value)) {
      return String(value);
    }
  }
  return undefined;
}

function toolNumber(record: Record<string, unknown> | null, keys: string[]): number | undefined {
  if (!record) {
    return undefined;
  }

  for (const key of keys) {
    const value = record[key];
    if (typeof value === "number" && Number.isFinite(value)) {
      return value;
    }
    if (typeof value === "string" && value.trim()) {
      const parsed = Number(value);
      if (Number.isFinite(parsed)) {
        return parsed;
      }
    }
  }
  return undefined;
}

function compactToolList(values: string[], limit = 5): string {
  const visible = values.filter(Boolean).slice(0, limit);
  const suffix = values.length > limit ? `，另有 ${values.length - limit} 项` : "";
  return visible.length ? `${visible.join(", ")}${suffix}` : "";
}

function firstUsefulLine(value?: string): string | undefined {
  return value
    ?.split(/\r?\n/)
    .map((line) => line.trim())
    .find(Boolean);
}

function summarizeToolArguments(toolName: string, value: unknown, fallback = "未记录参数"): string {
  const parsed = parseToolValue(value);
  const record = asRecord(parsed);
  const path = toolString(record, ["path", "file", "cwd", "root"]);

  if (toolName === "list_dir") {
    return `列出 ${path ?? "."}`;
  }
  if (toolName === "read_file") {
    return `读取 ${path ?? "文件"}`;
  }
  if (toolName === "search_files") {
    const query = toolString(record, ["query", "pattern", "glob"]);
    return query ? `搜索 ${query}${path ? ` @ ${path}` : ""}` : `搜索${path ? ` ${path}` : ""}`;
  }
  if (toolName === "apply_patch") {
    const filesValue = record?.files;
    const changedPathsValue = record?.changedPaths ?? record?.paths;
    const files = Array.isArray(filesValue)
      ? filesValue.map((item) => toolString(asRecord(item), ["path"])).filter((item): item is string => Boolean(item))
      : Array.isArray(changedPathsValue)
        ? changedPathsValue.filter((item): item is string => typeof item === "string" && Boolean(item.trim()))
        : [];
    return files.length ? `修改 ${compactToolList(files)}` : "应用补丁";
  }
  if (toolName === "run_command") {
    const command = toolString(record, ["command", "cmd"]);
    return command ? `运行 ${command}` : "运行命令";
  }

  return summarizeValue(value, fallback, 120);
}

function summarizeToolResult(toolName: string, resultValue: unknown, errorValue: unknown, fallback = "等待结果") {
  if (errorValue !== undefined && errorValue !== null && summarizeValue(errorValue, "", 160)) {
    return `失败：${summarizeValue(errorValue, "", 160)}`;
  }

  const parsed = parseToolValue(resultValue);
  const record = asRecord(parsed);

  if (toolName === "list_dir") {
    const itemsValue = record?.items ?? parsed;
    const items = Array.isArray(itemsValue) ? itemsValue : [];
    if (!items.length) {
      return resultValue === undefined ? fallback : "没有找到条目";
    }
    const names = items
      .map((item) => {
        const itemRecord = asRecord(item);
        const name = toolString(itemRecord, ["name", "path"]);
        const type = toolString(itemRecord, ["type"]);
        return name ? `${name}${type === "directory" ? "/" : ""}` : undefined;
      })
      .filter((item): item is string => Boolean(item));
    const dirCount = items.filter((item) => toolString(asRecord(item), ["type"]) === "directory").length;
    const fileCount = items.filter((item) => toolString(asRecord(item), ["type"]) === "file").length;
    return `找到 ${items.length} 项（${dirCount} 个目录，${fileCount} 个文件）：${compactToolList(names)}`;
  }

  if (toolName === "read_file") {
    const bytes = toolNumber(record, ["bytesRead", "bytes", "size"]);
    const content = toolString(record, ["content", "text"]);
    const preview = firstUsefulLine(content);
    return `读取完成${bytes !== undefined ? `，${bytes} 字节` : ""}${preview ? `：${truncateText(preview, 80)}` : ""}`;
  }

  if (toolName === "apply_patch") {
    const pathsValue = record?.changedPaths ?? record?.paths;
    const paths = Array.isArray(pathsValue)
      ? pathsValue.filter((item): item is string => typeof item === "string" && Boolean(item.trim()))
      : [];
    const error = toolString(record, ["error"]);
    if (error) {
      return `补丁失败：${truncateText(error, 140)}`;
    }
    return paths.length ? `补丁完成：${compactToolList(paths)}` : summarizeValue(resultValue, "补丁完成", 140);
  }

  if (toolName === "run_command") {
    const exitCode = toolNumber(record, ["exitCode", "code"]);
    const output = firstUsefulLine(toolString(record, ["stdout", "stderr", "output"]));
    return `命令${exitCode === undefined ? "完成" : `退出码 ${exitCode}`}${output ? `：${truncateText(output, 100)}` : ""}`;
  }

  return summarizeValue(resultValue, fallback, 160);
}

function sortByUpdatedAtDesc<T extends { updatedAt: number }>(items: T[]): T[] {
  return [...items].sort((left, right) => right.updatedAt - left.updatedAt);
}

interface ApprovalCardView {
  approvalId: string;
  taskId: string;
  kind: string;
  patchId?: string;
  patchSummary?: string;
  filesChanged?: number;
  command: string;
  cwd: string;
  shell: string;
  timeoutMs: number;
  risk: string;
  requestJson: string;
  requestSummary: string;
  status: "pending" | "approved" | "rejected";
  requestedAt: number;
  updatedAt: number;
  resolvedAt?: number;
  requestedEventId?: string;
  resolvedEventId?: string;
}

interface PatchCardView {
  patchId: string;
  taskId: string;
  summary: string;
  filesChanged: number;
  status: PatchRecord["status"];
  requestedAt: number;
  updatedAt: number;
  diffText?: string;
  approvalId?: string;
  approvalStatus?: ApprovalCardView["status"];
  approvalResolvedAt?: number;
}

interface ToolTimelineItem {
  id: string;
  taskId: string;
  toolCallId: string;
  toolName: string;
  status: "started" | "completed" | "failed";
  argsSummary: string;
  resultSummary: string;
  errorSummary?: string;
  argsRaw?: string;
  resultRaw?: string;
  startedAt: number;
  updatedAt: number;
  finishedAt?: number;
  durationMs?: number;
  eventCount: number;
}

interface QueuedPromptSubmission {
  id: string;
  content: string;
  attachments: string[];
}

interface CommandJobTimelineItem {
  id: string;
  taskId: string;
  commandId: string;
  command: string;
  status: string;
  cwd?: string;
  shell?: string;
  summary?: string;
  startedAt: number;
  updatedAt: number;
  finishedAt?: number;
  durationMs?: number;
  exitCode?: number | null;
  stdout: string;
  stderr: string;
  stdoutPath?: string;
  stderrPath?: string;
  isBackground?: boolean;
  eventCount: number;
}

function stringifyRequestJson(request: Record<string, unknown>): string {
  try {
    return JSON.stringify(request, null, 2);
  } catch {
    return "{}";
  }
}

function readRequestStringList(request: Record<string, unknown>, keys: string[]): string[] {
  for (const key of keys) {
    const value = request[key];
    if (Array.isArray(value)) {
      return value
        .map((item) => (typeof item === "string" ? item.trim() : ""))
        .filter(Boolean);
    }
  }
  return [];
}

function readRequestOptionalNumber(request: Record<string, unknown>, keys: string[]): number | undefined {
  for (const key of keys) {
    const value = request[key];
    if (typeof value === "number" && Number.isFinite(value)) {
      return value;
    }
    if (typeof value === "string" && value.trim()) {
      const parsed = Number(value);
      if (Number.isFinite(parsed)) {
        return parsed;
      }
    }
  }
  return undefined;
}

function getApprovalBadgeClass(status: ApprovalCardView["status"]): "ok" | "warn" | "error" {
  if (status === "approved") {
    return "ok";
  }
  if (status === "rejected") {
    return "error";
  }
  return "warn";
}

function upsertRecord<T extends { id: string; updatedAt: number }>(items: T[], record: T): T[] {
  const next = new Map(items.map((item) => [item.id, item]));
  next.set(record.id, record);
  return sortByUpdatedAtDesc(Array.from(next.values()));
}

function coerceTaskStatus(
  event: AgentEventEnvelope,
  fallback: TaskRecord["status"],
): TaskRecord["status"] {
  if (event.type === "task.started") {
    return "running";
  }
  if (event.type === "task.waiting_approval") {
    return "waiting_approval";
  }
  if (event.type === "task.completed") {
    return "completed";
  }
  if (event.type === "task.failed") {
    return "failed";
  }
  if (event.type === "task.cancelled") {
    return "cancelled";
  }
  return fallback;
}

function applyEventToTask(current: TaskRecord | null, event: AgentEventEnvelope): TaskRecord | null {
  if (!current || current.id !== event.taskId || !event.type.startsWith("task.")) {
    return current;
  }

  const payload = (event.payload ?? {}) as Partial<TaskUpdatedPayload> & {
    resultSummary?: string;
    errorCode?: string;
    detail?: string;
  };

  return {
    ...current,
    status: payload.status ?? coerceTaskStatus(event, current.status),
    plan: payload.plan ?? current.plan,
    acceptanceCriteria: payload.acceptanceCriteria ?? current.acceptanceCriteria,
    outOfScope: payload.outOfScope ?? current.outOfScope,
    currentStep: payload.currentStep ?? current.currentStep,
    changedFiles: payload.changedFiles ?? current.changedFiles,
    commands: payload.commands ?? current.commands,
    verification: payload.verification ?? current.verification,
    summary: payload.summary ?? current.summary,
    resultSummary: payload.detail ?? payload.resultSummary ?? payload.summary ?? current.resultSummary,
    errorCode: payload.errorCode ?? current.errorCode,
    updatedAt: event.ts,
  };
}

function taskRecordFromEvent(event: AgentEventEnvelope): TaskRecord | null {
  if (!event.taskId || !event.sessionId || !event.type.startsWith("task.")) {
    return null;
  }

  const payload = (event.payload ?? {}) as Partial<TaskUpdatedPayload> & {
    goal?: string;
    title?: string;
    resultSummary?: string;
    detail?: string;
    errorCode?: string;
  };
  const status = payload.status ?? coerceTaskStatus(event, "running");
  return {
    id: event.taskId,
    sessionId: event.sessionId,
    type: "chat",
    status,
    goal: payload.goal ?? payload.title ?? "",
    acceptanceCriteria: payload.acceptanceCriteria,
    outOfScope: payload.outOfScope,
    currentStep: payload.currentStep,
    plan: payload.plan,
    changedFiles: payload.changedFiles,
    commands: payload.commands,
    verification: payload.verification,
    summary: payload.summary,
    resultSummary: payload.detail ?? payload.resultSummary ?? payload.summary,
    errorCode: payload.errorCode,
    createdAt: event.ts,
    updatedAt: event.ts,
  };
}

function readTaskContextPreview(value: unknown): TaskContextPreviewPayload | null {
  if (!value || typeof value !== "object") {
    return null;
  }
  const payload = value as { context?: unknown };
  if (!payload.context || typeof payload.context !== "object") {
    return null;
  }
  return payload.context as TaskContextPreviewPayload;
}

function buildSessionContextPreview({
  events,
  traceEvents,
  workspace,
  activeTaskId,
  activeTask,
}: {
  events: AgentEventEnvelope[];
  traceEvents: TraceEventRecord[];
  workspace: WorkspaceRef | null;
  activeTaskId: string | null;
  activeTask: TaskRecord | null;
}): SessionWorkspaceContextPreview | undefined {
  const liveContexts = events
    .filter((event) => !activeTaskId || event.taskId === activeTaskId)
    .map((event) => ({ ts: event.ts, context: readTaskContextPreview(event.payload) }))
    .filter((entry): entry is { ts: number; context: TaskContextPreviewPayload } => Boolean(entry.context));
  const traceContexts = traceEvents
    .filter((event) => !activeTaskId || event.taskId === activeTaskId)
    .map((event) => ({ ts: event.createdAt, context: readTaskContextPreview(event.payload) }))
    .filter((entry): entry is { ts: number; context: TaskContextPreviewPayload } => Boolean(entry.context));
  const latest = [...liveContexts, ...traceContexts].sort((left, right) => right.ts - left.ts)[0]?.context;
  const projectFocus = workspace?.focus ?? latest?.projectFocus ?? null;
  const projectMemory = workspace?.summary ?? latest?.projectMemory ?? null;

  if (!latest && !projectFocus && !projectMemory) {
    return undefined;
  }

  return {
    projectFocus,
    projectMemory,
    workspaceRoot: latest?.workspaceRoot ?? workspace?.rootPath,
    searchQuery: latest?.searchQuery,
    searchMode: latest?.searchMode,
    toolCount: latest?.toolCount,
    budgetStats: latest?.budgetStats,
    taskFocus: {
      currentStep: activeTask?.currentStep ?? latest?.taskFocus?.currentStep,
      acceptanceCriteriaCount:
        activeTask?.acceptanceCriteria?.length ?? latest?.taskFocus?.acceptanceCriteriaCount,
      outOfScopeCount: activeTask?.outOfScope?.length ?? latest?.taskFocus?.outOfScopeCount,
    },
  };
}

function getTaskBadgeClass(status?: TaskRecord["status"]): string {
  if (status === "completed") {
    return "ok";
  }
  if (status === "failed" || status === "cancelled") {
    return "error";
  }
  if (status === "running") {
    return "info";
  }
  if (status === "planning" || status === "verifying") {
    return "info";
  }
  if (status === "waiting_approval") {
    return "warn";
  }
  return "neutral";
}

const TASK_ACTION_PLAN_RE =
  /\b(apply|patch|edit|write|implement|modify|command|shell|run|verify|git|commit|diff|build|fix)\b|\u5e94\u7528|\u8865\u4e01|\u7f16\u8f91|\u5199\u5165|\u5b9e\u73b0|\u4fee\u6539|\u8fd0\u884c|\u6267\u884c|\u9a8c\u8bc1|\u6784\u5efa|\u4fee\u590d|\u63d0\u4ea4/i;
const TASK_QUESTION_GOAL_RE = new RegExp(
  "[?\\uFF1F]\\s*$|\\u5417|\\u662f\\u5426|\\u662f\\u4e0d\\u662f|\\u80fd\\u4e0d\\u80fd|\\u53ef\\u4ee5|\\u5b8c\\u6210\\u4e86\\u5417|\\u7ed3\\u675f\\u4e86\\u5417|\\u4ec0\\u4e48\\u60c5\\u51b5|\\u4e3a\\u4ec0\\u4e48|\\u600e\\u4e48",
);
const TASK_GENERIC_ANSWER_STEP_RE =
  /\b(inspect|search|summarize|understand|locate)\b|\u7406\u89e3|\u5b9a\u4f4d|\u67e5\u627e|\u6574\u7406|\u7b54\u590d|\u603b\u7ed3/i;

function taskHasWorkEvidence(task?: TaskRecord | null) {
  return Boolean(task?.changedFiles?.length || task?.commands?.length || task?.verification?.length);
}

function taskHasActionPlan(task?: TaskRecord | null) {
  return Boolean(task?.plan?.some((step) => TASK_ACTION_PLAN_RE.test(`${step.id ?? ""} ${step.title ?? ""}`)));
}

function isGenericAnswerTaskPlan(task?: TaskRecord | null) {
  const plan = task?.plan ?? [];
  return Boolean(
    plan.length > 0 &&
      plan.length <= 3 &&
      plan.every((step) => TASK_GENERIC_ANSWER_STEP_RE.test(`${step.id ?? ""} ${step.title ?? ""}`)),
  );
}

function shouldPromoteTaskToActive(task: TaskRecord, currentTaskId?: string | null) {
  if (currentTaskId && task.id === currentTaskId) return true;
  if (taskHasWorkEvidence(task) || taskHasActionPlan(task)) return true;
  if ((task.plan?.length ?? 0) > 3) return true;
  if (TASK_QUESTION_GOAL_RE.test(task.goal.trim()) && isGenericAnswerTaskPlan(task)) return false;
  return !TASK_QUESTION_GOAL_RE.test(task.goal.trim());
}

function appendAssistantToken(current: ChatMessageView[], event: AgentEventEnvelope): ChatMessageView[] {
  const payload = event.payload as AssistantTokenPayload;
  const delta = payload.delta ?? "";
  if (!delta || isOperationalAssistantDelta(delta)) {
    return current;
  }

  const next = [...current];
  const lastAssistantIndex = (() => {
    for (let index = next.length - 1; index >= 0; index -= 1) {
      const item = next[index];
      if (
        item.role === "assistant" &&
        item.streaming &&
        (item.taskId === event.taskId || (item.taskId === "pending" && item.sessionId === event.sessionId))
      ) {
        return index;
      }
    }
    return -1;
  })();

  if (lastAssistantIndex >= 0) {
    const currentMessage = next[lastAssistantIndex];
    next[lastAssistantIndex] = {
      ...currentMessage,
      taskId: event.taskId,
      content: currentMessage.placeholder ? delta : `${currentMessage.content}${delta}`,
      updatedAt: event.ts,
      placeholder: false,
    };
    return next;
  }

  return [
    ...next,
    {
      id: `assistant_${event.eventId}`,
      sessionId: event.sessionId,
      taskId: event.taskId,
      role: "assistant",
      content: delta,
      createdAt: event.ts,
      updatedAt: event.ts,
      streaming: true,
    },
  ];
}

function completeAssistantMessage(current: ChatMessageView[], event: AgentEventEnvelope): ChatMessageView[] {
  const completedContent = summarizeValue(
    getPayloadValue(event.payload, ["content", "message", "text"]),
    "",
    10_000,
  );
  const payloadRecord = event.payload && typeof event.payload === "object" ? (event.payload as Record<string, unknown>) : {};
  if (payloadRecord.supplemental === true && completedContent) {
    return [
      ...current,
      {
        id: `assistant_${event.eventId}`,
        sessionId: event.sessionId,
        taskId: event.taskId,
        role: "assistant",
        content: completedContent,
        createdAt: event.ts,
        updatedAt: event.ts,
        streaming: false,
      },
    ];
  }
  const next = [...current];
  const lastAssistantIndex = (() => {
    for (let index = next.length - 1; index >= 0; index -= 1) {
      const item = next[index];
      if (
        item.role === "assistant" &&
        (item.taskId === event.taskId || (item.streaming && item.taskId === "pending" && item.sessionId === event.sessionId))
      ) {
        return index;
      }
    }
    return -1;
  })();

  if (lastAssistantIndex >= 0) {
    const currentMessage = next[lastAssistantIndex];
    // Prefer streaming content when it was built from token deltas — avoids
    // replacing a rich multi-step answer with a shorter/final-summary that
    // may overlap or differ. Fall back to completedContent when the streaming
    // message is still a placeholder or very short.
    const streamingContent = currentMessage.content || "";
    const isPlaceholder = currentMessage.placeholder === true || streamingContent === "\u601d\u8003\u4e2d..." || streamingContent.length < 5;
    next[lastAssistantIndex] = {
      ...currentMessage,
      taskId: event.taskId,
      content: isPlaceholder ? (completedContent || streamingContent) : streamingContent,
      updatedAt: event.ts,
      streaming: false,
      placeholder: false,
    };
    return next;
  }

  if (!completedContent) {
    return current;
  }

  return [
    ...next,
    {
      id: `assistant_${event.eventId}`,
      sessionId: event.sessionId,
      taskId: event.taskId,
      role: "assistant",
      content: completedContent,
      createdAt: event.ts,
      updatedAt: event.ts,
      streaming: false,
    },
  ];
}

function failAssistantMessageForEvent(current: ChatMessageView[], event: AgentEventEnvelope): ChatMessageView[] {
  const content =
    summarizeValue(
      getPayloadValue(event.payload, ["detail", "resultSummary", "summary", "error", "message"]),
      "",
      10_000,
    ) || "任务失败，未返回具体错误。";
  return failAssistantMessage(current, {
    sessionId: event.sessionId,
    taskId: event.taskId,
    content: `任务失败：${content}`,
    now: event.ts,
  });
}

interface CollaborationSourceEvent {
  id: string;
  type: string;
  payload: unknown;
  time: number;
  taskId?: string;
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function readRecordString(record: Record<string, unknown>, key: string): string | undefined {
  const value = record[key];
  return typeof value === "string" && value.trim() ? value.trim() : undefined;
}

function readRecordRawString(record: Record<string, unknown>, key: string): string | undefined {
  const value = record[key];
  return typeof value === "string" && value.length > 0 ? value : undefined;
}

function readRecordNumber(record: Record<string, unknown>, key: string): number | undefined {
  const value = record[key];
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

function readRecordBoolean(record: Record<string, unknown>, key: string): boolean | undefined {
  const value = record[key];
  if (typeof value === "boolean") {
    return value;
  }
  if (typeof value === "string") {
    const normalized = value.trim().toLowerCase();
    if (normalized === "true") {
      return true;
    }
    if (normalized === "false") {
      return false;
    }
  }
  return undefined;
}

function readChildRecord(record: Record<string, unknown>, key: string): Record<string, unknown> | null {
  return asRecord(record[key]);
}

function readResultSummary(record: Record<string, unknown>): string | undefined {
  const result = readChildRecord(record, "result");
  return (
    readRecordString(record, "summary") ??
    (result ? readRecordString(result, "summary") : undefined) ??
    readRecordString(record, "description")
  );
}

function appendOutputTail(current: string, chunk: string, maxLength = 4000): string {
  if (current.endsWith(chunk)) {
    return current;
  }
  const combined = `${current}${chunk}`;
  if (combined.length <= maxLength) {
    return combined;
  }
  return combined.slice(combined.length - maxLength);
}

function buildSessionCollaboration(
  events: AgentEventEnvelope[],
  traceEvents: TraceEventRecord[],
): SessionWorkspaceCollaboration {
  const workers = new Map<string, NonNullable<SessionWorkspaceCollaboration["workers"]>[number]>();
  const childTasks = new Map<string, NonNullable<SessionWorkspaceCollaboration["childTasks"]>[number]>();
  const results = new Map<string, NonNullable<SessionWorkspaceCollaboration["results"]>[number]>();
  const sources: CollaborationSourceEvent[] = [
    ...events.map((event) => ({
      id: event.eventId,
      type: event.type,
      payload: event.payload,
      time: event.ts,
    })),
    ...traceEvents.map((trace) => ({
      id: trace.id,
      type: trace.type,
      payload: trace.payload,
      time: trace.createdAt,
    })),
  ];

  const rememberWorker = (worker: Record<string, unknown> | null, time: number) => {
    if (!worker) {
      return;
    }
    const id = readRecordString(worker, "id");
    if (!id) {
      return;
    }
    const metadata = readChildRecord(worker, "metadata");
    const health = readChildRecord(worker, "health");
    workers.set(id, {
      id,
      name: readRecordString(worker, "name") ?? id,
      status: readRecordString(worker, "status"),
      mode: readRecordString(worker, "role") ?? (metadata ? readRecordString(metadata, "mode") : undefined),
      healthState:
        readRecordString(worker, "healthState") ?? (health ? readRecordString(health, "state") : undefined),
      healthReason: health ? readRecordString(health, "reason") : undefined,
      heartbeatAgeMs: health ? readRecordNumber(health, "heartbeatAgeMs") : undefined,
      lastHeartbeatAt: health ? readRecordNumber(health, "lastHeartbeatAt") : undefined,
      claimedTaskId: readRecordString(worker, "currentTaskId") ?? undefined,
      summary: Array.isArray(worker.capabilities)
        ? worker.capabilities.filter((item): item is string => typeof item === "string").join(", ")
        : undefined,
      updatedAt: readRecordNumber(worker, "updatedAt") ?? time,
    });
  };

  const rememberTask = (task: Record<string, unknown> | null, time: number) => {
    if (!task) {
      return;
    }
    const id = readRecordString(task, "id");
    if (!id) {
      return;
    }
    childTasks.set(id, {
      id,
      title: readRecordString(task, "title") ?? id,
      status: readRecordString(task, "status"),
      workerId: readRecordString(task, "assignedWorkerId"),
      summary: readResultSummary(task),
      updatedAt: readRecordNumber(task, "updatedAt") ?? time,
    });

    const status = readRecordString(task, "status");
    const summary = readResultSummary(task);
    if (summary && (status === "completed" || status === "failed")) {
      results.set(`${id}:result`, {
        id: `${id}:result`,
        taskId: id,
        title: readRecordString(task, "title") ?? id,
        status,
        summary,
        updatedAt: readRecordNumber(task, "completedAt") ?? readRecordNumber(task, "updatedAt") ?? time,
      });
    }
  };

  for (const event of sources) {
    const payload = asRecord(event.payload);
    if (!payload) {
      continue;
    }

    if (event.type.startsWith("collab.task.")) {
      rememberTask(readChildRecord(payload, "task"), event.time);
      rememberWorker(readChildRecord(payload, "worker"), event.time);
    }

    if (event.type.startsWith("collab.worker.")) {
      rememberWorker(readChildRecord(payload, "worker"), event.time);
    }

    if (event.type === "collab.message.sent") {
      const message = readChildRecord(payload, "message");
      const taskId = message ? readRecordString(message, "taskId") : undefined;
      const kind = message ? readRecordString(message, "kind") : undefined;
      if (message && taskId && kind === "result") {
        results.set(message.id ? String(message.id) : `${taskId}:message`, {
          id: readRecordString(message, "id") ?? `${taskId}:message`,
          taskId,
          status: kind,
          summary: readRecordString(message, "body"),
          updatedAt: readRecordNumber(message, "createdAt") ?? event.time,
        });
      }
    }

    if (event.type === "tool.completed" && readRecordString(payload, "toolName") === "task") {
      const result = readChildRecord(payload, "result");
      if (result) {
        rememberTask(readChildRecord(result, "task"), event.time);
        rememberWorker(readChildRecord(result, "worker"), event.time);
        const childTaskId = readRecordString(result, "childTaskId");
        const summary = readRecordString(result, "summary") ?? readResultSummary(result);
        if (childTaskId && summary) {
          results.set(`${childTaskId}:tool`, {
            id: `${childTaskId}:tool`,
            taskId: childTaskId,
            status: readRecordString(result, "status"),
            summary,
            updatedAt: event.time,
          });
        }
      }
    }
  }

  const workerList = [...workers.values()];
  const taskList = [...childTasks.values()].map((task) => ({
    ...task,
    workerName: task.workerId ? workers.get(task.workerId)?.name ?? task.workerName : task.workerName,
  }));
  const resultList = [...results.values()];
  const healthSummary = workerList.reduce(
    (summary, worker) => {
      summary.total += 1;
      if (worker.healthState === "healthy" || worker.healthState === "stale" || worker.healthState === "offline") {
        summary[worker.healthState] += 1;
      }
      return summary;
    },
    { healthy: 0, stale: 0, offline: 0, total: 0 },
  );

  return {
    workers: workerList.sort((left, right) => (right.updatedAt ?? 0) - (left.updatedAt ?? 0)),
    childTasks: taskList.sort((left, right) => (right.updatedAt ?? 0) - (left.updatedAt ?? 0)),
    results: resultList.sort((left, right) => (right.updatedAt ?? 0) - (left.updatedAt ?? 0)),
    healthSummary,
  };
}

function buildSessionBackgroundJobs(
  events: AgentEventEnvelope[],
  traceEvents: TraceEventRecord[],
): SessionWorkspaceBackgroundJob[] {
  const jobs = new Map<string, SessionWorkspaceBackgroundJob>();
  const sources: CollaborationSourceEvent[] = [
    ...traceEvents.map((trace) => ({
      id: trace.id,
      type: trace.type,
      payload: trace.payload,
      time: trace.createdAt,
      taskId: trace.taskId,
    })),
    ...events.map((event) => ({
      id: event.eventId,
      type: event.type,
      payload: event.payload,
      time: event.ts,
      taskId: event.taskId,
    })),
  ].sort((left, right) => left.time - right.time);

  const rememberLifecycle = (type: string, payload: Record<string, unknown>, time: number) => {
    const id = readRecordString(payload, "commandId");
    if (!id) {
      return;
    }
    const current = jobs.get(id);
    const status =
      readRecordString(payload, "status") ??
      (type === "command.started"
        ? "running"
        : type === "command.completed"
          ? "completed"
          : type === "command.cancelled"
            ? "killed"
            : "failed");
    const next: SessionWorkspaceBackgroundJob = {
      id,
      command: readRecordString(payload, "command") ?? current?.command ?? id,
      status,
      cwd: readRecordString(payload, "cwd") ?? current?.cwd,
      shell: readRecordString(payload, "shell") ?? current?.shell,
      startedAt: current?.startedAt ?? (type === "command.started" ? time : undefined),
      finishedAt: type === "command.started" ? current?.finishedAt : time,
      durationMs: readRecordNumber(payload, "durationMs") ?? current?.durationMs,
      exitCode:
        readRecordNumber(payload, "exitCode") ??
        (payload.exitCode === null ? null : current?.exitCode),
      stdout: current?.stdout,
      stderr: current?.stderr,
      stdoutPath: readRecordString(payload, "stdoutPath") ?? current?.stdoutPath,
      stderrPath: readRecordString(payload, "stderrPath") ?? current?.stderrPath,
      isBackground: readRecordBoolean(payload, "background") ?? current?.isBackground,
      summary:
        status === "running"
          ? "命令仍在运行。"
          : status === "completed"
            ? `命令已完成${typeof readRecordNumber(payload, "exitCode") === "number" ? `，退出码 ${readRecordNumber(payload, "exitCode")}` : "。"}`
            : `命令状态：${formatStatusLabel(status)}${typeof readRecordNumber(payload, "exitCode") === "number" ? `，退出码 ${readRecordNumber(payload, "exitCode")}` : "。"}`,
    };
    jobs.set(id, next);
  };

  const rememberOutput = (payload: Record<string, unknown>) => {
    const id = readRecordString(payload, "commandId");
    const stream = readRecordString(payload, "stream");
    const chunk = readRecordRawString(payload, "chunk");
    if (!id || !stream || !chunk) {
      return;
    }
    const current = jobs.get(id) ?? {
      id,
      command: id,
      status: "running",
    };
    jobs.set(id, {
      ...current,
      stdout: stream === "stdout" ? appendOutputTail(current.stdout ?? "", chunk) : current.stdout,
      stderr: stream === "stderr" ? appendOutputTail(current.stderr ?? "", chunk) : current.stderr,
    });
  };

  for (const source of sources) {
    const payload = asRecord(source.payload);
    if (!payload) {
      continue;
    }
    if (source.type === "command.output") {
      rememberOutput(payload);
      continue;
    }
    if (
      source.type === "command.started" ||
      source.type === "command.completed" ||
      source.type === "command.failed" ||
      source.type === "command.cancelled"
    ) {
      rememberLifecycle(source.type, payload, source.time);
    }
  }

  return [...jobs.values()].sort(
    (left, right) =>
      (right.finishedAt ?? right.startedAt ?? 0) - (left.finishedAt ?? left.startedAt ?? 0),
  );
}

function commandLogToSessionBackgroundJob(log: CommandLogRecord): SessionWorkspaceBackgroundJob {
  return {
    id: log.id,
    command: log.command,
    status: log.status,
    cwd: log.cwd,
    shell: log.shell,
    startedAt: log.startedAt,
    finishedAt: log.finishedAt,
    durationMs: log.durationMs,
    exitCode: log.exitCode ?? null,
    stdout: log.stdout,
    stderr: log.stderr,
    stdoutPath: log.stdoutPath,
    stderrPath: log.stderrPath,
    isBackground: false,
    summary:
      log.status === "running"
        ? "命令仍在运行。"
        : log.status === "completed"
          ? `命令已完成${typeof log.exitCode === "number" ? `，退出码 ${log.exitCode}` : "。"}`
          : `命令状态：${formatStatusLabel(log.status)}${typeof log.exitCode === "number" ? `，退出码 ${log.exitCode}` : "。"}`
  };
}

function mergeSessionBackgroundJobs(
  eventJobs: SessionWorkspaceBackgroundJob[],
  commandLogs: CommandLogRecord[],
): SessionWorkspaceBackgroundJob[] {
  const jobsById = new Map(eventJobs.map((job) => [job.id, job]));

  for (const log of commandLogs) {
    const current = jobsById.get(log.id);
    jobsById.set(log.id, {
      ...current,
      ...commandLogToSessionBackgroundJob(log),
      stdout: log.stdout ?? current?.stdout,
      stderr: log.stderr ?? current?.stderr,
      stdoutPath: log.stdoutPath ?? current?.stdoutPath,
      stderrPath: log.stderrPath ?? current?.stderrPath,
    });
  }

  return [...jobsById.values()].sort(
    (left, right) =>
      (right.finishedAt ?? right.startedAt ?? 0) - (left.finishedAt ?? left.startedAt ?? 0),
  );
}

function describeMode(hostStatus: HostStatus | null): string {
  if (!hostStatus) {
    return "正在检测运行时";
  }

  return hostStatus.runtimeRunning ? "本地运行时已连接" : "浏览器预览模式";
}

function getProviderDisplayLabel(settings: ProviderSettingsForm): string {
  if (settings.mode === "mock") {
    return "本地预览模型";
  }
  return settings.model || settings.name || "未配置模型";
}

function RuntimeUnavailableWorkspace({ errorMessage }: { errorMessage: string }) {
  return (
    <main className="runtime-unavailable-workspace" aria-labelledby="runtime-unavailable-title">
      <section className="runtime-unavailable-card">
        <p className="runtime-unavailable-kicker">需要运行时</p>
        <h1 id="runtime-unavailable-title">桌面运行时不可用</h1>
        <p className="runtime-unavailable-copy">
          当前 UI 未连接到 Tauri 桌面运行时，因此模型供应商配置、聊天、工具调用和任务执行都会被阻止。
        </p>
        <div className="runtime-unavailable-actions">
          <div>
            <strong>启动真实桌面应用</strong>
            <code>npm run tauri:dev</code>
          </div>
          <div>
            <strong>仅预览浏览器模式</strong>
            <code>VITE_YUANBAO_ENABLE_BROWSER_MOCK=1 npm run dev</code>
          </div>
        </div>
        <div className="runtime-unavailable-detail" role="status">
          <strong>当前失败原因</strong>
          <pre>{errorMessage}</pre>
        </div>
      </section>
    </main>
  );
}

export function App() {
  const [hostStatus, setHostStatus] = useState<HostStatus | null>(null);
  const [config, setConfig] = useState<RuntimeConfig | null>(null);
  const [providerSettings, setProviderSettings] = useState<ProviderSettingsForm>(() =>
    buildProviderSettingsForm(null),
  );
  const [commandPolicySettings, setCommandPolicySettings] = useState<CommandPolicyForm>(() =>
    buildCommandPolicyForm(null),
  );
  const [activeProviderProfileId, setActiveProviderProfileId] = useState("default");
  const [providerTestResult, setProviderTestResult] = useState<ProviderTestResult | null>(null);
  const [providerFeedback, setProviderFeedback] = useState<SettingsProviderFeedback | null>(null);
  const [searchGlob, setSearchGlob] = useState(DEFAULT_SEARCH_GLOB_TEXT);
  const [searchIgnoreText, setSearchIgnoreText] = useState("");
  const [workspacePath, setWorkspacePath] = useState(DEFAULT_WORKSPACE_PATH);
  const [sessionTitle, setSessionTitle] = useState(DEFAULT_SESSION_TITLE);
  const [workspace, setWorkspace] = useState<WorkspaceRef | null>(null);
  const [sessions, setSessions] = useState<Array<SessionRecord>>([]);
  const [session, setSession] = useState<SessionRecord | null>(null);
  const [taskHistory, setTaskHistory] = useState<Array<TaskRecord>>([]);
  const [task, setTask] = useState<TaskRecord | null>(null);
  const [events, setEvents] = useState<Array<AgentEventEnvelope>>([]);
  const [traceEvents, setTraceEvents] = useState<Array<TraceEventRecord>>([]);
  const [commandLogCacheById, setCommandLogCacheById] = useState<Record<string, CommandLogRecord>>({});
  const [patchCacheById, setPatchCacheById] = useState<Record<string, PatchRecord>>({});
  const [prompt, setPrompt] = useState(DEFAULT_PROMPT);
  const [promptAttachments, setPromptAttachments] = useState<string[]>([]);
  const [queuedPromptSubmissions, setQueuedPromptSubmissions] = useState<QueuedPromptSubmission[]>([]);
  const [chatMessages, setChatMessages] = useState<ChatMessageView[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [toasts, setToasts] = useState<ToastEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [workspaceBusy, setWorkspaceBusy] = useState(false);
  const [workspaceFocusBusy, setWorkspaceFocusBusy] = useState(false);
  const [workspaceMemoryBusy, setWorkspaceMemoryBusy] = useState(false);
  const [sessionBusy, setSessionBusy] = useState(false);
  const [messageBusy, setMessageBusy] = useState(false);
  const [refreshBusy, setRefreshBusy] = useState(false);
  const [sessionListBusy, setSessionListBusy] = useState(false);
  const [providerConfigBusy, setProviderConfigBusy] = useState(false);
  const [providerTestBusy, setProviderTestBusy] = useState(false);
  const [commandPolicyBusy, setCommandPolicyBusy] = useState(false);
  const [searchConfigBusy, setSearchConfigBusy] = useState(false);
  const [approvalBusyId, setApprovalBusyId] = useState<string | null>(null);
  const [patchBusyId, setPatchBusyId] = useState<string | null>(null);
  const [commandJobBusyId, setCommandJobBusyId] = useState<string | null>(null);
  const [traceBusy, setTraceBusy] = useState(false);
  const [traceError, setTraceError] = useState<string | null>(null);
  const [taskControlBusyAction, setTaskControlBusyAction] = useState<TaskControlAction | null>(null);
  const [taskControlError, setTaskControlError] = useState<string | null>(null);
  const [activeTaskId, setActiveTaskId] = useState<string | null>(null);
  const [scheduledRecords, setScheduledRecords] = useState<ScheduledTaskRecord[]>([]);
  const [scheduledLogs, setScheduledLogs] = useState<ScheduledTaskRunRecord[]>([]);
  const [selectedScheduledTaskId, setSelectedScheduledTaskId] = useState<string | null>(null);
  const [scheduledBusyTaskId, setScheduledBusyTaskId] = useState<string | null>(null);
  const [scheduledCreateBusy, setScheduledCreateBusy] = useState(false);
  const [skills, setSkills] = useState<SkillPresetRecord[]>([]);
  const [skillBusyId, setSkillBusyId] = useState<string | null>(null);
  const [mcpServers, setMcpServers] = useState<McpServerRecord[]>([]);
  const [mcpBusyServerId, setMcpBusyServerId] = useState<string | null>(null);
  const [mcpLoading, setMcpLoading] = useState(false);
  const [mcpLastRefresh, setMcpLastRefresh] = useState<{ refreshed: number; tools: string[] } | null>(null);
  const [mcpError, setMcpError] = useState<string | null>(null);

  function addToast(kind: ToastEntry["kind"], message: string) {
    setToasts((current) => [...current.slice(-4), createToast(kind, message)]);
  }

  function getErrorMessage(reason: unknown): string {
    return reason instanceof Error ? reason.message : String(reason);
  }

  function toastError(reason: unknown) {
    addToast("error", getErrorMessage(reason));
  }

  function dismissToast(id: string) {
    setToasts((current) => current.filter((t) => t.id !== id));
  }

  const [generalSettings, setGeneralSettings] = useState<SettingsGeneralConfig>({
    theme: "dark",
    density: "comfortable",
    radius: "md",
    motion: "subtle",
    accentColor: "cyan",
    transparency: 0.78,
    fontScale: 1,
    language: "en",
    reasoningEffort: "max",
    webFetchPreflight: true,
  });
  const [imSettings, setIMSettings] = useState<SettingsIMConfig>({
    enabled: false,
    provider: "feishu",
    webhookUrl: "",
    signingSecretSet: false,
    defaultReplyMode: "manual",
  });
  const [computerUseSettings, setComputerUseSettings] = useState<SettingsComputerUseConfig>({
    screenshot: false,
    browserAutomation: false,
    clipboardAccess: true,
    systemKeyCombos: false,
    sensitiveActionConfirm: true,
    status: "",
  });
  const [openTabs, setOpenTabs] = useState<WorkbenchTab[]>(() => getInitialTabs());
  const [activeTabId, setActiveTabId] = useState<WorkbenchTab["id"]>("system:overview");
  const pendingAssistantTokenEventsRef = useRef<AgentEventEnvelope[]>([]);
  const assistantTokenFlushTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const messageLoadRequestRef = useRef(0);
  const childTaskIdsRef = useRef<Set<string>>(new Set());

  function clearPendingAssistantTokens() {
    pendingAssistantTokenEventsRef.current = [];
    if (assistantTokenFlushTimerRef.current !== null) {
      clearTimeout(assistantTokenFlushTimerRef.current);
      assistantTokenFlushTimerRef.current = null;
    }
  }

  function flushPendingAssistantTokens() {
    const pendingEvents = pendingAssistantTokenEventsRef.current;
    if (!pendingEvents.length) {
      return;
    }

    pendingAssistantTokenEventsRef.current = [];
    if (assistantTokenFlushTimerRef.current !== null) {
      clearTimeout(assistantTokenFlushTimerRef.current);
      assistantTokenFlushTimerRef.current = null;
    }

    setChatMessages((current) =>
      pendingEvents.reduce((nextMessages, event) => appendAssistantToken(nextMessages, event), current),
    );
  }

  function queueAssistantToken(event: AgentEventEnvelope) {
    const payload = event.payload as AssistantTokenPayload;
    const delta = payload.delta ?? "";
    if (!delta || isOperationalAssistantDelta(delta)) {
      return;
    }

    pendingAssistantTokenEventsRef.current.push(event);
    if (assistantTokenFlushTimerRef.current !== null) {
      return;
    }

    assistantTokenFlushTimerRef.current = setTimeout(() => {
      assistantTokenFlushTimerRef.current = null;
      flushPendingAssistantTokens();
    }, 33);
  }

  async function loadSessionMessages(sessionId: string | null | undefined) {
    if (!sessionId) {
      return;
    }

    const requestId = messageLoadRequestRef.current + 1;
    messageLoadRequestRef.current = requestId;
    try {
      const result = await runtimeClient.listMessages({ sessionId, limit: 500 });
      if (messageLoadRequestRef.current !== requestId) {
        return;
      }
      setChatMessages((current) => replaceSessionMessages(current, sessionId, result.messages));
    } catch (reason) {
      if (messageLoadRequestRef.current === requestId) {
        toastError(reason);
      }
    }
  }

  useEffect(() => {
    let disposed = false;

    Promise.all([
      runtimeClient.getHostStatus(),
      runtimeClient.getConfig(),
      runtimeClient.listSessions(),
      runtimeClient.listTasks(),
      runtimeClient.listScheduledTasks(),
      runtimeClient.listSkills().catch(() => ({ skills: [] as SkillPresetRecord[] })),
      runtimeClient.listMcpServers().catch(() => ({ servers: [] as McpServerRecord[] })),
    ])
      .then(([nextHostStatus, nextConfig, nextSessions, nextTasks, nextScheduledTasks, nextSkills, nextMcpServers]) => {
        if (disposed) {
          return;
        }

        const normalizedConfig = normalizeRuntimeConfig(nextConfig.config);
        setHostStatus(nextHostStatus);
        setConfig(normalizedConfig);
        setProviderSettings(buildProviderSettingsForm(normalizedConfig));
        setCommandPolicySettings(buildCommandPolicyForm(normalizedConfig));
        setGeneralSettings(buildSettingsGeneralConfig(normalizedConfig));
        setActiveProviderProfileId(normalizedConfig.provider.activeProfileId ?? "default");
        setSearchGlob(serializePatternList(normalizedConfig.search.glob));
        setSearchIgnoreText(serializePatternList(normalizedConfig.search.ignore));
        setSessions(nextSessions.sessions);
        setTaskHistory(nextTasks.tasks);
        const initialSession = nextSessions.sessions[0] ?? null;
        const initialTask = initialSession
          ? sortByUpdatedAtDesc(nextTasks.tasks.filter((item) => item.sessionId === initialSession.id))[0] ?? null
          : sortByUpdatedAtDesc(nextTasks.tasks)[0] ?? null;
        setSession(initialSession);
        setTask(initialTask);
        setActiveTaskId(initialTask?.id ?? null);
        void loadSessionMessages(initialSession?.id);
        setScheduledRecords(nextScheduledTasks.tasks);
        setSelectedScheduledTaskId(nextScheduledTasks.tasks[0]?.id ?? null);
        setSkills(nextSkills.skills);
        setMcpServers(nextMcpServers.servers);

        if (nextConfig.config.workspace.rootPath) {
          setWorkspacePath(nextConfig.config.workspace.rootPath);
        }
      })
      .catch((reason) => {
        if (!disposed) {
          setError(reason instanceof Error ? reason.message : String(reason));
        }
      })
      .finally(() => {
        if (!disposed) {
          setLoading(false);
        }
      });

    return () => {
      disposed = true;
    };
  }, []);

  useEffect(() => {
    let disposed = false;

    if (!selectedScheduledTaskId) {
      setScheduledLogs([]);
      return () => {
        disposed = true;
      };
    }

    runtimeClient
      .listScheduledTaskLogs({ taskId: selectedScheduledTaskId, limit: 50 })
      .then((result) => {
        if (!disposed) {
          setScheduledLogs(result.logs);
        }
      })
      .catch((reason) => {
        if (!disposed) {
          toastError(reason);
        }
      });

    return () => {
      disposed = true;
    };
  }, [selectedScheduledTaskId]);

  useEffect(() => {
    let active = true;
    let dispose: (() => void) | undefined;

    runtimeClient
      .subscribeEvents((event) => {
        if (!active) {
          return;
        }

        if (event.type === "assistant.token") {
          if (!childTaskIdsRef.current.has(event.taskId)) {
            queueAssistantToken(event);
          }
          return;
        }

        setEvents((current) => [...current, event].slice(-500));

        if (event.type === "session.updated") {
          const payload = (event.payload ?? {}) as SessionUpdatedPayload;
          setSession((current) =>
            current && current.id === event.sessionId
              ? {
                  ...current,
                  title: payload.title ?? current.title,
                  status: (payload.status as SessionRecord["status"] | undefined) ?? current.status,
                  summary: payload.summary ?? current.summary,
                  updatedAt: event.ts,
                }
              : current,
          );
          setSessions((current) =>
            current.map((item) =>
              item.id === event.sessionId
                ? {
                    ...item,
                    title: payload.title ?? item.title,
                    status: (payload.status as SessionRecord["status"] | undefined) ?? item.status,
                    summary: payload.summary ?? item.summary,
                    updatedAt: event.ts,
                  }
                : item,
            ),
          );
        }

        if (event.type.startsWith("task.")) {
          const eventTask = taskRecordFromEvent(event);
          const isChildWorker = (event.payload as Record<string, unknown>)?.childWorker === true;
          if (isChildWorker && event.type === "task.started") {
            childTaskIdsRef.current.add(event.taskId);
          }
          if (isChildWorker && event.type === "task.completed") {
            childTaskIdsRef.current.delete(event.taskId);
          }
          setActiveTaskId((current) => {
            if (isChildWorker) return current;
            if (event.type === "task.started") {
              return eventTask && shouldPromoteTaskToActive(eventTask, current) ? event.taskId : current;
            }
            return !current && eventTask && shouldPromoteTaskToActive(eventTask, current) ? event.taskId : current;
          });
          setTask((current) => {
            if (event.type === "task.started" && isChildWorker) {
              return current;
            }
            if (event.type === "task.started" && eventTask && !shouldPromoteTaskToActive(eventTask, current?.id ?? null)) {
              return current;
            }
            if (!current && event.type !== "task.started") {
              return current;
            }
            if (current && current.id !== event.taskId && event.type !== "task.started") {
              return current;
            }
            return applyEventToTask(current, event) ?? eventTask ?? current;
          });
          setTaskHistory((current) => {
            const existing = current.find((item) => item.id === event.taskId);
            const updated = existing ? applyEventToTask(existing, event) : eventTask;
            if (!updated) {
              return current;
            }

            return upsertRecord(current, updated);
          });
          setSession((current) =>
            current && current.id === event.sessionId
              ? { ...current, updatedAt: event.ts }
              : current,
          );
          setSessions((current) =>
            current.map((item) => (item.id === event.sessionId ? { ...item, updatedAt: event.ts } : item)),
          );
          if (event.type === "task.failed" && !isChildWorker) {
            flushPendingAssistantTokens();
            setChatMessages((current) => failAssistantMessageForEvent(current, event));
          }
        }

        if (event.type === "assistant.message.completed") {
          if (childTaskIdsRef.current.has(event.taskId)) {
            // skip child task completion
          } else {
            flushPendingAssistantTokens();
            setChatMessages((current) => completeAssistantMessage(current, event));
          }
        }
      })
      .then((unlisten) => {
        dispose = unlisten;
      })
      .catch((reason) => {
        if (active) {
          setError(reason instanceof Error ? reason.message : String(reason));
        }
      });

    return () => {
      active = false;
      clearPendingAssistantTokens();
      dispose?.();
    };
  }, []);

  async function loadTraceForTask(taskId: string, isCancelled: () => boolean = () => false) {
    setTraceBusy(true);
    setTraceError(null);

    try {
      const [result, commandResult] = await Promise.all([
        runtimeClient.listTrace({
          taskId,
          limit: TRACE_LIMIT,
        }),
        runtimeClient
          .commandLogList({
            taskId,
            limit: TRACE_LIMIT,
          })
          .catch(() => ({ commandLogs: [] as CommandLogRecord[] })),
      ]);
      if (!isCancelled()) {
        setTraceEvents(result.traceEvents);
        setCommandLogCacheById((current) => ({
          ...current,
          ...Object.fromEntries(commandResult.commandLogs.map((log) => [log.id, log])),
        }));
      }
    } catch (reason) {
      if (!isCancelled()) {
        setTraceEvents([]);
        setTraceError(reason instanceof Error ? reason.message : String(reason));
      }
    } finally {
      if (!isCancelled()) {
        setTraceBusy(false);
      }
    }
  }

  const traceAutoRefreshStatus =
    task && task.id === activeTaskId && TRACE_AUTO_REFRESH_STATUSES.has(task.status)
      ? task.status
      : undefined;

  useEffect(() => {
    if (!activeTaskId) {
      setTraceEvents([]);
      setCommandLogCacheById({});
      setTraceError(null);
      setTraceBusy(false);
      return;
    }

    let cancelled = false;
    void loadTraceForTask(activeTaskId, () => cancelled);
    return () => {
      cancelled = true;
    };
  }, [activeTaskId, traceAutoRefreshStatus]);

  useEffect(() => {
    setTaskControlError(null);
  }, [task?.id, task?.status]);

  const activeProviderProfile = useMemo(
    () => config?.provider.profiles?.find((profile) => profile.id === activeProviderProfileId),
    [activeProviderProfileId, config],
  );
  const providerStatusView = getProviderStatusView(providerSettings, providerTestResult);
  const providerRuntimeNotice = getProviderRuntimeNotice(providerSettings, providerTestResult);
  const providerHealthView = getProviderHealthView(activeProviderProfile, providerTestResult);

  const toolTimelineItems = useMemo<ToolTimelineItem[]>(() => {
    const items = new Map<string, ToolTimelineItem>();

    for (const event of events) {
      if (
        event.type !== "tool.started" &&
        event.type !== "tool.completed" &&
        event.type !== "tool.failed"
      ) {
        continue;
      }

      const payload = event.payload as Partial<ToolLifecyclePayload>;
      const toolCallId = payload.toolCallId ?? event.eventId;
      const current = items.get(toolCallId);
      const status = event.type.replace("tool.", "") as ToolTimelineItem["status"];
      const toolName = payload.toolName ?? current?.toolName ?? "unknown_tool";
      const argumentValue = payload.arguments ?? getPayloadValue(event.payload, ["args", "input", "parameters"]);
      const resultValue = getPayloadValue(event.payload, ["result", "output", "content", "summary"]);
      const errorValue = getPayloadValue(event.payload, ["error", "errorJson", "message"]);
      const durationMs =
        readEventNumber(event.payload, "durationMs") ??
        (current ? event.ts - current.startedAt : undefined);
      const resultSummary = summarizeToolResult(toolName, resultValue, errorValue, current?.resultSummary ?? "等待结果");
      const errorSummary = summarizeValue(errorValue, "");

      items.set(toolCallId, {
        id: toolCallId,
        taskId: event.taskId,
        toolCallId,
        toolName,
        status,
        argsSummary: summarizeToolArguments(toolName, argumentValue, current?.argsSummary ?? "未记录参数"),
        resultSummary,
        errorSummary: errorSummary || current?.errorSummary,
        argsRaw: formatRawValue(argumentValue) ?? current?.argsRaw,
        resultRaw: formatRawValue(resultValue ?? errorValue) ?? current?.resultRaw,
        startedAt: current?.startedAt ?? event.ts,
        updatedAt: event.ts,
        finishedAt: status === "started" ? current?.finishedAt : event.ts,
        durationMs: status === "started" ? current?.durationMs : durationMs,
        eventCount: (current?.eventCount ?? 0) + 1,
      });
    }

    return Array.from(items.values()).sort((left, right) => right.updatedAt - left.updatedAt);
  }, [events]);

  const approvalCards = useMemo<ApprovalCardView[]>(() => {
    const cards = new Map<string, ApprovalCardView>();

    for (const event of events) {
      if (event.type === "approval.requested") {
        const payload = event.payload as ApprovalRequestedPayload;
        const request = payload.request as Record<string, unknown>;
        const filesChanged = readRequestOptionalNumber(request, ["filesChanged", "files_changed"]);
        const changedFiles = readRequestStringList(request, ["files", "filesChangedList", "paths"]);
        const patchId = payload.kind === "apply_patch" ? payload.patchId ?? readRequestPatchId(request) : undefined;
        const patchSummary = readRequestText(request, "summary", readRequestText(request, "patchSummary", "patch approval request"));
        const command = readRequestText(request, "command", payload.kind === "apply_patch" ? "apply_patch" : "command");
        cards.set(payload.approvalId, {
          approvalId: payload.approvalId,
          taskId: payload.taskId,
          kind: payload.kind,
          patchId,
          patchSummary,
          filesChanged,
          command,
          cwd: readRequestText(request, "cwd", readRequestText(request, "workspaceRoot", ".")),
          shell: readRequestText(request, "shell", "system default"),
          timeoutMs: readRequestNumber(request, "timeoutMs", 0),
          risk: readRequestText(request, "risk", payload.kind === "apply_patch" ? "writes files" : "executes command"),
          requestJson: stringifyRequestJson(request),
          requestSummary:
            payload.kind === "apply_patch"
              ? `${patchSummary}${filesChanged !== undefined ? ` | ${filesChanged} file(s)` : ""}${
                  changedFiles.length > 0 ? ` | ${changedFiles.slice(0, 3).join(", ")}` : ""
                }`
              : `${command} | cwd ${readRequestText(request, "cwd", readRequestText(request, "workspaceRoot", "."))}`,
          status: "pending",
          requestedAt: event.ts,
          updatedAt: event.ts,
          requestedEventId: event.eventId,
        });
      }

      if (event.type === "approval.resolved") {
        const payload = event.payload as ApprovalResolvedPayload;
        const current = cards.get(payload.approvalId);
        if (current) {
          cards.set(payload.approvalId, {
            ...current,
            status: payload.decision,
            resolvedAt: event.ts,
            updatedAt: event.ts,
            resolvedEventId: event.eventId,
          });
          continue;
        }

        cards.set(payload.approvalId, {
          approvalId: payload.approvalId,
          taskId: payload.taskId,
          kind: "run_command",
          patchId: undefined,
          command: "unknown",
          cwd: ".",
          shell: "system default",
          timeoutMs: 0,
          risk: "not recorded",
          requestJson: "{}",
          requestSummary: "Resolved approval was received before the request event.",
          status: payload.decision,
          requestedAt: event.ts,
          updatedAt: event.ts,
          resolvedAt: event.ts,
          resolvedEventId: event.eventId,
        });
      }
    }

    return sortByUpdatedAtDesc(Array.from(cards.values()));
  }, [events]);

  const approvalByPatchId = useMemo(() => {
    const cards = new Map<string, ApprovalCardView>();
    for (const approval of approvalCards) {
      if (approval.patchId && !cards.has(approval.patchId)) {
        cards.set(approval.patchId, approval);
      }
    }
    return cards;
  }, [approvalCards]);

  const patchCards = useMemo<PatchCardView[]>(() => {
    const cards = new Map<string, PatchCardView>();

    for (const event of events) {
      if (event.type === "patch.proposed") {
        const payload = event.payload as PatchProposedPayload;
        const patchId = readEventText(payload, "patchId");
        if (!patchId) {
          continue;
        }
        const diffText = readEventText(payload, "diffText");
        cards.set(patchId, {
          patchId,
          taskId: event.taskId,
          summary: payload.summary,
          filesChanged: payload.filesChanged,
          status: "proposed",
          requestedAt: event.ts,
          updatedAt: event.ts,
          diffText,
        });
      }
    }

    for (const [patchId, patch] of Object.entries(patchCacheById)) {
      const current = cards.get(patchId);
      cards.set(patchId, {
        patchId,
        taskId: patch.taskId,
        summary: patch.summary,
        filesChanged: patch.filesChanged,
        status: patch.status,
        requestedAt: current?.requestedAt ?? patch.createdAt,
        updatedAt: Math.max(current?.updatedAt ?? patch.updatedAt, patch.updatedAt),
        diffText: patch.diffText,
      });
    }

    for (const card of cards.values()) {
      const approval = approvalByPatchId.get(card.patchId);
      if (approval) {
        card.approvalId = approval.approvalId;
        card.approvalStatus = approval.status;
        card.approvalResolvedAt = approval.resolvedAt;
        card.updatedAt = Math.max(card.updatedAt, approval.updatedAt);
        if (approval.status === "approved") {
          card.status = "approved";
        } else if (approval.status === "rejected") {
          card.status = "rejected";
        }
      }
    }

    return sortByUpdatedAtDesc(Array.from(cards.values()));
  }, [approvalByPatchId, events, patchCacheById]);

  const visibleChatMessages = useMemo(
    () => getVisibleChatMessages(chatMessages, session?.id),
    [chatMessages, session?.id],
  );

  async function ensureWorkspace(): Promise<WorkspaceRef> {
    const requestedPath = workspacePath.trim();
    if (!requestedPath) {
      throw new Error("Enter a workspace path before connecting.");
    }

    if (workspace && normalizeWorkspacePathForCompare(workspace.rootPath) === normalizeWorkspacePathForCompare(requestedPath)) {
      return workspace;
    }

    const result = await runtimeClient.openWorkspace(requestedPath);
    setWorkspace(result.workspace);
    setConfig((current) =>
      current
        ? {
            ...current,
            workspace: {
              ...current.workspace,
              rootPath: result.workspace.rootPath,
              writableRoots: [result.workspace.rootPath],
            },
          }
        : current,
    );
    return result.workspace;
  }

  function selectSession(nextSession: SessionRecord | null) {
    clearPendingAssistantTokens();
    setSession(nextSession);
    setActiveTaskId(null);
    setTask(null);
    setEvents([]);
    setTraceEvents([]);
    setCommandLogCacheById({});
    setTraceError(null);
    setPatchCacheById({});
    setPatchBusyId(null);
    setApprovalBusyId(null);

    if (!nextSession) {
      return;
    }

    void loadSessionMessages(nextSession.id);
    const nextTask = sortByUpdatedAtDesc(
      taskHistory.filter((item) => item.sessionId === nextSession.id),
    )[0];
    setTask(nextTask ?? null);
    setActiveTaskId(nextTask?.id ?? null);
  }

  function handleOpenSystemTab(kind: SystemWorkspaceKind) {
    setOpenTabs((current) => {
      const result = openSystemTab(current, kind);
      setActiveTabId(result.activeTabId);
      return result.tabs;
    });
  }

  function handleOpenSessionTab(nextSession: WorkbenchSession) {
    setOpenTabs((current) => {
      const result = openSessionTab(current, nextSession);
      setActiveTabId(result.activeTabId);
      return result.tabs;
    });
    selectSession(nextSession);
  }

  function handleActivateTab(tabId: WorkbenchTab["id"]) {
    setActiveTabId(tabId);
    if (!tabId.startsWith("session:")) {
      return;
    }

    const sessionId = tabId.slice("session:".length);
    const nextSession = sessions.find((item) => item.id === sessionId) ?? null;
    selectSession(nextSession);
  }

  function handleCloseTab(tabId: WorkbenchTab["id"]) {
    setOpenTabs((current) => {
      const result = closeTab(current, tabId, activeTabId);
      setActiveTabId(result.activeTabId);
      if (result.activeTabId.startsWith("session:")) {
        const sessionId = result.activeTabId.slice("session:".length);
        selectSession(sessions.find((item) => item.id === sessionId) ?? null);
      } else {
        selectSession(null);
      }
      return result.tabs;
    });
  }

  function handleCloseOtherTabs(tabId: WorkbenchTab["id"]) {
    setOpenTabs((current) => {
      const result = closeOtherTabs(current, tabId);
      setActiveTabId(result.activeTabId);
      if (result.activeTabId.startsWith("session:")) {
        const sessionId = result.activeTabId.slice("session:".length);
        selectSession(sessions.find((item) => item.id === sessionId) ?? null);
      } else {
        selectSession(null);
      }
      return result.tabs;
    });
  }

  async function handleRenameSession(sessionId: string, newTitle: string) {
    try {
      const result = await runtimeClient.updateSession({ sessionId, title: newTitle });
      setSessions((current) => upsertRecord(current, result.session));
      setSession((current) =>
        current && current.id === sessionId ? result.session : current,
      );
      setOpenTabs((current) => {
        const tabId = `session:${sessionId}`;
        return current.map((tab) =>
          tab.id === tabId ? { ...tab, title: newTitle } : tab,
        );
      });
    } catch (err) {
      setError(String(err));
    }
  }

  async function handleDeleteSession(sessionId: string) {
    try {
      await runtimeClient.deleteSession({ sessionId });
      setSessions((current) => current.filter((s) => s.id !== sessionId));
      setSession((current) => (current && current.id === sessionId ? null : current));
      setOpenTabs((current) => {
        const tabId = `session:${sessionId}`;
        const remaining = current.filter((tab) => tab.id !== tabId);
        if (remaining.length === current.length) return current;
        const fallback = remaining.length > 0 ? remaining[remaining.length - 1].id : "system:new-session";
        setActiveTabId(fallback);
        if (fallback.startsWith("session:")) {
          const sid = fallback.slice("session:".length);
          selectSession(sessions.find((item) => item.id === sid) ?? null);
        } else {
          selectSession(null);
        }
        return remaining;
      });
    } catch (err) {
      setError(String(err));
    }
  }

  function selectTask(taskId: string) {
    const nextTask = taskHistory.find((item) => item.id === taskId);
    if (!nextTask) {
      return;
    }

    setTask(nextTask);
    setActiveTaskId(nextTask.id);
  }

  function updateProviderSetting<K extends keyof ProviderSettingsForm>(
    key: K,
    value: ProviderSettingsForm[K],
  ) {
    setProviderSettings((current) => ({
      ...current,
      [key]: value,
    }));
    setProviderTestResult(null);
  }

  function updateCommandPolicySetting<K extends keyof CommandPolicyForm>(
    key: K,
    value: CommandPolicyForm[K],
  ) {
    setCommandPolicySettings((current) => ({
      ...current,
      [key]: value,
    }));
  }

  function buildProviderProfileFromForm(profileId = activeProviderProfileId): ProviderProfile {
    const existingProfile = config?.provider.profiles?.find((item) => item.id === profileId);
    const model = providerSettings.model.trim();
    const baseUrl = providerSettings.baseUrl.trim();
    const apiKeyEnvVarName = providerSettings.apiKeyEnvVarName.trim() || DEFAULT_PROVIDER_API_KEY_ENV_VAR;
    const profileName = providerSettings.name.trim() || "供应商配置";

    if (!model) {
      throw new Error("必须填写供应商模型。");
    }
    if (providerSettings.mode === "openai-compatible" && !baseUrl) {
      throw new Error("OpenAI 兼容模式必须填写基础 URL。");
    }

    const temperature = parseProviderNumber(providerSettings.temperature, "温度", {
      min: 0,
      max: 2,
    });
    const maxTokens = parseProviderNumber(providerSettings.maxTokens, "最大输出令牌", {
      integer: true,
      min: 1,
    });
    const maxContextTokens = parseProviderNumber(providerSettings.maxContextTokens, "最大上下文令牌", {
      integer: true,
      min: 1,
    });
    const timeout = parseProviderNumber(providerSettings.timeout, "超时时间", {
      min: 1,
    });

    return {
      id: profileId,
      name: profileName,
      mode: providerSettings.mode,
      baseUrl: baseUrl || DEFAULT_PROVIDER_BASE_URL,
      model,
      defaultModel: model,
      fallbackModel: config?.provider.fallbackModel,
      apiKeyEnvVarName,
      temperature,
      maxTokens,
      maxOutputTokens: maxTokens,
      maxContextTokens,
      timeout,
      lastCheckedAt: existingProfile?.lastCheckedAt,
      lastStatus: existingProfile?.lastStatus,
      lastErrorSummary: existingProfile?.lastErrorSummary,
    };
  }

  function buildProviderPatchFromForm(profileId = activeProviderProfileId): AppConfig["provider"] {
    const profile = buildProviderProfileFromForm(profileId);
    const model = profile.model ?? DEFAULT_PROVIDER_MODEL;
    const currentProfiles = config?.provider.profiles ?? [];
    const profiles = currentProfiles.some((item) => item.id === profile.id)
      ? currentProfiles.map((item) => (item.id === profile.id ? profile : item))
      : [...currentProfiles, profile];

    return {
      ...profile,
      model,
      defaultModel: profile.defaultModel ?? model,
      fallbackModel: config?.provider.fallbackModel,
      temperature: profile.temperature ?? DEFAULT_PROVIDER_TEMPERATURE,
      maxOutputTokens: profile.maxOutputTokens ?? profile.maxTokens ?? DEFAULT_PROVIDER_MAX_TOKENS,
      activeProfileId: profile.id,
      profiles,
    };
  }

  function selectProviderProfile(profileId: string) {
    if (!config) {
      return;
    }
    const normalized = normalizeProviderConfig({
      ...config.provider,
      activeProfileId: profileId,
    });
    setActiveProviderProfileId(normalized.activeProfileId ?? profileId);
    setProviderSettings(buildProviderSettingsForm({ ...config, provider: normalized }));
    setProviderTestResult(null);
    setProviderFeedback(null);
  }

  function showProviderSavedFeedback(normalized: RuntimeConfig, fallbackProfileId: string) {
    const provider = normalizeProviderConfig(normalized.provider);
    const activeProfile =
      provider.profiles?.find((profile) => profile.id === provider.activeProfileId) ??
      provider.profiles?.find((profile) => profile.id === fallbackProfileId);

    setProviderFeedback({
      providerId: activeProfile?.id ?? fallbackProfileId,
      tone: "success",
      title: "已保存并启用",
      message: `${activeProfile?.name ?? "供应商"} 已设为当前供应商。`,
      detail: `模型：${activeProfile?.model ?? provider.model ?? DEFAULT_PROVIDER_MODEL}`,
    });
  }

  function showProviderTestFeedback(result: ProviderTestResult, profileId: string) {
    setProviderFeedback({
      providerId: result.profileId ?? profileId,
      tone: result.ok ? "success" : "danger",
      title: result.ok ? "测试通过" : "测试失败",
      message: result.ok
        ? `运行时可连接 ${result.model ?? DEFAULT_PROVIDER_MODEL}。`
        : result.lastErrorSummary ?? result.message,
      detail: result.ok
        ? result.lastStatus ?? result.status
        : result.checkedEnvVarName
          ? `检查环境变量：${result.checkedEnvVarName}`
          : undefined,
    });
  }

  async function refreshSessionHistory(preferredSessionId?: string) {
    setSessionListBusy(true);
    setError(null);

    try {
      const result = await runtimeClient.listSessions();
      const taskResult = await runtimeClient.listTasks();
      const nextSessions = result.sessions;
      const nextTasks = taskResult.tasks;
      setSessions(nextSessions);
      setTaskHistory(nextTasks);

      const preferredSession =
        nextSessions.find((item) => item.id === preferredSessionId) ??
        (session ? nextSessions.find((item) => item.id === session.id) : undefined) ??
        nextSessions[0] ??
        null;

      setSession(preferredSession);

      if (!preferredSession) {
        setTask(null);
        setActiveTaskId(null);
        return;
      }

      const nextTask = sortByUpdatedAtDesc(
        nextTasks.filter((item) => item.sessionId === preferredSession.id),
      )[0];
      setTask(nextTask ?? null);
      setActiveTaskId(nextTask?.id ?? null);
      void loadSessionMessages(preferredSession.id);
    } catch (reason) {
      toastError(reason);
    } finally {
      setSessionListBusy(false);
    }
  }

  async function persistProviderConfig(): Promise<RuntimeConfig | null> {
    if (!config) {
      return null;
    }

    const providerPatch = buildProviderPatchFromForm();
    const result = await runtimeClient.updateConfig({
      config: {
        provider: providerPatch,
      },
    });
    const normalized = normalizeRuntimeConfig(result.config);
    setConfig(normalized);
    setProviderSettings(buildProviderSettingsForm(normalized));
    setActiveProviderProfileId(normalized.provider.activeProfileId ?? activeProviderProfileId);
    showProviderSavedFeedback(normalized, providerPatch.activeProfileId ?? activeProviderProfileId);
    return normalized;
  }

  async function runProviderTest(provider?: AppConfig["provider"], profileId = activeProviderProfileId) {
    setProviderTestBusy(true);
    setProviderTestResult(null);

    try {
      const result = await runtimeClient.testProvider(provider ? { profileId, provider } : { profileId });
      setProviderTestResult(result);
      showProviderTestFeedback(result, profileId);
      if (!provider) {
        const nextConfig = await runtimeClient.getConfig();
        const normalized = normalizeRuntimeConfig(nextConfig.config);
        setConfig(normalized);
        setProviderSettings(buildProviderSettingsForm(normalized));
        setActiveProviderProfileId(normalized.provider.activeProfileId ?? profileId);
      }
      return result;
    } finally {
      setProviderTestBusy(false);
    }
  }

  async function persistSearchConfig(): Promise<RuntimeConfig | null> {
    if (!config) {
      return null;
    }

    const nextSearch = {
      ...config.search,
      glob: parsePatternText(searchGlob),
      ignore: parsePatternText(searchIgnoreText),
    };
    const result = await runtimeClient.updateConfig({
      config: {
        search: {
          ...nextSearch,
        },
      },
    });
    const normalized = normalizeRuntimeConfig(result.config);
    setConfig(normalized);
    setSearchGlob(serializePatternList(normalized.search.glob));
    setSearchIgnoreText(serializePatternList(normalized.search.ignore));
    return normalized;
  }

  function buildRunCommandPatchFromForm(): ToolRuntimeConfig {
    const allowedCommands = parsePatternText(commandPolicySettings.allowedCommands);
    const deniedCommands = parsePatternText(commandPolicySettings.deniedCommands);
    return {
      allowedShell: commandPolicySettings.allowedShell,
      allowedCommands,
      allowlist: [...allowedCommands],
      deniedCommands,
      denylist: [...deniedCommands],
      blockedPatterns: parsePatternText(commandPolicySettings.blockedPatterns),
      allowedCwdRoots: parsePatternText(commandPolicySettings.allowedCwdRoots),
    };
  }

  async function persistCommandPolicyConfig(): Promise<RuntimeConfig | null> {
    if (!config) {
      return null;
    }

    await runtimeClient.updateConfig({
      config: {
        tools: {
          runCommand: buildRunCommandPatchFromForm(),
        },
      },
    });

    const refreshed = await runtimeClient.getConfig();
    const normalized = normalizeRuntimeConfig(refreshed.config);
    setConfig(normalized);
    setCommandPolicySettings(buildCommandPolicyForm(normalized));
    return normalized;
  }

  async function handleOpenWorkspace() {
    if (!workspacePath.trim()) {
      setError("Enter a workspace path before connecting.");
      return;
    }

    setWorkspaceBusy(true);
    setError(null);

    try {
      const result = await runtimeClient.openWorkspace(workspacePath.trim());
      clearPendingAssistantTokens();
      setWorkspace(result.workspace);
      setSession(null);
      setTask(null);
      setTaskHistory([]);
      setSessions([]);
      setActiveTaskId(null);
      setTraceEvents([]);
      setCommandLogCacheById({});
      setTraceError(null);
      setApprovalBusyId(null);
      setPatchCacheById({});
      setPatchBusyId(null);
      setEvents([]);
      setChatMessages([]);
      await refreshSessionHistory();
    } catch (reason) {
      toastError(reason);
    } finally {
      setWorkspaceBusy(false);
    }
  }

  async function handleClearWorkspaceMemory() {
    if (!workspace) {
      setError("Open a workspace before clearing project memory.");
      return;
    }

    setWorkspaceMemoryBusy(true);
    setError(null);
    try {
      const result = await runtimeClient.clearWorkspaceMemory({ workspaceId: workspace.id });
      setWorkspace(result.workspace);
    } catch (reason) {
      toastError(reason);
    } finally {
      setWorkspaceMemoryBusy(false);
    }
  }

  async function handleSaveWorkspaceFocus(focus: string) {
    if (!workspace) {
      setError("Open a workspace before saving project focus.");
      return;
    }

    setWorkspaceFocusBusy(true);
    setError(null);
    try {
      const result = await runtimeClient.updateWorkspaceFocus({
        workspaceId: workspace.id,
        focus,
      });
      setWorkspace(result.workspace);
    } catch (reason) {
      toastError(reason);
    } finally {
      setWorkspaceFocusBusy(false);
    }
  }

  async function handleCreateSession() {
    setSessionBusy(true);
    setError(null);

    try {
      const nextWorkspace = await ensureWorkspace();
      const result = await runtimeClient.createSession({
        workspaceId: nextWorkspace.id,
        title: sessionTitle.trim() || DEFAULT_SESSION_TITLE,
      });

      setSessions((current) => upsertRecord(current, result.session));
      selectSession(result.session);
      handleOpenSessionTab(result.session);
    } catch (reason) {
      toastError(reason);
    } finally {
      setSessionBusy(false);
    }
  }

  async function handleSaveSearchConfig() {
    setSearchConfigBusy(true);
    setError(null);

    try {
      await persistSearchConfig();
      addToast("success", "搜索设置已保存");
    } catch (reason) {
      toastError(reason);
    } finally {
      setSearchConfigBusy(false);
    }
  }

  async function handleSaveProviderConfig() {
    setProviderConfigBusy(true);
    setError(null);
    setProviderTestResult(null);

    try {
      const normalized = await persistProviderConfig();
      if (normalized) {
        await runProviderTest(undefined, normalized.provider.activeProfileId);
        addToast("success", "模型供应商设置已保存");
      }
    } catch (reason) {
      toastError(reason);
    } finally {
      setProviderConfigBusy(false);
    }
  }

  async function handleSaveCommandPolicyConfig() {
    setCommandPolicyBusy(true);
    setError(null);

    try {
      await persistCommandPolicyConfig();
      addToast("success", "命令策略已保存");
    } catch (reason) {
      toastError(reason);
    } finally {
      setCommandPolicyBusy(false);
    }
  }

  async function handleTestProvider() {
    setError(null);

    try {
      const providerPatch = buildProviderPatchFromForm();
      await runProviderTest(providerPatch, activeProviderProfileId);
    } catch (reason) {
      toastError(reason);
    }
  }

  async function handleTestSelectedProvider(profileId?: string) {
    setError(null);

    try {
      await runProviderTest(undefined, profileId ?? activeProviderProfileId);
    } catch (reason) {
      toastError(reason);
    }
  }

  async function handleTestProviderConfigFromSettings(payload: SettingsProviderPayload) {
    if (!config) {
      return;
    }

    setError(null);

    try {
      const profile = buildProviderProfileFromPayload(
        payload,
        activeProviderProfileId,
        config,
        activeProviderProfile,
      );
      return await runProviderTest(
        {
          ...profile,
          defaultModel: profile.defaultModel ?? profile.model ?? DEFAULT_PROVIDER_MODEL,
          temperature: profile.temperature ?? DEFAULT_PROVIDER_TEMPERATURE,
          maxOutputTokens: profile.maxOutputTokens ?? profile.maxTokens ?? DEFAULT_PROVIDER_MAX_TOKENS,
        },
        profile.id,
      );
    } catch (reason) {
      toastError(reason);
    }
  }

  async function handleAddProviderFromSettings(payload: SettingsProviderPayload) {
    if (!config) {
      return;
    }

    setProviderConfigBusy(true);
    setError(null);
    setProviderTestResult(null);

    try {
      const profileId = `profile_${Date.now()}`;
      const profile = buildProviderProfileFromPayload(payload, profileId, config);
      const provider = normalizeProviderConfig({
        ...config.provider,
        ...profile,
        activeProfileId: profile.id,
        profiles: [...(config.provider.profiles ?? []), profile],
      });
      const result = await runtimeClient.updateConfig({
        config: {
          provider,
        },
      });
      const normalized = normalizeRuntimeConfig(result.config);
      setConfig(normalized);
      setActiveProviderProfileId(normalized.provider.activeProfileId ?? profile.id);
      setProviderSettings(buildProviderSettingsForm(normalized));
      showProviderSavedFeedback(normalized, profile.id);
    } catch (reason) {
      toastError(reason);
    } finally {
      setProviderConfigBusy(false);
    }
  }

  async function handleEditProviderFromSettings(providerId: string, payload: SettingsProviderPayload) {
    if (!config) {
      return;
    }

    setProviderConfigBusy(true);
    setError(null);
    setProviderTestResult(null);

    try {
      const existingProfile = config.provider.profiles?.find((profile) => profile.id === providerId);
      const profile = buildProviderProfileFromPayload(payload, providerId, config, existingProfile);
      const nextProfiles = (config.provider.profiles ?? []).some((item) => item.id === providerId)
        ? (config.provider.profiles ?? []).map((item) => (item.id === providerId ? profile : item))
        : [...(config.provider.profiles ?? []), profile];
      const provider = normalizeProviderConfig({
        ...config.provider,
        ...profile,
        activeProfileId: profile.id,
        profiles: nextProfiles,
      });
      const result = await runtimeClient.updateConfig({
        config: {
          provider,
        },
      });
      const normalized = normalizeRuntimeConfig(result.config);
      setConfig(normalized);
      setActiveProviderProfileId(normalized.provider.activeProfileId ?? profile.id);
      setProviderSettings(buildProviderSettingsForm(normalized));
      showProviderSavedFeedback(normalized, profile.id);
    } catch (reason) {
      toastError(reason);
    } finally {
      setProviderConfigBusy(false);
    }
  }

  async function handlePermissionModeChange(mode: string) {
    if (!config) {
      return;
    }

    setError(null);

    try {
      const result = await runtimeClient.updateConfig({
        config: {
          policy: {
            approvalMode: settingsModeToApprovalMode(mode),
          },
        },
      });
      const normalized = normalizeRuntimeConfig(result.config);
      setConfig(normalized);
      addToast("success", "权限模式已保存");
    } catch (reason) {
      toastError(reason);
    }
  }

  async function handleGeneralSettingsChange(next: SettingsGeneralConfig) {
    setGeneralSettings(next);

    if (!config) {
      return;
    }

    setError(null);

    try {
      const result = await runtimeClient.updateConfig({
        config: {
          ui: {
            ...config.ui,
            language: settingsLanguageToConfig(next.language),
            theme: next.theme,
            density: next.density,
            radius: next.radius,
            motion: next.motion,
            accentColor: next.accentColor,
            transparency: next.transparency,
            fontScale: next.fontScale,
            reasoningEffort: next.reasoningEffort,
            webFetchPreflight: next.webFetchPreflight,
          },
        },
      });
      const normalized = normalizeRuntimeConfig(result.config);
      setConfig(normalized);
      setGeneralSettings(buildSettingsGeneralConfig(normalized));
      addToast("success", "外观设置已保存");
    } catch (reason) {
      toastError(reason);
    }
  }

  async function refreshSkills() {
    setError(null);
    try {
      const result = await runtimeClient.listSkills();
      setSkills(result.skills);
      addToast("success", "技能已刷新");
    } catch (reason) {
      toastError(reason);
    }
  }

  async function handleCreateSkill(draft: SkillDraft) {
    setSkillBusyId("create");
    setError(null);
    try {
      const result = await runtimeClient.createSkill(buildSkillPayload(draft));
      setSkills((current) => [result.skill, ...current.filter((skill) => skill.id !== result.skill.id)]);
      addToast("success", `技能已创建：${result.skill.name}`);
    } catch (reason) {
      toastError(reason);
    } finally {
      setSkillBusyId(null);
    }
  }

  async function handleUpdateSkill(skillId: string, draft: SkillDraft) {
    setSkillBusyId(skillId);
    setError(null);
    try {
      const result = await runtimeClient.updateSkill({
        skillId,
        ...buildSkillPayload(draft),
      });
      setSkills((current) =>
        current.map((skill) => skill.id === result.skill.id ? result.skill : skill),
      );
      addToast("success", `技能已更新：${result.skill.name}`);
    } catch (reason) {
      toastError(reason);
    } finally {
      setSkillBusyId(null);
    }
  }

  async function handleDeleteSkill(skillId: string) {
    setSkillBusyId(skillId);
    setError(null);
    try {
      await runtimeClient.deleteSkill({ skillId });
      setSkills((current) => current.filter((skill) => skill.id !== skillId));
      addToast("success", "技能已删除");
    } catch (reason) {
      toastError(reason);
    } finally {
      setSkillBusyId(null);
    }
  }

  async function handleImportSkills(filePath: string) {
    setSkillBusyId("import");
    setError(null);
    try {
      const result = await runtimeClient.importSkills({ filePath });
      if (result.imported.length > 0) {
        await refreshSkills();
        addToast("success", `已导入 ${result.imported.length} 个技能`);
      }
      if (result.skipped.length > 0) {
        addToast("info", `已跳过 ${result.skipped.length} 个同名技能：${result.skipped.join("、")}`);
      }
      if (result.errors.length > 0) {
        const errorNames = result.errors.map((e) => e.name || "未知").join("、");
        addToast("error", `导入失败：${errorNames}`);
      }
    } catch (reason) {
      toastError(reason);
    } finally {
      setSkillBusyId(null);
    }
  }

  async function handleOpenAppPath(kind: "logs" | "data") {
    setError(null);
    try {
      const result = await runtimeClient.openAppPath(kind);
      addToast("success", `已打开${kind === "logs" ? "日志" : "数据目录"}：${result.path}`);
    } catch (reason) {
      toastError(reason);
    }
  }

  async function handleCopyRuntimeText(label: string, text: string) {
    try {
      await navigator.clipboard?.writeText(text);
      addToast("success", `${label}已复制`);
    } catch (reason) {
      toastError(reason);
    }
  }

  function handleRecheckComputerUse() {
    const status = buildComputerUseStatus();
    setComputerUseSettings((current) => ({
      ...current,
      status,
    }));
    addToast("info", "电脑操作能力已检查");
  }

  async function refreshMcpServers() {
    setMcpLoading(true);
    setError(null);
    try {
      const result = await runtimeClient.listMcpServers();
      setMcpServers(result.servers);
      setMcpError(null);
    } catch (reason) {
      setMcpError(getErrorMessage(reason));
      toastError(reason);
    } finally {
      setMcpLoading(false);
    }
  }

  async function handleCreateMcpServer(draft: McpServerDraft) {
    setMcpLoading(true);
    setError(null);
    try {
      const result = await runtimeClient.createMcpServer(buildMcpServerPayload(draft));
      setMcpServers((current) => [
        result.server,
        ...current.filter((server) => server.id !== result.server.id),
      ]);
      setMcpError(null);
      addToast("success", "MCP 服务器已创建");
    } catch (reason) {
      setMcpError(getErrorMessage(reason));
      toastError(reason);
      throw reason;
    } finally {
      setMcpLoading(false);
    }
  }

  async function handleImportMcpServers(drafts: McpServerDraft[]) {
    setMcpLoading(true);
    setError(null);
    try {
      const imported: McpServerRecord[] = [];
      for (const draft of drafts) {
        const result = await runtimeClient.createMcpServer(buildMcpServerPayload(draft));
        imported.push(result.server);
      }
      setMcpServers((current) => [
        ...imported,
        ...current.filter((server) => !imported.some((item) => item.id === server.id)),
      ]);
      setMcpError(null);
      addToast("success", `Imported ${imported.length} MCP server${imported.length === 1 ? "" : "s"}`);
    } catch (reason) {
      setMcpError(getErrorMessage(reason));
      toastError(reason);
      throw reason;
    } finally {
      setMcpLoading(false);
    }
  }

  async function handleUpdateMcpServer(serverId: string, draft: McpServerDraft) {
    setMcpBusyServerId(serverId);
    setError(null);
    try {
      const result = await runtimeClient.updateMcpServer({
        serverId,
        ...buildMcpServerPayload(draft),
      });
      setMcpServers((current) =>
        current.map((server) => (server.id === result.server.id ? result.server : server)),
      );
      setMcpError(null);
      addToast("success", "MCP 服务器已更新");
    } catch (reason) {
      setMcpError(getErrorMessage(reason));
      toastError(reason);
      throw reason;
    } finally {
      setMcpBusyServerId(null);
    }
  }

  async function handleToggleMcpServer(serverId: string, enabled: boolean) {
    setMcpBusyServerId(serverId);
    setError(null);
    try {
      const result = await runtimeClient.updateMcpServer({ serverId, enabled });
      setMcpServers((current) =>
        current.map((server) => (server.id === result.server.id ? result.server : server)),
      );
      setMcpError(null);
      addToast("success", enabled ? "MCP 服务器已启用" : "MCP 服务器已停用");
    } catch (reason) {
      setMcpError(getErrorMessage(reason));
      toastError(reason);
    } finally {
      setMcpBusyServerId(null);
    }
  }

  async function handleRefreshMcpTools(serverId?: string) {
    setMcpBusyServerId(serverId ?? "__all__");
    setError(null);
    try {
      const result = await runtimeClient.refreshMcpTools(serverId ? { serverId } : {});
      setMcpLastRefresh(result);
      await refreshMcpServers();
      setMcpError(null);
      addToast("success", `已刷新 ${result.refreshed} 个 MCP 工具`);
    } catch (reason) {
      setMcpError(getErrorMessage(reason));
      toastError(reason);
    } finally {
      setMcpBusyServerId(null);
    }
  }

  async function handleDeleteMcpServer(serverId: string) {
    setMcpBusyServerId(serverId);
    setError(null);
    try {
      await runtimeClient.deleteMcpServer({ serverId });
      setMcpServers((current) => current.filter((server) => server.id !== serverId));
      setMcpError(null);
      addToast("success", "MCP 服务器已删除");
    } catch (reason) {
      setMcpError(getErrorMessage(reason));
      toastError(reason);
    } finally {
      setMcpBusyServerId(null);
    }
  }

  async function refreshScheduledRecords(preferredTaskId?: string) {
    const result = await runtimeClient.listScheduledTasks();
    setScheduledRecords(result.tasks);
    const nextSelectedTaskId =
      preferredTaskId && result.tasks.some((item) => item.id === preferredTaskId)
        ? preferredTaskId
        : selectedScheduledTaskId && result.tasks.some((item) => item.id === selectedScheduledTaskId)
          ? selectedScheduledTaskId
          : result.tasks[0]?.id ?? null;
    setSelectedScheduledTaskId(nextSelectedTaskId);
    return result.tasks;
  }

  async function handleRunScheduledTask(taskId: string) {
    setScheduledBusyTaskId(taskId);
    setError(null);

    try {
      const result = await runtimeClient.runScheduledTaskNow({ taskId });
      await refreshScheduledRecords(taskId);
      const logs = await runtimeClient.listScheduledTaskLogs({ taskId, limit: 50 });
      setScheduledLogs(logs.logs);
      setSelectedScheduledTaskId(taskId);
      if (result.run.summary) {
        setError(result.run.summary);
      }
    } catch (reason) {
      toastError(reason);
    } finally {
      setScheduledBusyTaskId(null);
    }
  }

  async function handleToggleScheduledTask(taskId: string) {
    const current = scheduledRecords.find((item) => item.id === taskId);
    if (!current) {
      return;
    }

    setScheduledBusyTaskId(taskId);
    setError(null);

    try {
      await runtimeClient.toggleScheduledTask({
        taskId,
        enabled: !current.enabled,
      });
      await refreshScheduledRecords(taskId);
      setSelectedScheduledTaskId(taskId);
    } catch (reason) {
      toastError(reason);
    } finally {
      setScheduledBusyTaskId(null);
    }
  }

  async function handleCreateProviderProfile() {
    if (!config) {
      return;
    }

    setProviderConfigBusy(true);
    setError(null);
    setProviderTestResult(null);

    try {
      const profileId = `profile_${Date.now()}`;
      const profile = {
        ...buildProviderProfileFromForm(profileId),
        name: `配置 ${(config.provider.profiles?.length ?? 0) + 1}`,
      };
      const result = await runtimeClient.updateConfig({
        config: {
          provider: {
            ...profile,
            defaultModel: profile.defaultModel ?? profile.model,
            temperature: profile.temperature ?? DEFAULT_PROVIDER_TEMPERATURE,
            maxOutputTokens: profile.maxOutputTokens ?? profile.maxTokens ?? DEFAULT_PROVIDER_MAX_TOKENS,
            fallbackModel: config.provider.fallbackModel,
            activeProfileId: profile.id,
            profiles: [...(config.provider.profiles ?? []), profile],
          },
        },
      });
      const normalized = normalizeRuntimeConfig(result.config);
      setConfig(normalized);
      setActiveProviderProfileId(normalized.provider.activeProfileId ?? profile.id);
      setProviderSettings(buildProviderSettingsForm(normalized));
    } catch (reason) {
      toastError(reason);
    } finally {
      setProviderConfigBusy(false);
    }
  }

  async function handleCopyProviderProfile() {
    if (!config) {
      return;
    }

    setProviderConfigBusy(true);
    setError(null);
    setProviderTestResult(null);

    try {
      const source = activeProviderProfile ?? buildProviderProfileFromForm(activeProviderProfileId);
      const profileId = `profile_${Date.now()}`;
      const profile: ProviderProfile = {
        ...source,
        id: profileId,
        name: `${source.name || "供应商配置"} 副本`,
      };
      delete profile.lastCheckedAt;
      delete profile.lastStatus;
      delete profile.lastErrorSummary;

      const provider = normalizeProviderConfig({
        ...config.provider,
        activeProfileId: profileId,
        profiles: [...(config.provider.profiles ?? []), profile],
      });
      const result = await runtimeClient.updateConfig({
        config: {
          provider,
        },
      });
      const normalized = normalizeRuntimeConfig(result.config);
      setConfig(normalized);
      setActiveProviderProfileId(normalized.provider.activeProfileId ?? profileId);
      setProviderSettings(buildProviderSettingsForm(normalized));
    } catch (reason) {
      toastError(reason);
    } finally {
      setProviderConfigBusy(false);
    }
  }

  async function handleDeleteProviderProfile() {
    if (!config) {
      return;
    }

    setProviderConfigBusy(true);
    setError(null);
    setProviderTestResult(null);

    try {
      const currentProfiles = config.provider.profiles ?? [];
      const remainingProfiles = currentProfiles.filter((profile) => profile.id !== activeProviderProfileId);
      const nextProfiles = remainingProfiles.length ? remainingProfiles : [buildDefaultProviderProfile()];
      const nextActiveProfileId = nextProfiles[0]?.id ?? "default";
      const provider = normalizeProviderConfig({
        ...config.provider,
        activeProfileId: nextActiveProfileId,
        profiles: nextProfiles,
      });
      const result = await runtimeClient.updateConfig({
        config: {
          provider,
        },
      });
      const normalized = normalizeRuntimeConfig(result.config);
      setConfig(normalized);
      setActiveProviderProfileId(normalized.provider.activeProfileId ?? nextActiveProfileId);
      setProviderSettings(buildProviderSettingsForm(normalized));
    } catch (reason) {
      toastError(reason);
    } finally {
      setProviderConfigBusy(false);
    }
  }

  async function sendMessageContent(
    messageContentInput: string,
    messageAttachmentsInput: string[],
    options: { clearComposer?: boolean; mode?: "new" | "supplement" | "queued" } = {},
  ) {
    if (!messageContentInput.trim() && messageAttachmentsInput.length === 0) {
      setError("Enter a task description before sending.");
      return;
    }

    setMessageBusy(true);
    setError(null);
    let pendingAssistantMessageIdForCatch: string | null = null;
    let pendingSessionIdForCatch: string | null = null;

    try {
      await persistSearchConfig();
      const activeSession =
        activeTab.kind === "session"
          ? activeSessionRecord ?? (await ensureSessionForSend())
          : await ensureSessionForSend();
      pendingSessionIdForCatch = activeSession.id;
      const messageContent = messageContentInput.trim() || "Please review the attached file.";
      const messageAttachments = messageAttachmentsInput;
      const messageCreatedAt = Date.now();
      const pendingUserMessageId = `user_${messageCreatedAt}`;
      const pendingAssistantMessageId = `assistant_pending_${messageCreatedAt}`;
      const shouldCreateAssistantPlaceholder = options.mode !== "supplement";
      pendingAssistantMessageIdForCatch = shouldCreateAssistantPlaceholder ? pendingAssistantMessageId : null;
      const currentTaskIdBeforeSend = task?.id ?? null;
      if (shouldCreateAssistantPlaceholder) {
        clearPendingAssistantTokens();
      }
      if (options.clearComposer ?? true) {
        setPrompt("");
        setPromptAttachments([]);
      }
      setChatMessages((current) =>
        shouldCreateAssistantPlaceholder
          ? appendAssistantPlaceholder(
              appendUserMessage(current, {
                id: pendingUserMessageId,
                sessionId: activeSession.id,
                content: messageContent,
                now: messageCreatedAt,
              }),
              {
                id: pendingAssistantMessageId,
                sessionId: activeSession.id,
                content: "\u601d\u8003\u4e2d...",
                now: messageCreatedAt + 1,
              },
            )
          : appendUserMessage(current, {
              id: pendingUserMessageId,
              sessionId: activeSession.id,
              content: messageContent,
              now: messageCreatedAt,
            }),
      );
      setApprovalBusyId(null);

      const result = await runtimeClient.sendMessage({
        sessionId: activeSession.id,
        content: messageContent,
        attachments: messageAttachments,
        mode: options.mode,
        taskId: options.mode === "supplement" ? task?.id ?? activeTaskId ?? undefined : undefined,
        newTask: options.mode === "new" ? true : undefined,
      });

      setChatMessages((current) => updatePendingMessageTask(current, pendingUserMessageId, result.task.id));
      if (shouldCreateAssistantPlaceholder) {
        setChatMessages((current) => updatePendingMessageTask(current, pendingAssistantMessageId, result.task.id));
      }
      setTaskHistory((current) => upsertRecord(current, result.task));
      if (shouldPromoteTaskToActive(result.task, currentTaskIdBeforeSend)) {
        setTask(result.task);
        setActiveTaskId(result.task.id);
      }
      const touchedSession = { ...activeSession, updatedAt: result.task.updatedAt };
      setSessions((current) => upsertRecord(current, touchedSession));
      setSession(touchedSession);
      setOpenTabs((current) => {
        const resultTabs = openSessionTab(current, touchedSession);
        setActiveTabId(resultTabs.activeTabId);
        return resultTabs.tabs;
      });
    } catch (reason) {
      if (pendingAssistantMessageIdForCatch || options.mode === "supplement") {
        const errorSummary = getErrorMessage(reason);
        setChatMessages((current) =>
          failAssistantMessage(current, {
            messageId: pendingAssistantMessageIdForCatch,
            sessionId: pendingSessionIdForCatch ?? activeSessionRecord?.id ?? session?.id ?? "pending",
            taskId: task?.id ?? activeTaskId ?? undefined,
            content: `发送失败：${errorSummary}`,
            now: Date.now(),
          }),
        );
      }
      toastError(reason);
    } finally {
      setMessageBusy(false);
    }
  }

  async function handleSendMessage() {
    if (!prompt.trim() && promptAttachments.length === 0) {
      setError("Enter a task description before sending.");
      return;
    }

    // Slash command dispatch.
    const slashResult = dispatchSlashCommand(prompt);
    if (slashResult) {
      setPrompt("");
      handleSlashCommand(slashResult);
      return;
    }

    await sendMessageContent(prompt, promptAttachments, {
      clearComposer: true,
      mode: composerCanStop || composerHasStreamingMessage ? "supplement" : "new",
    });
  }

  function handleQueuePrompt() {
    if (!prompt.trim() && promptAttachments.length === 0) {
      setError("Enter a task description before queueing.");
      return;
    }

    const slashResult = dispatchSlashCommand(prompt);
    if (slashResult) {
      setError("Slash commands cannot be queued.");
      return;
    }

    const queued: QueuedPromptSubmission = {
      id: `queued_${Date.now()}`,
      content: prompt.trim() || "Please review the attached file.",
      attachments: promptAttachments,
    };
    setQueuedPromptSubmissions((current) => [...current, queued]);
    setPrompt("");
    setPromptAttachments([]);
    setError(null);
  }

  function formatMcpSummary(servers: McpServerRecord[]): string {
    if (servers.length === 0) {
      return "暂无 MCP 服务器。可以在侧边栏的 **MCP** 页签中添加。";
    }
    const lines = servers.map((s) => {
      const status = s.enabled ? "已启用" : "已停用";
      const transport = s.transport ?? "stdio";
      const detail = s.url ?? s.command ?? "";
      return `- **${s.name}** (${status}) — ${transport}${detail ? `: ${detail}` : ""}`;
    });
    const enabled = servers.filter((s) => s.enabled).length;
    return `**MCP 服务器**（${enabled}/${servers.length} 已启用）\n\n${lines.join("\n")}\n\n_使用 \`/mcp refresh\` 重新发现工具。_`;
  }

  function formatSkillsSummary(skillList: SkillPresetRecord[]): string {
    if (skillList.length === 0) {
      return "暂无技能预设。可以在侧边栏的 **技能** 页签中创建。";
    }
    const lines = skillList.map((s) => {
      const builtin = s.isBuiltin || s.is_builtin ? " [内置]" : "";
      const cat = s.category ? ` (${s.category})` : "";
      return `- **${s.name}**${cat}${builtin} — ${(s.description || "").slice(0, 80)}`;
    });
    return `**技能**（${skillList.length} 个预设）\n\n${lines.join("\n")}`;
  }

  /** Handle a locally-recognized slash command. */
  async function handleSlashCommand(cmd: ReturnType<typeof dispatchSlashCommand>) {
    if (!cmd) return;

    switch (cmd.kind) {
      case "help": {
        const lines = SLASH_COMMANDS.map(
          (c) => `**${c.name}**${c.argsHint ? ` ${c.argsHint}` : ""} - ${c.description}`,
        );
        const helpText = `**可用命令：**\n\n${lines.join("\n")}`;
        addSystemMessage(helpText);
        break;
      }
      case "clear":
        setChatMessages([]);
        addToast("success", "聊天已清空");
        break;
      case "compact": {
        if (!session) {
          addSystemMessage("没有活跃会话，无法压缩上下文。");
          break;
        }
        try {
          const result = await runtimeClient.compactSession({ sessionId: session.id });
          if (result.strategy === "none" || result.tokensBefore === 0) {
            addSystemMessage("会话消息为空，无需压缩。");
          } else {
            const saved = result.tokensBefore - result.tokensAfter;
            addSystemMessage(
              `**上下文压缩完成**\n\n` +
              `- 策略：${result.strategy}\n` +
              `- 压缩前：${result.tokensBefore} tokens\n` +
              `- 压缩后：${result.tokensAfter} tokens\n` +
              `- 节省：${saved > 0 ? saved : 0} tokens` +
              (result.summary ? `\n\n**摘要：**\n${result.summary.slice(0, 500)}` : ""),
            );
          }
        } catch (err: unknown) {
          addSystemMessage(`压缩失败：${err instanceof Error ? err.message : String(err)}`);
        }
        break;
      }
      case "status": {
        const statusLines: string[] = [];
        statusLines.push(`**运行时：** ${hostStatusText}`);
        if (hostStatus?.runtimeRunning) {
          statusLines.push(`**传输：** ${hostStatus.runtimeTransport}`);
        }
        statusLines.push(`**模型：** ${getProviderDisplayLabel(providerSettings)}`);
        statusLines.push(`**会话：** ${session ? session.title : "无"}`);
        statusLines.push(`**消息：** ${chatMessages.length}`);
        if (task) {
          statusLines.push(`**任务：** ${task.id} - ${formatStatusLabel(task.status)}`);
        }
        addSystemMessage(statusLines.join("\n"));
        break;
      }
      case "model":
        if (cmd.args) {
          setProviderSettings((current) => ({ ...current, model: cmd.args }));
          addToast("success", `模型已切换为：${cmd.args}`);
        } else {
          addSystemMessage(
            `**当前模型：** ${getProviderDisplayLabel(providerSettings)}`,
          );
        }
        break;
      case "config": {
        const configLines: string[] = [
          `**模式：** ${formatRuntimeModeLabel(providerSettings.mode)}`,
          `**基础 URL：** ${providerSettings.baseUrl || "默认"}`,
          `**模型：** ${providerSettings.model || "(默认)"}`,
          `**温度：** ${providerSettings.temperature}`,
          `**最大输出令牌：** ${providerSettings.maxTokens}`,
          `**最大上下文：** ${providerSettings.maxContextTokens}`,
          `**超时：** ${providerSettings.timeout}s`,
        ];
        addSystemMessage(configLines.join("\n"));
        break;
      }
      case "mcp": {
        if (cmd.args === "refresh") {
          addSystemMessage("正在刷新 MCP 工具...");
          handleRefreshMcpTools().then(() => {
            addSystemMessage(formatMcpSummary(mcpServers));
          });
        } else {
          addSystemMessage(formatMcpSummary(mcpServers));
        }
        break;
      }
      case "skills": {
        if (cmd.args === "refresh") {
        addSystemMessage("正在刷新技能...");
          refreshSkills();
        } else {
          addSystemMessage(formatSkillsSummary(skills));
        }
        break;
      }
    }
  }

  /** Append a system-level info message (rendered as an assistant bubble). */
  function addSystemMessage(markdown: string) {
    const now = Date.now();
    const systemMessageId = `system_${now}`;
    setChatMessages((current) => [
      ...current,
      {
        id: systemMessageId,
        sessionId: session?.id ?? "",
        taskId: "system",
        role: "assistant" as const,
        content: markdown,
        createdAt: now,
        updatedAt: now,
      },
    ]);
  }

  async function ensureSessionForSend(): Promise<SessionRecord> {
    const nextWorkspace = await ensureWorkspace();
    const result = await runtimeClient.createSession({
      workspaceId: nextWorkspace.id,
      title: sessionTitle.trim() || DEFAULT_SESSION_TITLE,
    });
    setSessions((current) => upsertRecord(current, result.session));
    setSession(result.session);
    setOpenTabs((current) => {
      const resultTabs = openSessionTab(current, result.session);
      setActiveTabId(resultTabs.activeTabId);
      return resultTabs.tabs;
    });
    return result.session;
  }

  async function handleRefreshTask() {
    if (!task) {
      return;
    }

    const taskId = task.id;
    setRefreshBusy(true);
    setError(null);

    try {
      const result = await runtimeClient.getTask(taskId);
      setTask(result.task);
      setActiveTaskId(result.task.id);
      setTaskHistory((current) => upsertRecord(current, result.task));
      await loadTraceForTask(taskId);
    } catch (reason) {
      toastError(reason);
    } finally {
      setRefreshBusy(false);
    }
  }

  async function refreshTaskControlState(taskId: string) {
    const [taskResult, taskListResult] = await Promise.all([
      runtimeClient.getTask(taskId),
      runtimeClient.listTasks(),
    ]);
    setTask(taskResult.task);
    setTaskHistory(taskListResult.tasks);
    setActiveTaskId(taskId);
    await loadTraceForTask(taskId);
  }

  async function handleTaskControl(action: TaskControlAction) {
    if (!task) {
      return;
    }

    const taskId = task.id;
    setTaskControlBusyAction(action);
    setTaskControlError(null);
    setError(null);

    try {
      if (action === "cancel") {
        await runtimeClient.cancelTask({ taskId });
      } else if (action === "pause") {
        await runtimeClient.pauseTask({ taskId });
      } else {
        await runtimeClient.resumeTask({ taskId });
      }

      await refreshTaskControlState(taskId);
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : String(reason);
      setTaskControlError(message);
      setError(message);
    } finally {
      setTaskControlBusyAction(null);
    }
  }

  function handleStopPrompt() {
    clearPendingAssistantTokens();
    setChatMessages((current) => stopStreamingMessages(current, session?.id));
    setMessageBusy(false);
    setSessionBusy(false);
    setError(null);
    setTaskControlError(null);
    if (task && isTaskControllable(task.status)) {
      void handleTaskControl("cancel");
    }
  }

  async function handleRefreshTrace() {
    if (!activeTaskId) {
      setTraceEvents([]);
      setCommandLogCacheById({});
      setTraceError(null);
      return;
    }

    await loadTraceForTask(activeTaskId);
  }

  async function handleRefreshCommandJob(commandId: string) {
    setCommandJobBusyId(commandId);
    setError(null);

    try {
      const result = await runtimeClient.commandLogGet({ commandId });
      setCommandLogCacheById((current) => ({
        ...current,
        [result.commandLog.id]: result.commandLog,
      }));
      await loadTraceForTask(result.commandLog.taskId);
    } catch (reason) {
      toastError(reason);
    } finally {
      setCommandJobBusyId((current) => (current === commandId ? null : current));
    }
  }

  async function handleStopCommandJob(commandId: string) {
    setCommandJobBusyId(commandId);
    setError(null);

    try {
      const result = await runtimeClient.commandCancel({ commandId });
      setCommandLogCacheById((current) => ({
        ...current,
        [result.commandLog.id]: result.commandLog,
      }));
      await loadTraceForTask(result.commandLog.taskId);
    } catch (reason) {
      toastError(reason);
    } finally {
      setCommandJobBusyId((current) => (current === commandId ? null : current));
    }
  }

  async function handleLoadPatchDiff(patchId: string) {
    setPatchBusyId(patchId);
    setError(null);

    try {
      const result = await runtimeClient.diffGet({ patchId });
      setPatchCacheById((current) => ({
        ...current,
        [patchId]: {
          ...result.patch,
          diffText: result.diffText || result.patch.diffText,
        },
      }));
    } catch (reason) {
      toastError(reason);
    } finally {
      setPatchBusyId((current) => (current === patchId ? null : current));
    }
  }

  async function handleApprovalSubmit(approvalId: string, decision: "approved" | "rejected") {
    setApprovalBusyId(approvalId);
    setError(null);

    try {
      await runtimeClient.approvalSubmit({
        approvalId,
        decision,
      });
      addToast("success", decision === "approved" ? "已批准" : "已拒绝");
    } catch (reason) {
      toastError(reason);
    } finally {
      setApprovalBusyId((current) => (current === approvalId ? null : current));
    }
  }

  const activeTab = openTabs.find((tabItem) => tabItem.id === activeTabId) ?? openTabs[0] ?? getInitialTabs()[0];
  const activeSessionRecord = resolveSessionForTab(activeTab, sessions, session);
  const runtimeReady = Boolean(hostStatus && config);
  const localPathActionsAvailable = runtimeClient.canOpenLocalAppPaths();
  const composerVisible = runtimeReady && (activeTab.kind === "new-session" || activeTab.kind === "session");
  const composerCanStop = isTaskControllable(task?.status);
  const composerHasStreamingMessage = visibleChatMessages.some((message) => message.streaming);
  const composerSending = messageBusy || composerCanStop || composerHasStreamingMessage;
  const queuedPromptCount = queuedPromptSubmissions.length;
  const activeSessionWorkspaceRoot = activeTab.kind === "session" ? activeSessionRecord?.workspaceRoot : undefined;
  const activeSessionWorkspaceName =
    activeTab.kind === "session"
      ? activeSessionRecord?.workspaceName ?? workspaceNameFromPath(activeSessionWorkspaceRoot)
      : undefined;
  const workspaceName =
    activeSessionWorkspaceName ?? workspace?.name ?? workspaceNameFromPath(workspacePath) ?? "yuanbao_agent";
  const providerLabel = getProviderDisplayLabel(providerSettings);
  useEffect(() => {
    if (
      loading ||
      !runtimeReady ||
      messageBusy ||
      composerCanStop ||
      composerHasStreamingMessage ||
      queuedPromptSubmissions.length === 0
    ) {
      return;
    }

    const [nextSubmission] = queuedPromptSubmissions;
    setQueuedPromptSubmissions((current) => current.slice(1));
    void sendMessageContent(nextSubmission.content, nextSubmission.attachments, { clearComposer: false, mode: "queued" });
  }, [composerCanStop, composerHasStreamingMessage, loading, messageBusy, queuedPromptSubmissions, runtimeReady]);

  const sessionContextPreview = useMemo(
    () =>
      buildSessionContextPreview({
        events,
        traceEvents,
        workspace,
        activeTaskId,
        activeTask: task,
      }),
    [activeTaskId, events, traceEvents, task, workspace],
  );
  const cwdLabel =
    sessionContextPreview?.workspaceRoot ??
    activeSessionWorkspaceRoot ??
    workspace?.rootPath ??
    workspacePath ??
    DEFAULT_WORKSPACE_PATH;
  const hostStatusText = describeMode(hostStatus);
  const overviewRuntimeStatus = runtimeReady
    ? providerSettings.mode === "mock"
      ? "degraded" as const
      : "ready" as const
    : "offline" as const;
  const runtimeStatusLabel =
    overviewRuntimeStatus === "ready"
      ? "运行时就绪"
      : overviewRuntimeStatus === "degraded"
        ? "运行时预览"
        : "运行时离线";
  const enabledMcpServers = mcpServers.filter((server) => server.enabled).length;
  const mcpStatusLabel = `${enabledMcpServers}/${mcpServers.length || 0} MCP`;
  const pendingApprovalCount = approvalCards.filter((approval) => approval.status === "pending").length;
  const approvalStatusLabel = `${pendingApprovalCount} 个审批`;
  const contextStats = sessionContextPreview?.budgetStats;
  const contextStatusLabel = contextStats?.maxContextTokens
    ? `${formatCompactCount(contextStats.estimatedTokens ?? contextStats.estimatedInputTokens)}/${formatCompactCount(contextStats.maxContextTokens)} 上下文`
    : `${formatCompactCount(contextStats?.estimatedTokens ?? contextStats?.estimatedInputTokens)} 上下文`;
  const runtimeUnavailableReason =
    !loading && !runtimeReady
      ? error ?? "运行时握手未完成。前端无法独立执行任务。"
      : null;
  const settingsProviders = useMemo<SettingsProvider[] | undefined>(() => {
    if (!config) {
      return undefined;
    }

    const providerConfig = normalizeProviderConfig(config.provider);
    return (providerConfig.profiles ?? []).map((profile) => {
      const models = [profile.model, profile.fallbackModel].filter(
        (value): value is string => Boolean(value),
      );

      return {
        id: profile.id,
        name: profile.name,
        endpoint: profile.baseUrl ?? "未配置接口地址",
        apiFormat: profile.apiFormat as SettingsProvider["apiFormat"],
        note:
          profile.mode === "mock"
            ? "本地预览；不会调用远程模型"
            : profile.apiKeyEnvVarName
              ? `环境变量：${profile.apiKeyEnvVarName}`
              : "需要 API 密钥",
        models: models.length ? models : undefined,
        modelMapping: {
          main: profile.model ?? "",
          haiku: profile.defaultModel ?? profile.model ?? "",
          sonnet: profile.model ?? profile.defaultModel ?? "",
          opus: profile.fallbackModel ?? "",
        },
        apiKeyMasked: profile.apiKey ? "已输入密钥" : profile.apiKeyEnvVarName,
        lastTest: buildSettingsProviderLastTest(
          profile,
          providerTestResult,
          providerConfig.activeProfileId,
        ),
        status:
          profile.id === providerConfig.activeProfileId
            ? "active"
            : profile.lastStatus ?? "configured",
      };
    });
  }, [config, providerTestResult]);
  const sessionTaskCount = useMemo(() => {
    if (!activeSessionRecord) {
      return undefined;
    }

    return taskHistory.filter((item) => item.sessionId === activeSessionRecord.id).length;
  }, [activeSessionRecord, taskHistory]);
  const scheduledTasks = useMemo<ScheduledTask[]>(
    () => scheduledRecords.map(scheduledRecordToWorkspaceTask),
    [scheduledRecords],
  );
  const settingsSkills = useMemo<SettingsSkillConfig[]>(
    () => skills.map(normalizeSkillForSettings),
    [skills],
  );
  const scheduledLogsByTaskId = useMemo<Record<string, ExecutionLog[]>>(() => {
    return Object.fromEntries(
      scheduledRecords.map((record) => [
        record.id,
        scheduledLogs.filter((log) => log.taskId === record.id).map(scheduledRunToExecutionLog),
      ]),
    );
  }, [scheduledLogs, scheduledRecords]);
  const sessionApprovals = useMemo(
    () =>
      approvalCards.map((approval) => ({
        id: approval.approvalId,
        title: approval.patchSummary ?? approval.command,
        kind: approval.kind,
        status: approval.status,
        summary: approval.requestSummary,
        requestedAt: approval.requestedAt,
        risk: riskToLevel(approval.risk),
        parametersPreview: approval.requestSummary,
        fullInput: approval.requestJson,
        command: approval.command,
        cwd: approval.cwd,
      })),
    [approvalCards],
  );
  const sessionPatches = useMemo(
    () =>
      patchCards.map((patch) => ({
        id: patch.patchId,
        summary: patch.summary,
        status: patch.status,
        filesChanged: patch.filesChanged,
        additions: countAddedLines(patch.diffText ?? ""),
        deletions: countDeletedLines(patch.diffText ?? ""),
        updatedAt: patch.updatedAt,
        files: parsePatchFiles(patch.diffText),
        diff: patch.diffText,
      })),
    [patchCards],
  );
  const sessionTraceItems = useMemo(
    () =>
      [...traceEvents]
        .sort((left, right) => right.sequence - left.sequence)
        .map((trace) => ({
          id: trace.id,
          type: trace.type,
          source: trace.source,
          time: trace.createdAt,
          title: trace.type,
          summary: summarizeValue(trace.payload, trace.type, 120),
          detail: summarizeValue(trace.payload, trace.type, 800),
          status: readEventText(trace.payload, "status"),
          durationMs: readEventNumber(trace.payload, "durationMs"),
          tokenCount: readEventNumber(trace.payload, "tokenCount"),
          stdout: readEventText(trace.payload, "stdout"),
          stderr: readEventText(trace.payload, "stderr"),
        })),
    [traceEvents],
  );
  const sessionToolCalls = useMemo(
    () =>
      toolTimelineItems
        .filter((toolCall) => !activeTaskId || toolCall.taskId === activeTaskId)
        .map((toolCall) => ({
          id: toolCall.id,
          toolName: toolCall.toolName,
          status: toolCall.status,
          time: toolCall.finishedAt ?? toolCall.updatedAt ?? toolCall.startedAt,
          resultSummary: toolCall.errorSummary ?? toolCall.resultSummary,
          durationMs: toolCall.durationMs,
          argsPreview: toolCall.argsSummary,
          input: toolCall.argsSummary,
          output: toolCall.resultSummary,
          rawInput: toolCall.argsRaw,
          rawOutput: toolCall.resultRaw,
          stderr: toolCall.errorSummary,
        })),
    [activeTaskId, toolTimelineItems],
  );
  const sessionCollaboration = useMemo(
    () => buildSessionCollaboration(events, traceEvents),
    [events, traceEvents],
  );
  const composerRuntimeChildTasks: ComposerRuntimeChildTask[] = useMemo(
    () =>
      (sessionCollaboration.childTasks ?? []).map((childTask) => ({
        id: childTask.id,
        title: childTask.title,
        status: childTask.status,
        workerName: childTask.workerName,
        summary: childTask.summary,
        updatedAt: childTask.updatedAt,
      })),
    [sessionCollaboration.childTasks],
  );
  const sessionBackgroundJobs = useMemo(
    () => {
      const eventJobs = buildSessionBackgroundJobs(events, traceEvents);
      const commandLogs = Object.values(commandLogCacheById).filter(
        (log) => !activeTaskId || log.taskId === activeTaskId,
      );
      return mergeSessionBackgroundJobs(eventJobs, commandLogs);
    },
    [activeTaskId, commandLogCacheById, events, traceEvents],
  );

  function handleSelectScheduledTask(taskId: string) {
    setSelectedScheduledTaskId(taskId);
  }

  async function handleCreateScheduledTask(draft?: ScheduledTaskDraft) {
    if (!draft) {
      return;
    }

    setScheduledCreateBusy(true);
    setError(null);

    try {
      const prompt = draft.description ? `${draft.description}\n\n${draft.prompt}` : draft.prompt;
      const result = await runtimeClient.createScheduledTask({
        name: draft.name,
        prompt,
        schedule: draft.schedule,
        enabled: draft.enabled,
      });
      await refreshScheduledRecords(result.task.id);
      setSelectedScheduledTaskId(result.task.id);
      addToast("success", "定时任务已创建");
    } catch (reason) {
      toastError(reason);
    } finally {
      setScheduledCreateBusy(false);
    }
  }

  const workspaceContent = (() => {
    if (!runtimeReady && !loading) {
      return <RuntimeUnavailableWorkspace errorMessage={runtimeUnavailableReason ?? "Runtime unavailable."} />;
    }

    if (activeTab.kind === "overview") {
      return (
        <WorkbenchOverviewPage
          workspace={workspace}
          workspacePath={workspacePath}
          providerLabel={providerLabel}
          runtimeStatus={overviewRuntimeStatus}
          sessions={sessions}
          tasks={taskHistory}
          scheduledTasks={scheduledRecords}
          mcpServers={mcpServers}
          skills={skills}
          onOpenNewSession={() => handleOpenSystemTab("new-session")}
          onOpenSession={handleOpenSessionTab}
          onOpenScheduled={() => handleOpenSystemTab("scheduled")}
          onOpenMcp={() => handleOpenSystemTab("mcp")}
          onOpenSettings={() => handleOpenSystemTab("settings")}
        />
      );
    }

    if (activeTab.kind === "new-session") {
      return (
        <NewSessionWorkspace
          workspacePath={workspacePath}
          hostStatusText={hostStatusText}
          sessionTitle={sessionTitle}
          modelLabel={providerLabel}
          modelOptions={(settingsProviders ?? []).map((provider) => ({
            id: provider.id,
            label: provider.models?.[0] ?? provider.name,
            subtitle: provider.models?.[0] && provider.name !== provider.models[0] ? provider.name : undefined,
          }))}
          selectedModelId={activeProviderProfileId}
          workspaceBusy={workspaceBusy}
          sessionBusy={sessionBusy}
          onSelectModel={selectProviderProfile}
          onSessionTitleChange={setSessionTitle}
          onWorkspacePathChange={setWorkspacePath}
          onOpenWorkspace={handleOpenWorkspace}
          onCreateSession={handleCreateSession}
        />
      );
    }

    if (activeTab.kind === "session") {
      return (
        <SessionWorkspace
          session={activeSessionRecord}
          activeTask={
            task
              ? {
                  id: task.id,
                  status: task.status,
                  goal: task.goal,
                  createdAt: task.createdAt,
                  updatedAt: task.updatedAt,
                  acceptanceCriteria: task.acceptanceCriteria,
                  outOfScope: task.outOfScope,
                  currentStep: task.currentStep,
                  changedFiles: task.changedFiles,
                  commands: task.commands,
                  verification: task.verification,
                  summary: task.summary,
                  resultSummary: task.resultSummary,
                  planSteps: task.plan?.map((step) => ({
                    id: step.id,
                    title: step.title,
                    status: step.status,
                    detail: step.detail,
                  })),
                }
              : null
          }
          messages={visibleChatMessages}
          messagesLoading={sessionBusy}
          taskCount={sessionTaskCount}
          collaboration={sessionCollaboration}
          backgroundJobs={sessionBackgroundJobs}
          approvals={sessionApprovals}
          patches={sessionPatches}
          traces={sessionTraceItems}
          toolCalls={sessionToolCalls}
          contextPreview={sessionContextPreview}
          composerContext={{
            cwd: cwdLabel,
            repo: workspaceName,
            model: providerLabel,
            permissionMode: approvalModeToSettingsMode(config?.policy.approvalMode),
          }}
          onApprove={(approvalId) => handleApprovalSubmit(approvalId, "approved")}
          onApproveForSession={(approvalId) => handleApprovalSubmit(approvalId, "approved")}
          onReject={(approvalId) => handleApprovalSubmit(approvalId, "rejected")}
          onLoadPatch={handleLoadPatchDiff}
          onCopyPatchPath={(_patchId, path) => {
            void handleCopyRuntimeText("补丁路径", path);
          }}
          onCopyRuntimeText={handleCopyRuntimeText}
          onRefreshCommandJob={handleRefreshCommandJob}
          onStopCommandJob={handleStopCommandJob}
          onRefreshTask={handleRefreshTask}
          onStopTask={() => handleTaskControl("cancel")}
          onRefreshTrace={handleRefreshTrace}
          taskBusyAction={refreshBusy ? "refresh" : taskControlBusyAction === "cancel" ? "stop" : null}
          busyId={approvalBusyId ?? patchBusyId ?? commandJobBusyId ?? (traceBusy ? "trace" : null)}
        />
      );
    }

    if (activeTab.kind === "scheduled") {
      return (
        <ScheduledWorkspace
          tasks={scheduledTasks}
          logsByTaskId={scheduledLogsByTaskId}
          selectedTaskId={selectedScheduledTaskId ?? undefined}
          onSelectTask={handleSelectScheduledTask}
          onCreateTask={handleCreateScheduledTask}
          onRunTask={handleRunScheduledTask}
          onToggleTask={handleToggleScheduledTask}
          busyTaskId={scheduledBusyTaskId}
          createBusy={scheduledCreateBusy}
          workspacePath={cwdLabel}
        />
      );
    }

    if (activeTab.kind === "mcp") {
      return (
        <McpWorkspace
          servers={mcpServers}
          loading={mcpLoading}
          busyServerId={mcpBusyServerId}
          lastRefresh={mcpLastRefresh}
          errorMessage={mcpError}
          onRefreshServers={refreshMcpServers}
          onCreateServer={handleCreateMcpServer}
          onImportServers={handleImportMcpServers}
          onUpdateServer={handleUpdateMcpServer}
          onToggleServer={handleToggleMcpServer}
          onRefreshTools={handleRefreshMcpTools}
          onDeleteServer={handleDeleteMcpServer}
          onDismissError={() => setMcpError(null)}
        />
      );
    }

    if (activeTab.kind === "skills") {
      return (
        <SkillsWorkspace
          skills={settingsSkills}
          mcpServers={mcpServers}
          mcpToolCount={mcpLastRefresh?.tools.length ?? 0}
          providerLabel={providerLabel}
          busySkillId={skillBusyId}
          onRefreshSkills={refreshSkills}
          onOpenMcp={() => handleOpenSystemTab("mcp")}
          onOpenSettings={() => handleOpenSystemTab("settings")}
          onCreateSkill={handleCreateSkill}
          onUpdateSkill={handleUpdateSkill}
          onDeleteSkill={handleDeleteSkill}
          onImportSkills={handleImportSkills}
        />
      );
    }

    if (activeTab.kind === "appearance") {
      return (
        <AppearanceWorkspace
          value={generalSettings}
          workspaceName={workspaceName}
          providerLabel={providerLabel}
          onChange={handleGeneralSettingsChange}
          onOpenSettings={() => handleOpenSystemTab("settings")}
        />
      );
    }

    if (activeTab.kind === "playground") {
      return (
        <ComponentPlaygroundWorkspace
          onOpenAppearance={() => handleOpenSystemTab("appearance")}
          onOpenSkills={() => handleOpenSystemTab("skills")}
        />
      );
    }

    return (
      <SettingsWorkspace
        providers={settingsProviders}
        activeProviderId={config?.provider.activeProfileId}
        onSelectProvider={selectProviderProfile}
        onAddProvider={handleAddProviderFromSettings}
        onEditProvider={handleEditProviderFromSettings}
        onTestProvider={handleTestSelectedProvider}
        onTestProviderConfig={handleTestProviderConfigFromSettings}
        onSaveProvider={handleSaveProviderConfig}
        providerBusy={providerConfigBusy}
        providerTestBusy={providerTestBusy}
        providerFeedback={providerFeedback}
        permissionMode={approvalModeToSettingsMode(config?.policy.approvalMode)}
        onPermissionModeChange={handlePermissionModeChange}
        general={generalSettings}
        onGeneralChange={handleGeneralSettingsChange}
        im={imSettings}
        onIMChange={setIMSettings}
        skills={settingsSkills}
        onRefreshSkills={refreshSkills}
        computerUse={computerUseSettings}
        onComputerUseChange={setComputerUseSettings}
        onRecheckComputerUse={handleRecheckComputerUse}
        workspaceFocus={workspace?.focus}
        workspaceFocusBusy={workspaceFocusBusy}
        onSaveWorkspaceFocus={workspace ? handleSaveWorkspaceFocus : undefined}
        workspaceMemorySummary={workspace?.summary}
        workspaceMemoryBusy={workspaceMemoryBusy}
        onClearWorkspaceMemory={workspace ? handleClearWorkspaceMemory : undefined}
        about={{
          version: "0.1.0",
          runtime: hostStatus?.runtimeTransport ?? "mock-browser",
          dataPath: workspacePath,
          build: hostStatus?.runtimeRunning ? "runtime running" : "runtime idle",
        }}
        onOpenLogs={localPathActionsAvailable ? () => void handleOpenAppPath("logs") : undefined}
        onOpenDataDirectory={localPathActionsAvailable ? () => void handleOpenAppPath("data") : undefined}
      />
    );
  })();

  return (
    <AppShell
      tabs={openTabs}
      activeTabId={activeTabId}
      sessions={sessions}
      activeSessionId={getSidebarActiveSessionId(activeTab)}
      workspaceName={workspaceName}
      composerVisible={composerVisible}
      promptValue={prompt}
      onPromptChange={setPrompt}
      onOpenSystemTab={handleOpenSystemTab}
      onOpenSessionTab={handleOpenSessionTab}
      onActivateTab={handleActivateTab}
      onCloseTab={handleCloseTab}
      onCloseOtherTabs={handleCloseOtherTabs}
      onRenameSession={handleRenameSession}
      onDeleteSession={handleDeleteSession}
      onSubmitPrompt={handleSendMessage}
      onQueuePrompt={handleQueuePrompt}
      onStopPrompt={handleStopPrompt}
      disabled={loading || !runtimeReady}
      sending={composerSending}
      submitting={messageBusy}
      queuedPromptCount={queuedPromptCount}
      runtimeChildTasks={composerRuntimeChildTasks}
      attachments={promptAttachments}
      onAttachmentsChange={setPromptAttachments}
      onAttachmentError={toastError}
      modelOptions={(settingsProviders ?? []).map((provider) => ({
        id: provider.id,
        label: provider.models?.[0] ?? provider.name,
        subtitle: provider.models?.[0] && provider.name !== provider.models[0] ? provider.name : undefined,
      }))}
      selectedModelId={activeProviderProfileId}
      onSelectModel={selectProviderProfile}
      loading={loading}
      providerLabel={providerLabel}
      cwdLabel={cwdLabel}
      runtimeLabel={runtimeStatusLabel}
      mcpLabel={mcpStatusLabel}
      approvalLabel={approvalStatusLabel}
      contextLabel={contextStatusLabel}
      theme={generalSettings.theme}
      density={generalSettings.density}
      radius={generalSettings.radius}
      motion={generalSettings.motion}
      accentColor={generalSettings.accentColor}
      transparency={generalSettings.transparency}
      fontScale={generalSettings.fontScale}
    >
      {error ? (
        <div className="error-banner compact" role="alert">
          <span>{error}</span>
          <button type="button" className="error-banner-dismiss" aria-label="关闭错误" onClick={() => setError(null)}>×</button>
        </div>
      ) : null}
      {workspaceContent}
      <ToastContainer toasts={toasts} onDismiss={dismissToast} />
    </AppShell>
  );
}
