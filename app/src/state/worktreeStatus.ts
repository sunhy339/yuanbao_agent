import type { WorktreeStatusResult } from "@shared";
import type { SessionWorkspaceWorktreeStatus } from "../ui/workbench/workspaces/session/types";

export function normalizeSessionWorktreeStatus(
  value: WorktreeStatusResult["gitStatus"] | null | undefined,
): SessionWorkspaceWorktreeStatus | null {
  if (!value) return null;
  const record = value as Record<string, any>;
  if (typeof record.error === "string" && !("dirtyFiles" in record) && !("changes" in record)) {
    return { error: record.error };
  }

  const rawFiles = Array.isArray(record.files) ? record.files : [];
  const changes = Array.isArray(record.changes) ? record.changes : rawFiles;
  const files = changes
    .map((change: any) => {
      if (typeof change === "string") return change;
      return [change.status, change.path].filter(Boolean).join(" ").trim();
    })
    .filter(Boolean);
  const dirtyFiles = typeof record.dirtyFiles === "number" ? record.dirtyFiles : changes.length;

  return {
    branch: typeof record.branch === "string" ? record.branch : null,
    upstream: typeof record.upstream === "string" ? record.upstream : null,
    ahead: typeof record.ahead === "number" ? record.ahead : 0,
    behind: typeof record.behind === "number" ? record.behind : 0,
    rawStatus: typeof record.rawStatus === "string" ? record.rawStatus : undefined,
    dirtyFiles,
    files,
    clean: typeof record.clean === "boolean" ? record.clean : dirtyFiles === 0,
    error: typeof record.error === "string" ? record.error : undefined,
  };
}
