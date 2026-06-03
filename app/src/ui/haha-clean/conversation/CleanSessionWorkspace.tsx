import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ArrowDown } from "lucide-react";
import { RuntimeClient } from "../../../lib/runtimeClient";
import { buildConversationActivity } from "../../workbench/workspaces/session/ConversationActivity";
import { buildRuntimeItems } from "../../workbench/workspaces/session/runtimeItemBuilder";
import type {
  ConversationActivityItem,
  RuntimeTimelineItem,
  SessionWorkspaceCollaboration,
  SessionWorkspaceMessage,
  SessionWorkspaceProps,
} from "../../workbench/workspaces/session/types";
import { isRuntimeInFlight, isVerificationCommand, normalizeComparableCommand } from "../../workbench/workspaces/session/utils";
import { CleanActivityItem } from "./CleanConversation";

const runtimeClient = new RuntimeClient();
const FILE_WORKSPACE_OPEN_EVENT = "haha-clean:open-file-workspace";

function normalizePath(value: string) {
  return value.replace(/\\/g, "/").replace(/^[MADRCU?!]{1,2}\s+/, "").trim();
}

function isVisibleRuntime(item: RuntimeTimelineItem) {
  if (item.superseded) return false;
  if (item.kind === "task") return false;
  if (item.kind === "trace") {
    return ["failed", "error", "warning", "cancelled"].includes(item.status?.toLowerCase() ?? "");
  }
  return item.visibility !== "panel";
}

const QUIET_INLINE_TOOL_NAMES = new Set([
  "read_file",
  "list_dir",
  "list_directory",
  "git_status",
  "git_diff",
  "search_files",
  "code_search",
]);
const IMPORTANT_INLINE_TOOL_NAMES = new Set([
  "apply_patch",
  "write_file",
  "edit_file",
  "multi_edit",
  "replace_file",
  "delete_file",
]);
const INLINE_SHELL_TOOL_NAMES = new Set([
  "run_command",
  "command",
  "bash",
  "shell",
  "shell_command",
  "powershell",
]);
const FOLD_INLINE_TOOL_CATEGORIES = new Set([
  "command",
  "context_read",
  "git",
  "search",
  "verification",
]);
const INLINE_CONTEXT_COMMAND_RE =
  /^(git\s+(?:status|diff|log|show|branch|remote|tag|rev-parse|rev-list|ls-files|grep|blame)(?:\s|$)|rg(?:\s|$)|grep(?:\s|$)|ag(?:\s|$)|ack(?:\s|$)|findstr(?:\s|$)|select-string(?:\s|$)|find(?:\s|$)|where(?:\.exe)?(?:\s|$)|which(?:\s|$)|whereis(?:\s|$)|locate(?:\s|$)|cat(?:\s|$)|head(?:\s|$)|tail(?:\s|$)|less(?:\s|$)|more(?:\s|$)|type(?:\s|$)|wc(?:\s|$)|stat(?:\s|$)|file(?:\s|$)|strings(?:\s|$)|jq(?:\s|$)|awk(?:\s|$)|cut(?:\s|$)|sort(?:\s|$)|uniq(?:\s|$)|tr(?:\s|$)|get-content(?:\s|$)|gc(?:\s|$)|get-item(?:\s|$)|test-path(?:\s|$)|resolve-path(?:\s|$)|get-filehash(?:\s|$)|get-acl(?:\s|$)|format-hex(?:\s|$)|pwd(?:\s|$)|get-location(?:\s|$)|ls(?:\s|$)|dir(?:\s|$)|tree(?:\s|$)|du(?:\s|$)|get-childitem(?:\s|$)|gci(?:\s|$))/i;
const INLINE_VERIFICATION_COMMAND_RE =
  /\b(npm\s+(?:run\s+)?(?:test|typecheck|lint|build)|pnpm\s+(?:run\s+)?(?:test|typecheck|lint|build)|yarn\s+(?:test|typecheck|lint|build)|pytest|vitest|jest|playwright|tsc|ruff|eslint|mypy|cargo\s+(?:test|check|build)|go\s+test|dotnet\s+test)\b|\b(test|typecheck|lint|build|verify|check)\b/i;
const INLINE_ROUTINE_MUTATION_COMMAND_RE =
  /^git\s+(?:add|commit|reset\s+--soft|restore\s+--staged)(?:\s|$)/i;
