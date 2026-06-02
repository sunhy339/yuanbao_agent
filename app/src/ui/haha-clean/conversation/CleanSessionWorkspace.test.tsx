import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ConversationActivityItem } from "../../workbench/workspaces/session/types";
import { CleanSessionWorkspace, filterCleanDuplicateToolMessages, filterCleanLowSignalMessages, filterCleanLowSignalSpecialEvents } from "./CleanSessionWorkspace";

afterEach(() => {
  cleanup();
});

if (!window.matchMedia) {
  window.matchMedia = () => ({
    matches: false,
    media: "",
    onchange: null,
    addListener: () => undefined,
    removeListener: () => undefined,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
    dispatchEvent: () => false,
  });
}

describe("CleanSessionWorkspace", () => {
  it("hides quiet inline tool messages when the same runtime item is already visible", () => {
    const items: ConversationActivityItem[] = [
      {
        id: "message:tool_activity:tc_1",
        kind: "message",
        order: 1,
        message: {
          id: "tool_activity:tc_1",
          role: "assistant",
          content: JSON.stringify({ path: "snake_game/game.py" }),
          toolName: "read_file",
          status: "completed",
          metadata: {
            kind: "tool_activity",
            toolUseId: "tc_1",
            inputText: JSON.stringify({ path: "snake_game/game.py" }),
            resultText: "读取完成",
          },
        },
      },
      {
        id: "worklog:tool:tc_1",
        kind: "worklog",
        order: 2,
        runtimeItems: [
          {
            id: "tool:tc_1",
            kind: "tool",
            toolName: "read_file",
            toolUseId: "tc_1",
            title: "读取 snake_game/game.py",
            status: "completed",
            code: JSON.stringify({ path: "snake_game/game.py" }),
          },
        ],
      },
    ];

    const filtered = filterCleanDuplicateToolMessages(items);

    expect(filtered.map((item) => item.id)).toEqual(["worklog:tool:tc_1"]);
  });

  it("keeps important inline tool messages even when runtime items exist", () => {
    const items: ConversationActivityItem[] = [
      {
        id: "message:tool_activity:write_1",
        kind: "message",
        order: 1,
        message: {
          id: "tool_activity:write_1",
          role: "assistant",
          content: JSON.stringify({ path: "snake_game/game.py" }),
          toolName: "write_file",
          status: "completed",
          metadata: {
            kind: "tool_activity",
            toolUseId: "write_1",
            inputText: JSON.stringify({ path: "snake_game/game.py" }),
          },
        },
      },
      {
        id: "runtime:tool:write_1",
        kind: "runtime",
        order: 2,
        runtime: {
          id: "tool:write_1",
          kind: "tool",
          toolName: "write_file",
          toolUseId: "write_1",
          title: "写入 snake_game/game.py",
          status: "completed",
          code: JSON.stringify({ path: "snake_game/game.py" }),
        },
      },
      {
        id: "message:tool_activity:read_failed",
        kind: "message",
        order: 3,
        message: {
          id: "tool_activity:read_failed",
          role: "assistant",
          content: JSON.stringify({ path: "missing.py" }),
          toolName: "read_file",
          status: "failed",
          metadata: {
            kind: "tool_activity",
            toolUseId: "read_failed",
            inputText: JSON.stringify({ path: "missing.py" }),
          },
        },
      },
    ];

    const filtered = filterCleanDuplicateToolMessages(items);

    expect(filtered.map((item) => item.id)).toEqual([
      "message:tool_activity:write_1",
      "runtime:tool:write_1",
      "message:tool_activity:read_failed",
    ]);
  });

  it("folds completed inline tools into a visible worklog when no runtime row can represent them", () => {
    const items: ConversationActivityItem[] = [
      {
        id: "message:tool_activity:read_1",
        kind: "message",
        order: 1,
        message: {
          id: "tool_activity:read_1",
          role: "assistant",
          content: "",
          toolName: "read_file",
          status: "completed",
          metadata: {
            kind: "tool_activity",
            inputText: JSON.stringify({ path: "src/app.tsx" }),
          },
        },
      },
      {
        id: "message:tool_activity:git_status",
        kind: "message",
        order: 2,
        message: {
          id: "tool_activity:git_status",
          role: "assistant",
          content: "",
          toolName: "run_command",
          status: "completed",
          metadata: {
            kind: "tool_activity",
            inputText: JSON.stringify({ command: "git status --short" }),
          },
        },
      },
      {
        id: "message:tool_activity:rg",
        kind: "message",
        order: 3,
        message: {
          id: "tool_activity:rg",
          role: "assistant",
          content: "",
          toolName: "run_command",
          status: "completed",
          metadata: {
            kind: "tool_activity",
            inputText: JSON.stringify({ command: "rg -n TODO app/src" }),
          },
        },
      },
      {
        id: "message:tool_activity:get_content",
        kind: "message",
        order: 4,
        message: {
          id: "tool_activity:get_content",
          role: "assistant",
          content: "",
          toolName: "powershell",
          status: "completed",
          metadata: {
            kind: "tool_activity",
            inputText: JSON.stringify({ command: "Get-Content app/src/ui.tsx" }),
          },
        },
      },
      {
        id: "message:tool_activity:test",
        kind: "message",
        order: 5,
        message: {
          id: "tool_activity:test",
          role: "assistant",
          content: "",
          toolName: "run_command",
          status: "completed",
          metadata: {
            kind: "tool_activity",
            inputText: JSON.stringify({ command: "npm run typecheck" }),
          },
        },
      },
    ];

    const filtered = filterCleanDuplicateToolMessages(items);

    expect(filtered.map((item) => item.id)).toEqual(["worklog:inline:read_1:git_status:rg:get_content:test"]);
    expect(filtered[0]?.kind).toBe("worklog");
    if (filtered[0]?.kind !== "worklog") {
      throw new Error("Expected inline tools to fold into a worklog");
    }
    expect(filtered[0].runtimeItems.map((item) => item.toolName)).toEqual([
      "read_file",
      "run_command",
      "run_command",
      "powershell",
      "run_command",
    ]);
    expect(filtered[0].runtimeItems.map((item) => item.toolCategory)).toEqual([
      "context_read",
      "git",
      "command",
      "command",
      "verification",
    ]);
  });

  it("folds categorized inline tools while keeping failed records visible", () => {
    const items: ConversationActivityItem[] = [
      {
        id: "message:tool_activity:category_read",
        kind: "message",
        order: 1,
        message: {
          id: "tool_activity:category_read",
          role: "assistant",
          content: "",
          toolName: "custom_reader",
          status: "completed",
          metadata: {
            kind: "tool_activity",
            toolCategory: "context_read",
            inputText: JSON.stringify({ path: "src/app.tsx" }),
          },
        },
      },
      {
        id: "message:tool_activity:category_git",
        kind: "message",
        order: 2,
        message: {
          id: "tool_activity:category_git",
          role: "assistant",
          content: "",
          toolName: "custom_git_probe",
          status: "completed",
          metadata: {
            kind: "tool_activity",
            toolCategory: "git",
            inputText: JSON.stringify({ command: "git diff -- src/app.tsx" }),
          },
        },
      },
      {
        id: "message:tool_activity:category_failed",
        kind: "message",
        order: 3,
        message: {
          id: "tool_activity:category_failed",
          role: "assistant",
          content: "",
          toolName: "custom_search",
          status: "failed",
          metadata: {
            kind: "tool_activity",
            toolCategory: "search",
            inputText: JSON.stringify({ query: "missingSymbol" }),
          },
        },
      },
    ];

    const filtered = filterCleanDuplicateToolMessages(items);

    expect(filtered.map((item) => item.id)).toEqual([
      "worklog:inline:category_read:category_git",
      "message:tool_activity:category_failed",
    ]);
    expect(filtered[0]?.kind).toBe("worklog");
    if (filtered[0]?.kind !== "worklog") {
      throw new Error("Expected categorized inline tools to fold into a worklog");
    }
    expect(filtered[0].runtimeItems.map((item) => item.toolCategory)).toEqual(["context_read", "git"]);
  });

  it("folds completed child tool messages into the visible worklog tree", () => {
    const items: ConversationActivityItem[] = [
      {
        id: "message:tool_activity:child_1",
        kind: "message",
        order: 1,
        message: {
          id: "tool_activity:child_1",
          role: "assistant",
          content: JSON.stringify({ path: "app/src/child.ts" }),
          toolName: "write_file",
          status: "completed",
          metadata: {
            kind: "tool_activity",
            toolUseId: "child_1",
            parentToolUseId: "parent_1",
            inputText: JSON.stringify({ path: "app/src/child.ts" }),
          },
        },
      },
      {
        id: "message:tool_activity:child_failed",
        kind: "message",
        order: 2,
        message: {
          id: "tool_activity:child_failed",
          role: "assistant",
          content: JSON.stringify({ path: "app/src/missing.ts" }),
          toolName: "write_file",
          status: "failed",
          metadata: {
            kind: "tool_activity",
            toolUseId: "child_failed",
            parentToolUseId: "parent_1",
            inputText: JSON.stringify({ path: "app/src/missing.ts" }),
          },
        },
      },
      {
        id: "worklog:tool:parent",
        kind: "worklog",
        order: 3,
        runtimeItems: [
          {
            id: "tool:parent",
            kind: "tool",
            toolName: "task",
            toolUseId: "parent_1",
            title: "子任务",
            status: "completed",
          },
          {
            id: "tool:child_1",
            kind: "tool",
            toolName: "write_file",
            toolUseId: "child_1",
            parentToolUseId: "parent_1",
            title: "写入 app/src/child.ts",
            status: "completed",
            code: JSON.stringify({ path: "app/src/child.ts" }),
          },
          {
            id: "tool:child_failed",
            kind: "tool",
            toolName: "write_file",
            toolUseId: "child_failed",
            parentToolUseId: "parent_1",
            title: "写入 app/src/missing.ts",
            status: "failed",
            code: JSON.stringify({ path: "app/src/missing.ts" }),
          },
        ],
      },
    ];

    const filtered = filterCleanDuplicateToolMessages(items);

    expect(filtered.map((item) => item.id)).toEqual([
      "message:tool_activity:child_failed",
      "worklog:tool:parent",
    ]);
  });

  it("hides early task summaries and routine status events from the main transcript", () => {
    const items: ConversationActivityItem[] = [
      {
        id: "message:task_summary:start",
        kind: "message",
        order: 1,
        message: {
          id: "task_summary:start",
          role: "assistant",
          content: "理解任务目标",
          metadata: { kind: "task_summary", status: "running", summary: "理解任务目标" },
        },
      },
      {
        id: "message:status:thinking",
        kind: "message",
        order: 2,
        message: {
          id: "status:thinking",
          role: "assistant",
          content: "正在处理：准备上下文",
          metadata: { kind: "status", state: "thinking" },
        },
      },
      {
        id: "message:assistant_progress:start",
        kind: "message",
        order: 3,
        message: {
          id: "assistant_progress:start",
          role: "assistant",
          content: "理解任务目标",
          metadata: { kind: "assistant_progress", stage: "task_start" },
        },
      },
      {
        id: "message:assistant",
        kind: "message",
        order: 4,
        message: {
          id: "assistant",
          role: "assistant",
          content: "我会先看相关文件。",
        },
      },
    ];

    const filtered = filterCleanLowSignalSpecialEvents(items);

    expect(filtered.map((item) => item.id)).toEqual(["message:assistant"]);
  });

  it("hides routine goal, memory, and progress events from the main transcript", () => {
    const items: ConversationActivityItem[] = [
      {
        id: "message:goal:start",
        kind: "message",
        order: 1,
        message: {
          id: "goal:start",
          role: "assistant",
          content: "提交一下\n\naction: started\ncategory: task_goal\nstatus: running",
          metadata: {
            kind: "goal_event",
            action: "started",
            category: "task_goal",
            status: "running",
            title: "目标已开始",
            summary: "提交一下",
          },
        },
      },
      {
        id: "message:progress:model",
        kind: "message",
        order: 2,
        message: {
          id: "progress:model",
          role: "assistant",
          content: "正在请求模型（第 1 轮）",
          metadata: { kind: "assistant_progress", stage: "model_request" },
        },
      },
      {
        id: "message:memory:done",
        kind: "message",
        order: 3,
        message: {
          id: "memory:done",
          role: "assistant",
          content: "completed: 提交一下 result: 已提交",
          metadata: {
            kind: "memory_event",
            action: "completed",
            status: "completed",
            summary: "已提交",
          },
        },
      },
      {
        id: "message:assistant",
        kind: "message",
        order: 4,
        message: {
          id: "assistant",
          role: "assistant",
          content: "已提交。",
        },
      },
    ];

    const filtered = filterCleanLowSignalSpecialEvents(items);

    expect(filtered.map((item) => item.id)).toEqual(["message:assistant"]);
  });

  it("filters low-signal progress before building the visible timeline", () => {
    const messages = [
      {
        id: "progress:model",
        role: "assistant",
        content: "正在请求模型（第 1 轮）",
        createdAt: 2,
        metadata: { kind: "assistant_progress", stage: "model_request" },
      },
      {
        id: "assistant",
        role: "assistant",
        content: "已完成调整。",
        createdAt: 5,
      },
    ] as const;

    expect(filterCleanLowSignalMessages([...messages]).map((message) => message.id)).toEqual(["assistant"]);
  });

  it("filters placeholder-only thinking and streaming rows", () => {
    const messages = [
      {
        id: "thinking-placeholder",
        role: "assistant",
        content: "思考中...",
        createdAt: 1,
        streaming: true,
        placeholder: true,
      },
      {
        id: "streaming-placeholder",
        role: "assistant",
        content: "正在输出回复",
        createdAt: 2,
        streaming: true,
      },
      {
        id: "assistant",
        role: "assistant",
        content: "真正的回复",
        createdAt: 3,
      },
    ] as const;

    expect(filterCleanLowSignalMessages([...messages]).map((message) => message.id)).toEqual(["assistant"]);
  });

  it("keeps final summaries, actionable plans, and attention states visible", () => {
    const items: ConversationActivityItem[] = [
      {
        id: "message:task_summary:done",
        kind: "message",
        order: 1,
        message: {
          id: "task_summary:done",
          role: "assistant",
          content: "任务已完成，验证通过。",
          metadata: { kind: "task_summary", status: "completed", summary: "验证通过" },
        },
      },
      {
        id: "message:plan_update:action",
        kind: "message",
        order: 2,
        message: {
          id: "plan_update:action",
          role: "assistant",
          content: "下一步运行测试并查看 diff。",
          metadata: { kind: "plan_update", status: "running" },
        },
      },
      {
        id: "message:status:blocked",
        kind: "message",
        order: 3,
        message: {
          id: "status:blocked",
          role: "assistant",
          content: "等待审批后继续。",
          metadata: { kind: "status", status: "waiting_approval" },
        },
      },
    ];

    const filtered = filterCleanLowSignalSpecialEvents(items);

    expect(filtered.map((item) => item.id)).toEqual([
      "message:task_summary:done",
      "message:plan_update:action",
      "message:status:blocked",
    ]);
  });

  it("keeps the legacy session detail strip out of the transcript", () => {
    render(
      <CleanSessionWorkspace
        session={{
          id: "session-1",
          title: "前端对齐",
          status: "running",
          updatedAt: new Date("2026-06-01T08:00:00Z").getTime(),
          tokenCount: 12345,
        }}
        activeTask={{
          id: "task-1",
          status: "running",
          currentStep: "整理新建对话状态",
          changedFiles: [{ path: "app/src/ui/haha-clean/pages/CleanNewSessionWorkspace.tsx" }],
        }}
        messages={[]}
        composerContext={{
          cwd: "D:\\py\\yuanbao_agent",
          model: "GPT-5",
          permissionMode: "ask",
        }}
        worktreeStatus={{
          dirtyFiles: 1,
          branch: "codex/frontend-parity",
          files: ["app/src/ui/haha-clean/pages/CleanNewSessionWorkspace.tsx"],
        }}
      />,
    );

    expect(screen.queryByRole("heading", { name: "前端对齐" })).not.toBeInTheDocument();
    expect(screen.queryByText("整理新建对话状态")).not.toBeInTheDocument();
    expect(screen.queryByText("GPT-5")).not.toBeInTheDocument();
    expect(screen.queryByText("询问权限")).not.toBeInTheDocument();
    expect(screen.queryByText("codex/frontend-parity")).not.toBeInTheDocument();
  });

  it("keeps synthetic active-task file summaries out of the main transcript", () => {
    render(
      <CleanSessionWorkspace
        session={{
          id: "session-1",
          title: "Frontend parity",
          status: "completed",
          updatedAt: new Date("2026-06-01T08:00:00Z").getTime(),
        }}
        activeTask={{
          id: "task-1",
          status: "completed",
          changedFiles: [
            { path: "snake_game/game.py", status: "modified", additions: 3, deletions: 1 },
            { path: "snake_game/rules.py", status: "modified", additions: 1, deletions: 0 },
          ],
        }}
        messages={[
          {
            id: "assistant:done",
            role: "assistant",
            content: "已完成这次优化。",
          },
        ]}
        composerContext={{
          cwd: "D:\\py\\yuanbao_agent",
          model: "GPT-5",
          permissionMode: "ask",
        }}
      />,
    );

    expect(screen.getByText("已完成这次优化。")).toBeInTheDocument();
    expect(screen.queryByText("变更文件")).not.toBeInTheDocument();
    expect(screen.queryByText(/snake_game\/game\.py/)).not.toBeInTheDocument();
  });

  it("keeps historical tool activity visible after another task becomes active", () => {
    render(
      <CleanSessionWorkspace
        session={{
          id: "session-1",
          title: "Frontend parity",
          status: "running",
          updatedAt: new Date("2026-06-01T08:00:00Z").getTime(),
        }}
        activeTask={{
          id: "task-2",
          status: "running",
          currentStep: "继续优化",
        }}
        messages={[
          {
            id: "user:task-1",
            sessionId: "session-1",
            taskId: "task-1",
            role: "user",
            content: "先检查项目",
            createdAt: 100,
            updatedAt: 100,
          },
          {
            id: "assistant:task-1",
            sessionId: "session-1",
            taskId: "task-1",
            role: "assistant",
            content: "第一轮检查完成。",
            createdAt: 300,
            updatedAt: 300,
          },
          {
            id: "user:task-2",
            sessionId: "session-1",
            taskId: "task-2",
            role: "user",
            content: "继续",
            createdAt: 400,
            updatedAt: 400,
          },
        ]}
        toolCalls={[
          {
            id: "call-task-1",
            toolUseId: "call-task-1",
            toolName: "write_file",
            status: "completed",
            taskId: "task-1",
            time: 220,
            target: "snake_game/game.py",
            input: JSON.stringify({ path: "snake_game/game.py" }),
            resultSummary: "wrote snake_game/game.py",
          },
        ]}
        composerContext={{
          cwd: "D:\\py\\yuanbao_agent",
          model: "GPT-5",
          permissionMode: "skip",
        }}
      />,
    );

    expect(screen.getByText("第一轮检查完成。")).toBeInTheDocument();
    expect(screen.getByText(/写入 snake_game\/game\.py/)).toBeInTheDocument();
  });

  it("keeps older task runtime rows when a newer user turn has no assistant reply yet", () => {
    render(
      <CleanSessionWorkspace
        session={{
          id: "session-1",
          title: "Frontend parity",
          status: "running",
          updatedAt: new Date("2026-06-01T08:00:00Z").getTime(),
        }}
        activeTask={{
          id: "task-2",
          status: "running",
          currentStep: "继续优化",
        }}
        messages={[
          {
            id: "stored-user-1",
            sessionId: "session-1",
            taskId: "task-1",
            role: "user",
            content: "先检查项目",
            createdAt: 100,
            updatedAt: 100,
          },
          {
            id: "stored-assistant-1",
            sessionId: "session-1",
            taskId: "task-1",
            role: "assistant",
            content: "第一轮检查完成。",
            createdAt: 300,
            updatedAt: 300,
          },
          {
            id: "stored-user-2",
            sessionId: "session-1",
            taskId: "task-2",
            role: "user",
            content: "继续",
            createdAt: 400,
            updatedAt: 400,
          },
          {
            id: "assistant-pending-2",
            sessionId: "session-1",
            taskId: "task-2",
            role: "assistant",
            content: "思考中...",
            createdAt: 401,
            updatedAt: 401,
            streaming: true,
            placeholder: true,
          },
        ]}
        toolCalls={[
          {
            id: "call-task-1-read",
            toolUseId: "call-task-1-read",
            toolName: "read_file",
            status: "completed",
            taskId: "task-1",
            time: 180,
            target: "snake_game/game.py",
            input: JSON.stringify({ path: "snake_game/game.py" }),
            resultSummary: "read snake_game/game.py",
          },
          {
            id: "call-task-1-write",
            toolUseId: "call-task-1-write",
            toolName: "write_file",
            status: "completed",
            taskId: "task-1",
            time: 220,
            target: "snake_game/game.py",
            input: JSON.stringify({ path: "snake_game/game.py" }),
            resultSummary: "wrote snake_game/game.py",
          },
        ]}
        composerContext={{
          cwd: "D:\\py\\yuanbao_agent",
          model: "GPT-5",
          permissionMode: "skip",
        }}
      />,
    );

    expect(screen.getByText("第一轮检查完成。")).toBeInTheDocument();
    expect(screen.getByText("继续")).toBeInTheDocument();
    expect(screen.getAllByText(/读取上下文/).length).toBeGreaterThan(0);
    expect(screen.getByText(/写入 snake_game\/game\.py/)).toBeInTheDocument();
  });

  it("surfaces collaboration child tasks as an agent group in the transcript", () => {
    render(
      <CleanSessionWorkspace
        session={{
          id: "session-1",
          title: "Frontend parity",
          status: "running",
          updatedAt: new Date("2026-06-01T08:00:00Z").getTime(),
        }}
        activeTask={{
          id: "task-2",
          status: "running",
          currentStep: "Audit active",
        }}
        messages={[]}
        collaboration={{
          workers: [
            {
              id: "worker-1",
              name: "Agent",
              status: "idle",
              updatedAt: 120,
            },
          ],
          childTasks: [
            {
              id: "subtask-1",
              title: "Audit session layout",
              status: "completed",
              workerId: "worker-1",
              workerName: "Agent",
              summary: "Composer and workspace are aligned",
              createdAt: 100,
              updatedAt: 200,
              completedAt: 200,
            },
            {
              id: "subtask-2",
              title: "Fix output folding",
              status: "running",
              workerId: "worker-1",
              workerName: "Agent",
              summary: "Checking tool grouping",
              createdAt: 210,
              updatedAt: 260,
            },
          ],
          results: [
            {
              id: "result-1",
              taskId: "subtask-1",
              title: "Audit result",
              status: "completed",
              summary: "Layout gaps closed",
              updatedAt: 220,
            },
          ],
        }}
        composerContext={{
          cwd: "D:\\py\\yuanbao_agent",
          model: "GPT-5",
          permissionMode: "skip",
        }}
      />,
    );

    expect(screen.getByText(/派遣了 2 个代理/)).toBeInTheDocument();
    expect(screen.getByText("Audit session layout")).toBeInTheDocument();
    expect(screen.getByText("Fix output folding")).toBeInTheDocument();
  });

  it("keeps file workspace rendering in the shell layer", () => {
    const { container } = render(
      <CleanSessionWorkspace
        session={{
          id: "session-1",
          title: "Frontend parity",
          status: "completed",
          updatedAt: new Date("2026-06-01T08:00:00Z").getTime(),
        }}
        activeTask={null}
        messages={[]}
        composerContext={{
          cwd: "D:\\py\\yuanbao_agent",
          model: "GPT-5",
          permissionMode: "ask",
        }}
        worktreeStatus={{
          dirtyFiles: 1,
          branch: "codex/frontend-parity",
          files: ["app/src/ui/haha-clean/conversation/CleanSessionWorkspace.tsx"],
        }}
      />,
    );

    expect(container.querySelector(".hc-file-pane")).not.toBeInTheDocument();
    expect(container.querySelector(".hc-session-resizer")).not.toBeInTheDocument();
    expect(container.querySelector(".hc-pane-restore")).not.toBeInTheDocument();
    expect(container.querySelector(".hc-session")).not.toHaveAttribute("data-pane");
  });

  it("shows a jump-to-latest control when the transcript is scrolled away from the bottom", () => {
    const { container } = render(
      <CleanSessionWorkspace
        session={{
          id: "session-1",
          title: "Frontend parity",
          status: "running",
          updatedAt: new Date("2026-06-01T08:00:00Z").getTime(),
        }}
        activeTask={null}
        messages={[
          {
            id: "assistant:one",
            role: "assistant",
            content: "First response",
          },
        ]}
      />,
    );
    const scroll = container.querySelector(".hc-session-scroll") as HTMLElement;
    const scrollTo = vi.fn();

    Object.defineProperties(scroll, {
      clientHeight: { configurable: true, value: 600 },
      scrollHeight: { configurable: true, value: 1400 },
      scrollTop: { configurable: true, writable: true, value: 120 },
      scrollTo: { configurable: true, value: scrollTo },
    });

    fireEvent.scroll(scroll);

    const button = screen.getByRole("button", { name: /\u56de\u5230\u6700\u65b0/ });
    expect(button).toBeInTheDocument();

    fireEvent.click(button);

    expect(scrollTo).toHaveBeenCalledWith({ top: 1400, behavior: "smooth" });
  });
});
