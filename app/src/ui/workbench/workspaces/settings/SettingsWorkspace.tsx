import { useEffect, useState, type ReactNode } from "react";
import {
  Bot,
  Info,
  MessagesSquare,
  MonitorCog,
  Palette,
  ServerCog,
  ShieldCheck,
  Sparkles,
  Workflow,
  Wrench,
} from "lucide-react";
import {
  sections,
  fallbackProviders,
  permissionModes,
  fallbackGeneral,
  fallbackIM,
  fallbackComputerUse,
} from "./settingsTypes";
import type {
  SettingsSection,
  SettingsProvider,
  SettingsProviderPayload,
  SettingsProviderTestResult,
  SettingsProviderFeedback,
  SettingsAgentFeedback,
  SettingsHookFeedback,
  SettingsHookConfig,
  SettingsHookDraft,
  SettingsHookPatch,
  SettingsGeneralConfig,
  SettingsIMConfig,
  SettingsAgentConfig,
  SettingsSkillConfig,
  SettingsComputerUseConfig,
  SettingsAgentBehaviorConfig,
  SettingsPermissionRule,
  SettingsAboutInfo,
  SettingsWorkspaceProps,
} from "./settingsTypes";
import { ProvidersPanel } from "./ProvidersPanel";
import { PermissionsPanel } from "./PermissionsPanel";
import { AgentBehaviorPanel } from "./AgentBehaviorPanel";
import { GeneralPanel } from "./GeneralPanel";
import { IMPanel } from "./IMPanel";
import { AgentsPanel } from "./AgentsPanel";
import { HooksPanel } from "./HooksPanel";
import { SkillsPanel } from "./SkillsPanel";
import { ComputerUsePanel } from "./ComputerUsePanel";
import { AboutPanel } from "./AboutPanel";
import { ProviderModal } from "./ProviderModal";
import "./settings.css";

const sectionChrome: Record<SettingsSection, { icon: ReactNode; summary: string }> = {
  providers: {
    icon: <ServerCog size={16} />,
    summary: "API、模型映射、密钥与连通性",
  },
  permissions: {
    icon: <ShieldCheck size={16} />,
    summary: "审批模式、始终允许规则与风险边界",
  },
  general: {
    icon: <Palette size={16} />,
    summary: "主题、密度、语言与默认推理偏好",
  },
  im: {
    icon: <MessagesSquare size={16} />,
    summary: "外部消息渠道和默认回复方式",
  },
  agents: {
    icon: <Bot size={16} />,
    summary: "角色预设、工具策略和模型默认值",
  },
  hooks: {
    icon: <Workflow size={16} />,
    summary: "运行时生命周期触发器",
  },
  skills: {
    icon: <Sparkles size={16} />,
    summary: "本地技能预设和工具白名单",
  },
  computer: {
    icon: <MonitorCog size={16} />,
    summary: "屏幕、浏览器和系统动作能力",
  },
  about: {
    icon: <Info size={16} />,
    summary: "版本、数据目录、项目焦点和记忆",
  },
};

// Re-export types for backward compatibility with external consumers
export type {
  SettingsSection,
  ProviderPresetId,
  ThemeMode,
  DensityMode,
  RadiusMode,
  MotionMode,
  AccentColor,
  LanguageMode,
  ReasoningEffort,
  SettingsProviderModelMapping,
  SettingsProviderTestResult,
  SettingsProvider,
  SettingsProviderFeedback,
  SettingsAgentFeedback,
  SettingsHookFeedback,
  SettingsHookConfig,
  SettingsHookDraft,
  SettingsHookPatch,
  SettingsPermissionRule,
  SettingsProviderPayload,
  SettingsGeneralConfig,
  SettingsIMConfig,
  SettingsAgentConfig,
  SettingsSkillConfig,
  SettingsComputerUseConfig,
  SettingsAgentBehaviorConfig,
  SettingsAboutInfo,
  SettingsWorkspaceProps,
} from "./settingsTypes";