const FAILED_INLINE_TOOL_STATUSES = new Set(["failed", "failure", "error", "cancelled", "canceled", "rejected", "blocked"]);
const COMPLETED_INLINE_TOOL_STATUSES = new Set(["completed", "complete", "done", "finished", "succeeded", "success", "passed"]);

const LOW_SIGNAL_TASK_STEP_PATTERNS = [
  /目标已开始/,
  /任务已开始/,
  /正在请求模型/,
  /进入.*阶段/,
  /准备(?:检查|读取|运行|执行|调用|使用)/,
  /\b(?:git_status|git_diff)\b/i,
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

function normalizeToolName(value?: string | null) {
  return String(value ?? "").trim().toLowerCase().replace(/\s+/g, "_");
}

function stripToolEntityId(value?: string | null) {
  const text = String(value ?? "").trim();
  if (!text) return "";
  return text.replace(/^(message:|runtime:|tool:|command:|tool_activity:|tool_result:|tool_use:)/, "");
}

function metadataText(message: SessionWorkspaceMessage, key: string) {
  const value = message.metadata?.[key];
  return typeof value === "string" ? value.trim() : "";
}

function parseRecord(value: string) {
  try {
    const parsed = JSON.parse(value);
    return parsed && typeof parsed === "object" ? parsed as Record<string, unknown> : null;
  } catch {
    return null;
  }
}

function readRecordText(record: Record<string, unknown> | null, keys: string[]) {
  if (!record) return "";
  for (const key of keys) {
    const value = record[key];
    if (typeof value === "string" && value.trim()) return value.trim();
    if (typeof value === "number" && Number.isFinite(value)) return String(value);
  }
  return "";
}

function metadataRecord(message: SessionWorkspaceMessage, key: string) {
  const value = message.metadata?.[key];
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function metadataNumber(message: SessionWorkspaceMessage, key: string) {
  const value = message.metadata?.[key];
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value.trim()) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : undefined;
  }
  return undefined;
}

function compactInlineText(value: string, limit = 140) {
  const text = value.replace(/\s+/g, " ").trim();
  return text.length > limit ? `${text.slice(0, Math.max(0, limit - 3))}...` : text;
}

function toolInputFingerprint(value?: string | null) {
  const text = String(value ?? "").trim();
  if (!text) return "";
  const record = parseRecord(text);
  const structured = readRecordText(record, ["path", "file", "cwd", "root", "target", "query", "url", "command", "cmd"]);
  return normalizePath(structured || text).toLowerCase();
}

function isQuietCompletedRuntime(item: RuntimeTimelineItem) {
  const name = normalizeToolName(item.toolName || item.title);
  const status = item.status?.toLowerCase() ?? "";
  if (!QUIET_INLINE_TOOL_NAMES.has(name)) return false;
  if (isRuntimeInFlight(status)) return false;
  return !["failed", "error", "cancelled", "rejected"].includes(status);
}

function isCompletedChildRuntime(item: RuntimeTimelineItem) {
  const status = item.status?.toLowerCase() ?? "";
  if (!item.parentToolUseId || item.kind !== "tool") return false;
  if (isRuntimeInFlight(status)) return false;
  return !["failed", "error", "cancelled", "rejected", "blocked"].includes(status);
}

function shouldFoldDuplicateRuntime(item: RuntimeTimelineItem) {
  return isQuietCompletedRuntime(item) || isCompletedChildRuntime(item);
}

function runtimeMatchKeys(item: RuntimeTimelineItem) {
  return [
    item.toolUseId,
    item.sourceId,
    item.id,
    stripToolEntityId(item.id),
  ].map(stripToolEntityId).filter(Boolean);
}

function messageMatchKeys(message: SessionWorkspaceMessage) {
  return [
    metadataText(message, "toolUseId"),
    metadataText(message, "sourceId"),
    message.id,
    stripToolEntityId(message.id),
  ].map(stripToolEntityId).filter(Boolean);
}

function runtimeFingerprint(item: RuntimeTimelineItem) {
  const name = normalizeToolName(item.toolName || item.title);
  const target = toolInputFingerprint(item.code || item.rawDetail || item.title);
  return name && target ? `${name}:${target}` : "";
}

function messageFingerprint(message: SessionWorkspaceMessage) {
  const name = normalizeToolName(message.toolName || metadataText(message, "toolName"));
  const input = metadataText(message, "inputText") || message.content;
  const target = toolInputFingerprint(input);
  return name && target ? `${name}:${target}` : "";
}

function activityRuntimeItems(items: ConversationActivityItem[]) {
  return items.flatMap((item) => {
    if (item.kind === "runtime") return [item.runtime];
    if (item.kind === "worklog") return item.runtimeItems;
    return [];
  });
}

