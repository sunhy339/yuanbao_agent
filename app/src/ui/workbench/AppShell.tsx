import type { ReactNode } from "react";
import { CleanAppShell } from "../haha-clean/shell/CleanAppShell";
import {
  ThemeProvider,
  type AccentColor,
  type DensityMode,
  type MotionMode,
  type RadiusMode,
  type ThemeMode,
} from "../v2/theme/ThemeProvider";
import type { SystemWorkspaceKind, WorkbenchSession, WorkbenchTab } from "./types";
import type { ComposerRuntimeChildTask } from "./ComposerDock";
import type { QueuedPromptSubmission } from "../../state/eventRecordViews";

interface AppShellProps {
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
  onSubmitPrompt: () => void;
  onQueuePrompt?: (mode?: "queued" | "supplement") => void;
  onStopPrompt?: () => void;
  queuedPrompts?: QueuedPromptSubmission[];
  onGuideQueuedPrompt?: (id: string) => void;
  onQueuedPromptRemove?: (id: string) => void;
  onQueuedPromptMove?: (id: string, direction: "up" | "down") => void;
  disabled: boolean;
  sending?: boolean;
  submitting?: boolean;
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
  runtimeLabel?: string;
  mcpLabel?: string;
  approvalLabel?: string;
  contextLabel?: string;
  contextPreview?: import("../workbench/workspaces/session/types").SessionWorkspaceContextPreview | null;
  worktreeStatus?: { dirtyFiles?: number; files?: string[] } | null;
  activeTaskStatus?: string | null;
  activeTaskCurrentStep?: string | null;
  loading?: boolean;
  theme?: ThemeMode;
  density?: DensityMode;
  radius?: RadiusMode;
  motion?: MotionMode;
  accentColor?: AccentColor;
  transparency?: number;
  fontScale?: number;
  children: ReactNode;
}

export function AppShell({
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
  mcpLabel,
  approvalLabel,
  contextLabel,
  contextPreview,
  worktreeStatus,
  activeTaskStatus,
  activeTaskCurrentStep,
  loading,
  theme = "dark",
  density = "comfortable",
  radius = "md",
  motion = "subtle",
  accentColor = "cyan",
  transparency,
  fontScale,
  children,
}: AppShellProps) {
  return (
    <ThemeProvider
      theme={theme}
      density={density}
      radius={radius}
      motion={motion}
      accentColor={accentColor}
      transparency={transparency}
      fontScale={fontScale}
    >
      <CleanAppShell
        tabs={tabs}
        activeTabId={activeTabId}
        sessions={sessions}
        activeSessionId={activeSessionId}
        workspaceName={workspaceName}
        composerVisible={composerVisible}
        promptValue={promptValue}
        onPromptChange={onPromptChange}
        onOpenSystemTab={onOpenSystemTab}
        onOpenSessionTab={onOpenSessionTab}
        onActivateTab={onActivateTab}
        onCloseTab={onCloseTab}
        onCloseOtherTabs={onCloseOtherTabs}
        onRenameSession={onRenameSession}
        onDeleteSession={onDeleteSession}
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
        permissionLabel={permissionLabel}
        permissionMode={permissionMode}
        onPermissionModeChange={onPermissionModeChange}
        loading={loading}
        runtimeLabel={runtimeLabel}
        mcpLabel={mcpLabel}
        approvalLabel={approvalLabel}
        contextLabel={contextLabel}
        contextPreview={contextPreview}
        worktreeStatus={worktreeStatus}
        activeTaskStatus={activeTaskStatus}
        activeTaskCurrentStep={activeTaskCurrentStep}
      >
        {children}
      </CleanAppShell>
    </ThemeProvider>
  );
}
