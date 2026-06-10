import { useState } from "react";
import type { RuntimeConfig } from "../lib/runtimeClient";
import type {
  SettingsGeneralConfig,
  SettingsIMConfig,
  SettingsComputerUseConfig,
  SettingsAgentBehaviorConfig,
} from "../ui/workbench/workspaces/settings/SettingsWorkspace";
import { RuntimeClient, type ComputerUseProbeCapability, type ComputerUseProbeResult } from "../lib/runtimeClient";
import {
  normalizeRuntimeConfig,
  normalizeAutonomyConfig,
  normalizeAgentSoulConfig,
} from "../state/providerConfig";
import {
  buildSettingsGeneralConfig,
  settingsLanguageToConfig,
  settingsModeToApprovalMode,
  settingsModeToPermissionPreset,
} from "../state/providerPayloadParsing";
import type { HookDeps } from "./types";

const runtimeClient = new RuntimeClient();

const fullAccessCapabilityOverrides = {
  writeFile: { mode: "allow", scope: "*" },
  runCommand: { mode: "allow", scope: "*" },
  webFetch: { mode: "allow", scope: "*" },
  network: { mode: "allow", scope: "*" },
  subagents: { mode: "allow", scope: "*" },
  memoryWrite: { mode: "allow", scope: "*" },
  gitWrite: { mode: "allow", scope: "*" },
  hooksExecute: { mode: "allow", scope: "*" },
} as const;

export interface UseSettingsDeps extends HookDeps {
  config: RuntimeConfig | null;
  setConfig: (config: RuntimeConfig) => void;
}

function buildComputerUseProbe(): Pick<SettingsComputerUseConfig, "status" | "checkedAt" | "capabilities"> {
  const clipboardAvailable =
    typeof navigator !== "undefined" &&
    typeof navigator.clipboard?.writeText === "function";
  const desktopBridgeAvailable = runtimeClient.canOpenLocalAppPaths();

  return {
    status: "degraded",
    checkedAt: Date.now(),
    capabilities: [
      {
        id: "permission-audit",
        label: "权限审计",
        state: "ready",
        detail: "computer_use 会先走运行时审批，并把申请与审批结果写入专用事件。",
      },
      {
        id: "screen-observation",
        label: "屏幕观察",
        state: "ready",
        detail: "截图 action 已接入运行时，执行时会尝试 Pillow ImageGrab 并返回缩略预览。",
      },
      {
        id: "desktop-actions",
        label: "桌面动作",
        state: desktopBridgeAvailable ? "partial" : "guarded",
        detail: desktopBridgeAvailable
          ? "桌面桥接可用；运行时会优先使用 pyautogui，Windows 下可回退到 ctypes 坐标点击/滚动。"
          : "当前预览环境没有 Tauri 桌面桥接；正式桌面会话中可执行坐标点击、键入、按键与滚动。",
      },
      {
        id: "browser-dom",
        label: "浏览器 DOM 控制",
        state: "partial",
        detail: "Playwright page-like executor 协议已就绪，宿主还需要注入真实浏览器会话后才能按 selector 点击/输入。",
      },
      {
        id: "clipboard",
        label: "剪贴板",
        state: clipboardAvailable ? "ready" : "guarded",
        detail: clipboardAvailable ? "浏览器剪贴板写入可用。" : "当前上下文未暴露浏览器剪贴板写入 API。",
      },
      {
        id: "system-key-combos",
        label: "系统快捷键",
        state: "guarded",
        detail: "单键 press 已接入；系统级组合键仍保留在显式确认和后续宿主能力扩展下。",
      },
    ],
  };
}

function mergeBrowserClipboardProbe(
  probe: ComputerUseProbeResult,
  clipboardAvailable: boolean,
): Pick<SettingsComputerUseConfig, "status" | "checkedAt" | "capabilities"> {
  return {
    status: probe.status,
    checkedAt: probe.checkedAt,
    capabilities: probe.capabilities.map((capability: ComputerUseProbeCapability) => {
      if (capability.id !== "clipboard") {
        return capability;
      }
      return {
        ...capability,
        state: clipboardAvailable ? "ready" : capability.state,
        detail: clipboardAvailable
          ? "浏览器剪贴板写入可用；系统级读写仍需显式权限。"
          : capability.detail,
      };
    }),
  };
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
    externalEditor: "system",
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
  const [permissionRuleBusyId, setPermissionRuleBusyId] = useState<string | null>(null);

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
            externalEditor: next.externalEditor,
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
          permissions: {
            preset: settingsModeToPermissionPreset(mode),
            capabilities: mode === "skip"
              ? fullAccessCapabilityOverrides
              : { ...(config.permissions?.capabilities ?? {}) },
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

  async function handleClearPermissionRule(capability: string) {
    if (!config || !capability) {
      return;
    }

    setPermissionRuleBusyId(capability);
    setError(null);

    try {
      const result = await runtimeClient.clearPermissionRule({ capability });
      const normalized = normalizeRuntimeConfig(result.config);
      setConfig(normalized);
      addToast("success", result.removed ? "已恢复默认权限规则" : "权限规则已经是默认状态");
    } catch (reason) {
      toastError(reason);
    } finally {
      setPermissionRuleBusyId((current) => (current === capability ? null : current));
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

  async function handleRecheckComputerUse() {
    const clipboardAvailable =
      typeof navigator !== "undefined" &&
      typeof navigator.clipboard?.writeText === "function";
    try {
      const runtimeProbe = await runtimeClient.probeComputerUse();
      setComputerUseSettings((current) => ({
        ...current,
        ...mergeBrowserClipboardProbe(runtimeProbe, clipboardAvailable),
      }));
      addToast("info", "电脑操作能力已检查");
    } catch (reason) {
      const probe = buildComputerUseProbe();
      setComputerUseSettings((current) => ({
        ...current,
        ...probe,
      }));
      toastError(reason);
    }
  }

  return {
    generalSettings,
    setGeneralSettings,
    imSettings,
    setIMSettings,
    computerUseSettings,
    setComputerUseSettings,
    permissionRuleBusyId,
    handleGeneralSettingsChange,
    handleAgentBehaviorChange,
    handlePermissionModeChange,
    handleClearPermissionRule,
    handleOpenAppPath,
    handleCopyRuntimeText,
    handleRecheckComputerUse,
  };
}