function shouldHideDuplicateInlineToolMessage(
  message: SessionWorkspaceMessage,
  runtimeIds: Set<string>,
  quietRuntimeFingerprints: Set<string>,
) {
  const kind = message.metadata?.kind;
  if (kind !== "tool_use" && kind !== "tool_activity" && kind !== "tool_result") return false;
  const name = normalizeToolName(message.toolName || metadataText(message, "toolName"));
  const status = message.status?.toLowerCase() ?? "";
  if (message.streaming || isRuntimeInFlight(status) || ["failed", "error", "cancelled", "rejected", "blocked"].includes(status)) {
    return false;
  }
  if (messageMatchKeys(message).some((key) => runtimeIds.has(key))) return true;
  if (!QUIET_INLINE_TOOL_NAMES.has(name)) return false;
  const fingerprint = messageFingerprint(message);
  return Boolean(fingerprint && quietRuntimeFingerprints.has(fingerprint));
}

function inlineToolInputText(message: SessionWorkspaceMessage) {
  const metadataInputText = metadataText(message, "inputText");
  if (metadataInputText) return metadataInputText;
  const input = message.metadata?.input;
  if (input && typeof input === "object") {
    try {
      return JSON.stringify(input);
    } catch {
      return "";
    }
  }
  return message.content.trim();
}

function inlineToolInputRecord(message: SessionWorkspaceMessage) {
  return metadataRecord(message, "input") || parseRecord(inlineToolInputText(message)) || parseRecord(message.content);
}

function inlineToolStatus(message: SessionWorkspaceMessage) {
  for (const key of ["status", "phase", "state"]) {
    const value = message.metadata?.[key];
    if (typeof value === "string" && value.trim()) {
      return value.trim().toLowerCase();
    }
  }
  return typeof message.status === "string" ? message.status.trim().toLowerCase() : "";
}

function isCompletedInlineToolMessage(message: SessionWorkspaceMessage) {
  const status = inlineToolStatus(message);
  if (message.streaming || isRuntimeInFlight(status) || FAILED_INLINE_TOOL_STATUSES.has(status)) {
    return false;
  }
  return !status || COMPLETED_INLINE_TOOL_STATUSES.has(status);
}

function inlineToolCommand(message: SessionWorkspaceMessage) {
  const record = inlineToolInputRecord(message);
  const structured = readRecordText(record, ["command", "cmd"]);
  if (structured) return structured;
  const input = inlineToolInputText(message);
  const normalized = normalizeComparableCommand(input);
  if (
    normalized &&
    (
      INLINE_CONTEXT_COMMAND_RE.test(normalized) ||
      INLINE_ROUTINE_MUTATION_COMMAND_RE.test(normalized) ||
      INLINE_VERIFICATION_COMMAND_RE.test(normalized) ||
      isVerificationCommand(normalized)
    )
  ) {
    return input;
  }
  return "";
}

function inlineToolTarget(message: SessionWorkspaceMessage) {
  const record = inlineToolInputRecord(message);
  return normalizePath(readRecordText(record, ["path", "file", "target", "query", "url", "cwd", "root", "command", "cmd"]));
}

function isCollapsibleInlineCommand(message: SessionWorkspaceMessage) {
  const command = normalizeComparableCommand(inlineToolCommand(message));
  return Boolean(
    command &&
    (
      isVerificationCommand(command) ||
      INLINE_VERIFICATION_COMMAND_RE.test(command) ||
      INLINE_CONTEXT_COMMAND_RE.test(command) ||
      INLINE_ROUTINE_MUTATION_COMMAND_RE.test(command)
    ),
  );
}

function shouldFoldInlineToolMessageToWorklog(message: SessionWorkspaceMessage) {
  const kind = messageMetadataKind(message);
  if (kind !== "tool_use" && kind !== "tool_activity" && kind !== "tool_result") {
    return false;
  }
  if (!isCompletedInlineToolMessage(message)) {
    return false;
  }
  const name = normalizeToolName(message.toolName || metadataText(message, "toolName"));
  if (IMPORTANT_INLINE_TOOL_NAMES.has(name)) {
    return false;
  }
  const category = metadataText(message, "toolCategory").toLowerCase();
  if (category && FOLD_INLINE_TOOL_CATEGORIES.has(category)) {
    return true;
  }
  if (QUIET_INLINE_TOOL_NAMES.has(name)) {
    return true;
  }
  if (INLINE_SHELL_TOOL_NAMES.has(name)) {
    return isCollapsibleInlineCommand(message);
  }
  return false;
}

