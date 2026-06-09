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
  PreviewSectionItemView,
  PreviewSectionView,
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

function readPreviewRows(value: unknown): Array<{ label: string; value: string }> | undefined {
  if (!Array.isArray(value)) {
    return undefined;
  }
  const rows = value
    .map((row) => {
      if (!row || typeof row !== "object") {
        return null;
      }
      const record = row as Record<string, unknown>;
      const label = typeof record.label === "string" ? record.label.trim() : "";
      const rowValue = typeof record.value === "string" ? record.value.trim() : "";
      return label && rowValue ? { label, value: rowValue } : null;
    })
    .filter((row): row is { label: string; value: string } => row !== null);
  return rows.length ? rows.slice(0, 5) : undefined;
}

export interface AgentEventLike {
  type: string;
  payload: any;
  taskId?: string;
  sessionId?: string;
  eventId: string;
  ts: number;
}

function normalizeChangedPaths(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return [];
  }
  const seen = new Set<string>();
  const paths: string[] = [];
  value.forEach((item) => {
    const path = typeof item === "string" ? item.trim().replace(/\\/g, "/") : "";
    if (!path || seen.has(path)) {
      return;
    }
    seen.add(path);
    paths.push(path);
  });
  return paths;
}

export function computeToolTimelineItems(events: AgentEventLike[]): ToolTimelineItem[] {
  const items = new Map<string, ToolTimelineItem>();

  for (const event of events) {
    if (
      event.type !== "tool.started" &&
      event.type !== "tool.completed" &&
      event.type !== "tool.failed" &&
      event.type !== "tool.blocked"
    ) {
      continue;
    }

    const payload = event.payload as Partial<ToolLifecyclePayload>;
    const toolCallId = payload.toolCallId ?? event.eventId;
    const current = items.get(toolCallId);
    const status = event.type.replace("tool.", "") as ToolTimelineItem["status"];
    const toolName = payload.toolName ?? current?.toolName ?? "unknown_tool";
    const rawArgumentValue = payload.arguments ?? getPayloadValue(event.payload, ["args", "input", "parameters"]);
    const argumentValue = payload.inputSummary ?? rawArgumentValue;
    const rawResultValue = getPayloadValue(event.payload, ["result", "output", "content", "summary"]);
    const resultValue = payload.resultSummary ?? rawResultValue;
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
      parentToolUseId: readEventText(event.payload, "parentToolUseId") ?? current?.parentToolUseId,
      toolGroupId: readEventText(event.payload, "toolGroupId") ?? current?.toolGroupId,
      toolIndex: readEventNumber(event.payload, "toolIndex") ?? current?.toolIndex,
      toolTotal: readEventNumber(event.payload, "toolTotal") ?? current?.toolTotal,
      toolOperationId: readEventText(event.payload, "toolOperationId") ?? current?.toolOperationId,
      toolOperationLabel: readEventText(event.payload, "toolOperationLabel") ?? current?.toolOperationLabel,
      toolCategory: readEventText(event.payload, "toolCategory") ?? current?.toolCategory,
      toolPhaseId: readEventText(event.payload, "toolPhaseId") ?? current?.toolPhaseId,
      toolPhaseLabel: readEventText(event.payload, "toolPhaseLabel") ?? current?.toolPhaseLabel,
      toolSemanticParentId: readEventText(event.payload, "toolSemanticParentId") ?? current?.toolSemanticParentId,
      toolSemanticParentLabel: readEventText(event.payload, "toolSemanticParentLabel") ?? current?.toolSemanticParentLabel,
      toolName,
      target: readEventText(event.payload, "target") ?? current?.target,
      status,
      argsSummary: summarizeToolArguments(toolName, argumentValue, current?.argsSummary ?? "未记录参数"),
      resultSummary,
      resultPreview: readPreviewRows(payload.resultPreview) ?? current?.resultPreview,
      errorSummary: errorSummary || current?.errorSummary,
      argsRaw: formatRawValue(rawArgumentValue) ?? current?.argsRaw,
      resultRaw: formatRawValue(rawResultValue ?? errorValue) ?? current?.resultRaw,
      startedAt: current?.startedAt ?? event.ts,
      updatedAt: event.ts,
      finishedAt: status === "started" ? current?.finishedAt : event.ts,
      durationMs: status === "started" ? current?.durationMs : durationMs,
      eventCount: (current?.eventCount ?? 0) + 1,
    });
  }

  return Array.from(items.values()).sort((left, right) => right.updatedAt - left.updatedAt);
}

