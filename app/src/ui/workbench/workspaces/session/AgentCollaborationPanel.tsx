import { StatusBadge } from "../../../v2/components/ui";
import { formatStatusLabel } from "../../../copy";
import type {
  SessionWorkspaceChildTask,
  SessionWorkspaceChildTaskResult,
  SessionWorkspaceCollaboration,
  SessionWorkspaceCollaborator,
} from "./types";
import { compactMeta, compactText, formatDuration, getStatusTone, isRuntimeInFlight } from "./utils";
import { normalizeSubtaskStatus } from "./taskPhase";

type TaskCard = {
  id: string;
  title: string;
  status?: string;
  tone: ReturnType<typeof getStatusTone>;
  pulse: boolean;
  summary?: string;
  footer?: string;
  count?: number;
  collapsedKind?: "planner_scan";
};

const GENERIC_PLANNER_TITLE_RE =
  /\b(inspect|identify|understand|locate|search|scan)\b|\u68c0\u67e5|\u5206\u6790|\u8bc6\u522b|\u5b9a\u4f4d|\u626b\u63cf|\u67e5\u627e/i;

function parseStructuredSummary(value?: string) {
  if (!value) {
    return null;
  }
  try {
    const parsed = JSON.parse(value);
    return parsed && typeof parsed === "object" && !Array.isArray(parsed)
      ? (parsed as Record<string, unknown>)
      : null;
  } catch {
    return null;
  }
}

function getArrayCount(record: Record<string, unknown> | null, key: string) {
  const value = record?.[key];
  return Array.isArray(value) ? value.length : undefined;
}

function summarizeStructuredRecord(record: Record<string, unknown> | null) {
  if (!record) {
    return undefined;
  }
  const changedFiles = getArrayCount(record, "changedFiles");
  const testsRun = getArrayCount(record, "testsRun");
  const risks = getArrayCount(record, "risks");
  const notes = compactMeta([
    changedFiles !== undefined ? (changedFiles > 0 ? `${changedFiles} 个文件改动` : "无文件改动") : null,
    testsRun !== undefined ? (testsRun > 0 ? `${testsRun} 项测试` : "未运行测试") : null,
    risks !== undefined ? (risks > 0 ? `${risks} 个风险` : null) : null,
  ]);
  if (notes.length) {
    return notes.join(" · ");
  }
  const summary = record.summary;
  return typeof summary === "string" && summary.trim() ? compactText(summary, 88) : undefined;
}

function getTaskSummary(task: SessionWorkspaceChildTask) {
  if (task.errorMessage) {
    return compactText(task.errorMessage, 96);
  }
  if (task.attention) {
    return compactText(task.attention, 96);
  }
  const structured = summarizeStructuredRecord(parseStructuredSummary(task.summary));
  if (structured) {
    return structured;
  }
  const summary = task.summary?.trim();
  if (!summary || summary === task.title) {
    return undefined;
  }
  return compactText(summary, 96);
}

function getTaskFooter(task: SessionWorkspaceChildTask) {
  const workerLabel = isGenericPlannerWorker(task.workerName) ? null : task.workerName ? `worker: ${task.workerName}` : null;
  return compactMeta([
    task.agentType ? `类型: ${task.agentType}` : null,
    workerLabel,
    task.durationMs != null ? formatDuration(task.durationMs) : null,
    task.artifactCount != null && task.artifactCount > 0 ? `${task.artifactCount} 产物` : null,
  ]).join(" · ");
}

function isGenericPlannerWorker(workerName?: string) {
  return /planner worker/i.test(workerName ?? "");
}

function isCollapsiblePlannerTask(task: SessionWorkspaceChildTask) {
  const status = String(task.status ?? "").toLowerCase();
  if (!["completed", "passed", "succeeded"].includes(status)) {
    return false;
  }
  if (String(task.agentType ?? "").toLowerCase() !== "planner") {
    return false;
  }
  if (task.errorMessage || task.attention) {
    return false;
  }
  return isGenericPlannerWorker(task.workerName);
}

