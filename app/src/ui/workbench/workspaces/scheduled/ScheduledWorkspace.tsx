import { useMemo, useState, type FormEvent } from "react";
import { Button, StatusBadge } from "../../../v2/components/ui";
import "./scheduled.css";

export type ScheduledTaskStatus = "active" | "disabled" | "failed" | "completed";

export interface ScheduledTask {
  id: string;
  title: string;
  description?: string;
  status: ScheduledTaskStatus;
  scheduleText?: string;
  lastRunText?: string;
}

export interface ExecutionLog {
  id: string;
  taskId?: string;
  time: string;
  result: "completed" | "failed";
  message: string;
}

export interface ScheduledTaskDraft {
  name: string;
  description: string;
  prompt: string;
  schedule: string;
  enabled: boolean;
}

export interface ScheduledWorkspaceProps {
  tasks?: ScheduledTask[];
  logs?: ExecutionLog[];
  logsByTaskId?: Record<string, ExecutionLog[]>;
  selectedTaskId?: string;
  onSelectTask?(taskId: string): void;
  onCreateTask?(draft?: ScheduledTaskDraft): void | Promise<void>;
  onRunTask?(taskId: string): void | Promise<void>;
  onToggleTask?(taskId: string): void | Promise<void>;
  busyTaskId?: string | null;
  workspacePath?: string;
  createBusy?: boolean;
}

const statusLabel: Record<ScheduledTaskStatus, string> = {
  active: "Active",
  disabled: "Paused",
  failed: "Failed",
  completed: "Completed",
};

const statusTone: Record<ScheduledTaskStatus | ExecutionLog["result"], "success" | "neutral" | "danger" | "info"> = {
  active: "success",
  disabled: "neutral",
  failed: "danger",
  completed: "info",
};

const defaultDraft: ScheduledTaskDraft = {
  name: "",
  description: "",
  prompt: "",
  schedule: "every 24 hours",
  enabled: true,
};

function buildLogsForTask(
  task: ScheduledTask | undefined,
  logs: ExecutionLog[] | undefined,
  logsByTaskId: Record<string, ExecutionLog[]> | undefined,
) {
  if (!task) {
    return [];
  }

  if (logsByTaskId) {
    return logsByTaskId[task.id] ?? [];
  }

  if (logs) {
    return logs.filter((log) => log.taskId === undefined || log.taskId === task.id);
  }

  return [];
}

