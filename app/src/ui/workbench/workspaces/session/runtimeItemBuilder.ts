import type {
  SessionWorkspaceProps,
  SessionWorkspaceToolCall,
  SessionWorkspaceActiveTask,
  SessionWorkspaceSession,
  SessionWorkspaceContextPreview,
  RuntimeTimelineItem,
  ToolRuntimePresentation,
  DiffLine,
  SessionWorkspaceTrace,
} from "./types";

import {
  compactMeta,
  compactList,
  compactText,
  parseUnifiedDiff,
  formatDuration,
  summarizeRuntimeOutput,
  getRuntimeKindLabel,
  parsePatchPath,
  parsePatchFileSummaries,
  buildCommandOutput,
  formatTaskFileChange,
  formatTaskCommand,
  formatTaskVerification,
  getProcessStatusLabel,
  getStatusTone,
  parseRuntimeJsonRecord,
  readRuntimeString,
  normalizeRuntimeComparableString,
  compactRepeatedReadFileCalls,
  getRepeatedReadFileKey,
} from "./utils";

import { formatStatusLabel } from "../../../copy";

// ── Tool runtime presentation ──────────────────────────────────────

export function buildToolRuntimePresentation(toolCall: SessionWorkspaceToolCall): ToolRuntimePresentation {
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

// ── Aggregate status ───────────────────────────────────────────────

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

// ── Active task runtime items ──────────────────────────────────────

export function buildActiveTaskRuntimeItems(activeTask?: SessionWorkspaceActiveTask | null): RuntimeTimelineItem[] {
  if (!activeTask) {
    return [];
  }

  const items: RuntimeTimelineItem[] = [];
  const changedFiles = activeTask.changedFiles ?? [];
  const commands = activeTask.commands ?? [];
  const verification = activeTask.verification ?? [];

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

// ── Session memory runtime items ───────────────────────────────────

export function buildSessionMemoryRuntimeItems(_session?: SessionWorkspaceSession | null): RuntimeTimelineItem[] {
  // Session memory is internal context; hidden from user-facing UI.
  return [];
}

// ── Context preview runtime items ──────────────────────────────────

export function buildContextPreviewRuntimeItems(_contextPreview?: SessionWorkspaceContextPreview): RuntimeTimelineItem[] {
  // Context preview is internal system state; hidden from user-facing UI.
  return [];
}

// ── Trace visibility helpers ───────────────────────────────────────

export const hiddenTraceTypes = new Set([
  "assistant.token",
  "provider.request",
  "provider.response",
  "task.started",
  "task.updated",
  "task.orphaned",
]);

export const hiddenTracePrefixes = ["tool.", "command.", "patch.", "approval."];

export const visibleTraceTypes = new Set([
  "routing.decision",
  "runtime.error",
  "provider.error",
  "mcp.error",
  "context.trimmed",
  "task.failed",
  "task.cancelled",
]);

export function isRawJsonLike(value?: string) {
  const trimmed = value?.trim();
  return Boolean(trimmed && ((trimmed.startsWith("{") && trimmed.endsWith("}")) || (trimmed.startsWith("[") && trimmed.endsWith("]"))));
}

export function isUserVisibleTrace(trace: SessionWorkspaceTrace) {
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

export function formatTraceTitle(trace: SessionWorkspaceTrace) {
  if (trace.title && trace.title !== trace.type) {
    return trace.title;
  }
  return trace.type
    .split(".")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

export function formatTraceSummary(trace: SessionWorkspaceTrace) {
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

export function buildTraceDetail(trace: SessionWorkspaceTrace) {
  return compactMeta([
    trace.stderr ? `错误\n${compactText(trace.stderr, 800)}` : null,
    trace.stdout && !isRawJsonLike(trace.stdout) ? `输出\n${compactText(trace.stdout, 800)}` : null,
    trace.detail && !isRawJsonLike(trace.detail) ? `详情\n${compactText(trace.detail, 800)}` : null,
  ]).join("\n\n");
}

// ── Main runtime items builder ─────────────────────────────────────

export function buildRuntimeItems({
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
  const items: RuntimeTimelineItem[] = [];

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
        trace.visibility,
        trace.agentType,
      ]),
      code: outputDetail || undefined,
      time: trace.time,
      durationMs: trace.durationMs,
      visibility: trace.visibility,
      taskId: trace.taskId,
      agentType: trace.agentType,
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
      riskLevel: approval.risk,
      code: approval.command || approval.parametersPreview,
      rawDetail: approval.fullInput,
      completionEvidence: approval.completionEvidence,
      time: approval.requestedAt,
    });
  });

  compactRepeatedReadFileCalls(toolCalls).forEach((toolCall) => {
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
      durationMs: toolCall.durationMs,
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
      durationMs: job.durationMs,
    });
  });

  return items;
}