function isGenericPlannerTask(task: SessionWorkspaceChildTask) {
  return (
    String(task.agentType ?? "").toLowerCase() === "planner" &&
    isGenericPlannerWorker(task.workerName) &&
    GENERIC_PLANNER_TITLE_RE.test(task.title ?? "")
  );
}

function summarizeGroupedPlannerTasks(tasks: SessionWorkspaceChildTask[]) {
  const summaries = tasks
    .map((task) => summarizeStructuredRecord(parseStructuredSummary(task.summary)))
    .filter(Boolean) as string[];
  const uniqueSummaries = [...new Set(summaries)];
  if (!uniqueSummaries.length) {
    return "只完成了范围确认，还没有进入修改或验证。";
  }
  const joined = uniqueSummaries.slice(0, 2).join(" · ");
  if (/无文件改动/.test(joined) && /未运行测试/.test(joined)) {
    return "只完成了范围确认，还没有进入修改或验证。";
  }
  return joined;
}

function groupChildTasks(childTasks: SessionWorkspaceChildTask[]): TaskCard[] {
  const cards: TaskCard[] = [];
  const grouped = new Map<string, SessionWorkspaceChildTask[]>();

  for (const task of childTasks) {
    if (isCollapsiblePlannerTask(task) && isGenericPlannerTask(task)) {
      const key = `planner_scan|${task.agentType}|${String(task.status ?? "").toLowerCase()}`;
      grouped.set(key, [...(grouped.get(key) ?? []), task]);
      continue;
    }
    cards.push({
      id: task.id,
      title: task.title,
      status: task.status,
      tone: getStatusTone(task.status),
      pulse: isRuntimeInFlight(task.status),
      summary: getTaskSummary(task),
      footer: getTaskFooter(task) || undefined,
    });
  }

  grouped.forEach((tasks, key) => {
    const first = tasks[0];
    cards.push({
      id: `group:${key}`,
      title: "已完成范围确认",
      status: first.status,
      tone: getStatusTone(first.status),
      pulse: false,
      summary: summarizeGroupedPlannerTasks(tasks),
      footer: `${tasks.length} 个前置检查已完成`,
      count: tasks.length,
      collapsedKind: "planner_scan",
    });
  });

  return cards.sort((left, right) => {
    const leftFailed = ["failed", "error", "cancelled"].includes(String(left.status ?? "").toLowerCase());
    const rightFailed = ["failed", "error", "cancelled"].includes(String(right.status ?? "").toLowerCase());
    if (leftFailed !== rightFailed) {
      return leftFailed ? -1 : 1;
    }
    const leftRunning = isRuntimeInFlight(left.status);
    const rightRunning = isRuntimeInFlight(right.status);
    if (leftRunning !== rightRunning) {
      return leftRunning ? -1 : 1;
    }
    return 0;
  });
}

function buildTaskTitle(task: TaskCard) {
  if (task.collapsedKind === "planner_scan") {
    return "已完成范围确认";
  }
  return task.title;
}

function buildTaskLabel(task: TaskCard) {
  if (task.collapsedKind === "planner_scan") {
    return task.count && task.count > 1 ? `${task.count} 次扫描` : "扫描";
  }
  return task.status ? formatStatusLabel(task.status) : "已记录";
}

function getVisibleWorkers(workers: SessionWorkspaceCollaborator[]) {
  return workers.filter((worker) => {
    const status = String(worker.status ?? "").toLowerCase();
    const health = String(worker.healthState ?? "").toLowerCase();
    return (
      ["running", "started", "pending", "queued"].includes(status) ||
      Boolean(worker.claimedTaskId) ||
      (health && health !== "healthy")
    );
  });
}

function getResultSummary(result: SessionWorkspaceChildTaskResult) {
  const structured = summarizeStructuredRecord(parseStructuredSummary(result.summary));
  if (structured) {
    return structured;
  }
  const summary = result.summary?.trim();
  if (!summary) {
    return undefined;
  }
  if (summary.startsWith("{") || summary.startsWith("[")) {
    return undefined;
  }
  return compactText(summary, 92);
}

