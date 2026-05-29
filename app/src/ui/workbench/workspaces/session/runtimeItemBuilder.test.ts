import { describe, expect, it } from "vitest";
import { buildRuntimeItems } from "./runtimeItemBuilder";

describe("runtimeItemBuilder", () => {
  it("keeps patch diff text and filters synthetic patch titles from file summaries", () => {
    const items = buildRuntimeItems({
      session: null,
      activeTask: null,
      approvals: [],
      traces: [],
      toolCalls: [],
      backgroundJobs: [],
      patches: [
        {
          id: "patch-1",
          summary: "Update snake_game/README.md",
          status: "applied",
          diff: [
            "diff --git a/snake_game/README.md b/snake_game/README.md",
            "--- a/snake_game/README.md",
            "+++ b/snake_game/README.md",
            "@@ -1 +1 @@",
            "-old",
            "+new",
          ].join("\n"),
          files: [
            { path: "snake_game/README.md", status: "modified", additions: 1, deletions: 1 },
          ],
        },
      ],
    });

    expect(items).toHaveLength(1);
    expect(items[0]?.rawDetail).toContain("diff --git");
    expect(items[0]?.code).toBe("snake_game/README.md (+1/-1)");
    expect(items[0]?.code).not.toContain("Update snake_game/README.md");
  });

  it("preserves approval tool kind for clean action title formatting", () => {
    const items = buildRuntimeItems({
      session: null,
      activeTask: null,
      patches: [],
      traces: [],
      toolCalls: [],
      backgroundJobs: [],
      approvals: [
        {
          id: "approval-1",
          title: "patch approval request",
          status: "pending",
          kind: "apply_patch",
          parametersPreview: JSON.stringify({ files: ["snake_game/game.py", "snake_game/rules.py"] }),
          requestedAt: 1,
        },
      ],
    });

    expect(items[0]?.kind).toBe("approval");
    expect(items[0]?.toolName).toBe("apply_patch");
    expect(items[0]?.code).toContain("snake_game/game.py");
  });
});
