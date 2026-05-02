import type {
  McpServerRecord,
  ScheduledTaskRecord,
  SessionRecord,
  SkillPresetRecord,
  TaskRecord,
  WorkspaceRef,
} from "@shared";
import { Button, Panel, StatusBadge } from "../components/ui";
import "./workbench-overview.css";

type RuntimeStatus = "ready" | "degraded" | "offline";

export interface WorkbenchOverviewPageProps {
  workspace: WorkspaceRef | null;
  workspacePath: string;
  providerLabel: string;
  runtimeStatus: RuntimeStatus;
  sessions: SessionRecord[];
  tasks: TaskRecord[];
  scheduledTasks: ScheduledTaskRecord[];
  mcpServers: McpServerRecord[];
  skills: SkillPresetRecord[];
  onOpenNewSession: () => void;
  onOpenSession: (session: SessionRecord) => void;
  onOpenScheduled: () => void;
  onOpenMcp: () => void;
  onOpenSettings: () => void;
}

const ACTIVE_TASK_STATUSES = new Set<TaskRecord["status"]>([
  "queued",
  "planning",
  "running",
  "waiting_approval",
  "verifying",
  "paused",
]);

function formatTimestamp(value?: number | null) {
  if (!value) {
    return "never";
  }
  return new Date(value).toLocaleString("en-US", { hour12: false });
}

function statusTone(status: RuntimeStatus) {
  if (status === "ready") {
    return "success" as const;
  }
  if (status === "degraded") {
    return "warning" as const;
  }
  return "danger" as const;
}

function taskTone(status: TaskRecord["status"]) {
  if (status === "completed") return "success" as const;
  if (status === "failed" || status === "cancelled") return "danger" as const;
  if (status === "waiting_approval" || status === "paused") return "warning" as const;
  return "info" as const;
}

function scheduleLabel(task?: ScheduledTaskRecord) {
  if (!task) {
    return "No active schedule";
  }
  return task.name;
}

