import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { formatStatusLabel } from "../../../copy";
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
type ProviderApiFormat = "openai-chat" | "openai-responses" | "anthropic-messages";
type ThemeMode = "light" | "dark" | "system";
type DensityMode = "comfortable" | "compact";
type RadiusMode = "sm" | "md" | "lg";
type MotionMode = "reduced" | "subtle" | "expressive";
type AccentColor = "cyan" | "violet" | "green" | "amber" | "rose";
type LanguageMode = "zh" | "en" | "auto";
type ReasoningEffort = "low" | "medium" | "high" | "max";

export interface SettingsProviderModelMapping {
  main: string;
  haiku: string;
  sonnet: string;
  opus: string;
}

export interface SettingsProviderTestResult {
  ok: boolean;
  status: string;
  message: string;
  model?: string;
  finishReason?: string;
  checkedAt?: number;
  errorSummary?: string | null;
  checkedEnvVarName?: string;
  details?: Record<string, unknown>;
}

export interface SettingsProvider {
  id: string;
  name: string;
  endpoint: string;
  apiFormat?: ProviderApiFormat;
  note?: string;
  models?: string[];
  status?: string;
  apiKeyMasked?: string;
  modelMapping?: Partial<SettingsProviderModelMapping>;
  jsonConfig?: string;
  preset?: ProviderPresetId;
  lastTest?: SettingsProviderTestResult;
}

export interface SettingsProviderFeedback {
  providerId?: string;
  tone: "success" | "danger" | "info";
  title: string;
  message: string;
  detail?: string;
}

export interface SettingsProviderPayload {
  name: string;
  note: string;
  endpoint: string;
  apiFormat: ProviderApiFormat;
  apiKey: string;
  apiKeyEnvVarName?: string;
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
  density: DensityMode;
  radius: RadiusMode;
  motion: MotionMode;
  accentColor: AccentColor;
  transparency: number;
  fontScale: number;
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
  systemPrompt?: string;
  toolWhitelist?: string[];
  isBuiltin?: boolean;
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
  onEditProvider?: (providerId: string, payload: SettingsProviderPayload) => void | Promise<void>;
  onTestProvider?: (providerId?: string) => void | Promise<void>;
  onTestProviderConfig?: (
    payload: SettingsProviderPayload,
  ) => void | SettingsProviderTestResult | Promise<void | SettingsProviderTestResult>;
  onSaveProvider?: (providerId?: string) => void | Promise<void>;
  providerBusy?: boolean;
  providerTestBusy?: boolean;
  providerFeedback?: SettingsProviderFeedback | null;
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
  onRefreshSkills?: () => void | Promise<void>;
  onOpenSkillsFolder?: () => void;
  computerUse?: SettingsComputerUseConfig;
  onComputerUseChange?: (next: SettingsComputerUseConfig) => void;
  onRecheckComputerUse?: () => void | Promise<void>;
  workspaceFocus?: string | null;
  workspaceFocusBusy?: boolean;
  onSaveWorkspaceFocus?: (focus: string) => void | Promise<void>;
  workspaceMemorySummary?: string | null;
  workspaceMemoryBusy?: boolean;
  onClearWorkspaceMemory?: () => void | Promise<void>;
  about?: SettingsAboutInfo;
  onOpenLogs?: () => void;
  onOpenDataDirectory?: () => void;
}

const sections: Array<{ id: SettingsSection; label: string; eyebrow: string }> = [
  { id: "providers", label: "模型供应商", eyebrow: "API" },
  { id: "permissions", label: "权限模式", eyebrow: "模式" },
  { id: "general", label: "外观偏好", eyebrow: "界面" },
  { id: "im", label: "消息桥接", eyebrow: "桥接" },
  { id: "agents", label: "智能体", eyebrow: "队列" },
  { id: "skills", label: "技能库", eyebrow: "技能" },
  { id: "computer", label: "电脑操作", eyebrow: "控制" },
  { id: "about", label: "关于", eyebrow: "构建" },
];

const fallbackProviders: SettingsProvider[] = [
  {
    id: "default",
    name: "默认供应商",
    endpoint: "未配置",
    note: "运行正式任务前，请先连接一个 OpenAI 兼容供应商。",
    models: ["未配置模型"],
    status: "not_configured",
    preset: "custom",
  },
];

