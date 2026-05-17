import type {
  AgentProfileCreateParams,
  AgentProfilePreviewToolsParams,
  AgentProfilePreviewToolsResult,
  AgentProfileRecord,
  AgentProfileValidateParams,
  AgentProfileValidateResult,
  HookCreateParams,
  HookUpdateParams,
  ProviderApiFormat,
  RuntimeHookEvent,
  RuntimeHookExecutionRecord,
  RuntimeHookFailureMode,
  RuntimeHookRecord,
} from "@shared";

export type { ProviderApiFormat };

export type SettingsSection =
  | "providers"
  | "permissions"
  | "general"
  | "im"
  | "agents"
  | "hooks"
  | "skills"
  | "computer"
  | "about";

export type ProviderPresetId = "deepseek" | "zhipu" | "kimi" | "minimax" | "custom";
export type ThemeMode = "light" | "dark" | "system";
export type DensityMode = "comfortable" | "compact";
export type RadiusMode = "sm" | "md" | "lg";
export type MotionMode = "reduced" | "subtle" | "expressive";
export type AccentColor = "cyan" | "violet" | "green" | "amber" | "rose";
export type LanguageMode = "zh" | "en" | "auto";
export type ReasoningEffort = "low" | "medium" | "high" | "max";

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

export interface SettingsAgentConfig extends Partial<AgentProfileRecord> {
  id: string;
  name: string;
  description?: string;
  cwd?: string;
  enabled: boolean;
  role?: AgentProfileRecord["role"];
  permissionMode?: AgentProfileRecord["permissionMode"];
  providerProfileId?: string;
  model?: string;
  skillIds?: string[];
  mcpServerIds?: string[];
  toolPolicy?: AgentProfileRecord["toolPolicy"];
  systemPrompt?: string;
  isBuiltin?: boolean;
}

export type SettingsAgentDraft = AgentProfileCreateParams;

export interface SettingsAgentFeedback {
  tone: "success" | "danger" | "info";
  message: string;
}

export interface SettingsHookConfig extends RuntimeHookRecord {}

export type SettingsHookDraft = HookCreateParams;
export type SettingsHookPatch = HookUpdateParams;

