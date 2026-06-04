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
