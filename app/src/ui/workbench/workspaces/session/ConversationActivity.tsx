import { Fragment, memo } from "react";
import type { ConversationActivityItem, SessionWorkspaceMessage, SessionWorkspaceActiveTask } from "./types";
import { isTaskControllable, getMessageActivitySortTime } from "./utils";
import { MessageBubble } from "./MessageBubble";
import { RuntimeEventCard } from "./RuntimeEventCard";
import { ConversationLivePill } from "./ConversationLivePill";

export function buildConversationActivity(
  messages: SessionWorkspaceMessage[],
  runtimeItems: import("./types").RuntimeTimelineItem[],
): ConversationActivityItem[] {
  const activity: ConversationActivityItem[] = [
    ...messages.map((message, index) => ({
      id: `message:${message.id}`,
      kind: "message" as const,
      order: index,
      time: getMessageActivitySortTime(message),
      message,
    })),
    ...runtimeItems.map((runtime, index) => ({
      id: `runtime:${runtime.id}`,
      kind: "runtime" as const,
      order: messages.length + index,
      time: runtime.time,
      runtime,
    })),
  ];

  return activity.sort((left, right) => {
    if (left.time !== undefined && right.time !== undefined && left.time !== right.time) {
      return left.time - right.time;
    }
    if (left.time !== undefined && right.time === undefined) {
      return -1;
    }
    if (left.time === undefined && right.time !== undefined) {
      return 1;
    }
    return left.order - right.order;
  });
}

export const ConversationActivity = memo(function ConversationActivity({
  items,
  messages,
  activeTask,
  messagesLoading,
  onApprove,
  onReject,
  onLoadPatch,
  onCopyPatchPath,
  onCopyRuntimeText,
  onRefreshCommandJob,
  onStopCommandJob,
  busyId,
}: {
  items: ConversationActivityItem[];
  messages: SessionWorkspaceMessage[];
  activeTask?: SessionWorkspaceActiveTask | null;
  messagesLoading?: boolean;
  onApprove?(approvalId: string): void | Promise<void>;
  onReject?(approvalId: string): void | Promise<void>;
  onLoadPatch?(patchId: string): void | Promise<void>;
  onCopyPatchPath?(patchId: string, path: string): void | Promise<void>;
  onCopyRuntimeText?(label: string, text: string): void | Promise<void>;
  onRefreshCommandJob?(commandId: string): void | Promise<void>;
  onStopCommandJob?(commandId: string): void | Promise<void>;
  busyId?: string | null;
}) {
  const livePill = (
    <div className="conversation-live-row">
      <ConversationLivePill messages={messages} activeTask={activeTask} messagesLoading={messagesLoading} />
    </div>
  );
  const activeTaskIsRunning = isTaskControllable(activeTask?.status);
  const hasStreamingAssistantContent = messages.some((message) => message.streaming && !message.placeholder);
  const hasThinkingPlaceholder = messages.some((message) => message.streaming && message.placeholder);
  const showLivePill = Boolean(
    !hasStreamingAssistantContent &&
      (messages.length || activeTask || messagesLoading || activeTaskIsRunning || hasThinkingPlaceholder),
  );

  return (
    <div className="conversation-activity" aria-label="会话活动">
      {items.map((item) =>
        item.kind === "message" ? (
          <Fragment key={item.id}>
            <MessageBubble message={item.message} />
          </Fragment>
        ) : (
          <RuntimeEventCard
            item={item.runtime}
            key={item.id}
            onApprove={onApprove}
            onReject={onReject}
            onLoadPatch={onLoadPatch}
            onCopyPatchPath={onCopyPatchPath}
            onCopyRuntimeText={onCopyRuntimeText}
            onRefreshCommandJob={onRefreshCommandJob}
            onStopCommandJob={onStopCommandJob}
            busyId={busyId}
          />
        ),
      )}
      {showLivePill ? livePill : null}
    </div>
  );
});
