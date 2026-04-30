import type { ReactNode } from "react";
import { ComposerDock } from "./ComposerDock";
import { DesktopTitlebar } from "./DesktopTitlebar";
import { GlobalSidebar } from "./GlobalSidebar";
import { WorkspaceFrame } from "./WorkspaceFrame";
import { WorkspaceTabs } from "./WorkspaceTabs";
import type { SystemWorkspaceKind, WorkbenchSession, WorkbenchTab } from "./types";

function LoadingSkeleton() {
  return (
    <div className="loading-skeleton" aria-label="Loading">
      <div className="skeleton-bar skeleton-bar-wide" />
      <div className="skeleton-bar skeleton-bar-medium" />
      <div className="skeleton-bar skeleton-bar-narrow" />
      <div className="skeleton-bar skeleton-bar-medium" />
    </div>
  );
}

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
  disabled: boolean;
  sending?: boolean;
  providerLabel: string;
  cwdLabel: string;
  loading?: boolean;
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
  disabled,
  sending,
  providerLabel,
  cwdLabel,
  loading,
  children,
}: AppShellProps) {
  return (
    <div className="workbench-shell">
      <DesktopTitlebar />
      <div className="workbench-body">
        <GlobalSidebar
          sessions={sessions}
          activeSessionId={activeSessionId}
          workspaceName={workspaceName}
          onOpenSystemTab={onOpenSystemTab}
          onOpenSessionTab={onOpenSessionTab}
          onRenameSession={onRenameSession}
          onDeleteSession={onDeleteSession}
        />
        <section className="workbench-main" aria-label="Workbench desk">
          <WorkspaceTabs
            tabs={tabs}
            activeTabId={activeTabId}
            onActivateTab={onActivateTab}
            onCloseTab={onCloseTab}
            onCloseOtherTabs={onCloseOtherTabs}
            onRenameSession={onRenameSession}
          />
          <WorkspaceFrame composerVisible={composerVisible}>{loading ? <LoadingSkeleton /> : children}</WorkspaceFrame>
          <ComposerDock
            promptValue={promptValue}
            onPromptChange={onPromptChange}
            onSubmitPrompt={onSubmitPrompt}
            disabled={disabled}
            sending={sending}
            providerLabel={providerLabel}
            cwdLabel={cwdLabel}
            hidden={!composerVisible}
          />
        </section>
      </div>
    </div>
  );
}
