import { useEffect, useMemo, useRef, useState } from "react";
import type { SystemWorkspaceKind, WorkbenchSession } from "./types";

interface GlobalSidebarProps {
  sessions: WorkbenchSession[];
  activeSessionId: string | null;
  workspaceName: string;
  onOpenSystemTab: (kind: SystemWorkspaceKind) => void;
  onOpenSessionTab: (session: WorkbenchSession) => void;
  onRenameSession: (sessionId: string, newTitle: string) => void;
  onDeleteSession: (sessionId: string) => void;
}

interface ContextMenuState {
  session: WorkbenchSession;
  x: number;
  y: number;
}

export function GlobalSidebar({
  sessions,
  activeSessionId,
  workspaceName,
  onOpenSystemTab,
  onOpenSessionTab,
  onRenameSession,
  onDeleteSession,
}: GlobalSidebarProps) {
  const [searchText, setSearchText] = useState("");
  const [contextMenu, setContextMenu] = useState<ContextMenuState | null>(null);
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const renameInputRef = useRef<HTMLInputElement>(null);

  const filtered = useMemo(() => {
    const query = searchText.trim().toLowerCase();
    if (!query) return sessions;
    return sessions.filter((session) =>
      (session.title || "Untitled Session").toLowerCase().includes(query),
    );
  }, [sessions, searchText]);

  useEffect(() => {
    if (!contextMenu) return;
    function handleClick() { setContextMenu(null); }
    function handleKey(event: KeyboardEvent) { if (event.key === "Escape") setContextMenu(null); }
    document.addEventListener("click", handleClick);
    document.addEventListener("keydown", handleKey);
    return () => {
      document.removeEventListener("click", handleClick);
      document.removeEventListener("keydown", handleKey);
    };
  }, [contextMenu]);

  useEffect(() => {
    if (renamingId && renameInputRef.current) {
      renameInputRef.current.focus();
      renameInputRef.current.select();
    }
  }, [renamingId]);

  function handleStartRename(session: WorkbenchSession) {
    setContextMenu(null);
    setRenamingId(session.id);
    setRenameValue(session.title || "");
  }

  function handleCommitRename() {
    if (renamingId && renameValue.trim()) {
      onRenameSession(renamingId, renameValue.trim());
    }
    setRenamingId(null);
  }

  function handleConfirmDelete(session: WorkbenchSession) {
    setContextMenu(null);
    if (window.confirm(`Delete session "${session.title || "Untitled Session"}"?`)) {
      onDeleteSession(session.id);
    }
  }

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
        <button type="button" aria-label="Overview" onClick={() => onOpenSystemTab("overview")}>
          <span>Overview</span>
          <small>Command center</small>
        </button>
        <button type="button" aria-label="New Session" onClick={() => onOpenSystemTab("new-session")}>
          <span>New Session</span>
          <small>New session</small>
        </button>
      </nav>

      <section className="sidebar-session-section" aria-label="Session navigation">
        <div className="sidebar-section-heading">
          <h2 id="sidebar-sessions-title">Sessions</h2>
          <span>Sessions</span>
        </div>

        <label className="sidebar-search">
          <span>Search</span>
          <input
            type="search"
            aria-label="Search sessions"
            placeholder="Search sessions"
            value={searchText}
            onChange={(event) => setSearchText(event.target.value)}
          />
        </label>

        <div className="session-rail" aria-label="Sessions">
          {filtered.length ? (
            filtered.map((session) =>
              renamingId === session.id ? (
                <div key={session.id} className="session-rail-item session-rail-rename">
                  <input
                    ref={renameInputRef}
                    type="text"
                    className="session-rename-input"
                    value={renameValue}
                    onChange={(e) => setRenameValue(e.target.value)}
                    onBlur={handleCommitRename}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") handleCommitRename();
                      if (e.key === "Escape") setRenamingId(null);
                    }}
                  />
                </div>
              ) : (
                <button
                  key={session.id}
                  type="button"
                  className="session-rail-item"
                  data-active={session.id === activeSessionId}
                  onClick={() => onOpenSessionTab(session)}
                  onContextMenu={(e) => {
                    e.preventDefault();
                    setContextMenu({ session, x: e.clientX, y: e.clientY });
                  }}
                >
                  <span className="session-dot" data-status={session.status} aria-hidden="true" />
                  <span className="session-title">{session.title || "Untitled Session"}</span>
                  <span className="session-meta">{session.status}</span>
                </button>
              ),
            )
          ) : sessions.length > 0 ? (
            <p className="sidebar-empty">No matching sessions.</p>
          ) : (
            <p className="sidebar-empty">No sessions yet.</p>
          )}
        </div>
      </section>

      {contextMenu ? (
        <div
          className="workspace-tab-menu sidebar-context-menu"
          role="menu"
          aria-label={`${contextMenu.session.title || "Session"} actions`}
          style={{ left: contextMenu.x, top: contextMenu.y }}
        >
          <button
            type="button"
            role="menuitem"
            onClick={() => handleStartRename(contextMenu.session)}
          >
            Rename
          </button>
          <button
            type="button"
            role="menuitem"
            onClick={() => handleConfirmDelete(contextMenu.session)}
          >
            Delete session
          </button>
        </div>
      ) : null}

      <div className="sidebar-footer">
        <button type="button" aria-label="Scheduled" onClick={() => onOpenSystemTab("scheduled")}>
          <span>Scheduled</span>
          <small>Scheduled</small>
        </button>
        <button type="button" aria-label="MCP Center" onClick={() => onOpenSystemTab("mcp")}>
          <span>MCP</span>
          <small>Tools</small>
        </button>
        <button type="button" aria-label="Agent Skills" onClick={() => onOpenSystemTab("skills")}>
          <span>Skills</span>
          <small>Agents</small>
        </button>
        <button type="button" aria-label="Appearance" onClick={() => onOpenSystemTab("appearance")}>
          <span>Appearance</span>
          <small>Theme</small>
        </button>
        <button type="button" aria-label="Component Playground" onClick={() => onOpenSystemTab("playground")}>
          <span>Playground</span>
          <small>UI states</small>
        </button>
        <button type="button" aria-label="Settings" onClick={() => onOpenSystemTab("settings")}>
          <span>Settings</span>
          <small>Settings</small>
        </button>
      </div>
    </aside>
  );
}