function inlineToolRuntimeCategory(message: SessionWorkspaceMessage) {
  const category = metadataText(message, "toolCategory").toLowerCase();
  if (category) return category;
  const name = normalizeToolName(message.toolName || metadataText(message, "toolName"));
  if (["git_status", "git_diff"].includes(name)) return "git";
  if (["read_file", "list_dir", "list_directory"].includes(name)) return "context_read";
  if (["search_files", "code_search"].includes(name)) return "search";
  if (INLINE_SHELL_TOOL_NAMES.has(name)) {
    const command = normalizeComparableCommand(inlineToolCommand(message));
    if (command && (isVerificationCommand(command) || INLINE_VERIFICATION_COMMAND_RE.test(command))) return "verification";
    if (command && /^git\s+/i.test(command)) return "git";
    return "command";
  }
  return "";
}

function inlineToolRuntimeTitle(message: SessionWorkspaceMessage) {
  const explicitTitle = metadataText(message, "title") || metadataText(message, "summary");
  if (explicitTitle) return compactInlineText(explicitTitle);
  const name = normalizeToolName(message.toolName || metadataText(message, "toolName"));
  const command = inlineToolCommand(message);
  if (command) return `Run ${compactInlineText(command)}`;
  const target = inlineToolTarget(message);
  if (target) {
    if (["read_file"].includes(name)) return `Read ${compactInlineText(target)}`;
    if (["list_dir", "list_directory"].includes(name)) return `List ${compactInlineText(target)}`;
    if (["git_status", "git_diff"].includes(name)) return `Git ${compactInlineText(target)}`;
    if (["search_files", "code_search"].includes(name)) return `Search ${compactInlineText(target)}`;
    return `${name.replace(/_/g, " ") || "Tool"} ${compactInlineText(target)}`;
  }
  return name.replace(/_/g, " ") || "Tool";
}

function inlineToolMessageToRuntime(message: SessionWorkspaceMessage): RuntimeTimelineItem {
  const name = normalizeToolName(message.toolName || metadataText(message, "toolName"));
  const sourceId = metadataText(message, "sourceId") || message.id;
  const inputText = inlineToolInputText(message);
  const resultText = metadataText(message, "resultText") || message.content;
  const category = inlineToolRuntimeCategory(message);
  const target = inlineToolTarget(message);
  const runtimeKind: RuntimeTimelineItem["kind"] = INLINE_SHELL_TOOL_NAMES.has(name) ? "command" : "tool";
  return {
    id: `inline:${stripToolEntityId(message.id) || message.id}`,
    kind: runtimeKind,
    sourceId,
    toolUseId: metadataText(message, "toolUseId") || undefined,
    parentToolUseId: metadataText(message, "parentToolUseId") || undefined,
    toolGroupId: metadataText(message, "toolGroupId") || undefined,
    toolIndex: metadataNumber(message, "toolIndex"),
    toolTotal: metadataNumber(message, "toolTotal"),
    toolCategory: category || undefined,
    toolPhaseId: metadataText(message, "toolPhaseId") || undefined,
    toolPhaseLabel: metadataText(message, "toolPhaseLabel") || undefined,
    toolSemanticParentId: metadataText(message, "toolSemanticParentId") || undefined,
    toolSemanticParentLabel: metadataText(message, "toolSemanticParentLabel") || undefined,
    title: inlineToolRuntimeTitle(message),
    status: inlineToolStatus(message) || "completed",
    summary: metadataText(message, "resultSummary") || metadataText(message, "summary") || undefined,
    meta: [category, target].filter(Boolean),
    code: inputText || undefined,
    rawDetail: resultText || undefined,
    time: message.updatedAt ?? message.createdAt,
    durationMs: metadataNumber(message, "durationMs"),
    taskId: message.taskId,
    toolName: name || undefined,
  };
}

function foldInlineToolMessagesIntoWorklogs(items: ConversationActivityItem[]) {
  const folded: ConversationActivityItem[] = [];
  let buffer: Array<Extract<ConversationActivityItem, { kind: "message" }>> = [];

  const flush = () => {
    if (!buffer.length) {
      return;
    }
    const first = buffer[0];
    folded.push({
      id: `worklog:inline:${buffer.map((item) => stripToolEntityId(item.message.id) || item.message.id).join(":")}`,
      kind: "worklog",
      order: first.order,
      time: first.time,
      runtimeItems: buffer.map((item) => inlineToolMessageToRuntime(item.message)),
    });
    buffer = [];
  };

  items.forEach((item) => {
    if (item.kind === "message" && shouldFoldInlineToolMessageToWorklog(item.message)) {
      buffer.push(item);
      return;
    }
    flush();
    folded.push(item);
  });

  flush();
  return folded;
}

