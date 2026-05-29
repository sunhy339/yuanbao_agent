import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";
import { CleanActivityItem, CleanPermissionMessageBlock, CleanRuntimeBlock, CleanToolMessageBlock, CleanWorklogBlock } from "./CleanConversation";

afterEach(() => cleanup());

describe("CleanConversation", () => {
  it("does not treat patch titles as changed file paths", () => {
    render(
      <CleanRuntimeBlock
        item={{
          id: "patch:1",
          kind: "patch",
          title: "Update snake_game/README.md",
          status: "applied",
          code: "Update snake_game/README.md\nmodified snake_game/README.md (+2/-4)\nmodified snake_game/rules.py (+0/-2)",
        }}
      />,
    );

    expect(screen.getByText("snake_game/README.md")).toBeInTheDocument();
    expect(screen.getByText("snake_game/rules.py")).toBeInTheDocument();
    expect(screen.queryByText("Update snake_game/README.md", { selector: "code" })).not.toBeInTheDocument();
  });

  it("opens the matching local diff when a patch file row is clicked", async () => {
    const user = userEvent.setup();
    const onLoadPatch = vi.fn();
    render(
      <CleanRuntimeBlock
        onLoadPatch={onLoadPatch}
        item={{
          id: "patch:diff",
          kind: "patch",
          title: "Update snake_game files",
          status: "applied",
          code: "modified snake_game/game.py (+1/-1)\nmodified snake_game/rules.py (+1/-1)",
          rawDetail: [
            "diff --git a/snake_game/game.py b/snake_game/game.py",
            "--- a/snake_game/game.py",
            "+++ b/snake_game/game.py",
            "@@ -1 +1 @@",
            "-old_game",
            "+new_game",
            "diff --git a/snake_game/rules.py b/snake_game/rules.py",
            "--- a/snake_game/rules.py",
            "+++ b/snake_game/rules.py",
            "@@ -1 +1 @@",
            "-old_rules",
            "+new_rules",
          ].join("\n"),
          sourceId: "patch_1",
        }}
      />,
    );

    await user.click(screen.getByRole("button", { name: /snake_game\/rules\.py/ }));

    expect(onLoadPatch).not.toHaveBeenCalled();
    expect(screen.getAllByText("snake_game/rules.py").length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText("new_rules")).toBeInTheDocument();
    expect(screen.queryByText("new_game")).not.toBeInTheDocument();
  });

  it("exposes patch file list copy and revert placeholder actions", async () => {
    const user = userEvent.setup();
    const onCopyRuntimeText = vi.fn();

    render(
      <CleanRuntimeBlock
        onCopyRuntimeText={onCopyRuntimeText}
        item={{
          id: "patch:actions",
          kind: "patch",
          title: "Update snake_game files",
          status: "applied",
          code: "modified snake_game/game.py (+2/-4)\nmodified snake_game/rules.py (+0/-2)",
        }}
      />,
    );

    await user.click(screen.getByRole("button", { name: /复制文件列表/ }));
    expect(onCopyRuntimeText).toHaveBeenCalledWith(
      "改动文件列表",
      expect.stringContaining("snake_game/game.py  修改 +2 -4"),
    );
    expect(screen.getByRole("button", { name: /撤销本轮/ })).toBeDisabled();
  });

  it("exposes lightweight copy and quote actions for normal messages", async () => {
    const user = userEvent.setup();
    const onCopyRuntimeText = vi.fn();
    const onQuoteMessage = vi.fn();

    render(
      <CleanActivityItem
        item={{
          id: "message:1",
          kind: "message",
          order: 1,
          message: {
            id: "m1",
            role: "assistant",
            content: "可以先拆出 rules.py。",
          },
        }}
        onCopyRuntimeText={onCopyRuntimeText}
        onQuoteMessage={onQuoteMessage}
      />,
    );

    await user.click(screen.getByRole("button", { name: "复制" }));
    expect(onCopyRuntimeText).toHaveBeenCalledWith("消息内容", "可以先拆出 rules.py。");

    await user.click(screen.getByRole("button", { name: "引用" }));
    expect(onQuoteMessage).toHaveBeenCalledWith(
      expect.stringContaining("> 助手："),
    );
  });

  it("falls back to copying a quote when the composer quote bridge is absent", async () => {
    const user = userEvent.setup();
    const onCopyRuntimeText = vi.fn();

    render(
      <CleanActivityItem
        item={{
          id: "message:2",
          kind: "message",
          order: 1,
          message: {
            id: "m2",
            role: "user",
            content: "继续做",
          },
        }}
        onCopyRuntimeText={onCopyRuntimeText}
      />,
    );

    await user.click(screen.getByRole("button", { name: "引用" }));
    expect(onCopyRuntimeText).toHaveBeenCalledWith("引用消息", expect.stringContaining("> 用户："));
  });

  it("shows clear overflow actions and disabled backend placeholders", async () => {
    const user = userEvent.setup();
    const onCopyRuntimeText = vi.fn();

    render(
      <CleanActivityItem
        item={{
          id: "message:overflow",
          kind: "message",
          order: 1,
          message: {
            id: "m-overflow",
            role: "assistant",
            content: "下一步可以拆出 rules.py。",
          },
        }}
        onCopyRuntimeText={onCopyRuntimeText}
        onQuoteMessage={vi.fn()}
      />,
    );

    await user.click(screen.getByRole("button", { name: "更多" }));

    expect(screen.getByRole("button", { name: /从这里继续/ })).toBeDisabled();
    expect(screen.getByRole("button", { name: /从这里分支/ })).toBeDisabled();
    expect(screen.getByRole("button", { name: /删除消息/ })).toBeDisabled();

    await user.click(screen.getByRole("button", { name: /复制为 Markdown/ }));
    expect(onCopyRuntimeText).toHaveBeenCalledWith("Markdown 引用", expect.stringContaining("> 助手："));
  });

  it("renders ask-user events as a dedicated decision node", () => {
    render(
      <CleanActivityItem
        item={{
          id: "message:ask",
          kind: "message",
          order: 1,
          message: {
            id: "ask1",
            role: "assistant",
            content: "",
            metadata: {
              kind: "ask_user_question",
              question: "要继续拆分渲染层吗？",
              options: [
                { label: "继续", description: "沿着当前任务往下做。" },
                { label: "暂停", description: "等待下一步确认。" },
              ],
            },
          },
        }}
      />,
    );

    expect(screen.getByText("需要你确认")).toBeInTheDocument();
    expect(screen.getByText("要继续拆分渲染层吗？")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /继续/ })).toBeDisabled();
    expect(screen.getAllByText("待接入回答提交").length).toBe(2);
  });

  it("renders computer-use permission placeholders", () => {
    render(
      <CleanActivityItem
        item={{
          id: "message:computer",
          kind: "message",
          order: 1,
          message: {
            id: "computer1",
            role: "assistant",
            content: "",
            metadata: {
              kind: "computer_use_permission",
              app: "VS Code",
              action: "读取当前窗口",
            },
          },
        }}
      />,
    );

    expect(screen.getByText("Computer Use 权限")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "允许" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "拒绝" })).toBeDisabled();
  });

  it("uses readable action titles for inline tool messages", () => {
    render(
      <CleanToolMessageBlock
        message={{
          id: "tool1",
          role: "assistant",
          content: "",
          toolName: "read_file",
          status: "completed",
          metadata: {
            inputText: JSON.stringify({ path: "snake_game/game.py" }),
            resultText: "ok",
          },
        }}
      />,
    );

    expect(screen.getByText("读取 snake_game/game.py")).toBeInTheDocument();
  });

  it("summarizes inline tool JSON results instead of exposing raw details", async () => {
    const user = userEvent.setup();
    const onCopyRuntimeText = vi.fn();

    render(
      <CleanToolMessageBlock
        onCopyRuntimeText={onCopyRuntimeText}
        message={{
          id: "tool-json",
          role: "assistant",
          content: "",
          toolName: "run_command",
          status: "completed",
          metadata: {
            inputText: JSON.stringify({ command: "python -m pytest tests -q" }),
            resultText: JSON.stringify({
              status: "completed",
              exitCode: 0,
              stdout: "3 passed in 0.01s",
            }),
          },
        }}
      />,
    );

    expect(screen.getByText("运行 python -m pytest tests -q")).toBeInTheDocument();
    expect(screen.getByText(/已完成 · 退出码 0 · 3 passed in 0\.01s/)).toBeInTheDocument();
    expect(screen.queryByText(/"exitCode"/)).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /运行 python -m pytest tests -q/ }));
    expect(screen.getByText("工具详情")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "复制" }));
    expect(onCopyRuntimeText).toHaveBeenCalledWith("工具详情", expect.stringContaining('"exitCode":0'));
  });

  it("summarizes inline tool item arrays with useful names", () => {
    render(
      <CleanToolMessageBlock
        message={{
          id: "tool-list",
          role: "assistant",
          content: "",
          toolName: "list_dir",
          status: "completed",
          metadata: {
            inputText: JSON.stringify({ path: "snake_game" }),
            resultText: JSON.stringify({
              items: [
                { name: "game.py" },
                { name: "rules.py" },
                { name: "README.md" },
                { name: "config.py" },
              ],
            }),
          },
        }}
      />,
    );

    expect(screen.getByText(/找到 4 项：game\.py、rules\.py、README\.md，另 1 项/)).toBeInTheDocument();
  });

  it("uses readable action titles for permission messages", () => {
    render(
      <CleanPermissionMessageBlock
        message={{
          id: "permission1",
          role: "assistant",
          content: "patch approval request",
          toolName: "apply_patch",
          metadata: {
            requestId: "approval-1",
            parametersPreview: JSON.stringify({ path: "snake_game/rules.py" }),
          },
        }}
      />,
    );

    expect(screen.getByText("修改 snake_game/rules.py 需要确认")).toBeInTheDocument();
    expect(screen.queryByText("patch approval request")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "始终允许" })).toBeDisabled();
    expect(screen.queryByText(/apply_patch 需要确认/)).not.toBeInTheDocument();
  });

  it("renders approval runtime items as dedicated approval nodes", async () => {
    const user = userEvent.setup();
    const onApprove = vi.fn();
    const onReject = vi.fn();

    render(
      <CleanRuntimeBlock
        item={{
          id: "approval:1",
          kind: "approval",
          sourceId: "approval-1",
          title: "apply_patch",
          status: "pending",
          riskLevel: "medium",
          code: JSON.stringify({ path: "snake_game/rules.py" }),
          rawDetail: "modified snake_game/rules.py (+2/-1)",
        }}
        onApprove={onApprove}
        onReject={onReject}
      />,
    );

    expect(screen.getByText("需要确认")).toBeInTheDocument();
    expect(screen.getByText("修改 snake_game/rules.py")).toBeInTheDocument();
    expect(screen.getByText("snake_game/rules.py")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "允许一次" }));
    expect(onApprove).toHaveBeenCalledWith("approval-1");

    await user.click(screen.getByRole("button", { name: "拒绝" }));
    expect(onReject).toHaveBeenCalledWith("approval-1");
    expect(screen.getByRole("button", { name: "始终允许" })).toBeDisabled();
  });

  it("renders expanded worklogs as compact runtime rows", async () => {
    const user = userEvent.setup();
    const onCopyRuntimeText = vi.fn();

    render(
      <CleanWorklogBlock
        onCopyRuntimeText={onCopyRuntimeText}
        items={[
          {
            id: "tool:list",
            kind: "tool",
            title: "list_dir",
            status: "completed",
            toolName: "list_dir",
            code: JSON.stringify({ path: "snake_game" }),
            rawDetail: JSON.stringify({ items: [{ name: "game.py" }] }),
            durationMs: 12,
          },
          {
            id: "tool:read",
            kind: "tool",
            title: "read_file",
            status: "completed",
            toolName: "read_file",
            code: JSON.stringify({ path: "snake_game/game.py" }),
            rawDetail: "class Game: pass",
          },
        ]}
      />,
    );

    expect(screen.getByText(/已执行 2 项/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /已执行 2 项/ }));
    expect(screen.getByText("查看 snake_game")).toBeInTheDocument();
    expect(screen.getByText("读取 snake_game/game.py")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /读取 snake_game\/game\.py/ }));
    await user.click(screen.getByRole("button", { name: "复制" }));
    expect(onCopyRuntimeText).toHaveBeenCalledWith("工具详情", "class Game: pass");
  });

  it("indents parented worklog tools as a compact tree", async () => {
    const user = userEvent.setup();

    render(
      <CleanWorklogBlock
        items={[
          {
            id: "tool:parent",
            kind: "tool",
            title: "task",
            status: "completed",
            toolName: "task",
            toolUseId: "parent_1",
            summary: "拆分渲染任务",
          },
          {
            id: "tool:child",
            kind: "tool",
            title: "read_file",
            status: "completed",
            toolName: "read_file",
            toolUseId: "child_1",
            parentToolUseId: "parent_1",
            code: JSON.stringify({ path: "app/src/ui.tsx" }),
          },
        ]}
      />,
    );

    await user.click(screen.getByRole("button", { name: /已执行 2 项/ }));
    expect(screen.getByText("1 个子步骤")).toBeInTheDocument();
    expect(screen.getByText("读取 app/src/ui.tsx").closest(".hc-worklog-row")).toHaveAttribute("data-depth", "1");
  });
});
