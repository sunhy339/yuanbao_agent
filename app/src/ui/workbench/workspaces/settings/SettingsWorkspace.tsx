import { useEffect, useState } from "react";
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
  SettingsGeneralConfig,
  SettingsIMConfig,
  SettingsAgentConfig,
  SettingsSkillConfig,
  SettingsComputerUseConfig,
  SettingsAgentBehaviorConfig,
  SettingsAboutInfo,
  SettingsWorkspaceProps,
} from "./settingsTypes";
import { ProvidersPanel } from "./ProvidersPanel";
import { PermissionsPanel } from "./PermissionsPanel";
import { AgentBehaviorPanel } from "./AgentBehaviorPanel";
import { GeneralPanel } from "./GeneralPanel";
import { IMPanel } from "./IMPanel";
import { AgentsPanel } from "./AgentsPanel";
import { SkillsPanel } from "./SkillsPanel";
import { ComputerUsePanel } from "./ComputerUsePanel";
import { AboutPanel } from "./AboutPanel";
import { ProviderModal } from "./ProviderModal";
import "./settings.css";

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
  skills = [],
  onRefreshSkills,
  onOpenSkillsFolder,
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
  const readyProviders = providers.filter((provider) => provider.id === activeProviderId || provider.lastTest?.ok).length;
  const enabledAgents = agents.filter((agent) => agent.enabled).length;
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
              <span>{item.label}</span>
              <small>{item.eyebrow}</small>
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
          <span aria-hidden="true">i</span>
          关于
        </button>
      </aside>

      <section className="settings-pane" aria-live="polite">
        <div className="settings-content-panel">
          <h1 id="settings-title" className="settings-page-title">设置</h1>
          <section className="settings-command-strip" aria-label="设置概览">
            <div>
              <p className="settings-kicker">{activeSection.eyebrow} 管理</p>
              <h2>{activeSection.label}</h2>
              <span>{section === "providers" ? activeProvider?.name ?? "未选择供应商" : "桌面运行时配置"}</span>
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
              onSelectMode={(mode) => {
                setSelectedPermissionMode(mode);
                onPermissionModeChange?.(mode);
              }}
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
