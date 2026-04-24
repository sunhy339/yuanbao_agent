import { useEffect, useMemo, useState } from "react";
import type {
  ApprovalRequestedPayload,
  ApprovalResolvedPayload,
  AgentEventEnvelope,
  AppConfig,
  AssistantTokenPayload,
  CommandLifecyclePayload,
  CommandLogRecord,
  CommandOutputPayload,
  PatchRecord,
  PatchProposedPayload,
  PlanStep,
  ProviderMode,
  ProviderProfile,
  ProviderTestResult,
  ScheduledTaskRecord,
  ScheduledTaskRunRecord,
  SessionRecord,
  TaskRecord,
  TaskUpdatedPayload,
  ToolRuntimeConfig,
  ToolLifecyclePayload,
  TraceEventRecord,
  WorkspaceRef,
} from "@shared";
import { RuntimeClient, type HostStatus, type RuntimeConfig } from "./lib/runtimeClient";
import {
  DEFAULT_PROMPT,
  DEFAULT_SESSION_TITLE,
  DEFAULT_WORKSPACE_PATH,
} from "./state/mockData";
import { AppShell } from "./ui/workbench/AppShell";
import { getSidebarActiveSessionId, resolveSessionForTab } from "./ui/workbench/sessionRouting";
import {
  closeTab,
  getInitialTabs,
  openSessionTab,
  openSystemTab,
} from "./ui/workbench/tabModel";
import type { SystemWorkspaceKind, WorkbenchTab, WorkbenchSession } from "./ui/workbench/types";
import { NewSessionWorkspace } from "./ui/workbench/workspaces/NewSessionWorkspace";
import {
  ScheduledWorkspace,
  type ExecutionLog,
  type ScheduledTask,
} from "./ui/workbench/workspaces/scheduled/ScheduledWorkspace";
import {
  SessionWorkspace,
  type SessionWorkspaceBackgroundJob,
  type SessionWorkspaceCollaboration,
} from "./ui/workbench/workspaces/session/SessionWorkspace";
import {
  SettingsWorkspace,
  type SettingsComputerUseConfig,
  type SettingsGeneralConfig,
  type SettingsIMConfig,
  type SettingsProvider,
  type SettingsProviderFeedback,
  type SettingsProviderPayload,
  type SettingsProviderTestResult,
} from "./ui/workbench/workspaces/settings/SettingsWorkspace";

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

interface TaskControlButtonView {
  action: TaskControlAction;
  label: string;
  busyLabel: string;
  tone: "neutral" | "warn";
}

const TASK_CONTROL_BUTTONS: Record<TaskControlAction, TaskControlButtonView> = {
  cancel: {
    action: "cancel",
    label: "Cancel",
    busyLabel: "Cancelling...",
    tone: "warn",
  },
  pause: {
    action: "pause",
    label: "Pause",
    busyLabel: "Pausing...",
    tone: "neutral",
  },
  resume: {
    action: "resume",
    label: "Resume",
    busyLabel: "Resuming...",
    tone: "neutral",
  },
};

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

type ProviderStatusBadge = "mock" | "configured" | "missing env" | "failed" | "ok";

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

