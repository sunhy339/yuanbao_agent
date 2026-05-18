import { describe, expect, it } from "vitest";
import { computeApprovalCards, type AgentEventLike } from "./viewComputations";

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

describe("computeApprovalCards completion evidence", () => {
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
