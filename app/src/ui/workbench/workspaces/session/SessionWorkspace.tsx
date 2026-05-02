import { memo, useMemo, useState, type ReactNode } from "react";
import {
  ApprovalCard,
  CommandOutputPanel,
  ContextBudgetBar,
  PatchPlanCard,
  ToolTraceCard,
} from "../../../v2/components/runtime";
import { Button, Panel, StatusBadge } from "../../../v2/components/ui";
import { formatStatusLabel } from "../../../copy";
import "./session.css";

export interface SessionWorkspaceSession {
  id: string;
  title: string;
  status?: string;
  summary?: string | null;
  updatedAt?: number;
  messageCount?: number;
  tokenCount?: number;
}

export interface SessionWorkspacePlanStep {
  id: string;
  title: string;
  status?: string;
  summary?: string;
  detail?: string;
  durationMs?: number;
}

export interface SessionWorkspaceActiveTask {
  id: string;
  status?: string;
  goal?: string;
  acceptanceCriteria?: string[];
  outOfScope?: string[];
  currentStep?: string;
  changedFiles?: Array<{
    path: string;
    status?: string;
    additions?: number;
    deletions?: number;
    reason?: string;
    patchId?: string | null;
  }>;
  commands?: Array<{
    id?: string;
    command: string;
    cwd?: string;
    shell?: string;
    status?: string;
    exitCode?: number | null;
    durationMs?: number | null;
    summary?: string;
    stdoutPath?: string | null;
    stderrPath?: string | null;
    background?: boolean;
  }>;
  verification?: Array<{
    id?: string;
    command?: string;
    status: string;
    exitCode?: number | null;
    durationMs?: number | null;
    summary?: string;
  }>;
  summary?: string;
  resultSummary?: string;
  planSteps?: SessionWorkspacePlanStep[];
}

export interface SessionWorkspaceCollaborator {
  id: string;
  name: string;
  status?: string;
  mode?: string;
  healthState?: string;
  healthReason?: string;
  heartbeatAgeMs?: number;
  lastHeartbeatAt?: number;
  claimedTaskId?: string;
  summary?: string;
  updatedAt?: number;
}

export interface SessionWorkspaceChildTask {
  id: string;
  title: string;
  status?: string;
  workerId?: string;
  workerName?: string;
  summary?: string;
  updatedAt?: number;
}

export interface SessionWorkspaceChildTaskResult {
  id: string;
  taskId?: string;
  title?: string;
  status?: string;
  summary?: string;
  updatedAt?: number;
}

export interface SessionWorkspaceCollaboration {
  workers?: SessionWorkspaceCollaborator[];
  childTasks?: SessionWorkspaceChildTask[];
  results?: SessionWorkspaceChildTaskResult[];
  healthSummary?: {
    healthy: number;
    stale: number;
    offline: number;
    total: number;
  };
}

export interface SessionWorkspaceMessage {
  id: string;
  role: "user" | "assistant" | "system" | "tool";
  content: string;
  streaming?: boolean;
  placeholder?: boolean;
  createdAt?: number;
  toolName?: string;
  status?: string;
}

export interface SessionWorkspaceApproval {
  id: string;
  title: string;
  kind?: string;
  status: string;
  summary?: string;
  requestedAt?: number;
  risk?: "low" | "medium" | "high";
  parametersPreview?: string;
  fullInput?: string;
  command?: string;
  cwd?: string;
}

export interface SessionWorkspacePatchFile {
  path: string;
  status?: string;
  additions?: number;
  deletions?: number;
  diff?: string;
}

export interface SessionWorkspacePatch {
  id: string;
  summary: string;
  status: string;
  filesChanged?: number;
  additions?: number;
  deletions?: number;
  updatedAt?: number;
  files?: SessionWorkspacePatchFile[];
  diff?: string;
}

export interface SessionWorkspaceTrace {
  id: string;
  type: string;
  source?: string;
  time?: number;
  title?: string;
  summary?: string;
  detail?: string;
  status?: string;
  durationMs?: number;
  tokenCount?: number;
  stdout?: string;
  stderr?: string;
}

export interface SessionWorkspaceToolCall {
  id: string;
  toolName: string;
  status: string;
  time?: number;
  resultSummary?: string;
  durationMs?: number;
  tokenCount?: number;
  argsPreview?: string;
  input?: string;
  output?: string;
  rawInput?: string;
  rawOutput?: string;
  stdout?: string;
  stderr?: string;
}

export interface SessionWorkspaceBackgroundJob {
  id: string;
  command: string;
  status: string;
  cwd?: string;
  shell?: string;
  summary?: string;
  startedAt?: number;
  finishedAt?: number;
  durationMs?: number;
  exitCode?: number | null;
  stdout?: string;
  stderr?: string;
  stdoutPath?: string;
  stderrPath?: string;
  isBackground?: boolean;
}

export interface SessionWorkspaceComposerContext {
  cwd?: string;
  repo?: string;
  branch?: string;
  model?: string;
  permissionMode?: string;
}

export interface SessionWorkspaceContextPreview {
  projectFocus?: string | null;
  projectMemory?: string | null;
  workspaceRoot?: string | null;
  searchQuery?: string | null;
  searchMode?: string | null;
  toolCount?: number | null;
  budgetStats?: {
    estimatedTokens?: number | null;
    estimatedInputTokens?: number | null;
    messageTokens?: number | null;
    toolSchemaTokens?: number | null;
    maxContextTokens?: number | null;
    droppedSections?: string[];
    trimmedSections?: string[];
  } | null;
  taskFocus?: {
    currentStep?: string | null;
    acceptanceCriteriaCount?: number | null;
    outOfScopeCount?: number | null;
  } | null;
}

export interface SessionWorkspaceProps {
  session: SessionWorkspaceSession | null;
  activeTask: SessionWorkspaceActiveTask | null;
  messages: SessionWorkspaceMessage[];
  taskCount?: number;
  collaboration?: SessionWorkspaceCollaboration;
  backgroundJobs?: SessionWorkspaceBackgroundJob[];
  approvals?: SessionWorkspaceApproval[];
  patches?: SessionWorkspacePatch[];
  traces?: SessionWorkspaceTrace[];
  toolCalls?: SessionWorkspaceToolCall[];
  composerContext?: SessionWorkspaceComposerContext;
  contextPreview?: SessionWorkspaceContextPreview;
  onApprove?(approvalId: string): void | Promise<void>;
  onApproveForSession?(approvalId: string): void | Promise<void>;
  onReject?(approvalId: string): void | Promise<void>;
  onLoadPatch?(patchId: string): void | Promise<void>;
  onCopyPatchPath?(patchId: string, path: string): void | Promise<void>;
  onCopyRuntimeText?(label: string, text: string): void | Promise<void>;
  onRefreshCommandJob?(commandId: string): void | Promise<void>;
  onStopCommandJob?(commandId: string): void | Promise<void>;
  onRefreshTask?(): void | Promise<void>;
  onStopTask?(taskId: string): void | Promise<void>;
  onRefreshTrace?(): void | Promise<void>;
  taskBusyAction?: "refresh" | "stop" | null;
  busyId?: string | null;
  messagesLoading?: boolean;
}

