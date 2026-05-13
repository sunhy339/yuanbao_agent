import type {
  AgentEventEnvelope,
  PatchRecord,
  TaskContextPreviewPayload,
  TaskRecord,
  TaskUpdatedPayload,
} from "@shared";

export interface ApprovalCardView {
  approvalId: string;
  taskId: string;
  kind: string;
  patchId?: string;
  patchSummary?: string;
  filesChanged?: number;
  command: string;
  cwd: string;
  shell: string;
  timeoutMs: number;
  risk: string;
  requestJson: string;
  requestSummary: string;
  status: "pending" | "approved" | "rejected";
  requestedAt: number;
  updatedAt: number;
  resolvedAt?: number;
  requestedEventId?: string;
  resolvedEventId?: string;
}

export interface PatchCardView {
  patchId: string;
  taskId: string;
  summary: string;
  filesChanged: number;
  status: PatchRecord["status"];
  requestedAt: number;
  updatedAt: number;
  diffText?: string;
  approvalId?: string;
  approvalStatus?: ApprovalCardView["status"];
  approvalResolvedAt?: number;
}

export interface ToolTimelineItem {
  id: string;
  taskId: string;
  toolCallId: string;
  toolName: string;
  status: "started" | "completed" | "failed";
  argsSummary: string;
  resultSummary: string;
  errorSummary?: string;
  argsRaw?: string;
  resultRaw?: string;
  startedAt: number;
  updatedAt: number;
  finishedAt?: number;
  durationMs?: number;
  eventCount: number;
}

export interface QueuedPromptSubmission {
  id: string;
  content: string;
  attachments: string[];
}

export interface CommandJobTimelineItem {
  id: string;
  taskId: string;
  commandId: string;
  command: string;
  status: string;
  cwd?: string;
  shell?: string;
  summary?: string;
  startedAt: number;
  updatedAt: number;
  finishedAt?: number;
  durationMs?: number;
  exitCode?: number | null;
  stdout: string;
  stderr: string;
  stdoutPath?: string;
  stderrPath?: string;
  isBackground?: boolean;
  eventCount: number;
}

export function sortByUpdatedAtDesc<T extends { updatedAt: number }>(items: T[]): T[] {
  return [...items].sort((left, right) => right.updatedAt - left.updatedAt);
}

export function stringifyRequestJson(request: Record<string, unknown>): string {
  try {
    return JSON.stringify(request, null, 2);
  } catch {
    return "{}";
  }
}

export function readRequestStringList(request: Record<string, unknown>, keys: string[]): string[] {
  for (const key of keys) {
    const value = request[key];
    if (Array.isArray(value)) {
      return value
        .map((item) => (typeof item === "string" ? item.trim() : ""))
        .filter(Boolean);
    }
  }
  return [];
}

export function readRequestOptionalNumber(request: Record<string, unknown>, keys: string[]): number | undefined {
  for (const key of keys) {
    const value = request[key];
    if (typeof value === "number" && Number.isFinite(value)) {
      return value;
    }
    if (typeof value === "string" && value.trim()) {
      const parsed = Number(value);
      if (Number.isFinite(parsed)) {
        return parsed;
      }
    }
  }
  return undefined;
}

export function getApprovalBadgeClass(status: ApprovalCardView["status"]): "ok" | "warn" | "error" {
  if (status === "approved") {
    return "ok";
  }
  if (status === "rejected") {
    return "error";
  }
  return "warn";
}

export function upsertRecord<T extends { id: string; updatedAt: number }>(items: T[], record: T): T[] {
  const next = new Map(items.map((item) => [item.id, item]));
  next.set(record.id, record);
  return sortByUpdatedAtDesc(Array.from(next.values()));
}

export function coerceTaskStatus(
  event: AgentEventEnvelope,
  fallback: TaskRecord["status"],
): TaskRecord["status"] {
  if (event.type === "task.started") {
    return "running";
  }
  if (event.type === "task.waiting_approval") {
    return "waiting_approval";
  }
  if (event.type === "task.completed") {
    return "completed";
  }
  if (event.type === "task.failed") {
    return "failed";
  }
  if (event.type === "task.cancelled") {
    return "cancelled";
  }
  return fallback;
}

export function applyEventToTask(current: TaskRecord | null, event: AgentEventEnvelope): TaskRecord | null {
  if (!current || current.id !== event.taskId || !event.type.startsWith("task.")) {
    return current;
  }

  const payload = (event.payload ?? {}) as Partial<TaskUpdatedPayload> & {
    resultSummary?: string;
    errorCode?: string;
    detail?: string;
  };

  return {
    ...current,
    status: payload.status ?? coerceTaskStatus(event, current.status),
    plan: payload.plan ?? current.plan,
    acceptanceCriteria: payload.acceptanceCriteria ?? current.acceptanceCriteria,
    outOfScope: payload.outOfScope ?? current.outOfScope,
    currentStep: payload.currentStep ?? current.currentStep,
    changedFiles: payload.changedFiles ?? current.changedFiles,
    commands: payload.commands ?? current.commands,
    verification: payload.verification ?? current.verification,
    summary: payload.summary ?? current.summary,
    resultSummary: payload.detail ?? payload.resultSummary ?? payload.summary ?? current.resultSummary,
    errorCode: payload.errorCode ?? current.errorCode,
    updatedAt: event.ts,
  };
}

export function taskRecordFromEvent(event: AgentEventEnvelope): TaskRecord | null {
  if (!event.taskId || !event.sessionId || !event.type.startsWith("task.")) {
    return null;
  }

  const payload = (event.payload ?? {}) as Partial<TaskUpdatedPayload> & {
    goal?: string;
    title?: string;
    resultSummary?: string;
    detail?: string;
    errorCode?: string;
  };
  const status = payload.status ?? coerceTaskStatus(event, "running");
  return {
    id: event.taskId,
    sessionId: event.sessionId,
    type: "chat",
    status,
    goal: payload.goal ?? payload.title ?? "",
    acceptanceCriteria: payload.acceptanceCriteria,
    outOfScope: payload.outOfScope,
    currentStep: payload.currentStep,
    plan: payload.plan,
    changedFiles: payload.changedFiles,
    commands: payload.commands,
    verification: payload.verification,
    summary: payload.summary,
    resultSummary: payload.detail ?? payload.resultSummary ?? payload.summary,
    errorCode: payload.errorCode,
    createdAt: event.ts,
    updatedAt: event.ts,
  };
}

export function readTaskContextPreview(value: unknown): TaskContextPreviewPayload | null {
  if (!value || typeof value !== "object") {
    return null;
  }
  const payload = value as { context?: unknown };
  if (!payload.context || typeof payload.context !== "object") {
    return null;
  }
  return payload.context as TaskContextPreviewPayload;
}