const providerPresets = [
  {
    id: "deepseek",
    label: "DeepSeek",
    endpoint: "https://api.deepseek.com/anthropic",
    apiFormat: "openai-chat",
    mainModel: "DeepSeek-V3.2",
    haikuModel: "DeepSeek-V3.2",
    sonnetModel: "DeepSeek-V3.2",
    opusModel: "DeepSeek-R1",
  },
  {
    id: "zhipu",
    label: "Zhipu GLM",
    endpoint: "https://open.bigmodel.cn/api/paas/v4",
    apiFormat: "openai-chat",
    mainModel: "glm-4-plus",
    haikuModel: "glm-4-air",
    sonnetModel: "glm-4-plus",
    opusModel: "glm-4-plus",
  },
  {
    id: "kimi",
    label: "Kimi",
    endpoint: "https://api.moonshot.cn/v1",
    apiFormat: "openai-chat",
    mainModel: "moonshot-v1-128k",
    haikuModel: "moonshot-v1-8k",
    sonnetModel: "moonshot-v1-32k",
    opusModel: "moonshot-v1-128k",
  },
  {
    id: "minimax",
    label: "MiniMax",
    endpoint: "https://api.minimax.chat/v1",
    apiFormat: "openai-chat",
    mainModel: "MiniMax-M2.7-highspeed",
    haikuModel: "MiniMax-M2.7-highspeed",
    sonnetModel: "MiniMax-M2.7-highspeed",
    opusModel: "MiniMax-M2.7-highspeed",
  },
  {
    id: "custom",
    label: "自定义",
    endpoint: "",
    apiFormat: "openai-chat",
    mainModel: "",
    haikuModel: "",
    sonnetModel: "",
    opusModel: "",
  },
] satisfies Array<{
  id: ProviderPresetId;
  label: string;
  endpoint: string;
  apiFormat: ProviderApiFormat;
  mainModel: string;
  haikuModel: string;
  sonnetModel: string;
  opusModel: string;
}>;

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

const permissionModes = [
  {
    id: "ask",
    title: "请求审批",
    text: "执行命令、编辑文件、访问网络或高风险工具调用前先请求确认。",
  },
  {
    id: "edits",
    title: "允许工作区编辑",
    text: "允许智能体编辑工作区内文件，高风险动作仍然需要确认。",
  },
  {
    id: "plan",
    title: "先规划",
    text: "进入实现前先保持在可审阅的规划模式。",
  },
  {
    id: "skip",
    title: "自主执行",
    text: "为可信本地任务减少审批提示，仅建议在受控环境中使用。",
  },
];

const fallbackGeneral: SettingsGeneralConfig = {
  theme: "dark",
  density: "comfortable",
  radius: "md",
  motion: "subtle",
  accentColor: "cyan",
  transparency: 0.78,
  fontScale: 1,
  language: "auto",
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
  status: "ready",
};

