/**
 * Pure view computation functions extracted from App.tsx useMemo blocks.
 * Each function takes raw state and returns derived view data.
 */

import type {
  ApprovalRequestedPayload,
  ApprovalResolvedPayload,
  PatchProposedPayload,
  ToolLifecyclePayload,
} from "@shared";
import type {
  ApprovalCompletionEvidenceView,
  ApprovalCardView,
  PatchCardView,
  ToolTimelineItem,
} from "./eventRecordViews";
import type { PatchRecord } from "@shared";
import {
  readRequestText,
  readRequestNumber,
  readRequestPatchId,
  readEventText,
  readEventNumber,
  summarizeValue,
  getPayloadValue,
  formatRawValue,
  summarizeToolArguments,
  summarizeToolResult,
} from "./traceReaders";
import {
  readRequestStringList,
  readRequestOptionalNumber,
  stringifyRequestJson,
  sortByUpdatedAtDesc,
} from "./eventRecordViews";

export interface AgentEventLike {
  type: string;
  payload: any;
  taskId?: string;
  sessionId?: string;
  eventId: string;
  ts: number;
}

export function computeToolTimelineItems(events: AgentEventLike[]): ToolTimelineItem[] {
  const items = new Map<string, ToolTimelineItem>();

  for (const event of events) {
    if (
      event.type !== "tool.started" &&
      event.type !== "tool.completed" &&
      event.type !== "tool.failed"
    ) {
      continue;
    }

    const payload = event.payload as Partial<ToolLifecyclePayload>;
    const toolCallId = payload.toolCallId ?? event.eventId;
    const current = items.get(toolCallId);
    const status = event.type.replace("tool.", "") as ToolTimelineItem["status"];
    const toolName = payload.toolName ?? current?.toolName ?? "unknown_tool";
    const argumentValue = payload.arguments ?? getPayloadValue(event.payload, ["args", "input", "parameters"]);
    const resultValue = getPayloadValue(event.payload, ["result", "output", "content", "summary"]);
    const errorValue = getPayloadValue(event.payload, ["error", "errorJson", "message"]);
    const durationMs =
      readEventNumber(event.payload, "durationMs") ??
      (current ? event.ts - current.startedAt : undefined);
    const resultSummary = summarizeToolResult(toolName, resultValue, errorValue, current?.resultSummary ?? "等待结果");
    const errorSummary = summarizeValue(errorValue, "");

    items.set(toolCallId, {
      id: toolCallId,
      taskId: event.taskId ?? "",
      toolCallId,
      toolName,
      status,
      argsSummary: summarizeToolArguments(toolName, argumentValue, current?.argsSummary ?? "未记录参数"),
      resultSummary,
      errorSummary: errorSummary || current?.errorSummary,
      argsRaw: formatRawValue(argumentValue) ?? current?.argsRaw,
      resultRaw: formatRawValue(resultValue ?? errorValue) ?? current?.resultRaw,
      startedAt: current?.startedAt ?? event.ts,
      updatedAt: event.ts,
      finishedAt: status === "started" ? current?.finishedAt : event.ts,
      durationMs: status === "started" ? current?.durationMs : durationMs,
      eventCount: (current?.eventCount ?? 0) + 1,
    });
  }

  return Array.from(items.values()).sort((left, right) => right.updatedAt - left.updatedAt);
}

