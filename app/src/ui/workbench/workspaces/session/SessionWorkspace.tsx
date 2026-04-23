import { useState } from "react";
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
  resultSummary?: string;
  durationMs?: number;
  tokenCount?: number;
  argsPreview?: string;
  input?: string;
  output?: string;
  stdout?: string;
  stderr?: string;
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

function formatTokens(tokenCount?: number) {
  if (tokenCount === undefined) {
    return null;
  }

  return `${tokenCount.toLocaleString()} tokens`;
}

function formatLineStat(value?: number, label = "") {
  if (value === undefined) {
    return null;
  }

  return `${value >= 0 ? "+" : ""}${value}${label}`;
}

function getPatchFiles(patch: SessionWorkspacePatch): SessionWorkspacePatchFile[] {
  if (patch.files && patch.files.length > 0) {
    return patch.files;
  }

  return patch.filesChanged
    ? Array.from({ length: patch.filesChanged }, (_, index) => ({
        path: `changed-file-${index + 1}`,
        status: "changed",
      }) satisfies SessionWorkspacePatchFile)
    : [];
}

function getPatchDiff(patch: SessionWorkspacePatch) {
  if (patch.diff) {
    return patch.diff;
  }

  const fileDiffs = patch.files?.flatMap((file) => (file.diff ? [`# ${file.path}`, file.diff] : [])) ?? [];
  return fileDiffs.length > 0 ? fileDiffs.join("\n") : null;
}

function getTraceTitle(trace: SessionWorkspaceTrace) {
  return trace.title ?? trace.summary ?? trace.type;
}

function getCollaborationTaskTitle(task?: SessionWorkspaceChildTask, fallback = "Child task") {
  return task?.title ?? fallback;
}

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

function toggleSetValue(current: Set<string>, value: string) {
  const next = new Set(current);

  if (next.has(value)) {
    next.delete(value);
  } else {
    next.add(value);
  }

  return next;
}

function PanelToggle({
  controls,
  expanded,
  label,
  onClick,
}: {
  controls: string;
  expanded: boolean;
  label: string;
  onClick(): void;
}) {
  return (
    <button
      aria-controls={controls}
      aria-expanded={expanded}
      className="session-plain-button"
      onClick={onClick}
      type="button"
    >
      {expanded ? "Collapse" : "Expand"} {label}
    </button>
  );
}

function CodeBlock({ children, label }: { children: string; label: string }) {
  return (
    <pre aria-label={label} className="session-code-block">
      <code>{children}</code>
    </pre>
  );
}

