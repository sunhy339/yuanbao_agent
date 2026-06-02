import {
  DEFAULT_PROVIDER_API_FORMAT as SHARED_DEFAULT_PROVIDER_API_FORMAT,
  normalizeProviderApiFormat,
} from "@shared";
import type {
  AgentSoulConfig,
  AgentSoulProfile,
  AppConfig,
  AutonomyConfig,
  AutonomyProfile,
  ProviderApiFormat,
  ProviderMode,
  ProviderProfile,
  TaskRecord,
  ToolRuntimeConfig,
} from "@shared";
import type { RuntimeConfig } from "../lib/runtimeClient";

export { normalizeProviderApiFormat };

export const DEFAULT_SEARCH_GLOB_TEXT = "";
export const DEFAULT_PROVIDER_MODE: ProviderMode = "mock";
export const DEFAULT_PROVIDER_BASE_URL = "https://api.openai.com/v1";
export const DEFAULT_PROVIDER_API_FORMAT = SHARED_DEFAULT_PROVIDER_API_FORMAT;
export const DEFAULT_PROVIDER_MODEL = "gpt-5-codex";
export const DEFAULT_PROVIDER_API_KEY_ENV_VAR = "LOCAL_AGENT_PROVIDER_API_KEY";
export const DEFAULT_PROVIDER_TEMPERATURE = 0.2;
export const DEFAULT_PROVIDER_MAX_TOKENS = 4000;
export const DEFAULT_PROVIDER_MAX_CONTEXT_TOKENS = 256000;
export const DEFAULT_PROVIDER_TIMEOUT = 30;
export const DEFAULT_ALLOWED_SHELL: ToolRuntimeConfig["allowedShell"] = "powershell";
export const DEFAULT_AUTONOMY_PROFILES: AutonomyProfile[] = [
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
];
export const DEFAULT_AGENT_SOUL_PROFILE: AgentSoulProfile = {
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
};
export const TRACE_LIMIT = 200;
export const TRACE_CACHE_LIMIT = 4000;
export const SESSION_TRACE_TASK_LIMIT = 8;
export const TRACE_AUTO_REFRESH_STATUSES = new Set<TaskRecord["status"]>([
  "completed",
  "failed",
  "waiting_approval",
]);
export type TaskControlAction = "cancel" | "pause" | "resume";

export function isTaskControllable(status?: string) {
  return Boolean(status && ["running", "planning", "verifying", "waiting_approval", "queued"].includes(status));
}

export function normalizeWorkspacePathForCompare(path: string) {
  return path.trim().replace(/\\/g, "/").replace(/\/+$/, "").toLowerCase();
}

export function workspaceNameFromPath(path?: string | null) {
  return path?.split(/[\\/]/).filter(Boolean).pop() || undefined;
}

export interface ProviderSettingsForm {
  name: string;
  mode: ProviderMode;
  baseUrl: string;
  apiFormat: ProviderApiFormat;
  model: string;
  apiKeyEnvVarName: string;
  temperature: string;
  maxTokens: string;
  maxContextTokens: string;
  timeout: string;
}

export interface CommandPolicyForm {
  allowedShell: ToolRuntimeConfig["allowedShell"];
  allowedCommands: string;
  deniedCommands: string;
  blockedPatterns: string;
  allowedCwdRoots: string;
}

export function formatTimestamp(timestamp?: number): string {
  if (!timestamp) {
    return "not recorded";
  }

  return new Date(timestamp).toLocaleString("en-US", {
    hour12: false,
  });
}

export function formatDuration(durationMs?: number): string {
  if (typeof durationMs !== "number" || !Number.isFinite(durationMs)) {
    return "in progress";
  }

  if (durationMs < 1000) {
    return `${Math.max(0, Math.round(durationMs))} ms`;
  }

  return `${(durationMs / 1000).toFixed(1)} s`;
}

