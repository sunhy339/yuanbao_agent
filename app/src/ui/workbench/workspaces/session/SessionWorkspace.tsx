import { useMemo, useState } from "react";
import {
  ContextBudgetBar,
} from "../../../v2/components/runtime";
import { Button, StatusBadge } from "../../../v2/components/ui";
import { formatStatusLabel } from "../../../copy";
import type { SessionWorkspaceProps, RuntimeTimelineItem } from "./types";
import { getStatusTone, isTaskControllable, getRuntimeKindLabel } from "./utils";
import { getTaskPhase, getTaskPhaseLabel, buildTaskProgressSummary, shouldDisplayTaskScaffold, expectsAgentWork } from "./taskPhase";
import { buildRuntimeItems } from "./runtimeItemBuilder";
import { buildConversationActivity, ConversationActivity } from "./ConversationActivity";
import { TaskProgressPanel } from "./TaskProgressPanel";
import { RuntimeCockpitPanel } from "./RuntimeCockpitPanel";
import { AgentCollaborationPanel } from "./AgentCollaborationPanel";
import { TraceFilterBar } from "./TraceFilterBar";
import { WorktreePanel } from "./WorktreePanel";
import "./session.css";

// Re-export types for backward compatibility with external consumers
export type {
  SessionWorkspaceCollaboration,
  SessionWorkspaceBackgroundJob,
  SessionWorkspaceContextPreview,
  SessionWorkspaceWorktreeDiff,
  SessionWorkspaceWorktreeStatus,
  SessionWorkspaceProps,
} from "./types";

