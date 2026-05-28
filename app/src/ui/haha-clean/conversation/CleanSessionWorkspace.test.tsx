import { describe, expect, it } from "vitest";
import type { ConversationActivityItem } from "../../workbench/workspaces/session/types";
import { filterCleanDuplicateToolMessages } from "./CleanSessionWorkspace";

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
});
