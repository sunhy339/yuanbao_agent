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
  active: "运行中",
  disabled: "已暂停",
  failed: "失败",
  completed: "已完成",
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

const scheduleLabel: Record<string, string> = {
  "every 24 hours": "每天",
  "every 12 hours": "每 12 小时",
  "every 4 hours": "每 4 小时",
  "every 30 minutes": "每 30 分钟",
};

function formatScheduleLabel(value: string) {
  return scheduleLabel[value] ?? value;
}

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
      { label: "总数", value: tasks.length },
      { label: "运行中", value: tasks.filter((task) => task.status === "active").length },
      { label: "已暂停", value: tasks.filter((task) => task.status === "disabled").length },
      { label: "失败", value: tasks.filter((task) => task.status === "failed").length },
    ],
    [tasks],
  );

  const activeTask = tasks.find((task) => task.status === "active") ?? selectedTask;
  const createDisabled = createBusy || !draft.name.trim() || !draft.prompt.trim();
  const executionLanes = [
    {
      id: "queue",
      eyebrow: "队列",
      title: activeTask?.title ?? "暂无运行中任务",
      meta: activeTask?.scheduleText ?? "定时作业会在这里等待运行。",
      status: activeTask?.status,
    },
    {
      id: "selected",
      eyebrow: "检查器",
      title: selectedTask?.title ?? "选择一个任务",
      meta: selectedTask?.description || selectedTask?.lastRunText || "选择一行以检查运行历史。",
      status: selectedTask?.status,
    },
    {
      id: "last-run",
      eyebrow: "上次运行",
      title: latestLog?.result ? statusLabel[latestLog.result] : "空闲",
      meta: latestLog ? `${latestLog.time} - ${latestLog.message}` : "所选任务暂无执行日志。",
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
      <section className="scheduled-command-strip" aria-label="定时任务命令区">
        <div className="scheduled-title-block">
          <p className="scheduled-kicker">自动化面板</p>
          <h1 id="scheduled-title">定时任务</h1>
          <p>监控本地智能体的周期性作业，查看最近运行记录，并在工作台内创建新的定时提示。</p>
        </div>
        <div className="scheduled-command-actions">
          <span>{workspacePath ?? "未选择工作区"}</span>
          <Button
            aria-label="创建定时任务"
            disabled={createBusy}
            loading={createBusy}
            onClick={() => setCreateDialogOpen(true)}
            type="button"
            variant="primary"
          >
            新建任务
          </Button>
        </div>
      </section>

      <section className="scheduled-metrics" aria-label="定时任务指标">
        {metrics.map((metric) => (
          <div key={metric.label}>
            <dt>{metric.label}</dt>
            <dd>{metric.value}</dd>
          </div>
        ))}
      </section>

      <section className="scheduled-workbench-grid">
        <section className="scheduled-task-panel" aria-label="定时任务列表">
          <div className="scheduled-panel-heading">
            <div>
              <p className="scheduled-kicker">任务台账</p>
              <h2>运行时计划</h2>
            </div>
            <span>{tasks.length} 项</span>
          </div>

          {tasks.length === 0 ? (
            <div className="scheduled-empty" role="status">
              <h3>暂无定时任务</h3>
              <p>创建一个任务，让提示词按周期在当前工作区运行。</p>
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
                      aria-label={`选择任务 ${task.title}`}
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
                      <small>计划</small>
                      <strong>{task.scheduleText ?? "手动"}</strong>
                    </span>
                    <span>
                      <small>上次运行</small>
                      <strong>{task.lastRunText ?? "尚未运行"}</strong>
                    </span>
                    <div className="scheduled-task-actions" aria-label={`${task.title} 操作`}>
                      <Button
                        aria-label={`运行任务 ${task.title}`}
                        disabled={busyTaskId === task.id}
                        loading={busyTaskId === task.id}
                        onClick={() => {
                          void onRunTask?.(task.id);
                        }}
                        size="xs"
                        type="button"
                        variant="secondary"
                      >
                        运行
                      </Button>
                      <Button
                        aria-label={`${task.status === "disabled" ? "启用" : "暂停"}任务 ${task.title}`}
                        disabled={busyTaskId === task.id}
                        onClick={() => {
                          void onToggleTask?.(task.id);
                        }}
                        size="xs"
                        type="button"
                        variant={task.status === "disabled" ? "primary" : "secondary"}
                      >
                        {task.status === "disabled" ? "启用" : "暂停"}
                      </Button>
                      {busyTaskId === task.id ? <span className="scheduled-task-busy">处理中</span> : null}
                    </div>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </section>

        <aside className="scheduled-runtime-panel" aria-label="定时任务运行时">
          <section className="scheduled-execution-console" aria-label="执行通道">
            {executionLanes.map((lane) => (
              <article className="scheduled-execution-lane" data-lane={lane.id} key={lane.id}>
                <header>
                  <div>
                    <p className="scheduled-kicker">{lane.eyebrow}</p>
                    <h3>{lane.title}</h3>
                  </div>
                  {lane.status ? <StatusBadge label={statusLabel[lane.status]} tone={statusTone[lane.status]} compact /> : null}
                </header>
                <span>{lane.meta}</span>
              </article>
            ))}
          </section>

          <section className="scheduled-log-panel" aria-label="执行日志">
            <div className="scheduled-panel-heading">
              <div>
                <p className="scheduled-kicker">运行尾部</p>
                <h2>执行日志</h2>
              </div>
              <span>{selectedTask?.title ?? "未选择任务"}</span>
            </div>

            {selectedLogs.length === 0 ? (
              <div className="scheduled-log-empty">选择一个任务以查看最近执行日志。</div>
            ) : (
              <ol className="scheduled-log-list">
                {selectedLogs.map((log) => (
                  <li className="scheduled-log-row" key={log.id}>
                    <div className="scheduled-log-marker" data-result={log.result} aria-hidden="true" />
                    <div className="scheduled-log-entry">
                      <div className="scheduled-log-head">
                        <time>{log.time}</time>
                        <strong data-result={log.result}>{statusLabel[log.result]}</strong>
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
              <h2 id="scheduled-create-title">创建定时任务</h2>
              <button aria-label="关闭对话框" onClick={closeCreateDialog} type="button">
                x
              </button>
            </div>

            <p className="scheduled-dialog-note">本地定时任务会在运行时宿主唤醒时执行。</p>

            <label className="scheduled-field">
              <span>名称</span>
              <input
                aria-label="名称"
                onChange={(event) => updateDraft("name", event.currentTarget.value)}
                placeholder="daily-code-review"
                value={draft.name}
              />
            </label>

            <label className="scheduled-field">
              <span>描述</span>
              <input
                aria-label="描述"
                onChange={(event) => updateDraft("description", event.currentTarget.value)}
                placeholder="复盘昨天的提交"
                value={draft.description}
              />
            </label>

            <label className="scheduled-field">
              <span>提示词</span>
              <textarea
                aria-label="提示词"
                onChange={(event) => updateDraft("prompt", event.currentTarget.value)}
                placeholder="检查仓库状态，并总结需要关注的事项。"
                rows={5}
                value={draft.prompt}
              />
            </label>

            <div className="scheduled-dialog-context">
              <span>工作区</span>
              <strong>{workspacePath ?? "未选择工作区"}</strong>
            </div>

            <label className="scheduled-field">
              <span>频率</span>
              <select
                aria-label="频率"
                onChange={(event) => updateDraft("schedule", event.currentTarget.value)}
                value={draft.schedule}
              >
                <option value="every 24 hours">每天</option>
                <option value="every 12 hours">每 12 小时</option>
                <option value="every 4 hours">每 4 小时</option>
                <option value="every 30 minutes">每 30 分钟</option>
              </select>
            </label>

            <label className="scheduled-checkbox">
              <input
                checked={draft.enabled}
                onChange={(event) => updateDraft("enabled", event.currentTarget.checked)}
                type="checkbox"
              />
              <span>创建后启用</span>
            </label>

            <p className="scheduled-dialog-summary">{formatScheduleLabel(draft.schedule)}执行频率</p>

            <div className="scheduled-dialog-actions">
              <button onClick={closeCreateDialog} type="button">
                取消
              </button>
              <button disabled={createDisabled} type="submit">
                {createBusy ? "创建中..." : "创建任务"}
              </button>
            </div>
          </form>
        </div>
      ) : null}
    </main>
  );
}

export default ScheduledWorkspace;
