import { describe, expect, it } from "vitest";
import { closeTab, getInitialTabs, openSessionTab, openSystemTab } from "./tabModel";
import type { WorkbenchTab } from "./types";

describe("tabModel", () => {
  it("deduplicates system tabs and activates the requested tab", () => {
    const initial = getInitialTabs();

    const result = openSystemTab(initial, "settings");
    const duplicate = openSystemTab(result.tabs, "settings");

    expect(result.activeTabId).toBe("system:settings");
    expect(duplicate.activeTabId).toBe("system:settings");
    expect(duplicate.tabs.filter((tab) => tab.id === "system:settings")).toHaveLength(1);
  });

  it("deduplicates session tabs by session id", () => {
    const initial = getInitialTabs();

    const first = openSessionTab(initial, { id: "sess_1", title: "Repair failing tests" });
    const second = openSessionTab(first.tabs, { id: "sess_1", title: "Updated title" });

    expect(second.activeTabId).toBe("session:sess_1");
    expect(second.tabs.filter((tab) => tab.id === "session:sess_1")).toHaveLength(1);
    expect(second.tabs.find((tab) => tab.id === "session:sess_1")?.title).toBe("Repair failing tests");
  });

  it("activates a neighbor when closing the active session tab", () => {
    const tabs: WorkbenchTab[] = [
      { id: "system:new-session", kind: "new-session", title: "New Session" },
      { id: "session:sess_1", kind: "session", title: "Session A", sessionId: "sess_1", closable: true },
      { id: "system:settings", kind: "settings", title: "Settings" },
    ];

    const result = closeTab(tabs, "session:sess_1", "session:sess_1");

    expect(result.tabs.map((tab) => tab.id)).toEqual(["system:new-session", "system:settings"]);
    expect(result.activeTabId).toBe("system:settings");
  });

  it("does not close system tabs", () => {
    const tabs: WorkbenchTab[] = [{ id: "system:settings", kind: "settings", title: "Settings" }];

    const result = closeTab(tabs, "system:settings", "system:settings");

    expect(result.tabs).toEqual(tabs);
    expect(result.activeTabId).toBe("system:settings");
  });
});
