import type {
  AgentEventEnvelope,
  CommandLogRecord,
  TaskRecord,
  TraceEventRecord,
  WorkspaceRef,
} from "@shared";
import type {
  SessionWorkspaceBackgroundJob,
  SessionWorkspaceCollaboration,
  SessionWorkspaceContextPreview,
} from "../ui/workbench/workspaces/session/SessionWorkspace";
import { formatStatusLabel } from "../ui/copy";
import { readTaskContextPreview } from "./eventRecordViews";

export function buildSessionContextPreview({
  events,
  traceEvents,
  workspace,
  activeTaskId,
  activeTask,
}: {
  events: AgentEventEnvelope[];
  traceEvents: TraceEventRecord[];
  workspace: WorkspaceRef | null;
  activeTaskId: string | null;
  activeTask: TaskRecord | null;
}): SessionWorkspaceContextPreview | undefined {
  const liveContexts = events
    .filter((event) => !activeTaskId || event.taskId === activeTaskId)
    .map((event) => ({ ts: event.ts, context: readTaskContextPreview(event.payload) }))
    .filter((entry): entry is { ts: number; context: import("@shared").TaskContextPreviewPayload } => Boolean(entry.context));
  const traceContexts = traceEvents
    .filter((event) => !activeTaskId || event.taskId === activeTaskId)
    .map((event) => ({ ts: event.createdAt, context: readTaskContextPreview(event.payload) }))
    .filter((entry): entry is { ts: number; context: import("@shared").TaskContextPreviewPayload } => Boolean(entry.context));
  const latest = [...liveContexts, ...traceContexts].sort((left, right) => right.ts - left.ts)[0]?.context;
  const projectFocus = workspace?.focus ?? latest?.projectFocus ?? null;
  const projectMemory = workspace?.summary ?? latest?.projectMemory ?? null;

  if (!latest && !projectFocus && !projectMemory) {
    return undefined;
  }

  return {
    projectFocus,
    projectMemory,
    workspaceRoot: latest?.workspaceRoot ?? workspace?.rootPath,
    searchQuery: latest?.searchQuery,
    searchMode: latest?.searchMode,
    toolCount: latest?.toolCount,
    budgetStats: latest?.budgetStats,
    taskFocus: {
      currentStep: activeTask?.currentStep ?? latest?.taskFocus?.currentStep,
      acceptanceCriteriaCount:
        activeTask?.acceptanceCriteria?.length ?? latest?.taskFocus?.acceptanceCriteriaCount,
      outOfScopeCount: activeTask?.outOfScope?.length ?? latest?.taskFocus?.outOfScopeCount,
    },
  };
}

export function getTaskBadgeClass(status?: TaskRecord["status"]): string {
  if (status === "completed") {
    return "ok";
  }
  if (status === "failed" || status === "cancelled") {
    return "error";
  }
  if (status === "running") {
    return "info";
  }
  if (status === "planning" || status === "verifying") {
    return "info";
  }
  if (status === "waiting_approval") {
    return "warn";
  }
  return "neutral";
}

export const TASK_ACTION_PLAN_RE =
  /\b(apply|patch|edit|write|implement|modify|command|shell|run|verify|git|commit|diff|build|fix)\b|\u5e94\u7528|\u8865\u4e01|\u7f16\u8f91|\u5199\u5165|\u5b9e\u73b0|\u4fee\u6539|\u8fd0\u884c|\u6267\u884c|\u9a8c\u8bc1|\u6784\u5efa|\u4fee\u590d|\u63d0\u4ea4/i;
export const TASK_QUESTION_GOAL_RE = new RegExp(
  "[?\\uFF1F]\\s*$|\\u5417|\\u662f\\u5426|\\u662f\\u4e0d\\u662f|\\u80fd\\u4e0d\\u80fd|\\u53ef\\u4ee5|\\u5b8c\\u6210\\u4e86\\u5417|\\u7ed3\\u675f\\u4e86\\u5417|\\u4ec0\\u4e48\\u60c5\\u51b5|\\u4e3a\\u4ec0\\u4e48|\\u600e\\u4e48",
);
export const TASK_GENERIC_ANSWER_STEP_RE =
  /\b(inspect|search|summarize|understand|locate)\b|\u7406\u89e3|\u5b9a\u4f4d|\u67e5\u627e|\u6574\u7406|\u7b54\u590d|\u603b\u7ed3/i;