type ApprovalEventPayloadDetails = {
  approvalId: string;
  taskId: string;
  kind?: ApprovalRequestedPayload["kind"];
  request?: Record<string, unknown>;
  internal?: boolean;
  _bridge?: unknown;
  preview?: unknown;
  patchId?: string;
  filesChanged?: number;
  changedPaths?: unknown;
  diffText?: string;
  completionReviewConclusion?: unknown;
};

function isInternalCompletionReviewApproval(payload: ApprovalEventPayloadDetails): boolean {
  if (payload.kind !== "completion_review") {
    return false;
  }
  const bridge = readRecord(payload._bridge);
  return Boolean(
    payload.internal === true ||
      bridge?.internal === true ||
      bridge?.suppressChatReplay === true ||
      bridge?.suppressRealtimeFlat === true,
  );
}

function buildApprovalCardView(
  payload: ApprovalEventPayloadDetails,
  event: AgentEventLike,
  current: ApprovalCardView | undefined,
  status: ApprovalCardView["status"],
  timing: {
    requestedAt: number;
    requestedEventId?: string;
    resolvedAt?: number;
    resolvedEventId?: string;
  },
): ApprovalCardView {
  const request = readRecord(payload.request) ?? {};
  const hasRequestDetails =
    Boolean(payload.kind) ||
    Object.keys(request).length > 0 ||
    payload.preview !== undefined ||
    payload.filesChanged !== undefined ||
    payload.changedPaths !== undefined ||
    payload.diffText !== undefined ||
    payload.patchId !== undefined;
  const kind = payload.kind ?? current?.kind ?? "run_command";
  const payloadChangedPaths = normalizeChangedPaths(payload.changedPaths);
  const requestChangedPaths = normalizeChangedPaths(readRequestStringList(request, ["changedPaths", "files", "filesChangedList", "changedFiles", "paths", "path", "file"]));
  const changedPaths = payloadChangedPaths.length
    ? payloadChangedPaths
    : requestChangedPaths.length
      ? requestChangedPaths
      : current?.changedPaths ?? [];
  const explicitFilesChanged = readRequestOptionalNumber(request, ["filesChanged", "files_changed"]) ??
    (typeof payload.filesChanged === "number" && Number.isFinite(payload.filesChanged) ? payload.filesChanged : undefined);
  const filesChanged = explicitFilesChanged ?? current?.filesChanged ?? (changedPaths.length ? changedPaths.length : undefined);
  const payloadDiffText = readEventText(payload, "diffText") ?? readRequestText(request, "diffText", "");
  const diffText = payloadDiffText || current?.diffText;
  const patchId = kind === "apply_patch"
    ? payload.patchId ?? readRequestPatchId(request) ?? current?.patchId
    : undefined;
  const isWorktreeMerge = kind === "worktree_merge";
  const isCompletionReview = kind === "completion_review";
  const isPlanApproval = kind === "plan";
  const completionEvidence = isCompletionReview ? buildCompletionEvidenceView(request) : undefined;
  const worktreeBranch = readRequestText(request, "branchName", "worktree");
  const worktreeTarget = readRequestText(request, "targetBranch", "main");
  const worktreeDiffSummary = isWorktreeMerge ? buildWorktreeDiffSummary(request) : "";
  const worktreeReviewSummary = isWorktreeMerge ? buildWorktreeReviewSummary(request) : "";
  const worktreeStrategySummary = isWorktreeMerge ? buildWorktreeStrategySummary(request) : "";
  const patchSummary = isWorktreeMerge
    ? `Worktree merge ${worktreeBranch} -> ${worktreeTarget}`
    : isCompletionReview
      ? "Completion review required"
      : isPlanApproval
        ? "计划审批"
      : readRequestText(request, "summary", readRequestText(request, "patchSummary", current?.patchSummary ?? "patch approval request"));
  const defaultCommand = !hasRequestDetails && !current
    ? "unknown"
    : kind === "apply_patch"
      ? "apply_patch"
      : isWorktreeMerge
        ? `merge ${worktreeBranch} -> ${worktreeTarget}`
        : isCompletionReview
          ? "review completion evidence"
          : isPlanApproval
            ? "approve execution plan"
          : current?.command ?? "command";
  const command = readRequestText(request, "command", defaultCommand);
  const cwd = readRequestText(request, "cwd", readRequestText(request, "workspaceRoot", readRequestText(request, "worktreePath", current?.cwd ?? ".")));
  const risk = readRequestText(
    request,
    "risk",
    current?.risk ??
      (kind === "apply_patch"
        ? "writes files"
        : isCompletionReview
          ? completionEvidence?.gateStatus ?? "completion evidence requires review"
          : isPlanApproval
            ? "plan requires approval before execution"
          : "executes command"),
  );
  const requestSummary = !hasRequestDetails
    ? current?.requestSummary ?? "Resolved approval was received before the request event."
    : kind === "apply_patch" || kind === "write_file"
      ? `${patchSummary}${filesChanged !== undefined ? ` | ${filesChanged} file(s)` : ""}${
          changedPaths.length > 0 ? ` | ${changedPaths.slice(0, 3).join(", ")}` : ""
        }`
      : isWorktreeMerge
        ? compactSummary([command, worktreeDiffSummary, worktreeReviewSummary, worktreeStrategySummary])
        : isCompletionReview
          ? completionEvidence?.summary ?? `${readRequestText(request, "reason", "completion evidence requires review")} | ${readRequestText(request, "summary", "").slice(0, 120)}`
          : isPlanApproval
            ? summarizePlanApprovalRequest(request)
          : `${command} | cwd ${cwd}`;
  const previewRows = readApprovalPreviewRows(payload.preview, request, kind);
  const previewSections = readPreviewSections(request["previewSections"]) ?? (isPlanApproval ? readPlanPreviewSections(request) : undefined);
  const mergedCompletionEvidence = mergeCompletionReviewConclusion(
    mergeCompletionReviewConclusion(completionEvidence ?? current?.completionEvidence, current?.completionEvidence?.reviewConclusion),
    payload.completionReviewConclusion,
  );

  return {
    ...current,
    approvalId: payload.approvalId,
    taskId: payload.taskId,
    kind,
    patchId,
    patchSummary,
    filesChanged,
    changedPaths,
    diffText: diffText || undefined,
    command,
    cwd,
    shell: readRequestText(request, "shell", current?.shell ?? "system default"),
    timeoutMs: readRequestNumber(request, "timeoutMs", current?.timeoutMs ?? 0),
    risk,
    requestJson: Object.keys(request).length ? stringifyRequestJson(request) : current?.requestJson ?? "{}",
    requestSummary,
    previewRows: previewRows.length ? previewRows : current?.previewRows ?? [],
    previewSections: previewSections?.length ? previewSections : current?.previewSections,
    completionEvidence: mergedCompletionEvidence,
    status,
    requestedAt: timing.requestedAt,
    updatedAt: event.ts,
    requestedEventId: timing.requestedEventId,
    resolvedAt: timing.resolvedAt,
    resolvedEventId: timing.resolvedEventId,
  };
}

