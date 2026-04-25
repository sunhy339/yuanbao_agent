import { useState } from "react";
import type { WorkbenchTab } from "./types";

interface WorkspaceTabsProps {
  tabs: WorkbenchTab[];
  activeTabId: WorkbenchTab["id"];
  onActivateTab: (tabId: WorkbenchTab["id"]) => void;
  onCloseTab: (tabId: WorkbenchTab["id"]) => void;
  onCloseOtherTabs?: (tabId: WorkbenchTab["id"]) => void;
}

interface TabContextMenuState {
  tab: WorkbenchTab;
  x: number;
  y: number;
}

export function WorkspaceTabs({
  tabs,
  activeTabId,
  onActivateTab,
  onCloseTab,
  onCloseOtherTabs,
}: WorkspaceTabsProps) {
  const [contextMenu, setContextMenu] = useState<TabContextMenuState | null>(null);
  const otherClosableCount = contextMenu
    ? tabs.filter((tab) => tab.id !== contextMenu.tab.id && tab.closable).length
    : 0;

  function closeContextMenu() {
    setContextMenu(null);
  }

  return (
    <>
      <div className="workspace-tabs" role="tablist" aria-label="Open workspaces" onClick={closeContextMenu}>
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
              aria-selected={tab.id === activeTabId}
              aria-controls="workspace-frame"
              className="workspace-tab"
              onClick={() => onActivateTab(tab.id)}
            >
              {tab.title}
            </button>
            {tab.closable ? (
              <button
                type="button"
                className="workspace-tab-close"
                aria-label={`Close ${tab.title}`}
                onClick={() => onCloseTab(tab.id)}
              >
                {"\u00d7"}
              </button>
            ) : null}
          </div>
        ))}
      </div>
      {contextMenu ? (
        <div
          className="workspace-tab-menu"
          role="menu"
          aria-label={`${contextMenu.tab.title} tab actions`}
          style={{ left: contextMenu.x, top: contextMenu.y }}
        >
          <button
            type="button"
            role="menuitem"
            disabled={!contextMenu.tab.closable}
            onClick={() => {
              onCloseTab(contextMenu.tab.id);
              closeContextMenu();
            }}
          >
            关闭此对话
          </button>
          <button
            type="button"
            role="menuitem"
            disabled={!otherClosableCount || !onCloseOtherTabs}
            onClick={() => {
              onCloseOtherTabs?.(contextMenu.tab.id);
              closeContextMenu();
            }}
          >
            关闭其他对话
          </button>
        </div>
      ) : null}
    </>
  );
}
