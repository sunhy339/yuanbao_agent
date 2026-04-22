import { useEffect, useMemo, useState } from "react";
import type {
  ApprovalRequestedPayload,
  ApprovalResolvedPayload,
  AgentEventEnvelope,
  AppConfig,
  AssistantTokenPayload,
  CommandLifecyclePayload,
  CommandOutputPayload,
  PatchRecord,
  PatchProposedPayload,
  PlanStep,
  ProviderMode,
  ProviderProfile,
  ProviderTestResult,
  SessionRecord,
  TaskRecord,
  TaskUpdatedPayload,
  ToolRuntimeConfig,
  ToolLifecyclePayload,
  TraceEventRecord,
  WorkspaceRef,
} from "@shared";
import { RuntimeClient, type HostStatus, type RuntimeConfig } from "./lib/runtimeClient";
import {
  DEFAULT_PROMPT,
  DEFAULT_SESSION_TITLE,
  DEFAULT_WORKSPACE_PATH,
} from "./state/mockData";

const runtimeClient = new RuntimeClient();
const DEFAULT_SEARCH_GLOB_TEXT = "";
const DEFAULT_PROVIDER_MODE: ProviderMode = "mock";
const DEFAULT_PROVIDER_BASE_URL = "https://api.openai.com/v1";
const DEFAULT_PROVIDER_MODEL = "gpt-5-codex";
const DEFAULT_PROVIDER_API_KEY_ENV_VAR = "LOCAL_AGENT_PROVIDER_API_KEY";
const DEFAULT_PROVIDER_TEMPERATURE = 0.2;
const DEFAULT_PROVIDER_MAX_TOKENS = 4000;
const DEFAULT_PROVIDER_MAX_CONTEXT_TOKENS = 120000;
const DEFAULT_PROVIDER_TIMEOUT = 30;
const DEFAULT_ALLOWED_SHELL: ToolRuntimeConfig["allowedShell"] = "powershell";
const TRACE_LIMIT = 50;
const TRACE_AUTO_REFRESH_STATUSES = new Set<TaskRecord["status"]>([
  "completed",
  "failed",
  "waiting_approval",
]);
type TaskControlAction = "cancel" | "pause" | "resume";

interface TaskControlButtonView {
  action: TaskControlAction;
  label: string;
  busyLabel: string;
  tone: "neutral" | "warn";
}

const TASK_CONTROL_BUTTONS: Record<TaskControlAction, TaskControlButtonView> = {
  cancel: {
    action: "cancel",
    label: "Cancel",
    busyLabel: "Cancelling...",
    tone: "warn",
  },
  pause: {
    action: "pause",
    label: "Pause",
    busyLabel: "Pausing...",
    tone: "neutral",
  },
  resume: {
    action: "resume",
    label: "Resume",
    busyLabel: "Resuming...",
    tone: "neutral",
  },
};

interface ProviderSettingsForm {
  name: string;
  mode: ProviderMode;
  baseUrl: string;
  model: string;
  apiKeyEnvVarName: string;
  temperature: string;
  maxTokens: string;
  maxContextTokens: string;
  timeout: string;
}

interface CommandPolicyForm {
  allowedShell: ToolRuntimeConfig["allowedShell"];
  allowedCommands: string;
  deniedCommands: string;
  blockedPatterns: string;
  allowedCwdRoots: string;
}

type ProviderStatusBadge = "mock" | "configured" | "missing env" | "failed" | "ok";

interface ProviderStatusView {
  label: ProviderStatusBadge;
  badgeClass: "ok" | "warn" | "error" | "info" | "neutral";
}

interface ProviderHealthView {
  checkedAtText: string;
  statusText: string;
  summaryText: string;
  badgeClass: "ok" | "warn" | "error" | "info" | "neutral";
}

function formatTimestamp(timestamp?: number): string {
  if (!timestamp) {
    return "not recorded";
  }

  return new Date(timestamp).toLocaleString("en-US", {
    hour12: false,
  });
}

function formatDuration(durationMs?: number): string {
  if (typeof durationMs !== "number" || !Number.isFinite(durationMs)) {
    return "in progress";
  }

  if (durationMs < 1000) {
    return `${Math.max(0, Math.round(durationMs))} ms`;
  }

  return `${(durationMs / 1000).toFixed(1)} s`;
}

function getTaskControlActions(status?: TaskRecord["status"]): TaskControlButtonView[] {
  if (status === "running" || status === "waiting_approval") {
    return [TASK_CONTROL_BUTTONS.cancel, TASK_CONTROL_BUTTONS.pause];
  }

  if (status === "paused") {
    return [TASK_CONTROL_BUTTONS.resume, TASK_CONTROL_BUTTONS.cancel];
  }

  return [];
}

function normalizeRuntimeConfig(config: AppConfig | RuntimeConfig): RuntimeConfig {
  return {
    ...config,
    provider: normalizeProviderConfig(config.provider),
    search: config.search ?? {
      glob: [],
      ignore: config.workspace.ignore,
    },
    tools: {
      ...config.tools,
      runCommand: normalizeRunCommandConfig(config.tools.runCommand),
    },
  };
}

function normalizeProviderConfig(provider: AppConfig["provider"]): AppConfig["provider"] {
  const profiles = normalizeProviderProfiles(provider);
  const activeProfileId = provider.activeProfileId && profiles.some((item) => item.id === provider.activeProfileId)
    ? provider.activeProfileId
    : profiles[0]?.id;
  const activeProfile = profiles.find((item) => item.id === activeProfileId) ?? profiles[0];
  const model = activeProfile?.model || provider.model || provider.defaultModel || DEFAULT_PROVIDER_MODEL;
  const maxTokens =
    activeProfile?.maxTokens ?? provider.maxTokens ?? provider.maxOutputTokens ?? DEFAULT_PROVIDER_MAX_TOKENS;
  return {
    ...provider,
    ...activeProfile,
    mode: activeProfile?.mode ?? provider.mode ?? DEFAULT_PROVIDER_MODE,
    baseUrl: activeProfile?.baseUrl ?? provider.baseUrl ?? DEFAULT_PROVIDER_BASE_URL,
    model,
    defaultModel: activeProfile?.defaultModel || provider.defaultModel || model,
    apiKeyEnvVarName:
      activeProfile?.apiKeyEnvVarName ?? provider.apiKeyEnvVarName ?? DEFAULT_PROVIDER_API_KEY_ENV_VAR,
    temperature: activeProfile?.temperature ?? provider.temperature ?? DEFAULT_PROVIDER_TEMPERATURE,
    maxTokens,
    maxOutputTokens: activeProfile?.maxOutputTokens ?? provider.maxOutputTokens ?? maxTokens,
    maxContextTokens:
      activeProfile?.maxContextTokens ?? provider.maxContextTokens ?? DEFAULT_PROVIDER_MAX_CONTEXT_TOKENS,
    timeout: activeProfile?.timeout ?? provider.timeout ?? DEFAULT_PROVIDER_TIMEOUT,
    activeProfileId,
    profiles,
  };
}

function normalizeProviderProfiles(provider: AppConfig["provider"]): ProviderProfile[] {
  const legacyProfile = providerToProfile(provider, provider.activeProfileId || "default", "Default");
  const rawProfiles = provider.profiles?.length ? provider.profiles : [legacyProfile];
  return rawProfiles.map((profile, index) =>
    normalizeProviderProfile(profile, legacyProfile, index),
  );
}

function normalizeProviderProfile(
  profile: Partial<ProviderProfile>,
  fallback: ProviderProfile,
  index: number,
): ProviderProfile {
  const merged = { ...fallback, ...profile };
  const model = merged.model || merged.defaultModel || DEFAULT_PROVIDER_MODEL;
  const maxTokens = merged.maxTokens ?? merged.maxOutputTokens ?? DEFAULT_PROVIDER_MAX_TOKENS;
  return {
    ...merged,
    id: merged.id?.trim() || `profile_${index + 1}`,
    name: merged.name?.trim() || `Profile ${index + 1}`,
    mode: merged.mode ?? DEFAULT_PROVIDER_MODE,
    baseUrl: merged.baseUrl ?? DEFAULT_PROVIDER_BASE_URL,
    model,
    defaultModel: merged.defaultModel || model,
    apiKeyEnvVarName: merged.apiKeyEnvVarName ?? DEFAULT_PROVIDER_API_KEY_ENV_VAR,
    temperature: merged.temperature ?? DEFAULT_PROVIDER_TEMPERATURE,
    maxTokens,
    maxOutputTokens: merged.maxOutputTokens ?? maxTokens,
    maxContextTokens: merged.maxContextTokens ?? DEFAULT_PROVIDER_MAX_CONTEXT_TOKENS,
    timeout: merged.timeout ?? DEFAULT_PROVIDER_TIMEOUT,
  };
}

function providerToProfile(provider: AppConfig["provider"], id: string, name: string): ProviderProfile {
  const model = provider.model || provider.defaultModel || DEFAULT_PROVIDER_MODEL;
  const maxTokens = provider.maxTokens ?? provider.maxOutputTokens ?? DEFAULT_PROVIDER_MAX_TOKENS;
  return {
    id,
    name,
    mode: provider.mode ?? DEFAULT_PROVIDER_MODE,
    baseUrl: provider.baseUrl ?? DEFAULT_PROVIDER_BASE_URL,
    model,
    defaultModel: provider.defaultModel || model,
    fallbackModel: provider.fallbackModel,
    apiKeyEnvVarName: provider.apiKeyEnvVarName ?? DEFAULT_PROVIDER_API_KEY_ENV_VAR,
    temperature: provider.temperature ?? DEFAULT_PROVIDER_TEMPERATURE,
    maxTokens,
    maxOutputTokens: provider.maxOutputTokens ?? maxTokens,
    maxContextTokens: provider.maxContextTokens ?? DEFAULT_PROVIDER_MAX_CONTEXT_TOKENS,
    timeout: provider.timeout ?? DEFAULT_PROVIDER_TIMEOUT,
  };
}

function buildProviderSettingsForm(config: RuntimeConfig | null): ProviderSettingsForm {
  const provider = config?.provider
    ? normalizeProviderConfig(config.provider)
    : {
        defaultModel: DEFAULT_PROVIDER_MODEL,
        temperature: DEFAULT_PROVIDER_TEMPERATURE,
        maxOutputTokens: DEFAULT_PROVIDER_MAX_TOKENS,
      };
  const normalized = normalizeProviderConfig(provider);

  return {
    name:
      normalized.profiles?.find((item) => item.id === normalized.activeProfileId)?.name ??
      "Default",
    mode: normalized.mode ?? DEFAULT_PROVIDER_MODE,
    baseUrl: normalized.baseUrl ?? DEFAULT_PROVIDER_BASE_URL,
    model: normalized.model ?? normalized.defaultModel,
    apiKeyEnvVarName: normalized.apiKeyEnvVarName ?? DEFAULT_PROVIDER_API_KEY_ENV_VAR,
    temperature: String(normalized.temperature),
    maxTokens: String(normalized.maxTokens ?? normalized.maxOutputTokens),
    maxContextTokens: String(normalized.maxContextTokens ?? DEFAULT_PROVIDER_MAX_CONTEXT_TOKENS),
    timeout: String(normalized.timeout ?? DEFAULT_PROVIDER_TIMEOUT),
  };
}

