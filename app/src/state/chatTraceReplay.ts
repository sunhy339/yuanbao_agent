import type {
  AgentEventEnvelope,
  AssistantProgressPayload,
  ChatStatusPayload,
  CommandLifecyclePayload,
  CommandOutputPayload,
  ContentDeltaPayload,
  ContentStartPayload,
  PermissionRequestPayload,
  ToolLifecyclePayload,
  ToolOutputPayload,
  ToolResultPayload,
  ToolUseCompletePayload,
  TraceEventRecord,
} from "@shared";
import {
  appendAssistantProgressMessage,
  appendAssistantToolResultMessage,
  appendOrUpdateAssistantThinkingMessage,
  appendOrUpdateAssistantToolInputDelta,
  appendOrUpdateAssistantToolOutputDelta,
  appendOrUpdateAssistantToolStartMessage,
  appendOrUpdatePermissionRequestMessage,
  appendSpecialEventMessage,
  completeAssistantToolUseMessage,
  removeAssistantThinkingMessage,
  resolveAskUserQuestionMessage,
  resolvePermissionRequestMessage,
  resolveSpecialApprovalMessage,
  stopStreamingMessagesForTask,
  summarizeOperationalAssistantDelta,
  type ChatMessageView,
} from "./chatMessages";

const SPECIAL_EVENT_TYPES = new Set([
  "api_retry",
  "system_notification",
  "compact_summary",
  "goal_event",
  "memory_event",
  "background_task",
  "task_summary",
  "plan_update",
  "ask_user_question",
  "computer_use_permission_request",
  "computer_use_permission",
]);

const EMPTY_CHILD_TASK_IDS = new Set<string>();

function envelopeFromTrace(trace: TraceEventRecord): AgentEventEnvelope {
  return {
    eventId: trace.id,
    sessionId: trace.sessionId,
    taskId: trace.taskId,
    type: trace.type as AgentEventEnvelope["type"],
    ts: trace.createdAt,
    seq: trace.sequence,
    payload: trace.payload,
    visibility: trace.visibility,
    yuanbao: trace.yuanbao,
    hahaCc: trace.hahaCc,
  };
}

function readPayloadText(payload: unknown, keys: string[]): string {
  if (!payload || typeof payload !== "object") return "";
  const record = payload as Record<string, unknown>;
  for (const key of keys) {
    const value = record[key];
    if (typeof value === "string" && value.trim()) return value.trim();
    if (typeof value === "number" || typeof value === "boolean") return String(value);
  }
  return "";
}

function readPayloadChunk(payload: unknown, keys: string[]): string {
  if (!payload || typeof payload !== "object") return "";
  const record = payload as Record<string, unknown>;
  for (const key of keys) {
    const value = record[key];
    if (typeof value === "string" && value) return value;
    if (typeof value === "number" || typeof value === "boolean") return String(value);
  }
  return "";
}

