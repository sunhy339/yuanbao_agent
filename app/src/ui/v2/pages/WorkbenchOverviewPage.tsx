import type {
  McpServerRecord,
  ScheduledTaskRecord,
  SessionRecord,
  SkillPresetRecord,
  TaskRecord,
  WorkspaceRef,
} from "@shared";
import { formatStatusLabel } from "../../copy";
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
    return "从未运行";
  }
  return new Date(value).toLocaleString("zh-CN", { hour12: false });
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
    return "暂无启用计划";
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
          <p className="yb-kicker">运行时总览</p>
          <h1 id="overview-title">工作台总览</h1>
          <p>
            集中查看工作区上下文、运行时健康度、MCP 能力、审批队列和正在进行的智能体任务。
          </p>
        </div>
        <div className="overview-command-actions">
          <Button variant="primary" onClick={onOpenNewSession}>新建会话</Button>
          <Button variant="secondary" onClick={onOpenMcp}>MCP 中心</Button>
          <Button variant="ghost" onClick={onOpenSettings}>设置</Button>
        </div>
      </section>

      <section className="overview-workbench">
        <div className="overview-main-lane">
          <section className="overview-metrics" aria-label="运行时指标">
            <Panel eyebrow="运行时" title={formatStatusLabel(runtimeStatus)} action={<StatusBadge label={formatStatusLabel(runtimeStatus)} tone={statusTone(runtimeStatus)} pulse={runtimeStatus === "ready"} />}>
              <strong>{providerLabel}</strong>
              <small>当前模型供应商</small>
            </Panel>
            <Panel eyebrow="会话" title={String(sessions.length)}>
              <strong>{activeTasks.length} 个活跃任务</strong>
              <small>{pendingApprovals.length} 个等待审批</small>
            </Panel>
            <Panel eyebrow="MCP" title={`${enabledMcpServers.length}/${mcpServers.length}`}>
              <strong>已启用服务</strong>
              <small>{skills.length} 个技能预设已加载</small>
            </Panel>
          </section>

          <Panel eyebrow="当前工作区" title={workspace?.name ?? "未打开工作区"} action={<Button size="sm" variant="ghost" onClick={onOpenSettings}>聚焦</Button>}>
            <div className="overview-workspace-card">
              <dl className="overview-definition-list">
                <div>
                  <dt>根目录</dt>
                  <dd>{workspace?.rootPath ?? workspacePath}</dd>
                </div>
                <div>
                  <dt>记忆</dt>
                  <dd>{workspace?.summary ? "可用" : "空"}</dd>
                </div>
              </dl>
              <p>{workspace?.focus ?? "打开或聚焦一个工作区后，后续任务会获得更完整的上下文。"}</p>
            </div>
          </Panel>

          <div className="overview-flow-grid">
            <Panel eyebrow="最近会话" title="对话通道">
              <div className="overview-list">
                {recentSessions.length ? (
                  recentSessions.map((session) => (
                    <button key={session.id} type="button" className="overview-row" onClick={() => onOpenSession(session)}>
                      <span>
                        <strong>{session.title || "未命名会话"}</strong>
                        <small>{session.summary || formatStatusLabel(session.status)}</small>
                      </span>
                      <StatusBadge label={formatStatusLabel(session.status)} tone={session.status === "active" ? "success" : "neutral"} compact />
                    </button>
                  ))
                ) : (
                  <div className="overview-empty">
                    <strong>还没有会话</strong>
                    <small>创建新会话后即可开始工作台流程。</small>
                  </div>
                )}
              </div>
            </Panel>

            <Panel eyebrow="任务流" title="执行时间线">
              <div className="overview-timeline">
                {recentTasks.length ? (
                  recentTasks.map((task) => (
                    <article key={task.id} className="overview-timeline-item">
                      <span aria-hidden="true" />
                      <div>
                        <strong>{task.goal}</strong>
                        <small>{task.currentStep || task.summary || task.resultSummary || "暂无摘要"}</small>
                      </div>
                      <StatusBadge label={formatStatusLabel(task.status)} tone={taskTone(task.status)} compact />
                    </article>
                  ))
                ) : (
                  <div className="overview-empty">
                    <strong>暂无任务记录</strong>
                    <small>发送消息后，任务遥测会显示在这里。</small>
                  </div>
                )}
              </div>
            </Panel>
          </div>
        </div>

        <aside className="overview-intelligence" aria-label="运行时信号">
          <Panel eyebrow="运行时信号" title="智能状态">
            <div className="overview-signal-stack">
              <div className="overview-signal-card">
                <span>模型供应商</span>
                <strong>{providerLabel}</strong>
                <StatusBadge label={formatStatusLabel(runtimeStatus)} tone={statusTone(runtimeStatus)} compact pulse={runtimeStatus === "ready"} />
              </div>
              <div className="overview-signal-card">
                <span>审批队列</span>
                <strong>{pendingApprovals.length ? `${pendingApprovals.length} 个待处理` : "已清空"}</strong>
                <small>{activeTasks.length} 个活跃任务</small>
              </div>
              <div className="overview-signal-card">
                <span>上下文预算</span>
                <strong>{sessions.length ? "会话驱动" : "待命"}</strong>
                <small>会话激活后会显示预算详情。</small>
              </div>
              <div className="overview-signal-card">
                <span>计划</span>
                <strong>{scheduleLabel(nextScheduled)}</strong>
                <small>下次：{formatTimestamp(nextScheduled?.nextRunAt)}</small>
              </div>
            </div>
          </Panel>

          <Panel eyebrow="能力" title="MCP 与技能" action={<Button size="sm" variant="ghost" onClick={onOpenMcp}>管理</Button>}>
            <div className="overview-capability-grid">
              {mcpServers.slice(0, 4).map((server) => (
                <div key={server.id} className="overview-capability-card">
                  <StatusBadge label={server.enabled ? "已启用" : "已停用"} tone={server.enabled ? "success" : "neutral"} compact />
                  <strong>{server.name}</strong>
                  <small>{server.transport}</small>
                </div>
              ))}
              {!mcpServers.length ? (
                <div className="overview-empty">
                  <strong>暂无 MCP 服务</strong>
                  <small>可以在 MCP 中心新增服务。</small>
                </div>
              ) : null}
            </div>
          </Panel>
        </aside>
      </section>
    </main>
  );
}
