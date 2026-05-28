import { Fragment, memo } from "react";
import { StatusBadge } from "../../../v2/components/ui";
import { formatStatusLabel } from "../../../copy";
import type {
  SessionWorkspaceActiveTask,
  SessionWorkspaceBackgroundJob,
  SessionWorkspaceComposerContext,
  SessionWorkspacePatch,
  SessionWorkspaceWorktreeDiff,
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
  patchId?: string | null;
};

type DigestDiffEntry = {
  id: string;
  path: string;
  additions?: number;
  deletions?: number;
  diff: string;
};

type ChangeTotals = {
  additions?: number;
  deletions?: number;
};

const LOW_SIGNAL_TASK_STEP_PATTERNS = [
  /理解任务目标/,
  /分析任务目标/,
  /整理上下文/,
  /构建上下文/,
  /准备上下文/,
  /准备工具/,
  /规划任务/,
  /任务启动/,
  /等待模型/,
  /思考中/,
  /understand(?:ing)? (?:the )?task/i,
  /analy[sz](?:e|ing) (?:the )?task/i,
  /build(?:ing)? context/i,
  /prepar(?:e|ing) context/i,
  /prepar(?:e|ing) (?:the )?first tool/i,
  /plan(?:ning)? (?:the )?task/i,
  /waiting for (?:the )?model/i,
];

function readRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" ? (value as Record<string, unknown>) : null;
}

