import { useMemo } from "react";
import type {
  AppConfig,
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
import type { SettingsProvider, SettingsAgentBehaviorConfig, SettingsSkillConfig } from "../ui/workbench/workspaces/settings/SettingsWorkspace";
import type { ScheduledTask, ExecutionLog } from "../ui/workbench/workspaces/scheduled/ScheduledWorkspace";
import type { SessionWorkspaceCollaboration, SessionWorkspaceBackgroundJob, SessionWorkspaceContextPreview } from "../ui/workbench/workspaces/session/SessionWorkspace";
import type { WorkbenchTab } from "../ui/workbench/types";

import { getVisibleChatMessages } from "../state/chatMessages";
import { normalizeProviderConfig, formatCompactCount, buildSettingsAgentBehaviorConfig } from "../state/providerConfig";
import { buildSettingsProviderLastTest, getProviderStatusView, getProviderRuntimeNotice, getProviderHealthView } from "../state/providerStatus";
import { normalizeSkillForSettings } from "../state/mcpSkillPayloads";
import { buildSettingsGeneralConfig } from "../state/providerPayloadParsing";
import { scheduledRecordToWorkspaceTask, scheduledRunToExecutionLog } from "../state/scheduleHelpers";
import { readEventText, readEventNumber, summarizeValue, countAddedLines, countDeletedLines, parsePatchFiles, riskToLevel } from "../state/traceReaders";
import { computeToolTimelineItems, computeApprovalCards, computeApprovalByPatchId, computePatchCards } from "../state/viewComputations";
import { buildSessionContextPreview, buildSessionCollaboration, buildSessionBackgroundJobs, mergeSessionBackgroundJobs } from "../state/sessionDerivedViews";
import { workspaceNameFromPath } from "../state/providerConfig";

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
  loading: boolean;
  error: string | null;
}

export function useDerivedViews(deps: UseDerivedViewsDeps) {
  const {
    config, hostStatus, providerSettings, providerTestResult,
    activeProviderProfile, activeTab, session, task, activeTaskId,
    taskHistory, events, traceEvents, commandLogCacheById,
    patchCacheById, chatMessages, workspace, workspacePath,
    scheduledRecords, scheduledLogs, skills, mcpServers, loading, error,
  } = deps;

  const runtimeReady = Boolean(hostStatus && config);

  // Provider status views
  const providerStatusView = getProviderStatusView(providerSettings, providerTestResult);
  const providerRuntimeNotice = getProviderRuntimeNotice(providerSettings, providerTestResult);
  const providerHealthView = getProviderHealthView(activeProviderProfile, providerTestResult);

  // Tool/approval/patch cards
  const toolTimelineItems = useMemo<ToolTimelineItem[]>(
    () => computeToolTimelineItems(events),
    [events],
  );
  const approvalCards = useMemo<ApprovalCardView[]>(
    () => computeApprovalCards(events),
    [events],
  );
  const approvalByPatchId = useMemo(
    () => computeApprovalByPatchId(approvalCards),
    [approvalCards],
  );
  const patchCards = useMemo<PatchCardView[]>(
    () => computePatchCards(events, patchCacheById, approvalByPatchId),
    [approvalByPatchId, events, patchCacheById],
  );

  // Visible chat messages
  const visibleChatMessages = useMemo(
    () => getVisibleChatMessages(chatMessages, session?.id),
    [chatMessages, session?.id],
  );

  // Session context preview
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

  const sessionTaskCount = useMemo(() => {
    if (!session) return undefined;
    return taskHistory.filter((item) => item.sessionId === session.id).length;
  }, [session, taskHistory]);

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
        status: approval.status,
        summary: approval.requestSummary,
        requestedAt: approval.requestedAt,
        risk: riskToLevel(approval.risk),
        parametersPreview: approval.requestSummary,
        fullInput: approval.requestJson,
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
        .sort((left: any, right: any) => right.sequence - left.sequence)
        .map((trace: any) => ({
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
      (sessionCollaboration.childTasks ?? []).map((childTask: any) => ({
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

  // Labels
  const activeSessionWorkspaceRoot = activeTab.kind === "session" ? session?.workspaceRoot : undefined;
  const activeSessionWorkspaceName =
    activeTab.kind === "session"
      ? session?.workspaceName ?? workspaceNameFromPath(activeSessionWorkspaceRoot)
      : undefined;
  const workspaceName =
    activeSessionWorkspaceName ?? workspace?.name ?? workspaceNameFromPath(workspacePath) ?? "yuanbao_agent";

  const providerLabel =
    providerSettings.mode === "mock"
      ? "本地预览模型"
      : providerSettings.model || providerSettings.name || "未配置模型";

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
    sessionContextPreview?.workspaceRoot ??
    activeSessionWorkspaceRoot ??
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
    scheduledTasks, settingsSkills, scheduledLogsByTaskId,
    sessionApprovals, sessionPatches, sessionTraceItems, sessionToolCalls,
    sessionCollaboration, composerRuntimeChildTasks, sessionBackgroundJobs,
    workspaceName, providerLabel, hostStatusText, overviewRuntimeStatus,
    runtimeStatusLabel, mcpStatusLabel, approvalStatusLabel, contextStatusLabel,
    cwdLabel, runtimeUnavailableReason,
  };
}
