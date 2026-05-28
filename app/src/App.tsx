import { useCallback, useEffect, useRef, useState } from "react";
import type {
  AgentProfileCreateParams,
  AgentProfilePreviewToolsParams,
  AgentProfileRecord,
  AgentProfileUpdateParams,
  AgentProfileValidateParams,
  AgentEventEnvelope,
  AssistantTokenPayload,
  HookCreateParams,
  HookUpdateParams,
  McpServerRecord,
  RuntimeHookExecutionRecord,
  RuntimeHookRecord,
  SessionRecord,
  SkillPresetRecord,
  TaskRecord,
  TraceEventRecord,
  WorktreeDiffResult,
  WorktreeStatusResult,
} from "@shared";
import { RuntimeClient, type HostStatus, type RuntimeConfig } from "./lib/runtimeClient";
import {
  getVisibleChatMessages,
  isOperationalAssistantDelta,
  replaceSessionMessages,
  type ChatMessageView,
} from "./state/chatMessages";
import { DEFAULT_PROMPT, DEFAULT_WORKSPACE_PATH } from "./state/constants";
import { AppShell } from "./ui/workbench/AppShell";
import { getSidebarActiveSessionId, resolveSessionForTab } from "./ui/workbench/sessionRouting";
import { getInitialTabs } from "./ui/workbench/tabModel";
import type { WorkbenchTab } from "./ui/workbench/types";
import { ToastContainer, createToast, type ToastEntry } from "./ui/workbench/Toast";
import {
  TRACE_AUTO_REFRESH_STATUSES,
  normalizeRuntimeConfig,
  buildProviderSettingsForm,
  buildCommandPolicyForm,
  serializePatternList,
  type ProviderSettingsForm,
  isTaskControllable,
} from "./state/providerConfig";
import type { QueuedPromptSubmission } from "./state/eventRecordViews";
import { buildSettingsGeneralConfig } from "./state/providerPayloadParsing";
import { sortByUpdatedAtDesc } from "./state/eventRecordViews";
import {
  appendAssistantToken,
} from "./state/chatTokenHelpers";
import { WorkspaceRouter } from "./ui/workbench/workspaces/WorkspaceRouter";
import { approvalModeToSettingsMode } from "./state/providerPayloadParsing";
import type { SessionWorkspaceWorktreeStatus } from "./ui/workbench/workspaces/session/SessionWorkspace";

// Hooks
import { useProviderConfig } from "./hooks/useProviderConfig";
import { useMcpServers } from "./hooks/useMcpServers";
import { useSkills } from "./hooks/useSkills";
import { useScheduledTasks } from "./hooks/useScheduledTasks";
import { useSettings } from "./hooks/useSettings";
import { useWorkspaceSessions } from "./hooks/useWorkspaceSessions";
import { useTaskTrace } from "./hooks/useTaskTrace";
import { useMessageActions } from "./hooks/useMessageActions";
import { useTabActions } from "./hooks/useTabActions";
import { useEventSubscription } from "./hooks/useEventSubscription";
import { useDerivedViews } from "./hooks/useDerivedViews";

const runtimeClient = new RuntimeClient();

function resolveActiveTabKind(tab: unknown): string | undefined {
  const candidate = tab as { id?: unknown; kind?: unknown };
  if (typeof candidate.kind === "string" && candidate.kind.length > 0) {
    return candidate.kind;
  }
  if (typeof candidate.id !== "string") {
    return undefined;
  }
  if (candidate.id.startsWith("session:")) {
    return "session";
  }
  if (candidate.id.startsWith("system:")) {
    return candidate.id.slice("system:".length);
  }
  return undefined;
}

