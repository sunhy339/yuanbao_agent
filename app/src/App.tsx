import { useEffect, useMemo, useState } from "react";
import type {
  ApprovalRequestedPayload,
  ApprovalResolvedPayload,
  AgentEventEnvelope,
  AppConfig,
  AssistantTokenPayload,
  CommandOutputPayload,
  PatchRecord,
  PatchProposedPayload,
  PlanStep,
  SessionRecord,
  TaskRecord,
  TaskUpdatedPayload,
  WorkspaceRef,
} from "@shared";
import { RuntimeClient, type HostStatus, type RuntimeConfig } from "./lib/runtimeClient";
import {
  DEFAULT_PROMPT,
  DEFAULT_SESSION_TITLE,
  DEFAULT_WORKSPACE_PATH,
} from "./state/mockData";

const runtimeClient = new RuntimeClient();
const DEFAULT_SEARCH_GLOB_TEXT = "";

function formatTimestamp(timestamp?: number): string {
  if (!timestamp) {
    return "not recorded";
  }

  return new Date(timestamp).toLocaleString("en-US", {
    hour12: false,
  });
}

function normalizeRuntimeConfig(config: AppConfig | RuntimeConfig): RuntimeConfig {
  return {
    ...config,
    search: config.search ?? {
      glob: [],
      ignore: config.workspace.ignore,
    },
  };
}

