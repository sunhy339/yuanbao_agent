import { formatStatusLabel } from "../../../copy";
import {
  type ProviderPresetId,
  type ProviderApiFormat,
  type SettingsProvider,
  type SettingsProviderTestResult,
  type SettingsProviderPayload,
  providerApiKeyEnvKeys,
} from "./settingsTypes";

export interface ProviderFormDraft {
  preset: ProviderPresetId;
  name: string;
  note: string;
  endpoint: string;
  apiFormat: ProviderApiFormat;
  apiKey: string;
  mainModel: string;
  haikuModel: string;
  sonnetModel: string;
  opusModel: string;
  testConnection: string;
  jsonConfig: string;
}

export function isDirectApiKey(value: string) {
  return value.trim().startsWith("sk-");
}

export function parseProviderConfigText(value: string) {
  const raw = value.trim();
  if (!raw) {
    return { env: {} as Record<string, string>, apiKeyEnvVarName: undefined as string | undefined };
  }

  try {
    const parsed = JSON.parse(raw) as unknown;
    const record = parsed && typeof parsed === "object" ? (parsed as Record<string, unknown>) : {};
    const envRecord = record.env && typeof record.env === "object"
      ? { ...record, ...(record.env as Record<string, unknown>) }
      : record;
    const env = Object.fromEntries(
      Object.entries(envRecord)
        .filter(([, item]) => typeof item === "string" && item.trim())
        .map(([key, item]) => [key, String(item).trim()]),
    );
    return {
      env,
      apiKeyEnvVarName: typeof record.apiKeyEnvVarName === "string" && record.apiKeyEnvVarName.trim()
        ? record.apiKeyEnvVarName.trim()
        : providerApiKeyEnvKeys.find((key) => key in env),
    };
  } catch {
    const env = Object.fromEntries(
      raw
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
    return {
      env,
      apiKeyEnvVarName: providerApiKeyEnvKeys.find((key) => key in env),
    };
  }
}

export function createProviderDraft(provider?: SettingsProvider): ProviderFormDraft {
  return {
    preset: provider?.preset ?? "custom",
    name: provider?.name ?? "自定义供应商",
    note: provider?.note ?? "",
    endpoint: provider?.endpoint ?? "",
    apiFormat: provider?.apiFormat ?? "openai-chat",
    apiKey: "",
    mainModel: provider?.modelMapping?.main ?? provider?.models?.[0] ?? "",
    haikuModel: provider?.modelMapping?.haiku ?? provider?.models?.[0] ?? "",
    sonnetModel: provider?.modelMapping?.sonnet ?? provider?.models?.[0] ?? "",
    opusModel: provider?.modelMapping?.opus ?? provider?.models?.[0] ?? "",
    testConnection: "GET /models",
    jsonConfig: provider?.jsonConfig ?? "",
  };
}

export function toProviderPayload(draft: ProviderFormDraft): SettingsProviderPayload {
  const parsedConfig = parseProviderConfigText(draft.jsonConfig);
  const detectedApiKeyEnvVarName =
    !draft.apiKey.trim() || !isDirectApiKey(draft.apiKey)
      ? parsedConfig.apiKeyEnvVarName
      : undefined;
  const payload: SettingsProviderPayload = {
    name: draft.name.trim(),
    note: draft.note.trim(),
    endpoint: draft.endpoint.trim(),
    apiFormat: draft.apiFormat,
    apiKey: isDirectApiKey(draft.apiKey) ? draft.apiKey.trim() : "",
    modelMapping: [
      `main=${draft.mainModel.trim()}`,
      `haiku=${draft.haikuModel.trim()}`,
      `sonnet=${draft.sonnetModel.trim()}`,
      `opus=${draft.opusModel.trim()}`,
    ].join("\n"),
    mainModel: draft.mainModel.trim(),
    haikuModel: draft.haikuModel.trim(),
    sonnetModel: draft.sonnetModel.trim(),
    opusModel: draft.opusModel.trim(),
    testConnection: draft.testConnection,
    jsonConfig: draft.jsonConfig,
    preset: draft.preset,
  };

  if (detectedApiKeyEnvVarName) {
    payload.apiKeyEnvVarName = detectedApiKeyEnvVarName;
  }

  return payload;
}

export function formatProviderModels(provider?: SettingsProvider) {
  if (!provider) {
    return "未配置模型";
  }
  const mapping = provider.modelMapping;
  if (mapping?.main || mapping?.sonnet || mapping?.opus) {
    return [mapping.main, mapping.haiku, mapping.sonnet, mapping.opus].filter(Boolean).join(" / ");
  }
  return provider.models?.join(" / ") || "未配置模型";
}

export function formatProviderSuccessDetail(result: SettingsProviderTestResult) {
  return [result.model, result.finishReason].filter(Boolean).join(" / ");
}

export function formatProviderTestResult(result: SettingsProviderTestResult) {
  return [
    result.ok ? "连接成功。" : "连接失败。",
    `状态：${formatStatusLabel(result.status)}`,
    result.model ? `模型：${result.model}` : undefined,
    result.finishReason ? `结束原因：${result.finishReason}` : undefined,
    result.checkedEnvVarName ? `环境变量：${result.checkedEnvVarName}` : undefined,
    result.errorSummary ? `原因：${result.errorSummary}` : undefined,
    result.message ? `消息：${result.message}` : undefined,
  ].filter(Boolean).join("\n");
}

export function buildProviderJson(draft: ProviderFormDraft) {
  return JSON.stringify(
    {
      env: {
        LOCAL_AGENT_PROVIDER_BASE_URL: draft.endpoint || "(供应商基础 URL)",
        LOCAL_AGENT_PROVIDER_API_FORMAT: draft.apiFormat,
        LOCAL_AGENT_PROVIDER_MODEL: draft.mainModel || "(模型 ID)",
        LOCAL_AGENT_PROVIDER_API_KEY: draft.apiKey ? "(已在表单中提供)" : "(在运行时环境中设置)",
      },
    },
    null,
    2,
  );
}
