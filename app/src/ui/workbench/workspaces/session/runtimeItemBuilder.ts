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
  normalizeComparableCommand,
  isVerificationCommand,
  isBackgroundProbeCommand,
  isSuccessfulRuntimeStatus,
} from "./utils";

import { formatStatusLabel } from "../../../copy";

function commandGroupKey(command?: string) {
  const normalized = normalizeComparableCommand(command);
  return normalized ? `command:${normalized}` : undefined;
}

function summarizeChangedFiles(activeTask: SessionWorkspaceActiveTask) {
  const changedFiles = activeTask.changedFiles ?? [];
  if (!changedFiles.length) {
    return "";
  }
  const topFiles = changedFiles.slice(0, 4).map((file) => file.path);
  const suffix = changedFiles.length > topFiles.length ? ` +${changedFiles.length - topFiles.length}` : "";
  return `${changedFiles.length} 个文件：${topFiles.join(", ")}${suffix}`;
}

function summarizeCommandForTitle(command?: string) {
  const normalized = normalizeComparableCommand(command);
  if (!normalized) {
    return command ?? "command";
  }
  if (normalized.startsWith("python -m pytest")) {
    return "pytest";
  }
  if (normalized.startsWith("python -m py_compile")) {
    return "py_compile";
  }
  if (normalized.startsWith("node --check")) {
    return "node --check";
  }
  if (normalized === "git status") {
    return "git status";
  }
  return command ?? normalized;
}

function summarizeCommandAction(command?: string) {
  const normalized = normalizeComparableCommand(command);
  if (!normalized) {
    return command ?? "Ran command";
  }
  if (normalized.startsWith("python -m pytest")) {
    return "Ran tests";
  }
  if (normalized.startsWith("python -m py_compile")) {
    return "Checked Python syntax";
  }
  if (normalized.startsWith("node --check")) {
    return "Checked script syntax";
  }
  if (normalized === "git status") {
    return "Checked workspace status";
  }
  if (normalized.startsWith("git diff")) {
    return "Viewed code diff";
  }
  if (normalized.startsWith("get-content")) {
    return "Read file contents";
  }
  return command ?? normalized;
}

function classifyTaskCommandVisibility(command?: string, status?: string) {
  const normalizedStatus = status?.toLowerCase();
  const normalizedCommand = normalizeComparableCommand(command);
  if (normalizedStatus && ["running", "started", "pending", "queued", "failed", "error", "cancelled"].includes(normalizedStatus)) {
    return "chat" as const;
  }
  if (
    normalizedStatus &&
    ["completed", "passed", "succeeded"].includes(normalizedStatus) &&
    normalizedCommand &&
    isVerificationCommand(normalizedCommand)
  ) {
    return "chat" as const;
  }
  if (normalizedStatus && isSuccessfulRuntimeStatus(normalizedStatus) && isBackgroundProbeCommand(command)) {
    return "trace" as const;
  }
  return "panel" as const;
}

function classifyToolVisibility(toolCall: SessionWorkspaceToolCall): RuntimeTimelineItem["visibility"] {
  const normalizedStatus = toolCall.status?.toLowerCase();
  const isInFlight = Boolean(
    normalizedStatus &&
      ["running", "started", "planning", "verifying", "pending", "queued", "waiting_approval"].includes(normalizedStatus),
  );
  const needsAttention = Boolean(
    normalizedStatus && ["failed", "error", "cancelled", "rejected"].includes(normalizedStatus),
  );

  if (toolCall.toolName === "run_command" || toolCall.toolName === "apply_patch" || toolCall.toolName === "write_file") {
    return isInFlight || needsAttention ? "chat" : "panel";
  }
  if (toolCall.toolName === "list_dir" || toolCall.toolName === "git_status" || toolCall.toolName === "read_file") {
    return isInFlight || needsAttention ? "chat" : "trace";
  }
  return isInFlight || needsAttention ? "chat" : "panel";
}

function shouldHideToolFromRuntimePanel(toolCall: SessionWorkspaceToolCall) {
  return false;
}

