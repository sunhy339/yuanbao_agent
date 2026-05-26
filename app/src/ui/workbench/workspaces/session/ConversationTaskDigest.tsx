import { memo } from "react";
import { StatusBadge } from "../../../v2/components/ui";
import { formatStatusLabel } from "../../../copy";
import type {
  SessionWorkspaceActiveTask,
  SessionWorkspaceBackgroundJob,
  SessionWorkspaceComposerContext,
  SessionWorkspacePatch,
} from "./types";
import {
  compactText,
  compactMeta,
  isBackgroundProbeCommand,
  isSuccessfulRuntimeStatus,
  isVerificationCommand,
  normalizeCommandLabel,
} from "./utils";
import { buildTaskProgressSummary, getTaskPhase, getTaskPhaseLabel, getTaskPhaseTone } from "./taskPhase";

type DigestFileRow = {
  path: string;
  status?: string;
  additions?: number;
  deletions?: number;
  reason?: string;
};

function normalizeDigestPath(path: string) {
  return path.replace(/\\/g, "/").trim();
}

function buildDigestFiles(activeTask?: SessionWorkspaceActiveTask | null, patches?: SessionWorkspacePatch[]) {
  const seen = new Set<string>();
  const files: DigestFileRow[] = [];

  const pushFile = (file: DigestFileRow) => {
    const key = normalizeDigestPath(file.path).toLowerCase();
    if (!key || seen.has(key)) {
      return;
    }
    seen.add(key);
    files.push(file);
  };

  for (const file of activeTask?.changedFiles ?? []) {
    pushFile({
      path: file.path,
      status: file.status,
      additions: file.additions,
      deletions: file.deletions,
      reason: file.reason,
    });
  }

  for (const patch of patches ?? []) {
    for (const file of patch.files ?? []) {
      pushFile({
        path: file.path,
        status: file.status,
        additions: file.additions,
        deletions: file.deletions,
        reason: undefined,
      });
    }
  }

  return files;
}

function buildDigestCommands(
  activeTask?: SessionWorkspaceActiveTask | null,
  backgroundJobs?: SessionWorkspaceBackgroundJob[],
) {
  const commands = [
    ...((activeTask?.commands ?? [])
      .filter((command) => !(isSuccessfulRuntimeStatus(command.status) && isBackgroundProbeCommand(command.command)))
      .map((command) => ({
        id: command.id ?? command.command,
        command: command.command,
        status: command.status,
        summary: command.summary,
        cwd: command.cwd,
        exitCode: command.exitCode,
        durationMs: command.durationMs,
      })) ?? []),
    ...((backgroundJobs ?? []).filter((job) => !(isSuccessfulRuntimeStatus(job.status) && isBackgroundProbeCommand(job.command))).map((job) => ({
      id: job.id,
      command: job.command,
      status: job.status,
      summary: job.summary,
      cwd: job.cwd,
      exitCode: job.exitCode,
      durationMs: job.durationMs,
    })) ?? []),
  ];

  const seen = new Set<string>();
  const dedupedLatestFirst = commands
    .slice()
    .reverse()
    .filter((command) => {
    const key = normalizeCommandLabel(command.command)?.toLowerCase() ?? command.command.toLowerCase();
    if (seen.has(key)) {
      return false;
    }
    seen.add(key);
    return true;
    });

  return dedupedLatestFirst.slice(0, 3);
}

function buildDigestVerificationRows(activeTask?: SessionWorkspaceActiveTask | null) {
  const explicit = (activeTask?.verification ?? [])
    .slice(-3)
    .reverse()
    .map((item, index) => ({
      id: item.id ?? `${item.command ?? "verification"}:${index}`,
      command: item.command ?? item.id ?? "verification",
      status: item.status,
      summary: item.summary,
      exitCode: item.exitCode,
      durationMs: item.durationMs,
    }));

  if (explicit.length) {
    return explicit;
  }

  return (activeTask?.commands ?? [])
    .filter((command) => isVerificationCommand(command.command))
    .slice(-3)
    .reverse()
    .map((command, index) => ({
      id: command.id ?? `${command.command}:${index}`,
      command: command.command,
      status: command.status,
      summary: command.summary,
      exitCode: command.exitCode,
      durationMs: command.durationMs,
    }));
}

function buildDigestSummary(
  activeTask: SessionWorkspaceActiveTask | null | undefined,
  fileCount: number,
  commandCount: number,
  verificationCount: number,
  options: { suppressFinalAnswer?: boolean } = {},
) {
  if (activeTask) {
    const phase = getTaskPhase(activeTask);
    if (phase === "failed") {
      return "发现失败的验证或命令，需要继续处理后再收口。";
    }
    if (activeTask.resultSummary && !options.suppressFinalAnswer) {
      return activeTask.resultSummary;
    }
    if (activeTask.summary && !options.suppressFinalAnswer) {
      return activeTask.summary;
    }
    if (options.suppressFinalAnswer && phase === "completed") {
      return "任务已完成，下面可以查看变更和验证结果。";
    }
    if (["completed", "failed", "waiting"].includes(phase)) {
      return buildTaskProgressSummary(activeTask);
    }
    const currentStep = activeTask.currentStep?.trim();
    if (currentStep) {
      return currentStep;
    }
    if (fileCount || commandCount || verificationCount) {
      const parts = compactMeta([
        fileCount ? `${fileCount} 个文件改动` : null,
        commandCount ? `${commandCount} 条命令` : null,
        verificationCount ? `${verificationCount} 项验证` : null,
      ]);
      if (parts.length) {
        return `正在跟进${parts.join("、")}。`;
      }
    }
    return "任务正在执行。";
  }
  if (fileCount || commandCount || verificationCount) {
    return "下面汇总最近的文件、命令和验证记录。";
  }
  return buildTaskProgressSummary(activeTask);
}

