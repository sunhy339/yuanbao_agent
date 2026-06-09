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
  isUsableTimelineTimestamp,
  isRuntimeInFlight,
  isTaskControllable,
  isVerificationCommand,
  normalizeComparableCommand,
} from "./utils";
import { MessageBubble } from "./MessageBubble";
import { RuntimeEventCard } from "./RuntimeEventCard";

type RawConversationActivityItem =
  | Extract<ConversationActivityItem, { kind: "message" }>
  | Extract<ConversationActivityItem, { kind: "runtime" }>;

const DEFAULT_THINKING_ACTIVITY_HINT = "正在等待模型或运行时返回第一段内容。";

const LOW_SIGNAL_TASK_STEP_PATTERNS = [
  /理解任务目标/,
  /分析任务目标/,
  /整理上下文/,
  /构建上下文/,
  /准备上下文/,
  /准备工具/,
  /规划任务/,
  /任务启动/,
  /等待模型/,
  /思考中/,
  /understand(?:ing)? (?:the )?task/i,
  /analy[sz](?:e|ing) (?:the )?task/i,
  /build(?:ing)? context/i,
  /prepar(?:e|ing) context/i,
  /prepar(?:e|ing) (?:the )?first tool/i,
  /plan(?:ning)? (?:the )?task/i,
  /waiting for (?:the )?model/i,
];

const WORKLOG_CONTEXT_COMMAND_RE =
  /^(git\s+(?:status|diff|log|show|branch|remote|tag|rev-parse|rev-list|ls-files|grep|blame)(?:\s|$)|rg(?:\s|$)|grep(?:\s|$)|ag(?:\s|$)|ack(?:\s|$)|findstr(?:\s|$)|select-string(?:\s|$)|find(?:\s|$)|where(?:\.exe)?(?:\s|$)|which(?:\s|$)|whereis(?:\s|$)|locate(?:\s|$)|cat(?:\s|$)|head(?:\s|$)|tail(?:\s|$)|less(?:\s|$)|more(?:\s|$)|type(?:\s|$)|wc(?:\s|$)|stat(?:\s|$)|file(?:\s|$)|strings(?:\s|$)|jq(?:\s|$)|awk(?:\s|$)|cut(?:\s|$)|sort(?:\s|$)|uniq(?:\s|$)|tr(?:\s|$)|get-content(?:\s|$)|gc(?:\s|$)|get-item(?:\s|$)|test-path(?:\s|$)|resolve-path(?:\s|$)|get-filehash(?:\s|$)|get-acl(?:\s|$)|format-hex(?:\s|$)|pwd(?:\s|$)|get-location(?:\s|$)|ls(?:\s|$)|dir(?:\s|$)|tree(?:\s|$)|du(?:\s|$)|get-childitem(?:\s|$)|gci(?:\s|$))/i;

const WORKLOG_VERIFICATION_COMMAND_RE =
  /\b(npm\s+(?:run\s+)?(?:test|typecheck|lint|build)|pnpm\s+(?:run\s+)?(?:test|typecheck|lint|build)|yarn\s+(?:test|typecheck|lint|build)|pytest|vitest|jest|playwright|tsc|ruff|eslint|mypy|cargo\s+(?:test|check|build)|go\s+test|dotnet\s+test)\b|\b(test|typecheck|lint|build|verify|check)\b/i;

const WORKLOG_ROUTINE_MUTATION_COMMAND_RE =
  /^git\s+(?:add|commit|reset\s+--soft|restore\s+--staged)(?:\s|$)/i;

function isUsableActivityTime(value?: number): value is number {
  return isUsableTimelineTimestamp(value);
}

function normalizeTaskStep(value?: string | null) {
  return compactText(value ?? "", 96).replace(/[。.!！…]+$/g, "").trim();
}

function isLowSignalTaskStep(value?: string | null) {
  const normalized = normalizeTaskStep(value);
  if (!normalized) {
    return true;
  }
  return LOW_SIGNAL_TASK_STEP_PATTERNS.some((pattern) => pattern.test(normalized));
}

