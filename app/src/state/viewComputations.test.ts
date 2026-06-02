import { describe, expect, it } from "vitest";
import { computeApprovalCards, computePatchCards, computeToolTimelineItems, type AgentEventLike } from "./viewComputations";
import { parsePatchFiles } from "./traceReaders";

function approvalRequested(request: Record<string, unknown>): AgentEventLike {
  return {
    eventId: "evt_approval",
    sessionId: "sess_1",
    taskId: "task_1",
    type: "approval.requested",
    ts: 1778734168000,
    payload: {
      approvalId: "approval_1",
      taskId: "task_1",
      kind: "completion_review",
      request,
    },
  };
}

describe("computeToolTimelineItems", () => {
  it("prefers lifecycle summaries while preserving raw input and result", () => {
    const [item] = computeToolTimelineItems([
      {
        eventId: "evt_tool_started",
        sessionId: "sess_1",
        taskId: "task_1",
        type: "tool.started",
        ts: 1778734168000,
        payload: {
          toolCallId: "call_1",
          parentToolUseId: "parent_1",
          toolGroupId: "tgrp_1",
          toolIndex: 0,
          toolTotal: 2,
          toolOperationId: "context:path:src/app.ts",
          toolOperationLabel: "读取上下文",
          toolCategory: "context_read",
          toolPhaseId: "context_read",
          toolPhaseLabel: "读取上下文",
          toolSemanticParentId: "phase:context_read",
          toolSemanticParentLabel: "读取上下文",
          toolName: "read_file",
          arguments: { path: "src/app.ts" },
          target: "src/app.ts",
          inputSummary: "read src/app.ts",
        },
      },
      {
        eventId: "evt_tool_completed",
        sessionId: "sess_1",
        taskId: "task_1",
        type: "tool.completed",
        ts: 1778734168025,
        payload: {
          toolCallId: "call_1",
          parentToolUseId: "parent_1",
          toolGroupId: "tgrp_1",
          toolIndex: 0,
          toolTotal: 2,
          toolOperationId: "context:path:src/app.ts",
          toolOperationLabel: "读取上下文",
          toolCategory: "context_read",
          toolPhaseId: "context_read",
          toolPhaseLabel: "读取上下文",
          toolSemanticParentId: "phase:context_read",
          toolSemanticParentLabel: "读取上下文",
          toolName: "read_file",
          arguments: { path: "src/app.ts" },
          result: { path: "src/app.ts", bytesRead: 42 },
          target: "src/app.ts",
          inputSummary: "read src/app.ts",
          resultSummary: "read src/app.ts (42 bytes)",
          resultPreview: [
            { label: "文件", value: "src/app.ts" },
            { label: "大小", value: "42 bytes" },
          ],
        },
      },
    ]);

    expect(item.target).toBe("src/app.ts");
    expect(item.parentToolUseId).toBe("parent_1");
    expect(item.toolGroupId).toBe("tgrp_1");
    expect(item.toolIndex).toBe(0);
    expect(item.toolTotal).toBe(2);
    expect(item.toolOperationId).toBe("context:path:src/app.ts");
    expect(item.toolOperationLabel).toBe("读取上下文");
    expect(item.toolCategory).toBe("context_read");
    expect(item.toolPhaseId).toBe("context_read");
    expect(item.toolPhaseLabel).toBe("读取上下文");
    expect(item.toolSemanticParentId).toBe("phase:context_read");
    expect(item.toolSemanticParentLabel).toBe(item.toolPhaseLabel);
    expect(item.argsSummary).toBe("read src/app.ts");
    expect(item.resultSummary).toBe("read src/app.ts (42 bytes)");
    expect(item.resultPreview).toEqual([
      { label: "文件", value: "src/app.ts" },
      { label: "大小", value: "42 bytes" },
    ]);
    expect(item.argsRaw).toContain('"path": "src/app.ts"');
    expect(item.resultRaw).toContain('"bytesRead": 42');
  });

  it("keeps blocked lifecycle events visible", () => {
    const [item] = computeToolTimelineItems([
      {
        eventId: "evt_tool_blocked",
        sessionId: "sess_1",
        taskId: "task_1",
        type: "tool.blocked",
        ts: 1778734168000,
        payload: {
          toolCallId: "call_2",
          toolName: "write_file",
          arguments: { path: "src/app.ts" },
          target: "src/app.ts",
          inputSummary: "write src/app.ts",
          resultSummary: "blocked by permission policy",
          reason: "Blocked by permission policy.",
        },
      },
    ]);

    expect(item.status).toBe("blocked");
    expect(item.target).toBe("src/app.ts");
    expect(item.resultSummary).toBe("blocked by permission policy");
  });

  it("keeps tool lifecycle events from older tasks when computing a session timeline", () => {
    const items = computeToolTimelineItems([
      {
        eventId: "evt_tool_old",
        sessionId: "sess_1",
        taskId: "task_1",
        type: "tool.completed",
        ts: 1778734168000,
        payload: {
          toolCallId: "call_old",
          toolName: "write_file",
          arguments: { path: "snake_game/game.py" },
          target: "snake_game/game.py",
          resultSummary: "wrote snake_game/game.py",
        },
      },
      {
        eventId: "evt_tool_new",
        sessionId: "sess_1",
        taskId: "task_2",
        type: "tool.completed",
        ts: 1778734169000,
        payload: {
          toolCallId: "call_new",
          toolName: "read_file",
          arguments: { path: "snake_game/rules.py" },
          target: "snake_game/rules.py",
          resultSummary: "read snake_game/rules.py",
        },
      },
    ]);

    expect(items.map((item) => item.taskId)).toEqual(["task_2", "task_1"]);
    expect(items.map((item) => item.toolCallId)).toEqual(["call_new", "call_old"]);
  });
});

