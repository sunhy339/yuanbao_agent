import { describe, expect, it } from "vitest";
import { buildRuntimeItems } from "./runtimeItemBuilder";
import type { SessionWorkspaceTrace } from "./types";

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
    expect(items[0]?.rawDetail).toBeUndefined();
  });

  it("keeps file approval diff but never falls back to full request json", () => {
    const diff = "diff --git a/src/app.ts b/src/app.ts\n--- a/src/app.ts\n+++ b/src/app.ts\n@@ -1 +1 @@\n-old\n+new";
    const items = buildRuntimeItems({
      session: null,
      activeTask: null,
      patches: [],
      traces: [],
      toolCalls: [],
      backgroundJobs: [],
      approvals: [
        {
          id: "approval-write",
          title: "write_file",
          status: "pending",
          kind: "write_file",
          filesChanged: 1,
          changedPaths: ["src/app.ts"],
          diff,
          parametersPreview: JSON.stringify({
            path: "src/app.ts",
            content: "<secret full file>",
          }),
          fullInput: JSON.stringify({
            workspaceRoot: "D:/py/test_pro",
            path: "src/app.ts",
            content: "<secret full file>",
          }),
          requestedAt: 1,
        },
      ],
    });

    expect(items).toHaveLength(1);
    expect(items[0]?.rawDetail).toBe(diff);
    expect(items[0]?.code).toContain("src/app.ts");
    expect(items[0]?.code).not.toContain("<secret full file>");
  });

  it("does not keep non-diff approval fullInput as runtime raw detail", () => {
    const items = buildRuntimeItems({
      session: null,
      activeTask: null,
      patches: [],
      traces: [],
      toolCalls: [],
      backgroundJobs: [],
      approvals: [
        {
          id: "approval-cmd",
          title: "run_command",
          status: "pending",
          kind: "run_command",
          command: "npm run typecheck",
          parametersPreview: "npm run typecheck",
          fullInput: JSON.stringify({ workspaceRoot: "D:/py/test_pro", command: "npm run typecheck" }),
          requestedAt: 1,
        },
      ],
    });

    expect(items[0]?.rawDetail).toBeUndefined();
    expect(items[0]?.code).toBe("npm run typecheck");
  });

  it("does not surface internal completion review approvals as runtime cards", () => {
    const items = buildRuntimeItems({
      session: null,
      activeTask: null,
      patches: [],
      traces: [],
      toolCalls: [],
      backgroundJobs: [],
      approvals: [
        {
          id: "approval-review",
          title: "completion review",
          status: "pending",
          kind: "completion_review",
          parametersPreview: JSON.stringify({ advisorRequestedEvidence: [{ summary: "internal" }] }),
          requestedAt: 1,
        },
      ],
    });

    expect(items).toEqual([]);
  });

  it("keeps completion review evidence panel-only when present", () => {
    const items = buildRuntimeItems({
      session: null,
      activeTask: null,
      patches: [],
      traces: [],
      toolCalls: [],
      backgroundJobs: [],
      approvals: [
        {
          id: "approval-review",
          title: "completion review",
          status: "pending",
          kind: "completion_review",
          summary: "Completion gate summary",
          completionEvidence: {
            summary: "Evidence summary",
            evidenceLevel: "summary_only",
            metrics: [],
            issues: [],
          },
          requestedAt: 1,
        },
      ],
    });

    expect(items).toHaveLength(1);
    expect(items[0]?.kind).toBe("completion");
    expect(items[0]?.visibility).toBe("panel");
    expect(items[0]?.rawDetail).toBeUndefined();
  });

  it("does not surface legacy advisor_tool approvals as runtime cards", () => {
    const items = buildRuntimeItems({
      session: null,
      activeTask: null,
      patches: [],
      traces: [],
      toolCalls: [],
      backgroundJobs: [],
      approvals: [
        {
          id: "approval-advisor",
          title: "advisor tool",
          status: "pending",
          kind: "advisor_tool",
          parametersPreview: JSON.stringify({ advisorRequestedEvidence: [{ summary: "internal" }] }),
          requestedAt: 1,
        },
      ],
    });

    expect(items).toEqual([]);
  });

  it("does not surface internal bridge traces as runtime cards", () => {
    const items = buildRuntimeItems({
      session: null,
      activeTask: null,
      approvals: [],
      patches: [],
      toolCalls: [],
      backgroundJobs: [],
      traces: [
        {
          id: "trace-review",
          type: "task.waiting_approval",
          source: "task",
          summary: JSON.stringify({ completionEvidence: { evidenceLevel: "summary_only" } }),
          detail: JSON.stringify({ request: { summary: "internal completion gate" } }),
          payload: {
            internalGate: "completion_review",
            _bridge: {
              internal: true,
              suppressRealtimeFlat: true,
              suppressChatReplay: true,
            },
          },
          visibility: "trace",
        },
      ],
    });

    expect(items).toEqual([]);
  });

  it("keeps canonical chat stream traces out of runtime cards", () => {
    const traces: SessionWorkspaceTrace[] = [
      {
        id: "trace-token",
        type: "assistant.token",
        source: "provider",
        summary: "hello",
        payload: {
          delta: "hello",
          _bridge: {
            internal: true,
            derivedBy: "message.delta",
            suppressRealtimeFlat: true,
            suppressChatReplay: true,
          },
        },
        visibility: "trace",
      },
      {
        id: "trace-delta",
        type: "message.delta",
        source: "message",
        summary: "hello",
        payload: { messageId: "msg_1", delta: "hello", _chatCompat: true },
        visibility: "chat",
      },
      {
        id: "trace-content",
        type: "content_delta",
        source: "chat",
        summary: "hello",
        payload: { text: "hello", _chatCompat: true },
        visibility: "chat",
      },
      {
        id: "trace-thinking",
        type: "thinking",
        source: "provider",
        summary: "Inspecting",
        payload: { text: "Inspecting", source: "provider_reasoning_delta" },
        visibility: "chat",
      },
      {
        id: "trace-status",
        type: "status",
        source: "provider",
        summary: "Thinking",
        payload: { state: "thinking", verb: "Thinking" },
        visibility: "chat",
      },
    ];

    const items = buildRuntimeItems({
      session: null,
      activeTask: null,
      approvals: [],
      patches: [],
      toolCalls: [],
      backgroundJobs: [],
      traces,
    });

    expect(items).toEqual([]);
  });

  it("formats protocol trace event names as user-facing titles", () => {
    const items = buildRuntimeItems({
      session: null,
      activeTask: null,
      approvals: [],
      patches: [],
      toolCalls: [],
      backgroundJobs: [],
      traces: [
        {
          id: "goal-failed",
          type: "goal_event",
          source: "task",
          status: "failed",
          summary: "Provider returned error",
          payload: { action: "failed", summary: "Provider returned error" },
          visibility: "panel",
        },
      ],
    });

    expect(items).toHaveLength(1);
    expect(items[0]?.title).toBe("目标状态");
    expect(items[0]?.title).not.toBe("Goal Event");
  });

  it("surfaces approval changed paths and diff preview fields", () => {
    const diff = [
      "diff --git a/src/app.ts b/src/app.ts",
      "--- a/src/app.ts",
      "+++ b/src/app.ts",
      "@@ -1 +1 @@",
      "-old",
      "+new",
    ].join("\n");
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
          parametersPreview: "apply_patch",
          filesChanged: 2,
          changedPaths: ["src/app.ts", "src/view.tsx"],
          diff,
          requestedAt: 1,
        },
      ],
    });

    expect(items[0]?.rawDetail).toBe(diff);
    expect(items[0]?.diffLines?.some((line) => line.type === "add" && line.content === "new")).toBe(true);
    expect(items[0]?.code).toContain("src/app.ts");
    expect(items[0]?.meta).toContain("2 个文件");
  });

  it("surfaces write_file approval diffs the same way as patch approvals", () => {
    const diff = [
      "--- /dev/null",
      "+++ b/src/new.ts",
      "@@ -0,0 +1 @@",
      "+export const value = 1;",
    ].join("\n");
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
          title: "write_file",
          status: "pending",
          kind: "write_file",
          parametersPreview: "write_file",
          filesChanged: 1,
          changedPaths: ["src/new.ts"],
          diff,
          requestedAt: 1,
        },
      ],
    });

    expect(items[0]?.toolName).toBe("write_file");
    expect(items[0]?.rawDetail).toBe(diff);
    expect(items[0]?.diffLines?.some((line) => line.type === "add" && line.content === "export const value = 1;")).toBe(true);
    expect(items[0]?.code).toContain("src/new.ts");
  });

  it("passes structured approval preview rows into runtime items", () => {
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
          title: "run_command",
          status: "pending",
          kind: "run_command",
          command: "npm run typecheck",
          parametersPreview: "npm run typecheck | cwd app",
          previewRows: [
            { label: "命令", value: "npm run typecheck" },
            { label: "目录", value: "app" },
          ],
          requestedAt: 1,
        },
      ],
    });

    expect(items[0]?.previewRows).toEqual([
      { label: "命令", value: "npm run typecheck" },
      { label: "目录", value: "app" },
    ]);
  });

  it("keeps plan approval display structured instead of exposing the request json as code", () => {
    const previewSections = [
      {
        kind: "items" as const,
        title: "已拆分 2 个子任务",
        items: [
          { id: "sub-0", title: "检查编排流程", meta: ["planner"] },
          { id: "sub-1", title: "验证输出协议" },
        ],
      },
    ];
    const items = buildRuntimeItems({
      session: null,
      activeTask: null,
      patches: [],
      traces: [],
      toolCalls: [],
      backgroundJobs: [],
      approvals: [
        {
          id: "approval-plan",
          title: "计划审批",
          status: "pending",
          kind: "plan",
          parametersPreview: "已拆分 2 个 swarm 子任务",
          fullInput: JSON.stringify({ goal: "优化多 agent 流程", previewSections }),
          previewRows: [
            { label: "模式", value: "swarm" },
            { label: "子任务", value: "2" },
          ],
          previewSections,
          requestedAt: 1,
        },
      ],
    });

    expect(items[0]?.toolName).toBe("plan");
    expect(items[0]?.code).toBeUndefined();
    expect(items[0]?.rawDetail).toBeUndefined();
    expect(items[0]?.previewRows).toEqual([
      { label: "模式", value: "swarm" },
      { label: "子任务", value: "2" },
    ]);
    expect(items[0]?.previewSections).toEqual(previewSections);
  });

  it("preserves backend tool batch order metadata on runtime items", () => {
    const items = buildRuntimeItems({
      session: null,
      activeTask: null,
      approvals: [],
      patches: [],
      traces: [],
      backgroundJobs: [],
      toolCalls: [
        {
          id: "call_1",
          toolUseId: "tc_1",
          toolGroupId: "tgrp_1",
          toolIndex: 0,
          toolTotal: 2,
          toolCategory: "search",
          toolPhaseId: "search",
          toolPhaseLabel: "搜索",
          toolSemanticParentId: "phase:search",
          toolSemanticParentLabel: "搜索",
          toolName: "search_files",
          status: "completed",
          input: "search needle",
          resultPreview: [{ label: "命中", value: "2 项" }],
        },
      ],
    });

    expect(items[0]).toMatchObject({
      toolUseId: "tc_1",
      toolGroupId: "tgrp_1",
      toolIndex: 0,
      toolTotal: 2,
      toolCategory: "search",
      toolPhaseId: "search",
      toolPhaseLabel: "搜索",
      toolSemanticParentId: "phase:search",
      toolSemanticParentLabel: "搜索",
      previewRows: [{ label: "命中", value: "2 项" }],
    });
  });

  it("does not render successful structured tool JSON as raw runtime detail", () => {
    const items = buildRuntimeItems({
      session: null,
      activeTask: null,
      approvals: [],
      patches: [],
      traces: [],
      backgroundJobs: [],
      toolCalls: [
        {
          id: "call_read",
          toolUseId: "call_read",
          toolName: "read_file",
          status: "completed",
          target: "snake_game/README.md",
          resultSummary: "read snake_game/README.md (4105 bytes)",
          resultPreview: [{ label: "文件", value: "snake_game/README.md" }],
          rawOutput: JSON.stringify({
            path: "snake_game/README.md",
            bytesRead: 4105,
            content: "# Snake Game\n...",
          }),
        },
      ],
    });

    expect(items).toHaveLength(1);
    expect(items[0]?.summary).toContain("read snake_game/README.md");
    expect(items[0]?.previewRows).toEqual([{ label: "文件", value: "snake_game/README.md" }]);
    expect(items[0]?.rawDetail).toBe("");
  });

  it("prefers backend display fields for successful tool runtime items", () => {
    const items = buildRuntimeItems({
      session: null,
      activeTask: null,
      approvals: [],
      patches: [],
      traces: [],
      backgroundJobs: [],
      toolCalls: [
        {
          id: "call_display",
          toolUseId: "call_display",
          toolName: "read_file",
          status: "completed",
          displayTitle: "Read app shell",
          displayTarget: "src/app.ts",
          displaySummary: "Read app shell summary",
          resultSummary: "raw fallback summary",
          rawInput: JSON.stringify({ path: "src/app.ts", content: "raw input content" }),
          rawOutput: JSON.stringify({ status: "completed", content: "raw result content" }),
        },
      ],
    });

    expect(items).toHaveLength(1);
    const item = items[0]!;
    expect(item.title).toBe("Read app shell");
    expect(item.summary).toContain("Read app shell summary");
    expect((item.meta ?? []).join(" ")).toContain("src/app.ts");
    expect(item.rawDetail).toBe("");
    expect(item.code).not.toContain("raw input content");
  });

  it("does not keep successful non-command stdout as raw runtime detail", () => {
    const items = buildRuntimeItems({
      session: null,
      activeTask: null,
      approvals: [],
      patches: [],
      traces: [],
      backgroundJobs: [],
      toolCalls: [
        {
          id: "call_read_stdout",
          toolUseId: "call_read_stdout",
          toolName: "read_file",
          status: "completed",
          target: "index.html",
          resultSummary: "read index.html (21000 bytes)",
          stdout: "<!doctype html><html>full file content</html>",
          rawInput: JSON.stringify({ path: "index.html" }),
        },
      ],
    });

    expect(items).toHaveLength(1);
    expect(items[0]?.summary).toContain("read index.html");
    expect(items[0]?.rawDetail).toBe("");
    expect(items[0]?.code).toBe("index.html");
  });

  it("summarizes write_file tool calls without exposing raw content input", () => {
    const items = buildRuntimeItems({
      session: null,
      activeTask: null,
      approvals: [],
      patches: [],
      traces: [],
      backgroundJobs: [],
      toolCalls: [
        {
          id: "call_write",
          toolUseId: "call_write",
          toolName: "write_file",
          status: "completed",
          target: "index.html",
          resultSummary: "wrote index.html (21920 bytes)",
          resultPreview: [{ label: "文件", value: "index.html" }],
          rawInput: JSON.stringify({
            path: "index.html",
            content: "<!doctype html><html>full page</html>",
            overwrite: true,
          }),
          rawOutput: JSON.stringify({
            status: "completed",
            changedPaths: ["index.html"],
            filesChanged: 1,
          }),
        },
      ],
    });

    expect(items).toHaveLength(1);
    expect(items[0]?.title).toContain("index.html");
    expect(items[0]?.code).toBe("index.html");
    expect(items[0]?.rawDetail).toBe("");
    expect(items[0]?.summary).toContain("wrote index.html");
    expect(items[0]?.code).not.toContain("<!doctype");
  });

  it("keeps failed non-command stdout and stderr as diagnostics", () => {
    const items = buildRuntimeItems({
      session: null,
      activeTask: null,
      approvals: [],
      patches: [],
      traces: [],
      backgroundJobs: [],
      toolCalls: [
        {
          id: "call_read_failed",
          toolUseId: "call_read_failed",
          toolName: "read_file",
          status: "failed",
          target: "missing.md",
          resultSummary: "read failed",
          stdout: "attempted missing.md",
          stderr: "ENOENT",
          rawInput: JSON.stringify({ path: "missing.md" }),
        },
      ],
    });

    expect(items).toHaveLength(1);
    expect(items[0]?.rawDetail).toContain("标准输出");
    expect(items[0]?.rawDetail).toContain("attempted missing.md");
    expect(items[0]?.rawDetail).toContain("标准错误");
    expect(items[0]?.rawDetail).toContain("ENOENT");
  });

  it("hides control-flow tools from runtime panel tool rows", () => {
    const items = buildRuntimeItems({
      session: null,
      activeTask: null,
      approvals: [],
      patches: [],
      traces: [],
      backgroundJobs: [],
      toolCalls: [
        {
          id: "call_plan",
          toolName: "enter_plan_mode",
          status: "completed",
          rawInput: JSON.stringify({ reason: "Need a plan" }),
        },
        {
          id: "call_ask",
          toolName: "ask_user_question",
          status: "completed",
          rawInput: JSON.stringify({ question: "Pick a format" }),
        },
      ],
    });

    expect(items).toEqual([]);
  });

  it("hides approval-required tool results until the approved command actually runs", () => {
    const items = buildRuntimeItems({
      session: null,
      activeTask: null,
      approvals: [],
      patches: [],
      traces: [],
      backgroundJobs: [],
      toolCalls: [
        {
          id: "call_command_pending",
          toolName: "run_command",
          status: "completed",
          rawInput: JSON.stringify({ command: "git commit -m test" }),
          rawOutput: JSON.stringify({ status: "approval_required" }),
        },
        {
          id: "call_command_done",
          toolName: "run_command",
          status: "completed",
          rawInput: JSON.stringify({ command: "git status --short" }),
          rawOutput: JSON.stringify({ status: "completed" }),
        },
      ],
    });

    expect(items.map((item) => item.toolUseId)).toEqual(["call_command_done"]);
  });

  it("preserves recovered command metadata on background job runtime items", () => {
    const items = buildRuntimeItems({
      session: null,
      activeTask: null,
      approvals: [],
      patches: [],
      traces: [],
      toolCalls: [],
      backgroundJobs: [
        {
          id: "cmd_1",
          toolUseId: "call_command",
          parentToolUseId: "call_parent",
          toolGroupId: "tgrp_1",
          toolIndex: 1,
          toolTotal: 2,
          toolOperationId: "run_command",
          toolOperationLabel: "运行命令",
          toolCategory: "verification",
          toolPhaseId: "verification",
          toolPhaseLabel: "验证",
          toolSemanticParentId: "group:tgrp_1:phase:verification",
          toolSemanticParentLabel: "验证",
          target: "npm test",
          inputSummary: "npm test",
          command: "npm test",
          status: "completed",
          cwd: "app",
          shell: "powershell",
          stdout: "3 passed\n",
          exitCode: 0,
          finishedAt: 1000,
        },
      ],
    });

    expect(items[0]).toMatchObject({
      kind: "command",
      sourceId: "cmd_1",
      toolUseId: "call_command",
      parentToolUseId: "call_parent",
      toolGroupId: "tgrp_1",
      toolIndex: 1,
      toolTotal: 2,
      toolOperationId: "run_command",
      toolOperationLabel: "运行命令",
      toolCategory: "verification",
      toolPhaseId: "verification",
      toolPhaseLabel: "验证",
      toolSemanticParentId: "group:tgrp_1:phase:verification",
      toolSemanticParentLabel: "验证",
      toolName: "run_command",
      code: "npm test",
    });
    expect(items[0]?.summary).toContain("npm test");
    expect(items[0]?.rawDetail).toContain("3 passed");
    expect(items[0]?.meta).toEqual(expect.arrayContaining(["app", "powershell", "退出码 0"]));
  });

  it("keeps task.failed lifecycle traces out of the clean runtime stream", () => {
    const items = buildRuntimeItems({
      session: null,
      activeTask: null,
      approvals: [],
      patches: [],
      toolCalls: [],
      backgroundJobs: [],
      traces: [
        {
          id: "task_failed",
          type: "task.failed",
          status: "failed",
          title: "Task Failed",
          summary: "Provider returned error: Concurrency limit exceeded for account, please retry later",
        },
        {
          id: "provider_error",
          type: "provider.error",
          status: "error",
          title: "Provider Error",
          summary: "Provider returned error: Concurrency limit exceeded for account, please retry later",
        },
      ],
    });

    expect(items.map((item) => item.sourceId)).toEqual(["provider_error"]);
    expect(items[0]?.title).toBe("Provider Error");
  });
});