function normalizeRunCommandConfig(runCommand?: Partial<ToolRuntimeConfig>): ToolRuntimeConfig {
  const allowedCommands = runCommand?.allowedCommands ?? runCommand?.allowlist ?? [];
  const deniedCommands = runCommand?.deniedCommands ?? runCommand?.denylist ?? [];
  return {
    allowedShell: runCommand?.allowedShell ?? DEFAULT_ALLOWED_SHELL,
    allowedCommands,
    allowlist: runCommand?.allowlist ?? allowedCommands,
    deniedCommands,
    denylist: runCommand?.denylist ?? deniedCommands,
    blockedPatterns: runCommand?.blockedPatterns ?? [],
    allowedCwdRoots: runCommand?.allowedCwdRoots ?? [],
  };
}

function buildCommandPolicyForm(config: RuntimeConfig | null): CommandPolicyForm {
  const runCommand = normalizeRunCommandConfig(config?.tools.runCommand);
  return {
    allowedShell: runCommand.allowedShell,
    allowedCommands: serializePatternList(runCommand.allowedCommands ?? []),
    deniedCommands: serializePatternList(runCommand.deniedCommands ?? []),
    blockedPatterns: serializePatternList(runCommand.blockedPatterns),
    allowedCwdRoots: serializePatternList(runCommand.allowedCwdRoots ?? []),
  };
}

function parseProviderNumber(
  value: string,
  label: string,
  options: { integer?: boolean; min?: number; max?: number } = {},
): number {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) {
    throw new Error(`${label} must be a number.`);
  }
  if (options.integer && !Number.isInteger(parsed)) {
    throw new Error(`${label} must be an integer.`);
  }
  if (typeof options.min === "number" && parsed < options.min) {
    throw new Error(`${label} must be at least ${options.min}.`);
  }
  if (typeof options.max === "number" && parsed > options.max) {
    throw new Error(`${label} must be at most ${options.max}.`);
  }
  return parsed;
}

function parsePatternText(value: string): string[] {
  return value
    .split(/[\n,]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function serializePatternList(values: string[]): string {
  return values.join("\n");
}

function getProviderStatusView(
  settings: ProviderSettingsForm,
  result: ProviderTestResult | null,
): ProviderStatusView {
  if (!result) {
    return settings.mode === "mock"
      ? { label: "mock", badgeClass: "info" }
      : { label: "configured", badgeClass: "neutral" };
  }

  if (result.status === "ok") {
    return { label: "ok", badgeClass: "ok" };
  }
  if (result.status === "mocked") {
    return { label: "mock", badgeClass: "info" };
  }
  if (result.status === "missing_env" || result.status === "not_configured") {
    return { label: "missing env", badgeClass: "warn" };
  }

  return { label: "failed", badgeClass: "error" };
}

function getProviderRuntimeNotice(
  settings: ProviderSettingsForm,
  result: ProviderTestResult | null,
): string | null {
  const envVarName =
    result?.checkedEnvVarName ?? (settings.apiKeyEnvVarName || DEFAULT_PROVIDER_API_KEY_ENV_VAR);

  if (settings.mode === "mock" || result?.status === "mocked") {
    return "Current provider is mock mode. Tasks sent now use deterministic local behavior, not a real model.";
  }
  if (result?.status === "missing_env" || result?.status === "not_configured") {
    return `Current provider cannot reach a real model because ${envVarName} is missing. Set the env var and test again before sending real-model tasks.`;
  }

  return null;
}

function getProviderErrorSummary(result: ProviderTestResult): string {
  const detail =
    result.lastErrorSummary ?? result.details?.errorSummary ?? result.details?.error ?? result.details?.summary;
  return typeof detail === "string" && detail.trim() ? detail.trim() : result.message;
}

function getProviderHealthBadge(status?: string): ProviderHealthView["badgeClass"] {
  if (status === "ok") {
    return "ok";
  }
  if (status === "mocked") {
    return "info";
  }
  if (status === "missing_env" || status === "not_configured") {
    return "warn";
  }
  if (status === "failed" || status === "unsupported") {
    return "error";
  }
  return "neutral";
}

function getProviderHealthView(
  profile: ProviderProfile | undefined,
  result: ProviderTestResult | null,
): ProviderHealthView {
  const status = result?.lastStatus ?? profile?.lastStatus;
  const summary =
    (result ? getProviderErrorSummary(result) : undefined) ??
    profile?.lastErrorSummary ??
    "No health check has been recorded for this profile yet.";

  return {
    checkedAtText: formatTimestamp(result?.lastCheckedAt ?? profile?.lastCheckedAt),
    statusText: status ?? "not recorded",
    summaryText: summary,
    badgeClass: getProviderHealthBadge(status),
  };
}

function buildDefaultProviderProfile(): ProviderProfile {
  return {
    id: "default",
    name: "Default",
    mode: DEFAULT_PROVIDER_MODE,
    baseUrl: DEFAULT_PROVIDER_BASE_URL,
    model: DEFAULT_PROVIDER_MODEL,
    defaultModel: DEFAULT_PROVIDER_MODEL,
    apiKeyEnvVarName: DEFAULT_PROVIDER_API_KEY_ENV_VAR,
    temperature: DEFAULT_PROVIDER_TEMPERATURE,
    maxTokens: DEFAULT_PROVIDER_MAX_TOKENS,
    maxOutputTokens: DEFAULT_PROVIDER_MAX_TOKENS,
    maxContextTokens: DEFAULT_PROVIDER_MAX_CONTEXT_TOKENS,
    timeout: DEFAULT_PROVIDER_TIMEOUT,
  };
}

function readRequestText(request: Record<string, unknown>, key: string, fallback: string): string {
  const value = request[key];
  if (typeof value === "string" && value.trim()) {
    return value.trim();
  }
  if (typeof value === "number" && Number.isFinite(value)) {
    return String(value);
  }
  return fallback;
}

function readRequestNumber(request: Record<string, unknown>, key: string, fallback: number): number {
  const value = request[key];
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }
  if (typeof value === "string" && value.trim()) {
    const parsed = Number(value);
    if (Number.isFinite(parsed)) {
      return parsed;
    }
  }
  return fallback;
}

function readRequestPatchId(request: Record<string, unknown>): string | undefined {
  const value = request.patch_id ?? request.patchId;
  if (typeof value === "string" && value.trim()) {
    return value.trim();
  }
  return undefined;
}

function readEventText(payload: unknown, key: string): string | undefined {
  if (!payload || typeof payload !== "object") {
    return undefined;
  }

  const value = (payload as Record<string, unknown>)[key];
  return typeof value === "string" && value.trim() ? value.trim() : undefined;
}

function readEventNumber(payload: unknown, key: string): number | undefined {
  if (!payload || typeof payload !== "object") {
    return undefined;
  }

  const value = (payload as Record<string, unknown>)[key];
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }
  if (typeof value === "string" && value.trim()) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : undefined;
  }
  return undefined;
}

function summarizeValue(value: unknown, fallback = "not recorded", maxLength = 180): string {
  if (value === undefined || value === null) {
    return fallback;
  }

  const raw =
    typeof value === "string"
      ? value
      : typeof value === "number" || typeof value === "boolean"
        ? String(value)
        : (() => {
            try {
              return JSON.stringify(value);
            } catch {
              return fallback;
            }
          })();
  const compact = raw.replace(/\s+/g, " ").trim();
  if (!compact) {
    return fallback;
  }
  return compact.length > maxLength ? `${compact.slice(0, maxLength - 1)}...` : compact;
}

function getPayloadValue(payload: unknown, keys: string[]): unknown {
  if (!payload || typeof payload !== "object") {
    return undefined;
  }

  const record = payload as Record<string, unknown>;
  for (const key of keys) {
    if (record[key] !== undefined) {
      return record[key];
    }
  }
  return undefined;
}

function sortByUpdatedAtDesc<T extends { updatedAt: number }>(items: T[]): T[] {
  return [...items].sort((left, right) => right.updatedAt - left.updatedAt);
}

interface ApprovalCardView {
  approvalId: string;
  taskId: string;
  kind: string;
  patchId?: string;
  patchSummary?: string;
  filesChanged?: number;
  command: string;
  cwd: string;
  shell: string;
  timeoutMs: number;
  risk: string;
  requestJson: string;
  requestSummary: string;
  status: "pending" | "approved" | "rejected";
  requestedAt: number;
  updatedAt: number;
  resolvedAt?: number;
  requestedEventId?: string;
  resolvedEventId?: string;
}

interface PatchCardView {
  patchId: string;
  taskId: string;
  summary: string;
  filesChanged: number;
  status: PatchRecord["status"];
  requestedAt: number;
  updatedAt: number;
  diffText?: string;
  approvalId?: string;
  approvalStatus?: ApprovalCardView["status"];
  approvalResolvedAt?: number;
}

interface ChatMessageView {
  id: string;
  taskId: string;
  role: "user" | "assistant";
  content: string;
  createdAt: number;
  updatedAt: number;
  streaming?: boolean;
}

interface ToolTimelineItem {
  id: string;
  taskId: string;
  toolCallId: string;
  toolName: string;
  status: "started" | "completed" | "failed";
  argsSummary: string;
  resultSummary: string;
  errorSummary?: string;
  startedAt: number;
  updatedAt: number;
  finishedAt?: number;
  durationMs?: number;
  eventCount: number;
}

interface ActivityTimelineItem {
  id: string;
  type: string;
  category: "task" | "tool" | "approval" | "patch" | "command" | "assistant" | "event";
  taskId: string;
  time: string;
  status?: string;
  title: string;
  summary: string;
  relatedId?: string;
  raw: string;
}

interface TracePanelItem {
  id: string;
  type: string;
  source: string;
  relatedId: string;
  time: string;
  payloadSummary: string;
  sequence: number;
}

function stringifyRequestJson(request: Record<string, unknown>): string {
  try {
    return JSON.stringify(request, null, 2);
  } catch {
    return "{}";
  }
}

function readRequestStringList(request: Record<string, unknown>, keys: string[]): string[] {
  for (const key of keys) {
    const value = request[key];
    if (Array.isArray(value)) {
      return value
        .map((item) => (typeof item === "string" ? item.trim() : ""))
        .filter(Boolean);
    }
  }
  return [];
}

function readRequestOptionalNumber(request: Record<string, unknown>, keys: string[]): number | undefined {
  for (const key of keys) {
    const value = request[key];
    if (typeof value === "number" && Number.isFinite(value)) {
      return value;
    }
    if (typeof value === "string" && value.trim()) {
      const parsed = Number(value);
      if (Number.isFinite(parsed)) {
        return parsed;
      }
    }
  }
  return undefined;
}

function getApprovalBadgeClass(status: ApprovalCardView["status"]): "ok" | "warn" | "error" {
  if (status === "approved") {
    return "ok";
  }
  if (status === "rejected") {
    return "error";
  }
  return "warn";
}

function upsertRecord<T extends { id: string; updatedAt: number }>(items: T[], record: T): T[] {
  const next = new Map(items.map((item) => [item.id, item]));
  next.set(record.id, record);
  return sortByUpdatedAtDesc(Array.from(next.values()));
}

