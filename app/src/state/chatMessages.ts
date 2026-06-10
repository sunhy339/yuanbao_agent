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

interface ReplaceSessionMessagesOptions {
  taskIds?: string[];
  includeUserMessages?: boolean;
  excludeTaskIds?: string[];
  preserveOtherTaskMessages?: boolean;
}

function isAssistantTextStreamTarget(message: ChatMessageView): boolean {
  const kind = message.metadata?.kind;
  return (
    kind == null ||
    kind === "assistant_text" ||
    message.placeholder === true ||
    message.id.startsWith("assistant_pending_")
  );
}

function asAssistantTextMessage(message: ChatMessageView): ChatMessageView {
  if (!message.metadata?.kind || message.metadata.kind === "assistant_text") {
    return message;
  }
  const { kind: _kind, ...metadata } = message.metadata;
  return {
    ...message,
    metadata: Object.keys(metadata).length > 0 ? metadata : undefined,
  };
}

function isAssistantContentBlockMessage(message: ChatMessageView): boolean {
  return (
    message.role === "assistant" &&
    (typeof message.metadata?.contentBlockId === "string" ||
      typeof message.metadata?.baseMessageId === "string")
  );
}

function assistantContentBlockMetadata(
  existing: Record<string, unknown> | undefined,
  payload: {
    baseMessageId?: string | null;
    contentBlockId?: string | null;
    blockIndex?: number | null;
  },
): Record<string, unknown> | undefined {
  const metadata: Record<string, unknown> = { ...(existing ?? {}) };
  if (payload.baseMessageId) {
    metadata.baseMessageId = payload.baseMessageId;
  }
  if (payload.contentBlockId) {
    metadata.contentBlockId = payload.contentBlockId;
  }
  if (typeof payload.blockIndex === "number" && Number.isFinite(payload.blockIndex)) {
    metadata.blockIndex = payload.blockIndex;
  }
  return Object.keys(metadata).length ? metadata : undefined;
}

function hasAssistantContentBlocksForMessage(
  messages: ChatMessageView[],
  payload: {
    sessionId: string;
    taskId?: string | null;
    messageId?: string | null;
  },
): boolean {
  const messageId = payload.messageId?.trim();
  return messages.some((message) => {
    if (!isAssistantContentBlockMessage(message)) {
      return false;
    }
    if (message.sessionId !== payload.sessionId) {
      return false;
    }
    if (payload.taskId && message.taskId !== payload.taskId) {
      return false;
    }
    return !messageId || message.metadata?.baseMessageId === messageId || message.id === messageId;
  });
}

function withoutEmptyAssistantBaseMessage(
  messages: ChatMessageView[],
  payload: {
    sessionId: string;
    taskId?: string | null;
    messageId?: string | null;
  },
): ChatMessageView[] {
  const messageId = payload.messageId?.trim();
  if (!messageId) {
    return messages;
  }
  return messages.filter((message) => {
    if (message.id !== messageId || message.role !== "assistant") {
      return true;
    }
    if (message.sessionId !== payload.sessionId) {
      return true;
    }
    if (payload.taskId && message.taskId !== payload.taskId) {
      return true;
    }
    if (isAssistantContentBlockMessage(message)) {
      return true;
    }
    return message.content.trim().length > 0;
  });
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
    metadata: record.metadata,
  };
}

export function replaceSessionMessages(
  current: ChatMessageView[],
  sessionId: string,
  records: MessageRecord[],
  options: ReplaceSessionMessagesOptions = {},
): ChatMessageView[] {
  const allowedTaskIds = options.taskIds?.length ? new Set(options.taskIds.filter(Boolean)) : null;
  const excludedTaskIds = options.excludeTaskIds?.length ? new Set(options.excludeTaskIds.filter(Boolean)) : null;
  const includeUserMessages = options.includeUserMessages !== false;
  const preserveOtherTaskMessages = Boolean(allowedTaskIds && options.preserveOtherTaskMessages);
  const persistedMessages = records
    .map(messageRecordToChatMessage)
    .filter((message): message is ChatMessageView => message !== null)
    .filter((message) => {
      if (excludedTaskIds?.has(message.taskId)) {
        return false;
      }
      if (!allowedTaskIds) {
        return true;
      }
      if (includeUserMessages && message.role === "user") {
        return true;
      }
      return allowedTaskIds.has(message.taskId);
    });

  const segmentedBaseMessageIds = new Set<string>();
  const segmentedTaskIds = new Set<string>();
  for (const message of current) {
    if (message.sessionId !== sessionId || !isAssistantContentBlockMessage(message)) {
      continue;
    }
    const baseMessageId = message.metadata?.baseMessageId;
    if (typeof baseMessageId === "string" && baseMessageId.trim()) {
      segmentedBaseMessageIds.add(baseMessageId);
    }
    if (message.taskId) {
      segmentedTaskIds.add(message.taskId);
    }
  }
  const persistedMessagesForRender = persistedMessages.filter((message) => {
    if (message.role !== "assistant") {
      return true;
    }
    return !(segmentedBaseMessageIds.has(message.id) || segmentedTaskIds.has(message.taskId));
  });

  // Single pass over current to split into other-session, live-streaming, and pending-local
  const otherSessionMessages: ChatMessageView[] = [];
  const segmentedAssistantMessages: ChatMessageView[] = [];
  const liveStreamingMessages: ChatMessageView[] = [];
  const pendingLocalMessages: ChatMessageView[] = [];
  const ephemeralBlockMessages: ChatMessageView[] = [];
  const preservedOtherTaskMessages: ChatMessageView[] = [];
  for (const message of current) {
    if (message.sessionId !== sessionId) {
      otherSessionMessages.push(message);
    } else if (excludedTaskIds?.has(message.taskId)) {
      continue;
    } else if (isAssistantContentBlockMessage(message)) {
      segmentedAssistantMessages.push(message);
    } else if (message.streaming) {
      liveStreamingMessages.push(message);
    } else if (isLocalPendingMessage(message)) {
      pendingLocalMessages.push(message);
    } else if (isEphemeralChatBlockMessage(message)) {
      ephemeralBlockMessages.push(message);
    } else if (
      preserveOtherTaskMessages &&
      allowedTaskIds &&
      !allowedTaskIds.has(message.taskId) &&
      !(excludedTaskIds?.has(message.taskId))
    ) {
      preservedOtherTaskMessages.push(message);
    }
  }

  const persistedIds = new Set(persistedMessagesForRender.map((message) => message.id));
  const persistedClientMessageIds = new Set(
    persistedMessagesForRender.map((message) => message.clientMessageId).filter((id): id is string => Boolean(id)),
  );
  const dedupedPreservedOtherTaskMessages = preservedOtherTaskMessages.filter((message) => {
    if (persistedIds.has(message.id)) {
      return false;
    }
    return !(message.clientMessageId && persistedClientMessageIds.has(message.clientMessageId));
  });
  const unmatchedLiveStreamingMessages = liveStreamingMessages.filter((message) => {
    if (persistedIds.has(message.id)) {
      return false;
    }
    return !(message.clientMessageId && persistedClientMessageIds.has(message.clientMessageId));
  });

  const maxPersistedTime = persistedMessagesForRender.reduce((max, msg) => Math.max(max, msg.createdAt), 0);
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
    const match = findPersistedMatch(persistedMessagesForRender, local, matchedPersistedIds);
    if (match) {
      matchedPersistedIds.add(match.id);
    } else {
      unmatchedPendingLocalMessages.push(local);
    }
  }

  return [
    ...otherSessionMessages,
    ...dedupedPreservedOtherTaskMessages,
    ...persistedMessagesForRender,
    ...segmentedAssistantMessages,
    ...ephemeralBlockMessages,
    ...unmatchedPendingLocalMessages,
    ...updatedLiveStreamingMessages,
  ].sort((left, right) => sortBySeqAndTime(left, right));
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
  const kind = message.metadata?.kind;
  return (
    kind === "tool_use" ||
    kind === "tool_result" ||
    kind === "tool_activity" ||
    kind === "assistant_progress" ||
    kind === "assistant_thinking" ||
    kind === "permission_request" ||
    kind === "api_retry" ||
    kind === "system_notification" ||
    kind === "system" ||
    kind === "compact_summary" ||
    kind === "goal_event" ||
    kind === "memory_event" ||
    kind === "ask_user_question" ||
    kind === "computer_use_permission_request" ||
    kind === "computer_use_permission" ||
    kind === "background_task" ||
    kind === "agent_task_group" ||
    kind === "task_summary" ||
    kind === "plan_update" ||
    kind === "slash_command" ||
    kind === "status"
  );
}

