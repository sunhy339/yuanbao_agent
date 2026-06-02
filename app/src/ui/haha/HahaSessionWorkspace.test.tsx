import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { HahaComposer } from "./HahaComposer";
import { HahaSessionWorkspace } from "./HahaSessionWorkspace";

afterEach(() => cleanup());

describe("haha frontend module", () => {
  it("renders chat messages, runtime activity, and approval actions from existing session data", () => {
    const onApprove = vi.fn();
    const onLoadPatch = vi.fn();

    render(
      <HahaSessionWorkspace
        session={{ id: "s1", title: "New Session", status: "active" }}
        activeTask={{
          id: "task_1",
          status: "running",
          goal: "Patch project",
          changedFiles: [{ path: "src/app.ts", status: "modified", additions: 2, deletions: 1 }],
        }}
        messages={[
          { id: "u1", role: "user", content: "优化一下", createdAt: 1 },
          { id: "a1", role: "assistant", content: "我先查看结构。\n\n- 读取文件\n- 再修改", createdAt: 2 },
          {
            id: "t1",
            role: "assistant",
            content: "Thinking about next steps",
            streaming: true,
            metadata: { kind: "assistant_thinking" },
            createdAt: Date.now(),
          },
        ]}
        approvals={[{ id: "ap1", title: "Allow patch", status: "pending", kind: "apply_patch" }]}
        patches={[{
          id: "patch_1",
          summary: "Update src/app.ts",
          status: "applied",
          filesChanged: 1,
          files: [{ path: "src/app.ts", status: "modified", additions: 2, deletions: 1 }],
          diff: "diff --git a/src/app.ts b/src/app.ts\n--- a/src/app.ts\n+++ b/src/app.ts\n@@\n-old\n+new",
        }]}
        traces={[]}
        toolCalls={[]}
        backgroundJobs={[]}
        composerContext={{ cwd: "D:/py/yuanbao_agent", model: "gpt-5.4-mini" }}
        onApprove={onApprove}
        onLoadPatch={onLoadPatch}
      />,
    );

    expect(screen.getByText("优化一下")).toBeInTheDocument();
    expect(screen.getByText("我先查看结构。")).toBeInTheDocument();
    expect(screen.getByText(/正在思考/)).toBeInTheDocument();
    expect(screen.getByText("Update src/app.ts")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "批准" }));
    expect(onApprove).toHaveBeenCalledWith("ap1");
  });

  it("submits prompt and opens the plus menu", () => {
    const onSubmit = vi.fn();
    const onPromptChange = vi.fn();

    render(
      <HahaComposer
        promptValue="hello"
        onPromptChange={onPromptChange}
        onSubmitPrompt={onSubmit}
        disabled={false}
        providerLabel="OpenAI"
        cwdLabel="D:/py/yuanbao_agent"
        permissionLabel="完全访问权限"
        modelOptions={[{ id: "m1", label: "5.4-mini" }]}
        selectedModelId="m1"
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "添加" }));
    expect(screen.getByText("添加文件或图片")).toBeInTheDocument();
    fireEvent.submit(screen.getByRole("textbox"));
    expect(onSubmit).toHaveBeenCalled();
  });
});
