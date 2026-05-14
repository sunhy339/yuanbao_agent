import { StatusBadge } from "../../../v2/components/ui";
import { formatStatusLabel } from "../../../copy";
import type { SessionWorkspaceCollaboration } from "./types";
import { compactMeta, formatDuration, getStatusTone, isRuntimeInFlight } from "./utils";
import { normalizeSubtaskStatus } from "./taskPhase";

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

  return (
    <section className="agent-collaboration-panel" aria-label="真实 Agent 任务">
      <header>
        <div>
          <p className="session-kicker">Agent 协作</p>
          <h3>真实 Agent 任务</h3>
        </div>
        <StatusBadge
          label={`${childTasks.length} 项`}
          tone={childTasks.some((task) => isRuntimeInFlight(task.status)) ? "info" : "neutral"}
          pulse={childTasks.some((task) => isRuntimeInFlight(task.status))}
          compact
        />
      </header>

      {childTasks.length ? (
        <ol className="agent-task-list" aria-label="Agent child tasks">
          {childTasks.map((task) => (
            <li key={task.id} data-state={normalizeSubtaskStatus(task.status)}>
              <div>
                <strong>{task.title}</strong>
                <small>
                  {compactMeta([
                    task.agentType ? `类型: ${task.agentType}` : null,
                    task.workerName ? `worker: ${task.workerName}` : null,
                    task.summary,
                    task.durationMs != null ? formatDuration(task.durationMs) : null,
                    task.artifactCount != null && task.artifactCount > 0 ? `${task.artifactCount} 产物` : null,
                    task.errorMessage,
                  ]).join(" - ") || task.id}
                </small>
              </div>
              <StatusBadge
                label={formatStatusLabel(task.status)}
                tone={getStatusTone(task.status)}
                pulse={isRuntimeInFlight(task.status)}
                compact
              />
            </li>
          ))}
        </ol>
      ) : (
        <p className="agent-collaboration-empty">尚未检测到运行时创建的真实 agent child task。</p>
      )}

      {workers.length ? (
        <div className="agent-worker-strip" aria-label="Agent workers">
          {workers.slice(0, 4).map((worker) => (
            <article key={worker.id}>
              <strong>{worker.name}</strong>
              <span>
                {compactMeta([
                  worker.mode,
                  worker.claimedTaskId ? `task: ${worker.claimedTaskId}` : null,
                  worker.healthState,
                ]).join(" - ") || worker.id}
              </span>
            </article>
          ))}
        </div>
      ) : null}

      {results.length ? (
        <ul className="agent-result-list" aria-label="Agent task results">
          {results.slice(0, 3).map((result) => (
            <li key={result.id}>
              <strong>{result.title ?? result.taskId ?? result.id}</strong>
              <span>{result.summary ?? formatStatusLabel(result.status)}</span>
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}
