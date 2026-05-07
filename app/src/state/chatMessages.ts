import type { MessageRecord } from "@shared";

export interface ChatMessageView {
  id: string;
  sessionId: string;
  taskId: string;
  role: "user" | "assistant";
  content: string;
  createdAt: number;
  updatedAt: number;
  streaming?: boolean;
  placeholder?: boolean;
}

export function messageRecordToChatMessage(record: MessageRecord): ChatMessageView | null {
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
    updatedAt: record.createdAt,
  };
}

export function replaceSessionMessages(
  current: ChatMessageView[],
  sessionId: string,
  records: MessageRecord[],
): ChatMessageView[] {
  const persistedMessages = records
    .map(messageRecordToChatMessage)
    .filter((message): message is ChatMessageView => message !== null);

  // Single pass over current to split into other-session and live-streaming
  const otherSessionMessages: ChatMessageView[] = [];
  const liveStreamingMessages: ChatMessageView[] = [];
  const pendingLocalMessages: ChatMessageView[] = [];
  for (const message of current) {
    if (message.sessionId === sessionId && message.streaming) {
      liveStreamingMessages.push(message);
    } else if (message.sessionId === sessionId && isLocalPendingMessage(message)) {
      pendingLocalMessages.push(message);
    } else if (message.sessionId !== sessionId) {
      otherSessionMessages.push(message);
    }
  }

  const maxPersistedTime = persistedMessages.reduce((max, msg) => Math.max(max, msg.createdAt), 0);
  const updatedLiveStreamingMessages = liveStreamingMessages.map((msg, index) => {
    const createdAt = Math.max(msg.createdAt, maxPersistedTime + 1 + index);
    return {
      ...msg,
      createdAt,
      updatedAt: Math.max(msg.updatedAt, createdAt),
    };
  });
  const unmatchedPendingLocalMessages = pendingLocalMessages.filter(
    (message) => !persistedMessages.some((persisted) => isPersistedMatchForLocalMessage(persisted, message)),
  );

  return [...otherSessionMessages, ...persistedMessages, ...unmatchedPendingLocalMessages, ...updatedLiveStreamingMessages].sort(
    (left, right) => left.createdAt - right.createdAt || left.id.localeCompare(right.id),
  );
}

function isLocalPendingMessage(message: ChatMessageView) {
  return (
    message.taskId === "pending" ||
    message.id.startsWith("user_") ||
    message.id.startsWith("assistant_pending_") ||
    message.id.startsWith("assistant_failed_")
  );
}

function isPersistedMatchForLocalMessage(persisted: ChatMessageView, local: ChatMessageView) {
  return (
    persisted.sessionId === local.sessionId &&
    persisted.role === local.role &&
    persisted.content.trim() === local.content.trim() &&
    Math.abs(persisted.createdAt - local.createdAt) < 5 * 60 * 1000
  );
}

export function appendUserMessage(
  current: ChatMessageView[],
  payload: {
    id: string;
    sessionId: string;
    content: string;
    now: number;
  },
): ChatMessageView[] {
  return [
    ...current,
    {
      id: payload.id,
      sessionId: payload.sessionId,
      taskId: "pending",
      role: "user",
      content: payload.content,
      createdAt: payload.now,
      updatedAt: payload.now,
    },
  ];
}

export function updatePendingMessageTask(
  current: ChatMessageView[],
  messageId: string,
  taskId: string,
): ChatMessageView[] {
  return current.map((message) =>
    message.id === messageId
      ? {
          ...message,
          taskId,
        }
      : message,
  );
}

export function appendAssistantPlaceholder(
  current: ChatMessageView[],
  payload: {
    id: string;
    sessionId: string;
    content: string;
    now: number;
  },
): ChatMessageView[] {
  return [
    ...current,
    {
      id: payload.id,
      sessionId: payload.sessionId,
      taskId: "pending",
      role: "assistant",
      content: payload.content,
      createdAt: payload.now,
      updatedAt: payload.now,
      streaming: true,
      placeholder: true,
    },
  ];
}

export function removeChatMessage(
  current: ChatMessageView[],
  messageId: string,
): ChatMessageView[] {
  return current.filter((message) => message.id !== messageId);
}

export function failAssistantMessage(
  current: ChatMessageView[],
  payload: {
    messageId?: string | null;
    sessionId: string;
    taskId?: string;
    content: string;
    now: number;
  },
): ChatMessageView[] {
  const next = [...current];
  const targetIndex = (() => {
    if (payload.messageId) {
      const exactIndex = next.findIndex((message) => message.id === payload.messageId);
      if (exactIndex >= 0) {
        return exactIndex;
      }
    }
    for (let index = next.length - 1; index >= 0; index -= 1) {
      const message = next[index];
      if (
        message.role === "assistant" &&
        message.sessionId === payload.sessionId &&
        (!payload.taskId || message.taskId === payload.taskId || (message.streaming && message.taskId === "pending"))
      ) {
        return index;
      }
    }
    return -1;
  })();

  if (targetIndex >= 0) {
    const message = next[targetIndex];
    next[targetIndex] = {
      ...message,
      taskId: payload.taskId ?? message.taskId,
      content: payload.content,
      updatedAt: payload.now,
      streaming: false,
      placeholder: false,
    };
    return next;
  }

  return [
    ...next,
    {
      id: `assistant_failed_${payload.now}`,
      sessionId: payload.sessionId,
      taskId: payload.taskId ?? "pending",
      role: "assistant",
      content: payload.content,
      createdAt: payload.now,
      updatedAt: payload.now,
      streaming: false,
      placeholder: false,
    },
  ];
}

export function stopStreamingMessages(
  current: ChatMessageView[],
  sessionId?: string | null,
): ChatMessageView[] {
  return current
    .map((message) =>
      (!sessionId || message.sessionId === sessionId) && message.streaming
        ? {
            ...message,
            streaming: false,
            placeholder: false,
          }
        : message,
    )
    .filter((message) => !(message.placeholder && !message.content.trim()));
}

export function isOperationalAssistantDelta(delta: string): boolean {
  const normalized = delta.trim();
  if (!normalized) {
    return true;
  }

  return (
    normalized === "Building context and preparing the first tool calls..." ||
    normalized === "Completed the minimal tool loop and preparing a summary..." ||
    normalized.startsWith("Running tool: ") ||
    normalized.startsWith("Running post-task ") ||
    normalized.startsWith("Running post-task validation command: ") ||
    normalized.startsWith("Approval accepted. Running the command now") ||
    normalized.startsWith("Approval accepted. Applying the patch now")
  );
}

export function getVisibleChatMessages(
  messages: ChatMessageView[],
  sessionId: string | null | undefined,
): ChatMessageView[] {
  if (!sessionId) {
    return [];
  }

  return messages
    .filter((message) => message.sessionId === sessionId)
    .sort((left, right) => left.createdAt - right.createdAt || left.id.localeCompare(right.id));
}