function readText(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function normalizeDigestPath(path: string) {
  return path.replace(/\\/g, "/").trim();
}

function isDiffMetadataPath(path?: string | null) {
  if (!path) {
    return true;
  }
  const normalized = normalizeDigestPath(path);
  if (!normalized) {
    return true;
  }
  return /^(?:---+|\+\+\+)\s+/.test(normalized) || /^diff --git\s+/.test(normalized) || /^@@\s/.test(normalized);
}

function cleanDiffPath(path: string) {
  const normalized = normalizeDigestPath(path)
    .replace(/^(?:---+|\+\+\+)\s+/, "")
    .replace(/^[ab]\//, "")
    .trim();
  return normalized === "/dev/null" ? "" : normalized;
}

function isUsableDigestPath(path?: string | null) {
  const cleaned = path ? cleanDiffPath(path) : "";
  if (!cleaned || isDiffMetadataPath(path)) {
    return false;
  }
  if (/^(?:workspace diff|\d+\s+files?\s+changed)/i.test(cleaned)) {
    return false;
  }
  if (/^(?:update|updated|create|created|delete|deleted|modify|modified)\s+/i.test(cleaned)) {
    return false;
  }
  return true;
}

function parseDiffEntriesFromText(idPrefix: string, diff: string, fallbackPath: string, totals?: ChangeTotals) {
  const entries: DigestDiffEntry[] = [];
  const lines = diff.replace(/\r\n/g, "\n").split("\n");
  let current: { oldPath?: string; newPath?: string; startIndex: number } | null = null;

  const flush = (endIndex: number) => {
    if (!current) {
      return;
    }
    const path = cleanDiffPath(current.newPath || current.oldPath || fallbackPath || "workspace diff");
    if (path && path !== "/dev/null") {
      entries.push({
        id: `${idPrefix}:${entries.length}:${path}`,
        path,
        additions: entries.length === 0 ? totals?.additions : undefined,
        deletions: entries.length === 0 ? totals?.deletions : undefined,
        diff: lines.slice(current.startIndex, endIndex).join("\n"),
      });
    }
    current = null;
  };

  lines.forEach((line, index) => {
    const diffGitMatch = line.match(/^diff --git a\/(.+?) b\/(.+)$/);
    if (diffGitMatch) {
      flush(index);
      current = { oldPath: diffGitMatch[1], newPath: diffGitMatch[2], startIndex: index };
      return;
    }
    if (line.startsWith("--- ") && current) {
      current.oldPath = line.slice(4).trim();
      return;
    }
    if (line.startsWith("+++ ") && current) {
      current.newPath = line.slice(4).trim();
    }
  });
  flush(lines.length);

  if (entries.length) {
    return entries;
  }

  return [
    {
      id: idPrefix,
      path: isUsableDigestPath(fallbackPath) ? cleanDiffPath(fallbackPath) : "workspace diff",
      additions: totals?.additions,
      deletions: totals?.deletions,
      diff,
    },
  ];
}

function isDiffPreviewMetadataLine(line: string) {
  return (
    /^diff --git\s+/.test(line) ||
    /^(?:---+|\+\+\+)\s+/.test(line) ||
    /^index\s+/.test(line) ||
    /^(?:new|deleted) file mode\s+/.test(line) ||
    /^similarity index\s+/.test(line) ||
    /^rename (?:from|to)\s+/.test(line)
  );
}

function normalizeTaskStep(value?: string | null) {
  return compactText(value ?? "", 96).replace(/[。.!！…]+$/g, "").trim();
}

function isLowSignalTaskStep(value?: string | null) {
  const normalized = normalizeTaskStep(value);
  if (!normalized) {
    return true;
  }
  return LOW_SIGNAL_TASK_STEP_PATTERNS.some((pattern) => pattern.test(normalized));
}

function buildDigestFiles(activeTask?: SessionWorkspaceActiveTask | null, patches?: SessionWorkspacePatch[]) {
  const seen = new Set<string>();
  const files: DigestFileRow[] = [];

  const pushFile = (file: DigestFileRow) => {
    if (!isUsableDigestPath(file.path)) {
      return false;
    }
    const normalizedPath = cleanDiffPath(file.path);
    const key = normalizedPath.toLowerCase();
    if (!key || seen.has(key)) {
      return false;
    }
    seen.add(key);
    files.push({ ...file, path: normalizedPath });
    return true;
  };

  for (const file of activeTask?.changedFiles ?? []) {
    pushFile({
      path: file.path,
      status: file.status,
      additions: file.additions,
      deletions: file.deletions,
      reason: file.reason,
      patchId: file.patchId,
    });
  }

  for (const patch of patches ?? []) {
    let pushedPatchFile = false;
    for (const file of patch.files ?? []) {
      pushedPatchFile = pushFile({
        path: file.path,
        status: file.status,
        additions: file.additions,
        deletions: file.deletions,
        reason: undefined,
        patchId: patch.id,
      }) || pushedPatchFile;
    }
    if (!pushedPatchFile && patch.diff?.trim()) {
      for (const entry of parseDiffEntriesFromText(patch.id, patch.diff, patch.summary || "")) {
        pushFile({
          path: entry.path,
          status: "modified",
          additions: entry.additions,
          deletions: entry.deletions,
          patchId: patch.id,
        });
      }
    }
  }

  return files;
}

function buildDigestDiffEntries(patches?: SessionWorkspacePatch[], worktreeDiff?: SessionWorkspaceWorktreeDiff | null) {
  const entries: DigestDiffEntry[] = [];
  for (const patch of patches ?? []) {
    for (const file of patch.files ?? []) {
      if (!file.diff?.trim()) {
        continue;
      }
      if (!isUsableDigestPath(file.path)) {
        continue;
      }
      entries.push({
        id: `${patch.id}:${file.path}`,
        path: cleanDiffPath(file.path),
        additions: file.additions,
        deletions: file.deletions,
        diff: file.diff,
      });
    }
    if (!patch.files?.some((file) => file.diff?.trim()) && patch.diff?.trim()) {
      entries.push(...parseDiffEntriesFromText(patch.id, patch.diff, patch.summary || "workspace diff", {
        additions: patch.additions,
        deletions: patch.deletions,
      }));
    }
  }
  const worktreeDiffText = readText(worktreeDiff?.diff) || readText(worktreeDiff?.preview);
  if (worktreeDiffText && !entries.some((entry) => entry.diff === worktreeDiffText)) {
    entries.push(...parseDiffEntriesFromText("worktree-diff", worktreeDiffText, worktreeDiff?.diffStat || "workspace diff"));
  }
  return entries;
}

function previewDigestDiff(diff: string, maxLines = 80) {
  const rawLines = diff.replace(/\r\n/g, "\n").split("\n");
  const lines = rawLines.filter((line) => !isDiffPreviewMetadataLine(line));
  return {
    lines: lines.slice(0, maxLines),
    truncated: rawLines.length > maxLines || lines.length > maxLines,
  };
}

function sumDefined(values: Array<number | undefined>) {
  const known = values.filter((value): value is number => typeof value === "number");
  return known.length ? known.reduce((total, value) => total + value, 0) : undefined;
}

function parseDiffStatTotals(diffStat?: string | null): ChangeTotals {
  if (!diffStat) {
    return {};
  }
  const insertions = diffStat.match(/(\d+)\s+insertion/i);
  const deletions = diffStat.match(/(\d+)\s+deletion/i);
  const compact = diffStat.match(/\|\s*\d+\s+([+\-]+)/);
  return {
    additions: insertions ? Number(insertions[1]) : compact ? (compact[1].match(/\+/g) ?? []).length : undefined,
    deletions: deletions ? Number(deletions[1]) : compact ? (compact[1].match(/-/g) ?? []).length : undefined,
  };
}

function buildChangeTotals(files: DigestFileRow[], diffEntries: DigestDiffEntry[], worktreeDiffStat?: string | null) {
  const fromDiffStat = parseDiffStatTotals(worktreeDiffStat);
  if (fromDiffStat.additions !== undefined || fromDiffStat.deletions !== undefined) {
    return fromDiffStat;
  }
  const fromFiles = {
    additions: sumDefined(files.map((file) => file.additions)),
    deletions: sumDefined(files.map((file) => file.deletions)),
  };
  if (fromFiles.additions !== undefined || fromFiles.deletions !== undefined) {
    return fromFiles;
  }
  const fromDiffEntries = {
    additions: sumDefined(diffEntries.map((entry) => entry.additions)),
    deletions: sumDefined(diffEntries.map((entry) => entry.deletions)),
  };
  if (fromDiffEntries.additions !== undefined || fromDiffEntries.deletions !== undefined) {
    return fromDiffEntries;
  }
  return {};
}

function formatDigestFileStatus(status?: string) {
  if (!status) {
    return null;
  }
  const normalized = status.toLowerCase();
  if (["added", "created", "new"].includes(normalized)) {
    return "新增";
  }
  if (["modified", "changed", "updated"].includes(normalized)) {
    return "修改";
  }
  if (["deleted", "removed"].includes(normalized)) {
    return "删除";
  }
  if (["renamed", "moved"].includes(normalized)) {
    return "重命名";
  }
  return status;
}

function lineTone(line: string) {
  if (line.startsWith("+") && !line.startsWith("+++")) {
    return "add";
  }
  if (line.startsWith("-") && !line.startsWith("---")) {
    return "delete";
  }
  if (line.startsWith("@@")) {
    return "hunk";
  }
  return "context";
}

function buildWorktreeReviewSummary(activeTask?: SessionWorkspaceActiveTask | null, worktreeDiffStat?: string | null) {
  const lastStatus = readRecord(activeTask?.activeWorktree?.lastStatus);
  if (!lastStatus && !worktreeDiffStat) {
    return null;
  }
  const review = readRecord(lastStatus?.review);
  const mergeApproval = readRecord(lastStatus?.mergeApproval);
  const reviewSummary = compactMeta([
    readText(review?.reviewer),
    readText(review?.summary),
    readText(review?.status),
  ]).join(" - ");
  const mergeSummary = compactMeta([
    readText(mergeApproval?.decision),
    readText(mergeApproval?.targetBranch),
    readText(mergeApproval?.verificationStatus),
  ]).join(" - ");
  const parts = compactMeta([
    reviewSummary ? `审查：${reviewSummary}` : null,
    mergeSummary ? `合并：${mergeSummary}` : null,
    worktreeDiffStat ? `Diff：${worktreeDiffStat}` : null,
  ]);
  return parts.length ? parts.join("；") : null;
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
) {
  if (activeTask) {
    const phase = getTaskPhase(activeTask);
    if (phase === "failed") {
      return "发现失败的验证或命令，需要继续处理后再收口。";
    }
    if (phase === "completed") {
      return "任务已完成，下面可以查看变更和验证结果。";
    }
    if (["completed", "failed", "waiting"].includes(phase)) {
      return buildTaskProgressSummary(activeTask);
    }
    const currentStep = normalizeTaskStep(activeTask.currentStep);
    if (currentStep && !isLowSignalTaskStep(currentStep)) {
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
  patches,
  backgroundJobs,
  composerContext,
  worktreeDiff,
  onLoadPatch,
}: {
  activeTask?: SessionWorkspaceActiveTask | null;
  patches?: SessionWorkspacePatch[];
  backgroundJobs?: SessionWorkspaceBackgroundJob[];
  composerContext?: SessionWorkspaceComposerContext;
  worktreeDiff?: SessionWorkspaceWorktreeDiff | null;
  onLoadPatch?: (patchId: string) => void | Promise<void>;
}) {
  const diffEntries = buildDigestDiffEntries(patches, worktreeDiff);
  const files = buildDigestFiles(activeTask, patches);
  const fileRows: DigestFileRow[] = files.length
    ? files
    : diffEntries.map((entry) => ({
        path: entry.path,
        status: "modified",
        additions: entry.additions,
        deletions: entry.deletions,
      }));
  const visibleFiles = fileRows.slice(0, 4);
  const visibleDiffEntries = diffEntries.slice(0, 2);
  const commands = buildDigestCommands(activeTask, backgroundJobs);
  const verifications = buildDigestVerificationRows(activeTask);
  const worktreeDiffStat = readText(worktreeDiff?.diffStat) || readText(activeTask?.activeWorktree?.lastStatus?.diffStat);
  const changeTotals = buildChangeTotals(fileRows, diffEntries, worktreeDiffStat);
  const worktreeReviewSummary = buildWorktreeReviewSummary(activeTask, worktreeDiffStat);
  const hasConcreteWork = Boolean(fileRows.length || commands.length || verifications.length || diffEntries.length || worktreeReviewSummary);
  const hasMeaningfulActiveTask = Boolean(
    activeTask &&
      (hasConcreteWork ||
        ["completed", "failed", "waiting"].includes(getTaskPhase(activeTask)) ||
        !isLowSignalTaskStep(activeTask.currentStep)),
  );

  if (!hasMeaningfulActiveTask && !hasConcreteWork) {
    return null;
  }

  const phase = getTaskPhase(activeTask);
  const summary = buildDigestSummary(activeTask, fileRows.length, commands.length, verifications.length);
  const title = activeTask?.goal || "最近工作";
  const contextBits = compactMeta([
    composerContext?.cwd || activeTask?.activeWorktree?.worktreePath || null,
    composerContext?.branch || activeTask?.activeWorktree?.branchName || null,
    composerContext?.model || null,
    composerContext?.permissionMode ? `审批：${composerContext.permissionMode}` : null,
  ]);
  const activityBits = compactMeta([
    fileRows.length ? `${fileRows.length} 个改动文件` : null,
    commands.length ? `${commands.length} 条最近命令` : null,
    verifications.length ? `${verifications.length} 项验证` : null,
    diffEntries.length ? `${diffEntries.length} 个 diff` : null,
  ]);
  const filePreview = visibleFiles
    .map((file) =>
      compactMeta([
        file.path,
        file.additions !== undefined ? `+${file.additions}` : null,
        file.deletions !== undefined ? `-${file.deletions}` : null,
      ]).join(" "),
    )
    .join("；");
  const patchIdByPath = new Map<string, string>();
  for (const patch of patches ?? []) {
    for (const file of patch.files ?? []) {
      patchIdByPath.set(cleanDiffPath(file.path).toLowerCase(), patch.id);
    }
    if (patch.files?.length === 1) {
      patchIdByPath.set(cleanDiffPath(patch.files[0].path).toLowerCase(), patch.id);
    }
  }

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

      {worktreeReviewSummary ? <p className="conversation-task-digest-review">{worktreeReviewSummary}</p> : null}

      {contextBits.length ? (
        <div className="conversation-task-digest-meta" aria-label="工作区上下文">
          {contextBits.map((item) => (
            <span key={item}>{item}</span>
          ))}
        </div>
      ) : null}

      {hasConcreteWork ? (
        <div className="conversation-task-digest-activity" aria-label="本轮工作概览">
          {activityBits.length ? (
            <strong>
              {activityBits.map((item, index) => (
                <Fragment key={item}>
                  {index ? <span aria-hidden="true"> · </span> : null}
                  <span>{item}</span>
                </Fragment>
              ))}
            </strong>
          ) : null}
          {filePreview ? (
            <small>
              {filePreview}
            </small>
          ) : null}
          {verifications.length ? (
            <div className="conversation-task-digest-checks" aria-label="最近验证">
              <span className="conversation-task-digest-checks-label">验证：</span>
              {verifications.map((item) => (
                <span className="conversation-task-digest-check" key={item.id}>
                  <code>{item.command}</code>
                  {item.status ? <em>{formatStatusLabel(item.status)}</em> : null}
                  {item.summary ? <small>{item.summary}</small> : null}
                </span>
              ))}
            </div>
          ) : null}
        </div>
      ) : null}

      {hasConcreteWork && fileRows.length ? (
        <section className="conversation-turn-changes" aria-label="本轮代码改动">
          <header>
            <div>
              <strong>{fileRows.length ? `已改动 ${fileRows.length} 个文件` : visibleDiffEntries.length ? `已记录 ${diffEntries.length} 个 diff` : "本轮改动"}</strong>
              <span>{worktreeDiffStat || (fileRows.length ? "点击文件可查看差异，右侧继续用于浏览源码。" : "等待可展示的 diff。")}</span>
            </div>
            {(changeTotals.additions !== undefined || changeTotals.deletions !== undefined) ? (
              <p className="conversation-turn-changes-stat" aria-label="改动统计">
                <em data-tone="add">+{changeTotals.additions ?? 0}</em>
                <em data-tone="delete">-{changeTotals.deletions ?? 0}</em>
              </p>
            ) : null}
          </header>

          {visibleFiles.length ? (
            <div className="conversation-turn-file-list" aria-label="改动文件">
              {visibleFiles.map((file) => (
                <div className="conversation-turn-file-row" key={file.path}>
                  <code>{file.path}</code>
                  <span>{compactMeta([
                    formatDigestFileStatus(file.status),
                    file.additions !== undefined ? `+${file.additions}` : null,
                    file.deletions !== undefined ? `-${file.deletions}` : null,
                  ]).join(" ") || "已记录"}</span>
                  {(() => {
                    const patchId = file.patchId || patchIdByPath.get(cleanDiffPath(file.path).toLowerCase());
                    return patchId && onLoadPatch ? (
                      <button
                        type="button"
                        className="conversation-turn-diff-button"
                        aria-label="查看文件差异"
                        onClick={() => {
                          void onLoadPatch(patchId);
                        }}
                      >
                        查看文件差异
                      </button>
                    ) : null;
                  })()}
                </div>
              ))}
              {fileRows.length > visibleFiles.length ? (
                <small>另有 {fileRows.length - visibleFiles.length} 个文件可在右侧文件浏览中打开。</small>
              ) : null}
            </div>
          ) : null}

          {visibleDiffEntries.length ? (
          <div className="conversation-task-digest-diff" aria-label="代码改动 diff">
            <header>
              <strong>Diff 预览</strong>
              <span>
                {diffEntries.length} 个 diff
                {diffEntries.length > visibleDiffEntries.length ? `，另有 ${diffEntries.length - visibleDiffEntries.length} 个未展开` : ""}
              </span>
            </header>
            {visibleDiffEntries.map((entry) => {
              const preview = previewDigestDiff(entry.diff);
              return (
                <details className="conversation-turn-diff" key={entry.id} open={visibleDiffEntries.length === 1}>
                  <summary>
                    <code>{entry.path}</code>
                    <small>{compactMeta([entry.additions !== undefined ? `+${entry.additions}` : null, entry.deletions !== undefined ? `-${entry.deletions}` : null]).join(" ")}</small>
                  </summary>
                  <pre>
                    {preview.lines.map((line, index) => (
                      <span key={`${index}:${line.slice(0, 16)}`} data-tone={lineTone(line)}>
                        {line || " "}
                      </span>
                    ))}
                    {preview.truncated ? <span data-tone="context">...</span> : null}
                  </pre>
                </details>
              );
            })}
          </div>
          ) : null}
        </section>
      ) : null}
    </section>
  );
});