const LOW_SIGNAL_SPECIAL_EVENT_KINDS = new Set([
  "assistant_thinking",
  "assistant_progress",
  "goal_event",
  "memory_event",
  "task_summary",
  "plan_update",
  "status",
]);
const TERMINAL_EVENT_STATUSES = new Set([
  "completed",
  "complete",
  "done",
  "finished",
  "succeeded",
  "success",
  "failed",
  "failure",
  "error",
  "cancelled",
  "canceled",
  "rejected",
]);
const ATTENTION_EVENT_STATUSES = new Set([
  "blocked",
  "blocking",
  "waiting",
  "waiting_approval",
  "needs_input",
  "paused",
  "requires_action",
]);
const FAILURE_EVENT_STATUSES = new Set([
  "failed",
  "failure",
  "error",
  "cancelled",
  "canceled",
  "rejected",
]);
const ACTIONABLE_PLAN_RE =
  /\b(apply|patch|edit|write|implement|modify|command|shell|run|verify|test|git|commit|diff|build|fix)\b|应用|补丁|编辑|写入|实现|修改|运行|执行|验证|测试|构建|修复|文件|改动|差异|审批|命令/i;
const TERMINAL_TEXT_RE =
  /\b(completed|complete|done|finished|succeeded|success|failed|failure|error|cancelled|canceled|rejected)\b|已完成|完成|成功|失败|出错|取消|拒绝|阻塞|等待审批|需要确认/i;
const FAILURE_TEXT_RE =
  /\b(failed|failure|error|cancelled|canceled|rejected|blocked)\b|失败|错误|出错|取消|拒绝|阻塞/i;

function messageMetadataKind(message: SessionWorkspaceMessage) {
  const kind = message.metadata?.kind;
  return typeof kind === "string" ? kind : "";
}

function messageLifecycleStatus(message: SessionWorkspaceMessage) {
  for (const key of ["status", "phase", "state"]) {
    const value = message.metadata?.[key];
    if (typeof value === "string" && value.trim()) {
      return value.trim().toLowerCase();
    }
  }
  return message.status === "failed" ? "failed" : "";
}

function messageSummaryText(message: SessionWorkspaceMessage) {
  return [
    message.content,
    metadataText(message, "title"),
    metadataText(message, "summary"),
    metadataText(message, "description"),
    metadataText(message, "message"),
    metadataText(message, "detail"),
    metadataText(message, "reason"),
  ].filter(Boolean).join("\n");
}

function isLowSignalSpecialText(value: string) {
  const normalized = value.trim();
  if (!normalized) return true;
  return LOW_SIGNAL_TASK_STEP_PATTERNS.some((pattern) => pattern.test(normalized));
}

function isLowSignalStreamingPlaceholder(message: SessionWorkspaceMessage) {
  const text = message.content.trim();
  if (!message.streaming && !message.placeholder) return false;
  if (message.role !== "assistant") return false;
  if (!text) return true;
  return (
    isLowSignalSpecialText(text) ||
    text === "正在输出回复" ||
    text === "模型正在思考" ||
    text === "思考中..."
  );
}

function shouldHideLowSignalSpecialMessage(message: SessionWorkspaceMessage) {
  if (isLowSignalStreamingPlaceholder(message)) {
    return true;
  }

  const kind = messageMetadataKind(message);
  if (!LOW_SIGNAL_SPECIAL_EVENT_KINDS.has(kind)) {
    return false;
  }

  const status = messageLifecycleStatus(message);
  const text = messageSummaryText(message);
  const hasTerminalStatus = TERMINAL_EVENT_STATUSES.has(status) || TERMINAL_TEXT_RE.test(text);
  const needsAttention = ATTENTION_EVENT_STATUSES.has(status);
  if (kind === "assistant_thinking") {
    return isLowSignalStreamingPlaceholder(message);
  }
  if (needsAttention) {
    return false;
  }
  if (FAILURE_EVENT_STATUSES.has(status) || FAILURE_TEXT_RE.test(text)) {
    return false;
  }

  if (kind === "status") {
    return !["failed", "failure", "error", "blocked", "blocking"].includes(status);
  }

  if (kind === "assistant_progress") {
    return isLowSignalSpecialText(text);
  }

  if (kind === "goal_event" || kind === "memory_event") {
    return true;
  }

  if (kind === "task_summary") {
    return !hasTerminalStatus;
  }

  if (kind === "plan_update") {
    return !hasTerminalStatus && (!ACTIONABLE_PLAN_RE.test(text) || isLowSignalSpecialText(text));
  }

  return false;
}

