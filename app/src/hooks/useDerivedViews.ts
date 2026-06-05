import { useMemo } from "react";
import type {
  AppConfig,
  AgentProfileRecord,
  CommandLogRecord,
  SessionRecord,
  TaskRecord,
  WorkspaceRef,
} from "@shared";
import type { HostStatus, RuntimeConfig } from "../lib/runtimeClient";
import type { ProviderSettingsForm } from "../state/providerConfig";
import type { ProviderTestResult } from "@shared";
import type { ApprovalCardView, PatchCardView, ToolTimelineItem } from "../state/eventRecordViews";
import type { ComposerRuntimeChildTask } from "../ui/workbench/ComposerDock";
import type { SettingsProvider, SettingsAgentBehaviorConfig, SettingsAgentConfig, SettingsSkillConfig, SettingsPermissionRule } from "../ui/workbench/workspaces/settings/SettingsWorkspace";
import type { ScheduledTask, ExecutionLog } from "../ui/workbench/workspaces/scheduled/ScheduledWorkspace";
import type { SessionWorkspaceCollaboration, SessionWorkspaceBackgroundJob, SessionWorkspaceContextPreview } from "../ui/workbench/workspaces/session/SessionWorkspace";
import type { WorkbenchTab } from "../ui/workbench/types";

import { getVisibleChatMessages } from "../state/chatMessages";
import { normalizeProviderConfig, formatCompactCount, buildSettingsAgentBehaviorConfig } from "../state/providerConfig";
import { buildSettingsProviderLastTest, getProviderStatusView, getProviderRuntimeNotice, getProviderHealthView } from "../state/providerStatus";
import { normalizeSkillForSettings } from "../state/mcpSkillPayloads";
import { approvalModeToSettingsMode, buildSettingsGeneralConfig, buildSettingsPermissionRules } from "../state/providerPayloadParsing";
import { permissionModes } from "../ui/workbench/workspaces/settings/settingsTypes";
import { scheduledRecordToWorkspaceTask, scheduledRunToExecutionLog } from "../state/scheduleHelpers";
import { readEventText, readEventNumber, summarizeValue, countAddedLines, countDeletedLines, parsePatchFiles, riskToLevel } from "../state/traceReaders";
import { computeToolTimelineItems, computeApprovalCards, computeApprovalByPatchId, computePatchCards } from "../state/viewComputations";
import type { AgentEventLike } from "../state/viewComputations";
import { buildSessionContextPreview, buildSessionCollaboration, buildSessionBackgroundJobs, mergeSessionBackgroundJobs } from "../state/sessionDerivedViews";
import { workspaceNameFromPath } from "../state/providerConfig";
import { resolveSessionForTab } from "../ui/workbench/sessionRouting";

export interface UseDerivedViewsDeps {
  // Core state
  config: RuntimeConfig | null;
  hostStatus: HostStatus | null;
  providerSettings: ProviderSettingsForm;
  providerTestResult: ProviderTestResult | null;
  activeProviderProfileId: string;
  activeProviderProfile: any;
  activeTab: WorkbenchTab;
  activeTabId: string;
  session: SessionRecord | null;
  sessions: SessionRecord[];
  task: TaskRecord | null;
  activeTaskId: string | null;
  taskHistory: TaskRecord[];
  events: any[];
  traceEvents: any[];
  commandLogCacheById: Record<string, CommandLogRecord>;
  patchCacheById: Record<string, any>;
  chatMessages: any[];
  workspace: WorkspaceRef | null;
  workspacePath: string;
  scheduledRecords: any[];
  scheduledLogs: any[];
  skills: any[];
  mcpServers: any[];
  agentProfiles: AgentProfileRecord[];
  loading: boolean;
  error: string | null;
}