interface ToolBatchMetadataPayload {
  toolGroupId?: string | null;
  toolIndex?: number | null;
  toolTotal?: number | null;
  toolOperationId?: string | null;
  toolOperationLabel?: string | null;
  toolCategory?: string | null;
  toolPhaseId?: string | null;
  toolPhaseLabel?: string | null;
  toolSemanticParentId?: string | null;
  toolSemanticParentLabel?: string | null;
  target?: string | null;
  inputSummary?: string | null;
  durationMs?: number | null;
}

interface ToolPresentationPayload extends ToolBatchMetadataPayload {
  displayTitle?: string | null;
  displaySummary?: string | null;
  displayTarget?: string | null;
  displayKind?: string | null;
}

type ToolResultDisplayMetadata = {
  resultText: string;
  changedPaths?: string[];
  filesChanged?: number;
  diffText?: string;
  previewRows?: Array<{ label: string; value: string }>;
};

function toolBatchMetadataFromPayload(
  payload: ToolBatchMetadataPayload,
  current?: Record<string, unknown>,
): Record<string, unknown> {
  const metadata: Record<string, unknown> = {};
  const toolGroupId = payload.toolGroupId ?? current?.toolGroupId;
  const toolIndex = payload.toolIndex ?? current?.toolIndex;
  const toolTotal = payload.toolTotal ?? current?.toolTotal;
  const toolOperationId = payload.toolOperationId ?? current?.toolOperationId;
  const toolOperationLabel = payload.toolOperationLabel ?? current?.toolOperationLabel;
  const toolCategory = payload.toolCategory ?? current?.toolCategory;
  const toolPhaseId = payload.toolPhaseId ?? current?.toolPhaseId;
  const toolPhaseLabel = payload.toolPhaseLabel ?? current?.toolPhaseLabel;
  const toolSemanticParentId = payload.toolSemanticParentId ?? current?.toolSemanticParentId;
  const toolSemanticParentLabel = payload.toolSemanticParentLabel ?? current?.toolSemanticParentLabel;
  const target = payload.target ?? current?.target;
  const inputSummary = payload.inputSummary ?? current?.inputSummary;
  const durationMs = payload.durationMs ?? current?.durationMs;
  if (toolGroupId != null) {
    metadata.toolGroupId = toolGroupId;
  }
  if (toolIndex != null) {
    metadata.toolIndex = toolIndex;
  }
  if (toolTotal != null) {
    metadata.toolTotal = toolTotal;
  }
  if (toolOperationId != null) {
    metadata.toolOperationId = toolOperationId;
  }
  if (toolOperationLabel != null) {
    metadata.toolOperationLabel = toolOperationLabel;
  }
  if (toolCategory != null) {
    metadata.toolCategory = toolCategory;
  }
  if (toolPhaseId != null) {
    metadata.toolPhaseId = toolPhaseId;
  }
  if (toolPhaseLabel != null) {
    metadata.toolPhaseLabel = toolPhaseLabel;
  }
  if (toolSemanticParentId != null) {
    metadata.toolSemanticParentId = toolSemanticParentId;
  }
  if (toolSemanticParentLabel != null) {
    metadata.toolSemanticParentLabel = toolSemanticParentLabel;
  }
  if (target != null) {
    metadata.target = target;
  }
  if (inputSummary != null) {
    metadata.inputSummary = inputSummary;
  }
  if (typeof durationMs === "number" && Number.isFinite(durationMs)) {
    metadata.durationMs = durationMs;
  }
  return metadata;
}

function toolPresentationMetadataFromPayload(
  payload: ToolPresentationPayload,
  current?: Record<string, unknown>,
): Record<string, unknown> {
  const metadata = toolBatchMetadataFromPayload(payload, current);
  const displayTitle = payload.displayTitle ?? current?.displayTitle;
  const displaySummary = payload.displaySummary ?? current?.displaySummary;
  const displayTarget = payload.displayTarget ?? current?.displayTarget;
  const displayKind = payload.displayKind ?? current?.displayKind;
  if (displayTitle != null) {
    metadata.displayTitle = displayTitle;
  }
  if (displaySummary != null) {
    metadata.displaySummary = displaySummary;
  }
  if (displayTarget != null) {
    metadata.displayTarget = displayTarget;
  }
  if (displayKind != null) {
    metadata.displayKind = displayKind;
  }
  return metadata;
}