function readCommandFromStructuredText(value: string) {
  try {
    const parsed = JSON.parse(value) as unknown;
    if (!parsed || typeof parsed !== "object") {
      return "";
    }
    const record = parsed as Record<string, unknown>;
    const command = record.command ?? record.cmd;
    return typeof command === "string" ? command.trim() : "";
  } catch {
    return "";
  }
}

function runtimeCommandText(item: RuntimeTimelineItem) {
  const candidates = [item.code, item.rawDetail, item.title]
    .map((value) => value?.trim())
    .filter((value): value is string => Boolean(value));

  for (const candidate of candidates) {
    const structuredCommand = readCommandFromStructuredText(candidate);
    if (structuredCommand) {
      return structuredCommand;
    }
    const normalized = normalizeComparableCommand(candidate);
    if (
      normalized &&
      (
        WORKLOG_CONTEXT_COMMAND_RE.test(normalized) ||
        WORKLOG_ROUTINE_MUTATION_COMMAND_RE.test(normalized) ||
        WORKLOG_VERIFICATION_COMMAND_RE.test(normalized) ||
        isVerificationCommand(normalized)
      )
    ) {
      return candidate;
    }
  }

  return candidates[0] ?? "";
}

function isCollapsibleCommandRuntimeItem(item: RuntimeTimelineItem) {
  const command = normalizeComparableCommand(runtimeCommandText(item));
  if (!command) {
    return false;
  }
  return (
    WORKLOG_CONTEXT_COMMAND_RE.test(command) ||
    WORKLOG_ROUTINE_MUTATION_COMMAND_RE.test(command)
  );
}

