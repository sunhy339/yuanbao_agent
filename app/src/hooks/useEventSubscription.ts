import { useEffect, useRef } from "react";
import type {
  AgentEventEnvelope,
  ChatMessageCompletePayload,
  ChatStatusPayload,
  ContentDeltaPayload,
  MessageDeltaPayload,
  MessageCreatedPayload,
  MessageCompletedPayload,
  MessageFailedPayload,
  PermissionRequestPayload,
  ToolResultPayload,
  ToolUseCompletePayload,
  SessionUpdatedPayload,
  SessionRecord,
  TaskRecord,
  TraceEventRecord,
} from "@shared";
import { RuntimeClient } from "../lib/runtimeClient";
import {
  formatAssistantFailureContent,
  isOperationalAssistantDelta,
  summarizeOperationalAssistantDelta,
} from "../state/chatMessages";
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
  appendAssistantToolResultMessage,
  appendAssistantProgressMessage,
  appendOrUpdateAssistantThinkingMessage,
  appendOrUpdatePermissionRequestMessage,
  completeAssistantToolUseMessage,
  completeChatCompatMessage,
  removeAssistantThinkingMessage,
  resolvePermissionRequestMessage,
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
    setError,
    sessionActiveTaskMapRef,
    childTaskIdsRef,
    pendingAssistantTokenEventsRef,
    assistantTokenFlushTimerRef,
  } = deps;

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

  function queueAssistantToken(event: AgentEventEnvelope) {
    const payload = event.payload as any;
    const delta = payload.delta ?? "";
    const displayDelta = isOperationalAssistantDelta(delta) ? summarizeOperationalAssistantDelta(delta) : delta;
    if (!displayDelta) return;
    pendingAssistantTokenEventsRef.current.push(event);
    if (assistantTokenFlushTimerRef.current !== null) return;
    assistantTokenFlushTimerRef.current = setTimeout(() => {
      assistantTokenFlushTimerRef.current = null;
      flushPendingAssistantTokens();
    }, 33);
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

  function appendOperationalAssistantProgress(event: AgentEventEnvelope, delta: string): boolean {
    const text = summarizeOperationalAssistantDelta(delta)?.trim();
    if (!text) {
      return false;
    }
    setChatMessages((current) =>
      appendAssistantProgressMessage(current, {
        sessionId: event.sessionId,
        taskId: event.taskId,
        content: text,
        now: event.ts,
        eventId: event.eventId,
      }),
    );
    return true;
  }

  useEffect(() => {
    let active = true;
    let dispose: (() => void) | undefined;

    runtimeClient
      .subscribeEvents((event) => {
        if (!active) {
          return;
        }

        if (event.type === "content_start") {
          return;
        }

        if (event.type === "content_delta") {
          if (!isChatVisibleEvent(event)) {
            return;
          }
          const payload = event.payload as ContentDeltaPayload;
          if (typeof payload.text === "string" && payload.text) {
            const text = payload.text;
            if (isOperationalAssistantDelta(text)) {
              appendOperationalAssistantProgress(event, text);
            } else if (text) {
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
          }
          if (typeof payload.toolInput === "string" && payload.toolInput) {
            const toolUseId =
              typeof payload.toolUseId === "string" && payload.toolUseId
                ? payload.toolUseId
                : `pending_${event.taskId}`;
            setChatMessages((current) =>
              appendOrUpdateAssistantToolInputDelta(current, {
                toolUseId,
                toolName: payload.toolName,
                sessionId: event.sessionId,
                taskId: event.taskId,
                delta: payload.toolInput!,
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
            completeAssistantToolUseMessage(current, {
              toolUseId: payload.toolUseId,
              toolName: payload.toolName,
              input: payload.input,
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
            appendAssistantToolResultMessage(current, {
              toolUseId: payload.toolUseId,
              toolName: payload.toolName,
              content: payload.content,
              isError: payload.isError,
              sessionId: event.sessionId,
              taskId: event.taskId,
              now: event.ts,
            }),
          );
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
              },
            ),
          );
          return;
        }

        if (event.type === "status") {
          if (!isChatVisibleEvent(event)) {
            return;
          }
          const payload = event.payload as ChatStatusPayload;
          if (payload.state === "idle") {
            setChatMessages((current) =>
              removeAssistantThinkingMessage(current, {
                sessionId: event.sessionId,
                taskId: event.taskId,
              }),
            );
            return;
          }
          if (["thinking", "tool_executing", "streaming"].includes(String(payload.state))) {
            setChatMessages((current) =>
              appendOrUpdateAssistantThinkingMessage(current, {
                sessionId: event.sessionId,
                taskId: event.taskId,
                state: payload.state,
                verb: payload.verb,
                now: event.ts,
              }),
            );
          }
          return;
        }

        if (event.type === "thinking") {
          if (!isChatVisibleEvent(event)) {
            return;
          }
          const payload = event.payload as { text?: unknown };
          setChatMessages((current) =>
            appendOrUpdateAssistantThinkingMessage(current, {
              sessionId: event.sessionId,
              taskId: event.taskId,
              state: "thinking",
              text: typeof payload.text === "string" ? payload.text : undefined,
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
          setChatMessages((current) =>
            appendOrUpdatePermissionRequestMessage(
              removeAssistantThinkingMessage(current, {
                sessionId: event.sessionId,
                taskId: event.taskId,
              }),
              {
                requestId: payload.requestId,
                toolName: payload.toolName,
                input: payload.input,
                description: payload.description,
                sessionId: event.sessionId,
                taskId: event.taskId,
                now: event.ts,
              },
            ),
          );
          return;
        }

        if (event.type === "approval.resolved") {
          const payload = event.payload as { approvalId?: unknown; decision?: unknown };
          if (typeof payload.approvalId === "string" && payload.approvalId.trim()) {
            const approvalId = payload.approvalId.trim();
            setChatMessages((current) =>
              resolvePermissionRequestMessage(current, {
                requestId: approvalId,
                decision: typeof payload.decision === "string" ? payload.decision : "approved",
                now: event.ts,
              }),
            );
          }
        }

        // --- New message lifecycle events (P1.3 / P1.4) ---
        // message.delta: streaming token, routed by messageId
        if (event.type === "message.delta") {
          if (isChatCompatPayload(event.payload)) {
            return;
          }
          if (!isChatVisibleEvent(event)) {
            return;
          }
          const payload = event.payload as MessageDeltaPayload;
          const delta = payload.delta ?? "";
          if (isOperationalAssistantDelta(delta)) {
            appendOperationalAssistantProgress(event, delta);
            return;
          }
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
                },
              ),
            );
          } else {
            // Fallback: no messageId, use legacy completion
            setChatMessages((current) =>
              removeAssistantThinkingMessage(completeAssistantMessage(current, event), {
                sessionId: event.sessionId,
                taskId: event.taskId,
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
                },
              ),
            );
            // Legacy fallback: only create failure bubble if no message.failed was received
            // (handled by message.failed event now)
          }
          if (event.type === "task.cancelled" && !isChildWorker) {
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
