import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { CleanAppShell } from "./CleanAppShell";
import type { AppShellV2Props } from "../../v2/layout/AppShellV2";

const runtimeMocks = vi.hoisted(() => ({
  workspaceFileSearch: vi.fn(),
}));

vi.mock("../../../lib/runtimeClient", () => ({
  RuntimeClient: vi.fn(function RuntimeClient() {
    return runtimeMocks;
  }),
}));

vi.mock("../../workbench/workspaces/session/FileWorkspacePanel", () => ({
  FileWorkspacePanel: ({ activeFilePath, onPreviewStateChange }: { activeFilePath?: string | null; onPreviewStateChange?: (hasPreview: boolean) => void }) => {
    onPreviewStateChange?.(Boolean(activeFilePath));
    return <div data-testid="mock-file-workspace">{activeFilePath ?? "files"}</div>;
  },
}));

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  window.localStorage.clear();
  runtimeMocks.workspaceFileSearch.mockReset();
});

function renderCleanShell(overrides: Partial<AppShellV2Props> = {}) {
  const handlers = {
    onPromptChange: vi.fn(),
    onOpenSystemTab: vi.fn(),
    onOpenSessionTab: vi.fn(),
    onActivateTab: vi.fn(),
    onCloseTab: vi.fn(),
    onCloseOtherTabs: vi.fn(),
    onRenameSession: vi.fn(),
    onDeleteSession: vi.fn(),
    onSubmitPrompt: vi.fn(),
  };
  const result = render(
    <CleanAppShell
      tabs={[{ id: "system:new-session", kind: "new-session", title: "新建会话", closable: true }]}
      activeTabId="system:new-session"
      sessions={[]}
      activeSessionId={null}
      workspaceName="yuanbao_agent"
      composerVisible
      promptValue=""
      disabled={false}
      providerLabel="gpt-5"
      cwdLabel="D:/py/yuanbao_agent"
      {...handlers}
      {...overrides}
    >
      <section aria-label="workspace content">Content</section>
    </CleanAppShell>,
  );
  return { ...handlers, ...result };
}

