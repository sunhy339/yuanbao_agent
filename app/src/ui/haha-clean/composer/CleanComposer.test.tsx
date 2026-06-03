import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { CleanComposer, type CleanComposerProps } from "./CleanComposer";

const runtimeMocks = vi.hoisted(() => ({
  gitLocalStatus: vi.fn(),
}));

vi.mock("../../../lib/runtimeClient", () => ({
  RuntimeClient: vi.fn(function RuntimeClient() {
    return runtimeMocks;
  }),
}));

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  window.localStorage.clear();
  runtimeMocks.gitLocalStatus.mockReset();
});

function renderComposer(overrides: Partial<CleanComposerProps> = {}) {
  const handlers = {
    onPromptChange: vi.fn(),
    onSubmitPrompt: vi.fn(),
    onPermissionModeChange: vi.fn(),
    onAttachmentsChange: vi.fn(),
    onAttachmentError: vi.fn(),
  };

  render(
    <CleanComposer
      promptValue=""
      onPromptChange={handlers.onPromptChange}
      onSubmitPrompt={handlers.onSubmitPrompt}
      disabled={false}
      providerLabel="OpenAI"
      cwdLabel="D:/py/yuanbao_agent"
      permissionLabel="询问权限"
      permissionMode="ask"
      onPermissionModeChange={handlers.onPermissionModeChange}
      onAttachmentsChange={handlers.onAttachmentsChange}
      onAttachmentError={handlers.onAttachmentError}
      contextLabel="上下文 25%"
      contextPreview={{
        projectFocus: "Keep the UI close to haha-cc.",
        toolCount: 8,
        budgetStats: {
          estimatedInputTokens: 2500,
          maxContextTokens: 10000,
          messageTokens: 1200,
          toolSchemaTokens: 600,
          stablePrefixTokens: 1800,
          promptLayers: [
            { name: "role", tokenEstimate: 120 },
            { name: "runtime_safety", tokenEstimate: 240 },
          ],
          includedSections: [
            "system_prompt",
            "project_focus",
            "referenced_file:app/src/App.tsx",
            "recent_conversation",
            "user_message",
          ],
          trimmedSections: ["旧工具日志"],
        },
        taskFocus: {
          currentStep: "补齐 composer 面板",
        },
      }}
      worktreeStatus={{
        dirtyFiles: 2,
        files: ["app/src/App.tsx", "app/src/ui/clean.css"],
        branch: "main",
        upstream: "origin/main",
        ahead: 1,
        behind: 0,
      }}
      modelOptions={[
        { id: "gpt-5", label: "gpt-5" },
        { id: "gpt-5.4-mini", label: "5.4-mini" },
      ]}
      selectedModelId="gpt-5.4-mini"
      {...overrides}
    />,
  );

  return handlers;
}

function renderSlashComposer(promptValue = "/") {
  const handlers = {
    onPromptChange: vi.fn(),
    onSubmitPrompt: vi.fn(),
  };

  render(
    <CleanComposer
      promptValue={promptValue}
      onPromptChange={handlers.onPromptChange}
      onSubmitPrompt={handlers.onSubmitPrompt}
      disabled={false}
      providerLabel="OpenAI"
      cwdLabel="D:/py/yuanbao_agent"
      modelOptions={[{ id: "gpt-5", label: "gpt-5" }]}
      selectedModelId="gpt-5"
    />,
  );

  return handlers;
}

