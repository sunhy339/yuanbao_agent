import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
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

function renderShell(
  options: {
    activeTab?: WorkbenchTab["id"];
    composerVisible?: boolean;
    sending?: boolean;
    submitting?: boolean;
    promptValue?: string;
    modelOptions?: Array<{ id: string; label: string; subtitle?: string }>;
    selectedModelId?: string;
    runtimeChildTasks?: Array<{ id: string; title: string; status?: string; workerName?: string; summary?: string }>;
  } = {},
) {
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
    onQueuePrompt: vi.fn(),
    onStopPrompt: vi.fn(),
    onPromptChange: vi.fn(),
    onSelectModel: vi.fn(),
  };

  render(
    <AppShell
      tabs={tabs}
      activeTabId={activeTabId}
      sessions={sessions}
      activeSessionId={null}
      workspaceName="yuanbao_agent"
      composerVisible={options.composerVisible ?? true}
      promptValue={options.promptValue ?? ""}
      disabled={false}
      sending={options.sending}
      submitting={options.submitting}
      queuedPromptCount={1}
      runtimeChildTasks={options.runtimeChildTasks}
      providerLabel="MiniMax-M2.7-highspeed"
      cwdLabel="D:/py/yuanbao_agent"
      modelOptions={options.modelOptions ?? [
        { id: "gpt-5-codex", label: "gpt-5-codex" },
        { id: "glm-5.1", label: "GLM-5.1" },
      ]}
      selectedModelId={options.selectedModelId ?? "glm-5.1"}
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

  it("opens the composer model menu and switches model", async () => {
    const handlers = renderShell();
    const user = userEvent.setup();

    await user.click(screen.getByRole("button", { name: /GLM-5\.1/ }));
    const menu = screen.getByRole("listbox");
    expect(within(menu).getByRole("option", { name: /glm-5\.1/i })).toHaveAttribute("aria-selected", "true");

    await user.click(within(menu).getByRole("option", { name: /gpt-5-codex/i }));

    expect(handlers.onSelectModel).toHaveBeenCalledWith("gpt-5-codex");
  });

  it("does not expose provider profile ids in the model picker", () => {
    renderShell({
      selectedModelId: "profile_1777724299376",
      modelOptions: [
        { id: "profile_1777724299376", label: "GLM-5.1", subtitle: "GLM" },
        { id: "profile_2", label: "gpt-5-codex", subtitle: "OpenAI" },
      ],
    });

    const trigger = screen.getByRole("button", { name: /GLM-5\.1/ });
    expect(trigger).toHaveTextContent("GLM-5.1");
    expect(trigger).toHaveTextContent("GLM");
    expect(trigger).not.toHaveTextContent("profile_1777724299376");
  });

  it("keeps stop separate from sending supplements while a task is running", async () => {
    const handlers = renderShell({ sending: true, promptValue: "Add this detail" });
    const user = userEvent.setup();

    const stopButton = document.querySelector<HTMLButtonElement>(".composer-stop");
    expect(stopButton).toBeInTheDocument();
    expect(stopButton).not.toBeDisabled();
    expect(screen.getByRole("textbox")).not.toBeDisabled();

    await user.click(stopButton!);

    expect(handlers.onStopPrompt).toHaveBeenCalledOnce();

    const submitButton = document.querySelector<HTMLButtonElement>(".composer-run");
    expect(submitButton).toBeInTheDocument();
    expect(submitButton).not.toBeDisabled();

    await user.click(submitButton!);

    expect(handlers.onSubmitPrompt).toHaveBeenCalledOnce();
  });

  it("queues a running conversation supplement for later", async () => {
    const handlers = renderShell({ sending: true, promptValue: "Send after this finishes" });
    const user = userEvent.setup();

    const queueButton = screen.getByRole("button", { name: /暂存/ });
    expect(queueButton).not.toBeDisabled();

    await user.click(queueButton);

    expect(handlers.onQueuePrompt).toHaveBeenCalledOnce();
    expect(handlers.onSubmitPrompt).not.toHaveBeenCalled();
  });

  it("shows real runtime child tasks above the prompt input", () => {
    renderShell({
      runtimeChildTasks: [
        { id: "child_one", title: "Patch snake rendering", status: "completed", workerName: "Worker 1" },
        { id: "child_two", title: "Verify gameplay loop", status: "running", workerName: "Worker 2" },
      ],
    });

    const checklist = screen.getByLabelText("Runtime child tasks");
    const input = screen.getByLabelText("任务指令");
    expect(checklist).toBeInTheDocument();
    expect(within(checklist).getByText("1/2")).toBeInTheDocument();
    expect(within(checklist).getByText("Patch snake rendering")).toBeInTheDocument();
    expect(within(checklist).getByText("Verify gameplay loop")).toBeInTheDocument();
    expect(within(checklist).getByText(/Worker 1/)).toBeInTheDocument();

    const nodes = Array.from(document.querySelectorAll(".composer-runtime-child-tasks, .composer-input"));
    expect(nodes[0]).toBe(checklist);
    expect(nodes[1]).toBe(input.closest(".composer-input"));
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