function looksLikeInternalDisplayText(value: string): boolean {
  const text = value.trim();
  if (!text) return true;
  const lines = text.split(/\r?\n/).filter((line) => line.trim());
  if (
    lines.length >= 3 &&
    /(^|\n)\s*(_chatCompat|activeStep|currentStep|completedSteps|fingerprint|tool_results|workspaceRoot|sessionId|taskId|eventId|payload|metadata)\s*[:=]/.test(text)
  ) {
    return true;
  }
  if (/^[{\[]/.test(text)) {
    try {
      const parsed = JSON.parse(text);
      if (parsed && typeof parsed === "object") {
        const keys = Object.keys(parsed as Record<string, unknown>);
        return keys.some((key) => [
          "_chatCompat",
          "context",
          "eventId",
          "messages",
          "metadata",
          "options",
          "payload",
          "questions",
          "requestId",
          "sessionId",
          "taskId",
          "toolCallId",
          "tool_results",
          "workspaceRoot",
        ].includes(key));
      }
    } catch {
      return true;
    }
  }
  return false;
}

function safePayloadDisplayText(value: string): string {
  const text = value.trim();
  if (!text || looksLikeInternalDisplayText(text)) return "";
  return text;
}

function isChatCompatPayload(payload: unknown): boolean {
  return Boolean(payload && typeof payload === "object" && (payload as { _chatCompat?: unknown })._chatCompat === true);
}

function isReplayChatVisibleEvent(
  event: AgentEventEnvelope,
  childTaskIds: ReadonlySet<string> = EMPTY_CHILD_TASK_IDS,
): boolean {
  if (event.visibility === "chat") return true;
  if (event.visibility === "panel" || event.visibility === "trace") return false;
  return !childTaskIds.has(event.taskId);
}

function replayMarker(event: AgentEventEnvelope, part: string): string {
  return `${event.eventId}:${part}`;
}

function messageHasReplayMarker(message: ChatMessageView, toolUseId: string, marker: string): boolean {
  if (message.metadata?.toolUseId !== toolUseId) return false;
  const markers = message.metadata._traceReplayMarkers;
  return Array.isArray(markers) && markers.includes(marker);
}

function hasToolReplayMarker(messages: ChatMessageView[], toolUseId: string, marker: string): boolean {
  return messages.some((message) => messageHasReplayMarker(message, toolUseId, marker));
}

function markToolReplayMarker(messages: ChatMessageView[], toolUseId: string, marker: string): ChatMessageView[] {
  let changed = false;
  const next = messages.map((message) => {
    if (message.metadata?.toolUseId !== toolUseId) return message;
    const markers = Array.isArray(message.metadata._traceReplayMarkers)
      ? message.metadata._traceReplayMarkers.filter((item): item is string => typeof item === "string")
      : [];
    if (markers.includes(marker)) return message;
    changed = true;
    return {
      ...message,
      metadata: {
        ...(message.metadata ?? {}),
        _traceReplayMarkers: [...markers, marker],
      },
    };
  });
  return changed ? next : messages;
}

function replaySpecialEvent(current: ChatMessageView[], event: AgentEventEnvelope, kind: string): ChatMessageView[] {
  const payload = event.payload as Record<string, unknown> | null | undefined;
  const title = safePayloadDisplayText(readPayloadText(payload, ["title", "label", "phase", "state"]));
  const summary = safePayloadDisplayText(readPayloadText(payload, ["summary", "description", "message", "detail", "reason", "errorMessage"]));
  const rawContent = kind === "slash_command"
    ? readPayloadText(payload, ["content", "text", "body"])
    : readPayloadText(payload, ["content", "text", "body", "error"]);
  const content = kind === "ask_user_question" || kind === "computer_use_permission"
    ? ""
    : safePayloadDisplayText(rawContent);
  const status = readPayloadText(payload, ["status"]);
  const withoutTransientThinking = kind === "ask_user_question" || kind === "computer_use_permission"
    ? removeAssistantThinkingMessage(current, {
        sessionId: event.sessionId,
        taskId: event.taskId,
        now: event.ts,
      })
    : current;
  return appendSpecialEventMessage(withoutTransientThinking, {
    kind,
    sessionId: event.sessionId,
    taskId: event.taskId,
    content,
    title,
    summary,
    status,
    eventId: event.eventId,
    metadata: payload && typeof payload === "object" ? payload : null,
    now: event.ts,
  });
}

function replayTraceEvent(
  current: ChatMessageView[],
  event: AgentEventEnvelope,
  childTaskIds: ReadonlySet<string>,
): ChatMessageView[] {
  if (!isReplayChatVisibleEvent(event, childTaskIds)) {
    return current;
  }

  if (event.type === "content_start") {
    const payload = event.payload as ContentStartPayload;
    if (payload.blockType === "tool_use" && payload.toolUseId) {
      return appendOrUpdateAssistantToolStartMessage(current, {
        toolUseId: payload.toolUseId,
        toolName: payload.toolName,
        target: payload.target,
        inputSummary: payload.inputSummary,
        parentToolUseId: payload.parentToolUseId,
        toolGroupId: payload.toolGroupId,
        toolIndex: payload.toolIndex,
        toolTotal: payload.toolTotal,
        toolOperationId: payload.toolOperationId,
        toolOperationLabel: payload.toolOperationLabel,
        toolCategory: payload.toolCategory,
        toolPhaseId: payload.toolPhaseId,
        toolPhaseLabel: payload.toolPhaseLabel,
        toolSemanticParentId: payload.toolSemanticParentId,
        toolSemanticParentLabel: payload.toolSemanticParentLabel,
        sessionId: event.sessionId,
        taskId: event.taskId,
        now: event.ts,
      });
    }
    return current;
  }

  if (event.type === "content_delta") {
    const payload = event.payload as ContentDeltaPayload;
    let next = current;
    if (typeof payload.text === "string" && payload.text) {
      const progressText = summarizeOperationalAssistantDelta(payload.text)?.trim();
      if (progressText) {
        next = appendAssistantProgressMessage(next, {
          sessionId: event.sessionId,
          taskId: event.taskId,
          content: progressText,
          now: event.ts,
          eventId: event.eventId,
        });
      }
    }
    if (typeof payload.toolInput === "string" && payload.toolInput) {
      const toolUseId = typeof payload.toolUseId === "string" && payload.toolUseId ? payload.toolUseId : `pending_${event.taskId}`;
      const marker = replayMarker(event, "toolInput");
      if (hasToolReplayMarker(next, toolUseId, marker)) {
        return next;
      }
      next = appendOrUpdateAssistantToolInputDelta(next, {
        toolUseId,
        toolName: payload.toolName,
        target: payload.target,
        inputSummary: payload.inputSummary,
        parentToolUseId: payload.parentToolUseId,
        toolGroupId: payload.toolGroupId,
        toolIndex: payload.toolIndex,
        toolTotal: payload.toolTotal,
        toolOperationId: payload.toolOperationId,
        toolOperationLabel: payload.toolOperationLabel,
        toolCategory: payload.toolCategory,
        toolPhaseId: payload.toolPhaseId,
        toolPhaseLabel: payload.toolPhaseLabel,
        toolSemanticParentId: payload.toolSemanticParentId,
        toolSemanticParentLabel: payload.toolSemanticParentLabel,
        sessionId: event.sessionId,
        taskId: event.taskId,
        delta: payload.toolInput,
        now: event.ts,
      });
      next = markToolReplayMarker(next, toolUseId, marker);
    }
    if (typeof payload.toolOutput === "string" && payload.toolOutput) {
      const toolUseId = typeof payload.toolUseId === "string" && payload.toolUseId ? payload.toolUseId : `pending_${event.taskId}`;
      const marker = replayMarker(event, "toolOutput");
      if (hasToolReplayMarker(next, toolUseId, marker)) {
        return next;
      }
      next = appendOrUpdateAssistantToolOutputDelta(next, {
        toolUseId,
        toolName: payload.toolName,
        target: payload.target,
        inputSummary: payload.inputSummary,
        parentToolUseId: payload.parentToolUseId,
        toolGroupId: payload.toolGroupId,
        toolIndex: payload.toolIndex,
        toolTotal: payload.toolTotal,
        toolOperationId: payload.toolOperationId,
        toolOperationLabel: payload.toolOperationLabel,
        toolCategory: payload.toolCategory,
        toolPhaseId: payload.toolPhaseId,
        toolPhaseLabel: payload.toolPhaseLabel,
        toolSemanticParentId: payload.toolSemanticParentId,
        toolSemanticParentLabel: payload.toolSemanticParentLabel,
        sessionId: event.sessionId,
        taskId: event.taskId,
        delta: payload.toolOutput,
        stream: payload.outputStream,
        now: event.ts,
      });
      next = markToolReplayMarker(next, toolUseId, marker);
    }
    return next;
  }

  if (event.type === "tool_use_complete") {
    const payload = event.payload as ToolUseCompletePayload;
    if (!payload.toolUseId || !payload.toolName) return current;
    return completeAssistantToolUseMessage(current, {
      toolUseId: payload.toolUseId,
      toolName: payload.toolName,
      input: payload.input,
      target: payload.target,
      inputSummary: payload.inputSummary,
      parentToolUseId: payload.parentToolUseId,
      toolGroupId: payload.toolGroupId,
      toolIndex: payload.toolIndex,
      toolTotal: payload.toolTotal,
      toolOperationId: payload.toolOperationId,
      toolOperationLabel: payload.toolOperationLabel,
      toolCategory: payload.toolCategory,
      toolPhaseId: payload.toolPhaseId,
      toolPhaseLabel: payload.toolPhaseLabel,
      toolSemanticParentId: payload.toolSemanticParentId,
      toolSemanticParentLabel: payload.toolSemanticParentLabel,
      sessionId: event.sessionId,
      taskId: event.taskId,
      now: event.ts,
    });
  }

  if (event.type === "tool_result") {
    const payload = event.payload as ToolResultPayload;
    if (!payload.toolUseId) return current;
    return appendAssistantToolResultMessage(current, {
      toolUseId: payload.toolUseId,
      toolName: payload.toolName,
      parentToolUseId: payload.parentToolUseId,
      toolGroupId: payload.toolGroupId,
      toolIndex: payload.toolIndex,
      toolTotal: payload.toolTotal,
      toolOperationId: payload.toolOperationId,
      toolOperationLabel: payload.toolOperationLabel,
      toolCategory: payload.toolCategory,
      toolPhaseId: payload.toolPhaseId,
      toolPhaseLabel: payload.toolPhaseLabel,
      toolSemanticParentId: payload.toolSemanticParentId,
      toolSemanticParentLabel: payload.toolSemanticParentLabel,
      content: payload.content,
      isError: payload.isError,
      target: payload.target,
      inputSummary: payload.inputSummary,
      resultSummary: payload.resultSummary,
      resultPreview: payload.resultPreview,
      durationMs: payload.durationMs,
      sessionId: event.sessionId,
      taskId: event.taskId,
      now: event.ts,
    });
  }

  if (event.type === "tool.started") {
    if (isChatCompatPayload(event.payload)) return current;
    const payload = event.payload as ToolLifecyclePayload;
    if (!payload.toolCallId) return current;
    return appendOrUpdateAssistantToolStartMessage(current, {
      toolUseId: payload.toolCallId,
      toolName: payload.toolName,
      input: payload.arguments,
      target: payload.target,
      inputSummary: payload.inputSummary,
      parentToolUseId: payload.parentToolUseId,
      toolGroupId: payload.toolGroupId,
      toolIndex: payload.toolIndex,
      toolTotal: payload.toolTotal,
      toolOperationId: payload.toolOperationId,
      toolOperationLabel: payload.toolOperationLabel,
      toolCategory: payload.toolCategory,
      toolPhaseId: payload.toolPhaseId,
      toolPhaseLabel: payload.toolPhaseLabel,
      toolSemanticParentId: payload.toolSemanticParentId,
      toolSemanticParentLabel: payload.toolSemanticParentLabel,
      sessionId: event.sessionId,
      taskId: event.taskId,
      now: event.ts,
    });
  }

  if (event.type === "tool.completed" || event.type === "tool.failed" || event.type === "tool.blocked") {
    if (isChatCompatPayload(event.payload)) return current;
    const payload = event.payload as ToolLifecyclePayload;
    if (!payload.toolCallId) return current;
    const isError = event.type !== "tool.completed";
    return appendAssistantToolResultMessage(current, {
      toolUseId: payload.toolCallId,
      toolName: payload.toolName,
      parentToolUseId: payload.parentToolUseId,
      toolGroupId: payload.toolGroupId,
      toolIndex: payload.toolIndex,
      toolTotal: payload.toolTotal,
      toolOperationId: payload.toolOperationId,
      toolOperationLabel: payload.toolOperationLabel,
      toolCategory: payload.toolCategory,
      toolPhaseId: payload.toolPhaseId,
      toolPhaseLabel: payload.toolPhaseLabel,
      toolSemanticParentId: payload.toolSemanticParentId,
      toolSemanticParentLabel: payload.toolSemanticParentLabel,
      content: payload.result ?? {
        status: event.type.slice("tool.".length),
        reason: payload.reason,
        error: payload.error,
        failureKind: payload.failureKind,
        recoveryHint: payload.recoveryHint,
        recoveryDecision: payload.recoveryDecision,
      },
      isError,
      status: event.type === "tool.blocked" ? "blocked" : isError ? "failed" : "completed",
      lifecycleStatus: event.type.slice("tool.".length),
      target: payload.target,
      inputSummary: payload.inputSummary,
      resultSummary: payload.resultSummary ?? payload.reason,
      resultPreview: payload.resultPreview,
      durationMs: payload.durationMs,
      sessionId: event.sessionId,
      taskId: event.taskId,
      now: event.ts,
    });
  }

  if (event.type === "tool.progress" || event.type === "tool.output") {
    if (isChatCompatPayload(event.payload)) return current;
    const payload = event.payload as ToolOutputPayload;
    const toolUseId = payload.toolUseId ?? payload.toolCallId;
    if (!toolUseId) return current;
    const delta = readPayloadChunk(payload, ["chunk", "delta", "toolOutput", "message", "summary", "text"]);
    if (!delta) return current;
    const marker = replayMarker(event, "toolOutput");
    if (hasToolReplayMarker(current, toolUseId, marker)) return current;
    const next = appendOrUpdateAssistantToolOutputDelta(current, {
      toolUseId,
      toolName: payload.toolName,
      target: payload.target,
      inputSummary: payload.inputSummary,
      parentToolUseId: payload.parentToolUseId,
      toolGroupId: payload.toolGroupId,
      toolIndex: payload.toolIndex,
      toolTotal: payload.toolTotal,
      toolOperationId: payload.toolOperationId,
      toolOperationLabel: payload.toolOperationLabel,
      toolCategory: payload.toolCategory,
      toolPhaseId: payload.toolPhaseId,
      toolPhaseLabel: payload.toolPhaseLabel,
      toolSemanticParentId: payload.toolSemanticParentId,
      toolSemanticParentLabel: payload.toolSemanticParentLabel,
      sessionId: event.sessionId,
      taskId: event.taskId,
      delta,
      stream: payload.outputStream ?? payload.stream ?? (event.type === "tool.progress" ? "activity" : "result_preview"),
      now: event.ts,
    });
    return markToolReplayMarker(next, toolUseId, marker);
  }

  if (event.type === "command.started") {
    const payload = event.payload as CommandLifecyclePayload;
    if (!payload.toolUseId) return current;
    const commandTarget = payload.target ?? payload.command;
    return appendOrUpdateAssistantToolStartMessage(current, {
      toolUseId: payload.toolUseId,
      toolName: payload.toolName ?? "run_command",
      target: commandTarget,
      inputSummary: payload.inputSummary ?? commandTarget,
      parentToolUseId: payload.parentToolUseId,
      toolGroupId: payload.toolGroupId,
      toolIndex: payload.toolIndex,
      toolTotal: payload.toolTotal,
      toolOperationId: payload.toolOperationId,
      toolOperationLabel: payload.toolOperationLabel,
      toolCategory: payload.toolCategory,
      toolPhaseId: payload.toolPhaseId,
      toolPhaseLabel: payload.toolPhaseLabel,
      toolSemanticParentId: payload.toolSemanticParentId,
      toolSemanticParentLabel: payload.toolSemanticParentLabel,
      sessionId: event.sessionId,
      taskId: event.taskId,
      now: event.ts,
    });
  }

  if (event.type === "command.output") {
    const payload = event.payload as CommandOutputPayload;
    if (!payload.toolUseId || !payload.chunk) return current;
    const marker = replayMarker(event, `commandOutput:${payload.stream}`);
    if (hasToolReplayMarker(current, payload.toolUseId, marker)) return current;
    const next = appendOrUpdateAssistantToolOutputDelta(current, {
      toolUseId: payload.toolUseId,
      toolName: payload.toolName ?? "run_command",
      target: payload.target,
      inputSummary: payload.inputSummary,
      parentToolUseId: payload.parentToolUseId,
      toolGroupId: payload.toolGroupId,
      toolIndex: payload.toolIndex,
      toolTotal: payload.toolTotal,
      toolOperationId: payload.toolOperationId,
      toolOperationLabel: payload.toolOperationLabel,
      toolCategory: payload.toolCategory,
      toolPhaseId: payload.toolPhaseId,
      toolPhaseLabel: payload.toolPhaseLabel,
      toolSemanticParentId: payload.toolSemanticParentId,
      toolSemanticParentLabel: payload.toolSemanticParentLabel,
      sessionId: event.sessionId,
      taskId: event.taskId,
      delta: payload.chunk,
      stream: payload.stream,
      now: event.ts,
    });
    return markToolReplayMarker(next, payload.toolUseId, marker);
  }

  if (event.type === "command.completed" || event.type === "command.failed" || event.type === "command.cancelled") {
    const payload = event.payload as CommandLifecyclePayload;
    if (!payload.toolUseId) return current;
    const normalizedStatus = String(payload.status ?? "").toLowerCase();
    const isCancelled = event.type === "command.cancelled" || normalizedStatus === "cancelled";
    const isError = !isCancelled && (event.type === "command.failed" || !["completed", "success", "succeeded"].includes(normalizedStatus));
    const exitLabel = typeof payload.exitCode === "number" ? `exit ${payload.exitCode}` : String(payload.status || (isError ? "failed" : "completed"));
    const commandSummary = isCancelled
      ? `命令已取消：${exitLabel}`
      : isError
        ? `命令失败：${exitLabel}`
        : `命令已完成：${exitLabel}`;
    const commandTarget = payload.target ?? payload.command;
    return appendAssistantToolResultMessage(current, {
      toolUseId: payload.toolUseId,
      toolName: payload.toolName ?? "run_command",
      target: commandTarget,
      inputSummary: payload.inputSummary ?? commandTarget,
      parentToolUseId: payload.parentToolUseId,
      toolGroupId: payload.toolGroupId,
      toolIndex: payload.toolIndex,
      toolTotal: payload.toolTotal,
      toolOperationId: payload.toolOperationId,
      toolOperationLabel: payload.toolOperationLabel,
      toolCategory: payload.toolCategory,
      toolPhaseId: payload.toolPhaseId,
      toolPhaseLabel: payload.toolPhaseLabel,
      toolSemanticParentId: payload.toolSemanticParentId,
      toolSemanticParentLabel: payload.toolSemanticParentLabel,
      content: {
        status: payload.status,
        exitCode: payload.exitCode,
        commandId: payload.commandId,
        stdoutPath: payload.stdoutPath,
        stderrPath: payload.stderrPath,
        background: payload.background,
      },
      isError,
      status: isCancelled ? "cancelled" : undefined,
      resultSummary: commandSummary,
      durationMs: payload.durationMs,
      sessionId: event.sessionId,
      taskId: event.taskId,
      now: event.ts,
    });
  }

  if (event.type === "assistant_progress") {
    const payload = event.payload as AssistantProgressPayload;
    const text = readPayloadText(payload, ["text", "summary", "message", "title"]);
    if (!text) return current;
    return appendAssistantProgressMessage(current, {
      sessionId: event.sessionId,
      taskId: event.taskId,
      content: text,
      now: event.ts,
      eventId: event.eventId,
      metadata: event.payload && typeof event.payload === "object" ? event.payload as Record<string, unknown> : null,
    });
  }

  if (event.type === "status") {
    const payload = event.payload as ChatStatusPayload;
    if (payload.state === "idle") {
      return removeAssistantThinkingMessage(current, {
        sessionId: event.sessionId,
        taskId: event.taskId,
        now: event.ts,
      });
    }
    if (["thinking", "tool_executing", "streaming"].includes(String(payload.state))) {
      return appendOrUpdateAssistantThinkingMessage(current, {
        sessionId: event.sessionId,
        taskId: event.taskId,
        eventId: event.eventId,
        state: payload.state,
        verb: payload.verb,
        now: event.ts,
      });
    }
    return current;
  }

  if (event.type === "thinking") {
    const payload = event.payload as { text?: unknown; source?: unknown };
    return appendOrUpdateAssistantThinkingMessage(current, {
      sessionId: event.sessionId,
      taskId: event.taskId,
      eventId: event.eventId,
      state: "thinking",
      text: typeof payload.text === "string" ? payload.text : undefined,
      source: typeof payload.source === "string" ? payload.source : undefined,
      now: event.ts,
    });
  }

  if (event.type === "permission_request") {
    const payload = event.payload as PermissionRequestPayload;
    if (!payload.requestId) return current;
    if (payload.resolved) {
      return resolvePermissionRequestMessage(current, {
        requestId: payload.requestId,
        decision: payload.decision ?? "approved",
        toolName: payload.toolName,
        input: payload.input,
        preview: payload.preview,
        filesChanged: payload.filesChanged,
        changedPaths: payload.changedPaths,
        diffText: payload.diffText,
        sessionId: event.sessionId,
        taskId: event.taskId,
        createIfMissing: true,
        now: event.ts,
      });
    }
    return appendOrUpdatePermissionRequestMessage(
      removeAssistantThinkingMessage(current, {
        sessionId: event.sessionId,
        taskId: event.taskId,
        now: event.ts,
      }),
      {
        requestId: payload.requestId,
        toolName: payload.toolName,
        input: payload.input,
        description: payload.description,
        preview: payload.preview,
        filesChanged: payload.filesChanged,
        changedPaths: payload.changedPaths,
        diffText: payload.diffText,
        sessionId: event.sessionId,
        taskId: event.taskId,
        now: event.ts,
      },
    );
  }

  if (SPECIAL_EVENT_TYPES.has(event.type)) {
    const kind =
      event.type === "system_notification"
        ? "system"
        : event.type === "computer_use_permission_request"
          ? "computer_use_permission"
          : event.type;
    return replaySpecialEvent(current, event, kind);
  }

  if (event.type === "task.supplement.consumed") {
    const payload = event.payload as {
      reason?: unknown;
      answer?: unknown;
      requestId?: unknown;
      toolCallId?: unknown;
      internalResponse?: unknown;
    };
    if (payload.reason !== "ask_user_question_answer") return current;
    const internalResponse =
      payload.internalResponse && typeof payload.internalResponse === "object"
        ? payload.internalResponse as Record<string, unknown>
        : {};
    return resolveAskUserQuestionMessage(current, {
      messageId: typeof internalResponse.messageId === "string" ? internalResponse.messageId : undefined,
      requestId:
        typeof payload.requestId === "string"
          ? payload.requestId
          : typeof internalResponse.requestId === "string"
            ? internalResponse.requestId
            : undefined,
      toolCallId: typeof payload.toolCallId === "string" ? payload.toolCallId : undefined,
      answer: typeof payload.answer === "string" ? payload.answer : "",
      now: event.ts,
    });
  }

  if (event.type === "approval.resolved") {
    const payload = event.payload as {
      approvalId?: unknown;
      decision?: unknown;
      kind?: unknown;
      request?: unknown;
      preview?: Array<{ label: string; value: string }>;
      filesChanged?: unknown;
      changedPaths?: unknown;
      diffText?: unknown;
    };
    if (typeof payload.approvalId !== "string" || !payload.approvalId.trim()) return current;
    const approvalId = payload.approvalId.trim();
    return resolveSpecialApprovalMessage(
      resolvePermissionRequestMessage(current, {
        requestId: approvalId,
        decision: typeof payload.decision === "string" ? payload.decision : "approved",
        toolName: typeof payload.kind === "string" ? payload.kind : undefined,
        input: payload.request,
        preview: payload.preview,
        filesChanged: typeof payload.filesChanged === "number" ? payload.filesChanged : undefined,
        changedPaths: Array.isArray(payload.changedPaths) ? payload.changedPaths.filter((item): item is string => typeof item === "string") : undefined,
        diffText: typeof payload.diffText === "string" ? payload.diffText : undefined,
        sessionId: event.sessionId,
        taskId: event.taskId,
        createIfMissing: true,
        now: event.ts,
      }),
      {
        approvalId,
        decision: typeof payload.decision === "string" ? payload.decision : "approved",
        input: payload.request,
        preview: payload.preview,
        filesChanged: typeof payload.filesChanged === "number" ? payload.filesChanged : undefined,
        changedPaths: Array.isArray(payload.changedPaths) ? payload.changedPaths.filter((item): item is string => typeof item === "string") : undefined,
        diffText: typeof payload.diffText === "string" ? payload.diffText : undefined,
        now: event.ts,
      },
    );
  }

  if (event.type === "task.failed" || event.type === "task.cancelled" || event.type === "task.completed") {
    return removeAssistantThinkingMessage(
      stopStreamingMessagesForTask(current, {
        sessionId: event.sessionId,
        taskId: event.taskId,
      }),
      {
        sessionId: event.sessionId,
        taskId: event.taskId,
        now: event.ts,
      },
    );
  }

  return current;
}

export function replayTraceEventsToChatMessages(
  current: ChatMessageView[],
  traces: TraceEventRecord[],
  options: {
    childTaskIds?: ReadonlySet<string>;
  } = {},
): ChatMessageView[] {
  if (!traces.length) return current;
  const childTaskIds = options.childTaskIds ?? EMPTY_CHILD_TASK_IDS;
  return traces
    .slice()
    .sort((left, right) => {
      const seqDiff = (left.sequence ?? 0) - (right.sequence ?? 0);
      if (seqDiff !== 0) return seqDiff;
      const timeDiff = (left.createdAt ?? 0) - (right.createdAt ?? 0);
      if (timeDiff !== 0) return timeDiff;
      return String(left.id).localeCompare(String(right.id));
    })
    .reduce((messages, trace) => replayTraceEvent(messages, envelopeFromTrace(trace), childTaskIds), current);
}