export function computeApprovalCards(events: AgentEventLike[]): ApprovalCardView[] {
  const cards = new Map<string, ApprovalCardView>();

  for (const event of events) {
    if (event.type === "approval.requested") {
      const payload = event.payload as ApprovalRequestedPayload;
      if (isInternalCompletionReviewApproval(payload)) {
        cards.delete(payload.approvalId);
        continue;
      }
      const current = cards.get(payload.approvalId);
      cards.set(payload.approvalId, buildApprovalCardView(payload, event, current, current?.status ?? "pending", {
        requestedAt: event.ts,
        requestedEventId: event.eventId,
        resolvedAt: current?.resolvedAt,
        resolvedEventId: current?.resolvedEventId,
      }));
    }

    if (event.type === "approval.resolved") {
      const payload = event.payload as ApprovalResolvedPayload;
      if (isInternalCompletionReviewApproval(payload)) {
        cards.delete(payload.approvalId);
        continue;
      }
      const current = cards.get(payload.approvalId);
      cards.set(payload.approvalId, buildApprovalCardView(payload, event, current, payload.decision, {
        requestedAt: current?.requestedAt ?? event.ts,
        resolvedAt: event.ts,
        requestedEventId: current?.requestedEventId,
        resolvedEventId: event.eventId,
      }));
    }
  }

  return sortByUpdatedAtDesc(Array.from(cards.values()));
}

