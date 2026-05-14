import { useEffect, useRef, useState } from "react";
import type {
  AgentEventEnvelope,
  AssistantTokenPayload,
  McpServerRecord,
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
  const [chatMessages, setChatMessages] = useState<ChatMessageView[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [toasts, setToasts] = useState<ToastEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [messageBusy, setMessageBusy] = useState(false);
  const [openTabs, setOpenTabs] = useState<WorkbenchTab[]>(() => getInitialTabs());
  const [activeTabId, setActiveTabId] = useState<WorkbenchTab["id"]>("system:overview");

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

  async function handleLoadWorktreeDiff(worktreeId: string) {
    const result = await runWorktreeAction("diff", () => runtimeClient.worktreeDiff({ worktreeId }));
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
    const result = await runWorktreeAction("requestMergeApproval", () => runtimeClient.worktreeRequestMergeApproval({ worktreeId }));
    if (result?.approval) {
      setWorktreeStatus(normalizeWorktreeStatus(result.gitStatus));
      setWorktreeDiff(result.diff ?? worktreeDiff);
      addToast("info", "Worktree merge approval requested.");
    }
  }

  async function handleCleanupWorktree(worktreeId: string, force = false) {
    const result = await runWorktreeAction("cleanup", () => runtimeClient.worktreeCleanup({ worktreeId, force }));
    if (result?.cleaned) {
      addToast("success", "Worktree cleaned.");
      await handleRefreshTask();
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
    task, setTask, setTaskHistory,
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
    ])
      .then(([nextHostStatus, nextConfig, nextSessions, nextTasks, nextScheduledTasks, nextSkills, nextMcpServers]) => {
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
    void sendMessageContent(nextSubmission.content, nextSubmission.attachments, { clearComposer: false, mode: "queued" });
  }, [composerCanStop, composerHasStreamingMessage, loading, messageBusy, queuedPromptSubmissions, runtimeReady]);

  // ── Derived views hook ──────────────────────────────────────────────
  const views = useDerivedViews({
    config, hostStatus, providerSettings, providerTestResult,
    activeProviderProfileId, activeProviderProfile, activeTab, activeTabId,
    session, sessions, task, activeTaskId, taskHistory,
    events, traceEvents, commandLogCacheById, patchCacheById,
    chatMessages, workspace, workspacePath,
    scheduledRecords, scheduledLogs, skills, mcpServers,
    loading, error,
  });

  // ── Remaining inline computations ───────────────────────────────────
  const localPathActionsAvailable = runtimeClient.canOpenLocalAppPaths();
  const composerVisible = runtimeReady && (activeTab.kind === "new-session" || activeTab.kind === "session");
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
      runtimeLabel={views.runtimeStatusLabel}
      mcpLabel={views.mcpStatusLabel}
      approvalLabel={views.approvalStatusLabel}
      contextLabel={views.contextStatusLabel}
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
