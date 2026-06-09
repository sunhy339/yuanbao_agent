import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";
import { CleanActivityItem, CleanAgentTaskGroupBlock, CleanPermissionMessageBlock, CleanPlanUpdateBlock, CleanRuntimeBlock, CleanSlashCommandBlock, CleanThinkingBlock, CleanToolGroupBlock, CleanToolMessageBlock, CleanWorklogBlock } from "./CleanConversation";

afterEach(() => cleanup());

describe("CleanConversation", () => {
  it("dedupes generic child agent names in agent groups", () => {
    render(
      <CleanAgentTaskGroupBlock
        message={{
          id: "agents",
          role: "assistant",
          status: "running",
          content: "",
          metadata: {
            kind: "agent_task_group",
            title: "派遣了 2 个代理",
            summary: "1 个完成 / 1 个运行中",
            agentTasks: [
              {
                id: "planner",
                title: "Analyze codebase",
                status: "completed",
                workerName: "Planner Worker",
                agentType: "planner",
              },
              {
                id: "worker",
                title: "Implement changes",
                status: "running",
                workerName: "Worker Worker",
                agentType: "worker",
              },
            ],
          },
        }}
      />,
    );

    const planner = screen.getByText("Analyze codebase").closest("article") as HTMLElement;
    const worker = screen.getByText("Implement changes").closest("article") as HTMLElement;

    expect(planner).toHaveTextContent("Planner");
    expect(planner).not.toHaveTextContent("Planner Worker");
    expect(worker).toHaveTextContent("Worker");
    expect(worker).not.toHaveTextContent("Worker Worker");
  });

  it("renders haha-style team members as agent task rows", () => {
    render(
      <CleanAgentTaskGroupBlock
        message={{
          id: "team",
          role: "assistant",
          status: "running",
          content: "",
          metadata: {
            kind: "agent_task_group",
            title: "Team update",
            summary: "2 agents active",
            members: [
              {
                agentId: "planner-1",
                role: "planner",
                status: "running",
                currentTask: "Inspect current multi-agent workflow",
              },
              {
                agentId: "worker-1",
                role: "worker",
                status: "completed",
                currentTask: "Implement replay ordering fix",
              },
            ],
          },
        }}
      />,
    );

    expect(screen.getByText("Inspect current multi-agent workflow")).toBeInTheDocument();
    expect(screen.getByText("Implement replay ordering fix")).toBeInTheDocument();
    expect(screen.queryByText("planner-1")).not.toBeInTheDocument();
  });

  it("does not use internal task ids as agent or plan titles", () => {
    render(
      <div>
        <CleanAgentTaskGroupBlock
          message={{
            id: "agents-internal",
            role: "assistant",
            status: "running",
            content: "",
            metadata: {
              kind: "agent_task_group",
              title: "ctask_abc123",
              agentTasks: [
                {
                  id: "ctask_abc123",
                  title: "ctask_abc123",
                  currentTask: "Review frontend trace",
                  agentType: "reviewer",
                  status: "running",
                },
              ],
            },
          }}
        />
        <CleanPlanUpdateBlock
          message={{
            id: "plan-internal",
            role: "assistant",
            status: "running",
            content: "",
            metadata: {
              kind: "plan_update",
              title: "task_plan123",
              summary: "Split work across agents",
              plan: [
                {
                  id: "task_child123",
                  title: "task_child123",
                  currentTask: "Update renderer",
                  role: "frontend",
                  status: "pending",
                },
              ],
            },
          }}
        />
      </div>,
    );

    expect(screen.getByText("Review frontend trace")).toBeInTheDocument();
    expect(screen.getByText("Update renderer")).toBeInTheDocument();
    expect(screen.getAllByText("Split work across agents").length).toBeGreaterThan(0);
    expect(screen.queryByText("ctask_abc123")).not.toBeInTheDocument();
    expect(screen.queryByText("task_plan123")).not.toBeInTheDocument();
    expect(screen.queryByText("task_child123")).not.toBeInTheDocument();
  });

  it("renders plan updates as structured subtask panels instead of raw json", () => {
    render(
      <CleanPlanUpdateBlock
        message={{
          id: "plan_update:evt_1",
          role: "assistant",
          status: "completed",
          content: JSON.stringify({
            goal: "多 agent 去优化该项",
            orchestrationMode: "swarm",
            subtasks: [{ id: "sub-0", title: "Inspect workflow" }],
          }),
          metadata: {
            kind: "plan_update",
            title: "计划更新",
            summary: "已拆分 2 个 swarm 子任务，准备派发 agent。",
            goal: "多 agent 去优化该项",
            orchestrationMode: "swarm",
            subtaskCount: 2,
            subtasks: [
              { id: "sub-0", title: "Inspect current multi-agent workflow", agentType: "planner" },
              { id: "sub-1", title: "Implement orchestration changes", agentType: "worker" },
            ],
          },
        }}
      />,
    );

    expect(screen.getByText("Inspect current multi-agent workflow")).toBeInTheDocument();
    expect(screen.getByText("Implement orchestration changes")).toBeInTheDocument();
    expect(screen.getByText("swarm")).toBeInTheDocument();
    expect(screen.queryByText(/"subtasks"/)).not.toBeInTheDocument();
  });

  it("folds child agent result summaries into their task rows without exposing raw task ids", async () => {
    const user = userEvent.setup();
    render(
      <CleanAgentTaskGroupBlock
        message={{
          id: "agents",
          role: "assistant",
          status: "completed",
          content: "",
          metadata: {
            kind: "agent_task_group",
            title: "Dispatched 3 agents",
            summary: "3 completed",
            agentTasks: [
              {
                id: "ctask_4f7a483ff9e9",
                title: "Verify results",
                status: "completed",
                workerName: "Worker",
                agentType: "worker",
              },
              {
                id: "ctask_4ad725cbd806",
                title: "Implement changes",
                status: "completed",
                workerName: "Worker",
                agentType: "worker",
              },
              {
                id: "ctask_rawonly123",
                status: "completed",
                workerName: "Reviewer",
                agentType: "reviewer",
              },
            ],
            agentResults: [
              {
                id: "result-verify",
                taskId: "ctask_4f7a483ff9e9",
                title: "ctask_4f7a483ff9e9",
                summary: "Verification complete. Changed files: None.",
              },
              {
                id: "result-implement",
                taskId: "ctask_4ad725cbd806",
                title: "Implement changes",
                summary: "I inspected the current UI/repo state.",
              },
              {
                id: "result-raw-only",
                taskId: "ctask_rawonly123",
                title: "ctask_rawonly123",
                summary: "Raw-only task summary.",
              },
            ],
          },
        }}
      />,
    );

    await user.click(screen.getByRole("button", { name: /Dispatched 3 agents/ }));
    const verify = screen.getByText("Verify results").closest("article") as HTMLElement;
    const implement = screen.getByText("Implement changes").closest("article") as HTMLElement;

    expect(verify).toHaveTextContent("Verification complete");
    expect(implement).toHaveTextContent("I inspected the current UI/repo state");
    expect(screen.queryByText("ctask_4f7a483ff9e9")).not.toBeInTheDocument();
    expect(screen.getByText("Reviewer task")).toBeInTheDocument();
    expect(screen.queryByText("ctask_rawonly123")).not.toBeInTheDocument();
    expect(document.querySelector(".hc-agent-results")).not.toBeInTheDocument();
  });

  it("renders thinking as a lightweight progress row", () => {
    render(
      <CleanThinkingBlock
        message={{
          id: "thinking1",
          role: "assistant",
          content: "我在检查相关文件。",
          streaming: true,
          metadata: { kind: "assistant_thinking", source: "provider_reasoning_delta", transient: false },
        }}
      />,
    );

    expect(screen.getByRole("button", { name: /正在思考/ })).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByText("我在检查相关文件。")).toBeInTheDocument();
  });

  it("renders transient status thinking as processing instead of provider reasoning", () => {
    render(
      <CleanThinkingBlock
        message={{
          id: "thinking-status",
          role: "assistant",
          content: "正在定位相关文件",
          streaming: true,
          metadata: { kind: "assistant_thinking", state: "thinking", transient: true },
        }}
      />,
    );

    const button = screen.getByRole("button", { name: /正在处理/ });
    expect(button).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("button", { name: /正在思考/ })).not.toBeInTheDocument();
  });

  it("renders expanded thinking markdown instead of raw source blocks", () => {
    const { container } = render(
      <CleanThinkingBlock
        message={{
          id: "thinking-md",
          role: "assistant",
          content: "# Plan\n\n- **Priority** item",
          metadata: { kind: "assistant_thinking" },
        }}
      />,
    );

    fireEvent.click(container.querySelector(".hc-thinking button") as HTMLElement);
    const detail = container.querySelector(".hc-thinking-detail") as HTMLElement;
    expect(detail).toBeInTheDocument();
    expect(detail.querySelector("pre")).not.toBeInTheDocument();
    expect(within(detail).getByRole("heading", { name: "Plan" })).toBeInTheDocument();
    expect(detail.querySelector("strong")).toHaveTextContent("Priority");
    expect(detail).not.toHaveTextContent("**Priority**");
  });

  it("renders collapsed thinking markdown inline instead of raw markers", () => {
    const { container } = render(
      <CleanThinkingBlock
        message={{
          id: "thinking-md-preview",
          role: "assistant",
          content: "**Inspecting files for README**",
          metadata: { kind: "assistant_thinking" },
        }}
      />,
    );

    const preview = container.querySelector(".hc-thinking em") as HTMLElement;
    expect(preview.querySelector("strong")).toHaveTextContent("Inspecting files for README");
    expect(preview).not.toHaveTextContent("**Inspecting");
  });

  it("renders slash command results as expandable detail nodes", async () => {
    const user = userEvent.setup();
    const onCopyRuntimeText = vi.fn();

    render(
      <CleanSlashCommandBlock
        onCopyRuntimeText={onCopyRuntimeText}
        message={{
          id: "slash:status",
          role: "assistant",
          content: "**运行时：** 本地运行时已连接\n**模型：** gpt-5",
          metadata: {
            kind: "slash_command",
            command: "/status",
            summary: "本地运行时已连接",
            status: "completed",
          },
        }}
      />,
    );

    expect(screen.getByRole("button", { name: /\/status/ })).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText("命令详情")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /\/status/ }));
    expect(screen.getByText("命令详情")).toBeInTheDocument();
    const detail = screen.getByText("命令详情").closest("figure") as HTMLElement;
    expect(within(detail).getByText("模型：")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "复制" }));
    expect(onCopyRuntimeText).toHaveBeenCalledWith("/status result", expect.stringContaining("**模型：** gpt-5"));
  });

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
    const onCopyRuntimeText = vi.fn();
    const onOpenFile = vi.fn();
    render(
      <CleanRuntimeBlock
        onLoadPatch={onLoadPatch}
        onCopyRuntimeText={onCopyRuntimeText}
        onOpenFile={onOpenFile}
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

    expect(onOpenFile).toHaveBeenCalledWith("snake_game/rules.py");
    expect(onLoadPatch).not.toHaveBeenCalled();
    expect(screen.getAllByText("snake_game/rules.py").length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText("new_rules")).toBeInTheDocument();
    expect(screen.queryByText("new_game")).not.toBeInTheDocument();
    expect(screen.getByText("+1")).toBeInTheDocument();
    expect(screen.getByText("-1")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "1" }));
    expect(onOpenFile).toHaveBeenCalledWith("snake_game/rules.py:1");

    await user.click(screen.getByRole("button", { name: "复制" }));
    expect(onCopyRuntimeText).toHaveBeenCalledWith("文件差异", expect.stringContaining("+new_rules"));
    expect(onCopyRuntimeText).toHaveBeenCalledWith("文件差异", expect.not.stringContaining("+new_game"));
  });

  it("renders plan approvals as task panels instead of raw json", () => {
    render(
      <CleanRuntimeBlock
        item={{
          id: "approval:plan",
          kind: "approval",
          sourceId: "appr_plan",
          toolName: "plan",
          title: "计划审批",
          status: "pending",
          summary: "已拆分 2 个 swarm 子任务",
          rawDetail: JSON.stringify({
            goal: "多 agent 优化输出",
            orchestrationMode: "swarm",
            subtaskCount: 2,
            subtasks: [
              {
                id: "sub-0",
                title: "定位输出链路",
                description: "检查后端事件与前端回放路径。",
                agentType: "planner",
                dependencies: [],
              },
              {
                id: "sub-1",
                title: "收敛审批展示",
                description: "把计划审批渲染成子任务面板。",
                agentType: "worker",
                dependencies: ["sub-0"],
              },
            ],
          }),
          previewRows: [
            { label: "模式", value: "swarm" },
            { label: "子任务", value: "2" },
          ],
        }}
      />,
    );

    expect(screen.getByText("定位输出链路")).toBeInTheDocument();
    expect(screen.getByText("收敛审批展示")).toBeInTheDocument();
    expect(screen.getByText(/依赖前序任务/)).toBeInTheDocument();
    expect(screen.queryByText("sub-0")).not.toBeInTheDocument();
    expect(screen.queryByText("sub-1")).not.toBeInTheDocument();
    expect(screen.queryByText(/"subtasks"/)).not.toBeInTheDocument();
  });

  it("renders completion review evidence without exposing json by default", () => {
    render(
      <CleanRuntimeBlock
        item={{
          id: "approval:completion",
          kind: "approval",
          sourceId: "appr_done",
          toolName: "completion_review",
          title: "完成确认",
          status: "approved",
          summary: "Completion review required",
          rawDetail: JSON.stringify({
            completionEvidence: {
              counts: { changedFiles: 1 },
            },
          }),
          completionEvidence: {
            summary: "缺少实际验证命令。",
            metrics: [{ label: "files", value: "1" }],
            issues: ["Report the exact command run."],
          },
        }}
      />,
    );

    expect(screen.getByText("缺少实际验证命令。")).toBeInTheDocument();
    expect(screen.getByText("Report the exact command run.")).toBeInTheDocument();
    expect(screen.queryByText(/"completionEvidence"/)).not.toBeInTheDocument();
  });

  it("does not expose path-like text from non-patch approvals as patch files", async () => {
    const user = userEvent.setup();
    const onLoadPatch = vi.fn();
    render(
      <CleanRuntimeBlock
        onLoadPatch={onLoadPatch}
        item={{
          id: "approval:plan-path",
          kind: "approval",
          sourceId: "appr_cannot_be_patch",
          toolName: "plan",
          title: "计划审批",
          status: "pending",
          code: "plan",
          rawDetail: JSON.stringify({ goal: "检查 snake_game/game.py", subtasks: [] }),
        }}
      />,
    );

    expect(screen.queryByRole("button", { name: /snake_game\/game\.py/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /查看原始详情|查看详情/ })).not.toBeInTheDocument();
    expect(onLoadPatch).not.toHaveBeenCalled();
  });

  it("syncs diff tabs with the right file pane", async () => {
    const user = userEvent.setup();
    const onOpenFile = vi.fn();
    render(
      <CleanRuntimeBlock
        onOpenFile={onOpenFile}
        item={{
          id: "patch:tabs",
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
        }}
      />,
    );

    await user.click(screen.getByRole("button", { name: "查看全部差异" }));
    const diffTabs = screen.getByLabelText("差异文件");
    await user.click(within(diffTabs).getByRole("button", { name: /snake_game\/rules\.py/ }));

    expect(onOpenFile).toHaveBeenCalledWith("snake_game/rules.py");
    expect(screen.getByText("new_rules")).toBeInTheDocument();
    expect(screen.queryByText("new_game")).not.toBeInTheDocument();
  });

  it("exposes patch file list copy and revert actions", async () => {
    const user = userEvent.setup();
    const onCopyRuntimeText = vi.fn();
    const onQuoteMessage = vi.fn();
    const onRevertTaskChanges = vi.fn();

    render(
      <CleanRuntimeBlock
        onCopyRuntimeText={onCopyRuntimeText}
        onQuoteMessage={onQuoteMessage}
        onRevertTaskChanges={onRevertTaskChanges}
        item={{
          id: "patch:actions",
          kind: "patch",
          taskId: "task_1",
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
    await user.click(screen.getByRole("button", { name: /撤销本轮/ }));
    expect(onRevertTaskChanges).toHaveBeenCalledWith("task_1");

    await user.click(screen.getByRole("button", { name: /审查改动/ }));
    expect(onQuoteMessage).toHaveBeenCalledWith(expect.stringContaining("请审查这轮改动：Update snake_game files"));
    expect(onQuoteMessage).toHaveBeenCalledWith(expect.stringContaining("snake_game/rules.py"));
  });

  it("keeps revert disabled until a patch is applied", () => {
    const onRevertTaskChanges = vi.fn();

    render(
      <CleanRuntimeBlock
        onRevertTaskChanges={onRevertTaskChanges}
        item={{
          id: "patch:proposed",
          kind: "patch",
          taskId: "task_1",
          title: "Update snake_game files",
          status: "proposed",
          code: "modified snake_game/game.py (+2/-4)",
        }}
      />,
    );

    expect(screen.getByRole("button", { name: /撤销本轮/ })).toBeDisabled();
  });

  it("passes patch review actions through activity runtime items", async () => {
    const user = userEvent.setup();
    const onQuoteMessage = vi.fn();
    const onOpenFile = vi.fn();

    render(
      <CleanActivityItem
        item={{
          id: "runtime:patch:activity",
          kind: "runtime",
          order: 1,
          runtime: {
            id: "patch:activity",
            kind: "patch",
            title: "Update snake_game files",
            status: "applied",
            code: "modified snake_game/game.py (+2/-4)",
          },
        }}
        onOpenFile={onOpenFile}
        onQuoteMessage={onQuoteMessage}
      />,
    );

    await user.click(screen.getByRole("button", { name: /snake_game\/game\.py/ }));
    expect(onOpenFile).toHaveBeenCalledWith("snake_game/game.py");

    await user.click(screen.getByRole("button", { name: /审查改动/ }));
    expect(onQuoteMessage).toHaveBeenCalledWith(expect.stringContaining("请审查这轮改动：Update snake_game files"));
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

  it("renders message metadata attachments as lightweight chips", async () => {
    const user = userEvent.setup();
    const onCopyRuntimeText = vi.fn();

    render(
      <CleanActivityItem
        item={{
          id: "message:user:attachments",
          kind: "message",
          order: 1,
          message: {
            id: "user:attachments",
            role: "user",
            content: "参考这些文件",
            metadata: {
              attachments: [
                "D:/notes/design.md",
                { path: "D:/screenshots/layout.png", type: "image", name: "layout.png" },
              ],
            },
          },
        }}
        onCopyRuntimeText={onCopyRuntimeText}
      />,
    );

    expect(screen.getByText("design.md")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "layout.png" })).toHaveAttribute("src", "D:/screenshots/layout.png");

    await user.click(screen.getByRole("button", { name: /design\.md/ }));
    expect(onCopyRuntimeText).toHaveBeenCalledWith("附件路径", "D:/notes/design.md");
  });

  it("supports multi-image attachment preview navigation", async () => {
    const user = userEvent.setup();

    render(
      <CleanActivityItem
        item={{
          id: "message:user:image-gallery",
          kind: "message",
          order: 1,
          message: {
            id: "user:image-gallery",
            role: "user",
            content: "两张参考图",
            metadata: {
              attachments: [
                { path: "D:/screenshots/one.png", type: "image", name: "one.png" },
                { path: "D:/screenshots/two.png", type: "image", name: "two.png" },
              ],
            },
          },
        }}
      />,
    );

    await user.click(screen.getByRole("img", { name: "one.png" }));

    expect(screen.getByRole("dialog", { name: "图片预览" })).toBeInTheDocument();
    expect(screen.getByText("1/2")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "下一张图片" }));
    expect(screen.getByText("2/2")).toBeInTheDocument();
    expect(screen.getAllByRole("img", { name: "two.png" }).at(-1)).toHaveAttribute("src", "D:/screenshots/two.png");

    fireEvent.keyDown(document, { key: "ArrowLeft" });
    expect(screen.getByText("1/2")).toBeInTheDocument();

    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog", { name: "图片预览" })).not.toBeInTheDocument();
  });

  it("extracts inline image paths from assistant text into a gallery", () => {
    render(
      <CleanActivityItem
        item={{
          id: "message:assistant:image-path",
          kind: "message",
          order: 1,
          message: {
            id: "assistant:image-path",
            role: "assistant",
            content: "截图已生成：D:\\tmp\\result.png",
          },
        }}
      />,
    );

    expect(screen.getByRole("img", { name: "result.png" })).toHaveAttribute("src", "D:/tmp/result.png");
  });

  it("does not duplicate markdown image blocks as attachment thumbnails", () => {
    render(
      <CleanActivityItem
        item={{
          id: "message:assistant:markdown-image",
          kind: "message",
          order: 1,
          message: {
            id: "assistant:markdown-image",
            role: "assistant",
            content: "![Preview](D:\\tmp\\preview.png)",
          },
        }}
      />,
    );

    expect(screen.getAllByRole("img", { name: "Preview" })).toHaveLength(1);
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

  it("renders user messages with markdown paragraphs instead of a single cramped line", () => {
    const { container } = render(
      <CleanActivityItem
        item={{
          id: "message:user:paragraphs",
          kind: "message",
          order: 1,
          message: {
            id: "user:paragraphs",
            role: "user",
            content: "第一段\n\n第二段\n保留换行",
          },
        }}
      />,
    );

    const paragraphs = container.querySelectorAll(".hc-user .hc-markdown p");
    expect(paragraphs).toHaveLength(2);
    expect(paragraphs[0]).toHaveTextContent("第一段");
    expect(paragraphs[1]?.textContent).toBe("第二段\n保留换行");
  });

  it("submits overflow transcript actions when handlers are available", async () => {
    const user = userEvent.setup();
    const onCopyRuntimeText = vi.fn();
    const onContinueFromMessage = vi.fn();
    const onBranchFromMessage = vi.fn();
    const onDeleteMessage = vi.fn();
    const message = {
      id: "m-overflow",
      role: "assistant" as const,
      content: "下一步可以拆出 rules.py。",
    };

    render(
      <CleanActivityItem
        item={{
          id: "message:overflow",
          kind: "message",
          order: 1,
          message,
        }}
        onCopyRuntimeText={onCopyRuntimeText}
        onQuoteMessage={vi.fn()}
        onContinueFromMessage={onContinueFromMessage}
        onBranchFromMessage={onBranchFromMessage}
        onDeleteMessage={onDeleteMessage}
      />,
    );

    await user.click(screen.getByRole("button", { name: "更多" }));

    await user.click(screen.getByRole("button", { name: /复制为 Markdown/ }));
    expect(onCopyRuntimeText).toHaveBeenCalledWith("Markdown 引用", expect.stringContaining("> 助手："));

    await user.click(screen.getByRole("button", { name: "更多" }));
    await user.click(screen.getByRole("button", { name: /从这里继续/ }));
    expect(onContinueFromMessage).toHaveBeenCalledWith(message);

    await user.click(screen.getByRole("button", { name: "更多" }));
    await user.click(screen.getByRole("button", { name: /从这里分支/ }));
    expect(onBranchFromMessage).toHaveBeenCalledWith(message);

    await user.click(screen.getByRole("button", { name: "更多" }));
    await user.click(screen.getByRole("button", { name: /删除消息/ }));
    expect(onDeleteMessage).toHaveBeenCalledWith(message);
  });

  it("renders ask-user events as an interactive decision node", async () => {
    const user = userEvent.setup();
    const onSubmitUserQuestionAnswer = vi.fn();

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
        onSubmitUserQuestionAnswer={onSubmitUserQuestionAnswer}
      />,
    );

    expect(screen.getByText("需要你确认")).toBeInTheDocument();
    expect(screen.getByText("要继续拆分渲染层吗？")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /继续/ }));
    expect(onSubmitUserQuestionAnswer).toHaveBeenCalledWith(
      expect.objectContaining({ id: "ask1" }),
      expect.stringContaining("选择：继续"),
    );

    await user.type(screen.getByPlaceholderText("补充说明或直接回答..."), "继续，但先收窄到渲染层。");
    await user.click(screen.getByRole("button", { name: "提交回答" }));
    expect(onSubmitUserQuestionAnswer).toHaveBeenLastCalledWith(
      expect.objectContaining({ id: "ask1" }),
      "继续，但先收窄到渲染层。",
    );
  });

  it("disables ask-user answers after the question is answered", async () => {
    const user = userEvent.setup();
    const onSubmitUserQuestionAnswer = vi.fn();

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
              question: "Which list should I make?",
              options: [
                { label: "Status list", description: "Use current state." },
              ],
            },
          },
        }}
        answeredQuestionIds={new Set(["ask1"])}
        onSubmitUserQuestionAnswer={onSubmitUserQuestionAnswer}
      />,
    );

    const optionButton = screen.getByRole("button", { name: /Status list/ });
    expect(optionButton).toBeDisabled();
    expect(screen.getByPlaceholderText("等待回答提交接口")).toBeDisabled();
    await user.click(optionButton);
    expect(onSubmitUserQuestionAnswer).not.toHaveBeenCalled();
  });

  it("submits computer-use permission decisions", async () => {
    const user = userEvent.setup();
    const onApprove = vi.fn();
    const onReject = vi.fn();

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
              approvalId: "appr_computer",
              app: "VS Code",
              action: "click",
              selector: "Run button",
              x: 320,
              y: 180,
              url: "http://localhost:5173",
              pageId: "page_1",
              permission: "点击运行按钮",
            },
          },
        }}
        onApprove={onApprove}
        onReject={onReject}
      />,
    );

    expect(screen.getByText("Computer Use 权限")).toBeInTheDocument();
    const details = screen.getByLabelText("Computer Use 权限详情");
    expect(within(details).getByText("动作")).toBeInTheDocument();
    expect(within(details).getByText("click")).toBeInTheDocument();
    expect(within(details).getByText("目标")).toBeInTheDocument();
    expect(within(details).getByText("Run button")).toBeInTheDocument();
    expect(within(details).getByText("坐标")).toBeInTheDocument();
    expect(within(details).getByText("320, 180")).toBeInTheDocument();
    expect(within(details).getByText("URL")).toBeInTheDocument();
    expect(within(details).getByText("http://localhost:5173")).toBeInTheDocument();
    expect(within(details).getByText("Page")).toBeInTheDocument();
    expect(within(details).getByText("page_1")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "允许" }));
    await user.click(screen.getByRole("button", { name: "拒绝" }));
    expect(onApprove).toHaveBeenCalledWith("appr_computer");
    expect(onReject).toHaveBeenCalledWith("appr_computer");
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
    expect(screen.queryByText("工具详情")).not.toBeInTheDocument();
    expect(screen.queryByText(/"exitCode"/)).not.toBeInTheDocument();
    expect(onCopyRuntimeText).not.toHaveBeenCalled();
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

  it("prefers structured result summaries over streamed activity text", () => {
    render(
      <CleanToolMessageBlock
        message={{
          id: "tool-search",
          role: "assistant",
          content: "",
          toolName: "search_files",
          status: "completed",
          metadata: {
            inputText: JSON.stringify({ query: "needle" }),
            resultSummary: "found 2 match(es) for needle: src/app.ts",
            resultText: "过程\n正在搜索文件：search needle\n\n结果预览\n命中: 2 项\n样例: src/app.ts",
          },
        }}
      />,
    );

    expect(screen.getByText("found 2 match(es) for needle: src/app.ts")).toBeInTheDocument();
    expect(screen.queryByText(/正在搜索文件/)).not.toBeInTheDocument();
  });

  it("prefers display fields and previews over successful raw tool json", async () => {
    const user = userEvent.setup();
    render(
      <CleanToolMessageBlock
        message={{
          id: "tool-display",
          role: "assistant",
          content: JSON.stringify({ taskId: "task_1", provider: "yuanbao" }),
          toolName: "read_file",
          status: "completed",
          metadata: {
            kind: "tool_activity",
            inputText: JSON.stringify({ path: "src/app.ts", content: "raw file content" }),
            resultText: JSON.stringify({ status: "completed", content: "raw result content" }),
            displayTitle: "Read app shell",
            displayTarget: "src/app.ts",
            displaySummary: "Read app shell summary",
            resultPreview: [{ label: "File", value: "src/app.ts" }],
          },
        }}
      />,
    );

    expect(screen.getByText("Read app shell")).toBeInTheDocument();
    expect(screen.getByText("Read app shell summary")).toBeInTheDocument();
    expect(screen.getByText("src/app.ts")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /Read app shell/ }));
    expect(screen.getByText("File")).toBeInTheDocument();
    expect(screen.queryByText(/raw file content/)).not.toBeInTheDocument();
    expect(screen.queryByText(/raw result content/)).not.toBeInTheDocument();
    expect(screen.queryByText(/"provider"/)).not.toBeInTheDocument();
  });

  it("renders blocked inline tool rows as warning status instead of failure", () => {
    render(
      <CleanToolMessageBlock
        message={{
          id: "tool-blocked",
          role: "assistant",
          content: "",
          toolName: "write_file",
          status: "blocked",
          metadata: {
            inputText: JSON.stringify({ path: "snake_game/game.py" }),
            resultSummary: "blocked by policy",
            status: "blocked",
            isError: true,
          },
        }}
      />,
    );

    const row = screen.getByText("已阻塞").closest("section") as HTMLElement;
    expect(row).toHaveAttribute("data-tone", "warning");
    expect(screen.queryByText("失败")).not.toBeInTheDocument();
  });

  it("shows backend tool duration on inline tool rows", () => {
    render(
      <CleanToolMessageBlock
        message={{
          id: "tool-duration",
          role: "assistant",
          content: "",
          toolName: "run_command",
          status: "completed",
          metadata: {
            inputText: JSON.stringify({ command: "npm test" }),
            resultSummary: "exit 0: ok",
            durationMs: 1250,
          },
        }}
      />,
    );

    expect(screen.getByText("1.3s")).toBeInTheDocument();
  });

  it("uses backend target metadata for started command tool titles", () => {
    render(
      <CleanToolMessageBlock
        message={{
          id: "tool-started-command",
          role: "assistant",
          content: "",
          toolName: "run_command",
          status: "streaming",
          streaming: true,
          metadata: {
            kind: "tool_use",
            toolUseId: "call_command",
            target: "npm run dev",
            inputSummary: "npm run dev",
          },
        }}
      />,
    );

    expect(screen.getByText("运行 npm run dev")).toBeInTheDocument();
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

  it("does not expose structured permission request JSON as visible details", () => {
    render(
      <CleanPermissionMessageBlock
        message={{
          id: "permission-json",
          role: "assistant",
          content: JSON.stringify({
            content: "<!doctype html><html>secret page</html>",
            overwrite: true,
            path: "index.html",
          }),
          toolName: "write_file",
          metadata: {
            requestId: "approval-json",
            parametersPreview: JSON.stringify({ path: "index.html" }),
            changedPaths: ["index.html"],
          },
        }}
      />,
    );

    expect(screen.getByText("写入 index.html 需要确认")).toBeInTheDocument();
    expect(screen.getByText("index.html")).toBeInTheDocument();
    expect(screen.queryByText(/secret page/)).not.toBeInTheDocument();
    expect(screen.queryByText(/overwrite/)).not.toBeInTheDocument();
    expect(screen.queryByText(/"content"/)).not.toBeInTheDocument();
  });

  it("shows structured permission preview rows and changed files", () => {
    render(
      <CleanPermissionMessageBlock
        onApproveAlways={vi.fn()}
        message={{
          id: "permission2",
          role: "assistant",
          content: "",
          toolName: "apply_patch",
          metadata: {
            requestId: "approval-2",
            approvalKind: "apply_patch",
            previewRows: [
              { label: "摘要", value: "Update rules" },
              { label: "原因", value: "writes files" },
            ],
            changedPaths: ["src/rules.ts", "src/rules.test.ts"],
          },
        }}
      />,
    );

    expect(screen.getByText("摘要")).toBeInTheDocument();
    expect(screen.getByText("Update rules")).toBeInTheDocument();
    expect(screen.getByText("src/rules.ts")).toBeInTheDocument();
    expect(screen.getByText("src/rules.test.ts")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "始终允许" })).not.toBeDisabled();
  });

  it("renders approval runtime items as dedicated approval nodes", async () => {
    const user = userEvent.setup();
    const onApprove = vi.fn();
    const onApproveAlways = vi.fn();
    const onReject = vi.fn();
    const onLoadPatch = vi.fn();
    const onOpenFile = vi.fn();

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
        onApproveAlways={onApproveAlways}
        onReject={onReject}
        onLoadPatch={onLoadPatch}
        onOpenFile={onOpenFile}
      />,
    );

    expect(screen.getByText("需要确认")).toBeInTheDocument();
    expect(screen.getByText("修改 snake_game/rules.py")).toBeInTheDocument();
    expect(screen.getByText("snake_game/rules.py")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "允许一次" }));
    await user.click(screen.getByRole("button", { name: /snake_game\/rules\.py/ }));
    expect(onOpenFile).toHaveBeenCalledWith("snake_game/rules.py");
    expect(onLoadPatch).not.toHaveBeenCalled();
    expect(onApprove).toHaveBeenCalledWith("approval-1");

    await user.click(screen.getByRole("button", { name: "拒绝" }));
    expect(onReject).toHaveBeenCalledWith("approval-1");

    await user.click(screen.getByRole("button", { name: "始终允许" }));
    expect(onApproveAlways).toHaveBeenCalledWith("approval-1");
  });

  it("loads approval file diffs only with a real patch id", async () => {
    const user = userEvent.setup();
    const onLoadPatch = vi.fn();
    const onOpenFile = vi.fn();

    render(
      <CleanRuntimeBlock
        item={{
          id: "approval:patch",
          kind: "approval",
          sourceId: "approval-1",
          patchId: "patch-real",
          toolName: "apply_patch",
          title: "apply_patch",
          status: "pending",
          code: JSON.stringify({ path: "snake_game/rules.py" }),
          rawDetail: "modified snake_game/rules.py (+2/-1)",
        }}
        onLoadPatch={onLoadPatch}
        onOpenFile={onOpenFile}
      />,
    );

    await user.click(screen.getByRole("button", { name: /snake_game\/rules\.py/ }));
    expect(onOpenFile).toHaveBeenCalledWith("snake_game/rules.py");
    expect(onLoadPatch).toHaveBeenCalledWith("patch-real");
  });

  it("shows structured approval preview rows for non-file approvals", () => {
    render(
      <CleanRuntimeBlock
        item={{
          id: "approval:command",
          kind: "approval",
          sourceId: "approval-command",
          title: "run_command",
          status: "pending",
          riskLevel: "medium",
          previewRows: [
            { label: "命令", value: "npm run typecheck" },
            { label: "目录", value: "app" },
          ],
        }}
      />,
    );

    expect(screen.getByText("命令")).toBeInTheDocument();
    expect(screen.getByText("npm run typecheck")).toBeInTheDocument();
    expect(screen.getByText("目录")).toBeInTheDocument();
    expect(screen.getByText("app")).toBeInTheDocument();
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

    expect(screen.getByText(/已处理 2 项操作/)).toBeInTheDocument();
    expect(screen.getByText(/读取上下文 2，均已收起为轻量日志/)).toBeInTheDocument();
    expect(screen.queryByText("我在读取项目上下文，低价值的读文件和目录检查已折叠收纳。")).not.toBeInTheDocument();
    expect(screen.queryByText("查看 snake_game")).not.toBeInTheDocument();
    expect(screen.queryByText("读取 snake_game/game.py")).not.toBeInTheDocument();
    const groups = within(screen.getByLabelText("操作类别"));
    expect(groups.getByText("读取上下文")).toBeInTheDocument();
    expect(groups.getByText("2")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /复制摘要/ })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /已处理 2 项操作/ }));
    expect(screen.getByText("我在读取项目上下文，低价值的读文件和目录检查已折叠收纳。")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /复制摘要/ }));
    expect(onCopyRuntimeText).toHaveBeenCalledWith("工作日志摘要", expect.stringContaining("#1 · 查看 snake_game · 已完成"));

    expect(screen.getByText("查看 snake_game")).toBeInTheDocument();
    expect(screen.getByText("读取 snake_game/game.py")).toBeInTheDocument();
    expect(screen.getAllByText("读取上下文").length).toBeGreaterThanOrEqual(2);

    await user.click(screen.getByRole("button", { name: /读取 snake_game\/game\.py/ }));
    expect(screen.queryByText("class Game: pass")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "复制" })).not.toBeInTheDocument();
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

    await user.click(screen.getByRole("button", { name: /已处理 2 项操作/ }));
    expect(screen.getByText("1 个子步骤")).toBeInTheDocument();
    expect(screen.getByText("读取 app/src/ui.tsx").closest(".hc-worklog-row")).toHaveAttribute("data-depth", "1");
  });

  it("orders same-batch worklog tools by backend tool index", async () => {
    const user = userEvent.setup();

    render(
      <CleanWorklogBlock
        items={[
          {
            id: "tool:second",
            kind: "tool",
            title: "read_file",
            status: "completed",
            toolName: "read_file",
            toolUseId: "tc_2",
            toolGroupId: "tgrp_1",
            toolIndex: 1,
            toolTotal: 2,
            time: 20,
            code: JSON.stringify({ path: "app/src/second.ts" }),
          },
          {
            id: "tool:first",
            kind: "tool",
            title: "search_files",
            status: "completed",
            toolName: "search_files",
            toolUseId: "tc_1",
            toolGroupId: "tgrp_1",
            toolIndex: 0,
            toolTotal: 2,
            time: 30,
            code: JSON.stringify({ query: "needle" }),
          },
        ]}
      />,
    );

    await user.click(screen.getByRole("button", { name: /已处理 2 项操作/ }));
    const rows = screen.getAllByRole("button", { name: /搜索 needle|读取 app\/src\/second\.ts/ });
    expect(rows.map((row) => row.textContent)).toEqual([
      expect.stringContaining("搜索 needle"),
      expect.stringContaining("读取 app/src/second.ts"),
    ]);
  });

  it("renders inferred search to read parent-child worklog trees", async () => {
    const user = userEvent.setup();

    render(
      <CleanWorklogBlock
        items={[
          {
            id: "tool:search",
            kind: "tool",
            title: "search_files",
            status: "completed",
            toolName: "search_files",
            toolUseId: "call_search",
            toolGroupId: "tgrp_1",
            toolIndex: 0,
            toolTotal: 2,
            toolSemanticParentId: "group:tgrp_1:phase:search",
            toolSemanticParentLabel: "搜索",
            code: JSON.stringify({ query: "needle" }),
          },
          {
            id: "tool:read",
            kind: "tool",
            title: "read_file",
            status: "completed",
            toolName: "read_file",
            toolUseId: "call_read",
            parentToolUseId: "call_search",
            toolGroupId: "tgrp_1",
            toolIndex: 1,
            toolTotal: 2,
            toolSemanticParentId: "group:tgrp_1:phase:context_read",
            toolSemanticParentLabel: "读取上下文",
            code: JSON.stringify({ path: "app/src/target.ts" }),
          },
        ]}
      />,
    );

    await user.click(screen.getByRole("button", { name: /已处理 2 项操作/ }));
    expect(screen.getByText("1 个子步骤")).toBeInTheDocument();
    expect(screen.getByText("读取 app/src/target.ts").closest(".hc-worklog-row")).toHaveAttribute("data-depth", "1");
  });

  it("keeps parented worklog tools inside the root phase only", () => {
    const { container } = render(
      <CleanWorklogBlock
        items={[
          {
            id: "tool:search:root",
            kind: "tool",
            title: "search_files",
            status: "completed",
            toolName: "search_files",
            toolUseId: "call_search_root",
            toolGroupId: "tgrp_root",
            toolSemanticParentId: "group:tgrp_root:phase:search",
            toolSemanticParentLabel: "Search",
            code: JSON.stringify({ query: "README" }),
          },
          {
            id: "tool:read:child",
            kind: "tool",
            title: "read_file",
            status: "completed",
            toolName: "read_file",
            toolUseId: "call_read_child",
            parentToolUseId: "call_search_root",
            toolGroupId: "tgrp_root",
            toolSemanticParentId: "group:tgrp_root:phase:context_read",
            toolSemanticParentLabel: "Read Context",
            code: JSON.stringify({ path: "snake_game/README.md" }),
          },
        ]}
      />,
    );

    fireEvent.click(container.querySelector(".hc-worklog-head") as HTMLElement);
    const phases = Array.from(container.querySelectorAll(".hc-worklog-phase"));
    expect(phases).toHaveLength(1);
    expect(phases[0]).toHaveTextContent("snake_game/README.md");
    expect(container.querySelector('[data-depth="1"]')).toHaveTextContent("snake_game/README.md");
  });

  it("renders inferred file change review parent-child worklog trees", async () => {
    const { container } = render(
      <CleanWorklogBlock
        items={[
          {
            id: "tool:write",
            kind: "tool",
            title: "write_file",
            status: "completed",
            toolName: "write_file",
            toolUseId: "call_write",
            toolGroupId: "tgrp_1",
            toolIndex: 0,
            toolTotal: 2,
            toolCategory: "file_change",
            toolSemanticParentId: "group:tgrp_1:phase:file_change",
            toolSemanticParentLabel: "鏂囦欢鏀瑰姩",
            code: JSON.stringify({ path: "app/src/target.ts", content: "updated" }),
          },
          {
            id: "tool:verify",
            kind: "tool",
            title: "run_command",
            status: "completed",
            toolName: "run_command",
            toolUseId: "call_verify",
            parentToolUseId: "call_write",
            toolGroupId: "tgrp_1",
            toolIndex: 1,
            toolTotal: 2,
            toolCategory: "verification",
            toolSemanticParentId: "group:tgrp_1:phase:verification",
            toolSemanticParentLabel: "楠岃瘉",
            code: JSON.stringify({ command: "npm run typecheck" }),
          },
          {
            id: "tool:git",
            kind: "tool",
            title: "git_status",
            status: "completed",
            toolName: "git_status",
            toolUseId: "call_status",
            parentToolUseId: "call_write",
            toolGroupId: "tgrp_1",
            toolIndex: 2,
            toolTotal: 3,
            toolCategory: "git",
            toolSemanticParentId: "group:tgrp_1:phase:git",
            code: JSON.stringify({}),
          },
        ]}
      />,
    );

    fireEvent.click(container.querySelector(".hc-worklog-head") as HTMLElement);
    const verifyRow = Array.from(container.querySelectorAll(".hc-worklog-row")).find((row) =>
      row.textContent?.includes("npm run typecheck"),
    );
    expect(verifyRow).toHaveAttribute("data-depth", "1");
    expect(verifyRow?.textContent).toContain("npm run typecheck");
    const gitRow = Array.from(container.querySelectorAll(".hc-worklog-row")).find((row) =>
      row.textContent?.includes("Git"),
    );
    expect(gitRow).toHaveAttribute("data-depth", "1");
    const phases = Array.from(container.querySelectorAll(".hc-worklog-phase"));
    expect(phases).toHaveLength(1);
    expect(phases[0]).toHaveTextContent("npm run typecheck");
    expect(phases[0]).toHaveTextContent("Git");
  });

  it("groups expanded worklog rows into semantic phases", async () => {
    const user = userEvent.setup();

    render(
      <CleanWorklogBlock
        items={[
          {
            id: "tool:read",
            kind: "tool",
            title: "read_file",
            status: "completed",
            toolName: "read_file",
            code: JSON.stringify({ path: "app/src/ui.tsx" }),
          },
          {
            id: "tool:git",
            kind: "tool",
            title: "git_status",
            status: "completed",
            toolName: "git_status",
            summary: "main: 1 changed file(s): app/src/ui.tsx",
          },
          {
            id: "command:test",
            kind: "command",
            title: "npm run typecheck",
            status: "completed",
            code: "npm run typecheck",
            toolCategory: "verification",
          },
        ]}
      />,
    );

    await user.click(screen.getByRole("button", { name: /已处理 3 项操作/ }));
    const phases = screen.getAllByRole("region").filter((node) => node.classList.contains("hc-worklog-phase"));
    expect(phases.map((phase) => phase.getAttribute("aria-label"))).toEqual([
      "阶段：验证",
      "阶段：Git 检查",
      "阶段：读取上下文",
    ]);
    expect(within(phases[0] as HTMLElement).getByText("运行命令")).toBeInTheDocument();
    expect(within(phases[1] as HTMLElement).getByText("查看 Git 状态")).toBeInTheDocument();
    expect(within(phases[2] as HTMLElement).getByText("读取 app/src/ui.tsx")).toBeInTheDocument();
  });

  it("summarizes verification commands as a semantic worklog phase", async () => {
    const user = userEvent.setup();

    render(
      <CleanWorklogBlock
        items={[
          {
            id: "tool:read",
            kind: "tool",
            title: "read_file",
            status: "completed",
            toolName: "read_file",
            code: JSON.stringify({ path: "app/src/ui.tsx" }),
          },
          {
            id: "command:test",
            kind: "command",
            title: "npm run typecheck",
            status: "completed",
            code: "npm run typecheck",
          },
        ]}
      />,
    );

    expect(screen.getByText(/验证 1、读取上下文 1，1 项低噪声/)).toBeInTheDocument();
    const groups = within(screen.getByLabelText("操作类别"));
    expect(groups.getByText("验证")).toBeInTheDocument();
    expect(groups.getByText("读取上下文")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /已处理 2 项操作/ }));
    expect(screen.getByText("我在验证当前结果，并把测试、构建或检查输出收在下面。")).toBeInTheDocument();
    const rows = screen.getAllByText("验证").map((node) => node.closest("article")).filter(Boolean);
    expect(rows.some((row) => row?.getAttribute("data-kind") === "command")).toBe(true);
  });

  it("summarizes shell context commands as quiet worklog phases", async () => {
    const user = userEvent.setup();

    render(
      <CleanWorklogBlock
        items={[
          {
            id: "command:rg",
            kind: "command",
            title: "run_command",
            status: "completed",
            code: JSON.stringify({ command: "rg -n CleanWorklogBlock app/src/ui/haha-clean" }),
          },
          {
            id: "command:psread",
            kind: "command",
            title: "PowerShell",
            status: "completed",
            code: JSON.stringify({ command: "Get-Content app/src/ui/haha-clean/conversation/CleanConversation.tsx" }),
          },
          {
            id: "command:gitdiff",
            kind: "command",
            title: "run_command",
            status: "completed",
            code: JSON.stringify({ command: "git diff -- app/src/ui/haha-clean/clean.css" }),
          },
        ]}
      />,
    );

    expect(screen.getByText(/Git 检查 1、搜索 1、读取上下文 1，均已收起为轻量日志/)).toBeInTheDocument();
    const groups = within(screen.getByLabelText("操作类别"));
    expect(groups.getByText("Git 检查")).toBeInTheDocument();
    expect(groups.getByText("搜索")).toBeInTheDocument();
    expect(groups.getByText("读取上下文")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /已处理 3 项操作/ }));
    const phases = screen.getAllByRole("region").filter((node) => node.classList.contains("hc-worklog-phase"));
    expect(phases.map((phase) => phase.getAttribute("aria-label"))).toEqual([
      "阶段：Git 检查",
      "阶段：搜索",
      "阶段：读取上下文",
    ]);
    expect(within(phases[0] as HTMLElement).getByText(/git diff/)).toBeInTheDocument();
    expect(within(phases[1] as HTMLElement).getByText(/rg -n CleanWorklogBlock/)).toBeInTheDocument();
    expect(within(phases[2] as HTMLElement).getByText(/Get-Content app\/src\/ui\/haha-clean\/conversation\/CleanConversation\.tsx/)).toBeInTheDocument();
  });

  it("prefers backend toolCategory when grouping worklog phases", async () => {
    const user = userEvent.setup();

    render(
      <CleanWorklogBlock
        items={[
          {
            id: "command:custom",
            kind: "command",
            title: "custom-ci-script",
            status: "completed",
            code: "custom-ci-script",
            toolCategory: "verification",
          },
        ]}
      />,
    );

    expect(within(screen.getByLabelText("操作类别")).getByText("验证")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /已处理 1 项操作/ }));
    expect(screen.getByText("我在验证当前结果，并把测试、构建或检查输出收在下面。")).toBeInTheDocument();
    const row = screen.getByRole("button", { name: /运行命令/ }).closest("article");
    expect(row).toHaveAttribute("data-kind", "command");
    expect(within(row as HTMLElement).getByText("验证")).toBeInTheDocument();
  });

  it("prefers backend toolPhaseLabel before category fallback", async () => {
    const user = userEvent.setup();

    render(
      <CleanWorklogBlock
        items={[
          {
            id: "tool:phase",
            kind: "tool",
            title: "custom_probe",
            status: "completed",
            toolName: "custom_probe",
            toolCategory: "context_read",
            toolPhaseLabel: "验证",
          },
        ]}
      />,
    );

    expect(within(screen.getByLabelText("操作类别")).getByText("验证")).toBeInTheDocument();
    expect(within(screen.getByLabelText("操作类别")).queryByText("读取上下文")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /已处理 1 项操作/ }));
    expect(screen.getByRole("region", { name: "阶段：验证" })).toBeInTheDocument();
  });

  it("prefers backend semantic parent label before phase fallback", async () => {
    const user = userEvent.setup();

    render(
      <CleanWorklogBlock
        items={[
          {
            id: "tool:semantic",
            kind: "tool",
            title: "custom_probe",
            status: "completed",
            toolName: "custom_probe",
            toolCategory: "context_read",
            toolPhaseLabel: "读取上下文",
            toolSemanticParentId: "phase:verification",
            toolSemanticParentLabel: "验证",
          },
        ]}
      />,
    );

    expect(within(screen.getByLabelText("操作类别")).getByText("验证")).toBeInTheDocument();
    expect(within(screen.getByLabelText("操作类别")).queryByText("读取上下文")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /已处理 1 项操作/ }));
    expect(screen.getByRole("region", { name: "阶段：验证" })).toBeInTheDocument();
  });

  it("keeps separate semantic parent ids as separate expanded worklog phases", async () => {
    const user = userEvent.setup();

    render(
      <CleanWorklogBlock
        items={[
          {
            id: "tool:search:1",
            kind: "tool",
            title: "search_files",
            status: "completed",
            toolName: "search_files",
            toolSemanticParentId: "group:tgrp_1:phase:search",
            toolSemanticParentLabel: "搜索",
          },
          {
            id: "tool:search:2",
            kind: "tool",
            title: "search_files",
            status: "completed",
            toolName: "search_files",
            toolSemanticParentId: "group:tgrp_2:phase:search",
            toolSemanticParentLabel: "搜索",
          },
        ]}
      />,
    );

    await user.click(screen.getByRole("button", { name: /已处理 2 项操作/ }));
    const searchPhases = screen
      .getAllByRole("region", { name: "阶段：搜索" })
      .filter((node) => node.classList.contains("hc-worklog-phase"));
    expect(searchPhases).toHaveLength(2);
    expect(within(searchPhases[0] as HTMLElement).getByText("语义阶段")).toBeInTheDocument();
    expect(within(searchPhases[1] as HTMLElement).getByText("语义阶段")).toBeInTheDocument();
  });

  it("uses Chinese file status and accurate copy labels in runtime details", async () => {
    const user = userEvent.setup();
    const onCopyRuntimeText = vi.fn();

    render(
      <CleanRuntimeBlock
        onCopyRuntimeText={onCopyRuntimeText}
        item={{
          id: "tool:write",
          kind: "tool",
          title: "write_file",
          status: "completed",
          toolName: "write_file",
          code: "modified snake_game/rules.py (+2/-1)",
          rawDetail: "updated content",
        }}
      />,
    );

    await user.click(screen.getByRole("button", { name: /写入 snake_game\/rules\.py/ }));
    expect(screen.getByText("修改 +2 -1")).toBeInTheDocument();
    expect(screen.queryByText("updated content")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "复制" })).not.toBeInTheDocument();
  });

  it("shows structured tool result previews in expanded worklog rows", async () => {
    const user = userEvent.setup();

    render(
      <CleanWorklogBlock
        items={[
          {
            id: "tool:list",
            kind: "tool",
            title: "查看目录",
            status: "completed",
            toolName: "list_dir",
            summary: "listed 2 item(s) in src: src/app.ts, src/ui.tsx",
            toolPhaseLabel: "读取上下文",
            previewRows: [
              { label: "目录", value: "src" },
              { label: "样例", value: "src/app.ts, src/ui.tsx" },
            ],
          },
        ]}
      />,
    );

    await user.click(screen.getByRole("button", { name: /已处理 1 项操作/ }));
    await user.click(screen.getByRole("button", { name: /查看目录/ }));
    const preview = screen.getByLabelText("工具结果预览");
    expect(within(preview).getByText("目录")).toBeInTheDocument();
    expect(within(preview).getByText("src/app.ts, src/ui.tsx")).toBeInTheDocument();
  });

  it("renders typed tool groups without the legacy worklog chrome", async () => {
    const user = userEvent.setup();
    const { container } = render(
      <CleanToolGroupBlock
        items={[
          {
            id: "inline:search",
            kind: "tool",
            toolName: "search_files",
            toolUseId: "search_1",
            title: "Search README",
            status: "completed",
            toolCategory: "search",
            code: JSON.stringify({ query: "README" }),
          },
          {
            id: "inline:read",
            kind: "tool",
            toolName: "read_file",
            toolUseId: "read_1",
            parentToolUseId: "search_1",
            title: "Read docs/README.md",
            status: "completed",
            toolCategory: "context_read",
            code: JSON.stringify({ path: "docs/README.md" }),
          },
        ]}
      />,
    );

    expect(container.querySelector(".hc-worklog")).toBeNull();
    expect(container.querySelector(".hc-tool-group")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /搜索 1、读取 1/ })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /读取 docs\/README\.md/ }));
    expect(screen.getAllByText(/docs\/README\.md/).length).toBeGreaterThan(0);
  });
});