describe("computeApprovalCards completion evidence", () => {
  it("keeps apply_patch approval changed paths and diff text as structured fields", () => {
    const [card] = computeApprovalCards([
      {
        eventId: "evt_patch_approval",
        sessionId: "sess_1",
        taskId: "task_1",
        type: "approval.requested",
        ts: 1778734168000,
        payload: {
          approvalId: "approval_patch",
          taskId: "task_1",
          kind: "apply_patch",
          patchId: "patch_1",
          filesChanged: 2,
          changedPaths: ["src/app.ts", "src/app.ts", "src/view.tsx"],
          diffText: "diff --git a/src/app.ts b/src/app.ts\n--- a/src/app.ts\n+++ b/src/app.ts\n@@ -1 +1 @@\n-old\n+new",
          request: {
            summary: "Update UI",
          },
        },
      },
    ]);

    expect(card.patchId).toBe("patch_1");
    expect(card.filesChanged).toBe(2);
    expect(card.changedPaths).toEqual(["src/app.ts", "src/view.tsx"]);
    expect(card.diffText).toContain("diff --git a/src/app.ts");
    expect(card.requestSummary).toContain("src/app.ts");
  });

  it("falls back to changed path objects in the approval request", () => {
    const [card] = computeApprovalCards([
      {
        eventId: "evt_patch_approval",
        sessionId: "sess_1",
        taskId: "task_1",
        type: "approval.requested",
        ts: 1778734168000,
        payload: {
          approvalId: "approval_patch",
          taskId: "task_1",
          kind: "apply_patch",
          request: {
            summary: "Update UI",
            files: [{ path: "src/app.ts" }, { path: "src/view.tsx" }],
          },
        },
      },
    ]);

    expect(card.filesChanged).toBe(2);
    expect(card.changedPaths).toEqual(["src/app.ts", "src/view.tsx"]);
    expect(card.requestSummary).toContain("src/view.tsx");
  });

  it("surfaces write_file approval path and diff preview fields", () => {
    const [card] = computeApprovalCards([
      {
        eventId: "evt_write_approval",
        sessionId: "sess_1",
        taskId: "task_1",
        type: "approval.requested",
        ts: 1778734168000,
        payload: {
          approvalId: "approval_write",
          taskId: "task_1",
          kind: "write_file",
          filesChanged: 1,
          changedPaths: ["src/new.ts"],
          diffText: "--- /dev/null\n+++ b/src/new.ts\n@@ -0,0 +1 @@\n+export const value = 1;",
          request: {
            path: "src/new.ts",
            content: "export const value = 1;\n",
          },
        },
      },
    ]);

    expect(card.filesChanged).toBe(1);
    expect(card.changedPaths).toEqual(["src/new.ts"]);
    expect(card.diffText).toContain("+++ b/src/new.ts");
    expect(card.requestSummary).toContain("src/new.ts");
  });

  it("merges resolved-before-requested approvals into one real request card", () => {
    const cards = computeApprovalCards([
      {
        eventId: "evt_resolved",
        sessionId: "sess_1",
        taskId: "task_1",
        type: "approval.resolved",
        ts: 1778734169000,
        payload: {
          approvalId: "approval_1",
          taskId: "task_1",
          decision: "approved",
        },
      },
      {
        eventId: "evt_requested",
        sessionId: "sess_1",
        taskId: "task_1",
        type: "approval.requested",
        ts: 1778734170000,
        payload: {
          approvalId: "approval_1",
          taskId: "task_1",
          kind: "run_command",
          request: {
            command: "npm run typecheck",
            cwd: "D:/tmp/blog-task",
            shell: "powershell",
            timeoutMs: 120000,
            risk: "validates generated project",
          },
        },
      },
    ]);

    expect(cards).toHaveLength(1);
    expect(cards[0].status).toBe("approved");
    expect(cards[0].kind).toBe("run_command");
    expect(cards[0].command).toBe("npm run typecheck");
    expect(cards[0].cwd).toBe("D:/tmp/blog-task");
    expect(cards[0].requestSummary).toBe("npm run typecheck | cwd D:/tmp/blog-task");
    expect(cards[0].previewRows).toEqual([
      { label: "命令", value: "npm run typecheck" },
      { label: "目录", value: "D:/tmp/blog-task" },
      { label: "Shell", value: "powershell" },
      { label: "原因", value: "validates generated project" },
    ]);
    expect(cards[0].resolvedEventId).toBe("evt_resolved");
    expect(cards[0].requestedEventId).toBe("evt_requested");
  });

  it("uses resolved approval payload details when the request event is missing", () => {
    const [card] = computeApprovalCards([
      {
        eventId: "evt_resolved",
        sessionId: "sess_1",
        taskId: "task_1",
        type: "approval.resolved",
        ts: 1778734169000,
        payload: {
          approvalId: "approval_write",
          taskId: "task_1",
          kind: "write_file",
          decision: "approved",
          preview: [
            { label: "File", value: "src/new.ts" },
            { label: "Reason", value: "create generated file" },
          ],
          filesChanged: 1,
          changedPaths: ["src/new.ts"],
          diffText: "--- /dev/null\n+++ b/src/new.ts\n@@ -0,0 +1 @@\n+export const value = 1;",
          request: {
            path: "src/new.ts",
            risk: "writes a file",
          },
        },
      },
    ]);

    expect(card.status).toBe("approved");
    expect(card.kind).toBe("write_file");
    expect(card.filesChanged).toBe(1);
    expect(card.changedPaths).toEqual(["src/new.ts"]);
    expect(card.diffText).toContain("+++ b/src/new.ts");
    expect(card.previewRows).toEqual([
      { label: "File", value: "src/new.ts" },
      { label: "Reason", value: "create generated file" },
    ]);
    expect(card.requestSummary).toContain("src/new.ts");
    expect(card.requestJson).toContain('"path": "src/new.ts"');
  });

  it("prefers backend-provided approval preview rows", () => {
    const [card] = computeApprovalCards([
      {
        eventId: "evt_requested",
        sessionId: "sess_1",
        taskId: "task_1",
        type: "approval.requested",
        ts: 1778734170000,
        payload: {
          approvalId: "approval_web",
          taskId: "task_1",
          kind: "network_access",
          preview: [
            { label: "方法", value: "POST" },
            { label: "URL", value: "https://example.com/api" },
          ],
          request: {
            method: "GET",
            url: "https://fallback.example.com",
          },
        },
      },
    ]);

    expect(card.previewRows).toEqual([
      { label: "方法", value: "POST" },
      { label: "URL", value: "https://example.com/api" },
    ]);
    expect(card.requestSummary).toBe("command | cwd .");
  });

  it("falls back to notebook execution approval preview rows", () => {
    const [card] = computeApprovalCards([
      {
        eventId: "evt_requested",
        sessionId: "sess_1",
        taskId: "task_1",
        type: "approval.requested",
        ts: 1778734170000,
        payload: {
          approvalId: "approval_notebook",
          taskId: "task_1",
          kind: "run_command",
          request: {
            command: "notebook execute_cell analysis.ipynb #cell 0",
            cwd: ".",
            shell: "python",
            timeoutMs: 5000,
            toolName: "notebook",
            notebookAction: "execute_cell",
            path: "analysis.ipynb",
            cellIndex: 0,
            reason: "Notebook cell execution runs Python code in a subprocess.",
          },
        },
      },
    ]);

    expect(card.previewRows).toEqual([
      { label: "命令", value: "notebook execute_cell analysis.ipynb #cell 0" },
      { label: "Notebook", value: "analysis.ipynb" },
      { label: "Cell", value: "0" },
      { label: "目录", value: "." },
      { label: "Shell", value: "python" },
    ]);
  });

  it("shows computer-use approval target and coordinate preview rows", () => {
    const [card] = computeApprovalCards([
      {
        eventId: "evt_requested",
        sessionId: "sess_1",
        taskId: "task_1",
        type: "approval.requested",
        ts: 1778734170000,
        payload: {
          approvalId: "approval_computer",
          taskId: "task_1",
          kind: "computer_use",
          request: {
            app: "VS Code",
            action: "click",
            selector: "Run button",
            x: 320,
            y: 180,
            url: "http://localhost:5173",
            pageId: "page_1",
            permission: "Click the VS Code run button.",
          },
        },
      },
    ]);

    expect(card.previewRows).toEqual([
      { label: "应用", value: "VS Code" },
      { label: "动作", value: "click" },
      { label: "目标", value: "Run button" },
      { label: "坐标", value: "320, 180" },
      { label: "URL", value: "http://localhost:5173" },
      { label: "Page", value: "page_1" },
      { label: "权限", value: "Click the VS Code run button." },
    ]);
  });

  it("summarizes verification review evidence", () => {
    const [card] = computeApprovalCards([
      approvalRequested({
        reason: "Code files changed without targeted verification.",
        completionEvidence: {
          evidenceLevel: "verified",
          status: "success",
          counts: {
            changedFiles: 2,
            passedVerification: 2,
          },
          verificationRequirements: {
            required: ["javascript"],
            matched: ["python"],
            missing: ["javascript"],
            status: "missing",
          },
          changedFiles: [{ path: "src/feature.ts" }],
          verification: [
            { name: "git_status", status: "passed" },
            { name: "git_diff", status: "passed" },
          ],
        },
      }),
    ]);

    expect(card.completionEvidence?.gateStatus).toBe("needs_verification");
    expect(card.completionEvidence?.metrics).toContainEqual({ label: "files", value: "2" });
    expect(card.completionEvidence?.issues).toContain("Code/test changes need targeted test, build, or typecheck verification.");
    expect(card.completionEvidence?.issues).toContain("Missing framework verification: javascript");
  });

  it("summarizes acceptance and tool failure evidence", () => {
    const [card] = computeApprovalCards([
      approvalRequested({
        reason: "Acceptance criteria require review.",
        completionEvidence: {
          evidenceLevel: "verified",
          counts: {
            failedAcceptanceCriteria: 1,
            failedToolResults: 1,
          },
          acceptance: [
            { criterion: "Feature works", status: "supported" },
            { criterion: "Docs updated", status: "failed" },
          ],
          unresolvedToolFailures: [
            { name: "run_command", summary: "pytest failed" },
          ],
        },
      }),
    ]);

    expect(card.completionEvidence?.gateStatus).toBe("needs_tool_review");
    expect(card.completionEvidence?.metrics).toContainEqual({ label: "failed criteria", value: "1" });
    expect(card.completionEvidence?.metrics).toContainEqual({ label: "tool failures", value: "1" });
    expect(card.completionEvidence?.issues).toContain("failed: Docs updated");
    expect(card.completionEvidence?.issues).toContain("run_command: pytest failed");
  });

  it("surfaces advisor evidence adapter status", () => {
    const [card] = computeApprovalCards([
      approvalRequested({
        reason: "Advisor requested browser evidence before completion.",
        completionEvidence: {
          evidenceLevel: "runtime_evidence",
          counts: {},
          advisorEvidenceAdapters: {
            status: "approval_required",
            counts: {
              total: 1,
              approvalRequired: 1,
            },
            adapters: [
              {
                adapterKind: "browser_inspection_adapter",
                status: "approval_required",
                summary: "browser inspection needs approval",
              },
            ],
          },
        },
      }),
    ]);

    expect(card.completionEvidence?.advisorEvidenceAdapters?.status).toBe("approval_required");
    expect(card.completionEvidence?.metrics).toContainEqual({ label: "evidence adapters", value: "1" });
    expect(card.completionEvidence?.metrics).toContainEqual({ label: "adapter approvals", value: "1" });
    expect(card.completionEvidence?.issues).toContain("browser_inspection_adapter: browser inspection needs approval");
  });

  it("attaches completion review conclusions from resolved approvals", () => {
    const [card] = computeApprovalCards([
      approvalRequested({
        reason: "Completion requires review.",
        completionEvidence: {
          evidenceLevel: "summary_only",
          counts: {},
          audit: {
            approvalCounts: {
              total: 1,
              approved: 1,
              rejected: 0,
              pending: 0,
            },
            approvals: [
              {
                approvalId: "approval_1",
                kind: "completion_review",
                decision: "approved",
                decidedBy: "user",
                summary: "Completion review approved by user.",
              },
            ],
            completionAdvisor: {
              accepted: true,
              source: "llm",
              confidence: 0.8,
              proposalRecordId: "proposal_1",
            },
          },
        },
      }),
      {
        eventId: "evt_resolved",
        sessionId: "sess_1",
        taskId: "task_1",
        type: "approval.resolved",
        ts: 1778734169000,
        payload: {
          approvalId: "approval_1",
          taskId: "task_1",
          decision: "approved",
          completionReviewConclusion: {
            approvalId: "approval_1",
            decision: "approved",
            decidedBy: "user",
            summary: "Completion review approved by user.",
          },
        },
      },
    ]);

    expect(card.status).toBe("approved");
    expect(card.completionEvidence?.reviewConclusion?.decision).toBe("approved");
    expect(card.completionEvidence?.reviewConclusion?.decidedBy).toBe("user");
    expect(card.completionEvidence?.audit?.approvalCounts?.approved).toBe(1);
    expect(card.completionEvidence?.audit?.approvals?.[0]?.kind).toBe("completion_review");
    expect(card.completionEvidence?.audit?.completionAdvisor?.proposalRecordId).toBe("proposal_1");
  });
});