function parsePatternText(value: string): string[] {
  return value
    .split(/[\n,]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function serializePatternList(values: string[]): string {
  return values.join("\n");
}

function readRequestText(request: Record<string, unknown>, key: string, fallback: string): string {
  const value = request[key];
  if (typeof value === "string" && value.trim()) {
    return value.trim();
  }
  if (typeof value === "number" && Number.isFinite(value)) {
    return String(value);
  }
  return fallback;
}

function readRequestNumber(request: Record<string, unknown>, key: string, fallback: number): number {
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
  return fallback;
}

function readRequestPatchId(request: Record<string, unknown>): string | undefined {
  const value = request.patch_id ?? request.patchId;
  if (typeof value === "string" && value.trim()) {
    return value.trim();
  }
  return undefined;
}

function readEventText(payload: unknown, key: string): string | undefined {
  if (!payload || typeof payload !== "object") {
    return undefined;
  }

  const value = (payload as Record<string, unknown>)[key];
  return typeof value === "string" && value.trim() ? value.trim() : undefined;
}

function sortByUpdatedAtDesc<T extends { updatedAt: number }>(items: T[]): T[] {
  return [...items].sort((left, right) => right.updatedAt - left.updatedAt);
}

interface ApprovalCardView {
  approvalId: string;
  taskId: string;
  kind: string;
  patchId?: string;
  command: string;
  cwd: string;
  shell: string;
  timeoutMs: number;
  status: "pending" | "approved" | "rejected";
  requestedAt: number;
  updatedAt: number;
  resolvedAt?: number;
}

interface PatchCardView {
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

function upsertRecord<T extends { id: string; updatedAt: number }>(items: T[], record: T): T[] {
  const next = new Map(items.map((item) => [item.id, item]));
  next.set(record.id, record);
  return sortByUpdatedAtDesc(Array.from(next.values()));
}

function coerceTaskStatus(
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
  return fallback;
}

function applyEventToTask(current: TaskRecord | null, event: AgentEventEnvelope): TaskRecord | null {
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
    resultSummary: payload.detail ?? payload.resultSummary ?? current.resultSummary,
    errorCode: payload.errorCode ?? current.errorCode,
    updatedAt: event.ts,
  };
}

function summarizeEvent(event: AgentEventEnvelope): string {
  if (event.type === "assistant.token") {
    return ((event.payload as AssistantTokenPayload).delta ?? "").trim() || "Model is streaming output.";
  }

  if (event.type === "command.output") {
    const payload = event.payload as CommandOutputPayload;
    return `${payload.stream}: ${payload.chunk.trim()}`.trim();
  }

  if (event.type === "approval.requested") {
    const payload = event.payload as ApprovalRequestedPayload;
    const request = payload.request as Record<string, unknown>;
    if (payload.kind === "apply_patch") {
      const patchId = readRequestPatchId(request);
      return patchId ? `Patch approval requested for ${patchId}` : "Patch approval requested";
    }
    return `Approval requested for ${readRequestText(request, "command", "command")} in ${readRequestText(request, "cwd", readRequestText(request, "workspaceRoot", "."))}`;
  }

  if (event.type === "approval.resolved") {
    const payload = event.payload as ApprovalResolvedPayload;
    return `Approval ${payload.decision}`;
  }

  if (event.type === "patch.proposed") {
    const payload = event.payload as PatchProposedPayload;
    return `${payload.summary} | ${payload.filesChanged} file(s) changed`;
  }

  if (event.type.startsWith("task.")) {
    const payload = event.payload as Partial<TaskUpdatedPayload>;
    return payload.detail ?? `Task status changed to ${payload.status ?? "unknown"}`;
  }

  if (
    event.type === "tool.started" ||
    event.type === "tool.completed" ||
    event.type === "tool.failed"
  ) {
    const payload = event.payload as { toolName?: string };
    return payload.toolName ? `Tool event: ${payload.toolName}` : "Tool lifecycle event";
  }

  try {
    return JSON.stringify(event.payload);
  } catch {
    return "Unable to serialize event payload.";
  }
}

function describeMode(hostStatus: HostStatus | null): string {
  if (!hostStatus) {
    return "Detecting runtime";
  }

  return hostStatus.runtimeRunning ? "Connected to local runtime" : "Browser / Mock mode";
}

export function App() {
  const [hostStatus, setHostStatus] = useState<HostStatus | null>(null);
  const [config, setConfig] = useState<RuntimeConfig | null>(null);
  const [searchGlob, setSearchGlob] = useState(DEFAULT_SEARCH_GLOB_TEXT);
  const [searchIgnoreText, setSearchIgnoreText] = useState("");
  const [workspacePath, setWorkspacePath] = useState(DEFAULT_WORKSPACE_PATH);
  const [sessionTitle, setSessionTitle] = useState(DEFAULT_SESSION_TITLE);
  const [workspace, setWorkspace] = useState<WorkspaceRef | null>(null);
  const [sessions, setSessions] = useState<Array<SessionRecord>>([]);
  const [session, setSession] = useState<SessionRecord | null>(null);
  const [taskHistory, setTaskHistory] = useState<Array<TaskRecord>>([]);
  const [task, setTask] = useState<TaskRecord | null>(null);
  const [events, setEvents] = useState<Array<AgentEventEnvelope>>([]);
  const [patchCacheById, setPatchCacheById] = useState<Record<string, PatchRecord>>({});
  const [prompt, setPrompt] = useState(DEFAULT_PROMPT);
  const [assistantOutput, setAssistantOutput] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [workspaceBusy, setWorkspaceBusy] = useState(false);
  const [sessionBusy, setSessionBusy] = useState(false);
  const [messageBusy, setMessageBusy] = useState(false);
  const [refreshBusy, setRefreshBusy] = useState(false);
  const [sessionListBusy, setSessionListBusy] = useState(false);
  const [searchConfigBusy, setSearchConfigBusy] = useState(false);
  const [approvalBusyId, setApprovalBusyId] = useState<string | null>(null);
  const [patchBusyId, setPatchBusyId] = useState<string | null>(null);
  const [activeTaskId, setActiveTaskId] = useState<string | null>(null);

  useEffect(() => {
    let disposed = false;

    Promise.all([
      runtimeClient.getHostStatus(),
      runtimeClient.getConfig(),
      runtimeClient.listSessions(),
      runtimeClient.listTasks(),
    ])
      .then(([nextHostStatus, nextConfig, nextSessions, nextTasks]) => {
        if (disposed) {
          return;
        }

        const normalizedConfig = normalizeRuntimeConfig(nextConfig.config);
        setHostStatus(nextHostStatus);
        setConfig(normalizedConfig);
        setSearchGlob(serializePatternList(normalizedConfig.search.glob));
        setSearchIgnoreText(serializePatternList(normalizedConfig.search.ignore));
        setSessions(nextSessions.sessions);
        setTaskHistory(nextTasks.tasks);
        const initialSession = nextSessions.sessions[0] ?? null;
        const initialTask = initialSession
          ? sortByUpdatedAtDesc(nextTasks.tasks.filter((item) => item.sessionId === initialSession.id))[0] ?? null
          : sortByUpdatedAtDesc(nextTasks.tasks)[0] ?? null;
        setSession(initialSession);
        setTask(initialTask);
        setActiveTaskId(initialTask?.id ?? null);

        if (nextConfig.config.workspace.rootPath) {
          setWorkspacePath(nextConfig.config.workspace.rootPath);
        }
      })
      .catch((reason) => {
        if (!disposed) {
          setError(reason instanceof Error ? reason.message : String(reason));
        }
      })
      .finally(() => {
        if (!disposed) {
          setLoading(false);
        }
      });

    return () => {
      disposed = true;
    };
  }, []);

  useEffect(() => {
    let active = true;
    let dispose: (() => void) | undefined;

    runtimeClient
      .subscribeEvents((event) => {
        if (!active) {
          return;
        }

        setEvents((current) => [...current, event].slice(-80));
        setTask((current) => applyEventToTask(current, event));
        setTaskHistory((current) => {
          const existing = current.find((item) => item.id === event.taskId);
          if (!existing) {
            return current;
          }

          const updated = applyEventToTask(existing, event);
          return updated ? upsertRecord(current, updated) : current;
        });

        if (event.type.startsWith("task.") && session?.id === event.sessionId) {
          setSession((current) =>
            current && current.id === event.sessionId
              ? { ...current, updatedAt: event.ts }
              : current,
          );
          setSessions((current) =>
            current.map((item) => (item.id === event.sessionId ? { ...item, updatedAt: event.ts } : item)),
          );
        }

        if (event.type === "assistant.token") {
          const payload = event.payload as AssistantTokenPayload;
          setAssistantOutput((current) => `${current}${payload.delta}`);
        }
      })
      .then((unlisten) => {
        dispose = unlisten;
      })
      .catch((reason) => {
        if (active) {
          setError(reason instanceof Error ? reason.message : String(reason));
        }
      });

    return () => {
      active = false;
      dispose?.();
    };
  }, []);

  const eventItems = useMemo(
    () =>
      [...events]
        .reverse()
        .map((event) => ({
          id: event.eventId,
          type: event.type,
          taskId: event.taskId,
          time: formatTimestamp(event.ts),
          summary: summarizeEvent(event),
          raw: JSON.stringify(event.payload, null, 2),
        })),
    [events],
  );

  const approvalCards = useMemo<ApprovalCardView[]>(() => {
    const cards = new Map<string, ApprovalCardView>();

    for (const event of events) {
      if (event.type === "approval.requested") {
        const payload = event.payload as ApprovalRequestedPayload;
        const request = payload.request as Record<string, unknown>;
        cards.set(payload.approvalId, {
          approvalId: payload.approvalId,
          taskId: payload.taskId,
          kind: payload.kind,
          patchId: payload.kind === "apply_patch" ? readRequestPatchId(request) : undefined,
          command: readRequestText(request, "command", "command"),
          cwd: readRequestText(request, "cwd", readRequestText(request, "workspaceRoot", ".")),
          shell: readRequestText(request, "shell", "system default"),
          timeoutMs: readRequestNumber(request, "timeoutMs", 0),
          status: "pending",
          requestedAt: event.ts,
          updatedAt: event.ts,
        });
      }

      if (event.type === "approval.resolved") {
        const payload = event.payload as ApprovalResolvedPayload;
        const current = cards.get(payload.approvalId);
        if (current) {
          cards.set(payload.approvalId, {
            ...current,
            status: payload.decision,
            resolvedAt: event.ts,
            updatedAt: event.ts,
          });
          continue;
        }

        cards.set(payload.approvalId, {
          approvalId: payload.approvalId,
          taskId: payload.taskId,
          kind: "run_command",
          patchId: undefined,
          command: "unknown",
          cwd: ".",
          shell: "system default",
          timeoutMs: 0,
          status: payload.decision,
          requestedAt: event.ts,
          updatedAt: event.ts,
          resolvedAt: event.ts,
        });
      }
    }

    return sortByUpdatedAtDesc(Array.from(cards.values()));
  }, [events]);

  const approvalByPatchId = useMemo(() => {
    const cards = new Map<string, ApprovalCardView>();
    for (const approval of approvalCards) {
      if (approval.patchId && !cards.has(approval.patchId)) {
        cards.set(approval.patchId, approval);
      }
    }
    return cards;
  }, [approvalCards]);

  const patchCards = useMemo<PatchCardView[]>(() => {
    const cards = new Map<string, PatchCardView>();

    for (const event of events) {
      if (event.type === "patch.proposed") {
        const payload = event.payload as PatchProposedPayload;
        const patchId = readEventText(payload, "patchId");
        if (!patchId) {
          continue;
        }
        const diffText = readEventText(payload, "diffText");
        cards.set(patchId, {
          patchId,
          taskId: event.taskId,
          summary: payload.summary,
          filesChanged: payload.filesChanged,
          status: "proposed",
          requestedAt: event.ts,
          updatedAt: event.ts,
          diffText,
        });
      }
    }

    for (const [patchId, patch] of Object.entries(patchCacheById)) {
      const current = cards.get(patchId);
      cards.set(patchId, {
        patchId,
        taskId: patch.taskId,
        summary: patch.summary,
        filesChanged: patch.filesChanged,
        status: patch.status,
        requestedAt: current?.requestedAt ?? patch.createdAt,
        updatedAt: Math.max(current?.updatedAt ?? patch.updatedAt, patch.updatedAt),
        diffText: patch.diffText,
      });
    }

    for (const card of cards.values()) {
      const approval = approvalByPatchId.get(card.patchId);
      if (approval) {
        card.approvalId = approval.approvalId;
        card.approvalStatus = approval.status;
        card.approvalResolvedAt = approval.resolvedAt;
        card.updatedAt = Math.max(card.updatedAt, approval.updatedAt);
        if (approval.status === "approved") {
          card.status = "approved";
        } else if (approval.status === "rejected") {
          card.status = "rejected";
        }
      }
    }

    return sortByUpdatedAtDesc(Array.from(cards.values()));
  }, [approvalByPatchId, events, patchCacheById]);

  const commandOutputByTaskId = useMemo(() => {
    const outputs = new Map<string, string[]>();

    for (const event of events) {
      if (event.type !== "command.output") {
        continue;
      }

      const payload = event.payload as CommandOutputPayload;
      const chunk = payload.chunk.trim();
      const summary = chunk ? `${payload.stream}: ${chunk}` : `${payload.stream}: output event`;
      const next = outputs.get(event.taskId) ?? [];
      outputs.set(event.taskId, [...next, summary].slice(-3));
    }

    return outputs;
  }, [events]);

  const planSteps = useMemo<Array<PlanStep>>(() => task?.plan ?? [], [task]);

  const visibleTaskHistory = useMemo(() => {
    const scopedTasks = session ? taskHistory.filter((item) => item.sessionId === session.id) : taskHistory;
    return sortByUpdatedAtDesc(scopedTasks);
  }, [taskHistory, session]);

  async function ensureWorkspace(): Promise<WorkspaceRef> {
    if (workspace) {
      return workspace;
    }

    const result = await runtimeClient.openWorkspace(workspacePath.trim());
    setWorkspace(result.workspace);
    setConfig((current) =>
      current
        ? {
            ...current,
            workspace: {
              ...current.workspace,
              rootPath: result.workspace.rootPath,
              writableRoots: [result.workspace.rootPath],
            },
          }
        : current,
    );
    return result.workspace;
  }

  function selectSession(nextSession: SessionRecord | null) {
    setSession(nextSession);
    setActiveTaskId(null);
    setTask(null);
    setEvents([]);
    setPatchCacheById({});
    setPatchBusyId(null);
    setAssistantOutput("");
    setApprovalBusyId(null);

    if (!nextSession) {
      return;
    }

    const nextTask = sortByUpdatedAtDesc(
      taskHistory.filter((item) => item.sessionId === nextSession.id),
    )[0];
    setTask(nextTask ?? null);
    setActiveTaskId(nextTask?.id ?? null);
  }

  function selectTask(taskId: string) {
    const nextTask = taskHistory.find((item) => item.id === taskId);
    if (!nextTask) {
      return;
    }

    setTask(nextTask);
    setActiveTaskId(nextTask.id);
  }

  async function refreshSessionHistory(preferredSessionId?: string) {
    setSessionListBusy(true);
    setError(null);

    try {
      const result = await runtimeClient.listSessions();
      const taskResult = await runtimeClient.listTasks();
      const nextSessions = result.sessions;
      const nextTasks = taskResult.tasks;
      setSessions(nextSessions);
      setTaskHistory(nextTasks);

      const preferredSession =
        nextSessions.find((item) => item.id === preferredSessionId) ??
        (session ? nextSessions.find((item) => item.id === session.id) : undefined) ??
        nextSessions[0] ??
        null;

      setSession(preferredSession);

      if (!preferredSession) {
        setTask(null);
        setActiveTaskId(null);
        return;
      }

      const nextTask = sortByUpdatedAtDesc(
        nextTasks.filter((item) => item.sessionId === preferredSession.id),
      )[0];
      setTask(nextTask ?? null);
      setActiveTaskId(nextTask?.id ?? null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setSessionListBusy(false);
    }
  }

  async function persistSearchConfig(): Promise<RuntimeConfig | null> {
    if (!config) {
      return null;
    }

    const nextConfig = normalizeRuntimeConfig({
      ...config,
      search: {
        ...config.search,
        glob: parsePatternText(searchGlob),
        ignore: parsePatternText(searchIgnoreText),
      },
    });

    const result = await runtimeClient.updateConfig(nextConfig);
    const normalized = normalizeRuntimeConfig(result.config);
    setConfig(normalized);
    setSearchGlob(serializePatternList(normalized.search.glob));
    setSearchIgnoreText(serializePatternList(normalized.search.ignore));
    return normalized;
  }

  async function handleOpenWorkspace() {
    if (!workspacePath.trim()) {
      setError("Enter a workspace path before connecting.");
      return;
    }

    setWorkspaceBusy(true);
    setError(null);

    try {
      const result = await runtimeClient.openWorkspace(workspacePath.trim());
      setWorkspace(result.workspace);
      setSession(null);
      setTask(null);
      setTaskHistory([]);
      setSessions([]);
      setActiveTaskId(null);
      setApprovalBusyId(null);
      setPatchCacheById({});
      setPatchBusyId(null);
      setEvents([]);
      setAssistantOutput("");
      await refreshSessionHistory();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setWorkspaceBusy(false);
    }
  }

  async function handleCreateSession() {
    setSessionBusy(true);
    setError(null);

    try {
      const nextWorkspace = await ensureWorkspace();
      const result = await runtimeClient.createSession({
        workspaceId: nextWorkspace.id,
        title: sessionTitle.trim() || DEFAULT_SESSION_TITLE,
      });

      setSessions((current) => upsertRecord(current, result.session));
      selectSession(result.session);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setSessionBusy(false);
    }
  }

  async function handleSaveSearchConfig() {
    setSearchConfigBusy(true);
    setError(null);

    try {
      await persistSearchConfig();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setSearchConfigBusy(false);
    }
  }

  async function handleSendMessage() {
    if (!prompt.trim()) {
      setError("Enter a task description before sending.");
      return;
    }

    setMessageBusy(true);
    setError(null);

    try {
      await persistSearchConfig();
      const activeSession = session ?? (await ensureSessionForSend());
      setEvents([]);
      setPatchCacheById({});
      setPatchBusyId(null);
      setAssistantOutput("");
      setApprovalBusyId(null);

      const result = await runtimeClient.sendMessage({
        sessionId: activeSession.id,
        content: prompt.trim(),
        attachments: [],
      });

      setTaskHistory((current) => upsertRecord(current, result.task));
      setTask(result.task);
      setActiveTaskId(result.task.id);
      const touchedSession = { ...activeSession, updatedAt: result.task.updatedAt };
      setSessions((current) => upsertRecord(current, touchedSession));
      setSession(touchedSession);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setMessageBusy(false);
    }
  }

  async function ensureSessionForSend(): Promise<SessionRecord> {
    const nextWorkspace = await ensureWorkspace();
    const result = await runtimeClient.createSession({
      workspaceId: nextWorkspace.id,
      title: sessionTitle.trim() || DEFAULT_SESSION_TITLE,
    });
    setSessions((current) => upsertRecord(current, result.session));
    setSession(result.session);
    return result.session;
  }

  async function handleRefreshTask() {
    if (!task) {
      return;
    }

    setRefreshBusy(true);
    setError(null);

    try {
      const result = await runtimeClient.getTask(task.id);
      setTask(result.task);
      setActiveTaskId(result.task.id);
      setTaskHistory((current) => upsertRecord(current, result.task));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setRefreshBusy(false);
    }
  }

  async function handleLoadPatchDiff(patchId: string) {
    setPatchBusyId(patchId);
    setError(null);

    try {
      const result = await runtimeClient.diffGet({ patchId });
      setPatchCacheById((current) => ({
        ...current,
        [patchId]: {
          ...result.patch,
          diffText: result.diffText || result.patch.diffText,
        },
      }));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setPatchBusyId((current) => (current === patchId ? null : current));
    }
  }

  async function handleApprovalSubmit(approvalId: string, decision: "approved" | "rejected") {
    setApprovalBusyId(approvalId);
    setError(null);

    try {
      await runtimeClient.approvalSubmit({
        approvalId,
        decision,
      });
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setApprovalBusyId((current) => (current === approvalId ? null : current));
    }
  }

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <p className="eyebrow">Local AI Coding Agent</p>
          <h1>Sprint 1 Workspace</h1>
          <p className="muted">
            The UI uses bridge-driven data first and falls back to a controlled browser mock path when Tauri is
            unavailable.
          </p>
        </div>

        <section className="panel">
          <div className="section-header">
            <h2>Bridge</h2>
            <span className={`badge ${hostStatus?.runtimeRunning ? "ok" : "warn"}`}>
              {describeMode(hostStatus)}
            </span>
          </div>
          <dl className="meta">
            <div>
              <dt>Transport</dt>
              <dd>{hostStatus?.runtimeTransport ?? (loading ? "loading..." : "unavailable")}</dd>
            </div>
            <div>
              <dt>Event Channel</dt>
              <dd>{hostStatus?.eventChannel ?? "agent://event"}</dd>
            </div>
            <div>
              <dt>Model</dt>
              <dd>{config?.provider.defaultModel ?? "loading..."}</dd>
            </div>
            <div>
              <dt>Approval</dt>
              <dd>{config?.policy.approvalMode ?? "loading..."}</dd>
            </div>
          </dl>
        </section>

        <section className="panel">
          <h2>Workspace</h2>
          <label className="field">
            <span>Root Path</span>
            <input
              value={workspacePath}
              onChange={(event) => setWorkspacePath(event.target.value)}
              placeholder={DEFAULT_WORKSPACE_PATH}
            />
          </label>
          <button type="button" onClick={handleOpenWorkspace} disabled={workspaceBusy || loading}>
            {workspaceBusy ? "Connecting..." : "Connect Workspace"}
          </button>
          <dl className="meta compact">
            <div>
              <dt>Name</dt>
              <dd>{workspace?.name ?? "not connected"}</dd>
            </div>
            <div>
              <dt>Root</dt>
              <dd className="break">{workspace?.rootPath ?? "waiting for initialization"}</dd>
            </div>
          </dl>
        </section>

        <section className="panel">
          <div className="section-header">
            <h2>Search Config</h2>
            <button type="button" onClick={handleSaveSearchConfig} disabled={searchConfigBusy || loading || !config}>
              {searchConfigBusy ? "Saving..." : "Save"}
            </button>
          </div>
          <label className="field">
            <span>Glob</span>
            <input value={searchGlob} onChange={(event) => setSearchGlob(event.target.value)} placeholder="app/src/**/*.tsx" />
          </label>
          <label className="field">
            <span>Ignore</span>
            <textarea
              className="short"
              value={searchIgnoreText}
              onChange={(event) => setSearchIgnoreText(event.target.value)}
              placeholder={".git\nnode_modules\ndist"}
            />
          </label>
          <p className="help-text">One glob or ignore pattern per line. Blank glob means search every non-ignored file.</p>
        </section>

        <section className="panel">
          <div className="section-header">
            <h2>Session History</h2>
            <button type="button" onClick={() => refreshSessionHistory()} disabled={sessionListBusy || loading}>
              {sessionListBusy ? "Refreshing..." : "Refresh"}
            </button>
          </div>
          <ul className="history-list">
            {sessions.length > 0 ? (
              sessions.map((item) => (
                <li key={item.id}>
                  <button
                    type="button"
                    className={`history-card ${session?.id === item.id ? "active" : ""}`}
                    onClick={() => selectSession(item)}
                  >
                    <strong>{item.title}</strong>
                    <span>{item.status}</span>
                    <span>{formatTimestamp(item.updatedAt)}</span>
                    {item.summary ? <span className="muted">{item.summary}</span> : null}
                  </button>
                </li>
              ))
            ) : (
              <li className="empty-state">
                <strong>No sessions yet</strong>
                <span>Create one or send a message to populate history.</span>
              </li>
            )}
          </ul>
        </section>

        <section className="panel">
          <h2>Session</h2>
          <label className="field">
            <span>Title</span>
            <input
              value={sessionTitle}
              onChange={(event) => setSessionTitle(event.target.value)}
              placeholder={DEFAULT_SESSION_TITLE}
            />
          </label>
          <button type="button" onClick={handleCreateSession} disabled={sessionBusy || loading}>
            {sessionBusy ? "Creating..." : "Create Session"}
          </button>
          <dl className="meta compact">
            <div>
              <dt>ID</dt>
              <dd>{session?.id ?? "not created"}</dd>
            </div>
            <div>
              <dt>Status</dt>
              <dd>{session?.status ?? "idle"}</dd>
            </div>
            <div>
              <dt>Updated</dt>
              <dd>{formatTimestamp(session?.updatedAt)}</dd>
            </div>
          </dl>
        </section>

        <section className="panel">
          <div className="section-header">
            <h2>Current Task</h2>
            <button type="button" onClick={handleRefreshTask} disabled={!task || refreshBusy}>
              {refreshBusy ? "Refreshing..." : "Refresh"}
            </button>
          </div>
          <dl className="meta compact">
            <div>
              <dt>ID</dt>
              <dd>{task?.id ?? "no task yet"}</dd>
            </div>
            <div>
              <dt>Status</dt>
              <dd>{task?.status ?? "idle"}</dd>
            </div>
            <div>
              <dt>Error</dt>
              <dd>{task?.errorCode ?? "none"}</dd>
            </div>
            <div>
              <dt>Updated</dt>
              <dd>{formatTimestamp(task?.updatedAt)}</dd>
            </div>
          </dl>
        </section>

        <section className="panel">
          <div className="section-header">
            <h2>Task History</h2>
            <span className="muted">{visibleTaskHistory.length} item(s)</span>
          </div>
          <ul className="history-list">
            {visibleTaskHistory.length > 0 ? (
              visibleTaskHistory.map((item) => (
                <li key={item.id}>
                  <button
                    type="button"
                    className={`history-card ${activeTaskId === item.id ? "active" : ""}`}
                    onClick={() => selectTask(item.id)}
                  >
                    <strong>{item.goal}</strong>
                    <span>{item.status}</span>
                    <span>{formatTimestamp(item.updatedAt)}</span>
                  </button>
                </li>
              ))
            ) : (
              <li className="empty-state">
                <strong>No tasks yet</strong>
                <span>Send a message to start recording task history.</span>
              </li>
            )}
          </ul>
        </section>
      </aside>

      <main className="main">
        <section className="panel hero">
          <p className="eyebrow">Current Goal</p>
          <h2>{task?.goal ?? "Connect a workspace and create a session first."}</h2>
          <p className="muted">
            On startup the app reads host status, config and session history. After you send a message, task, plan and
            event stream panels all update from bridge-driven data.
          </p>
          {task?.resultSummary ? <p className="success-banner">{task.resultSummary}</p> : null}
          {error ? <p className="error-banner">{error}</p> : null}
        </section>

        <section className="grid">
          <section className="panel">
            <div className="section-header">
              <h2>Plan</h2>
              <span className="muted">{planSteps.length} step(s)</span>
            </div>
            <ul className="steps">
              {planSteps.length > 0 ? (
                planSteps.map((step) => (
                  <li key={step.id} data-status={step.status}>
                    <strong>{step.title}</strong>
                    <span>{step.detail ?? "No detail yet."}</span>
                  </li>
                ))
              ) : (
                <li>
                  <strong>No plan yet</strong>
                  <span>Send a message and the runtime will populate the first task plan.</span>
                </li>
              )}
            </ul>
          </section>

          <section className="panel">
            <div className="section-header">
              <h2>Assistant Output</h2>
              <span className="muted">{assistantOutput ? "streaming history" : "waiting"}</span>
            </div>
            <pre className="stream">{assistantOutput || "Waiting for assistant.token events..."}</pre>
          </section>
        </section>

        <section className="panel">
          <div className="section-header">
            <h2>Composer</h2>
            <span className="muted">
              {hostStatus?.runtimeRunning ? "Connected to desktop runtime" : "Using browser/mock fallback"}
            </span>
          </div>
          <textarea value={prompt} onChange={(event) => setPrompt(event.target.value)} placeholder={DEFAULT_PROMPT} />
          <div className="actions">
            <button type="button" onClick={handleSendMessage} disabled={messageBusy || loading}>
              {messageBusy ? "Sending..." : "Send Message"}
            </button>
            <span className="muted">
              Session: {session?.title ?? "auto-create on send"} | Workspace: {workspace?.name ?? "auto-connect on send"}
            </span>
          </div>
        </section>

        <section className="panel">
          <div className="section-header">
            <h2>Event Stream</h2>
            <span className="muted">{eventItems.length} event(s)</span>
          </div>
          <section className="patch-stack">
            <div className="section-header approval-header">
              <h3>Patches</h3>
              <span className="muted">{patchCards.length} item(s)</span>
            </div>
            {patchCards.length > 0 ? (
              <ul className="patch-list">
                {patchCards.map((item) => {
                  const approvalPending = item.approvalStatus === "pending" && item.approvalId;
                  const hasLoadedDiff = Boolean(patchCacheById[item.patchId]?.diffText || item.diffText);
                  const diffText = patchCacheById[item.patchId]?.diffText ?? item.diffText;
                  const badgeClass =
                    item.status === "approved" || item.status === "applied"
                      ? "ok"
                      : item.status === "rejected" || item.status === "failed"
                        ? "error"
                        : "warn";

                  return (
                    <li key={item.patchId} className="patch-card" data-status={item.status}>
                      <div className="section-header approval-topline">
                        <div>
                          <strong>{item.summary}</strong>
                          <span className="muted">patchId: {item.patchId}</span>
                        </div>
                        <span className={`badge ${badgeClass}`}>{item.status}</span>
                      </div>
                      <dl className="meta compact patch-meta">
                        <div>
                          <dt>files changed</dt>
                          <dd>{item.filesChanged}</dd>
                        </div>
                        <div>
                          <dt>task</dt>
                          <dd className="break">{item.taskId}</dd>
                        </div>
                        <div>
                          <dt>requested</dt>
                          <dd>{formatTimestamp(item.requestedAt)}</dd>
                        </div>
                        <div>
                          <dt>updated</dt>
                          <dd>{formatTimestamp(item.updatedAt)}</dd>
                        </div>
                      </dl>
                      <div className="patch-actions">
                        <button
                          type="button"
                          onClick={() => handleLoadPatchDiff(item.patchId)}
                          disabled={patchBusyId === item.patchId || loading}
                        >
                          {patchBusyId === item.patchId
                            ? "Loading..."
                            : hasLoadedDiff
                              ? "Reload diff"
                              : "Load diff"}
                        </button>
                        {approvalPending ? (
                          <>
                            <button
                              type="button"
                              onClick={() => handleApprovalSubmit(item.approvalId as string, "approved")}
                              disabled={approvalBusyId === item.approvalId || loading}
                            >
                              Approve patch
                            </button>
                            <button
                              type="button"
                              className="secondary"
                              onClick={() => handleApprovalSubmit(item.approvalId as string, "rejected")}
                              disabled={approvalBusyId === item.approvalId || loading}
                            >
                              Reject patch
                            </button>
                          </>
                        ) : null}
                      </div>
                      {item.approvalStatus ? (
                        <p className="muted approval-decision">
                          Approval: {item.approvalStatus}
                          {item.approvalResolvedAt ? ` | ${formatTimestamp(item.approvalResolvedAt)}` : ""}
                        </p>
                      ) : null}
                      <pre className="patch-diff">
                        {diffText ?? "Diff not loaded yet. Click Load diff to fetch diff.get."}
                      </pre>
                    </li>
                  );
                })}
              </ul>
            ) : (
              <p className="empty-state compact">
                <strong>No patches yet</strong>
                <span>When the runtime emits patch.proposed, the patch summary and diff will show up here.</span>
              </p>
            )}
          </section>
          <section className="approval-stack">
            <div className="section-header approval-header">
              <h3>Approvals</h3>
              <span className="muted">{approvalCards.length} item(s)</span>
            </div>
            {approvalCards.length > 0 ? (
              <ul className="approval-list">
                {approvalCards.map((item) => {
                  const outputLines = commandOutputByTaskId.get(item.taskId) ?? [];
                  const isPending = item.status === "pending";
                  return (
                    <li key={item.approvalId} className="approval-card" data-status={item.status}>
                      <div className="section-header approval-topline">
                        <div>
                          <strong>{item.command}</strong>
                          <span className="muted">approvalId: {item.approvalId}</span>
                        </div>
                        <span className={`badge ${item.status === "approved" ? "ok" : "warn"}`}>
                          {item.status}
                        </span>
                      </div>
                      <dl className="meta compact approval-meta">
                        <div>
                          <dt>cwd</dt>
                          <dd className="break">{item.cwd}</dd>
                        </div>
                        <div>
                          <dt>shell</dt>
                          <dd>{item.shell}</dd>
                        </div>
                        <div>
                          <dt>timeout</dt>
                          <dd>{item.timeoutMs > 0 ? `${item.timeoutMs} ms` : "not recorded"}</dd>
                        </div>
                        <div>
                          <dt>task</dt>
                          <dd className="break">{item.taskId}</dd>
                        </div>
                      </dl>
                      {outputLines.length > 0 ? (
                        <pre className="approval-output">{outputLines.join("\n")}</pre>
                      ) : null}
                      {isPending ? (
                        <div className="approval-actions">
                          <button
                            type="button"
                            onClick={() => handleApprovalSubmit(item.approvalId, "approved")}
                            disabled={approvalBusyId === item.approvalId || loading}
                          >
                            {approvalBusyId === item.approvalId ? "Submitting..." : "Approve"}
                          </button>
                          <button
                            type="button"
                            className="secondary"
                            onClick={() => handleApprovalSubmit(item.approvalId, "rejected")}
                            disabled={approvalBusyId === item.approvalId || loading}
                          >
                            Reject
                          </button>
                        </div>
                      ) : (
                        <p className="muted approval-decision">
                          Decision: {item.status}
                          {item.resolvedAt ? ` | ${formatTimestamp(item.resolvedAt)}` : ""}
                        </p>
                      )}
                    </li>
                  );
                })}
              </ul>
            ) : (
              <p className="empty-state compact">
                <strong>No approval requests yet</strong>
                <span>When the runtime pauses on a command, the pending approval card appears here.</span>
              </p>
            )}
          </section>
          <ul className="timeline rich">
            {eventItems.length > 0 ? (
              eventItems.map((item) => (
                <li key={item.id}>
                  <div className="timeline-row">
                    <strong>{item.type}</strong>
                    <span>{item.time}</span>
                  </div>
                  <span>{item.summary}</span>
                  {config?.ui.showRawEvents ? <pre className="raw">{item.raw}</pre> : null}
                </li>
              ))
            ) : (
              <li>
                <strong>No events yet</strong>
                <span>Initialize the workspace and send a message to populate the runtime event stream.</span>
              </li>
            )}
          </ul>
        </section>
      </main>
    </div>
  );
}