function compactSummary(parts: string[]): string {
  return parts.map((part) => part.trim()).filter(Boolean).join(" | ");
}

function readPlanSubtaskCount(request: Record<string, unknown>): number | undefined {
  const explicit = readRequestOptionalNumber(request, ["subtaskCount", "taskCount"]);
  if (explicit !== undefined) return explicit;
  const subtasks = request["subtasks"];
  return Array.isArray(subtasks) ? subtasks.length : undefined;
}

function summarizePlanApprovalRequest(request: Record<string, unknown>): string {
  const mode = readRequestText(request, "orchestrationMode", readRequestText(request, "mode", "plan"));
  const count = readPlanSubtaskCount(request);
  const goal = readRequestText(request, "goal", "");
  return compactSummary([
    count !== undefined ? `已拆分 ${count} 个 ${mode} 子任务` : `${mode} 计划等待审批`,
    goal ? compactSummary([goal]).slice(0, 120) : "",
  ]);
}

function readPlanPreviewSections(request: Record<string, unknown>): PreviewSectionView[] {
  const subtasks = request["subtasks"];
  if (!Array.isArray(subtasks)) {
    return [];
  }
  const items: PreviewSectionItemView[] = subtasks
    .map<PreviewSectionItemView | null>((item, index) => {
      if (typeof item === "string") {
        const title = item.trim();
        return title ? { id: `sub-${index}`, title } : null;
      }
      const record = readRecord(item);
      if (!record) {
        return null;
      }
      const title = readString(record["title"]) || readString(record["subtaskTitle"]) || readString(record["summary"]) || readString(record["description"]);
      if (!title) {
        return null;
      }
      const dependencies = Array.isArray(record["dependencies"])
        ? record["dependencies"].map(readString).filter((value): value is string => Boolean(value))
        : undefined;
      const description = readString(record["description"]) || readString(record["summary"]);
      const meta = [readString(record["agentType"]) || readString(record["agent_type"]), dependencies?.length ? `依赖 ${dependencies.join(", ")}` : undefined]
        .filter((value): value is string => Boolean(value));
      return {
        id: readString(record["id"]) || readString(record["subtaskId"]) || `sub-${index}`,
        title,
        ...(description ? { description } : {}),
        ...(meta.length ? { meta } : {}),
      };
    })
    .filter((item): item is PreviewSectionItemView => Boolean(item))
    .slice(0, 20);
  if (!items.length) {
    return [];
  }
  return [{ kind: "items", title: `已拆分 ${items.length} 个子任务`, items }];
}

