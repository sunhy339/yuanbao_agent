import { StatusBadge } from "../../../v2/components/ui";
import { formatStatusLabel } from "../../../copy";
import type { SessionWorkspaceActiveTask, SessionWorkspacePatch } from "./types";
import { compactMeta } from "./utils";
import {
  getTaskPhase,
  getTaskPhaseLabel,
  getTaskPhaseTone,
  getTaskPhaseIndex,
  buildTaskProgressSummary,
  TASK_PHASES,
} from "./taskPhase";

export function TaskProgressPanel({
  activeTask,
  patches,
}: {
  activeTask?: SessionWorkspaceActiveTask | null;
  patches?: SessionWorkspacePatch[];
}) {
  const phase = getTaskPhase(activeTask);
  const currentIndex = getTaskPhaseIndex(phase);
  const changedFiles = activeTask?.changedFiles ?? [];
  const commands = activeTask?.commands ?? [];
  const verification = activeTask?.verification ?? [];
  const patchFiles = patches?.flatMap((patch) => patch.files ?? []) ?? [];
  const files = changedFiles.length
    ? changedFiles
    : patchFiles.map((file) => ({
        path: file.path,
        status: file.status,
        additions: file.additions,
        deletions: file.deletions,
      }));

  return (
    <section className="task-progress-panel" aria-label="任务进度">
      <header>
        <div>
          <p className="session-kicker">任务进度</p>
          <h3>{getTaskPhaseLabel(phase)}</h3>
        </div>
        <StatusBadge
          label={getTaskPhaseLabel(phase)}
          tone={getTaskPhaseTone(phase)}
          pulse={["analyzing", "modifying", "verifying"].includes(phase)}
          compact
        />
      </header>
      <p>{buildTaskProgressSummary(activeTask)}</p>
      <ol className="task-progress-steps">
        {TASK_PHASES.map((step, index) => {
          const state =
            phase === "failed"
              ? index <= currentIndex
                ? "failed"
                : "pending"
              : index < currentIndex || phase === "completed"
                ? "done"
                : index === currentIndex
                  ? "current"
                  : "pending";
          return (
            <li key={step.id} data-state={state}>
              <span aria-hidden="true" />
              <strong>{step.label}</strong>
            </li>
          );
        })}
      </ol>
      <div className="task-result-grid" aria-label="任务结果">
        <article>
          <span>代码变更</span>
          <strong>{files.length ? `${files.length} 个文件` : "暂无变更"}</strong>
          {files.length ? (
            <ul>
              {files.slice(0, 5).map((file) => (
                <li key={file.path}>
                  <code>{file.path}</code>
                  <small>
                    {compactMeta([
                      file.status ? formatStatusLabel(file.status) : null,
                      file.additions !== undefined ? `+${file.additions}` : null,
                      file.deletions !== undefined ? `-${file.deletions}` : null,
                    ]).join(" ")}
                  </small>
                </li>
              ))}
            </ul>
          ) : null}
        </article>
        <article>
          <span>验证</span>
          <strong>{verification.length ? `${verification.length} 项` : "等待验证"}</strong>
          {verification.length ? (
            <ul>
              {verification.slice(0, 3).map((item) => (
                <li key={item.id ?? item.command ?? item.summary}>
                  <code>{item.command ?? item.id ?? "验证"}</code>
                  <small>{formatStatusLabel(item.status)}</small>
                </li>
              ))}
            </ul>
          ) : null}
        </article>
        <article>
          <span>执行</span>
          <strong>{commands.length ? `${commands.length} 条命令` : "暂无命令"}</strong>
          {commands.length ? (
            <ul>
              {commands.slice(0, 3).map((item) => (
                <li key={item.id ?? item.command}>
                  <code>{item.command}</code>
                  <small>{formatStatusLabel(item.status)}</small>
                </li>
              ))}
            </ul>
          ) : null}
        </article>
      </div>
    </section>
  );
}