export function filterCleanLowSignalSpecialEvents(items: ConversationActivityItem[]) {
  return items.filter((item) => (
    item.kind !== "message" ||
    !shouldHideLowSignalSpecialMessage(item.message)
  ));
}

export function filterCleanLowSignalMessages(messages: SessionWorkspaceMessage[]) {
  return messages.filter((message) => !shouldHideLowSignalSpecialMessage(message));
}

export function filterCleanDuplicateToolMessages(items: ConversationActivityItem[]) {
  const foldableRuntimes = activityRuntimeItems(items).filter(shouldFoldDuplicateRuntime);
  if (!foldableRuntimes.length) {
    return foldInlineToolMessagesIntoWorklogs(items);
  }
  const runtimeIds = new Set(foldableRuntimes.flatMap(runtimeMatchKeys));
  const quietRuntimeFingerprints = new Set(
    foldableRuntimes.filter(isQuietCompletedRuntime).map(runtimeFingerprint).filter(Boolean),
  );
  const deduped = items.filter((item) => (
    item.kind !== "message" ||
    !shouldHideDuplicateInlineToolMessage(item.message, runtimeIds, quietRuntimeFingerprints)
  ));
  return foldInlineToolMessagesIntoWorklogs(deduped);
}

function agentTaskStatusRank(status?: string) {
  const normalized = status?.toLowerCase() ?? "";
  if (["running", "started", "queued", "pending", "planning", "verifying", "waiting_approval", "blocked"].includes(normalized)) return "running";
  if (["failed", "error", "cancelled", "canceled", "rejected"].includes(normalized)) return "failed";
  if (["completed", "complete", "done", "finished", "succeeded", "success", "passed"].includes(normalized)) return "completed";
  return "recorded";
}

function summarizeAgentTasks(collaboration?: SessionWorkspaceCollaboration): SessionWorkspaceMessage[] {
  const tasks = collaboration?.childTasks ?? [];
  if (!tasks.length) return [];
  const workers = collaboration?.workers ?? [];
  const results = collaboration?.results ?? [];
  const firstTime = tasks.reduce(
    (min, task) => Math.min(min, task.createdAt ?? task.updatedAt ?? Date.now()),
    Number.POSITIVE_INFINITY,
  );
  const lastTime = tasks.reduce(
    (max, task) => Math.max(max, task.completedAt ?? task.updatedAt ?? task.createdAt ?? 0),
    0,
  );
  const runningCount = tasks.filter((task) => agentTaskStatusRank(task.status) === "running").length;
  const failedCount = tasks.filter((task) => agentTaskStatusRank(task.status) === "failed").length;
  const completedCount = tasks.filter((task) => agentTaskStatusRank(task.status) === "completed").length;
  const status = runningCount ? "running" : failedCount ? "failed" : completedCount === tasks.length ? "completed" : "recorded";
  const summaryParts = [
    completedCount ? `${completedCount} 个完成` : "",
    runningCount ? `${runningCount} 个运行中` : "",
    failedCount ? `${failedCount} 个失败或取消` : "",
    workers.length ? `${workers.length} 个 worker` : "",
  ].filter(Boolean);

  return [{
    id: `collaboration:${tasks.map((task) => task.id).join(":")}`,
    role: "assistant",
    content: summaryParts.join(" · "),
    kind: "background_task",
    status,
    createdAt: Number.isFinite(firstTime) ? firstTime : lastTime,
    updatedAt: lastTime || firstTime,
    metadata: {
      kind: "background_task",
      title: `派遣了 ${tasks.length} 个代理`,
      summary: summaryParts.join(" · "),
      status,
      agentTasks: tasks.map((task) => ({
        id: task.id,
        title: task.title,
        status: task.status ?? "recorded",
        workerName: task.workerName,
        agentType: task.agentType,
        summary: task.summary,
        attention: task.attention,
        durationMs: task.durationMs,
        artifactCount: task.artifactCount,
        errorMessage: task.errorMessage,
        updatedAt: task.updatedAt,
      })),
      agentResults: results.slice(0, 6).map((result) => ({
        id: result.id,
        taskId: result.taskId,
        title: result.title,
        status: result.status,
        summary: result.summary,
        updatedAt: result.updatedAt,
      })),
      workerCount: workers.length,
      taskCount: tasks.length,
      runningCount,
      failedCount,
      completedCount,
    },
  }];
}