function getTaskControlActions(status?: TaskRecord["status"]): TaskControlButtonView[] {
  if (status === "running" || status === "waiting_approval") {
    return [TASK_CONTROL_BUTTONS.cancel, TASK_CONTROL_BUTTONS.pause];
  }

  if (status === "paused") {
    return [TASK_CONTROL_BUTTONS.resume, TASK_CONTROL_BUTTONS.cancel];
  }

  return [];
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
      ? { label: "mock", badgeClass: "info" }
      : { label: "configured", badgeClass: "neutral" };
  }

  if (result.status === "ok") {
    return { label: "ok", badgeClass: "ok" };
  }
  if (result.status === "mocked") {
    return { label: "mock", badgeClass: "info" };
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
    return "Current provider is mock mode. Tasks sent now use deterministic local behavior, not a real model.";
  }
  if (result?.status === "missing_env" || result?.status === "not_configured") {
    return `Current provider cannot reach a real model because ${envVarName} is missing. Set the env var and test again before sending real-model tasks.`;
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
    "No health check has been recorded for this profile yet.";

  return {
    checkedAtText: formatTimestamp(result?.lastCheckedAt ?? profile?.lastCheckedAt),
    statusText: status ?? "not recorded",
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
    name: payload.name.trim() || existingProfile?.name || "Provider profile",
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
    language,
    reasoningEffort: ui.reasoningEffort ?? "max",
    webFetchPreflight: ui.webFetchPreflight ?? true,
  };
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

interface ChatMessageView {
  id: string;
  taskId: string;
  role: "user" | "assistant";
  content: string;
  createdAt: number;
  updatedAt: number;
  streaming?: boolean;
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
  startedAt: number;
  updatedAt: number;
  finishedAt?: number;
  durationMs?: number;
  eventCount: number;
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

interface ActivityTimelineItem {
  id: string;
  type: string;
  category: "task" | "tool" | "approval" | "patch" | "command" | "assistant" | "event";
  taskId: string;
  time: string;
  status?: string;
  title: string;
  summary: string;
  relatedId?: string;
  raw: string;
}

interface TracePanelItem {
  id: string;
  type: string;
  source: string;
  relatedId: string;
  time: string;
  payloadSummary: string;
  sequence: number;
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
    resultSummary: payload.detail ?? payload.resultSummary ?? current.resultSummary,
    errorCode: payload.errorCode ?? current.errorCode,
    updatedAt: event.ts,
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
  if (status === "waiting_approval") {
    return "warn";
  }
  return "neutral";
}

function appendAssistantToken(current: ChatMessageView[], event: AgentEventEnvelope): ChatMessageView[] {
  const payload = event.payload as AssistantTokenPayload;
  const delta = payload.delta ?? "";
  if (!delta) {
    return current;
  }

  const next = [...current];
  const lastAssistantIndex = (() => {
    for (let index = next.length - 1; index >= 0; index -= 1) {
      const item = next[index];
      if (item.taskId === event.taskId && item.role === "assistant" && item.streaming) {
        return index;
      }
    }
    return -1;
  })();

  if (lastAssistantIndex >= 0) {
    const currentMessage = next[lastAssistantIndex];
    next[lastAssistantIndex] = {
      ...currentMessage,
      content: `${currentMessage.content}${delta}`,
      updatedAt: event.ts,
    };
    return next;
  }

  return [
    ...next,
    {
      id: `assistant_${event.eventId}`,
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
  const next = [...current];
  const lastAssistantIndex = (() => {
    for (let index = next.length - 1; index >= 0; index -= 1) {
      const item = next[index];
      if (item.taskId === event.taskId && item.role === "assistant") {
        return index;
      }
    }
    return -1;
  })();

  if (lastAssistantIndex >= 0) {
    const currentMessage = next[lastAssistantIndex];
    next[lastAssistantIndex] = {
      ...currentMessage,
      content: completedContent || currentMessage.content,
      updatedAt: event.ts,
      streaming: false,
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
      taskId: event.taskId,
      role: "assistant",
      content: completedContent,
      createdAt: event.ts,
      updatedAt: event.ts,
      streaming: false,
    },
  ];
}

function buildActivityTitle(event: AgentEventEnvelope): string {
  if (event.type.startsWith("task.")) {
    return event.type.replace("task.", "task ");
  }
  if (event.type.startsWith("tool.")) {
    const payload = event.payload as Partial<ToolLifecyclePayload>;
    return payload.toolName ? `${payload.toolName} ${event.type.replace("tool.", "")}` : event.type;
  }
  if (event.type.startsWith("command.")) {
    const command = readEventText(event.payload, "command");
    return command ? `${command} ${event.type.replace("command.", "")}` : event.type;
  }
  if (event.type.startsWith("approval.")) {
    return event.type.replace("approval.", "approval ");
  }
  if (event.type.startsWith("patch.")) {
    return event.type.replace("patch.", "patch ");
  }
  if (event.type.startsWith("assistant.")) {
    return event.type.replace("assistant.", "assistant ");
  }
  return event.type;
}

function buildActivityItem(event: AgentEventEnvelope): ActivityTimelineItem {
  const category = event.type.startsWith("task.")
    ? "task"
    : event.type.startsWith("tool.")
      ? "tool"
      : event.type.startsWith("approval.")
        ? "approval"
        : event.type.startsWith("patch.")
          ? "patch"
          : event.type.startsWith("command.")
            ? "command"
            : event.type.startsWith("assistant.")
              ? "assistant"
              : "event";
  const status =
    readEventText(event.payload, "status") ??
    readEventText(event.payload, "decision") ??
    event.type.split(".")[1];
  const relatedId = summarizeValue(
    getPayloadValue(event.payload, [
      "toolCallId",
      "commandId",
      "approvalId",
      "patchId",
      "messageId",
    ]),
    "",
    80,
  );

  return {
    id: event.eventId,
    type: event.type,
    category,
    taskId: event.taskId,
    time: formatTimestamp(event.ts),
    status,
    title: buildActivityTitle(event),
    summary: summarizeEvent(event),
    relatedId: relatedId || undefined,
    raw: JSON.stringify(event.payload, null, 2),
  };
}

function buildTracePanelItem(trace: TraceEventRecord): TracePanelItem {
  return {
    id: trace.id,
    type: trace.type,
    source: trace.source,
    relatedId: trace.relatedId ?? "none",
    time: formatTimestamp(trace.createdAt),
    payloadSummary: summarizeValue(trace.payload, "empty payload", 240),
    sequence: trace.sequence,
  };
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
          ? "Command is still running."
          : status === "completed"
            ? `Command completed${typeof readRecordNumber(payload, "exitCode") === "number" ? ` with exit ${readRecordNumber(payload, "exitCode")}` : "."}`
            : `Command ${status}${typeof readRecordNumber(payload, "exitCode") === "number" ? ` with exit ${readRecordNumber(payload, "exitCode")}` : "."}`,
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
        ? "Command is still running."
        : log.status === "completed"
          ? `Command completed${typeof log.exitCode === "number" ? ` with exit ${log.exitCode}` : "."}`
          : `Command ${log.status}${typeof log.exitCode === "number" ? ` with exit ${log.exitCode}` : "."}`,
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

function summarizeEvent(event: AgentEventEnvelope): string {
  if (event.type === "assistant.token") {
    return ((event.payload as AssistantTokenPayload).delta ?? "").trim() || "Model is streaming output.";
  }

  if (event.type === "assistant.message.completed") {
    return "Assistant message completed.";
  }

  if (event.type === "command.output") {
    const payload = event.payload as CommandOutputPayload;
    return `${payload.stream}: ${payload.chunk.trim()}`.trim();
  }

  if (
    event.type === "command.started" ||
    event.type === "command.completed" ||
    event.type === "command.failed"
  ) {
    const payload = event.payload as Partial<CommandLifecyclePayload>;
    const command = payload.command ?? "command";
    const status = payload.status ?? event.type.replace("command.", "");
    const exit = typeof payload.exitCode === "number" ? ` | exit ${payload.exitCode}` : "";
    return `${command} ${status}${exit}`;
  }

  if (event.type === "approval.requested") {
    const payload = event.payload as ApprovalRequestedPayload;
    const request = payload.request as Record<string, unknown>;
    if (payload.kind === "apply_patch") {
      const patchId = readRequestPatchId(request);
      return patchId ? `Patch approval requested for ${patchId}` : "Patch approval requested";
    }
    return `Approval requested for ${readRequestText(request, "command", "command")} in ${readRequestText(request, "cwd", readRequestText(request, "workspaceRoot", "."))}`;
  }

  if (event.type === "approval.resolved") {
    const payload = event.payload as ApprovalResolvedPayload;
    return `Approval ${payload.decision}`;
  }

  if (event.type === "patch.proposed") {
    const payload = event.payload as PatchProposedPayload;
    return `${payload.summary} | ${payload.filesChanged} file(s) changed`;
  }

  if (event.type.startsWith("task.")) {
    const payload = event.payload as Partial<TaskUpdatedPayload>;
    return payload.detail ?? `Task status changed to ${payload.status ?? "unknown"}`;
  }

  if (
    event.type === "tool.started" ||
    event.type === "tool.completed" ||
    event.type === "tool.failed"
  ) {
    const payload = event.payload as Partial<ToolLifecyclePayload>;
    const result = summarizeValue(
      getPayloadValue(event.payload, ["result", "output", "content", "summary"]),
      "",
      120,
    );
    const error = summarizeValue(getPayloadValue(event.payload, ["error", "errorJson"]), "", 120);
    const detail = error || result;
    return payload.toolName
      ? `${payload.toolName} ${event.type.replace("tool.", "")}${detail ? ` | ${detail}` : ""}`
      : "Tool lifecycle event";
  }

  try {
    return JSON.stringify(event.payload);
  } catch {
    return "Unable to serialize event payload.";
  }
}

function describeMode(hostStatus: HostStatus | null): string {
  if (!hostStatus) {
    return "Detecting runtime";
  }

  return hostStatus.runtimeRunning ? "Connected to local runtime" : "Browser / Mock mode";
}

function RuntimeUnavailableWorkspace({ errorMessage }: { errorMessage: string }) {
  return (
    <main className="runtime-unavailable-workspace" aria-labelledby="runtime-unavailable-title">
      <section className="runtime-unavailable-card">
        <p className="runtime-unavailable-kicker">Runtime required</p>
        <h1 id="runtime-unavailable-title">Desktop runtime unavailable</h1>
        <p className="runtime-unavailable-copy">
          This UI is not connected to the Tauri desktop runtime, so provider setup, chat, tool calls,
          and task execution are blocked.
        </p>
        <div className="runtime-unavailable-actions">
          <div>
            <strong>Run the real desktop app</strong>
            <code>npm run tauri:dev</code>
          </div>
          <div>
            <strong>Preview-only browser mode</strong>
            <code>VITE_YUANBAO_ENABLE_BROWSER_MOCK=1 npm run dev</code>
          </div>
        </div>
        <div className="runtime-unavailable-detail" role="status">
          <strong>Current failure</strong>
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
  const [assistantOutput, setAssistantOutput] = useState("");
  const [chatMessages, setChatMessages] = useState<ChatMessageView[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [workspaceBusy, setWorkspaceBusy] = useState(false);
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
  const [generalSettings, setGeneralSettings] = useState<SettingsGeneralConfig>({
    theme: "light",
    language: "zh",
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
    status: "未检查",
  });
  const [openTabs, setOpenTabs] = useState<WorkbenchTab[]>(() => getInitialTabs());
  const [activeTabId, setActiveTabId] = useState<WorkbenchTab["id"]>("system:new-session");

  useEffect(() => {
    let disposed = false;

    Promise.all([
      runtimeClient.getHostStatus(),
      runtimeClient.getConfig(),
      runtimeClient.listSessions(),
      runtimeClient.listTasks(),
      runtimeClient.listScheduledTasks(),
    ])
      .then(([nextHostStatus, nextConfig, nextSessions, nextTasks, nextScheduledTasks]) => {
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
        setScheduledRecords(nextScheduledTasks.tasks);
        setSelectedScheduledTaskId(nextScheduledTasks.tasks[0]?.id ?? null);

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
          setError(reason instanceof Error ? reason.message : String(reason));
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

        setEvents((current) => [...current, event].slice(-80));
        setTask((current) => applyEventToTask(current, event));
        setTaskHistory((current) => {
          const existing = current.find((item) => item.id === event.taskId);
          if (!existing) {
            return current;
          }

          const updated = applyEventToTask(existing, event);
          return updated ? upsertRecord(current, updated) : current;
        });

        if (event.type.startsWith("task.")) {
          setSession((current) =>
            current && current.id === event.sessionId
              ? { ...current, updatedAt: event.ts }
              : current,
          );
          setSessions((current) =>
            current.map((item) => (item.id === event.sessionId ? { ...item, updatedAt: event.ts } : item)),
          );
        }

        if (event.type === "assistant.token") {
          const payload = event.payload as AssistantTokenPayload;
          setAssistantOutput((current) => `${current}${payload.delta}`);
          setChatMessages((current) => appendAssistantToken(current, event));
        }

        if (event.type === "assistant.message.completed") {
          setChatMessages((current) => completeAssistantMessage(current, event));
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

  const eventItems = useMemo(
    () => [...events].reverse().map((event) => buildActivityItem(event)),
    [events],
  );
  const traceItems = useMemo(
    () =>
      [...traceEvents]
        .sort((left, right) => right.sequence - left.sequence)
        .map((trace) => buildTracePanelItem(trace)),
    [traceEvents],
  );
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
      const durationMs =
        readEventNumber(event.payload, "durationMs") ??
        (current ? event.ts - current.startedAt : undefined);
      const resultSummary = summarizeValue(
        getPayloadValue(event.payload, ["result", "output", "content", "summary"]),
        current?.resultSummary ?? "waiting for result",
      );
      const errorSummary = summarizeValue(
        getPayloadValue(event.payload, ["error", "errorJson", "message"]),
        "",
      );

      items.set(toolCallId, {
        id: toolCallId,
        taskId: event.taskId,
        toolCallId,
        toolName: payload.toolName ?? current?.toolName ?? "unknown_tool",
        status,
        argsSummary: summarizeValue(
          payload.arguments ?? getPayloadValue(event.payload, ["args", "input", "parameters"]),
          current?.argsSummary ?? "not recorded",
        ),
        resultSummary,
        errorSummary: errorSummary || current?.errorSummary,
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

  const patchCardById = useMemo(() => {
    return new Map(patchCards.map((patch) => [patch.patchId, patch]));
  }, [patchCards]);

  const commandOutputByTaskId = useMemo(() => {
    const outputs = new Map<string, string[]>();

    for (const event of events) {
      if (event.type !== "command.output") {
        continue;
      }

      const payload = event.payload as CommandOutputPayload;
      const chunk = payload.chunk.trim();
      const summary = chunk ? `${payload.stream}: ${chunk}` : `${payload.stream}: output event`;
      const next = outputs.get(event.taskId) ?? [];
      outputs.set(event.taskId, [...next, summary].slice(-3));
    }

    return outputs;
  }, [events]);

  const planSteps = useMemo<Array<PlanStep>>(() => task?.plan ?? [], [task]);

  const visibleTaskHistory = useMemo(() => {
    const scopedTasks = session ? taskHistory.filter((item) => item.sessionId === session.id) : taskHistory;
    return sortByUpdatedAtDesc(scopedTasks);
  }, [taskHistory, session]);

  const visibleChatMessages = useMemo(
    () =>
      chatMessages.filter(
        (message) => message.taskId === activeTaskId || message.taskId === "pending",
      ),
    [activeTaskId, chatMessages],
  );
  const taskControlActions = useMemo(() => getTaskControlActions(task?.status), [task?.status]);

  async function ensureWorkspace(): Promise<WorkspaceRef> {
    if (workspace) {
      return workspace;
    }

    const result = await runtimeClient.openWorkspace(workspacePath.trim());
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
    setSession(nextSession);
    setActiveTaskId(null);
    setTask(null);
    setEvents([]);
    setTraceEvents([]);
    setCommandLogCacheById({});
    setTraceError(null);
    setPatchCacheById({});
    setPatchBusyId(null);
    setAssistantOutput("");
    setChatMessages([]);
    setApprovalBusyId(null);

    if (!nextSession) {
      return;
    }

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
      }
      return result.tabs;
    });
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
    const profileName = providerSettings.name.trim() || "Provider profile";

    if (!model) {
      throw new Error("Provider model is required.");
    }
    if (providerSettings.mode === "openai-compatible" && !baseUrl) {
      throw new Error("Base URL is required for OpenAI-compatible mode.");
    }

    const temperature = parseProviderNumber(providerSettings.temperature, "Temperature", {
      min: 0,
      max: 2,
    });
    const maxTokens = parseProviderNumber(providerSettings.maxTokens, "Max tokens", {
      integer: true,
      min: 1,
    });
    const maxContextTokens = parseProviderNumber(providerSettings.maxContextTokens, "Max context tokens", {
      integer: true,
      min: 1,
    });
    const timeout = parseProviderNumber(providerSettings.timeout, "Timeout", {
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
      title: "Saved and activated",
      message: `${activeProfile?.name ?? "Provider"} is now the active provider.`,
      detail: `Model: ${activeProfile?.model ?? provider.model ?? DEFAULT_PROVIDER_MODEL}`,
    });
  }

  function showProviderTestFeedback(result: ProviderTestResult, profileId: string) {
    setProviderFeedback({
      providerId: result.profileId ?? profileId,
      tone: result.ok ? "success" : "danger",
      title: result.ok ? "Test passed" : "Test failed",
      message: result.ok
        ? `Runtime can reach ${result.model ?? DEFAULT_PROVIDER_MODEL}.`
        : result.lastErrorSummary ?? result.message,
      detail: result.ok
        ? result.lastStatus ?? result.status
        : result.checkedEnvVarName
          ? `Check env var: ${result.checkedEnvVarName}`
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
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
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
      setAssistantOutput("");
      setChatMessages([]);
      await refreshSessionHistory();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setWorkspaceBusy(false);
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
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setSessionBusy(false);
    }
  }

  async function handleSaveSearchConfig() {
    setSearchConfigBusy(true);
    setError(null);

    try {
      await persistSearchConfig();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
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
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setProviderConfigBusy(false);
    }
  }

  async function handleSaveCommandPolicyConfig() {
    setCommandPolicyBusy(true);
    setError(null);

    try {
      await persistCommandPolicyConfig();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
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
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }

  async function handleTestSelectedProvider(profileId?: string) {
    setError(null);

    try {
      await runProviderTest(undefined, profileId ?? activeProviderProfileId);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }

  async function handleTestProviderConfigFromSettings(payload: SettingsProviderPayload) {
    if (!config) {
      return;
    }

    setProviderTestBusy(true);
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
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setProviderTestBusy(false);
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
      setError(reason instanceof Error ? reason.message : String(reason));
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
      setError(reason instanceof Error ? reason.message : String(reason));
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
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
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
            reasoningEffort: next.reasoningEffort,
            webFetchPreflight: next.webFetchPreflight,
          },
        },
      });
      const normalized = normalizeRuntimeConfig(result.config);
      setConfig(normalized);
      setGeneralSettings(buildSettingsGeneralConfig(normalized));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
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
      setError(reason instanceof Error ? reason.message : String(reason));
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
      setError(reason instanceof Error ? reason.message : String(reason));
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
        name: `Profile ${(config.provider.profiles?.length ?? 0) + 1}`,
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
      setError(reason instanceof Error ? reason.message : String(reason));
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
        name: `${source.name || "Provider profile"} Copy`,
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
      setError(reason instanceof Error ? reason.message : String(reason));
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
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setProviderConfigBusy(false);
    }
  }

  async function handleSendMessage() {
    if (!prompt.trim()) {
      setError("Enter a task description before sending.");
      return;
    }

    setMessageBusy(true);
    setError(null);

    try {
      await persistSearchConfig();
      const activeSession =
        activeTab.kind === "session"
          ? activeSessionRecord ?? (await ensureSessionForSend())
          : await ensureSessionForSend();
      const messageContent = prompt.trim();
      const pendingUserMessageId = `user_${Date.now()}`;
      setEvents([]);
      setTraceEvents([]);
      setCommandLogCacheById({});
      setTraceError(null);
      setPatchCacheById({});
      setPatchBusyId(null);
      setAssistantOutput("");
      setChatMessages([
        {
          id: pendingUserMessageId,
          taskId: "pending",
          role: "user",
          content: messageContent,
          createdAt: Date.now(),
          updatedAt: Date.now(),
        },
      ]);
      setApprovalBusyId(null);

      const result = await runtimeClient.sendMessage({
        sessionId: activeSession.id,
        content: messageContent,
        attachments: [],
      });

      setChatMessages((current) =>
        current.map((message) =>
          message.id === pendingUserMessageId ? { ...message, taskId: result.task.id } : message,
        ),
      );
      setTaskHistory((current) => upsertRecord(current, result.task));
      setTask(result.task);
      setActiveTaskId(result.task.id);
      const touchedSession = { ...activeSession, updatedAt: result.task.updatedAt };
      setSessions((current) => upsertRecord(current, touchedSession));
      setSession(touchedSession);
      setOpenTabs((current) => {
        const resultTabs = openSessionTab(current, touchedSession);
        setActiveTabId(resultTabs.activeTabId);
        return resultTabs.tabs;
      });
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setMessageBusy(false);
    }
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
      setError(reason instanceof Error ? reason.message : String(reason));
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
      setError(reason instanceof Error ? reason.message : String(reason));
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
      setError(reason instanceof Error ? reason.message : String(reason));
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
      setError(reason instanceof Error ? reason.message : String(reason));
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
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setApprovalBusyId((current) => (current === approvalId ? null : current));
    }
  }

  const activeTab = openTabs.find((tabItem) => tabItem.id === activeTabId) ?? openTabs[0] ?? getInitialTabs()[0];
  const activeSessionRecord = resolveSessionForTab(activeTab, sessions, session);
  const runtimeReady = Boolean(hostStatus && config);
  const composerVisible = runtimeReady && (activeTab.kind === "new-session" || activeTab.kind === "session");
  const workspaceName = workspace?.name ?? workspacePath.split(/[\\/]/).filter(Boolean).pop() ?? "yuanbao_agent";
  const providerLabel =
    providerSettings.mode === "mock"
      ? "测试模式"
      : providerSettings.model || providerSettings.name || "未配置模型";
  const cwdLabel = workspace?.rootPath ?? workspacePath ?? DEFAULT_WORKSPACE_PATH;
  const hostStatusText = describeMode(hostStatus);
  const runtimeUnavailableReason =
    !loading && !runtimeReady
      ? error ?? "Runtime handshake did not complete. The frontend cannot execute tasks on its own."
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
        endpoint: profile.baseUrl ?? "未配置接口",
        apiFormat: profile.apiFormat as SettingsProvider["apiFormat"],
        note:
          profile.mode === "mock"
            ? "测试模式，不会调用真实模型"
            : profile.apiKeyEnvVarName
              ? `环境变量：${profile.apiKeyEnvVarName}`
              : "需要配置 API 密钥",
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
            ? "已激活"
            : profile.lastStatus ?? "已配置",
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
      toolTimelineItems.map((toolCall) => ({
        id: toolCall.id,
        toolName: toolCall.toolName,
        status: toolCall.status,
        time: toolCall.updatedAt,
        resultSummary: toolCall.errorSummary ?? toolCall.resultSummary,
        durationMs: toolCall.durationMs,
        argsPreview: toolCall.argsSummary,
        input: toolCall.argsSummary,
        output: toolCall.resultSummary,
        stderr: toolCall.errorSummary,
      })),
    [toolTimelineItems],
  );
  const sessionCollaboration = useMemo(
    () => buildSessionCollaboration(events, traceEvents),
    [events, traceEvents],
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

  function handleCreateScheduledTask() {
    handleOpenSystemTab("new-session");
  }

  const workspaceContent = (() => {
    if (!runtimeReady && !loading) {
      return <RuntimeUnavailableWorkspace errorMessage={runtimeUnavailableReason ?? "Runtime unavailable."} />;
    }

    if (activeTab.kind === "new-session") {
      return <NewSessionWorkspace workspacePath={cwdLabel} hostStatusText={hostStatusText} />;
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
          taskCount={sessionTaskCount}
          collaboration={sessionCollaboration}
          backgroundJobs={sessionBackgroundJobs}
          approvals={sessionApprovals}
          patches={sessionPatches}
          traces={sessionTraceItems}
          toolCalls={sessionToolCalls}
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
            void navigator.clipboard?.writeText(path);
          }}
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
        onTestIM={() => setError("IM 接入后端尚未接入；当前仅保存界面草稿。")}
        computerUse={computerUseSettings}
        onComputerUseChange={setComputerUseSettings}
        onRecheckComputerUse={() =>
          setComputerUseSettings((current) => ({ ...current, status: "桌面权限检查待接入" }))
        }
        about={{
          version: "0.1.0",
          runtime: hostStatus?.runtimeTransport ?? "mock-browser",
          dataPath: workspacePath,
          build: hostStatus?.runtimeRunning ? "runtime running" : "runtime idle",
        }}
        onOpenLogs={() => setError("日志目录打开能力待接入 Tauri shell。")}
        onOpenDataDirectory={() => setError("数据目录打开能力待接入 Tauri shell。")}
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
      onSubmitPrompt={handleSendMessage}
      disabled={loading || messageBusy || !runtimeReady}
      providerLabel={providerLabel}
      cwdLabel={cwdLabel}
    >
      {error ? <p className="error-banner compact">{error}</p> : null}
      {workspaceContent}
    </AppShell>
  );
}
