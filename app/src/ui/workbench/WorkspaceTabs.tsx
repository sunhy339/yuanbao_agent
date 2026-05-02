import { useEffect, useRef, useState } from "react";
import type { WorkbenchTab } from "./types";

interface WorkspaceTabsProps {
  tabs: WorkbenchTab[];
  activeTabId: WorkbenchTab["id"];
  onActivateTab: (tabId: WorkbenchTab["id"]) => void;
  onCloseTab: (tabId: WorkbenchTab["id"]) => void;
  onCloseOtherTabs?: (tabId: WorkbenchTab["id"]) => void;
  onRenameSession?: (sessionId: string, newTitle: string) => void;
}

interface TabContextMenuState {
  tab: WorkbenchTab;
  x: number;
  y: number;
}

function getLegacyTabA11yName(title: string) {
  if (title === "总览") return "Overview";
  if (title === "新建会话") return "New Session";
  if (title === "定时任务") return "Scheduled";
  if (title === "MCP 中心") return "MCP Center";
  if (title === "技能") return "Agent Skills";
  if (title === "外观") return "Appearance";
  if (title === "组件预览") return "Playground";
  if (title === "设置") return "Settings";
  return title;
}

export function WorkspaceTabs({
  tabs,
  activeTabId,
  onActivateTab,
  onCloseTab,
  onCloseOtherTabs,
  onRenameSession,
}: WorkspaceTabsProps) {
  const [contextMenu, setContextMenu] = useState<TabContextMenuState | null>(null);
  const [renamingTabId, setRenamingTabId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const renameInputRef = useRef<HTMLInputElement>(null);
  const otherClosableCount = contextMenu
    ? tabs.filter((tab) => tab.id !== contextMenu.tab.id && tab.closable).length
    : 0;

  useEffect(() => {
    if (!contextMenu) return;
    function handleClick() {
      setContextMenu(null);
    }
    function handleKey(event: KeyboardEvent) {
      if (event.key === "Escape") setContextMenu(null);
    }
    document.addEventListener("click", handleClick);
    document.addEventListener("keydown", handleKey);
    return () => {
      document.removeEventListener("click", handleClick);
      document.removeEventListener("keydown", handleKey);
    };
  }, [contextMenu]);

  useEffect(() => {
    if (renamingTabId && renameInputRef.current) {
      renameInputRef.current.focus();
      renameInputRef.current.select();
    }
  }, [renamingTabId]);

  function handleStartRename() {
    if (!contextMenu?.tab.id.startsWith("session:") || !onRenameSession) return;
    setContextMenu(null);
    setRenamingTabId(contextMenu.tab.id);
    setRenameValue(contextMenu.tab.title);
  }

  function handleCommitRename() {
    if (renamingTabId && renameValue.trim() && onRenameSession) {
      const sessionId = renamingTabId.slice("session:".length);
      onRenameSession(sessionId, renameValue.trim());
    }
    setRenamingTabId(null);
  }

  const contextMenuIsSession = contextMenu?.tab.id.startsWith("session:");

  return (
    <>
      <div className="workspace-tabs" role="tablist" aria-label="已打开工作区">
        {tabs.map((tab) => (
          <div
            key={tab.id}
            className="workspace-tab-wrap"
            data-active={tab.id === activeTabId}
            onContextMenu={(event) => {
              event.preventDefault();
              setContextMenu({
                tab,
                x: event.clientX,
                y: event.clientY,
              });
            }}
          >
            <button
              type="button"
              role="tab"
              aria-label={getLegacyTabA11yName(tab.title)}
              aria-selected={tab.id === activeTabId}
              aria-controls="workspace-frame"
              className="workspace-tab"
              onClick={() => onActivateTab(tab.id)}
            >
              {renamingTabId === tab.id ? (
                <input
                  ref={renameInputRef}
                  type="text"
                  className="tab-rename-input"
                  value={renameValue}
                  onChange={(e) => setRenameValue(e.target.value)}
                  onBlur={handleCommitRename}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") handleCommitRename();
                    if (e.key === "Escape") setRenamingTabId(null);
                    e.stopPropagation();
                  }}
                  onClick={(e) => e.stopPropagation()}
                />
              ) : (
                tab.title
              )}
            </button>
            {tab.closable ? (
              <button
                type="button"
                className="workspace-tab-close"
                aria-label={`Close ${getLegacyTabA11yName(tab.title)}`}
                onClick={() => onCloseTab(tab.id)}
              >
                x
              </button>
            ) : null}
          </div>
        ))}
      </div>
      {contextMenu ? (
        <div
          className="workspace-tab-menu"
          role="menu"
          aria-label={`${contextMenu.tab.title} 标签操作`}
          style={{ left: contextMenu.x, top: contextMenu.y }}
        >
          {contextMenuIsSession && onRenameSession ? (
            <button
              type="button"
              role="menuitem"
              onClick={() => {
                handleStartRename();
                setContextMenu(null);
              }}
            >
              重命名
            </button>
          ) : null}
          <button
            type="button"
            role="menuitem"
            aria-label="Close tab"
            disabled={!contextMenu.tab.closable}
            onClick={() => {
              onCloseTab(contextMenu.tab.id);
              setContextMenu(null);
            }}
          >
            关闭标签
          </button>
          <button
            type="button"
            role="menuitem"
            aria-label="Close other tabs"
            disabled={!otherClosableCount || !onCloseOtherTabs}
            onClick={() => {
              onCloseOtherTabs?.(contextMenu.tab.id);
              setContextMenu(null);
            }}
          >
            关闭其它标签
          </button>
        </div>
      ) : null}
    </>
  );
}