function readPreviewSections(value: unknown): PreviewSectionView[] | undefined {
  if (!Array.isArray(value)) {
    return undefined;
  }
  const sections = value
    .map((section) => {
      const record = readRecord(section);
      if (!record || record["kind"] !== "items" || !Array.isArray(record["items"])) {
        return null;
      }
      const title = readString(record["title"]) || "详情";
      const items: PreviewSectionItemView[] = record["items"]
        .map<PreviewSectionItemView | null>((item, index) => {
          const itemRecord = readRecord(item);
          if (!itemRecord) {
            return null;
          }
          const itemTitle = readString(itemRecord["title"]);
          if (!itemTitle) {
            return null;
          }
          const description = readString(itemRecord["description"]);
          const meta = Array.isArray(itemRecord["meta"])
            ? itemRecord["meta"].map(readString).filter((entry): entry is string => Boolean(entry))
            : [];
          return {
            id: readString(itemRecord["id"]) || `item-${index}`,
            title: itemTitle,
            ...(description ? { description } : {}),
            ...(meta.length ? { meta } : {}),
          };
        })
        .filter((item): item is PreviewSectionItemView => Boolean(item));
      return items.length ? { kind: "items" as const, title, items: items.slice(0, 20) } : null;
    })
    .filter((section): section is PreviewSectionView => Boolean(section));
  return sections.length ? sections.slice(0, 4) : undefined;
}

function readApprovalPreviewRows(
  rawPreview: unknown,
  request: Record<string, unknown>,
  kind: string,
): Array<{ label: string; value: string }> {
  const requestPreview = readPreviewRows(request["previewRows"]);
  if (requestPreview?.length) {
    return requestPreview;
  }

  const fromPayload = Array.isArray(rawPreview)
    ? rawPreview
        .map((item) => readRecord(item))
        .filter((item): item is Record<string, unknown> => Boolean(item))
        .map((item) => ({
          label: readString(item["label"]) ?? "",
          value: readString(item["value"]) ?? "",
        }))
        .filter((item) => item.label && item.value)
    : [];
  if (fromPayload.length) {
    return fromPayload.slice(0, 5);
  }

  const row = (label: string, value: unknown) => {
    const text = readString(value) ?? (typeof value === "number" && Number.isFinite(value) ? String(value) : undefined);
    return text ? { label, value: text } : undefined;
  };
  const isNotebookExecution =
    kind === "run_command" && (request["toolName"] === "notebook" || request["notebookAction"] === "execute_cell");
  const coordinates =
    request["x"] !== undefined && request["x"] !== "" && request["y"] !== undefined && request["y"] !== ""
      ? `${String(request["x"])}, ${String(request["y"])}`
      : "";
  const scroll =
    request["direction"] !== undefined || request["amount"] !== undefined
      ? `${String(request["direction"] ?? "down")} ${String(request["amount"] ?? "")}`.trim()
      : "";
  const planOrder = Array.isArray(request["executionOrder"])
    ? request["executionOrder"].map((item) => String(item)).filter(Boolean).join(" -> ")
    : "";
  const rows =
    kind === "plan"
      ? [
          row("目标", request["goal"]),
          row("模式", request["orchestrationMode"] ?? request["mode"] ?? "plan"),
          row("子任务", readPlanSubtaskCount(request)),
          row("执行顺序", planOrder),
        ]
      : kind === "run_command"
      ? [
          row("命令", request["command"]),
          ...(isNotebookExecution ? [row("Notebook", request["path"]), row("Cell", request["cellIndex"])] : []),
          row("目录", request["cwd"] ?? request["workspaceRoot"]),
          row("Shell", request["shell"]),
          row("原因", request["policyReason"] ?? request["risk"] ?? request["reason"]),
        ]
      : kind === "network_access"
        ? [
            row("方法", request["method"] ?? "GET"),
            row("URL", request["url"]),
            row("原因", request["reason"] ?? request["risk"]),
          ]
        : kind === "computer_use"
          ? [
              row("应用", request["app"] ?? request["target"] ?? request["application"]),
              row("动作", request["action"]),
              row("目标", request["selector"] ?? request["target"]),
              row("坐标", coordinates),
              row("文本", request["text"]),
              row("滚动", scroll),
              row("URL", request["url"]),
              row("Page", request["pageId"] ?? request["browserContextId"]),
              row("权限", request["permission"] ?? request["summary"]),
              row("详情", request["details"]),
            ]
          : kind === "subagent_dispatch"
            ? [
                row("子任务", request["prompt"]),
                row("原因", request["reason"] ?? request["risk"]),
              ]
            : [
                row("摘要", request["summary"] ?? request["description"]),
                row("目标", request["target"] ?? request["path"] ?? request["url"]),
                row("原因", request["reason"] ?? request["risk"]),
              ];

  return rows.filter((item): item is { label: string; value: string } => Boolean(item)).slice(0, kind === "computer_use" ? 8 : 5);
}

