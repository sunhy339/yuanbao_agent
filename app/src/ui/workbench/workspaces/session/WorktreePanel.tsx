import { Button, StatusBadge } from "../../../v2/components/ui";
import { formatStatusLabel } from "../../../copy";
import type {
  SessionWorkspaceWorktree,
  SessionWorkspaceWorktreeDiff,
  SessionWorkspaceWorktreeStatus,
} from "./types";
import { compactMeta, getStatusTone } from "./utils";

export function WorktreePanel({
  worktree,
  status,
  diff,
  busyAction,
  error,
  onRefresh,
  onLoadDiff,
  onMerge,
  onCleanup,
}: {
  worktree?: SessionWorkspaceWorktree | null;
  status?: SessionWorkspaceWorktreeStatus | null;
  diff?: SessionWorkspaceWorktreeDiff | null;
  busyAction?: "status" | "diff" | "requestMergeApproval" | "merge" | "cleanup" | null;
  error?: string | null;
  onRefresh?(worktreeId: string): void | Promise<void>;
  onLoadDiff?(worktreeId: string): void | Promise<void>;
  onMerge?(worktreeId: string): void | Promise<void>;
  onCleanup?(worktreeId: string, force?: boolean): void | Promise<void>;
}) {
  if (!worktree) {
    return null;
  }

  const dirtyFiles = status?.dirtyFiles ?? 0;
  const statusSummary = status?.error
    ? status.error
    : dirtyFiles > 0
      ? `${dirtyFiles} dirty file${dirtyFiles === 1 ? "" : "s"}`
      : status
        ? "Clean worktree"
        : "Status not loaded";
  const diffSummary = diff?.error
    ? diff.error
    : diff?.diffStat || diff?.diff
      ? diff.diffStat || diff.diff
      : "Diff not loaded";
  const hasReviewedDiff = Boolean(diff && !diff.error && (diff.diffStat || diff.diff || diff.files?.length));
  const canMerge = Boolean(onMerge) && hasReviewedDiff && dirtyFiles === 0 && !status?.error;
  const canCleanup = Boolean(onCleanup) && dirtyFiles === 0 && !status?.error;
  const mergeVerification = worktree.lastStatus?.mergeVerification ?? [];
  const failedVerification = mergeVerification.find((item) => item.status === "failed");
  const verificationSummary = !mergeVerification.length
    ? "Not run"
    : failedVerification
      ? failedVerification.summary || failedVerification.command || "Failed"
      : `${mergeVerification.length} passed`;

  return (
    <section className="worktree-panel" aria-label="Task worktree">
      <header>
        <div>
          <p className="session-kicker">Worktree</p>
          <h3>{worktree.branchName || worktree.id}</h3>
        </div>
        <StatusBadge
          label={formatStatusLabel(worktree.status ?? "active")}
          tone={getStatusTone(worktree.status)}
          compact
        />
      </header>

      <dl className="worktree-meta">
        <div>
          <dt>Path</dt>
          <dd title={worktree.worktreePath}>{worktree.worktreePath}</dd>
        </div>
        <div>
          <dt>Base</dt>
          <dd>{worktree.baseRef || "HEAD"}</dd>
        </div>
        <div>
          <dt>Policy</dt>
          <dd>{compactMeta([worktree.mergePolicy, worktree.cleanupPolicy]).join(" / ") || "default"}</dd>
        </div>
      </dl>

      <div className="worktree-state-grid">
        <article>
          <span>Status</span>
          <strong>{statusSummary}</strong>
          {status?.files?.length ? (
            <ul>
              {status.files.slice(0, 4).map((file) => (
                <li key={file}><code>{file}</code></li>
              ))}
            </ul>
          ) : null}
        </article>
        <article>
          <span>Diff</span>
          <strong>{diffSummary}</strong>
        </article>
        <article>
          <span>Verification</span>
          <strong>{verificationSummary}</strong>
          {mergeVerification.length ? (
            <ul>
              {mergeVerification.slice(0, 3).map((item, index) => (
                <li key={`${item.command ?? "verification"}:${index}`}>
                  <code>{compactMeta([item.status, item.command]).join(" - ")}</code>
                </li>
              ))}
            </ul>
          ) : null}
        </article>
      </div>

      {error ? <p className="worktree-error" role="alert">{error}</p> : null}

      <div className="worktree-actions">
        <Button
          size="sm"
          variant="secondary"
          loading={busyAction === "status"}
          disabled={!onRefresh}
          onClick={() => {
            void onRefresh?.(worktree.id);
          }}
        >
          Refresh
        </Button>
        <Button
          size="sm"
          variant="secondary"
          loading={busyAction === "diff"}
          disabled={!onLoadDiff}
          onClick={() => {
            void onLoadDiff?.(worktree.id);
          }}
        >
          Diff
        </Button>
        <Button
          size="sm"
          variant="secondary"
          loading={busyAction === "requestMergeApproval" || busyAction === "merge"}
          disabled={!canMerge}
          disabledReason={
            status?.error ??
            (dirtyFiles > 0
              ? "Merge approval is blocked while the worktree has uncommitted changes"
              : "Review the worktree diff before merging")
          }
          onClick={() => {
            void onMerge?.(worktree.id);
          }}
        >
          Request merge
        </Button>
        <Button
          size="sm"
          variant="danger"
          loading={busyAction === "cleanup"}
          disabled={!canCleanup}
          disabledReason={dirtyFiles > 0 ? "Cleanup is blocked while the worktree has uncommitted changes" : undefined}
          onClick={() => {
            void onCleanup?.(worktree.id, false);
          }}
        >
          Cleanup
        </Button>
      </div>
    </section>
  );
}
