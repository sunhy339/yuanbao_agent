import type { SessionWorkspaceMessage, SessionWorkspaceActiveTask, SessionWorkspaceToolCall, SessionWorkspaceContextPreview, RuntimeTimelineItem, DiffLine } from "./types";
import { formatStatusLabel } from "../../../copy";
import { formatTimestamp } from "../../../../lib/formatUtils";

export const THINKING_STALLED_MS = 45_000;

export function getRoleLabel(role: SessionWorkspaceMessage["role"]) {
  switch (role) {
    case "assistant":
      return "助手";
    case "system":
      return "系统";
    case "tool":
      return "工具";
    case "user":
      return "用户";
    default:
      return "消息";
  }
}

export function formatDuration(durationMs?: number) {
  if (durationMs === undefined) {
    return null;
  }

  if (durationMs < 1000) {
    return `${durationMs}ms`;
  }

  return `${(durationMs / 1000).toFixed(1)}s`;
}

export function formatElapsedTime(durationMs: number) {
  const totalSeconds = Math.max(0, Math.floor(durationMs / 1000));
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  if (hours > 0) {
    return `${hours}h ${minutes}m`;
  }
  if (minutes > 0) {
    return `${minutes}m ${seconds}s`;
  }
  return `${seconds}s`;
}

export function compactMeta(values: Array<string | null | undefined>) {
  return values.filter((value): value is string => Boolean(value));
}

export function compactList(values: string[], limit = 3) {
  if (values.length <= limit) {
    return values.join(", ");
  }

  return `${values.slice(0, limit).join(", ")} +${values.length - limit}`;
}

export function compactText(value: string | null | undefined, maxChars = 240) {
  const text = typeof value === "string" ? value.trim() : "";
  if (!text) {
    return "";
  }
  if (text.length <= maxChars) {
    return text;
  }
  return `${text.slice(0, Math.max(1, maxChars - 14)).trimEnd()} [已截断]`;
}

export const MAX_RENDERED_DIFF_LINES = 500;
export const READ_FILE_REPEAT_WINDOW_MS = 2 * 60 * 1000;

export function parseUnifiedDiff(diffText: string): DiffLine[] {
  const lines: DiffLine[] = [];
  const rawLines = diffText.split("\n");
  for (const raw of rawLines.slice(0, MAX_RENDERED_DIFF_LINES)) {
    if (raw.startsWith("+++") || raw.startsWith("---")) {
      lines.push({ type: "header", content: raw });
    } else if (raw.startsWith("@@")) {
      lines.push({ type: "header", content: raw });
    } else if (raw.startsWith("+")) {
      lines.push({ type: "add", content: raw.slice(1) });
    } else if (raw.startsWith("-")) {
      lines.push({ type: "remove", content: raw.slice(1) });
    } else {
      lines.push({ type: "context", content: raw.startsWith(" ") ? raw.slice(1) : raw });
    }
  }
  if (rawLines.length > MAX_RENDERED_DIFF_LINES) {
    lines.push({
      type: "header",
      content: `[差异已截断：仅显示 ${rawLines.length} 行中的前 ${MAX_RENDERED_DIFF_LINES} 行]`,
    });
  }
  return lines;
}

export function stripSectionLabel(value: string, label: string) {
  const normalizedLabel = `${label}:`;
  return value.startsWith(normalizedLabel) ? value.slice(normalizedLabel.length).trim() : value;
}

export function formatTokenBudget(stats?: SessionWorkspaceContextPreview["budgetStats"]) {
  if (!stats) {
    return null;
  }

  const used = stats.estimatedInputTokens ?? stats.estimatedTokens;
  const max = stats.maxContextTokens;
  if (used === undefined || used === null || max === undefined || max === null) {
    return null;
  }
  return `${used}/${max} 令牌`;
}

export function formatSignedCount(value: number | undefined, prefix: string) {
  if (value === undefined) {
    return null;
  }

  return `${prefix}${value}`;
}

