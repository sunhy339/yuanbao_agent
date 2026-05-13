import { useEffect, useMemo, useRef, useState } from "react";
import type {
  ApprovalRequestedPayload,
  ApprovalResolvedPayload,
  AgentEventEnvelope,
  AppConfig,
  AgentSoulConfig,
  AgentSoulProfile,
  AutonomyConfig,
  AutonomyProfile,
  AssistantTokenPayload,
  CommandLogRecord,
  McpServerRecord,
  MessageCreatedPayload,
  MessageDeltaPayload,
  MessageCompletedPayload,
  MessageFailedPayload,
  MessageRecord,
  PatchRecord,
  PatchProposedPayload,
  ProviderMode,
  ProviderProfile,
  ProviderTestResult,
  ScheduledTaskRecord,
  ScheduledTaskRunRecord,
  SessionRecord,
  SessionUpdatedPayload,
  SkillPresetRecord,
  TaskRecord,
  TaskContextPreviewPayload,
  TaskUpdatedPayload,
  ToolRuntimeConfig,
  ToolLifecyclePayload,
  TraceEventRecord,
  WorkspaceRef,
} from "@shared";
import { RuntimeClient, type HostStatus, type RuntimeConfig } from "./lib/runtimeClient";
import {
  appendAssistantPlaceholder,
  appendUserMessage,
  failAssistantMessage,
  getVisibleChatMessages,
  isOperationalAssistantDelta,
  reconcileBackendMessage,
  replaceSessionMessages,
  stopStreamingMessages,
  updateAssistantMessageByMessageId,
  updatePendingMessageTask,
  type ChatMessageView,
} from "./state/chatMessages";
import {
  DEFAULT_PROMPT,
  DEFAULT_SESSION_TITLE,
  DEFAULT_WORKSPACE_PATH,
} from "./state/mockData";
import { dispatchSlashCommand, SLASH_COMMANDS } from "./state/slashCommands";
import { AppShell } from "./ui/workbench/AppShell";
import { getSidebarActiveSessionId, resolveSessionForTab } from "./ui/workbench/sessionRouting";
import {
  closeOtherTabs,
  closeTab,
  getInitialTabs,
  openSessionTab,
  openSystemTab,
} from "./ui/workbench/tabModel";
import type { SystemWorkspaceKind, WorkbenchTab, WorkbenchSession } from "./ui/workbench/types";
import { ToastContainer, createToast, type ToastEntry } from "./ui/workbench/Toast";
import { NewSessionWorkspace } from "./ui/workbench/workspaces/NewSessionWorkspace";
import {
  ScheduledWorkspace,
  type ExecutionLog,
  type ScheduledTask,
  type ScheduledTaskDraft,
} from "./ui/workbench/workspaces/scheduled/ScheduledWorkspace";
import {
  McpWorkspace,
  type McpServerDraft,
} from "./ui/workbench/workspaces/mcp/McpWorkspace";
import { AppearanceWorkspace } from "./ui/workbench/workspaces/appearance/AppearanceWorkspace";
import { ComponentPlaygroundWorkspace } from "./ui/workbench/workspaces/playground/ComponentPlaygroundWorkspace";
import {
  SkillsWorkspace,
  type SkillDraft,
} from "./ui/workbench/workspaces/skills/SkillsWorkspace";
import { WorkbenchOverviewPage } from "./ui/v2/pages/WorkbenchOverviewPage";
import {
  SessionWorkspace,
  type SessionWorkspaceBackgroundJob,
  type SessionWorkspaceCollaboration,
  type SessionWorkspaceContextPreview,
} from "./ui/workbench/workspaces/session/SessionWorkspace";
import { isChatVisibleEvent as shouldShowEventInChat } from "./ui/workbench/workspaces/session/visibilityRouting";
import type { ComposerRuntimeChildTask } from "./ui/workbench/ComposerDock";
import {
  SettingsWorkspace,
  type SettingsComputerUseConfig,
  type SettingsGeneralConfig,
  type SettingsIMConfig,
  type SettingsAgentBehaviorConfig,
  type SettingsProvider,
  type SettingsProviderFeedback,
  type SettingsProviderPayload,
  type SettingsProviderTestResult,
  type SettingsSkillConfig,
} from "./ui/workbench/workspaces/settings/SettingsWorkspace";
import { formatRuntimeModeLabel, formatStatusLabel } from "./ui/copy";

const runtimeClient = new RuntimeClient();
// ── Extracted state modules ──────────────────────────────────────────
import {
  DEFAULT_SEARCH_GLOB_TEXT,
  DEFAULT_PROVIDER_MODE,
  DEFAULT_PROVIDER_BASE_URL,
  DEFAULT_PROVIDER_MODEL,
  DEFAULT_PROVIDER_API_KEY_ENV_VAR,
  DEFAULT_PROVIDER_TEMPERATURE,
  DEFAULT_PROVIDER_MAX_TOKENS,
  TRACE_LIMIT,
  TRACE_AUTO_REFRESH_STATUSES,
  type TaskControlAction,
  type ProviderSettingsForm,
  type CommandPolicyForm,
  isTaskControllable,
  normalizeWorkspacePathForCompare,
  workspaceNameFromPath,
  formatCompactCount,
  normalizeRuntimeConfig,
  normalizeAutonomyConfig,
  normalizeAgentSoulConfig,
  buildSettingsAgentBehaviorConfig,
  normalizeProviderConfig,
  buildProviderSettingsForm,
  buildCommandPolicyForm,
  parseProviderNumber,
  parsePatternText,
  serializePatternList,
} from "./state/providerConfig";
import {
  type ProviderStatusView,
  type ProviderHealthView,
  getProviderStatusView,
  getProviderRuntimeNotice,
  getProviderHealthView,
  buildSettingsProviderLastTest,
  buildDefaultProviderProfile,
} from "./state/providerStatus";
import {
  normalizeSkillForSettings,
  buildMcpServerPayload,
  buildSkillPayload,
} from "./state/mcpSkillPayloads";
import {
  buildProviderProfileFromPayload,
  buildSettingsGeneralConfig,
  settingsLanguageToConfig,
  settingsModeToApprovalMode,
  approvalModeToSettingsMode,
} from "./state/providerPayloadParsing";
import {
  scheduledRecordToWorkspaceTask,
  scheduledRunToExecutionLog,
} from "./state/scheduleHelpers";
import {
  readRequestText,
  readRequestNumber,
  readRequestPatchId,
  readEventText,
  readEventNumber,
  summarizeValue,
  riskToLevel,
  countAddedLines,
  countDeletedLines,
  parsePatchFiles,
  getPayloadValue,
  formatRawValue,
  summarizeToolArguments,
  summarizeToolResult,
} from "./state/traceReaders";
import {
  type ApprovalCardView,
  type PatchCardView,
  type ToolTimelineItem,
  type QueuedPromptSubmission,
  sortByUpdatedAtDesc,
  stringifyRequestJson,
  readRequestStringList,
  readRequestOptionalNumber,
  upsertRecord,
  applyEventToTask,
  taskRecordFromEvent,
} from "./state/eventRecordViews";
import {
  appendAssistantToken,
  completeAssistantMessage,
  failAssistantMessageForEvent,
  messageRecordToChatMessageLocal,
} from "./state/chatTokenHelpers";
import {
  buildSessionContextPreview,
  shouldPromoteTaskToActive,
  buildSessionCollaboration,
  buildSessionBackgroundJobs,
  mergeSessionBackgroundJobs,
} from "./state/sessionDerivedViews";
import { useProviderConfig, type UseProviderConfigDeps } from "./hooks/useProviderConfig";
import { useMcpServers } from "./hooks/useMcpServers";
import { useSkills } from "./hooks/useSkills";
import { useScheduledTasks } from "./hooks/useScheduledTasks";
import { useSettings, type UseSettingsDeps } from "./hooks/useSettings";

function describeMode(hostStatus: HostStatus | null): string {
  if (!hostStatus) {
    return "正在检测运行时";
  }

  return hostStatus.runtimeRunning ? "本地运行时已连接" : "浏览器预览模式";
}

function getProviderDisplayLabel(settings: ProviderSettingsForm): string {
  if (settings.mode === "mock") {
    return "本地预览模型";
  }
  return settings.model || settings.name || "未配置模型";
}

function RuntimeUnavailableWorkspace({ errorMessage }: { errorMessage: string }) {
  return (
    <main className="runtime-unavailable-workspace" aria-labelledby="runtime-unavailable-title">
      <section className="runtime-unavailable-card">
        <p className="runtime-unavailable-kicker">需要运行时</p>
        <h1 id="runtime-unavailable-title">桌面运行时不可用</h1>
        <p className="runtime-unavailable-copy">
          当前 UI 未连接到 Tauri 桌面运行时，因此模型供应商配置、聊天、工具调用和任务执行都会被阻止。
        </p>
        <div className="runtime-unavailable-actions">
          <div>
            <strong>启动真实桌面应用</strong>
            <code>npm run tauri:dev</code>
          </div>
          <div>
            <strong>仅预览浏览器模式</strong>
            <code>VITE_YUANBAO_ENABLE_BROWSER_MOCK=1 npm run dev</code>
          </div>
        </div>
        <div className="runtime-unavailable-detail" role="status">
          <strong>当前失败原因</strong>
          <pre>{errorMessage}</pre>
        </div>
      </section>
    </main>
  );
}

