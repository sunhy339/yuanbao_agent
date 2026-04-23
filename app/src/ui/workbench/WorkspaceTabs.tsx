import type { WorkbenchTab } from "./types";

interface WorkspaceTabsProps {
  tabs: WorkbenchTab[];
  activeTabId: WorkbenchTab["id"];
  onActivateTab: (tabId: WorkbenchTab["id"]) => void;
  onCloseTab: (tabId: WorkbenchTab["id"]) => void;
}

export function WorkspaceTabs({ tabs, activeTabId, onActivateTab, onCloseTab }: WorkspaceTabsProps) {
  return (
    <div className="workspace-tabs" role="tablist" aria-label="Open workspaces">
      {tabs.map((tab) => (
        <div key={tab.id} className="workspace-tab-wrap" data-active={tab.id === activeTabId}>
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
  );
}