export function SettingsWorkspace({
  providers = fallbackProviders,
  activeProviderId,
  onSelectProvider,
  onAddProvider,
  onEditProvider,
  onTestProvider,
  onTestProviderConfig,
  onSaveProvider,
  providerBusy = false,
  providerTestBusy = false,
  providerFeedback = null,
  permissionMode,
  onPermissionModeChange,
  permissionRules = [],
  permissionRuleBusyId = null,
  onClearPermissionRule,
  agentBehavior,
  onAgentBehaviorChange,
  general,
  onGeneralChange,
  im,
  onIMChange,
  onTestIM,
  agents = [],
  agentBusyId,
  agentFeedback,
  onRefreshAgents,
  onAgentToggle,
  onAddAgent,
  onUpdateAgent,
  onDeleteAgent,
  onValidateAgent,
  onPreviewAgentTools,
  hooks = [],
  hookExecutions = [],
  hookBusyId,
  hookFeedback,
  hookWorkspaceId,
  onRefreshHooks,
  onHookToggle,
  onAddHook,
  onUpdateHook,
  onDeleteHook,
  onRefreshHookExecutions,
  skills = [],
  onRefreshSkills,
  onOpenSkillsFolder,
  onOpenMcpManager,
  onOpenSkillsManager,
  computerUse,
  onComputerUseChange,
  onRecheckComputerUse,
  workspaceFocus,
  workspaceFocusBusy = false,
  onSaveWorkspaceFocus,
  workspaceMemorySummary,
  workspaceMemoryBusy = false,
  onClearWorkspaceMemory,
  about,
  onOpenLogs,
  onOpenDataDirectory,
}: SettingsWorkspaceProps) {
  const [section, setSection] = useState<SettingsSection>("providers");
  const [selectedProviderId, setSelectedProviderId] = useState(activeProviderId ?? providers[0]?.id ?? "default");
  const [selectedPermissionMode, setSelectedPermissionMode] = useState(permissionMode ?? permissionModes[0].id);
  const [localGeneral, setLocalGeneral] = useState(general ?? fallbackGeneral);
  const [localIM, setLocalIM] = useState(im ?? fallbackIM);
  const [localComputerUse, setLocalComputerUse] = useState(computerUse ?? fallbackComputerUse);
  const [providerModal, setProviderModal] = useState<{ mode: "add" | "edit"; provider?: SettingsProvider } | null>(null);

  useEffect(() => {
    if (activeProviderId) {
      setSelectedProviderId(activeProviderId);
    }
  }, [activeProviderId]);

  useEffect(() => {
    if (permissionMode) {
      setSelectedPermissionMode(permissionMode);
    }
  }, [permissionMode]);

  useEffect(() => {
    if (general) {
      setLocalGeneral(general);
    }
  }, [general]);

  useEffect(() => {
    if (im) {
      setLocalIM(im);
    }
  }, [im]);

  useEffect(() => {
    if (computerUse) {
      setLocalComputerUse(computerUse);
    }
  }, [computerUse]);

  const activeProvider = providers.find((provider) => provider.id === selectedProviderId) ?? providers[0];
  const activeSection = sections.find((item) => item.id === section) ?? sections[0];
  const activeChrome = sectionChrome[activeSection.id];
  const readyProviders = providers.filter((provider) => provider.id === activeProviderId || provider.lastTest?.ok).length;
  const enabledAgents = agents.filter((agent) => agent.enabled).length;
  const enabledHooks = hooks.filter((hook) => hook.enabled).length;
  const enabledSkills = skills.filter((skill) => skill.enabled).length;

  return (
    <main className="settings-workspace" aria-labelledby="settings-title">
      <aside className="settings-rail" aria-label="设置分区">
        <nav className="settings-nav">
          {sections.map((item) => (
            <button
              key={item.id}
              type="button"
              className={section === item.id ? "is-active" : undefined}
              onClick={() => setSection(item.id)}
              aria-current={section === item.id ? "page" : undefined}
              aria-label={item.label}
            >
              <span className="settings-nav-copy">
                <span className="settings-nav-icon" aria-hidden="true">{sectionChrome[item.id].icon}</span>
                <span>
                  <span>{item.label}</span>
                  <small>{item.eyebrow}</small>
                </span>
              </span>
            </button>
          ))}
        </nav>
        <button
          type="button"
          className={section === "about" ? "settings-about-link is-active" : "settings-about-link"}
          onClick={() => setSection("about")}
          aria-current={section === "about" ? "page" : undefined}
          aria-label="关于"
        >
          <span className="settings-nav-icon" aria-hidden="true">{sectionChrome.about.icon}</span>
          <span>关于</span>
        </button>
      </aside>

      <section className="settings-pane" aria-live="polite">
        <div className="settings-content-panel">
          <h1 id="settings-title" className="settings-page-title">设置</h1>
          <section className="settings-command-strip" aria-label="设置概览">
            <div className="settings-command-main">
              <span className="settings-command-icon" aria-hidden="true">{activeChrome.icon}</span>
              <div>
                <p className="settings-kicker">{activeSection.eyebrow} 管理</p>
                <h2>{activeSection.label}</h2>
                <span>{section === "providers" ? activeProvider?.name ?? "未选择供应商" : activeChrome.summary}</span>
              </div>
            </div>
            <div className="settings-manager-actions" aria-label="能力管理入口">
              <button type="button" className="settings-secondary-action" onClick={onOpenMcpManager} disabled={!onOpenMcpManager}>
                <Wrench size={14} aria-hidden="true" />
                管理 MCP
              </button>
              <button type="button" className="settings-secondary-action" onClick={onOpenSkillsManager} disabled={!onOpenSkillsManager}>
                <Sparkles size={14} aria-hidden="true" />
                管理技能
              </button>
            </div>
            <dl>
              <div>
                <dt>供应商</dt>
                <dd>{readyProviders}/{providers.length}</dd>
              </div>
              <div>
                <dt>智能体</dt>
                <dd>{enabledAgents}/{agents.length}</dd>
              </div>
              <div>
                <dt>Hooks</dt>
                <dd>{enabledHooks}/{hooks.length}</dd>
              </div>
              <div>
                <dt>技能</dt>
                <dd>{enabledSkills}/{skills.length}</dd>
              </div>
              <div>
                <dt>权限</dt>
                <dd>{permissionModes.find((mode) => mode.id === selectedPermissionMode)?.title ?? selectedPermissionMode}</dd>
              </div>
            </dl>
          </section>
          {section === "providers" ? (
            <ProvidersPanel
              providers={providers}
              activeProvider={activeProvider}
              activeProviderId={activeProviderId}
              selectedProviderId={selectedProviderId}
              providerFeedback={providerFeedback}
              onSelectProvider={(providerId) => {
                setSelectedProviderId(providerId);
                onSelectProvider?.(providerId);
              }}
              onAddProvider={() => setProviderModal({ mode: "add" })}
              onEditProvider={() => setProviderModal({ mode: "edit", provider: activeProvider })}
              onTestProvider={() => void onTestProvider?.(selectedProviderId)}
              onSaveProvider={() => void onSaveProvider?.(selectedProviderId)}
              providerBusy={providerBusy}
              providerTestBusy={providerTestBusy}
            />
          ) : null}
          {section === "permissions" ? (
            <PermissionsPanel
              selectedMode={selectedPermissionMode}
              rules={permissionRules}
              busyRuleId={permissionRuleBusyId}
              onSelectMode={(mode) => {
                setSelectedPermissionMode(mode);
                onPermissionModeChange?.(mode);
              }}
              onClearRule={onClearPermissionRule}
            />
          ) : null}
          {section === "general" ? (
            <GeneralPanel
              value={localGeneral}
              onChange={(next) => {
                setLocalGeneral(next);
                onGeneralChange?.(next);
              }}
            />
          ) : null}
          {section === "im" ? (
            <IMPanel
              value={localIM}
              onChange={(next) => {
                setLocalIM(next);
                onIMChange?.(next);
              }}
              onTestIM={onTestIM}
            />
          ) : null}
          {section === "agents" ? (
            <>
              {agentBehavior ? (
                <AgentBehaviorPanel
                  value={agentBehavior}
                  onChange={(next) => void onAgentBehaviorChange?.(next)}
                />
              ) : null}
              <AgentsPanel
                agents={agents}
                agentBusyId={agentBusyId}
                agentFeedback={agentFeedback}
                onRefreshAgents={onRefreshAgents}
                onAgentToggle={onAgentToggle}
                onAddAgent={onAddAgent}
                onUpdateAgent={onUpdateAgent}
                onDeleteAgent={onDeleteAgent}
                onValidateAgent={onValidateAgent}
                onPreviewAgentTools={onPreviewAgentTools}
              />
            </>
          ) : null}
          {section === "hooks" ? (
            <HooksPanel
              hooks={hooks}
              executions={hookExecutions}
              hookBusyId={hookBusyId}
              hookFeedback={hookFeedback}
              workspaceId={hookWorkspaceId}
              onRefreshHooks={onRefreshHooks}
              onHookToggle={onHookToggle}
              onAddHook={onAddHook}
              onUpdateHook={onUpdateHook}
              onDeleteHook={onDeleteHook}
              onRefreshHookExecutions={onRefreshHookExecutions}
            />
          ) : null}
          {section === "skills" ? (
            <SkillsPanel
              skills={skills}
              onRefreshSkills={onRefreshSkills}
              onOpenSkillsFolder={onOpenSkillsFolder}
            />
          ) : null}
          {section === "computer" ? (
            <ComputerUsePanel
              value={localComputerUse}
              onChange={(next) => {
                setLocalComputerUse(next);
                onComputerUseChange?.(next);
              }}
              onRecheckComputerUse={onRecheckComputerUse}
            />
          ) : null}
          {section === "about" ? (
            <AboutPanel
              about={about}
              workspaceFocus={workspaceFocus}
              workspaceFocusBusy={workspaceFocusBusy}
              onSaveWorkspaceFocus={onSaveWorkspaceFocus}
              workspaceMemorySummary={workspaceMemorySummary}
              workspaceMemoryBusy={workspaceMemoryBusy}
              onClearWorkspaceMemory={onClearWorkspaceMemory}
              onOpenLogs={onOpenLogs}
              onOpenDataDirectory={onOpenDataDirectory}
            />
          ) : null}
        </div>
      </section>

      {providerModal ? (
        <ProviderModal
          mode={providerModal.mode}
          provider={providerModal.provider}
          onClose={() => setProviderModal(null)}
          onAddProvider={onAddProvider}
          onEditProvider={onEditProvider}
          onTestProviderConfig={onTestProviderConfig}
          providerBusy={providerBusy}
          providerTestBusy={providerTestBusy}
        />
      ) : null}
    </main>
  );
}

export default SettingsWorkspace;
