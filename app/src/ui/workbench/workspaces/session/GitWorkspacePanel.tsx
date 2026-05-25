import { useCallback, useEffect, useMemo, useState } from "react";
import { ChevronDown, GitCommitHorizontal, GitPullRequestCreate, RefreshCw } from "lucide-react";
import type { GitLocalDiffResult, GitLocalStatusResult } from "@shared";
import { RuntimeClient } from "../../../../lib/runtimeClient";
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
import { UnifiedDiffViewer } from "./UnifiedDiffViewer";
import { compactMeta, compactText, formatDuration, isSuccessfulRuntimeStatus } from "./utils";

const gitClient = new RuntimeClient();

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
  if (normalized === "MM") return "modified";
  if (normalized === "A") return "added";
  if (normalized === "D") return "deleted";
  if (normalized === "R") return "renamed";
  if (normalized === "??") return "untracked";
  return normalized.toLowerCase();
}

function statusRowsFromLocalGit(status?: GitLocalStatusResult | null): GitFileRow[] {
  return (status?.files ?? []).map((file) => ({
    path: file.path,
    status: file.status,
    source: "git" as const,
  }));
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
  const [localStatus, setLocalStatus] = useState<GitLocalStatusResult | null>(null);
  const [localDiff, setLocalDiff] = useState<GitLocalDiffResult | null>(null);
  const [gitBusy, setGitBusy] = useState<"status" | "diff" | "init" | "checkout" | "commit" | null>(null);
  const [gitError, setGitError] = useState<string | null>(null);
  const [selectedBranch, setSelectedBranch] = useState("");
  const [commitMessage, setCommitMessage] = useState("");
  const branchName = localStatus?.branch || composerContext?.branch || worktree?.branchName || "未识别分支";
  const fileRows = useMemo(() => {
    const localRows = statusRowsFromLocalGit(localStatus);
    return localRows.length ? collectGitFiles(activeTask, patches, { files: localRows.map((row) => `${row.status ?? "M"} ${row.path}`) }) : collectGitFiles(activeTask, patches, worktreeStatus);
  }, [activeTask, localStatus, patches, worktreeStatus]);
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
  const dirtyFiles = localStatus?.dirtyFiles ?? worktreeStatus?.dirtyFiles ?? fileRows.length;
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
  const diffPreview = localDiff?.diff || worktreeDiff?.preview || worktreeDiff?.diff || "";
  const diffStat = localDiff?.stat || worktreeDiff?.diffStat || "";
  const statusSummary = worktreeStatus?.error
    ? worktreeStatus.error
    : dirtyFiles > 0
      ? `${dirtyFiles} 个未提交文件`
      : localStatus || worktreeStatus
        ? "工作区干净"
        : "等待 Git 扫描";
  const canUseLocalGit = Boolean(workspacePath);
  const branchOptions = localStatus?.branches ?? [];
  const canInitRepo = Boolean(
    workspacePath &&
      !localStatus &&
      (gitError?.toLowerCase().includes("not a git repository") || gitError?.toLowerCase().includes("not a git repo")),
  );

  const refreshLocalStatus = useCallback(async () => {
    if (!workspacePath) {
      return;
    }
    setGitBusy("status");
    setGitError(null);
    try {
      const result = await gitClient.gitLocalStatus({ cwd: workspacePath });
      setLocalStatus(result);
      setSelectedBranch(result.branch);
    } catch (reason) {
      setGitError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setGitBusy(null);
    }
  }, [workspacePath]);

  const loadLocalDiff = useCallback(async (path?: string) => {
    if (!workspacePath) {
      return;
    }
    setGitBusy("diff");
    setGitError(null);
    try {
      setLocalDiff(await gitClient.gitLocalDiff({ cwd: workspacePath, path }));
    } catch (reason) {
      setGitError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setGitBusy(null);
    }
  }, [workspacePath]);

  async function initRepository() {
    if (!workspacePath) {
      return;
    }
    setGitBusy("init");
    setGitError(null);
    try {
      const result = await gitClient.gitLocalInit({ cwd: workspacePath });
      setLocalStatus(result.status ?? null);
      setSelectedBranch(result.status?.branch ?? "");
      setLocalDiff(null);
    } catch (reason) {
      setGitError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setGitBusy(null);
    }
  }

  async function checkoutBranch() {
    if (!workspacePath || !selectedBranch || selectedBranch === branchName) {
      return;
    }
    setGitBusy("checkout");
    setGitError(null);
    try {
      const result = await gitClient.gitLocalCheckout({ cwd: workspacePath, branch: selectedBranch });
      setLocalStatus(result.status ?? null);
      setLocalDiff(null);
    } catch (reason) {
      setGitError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setGitBusy(null);
    }
  }

  async function commitAllChanges() {
    if (!workspacePath || !commitMessage.trim()) {
      return;
    }
    setGitBusy("commit");
    setGitError(null);
    try {
      const result = await gitClient.gitLocalCommit({ cwd: workspacePath, message: commitMessage.trim() });
      setLocalStatus(result.status ?? null);
      setLocalDiff(null);
      setCommitMessage("");
    } catch (reason) {
      setGitError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setGitBusy(null);
    }
  }

  useEffect(() => {
    setLocalStatus(null);
    setLocalDiff(null);
    setSelectedBranch("");
    setGitError(null);
    if (workspacePath) {
      void refreshLocalStatus();
    }
  }, [refreshLocalStatus, workspacePath]);

  return (
    <section className="git-workspace-panel" aria-label="Git 工作区">
      <header className="git-workspace-panel-header git-workspace-toolbar">
        <h3 className="git-workspace-branch-title">{branchName}</h3>
        <div className="git-workspace-branch">
          <label>
            <span>分支</span>
            <select
              aria-label="切换 Git 分支"
              disabled={!branchOptions.length || gitBusy === "checkout"}
              value={selectedBranch || branchName}
              onChange={(event) => setSelectedBranch(event.target.value)}
            >
              {branchOptions.length ? (
                branchOptions.map((branch) => (
                  <option key={branch.name} value={branch.name}>
                    {branch.current ? "✓ " : ""}{branch.name}
                  </option>
                ))
              ) : (
                <option value={branchName}>{branchName}</option>
              )}
            </select>
          </label>
          <ChevronDown size={14} aria-hidden="true" />
        </div>
        <div className="git-workspace-toolbar-stats">
          <strong className="git-workspace-line-stat">+{additions} -{deletions}</strong>
          <StatusBadge label={dirtyFiles > 0 ? `${dirtyFiles} 个文件` : "干净"} tone={dirtyFiles > 0 ? "warning" : "success"} compact />
        </div>
        <div className="git-workspace-toolbar-actions">
          <Button
            size="xs"
            variant="secondary"
            loading={gitBusy === "status"}
            disabled={!canUseLocalGit}
            onClick={() => {
              void refreshLocalStatus();
            }}
          >
            <RefreshCw size={13} aria-hidden="true" />
            状态
          </Button>
          <Button
            size="xs"
            variant="secondary"
            loading={gitBusy === "checkout"}
            disabled={!selectedBranch || selectedBranch === branchName}
            onClick={() => {
              void checkoutBranch();
            }}
          >
            切换
          </Button>
          <Button
            size="xs"
            variant="secondary"
            loading={gitBusy === "diff"}
            disabled={!canUseLocalGit}
            onClick={() => {
              void loadLocalDiff();
            }}
          >
            查看 diff
          </Button>
          {canInitRepo ? (
            <Button
              size="xs"
              variant="secondary"
              loading={gitBusy === "init"}
              onClick={() => {
                void initRepository();
              }}
            >
              <GitPullRequestCreate size={13} aria-hidden="true" />
              初始化
            </Button>
          ) : null}
        </div>
      </header>

      <div className="git-workspace-status-line">
        <span title={workspacePath}>{compactText(workspacePath || "当前会话未提供工作目录", 96)}</span>
        <span>{statusSummary}</span>
        {localStatus?.upstream ? <span>{localStatus.upstream}</span> : null}
        {localStatus && (localStatus.ahead || localStatus.behind) ? <span>↑{localStatus.ahead ?? 0} ↓{localStatus.behind ?? 0}</span> : null}
      </div>

      <div className="git-workspace-actions git-worktree-actions">
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
      {gitError ? <p className="session-tool-error">{gitError}</p> : null}

      <div className="git-workspace-layout">
        <main className="git-workspace-diff-pane">
          <div className="git-workspace-section-head git-workspace-diff-head">
            <div>
              <strong>{localDiff ? "本地 diff" : "审查 diff"}</strong>
              <small>{diffStat || diffSummary(worktreeDiff)}</small>
            </div>
            <span className="git-workspace-line-stat">+{additions} -{deletions}</span>
          </div>
          {diffPreview ? (
            <UnifiedDiffViewer diffText={diffPreview} />
          ) : (
            <div className="git-workspace-empty-diff">
              <strong>等待代码变更</strong>
              <span>点击“查看 diff”后会显示当前工作区的真实差异。</span>
            </div>
          )}
        </main>

        <aside className="git-workspace-file-pane">
          <section className="git-workspace-section">
            <div className="git-workspace-section-head">
              <strong>变更文件</strong>
              <small>{fileRows.length ? `${fileRows.length} 个文件` : "暂无记录"}</small>
            </div>
            {fileRows.length ? (
              <ul className="git-workspace-file-list">
                {fileRows.slice(0, 80).map((file) => (
                  <li key={`${file.source}:${file.path}`}>
                    <span>{formatGitStatus(file.status)}</span>
                    <button
                      type="button"
                      onClick={() => {
                        void loadLocalDiff(file.path);
                      }}
                    >
                      <strong>{fileNameFromPath(file.path)}</strong>
                      <small title={file.path}>{file.path}</small>
                      {file.reason ? <small>{file.reason}</small> : null}
                    </button>
                    <code>{compactMeta([formatSignedCount(file.additions, "+"), formatSignedCount(file.deletions, "-")]).join(" ")}</code>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="session-tool-muted">暂无未提交文件。</p>
            )}
          </section>

          <section className="git-workspace-section git-workspace-commit-box">
            <div className="git-workspace-section-head">
              <strong>提交</strong>
              <small>提交全部当前改动</small>
            </div>
            <input
              aria-label="Git 提交信息"
              placeholder="Commit message"
              value={commitMessage}
              onChange={(event) => setCommitMessage(event.target.value)}
            />
            <Button
              size="xs"
              variant="secondary"
              loading={gitBusy === "commit"}
              disabled={!commitMessage.trim() || !dirtyFiles}
              onClick={() => {
                void commitAllChanges();
              }}
            >
              <GitCommitHorizontal size={13} aria-hidden="true" />
              提交
            </Button>
          </section>

          <section className="git-workspace-section">
            <div className="git-workspace-section-head">
              <strong>审查状态</strong>
              <small>{verificationSummary(verificationRows)}</small>
            </div>
            <div className="git-workspace-review-meta">
              <span>Review</span>
              <strong>{reviewStatus}</strong>
              <small>{reviewSummary || "等待代码审查结果"}</small>
              <span>Merge</span>
              <strong>{approvalDecision}</strong>
              <small>{approvalSummary || "加载 diff 且工作区干净后可申请"}</small>
            </div>
          </section>

          {verificationRows.length ? (
            <section className="git-workspace-section">
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
            </section>
          ) : null}
        </aside>
      </div>
    </section>
  );
}
