import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { CleanComposer } from "./CleanComposer";

afterEach(() => {
  cleanup();
});

function renderComposer() {
  const handlers = {
    onPromptChange: vi.fn(),
    onSubmitPrompt: vi.fn(),
    onPermissionModeChange: vi.fn(),
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
      modelOptions={[
        { id: "gpt-5", label: "gpt-5" },
        { id: "gpt-5.4-mini", label: "5.4-mini" },
      ]}
      selectedModelId="gpt-5.4-mini"
    />,
  );

  return handlers;
}

describe("CleanComposer", () => {
  it("opens context and project detail panels from the compact composer controls", async () => {
    const user = userEvent.setup();
    renderComposer();

    await user.click(screen.getByRole("button", { name: "上下文 25%", expanded: false }));
    const contextPanel = screen.getByLabelText("上下文详情");
    expect(contextPanel).toHaveTextContent("预算");
    expect(contextPanel).toHaveTextContent("2,500 / 10,000");
    expect(contextPanel).toHaveTextContent("可用工具");
    expect(contextPanel).toHaveTextContent("补齐 composer 面板");

    await user.click(screen.getByRole("button", { name: "yuanbao_agent" }));
    const projectPanel = screen.getByLabelText("项目目录");
    expect(projectPanel).toHaveTextContent("D:/py/yuanbao_agent");
    expect(screen.queryByLabelText("上下文详情")).not.toBeInTheDocument();
  });

  it("shows permission descriptions and slash command argument hints", async () => {
    const handlers = renderComposer();
    const user = userEvent.setup();

    await user.click(screen.getByRole("button", { name: /询问权限/ }));
    const permissionPanel = screen.getByText("写入、命令和高风险操作前先确认。").closest(".hc-popover");
    expect(permissionPanel).toBeInTheDocument();
    await user.click(within(permissionPanel as HTMLElement).getByRole("button", { name: /完全访问权限/ }));
    expect(handlers.onPermissionModeChange).toHaveBeenCalledWith("skip");

    await user.click(screen.getByRole("button", { name: "添加" }));
    await user.click(screen.getByRole("button", { name: /斜杠命令/ }));
    expect(handlers.onPromptChange).toHaveBeenCalledWith("/");
  });
});
