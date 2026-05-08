import type { MessageKind, MessageRecord, MessageStatus } from "@shared";

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
  clientMessageId?: string;
  kind?: MessageKind;
  status?: MessageStatus;
  createdSeq?: number;
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
    updatedAt: record.updatedAt ?? record.createdAt,
    clientMessageId: record.clientMessageId,
    kind: record.kind,
    status: record.status,
    createdSeq: record.createdSeq,
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

  // Single pass over current to split into other-session, live-streaming, and pending-local
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

  // Match pending local messages to persisted ones by clientMessageId (primary) or id/content (fallback)
  const matchedPersistedIds = new Set<string>();
  const unmatchedPendingLocalMessages: ChatMessageView[] = [];

  for (const local of pendingLocalMessages) {
    const match = findPersistedMatch(persistedMessages, local, matchedPersistedIds);
    if (match) {
      matchedPersistedIds.add(match.id);
    } else {
      unmatchedPendingLocalMessages.push(local);
    }
  }

  return [...otherSessionMessages, ...persistedMessages, ...unmatchedPendingLocalMessages, ...updatedLiveStreamingMessages].sort(
    (left, right) => sortBySeqAndTime(left, right),
  );
}

function findPersistedMatch(
  persisted: ChatMessageView[],
  local: ChatMessageView,
  alreadyMatched: Set<string>,
): ChatMessageView | null {
  // Primary: match by clientMessageId
  if (local.clientMessageId) {
    const match = persisted.find(
      (p) => !alreadyMatched.has(p.id) && p.clientMessageId === local.clientMessageId,
    );
    if (match) return match;
  }
  // Fallback: match by backend id directly (if local.id is a real backend id)
  const directMatch = persisted.find(
    (p) => !alreadyMatched.has(p.id) && p.id === local.id,
  );
  if (directMatch) return directMatch;
  // Legacy fallback: content + role + sessionId + time window
  return (
    persisted.find(
      (p) =>
        !alreadyMatched.has(p.id) &&
        p.sessionId === local.sessionId &&
        p.role === local.role &&
        p.content.trim() === local.content.trim() &&
        Math.abs(p.createdAt - local.createdAt) < 5 * 60 * 1000,
    ) ?? null
  );
}

/** Sort by createdSeq (if available), then createdAt, then id. */
function sortBySeqAndTime(left: ChatMessageView, right: ChatMessageView): number {
  const leftSeq = left.createdSeq;
  const rightSeq = right.createdSeq;
  if (leftSeq != null && rightSeq != null && leftSeq !== rightSeq) {
    return leftSeq - rightSeq;
  }
  if (leftSeq != null && rightSeq == null) return -1;
  if (leftSeq == null && rightSeq != null) return 1;
  const timeDiff = left.createdAt - right.createdAt;
  if (timeDiff !== 0) return timeDiff;
  return left.id.localeCompare(right.id);
}

function isLocalPendingMessage(message: ChatMessageView) {
  return (
    message.taskId === "pending" ||
    message.id.startsWith("user_") ||
    message.id.startsWith("assistant_pending_") ||
    message.id.startsWith("assistant_failed_")
  );
}

export function appendUserMessage(
  current: ChatMessageView[],
  payload: {
    id: string;
    sessionId: string;
    content: string;
    now: number;
    clientMessageId?: string;
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
      clientMessageId: payload.clientMessageId,
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

/**
 * Replace a local pending message with a backend-confirmed message (by clientMessageId or id).
 * Returns updated array with the local message replaced by the backend one.
 */
export function reconcileBackendMessage(
  current: ChatMessageView[],
  backend: ChatMessageView,
): ChatMessageView[] {
  const matchIndex = (() => {
    // Try clientMessageId match first
    if (backend.clientMessageId) {
      const idx = current.findIndex(
        (m) => m.clientMessageId === backend.clientMessageId && m.sessionId === backend.sessionId,
      );
      if (idx >= 0) return idx;
    }
    // Try direct id match
    const idx = current.findIndex((m) => m.id === backend.id);
    if (idx >= 0) return idx;
    return -1;
  })();

  if (matchIndex >= 0) {
    const local = current[matchIndex];
    // Preserve streaming state if the local message is actively streaming
    // (backend message for assistant streaming placeholder)
    const next = [...current];
    next[matchIndex] = {
      ...backend,
      streaming: local.streaming,
      placeholder: local.placeholder,
    };
    return next;
  }

  // No match found — just append
  return [...current, backend];
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
      status: "streaming",
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
      kind: "failure",
      status: "failed",
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
      kind: "failure",
      status: "failed",
    },
  ];
}

export function updateAssistantMessageByMessageId(
  current: ChatMessageView[],
  messageId: string,
  updater: (msg: ChatMessageView) => ChatMessageView,
): ChatMessageView[] {
  const index = current.findIndex((m) => m.id === messageId);
  if (index >= 0) {
    const next = [...current];
    next[index] = updater(next[index]);
    return next;
  }
  return current;
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
    .sort((left, right) => sortBySeqAndTime(left, right));
}
