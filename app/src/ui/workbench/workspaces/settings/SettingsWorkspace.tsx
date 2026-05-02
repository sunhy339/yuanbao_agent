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
  { id: "providers", label: "Providers", eyebrow: "API" },
  { id: "permissions", label: "Permissions", eyebrow: "Mode" },
  { id: "general", label: "Appearance", eyebrow: "Desk" },
  { id: "im", label: "IM Bridge", eyebrow: "Bridge" },
  { id: "agents", label: "Agents", eyebrow: "Roster" },
  { id: "skills", label: "Skills", eyebrow: "Library" },
  { id: "computer", label: "Computer Use", eyebrow: "Control" },
  { id: "about", label: "About", eyebrow: "Build" },
];

const fallbackProviders: SettingsProvider[] = [
  {
    id: "default",
    name: "Default Provider",
    endpoint: "Not configured",
    note: "Connect an OpenAI-compatible provider before running production tasks.",
    models: ["No model configured"],
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
    label: "Custom",
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
    title: "Ask for approval",
    text: "Request confirmation before commands, edits, network access, or sensitive tool calls.",
  },
  {
    id: "edits",
    title: "Allow workspace edits",
    text: "Let the agent edit files inside the workspace while still asking for higher-risk actions.",
  },
  {
    id: "plan",
    title: "Plan first",
    text: "Keep work in a reviewable planning mode before implementation begins.",
  },
  {
    id: "skip",
    title: "Autonomous",
    text: "Reduce approval prompts for trusted local work. Keep this mode for controlled environments.",
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
  status: "Ready",
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
    name: provider?.name ?? "Custom Provider",
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
    return "No model configured";
  }
  const mapping = provider.modelMapping;
  if (mapping?.main || mapping?.sonnet || mapping?.opus) {
    return [mapping.main, mapping.haiku, mapping.sonnet, mapping.opus].filter(Boolean).join(" / ");
  }
  return provider.models?.join(" / ") || "No model configured";
}

function formatProviderSuccessDetail(result: SettingsProviderTestResult) {
  return [result.model, result.finishReason].filter(Boolean).join(" / ");
}

function formatProviderTestResult(result: SettingsProviderTestResult) {
  return [
    result.ok ? "Connection succeeded." : "Connection failed.",
    `Status: ${result.status}`,
    result.model ? `Model: ${result.model}` : undefined,
    result.finishReason ? `Finish: ${result.finishReason}` : undefined,
    result.checkedEnvVarName ? `Env var: ${result.checkedEnvVarName}` : undefined,
    result.errorSummary ? `Reason: ${result.errorSummary}` : undefined,
    result.message ? `Message: ${result.message}` : undefined,
  ].filter(Boolean).join("\n");
}