export function taskHasWorkEvidence(task?: TaskRecord | null) {
  return Boolean(task?.changedFiles?.length || task?.commands?.length || task?.verification?.length);
}

export function taskHasActionPlan(task?: TaskRecord | null) {
  return Boolean(task?.plan?.some((step) => TASK_ACTION_PLAN_RE.test(`${step.id ?? ""} ${step.title ?? ""}`)));
}

export function isGenericAnswerTaskPlan(task?: TaskRecord | null) {
  const plan = task?.plan ?? [];
  return Boolean(
    plan.length > 0 &&
      plan.length <= 3 &&
      plan.every((step) => TASK_GENERIC_ANSWER_STEP_RE.test(`${step.id ?? ""} ${step.title ?? ""}`)),
  );
}

export function shouldPromoteTaskToActive(task: TaskRecord, currentTaskId?: string | null) {
  if (currentTaskId && task.id === currentTaskId) return true;
  if (taskHasWorkEvidence(task) || taskHasActionPlan(task)) return true;
  if ((task.plan?.length ?? 0) > 3) return true;
  if (TASK_QUESTION_GOAL_RE.test(task.goal.trim()) && isGenericAnswerTaskPlan(task)) return false;
  return !TASK_QUESTION_GOAL_RE.test(task.goal.trim());
}

// ── Collaboration builder ──────────────────────────────────────────────

