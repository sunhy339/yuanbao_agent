import { useEffect, useRef } from "react";
import type {
  AgentEventEnvelope,
  ChatMessageCompletePayload,
  CommandLogRecord,
  CommandLifecyclePayload,
  CommandOutputPayload,
  ContentStartPayload,
  ContentDeltaPayload,
  MessageDeltaPayload,
  MessageCreatedPayload,
  MessageCompletedPayload,
  MessageFailedPayload,
  PermissionRequestPayload,
  ToolLifecyclePayload,
  ToolOutputPayload,
  ToolResultPayload,
  ToolUseCompletePayload,
  SessionCreatedPayload,
  SessionUpdatedPayload,
  SessionRecord,
  TaskRecord,
  TraceEventRecord,
} from "@shared";
import { RuntimeClient } from "../lib/runtimeClient";
import { formatAssistantFailureContent } from "../state/chatMessages";
import {
  appendAssistantToken,
  completeAssistantMessage,
  failAssistantMessageForEvent,
  messageRecordToChatMessageLocal,
} from "../state/chatTokenHelpers";
import {
  appendOrUpdateAssistantMessageCompletion,
  appendOrUpdateAssistantMessageDelta,
  appendOrUpdateAssistantToolInputDelta,
  appendOrUpdateAssistantToolOutputDelta,
  appendAssistantToolResultMessage,
  appendSpecialEventMessage,
  appendOrUpdateAssistantThinkingMessage,
  appendOrUpdatePermissionRequestMessage,
  appendOrUpdateAssistantToolStartMessage,
  closeAssistantThinkingForToolBoundary,
  completeAssistantToolUseMessage,
  completeChatCompatMessage,
  removeAssistantThinkingMessage,
  resolvePermissionRequestMessage,
  resolveAskUserQuestionMessage,
  resolveSpecialApprovalMessage,
  updateAssistantMessageByMessageId,
  reconcileBackendMessage,
  failAssistantMessage,
  stopStreamingMessagesForTask,
} from "../state/chatMessages";
import { isChatVisibleEvent as shouldShowEventInChat } from "../ui/workbench/workspaces/session/visibilityRouting";
import {
  applyEventToTask,
  taskRecordFromEvent,
  upsertRecord,
  sortByUpdatedAtDesc,
} from "../state/eventRecordViews";
import { shouldPromoteTaskToActive } from "../state/sessionDerivedViews";
import { TRACE_CACHE_LIMIT } from "../state/providerConfig";
import {
  applyYuanbaoServerMessageToChat,
  shouldFlushPendingTokensForYuanbaoMessage,
  shouldSuppressLegacyRenderingForYuanbaoMessage,
  yuanbaoServerMessageFromEvent,
  yuanbaoServerMessageProducesChat,
} from "../state/yuanbaoServerMessages";

const runtimeClient = new RuntimeClient();

export interface UseEventSubscriptionDeps {
  setActiveTaskForSession: (taskId: string | null, sessionId?: string | null) => void;

  // State setters
  setEvents: React.Dispatch<React.SetStateAction<any[]>>;
  setSession: React.Dispatch<React.SetStateAction<SessionRecord | null>>;
  setSessions: React.Dispatch<React.SetStateAction<SessionRecord[]>>;
  setTask: React.Dispatch<React.SetStateAction<TaskRecord | null>>;
  setActiveTaskId: React.Dispatch<React.SetStateAction<string | null>>;
  setTaskHistory: React.Dispatch<React.SetStateAction<TaskRecord[]>>;
  setChatMessages: React.Dispatch<React.SetStateAction<any[]>>;
  setTraceEvents: React.Dispatch<React.SetStateAction<TraceEventRecord[]>>;
  setCommandLogCacheById: React.Dispatch<React.SetStateAction<Record<string, any>>>;
  setTraceError: React.Dispatch<React.SetStateAction<string | null>>;
  setPatchCacheById: React.Dispatch<React.SetStateAction<Record<string, any>>>;
  setPatchBusyId: React.Dispatch<React.SetStateAction<string | null>>;
  setApprovalBusyId: React.Dispatch<React.SetStateAction<string | null>>;
  setError: (error: string | null) => void;

  // Refs
  sessionActiveTaskMapRef: React.MutableRefObject<Map<string, string>>;
  childTaskIdsRef: React.MutableRefObject<Set<string>>;
  pendingAssistantTokenEventsRef: React.MutableRefObject<AgentEventEnvelope[]>;
  assistantTokenFlushTimerRef: React.MutableRefObject<ReturnType<typeof setTimeout> | null>;
}

