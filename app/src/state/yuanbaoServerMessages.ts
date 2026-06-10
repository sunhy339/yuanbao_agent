import type { AgentEventEnvelope, YuanbaoServerMessage } from "@shared";
import {
  appendAssistantToolResultMessage,
  appendOrUpdateAssistantMessageDelta,
  appendOrUpdateAssistantThinkingMessage,
  appendOrUpdateAssistantToolInputDelta,
  appendOrUpdateAssistantToolOutputDelta,
  appendOrUpdateAssistantToolStartMessage,
  appendOrUpdatePermissionRequestMessage,
  appendSpecialEventMessage,
  closeAssistantThinkingForToolBoundary,
  completeAssistantToolUseMessage,
  completeChatCompatMessage,
  failAssistantMessage,
  formatAssistantFailureContent,
  removeAssistantThinkingMessage,
  resolvePermissionRequestMessage,
  type ChatMessageView,
} from "./chatMessages";

export type YuanbaoServerMessageApplyResult = {
  handled: boolean;
  messages: ChatMessageView[];
};

type ApplyOptions = {
  stableThinkingEventId?: boolean;
};

const SILENT_FLAT_MESSAGE_TYPES = new Set([
  "connected",
  "pong",
  "session_title_updated",
  "status",
]);

const LEGACY_SUPPRESSED_FLAT_MESSAGE_TYPES = new Set([
  "api_retry",
  "computer_use_permission_request",
  "content_delta",
  "content_start",
  "error",
  "message_complete",
  "permission_request",
  "system_notification",
  "thinking",
  "tool_result",
  "tool_use_complete",
]);

const INTERNAL_APPROVAL_KINDS = new Set(["completion_review", "advisor_tool"]);
const PANEL_ONLY_SYSTEM_NOTIFICATION_SUBTYPES = new Set(["task_progress", "task_started"]);

function normalizedKind(value: unknown): string {
  return typeof value === "string" ? value.trim().toLowerCase().replace(/\s+/g, "_") : "";
}

function isInternalApprovalKind(value: unknown): boolean {
  return INTERNAL_APPROVAL_KINDS.has(normalizedKind(value));
}

function internalApprovalKindFromPayload(payload: unknown): string {
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) return "";
  const record = payload as Record<string, unknown>;
  for (const key of ["toolName", "kind", "approvalKind", "toolKind", "name"]) {
    const candidate = normalizedKind(record[key]);
    if (INTERNAL_APPROVAL_KINDS.has(candidate)) return candidate;
  }
  for (const key of ["request", "input", "metadata", "payload"]) {
    const nested = internalApprovalKindFromPayload(record[key]);
    if (nested) return nested;
  }
  return "";
}

