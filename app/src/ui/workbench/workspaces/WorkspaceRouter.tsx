import type {
  WorkspaceRef,
  AgentProfileCreateParams,
  AgentProfilePreviewToolsParams,
  AgentProfilePreviewToolsResult,
  AgentProfileValidateParams,
  AgentProfileValidateResult,
  SessionRecord,
  TaskRecord,
  McpServerRecord,
  SkillPresetRecord,
  ScheduledTaskRecord,
  RuntimeHookExecutionRecord,
} from "@shared";
import type { HostStatus, RuntimeConfig } from "../../../lib/runtimeClient";
import type {
  SettingsSkillConfig,
  SettingsAgentConfig,
  SettingsAgentFeedback,
  SettingsHookConfig,
  SettingsHookDraft,
  SettingsHookFeedback,
  SettingsProviderFeedback,
  SettingsAgentBehaviorConfig,
  SettingsGeneralConfig,
  SettingsIMConfig,
  SettingsComputerUseConfig,
} from "./settings/SettingsWorkspace";
import type {
  ScheduledTask,
  ExecutionLog,
} from "./scheduled/ScheduledWorkspace";
import type {
  SessionWorkspaceCollaboration,
  SessionWorkspaceContextPreview,
  SessionWorkspaceBackgroundJob,
  SessionWorkspaceWorktreeDiff,
  SessionWorkspaceWorktreeStatus,
} from "./session/SessionWorkspace";
import type { WorkbenchTab, SystemWorkspaceKind } from "../types";

import { approvalModeToSettingsMode } from "../../../state/providerPayloadParsing";
import { CleanNewSessionWorkspace } from "../../haha-clean/pages/CleanNewSessionWorkspace";
import { ScheduledWorkspace } from "./scheduled/ScheduledWorkspace";
import { McpWorkspace } from "./mcp/McpWorkspace";
import { AppearanceWorkspace } from "./appearance/AppearanceWorkspace";
import { ComponentPlaygroundWorkspace } from "./playground/ComponentPlaygroundWorkspace";
import { SkillsWorkspace } from "./skills/SkillsWorkspace";
import { WorkbenchOverviewPage } from "../../v2/pages/WorkbenchOverviewPage";
import { CleanSessionWorkspace } from "../../haha-clean/conversation/CleanSessionWorkspace";
import { SettingsWorkspace } from "./settings/SettingsWorkspace";

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

export interface WorkspaceRouterProps {
  loading: boolean;
  runtimeReady: boolean;
  runtimeUnavailableReason: string | null;
  activeTab: WorkbenchTab;

  // Overview
  workspace: WorkspaceRef | null;
  workspacePath: string;
  providerLabel: string;
  overviewRuntimeStatus: "ready" | "degraded" | "offline";
  sessions: SessionRecord[];
  taskHistory: TaskRecord[];
  scheduledRecords: ScheduledTaskRecord[];
  mcpServers: McpServerRecord[];
  skills: SkillPresetRecord[];
  onOpenSystemTab: (kind: SystemWorkspaceKind) => void;
  onOpenSessionTab: (session: any) => void;

  // New session
  hostStatusText: string;
  sessionTitle: string;
  activeProviderProfileId: string;
  settingsProviders: any[] | undefined;
  workspaceBusy: boolean;
  sessionBusy: boolean;
  selectProviderProfile: (id: string) => void;
  setSessionTitle: (title: string) => void;
  setWorkspacePath: (path: string) => void;
  handleOpenWorkspace: () => Promise<void>;
  handleCreateSession: () => Promise<void>;

