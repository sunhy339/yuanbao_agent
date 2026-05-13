import type { WorkbenchTab, SystemWorkspaceKind, WorkbenchSession } from "../ui/workbench/types";
import {
  closeOtherTabs,
  closeTab,
  openSessionTab,
  openSystemTab,
} from "../ui/workbench/tabModel";
import type { SessionRecord } from "@shared";

export interface UseTabActionsDeps {
  activeTabId: WorkbenchTab["id"];
  openTabs: WorkbenchTab[];
  setOpenTabs: React.Dispatch<React.SetStateAction<WorkbenchTab[]>>;
  setActiveTabId: React.Dispatch<React.SetStateAction<WorkbenchTab["id"]>>;
  sessions: SessionRecord[];
  selectSession: (session: SessionRecord | null) => void;
}

export function useTabActions(deps: UseTabActionsDeps) {
  const { activeTabId, setOpenTabs, setActiveTabId, sessions, selectSession } = deps;

  function handleOpenSystemTab(kind: SystemWorkspaceKind) {
    setOpenTabs((current) => {
      const result = openSystemTab(current, kind);
      setActiveTabId(result.activeTabId);
      return result.tabs;
    });
  }

  function handleOpenSessionTab(nextSession: WorkbenchSession) {
    setOpenTabs((current) => {
      const result = openSessionTab(current, nextSession);
      setActiveTabId(result.activeTabId);
      return result.tabs;
    });
    selectSession(nextSession);
  }

  function handleActivateTab(tabId: WorkbenchTab["id"]) {
    setActiveTabId(tabId);
    if (!tabId.startsWith("session:")) {
      return;
    }

    const sessionId = tabId.slice("session:".length);
    const nextSession = sessions.find((item) => item.id === sessionId) ?? null;
    selectSession(nextSession);
  }

  function handleCloseTab(tabId: WorkbenchTab["id"]) {
    setOpenTabs((current) => {
      const result = closeTab(current, tabId, activeTabId);
      setActiveTabId(result.activeTabId);
      if (result.activeTabId.startsWith("session:")) {
        const sessionId = result.activeTabId.slice("session:".length);
        selectSession(sessions.find((item) => item.id === sessionId) ?? null);
      } else {
        selectSession(null);
      }
      return result.tabs;
    });
  }

  function handleCloseOtherTabs(tabId: WorkbenchTab["id"]) {
    setOpenTabs((current) => {
      const result = closeOtherTabs(current, tabId);
      setActiveTabId(result.activeTabId);
      if (result.activeTabId.startsWith("session:")) {
        const sessionId = result.activeTabId.slice("session:".length);
        selectSession(sessions.find((item) => item.id === sessionId) ?? null);
      } else {
        selectSession(null);
      }
      return result.tabs;
    });
  }

  return {
    handleOpenSystemTab,
    handleOpenSessionTab,
    handleActivateTab,
    handleCloseTab,
    handleCloseOtherTabs,
  };
}
