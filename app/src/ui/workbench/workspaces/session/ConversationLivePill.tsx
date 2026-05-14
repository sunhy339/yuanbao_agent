import { memo, useState } from "react";
import type { SessionWorkspaceMessage, SessionWorkspaceActiveTask } from "./types";
import { useTickWhen } from "./useTick";
import {
  isTaskControllable,
  formatElapsedTime,
  getConversationStartedAt,
  getConversationFinishedAt,
  getConversationLiveLabel,
} from "./utils";

export const ConversationLivePill = memo(function ConversationLivePill({
  messages,
  activeTask,
  messagesLoading,
}: {
  messages: SessionWorkspaceMessage[];
  activeTask?: SessionWorkspaceActiveTask | null;
  messagesLoading?: boolean;
}) {
  const hasStreamingMessage = messages.some((message) => message.streaming);
  const isRunning = Boolean(messagesLoading || hasStreamingMessage || isTaskControllable(activeTask?.status));
  const shouldShow = Boolean(messages.length || activeTask || isRunning);
  const [fallbackStartedAt] = useState(() => Date.now());
  const startedAt = getConversationStartedAt(messages, activeTask, fallbackStartedAt);
  const now = useTickWhen(isRunning);

  if (!shouldShow) {
    return null;
  }

  const finishedAt = isRunning ? now : getConversationFinishedAt(messages, startedAt, activeTask);
  const elapsed = formatElapsedTime(finishedAt - startedAt);
  const label = getConversationLiveLabel({ messagesLoading, hasStreamingMessage, activeTask, isRunning });

  return (
    <div
      className="session-conversation-live-pill"
      data-state={isRunning ? "running" : "completed"}
      aria-label={`本轮对话${label}${elapsed}`}
      title="本轮对话持续时间"
    >
      <span className="session-conversation-live-spark" aria-hidden="true" />
      <strong>{label}</strong>
      <time>{elapsed}</time>
    </div>
  );
});
