import type { ReactNode } from "react";
import type { ComposerRuntimeChildTask } from "../../workbench/ComposerDock";
import { HahaComposer } from "../../haha/HahaComposer";
import { DesktopTitlebar } from "../../workbench/DesktopTitlebar";
import { GlobalSidebar } from "../../workbench/GlobalSidebar";
import { WorkspaceFrame } from "../../workbench/WorkspaceFrame";
import { WorkspaceTabs } from "../../workbench/WorkspaceTabs";
import type { SystemWorkspaceKind, WorkbenchSession, WorkbenchTab } from "../../workbench/types";
import type { SessionWorkspaceContextPreview } from "../../workbench/workspaces/session/types";
import type { QueuedPromptSubmission } from "../../../state/eventRecordViews";
import type { CleanSessionLaunchOptions } from "../../haha-clean/composer/CleanComposer";
import { Button, StatusBadge } from "../components/ui";
import "./app-shell-v2.css";

function LoadingSkeletonV2() {
  return (
    <div className="yb-shell-loading" aria-label="正在加载">
      <span />
      <span />
      <span />
      <span />
    </div>
  );
}

export interface AppShellV2Props {
  tabs: WorkbenchTab[];
  activeTabId: WorkbenchTab["id"];
  sessions: WorkbenchSession[];
  activeSessionId: string | null;
  workspaceName: string;
  composerVisible: boolean;
  promptValue: string;
  onPromptChange: (value: string) => void;
  onOpenSystemTab: (kind: SystemWorkspaceKind) => void;
  onOpenSessionTab: (session: WorkbenchSession) => void;
  onActivateTab: (tabId: WorkbenchTab["id"]) => void;
  onCloseTab: (tabId: WorkbenchTab["id"]) => void;
  onCloseOtherTabs: (tabId: WorkbenchTab["id"]) => void;
  onRenameSession: (sessionId: string, newTitle: string) => void;
  onDeleteSession: (sessionId: string) => void;
  onSubmitPrompt: (options?: CleanSessionLaunchOptions) => void;
  onQueuePrompt?: (mode?: "queued" | "supplement") => void;
  onStopPrompt?: () => void;
  queuedPrompts?: QueuedPromptSubmission[];
  onGuideQueuedPrompt?: (id: string) => void;
  onQueuedPromptRemove?: (id: string) => void;
  onQueuedPromptMove?: (id: string, direction: "up" | "down") => void;
  disabled: boolean;
  sending?: boolean;
  submitting?: boolean;
  stopPending?: boolean;
  queuedPromptCount?: number;
  attachments?: string[];
  onAttachmentsChange?: (attachments: string[]) => void;
  onAttachmentError?: (message: string) => void;
  modelOptions?: Array<{ id: string; label: string; subtitle?: string }>;
  selectedModelId?: string;
  onSelectModel?: (modelId: string) => void;
  runtimeChildTasks?: ComposerRuntimeChildTask[];
  providerLabel: string;
  cwdLabel: string;
  permissionLabel?: string;
  permissionMode?: string;
  onPermissionModeChange?: (mode: string) => void;
  onWorkspacePathChange?: (path: string) => void;
  useWorktree?: boolean;
  onUseWorktreeChange?: (enabled: boolean) => void | Promise<void>;
  worktreeModeBusy?: boolean;
  runtimeLabel?: string;
  mcpLabel?: string;
  approvalLabel?: string;
  contextLabel?: string;
  contextPreview?: SessionWorkspaceContextPreview | null;
  worktreeStatus?: { dirtyFiles?: number; files?: string[] } | null;
  fileWorkspaceChangedFiles?: Array<{
    path: string;
    status?: string;
    additions?: number;
    deletions?: number;
    source?: string;
  }>;
  activeTaskStatus?: string | null;
  activeTaskCurrentStep?: string | null;
  loading?: boolean;
  children: ReactNode;
}

