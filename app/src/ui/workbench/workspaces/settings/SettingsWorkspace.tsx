import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import "./settings.css";

type SettingsSection =
  | "providers"
  | "permissions"
  | "general"
  | "im"
  | "agents"
  | "skills"
  | "computer"
  | "about";

type ProviderPresetId = "deepseek" | "zhipu" | "kimi" | "minimax" | "custom";
type ThemeMode = "light" | "dark" | "system";
type LanguageMode = "zh" | "en" | "auto";
type ReasoningEffort = "low" | "medium" | "high" | "max";

export interface SettingsProviderModelMapping {
  main: string;
  haiku: string;
  sonnet: string;
  opus: string;
}

export interface SettingsProvider {
  id: string;
  name: string;
  endpoint: string;
  note?: string;
  models?: string[];
  status?: string;
  apiKeyMasked?: string;
  modelMapping?: Partial<SettingsProviderModelMapping>;
  jsonConfig?: string;
  preset?: ProviderPresetId;
}

export interface SettingsProviderPayload {
  name: string;
  note: string;
  endpoint: string;
  apiKey: string;
  modelMapping: string;
  mainModel: string;
  haikuModel: string;
  sonnetModel: string;
  opusModel: string;
  testConnection: string;
  jsonConfig: string;
  preset: ProviderPresetId;
}

export interface SettingsGeneralConfig {
  theme: ThemeMode;
  language: LanguageMode;
  reasoningEffort: ReasoningEffort;
  webFetchPreflight: boolean;
}

export interface SettingsIMConfig {
  enabled: boolean;
  provider: string;
  webhookUrl: string;
  signingSecretSet?: boolean;
  defaultReplyMode: "manual" | "auto" | "silent";
}

export interface SettingsAgentConfig {
  id: string;
  name: string;
  description?: string;
  cwd?: string;
  enabled: boolean;
  permissionMode?: string;
}

export interface SettingsSkillConfig {
  id: string;
  name: string;
  description?: string;
  path?: string;
  enabled: boolean;
  updateAvailable?: boolean;
}

export interface SettingsComputerUseConfig {
  screenshot: boolean;
  browserAutomation: boolean;
  clipboardAccess: boolean;
  systemKeyCombos: boolean;
  sensitiveActionConfirm: boolean;
  status?: string;
}

export interface SettingsAboutInfo {
  version?: string;
  runtime?: string;
  dataPath?: string;
  build?: string;
}

export interface SettingsWorkspaceProps {
  providers?: SettingsProvider[];
  activeProviderId?: string;
  onSelectProvider?: (providerId: string) => void;
  onAddProvider?: (payload: SettingsProviderPayload) => void | Promise<void>;
  onEditProvider?: (
    providerId: string,
    payload: SettingsProviderPayload,
  ) => void | Promise<void>;
  onTestProvider?: (providerId?: string) => void | Promise<void>;
  onTestProviderConfig?: (payload: SettingsProviderPayload) => void | Promise<void>;
  onSaveProvider?: (providerId?: string) => void | Promise<void>;
  providerBusy?: boolean;
  providerTestBusy?: boolean;
  permissionMode?: string;
  onPermissionModeChange?: (mode: string) => void;
  general?: SettingsGeneralConfig;
  onGeneralChange?: (next: SettingsGeneralConfig) => void;
  im?: SettingsIMConfig;
  onIMChange?: (next: SettingsIMConfig) => void;
  onTestIM?: () => void | Promise<void>;
  agents?: SettingsAgentConfig[];
  onAgentToggle?: (agentId: string, enabled: boolean) => void;
  onAddAgent?: () => void;
  skills?: SettingsSkillConfig[];
  onSkillToggle?: (skillId: string, enabled: boolean) => void;
  onRefreshSkills?: () => void | Promise<void>;
  onOpenSkillsFolder?: () => void;
  computerUse?: SettingsComputerUseConfig;
  onComputerUseChange?: (next: SettingsComputerUseConfig) => void;
  onRecheckComputerUse?: () => void | Promise<void>;
  about?: SettingsAboutInfo;
  onOpenLogs?: () => void;
  onOpenDataDirectory?: () => void;
}

const sections: Array<{ id: SettingsSection; label: string; eyebrow: string }> = [
  { id: "providers", label: "服务商", eyebrow: "API" },
  { id: "permissions", label: "权限", eyebrow: "Mode" },
  { id: "general", label: "通用", eyebrow: "Desk" },
  { id: "im", label: "IM 接入", eyebrow: "Bridge" },
  { id: "agents", label: "Agents", eyebrow: "Roster" },
  { id: "skills", label: "技能", eyebrow: "Library" },
  { id: "computer", label: "Computer Use", eyebrow: "Control" },
  { id: "about", label: "关于", eyebrow: "Build" },
];

