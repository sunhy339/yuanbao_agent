import { memo, useMemo, useState, type ReactNode } from "react";
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
  onRefreshCommandJob?(commandId: string): void | Promise<void>;
  onStopCommandJob?(commandId: string): void | Promise<void>;
  onRefreshTask?(): void | Promise<void>;
  onStopTask?(taskId: string): void | Promise<void>;
  onRefreshTrace?(): void | Promise<void>;
  taskBusyAction?: "refresh" | "stop" | null;
  busyId?: string | null;
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

  if (activeTask.currentStep || activeTask.goal || acceptanceCriteria.length || outOfScope.length) {
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
        activeTask.goal ? `Goal: ${activeTask.goal}` : null,
        acceptanceCriteria.length ? `Acceptance: ${acceptanceCriteria.join("; ")}` : null,
        outOfScope.length ? `Out of scope: ${outOfScope.join("; ")}` : null,
      ]).join("\n"),
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

function buildSessionMemoryRuntimeItems(session?: SessionWorkspaceSession | null): RuntimeTimelineItem[] {
  const summary = typeof session?.summary === "string" ? session.summary.trim() : "";
  if (!summary) {
    return [];
  }

  const memoryLines = summary
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
  const memoryEntries = memoryLines.filter((line) => line.startsWith("- ")).length;

  return [
    {
      id: `session-memory:${session?.id ?? "active"}`,
      kind: "memory",
      title: "Session memory",
      status: "recorded",
      summary: memoryEntries
        ? `${memoryEntries} remembered item${memoryEntries === 1 ? "" : "s"}`
        : "Session memory is available.",
      meta: compactMeta([memoryEntries ? `${memoryEntries} items` : null]),
      code: summary,
    },
  ];
}

function buildContextPreviewRuntimeItems(contextPreview?: SessionWorkspaceContextPreview): RuntimeTimelineItem[] {
  if (!contextPreview) {
    return [];
  }

  const projectFocus = stripSectionLabel(compactText(contextPreview.projectFocus, 900), "Project focus");
  const projectMemory = stripSectionLabel(compactText(contextPreview.projectMemory, 1200), "Project memory");
  const budgetStats = contextPreview.budgetStats ?? undefined;
  const droppedSections = budgetStats?.droppedSections ?? [];
  const trimmedSections = budgetStats?.trimmedSections ?? [];
  const budgetLabel = formatTokenBudget(budgetStats);
  const taskFocus = contextPreview.taskFocus;
  const hasTaskFocus = Boolean(
    taskFocus?.currentStep || taskFocus?.acceptanceCriteriaCount || taskFocus?.outOfScopeCount,
  );

  if (!projectFocus && !projectMemory && !budgetLabel && !hasTaskFocus) {
    return [];
  }

  const meta = compactMeta([
    projectFocus ? "Focus active" : null,
    projectMemory ? "Project memory" : null,
    budgetLabel,
    contextPreview.toolCount !== undefined && contextPreview.toolCount !== null
      ? `${contextPreview.toolCount} tools`
      : null,
    trimmedSections.length ? `${trimmedSections.length} trimmed` : null,
    droppedSections.length ? `${droppedSections.length} dropped` : null,
  ]);

  const lines = compactMeta([
    projectFocus ? `Project focus:\n${projectFocus}` : null,
    projectMemory ? `Project memory:\n${projectMemory}` : null,
    contextPreview.workspaceRoot ? `Workspace: ${contextPreview.workspaceRoot}` : null,
    contextPreview.searchMode || contextPreview.searchQuery
      ? `Search: ${compactMeta([contextPreview.searchMode ?? undefined, contextPreview.searchQuery ?? undefined]).join(" - ")}`
      : null,
    hasTaskFocus
      ? `Task focus: ${compactMeta([
          taskFocus?.currentStep ?? undefined,
          taskFocus?.acceptanceCriteriaCount !== undefined && taskFocus?.acceptanceCriteriaCount !== null
            ? `${taskFocus.acceptanceCriteriaCount} acceptance`
            : null,
          taskFocus?.outOfScopeCount !== undefined && taskFocus?.outOfScopeCount !== null
            ? `${taskFocus.outOfScopeCount} out of scope`
            : null,
        ]).join(" - ")}`
      : null,
    budgetStats
      ? `Budget: ${compactMeta([
          budgetLabel,
          budgetStats.messageTokens !== undefined && budgetStats.messageTokens !== null
            ? `${budgetStats.messageTokens} message`
            : null,
          budgetStats.toolSchemaTokens !== undefined && budgetStats.toolSchemaTokens !== null
            ? `${budgetStats.toolSchemaTokens} tool schema`
            : null,
        ]).join(" - ")}`
      : null,
    trimmedSections.length ? `Trimmed: ${trimmedSections.join(", ")}` : null,
    droppedSections.length ? `Dropped: ${droppedSections.join(", ")}` : null,
  ]);

  return [
    {
      id: "context-preview",
      kind: "memory",
      title: "Context preview",
      status: "ready",
      summary: budgetLabel ? `Using ${budgetLabel}.` : "Context is available.",
      meta,
      code: lines.join("\n\n"),
    },
  ];
}