describe("computePatchCards", () => {
  it("keeps changed paths when patch diff text is not loaded yet", () => {
    const [card] = computePatchCards(
      [
        {
          eventId: "evt_patch",
          sessionId: "sess_1",
          taskId: "task_1",
          type: "patch.proposed",
          ts: 1778734168000,
          payload: {
            patchId: "patch_1",
            summary: "Update two files",
            changedPaths: ["src/app.ts", "src/app.ts", "src/view.tsx"],
          },
        },
      ],
      {},
      new Map(),
    );

    expect(card.filesChanged).toBe(2);
    expect(card.changedPaths).toEqual(["src/app.ts", "src/view.tsx"]);
    expect(parsePatchFiles(card.diffText, card.changedPaths).map((file) => file.path)).toEqual(["src/app.ts", "src/view.tsx"]);
  });

  it("uses patch lifecycle event status after proposal", () => {
    const [card] = computePatchCards(
      [
        {
          eventId: "evt_patch_1",
          sessionId: "sess_1",
          taskId: "task_1",
          type: "patch.proposed",
          ts: 10,
          payload: {
            patchId: "patch_1",
            summary: "Update file",
            changedPaths: ["src/app.ts"],
          },
        },
        {
          eventId: "evt_patch_2",
          sessionId: "sess_1",
          taskId: "task_1",
          type: "patch.reverted",
          ts: 20,
          payload: {
            patchId: "patch_1",
            summary: "Update file",
            status: "reverted",
            changedPaths: ["src/app.ts"],
          },
        },
      ],
      {},
      new Map(),
    );

    expect(card.status).toBe("reverted");
    expect(card.updatedAt).toBe(20);
    expect(card.changedPaths).toEqual(["src/app.ts"]);
  });

  it("does not let approval status overwrite a terminal patch lifecycle state", () => {
    const [card] = computePatchCards(
      [
        {
          eventId: "evt_patch",
          sessionId: "sess_1",
          taskId: "task_1",
          type: "patch.reverted",
          ts: 20,
          payload: {
            patchId: "patch_1",
            summary: "Update file",
            status: "reverted",
            changedPaths: ["src/app.ts"],
          },
        },
      ],
      {},
      new Map([
        [
          "patch_1",
          {
            approvalId: "approval_1",
            taskId: "task_1",
            kind: "apply_patch",
            status: "approved",
            title: "Patch approval",
            command: "",
            cwd: "",
            shell: "",
            timeoutMs: 0,
            risk: "low",
            requestedAt: 10,
            updatedAt: 30,
            requestSummary: "",
            requestJson: "",
            eventCount: 1,
          },
        ],
      ]),
    );

    expect(card.status).toBe("reverted");
    expect(card.approvalStatus).toBe("approved");
    expect(card.updatedAt).toBe(30);
  });
});
