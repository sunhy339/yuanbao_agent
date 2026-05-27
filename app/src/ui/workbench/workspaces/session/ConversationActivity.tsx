import { Fragment, memo, useState } from "react";
import { Button, StatusBadge } from "../../../v2/components/ui";
import type {
  ConversationActivityItem,
  RuntimeTimelineItem,
  SessionWorkspaceMessage,
  SessionWorkspaceActiveTask,
} from "./types";
import {
  buildCommandOutput,
  compactMeta,
  compactText,
  formatDuration,
  getMessageActivitySortTime,
  getProcessStatusLabel,
  getStatusTone,
  isRuntimeInFlight,
  isTaskControllable,
} from "./utils";
import { MessageBubble } from "./MessageBubble";
import { RuntimeEventCard } from "./RuntimeEventCard";
import { ConversationLivePill } from "./ConversationLivePill";

type RawConversationActivityItem =
  | Extract<ConversationActivityItem, { kind: "message" }>
  | Extract<ConversationActivityItem, { kind: "runtime" }>;

function isCollapsibleWorklogRuntimeItem(item: RuntimeTimelineItem) {
  if (item.kind !== "command" && item.kind !== "tool") {
    return false;
  }
  if (isRuntimeInFlight(item.status)) {
    return false;
  }
  return !["failed", "error", "rejected", "cancelled"].includes(item.status?.toLowerCase() ?? "");
}

