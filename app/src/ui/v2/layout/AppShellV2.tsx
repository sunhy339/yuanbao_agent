import type { ReactNode } from "react";
import { ComposerDock } from "../../workbench/ComposerDock";
import { DesktopTitlebar } from "../../workbench/DesktopTitlebar";
import { GlobalSidebar } from "../../workbench/GlobalSidebar";
import { WorkspaceFrame } from "../../workbench/WorkspaceFrame";
import { WorkspaceTabs } from "../../workbench/WorkspaceTabs";
import type { SystemWorkspaceKind, WorkbenchSession, WorkbenchTab } from "../../workbench/types";
import { formatSystemWorkspaceLabel } from "../../copy";
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
  onSubmitPrompt: () => void;
  disabled: boolean;
  sending?: boolean;
  providerLabel: string;
  cwdLabel: string;
  runtimeLabel?: string;
  mcpLabel?: string;
  approvalLabel?: string;
  contextLabel?: string;
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
  disabled,
  sending,
  providerLabel,
  cwdLabel,
  runtimeLabel,
  mcpLabel,
  approvalLabel,
  contextLabel,
  loading,
  children,
}: AppShellV2Props) {
  const activeSystemTab = activeTabId.startsWith("system:") ? activeTabId.slice("system:".length) : "session";
  const activeTaskSessions = sessions.filter((session) => session.status === "active").length;

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
        />
        <section className="yb-app-main" aria-label="工作台">
          <header className="yb-topbar" aria-label="运行时状态">
            <div className="yb-topbar-title">
              <p className="yb-kicker">Yuanbao 工作台 V2</p>
              <h1>{workspaceName}</h1>
            </div>
            <div className="yb-topbar-status">
              <StatusBadge label={providerLabel} tone={disabled ? "info" : "success"} pulse={!disabled} />
              <StatusBadge label={runtimeLabel ?? `${activeTaskSessions} 个活跃任务`} tone={disabled ? "danger" : "success"} pulse={!disabled} />
              <StatusBadge label={mcpLabel ?? "MCP"} tone="info" />
              <StatusBadge label={approvalLabel ?? "审批"} tone="warning" />
              <StatusBadge label={contextLabel ?? "上下文"} tone="primary" />
              <StatusBadge label={formatSystemWorkspaceLabel(activeSystemTab)} tone="primary" />
            </div>
            <div className="yb-topbar-actions">
              <Button variant="ghost" size="sm" aria-label="打开 MCP 中心" onClick={() => onOpenSystemTab("mcp")}>MCP</Button>
              <Button variant="ghost" size="sm" aria-label="打开智能体技能" onClick={() => onOpenSystemTab("skills")}>技能</Button>
              <Button variant="ghost" size="sm" aria-label="打开外观" onClick={() => onOpenSystemTab("appearance")}>外观</Button>
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
