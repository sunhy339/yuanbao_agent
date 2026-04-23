import "./session.css";

export interface SessionWorkspaceSession {
  id: string;
  title: string;
  status?: string;
  updatedAt?: number;
}

export interface SessionWorkspaceActiveTask {
  id: string;
  status?: string;
  goal?: string;
  resultSummary?: string;
}

export interface SessionWorkspaceMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  streaming?: boolean;
  createdAt?: number;
}

export interface SessionWorkspaceApproval {
  id: string;
  title: string;
  kind?: string;
  status: string;
  summary?: string;
  requestedAt?: number;
}

export interface SessionWorkspacePatch {
  id: string;
  summary: string;
  status: string;
  filesChanged?: number;
  updatedAt?: number;
}

export interface SessionWorkspaceTrace {
  id: string;
  type: string;
  source?: string;
  time?: number;
  summary?: string;
}

export interface SessionWorkspaceToolCall {
  id: string;
  toolName: string;
  status: string;
  resultSummary?: string;
  durationMs?: number;
}

export interface SessionWorkspaceProps {
  session: SessionWorkspaceSession | null;
  activeTask: SessionWorkspaceActiveTask | null;
  messages: SessionWorkspaceMessage[];
  taskCount?: number;
  approvals?: SessionWorkspaceApproval[];
  patches?: SessionWorkspacePatch[];
  traces?: SessionWorkspaceTrace[];
  toolCalls?: SessionWorkspaceToolCall[];
  onApprove?(approvalId: string): void | Promise<void>;
  onReject?(approvalId: string): void | Promise<void>;
  onLoadPatch?(patchId: string): void | Promise<void>;
  onRefreshTrace?(): void | Promise<void>;
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

function formatTaskCount(taskCount?: number) {
  if (taskCount === undefined) {
    return "No task ledger";
  }

  return `${taskCount} ${taskCount === 1 ? "task" : "tasks"}`;
}

function formatFilesChanged(filesChanged?: number) {
  if (filesChanged === undefined) {
    return null;
  }

  return `${filesChanged} ${filesChanged === 1 ? "file" : "files"} changed`;
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

function compactItems<T>(items: T[]) {
  return {
    visibleItems: items.slice(0, 2),
    hiddenCount: Math.max(items.length - 2, 0),
  };
}

export function SessionWorkspace({
  session,
  activeTask,
  messages,
  taskCount,
  approvals = [],
  patches = [],
  traces = [],
  toolCalls = [],
  onApprove,
  onReject,
  onLoadPatch,
  onRefreshTrace,
  busyId = null,
}: SessionWorkspaceProps) {
  if (!session) {
    return (
      <main className="session-workspace session-workspace-empty" aria-labelledby="session-empty-title">
        <section className="session-empty-card">
          <span className="session-empty-rule" aria-hidden="true" />
          <p className="session-kicker">Session desk</p>
          <h1 id="session-empty-title">Open or create a session</h1>
          <p>
            Choose a session from the rail or start a new one to bring the
            conversation, task state, and operational trace onto this desk.
          </p>
        </section>
      </main>
    );
  }

  const updatedAt = formatTimestamp(session.updatedAt);
  const compactApprovals = compactItems(approvals);
  const compactPatches = compactItems(patches);
  const compactTraceItems = compactItems(traces);
  const compactToolCalls = compactItems(toolCalls);

  return (
    <main className="session-workspace" aria-labelledby="session-title">
      <header className="session-header">
        <div>
          <p className="session-kicker">Active conversation</p>
          <h1 id="session-title">{session.title}</h1>
        </div>

        <dl className="session-meta-strip" aria-label="Session metadata">
          <div>
            <dt>Status</dt>
            <dd>{session.status ?? "ready"}</dd>
          </div>
          <div>
            <dt>Updated</dt>
            <dd>{updatedAt ?? "Not recorded"}</dd>
          </div>
          <div>
            <dt>Tasks</dt>
            <dd>{formatTaskCount(taskCount)}</dd>
          </div>
        </dl>
      </header>

      <section className="session-body" aria-label="Session conversation and operations">
        <section className="message-stream" aria-label="Conversation messages">
          {messages.length === 0 ? (
            <div className="message-stream-empty">
              <p className="session-kicker">Quiet thread</p>
              <h2>No messages yet</h2>
              <p>The composer below the workbench will begin this session when connected.</p>
            </div>
          ) : (
            messages.map((message) => (
              <article
                className="message-bubble"
                data-role={message.role}
                key={message.id}
                aria-label={`${message.role} message`}
              >
                <div className="message-bubble-head">
                  <span>{message.role === "user" ? "User" : "Assistant"}</span>
                  {message.streaming ? <em>Streaming</em> : null}
                  {message.createdAt ? <time>{formatTimestamp(message.createdAt)}</time> : null}
                </div>
                <p>{message.content}</p>
              </article>
            ))
          )}
        </section>

        <aside className="session-operations" aria-label="Session operations">
          <section className="task-panel" aria-labelledby="active-task-title">
            <div className="panel-heading">
              <p className="session-kicker">Task progress</p>
              <h2 id="active-task-title">Active task</h2>
            </div>

            {activeTask ? (
              <div className="task-readout">
                <span className="task-status">{activeTask.status ?? "pending"}</span>
                <p className="task-goal">{activeTask.goal ?? "Task goal not provided."}</p>
                {activeTask.resultSummary ? (
                  <p className="task-summary">{activeTask.resultSummary}</p>
                ) : (
                  <p className="task-summary">Awaiting the next runtime update.</p>
                )}
              </div>
            ) : (
              <p className="task-summary">No active task is attached to this session.</p>
            )}
          </section>

          <section className="operation-shelf" aria-labelledby="operation-shelf-title">
            <div className="panel-heading">
              <p className="session-kicker">Operations</p>
              <h2 id="operation-shelf-title">Runtime shelf</h2>
            </div>

            <section className="operation-section" aria-labelledby="approvals-title">
              <div className="operation-section-head">
                <h3 id="approvals-title">Approvals</h3>
                <span>{approvals.length}</span>
              </div>
              {approvals.length === 0 ? (
                <p className="operation-empty">No pending approval slips.</p>
              ) : (
                <ul className="operation-list">
                  {compactApprovals.visibleItems.map((approval) => {
                    const isBusy = busyId === approval.id;

                    return (
                      <li className="operation-card" key={approval.id}>
                        <div className="operation-card-head">
                          <strong>{approval.title}</strong>
                          <span>{approval.status}</span>
                        </div>
                        <div className="operation-tags">
                          {approval.kind ? <span>{approval.kind}</span> : null}
                          {approval.requestedAt ? <time>{formatTimestamp(approval.requestedAt)}</time> : null}
                        </div>
                        {approval.summary ? <p>{approval.summary}</p> : null}
                        <div className="operation-actions">
                          {onApprove ? (
                            <button
                              aria-label={`Approve ${approval.title}`}
                              disabled={isBusy}
                              onClick={() => void onApprove(approval.id)}
                              type="button"
                            >
                              Approve
                            </button>
                          ) : null}
                          {onReject ? (
                            <button
                              aria-label={`Reject ${approval.title}`}
                              disabled={isBusy}
                              onClick={() => void onReject(approval.id)}
                              type="button"
                            >
                              Reject
                            </button>
                          ) : null}
                        </div>
                      </li>
                    );
                  })}
                </ul>
              )}
              {compactApprovals.hiddenCount > 0 ? (
                <p className="operation-more">+{compactApprovals.hiddenCount} more approvals</p>
              ) : null}
            </section>

            <section className="operation-section" aria-labelledby="patches-title">
              <div className="operation-section-head">
                <h3 id="patches-title">Patches</h3>
                <span>{patches.length}</span>
              </div>
              {patches.length === 0 ? (
                <p className="operation-empty">No patch docket loaded.</p>
              ) : (
                <ul className="operation-list">
                  {compactPatches.visibleItems.map((patch) => {
                    const filesChanged = formatFilesChanged(patch.filesChanged);
                    const isBusy = busyId === patch.id;

                    return (
                      <li className="operation-card" key={patch.id}>
                        <div className="operation-card-head">
                          <strong>{patch.summary}</strong>
                          <span>{patch.status}</span>
                        </div>
                        <div className="operation-tags">
                          {filesChanged ? <span>{filesChanged}</span> : null}
                          {patch.updatedAt ? <time>{formatTimestamp(patch.updatedAt)}</time> : null}
                        </div>
                        {onLoadPatch ? (
                          <div className="operation-actions">
                            <button
                              aria-label={`Open patch ${patch.summary}`}
                              disabled={isBusy}
                              onClick={() => void onLoadPatch(patch.id)}
                              type="button"
                            >
                              Open patch
                            </button>
                          </div>
                        ) : null}
                      </li>
                    );
                  })}
                </ul>
              )}
              {compactPatches.hiddenCount > 0 ? (
                <p className="operation-more">+{compactPatches.hiddenCount} more patches</p>
              ) : null}
            </section>

            <section className="operation-section" aria-labelledby="trace-tools-title">
              <div className="operation-section-head">
                <h3 id="trace-tools-title">Trace and tools</h3>
                {onRefreshTrace ? (
                  <button
                    aria-label="Refresh trace"
                    disabled={busyId === "trace"}
                    onClick={() => void onRefreshTrace()}
                    type="button"
                  >
                    Refresh
                  </button>
                ) : (
                  <span>{traces.length + toolCalls.length}</span>
                )}
              </div>
              {traces.length === 0 && toolCalls.length === 0 ? (
                <p className="operation-empty">Trace desk is quiet.</p>
              ) : (
                <div className="trace-stack">
                  {compactTraceItems.visibleItems.map((trace) => (
                    <article className="operation-card" key={trace.id}>
                      <div className="operation-card-head">
                        <strong>{trace.summary ?? trace.type}</strong>
                        <span>{trace.type}</span>
                      </div>
                      <div className="operation-tags">
                        {trace.source ? <span>{trace.source}</span> : null}
                        {trace.time ? <time>{formatTimestamp(trace.time)}</time> : null}
                      </div>
                    </article>
                  ))}
                  {compactToolCalls.visibleItems.map((toolCall) => {
                    const duration = formatDuration(toolCall.durationMs);

                    return (
                      <article className="operation-card" key={toolCall.id}>
                        <div className="operation-card-head">
                          <strong>{toolCall.toolName}</strong>
                          <span>{toolCall.status}</span>
                        </div>
                        <div className="operation-tags">{duration ? <span>{duration}</span> : null}</div>
                        {toolCall.resultSummary ? <p>{toolCall.resultSummary}</p> : null}
                      </article>
                    );
                  })}
                </div>
              )}
              {compactTraceItems.hiddenCount + compactToolCalls.hiddenCount > 0 ? (
                <p className="operation-more">
                  +{compactTraceItems.hiddenCount + compactToolCalls.hiddenCount} more trace items
                </p>
              ) : null}
            </section>
          </section>
        </aside>
      </section>
    </main>
  );
}

export default SessionWorkspace;
