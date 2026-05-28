import { useEffect, useMemo, useRef, useState } from "react";
import { formatStatusLabel } from "../copy";
import type { SystemWorkspaceKind, WorkbenchSession } from "./types";
import type { SessionWorkspaceContextPreview } from "./workspaces/session/types";

interface GlobalSidebarProps {
  sessions: WorkbenchSession[];
  activeSessionId: string | null;
  workspaceName: string;
  onOpenSystemTab: (kind: SystemWorkspaceKind) => void;
  onOpenSessionTab: (session: WorkbenchSession) => void;
  onRenameSession: (sessionId: string, newTitle: string) => void;
  onDeleteSession: (sessionId: string) => void;
  contextPreview?: SessionWorkspaceContextPreview | null;
  worktreeStatus?: { dirtyFiles?: number; files?: string[] } | null;
  activeTaskStatus?: string | null;
  activeTaskCurrentStep?: string | null;
}

interface ContextMenuState {
  session: WorkbenchSession;
  x: number;
  y: number;
}

function formatSessionRailMeta(session: WorkbenchSession, activeSessionId: string | null) {
  if (session.id === activeSessionId) {
    return "当前";
  }
  if (session.status === "failed") {
    return "失败";
  }
  if (session.status === "archived") {
    return "归档";
  }
  if (session.updatedAt) {
    return new Date(session.updatedAt).toLocaleString("zh-CN", {
      month: "numeric",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    });
  }
  return formatStatusLabel(session.status);
}

export function GlobalSidebar({
  sessions,
  activeSessionId,
  workspaceName,
  onOpenSystemTab,
  onOpenSessionTab,
  onRenameSession,
  onDeleteSession,
  contextPreview,
  worktreeStatus,
  activeTaskStatus,
  activeTaskCurrentStep,
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

  const budgetStats = contextPreview?.budgetStats;
  const contextPressure = useMemo(() => {
    if (!budgetStats?.maxContextTokens || !budgetStats?.estimatedTokens) return null;
    const ratio = budgetStats.estimatedTokens / budgetStats.maxContextTokens;
    return Math.min(1, Math.max(0, ratio));
  }, [budgetStats]);

  const contextPressureLabel = useMemo(() => {
    if (!budgetStats) return null;
    const used = budgetStats.estimatedTokens ?? budgetStats.estimatedInputTokens ?? 0;
    const max = budgetStats.maxContextTokens;
    if (!max) return `${used} tokens`;
    return `${Math.round(used / 1000)}k / ${Math.round(max / 1000)}k`;
  }, [budgetStats]);

  const worktreeDirtyCount = worktreeStatus?.dirtyFiles ?? 0;

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
        <button type="button" aria-label="新建会话" onClick={() => onOpenSystemTab("new-session")}>
          <span>新建会话</span>
          <small>开始任务</small>
        </button>
        <button type="button" aria-label="设置" onClick={() => onOpenSystemTab("settings")}>
          <span>设置</span>
          <small>模型与偏好</small>
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
            aria-label="搜索会话"
            placeholder="搜索会话"
            value={searchText}
            onChange={(event) => setSearchText(event.target.value)}
          />
        </label>

        <div className="session-rail" aria-label="会话">
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
                  <span className="session-meta">{formatSessionRailMeta(session, activeSessionId)}</span>
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

      <div className="sidebar-data-panels" aria-label="状态面板">
        {activeTaskStatus && activeTaskStatus !== "completed" && activeTaskStatus !== "cancelled" && (
          <div className="sidebar-task-panel">
            <div className="sidebar-task-heading">
              <span className="sidebar-task-dot" data-status={activeTaskStatus} />
              <span className="sidebar-task-label">{formatStatusLabel(activeTaskStatus)}</span>
            </div>
            {activeTaskCurrentStep ? (
              <p className="sidebar-task-step" title={activeTaskCurrentStep}>
                {activeTaskCurrentStep}
              </p>
            ) : null}
          </div>
        )}

        {contextPressure !== null && (
          <div className="sidebar-context-panel">
            <div className="sidebar-context-header">
              <span>上下文</span>
              <span className="sidebar-context-value">{contextPressureLabel}</span>
            </div>
            <div className="sidebar-context-bar-bg" aria-hidden="true">
              <div
                className="sidebar-context-bar-fill"
                data-pressure={contextPressure > 0.85 ? "high" : contextPressure > 0.6 ? "medium" : "low"}
                style={{ width: `${contextPressure * 100}%` }}
              />
            </div>
          </div>
        )}

        {worktreeDirtyCount > 0 && (
          <div className="sidebar-worktree-panel" data-dirty="true">
            <span className="sidebar-worktree-icon" aria-hidden="true">✦</span>
            <span className="sidebar-worktree-label">工作区更改</span>
            <span className="sidebar-worktree-count">{worktreeDirtyCount}</span>
          </div>
        )}
      </div>
    </aside>
  );
}
