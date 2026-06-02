import { describe, expect, it } from "vitest";
import { buildConversationActivity } from "./ConversationActivity";
import type { RuntimeTimelineItem } from "./types";

describe("buildConversationActivity", () => {
  it("folds context probes and verification commands into one worklog", () => {
    const runtimeItems: RuntimeTimelineItem[] = [
      {
        id: "command:rg",
        kind: "command",
        title: "run_command",
        status: "completed",
        code: JSON.stringify({ command: "rg -n CleanWorklogBlock app/src/ui/haha-clean" }),
      },
      {
        id: "command:get-content",
        kind: "command",
        title: "PowerShell",
        status: "completed",
        code: JSON.stringify({ command: "Get-Content app/src/ui/haha-clean/conversation/CleanConversation.tsx" }),
      },
      {
        id: "command:git-show",
        kind: "command",
        title: "git show",
        status: "completed",
        code: "git show --stat HEAD",
      },
      {
        id: "command:typecheck",
        kind: "command",
        title: "npm run typecheck",
        status: "completed",
        code: "npm run typecheck",
      },
    ];

    const items = buildConversationActivity([], runtimeItems);

    expect(items).toHaveLength(1);
    expect(items[0]?.kind).toBe("worklog");
    if (items[0]?.kind !== "worklog") {
      throw new Error("Expected runtime items to collapse into a worklog");
    }
    expect(items[0].runtimeItems.map((item) => item.id)).toEqual([
      "command:rg",
      "command:get-content",
      "command:git-show",
      "command:typecheck",
    ]);
  });

  it("folds successful routine git mutations into the worklog", () => {
    const runtimeItems: RuntimeTimelineItem[] = [
      {
        id: "command:git-add",
        kind: "command",
        title: "run_command",
        status: "completed",
        code: JSON.stringify({ command: "git add snake_game/README.md snake_game/game.py" }),
      },
      {
        id: "command:git-commit",
        kind: "command",
        title: "run_command",
        status: "completed",
        code: JSON.stringify({ command: "git commit -m \"optimize snake game core\"" }),
      },
    ];

    const items = buildConversationActivity([], runtimeItems);

    expect(items).toHaveLength(1);
    expect(items[0]?.kind).toBe("worklog");
    if (items[0]?.kind !== "worklog") {
      throw new Error("Expected git commands to collapse into a worklog");
    }
    expect(items[0].runtimeItems.map((item) => item.id)).toEqual([
      "command:git-add",
      "command:git-commit",
    ]);
  });

  it("keeps file edits and failures outside low-noise worklogs", () => {
    const runtimeItems: RuntimeTimelineItem[] = [
      {
        id: "tool:read",
        kind: "tool",
        title: "read_file",
        toolName: "read_file",
        status: "completed",
        code: JSON.stringify({ path: "app/src/App.tsx" }),
      },
      {
        id: "tool:write",
        kind: "tool",
        title: "write_file",
        toolName: "write_file",
        status: "completed",
        code: JSON.stringify({ path: "app/src/App.tsx" }),
      },
      {
        id: "command:failed",
        kind: "command",
        title: "rg -n missing app/src",
        status: "failed",
        code: "rg -n missing app/src",
      },
      {
        id: "tool:search",
        kind: "tool",
        title: "search_files",
        toolName: "search_files",
        status: "completed",
        code: JSON.stringify({ query: "needle" }),
      },
    ];

    const items = buildConversationActivity([], runtimeItems);

    expect(items.map((item) => item.kind)).toEqual(["worklog", "runtime", "runtime", "worklog"]);
    expect(items[0]?.kind === "worklog" ? items[0].runtimeItems.map((item) => item.id) : []).toEqual(["tool:read"]);
    expect(items[1]?.kind === "runtime" ? items[1].runtime.id : "").toBe("tool:write");
    expect(items[2]?.kind === "runtime" ? items[2].runtime.id : "").toBe("command:failed");
    expect(items[3]?.kind === "worklog" ? items[3].runtimeItems.map((item) => item.id) : []).toEqual(["tool:search"]);
  });
});
