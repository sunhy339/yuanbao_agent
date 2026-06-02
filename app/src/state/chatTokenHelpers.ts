import type {
  AgentEventEnvelope,
  AssistantTokenPayload,
} from "@shared";
import type { ChatMessageView } from "./chatMessages";
import { getPayloadValue, summarizeValue } from "./traceReaders";
import {
  appendAssistantContentDelta,
  appendOrUpdateAssistantThinkingMessage,
  failAssistantMessage,
  formatAssistantFailureContent,
  isOperationalAssistantDelta,
  resolveAssistantCompletionContent,
  summarizeOperationalAssistantDelta,
} from "./chatMessages";

export function appendAssistantToken(current: ChatMessageView[], event: AgentEventEnvelope): ChatMessageView[] {
  const payload = event.payload as AssistantTokenPayload;
  const delta = payload.delta ?? "";
  if (isOperationalAssistantDelta(delta)) {
    const progressText = summarizeOperationalAssistantDelta(delta)?.trim();
    if (!progressText) {
      return current;
    }
    return appendOrUpdateAssistantThinkingMessage(current, {
      sessionId: event.sessionId,
      taskId: event.taskId,
      state: "thinking",
      text: progressText,
      now: event.ts,
    });
  }

  const displayDelta = delta;
  if (!displayDelta) {
    return current;
  }

  const next = [...current];
  const lastAssistantIndex = (() => {
    for (let index = next.length - 1; index >= 0; index -= 1) {
      const item = next[index];
      const metadataKind = typeof item.metadata?.kind === "string" ? item.metadata.kind : "";
      if (
        item.role === "assistant" &&
        item.streaming &&
        !metadataKind &&
        (item.taskId === event.taskId || (item.taskId === "pending" && item.sessionId === event.sessionId))
      ) {
        return index;
      }
    }
    return -1;
  })();

  if (lastAssistantIndex >= 0) {
    const currentMessage = next[lastAssistantIndex];
    next[lastAssistantIndex] = {
      ...currentMessage,
      taskId: event.taskId,
      content: currentMessage.placeholder
        ? displayDelta.trim()
        : appendAssistantContentDelta(currentMessage.content, displayDelta),
      updatedAt: event.ts,
      placeholder: false,
    };
    return next;
  }

  return [
    ...next,
    {
      id: `assistant_${event.eventId}`,
      sessionId: event.sessionId,
      taskId: event.taskId,
      role: "assistant",
      content: displayDelta.trim(),
      createdAt: event.ts,
      updatedAt: event.ts,
      streaming: true,
    },
  ];
}

export function completeAssistantMessage(current: ChatMessageView[], event: AgentEventEnvelope): ChatMessageView[] {
  const completedContent = summarizeValue(
    getPayloadValue(event.payload, ["content", "message", "text"]),
    "",
    10_000,
  );
  const payloadRecord = event.payload && typeof event.payload === "object" ? (event.payload as Record<string, unknown>) : {};
  if (payloadRecord.supplemental === true && completedContent) {
    return [
      ...current,
      {
        id: `assistant_${event.eventId}`,
        sessionId: event.sessionId,
        taskId: event.taskId,
        role: "assistant",
        content: completedContent,
        createdAt: event.ts,
        updatedAt: event.ts,
        streaming: false,
      },
    ];
  }
  const next = [...current];
  const lastAssistantIndex = (() => {
    for (let index = next.length - 1; index >= 0; index -= 1) {
      const item = next[index];
      const metadataKind = typeof item.metadata?.kind === "string" ? item.metadata.kind : "";
      if (
        item.role === "assistant" &&
        !metadataKind &&
        (item.taskId === event.taskId || (item.streaming && item.taskId === "pending" && item.sessionId === event.sessionId))
      ) {
        return index;
      }
    }
    return -1;
  })();

  if (lastAssistantIndex >= 0) {
    const currentMessage = next[lastAssistantIndex];
    // Prefer streaming content when it was built from token deltas — avoids
    // replacing a rich multi-step answer with a shorter/final-summary that
    // may overlap or differ. Fall back to completedContent when the streaming
    // message is still a placeholder or very short.
    const streamingContent = currentMessage.content || "";
    const isPlaceholder = currentMessage.placeholder === true || streamingContent === "\u601d\u8003\u4e2d..." || streamingContent.length < 5;
    next[lastAssistantIndex] = {
      ...currentMessage,
      taskId: event.taskId,
      content: resolveAssistantCompletionContent(streamingContent, completedContent, isPlaceholder),
      updatedAt: event.ts,
      streaming: false,
      placeholder: false,
    };
    return next;
  }

  if (!completedContent) {
    return current;
  }

  return [
    ...next,
    {
      id: `assistant_${event.eventId}`,
      sessionId: event.sessionId,
      taskId: event.taskId,
      role: "assistant",
      content: completedContent,
      createdAt: event.ts,
      updatedAt: event.ts,
      streaming: false,
    },
  ];
}

export function failAssistantMessageForEvent(current: ChatMessageView[], event: AgentEventEnvelope): ChatMessageView[] {
  const content = formatAssistantFailureContent(
    summarizeValue(
      getPayloadValue(event.payload, ["detail", "resultSummary", "summary", "error", "message"]),
      "",
      10_000,
    ),
  );
  return failAssistantMessage(current, {
    sessionId: event.sessionId,
    taskId: event.taskId,
    content,
    now: event.ts,
  });
}

/** Convert a MessageRecord from events/RPC into a ChatMessageView. */
export function messageRecordToChatMessageLocal(record: import("@shared").MessageRecord): ChatMessageView | null {
  if (record.role !== "user" && record.role !== "assistant") {
    return null;
  }
  return {
    id: record.id,
    sessionId: record.sessionId,
    taskId: record.taskId ?? "persisted",
    role: record.role,
    content: record.content,
    createdAt: record.createdAt,
    updatedAt: record.updatedAt ?? record.createdAt,
    clientMessageId: record.clientMessageId,
    kind: record.kind,
    status: record.status,
    createdSeq: record.createdSeq,
    metadata: record.metadata,
  };
}