export function formatCompactCount(value?: number | null): string {
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

export function normalizeRuntimeConfig(config: AppConfig | RuntimeConfig): RuntimeConfig {
  return {
    ...config,
    provider: normalizeProviderConfig(config.provider),
    autonomy: normalizeAutonomyConfig(config.autonomy),
    agentSoul: normalizeAgentSoulConfig(config.agentSoul),
    search: config.search ?? {
      glob: [],
      ignore: config.workspace.ignore,
    },
    tools: {
      ...(config.tools ?? {}),
      runCommand: normalizeRunCommandConfig(config.tools?.runCommand),
    },
    worktree: config.worktree,
  };
}

export function normalizeAutonomyConfig(autonomy?: Partial<AutonomyConfig>): AutonomyConfig {
  const rawProfiles = autonomy?.profiles?.length ? autonomy.profiles : DEFAULT_AUTONOMY_PROFILES;
  const profiles = rawProfiles.map((profile, index) => normalizeAutonomyProfile(profile, index));
  const activeProfileId = autonomy?.activeProfileId && profiles.some((item) => item.id === autonomy.activeProfileId)
    ? autonomy.activeProfileId
    : profiles.find((item) => item.id === "balanced")?.id ?? profiles[0].id;
  return { activeProfileId, profiles };
}

export function normalizeAutonomyProfile(profile: Partial<AutonomyProfile>, index: number): AutonomyProfile {
  const fallback = DEFAULT_AUTONOMY_PROFILES[index] ?? DEFAULT_AUTONOMY_PROFILES[2];
  const merged = { ...fallback, ...profile };
  return {
    ...merged,
    id: merged.id?.trim() || `autonomy_${index + 1}`,
    name: merged.name?.trim() || `Autonomy ${index + 1}`,
    level: merged.level || fallback.level,
    maxSteps: Number(merged.maxSteps || fallback.maxSteps),
    maxParallelSubtasks: Number(merged.maxParallelSubtasks || fallback.maxParallelSubtasks),
    allowBackground: Boolean(merged.allowBackground),
    allowSubagents: Boolean(merged.allowSubagents),
    allowFileWrite: merged.allowFileWrite || fallback.allowFileWrite,
    allowShell: merged.allowShell || fallback.allowShell,
    allowNetwork: Boolean(merged.allowNetwork),
    memoryRecallPolicy: merged.memoryRecallPolicy || fallback.memoryRecallPolicy,
    retryLimit: Number(merged.retryLimit ?? fallback.retryLimit),
    timeoutMs: Number(merged.timeoutMs || fallback.timeoutMs),
  };
}

export function normalizeAgentSoulConfig(agentSoul?: Partial<AgentSoulConfig>): AgentSoulConfig {
  const rawProfiles = agentSoul?.profiles?.length ? agentSoul.profiles : [DEFAULT_AGENT_SOUL_PROFILE];
  const profiles = rawProfiles.map((profile, index) => normalizeAgentSoulProfile(profile, index));
  const activeProfileId = agentSoul?.activeProfileId && profiles.some((item) => item.id === agentSoul.activeProfileId)
    ? agentSoul.activeProfileId
    : profiles[0].id;
  return {
    activeProfileId,
    workspaceInstructions: agentSoul?.workspaceInstructions ?? "",
    sessionOverrideEnabled: Boolean(agentSoul?.sessionOverrideEnabled),
    profiles,
  };
}

export function normalizeAgentSoulProfile(profile: Partial<AgentSoulProfile>, index: number): AgentSoulProfile {
  const merged = { ...DEFAULT_AGENT_SOUL_PROFILE, ...profile };
  return {
    ...merged,
    id: merged.id?.trim() || `soul_${index + 1}`,
    name: merged.name?.trim() || `Soul ${index + 1}`,
    description: merged.description ?? "",
    identity: merged.identity || DEFAULT_AGENT_SOUL_PROFILE.identity,
    principles: Array.isArray(merged.principles) ? merged.principles.map(String).filter(Boolean) : [],
    communicationStyle: merged.communicationStyle ?? "",
    reasoningStyle: merged.reasoningStyle ?? "",
    collaborationStyle: merged.collaborationStyle ?? "",
    domainPreferences: Array.isArray(merged.domainPreferences)
      ? merged.domainPreferences.map(String).filter(Boolean)
      : [],
    customSystemPrompt: merged.customSystemPrompt ?? "",
    enabled: merged.enabled !== false,
    scope: merged.scope || "global",
    createdAt: merged.createdAt ?? 0,
    updatedAt: merged.updatedAt ?? 0,
  };
}

export function buildSettingsAgentBehaviorConfig(config: RuntimeConfig | null) {
  const autonomy = normalizeAutonomyConfig(config?.autonomy);
  const agentSoul = normalizeAgentSoulConfig(config?.agentSoul);
  const activeSoul = agentSoul.profiles.find((profile) => profile.id === agentSoul.activeProfileId)
    ?? agentSoul.profiles[0]
    ?? DEFAULT_AGENT_SOUL_PROFILE;
  return {
    autonomyActiveProfileId: autonomy.activeProfileId,
    autonomyProfiles: autonomy.profiles.map((profile) => ({
      id: profile.id,
      name: profile.name,
      level: String(profile.level),
      maxSteps: profile.maxSteps,
      maxParallelSubtasks: profile.maxParallelSubtasks,
      allowBackground: profile.allowBackground,
      allowSubagents: profile.allowSubagents,
    })),
    soulActiveProfileId: activeSoul.id,
    soulName: activeSoul.name,
    soulIdentity: activeSoul.identity,
    soulCommunicationStyle: activeSoul.communicationStyle,
    soulReasoningStyle: activeSoul.reasoningStyle,
    soulCollaborationStyle: activeSoul.collaborationStyle,
    soulCustomSystemPrompt: activeSoul.customSystemPrompt,
    workspaceInstructions: agentSoul.workspaceInstructions,
  };
}

export function normalizeProviderConfig(provider: AppConfig["provider"]): AppConfig["provider"] {
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
    apiFormat: normalizeProviderApiFormat(activeProfile?.apiFormat ?? provider.apiFormat),
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

export function normalizeProviderProfiles(provider: AppConfig["provider"]): ProviderProfile[] {
  const legacyProfile = providerToProfile(provider, provider.activeProfileId || "default", "Default");
  const rawProfiles = provider.profiles?.length ? provider.profiles : [legacyProfile];
  return rawProfiles.map((profile, index) =>
    normalizeProviderProfile(profile, legacyProfile, index),
  );
}

export function normalizeProviderProfile(
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
    apiFormat: normalizeProviderApiFormat(merged.apiFormat),
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

export function providerToProfile(provider: AppConfig["provider"], id: string, name: string): ProviderProfile {
  const model = provider.model || provider.defaultModel || DEFAULT_PROVIDER_MODEL;
  const maxTokens = provider.maxTokens ?? provider.maxOutputTokens ?? DEFAULT_PROVIDER_MAX_TOKENS;
  return {
    id,
    name,
    mode: provider.mode ?? DEFAULT_PROVIDER_MODE,
    baseUrl: provider.baseUrl ?? DEFAULT_PROVIDER_BASE_URL,
    apiFormat: normalizeProviderApiFormat(provider.apiFormat),
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

export function buildProviderSettingsForm(config: RuntimeConfig | null): ProviderSettingsForm {
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
    apiFormat: normalizeProviderApiFormat(normalized.apiFormat),
    model: normalized.model ?? normalized.defaultModel,
    apiKeyEnvVarName: normalized.apiKeyEnvVarName ?? DEFAULT_PROVIDER_API_KEY_ENV_VAR,
    temperature: String(normalized.temperature),
    maxTokens: String(normalized.maxTokens ?? normalized.maxOutputTokens),
    maxContextTokens: String(normalized.maxContextTokens ?? DEFAULT_PROVIDER_MAX_CONTEXT_TOKENS),
    timeout: String(normalized.timeout ?? DEFAULT_PROVIDER_TIMEOUT),
  };
}

export function normalizeRunCommandConfig(runCommand?: Partial<ToolRuntimeConfig>): ToolRuntimeConfig {
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

export function buildCommandPolicyForm(config: RuntimeConfig | null): CommandPolicyForm {
  const runCommand = normalizeRunCommandConfig(config?.tools.runCommand);
  return {
    allowedShell: runCommand.allowedShell,
    allowedCommands: serializePatternList(runCommand.allowedCommands ?? []),
    deniedCommands: serializePatternList(runCommand.deniedCommands ?? []),
    blockedPatterns: serializePatternList(runCommand.blockedPatterns),
    allowedCwdRoots: serializePatternList(runCommand.allowedCwdRoots ?? []),
  };
}

export function parseProviderNumber(
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

export function parsePatternText(value: string): string[] {
  return value
    .split(/[\n,]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

export function serializePatternList(values: string[]): string {
  return values.join("\n");
}
