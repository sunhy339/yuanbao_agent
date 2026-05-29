import { describe, expect, it } from "vitest";
import { normalizeSessionWorktreeStatus } from "./worktreeStatus";

describe("normalizeSessionWorktreeStatus", () => {
  it("keeps local git branch state and flattens file objects", () => {
    expect(normalizeSessionWorktreeStatus({
      cwd: "D:/project",
      repoRoot: "D:/project",
      branch: "main",
      upstream: "origin/main",
      ahead: 2,
      behind: 1,
      dirtyFiles: 1,
      files: [{ path: "src/app.ts", status: "M", raw: " M src/app.ts" }],
      clean: false,
      rawStatus: "## main...origin/main [ahead 2, behind 1]\n M src/app.ts\n",
    } as any)).toEqual({
      branch: "main",
      upstream: "origin/main",
      ahead: 2,
      behind: 1,
      rawStatus: "## main...origin/main [ahead 2, behind 1]\n M src/app.ts\n",
      dirtyFiles: 1,
      files: ["M src/app.ts"],
      clean: false,
      error: undefined,
    });
  });

  it("normalizes runtime git status changes and error-only states", () => {
    const runtimeStatus = {
      workspaceRoot: "D:/project",
      cwd: "D:/project",
      branch: "feature",
      upstream: null,
      ahead: 0,
      behind: 0,
      changes: [{ path: "README.md", status: "??", raw: "?? README.md" }],
    };
    expect(normalizeSessionWorktreeStatus(runtimeStatus)).toMatchObject({
      branch: "feature",
      upstream: null,
      dirtyFiles: 1,
      files: ["?? README.md"],
      clean: false,
    });

    expect(normalizeSessionWorktreeStatus({ error: "not a git repository" })).toEqual({
      error: "not a git repository",
    });
  });
});