export function App() {
  const [hostStatus, setHostStatus] = useState<HostStatus | null>(null);
  const [config, setConfig] = useState<RuntimeConfig | null>(null);
  const [taskHistory, setTaskHistory] = useState<Array<TaskRecord>>([]);
  const [task, setTask] = useState<TaskRecord | null>(null);
  const [activeTaskId, setActiveTaskId] = useState<string | null>(null);
  const [prompt, setPrompt] = useState(DEFAULT_PROMPT);
  const [promptAttachments, setPromptAttachments] = useState<string[]>([]);
  const [queuedPromptSubmissions, setQueuedPromptSubmissions] = useState<QueuedPromptSubmission[]>([]);
  const [worktreeStatus, setWorktreeStatus] = useState<SessionWorkspaceWorktreeStatus | null>(null);
  const [worktreeDiff, setWorktreeDiff] = useState<WorktreeDiffResult["diff"] | null>(null);
  const [worktreeBusyAction, setWorktreeBusyAction] = useState<"status" | "diff" | "requestMergeApproval" | "merge" | "cleanup" | null>(null);
  const [worktreeError, setWorktreeError] = useState<string | null>(null);
  const [agentProfiles, setAgentProfiles] = useState<AgentProfileRecord[]>([]);
  const [agentProfileBusyId, setAgentProfileBusyId] = useState<string | null>(null);
  const [agentProfileFeedback, setAgentProfileFeedback] = useState<{ tone: "success" | "danger" | "info"; message: string } | null>(null);
  const [runtimeHooks, setRuntimeHooks] = useState<RuntimeHookRecord[]>([]);
  const [hookExecutions, setHookExecutions] = useState<RuntimeHookExecutionRecord[]>([]);
  const [hookBusyId, setHookBusyId] = useState<string | null>(null);
  const [hookFeedback, setHookFeedback] = useState<{ tone: "success" | "danger" | "info"; message: string } | null>(null);
  const [chatMessages, setChatMessages] = useState<ChatMessageView[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [toasts, setToasts] = useState<ToastEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [messageBusy, setMessageBusy] = useState(false);
  const [openTabs, setOpenTabs] = useState<WorkbenchTab[]>(() => getInitialTabs());
  const [activeTabId, setActiveTabId] = useState<WorkbenchTab["id"]>("system:new-session");

  // ── Toast / error helpers ──────────────────────────────────────────
  function addToast(kind: ToastEntry["kind"], message: string) {
    setToasts((current) => [...current.slice(-4), createToast(kind, message)]);
  }
  function getErrorMessage(reason: unknown): string {
    return reason instanceof Error ? reason.message : String(reason);
  }
  function toastError(reason: unknown) {
    addToast("error", getErrorMessage(reason));
  }

  function normalizeWorktreeStatus(value: WorktreeStatusResult["gitStatus"] | null | undefined): SessionWorkspaceWorktreeStatus | null {
    if (!value) return null;
    if ("dirtyFiles" in value || "error" in value) {
      return value as SessionWorkspaceWorktreeStatus;
    }
    const changes = Array.isArray((value as any).changes) ? (value as any).changes : [];
    return {
      dirtyFiles: changes.length,
      files: changes
        .map((change: any) => [change.status, change.path].filter(Boolean).join(" ").trim())
        .filter(Boolean),
    };
  }

  async function runWorktreeAction<T>(
    action: "status" | "diff" | "requestMergeApproval" | "merge" | "cleanup",
    operation: () => Promise<T>,
  ): Promise<T | null> {
    setWorktreeBusyAction(action);
    setWorktreeError(null);
    try {
      return await operation();
    } catch (reason) {
      const message = getErrorMessage(reason);
      setWorktreeError(message);
      addToast("error", message);
      return null;
    } finally {
      setWorktreeBusyAction(null);
    }
  }

  async function handleRefreshWorktree(worktreeId: string) {
    const result = await runWorktreeAction("status", () => runtimeClient.worktreeStatus({ worktreeId }));
    if (result) setWorktreeStatus(normalizeWorktreeStatus(result.gitStatus));
  }

  async function handleLoadWorktreeDiff(worktreeId: string, full = false) {
    const result = await runWorktreeAction("diff", () => runtimeClient.worktreeDiff({ worktreeId, full }));
    if (result) setWorktreeDiff(result.diff ?? null);
  }

  async function handleMergeWorktree(worktreeId: string) {
    const diffError = worktreeDiff && "error" in worktreeDiff ? worktreeDiff.error : undefined;
    if (!worktreeDiff || diffError) {
      setWorktreeError("Review the worktree diff before merging.");
      addToast("info", "Review the worktree diff before merging.");
      return;
    }
    if ((worktreeStatus?.dirtyFiles ?? 0) > 0) {
      setWorktreeError("Worktree merge approval is blocked while there are uncommitted changes.");
      addToast("info", "Clean or commit the worktree changes before requesting merge approval.");
      return;
    }
    const result = await runWorktreeAction("requestMergeApproval", () => runtimeClient.worktreeRequestMergeApproval({
      worktreeId,
      verificationCommands: config?.worktree?.mergeVerificationCommands,
      verificationTimeoutMs: config?.worktree?.mergeVerificationTimeoutMs,
    }));
    if (result?.approval) {
      setWorktreeStatus(normalizeWorktreeStatus(result.gitStatus));
      setWorktreeDiff(result.diff ?? worktreeDiff);
      const passedVerification = result.verification?.length ? ` after ${result.verification.length} verification check${result.verification.length === 1 ? "" : "s"}` : "";
      addToast("info", `Worktree merge approval requested${passedVerification}.`);
      await handleRefreshTask();
    }
  }

  async function handleCleanupWorktree(worktreeId: string, force = false) {
    const result = await runWorktreeAction("cleanup", () => runtimeClient.worktreeCleanup({ worktreeId, force }));
    if (result?.cleaned) {
      addToast("success", "Worktree cleaned.");
      await handleRefreshTask();
    }
  }

  async function refreshAgentProfiles() {
    setAgentProfileBusyId("refresh");
    setAgentProfileFeedback(null);
    try {
      const result = await runtimeClient.listAgentProfiles();
      setAgentProfiles(result.agents);
    } catch (reason) {
      const message = getErrorMessage(reason);
      setAgentProfileFeedback({ tone: "danger", message });
      addToast("error", message);
    } finally {
      setAgentProfileBusyId(null);
    }
  }

  async function handleAddAgentProfile(payload: AgentProfileCreateParams) {
    setAgentProfileBusyId("create");
    setAgentProfileFeedback(null);
    try {
      const result = await runtimeClient.createAgentProfile(payload);
      setAgentProfiles((current) => [result.agent, ...current.filter((item) => item.id !== result.agent.id)]);
      setAgentProfileFeedback({ tone: "success", message: `Agent profile "${result.agent.name}" created.` });
      addToast("success", "Agent profile created.");
    } catch (reason) {
      const message = getErrorMessage(reason);
      setAgentProfileFeedback({ tone: "danger", message });
      addToast("error", message);
      throw reason;
    } finally {
      setAgentProfileBusyId(null);
    }
  }

  async function handleUpdateAgentProfile(agentId: string, payload: Partial<AgentProfileCreateParams>) {
    setAgentProfileBusyId(agentId);
    setAgentProfileFeedback(null);
    try {
      const updatePayload: AgentProfileUpdateParams = { agentId, ...payload };
      const result = await runtimeClient.updateAgentProfile(updatePayload);
      setAgentProfiles((current) => current.map((item) => (item.id === agentId ? result.agent : item)));
      setAgentProfileFeedback({ tone: "success", message: `Agent profile "${result.agent.name}" saved.` });
      addToast("success", "Agent profile saved.");
    } catch (reason) {
      const message = getErrorMessage(reason);
      setAgentProfileFeedback({ tone: "danger", message });
      addToast("error", message);
      throw reason;
    } finally {
      setAgentProfileBusyId(null);
    }
  }

  async function handleDeleteAgentProfile(agentId: string) {
    setAgentProfileBusyId(agentId);
    setAgentProfileFeedback(null);
    try {
      await runtimeClient.deleteAgentProfile({ agentId });
      setAgentProfiles((current) => current.filter((item) => item.id !== agentId));
      setAgentProfileFeedback({ tone: "success", message: "Agent profile deleted." });
      addToast("success", "Agent profile deleted.");
    } catch (reason) {
      const message = getErrorMessage(reason);
      setAgentProfileFeedback({ tone: "danger", message });
      addToast("error", message);
      throw reason;
    } finally {
      setAgentProfileBusyId(null);
    }
  }

  async function handleValidateAgentProfile(payload: AgentProfileValidateParams) {
    return runtimeClient.validateAgentProfile(payload);
  }

  async function handlePreviewAgentProfileTools(payload: AgentProfilePreviewToolsParams) {
    return runtimeClient.previewAgentProfileTools(payload);
  }

  async function refreshRuntimeHooks(workspaceIdOverride?: string) {
    const targetWorkspaceId = workspaceIdOverride ?? workspace?.id;
    if (!targetWorkspaceId) {
      setRuntimeHooks([]);
      setHookExecutions([]);
      setHookFeedback({ tone: "info", message: "Open a workspace before managing runtime hooks." });
      return;
    }
    setHookBusyId("refresh");
    setHookFeedback(null);
    try {
      const result = await runtimeClient.listHooks({ workspaceId: targetWorkspaceId });
      setRuntimeHooks(result.hooks);
      const selectedHookId = result.hooks[0]?.id;
      if (selectedHookId) {
        const executions = await runtimeClient.listHookExecutions({ hookId: selectedHookId, limit: 50 });
        setHookExecutions(executions.hookExecutions);
      } else {
        setHookExecutions([]);
      }
    } catch (reason) {
      const message = getErrorMessage(reason);
      setHookFeedback({ tone: "danger", message });
      addToast("error", message);
    } finally {
      setHookBusyId(null);
    }
  }

  async function refreshHookExecutions(hookId?: string) {
    const targetHookId = hookId ?? runtimeHooks[0]?.id;
    if (!targetHookId) {
      setHookExecutions([]);
      return;
    }
    setHookBusyId("executions");
    setHookFeedback(null);
    try {
      const result = await runtimeClient.listHookExecutions({ hookId: targetHookId, limit: 50 });
      setHookExecutions((current) => [
        ...current.filter((item) => item.hookId !== targetHookId),
        ...result.hookExecutions,
      ]);
    } catch (reason) {
      const message = getErrorMessage(reason);
      setHookFeedback({ tone: "danger", message });
      addToast("error", message);
    } finally {
      setHookBusyId(null);
    }
  }

  async function handleAddHook(payload: HookCreateParams) {
    setHookBusyId("create");
    setHookFeedback(null);
    try {
      const result = await runtimeClient.createHook(payload);
      setRuntimeHooks((current) => [result.hook, ...current.filter((item) => item.id !== result.hook.id)]);
      setHookFeedback({ tone: "success", message: `Hook "${result.hook.name}" created.` });
      addToast("success", "Runtime hook created.");
    } catch (reason) {
      const message = getErrorMessage(reason);
      setHookFeedback({ tone: "danger", message });
      addToast("error", message);
      throw reason;
    } finally {
      setHookBusyId(null);
    }
  }

  async function handleUpdateHook(hookId: string, payload: Partial<HookCreateParams>) {
    setHookBusyId(hookId);
    setHookFeedback(null);
    try {
      const updatePayload: HookUpdateParams = { hookId, ...payload };
      const result = await runtimeClient.updateHook(updatePayload);
      setRuntimeHooks((current) => current.map((item) => (item.id === hookId ? result.hook : item)));
      setHookFeedback({ tone: "success", message: `Hook "${result.hook.name}" saved.` });
      addToast("success", "Runtime hook saved.");
    } catch (reason) {
      const message = getErrorMessage(reason);
      setHookFeedback({ tone: "danger", message });
      addToast("error", message);
      throw reason;
    } finally {
      setHookBusyId(null);
    }
  }

  async function handleDeleteHook(hookId: string) {
    setHookBusyId(hookId);
    setHookFeedback(null);
    try {
      await runtimeClient.deleteHook({ hookId });
      setRuntimeHooks((current) => current.filter((item) => item.id !== hookId));
      setHookExecutions((current) => current.filter((item) => item.hookId !== hookId));
      setHookFeedback({ tone: "success", message: "Runtime hook deleted." });
      addToast("success", "Runtime hook deleted.");
    } catch (reason) {
      const message = getErrorMessage(reason);
      setHookFeedback({ tone: "danger", message });
      addToast("error", message);
      throw reason;
    } finally {
      setHookBusyId(null);
    }
  }

  // ── Streaming refs ─────────────────────────────────────────────────
  const pendingAssistantTokenEventsRef = useRef<AgentEventEnvelope[]>([]);
  const assistantTokenFlushTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const messageLoadRequestRef = useRef(0);
  const childTaskIdsRef = useRef<Set<string>>(new Set());

  function clearPendingAssistantTokens() {
    pendingAssistantTokenEventsRef.current = [];
    if (assistantTokenFlushTimerRef.current !== null) {
      clearTimeout(assistantTokenFlushTimerRef.current);
      assistantTokenFlushTimerRef.current = null;
    }
  }

  function flushPendingAssistantTokens() {
    const pendingEvents = pendingAssistantTokenEventsRef.current;
    if (!pendingEvents.length) return;
    pendingAssistantTokenEventsRef.current = [];
    if (assistantTokenFlushTimerRef.current !== null) {
      clearTimeout(assistantTokenFlushTimerRef.current);
      assistantTokenFlushTimerRef.current = null;
    }
    setChatMessages((current) =>
      pendingEvents.reduce((nextMessages, event) => appendAssistantToken(nextMessages, event), current),
    );
  }

  async function loadSessionMessages(sessionId: string | null | undefined) {
    if (!sessionId) return;
    const requestId = messageLoadRequestRef.current + 1;
    messageLoadRequestRef.current = requestId;
    try {
      const result = await runtimeClient.listMessages({ sessionId, limit: 500 });
      if (messageLoadRequestRef.current !== requestId) return;
      setChatMessages((current) => replaceSessionMessages(current, sessionId, result.messages));
    } catch (reason) {
      if (messageLoadRequestRef.current === requestId) toastError(reason);
    }
  }

  // ── Extracted custom hooks ──────────────────────────────────────────
  const providerHook = useProviderConfig({ addToast, toastError, setError, config, setConfig });
  const settingsHook = useSettings({ addToast, toastError, setError, config, setConfig });
  const scheduledHook = useScheduledTasks({ addToast, toastError, setError });
  const skillsHook = useSkills({ addToast, toastError, setError });
  const mcpHook = useMcpServers({ addToast, toastError, setError });

  // ── Core hooks: task/trace first, then workspace/sessions ───────────
  const setActiveTaskForSessionRef = useRef<(taskId: string | null, sessionId?: string | null) => void>(() => {});
  const taskTraceHook = useTaskTrace({
    addToast, toastError, setError,
    task, setTask, taskHistory, setTaskHistory,
    setActiveTaskForSession: (taskId, sessionId) => setActiveTaskForSessionRef.current(taskId, sessionId),
    activeTaskId, setActiveTaskId,
  });
  const {
    events, setEvents,
    traceEvents, setTraceEvents,
    commandLogCacheById, setCommandLogCacheById,
    patchCacheById, setPatchCacheById,
    traceBusy, setTraceBusy,
    traceError, setTraceError,
    approvalBusyId, setApprovalBusyId,
    patchBusyId, setPatchBusyId,
    commandJobBusyId, setCommandJobBusyId,
    taskControlBusyAction, setTaskControlBusyAction,
    taskControlError, setTaskControlError,
    refreshBusy, setRefreshBusy,
    loadTraceForTask,
    handleRefreshTask,
    handleTaskControl,
    handleRefreshTrace,
    handleRefreshCommandJob,
    handleStopCommandJob,
    handleLoadPatchDiff,
    handleApprovalSubmit,
  } = taskTraceHook;

  function handleSessionReset() {
    setEvents([]);
    setTraceEvents([]);
    setCommandLogCacheById({});
    setTraceError(null);
    setPatchCacheById({});
    setPatchBusyId(null);
    setApprovalBusyId(null);
  }

  function handleWorkspaceReset() {
    setEvents([]);
    setTraceEvents([]);
    setCommandLogCacheById({});
    setTraceError(null);
    setPatchCacheById({});
    setPatchBusyId(null);
    setApprovalBusyId(null);
    setChatMessages([]);
    setRuntimeHooks([]);
    setHookExecutions([]);
    setHookFeedback(null);
  }

  const workspaceHook = useWorkspaceSessions({
    addToast, toastError, setError,
    config, setConfig,
    onSessionReset: handleSessionReset,
    onWorkspaceReset: handleWorkspaceReset,
    loadSessionMessages,
    clearPendingAssistantTokens,
    taskHistory, setTaskHistory,
    setActiveTaskId,
    setTask,
    setChatMessages,
    setMessageBusy,
    setOpenTabs, setActiveTabId,
  });
  const {
    workspacePath, setWorkspacePath,
    sessionTitle, setSessionTitle,
    workspace, setWorkspace,
    sessions, setSessions,
    session, setSession,
    workspaceBusy, workspaceFocusBusy, workspaceMemoryBusy,
    sessionBusy, sessionListBusy, setSessionBusy,
    sessionActiveTaskMapRef,
    setActiveTaskForSession,
    ensureWorkspace,
    selectSession,
    refreshSessionHistory,
    handleOpenWorkspace,
    handleClearWorkspaceMemory,
    handleSaveWorkspaceFocus,
    handleCreateSession,
    handleRenameSession,
    handleDeleteSession,
    selectTask,
  } = workspaceHook;
  setActiveTaskForSessionRef.current = setActiveTaskForSession;

  function dismissToast(id: string) {
    setToasts((current) => current.filter((t) => t.id !== id));
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

  // Destructure hook returns
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

  // ── Tab actions hook ────────────────────────────────────────────────
  const tabActions = useTabActions({
    activeTabId,
    openTabs,
    setOpenTabs,
    setActiveTabId,
    sessions,
    selectSession,
  });

  // ── Message actions hook ────────────────────────────────────────────
  const activeTab = openTabs.find((tabItem) => tabItem.id === activeTabId) ?? openTabs[0] ?? getInitialTabs()[0];
  const activeSessionRecord = resolveSessionForTab(activeTab, sessions, session);
  const runtimeReady = Boolean(hostStatus && config);

  const messageActionsHook = useMessageActions({
    addToast, toastError, setError,
    prompt, setPrompt,
    promptAttachments, setPromptAttachments,
    queuedPromptSubmissions, setQueuedPromptSubmissions,
    chatMessages, setChatMessages,
    messageBusy, setMessageBusy,
    task, setTask,
    activeTaskId,
    session, setSession,
    sessions, setSessions,
    openTabs, setOpenTabs, setActiveTabId,
    hostStatus, config, providerSettings, setProviderSettings,
    loading,
    taskHistory, setTaskHistory,
    setActiveTaskForSession, setActiveTaskId,
    setApprovalBusyId, setTaskControlError, setSessionBusy,
    persistSearchConfig, ensureWorkspace,
    clearPendingAssistantTokens, handleTaskControl,
    handleRefreshMcpTools, refreshSkills,
    sessionTitle,
    activeTab, activeSessionRecord,
    runtimeReady,
    visibleChatMessages: getVisibleChatMessages(chatMessages, session?.id),
    workspace,
    mcpServers, skills,
  });
  const {
    sendMessageContent,
    handleSendMessage,
    handleQueuePrompt,
    handleQuoteMessage,
    handleSlashCommand,
    handleStopPrompt,
    addSystemMessage,
    ensureSessionForSend,
    composerCanStop,
    composerHasStreamingMessage,
    composerSending,
  } = messageActionsHook;

  // ── Event subscription hook ─────────────────────────────────────────
  useEventSubscription({
    setActiveTaskForSession,
    setEvents,
    setSession, setSessions,
    setTask, setActiveTaskId, setTaskHistory,
    setChatMessages,
    setTraceEvents,
    setCommandLogCacheById,
    setTraceError,
    setPatchCacheById,
    setPatchBusyId,
    setApprovalBusyId,
    setError,
    sessionActiveTaskMapRef,
    childTaskIdsRef,
    pendingAssistantTokenEventsRef,
    assistantTokenFlushTimerRef,
  });

  // ── Initialization useEffect ────────────────────────────────────────
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
      runtimeClient.listAgentProfiles().catch(() => ({ agents: [] as AgentProfileRecord[] })),
    ])
      .then(([nextHostStatus, nextConfig, nextSessions, nextTasks, nextScheduledTasks, nextSkills, nextMcpServers, nextAgentProfiles]) => {
        if (disposed) return;

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
        setAgentProfiles(nextAgentProfiles.agents);

        if (nextConfig.config.workspace.rootPath) {
          setWorkspacePath(nextConfig.config.workspace.rootPath);
        }
      })
      .catch((reason) => {
        if (!disposed) setError(reason instanceof Error ? reason.message : String(reason));
      })
      .finally(() => {
        if (!disposed) setLoading(false);
      });

    return () => { disposed = true; };
  }, []);

  useEffect(() => {
    if (!workspace?.id) {
      setRuntimeHooks([]);
      setHookExecutions([]);
      setHookFeedback(null);
      return;
    }
    void refreshRuntimeHooks(workspace.id);
  }, [workspace?.id]);

  // ── Trace auto-refresh useEffect ────────────────────────────────────
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
    return () => { cancelled = true; };
  }, [activeTaskId, traceAutoRefreshStatus]);

  useEffect(() => {
    if (!activeTaskId || !session?.id) {
      return;
    }
    if (task?.id === activeTaskId && !isTaskControllable(task.status)) {
      return;
    }
    let cancelled = false;
    const taskId = activeTaskId;
    const sessionId = session.id;

    const refreshTerminalTask = async () => {
      try {
        const result = await runtimeClient.getTask(taskId);
        if (cancelled) {
          return;
        }
        setTask((current) => (current?.id === result.task.id ? result.task : current));
        setTaskHistory((current) => sortByUpdatedAtDesc([result.task, ...current.filter((item) => item.id !== result.task.id)]));
        if (!isTaskControllable(result.task.status)) {
          setActiveTaskForSession(result.task.id, result.task.sessionId);
          clearPendingAssistantTokens();
          const messageResult = await runtimeClient.listMessages({ sessionId, limit: 500 });
          setChatMessages((current) => replaceSessionMessages(current, sessionId, messageResult.messages));
        }
      } catch {
        // Event delivery is still the primary path; this poll is a quiet safety net for missed terminal events.
      }
    };

    const timer = window.setInterval(() => {
      void refreshTerminalTask();
    }, 2_000);
    void refreshTerminalTask();

    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [activeTaskId, session?.id, task?.id, task?.status]);

  useEffect(() => {
    if (!task || !session?.id || !["completed", "failed", "cancelled"].includes(task.status)) {
      return;
    }
    let cancelled = false;
    const sessionId = session.id;

    clearPendingAssistantTokens();
    void runtimeClient
      .listMessages({ sessionId, limit: 500 })
      .then((result) => {
        if (!cancelled) {
          setChatMessages((current) => replaceSessionMessages(current, sessionId, result.messages));
        }
      })
      .catch(() => {
        // Terminal message events usually update the chat first; this refresh only backfills missed events.
      });

    return () => {
      cancelled = true;
    };
  }, [session?.id, task?.id, task?.status]);

  useEffect(() => {
    setTaskControlError(null);
  }, [task?.id, task?.status]);

  useEffect(() => {
    setWorktreeStatus(null);
    setWorktreeDiff(null);
    setWorktreeError(null);
    setWorktreeBusyAction(null);
  }, [task?.routing?.activeWorktree?.id]);

  // ── Queued prompt auto-send useEffect ───────────────────────────────
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
    void sendMessageContent(nextSubmission.content, nextSubmission.attachments, { clearComposer: false, mode: "new" });
  }, [composerCanStop, composerHasStreamingMessage, loading, messageBusy, queuedPromptSubmissions, runtimeReady, sendMessageContent]);

  const handleGuideQueuedPrompt = useCallback((id: string) => {
    const queued = queuedPromptSubmissions.find((item) => item.id === id);
    if (!queued) {
      return;
    }
    setQueuedPromptSubmissions((current) => current.filter((item) => item.id !== id));
    void sendMessageContent(queued.content, queued.attachments, {
      clearComposer: false,
      mode: "supplement",
    });
  }, [queuedPromptSubmissions, sendMessageContent]);

  const handleQueuedPromptRemove = useCallback((id: string) => {
    setQueuedPromptSubmissions((current) => current.filter((item) => item.id !== id));
  }, []);

  const handleQueuedPromptMove = useCallback((id: string, direction: "up" | "down") => {
    setQueuedPromptSubmissions((current) => {
      const index = current.findIndex((item) => item.id === id);
      if (index < 0) {
        return current;
      }
      const targetIndex = direction === "up" ? index - 1 : index + 1;
      if (targetIndex < 0 || targetIndex >= current.length) {
        return current;
      }
      const next = [...current];
      [next[index], next[targetIndex]] = [next[targetIndex], next[index]];
      return next;
    });
  }, []);

  // ── Derived views hook ──────────────────────────────────────────────
  const views = useDerivedViews({
    config, hostStatus, providerSettings, providerTestResult,
    activeProviderProfileId, activeProviderProfile, activeTab, activeTabId,
    session, sessions, task, activeTaskId, taskHistory,
    events, traceEvents, commandLogCacheById, patchCacheById,
    chatMessages, workspace, workspacePath,
    scheduledRecords, scheduledLogs, skills, mcpServers, agentProfiles,
    loading, error,
  });

  // ── Remaining inline computations ───────────────────────────────────
  const localPathActionsAvailable = runtimeClient.canOpenLocalAppPaths();
  const activeTabKind = resolveActiveTabKind(activeTab);
  const composerVisible = activeTabKind === "new-session" || activeTabKind === "session";
  const queuedPromptCount = queuedPromptSubmissions.length;

  // ── Render ──────────────────────────────────────────────────────────
  return (
    <AppShell
      tabs={openTabs}
      activeTabId={activeTabId}
      sessions={sessions}
      activeSessionId={getSidebarActiveSessionId(activeTab)}
      workspaceName={views.workspaceName}
      composerVisible={composerVisible}
      promptValue={prompt}
      onPromptChange={setPrompt}
      onOpenSystemTab={tabActions.handleOpenSystemTab}
      onOpenSessionTab={tabActions.handleOpenSessionTab}
      onActivateTab={tabActions.handleActivateTab}
      onCloseTab={tabActions.handleCloseTab}
      onCloseOtherTabs={tabActions.handleCloseOtherTabs}
      onRenameSession={handleRenameSession}
      onDeleteSession={handleDeleteSession}
      onSubmitPrompt={handleSendMessage}
      onQueuePrompt={handleQueuePrompt}
      onStopPrompt={handleStopPrompt}
      queuedPrompts={queuedPromptSubmissions}
      onGuideQueuedPrompt={handleGuideQueuedPrompt}
      onQueuedPromptRemove={handleQueuedPromptRemove}
      onQueuedPromptMove={handleQueuedPromptMove}
      disabled={loading || !runtimeReady}
      sending={composerSending}
      submitting={messageBusy}
      queuedPromptCount={queuedPromptCount}
      runtimeChildTasks={views.composerRuntimeChildTasks}
      attachments={promptAttachments}
      onAttachmentsChange={setPromptAttachments}
      onAttachmentError={toastError}
      modelOptions={(views.settingsProviders ?? []).map((provider: any) => ({
        id: provider.id,
        label: provider.models?.[0] ?? provider.name,
        subtitle: provider.models?.[0] && provider.name !== provider.models[0] ? provider.name : undefined,
      }))}
      selectedModelId={activeProviderProfileId}
      onSelectModel={selectProviderProfile}
      loading={loading}
      providerLabel={views.providerLabel}
      cwdLabel={views.cwdLabel}
      permissionLabel={views.permissionLabel}
      permissionMode={views.permissionMode}
      onPermissionModeChange={handlePermissionModeChange}
      runtimeLabel={views.runtimeStatusLabel}
      mcpLabel={views.mcpStatusLabel}
      approvalLabel={views.approvalStatusLabel}
      contextLabel={views.contextStatusLabel}
      contextPreview={views.sessionContextPreview}
      worktreeStatus={worktreeStatus ?? null}
      activeTaskStatus={task?.status ?? null}
      activeTaskCurrentStep={task?.currentStep ?? null}
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
      <WorkspaceRouter
        loading={loading}
        runtimeReady={runtimeReady}
        runtimeUnavailableReason={views.runtimeUnavailableReason}
        activeTab={activeTab}
        workspace={workspace}
        workspacePath={workspacePath}
        providerLabel={views.providerLabel}
        overviewRuntimeStatus={views.overviewRuntimeStatus}
        sessions={sessions}
        taskHistory={taskHistory}
        scheduledRecords={scheduledRecords}
        mcpServers={mcpServers}
        skills={skills}
        onOpenSystemTab={tabActions.handleOpenSystemTab}
        onOpenSessionTab={tabActions.handleOpenSessionTab}
        hostStatusText={views.hostStatusText}
        sessionTitle={sessionTitle}
        activeProviderProfileId={activeProviderProfileId}
        settingsProviders={views.settingsProviders}
        workspaceBusy={workspaceBusy}
        sessionBusy={sessionBusy}
        selectProviderProfile={selectProviderProfile}
        setSessionTitle={setSessionTitle}
        setWorkspacePath={setWorkspacePath}
        handleOpenWorkspace={handleOpenWorkspace}
        handleCreateSession={handleCreateSession}
        activeSessionRecord={activeSessionRecord}
        task={task}
        visibleChatMessages={views.visibleChatMessages}
        sessionTaskCount={views.sessionTaskCount}
        sessionCollaboration={views.sessionCollaboration}
        sessionBackgroundJobs={views.sessionBackgroundJobs}
        sessionApprovals={views.sessionApprovals}
        sessionPatches={views.sessionPatches}
        sessionTraceItems={views.sessionTraceItems}
        sessionToolCalls={views.sessionToolCalls}
        sessionContextPreview={views.sessionContextPreview}
        cwdLabel={views.cwdLabel}
        workspaceName={views.workspaceName}
        config={config}
        handleApprovalSubmit={handleApprovalSubmit}
        handleLoadPatchDiff={handleLoadPatchDiff}
        handleCopyRuntimeText={handleCopyRuntimeText}
        handleQuoteMessage={handleQuoteMessage}
        handleRefreshCommandJob={handleRefreshCommandJob}
        handleStopCommandJob={handleStopCommandJob}
        handleRefreshTask={handleRefreshTask}
        handleTaskControl={handleTaskControl}
        handleRefreshTrace={handleRefreshTrace}
        handleRefreshWorktree={handleRefreshWorktree}
        handleLoadWorktreeDiff={handleLoadWorktreeDiff}
        handleMergeWorktree={handleMergeWorktree}
        handleCleanupWorktree={handleCleanupWorktree}
        worktreeStatus={worktreeStatus ?? null}
        worktreeDiff={worktreeDiff ?? null}
        worktreeBusyAction={worktreeBusyAction}
        worktreeError={worktreeError}
        refreshBusy={refreshBusy}
        taskControlBusyAction={taskControlBusyAction}
        approvalBusyId={approvalBusyId}
        patchBusyId={patchBusyId}
        commandJobBusyId={commandJobBusyId}
        traceBusy={traceBusy}
        scheduledTasks={views.scheduledTasks}
        scheduledLogsByTaskId={views.scheduledLogsByTaskId}
        selectedScheduledTaskId={selectedScheduledTaskId}
        scheduledBusyTaskId={scheduledBusyTaskId}
        scheduledCreateBusy={scheduledCreateBusy}
        handleSelectScheduledTask={handleSelectScheduledTask}
        handleCreateScheduledTask={handleCreateScheduledTask}
        handleRunScheduledTask={handleRunScheduledTask}
        handleToggleScheduledTask={handleToggleScheduledTask}
        mcpLoading={mcpLoading}
        mcpBusyServerId={mcpBusyServerId}
        mcpLastRefresh={mcpLastRefresh}
        mcpError={mcpError}
        refreshMcpServers={refreshMcpServers}
        handleCreateMcpServer={handleCreateMcpServer}
        handleImportMcpServers={handleImportMcpServers}
        handleUpdateMcpServer={handleUpdateMcpServer}
        handleToggleMcpServer={handleToggleMcpServer}
        handleRefreshMcpTools={handleRefreshMcpTools}
        handleDeleteMcpServer={handleDeleteMcpServer}
        setMcpError={setMcpError}
        settingsSkills={views.settingsSkills}
        settingsAgents={views.settingsAgents}
        agentProfileBusyId={agentProfileBusyId}
        agentProfileFeedback={agentProfileFeedback}
        refreshAgentProfiles={refreshAgentProfiles}
        handleAddAgentProfile={handleAddAgentProfile}
        handleUpdateAgentProfile={handleUpdateAgentProfile}
        handleDeleteAgentProfile={handleDeleteAgentProfile}
        handleValidateAgentProfile={handleValidateAgentProfile}
        handlePreviewAgentProfileTools={handlePreviewAgentProfileTools}
        settingsHooks={runtimeHooks}
        hookExecutions={hookExecutions}
        hookBusyId={hookBusyId}
        hookFeedback={hookFeedback}
        refreshRuntimeHooks={() => refreshRuntimeHooks()}
        handleAddHook={handleAddHook}
        handleUpdateHook={handleUpdateHook}
        handleDeleteHook={handleDeleteHook}
        refreshHookExecutions={refreshHookExecutions}
        skillBusyId={skillBusyId}
        refreshSkills={refreshSkills}
        handleCreateSkill={handleCreateSkill}
        handleUpdateSkill={handleUpdateSkill}
        handleDeleteSkill={handleDeleteSkill}
        handleImportSkills={handleImportSkills}
        handleOpenAppPath={handleOpenAppPath}
        localPathActionsAvailable={localPathActionsAvailable}
        generalSettings={generalSettings}
        handleGeneralSettingsChange={handleGeneralSettingsChange}
        settingsAgentBehavior={views.settingsAgentBehavior}
        handleAgentBehaviorChange={handleAgentBehaviorChange}
        providerConfigBusy={providerConfigBusy}
        providerTestBusy={providerTestBusy}
        providerFeedback={providerFeedback ?? undefined}
        handlePermissionModeChange={handlePermissionModeChange}
        setIMSettings={setIMSettings}
        imSettings={imSettings}
        setComputerUseSettings={setComputerUseSettings}
        computerUseSettings={computerUseSettings}
        handleRecheckComputerUse={handleRecheckComputerUse}
        workspaceFocusBusy={workspaceFocusBusy}
        handleSaveWorkspaceFocus={workspace ? handleSaveWorkspaceFocus : undefined}
        workspaceMemoryBusy={workspaceMemoryBusy}
        handleClearWorkspaceMemory={workspace ? handleClearWorkspaceMemory : undefined}
        workspaceSummary={workspace?.summary ?? undefined}
        workspaceFocus={workspace?.focus ?? undefined}
        hostStatus={hostStatus}
        handleAddProviderFromSettings={handleAddProviderFromSettings}
        handleEditProviderFromSettings={handleEditProviderFromSettings}
        handleTestSelectedProvider={handleTestSelectedProvider}
        handleTestProviderConfigFromSettings={handleTestProviderConfigFromSettings}
        handleSaveProviderConfig={handleSaveProviderConfig}
      />
      <ToastContainer toasts={toasts} onDismiss={dismissToast} />
    </AppShell>
  );
}
