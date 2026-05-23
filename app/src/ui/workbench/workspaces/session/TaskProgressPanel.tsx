import { StatusBadge } from "../../../v2/components/ui";
import { formatStatusLabel } from "../../../copy";
import type { SessionWorkspaceActiveTask, SessionWorkspacePatch } from "./types";
import {
  compactMeta,
  isBackgroundProbeCommand,
  isSuccessfulRuntimeStatus,
  isVerificationCommand,
  normalizeCommandLabel,
  normalizeComparableCommand,
} from "./utils";
import { buildTaskProgressSummary, getTaskPhase, getTaskPhaseLabel, getTaskPhaseTone } from "./taskPhase";

function isVerificationLikeCommand(command?: string) {
  return isVerificationCommand(command);
}

function buildRecentCommandRows(commands: NonNullable<SessionWorkspaceActiveTask["commands"]>) {
  const latestByCommand = new Map<string, (typeof commands)[number]>();
  const visibleCommands = commands.filter(
    (command) => !(isSuccessfulRuntimeStatus(command.status) && isBackgroundProbeCommand(command.command)),
  );
  for (const command of visibleCommands) {
    const key = normalizeComparableCommand(command.command) ?? `${command.id ?? ""}:${command.command}`;
    latestByCommand.set(key, command);
  }
  return [...latestByCommand.values()].slice(-3).reverse();
}

function buildDerivedVerificationRows(activeTask?: SessionWorkspaceActiveTask | null) {
  const verification = activeTask?.verification ?? [];
  const commands = activeTask?.commands ?? [];
  const explicitRows = [...verification]
    .sort((left, right) => {
      const leftScore = ["passed", "completed", "succeeded"].includes(left.status) ? 2 : ["failed", "error"].includes(left.status) ? 0 : 1;
      const rightScore = ["passed", "completed", "succeeded"].includes(right.status) ? 2 : ["failed", "error"].includes(right.status) ? 0 : 1;
      return rightScore - leftScore;
    })
    .slice(0, 3)
    .map((item) => ({
      id: item.id ?? item.command ?? item.summary ?? "verification",
      command: normalizeCommandLabel(item.command) ?? item.command ?? item.id ?? "Verification",
      status: item.status,
      summary: item.summary,
    }));

  const seen = new Set(explicitRows.map((item) => normalizeComparableCommand(item.command) ?? item.id));
  const fallbackRows = [...commands]
    .reverse()
    .filter((command) => isVerificationLikeCommand(command.command))
    .filter((command) => {
      const key = normalizeComparableCommand(command.command) ?? command.id ?? command.command;
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    })
    .map((command) => ({
      id: command.id ?? command.command,
      command: normalizeCommandLabel(command.command) ?? command.command,
      status: command.status ?? "recorded",
      summary: command.summary,
    }));

  return [...explicitRows, ...fallbackRows].slice(0, 3);
}

function buildVerificationHeadline(rows: ReturnType<typeof buildDerivedVerificationRows>) {
  if (!rows.length) return "还没有运行验证";
  const passed = rows.filter((item) => ["passed", "completed", "succeeded"].includes(String(item.status ?? "").toLowerCase())).length;
  const failed = rows.filter((item) => ["failed", "error", "cancelled"].includes(String(item.status ?? "").toLowerCase())).length;
  const running = rows.filter((item) => ["running", "started", "pending", "queued", "verifying"].includes(String(item.status ?? "").toLowerCase())).length;
  if (failed > 0) return `${failed} 项验证失败`;
  if (running > 0) return `${running} 项验证进行中`;
  if (passed > 0) return `${passed} 项验证已通过`;
  return "已记录验证结果";
}

function buildCurrentFocus(activeTask: SessionWorkspaceActiveTask | null | undefined, phase: ReturnType<typeof getTaskPhase>) {
  if (phase === "completed") return "本轮任务已完成";
  if (phase === "failed") return "需要继续处理失败项";
  if (phase === "waiting") return "等待继续动作";
  if (activeTask?.currentStep) return activeTask.currentStep;
  const activePlanStep = activeTask?.planSteps?.find((step) => ["active", "running", "started", "pending", "verifying"].includes(String(step.status ?? "").toLowerCase()));
  if (activePlanStep?.title) return activePlanStep.title;
  return activeTask?.goal || buildTaskProgressSummary(activeTask) || "正在处理";
}