function looksLikeInternalDisplayText(value: string): boolean {
  const text = value.trim();
  if (!text) return true;
  const lines = text.split(/\r?\n/).filter((line) => line.trim());
  if (
    lines.length >= 3 &&
    /(^|\n)\s*(_chatCompat|activeStep|currentStep|completedSteps|fingerprint|provider|rawJson|toolProgress|tool_progress|trace|uiReplayScope|visibility|tool_results|workspaceRoot|sessionId|taskId|eventId|payload|metadata)\s*[:=]/.test(text)
  ) {
    return true;
  }
  if (!/^[{\[]/.test(text)) return false;
  try {
    const parsed = JSON.parse(text);
    if (!parsed || typeof parsed !== "object") return false;
    const keys = Object.keys(parsed as Record<string, unknown>);
    return keys.some((key) => [
      "_chatCompat",
      "context",
      "currentTaskId",
      "eventId",
      "frames",
      "messages",
      "metadata",
      "options",
      "payload",
      "progress",
      "provider",
      "providerRequest",
      "providerResponse",
      "questions",
      "rawJson",
      "requestId",
      "sessionId",
      "taskId",
      "taskStatus",
      "toolCallId",
      "toolProgress",
      "tool_progress",
      "tool_results",
      "trace",
      "uiReplayScope",
      "visibility",
      "workspaceRoot",
      "yuanbao",
    ].includes(key));
  } catch {
    return true;
  }
}

export function yuanbaoServerMessageFromEvent(event: AgentEventEnvelope): YuanbaoServerMessage | null {
  return event.yuanbao ?? null;
}

export function yuanbaoServerMessageProducesChat(message: YuanbaoServerMessage): boolean {
  if (message.type === "task_update") {
    return false;
  }
  return !SILENT_FLAT_MESSAGE_TYPES.has(message.type);
}

export function shouldSuppressLegacyRenderingForYuanbaoMessage(
  event: AgentEventEnvelope,
  message: YuanbaoServerMessage,
): boolean {
  if (LEGACY_SUPPRESSED_FLAT_MESSAGE_TYPES.has(message.type)) {
    return true;
  }
  if (message.type === "team_update" || message.type === "team_created" || message.type === "team_deleted") {
    return false;
  }
  return event.type === "message.delta" ||
    event.type === "message.completed" ||
    event.type === "message.failed" ||
    event.type === "assistant.message.completed" ||
    event.type === "assistant.token";
}

export function shouldFlushPendingTokensForYuanbaoMessage(message: YuanbaoServerMessage): boolean {
  return message.type === "message_complete" ||
    message.type === "error" ||
    message.type === "permission_request" ||
    message.type === "computer_use_permission_request";
}

export function applyYuanbaoServerMessageToChat(
  current: ChatMessageView[],
  event: AgentEventEnvelope,
  options: ApplyOptions = {},
): YuanbaoServerMessageApplyResult {
  const message = yuanbaoServerMessageFromEvent(event);
  if (!message || !yuanbaoServerMessageProducesChat(message)) {
    return { handled: false, messages: current };
  }

  const payload = recordValue(event.payload);
  const flatPayload = recordValue(message);
  const flatString = (key: string): string | undefined => readString(flatPayload[key]) ?? readString(payload[key]);
  const flatNumber = (key: string): number | undefined => readNumber(flatPayload[key]) ?? readNumber(payload[key]);
  const flatStringArray = (key: string): string[] | undefined => readStringArray(flatPayload[key]) ?? readStringArray(payload[key]);
  const flatPreviewRows = (key: string): Array<{ label: string; value: string }> | undefined =>
    readPreviewRows(flatPayload[key]) ?? readPreviewRows(payload[key]);
  const flatToolPresentation = () => ({
    target: flatString("target"),
    inputSummary: flatString("inputSummary"),
    displayTitle: flatString("displayTitle"),
    displaySummary: flatString("displaySummary"),
    displayTarget: flatString("displayTarget"),
    displayKind: flatString("displayKind"),
    toolGroupId: flatString("toolGroupId"),
    toolIndex: flatNumber("toolIndex"),
    toolTotal: flatNumber("toolTotal"),
    toolOperationId: flatString("toolOperationId"),
    toolOperationLabel: flatString("toolOperationLabel"),
    toolCategory: flatString("toolCategory"),
    toolPhaseId: flatString("toolPhaseId"),
    toolPhaseLabel: flatString("toolPhaseLabel"),
    toolSemanticParentId: flatString("toolSemanticParentId"),
    toolSemanticParentLabel: flatString("toolSemanticParentLabel"),
  });
  const taskId = event.taskId;
  const now = event.ts;
  const closeThinking = (messages: ChatMessageView[]) =>
    closeAssistantThinkingForToolBoundary(messages, {
      sessionId: event.sessionId,
      taskId,
      now,
    });

  switch (message.type) {
    case "content_start": {
      if (message.blockType !== "tool_use" || !message.toolUseId) {
        return { handled: true, messages: current };
      }
      return {
        handled: true,
        messages: appendOrUpdateAssistantToolStartMessage(closeThinking(current), {
          toolUseId: message.toolUseId,
          toolName: message.toolName,
          parentToolUseId: message.parentToolUseId ?? flatString("parentToolUseId"),
          ...flatToolPresentation(),
          sessionId: event.sessionId,
          taskId,
          now,
        }),
      };
    }

    case "content_delta": {
      let next = current;
      if (typeof message.text === "string" && message.text && !looksLikeInternalDisplayText(message.text)) {
        next = appendOrUpdateAssistantMessageDelta(next, {
          messageId: flatString("messageId") || `assistant_${taskId}`,
          contentBlockId: flatString("contentBlockId"),
          blockIndex: flatNumber("blockIndex"),
          sessionId: event.sessionId,
          taskId,
          delta: message.text,
          now,
        });
      }
      if (typeof message.toolInput === "string" && message.toolInput) {
        next = appendOrUpdateAssistantToolInputDelta(closeThinking(next), {
          toolUseId: flatString("toolUseId") || flatString("toolCallId") || `pending_${taskId}`,
          toolName: flatString("toolName"),
          parentToolUseId: flatString("parentToolUseId"),
          ...flatToolPresentation(),
          sessionId: event.sessionId,
          taskId,
          delta: message.toolInput,
          now,
        });
      }
      if (typeof message.toolOutput === "string" && message.toolOutput && !looksLikeInternalDisplayText(message.toolOutput)) {
        next = appendOrUpdateAssistantToolOutputDelta(closeThinking(next), {
          toolUseId: flatString("toolUseId") || flatString("toolCallId") || `pending_${taskId}`,
          toolName: flatString("toolName"),
          parentToolUseId: flatString("parentToolUseId"),
          ...flatToolPresentation(),
          sessionId: event.sessionId,
          taskId,
          delta: message.toolOutput,
          stream: flatString("outputStream"),
          now,
        });
      }
      return { handled: true, messages: next };
    }

    case "thinking":
      return {
        handled: true,
        messages: appendOrUpdateAssistantThinkingMessage(current, {
          sessionId: event.sessionId,
          taskId,
          eventId: options.stableThinkingEventId ? event.eventId : undefined,
          state: "thinking",
          text: message.text,
          source: flatString("source"),
          now,
        }),
      };

    case "tool_use_complete":
      return {
        handled: true,
        messages: completeAssistantToolUseMessage(closeThinking(current), {
          toolUseId: message.toolUseId,
          toolName: message.toolName,
          input: message.input,
          parentToolUseId: message.parentToolUseId ?? flatString("parentToolUseId"),
          ...flatToolPresentation(),
          sessionId: event.sessionId,
          taskId,
          now,
        }),
      };

    case "tool_result":
      return {
        handled: true,
        messages: appendAssistantToolResultMessage(closeThinking(current), {
          toolUseId: message.toolUseId,
          toolName: flatString("toolName"),
          content: message.content,
          isError: message.isError,
          parentToolUseId: message.parentToolUseId ?? flatString("parentToolUseId"),
          resultSummary: flatString("resultSummary"),
          resultPreview: flatPreviewRows("resultPreview"),
          filesChanged: flatNumber("filesChanged"),
          changedPaths: flatStringArray("changedPaths"),
          diffText: flatString("diffText"),
          durationMs: flatNumber("durationMs"),
          ...flatToolPresentation(),
          sessionId: event.sessionId,
          taskId,
          now,
        }),
      };

    case "permission_request": {
      if (isInternalApprovalKind(message.toolName) || internalApprovalKindFromPayload(flatPayload) || internalApprovalKindFromPayload(payload)) {
        return { handled: true, messages: current };
      }
      const permissionPayload = {
        requestId: message.requestId,
        toolName: message.toolName,
        toolUseId: message.toolUseId ?? flatString("toolUseId") ?? flatString("toolCallId"),
        input: message.input,
        description: message.description,
        preview: flatPreviewRows("preview"),
        previewSections: Array.isArray(flatPayload.previewSections) ? flatPayload.previewSections : Array.isArray(payload.previewSections) ? payload.previewSections : undefined,
        filesChanged: flatNumber("filesChanged"),
        changedPaths: flatStringArray("changedPaths"),
        diffText: flatString("diffText"),
        ...flatToolPresentation(),
        sessionId: event.sessionId,
        taskId,
        now,
      };
      if (flatPayload.resolved === true || payload.resolved === true) {
        return {
          handled: true,
          messages: resolvePermissionRequestMessage(current, {
            ...permissionPayload,
            decision: readString(flatPayload.decision) || readString(payload.decision) || "approved",
            createIfMissing: true,
          }),
        };
      }
      return {
        handled: true,
        messages: appendOrUpdatePermissionRequestMessage(
          removeAssistantThinkingMessage(current, {
            sessionId: event.sessionId,
            taskId,
            now,
          }),
          permissionPayload,
        ),
      };
    }

    case "computer_use_permission_request":
      return {
        handled: true,
        messages: appendSpecialEventMessage(
          removeAssistantThinkingMessage(current, {
            sessionId: event.sessionId,
            taskId,
            now,
          }),
          {
            kind: "computer_use_permission",
            sessionId: event.sessionId,
            taskId,
            title: "Computer use permission",
            summary: readString(message.request.reason) || readString(message.request.summary),
            status: "waiting_approval",
            eventId: event.eventId,
            metadata: {
              kind: "computer_use_permission",
              requestId: message.requestId,
              approvalId: message.requestId,
              request: message.request,
              ...recordValue(message.request),
            },
            now,
          },
        ),
      };

    case "message_complete":
      return {
        handled: true,
        messages: removeAssistantThinkingMessage(
          completeChatCompatMessage(current, {
            messageId: readString(payload.messageId),
            sessionId: event.sessionId,
            taskId,
            content: readString(payload.content),
            now,
          }),
          {
            sessionId: event.sessionId,
            taskId,
            now,
          },
        ),
      };

    case "api_retry":
      return {
        handled: true,
        messages: appendSpecialEventMessage(current, {
          kind: "api_retry",
          sessionId: event.sessionId,
          taskId,
          title: "API retry",
          summary: message.errorMessage || message.errorType || `Attempt ${message.attempt}/${message.maxRetries}`,
          status: "running",
          eventId: event.eventId,
          metadata: {
            kind: "api_retry",
            attempt: message.attempt,
            maxRetries: message.maxRetries,
            retryDelayMs: message.retryDelayMs,
            errorStatus: message.errorStatus,
            errorType: message.errorType,
            errorMessage: message.errorMessage,
          },
          now,
        }),
      };

    case "error":
      return {
        handled: true,
        messages: failAssistantMessage(current, {
          messageId: readString(payload.messageId),
          sessionId: event.sessionId,
          taskId,
          content: formatAssistantFailureContent(message.message),
          now,
        }),
      };

    case "system_notification":
      if (PANEL_ONLY_SYSTEM_NOTIFICATION_SUBTYPES.has(message.subtype)) {
        return { handled: true, messages: current };
      }
      return {
        handled: true,
        messages: appendSpecialEventMessage(current, {
          kind: systemNotificationKind(message.subtype),
          sessionId: event.sessionId,
          taskId,
          title: systemNotificationTitle(message.subtype),
          summary: message.message,
          content: message.message,
          status: readString(payload.status),
          eventId: event.eventId,
          metadata: {
            kind: systemNotificationKind(message.subtype),
            subtype: message.subtype,
            data: message.data,
          },
          now,
        }),
      };

    case "team_created":
      return {
        handled: true,
        messages: appendTeamEvent(current, event, message.teamName, [], "created"),
      };

    case "team_deleted":
      return {
        handled: true,
        messages: appendTeamEvent(current, event, message.teamName, [], "deleted"),
      };

    case "team_update":
      return {
        handled: true,
        messages: appendTeamEvent(current, event, message.teamName, message.members, "updated"),
      };

    case "task_update":
      return { handled: false, messages: current };

    default:
      return { handled: false, messages: current };
  }
}

function appendTeamEvent(
  current: ChatMessageView[],
  event: AgentEventEnvelope,
  teamName: string,
  members: Extract<YuanbaoServerMessage, { type: "team_update" }>["members"],
  action: "created" | "updated" | "deleted",
): ChatMessageView[] {
  const running = members.some((member) => member.status === "running");
  const failed = members.some((member) => member.status === "error");
  const completed = members.length > 0 && members.every((member) => member.status === "completed");
  const status = failed ? "failed" : running ? "running" : completed || action === "deleted" ? "completed" : "running";
  const visibleTeamName = looksLikeInternalDisplayText(teamName) || /^(?:c?task|team|session|worker|agent)[_-][a-z0-9_-]{4,}$/i.test(teamName)
    ? ""
    : teamName;
  const title = action === "created"
    ? (visibleTeamName ? `Team created: ${visibleTeamName}` : "Team created")
    : action === "deleted"
      ? (visibleTeamName ? `Team deleted: ${visibleTeamName}` : "Team deleted")
      : (visibleTeamName ? `Team update: ${visibleTeamName}` : "Team update");
  const summary = members.length
    ? `${members.length} member${members.length === 1 ? "" : "s"}`
    : action;
  return appendSpecialEventMessage(current, {
    kind: "agent_task_group",
    sessionId: event.sessionId,
    taskId: event.taskId,
    title,
    summary,
    content: summary,
    status,
    eventId: event.eventId,
    metadata: {
      kind: "agent_task_group",
      teamName: visibleTeamName,
      action,
      members,
    },
    now: event.ts,
  });
}

function systemNotificationKind(subtype: string): string {
  if (subtype === "compact_summary") return "compact_summary";
  if (subtype === "goal_event") return "goal_event";
  if (subtype === "memory_saved") return "memory_event";
  return "system";
}

function systemNotificationTitle(subtype: string): string {
  if (subtype === "compact_summary") return "Context compacted";
  if (subtype === "memory_saved") return "Memory updated";
  if (subtype === "task_progress" || subtype === "task_started") return "Task update";
  return subtype.replace(/_/g, " ");
}

function recordValue(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function readString(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value.trim() : undefined;
}

function readNumber(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

function readStringArray(value: unknown): string[] | undefined {
  if (!Array.isArray(value)) return undefined;
  const items = value.filter((item): item is string => typeof item === "string" && item.trim().length > 0);
  return items.length ? items : undefined;
}

function readPreviewRows(value: unknown): Array<{ label: string; value: string }> | undefined {
  if (!Array.isArray(value)) return undefined;
  const rows = value
    .map((item) => {
      const record = recordValue(item);
      const label = readString(record.label);
      const rowValue = readString(record.value);
      return label && rowValue ? { label, value: rowValue } : null;
    })
    .filter((item): item is { label: string; value: string } => item !== null);
  return rows.length ? rows : undefined;
}
