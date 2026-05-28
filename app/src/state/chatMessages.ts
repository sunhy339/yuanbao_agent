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
  toolName?: string;
  metadata?: Record<string, unknown>;
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
  const ephemeralBlockMessages: ChatMessageView[] = [];
  for (const message of current) {
    if (message.sessionId === sessionId && message.streaming) {
      liveStreamingMessages.push(message);
    } else if (message.sessionId === sessionId && isLocalPendingMessage(message)) {
      pendingLocalMessages.push(message);
    } else if (message.sessionId === sessionId && isEphemeralChatBlockMessage(message)) {
      ephemeralBlockMessages.push(message);
    } else if (message.sessionId !== sessionId) {
      otherSessionMessages.push(message);
    }
  }

  const persistedIds = new Set(persistedMessages.map((message) => message.id));
  const persistedClientMessageIds = new Set(
    persistedMessages.map((message) => message.clientMessageId).filter((id): id is string => Boolean(id)),
  );
  const unmatchedLiveStreamingMessages = liveStreamingMessages.filter((message) => {
    if (persistedIds.has(message.id)) {
      return false;
    }
    return !(message.clientMessageId && persistedClientMessageIds.has(message.clientMessageId));
  });

  const maxPersistedTime = persistedMessages.reduce((max, msg) => Math.max(max, msg.createdAt), 0);
  const updatedLiveStreamingMessages = unmatchedLiveStreamingMessages.map((msg, index) => {
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

  return [...otherSessionMessages, ...persistedMessages, ...ephemeralBlockMessages, ...unmatchedPendingLocalMessages, ...updatedLiveStreamingMessages].sort(
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

function isEphemeralChatBlockMessage(message: ChatMessageView) {
  return (
    message.metadata?.kind === "tool_use" ||
    message.metadata?.kind === "tool_result" ||
    message.metadata?.kind === "tool_activity" ||
    message.metadata?.kind === "assistant_progress" ||
    message.metadata?.kind === "assistant_thinking" ||
    message.metadata?.kind === "permission_request"
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
    // Assistant messages are created server-side, so they often do not carry a
    // clientMessageId. Attach the backend id to the local streaming placeholder
    // for the same session/task instead of appending a duplicate empty shell.
    if (backend.role === "assistant") {
      const assistantIdx = findAttachableAssistantMessageIndex(current, {
        sessionId: backend.sessionId,
        taskId: backend.taskId,
      });
      if (assistantIdx >= 0) return assistantIdx;
    }
    return -1;
  })();

  if (matchIndex >= 0) {
    const local = current[matchIndex];
    const localContent = local.content ?? "";
    const backendContent = backend.content ?? "";
    const shouldPreserveLocalContent =
      backend.role === "assistant" &&
      local.streaming === true &&
      Boolean(localContent.trim()) &&
      !backendContent.trim();
    const next = [...current];
    next[matchIndex] = {
      ...backend,
      content: shouldPreserveLocalContent ? localContent : backendContent,
      streaming: backend.streaming ?? local.streaming,
      placeholder: shouldPreserveLocalContent ? local.placeholder : (backend.placeholder ?? local.placeholder),
      status: backend.status ?? local.status,
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
    appendOnly?: boolean;
  },
): ChatMessageView[] {
  const next = [...current];
  const targetIndex = (() => {
    if (payload.appendOnly) {
      return -1;
    }
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
  options: {
    sessionId?: string | null;
    taskId?: string | null;
  } = {},
): ChatMessageView[] {
  const exactIndex = current.findIndex((m) => m.id === messageId);
  const index =
    exactIndex >= 0
      ? exactIndex
      : findAttachableAssistantMessageIndex(current, {
          sessionId: options.sessionId ?? undefined,
          taskId: options.taskId ?? undefined,
        });
  if (index >= 0) {
    const next = [...current];
    next[index] = updater({
      ...next[index],
      id: messageId,
      taskId: options.taskId ?? next[index].taskId,
    });
    return next;
  }
  return current;
}

export function appendOrUpdateAssistantMessageDelta(
  current: ChatMessageView[],
  payload: {
    messageId: string;
    sessionId: string;
    taskId?: string | null;
    delta: string;
    now: number;
  },
): ChatMessageView[] {
  let updatedExisting = false;
  const updated = updateAssistantMessageByMessageId(
    current,
    payload.messageId,
    (msg) => {
      updatedExisting = true;
      return {
        ...msg,
        taskId: payload.taskId ?? msg.taskId,
        content: msg.placeholder
          ? payload.delta.trimStart()
          : appendAssistantContentDelta(msg.content, payload.delta),
        updatedAt: payload.now,
        streaming: true,
        placeholder: false,
        status: "streaming",
      };
    },
    { sessionId: payload.sessionId, taskId: payload.taskId },
  );

  if (updatedExisting) {
    return updated;
  }

  return [
    ...current,
    {
      id: payload.messageId,
      sessionId: payload.sessionId,
      taskId: payload.taskId ?? "pending",
      role: "assistant",
      content: payload.delta.trimStart(),
      createdAt: payload.now,
      updatedAt: payload.now,
      streaming: true,
      placeholder: false,
      status: "streaming",
    },
  ];
}

export function appendOrUpdateAssistantToolInputDelta(
  current: ChatMessageView[],
  payload: {
    toolUseId: string;
    toolName?: string | null;
    sessionId: string;
    taskId?: string | null;
    delta: string;
    now: number;
  },
): ChatMessageView[] {
  const messageId = `tool_use:${payload.toolUseId}`;
  const next = [...current];
  const index = next.findIndex((message) => message.id === messageId);
  if (index >= 0) {
    const message = next[index];
    next[index] = {
      ...message,
      taskId: payload.taskId ?? message.taskId,
      content: appendAssistantContentDelta(message.content, payload.delta),
      updatedAt: payload.now,
      streaming: true,
      placeholder: false,
      status: "streaming",
      toolName: payload.toolName ?? message.toolName,
      metadata: {
        ...(message.metadata ?? {}),
        kind: "tool_use",
        toolUseId: payload.toolUseId,
      },
    };
    return next;
  }

  return [
    ...next,
    {
      id: messageId,
      sessionId: payload.sessionId,
      taskId: payload.taskId ?? "pending",
      role: "assistant",
      content: payload.delta.trimStart(),
      createdAt: payload.now,
      updatedAt: payload.now,
      streaming: true,
      placeholder: false,
      status: "streaming",
      toolName: payload.toolName ?? undefined,
      metadata: {
        kind: "tool_use",
        toolUseId: payload.toolUseId,
      },
    },
  ];
}

export function completeAssistantToolUseMessage(
  current: ChatMessageView[],
  payload: {
    toolUseId: string;
    toolName: string;
    input: unknown;
    sessionId: string;
    taskId?: string | null;
    now: number;
  },
): ChatMessageView[] {
  const messageId = `tool_use:${payload.toolUseId}`;
  const inputText = formatChatBlockValue(payload.input);
  const next = [...current];
  const index = next.findIndex(
    (message) =>
      message.id === messageId ||
      (message.metadata?.kind === "tool_activity" && message.metadata?.toolUseId === payload.toolUseId),
  );
  const content = inputText || "{}";
  if (index >= 0) {
    const message = next[index];
    const isActivity = message.metadata?.kind === "tool_activity";
    return next.map((item, itemIndex) =>
      itemIndex === index
        ? {
            ...message,
            taskId: payload.taskId ?? message.taskId,
            content,
            updatedAt: payload.now,
            streaming: false,
            placeholder: false,
            status: "completed",
            toolName: payload.toolName,
            metadata: {
              ...(message.metadata ?? {}),
              kind: isActivity ? "tool_activity" : "tool_use",
              toolUseId: payload.toolUseId,
              input: payload.input,
              inputText: content,
            },
          }
        : item,
    );
  }

  return [
    ...next,
    {
      id: messageId,
      sessionId: payload.sessionId,
      taskId: payload.taskId ?? "pending",
      role: "assistant",
      content,
      createdAt: payload.now,
      updatedAt: payload.now,
      streaming: false,
      placeholder: false,
      status: "completed",
      toolName: payload.toolName,
      metadata: {
        kind: "tool_use",
        toolUseId: payload.toolUseId,
        input: payload.input,
        inputText: content,
      },
    },
  ];
}

export function appendAssistantToolResultMessage(
  current: ChatMessageView[],
  payload: {
    toolUseId: string;
    toolName?: string | null;
    content: unknown;
    isError?: boolean;
    sessionId: string;
    taskId?: string | null;
    now: number;
  },
): ChatMessageView[] {
  const messageId = `tool_result:${payload.toolUseId}`;
  const content = formatToolResultSummary(payload.content, payload.isError);
  const toolUseIndex = current.findIndex(
    (message) =>
      (message.metadata?.kind === "tool_use" || message.metadata?.kind === "tool_activity") &&
      message.metadata?.toolUseId === payload.toolUseId,
  );
  if (toolUseIndex >= 0) {
    const next = [...current];
    const message = next[toolUseIndex];
    const inputContent =
      typeof message.metadata?.inputText === "string"
        ? message.metadata.inputText
        : message.content;
    next[toolUseIndex] = {
      ...message,
      id: `tool_activity:${payload.toolUseId}`,
      taskId: payload.taskId ?? message.taskId,
      content: inputContent || content,
      updatedAt: payload.now,
      streaming: false,
      placeholder: false,
      status: payload.isError ? "failed" : "completed",
      toolName: payload.toolName ?? message.toolName,
      metadata: {
        ...(message.metadata ?? {}),
        kind: "tool_activity",
        toolUseId: payload.toolUseId,
        inputText: inputContent,
        resultText: content,
        input: message.metadata?.input,
        rawContent: payload.content,
        isError: Boolean(payload.isError),
      },
    };
    return next.filter((message, index) => index === toolUseIndex || message.id !== messageId);
  }
  const existingIndex = current.findIndex((message) => message.id === messageId);
  const nextMessage: ChatMessageView = {
    id: messageId,
    sessionId: payload.sessionId,
    taskId: payload.taskId ?? "pending",
    role: "assistant",
    content,
    createdAt: existingIndex >= 0 ? current[existingIndex].createdAt : payload.now,
    updatedAt: payload.now,
    streaming: false,
    placeholder: false,
    status: payload.isError ? "failed" : "completed",
    toolName: payload.toolName ?? undefined,
    metadata: {
      kind: "tool_result",
      toolUseId: payload.toolUseId,
      isError: Boolean(payload.isError),
      rawContent: payload.content,
    },
  };

  if (existingIndex >= 0) {
    const next = [...current];
    next[existingIndex] = nextMessage;
    return next;
  }
  return [...current, nextMessage];
}

function chatStatusLabel(state?: string, verb?: string | null): string {
  const action = verb ? String(verb).trim() : "";
  if (state === "tool_executing") {
    return action ? `正在调用 ${action}` : "正在调用工具";
  }
  if (state === "permission_pending") {
    return action ? `等待 ${action} 审批` : "等待权限审批";
  }
  if (state === "streaming") {
    return "正在输出回复";
  }
  if (state === "thinking") {
    return "模型正在思考";
  }
  return action || "正在处理";
}

export function appendOrUpdateAssistantThinkingMessage(
  current: ChatMessageView[],
  payload: {
    sessionId: string;
    taskId?: string | null;
    state?: string | null;
    verb?: string | null;
    text?: string | null;
    now: number;
  },
): ChatMessageView[] {
  const taskId = payload.taskId ?? "pending";
  const messageId = `assistant_thinking:${taskId}`;
  const content = payload.text?.trim() || chatStatusLabel(payload.state ?? undefined, payload.verb);
  const existingIndex = current.findIndex((message) => message.id === messageId);
  const nextMessage: ChatMessageView = {
    id: messageId,
    sessionId: payload.sessionId,
    taskId,
    role: "assistant",
    content,
    createdAt: existingIndex >= 0 ? current[existingIndex].createdAt : payload.now,
    updatedAt: payload.now,
    streaming: true,
    placeholder: false,
    status: "streaming",
    metadata: {
      kind: "assistant_thinking",
      state: payload.state,
      verb: payload.verb,
    },
  };

  if (existingIndex >= 0) {
    const next = [...current];
    next[existingIndex] = nextMessage;
    return next;
  }
  return [...current, nextMessage];
}

export function appendAssistantProgressMessage(
  current: ChatMessageView[],
  payload: {
    sessionId: string;
    taskId?: string | null;
    content: string;
    now: number;
    eventId?: string | null;
  },
): ChatMessageView[] {
  const content = payload.content.trim();
  if (!content) {
    return current;
  }

  const taskId = payload.taskId ?? "pending";
  const existingProgress = current
    .filter(
      (message) =>
        message.sessionId === payload.sessionId &&
        message.taskId === taskId &&
        message.metadata?.kind === "assistant_progress",
    );
  const latestProgress = existingProgress.at(-1);
  if (latestProgress?.content.trim() === content) {
    return current;
  }

  const messageId =
    payload.eventId && payload.eventId.trim()
      ? `assistant_progress:${payload.eventId.trim()}`
      : `assistant_progress:${taskId}:${payload.now}:${existingProgress.length}`;
  if (current.some((message) => message.id === messageId)) {
    return current;
  }

  return [
    ...current,
    {
      id: messageId,
      sessionId: payload.sessionId,
      taskId,
      role: "assistant",
      content,
      createdAt: payload.now,
      updatedAt: payload.now,
      streaming: false,
      placeholder: false,
      status: "completed",
      metadata: {
        kind: "assistant_progress",
      },
    },
  ];
}

export function removeAssistantThinkingMessage(
  current: ChatMessageView[],
  payload: {
    sessionId: string;
    taskId?: string | null;
  },
): ChatMessageView[] {
  return current.filter((message) => {
    if (message.metadata?.kind !== "assistant_thinking") {
      return true;
    }
    if (message.sessionId !== payload.sessionId) {
      return true;
    }
    return Boolean(payload.taskId && message.taskId !== payload.taskId);
  });
}

export function appendOrUpdatePermissionRequestMessage(
  current: ChatMessageView[],
  payload: {
    requestId: string;
    toolName?: string | null;
    input: unknown;
    description?: string | null;
    sessionId: string;
    taskId?: string | null;
    now: number;
  },
): ChatMessageView[] {
  const messageId = `permission_request:${payload.requestId}`;
  const input = formatChatBlockValue(payload.input);
  const content = [payload.description?.trim(), input].filter(Boolean).join("\n\n");
  const existingIndex = current.findIndex((message) => message.id === messageId);
  const nextMessage: ChatMessageView = {
    id: messageId,
    sessionId: payload.sessionId,
    taskId: payload.taskId ?? "pending",
    role: "assistant",
    content: content || "此操作需要确认后继续。",
    createdAt: existingIndex >= 0 ? current[existingIndex].createdAt : payload.now,
    updatedAt: payload.now,
    streaming: false,
    placeholder: false,
    toolName: payload.toolName ?? undefined,
    metadata: {
      kind: "permission_request",
      requestId: payload.requestId,
      input: payload.input,
    },
  };

  if (existingIndex >= 0) {
    const next = [...current];
    next[existingIndex] = nextMessage;
    return next;
  }
  return [...current, nextMessage];
}

export function resolvePermissionRequestMessage(
  current: ChatMessageView[],
  payload: {
    requestId: string;
    decision: "approved" | "rejected" | string;
    now: number;
  },
): ChatMessageView[] {
  const messageId = `permission_request:${payload.requestId}`;
  const existingIndex = current.findIndex((message) => message.id === messageId);
  if (existingIndex < 0) {
    return current;
  }
  const next = [...current];
  const message = next[existingIndex];
  next[existingIndex] = {
    ...message,
    updatedAt: payload.now,
    status: payload.decision === "rejected" ? "failed" : "completed",
    metadata: {
      ...(message.metadata ?? {}),
      kind: "permission_request",
      requestId: payload.requestId,
      decision: payload.decision,
      resolved: true,
    },
  };
  return next;
}

export function completeChatCompatMessage(
  current: ChatMessageView[],
  payload: {
    messageId?: string | null;
    sessionId: string;
    taskId?: string | null;
    content?: string | null;
    now: number;
  },
): ChatMessageView[] {
  const next = current.map((message) =>
    message.sessionId === payload.sessionId &&
    message.taskId === (payload.taskId ?? message.taskId) &&
    message.streaming
      ? {
          ...message,
          streaming: false,
          placeholder: false,
          status: message.status === "failed" ? message.status : ("completed" as const),
          updatedAt: payload.now,
        }
      : message,
  );
  if (!payload.messageId) {
    return next;
  }
  return appendOrUpdateAssistantMessageCompletion(next, payload as {
    messageId: string;
    sessionId: string;
    taskId?: string | null;
    content?: string | null;
    now: number;
  });
}

export function appendOrUpdateAssistantMessageCompletion(
  current: ChatMessageView[],
  payload: {
    messageId: string;
    sessionId: string;
    taskId?: string | null;
    content?: string | null;
    now: number;
  },
): ChatMessageView[] {
  const completedContent = payload.content ?? "";
  let updatedExisting = false;
  const updated = updateAssistantMessageByMessageId(
    current,
    payload.messageId,
    (msg) => {
      updatedExisting = true;
      const streamingContent = msg.content || "";
      const isPlaceholder =
        msg.placeholder === true ||
        streamingContent === "\u601d\u8003\u4e2d..." ||
        streamingContent.length < 5;
      const content = resolveAssistantCompletionContent(streamingContent, completedContent, isPlaceholder);
      return {
        ...msg,
        taskId: payload.taskId ?? msg.taskId,
        content,
        updatedAt: payload.now,
        streaming: false,
        placeholder: false,
        status: "completed",
      };
    },
    { sessionId: payload.sessionId, taskId: payload.taskId },
  );

  if (updatedExisting) {
    return updated;
  }

  if (!completedContent.trim()) {
    return current;
  }

  return [
    ...current,
    {
      id: payload.messageId,
      sessionId: payload.sessionId,
      taskId: payload.taskId ?? "persisted",
      role: "assistant",
      content: completedContent,
      createdAt: payload.now,
      updatedAt: payload.now,
      streaming: false,
      placeholder: false,
      status: "completed",
    },
  ];
}

export function resolveAssistantCompletionContent(
  streamingContent: string,
  completedContent: string,
  placeholder = false,
): string {
  const cleanedStreaming = stripAssistantRuntimeProgress(streamingContent);
  const cleanedCompleted = stripAssistantRuntimeProgress(completedContent);
  const streamingHadRuntimeProgress = cleanedStreaming.trim() !== streamingContent.replace(/\r\n/g, "\n").trim();
  const streamingRuntimeOnly = Boolean(streamingContent.trim()) && !cleanedStreaming.trim();

  if (placeholder || streamingRuntimeOnly || !cleanedStreaming.trim()) {
    return cleanedCompleted || completedContent || cleanedStreaming || streamingContent;
  }
  if (cleanedCompleted.trim()) {
    if (cleanedStreaming.includes(cleanedCompleted)) {
      return cleanedStreaming;
    }
    if (cleanedCompleted.includes(cleanedStreaming)) {
      return cleanedCompleted;
    }
    if (streamingHadRuntimeProgress) {
      return cleanedCompleted;
    }
  }
  return cleanedStreaming || cleanedCompleted || streamingContent || completedContent;
}

function findAttachableAssistantMessageIndex(
  messages: ChatMessageView[],
  options: {
    sessionId?: string;
    taskId?: string;
  },
): number {
  if (!options.sessionId) {
    return -1;
  }

  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    if (message.role !== "assistant" || message.sessionId !== options.sessionId || message.streaming !== true) {
      continue;
    }
    if (
      options.taskId &&
      message.taskId !== options.taskId &&
      message.taskId !== "pending"
    ) {
      continue;
    }
    if (
      message.placeholder === true ||
      message.taskId === "pending" ||
      message.id.startsWith("assistant_pending_") ||
      message.status === "streaming"
    ) {
      return index;
    }
  }

  return -1;
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
            status: message.status === "failed" ? message.status : undefined,
          }
        : message,
    )
    .filter((message) => {
      const belongsToSession = !sessionId || message.sessionId === sessionId;
      return !(belongsToSession && message.role === "assistant" && !message.content.trim());
    });
}

export function stopStreamingMessagesForTask(
  current: ChatMessageView[],
  payload: {
    sessionId: string;
    taskId?: string | null;
  },
): ChatMessageView[] {
  return current
    .map((message) => {
      const matchesTask =
        message.sessionId === payload.sessionId &&
        (!payload.taskId || message.taskId === payload.taskId || (message.streaming && message.taskId === "pending"));
      if (!matchesTask || message.role !== "assistant" || message.streaming !== true) {
        return message;
      }
      return {
        ...message,
        streaming: false,
        placeholder: false,
        status: message.status === "failed" ? message.status : undefined,
      };
    })
    .filter((message) => {
      const matchesTask =
        message.sessionId === payload.sessionId &&
        (!payload.taskId || message.taskId === payload.taskId || message.taskId === "pending");
      return !(matchesTask && message.role === "assistant" && !message.content.trim());
    });
}

export function isOperationalAssistantDelta(delta: string): boolean {
  const normalized = delta.trim();
  if (!normalized) {
    return true;
  }

  return (
    looksLikeRuntimeMachinePayload(normalized) ||
    normalized === "Building context and preparing the first tool calls..." ||
    normalized === "Completed the minimal tool loop and preparing a summary..." ||
    normalized.startsWith("Started subtask: ") ||
    normalized.startsWith("Finished subtask: ") ||
    normalized.startsWith("Subtask running tool: ") ||
    normalized.startsWith("Subtask tool completed: ") ||
    normalized.startsWith("Subtask tool failed: ") ||
    normalized.startsWith("Subtask waiting for approval: ") ||
    normalized.startsWith("Subtask approval ") ||
    normalized.startsWith("Subtask command ") ||
    normalized.startsWith("Running tool: ") ||
    normalized.startsWith("Running post-task ") ||
    normalized.startsWith("Running post-task validation command: ") ||
    normalized.startsWith("Approval accepted. Running the command now") ||
    normalized.startsWith("Approval accepted. Applying the patch now")
  );
}

function looksLikeRuntimeMachinePayload(value: string): boolean {
  const normalized = value.trim();
  if (!normalized) return false;
  if (/^(Task Cancelled|task\.[a-z0-9_.-]+|command\.|provider\.|agent\.)/i.test(normalized)) {
    return true;
  }
  if (
    /"?(sessionId|taskId|workspaceRoot|acceptanceCriteria|toolCallId|recoveryDecision|failureKind)"?\s*:/.test(
      normalized,
    )
  ) {
    return true;
  }
  if (!/^[{\[]/.test(normalized)) {
    return false;
  }
  try {
    const parsed = JSON.parse(normalized);
    if (!parsed || typeof parsed !== "object") return false;
    const keys = new Set(Object.keys(parsed as Record<string, unknown>));
    return [
      "sessionId",
      "taskId",
      "workspaceRoot",
      "acceptanceCriteria",
      "toolCallId",
      "failureKind",
      "recoveryDecision",
      "cwd",
    ].some((key) => keys.has(key));
  } catch {
    return true;
  }
}

export function sanitizeAssistantStatusContent(
  content: string | null | undefined,
  fallback = "任务失败，未返回具体错误。",
): string {
  const normalized = (content ?? "").replace(/\r\n/g, "\n").trim();
  if (!normalized) {
    return fallback;
  }

  if (/Cannot supplement task that is not active|cannot transition from 'cancelled'|terminal state/i.test(normalized)) {
    return "这条任务已经结束，不能继续补充；请重新发起一条任务。";
  }
  if (/^(Task Cancelled|task\.cancelled)/i.test(normalized)) {
    return "任务已取消，已停止继续执行。";
  }
  if (/concurrency limit exceeded/i.test(normalized)) {
    return "模型并发额度暂时满了，请稍后重试。";
  }
  if (/Command is not allowed by command allowlist|permission_denied/i.test(normalized)) {
    return "命令没有真正执行：运行时策略拦截了这条命令，需要先审批或使用允许的等价命令。";
  }
  if (looksLikeRuntimeMachinePayload(normalized)) {
    return fallback;
  }

  const lines = normalized
    .split("\n")
    .map((line) => line.trimEnd())
    .filter((line) => {
      const trimmed = line.trim();
      if (!trimmed) return true;
      if (looksLikeRuntimeMachinePayload(trimmed)) return false;
      if (/^task\.[a-z0-9_.-]+/i.test(trimmed)) return false;
      return true;
    });
  const cleaned = lines.join("\n").trim();
  if (cleaned) {
    return cleaned;
  }

  return fallback;
}

export function formatAssistantFailureContent(content: string | null | undefined): string {
  const cleaned = sanitizeAssistantStatusContent(content);
  if (
    /^(任务|这条任务|当前任务|命令没有真正执行|模型并发额度|模型调用|发送失败|Provider returned error)/.test(
      cleaned,
    )
  ) {
    return cleaned;
  }
  return `任务失败：${cleaned}`;
}

function friendlyToolLabel(toolName: string) {
  const normalized = toolName.trim();
  if (!normalized) return "工具";
  if (normalized === "list_dir") return "目录";
  if (normalized === "read_file") return "文件";
  if (normalized === "run_command") return "命令";
  if (normalized === "apply_patch") return "补丁";
  if (normalized === "write_file") return "写入文件";
  if (normalized === "git_status") return "Git 状态";
  if (normalized === "git_diff") return "代码差异";
  if (normalized === "search_files" || normalized === "code_search") return "代码搜索";
  return normalized;
}

function friendlyToolProgress(toolName: string) {
  const normalized = toolName.trim();
  if (normalized === "list_dir") return "我在查看目录。";
  if (normalized === "read_file") return "我在读取文件。";
  if (normalized === "run_command") return "我在运行命令。";
  if (normalized === "apply_patch" || normalized === "write_file") return "我在准备文件改动。";
  if (normalized === "git_status") return "我在检查 Git 状态。";
  if (normalized === "git_diff") return "我在读取代码差异。";
  if (normalized === "search_files" || normalized === "code_search") return "我在搜索代码。";
  return `我在使用${friendlyToolLabel(toolName)}。`;
}

function isAssistantRuntimeProgressLine(line: string) {
  const normalized = line.trim();
  if (!normalized) {
    return false;
  }
  if (isEnglishAssistantRuntimeProgressLine(normalized)) {
    return true;
  }
  return (
    /^我在(查看目录|读取文件|运行命令|准备文件改动|检查 Git 状态|读取代码差异|搜索代码|使用.+)。?$/.test(normalized) ||
    /^正在(整理上下文|做收尾验证|做任务后的收尾检查|运行命令|应用文件改动)/.test(normalized) ||
    /^工具检查已完成/.test(normalized) ||
    /^开始处理：/.test(normalized) ||
    /^处理完成：/.test(normalized) ||
    /^(命令|工具|目录|文件|补丁|Git 状态|代码差异|代码搜索)已完成。$/.test(normalized) ||
    /^等待审批：/.test(normalized) ||
    /^审批状态已更新/.test(normalized) ||
    /^命令(已开始运行|已完成|失败|状态：)/.test(normalized)
  );
}

function isEnglishAssistantRuntimeProgressLine(normalized: string): boolean {
  return (
    /^Building context and preparing the first tool calls\.\.\.$/i.test(normalized) ||
    /^Completed the minimal tool loop and preparing a summary\.\.\.$/i.test(normalized) ||
    /^(Started|Finished) subtask:\s+/i.test(normalized) ||
    /^Subtask (running tool|tool completed|tool failed|waiting for approval|approval|command)\b/i.test(normalized) ||
    /^Running tool:\s+/i.test(normalized) ||
    /^Running post-task\b/i.test(normalized) ||
    /^Approval accepted\./i.test(normalized)
  );
}

export function stripAssistantRuntimeProgress(content: string): string {
  const normalized = content
    .replace(/\r\n/g, "\n")
    .replace(/^\s*Building context and preparing the first tool calls\.\.\.\s*/i, "")
    .replace(/^\s*Completed the minimal tool loop and preparing a summary\.\.\.\s*/i, "");
  const lines = normalized.split("\n");
  const kept: string[] = [];
  for (const line of lines) {
    if (isAssistantRuntimeProgressLine(line)) {
      continue;
    }
    kept.push(line);
  }
  return kept.join("\n").replace(/\n{3,}/g, "\n\n").trim();
}

function progressLine(text: string) {
  return `\n\n${text}`;
}

function formatChatBlockValue(value: unknown): string {
  if (value === null || value === undefined) {
    return "";
  }
  if (typeof value === "string") {
    return value.trim();
  }
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function formatToolResultSummary(value: unknown, isError?: boolean): string {
  const raw = formatChatBlockValue(value);
  if (!raw) {
    return isError ? "Tool failed without a detailed result." : "Tool completed.";
  }
  if (raw.length <= 1600) {
    return raw;
  }
  return `${raw.slice(0, 1600).trimEnd()}\n...`;
}

export function summarizeOperationalAssistantDelta(delta: string): string | null {
  const normalized = delta.trim();
  if (!normalized) return null;
  if (/^(Task Cancelled|task\.cancelled)/i.test(normalized)) {
    return progressLine("任务已取消，已停止继续执行。");
  }
  if (looksLikeRuntimeMachinePayload(normalized)) {
    return null;
  }
  if (normalized === "Building context and preparing the first tool calls...") {
    return progressLine("正在整理上下文，并确定要先查看的文件和工具。");
  }
  if (normalized === "Completed the minimal tool loop and preparing a summary...") {
    return progressLine("工具检查已完成，正在整理当前结论。");
  }
  if (normalized.startsWith("Started subtask: ")) {
    return progressLine(`开始处理：${normalized.slice("Started subtask: ".length).trim()}`);
  }
  if (normalized.startsWith("Finished subtask: ")) {
    return progressLine(`处理完成：${normalized.slice("Finished subtask: ".length).trim()}`);
  }
  if (normalized.startsWith("Subtask running tool: ")) {
    return progressLine(friendlyToolProgress(normalized.slice("Subtask running tool: ".length)));
  }
  if (normalized.startsWith("Subtask tool completed: ")) {
    return progressLine(`${friendlyToolLabel(normalized.slice("Subtask tool completed: ".length))}已完成。`);
  }
  if (normalized.startsWith("Subtask tool failed: ")) {
    return progressLine(`${friendlyToolLabel(normalized.slice("Subtask tool failed: ".length))}失败，正在根据输出定位原因。`);
  }
  if (normalized.startsWith("Subtask waiting for approval: ")) {
    return progressLine(`等待审批：${friendlyToolLabel(normalized.slice("Subtask waiting for approval: ".length))}。`);
  }
  if (normalized.startsWith("Subtask approval ")) {
    return progressLine("审批状态已更新，继续推进。");
  }
  if (normalized.startsWith("Subtask command ")) {
    const commandStatus = normalized.slice("Subtask command ".length).trim();
    if (commandStatus === "started") return progressLine("命令已开始运行。");
    if (commandStatus === "completed") return progressLine("命令已完成。");
    if (commandStatus === "failed") return progressLine("命令失败，正在查看输出并准备修复。");
    return progressLine(`命令状态：${commandStatus}`);
  }
  if (normalized.startsWith("Running tool: ")) {
    return progressLine(friendlyToolProgress(normalized.slice("Running tool: ".length)));
  }
  if (normalized.startsWith("Running post-task validation command: ")) {
    return progressLine(`正在做收尾验证：${normalized.slice("Running post-task validation command: ".length).trim()}`);
  }
  if (normalized.startsWith("Running post-task ")) {
    return progressLine("正在做任务后的收尾检查。");
  }
  if (normalized.startsWith("Approval accepted. Running the command now")) {
    return progressLine("审批已通过，正在运行命令。");
  }
  if (normalized.startsWith("Approval accepted. Applying the patch now")) {
    return progressLine("审批已通过，正在应用文件改动。");
  }
  return null;
}

export function appendAssistantContentDelta(existingContent: string, delta: string) {
  const text = delta.replace(/\r\n/g, "\n");
  if (!text.trim()) return existingContent;
  const existing = existingContent ?? "";
  if (existing.endsWith(text)) {
    return existing;
  }
  const existingTrimmed = existing.trim();
  const incomingTrimmedStart = text.trimStart();
  const incomingTrimmed = text.trim();
  if (incomingTrimmed && existing.includes(incomingTrimmed)) {
    return existing;
  }
  if (existingTrimmed && incomingTrimmedStart.startsWith(existingTrimmed)) {
    return incomingTrimmedStart;
  }
  if (!existing.trim()) return text.trimStart();
  return `${existing}${text}`;
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
    .filter((message) => !isEmptyStreamingAssistantShell(message))
    .filter((message) => !isRuntimeProgressOnlyAssistantMessage(message))
    .sort((left, right) => sortBySeqAndTime(left, right));
}

function isEmptyStreamingAssistantShell(message: ChatMessageView): boolean {
  return (
    message.role === "assistant" &&
    (message.streaming === true || message.status === "streaming") &&
    message.placeholder !== true &&
    !message.content.trim()
  );
}

function isRuntimeProgressOnlyAssistantMessage(message: ChatMessageView): boolean {
  if (
    message.metadata?.kind === "tool_use" ||
    message.metadata?.kind === "tool_result" ||
    message.metadata?.kind === "tool_activity" ||
    message.metadata?.kind === "assistant_progress" ||
    message.metadata?.kind === "assistant_thinking" ||
    message.metadata?.kind === "permission_request"
  ) {
    return false;
  }
  return (
    message.role === "assistant" &&
    message.placeholder !== true &&
    Boolean(message.content.trim()) &&
    !stripAssistantRuntimeProgress(message.content).trim()
  );
}