function buildProviderJson(draft: ProviderFormDraft) {
  return JSON.stringify(
    {
      env: {
        LOCAL_AGENT_PROVIDER_BASE_URL: draft.endpoint || "(provider base URL)",
        LOCAL_AGENT_PROVIDER_API_FORMAT: draft.apiFormat,
        LOCAL_AGENT_PROVIDER_MODEL: draft.mainModel || "(model id)",
        LOCAL_AGENT_PROVIDER_API_KEY: draft.apiKey ? "(provided in form)" : "(set in runtime env)",
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
      <aside className="settings-rail" aria-label="Settings sections">
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
          aria-label="About"
        >
          <span aria-hidden="true">i</span>
          About
        </button>
      </aside>

      <section className="settings-pane" aria-live="polite">
        <div className="settings-content-panel">
          <h1 id="settings-title" className="settings-page-title">Settings</h1>
          <section className="settings-command-strip" aria-label="Settings command strip">
            <div>
              <p className="settings-kicker">{activeSection.eyebrow} Control</p>
              <h2>{activeSection.label}</h2>
              <span>{section === "providers" ? activeProvider?.name ?? "No provider selected" : "Desktop runtime configuration"}</span>
            </div>
            <dl>
              <div>
                <dt>Providers</dt>
                <dd>{readyProviders}/{providers.length}</dd>
              </div>
              <div>
                <dt>Agents</dt>
                <dd>{enabledAgents}/{agents.length}</dd>
              </div>
              <div>
                <dt>Skills</dt>
                <dd>{enabledSkills}/{skills.length}</dd>
              </div>
              <div>
                <dt>Permission</dt>
                <dd>{selectedPermissionMode}</dd>
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
          <p className="settings-kicker">Providers</p>
          <h2>Provider Control</h2>
          <p>Manage API endpoints, model mapping, connection checks, and the active runtime provider.</p>
        </div>
        <button className="settings-primary-action" type="button" onClick={onAddProvider} aria-label="Add provider">
          Add provider
        </button>
      </header>

      <div className="settings-provider-grid">
        <div className="settings-provider-list" aria-label="Provider list">
          {providers.map((provider) => (
            <button
              key={provider.id}
              type="button"
              className={provider.id === selectedProviderId ? "settings-provider-item is-active" : "settings-provider-item"}
              onClick={() => onSelectProvider(provider.id)}
              aria-current={provider.id === selectedProviderId ? "page" : undefined}
              aria-label={`Select provider ${provider.name}`}
            >
              <span className="settings-provider-dot" aria-hidden="true" />
              <span className="settings-provider-copy">
                <strong>{provider.name}</strong>
                <small>
                  {provider.endpoint}
                  {provider.models?.[0] ? ` / ${provider.models[0]}` : ""}
                </small>
                {provider.note ? <small>{provider.note}</small> : null}
                {provider.lastTest ? <small>{`Last test: ${provider.lastTest.status}`}</small> : null}
                {provider.lastTest?.ok && formatProviderSuccessDetail(provider.lastTest) ? (
                  <small>{formatProviderSuccessDetail(provider.lastTest)}</small>
                ) : null}
              </span>
              {provider.id === activeProviderId ? (
                <em className="settings-provider-badge">ACTIVE</em>
              ) : provider.status ? (
                <em>{provider.status}</em>
              ) : null}
            </button>
          ))}
        </div>

        <article className="settings-provider-detail" aria-label="Selected provider details">
          <div>
            <p className="settings-kicker">Selected Provider</p>
            <h3>{activeProvider?.name ?? "No provider selected"}</h3>
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
              <dt>Endpoint</dt>
              <dd>{activeProvider?.endpoint ?? "Not configured"}</dd>
            </div>
            <div>
              <dt>Model mapping</dt>
              <dd>{formatProviderModels(activeProvider)}</dd>
            </div>
            <div>
              <dt>Key state</dt>
              <dd>{activeProvider?.apiKeyMasked ?? "Provided by runtime environment variables."}</dd>
            </div>
            {activeProvider?.lastTest ? <ProviderTestSummaryRows result={activeProvider.lastTest} /> : null}
          </dl>
          <div className="settings-provider-actions">
            <button type="button" className="settings-secondary-action" onClick={onEditProvider}>Edit</button>
            <button type="button" className="settings-secondary-action" onClick={onTestProvider} disabled={providerTestBusy}>
              {providerTestBusy ? "Testing..." : "Test connection"}
            </button>
            <button type="button" className="settings-primary-action" onClick={onSaveProvider} disabled={providerBusy}>
              {providerBusy ? "Saving..." : "Save"}
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
  const currentModel = provider.modelMapping?.main || provider.models?.[0] || "Not configured";
  const lastTest = provider.lastTest;
  const connectionLabel = lastTest ? (lastTest.ok ? "Test passed" : "Test failed") : "Not tested";
  const connectionDetail = lastTest
    ? lastTest.ok
      ? formatProviderSuccessDetail(lastTest) || lastTest.message
      : lastTest.errorSummary || lastTest.message
    : "Run Test connection to verify this provider before starting a real task.";

  return (
    <div className="settings-provider-state" aria-label="Provider status summary">
      <div>
        <span className={isActive ? "settings-status-pill is-active" : "settings-status-pill"}>
          {isActive ? "Active provider" : "Selected only"}
        </span>
        <strong>{currentModel}</strong>
        <small>Current model</small>
      </div>
      <div>
        <span className={lastTest?.ok ? "settings-status-pill is-pass" : lastTest ? "settings-status-pill is-fail" : "settings-status-pill"}>
          {connectionLabel}
        </span>
        <strong>{connectionDetail}</strong>
        <small>Connection status</small>
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
        <dt>Last test</dt>
        <dd>{`Last test: ${result.status}`}</dd>
      </div>
      {result.ok ? (
        <div>
          <dt>Model / finish</dt>
          <dd>{successDetail || result.message}</dd>
        </div>
      ) : (
        <div>
          <dt>Failure reason</dt>
          <dd>{failureDetail}</dd>
        </div>
      )}
      {result.checkedAt ? (
        <div>
          <dt>Checked at</dt>
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
          <p className="settings-kicker">Permission Mode</p>
          <h2>Permission Mode</h2>
          <p>Choose how the runtime asks for approval before edits, commands, and higher-risk actions.</p>
        </div>
      </header>
      <div className="settings-card-stack" role="radiogroup" aria-label="Permission mode">
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
          <p className="settings-kicker">General</p>
          <h2>Appearance</h2>
          <p>Set theme, density, accent, motion, language, reasoning effort, and web preflight behavior for the workbench.</p>
        </div>
      </header>
      <div className="settings-form-stack">
        <SegmentedControl
          label="Theme"
          name="theme"
          value={value.theme}
          options={[
            { value: "light", label: "Light" },
            { value: "dark", label: "Dark" },
            { value: "system", label: "System" },
          ]}
          onChange={(theme) => onChange({ ...value, theme: theme as ThemeMode })}
        />
        <SegmentedControl
          label="Density"
          name="density"
          value={value.density}
          options={[
            { value: "comfortable", label: "Comfort" },
            { value: "compact", label: "Compact" },
          ]}
          onChange={(density) => onChange({ ...value, density: density as DensityMode })}
        />
        <SegmentedControl
          label="Radius"
          name="radius"
          value={value.radius}
          options={[
            { value: "sm", label: "Small" },
            { value: "md", label: "Medium" },
            { value: "lg", label: "Large" },
          ]}
          onChange={(radius) => onChange({ ...value, radius: radius as RadiusMode })}
        />
        <SegmentedControl
          label="Motion"
          name="motion"
          value={value.motion}
          options={[
            { value: "reduced", label: "Reduced" },
            { value: "subtle", label: "Subtle" },
            { value: "expressive", label: "Expressive" },
          ]}
          onChange={(motion) => onChange({ ...value, motion: motion as MotionMode })}
        />
        <SegmentedControl
          label="Accent"
          name="accent"
          value={value.accentColor}
          options={[
            { value: "cyan", label: "Cyan" },
            { value: "violet", label: "Violet" },
            { value: "green", label: "Green" },
            { value: "amber", label: "Amber" },
            { value: "rose", label: "Rose" },
          ]}
          onChange={(accentColor) => onChange({ ...value, accentColor: accentColor as AccentColor })}
        />
        <label className="settings-field" htmlFor="appearance-transparency">
          <span>Transparency {Math.round(value.transparency * 100)}%</span>
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
          <span>Font scale {Math.round(value.fontScale * 100)}%</span>
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
          label="Language"
          name="language"
          value={value.language}
          options={[
            { value: "en", label: "English" },
            { value: "zh", label: "Chinese" },
            { value: "auto", label: "Auto" },
          ]}
          onChange={(language) => onChange({ ...value, language: language as LanguageMode })}
        />
        <SegmentedControl
          label="Reasoning effort"
          name="reasoning"
          value={value.reasoningEffort}
          options={[
            { value: "low", label: "Low" },
            { value: "medium", label: "Medium" },
            { value: "high", label: "High" },
            { value: "max", label: "Max" },
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
            <strong>Skip WebFetch domain preflight</strong>
            <small>Keep enabled for local runtime compatibility unless the upstream safety check is required.</small>
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
          <p className="settings-kicker">IM Bridge</p>
          <h2>IM Bridge</h2>
          <p>Connect Feishu, WeCom, or a custom webhook so conversations can enter external message channels.</p>
        </div>
      </header>
      <div className="settings-form-stack">
        <label className="settings-toggle-card" htmlFor="im-enabled">
          <input id="im-enabled" type="checkbox" checked={value.enabled} onChange={(event) => onChange({ ...value, enabled: event.currentTarget.checked })} />
          <span>
            <strong>Enable IM gateway</strong>
            <small>Keep the configuration but stop receiving external messages when disabled.</small>
          </span>
        </label>
        <label className="settings-field" htmlFor="im-provider">
          <span>Channel</span>
          <select id="im-provider" value={value.provider} onChange={(event) => onChange({ ...value, provider: event.currentTarget.value })}>
            <option value="feishu">Feishu</option>
            <option value="wecom">WeCom</option>
            <option value="custom">Custom gateway</option>
          </select>
        </label>
        <label className="settings-field" htmlFor="im-webhook">
          <span>Webhook URL</span>
          <input id="im-webhook" type="url" value={value.webhookUrl} placeholder="https://example.com/im/webhook" onChange={(event) => onChange({ ...value, webhookUrl: event.currentTarget.value })} />
        </label>
        <SegmentedControl
          label="Default reply mode"
          name="im-reply-mode"
          value={value.defaultReplyMode}
          options={[
            { value: "manual", label: "Manual" },
            { value: "auto", label: "Auto reply" },
            { value: "silent", label: "Silent log" },
          ]}
          onChange={(defaultReplyMode) => onChange({ ...value, defaultReplyMode: defaultReplyMode as SettingsIMConfig["defaultReplyMode"] })}
        />
        <div className="settings-inline-actions">
          <span>Signing secret: {value.signingSecretSet ? "Configured" : "Not configured"}</span>
          <button type="button" className="settings-secondary-action" onClick={onTestIM} disabled={!onTestIM}>Test IM connection</button>
          {!onTestIM ? <small className="settings-action-note">Runtime IM bridge testing is not available in this desktop build.</small> : null}
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
          <p className="settings-kicker">Agent Roster</p>
          <h2>Agents</h2>
          <p>Manage resident agents, working directories, and default permission policies.</p>
        </div>
        <button type="button" className="settings-primary-action" onClick={onAddAgent} disabled={!onAddAgent} title={!onAddAgent ? "Agent profile management is not available yet." : undefined}>Add agent</button>
      </header>
      {!onAddAgent || !onAgentToggle ? (
        <p className="settings-action-note settings-panel-note">Agent profiles are read-only until runtime agent management is defined.</p>
      ) : null}
      <ListOrEmpty emptyTitle="No agents" emptyText="Resident agents will appear here after runtime integration.">
        {agents.map((agent) => (
          <label key={agent.id} className="settings-row-card">
            <input type="checkbox" checked={agent.enabled} disabled={!onAgentToggle} onChange={(event) => onAgentToggle?.(agent.id, event.currentTarget.checked)} />
            <span>
              <strong>{agent.name}</strong>
              <small>{agent.description ?? "No description"}</small>
              <small>{agent.cwd ?? "No working directory"}</small>
            </span>
            <em>{agent.permissionMode ?? "Inherited"}</em>
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
          <p className="settings-kicker">Skill Library</p>
          <h2>Skills</h2>
          <p>Skills extend the local agent with focused workflows. Installed presets are available to the runtime from ~/.codex/skills/.</p>
        </div>
        <div className="settings-header-actions">
          <button type="button" className="settings-secondary-action" onClick={onOpenSkillsFolder} disabled={!onOpenSkillsFolder} title={!onOpenSkillsFolder ? "Opening the skills folder is not wired yet." : undefined}>Open folder</button>
          <button type="button" className="settings-primary-action" onClick={onRefreshSkills} disabled={!onRefreshSkills}>Refresh skills</button>
        </div>
      </header>
      {!onOpenSkillsFolder ? <p className="settings-action-note settings-panel-note">Folder opening is pending a desktop shell bridge; refresh still uses the runtime skill registry.</p> : null}
      <ListOrEmpty emptyTitle="No installed skills" emptyText="Add skills in ~/.codex/skills/ to make them available here.">
        {skills.map((skill) => (
          <article key={skill.id} className="settings-row-card settings-skill-card">
            <span className="settings-skill-marker" aria-hidden="true" />
            <span>
              <strong>{skill.name}</strong>
              <small>{skill.description ?? "No description"}</small>
              <small>{skill.path ?? "No path"}</small>
            </span>
            <em>{skill.enabled ? "Available" : "Unavailable"}</em>
            {skill.updateAvailable ? <em>Update available</em> : null}
          </article>
        ))}
      </ListOrEmpty>
    </div>
  );
}

function ComputerUsePanel({ value, onChange, onRecheckComputerUse }: { value: SettingsComputerUseConfig; onChange: (next: SettingsComputerUseConfig) => void; onRecheckComputerUse?: () => void | Promise<void> }) {
  const toggles: Array<{ key: keyof SettingsComputerUseConfig; label: string; text: string }> = [
    { key: "screenshot", label: "Screen observation", text: "Allow screenshots for visual task context." },
    { key: "browserAutomation", label: "Browser automation", text: "Allow opening and controlling browser sessions." },
    { key: "clipboardAccess", label: "Clipboard access", text: "Allow reading and writing the clipboard." },
    { key: "systemKeyCombos", label: "System shortcuts", text: "Allow system-level keyboard combinations." },
    { key: "sensitiveActionConfirm", label: "Sensitive action confirmation", text: "Require confirmation before destructive or external actions." },
  ];

  return (
    <div className="settings-panel settings-narrow-panel">
      <header className="settings-panel-header settings-panel-header-plain">
        <div>
          <p className="settings-kicker">Computer Use</p>
          <h2>Computer Use</h2>
          <p>Control local desktop capabilities such as screenshots, browser automation, clipboard access, and high-risk confirmations.</p>
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
          <span>Status: {value.status ?? "Not checked"}</span>
          <button type="button" className="settings-secondary-action" onClick={onRecheckComputerUse} disabled={!onRecheckComputerUse}>Recheck</button>
          {!onRecheckComputerUse ? <small className="settings-action-note">Desktop permission recheck is not implemented yet.</small> : null}
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
    ["Version", about?.version ?? "0.1.0"],
    ["Runtime", about?.runtime ?? "Tauri + React"],
    ["Data path", about?.dataPath ?? "Not connected"],
    ["Build", about?.build ?? "development"],
  ];
  const memoryPreview = workspaceMemorySummary
    ?.split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => line && line !== "Project memory:")
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
          <p className="settings-kicker">About</p>
          <h2>About</h2>
          <p>Local agent workbench for multi-session orchestration, scheduled tasks, and controlled tool execution.</p>
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
        <button type="button" className="settings-secondary-action" onClick={onOpenLogs} disabled={!onOpenLogs} title={!onOpenLogs ? "Opening logs requires a desktop shell bridge." : undefined}>Open logs</button>
        <button type="button" className="settings-secondary-action" onClick={onOpenDataDirectory} disabled={!onOpenDataDirectory} title={!onOpenDataDirectory ? "Opening the data folder requires a desktop shell bridge." : undefined}>Open data folder</button>
      </div>
      {!onOpenLogs || !onOpenDataDirectory ? (
        <p className="settings-action-note settings-panel-note">Opening local folders is pending a Tauri shell bridge; paths are shown above for manual inspection.</p>
      ) : null}
      <section className="settings-memory-card" aria-label="Project focus">
        <div>
          <p className="settings-kicker">Context</p>
          <h3>Project focus</h3>
          <p>Pin project goals, boundaries, and preferences into new task context.</p>
        </div>
        <label className="settings-field" htmlFor="project-focus">
          <span>Pinned focus</span>
          <textarea
            id="project-focus"
            value={focusDraft}
            rows={4}
            placeholder="Example: prioritize durable local coding-agent workflows and avoid unrelated refactors."
            onChange={(event) => setFocusDraft(event.currentTarget.value)}
          />
        </label>
        <div className="settings-provider-actions">
          <button
            type="button"
            className="settings-primary-action"
            onClick={() => void onSaveWorkspaceFocus?.(focusDraft)}
            disabled={workspaceFocusBusy || !onSaveWorkspaceFocus}
          >
            {workspaceFocusBusy ? "Saving..." : "Save project focus"}
          </button>
          <button
            type="button"
            className="settings-secondary-action"
            onClick={() => {
              setFocusDraft("");
              void onSaveWorkspaceFocus?.("");
            }}
            disabled={workspaceFocusBusy || !onSaveWorkspaceFocus || !focusDraft.trim()}
          >
            Clear project focus
          </button>
        </div>
      </section>
      <section className="settings-memory-card" aria-label="Project memory">
        <div>
          <p className="settings-kicker">Context</p>
          <h3>Project memory</h3>
          <p>Persisted project notes can be added to future model context.</p>
        </div>
        <pre>{memoryPreview || "No project memory stored."}</pre>
        <button
          type="button"
          className="settings-secondary-action"
          onClick={() => void onClearWorkspaceMemory?.()}
          disabled={workspaceMemoryBusy || !workspaceMemorySummary?.trim() || !onClearWorkspaceMemory}
        >
          {workspaceMemoryBusy ? "Clearing..." : "Clear project memory"}
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
  const title = mode === "edit" ? "Edit provider" : "Add provider";
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
            <p className="settings-kicker">Provider</p>
            <h2 id="provider-modal-title">{title}</h2>
          </div>
          <button type="button" className="settings-icon-button" onClick={onClose} aria-label="Close">x</button>
        </header>

        <div className="settings-provider-form">
          <div className="settings-preset-row" aria-label="Provider presets">
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
            <span>Name *</span>
            <input id="provider-name" value={draft.name} onChange={(event) => updateDraft({ name: event.currentTarget.value })} required />
          </label>
          <label className="settings-field" htmlFor="provider-note">
            <span>Note</span>
            <input id="provider-note" value={draft.note} onChange={(event) => updateDraft({ note: event.currentTarget.value })} placeholder="Optional note..." />
          </label>
          <label className="settings-field" htmlFor="provider-endpoint">
            <span>Endpoint</span>
            <input id="provider-endpoint" value={draft.endpoint} onChange={(event) => updateDraft({ endpoint: event.currentTarget.value })} placeholder="https://api.example.com/v1" />
          </label>
          <label className="settings-field" htmlFor="provider-api-format">
            <span>API format</span>
            <select id="provider-api-format" value={draft.apiFormat} onChange={(event) => updateDraft({ apiFormat: event.currentTarget.value as ProviderApiFormat })}>
              <option value="openai-chat">OpenAI Chat Completions</option>
              <option value="openai-responses">OpenAI Responses API</option>
              <option value="anthropic-messages">Anthropic Messages</option>
            </select>
          </label>
          <label className="settings-field" htmlFor="provider-api-key">
            <span>API key</span>
            <input id="provider-api-key" type="password" value={draft.apiKey} onChange={(event) => updateDraft({ apiKey: event.currentTarget.value })} placeholder="sk-..." />
          </label>

          <fieldset className="settings-model-grid">
            <legend>Model mapping</legend>
            <label className="settings-field" htmlFor="provider-main-model">
              <span>Main model *</span>
              <input id="provider-main-model" value={draft.mainModel} onChange={(event) => updateDraft({ mainModel: event.currentTarget.value })} required />
            </label>
            <label className="settings-field" htmlFor="provider-haiku-model">
              <span>Haiku model</span>
              <input id="provider-haiku-model" value={draft.haikuModel} onChange={(event) => updateDraft({ haikuModel: event.currentTarget.value })} />
            </label>
            <label className="settings-field" htmlFor="provider-sonnet-model">
              <span>Sonnet model</span>
              <input id="provider-sonnet-model" value={draft.sonnetModel} onChange={(event) => updateDraft({ sonnetModel: event.currentTarget.value })} />
            </label>
            <label className="settings-field" htmlFor="provider-opus-model">
              <span>Opus model</span>
              <input id="provider-opus-model" value={draft.opusModel} onChange={(event) => updateDraft({ opusModel: event.currentTarget.value })} />
            </label>
          </fieldset>

          <label className="settings-field" htmlFor="provider-json">
            <span>Settings JSON / env vars</span>
            <textarea id="provider-json" value={draft.jsonConfig} onChange={(event) => updateDraft({ jsonConfig: event.currentTarget.value })} rows={8} placeholder={buildProviderJson(draft)} />
          </label>

          {detectedApiKeyEnvVarName ? (
            <p className="settings-provider-feedback is-info">Detected env var: {detectedApiKeyEnvVarName}</p>
          ) : null}
          {testResult ? <pre className="settings-json-box">{formatProviderTestResult(testResult)}</pre> : null}
          {testError ? <p className="settings-provider-feedback is-danger">{testError}</p> : null}
        </div>

        <footer className="settings-modal-footer">
          <button type="button" className="settings-secondary-action" onClick={onClose}>Cancel</button>
          <button type="button" className="settings-secondary-action" onClick={handleTestProvider} disabled={providerTestBusy}>{providerTestBusy ? "Testing..." : "Test connection"}</button>
          <button type="submit" className="settings-primary-action" disabled={providerBusy}>{mode === "edit" ? "Save" : "Add"}</button>
        </footer>
      </form>
    </div>
  );
}

export default SettingsWorkspace;
