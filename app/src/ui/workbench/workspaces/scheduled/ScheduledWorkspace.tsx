import "./scheduled.css";
import { useState } from "react";

export type ScheduledTaskStatus =
  | "active"
  | "disabled"
  | "failed"
  | "completed";

export interface ScheduledTask {
  id: string;
  title: string;
  description?: string;
  status: ScheduledTaskStatus;
  scheduleText?: string;
  lastRunText?: string;
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

export interface ExecutionLog {
  id: string;
  taskId?: string;
  time: string;
  result: "completed" | "failed";
  message: string;
}

const demoTasks: ScheduledTask[] = [
  {
    id: "daily-brief",
    title: "晨间工作简报",
    description: "汇总昨日会话与待办，生成今日开工提示。",
    status: "active",
    scheduleText: "每天 08:30",
    lastRunText: "上次运行：今天 08:30",
  },
  {
    id: "log-prune",
    title: "运行日志整理",
    description: "压缩旧日志并保留最近七天记录。",
    status: "completed",
    scheduleText: "每周一 21:00",
    lastRunText: "上次运行：周一 21:00",
  },
  {
    id: "repo-watch",
    title: "仓库巡检",
    description: "检查关键分支与失败任务，异常时提醒。",
    status: "failed",
    scheduleText: "每 4 小时",
    lastRunText: "上次运行：昨天 18:00",
  },
  {
    id: "archive-disabled",
    title: "归档旧会话",
    description: "暂缓执行，等待父级接入归档策略。",
    status: "disabled",
    scheduleText: "已暂停",
    lastRunText: "上次运行：三天前",
  },
];

const demoLogs: ExecutionLog[] = [
  {
    id: "log-1",
    time: "今天 08:30",
    result: "completed",
    message: "完成：晨间工作简报已生成。",
  },
  {
    id: "log-2",
    time: "昨天 08:30",
    result: "failed",
    message: "失败：桌面应用未保持打开，任务未能启动。",
  },
  {
    id: "log-3",
    time: "前天 08:30",
    result: "completed",
    message: "完成：读取 6 条会话摘要。",
  },
];

const statusLabel: Record<ScheduledTaskStatus, string> = {
  active: "运行中",
  disabled: "已停用",
  failed: "失败",
  completed: "已完成",
};

function buildLogsForTask(
  task: ScheduledTask | undefined,
  isDemoFallback: boolean,
  logs: ExecutionLog[] | undefined,
  logsByTaskId: Record<string, ExecutionLog[]> | undefined,
) {
  if (isDemoFallback) {
    return demoLogs;
  }

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
  tasks,
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
  const isDemoFallback = tasks === undefined;
  const visibleTasks = tasks ?? demoTasks;
  const resolvedSelectedTaskId =
    selectedTaskId ?? localSelectedTaskId ?? visibleTasks[0]?.id ?? null;
  const selectedTask =
    visibleTasks.find((task) => task.id === resolvedSelectedTaskId) ?? visibleTasks[0];
  const selectedLogs = buildLogsForTask(
    selectedTask,
    isDemoFallback,
    logs,
    logsByTaskId,
  );
  const totalCount = visibleTasks.length;
  const activeCount = visibleTasks.filter((task) => task.status === "active").length;
  const disabledCount = visibleTasks.filter(
    (task) => task.status === "disabled",
  ).length;

  return (
    <main className="scheduled-workspace" aria-labelledby="scheduled-title">
      <section className="scheduled-heading">
        <div>
          <p className="scheduled-kicker">Scheduled desk</p>
          <h1 id="scheduled-title">调度任务</h1>
          <p className="scheduled-copy">
            集中查看自动运行的任务、启停状态与最近执行记录。此处不包含输入框，任务创建由父级工作台接入。
          </p>
        </div>
        <button
          aria-label="Create scheduled task"
          className="scheduled-create-button"
          onClick={onCreateTask}
          type="button"
        >
          + 新建任务
        </button>
      </section>

      <aside className="scheduled-notice" role="note">
        <span aria-hidden="true" />
        <p>桌面应用保持打开时，调度任务才能按时执行；关闭应用会暂停本机调度。</p>
      </aside>

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
      </dl>

      <section className="scheduled-ledger" aria-label="调度任务与执行日志">
        <div className="scheduled-task-panel">
          <div className="scheduled-panel-heading">
            <h2>任务列表</h2>
            <span>{totalCount} 项</span>
          </div>

          {visibleTasks.length === 0 ? (
            <div className="scheduled-empty" role="status">
              <h3>暂无调度任务</h3>
              <p>新建任务后，这里会显示计划时间、启停状态与最近运行结果。</p>
            </div>
          ) : (
            <ul className="scheduled-task-list">
              {visibleTasks.map((task) => (
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
                      <h3>{task.title}</h3>
                    </button>
                    {task.description ? <p>{task.description}</p> : null}
                  </div>
                  <div className="scheduled-task-meta">
                    <span
                      className="scheduled-status"
                      data-status={task.status}
                    >
                      {statusLabel[task.status]} · {task.status}
                    </span>
                    <span>{task.scheduleText ?? "未设置计划"}</span>
                    <span>{task.lastRunText ?? "尚未运行"}</span>
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
                        aria-label={`${
                          task.status === "disabled" ? "Enable" : "Disable"
                        } task ${task.title}`}
                        disabled={busyTaskId === task.id}
                        onClick={() => {
                          void onToggleTask?.(task.id);
                        }}
                        type="button"
                      >
                        {task.status === "disabled" ? "启用" : "停用"}
                      </button>
                      {busyTaskId === task.id ? (
                        <span className="scheduled-task-busy">Working</span>
                      ) : null}
                    </div>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="scheduled-log-panel">
          <div className="scheduled-panel-heading">
            <h2>执行日志</h2>
            <span>{selectedTask?.title ?? "未选择任务"}</span>
          </div>

          {selectedLogs.length === 0 ? (
            <div className="scheduled-log-empty">选择任务后会显示最近执行记录。</div>
          ) : (
            <ol className="scheduled-log-list">
              {selectedLogs.map((log) => (
                <li className="scheduled-log-row" key={log.id}>
                  <time>{log.time}</time>
                  <strong data-result={log.result}>
                    {log.result === "completed" ? "完成" : "失败"}
                  </strong>
                  <span>{log.message}</span>
                </li>
              ))}
            </ol>
          )}
        </div>
      </section>
    </main>
  );
}

export default ScheduledWorkspace;
