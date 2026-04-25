import { useState, type FormEvent } from "react";
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
  disabled: "已停用",
  failed: "异常",
  completed: "已完成",
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
  const totalCount = tasks.length;
  const activeCount = tasks.filter((task) => task.status === "active").length;
  const disabledCount = tasks.filter((task) => task.status === "disabled").length;
  const failedCount = tasks.filter((task) => task.status === "failed").length;
  const createDisabled = createBusy || !draft.name.trim() || !draft.prompt.trim();

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

    const payload: ScheduledTaskDraft = {
      name: draft.name.trim(),
      description: draft.description.trim(),
      prompt: draft.prompt.trim(),
      schedule: draft.schedule,
      enabled: draft.enabled,
    };
    await onCreateTask?.(payload);
    closeCreateDialog();
  }

  return (
    <main className="scheduled-workspace" aria-labelledby="scheduled-title">
      <section className="scheduled-overview" aria-label="调度总览">
        <div className="scheduled-heading">
          <div>
            <p className="scheduled-kicker">Scheduled</p>
            <h1 id="scheduled-title">调度任务</h1>
            <p className="scheduled-copy">
              管理本地后台任务的计划、状态和最近执行记录。调度任务只在桌面应用运行时触发。
            </p>
          </div>
          <button
            aria-label="Create scheduled task"
            className="scheduled-create-button"
            onClick={() => setCreateDialogOpen(true)}
            type="button"
          >
            新建任务
          </button>
        </div>

        <div className="scheduled-overview-row">
          <dl className="scheduled-metrics" aria-label="调度任务统计">
            <div>
              <dt>总计</dt>
              <dd>{totalCount}</dd>
            </div>
            <div>
              <dt>运行中</dt>
              <dd>{activeCount}</dd>
            </div>
            <div>
              <dt>已停用</dt>
              <dd>{disabledCount}</dd>
            </div>
            <div>
              <dt>异常</dt>
              <dd>{failedCount}</dd>
            </div>
          </dl>
          <aside className="scheduled-notice" role="note">
            <span aria-hidden="true" />
            <p>关闭桌面应用后，计划任务不会在后台静默执行。需要常驻运行时，请保持应用打开。</p>
          </aside>
        </div>
      </section>

      <section className="scheduled-ledger" aria-label="调度任务与执行日志">
        <section className="scheduled-task-panel" aria-label="任务列表">
          <div className="scheduled-panel-heading">
            <div>
              <p>Tasks</p>
              <h2>任务列表</h2>
            </div>
            <span>{totalCount} 项</span>
          </div>

          {tasks.length === 0 ? (
            <div className="scheduled-empty" role="status">
              <h3>暂无调度任务</h3>
              <p>新建任务后，这里会显示计划时间、启停状态和最近运行结果。</p>
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
                    <span className="scheduled-task-stamp" data-status={task.status}>
                      {statusLabel[task.status]}
                    </span>
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
                      <h3>{task.title}</h3>
                    </button>
                    {task.description ? <p>{task.description}</p> : null}
                  </div>
                  <div className="scheduled-task-meta">
                    <span className="scheduled-meta-cell scheduled-meta-status">
                      <span>状态</span>
                      <strong className="scheduled-status" data-status={task.status}>
                        {statusLabel[task.status]} / {task.status}
                      </strong>
                    </span>
                    <span className="scheduled-meta-cell">
                      <span>下次/周期</span>
                      <strong>{task.scheduleText ?? "未设置计划"}</strong>
                    </span>
                    <span className="scheduled-meta-cell">
                      <span>最近执行</span>
                      <strong>{task.lastRunText ?? "尚未运行"}</strong>
                    </span>
                    <div className="scheduled-task-actions" aria-label={`${task.title} actions`}>
                      <button
                        aria-label={`Run task ${task.title}`}
                        disabled={busyTaskId === task.id}
                        onClick={() => {
                          void onRunTask?.(task.id);
                        }}
                        type="button"
                      >
                        运行
                      </button>
                      <button
                        aria-label={`${task.status === "disabled" ? "Enable" : "Disable"} task ${task.title}`}
                        disabled={busyTaskId === task.id}
                        onClick={() => {
                          void onToggleTask?.(task.id);
                        }}
                        type="button"
                      >
                        {task.status === "disabled" ? "启用" : "停用"}
                      </button>
                      {busyTaskId === task.id ? <span className="scheduled-task-busy">处理中</span> : null}
                    </div>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className="scheduled-log-panel" aria-label="执行日志">
          <div className="scheduled-panel-heading">
            <div>
              <p>Logs</p>
              <h2>执行日志</h2>
            </div>
            <span>{selectedTask?.title ?? "未选择任务"}</span>
          </div>

          {selectedLogs.length === 0 ? (
            <div className="scheduled-log-empty">选择任务后会显示最近执行日志。</div>
          ) : (
            <ol className="scheduled-log-list">
              {selectedLogs.map((log) => (
                <li className="scheduled-log-row" key={log.id}>
                  <div className="scheduled-log-marker" data-result={log.result} aria-hidden="true" />
                  <div className="scheduled-log-entry">
                    <div className="scheduled-log-head">
                      <time>{log.time}</time>
                      <strong data-result={log.result}>{log.result === "completed" ? "完成" : "失败"}</strong>
                    </div>
                    <span>{log.message}</span>
                  </div>
                </li>
              ))}
            </ol>
          )}
        </section>
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
              <h2 id="scheduled-create-title">新建定时任务</h2>
              <button aria-label="关闭" onClick={closeCreateDialog} type="button">
                ×
              </button>
            </div>

            <p className="scheduled-dialog-note">本地任务仅在电脑唤醒时运行。</p>

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
                placeholder="审查昨天的提交并标记可疑之处"
                value={draft.description}
              />
            </label>

            <label className="scheduled-field">
              <span>任务内容</span>
              <textarea
                aria-label="任务内容"
                onChange={(event) => updateDraft("prompt", event.currentTarget.value)}
                placeholder="查看过去 24 小时的提交。总结变更内容，标记有风险的模式或缺失的测试。"
                rows={5}
                value={draft.prompt}
              />
            </label>

            <div className="scheduled-dialog-context">
              <span>访问权限</span>
              <strong>{workspacePath ?? "当前工作区"}</strong>
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

            <p className="scheduled-dialog-summary">
              {draft.schedule === "every 24 hours" ? "每天执行" : `${draft.schedule} 执行`}
            </p>

            <div className="scheduled-dialog-actions">
              <button onClick={closeCreateDialog} type="button">
                取消
              </button>
              <button disabled={createDisabled} type="submit">
                {createBusy ? "创建中" : "创建任务"}
              </button>
            </div>
          </form>
        </div>
      ) : null}
    </main>
  );
}

export default ScheduledWorkspace;