function coerceTaskStatus(
  event: AgentEventEnvelope,
  fallback: TaskRecord["status"],
): TaskRecord["status"] {
  if (event.type === "task.started") {
    return "running";
  }
  if (event.type === "task.waiting_approval") {
    return "waiting_approval";
  }
  if (event.type === "task.completed") {
    return "completed";
  }
  if (event.type === "task.failed") {
    return "failed";
  }
  if (event.type === "task.cancelled") {
    return "cancelled";
  }
  return fallback;
}

function applyEventToTask(current: TaskRecord | null, event: AgentEventEnvelope): TaskRecord | null {
  if (!current || current.id !== event.taskId || !event.type.startsWith("task.")) {
    return current;
  }

  const payload = (event.payload ?? {}) as Partial<TaskUpdatedPayload> & {
    resultSummary?: string;
    errorCode?: string;
    detail?: string;
  };

  return {
    ...current,
    status: payload.status ?? coerceTaskStatus(event, current.status),
    plan: payload.plan ?? current.plan,
    resultSummary: payload.detail ?? payload.resultSummary ?? current.resultSummary,
    errorCode: payload.errorCode ?? current.errorCode,
    updatedAt: event.ts,
  };
}

function getTaskBadgeClass(status?: TaskRecord["status"]): string {
  if (status === "completed") {
    return "ok";
  }
  if (status === "failed" || status === "cancelled") {
    return "error";
  }
  if (status === "running") {
    return "info";
  }
  if (status === "waiting_approval") {
    return "warn";
  }
  return "neutral";
}

function appendAssistantToken(current: ChatMessageView[], event: AgentEventEnvelope): ChatMessageView[] {
  const payload = event.payload as AssistantTokenPayload;
  const delta = payload.delta ?? "";
  if (!delta) {
    return current;
  }

  const next = [...current];
  const lastAssistantIndex = (() => {
    for (let index = next.length - 1; index >= 0; index -= 1) {
      const item = next[index];
      if (item.taskId === event.taskId && item.role === "assistant" && item.streaming) {
        return index;
      }
    }
    return -1;
  })();

  if (lastAssistantIndex >= 0) {
    const currentMessage = next[lastAssistantIndex];
    next[lastAssistantIndex] = {
      ...currentMessage,
      content: `${currentMessage.content}${delta}`,
      updatedAt: event.ts,
    };
    return next;
  }

  return [
    ...next,
    {
      id: `assistant_${event.eventId}`,
      taskId: event.taskId,
      role: "assistant",
      content: delta,
      createdAt: event.ts,
      updatedAt: event.ts,
      streaming: true,
    },
  ];
}

function completeAssistantMessage(current: ChatMessageView[], event: AgentEventEnvelope): ChatMessageView[] {
  const completedContent = summarizeValue(
    getPayloadValue(event.payload, ["content", "message", "text"]),
    "",
    10_000,
  );
  const next = [...current];
  const lastAssistantIndex = (() => {
    for (let index = next.length - 1; index >= 0; index -= 1) {
      const item = next[index];
      if (item.taskId === event.taskId && item.role === "assistant") {
        return index;
      }
    }
    return -1;
  })();

  if (lastAssistantIndex >= 0) {
    const currentMessage = next[lastAssistantIndex];
    next[lastAssistantIndex] = {
      ...currentMessage,
      content: completedContent || currentMessage.content,
      updatedAt: event.ts,
      streaming: false,
    };
    return next;
  }

  if (!completedContent) {
    return current;
  }

  return [
    ...next,
    {
      id: `assistant_${event.eventId}`,
      taskId: event.taskId,
      role: "assistant",
      content: completedContent,
      createdAt: event.ts,
      updatedAt: event.ts,
      streaming: false,
    },
  ];
}

function buildActivityTitle(event: AgentEventEnvelope): string {
  if (event.type.startsWith("task.")) {
    return event.type.replace("task.", "task ");
  }
  if (event.type.startsWith("tool.")) {
    const payload = event.payload as Partial<ToolLifecyclePayload>;
    return payload.toolName ? `${payload.toolName} ${event.type.replace("tool.", "")}` : event.type;
  }
  if (event.type.startsWith("command.")) {
    const command = readEventText(event.payload, "command");
    return command ? `${command} ${event.type.replace("command.", "")}` : event.type;
  }
  if (event.type.startsWith("approval.")) {
    return event.type.replace("approval.", "approval ");
  }
  if (event.type.startsWith("patch.")) {
    return event.type.replace("patch.", "patch ");
  }
  if (event.type.startsWith("assistant.")) {
    return event.type.replace("assistant.", "assistant ");
  }
  return event.type;
}

function buildActivityItem(event: AgentEventEnvelope): ActivityTimelineItem {
  const category = event.type.startsWith("task.")
    ? "task"
    : event.type.startsWith("tool.")
      ? "tool"
      : event.type.startsWith("approval.")
        ? "approval"
        : event.type.startsWith("patch.")
          ? "patch"
          : event.type.startsWith("command.")
            ? "command"
            : event.type.startsWith("assistant.")
              ? "assistant"
              : "event";
  const status =
    readEventText(event.payload, "status") ??
    readEventText(event.payload, "decision") ??
    event.type.split(".")[1];
  const relatedId = summarizeValue(
    getPayloadValue(event.payload, [
      "toolCallId",
      "commandId",
      "approvalId",
      "patchId",
      "messageId",
    ]),
    "",
    80,
  );

  return {
    id: event.eventId,
    type: event.type,
    category,
    taskId: event.taskId,
    time: formatTimestamp(event.ts),
    status,
    title: buildActivityTitle(event),
    summary: summarizeEvent(event),
    relatedId: relatedId || undefined,
    raw: JSON.stringify(event.payload, null, 2),
  };
}

function buildTracePanelItem(trace: TraceEventRecord): TracePanelItem {
  return {
    id: trace.id,
    type: trace.type,
    source: trace.source,
    relatedId: trace.relatedId ?? "none",
    time: formatTimestamp(trace.createdAt),
    payloadSummary: summarizeValue(trace.payload, "empty payload", 240),
    sequence: trace.sequence,
  };
}

function summarizeEvent(event: AgentEventEnvelope): string {
  if (event.type === "assistant.token") {
    return ((event.payload as AssistantTokenPayload).delta ?? "").trim() || "Model is streaming output.";
  }

  if (event.type === "assistant.message.completed") {
    return "Assistant message completed.";
  }

  if (event.type === "command.output") {
    const payload = event.payload as CommandOutputPayload;
    return `${payload.stream}: ${payload.chunk.trim()}`.trim();
  }

  if (
    event.type === "command.started" ||
    event.type === "command.completed" ||
    event.type === "command.failed"
  ) {
    const payload = event.payload as Partial<CommandLifecyclePayload>;
    const command = payload.command ?? "command";
    const status = payload.status ?? event.type.replace("command.", "");
    const exit = typeof payload.exitCode === "number" ? ` | exit ${payload.exitCode}` : "";
    return `${command} ${status}${exit}`;
  }

  if (event.type === "approval.requested") {
    const payload = event.payload as ApprovalRequestedPayload;
    const request = payload.request as Record<string, unknown>;
    if (payload.kind === "apply_patch") {
      const patchId = readRequestPatchId(request);
      return patchId ? `Patch approval requested for ${patchId}` : "Patch approval requested";
    }
    return `Approval requested for ${readRequestText(request, "command", "command")} in ${readRequestText(request, "cwd", readRequestText(request, "workspaceRoot", "."))}`;
  }

  if (event.type === "approval.resolved") {
    const payload = event.payload as ApprovalResolvedPayload;
    return `Approval ${payload.decision}`;
  }

  if (event.type === "patch.proposed") {
    const payload = event.payload as PatchProposedPayload;
    return `${payload.summary} | ${payload.filesChanged} file(s) changed`;
  }

  if (event.type.startsWith("task.")) {
    const payload = event.payload as Partial<TaskUpdatedPayload>;
    return payload.detail ?? `Task status changed to ${payload.status ?? "unknown"}`;
  }

  if (
    event.type === "tool.started" ||
    event.type === "tool.completed" ||
    event.type === "tool.failed"
  ) {
    const payload = event.payload as Partial<ToolLifecyclePayload>;
    const result = summarizeValue(
      getPayloadValue(event.payload, ["result", "output", "content", "summary"]),
      "",
      120,
    );
    const error = summarizeValue(getPayloadValue(event.payload, ["error", "errorJson"]), "", 120);
    const detail = error || result;
    return payload.toolName
      ? `${payload.toolName} ${event.type.replace("tool.", "")}${detail ? ` | ${detail}` : ""}`
      : "Tool lifecycle event";
  }

  try {
    return JSON.stringify(event.payload);
  } catch {
    return "Unable to serialize event payload.";
  }
}

function describeMode(hostStatus: HostStatus | null): string {
  if (!hostStatus) {
    return "Detecting runtime";
  }

  return hostStatus.runtimeRunning ? "Connected to local runtime" : "Browser / Mock mode";
}