export const ConversationTaskDigest = memo(function ConversationTaskDigest({
  activeTask,
  latestAssistantMessage,
  patches,
  backgroundJobs,
  composerContext,
}: {
  activeTask?: SessionWorkspaceActiveTask | null;
  latestAssistantMessage?: string | null;
  patches?: SessionWorkspacePatch[];
  backgroundJobs?: SessionWorkspaceBackgroundJob[];
  composerContext?: SessionWorkspaceComposerContext;
}) {
  const files = buildDigestFiles(activeTask, patches);
  const visibleFiles = files.slice(0, 4);
  const commands = buildDigestCommands(activeTask, backgroundJobs);
  const verifications = buildDigestVerificationRows(activeTask);
  const hasConcreteWork = Boolean(files.length || commands.length || verifications.length);

  if (!activeTask && !hasConcreteWork) {
    return null;
  }

  const phase = getTaskPhase(activeTask);
  const normalizedSummary = compactText(activeTask?.resultSummary || activeTask?.summary || "", 1000).trim();
  const normalizedAssistant = compactText(latestAssistantMessage || "", 1000).trim();
  const suppressFinalAnswer = Boolean(
    normalizedSummary &&
      normalizedAssistant &&
      (normalizedSummary === normalizedAssistant || normalizedAssistant.includes(normalizedSummary)),
  );
  const summary = buildDigestSummary(activeTask, files.length, commands.length, verifications.length, {
    suppressFinalAnswer,
  });
  const title = activeTask?.goal || "最近工作";
  const contextBits = compactMeta([
    composerContext?.cwd || activeTask?.activeWorktree?.worktreePath || null,
    composerContext?.branch || activeTask?.activeWorktree?.branchName || null,
    composerContext?.model || null,
    composerContext?.permissionMode ? `审批：${composerContext.permissionMode}` : null,
  ]);

  return (
    <section className="conversation-task-digest" aria-label="工作摘要">
      <header className="conversation-task-digest-header">
        <div>
          <p className="session-kicker">工作摘要</p>
          <h2 title={title}>{title}</h2>
        </div>
        <div className="conversation-task-digest-status">
          <StatusBadge label={getTaskPhaseLabel(phase)} tone={getTaskPhaseTone(phase)} pulse={["analyzing", "modifying", "verifying"].includes(phase)} compact />
          {activeTask?.status ? <StatusBadge label={formatStatusLabel(activeTask.status)} tone={getTaskPhaseTone(phase)} compact /> : null}
        </div>
      </header>

      <p className="conversation-task-digest-summary">{summary}</p>

      {contextBits.length ? (
        <div className="conversation-task-digest-meta" aria-label="工作区上下文">
          {contextBits.map((item) => (
            <span key={item}>{item}</span>
          ))}
        </div>
      ) : null}

      {hasConcreteWork ? (
        <div className="conversation-task-digest-grid" aria-label="工作明细">
        <article className="conversation-task-digest-card">
          <span>文件</span>
          <strong>{files.length ? `${files.length} 个改动文件` : "暂无文件改动"}</strong>
          {files.length ? (
            <ul>
              {visibleFiles.map((file) => (
                <li key={normalizeDigestPath(file.path)}>
                  <code>{file.path}</code>
                  <small>
                    {compactMeta([
                      file.status ? formatStatusLabel(file.status) : null,
                      file.additions !== undefined ? `+${file.additions}` : null,
                      file.deletions !== undefined ? `-${file.deletions}` : null,
                      file.reason,
                    ]).join(" · ")}
                  </small>
                </li>
              ))}
              {files.length > visibleFiles.length ? (
                <li>
                  <small className="conversation-task-digest-empty">另有 {files.length - visibleFiles.length} 个文件在右侧文件/审查中查看。</small>
                </li>
              ) : null}
            </ul>
          ) : (
            <small className="conversation-task-digest-empty">这一轮还没有记录到文件改动。</small>
          )}
        </article>

        <article className="conversation-task-digest-card">
          <span>命令</span>
          <strong>{commands.length ? `${commands.length} 条最近命令` : "暂无命令"}</strong>
          {commands.length ? (
            <ul>
              {commands.map((command) => (
                <li key={command.id}>
                  <code>{normalizeCommandLabel(command.command) ?? command.command}</code>
                  <small>
                    {compactMeta([
                      command.status ? formatStatusLabel(command.status) : null,
                      command.summary,
                      command.cwd,
                    ]).join(" · ")}
                  </small>
                </li>
              ))}
            </ul>
          ) : (
            <small className="conversation-task-digest-empty">这一轮还没有记录到命令。</small>
          )}
        </article>

        <article className="conversation-task-digest-card">
          <span>验证</span>
          <strong>{verifications.length ? `${verifications.length} 项验证` : "暂无验证"}</strong>
          {verifications.length ? (
            <ul>
              {verifications.map((verification) => (
                <li key={verification.id}>
                  <code>{normalizeCommandLabel(verification.command) ?? verification.command}</code>
                  <small>
                    {compactMeta([
                      verification.status ? formatStatusLabel(verification.status) : null,
                      verification.summary,
                      verification.exitCode !== undefined && verification.exitCode !== null ? `退出码 ${verification.exitCode}` : null,
                    ]).join(" · ")}
                  </small>
                </li>
              ))}
            </ul>
          ) : (
            <small className="conversation-task-digest-empty">这一轮还没有记录到验证。</small>
          )}
        </article>
        </div>
      ) : null}
    </section>
  );
});
