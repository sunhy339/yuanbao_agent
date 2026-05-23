import { Button, StatusBadge } from "../../../v2/components/ui";
import { formatStatusLabel } from "../../../copy";
import type {
  SessionWorkspaceActiveTask,
  SessionWorkspaceComposerContext,
  SessionWorkspacePatch,
  SessionWorkspaceProps,
  SessionWorkspaceWorktree,
  SessionWorkspaceWorktreeDiff,
  SessionWorkspaceWorktreeStatus,
} from "./types";
import { compactMeta, compactText, formatDuration, isSuccessfulRuntimeStatus } from "./utils";

type GitFileRow = {
  path: string;
  status?: string;
  additions?: number;
  deletions?: number;
  reason?: string;
  source: "task" | "patch" | "git";
};

type GitVerificationRow = {
  id: string;
  command: string;
  status?: string;
  summary?: string;
  durationMs?: number | null;
  exitCode?: number | null;
};

function readObject(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : null;
}

function readText(record: Record<string, unknown> | null, key: string): string {
  const value = record?.[key];
  return typeof value === "string" ? value : "";
}

function normalizeWorkspaceRelativePath(path: string) {
  return path
    .replace(/\\/g, "/")
    .replace(/^[MADRCU?! ]{1,2}\s+/, "")
    .replace(/^"(.+)"$/, "$1")
    .trim();
}

function fileNameFromPath(path: string) {
  const normalized = normalizeWorkspaceRelativePath(path);
  return normalized.split("/").filter(Boolean).at(-1) || normalized || ".";
}