export function formatTaskFileChange(file: NonNullable<SessionWorkspaceActiveTask["changedFiles"]>[number]) {
  const changeStats = compactMeta([formatSignedCount(file.additions, "+"), formatSignedCount(file.deletions, "-")]).join(" ");
  const status = formatStatusLabel(file.status ?? "changed");
  const suffix = compactMeta([changeStats, file.reason]).join(" - ");
  return `${status} ${file.path}${suffix ? ` - ${suffix}` : ""}`;
}

export function formatTaskCommand(command: NonNullable<SessionWorkspaceActiveTask["commands"]>[number]) {
  const meta = compactMeta([
    command.status ? formatStatusLabel(command.status) : null,
    command.exitCode !== undefined && command.exitCode !== null ? `退出码 ${command.exitCode}` : null,
    formatDuration(command.durationMs ?? undefined),
    command.cwd,
    command.background ? "后台" : null,
  ]);
  return `${command.command}${meta.length ? ` - ${meta.join(" - ")}` : ""}`;
}

export function formatTaskVerification(record: NonNullable<SessionWorkspaceActiveTask["verification"]>[number]) {
  const label = record.command ?? record.id ?? "验证";
  const meta = compactMeta([
    formatStatusLabel(record.status),
    record.exitCode !== undefined && record.exitCode !== null ? `退出码 ${record.exitCode}` : null,
    formatDuration(record.durationMs ?? undefined),
    record.summary,
  ]);
  return `${label}${meta.length ? ` - ${meta.join(" - ")}` : ""}`;
}

export function parseRuntimeJsonRecord(value?: string): Record<string, unknown> | null {
  if (!value) {
    return null;
  }

  try {
    const parsed = JSON.parse(value);
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? (parsed as Record<string, unknown>) : null;
  } catch {
    return null;
  }
}

export function readRuntimeString(record: Record<string, unknown> | null, keys: string[]) {
  if (!record) {
    return undefined;
  }

  for (const key of keys) {
    const value = record[key];
    if (typeof value === "string" && value.trim()) {
      return value.trim();
    }
    if (typeof value === "number" && Number.isFinite(value)) {
      return String(value);
    }
  }

  return undefined;
}

export function normalizeRuntimeComparableString(value: string | undefined) {
  return value?.trim().replace(/\\/g, "/").replace(/\/+$/, "").toLowerCase();
}

export function getRepeatedReadFileKey(toolCall: SessionWorkspaceToolCall) {
  if (toolCall.toolName !== "read_file") {
    return null;
  }
  const inputRecord = parseRuntimeJsonRecord(toolCall.rawInput);
  const path = normalizeRuntimeComparableString(readRuntimeString(inputRecord, ["path", "file"]));
  if (!path) {
    return null;
  }
  const workspaceRoot = normalizeRuntimeComparableString(readRuntimeString(inputRecord, ["workspaceRoot", "workspace_root", "root"]));
  const encoding = readRuntimeString(inputRecord, ["encoding"]) ?? "utf-8";
  const maxBytes = readRuntimeString(inputRecord, ["max_bytes", "maxBytes"]) ?? "";
  return [workspaceRoot ?? "", path, encoding.toLowerCase(), maxBytes].join("|");
}

export function compactRepeatedReadFileCalls(toolCalls: SessionWorkspaceToolCall[]) {
  const sorted = [...toolCalls].sort((left, right) => {
    const leftTime = left.time ?? Number.MAX_SAFE_INTEGER;
    const rightTime = right.time ?? Number.MAX_SAFE_INTEGER;
    return leftTime - rightTime || left.id.localeCompare(right.id);
  });
  const visible: SessionWorkspaceToolCall[] = [];
  const lastCompletedReadByKey = new Map<string, number>();

  for (const toolCall of sorted) {
    if (["apply_patch", "write_file", "run_command", "task"].includes(toolCall.toolName)) {
      lastCompletedReadByKey.clear();
    }

    const key = getRepeatedReadFileKey(toolCall);
    const time = toolCall.time;
    const status = toolCall.status.toLowerCase();
    const completed = ["completed", "succeeded", "passed"].includes(status);

    if (key && completed && time !== undefined) {
      const previousTime = lastCompletedReadByKey.get(key);
      if (previousTime !== undefined && time - previousTime <= READ_FILE_REPEAT_WINDOW_MS) {
        continue;
      }
      lastCompletedReadByKey.set(key, time);
    }

    visible.push(toolCall);
  }

  return visible;
}

