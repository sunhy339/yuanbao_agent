import type { AppConfig, CapabilityName, CapabilityRule, PermissionPreset, ProviderProfile } from "@shared";
import type { RuntimeConfig } from "../lib/runtimeClient";
import type { SettingsPermissionRule, SettingsProviderPayload } from "../ui/workbench/workspaces/settings/SettingsWorkspace";
import {
  DEFAULT_PROVIDER_API_FORMAT,
  DEFAULT_PROVIDER_API_KEY_ENV_VAR,
  DEFAULT_PROVIDER_BASE_URL,
  DEFAULT_PROVIDER_MAX_CONTEXT_TOKENS,
  DEFAULT_PROVIDER_MAX_TOKENS,
  DEFAULT_PROVIDER_MODEL,
  DEFAULT_PROVIDER_TEMPERATURE,
  DEFAULT_PROVIDER_TIMEOUT,
  normalizeProviderApiFormat,
} from "./providerConfig";

export function getModelFromProviderPayload(payload: SettingsProviderPayload): string {
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

export function getProviderEnvVarName(payload: SettingsProviderPayload): string {
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

export interface ProviderJsonConfig {
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

export function readProviderJsonConfig(payload: SettingsProviderPayload): ProviderJsonConfig {
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

export function readEnvConfigText(value: string): Record<string, string> {
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

export function readEnvString(env: Record<string, string>, ...keys: string[]) {
  for (const key of keys) {
    const value = env[key];
    if (value?.trim()) {
      return value.trim();
    }
  }
  return undefined;
}

export function readEnvNumber(env: Record<string, string>, ...keys: string[]) {
  const value = readEnvString(env, ...keys);
  if (!value) {
    return undefined;
  }
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : undefined;
}

export function buildProviderProfileFromPayload(
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
    apiFormat: normalizeProviderApiFormat(
      jsonConfig.apiFormat || payload.apiFormat || existingProfile?.apiFormat || DEFAULT_PROVIDER_API_FORMAT,
    ),
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

export function buildSettingsGeneralConfig(config: RuntimeConfig) {
  const ui = config.ui;
  const language = (ui.language.toLowerCase().startsWith("zh")
    ? "zh"
    : ui.language.toLowerCase().startsWith("en")
      ? "en"
      : "auto") as "zh" | "en" | "auto";
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

export function clampAppearanceNumber(
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

export function settingsLanguageToConfig(language: "zh" | "en" | "auto"): string {
  if (language === "zh") {
    return "zh-CN";
  }
  if (language === "en") {
    return "en-US";
  }
  return "auto";
}

export function settingsModeToApprovalMode(mode: string): AppConfig["policy"]["approvalMode"] {
  if (mode === "skip") {
    return "none";
  }
  if (mode === "edits") {
    return "on_write_or_command";
  }
  return "strict";
}

export function settingsModeToPermissionPreset(mode: string): PermissionPreset {
  if (mode === "skip" || mode === "edits") {
    return "autonomous";
  }
  if (mode === "plan") {
    return "safe";
  }
  return "balanced";
}

export function approvalModeToSettingsMode(mode?: AppConfig["policy"]["approvalMode"] | string): string {
  if (mode === "none" || mode === "never" || mode === "off" || mode === "relaxed") {
    return "skip";
  }
  if (mode === "on_write_or_command") {
    return "edits";
  }
  return "ask";
}

const capabilityLabels: Partial<Record<CapabilityName | string, string>> = {
  readFile: "文件读取",
  writeFile: "文件写入",
  runCommand: "命令执行",
  webFetch: "Web 访问",
  network: "网络访问",
  subagents: "子任务",
  memoryWrite: "记忆写入",
  gitWrite: "Git 写入",
  browserAutomation: "浏览器自动化",
  computerUse: "电脑操作",
  hooksExecute: "Hooks 执行",
};

const capabilityDescriptions: Partial<Record<CapabilityName | string, string>> = {
  writeFile: "来自“始终允许”或手动配置的文件写入规则。",
  runCommand: "来自“始终允许”或手动配置的命令执行规则。",
  webFetch: "来自“始终允许”或手动配置的 Web 访问规则。",
  computerUse: "来自“始终允许”或手动配置的电脑操作规则。",
  subagents: "来自“始终允许”或手动配置的子任务规则。",
  gitWrite: "来自“始终允许”或手动配置的 Git 写入规则。",
};

const permissionModeLabels: Record<string, string> = {
  allow: "始终允许",
  ask: "每次询问",
  blocked: "阻止",
};

export function buildSettingsPermissionRules(config: RuntimeConfig | null): SettingsPermissionRule[] {
  const capabilities = config?.permissions?.capabilities;
  if (!capabilities) {
    return [];
  }

  return Object.entries(capabilities)
    .filter((entry): entry is [string, CapabilityRule] => {
      const [, rule] = entry;
      return Boolean(rule && typeof rule === "object" && typeof rule.mode === "string");
    })
    .map(([capability, rule]) => ({
      capability,
      label: capabilityLabels[capability] ?? capability,
      mode: rule.mode,
      modeLabel: permissionModeLabels[rule.mode] ?? rule.mode,
      scope: rule.scope || "*",
      description: capabilityDescriptions[capability] ?? "来自配置文件的 capability 覆盖规则。",
    }))
    .sort((left, right) => left.label.localeCompare(right.label, "zh-CN"));
}