export function App() {
  const [hostStatus, setHostStatus] = useState<HostStatus | null>(null);
  const [config, setConfig] = useState<RuntimeConfig | null>(null);
  const [providerSettings, setProviderSettings] = useState<ProviderSettingsForm>(() =>
    buildProviderSettingsForm(null),
  );
  const [commandPolicySettings, setCommandPolicySettings] = useState<CommandPolicyForm>(() =>
    buildCommandPolicyForm(null),
  );
  const [activeProviderProfileId, setActiveProviderProfileId] = useState("default");
  const [providerTestResult, setProviderTestResult] = useState<ProviderTestResult | null>(null);
  const [searchGlob, setSearchGlob] = useState(DEFAULT_SEARCH_GLOB_TEXT);
  const [searchIgnoreText, setSearchIgnoreText] = useState("");
  const [workspacePath, setWorkspacePath] = useState(DEFAULT_WORKSPACE_PATH);
  const [sessionTitle, setSessionTitle] = useState(DEFAULT_SESSION_TITLE);
  const [workspace, setWorkspace] = useState<WorkspaceRef | null>(null);
  const [sessions, setSessions] = useState<Array<SessionRecord>>([]);
  const [session, setSession] = useState<SessionRecord | null>(null);
  const [taskHistory, setTaskHistory] = useState<Array<TaskRecord>>([]);
  const [task, setTask] = useState<TaskRecord | null>(null);
  const [events, setEvents] = useState<Array<AgentEventEnvelope>>([]);
  const [traceEvents, setTraceEvents] = useState<Array<TraceEventRecord>>([]);
  const [patchCacheById, setPatchCacheById] = useState<Record<string, PatchRecord>>({});
  const [prompt, setPrompt] = useState(DEFAULT_PROMPT);
  const [assistantOutput, setAssistantOutput] = useState("");
  const [chatMessages, setChatMessages] = useState<ChatMessageView[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [workspaceBusy, setWorkspaceBusy] = useState(false);
  const [sessionBusy, setSessionBusy] = useState(false);
  const [messageBusy, setMessageBusy] = useState(false);
  const [refreshBusy, setRefreshBusy] = useState(false);
  const [sessionListBusy, setSessionListBusy] = useState(false);
  const [providerConfigBusy, setProviderConfigBusy] = useState(false);
  const [providerTestBusy, setProviderTestBusy] = useState(false);
  const [commandPolicyBusy, setCommandPolicyBusy] = useState(false);
  const [searchConfigBusy, setSearchConfigBusy] = useState(false);
  const [approvalBusyId, setApprovalBusyId] = useState<string | null>(null);
  const [patchBusyId, setPatchBusyId] = useState<string | null>(null);
  const [traceBusy, setTraceBusy] = useState(false);
  const [traceError, setTraceError] = useState<string | null>(null);
  const [taskControlBusyAction, setTaskControlBusyAction] = useState<TaskControlAction | null>(null);
  const [taskControlError, setTaskControlError] = useState<string | null>(null);
  const [activeTaskId, setActiveTaskId] = useState<string | null>(null);

  useEffect(() => {
    let disposed = false;

    Promise.all([
      runtimeClient.getHostStatus(),
      runtimeClient.getConfig(),
      runtimeClient.listSessions(),
      runtimeClient.listTasks(),
    ])
      .then(([nextHostStatus, nextConfig, nextSessions, nextTasks]) => {
        if (disposed) {
          return;
        }

        const normalizedConfig = normalizeRuntimeConfig(nextConfig.config);
        setHostStatus(nextHostStatus);
        setConfig(normalizedConfig);
        setProviderSettings(buildProviderSettingsForm(normalizedConfig));
        setCommandPolicySettings(buildCommandPolicyForm(normalizedConfig));
        setActiveProviderProfileId(normalizedConfig.provider.activeProfileId ?? "default");
        setSearchGlob(serializePatternList(normalizedConfig.search.glob));
        setSearchIgnoreText(serializePatternList(normalizedConfig.search.ignore));
        setSessions(nextSessions.sessions);
        setTaskHistory(nextTasks.tasks);
        const initialSession = nextSessions.sessions[0] ?? null;
        const initialTask = initialSession
          ? sortByUpdatedAtDesc(nextTasks.tasks.filter((item) => item.sessionId === initialSession.id))[0] ?? null
          : sortByUpdatedAtDesc(nextTasks.tasks)[0] ?? null;
        setSession(initialSession);
        setTask(initialTask);
        setActiveTaskId(initialTask?.id ?? null);

        if (nextConfig.config.workspace.rootPath) {
          setWorkspacePath(nextConfig.config.workspace.rootPath);
        }
      })
      .catch((reason) => {
        if (!disposed) {
          setError(reason instanceof Error ? reason.message : String(reason));
        }
      })
      .finally(() => {
        if (!disposed) {
          setLoading(false);
        }
      });

    return () => {
      disposed = true;
    };
  }, []);

  useEffect(() => {
    let active = true;
    let dispose: (() => void) | undefined;

    runtimeClient
      .subscribeEvents((event) => {
        if (!active) {
          return;
        }

        setEvents((current) => [...current, event].slice(-80));
        setTask((current) => applyEventToTask(current, event));
        setTaskHistory((current) => {
          const existing = current.find((item) => item.id === event.taskId);
          if (!existing) {
            return current;
          }

          const updated = applyEventToTask(existing, event);
          return updated ? upsertRecord(current, updated) : current;
        });

        if (event.type.startsWith("task.")) {
          setSession((current) =>
            current && current.id === event.sessionId
              ? { ...current, updatedAt: event.ts }
              : current,
          );
          setSessions((current) =>
            current.map((item) => (item.id === event.sessionId ? { ...item, updatedAt: event.ts } : item)),
          );
        }

        if (event.type === "assistant.token") {
          const payload = event.payload as AssistantTokenPayload;
          setAssistantOutput((current) => `${current}${payload.delta}`);
          setChatMessages((current) => appendAssistantToken(current, event));
        }

        if (event.type === "assistant.message.completed") {
          setChatMessages((current) => completeAssistantMessage(current, event));
        }
      })
      .then((unlisten) => {
        dispose = unlisten;
      })
      .catch((reason) => {
        if (active) {
          setError(reason instanceof Error ? reason.message : String(reason));
        }
      });

    return () => {
      active = false;
      dispose?.();
    };
  }, []);

  async function loadTraceForTask(taskId: string, isCancelled: () => boolean = () => false) {
    setTraceBusy(true);
    setTraceError(null);

    try {
      const result = await runtimeClient.listTrace({
        taskId,
        limit: TRACE_LIMIT,
      });
      if (!isCancelled()) {
        setTraceEvents(result.traceEvents);
      }
    } catch (reason) {
      if (!isCancelled()) {
        setTraceEvents([]);
        setTraceError(reason instanceof Error ? reason.message : String(reason));
      }
    } finally {
      if (!isCancelled()) {
        setTraceBusy(false);
      }
    }
  }

  const traceAutoRefreshStatus =
    task && task.id === activeTaskId && TRACE_AUTO_REFRESH_STATUSES.has(task.status)
      ? task.status
      : undefined;

  useEffect(() => {
    if (!activeTaskId) {
      setTraceEvents([]);
      setTraceError(null);
      setTraceBusy(false);
      return;
    }

    let cancelled = false;
    void loadTraceForTask(activeTaskId, () => cancelled);
    return () => {
      cancelled = true;
    };
  }, [activeTaskId, traceAutoRefreshStatus]);

  useEffect(() => {
    setTaskControlError(null);
  }, [task?.id, task?.status]);

  const eventItems = useMemo(
    () => [...events].reverse().map((event) => buildActivityItem(event)),
    [events],
  );
  const traceItems = useMemo(
    () =>
      [...traceEvents]
        .sort((left, right) => right.sequence - left.sequence)
        .map((trace) => buildTracePanelItem(trace)),
    [traceEvents],
  );
  const activeProviderProfile = useMemo(
    () => config?.provider.profiles?.find((profile) => profile.id === activeProviderProfileId),
    [activeProviderProfileId, config],
  );
  const providerStatusView = getProviderStatusView(providerSettings, providerTestResult);
  const providerRuntimeNotice = getProviderRuntimeNotice(providerSettings, providerTestResult);
  const providerHealthView = getProviderHealthView(activeProviderProfile, providerTestResult);

  const toolTimelineItems = useMemo<ToolTimelineItem[]>(() => {
    const items = new Map<string, ToolTimelineItem>();

    for (const event of events) {
      if (
        event.type !== "tool.started" &&
        event.type !== "tool.completed" &&
        event.type !== "tool.failed"
      ) {
        continue;
      }

      const payload = event.payload as Partial<ToolLifecyclePayload>;
      const toolCallId = payload.toolCallId ?? event.eventId;
      const current = items.get(toolCallId);
      const status = event.type.replace("tool.", "") as ToolTimelineItem["status"];
      const durationMs =
        readEventNumber(event.payload, "durationMs") ??
        (current ? event.ts - current.startedAt : undefined);
      const resultSummary = summarizeValue(
        getPayloadValue(event.payload, ["result", "output", "content", "summary"]),
        current?.resultSummary ?? "waiting for result",
      );
      const errorSummary = summarizeValue(
        getPayloadValue(event.payload, ["error", "errorJson", "message"]),
        "",
      );

      items.set(toolCallId, {
        id: toolCallId,
        taskId: event.taskId,
        toolCallId,
        toolName: payload.toolName ?? current?.toolName ?? "unknown_tool",
        status,
        argsSummary: summarizeValue(
          payload.arguments ?? getPayloadValue(event.payload, ["args", "input", "parameters"]),
          current?.argsSummary ?? "not recorded",
        ),
        resultSummary,
        errorSummary: errorSummary || current?.errorSummary,
        startedAt: current?.startedAt ?? event.ts,
        updatedAt: event.ts,
        finishedAt: status === "started" ? current?.finishedAt : event.ts,
        durationMs: status === "started" ? current?.durationMs : durationMs,
        eventCount: (current?.eventCount ?? 0) + 1,
      });
    }

    return Array.from(items.values()).sort((left, right) => right.updatedAt - left.updatedAt);
  }, [events]);

  const approvalCards = useMemo<ApprovalCardView[]>(() => {
    const cards = new Map<string, ApprovalCardView>();

    for (const event of events) {
      if (event.type === "approval.requested") {
        const payload = event.payload as ApprovalRequestedPayload;
        const request = payload.request as Record<string, unknown>;
        const filesChanged = readRequestOptionalNumber(request, ["filesChanged", "files_changed"]);
        const changedFiles = readRequestStringList(request, ["files", "filesChangedList", "paths"]);
        const patchId = payload.kind === "apply_patch" ? payload.patchId ?? readRequestPatchId(request) : undefined;
        const patchSummary = readRequestText(request, "summary", readRequestText(request, "patchSummary", "patch approval request"));
        const command = readRequestText(request, "command", payload.kind === "apply_patch" ? "apply_patch" : "command");
        cards.set(payload.approvalId, {
          approvalId: payload.approvalId,
          taskId: payload.taskId,
          kind: payload.kind,
          patchId,
          patchSummary,
          filesChanged,
          command,
          cwd: readRequestText(request, "cwd", readRequestText(request, "workspaceRoot", ".")),
          shell: readRequestText(request, "shell", "system default"),
          timeoutMs: readRequestNumber(request, "timeoutMs", 0),
          risk: readRequestText(request, "risk", payload.kind === "apply_patch" ? "writes files" : "executes command"),
          requestJson: stringifyRequestJson(request),
          requestSummary:
            payload.kind === "apply_patch"
              ? `${patchSummary}${filesChanged !== undefined ? ` | ${filesChanged} file(s)` : ""}${
                  changedFiles.length > 0 ? ` | ${changedFiles.slice(0, 3).join(", ")}` : ""
                }`
              : `${command} | cwd ${readRequestText(request, "cwd", readRequestText(request, "workspaceRoot", "."))}`,
          status: "pending",
          requestedAt: event.ts,
          updatedAt: event.ts,
          requestedEventId: event.eventId,
        });
      }

      if (event.type === "approval.resolved") {
        const payload = event.payload as ApprovalResolvedPayload;
        const current = cards.get(payload.approvalId);
        if (current) {
          cards.set(payload.approvalId, {
            ...current,
            status: payload.decision,
            resolvedAt: event.ts,
            updatedAt: event.ts,
            resolvedEventId: event.eventId,
          });
          continue;
        }

        cards.set(payload.approvalId, {
          approvalId: payload.approvalId,
          taskId: payload.taskId,
          kind: "run_command",
          patchId: undefined,
          command: "unknown",
          cwd: ".",
          shell: "system default",
          timeoutMs: 0,
          risk: "not recorded",
          requestJson: "{}",
          requestSummary: "Resolved approval was received before the request event.",
          status: payload.decision,
          requestedAt: event.ts,
          updatedAt: event.ts,
          resolvedAt: event.ts,
          resolvedEventId: event.eventId,
        });
      }
    }

    return sortByUpdatedAtDesc(Array.from(cards.values()));
  }, [events]);

  const approvalByPatchId = useMemo(() => {
    const cards = new Map<string, ApprovalCardView>();
    for (const approval of approvalCards) {
      if (approval.patchId && !cards.has(approval.patchId)) {
        cards.set(approval.patchId, approval);
      }
    }
    return cards;
  }, [approvalCards]);

  const patchCards = useMemo<PatchCardView[]>(() => {
    const cards = new Map<string, PatchCardView>();

    for (const event of events) {
      if (event.type === "patch.proposed") {
        const payload = event.payload as PatchProposedPayload;
        const patchId = readEventText(payload, "patchId");
        if (!patchId) {
          continue;
        }
        const diffText = readEventText(payload, "diffText");
        cards.set(patchId, {
          patchId,
          taskId: event.taskId,
          summary: payload.summary,
          filesChanged: payload.filesChanged,
          status: "proposed",
          requestedAt: event.ts,
          updatedAt: event.ts,
          diffText,
        });
      }
    }

    for (const [patchId, patch] of Object.entries(patchCacheById)) {
      const current = cards.get(patchId);
      cards.set(patchId, {
        patchId,
        taskId: patch.taskId,
        summary: patch.summary,
        filesChanged: patch.filesChanged,
        status: patch.status,
        requestedAt: current?.requestedAt ?? patch.createdAt,
        updatedAt: Math.max(current?.updatedAt ?? patch.updatedAt, patch.updatedAt),
        diffText: patch.diffText,
      });
    }

    for (const card of cards.values()) {
      const approval = approvalByPatchId.get(card.patchId);
      if (approval) {
        card.approvalId = approval.approvalId;
        card.approvalStatus = approval.status;
        card.approvalResolvedAt = approval.resolvedAt;
        card.updatedAt = Math.max(card.updatedAt, approval.updatedAt);
        if (approval.status === "approved") {
          card.status = "approved";
        } else if (approval.status === "rejected") {
          card.status = "rejected";
        }
      }
    }

    return sortByUpdatedAtDesc(Array.from(cards.values()));
  }, [approvalByPatchId, events, patchCacheById]);

  const patchCardById = useMemo(() => {
    return new Map(patchCards.map((patch) => [patch.patchId, patch]));
  }, [patchCards]);

  const commandOutputByTaskId = useMemo(() => {
    const outputs = new Map<string, string[]>();

    for (const event of events) {
      if (event.type !== "command.output") {
        continue;
      }

      const payload = event.payload as CommandOutputPayload;
      const chunk = payload.chunk.trim();
      const summary = chunk ? `${payload.stream}: ${chunk}` : `${payload.stream}: output event`;
      const next = outputs.get(event.taskId) ?? [];
      outputs.set(event.taskId, [...next, summary].slice(-3));
    }

    return outputs;
  }, [events]);

  const planSteps = useMemo<Array<PlanStep>>(() => task?.plan ?? [], [task]);

  const visibleTaskHistory = useMemo(() => {
    const scopedTasks = session ? taskHistory.filter((item) => item.sessionId === session.id) : taskHistory;
    return sortByUpdatedAtDesc(scopedTasks);
  }, [taskHistory, session]);

  const visibleChatMessages = useMemo(
    () =>
      chatMessages.filter(
        (message) => message.taskId === activeTaskId || message.taskId === "pending",
      ),
    [activeTaskId, chatMessages],
  );
  const taskControlActions = useMemo(() => getTaskControlActions(task?.status), [task?.status]);

  async function ensureWorkspace(): Promise<WorkspaceRef> {
    if (workspace) {
      return workspace;
    }

    const result = await runtimeClient.openWorkspace(workspacePath.trim());
    setWorkspace(result.workspace);
    setConfig((current) =>
      current
        ? {
            ...current,
            workspace: {
              ...current.workspace,
              rootPath: result.workspace.rootPath,
              writableRoots: [result.workspace.rootPath],
            },
          }
        : current,
    );
    return result.workspace;
  }

  function selectSession(nextSession: SessionRecord | null) {
    setSession(nextSession);
    setActiveTaskId(null);
    setTask(null);
    setEvents([]);
    setTraceEvents([]);
    setTraceError(null);
    setPatchCacheById({});
    setPatchBusyId(null);
    setAssistantOutput("");
    setChatMessages([]);
    setApprovalBusyId(null);

    if (!nextSession) {
      return;
    }

    const nextTask = sortByUpdatedAtDesc(
      taskHistory.filter((item) => item.sessionId === nextSession.id),
    )[0];
    setTask(nextTask ?? null);
    setActiveTaskId(nextTask?.id ?? null);
  }

  function selectTask(taskId: string) {
    const nextTask = taskHistory.find((item) => item.id === taskId);
    if (!nextTask) {
      return;
    }

    setTask(nextTask);
    setActiveTaskId(nextTask.id);
  }

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
    const profileName = providerSettings.name.trim() || "Provider profile";

    if (!model) {
      throw new Error("Provider model is required.");
    }
    if (providerSettings.mode === "openai-compatible" && !baseUrl) {
      throw new Error("Base URL is required for OpenAI-compatible mode.");
    }

    const temperature = parseProviderNumber(providerSettings.temperature, "Temperature", {
      min: 0,
      max: 2,
    });
    const maxTokens = parseProviderNumber(providerSettings.maxTokens, "Max tokens", {
      integer: true,
      min: 1,
    });
    const maxContextTokens = parseProviderNumber(providerSettings.maxContextTokens, "Max context tokens", {
      integer: true,
      min: 1,
    });
    const timeout = parseProviderNumber(providerSettings.timeout, "Timeout", {
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
  }

  async function refreshSessionHistory(preferredSessionId?: string) {
    setSessionListBusy(true);
    setError(null);

    try {
      const result = await runtimeClient.listSessions();
      const taskResult = await runtimeClient.listTasks();
      const nextSessions = result.sessions;
      const nextTasks = taskResult.tasks;
      setSessions(nextSessions);
      setTaskHistory(nextTasks);

      const preferredSession =
        nextSessions.find((item) => item.id === preferredSessionId) ??
        (session ? nextSessions.find((item) => item.id === session.id) : undefined) ??
        nextSessions[0] ??
        null;

      setSession(preferredSession);

      if (!preferredSession) {
        setTask(null);
        setActiveTaskId(null);
        return;
      }

      const nextTask = sortByUpdatedAtDesc(
        nextTasks.filter((item) => item.sessionId === preferredSession.id),
      )[0];
      setTask(nextTask ?? null);
      setActiveTaskId(nextTask?.id ?? null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setSessionListBusy(false);
    }
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
    return normalized;
  }

  async function runProviderTest(provider?: AppConfig["provider"], profileId = activeProviderProfileId) {
    setProviderTestBusy(true);
    setProviderTestResult(null);

    try {
      const result = await runtimeClient.testProvider(provider ? { profileId, provider } : { profileId });
      setProviderTestResult(result);
      if (!provider) {
        const nextConfig = await runtimeClient.getConfig();
        const normalized = normalizeRuntimeConfig(nextConfig.config);
        setConfig(normalized);
        setProviderSettings(buildProviderSettingsForm(normalized));
        setActiveProviderProfileId(normalized.provider.activeProfileId ?? profileId);
      }
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

  async function handleOpenWorkspace() {
    if (!workspacePath.trim()) {
      setError("Enter a workspace path before connecting.");
      return;
    }

    setWorkspaceBusy(true);
    setError(null);

    try {
      const result = await runtimeClient.openWorkspace(workspacePath.trim());
      setWorkspace(result.workspace);
      setSession(null);
      setTask(null);
      setTaskHistory([]);
      setSessions([]);
      setActiveTaskId(null);
      setTraceEvents([]);
      setTraceError(null);
      setApprovalBusyId(null);
      setPatchCacheById({});
      setPatchBusyId(null);
      setEvents([]);
      setAssistantOutput("");
      setChatMessages([]);
      await refreshSessionHistory();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setWorkspaceBusy(false);
    }
  }

  async function handleCreateSession() {
    setSessionBusy(true);
    setError(null);

    try {
      const nextWorkspace = await ensureWorkspace();
      const result = await runtimeClient.createSession({
        workspaceId: nextWorkspace.id,
        title: sessionTitle.trim() || DEFAULT_SESSION_TITLE,
      });

      setSessions((current) => upsertRecord(current, result.session));
      selectSession(result.session);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setSessionBusy(false);
    }
  }

  async function handleSaveSearchConfig() {
    setSearchConfigBusy(true);
    setError(null);

    try {
      await persistSearchConfig();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
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
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setProviderConfigBusy(false);
    }
  }

  async function handleSaveCommandPolicyConfig() {
    setCommandPolicyBusy(true);
    setError(null);

    try {
      await persistCommandPolicyConfig();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
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
      setError(reason instanceof Error ? reason.message : String(reason));
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
        name: `Profile ${(config.provider.profiles?.length ?? 0) + 1}`,
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
      setError(reason instanceof Error ? reason.message : String(reason));
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
        name: `${source.name || "Provider profile"} Copy`,
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
      setError(reason instanceof Error ? reason.message : String(reason));
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
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setProviderConfigBusy(false);
    }
  }

  async function handleSendMessage() {
    if (!prompt.trim()) {
      setError("Enter a task description before sending.");
      return;
    }

    setMessageBusy(true);
    setError(null);

    try {
      await persistSearchConfig();
      const activeSession = session ?? (await ensureSessionForSend());
      const messageContent = prompt.trim();
      const pendingUserMessageId = `user_${Date.now()}`;
      setEvents([]);
      setTraceEvents([]);
      setTraceError(null);
      setPatchCacheById({});
      setPatchBusyId(null);
      setAssistantOutput("");
      setChatMessages([
        {
          id: pendingUserMessageId,
          taskId: "pending",
          role: "user",
          content: messageContent,
          createdAt: Date.now(),
          updatedAt: Date.now(),
        },
      ]);
      setApprovalBusyId(null);

      const result = await runtimeClient.sendMessage({
        sessionId: activeSession.id,
        content: messageContent,
        attachments: [],
      });

      setChatMessages((current) =>
        current.map((message) =>
          message.id === pendingUserMessageId ? { ...message, taskId: result.task.id } : message,
        ),
      );
      setTaskHistory((current) => upsertRecord(current, result.task));
      setTask(result.task);
      setActiveTaskId(result.task.id);
      const touchedSession = { ...activeSession, updatedAt: result.task.updatedAt };
      setSessions((current) => upsertRecord(current, touchedSession));
      setSession(touchedSession);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setMessageBusy(false);
    }
  }

  async function ensureSessionForSend(): Promise<SessionRecord> {
    const nextWorkspace = await ensureWorkspace();
    const result = await runtimeClient.createSession({
      workspaceId: nextWorkspace.id,
      title: sessionTitle.trim() || DEFAULT_SESSION_TITLE,
    });
    setSessions((current) => upsertRecord(current, result.session));
    setSession(result.session);
    return result.session;
  }

  async function handleRefreshTask() {
    if (!task) {
      return;
    }

    setRefreshBusy(true);
    setError(null);

    try {
      const result = await runtimeClient.getTask(task.id);
      setTask(result.task);
      setActiveTaskId(result.task.id);
      setTaskHistory((current) => upsertRecord(current, result.task));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setRefreshBusy(false);
    }
  }

  async function refreshTaskControlState(taskId: string) {
    const [taskResult, taskListResult] = await Promise.all([
      runtimeClient.getTask(taskId),
      runtimeClient.listTasks(),
    ]);
    setTask(taskResult.task);
    setTaskHistory(taskListResult.tasks);
    setActiveTaskId(taskId);
    await loadTraceForTask(taskId);
  }

  async function handleTaskControl(action: TaskControlAction) {
    if (!task) {
      return;
    }

    const taskId = task.id;
    setTaskControlBusyAction(action);
    setTaskControlError(null);
    setError(null);

    try {
      if (action === "cancel") {
        await runtimeClient.cancelTask({ taskId });
      } else if (action === "pause") {
        await runtimeClient.pauseTask({ taskId });
      } else {
        await runtimeClient.resumeTask({ taskId });
      }

      await refreshTaskControlState(taskId);
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : String(reason);
      setTaskControlError(message);
      setError(message);
    } finally {
      setTaskControlBusyAction(null);
    }
  }

  async function handleRefreshTrace() {
    if (!activeTaskId) {
      setTraceEvents([]);
      setTraceError(null);
      return;
    }

    await loadTraceForTask(activeTaskId);
  }

  async function handleLoadPatchDiff(patchId: string) {
    setPatchBusyId(patchId);
    setError(null);

    try {
      const result = await runtimeClient.diffGet({ patchId });
      setPatchCacheById((current) => ({
        ...current,
        [patchId]: {
          ...result.patch,
          diffText: result.diffText || result.patch.diffText,
        },
      }));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setPatchBusyId((current) => (current === patchId ? null : current));
    }
  }

  async function handleApprovalSubmit(approvalId: string, decision: "approved" | "rejected") {
    setApprovalBusyId(approvalId);
    setError(null);

    try {
      await runtimeClient.approvalSubmit({
        approvalId,
        decision,
      });
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setApprovalBusyId((current) => (current === approvalId ? null : current));
    }
  }

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <p className="eyebrow">Local AI Coding Agent</p>
          <h1>Sprint 1 Workspace</h1>
          <p className="muted">
            The UI uses bridge-driven data first and falls back to a controlled browser mock path when Tauri is
            unavailable.
          </p>
        </div>

        <section className="panel">
          <div className="section-header">
            <h2>Bridge</h2>
            <span className={`badge ${hostStatus?.runtimeRunning ? "ok" : "warn"}`}>
              {describeMode(hostStatus)}
            </span>
          </div>
          <dl className="meta">
            <div>
              <dt>Transport</dt>
              <dd>{hostStatus?.runtimeTransport ?? (loading ? "loading..." : "unavailable")}</dd>
            </div>
            <div>
              <dt>Event Channel</dt>
              <dd>{hostStatus?.eventChannel ?? "agent://event"}</dd>
            </div>
            <div>
              <dt>Model</dt>
              <dd>{config?.provider.defaultModel ?? "loading..."}</dd>
            </div>
            <div>
              <dt>Approval</dt>
              <dd>{config?.policy.approvalMode ?? "loading..."}</dd>
            </div>
          </dl>
        </section>

        <section className="panel">
          <div className="section-header">
            <h2>Provider</h2>
            <span className={`badge ${providerStatusView.badgeClass}`}>
              {providerStatusView.label}
            </span>
          </div>
          <div className="provider-form">
            <label className="field">
              <span>Profile</span>
              <select value={activeProviderProfileId} onChange={(event) => selectProviderProfile(event.target.value)}>
                {(config?.provider.profiles ?? []).map((profile) => (
                  <option key={profile.id} value={profile.id}>
                    {profile.name}
                  </option>
                ))}
              </select>
            </label>
            <div className="provider-profile-actions">
              <button
                type="button"
                className="secondary"
                onClick={handleCreateProviderProfile}
                disabled={providerConfigBusy || loading || !config}
              >
                New Profile
              </button>
              <button
                type="button"
                className="secondary"
                onClick={handleCopyProviderProfile}
                disabled={providerConfigBusy || loading || !config}
              >
                Copy Profile
              </button>
              <button
                type="button"
                className="secondary danger"
                onClick={handleDeleteProviderProfile}
                disabled={providerConfigBusy || loading || !config}
              >
                Delete Profile
              </button>
            </div>
            <label className="field">
              <span>Profile name</span>
              <input
                value={providerSettings.name}
                onChange={(event) => updateProviderSetting("name", event.target.value)}
                placeholder="Default"
              />
            </label>
            <label className="field">
              <span>Mode</span>
              <select
                value={providerSettings.mode}
                onChange={(event) => updateProviderSetting("mode", event.target.value as ProviderMode)}
              >
                <option value="mock">Mock / deterministic</option>
                <option value="openai-compatible">OpenAI compatible</option>
              </select>
            </label>
            <label className="field">
              <span>Base URL</span>
              <input
                value={providerSettings.baseUrl}
                onChange={(event) => updateProviderSetting("baseUrl", event.target.value)}
                placeholder={DEFAULT_PROVIDER_BASE_URL}
              />
            </label>
            <label className="field">
              <span>Model</span>
              <input
                value={providerSettings.model}
                onChange={(event) => updateProviderSetting("model", event.target.value)}
                placeholder={DEFAULT_PROVIDER_MODEL}
              />
            </label>
            <label className="field">
              <span>API key env var</span>
              <input
                value={providerSettings.apiKeyEnvVarName}
                onChange={(event) => updateProviderSetting("apiKeyEnvVarName", event.target.value)}
                placeholder={DEFAULT_PROVIDER_API_KEY_ENV_VAR}
              />
            </label>
            <div className="field-grid">
              <label className="field">
                <span>Temperature</span>
                <input
                  type="number"
                  min="0"
                  max="2"
                  step="0.1"
                  value={providerSettings.temperature}
                  onChange={(event) => updateProviderSetting("temperature", event.target.value)}
                />
              </label>
              <label className="field">
                <span>Max tokens</span>
                <input
                  type="number"
                  min="1"
                  step="1"
                  value={providerSettings.maxTokens}
                  onChange={(event) => updateProviderSetting("maxTokens", event.target.value)}
                />
              </label>
              <label className="field">
                <span>Max context</span>
                <input
                  type="number"
                  min="1"
                  step="1"
                  value={providerSettings.maxContextTokens}
                  onChange={(event) => updateProviderSetting("maxContextTokens", event.target.value)}
                />
              </label>
              <label className="field">
                <span>Timeout sec</span>
                <input
                  type="number"
                  min="1"
                  step="1"
                  value={providerSettings.timeout}
                  onChange={(event) => updateProviderSetting("timeout", event.target.value)}
                />
              </label>
            </div>
          </div>
          <p className="help-text">
            API key values are not stored here. Set {providerSettings.apiKeyEnvVarName || DEFAULT_PROVIDER_API_KEY_ENV_VAR}
            in the runtime environment before using a real provider.
          </p>
          {providerRuntimeNotice ? <p className="provider-runtime-notice">{providerRuntimeNotice}</p> : null}
          <div className={`provider-status ${providerHealthView.badgeClass}`}>
            <strong>{providerTestResult?.message ?? "Most recent health check for this profile."}</strong>
            <dl className="provider-detail-grid">
              <div>
                <dt>Profile</dt>
                <dd>{providerTestResult?.profileName ?? providerSettings.name}</dd>
              </div>
              <div>
                <dt>Base URL</dt>
                <dd className="break">{providerTestResult?.baseUrl ?? providerSettings.baseUrl}</dd>
              </div>
              <div>
                <dt>Model</dt>
                <dd>{providerTestResult?.model ?? providerSettings.model}</dd>
              </div>
              <div>
                <dt>Env var</dt>
                <dd>{providerTestResult?.checkedEnvVarName ?? providerSettings.apiKeyEnvVarName}</dd>
              </div>
              <div>
                <dt>Last checked</dt>
                <dd>{providerHealthView.checkedAtText}</dd>
              </div>
              <div>
                <dt>Status</dt>
                <dd>{providerHealthView.statusText}</dd>
              </div>
              <div className="provider-detail-full">
                <dt>Error summary</dt>
                <dd>{providerHealthView.summaryText}</dd>
              </div>
            </dl>
          </div>
          <div className="actions split-actions">
            <button type="button" onClick={handleSaveProviderConfig} disabled={providerConfigBusy || loading || !config}>
              {providerConfigBusy ? "Saving..." : "Save Current Profile"}
            </button>
            <button
              type="button"
              className="secondary"
              onClick={handleTestProvider}
              disabled={providerTestBusy || loading || !config}
            >
              {providerTestBusy ? "Testing..." : "Test Connection"}
            </button>
          </div>
        </section>

        <section className="panel">
          <h2>Workspace</h2>
          <label className="field">
            <span>Root Path</span>
            <input
              value={workspacePath}
              onChange={(event) => setWorkspacePath(event.target.value)}
              placeholder={DEFAULT_WORKSPACE_PATH}
            />
          </label>
          <button type="button" onClick={handleOpenWorkspace} disabled={workspaceBusy || loading}>
            {workspaceBusy ? "Connecting..." : "Connect Workspace"}
          </button>
          <dl className="meta compact">
            <div>
              <dt>Name</dt>
              <dd>{workspace?.name ?? "not connected"}</dd>
            </div>
            <div>
              <dt>Root</dt>
              <dd className="break">{workspace?.rootPath ?? "waiting for initialization"}</dd>
            </div>
          </dl>
        </section>

        <section className="panel">
          <div className="section-header">
            <h2>Search Config</h2>
            <button type="button" onClick={handleSaveSearchConfig} disabled={searchConfigBusy || loading || !config}>
              {searchConfigBusy ? "Saving..." : "Save"}
            </button>
          </div>
          <label className="field">
            <span>Glob</span>
            <input value={searchGlob} onChange={(event) => setSearchGlob(event.target.value)} placeholder="app/src/**/*.tsx" />
          </label>
          <label className="field">
            <span>Ignore</span>
            <textarea
              className="short"
              value={searchIgnoreText}
              onChange={(event) => setSearchIgnoreText(event.target.value)}
              placeholder={".git\nnode_modules\ndist"}
            />
          </label>
          <p className="help-text">One glob or ignore pattern per line. Blank glob means search every non-ignored file.</p>
        </section>

        <section className="panel">
          <div className="section-header">
            <h2>Command Policy</h2>
            <button
              type="button"
              onClick={handleSaveCommandPolicyConfig}
              disabled={commandPolicyBusy || loading || !config}
            >
              {commandPolicyBusy ? "Saving..." : "Save"}
            </button>
          </div>
          <div className="config-form">
            <label className="field">
              <span>Allowed shell</span>
              <select
                value={commandPolicySettings.allowedShell}
                onChange={(event) =>
                  updateCommandPolicySetting(
                    "allowedShell",
                    event.target.value as CommandPolicyForm["allowedShell"],
                  )}
              >
                <option value="powershell">PowerShell</option>
                <option value="bash">Bash</option>
                <option value="zsh">Zsh</option>
              </select>
            </label>
            <label className="field">
              <span>Allowed commands / allowlist</span>
              <textarea
                className="short config-textarea"
                value={commandPolicySettings.allowedCommands}
                onChange={(event) => updateCommandPolicySetting("allowedCommands", event.target.value)}
                placeholder={"git\nnpm\npytest"}
              />
            </label>
            <label className="field">
              <span>Denied commands / denylist</span>
              <textarea
                className="short config-textarea"
                value={commandPolicySettings.deniedCommands}
                onChange={(event) => updateCommandPolicySetting("deniedCommands", event.target.value)}
                placeholder={"del\nRemove-Item\ncurl"}
              />
            </label>
            <label className="field">
              <span>Blocked patterns</span>
              <textarea
                className="short config-textarea"
                value={commandPolicySettings.blockedPatterns}
                onChange={(event) => updateCommandPolicySetting("blockedPatterns", event.target.value)}
                placeholder={"rm -rf\nshutdown\nformat"}
              />
            </label>
            <label className="field">
              <span>Allowed cwd roots</span>
              <textarea
                className="short config-textarea"
                value={commandPolicySettings.allowedCwdRoots}
                onChange={(event) => updateCommandPolicySetting("allowedCwdRoots", event.target.value)}
                placeholder={workspacePath || DEFAULT_WORKSPACE_PATH}
              />
            </label>
          </div>
          <p className="help-text">
            One entry per line. Saving keeps `allowedCommands` and `allowlist` mirrored, and also mirrors
            `deniedCommands` with `denylist`.
          </p>
        </section>

        <section className="panel">
          <div className="section-header">
            <h2>Session History</h2>
            <button type="button" onClick={() => refreshSessionHistory()} disabled={sessionListBusy || loading}>
              {sessionListBusy ? "Refreshing..." : "Refresh"}
            </button>
          </div>
          <ul className="history-list">
            {sessions.length > 0 ? (
              sessions.map((item) => (
                <li key={item.id}>
                  <button
                    type="button"
                    className={`history-card ${session?.id === item.id ? "active" : ""}`}
                    onClick={() => selectSession(item)}
                  >
                    <strong>{item.title}</strong>
                    <span>{item.status}</span>
                    <span>{formatTimestamp(item.updatedAt)}</span>
                    {item.summary ? <span className="muted">{item.summary}</span> : null}
                  </button>
                </li>
              ))
            ) : (
              <li className="empty-state">
                <strong>No sessions yet</strong>
                <span>Create one or send a message to populate history.</span>
              </li>
            )}
          </ul>
        </section>

        <section className="panel">
          <h2>Session</h2>
          <label className="field">
            <span>Title</span>
            <input
              value={sessionTitle}
              onChange={(event) => setSessionTitle(event.target.value)}
              placeholder={DEFAULT_SESSION_TITLE}
            />
          </label>
          <button type="button" onClick={handleCreateSession} disabled={sessionBusy || loading}>
            {sessionBusy ? "Creating..." : "Create Session"}
          </button>
          <dl className="meta compact">
            <div>
              <dt>ID</dt>
              <dd>{session?.id ?? "not created"}</dd>
            </div>
            <div>
              <dt>Status</dt>
              <dd>{session?.status ?? "idle"}</dd>
            </div>
            <div>
              <dt>Updated</dt>
              <dd>{formatTimestamp(session?.updatedAt)}</dd>
            </div>
          </dl>
        </section>

        <section className="panel">
          <div className="section-header">
            <h2>Current Task</h2>
            <button type="button" onClick={handleRefreshTask} disabled={!task || refreshBusy}>
              {refreshBusy ? "Refreshing..." : "Refresh"}
            </button>
          </div>
          <dl className="meta compact">
            <div>
              <dt>ID</dt>
              <dd>{task?.id ?? "no task yet"}</dd>
            </div>
            <div>
              <dt>Status</dt>
              <dd>
                <span className={`badge ${getTaskBadgeClass(task?.status)}`}>
                  {task?.status ?? "idle"}
                </span>
              </dd>
            </div>
            <div>
              <dt>Error</dt>
              <dd>{task?.errorCode ?? "none"}</dd>
            </div>
            <div>
              <dt>Updated</dt>
              <dd>{formatTimestamp(task?.updatedAt)}</dd>
            </div>
          </dl>
          {task && taskControlActions.length > 0 ? (
            <div className="task-controls">
              <div className="section-header task-controls-header">
                <h3>Task Controls</h3>
                <span className="muted">Active task: {task.id}</span>
              </div>
              <div className="task-control-actions">
                {taskControlActions.map((control) => {
                  const isBusy = taskControlBusyAction === control.action;
                  return (
                    <button
                      key={control.action}
                      type="button"
                      className={`secondary ${control.tone === "warn" ? "warn" : ""}`}
                      onClick={() => handleTaskControl(control.action)}
                      disabled={Boolean(taskControlBusyAction)}
                    >
                      {isBusy ? control.busyLabel : control.label}
                    </button>
                  );
                })}
              </div>
              {taskControlError ? <p className="error-banner compact">{taskControlError}</p> : null}
            </div>
          ) : null}
        </section>

        <section className="panel">
          <div className="section-header">
            <h2>Task History</h2>
            <span className="muted">{visibleTaskHistory.length} item(s)</span>
          </div>
          <ul className="history-list">
            {visibleTaskHistory.length > 0 ? (
              visibleTaskHistory.map((item) => (
                <li key={item.id}>
                  <button
                    type="button"
                    className={`history-card ${activeTaskId === item.id ? "active" : ""}`}
                    onClick={() => selectTask(item.id)}
                  >
                    <strong>{item.goal}</strong>
                    <span>
                      <span className={`badge mini ${getTaskBadgeClass(item.status)}`}>
                        {item.status}
                      </span>
                    </span>
                    <span>{formatTimestamp(item.updatedAt)}</span>
                  </button>
                </li>
              ))
            ) : (
              <li className="empty-state">
                <strong>No tasks yet</strong>
                <span>Send a message to start recording task history.</span>
              </li>
            )}
          </ul>
        </section>
      </aside>

      <main className="main">
        <section className="panel hero">
          <p className="eyebrow">Current Goal</p>
          <h2>{task?.goal ?? "Connect a workspace and create a session first."}</h2>
          <p className="muted">
            On startup the app reads host status, config and session history. After you send a message, task, plan and
            event stream panels all update from bridge-driven data.
          </p>
          {task?.resultSummary ? <p className="success-banner">{task.resultSummary}</p> : null}
          {error ? <p className="error-banner">{error}</p> : null}
        </section>

        <section className="grid">
          <section className="panel">
            <div className="section-header">
              <h2>Plan</h2>
              <span className="muted">{planSteps.length} step(s)</span>
            </div>
            <ul className="steps">
              {planSteps.length > 0 ? (
                planSteps.map((step) => (
                  <li key={step.id} data-status={step.status}>
                    <strong>{step.title}</strong>
                    <span>{step.detail ?? "No detail yet."}</span>
                  </li>
                ))
              ) : (
                <li>
                  <strong>No plan yet</strong>
                  <span>Send a message and the runtime will populate the first task plan.</span>
                </li>
              )}
            </ul>
          </section>

          <section className="panel">
            <div className="section-header">
              <h2>Chat</h2>
              <span className="muted">
                {visibleChatMessages.length > 0
                  ? `${visibleChatMessages.length} message(s)`
                  : "waiting"}
              </span>
            </div>
            <div className="chat-list">
              {visibleChatMessages.length > 0 ? (
                visibleChatMessages.map((message) => (
                  <article key={message.id} className="chat-message" data-role={message.role}>
                    <div className="chat-meta">
                      <strong>{message.role}</strong>
                      <span>{formatTimestamp(message.updatedAt)}</span>
                      {message.streaming ? <span className="streaming-pill">streaming</span> : null}
                    </div>
                    <p>{message.content}</p>
                  </article>
                ))
              ) : (
                <p className="empty-state compact">
                  <strong>No chat messages yet</strong>
                  <span>User messages appear after send; assistant.token events stream into one assistant bubble.</span>
                </p>
              )}
            </div>
          </section>
        </section>

        <section className="panel">
          <div className="section-header">
            <h2>Composer</h2>
            <span className="muted">
              {hostStatus?.runtimeRunning ? "Connected to desktop runtime" : "Using browser/mock fallback"}
            </span>
          </div>
          <textarea value={prompt} onChange={(event) => setPrompt(event.target.value)} placeholder={DEFAULT_PROMPT} />
          {providerRuntimeNotice ? <p className="provider-runtime-notice composer">{providerRuntimeNotice}</p> : null}
          <div className="actions">
            <button type="button" onClick={handleSendMessage} disabled={messageBusy || loading}>
              {messageBusy ? "Sending..." : "Send Message"}
            </button>
            <span className="muted">
              Session: {session?.title ?? "auto-create on send"} | Workspace: {workspace?.name ?? "auto-connect on send"}
            </span>
          </div>
        </section>

        <section className="panel">
          <div className="section-header">
            <h2>Runtime Timeline</h2>
            <span className="muted">{eventItems.length} event(s)</span>
          </div>
          <section className="trace-stack">
            <div className="section-header approval-header">
              <h3>Persistent Trace</h3>
              <div className="timeline-badges">
                <span className="muted">{traceItems.length} trace item(s)</span>
                <button type="button" onClick={handleRefreshTrace} disabled={!activeTaskId || traceBusy}>
                  {traceBusy ? "Loading..." : "Refresh"}
                </button>
              </div>
            </div>
            {traceError ? <p className="error-banner compact">{traceError}</p> : null}
            {traceItems.length > 0 ? (
              <ul className="trace-list">
                {traceItems.map((item) => (
                  <li key={item.id} className="trace-card">
                    <div className="timeline-row">
                      <div className="timeline-title">
                        <strong>{item.type}</strong>
                        <span className="muted">source: {item.source}</span>
                      </div>
                      <div className="timeline-badges">
                        <span className="badge mini neutral">#{item.sequence}</span>
                        <span>{item.time}</span>
                      </div>
                    </div>
                    <dl className="meta compact trace-meta">
                      <div>
                        <dt>related</dt>
                        <dd className="break">{item.relatedId}</dd>
                      </div>
                      <div>
                        <dt>task</dt>
                        <dd className="break">{activeTaskId ?? "none"}</dd>
                      </div>
                    </dl>
                    <code className="trace-payload">{item.payloadSummary}</code>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="empty-state compact">
                <strong>No persistent trace loaded</strong>
                <span>
                  Select a task or click Refresh to load trace.list. Runtime events and tool timeline remain separate.
                </span>
              </p>
            )}
          </section>
          <section className="tool-stack">
            <div className="section-header approval-header">
              <h3>Tool Timeline</h3>
              <span className="muted">{toolTimelineItems.length} call(s)</span>
            </div>
            {toolTimelineItems.length > 0 ? (
              <ul className="tool-list">
                {toolTimelineItems.map((item) => {
                  const badgeClass =
                    item.status === "completed" ? "ok" : item.status === "failed" ? "error" : "info";
                  return (
                    <li key={item.id} className="tool-card" data-status={item.status}>
                      <div className="section-header approval-topline">
                        <div>
                          <strong>{item.toolName}</strong>
                          <span className="muted">toolCallId: {item.toolCallId}</span>
                        </div>
                        <span className={`badge ${badgeClass}`}>{item.status}</span>
                      </div>
                      <dl className="meta compact tool-meta">
                        <div>
                          <dt>duration</dt>
                          <dd>{formatDuration(item.durationMs)}</dd>
                        </div>
                        <div>
                          <dt>task</dt>
                          <dd className="break">{item.taskId}</dd>
                        </div>
                        <div>
                          <dt>started</dt>
                          <dd>{formatTimestamp(item.startedAt)}</dd>
                        </div>
                        <div>
                          <dt>updated</dt>
                          <dd>{formatTimestamp(item.updatedAt)}</dd>
                        </div>
                      </dl>
                      <div className="tool-summary-grid">
                        <div>
                          <span className="label">Args</span>
                          <code>{item.argsSummary}</code>
                        </div>
                        <div>
                          <span className="label">{item.status === "failed" ? "Error" : "Result"}</span>
                          <code>{item.errorSummary ?? item.resultSummary}</code>
                        </div>
                      </div>
                    </li>
                  );
                })}
              </ul>
            ) : (
              <p className="empty-state compact">
                <strong>No tool calls yet</strong>
                <span>tool.started, tool.completed and tool.failed events will aggregate here by toolCallId.</span>
              </p>
            )}
          </section>
          <section className="patch-stack">
            <div className="section-header approval-header">
              <h3>Patches</h3>
              <span className="muted">{patchCards.length} item(s)</span>
            </div>
            {patchCards.length > 0 ? (
              <ul className="patch-list">
                {patchCards.map((item) => {
                  const approvalPending = item.approvalStatus === "pending" && item.approvalId;
                  const hasLoadedDiff = Boolean(patchCacheById[item.patchId]?.diffText || item.diffText);
                  const diffText = patchCacheById[item.patchId]?.diffText ?? item.diffText;
                  const badgeClass =
                    item.status === "approved" || item.status === "applied"
                      ? "ok"
                      : item.status === "rejected" || item.status === "failed"
                        ? "error"
                        : "warn";

                  return (
                    <li key={item.patchId} className="patch-card" data-status={item.status}>
                      <div className="section-header approval-topline">
                        <div>
                          <strong>{item.summary}</strong>
                          <span className="muted">patchId: {item.patchId}</span>
                        </div>
                        <span className={`badge ${badgeClass}`}>{item.status}</span>
                      </div>
                      <dl className="meta compact patch-meta">
                        <div>
                          <dt>files changed</dt>
                          <dd>{item.filesChanged}</dd>
                        </div>
                        <div>
                          <dt>task</dt>
                          <dd className="break">{item.taskId}</dd>
                        </div>
                        <div>
                          <dt>requested</dt>
                          <dd>{formatTimestamp(item.requestedAt)}</dd>
                        </div>
                        <div>
                          <dt>updated</dt>
                          <dd>{formatTimestamp(item.updatedAt)}</dd>
                        </div>
                      </dl>
                      <div className="patch-actions">
                        <button
                          type="button"
                          onClick={() => handleLoadPatchDiff(item.patchId)}
                          disabled={patchBusyId === item.patchId || loading}
                        >
                          {patchBusyId === item.patchId
                            ? "Loading..."
                            : hasLoadedDiff
                              ? "Reload diff"
                              : "Load diff"}
                        </button>
                        {approvalPending ? (
                          <>
                            <button
                              type="button"
                              onClick={() => handleApprovalSubmit(item.approvalId as string, "approved")}
                              disabled={approvalBusyId === item.approvalId || loading}
                            >
                              Approve patch
                            </button>
                            <button
                              type="button"
                              className="secondary"
                              onClick={() => handleApprovalSubmit(item.approvalId as string, "rejected")}
                              disabled={approvalBusyId === item.approvalId || loading}
                            >
                              Reject patch
                            </button>
                          </>
                        ) : null}
                      </div>
                      {item.approvalStatus ? (
                        <p className="muted approval-decision">
                          Approval: {item.approvalStatus}
                          {item.approvalResolvedAt ? ` | ${formatTimestamp(item.approvalResolvedAt)}` : ""}
                        </p>
                      ) : null}
                      <pre className="patch-diff">
                        {diffText ?? "Diff not loaded yet. Click Load diff to fetch diff.get."}
                      </pre>
                    </li>
                  );
                })}
              </ul>
            ) : (
              <p className="empty-state compact">
                <strong>No patches yet</strong>
                <span>When the runtime emits patch.proposed, the patch summary and diff will show up here.</span>
              </p>
            )}
          </section>
          <section className="approval-stack">
            <div className="section-header approval-header">
              <h3>Approvals</h3>
              <span className="muted">{approvalCards.length} item(s)</span>
            </div>
            {approvalCards.length > 0 ? (
              <ul className="approval-list">
                {approvalCards.map((item) => {
                  const outputLines = commandOutputByTaskId.get(item.taskId) ?? [];
                  const isPending = item.status === "pending";
                  const patch = item.patchId ? patchCardById.get(item.patchId) : undefined;
                  const diffText = item.patchId
                    ? patchCacheById[item.patchId]?.diffText ?? patch?.diffText
                    : undefined;
                  const hasLoadedDiff = Boolean(diffText);
                  const isPatchApproval = item.kind === "apply_patch";
                  const cardTitle = isPatchApproval
                    ? item.patchSummary ?? patch?.summary ?? "Apply patch"
                    : item.command;
                  return (
                    <li key={item.approvalId} className="approval-card" data-status={item.status}>
                      <div className="section-header approval-topline">
                        <div>
                          <strong>{cardTitle}</strong>
                          <span className="muted">
                            {item.kind} | approvalId: {item.approvalId}
                          </span>
                        </div>
                        <span className={`badge ${getApprovalBadgeClass(item.status)}`}>{item.status}</span>
                      </div>

                      {isPatchApproval ? (
                        <>
                          <dl className="meta compact approval-meta">
                            <div>
                              <dt>files changed</dt>
                              <dd>{item.filesChanged ?? patch?.filesChanged ?? "not recorded"}</dd>
                            </div>
                            <div>
                              <dt>patch id</dt>
                              <dd className="break">{item.patchId ?? "not recorded"}</dd>
                            </div>
                            <div>
                              <dt>summary</dt>
                              <dd>{item.patchSummary ?? patch?.summary ?? item.requestSummary}</dd>
                            </div>
                            <div>
                              <dt>risk</dt>
                              <dd>{item.risk}</dd>
                            </div>
                          </dl>
                          <div className="approval-request-summary">
                            <span className="label">Diff preview</span>
                            <code>{diffText ? diffText.slice(0, 900) : "Diff not loaded yet."}</code>
                          </div>
                          <div className="patch-actions">
                            {item.patchId ? (
                              <button
                                type="button"
                                onClick={() => handleLoadPatchDiff(item.patchId as string)}
                                disabled={patchBusyId === item.patchId || loading}
                              >
                                {patchBusyId === item.patchId
                                  ? "Loading..."
                                  : hasLoadedDiff
                                    ? "Reload diff"
                                    : "Load diff"}
                              </button>
                            ) : null}
                          </div>
                          {diffText ? <pre className="patch-diff">{diffText}</pre> : null}
                        </>
                      ) : (
                        <>
                          <dl className="meta compact approval-meta">
                            <div>
                              <dt>command</dt>
                              <dd className="break">{item.command}</dd>
                            </div>
                            <div>
                              <dt>cwd</dt>
                              <dd className="break">{item.cwd}</dd>
                            </div>
                            <div>
                              <dt>shell</dt>
                              <dd>{item.shell}</dd>
                            </div>
                            <div>
                              <dt>timeout</dt>
                              <dd>{item.timeoutMs > 0 ? `${item.timeoutMs} ms` : "not recorded"}</dd>
                            </div>
                            <div>
                              <dt>risk</dt>
                              <dd>{item.risk}</dd>
                            </div>
                          </dl>
                          <div className="approval-request-summary">
                            <span className="label">Request summary</span>
                            <code>{item.requestSummary}</code>
                          </div>
                          <div className="approval-request-summary">
                            <span className="label">Approval request JSON</span>
                            <code>{item.requestJson}</code>
                          </div>
                          {outputLines.length > 0 ? (
                            <pre className="approval-output">{outputLines.join("\n")}</pre>
                          ) : null}
                        </>
                      )}

                      <dl className="meta compact approval-meta approval-trace-meta">
                        <div>
                          <dt>decision</dt>
                          <dd>{item.status === "pending" ? "pending user decision" : item.status}</dd>
                        </div>
                        <div>
                          <dt>task</dt>
                          <dd className="break">{item.taskId}</dd>
                        </div>
                        <div>
                          <dt>requested</dt>
                          <dd>{formatTimestamp(item.requestedAt)}</dd>
                        </div>
                        <div>
                          <dt>resolved</dt>
                          <dd>{item.resolvedAt ? formatTimestamp(item.resolvedAt) : "not resolved"}</dd>
                        </div>
                        <div>
                          <dt>trace hint</dt>
                          <dd className="break">
                            Refresh Persistent Trace and filter by approvalId/taskId if event history is incomplete.
                          </dd>
                        </div>
                        <div>
                          <dt>event ids</dt>
                          <dd className="break">
                            requested {item.requestedEventId ?? "unknown"}
                            {item.resolvedEventId ? ` | resolved ${item.resolvedEventId}` : ""}
                          </dd>
                        </div>
                      </dl>

                      {isPending ? (
                        <div className="approval-actions">
                          <button
                            type="button"
                            onClick={() => handleApprovalSubmit(item.approvalId, "approved")}
                            disabled={approvalBusyId === item.approvalId || loading}
                          >
                            {approvalBusyId === item.approvalId ? "Submitting..." : "Approve"}
                          </button>
                          <button
                            type="button"
                            className="secondary"
                            onClick={() => handleApprovalSubmit(item.approvalId, "rejected")}
                            disabled={approvalBusyId === item.approvalId || loading}
                          >
                            Reject
                          </button>
                        </div>
                      ) : (
                        <p className="muted approval-decision">
                          Decision: {item.status}
                          {item.resolvedAt ? ` | ${formatTimestamp(item.resolvedAt)}` : ""}
                        </p>
                      )}
                    </li>
                  );
                })}
              </ul>
            ) : (
              <p className="empty-state compact">
                <strong>No approval requests yet</strong>
                <span>When the runtime pauses on a command, the pending approval card appears here.</span>
              </p>
            )}
          </section>
          <ul className="timeline rich">
            {eventItems.length > 0 ? (
              eventItems.map((item) => (
                <li key={item.id} data-category={item.category}>
                  <div className="timeline-row">
                    <div className="timeline-title">
                      <strong>{item.title}</strong>
                      <span className="muted">{item.type}</span>
                    </div>
                    <div className="timeline-badges">
                      {item.status ? <span className="badge mini neutral">{item.status}</span> : null}
                      <span>{item.time}</span>
                    </div>
                  </div>
                  <span>{item.summary}</span>
                  {item.relatedId ? <span className="muted">Related: {item.relatedId}</span> : null}
                  {config?.ui.showRawEvents ? <pre className="raw">{item.raw}</pre> : null}
                </li>
              ))
            ) : (
              <li>
                <strong>No events yet</strong>
                <span>Initialize the workspace and send a message to populate the runtime event stream.</span>
              </li>
            )}
          </ul>
        </section>
      </main>
    </div>
  );
}