export function CleanSessionWorkspace({
  session,
  messages,
  activeTask,
  collaboration,
  contextPreview,
  approvals,
  patches,
  traces,
  toolCalls,
  backgroundJobs,
  onApprove,
  onApproveAlways,
  onReject,
  onLoadPatch,
  onCopyRuntimeText,
  onRevertTaskChanges,
  onSubmitUserQuestionAnswer,
  onQuoteMessage,
  onContinueFromMessage,
  onBranchFromMessage,
  onDeleteMessage,
  onRefreshCommandJob,
  onStopCommandJob,
  worktreeStatus,
  composerContext,
  busyId,
  patchBusyId,
  messagesLoading,
}: SessionWorkspaceProps) {
  const [questionBusyId, setQuestionBusyId] = useState<string | null>(null);
  const [answeredQuestionIds, setAnsweredQuestionIds] = useState<Set<string>>(() => new Set());
  const [messageActionBusyId, setMessageActionBusyId] = useState<string | null>(null);
  const scrollRef = useRef<HTMLElement | null>(null);
  const wasNearBottomRef = useRef(true);
  const [showJumpToBottom, setShowJumpToBottom] = useState(false);
  const runtimeItems = useMemo(
    () => buildRuntimeItems({ session, activeTask, contextPreview, approvals, patches, traces, toolCalls, backgroundJobs }),
    [activeTask, approvals, backgroundJobs, contextPreview, patches, session, toolCalls, traces],
  );
  const running = isRuntimeInFlight(activeTask?.status) && !isLowSignalSpecialText(activeTask?.currentStep ?? activeTask?.summary ?? "");
  const visibleRuntimeItems = useMemo(
    () => runtimeItems.filter(isVisibleRuntime),
    [runtimeItems],
  );
  const activityItems = useMemo(
    () => {
      const collaborationMessages = summarizeAgentTasks(collaboration);
      return filterCleanDuplicateToolMessages(
        buildConversationActivity(filterCleanLowSignalMessages([...messages, ...collaborationMessages]), visibleRuntimeItems),
      );
    },
    [collaboration, messages, visibleRuntimeItems],
  );
  const openFileInPane = useCallback((path: string) => {
    const normalized = normalizePath(path);
    if (!normalized) {
      return;
    }
    window.dispatchEvent(new CustomEvent(FILE_WORKSPACE_OPEN_EVENT, { detail: { path: normalized } }));
  }, []);

  const updateJumpToBottomState = useCallback(() => {
    const scroll = scrollRef.current;
    if (!scroll) {
      setShowJumpToBottom(false);
      wasNearBottomRef.current = true;
      return;
    }
    const distanceToBottom = scroll.scrollHeight - scroll.scrollTop - scroll.clientHeight;
    const isNearBottom = distanceToBottom < 96;
    wasNearBottomRef.current = isNearBottom;
    setShowJumpToBottom(distanceToBottom > 220);
  }, []);

  const scrollToLatest = useCallback((behavior: ScrollBehavior = "smooth") => {
    const scroll = scrollRef.current;
    if (!scroll) {
      return;
    }
    scroll.scrollTo({ top: scroll.scrollHeight, behavior });
    wasNearBottomRef.current = true;
    setShowJumpToBottom(false);
  }, []);

  const submitUserQuestionAnswer = useCallback(async (message: SessionWorkspaceMessage, answer: string) => {
    const text = answer.trim();
    const targetSessionId = session?.id;
    const targetTaskId = message.taskId && message.taskId !== "persisted" ? message.taskId : activeTask?.id;
    if (!text || !targetSessionId || answeredQuestionIds.has(message.id) || questionBusyId === message.id) {
      return;
    }
    setAnsweredQuestionIds((current) => new Set(current).add(message.id));
    if (onSubmitUserQuestionAnswer) {
      try {
        await onSubmitUserQuestionAnswer(message, text);
      } catch (reason) {
        setAnsweredQuestionIds((current) => {
          const next = new Set(current);
          next.delete(message.id);
          return next;
        });
        throw reason;
      }
      return;
    }
    setQuestionBusyId(message.id);
    try {
      const result = await runtimeClient.sendMessage({
        sessionId: targetSessionId,
        content: text,
        attachments: [],
        taskId: targetTaskId,
        mode: "supplement",
        internalResponse: {
          kind: "ask_user_question",
          messageId: message.id,
          requestId:
            typeof message.metadata?.requestId === "string"
              ? message.metadata.requestId
              : undefined,
          toolCallId:
            typeof message.metadata?.toolCallId === "string"
              ? message.metadata.toolCallId
              : undefined,
        },
      });
      const resultTaskId = result.task.id || targetTaskId;
      if (result.task.status === "paused" && resultTaskId) {
        await runtimeClient.resumeTask({ taskId: resultTaskId });
      }
    } catch (reason) {
      setAnsweredQuestionIds((current) => {
        const next = new Set(current);
        next.delete(message.id);
        return next;
      });
      throw reason;
    } finally {
      setQuestionBusyId(null);
    }
  }, [activeTask?.id, answeredQuestionIds, onSubmitUserQuestionAnswer, questionBusyId, session?.id]);

  const runMessageAction = useCallback(async (
    message: SessionWorkspaceMessage,
    action?: (message: SessionWorkspaceMessage) => void | Promise<void>,
  ) => {
    if (!action) return;
    setMessageActionBusyId(message.id);
    try {
      await action(message);
    } finally {
      setMessageActionBusyId(null);
    }
  }, []);

  useEffect(() => {
    const scroll = scrollRef.current;
    if (!scroll) {
      return;
    }
    updateJumpToBottomState();
    scroll.addEventListener("scroll", updateJumpToBottomState, { passive: true });
    const observer = typeof ResizeObserver !== "undefined" ? new ResizeObserver(updateJumpToBottomState) : null;
    observer?.observe(scroll);
    window.addEventListener("resize", updateJumpToBottomState);
    return () => {
      scroll.removeEventListener("scroll", updateJumpToBottomState);
      observer?.disconnect();
      window.removeEventListener("resize", updateJumpToBottomState);
    };
  }, [updateJumpToBottomState]);

  useEffect(() => {
    const frameId = window.requestAnimationFrame(() => {
      if (wasNearBottomRef.current) {
        scrollToLatest("auto");
      } else {
        updateJumpToBottomState();
      }
    });
    return () => window.cancelAnimationFrame(frameId);
  }, [activityItems.length, messagesLoading, running, scrollToLatest, updateJumpToBottomState]);

  if (!session) {
    return (
      <main className="hc-session hc-session-empty">
        <h1>新建会话</h1>
        <p>开始一个新的编码会话。</p>
      </main>
    );
  }

  return (
    <main
      className="hc-session"
    >
      <section className="hc-session-scroll" ref={scrollRef}>
        <div className="hc-transcript">
          {activityItems.length ? (
            activityItems.map((item) => (
              <CleanActivityItem
                key={item.id}
                item={item}
                onApprove={onApprove}
                onApproveAlways={onApproveAlways}
                onReject={onReject}
                onLoadPatch={onLoadPatch}
                onCopyRuntimeText={onCopyRuntimeText}
                onOpenFile={openFileInPane}
                onQuoteMessage={onQuoteMessage}
                onRevertTaskChanges={onRevertTaskChanges}
                onContinueFromMessage={onContinueFromMessage ? (message) => runMessageAction(message, onContinueFromMessage) : undefined}
                onBranchFromMessage={onBranchFromMessage ? (message) => runMessageAction(message, onBranchFromMessage) : undefined}
                onDeleteMessage={onDeleteMessage ? (message) => runMessageAction(message, onDeleteMessage) : undefined}
                onRefreshCommandJob={onRefreshCommandJob}
                onStopCommandJob={onStopCommandJob}
                busyId={patchBusyId ?? messageActionBusyId ?? questionBusyId ?? busyId}
                onSubmitUserQuestionAnswer={submitUserQuestionAnswer}
                answeredQuestionIds={answeredQuestionIds}
              />
            ))
          ) : messagesLoading ? (
            <div className="hc-empty-note">正在加载会话消息...</div>
          ) : (
            <div className="hc-empty-note">还没有消息，直接从下方开始。</div>
          )}
        </div>
      </section>
      {showJumpToBottom ? (
        <button
          type="button"
          className="hc-bottom-jump"
          aria-label="回到最新"
          onClick={() => scrollToLatest()}
        >
          <ArrowDown size={16} strokeWidth={2.1} aria-hidden="true" />
          <span>回到最新</span>
        </button>
      ) : null}
    </main>
  );
}