export function App() {
  const [hostStatus, setHostStatus] = useState<HostStatus | null>(null);
  const [config, setConfig] = useState<RuntimeConfig | null>(null);
  const [workspacePath, setWorkspacePath] = useState(DEFAULT_WORKSPACE_PATH);
  const [sessionTitle, setSessionTitle] = useState(DEFAULT_SESSION_TITLE);
  const [workspace, setWorkspace] = useState<WorkspaceRef | null>(null);
  const [sessions, setSessions] = useState<Array<SessionRecord>>([]);
  const [session, setSession] = useState<SessionRecord | null>(null);
  const [taskHistory, setTaskHistory] = useState<Array<TaskRecord>>([]);
  const [task, setTask] = useState<TaskRecord | null>(null);
  const [events, setEvents] = useState<Array<AgentEventEnvelope>>([]);
  const [traceEvents, setTraceEvents] = useState<Array<TraceEventRecord>>([]);
  const [commandLogCacheById, setCommandLogCacheById] = useState<Record<string, CommandLogRecord>>({});
  const [patchCacheById, setPatchCacheById] = useState<Record<string, PatchRecord>>({});
  const [prompt, setPrompt] = useState(DEFAULT_PROMPT);
  const [promptAttachments, setPromptAttachments] = useState<string[]>([]);
  const [queuedPromptSubmissions, setQueuedPromptSubmissions] = useState<QueuedPromptSubmission[]>([]);
  const [chatMessages, setChatMessages] = useState<ChatMessageView[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [toasts, setToasts] = useState<ToastEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [workspaceBusy, setWorkspaceBusy] = useState(false);
  const [workspaceFocusBusy, setWorkspaceFocusBusy] = useState(false);
  const [workspaceMemoryBusy, setWorkspaceMemoryBusy] = useState(false);
  const [sessionBusy, setSessionBusy] = useState(false);
  const [messageBusy, setMessageBusy] = useState(false);
  const [refreshBusy, setRefreshBusy] = useState(false);
  const [sessionListBusy, setSessionListBusy] = useState(false);
  const [approvalBusyId, setApprovalBusyId] = useState<string | null>(null);
  const [patchBusyId, setPatchBusyId] = useState<string | null>(null);
  const [commandJobBusyId, setCommandJobBusyId] = useState<string | null>(null);
  const [traceBusy, setTraceBusy] = useState(false);
  const [traceError, setTraceError] = useState<string | null>(null);
  const [taskControlBusyAction, setTaskControlBusyAction] = useState<TaskControlAction | null>(null);
  const [taskControlError, setTaskControlError] = useState<string | null>(null);
  const [activeTaskId, setActiveTaskId] = useState<string | null>(null);
  const sessionActiveTaskMapRef = useRef<Map<string, string>>(new Map());

  // ── Extracted custom hooks ──────────────────────────────────────────
  const providerHook = useProviderConfig({ addToast, toastError, setError, config, setConfig });
  const settingsHook = useSettings({ addToast, toastError, setError, config, setConfig });
  const scheduledHook = useScheduledTasks({ addToast, toastError, setError });
  const skillsHook = useSkills({ addToast, toastError, setError });
  const mcpHook = useMcpServers({ addToast, toastError, setError });

  function addToast(kind: ToastEntry["kind"], message: string) {
    setToasts((current) => [...current.slice(-4), createToast(kind, message)]);
  }

  function getErrorMessage(reason: unknown): string {
    return reason instanceof Error ? reason.message : String(reason);
  }

  function toastError(reason: unknown) {
    addToast("error", getErrorMessage(reason));
  }

  function buildComputerUseStatus(): string {
    const clipboardAvailable =
      typeof navigator !== "undefined" &&
      typeof navigator.clipboard?.writeText === "function";
    const desktopBridgeAvailable = runtimeClient.canOpenLocalAppPaths();
    const ready = [
      clipboardAvailable ? "clipboard" : null,
      desktopBridgeAvailable ? "desktop bridge" : null,
      "manual confirmation",
    ].filter(Boolean);
    const pending = ["screenshot capture", "computer-use action runtime", "permission audit"];

    return `${new Date().toLocaleTimeString("zh-CN", { hour12: false })} ready: ${ready.join(", ")}; pending: ${pending.join(", ")}`;
  }

  function dismissToast(id: string) {
    setToasts((current) => current.filter((t) => t.id !== id));
  }

  // Destructure hook returns for convenience
  const {
    providerSettings, setProviderSettings,
    commandPolicySettings, setCommandPolicySettings,
    activeProviderProfileId, setActiveProviderProfileId,
    providerTestResult, setProviderTestResult,
    providerFeedback, setProviderFeedback,
    searchGlob, setSearchGlob,
    searchIgnoreText, setSearchIgnoreText,
    providerConfigBusy, setProviderConfigBusy,
    providerTestBusy,
    commandPolicyBusy, searchConfigBusy,
    activeProviderProfile,
    buildProviderProfileFromForm,
    updateProviderSetting, updateCommandPolicySetting,
    selectProviderProfile,
    handleSaveSearchConfig, handleSaveProviderConfig, handleSaveCommandPolicyConfig,
    handleTestProvider, handleTestSelectedProvider,
    handleTestProviderConfigFromSettings,
    handleAddProviderFromSettings, handleEditProviderFromSettings,
    persistSearchConfig,
  } = providerHook;
  const {
    generalSettings, setGeneralSettings,
    imSettings, setIMSettings,
    computerUseSettings, setComputerUseSettings,
    handleGeneralSettingsChange, handleAgentBehaviorChange,
    handlePermissionModeChange, handleOpenAppPath,
    handleCopyRuntimeText, handleRecheckComputerUse,
  } = settingsHook;
  const {
    scheduledRecords, setScheduledRecords,
    scheduledLogs, setScheduledLogs,
    selectedScheduledTaskId, setSelectedScheduledTaskId,
    scheduledBusyTaskId, setScheduledBusyTaskId,
    scheduledCreateBusy, setScheduledCreateBusy,
    refreshScheduledRecords,
    handleRunScheduledTask, handleToggleScheduledTask,
    handleSelectScheduledTask, handleCreateScheduledTask,
  } = scheduledHook;
  const {
    skills, setSkills,
    skillBusyId, setSkillBusyId,
    refreshSkills,
    handleCreateSkill, handleUpdateSkill, handleDeleteSkill, handleImportSkills,
  } = skillsHook;
  const {
    mcpServers, setMcpServers,
    mcpBusyServerId, setMcpBusyServerId,
    mcpLoading, setMcpLoading,
    mcpLastRefresh, setMcpLastRefresh,
    mcpError, setMcpError,
    refreshMcpServers,
    handleCreateMcpServer, handleImportMcpServers, handleUpdateMcpServer,
    handleToggleMcpServer, handleRefreshMcpTools, handleDeleteMcpServer,
  } = mcpHook;

  const [openTabs, setOpenTabs] = useState<WorkbenchTab[]>(() => getInitialTabs());
  const [activeTabId, setActiveTabId] = useState<WorkbenchTab["id"]>("system:overview");
  const pendingAssistantTokenEventsRef = useRef<AgentEventEnvelope[]>([]);
  const assistantTokenFlushTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const messageLoadRequestRef = useRef(0);
  const childTaskIdsRef = useRef<Set<string>>(new Set()); // deprecated: kept for backward compat, visibility routing preferred

  /** Check if event should be routed to chat stream (visibility=chat or no visibility for backward compat). */
  function isChatVisibleEvent(event: AgentEventEnvelope): boolean {
    return shouldShowEventInChat(event, childTaskIdsRef.current);
  }

  /** Update activeTaskId state and persist it in per-session map. */
  function setActiveTaskForSession(taskId: string | null, sessionId?: string | null) {
    setActiveTaskId(taskId);
    const sid = sessionId ?? session?.id;
    if (sid) {
      if (taskId) {
        sessionActiveTaskMapRef.current.set(sid, taskId);
      } else {
        sessionActiveTaskMapRef.current.delete(sid);
      }
    }
  }

  function clearPendingAssistantTokens() {
    pendingAssistantTokenEventsRef.current = [];
    if (assistantTokenFlushTimerRef.current !== null) {
      clearTimeout(assistantTokenFlushTimerRef.current);
      assistantTokenFlushTimerRef.current = null;
    }
  }

  function flushPendingAssistantTokens() {
    const pendingEvents = pendingAssistantTokenEventsRef.current;
    if (!pendingEvents.length) {
      return;
    }

    pendingAssistantTokenEventsRef.current = [];
    if (assistantTokenFlushTimerRef.current !== null) {
      clearTimeout(assistantTokenFlushTimerRef.current);
      assistantTokenFlushTimerRef.current = null;
    }

    setChatMessages((current) =>
      pendingEvents.reduce((nextMessages, event) => appendAssistantToken(nextMessages, event), current),
    );
  }

  function queueAssistantToken(event: AgentEventEnvelope) {
    const payload = event.payload as AssistantTokenPayload;
    const delta = payload.delta ?? "";
    if (!delta || isOperationalAssistantDelta(delta)) {
      return;
    }

    pendingAssistantTokenEventsRef.current.push(event);
    if (assistantTokenFlushTimerRef.current !== null) {
      return;
    }

    assistantTokenFlushTimerRef.current = setTimeout(() => {
      assistantTokenFlushTimerRef.current = null;
      flushPendingAssistantTokens();
    }, 33);
  }

  async function loadSessionMessages(sessionId: string | null | undefined) {
    if (!sessionId) {
      return;
    }

    const requestId = messageLoadRequestRef.current + 1;
    messageLoadRequestRef.current = requestId;
    try {
      const result = await runtimeClient.listMessages({ sessionId, limit: 500 });
      if (messageLoadRequestRef.current !== requestId) {
        return;
      }
      setChatMessages((current) => replaceSessionMessages(current, sessionId, result.messages));
    } catch (reason) {
      if (messageLoadRequestRef.current === requestId) {
        toastError(reason);
      }
    }
  }

  useEffect(() => {
    let disposed = false;

    Promise.all([
      runtimeClient.getHostStatus(),
      runtimeClient.getConfig(),
      runtimeClient.listSessions(),
      runtimeClient.listTasks(),
      runtimeClient.listScheduledTasks(),
      runtimeClient.listSkills().catch(() => ({ skills: [] as SkillPresetRecord[] })),
      runtimeClient.listMcpServers().catch(() => ({ servers: [] as McpServerRecord[] })),
    ])
      .then(([nextHostStatus, nextConfig, nextSessions, nextTasks, nextScheduledTasks, nextSkills, nextMcpServers]) => {
        if (disposed) {
          return;
        }

        const normalizedConfig = normalizeRuntimeConfig(nextConfig.config);
        setHostStatus(nextHostStatus);
        setConfig(normalizedConfig);
        setProviderSettings(buildProviderSettingsForm(normalizedConfig));
        setCommandPolicySettings(buildCommandPolicyForm(normalizedConfig));
        setGeneralSettings(buildSettingsGeneralConfig(normalizedConfig));
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
        setActiveTaskForSession(initialTask?.id ?? null, initialSession?.id);
        void loadSessionMessages(initialSession?.id);
        setScheduledRecords(nextScheduledTasks.tasks);
        setSelectedScheduledTaskId(nextScheduledTasks.tasks[0]?.id ?? null);
        setSkills(nextSkills.skills);
        setMcpServers(nextMcpServers.servers);

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

        // --- New message lifecycle events (P1.3 / P1.4) ---
        // message.delta: streaming token, routed by messageId
        if (event.type === "message.delta") {
          if (!isChatVisibleEvent(event)) {
            return;
          }
          const payload = event.payload as MessageDeltaPayload;
          const delta = payload.delta ?? "";
          if (!delta || isOperationalAssistantDelta(delta)) {
            return;
          }
          const messageId = payload.messageId;
          if (messageId) {
            setChatMessages((current) =>
              updateAssistantMessageByMessageId(current, messageId, (msg) => ({
                ...msg,
                taskId: event.taskId,
                content: msg.placeholder ? delta : `${msg.content}${delta}`,
                updatedAt: event.ts,
                placeholder: false,
              })),
            );
          } else {
            // Fallback: no messageId, use legacy behavior
            queueAssistantToken(event);
          }
          return;
        }

        // message.created: reconcile local pending message with backend message
        if (event.type === "message.created") {
          const payload = event.payload as MessageCreatedPayload;
          const msg = payload.message;
          if (!msg) return;
          // Only process user/assistant messages
          if (msg.role !== "user" && msg.role !== "assistant") return;
          const chatMsg = messageRecordToChatMessageLocal(msg);
          if (!chatMsg) return;
          setChatMessages((current) => reconcileBackendMessage(current, chatMsg));
          setEvents((current) => [...current, event].slice(-500));
          return;
        }

        // message.completed: finalize assistant message by messageId
        if (event.type === "message.completed") {
          const payload = event.payload as MessageCompletedPayload;
          flushPendingAssistantTokens();
          if (payload.messageId) {
            setChatMessages((current) =>
              updateAssistantMessageByMessageId(current, payload.messageId!, (msg) => {
                // Prefer existing streaming content if richer
                const streamingContent = msg.content || "";
                const isPlaceholder = msg.placeholder === true || streamingContent === "\u601d\u8003\u4e2d..." || streamingContent.length < 5;
                return {
                  ...msg,
                  taskId: event.taskId || msg.taskId,
                  content: isPlaceholder ? (payload.content || streamingContent) : streamingContent,
                  updatedAt: event.ts,
                  streaming: false,
                  placeholder: false,
                  status: "completed",
                };
              }),
            );
          } else {
            // Fallback: no messageId, use legacy completion
            setChatMessages((current) => completeAssistantMessage(current, event));
          }
          setEvents((current) => [...current, event].slice(-500));
          return;
        }

        // message.failed: mark assistant message as failed by messageId
        if (event.type === "message.failed") {
          const payload = event.payload as MessageFailedPayload;
          flushPendingAssistantTokens();
          if (payload.messageId) {
            setChatMessages((current) =>
              failAssistantMessage(current, {
                messageId: payload.messageId,
                sessionId: event.sessionId,
                taskId: event.taskId,
                content: payload.content || "任务失败，未返回具体错误。",
                now: event.ts,
              }),
            );
          } else {
            setChatMessages((current) => failAssistantMessageForEvent(current, event));
          }
          setEvents((current) => [...current, event].slice(-500));
          return;
        }

        // --- Legacy assistant.token (kept for backward compat) ---
        if (event.type === "assistant.token") {
          if (isChatVisibleEvent(event)) {
            queueAssistantToken(event);
          }
          return;
        }

        setEvents((current) => [...current, event].slice(-500));

        if (event.type === "session.updated") {
          const payload = (event.payload ?? {}) as SessionUpdatedPayload;
          setSession((current) =>
            current && current.id === event.sessionId
              ? {
                  ...current,
                  title: payload.title ?? current.title,
                  status: (payload.status as SessionRecord["status"] | undefined) ?? current.status,
                  summary: payload.summary ?? current.summary,
                  updatedAt: event.ts,
                }
              : current,
          );
          setSessions((current) =>
            current.map((item) =>
              item.id === event.sessionId
                ? {
                    ...item,
                    title: payload.title ?? item.title,
                    status: (payload.status as SessionRecord["status"] | undefined) ?? item.status,
                    summary: payload.summary ?? item.summary,
                    updatedAt: event.ts,
                  }
                : item,
            ),
          );
        }

        if (event.type.startsWith("task.")) {
          const eventTask = taskRecordFromEvent(event);
          const isChildWorker = (event.payload as Record<string, unknown>)?.childWorker === true;
          if (isChildWorker && event.type === "task.started") {
            childTaskIdsRef.current.add(event.taskId);
          }
          setActiveTaskId((current) => {
            if (isChildWorker) return current;
            if (event.type === "task.started") {
              const next = eventTask && shouldPromoteTaskToActive(eventTask, current) ? event.taskId : current;
              if (next && next !== current) sessionActiveTaskMapRef.current.set(event.sessionId, next);
              return next;
            }
            const next = !current && eventTask && shouldPromoteTaskToActive(eventTask, current) ? event.taskId : current;
            if (next && next !== current) sessionActiveTaskMapRef.current.set(event.sessionId, next);
            return next;
          });
          setTask((current) => {
            if (event.type === "task.started" && isChildWorker) {
              return current;
            }
            if (event.type === "task.started" && eventTask && !shouldPromoteTaskToActive(eventTask, current?.id ?? null)) {
              return current;
            }
            if (!current && event.type !== "task.started") {
              return current;
            }
            if (current && current.id !== event.taskId && event.type !== "task.started") {
              return current;
            }
            return applyEventToTask(current, event) ?? eventTask ?? current;
          });
          setTaskHistory((current) => {
            const existing = current.find((item) => item.id === event.taskId);
            const updated = existing ? applyEventToTask(existing, event) : eventTask;
            if (!updated) {
              return current;
            }

            return upsertRecord(current, updated);
          });
          setSession((current) =>
            current && current.id === event.sessionId
              ? { ...current, updatedAt: event.ts }
              : current,
          );
          setSessions((current) =>
            current.map((item) => (item.id === event.sessionId ? { ...item, updatedAt: event.ts } : item)),
          );
          // task.failed: only update task panel, don't create chat bubble
          // (message.failed handles the chat bubble now)
          if (event.type === "task.failed" && !isChildWorker) {
            flushPendingAssistantTokens();
            // Legacy fallback: only create failure bubble if no message.failed was received
            // (handled by message.failed event now)
          }
        }

        // Legacy assistant.message.completed (kept for backward compat)
        if (event.type === "assistant.message.completed") {
          if (!isChatVisibleEvent(event)) {
            // skip non-chat event completion
          } else {
            flushPendingAssistantTokens();
            setChatMessages((current) => completeAssistantMessage(current, event));
          }
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
      clearPendingAssistantTokens();
      dispose?.();
    };
  }, []);

  async function loadTraceForTask(taskId: string, isCancelled: () => boolean = () => false) {
    setTraceBusy(true);
    setTraceError(null);

    try {
      const [result, commandResult] = await Promise.all([
        runtimeClient.listTrace({
          taskId,
          limit: TRACE_LIMIT,
        }),
        runtimeClient
          .commandLogList({
            taskId,
            limit: TRACE_LIMIT,
          })
          .catch(() => ({ commandLogs: [] as CommandLogRecord[] })),
      ]);
      if (!isCancelled()) {
        setTraceEvents(result.traceEvents);
        setCommandLogCacheById((current) => ({
          ...current,
          ...Object.fromEntries(commandResult.commandLogs.map((log) => [log.id, log])),
        }));
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
      setCommandLogCacheById({});
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
      const toolName = payload.toolName ?? current?.toolName ?? "unknown_tool";
      const argumentValue = payload.arguments ?? getPayloadValue(event.payload, ["args", "input", "parameters"]);
      const resultValue = getPayloadValue(event.payload, ["result", "output", "content", "summary"]);
      const errorValue = getPayloadValue(event.payload, ["error", "errorJson", "message"]);
      const durationMs =
        readEventNumber(event.payload, "durationMs") ??
        (current ? event.ts - current.startedAt : undefined);
      const resultSummary = summarizeToolResult(toolName, resultValue, errorValue, current?.resultSummary ?? "等待结果");
      const errorSummary = summarizeValue(errorValue, "");

      items.set(toolCallId, {
        id: toolCallId,
        taskId: event.taskId,
        toolCallId,
        toolName,
        status,
        argsSummary: summarizeToolArguments(toolName, argumentValue, current?.argsSummary ?? "未记录参数"),
        resultSummary,
        errorSummary: errorSummary || current?.errorSummary,
        argsRaw: formatRawValue(argumentValue) ?? current?.argsRaw,
        resultRaw: formatRawValue(resultValue ?? errorValue) ?? current?.resultRaw,
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

  const visibleChatMessages = useMemo(
    () => getVisibleChatMessages(chatMessages, session?.id),
    [chatMessages, session?.id],
  );

  async function ensureWorkspace(): Promise<WorkspaceRef> {
    const requestedPath = workspacePath.trim();
    if (!requestedPath) {
      throw new Error("Enter a workspace path before connecting.");
    }

    if (workspace && normalizeWorkspacePathForCompare(workspace.rootPath) === normalizeWorkspacePathForCompare(requestedPath)) {
      return workspace;
    }

    const result = await runtimeClient.openWorkspace(requestedPath);
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
    clearPendingAssistantTokens();
    setSession(nextSession);
    setActiveTaskId(null);
    setTask(null);
    setEvents([]);
    setTraceEvents([]);
    setCommandLogCacheById({});
    setTraceError(null);
    setPatchCacheById({});
    setPatchBusyId(null);
    setApprovalBusyId(null);

    if (!nextSession) {
      return;
    }

    void loadSessionMessages(nextSession.id);

    // Restore per-session activeTaskId — prefer persisted map, fallback to latest task
    const restoredTaskId = sessionActiveTaskMapRef.current.get(nextSession.id);
    const fallbackTask = sortByUpdatedAtDesc(
      taskHistory.filter((item) => item.sessionId === nextSession.id),
    )[0];
    const nextTask = restoredTaskId
      ? taskHistory.find((item) => item.id === restoredTaskId) ?? fallbackTask
      : fallbackTask;
    setTask(nextTask ?? null);
    setActiveTaskForSession(nextTask?.id ?? null, nextSession.id);
  }

  function handleOpenSystemTab(kind: SystemWorkspaceKind) {
    setOpenTabs((current) => {
      const result = openSystemTab(current, kind);
      setActiveTabId(result.activeTabId);
      return result.tabs;
    });
  }

  function handleOpenSessionTab(nextSession: WorkbenchSession) {
    setOpenTabs((current) => {
      const result = openSessionTab(current, nextSession);
      setActiveTabId(result.activeTabId);
      return result.tabs;
    });
    selectSession(nextSession);
  }

  function handleActivateTab(tabId: WorkbenchTab["id"]) {
    setActiveTabId(tabId);
    if (!tabId.startsWith("session:")) {
      return;
    }

    const sessionId = tabId.slice("session:".length);
    const nextSession = sessions.find((item) => item.id === sessionId) ?? null;
    selectSession(nextSession);
  }

  function handleCloseTab(tabId: WorkbenchTab["id"]) {
    setOpenTabs((current) => {
      const result = closeTab(current, tabId, activeTabId);
      setActiveTabId(result.activeTabId);
      if (result.activeTabId.startsWith("session:")) {
        const sessionId = result.activeTabId.slice("session:".length);
        selectSession(sessions.find((item) => item.id === sessionId) ?? null);
      } else {
        selectSession(null);
      }
      return result.tabs;
    });
  }

  function handleCloseOtherTabs(tabId: WorkbenchTab["id"]) {
    setOpenTabs((current) => {
      const result = closeOtherTabs(current, tabId);
      setActiveTabId(result.activeTabId);
      if (result.activeTabId.startsWith("session:")) {
        const sessionId = result.activeTabId.slice("session:".length);
        selectSession(sessions.find((item) => item.id === sessionId) ?? null);
      } else {
        selectSession(null);
      }
      return result.tabs;
    });
  }

  async function handleRenameSession(sessionId: string, newTitle: string) {
    try {
      const result = await runtimeClient.updateSession({ sessionId, title: newTitle });
      setSessions((current) => upsertRecord(current, result.session));
      setSession((current) =>
        current && current.id === sessionId ? result.session : current,
      );
      setOpenTabs((current) => {
        const tabId = `session:${sessionId}`;
        return current.map((tab) =>
          tab.id === tabId ? { ...tab, title: newTitle } : tab,
        );
      });
    } catch (err) {
      setError(String(err));
    }
  }

  async function handleDeleteSession(sessionId: string) {
    try {
      await runtimeClient.deleteSession({ sessionId });
      sessionActiveTaskMapRef.current.delete(sessionId);
      setSessions((current) => current.filter((s) => s.id !== sessionId));
      setSession((current) => (current && current.id === sessionId ? null : current));
      setOpenTabs((current) => {
        const tabId = `session:${sessionId}`;
        const remaining = current.filter((tab) => tab.id !== tabId);
        if (remaining.length === current.length) return current;
        const fallback = remaining.length > 0 ? remaining[remaining.length - 1].id : "system:new-session";
        setActiveTabId(fallback);
        if (fallback.startsWith("session:")) {
          const sid = fallback.slice("session:".length);
          selectSession(sessions.find((item) => item.id === sid) ?? null);
        } else {
          selectSession(null);
        }
        return remaining;
      });
    } catch (err) {
      setError(String(err));
    }
  }

  function selectTask(taskId: string) {
    const nextTask = taskHistory.find((item) => item.id === taskId);
    if (!nextTask) {
      return;
    }

    setTask(nextTask);
    setActiveTaskForSession(nextTask.id);
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

      // Restore per-session activeTaskId from map, fallback to latest task
      const restoredTaskId = sessionActiveTaskMapRef.current.get(preferredSession.id);
      const fallbackTask = sortByUpdatedAtDesc(
        nextTasks.filter((item) => item.sessionId === preferredSession.id),
      )[0];
      const nextTask = restoredTaskId
        ? nextTasks.find((item) => item.id === restoredTaskId) ?? fallbackTask
        : fallbackTask;
      setTask(nextTask ?? null);
      setActiveTaskForSession(nextTask?.id ?? null, preferredSession.id);
      void loadSessionMessages(preferredSession.id);
    } catch (reason) {
      toastError(reason);
    } finally {
      setSessionListBusy(false);
    }
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
      clearPendingAssistantTokens();
      setWorkspace(result.workspace);
      setSession(null);
      setTask(null);
      setTaskHistory([]);
      setSessions([]);
      setActiveTaskId(null);
      sessionActiveTaskMapRef.current.clear();
      setTraceEvents([]);
      setCommandLogCacheById({});
      setTraceError(null);
      setApprovalBusyId(null);
      setPatchCacheById({});
      setPatchBusyId(null);
      setEvents([]);
      setChatMessages([]);
      await refreshSessionHistory();
    } catch (reason) {
      toastError(reason);
    } finally {
      setWorkspaceBusy(false);
    }
  }

  async function handleClearWorkspaceMemory() {
    if (!workspace) {
      setError("Open a workspace before clearing project memory.");
      return;
    }

    setWorkspaceMemoryBusy(true);
    setError(null);
    try {
      const result = await runtimeClient.clearWorkspaceMemory({ workspaceId: workspace.id });
      setWorkspace(result.workspace);
    } catch (reason) {
      toastError(reason);
    } finally {
      setWorkspaceMemoryBusy(false);
    }
  }

  async function handleSaveWorkspaceFocus(focus: string) {
    if (!workspace) {
      setError("Open a workspace before saving project focus.");
      return;
    }

    setWorkspaceFocusBusy(true);
    setError(null);
    try {
      const result = await runtimeClient.updateWorkspaceFocus({
        workspaceId: workspace.id,
        focus,
      });
      setWorkspace(result.workspace);
    } catch (reason) {
      toastError(reason);
    } finally {
      setWorkspaceFocusBusy(false);
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
      handleOpenSessionTab(result.session);
    } catch (reason) {
      toastError(reason);
    } finally {
      setSessionBusy(false);
    }
  }

  async function sendMessageContent(
    messageContentInput: string,
    messageAttachmentsInput: string[],
    options: { clearComposer?: boolean; mode?: "new" | "supplement" | "queued" } = {},
  ) {
    if (!messageContentInput.trim() && messageAttachmentsInput.length === 0) {
      setError("Enter a task description before sending.");
      return;
    }

    setMessageBusy(true);
    setError(null);
    let pendingAssistantMessageIdForCatch: string | null = null;
    let pendingSessionIdForCatch: string | null = null;

    try {
      await persistSearchConfig();
      const activeSession =
        activeTab.kind === "session"
          ? activeSessionRecord ?? (await ensureSessionForSend())
          : await ensureSessionForSend();
      pendingSessionIdForCatch = activeSession.id;
      const messageContent = messageContentInput.trim() || "Please review the attached file.";
      const messageAttachments = messageAttachmentsInput;
      const messageCreatedAt = Date.now();
      const clientMessageId = `client_${messageCreatedAt}`;
      const pendingUserMessageId = `user_${messageCreatedAt}`;
      const pendingAssistantMessageId = `assistant_pending_${messageCreatedAt}`;
      const shouldCreateAssistantPlaceholder = options.mode !== "supplement";
      pendingAssistantMessageIdForCatch = shouldCreateAssistantPlaceholder ? pendingAssistantMessageId : null;
      const currentTaskIdBeforeSend = task?.id ?? null;
      if (shouldCreateAssistantPlaceholder) {
        clearPendingAssistantTokens();
      }
      if (options.clearComposer ?? true) {
        setPrompt("");
        setPromptAttachments([]);
      }
      setChatMessages((current) =>
        shouldCreateAssistantPlaceholder
          ? appendAssistantPlaceholder(
              appendUserMessage(current, {
                id: pendingUserMessageId,
                sessionId: activeSession.id,
                content: messageContent,
                now: messageCreatedAt,
                clientMessageId,
              }),
              {
                id: pendingAssistantMessageId,
                sessionId: activeSession.id,
                content: "\u601d\u8003\u4e2d...",
                now: messageCreatedAt + 1,
              },
            )
          : appendUserMessage(current, {
              id: pendingUserMessageId,
              sessionId: activeSession.id,
              content: messageContent,
              now: messageCreatedAt,
              clientMessageId,
            }),
      );
      setApprovalBusyId(null);

      const result = await runtimeClient.sendMessage({
        sessionId: activeSession.id,
        content: messageContent,
        attachments: messageAttachments,
        mode: options.mode,
        taskId: options.mode === "supplement" ? task?.id ?? activeTaskId ?? undefined : undefined,
        newTask: options.mode === "new" ? true : undefined,
        clientMessageId,
      });

      // Reconcile local pending messages with backend-confirmed messages
      if (result.userMessage) {
        const chatMsg = messageRecordToChatMessageLocal(result.userMessage);
        if (chatMsg) {
          setChatMessages((current) => reconcileBackendMessage(current, chatMsg));
        }
      } else {
        setChatMessages((current) => updatePendingMessageTask(current, pendingUserMessageId, result.task.id));
      }
      if (shouldCreateAssistantPlaceholder && result.assistantMessage) {
        const chatMsg = messageRecordToChatMessageLocal(result.assistantMessage);
        if (chatMsg) {
          setChatMessages((current) => reconcileBackendMessage(current, chatMsg));
        }
      } else if (shouldCreateAssistantPlaceholder) {
        setChatMessages((current) => updatePendingMessageTask(current, pendingAssistantMessageId, result.task.id));
      }
      setTaskHistory((current) => upsertRecord(current, result.task));
      if (shouldPromoteTaskToActive(result.task, currentTaskIdBeforeSend)) {
        setTask(result.task);
        setActiveTaskForSession(result.task.id, activeSession.id);
      }
      const touchedSession = { ...activeSession, updatedAt: result.task.updatedAt };
      setSessions((current) => upsertRecord(current, touchedSession));
      setSession(touchedSession);
      setOpenTabs((current) => {
        const resultTabs = openSessionTab(current, touchedSession);
        setActiveTabId(resultTabs.activeTabId);
        return resultTabs.tabs;
      });
    } catch (reason) {
      if (pendingAssistantMessageIdForCatch || options.mode === "supplement") {
        const errorSummary = getErrorMessage(reason);
        setChatMessages((current) =>
          failAssistantMessage(current, {
            messageId: pendingAssistantMessageIdForCatch,
            sessionId: pendingSessionIdForCatch ?? activeSessionRecord?.id ?? session?.id ?? "pending",
            taskId: task?.id ?? activeTaskId ?? undefined,
            content: `发送失败：${errorSummary}`,
            now: Date.now(),
          }),
        );
      }
      toastError(reason);
    } finally {
      setMessageBusy(false);
    }
  }

  async function handleSendMessage() {
    if (!prompt.trim() && promptAttachments.length === 0) {
      setError("Enter a task description before sending.");
      return;
    }

    // Slash command dispatch.
    const slashResult = dispatchSlashCommand(prompt);
    if (slashResult) {
      setPrompt("");
      handleSlashCommand(slashResult);
      return;
    }

    await sendMessageContent(prompt, promptAttachments, {
      clearComposer: true,
      mode: composerCanStop || composerHasStreamingMessage ? "supplement" : "new",
    });
  }

  function handleQueuePrompt() {
    if (!prompt.trim() && promptAttachments.length === 0) {
      setError("Enter a task description before queueing.");
      return;
    }

    const slashResult = dispatchSlashCommand(prompt);
    if (slashResult) {
      setError("Slash commands cannot be queued.");
      return;
    }

    const queued: QueuedPromptSubmission = {
      id: `queued_${Date.now()}`,
      content: prompt.trim() || "Please review the attached file.",
      attachments: promptAttachments,
    };
    setQueuedPromptSubmissions((current) => [...current, queued]);
    setPrompt("");
    setPromptAttachments([]);
    setError(null);
  }

  function formatMcpSummary(servers: McpServerRecord[]): string {
    if (servers.length === 0) {
      return "暂无 MCP 服务器。可以在侧边栏的 **MCP** 页签中添加。";
    }
    const lines = servers.map((s) => {
      const status = s.enabled ? "已启用" : "已停用";
      const transport = s.transport ?? "stdio";
      const detail = s.url ?? s.command ?? "";
      return `- **${s.name}** (${status}) — ${transport}${detail ? `: ${detail}` : ""}`;
    });
    const enabled = servers.filter((s) => s.enabled).length;
    return `**MCP 服务器**（${enabled}/${servers.length} 已启用）\n\n${lines.join("\n")}\n\n_使用 \`/mcp refresh\` 重新发现工具。_`;
  }

  function formatSkillsSummary(skillList: SkillPresetRecord[]): string {
    if (skillList.length === 0) {
      return "暂无技能预设。可以在侧边栏的 **技能** 页签中创建。";
    }
    const lines = skillList.map((s) => {
      const builtin = s.isBuiltin || s.is_builtin ? " [内置]" : "";
      const cat = s.category ? ` (${s.category})` : "";
      return `- **${s.name}**${cat}${builtin} — ${(s.description || "").slice(0, 80)}`;
    });
    return `**技能**（${skillList.length} 个预设）\n\n${lines.join("\n")}`;
  }

  /** Handle a locally-recognized slash command. */
  async function handleSlashCommand(cmd: ReturnType<typeof dispatchSlashCommand>) {
    if (!cmd) return;

    switch (cmd.kind) {
      case "help": {
        const lines = SLASH_COMMANDS.map(
          (c) => `**${c.name}**${c.argsHint ? ` ${c.argsHint}` : ""} - ${c.description}`,
        );
        const helpText = `**可用命令：**\n\n${lines.join("\n")}`;
        addSystemMessage(helpText);
        break;
      }
      case "clear":
        setChatMessages([]);
        addToast("success", "聊天已清空");
        break;
      case "compact": {
        if (!session) {
          addSystemMessage("没有活跃会话，无法压缩上下文。");
          break;
        }
        try {
          const result = await runtimeClient.compactSession({ sessionId: session.id });
          if (result.strategy === "none" || result.tokensBefore === 0) {
            addSystemMessage("会话消息为空，无需压缩。");
          } else {
            const saved = result.tokensBefore - result.tokensAfter;
            addSystemMessage(
              `**上下文压缩完成**\n\n` +
              `- 策略：${result.strategy}\n` +
              `- 压缩前：${result.tokensBefore} tokens\n` +
              `- 压缩后：${result.tokensAfter} tokens\n` +
              `- 节省：${saved > 0 ? saved : 0} tokens` +
              (result.summary ? `\n\n**摘要：**\n${result.summary.slice(0, 500)}` : ""),
            );
          }
        } catch (err: unknown) {
          addSystemMessage(`压缩失败：${err instanceof Error ? err.message : String(err)}`);
        }
        break;
      }
      case "status": {
        const statusLines: string[] = [];
        statusLines.push(`**运行时：** ${hostStatusText}`);
        if (hostStatus?.runtimeRunning) {
          statusLines.push(`**传输：** ${hostStatus.runtimeTransport}`);
        }
        statusLines.push(`**模型：** ${getProviderDisplayLabel(providerSettings)}`);
        statusLines.push(`**会话：** ${session ? session.title : "无"}`);
        statusLines.push(`**消息：** ${chatMessages.length}`);
        if (task) {
          statusLines.push(`**任务：** ${task.id} - ${formatStatusLabel(task.status)}`);
        }
        addSystemMessage(statusLines.join("\n"));
        break;
      }
      case "model":
        if (cmd.args) {
          setProviderSettings((current) => ({ ...current, model: cmd.args }));
          addToast("success", `模型已切换为：${cmd.args}`);
        } else {
          addSystemMessage(
            `**当前模型：** ${getProviderDisplayLabel(providerSettings)}`,
          );
        }
        break;
      case "config": {
        const configLines: string[] = [
          `**模式：** ${formatRuntimeModeLabel(providerSettings.mode)}`,
          `**基础 URL：** ${providerSettings.baseUrl || "默认"}`,
          `**模型：** ${providerSettings.model || "(默认)"}`,
          `**温度：** ${providerSettings.temperature}`,
          `**最大输出令牌：** ${providerSettings.maxTokens}`,
          `**最大上下文：** ${providerSettings.maxContextTokens}`,
          `**超时：** ${providerSettings.timeout}s`,
        ];
        addSystemMessage(configLines.join("\n"));
        break;
      }
      case "mcp": {
        if (cmd.args === "refresh") {
          addSystemMessage("正在刷新 MCP 工具...");
          handleRefreshMcpTools().then(() => {
            addSystemMessage(formatMcpSummary(mcpServers));
          });
        } else {
          addSystemMessage(formatMcpSummary(mcpServers));
        }
        break;
      }
      case "skills": {
        if (cmd.args === "refresh") {
        addSystemMessage("正在刷新技能...");
          refreshSkills();
        } else {
          addSystemMessage(formatSkillsSummary(skills));
        }
        break;
      }
    }
  }

  /** Append a system-level info message (rendered as an assistant bubble). */
  function addSystemMessage(markdown: string) {
    const now = Date.now();
    const systemMessageId = `system_${now}`;
    setChatMessages((current) => [
      ...current,
      {
        id: systemMessageId,
        sessionId: session?.id ?? "",
        taskId: "system",
        role: "assistant" as const,
        content: markdown,
        createdAt: now,
        updatedAt: now,
      },
    ]);
  }

  async function ensureSessionForSend(): Promise<SessionRecord> {
    const nextWorkspace = await ensureWorkspace();
    const result = await runtimeClient.createSession({
      workspaceId: nextWorkspace.id,
      title: sessionTitle.trim() || DEFAULT_SESSION_TITLE,
    });
    setSessions((current) => upsertRecord(current, result.session));
    setSession(result.session);
    setOpenTabs((current) => {
      const resultTabs = openSessionTab(current, result.session);
      setActiveTabId(resultTabs.activeTabId);
      return resultTabs.tabs;
    });
    return result.session;
  }

  async function handleRefreshTask() {
    if (!task) {
      return;
    }

    const taskId = task.id;
    setRefreshBusy(true);
    setError(null);

    try {
      const result = await runtimeClient.getTask(taskId);
      setTask(result.task);
      setActiveTaskForSession(result.task.id);
      setTaskHistory((current) => upsertRecord(current, result.task));
      await loadTraceForTask(taskId);
    } catch (reason) {
      toastError(reason);
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
    setActiveTaskForSession(taskId);
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

  function handleStopPrompt() {
    clearPendingAssistantTokens();
    setChatMessages((current) => stopStreamingMessages(current, session?.id));
    setMessageBusy(false);
    setSessionBusy(false);
    setError(null);
    setTaskControlError(null);
    if (task && isTaskControllable(task.status)) {
      void handleTaskControl("cancel");
    }
  }

  async function handleRefreshTrace() {
    if (!activeTaskId) {
      setTraceEvents([]);
      setCommandLogCacheById({});
      setTraceError(null);
      return;
    }

    await loadTraceForTask(activeTaskId);
  }

  async function handleRefreshCommandJob(commandId: string) {
    setCommandJobBusyId(commandId);
    setError(null);

    try {
      const result = await runtimeClient.commandLogGet({ commandId });
      setCommandLogCacheById((current) => ({
        ...current,
        [result.commandLog.id]: result.commandLog,
      }));
      await loadTraceForTask(result.commandLog.taskId);
    } catch (reason) {
      toastError(reason);
    } finally {
      setCommandJobBusyId((current) => (current === commandId ? null : current));
    }
  }

  async function handleStopCommandJob(commandId: string) {
    setCommandJobBusyId(commandId);
    setError(null);

    try {
      const result = await runtimeClient.commandCancel({ commandId });
      setCommandLogCacheById((current) => ({
        ...current,
        [result.commandLog.id]: result.commandLog,
      }));
      await loadTraceForTask(result.commandLog.taskId);
    } catch (reason) {
      toastError(reason);
    } finally {
      setCommandJobBusyId((current) => (current === commandId ? null : current));
    }
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
      toastError(reason);
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
      addToast("success", decision === "approved" ? "已批准" : "已拒绝");
    } catch (reason) {
      toastError(reason);
    } finally {
      setApprovalBusyId((current) => (current === approvalId ? null : current));
    }
  }

  const activeTab = openTabs.find((tabItem) => tabItem.id === activeTabId) ?? openTabs[0] ?? getInitialTabs()[0];
  const activeSessionRecord = resolveSessionForTab(activeTab, sessions, session);
  const runtimeReady = Boolean(hostStatus && config);
  const localPathActionsAvailable = runtimeClient.canOpenLocalAppPaths();
  const composerVisible = runtimeReady && (activeTab.kind === "new-session" || activeTab.kind === "session");
  const composerCanStop = isTaskControllable(task?.status);
  const composerHasStreamingMessage = visibleChatMessages.some((message) => message.streaming);
  const composerSending = messageBusy || composerCanStop || composerHasStreamingMessage;
  const queuedPromptCount = queuedPromptSubmissions.length;
  const activeSessionWorkspaceRoot = activeTab.kind === "session" ? activeSessionRecord?.workspaceRoot : undefined;
  const activeSessionWorkspaceName =
    activeTab.kind === "session"
      ? activeSessionRecord?.workspaceName ?? workspaceNameFromPath(activeSessionWorkspaceRoot)
      : undefined;
  const workspaceName =
    activeSessionWorkspaceName ?? workspace?.name ?? workspaceNameFromPath(workspacePath) ?? "yuanbao_agent";
  const providerLabel = getProviderDisplayLabel(providerSettings);
  useEffect(() => {
    if (
      loading ||
      !runtimeReady ||
      messageBusy ||
      composerCanStop ||
      composerHasStreamingMessage ||
      queuedPromptSubmissions.length === 0
    ) {
      return;
    }

    const [nextSubmission] = queuedPromptSubmissions;
    setQueuedPromptSubmissions((current) => current.slice(1));
    void sendMessageContent(nextSubmission.content, nextSubmission.attachments, { clearComposer: false, mode: "queued" });
  }, [composerCanStop, composerHasStreamingMessage, loading, messageBusy, queuedPromptSubmissions, runtimeReady]);

  const sessionContextPreview = useMemo(
    () =>
      buildSessionContextPreview({
        events,
        traceEvents,
        workspace,
        activeTaskId,
        activeTask: task,
      }),
    [activeTaskId, events, traceEvents, task, workspace],
  );
  const cwdLabel =
    sessionContextPreview?.workspaceRoot ??
    activeSessionWorkspaceRoot ??
    workspace?.rootPath ??
    workspacePath ??
    DEFAULT_WORKSPACE_PATH;
  const hostStatusText = describeMode(hostStatus);
  const overviewRuntimeStatus = runtimeReady
    ? providerSettings.mode === "mock"
      ? "degraded" as const
      : "ready" as const
    : "offline" as const;
  const runtimeStatusLabel =
    overviewRuntimeStatus === "ready"
      ? "运行时就绪"
      : overviewRuntimeStatus === "degraded"
        ? "运行时预览"
        : "运行时离线";
  const enabledMcpServers = mcpServers.filter((server) => server.enabled).length;
  const mcpStatusLabel = `${enabledMcpServers}/${mcpServers.length || 0} MCP`;
  const pendingApprovalCount = approvalCards.filter((approval) => approval.status === "pending").length;
  const approvalStatusLabel = `${pendingApprovalCount} 个审批`;
  const contextStats = sessionContextPreview?.budgetStats;
  const contextStatusLabel = contextStats?.maxContextTokens
    ? `${formatCompactCount(contextStats.estimatedTokens ?? contextStats.estimatedInputTokens)}/${formatCompactCount(contextStats.maxContextTokens)} 上下文`
    : `${formatCompactCount(contextStats?.estimatedTokens ?? contextStats?.estimatedInputTokens)} 上下文`;
  const runtimeUnavailableReason =
    !loading && !runtimeReady
      ? error ?? "运行时握手未完成。前端无法独立执行任务。"
      : null;
  const settingsProviders = useMemo<SettingsProvider[] | undefined>(() => {
    if (!config) {
      return undefined;
    }

    const providerConfig = normalizeProviderConfig(config.provider);
    return (providerConfig.profiles ?? []).map((profile) => {
      const models = [profile.model, profile.fallbackModel].filter(
        (value): value is string => Boolean(value),
      );

      return {
        id: profile.id,
        name: profile.name,
        endpoint: profile.baseUrl ?? "未配置接口地址",
        apiFormat: profile.apiFormat as SettingsProvider["apiFormat"],
        note:
          profile.mode === "mock"
            ? "本地预览；不会调用远程模型"
            : profile.apiKeyEnvVarName
              ? `环境变量：${profile.apiKeyEnvVarName}`
              : "需要 API 密钥",
        models: models.length ? models : undefined,
        modelMapping: {
          main: profile.model ?? "",
          haiku: profile.defaultModel ?? profile.model ?? "",
          sonnet: profile.model ?? profile.defaultModel ?? "",
          opus: profile.fallbackModel ?? "",
        },
        apiKeyMasked: profile.apiKey ? "已输入密钥" : profile.apiKeyEnvVarName,
        lastTest: buildSettingsProviderLastTest(
          profile,
          providerTestResult,
          providerConfig.activeProfileId,
        ),
        status:
          profile.id === providerConfig.activeProfileId
            ? "active"
            : profile.lastStatus ?? "configured",
      };
    });
  }, [config, providerTestResult]);
  const settingsAgentBehavior = useMemo(
    () => buildSettingsAgentBehaviorConfig(config),
    [config],
  );
  const sessionTaskCount = useMemo(() => {
    if (!activeSessionRecord) {
      return undefined;
    }

    return taskHistory.filter((item) => item.sessionId === activeSessionRecord.id).length;
  }, [activeSessionRecord, taskHistory]);
  const scheduledTasks = useMemo<ScheduledTask[]>(
    () => scheduledRecords.map(scheduledRecordToWorkspaceTask),
    [scheduledRecords],
  );
  const settingsSkills = useMemo<SettingsSkillConfig[]>(
    () => skills.map(normalizeSkillForSettings),
    [skills],
  );
  const scheduledLogsByTaskId = useMemo<Record<string, ExecutionLog[]>>(() => {
    return Object.fromEntries(
      scheduledRecords.map((record) => [
        record.id,
        scheduledLogs.filter((log) => log.taskId === record.id).map(scheduledRunToExecutionLog),
      ]),
    );
  }, [scheduledLogs, scheduledRecords]);
  const sessionApprovals = useMemo(
    () =>
      approvalCards.map((approval) => ({
        id: approval.approvalId,
        title: approval.patchSummary ?? approval.command,
        kind: approval.kind,
        status: approval.status,
        summary: approval.requestSummary,
        requestedAt: approval.requestedAt,
        risk: riskToLevel(approval.risk),
        parametersPreview: approval.requestSummary,
        fullInput: approval.requestJson,
        command: approval.command,
        cwd: approval.cwd,
      })),
    [approvalCards],
  );
  const sessionPatches = useMemo(
    () =>
      patchCards.map((patch) => ({
        id: patch.patchId,
        summary: patch.summary,
        status: patch.status,
        filesChanged: patch.filesChanged,
        additions: countAddedLines(patch.diffText ?? ""),
        deletions: countDeletedLines(patch.diffText ?? ""),
        updatedAt: patch.updatedAt,
        files: parsePatchFiles(patch.diffText),
        diff: patch.diffText,
      })),
    [patchCards],
  );
  const sessionTraceItems = useMemo(
    () =>
      [...traceEvents]
        .sort((left, right) => right.sequence - left.sequence)
        .map((trace) => ({
          id: trace.id,
          type: trace.type,
          source: trace.source,
          time: trace.createdAt,
          title: trace.type,
          summary: summarizeValue(trace.payload, trace.type, 120),
          detail: summarizeValue(trace.payload, trace.type, 800),
          status: readEventText(trace.payload, "status"),
          durationMs: readEventNumber(trace.payload, "durationMs"),
          tokenCount: readEventNumber(trace.payload, "tokenCount"),
          stdout: readEventText(trace.payload, "stdout"),
          stderr: readEventText(trace.payload, "stderr"),
          visibility: trace.visibility,
          taskId: trace.taskId,
          agentType: (trace.payload as Record<string, unknown> | null)?.agentType as string | undefined,
        })),
    [traceEvents],
  );
  const sessionToolCalls = useMemo(
    () =>
      toolTimelineItems
        .filter((toolCall) => !activeTaskId || toolCall.taskId === activeTaskId)
        .map((toolCall) => ({
          id: toolCall.id,
          toolName: toolCall.toolName,
          status: toolCall.status,
          time: toolCall.finishedAt ?? toolCall.updatedAt ?? toolCall.startedAt,
          resultSummary: toolCall.errorSummary ?? toolCall.resultSummary,
          durationMs: toolCall.durationMs,
          argsPreview: toolCall.argsSummary,
          input: toolCall.argsSummary,
          output: toolCall.resultSummary,
          rawInput: toolCall.argsRaw,
          rawOutput: toolCall.resultRaw,
          stderr: toolCall.errorSummary,
        })),
    [activeTaskId, toolTimelineItems],
  );
  const sessionCollaboration = useMemo(
    () => buildSessionCollaboration(events, traceEvents),
    [events, traceEvents],
  );
  const composerRuntimeChildTasks: ComposerRuntimeChildTask[] = useMemo(
    () =>
      (sessionCollaboration.childTasks ?? []).map((childTask) => ({
        id: childTask.id,
        title: childTask.title,
        status: childTask.status,
        workerName: childTask.workerName,
        summary: childTask.summary,
        updatedAt: childTask.updatedAt,
      })),
    [sessionCollaboration.childTasks],
  );
  const sessionBackgroundJobs = useMemo(
    () => {
      const eventJobs = buildSessionBackgroundJobs(events, traceEvents);
      const commandLogs = Object.values(commandLogCacheById).filter(
        (log) => !activeTaskId || log.taskId === activeTaskId,
      );
      return mergeSessionBackgroundJobs(eventJobs, commandLogs);
    },
    [activeTaskId, commandLogCacheById, events, traceEvents],
  );



  const workspaceContent = (() => {
    if (!runtimeReady && !loading) {
      return <RuntimeUnavailableWorkspace errorMessage={runtimeUnavailableReason ?? "Runtime unavailable."} />;
    }

    if (activeTab.kind === "overview") {
      return (
        <WorkbenchOverviewPage
          workspace={workspace}
          workspacePath={workspacePath}
          providerLabel={providerLabel}
          runtimeStatus={overviewRuntimeStatus}
          sessions={sessions}
          tasks={taskHistory}
          scheduledTasks={scheduledRecords}
          mcpServers={mcpServers}
          skills={skills}
          onOpenNewSession={() => handleOpenSystemTab("new-session")}
          onOpenSession={handleOpenSessionTab}
          onOpenScheduled={() => handleOpenSystemTab("scheduled")}
          onOpenMcp={() => handleOpenSystemTab("mcp")}
          onOpenSettings={() => handleOpenSystemTab("settings")}
        />
      );
    }

    if (activeTab.kind === "new-session") {
      return (
        <NewSessionWorkspace
          workspacePath={workspacePath}
          hostStatusText={hostStatusText}
          sessionTitle={sessionTitle}
          modelLabel={providerLabel}
          modelOptions={(settingsProviders ?? []).map((provider) => ({
            id: provider.id,
            label: provider.models?.[0] ?? provider.name,
            subtitle: provider.models?.[0] && provider.name !== provider.models[0] ? provider.name : undefined,
          }))}
          selectedModelId={activeProviderProfileId}
          workspaceBusy={workspaceBusy}
          sessionBusy={sessionBusy}
          onSelectModel={selectProviderProfile}
          onSessionTitleChange={setSessionTitle}
          onWorkspacePathChange={setWorkspacePath}
          onOpenWorkspace={handleOpenWorkspace}
          onCreateSession={handleCreateSession}
        />
      );
    }

    if (activeTab.kind === "session") {
      return (
        <SessionWorkspace
          session={activeSessionRecord}
          activeTask={
            task
              ? {
                  id: task.id,
                  status: task.status,
                  goal: task.goal,
                  createdAt: task.createdAt,
                  updatedAt: task.updatedAt,
                  acceptanceCriteria: task.acceptanceCriteria,
                  outOfScope: task.outOfScope,
                  currentStep: task.currentStep,
                  changedFiles: task.changedFiles,
                  commands: task.commands,
                  verification: task.verification,
                  summary: task.summary,
                  resultSummary: task.resultSummary,
                  planSteps: task.plan?.map((step) => ({
                    id: step.id,
                    title: step.title,
                    status: step.status,
                    detail: step.detail,
                  })),
                }
              : null
          }
          messages={visibleChatMessages}
          messagesLoading={sessionBusy}
          taskCount={sessionTaskCount}
          collaboration={sessionCollaboration}
          backgroundJobs={sessionBackgroundJobs}
          approvals={sessionApprovals}
          patches={sessionPatches}
          traces={sessionTraceItems}
          toolCalls={sessionToolCalls}
          contextPreview={sessionContextPreview}
          composerContext={{
            cwd: cwdLabel,
            repo: workspaceName,
            model: providerLabel,
            permissionMode: approvalModeToSettingsMode(config?.policy.approvalMode),
          }}
          onApprove={(approvalId) => handleApprovalSubmit(approvalId, "approved")}
          onApproveForSession={(approvalId) => handleApprovalSubmit(approvalId, "approved")}
          onReject={(approvalId) => handleApprovalSubmit(approvalId, "rejected")}
          onLoadPatch={handleLoadPatchDiff}
          onCopyPatchPath={(_patchId, path) => {
            void handleCopyRuntimeText("补丁路径", path);
          }}
          onCopyRuntimeText={handleCopyRuntimeText}
          onRefreshCommandJob={handleRefreshCommandJob}
          onStopCommandJob={handleStopCommandJob}
          onRefreshTask={handleRefreshTask}
          onStopTask={() => handleTaskControl("cancel")}
          onRefreshTrace={handleRefreshTrace}
          taskBusyAction={refreshBusy ? "refresh" : taskControlBusyAction === "cancel" ? "stop" : null}
          busyId={approvalBusyId ?? patchBusyId ?? commandJobBusyId ?? (traceBusy ? "trace" : null)}
        />
      );
    }

    if (activeTab.kind === "scheduled") {
      return (
        <ScheduledWorkspace
          tasks={scheduledTasks}
          logsByTaskId={scheduledLogsByTaskId}
          selectedTaskId={selectedScheduledTaskId ?? undefined}
          onSelectTask={handleSelectScheduledTask}
          onCreateTask={handleCreateScheduledTask}
          onRunTask={handleRunScheduledTask}
          onToggleTask={handleToggleScheduledTask}
          busyTaskId={scheduledBusyTaskId}
          createBusy={scheduledCreateBusy}
          workspacePath={cwdLabel}
        />
      );
    }

    if (activeTab.kind === "mcp") {
      return (
        <McpWorkspace
          servers={mcpServers}
          loading={mcpLoading}
          busyServerId={mcpBusyServerId}
          lastRefresh={mcpLastRefresh}
          errorMessage={mcpError}
          onRefreshServers={refreshMcpServers}
          onCreateServer={handleCreateMcpServer}
          onImportServers={handleImportMcpServers}
          onUpdateServer={handleUpdateMcpServer}
          onToggleServer={handleToggleMcpServer}
          onRefreshTools={handleRefreshMcpTools}
          onDeleteServer={handleDeleteMcpServer}
          onDismissError={() => setMcpError(null)}
        />
      );
    }

    if (activeTab.kind === "skills") {
      return (
        <SkillsWorkspace
          skills={settingsSkills}
          mcpServers={mcpServers}
          mcpToolCount={mcpLastRefresh?.tools.length ?? 0}
          providerLabel={providerLabel}
          busySkillId={skillBusyId}
          onRefreshSkills={refreshSkills}
          onOpenMcp={() => handleOpenSystemTab("mcp")}
          onOpenSettings={() => handleOpenSystemTab("settings")}
          onCreateSkill={handleCreateSkill}
          onUpdateSkill={handleUpdateSkill}
          onDeleteSkill={handleDeleteSkill}
          onImportSkills={handleImportSkills}
          onOpenSkillsFolder={localPathActionsAvailable ? () => void handleOpenAppPath("skills") : undefined}
        />
      );
    }

    if (activeTab.kind === "appearance") {
      return (
        <AppearanceWorkspace
          value={generalSettings}
          workspaceName={workspaceName}
          providerLabel={providerLabel}
          onChange={handleGeneralSettingsChange}
          onOpenSettings={() => handleOpenSystemTab("settings")}
        />
      );
    }

    if (activeTab.kind === "playground") {
      return (
        <ComponentPlaygroundWorkspace
          onOpenAppearance={() => handleOpenSystemTab("appearance")}
          onOpenSkills={() => handleOpenSystemTab("skills")}
        />
      );
    }

    return (
      <SettingsWorkspace
        providers={settingsProviders}
        activeProviderId={config?.provider.activeProfileId}
        onSelectProvider={selectProviderProfile}
        onAddProvider={handleAddProviderFromSettings}
        onEditProvider={handleEditProviderFromSettings}
        onTestProvider={handleTestSelectedProvider}
        onTestProviderConfig={handleTestProviderConfigFromSettings}
        onSaveProvider={handleSaveProviderConfig}
        providerBusy={providerConfigBusy}
        providerTestBusy={providerTestBusy}
        providerFeedback={providerFeedback}
        permissionMode={approvalModeToSettingsMode(config?.policy.approvalMode)}
        onPermissionModeChange={handlePermissionModeChange}
        agentBehavior={settingsAgentBehavior}
        onAgentBehaviorChange={handleAgentBehaviorChange}
        general={generalSettings}
        onGeneralChange={handleGeneralSettingsChange}
        im={imSettings}
        onIMChange={setIMSettings}
        skills={settingsSkills}
        onRefreshSkills={refreshSkills}
        onOpenSkillsFolder={localPathActionsAvailable ? () => void handleOpenAppPath("skills") : undefined}
        computerUse={computerUseSettings}
        onComputerUseChange={setComputerUseSettings}
        onRecheckComputerUse={handleRecheckComputerUse}
        workspaceFocus={workspace?.focus}
        workspaceFocusBusy={workspaceFocusBusy}
        onSaveWorkspaceFocus={workspace ? handleSaveWorkspaceFocus : undefined}
        workspaceMemorySummary={workspace?.summary}
        workspaceMemoryBusy={workspaceMemoryBusy}
        onClearWorkspaceMemory={workspace ? handleClearWorkspaceMemory : undefined}
        about={{
          version: "0.1.0",
          runtime: hostStatus?.runtimeTransport ?? "mock-browser",
          dataPath: workspacePath,
          build: hostStatus?.runtimeRunning ? "runtime running" : "runtime idle",
        }}
        onOpenLogs={localPathActionsAvailable ? () => void handleOpenAppPath("logs") : undefined}
        onOpenDataDirectory={localPathActionsAvailable ? () => void handleOpenAppPath("data") : undefined}
      />
    );
  })();

  return (
    <AppShell
      tabs={openTabs}
      activeTabId={activeTabId}
      sessions={sessions}
      activeSessionId={getSidebarActiveSessionId(activeTab)}
      workspaceName={workspaceName}
      composerVisible={composerVisible}
      promptValue={prompt}
      onPromptChange={setPrompt}
      onOpenSystemTab={handleOpenSystemTab}
      onOpenSessionTab={handleOpenSessionTab}
      onActivateTab={handleActivateTab}
      onCloseTab={handleCloseTab}
      onCloseOtherTabs={handleCloseOtherTabs}
      onRenameSession={handleRenameSession}
      onDeleteSession={handleDeleteSession}
      onSubmitPrompt={handleSendMessage}
      onQueuePrompt={handleQueuePrompt}
      onStopPrompt={handleStopPrompt}
      disabled={loading || !runtimeReady}
      sending={composerSending}
      submitting={messageBusy}
      queuedPromptCount={queuedPromptCount}
      runtimeChildTasks={composerRuntimeChildTasks}
      attachments={promptAttachments}
      onAttachmentsChange={setPromptAttachments}
      onAttachmentError={toastError}
      modelOptions={(settingsProviders ?? []).map((provider) => ({
        id: provider.id,
        label: provider.models?.[0] ?? provider.name,
        subtitle: provider.models?.[0] && provider.name !== provider.models[0] ? provider.name : undefined,
      }))}
      selectedModelId={activeProviderProfileId}
      onSelectModel={selectProviderProfile}
      loading={loading}
      providerLabel={providerLabel}
      cwdLabel={cwdLabel}
      runtimeLabel={runtimeStatusLabel}
      mcpLabel={mcpStatusLabel}
      approvalLabel={approvalStatusLabel}
      contextLabel={contextStatusLabel}
      theme={generalSettings.theme}
      density={generalSettings.density}
      radius={generalSettings.radius}
      motion={generalSettings.motion}
      accentColor={generalSettings.accentColor}
      transparency={generalSettings.transparency}
      fontScale={generalSettings.fontScale}
    >
      {error ? (
        <div className="error-banner compact" role="alert">
          <span>{error}</span>
          <button type="button" className="error-banner-dismiss" aria-label="关闭错误" onClick={() => setError(null)}>×</button>
        </div>
      ) : null}
      {workspaceContent}
      <ToastContainer toasts={toasts} onDismiss={dismissToast} />
    </AppShell>
  );
}