export function useEventSubscription(deps: UseEventSubscriptionDeps) {
  const {
    setActiveTaskForSession,
    setEvents,
    setSession, setSessions,
    setTask, setActiveTaskId, setTaskHistory,
    setChatMessages,
    setTraceEvents,
    setCommandLogCacheById,
    setError,
    sessionActiveTaskMapRef,
    childTaskIdsRef,
    pendingAssistantTokenEventsRef,
    assistantTokenFlushTimerRef,
  } = deps;
  const cancelledTaskIdsRef = useRef<Set<string>>(new Set());

  function isChatVisibleEvent(event: AgentEventEnvelope): boolean {
    return shouldShowEventInChat(event, childTaskIdsRef.current);
  }

  function flushPendingAssistantTokens() {
    const pendingEvents = pendingAssistantTokenEventsRef.current;
    if (!pendingEvents.length) return;
    pendingAssistantTokenEventsRef.current = [];
    if (assistantTokenFlushTimerRef.current !== null) {
      clearTimeout(assistantTokenFlushTimerRef.current);
      assistantTokenFlushTimerRef.current = null;
    }
    setChatMessages((current) =>
      pendingEvents.reduce((nextMessages, event) => appendAssistantToken(nextMessages, event), current),
    );
  }

  function discardPendingAssistantTokensForTask(taskId: string) {
    const nextEvents = pendingAssistantTokenEventsRef.current.filter((event) => event.taskId !== taskId);
    pendingAssistantTokenEventsRef.current = nextEvents;
    if (nextEvents.length === 0 && assistantTokenFlushTimerRef.current !== null) {
      clearTimeout(assistantTokenFlushTimerRef.current);
      assistantTokenFlushTimerRef.current = null;
    }
  }

  function queueAssistantToken(event: AgentEventEnvelope) {
    const payload = event.payload as any;
    const delta = payload.delta ?? "";
    if (!delta) return;
    pendingAssistantTokenEventsRef.current.push(event);
    if (assistantTokenFlushTimerRef.current !== null) return;
    assistantTokenFlushTimerRef.current = setTimeout(() => {
      assistantTokenFlushTimerRef.current = null;
      flushPendingAssistantTokens();
    }, 33);
  }

  function shouldSuppressCancelledTaskEvent(event: AgentEventEnvelope): boolean {
    if (!event.taskId || !cancelledTaskIdsRef.current.has(event.taskId)) {
      return false;
    }
    if (event.type.startsWith("task.") || event.type === "session.created" || event.type === "session.updated") {
      return false;
    }
    if (event.type === "command.cancelled") {
      return false;
    }
    return true;
  }

  function shouldRenderLegacyAssistantToken(event: AgentEventEnvelope): boolean {
    const payload = event.payload as { messageId?: unknown };
    if (typeof payload.messageId === "string" && payload.messageId.trim()) {
      return false;
    }
    return isChatVisibleEvent(event);
  }

  function isChatCompatPayload(payload: unknown): boolean {
    return Boolean(payload && typeof payload === "object" && (payload as { _chatCompat?: unknown })._chatCompat === true);
  }

  function bridgeMetadata(payload: unknown): Record<string, unknown> {
    if (!payload || typeof payload !== "object") return {};
    const bridge = (payload as { _bridge?: unknown })._bridge;
    return bridge && typeof bridge === "object" && !Array.isArray(bridge)
      ? bridge as Record<string, unknown>
      : {};
  }

  function suppressesChatRendering(payload: unknown): boolean {
    const bridge = bridgeMetadata(payload);
    return bridge.suppressChatReplay === true || bridge.suppressRealtimeFlat === true;
  }

  const CONTROL_FLOW_TOOL_NAMES = new Set(["ask_user_question", "enter_plan_mode", "exit_plan_mode"]);
  const INTERNAL_APPROVAL_KINDS = new Set(["completion_review"]);

  function isControlFlowToolPayload(payload: unknown): boolean {
    if (!payload || typeof payload !== "object") return false;
    const toolName = String((payload as { toolName?: unknown }).toolName ?? "").toLowerCase();
    return CONTROL_FLOW_TOOL_NAMES.has(toolName);
  }

  function isInternalApprovalKind(value: unknown): boolean {
    return typeof value === "string" && INTERNAL_APPROVAL_KINDS.has(value);
  }

  function isApprovalRequiredToolResultPayload(payload: unknown): boolean {
    if (!payload || typeof payload !== "object") return false;
    const result = (payload as { result?: unknown }).result;
    if (!result || typeof result !== "object") return false;
    return String((result as { status?: unknown }).status ?? "").toLowerCase() === "approval_required";
  }

  function readPayloadText(payload: unknown, keys: string[]): string {
    if (!payload || typeof payload !== "object") {
      return "";
    }
    const record = payload as Record<string, unknown>;
    for (const key of keys) {
      const value = record[key];
      if (typeof value === "string" && value.trim()) {
        return value.trim();
      }
      if (typeof value === "number" || typeof value === "boolean") {
        return String(value);
      }
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
    if (!text || looksLikeInternalDisplayText(text)) {
      return "";
    }
    return text;
  }

  function readPayloadChunk(payload: unknown, keys: string[]): string {
    if (!payload || typeof payload !== "object") {
      return "";
    }
    const record = payload as Record<string, unknown>;
    for (const key of keys) {
      const value = record[key];
      if (typeof value === "string" && value) {
        return value;
      }
      if (typeof value === "number" || typeof value === "boolean") {
        return String(value);
      }
    }
    return "";
  }

  function appendCommandOutputTail(current: string, chunk: string, maxLength = 4000): string {
    const next = `${current}${chunk}`;
    if (next.length <= maxLength) {
      return next;
    }
    return next.slice(next.length - maxLength);
  }

  function commandLifecycleStatus(eventType: string, status?: string): CommandLogRecord["status"] {
    if (status === "running" || status === "completed" || status === "failed" || status === "timeout" || status === "killed" || status === "cancelled") {
      return status;
    }
    if (eventType === "command.started") return "running";
    if (eventType === "command.completed") return "completed";
    if (eventType === "command.cancelled") return "cancelled";
    return "failed";
  }

  function commandLogMetadataFromPayload(
    payload: CommandLifecyclePayload | CommandOutputPayload,
    current?: Partial<CommandLogRecord>,
  ): Partial<CommandLogRecord> {
    return {
      toolUseId: payload.toolUseId ?? current?.toolUseId,
      toolName: payload.toolName ?? current?.toolName,
      parentToolUseId: payload.parentToolUseId ?? current?.parentToolUseId,
      toolGroupId: payload.toolGroupId ?? current?.toolGroupId,
      toolIndex: payload.toolIndex ?? current?.toolIndex,
      toolTotal: payload.toolTotal ?? current?.toolTotal,
      toolOperationId: payload.toolOperationId ?? current?.toolOperationId,
      toolOperationLabel: payload.toolOperationLabel ?? current?.toolOperationLabel,
      toolCategory: payload.toolCategory ?? current?.toolCategory,
      toolPhaseId: payload.toolPhaseId ?? current?.toolPhaseId,
      toolPhaseLabel: payload.toolPhaseLabel ?? current?.toolPhaseLabel,
      toolSemanticParentId: payload.toolSemanticParentId ?? current?.toolSemanticParentId,
      toolSemanticParentLabel: payload.toolSemanticParentLabel ?? current?.toolSemanticParentLabel,
      target: payload.target ?? current?.target,
      inputSummary: payload.inputSummary ?? current?.inputSummary,
    };
  }

  function rememberCommandLifecycleEvent(event: AgentEventEnvelope, payload: CommandLifecyclePayload) {
    if (!payload.commandId) {
      return;
    }
    setCommandLogCacheById((current) => {
      const existing = current[payload.commandId] as Partial<CommandLogRecord> | undefined;
      const next: CommandLogRecord = {
        id: payload.commandId,
        taskId: event.taskId,
        ...commandLogMetadataFromPayload(payload, existing),
        command: payload.command ?? existing?.command ?? payload.target ?? payload.commandId,
        cwd: payload.cwd ?? existing?.cwd ?? "",
        shell: (payload.shell as CommandLogRecord["shell"] | undefined) ?? existing?.shell,
        background: payload.background ?? existing?.background,
        status: commandLifecycleStatus(event.type, payload.status),
        exitCode: payload.exitCode ?? existing?.exitCode,
        startedAt: existing?.startedAt ?? event.ts,
        finishedAt: event.type === "command.started" ? existing?.finishedAt : event.ts,
        durationMs: payload.durationMs ?? existing?.durationMs,
        stdoutPath: payload.stdoutPath ?? existing?.stdoutPath,
        stderrPath: payload.stderrPath ?? existing?.stderrPath,
        stdout: existing?.stdout,
        stderr: existing?.stderr,
      };
      return { ...current, [payload.commandId]: next };
    });
  }

  function rememberCommandOutputEvent(event: AgentEventEnvelope, payload: CommandOutputPayload) {
    if (!payload.commandId || !payload.chunk) {
      return;
    }
    setCommandLogCacheById((current) => {
      const existing = current[payload.commandId] as Partial<CommandLogRecord> | undefined;
      const stream = payload.stream === "stderr" ? "stderr" : "stdout";
      const next: CommandLogRecord = {
        id: payload.commandId,
        taskId: event.taskId,
        ...commandLogMetadataFromPayload(payload, existing),
        command: existing?.command ?? payload.target ?? payload.commandId,
        cwd: existing?.cwd ?? "",
        shell: existing?.shell,
        background: existing?.background,
        status: existing?.status ?? "running",
        exitCode: existing?.exitCode,
        startedAt: existing?.startedAt ?? event.ts,
        finishedAt: existing?.finishedAt,
        durationMs: existing?.durationMs,
        stdoutPath: existing?.stdoutPath,
        stderrPath: existing?.stderrPath,
        stdout: stream === "stdout" ? appendCommandOutputTail(existing?.stdout ?? "", payload.chunk) : existing?.stdout,
        stderr: stream === "stderr" ? appendCommandOutputTail(existing?.stderr ?? "", payload.chunk) : existing?.stderr,
      };
      return { ...current, [payload.commandId]: next };
    });
  }

  function rememberTraceEvent(event: AgentEventEnvelope) {
    setTraceEvents((current) => {
      const trace: TraceEventRecord = {
        id: event.eventId,
        sessionId: event.sessionId,
        taskId: event.taskId,
        type: event.type,
        source: event.type.split(".")[0] ?? "runtime",
        payload: event.payload,
        createdAt: event.ts,
        sequence: event.seq ?? event.ts,
        visibility: event.visibility,
        uiReplayScope: "chat",
        yuanbao: event.yuanbao ?? event.hahaCc,
        hahaCc: event.hahaCc ?? event.yuanbao,
      };
      const existingIndex = current.findIndex((item) => item.id === trace.id);
      if (existingIndex >= 0) {
        const next = [...current];
        next[existingIndex] = { ...next[existingIndex], ...trace };
        return next;
      }
      return [...current, trace].sort((left, right) => {
        const leftSeq = left.sequence;
        const rightSeq = right.sequence;
        if (leftSeq != null && rightSeq != null && leftSeq !== rightSeq) {
          return leftSeq - rightSeq;
        }
        if (leftSeq != null && rightSeq == null) return -1;
        if (leftSeq == null && rightSeq != null) return 1;
        const timeDiff = (left.createdAt ?? 0) - (right.createdAt ?? 0);
        if (timeDiff !== 0) return timeDiff;
        return left.id.localeCompare(right.id);
      }).slice(-TRACE_CACHE_LIMIT);
    });
  }

  function appendSpecialEventFromEnvelope(event: AgentEventEnvelope, kind: string) {
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
    const blocksAssistantStream = kind === "ask_user_question" || kind === "computer_use_permission";
    if (blocksAssistantStream) {
      flushPendingAssistantTokens();
    }
    setChatMessages((current) =>
      appendSpecialEventMessage(
        blocksAssistantStream
          ? removeAssistantThinkingMessage(current, {
              sessionId: event.sessionId,
              taskId: event.taskId,
              now: event.ts,
            })
          : current,
        {
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
        },
      ),
    );
  }

  function closeThinkingForToolBoundary(current: import("../state/chatMessages").ChatMessageView[], event: AgentEventEnvelope) {
    return closeAssistantThinkingForToolBoundary(current, {
      sessionId: event.sessionId,
      taskId: event.taskId,
      now: event.ts,
    });
  }

  useEffect(() => {
    let active = true;
    let dispose: (() => void) | undefined;

    runtimeClient
      .subscribeEvents((event) => {
        if (!active) {
          return;
        }
        rememberTraceEvent(event);

        if (event.type === "task.started" || event.type === "task.resumed") {
          cancelledTaskIdsRef.current.delete(event.taskId);
        } else if (event.type === "task.cancelled") {
          cancelledTaskIdsRef.current.add(event.taskId);
          discardPendingAssistantTokensForTask(event.taskId);
        }

        if (shouldSuppressCancelledTaskEvent(event)) {
          return;
        }

        const yuanbaoMessage = yuanbaoServerMessageFromEvent(event);
        const yuanbaoMessageVisible =
          Boolean(yuanbaoMessage) &&
          yuanbaoServerMessageProducesChat(yuanbaoMessage!) &&
          isChatVisibleEvent(event);
        if (yuanbaoMessage && yuanbaoMessageVisible) {
          if (shouldFlushPendingTokensForYuanbaoMessage(yuanbaoMessage)) {
            flushPendingAssistantTokens();
          }
          setChatMessages((current) =>
            applyYuanbaoServerMessageToChat(current, event).messages,
          );
        }
        if (
          yuanbaoMessage &&
          yuanbaoMessageVisible &&
          shouldSuppressLegacyRenderingForYuanbaoMessage(event, yuanbaoMessage)
        ) {
          return;
        }

        if (event.type === "content_start") {
          if (!isChatVisibleEvent(event)) {
            return;
          }
          const payload = event.payload as ContentStartPayload;
          if (payload.blockType === "tool_use" && payload.toolUseId) {
            setChatMessages((current) =>
              appendOrUpdateAssistantToolStartMessage(closeThinkingForToolBoundary(current, event), {
                toolUseId: payload.toolUseId!,
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
              }),
            );
          } else if (payload.blockType === "text") {
            return;
            setChatMessages((current) =>
              appendOrUpdateAssistantThinkingMessage(current, {
                sessionId: event.sessionId,
                taskId: event.taskId,
                state: "streaming",
                text: "正在输出回复",
                transient: true,
                now: event.ts,
              }),
            );
          }
          return;
        }

        if (event.type === "content_delta") {
          if (!isChatVisibleEvent(event)) {
            return;
          }
          const payload = event.payload as ContentDeltaPayload;
          if (typeof payload.text === "string" && payload.text) {
            const text = payload.text;
            const messageId =
              typeof payload.messageId === "string" && payload.messageId
                ? payload.messageId
                : `assistant_${event.taskId}`;
            setChatMessages((current) =>
              appendOrUpdateAssistantMessageDelta(current, {
                messageId,
                sessionId: event.sessionId,
                taskId: event.taskId,
                delta: text,
                now: event.ts,
              }),
            );
          }
          if (typeof payload.toolInput === "string" && payload.toolInput) {
            const toolUseId =
              typeof payload.toolUseId === "string" && payload.toolUseId
                ? payload.toolUseId
                : `pending_${event.taskId}`;
            setChatMessages((current) =>
              appendOrUpdateAssistantToolInputDelta(closeThinkingForToolBoundary(current, event), {
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
                delta: payload.toolInput!,
                now: event.ts,
              }),
            );
          }
          if (typeof payload.toolOutput === "string" && payload.toolOutput) {
            const toolUseId =
              typeof payload.toolUseId === "string" && payload.toolUseId
                ? payload.toolUseId
                : `pending_${event.taskId}`;
            setChatMessages((current) =>
              appendOrUpdateAssistantToolOutputDelta(closeThinkingForToolBoundary(current, event), {
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
                delta: payload.toolOutput!,
                stream: payload.outputStream,
                now: event.ts,
              }),
            );
          }
          return;
        }

        if (event.type === "tool_use_complete") {
          if (!isChatVisibleEvent(event)) {
            return;
          }
          const payload = event.payload as ToolUseCompletePayload;
          if (!payload.toolUseId || !payload.toolName) {
            return;
          }
          setChatMessages((current) =>
            completeAssistantToolUseMessage(closeThinkingForToolBoundary(current, event), {
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
            }),
          );
          return;
        }

        if (event.type === "tool_result") {
          if (!isChatVisibleEvent(event)) {
            return;
          }
          const payload = event.payload as ToolResultPayload;
          if (!payload.toolUseId) {
            return;
          }
          setChatMessages((current) =>
            appendAssistantToolResultMessage(closeThinkingForToolBoundary(current, event), {
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
            }),
          );
          return;
        }

        if (event.type === "tool.started") {
          if (!isChatVisibleEvent(event) || isChatCompatPayload(event.payload) || isControlFlowToolPayload(event.payload)) {
            return;
          }
          const payload = event.payload as ToolLifecyclePayload;
          if (!payload.toolCallId) {
            return;
          }
          setChatMessages((current) =>
            appendOrUpdateAssistantToolStartMessage(closeThinkingForToolBoundary(current, event), {
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
            }),
          );
          return;
        }

        if (event.type === "tool.completed" || event.type === "tool.failed" || event.type === "tool.blocked") {
          if (
            !isChatVisibleEvent(event) ||
            isChatCompatPayload(event.payload) ||
            isControlFlowToolPayload(event.payload) ||
            (event.type === "tool.completed" && isApprovalRequiredToolResultPayload(event.payload))
          ) {
            return;
          }
          const payload = event.payload as ToolLifecyclePayload;
          if (!payload.toolCallId) {
            return;
          }
          const isError = event.type !== "tool.completed";
          const resultContent = payload.result ?? {
            status: event.type.slice("tool.".length),
            reason: payload.reason,
            error: payload.error,
            failureKind: payload.failureKind,
            recoveryHint: payload.recoveryHint,
            recoveryDecision: payload.recoveryDecision,
          };
          setChatMessages((current) =>
            appendAssistantToolResultMessage(closeThinkingForToolBoundary(current, event), {
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
              content: resultContent,
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
            }),
          );
          return;
        }

        if (event.type === "tool.progress" || event.type === "tool.output") {
          if (!isChatVisibleEvent(event)) {
            return;
          }
          if (isChatCompatPayload(event.payload)) {
            return;
          }
          const payload = event.payload as ToolOutputPayload;
          const toolUseId = payload.toolUseId ?? payload.toolCallId;
          if (!toolUseId) {
            return;
          }
          const delta = readPayloadChunk(payload, ["chunk", "delta", "toolOutput", "message", "summary", "text"]);
          if (!delta) {
            return;
          }
          const stream = payload.outputStream ?? payload.stream ?? (event.type === "tool.progress" ? "activity" : "result_preview");
          setChatMessages((current) =>
            appendOrUpdateAssistantToolOutputDelta(closeThinkingForToolBoundary(current, event), {
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
              stream,
              now: event.ts,
            }),
          );
          return;
        }

        if (event.type === "command.started") {
          const payload = event.payload as CommandLifecyclePayload;
          rememberCommandLifecycleEvent(event, payload);
          if (!isChatVisibleEvent(event)) {
            return;
          }
          if (!payload.toolUseId) {
            return;
          }
          const commandTarget = payload.target ?? payload.command;
          setChatMessages((current) =>
            appendOrUpdateAssistantToolStartMessage(closeThinkingForToolBoundary(current, event), {
              toolUseId: payload.toolUseId!,
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
            }),
          );
          return;
        }

        if (event.type === "command.output") {
          const payload = event.payload as CommandOutputPayload;
          rememberCommandOutputEvent(event, payload);
          if (!isChatVisibleEvent(event)) {
            return;
          }
          if (!payload.toolUseId || !payload.chunk) {
            return;
          }
          setChatMessages((current) =>
            appendOrUpdateAssistantToolOutputDelta(closeThinkingForToolBoundary(current, event), {
              toolUseId: payload.toolUseId!,
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
            }),
          );
          return;
        }

        if (event.type === "command.completed" || event.type === "command.failed" || event.type === "command.cancelled") {
          const payload = event.payload as CommandLifecyclePayload;
          rememberCommandLifecycleEvent(event, payload);
          if (!isChatVisibleEvent(event)) {
            return;
          }
          if (!payload.toolUseId) {
            return;
          }
          const normalizedStatus = String(payload.status ?? "").toLowerCase();
          const isCancelled = event.type === "command.cancelled" || normalizedStatus === "cancelled";
          const isError = !isCancelled && (event.type === "command.failed" || !["completed", "success", "succeeded"].includes(normalizedStatus));
          const exitLabel = typeof payload.exitCode === "number" ? `exit ${payload.exitCode}` : String(payload.status || (isError ? "failed" : "completed"));
          const summary = isError
            ? `命令失败：${exitLabel}`
            : `命令已完成：${exitLabel}`;
          const commandSummary = isCancelled ? `命令已取消：${exitLabel}` : summary;
          const commandTarget = payload.target ?? payload.command;
          setChatMessages((current) =>
            appendAssistantToolResultMessage(closeThinkingForToolBoundary(current, event), {
              toolUseId: payload.toolUseId!,
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
            }),
          );
          return;
        }

        if ((event as { type?: string }).type === "assistant_progress") {
          return;
        }

        if (event.type === "message_complete") {
          const payload = event.payload as ChatMessageCompletePayload;
          flushPendingAssistantTokens();
          setChatMessages((current) =>
            removeAssistantThinkingMessage(
              completeChatCompatMessage(current, {
                messageId: payload.messageId,
                sessionId: event.sessionId,
                taskId: event.taskId,
                content: payload.content,
                now: event.ts,
              }),
              {
                sessionId: event.sessionId,
                taskId: event.taskId,
                now: event.ts,
              },
            ),
          );
          return;
        }

        if (event.type === "status") {
          return;
        }

        if (event.type === "thinking") {
          if (!isChatVisibleEvent(event) || suppressesChatRendering(event.payload)) {
            return;
          }
          const payload = event.payload as { text?: unknown; source?: unknown };
          setChatMessages((current) =>
            appendOrUpdateAssistantThinkingMessage(current, {
              sessionId: event.sessionId,
              taskId: event.taskId,
              state: "thinking",
              text: typeof payload.text === "string" ? payload.text : undefined,
              source: typeof payload.source === "string" ? payload.source : undefined,
              now: event.ts,
            }),
          );
          return;
        }

        if (event.type === "permission_request") {
          if (!isChatVisibleEvent(event)) {
            return;
          }
          const payload = event.payload as PermissionRequestPayload;
          if (!payload.requestId) {
            return;
          }
          if (isInternalApprovalKind(payload.toolName)) {
            return;
          }
          if (payload.resolved) {
            setChatMessages((current) =>
              resolvePermissionRequestMessage(current, {
                requestId: payload.requestId,
                decision: payload.decision ?? "approved",
                toolName: payload.toolName,
                input: payload.input,
                preview: payload.preview,
                previewSections: payload.previewSections,
                filesChanged: payload.filesChanged,
                changedPaths: payload.changedPaths,
                diffText: payload.diffText,
                sessionId: event.sessionId,
                taskId: event.taskId,
                createIfMissing: true,
                now: event.ts,
              }),
            );
            return;
          }
          setChatMessages((current) =>
            appendOrUpdatePermissionRequestMessage(
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
                previewSections: payload.previewSections,
                filesChanged: payload.filesChanged,
                changedPaths: payload.changedPaths,
                diffText: payload.diffText,
                sessionId: event.sessionId,
                taskId: event.taskId,
                now: event.ts,
              },
            ),
          );
          return;
        }

        if (
          [
            "api_retry",
            "system_notification",
            "compact_summary",
            "goal_event",
            "memory_event",
            "background_task",
            "agent_task_group",
            "task_summary",
            "plan_update",
            "ask_user_question",
            "computer_use_permission_request",
            "computer_use_permission",
          ].includes(event.type)
        ) {
          if (!isChatVisibleEvent(event)) {
            return;
          }
          const kind =
            event.type === "system_notification"
              ? "system"
              : event.type === "computer_use_permission_request"
                ? "computer_use_permission"
                : event.type;
          appendSpecialEventFromEnvelope(event, kind);
          return;
        }

        if (event.type === "task.supplement.consumed") {
          const payload = event.payload as {
            reason?: unknown;
            answer?: unknown;
            requestId?: unknown;
            toolCallId?: unknown;
            internalResponse?: unknown;
          };
          if (payload.reason === "ask_user_question_answer") {
            const internalResponse =
              payload.internalResponse && typeof payload.internalResponse === "object"
                ? payload.internalResponse as Record<string, unknown>
                : {};
            setChatMessages((current) =>
              resolveAskUserQuestionMessage(current, {
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
              }),
            );
          }
        }

        if (event.type === "approval.resolved") {
          const payload = event.payload as {
            approvalId?: unknown;
            decision?: unknown;
            kind?: unknown;
            request?: unknown;
            preview?: Array<{ label: string; value: string }>;
            previewSections?: unknown[];
            filesChanged?: unknown;
            changedPaths?: unknown;
            diffText?: unknown;
          };
          if (typeof payload.approvalId === "string" && payload.approvalId.trim()) {
            if (isInternalApprovalKind(payload.kind)) {
              return;
            }
            const approvalId = payload.approvalId.trim();
            setChatMessages((current) =>
              resolveSpecialApprovalMessage(
                resolvePermissionRequestMessage(current, {
                  requestId: approvalId,
                  decision: typeof payload.decision === "string" ? payload.decision : "approved",
                  toolName: typeof payload.kind === "string" ? payload.kind : undefined,
                  input: payload.request,
                  preview: payload.preview,
                  previewSections: payload.previewSections,
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
                  previewSections: payload.previewSections,
                  filesChanged: typeof payload.filesChanged === "number" ? payload.filesChanged : undefined,
                  changedPaths: Array.isArray(payload.changedPaths) ? payload.changedPaths.filter((item): item is string => typeof item === "string") : undefined,
                  diffText: typeof payload.diffText === "string" ? payload.diffText : undefined,
                  now: event.ts,
                },
              ),
            );
          }
        }

        // --- New message lifecycle events (P1.3 / P1.4) ---
        // message.delta: streaming token, routed by messageId
        if (event.type === "message.delta") {
          if (!isChatVisibleEvent(event)) {
            return;
          }
          const payload = event.payload as MessageDeltaPayload;
          const delta = payload.delta ?? "";
          const displayDelta = delta;
          if (!displayDelta) {
            return;
          }
          const messageId = payload.messageId;
          if (messageId) {
            setChatMessages((current) =>
              appendOrUpdateAssistantMessageDelta(
                current,
                {
                  messageId,
                  sessionId: event.sessionId,
                  taskId: event.taskId,
                  delta: displayDelta,
                  now: event.ts,
                },
              ),
            );
          } else {
            // Fallback: no messageId, use legacy behavior
            queueAssistantToken(event);
          }
          return;
        }

        // message.created: reconcile local pending message with backend message
        if (event.type === "message.created") {
          const payload = event.payload as MessageCreatedPayload;
          const msg = payload.message;
          if (!msg) return;
          // Only process user/assistant messages
          if (msg.role !== "user" && msg.role !== "assistant") return;
          const chatMsg = messageRecordToChatMessageLocal(msg);
          if (!chatMsg) return;
          setChatMessages((current) => reconcileBackendMessage(current, chatMsg));
          setEvents((current) => [...current, event].slice(-500));
          return;
        }

        // message.completed: finalize assistant message by messageId
        if (event.type === "message.completed") {
          const payload = event.payload as MessageCompletedPayload;
          flushPendingAssistantTokens();
          if (payload.messageId) {
            setChatMessages((current) =>
              removeAssistantThinkingMessage(
                appendOrUpdateAssistantMessageCompletion(current, {
                  messageId: payload.messageId!,
                  sessionId: event.sessionId,
                  taskId: event.taskId,
                  content: payload.content,
                  now: event.ts,
                }),
                {
                  sessionId: event.sessionId,
                  taskId: event.taskId,
                  now: event.ts,
                },
              ),
            );
          } else {
            // Fallback: no messageId, use legacy completion
            setChatMessages((current) =>
              removeAssistantThinkingMessage(completeAssistantMessage(current, event), {
                sessionId: event.sessionId,
                taskId: event.taskId,
                now: event.ts,
              }),
            );
          }
          setEvents((current) => [...current, event].slice(-500));
          return;
        }

        // message.failed: mark assistant message as failed by messageId
        if (event.type === "message.failed") {
          const payload = event.payload as MessageFailedPayload;
          flushPendingAssistantTokens();
          if (payload.messageId) {
            setChatMessages((current) =>
              failAssistantMessage(current, {
                messageId: payload.messageId,
                sessionId: event.sessionId,
                taskId: event.taskId,
                content: formatAssistantFailureContent(payload.content),
                now: event.ts,
              }),
            );
          } else {
            setChatMessages((current) => failAssistantMessageForEvent(current, event));
          }
          setEvents((current) => [...current, event].slice(-500));
          return;
        }

        // --- Legacy assistant.token (kept for backward compat) ---
        if (event.type === "assistant.token") {
          if (isChatCompatPayload(event.payload)) {
            return;
          }
          if (shouldRenderLegacyAssistantToken(event)) {
            queueAssistantToken(event);
          }
          return;
        }

        setEvents((current) => [...current, event].slice(-500));

        if (event.type === "session.created") {
          const payload = (event.payload ?? {}) as SessionCreatedPayload;
          const nextSession = payload.session;
          if (nextSession?.id) {
            setSessions((current) => sortByUpdatedAtDesc(upsertRecord(current, nextSession)));
          }
        }

        if (event.type === "session.updated") {
          const payload = (event.payload ?? {}) as SessionUpdatedPayload;
          setSession((current) =>
            current && current.id === event.sessionId
              ? {
                  ...current,
                  title: payload.title ?? current.title,
                  status: (payload.status as SessionRecord["status"] | undefined) ?? current.status,
                  summary: payload.summary ?? current.summary,
                  updatedAt: event.ts,
                }
              : current,
          );
          setSessions((current) =>
            current.map((item) =>
              item.id === event.sessionId
                ? {
                    ...item,
                    title: payload.title ?? item.title,
                    status: (payload.status as SessionRecord["status"] | undefined) ?? item.status,
                    summary: payload.summary ?? item.summary,
                    updatedAt: event.ts,
                  }
                : item,
            ),
          );
        }

        if (event.type.startsWith("task.")) {
          const eventTask = taskRecordFromEvent(event);
          const isChildWorker = (event.payload as Record<string, unknown>)?.childWorker === true;
          if (isChildWorker && event.type === "task.started") {
            childTaskIdsRef.current.add(event.taskId);
          }
          setActiveTaskId((current) => {
            if (isChildWorker) return current;
            if (event.type === "task.started") {
              const next = eventTask && shouldPromoteTaskToActive(eventTask, current) ? event.taskId : current;
              if (next && next !== current) sessionActiveTaskMapRef.current.set(event.sessionId, next);
              return next;
            }
            const next = !current && eventTask && shouldPromoteTaskToActive(eventTask, current) ? event.taskId : current;
            if (next && next !== current) sessionActiveTaskMapRef.current.set(event.sessionId, next);
            return next;
          });
          setTask((current) => {
            if (event.type === "task.started" && isChildWorker) {
              return current;
            }
            if (event.type === "task.started" && eventTask && !shouldPromoteTaskToActive(eventTask, current?.id ?? null)) {
              return current;
            }
            if (!current && event.type !== "task.started") {
              return current;
            }
            if (current && current.id !== event.taskId && event.type !== "task.started") {
              return current;
            }
            return applyEventToTask(current, event) ?? eventTask ?? current;
          });
          setTaskHistory((current) => {
            const existing = current.find((item) => item.id === event.taskId);
            const updated = existing ? applyEventToTask(existing, event) : eventTask;
            if (!updated) {
              return current;
            }

            return upsertRecord(current, updated);
          });
          setSession((current) =>
            current && current.id === event.sessionId
              ? { ...current, updatedAt: event.ts }
              : current,
          );
          setSessions((current) =>
            current.map((item) => (item.id === event.sessionId ? { ...item, updatedAt: event.ts } : item)),
          );
          // task.failed: only update task panel, don't create chat bubble
          // (message.failed handles the chat bubble now)
          if (event.type === "task.failed" && !isChildWorker) {
            flushPendingAssistantTokens();
            setChatMessages((current) =>
              removeAssistantThinkingMessage(
                stopStreamingMessagesForTask(current, {
                  sessionId: event.sessionId,
                  taskId: event.taskId,
                }),
                {
                  sessionId: event.sessionId,
                  taskId: event.taskId,
                  now: event.ts,
                },
              ),
            );
            // Legacy fallback: only create failure bubble if no message.failed was received
            // (handled by message.failed event now)
          }
          if (event.type === "task.cancelled" && !isChildWorker) {
            discardPendingAssistantTokensForTask(event.taskId);
            setChatMessages((current) =>
              removeAssistantThinkingMessage(
                stopStreamingMessagesForTask(current, {
                  sessionId: event.sessionId,
                  taskId: event.taskId,
                }),
                {
                  sessionId: event.sessionId,
                  taskId: event.taskId,
                  now: event.ts,
                },
              ),
            );
          }
        }

        // Legacy assistant.message.completed (kept for backward compat)
        if (event.type === "assistant.message.completed") {
          if (!isChatVisibleEvent(event)) {
            // skip non-chat event completion
          } else {
            flushPendingAssistantTokens();
            setChatMessages((current) => completeAssistantMessage(current, event));
          }
        }
      })
      .then((unlisten) => {
        dispose = unlisten;
      })
      .catch((reason) => {
        if (active) {
          setError(reason instanceof Error ? reason.message : String(reason));
        }
      });

    return () => {
      active = false;
      // Clear pending tokens
      pendingAssistantTokenEventsRef.current = [];
      if (assistantTokenFlushTimerRef.current !== null) {
        clearTimeout(assistantTokenFlushTimerRef.current);
        assistantTokenFlushTimerRef.current = null;
      }
      dispose?.();
    };
  }, []);

  return { flushPendingAssistantTokens };
}