export function ScheduledWorkspace({
  tasks = [],
  logs,
  logsByTaskId,
  selectedTaskId,
  onSelectTask,
  onCreateTask,
  onRunTask,
  onToggleTask,
  busyTaskId = null,
  workspacePath,
  createBusy = false,
}: ScheduledWorkspaceProps) {
  const [localSelectedTaskId, setLocalSelectedTaskId] = useState<string | null>(null);
  const [createDialogOpen, setCreateDialogOpen] = useState(false);
  const [draft, setDraft] = useState<ScheduledTaskDraft>(defaultDraft);

  const resolvedSelectedTaskId = selectedTaskId ?? localSelectedTaskId ?? tasks[0]?.id ?? null;
  const selectedTask = tasks.find((task) => task.id === resolvedSelectedTaskId) ?? tasks[0];
  const selectedLogs = buildLogsForTask(selectedTask, logs, logsByTaskId);
  const latestLog = selectedLogs[0];

  const metrics = useMemo(
    () => [
      { label: "Total", value: tasks.length },
      { label: "Active", value: tasks.filter((task) => task.status === "active").length },
      { label: "Paused", value: tasks.filter((task) => task.status === "disabled").length },
      { label: "Failed", value: tasks.filter((task) => task.status === "failed").length },
    ],
    [tasks],
  );

  const activeTask = tasks.find((task) => task.status === "active") ?? selectedTask;
  const createDisabled = createBusy || !draft.name.trim() || !draft.prompt.trim();
  const executionLanes = [
    {
      id: "queue",
      eyebrow: "Queue",
      title: activeTask?.title ?? "No active task",
      meta: activeTask?.scheduleText ?? "Scheduled jobs will wait here.",
      status: activeTask?.status,
    },
    {
      id: "selected",
      eyebrow: "Inspector",
      title: selectedTask?.title ?? "Select a task",
      meta: selectedTask?.description || selectedTask?.lastRunText || "Choose a row to inspect run history.",
      status: selectedTask?.status,
    },
    {
      id: "last-run",
      eyebrow: "Last run",
      title: latestLog?.result ?? "Idle",
      meta: latestLog ? `${latestLog.time} - ${latestLog.message}` : "No execution log for the selected task.",
      status: latestLog?.result,
    },
  ];

  function updateDraft<K extends keyof ScheduledTaskDraft>(key: K, value: ScheduledTaskDraft[K]) {
    setDraft((current) => ({ ...current, [key]: value }));
  }

  function closeCreateDialog() {
    setCreateDialogOpen(false);
    setDraft(defaultDraft);
  }

  async function submitCreateDialog(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (createDisabled) {
      return;
    }

    await onCreateTask?.({
      name: draft.name.trim(),
      description: draft.description.trim(),
      prompt: draft.prompt.trim(),
      schedule: draft.schedule,
      enabled: draft.enabled,
    });
    closeCreateDialog();
  }

  return (
    <main className="scheduled-workspace" aria-labelledby="scheduled-title">
      <section className="scheduled-command-strip" aria-label="Schedule command strip">
        <div className="scheduled-title-block">
          <p className="scheduled-kicker">Automation deck</p>
          <h1 id="scheduled-title">Scheduled Tasks</h1>
          <p>Monitor recurring local agent jobs, inspect recent runs, and create a new scheduled prompt without leaving the workbench.</p>
        </div>
        <div className="scheduled-command-actions">
          <span>{workspacePath ?? "No workspace selected"}</span>
          <Button
            aria-label="Create scheduled task"
            disabled={createBusy}
            loading={createBusy}
            onClick={() => setCreateDialogOpen(true)}
            type="button"
            variant="primary"
          >
            New task
          </Button>
        </div>
      </section>

      <section className="scheduled-metrics" aria-label="Schedule metrics">
        {metrics.map((metric) => (
          <div key={metric.label}>
            <dt>{metric.label}</dt>
            <dd>{metric.value}</dd>
          </div>
        ))}
      </section>

      <section className="scheduled-workbench-grid">
        <section className="scheduled-task-panel" aria-label="Scheduled tasks">
          <div className="scheduled-panel-heading">
            <div>
              <p className="scheduled-kicker">Task ledger</p>
              <h2>Runtime schedule</h2>
            </div>
            <span>{tasks.length} item{tasks.length === 1 ? "" : "s"}</span>
          </div>

          {tasks.length === 0 ? (
            <div className="scheduled-empty" role="status">
              <h3>No scheduled tasks</h3>
              <p>Create a task to run a recurring prompt against this workspace.</p>
            </div>
          ) : (
            <ul className="scheduled-task-list">
              {tasks.map((task) => (
                <li
                  aria-label={`${task.title} ${statusLabel[task.status]}`}
                  className="scheduled-task-item"
                  data-selected={selectedTask?.id === task.id ? "true" : "false"}
                  key={task.id}
                >
                  <div className="scheduled-task-main">
                    <button
                      aria-label={`Select task ${task.title}`}
                      aria-pressed={selectedTask?.id === task.id}
                      className="scheduled-task-select"
                      onClick={() => {
                        setLocalSelectedTaskId(task.id);
                        onSelectTask?.(task.id);
                      }}
                      type="button"
                    >
                      <span className="scheduled-task-index">{task.id.slice(0, 2).toUpperCase()}</span>
                      <span>
                        <strong>{task.title}</strong>
                        {task.description ? <em>{task.description}</em> : null}
                      </span>
                    </button>
                  </div>
                  <div className="scheduled-task-meta">
                    <StatusBadge label={statusLabel[task.status]} tone={statusTone[task.status]} compact />
                    <span>
                      <small>Schedule</small>
                      <strong>{task.scheduleText ?? "Manual"}</strong>
                    </span>
                    <span>
                      <small>Last run</small>
                      <strong>{task.lastRunText ?? "Not run"}</strong>
                    </span>
                    <div className="scheduled-task-actions" aria-label={`${task.title} actions`}>
                      <Button
                        aria-label={`Run task ${task.title}`}
                        disabled={busyTaskId === task.id}
                        loading={busyTaskId === task.id}
                        onClick={() => {
                          void onRunTask?.(task.id);
                        }}
                        size="xs"
                        type="button"
                        variant="secondary"
                      >
                        Run
                      </Button>
                      <Button
                        aria-label={`${task.status === "disabled" ? "Enable" : "Disable"} task ${task.title}`}
                        disabled={busyTaskId === task.id}
                        onClick={() => {
                          void onToggleTask?.(task.id);
                        }}
                        size="xs"
                        type="button"
                        variant={task.status === "disabled" ? "primary" : "secondary"}
                      >
                        {task.status === "disabled" ? "Enable" : "Pause"}
                      </Button>
                      {busyTaskId === task.id ? <span className="scheduled-task-busy">Working</span> : null}
                    </div>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </section>

        <aside className="scheduled-runtime-panel" aria-label="Schedule runtime">
          <section className="scheduled-execution-console" aria-label="Execution lanes">
            {executionLanes.map((lane) => (
              <article className="scheduled-execution-lane" data-lane={lane.id} key={lane.id}>
                <header>
                  <div>
                    <p className="scheduled-kicker">{lane.eyebrow}</p>
                    <h3>{lane.title}</h3>
                  </div>
                  {lane.status ? <StatusBadge label={lane.status} tone={statusTone[lane.status]} compact /> : null}
                </header>
                <span>{lane.meta}</span>
              </article>
            ))}
          </section>

          <section className="scheduled-log-panel" aria-label="Execution log">
            <div className="scheduled-panel-heading">
              <div>
                <p className="scheduled-kicker">Run tail</p>
                <h2>Execution log</h2>
              </div>
              <span>{selectedTask?.title ?? "No task selected"}</span>
            </div>

            {selectedLogs.length === 0 ? (
              <div className="scheduled-log-empty">Select a task to see recent execution logs.</div>
            ) : (
              <ol className="scheduled-log-list">
                {selectedLogs.map((log) => (
                  <li className="scheduled-log-row" key={log.id}>
                    <div className="scheduled-log-marker" data-result={log.result} aria-hidden="true" />
                    <div className="scheduled-log-entry">
                      <div className="scheduled-log-head">
                        <time>{log.time}</time>
                        <strong data-result={log.result}>{log.result}</strong>
                      </div>
                      <span>{log.message}</span>
                    </div>
                  </li>
                ))}
              </ol>
            )}
          </section>
        </aside>
      </section>

      {createDialogOpen ? (
        <div className="scheduled-dialog-backdrop">
          <form
            aria-labelledby="scheduled-create-title"
            className="scheduled-dialog"
            onSubmit={(event) => {
              void submitCreateDialog(event);
            }}
            role="dialog"
          >
            <div className="scheduled-dialog-heading">
              <h2 id="scheduled-create-title">Create scheduled task</h2>
              <button aria-label="Close dialog" onClick={closeCreateDialog} type="button">
                x
              </button>
            </div>

            <p className="scheduled-dialog-note">Local scheduled tasks run when the runtime host is awake.</p>

            <label className="scheduled-field">
              <span>Name</span>
              <input
                aria-label="Name"
                onChange={(event) => updateDraft("name", event.currentTarget.value)}
                placeholder="daily-code-review"
                value={draft.name}
              />
            </label>

            <label className="scheduled-field">
              <span>Description</span>
              <input
                aria-label="Description"
                onChange={(event) => updateDraft("description", event.currentTarget.value)}
                placeholder="Review yesterday's commits"
                value={draft.description}
              />
            </label>

            <label className="scheduled-field">
              <span>Prompt</span>
              <textarea
                aria-label="Prompt"
                onChange={(event) => updateDraft("prompt", event.currentTarget.value)}
                placeholder="Check the repository status and summarize anything that needs attention."
                rows={5}
                value={draft.prompt}
              />
            </label>

            <div className="scheduled-dialog-context">
              <span>Workspace</span>
              <strong>{workspacePath ?? "No workspace selected"}</strong>
            </div>

            <label className="scheduled-field">
              <span>Frequency</span>
              <select
                aria-label="Frequency"
                onChange={(event) => updateDraft("schedule", event.currentTarget.value)}
                value={draft.schedule}
              >
                <option value="every 24 hours">Every day</option>
                <option value="every 12 hours">Every 12 hours</option>
                <option value="every 4 hours">Every 4 hours</option>
                <option value="every 30 minutes">Every 30 minutes</option>
              </select>
            </label>

            <label className="scheduled-checkbox">
              <input
                checked={draft.enabled}
                onChange={(event) => updateDraft("enabled", event.currentTarget.checked)}
                type="checkbox"
              />
              <span>Enable after creation</span>
            </label>

            <p className="scheduled-dialog-summary">{draft.schedule} execution cadence</p>

            <div className="scheduled-dialog-actions">
              <button onClick={closeCreateDialog} type="button">
                Cancel
              </button>
              <button disabled={createDisabled} type="submit">
                {createBusy ? "Creating..." : "Create task"}
              </button>
            </div>
          </form>
        </div>
      ) : null}
    </main>
  );
}

export default ScheduledWorkspace;