  // Session workspace
  activeSessionRecord: SessionRecord | null;
  task: TaskRecord | null;
  visibleChatMessages: any[];
  sessionTaskCount: number | undefined;
  sessionCollaboration: SessionWorkspaceCollaboration;
  sessionBackgroundJobs: SessionWorkspaceBackgroundJob[];
  sessionApprovals: any[];
  sessionPatches: any[];
  sessionTraceItems: any[];
  sessionToolCalls: any[];
  sessionContextPreview: SessionWorkspaceContextPreview | undefined;
  cwdLabel: string;
  workspaceName: string;
  config: RuntimeConfig | null;
  handleApprovalSubmit: (approvalId: string, decision: "approved" | "rejected") => Promise<void>;
  handleLoadPatchDiff: (patchId: string) => Promise<void>;
  handleCopyRuntimeText: (label: string, text: string) => Promise<void>;
  handleRefreshCommandJob: (commandId: string) => Promise<void>;
  handleStopCommandJob: (commandId: string) => Promise<void>;
  handleRefreshTask: () => Promise<void>;
  handleTaskControl: (action: any, taskId?: string) => Promise<void>;
  handleRefreshTrace: () => Promise<void>;
  handleRefreshWorktree: (worktreeId: string) => Promise<void>;
  handleLoadWorktreeDiff: (worktreeId: string, full?: boolean) => Promise<void>;
  handleMergeWorktree: (worktreeId: string) => Promise<void>;
  handleCleanupWorktree: (worktreeId: string, force?: boolean) => Promise<void>;
  worktreeStatus: SessionWorkspaceWorktreeStatus | null;
  worktreeDiff: SessionWorkspaceWorktreeDiff | null;
  worktreeBusyAction: "status" | "diff" | "requestMergeApproval" | "merge" | "cleanup" | null;
  worktreeError: string | null;
  refreshBusy: boolean;
  taskControlBusyAction: any;
  approvalBusyId: string | null;
  patchBusyId: string | null;
  commandJobBusyId: string | null;
  traceBusy: boolean;

  // Scheduled
  scheduledTasks: ScheduledTask[];
  scheduledLogsByTaskId: Record<string, ExecutionLog[]>;
  selectedScheduledTaskId: string | null;
  scheduledBusyTaskId: string | null;
  scheduledCreateBusy: boolean;
  handleSelectScheduledTask: (id: string) => void;
  handleCreateScheduledTask: (draft: any) => Promise<void>;
  handleRunScheduledTask: (id: string) => Promise<void>;
  handleToggleScheduledTask: (taskId: string) => void | Promise<void>;

  // MCP
  mcpLoading: boolean;
  mcpBusyServerId: string | null;
  mcpLastRefresh: any;
  mcpError: string | null;
  refreshMcpServers: () => Promise<void>;
  handleCreateMcpServer: (draft: any) => Promise<void>;
  handleImportMcpServers: (servers: any[]) => Promise<void>;
  handleUpdateMcpServer: (id: string, patch: any) => Promise<void>;
  handleToggleMcpServer: (id: string, enabled: boolean) => Promise<void>;
  handleRefreshMcpTools: () => Promise<void>;
  handleDeleteMcpServer: (id: string) => Promise<void>;
  setMcpError: (error: string | null) => void;

  // Skills
  settingsAgents: SettingsAgentConfig[];
  agentProfileBusyId: string | null;
  agentProfileFeedback: SettingsAgentFeedback | null;
  refreshAgentProfiles: () => Promise<void>;
  handleAddAgentProfile: (payload: AgentProfileCreateParams) => Promise<void>;
  handleUpdateAgentProfile: (agentId: string, payload: Partial<AgentProfileCreateParams>) => Promise<void>;
  handleDeleteAgentProfile: (agentId: string) => Promise<void>;
  handleValidateAgentProfile: (payload: AgentProfileValidateParams) => Promise<AgentProfileValidateResult>;
  handlePreviewAgentProfileTools: (payload: AgentProfilePreviewToolsParams) => Promise<AgentProfilePreviewToolsResult>;
  settingsHooks: SettingsHookConfig[];
  hookExecutions: RuntimeHookExecutionRecord[];
  hookBusyId: string | null;
  hookFeedback: SettingsHookFeedback | null;
  refreshRuntimeHooks: () => Promise<void>;
  handleAddHook: (payload: SettingsHookDraft) => Promise<void>;
  handleUpdateHook: (hookId: string, payload: Partial<SettingsHookDraft>) => Promise<void>;
  handleDeleteHook: (hookId: string) => Promise<void>;
  refreshHookExecutions: (hookId?: string) => Promise<void>;
  settingsSkills: SettingsSkillConfig[];
  skillBusyId: string | null;
  refreshSkills: () => void;
  handleCreateSkill: (draft: any) => Promise<void>;
  handleUpdateSkill: (id: string, patch: any) => Promise<void>;
  handleDeleteSkill: (id: string) => Promise<void>;
  handleImportSkills: (filePath: string) => Promise<void>;
  handleOpenAppPath: (kind: "logs" | "data" | "skills") => Promise<void>;
  localPathActionsAvailable: boolean;

  // Appearance
  generalSettings: SettingsGeneralConfig;
  handleGeneralSettingsChange: (next: SettingsGeneralConfig) => Promise<void>;

