import type { ProviderProfile, ProviderTestResult } from "@shared";
import type { SettingsProviderTestResult } from "../ui/workbench/workspaces/settings/SettingsWorkspace";
import {
  DEFAULT_PROVIDER_API_FORMAT,
  DEFAULT_PROVIDER_API_KEY_ENV_VAR,
  DEFAULT_PROVIDER_BASE_URL,
  DEFAULT_PROVIDER_MAX_CONTEXT_TOKENS,
  DEFAULT_PROVIDER_MAX_TOKENS,
  DEFAULT_PROVIDER_MODEL,
  DEFAULT_PROVIDER_TEMPERATURE,
  DEFAULT_PROVIDER_TIMEOUT,
  DEFAULT_PROVIDER_MODE,
  type ProviderSettingsForm,
  formatTimestamp,
} from "./providerConfig";

export type ProviderStatusBadge = "preview" | "configured" | "missing env" | "failed" | "ok";

export interface ProviderStatusView {
  label: ProviderStatusBadge;
  badgeClass: "ok" | "warn" | "error" | "info" | "neutral";
}

export interface ProviderHealthView {
  checkedAtText: string;
  statusText: string;
  summaryText: string;
  badgeClass: "ok" | "warn" | "error" | "info" | "neutral";
}

export function getProviderStatusView(
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

export function getProviderRuntimeNotice(
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

export function getProviderErrorSummary(result: ProviderTestResult): string {
  const detail =
    result.lastErrorSummary ?? result.details?.errorSummary ?? result.details?.error ?? result.details?.summary;
  return typeof detail === "string" && detail.trim() ? detail.trim() : result.message;
}

export function getProviderHealthBadge(status?: string): ProviderHealthView["badgeClass"] {
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

export function getProviderHealthView(
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

export function readProviderTestDetail(
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

export function buildSettingsProviderLastTest(
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

export function buildDefaultProviderProfile(): ProviderProfile {
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
