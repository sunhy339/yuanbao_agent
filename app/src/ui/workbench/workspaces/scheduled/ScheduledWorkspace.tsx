import { useState } from "react";
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

export interface ScheduledWorkspaceProps {
  tasks?: ScheduledTask[];
  logs?: ExecutionLog[];
  logsByTaskId?: Record<string, ExecutionLog[]>;
  selectedTaskId?: string;
  onSelectTask?(taskId: string): void;
  onCreateTask?(): void;
  onRunTask?(taskId: string): void | Promise<void>;
  onToggleTask?(taskId: string): void | Promise<void>;
  busyTaskId?: string | null;
}

const statusLabel: Record<ScheduledTaskStatus, string> = {
  active: "运行中",
  disabled: "已停用",
  failed: "异常",
  completed: "已完成",
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
}: ScheduledWorkspaceProps) {
  const [localSelectedTaskId, setLocalSelectedTaskId] = useState<string | null>(null);
  const resolvedSelectedTaskId = selectedTaskId ?? localSelectedTaskId ?? tasks[0]?.id ?? null;
  const selectedTask = tasks.find((task) => task.id === resolvedSelectedTaskId) ?? tasks[0];
  const selectedLogs = buildLogsForTask(selectedTask, logs, logsByTaskId);
  const totalCount = tasks.length;
  const activeCount = tasks.filter((task) => task.status === "active").length;
  const disabledCount = tasks.filter((task) => task.status === "disabled").length;
  const failedCount = tasks.filter((task) => task.status === "failed").length;

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
            onClick={onCreateTask}
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
    </main>
  );
}

export default ScheduledWorkspace;