const fallbackProviders: SettingsProvider[] = [
  {
    id: "deepseek",
    name: "DeepSeek",
    endpoint: "https://api.deepseek.com/anthropic",
    note: "代码与推理主力",
    models: ["DeepSeek-V3.2", "DeepSeek-R1"],
    status: "已启用",
    preset: "deepseek",
    modelMapping: {
      main: "DeepSeek-V3.2",
      haiku: "DeepSeek-V3.2",
      sonnet: "DeepSeek-V3.2",
      opus: "DeepSeek-R1",
    },
  },
  {
    id: "minimax",
    name: "MiniMax",
    endpoint: "https://api.minimax.chat/v1",
    note: "高速 Claude 兼容服务",
    models: ["MiniMax-M2.7-highspeed"],
    status: "待验证",
    preset: "minimax",
  },
  {
    id: "kimi",
    name: "Kimi",
    endpoint: "https://api.moonshot.cn/v1",
    note: "长上下文阅读备用",
    models: ["moonshot-v1-128k"],
    status: "已配置",
    preset: "kimi",
  },
];

const providerPresets: Array<{
  id: ProviderPresetId;
  label: string;
  endpoint: string;
  mainModel: string;
  haikuModel: string;
  sonnetModel: string;
  opusModel: string;
}> = [
  {
    id: "deepseek",
    label: "DeepSeek",
    endpoint: "https://api.deepseek.com/anthropic",
    mainModel: "DeepSeek-V3.2",
    haikuModel: "DeepSeek-V3.2",
    sonnetModel: "DeepSeek-V3.2",
    opusModel: "DeepSeek-R1",
  },
  {
    id: "zhipu",
    label: "Zhipu GLM",
    endpoint: "https://open.bigmodel.cn/api/paas/v4",
    mainModel: "glm-4-plus",
    haikuModel: "glm-4-air",
    sonnetModel: "glm-4-plus",
    opusModel: "glm-4-plus",
  },
  {
    id: "kimi",
    label: "Kimi",
    endpoint: "https://api.moonshot.cn/v1",
    mainModel: "moonshot-v1-128k",
    haikuModel: "moonshot-v1-8k",
    sonnetModel: "moonshot-v1-32k",
    opusModel: "moonshot-v1-128k",
  },
  {
    id: "minimax",
    label: "MiniMax",
    endpoint: "https://api.minimax.chat/v1",
    mainModel: "MiniMax-M2.7-highspeed",
    haikuModel: "MiniMax-M2.7-highspeed",
    sonnetModel: "MiniMax-M2.7-highspeed",
    opusModel: "MiniMax-M2.7-highspeed",
  },
  {
    id: "custom",
    label: "Custom",
    endpoint: "",
    mainModel: "",
    haikuModel: "",
    sonnetModel: "",
    opusModel: "",
  },
];

const permissionModes = [
  {
    id: "ask",
    title: "询问权限",
    text: "执行工具前先询问，适合陌生项目和高风险目录。",
  },
  {
    id: "edits",
    title: "接受编辑",
    text: "自动批准文件编辑，其他操作仍按规则确认。",
  },
  {
    id: "plan",
    title: "计划模式",
    text: "只思考和规划，不直接执行写入或命令。",
  },
  {
    id: "skip",
    title: "跳过全部",
    text: "跳过所有权限检查，仅适合完全可信的本地任务。",
  },
];

const fallbackGeneral: SettingsGeneralConfig = {
  theme: "light",
  language: "zh",
  reasoningEffort: "max",
  webFetchPreflight: true,
};

const fallbackIM: SettingsIMConfig = {
  enabled: false,
  provider: "feishu",
  webhookUrl: "",
  signingSecretSet: false,
  defaultReplyMode: "manual",
};