interface CollaborationSourceEvent {
  id: string;
  type: string;
  payload: unknown;
  time: number;
  taskId?: string;
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function readRecordString(record: Record<string, unknown>, key: string): string | undefined {
  const value = record[key];
  return typeof value === "string" && value.trim() ? value.trim() : undefined;
}

function readRecordNumber(record: Record<string, unknown>, key: string): number | undefined {
  const value = record[key];
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

function readRecordBoolean(record: Record<string, unknown>, key: string): boolean | undefined {
  const value = record[key];
  if (typeof value === "boolean") {
    return value;
  }
  if (typeof value === "string") {
    const normalized = value.trim().toLowerCase();
    if (normalized === "true") {
      return true;
    }
    if (normalized === "false") {
      return false;
    }
  }
  return undefined;
}

function readChildRecord(record: Record<string, unknown>, key: string): Record<string, unknown> | null {
  return asRecord(record[key]);
}

function readResultSummary(record: Record<string, unknown>): string | undefined {
  const result = readChildRecord(record, "result");
  return (
    readRecordString(record, "summary") ??
    (result ? readRecordString(result, "summary") : undefined) ??
    readRecordString(record, "description")
  );
}

function readChildTaskAttention(record: Record<string, unknown>): string | undefined {
  const result = readChildRecord(record, "result");
  const errorMessage = readRecordString(record, "errorMessage");
  const error = readChildRecord(record, "error");
  const errorSummary = error ? readRecordString(error, "summary") ?? readRecordString(error, "message") : undefined;
  const explicitTone =
    readRecordString(record, "tone") ??
    readRecordString(record, "health") ??
    (result ? readRecordString(result, "tone") ?? readRecordString(result, "health") : undefined);
  const validationStatus =
    readRecordString(record, "validationStatus") ??
    (result ? readRecordString(result, "validationStatus") ?? readRecordString(result, "verificationStatus") : undefined);
  const blocking =
    readRecordBoolean(record, "blocking") ??
    readRecordBoolean(record, "blocked") ??
    (result ? readRecordBoolean(result, "blocking") ?? readRecordBoolean(result, "blocked") : undefined);
  const risk =
    readRecordString(record, "risk") ??
    readRecordString(record, "riskLevel") ??
    (result ? readRecordString(result, "risk") ?? readRecordString(result, "riskLevel") : undefined);
  const summary = readResultSummary(record);
  const normalizedSignals = [explicitTone, validationStatus, risk].map((value) => value?.toLowerCase());

  if (errorMessage || errorSummary) {
    return errorMessage ?? errorSummary;
  }
  if (blocking) {
    return "Child task reported a blocking condition.";
  }
  if (normalizedSignals.some((value) => value && ["warning", "partial", "blocked", "missing", "failed", "error", "high"].includes(value))) {
    return summary ?? validationStatus ?? risk ?? "Child task needs attention.";
  }
  if (summary && /\b(blocked|blocking|blocker|partial|missing|not present|unable|failed|failure|error|unresolved|incomplete)\b/i.test(summary)) {
    return summary;
  }
  return undefined;
}

function appendOutputTail(current: string, chunk: string, maxLength = 4000): string {
  if (current.endsWith(chunk)) {
    return current;
  }
  const combined = `${current}${chunk}`;
  if (combined.length <= maxLength) {
    return combined;
  }
  return combined.slice(combined.length - maxLength);
}

export function buildSessionCollaboration(
  events: AgentEventEnvelope[],
  traceEvents: TraceEventRecord[],
): SessionWorkspaceCollaboration {
  const workers = new Map<string, NonNullable<SessionWorkspaceCollaboration["workers"]>[number]>();
  const childTasks = new Map<string, NonNullable<SessionWorkspaceCollaboration["childTasks"]>[number]>();
  const results = new Map<string, NonNullable<SessionWorkspaceCollaboration["results"]>[number]>();
  const sources: CollaborationSourceEvent[] = [
    ...events.map((event) => ({
      id: event.eventId,
      type: event.type,
      payload: event.payload,
      time: event.ts,
    })),
    ...traceEvents.map((trace) => ({
      id: trace.id,
      type: trace.type,
      payload: trace.payload,
      time: trace.createdAt,
    })),
  ];

  const rememberWorker = (worker: Record<string, unknown> | null, time: number) => {
    if (!worker) {
      return;
    }
    const id = readRecordString(worker, "id");
    if (!id) {
      return;
    }
    const metadata = readChildRecord(worker, "metadata");
    const health = readChildRecord(worker, "health");
    workers.set(id, {
      id,
      name: readRecordString(worker, "name") ?? id,
      status: readRecordString(worker, "status"),
      mode: readRecordString(worker, "role") ?? (metadata ? readRecordString(metadata, "mode") : undefined),
      healthState:
        readRecordString(worker, "healthState") ?? (health ? readRecordString(health, "state") : undefined),
      healthReason: health ? readRecordString(health, "reason") : undefined,
      heartbeatAgeMs: health ? readRecordNumber(health, "heartbeatAgeMs") : undefined,
      lastHeartbeatAt: health ? readRecordNumber(health, "lastHeartbeatAt") : undefined,
      claimedTaskId: readRecordString(worker, "currentTaskId") ?? undefined,
      summary: Array.isArray(worker.capabilities)
        ? worker.capabilities.filter((item): item is string => typeof item === "string").join(", ")
        : undefined,
      updatedAt: readRecordNumber(worker, "updatedAt") ?? time,
    });
  };

  const rememberTask = (task: Record<string, unknown> | null, time: number) => {
    if (!task) {
      return;
    }
    const id = readRecordString(task, "id");
    if (!id) {
      return;
    }
    childTasks.set(id, {
      id,
      title: readRecordString(task, "title") ?? id,
      status: readRecordString(task, "status"),
      workerId: readRecordString(task, "assignedWorkerId"),
      summary: readResultSummary(task),
      attention: readChildTaskAttention(task),
      updatedAt: readRecordNumber(task, "updatedAt") ?? time,
      createdAt: readRecordNumber(task, "createdAt"),
      completedAt: readRecordNumber(task, "completedAt"),
      durationMs: readRecordNumber(task, "durationMs"),
      agentType: readRecordString(task, "agentType") ?? (readChildRecord(task, "metadata") ? readRecordString(readChildRecord(task, "metadata")!, "agentType") : undefined),
      artifactCount: readRecordNumber(task, "artifactCount"),
      errorMessage: readRecordString(task, "errorMessage"),
    });

    const status = readRecordString(task, "status");
    const summary = readResultSummary(task);
    if (summary && (status === "completed" || status === "failed")) {
      results.set(`${id}:result`, {
        id: `${id}:result`,
        taskId: id,
        title: readRecordString(task, "title") ?? id,
        status,
        summary,
        updatedAt: readRecordNumber(task, "completedAt") ?? readRecordNumber(task, "updatedAt") ?? time,
      });
    }
  };

  for (const event of sources) {
    const payload = asRecord(event.payload);
    if (!payload) {
      continue;
    }

    if (event.type.startsWith("collab.task.")) {
      rememberTask(readChildRecord(payload, "task"), event.time);
      rememberWorker(readChildRecord(payload, "worker"), event.time);
    }

    if (event.type.startsWith("collab.worker.")) {
      rememberWorker(readChildRecord(payload, "worker"), event.time);
    }

    if (event.type === "collab.message.sent") {
      const message = readChildRecord(payload, "message");
      const taskId = message ? readRecordString(message, "taskId") : undefined;
      const kind = message ? readRecordString(message, "kind") : undefined;
      if (message && taskId && kind === "result") {
        results.set(message.id ? String(message.id) : `${taskId}:message`, {
          id: readRecordString(message, "id") ?? `${taskId}:message`,
          taskId,
          status: kind,
          summary: readRecordString(message, "body"),
          updatedAt: readRecordNumber(message, "createdAt") ?? event.time,
        });
      }
    }

    if (event.type === "tool.completed" && readRecordString(payload, "toolName") === "task") {
      const result = readChildRecord(payload, "result");
      if (result) {
        rememberTask(readChildRecord(result, "task"), event.time);
        rememberWorker(readChildRecord(result, "worker"), event.time);
        const childTaskId = readRecordString(result, "childTaskId");
        const summary = readRecordString(result, "summary") ?? readResultSummary(result);
        if (childTaskId && summary) {
          results.set(`${childTaskId}:tool`, {
            id: `${childTaskId}:tool`,
            taskId: childTaskId,
            status: readRecordString(result, "status"),
            summary,
            updatedAt: event.time,
          });
        }
      }
    }
  }

  const workerList = [...workers.values()];
  const taskList = [...childTasks.values()].map((task) => ({
    ...task,
    workerName: task.workerId ? workers.get(task.workerId)?.name ?? task.workerName : task.workerName,
  }));
  const resultList = [...results.values()];
  const healthSummary = workerList.reduce(
    (summary, worker) => {
      summary.total += 1;
      if (worker.healthState === "healthy" || worker.healthState === "stale" || worker.healthState === "offline") {
        summary[worker.healthState] += 1;
      }
      return summary;
    },
    { healthy: 0, stale: 0, offline: 0, total: 0 },
  );

  return {
    workers: workerList.sort((left, right) => (right.updatedAt ?? 0) - (left.updatedAt ?? 0)),
    childTasks: taskList.sort((left, right) => (right.updatedAt ?? 0) - (left.updatedAt ?? 0)),
    results: resultList.sort((left, right) => (right.updatedAt ?? 0) - (left.updatedAt ?? 0)),
    healthSummary,
  };
}

export function buildSessionBackgroundJobs(
  events: AgentEventEnvelope[],
  traceEvents: TraceEventRecord[],
): SessionWorkspaceBackgroundJob[] {
  const jobs = new Map<string, SessionWorkspaceBackgroundJob>();
  const sources: CollaborationSourceEvent[] = [
    ...traceEvents.map((trace) => ({
      id: trace.id,
      type: trace.type,
      payload: trace.payload,
      time: trace.createdAt,
      taskId: trace.taskId,
    })),
    ...events.map((event) => ({
      id: event.eventId,
      type: event.type,
      payload: event.payload,
      time: event.ts,
      taskId: event.taskId,
    })),
  ].sort((left, right) => left.time - right.time);

  const rememberLifecycle = (type: string, payload: Record<string, unknown>, time: number) => {
    const id = readRecordString(payload, "commandId");
    if (!id) {
      return;
    }
    const current = jobs.get(id);
    const status =
      readRecordString(payload, "status") ??
      (type === "command.started"
        ? "running"
        : type === "command.completed"
          ? "completed"
          : type === "command.cancelled"
            ? "killed"
            : "failed");
    const next: SessionWorkspaceBackgroundJob = {
      id,
      command: readRecordString(payload, "command") ?? current?.command ?? id,
      status,
      cwd: readRecordString(payload, "cwd") ?? current?.cwd,
      shell: readRecordString(payload, "shell") ?? current?.shell,
      startedAt: current?.startedAt ?? (type === "command.started" ? time : undefined),
      finishedAt: type === "command.started" ? current?.finishedAt : time,
      durationMs: readRecordNumber(payload, "durationMs") ?? current?.durationMs,
      exitCode:
        readRecordNumber(payload, "exitCode") ??
        (payload.exitCode === null ? null : current?.exitCode),
      stdout: current?.stdout,
      stderr: current?.stderr,
      stdoutPath: readRecordString(payload, "stdoutPath") ?? current?.stdoutPath,
      stderrPath: readRecordString(payload, "stderrPath") ?? current?.stderrPath,
      isBackground: readRecordBoolean(payload, "background") ?? current?.isBackground,
      summary:
        status === "running"
          ? "命令仍在运行。"
          : status === "completed"
            ? `命令已完成${typeof readRecordNumber(payload, "exitCode") === "number" ? `，退出码 ${readRecordNumber(payload, "exitCode")}` : "。"}`
            : `命令状态：${formatStatusLabel(status)}${typeof readRecordNumber(payload, "exitCode") === "number" ? `，退出码 ${readRecordNumber(payload, "exitCode")}` : "。"}`,
    };
    jobs.set(id, next);
  };

  const rememberOutput = (payload: Record<string, unknown>) => {
    const id = readRecordString(payload, "commandId");
    const stream = readRecordString(payload, "stream");
    const chunk = (function readRecordRawString(record: Record<string, unknown>, key: string): string | undefined {
      const value = record[key];
      return typeof value === "string" && value.length > 0 ? value : undefined;
    })(payload, "chunk");
    if (!id || !stream || !chunk) {
      return;
    }
    const current = jobs.get(id) ?? {
      id,
      command: id,
      status: "running",
    };
    jobs.set(id, {
      ...current,
      stdout: stream === "stdout" ? appendOutputTail(current.stdout ?? "", chunk) : current.stdout,
      stderr: stream === "stderr" ? appendOutputTail(current.stderr ?? "", chunk) : current.stderr,
    });
  };

  for (const source of sources) {
    const payload = asRecord(source.payload);
    if (!payload) {
      continue;
    }
    if (source.type === "command.output") {
      rememberOutput(payload);
      continue;
    }
    if (
      source.type === "command.started" ||
      source.type === "command.completed" ||
      source.type === "command.failed" ||
      source.type === "command.cancelled"
    ) {
      rememberLifecycle(source.type, payload, source.time);
    }
  }

  return [...jobs.values()].sort(
    (left, right) =>
      (right.finishedAt ?? right.startedAt ?? 0) - (left.finishedAt ?? left.startedAt ?? 0),
  );
}

export function commandLogToSessionBackgroundJob(log: CommandLogRecord): SessionWorkspaceBackgroundJob {
  return {
    id: log.id,
    command: log.command,
    status: log.status,
    cwd: log.cwd,
    shell: log.shell,
    startedAt: log.startedAt,
    finishedAt: log.finishedAt,
    durationMs: log.durationMs,
    exitCode: log.exitCode ?? null,
    stdout: log.stdout,
    stderr: log.stderr,
    stdoutPath: log.stdoutPath,
    stderrPath: log.stderrPath,
    isBackground: false,
    summary:
      log.status === "running"
        ? "命令仍在运行。"
        : log.status === "completed"
          ? `命令已完成${typeof log.exitCode === "number" ? `，退出码 ${log.exitCode}` : "。"}`
          : `命令状态：${formatStatusLabel(log.status)}${typeof log.exitCode === "number" ? `，退出码 ${log.exitCode}` : "。"}`
  };
}

export function mergeSessionBackgroundJobs(
  eventJobs: SessionWorkspaceBackgroundJob[],
  commandLogs: CommandLogRecord[],
): SessionWorkspaceBackgroundJob[] {
  const jobsById = new Map(eventJobs.map((job) => [job.id, job]));

  for (const log of commandLogs) {
    const current = jobsById.get(log.id);
    jobsById.set(log.id, {
      ...current,
      ...commandLogToSessionBackgroundJob(log),
      stdout: log.stdout ?? current?.stdout,
      stderr: log.stderr ?? current?.stderr,
      stdoutPath: log.stdoutPath ?? current?.stdoutPath,
      stderrPath: log.stderrPath ?? current?.stderrPath,
    });
  }

  return [...jobsById.values()].sort(
    (left, right) =>
      (right.finishedAt ?? right.startedAt ?? 0) - (left.finishedAt ?? left.startedAt ?? 0),
  );
}