function sortActivityItems(items: RawConversationActivityItem[]) {
  return items.sort((left, right) => {
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

function collapseRuntimeWorklogItems(items: RawConversationActivityItem[]): ConversationActivityItem[] {
  const collapsed: ConversationActivityItem[] = [];
  let buffer: Array<Extract<RawConversationActivityItem, { kind: "runtime" }>> = [];

  const flush = () => {
    if (buffer.length === 0) {
      return;
    }
    const first = buffer[0];
    collapsed.push({
      id: `worklog:${buffer.map((item) => item.runtime.id).join(":")}`,
      kind: "worklog",
      order: first.order,
      time: first.time,
      runtimeItems: buffer.map((item) => item.runtime),
    });
    buffer = [];
  };

  for (const item of items) {
    if (item.kind === "runtime" && isCollapsibleWorklogRuntimeItem(item.runtime)) {
      buffer.push(item);
      continue;
    }

    flush();
    collapsed.push(item);
  }

  flush();
  return collapsed;
}

export function buildConversationActivity(
  messages: SessionWorkspaceMessage[],
  runtimeItems: RuntimeTimelineItem[],
): ConversationActivityItem[] {
  const activity: RawConversationActivityItem[] = [
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

  return collapseRuntimeWorklogItems(sortActivityItems(activity));
}

function getWorklogStatus(items: RuntimeTimelineItem[]) {
  if (items.some((item) => ["failed", "error"].includes(item.status?.toLowerCase() ?? ""))) {
    return "failed";
  }
  if (items.some((item) => isRuntimeInFlight(item.status))) {
    return "running";
  }
  if (items.every((item) => ["completed", "succeeded", "passed"].includes(item.status?.toLowerCase() ?? ""))) {
    return "completed";
  }
  return "recorded";
}

function buildWorklogTitle(items: RuntimeTimelineItem[]) {
  const commandCount = items.filter((item) => item.kind === "command").length;
  const toolCount = items.filter((item) => item.kind === "tool").length;
  const failedCount = items.filter((item) => ["failed", "error"].includes(item.status?.toLowerCase() ?? "")).length;
  const runningCount = items.filter((item) => isRuntimeInFlight(item.status)).length;

  if (failedCount) {
    return `${failedCount} 项执行失败`;
  }
  if (runningCount) {
    return `正在执行 ${runningCount} 项工作`;
  }
  if (commandCount && toolCount) {
    return `已处理 ${commandCount} 条命令、${toolCount} 个工具`;
  }
  if (commandCount) {
    return commandCount === 1 ? "已运行 1 条命令" : `已运行 ${commandCount} 条命令`;
  }
  return toolCount === 1 ? "已调用 1 个工具" : `已调用 ${toolCount} 个工具`;
}

function buildWorklogSummary(items: RuntimeTimelineItem[]) {
  const snippets = Array.from(new Set(items.map((item) => compactText(item.title, 56)).filter(Boolean)))
    .slice(0, 3);
  if (!snippets.length) {
    return "工具和命令结果已记录，可展开查看细节。";
  }
  const suffix = items.length > snippets.length ? `，另有 ${items.length - snippets.length} 项` : "";
  return `${snippets.join("；")}${suffix}`;
}

function buildRuntimeRowSummary(item: RuntimeTimelineItem) {
  return compactText(
    compactMeta([
      item.summary,
      getProcessStatusLabel(item.status),
      item.durationMs !== undefined ? formatDuration(item.durationMs) ?? undefined : undefined,
    ]).join(" · "),
    160,
  );
}

function collectActivityRuntimeItems(items: ConversationActivityItem[]) {
  const runtimeItems: RuntimeTimelineItem[] = [];
  for (const item of items) {
    if (item.kind === "runtime") {
      runtimeItems.push(item.runtime);
    } else if (item.kind === "worklog") {
      runtimeItems.push(...item.runtimeItems);
    }
  }
  return runtimeItems;
}

function buildRuntimeActivityHint(item: RuntimeTimelineItem) {
  const title = compactText(item.title, 96);
  const status = item.status?.toLowerCase() ?? "";
  if (isRuntimeInFlight(item.status)) {
    if (item.kind === "command") {
      return `正在运行命令：${title}`;
    }
    if (item.kind === "tool") {
      return `正在使用工具：${title}`;
    }
    if (item.kind === "patch") {
      return `正在处理文件改动：${title}`;
    }
    return `正在处理：${title}`;
  }
  if (item.kind === "approval" && ["pending", "waiting"].includes(status)) {
    return `等待审批：${title}`;
  }
  if (["failed", "error", "rejected"].includes(status)) {
    return `最近失败：${title}，需要继续处理。`;
  }
  if (item.kind === "patch") {
    return `最近改动：${title}`;
  }
  if (item.kind === "command") {
    return `最近运行：${title}`;
  }
  return `最近活动：${title}`;
}

function buildTaskActivityHint(activeTask?: SessionWorkspaceActiveTask | null) {
  if (!activeTask || !isTaskControllable(activeTask.status)) {
    return "";
  }
  const currentStep = compactText(activeTask.currentStep, 96);
  if (currentStep) {
    return `任务仍在运行：${currentStep}`;
  }
  return `任务仍在运行：${compactText(activeTask.goal || getProcessStatusLabel(activeTask.status), 96)}`;
}

function buildThinkingActivityHint(items: ConversationActivityItem[], activeTask?: SessionWorkspaceActiveTask | null) {
  const taskHint = buildTaskActivityHint(activeTask);
  if (taskHint) {
    return taskHint;
  }

  const runtimeItems = collectActivityRuntimeItems(items).sort((left, right) => (right.time ?? 0) - (left.time ?? 0));
  const priority =
    runtimeItems.find((item) => isRuntimeInFlight(item.status)) ??
    runtimeItems.find((item) => item.kind === "approval" && ["pending", "waiting"].includes(item.status ?? "")) ??
    runtimeItems[0];

  if (priority) {
    return buildRuntimeActivityHint(priority);
  }

  return "正在等待模型或运行时返回第一段内容。";
}

function RuntimeWorklogCard({
  items,
  onCopyRuntimeText,
  onRefreshCommandJob,
  onStopCommandJob,
  busyId,
}: {
  items: RuntimeTimelineItem[];
  onCopyRuntimeText?(label: string, text: string): void | Promise<void>;
  onRefreshCommandJob?(commandId: string): void | Promise<void>;
  onStopCommandJob?(commandId: string): void | Promise<void>;
  busyId?: string | null;
}) {
  const [expanded, setExpanded] = useState(false);
  const status = getWorklogStatus(items);
  const visibleItems = expanded ? items : items.slice(0, 4);

  return (
    <article
      aria-label="运行摘要"
      className="runtime-worklog-card"
      data-activity-kind="runtime-worklog"
      data-status={status}
    >
      <button
        type="button"
        className="runtime-worklog-head"
        aria-expanded={expanded}
        onClick={() => setExpanded((current) => !current)}
      >
        <span className="runtime-worklog-dot" aria-hidden="true" />
        <span className="runtime-worklog-copy">
          <strong>{buildWorklogTitle(items)}</strong>
          <small>{buildWorklogSummary(items)}</small>
        </span>
        <StatusBadge label={getProcessStatusLabel(status)} tone={getStatusTone(status)} compact />
        <i aria-hidden="true">{expanded ? "^" : "v"}</i>
      </button>
      <div className="runtime-worklog-list">
        {visibleItems.map((item) => {
          const output = item.kind === "command" ? buildCommandOutput(item) : item.rawDetail || item.code || "";
          const canCopy = Boolean(onCopyRuntimeText && output.trim());
          const canRefresh = Boolean(item.kind === "command" && item.sourceId && onRefreshCommandJob);
          const canStop = Boolean(
            item.kind === "command" &&
              item.sourceId &&
              onStopCommandJob &&
              ["running", "started"].includes(item.status ?? ""),
          );
          const isBusy = item.sourceId ? busyId === item.sourceId : false;

          return (
            <section className="runtime-worklog-row" data-status={item.status ?? "recorded"} key={item.id}>
              <span className="runtime-worklog-row-dot" aria-hidden="true" />
              <div className="runtime-worklog-row-main">
                <strong>{item.title}</strong>
                {buildRuntimeRowSummary(item) ? <small>{buildRuntimeRowSummary(item)}</small> : null}
                {expanded && output ? <pre>{output}</pre> : null}
              </div>
              <StatusBadge label={getProcessStatusLabel(item.status)} tone={getStatusTone(item.status)} compact />
              {expanded && (canCopy || canRefresh || canStop) ? (
                <div className="runtime-worklog-row-actions">
                  {canCopy ? (
                    <Button
                      size="xs"
                      variant="secondary"
                      onClick={() => {
                        void onCopyRuntimeText?.("运行输出", output);
                      }}
                    >
                      复制
                    </Button>
                  ) : null}
                  {canRefresh ? (
                    <Button
                      size="xs"
                      variant="secondary"
                      loading={isBusy}
                      onClick={() => {
                        void onRefreshCommandJob?.(item.sourceId ?? "");
                      }}
                    >
                      刷新
                    </Button>
                  ) : null}
                  {canStop ? (
                    <Button
                      size="xs"
                      variant="danger"
                      loading={isBusy}
                      onClick={() => {
                        void onStopCommandJob?.(item.sourceId ?? "");
                      }}
                    >
                      停止
                    </Button>
                  ) : null}
                </div>
              ) : null}
            </section>
          );
        })}
      </div>
      {!expanded && items.length > visibleItems.length ? (
        <button type="button" className="runtime-worklog-more" onClick={() => setExpanded(true)}>
          展开另外 {items.length - visibleItems.length} 项
        </button>
      ) : null}
    </article>
  );
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
  const thinkingActivityHint = buildThinkingActivityHint(items, activeTask);
  const hasVisibleRuntimeActivity = items.some((item) => item.kind === "runtime" || item.kind === "worklog");
  const showLivePill = Boolean(
    !hasStreamingAssistantContent &&
      (messages.length || activeTask || messagesLoading || activeTaskIsRunning || hasThinkingPlaceholder),
  );

  return (
    <div className="conversation-activity" aria-label="会话活动">
      {items.map((item) =>
        item.kind === "message" ? (
          <Fragment key={item.id}>
            <MessageBubble
              message={item.message}
              activityHint={item.message.streaming && item.message.placeholder ? thinkingActivityHint : undefined}
            />
          </Fragment>
        ) : item.kind === "worklog" ? (
          <RuntimeWorklogCard
            key={item.id}
            items={item.runtimeItems}
            onCopyRuntimeText={onCopyRuntimeText}
            onRefreshCommandJob={onRefreshCommandJob}
            onStopCommandJob={onStopCommandJob}
            busyId={busyId}
          />
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
      {showLivePill && activeTaskIsRunning && !hasVisibleRuntimeActivity ? (
        <article className="runtime-progress-note" aria-label="运行进展">
          <span className="runtime-progress-note-dot" aria-hidden="true" />
          <div>
            <strong>{thinkingActivityHint}</strong>
            <small>后台还没有返回可展示的工具或命令结果；一旦进入审批、改动、命令或验证，会显示在这里。</small>
          </div>
        </article>
      ) : null}
      {showLivePill ? livePill : null}
    </div>
  );
});