export function AppShellV2({
  tabs,
  activeTabId,
  sessions,
  activeSessionId,
  workspaceName,
  composerVisible,
  promptValue,
  onPromptChange,
  onOpenSystemTab,
  onOpenSessionTab,
  onActivateTab,
  onCloseTab,
  onCloseOtherTabs,
  onRenameSession,
  onDeleteSession,
  onSubmitPrompt,
  onQueuePrompt,
  onStopPrompt,
  queuedPrompts,
  onGuideQueuedPrompt,
  onQueuedPromptRemove,
  onQueuedPromptMove,
  disabled,
  sending,
  submitting,
  stopPending,
  queuedPromptCount,
  attachments,
  onAttachmentsChange,
  onAttachmentError,
  modelOptions,
  selectedModelId,
  onSelectModel,
  runtimeChildTasks,
  providerLabel,
  cwdLabel,
  permissionLabel,
  permissionMode,
  onPermissionModeChange,
  runtimeLabel,
  approvalLabel,
  contextLabel,
  contextPreview,
  worktreeStatus,
  activeTaskStatus,
  activeTaskCurrentStep,
  loading,
  children,
}: AppShellV2Props) {
  const activeTab = tabs.find((tab) => tab.id === activeTabId);
  const activeTabKind =
    activeTab?.kind ??
    (activeTabId.startsWith("session:")
      ? "session"
      : activeTabId.startsWith("system:")
        ? activeTabId.slice("system:".length)
        : "session");
  const activeTaskSessions = sessions.filter((session) => session.status === "active").length;
  const showApprovalPill = Boolean(approvalLabel && !/^0\s*个?审批/.test(approvalLabel));

  return (
    <div className="yb-app-shell">
      <DesktopTitlebar />
      <div className="yb-app-body">
        <GlobalSidebar
          sessions={sessions}
          activeSessionId={activeSessionId}
          workspaceName={workspaceName}
          onOpenSystemTab={onOpenSystemTab}
          onOpenSessionTab={onOpenSessionTab}
          onRenameSession={onRenameSession}
          onDeleteSession={onDeleteSession}
          contextPreview={contextPreview}
          worktreeStatus={worktreeStatus}
          activeTaskStatus={activeTaskStatus}
          activeTaskCurrentStep={activeTaskCurrentStep}
        />
        <section className="yb-app-main" aria-label="工作台">
          <header className="yb-topbar" aria-label="运行时状态">
            <div className="yb-topbar-title">
              <p className="yb-kicker">Yuanbao 工作台 V2</p>
              <h1>{workspaceName}</h1>
            </div>
            <div className="yb-topbar-status">
              <StatusBadge compact label={providerLabel} tone={disabled ? "info" : "success"} pulse={!disabled} />
              <StatusBadge compact label={runtimeLabel ?? `${activeTaskSessions} 个活跃任务`} tone={disabled ? "danger" : "success"} pulse={!disabled} />
              {showApprovalPill ? <span className="yb-topbar-pill" title={approvalLabel}>{approvalLabel}</span> : null}
              {contextLabel ? <span className="yb-topbar-pill" title={contextLabel}>上下文</span> : null}
            </div>
            <div className="yb-topbar-actions">
              <Button variant="ghost" size="sm" aria-label="打开 MCP 中心" onClick={() => onOpenSystemTab("mcp")}>MCP</Button>
              <Button variant="ghost" size="sm" aria-label="打开智能体技能" onClick={() => onOpenSystemTab("skills")}>技能</Button>
              <Button variant="ghost" size="sm" aria-label="打开设置" onClick={() => onOpenSystemTab("settings")}>设置</Button>
            </div>
          </header>

          <div className="yb-workspace-chrome">
            <WorkspaceTabs
              tabs={tabs}
              activeTabId={activeTabId}
              onActivateTab={onActivateTab}
              onCloseTab={onCloseTab}
              onCloseOtherTabs={onCloseOtherTabs}
              onRenameSession={onRenameSession}
            />
            <WorkspaceFrame composerVisible={composerVisible}>
              {loading ? <LoadingSkeletonV2 /> : children}
            </WorkspaceFrame>
          </div>

          <HahaComposer
            promptValue={promptValue}
            onPromptChange={onPromptChange}
            onSubmitPrompt={onSubmitPrompt}
            onQueuePrompt={onQueuePrompt}
            onStopPrompt={onStopPrompt}
            queuedPrompts={queuedPrompts}
            onGuideQueuedPrompt={onGuideQueuedPrompt}
            onQueuedPromptRemove={onQueuedPromptRemove}
            onQueuedPromptMove={onQueuedPromptMove}
            disabled={disabled}
            sending={sending}
            submitting={submitting}
            stopPending={stopPending}
            queuedPromptCount={queuedPromptCount}
            attachments={attachments}
            onAttachmentsChange={onAttachmentsChange}
            onAttachmentError={onAttachmentError}
            modelOptions={modelOptions}
            selectedModelId={selectedModelId}
            onSelectModel={onSelectModel}
            runtimeChildTasks={runtimeChildTasks}
            providerLabel={providerLabel}
            cwdLabel={cwdLabel}
            contextLabel={contextLabel}
            contextPreview={contextPreview}
            permissionLabel={permissionLabel}
            permissionMode={permissionMode}
            onPermissionModeChange={onPermissionModeChange}
            hidden={!composerVisible}
            layout={activeTabKind === "session" ? "session" : "default"}
          />
        </section>
      </div>
    </div>
  );
}
