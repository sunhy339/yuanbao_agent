import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { CleanComposer, type CleanComposerProps } from "./CleanComposer";

afterEach(() => {
  cleanup();
});

function renderComposer(overrides: Partial<CleanComposerProps> = {}) {
  const handlers = {
    onPromptChange: vi.fn(),
    onSubmitPrompt: vi.fn(),
    onPermissionModeChange: vi.fn(),
    onAttachmentsChange: vi.fn(),
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
      contextLabel="上下文 25%"
      contextPreview={{
        projectFocus: "Keep the UI close to haha-cc.",
        toolCount: 8,
        budgetStats: {
          estimatedInputTokens: 2500,
          maxContextTokens: 10000,
          messageTokens: 1200,
          toolSchemaTokens: 600,
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
    expect(contextPanel).toHaveTextContent("预算");
    expect(contextPanel).toHaveTextContent("2,500 / 10,000");
    expect(contextPanel).toHaveTextContent("可用工具");
    expect(contextPanel).toHaveTextContent("补齐 composer 面板");
    await user.click(within(contextPanel).getByRole("button", { name: /复制上下文/ }));
    expect(copyText).toHaveBeenCalledWith(expect.stringContaining("当前步骤: 补齐 composer 面板"));
    expect(copyText).toHaveBeenCalledWith(expect.stringContaining("项目焦点: Keep the UI close to haha-cc."));

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
    expect(screen.queryByLabelText("上下文详情")).not.toBeInTheDocument();
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
    const handlers = renderComposer({
      promptValue: "请看 @",
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
    expect(within(panel).getByRole("option", { name: /app\.tsx/ })).toHaveAttribute("aria-selected", "true");

    await user.keyboard("{ArrowDown}{Enter}");
    expect(handlers.onPromptChange).toHaveBeenCalledWith("请看 @docs/readme.md ");
  });
});