function classifyBackgroundJobVisibility(job: { command: string; status: string; summary?: string }) {
  const normalizedStatus = job.status.toLowerCase();
  if (["running", "started", "pending", "queued", "failed", "error", "cancelled"].includes(normalizedStatus)) {
    return "chat" as const;
  }
  const normalizedCommand = normalizeComparableCommand(job.command);
  if (
    normalizedStatus === "completed" &&
    normalizedCommand &&
    isVerificationCommand(normalizedCommand)
  ) {
    return "chat" as const;
  }
  if (isSuccessfulRuntimeStatus(normalizedStatus) && isBackgroundProbeCommand(job.command)) {
    return "trace" as const;
  }
  return "panel" as const;
}

function markSupersededRuntimeItems(items: RuntimeTimelineItem[]) {
  const latestSuccessfulByGroup = new Map<string, { index: number; time?: number }>();

  items.forEach((item, index) => {
    if (!item.groupKey) {
      return;
    }
    const normalizedStatus = item.status?.toLowerCase();
    if (isSuccessfulRuntimeStatus(normalizedStatus)) {
      const existing = latestSuccessfulByGroup.get(item.groupKey);
      const candidate = { index, time: item.time };
      if (!existing) {
        latestSuccessfulByGroup.set(item.groupKey, candidate);
      } else {
        const existingTime = existing.time ?? -1;
        const candidateTime = candidate.time ?? -1;
        if (candidateTime > existingTime || (candidateTime === existingTime && candidate.index > existing.index)) {
          latestSuccessfulByGroup.set(item.groupKey, candidate);
        }
      }
    }
  });

  items.forEach((item, index) => {
    if (!item.groupKey) {
      return;
    }
    const normalizedStatus = item.status?.toLowerCase();
    if (!normalizedStatus || !["failed", "error", "cancelled", "rejected"].includes(normalizedStatus)) {
      return;
    }
    const recovery = latestSuccessfulByGroup.get(item.groupKey);
    const recoveryTime = recovery?.time ?? -1;
    const itemTime = item.time ?? -1;
    const resolvedByLaterSuccess =
      Boolean(recovery) &&
      (recoveryTime > itemTime || (recoveryTime === itemTime && (recovery?.index ?? -1) > index));
    if (resolvedByLaterSuccess) {
      item.superseded = true;
      item.visibility = "trace";
    }
  });
}

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
  const resultSummary = summarizeRuntimeOutput(
    toolCall.resultSummary || toolCall.output || toolCall.stdout || toolCall.stderr || toolCall.rawOutput,
  );

  if (toolCall.toolName === "run_command") {
    return {
      kind: "command",
      title: command ?? toolCall.argsPreview ?? "命令",
      summary: compactMeta([summarizeCommandAction(command), resultSummary]).join(" · "),
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
  const taskTime = activeTask.updatedAt ?? activeTask.createdAt;

  if (changedFiles.length) {
    items.push({
      id: `task-files:${activeTask.id}`,
      kind: "task",
      title: "变更文件",
      status: "recorded",
      summary: summarizeChangedFiles(activeTask),
      meta: compactMeta([`${changedFiles.length} 个文件`]),
      code: changedFiles.map(formatTaskFileChange).join("\n"),
      visibility: "chat",
      taskId: activeTask.id,
      time: taskTime,
    });
  }

  if (commands.length) {
    commands.slice(-4).forEach((command, index) => {
      items.push({
        id: `task-command:${activeTask.id}:${command.id ?? index}`,
        kind: "command",
        title: summarizeCommandForTitle(command.command),
        status: command.status,
        summary: compactMeta([summarizeCommandAction(command.command), command.summary]).join(" · "),
        meta: compactMeta([
          command.cwd,
          command.shell,
          command.exitCode !== undefined && command.exitCode !== null ? `退出码 ${command.exitCode}` : null,
          formatDuration(command.durationMs ?? undefined),
        ]),
        code: command.command,
        visibility: classifyTaskCommandVisibility(command.command, command.status),
        taskId: activeTask.id,
        time: taskTime,
        groupKey: commandGroupKey(command.command),
      });
    });
  }

  if (verification.length) {
    verification.slice(-3).forEach((record, index) => {
      const title = summarizeCommandForTitle(record.command ?? record.id);
      items.push({
        id: `task-verification:${activeTask.id}:${record.id ?? index}`,
        kind: "command",
        title,
        status: record.status,
        summary: compactMeta([summarizeCommandAction(record.command ?? record.id), record.summary]).join(" · "),
        meta: compactMeta([
          record.exitCode !== undefined && record.exitCode !== null ? `退出码 ${record.exitCode}` : null,
          formatDuration(record.durationMs ?? undefined),
          "验证",
        ]),
        code: record.command ?? record.id,
        visibility: classifyTaskCommandVisibility(record.command ?? record.id, record.status),
        taskId: activeTask.id,
        time: taskTime,
        groupKey: commandGroupKey(record.command ?? record.id) ?? `verification:${title.toLowerCase()}`,
      });
    });
  }

  markSupersededRuntimeItems(items);
  return items;
}

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
  const haystack = `${trace.type} ${trace.source ?? ""} ${trace.title ?? ""} ${trace.summary ?? ""}`.toLowerCase();
  if (hiddenTraceTypes.has(type) || hiddenTracePrefixes.some((prefix) => type.startsWith(prefix))) {
    return false;
  }
  if (
    haystack.includes("provider.failure.recovery_decision") ||
    haystack.includes("provider.preflight") ||
    haystack.includes("agent.decision") ||
    haystack.includes("tool_recovery")
  ) {
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

  items.push(...buildActiveTaskRuntimeItems(activeTask));

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
      visibility: "chat",
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
      visibility: trace.visibility ?? "panel",
      taskId: trace.taskId,
      agentType: trace.agentType,
    });
  });

  approvals.forEach((approval) => {
    const normalizedStatus = approval.status.toLowerCase();
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
      visibility:
        normalizedStatus === "pending" || normalizedStatus === "waiting_approval" || normalizedStatus === "queued"
          ? "chat"
          : "panel",
    });
  });

  compactRepeatedReadFileCalls(toolCalls).forEach((toolCall) => {
    if (shouldHideToolFromRuntimePanel(toolCall)) {
      return;
    }
    const presentation = buildToolRuntimePresentation(toolCall);
    items.push({
      id: `tool:${toolCall.id}`,
      kind: presentation.kind,
      title: presentation.title,
      status: toolCall.status,
      summary: presentation.summary,
      meta: presentation.meta,
      code: presentation.code,
      rawDetail: compactMeta([
        toolCall.rawOutput ? `输出\n${toolCall.rawOutput}` : null,
        toolCall.stdout ? `标准输出\n${compactText(toolCall.stdout, 1200)}` : null,
        toolCall.stderr ? `标准错误\n${compactText(toolCall.stderr, 1200)}` : null,
      ]).join("\n\n"),
      time: toolCall.time,
      durationMs: toolCall.durationMs,
      visibility: classifyToolVisibility(toolCall),
      groupKey: presentation.kind === "command" ? commandGroupKey(presentation.title) : undefined,
    });
  });

  backgroundJobs.forEach((job) => {
    const rawDetail = compactMeta([
      job.stdout ? `输出\n${compactText(job.stdout, 1200)}` : null,
      job.stderr ? `错误\n${compactText(job.stderr, 1200)}` : null,
    ]).join("\n\n");
    const pathDetail = compactMeta([
      job.stdoutPath ? `stdout\n${job.stdoutPath}` : null,
      job.stderrPath ? `stderr\n${job.stderrPath}` : null,
    ]).join("\n\n");
    items.push({
      id: `command:${job.id}`,
      kind: "command",
      title: summarizeCommandForTitle(job.command),
      status: job.status,
      summary: compactMeta([
        summarizeCommandAction(job.command),
        job.summary || summarizeRuntimeOutput(job.stdout || job.stderr),
      ]).join(" · "),
      meta: compactMeta([
        job.cwd,
        job.shell,
        job.exitCode !== undefined && job.exitCode !== null ? `退出码 ${job.exitCode}` : null,
        formatDuration(job.durationMs),
        job.stdoutPath ?? null,
        job.stderrPath ?? null,
      ]),
      rawDetail: rawDetail || undefined,
      code: job.command,
      time: job.finishedAt ?? job.startedAt,
      durationMs: job.durationMs,
      visibility: classifyBackgroundJobVisibility(job),
      groupKey: commandGroupKey(job.command),
    });
  });

  markSupersededRuntimeItems(items);
  return items;
}