const fallbackComputerUse: SettingsComputerUseConfig = {
  screenshot: false,
  browserAutomation: false,
  clipboardAccess: true,
  systemKeyCombos: false,
  sensitiveActionConfirm: true,
  status: "未检查",
};

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
  permissionMode,
  onPermissionModeChange,
  general,
  onGeneralChange,
  im,
  onIMChange,
  onTestIM,
  agents = [],
  onAgentToggle,
  onAddAgent,
  skills = [],
  onSkillToggle,
  onRefreshSkills,
  onOpenSkillsFolder,
  computerUse,
  onComputerUseChange,
  onRecheckComputerUse,
  about,
  onOpenLogs,
  onOpenDataDirectory,
}: SettingsWorkspaceProps) {
  const [section, setSection] = useState<SettingsSection>("providers");
  const [selectedProviderId, setSelectedProviderId] = useState(
    activeProviderId ?? providers[0]?.id ?? fallbackProviders[0].id,
  );
  const [selectedPermissionMode, setSelectedPermissionMode] = useState(
    permissionMode ?? permissionModes[0].id,
  );
  const [localGeneral, setLocalGeneral] = useState(general ?? fallbackGeneral);
  const [localIM, setLocalIM] = useState(im ?? fallbackIM);
  const [localComputerUse, setLocalComputerUse] = useState(
    computerUse ?? fallbackComputerUse,
  );
  const [providerModal, setProviderModal] = useState<{
    mode: "add" | "edit";
    provider?: SettingsProvider;
  } | null>(null);

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

  const activeProvider =
    providers.find((provider) => provider.id === selectedProviderId) ??
    providers[0];

  const handleSelectProvider = (providerId: string) => {
    setSelectedProviderId(providerId);
    onSelectProvider?.(providerId);
  };

  const handlePermissionModeChange = (mode: string) => {
    setSelectedPermissionMode(mode);
    onPermissionModeChange?.(mode);
  };

  const handleGeneralChange = (next: SettingsGeneralConfig) => {
    setLocalGeneral(next);
    onGeneralChange?.(next);
  };

  const handleIMChange = (next: SettingsIMConfig) => {
    setLocalIM(next);
    onIMChange?.(next);
  };

  const handleComputerUseChange = (next: SettingsComputerUseConfig) => {
    setLocalComputerUse(next);
    onComputerUseChange?.(next);
  };

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
          <span aria-hidden="true">·</span>
          关于
        </button>
      </aside>

      <section className="settings-pane" aria-live="polite">
        <div className="settings-content-panel">
          <h1 id="settings-title" className="settings-page-title">
            设置
          </h1>

          {section === "providers" ? (
            <ProvidersPanel
              providers={providers}
              activeProvider={activeProvider}
              selectedProviderId={selectedProviderId}
              onSelectProvider={handleSelectProvider}
              onAddProvider={() => setProviderModal({ mode: "add" })}
              onEditProvider={() =>
                setProviderModal({ mode: "edit", provider: activeProvider })
              }
              onTestProvider={() => void onTestProvider?.(selectedProviderId)}
              onSaveProvider={() => void onSaveProvider?.(selectedProviderId)}
              providerBusy={providerBusy}
              providerTestBusy={providerTestBusy}
            />
          ) : null}
          {section === "permissions" ? (
            <PermissionsPanel
              selectedMode={selectedPermissionMode}
              onSelectMode={handlePermissionModeChange}
            />
          ) : null}
          {section === "general" ? (
            <GeneralPanel value={localGeneral} onChange={handleGeneralChange} />
          ) : null}
          {section === "im" ? (
            <IMPanel value={localIM} onChange={handleIMChange} onTestIM={onTestIM} />
          ) : null}
          {section === "agents" ? (
            <AgentsPanel
              agents={agents}
              onAgentToggle={onAgentToggle}
              onAddAgent={onAddAgent}
            />
          ) : null}
          {section === "skills" ? (
            <SkillsPanel
              skills={skills}
              onSkillToggle={onSkillToggle}
              onRefreshSkills={onRefreshSkills}
              onOpenSkillsFolder={onOpenSkillsFolder}
            />
          ) : null}
          {section === "computer" ? (
            <ComputerUsePanel
              value={localComputerUse}
              onChange={handleComputerUseChange}
              onRecheckComputerUse={onRecheckComputerUse}
            />
          ) : null}
          {section === "about" ? (
            <AboutPanel
              about={about}
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

function ProvidersPanel({
  providers,
  activeProvider,
  selectedProviderId,
  onSelectProvider,
  onAddProvider,
  onEditProvider,
  onTestProvider,
  onSaveProvider,
  providerBusy,
  providerTestBusy,
}: {
  providers: SettingsProvider[];
  activeProvider?: SettingsProvider;
  selectedProviderId: string;
  onSelectProvider: (providerId: string) => void;
  onAddProvider: () => void;
  onEditProvider: () => void;
  onTestProvider: () => void;
  onSaveProvider: () => void;
  providerBusy: boolean;
  providerTestBusy: boolean;
}) {
  return (
    <div className="settings-panel settings-panel-providers">
      <header className="settings-panel-header">
        <div>
          <p className="settings-kicker">Providers</p>
          <h2>服务商</h2>
          <p>管理 API 服务商以访问模型。每个服务商可配置接口、密钥和 Claude 兼容模型映射。</p>
        </div>
        <button
          className="settings-primary-action"
          type="button"
          onClick={onAddProvider}
          aria-label="添加服务商"
        >
          + 添加服务商
        </button>
      </header>

      <div className="settings-provider-grid">
        <div className="settings-provider-list" aria-label="服务商列表">
          {providers.map((provider) => (
            <button
              key={provider.id}
              type="button"
              className={
                provider.id === selectedProviderId
                  ? "settings-provider-item is-active"
                  : "settings-provider-item"
              }
              onClick={() => onSelectProvider(provider.id)}
              aria-current={provider.id === selectedProviderId ? "page" : undefined}
              aria-label={`选择服务商 ${provider.name}`}
            >
              <span className="settings-provider-dot" aria-hidden="true" />
              <span className="settings-provider-copy">
                <strong>{provider.name}</strong>
                <small>
                  {provider.endpoint}
                  {provider.models?.[0] ? ` · ${provider.models[0]}` : ""}
                </small>
                {provider.note ? <small>{provider.note}</small> : null}
              </span>
              {provider.status ? <em>{provider.status}</em> : null}
            </button>
          ))}
        </div>

        <article className="settings-provider-detail" aria-label="当前服务商详情">
          <div>
            <p className="settings-kicker">Selected Provider</p>
            <h3>{activeProvider?.name ?? "未选择服务商"}</h3>
          </div>
          <dl>
            <div>
              <dt>接口地址</dt>
              <dd>{activeProvider?.endpoint ?? "未配置"}</dd>
            </div>
            <div>
              <dt>模型映射</dt>
              <dd>{formatProviderModels(activeProvider)}</dd>
            </div>
            <div>
              <dt>密钥状态</dt>
              <dd>{activeProvider?.apiKeyMasked ?? "由运行时保存，不在界面明文展示"}</dd>
            </div>
          </dl>
          <div className="settings-provider-actions">
            <button type="button" className="settings-secondary-action" onClick={onEditProvider}>
              编辑
            </button>
            <button
              type="button"
              className="settings-secondary-action"
              onClick={onTestProvider}
              disabled={providerTestBusy}
            >
              {providerTestBusy ? "测试中..." : "测试连接"}
            </button>
            <button
              type="button"
              className="settings-primary-action"
              onClick={onSaveProvider}
              disabled={providerBusy}
            >
              {providerBusy ? "保存中..." : "保存"}
            </button>
          </div>
        </article>
      </div>
    </div>
  );
}

function PermissionsPanel({
  selectedMode,
  onSelectMode,
}: {
  selectedMode: string;
  onSelectMode: (mode: string) => void;
}) {
  return (
    <div className="settings-panel settings-narrow-panel">
      <header className="settings-panel-header settings-panel-header-plain">
        <div>
          <p className="settings-kicker">Permission Mode</p>
          <h2>权限模式</h2>
          <p>控制工具执行权限的处理方式。选中后会通过回调同步给上层运行时。</p>
        </div>
      </header>

      <div className="settings-card-stack" role="radiogroup" aria-label="权限模式">
        {permissionModes.map((mode) => (
          <label
            key={mode.id}
            className={
              selectedMode === mode.id
                ? "settings-choice-card is-selected"
                : "settings-choice-card"
            }
          >
            <input
              type="radio"
              name="permission-mode"
              checked={selectedMode === mode.id}
              onChange={() => onSelectMode(mode.id)}
            />
            <span>
              <strong>{mode.title}</strong>
              <small>{mode.text}</small>
            </span>
          </label>
        ))}
      </div>
    </div>
  );
}

function GeneralPanel({
  value,
  onChange,
}: {
  value: SettingsGeneralConfig;
  onChange: (next: SettingsGeneralConfig) => void;
}) {
  return (
    <div className="settings-panel settings-narrow-panel">
      <header className="settings-panel-header settings-panel-header-plain">
        <div>
          <p className="settings-kicker">General</p>
          <h2>通用</h2>
          <p>配置主题、语言、推理强度和 WebFetch 预检策略。</p>
        </div>
      </header>

      <div className="settings-form-stack">
        <SegmentedControl
          label="配色主题"
          name="theme"
          value={value.theme}
          options={[
            { value: "light", label: "亮色" },
            { value: "dark", label: "暗色" },
            { value: "system", label: "跟随系统" },
          ]}
          onChange={(theme) => onChange({ ...value, theme: theme as ThemeMode })}
        />
        <SegmentedControl
          label="语言"
          name="language"
          value={value.language}
          options={[
            { value: "en", label: "English" },
            { value: "zh", label: "中文" },
            { value: "auto", label: "自动" },
          ]}
          onChange={(language) =>
            onChange({ ...value, language: language as LanguageMode })
          }
        />
        <SegmentedControl
          label="推理强度"
          name="reasoning"
          value={value.reasoningEffort}
          options={[
            { value: "low", label: "低" },
            { value: "medium", label: "中" },
            { value: "high", label: "高" },
            { value: "max", label: "最大" },
          ]}
          onChange={(reasoningEffort) =>
            onChange({
              ...value,
              reasoningEffort: reasoningEffort as ReasoningEffort,
            })
          }
        />
        <label className="settings-toggle-card" htmlFor="webfetch-preflight">
          <input
            id="webfetch-preflight"
            type="checkbox"
            checked={value.webFetchPreflight}
            onChange={(event) =>
              onChange({ ...value, webFetchPreflight: event.currentTarget.checked })
            }
          />
          <span>
            <strong>跳过 WebFetch 域名预检</strong>
            <small>仅在明确需要恢复上游默认安全预检时关闭此选项。</small>
          </span>
        </label>
      </div>
    </div>
  );
}

function IMPanel({
  value,
  onChange,
  onTestIM,
}: {
  value: SettingsIMConfig;
  onChange: (next: SettingsIMConfig) => void;
  onTestIM?: () => void | Promise<void>;
}) {
  return (
    <div className="settings-panel settings-narrow-panel">
      <header className="settings-panel-header settings-panel-header-plain">
        <div>
          <p className="settings-kicker">IM Bridge</p>
          <h2>IM 接入</h2>
          <p>连接飞书、企业微信或自建网关，让会话进入消息渠道。</p>
        </div>
      </header>

      <div className="settings-form-stack">
        <label className="settings-toggle-card" htmlFor="im-enabled">
          <input
            id="im-enabled"
            type="checkbox"
            checked={value.enabled}
            onChange={(event) => onChange({ ...value, enabled: event.currentTarget.checked })}
          />
          <span>
            <strong>启用 IM 网关</strong>
            <small>关闭时保留配置，但不接收外部消息。</small>
          </span>
        </label>
        <label className="settings-field" htmlFor="im-provider">
          <span>渠道</span>
          <select
            id="im-provider"
            value={value.provider}
            onChange={(event) => onChange({ ...value, provider: event.currentTarget.value })}
          >
            <option value="feishu">飞书</option>
            <option value="wecom">企业微信</option>
            <option value="custom">自建网关</option>
          </select>
        </label>
        <label className="settings-field" htmlFor="im-webhook">
          <span>Webhook 地址</span>
          <input
            id="im-webhook"
            type="url"
            value={value.webhookUrl}
            placeholder="https://example.com/im/webhook"
            onChange={(event) =>
              onChange({ ...value, webhookUrl: event.currentTarget.value })
            }
          />
        </label>
        <SegmentedControl
          label="默认回复策略"
          name="im-reply-mode"
          value={value.defaultReplyMode}
          options={[
            { value: "manual", label: "人工确认" },
            { value: "auto", label: "自动回复" },
            { value: "silent", label: "静默记录" },
          ]}
          onChange={(defaultReplyMode) =>
            onChange({
              ...value,
              defaultReplyMode: defaultReplyMode as SettingsIMConfig["defaultReplyMode"],
            })
          }
        />
        <div className="settings-inline-actions">
          <span>签名密钥：{value.signingSecretSet ? "已配置" : "未配置"}</span>
          <button type="button" className="settings-secondary-action" onClick={onTestIM}>
            测试 IM 连接
          </button>
        </div>
      </div>
    </div>
  );
}

function AgentsPanel({
  agents,
  onAgentToggle,
  onAddAgent,
}: {
  agents: SettingsAgentConfig[];
  onAgentToggle?: (agentId: string, enabled: boolean) => void;
  onAddAgent?: () => void;
}) {
  return (
    <div className="settings-panel">
      <header className="settings-panel-header">
        <div>
          <p className="settings-kicker">Agent Roster</p>
          <h2>Agents</h2>
          <p>管理常驻代理、工作目录和默认授权策略。接入运行时后可在这里扩展代理配置。</p>
        </div>
        <button type="button" className="settings-primary-action" onClick={onAddAgent}>
          + 添加 Agent
        </button>
      </header>
      <ListOrEmpty emptyTitle="暂无 Agent" emptyText="接入运行时后可显示常驻代理。">
        {agents.map((agent) => (
          <label key={agent.id} className="settings-row-card">
            <input
              type="checkbox"
              checked={agent.enabled}
              onChange={(event) => onAgentToggle?.(agent.id, event.currentTarget.checked)}
            />
            <span>
              <strong>{agent.name}</strong>
              <small>{agent.description ?? "未填写说明"}</small>
              <small>{agent.cwd ?? "未绑定工作目录"}</small>
            </span>
            <em>{agent.permissionMode ?? "继承权限"}</em>
          </label>
        ))}
      </ListOrEmpty>
    </div>
  );
}

function SkillsPanel({
  skills,
  onSkillToggle,
  onRefreshSkills,
  onOpenSkillsFolder,
}: {
  skills: SettingsSkillConfig[];
  onSkillToggle?: (skillId: string, enabled: boolean) => void;
  onRefreshSkills?: () => void | Promise<void>;
  onOpenSkillsFolder?: () => void;
}) {
  return (
    <div className="settings-panel">
      <header className="settings-panel-header">
        <div>
          <p className="settings-kicker">Skill Library</p>
          <h2>技能</h2>
          <p>技能扩展 Claude 的能力。在 ~/.claude/skills/ 中管理技能。</p>
        </div>
        <div className="settings-header-actions">
          <button type="button" className="settings-secondary-action" onClick={onOpenSkillsFolder}>
            打开目录
          </button>
          <button type="button" className="settings-primary-action" onClick={onRefreshSkills}>
            刷新技能
          </button>
        </div>
      </header>
      <ListOrEmpty emptyTitle="暂无已安装技能" emptyText="在 ~/.claude/skills/ 中添加技能即可开始。">
        {skills.map((skill) => (
          <label key={skill.id} className="settings-row-card">
            <input
              type="checkbox"
              checked={skill.enabled}
              onChange={(event) => onSkillToggle?.(skill.id, event.currentTarget.checked)}
            />
            <span>
              <strong>{skill.name}</strong>
              <small>{skill.description ?? "未填写说明"}</small>
              <small>{skill.path ?? "未提供路径"}</small>
            </span>
            {skill.updateAvailable ? <em>可更新</em> : null}
          </label>
        ))}
      </ListOrEmpty>
    </div>
  );
}

function ComputerUsePanel({
  value,
  onChange,
  onRecheckComputerUse,
}: {
  value: SettingsComputerUseConfig;
  onChange: (next: SettingsComputerUseConfig) => void;
  onRecheckComputerUse?: () => void | Promise<void>;
}) {
  const toggles: Array<{
    key: keyof SettingsComputerUseConfig;
    label: string;
    text: string;
  }> = [
    { key: "screenshot", label: "截图观察", text: "允许读取屏幕快照用于任务判断。" },
    { key: "browserAutomation", label: "浏览器自动化", text: "允许打开并控制浏览器。" },
    { key: "clipboardAccess", label: "剪贴板访问", text: "允许读取和写入剪贴板。" },
    { key: "systemKeyCombos", label: "系统快捷键", text: "允许发送系统组合键。" },
    {
      key: "sensitiveActionConfirm",
      label: "敏感操作确认",
      text: "删除、支付、发送等动作前强制确认。",
    },
  ];

  return (
    <div className="settings-panel settings-narrow-panel">
      <header className="settings-panel-header settings-panel-header-plain">
        <div>
          <p className="settings-kicker">Computer Use</p>
          <h2>Computer Use</h2>
          <p>允许代理截图、点击、输入和控制电脑。真实权限检查由运行时接入。</p>
        </div>
      </header>
      <div className="settings-form-stack">
        {toggles.map((toggle) => (
          <label key={toggle.key} className="settings-toggle-card">
            <input
              type="checkbox"
              checked={Boolean(value[toggle.key])}
              onChange={(event) =>
                onChange({ ...value, [toggle.key]: event.currentTarget.checked })
              }
            />
            <span>
              <strong>{toggle.label}</strong>
              <small>{toggle.text}</small>
            </span>
          </label>
        ))}
        <div className="settings-inline-actions">
          <span>当前状态：{value.status ?? "未检查"}</span>
          <button
            type="button"
            className="settings-secondary-action"
            onClick={onRecheckComputerUse}
          >
            重新检查
          </button>
        </div>
      </div>
    </div>
  );
}

function AboutPanel({
  about,
  onOpenLogs,
  onOpenDataDirectory,
}: {
  about?: SettingsAboutInfo;
  onOpenLogs?: () => void;
  onOpenDataDirectory?: () => void;
}) {
  const rows = [
    ["版本", about?.version ?? "0.1.0"],
    ["运行时", about?.runtime ?? "Tauri + React"],
    ["数据目录", about?.dataPath ?? "未连接运行时"],
    ["构建", about?.build ?? "development"],
  ];

  return (
    <div className="settings-panel settings-narrow-panel">
      <header className="settings-panel-header settings-panel-header-plain">
        <div>
          <p className="settings-kicker">About</p>
          <h2>关于</h2>
          <p>本地智能代理桌面，面向多会话编排、调度任务和可控工具执行。</p>
        </div>
      </header>
      <dl className="settings-definition-list">
        {rows.map(([label, value]) => (
          <div key={label}>
            <dt>{label}</dt>
            <dd>{value}</dd>
          </div>
        ))}
      </dl>
      <div className="settings-provider-actions">
        <button type="button" className="settings-secondary-action" onClick={onOpenLogs}>
          打开日志
        </button>
        <button
          type="button"
          className="settings-secondary-action"
          onClick={onOpenDataDirectory}
        >
          打开数据目录
        </button>
      </div>
    </div>
  );
}

function ProviderModal({
  mode,
  provider,
  onClose,
  onAddProvider,
  onEditProvider,
  onTestProviderConfig,
  providerBusy,
  providerTestBusy,
}: {
  mode: "add" | "edit";
  provider?: SettingsProvider;
  onClose: () => void;
  onAddProvider?: (payload: SettingsProviderPayload) => void | Promise<void>;
  onEditProvider?: (
    providerId: string,
    payload: SettingsProviderPayload,
  ) => void | Promise<void>;
  onTestProviderConfig?: (payload: SettingsProviderPayload) => void | Promise<void>;
  providerBusy: boolean;
  providerTestBusy: boolean;
}) {
  const [draft, setDraft] = useState(() => createProviderDraft(provider));
  const title = mode === "edit" ? "编辑服务商" : "添加服务商";
  const jsonPreview = buildProviderJson(draft);

  const updateDraft = (patch: Partial<ProviderFormDraft>) => {
    setDraft((current) => ({ ...current, ...patch }));
  };

  const applyPreset = (presetId: ProviderPresetId) => {
    const preset = providerPresets.find((item) => item.id === presetId);
    if (!preset) {
      return;
    }

    updateDraft({
      preset: preset.id,
      name: preset.id === "custom" ? draft.name : preset.label,
      endpoint: preset.endpoint,
      mainModel: preset.mainModel,
      haikuModel: preset.haikuModel,
      sonnetModel: preset.sonnetModel,
      opusModel: preset.opusModel,
    });
  };

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const payload = toProviderPayload(draft);

    if (mode === "edit" && provider) {
      await onEditProvider?.(provider.id, payload);
    } else {
      await onAddProvider?.(payload);
    }

    onClose();
  };

  return (
    <div className="settings-modal-backdrop">
      <section
        className="settings-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="settings-provider-modal-title"
      >
        <header className="settings-modal-header">
          <div>
            <p className="settings-kicker">Provider Registry</p>
            <h2 id="settings-provider-modal-title">{title}</h2>
          </div>
          <button
            className="settings-icon-button"
            type="button"
            onClick={onClose}
            aria-label={`关闭${title}`}
          >
            ×
          </button>
        </header>

        <div className="settings-preset-row" role="radiogroup" aria-label="服务商预设">
          {providerPresets.map((preset) => (
            <button
              key={preset.id}
              type="button"
              className={draft.preset === preset.id ? "is-active" : undefined}
              onClick={() => applyPreset(preset.id)}
              aria-pressed={draft.preset === preset.id}
            >
              {preset.label}
            </button>
          ))}
        </div>

        <form className="settings-provider-form" onSubmit={handleSubmit}>
          <label className="settings-field" htmlFor="provider-name">
            <span>名称 *</span>
            <input
              id="provider-name"
              value={draft.name}
              onChange={(event) => updateDraft({ name: event.currentTarget.value })}
              placeholder="DeepSeek"
              required
            />
          </label>
          <label className="settings-field" htmlFor="provider-note">
            <span>备注</span>
            <input
              id="provider-note"
              value={draft.note}
              onChange={(event) => updateDraft({ note: event.currentTarget.value })}
              placeholder="可选备注..."
            />
          </label>
          <label className="settings-field settings-form-wide" htmlFor="provider-endpoint">
            <span>接口地址</span>
            <input
              id="provider-endpoint"
              type="url"
              value={draft.endpoint}
              onChange={(event) => updateDraft({ endpoint: event.currentTarget.value })}
              placeholder="https://api.example.com/anthropic"
            />
          </label>
          <label className="settings-field settings-form-wide" htmlFor="provider-api-key">
            <span>API 密钥 *</span>
            <input
              id="provider-api-key"
              type="password"
              value={draft.apiKey}
              onChange={(event) => updateDraft({ apiKey: event.currentTarget.value })}
              placeholder="sk-..."
              autoComplete="off"
              required={mode === "add"}
            />
          </label>

          <fieldset className="settings-model-grid">
            <legend>模型映射</legend>
            <label className="settings-field" htmlFor="provider-main-model">
              <span>主模型 *</span>
              <input
                id="provider-main-model"
                value={draft.mainModel}
                onChange={(event) => updateDraft({ mainModel: event.currentTarget.value })}
                placeholder="DeepSeek-V3.2"
                required
              />
            </label>
            <label className="settings-field" htmlFor="provider-haiku-model">
              <span>Haiku 模型</span>
              <input
                id="provider-haiku-model"
                value={draft.haikuModel}
                onChange={(event) => updateDraft({ haikuModel: event.currentTarget.value })}
                placeholder="DeepSeek-V3.2"
              />
            </label>
            <label className="settings-field" htmlFor="provider-sonnet-model">
              <span>Sonnet 模型</span>
              <input
                id="provider-sonnet-model"
                value={draft.sonnetModel}
                onChange={(event) => updateDraft({ sonnetModel: event.currentTarget.value })}
                placeholder="DeepSeek-V3.2"
              />
            </label>
            <label className="settings-field" htmlFor="provider-opus-model">
              <span>Opus 模型</span>
              <input
                id="provider-opus-model"
                value={draft.opusModel}
                onChange={(event) => updateDraft({ opusModel: event.currentTarget.value })}
                placeholder="DeepSeek-R1"
              />
            </label>
          </fieldset>

          <label className="settings-field settings-form-wide" htmlFor="provider-test">
            <span>测试连接</span>
            <input
              id="provider-test"
              value={draft.testConnection}
              onChange={(event) => updateDraft({ testConnection: event.currentTarget.value })}
              placeholder="GET /models 或自定义探测模型"
            />
          </label>
          <label className="settings-field settings-form-wide" htmlFor="provider-json">
            <span>设置 JSON</span>
            <textarea
              id="provider-json"
              rows={6}
              value={draft.jsonConfig}
              onChange={(event) => updateDraft({ jsonConfig: event.currentTarget.value })}
              placeholder={'{\n  "timeout": 30000,\n  "stream": true\n}'}
            />
          </label>

          <div className="settings-json-preview settings-form-wide" aria-label="JSON 配置预览">
            <span>JSON 配置预览</span>
            <pre>{jsonPreview}</pre>
          </div>

          <footer className="settings-modal-actions settings-form-wide">
            <button type="button" className="settings-secondary-action" onClick={onClose}>
              取消
            </button>
            <button
              type="button"
              className="settings-secondary-action"
              disabled={providerTestBusy}
              onClick={() => void onTestProviderConfig?.(toProviderPayload(draft))}
            >
              {providerTestBusy ? "测试中..." : "测试连接"}
            </button>
            <button type="submit" className="settings-primary-action" disabled={providerBusy}>
              {providerBusy ? "保存中..." : mode === "edit" ? "保存" : "添加"}
            </button>
          </footer>
        </form>
      </section>
    </div>
  );
}

function SegmentedControl({
  label,
  name,
  value,
  options,
  onChange,
}: {
  label: string;
  name: string;
  value: string;
  options: Array<{ value: string; label: string }>;
  onChange: (value: string) => void;
}) {
  return (
    <fieldset className="settings-segmented">
      <legend>{label}</legend>
      {options.map((option) => (
        <label key={option.value}>
          <input
            type="radio"
            name={name}
            value={option.value}
            checked={value === option.value}
            onChange={() => onChange(option.value)}
          />
          <span>{option.label}</span>
        </label>
      ))}
    </fieldset>
  );
}

function ListOrEmpty({
  children,
  emptyTitle,
  emptyText,
}: {
  children: ReactNode;
  emptyTitle: string;
  emptyText: string;
}) {
  const items = Array.isArray(children) ? children.filter(Boolean) : children;
  const isEmpty = Array.isArray(items) ? items.length === 0 : !items;

  if (isEmpty) {
    return (
      <div className="settings-empty-state" role="status">
        <span aria-hidden="true">·</span>
        <strong>{emptyTitle}</strong>
        <p>{emptyText}</p>
      </div>
    );
  }

  return <div className="settings-list-stack">{items}</div>;
}

interface ProviderFormDraft {
  preset: ProviderPresetId;
  name: string;
  note: string;
  endpoint: string;
  apiKey: string;
  mainModel: string;
  haikuModel: string;
  sonnetModel: string;
  opusModel: string;
  testConnection: string;
  jsonConfig: string;
}

function createProviderDraft(provider?: SettingsProvider): ProviderFormDraft {
  const preset = providerPresets.find((item) => item.id === provider?.preset) ?? providerPresets[0];

  return {
    preset: provider?.preset ?? preset.id,
    name: provider?.name ?? preset.label,
    note: provider?.note ?? "",
    endpoint: provider?.endpoint ?? preset.endpoint,
    apiKey: "",
    mainModel: provider?.modelMapping?.main ?? provider?.models?.[0] ?? preset.mainModel,
    haikuModel: provider?.modelMapping?.haiku ?? preset.haikuModel,
    sonnetModel: provider?.modelMapping?.sonnet ?? preset.sonnetModel,
    opusModel: provider?.modelMapping?.opus ?? preset.opusModel,
    testConnection: "GET /models",
    jsonConfig: provider?.jsonConfig ?? "",
  };
}

function toProviderPayload(draft: ProviderFormDraft): SettingsProviderPayload {
  const modelMapping = [
    `main=${draft.mainModel}`,
    `haiku=${draft.haikuModel}`,
    `sonnet=${draft.sonnetModel}`,
    `opus=${draft.opusModel}`,
  ].join("\n");

  return {
    name: draft.name,
    note: draft.note,
    endpoint: draft.endpoint,
    apiKey: draft.apiKey,
    modelMapping,
    mainModel: draft.mainModel,
    haikuModel: draft.haikuModel,
    sonnetModel: draft.sonnetModel,
    opusModel: draft.opusModel,
    testConnection: draft.testConnection,
    jsonConfig: draft.jsonConfig,
    preset: draft.preset,
  };
}

function buildProviderJson(draft: ProviderFormDraft) {
  let extraConfig: Record<string, unknown> = {};

  if (draft.jsonConfig.trim()) {
    try {
      extraConfig = JSON.parse(draft.jsonConfig) as Record<string, unknown>;
    } catch {
      extraConfig = { rawConfig: draft.jsonConfig, parseError: "JSON 格式待修正" };
    }
  }

  return JSON.stringify(
    {
      env: {
        ANTHROPIC_AUTH_TOKEN: draft.apiKey ? "(your API key)" : "",
        ANTHROPIC_BASE_URL: draft.endpoint,
        ANTHROPIC_MODEL: draft.mainModel,
        ANTHROPIC_DEFAULT_HAIKU_MODEL: draft.haikuModel,
        ANTHROPIC_DEFAULT_SONNET_MODEL: draft.sonnetModel,
        ANTHROPIC_DEFAULT_OPUS_MODEL: draft.opusModel,
      },
      ...extraConfig,
    },
    null,
    2,
  );
}

function formatProviderModels(provider?: SettingsProvider) {
  if (!provider) {
    return "待设置";
  }

  if (provider.modelMapping) {
    return [
      provider.modelMapping.main,
      provider.modelMapping.haiku,
      provider.modelMapping.sonnet,
      provider.modelMapping.opus,
    ]
      .filter(Boolean)
      .join(" / ");
  }

  return provider.models?.join(" / ") ?? "待设置";
}

export default SettingsWorkspace;