function isCollapsibleWorklogRuntimeItem(item: RuntimeTimelineItem) {
  if (!["tool", "command"].includes(item.kind)) {
    return false;
  }
  if (isRuntimeInFlight(item.status)) {
    return false;
  }
  if (["apply_patch", "write_file"].includes(item.toolName ?? "")) {
    return false;
  }
  if (["failed", "error", "rejected", "cancelled"].includes(item.status?.toLowerCase() ?? "")) {
    return false;
  }
  if (item.kind === "command") {
    return isCollapsibleCommandRuntimeItem(item);
  }
  return !["run_command"].includes(item.toolName ?? "");
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

function attachRuntimeTimesToMessages(
  messages: SessionWorkspaceMessage[],
  runtimeItems: RuntimeTimelineItem[],
) {
  if (!messages.length) {
    return runtimeItems;
  }
  const sortedMessageTimes = messages
    .map(getMessageActivitySortTime)
    .filter(isUsableActivityTime)
    .sort((left, right) => left - right);
  const needsSyntheticTime = runtimeItems.some((item) => !isUsableTimelineTimestamp(item.time));
  if (!sortedMessageTimes.length) {
    return needsSyntheticTime
      ? runtimeItems.map((runtime) =>
          isUsableTimelineTimestamp(runtime.time) ? runtime : { ...runtime, time: undefined, syntheticTime: true },
        )
      : runtimeItems;
  }
  if (!needsSyntheticTime) {
    return runtimeItems;
  }
  const assistantTimes = messages
    .filter((message) => message.role === "assistant")
    .map(getMessageActivitySortTime)
    .filter(isUsableActivityTime)
    .sort((left, right) => left - right);
  const anchorTime = assistantTimes.at(-1) ?? sortedMessageTimes.at(-1);
  if (anchorTime === undefined) {
    return runtimeItems;
  }
  return runtimeItems.map((runtime, index) =>
    isUsableTimelineTimestamp(runtime.time)
      ? runtime
      : { ...runtime, time: anchorTime + 0.001 * (index + 1), syntheticTime: true },
  );
}

function isPlainAssistantReply(message: SessionWorkspaceMessage) {
  return (
    message.role === "assistant" &&
    message.streaming !== true &&
    message.placeholder !== true &&
    message.kind !== "thinking" &&
    message.kind !== "status" &&
    !message.metadata?.kind
  );
}

function shouldPlaceAssistantAfterRuntime(message: SessionWorkspaceMessage, messages: SessionWorkspaceMessage[]) {
  if (!isPlainAssistantReply(message)) {
    return false;
  }
  const index = messages.findIndex((candidate) => candidate.id === message.id);
  if (index === -1) {
    return true;
  }
  const nextUserIndex = messages.findIndex((candidate, candidateIndex) => (
    candidateIndex > index && candidate.role === "user"
  ));
  const turnTail = messages.slice(index + 1, nextUserIndex === -1 ? undefined : nextUserIndex);
  return !turnTail.some(isPlainAssistantReply);
}

function buildRuntimeEndTimesByTask(runtimeItems: RuntimeTimelineItem[]) {
  const endTimes = new Map<string, number>();
  runtimeItems.forEach((item) => {
    const itemTime = item.time;
    if (!item.taskId || typeof itemTime !== "number" || !isUsableTimelineTimestamp(itemTime)) {
      return;
    }
    const current = endTimes.get(item.taskId) ?? Number.NEGATIVE_INFINITY;
    if (itemTime > current) {
      endTimes.set(item.taskId, itemTime);
    }
  });
  return endTimes;
}

function findSurroundingUserTimes(message: SessionWorkspaceMessage, messages: SessionWorkspaceMessage[]) {
  const baseTime = getMessageActivitySortTime(message);
  const usableUserTimes = messages
    .filter((candidate) => candidate.role === "user")
    .map(getMessageActivitySortTime)
    .filter(isUsableActivityTime)
    .sort((left, right) => left - right);
  const previousUserTime = usableUserTimes.filter((time) => !isUsableActivityTime(baseTime) || time <= baseTime).at(-1);
  const nextUserTime = usableUserTimes.find((time) => isUsableActivityTime(baseTime) && time > baseTime);
  return { previousUserTime, nextUserTime };
}

function getRuntimeEndTimeForMessageTurn(
  message: SessionWorkspaceMessage,
  messages: SessionWorkspaceMessage[],
  runtimeItems: RuntimeTimelineItem[],
) {
  if (message.taskId) {
    return undefined;
  }
  const { previousUserTime, nextUserTime } = findSurroundingUserTimes(message, messages);
  let endTime: number | undefined;
  runtimeItems.forEach((item) => {
    if (item.syntheticTime) {
      return;
    }
    const itemTime = item.time;
    if (!isUsableActivityTime(itemTime)) {
      return;
    }
    if (previousUserTime !== undefined && itemTime < previousUserTime) {
      return;
    }
    if (nextUserTime !== undefined && itemTime >= nextUserTime) {
      return;
    }
    if (endTime === undefined || itemTime > endTime) {
      endTime = itemTime;
    }
  });
  return endTime;
}

function getActivityMessageTime(
  message: SessionWorkspaceMessage,
  messages: SessionWorkspaceMessage[],
  runtimeEndTimesByTask: Map<string, number>,
) {
  const baseTime = getMessageActivitySortTime(message);
  if (!shouldPlaceAssistantAfterRuntime(message, messages)) {
    return baseTime;
  }
  const runtimeEndTime = message.taskId ? runtimeEndTimesByTask.get(message.taskId) : undefined;
  if (!isUsableActivityTime(baseTime)) {
    return runtimeEndTime !== undefined ? runtimeEndTime + 0.001 : baseTime;
  }
  if (runtimeEndTime !== undefined && runtimeEndTime >= baseTime) {
    return runtimeEndTime + 0.001;
  }
  return baseTime;
}

function getActivityMessageTimeWithRuntime(
  message: SessionWorkspaceMessage,
  messages: SessionWorkspaceMessage[],
  runtimeItems: RuntimeTimelineItem[],
  runtimeEndTimesByTask: Map<string, number>,
) {
  const baseTime = getActivityMessageTime(message, messages, runtimeEndTimesByTask);
  if (!shouldPlaceAssistantAfterRuntime(message, messages)) {
    return baseTime;
  }
  const runtimeEndTime =
    (message.taskId ? runtimeEndTimesByTask.get(message.taskId) : undefined) ??
    getRuntimeEndTimeForMessageTurn(message, messages, runtimeItems);
  if (runtimeEndTime === undefined) {
    return baseTime;
  }
  if (!isUsableActivityTime(baseTime) || runtimeEndTime >= baseTime) {
    return runtimeEndTime + 0.001;
  }
  return baseTime;
}

export function buildConversationActivity(
  messages: SessionWorkspaceMessage[],
  runtimeItems: RuntimeTimelineItem[],
): ConversationActivityItem[] {
  const timedRuntimeItems = attachRuntimeTimesToMessages(messages, runtimeItems);
  const runtimeEndTimesByTask = buildRuntimeEndTimesByTask(timedRuntimeItems);
  const activity: RawConversationActivityItem[] = [
    ...messages.map((message, index) => ({
      id: `message:${message.id}`,
      kind: "message" as const,
      order: index,
      time: getActivityMessageTimeWithRuntime(message, messages, timedRuntimeItems, runtimeEndTimesByTask),
      message,
    })),
    ...timedRuntimeItems.map((runtime, index) => ({
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
  const status = getProcessStatusLabel(item.status);
  return compactText(
    compactMeta([
      item.summary,
      status && status !== "已记录" ? status : undefined,
      item.durationMs !== undefined ? formatDuration(item.durationMs) ?? undefined : undefined,
    ]).join(" · "),
    160,
  );
}

function isDiagnosticRuntimeStatus(status?: string | null) {
  return ["failed", "error", "blocked", "cancelled", "rejected"].includes(status?.toLowerCase() ?? "");
}

function worklogRowOutput(item: RuntimeTimelineItem) {
  if (item.kind === "command") {
    return buildCommandOutput(item);
  }
  if (!isDiagnosticRuntimeStatus(item.status)) {
    return "";
  }
  return item.rawDetail || item.code || "";
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
  const currentStep = normalizeTaskStep(activeTask.currentStep);
  if (currentStep && !isLowSignalTaskStep(currentStep)) {
    return `正在处理：${currentStep}`;
  }
  return "";
}

function buildThinkingActivityHint(items: ConversationActivityItem[], activeTask?: SessionWorkspaceActiveTask | null) {
  const runtimeItems = collectActivityRuntimeItems(items).sort((left, right) => (right.time ?? 0) - (left.time ?? 0));
  const priority =
    runtimeItems.find((item) => isRuntimeInFlight(item.status)) ??
    runtimeItems.find((item) => item.kind === "approval" && ["pending", "waiting"].includes(item.status ?? "")) ??
    runtimeItems[0];

  if (priority) {
    return buildRuntimeActivityHint(priority);
  }

  const taskHint = buildTaskActivityHint(activeTask);
  if (taskHint) {
    return taskHint;
  }

  return DEFAULT_THINKING_ACTIVITY_HINT;
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
          const output = worklogRowOutput(item);
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
  const activeTaskIsRunning = isTaskControllable(activeTask?.status);
  const hasStreamingAssistantContent = messages.some((message) => message.streaming && !message.placeholder);
  const hasThinkingPlaceholder = messages.some((message) => message.streaming && message.placeholder);
  const thinkingActivityHint = buildThinkingActivityHint(items, activeTask);
  const hasVisibleRuntimeActivity = items.some((item) => item.kind === "runtime" || item.kind === "worklog");
  const showProgressNote =
    thinkingActivityHint !== DEFAULT_THINKING_ACTIVITY_HINT &&
    activeTaskIsRunning &&
    !hasVisibleRuntimeActivity &&
    !hasStreamingAssistantContent &&
    (messagesLoading || hasThinkingPlaceholder || Boolean(activeTask));

  return (
    <div className="conversation-activity" aria-label="会话活动">
      {items.map((item) =>
        item.kind === "message" ? (
          <Fragment key={item.id}>
            <MessageBubble
              message={item.message}
              activityHint={item.message.streaming && item.message.placeholder ? thinkingActivityHint : undefined}
              onApprove={onApprove}
              onReject={onReject}
              busyId={busyId}
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
      {showProgressNote ? (
        <article className="runtime-progress-note" aria-label="运行进展">
          <span className="runtime-progress-note-dot" aria-hidden="true" />
          <div>
            <strong>{thinkingActivityHint}</strong>
            <small>后台还没有返回可展示的工具或命令结果；一旦进入审批、改动、命令或验证，会显示在这里。</small>
          </div>
        </article>
      ) : null}
    </div>
  );
});
