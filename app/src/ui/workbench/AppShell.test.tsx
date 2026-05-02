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
  const activeTabId = options.activeTab ?? "system:overview";
  const handlers = {
    onOpenSystemTab: vi.fn(),
    onOpenSessionTab: vi.fn(),
    onActivateTab: vi.fn(),
    onCloseTab: vi.fn(),
    onCloseOtherTabs: vi.fn(),
    onRenameSession: vi.fn(),
    onDeleteSession: vi.fn(),
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
  it("renders sidebar, tabs, focused content, and composer for overview", () => {
    renderShell();

    expect(screen.getByRole("button", { name: "总览" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "新建会话" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "定时任务" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "智能体技能" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "外观" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "组件预览" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "设置" })).toBeInTheDocument();
    expect(screen.getByLabelText("桌面标题栏")).toHaveTextContent("Yuanbao Agent");
    expect(screen.getByRole("tab", { name: "总览" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByLabelText("workspace content")).toBeInTheDocument();
    expect(screen.getByLabelText("任务指令")).toBeInTheDocument();
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
        onRenameSession={vi.fn()}
        onDeleteSession={vi.fn()}
        onSubmitPrompt={vi.fn()}
        disabled={false}
        providerLabel="MiniMax-M2.7-highspeed"
        cwdLabel="D:/py/yuanbao_agent"
      >
        <section>Settings</section>
      </AppShell>,
    );

    const form = document.querySelector("form.composer-dock-hidden");
    expect(form).toBeTruthy();
  });

  it("applies the selected shell theme", () => {
    const tabs: WorkbenchTab[] = [{ id: "system:appearance", kind: "appearance", title: "Appearance" }];

    render(
      <AppShell
        tabs={tabs}
        activeTabId="system:appearance"
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
        onRenameSession={vi.fn()}
        onDeleteSession={vi.fn()}
        onSubmitPrompt={vi.fn()}
        disabled={false}
        providerLabel="MiniMax-M2.7-highspeed"
        cwdLabel="D:/py/yuanbao_agent"
        theme="light"
      >
        <section>Appearance</section>
      </AppShell>,
    );

    expect(document.querySelector(".yb-v2")).toHaveAttribute("data-theme", "light");
  });

  it("calls open handlers from sidebar", async () => {
    const handlers = renderShell();
    const user = userEvent.setup();

    await user.click(screen.getByRole("button", { name: "总览" }));
    await user.click(screen.getByRole("button", { name: "定时任务" }));
    await user.click(screen.getByRole("button", { name: "智能体技能" }));
    await user.click(screen.getByRole("button", { name: "外观" }));
    await user.click(screen.getByRole("button", { name: "组件预览" }));
    await user.click(screen.getByRole("button", { name: "设置" }));
    await user.click(screen.getByRole("button", { name: /Repair failing tests/ }));

    expect(handlers.onOpenSystemTab).toHaveBeenCalledWith("overview");
    expect(handlers.onOpenSystemTab).toHaveBeenCalledWith("scheduled");
    expect(handlers.onOpenSystemTab).toHaveBeenCalledWith("skills");
    expect(handlers.onOpenSystemTab).toHaveBeenCalledWith("appearance");
    expect(handlers.onOpenSystemTab).toHaveBeenCalledWith("playground");
    expect(handlers.onOpenSystemTab).toHaveBeenCalledWith("settings");
    expect(handlers.onOpenSessionTab).toHaveBeenCalledWith(sessions[0]);
  });

  it("shows close controls for opened tabs", async () => {
    const handlers = renderShell();
    const user = userEvent.setup();

    await user.click(screen.getByRole("button", { name: "关闭 总览" }));

    expect(handlers.onCloseTab).toHaveBeenCalledWith("system:overview");
  });
});
