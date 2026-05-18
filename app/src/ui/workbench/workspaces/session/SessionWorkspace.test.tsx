import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
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
    expect(screen.getByText(/新建会话后开始对话/)).toBeInTheDocument();
  });

  it("renders only the conversation area for an active session", () => {
    const { container } = render(
      <SessionWorkspace
        session={session}
        activeTask={{
          id: "task_1",
          status: "running",
          goal: "Patch the session workspace",
        }}
        composerContext={{
          cwd: "D:/py/yuanbao_agent",
          repo: "NanmiCoder/cc-haha",
          branch: "feat/dev-desktop",
          model: "MiniMax-M2.7-highspeed",
          permissionMode: "bypass",
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
    expect(screen.getByText("Allow npm test")).toBeInTheDocument();
    expect(screen.getByText("Updated session layout")).toBeInTheDocument();
    expect(screen.queryByText("Provider response")).not.toBeInTheDocument();
    expect(screen.getByText("apply_patch")).toBeInTheDocument();
    expect(screen.getByText("npm run typecheck")).toBeInTheDocument();
    expect(screen.getByLabelText("会话活动")).toBeInTheDocument();
    const activityText = Array.from(container.querySelectorAll("[data-activity-kind]")).map((item) =>
      item.textContent ?? "",
    );
    expect(activityText[0]).toContain("Check the current failing test.");
    expect(activityText[1]).toContain("apply_patch");
    expect(activityText[2]).toContain("npm run typecheck");
    expect(activityText[3]).toContain("I found the failure in the session renderer.");

    expect(screen.queryByRole("heading", { name: "Active task" })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Runtime shelf" })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Collaboration" })).not.toBeInTheDocument();
    expect(screen.getByText("MiniMax-M2.7-highspeed")).toBeInTheDocument();
  });

  it("renders a message empty state inside the conversation area", () => {
    render(<SessionWorkspace session={session} activeTask={null} messages={[]} />);

    expect(screen.getByRole("heading", { name: "还没有消息" })).toBeInTheDocument();
    expect(screen.getByText(/发送第一条消息/)).toBeInTheDocument();
  });

  it("shows the current conversation elapsed time while the assistant is streaming", () => {
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

    expect(screen.getByLabelText(/本轮对话正在输出\.\.\.1m/)).toBeInTheDocument();
    const flow = Array.from(container.querySelectorAll(".message-bubble, .conversation-live-row")).map(
      (item) => item.textContent ?? "",
    );
    expect(flow[0]).toContain("Keep working");
    expect(flow[1]).toContain("Still checking the flow.");
    expect(flow[2]).toContain("正在输出");
  });

  it("keeps the live pill at the bottom of the activity stream", () => {
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

    const flow = Array.from(container.querySelectorAll(".message-bubble, .conversation-live-row, .runtime-event-card")).map(
      (item) => item.textContent ?? "",
    );
    expect(flow[0]).toContain("Patch the game");
    expect(flow[1]).toContain("write_file");
    expect(flow[2]).toContain("思考");
    expect(flow[3]).toContain("正在输出");
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

    expect(screen.getByLabelText(/本轮对话已完成1m/)).toBeInTheDocument();
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
    expect(screen.getByText(/长时间无新输出/)).toBeInTheDocument();
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

  it("hides low-level trace noise while keeping important diagnostics readable", () => {
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
    expect(screen.queryByText(/Command failed with exit 1/)).not.toBeInTheDocument();
    expect(screen.queryByText(/"command":"npm test"/)).not.toBeInTheDocument();
    await user.click(commandButton);
    expect(screen.getByText(/Command failed with exit 1/)).toBeInTheDocument();

    // Raw data should not be shown at all
    expect(screen.queryByText("查看原始数据")).not.toBeInTheDocument();
    expect(screen.queryByText(/"command":"npm test"/)).not.toBeInTheDocument();
  });

  it("renders tool process rows with status and elapsed time before expansion", async () => {
    const user = userEvent.setup();
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

    expect(screen.getByRole("heading", { name: "read_file snake_game/game.py" })).toBeInTheDocument();
    const toolButton = screen.getByRole("button", { name: /read_file snake_game\/game.py .*1\.3s/ });
    expect(toolButton).toBeInTheDocument();
    expect(screen.queryByText(/读取完成，3749 字节/)).not.toBeInTheDocument();
    expect(screen.queryByText(/"path":"snake_game\/game.py"/)).not.toBeInTheDocument();
    await user.click(toolButton);
    expect(screen.getByText(/读取完成，3749 字节/)).toBeInTheDocument();
  });

  it("folds repeated read_file process rows for the same file in a short window", () => {
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

    expect(screen.getAllByRole("heading", { name: "read_file snake_game/game.py" })).toHaveLength(1);
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

    const flow = Array.from(container.querySelectorAll(".message-bubble, .runtime-process-card")).map(
      (item) => item.textContent ?? "",
    );
    expect(flow[0]).toContain("Check current status");
    expect(flow[1]).toContain("Latest generated answer");
    expect(flow[2]).toContain("read_file snake_game/game.py");
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

    expect(screen.getByRole("button", { name: /read_file snake_game\/game.py .*50s/ })).toBeInTheDocument();
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

    expect(screen.getByRole("button", { name: /read_file snake_game\/game.py .*0s/ })).toBeInTheDocument();
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

    await user.click(screen.getByRole("button", { name: /trace Runtime Error 失败/i }));
    await user.click(screen.getByRole("button", { name: "复制详情" }));

    expect(onCopyRuntimeText).toHaveBeenCalledWith(
      "诊断详情",
      expect.stringContaining("Command process exited unexpectedly."),
    );
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

  it("truncates large patch diffs before rendering", async () => {
    const user = userEvent.setup();
    const largeDiff = [
      "--- a/app/src/App.tsx",
      "+++ b/app/src/App.tsx",
      "@@ -1,1 +1,620 @@",
      ...Array.from({ length: 620 }, (_, index) => `+added line ${index + 1}`),
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

    await user.click(screen.getByRole("button", { name: "查看差异" }));

    expect(screen.getByText("[差异已截断：仅显示 623 行中的前 500 行]")).toBeInTheDocument();
    expect(screen.getByText("added line 1")).toBeInTheDocument();
    expect(screen.queryByText("added line 620")).not.toBeInTheDocument();
  });

  it("does not expose raw tool JSON to users (hidden by design)", async () => {
    const user = userEvent.setup();
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

    await user.click(screen.getByRole("button", { name: /list_dir/ }));

    expect(screen.getAllByText("Found 2 items: app, docs").length).toBeGreaterThan(0);
    expect(screen.getAllByText("列出 .").length).toBeGreaterThan(0);
    // Raw data should not be visible to users
    expect(screen.queryByText("查看原始数据")).not.toBeInTheDocument();
  });

  it("renders active task execution progress without duplicating task summary cards", () => {

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

    expect(screen.getByRole("region", { name: "任务进度" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "正在验证" })).toBeInTheDocument();
    expect(screen.getByLabelText(/本轮对话助手工作中/)).toBeInTheDocument();
    expect(screen.getByText(/2m/)).toBeInTheDocument();
    expect(screen.getAllByText("Create a pixel-art image tool").length).toBeGreaterThan(0);
    expect(screen.queryByText("Inspect workspace")).not.toBeInTheDocument();
    expect(screen.queryByText("Implement the generator")).not.toBeInTheDocument();
    expect(screen.queryByText("Verify the CLI")).not.toBeInTheDocument();
    expect(screen.getByText("正在分析代码")).toBeInTheDocument();
    expect(screen.getByText("正在修改")).toBeInTheDocument();
    expect(screen.getAllByText("正在验证").length).toBeGreaterThan(0);
    expect(screen.getByText("代码变更")).toBeInTheDocument();
    expect(screen.getAllByText("1 个文件").length).toBeGreaterThan(0);
    expect(screen.getAllByText("验证").length).toBeGreaterThan(0);
    expect(screen.getByText("1 项")).toBeInTheDocument();
    expect(screen.getAllByText("执行").length).toBeGreaterThan(0);
    expect(screen.getAllByText("1 条命令").length).toBeGreaterThan(0);
    expect(screen.queryByRole("button", { name: /任务 变更文件 已记录/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /任务 验证 已通过/ })).not.toBeInTheDocument();
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
          goal: "起多个agent来做",
          planSteps: [{ id: "plan_1", title: "Plan visual updates", status: "active" }],
        }}
        collaboration={{ workers: [], childTasks: [], results: [] }}
        messages={[{ id: "m1", role: "user", content: "起多个agent来做", createdAt: 1, taskId: "task_parent" }]}
      />,
    );

    const agentPanel = screen.getByLabelText("真实 Agent 任务");
    expect(within(agentPanel).getByText("尚未检测到运行时创建的真实 agent child task。")).toBeInTheDocument();
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
        messages={[{ id: "m1", role: "assistant", content: "需要确认执行。", createdAt: 1 }]}
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

    expect(screen.getByText("中风险")).toBeInTheDocument();

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
    expect(screen.getAllByText("已批准").length).toBeGreaterThan(0);
    expect(screen.getByText("Updated the session runtime panel.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "批准" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "拒绝" })).toBeDisabled();
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

  it("summarizes runtime gate, verification, and context budget in the cockpit", () => {
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

    const cockpit = screen.getByLabelText("Runtime cockpit");
    expect(within(cockpit).getByText("Ship the guarded frontend")).toBeInTheDocument();
    expect(within(cockpit).getByText("needs_acceptance_review")).toBeInTheDocument();
    expect(within(cockpit).getByText("Files")).toBeInTheDocument();
    expect(within(cockpit).getByText("2")).toBeInTheDocument();
    expect(within(cockpit).getByText("Verified")).toBeInTheDocument();
    expect(within(cockpit).getByText("1/2")).toBeInTheDocument();
    expect(within(cockpit).getByText("Approvals")).toBeInTheDocument();
    expect(within(cockpit).getByText("Context budget")).toBeInTheDocument();
    expect(within(cockpit).getByText("72%")).toBeInTheDocument();
    expect(within(cockpit).getAllByText("failed: Static frontend asset reachable: index.html -> app.js").length).toBeGreaterThan(0);
    expect(within(cockpit).getByText("Acceptance audit")).toBeInTheDocument();
    expect(within(cockpit).getByText("1 approved / 0 pending / 0 rejected")).toBeInTheDocument();
    expect(within(cockpit).getByText("llm | 82% | proposal_completion")).toBeInTheDocument();
    expect(within(cockpit).getByText("Provider recovery")).toBeInTheDocument();
    expect(within(cockpit).getByText("Switched provider profile.")).toBeInTheDocument();
    expect(within(cockpit).getByText("MCP / Skills")).toBeInTheDocument();
    expect(within(cockpit).getByText("MCP server refresh recommended.")).toBeInTheDocument();
    expect(within(cockpit).getByText("Memory / Context")).toBeInTheDocument();
    expect(within(cockpit).getByText("Review generated frontend acceptance evidence.")).toBeInTheDocument();
    expect(screen.queryByText("Internal focus should stay hidden.")).not.toBeInTheDocument();
  });

  it("renders trace filter bar with task id, visibility, and agent type filters", () => {
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

    expect(screen.getByLabelText("诊断过滤")).toBeInTheDocument();
    const filterBar = screen.getByLabelText("诊断过滤");
    expect(within(filterBar).getByText("任务")).toBeInTheDocument();
    expect(within(filterBar).getByText("可见性")).toBeInTheDocument();
    expect(within(filterBar).getByText("Agent")).toBeInTheDocument();
  });

  it("shows child task fields including agentType and duration in the collaboration panel", () => {
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

    const panel = screen.getByLabelText("Task worktree");
    expect(within(panel).getByText("agent/task_1")).toBeInTheDocument();
    expect(within(panel).getByText("D:/py/yuanbao_agent.worktrees/task_1")).toBeInTheDocument();
    expect(within(panel).getByText("1 dirty file")).toBeInTheDocument();
    expect(within(panel).getByText("app/src/App.tsx | 12 ++++++++++++")).toBeInTheDocument();
    expect(within(panel).getByText("1 passed")).toBeInTheDocument();
    expect(within(panel).getByText("passed - npm test")).toBeInTheDocument();
    expect(within(panel).getAllByText("approved")).toHaveLength(2);
    expect(within(panel).getByText("reviewer-agent - Looks good.")).toBeInTheDocument();
    expect(within(panel).getByText("main - passed")).toBeInTheDocument();
    expect(within(panel).getByText("isolated_child_worktrees")).toBeInTheDocument();

    await user.click(within(panel).getByRole("button", { name: "Refresh" }));
    await user.click(within(panel).getByRole("button", { name: "Diff" }));
    await user.click(within(panel).getByRole("button", { name: "Request merge" }));

    expect(onRefreshWorktree).toHaveBeenCalledWith("wt_1");
    expect(onLoadWorktreeDiff).toHaveBeenCalledWith("wt_1");
    expect(onMergeWorktree).not.toHaveBeenCalled();
    expect(within(panel).getByRole("button", { name: "Request merge" })).toBeDisabled();
    expect(within(panel).getByRole("button", { name: "Cleanup" })).toBeDisabled();
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

    const panel = screen.getByLabelText("Task worktree");
    const mergeButton = within(panel).getByRole("button", { name: "Request merge" });

    expect(mergeButton).toBeEnabled();
    await user.click(mergeButton);

    expect(onMergeWorktree).toHaveBeenCalledWith("wt_1");
  });
});