export function summarizeRuntimeOutput(value?: string) {
  const text = compactText(value, 180);
  if (!text) {
    return undefined;
  }

  const firstLine = text
    .split(/\r?\n/)
    .map((line) => line.trim())
    .find(Boolean);
  return firstLine ? compactText(firstLine, 160) : text;
}

// ── Status & Label Helpers ─────────────────────────────────────────
export function getRuntimeKindLabel(kind: RuntimeTimelineItem["kind"]) {
  if (kind === "command") {
    return "命令";
  }
  if (kind === "approval") {
    return "审批";
  }
  if (kind === "tool") {
    return "工具";
  }
  if (kind === "patch") {
    return "改动";
  }
  if (kind === "task") {
    return "任务";
  }
  if (kind === "memory") {
    return "记忆";
  }
  return kind;
}

export function getStatusTone(status?: string): "neutral" | "primary" | "success" | "warning" | "danger" | "info" {
  if (!status) {
    return "neutral";
  }
  const normalized = status.toLowerCase();
  if (["completed", "succeeded", "approved", "applied", "passed"].includes(normalized)) {
    return "success";
  }
  if (["running", "started", "planning", "verifying"].includes(normalized)) {
    return "info";
  }
  if (["pending", "queued", "waiting_approval"].includes(normalized)) {
    return "warning";
  }
  if (["failed", "error", "cancelled", "rejected"].includes(normalized)) {
    return "danger";
  }
  return "neutral";
}

export function isTaskControllable(status?: string) {
  return Boolean(status && ["running", "planning", "verifying", "waiting_approval", "queued"].includes(status));
}

export function isRuntimeInFlight(status?: string) {
  const normalized = status?.toLowerCase();
  return Boolean(
    normalized &&
      ["running", "started", "planning", "verifying", "pending", "queued", "waiting_approval"].includes(normalized),
  );
}

export function getProcessStatusLabel(status?: string) {
  const normalized = status?.toLowerCase();
  if (!normalized) return "已记录";
  if (["completed", "succeeded", "approved", "applied", "passed"].includes(normalized)) return "已完成";
  if (["running", "started", "planning", "verifying"].includes(normalized)) return "进行中";
  if (["pending", "queued", "waiting_approval"].includes(normalized)) return "等待中";
  if (["failed", "error", "rejected"].includes(normalized)) return "失败";
  if (normalized === "cancelled") return "已取消";
  if (normalized === "skipped") return "已跳过";
  return formatStatusLabel(status);
}

export function getProcessTimeLabel(item: RuntimeTimelineItem, now: number, fallbackStartedAt: number) {
  if (isRuntimeInFlight(item.status)) {
    return formatElapsedTime(now - (item.time ?? fallbackStartedAt));
  }
  const duration = formatDuration(item.durationMs);
  const timestamp = formatTimestamp(item.time);
  if (duration && timestamp) {
    return `${duration} · ${timestamp}`;
  }
  return duration ?? timestamp ?? "刚刚";
}

export function getLiveTaskLabel(activeTask?: SessionWorkspaceActiveTask | null) {
  const status = activeTask?.status?.toLowerCase();
  if (status === "waiting_approval") return "等待审批...";
  if (status === "queued") return "排队中...";
  if (status === "planning") return "规划中...";
  if (status === "verifying") return "验证中...";
  return "执行中...";
}

export function isUsableTimelineTimestamp(timestamp?: number) {
  return Boolean(
    timestamp !== undefined &&
      Number.isFinite(timestamp) &&
      timestamp > Date.UTC(2020, 0, 1) &&
      timestamp < Date.now() + 60_000,
  );
}

