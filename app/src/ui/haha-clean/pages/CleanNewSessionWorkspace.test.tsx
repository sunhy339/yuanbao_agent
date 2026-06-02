import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { CleanNewSessionWorkspace } from "./CleanNewSessionWorkspace";

afterEach(() => {
  cleanup();
});

describe("CleanNewSessionWorkspace", () => {
  it("renders the haha-cc style empty state without legacy setup cards", () => {
    render(
      <CleanNewSessionWorkspace
        workspacePath="D:\\py\\yuanbao_agent"
        hostStatusText="运行正常"
        sessionTitle="前端对齐"
        modelLabel="GPT-5"
        branchLabel="codex/frontend-parity"
        worktreeModeLabel="任务工作树"
        permissionLabel="edits"
      />,
    );

    expect(screen.getByRole("heading", { name: "新建会话" })).toBeInTheDocument();
    expect(screen.getByText("开始一个新的编码会话。Claude 已准备好帮你构建、调试和架构你的项目。")).toBeInTheDocument();
    expect(screen.queryByText("会话设置")).not.toBeInTheDocument();
    expect(screen.queryByText("项目目录")).not.toBeInTheDocument();
    expect(screen.queryByText("codex/frontend-parity")).not.toBeInTheDocument();
    expect(screen.queryByText("允许编辑")).not.toBeInTheDocument();
  });

  it("can embed the shared composer for isolated rendering", () => {
    render(
      <CleanNewSessionWorkspace
        workspacePath="D:\\py\\yuanbao_agent"
        hostStatusText="运行正常"
        sessionTitle="前端对齐"
        modelOptions={[{ id: "gpt5", label: "GPT-5" }]}
        selectedModelId="gpt5"
        permissionLabel="ask"
        composer={{
          promptValue: "",
          onPromptChange: () => {},
          onSubmitPrompt: () => {},
          disabled: false,
          permissionLabel: "询问权限",
          permissionMode: "ask",
        }}
      />,
    );

    expect(screen.getByRole("textbox", { name: "任务指令" })).toHaveAttribute("placeholder", "随便问点什么...");
    expect(screen.getByLabelText("会话启动环境")).toBeInTheDocument();
  });
});