function buildExecutionHeadline(commands: ReturnType<typeof buildRecentCommandRows>) {
  if (!commands.length) return "暂无命令";
  const failed = commands.find((item) => ["failed", "error", "cancelled"].includes(String(item.status ?? "").toLowerCase()));
  if (failed) return `${normalizeCommandLabel(failed.command) ?? failed.command} 需要处理`;
  return normalizeCommandLabel(commands[0]?.command) ?? commands[0]?.command ?? "暂无命令";
}

export function TaskProgressPanel({
  activeTask,
  patches,
}: {
  activeTask?: SessionWorkspaceActiveTask | null;
  patches?: SessionWorkspacePatch[];
}) {
  if (!activeTask) return null;

  const phase = getTaskPhase(activeTask);
  const changedFiles = activeTask?.changedFiles ?? [];
  const commands = activeTask?.commands ?? [];
  const recentCommands = buildRecentCommandRows(commands);
  const visibleVerification = buildDerivedVerificationRows(activeTask);
  const patchFiles = patches?.flatMap((patch) => patch.files ?? []) ?? [];
  const files = changedFiles.length
    ? changedFiles
    : patchFiles.map((file) => ({ path: file.path, status: file.status, additions: file.additions, deletions: file.deletions }));

  const summaryRows = [
    { label: "当前步骤", value: buildCurrentFocus(activeTask, phase) },
    visibleVerification.length ? { label: "最近检查", value: buildVerificationHeadline(visibleVerification) } : null,
    files.length ? { label: "代码变更", value: `${files.length} 个文件` } : null,
    recentCommands.length ? { label: "执行状态", value: buildExecutionHeadline(recentCommands) } : null,
  ].filter(Boolean) as Array<{ label: string; value: string }>;

  const resultSections = [
    files.length
      ? {
          label: "代码变更",
          headline: `${files.length} 个文件`,
          rows: files.slice(0, 5).map((file) => ({
            id: file.path,
            code: file.path,
            meta: compactMeta([
              file.status ? formatStatusLabel(file.status) : null,
              file.additions !== undefined ? `+${file.additions}` : null,
              file.deletions !== undefined ? `-${file.deletions}` : null,
            ]).join(" "),
          })),
        }
      : null,
    visibleVerification.length
      ? {
          label: "验证",
          headline: buildVerificationHeadline(visibleVerification),
          rows: visibleVerification.map((item) => ({
            id: item.id,
            code: item.command,
            meta: [formatStatusLabel(item.status), item.summary].filter(Boolean).join(" · "),
          })),
        }
      : null,
    recentCommands.length
      ? {
          label: "执行",
          headline: `${recentCommands.length} 条命令`,
          rows: recentCommands.map((item) => ({
            id: item.id ?? item.command ?? "command",
            code: normalizeCommandLabel(item.command) ?? item.command,
            meta: [formatStatusLabel(item.status), item.summary].filter(Boolean).join(" · "),
          })),
        }
      : null,
  ].filter(Boolean) as Array<{ label: string; headline: string; rows: Array<{ id: string; code: string; meta: string }> }>;

  return (
    <section className="task-progress-panel" aria-label="任务进度">
      <header>
        <div>
          <p className="session-kicker">任务进度</p>
          <h3>{getTaskPhaseLabel(phase)}</h3>
        </div>
        <StatusBadge label={getTaskPhaseLabel(phase)} tone={getTaskPhaseTone(phase)} pulse={["analyzing", "modifying", "verifying"].includes(phase)} compact />
      </header>
      <div className="task-progress-summary" aria-label="任务摘要">
        {summaryRows.map((item) => (
          <div key={item.label} className="task-progress-summary-row">
            <span>{item.label}</span>
            <strong>{item.value}</strong>
          </div>
        ))}
      </div>
      {resultSections.length ? (
        <div className="task-result-grid" aria-label="任务结果">
          {resultSections.map((section) => (
            <article key={section.label}>
              <span>{section.label}</span>
              <strong>{section.headline}</strong>
              <ul>
                {section.rows.map((row) => (
                  <li key={row.id}>
                    <code>{row.code}</code>
                    <small>{row.meta}</small>
                  </li>
                ))}
              </ul>
            </article>
          ))}
        </div>
      ) : null}
    </section>
  );
}