export function SessionWorkspace({
  session,
  messages,
  activeTask,
  collaboration,
  contextPreview,
  approvals,
  patches,
  traces,
  toolCalls,
  backgroundJobs,
  onApprove,
  onReject,
  onLoadPatch,
  onCopyPatchPath,
  onCopyRuntimeText,
  onRefreshCommandJob,
  onStopCommandJob,
  onRefreshTask,
  onStopTask,
  onRefreshTrace,
  onRefreshWorktree,
  onLoadWorktreeDiff,
  onMergeWorktree,
  onCleanupWorktree,
  worktreeStatus,
  worktreeDiff,
  taskBusyAction,
  busyId,
  worktreeBusyAction,
  worktreeError,
  messagesLoading,
  taskCount,
  composerContext,
}: SessionWorkspaceProps) {
  if (!session) {
    return (
      <main className="session-workspace session-workspace-empty" aria-labelledby="session-empty-title">
        <section className="session-empty-card">
          <span className="session-empty-rule" aria-hidden="true" />
          <p className="session-kicker">会话工作台</p>
          <h1 id="session-empty-title">打开或创建会话</h1>
          <p>从侧栏选择一个会话，或新建会话后开始对话。</p>
        </section>
      </main>
    );
  }

  const visibleActiveTask = shouldDisplayTaskScaffold(activeTask) ? activeTask : null;
  const runtimeItems = useMemo(
    () =>
      buildRuntimeItems({
        session,
        activeTask: visibleActiveTask,
        contextPreview,
        approvals,
        patches,
        traces,
        toolCalls,
        backgroundJobs,
      }),
    [visibleActiveTask, approvals, backgroundJobs, contextPreview, patches, session, toolCalls, traces],
  );
  const activityItems = useMemo(
    () => buildConversationActivity(messages, runtimeItems),
    [messages, runtimeItems],
  );
  const [traceFilter, setTraceFilter] = useState<{
    taskId: string;
    visibility: "" | "chat" | "panel" | "trace";
    agentType: string;
  }>({ taskId: "", visibility: "", agentType: "" });
  const { pendingApprovals, patchCount, commandCount, diagnosticCount, runtimeLanes, uniqueTaskIds, uniqueAgentTypes } = useMemo(() => {
    const pendingApprovals = approvals?.filter((approval) => approval.status === "pending").length ?? 0;
    const patchCount = (patches?.length ?? 0) || (visibleActiveTask?.changedFiles?.length ?? 0);
    let commandCount = 0;
    let diagnosticCount = 0;
    const commandsAndTools: RuntimeTimelineItem[] = [];
    const patchItems: RuntimeTimelineItem[] = [];
    const traceItems: RuntimeTimelineItem[] = [];

    for (const item of runtimeItems) {
      if (item.kind === "command" || item.kind === "tool") {
        commandsAndTools.push(item);
        if (item.kind === "command") commandCount++;
      } else if (item.kind === "patch" || item.id.startsWith("task-files:")) {
        patchItems.push(item);
      } else {
        traceItems.push(item);
        if (item.kind === "trace") diagnosticCount++;
      }
    }

    const filteredTraceItems = traceItems.filter((item) => {
      if (traceFilter.taskId && item.taskId !== traceFilter.taskId) return false;
      if (traceFilter.visibility && item.visibility !== traceFilter.visibility) return false;
      if (traceFilter.agentType && item.agentType !== traceFilter.agentType) return false;
      return true;
    });

    const runtimeLanes = [
      {
        id: "commands" as const,
        eyebrow: "执行",
        title: "命令通道",
        emptyTitle: "暂无运行中的命令",
        emptyText: "Shell 任务会显示在这里，并提供停止和刷新控制。",
        items: commandsAndTools,
      },
      {
        id: "patches" as const,
        eyebrow: "改动",
        title: "改动队列",
        emptyTitle: "暂无改动",
        emptyText: "生成的差异会先显示在这里，再进入活动流。",
        items: patchItems,
      },
      {
        id: "trace" as const,
        eyebrow: "诊断",
        title: "重要信号",
        emptyTitle: "暂无诊断",
        emptyText: "失败、路由决策和可操作信号会显示在这里。",
        items: filteredTraceItems,
      },
    ];

    const uniqueTaskIds = [...new Set(traceItems.map((i) => i.taskId).filter(Boolean) as string[])];
    const uniqueAgentTypes = [...new Set(traceItems.map((i) => i.agentType).filter(Boolean) as string[])];

    return { pendingApprovals, patchCount, commandCount, diagnosticCount, runtimeLanes, uniqueTaskIds, uniqueAgentTypes };
  }, [visibleActiveTask?.changedFiles?.length, approvals, patches, runtimeItems, traceFilter]);
  const activeTaskPhase = getTaskPhase(visibleActiveTask);
  const contextBudgetStats = contextPreview?.budgetStats;
  const contextUsedTokens =
    contextBudgetStats?.estimatedInputTokens ?? contextBudgetStats?.estimatedTokens ?? contextBudgetStats?.messageTokens;
  const contextMaxTokens = contextBudgetStats?.maxContextTokens;

  return (
    <main className="session-workspace session-workspace-chat-only" aria-label="Session">
      <section className="session-workbench-grid">
        <section className="session-conversation-column">
          <header className="session-chat-header">
            <div className="session-chat-title-block">
              <p className="session-kicker">会话</p>
              <h1 id="session-title">{session.title}</h1>
              <div className="session-chip-row" aria-label="会话上下文">
                <StatusBadge label={formatStatusLabel(session.status ?? "active")} tone={getStatusTone(session.status)} />
                {visibleActiveTask?.status ? <StatusBadge label={formatStatusLabel(visibleActiveTask.status)} tone={getStatusTone(visibleActiveTask.status)} pulse={isTaskControllable(visibleActiveTask.status)} /> : null}
                {taskCount !== undefined ? <span>{taskCount} 个任务</span> : null}
                {composerContext?.model ? <span>{composerContext.model}</span> : null}
                {composerContext?.permissionMode ? <span>审批：{composerContext.permissionMode}</span> : null}
              </div>
            </div>
            <div className="session-chat-actions" aria-label="会话操作">
              <Button
                size="sm"
                variant="secondary"
                loading={taskBusyAction === "refresh"}
                disabled={!visibleActiveTask || !onRefreshTask}
                onClick={() => {
                  void onRefreshTask?.();
                }}
              >
                刷新任务
              </Button>
              <Button
                size="sm"
                variant="secondary"
                disabled={!onRefreshTrace}
                loading={busyId === "trace"}
                onClick={() => {
                  void onRefreshTrace?.();
                }}
              >
                刷新诊断
              </Button>
              <Button
                size="sm"
                variant="danger"
                loading={taskBusyAction === "stop"}
                disabled={!visibleActiveTask || !isTaskControllable(visibleActiveTask.status) || !onStopTask}
                onClick={() => {
                  if (visibleActiveTask) {
                    void onStopTask?.(visibleActiveTask.id);
                  }
                }}
              >
                停止任务
              </Button>
            </div>
          </header>

          <section className="session-console" aria-label="运行时控制台">
            <header className="session-console-heading">
              <div>
                <p className="session-kicker">活动流</p>
                <h2>消息与操作</h2>
              </div>
              <span>{activityItems.length} 个事件</span>
            </header>
            <div className="message-stream message-stream-chat-only" aria-label="会话消息">
              <RuntimeCockpitPanel
                activeTask={visibleActiveTask}
                approvals={approvals}
                patches={patches}
                traces={traces}
                contextPreview={contextPreview}
              />
              <WorktreePanel
                worktree={visibleActiveTask?.activeWorktree}
                status={worktreeStatus}
                diff={worktreeDiff}
                busyAction={worktreeBusyAction}
                error={worktreeError}
                onRefresh={onRefreshWorktree}
                onLoadDiff={onLoadWorktreeDiff}
                onMerge={onMergeWorktree}
                onCleanup={onCleanupWorktree}
              />
              {messagesLoading && activityItems.length === 0 ? (
                <div className="message-stream-loading" aria-label="加载消息">
                  <div className="message-stream-loading-bar" />
                </div>
              ) : activityItems.length === 0 ? (
                <div className="message-stream-empty">
                  <p className="session-kicker">安静线程</p>
                  <h2>还没有消息</h2>
                  <p>从下方输入区发送第一条消息。</p>
                </div>
              ) : (
                <ConversationActivity
                  items={activityItems}
                  messages={messages}
                  activeTask={visibleActiveTask}
                  messagesLoading={messagesLoading}
                  onApprove={onApprove}
                  onReject={onReject}
                  onLoadPatch={onLoadPatch}
                  onCopyPatchPath={onCopyPatchPath}
                  onCopyRuntimeText={onCopyRuntimeText}
                  onRefreshCommandJob={onRefreshCommandJob}
                  onStopCommandJob={onStopCommandJob}
                  busyId={busyId}
                />
              )}
            </div>
          </section>
        </section>

        <aside className="session-runtime-column" aria-label="运行时智能状态">
          <section className="session-runtime-dashboard" aria-label="运行时仪表盘">
            <div>
              <p className="session-kicker">任务状态</p>
              <strong>{getTaskPhaseLabel(activeTaskPhase)}</strong>
              <span>{buildTaskProgressSummary(visibleActiveTask)}</span>
            </div>
            <dl>
              <div>
                <dt>消息</dt>
                <dd>{messages.length}</dd>
              </div>
              <div>
                <dt>命令</dt>
                <dd>{commandCount}</dd>
              </div>
              <div>
                <dt>改动</dt>
                <dd>{patchCount}</dd>
              </div>
              <div>
                <dt>审批</dt>
                <dd>{pendingApprovals}</dd>
              </div>
              <div>
                <dt>信号</dt>
                <dd>{diagnosticCount}</dd>
              </div>
            </dl>
          </section>

          <TaskProgressPanel activeTask={visibleActiveTask} patches={patches} />
          <AgentCollaborationPanel collaboration={collaboration} expectAgentWork={expectsAgentWork(visibleActiveTask)} />

          {typeof contextUsedTokens === "number" && typeof contextMaxTokens === "number" ? (
            <ContextBudgetBar
              usedTokens={contextUsedTokens}
              reservedTokens={contextBudgetStats?.toolSchemaTokens ?? 0}
              maxTokens={contextMaxTokens}
              label="会话上下文"
            />
          ) : null}

          <section className="session-runtime-lanes" aria-label="执行通道">
            {runtimeLanes.map((lane) => (
              <article className="session-runtime-lane" data-lane={lane.id} key={lane.id}>
                <header>
                  <div>
                    <p className="session-kicker">{lane.eyebrow}</p>
                    <h3>{lane.title}</h3>
                  </div>
                  <span>{lane.items.length}</span>
                </header>
                {lane.id === "trace" ? (
                  <TraceFilterBar
                    filter={traceFilter}
                    onChange={setTraceFilter}
                    taskIds={uniqueTaskIds}
                    agentTypes={uniqueAgentTypes}
                  />
                ) : null}
                {lane.items.length > 0 ? (
                  <ul>
                    {lane.items.slice(0, 3).map((item) => (
                      <li key={item.id}>
                        <div>
                          <strong>{getRuntimeKindLabel(item.kind)}事件</strong>
                          <span>{item.meta?.slice(0, 2).join(" - ") || "详情可在活动流中查看"}</span>
                        </div>
                        {item.status ? <StatusBadge label={formatStatusLabel(item.status)} tone={getStatusTone(item.status)} compact /> : null}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <div className="session-runtime-lane-empty">
                    <strong>{lane.emptyTitle}</strong>
                    <span>{lane.emptyText}</span>
                  </div>
                )}
              </article>
            ))}
          </section>
        </aside>
      </section>
    </main>
  );
}

export default SessionWorkspace;
