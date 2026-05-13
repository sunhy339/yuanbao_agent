import { useState } from "react";
import type { RuntimeConfig } from "../lib/runtimeClient";
import type {
  SettingsGeneralConfig,
  SettingsIMConfig,
  SettingsComputerUseConfig,
  SettingsAgentBehaviorConfig,
} from "../ui/workbench/workspaces/settings/SettingsWorkspace";
import { RuntimeClient } from "../lib/runtimeClient";
import {
  normalizeRuntimeConfig,
  normalizeAutonomyConfig,
  normalizeAgentSoulConfig,
} from "../state/providerConfig";
import {
  buildSettingsGeneralConfig,
  settingsLanguageToConfig,
  settingsModeToApprovalMode,
} from "../state/providerPayloadParsing";
import type { HookDeps } from "./types";

const runtimeClient = new RuntimeClient();

export interface UseSettingsDeps extends HookDeps {
  config: RuntimeConfig | null;
  setConfig: (config: RuntimeConfig) => void;
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

export function useSettings(deps: UseSettingsDeps) {
  const { addToast, toastError, setError, config, setConfig } = deps;

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
  });

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

  async function handleAgentBehaviorChange(next: SettingsAgentBehaviorConfig) {
    if (!config) {
      return;
    }

    setError(null);

    try {
      const autonomy = normalizeAutonomyConfig({
        ...config.autonomy,
        activeProfileId: next.autonomyActiveProfileId,
      });
      const agentSoul = normalizeAgentSoulConfig(config.agentSoul);
      const activeSoulId = next.soulActiveProfileId || agentSoul.activeProfileId;
      const now = Date.now();
      const profiles = agentSoul.profiles.map((profile) =>
        profile.id === activeSoulId
          ? {
              ...profile,
              name: next.soulName.trim() || profile.name,
              identity: next.soulIdentity,
              communicationStyle: next.soulCommunicationStyle,
              reasoningStyle: next.soulReasoningStyle,
              collaborationStyle: next.soulCollaborationStyle,
              customSystemPrompt: next.soulCustomSystemPrompt,
              updatedAt: now,
            }
          : profile,
      );
      const result = await runtimeClient.updateConfig({
        config: {
          autonomy,
          agentSoul: {
            ...agentSoul,
            activeProfileId: activeSoulId,
            workspaceInstructions: next.workspaceInstructions,
            profiles,
          },
        },
      });
      const normalized = normalizeRuntimeConfig(result.config);
      setConfig(normalized);
      addToast("success", "Agent behavior settings saved");
    } catch (reason) {
      toastError(reason);
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

  async function handleOpenAppPath(kind: "logs" | "data" | "skills") {
    setError(null);
    try {
      const result = await runtimeClient.openAppPath(kind);
      const label = kind === "logs" ? "日志" : kind === "skills" ? "技能目录" : "数据目录";
      addToast("success", `已打开${label}：${result.path}`);
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

  return {
    generalSettings,
    setGeneralSettings,
    imSettings,
    setIMSettings,
    computerUseSettings,
    setComputerUseSettings,
    handleGeneralSettingsChange,
    handleAgentBehaviorChange,
    handlePermissionModeChange,
    handleOpenAppPath,
    handleCopyRuntimeText,
    handleRecheckComputerUse,
  };
}
