import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SessionWorkspace } from "./SessionWorkspace";

afterEach(() => {
  cleanup();
});

const session = {
  id: "sess_1",
  title: "Investigate runtime boot",
  status: "active",
  summary: undefined,
  updatedAt: Date.UTC(2026, 3, 23, 3, 30),
  tokenCount: 36115,
};

describe("SessionWorkspace", () => {
  it("renders a calm empty state when no session is selected", () => {
    render(<SessionWorkspace session={null} activeTask={null} messages={[]} />);

    expect(screen.getByRole("heading", { name: "打开或创建会话" })).toBeInTheDocument();
    expect(screen.getByText(/从侧栏选择一个会话，或新建会话后开始对话/)).toBeInTheDocument();
  });
  it("renders only the conversation area for an active session", () => {
    const { container } = render(
      <SessionWorkspace
        session={session}
        activeTask={{
          id: "task_1",
          status: "running",
          goal: "Patch the session workspace",
          changedFiles: [
            {
              path: "app/src/ui/workbench/workspaces/session/SessionWorkspace.tsx",
              status: "modified",
              additions: 18,
              deletions: 4,
              reason: "Expose the worklog and dock.",
            },
          ],
        }}
        composerContext={{
          cwd: "D:/py/yuanbao_agent",
          repo: "NanmiCoder/cc-haha",
          branch: "feat/dev-desktop",
          model: "MiniMax-M2.7-highspeed",
          permissionMode: "bypass",
        }}
        worktreeStatus={{
          dirtyFiles: 1,
          files: ["app/src/ui/workbench/workspaces/session/SessionWorkspace.tsx"],
        }}
        worktreeDiff={{
          diffStat: "1 file changed, 12 insertions(+), 4 deletions(-)",
        }}
        messages={[
          { id: "m1", role: "user", content: "Check the current failing test.", createdAt: 1 },
          { id: "m2", role: "assistant", content: "I found the failure in the session renderer.", streaming: true, createdAt: 4 },
          { id: "m3", role: "system", content: "Runtime resumed session state." },
          { id: "m4", role: "tool", toolName: "shell_command", status: "completed", content: "Tests passed." },
        ]}
        taskCount={3}
        approvals={[
          {
            id: "approval_1",
            title: "Allow npm test",
            status: "pending",
            kind: "shell",
            command: "npm test",
          },
        ]}
        patches={[
          {
            id: "patch_1",
            summary: "Updated session layout",
            status: "applied",
            filesChanged: 1,
            additions: 12,
            deletions: 4,
            files: [
              {
                path: "app/src/ui/workbench/workspaces/session/SessionWorkspace.tsx",
                status: "modified",
                additions: 12,
                deletions: 4,
              },
            ],
          },
        ]}
        traces={[
          {
            id: "trace_1",
            type: "provider.response",
            title: "Provider response",
            status: "completed",
          },
        ]}
        toolCalls={[
          {
            id: "tool_1",
            toolName: "apply_patch",
            status: "completed",
            resultSummary: "Patch applied.",
            time: 2,
          },
        ]}
        backgroundJobs={[
          {
            id: "job_1",
            command: "npm run typecheck",
            status: "completed",
            cwd: "D:/py/yuanbao_agent/app",
            startedAt: 3,
          },
        ]}
      />,
    );

    expect(screen.getByRole("heading", { name: "Investigate runtime boot" })).toBeInTheDocument();
    expect(screen.getByText("Check the current failing test.")).toBeInTheDocument();
    expect(screen.getByText("I found the failure in the session renderer.")).toBeInTheDocument();
    expect(screen.getByText("Runtime resumed session state.")).toBeInTheDocument();
    expect(screen.getByText("shell_command")).toBeInTheDocument();
    expect(screen.queryByLabelText("Runtime timeline")).not.toBeInTheDocument();
    expect(screen.getAllByText("Patch the session workspace").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Allow npm test").length).toBeGreaterThan(0);
    expect(screen.getByText("Updated session layout")).toBeInTheDocument();
    expect(screen.getAllByText(/SessionWorkspace\.tsx/).length).toBeGreaterThan(0);
    expect(screen.queryByText("Provider response")).not.toBeInTheDocument();
    expect(screen.getByText("apply_patch")).toBeInTheDocument();
    expect(screen.getAllByText("npm run typecheck").length).toBeGreaterThan(0);
    expect(screen.getByLabelText("会话活动")).toBeInTheDocument();
    const digest = screen.getByLabelText("工作摘要");
    expect(within(digest).getByText("工作摘要")).toBeInTheDocument();
    expect(within(digest).getByRole("heading", { name: "Patch the session workspace" })).toBeInTheDocument();
    expect(within(digest).getByText("正在跟进1 个文件改动、1 条命令。")).toBeInTheDocument();
    expect(within(digest).getByText("正在修改")).toBeInTheDocument();
    expect(within(digest).getByText("1 个改动文件")).toBeInTheDocument();
    expect(within(digest).getByText("1 条最近命令")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /审查/ })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /终端/ })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /Git/ })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: /文件/ }));
    const fileWorkspace = screen.getByLabelText("文件工作区");
    expect(within(fileWorkspace).getByLabelText("文件浏览器")).toBeInTheDocument();
    expect(within(fileWorkspace).getByText("任务相关文件")).toBeInTheDocument();
    const activityText = Array.from(container.querySelectorAll("[data-activity-kind]")).map((item) =>
      item.textContent ?? "",
    );
    expect(activityText[0]).toContain("Check the current failing test.");
    expect(activityText[1]).toContain("I found the failure in the session renderer.");

    expect(screen.queryByRole("heading", { name: "Active task" })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Runtime shelf" })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Collaboration" })).not.toBeInTheDocument();
    expect(screen.getAllByText("MiniMax-M2.7-highspeed").length).toBeGreaterThan(0);
  });

  it("switches workspace pane pages with useful file, review, command, and git details", async () => {
    const user = userEvent.setup();
    const onLoadPatch = vi.fn();
    const onLoadWorktreeDiff = vi.fn();
    const onRefreshWorktree = vi.fn();

    render(
      <SessionWorkspace
        session={session}
        activeTask={{
          id: "task_1",
          status: "running",
          goal: "Patch the session workspace",
          changedFiles: [
            {
              path: "app/src/ui/workbench/workspaces/session/SessionWorkspace.tsx",
              status: "modified",
              additions: 18,
              deletions: 4,
            },
          ],
          commands: [
            {
              id: "cmd_1",
              command: "npm run typecheck",
              status: "completed",
              exitCode: 0,
              durationMs: 1200,
              summary: "Typecheck passed.",
            },
          ],
          activeWorktree: {
            id: "wt_1",
            taskId: "task_1",
            branchName: "agent/task_1",
            baseRef: "HEAD",
            worktreePath: "D:/py/yuanbao_agent.worktrees/task_1",
            status: "active",
            mergePolicy: "approval_required",
            cleanupPolicy: "ask_user",
            lastStatus: {
              mergeVerification: [
                {
                  command: "npm test",
                  status: "passed",
                  exitCode: 0,
                  summary: "43 passed",
                },
              ],
              review: {
                status: "approved",
                reviewer: "reviewer-agent",
                summary: "Looks good.",
              },
              mergeApproval: {
                decision: "approved",
                targetBranch: "main",
                verificationStatus: "passed",
              },
            },
          },
        }}
        composerContext={{
          cwd: "D:/py/yuanbao_agent",
          branch: "feat/dev-desktop",
          model: "gpt-5.4",
          permissionMode: "bypass",
        }}
        messages={[{ id: "m1", role: "user", content: "Check the workspace.", createdAt: 1 }]}
        patches={[
          {
            id: "patch_1",
            summary: "Updated session layout",
            status: "applied",
            filesChanged: 1,
            additions: 12,
            deletions: 4,
            files: [
              {
                path: "app/src/ui/workbench/workspaces/session/SessionWorkspace.tsx",
                status: "modified",
              },
            ],
          },
        ]}
        worktreeStatus={{
          dirtyFiles: 1,
          files: ["M app/src/ui/workbench/workspaces/session/SessionWorkspace.tsx"],
        }}
        worktreeDiff={{
          diffStat: "1 file changed, 12 insertions(+), 4 deletions(-)",
          preview: [
            "diff --git a/SessionWorkspace.tsx b/SessionWorkspace.tsx",
            "--- a/SessionWorkspace.tsx",
            "+++ b/SessionWorkspace.tsx",
            "@@ -1,2 +1,2 @@",
            "-old layout",
            "+new layout",
          ].join("\n"),
        }}
        onLoadPatch={onLoadPatch}
        onLoadWorktreeDiff={onLoadWorktreeDiff}
        onRefreshWorktree={onRefreshWorktree}
      />,
    );

    expect(screen.getByLabelText("工作区入口")).toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: /文件/ }));
    const fileWorkspace = screen.getByLabelText("文件工作区");
    expect(within(fileWorkspace).getByText("任务相关文件")).toBeInTheDocument();
    expect(within(fileWorkspace).getAllByText(/SessionWorkspace\.tsx/).length).toBeGreaterThan(0);

    await user.click(screen.getByRole("tab", { name: /审查/ }));
    const tools = screen.getByLabelText("工作区工具");
    expect(within(tools).getByText("代码审查")).toBeInTheDocument();
    expect(within(tools).getByLabelText("审查摘要")).toHaveTextContent("改动：Updated session layout");
    expect(within(tools).getByLabelText("审查摘要")).toHaveTextContent("Diff：1 file changed, 12 insertions(+), 4 deletions(-)");
    expect(within(tools).getByText("1 file changed, 12 insertions(+), 4 deletions(-)")).toBeInTheDocument();
    expect(within(tools).getByLabelText("真实差异")).toBeInTheDocument();
    expect(within(tools).getByText("old layout")).toBeInTheDocument();
    expect(within(tools).getByText("new layout")).toBeInTheDocument();
    await user.click(within(tools).getByRole("button", { name: "打开补丁" }));
    expect(onLoadPatch).toHaveBeenCalledWith("patch_1");

    await user.click(screen.getByRole("tab", { name: /终端/ }));
    expect(within(tools).getByLabelText("本地终端")).toBeInTheDocument();
    expect(within(tools).getAllByText("命令记录").length).toBeGreaterThan(0);
    expect(within(tools).getAllByText(/shell/).length).toBeGreaterThan(0);
    expect(within(tools).getAllByText("npm run typecheck").length).toBeGreaterThan(0);
    expect(within(tools).getByText("Typecheck passed.")).toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: /Git/ }));
    const gitPanel = within(tools).getByLabelText("Git 工作区");
    expect(within(gitPanel).getByRole("heading", { name: "feat/dev-desktop" })).toBeInTheDocument();
    expect(within(gitPanel).getByText("1 个未提交文件")).toBeInTheDocument();
    expect(within(gitPanel).getAllByText("+12 -4").length).toBeGreaterThan(0);
    expect(within(gitPanel).getByText("1 项通过")).toBeInTheDocument();
    expect(within(gitPanel).getByText("reviewer-agent - Looks good.")).toBeInTheDocument();
    expect(within(gitPanel).getByText("main - passed")).toBeInTheDocument();
    expect(within(gitPanel).getByText(/diff --git a\/SessionWorkspace\.tsx b\/SessionWorkspace\.tsx/)).toBeInTheDocument();
    expect(within(gitPanel).getAllByText("1 个文件").length).toBeGreaterThan(0);
    await user.click(within(gitPanel).getByRole("button", { name: "刷新状态" }));
    expect(onRefreshWorktree).toHaveBeenCalledWith("wt_1");
  });

  it("does not repeat the final assistant answer in the work summary", () => {
    const finalAnswer = "README.md exists.";
    render(
      <SessionWorkspace
        session={session}
        activeTask={{
          id: "task_1",
          status: "completed",
          goal: "Read-only UI smoke",
          resultSummary: finalAnswer,
        }}
        messages={[
          { id: "m1", role: "user", content: "Check README", createdAt: 1 },
          { id: "m2", role: "assistant", content: finalAnswer, createdAt: 2 },
        ]}
      />,
    );

    const messageStream = screen.getByLabelText("会话消息");
    expect(within(messageStream).getAllByText(finalAnswer)).toHaveLength(1);
    expect(within(screen.getByLabelText("工作摘要")).getByText(/任务已完成/)).toBeInTheDocument();
  });

  it("counts all changed files in the digest and avoids fake review diff stats", async () => {
    const user = userEvent.setup();
    const changedFiles = Array.from({ length: 8 }, (_, index) => ({
      path: `kanban_cli/file_${index + 1}.py`,
      status: index === 0 ? "added" : "modified",
    }));

    render(
      <SessionWorkspace
        session={session}
        activeTask={{
          id: "task_files",
          status: "running",
          goal: "Build a small Kanban CLI with package layout and tests",
          changedFiles,
        }}
        messages={[{ id: "m1", role: "user", content: "Build the CLI", createdAt: 1 }]}
      />,
    );

    const digest = screen.getByLabelText("工作摘要");
    expect(within(digest).getByText("8 个改动文件")).toBeInTheDocument();
    expect(within(digest).getByText("另有 4 个文件在右侧文件/审查中查看。")).toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: /审查/ }));
    const tools = screen.getByLabelText("工作区工具");
    expect(within(tools).getByText("8 个文件")).toBeInTheDocument();
    expect(within(tools).queryByText("+0 -0")).not.toBeInTheDocument();
  });

  it("shows terminal history from runtime command tool events when task commands are empty", async () => {
    const user = userEvent.setup();

    render(
      <SessionWorkspace
        session={session}
        activeTask={{
          id: "task_tool_commands",
          status: "completed",
          goal: "Verify generated files",
          changedFiles: [{ path: "kanban_cli/service.py", status: "modified" }],
        }}
        messages={[{ id: "m1", role: "user", content: "Run verification", createdAt: 1 }]}
        toolCalls={[
          {
            id: "tool_run_pytest",
            toolName: "run_command",
            status: "completed",
            rawInput: '{"command":"python -m pytest -q","cwd":"D:/tmp/kanban"}',
            argsPreview: "python -m pytest -q",
            resultSummary: "4 passed",
            durationMs: 1400,
            time: 2,
          },
        ]}
      />,
    );

    await user.click(screen.getByRole("tab", { name: /终端/ }));
    const tools = screen.getByLabelText("工作区工具");
    expect(within(tools).getByText("命令记录")).toBeInTheDocument();
    expect(within(tools).getAllByText("python -m pytest -q").length).toBeGreaterThan(0);
    expect(within(tools).getByText(/4 passed/)).toBeInTheDocument();
    expect(within(tools).getAllByText(/shell/).length).toBeGreaterThan(0);
  });

  it("keeps assistant paragraphs readable instead of splitting them into one-word lines", () => {
    const { container } = render(
      <SessionWorkspace
        session={session}
        activeTask={null}
        messages={[
          {
            id: "assistant_paragraph",
            role: "assistant",
            content: "我就可以正在使用 git_status。当前工作区路径不可访问：`C:/tmp/workspace`。",
            createdAt: 1,
          },
        ]}
      />,
    );

    const paragraph = container.querySelector(".message-bubble[data-role='assistant'] p");
    expect(paragraph).toBeInTheDocument();
    expect(paragraph?.textContent).toContain("我就可以正在使用 git_status。当前工作区路径不可访问：");
    expect(paragraph?.textContent).toContain("C:/tmp/workspace");
    expect(paragraph?.textContent).not.toContain("我\n");
  });

  it("renders a message empty state inside the conversation area", () => {
    render(<SessionWorkspace session={session} activeTask={null} messages={[]} />);

    expect(screen.getByRole("heading", { name: "还没有消息" })).toBeInTheDocument();
    expect(screen.getByText(/发送第一条消息/)).toBeInTheDocument();
  });

  it("toggles the file workspace focus mode", async () => {
    const user = userEvent.setup();
    const { container } = render(
      <SessionWorkspace
        session={session}
        activeTask={null}
        composerContext={{
          cwd: "D:/py/yuanbao_agent",
          branch: "main",
          model: "gpt-5.4",
          permissionMode: "bypass",
        }}
        messages={[{ id: "m1", role: "user", content: "Open files.", createdAt: 1 }]}
      />,
    );

    expect(container.querySelector(".session-workspace-files-focused")).not.toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: /文件/ }));
    await user.click(screen.getByRole("button", { name: "专注文件" }));
    expect(container.querySelector(".session-workspace-files-focused")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "退出专注" }));
    expect(container.querySelector(".session-workspace-files-focused")).not.toBeInTheDocument();
  });

  it("collapses, restores, and resizes the right workspace pane", async () => {
    const user = userEvent.setup();
    const { container } = render(
      <SessionWorkspace
        session={session}
        activeTask={null}
        messages={[{ id: "m1", role: "user", content: "Open workspace.", createdAt: 1 }]}
      />,
    );

    expect(screen.getByLabelText("工作区侧栏")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "隐藏右侧工作区" }));
    expect(screen.queryByLabelText("工作区侧栏")).not.toBeInTheDocument();
    expect(container.querySelector(".session-workspace-pane-collapsed")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "显示右侧工作区" }));
    expect(screen.getByLabelText("工作区侧栏")).toBeInTheDocument();

    const grid = container.querySelector(".session-workbench-grid") as HTMLElement;
    Object.defineProperty(grid, "getBoundingClientRect", {
      configurable: true,
      value: () => ({ width: 1200, height: 800, top: 0, left: 0, right: 1200, bottom: 800, x: 0, y: 0, toJSON: () => ({}) }),
    });
    fireEvent.pointerDown(screen.getByRole("button", { name: "调整右侧工作区宽度" }), { clientX: 900, pointerId: 1 });
    fireEvent.pointerMove(window, { clientX: 760 });
    fireEvent.pointerUp(window);

    expect(grid.getAttribute("style")).toContain("--session-workspace-pane-width");
  });

  it("shows streaming progress on the assistant bubble without a duplicate live pill", () => {
    const { container } = render(
      <SessionWorkspace
        session={session}
        activeTask={null}
        messages={[
          { id: "m1", role: "user", content: "Keep working", createdAt: Date.now() - 75_000 },
          { id: "m2", role: "assistant", content: "Still checking the flow.", streaming: true, createdAt: Date.now() - 40_000 },
        ]}
      />,
    );

    expect(screen.queryByLabelText(/本轮对话正在输出/)).not.toBeInTheDocument();
    const flow = Array.from(container.querySelectorAll(".message-bubble, .conversation-live-row")).map(
      (item) => item.textContent ?? "",
    );
    expect(flow[0]).toContain("Keep working");
    expect(flow[1]).toContain("Still checking the flow.");
    expect(flow).toHaveLength(2);
  });

  it("keeps a live status visible while a thinking placeholder is waiting", () => {
    const { container } = render(
      <SessionWorkspace
        session={session}
        activeTask={null}
        messages={[
          { id: "m1", role: "user", content: "Patch the game", createdAt: 1 },
          { id: "m2", role: "assistant", content: "Thinking...", streaming: true, placeholder: true, createdAt: Date.now() - 1_000 },
        ]}
        toolCalls={[
          {
            id: "tool_write",
            toolName: "write_file",
            status: "completed",
            rawInput: '{"path":"game.py"}',
            resultSummary: "Wrote file",
            time: 3,
          },
        ]}
      />,
    );

    const flow = Array.from(container.querySelectorAll(".message-bubble, .conversation-live-row")).map(
      (item) => item.textContent ?? "",
    );
    expect(flow[0]).toContain("Patch the game");
    expect(flow[1]).toContain("思考");
    expect(flow[2]).toContain("正在输出");
    expect(flow).toHaveLength(3);
  });

  it("does not move a streaming assistant message below a later user message when updatedAt changes", () => {
    const { container } = render(
      <SessionWorkspace
        session={session}
        activeTask={null}
        messages={[
          { id: "m1", role: "assistant", content: "Streaming answer", streaming: true, createdAt: 10, updatedAt: 100 },
          { id: "m2", role: "user", content: "Follow-up", createdAt: 20, updatedAt: 20 },
        ]}
      />,
    );

    const flow = Array.from(container.querySelectorAll(".message-bubble")).map((item) => item.textContent ?? "");
    expect(flow[0]).toContain("Streaming answer");
    expect(flow[1]).toContain("Follow-up");
  });

  it("keeps the current conversation status visible after the assistant stops", () => {
    render(
      <SessionWorkspace
        session={session}
        activeTask={null}
        messages={[
          { id: "m1", role: "user", content: "Keep working", createdAt: Date.now() - 75_000 },
          { id: "m2", role: "assistant", content: "Done.", createdAt: Date.now() - 15_000 },
        ]}
      />,
    );

    expect(screen.getByLabelText(/本轮对话已完成.*1m 0s/)).toBeInTheDocument();
  });

  it("explains when a thinking placeholder has not produced output for a while", () => {
    render(
      <SessionWorkspace
        session={session}
        activeTask={null}
        messages={[
          { id: "m1", role: "user", content: "Any update?", createdAt: Date.now() - 130_000 },
          {
            id: "m2",
            role: "assistant",
            content: "Thinking...",
            createdAt: Date.now() - 125_000,
            updatedAt: Date.now() - 125_000,
            streaming: true,
            placeholder: true,
          },
        ]}
      />,
    );

    expect(screen.getByText("仍在思考…")).toBeInTheDocument();
    expect(screen.getByText(/长时间没有收到新 token/)).toBeInTheDocument();
  });

  it("does not invent plan steps when the runtime did not create a plan", () => {
    render(
      <SessionWorkspace
        session={session}
        activeTask={{
          id: "task_1",
          status: "running",
          goal: "Answer a short question",
        }}
        messages={[{ id: "m1", role: "user", content: "Quick question", createdAt: Date.now() - 10_000 }]}
      />,
    );

    expect(screen.queryByLabelText("计划步骤")).not.toBeInTheDocument();
  });

  it("renders assistant markdown as structured content", () => {
    render(
      <SessionWorkspace
        session={session}
        activeTask={null}
        messages={[
          {
            id: "m1",
            role: "assistant",
            content: "## Can do\n- **Read files**\n- Run `npm test`\n\n| Tool | Use |\n| --- | --- |\n| list_dir | Browse |",
            createdAt: 1,
          },
        ]}
      />,
    );

    expect(screen.getByRole("heading", { name: "Can do" })).toBeInTheDocument();
    expect(screen.getByText("Read files")).toBeInTheDocument();
    expect(screen.getByText("npm test")).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Tool" })).toBeInTheDocument();
    expect(screen.queryByText(/## Can do/)).not.toBeInTheDocument();
  });

  it("does not render a runtime divider when only chat messages are visible", () => {
    render(
      <SessionWorkspace
        session={session}
        activeTask={null}
        messages={[{ id: "m1", role: "user", content: "你好", createdAt: 1 }]}
      />,
    );

    expect(screen.getByText("你好")).toBeInTheDocument();
    expect(screen.queryByLabelText("Runtime timeline")).not.toBeInTheDocument();
  });

  it("hides low-level trace noise while keeping important diagnostics readable", async () => {
    const user = userEvent.setup();
    render(
      <SessionWorkspace
        session={session}
        activeTask={null}
        messages={[{ id: "m1", role: "user", content: "Run task", createdAt: 1 }]}
        traces={[
          {
            id: "trace_token",
            type: "assistant.token",
            source: "assistant",
            detail: '{"delta":"hello","step":1}',
          },
          {
            id: "trace_provider",
            type: "provider.request",
            source: "provider",
            detail: '{"baseUrl":"https://example.invalid","messages":[{"role":"user"}]}',
          },
          {
            id: "trace_task_started",
            type: "task.started",
            source: "task",
            detail: '{"context":{"estimatedInputTokens":7792}}',
          },
          {
            id: "trace_error",
            type: "runtime.error",
            source: "runtime",
            status: "failed",
            detail: '{"internal":"hidden"}',
            stderr: "Command process exited unexpectedly.",
          },
        ]}
      />,
    );

    await user.click(screen.getByRole("tab", { name: /诊断/ }));
    expect(screen.queryByText("assistant.token")).not.toBeInTheDocument();
    expect(screen.queryByText("provider.request")).not.toBeInTheDocument();
    expect(screen.queryByText("task.started")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /trace Runtime Error 失败/i })).toBeInTheDocument();
    expect(screen.getByText("Command process exited unexpectedly.")).toBeInTheDocument();
    expect(screen.queryByText(/baseUrl/)).not.toBeInTheDocument();
    expect(screen.queryByText(/estimatedInputTokens/)).not.toBeInTheDocument();
    expect(screen.queryByText(/"internal":"hidden"/)).not.toBeInTheDocument();
  });

  it("renders command runtime cards as compact rows until expanded", async () => {
    const user = userEvent.setup();
    render(
      <SessionWorkspace
        session={session}
        activeTask={null}
        messages={[{ id: "m1", role: "user", content: "Run tests", createdAt: 1 }]}
        toolCalls={[
          {
            id: "tool_1",
            toolName: "run_command",
            status: "failed",
            resultSummary: "Command failed with exit 1.",
            argsPreview: "执行 npm test",
            rawInput: '{"command":"npm test","cwd":"app"}',
            time: 2,
          },
        ]}
      />,
    );

    expect(screen.getByRole("heading", { name: "npm test" })).toBeInTheDocument();
    const commandButton = screen.getByRole("button", { name: /npm test/ });
    expect(screen.getAllByText(/Command failed with exit 1/).length).toBeGreaterThan(0);
    expect(screen.queryByText(/"command":"npm test"/)).not.toBeInTheDocument();
    await user.click(commandButton);
    expect(screen.getAllByText(/Command failed with exit 1/).length).toBeGreaterThan(0);

    // Raw data should not be shown at all
    expect(screen.queryByText("查看原始数据")).not.toBeInTheDocument();
    expect(screen.queryByText(/输入/)).not.toBeInTheDocument();
    expect(screen.queryByText(/"command":"npm test"/)).not.toBeInTheDocument();
  });

  it("groups completed command and tool activity into a readable worklog", async () => {
    const user = userEvent.setup();
    render(
      <SessionWorkspace
        session={session}
        activeTask={null}
        messages={[{ id: "m1", role: "user", content: "Check workspace", createdAt: 1 }]}
        toolCalls={[
          {
            id: "tool_1",
            toolName: "run_command",
            status: "completed",
            resultSummary: "Command completed with exit 0.",
            rawInput: '{"command":"python -m pytest -q","cwd":"app"}',
            durationMs: 900,
            time: 2,
          },
          {
            id: "tool_2",
            toolName: "run_command",
            status: "completed",
            resultSummary: "Command completed with exit 0.",
            rawInput: '{"command":"python -m py_compile app.py","cwd":"app"}',
            durationMs: 120,
            time: 3,
          },
          {
            id: "tool_3",
            toolName: "git_status",
            status: "completed",
            resultSummary: "Workspace clean.",
            durationMs: 80,
            time: 4,
          },
        ]}
      />,
    );

    const worklog = screen.getByLabelText("运行摘要");
    expect(within(worklog).getByText("已运行 2 条命令")).toBeInTheDocument();
    expect(within(worklog).getAllByText(/python -m pytest -q/).length).toBeGreaterThan(0);
    expect(within(worklog).getAllByText(/python -m py_compile app.py/).length).toBeGreaterThan(0);
    expect(screen.queryByRole("heading", { name: "python -m pytest -q" })).not.toBeInTheDocument();

    await user.click(within(worklog).getByRole("button", { name: /已运行 2 条命令/ }));
    expect(within(worklog).getAllByText(/Command completed with exit 0/).length).toBeGreaterThan(0);
  });

  it("keeps completed background read_file rows out of the main activity stream", () => {
    render(
      <SessionWorkspace
        session={session}
        activeTask={null}
        messages={[{ id: "m1", role: "user", content: "Read game", createdAt: 1 }]}
        toolCalls={[
          {
            id: "tool_read",
            toolName: "read_file",
            status: "completed",
            resultSummary: "读取完成，3749 字节: \"\"\"游戏核心逻辑\"\"\"",
            rawInput: '{"path":"snake_game/game.py"}',
            durationMs: 1250,
            time: 2,
          },
        ]}
      />,
    );

    expect(screen.queryByRole("button", { name: /read_file snake_game\/game.py/i })).not.toBeInTheDocument();
    expect(screen.queryByText(/3749 字节/)).not.toBeInTheDocument();
    expect(screen.queryByText(/"path":"snake_game\/game.py"/)).not.toBeInTheDocument();
  });

  it("keeps repeated successful read_file rows quiet in the main activity stream", () => {
    const baseTime = Date.UTC(2026, 4, 6, 6, 0, 0);
    render(
      <SessionWorkspace
        session={session}
        activeTask={null}
        messages={[{ id: "m1", role: "user", content: "Read game", createdAt: baseTime }]}
        toolCalls={[
          {
            id: "tool_read_1",
            toolName: "read_file",
            status: "completed",
            resultSummary: "Read snake_game/game.py",
            rawInput: '{"path":"snake_game/game.py"}',
            time: baseTime + 10,
          },
          {
            id: "tool_read_2",
            toolName: "read_file",
            status: "completed",
            resultSummary: "Read snake_game/game.py again",
            rawInput: '{"path":"snake_game/game.py"}',
            time: baseTime + 35,
          },
        ]}
      />,
    );

    expect(screen.queryByRole("heading", { name: "read_file snake_game/game.py" })).not.toBeInTheDocument();
    expect(screen.queryByText("Read snake_game/game.py")).not.toBeInTheDocument();
  });

  it("still surfaces failed background probes because they need attention", () => {
    render(
      <SessionWorkspace
        session={session}
        activeTask={null}
        messages={[{ id: "m1", role: "user", content: "Read game", createdAt: 1 }]}
        toolCalls={[
          {
            id: "tool_read_failed",
            toolName: "read_file",
            status: "failed",
            resultSummary: "File not found: snake_game/game.py",
            rawInput: '{"path":"snake_game/game.py"}',
            time: 2,
          },
        ]}
      />,
    );

    expect(screen.getByRole("button", { name: /read_file snake_game\/game.py/i })).toBeInTheDocument();
    expect(screen.getByText(/File not found/)).toBeInTheDocument();
    expect(screen.queryByText(/"path":"snake_game\/game.py"/)).not.toBeInTheDocument();
  });

  it("renders markdown images as bounded image blocks", () => {
    render(
      <SessionWorkspace
        session={session}
        activeTask={null}
        messages={[
          {
            id: "m1",
            role: "assistant",
            content: "Here is the screenshot:\n\n![UI screenshot](https://example.com/screenshot.png)",
            createdAt: 1,
          },
        ]}
      />,
    );

    expect(screen.getByRole("img", { name: "UI screenshot" })).toHaveAttribute(
      "src",
      "https://example.com/screenshot.png",
    );
  });

  it("keeps assistant output in created order even when updated after tool activity", () => {
    const baseTime = Date.UTC(2026, 4, 6, 6, 0, 0);
    const { container } = render(
      <SessionWorkspace
        session={session}
        activeTask={null}
        messages={[
          { id: "m1", role: "user", content: "Check current status", createdAt: baseTime + 1 },
          {
            id: "m2",
            role: "assistant",
            content: "Latest generated answer",
            createdAt: baseTime + 2,
            updatedAt: baseTime + 5,
          },
        ]}
        toolCalls={[
          {
            id: "tool_read",
            toolName: "read_file",
            status: "completed",
            resultSummary: "Read snake_game/game.py",
            rawInput: '{"path":"snake_game/game.py"}',
            time: baseTime + 3,
          },
        ]}
      />,
    );

    const flow = Array.from(container.querySelectorAll(".message-bubble")).map(
      (item) => item.textContent ?? "",
    );
    expect(flow[0]).toContain("Check current status");
    expect(flow[1]).toContain("Latest generated answer");
    expect(flow).toHaveLength(2);
  });

  it("shows a live elapsed timer for running tool process cards", () => {
    const startedAt = Date.now() - 50_000;

    render(
      <SessionWorkspace
        session={session}
        activeTask={null}
        messages={[{ id: "m1", role: "user", content: "Inspect", createdAt: 1 }]}
        toolCalls={[
          {
            id: "tool_read",
            toolName: "read_file",
            status: "started",
            resultSummary: "正在读取 snake_game/game.py",
            rawInput: '{"path":"snake_game/game.py"}',
            time: startedAt,
          },
        ]}
      />,
    );

    expect(screen.getAllByRole("button", { name: /read_file snake_game\/game.py .*50s/ }).length).toBeGreaterThan(0);
  });

  it("shows a fallback elapsed timer when a running tool has no timestamp yet", () => {
    render(
      <SessionWorkspace
        session={session}
        activeTask={null}
        messages={[{ id: "m1", role: "user", content: "Inspect", createdAt: 1 }]}
        toolCalls={[
          {
            id: "tool_read",
            toolName: "read_file",
            status: "started",
            resultSummary: "正在读取 snake_game/game.py",
            rawInput: '{"path":"snake_game/game.py"}',
          },
        ]}
      />,
    );

    expect(screen.queryByRole("button", { name: /read_file snake_game\/game.py .*0s/ })).not.toBeInTheDocument();
  });

  it("copies command output and trace detail through explicit controls", async () => {
    const user = userEvent.setup();
    const onCopyRuntimeText = vi.fn();

    render(
      <SessionWorkspace
        session={session}
        activeTask={null}
        messages={[{ id: "m1", role: "user", content: "Run diagnostics", createdAt: 1 }]}
        toolCalls={[
          {
            id: "tool_1",
            toolName: "run_command",
            status: "failed",
            resultSummary: "Command failed with exit 1.",
            rawInput: '{"command":"npm test","cwd":"app"}',
            time: 2,
          },
        ]}
        traces={[
          {
            id: "trace_error",
            type: "runtime.error",
            source: "runtime",
            status: "failed",
            stderr: "Command process exited unexpectedly.",
          },
        ]}
        onCopyRuntimeText={onCopyRuntimeText}
      />,
    );

    await user.click(screen.getByRole("button", { name: "复制输出" }));
    expect(onCopyRuntimeText).toHaveBeenCalledWith(
      "命令输出",
      expect.stringContaining("Command failed with exit 1."),
    );

    await user.click(screen.getByRole("tab", { name: /诊断/ }));
    await user.click(screen.getByRole("button", { name: /trace Runtime Error 失败/i }));
    await user.click(screen.getByRole("button", { name: "复制详情" }));

    expect(onCopyRuntimeText).toHaveBeenCalledWith(
      "诊断详情",
      expect.stringContaining("Command process exited unexpectedly."),
    );
  });

  it("explains when a command needs policy approval", async () => {
    const user = userEvent.setup();

    render(
      <SessionWorkspace
        session={session}
        activeTask={null}
        messages={[{ id: "m1", role: "user", content: "Run the game", createdAt: 1 }]}
        toolCalls={[
          {
            id: "tool_blocked",
            toolName: "run_command",
            status: "failed",
            resultSummary: "Command is not allowed by command allowlist",
            rawInput: '{"command":"python main.py","cwd":"snake_game"}',
            rawOutput: '{"failureKind":"permission_denied","recoveryDecision":{"action":"request_permission"}}',
            time: 2,
          },
        ]}
      />,
    );

    await user.click(screen.getByRole("button", { name: /命令 python main\.py 失败/i }));
    expect(screen.getAllByText(/命令没有真正执行：运行时策略要求先审批这条命令/).length).toBeGreaterThan(0);
  });

  it("shows a clear patch diff state when the runtime has not returned diff text", async () => {
    const user = userEvent.setup();
    const onLoadPatch = vi.fn();

    render(
      <SessionWorkspace
        session={session}
        activeTask={null}
        messages={[{ id: "m1", role: "user", content: "Review patch", createdAt: 1 }]}
        patches={[
          {
            id: "patch_1",
            summary: "Update settings copy",
            status: "recorded",
            filesChanged: 1,
            files: [{ path: "app/src/App.tsx", status: "changed", additions: 4, deletions: 1 }],
          },
        ]}
        onLoadPatch={onLoadPatch}
      />,
    );

    await user.click(screen.getByRole("button", { name: "查看差异" }));

    expect(onLoadPatch).toHaveBeenCalledWith("patch_1");
    expect(screen.getByRole("status")).toHaveTextContent("差异暂不可用");
  });

  it("renders patch diffs as a structured review viewer", async () => {
    const user = userEvent.setup();
    const largeDiff = [
      "diff --git a/app/src/App.tsx b/app/src/App.tsx",
      "--- a/app/src/App.tsx",
      "+++ b/app/src/App.tsx",
      "@@ -1,2 +1,2 @@",
      "-old copy",
      "+new copy",
    ].join("\n");

    render(
      <SessionWorkspace
        session={session}
        activeTask={null}
        messages={[{ id: "m1", role: "user", content: "Review large patch", createdAt: 1 }]}
        patches={[
          {
            id: "patch_1",
            summary: "Update generated report",
            status: "recorded",
            filesChanged: 1,
            files: [{ path: "app/src/generated/report.ts", status: "changed", additions: 620, deletions: 0 }],
            diff: largeDiff,
          },
        ]}
      />,
    );

    await user.click(screen.getByRole("tab", { name: /审查/ }));
    const viewer = screen.getByLabelText("真实差异");
    expect(within(viewer).getByText("app/src/App.tsx")).toBeInTheDocument();
    expect(within(viewer).getByText("old copy")).toBeInTheDocument();
    expect(within(viewer).getByText("new copy")).toBeInTheDocument();
    expect(within(viewer).getByText("+1 -1")).toBeInTheDocument();
  });

  it("keeps completed list_dir probes quiet and does not expose raw tool JSON", () => {
    render(
      <SessionWorkspace
        session={session}
        activeTask={null}
        messages={[{ id: "m1", role: "user", content: "List files", createdAt: 1 }]}
        toolCalls={[
          {
            id: "tool_1",
            toolName: "list_dir",
            status: "completed",
            resultSummary: "Found 2 items: app, docs",
            argsPreview: "列出 .",
            input: "列出 .",
            rawInput: '{"ignore":["node_modules"],"path":"."}',
            rawOutput: '{"items":[{"name":"app","type":"directory"}]}',
            time: 2,
          },
        ]}
      />,
    );

    expect(screen.queryByRole("button", { name: /list_dir/ })).not.toBeInTheDocument();
    expect(screen.queryByText("Found 2 items: app, docs")).not.toBeInTheDocument();
    // Raw data should not be visible to users
    expect(screen.queryByText("查看原始数据")).not.toBeInTheDocument();
    expect(screen.queryByText(/"items"/)).not.toBeInTheDocument();
  });

  it("keeps verification commands visible while hiding successful background probes", () => {
    render(
      <SessionWorkspace
        session={session}
        activeTask={{
          id: "task_verify",
          status: "completed",
          goal: "Verify blog workspace",
          commands: [
            {
              id: "cmd_probe",
              command: "Get-ChildItem .",
              status: "completed",
              summary: "Listed fixture files.",
            },
            {
              id: "cmd_pytest",
              command: "python -m pytest -q",
              status: "completed",
              exitCode: 0,
              durationMs: 1900,
              summary: "4 passed",
            },
          ],
          verification: [
            {
              id: "verify_pytest",
              command: "python -m pytest -q",
              status: "passed",
              exitCode: 0,
              durationMs: 1900,
              summary: "4 passed",
            },
          ],
        }}
        messages={[{ id: "m1", role: "user", content: "Run verification", createdAt: 1 }]}
      />,
    );

    expect(screen.getAllByText("pytest").length).toBeGreaterThan(0);
    expect(screen.getAllByText(/4 passed/).length).toBeGreaterThan(0);
    expect(screen.queryByText("Get-ChildItem .")).not.toBeInTheDocument();
    expect(screen.queryByText("Listed fixture files.")).not.toBeInTheDocument();
  });

  it("renders active task execution progress without duplicating task summary cards", async () => {
    const user = userEvent.setup();

    render(
      <SessionWorkspace
        session={session}
        activeTask={{
          id: "task_1",
          status: "verifying",
          goal: "Create a pixel-art image tool",
          createdAt: Date.now() - 120_000,
          currentStep: "Run the generated CLI against a sample image",
          acceptanceCriteria: ["Script exists", "Help command works"],
          outOfScope: ["No GUI in this pass"],
          planSteps: [
            { id: "inspect", title: "Inspect workspace", status: "completed", detail: "Read the project shape." },
            { id: "implement", title: "Implement the generator", status: "completed", detail: "Create the CLI." },
            { id: "verify", title: "Verify the CLI", status: "active", detail: "Run the help command." },
          ],
          changedFiles: [
            {
              path: "tools/bead_art_generator.py",
              status: "added",
              additions: 148,
              deletions: 0,
              reason: "Created the CLI entry point",
              patchId: "patch_1",
            },
          ],
          commands: [
            {
              id: "cmd_1",
              command: "python tools/bead_art_generator.py --help",
              cwd: "D:/py/yuanbao_agent",
              status: "completed",
              exitCode: 0,
              durationMs: 312,
              summary: "Help text printed.",
            },
          ],
          verification: [
            {
              id: "verify_1",
              command: "python tools/bead_art_generator.py --help",
              status: "passed",
              exitCode: 0,
              durationMs: 312,
              summary: "CLI help is available.",
            },
          ],
        }}
        messages={[{ id: "m1", role: "user", content: "Build the tool", createdAt: 1 }]}
      />,
    );

    await user.click(screen.getByRole("tab", { name: /诊断/ }));
    const taskProgress = screen.getByRole("region", { name: "任务进度" });
    expect(taskProgress).toBeInTheDocument();
    expect(within(taskProgress).getByRole("heading", { name: "正在验证" })).toBeInTheDocument();
    expect(screen.getByLabelText("任务进度")).toBeInTheDocument();
    expect(screen.getAllByText(/Run the generated CLI against a sample image/).length).toBeGreaterThan(0);
    expect(screen.getAllByText("Create a pixel-art image tool").length).toBeGreaterThan(0);
    expect(screen.queryByText("Inspect workspace")).not.toBeInTheDocument();
    expect(screen.queryByText("Implement the generator")).not.toBeInTheDocument();
    expect(screen.queryByText("Verify the CLI")).not.toBeInTheDocument();
    expect(within(taskProgress).getAllByText("当前步骤").length).toBeGreaterThan(0);
    expect(within(taskProgress).getAllByText("最近检查").length).toBeGreaterThan(0);
    expect(within(taskProgress).getAllByText("代码变更").length).toBeGreaterThan(0);
    expect(within(taskProgress).getAllByText("执行状态").length).toBeGreaterThan(0);
    expect(within(taskProgress).getAllByText("验证").length).toBeGreaterThan(0);
    expect(within(taskProgress).getAllByText("执行").length).toBeGreaterThan(0);
    expect(screen.queryByRole("button", { name: /changed files/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /verification/i })).not.toBeInTheDocument();
    expect(screen.queryByText(/sessionId/)).not.toBeInTheDocument();
    expect(screen.queryByText(/taskId/)).not.toBeInTheDocument();

    expect(screen.getAllByText(/tools\/bead_art_generator.py/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/\+148/).length).toBeGreaterThan(0);

    expect(screen.getAllByText(/python tools\/bead_art_generator.py --help/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/CLI help is available|已通过/).length).toBeGreaterThan(0);
  });

  it("renders real agent child tasks separately from the execution plan", () => {
    render(
      <SessionWorkspace
        session={session}
        activeTask={{
          id: "task_parent",
          status: "running",
          goal: "Improve snake game UI",
          planSteps: [{ id: "plan_1", title: "Update board visuals", status: "active" }],
        }}
        collaboration={{
          workers: [
            {
              id: "worker_1",
              name: "Worker 1",
              status: "running",
              mode: "worker",
              claimedTaskId: "child_1",
            },
          ],
          childTasks: [
            {
              id: "child_1",
              title: "Implement food sprite polish",
              status: "running",
              workerId: "worker_1",
              workerName: "Worker 1",
              summary: "Editing snake_game/food.py",
            },
          ],
          results: [
            {
              id: "child_1:result",
              taskId: "child_1",
              title: "Implement food sprite polish",
              status: "completed",
              summary: "Food rendering updated.",
            },
          ],
        }}
        messages={[{ id: "m1", role: "user", content: "Improve snake game UI", createdAt: 1, taskId: "task_parent" }]}
      />,
    );

    expect(screen.queryByLabelText("计划步骤")).not.toBeInTheDocument();
    const agentPanel = screen.getByLabelText("真实 Agent 任务");
    expect(within(agentPanel).getAllByText("Implement food sprite polish").length).toBeGreaterThan(0);
    expect(within(agentPanel).getByText(/worker: Worker 1/)).toBeInTheDocument();
    expect(within(agentPanel).getByText("Worker 1")).toBeInTheDocument();
    expect(within(agentPanel).getByText("Food rendering updated.")).toBeInTheDocument();
  });

  it("shows that no real agent child task exists when agent work was requested but none was emitted", () => {
    render(
      <SessionWorkspace
        session={session}
        activeTask={{
          id: "task_parent",
          status: "running",
          goal: "Use multiple agents to handle this",
          planSteps: [{ id: "plan_1", title: "Plan visual updates", status: "active" }],
        }}
        collaboration={{ workers: [], childTasks: [], results: [] }}
        messages={[{ id: "m1", role: "user", content: "Use multiple agents to handle this", createdAt: 1, taskId: "task_parent" }]}
      />,
    );

    const agentPanel = screen.getByLabelText("真实 Agent 任务");
    expect(within(agentPanel).getByText(/尚未检测到运行时创建的真实 agent child task/i)).toBeInTheDocument();
  });

  it("collapses generic planner-only child tasks into a short collaboration summary", () => {
    render(
      <SessionWorkspace
        session={session}
        activeTask={{
          id: "task_parent",
          status: "completed",
          goal: "Inspect workspace",
        }}
        collaboration={{
          workers: [],
          childTasks: [
            {
              id: "child_1",
              title: "Inspect workspace and identify targets",
              status: "completed",
              workerName: "Planner Worker",
              agentType: "planner",
              summary: '{"status":"completed","changedFiles":[],"testsRun":[]}',
            },
            {
              id: "child_2",
              title: "Inspect workspace and identify targets",
              status: "completed",
              workerName: "Planner Worker",
              agentType: "planner",
              summary: '{"status":"completed","changedFiles":[],"testsRun":[]}',
            },
          ],
          results: [],
        }}
        messages={[{ id: "m1", role: "assistant", content: "Inspection finished.", createdAt: 1 }]}
      />,
    );

    expect(screen.queryByLabelText("真实 Agent 任务")).not.toBeInTheDocument();
  });

  it("does not render the task checklist inside the message stream", () => {
    render(
      <SessionWorkspace
        session={session}
        activeTask={{
          id: "task_inline",
          status: "running",
          goal: "Add AI snake battle",
          planSteps: [
            { id: "inspect", title: "Inspect snake game structure", status: "completed", detail: "Read game files." },
            { id: "implement", title: "Implement AI snake opponent", status: "active", detail: "Add opponent logic." },
            { id: "verify", title: "Verify the battle mode", status: "pending", detail: "Run the game smoke check." },
          ],
        }}
        messages={[
          { id: "m1", role: "user", content: "Earlier unrelated request", createdAt: 1, taskId: "task_old" },
          { id: "m2", role: "assistant", content: "Earlier answer", createdAt: 2, taskId: "task_old" },
          { id: "m3", role: "user", content: "Add AI snake battle", createdAt: 3, taskId: "task_inline" },
        ]}
      />,
    );

    expect(screen.queryByLabelText("助手任务清单")).not.toBeInTheDocument();
  });

  it("does not show task scaffolding for a simple follow-up question", () => {
    const question = "\u4ec0\u4e48\u60c5\u51b5\u4e86";

    render(
      <SessionWorkspace
        session={session}
        activeTask={{
          id: "task_question",
          status: "failed",
          goal: question,
          acceptanceCriteria: ["Answer the user's question."],
          outOfScope: ["Do not change files."],
          planSteps: [
            {
              id: "inspect-workspace",
              title: "\u7406\u89e3\u4efb\u52a1\u76ee\u6807",
              status: "active",
              detail: "\u786e\u8ba4 test_pro \u4e2d\u4e0e\u201c\u4ec0\u4e48\u60c5\u51b5\u4e86\u201d\u76f8\u5173\u7684\u5165\u53e3\u3001\u6a21\u5757\u548c\u73b0\u6709\u72b6\u6001\u3002",
            },
            {
              id: "search-relevant-files",
              title: "\u5b9a\u4f4d\u76f8\u5173\u6587\u4ef6",
              status: "pending",
              detail: "\u67e5\u627e\u652f\u6491\u201c\u4ec0\u4e48\u60c5\u51b5\u4e86\u201d\u6240\u9700\u4fee\u6539\u7684\u4ee3\u7801\u3001\u8d44\u6e90\u548c\u6d4b\u8bd5\u4f4d\u7f6e\u3002",
            },
            {
              id: "summarize-findings",
              title: "\u6574\u7406\u7b54\u590d",
              status: "pending",
              detail: "\u56f4\u7ed5\u201c\u4ec0\u4e48\u60c5\u51b5\u4e86\u201d\u7ed9\u51fa\u5177\u4f53\u7ed3\u8bba\u548c\u4e0b\u4e00\u6b65\u3002",
            },
          ],
        }}
        messages={[
          {
            id: "m1",
            role: "user",
            content: question,
            createdAt: 1,
            taskId: "task_question",
          },
        ]}
      />,
    );

    expect(screen.getByText(question)).toBeInTheDocument();
    expect(screen.queryByText("\u7406\u89e3\u4efb\u52a1\u76ee\u6807")).not.toBeInTheDocument();
    expect(screen.queryByText("\u5b9a\u4f4d\u76f8\u5173\u6587\u4ef6")).not.toBeInTheDocument();
    expect(screen.queryByText("\u6574\u7406\u7b54\u590d")).not.toBeInTheDocument();
  });

  it("does not render session memory cards (hidden by design)", () => {
    render(
      <SessionWorkspace
        session={{
          ...session,
          summary:
            "Task memory:\n- completed: add a focused project checklist\n  result: Created the checklist and verified it.",
        }}
        activeTask={null}
        messages={[{ id: "m1", role: "user", content: "Continue", createdAt: 1 }]}
      />,
    );

    // Session memory is internal context and should not be visible
    expect(screen.queryByRole("button", { name: /Session memory/ })).not.toBeInTheDocument();
    expect(screen.queryByText(/add a focused project checklist/)).not.toBeInTheDocument();
  });

  it("does not render context preview cards (hidden by design)", () => {
    render(
      <SessionWorkspace
        session={session}
        activeTask={null}
        messages={[{ id: "m1", role: "user", content: "Continue", createdAt: 1 }]}
        contextPreview={{
          projectFocus: "Keep attention on large-project iteration.",
          projectMemory: "Project memory:\n- completed: task run UI V1",
          workspaceRoot: "D:/py/yuanbao_agent",
          searchMode: "content",
          searchQuery: "context preview",
          toolCount: 8,
          budgetStats: {
            estimatedInputTokens: 6200,
            messageTokens: 2200,
            toolSchemaTokens: 4000,
            maxContextTokens: 8000,
            droppedSections: ["patch_diff:old"],
            trimmedSections: ["session_summary"],
          },
          taskFocus: {
            currentStep: "Inspect current context handoff",
            acceptanceCriteriaCount: 3,
            outOfScopeCount: 2,
          },
        }}
      />,
    );

    // Context preview is internal system state and should not be visible
    expect(screen.queryByRole("button", { name: /Context preview/ })).not.toBeInTheDocument();
    expect(screen.queryByText("Focus active")).not.toBeInTheDocument();
    expect(screen.queryByText("Project memory")).not.toBeInTheDocument();
  });

  it("shows pending approval actions so commands do not wait invisibly", async () => {
    const user = userEvent.setup();
    const onApprove = vi.fn();
    const onReject = vi.fn();

    render(
      <SessionWorkspace
        session={session}
        activeTask={null}
        messages={[{ id: "m1", role: "assistant", content: "Needs confirmation.", createdAt: 1 }]}
        approvals={[
          {
            id: "approval_1",
            title: "apply_patch",
            status: "pending",
            kind: "apply_patch",
            command: "apply_patch",
            risk: "medium",
          },
        ]}
        onApprove={onApprove}
        onReject={onReject}
      />,
    );

    expect(screen.getByText("Needs confirmation.")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "批准" }));
    await user.click(screen.getByRole("button", { name: "拒绝" }));

    expect(onApprove).toHaveBeenCalledWith("approval_1");
    expect(onReject).toHaveBeenCalledWith("approval_1");
  });

  it("keeps resolved approval cards visible without repeat action buttons", () => {
    render(
      <SessionWorkspace
        session={session}
        activeTask={{
          id: "task_1",
          status: "completed",
          goal: "Apply the accepted patch",
        }}
        messages={[{ id: "m1", role: "assistant", content: "Patch approval resolved.", createdAt: 1 }]}
        approvals={[
          {
            id: "approval_1",
            title: "apply_patch",
            status: "approved",
            kind: "apply_patch",
            summary: "Updated the session runtime panel.",
            command: "apply_patch",
          },
        ]}
      />,
    );

    expect(screen.getAllByText("apply_patch").length).toBeGreaterThan(0);
    expect(screen.getAllByText(/approved|已批准|通过/i).length).toBeGreaterThan(0);
    expect(screen.getByText("Updated the session runtime panel.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "批准" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "拒绝" })).not.toBeInTheDocument();
  });

  it("shows assistant message timestamps from createdAt with date and seconds for stable chronology", () => {
    vi.useFakeTimers();
    try {
      vi.setSystemTime(new Date("2026-05-22T18:00:05+08:00"));

      render(
        <SessionWorkspace
          session={session}
          activeTask={null}
          messages={[
            {
              id: "m1",
              role: "assistant",
              content: "Stable timestamp test",
              createdAt: new Date("2026-05-22T17:59:12+08:00").getTime(),
              updatedAt: new Date("2026-05-22T17:59:58+08:00").getTime(),
            },
          ]}
        />,
      );

      expect(screen.getByText(/17:59:12/i)).toBeInTheDocument();
      expect(screen.queryByText(/17:59:58/i)).not.toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it("renders completion review evidence on approval cards", () => {
    render(
      <SessionWorkspace
        session={session}
        activeTask={{
          id: "task_1",
          status: "completion_review",
          goal: "Finish guarded completion",
        }}
        messages={[{ id: "m1", role: "assistant", content: "Completion needs review.", createdAt: 1 }]}
        approvals={[
          {
            id: "approval_completion",
            title: "Completion review",
            status: "pending",
            kind: "completion_review",
            summary: "Completion requires verification.",
            risk: "medium",
            completionEvidence: {
              gateStatus: "needs_verification",
              evidenceLevel: "verified",
              status: "review",
              summary: "Code files changed without targeted verification.",
              metrics: [
                { label: "files", value: "2" },
                { label: "verified", value: "1" },
              ],
              issues: ["Code/test changes need targeted test, build, or typecheck verification."],
            },
          },
        ]}
      />,
    );

    const evidence = screen.getByLabelText("Completion evidence");
    expect(within(evidence).getByText("needs_verification")).toBeInTheDocument();
    expect(within(evidence).getAllByText("verified").length).toBeGreaterThan(0);
    expect(within(evidence).getByText("files")).toBeInTheDocument();
    expect(within(evidence).getByText("2")).toBeInTheDocument();
    expect(within(evidence).getByText("Code/test changes need targeted test, build, or typecheck verification.")).toBeInTheDocument();
  });

  it("summarizes runtime gate, verification, and context budget in the cockpit", async () => {
    const user = userEvent.setup();
    render(
      <SessionWorkspace
        session={session}
        activeTask={{
          id: "task_1",
          status: "completion_review",
          goal: "Ship the guarded frontend",
          changedFiles: [
            { path: "index.html", status: "modified" },
            { path: "app.js", status: "modified" },
          ],
          commands: [{ id: "cmd_1", command: "node --check app.js", status: "completed" }],
          verification: [
            { id: "verify_1", command: "node --check app.js", status: "passed" },
            { id: "verify_2", command: "npm test", status: "failed" },
          ],
        }}
        messages={[{ id: "m1", role: "assistant", content: "Completion needs review.", createdAt: 1 }]}
        approvals={[
          {
            id: "approval_completion",
            title: "Completion review",
            status: "pending",
            kind: "completion_review",
            summary: "Generated artifact needs acceptance review.",
            completionEvidence: {
              gateStatus: "needs_acceptance_review",
              evidenceLevel: "product_quality",
              status: "review",
              summary: "Readable artifact copy needs review.",
              metrics: [{ label: "failed criteria", value: "1" }],
              issues: ["failed: Static frontend asset reachable: index.html -> app.js"],
              audit: {
                approvalCounts: {
                  total: 1,
                  approved: 1,
                  rejected: 0,
                  pending: 0,
                },
                approvals: [
                  {
                    approvalId: "approval_completion",
                    kind: "completion_review",
                    decision: "approved",
                    decidedBy: "user",
                    summary: "Completion review approved by user.",
                  },
                ],
                completionAdvisor: {
                  accepted: true,
                  source: "llm",
                  confidence: 0.82,
                  proposalRecordId: "proposal_completion",
                },
              },
            },
          },
        ]}
        traces={[
          { id: "trace_failed", type: "provider.failure.recovery_decision", status: "completed", summary: "Switched provider profile." },
          { id: "trace_mcp", type: "agent.decision.tool_recovery", status: "completed", summary: "MCP server refresh recommended." },
        ]}
        contextPreview={{
          budgetStats: {
            estimatedInputTokens: 7200,
            maxContextTokens: 10000,
            trimmedSections: ["tool_output"],
          },
          taskFocus: {
            currentStep: "Review generated frontend acceptance evidence.",
          },
          projectFocus: "Internal focus should stay hidden.",
        }}
      />,
    );

    await user.click(screen.getByRole("tab", { name: /诊断/ }));
    const cockpit = screen.getByLabelText("运行态概览");
    expect(within(cockpit).getByText("Ship the guarded frontend")).toBeInTheDocument();
    expect(within(cockpit).getByText("needs_acceptance_review")).toBeInTheDocument();
    expect(within(cockpit).getByText("命令")).toBeInTheDocument();
    expect(within(cockpit).getByText("1 通过")).toBeInTheDocument();
    expect(within(cockpit).getByText("1 待处理")).toBeInTheDocument();
    expect(within(cockpit).getByText("上下文预算")).toBeInTheDocument();
    expect(within(cockpit).getByText("72%")).toBeInTheDocument();
    expect(within(cockpit).getAllByText("failed: Static frontend asset reachable: index.html -> app.js").length).toBeGreaterThan(0);
    expect(within(cockpit).getByText("完成依据")).toBeInTheDocument();
    expect(within(cockpit).getByText("1 已批准 / 0 待处理 / 0 已拒绝")).toBeInTheDocument();
    expect(within(cockpit).getByText("llm | 82% | proposal_completion")).toBeInTheDocument();
    expect(within(cockpit).queryByText("模型异常")).not.toBeInTheDocument();
    expect(within(cockpit).queryByText("Switched provider profile.")).not.toBeInTheDocument();
    expect(within(cockpit).getByText("Review generated frontend acceptance evidence.")).toBeInTheDocument();
    expect(screen.queryByText("Internal focus should stay hidden.")).not.toBeInTheDocument();
  });

  it("prefers current successful checks over stale failed commands in workspace status", async () => {
    const user = userEvent.setup();
    render(
      <SessionWorkspace
        session={session}
        activeTask={{
          id: "task_followup",
          status: "completed",
          goal: "Stabilize the blog follow-up",
          commands: [
            { id: "cmd_fail", command: "python -m py_compile blog_service.py blog_api.py", status: "failed" },
            { id: "cmd_pytest", command: "python -m pytest -q", status: "completed" },
            { id: "cmd_ok", command: "python -m py_compile blog_service.py blog_api.py", status: "completed" },
          ],
          verification: [
            { id: "verify_pytest", command: "python -m pytest -q", status: "passed" },
            { id: "verify_compile", command: "python -m py_compile blog_service.py blog_api.py", status: "passed" },
          ],
        }}
        messages={[{ id: "m1", role: "assistant", content: "Everything has been reverified.", createdAt: 1 }]}
      />,
    );

    await user.click(screen.getByRole("tab", { name: /诊断/ }));
    const cockpit = screen.getByLabelText("运行态概览");
    expect(within(cockpit).getByText("本轮任务已完成")).toBeInTheDocument();
    expect(
      within(cockpit).getByText("python -m py_compile blog_service.py blog_api.py · 已通过"),
    ).toBeInTheDocument();
    expect(within(cockpit).queryByText("最近动作")).not.toBeInTheDocument();
    expect(within(cockpit).getByText("python -m py_compile blog_service.py blog_api.py · 已通过")).toBeInTheDocument();
    expect(within(cockpit).queryByText("python -m py_compile blog_service.py blog_api.py | failed")).not.toBeInTheDocument();
  });

  it("surfaces resumable handoff controls in the cockpit", async () => {
    const user = userEvent.setup();
    const onRefreshTask = vi.fn();
    const onResumeTask = vi.fn();

    render(
      <SessionWorkspace
        session={session}
        activeTask={{
          id: "task_handoff",
          status: "paused",
          goal: "Resume the provider recovery follow-up",
          mainWorkflow: {
            automation: {
              level: "supervised",
            },
            convergence: {
              state: "wrap_up_requested",
              reason: "User asked to wait for wrap-up.",
              resumable: true,
              targetGoal: "Continue provider recovery validation.",
              handoffFocus: "Rerun the targeted provider preflight checks.",
            },
            userTakeover: {
              state: "wrap_up_requested",
              intent: "wait_for_wrap_up",
            },
          },
        }}
        messages={[{ id: "m1", role: "assistant", content: "Paused with handoff.", createdAt: 1 }]}
        onRefreshTask={onRefreshTask}
        onResumeTask={onResumeTask}
      />,
    );

    await user.click(screen.getByRole("tab", { name: /诊断/ }));
    const cockpit = screen.getByLabelText("运行态概览");
    const actions = within(cockpit).getByLabelText("任务操作");

    expect(within(cockpit).getByText("等待继续动作")).toBeInTheDocument();
    expect(within(cockpit).queryByText("接力状态")).not.toBeInTheDocument();
    expect(within(cockpit).queryByText("Continue provider recovery validation.")).not.toBeInTheDocument();

    await user.click(within(actions).getByRole("button", { name: "刷新" }));
    await user.click(within(actions).getByRole("button", { name: "继续" }));

    expect(onRefreshTask).toHaveBeenCalledTimes(1);
    expect(onResumeTask).toHaveBeenCalledWith("task_handoff");
  });

  it("renders trace filter bar with task id, visibility, and agent type filters", async () => {
    const user = userEvent.setup();
    render(
      <SessionWorkspace
        session={session}
        activeTask={null}
        messages={[{ id: "m1", role: "user", content: "Run task", createdAt: 1 }]}
        traces={[
          {
            id: "trace_1",
            type: "collab.task.created",
            source: "collab",
            status: "completed",
            taskId: "child_1",
            visibility: "panel",
            agentType: "explorer",
          },
          {
            id: "trace_2",
            type: "runtime.error",
            source: "runtime",
            status: "failed",
            taskId: "child_2",
            visibility: "trace",
            agentType: "worker",
          },
        ]}
      />,
    );

    await user.click(screen.getByRole("tab", { name: /诊断/ }));
    expect(screen.getByLabelText("诊断过滤")).toBeInTheDocument();
    const filterBar = screen.getByLabelText("诊断过滤");
    expect(within(filterBar).getByText("任务")).toBeInTheDocument();
    expect(within(filterBar).getAllByText("全部")[0]).toBeInTheDocument();
    expect(within(filterBar).getByText("Agent")).toBeInTheDocument();
  });

  it("shows child task fields including agentType and duration in the collaboration panel", async () => {
    const user = userEvent.setup();
    render(
      <SessionWorkspace
        session={session}
        activeTask={{
          id: "task_parent",
          status: "running",
          goal: "Multi-agent task",
        }}
        collaboration={{
          workers: [],
          childTasks: [
            {
              id: "child_1",
              title: "Explore workspace",
              status: "completed",
              workerId: "w1",
              workerName: "Explorer Worker",
              agentType: "explorer",
              durationMs: 4500,
              artifactCount: 2,
            },
            {
              id: "child_2",
              title: "Apply fixes",
              status: "failed",
              workerId: "w2",
              workerName: "Worker 2",
              agentType: "worker",
              errorMessage: "CHILD_TASK_TIMEOUT",
            },
          ],
          results: [],
        }}
        messages={[{ id: "m1", role: "user", content: "Fix bugs", createdAt: 1 }]}
      />,
    );

    await user.click(screen.getByRole("tab", { name: /诊断/ }));
    const agentPanel = screen.getByLabelText("真实 Agent 任务");
    expect(within(agentPanel).getByText(/类型: explorer/)).toBeInTheDocument();
    expect(within(agentPanel).getByText(/4.5s/)).toBeInTheDocument();
    expect(within(agentPanel).getByText(/2 产物/)).toBeInTheDocument();
    expect(within(agentPanel).getByText(/CHILD_TASK_TIMEOUT/)).toBeInTheDocument();
  });

  it("shows active worktree details and guarded lifecycle actions", async () => {
    const user = userEvent.setup();
    const onRefreshWorktree = vi.fn();
    const onLoadWorktreeDiff = vi.fn();
    const onMergeWorktree = vi.fn();
    const onCleanupWorktree = vi.fn();

    render(
      <SessionWorkspace
        session={session}
        activeTask={{
          id: "task_1",
          status: "running",
          goal: "Edit files in isolation",
          activeWorktree: {
            id: "wt_1",
            taskId: "task_1",
            branchName: "agent/task_1",
            baseRef: "HEAD",
            worktreePath: "D:/py/yuanbao_agent.worktrees/task_1",
            status: "active",
            mergePolicy: "approval_required",
            cleanupPolicy: "ask_user",
            lastStatus: {
              mergeVerification: [
                {
                  command: "npm test",
                  status: "passed",
                  exitCode: 0,
                  summary: "43 passed",
                },
              ],
              review: {
                status: "approved",
                reviewer: "reviewer-agent",
                summary: "Looks good.",
              },
              mergeApproval: {
                decision: "approved",
                targetBranch: "main",
                verificationStatus: "passed",
              },
              multiAgentWorktreeStrategy: {
                strategy: "isolated_child_worktrees",
                reason: "Child scopes merge independently.",
              },
            },
          },
        }}
        messages={[{ id: "m1", role: "user", content: "Change the runtime.", createdAt: 1 }]}
        worktreeStatus={{ dirtyFiles: 1, files: ["M app/src/App.tsx"] }}
        worktreeDiff={{ diffStat: "app/src/App.tsx | 12 ++++++++++++" }}
        onRefreshWorktree={onRefreshWorktree}
        onLoadWorktreeDiff={onLoadWorktreeDiff}
        onMergeWorktree={onMergeWorktree}
        onCleanupWorktree={onCleanupWorktree}
      />,
    );

    await user.click(screen.getByRole("tab", { name: /Git/ }));
    const tools = screen.getByLabelText("工作区工具");
    const panel = within(tools).getByLabelText("Git 工作区");
    expect(within(panel).getByRole("heading", { name: "agent/task_1" })).toBeInTheDocument();
    expect(within(panel).getAllByText("D:/py/yuanbao_agent.worktrees/task_1").length).toBeGreaterThan(0);
    expect(within(panel).getByText("1 个未提交文件")).toBeInTheDocument();
    expect(within(panel).getByText("app/src/App.tsx | 12 ++++++++++++")).toBeInTheDocument();
    expect(within(panel).getByText("1 项通过")).toBeInTheDocument();
    expect(within(panel).getAllByText("npm test").length).toBeGreaterThan(0);
    expect(within(panel).getAllByText("approved")).toHaveLength(2);
    expect(within(panel).getByText("reviewer-agent - Looks good.")).toBeInTheDocument();
    expect(within(panel).getByText("main - passed")).toBeInTheDocument();

    await user.click(within(panel).getByRole("button", { name: "刷新状态" }));
    await user.click(within(panel).getByRole("button", { name: "查看差异" }));
    await user.click(within(panel).getByRole("button", { name: "合并申请" }));

    expect(onRefreshWorktree).toHaveBeenCalledWith("wt_1");
    expect(onLoadWorktreeDiff).toHaveBeenCalledWith("wt_1");
    expect(onMergeWorktree).not.toHaveBeenCalled();
    expect(within(panel).getByRole("button", { name: "合并申请" })).toBeDisabled();
    expect(within(panel).getByRole("button", { name: "清理" })).toBeDisabled();
    expect(onCleanupWorktree).not.toHaveBeenCalled();
  });

  it("allows merge approval requests after a clean diff review", async () => {
    const user = userEvent.setup();
    const onMergeWorktree = vi.fn();

    render(
      <SessionWorkspace
        session={session}
        activeTask={{
          id: "task_1",
          status: "running",
          goal: "Edit files in isolation",
          activeWorktree: {
            id: "wt_1",
            taskId: "task_1",
            branchName: "agent/task_1",
            baseRef: "HEAD",
            worktreePath: "D:/py/yuanbao_agent.worktrees/task_1",
            status: "active",
            mergePolicy: "approval_required",
            cleanupPolicy: "ask_user",
          },
        }}
        messages={[{ id: "m1", role: "user", content: "Change the runtime.", createdAt: 1 }]}
        worktreeStatus={{ dirtyFiles: 0, files: [] }}
        worktreeDiff={{ diffStat: "app/src/App.tsx | 12 ++++++++++++" }}
        onMergeWorktree={onMergeWorktree}
      />,
    );

    await user.click(screen.getByRole("tab", { name: /Git/ }));
    const tools = screen.getByLabelText("工作区工具");
    const panel = within(tools).getByLabelText("Git 工作区");
    const mergeButton = within(panel).getByRole("button", { name: "合并申请" });

    expect(mergeButton).toBeEnabled();
    await user.click(mergeButton);

    expect(onMergeWorktree).toHaveBeenCalledWith("wt_1");
  });
});