interface ProviderFormDraft {
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

function isDirectApiKey(value: string) {
  return value.trim().startsWith("sk-");
}

function parseProviderConfigText(value: string) {
  const raw = value.trim();
  if (!raw) {
    return { env: {} as Record<string, string>, apiKeyEnvVarName: undefined as string | undefined };
  }

  try {
    const parsed = JSON.parse(raw) as unknown;
    const record = parsed && typeof parsed === "object" ? (parsed as Record<string, unknown>) : {};
    const envRecord = record.env && typeof record.env === "object"
      ? (record.env as Record<string, unknown>)
      : record;
    const env = Object.fromEntries(
      Object.entries(envRecord)
        .filter(([, item]) => typeof item === "string" && item.trim())
        .map(([key, item]) => [key, String(item).trim()]),
    );
    return {
      env,
      apiKeyEnvVarName: providerApiKeyEnvKeys.find((key) => key in env),
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

function createProviderDraft(provider?: SettingsProvider): ProviderFormDraft {
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

function toProviderPayload(draft: ProviderFormDraft): SettingsProviderPayload {
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

function formatProviderModels(provider?: SettingsProvider) {
  if (!provider) {
    return "未配置模型";
  }
  const mapping = provider.modelMapping;
  if (mapping?.main || mapping?.sonnet || mapping?.opus) {
    return [mapping.main, mapping.haiku, mapping.sonnet, mapping.opus].filter(Boolean).join(" / ");
  }
  return provider.models?.join(" / ") || "未配置模型";
}

function formatProviderSuccessDetail(result: SettingsProviderTestResult) {
  return [result.model, result.finishReason].filter(Boolean).join(" / ");
}

function formatProviderTestResult(result: SettingsProviderTestResult) {
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

function buildProviderJson(draft: ProviderFormDraft) {
  return JSON.stringify(
    {
      env: {
        LOCAL_AGENT_PROVIDER_BASE_URL: draft.endpoint || "(供应商 Base URL)",
        LOCAL_AGENT_PROVIDER_API_FORMAT: draft.apiFormat,
        LOCAL_AGENT_PROVIDER_MODEL: draft.mainModel || "(模型 ID)",
        LOCAL_AGENT_PROVIDER_API_KEY: draft.apiKey ? "(已在表单中提供)" : "(在运行时环境中设置)",
      },
    },
    null,
    2,
  );
}

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
  general,
  onGeneralChange,
  im,
  onIMChange,
  onTestIM,
  agents = [],
  onAgentToggle,
  onAddAgent,
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
          {section === "agents" ? <AgentsPanel agents={agents} onAgentToggle={onAgentToggle} onAddAgent={onAddAgent} /> : null}
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

function ProvidersPanel({
  providers,
  activeProvider,
  activeProviderId,
  selectedProviderId,
  providerFeedback,
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
  activeProviderId?: string;
  selectedProviderId: string;
  providerFeedback?: SettingsProviderFeedback | null;
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
          <p className="settings-kicker">模型供应商</p>
          <h2>供应商控制台</h2>
          <p>管理 API 地址、模型映射、连接检查和当前运行时供应商。</p>
        </div>
        <button className="settings-primary-action" type="button" onClick={onAddProvider} aria-label="添加供应商">
          添加供应商
        </button>
      </header>

      <div className="settings-provider-grid">
        <div className="settings-provider-list" aria-label="供应商列表">
          {providers.map((provider) => (
            <button
              key={provider.id}
              type="button"
              className={provider.id === selectedProviderId ? "settings-provider-item is-active" : "settings-provider-item"}
              onClick={() => onSelectProvider(provider.id)}
              aria-current={provider.id === selectedProviderId ? "page" : undefined}
              aria-label={`选择供应商 ${provider.name}`}
            >
              <span className="settings-provider-dot" aria-hidden="true" />
              <span className="settings-provider-copy">
                <strong>{provider.name}</strong>
                <small>
                  {provider.endpoint}
                  {provider.models?.[0] ? ` / ${provider.models[0]}` : ""}
                </small>
                {provider.note ? <small>{provider.note}</small> : null}
                {provider.lastTest ? <small>{`最近测试：${formatStatusLabel(provider.lastTest.status)}`}</small> : null}
                {provider.lastTest?.ok && formatProviderSuccessDetail(provider.lastTest) ? (
                  <small>{formatProviderSuccessDetail(provider.lastTest)}</small>
                ) : null}
              </span>
              {provider.id === activeProviderId ? (
                <em className="settings-provider-badge">当前</em>
              ) : provider.status ? (
                <em>{formatStatusLabel(provider.status)}</em>
              ) : null}
            </button>
          ))}
        </div>

        <article className="settings-provider-detail" aria-label="已选供应商详情">
          <div>
            <p className="settings-kicker">已选供应商</p>
            <h3>{activeProvider?.name ?? "未选择供应商"}</h3>
          </div>
          {activeProvider ? (
            <ProviderStateSummary
              provider={activeProvider}
              isActive={activeProvider.id === activeProviderId}
              feedback={
                providerFeedback && (!providerFeedback.providerId || providerFeedback.providerId === activeProvider.id)
                  ? providerFeedback
                  : null
              }
            />
          ) : null}
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
              <dd>{activeProvider?.apiKeyMasked ?? "由运行时环境变量提供。"}</dd>
            </div>
            {activeProvider?.lastTest ? <ProviderTestSummaryRows result={activeProvider.lastTest} /> : null}
          </dl>
          <div className="settings-provider-actions">
            <button type="button" className="settings-secondary-action" onClick={onEditProvider}>编辑</button>
            <button type="button" className="settings-secondary-action" onClick={onTestProvider} disabled={providerTestBusy}>
              {providerTestBusy ? "测试中..." : "测试连接"}
            </button>
            <button type="button" className="settings-primary-action" onClick={onSaveProvider} disabled={providerBusy}>
              {providerBusy ? "保存中..." : "保存"}
            </button>
          </div>
        </article>
      </div>
    </div>
  );
}

function ProviderStateSummary({
  provider,
  isActive,
  feedback,
}: {
  provider: SettingsProvider;
  isActive: boolean;
  feedback?: SettingsProviderFeedback | null;
}) {
  const currentModel = provider.modelMapping?.main || provider.models?.[0] || "未配置";
  const lastTest = provider.lastTest;
  const connectionLabel = lastTest ? (lastTest.ok ? "测试通过" : "测试失败") : "尚未测试";
  const connectionDetail = lastTest
    ? lastTest.ok
      ? formatProviderSuccessDetail(lastTest) || lastTest.message
      : lastTest.errorSummary || lastTest.message
    : "开始正式任务前，请先运行测试连接验证该供应商。";

  return (
    <div className="settings-provider-state" aria-label="供应商状态摘要">
      <div>
        <span className={isActive ? "settings-status-pill is-active" : "settings-status-pill"}>
          {isActive ? "当前供应商" : "仅已选中"}
        </span>
        <strong>{currentModel}</strong>
        <small>当前模型</small>
      </div>
      <div>
        <span className={lastTest?.ok ? "settings-status-pill is-pass" : lastTest ? "settings-status-pill is-fail" : "settings-status-pill"}>
          {connectionLabel}
        </span>
        <strong>{connectionDetail}</strong>
        <small>连接状态</small>
      </div>
      {feedback ? (
        <div className={`settings-provider-feedback is-${feedback.tone}`} role="status">
          <strong>{feedback.title}</strong>
          <span>{feedback.message}</span>
          {feedback.detail ? <small>{feedback.detail}</small> : null}
        </div>
      ) : null}
    </div>
  );
}

function ProviderTestSummaryRows({ result }: { result: SettingsProviderTestResult }) {
  const successDetail = formatProviderSuccessDetail(result);
  const failureDetail = result.errorSummary ?? result.message;

  return (
    <>
      <div>
        <dt>最近测试</dt>
        <dd>{`最近测试：${formatStatusLabel(result.status)}`}</dd>
      </div>
      {result.ok ? (
        <div>
          <dt>模型 / 结束</dt>
          <dd>{successDetail || result.message}</dd>
        </div>
      ) : (
        <div>
          <dt>失败原因</dt>
          <dd>{failureDetail}</dd>
        </div>
      )}
      {result.checkedAt ? (
        <div>
          <dt>检查时间</dt>
          <dd>{new Date(result.checkedAt).toLocaleString()}</dd>
        </div>
      ) : null}
    </>
  );
}

function PermissionsPanel({ selectedMode, onSelectMode }: { selectedMode: string; onSelectMode: (mode: string) => void }) {
  return (
    <div className="settings-panel settings-narrow-panel">
      <header className="settings-panel-header settings-panel-header-plain">
        <div>
          <p className="settings-kicker">权限模式</p>
          <h2>权限模式</h2>
          <p>选择运行时在编辑、命令和高风险动作前如何请求审批。</p>
        </div>
      </header>
      <div className="settings-card-stack" role="radiogroup" aria-label="权限模式">
        {permissionModes.map((mode) => (
          <label key={mode.id} className={selectedMode === mode.id ? "settings-choice-card is-selected" : "settings-choice-card"}>
            <input type="radio" name="permission-mode" checked={selectedMode === mode.id} onChange={() => onSelectMode(mode.id)} />
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

function GeneralPanel({ value, onChange }: { value: SettingsGeneralConfig; onChange: (next: SettingsGeneralConfig) => void }) {
  return (
    <div className="settings-panel settings-narrow-panel">
      <header className="settings-panel-header settings-panel-header-plain">
        <div>
          <p className="settings-kicker">通用</p>
          <h2>外观偏好</h2>
          <p>设置工作台主题、密度、强调色、动效、语言、推理力度和 Web 预检行为。</p>
        </div>
      </header>
      <div className="settings-form-stack">
        <SegmentedControl
          label="主题"
          name="theme"
          value={value.theme}
          options={[
            { value: "light", label: "浅色" },
            { value: "dark", label: "深色" },
            { value: "system", label: "跟随系统" },
          ]}
          onChange={(theme) => onChange({ ...value, theme: theme as ThemeMode })}
        />
        <SegmentedControl
          label="密度"
          name="density"
          value={value.density}
          options={[
            { value: "comfortable", label: "舒适" },
            { value: "compact", label: "紧凑" },
          ]}
          onChange={(density) => onChange({ ...value, density: density as DensityMode })}
        />
        <SegmentedControl
          label="圆角"
          name="radius"
          value={value.radius}
          options={[
            { value: "sm", label: "小" },
            { value: "md", label: "中" },
            { value: "lg", label: "大" },
          ]}
          onChange={(radius) => onChange({ ...value, radius: radius as RadiusMode })}
        />
        <SegmentedControl
          label="动效"
          name="motion"
          value={value.motion}
          options={[
            { value: "reduced", label: "减少" },
            { value: "subtle", label: "轻微" },
            { value: "expressive", label: "丰富" },
          ]}
          onChange={(motion) => onChange({ ...value, motion: motion as MotionMode })}
        />
        <SegmentedControl
          label="强调色"
          name="accent"
          value={value.accentColor}
          options={[
            { value: "cyan", label: "青色" },
            { value: "violet", label: "紫色" },
            { value: "green", label: "绿色" },
            { value: "amber", label: "琥珀" },
            { value: "rose", label: "玫瑰" },
          ]}
          onChange={(accentColor) => onChange({ ...value, accentColor: accentColor as AccentColor })}
        />
        <label className="settings-field" htmlFor="appearance-transparency">
          <span>透明度 {Math.round(value.transparency * 100)}%</span>
          <input
            id="appearance-transparency"
            type="range"
            min="0.58"
            max="0.96"
            step="0.02"
            value={value.transparency}
            onChange={(event) => onChange({ ...value, transparency: Number(event.currentTarget.value) })}
          />
        </label>
        <label className="settings-field" htmlFor="appearance-font-scale">
          <span>字体缩放 {Math.round(value.fontScale * 100)}%</span>
          <input
            id="appearance-font-scale"
            type="range"
            min="0.92"
            max="1.12"
            step="0.02"
            value={value.fontScale}
            onChange={(event) => onChange({ ...value, fontScale: Number(event.currentTarget.value) })}
          />
        </label>
        <SegmentedControl
          label="语言"
          name="language"
          value={value.language}
          options={[
            { value: "en", label: "英文" },
            { value: "zh", label: "中文" },
            { value: "auto", label: "自动" },
          ]}
          onChange={(language) => onChange({ ...value, language: language as LanguageMode })}
        />
        <SegmentedControl
          label="推理力度"
          name="reasoning"
          value={value.reasoningEffort}
          options={[
            { value: "low", label: "低" },
            { value: "medium", label: "中" },
            { value: "high", label: "高" },
            { value: "max", label: "最高" },
          ]}
          onChange={(reasoningEffort) => onChange({ ...value, reasoningEffort: reasoningEffort as ReasoningEffort })}
        />
        <label className="settings-toggle-card" htmlFor="webfetch-preflight">
          <input
            id="webfetch-preflight"
            type="checkbox"
            checked={value.webFetchPreflight}
            onChange={(event) => onChange({ ...value, webFetchPreflight: event.currentTarget.checked })}
          />
          <span>
            <strong>跳过 WebFetch 域名预检</strong>
            <small>为了本地运行时兼容建议保持开启，除非需要上游安全检查。</small>
          </span>
        </label>
      </div>
    </div>
  );
}

function IMPanel({ value, onChange, onTestIM }: { value: SettingsIMConfig; onChange: (next: SettingsIMConfig) => void; onTestIM?: () => void | Promise<void> }) {
  return (
    <div className="settings-panel settings-narrow-panel">
      <header className="settings-panel-header settings-panel-header-plain">
        <div>
          <p className="settings-kicker">消息桥接</p>
          <h2>消息桥接</h2>
          <p>连接飞书、企业微信或自定义 Webhook，让外部消息渠道进入会话。</p>
        </div>
      </header>
      <div className="settings-form-stack">
        <label className="settings-toggle-card" htmlFor="im-enabled">
          <input id="im-enabled" type="checkbox" checked={value.enabled} onChange={(event) => onChange({ ...value, enabled: event.currentTarget.checked })} />
          <span>
            <strong>启用消息网关</strong>
            <small>关闭后会保留配置，但停止接收外部消息。</small>
          </span>
        </label>
        <label className="settings-field" htmlFor="im-provider">
          <span>渠道</span>
          <select id="im-provider" value={value.provider} onChange={(event) => onChange({ ...value, provider: event.currentTarget.value })}>
            <option value="feishu">飞书</option>
            <option value="wecom">企业微信</option>
            <option value="custom">自定义网关</option>
          </select>
        </label>
        <label className="settings-field" htmlFor="im-webhook">
          <span>Webhook 地址</span>
          <input id="im-webhook" type="url" value={value.webhookUrl} placeholder="https://example.com/im/webhook" onChange={(event) => onChange({ ...value, webhookUrl: event.currentTarget.value })} />
        </label>
        <SegmentedControl
          label="默认回复模式"
          name="im-reply-mode"
          value={value.defaultReplyMode}
          options={[
            { value: "manual", label: "手动" },
            { value: "auto", label: "自动回复" },
            { value: "silent", label: "静默记录" },
          ]}
          onChange={(defaultReplyMode) => onChange({ ...value, defaultReplyMode: defaultReplyMode as SettingsIMConfig["defaultReplyMode"] })}
        />
        <div className="settings-inline-actions">
          <span>签名密钥：{value.signingSecretSet ? "已配置" : "未配置"}</span>
          <button type="button" className="settings-secondary-action" onClick={onTestIM} disabled={!onTestIM} aria-label="测试 IM 连接">测试消息桥接</button>
          {!onTestIM ? <small className="settings-action-note">当前桌面版本尚未接入运行时消息桥接测试。</small> : null}
        </div>
      </div>
    </div>
  );
}

function ListOrEmpty({ children, emptyTitle, emptyText }: { children: ReactNode; emptyTitle: string; emptyText: string }) {
  const childArray = Array.isArray(children) ? children : [children];
  if (childArray.filter(Boolean).length === 0) {
    return (
      <div className="settings-empty-state">
        <span aria-hidden="true">·</span>
        <strong>{emptyTitle}</strong>
        <small>{emptyText}</small>
      </div>
    );
  }
  return <div className="settings-form-stack">{children}</div>;
}

function AgentsPanel({ agents, onAgentToggle, onAddAgent }: { agents: SettingsAgentConfig[]; onAgentToggle?: (agentId: string, enabled: boolean) => void; onAddAgent?: () => void }) {
  return (
    <div className="settings-panel">
      <header className="settings-panel-header">
        <div>
          <p className="settings-kicker">智能体队列</p>
          <h2>智能体</h2>
          <p>管理常驻智能体、工作目录和默认权限策略。</p>
        </div>
        <button type="button" className="settings-primary-action" onClick={onAddAgent} disabled={!onAddAgent} title={!onAddAgent ? "智能体配置管理尚未可用。" : undefined} aria-label="添加智能体">添加智能体</button>
      </header>
      {!onAddAgent || !onAgentToggle ? (
        <p className="settings-action-note settings-panel-note">运行时智能体管理定义完成前，智能体配置暂时只读。</p>
      ) : null}
      <ListOrEmpty emptyTitle="暂无智能体" emptyText="运行时集成后，常驻智能体会显示在这里。">
        {agents.map((agent) => (
          <label key={agent.id} className="settings-row-card">
            <input type="checkbox" checked={agent.enabled} disabled={!onAgentToggle} onChange={(event) => onAgentToggle?.(agent.id, event.currentTarget.checked)} />
            <span>
              <strong>{agent.name}</strong>
              <small>{agent.description ?? "暂无描述"}</small>
              <small>{agent.cwd ?? "未设置工作目录"}</small>
            </span>
            <em>{agent.permissionMode ?? "继承默认"}</em>
          </label>
        ))}
      </ListOrEmpty>
    </div>
  );
}

function SkillsPanel({ skills, onRefreshSkills, onOpenSkillsFolder }: { skills: SettingsSkillConfig[]; onRefreshSkills?: () => void | Promise<void>; onOpenSkillsFolder?: () => void }) {
  return (
    <div className="settings-panel">
      <header className="settings-panel-header">
        <div>
          <p className="settings-kicker">技能库</p>
          <h2>技能</h2>
          <p>技能为本地智能体扩展专项工作流。已安装预设会从 ~/.codex/skills/ 提供给运行时。</p>
        </div>
        <div className="settings-header-actions">
          <button type="button" className="settings-secondary-action" onClick={onOpenSkillsFolder} disabled={!onOpenSkillsFolder} title={!onOpenSkillsFolder ? "技能目录打开功能尚未接入。" : undefined} aria-label="打开目录">打开目录</button>
          <button type="button" className="settings-primary-action" onClick={onRefreshSkills} disabled={!onRefreshSkills}>刷新技能</button>
        </div>
      </header>
      {!onOpenSkillsFolder ? <p className="settings-action-note settings-panel-note">打开目录还在等待桌面 shell 桥接；刷新仍会使用运行时技能注册表。</p> : null}
      <ListOrEmpty emptyTitle="暂无已安装技能" emptyText="将技能放入 ~/.codex/skills/ 后会在这里显示。">
        {skills.map((skill) => (
          <article key={skill.id} className="settings-row-card settings-skill-card">
            <span className="settings-skill-marker" aria-hidden="true" />
            <span>
              <strong>{skill.name}</strong>
              <small>{skill.description ?? "暂无描述"}</small>
              <small>{skill.path ?? "未记录路径"}</small>
            </span>
            <em>{skill.enabled ? "可用" : "不可用"}</em>
            {skill.updateAvailable ? <em>有更新</em> : null}
          </article>
        ))}
      </ListOrEmpty>
    </div>
  );
}

function ComputerUsePanel({ value, onChange, onRecheckComputerUse }: { value: SettingsComputerUseConfig; onChange: (next: SettingsComputerUseConfig) => void; onRecheckComputerUse?: () => void | Promise<void> }) {
  const toggles: Array<{ key: keyof SettingsComputerUseConfig; label: string; text: string }> = [
    { key: "screenshot", label: "屏幕观察", text: "允许截图用于视觉任务上下文。" },
    { key: "browserAutomation", label: "浏览器自动化", text: "允许打开并控制浏览器会话。" },
    { key: "clipboardAccess", label: "剪贴板访问", text: "允许读取和写入剪贴板。" },
    { key: "systemKeyCombos", label: "系统快捷键", text: "允许使用系统级键盘组合。" },
    { key: "sensitiveActionConfirm", label: "敏感动作确认", text: "破坏性或外部动作前需要确认。" },
  ];

  return (
    <div className="settings-panel settings-narrow-panel">
      <header className="settings-panel-header settings-panel-header-plain">
        <div>
          <p className="settings-kicker">电脑操作</p>
          <h2>电脑操作</h2>
          <p>控制本地桌面能力，例如截图、浏览器自动化、剪贴板访问和高风险确认。</p>
        </div>
      </header>
      <div className="settings-form-stack">
        {toggles.map((toggle) => (
          <label key={toggle.key} className="settings-toggle-card">
            <input type="checkbox" checked={Boolean(value[toggle.key])} onChange={(event) => onChange({ ...value, [toggle.key]: event.currentTarget.checked })} />
            <span>
              <strong>{toggle.label}</strong>
              <small>{toggle.text}</small>
            </span>
          </label>
        ))}
        <div className="settings-inline-actions">
          <span>状态：{value.status ? formatStatusLabel(value.status) : "未检查"}</span>
          <button type="button" className="settings-secondary-action" onClick={onRecheckComputerUse} disabled={!onRecheckComputerUse} aria-label="重新检查">重新检查</button>
          {!onRecheckComputerUse ? <small className="settings-action-note">桌面权限重新检查尚未实现。</small> : null}
        </div>
      </div>
    </div>
  );
}

function AboutPanel({
  about,
  workspaceFocus,
  workspaceFocusBusy,
  onSaveWorkspaceFocus,
  workspaceMemorySummary,
  workspaceMemoryBusy,
  onClearWorkspaceMemory,
  onOpenLogs,
  onOpenDataDirectory,
}: {
  about?: SettingsAboutInfo;
  workspaceFocus?: string | null;
  workspaceFocusBusy: boolean;
  onSaveWorkspaceFocus?: (focus: string) => void | Promise<void>;
  workspaceMemorySummary?: string | null;
  workspaceMemoryBusy: boolean;
  onClearWorkspaceMemory?: () => void | Promise<void>;
  onOpenLogs?: () => void;
  onOpenDataDirectory?: () => void;
}) {
  const rows = [
    ["版本", about?.version ?? "0.1.0"],
    ["运行时", about?.runtime ?? "Tauri + React"],
    ["数据路径", about?.dataPath ?? "未连接"],
    ["构建", about?.build ?? "development"],
  ];
  const memoryPreview = workspaceMemorySummary
    ?.split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => line && line !== "Project memory:" && line !== "项目记忆：")
    .slice(0, 4)
    .join("\n");
  const [focusDraft, setFocusDraft] = useState(workspaceFocus ?? "");

  useEffect(() => {
    setFocusDraft(workspaceFocus ?? "");
  }, [workspaceFocus]);

  return (
    <div className="settings-panel settings-narrow-panel">
      <header className="settings-panel-header settings-panel-header-plain">
        <div>
          <p className="settings-kicker">关于</p>
          <h2>关于</h2>
          <p>本地智能体工作台，用于多会话编排、定时任务和受控工具执行。</p>
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
        <button type="button" className="settings-secondary-action" onClick={onOpenLogs} disabled={!onOpenLogs} title={!onOpenLogs ? "打开日志需要桌面 shell 桥接。" : undefined} aria-label="打开日志">打开日志</button>
        <button type="button" className="settings-secondary-action" onClick={onOpenDataDirectory} disabled={!onOpenDataDirectory} title={!onOpenDataDirectory ? "打开数据目录需要桌面 shell 桥接。" : undefined} aria-label="打开数据目录">打开数据目录</button>
      </div>
      {!onOpenLogs || !onOpenDataDirectory ? (
        <p className="settings-action-note settings-panel-note">打开本地目录还在等待 Tauri shell 桥接；上方路径可用于手动检查。</p>
      ) : null}
      <section className="settings-memory-card" aria-label="项目焦点">
        <div>
          <p className="settings-kicker">上下文</p>
          <h3>项目焦点</h3>
          <p>将项目目标、边界和偏好固定到新任务上下文中。</p>
        </div>
        <label className="settings-field" htmlFor="project-focus">
          <span>固定焦点</span>
          <textarea
            id="project-focus"
            aria-label="固定焦点"
            value={focusDraft}
            rows={4}
            placeholder="示例：优先保障稳定的本地编码智能体工作流，避免无关重构。"
            onChange={(event) => setFocusDraft(event.currentTarget.value)}
          />
        </label>
        <div className="settings-provider-actions">
          <button
            type="button"
            className="settings-primary-action"
            aria-label="保存项目焦点"
            onClick={() => void onSaveWorkspaceFocus?.(focusDraft)}
            disabled={workspaceFocusBusy || !onSaveWorkspaceFocus}
          >
            {workspaceFocusBusy ? "保存中..." : "保存项目焦点"}
          </button>
          <button
            type="button"
            className="settings-secondary-action"
            aria-label="清空项目焦点"
            onClick={() => {
              setFocusDraft("");
              void onSaveWorkspaceFocus?.("");
            }}
            disabled={workspaceFocusBusy || !onSaveWorkspaceFocus || !focusDraft.trim()}
          >
            清空项目焦点
          </button>
        </div>
      </section>
      <section className="settings-memory-card" aria-label="项目记忆">
        <div>
          <p className="settings-kicker">上下文</p>
          <h3>项目记忆</h3>
          <p>持久化项目笔记可加入未来模型上下文。</p>
        </div>
        <pre>{memoryPreview || "暂无项目记忆。"}</pre>
        <button
          type="button"
          className="settings-secondary-action"
          aria-label="清空项目记忆"
          onClick={() => void onClearWorkspaceMemory?.()}
          disabled={workspaceMemoryBusy || !workspaceMemorySummary?.trim() || !onClearWorkspaceMemory}
        >
          {workspaceMemoryBusy ? "清空中..." : "清空项目记忆"}
        </button>
      </section>
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
  onEditProvider?: (providerId: string, payload: SettingsProviderPayload) => void | Promise<void>;
  onTestProviderConfig?: (payload: SettingsProviderPayload) => void | SettingsProviderTestResult | Promise<void | SettingsProviderTestResult>;
  providerBusy: boolean;
  providerTestBusy: boolean;
}) {
  const [draft, setDraft] = useState(() => createProviderDraft(provider));
  const [testResult, setTestResult] = useState<SettingsProviderTestResult | null>(null);
  const [testError, setTestError] = useState<string | null>(null);
  const title = mode === "edit" ? "编辑供应商" : "添加供应商";
  const parsedConfig = parseProviderConfigText(draft.jsonConfig);
  const detectedApiKeyEnvVarName =
    !draft.apiKey.trim() || !isDirectApiKey(draft.apiKey)
      ? parsedConfig.apiKeyEnvVarName
      : undefined;

  const updateDraft = (patch: Partial<ProviderFormDraft>) => {
    setTestResult(null);
    setTestError(null);
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
      apiFormat: preset.apiFormat,
      mainModel: preset.mainModel,
      haikuModel: preset.haikuModel,
      sonnetModel: preset.sonnetModel,
      opusModel: preset.opusModel,
    });
  };

  const handleTestProvider = async () => {
    setTestResult(null);
    setTestError(null);
    try {
      const result = await onTestProviderConfig?.(toProviderPayload(draft));
      if (result) {
        setTestResult(result);
      }
    } catch (reason) {
      setTestError(reason instanceof Error ? reason.message : String(reason));
    }
  };

  const handleSubmit = async (event: FormEvent) => {
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
    <div className="settings-modal-backdrop" role="presentation">
      <form className="settings-modal" role="dialog" aria-modal="true" aria-labelledby="provider-modal-title" onSubmit={handleSubmit}>
        <header className="settings-modal-header">
          <div>
            <p className="settings-kicker">供应商</p>
            <h2 id="provider-modal-title">{title}</h2>
          </div>
          <button type="button" className="settings-icon-button" onClick={onClose} aria-label="关闭">x</button>
        </header>

        <div className="settings-provider-form">
          <div className="settings-preset-row" aria-label="供应商预设">
            {providerPresets.map((preset) => (
              <button
                key={preset.id}
                type="button"
                className={draft.preset === preset.id ? "is-active" : undefined}
                onClick={() => applyPreset(preset.id)}
              >
                {preset.label}
              </button>
            ))}
          </div>

          <label className="settings-field" htmlFor="provider-name">
            <span>名称 *</span>
            <input id="provider-name" value={draft.name} onChange={(event) => updateDraft({ name: event.currentTarget.value })} required />
          </label>
          <label className="settings-field" htmlFor="provider-note">
            <span>备注</span>
            <input id="provider-note" value={draft.note} onChange={(event) => updateDraft({ note: event.currentTarget.value })} placeholder="可选备注..." />
          </label>
          <label className="settings-field" htmlFor="provider-endpoint">
            <span>接口地址</span>
            <input id="provider-endpoint" value={draft.endpoint} onChange={(event) => updateDraft({ endpoint: event.currentTarget.value })} placeholder="https://api.example.com/v1" />
          </label>
          <label className="settings-field" htmlFor="provider-api-format">
            <span>API 格式</span>
            <select id="provider-api-format" value={draft.apiFormat} onChange={(event) => updateDraft({ apiFormat: event.currentTarget.value as ProviderApiFormat })}>
              <option value="openai-chat">OpenAI Chat Completions</option>
              <option value="openai-responses">OpenAI Responses API</option>
              <option value="anthropic-messages">Anthropic Messages</option>
            </select>
          </label>
          <label className="settings-field" htmlFor="provider-api-key">
            <span>API 密钥</span>
            <input id="provider-api-key" type="password" value={draft.apiKey} onChange={(event) => updateDraft({ apiKey: event.currentTarget.value })} placeholder="sk-..." />
          </label>

          <fieldset className="settings-model-grid">
            <legend>模型映射</legend>
            <label className="settings-field" htmlFor="provider-main-model">
              <span>主模型 *</span>
              <input id="provider-main-model" value={draft.mainModel} onChange={(event) => updateDraft({ mainModel: event.currentTarget.value })} required />
            </label>
            <label className="settings-field" htmlFor="provider-haiku-model">
              <span>Haiku 模型</span>
              <input id="provider-haiku-model" value={draft.haikuModel} onChange={(event) => updateDraft({ haikuModel: event.currentTarget.value })} />
            </label>
            <label className="settings-field" htmlFor="provider-sonnet-model">
              <span>Sonnet 模型</span>
              <input id="provider-sonnet-model" value={draft.sonnetModel} onChange={(event) => updateDraft({ sonnetModel: event.currentTarget.value })} />
            </label>
            <label className="settings-field" htmlFor="provider-opus-model">
              <span>Opus 模型</span>
              <input id="provider-opus-model" value={draft.opusModel} onChange={(event) => updateDraft({ opusModel: event.currentTarget.value })} />
            </label>
          </fieldset>

          <label className="settings-field" htmlFor="provider-json">
            <span>设置 JSON / 环境变量</span>
            <textarea id="provider-json" value={draft.jsonConfig} onChange={(event) => updateDraft({ jsonConfig: event.currentTarget.value })} rows={8} placeholder={buildProviderJson(draft)} />
          </label>

          {detectedApiKeyEnvVarName ? (
            <p className="settings-provider-feedback is-info">检测到环境变量：{detectedApiKeyEnvVarName}</p>
          ) : null}
          {testResult ? <pre className="settings-json-box">{formatProviderTestResult(testResult)}</pre> : null}
          {testError ? <p className="settings-provider-feedback is-danger">{testError}</p> : null}
        </div>

        <footer className="settings-modal-footer">
          <button type="button" className="settings-secondary-action" onClick={onClose}>取消</button>
          <button type="button" className="settings-secondary-action" onClick={handleTestProvider} disabled={providerTestBusy} aria-label="测试连接">{providerTestBusy ? "测试中..." : "测试连接"}</button>
          <button type="submit" className="settings-primary-action" disabled={providerBusy} aria-label={mode === "edit" ? "保存" : "添加"}>{mode === "edit" ? "保存" : "添加"}</button>
        </footer>
      </form>
    </div>
  );
}

export default SettingsWorkspace;
