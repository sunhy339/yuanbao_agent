import { describe, expect, it } from "vitest";
import type { SessionRecord } from "@shared";
import { getSidebarActiveSessionId, resolveSessionForTab } from "./sessionRouting";
import type { WorkbenchTab } from "./types";

const sessions: SessionRecord[] = [
  {
    id: "sess_demo",
    workspaceId: "ws_demo",
    title: "Sprint 1 Demo Session",
    status: "active",
    createdAt: 1,
    updatedAt: 2,
  },
];

describe("sessionRouting", () => {
  it("does not reuse a remembered session on the new-session tab", () => {
    const activeTab: WorkbenchTab = {
      id: "system:new-session",
      kind: "new-session",
      title: "New Session",
      closable: true,
    };

    expect(resolveSessionForTab(activeTab, sessions, sessions[0])).toBeNull();
    expect(getSidebarActiveSessionId(activeTab)).toBeNull();
  });

  it("resolves the session that matches the active session tab", () => {
    const activeTab: WorkbenchTab = {
      id: "session:sess_demo",
      kind: "session",
      title: "Sprint 1 Demo Session",
      sessionId: "sess_demo",
      closable: true,
    };

    expect(resolveSessionForTab(activeTab, sessions, null)).toEqual(sessions[0]);
    expect(getSidebarActiveSessionId(activeTab)).toBe("sess_demo");
  });

  it("falls back only when the remembered session matches the active session tab id", () => {
    const activeTab: WorkbenchTab = {
      id: "session:sess_missing",
      kind: "session",
      title: "Missing Session",
      sessionId: "sess_missing",
      closable: true,
    };

    expect(resolveSessionForTab(activeTab, sessions, sessions[0])).toBeNull();
    expect(
      resolveSessionForTab(activeTab, sessions, {
        ...sessions[0],
        id: "sess_missing",
      }),
    ).toEqual({
      ...sessions[0],
      id: "sess_missing",
    });
  });
});

