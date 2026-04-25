import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AppShell } from "./AppShell";
import { getInitialTabs } from "./tabModel";
import type { WorkbenchSession, WorkbenchTab } from "./types";

const sessions: WorkbenchSession[] = [
  {
    id: "sess_1",
    title: "Repair failing tests",
    summary: "Inspect tests and patch the issue.",
    status: "active",
    workspaceId: "ws_1",
    createdAt: 1,
    updatedAt: 2,
  },
];

afterEach(() => {
  cleanup();
});

function renderShell(options: { activeTab?: WorkbenchTab["id"]; composerVisible?: boolean } = {}) {
  const tabs = getInitialTabs();
  const activeTabId = options.activeTab ?? "system:new-session";
  const handlers = {
    onOpenSystemTab: vi.fn(),
    onOpenSessionTab: vi.fn(),
    onActivateTab: vi.fn(),
    onCloseTab: vi.fn(),
    onCloseOtherTabs: vi.fn(),
    onSubmitPrompt: vi.fn(),
    onPromptChange: vi.fn(),
  };

  render(
    <AppShell
      tabs={tabs}
      activeTabId={activeTabId}
      sessions={sessions}
      activeSessionId={null}
      workspaceName="yuanbao_agent"
      composerVisible={options.composerVisible ?? true}
      promptValue=""
      disabled={false}
      providerLabel="MiniMax-M2.7-highspeed"
      cwdLabel="D:/py/yuanbao_agent"
      {...handlers}
    >
      <section aria-label="workspace content">Content</section>
    </AppShell>,
  );

  return handlers;
}

describe("AppShell", () => {
  it("renders sidebar, tabs, focused content, and composer for new session", () => {
    renderShell();

    expect(screen.getByRole("button", { name: "New Session" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Scheduled" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Settings" })).toBeInTheDocument();
    expect(screen.getByLabelText("Desktop titlebar")).toHaveTextContent("Yuanbao Agent");
    expect(screen.getByRole("tab", { name: "New Session" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByLabelText("workspace content")).toBeInTheDocument();
    expect(screen.getByLabelText("Task prompt")).toBeInTheDocument();
  });

  it("hides composer when composerVisible is false", () => {
    const tabs: WorkbenchTab[] = [{ id: "system:settings", kind: "settings", title: "Settings" }];

    render(
      <AppShell
        tabs={tabs}
        activeTabId="system:settings"
        sessions={sessions}
        activeSessionId={null}
        workspaceName="yuanbao_agent"
        composerVisible={false}
        promptValue=""
        onPromptChange={vi.fn()}
        onOpenSystemTab={vi.fn()}
        onOpenSessionTab={vi.fn()}
        onActivateTab={vi.fn()}
        onCloseTab={vi.fn()}
        onCloseOtherTabs={vi.fn()}
        onSubmitPrompt={vi.fn()}
        disabled={false}
        providerLabel="MiniMax-M2.7-highspeed"
        cwdLabel="D:/py/yuanbao_agent"
      >
        <section>Settings</section>
      </AppShell>,
    );

    expect(screen.queryByLabelText("Task prompt")).not.toBeInTheDocument();
  });

  it("calls open handlers from sidebar", async () => {
    const handlers = renderShell();
    const user = userEvent.setup();

    await user.click(screen.getByRole("button", { name: "Scheduled" }));
    await user.click(screen.getByRole("button", { name: "Settings" }));
    await user.click(screen.getByRole("button", { name: /Repair failing tests/ }));

    expect(handlers.onOpenSystemTab).toHaveBeenCalledWith("scheduled");
    expect(handlers.onOpenSystemTab).toHaveBeenCalledWith("settings");
    expect(handlers.onOpenSessionTab).toHaveBeenCalledWith(sessions[0]);
  });

  it("shows close controls for opened tabs", async () => {
    const handlers = renderShell();
    const user = userEvent.setup();

    await user.click(screen.getByRole("button", { name: "Close New Session" }));

    expect(handlers.onCloseTab).toHaveBeenCalledWith("system:new-session");
  });
});