export function WorkbenchOverviewPage({
  workspace,
  workspacePath,
  providerLabel,
  runtimeStatus,
  sessions,
  tasks,
  scheduledTasks,
  mcpServers,
  skills,
  onOpenNewSession,
  onOpenSession,
  onOpenScheduled,
  onOpenMcp,
  onOpenSettings,
}: WorkbenchOverviewPageProps) {
  const activeTasks = tasks.filter((task) => ACTIVE_TASK_STATUSES.has(task.status));
  const pendingApprovals = tasks.filter((task) => task.status === "waiting_approval");
  const enabledMcpServers = mcpServers.filter((server) => server.enabled);
  const recentSessions = [...sessions].sort((left, right) => right.updatedAt - left.updatedAt).slice(0, 5);
  const recentTasks = [...tasks].sort((left, right) => right.updatedAt - left.updatedAt).slice(0, 5);
  const nextScheduled = [...scheduledTasks]
    .filter((task) => task.enabled)
    .sort((left, right) => (left.nextRunAt ?? Number.MAX_SAFE_INTEGER) - (right.nextRunAt ?? Number.MAX_SAFE_INTEGER))[0];

  return (
    <main className="overview-page" aria-labelledby="overview-title">
      <section className="overview-command-strip">
        <div>
          <p className="yb-kicker">Runtime Overview</p>
          <h1 id="overview-title">Workbench Overview</h1>
          <p>
            Workspace context, runtime health, MCP capacity, approval queues, and active agent work in one view.
          </p>
        </div>
        <div className="overview-command-actions">
          <Button variant="primary" onClick={onOpenNewSession}>New session</Button>
          <Button variant="secondary" onClick={onOpenMcp}>MCP center</Button>
          <Button variant="ghost" onClick={onOpenSettings}>Settings</Button>
        </div>
      </section>

      <section className="overview-workbench">
        <div className="overview-main-lane">
          <section className="overview-metrics" aria-label="Runtime metrics">
            <Panel eyebrow="Runtime" title={runtimeStatus} action={<StatusBadge label={runtimeStatus} tone={statusTone(runtimeStatus)} pulse={runtimeStatus === "ready"} />}>
              <strong>{providerLabel}</strong>
              <small>Active provider profile</small>
            </Panel>
            <Panel eyebrow="Sessions" title={String(sessions.length)}>
              <strong>{activeTasks.length} active tasks</strong>
              <small>{pendingApprovals.length} waiting approval</small>
            </Panel>
            <Panel eyebrow="MCP" title={`${enabledMcpServers.length}/${mcpServers.length}`}>
              <strong>Enabled servers</strong>
              <small>{skills.length} skill presets loaded</small>
            </Panel>
          </section>

          <Panel eyebrow="Current Workspace" title={workspace?.name ?? "No workspace opened"} action={<Button size="sm" variant="ghost" onClick={onOpenSettings}>Focus</Button>}>
            <div className="overview-workspace-card">
              <dl className="overview-definition-list">
                <div>
                  <dt>Root</dt>
                  <dd>{workspace?.rootPath ?? workspacePath}</dd>
                </div>
                <div>
                  <dt>Memory</dt>
                  <dd>{workspace?.summary ? "available" : "empty"}</dd>
                </div>
              </dl>
              <p>{workspace?.focus ?? "Open or focus a workspace to enrich future tasks."}</p>
            </div>
          </Panel>

          <div className="overview-flow-grid">
            <Panel eyebrow="Recent Sessions" title="Conversation lanes">
              <div className="overview-list">
                {recentSessions.length ? (
                  recentSessions.map((session) => (
                    <button key={session.id} type="button" className="overview-row" onClick={() => onOpenSession(session)}>
                      <span>
                        <strong>{session.title || "Untitled Session"}</strong>
                        <small>{session.summary || session.status}</small>
                      </span>
                      <StatusBadge label={session.status} tone={session.status === "active" ? "success" : "neutral"} compact />
                    </button>
                  ))
                ) : (
                  <div className="overview-empty">
                    <strong>No sessions yet</strong>
                    <small>Create a new session to start the workbench loop.</small>
                  </div>
                )}
              </div>
            </Panel>

            <Panel eyebrow="Task Flow" title="Execution timeline">
              <div className="overview-timeline">
                {recentTasks.length ? (
                  recentTasks.map((task) => (
                    <article key={task.id} className="overview-timeline-item">
                      <span aria-hidden="true" />
                      <div>
                        <strong>{task.goal}</strong>
                        <small>{task.currentStep || task.summary || task.resultSummary || "No summary yet"}</small>
                      </div>
                      <StatusBadge label={task.status} tone={taskTone(task.status)} compact />
                    </article>
                  ))
                ) : (
                  <div className="overview-empty">
                    <strong>No tasks recorded</strong>
                    <small>Task telemetry will appear here after a message is sent.</small>
                  </div>
                )}
              </div>
            </Panel>
          </div>
        </div>

        <aside className="overview-intelligence" aria-label="Runtime intelligence">
          <Panel eyebrow="Runtime Signals" title="Intelligence">
            <div className="overview-signal-stack">
              <div className="overview-signal-card">
                <span>Provider</span>
                <strong>{providerLabel}</strong>
                <StatusBadge label={runtimeStatus} tone={statusTone(runtimeStatus)} compact pulse={runtimeStatus === "ready"} />
              </div>
              <div className="overview-signal-card">
                <span>Approval queue</span>
                <strong>{pendingApprovals.length ? `${pendingApprovals.length} pending` : "clear"}</strong>
                <small>{activeTasks.length} active task{activeTasks.length === 1 ? "" : "s"}</small>
              </div>
              <div className="overview-signal-card">
                <span>Context budget</span>
                <strong>{sessions.length ? "session driven" : "standby"}</strong>
                <small>Budget details appear once a session is active.</small>
              </div>
              <div className="overview-signal-card">
                <span>Schedule</span>
                <strong>{scheduleLabel(nextScheduled)}</strong>
                <small>Next: {formatTimestamp(nextScheduled?.nextRunAt)}</small>
              </div>
            </div>
          </Panel>

          <Panel eyebrow="Capability" title="MCP & Skills" action={<Button size="sm" variant="ghost" onClick={onOpenMcp}>Manage</Button>}>
            <div className="overview-capability-grid">
              {mcpServers.slice(0, 4).map((server) => (
                <div key={server.id} className="overview-capability-card">
                  <StatusBadge label={server.enabled ? "enabled" : "disabled"} tone={server.enabled ? "success" : "neutral"} compact />
                  <strong>{server.name}</strong>
                  <small>{server.transport}</small>
                </div>
              ))}
              {!mcpServers.length ? (
                <div className="overview-empty">
                  <strong>No MCP servers</strong>
                  <small>Add a server in MCP Center.</small>
                </div>
              ) : null}
            </div>
          </Panel>
        </aside>
      </section>
    </main>
  );
}