function compactDisplayText(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function toolInputDisplayText(payload: ToolPresentationPayload, current?: Record<string, unknown>): string {
  return compactDisplayText(payload.displayTitle) ||
    compactDisplayText(current?.displayTitle) ||
    compactDisplayText(payload.inputSummary) ||
    compactDisplayText(current?.inputSummary) ||
    compactDisplayText(payload.target) ||
    compactDisplayText(current?.target) ||
    compactDisplayText(payload.displayTarget) ||
    compactDisplayText(current?.displayTarget);
}

function toolDisplayTitle(payload: ToolPresentationPayload, current?: Record<string, unknown>): string {
  return toolInputDisplayText(payload, current);
}

function toolDisplaySummary(payload: ToolPresentationPayload, current?: Record<string, unknown>): string {
  return compactDisplayText(payload.displaySummary) ||
    compactDisplayText(current?.displaySummary) ||
    compactDisplayText(payload.inputSummary) ||
    compactDisplayText(current?.inputSummary) ||
    compactDisplayText(payload.displayTitle) ||
    compactDisplayText(current?.displayTitle);
}

function normalizePreviewRows(value: unknown): Array<{ label: string; value: string }> | undefined {
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

function normalizeChangedPathList(value: unknown): string[] | undefined {
  const paths = readPublicRecordStringList(
    isPlainRecord({ value }) ? { value } : {},
    ["value"],
  )
    .map((path) => path.replace(/^[ab]\//, "").trim())
    .filter((path) => path && path !== "/dev/null");
  const unique = Array.from(new Set(paths));
  return unique.length ? unique.slice(0, 12) : undefined;
}

function structuredToolResultDisplay(
  value: unknown,
  fallbackText: string,
  previewRows?: Array<{ label: string; value: string }>,
  explicit?: {
    changedPaths?: string[] | null;
    filesChanged?: number | null;
    diffText?: string | null;
  },
): ToolResultDisplayMetadata {
  const record = isPlainRecord(value) ? value : undefined;
  const changedPaths =
    normalizeChangedPathList(explicit?.changedPaths) ??
    (record ? normalizeChangedPathList(record.changedPaths ?? record.paths ?? record.files ?? record.targets ?? record.changes) : undefined);
  const filesChanged =
    typeof explicit?.filesChanged === "number" && Number.isFinite(explicit.filesChanged)
      ? explicit.filesChanged
      :
    record && typeof record.filesChanged === "number" && Number.isFinite(record.filesChanged)
      ? record.filesChanged
      : record && typeof record.fileCount === "number" && Number.isFinite(record.fileCount)
        ? record.fileCount
        : changedPaths?.length;
  const diffText = compactDisplayText(explicit?.diffText) || (record ? readPublicRecordText(record, ["diffText", "patchText", "diff"]) : "");
  const resultText = compactDisplayText(fallbackText) || (record ? summarizePublicChatObject(record) : "");
  return {
    resultText: resultText || "Tool completed.",
    ...(changedPaths?.length ? { changedPaths } : {}),
    ...(typeof filesChanged === "number" ? { filesChanged } : {}),
    ...(diffText ? { diffText } : {}),
    ...(previewRows?.length ? { previewRows } : {}),
  };
}

export function appendUserMessage(
  current: ChatMessageView[],
  payload: {
    id: string;
    sessionId: string;
    content: string;
    now: number;
    clientMessageId?: string;
    metadata?: Record<string, unknown>;
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
      metadata: payload.metadata,
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
    allowAttachFallback?: boolean;
  } = {},
): ChatMessageView[] {
  const exactIndex = current.findIndex((m) => m.id === messageId);
  const index =
    exactIndex >= 0
      ? exactIndex
      : options.allowAttachFallback === false
        ? -1
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

export function assistantTextBlockMessageId(messageId: string, contentBlockId?: string | null, blockIndex?: number | null): string {
  const normalizedBlockId = contentBlockId?.trim();
  if (normalizedBlockId) {
    return `assistant_text:${normalizedBlockId}`;
  }
  if (typeof blockIndex === "number" && Number.isFinite(blockIndex)) {
    return `assistant_text:${messageId}:text:${blockIndex}`;
  }
  return messageId;
}

export function appendOrUpdateAssistantMessageDelta(
  current: ChatMessageView[],
  payload: {
    messageId: string;
    contentBlockId?: string | null;
    blockIndex?: number | null;
    sessionId: string;
    taskId?: string | null;
    delta: string;
    now: number;
  },
): ChatMessageView[] {
  const hasBlockIdentity =
    (typeof payload.contentBlockId === "string" && payload.contentBlockId.trim()) ||
    (typeof payload.blockIndex === "number" && Number.isFinite(payload.blockIndex));
  const targetMessageId = hasBlockIdentity
    ? assistantTextBlockMessageId(payload.messageId, payload.contentBlockId, payload.blockIndex)
    : payload.messageId;
  const sourceMessages = hasBlockIdentity
    ? withoutEmptyAssistantBaseMessage(current, {
        sessionId: payload.sessionId,
        taskId: payload.taskId,
        messageId: payload.messageId,
      })
    : current;
  let updatedExisting = false;
  const updated = updateAssistantMessageByMessageId(
    sourceMessages,
    targetMessageId,
    (msg) => {
      updatedExisting = true;
      const textMessage = asAssistantTextMessage(msg);
      return {
        ...textMessage,
        taskId: payload.taskId ?? msg.taskId,
        metadata: assistantContentBlockMetadata(textMessage.metadata, {
          baseMessageId: hasBlockIdentity ? payload.messageId : undefined,
          contentBlockId: payload.contentBlockId,
          blockIndex: payload.blockIndex,
        }),
        content: msg.placeholder
          ? payload.delta.trimStart()
          : appendAssistantContentDelta(msg.content, payload.delta),
        updatedAt: payload.now,
        streaming: true,
        placeholder: false,
        status: "streaming",
      };
    },
    { sessionId: payload.sessionId, taskId: payload.taskId, allowAttachFallback: !hasBlockIdentity },
  );

  if (updatedExisting) {
    return updated;
  }

  return [
    ...sourceMessages,
    {
      id: targetMessageId,
      sessionId: payload.sessionId,
      taskId: payload.taskId ?? "pending",
      role: "assistant",
      content: payload.delta.trimStart(),
      createdAt: payload.now,
      updatedAt: payload.now,
      streaming: true,
      placeholder: false,
      status: "streaming",
      metadata: assistantContentBlockMetadata(undefined, {
        baseMessageId: hasBlockIdentity ? payload.messageId : undefined,
        contentBlockId: payload.contentBlockId,
        blockIndex: payload.blockIndex,
      }),
    },
  ];
}

export function appendOrUpdateAssistantToolInputDelta(
  current: ChatMessageView[],
  payload: ToolPresentationPayload & {
    toolUseId: string;
    toolName?: string | null;
    parentToolUseId?: string | null;
    sessionId: string;
    taskId?: string | null;
    delta: string;
    now: number;
  },
): ChatMessageView[] {
  const messageId = `tool_use:${payload.toolUseId}`;
  const inputDelta = appendAssistantContentDelta(
    typeof current.find((message) => message.id === messageId)?.metadata?.rawInputText === "string"
      ? current.find((message) => message.id === messageId)?.metadata?.rawInputText as string
      : "",
    payload.delta,
  );
  const next = [...current];
  const index = next.findIndex((message) => message.id === messageId);
  if (index >= 0) {
    const message = next[index];
    const rawInputText = appendAssistantContentDelta(
      typeof message.metadata?.rawInputText === "string" ? message.metadata.rawInputText : "",
      payload.delta,
    );
    const displayContent = toolInputDisplayText(payload, message.metadata);
    next[index] = {
      ...message,
      taskId: payload.taskId ?? message.taskId,
      content: displayContent || message.content,
      updatedAt: payload.now,
      streaming: true,
      placeholder: false,
      status: "streaming",
      toolName: payload.toolName ?? message.toolName,
      metadata: {
        ...(message.metadata ?? {}),
        kind: "tool_use",
        toolUseId: payload.toolUseId,
        parentToolUseId: payload.parentToolUseId ?? message.metadata?.parentToolUseId,
        ...toolPresentationMetadataFromPayload(payload, message.metadata),
        rawInputText,
        ...(displayContent ? { inputText: displayContent } : {}),
      },
    };
    return next;
  }

  const displayContent = toolInputDisplayText(payload);
  return [
    ...next,
    {
      id: messageId,
      sessionId: payload.sessionId,
      taskId: payload.taskId ?? "pending",
      role: "assistant",
      content: displayContent,
      createdAt: payload.now,
      updatedAt: payload.now,
      streaming: true,
      placeholder: false,
      status: "streaming",
      toolName: payload.toolName ?? undefined,
      metadata: {
        kind: "tool_use",
        toolUseId: payload.toolUseId,
        parentToolUseId: payload.parentToolUseId ?? undefined,
        ...toolPresentationMetadataFromPayload(payload),
        rawInputText: inputDelta.trimStart(),
        ...(displayContent ? { inputText: displayContent } : {}),
      },
    },
  ];
}

function appendToolOutputDelta(
  currentOutput: unknown,
  stream: string,
  delta: string,
): Record<string, string> {
  const output = currentOutput && typeof currentOutput === "object" && !Array.isArray(currentOutput)
    ? { ...(currentOutput as Record<string, unknown>) }
    : {};
  const key = stream === "stderr"
    ? "stderr"
    : stream === "stdout"
      ? "stdout"
      : stream === "activity"
        ? "activity"
        : "result";
  return {
    activity: typeof output.activity === "string" ? output.activity : "",
    stdout: typeof output.stdout === "string" ? output.stdout : "",
    stderr: typeof output.stderr === "string" ? output.stderr : "",
    result: typeof output.result === "string" ? output.result : "",
    [key]: appendAssistantContentDelta(typeof output[key] === "string" ? output[key] : "", delta),
  };
}

function formatToolOutputText(output: unknown) {
  if (!output || typeof output !== "object" || Array.isArray(output)) {
    return "";
  }
  const record = output as Record<string, unknown>;
  const activity = typeof record.activity === "string" ? record.activity.trimEnd() : "";
  const stdout = typeof record.stdout === "string" ? record.stdout.trimEnd() : "";
  const stderr = typeof record.stderr === "string" ? record.stderr.trimEnd() : "";
  const result = typeof record.result === "string" ? record.result.trimEnd() : "";
  return [
    activity ? `过程\n${activity}` : "",
    stdout ? `stdout\n${stdout}` : "",
    stderr ? `stderr\n${stderr}` : "",
    result ? `结果预览\n${result}` : "",
  ].filter(Boolean).join("\n\n");
}

export function appendOrUpdateAssistantToolOutputDelta(
  current: ChatMessageView[],
  payload: ToolPresentationPayload & {
    toolUseId: string;
    toolName?: string | null;
    parentToolUseId?: string | null;
    sessionId: string;
    taskId?: string | null;
    delta: string;
    stream?: string | null;
    now: number;
  },
): ChatMessageView[] {
  const messageId = `tool_use:${payload.toolUseId}`;
  const stream = payload.stream === "stderr"
    ? "stderr"
    : payload.stream === "stdout"
      ? "stdout"
      : payload.stream === "activity"
        ? "activity"
        : "result_preview";
  const next = [...current];
  const index = next.findIndex(
    (message) =>
      message.id === messageId ||
      message.id === `tool_activity:${payload.toolUseId}` ||
      ((message.metadata?.kind === "tool_use" || message.metadata?.kind === "tool_activity") &&
        message.metadata?.toolUseId === payload.toolUseId),
  );
  if (index >= 0) {
    const message = next[index];
    const output = appendToolOutputDelta(message.metadata?.output, stream, payload.delta);
    const resultText = formatToolOutputText(output) || (
      typeof message.metadata?.resultText === "string" ? message.metadata.resultText : ""
    );
    next[index] = {
      ...message,
      taskId: payload.taskId ?? message.taskId,
      updatedAt: payload.now,
      streaming: true,
      placeholder: false,
      status: "streaming",
      toolName: payload.toolName ?? message.toolName,
      metadata: {
        ...(message.metadata ?? {}),
        kind: message.metadata?.kind === "tool_activity" ? "tool_activity" : "tool_use",
        toolUseId: payload.toolUseId,
        parentToolUseId: payload.parentToolUseId ?? message.metadata?.parentToolUseId,
        ...toolPresentationMetadataFromPayload(payload, message.metadata),
        output,
        resultText,
      },
    };
    return next;
  }

  const output = appendToolOutputDelta(undefined, stream, payload.delta);
  return [
    ...next,
    {
      id: messageId,
      sessionId: payload.sessionId,
      taskId: payload.taskId ?? "pending",
      role: "assistant",
      content: "",
      createdAt: payload.now,
      updatedAt: payload.now,
      streaming: true,
      placeholder: false,
      status: "streaming",
      toolName: payload.toolName ?? undefined,
      metadata: {
        kind: "tool_use",
        toolUseId: payload.toolUseId,
        parentToolUseId: payload.parentToolUseId ?? undefined,
        ...toolPresentationMetadataFromPayload(payload),
        output,
        resultText: formatToolOutputText(output),
      },
    },
  ];
}

export function appendOrUpdateAssistantToolStartMessage(
  current: ChatMessageView[],
  payload: ToolPresentationPayload & {
    toolUseId: string;
    toolName?: string | null;
    parentToolUseId?: string | null;
    input?: unknown;
    sessionId: string;
    taskId?: string | null;
    now: number;
  },
): ChatMessageView[] {
  const messageId = `tool_use:${payload.toolUseId}`;
  const inputText = payload.input === undefined ? undefined : formatChatBlockValue(payload.input);
  const next = [...current];
  const index = next.findIndex(
    (message) =>
      message.id === messageId ||
      ((message.metadata?.kind === "tool_use" || message.metadata?.kind === "tool_activity") &&
        message.metadata?.toolUseId === payload.toolUseId),
  );
  if (index >= 0) {
    const message = next[index];
    const displayContent = toolDisplayTitle(payload, message.metadata);
    const terminal =
      message.status === "completed" ||
      message.status === "failed" ||
      message.status === "blocked" ||
      message.status === "cancelled";
    next[index] = {
      ...message,
      taskId: payload.taskId ?? message.taskId,
      content: displayContent || message.content,
      updatedAt: payload.now,
      streaming: terminal ? message.streaming : true,
      status: terminal ? message.status : "streaming",
      toolName: payload.toolName ?? message.toolName,
      metadata: {
        ...(message.metadata ?? {}),
        kind: message.metadata?.kind === "tool_activity" ? "tool_activity" : "tool_use",
        toolUseId: payload.toolUseId,
        parentToolUseId: payload.parentToolUseId ?? message.metadata?.parentToolUseId,
        ...toolPresentationMetadataFromPayload(payload, message.metadata),
        ...(inputText !== undefined ? { input: payload.input, rawInputText: inputText } : {}),
        ...(displayContent ? { inputText: displayContent } : {}),
      },
    };
    return next;
  }

  const displayContent = toolDisplayTitle(payload);
  return [
    ...next,
    {
      id: messageId,
      sessionId: payload.sessionId,
      taskId: payload.taskId ?? "pending",
      role: "assistant",
      content: displayContent,
      createdAt: payload.now,
      updatedAt: payload.now,
      streaming: true,
      placeholder: false,
      status: "streaming",
      toolName: payload.toolName ?? undefined,
      metadata: {
        kind: "tool_use",
        toolUseId: payload.toolUseId,
        parentToolUseId: payload.parentToolUseId ?? undefined,
        ...toolPresentationMetadataFromPayload(payload),
        ...(inputText !== undefined ? { input: payload.input, rawInputText: inputText } : {}),
        ...(displayContent ? { inputText: displayContent } : {}),
      },
    },
  ];
}

export function completeAssistantToolUseMessage(
  current: ChatMessageView[],
  payload: ToolPresentationPayload & {
    toolUseId: string;
    toolName: string;
    input: unknown;
    parentToolUseId?: string | null;
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
  const existingMetadata = index >= 0 ? next[index].metadata : undefined;
  const content = toolDisplayTitle(payload, existingMetadata) || inputText || payload.toolName || "工具调用";
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
              parentToolUseId: payload.parentToolUseId ?? message.metadata?.parentToolUseId,
              ...toolPresentationMetadataFromPayload(payload, message.metadata),
              input: payload.input,
              inputText: content,
              rawInputText: inputText,
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
        parentToolUseId: payload.parentToolUseId ?? undefined,
        ...toolPresentationMetadataFromPayload(payload),
        input: payload.input,
        inputText: content,
        rawInputText: inputText,
      },
    },
  ];
}

export function appendAssistantToolResultMessage(
  current: ChatMessageView[],
  payload: ToolPresentationPayload & {
    toolUseId: string;
    toolName?: string | null;
    parentToolUseId?: string | null;
    content: unknown;
    isError?: boolean;
    resultSummary?: string | null;
    resultPreview?: Array<{ label: string; value: string }> | null;
    filesChanged?: number | null;
    changedPaths?: string[] | null;
    diffText?: string | null;
    durationMs?: number | null;
    status?: MessageStatus;
    lifecycleStatus?: string | null;
    sessionId: string;
    taskId?: string | null;
    now: number;
  },
): ChatMessageView[] {
  const messageId = `tool_result:${payload.toolUseId}`;
  const resultPreview = normalizePreviewRows(payload.resultPreview);
  const terminalStatus = payload.status ?? (payload.isError ? "failed" : "completed");
  const toolUseIndex = current.findIndex(
    (message) =>
      (message.metadata?.kind === "tool_use" || message.metadata?.kind === "tool_activity") &&
      message.metadata?.toolUseId === payload.toolUseId,
  );
  if (toolUseIndex >= 0) {
    const next = [...current];
    const message = next[toolUseIndex];
    const content = compactDisplayText(payload.displaySummary) ||
      compactDisplayText(payload.resultSummary) ||
      formatToolResultSummary(payload.content, payload.isError);
    const structuredResult = structuredToolResultDisplay(payload.content, content, resultPreview, {
      changedPaths: payload.changedPaths,
      filesChanged: payload.filesChanged,
      diffText: payload.diffText,
    });
    const inputContent =
      typeof message.metadata?.inputText === "string"
        ? message.metadata.inputText
        : message.content;
    const existingOutputText = formatToolOutputText(message.metadata?.output);
    const visibleContent = payload.isError ? content || inputContent : inputContent || content;
    next[toolUseIndex] = {
      ...message,
      id: `tool_activity:${payload.toolUseId}`,
      taskId: payload.taskId ?? message.taskId,
      content: visibleContent,
      updatedAt: payload.now,
      streaming: false,
      placeholder: false,
      status: terminalStatus,
      toolName: payload.toolName ?? message.toolName,
      metadata: {
        ...(message.metadata ?? {}),
        kind: "tool_activity",
        toolUseId: payload.toolUseId,
        parentToolUseId: payload.parentToolUseId ?? message.metadata?.parentToolUseId,
        ...toolPresentationMetadataFromPayload(payload, message.metadata),
        inputText: inputContent,
        resultText: existingOutputText ? `${existingOutputText}\n\n${structuredResult.resultText}` : structuredResult.resultText,
        target: payload.target ?? message.metadata?.target,
        inputSummary: payload.inputSummary ?? message.metadata?.inputSummary,
        resultSummary: payload.resultSummary ?? undefined,
        resultPreview: structuredResult.previewRows ?? message.metadata?.resultPreview,
        changedPaths: structuredResult.changedPaths ?? message.metadata?.changedPaths,
        filesChanged: structuredResult.filesChanged ?? message.metadata?.filesChanged,
        diffText: structuredResult.diffText ?? message.metadata?.diffText,
        output: message.metadata?.output,
        input: message.metadata?.input,
        status: payload.lifecycleStatus ?? message.metadata?.status,
        rawContent: payload.content,
        isError: Boolean(payload.isError),
      },
    };
    return next.filter((message, index) => index === toolUseIndex || message.id !== messageId);
  }
  const existingIndex = current.findIndex((message) => message.id === messageId);
  const existingMetadata = existingIndex >= 0 ? current[existingIndex].metadata : undefined;
  const resultText = compactDisplayText(payload.displaySummary) ||
    compactDisplayText(payload.resultSummary) ||
    formatToolResultSummary(payload.content, payload.isError);
  const structuredResult = structuredToolResultDisplay(payload.content, resultText, resultPreview, {
    changedPaths: payload.changedPaths,
    filesChanged: payload.filesChanged,
    diffText: payload.diffText,
  });
  const content = payload.isError
    ? structuredResult.resultText || toolDisplaySummary(payload, existingMetadata)
    : toolDisplaySummary(payload, existingMetadata) || structuredResult.resultText;
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
    status: terminalStatus,
    toolName: payload.toolName ?? undefined,
    metadata: {
      kind: "tool_result",
      toolUseId: payload.toolUseId,
      parentToolUseId: payload.parentToolUseId ?? undefined,
      ...toolPresentationMetadataFromPayload(payload, existingMetadata),
      target: payload.target ?? undefined,
      inputSummary: payload.inputSummary ?? undefined,
      resultSummary: payload.resultSummary ?? undefined,
      resultText: structuredResult.resultText,
      resultPreview: structuredResult.previewRows,
      changedPaths: structuredResult.changedPaths,
      filesChanged: structuredResult.filesChanged,
      diffText: structuredResult.diffText,
      durationMs: payload.durationMs,
      status: payload.lifecycleStatus ?? undefined,
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

function isAssistantThinkingMessage(message: ChatMessageView): boolean {
  return message.metadata?.kind === "assistant_thinking";
}

function isSameTaskMessage(message: ChatMessageView, sessionId: string, taskId: string): boolean {
  return message.sessionId === sessionId && message.taskId === taskId;
}

function latestTaskMessageIndex(current: ChatMessageView[], sessionId: string, taskId: string): number {
  for (let index = current.length - 1; index >= 0; index -= 1) {
    if (isSameTaskMessage(current[index], sessionId, taskId)) {
      return index;
    }
  }
  return -1;
}

function latestTaskThinkingIndex(current: ChatMessageView[], sessionId: string, taskId: string): number {
  for (let index = current.length - 1; index >= 0; index -= 1) {
    const message = current[index];
    if (isSameTaskMessage(message, sessionId, taskId) && isAssistantThinkingMessage(message)) {
      return index;
    }
  }
  return -1;
}

function assistantThinkingSegmentId(current: ChatMessageView[], taskId: string): string {
  const baseId = `assistant_thinking:${taskId}`;
  let segment = current.filter((message) => message.id === baseId || message.id.startsWith(`${baseId}:`)).length;
  let candidate = segment === 0 ? baseId : `${baseId}:${segment}`;
  while (current.some((message) => message.id === candidate)) {
    segment += 1;
    candidate = `${baseId}:${segment}`;
  }
  return candidate;
}

function isIncomingTransientThinking(
  payload: { state?: string | null; text?: string | null; source?: string | null; transient?: boolean | null },
  content: string,
): boolean {
  if (typeof payload.transient === "boolean") {
    return payload.transient;
  }
  if (typeof payload.source === "string" && payload.source.trim()) {
    return false;
  }
  const explicitText = Boolean(payload.text?.trim());
  if (!explicitText) {
    return true;
  }
  if (payload.state && payload.state !== "thinking") {
    return true;
  }
  return content.trim() === chatStatusLabel(payload.state ?? undefined).trim();
}

function isTransientAssistantThinkingMessage(message: ChatMessageView): boolean {
  if (!isAssistantThinkingMessage(message)) {
    return false;
  }
  if (typeof message.metadata?.transient === "boolean") {
    return message.metadata.transient;
  }
  if (typeof message.metadata?.source === "string" && message.metadata.source.trim()) {
    return false;
  }
  const state = typeof message.metadata?.state === "string" ? message.metadata.state : undefined;
  const verb = typeof message.metadata?.verb === "string" ? message.metadata.verb : undefined;
  const content = message.content.trim();
  if (!content) {
    return true;
  }
  if (state && state !== "thinking") {
    return true;
  }
  return content === chatStatusLabel(state, verb).trim();
}

function canUpdateThinkingSegment(
  existing: ChatMessageView,
  payload: { state?: string | null; transient?: boolean | null },
  incomingTransient: boolean,
): boolean {
  if (!isAssistantThinkingMessage(existing)) {
    return false;
  }
  if (isTransientAssistantThinkingMessage(existing) !== incomingTransient) {
    return false;
  }
  if (incomingTransient) {
    return true;
  }
  const existingState = typeof existing.metadata?.state === "string" ? existing.metadata.state : undefined;
  const incomingState = payload.state ?? undefined;
  return !existingState || !incomingState || existingState === incomingState;
}

export function appendOrUpdateAssistantThinkingMessage(
  current: ChatMessageView[],
  payload: {
    sessionId: string;
    taskId?: string | null;
    eventId?: string | null;
    state?: string | null;
    verb?: string | null;
    text?: string | null;
    source?: string | null;
    transient?: boolean | null;
    now: number;
  },
): ChatMessageView[] {
  const taskId = payload.taskId ?? "pending";
  const content = payload.text && payload.text.trim() ? payload.text : chatStatusLabel(payload.state ?? undefined, payload.verb);
  const incomingTransient = isIncomingTransientThinking(payload, content);
  const stableEventId = payload.eventId?.trim() ?? "";
  const source = typeof payload.source === "string" ? payload.source.trim() : "";
  const providerSummaryThinking = source === "provider_reasoning_summary" || source === "non_stream_thought_summary";
  const eventMessageId = stableEventId && !providerSummaryThinking ? `assistant_thinking:${stableEventId}` : "";
  const existingEventIndex = stableEventId && !providerSummaryThinking
    ? current.findIndex(
        (message) =>
          message.sessionId === payload.sessionId &&
          message.taskId === taskId &&
          message.metadata?.kind === "assistant_thinking" &&
          (message.id === eventMessageId || message.metadata?.eventId === stableEventId),
      )
    : -1;
  const latestThinkingIndex = latestTaskThinkingIndex(current, payload.sessionId, taskId);
  const latestTaskIndex = latestTaskMessageIndex(current, payload.sessionId, taskId);
  const existingIndex =
    existingEventIndex >= 0
      ? existingEventIndex
      : latestThinkingIndex >= 0 &&
    latestThinkingIndex === latestTaskIndex &&
    (providerSummaryThinking || canUpdateThinkingSegment(current[latestThinkingIndex], payload, incomingTransient))
      ? latestThinkingIndex
      : -1;
  const existing = existingIndex >= 0 ? current[existingIndex] : undefined;
  const messageId = existing?.id ?? (eventMessageId || assistantThinkingSegmentId(current, taskId));
  const nextContent =
    providerSummaryThinking &&
    existing &&
    existing.content.trim() &&
    existing.content.trim() !== content.trim()
      ? appendAssistantContentDelta(existing.content, `\n\n${content}`)
      : content;
  const nextMessage: ChatMessageView = {
    id: messageId,
    sessionId: payload.sessionId,
    taskId,
    role: "assistant",
    content: nextContent,
    createdAt: existing ? existing.createdAt : payload.now,
    updatedAt: payload.now,
    streaming: true,
    placeholder: false,
    status: "streaming",
    metadata: {
      ...(existing?.metadata ?? {}),
      kind: "assistant_thinking",
      state: payload.state,
      verb: payload.verb,
      eventId: stableEventId || (existing?.metadata?.eventId ?? undefined),
      source: payload.source ?? existing?.metadata?.source ?? undefined,
      transient: incomingTransient,
    },
  };

  if (existingIndex >= 0 && existing) {
    const next = [...current];
    const incomingStatusContent = chatStatusLabel(payload.state ?? undefined, payload.verb);
    const existingStatusContent = chatStatusLabel(
      typeof existing.metadata?.state === "string" ? existing.metadata.state : payload.state ?? undefined,
      typeof existing.metadata?.verb === "string" ? existing.metadata.verb : payload.verb,
    );
    const shouldAppendText =
      Boolean(payload.text?.trim()) &&
      !providerSummaryThinking &&
      existing.metadata?.state === "thinking" &&
      payload.state === "thinking" &&
      !incomingTransient &&
      !isTransientAssistantThinkingMessage(existing) &&
      existing.content !== incomingStatusContent &&
      existing.content !== existingStatusContent;
    next[existingIndex] = {
      ...nextMessage,
      content: shouldAppendText ? `${existing.content}${payload.text ?? ""}` : nextMessage.content,
    };
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
    metadata?: Record<string, unknown> | null;
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
        ...(payload.metadata ?? {}),
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
    now?: number;
  },
): ChatMessageView[] {
  const next: ChatMessageView[] = [];
  let changed = false;
  for (const message of current) {
    if (message.metadata?.kind !== "assistant_thinking") {
      next.push(message);
      continue;
    }
    if (message.sessionId !== payload.sessionId) {
      next.push(message);
      continue;
    }
    if (payload.taskId && message.taskId !== payload.taskId) {
      next.push(message);
      continue;
    }
    if (isTransientAssistantThinkingMessage(message)) {
      changed = true;
      continue;
    }
    if (message.streaming || message.status === "streaming") {
      changed = true;
      next.push({
        ...message,
        updatedAt: payload.now ?? message.updatedAt,
        streaming: false,
        status: "completed",
        metadata: {
          ...(message.metadata ?? {}),
          transient: false,
        },
      });
      continue;
    }
    next.push(message);
  }
  return changed ? next : current;
}

export function closeAssistantThinkingForToolBoundary(
  current: ChatMessageView[],
  payload: {
    sessionId: string;
    taskId?: string | null;
    now?: number;
  },
): ChatMessageView[] {
  return removeAssistantThinkingMessage(current, payload);
}

export function appendOrUpdatePermissionRequestMessage(
  current: ChatMessageView[],
  payload: ToolPresentationPayload & {
    requestId: string;
    toolUseId?: string | null;
    toolName?: string | null;
    input: unknown;
    description?: string | null;
    preview?: Array<{ label: string; value: string }> | null;
    previewSections?: unknown[] | null;
    filesChanged?: number | null;
    changedPaths?: string[] | null;
    diffText?: string | null;
    sessionId: string;
    taskId?: string | null;
    now: number;
  },
): ChatMessageView[] {
  const messageId = `permission_request:${payload.requestId}`;
  const input = formatPermissionInputForChat(payload.toolName, payload.input);
  const content = [
    compactDisplayText(payload.displayTitle) || compactDisplayText(payload.description),
    compactDisplayText(payload.displaySummary),
    input,
  ].filter(Boolean).join("\n\n");
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
      toolUseId: payload.toolUseId ?? undefined,
      input: payload.input,
      approvalKind: payload.toolName ?? undefined,
      ...toolPresentationMetadataFromPayload(payload),
      previewRows: payload.preview ?? undefined,
      previewSections: Array.isArray(payload.previewSections) ? payload.previewSections : undefined,
      filesChanged: typeof payload.filesChanged === "number" ? payload.filesChanged : undefined,
      changedPaths: Array.isArray(payload.changedPaths) ? payload.changedPaths : undefined,
      diffText: typeof payload.diffText === "string" ? payload.diffText : undefined,
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
  payload: ToolPresentationPayload & {
    requestId: string;
    decision: "approved" | "rejected" | string;
    toolUseId?: string | null;
    input?: unknown;
    toolName?: string | null;
    preview?: Array<{ label: string; value: string }> | null;
    previewSections?: unknown[] | null;
    filesChanged?: number | null;
    changedPaths?: string[] | null;
    diffText?: string | null;
    sessionId?: string | null;
    taskId?: string | null;
    createIfMissing?: boolean;
    now: number;
  },
): ChatMessageView[] {
  const messageId = `permission_request:${payload.requestId}`;
  const existingIndex = current.findIndex((message) => message.id === messageId);
  if (existingIndex < 0) {
    const hasDetails =
      payload.input !== undefined ||
      Boolean(payload.preview?.length) ||
      Boolean(payload.previewSections?.length) ||
      typeof payload.filesChanged === "number" ||
      Boolean(payload.changedPaths?.length) ||
      typeof payload.diffText === "string";
    if (!payload.createIfMissing || !payload.sessionId || !hasDetails) {
      return current;
    }
    const input = formatPermissionInputForChat(payload.toolName, payload.input);
    const content = compactDisplayText(payload.displayTitle) ||
      compactDisplayText(payload.displaySummary) ||
      input ||
      (payload.decision === "rejected" ? "此操作已被拒绝。" : "此操作已被允许。");
    return [
      ...current,
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
        status: payload.decision === "rejected" ? "failed" : "completed",
        toolName: payload.toolName ?? undefined,
        metadata: {
          kind: "permission_request",
          requestId: payload.requestId,
          toolUseId: payload.toolUseId ?? undefined,
          decision: payload.decision,
          resolved: true,
          ...toolPresentationMetadataFromPayload(payload),
          ...(payload.input !== undefined ? { input: payload.input } : {}),
          ...(payload.toolName ? { approvalKind: payload.toolName } : {}),
          ...(payload.preview ? { previewRows: payload.preview } : {}),
          ...(payload.previewSections ? { previewSections: payload.previewSections } : {}),
          ...(typeof payload.filesChanged === "number" ? { filesChanged: payload.filesChanged } : {}),
          ...(Array.isArray(payload.changedPaths) ? { changedPaths: payload.changedPaths } : {}),
          ...(typeof payload.diffText === "string" ? { diffText: payload.diffText } : {}),
        },
      },
    ];
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
      toolUseId: payload.toolUseId ?? message.metadata?.toolUseId,
      decision: payload.decision,
      resolved: true,
      ...toolPresentationMetadataFromPayload(payload, message.metadata),
      ...(payload.input !== undefined ? { input: payload.input } : {}),
      ...(payload.toolName ? { approvalKind: payload.toolName } : {}),
      ...(payload.preview ? { previewRows: payload.preview } : {}),
      ...(payload.previewSections ? { previewSections: payload.previewSections } : {}),
      ...(typeof payload.filesChanged === "number" ? { filesChanged: payload.filesChanged } : {}),
      ...(Array.isArray(payload.changedPaths) ? { changedPaths: payload.changedPaths } : {}),
      ...(typeof payload.diffText === "string" ? { diffText: payload.diffText } : {}),
    },
  };
  return next;
}

export function resolveSpecialApprovalMessage(
  current: ChatMessageView[],
  payload: {
    approvalId: string;
    decision: "approved" | "rejected" | string;
    input?: unknown;
    preview?: Array<{ label: string; value: string }> | null;
    previewSections?: unknown[] | null;
    filesChanged?: number | null;
    changedPaths?: string[] | null;
    diffText?: string | null;
    now: number;
  },
): ChatMessageView[] {
  let changed = false;
  const request = payload.input && typeof payload.input === "object" && !Array.isArray(payload.input)
    ? payload.input as Record<string, unknown>
    : {};
  const next = current.map((message) => {
    const metadata = message.metadata ?? {};
    const messageApprovalId =
      typeof metadata.approvalId === "string"
        ? metadata.approvalId.trim()
        : typeof metadata.requestId === "string"
          ? metadata.requestId.trim()
          : "";
    if (!messageApprovalId || messageApprovalId !== payload.approvalId) {
      return message;
    }
    const kind = typeof metadata.kind === "string" ? metadata.kind : "";
    if (kind !== "computer_use_permission" && kind !== "computer_use_permission_request") {
      return message;
    }
    changed = true;
    return {
      ...message,
      updatedAt: payload.now,
      status: payload.decision === "rejected" ? "failed" as const : "completed" as const,
      metadata: {
        ...metadata,
        ...request,
        kind: "computer_use_permission",
        status: payload.decision,
        decision: payload.decision,
        resolved: true,
        ...(payload.input !== undefined ? { request: payload.input } : {}),
        ...(payload.preview ? { previewRows: payload.preview } : {}),
        ...(payload.previewSections ? { previewSections: payload.previewSections } : {}),
        ...(typeof payload.filesChanged === "number" ? { filesChanged: payload.filesChanged } : {}),
        ...(Array.isArray(payload.changedPaths) ? { changedPaths: payload.changedPaths } : {}),
        ...(typeof payload.diffText === "string" ? { diffText: payload.diffText } : {}),
      },
    };
  });
  return changed ? next : current;
}

export function appendSpecialEventMessage(
  current: ChatMessageView[],
  payload: {
    kind: string;
    sessionId: string;
    taskId?: string | null;
    content?: string | null;
    title?: string | null;
    summary?: string | null;
    status?: string | null;
    eventId?: string | null;
    metadata?: Record<string, unknown> | null;
    now: number;
  },
): ChatMessageView[] {
  const normalizedKind = payload.kind.trim() || "system";
  const taskId = payload.taskId ?? "pending";
  const approvalId =
    typeof payload.metadata?.approvalId === "string" && payload.metadata.approvalId.trim()
      ? payload.metadata.approvalId.trim()
      : typeof payload.metadata?.requestId === "string" && payload.metadata.requestId.trim()
        ? payload.metadata.requestId.trim()
        : "";
  const askUserStableKey =
    normalizedKind === "ask_user_question"
      ? typeof payload.metadata?.requestId === "string" && payload.metadata.requestId.trim()
        ? payload.metadata.requestId.trim()
        : typeof payload.metadata?.toolCallId === "string" && payload.metadata.toolCallId.trim()
          ? payload.metadata.toolCallId.trim()
          : ""
      : "";
  const stableKey = normalizedKind === "computer_use_permission" && approvalId ? approvalId : askUserStableKey;
  const eventKey = stableKey || payload.eventId?.trim() || `${taskId}:${payload.now}`;
  const messageId = `${normalizedKind}:${eventKey}`;
  const existingIndex = current.findIndex((message) => message.id === messageId);
  const existingMessage = existingIndex >= 0 ? current[existingIndex] : undefined;
  const mergeExisting = Boolean(stableKey && existingMessage);
  const metadataSource = payload.metadata ?? {};
  const contentCandidates =
    normalizedKind === "ask_user_question"
      ? [payload.summary, payload.title, payload.content]
      : [payload.summary, payload.content, payload.title];
  const content = contentCandidates
    .map((value) => value?.trim() ?? "")
    .find(Boolean) ?? (mergeExisting ? existingMessage?.content ?? "" : "");
  const messageStatus = messageStatusFromSpecialEventStatus(payload.status);
  const nextMessage: ChatMessageView = {
    id: messageId,
    sessionId: payload.sessionId,
    taskId,
    role: "assistant",
    content,
    createdAt: existingMessage?.createdAt ?? payload.now,
    updatedAt: payload.now,
    streaming: false,
    placeholder: false,
    status: messageStatus,
    metadata: {
      ...(mergeExisting ? existingMessage?.metadata ?? {} : {}),
      ...metadataSource,
      kind: normalizedKind,
      title: payload.title ?? (mergeExisting ? existingMessage?.metadata?.title : undefined),
      summary: payload.summary ?? (mergeExisting ? existingMessage?.metadata?.summary : undefined),
      status: payload.status ?? (mergeExisting ? existingMessage?.metadata?.status : undefined),
    },
  };

  if (existingIndex >= 0) {
    const next = [...current];
    next[existingIndex] = nextMessage;
    return next;
  }
  return [...current, nextMessage];
}

function messageStatusFromSpecialEventStatus(status?: string | null): MessageStatus {
  const normalized = status?.trim().toLowerCase() ?? "";
  if (["running", "started", "planning", "verifying", "pending", "queued", "waiting", "waiting_approval", "active", "in_progress"].includes(normalized)) {
    return "streaming";
  }
  if (["blocked"].includes(normalized)) {
    return "blocked";
  }
  if (["cancelled", "canceled"].includes(normalized)) {
    return "cancelled";
  }
  if (["failed", "error", "rejected"].includes(normalized)) {
    return "failed";
  }
  return "completed";
}

export function resolveAskUserQuestionMessage(
  current: ChatMessageView[],
  payload: {
    messageId?: string | null;
    requestId?: string | null;
    toolCallId?: string | null;
    answer: string;
    now: number;
  },
): ChatMessageView[] {
  let changed = false;
  const messageId = payload.messageId?.trim() ?? "";
  const requestId = payload.requestId?.trim() ?? "";
  const toolCallId = payload.toolCallId?.trim() ?? "";
  const next = current.map((message) => {
    if (message.metadata?.kind !== "ask_user_question") {
      return message;
    }
    const metadataRequestId = typeof message.metadata?.requestId === "string" ? message.metadata.requestId.trim() : "";
    const metadataToolCallId = typeof message.metadata?.toolCallId === "string" ? message.metadata.toolCallId.trim() : "";
    const matches =
      (messageId && message.id === messageId) ||
      (requestId && metadataRequestId === requestId) ||
      (toolCallId && metadataToolCallId === toolCallId);
    if (!matches) {
      return message;
    }
    changed = true;
    return {
      ...message,
      updatedAt: payload.now,
      status: "completed" as const,
      metadata: {
        ...(message.metadata ?? {}),
        status: "answered",
        resolved: true,
        answered: true,
        answer: payload.answer,
        answeredAt: payload.now,
      },
    };
  });
  return changed ? next : current;
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
  if (hasAssistantContentBlocksForMessage(current, {
    sessionId: payload.sessionId,
    taskId: payload.taskId,
    messageId: payload.messageId,
  })) {
    return current.map((message) =>
      message.sessionId === payload.sessionId &&
      message.role === "assistant" &&
      isAssistantContentBlockMessage(message) &&
      (!payload.taskId || message.taskId === payload.taskId) &&
      (!payload.messageId || message.metadata?.baseMessageId === payload.messageId || message.id === payload.messageId)
        ? {
            ...message,
            streaming: false,
            placeholder: false,
            status: message.status === "failed" ? message.status : ("completed" as const),
            updatedAt: payload.now,
          }
        : message,
    );
  }
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
  if (hasAssistantContentBlocksForMessage(current, {
    sessionId: payload.sessionId,
    taskId: payload.taskId,
    messageId: payload.messageId,
  })) {
    return current.map((message) =>
      message.sessionId === payload.sessionId &&
      message.role === "assistant" &&
      isAssistantContentBlockMessage(message) &&
      (!payload.taskId || message.taskId === payload.taskId) &&
      (!payload.messageId || message.metadata?.baseMessageId === payload.messageId || message.id === payload.messageId)
        ? {
            ...message,
            streaming: false,
            placeholder: false,
            status: message.status === "failed" ? message.status : ("completed" as const),
            updatedAt: payload.now,
          }
        : message,
    );
  }
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
      const textMessage = asAssistantTextMessage(msg);
      return {
        ...textMessage,
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
    if (!isAssistantTextStreamTarget(message)) {
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
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  const structuredSummary = summarizePublicChatObject(value);
  if (structuredSummary) {
    return structuredSummary;
  }
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function isPlainRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value && typeof value === "object" && !Array.isArray(value));
}

function summarizePublicChatObject(value: unknown): string {
  if (Array.isArray(value)) {
    const items = value
      .map((item) => summarizePublicChatObject(item) || scalarPreview(item))
      .filter(Boolean)
      .slice(0, 4);
    const suffix = value.length > items.length ? `，另有 ${value.length - items.length} 项` : "";
    return items.length ? `${items.join("；")}${suffix}` : "";
  }
  if (!isPlainRecord(value)) {
    return "";
  }

  const summary = readPublicRecordText(value, ["summary", "resultSummary", "message", "description"]);
  const status = readPublicRecordText(value, ["status", "state", "approvalStatus"]);
  const path = readPublicRecordText(value, ["path", "file", "target", "url"]);
  const command = readPublicRecordText(value, ["command", "cmd"]);
  const query = readPublicRecordText(value, ["query"]);
  const changedPaths = readPublicRecordStringList(value, ["changedPaths", "paths", "files", "targets"]);
  const filesChanged = readPublicRecordNumber(value, ["filesChanged", "fileCount"]);
  const exitCode = readPublicRecordNumber(value, ["exitCode", "exit_code"]);
  const durationMs = readPublicRecordNumber(value, ["durationMs", "duration_ms"]);
  const contentChars =
    readPublicRecordNumber(value, ["contentChars", "chars"]) ??
    readOmittedChars(value.content) ??
    readOmittedChars(value.patch);
  const patchChars = readPublicRecordNumber(value, ["patchChars"]);
  const preview = summarizePreviewRows(value.preview) || summarizePreviewRows(value.resultPreview);

  const parts: string[] = [];
  if (summary) parts.push(summary);
  if (!summary && status) parts.push(status);
  if (command && !summary?.includes(command)) parts.push(command);
  if (query && !summary?.includes(query)) parts.push(`查询 ${query}`);
  if (path && !summary?.includes(path)) parts.push(path);
  if (changedPaths.length) {
    const listed = changedPaths.slice(0, 3).join(", ");
    const suffix = changedPaths.length > 3 ? ` +${changedPaths.length - 3}` : "";
    parts.push(`涉及 ${listed}${suffix}`);
  } else if (typeof filesChanged === "number") {
    parts.push(`${filesChanged} 个文件`);
  }
  if (typeof contentChars === "number") parts.push(`内容 ${contentChars} 字符`);
  if (typeof patchChars === "number") parts.push(`补丁 ${patchChars} 字符`);
  if (typeof exitCode === "number") parts.push(`退出码 ${exitCode}`);
  if (typeof durationMs === "number") parts.push(`${durationMs}ms`);
  if (preview) parts.push(preview);

  if (parts.length) {
    return parts.filter(Boolean).slice(0, 6).join(" · ");
  }

  const simplePairs = Object.entries(value)
    .filter(([key, item]) => !isInternalDisplayKey(key) && item !== null && item !== undefined && item !== "" && !isPlainRecord(item) && !Array.isArray(item))
    .slice(0, 4)
    .map(([key, item]) => `${key}: ${scalarPreview(item)}`)
    .filter(Boolean);
  return simplePairs.join(" · ");
}

function isInternalDisplayKey(key: string): boolean {
  return /^(sessionId|taskId|eventId|workspaceRoot|originalWorkspaceRoot|requestJson|diffText|patchText|rawContent|providerRequest|authorization)$/i.test(key);
}

function readPublicRecordText(record: Record<string, unknown>, keys: string[]): string {
  for (const key of keys) {
    const value = record[key];
    if (typeof value === "string" && value.trim()) return value.trim();
    if (typeof value === "number" && Number.isFinite(value)) return String(value);
  }
  return "";
}

function readPublicRecordNumber(record: Record<string, unknown>, keys: string[]): number | undefined {
  for (const key of keys) {
    const value = record[key];
    if (typeof value === "number" && Number.isFinite(value)) return value;
  }
  return undefined;
}

function readPublicRecordStringList(record: Record<string, unknown>, keys: string[]): string[] {
  const values: string[] = [];
  const visit = (item: unknown, depth = 0) => {
    if (depth > 2 || values.length >= 12) return;
    if (typeof item === "string" && item.trim()) {
      values.push(item.trim());
      return;
    }
    if (Array.isArray(item)) {
      item.forEach((entry) => visit(entry, depth + 1));
      return;
    }
    if (isPlainRecord(item)) {
      const direct = readPublicRecordText(item, ["path", "file", "name", "target", "url"]);
      if (direct) values.push(direct);
    }
  };
  keys.forEach((key) => visit(record[key]));
  return Array.from(new Set(values.filter(Boolean)));
}

function readOmittedChars(value: unknown): number | undefined {
  if (!isPlainRecord(value) || value.omitted !== true) return undefined;
  const chars = value.chars;
  return typeof chars === "number" && Number.isFinite(chars) ? chars : undefined;
}

function summarizePreviewRows(value: unknown): string {
  if (!Array.isArray(value)) return "";
  const rows = value
    .map((row) => {
      if (!isPlainRecord(row)) return "";
      const label = readPublicRecordText(row, ["label", "title", "name"]);
      const rowValue = scalarPreview(row.value ?? row.summary ?? row.text);
      return label && rowValue ? `${label}: ${rowValue}` : rowValue || label;
    })
    .filter(Boolean)
    .slice(0, 3);
  return rows.join(" · ");
}

function scalarPreview(value: unknown): string {
  if (typeof value === "string") return value.trim().replace(/\s+/g, " ").slice(0, 160);
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (isPlainRecord(value)) return summarizePublicChatObject(value);
  return "";
}

function isPlanPermissionInput(value: unknown): value is Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return false;
  }
  const record = value as Record<string, unknown>;
  return (
    typeof record.goal === "string" &&
    (Array.isArray(record.subtasks) ||
      Array.isArray(record.executionOrder) ||
      Array.isArray(record.execution_order) ||
      Array.isArray(record.previewSections))
  );
}

function summarizePlanPermissionInput(value: Record<string, unknown>): string {
  const goal = typeof value.goal === "string" ? value.goal.trim() : "";
  const mode = typeof value.orchestrationMode === "string"
    ? value.orchestrationMode.trim()
    : typeof value.mode === "string"
      ? value.mode.trim()
      : "plan";
  const subtaskCount = typeof value.subtaskCount === "number"
    ? value.subtaskCount
    : Array.isArray(value.subtasks)
      ? value.subtasks.length
      : undefined;
  const parts = [
    subtaskCount !== undefined ? `Plan ready: ${subtaskCount} subtask${subtaskCount === 1 ? "" : "s"}` : "Plan ready",
    mode ? `mode ${mode}` : "",
    goal ? `goal ${goal}` : "",
  ].filter(Boolean);
  return parts.join(" | ");
}

function formatPermissionInputForChat(toolName: string | null | undefined, value: unknown): string {
  if (String(toolName ?? "").toLowerCase() === "plan" && isPlanPermissionInput(value)) {
    return summarizePlanPermissionInput(value);
  }
  return formatChatBlockValue(value);
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
    if (commandStatus === "cancelled") return progressLine("命令已取消。");
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
  if (isEphemeralChatBlockMessage(message)) {
    return false;
  }
  return (
    message.role === "assistant" &&
    (message.streaming === true || message.status === "streaming") &&
    message.placeholder !== true &&
    !message.content.trim()
  );
}

function isRuntimeProgressOnlyAssistantMessage(message: ChatMessageView): boolean {
  if (
    isEphemeralChatBlockMessage(message)
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