export function computeApprovalCards(events: AgentEventLike[]): ApprovalCardView[] {
  const cards = new Map<string, ApprovalCardView>();

  for (const event of events) {
    if (event.type === "approval.requested") {
      const payload = event.payload as ApprovalRequestedPayload;
      const request = payload.request as Record<string, unknown>;
      const filesChanged = readRequestOptionalNumber(request, ["filesChanged", "files_changed"]);
      const changedFiles = readRequestStringList(request, ["files", "filesChangedList", "paths"]);
      const patchId = payload.kind === "apply_patch" ? payload.patchId ?? readRequestPatchId(request) : undefined;
      const isWorktreeMerge = payload.kind === "worktree_merge";
      const isCompletionReview = payload.kind === "completion_review";
      const completionEvidence = isCompletionReview ? buildCompletionEvidenceView(request) : undefined;
      const worktreeBranch = readRequestText(request, "branchName", "worktree");
      const worktreeTarget = readRequestText(request, "targetBranch", "main");
      const patchSummary = isWorktreeMerge
        ? `Worktree merge ${worktreeBranch} -> ${worktreeTarget}`
        : isCompletionReview
          ? "Completion review required"
        : readRequestText(request, "summary", readRequestText(request, "patchSummary", "patch approval request"));
      const command = readRequestText(
        request,
        "command",
        payload.kind === "apply_patch"
          ? "apply_patch"
          : isWorktreeMerge
            ? `merge ${worktreeBranch} -> ${worktreeTarget}`
            : isCompletionReview
              ? "review completion evidence"
              : "command",
      );
      const cwd = readRequestText(request, "cwd", readRequestText(request, "workspaceRoot", readRequestText(request, "worktreePath", ".")));
      cards.set(payload.approvalId, {
        approvalId: payload.approvalId,
        taskId: payload.taskId,
        kind: payload.kind,
        patchId,
        patchSummary,
        filesChanged,
        command,
        cwd,
        shell: readRequestText(request, "shell", "system default"),
        timeoutMs: readRequestNumber(request, "timeoutMs", 0),
        risk: readRequestText(
          request,
          "risk",
          payload.kind === "apply_patch"
            ? "writes files"
            : isCompletionReview
              ? completionEvidence?.gateStatus ?? "completion evidence requires review"
              : "executes command",
        ),
        requestJson: stringifyRequestJson(request),
        requestSummary:
          payload.kind === "apply_patch"
            ? `${patchSummary}${filesChanged !== undefined ? ` | ${filesChanged} file(s)` : ""}${
                changedFiles.length > 0 ? ` | ${changedFiles.slice(0, 3).join(", ")}` : ""
              }`
            : isWorktreeMerge
              ? `${command} | ${readRequestText(request, "diffStat", "diff reviewed")}`
              : isCompletionReview
                ? completionEvidence?.summary ?? `${readRequestText(request, "reason", "completion evidence requires review")} | ${readRequestText(request, "summary", "").slice(0, 120)}`
              : `${command} | cwd ${cwd}`,
        completionEvidence,
        status: "pending",
        requestedAt: event.ts,
        updatedAt: event.ts,
        requestedEventId: event.eventId,
      });
    }

    if (event.type === "approval.resolved") {
      const payload = event.payload as ApprovalResolvedPayload;
      const current = cards.get(payload.approvalId);
      if (current) {
        cards.set(payload.approvalId, {
          ...current,
          status: payload.decision,
          resolvedAt: event.ts,
          updatedAt: event.ts,
          resolvedEventId: event.eventId,
        });
        continue;
      }

      cards.set(payload.approvalId, {
        approvalId: payload.approvalId,
        taskId: payload.taskId,
        kind: "run_command",
        patchId: undefined,
        command: "unknown",
        cwd: ".",
        shell: "system default",
        timeoutMs: 0,
        risk: "not recorded",
        requestJson: "{}",
        requestSummary: "Resolved approval was received before the request event.",
        status: payload.decision,
        requestedAt: event.ts,
        updatedAt: event.ts,
        resolvedAt: event.ts,
        resolvedEventId: event.eventId,
      });
    }
  }

  return sortByUpdatedAtDesc(Array.from(cards.values()));
}

function buildCompletionEvidenceView(request: Record<string, unknown>): ApprovalCompletionEvidenceView | undefined {
  const evidence = readRecord(request["completionEvidence"]);
  if (!evidence) return undefined;
  const structuredResult = readRecord(request["structuredResult"]);
  const completionGate = readRecord(structuredResult?.["completionGate"]);
  const counts = readRecord(evidence["counts"]);
  const reason = readString(request["reason"]) ?? readString(completionGate?.["reason"]);
  const risk = readString(request["risk"]);
  const gateStatus = readString(completionGate?.["status"]) ?? inferCompletionGateStatus(counts, reason, risk, evidence);
  const evidenceLevel = readString(evidence["evidenceLevel"]);
  const status = readString(evidence["status"]);

  const metrics = [
    countMetric(counts, "changedFiles", "files"),
    countMetric(counts, "passedVerification", "verified"),
    countMetric(counts, "failedVerification", "failed checks"),
    countMetric(counts, "failedAcceptanceCriteria", "failed criteria"),
    countMetric(counts, "unverifiedAcceptanceCriteria", "unverified criteria"),
    countMetric(counts, "failedToolResults", "tool failures"),
  ].filter((item): item is { label: string; value: string } => Boolean(item));

  const issues = [
    ...summarizeAcceptanceIssues(evidence["acceptance"]),
    ...summarizeToolFailures(evidence["unresolvedToolFailures"]),
    ...summarizeVerificationGap(gateStatus, evidence),
  ].slice(0, 5);

  return {
    gateStatus,
    evidenceLevel,
    status,
    summary: compactCompletionSummary(reason, gateStatus, evidenceLevel),
    metrics,
    issues,
  };
}

function inferCompletionGateStatus(
  counts: Record<string, unknown> | undefined,
  reason: string | undefined,
  risk: string | undefined,
  evidence: Record<string, unknown>,
): string | undefined {
  const text = `${reason ?? ""} ${risk ?? ""}`.toLowerCase();
  if (
    countMetric(counts, "failedToolResults", "tool failures") ||
    (Array.isArray(evidence["unresolvedToolFailures"]) && evidence["unresolvedToolFailures"].length > 0)
  ) {
    return "needs_tool_review";
  }
  if (countMetric(counts, "failedAcceptanceCriteria", "failed criteria") || countMetric(counts, "unverifiedAcceptanceCriteria", "unverified criteria")) {
    return "needs_acceptance_review";
  }
  if (text.includes("acceptance")) {
    return "needs_acceptance_review";
  }
  if (text.includes("tool")) {
    return "needs_tool_review";
  }
  if (text.includes("verification") || text.includes("verified")) {
    return "needs_verification";
  }
  return "needs_user_review";
}