function buildWorktreeDiffSummary(request: Record<string, unknown>): string {
  const summary = request["diffSummary"];
  const diffSummary = summary && typeof summary === "object" ? summary as Record<string, unknown> : {};
  const stat = readRequestText(diffSummary, "diffStat", readRequestText(request, "diffStat", "diff reviewed"));
  const bytes = readRequestOptionalNumber(diffSummary, ["bytes"]);
  const truncated = diffSummary["truncated"] === true || request["diffTruncated"] === true;
  const suffix = bytes !== undefined ? `${bytes} bytes${truncated ? ", preview truncated" : ""}` : truncated ? "preview truncated" : "";
  return compactSummary([stat, suffix]);
}

function buildWorktreeReviewSummary(request: Record<string, unknown>): string {
  const review = request["review"];
  const record = review && typeof review === "object" ? review as Record<string, unknown> : {};
  const status = readRequestText(record, "status", readRequestText(request, "reviewStatus", ""));
  const reviewer = readRequestText(record, "reviewer", "");
  const summary = readRequestText(record, "summary", readRequestText(request, "reviewerSummary", ""));
  if (!status && !reviewer && !summary) return "";
  return compactSummary([`review ${status || "pending"}`, reviewer, summary]);
}

function buildWorktreeStrategySummary(request: Record<string, unknown>): string {
  const strategy = request["multiAgentWorktreeStrategy"];
  const record = strategy && typeof strategy === "object" ? strategy as Record<string, unknown> : {};
  const name = readRequestText(record, "strategy", "");
  const role = readRequestText(record, "taskRole", "");
  if (!name && !role) return "";
  return compactSummary([`worktree ${name || "strategy"}`, role]);
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
  const advisorEvidenceAdapters = readAdvisorEvidenceAdapters(evidence["advisorEvidenceAdapters"]);

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
    ...summarizeVerificationRequirements(evidence["verificationRequirements"]),
  ].slice(0, 5);

  return {
    gateStatus,
    evidenceLevel,
    status,
    summary: compactCompletionSummary(reason, gateStatus, evidenceLevel),
    metrics,
    issues,
    advisorEvidenceAdapters,
    audit: readCompletionAudit(evidence["audit"]),
    reviewConclusion: readCompletionReviewConclusion(
      evidence["reviewConclusion"] ?? request["completionReviewConclusion"],
    ),
  };
}

function mergeCompletionReviewConclusion(
  evidence: ApprovalCompletionEvidenceView | undefined,
  rawConclusion: unknown,
): ApprovalCompletionEvidenceView | undefined {
  const reviewConclusion = readCompletionReviewConclusion(rawConclusion);
  if (!reviewConclusion) return evidence;
  if (!evidence) {
    return {
      summary: reviewConclusion.summary ?? "Completion review resolved.",
      metrics: [],
      issues: [],
      reviewConclusion,
    };
  }
  return {
    ...evidence,
    reviewConclusion,
  };
}