function getVisibleResults(results: SessionWorkspaceChildTaskResult[]) {
  return results
    .map((result) => ({
      ...result,
      displaySummary: getResultSummary(result),
    }))
    .filter((result) => Boolean(result.displaySummary))
    .slice(0, 3);
}

function buildHeaderLabel(childTasks: SessionWorkspaceChildTask[]) {
  const running = childTasks.filter((task) => isRuntimeInFlight(task.status)).length;
  const failed = childTasks.filter((task) =>
    ["failed", "error", "cancelled"].includes(String(task.status ?? "").toLowerCase()),
  ).length;
  if (failed > 0) {
    return `${failed} 个待处理`;
  }
  if (running > 0) {
    return `${running} 个进行中`;
  }
  return `${childTasks.length} 项`;
}

export function AgentCollaborationPanel({
  collaboration,
  expectAgentWork,
}: {
  collaboration?: SessionWorkspaceCollaboration;
  expectAgentWork?: boolean;
}) {
  const workers = collaboration?.workers ?? [];
  const childTasks = collaboration?.childTasks ?? [];
  const results = collaboration?.results ?? [];
  const hasAgentWork = workers.length > 0 || childTasks.length > 0 || results.length > 0;

  if (!hasAgentWork && !expectAgentWork) {
    return null;
  }

  const taskCards = groupChildTasks(childTasks);
  const visibleWorkers = getVisibleWorkers(workers);
  const visibleResults = getVisibleResults(results);
  const onlyCollapsedPlannerScans =
    taskCards.length > 0 &&
    taskCards.every((task) => task.collapsedKind === "planner_scan") &&
    visibleWorkers.length === 0 &&
    visibleResults.length === 0;

  if (onlyCollapsedPlannerScans) {
    return null;
  }

  return (
    <section className="agent-collaboration-panel" aria-label="真实 Agent 任务">
      <header>
        <div>
          <p className="session-kicker">子任务</p>
          <h3>协作摘要</h3>
        </div>
        <StatusBadge
          label={childTasks.length ? buildHeaderLabel(childTasks) : "等待任务"}
          tone={childTasks.some((task) => ["failed", "error", "cancelled"].includes(String(task.status ?? "").toLowerCase()))
            ? "danger"
            : childTasks.some((task) => isRuntimeInFlight(task.status))
              ? "info"
              : "neutral"}
          pulse={childTasks.some((task) => isRuntimeInFlight(task.status))}
          compact
        />
      </header>

      {taskCards.length ? (
        <ol className="agent-task-list" aria-label="Agent child tasks">
              {taskCards.map((task) => (
                <li key={task.id} data-state={normalizeSubtaskStatus(task.status)}>
                  <div>
                <strong>{buildTaskTitle(task)}</strong>
                    {task.summary ? <small>{task.summary}</small> : null}
                    {task.footer ? <small>{task.footer}</small> : null}
                  </div>
                  <div className="agent-task-status">
                {task.count && task.count > 1 ? <span className="agent-task-count">{task.count} 次</span> : null}
                    <StatusBadge
                  label={buildTaskLabel(task)}
                  tone={task.tone}
                  pulse={task.pulse}
                  compact
                    />
                  </div>
            </li>
          ))}
        </ol>
      ) : (
        <p className="agent-collaboration-empty">尚未检测到运行时创建的真实 agent child task。</p>
      )}

      {visibleWorkers.length ? (
        <div className="agent-worker-strip" aria-label="Agent workers">
          {visibleWorkers.slice(0, 3).map((worker) => (
            <article key={worker.id}>
              <strong>{worker.name}</strong>
              <span>
                {compactMeta([
                  worker.mode,
                  worker.claimedTaskId ? `task: ${worker.claimedTaskId}` : null,
                  worker.healthState,
                ]).join(" · ") || worker.id}
              </span>
            </article>
          ))}
        </div>
      ) : null}

      {visibleResults.length ? (
        <ul className="agent-result-list" aria-label="Agent task results">
          {visibleResults.map((result) => (
            <li key={result.id}>
              <strong>{result.title ?? result.taskId ?? result.id}</strong>
              <span>{result.displaySummary}</span>
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}