function buildRuntimeItems({
  session,
  activeTask,
  contextPreview,
  approvals = [],
  toolCalls = [],
  backgroundJobs = [],
}: Pick<
  SessionWorkspaceProps,
  "session" | "activeTask" | "contextPreview" | "approvals" | "patches" | "traces" | "toolCalls" | "backgroundJobs"
>): RuntimeTimelineItem[] {
  const items: RuntimeTimelineItem[] = [
    ...buildContextPreviewRuntimeItems(contextPreview),
    ...buildSessionMemoryRuntimeItems(session),
    ...buildActiveTaskRuntimeItems(activeTask),
  ];

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
}: {
  item: RuntimeTimelineItem;
  onApprove?(approvalId: string): void | Promise<void>;
  onReject?(approvalId: string): void | Promise<void>;
}) {
  const [expanded, setExpanded] = useState(false);
  const kindLabel = getRuntimeKindLabel(item.kind);
  const canResolveApproval = item.kind === "approval" && item.status === "pending" && item.sourceId;

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
        {item.status ? <em>{item.status}</em> : null}
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
          {item.rawDetail ? (
            <details className="runtime-raw-detail">
              <summary>查看原始数据</summary>
              <pre>{item.rawDetail}</pre>
            </details>
          ) : null}
        </div>
      ) : null}
    </article>
  );
});

const MessageBubble = memo(function MessageBubble({ message }: { message: SessionWorkspaceMessage }) {
  return (
    <article
      className="message-bubble"
      data-activity-kind="message"
      data-role={message.role}
      aria-label={`${message.role} message`}
    >
      <div className="message-bubble-head">
        <span>{getRoleLabel(message.role)}</span>
        {message.toolName ? <em>{message.toolName}</em> : null}
        {message.status ? <em>{message.status}</em> : null}
        {message.streaming ? <em>Streaming</em> : null}
        {message.createdAt ? <time>{formatTimestamp(message.createdAt)}</time> : null}
      </div>
      {message.role === "assistant" ? <MarkdownContent content={message.content} /> : <p>{message.content}</p>}
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
}: {
  items: ConversationActivityItem[];
  onApprove?(approvalId: string): void | Promise<void>;
  onReject?(approvalId: string): void | Promise<void>;
}) {
  return (
    <div className="conversation-activity" aria-label="Conversation activity">
      {items.map((item) =>
        item.kind === "message" ? (
          <MessageBubble message={item.message} key={item.id} />
        ) : (
          <RuntimeEventCard item={item.runtime} key={item.id} onApprove={onApprove} onReject={onReject} />
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

  return (
    <main className="session-workspace session-workspace-chat-only" aria-labelledby="session-title">
      <header className="session-chat-header">
        <p className="session-kicker">Conversation</p>
        <h1 id="session-title">{session.title}</h1>
      </header>

      <section className="message-stream message-stream-chat-only" aria-label="Conversation messages">
        {activityItems.length === 0 ? (
          <div className="message-stream-empty">
            <p className="session-kicker">Quiet thread</p>
            <h2>No messages yet</h2>
            <p>Send the first message from the composer below.</p>
          </div>
        ) : (
          <ConversationActivity items={activityItems} onApprove={onApprove} onReject={onReject} />
        )}
      </section>
    </main>
  );
}

export default SessionWorkspace;