function readCompletionAudit(raw: unknown): ApprovalCompletionEvidenceView["audit"] | undefined {
  const record = readRecord(raw);
  if (!record) return undefined;
  const audit: NonNullable<ApprovalCompletionEvidenceView["audit"]> = {};
  const approvalCounts = readRecord(record["approvalCounts"]);
  if (approvalCounts) {
    const counts: NonNullable<NonNullable<ApprovalCompletionEvidenceView["audit"]>["approvalCounts"]> = {};
    for (const key of ["total", "approved", "rejected", "pending"] as const) {
      const value = approvalCounts[key];
      if (typeof value === "number" && Number.isFinite(value)) {
        counts[key] = value;
      }
    }
    if (Object.keys(counts).length) {
      audit.approvalCounts = counts;
    }
  }
  if (Array.isArray(record["approvals"])) {
    const approvals = record["approvals"]
      .map((item) => readRecord(item))
      .filter((item): item is Record<string, unknown> => Boolean(item))
      .map((item) => ({
        approvalId: readString(item["approvalId"]),
        kind: readString(item["kind"]),
        decision: readString(item["decision"]),
        decidedBy: readString(item["decidedBy"]),
        gateStatus: readString(item["gateStatus"]),
        evidenceLevel: readString(item["evidenceLevel"]),
        summary: readString(item["summary"]),
        reviewStatus: readString(item["reviewStatus"]),
        verificationStatus: readString(item["verificationStatus"]),
      }))
      .map((item) => Object.fromEntries(Object.entries(item).filter(([, value]) => value !== undefined)));
    if (approvals.length) {
      audit.approvals = approvals;
    }
  }
  const completionAdvisor = readRecord(record["completionAdvisor"]);
  if (completionAdvisor) {
    const advisor: NonNullable<NonNullable<ApprovalCompletionEvidenceView["audit"]>["completionAdvisor"]> = {};
    const accepted = completionAdvisor["accepted"];
    if (typeof accepted === "boolean") advisor.accepted = accepted;
    const confidence = completionAdvisor["confidence"];
    if (typeof confidence === "number" && Number.isFinite(confidence)) advisor.confidence = confidence;
    for (const key of ["source", "proposalRecordId", "fallback_reason"] as const) {
      const value = readString(completionAdvisor[key]);
      if (value) advisor[key] = value;
    }
    if (Object.keys(advisor).length) {
      audit.completionAdvisor = advisor;
    }
  }
  return Object.keys(audit).length ? audit : undefined;
}

