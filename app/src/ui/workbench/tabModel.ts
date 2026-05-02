import type { SystemWorkspaceKind, WorkbenchTab, WorkbenchTabResult, WorkbenchSession } from "./types";
import { SYSTEM_WORKSPACE_LABELS } from "../copy";

const SYSTEM_TITLES: Record<SystemWorkspaceKind, string> = {
  overview: SYSTEM_WORKSPACE_LABELS.overview,
  "new-session": SYSTEM_WORKSPACE_LABELS["new-session"],
  scheduled: SYSTEM_WORKSPACE_LABELS.scheduled,
  mcp: SYSTEM_WORKSPACE_LABELS.mcp,
  skills: SYSTEM_WORKSPACE_LABELS.skills,
  appearance: SYSTEM_WORKSPACE_LABELS.appearance,
  playground: SYSTEM_WORKSPACE_LABELS.playground,
  settings: SYSTEM_WORKSPACE_LABELS.settings,
};

export function getInitialTabs(): WorkbenchTab[] {
  return [{ id: "system:overview", kind: "overview", title: SYSTEM_TITLES.overview, closable: true }];
}

export function openSystemTab(tabs: WorkbenchTab[], kind: SystemWorkspaceKind): WorkbenchTabResult {
  const id = `system:${kind}` as const;
  if (tabs.some((tab) => tab.id === id)) {
    return { tabs, activeTabId: id };
  }

  return {
    tabs: [...tabs, { id, kind, title: SYSTEM_TITLES[kind], closable: true }],
    activeTabId: id,
  };
}

export function openSessionTab(
  tabs: WorkbenchTab[],
  session: Pick<WorkbenchSession, "id" | "title">,
): WorkbenchTabResult {
  const id = `session:${session.id}` as const;
  if (tabs.some((tab) => tab.id === id)) {
    return { tabs, activeTabId: id };
  }

  return {
    tabs: [
      ...tabs,
      {
        id,
        kind: "session",
        title: session.title.trim() || "未命名会话",
        sessionId: session.id,
        closable: true,
      },
    ],
    activeTabId: id,
  };
}

export function closeTab(
  tabs: WorkbenchTab[],
  tabId: WorkbenchTab["id"],
  activeTabId: WorkbenchTab["id"],
): WorkbenchTabResult {
  const index = tabs.findIndex((tab) => tab.id === tabId);
  const target = tabs[index];
  if (!target?.closable) {
    return { tabs, activeTabId };
  }

  const nextTabs = tabs.filter((tab) => tab.id !== tabId);
  const ensuredTabs = nextTabs.length ? nextTabs : getInitialTabs();

  if (activeTabId !== tabId) {
    return { tabs: ensuredTabs, activeTabId };
  }

  const neighbor = nextTabs[index] ?? nextTabs[index - 1] ?? ensuredTabs[0];
  return { tabs: ensuredTabs, activeTabId: neighbor.id };
}

export function closeOtherTabs(tabs: WorkbenchTab[], tabId: WorkbenchTab["id"]): WorkbenchTabResult {
  const target = tabs.find((tab) => tab.id === tabId);
  if (!target) {
    return { tabs, activeTabId: tabs[0]?.id ?? getInitialTabs()[0].id };
  }

  const nextTabs = tabs.filter((tab) => tab.id === tabId || !tab.closable);
  const ensuredTabs = nextTabs.length ? nextTabs : getInitialTabs();
  const nextActive = ensuredTabs.find((tab) => tab.id === tabId) ?? ensuredTabs[0];

  return {
    tabs: ensuredTabs,
    activeTabId: nextActive.id,
  };
}
