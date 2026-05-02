import { useEffect, useMemo, useRef, useState } from "react";
import { formatStatusLabel } from "../copy";
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
      (session.title || "未命名会话").toLowerCase().includes(query),
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
    if (window.confirm(`删除会话“${session.title || "未命名会话"}”？`)) {
      onDeleteSession(session.id);
    }
  }

  return (
    <aside className="workbench-sidebar" aria-label="全局导航">
      <div className="sidebar-brand">
        <span className="brand-seal" aria-hidden="true">
          Y
        </span>
        <div>
          <strong>Yuanbao Agent</strong>
          <span>{workspaceName}</span>
        </div>
      </div>

      <nav className="sidebar-primary" aria-label="工作台">
        <button type="button" aria-label="Overview" onClick={() => onOpenSystemTab("overview")}>
          <span>总览</span>
          <small>控制中心</small>
        </button>
        <button type="button" aria-label="New Session" onClick={() => onOpenSystemTab("new-session")}>
          <span>新建会话</span>
          <small>开始任务</small>
        </button>
      </nav>

      <section className="sidebar-session-section" aria-label="会话导航">
        <div className="sidebar-section-heading">
          <h2 id="sidebar-sessions-title">会话</h2>
          <span>会话列表</span>
        </div>

        <label className="sidebar-search">
          <span>搜索</span>
          <input
            type="search"
            aria-label="Search sessions"
            placeholder="搜索会话"
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
                  <span className="session-title">{session.title || "未命名会话"}</span>
                  <span className="session-meta">{formatStatusLabel(session.status)}</span>
                </button>
              ),
            )
          ) : sessions.length > 0 ? (
            <p className="sidebar-empty">没有匹配的会话。</p>
          ) : (
            <p className="sidebar-empty">还没有会话。</p>
          )}
        </div>
      </section>

      {contextMenu ? (
        <div
          className="workspace-tab-menu sidebar-context-menu"
          role="menu"
          aria-label={`${contextMenu.session.title || "会话"} 操作`}
          style={{ left: contextMenu.x, top: contextMenu.y }}
        >
          <button
            type="button"
            role="menuitem"
            onClick={() => handleStartRename(contextMenu.session)}
          >
            重命名
          </button>
          <button
            type="button"
            role="menuitem"
            onClick={() => handleConfirmDelete(contextMenu.session)}
          >
            删除会话
          </button>
        </div>
      ) : null}

      <div className="sidebar-footer">
        <button type="button" aria-label="Scheduled" onClick={() => onOpenSystemTab("scheduled")}>
          <span>定时任务</span>
          <small>计划</small>
        </button>
        <button type="button" aria-label="MCP Center" onClick={() => onOpenSystemTab("mcp")}>
          <span>MCP</span>
          <small>工具</small>
        </button>
        <button type="button" aria-label="Agent Skills" onClick={() => onOpenSystemTab("skills")}>
          <span>技能</span>
          <small>智能体</small>
        </button>
        <button type="button" aria-label="Appearance" onClick={() => onOpenSystemTab("appearance")}>
          <span>外观</span>
          <small>主题</small>
        </button>
        <button type="button" aria-label="Component Playground" onClick={() => onOpenSystemTab("playground")}>
          <span>组件预览</span>
          <small>UI 状态</small>
        </button>
        <button type="button" aria-label="Settings" onClick={() => onOpenSystemTab("settings")}>
          <span>设置</span>
          <small>配置</small>
        </button>
      </div>
    </aside>
  );
}