describe("CleanComposer", () => {
  it("opens context and project detail panels from the compact composer controls", async () => {
    const user = userEvent.setup();
    const copyText = vi.fn();
    renderComposer({ onCopyText: copyText });

    await user.click(screen.getByRole("button", { name: "上下文 25%", expanded: false }));
    const contextPanel = screen.getByLabelText("上下文详情");
    expect(contextPanel).toHaveTextContent("25%");
    expect(contextPanel).toHaveTextContent("已使用");
    expect(contextPanel).toHaveTextContent("2,500");
    expect(contextPanel).toHaveTextContent("剩余");
    expect(contextPanel).toHaveTextContent("7,500");
    expect(contextPanel).toHaveTextContent("窗口");
    expect(contextPanel).toHaveTextContent("10,000");
    const tokenBars = within(contextPanel).getByLabelText("上下文 token 使用");
    expect(tokenBars).toHaveTextContent("Input tokens");
    expect(tokenBars).toHaveTextContent("Cache read");
    expect(tokenBars).toHaveTextContent("Output tokens");
    expect(within(contextPanel).getByLabelText("纳入上下文")).toHaveTextContent("引用文件");
    expect(within(contextPanel).getByLabelText("系统提示层")).toHaveTextContent("runtime_safety");
    expect(contextPanel).toHaveTextContent("补齐 composer 面板");
    await user.click(within(contextPanel).getByRole("button", { name: /复制上下文/ }));
    expect(copyText).toHaveBeenCalledWith(expect.stringContaining("当前步骤: 补齐 composer 面板"));
    expect(copyText).toHaveBeenCalledWith(expect.stringContaining("项目焦点: Keep the UI close to haha-cc."));
    expect(copyText).toHaveBeenCalledWith(expect.stringContaining("纳入上下文: 系统提示 1、项目上下文 1、引用文件 1"));

    await user.click(screen.getByRole("button", { name: "yuanbao_agent" }));
    const projectPanel = screen.getByLabelText("项目目录");
    expect(projectPanel).toHaveTextContent("D:/py/yuanbao_agent");
    expect(projectPanel).toHaveTextContent("main");
    expect(projectPanel).toHaveTextContent("origin/main");
    expect(projectPanel).toHaveTextContent("领先 1");
    expect(projectPanel).toHaveTextContent("2 个文件");
    expect(projectPanel).toHaveTextContent("app/src/App.tsx");
    await user.click(within(projectPanel).getByRole("button", { name: /复制路径/ }));
    expect(copyText).toHaveBeenCalledWith("D:/py/yuanbao_agent");
    await user.click(within(projectPanel).getByRole("button", { name: /复制改动文件/ }));
    expect(copyText).toHaveBeenCalledWith("app/src/App.tsx\napp/src/ui/clean.css");
    expect(projectPanel).not.toHaveTextContent("后续会在这里补");
    expect(screen.queryByRole("button", { name: /权限与上下文会随会话更新/ })).not.toBeInTheDocument();
    expect(screen.queryByLabelText("上下文详情")).not.toBeInTheDocument();
  });

  it("hides low-value default task steps from the context panel", async () => {
    const user = userEvent.setup();
    const copyText = vi.fn();
    renderComposer({
      onCopyText: copyText,
      contextPreview: {
        budgetStats: {
          estimatedInputTokens: 0,
          maxContextTokens: 256000,
          messageTokens: 0,
        },
        taskFocus: {
          currentStep: "理解任务目标",
        },
      } as any,
    });

    await user.click(screen.getByRole("button", { name: "上下文 0%", expanded: false }));
    const contextPanel = screen.getByLabelText("上下文详情");
    expect(contextPanel).not.toHaveTextContent("当前步骤：理解任务目标");

    await user.click(within(contextPanel).getByRole("button", { name: /复制上下文/ }));
    expect(copyText).not.toHaveBeenCalledWith(expect.stringContaining("理解任务目标"));
  });

  it("shows permission descriptions and slash command argument hints", async () => {
    const handlers = renderComposer();
    const user = userEvent.setup();

    await user.click(screen.getByRole("button", { name: /询问权限/ }));
    const permissionPanel = screen.getByText("写入、命令和高风险操作前先确认。").closest(".hc-popover");
    expect(permissionPanel).toBeInTheDocument();
    await user.click(within(permissionPanel as HTMLElement).getByRole("button", { name: /完全访问权限/ }));
    expect(handlers.onPermissionModeChange).not.toHaveBeenCalled();
    expect(within(permissionPanel as HTMLElement).getByText("确认完全访问权限？")).toBeInTheDocument();

    await user.click(within(permissionPanel as HTMLElement).getByRole("button", { name: "确认完全访问" }));
    expect(handlers.onPermissionModeChange).toHaveBeenCalledWith("skip");

    await user.click(screen.getByRole("button", { name: "添加" }));
    await user.click(screen.getByRole("button", { name: /斜杠命令/ }));
    expect(handlers.onPromptChange).toHaveBeenCalledWith("/");
  });

  it("renders image attachments as thumbnails with removable labels", () => {
    const handlers = renderComposer({
      attachments: ["D:/screenshots/ui.png", "D:/notes/readme.txt"],
    });

    expect(screen.getByRole("img", { name: "ui.png" })).toHaveAttribute("src", "D:/screenshots/ui.png");
    expect(screen.getByText("readme.txt")).toBeInTheDocument();
    screen.getByRole("button", { name: "移除 ui.png" }).click();
    expect(handlers.onAttachmentsChange).toHaveBeenCalledWith(["D:/notes/readme.txt"]);
  });

  it("shows a disabled pending state while stop is in flight", () => {
    const onStopPrompt = vi.fn();
    renderComposer({ sending: true, stopPending: true, onStopPrompt });

    const stopButton = screen.getByRole("button", { name: /停止中/ });
    expect(stopButton).toBeDisabled();
    stopButton.click();
    expect(onStopPrompt).not.toHaveBeenCalled();
  });

  it("accepts dragged files as composer attachments", async () => {
    const handlers = renderComposer({
      attachments: ["D:/screenshots/ui.png"],
    });

    const form = screen.getByRole("form", { name: "消息输入" });
    const dropped = new File(["hello"], "readme.md", { type: "text/markdown" }) as File & { path?: string };
    dropped.path = "D:/notes/readme.md";
    const dataTransfer = {
      types: ["Files"],
      files: [dropped],
      dropEffect: "none",
    } as unknown as DataTransfer;

    fireEvent.dragEnter(form, { dataTransfer });
    expect(screen.getByText("松开添加到附件")).toBeInTheDocument();

    fireEvent.drop(form, { dataTransfer });
    expect(screen.queryByText("松开添加到附件")).not.toBeInTheDocument();
    expect(handlers.onAttachmentsChange).toHaveBeenCalledWith([
      "D:/screenshots/ui.png",
      "D:/notes/readme.md",
    ]);
  });

  it("reports empty drops without changing attachments", () => {
    const handlers = renderComposer();
    const form = screen.getByRole("form", { name: "消息输入" });

    fireEvent.drop(form, {
      dataTransfer: {
        types: ["Files"],
        files: [],
        dropEffect: "none",
      },
    });

    expect(handlers.onAttachmentsChange).not.toHaveBeenCalled();
    expect(handlers.onAttachmentError).toHaveBeenCalledWith("没有读取到可添加的文件。");
  });

  it("lets the slash command panel be selected with the keyboard", async () => {
    const handlers = renderSlashComposer("/m");
    const user = userEvent.setup();
    const textbox = screen.getByRole("textbox");
    textbox.focus();

    const panel = screen.getByRole("listbox", { name: "斜杠命令" });
    expect(within(panel).getByRole("option", { name: /\/model/ })).toHaveAttribute("aria-selected", "true");

    await user.keyboard("{ArrowDown}");
    expect(within(panel).getByRole("option", { name: /\/mcp/ })).toHaveAttribute("aria-selected", "true");

    await user.keyboard("{Enter}");
    expect(handlers.onPromptChange).toHaveBeenCalledWith("/mcp ");
  });

  it("inserts project file references from the @ picker", async () => {
    const onFileReferenceQueryChange = vi.fn();
    const handlers = renderComposer({
      promptValue: "请看 @",
      onFileReferenceQueryChange,
      fileReferenceOptions: [
        { path: "src/app.tsx" },
        { path: "docs/readme.md" },
      ],
    });
    const user = userEvent.setup();
    const textbox = screen.getByRole("textbox");
    textbox.focus();
    (textbox as HTMLTextAreaElement).setSelectionRange("请看 @".length, "请看 @".length);

    const panel = screen.getByRole("listbox", { name: "文件引用" });
    expect(onFileReferenceQueryChange).toHaveBeenCalledWith("");
    expect(within(panel).getByRole("option", { name: /app\.tsx/ })).toHaveAttribute("aria-selected", "true");

    await user.keyboard("{ArrowDown}{Enter}");
    expect(handlers.onPromptChange).toHaveBeenCalledWith("请看 @docs/readme.md ");
  });

  it("passes launch repository choices when starting a new session", async () => {
    vi.stubGlobal("__TAURI_INTERNALS__", {});
    runtimeMocks.gitLocalStatus.mockResolvedValue({
      cwd: "D:/py/test_pro",
      repoRoot: "D:/py/test_pro",
      repoName: "test_pro",
      branch: "master",
      defaultBranch: "master",
      upstream: "origin/master",
      ahead: 0,
      behind: 0,
      dirtyFiles: 0,
      files: [],
      branches: [
        { name: "master", current: true, local: true },
        { name: "feature/parity", current: false, local: true },
        { name: "worktree-desktop/scratch", current: false, local: true },
      ],
      clean: true,
      rawStatus: "",
    });
    const user = userEvent.setup();
    const handlers = renderComposer({
      variant: "new",
      promptValue: "开始",
      cwdLabel: "D:/py/test_pro",
      useWorktree: false,
      onUseWorktreeChange: vi.fn(),
      worktreeStatus: { dirtyFiles: 0, files: [], branch: "master" } as any,
    });

    await screen.findByRole("button", { name: /master/ });
    await user.click(screen.getByRole("button", { name: /master/ }));
    const branchMenu = screen.getByLabelText("选择分支");
    expect(within(branchMenu).queryByText("worktree-desktop/scratch")).not.toBeInTheDocument();
    await user.click(await within(branchMenu).findByRole("menuitemradio", { name: /feature\/parity/ }));
    expect(screen.getByRole("button", { name: /feature\/parity/ })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /当前工作树/ }));
    await user.click(within(screen.getByLabelText("工作树模式")).getByRole("menuitemradio", { name: /独立工作树/ }));
    expect(screen.getByRole("button", { name: /独立工作树/ })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "发送" }));
    expect(handlers.onSubmitPrompt).toHaveBeenCalledWith({
      workDir: "D:/py/test_pro",
      repository: {
        worktree: true,
        branch: "feature/parity",
      },
    });
  });
});
