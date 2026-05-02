import { memo, useMemo, useState, type ReactNode } from "react";
import {
  ApprovalCard,
  CommandOutputPanel,
  ContextBudgetBar,
  PatchPlanCard,
  ToolTraceCard,
} from "../../../v2/components/runtime";
import { Button, Panel, StatusBadge } from "../../../v2/components/ui";
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
      return "Assistant";
    case "system":
      return "System";
    case "tool":
      return "Tool";
    case "user":
      return "User";
    default:
      return "Message";
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
  return `${text.slice(0, Math.max(1, maxChars - 14)).trimEnd()} [truncated]`;
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
      content: `[Diff truncated: showing first ${MAX_RENDERED_DIFF_LINES} of ${rawLines.length} lines]`,
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
  return `${used}/${max} tokens`;
}

function formatSignedCount(value: number | undefined, prefix: string) {
  if (value === undefined) {
    return null;
  }

  return `${prefix}${value}`;
}

function formatTaskFileChange(file: NonNullable<SessionWorkspaceActiveTask["changedFiles"]>[number]) {
  const changeStats = compactMeta([formatSignedCount(file.additions, "+"), formatSignedCount(file.deletions, "-")]).join(" ");
  const status = file.status ?? "changed";
  const suffix = compactMeta([changeStats, file.reason]).join(" - ");
  return `${status} ${file.path}${suffix ? ` - ${suffix}` : ""}`;
}

function formatTaskCommand(command: NonNullable<SessionWorkspaceActiveTask["commands"]>[number]) {
  const meta = compactMeta([
    command.status,
    command.exitCode !== undefined && command.exitCode !== null ? `exit ${command.exitCode}` : null,
    formatDuration(command.durationMs ?? undefined),
    command.cwd,
    command.background ? "background" : null,
  ]);
  return `${command.command}${meta.length ? ` - ${meta.join(" - ")}` : ""}`;
}