export function useDerivedViews(deps: UseDerivedViewsDeps) {
  const {
    config, hostStatus, providerSettings, providerTestResult,
    activeProviderProfile, activeTab, session, task, activeTaskId,
    taskHistory, events, traceEvents, commandLogCacheById,
    patchCacheById, chatMessages, workspace, workspacePath,
    scheduledRecords, scheduledLogs, skills, mcpServers, agentProfiles, loading, error,
  } = deps;

  const runtimeReady = Boolean(hostStatus && config);
  const activeSessionRecord = resolveSessionForTab(activeTab, deps.sessions, session);
  const viewSession = activeTab.kind === "session" ? activeSessionRecord : session;
  const viewSessionId = activeTab.kind === "session" ? viewSession?.id ?? activeTab.sessionId : viewSession?.id;
  const sessionScopedEvents = useMemo(
    () => viewSessionId ? events.filter((event: any) => !event?.sessionId || event.sessionId === viewSessionId) : events,
    [events, viewSessionId],
  );
  const sessionScopedTraceEvents = useMemo(
    () => viewSessionId ? traceEvents.filter((trace: any) => !trace?.sessionId || trace.sessionId === viewSessionId) : traceEvents,
    [traceEvents, viewSessionId],
  );

  // Provider status views
  const providerStatusView = getProviderStatusView(providerSettings, providerTestResult);
  const providerRuntimeNotice = getProviderRuntimeNotice(providerSettings, providerTestResult);
  const providerHealthView = getProviderHealthView(activeProviderProfile, providerTestResult);

  // Tool/approval/patch cards
  const runtimeTimelineEvents = useMemo<AgentEventLike[]>(() => {
    const merged = new Map<string, AgentEventLike>();

    sessionScopedTraceEvents
      .filter((trace: any) => trace?.uiReplayScope !== "panel")
      .forEach((trace: any) => {
        if (!trace?.type) return;
        const eventId = String(trace.id ?? `${trace.type}:${trace.taskId ?? ""}:${trace.sequence ?? trace.createdAt ?? ""}`);
        merged.set(`trace:${eventId}`, {
          type: trace.type,
          payload: trace.payload ?? {},
          taskId: trace.taskId,
          sessionId: trace.sessionId,
          eventId,
          ts: trace.createdAt ?? 0,
        });
      });

    sessionScopedEvents.forEach((event: any) => {
      if (!event?.type) return;
      const eventId = String(event.eventId ?? event.id ?? `${event.type}:${event.taskId ?? ""}:${event.ts ?? event.createdAt ?? ""}`);
      merged.set(`event:${eventId}`, {
        ...event,
        eventId,
        payload: event.payload ?? {},
        ts: event.ts ?? event.createdAt ?? 0,
      });
    });

    return Array.from(merged.values());
  }, [sessionScopedEvents, sessionScopedTraceEvents]);

  const toolTimelineItems = useMemo<ToolTimelineItem[]>(
    () => computeToolTimelineItems(runtimeTimelineEvents),
    [runtimeTimelineEvents],
  );
  const approvalCards = useMemo<ApprovalCardView[]>(
    () => computeApprovalCards(sessionScopedEvents),
    [sessionScopedEvents],
  );
  const approvalByPatchId = useMemo(
    () => computeApprovalByPatchId(approvalCards),
    [approvalCards],
  );
  const patchCards = useMemo<PatchCardView[]>(
    () => computePatchCards(sessionScopedEvents, patchCacheById, approvalByPatchId),
    [approvalByPatchId, sessionScopedEvents, patchCacheById],
  );

  // Visible chat messages
  const visibleChatMessages = useMemo(
    () => getVisibleChatMessages(chatMessages, viewSessionId),
    [chatMessages, viewSessionId],
  );

  const maxContextTokenBudget = useMemo(() => {
    const raw = activeProviderProfile?.maxContextTokens ?? providerSettings.maxContextTokens;
    const value = typeof raw === "number" ? raw : Number(raw);
    return Number.isFinite(value) && value > 0 ? value : undefined;
  }, [activeProviderProfile?.maxContextTokens, providerSettings.maxContextTokens]);

  // Session context preview
  const sessionContextPreview = useMemo(
    () => {
      if (activeTab.kind === "new-session") {
        return undefined;
      }
      return buildSessionContextPreview({
        events: sessionScopedEvents,
        traceEvents: sessionScopedTraceEvents,
        workspace,
        session: viewSession,
        activeTaskId,
        activeTask: task,
        maxContextTokens: maxContextTokenBudget,
      });
    },
    [activeTab.kind, activeTaskId, maxContextTokenBudget, sessionScopedEvents, sessionScopedTraceEvents, task, viewSession, workspace],
  );

  // Settings providers
  const settingsProviders = useMemo<SettingsProvider[] | undefined>(() => {
    if (!config) return undefined;
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

  const settingsPermissionRules = useMemo<SettingsPermissionRule[]>(
    () => buildSettingsPermissionRules(config),
    [config],
  );

  const sessionTaskCount = useMemo(() => {
    if (!viewSessionId) return undefined;
    return taskHistory.filter((item) => item.sessionId === viewSessionId).length;
  }, [taskHistory, viewSessionId]);
  const sessionTaskIds = useMemo(
    () => new Set(taskHistory.filter((item) => !viewSessionId || item.sessionId === viewSessionId).map((item) => item.id)),
    [taskHistory, viewSessionId],
  );

  const scheduledTasks = useMemo<ScheduledTask[]>(
    () => scheduledRecords.map(scheduledRecordToWorkspaceTask),
    [scheduledRecords],
  );

  const settingsSkills = useMemo<SettingsSkillConfig[]>(
    () => skills.map(normalizeSkillForSettings),
    [skills],
  );

  const settingsAgents = useMemo<SettingsAgentConfig[]>(
    () =>
      agentProfiles.map((profile) => ({
        ...profile,
        enabled: Boolean(profile.enabled),
        skillIds: profile.skillIds ?? [],
        mcpServerIds: profile.mcpServerIds ?? [],
        isBuiltin: Boolean(profile.isBuiltin ?? profile.is_builtin),
      })),
    [agentProfiles],
  );

  const scheduledLogsByTaskId = useMemo<Record<string, ExecutionLog[]>>(() => {
    return Object.fromEntries(
      scheduledRecords.map((record: any) => [
        record.id,
        scheduledLogs.filter((log: any) => log.taskId === record.id).map(scheduledRunToExecutionLog),
      ]),
    );
  }, [scheduledLogs, scheduledRecords]);

  // Session workspace data
  const sessionApprovals = useMemo(
    () =>
      approvalCards.map((approval) => ({
        id: approval.approvalId,
        title: approval.patchSummary ?? approval.command,
        kind: approval.kind,
        patchId: approval.patchId,
        status: approval.status,
        summary: approval.requestSummary,
        filesChanged: approval.filesChanged,
        changedPaths: approval.changedPaths,
        diff: approval.diffText,
        requestedAt: approval.requestedAt,
        risk: riskToLevel(approval.risk),
        parametersPreview: approval.requestSummary,
        fullInput: approval.requestJson,
        previewRows: approval.previewRows,
        previewSections: approval.previewSections,
        command: approval.command,
        cwd: approval.cwd,
        completionEvidence: approval.completionEvidence,
      })),
    [approvalCards],
  );

  const sessionPatches = useMemo(
    () =>
      patchCards.map((patch) => ({
        id: patch.patchId,
        taskId: patch.taskId,
        summary: patch.summary,
        status: patch.status,
        filesChanged: patch.filesChanged,
        additions: countAddedLines(patch.diffText ?? ""),
        deletions: countDeletedLines(patch.diffText ?? ""),
        updatedAt: patch.updatedAt,
        files: parsePatchFiles(patch.diffText, patch.changedPaths),
        diff: patch.diffText,
      })),
    [patchCards],
  );

  const sessionTraceItems = useMemo(
    () =>
      [...sessionScopedTraceEvents]
        .sort((left: any, right: any) => right.sequence - left.sequence)
        .map((trace: any) => ({
          id: trace.id,
          type: trace.type,
          source: trace.source,
          payload: trace.payload,
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
    [sessionScopedTraceEvents],
  );

  const sessionToolCalls = useMemo(
    () =>
      toolTimelineItems
        .map((toolCall) => ({
          id: toolCall.id,
          toolUseId: toolCall.toolCallId,
          parentToolUseId: toolCall.parentToolUseId,
          toolGroupId: toolCall.toolGroupId,
          toolIndex: toolCall.toolIndex,
          toolTotal: toolCall.toolTotal,
          toolOperationId: toolCall.toolOperationId,
          toolOperationLabel: toolCall.toolOperationLabel,
          toolCategory: toolCall.toolCategory,
          toolPhaseId: toolCall.toolPhaseId,
          toolPhaseLabel: toolCall.toolPhaseLabel,
          toolSemanticParentId: toolCall.toolSemanticParentId,
          toolSemanticParentLabel: toolCall.toolSemanticParentLabel,
          toolName: toolCall.toolName,
          status: toolCall.status,
          target: toolCall.target,
          taskId: toolCall.taskId,
          time: toolCall.finishedAt ?? toolCall.updatedAt ?? toolCall.startedAt,
          resultSummary: toolCall.errorSummary ?? toolCall.resultSummary,
          resultPreview: toolCall.resultPreview,
          durationMs: toolCall.durationMs,
          argsPreview: toolCall.argsSummary,
          input: toolCall.argsSummary,
          output: toolCall.resultSummary,
          rawInput: toolCall.argsRaw,
          rawOutput: toolCall.resultRaw,
          stderr: toolCall.errorSummary,
        })),
    [toolTimelineItems],
  );

  const sessionCollaboration = useMemo(
    () => buildSessionCollaboration(sessionScopedEvents, sessionScopedTraceEvents),
    [sessionScopedEvents, sessionScopedTraceEvents],
  );

  const composerRuntimeChildTasks: ComposerRuntimeChildTask[] = useMemo(
    () =>
      (sessionCollaboration.childTasks ?? []).map((childTask: any) => ({
        id: childTask.id,
        title: childTask.title,
        status: childTask.status,
        workerName: childTask.workerName,
        agentType: childTask.agentType,
        summary: childTask.summary,
        attention: childTask.attention,
        createdAt: childTask.createdAt,
        updatedAt: childTask.updatedAt,
      })),
    [sessionCollaboration.childTasks],
  );

  const sessionBackgroundJobs = useMemo(
    () => {
      const eventJobs = buildSessionBackgroundJobs(
        sessionScopedEvents,
        sessionScopedTraceEvents.filter((trace: any) => trace?.uiReplayScope !== "panel"),
      );
      const commandLogs = Object.values(commandLogCacheById).filter((log) => !viewSessionId || sessionTaskIds.has(log.taskId));
      return mergeSessionBackgroundJobs(eventJobs, commandLogs);
    },
    [commandLogCacheById, sessionScopedEvents, sessionScopedTraceEvents, sessionTaskIds, viewSessionId],
  );

  // Labels
  const isNewSessionTab = activeTab.kind === "new-session";
  const activeSessionWorkspaceRoot = activeTab.kind === "session" ? viewSession?.workspaceRoot : undefined;
  const activeSessionWorkspaceName =
    activeTab.kind === "session"
      ? viewSession?.workspaceName ?? workspaceNameFromPath(activeSessionWorkspaceRoot)
      : undefined;
  const launchWorkspaceName = workspaceNameFromPath(workspacePath);
  const workspaceName =
    isNewSessionTab
      ? launchWorkspaceName ?? workspace?.name ?? "yuanbao_agent"
      : activeSessionWorkspaceName ?? workspace?.name ?? launchWorkspaceName ?? "yuanbao_agent";

  const providerLabel =
    providerSettings.mode === "mock"
      ? "本地预览模型"
      : providerSettings.model || providerSettings.name || "未配置模型";
  const permissionMode = approvalModeToSettingsMode(config?.policy.approvalMode);
  const permissionLabel =
    permissionMode === "skip"
      ? "完全访问权限"
      : permissionModes.find((mode) => mode.id === permissionMode)?.title ?? permissionMode;

  const hostStatusText = !hostStatus
    ? "正在检测运行时"
    : hostStatus.runtimeRunning ? "本地运行时已连接" : "浏览器预览模式";

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

  const enabledMcpServers = mcpServers.filter((server: any) => server.enabled).length;
  const mcpStatusLabel = `${enabledMcpServers}/${mcpServers.length || 0} MCP`;
  const pendingApprovalCount = approvalCards.filter((approval) => approval.status === "pending").length;
  const approvalStatusLabel = `${pendingApprovalCount} 个审批`;
  const contextStats = sessionContextPreview?.budgetStats;
  const contextStatusLabel = contextStats?.maxContextTokens
    ? `${formatCompactCount(contextStats.estimatedTokens ?? contextStats.estimatedInputTokens)}/${formatCompactCount(contextStats.maxContextTokens)} 上下文`
    : `${formatCompactCount(contextStats?.estimatedTokens ?? contextStats?.estimatedInputTokens)} 上下文`;

  const cwdLabel =
    isNewSessionTab
      ? workspacePath ?? workspace?.rootPath ?? ""
      : activeSessionWorkspaceRoot ??
        sessionContextPreview?.workspaceRoot ??
        workspace?.rootPath ??
        workspacePath ??
        "";

  const runtimeUnavailableReason =
    !loading && !runtimeReady
      ? error ?? "运行时握手未完成。前端无法独立执行任务。"
      : null;

  return {
    runtimeReady,
    providerStatusView, providerRuntimeNotice, providerHealthView,
    toolTimelineItems, approvalCards, approvalByPatchId, patchCards,
    visibleChatMessages, sessionContextPreview,
    settingsProviders, settingsAgentBehavior, sessionTaskCount,
    settingsPermissionRules,
    scheduledTasks, settingsSkills, settingsAgents, scheduledLogsByTaskId,
    sessionApprovals, sessionPatches, sessionTraceItems, sessionToolCalls,
    sessionCollaboration, composerRuntimeChildTasks, sessionBackgroundJobs,
    workspaceName, providerLabel, hostStatusText, overviewRuntimeStatus,
    runtimeStatusLabel, mcpStatusLabel, approvalStatusLabel, contextStatusLabel,
    cwdLabel, permissionMode, permissionLabel, runtimeUnavailableReason,
  };
}
