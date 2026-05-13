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
      const patchSummary = readRequestText(request, "summary", readRequestText(request, "patchSummary", "patch approval request"));
      const command = readRequestText(request, "command", payload.kind === "apply_patch" ? "apply_patch" : "command");
      cards.set(payload.approvalId, {
        approvalId: payload.approvalId,
        taskId: payload.taskId,
        kind: payload.kind,
        patchId,
        patchSummary,
        filesChanged,
        command,
        cwd: readRequestText(request, "cwd", readRequestText(request, "workspaceRoot", ".")),
        shell: readRequestText(request, "shell", "system default"),
        timeoutMs: readRequestNumber(request, "timeoutMs", 0),
        risk: readRequestText(request, "risk", payload.kind === "apply_patch" ? "writes files" : "executes command"),
        requestJson: stringifyRequestJson(request),
        requestSummary:
          payload.kind === "apply_patch"
            ? `${patchSummary}${filesChanged !== undefined ? ` | ${filesChanged} file(s)` : ""}${
                changedFiles.length > 0 ? ` | ${changedFiles.slice(0, 3).join(", ")}` : ""
              }`
            : `${command} | cwd ${readRequestText(request, "cwd", readRequestText(request, "workspaceRoot", "."))}`,
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
