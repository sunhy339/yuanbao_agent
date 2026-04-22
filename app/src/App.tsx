import { useEffect, useMemo, useState } from "react";
import type {
  AgentEventEnvelope,
  AppConfig,
  AssistantTokenPayload,
  CommandOutputPayload,
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

function sortByUpdatedAtDesc<T extends { updatedAt: number }>(items: T[]): T[] {
  return [...items].sort((left, right) => right.updatedAt - left.updatedAt);
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
  const [activeTaskId, setActiveTaskId] = useState<string | null>(null);

  useEffect(() => {
    let disposed = false;

    Promise.all([runtimeClient.getHostStatus(), runtimeClient.getConfig(), runtimeClient.listSessions()])
      .then(([nextHostStatus, nextConfig, nextSessions]) => {
        if (disposed) {
          return;
        }

        const normalizedConfig = normalizeRuntimeConfig(nextConfig.config);
        setHostStatus(nextHostStatus);
        setConfig(normalizedConfig);
        setSearchGlob(serializePatternList(normalizedConfig.search.glob));
        setSearchIgnoreText(serializePatternList(normalizedConfig.search.ignore));
        setSessions(nextSessions.sessions);
        setSession(nextSessions.sessions[0] ?? null);
        setActiveTaskId(null);

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
    setAssistantOutput("");

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
      const nextSessions = result.sessions;
      setSessions(nextSessions);

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
        taskHistory.filter((item) => item.sessionId === preferredSession.id),
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
      setAssistantOutput("");

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