export function getConversationLiveLabel({
  messagesLoading,
  hasStreamingMessage,
  activeTask,
  isRunning,
}: {
  messagesLoading?: boolean;
  hasStreamingMessage: boolean;
  activeTask?: SessionWorkspaceActiveTask | null;
  isRunning: boolean;
}) {
  if (hasStreamingMessage) return "正在输出...";
  if (messagesLoading) return "正在加载...";
  const status = activeTask?.status?.toLowerCase();
  if (status === "waiting_approval") return "等待审批...";
  if (isRunning) return "助手工作中...";
  return "已完成";
}

// ── Conversation Timeline Helpers ──────────────────────────────────
export function getConversationStartedAt(
  messages: SessionWorkspaceMessage[],
  activeTask?: SessionWorkspaceActiveTask | null,
  fallbackStartedAt?: number,
) {
  const latestUserMessage = [...messages]
    .reverse()
    .find((message) => message.role === "user" && isUsableTimelineTimestamp(message.createdAt));
  const streamingAssistantMessage = [...messages]
    .reverse()
    .find((message) => message.role === "assistant" && message.streaming && isUsableTimelineTimestamp(message.createdAt));

  return (
    latestUserMessage?.createdAt ??
    streamingAssistantMessage?.createdAt ??
    (isUsableTimelineTimestamp(activeTask?.createdAt) ? activeTask?.createdAt : undefined) ??
    (isUsableTimelineTimestamp(activeTask?.updatedAt) ? activeTask?.updatedAt : undefined) ??
    fallbackStartedAt ??
    Date.now()
  );
}

export function getConversationFinishedAt(
  messages: SessionWorkspaceMessage[],
  startedAt: number,
  activeTask?: SessionWorkspaceActiveTask | null,
) {
  const latestMessageAfterStart = messages
    .filter((message) => {
      const timestamp = getMessageTimelineTime(message);
      return message.role !== "user" && isUsableTimelineTimestamp(timestamp) && (timestamp ?? 0) >= startedAt;
    })
    .reduce<number | undefined>((latest, message) => {
      const timestamp = getMessageTimelineTime(message) ?? 0;
      return latest === undefined || timestamp > latest ? timestamp : latest;
    }, undefined);

  if (latestMessageAfterStart !== undefined) {
    return latestMessageAfterStart;
  }
  const taskUpdatedAt = activeTask?.updatedAt;
  if (isUsableTimelineTimestamp(taskUpdatedAt) && taskUpdatedAt !== undefined && taskUpdatedAt >= startedAt) {
    return taskUpdatedAt;
  }
  return Date.now();
}

export function getMessageTimelineTime(message: SessionWorkspaceMessage) {
  if (message.role === "assistant" && isUsableTimelineTimestamp(message.updatedAt)) {
    return message.updatedAt;
  }
  return message.createdAt;
}

export function getMessageActivitySortTime(message: SessionWorkspaceMessage) {
  return message.createdAt;
}

export function parsePatchPath(line: string) {
  return line
    .replace(/\s+\(\+\d+\/-\d+\).*$/, "")
    .replace(/^(added|modified|deleted|changed)\s+/i, "")
    .trim();
}

export function parsePatchFileSummary(line: string) {
  const path = parsePatchPath(line);
  if (!path) {
    return null;
  }
  const additions = /\+(\d+)/.exec(line)?.[1];
  const deletions = /-(\d+)/.exec(line)?.[1];
  return {
    path,
    status: line.includes("added") ? "added" : line.includes("deleted") ? "deleted" : "changed",
    additions: additions ? Number(additions) : undefined,
    deletions: deletions ? Number(deletions) : undefined,
  };
}

export function parsePatchFileSummaries(code?: string) {
  if (!code) {
    return [];
  }
  return code
    .split("\n")
    .map(parsePatchFileSummary)
    .filter((entry): entry is NonNullable<ReturnType<typeof parsePatchFileSummary>> => Boolean(entry));
}

export function buildCommandOutput(item: RuntimeTimelineItem) {
  return compactMeta([item.summary, item.code]).join("\n\n") || "暂无输出。";
}