function formatTaskVerification(record: NonNullable<SessionWorkspaceActiveTask["verification"]>[number]) {
  const label = record.command ?? record.id ?? "verification";
  const meta = compactMeta([
    record.status,
    record.exitCode !== undefined && record.exitCode !== null ? `exit ${record.exitCode}` : null,
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
  const tokenCount = toolCall.tokenCount !== undefined ? `${toolCall.tokenCount} tokens` : null;
  const statusMeta = compactMeta([duration, tokenCount]);
  const resultSummary = summarizeRuntimeOutput(toolCall.resultSummary || toolCall.output || toolCall.stdout || toolCall.stderr);

  if (toolCall.toolName === "run_command") {
    return {
      kind: "command",
      title: command ?? toolCall.argsPreview ?? "Command",
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
      meta: compactMeta([path ? `path: ${path}` : toolCall.input, ...statusMeta]),
      code: toolCall.argsPreview,
    };
  }

  if (toolCall.toolName === "read_file") {
    return {
      kind: "tool",
      title: path ? `read_file ${path}` : "read_file",
      summary: resultSummary,
      meta: compactMeta([path ? `path: ${path}` : toolCall.input, ...statusMeta]),
      code: toolCall.argsPreview,
    };
  }

  if (toolCall.toolName === "write_file") {
    return {
      kind: "tool",
      title: path ? `write_file ${path}` : "write_file",
      summary: resultSummary,
      meta: compactMeta([path ? `path: ${path}` : toolCall.input, ...statusMeta]),
      code: toolCall.argsPreview,
    };
  }

  if (toolCall.toolName === "search_files") {
    return {
      kind: "tool",
      title: query ? `search_files "${query}"` : "search_files",
      summary: resultSummary,
      meta: compactMeta([path ? `root: ${path}` : null, ...statusMeta]),
      code: toolCall.argsPreview,
    };
  }

  if (toolCall.toolName === "code_search") {
    return {
      kind: "tool",
      title: query ? `code_search "${query}"` : "code_search",
      summary: resultSummary,
      meta: compactMeta([path ? `root: ${path}` : null, ...statusMeta]),
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
      meta: compactMeta([action ? `action: ${action}` : null, path ? `path: ${path}` : null, ...statusMeta]),
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
      title: "Task focus",
      status: activeTask.status,
      summary: activeTask.currentStep || activeTask.goal,
      meta: compactMeta([
        acceptanceCriteria.length ? `${acceptanceCriteria.length} acceptance` : null,
        outOfScope.length ? `${outOfScope.length} out of scope` : null,
      ]),
      code: compactMeta([
        activeTask.goal ? `📌 Goal:\n${activeTask.goal}` : null,
        acceptanceCriteria.length
          ? `✅ Acceptance criteria:\n${acceptanceCriteria.map((c, i) => `  ${i + 1}. ${c}`).join("\n")}`
          : null,
        outOfScope.length
          ? `🚫 Out of scope:\n${outOfScope.map((c, i) => `  ${i + 1}. ${c}`).join("\n")}`
          : null,
      ]).join("\n\n"),
    });
  }

  if (changedFiles.length) {
    items.push({
      id: `task-files:${activeTask.id}`,
      kind: "task",
      title: "Changed files",
      status: "recorded",
      summary: `${changedFiles.length} file${changedFiles.length === 1 ? "" : "s"}: ${compactList(
        changedFiles.map((file) => file.path),
      )}`,
      meta: compactMeta([`${changedFiles.length} files`]),
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
      title: "Command runs",
      status,
      summary: `${commands.length} command${commands.length === 1 ? "" : "s"} tracked`,
      meta: compactMeta([`${commands.length} commands`]),
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
      title: "Verification",
      status,
      summary: `${verification.length} verification check${verification.length === 1 ? "" : "s"} ${status}`,
      meta: compactMeta([`${verification.length} checks`]),
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
  return "Runtime diagnostic event";
}

function buildTraceDetail(trace: SessionWorkspaceTrace) {
  return compactMeta([
    trace.stderr ? `Error\n${compactText(trace.stderr, 800)}` : null,
    trace.stdout && !isRawJsonLike(trace.stdout) ? `Output\n${compactText(trace.stdout, 800)}` : null,
    trace.detail && !isRawJsonLike(trace.detail) ? `Detail\n${compactText(trace.detail, 800)}` : null,
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
      title: patch.summary || "Patch",
      status: patch.status,
      summary: patch.files && patch.files.length > 0
        ? `${patch.files.length} file${patch.files.length === 1 ? "" : "s"}: ${compactList(patch.files.map((f) => f.path))}`
        : undefined,
      meta: compactMeta([
        patch.filesChanged !== undefined ? `${patch.filesChanged} files` : null,
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
        trace.tokenCount !== undefined ? `${trace.tokenCount} tokens` : null,
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
      meta: compactMeta([approval.kind, approval.risk ? `risk: ${approval.risk}` : null, approval.cwd]),
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
      rawDetail: compactMeta([toolCall.rawInput ? `Input\n${toolCall.rawInput}` : null, toolCall.rawOutput ? `Output\n${toolCall.rawOutput}` : null]).join("\n\n"),
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
        job.exitCode !== undefined && job.exitCode !== null ? `exit ${job.exitCode}` : null,
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
    return "Bash";
  }
  if (kind === "approval") {
    return "Approval";
  }
  if (kind === "tool") {
    return "Tool";
  }
  if (kind === "patch") {
    return "Patch";
  }
  if (kind === "task") {
    return "Task";
  }
  if (kind === "memory") {
    return "Memory";
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
  return compactMeta([item.summary, item.code]).join("\n\n") || "No output captured.";
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
                onClick={() => {
                  void onCopyRuntimeText?.("Command output", commandOutput);
                }}
              >
                Copy output
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
                Refresh
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
                Stop
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
          aria-label={`${kindLabel} ${item.title}${item.status ? ` ${item.status}` : ""}`}
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
          {item.status ? <StatusBadge label={item.status} tone={getStatusTone(item.status)} compact /> : null}
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
                  onClick={() => {
                    void onCopyRuntimeText?.("Trace detail", item.code ?? "");
                  }}
                >
                  Copy detail
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
        aria-label={`${kindLabel} ${item.title}${item.status ? ` ${item.status}` : ""}`}
        aria-expanded={expanded}
        className="runtime-event-summary"
        onClick={() => setExpanded((current) => !current)}
        type="button"
      >
        <span>{kindLabel}</span>
        <strong>{item.title}</strong>
        {item.status ? <StatusBadge label={item.status} tone={getStatusTone(item.status)} /> : null}
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
            aria-label={`Approve ${item.title}`}
            onClick={() => {
              void onApprove?.(item.sourceId ?? "");
            }}
            type="button"
          >
            Approve
          </button>
          <button
            aria-label={`Reject ${item.title}`}
            onClick={() => {
              void onReject?.(item.sourceId ?? "");
            }}
            type="button"
          >
            Reject
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
              Load diff
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
              Refresh
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
              Stop
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
            <div className="runtime-patch-files" aria-label="Patch files">
              {patchPaths.map((path) => (
                <button
                  key={path}
                  type="button"
                  onClick={() => {
                    void onCopyPatchPath(item.sourceId ?? "", path);
                  }}
                >
                  <span>{path}</span>
                  <strong>Copy path</strong>
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
        {isBusy ? "Diff is loading." : "Diff is not available yet. Try loading it again after the runtime finishes writing the patch."}
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
      aria-label={`${message.role} message`}
    >
      <div className="message-bubble-head">
        <span>{getRoleLabel(message.role)}</span>
        {message.toolName ? <em>{message.toolName}</em> : null}
        {message.status ? <em>{message.status}</em> : null}
        {message.streaming && !message.placeholder ? <em>Streaming</em> : null}
        {message.createdAt ? <time>{formatTimestamp(message.createdAt)}</time> : null}
      </div>
      {isThinking ? (
        <div className="thinking-dots" aria-label="Thinking">
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
    <div className="conversation-activity" aria-label="Conversation activity">
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
          <p className="session-kicker">Conversation desk</p>
          <h1 id="session-empty-title">Open or create a session</h1>
          <p>Choose a session from the rail or start a new one to begin chatting here.</p>
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
      eyebrow: "Execution",
      title: "Command lane",
      emptyTitle: "No commands running",
      emptyText: "Shell jobs appear here with stop and refresh controls.",
      items: runtimeItems.filter((item) => item.kind === "command" || item.kind === "tool"),
    },
    {
      id: "patches",
      eyebrow: "Patch",
      title: "Patch queue",
      emptyTitle: "No patch loaded",
      emptyText: "Generated diffs stay here before entering the stream.",
      items: runtimeItems.filter((item) => item.kind === "patch"),
    },
    {
      id: "trace",
      eyebrow: "Diagnostics",
      title: "Important signals",
      emptyTitle: "Diagnostics are quiet",
      emptyText: "Failures, routing decisions, and actionable signals appear here.",
      items: runtimeItems.filter((item) => item.kind === "trace" || item.kind === "approval" || item.kind === "task"),
    },
  ];

  return (
    <main className="session-workspace session-workspace-chat-only" aria-labelledby="session-title">
      <section className="session-workbench-grid">
        <section className="session-conversation-column">
          <header className="session-chat-header">
            <div className="session-chat-title-block">
              <p className="session-kicker">Conversation</p>
              <h1 id="session-title">{session.title}</h1>
              <div className="session-chip-row" aria-label="Session context">
                <StatusBadge label={session.status ?? "active"} tone={getStatusTone(session.status)} />
                {activeTask?.status ? <StatusBadge label={activeTask.status} tone={getStatusTone(activeTask.status)} pulse={isTaskControllable(activeTask.status)} /> : null}
                {taskCount !== undefined ? <span>{taskCount} task{taskCount === 1 ? "" : "s"}</span> : null}
                {composerContext?.model ? <span>{composerContext.model}</span> : null}
                {composerContext?.permissionMode ? <span>approval: {composerContext.permissionMode}</span> : null}
              </div>
            </div>
            <div className="session-chat-actions" aria-label="Session actions">
              <Button
                size="sm"
                variant="secondary"
                loading={taskBusyAction === "refresh"}
                disabled={!activeTask || !onRefreshTask}
                onClick={() => {
                  void onRefreshTask?.();
                }}
              >
                Refresh task
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
                Refresh diagnostics
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
                Stop task
              </Button>
            </div>
          </header>

          <section className="session-console" aria-label="Runtime console">
            <header className="session-console-heading">
              <div>
                <p className="session-kicker">Activity stream</p>
                <h2>Messages and operations</h2>
              </div>
              <span>{activityItems.length} event{activityItems.length === 1 ? "" : "s"}</span>
            </header>
            <div className="message-stream message-stream-chat-only" aria-label="Conversation messages">
              {messagesLoading && activityItems.length === 0 ? (
                <div className="message-stream-loading" aria-label="Loading messages">
                  <div className="message-stream-loading-bar" />
                </div>
              ) : activityItems.length === 0 ? (
                <div className="message-stream-empty">
                  <p className="session-kicker">Quiet thread</p>
                  <h2>No messages yet</h2>
                  <p>Send the first message from the composer below.</p>
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

        <aside className="session-runtime-column" aria-label="Runtime intelligence">
          <section className="session-runtime-dashboard" aria-label="Runtime dashboard">
            <div>
              <p className="session-kicker">Task state</p>
              <strong>{activeTaskStatus}</strong>
              <span>{activeTask?.currentStep ?? activeTask?.goal ?? "Ready for the next instruction"}</span>
            </div>
            <dl>
              <div>
                <dt>Messages</dt>
                <dd>{messages.length}</dd>
              </div>
              <div>
                <dt>Commands</dt>
                <dd>{commandCount}</dd>
              </div>
              <div>
                <dt>Patches</dt>
                <dd>{patchCount}</dd>
              </div>
              <div>
                <dt>Approvals</dt>
                <dd>{pendingApprovals}</dd>
              </div>
              <div>
                <dt>Signals</dt>
                <dd>{diagnosticCount}</dd>
              </div>
            </dl>
          </section>

          {activeTask?.currentStep || activeTask?.goal ? (
            <Panel className="session-task-panel" eyebrow="Runtime Focus" title={activeTask?.currentStep ?? "Ready for the next task"}>
              <div className="session-task-panel-grid">
                <p>{activeTask?.goal ?? session.summary ?? "No active task is running in this session."}</p>
                {composerContext?.cwd ? <code>{composerContext.cwd}</code> : null}
              </div>
            </Panel>
          ) : null}

          {typeof contextUsedTokens === "number" && typeof contextMaxTokens === "number" ? (
            <ContextBudgetBar
              usedTokens={contextUsedTokens}
              reservedTokens={contextBudgetStats?.toolSchemaTokens ?? 0}
              maxTokens={contextMaxTokens}
              label="Session context"
            />
          ) : null}

          <section className="session-runtime-lanes" aria-label="Execution lanes">
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
                          <strong>{getRuntimeKindLabel(item.kind)} event</strong>
                          <span>{item.meta?.slice(0, 2).join(" - ") || "Details are available in the activity stream"}</span>
                        </div>
                        {item.status ? <StatusBadge label={item.status} tone={getStatusTone(item.status)} compact /> : null}
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
