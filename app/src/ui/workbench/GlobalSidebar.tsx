import type { SystemWorkspaceKind, WorkbenchSession } from "./types";

interface GlobalSidebarProps {
  sessions: WorkbenchSession[];
  activeSessionId: string | null;
  workspaceName: string;
  onOpenSystemTab: (kind: SystemWorkspaceKind) => void;
  onOpenSessionTab: (session: WorkbenchSession) => void;
}

export function GlobalSidebar({
  sessions,
  activeSessionId,
  workspaceName,
  onOpenSystemTab,
  onOpenSessionTab,
}: GlobalSidebarProps) {
  return (
    <aside className="workbench-sidebar" aria-label="Global navigation">
      <div className="sidebar-brand">
        <span className="brand-seal" aria-hidden="true">
          Y
        </span>
        <div>
          <strong>Yuanbao Agent</strong>
          <span>{workspaceName}</span>
        </div>
      </div>

      <nav className="sidebar-primary" aria-label="Workbench">
        <button type="button" aria-label="New Session" onClick={() => onOpenSystemTab("new-session")}>
          <span>新会话</span>
          <small>New session</small>
        </button>
      </nav>

      <section className="sidebar-session-section" aria-labelledby="sidebar-sessions-title">
        <div className="sidebar-section-heading">
          <h2 id="sidebar-sessions-title">会话</h2>
          <span>Sessions</span>
        </div>

        <label className="sidebar-search">
          <span>搜索</span>
          <input type="search" aria-label="Search sessions" placeholder="Search sessions" />
        </label>

        <div className="session-rail" aria-label="Sessions">
          {sessions.length ? (
            sessions.map((session) => (
              <button
                key={session.id}
                type="button"
                className="session-rail-item"
                data-active={session.id === activeSessionId}
                onClick={() => onOpenSessionTab(session)}
              >
                <span className="session-dot" aria-hidden="true" />
                <span className="session-title">{session.title || "Untitled Session"}</span>
                <span className="session-meta">{session.status}</span>
              </button>
            ))
          ) : (
            <p className="sidebar-empty">暂无会话记录。</p>
          )}
        </div>
      </section>

      <div className="sidebar-footer">
        <button type="button" aria-label="Scheduled" onClick={() => onOpenSystemTab("scheduled")}>
          <span>调度</span>
          <small>Scheduled</small>
        </button>
        <button type="button" aria-label="Settings" onClick={() => onOpenSystemTab("settings")}>
          <span>设置</span>
          <small>Settings</small>
        </button>
      </div>
    </aside>
  );
}