export interface SettingsHookFeedback {
  tone: "success" | "danger" | "info";
  message: string;
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

export interface SettingsAgentBehaviorConfig {
  autonomyActiveProfileId: string;
  autonomyProfiles: Array<{
    id: string;
    name: string;
    level: string;
    maxSteps: number;
    maxParallelSubtasks: number;
    allowBackground: boolean;
    allowSubagents: boolean;
  }>;
  soulActiveProfileId: string;
  soulName: string;
  soulIdentity: string;
  soulCommunicationStyle: string;
  soulReasoningStyle: string;
  soulCollaborationStyle: string;
  soulCustomSystemPrompt: string;
  workspaceInstructions: string;
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
  agentBehavior?: SettingsAgentBehaviorConfig;
  onAgentBehaviorChange?: (next: SettingsAgentBehaviorConfig) => void | Promise<void>;
  general?: SettingsGeneralConfig;
  onGeneralChange?: (next: SettingsGeneralConfig) => void;
  im?: SettingsIMConfig;
  onIMChange?: (next: SettingsIMConfig) => void;
  onTestIM?: () => void | Promise<void>;
  agents?: SettingsAgentConfig[];
  agentBusyId?: string | null;
  agentFeedback?: SettingsAgentFeedback | null;
  onRefreshAgents?: () => void | Promise<void>;
  onAgentToggle?: (agentId: string, enabled: boolean) => void;
  onAddAgent?: (payload: SettingsAgentDraft) => void | Promise<void>;
  onUpdateAgent?: (agentId: string, payload: Partial<SettingsAgentDraft>) => void | Promise<void>;
  onDeleteAgent?: (agentId: string) => void | Promise<void>;
  onValidateAgent?: (payload: AgentProfileValidateParams) => AgentProfileValidateResult | Promise<AgentProfileValidateResult>;
  onPreviewAgentTools?: (payload: AgentProfilePreviewToolsParams) => AgentProfilePreviewToolsResult | Promise<AgentProfilePreviewToolsResult>;
  hooks?: SettingsHookConfig[];
  hookExecutions?: RuntimeHookExecutionRecord[];
  hookBusyId?: string | null;
  hookFeedback?: SettingsHookFeedback | null;
  hookWorkspaceId?: string | null;
  onRefreshHooks?: () => void | Promise<void>;
  onHookToggle?: (hookId: string, enabled: boolean) => void | Promise<void>;
  onAddHook?: (payload: SettingsHookDraft) => void | Promise<void>;
  onUpdateHook?: (hookId: string, payload: Partial<SettingsHookDraft>) => void | Promise<void>;
  onDeleteHook?: (hookId: string) => void | Promise<void>;
  onRefreshHookExecutions?: (hookId?: string) => void | Promise<void>;
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

export const sections: Array<{ id: SettingsSection; label: string; eyebrow: string }> = [
  { id: "providers", label: "模型供应商", eyebrow: "API" },
  { id: "permissions", label: "权限模式", eyebrow: "模式" },
  { id: "general", label: "外观偏好", eyebrow: "界面" },
  { id: "im", label: "消息桥接", eyebrow: "桥接" },
  { id: "agents", label: "智能体", eyebrow: "队列" },
  { id: "hooks", label: "Hooks", eyebrow: "Runtime" },
  { id: "skills", label: "技能库", eyebrow: "技能" },
  { id: "computer", label: "电脑操作", eyebrow: "控制" },
  { id: "about", label: "关于", eyebrow: "构建" },
];

export const runtimeHookEvents = [
  "before_task_start",
  "after_task_complete",
  "on_task_failed",
  "on_task_cancel",
  "on_task_pause",
  "on_approval_required",
  "before_tool_call",
  "after_tool_call",
  "before_provider_turn",
  "after_provider_turn",
  "before_compaction",
  "after_compaction",
  "on_task_resume",
  "on_evidence_requested",
  "before_subagent_start",
  "after_subagent_complete",
  "on_subagent_failed",
  "before_worktree_create",
  "after_worktree_create",
  "before_worktree_merge",
  "after_worktree_merge",
  "before_patch_apply",
  "after_patch_apply",
  "on_memory_write",
  "on_context_snapshot",
] satisfies RuntimeHookEvent[];

export const runtimeHookActionTypes = [
  "audit_note",
  "notification",
  "run_command",
  "webhook",
  "memory_write",
  "auto_verification_suggestion",
  "external_sync",
] as const;

export const runtimeHookFailureModes = ["warn", "block", "retry", "ignore", "ask_user"] satisfies RuntimeHookFailureMode[];

export const fallbackProviders: SettingsProvider[] = [
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

export const providerPresets = [
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

export const providerApiFormatOptions: Array<{ value: ProviderApiFormat; label: string }> = [
  { value: "openai-chat", label: "OpenAI Chat Completions" },
  { value: "openai-responses", label: "OpenAI Responses API" },
  { value: "anthropic-messages", label: "Anthropic Messages" },
];

export const providerApiKeyEnvKeys = [
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

export const permissionModes = [
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

export const fallbackGeneral: SettingsGeneralConfig = {
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

export const fallbackIM: SettingsIMConfig = {
  enabled: false,
  provider: "feishu",
  webhookUrl: "",
  signingSecretSet: false,
  defaultReplyMode: "manual",
};

export const fallbackComputerUse: SettingsComputerUseConfig = {
  screenshot: false,
  browserAutomation: false,
  clipboardAccess: true,
  systemKeyCombos: false,
  sensitiveActionConfirm: true,
  status: "ready",
};