function formatBytes(bytes?: number | null) {
  if (bytes === undefined || bytes === null) {
    return null;
  }
  if (bytes < 1024) {
    return `${bytes} B`;
  }
  if (bytes < 1024 * 1024) {
    return `${(bytes / 1024).toFixed(1)} KB`;
  }
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function formatSignedCount(value: number | undefined, prefix: string) {
  return value === undefined ? null : `${prefix}${value}`;
}

function readGitStatusPrefix(rawPath: string) {
  const match = rawPath.match(/^([MADRCU?! ]{1,2})\s+/);
  return match?.[1]?.trim() || undefined;
}

function formatGitStatus(status?: string) {
  const normalized = status?.trim().toUpperCase();
  if (!normalized) return "changed";
  if (normalized === "M") return "modified";
  if (normalized === "A") return "added";
  if (normalized === "D") return "deleted";
  if (normalized === "R") return "renamed";
  if (normalized === "??") return "untracked";
  return normalized.toLowerCase();
}

function collectGitFiles(
  activeTask?: SessionWorkspaceActiveTask | null,
  patches?: SessionWorkspacePatch[],
  status?: SessionWorkspaceWorktreeStatus | null,
) {
  const seen = new Set<string>();
  const rows: GitFileRow[] = [];

  const pushRow = (row: GitFileRow) => {
    const path = normalizeWorkspaceRelativePath(row.path);
    const key = path.toLowerCase();
    if (!key || seen.has(key)) {
      return;
    }
    seen.add(key);
    rows.push({ ...row, path });
  };

  for (const file of activeTask?.changedFiles ?? []) {
    pushRow({
      path: file.path,
      status: file.status,
      additions: file.additions,
      deletions: file.deletions,
      reason: file.reason,
      source: "task",
    });
  }

  for (const patch of patches ?? []) {
    for (const file of patch.files ?? []) {
      pushRow({
        path: file.path,
        status: file.status,
        additions: file.additions,
        deletions: file.deletions,
        source: "patch",
      });
    }
  }

  for (const rawPath of status?.files ?? []) {
    pushRow({
      path: rawPath,
      status: readGitStatusPrefix(rawPath),
      source: "git",
    });
  }

  return rows;
}

function collectVerificationRows(worktree?: SessionWorkspaceWorktree | null, activeTask?: SessionWorkspaceActiveTask | null) {
  const rows: GitVerificationRow[] = [];
  const pushRow = (row: GitVerificationRow) => {
    const key = `${row.command}:${row.status ?? ""}:${row.summary ?? ""}`;
    if (rows.some((existing) => `${existing.command}:${existing.status ?? ""}:${existing.summary ?? ""}` === key)) {
      return;
    }
    rows.push(row);
  };

  for (const [index, item] of (worktree?.lastStatus?.mergeVerification ?? []).entries()) {
    pushRow({
      id: `merge:${item.command ?? index}`,
      command: item.command ?? "merge verification",
      status: item.status,
      summary: item.summary,
      durationMs: item.durationMs,
      exitCode: item.exitCode,
    });
  }

  for (const [index, item] of (activeTask?.verification ?? []).entries()) {
    pushRow({
      id: item.id ?? `task:${item.command ?? index}`,
      command: item.command ?? item.id ?? "verification",
      status: item.status,
      summary: item.summary,
      durationMs: item.durationMs,
      exitCode: item.exitCode,
    });
  }

  return rows;
}

function verificationSummary(rows: GitVerificationRow[]) {
  if (!rows.length) {
    return "等待验证";
  }
  const failed = rows.filter((row) => ["failed", "error", "cancelled"].includes(row.status?.toLowerCase() ?? "")).length;
  if (failed) {
    return `${failed} 项失败`;
  }
  const passed = rows.filter((row) => isSuccessfulRuntimeStatus(row.status)).length;
  if (passed === rows.length) {
    return `${passed} 项通过`;
  }
  return `${rows.length} 项记录`;
}

function diffSummary(diff?: SessionWorkspaceWorktreeDiff | null) {
  if (diff?.error) {
    return diff.error;
  }
  if (diff?.diffStat) {
    return diff.diffStat;
  }
  if (diff?.files?.length) {
    return `${diff.files.length} 个差异文件`;
  }
  if (diff?.diff || diff?.preview) {
    return "diff 已加载";
  }
  return "尚未加载 diff";
}

export function GitWorkspacePanel({
  activeTask,
  patches,
  worktreeStatus,
  worktreeDiff,
  worktreeBusyAction,
  worktreeError,
  composerContext,
  onRefreshWorktree,
  onLoadWorktreeDiff,
  onMergeWorktree,
  onCleanupWorktree,
}: {
  activeTask?: SessionWorkspaceActiveTask | null;
  patches?: SessionWorkspacePatch[];
  worktreeStatus?: SessionWorkspaceWorktreeStatus | null;
  worktreeDiff?: SessionWorkspaceWorktreeDiff | null;
  worktreeBusyAction?: SessionWorkspaceProps["worktreeBusyAction"];
  worktreeError?: SessionWorkspaceProps["worktreeError"];
  composerContext?: SessionWorkspaceComposerContext;
  onRefreshWorktree?: SessionWorkspaceProps["onRefreshWorktree"];
  onLoadWorktreeDiff?: SessionWorkspaceProps["onLoadWorktreeDiff"];
  onMergeWorktree?: SessionWorkspaceProps["onMergeWorktree"];
  onCleanupWorktree?: SessionWorkspaceProps["onCleanupWorktree"];
}) {
  const worktree = activeTask?.activeWorktree;
  const workspacePath = composerContext?.cwd || worktree?.worktreePath || "";
  const branchName = composerContext?.branch || worktree?.branchName || "未识别分支";
  const fileRows = collectGitFiles(activeTask, patches, worktreeStatus);
  const patchStats = (patches ?? []).reduce(
    (stats, patch) => ({
      additions: stats.additions + (patch.additions ?? 0),
      deletions: stats.deletions + (patch.deletions ?? 0),
    }),
    { additions: 0, deletions: 0 },
  );
  const taskStats = (activeTask?.changedFiles ?? []).reduce(
    (stats, file) => ({
      additions: stats.additions + (file.additions ?? 0),
      deletions: stats.deletions + (file.deletions ?? 0),
    }),
    { additions: 0, deletions: 0 },
  );
  const additions = patchStats.additions || taskStats.additions;
  const deletions = patchStats.deletions || taskStats.deletions;
  const dirtyFiles = worktreeStatus?.dirtyFiles ?? fileRows.length;
  const diffLoaded = Boolean(
    worktreeDiff && !worktreeDiff.error && (worktreeDiff.diffStat || worktreeDiff.diff || worktreeDiff.preview || worktreeDiff.files?.length),
  );
  const canRequestMerge = Boolean(worktree && onMergeWorktree && diffLoaded && dirtyFiles === 0 && !worktreeStatus?.error);
  const canCleanup = Boolean(worktree && onCleanupWorktree && dirtyFiles === 0 && !worktreeStatus?.error);
  const verificationRows = collectVerificationRows(worktree, activeTask);
  const review = readObject(worktree?.lastStatus?.review);
  const approval = readObject(worktree?.lastStatus?.mergeApproval);
  const reviewStatus = readText(review, "status") || "未审查";
  const reviewSummary = compactMeta([readText(review, "reviewer"), readText(review, "summary")]).join(" - ");
  const approvalDecision = readText(approval, "decision") || "未申请";
  const approvalSummary = compactMeta([readText(approval, "targetBranch"), readText(approval, "verificationStatus")]).join(" - ");
  const diffMeta = compactMeta([
    worktreeDiff?.truncated ? "预览已截断" : worktreeDiff?.mode === "full" ? "完整 diff" : null,
    formatBytes(worktreeDiff?.bytes ?? worktreeDiff?.previewBytes),
  ]);
  const diffPreview = worktreeDiff?.preview || worktreeDiff?.diff || "";
  const statusSummary = worktreeStatus?.error
    ? worktreeStatus.error
    : dirtyFiles > 0
      ? `${dirtyFiles} 个未提交文件`
      : worktreeStatus
        ? "工作区干净"
        : "等待 Git 扫描";

  return (
    <section className="git-workspace-panel" aria-label="Git 工作区">
      <header className="git-workspace-panel-header">
        <div>
          <p className="session-kicker">Git</p>
          <h3>{branchName}</h3>
          <small title={workspacePath}>{compactText(workspacePath || "当前会话未提供工作目录", 90)}</small>
        </div>
        <StatusBadge
          label={dirtyFiles > 0 ? `${dirtyFiles} 个文件` : "干净"}
          tone={dirtyFiles > 0 ? "warning" : "success"}
          compact
        />
      </header>

      <div className="git-workspace-stats" aria-label="Git 概览">
        <article>
          <span>状态</span>
          <strong>{statusSummary}</strong>
          {worktreeStatus?.files?.length ? <small>来自最近一次 git status</small> : null}
        </article>
        <article>
          <span>改动</span>
          <strong>{additions || deletions ? `+${additions} -${deletions}` : `${fileRows.length} 个文件`}</strong>
          <small>{fileRows.length ? "任务和 Git 扫描合并去重" : "暂无文件记录"}</small>
        </article>
        <article>
          <span>diff</span>
          <strong>{compactText(diffSummary(worktreeDiff), 68)}</strong>
          {diffMeta.length ? <small>{diffMeta.join(" - ")}</small> : null}
        </article>
        <article>
          <span>验证</span>
          <strong>{verificationSummary(verificationRows)}</strong>
          {verificationRows[0]?.command ? <small>{verificationRows[0].command}</small> : null}
        </article>
      </div>

      <div className="git-workspace-actions">
        {worktree && onRefreshWorktree ? (
          <Button
            size="xs"
            variant="secondary"
            loading={worktreeBusyAction === "status"}
            onClick={() => {
              void onRefreshWorktree(worktree.id);
            }}
          >
            刷新状态
          </Button>
        ) : null}
        {worktree && onLoadWorktreeDiff ? (
          <Button
            size="xs"
            variant="secondary"
            loading={worktreeBusyAction === "diff"}
            onClick={() => {
              void onLoadWorktreeDiff(worktree.id);
            }}
          >
            查看差异
          </Button>
        ) : null}
        {worktreeDiff?.truncated && worktree && onLoadWorktreeDiff ? (
          <Button
            size="xs"
            variant="secondary"
            loading={worktreeBusyAction === "diff"}
            onClick={() => {
              void onLoadWorktreeDiff(worktree.id, true);
            }}
          >
            完整 diff
          </Button>
        ) : null}
        {worktree && onMergeWorktree ? (
          <Button
            size="xs"
            variant="secondary"
            loading={worktreeBusyAction === "requestMergeApproval" || worktreeBusyAction === "merge"}
            disabled={!canRequestMerge}
            onClick={() => {
              void onMergeWorktree(worktree.id);
            }}
          >
            合并申请
          </Button>
        ) : null}
        {worktree && onCleanupWorktree ? (
          <Button
            size="xs"
            variant="secondary"
            loading={worktreeBusyAction === "cleanup"}
            disabled={!canCleanup}
            onClick={() => {
              void onCleanupWorktree(worktree.id, false);
            }}
          >
            清理
          </Button>
        ) : null}
      </div>

      {worktreeError || worktreeStatus?.error ? (
        <p className="session-tool-error">{worktreeError || worktreeStatus?.error}</p>
      ) : null}

      <div className="git-workspace-meta-grid">
        <article>
          <span>Worktree</span>
          <strong>{worktree?.branchName || branchName}</strong>
          <small title={worktree?.worktreePath || workspacePath}>{compactText(worktree?.worktreePath || workspacePath, 96)}</small>
          <small>{compactMeta([worktree?.baseRef ? `base ${worktree.baseRef}` : null, worktree?.mergePolicy]).join(" - ")}</small>
        </article>
        <article>
          <span>Review</span>
          <strong>{reviewStatus}</strong>
          {reviewSummary ? <small>{reviewSummary}</small> : <small>等待代码审查结果</small>}
        </article>
        <article>
          <span>Merge</span>
          <strong>{approvalDecision}</strong>
          {approvalSummary ? <small>{approvalSummary}</small> : <small>加载 diff 且工作区干净后可申请</small>}
        </article>
      </div>

      <div className="git-workspace-section">
        <div className="git-workspace-section-head">
          <strong>变更文件</strong>
          <small>{fileRows.length ? `${fileRows.length} 个文件` : "暂无记录"}</small>
        </div>
        {fileRows.length ? (
          <ul className="git-workspace-file-list">
            {fileRows.slice(0, 12).map((file) => (
              <li key={`${file.source}:${file.path}`}>
                <span>{formatGitStatus(file.status)}</span>
                <div>
                  <strong>{fileNameFromPath(file.path)}</strong>
                  <small title={file.path}>{file.path}</small>
                  {file.reason ? <small>{file.reason}</small> : null}
                </div>
                <code>{compactMeta([formatSignedCount(file.additions, "+"), formatSignedCount(file.deletions, "-")]).join(" ")}</code>
              </li>
            ))}
          </ul>
        ) : (
          <p className="session-tool-muted">暂无未提交文件，等待 Git 扫描或代码变更。</p>
        )}
      </div>

      {verificationRows.length ? (
        <div className="git-workspace-section">
          <div className="git-workspace-section-head">
            <strong>验证记录</strong>
            <small>{verificationRows.length} 项</small>
          </div>
          <ul className="git-workspace-verification-list">
            {verificationRows.slice(0, 5).map((item) => (
              <li key={item.id} data-status={item.status ?? "recorded"}>
                <StatusBadge label={formatStatusLabel(item.status ?? "recorded")} tone={isSuccessfulRuntimeStatus(item.status) ? "success" : "neutral"} compact />
                <div>
                  <strong>{item.command}</strong>
                  <small>
                    {compactMeta([
                      item.exitCode !== undefined && item.exitCode !== null ? `退出码 ${item.exitCode}` : null,
                      formatDuration(item.durationMs ?? undefined),
                      item.summary,
                    ]).join(" - ")}
                  </small>
                </div>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {diffPreview ? (
        <div className="git-workspace-section">
          <div className="git-workspace-section-head">
            <strong>Diff 预览</strong>
            <small>{worktreeDiff?.truncated ? "已截断" : "已加载"}</small>
          </div>
          <pre className="session-tool-code-preview">{diffPreview.slice(0, 5000)}</pre>
        </div>
      ) : null}
    </section>
  );
}
