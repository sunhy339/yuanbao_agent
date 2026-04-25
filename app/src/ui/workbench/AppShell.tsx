import type { ReactNode } from "react";
import { ComposerDock } from "./ComposerDock";
import { DesktopTitlebar } from "./DesktopTitlebar";
import { GlobalSidebar } from "./GlobalSidebar";
import { WorkspaceFrame } from "./WorkspaceFrame";
import { WorkspaceTabs } from "./WorkspaceTabs";
import type { SystemWorkspaceKind, WorkbenchSession, WorkbenchTab } from "./types";

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
  onSubmitPrompt: () => void;
  disabled: boolean;
  providerLabel: string;
  cwdLabel: string;
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
  onSubmitPrompt,
  disabled,
  providerLabel,
  cwdLabel,
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
        />
        <section className="workbench-main" aria-label="Workbench desk">
          <WorkspaceTabs
            tabs={tabs}
            activeTabId={activeTabId}
            onActivateTab={onActivateTab}
            onCloseTab={onCloseTab}
            onCloseOtherTabs={onCloseOtherTabs}
          />
          <WorkspaceFrame composerVisible={composerVisible}>{children}</WorkspaceFrame>
          {composerVisible ? (
            <ComposerDock
              promptValue={promptValue}
              onPromptChange={onPromptChange}
              onSubmitPrompt={onSubmitPrompt}
              disabled={disabled}
              providerLabel={providerLabel}
              cwdLabel={cwdLabel}
            />
          ) : null}
        </section>
      </div>
    </div>
  );
}
