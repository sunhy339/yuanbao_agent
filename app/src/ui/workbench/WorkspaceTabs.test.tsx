import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { WorkspaceTabs } from "./WorkspaceTabs";
import type { WorkbenchTab } from "./types";

afterEach(() => {
  cleanup();
});

const tabs: WorkbenchTab[] = [
  { id: "system:new-session", kind: "new-session", title: "New Session", closable: true },
  { id: "session:sess_1", kind: "session", title: "Repair failing tests", sessionId: "sess_1", closable: true },
  { id: "session:sess_2", kind: "session", title: "Check Claude CLI", sessionId: "sess_2", closable: true },
];

describe("WorkspaceTabs", () => {
  it("opens a context menu for closing this tab or other tabs", async () => {
    const user = userEvent.setup();
    const onCloseTab = vi.fn();
    const onCloseOtherTabs = vi.fn();

    render(
      <WorkspaceTabs
        tabs={tabs}
        activeTabId="session:sess_1"
        onActivateTab={vi.fn()}
        onCloseTab={onCloseTab}
        onCloseOtherTabs={onCloseOtherTabs}
      />,
    );

    fireEvent.contextMenu(screen.getByRole("tab", { name: "Repair failing tests" }));
    await user.click(screen.getByRole("menuitem", { name: "关闭其他对话" }));

    expect(onCloseOtherTabs).toHaveBeenCalledWith("session:sess_1");

    fireEvent.contextMenu(screen.getByRole("tab", { name: "Check Claude CLI" }));
    await user.click(screen.getByRole("menuitem", { name: "关闭此对话" }));

    expect(onCloseTab).toHaveBeenCalledWith("session:sess_2");
  });
});