export function SessionWorkspace({
  session,
  activeTask,
  messages,
  taskCount,
  collaboration,
  approvals = [],
  patches = [],
  traces = [],
  toolCalls = [],
  composerContext,
  onApprove,
  onApproveForSession,
  onReject,
  onLoadPatch,
  onCopyPatchPath,
  onRefreshTrace,
  busyId = null,
}: SessionWorkspaceProps) {
  const [expandedPanels, setExpandedPanels] = useState<Set<string>>(() => new Set(["active-task"]));
  const [fullApprovalInputs, setFullApprovalInputs] = useState<Set<string>>(() => new Set());
  const collaborationWorkers = collaboration?.workers ?? [];
  const collaborationChildTasks = collaboration?.childTasks ?? [];
  const collaborationResults = collaboration?.results ?? [];
  const collaborationTaskById = new Map(collaborationChildTasks.map((task) => [task.id, task]));
  const collaborationWorkerById = new Map(collaborationWorkers.map((worker) => [worker.id, worker]));

  const togglePanel = (panelId: string) => {
    setExpandedPanels((current) => toggleSetValue(current, panelId));
  };

  const toggleApprovalInput = (approvalId: string) => {
    setFullApprovalInputs((current) => toggleSetValue(current, approvalId));
  };

  if (!session) {
    return (
      <main className="session-workspace session-workspace-empty" aria-labelledby="session-empty-title">
        <section className="session-empty-card">
          <span className="session-empty-rule" aria-hidden="true" />
          <p className="session-kicker">Session desk</p>
          <h1 id="session-empty-title">Open or create a session</h1>
          <p>
            Choose a session from the rail or start a new one to bring the
            conversation, approvals, patches, and runtime trace onto this desk.
          </p>
        </section>
      </main>
    );
  }

  const updatedAt = formatTimestamp(session.updatedAt);
  const activeTaskExpanded = expandedPanels.has("active-task");
  const messageCount = session.messageCount ?? messages.length;

  return (
    <main className="session-workspace" aria-labelledby="session-title">
      <header className="session-header">
        <div className="session-title-block">
          <p className="session-kicker">Execution dossier</p>
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
          <div>
            <dt>Messages</dt>
            <dd>{messageCount}</dd>
          </div>
          <div>
            <dt>Tokens</dt>
            <dd>{session.tokenCount?.toLocaleString() ?? "Not recorded"}</dd>
          </div>
        </dl>
      </header>

      <section className="session-composer-context" aria-label="Current composer context">
        <span>{composerContext?.cwd ?? "cwd not selected"}</span>
        <span>{composerContext?.repo ?? "repo detached"}</span>
        <span>{composerContext?.branch ?? "branch unknown"}</span>
        <span>{composerContext?.model ?? "model not selected"}</span>
        <span>{composerContext?.permissionMode ?? "permission mode unset"}</span>
      </section>

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
        </section>

        <aside className="session-operations" aria-label="Session operations">
          <section className="task-panel" aria-labelledby="active-task-title">
            <div className="panel-heading panel-heading-with-action">
              <div>
                <p className="session-kicker">Task progress</p>
                <h2 id="active-task-title">Active task</h2>
              </div>
              <PanelToggle
                controls="active-task-detail"
                expanded={activeTaskExpanded}
                label="task"
                onClick={() => togglePanel("active-task")}
              />
            </div>

            <div className="task-readout">
              {activeTask ? (
                <>
                  <span className="task-status">{activeTask.status ?? "pending"}</span>
                  <p className="task-goal">{activeTask.goal ?? "Task goal not provided."}</p>
                  {activeTask.resultSummary ? (
                    <p className="task-summary">{activeTask.resultSummary}</p>
                  ) : (
                    <p className="task-summary">Awaiting the next runtime update.</p>
                  )}
                </>
              ) : (
                <p className="task-summary">No active task is attached to this session.</p>
              )}
            </div>

            {activeTaskExpanded ? (
              <div className="task-detail" id="active-task-detail">
                {activeTask?.planSteps && activeTask.planSteps.length > 0 ? (
                  <ol className="plan-step-list" aria-label="Plan steps">
                    {activeTask.planSteps.map((step) => (
                      <li className="plan-step" data-status={step.status ?? "pending"} key={step.id}>
                        <div>
                          <strong>{step.title}</strong>
                          {step.summary ? <p>{step.summary}</p> : null}
                          {step.detail ? <p>{step.detail}</p> : null}
                        </div>
                        <span>{step.status ?? "pending"}</span>
                        {step.durationMs ? <small>{formatDuration(step.durationMs)}</small> : null}
                      </li>
                    ))}
                  </ol>
                ) : (
                  <p className="operation-empty">No plan steps have been recorded yet.</p>
                )}
              </div>
            ) : null}
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
                  {approvals.map((approval) => {
                    const isBusy = busyId === approval.id;
                    const inputExpanded = fullApprovalInputs.has(approval.id);
                    const input = approval.fullInput ?? approval.command ?? approval.parametersPreview;

                    return (
                      <li className="operation-card approval-card" data-risk={approval.risk ?? "medium"} key={approval.id}>
                        <div className="operation-card-head">
                          <strong>{approval.title}</strong>
                          <span>{approval.status}</span>
                        </div>
                        <div className="operation-tags">
                          {approval.kind ? <span>{approval.kind}</span> : null}
                          {approval.cwd ? <span>{approval.cwd}</span> : null}
                          {approval.requestedAt ? <time>{formatTimestamp(approval.requestedAt)}</time> : null}
                        </div>
                        {approval.summary ? <p>{approval.summary}</p> : null}
                        {approval.parametersPreview ? (
                          <CodeBlock label={`${approval.title} parameters preview`}>
                            {approval.parametersPreview}
                          </CodeBlock>
                        ) : null}
                        {input ? (
                          <div className="approval-input">
                            <button
                              aria-controls={`approval-input-${approval.id}`}
                              aria-expanded={inputExpanded}
                              className="session-plain-button"
                              onClick={() => toggleApprovalInput(approval.id)}
                              type="button"
                            >
                              {inputExpanded ? "Hide full input" : "Show full input"}
                            </button>
                            {inputExpanded ? (
                              <CodeBlock label={`${approval.title} full input`}>
                                {approval.fullInput ?? input}
                              </CodeBlock>
                            ) : null}
                          </div>
                        ) : null}
                        <div className="operation-actions">
                          {onApprove ? (
                            <button
                              aria-label={`Allow ${approval.title}`}
                              disabled={isBusy}
                              onClick={() => void onApprove(approval.id)}
                              type="button"
                            >
                              Allow
                            </button>
                          ) : null}
                          {onApproveForSession ? (
                            <button
                              aria-label={`Allow ${approval.title} for session`}
                              disabled={isBusy}
                              onClick={() => void onApproveForSession(approval.id)}
                              type="button"
                            >
                              Allow for session
                            </button>
                          ) : null}
                          {onReject ? (
                            <button
                              aria-label={`Deny ${approval.title}`}
                              disabled={isBusy}
                              onClick={() => void onReject(approval.id)}
                              type="button"
                            >
                              Deny
                            </button>
                          ) : null}
                        </div>
                      </li>
                    );
                  })}
                </ul>
              )}
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
                  {patches.map((patch) => {
                    const filesChanged = formatFilesChanged(patch.filesChanged ?? patch.files?.length);
                    const isBusy = busyId === patch.id;
                    const panelId = `patch-detail-${patch.id}`;
                    const isExpanded = expandedPanels.has(panelId);
                    const patchFiles = getPatchFiles(patch);
                    const patchDiff = getPatchDiff(patch);

                    return (
                      <li className="operation-card patch-card" key={patch.id}>
                        <div className="operation-card-head">
                          <strong>{patch.summary}</strong>
                          <span>{patch.status}</span>
                        </div>
                        <div className="operation-tags">
                          {filesChanged ? <span>{filesChanged}</span> : null}
                          {formatLineStat(patch.additions) ? <span className="line-add">{formatLineStat(patch.additions)}</span> : null}
                          {formatLineStat(patch.deletions) ? <span className="line-del">-{patch.deletions}</span> : null}
                          {patch.updatedAt ? <time>{formatTimestamp(patch.updatedAt)}</time> : null}
                        </div>
                        <div className="operation-actions">
                          <PanelToggle
                            controls={panelId}
                            expanded={isExpanded}
                            label="patch"
                            onClick={() => togglePanel(panelId)}
                          />
                          {onLoadPatch ? (
                            <button
                              aria-label={`Load patch details ${patch.summary}`}
                              disabled={isBusy}
                              onClick={() => void onLoadPatch(patch.id)}
                              type="button"
                            >
                              Load details
                            </button>
                          ) : null}
                        </div>
                        {isExpanded ? (
                          <div className="patch-detail" id={panelId}>
                            {patchFiles.length > 0 ? (
                              <ul className="patch-file-list" aria-label={`${patch.summary} files`}>
                                {patchFiles.map((file) => (
                                  <li key={file.path}>
                                    <span>{file.status ?? "changed"}</span>
                                    <code>{file.path}</code>
                                    {file.additions !== undefined ? <em className="line-add">+{file.additions}</em> : null}
                                    {file.deletions !== undefined ? <em className="line-del">-{file.deletions}</em> : null}
                                    {onCopyPatchPath ? (
                                      <button
                                        aria-label={`Copy path ${file.path}`}
                                        onClick={() => void onCopyPatchPath(patch.id, file.path)}
                                        type="button"
                                      >
                                        Copy path
                                      </button>
                                    ) : null}
                                  </li>
                                ))}
                              </ul>
                            ) : (
                              <p className="operation-empty">No file list is attached to this patch.</p>
                            )}
                            {patchDiff ? (
                              <CodeBlock label={`${patch.summary} diff`}>{patchDiff}</CodeBlock>
                            ) : (
                              <p className="operation-empty">Diff body is not loaded yet.</p>
                            )}
                          </div>
                        ) : null}
                      </li>
                    );
                  })}
                </ul>
              )}
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
                  {traces.map((trace) => {
                    const panelId = `trace-detail-${trace.id}`;
                    const isExpanded = expandedPanels.has(panelId);
                    const duration = formatDuration(trace.durationMs);
                    const tokens = formatTokens(trace.tokenCount);

                    return (
                      <article className="operation-card trace-card" data-trace-type={trace.type} key={trace.id}>
                        <div className="operation-card-head">
                          <strong>{getTraceTitle(trace)}</strong>
                          <span>{trace.status ?? trace.type}</span>
                        </div>
                        <div className="operation-tags">
                          {trace.source ? <span>{trace.source}</span> : null}
                          {duration ? <span>{duration}</span> : null}
                          {tokens ? <span>{tokens}</span> : null}
                          {trace.time ? <time>{formatTimestamp(trace.time)}</time> : null}
                        </div>
                        {trace.summary && trace.summary !== getTraceTitle(trace) ? <p>{trace.summary}</p> : null}
                        <div className="operation-actions">
                          <PanelToggle
                            controls={panelId}
                            expanded={isExpanded}
                            label="trace"
                            onClick={() => togglePanel(panelId)}
                          />
                        </div>
                        {isExpanded ? (
                          <div className="trace-detail" id={panelId}>
                            {trace.detail ? <p>{trace.detail}</p> : null}
                            {trace.stdout ? <CodeBlock label={`${getTraceTitle(trace)} stdout`}>{trace.stdout}</CodeBlock> : null}
                            {trace.stderr ? <CodeBlock label={`${getTraceTitle(trace)} stderr`}>{trace.stderr}</CodeBlock> : null}
                            {!trace.detail && !trace.stdout && !trace.stderr ? (
                              <p className="operation-empty">No expanded trace payload is attached.</p>
                            ) : null}
                          </div>
                        ) : null}
                      </article>
                    );
                  })}
                  {toolCalls.map((toolCall) => {
                    const panelId = `tool-detail-${toolCall.id}`;
                    const isExpanded = expandedPanels.has(panelId);
                    const duration = formatDuration(toolCall.durationMs);
                    const tokens = formatTokens(toolCall.tokenCount);

                    return (
                      <article className="operation-card tool-card" key={toolCall.id}>
                        <div className="operation-card-head">
                          <strong>{toolCall.toolName}</strong>
                          <span>{toolCall.status}</span>
                        </div>
                        <div className="operation-tags">
                          {duration ? <span>{duration}</span> : null}
                          {tokens ? <span>{tokens}</span> : null}
                        </div>
                        {toolCall.resultSummary ? <p>{toolCall.resultSummary}</p> : null}
                        <div className="operation-actions">
                          <PanelToggle
                            controls={panelId}
                            expanded={isExpanded}
                            label="tool"
                            onClick={() => togglePanel(panelId)}
                          />
                        </div>
                        {isExpanded ? (
                          <div className="trace-detail" id={panelId}>
                            {toolCall.argsPreview ? <CodeBlock label={`${toolCall.toolName} args preview`}>{toolCall.argsPreview}</CodeBlock> : null}
                            {toolCall.input ? <CodeBlock label={`${toolCall.toolName} input`}>{toolCall.input}</CodeBlock> : null}
                            {toolCall.output ? <CodeBlock label={`${toolCall.toolName} output`}>{toolCall.output}</CodeBlock> : null}
                            {toolCall.stdout ? <CodeBlock label={`${toolCall.toolName} stdout`}>{toolCall.stdout}</CodeBlock> : null}
                            {toolCall.stderr ? <CodeBlock label={`${toolCall.toolName} stderr`}>{toolCall.stderr}</CodeBlock> : null}
                            {!toolCall.argsPreview && !toolCall.input && !toolCall.output && !toolCall.stdout && !toolCall.stderr ? (
                              <p className="operation-empty">No expanded tool payload is attached.</p>
                            ) : null}
                          </div>
                        ) : null}
                      </article>
                    );
                  })}
                </div>
              )}
            </section>
          </section>

          <aside className="session-collaboration" aria-labelledby="collaboration-title">
            <div className="panel-heading">
              <p className="session-kicker">Worker lane</p>
              <h2 id="collaboration-title">Collaboration</h2>
            </div>

            <dl className="collaboration-meta-strip" aria-label="Collaboration summary">
              <div>
                <dt>Workers</dt>
                <dd>{collaborationWorkers.length}</dd>
              </div>
              <div>
                <dt>Tasks</dt>
                <dd>{collaborationChildTasks.length}</dd>
              </div>
              <div>
                <dt>Results</dt>
                <dd>{collaborationResults.length}</dd>
              </div>
            </dl>

            <section className="collaboration-section" aria-labelledby="workers-title">
              <div className="collaboration-section-head">
                <h3 id="workers-title">Active workers</h3>
                <span>{collaborationWorkers.length}</span>
              </div>
              {collaborationWorkers.length === 0 ? (
                <p className="operation-empty">No active workers are reported yet.</p>
              ) : (
                <ul className="collaboration-list">
                  {collaborationWorkers.map((worker) => {
                    const claimedTask = worker.claimedTaskId ? collaborationTaskById.get(worker.claimedTaskId) : null;

                    return (
                      <li className="collaboration-row" key={worker.id}>
                        <div className="collaboration-row-head">
                          <strong>{worker.name}</strong>
                          <div className="collaboration-tags">
                            {worker.status ? <span>{worker.status}</span> : null}
                            {worker.mode ? <span>{worker.mode}</span> : null}
                            {worker.updatedAt ? <time>{formatTimestamp(worker.updatedAt)}</time> : null}
                          </div>
                        </div>
                        {worker.summary ? <p>{worker.summary}</p> : null}
                        {claimedTask ? (
                          <p className="collaboration-note">Claimed: {getCollaborationTaskTitle(claimedTask)}</p>
                        ) : worker.claimedTaskId ? (
                          <p className="collaboration-note">Claimed task: {worker.claimedTaskId}</p>
                        ) : null}
                      </li>
                    );
                  })}
                </ul>
              )}
            </section>

            <section className="collaboration-section" aria-labelledby="tasks-title">
              <div className="collaboration-section-head">
                <h3 id="tasks-title">Claimed child tasks</h3>
                <span>{collaborationChildTasks.length}</span>
              </div>
              {collaborationChildTasks.length === 0 ? (
                <p className="operation-empty">No child tasks have been claimed.</p>
              ) : (
                <ul className="collaboration-list">
                  {collaborationChildTasks.map((task) => {
                    const worker = task.workerId ? collaborationWorkerById.get(task.workerId) : null;

                    return (
                      <li className="collaboration-row" key={task.id}>
                        <div className="collaboration-row-head">
                          <strong>{task.title}</strong>
                          <div className="collaboration-tags">
                            {task.status ? <span>{task.status}</span> : null}
                            {task.workerName ? <span>{task.workerName}</span> : null}
                            {task.updatedAt ? <time>{formatTimestamp(task.updatedAt)}</time> : null}
                          </div>
                        </div>
                        {task.summary ? <p>{task.summary}</p> : null}
                        {worker ? <p className="collaboration-note">Owned by {worker.name}</p> : null}
                      </li>
                    );
                  })}
                </ul>
              )}
            </section>

            <section className="collaboration-section" aria-labelledby="results-title">
              <div className="collaboration-section-head">
                <h3 id="results-title">Latest child-task results</h3>
                <span>{collaborationResults.length}</span>
              </div>
              {collaborationResults.length === 0 ? (
                <p className="operation-empty">No child-task results have landed yet.</p>
              ) : (
                <ul className="collaboration-list">
                  {collaborationResults.map((result) => {
                    const task = result.taskId ? collaborationTaskById.get(result.taskId) : null;

                    return (
                      <li className="collaboration-row" key={result.id}>
                        <div className="collaboration-row-head">
                          <strong>{task ? task.title : result.title ?? result.taskId ?? "Child task result"}</strong>
                          <div className="collaboration-tags">
                            {result.status ? <span>{result.status}</span> : null}
                            {result.updatedAt ? <time>{formatTimestamp(result.updatedAt)}</time> : null}
                          </div>
                        </div>
                        {result.summary ? <p>{result.summary}</p> : null}
                        {result.taskId ? <p className="collaboration-note">Task: {result.taskId}</p> : null}
                      </li>
                    );
                  })}
                </ul>
              )}
            </section>
          </aside>
        </aside>
      </section>
    </main>
  );
}

export default SessionWorkspace;