const dateTimeFormatter = new Intl.DateTimeFormat(undefined, {
  month: "short",
  day: "numeric",
  hour: "2-digit",
  minute: "2-digit",
});

function formatTimestamp(timestamp?: number) {
  if (timestamp === undefined) {
    return null;
  }

  return dateTimeFormatter.format(new Date(timestamp));
}

interface DiffLine {
  type: "add" | "remove" | "context" | "header";
  content: string;
}

interface RuntimeTimelineItem {
  id: string;
  kind: "approval" | "patch" | "trace" | "tool" | "command" | "task" | "memory";
  sourceId?: string;
  title: string;
  status?: string;
  summary?: string;
  meta?: string[];
  code?: string;
  rawDetail?: string;
  time?: number;
  diffLines?: DiffLine[];
}

interface ToolRuntimePresentation {
  kind: RuntimeTimelineItem["kind"];
  title: string;
  summary?: string;
  meta: string[];
  code?: string;
}

type ConversationActivityItem =
  | {
      id: string;
      kind: "message";
      order: number;
      time?: number;
      message: SessionWorkspaceMessage;
    }
  | {
      id: string;
      kind: "runtime";
      order: number;
      time?: number;
      runtime: RuntimeTimelineItem;
    };

function getRoleLabel(role: SessionWorkspaceMessage["role"]) {
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

function formatDuration(durationMs?: number) {
  if (durationMs === undefined) {
    return null;
  }

  if (durationMs < 1000) {
    return `${durationMs}ms`;
  }

  return `${(durationMs / 1000).toFixed(1)}s`;
}

function compactMeta(values: Array<string | null | undefined>) {
  return values.filter((value): value is string => Boolean(value));
}

function compactList(values: string[], limit = 3) {
  if (values.length <= limit) {
    return values.join(", ");
  }

  return `${values.slice(0, limit).join(", ")} +${values.length - limit}`;
}

function compactText(value: string | null | undefined, maxChars = 240) {
  const text = typeof value === "string" ? value.trim() : "";
  if (!text) {
    return "";
  }
  if (text.length <= maxChars) {
    return text;
  }
  return `${text.slice(0, Math.max(1, maxChars - 14)).trimEnd()} [已截断]`;
}

const MAX_RENDERED_DIFF_LINES = 500;

function parseUnifiedDiff(diffText: string): DiffLine[] {
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

function stripSectionLabel(value: string, label: string) {
  const normalizedLabel = `${label}:`;
  return value.startsWith(normalizedLabel) ? value.slice(normalizedLabel.length).trim() : value;
}

function formatTokenBudget(stats?: SessionWorkspaceContextPreview["budgetStats"]) {
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

function formatSignedCount(value: number | undefined, prefix: string) {
  if (value === undefined) {
    return null;
  }

  return `${prefix}${value}`;
}

function formatTaskFileChange(file: NonNullable<SessionWorkspaceActiveTask["changedFiles"]>[number]) {
  const changeStats = compactMeta([formatSignedCount(file.additions, "+"), formatSignedCount(file.deletions, "-")]).join(" ");
  const status = formatStatusLabel(file.status ?? "changed");
  const suffix = compactMeta([changeStats, file.reason]).join(" - ");
  return `${status} ${file.path}${suffix ? ` - ${suffix}` : ""}`;
}

function formatTaskCommand(command: NonNullable<SessionWorkspaceActiveTask["commands"]>[number]) {
  const meta = compactMeta([
    command.status ? formatStatusLabel(command.status) : null,
    command.exitCode !== undefined && command.exitCode !== null ? `退出码 ${command.exitCode}` : null,
    formatDuration(command.durationMs ?? undefined),
    command.cwd,
    command.background ? "后台" : null,
  ]);
  return `${command.command}${meta.length ? ` - ${meta.join(" - ")}` : ""}`;
}

function formatTaskVerification(record: NonNullable<SessionWorkspaceActiveTask["verification"]>[number]) {
  const label = record.command ?? record.id ?? "验证";
  const meta = compactMeta([
    formatStatusLabel(record.status),
    record.exitCode !== undefined && record.exitCode !== null ? `退出码 ${record.exitCode}` : null,
    formatDuration(record.durationMs ?? undefined),
    record.summary,
  ]);
  return `${label}${meta.length ? ` - ${meta.join(" - ")}` : ""}`;
}

function parseRuntimeJsonRecord(value?: string): Record<string, unknown> | null {
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

function readRuntimeString(record: Record<string, unknown> | null, keys: string[]) {
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

function summarizeRuntimeOutput(value?: string) {
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

function buildToolRuntimePresentation(toolCall: SessionWorkspaceToolCall): ToolRuntimePresentation {
  const inputRecord = parseRuntimeJsonRecord(toolCall.rawInput);
  const command = readRuntimeString(inputRecord, ["command", "cmd"]);
  const path = readRuntimeString(inputRecord, ["path", "file", "cwd", "root"]);
  const url = readRuntimeString(inputRecord, ["url"]);
  const query = readRuntimeString(inputRecord, ["query"]);
  const duration = formatDuration(toolCall.durationMs);
  const tokenCount = toolCall.tokenCount !== undefined ? `${toolCall.tokenCount} 令牌` : null;
  const statusMeta = compactMeta([duration, tokenCount]);
  const resultSummary = summarizeRuntimeOutput(toolCall.resultSummary || toolCall.output || toolCall.stdout || toolCall.stderr);

  if (toolCall.toolName === "run_command") {
    return {
      kind: "command",
      title: command ?? toolCall.argsPreview ?? "命令",
      summary: resultSummary,
      meta: compactMeta([command ? "shell" : null, ...statusMeta]),
      code: command && toolCall.argsPreview && toolCall.argsPreview !== command ? toolCall.argsPreview : undefined,
    };
  }

  if (toolCall.toolName === "apply_patch") {
    return {
      kind: "patch",
      title: "apply_patch",
      summary: resultSummary,
      meta: compactMeta([toolCall.input, ...statusMeta]),
      code: toolCall.argsPreview,
    };
  }

  // --- path-based tools ---
  if (toolCall.toolName === "list_dir") {
    return {
      kind: "tool",
      title: path ? `list_dir ${path}` : "list_dir",
      summary: resultSummary,
      meta: compactMeta([path ? `路径：${path}` : toolCall.input, ...statusMeta]),
      code: toolCall.argsPreview,
    };
  }

  if (toolCall.toolName === "read_file") {
    return {
      kind: "tool",
      title: path ? `read_file ${path}` : "read_file",
      summary: resultSummary,
      meta: compactMeta([path ? `路径：${path}` : toolCall.input, ...statusMeta]),
      code: toolCall.argsPreview,
    };
  }

  if (toolCall.toolName === "write_file") {
    return {
      kind: "tool",
      title: path ? `write_file ${path}` : "write_file",
      summary: resultSummary,
      meta: compactMeta([path ? `路径：${path}` : toolCall.input, ...statusMeta]),
      code: toolCall.argsPreview,
    };
  }

  if (toolCall.toolName === "search_files") {
    return {
      kind: "tool",
      title: query ? `search_files "${query}"` : "search_files",
      summary: resultSummary,
      meta: compactMeta([path ? `根目录：${path}` : null, ...statusMeta]),
      code: toolCall.argsPreview,
    };
  }

  if (toolCall.toolName === "code_search") {
    return {
      kind: "tool",
      title: query ? `code_search "${query}"` : "code_search",
      summary: resultSummary,
      meta: compactMeta([path ? `根目录：${path}` : null, ...statusMeta]),
      code: toolCall.argsPreview,
    };
  }

  // --- url-based tools ---
  if (toolCall.toolName === "web_fetch") {
    return {
      kind: "tool",
      title: url ? `web_fetch ${url}` : "web_fetch",
      summary: resultSummary,
      meta: compactMeta([url ?? toolCall.input, ...statusMeta]),
      code: toolCall.argsPreview,
    };
  }

  if (toolCall.toolName === "browser") {
    return {
      kind: "tool",
      title: url ? `browser ${url}` : "browser",
      summary: resultSummary,
      meta: compactMeta([url ?? toolCall.input, ...statusMeta]),
      code: toolCall.argsPreview,
    };
  }

  // --- notebook ---
  if (toolCall.toolName === "notebook") {
    const action = readRuntimeString(inputRecord, ["action"]);
    return {
      kind: "tool",
      title: path ? `notebook ${path}` : "notebook",
      summary: resultSummary,
      meta: compactMeta([action ? `动作：${action}` : null, path ? `路径：${path}` : null, ...statusMeta]),
      code: toolCall.argsPreview,
    };
  }

  // --- git tools ---
  if (toolCall.toolName === "git_status") {
    return {
      kind: "tool",
      title: "git status",
      summary: resultSummary,
      meta: statusMeta,
      code: toolCall.argsPreview,
    };
  }

  if (toolCall.toolName === "git_diff") {
    return {
      kind: "tool",
      title: "git diff",
      summary: resultSummary,
      meta: statusMeta,
      code: toolCall.argsPreview,
    };
  }

  return {
    kind: "tool",
    title: toolCall.toolName,
    summary: resultSummary,
    meta: compactMeta([toolCall.input, ...statusMeta]),
    code: toolCall.argsPreview,
  };
}

function aggregateRuntimeStatus(statuses: Array<string | undefined>, emptyStatus: string) {
  const normalized = statuses.filter((status): status is string => Boolean(status));
  if (normalized.length === 0) {
    return emptyStatus;
  }
  if (normalized.some((status) => ["failed", "error", "cancelled"].includes(status))) {
    return "failed";
  }
  if (normalized.some((status) => ["running", "pending", "queued"].includes(status))) {
    return "running";
  }
  if (normalized.every((status) => status === "skipped")) {
    return "skipped";
  }
  if (normalized.every((status) => ["passed", "completed", "applied", "succeeded"].includes(status))) {
    return normalized.every((status) => status === "passed") ? "passed" : "completed";
  }

  return "recorded";
}

function buildActiveTaskRuntimeItems(activeTask?: SessionWorkspaceActiveTask | null): RuntimeTimelineItem[] {
  if (!activeTask) {
    return [];
  }

  const items: RuntimeTimelineItem[] = [];
  const acceptanceCriteria = activeTask.acceptanceCriteria ?? [];
  const outOfScope = activeTask.outOfScope ?? [];
  const changedFiles = activeTask.changedFiles ?? [];
  const commands = activeTask.commands ?? [];
  const verification = activeTask.verification ?? [];

  // Skip task focus for very short goals (simple conversations like "你好", "1+1=?") —
  // the boilerplate acceptance criteria / out-of-scope items add noise without value.
  const isSimpleGoal = !activeTask.goal || activeTask.goal.trim().length < 15;

  if (!isSimpleGoal && (activeTask.currentStep || activeTask.goal || acceptanceCriteria.length || outOfScope.length)) {
    items.push({
      id: `task-focus:${activeTask.id}`,
      kind: "task",
      title: "任务重点",
      status: activeTask.status,
      summary: activeTask.currentStep || activeTask.goal,
      meta: compactMeta([
        acceptanceCriteria.length ? `${acceptanceCriteria.length} 条验收标准` : null,
        outOfScope.length ? `${outOfScope.length} 条不在范围内` : null,
      ]),
      code: compactMeta([
        activeTask.goal ? `📌 目标：\n${activeTask.goal}` : null,
        acceptanceCriteria.length
          ? `✅ 验收标准：\n${acceptanceCriteria.map((c, i) => `  ${i + 1}. ${c}`).join("\n")}`
          : null,
        outOfScope.length
          ? `🚫 不在范围内：\n${outOfScope.map((c, i) => `  ${i + 1}. ${c}`).join("\n")}`
          : null,
      ]).join("\n\n"),
    });
  }

  if (changedFiles.length) {
    items.push({
      id: `task-files:${activeTask.id}`,
      kind: "task",
      title: "变更文件",
      status: "recorded",
      summary: `${changedFiles.length} 个文件：${compactList(
        changedFiles.map((file) => file.path),
      )}`,
      meta: compactMeta([`${changedFiles.length} 个文件`]),
      code: changedFiles.map(formatTaskFileChange).join("\n"),
    });
  }

  if (commands.length) {
    const status = aggregateRuntimeStatus(
      commands.map((command) => command.status),
      "recorded",
    );
    items.push({
      id: `task-commands:${activeTask.id}`,
      kind: "command",
      title: "命令执行",
      status,
      summary: `已跟踪 ${commands.length} 条命令`,
      meta: compactMeta([`${commands.length} 条命令`]),
      code: commands.map(formatTaskCommand).join("\n"),
    });
  }

  if (verification.length) {
    const status = aggregateRuntimeStatus(
      verification.map((record) => record.status),
      "recorded",
    );
    items.push({
      id: `task-verification:${activeTask.id}`,
      kind: "task",
      title: "验证",
      status,
      summary: `${verification.length} 个验证检查：${formatStatusLabel(status)}`,
      meta: compactMeta([`${verification.length} 个检查`]),
      code: verification.map(formatTaskVerification).join("\n"),
    });
  }

  return items;
}

function buildSessionMemoryRuntimeItems(_session?: SessionWorkspaceSession | null): RuntimeTimelineItem[] {
  // Session memory is internal context; hidden from user-facing UI.
  return [];
}

function buildContextPreviewRuntimeItems(_contextPreview?: SessionWorkspaceContextPreview): RuntimeTimelineItem[] {
  // Context preview is internal system state; hidden from user-facing UI.
  return [];
}

const hiddenTraceTypes = new Set([
  "assistant.token",
  "provider.request",
  "provider.response",
  "task.started",
  "task.orphaned",
]);

const hiddenTracePrefixes = ["tool.", "command.", "patch.", "approval."];

const visibleTraceTypes = new Set([
  "routing.decision",
  "runtime.error",
  "provider.error",
  "mcp.error",
  "context.trimmed",
  "task.failed",
  "task.cancelled",
]);

function isRawJsonLike(value?: string) {
  const trimmed = value?.trim();
  return Boolean(trimmed && ((trimmed.startsWith("{") && trimmed.endsWith("}")) || (trimmed.startsWith("[") && trimmed.endsWith("]"))));
}

function isUserVisibleTrace(trace: SessionWorkspaceTrace) {
  const type = trace.type.toLowerCase();
  const status = trace.status?.toLowerCase();
  if (hiddenTraceTypes.has(type) || hiddenTracePrefixes.some((prefix) => type.startsWith(prefix))) {
    return false;
  }
  if (visibleTraceTypes.has(type) || type.endsWith(".failed") || type.endsWith(".error")) {
    return true;
  }
  return Boolean(status && ["failed", "error", "warning", "cancelled"].includes(status));
}

function formatTraceTitle(trace: SessionWorkspaceTrace) {
  if (trace.title && trace.title !== trace.type) {
    return trace.title;
  }
  return trace.type
    .split(".")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function formatTraceSummary(trace: SessionWorkspaceTrace) {
  if (trace.summary && !isRawJsonLike(trace.summary)) {
    return trace.summary;
  }
  if (trace.stderr) {
    return compactText(trace.stderr, 180);
  }
  if (trace.detail && !isRawJsonLike(trace.detail)) {
    return compactText(trace.detail, 180);
  }
  if (trace.status && ["failed", "error", "warning", "cancelled"].includes(trace.status.toLowerCase())) {
    return `${formatTraceTitle(trace)} ${trace.status}`;
  }
  return "运行时诊断事件";
}

function buildTraceDetail(trace: SessionWorkspaceTrace) {
  return compactMeta([
    trace.stderr ? `错误\n${compactText(trace.stderr, 800)}` : null,
    trace.stdout && !isRawJsonLike(trace.stdout) ? `输出\n${compactText(trace.stdout, 800)}` : null,
    trace.detail && !isRawJsonLike(trace.detail) ? `详情\n${compactText(trace.detail, 800)}` : null,
  ]).join("\n\n");
}

function buildRuntimeItems({
  session,
  activeTask,
  contextPreview,
  approvals = [],
  patches = [],
  traces = [],
  toolCalls = [],
  backgroundJobs = [],
}: Pick<
  SessionWorkspaceProps,
  "session" | "activeTask" | "contextPreview" | "approvals" | "patches" | "traces" | "toolCalls" | "backgroundJobs"
>): RuntimeTimelineItem[] {
  const items: RuntimeTimelineItem[] = [
    ...buildActiveTaskRuntimeItems(activeTask),
  ];

  patches.forEach((patch) => {
    const changeStats = compactMeta([
      patch.additions !== undefined ? `+${patch.additions}` : null,
      patch.deletions !== undefined ? `-${patch.deletions}` : null,
    ]);
    const diffLines = patch.diff ? parseUnifiedDiff(patch.diff) : undefined;
    const fileSummaries = patch.files?.map(
      (file) => `${file.path}${file.additions !== undefined ? ` (+${file.additions}/-${file.deletions ?? 0})` : ""}`,
    );
    items.push({
      id: `patch:${patch.id}`,
      kind: "patch",
      sourceId: patch.id,
      title: patch.summary || "改动",
      status: patch.status,
      summary: patch.files && patch.files.length > 0
        ? `${patch.files.length} 个文件：${compactList(patch.files.map((f) => f.path))}`
        : undefined,
      meta: compactMeta([
        patch.filesChanged !== undefined ? `${patch.filesChanged} 个文件` : null,
        ...changeStats,
      ]),
      code: fileSummaries?.join("\n"),
      diffLines,
      time: patch.updatedAt,
    });
  });

  traces.filter(isUserVisibleTrace).forEach((trace) => {
    const outputDetail = buildTraceDetail(trace);
    items.push({
      id: `trace:${trace.id}`,
      kind: "trace",
      sourceId: trace.id,
      title: formatTraceTitle(trace),
      status: trace.status,
      summary: formatTraceSummary(trace),
      meta: compactMeta([
        trace.type,
        trace.source,
        formatDuration(trace.durationMs),
        trace.tokenCount !== undefined ? `${trace.tokenCount} 令牌` : null,
      ]),
      code: outputDetail || undefined,
      time: trace.time,
    });
  });

  approvals.forEach((approval) => {
    items.push({
      id: `approval:${approval.id}`,
      kind: "approval",
      sourceId: approval.id,
      title: approval.title,
      status: approval.status,
      summary: approval.summary,
      meta: compactMeta([approval.kind, approval.risk ? `风险：${formatStatusLabel(`${approval.risk} risk`)}` : null, approval.cwd]),
      code: approval.command || approval.parametersPreview,
      rawDetail: approval.fullInput,
      time: approval.requestedAt,
    });
  });

  toolCalls.forEach((toolCall) => {
    const presentation = buildToolRuntimePresentation(toolCall);
    items.push({
      id: `tool:${toolCall.id}`,
      kind: presentation.kind,
      title: presentation.title,
      status: toolCall.status,
      summary: presentation.summary,
      meta: presentation.meta,
      code: presentation.code,
      rawDetail: compactMeta([toolCall.rawInput ? `输入\n${toolCall.rawInput}` : null, toolCall.rawOutput ? `输出\n${toolCall.rawOutput}` : null]).join("\n\n"),
      time: toolCall.time,
    });
  });

  backgroundJobs.forEach((job) => {
    items.push({
      id: `command:${job.id}`,
      kind: "command",
      title: job.command,
      status: job.status,
      summary: job.summary || job.stdout || job.stderr,
      meta: compactMeta([
        job.cwd,
        job.shell,
        job.exitCode !== undefined && job.exitCode !== null ? `退出码 ${job.exitCode}` : null,
        formatDuration(job.durationMs),
      ]),
      code: job.stdoutPath || job.stderrPath,
      time: job.startedAt ?? job.finishedAt,
    });
  });

  return items;
}

function getRuntimeKindLabel(kind: RuntimeTimelineItem["kind"]) {
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

function getStatusTone(status?: string): "neutral" | "primary" | "success" | "warning" | "danger" | "info" {
  if (!status) {
    return "neutral";
  }
  if (["completed", "succeeded", "approved", "applied", "passed"].includes(status)) {
    return "success";
  }
  if (["running", "started", "planning", "verifying"].includes(status)) {
    return "info";
  }
  if (["pending", "queued", "waiting_approval"].includes(status)) {
    return "warning";
  }
  if (["failed", "error", "cancelled", "rejected"].includes(status)) {
    return "danger";
  }
  return "neutral";
}

function isTaskControllable(status?: string) {
  return Boolean(status && ["running", "planning", "verifying", "waiting_approval", "queued"].includes(status));
}

function parsePatchPath(line: string) {
  return line
    .replace(/\s+\(\+\d+\/-\d+\).*$/, "")
    .replace(/^(added|modified|deleted|changed)\s+/i, "")
    .trim();
}

function parsePatchFileSummary(line: string) {
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

function parsePatchFileSummaries(code?: string) {
  if (!code) {
    return [];
  }
  return code
    .split("\n")
    .map(parsePatchFileSummary)
    .filter((entry): entry is NonNullable<ReturnType<typeof parsePatchFileSummary>> => Boolean(entry));
}

function buildCommandOutput(item: RuntimeTimelineItem) {
  return compactMeta([item.summary, item.code]).join("\n\n") || "暂无输出。";
}

function normalizeMarkdownContent(content: string) {
  return content
    .replace(/\r\n/g, "\n")
    .replace(/([^\n])(\s+#{1,3}\s+)/g, "$1\n$2")
    .replace(/([^\n])(\s+-\s+\*\*)/g, "$1\n$2")
    .replace(/([^\n])(\s+\d+\.\s+\*\*)/g, "$1\n$2");
}

function isSafeLink(url: string) {
  return /^(https?:|mailto:)/i.test(url);
}

function renderInlineMarkdown(text: string, keyPrefix: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  const pattern = /(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\([^)]+\))/g;
  let cursor = 0;
  let match: RegExpExecArray | null;

  while ((match = pattern.exec(text)) !== null) {
    if (match.index > cursor) {
      nodes.push(text.slice(cursor, match.index));
    }

    const token = match[0];
    const key = `${keyPrefix}-${match.index}`;
    if (token.startsWith("**") && token.endsWith("**")) {
      nodes.push(<strong key={key}>{renderInlineMarkdown(token.slice(2, -2), `${key}-strong`)}</strong>);
    } else if (token.startsWith("`") && token.endsWith("`")) {
      nodes.push(<code key={key}>{token.slice(1, -1)}</code>);
    } else {
      const linkMatch = token.match(/^\[([^\]]+)\]\(([^)]+)\)$/);
      if (linkMatch && isSafeLink(linkMatch[2])) {
        nodes.push(
          <a href={linkMatch[2]} key={key} rel="noreferrer" target="_blank">
            {linkMatch[1]}
          </a>,
        );
      } else {
        nodes.push(token);
      }
    }

    cursor = match.index + token.length;
  }

  if (cursor < text.length) {
    nodes.push(text.slice(cursor));
  }

  return nodes;
}

function isTableDivider(line: string) {
  return /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$/.test(line);
}

function parseTableRow(line: string) {
  return line
    .trim()
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split("|")
    .map((cell) => cell.trim());
}

function isMarkdownBlockStart(line: string) {
  return (
    /^#{1,3}\s+/.test(line) ||
    /^[-*]\s+/.test(line) ||
    /^\d+\.\s+/.test(line) ||
    /^```/.test(line) ||
    (line.includes("|") && isTableDivider(line))
  );
}

function MarkdownContent({ content }: { content: string }) {
  const lines = normalizeMarkdownContent(content).split("\n");
  const blocks: ReactNode[] = [];
  let index = 0;

  while (index < lines.length) {
    const line = lines[index];
    if (!line.trim()) {
      index += 1;
      continue;
    }

    const fence = line.match(/^```\s*([\w-]+)?\s*$/);
    if (fence) {
      const codeLines: string[] = [];
      index += 1;
      while (index < lines.length && !/^```\s*$/.test(lines[index])) {
        codeLines.push(lines[index]);
        index += 1;
      }
      if (index < lines.length) {
        index += 1;
      }
      blocks.push(
        <pre className="markdown-code-block" key={`code-${index}`}>
          <code>{codeLines.join("\n")}</code>
        </pre>,
      );
      continue;
    }

    const heading = line.match(/^(#{1,3})\s+(.+)$/);
    if (heading) {
      const level = heading[1].length;
      const children = renderInlineMarkdown(heading[2], `heading-${index}`);
      blocks.push(
        level === 1 ? (
          <h2 key={`h-${index}`}>{children}</h2>
        ) : level === 2 ? (
          <h3 key={`h-${index}`}>{children}</h3>
        ) : (
          <h4 key={`h-${index}`}>{children}</h4>
        ),
      );
      index += 1;
      continue;
    }

    if (line.includes("|") && index + 1 < lines.length && isTableDivider(lines[index + 1])) {
      const headers = parseTableRow(line);
      const rows: string[][] = [];
      index += 2;
      while (index < lines.length && lines[index].includes("|") && lines[index].trim()) {
        rows.push(parseTableRow(lines[index]));
        index += 1;
      }
      blocks.push(
        <div className="markdown-table-wrap" key={`table-${index}`}>
          <table>
            <thead>
              <tr>
                {headers.map((header, cellIndex) => (
                  <th key={`${header}-${cellIndex}`}>{renderInlineMarkdown(header, `th-${index}-${cellIndex}`)}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, rowIndex) => (
                <tr key={`row-${index}-${rowIndex}`}>
                  {row.map((cell, cellIndex) => (
                    <td key={`cell-${index}-${rowIndex}-${cellIndex}`}>
                      {renderInlineMarkdown(cell, `td-${index}-${rowIndex}-${cellIndex}`)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>,
      );
      continue;
    }

    if (/^[-*]\s+/.test(line) || /^\d+\.\s+/.test(line)) {
      const ordered = /^\d+\.\s+/.test(line);
      const items: string[] = [];
      while (index < lines.length && (ordered ? /^\d+\.\s+/.test(lines[index]) : /^[-*]\s+/.test(lines[index]))) {
        items.push(lines[index].replace(ordered ? /^\d+\.\s+/ : /^[-*]\s+/, ""));
        index += 1;
      }
      const ListTag = ordered ? "ol" : "ul";
      blocks.push(
        <ListTag key={`list-${index}`}>
          {items.map((item, itemIndex) => (
            <li key={`${itemIndex}-${item.slice(0, 12)}`}>{renderInlineMarkdown(item, `li-${index}-${itemIndex}`)}</li>
          ))}
        </ListTag>,
      );
      continue;
    }

    const paragraphLines = [line];
    index += 1;
    while (index < lines.length && lines[index].trim() && !isMarkdownBlockStart(lines[index])) {
      paragraphLines.push(lines[index]);
      index += 1;
    }
    blocks.push(<p key={`p-${index}`}>{renderInlineMarkdown(paragraphLines.join("\n"), `p-${index}`)}</p>);
  }

  return <div className="markdown-content">{blocks}</div>;
}

const RuntimeEventCard = memo(function RuntimeEventCard({
  item,
  onApprove,
  onReject,
  onLoadPatch,
  onCopyPatchPath,
  onCopyRuntimeText,
  onRefreshCommandJob,
  onStopCommandJob,
  busyId,
}: {
  item: RuntimeTimelineItem;
  onApprove?(approvalId: string): void | Promise<void>;
  onReject?(approvalId: string): void | Promise<void>;
  onLoadPatch?(patchId: string): void | Promise<void>;
  onCopyPatchPath?(patchId: string, path: string): void | Promise<void>;
  onCopyRuntimeText?(label: string, text: string): void | Promise<void>;
  onRefreshCommandJob?(commandId: string): void | Promise<void>;
  onStopCommandJob?(commandId: string): void | Promise<void>;
  busyId?: string | null;
}) {
  const [expanded, setExpanded] = useState(false);
  const kindLabel = getRuntimeKindLabel(item.kind);
  const canResolveApproval = item.kind === "approval" && item.status === "pending" && item.sourceId;
  const canLoadPatch = item.kind === "patch" && Boolean(item.sourceId && onLoadPatch);
  const patchPaths = item.kind === "patch" && item.sourceId && item.code
    ? item.code.split("\n").map(parsePatchPath).filter(Boolean)
    : [];
  const canRefreshCommand = item.kind === "command" && Boolean(item.sourceId && onRefreshCommandJob);
  const canStopCommand =
    item.kind === "command" &&
    Boolean(item.sourceId && onStopCommandJob && ["running", "started"].includes(item.status ?? ""));
  const isBusy = item.sourceId ? busyId === item.sourceId : false;
  const commandOutput = item.kind === "command" ? buildCommandOutput(item) : "";
  const canCopyCommandOutput = Boolean(item.kind === "command" && onCopyRuntimeText && commandOutput.trim());
  const canCopyTraceDetail = Boolean(item.kind === "trace" && onCopyRuntimeText && item.code?.trim());
  const hasCommandActions = canRefreshCommand || canStopCommand || canCopyCommandOutput;

  if (item.kind === "approval" && item.sourceId) {
    return (
      <div className="runtime-event-card runtime-event-v2-card" data-activity-kind="runtime" data-kind={item.kind}>
        <ApprovalCard
          approval={{
            id: item.sourceId,
            title: item.title,
            kind: item.meta?.[0],
            status: item.status ?? "pending",
            summary: item.summary,
            risk: item.meta?.some((entry) => entry.includes("high")) ? "high" : item.meta?.some((entry) => entry.includes("medium")) ? "medium" : "low",
            command: item.code,
            cwd: item.meta?.find((entry) => /^[A-Z]:|^\//.test(entry)),
            requestedAt: item.time,
          }}
          busy={isBusy}
          onApprove={(approvalId) => {
            void onApprove?.(approvalId);
          }}
          onReject={(approvalId) => {
            void onReject?.(approvalId);
          }}
        />
      </div>
    );
  }

  if (item.kind === "patch" && item.sourceId) {
    return (
      <div className="runtime-event-card runtime-event-v2-card" data-activity-kind="runtime" data-kind={item.kind}>
        <PatchPlanCard
          patch={{
            id: item.sourceId,
            summary: item.title,
            status: item.status ?? "recorded",
            filesChanged: parsePatchFileSummaries(item.code).length || undefined,
          }}
          changedFiles={parsePatchFileSummaries(item.code)}
          onOpenDiff={(patchId) => {
            void onLoadPatch?.(patchId);
            setExpanded(true);
          }}
        />
        <PatchDiffDetail expanded={expanded} diffLines={item.diffLines} isBusy={isBusy} />
      </div>
    );
  }

  if (item.kind === "command") {
    return (
      <div className="runtime-event-card runtime-event-v2-card" data-activity-kind="runtime" data-kind={item.kind}>
        <CommandOutputPanel
          command={{
            id: item.sourceId ?? item.id,
            command: item.title,
            status: item.status ?? "recorded",
            stdout: commandOutput,
          }}
        />
        {hasCommandActions ? (
          <div className="runtime-event-actions">
            {canCopyCommandOutput ? (
              <Button
                size="xs"
                variant="secondary"
                aria-label="复制输出"
                onClick={() => {
                  void onCopyRuntimeText?.("命令输出", commandOutput);
                }}
              >
                复制输出
              </Button>
            ) : null}
            {canRefreshCommand ? (
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
            {canStopCommand ? (
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
      </div>
    );
  }

  if (item.kind === "tool") {
    return (
      <div className="runtime-event-card runtime-event-v2-card" data-activity-kind="runtime" data-kind={item.kind}>
        <ToolTraceCard
          toolCall={{
            id: item.sourceId ?? item.id,
            toolName: item.title,
            status: item.status ?? "recorded",
            inputPreview: item.code,
            outputPreview: item.summary,
            startedAt: item.time,
          }}
        />
      </div>
    );
  }

  if (item.kind === "trace") {
    return (
      <article
        className="runtime-event-card runtime-trace-row"
        data-activity-kind="runtime"
        data-kind={item.kind}
        data-status={item.status ?? "recorded"}
      >
        <button
          aria-label={`${kindLabel} ${item.title}${item.status ? ` ${formatStatusLabel(item.status)}` : ""}`}
          aria-expanded={expanded}
          className="runtime-trace-row-summary"
          onClick={() => setExpanded((current) => !current)}
          type="button"
        >
          <span className="runtime-trace-dot" aria-hidden="true" />
          <span className="runtime-trace-row-copy">
            <strong>{item.title}</strong>
            {item.summary ? <small>{compactText(item.summary, 160)}</small> : null}
          </span>
          {item.status ? <StatusBadge label={formatStatusLabel(item.status)} tone={getStatusTone(item.status)} compact /> : null}
          <i aria-hidden="true">{expanded ? "^" : "v"}</i>
        </button>
        {item.meta?.length ? (
          <div className="runtime-trace-row-meta">
            {item.meta.slice(0, expanded ? 5 : 3).map((entry) => (
              <span key={entry}>{entry}</span>
            ))}
          </div>
        ) : null}
        {expanded && item.code ? (
          <div className="runtime-trace-row-detail">
            {canCopyTraceDetail ? (
              <div className="runtime-trace-row-actions">
                <Button
                  size="xs"
                  variant="secondary"
                aria-label="复制详情"
                  onClick={() => {
                    void onCopyRuntimeText?.("诊断详情", item.code ?? "");
                  }}
                >
                  复制详情
                </Button>
              </div>
            ) : null}
            <pre>{item.code}</pre>
          </div>
        ) : null}
      </article>
    );
  }

  return (
    <article className="runtime-event-card" data-activity-kind="runtime" data-kind={item.kind}>
      <button
        aria-label={`${kindLabel} ${item.title}${item.status ? ` ${formatStatusLabel(item.status)}` : ""}`}
        aria-expanded={expanded}
        className="runtime-event-summary"
        onClick={() => setExpanded((current) => !current)}
        type="button"
      >
        <span>{kindLabel}</span>
        <strong>{item.title}</strong>
        {item.status ? <StatusBadge label={formatStatusLabel(item.status)} tone={getStatusTone(item.status)} /> : null}
        <i aria-hidden="true">{expanded ? "⌃" : "⌄"}</i>
      </button>
      {item.meta?.length && !expanded ? (
        <div className="runtime-event-meta runtime-event-meta-compact">
          {item.meta.slice(0, 4).map((entry) => (
            <span key={entry}>{entry}</span>
          ))}
        </div>
      ) : null}
      {!expanded && item.summary ? (
        <p className="runtime-event-collapsed-summary">{compactText(item.summary, 180)}</p>
      ) : null}
      {canResolveApproval ? (
        <div className="runtime-event-actions">
          <button
            aria-label={`批准 ${item.title}`}
            onClick={() => {
              void onApprove?.(item.sourceId ?? "");
            }}
            type="button"
          >
            批准
          </button>
          <button
            aria-label={`拒绝 ${item.title}`}
            onClick={() => {
              void onReject?.(item.sourceId ?? "");
            }}
            type="button"
          >
            拒绝
          </button>
        </div>
      ) : null}
      {canLoadPatch || canRefreshCommand || canStopCommand ? (
        <div className="runtime-event-actions">
          {canLoadPatch ? (
            <Button
              size="xs"
              variant="secondary"
              loading={isBusy}
              onClick={() => {
                void onLoadPatch?.(item.sourceId ?? "");
                setExpanded(true);
              }}
            >
              加载差异
            </Button>
          ) : null}
          {canRefreshCommand ? (
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
          {canStopCommand ? (
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
      {expanded ? (
        <div className="runtime-event-detail">
          {item.meta?.length ? (
            <div className="runtime-event-meta">
              {item.meta.map((entry) => (
                <span key={entry}>{entry}</span>
              ))}
            </div>
          ) : null}
          {item.summary ? <p>{item.summary}</p> : null}
          {item.code ? <p className="runtime-event-code-summary">{item.code}</p> : null}
          {patchPaths.length && item.sourceId && onCopyPatchPath ? (
            <div className="runtime-patch-files" aria-label="改动文件">
              {patchPaths.map((path) => (
                <button
                  key={path}
                  type="button"
                  onClick={() => {
                    void onCopyPatchPath(item.sourceId ?? "", path);
                  }}
                >
                  <span>{path}</span>
                  <strong>复制路径</strong>
                </button>
              ))}
            </div>
          ) : null}
          {item.kind === "patch" ? <PatchDiffBody diffLines={item.diffLines} isBusy={isBusy} /> : null}

        </div>
      ) : null}
    </article>
  );
});

function PatchDiffDetail({
  expanded,
  diffLines,
  isBusy,
}: {
  expanded: boolean;
  diffLines?: DiffLine[];
  isBusy: boolean;
}) {
  if (!expanded) {
    return null;
  }

  return (
    <div className="runtime-event-detail">
      <PatchDiffBody diffLines={diffLines} isBusy={isBusy} />
    </div>
  );
}

function PatchDiffBody({ diffLines, isBusy }: { diffLines?: DiffLine[]; isBusy: boolean }) {
  if (!diffLines || diffLines.length === 0) {
    return (
      <p className="runtime-diff-empty" role="status">
        {isBusy ? "差异正在加载。" : "差异暂不可用。请在运行时写入改动后再试一次。"}
      </p>
    );
  }

  return (
    <div className="diff-view">
      {diffLines.map((line, lineIndex) => (
        <div key={lineIndex} className={`diff-line diff-line-${line.type}`}>
          <span className="diff-line-prefix">
            {line.type === "add" ? "+" : line.type === "remove" ? "-" : line.type === "header" ? "" : " "}
          </span>
          <span className="diff-line-content">{line.content}</span>
        </div>
      ))}
    </div>
  );
}

const MessageBubble = memo(function MessageBubble({ message }: { message: SessionWorkspaceMessage }) {
  const isThinking = message.streaming && message.placeholder;
  return (
    <article
      className={`message-bubble${isThinking ? " message-bubble-thinking" : ""}`}
      data-activity-kind="message"
      data-role={message.role}
      aria-label={`${getRoleLabel(message.role)}消息`}
    >
      <div className="message-bubble-head">
        <span>{getRoleLabel(message.role)}</span>
        {message.toolName ? <em>{message.toolName}</em> : null}
        {message.status ? <em>{formatStatusLabel(message.status)}</em> : null}
        {message.streaming && !message.placeholder ? <em>流式输出</em> : null}
        {message.createdAt ? <time>{formatTimestamp(message.createdAt)}</time> : null}
      </div>
      {isThinking ? (
        <div className="thinking-dots" aria-label="思考中">
          <span /><span /><span />
        </div>
      ) : message.role === "assistant" ? (
        <MarkdownContent content={message.content} />
      ) : (
        <p>{message.content}</p>
      )}
    </article>
  );
});

function buildConversationActivity(
  messages: SessionWorkspaceMessage[],
  runtimeItems: RuntimeTimelineItem[],
): ConversationActivityItem[] {
  const activity: ConversationActivityItem[] = [
    ...messages.map((message, index) => ({
      id: `message:${message.id}`,
      kind: "message" as const,
      order: index,
      time: message.createdAt,
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

const ConversationActivity = memo(function ConversationActivity({
  items,
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
  onApprove?(approvalId: string): void | Promise<void>;
  onReject?(approvalId: string): void | Promise<void>;
  onLoadPatch?(patchId: string): void | Promise<void>;
  onCopyPatchPath?(patchId: string, path: string): void | Promise<void>;
  onCopyRuntimeText?(label: string, text: string): void | Promise<void>;
  onRefreshCommandJob?(commandId: string): void | Promise<void>;
  onStopCommandJob?(commandId: string): void | Promise<void>;
  busyId?: string | null;
}) {
  return (
    <div className="conversation-activity" aria-label="会话活动">
      {items.map((item) =>
        item.kind === "message" ? (
          <MessageBubble message={item.message} key={item.id} />
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
    </div>
  );
});

export function SessionWorkspace({
  session,
  messages,
  activeTask,
  contextPreview,
  approvals,
  patches,
  traces,
  toolCalls,
  backgroundJobs,
  onApprove,
  onReject,
  onLoadPatch,
  onCopyPatchPath,
  onCopyRuntimeText,
  onRefreshCommandJob,
  onStopCommandJob,
  onRefreshTask,
  onStopTask,
  onRefreshTrace,
  taskBusyAction,
  busyId,
  messagesLoading,
  taskCount,
  composerContext,
}: SessionWorkspaceProps) {
  if (!session) {
    return (
      <main className="session-workspace session-workspace-empty" aria-labelledby="session-empty-title">
        <section className="session-empty-card">
          <span className="session-empty-rule" aria-hidden="true" />
          <p className="session-kicker">会话工作台</p>
          <h1 id="session-empty-title">打开或创建会话</h1>
          <p>从侧栏选择一个会话，或新建会话后开始对话。</p>
        </section>
      </main>
    );
  }

  const runtimeItems = useMemo(
    () =>
      buildRuntimeItems({
        session,
        activeTask,
        contextPreview,
        approvals,
        patches,
        traces,
        toolCalls,
        backgroundJobs,
      }),
    [activeTask, approvals, backgroundJobs, contextPreview, patches, session, toolCalls, traces],
  );
  const activityItems = useMemo(
    () => buildConversationActivity(messages, runtimeItems),
    [messages, runtimeItems],
  );
  const pendingApprovals = approvals?.filter((approval) => approval.status === "pending").length ?? 0;
  const patchCount = patches?.length ?? 0;
  const commandCount = runtimeItems.filter((item) => item.kind === "command").length;
  const diagnosticCount = runtimeItems.filter((item) => item.kind === "trace").length;
  const activeTaskStatus = activeTask?.status ?? "idle";
  const contextBudgetStats = contextPreview?.budgetStats;
  const contextUsedTokens =
    contextBudgetStats?.estimatedInputTokens ?? contextBudgetStats?.estimatedTokens ?? contextBudgetStats?.messageTokens;
  const contextMaxTokens = contextBudgetStats?.maxContextTokens;
  const runtimeLanes = [
    {
      id: "commands",
      eyebrow: "执行",
      title: "命令通道",
      emptyTitle: "暂无运行中的命令",
      emptyText: "Shell 任务会显示在这里，并提供停止和刷新控制。",
      items: runtimeItems.filter((item) => item.kind === "command" || item.kind === "tool"),
    },
    {
      id: "patches",
      eyebrow: "改动",
      title: "改动队列",
      emptyTitle: "暂无改动",
      emptyText: "生成的差异会先显示在这里，再进入活动流。",
      items: runtimeItems.filter((item) => item.kind === "patch"),
    },
    {
      id: "trace",
      eyebrow: "诊断",
      title: "重要信号",
      emptyTitle: "暂无诊断",
      emptyText: "失败、路由决策和可操作信号会显示在这里。",
      items: runtimeItems.filter((item) => item.kind === "trace" || item.kind === "approval" || item.kind === "task"),
    },
  ];

  return (
    <main className="session-workspace session-workspace-chat-only" aria-labelledby="session-title">
      <section className="session-workbench-grid">
        <section className="session-conversation-column">
          <header className="session-chat-header">
            <div className="session-chat-title-block">
              <p className="session-kicker">会话</p>
              <h1 id="session-title">{session.title}</h1>
              <div className="session-chip-row" aria-label="会话上下文">
                <StatusBadge label={formatStatusLabel(session.status ?? "active")} tone={getStatusTone(session.status)} />
                {activeTask?.status ? <StatusBadge label={formatStatusLabel(activeTask.status)} tone={getStatusTone(activeTask.status)} pulse={isTaskControllable(activeTask.status)} /> : null}
                {taskCount !== undefined ? <span>{taskCount} 个任务</span> : null}
                {composerContext?.model ? <span>{composerContext.model}</span> : null}
                {composerContext?.permissionMode ? <span>审批：{composerContext.permissionMode}</span> : null}
              </div>
            </div>
            <div className="session-chat-actions" aria-label="会话操作">
              <Button
                size="sm"
                variant="secondary"
                loading={taskBusyAction === "refresh"}
                disabled={!activeTask || !onRefreshTask}
                onClick={() => {
                  void onRefreshTask?.();
                }}
              >
                刷新任务
              </Button>
              <Button
                size="sm"
                variant="secondary"
                disabled={!onRefreshTrace}
                loading={busyId === "trace"}
                onClick={() => {
                  void onRefreshTrace?.();
                }}
              >
                刷新诊断
              </Button>
              <Button
                size="sm"
                variant="danger"
                loading={taskBusyAction === "stop"}
                disabled={!activeTask || !isTaskControllable(activeTask.status) || !onStopTask}
                onClick={() => {
                  if (activeTask) {
                    void onStopTask?.(activeTask.id);
                  }
                }}
              >
                停止任务
              </Button>
            </div>
          </header>

          <section className="session-console" aria-label="运行时控制台">
            <header className="session-console-heading">
              <div>
                <p className="session-kicker">活动流</p>
                <h2>消息与操作</h2>
              </div>
              <span>{activityItems.length} 个事件</span>
            </header>
            <div className="message-stream message-stream-chat-only" aria-label="会话消息">
              {messagesLoading && activityItems.length === 0 ? (
                <div className="message-stream-loading" aria-label="加载消息">
                  <div className="message-stream-loading-bar" />
                </div>
              ) : activityItems.length === 0 ? (
                <div className="message-stream-empty">
                  <p className="session-kicker">安静线程</p>
                  <h2>还没有消息</h2>
                  <p>从下方输入区发送第一条消息。</p>
                </div>
              ) : (
                <ConversationActivity
                  items={activityItems}
                  onApprove={onApprove}
                  onReject={onReject}
                  onLoadPatch={onLoadPatch}
                  onCopyPatchPath={onCopyPatchPath}
                  onCopyRuntimeText={onCopyRuntimeText}
                  onRefreshCommandJob={onRefreshCommandJob}
                  onStopCommandJob={onStopCommandJob}
                  busyId={busyId}
                />
              )}
            </div>
          </section>
        </section>

        <aside className="session-runtime-column" aria-label="运行时智能状态">
          <section className="session-runtime-dashboard" aria-label="运行时仪表盘">
            <div>
              <p className="session-kicker">任务状态</p>
              <strong>{formatStatusLabel(activeTaskStatus)}</strong>
              <span>{activeTask?.currentStep ?? activeTask?.goal ?? "等待下一条指令"}</span>
            </div>
            <dl>
              <div>
                <dt>消息</dt>
                <dd>{messages.length}</dd>
              </div>
              <div>
                <dt>命令</dt>
                <dd>{commandCount}</dd>
              </div>
              <div>
                <dt>改动</dt>
                <dd>{patchCount}</dd>
              </div>
              <div>
                <dt>审批</dt>
                <dd>{pendingApprovals}</dd>
              </div>
              <div>
                <dt>信号</dt>
                <dd>{diagnosticCount}</dd>
              </div>
            </dl>
          </section>

          {activeTask?.currentStep || activeTask?.goal ? (
            <Panel className="session-task-panel" eyebrow="运行时重点" title={activeTask?.currentStep ?? "等待下一个任务"}>
              <div className="session-task-panel-grid">
                <p>{activeTask?.goal ?? session.summary ?? "当前会话没有正在运行的任务。"}</p>
                {composerContext?.cwd ? <code>{composerContext.cwd}</code> : null}
              </div>
            </Panel>
          ) : null}

          {typeof contextUsedTokens === "number" && typeof contextMaxTokens === "number" ? (
            <ContextBudgetBar
              usedTokens={contextUsedTokens}
              reservedTokens={contextBudgetStats?.toolSchemaTokens ?? 0}
              maxTokens={contextMaxTokens}
              label="会话上下文"
            />
          ) : null}

          <section className="session-runtime-lanes" aria-label="执行通道">
            {runtimeLanes.map((lane) => (
              <article className="session-runtime-lane" data-lane={lane.id} key={lane.id}>
                <header>
                  <div>
                    <p className="session-kicker">{lane.eyebrow}</p>
                    <h3>{lane.title}</h3>
                  </div>
                  <span>{lane.items.length}</span>
                </header>
                {lane.items.length > 0 ? (
                  <ul>
                    {lane.items.slice(0, 3).map((item) => (
                      <li key={item.id}>
                        <div>
                          <strong>{getRuntimeKindLabel(item.kind)}事件</strong>
                          <span>{item.meta?.slice(0, 2).join(" - ") || "详情可在活动流中查看"}</span>
                        </div>
                        {item.status ? <StatusBadge label={formatStatusLabel(item.status)} tone={getStatusTone(item.status)} compact /> : null}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <div className="session-runtime-lane-empty">
                    <strong>{lane.emptyTitle}</strong>
                    <span>{lane.emptyText}</span>
                  </div>
                )}
              </article>
            ))}
          </section>
        </aside>
      </section>
    </main>
  );
}

export default SessionWorkspace;