function countMetric(counts: Record<string, unknown> | undefined, key: string, label: string): { label: string; value: string } | null {
  const value = counts?.[key];
  if (typeof value !== "number" || !Number.isFinite(value) || value <= 0) return null;
  return { label, value: String(value) };
}

function summarizeAcceptanceIssues(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value
    .filter((item) => {
      const record = readRecord(item);
      const status = readString(record?.["status"]);
      return status === "failed" || status === "unsupported" || status === "unverified";
    })
    .map((item) => {
      const record = readRecord(item);
      const criterion = readString(record?.["criterion"]) ?? "criterion";
      const status = readString(record?.["status"]) ?? "needs review";
      return `${status}: ${criterion}`;
    });
}

function summarizeToolFailures(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.map((item) => {
    const record = readRecord(item);
    const name = readString(record?.["name"]) ?? "tool";
    const summary = readString(record?.["summary"]) ?? readString(record?.["status"]) ?? "failed";
    return `${name}: ${summary}`;
  });
}

function summarizeVerificationGap(gateStatus: string | undefined, evidence: Record<string, unknown>): string[] {
  if (gateStatus !== "needs_verification") return [];
  const changedFiles = Array.isArray(evidence["changedFiles"]) ? evidence["changedFiles"].length : 0;
  const verification = Array.isArray(evidence["verification"]) ? evidence["verification"].length : 0;
  if (changedFiles > 0 && verification > 0) {
    return ["Code/test changes need targeted test, build, or typecheck verification."];
  }
  return ["Write evidence needs passing verification before completion."];
}

function compactCompletionSummary(reason: string | undefined, gateStatus: string | undefined, evidenceLevel: string | undefined): string {
  const gate = gateStatus ? gateStatus.replace(/^needs_/, "").replace(/_/g, " ") : "review";
  const level = evidenceLevel ? `evidence: ${evidenceLevel}` : "evidence review";
  const text = reason?.trim();
  if (!text) return `${gate} required | ${level}`;
  return `${gate} required | ${text}`;
}

function readRecord(value: unknown): Record<string, unknown> | undefined {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : undefined;
}

function readString(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value.trim() : undefined;
}

export function computeApprovalByPatchId(approvalCards: ApprovalCardView[]): Map<string, ApprovalCardView> {
  const cards = new Map<string, ApprovalCardView>();
  for (const approval of approvalCards) {
    if (approval.patchId && !cards.has(approval.patchId)) {
      cards.set(approval.patchId, approval);
    }
  }
  return cards;
}

export function computePatchCards(
  events: AgentEventLike[],
  patchCacheById: Record<string, PatchRecord>,
  approvalByPatchId: Map<string, ApprovalCardView>,
): PatchCardView[] {
  const cards = new Map<string, PatchCardView>();

  for (const event of events) {
    if (event.type === "patch.proposed") {
      const payload = event.payload as PatchProposedPayload;
      const patchId = readEventText(payload, "patchId");
      if (!patchId) continue;
      const diffText = readEventText(payload, "diffText");
      cards.set(patchId, {
        patchId,
        taskId: event.taskId ?? "",
        summary: payload.summary ?? "",
        filesChanged: payload.filesChanged ?? 0,
        status: "proposed",
        requestedAt: event.ts,
        updatedAt: event.ts,
        diffText,
      });
    }
  }

  for (const [patchId, patch] of Object.entries(patchCacheById)) {
    const current = cards.get(patchId);
    cards.set(patchId, {
      patchId,
      taskId: patch.taskId,
      summary: patch.summary,
      filesChanged: patch.filesChanged,
      status: patch.status,
      requestedAt: current?.requestedAt ?? patch.createdAt,
      updatedAt: Math.max(current?.updatedAt ?? patch.updatedAt, patch.updatedAt),
      diffText: patch.diffText,
    });
  }

  for (const card of cards.values()) {
    const approval = approvalByPatchId.get(card.patchId);
    if (approval) {
      card.approvalId = approval.approvalId;
      card.approvalStatus = approval.status;
      card.approvalResolvedAt = approval.resolvedAt;
      card.updatedAt = Math.max(card.updatedAt, approval.updatedAt);
      if (approval.status === "approved") {
        card.status = "approved";
      } else if (approval.status === "rejected") {
        card.status = "rejected";
      }
    }
  }

  return sortByUpdatedAtDesc(Array.from(cards.values()));
}
