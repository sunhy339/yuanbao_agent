import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";
import { CleanActivityItem, CleanRuntimeBlock } from "./CleanConversation";

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
  });
});