describe("CleanAppShell", () => {
  it("searches workspace files for @ references in the composer", async () => {
    runtimeMocks.workspaceFileSearch.mockResolvedValue({
      rootPath: "D:/py/yuanbao_agent",
      query: "clean",
      entries: [
        { name: "CleanComposer.tsx", path: "app/src/ui/haha-clean/composer/CleanComposer.tsx", kind: "file" },
      ],
      truncated: false,
      scanned: 128,
    });
    const handlers = renderCleanShell({
      promptValue: "请看 @clean",
      worktreeStatus: { dirtyFiles: 1, files: ["app/src/App.tsx"] },
    });
    const textbox = screen.getByRole("textbox", { name: "任务指令" }) as HTMLTextAreaElement;
    textbox.focus();
    textbox.setSelectionRange("请看 @clean".length, "请看 @clean".length);

    await screen.findByText("CleanComposer.tsx");
    await waitFor(() => {
      expect(runtimeMocks.workspaceFileSearch).toHaveBeenCalledWith({
        workspaceRoot: "D:/py/yuanbao_agent",
        query: "clean",
        maxEntries: 24,
      });
    });

    await userEvent.setup().keyboard("{Enter}");
    expect(handlers.onPromptChange).toHaveBeenCalledWith(
      "请看 @app/src/ui/haha-clean/composer/CleanComposer.tsx ",
    );
  });

  it("uses a launch bar instead of the legacy project popover on the new-session composer", () => {
    renderCleanShell({
      worktreeStatus: { dirtyFiles: 0, files: [], branch: "feature/parity" } as any,
      onWorkspacePathChange: vi.fn(),
    } as Partial<AppShellV2Props>);

    expect(screen.getByRole("button", { name: "yuanbao_agent" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /当前工作树/ })).toBeInTheDocument();
    expect(screen.getByText("干净")).toBeInTheDocument();
    expect(screen.queryByRole("textbox", { name: "工作目录" })).not.toBeInTheDocument();

    expect(screen.queryByLabelText("项目目录")).not.toBeInTheDocument();
  });

  it("opens recent workspace choices from the new-session launch bar", async () => {
    const user = userEvent.setup();
    const onWorkspacePathChange = vi.fn();
    window.localStorage.setItem("haha-clean:recent-workspaces", JSON.stringify([
      "D:/py/yuanbao_agent",
      "D:/py/test_pro",
    ]));
    renderCleanShell({
      onWorkspacePathChange,
    } as Partial<AppShellV2Props>);

    await user.click(screen.getByRole("button", { name: "yuanbao_agent" }));

    const menu = screen.getByLabelText("选择项目文件夹");
    expect(within(menu).getByText("最近")).toBeInTheDocument();
    expect(within(menu).getByText("D:/py/test_pro")).toBeInTheDocument();
    expect(within(menu).getByRole("button", { name: /选择其他文件夹/ })).toBeInTheDocument();

    await user.click(within(menu).getByRole("menuitem", { name: /test_pro/ }));
    expect(onWorkspacePathChange).toHaveBeenCalledWith("D:/py/test_pro");
  });

  it("opens branch and worktree menus from the new-session launch bar", async () => {
    const user = userEvent.setup();
    const onUseWorktreeChange = vi.fn();
    renderCleanShell({
      onWorkspacePathChange: vi.fn(),
      useWorktree: false,
      onUseWorktreeChange,
      worktreeStatus: { dirtyFiles: 7, files: ["app/src/App.tsx"], branch: "master" } as any,
    } as Partial<AppShellV2Props>);

    await user.click(screen.getByRole("button", { name: /master/ }));
    expect(screen.getByLabelText("选择分支")).toBeInTheDocument();
    expect(screen.getByText("没有可选分支")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /当前工作树/ }));
    const worktreeMenu = screen.getByLabelText("工作树模式");
    expect(within(worktreeMenu).getByRole("menuitemradio", { name: /当前工作树/ })).toBeInTheDocument();
    await user.click(within(worktreeMenu).getByRole("menuitemradio", { name: /独立工作树/ }));
    expect(screen.getByRole("button", { name: /独立工作树/ })).toBeInTheDocument();
    expect(onUseWorktreeChange).not.toHaveBeenCalled();
  });

  it("inserts selected workspace text as a quoted file reference", () => {
    const handlers = renderCleanShell({
      activeTabId: "session:sess_1",
      tabs: [{ id: "session:sess_1", kind: "session", title: "Work", sessionId: "sess_1", closable: true }],
      sessions: [{ id: "sess_1", workspaceId: "workspace_1", title: "Work", status: "active", createdAt: 1, updatedAt: 1 }],
      activeSessionId: "sess_1",
      promptValue: "continue",
    });

    window.dispatchEvent(new CustomEvent("haha-clean:add-file-reference", {
      detail: {
        path: "src/app.py",
        selection: {
          text: "def hello():\n    return 'world'",
          startLine: 1,
          endLine: 2,
        },
      },
    }));

    expect(handlers.onPromptChange).toHaveBeenCalledWith("continue @src/app.py:1-2\n> def hello():\n>     return 'world'\n");
  });

  it("keeps line comments with selected workspace references", () => {
    const handlers = renderCleanShell({
      activeTabId: "session:sess_1",
      tabs: [{ id: "session:sess_1", kind: "session", title: "Work", sessionId: "sess_1", closable: true }],
      sessions: [{ id: "sess_1", workspaceId: "workspace_1", title: "Work", status: "active", createdAt: 1, updatedAt: 1 }],
      activeSessionId: "sess_1",
    });

    window.dispatchEvent(new CustomEvent("haha-clean:add-file-reference", {
      detail: {
        path: "src/app.py",
        selection: {
          text: "    return 'world'",
          note: "确认返回值",
          startLine: 2,
          endLine: 2,
        },
      },
    }));

    expect(handlers.onPromptChange).toHaveBeenCalledWith("@src/app.py:2\n>     return 'world'\n确认返回值\n");
  });

  it("surfaces stop pending state in the composer and floating status", () => {
    renderCleanShell({
      activeTabId: "session:sess_1",
      tabs: [{ id: "session:sess_1", kind: "session", title: "Work", sessionId: "sess_1", closable: true }],
      sessions: [{ id: "sess_1", workspaceId: "workspace_1", title: "Work", status: "active", createdAt: 1, updatedAt: 1 }],
      activeSessionId: "sess_1",
      sending: true,
      stopPending: true,
      activeTaskStatus: "running",
      onStopPrompt: vi.fn(),
    });

    expect(screen.getByRole("button", { name: /停止中/ })).toBeDisabled();
    expect(screen.getByText("正在停止任务...")).toBeInTheDocument();
  });

  it("does not keep low-signal completed task steps in the floating status", () => {
    renderCleanShell({
      activeTabId: "session:sess_1",
      tabs: [{ id: "session:sess_1", kind: "session", title: "Work", sessionId: "sess_1", closable: true }],
      sessions: [{ id: "sess_1", workspaceId: "workspace_1", title: "Work", status: "active", createdAt: 1, updatedAt: 1 }],
      activeSessionId: "sess_1",
      activeTaskStatus: "completed",
      activeTaskCurrentStep: "理解任务目标",
    });

    expect(screen.queryByText("理解任务目标")).not.toBeInTheDocument();
    expect(screen.queryByText("任务运行中")).not.toBeInTheDocument();
  });

  it("filters sessions and opens the selected match", async () => {
    const user = userEvent.setup();
    const handlers = renderCleanShell({
      sessions: [
        { id: "sess_alpha", workspaceId: "workspace_1", title: "Alpha refactor", status: "active", createdAt: 1, updatedAt: 1 },
        { id: "sess_beta", workspaceId: "workspace_1", title: "Beta cleanup", status: "archived", createdAt: 2, updatedAt: 2 },
      ],
    });

    await user.type(screen.getByRole("textbox", { name: "搜索会话" }), "beta");

    expect(screen.queryByText("Alpha refactor")).not.toBeInTheDocument();
    expect(screen.getByText("Beta cleanup")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "打开会话 Beta cleanup" }));

    expect(handlers.onOpenSessionTab).toHaveBeenCalledWith(expect.objectContaining({ id: "sess_beta" }));
  });

  it("renames a session inline", async () => {
    const user = userEvent.setup();
    const handlers = renderCleanShell({
      sessions: [
        { id: "sess_1", workspaceId: "workspace_1", title: "Old title", status: "active", createdAt: 1, updatedAt: 1 },
      ],
    });

    await user.click(screen.getByRole("button", { name: "重命名 Old title" }));
    const input = screen.getByRole("textbox", { name: "编辑会话名称" });
    await user.clear(input);
    await user.type(input, "New title");
    await user.click(screen.getByRole("button", { name: "保存会话名称" }));

    expect(handlers.onRenameSession).toHaveBeenCalledWith("sess_1", "New title");
  });

  it("confirms before deleting a session", async () => {
    const user = userEvent.setup();
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    const handlers = renderCleanShell({
      sessions: [
        { id: "sess_1", workspaceId: "workspace_1", title: "Remove me", status: "active", createdAt: 1, updatedAt: 1 },
      ],
    });

    await user.click(screen.getByRole("button", { name: "删除 Remove me" }));

    expect(confirmSpy).toHaveBeenCalledWith("删除会话“Remove me”？");
    expect(handlers.onDeleteSession).toHaveBeenCalledWith("sess_1");
    confirmSpy.mockRestore();
  });

  it("opens a shell-level file workspace from the session tab toolbar", async () => {
    const user = userEvent.setup();
    const { container } = renderCleanShell({
      activeTabId: "session:sess_1",
      tabs: [{ id: "session:sess_1", kind: "session", title: "Work", sessionId: "sess_1", closable: true }],
      sessions: [{ id: "sess_1", workspaceId: "workspace_1", title: "Work", status: "active", createdAt: 1, updatedAt: 1 }],
      activeSessionId: "sess_1",
      worktreeStatus: { dirtyFiles: 1, files: ["src/app.py"] },
      fileWorkspaceChangedFiles: [{ path: "src/app.py", status: "modified" }],
    });

    expect(screen.queryByTestId("mock-file-workspace")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "文件" }));

    expect(screen.getByTestId("mock-file-workspace")).toHaveTextContent("files");
    expect(screen.getByRole("separator", { name: "调整聊天和工作区宽度" })).toBeInTheDocument();
    expect(container.querySelector(".hc-main")).toHaveAttribute("data-file-pane", "open");
    expect(container.querySelector(".hc-composer")).toBeInTheDocument();
    expect(container.querySelector(".hc-file-pane")).toBeInTheDocument();
  });
});
