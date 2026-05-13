import { useMemo, useState } from "react";
import type { AppConfig, ProviderProfile, ProviderTestResult, ToolRuntimeConfig } from "@shared";
import type {
  SettingsProviderFeedback,
  SettingsProviderPayload,
} from "../ui/workbench/workspaces/settings/SettingsWorkspace";
import { RuntimeClient, type RuntimeConfig } from "../lib/runtimeClient";
import {
  DEFAULT_PROVIDER_API_KEY_ENV_VAR,
  DEFAULT_PROVIDER_BASE_URL,
  DEFAULT_PROVIDER_MAX_TOKENS,
  DEFAULT_PROVIDER_MODEL,
  DEFAULT_PROVIDER_TEMPERATURE,
  DEFAULT_SEARCH_GLOB_TEXT,
  type CommandPolicyForm,
  type ProviderSettingsForm,
  normalizeProviderConfig,
  normalizeRuntimeConfig,
  buildProviderSettingsForm,
  buildCommandPolicyForm,
  parseProviderNumber,
  parsePatternText,
  serializePatternList,
} from "../state/providerConfig";
import { buildDefaultProviderProfile } from "../state/providerStatus";
import { buildProviderProfileFromPayload } from "../state/providerPayloadParsing";
import type { HookDeps } from "./types";

const runtimeClient = new RuntimeClient();

export interface UseProviderConfigDeps extends HookDeps {
  config: RuntimeConfig | null;
  setConfig: (config: RuntimeConfig) => void;
}

export function useProviderConfig(deps: UseProviderConfigDeps) {
  const { addToast, toastError, setError, config, setConfig } = deps;

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
  const [providerConfigBusy, setProviderConfigBusy] = useState(false);
  const [providerTestBusy, setProviderTestBusy] = useState(false);
  const [commandPolicyBusy, setCommandPolicyBusy] = useState(false);
  const [searchConfigBusy, setSearchConfigBusy] = useState(false);

  const activeProviderProfile = useMemo(
    () => config?.provider.profiles?.find((profile) => profile.id === activeProviderProfileId),
    [activeProviderProfileId, config],
  );

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

  return {
    providerSettings,
    setProviderSettings,
    commandPolicySettings,
    setCommandPolicySettings,
    activeProviderProfileId,
    setActiveProviderProfileId,
    providerTestResult,
    setProviderTestResult,
    providerFeedback,
    setProviderFeedback,
    searchGlob,
    setSearchGlob,
    searchIgnoreText,
    setSearchIgnoreText,
    providerConfigBusy,
    setProviderConfigBusy,
    providerTestBusy,
    commandPolicyBusy,
    searchConfigBusy,
    activeProviderProfile,
    buildProviderProfileFromForm,
    updateProviderSetting,
    updateCommandPolicySetting,
    selectProviderProfile,
    handleSaveSearchConfig,
    handleSaveProviderConfig,
    handleSaveCommandPolicyConfig,
    handleTestProvider,
    handleTestSelectedProvider,
    handleTestProviderConfigFromSettings,
    handleAddProviderFromSettings,
    handleEditProviderFromSettings,
    handleCreateProviderProfile,
    handleCopyProviderProfile,
    handleDeleteProviderProfile,
    persistSearchConfig,
  };
}
