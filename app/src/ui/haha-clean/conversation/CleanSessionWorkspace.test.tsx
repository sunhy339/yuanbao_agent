import { describe, expect, it } from "vitest";
import type { ConversationActivityItem } from "../../workbench/workspaces/session/types";
import { filterCleanDuplicateToolMessages, filterCleanLowSignalSpecialEvents } from "./CleanSessionWorkspace";

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
        id: "message:assistant",
        kind: "message",
        order: 3,
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
});