  // Settings
  settingsAgentBehavior: SettingsAgentBehaviorConfig | undefined;
  handleAgentBehaviorChange: (next: SettingsAgentBehaviorConfig) => Promise<void>;
  providerConfigBusy: boolean;
  providerTestBusy: boolean;
  providerFeedback: SettingsProviderFeedback | undefined;
  handlePermissionModeChange: (mode: string) => void;
  setIMSettings: (settings: SettingsIMConfig) => void;
  imSettings: SettingsIMConfig;
  setComputerUseSettings: (settings: SettingsComputerUseConfig) => void;
  computerUseSettings: SettingsComputerUseConfig;
  handleRecheckComputerUse: () => void;
  workspaceFocusBusy: boolean;
  handleSaveWorkspaceFocus: ((focus: string) => Promise<void>) | undefined;
  workspaceMemoryBusy: boolean;
  handleClearWorkspaceMemory: (() => Promise<void>) | undefined;
  workspaceSummary: string | undefined;
  workspaceFocus: string | undefined;
  hostStatus: HostStatus | null;
  handleAddProviderFromSettings: (draft: any) => Promise<void>;
  handleEditProviderFromSettings: (id: string, patch: any) => Promise<void>;
  handleTestSelectedProvider: (providerId?: string) => void | Promise<void>;
  handleTestProviderConfigFromSettings: (payload: any) => Promise<any>;
  handleSaveProviderConfig: () => Promise<void>;
}

