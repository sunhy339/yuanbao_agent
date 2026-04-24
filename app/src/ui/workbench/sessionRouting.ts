import type { SessionRecord } from "@shared";
import type { WorkbenchTab } from "./types";

export function resolveSessionForTab(
  activeTab: WorkbenchTab,
  sessions: SessionRecord[],
  fallbackSession: SessionRecord | null,
): SessionRecord | null {
  if (activeTab.kind !== "session") {
    return null;
  }

  const matched = sessions.find((item) => item.id === activeTab.sessionId);
  if (matched) {
    return matched;
  }

  if (fallbackSession?.id === activeTab.sessionId) {
    return fallbackSession;
  }

  return null;
}

export function getSidebarActiveSessionId(activeTab: WorkbenchTab): string | null {
  return activeTab.kind === "session" ? activeTab.sessionId : null;
}

