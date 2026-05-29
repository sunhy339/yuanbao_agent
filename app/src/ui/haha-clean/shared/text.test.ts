import { describe, expect, it } from "vitest";
import { runtimeLabel, runtimeSummary, toolActionTitle } from "./text";
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

  it("summarizes command and file JSON outputs into readable runtime text", () => {
    const command: RuntimeTimelineItem = {
      id: "cmd:pytest",
      kind: "command",
      title: "python -m pytest tests -q",
      status: "completed",
      rawDetail: JSON.stringify({
        status: "completed",
        exitCode: 0,
        stdout: "3 passed in 0.01s",
      }),
    };
    const readFile: RuntimeTimelineItem = {
      id: "tool:read",
      kind: "tool",
      title: "read_file",
      status: "completed",
      toolName: "read_file",
      rawDetail: JSON.stringify({
        content: "class Game:\n    pass",
      }),
    };
    const matches: RuntimeTimelineItem = {
      id: "tool:search",
      kind: "tool",
      title: "search_files",
      status: "completed",
      toolName: "search_files",
      rawDetail: JSON.stringify({
        matches: [
          { path: "snake_game/game.py" },
          { path: "snake_game/rules.py" },
          { path: "README.md" },
          { path: "tests/test_game.py" },
          { path: "config.py" },
        ],
      }),
    };

    expect(runtimeSummary(command)).toBe("已完成 · 退出码 0 · 3 passed in 0.01s");
    expect(runtimeSummary(readFile)).toBe("读取完成，20 字符");
    expect(runtimeSummary(matches)).toBe("返回结果 5 项：snake_game/game.py, snake_game/rules.py, README.md, tests/test_game.py，另有 1 项");
  });

  it("formats tool action titles with concrete targets", () => {
    expect(toolActionTitle({
      toolName: "apply_patch",
      input: JSON.stringify({ path: "snake_game/game.py" }),
    })).toBe("修改 snake_game/game.py");

    expect(toolActionTitle({
      toolName: "apply_patch",
      title: "patch approval request",
      input: JSON.stringify({ files: ["snake_game/game.py", "snake_game/rules.py"] }),
    })).toBe("修改 2 个文件");

    expect(toolActionTitle({
      toolName: "apply_patch",
      rawDetail: [
        "diff --git a/snake_game/README.md b/snake_game/README.md",
        "--- a/snake_game/README.md",
        "+++ b/snake_game/README.md",
        "@@ -1 +1 @@",
        "diff --git a/snake_game/rules.py b/snake_game/rules.py",
        "--- a/snake_game/rules.py",
        "+++ b/snake_game/rules.py",
      ].join("\n"),
    })).toBe("修改 2 个文件");

    expect(toolActionTitle({
      toolName: "run_command",
      input: JSON.stringify({ command: "python -m pytest tests -q" }),
    })).toBe("运行 python -m pytest tests -q");
  });

  it("uses action titles for runtime labels", () => {
    const item: RuntimeTimelineItem = {
      id: "tool:read",
      kind: "tool",
      title: "read_file",
      status: "completed",
      toolName: "read_file",
      code: JSON.stringify({ path: "app/src/main.tsx" }),
    };

    expect(runtimeLabel(item)).toBe("读取 app/src/main.tsx");
  });

  it("formats patch runtime labels from raw diff text", () => {
    const item: RuntimeTimelineItem = {
      id: "patch:1",
      kind: "patch",
      title: "Update snake_game/rules.py",
      status: "completed",
      rawDetail: [
        "diff --git a/snake_game/rules.py b/snake_game/rules.py",
        "--- a/snake_game/rules.py",
        "+++ b/snake_game/rules.py",
        "@@ -1 +1 @@",
      ].join("\n"),
    };

    expect(runtimeLabel(item)).toBe("修改 snake_game/rules.py");
  });
});
