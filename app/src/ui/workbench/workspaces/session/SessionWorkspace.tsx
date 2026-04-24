import "./session.css";

export interface SessionWorkspaceSession {
  id: string;
  title: string;
  status?: string;
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

type RuntimeTimelineItem =
  | {
      id: string;
      kind: "approval";
      title: string;
      status: string;
      summary?: string;
      meta?: string[];
      code?: string;
    }
  | {
      id: string;
      kind: "patch";
      title: string;
      status: string;
      summary?: string;
      meta?: string[];
      code?: string;
    }
  | {
      id: string;
      kind: "trace";
      title: string;
      status?: string;
      summary?: string;
      meta?: string[];
      code?: string;
    }
  | {
      id: string;
      kind: "tool";
      title: string;
      status: string;
      summary?: string;
      meta?: string[];
      code?: string;
    }
  | {
      id: string;
      kind: "command";
      title: string;
      status: string;
      summary?: string;
      meta?: string[];
      code?: string;
    }
  | {
      id: string;
      kind: "task";
      title: string;
      status?: string;
      summary?: string;
      meta?: string[];
      code?: string;
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

function buildRuntimeItems({
  activeTask,
  approvals = [],
  patches = [],
  traces = [],
  toolCalls = [],
  backgroundJobs = [],
}: Pick<
  SessionWorkspaceProps,
  "activeTask" | "approvals" | "patches" | "traces" | "toolCalls" | "backgroundJobs"
>): RuntimeTimelineItem[] {
  const items: RuntimeTimelineItem[] = [];

  if (activeTask) {
    items.push({
      id: `task:${activeTask.id}`,
      kind: "task",
      title: activeTask.goal || "Active task",
      status: activeTask.status,
      summary: activeTask.resultSummary,
      meta: compactMeta([activeTask.id, activeTask.planSteps?.length ? `${activeTask.planSteps.length} steps` : null]),
    });
  }

  approvals.forEach((approval) => {
    items.push({
      id: `approval:${approval.id}`,
      kind: "approval",
      title: approval.title,
      status: approval.status,
      summary: approval.summary,
      meta: compactMeta([approval.kind, approval.risk ? `risk: ${approval.risk}` : null, approval.cwd]),
      code: approval.command || approval.parametersPreview || approval.fullInput,
    });
  });

  patches.forEach((patch) => {
    items.push({
      id: `patch:${patch.id}`,
      kind: "patch",
      title: patch.summary,
      status: patch.status,
      summary: patch.files?.map((file) => file.path).join("\n"),
      meta: compactMeta([
        patch.filesChanged !== undefined ? `${patch.filesChanged} files` : null,
        patch.additions !== undefined ? `+${patch.additions}` : null,
        patch.deletions !== undefined ? `-${patch.deletions}` : null,
      ]),
      code: patch.diff,
    });
  });

  traces.forEach((trace) => {
    items.push({
      id: `trace:${trace.id}`,
      kind: "trace",
      title: trace.title || trace.type,
      status: trace.status,
      summary: trace.summary || trace.detail,
      meta: compactMeta([
        trace.source,
        trace.type,
        formatDuration(trace.durationMs),
        trace.tokenCount !== undefined ? `${trace.tokenCount} tokens` : null,
      ]),
    });
  });

  toolCalls.forEach((toolCall) => {
    items.push({
      id: `tool:${toolCall.id}`,
      kind: "tool",
      title: toolCall.toolName,
      status: toolCall.status,
      summary: toolCall.resultSummary || toolCall.output || toolCall.stdout || toolCall.stderr,
      meta: compactMeta([
        formatDuration(toolCall.durationMs),
        toolCall.tokenCount !== undefined ? `${toolCall.tokenCount} tokens` : null,
      ]),
      code: toolCall.argsPreview || toolCall.input,
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
    });
  });

  return items;
}

function RuntimeTimeline({ items }: { items: RuntimeTimelineItem[] }) {
  if (items.length === 0) {
    return null;
  }

  return (
    <section className="runtime-timeline" aria-label="Runtime timeline">
      <div className="runtime-timeline-heading">
        <span>Runtime</span>
        <strong>{items.length} events</strong>
      </div>
      {items.map((item) => (
        <article className="runtime-event-card" data-kind={item.kind} key={item.id}>
          <div className="runtime-event-head">
            <span>{item.kind}</span>
            <strong>{item.title}</strong>
            {item.status ? <em>{item.status}</em> : null}
          </div>
          {item.meta?.length ? (
            <div className="runtime-event-meta">
              {item.meta.map((entry) => (
                <span key={entry}>{entry}</span>
              ))}
            </div>
          ) : null}
          {item.summary ? <p>{item.summary}</p> : null}
          {item.code ? <pre>{item.code}</pre> : null}
        </article>
      ))}
    </section>
  );
}

export function SessionWorkspace({
  session,
  messages,
  activeTask,
  approvals,
  patches,
  traces,
  toolCalls,
  backgroundJobs,
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

  const runtimeItems = buildRuntimeItems({
    activeTask,
    approvals,
    patches,
    traces,
    toolCalls,
    backgroundJobs,
  });

  return (
    <main className="session-workspace session-workspace-chat-only" aria-labelledby="session-title">
      <header className="session-chat-header">
        <p className="session-kicker">Conversation</p>
        <h1 id="session-title">{session.title}</h1>
      </header>

      <section className="message-stream message-stream-chat-only" aria-label="Conversation messages">
        {messages.length === 0 ? (
          <div className="message-stream-empty">
            <p className="session-kicker">Quiet thread</p>
            <h2>No messages yet</h2>
            <p>Send the first message from the composer below.</p>
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
                <span>{getRoleLabel(message.role)}</span>
                {message.toolName ? <em>{message.toolName}</em> : null}
                {message.status ? <em>{message.status}</em> : null}
                {message.streaming ? <em>Streaming</em> : null}
                {message.createdAt ? <time>{formatTimestamp(message.createdAt)}</time> : null}
              </div>
              <p>{message.content}</p>
            </article>
          ))
        )}
        <RuntimeTimeline items={runtimeItems} />
      </section>
    </main>
  );
}

export default SessionWorkspace;