export function WorkspaceRouter(props: WorkspaceRouterProps) {
  const {
    loading, runtimeReady, runtimeUnavailableReason, activeTab,
  } = props;

  if (!runtimeReady && !loading) {
    return <RuntimeUnavailableWorkspace errorMessage={runtimeUnavailableReason ?? "Runtime unavailable."} />;
  }

  if (activeTab.kind === "overview") {
    return (
      <WorkbenchOverviewPage
        workspace={props.workspace}
        workspacePath={props.workspacePath}
        providerLabel={props.providerLabel}
        runtimeStatus={props.overviewRuntimeStatus}
        sessions={props.sessions}
        tasks={props.taskHistory}
        mcpServers={props.mcpServers}
        skills={props.skills}
        onOpenNewSession={() => props.onOpenSystemTab("new-session")}
        onOpenSession={props.onOpenSessionTab}
        onOpenMcp={() => props.onOpenSystemTab("mcp")}
      />
    );
  }

  if (activeTab.kind === "new-session") {
    return (
      <CleanNewSessionWorkspace
        workspacePath={props.workspacePath}
        hostStatusText={props.hostStatusText}
        sessionTitle={props.sessionTitle}
        modelLabel={props.providerLabel}
        modelOptions={(props.settingsProviders ?? []).map((provider: any) => ({
          id: provider.id,
          label: provider.models?.[0] ?? provider.name,
          subtitle: provider.models?.[0] && provider.name !== provider.models[0] ? provider.name : undefined,
        }))}
        selectedModelId={props.activeProviderProfileId}
        workspaceBusy={props.workspaceBusy}
        sessionBusy={props.sessionBusy}
        onSelectModel={props.selectProviderProfile}
        onSessionTitleChange={props.setSessionTitle}
        onWorkspacePathChange={props.setWorkspacePath}
        onOpenWorkspace={props.handleOpenWorkspace}
        onCreateSession={props.handleCreateSession}
      />
    );
  }

  if (activeTab.kind === "session") {
    return (
      <CleanSessionWorkspace
        session={props.activeSessionRecord}
        activeTask={
          props.task
            ? {
                id: props.task.id,
                status: props.task.status,
                goal: props.task.goal,
                createdAt: props.task.createdAt,
                updatedAt: props.task.updatedAt,
                acceptanceCriteria: props.task.acceptanceCriteria,
                outOfScope: props.task.outOfScope,
                currentStep: props.task.currentStep,
                changedFiles: props.task.changedFiles,
                commands: props.task.commands,
                verification: props.task.verification,
                summary: props.task.summary,
                resultSummary: props.task.resultSummary,
                activeWorktree: props.task.routing?.activeWorktree ?? null,
                mainWorkflow: (props.task.routing?.mainWorkflow as any) ?? null,
                planSteps: props.task.plan?.map((step: any) => ({
                  id: step.id,
                  title: step.title,
                  status: step.status,
                  detail: step.detail,
                })),
              }
            : null
        }
        messages={props.visibleChatMessages}
        messagesLoading={props.sessionBusy}
        taskCount={props.sessionTaskCount}
        collaboration={props.sessionCollaboration}
        backgroundJobs={props.sessionBackgroundJobs}
        approvals={props.sessionApprovals}
        patches={props.sessionPatches}
        traces={props.sessionTraceItems}
        toolCalls={props.sessionToolCalls}
        contextPreview={props.sessionContextPreview}
        composerContext={{
          cwd: props.cwdLabel,
          repo: props.workspaceName,
          model: props.providerLabel,
          permissionMode: approvalModeToSettingsMode(props.config?.policy.approvalMode),
        }}
        onApprove={(approvalId: string) => props.handleApprovalSubmit(approvalId, "approved")}
        onApproveForSession={(approvalId: string) => props.handleApprovalSubmit(approvalId, "approved")}
        onReject={(approvalId: string) => props.handleApprovalSubmit(approvalId, "rejected")}
        onLoadPatch={props.handleLoadPatchDiff}
        onCopyPatchPath={(_patchId: string, path: string) => {
          void props.handleCopyRuntimeText("补丁路径", path);
        }}
        onCopyRuntimeText={props.handleCopyRuntimeText}
        onRefreshCommandJob={props.handleRefreshCommandJob}
        onStopCommandJob={props.handleStopCommandJob}
        onRefreshTask={props.handleRefreshTask}
        onPauseTask={(taskId: string) => props.handleTaskControl("pause", taskId)}
        onResumeTask={(taskId: string) => props.handleTaskControl("resume", taskId)}
        onStopTask={(taskId: string) => props.handleTaskControl("cancel", taskId)}
        onRefreshTrace={props.handleRefreshTrace}
        onRefreshWorktree={props.handleRefreshWorktree}
        onLoadWorktreeDiff={props.handleLoadWorktreeDiff}
        onMergeWorktree={props.handleMergeWorktree}
        onCleanupWorktree={props.handleCleanupWorktree}
        worktreeStatus={props.worktreeStatus}
        worktreeDiff={props.worktreeDiff}
        worktreeBusyAction={props.worktreeBusyAction}
        worktreeError={props.worktreeError}
        taskBusyAction={
          props.refreshBusy
            ? "refresh"
            : props.taskControlBusyAction === "cancel"
              ? "stop"
              : props.taskControlBusyAction === "pause" || props.taskControlBusyAction === "resume"
                ? props.taskControlBusyAction
                : null
        }
        busyId={props.approvalBusyId ?? props.patchBusyId ?? props.commandJobBusyId ?? (props.traceBusy ? "trace" : null)}
      />
    );
  }

  if (activeTab.kind === "scheduled") {
    return (
      <ScheduledWorkspace
        tasks={props.scheduledTasks}
        logsByTaskId={props.scheduledLogsByTaskId}
        selectedTaskId={props.selectedScheduledTaskId ?? undefined}
        onSelectTask={props.handleSelectScheduledTask}
        onCreateTask={props.handleCreateScheduledTask}
        onRunTask={props.handleRunScheduledTask}
        onToggleTask={props.handleToggleScheduledTask}
        busyTaskId={props.scheduledBusyTaskId}
        createBusy={props.scheduledCreateBusy}
        workspacePath={props.cwdLabel}
      />
    );
  }

  if (activeTab.kind === "mcp") {
    return (
      <McpWorkspace
        servers={props.mcpServers}
        loading={props.mcpLoading}
        busyServerId={props.mcpBusyServerId}
        lastRefresh={props.mcpLastRefresh}
        errorMessage={props.mcpError}
        onRefreshServers={props.refreshMcpServers}
        onCreateServer={props.handleCreateMcpServer}
        onImportServers={props.handleImportMcpServers}
        onUpdateServer={props.handleUpdateMcpServer}
        onToggleServer={props.handleToggleMcpServer}
        onRefreshTools={props.handleRefreshMcpTools}
        onDeleteServer={props.handleDeleteMcpServer}
        onDismissError={() => props.setMcpError(null)}
      />
    );
  }

  if (activeTab.kind === "skills") {
    return (
      <SkillsWorkspace
        skills={props.settingsSkills}
        mcpServers={props.mcpServers}
        mcpToolCount={props.mcpLastRefresh?.tools.length ?? 0}
        providerLabel={props.providerLabel}
        busySkillId={props.skillBusyId}
        onRefreshSkills={props.refreshSkills}
        onOpenMcp={() => props.onOpenSystemTab("mcp")}
        onOpenSettings={() => props.onOpenSystemTab("settings")}
        onCreateSkill={props.handleCreateSkill}
        onUpdateSkill={props.handleUpdateSkill}
        onDeleteSkill={props.handleDeleteSkill}
        onImportSkills={props.handleImportSkills}
        onOpenSkillsFolder={props.localPathActionsAvailable ? () => void props.handleOpenAppPath("skills") : undefined}
      />
    );
  }

  if (activeTab.kind === "appearance") {
    return (
      <AppearanceWorkspace
        value={props.generalSettings}
        workspaceName={props.workspaceName}
        providerLabel={props.providerLabel}
        onChange={props.handleGeneralSettingsChange}
        onOpenSettings={() => props.onOpenSystemTab("settings")}
      />
    );
  }

  if (activeTab.kind === "playground") {
    return (
      <ComponentPlaygroundWorkspace
        onOpenAppearance={() => props.onOpenSystemTab("appearance")}
        onOpenSkills={() => props.onOpenSystemTab("skills")}
      />
    );
  }

  return (
    <SettingsWorkspace
      providers={props.settingsProviders}
      activeProviderId={props.config?.provider.activeProfileId}
      onSelectProvider={props.selectProviderProfile}
      onAddProvider={props.handleAddProviderFromSettings}
      onEditProvider={props.handleEditProviderFromSettings}
      onTestProvider={props.handleTestSelectedProvider}
      onTestProviderConfig={props.handleTestProviderConfigFromSettings}
      onSaveProvider={props.handleSaveProviderConfig}
      providerBusy={props.providerConfigBusy}
      providerTestBusy={props.providerTestBusy}
      providerFeedback={props.providerFeedback}
      permissionMode={approvalModeToSettingsMode(props.config?.policy.approvalMode)}
      onPermissionModeChange={props.handlePermissionModeChange}
      agentBehavior={props.settingsAgentBehavior}
      onAgentBehaviorChange={props.handleAgentBehaviorChange}
      general={props.generalSettings}
      onGeneralChange={props.handleGeneralSettingsChange}
      im={props.imSettings}
      onIMChange={props.setIMSettings}
      agents={props.settingsAgents}
      agentBusyId={props.agentProfileBusyId}
      agentFeedback={props.agentProfileFeedback}
      onRefreshAgents={props.refreshAgentProfiles}
      onAgentToggle={(agentId, enabled) => props.handleUpdateAgentProfile(agentId, { enabled })}
      onAddAgent={props.handleAddAgentProfile}
      onUpdateAgent={props.handleUpdateAgentProfile}
      onDeleteAgent={props.handleDeleteAgentProfile}
      onValidateAgent={props.handleValidateAgentProfile}
      onPreviewAgentTools={props.handlePreviewAgentProfileTools}
      hooks={props.settingsHooks}
      hookExecutions={props.hookExecutions}
      hookBusyId={props.hookBusyId}
      hookFeedback={props.hookFeedback}
      hookWorkspaceId={props.workspace?.id ?? null}
      onRefreshHooks={props.refreshRuntimeHooks}
      onHookToggle={(hookId, enabled) => props.handleUpdateHook(hookId, { enabled })}
      onAddHook={props.handleAddHook}
      onUpdateHook={props.handleUpdateHook}
      onDeleteHook={props.handleDeleteHook}
      onRefreshHookExecutions={props.refreshHookExecutions}
      skills={props.settingsSkills}
      onRefreshSkills={props.refreshSkills}
      onOpenSkillsFolder={props.localPathActionsAvailable ? () => void props.handleOpenAppPath("skills") : undefined}
      onOpenMcpManager={() => props.onOpenSystemTab("mcp")}
      onOpenSkillsManager={() => props.onOpenSystemTab("skills")}
      computerUse={props.computerUseSettings}
      onComputerUseChange={props.setComputerUseSettings}
      onRecheckComputerUse={props.handleRecheckComputerUse}
      workspaceFocus={props.workspaceFocus}
      workspaceFocusBusy={props.workspaceFocusBusy}
      onSaveWorkspaceFocus={props.handleSaveWorkspaceFocus}
      workspaceMemorySummary={props.workspaceSummary}
      workspaceMemoryBusy={props.workspaceMemoryBusy}
      onClearWorkspaceMemory={props.handleClearWorkspaceMemory}
      about={{
        version: "0.1.0",
        runtime: props.hostStatus?.runtimeTransport ?? "mock-browser",
        dataPath: props.workspacePath,
        build: props.hostStatus?.runtimeRunning ? "runtime running" : "runtime idle",
      }}
      onOpenLogs={props.localPathActionsAvailable ? () => void props.handleOpenAppPath("logs") : undefined}
      onOpenDataDirectory={props.localPathActionsAvailable ? () => void props.handleOpenAppPath("data") : undefined}
    />
  );
}
