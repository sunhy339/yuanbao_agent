import { describe, expect, it } from "vitest";
import { runtimeSummary } from "./text";
import type { RuntimeTimelineItem } from "../../workbench/workspaces/session/types";

describe("haha-clean text helpers", () => {
  it("summarizes structured directory output instead of exposing raw JSON", () => {
    const item: RuntimeTimelineItem = {
      id: "tool:list",
      kind: "tool",
      title: "查看目录",
      status: "completed",
      toolName: "list_dir",
      rawDetail: JSON.stringify({
        items: [
          { name: "snake_game", path: "snake_game" },
          { name: "README.md", path: "README.md" },
          { name: "tests", path: "tests" },
        ],
      }),
      meta: ['{"items":[{"name":"snake_game"}]}', "16ms"],
    };

    expect(runtimeSummary(item)).toBe("找到 3 项：snake_game, README.md, tests · 16ms");
  });
});