function readCompletionReviewConclusion(raw: unknown): ApprovalCompletionEvidenceView["reviewConclusion"] | undefined {
  const record = readRecord(raw);
  if (!record) return undefined;
  const conclusion: NonNullable<ApprovalCompletionEvidenceView["reviewConclusion"]> = {};
  const approvalId = readString(record["approvalId"]);
  const decision = readString(record["decision"]);
  const decidedBy = readString(record["decidedBy"]);
  const gateStatus = readString(record["gateStatus"]);
  const summary = readString(record["summary"]);
  const decidedAt = typeof record["decidedAt"] === "number" ? record["decidedAt"] : undefined;
  if (approvalId) conclusion.approvalId = approvalId;
  if (decision) conclusion.decision = decision;
  if (decidedBy) conclusion.decidedBy = decidedBy;
  if (decidedAt !== undefined) conclusion.decidedAt = decidedAt;
  if (gateStatus) conclusion.gateStatus = gateStatus;
  if (summary) conclusion.summary = summary;
  return Object.keys(conclusion).length ? conclusion : undefined;
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

function readAdvisorEvidenceAdapters(raw: unknown): ApprovalCompletionEvidenceView["advisorEvidenceAdapters"] | undefined {
  const record = readRecord(raw);
  if (!record) return undefined;
  const counts = readRecord(record["counts"]);
  const adapters = Array.isArray(record["adapters"])
    ? record["adapters"]
        .map((item) => readRecord(item))
        .filter((item): item is Record<string, unknown> => Boolean(item))
        .map((item) => ({
          adapterKind: readString(item["adapterKind"]),
          status: readString(item["status"]),
          executorState: readString(item["executorState"]),
          summary: readString(item["summary"]),
        }))
        .map((item) => Object.fromEntries(Object.entries(item).filter(([, value]) => value !== undefined)))
    : [];
  const summary: NonNullable<ApprovalCompletionEvidenceView["advisorEvidenceAdapters"]> = {
    status: readString(record["status"]),
    adapters,
  };
  if (counts) {
    const numericCounts: NonNullable<NonNullable<ApprovalCompletionEvidenceView["advisorEvidenceAdapters"]>["counts"]> = {};
    for (const key of ["ready", "approvalRequired", "blocked", "missingAdapter", "satisfied", "total"] as const) {
      const value = counts[key];
      if (typeof value === "number" && Number.isFinite(value)) {
        numericCounts[key] = value;
      }
    }
    if (Object.keys(numericCounts).length) {
      summary.counts = numericCounts;
    }
  }
  return summary.status || summary.adapters.length || summary.counts ? summary : undefined;
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

function summarizeVerificationRequirements(value: unknown): string[] {
  const record = readRecord(value);
  if (!record) return [];
  const missing = Array.isArray(record["missing"])
    ? record["missing"].map((item) => String(item).trim()).filter(Boolean)
    : [];
  if (!missing.length) return [];
  return [`Missing framework verification: ${missing.join(", ")}`];
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
    if (event.type.startsWith("patch.")) {
      const payload = event.payload as PatchProposedPayload;
      const patchId = readEventText(payload, "patchId");
      if (!patchId) continue;
      const diffText = readEventText(payload, "diffText");
      const changedPaths = normalizeChangedPaths(payload.changedPaths);
      const status = readEventText(payload, "status") ?? event.type.slice("patch.".length);
      cards.set(patchId, {
        patchId,
        taskId: event.taskId ?? "",
        summary: payload.summary ?? "",
        filesChanged: payload.filesChanged ?? changedPaths.length,
        status: status as PatchRecord["status"],
        requestedAt: event.ts,
        updatedAt: event.ts,
        diffText,
        changedPaths,
      });
    }
  }

  for (const [patchId, patch] of Object.entries(patchCacheById)) {
    const current = cards.get(patchId);
    const changedPaths = normalizeChangedPaths(patch.changedPaths);
    cards.set(patchId, {
      patchId,
      taskId: patch.taskId,
      summary: patch.summary,
      filesChanged: patch.filesChanged,
      status: patch.status,
      requestedAt: current?.requestedAt ?? patch.createdAt,
      updatedAt: Math.max(current?.updatedAt ?? patch.updatedAt, patch.updatedAt),
      diffText: patch.diffText,
      changedPaths: changedPaths.length ? changedPaths : current?.changedPaths,
    });
  }

  for (const card of cards.values()) {
    const approval = approvalByPatchId.get(card.patchId);
    if (approval) {
      card.approvalId = approval.approvalId;
      card.approvalStatus = approval.status;
      card.approvalResolvedAt = approval.resolvedAt;
      card.updatedAt = Math.max(card.updatedAt, approval.updatedAt);
      const patchStatus = String(card.status ?? "").toLowerCase();
      const approvalCanOwnStatus = !["applied", "reverted", "failed"].includes(patchStatus);
      if (approval.status === "approved" && approvalCanOwnStatus) {
        card.status = "approved";
      } else if (approval.status === "rejected" && approvalCanOwnStatus) {
        card.status = "rejected";
      }
    }
  }

  return sortByUpdatedAtDesc(Array.from(cards.values()));
}
