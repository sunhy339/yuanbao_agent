import {
  type CSSProperties,
  type PointerEvent as ReactPointerEvent,
  useCallback,
  useMemo,
  useState,
} from "react";
import {
  Activity,
  ClipboardList,
  Files,
  GitBranch,
  GripVertical,
  Home,
  PanelRightClose,
  PanelRightOpen,
  SquareTerminal,
  type LucideIcon,
} from "lucide-react";
import { Button, StatusBadge } from "../../../v2/components/ui";
import { formatStatusLabel } from "../../../copy";
import type { SessionWorkspaceProps, RuntimeTimelineItem } from "./types";
import {
  buildCommandOutput,
  compactMeta,
  compactText,
  getRuntimeKindLabel,
  getStatusTone,
  isBackgroundProbeCommand,
  isSuccessfulRuntimeStatus,
  isTaskControllable,
  normalizeCommandLabel,
} from "./utils";
import { shouldDisplayTaskScaffold, expectsAgentWork } from "./taskPhase";
import { buildRuntimeItems } from "./runtimeItemBuilder";
import { buildConversationActivity, ConversationActivity } from "./ConversationActivity";
import { ConversationTaskDigest } from "./ConversationTaskDigest";
import { TaskProgressPanel } from "./TaskProgressPanel";
import { RuntimeCockpitPanel } from "./RuntimeCockpitPanel";
import { AgentCollaborationPanel } from "./AgentCollaborationPanel";
import { TraceFilterBar } from "./TraceFilterBar";
import { FileWorkspacePanel } from "./FileWorkspacePanel";
import { GitWorkspacePanel } from "./GitWorkspacePanel";
import { LocalTerminalPanel } from "./LocalTerminalPanel";
import { isChatVisibleEvent } from "./visibilityRouting";
import "./session.css";

// Re-export types for backward compatibility with external consumers
export type {
  SessionWorkspaceCollaboration,
  SessionWorkspaceBackgroundJob,
  SessionWorkspaceContextPreview,
  SessionWorkspaceWorktreeDiff,
  SessionWorkspaceWorktreeStatus,
  SessionWorkspaceProps,
} from "./types";

function runtimeLaneRowLabel(item: RuntimeTimelineItem) {
  if (item.kind === "command") {
    return item.title.replace(/^python -m /, "");
  }
  if (item.kind === "patch") {
    return item.title.replace(/^Update\s+/i, "");
  }
  return item.title;
}

function runtimeLaneRowSummary(item: RuntimeTimelineItem) {
  if (item.kind === "command") {
    const status = item.status ? formatStatusLabel(item.status) : "已记录";
    return compactText(item.summary ? `${item.summary}` : `命令${status}`, 86);
  }
  if (item.kind === "patch") {
    return compactText(item.summary || item.code || "已记录文件改动", 86);
  }
  return compactText(item.summary || item.rawDetail || item.code || "需要关注的运行信号", 86);
}

function uniqueNonEmptyStrings(values: Array<string | null | undefined>) {
  const seen = new Set<string>();
  const result: string[] = [];
  values.forEach((value) => {
    const normalized = String(value ?? "").trim();
    if (!normalized || seen.has(normalized)) {
      return;
    }
    seen.add(normalized);
    result.push(normalized);
  });
  return result;
}

function normalizeWorkspaceRelativePath(path: string) {
  return path.replace(/\\/g, "/").replace(/^[MADRCU?!]{1,2}\s+/, "").replace(/^"(.+)"$/, "$1").trim();
}

function fileNameFromPath(path: string) {
  const normalized = normalizeWorkspaceRelativePath(path);
  return normalized.split("/").filter(Boolean).at(-1) || normalized || ".";
}

function readObject(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : null;
}

function readText(record: Record<string, unknown> | null, key: string): string {
  const value = record?.[key];
  return typeof value === "string" && value.trim() ? value.trim() : "";
}

function RuntimeLanePatchDetail({ item, isBusy }: { item: RuntimeTimelineItem; isBusy: boolean }) {
  if (item.diffLines?.length) {
    return (
      <div className="session-runtime-lane-detail">
        <div className="diff-view">
          {item.diffLines.map((line, lineIndex) => (
            <div key={lineIndex} className={`diff-line diff-line-${line.type}`}>
              <span className="diff-line-prefix">
                {line.type === "add" ? "+" : line.type === "remove" ? "-" : line.type === "header" ? "" : " "}
              </span>
              <span className="diff-line-content">{line.content}</span>
            </div>
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className="session-runtime-lane-detail">
      <p className="runtime-diff-empty" role="status">
        {isBusy ? "差异正在加载。" : "差异暂不可用，请在运行时写入改动后再试一次。"}
      </p>
    </div>
  );
}

function RuntimeLaneSummaryRow({
  item,
  onApprove,
  onReject,
  onLoadPatch,
  onCopyRuntimeText,
  busyId,
}: {
  item: RuntimeTimelineItem;
  onApprove?(approvalId: string): void | Promise<void>;
  onReject?(approvalId: string): void | Promise<void>;
  onLoadPatch?(patchId: string): void | Promise<void>;
  onCopyRuntimeText?(label: string, text: string): void | Promise<void>;
  busyId?: string | null;
}) {
  const [expanded, setExpanded] = useState(false);
  const kindLabel = getRuntimeKindLabel(item.kind);
  const statusLabel = item.status ? formatStatusLabel(item.status) : undefined;
  const isBusy = item.sourceId ? busyId === item.sourceId : false;
  const commandOutput = item.kind === "command" ? buildCommandOutput(item) : "";
  const traceDetail = item.kind === "trace" ? item.code || item.rawDetail || "" : "";
  const canResolveApproval = item.kind === "approval" && item.status === "pending" && item.sourceId;
  const canLoadPatch = item.kind === "patch" && Boolean(item.sourceId);
  const canCopyCommandOutput = Boolean(item.kind === "command" && onCopyRuntimeText && commandOutput.trim());
  const canCopyTraceDetail = Boolean(item.kind === "trace" && onCopyRuntimeText && traceDetail.trim());
  const hasActions = canResolveApproval || canLoadPatch || canCopyCommandOutput || (expanded && canCopyTraceDetail);

  return (
    <li className="session-runtime-lane-row" data-kind={item.kind} data-status={item.status ?? "recorded"}>
      <button
        aria-expanded={expanded}
        aria-label={`${kindLabel} ${item.title}${statusLabel ? ` ${statusLabel}` : ""}`}
        className="session-runtime-lane-row-main"
        onClick={() => setExpanded((current) => !current)}
        type="button"
      >
        <span className="session-runtime-lane-dot" aria-hidden="true" />
        <div>
          <strong>{runtimeLaneRowLabel(item)}</strong>
          <small>{runtimeLaneRowSummary(item)}</small>
        </div>
        {item.status ? <StatusBadge label={formatStatusLabel(item.status)} tone={getStatusTone(item.status)} compact /> : null}
      </button>
      {hasActions ? (
        <div className="session-runtime-lane-row-actions">
          {canLoadPatch ? (
            <Button
              size="xs"
              variant="secondary"
              loading={isBusy}
              onClick={() => {
                void onLoadPatch?.(item.sourceId ?? "");
                setExpanded(true);
              }}
            >
              查看差异
            </Button>
          ) : null}
          {canCopyCommandOutput ? (
            <Button
              size="xs"
              variant="secondary"
              onClick={() => {
                void onCopyRuntimeText?.("命令输出", commandOutput);
              }}
            >
              复制输出
            </Button>
          ) : null}
          {expanded && canCopyTraceDetail ? (
            <Button
              size="xs"
              variant="secondary"
              onClick={() => {
                void onCopyRuntimeText?.("诊断详情", traceDetail);
              }}
            >
              复制详情
            </Button>
          ) : null}
          {canResolveApproval ? (
            <>
              <Button
                size="xs"
                variant="secondary"
                loading={isBusy}
                onClick={() => {
                  void onApprove?.(item.sourceId ?? "");
                }}
              >
                批准
              </Button>
              <Button
                size="xs"
                variant="secondary"
                loading={isBusy}
                onClick={() => {
                  void onReject?.(item.sourceId ?? "");
                }}
              >
                拒绝
              </Button>
            </>
          ) : null}
        </div>
      ) : null}
      {expanded && item.kind === "patch" ? <RuntimeLanePatchDetail item={item} isBusy={isBusy} /> : null}
      {expanded && item.kind === "command" && commandOutput ? (
        <pre className="session-runtime-lane-detail">{commandOutput}</pre>
      ) : null}
      {expanded && item.kind === "trace" && traceDetail ? (
        <pre className="session-runtime-lane-detail">{traceDetail}</pre>
      ) : null}
      {expanded && item.kind === "approval" && (item.summary || item.code) ? (
        <div className="session-runtime-lane-detail">
          {item.summary ? <p>{item.summary}</p> : null}
          {item.code ? <pre>{item.code}</pre> : null}
        </div>
      ) : null}
    </li>
  );
}

type SessionToolKey = "review" | "terminal" | "git";
type SessionWorkspacePaneKey = "home" | "files" | "review" | "terminal" | "git" | "diagnostics";

interface SessionToolCommand {
  id?: string;
  command: string;
  cwd?: string;
  shell?: string;
  status?: string;
  summary?: string;
  durationMs?: number | null;
  exitCode?: number | null;
  stdoutPath?: string | null;
  stderrPath?: string | null;
}

interface SessionToolReviewFile {
  path: string;
  status?: string;
  additions?: number;
  deletions?: number;
  reason?: string;
  source: "task" | "patch" | "git";
}

function normalizeReviewStatus(status?: string) {
  const normalized = String(status ?? "").trim();
  if (!normalized) return "";
  if (normalized === "M") return "modified";
  if (normalized === "A") return "added";
  if (normalized === "D") return "deleted";
  if (normalized === "R") return "renamed";
  if (normalized === "??") return "untracked";
  return normalized;
}

function readGitStatusPath(path: string) {
  const normalized = path.trim();
  const match = normalized.match(/^([ MADRCU?!]{1,2})\s+(.+)$/);
  if (!match) {
    return { status: "", path: normalizeWorkspaceRelativePath(normalized) };
  }
  return {
    status: normalizeReviewStatus(match[1].trim()),
    path: normalizeWorkspaceRelativePath(match[2]),
  };
}

function mergeReviewFileRows(rows: SessionToolReviewFile[]) {
  const merged = new Map<string, SessionToolReviewFile>();
  rows.forEach((row) => {
    const path = normalizeWorkspaceRelativePath(row.path);
    if (!path) return;
    const existing = merged.get(path);
    merged.set(path, {
      path,
      status: normalizeReviewStatus(row.status) || existing?.status,
      additions: row.additions ?? existing?.additions,
      deletions: row.deletions ?? existing?.deletions,
      reason: row.reason || existing?.reason,
      source: existing?.source ?? row.source,
    });
  });
  return [...merged.values()];
}

function buildReviewFileRows(
  activeTask: SessionWorkspaceProps["activeTask"],
  patches: SessionWorkspaceProps["patches"],
  worktreeStatus: SessionWorkspaceProps["worktreeStatus"],
) {
  return mergeReviewFileRows([
    ...(activeTask?.changedFiles?.map((file) => ({
      path: file.path,
      status: file.status,
      additions: file.additions,
      deletions: file.deletions,
      reason: file.reason,
      source: "task" as const,
    })) ?? []),
    ...((patches ?? []).flatMap((patch) =>
      (patch.files ?? []).map((file) => ({
        path: file.path,
        status: file.status,
        additions: file.additions,
        deletions: file.deletions,
        source: "patch" as const,
      })),
    ) as SessionToolReviewFile[]),
    ...(worktreeStatus?.files?.map((rawPath) => {
      const parsed = readGitStatusPath(rawPath);
      return {
        path: parsed.path,
        status: parsed.status,
        source: "git" as const,
      };
    }) ?? []),
  ]);
}

function formatReviewFileStatus(status?: string) {
  const normalized = normalizeReviewStatus(status);
  if (!normalized) return "recorded";
  if (normalized === "modified") return "modified";
  if (normalized === "added") return "added";
  if (normalized === "deleted") return "deleted";
  if (normalized === "untracked") return "untracked";
  return normalized;
}

function formatReviewFileStats(file: SessionToolReviewFile) {
  const hasStats = file.additions !== undefined || file.deletions !== undefined;
  if (!hasStats) return "";
  return `+${file.additions ?? 0} -${file.deletions ?? 0}`;
}

function isCommandPolicyBlocked(command: SessionToolCommand) {
  const haystack = [command.summary, command.command].filter(Boolean).join("\n").toLowerCase();
  return (
    haystack.includes("command is not allowed by command allowlist") ||
    haystack.includes("permission_denied") ||
    haystack.includes("request_permission") ||
    haystack.includes("not allowed by command allowlist")
  );
}

interface UnifiedDiffLine {
  type: "meta" | "hunk" | "add" | "remove" | "context";
  content: string;
  oldLine?: number;
  newLine?: number;
}

interface UnifiedDiffFile {
  key: string;
  path: string;
  additions: number;
  deletions: number;
  lines: UnifiedDiffLine[];
}

function diffPathFromHeader(line: string) {
  const match = line.match(/^diff --git a\/(.+?) b\/(.+)$/);
  return match?.[2] || match?.[1] || line.replace(/^diff --git\s+/, "").trim() || "diff";
}

function parseUnifiedDiff(diffText: string): UnifiedDiffFile[] {
  const files: UnifiedDiffFile[] = [];
  let current: UnifiedDiffFile | null = null;
  let oldLine = 0;
  let newLine = 0;

  diffText.replace(/\r\n/g, "\n").split("\n").forEach((line, index) => {
    if (line.startsWith("diff --git ")) {
      current = {
        key: `${index}:${line}`,
        path: diffPathFromHeader(line),
        additions: 0,
        deletions: 0,
        lines: [{ type: "meta", content: line }],
      };
      files.push(current);
      oldLine = 0;
      newLine = 0;
      return;
    }

    if (!current) {
      current = {
        key: `inline:${index}`,
        path: "diff",
        additions: 0,
        deletions: 0,
        lines: [],
      };
      files.push(current);
    }

    const hunk = line.match(/^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/);
    if (hunk) {
      oldLine = Number(hunk[1]);
      newLine = Number(hunk[2]);
      current.lines.push({ type: "hunk", content: line });
      return;
    }

    if (line.startsWith("+") && !line.startsWith("+++")) {
      current.additions += 1;
      current.lines.push({ type: "add", content: line.slice(1), newLine });
      newLine += 1;
      return;
    }

    if (line.startsWith("-") && !line.startsWith("---")) {
      current.deletions += 1;
      current.lines.push({ type: "remove", content: line.slice(1), oldLine });
      oldLine += 1;
      return;
    }

    if (line.startsWith(" ") || line === "") {
      current.lines.push({ type: "context", content: line.startsWith(" ") ? line.slice(1) : line, oldLine, newLine });
      if (oldLine) oldLine += 1;
      if (newLine) newLine += 1;
      return;
    }

    current.lines.push({ type: "meta", content: line });
  });

  return files.filter((file) => file.lines.length > 0);
}

function diffTextFromReviewSources(
  worktreeDiff: SessionWorkspaceProps["worktreeDiff"],
  patches?: SessionWorkspaceProps["patches"],
) {
  const directDiff = worktreeDiff?.diff || worktreeDiff?.preview;
  if (directDiff?.trim()) {
    return directDiff;
  }
  return (patches ?? [])
    .flatMap((patch) => [patch.diff, ...(patch.files ?? []).map((file) => file.diff)])
    .filter((value): value is string => Boolean(value?.trim()))
    .join("\n");
}

function UnifiedDiffViewer({ diffText }: { diffText: string }) {
  const files = parseUnifiedDiff(diffText);
  if (!files.length) {
    return <p className="session-tool-muted">还没有可展示的差异内容。</p>;
  }

  return (
    <div className="session-diff-viewer" aria-label="真实差异">
      {files.slice(0, 10).map((file) => (
        <article className="session-diff-file" key={file.key}>
          <header>
            <strong title={file.path}>{file.path}</strong>
            <span>
              +{file.additions} -{file.deletions}
            </span>
          </header>
          <ol className="session-diff-lines">
            {file.lines.slice(0, 800).map((line, index) => (
              <li className={`session-diff-line session-diff-line-${line.type}`} key={`${file.key}:${index}`}>
                <span className="session-diff-line-old">{line.oldLine ?? ""}</span>
                <span className="session-diff-line-new">{line.newLine ?? ""}</span>
                <code>{line.content || " "}</code>
              </li>
            ))}
          </ol>
        </article>
      ))}
    </div>
  );
}

function SessionWorkspaceToolDock({
  activeTask,
  patches,
  backgroundJobs,
  runtimeItems,
  worktreeStatus,
  worktreeDiff,
  worktreeBusyAction,
  worktreeError,
  composerContext,
  onLoadPatch,
  onRefreshCommandJob,
  onStopCommandJob,
  onRefreshWorktree,
  onLoadWorktreeDiff,
  onMergeWorktree,
  onCleanupWorktree,
  busyId,
  activeTool,
  onActiveToolChange,
  showChrome = true,
}: {
  activeTask: SessionWorkspaceProps["activeTask"];
  patches?: SessionWorkspaceProps["patches"];
  backgroundJobs?: SessionWorkspaceProps["backgroundJobs"];
  runtimeItems?: RuntimeTimelineItem[];
  worktreeStatus?: SessionWorkspaceProps["worktreeStatus"];
  worktreeDiff?: SessionWorkspaceProps["worktreeDiff"];
  worktreeBusyAction?: SessionWorkspaceProps["worktreeBusyAction"];
  worktreeError?: SessionWorkspaceProps["worktreeError"];
  composerContext?: SessionWorkspaceProps["composerContext"];
  onLoadPatch?: SessionWorkspaceProps["onLoadPatch"];
  onRefreshCommandJob?: SessionWorkspaceProps["onRefreshCommandJob"];
  onStopCommandJob?: SessionWorkspaceProps["onStopCommandJob"];
  onRefreshWorktree?: SessionWorkspaceProps["onRefreshWorktree"];
  onLoadWorktreeDiff?: SessionWorkspaceProps["onLoadWorktreeDiff"];
  onMergeWorktree?: SessionWorkspaceProps["onMergeWorktree"];
  onCleanupWorktree?: SessionWorkspaceProps["onCleanupWorktree"];
  busyId?: string | null;
  activeTool?: SessionToolKey;
  onActiveToolChange?: (tool: SessionToolKey) => void;
  showChrome?: boolean;
}) {
  const [internalActiveTool, setInternalActiveTool] = useState<SessionToolKey>("review");
  const selectedTool = activeTool ?? internalActiveTool;
  const selectTool = onActiveToolChange ?? setInternalActiveTool;
  const activeWorktree = activeTask?.activeWorktree;
  const workspacePath = composerContext?.cwd || activeWorktree?.worktreePath || "";
  const branchName = composerContext?.branch || activeWorktree?.branchName || "未识别分支";
  const workspaceLabel = workspacePath || "当前会话未提供工作目录";

  const reviewFileRows = buildReviewFileRows(activeTask, patches, worktreeStatus);
  const normalizedRelatedFiles = uniqueNonEmptyStrings(reviewFileRows.map((file) => file.path));

  const patchStats = (patches ?? []).reduce(
    (stats, patch) => ({
      additions: stats.additions + (patch.additions ?? 0),
      deletions: stats.deletions + (patch.deletions ?? 0),
    }),
    { additions: 0, deletions: 0 },
  );
  const taskPatchStats = (activeTask?.changedFiles ?? []).reduce(
    (stats, file) => ({
      additions: stats.additions + (file.additions ?? 0),
      deletions: stats.deletions + (file.deletions ?? 0),
    }),
    { additions: 0, deletions: 0 },
  );
  const hasPatchLineStats = (patches ?? []).some((patch) => patch.additions !== undefined || patch.deletions !== undefined);
  const hasTaskLineStats = (activeTask?.changedFiles ?? []).some((file) => file.additions !== undefined || file.deletions !== undefined);
  const hasLineStats = hasPatchLineStats || hasTaskLineStats;
  const additions = patchStats.additions || taskPatchStats.additions;
  const deletions = patchStats.deletions || taskPatchStats.deletions;
  const patchCount = (patches?.length ?? 0) + (activeTask?.changedFiles?.length ? 1 : 0);
  const reviewFileCount = normalizedRelatedFiles.length;
  const firstPatchId = patches?.[0]?.id;
  const dirtyFiles = worktreeStatus?.dirtyFiles ?? normalizedRelatedFiles.length;
  const latestPatch = patches?.[0];
  const reviewRecord = readObject(activeWorktree?.lastStatus?.review);
  const mergeApprovalRecord = readObject(activeWorktree?.lastStatus?.mergeApproval);
  const reviewSummary = compactText(
    compactMeta([
      readText(reviewRecord, "reviewer"),
      readText(reviewRecord, "summary") || readText(reviewRecord, "status"),
    ]).join(" - "),
    180,
  );
  const mergeApprovalSummary = compactText(
    compactMeta([
      readText(mergeApprovalRecord, "decision"),
      readText(mergeApprovalRecord, "targetBranch"),
      readText(mergeApprovalRecord, "verificationStatus"),
    ]).join(" - "),
    180,
  );
  const latestPatchSummary = compactText(latestPatch?.summary || activeTask?.summary || activeTask?.currentStep, 180);
  const reviewFileSummary =
    reviewFileRows.length && !latestPatchSummary
      ? `${reviewFileRows.length} 个文件：${reviewFileRows
          .slice(0, 4)
          .map((file) => `${fileNameFromPath(file.path)} ${formatReviewFileStatus(file.status)}`)
          .join("、")}${reviewFileRows.length > 4 ? " 等" : ""}`
      : "";
  const reviewHighlights = compactMeta([
    latestPatchSummary ? `改动：${latestPatchSummary}` : null,
    reviewFileSummary ? `文件：${reviewFileSummary}` : null,
    reviewSummary ? `审查：${reviewSummary}` : null,
    mergeApprovalSummary ? `合并：${mergeApprovalSummary}` : null,
    worktreeDiff?.diffStat ? `Diff：${worktreeDiff.diffStat}` : null,
  ]);
  const rawCommandItems: SessionToolCommand[] = [
    ...(activeTask?.commands?.map((command) => ({ ...command })) ?? []),
    ...(activeTask?.verification?.map((verification) => ({
      id: verification.id,
      command: verification.command ?? "verification",
      status: verification.status,
      summary: verification.summary,
      durationMs: verification.durationMs,
      exitCode: verification.exitCode,
    })) ?? []),
    ...((backgroundJobs ?? [])
      .filter((job) => !(isSuccessfulRuntimeStatus(job.status) && isBackgroundProbeCommand(job.command)))
      .map((job) => ({ ...job })) ?? []),
    ...((runtimeItems ?? [])
      .filter((item) => item.kind === "command")
      .filter((item) => item.id.startsWith("tool:"))
      .filter((item) => !item.superseded)
      .filter((item) => !(isSuccessfulRuntimeStatus(item.status) && isBackgroundProbeCommand(item.code || item.title)))
      .map((item) => ({
        id: item.id,
        command: item.code || item.title,
        status: item.status,
        summary: item.summary,
        durationMs: item.durationMs,
        shell: item.meta?.find((part) => /^(powershell|pwsh|cmd|bash|zsh|sh|shell)$/i.test(part)),
      })) ?? []),
  ];
  const seenCommands = new Set<string>();
  const commandItems = rawCommandItems
    .slice()
    .reverse()
    .filter((command) => {
      const normalized = normalizeCommandLabel(command.command)?.toLowerCase() ?? command.command.toLowerCase();
      const key = command.id ? `id:${command.id}` : `${normalized}|${command.status ?? ""}|${command.exitCode ?? ""}`;
      if (seenCommands.has(key)) {
        return false;
      }
      seenCommands.add(key);
      return true;
    })
    .reverse();
  const latestCommand = commandItems.at(-1);
  const latestDiffPreview = diffTextFromReviewSources(worktreeDiff, patches);
  const toolTabs: Array<{ id: SessionToolKey; label: string; description: string; icon: LucideIcon; count?: number }> = [
    { id: "review", label: "审查", description: "查看代码改动", icon: ClipboardList, count: patchCount },
    { id: "terminal", label: "终端", description: "本地 shell", icon: SquareTerminal, count: commandItems.length },
    { id: "git", label: "Git", description: "分支与提交", icon: GitBranch, count: dirtyFiles },
  ];

  return (
    <section className="session-tool-dock session-tool-dock-live" aria-label="工作区工具">
      {showChrome ? (
        <>
          <header className="session-tool-dock-header">
            <div>
              <p className="session-kicker">工作区</p>
              <h2>工具</h2>
            </div>
            <span title={branchName}>{branchName}</span>
          </header>

          <nav className="session-tool-tabs" role="tablist" aria-label="工作区工具类型">
            {toolTabs.map((tab) => {
              const ToolIcon = tab.icon;
              return (
                <button
                  key={tab.id}
                  aria-selected={selectedTool === tab.id}
                  className="session-tool-tab"
                  data-tool={tab.id}
                  onClick={() => selectTool(tab.id)}
                  role="tab"
                  type="button"
                >
                  <span className="session-tool-icon" aria-hidden="true">
                    <ToolIcon size={16} strokeWidth={2} />
                  </span>
                  <span>
                    <strong>{tab.label}</strong>
                    <small>{tab.description}</small>
                  </span>
                  {tab.count ? <em>{tab.count}</em> : null}
                </button>
              );
            })}
          </nav>
        </>
      ) : null}

      <div className="session-tool-panel" role="tabpanel">
        {selectedTool === "review" ? (
          <>
            <div className="session-tool-panel-header">
              <div>
                <strong>代码审查</strong>
                <small>{patchCount ? `${patchCount} 个改动记录` : reviewFileCount ? `${reviewFileCount} 个相关文件` : "等待代码变更"}</small>
              </div>
              {hasLineStats ? (
                <span className="session-tool-diff-stat">+{additions} -{deletions}</span>
              ) : reviewFileCount ? (
                <span className="session-tool-file-stat">{reviewFileCount} 个文件</span>
              ) : null}
            </div>
            {reviewHighlights.length ? (
              <div className="session-tool-review-summary" aria-label="审查摘要">
                {reviewHighlights.map((item) => (
                  <p key={item}>{item}</p>
                ))}
              </div>
            ) : (
              <p className="session-tool-muted">还没有 diff 或审查摘要；有文件改动后这里会显示改了什么、验证和审查状态。</p>
            )}
            <div className="session-tool-actions">
              {firstPatchId && onLoadPatch ? (
                <Button
                  size="xs"
                  variant="secondary"
                  loading={busyId === firstPatchId}
                  onClick={() => {
                    void onLoadPatch(firstPatchId);
                  }}
                >
                  打开补丁
                </Button>
              ) : null}
              {activeWorktree && onLoadWorktreeDiff ? (
                <Button
                  size="xs"
                  variant="secondary"
                  loading={worktreeBusyAction === "diff"}
                  onClick={() => {
                    void onLoadWorktreeDiff(activeWorktree.id, Boolean(worktreeDiff?.truncated));
                  }}
                >
                  {worktreeDiff?.truncated ? "加载完整差异" : "查看差异"}
                </Button>
              ) : null}
            </div>
            {reviewFileRows.length ? (
              <ul className="session-tool-list session-tool-review-file-list" aria-label="审查文件">
                {reviewFileRows.slice(0, 10).map((file) => {
                  const stats = formatReviewFileStats(file);
                  return (
                    <li className="session-tool-review-file-row" key={file.path}>
                      <div className="session-tool-review-file-copy">
                        <strong>{fileNameFromPath(file.path)}</strong>
                        <small>{file.path}</small>
                        {file.reason ? <p>{file.reason}</p> : null}
                      </div>
                      <span className="session-tool-file-badges">
                        <code>{formatReviewFileStatus(file.status)}</code>
                        {stats ? <code>{stats}</code> : null}
                      </span>
                  </li>
                  );
                })}
              </ul>
            ) : null}
            {worktreeDiff?.error ? <p className="session-tool-error">{worktreeDiff.error}</p> : null}
            {worktreeDiff?.diffStat ? <p className="session-tool-muted">{worktreeDiff.diffStat}</p> : null}
            {latestDiffPreview ? <UnifiedDiffViewer diffText={latestDiffPreview} /> : null}
          </>
        ) : null}

        {selectedTool === "terminal" ? (
          <>
            <div className="session-tool-panel-header">
              <div>
                <strong>终端</strong>
                <small>{latestCommand ? compactText(latestCommand.command, 58) : "还没有运行命令"}</small>
              </div>
            </div>
            <LocalTerminalPanel workspaceRoot={workspacePath} workspaceLabel={workspaceLabel} />
            <div className="session-tool-panel-header session-tool-command-history-head">
              <div>
                <strong>命令记录</strong>
                <small>Agent 运行和验证命令</small>
              </div>
            </div>
            {commandItems.length ? (
              <ul className="session-tool-command-list">
                {commandItems.slice(-8).map((command, index) => (
                  <li key={command.id ?? `${command.command}-${index}`} data-status={command.status ?? "recorded"}>
                    <code className="session-tool-terminal-prompt">
                      {command.shell || "shell"} {command.cwd ? compactText(command.cwd, 42) : "."}
                    </code>
                    <div>
                      <strong>{command.command}</strong>
                      <small>
                        {command.status ? formatStatusLabel(command.status) : "已记录"}
                        {command.exitCode !== undefined && command.exitCode !== null ? ` · exit ${command.exitCode}` : ""}
                        {command.durationMs ? ` · ${command.durationMs}ms` : ""}
                      </small>
                    </div>
                    {command.summary ? <p>{command.summary}</p> : null}
                    {isCommandPolicyBlocked(command) ? (
                      <p className="session-tool-warning">
                        命令没有真正执行：运行时策略拦截了这条命令。需要允许该命令，或改用当前白名单允许的等价命令。
                      </p>
                    ) : null}
                    {command.stdoutPath || command.stderrPath ? (
                      <code>
                        {[command.stdoutPath, command.stderrPath].filter(Boolean).join(" · ")}
                      </code>
                    ) : null}
                    {command.id && (onRefreshCommandJob || onStopCommandJob) ? (
                      <div className="session-tool-actions">
                        {onRefreshCommandJob ? (
                          <Button
                            size="xs"
                            variant="secondary"
                            onClick={() => {
                              void onRefreshCommandJob(command.id ?? "");
                            }}
                          >
                            刷新
                          </Button>
                        ) : null}
                        {onStopCommandJob && command.status === "running" ? (
                          <Button
                            size="xs"
                            variant="secondary"
                            onClick={() => {
                              void onStopCommandJob(command.id ?? "");
                            }}
                          >
                            停止
                          </Button>
                        ) : null}
                      </div>
                    ) : null}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="session-tool-muted">命令运行后会在这里保留摘要。</p>
            )}
          </>
        ) : null}

        {selectedTool === "git" ? (
          <GitWorkspacePanel
            activeTask={activeTask}
            patches={patches}
            worktreeStatus={worktreeStatus}
            worktreeDiff={worktreeDiff}
            worktreeBusyAction={worktreeBusyAction}
            worktreeError={worktreeError}
            composerContext={composerContext}
            onRefreshWorktree={onRefreshWorktree}
            onLoadWorktreeDiff={onLoadWorktreeDiff}
            onMergeWorktree={onMergeWorktree}
            onCleanupWorktree={onCleanupWorktree}
          />
        ) : null}

      </div>
    </section>
  );
}

export function SessionWorkspace({
  session,
  messages,
  activeTask,
  collaboration,
  contextPreview,
  approvals,
  patches,
  traces,
  toolCalls,
  backgroundJobs,
  onApprove,
  onReject,
  onLoadPatch,
  onCopyPatchPath,
  onCopyRuntimeText,
  onRefreshCommandJob,
  onStopCommandJob,
  onRefreshTask,
  onPauseTask,
  onResumeTask,
  onStopTask,
  onRefreshTrace,
  onRefreshWorktree,
  onLoadWorktreeDiff,
  onMergeWorktree,
  onCleanupWorktree,
  worktreeStatus,
  worktreeDiff,
  taskBusyAction,
  busyId,
  worktreeBusyAction,
  worktreeError,
  messagesLoading,
  taskCount,
  composerContext,
}: SessionWorkspaceProps) {
  if (!session) {
    return (
      <main className="session-workspace session-workspace-empty" aria-labelledby="session-empty-title">
        <section className="session-empty-card">
          <span className="session-empty-rule" aria-hidden="true" />
          <p className="session-kicker">会话工作台</p>
          <h1 id="session-empty-title">打开或创建会话</h1>
          <p>从侧栏选择一个会话，或新建会话后开始对话。</p>
        </section>
      </main>
    );
  }

  const hasTaskRuntimeEvidence = Boolean(
    activeTask?.changedFiles?.length ||
      activeTask?.commands?.length ||
      activeTask?.verification?.length ||
      activeTask?.activeWorktree,
  );
  const visibleActiveTask =
    shouldDisplayTaskScaffold(activeTask) || hasTaskRuntimeEvidence ? activeTask : null;
  const activeWorktree = visibleActiveTask?.activeWorktree;
  const workspacePath = composerContext?.cwd || activeWorktree?.worktreePath || "";
  const workspaceLabel = workspacePath || "当前会话未提供工作目录";
  const relatedFiles = uniqueNonEmptyStrings([
    ...(visibleActiveTask?.changedFiles?.map((file) => file.path) ?? []),
    ...((patches ?? []).flatMap((patch) => (patch.files ?? []).map((file) => file.path ?? "")) as string[]),
    ...(worktreeStatus?.files ?? []),
  ]).map(normalizeWorkspaceRelativePath);
  const normalizedRelatedFiles = uniqueNonEmptyStrings(relatedFiles);
  const runtimeItems = useMemo(
    () =>
      buildRuntimeItems({
        session,
        activeTask: visibleActiveTask,
        contextPreview,
        approvals,
        patches,
        traces,
        toolCalls,
        backgroundJobs,
      }),
    [visibleActiveTask, approvals, backgroundJobs, contextPreview, patches, session, toolCalls, traces],
  );
  const activityItems = useMemo(() => {
    const chatVisibleItems = runtimeItems.filter((item) =>
      isChatVisibleEvent(
        {
          taskId: item.taskId ?? "root",
          visibility: item.visibility,
        },
        new Set<string>(),
      ),
    );
    const visibleChatItems = chatVisibleItems.filter(
      (item) => !(isSuccessfulRuntimeStatus(item.status) && isBackgroundProbeCommand(item.title)),
    );
    const promotedVerificationByGroup = new Map<string, RuntimeTimelineItem>();
    runtimeItems.forEach((item) => {
      if (item.kind !== "command" || !item.groupKey || item.superseded) {
        return;
      }
      const normalizedStatus = item.status?.toLowerCase();
      const isSuccessful = Boolean(
        normalizedStatus && ["completed", "passed", "succeeded"].includes(normalizedStatus),
      );
      const isVerificationLike =
        item.groupKey.startsWith("command:python -m pytest") ||
        item.groupKey.startsWith("command:python -m py_compile") ||
        item.groupKey.startsWith("command:node --check");
      if (isSuccessful && isVerificationLike) {
        promotedVerificationByGroup.set(item.groupKey, item);
      }
    });
    const promotedVerificationItems = [...promotedVerificationByGroup.values()];
    const mergedChatItems = [...visibleChatItems, ...promotedVerificationItems];
    const dedupedChatItems = Array.from(
      new Map(mergedChatItems.map((item) => [item.id, item])).values(),
    );

    const latestSuccessfulVerificationIds = new Set<string>();
    const latestSuccessfulVerificationByGroup = new Map<string, RuntimeTimelineItem>();
    dedupedChatItems.forEach((item) => {
      if (item.kind !== "command" || !item.groupKey) {
        return;
      }
      const normalizedStatus = item.status?.toLowerCase();
      const isSuccessful = Boolean(
        normalizedStatus && ["completed", "passed", "succeeded"].includes(normalizedStatus),
      );
      const isVerificationLike =
        item.groupKey.startsWith("command:python -m pytest") ||
        item.groupKey.startsWith("command:python -m py_compile") ||
        item.groupKey.startsWith("command:node --check");
      if (isSuccessful && isVerificationLike) {
        latestSuccessfulVerificationByGroup.set(item.groupKey, item);
      }
    });
    latestSuccessfulVerificationByGroup.forEach((item) => latestSuccessfulVerificationIds.add(item.id));

    const filteredChatItems = dedupedChatItems.filter((item) => {
      if (item.superseded) {
        return false;
      }
      if (item.kind === "trace") {
        return false;
      }
      if (item.kind === "patch" || item.id.startsWith("task-files:")) {
        return true;
      }
      if (item.kind === "approval" && item.status !== "pending") {
        return false;
      }
      if (item.kind === "command") {
        const status = item.status?.toLowerCase();
        const isSuccessful = Boolean(status && ["completed", "passed", "succeeded"].includes(status));
        const isVerificationLike = Boolean(
          item.groupKey &&
            (item.groupKey.startsWith("command:python -m pytest") ||
              item.groupKey.startsWith("command:python -m py_compile") ||
              item.groupKey.startsWith("command:node --check")),
        );
        if (isSuccessful && isVerificationLike) {
          return latestSuccessfulVerificationIds.has(item.id);
        }
        if (isSuccessful) {
          return false;
        }
      }
      return true;
    });

    const latestByGroupForChat = new Map<string, RuntimeTimelineItem>();
    filteredChatItems.forEach((item) => {
      if (!item.groupKey) {
        return;
      }
      const existing = latestByGroupForChat.get(item.groupKey);
      const existingTime = existing?.time ?? -1;
      const candidateTime = item.time ?? -1;
      if (!existing || candidateTime > existingTime) {
        latestByGroupForChat.set(item.groupKey, item);
      }
    });
    const latestIdsByGroup = new Set(
      [...latestByGroupForChat.values()].map((item) => item.id),
    );
    const collapsedChatItems = filteredChatItems.filter((item) => {
      if (!item.groupKey) {
        return true;
      }
      const isVerificationLike =
        item.groupKey.startsWith("command:python -m pytest") ||
        item.groupKey.startsWith("command:python -m py_compile") ||
        item.groupKey.startsWith("command:node --check");
      if (!isVerificationLike) {
        return true;
      }
      return latestIdsByGroup.has(item.id);
    });

    if (collapsedChatItems.length <= 2) {
      const promotedRuntimeFallback = runtimeItems
        .filter((item) => !item.superseded)
        .filter(
          (item) =>
            item.kind === "command" &&
            item.groupKey &&
            (item.groupKey.startsWith("command:python -m pytest") ||
              item.groupKey.startsWith("command:python -m py_compile") ||
              item.groupKey.startsWith("command:node --check")),
        )
        .filter((item) => {
          const normalizedStatus = item.status?.toLowerCase();
          return Boolean(normalizedStatus && ["completed", "passed", "succeeded"].includes(normalizedStatus));
        })
        .slice(-3);
      promotedRuntimeFallback.forEach((item) => {
        if (!collapsedChatItems.some((existing) => existing.id === item.id)) {
          collapsedChatItems.push(item);
        }
      });
    }

    const hiddenCommandGroups = new Set(
      collapsedChatItems
        .filter((item) => item.kind === "command" && item.groupKey)
        .map((item) => item.groupKey as string),
    );

    return buildConversationActivity(
      messages,
      collapsedChatItems.filter((item) => !(item.kind === "tool" && item.groupKey && hiddenCommandGroups.has(item.groupKey))),
    );
  }, [messages, runtimeItems]);
  const [traceFilter, setTraceFilter] = useState<{
    taskId: string;
    visibility: "" | "chat" | "panel" | "trace";
    agentType: string;
  }>({ taskId: "", visibility: "", agentType: "" });
  const [workspaceFocus, setWorkspaceFocus] = useState<"files" | null>(null);
  const [workspacePane, setWorkspacePane] = useState<SessionWorkspacePaneKey>("home");
  const [workspacePaneCollapsed, setWorkspacePaneCollapsed] = useState(false);
  const [workspacePaneWidthPx, setWorkspacePaneWidthPx] = useState<number | null>(null);
  const [workspacePaneResizing, setWorkspacePaneResizing] = useState(false);
  const isQuietSuccessfulBackgroundItem = (item: RuntimeTimelineItem) => {
    if (!isSuccessfulRuntimeStatus(item.status)) {
      return false;
    }
    if (item.visibility === "trace" && (item.kind === "tool" || item.kind === "trace")) {
      return true;
    }
    if (item.kind === "tool" && /^(list_dir|read_file|git status)\b/i.test(item.title)) {
      return true;
    }
    if (item.kind === "command" && isBackgroundProbeCommand(item.title)) {
      return true;
    }
    return false;
  };
  const { runtimeLanes, uniqueTaskIds, uniqueAgentTypes } = useMemo(() => {
    const commandsAndTools: RuntimeTimelineItem[] = [];
    const patchItems: RuntimeTimelineItem[] = [];
    const traceItems: RuntimeTimelineItem[] = [];

    for (const item of runtimeItems) {
      if (isQuietSuccessfulBackgroundItem(item)) {
        continue;
      }
      if (item.kind === "tool" && isSuccessfulRuntimeStatus(item.status)) {
        continue;
      }
      if (item.kind === "command" || item.kind === "tool") {
        if (item.visibility !== "chat") {
          commandsAndTools.push(item);
        }
      } else if (item.kind === "patch" || item.id.startsWith("task-files:")) {
        if (item.visibility !== "chat") {
          patchItems.push(item);
        }
      } else {
        if (item.visibility !== "chat") {
          traceItems.push(item);
        }
      }
    }

    const filteredTraceItems = traceItems.filter((item) => {
      if (traceFilter.taskId && item.taskId !== traceFilter.taskId) return false;
      if (traceFilter.visibility && item.visibility !== traceFilter.visibility) return false;
      if (traceFilter.agentType && item.agentType !== traceFilter.agentType) return false;
      return true;
    });

    const runtimeLanes = [
      {
        id: "commands" as const,
        eyebrow: "执行",
        title: "命令与验证",
        emptyTitle: "当前没有需要关注的命令",
        emptyText: "失败、运行中和最近通过的验证会显示在这里。",
        items: commandsAndTools,
      },
      {
        id: "patches" as const,
        eyebrow: "改动",
        title: "补丁与文件",
        emptyTitle: "当前没有待看的改动",
        emptyText: "代码改动、补丁结果和关键文件变化会显示在这里。",
        items: patchItems,
      },
      {
        id: "trace" as const,
        eyebrow: "诊断",
        title: "异常与信号",
        emptyTitle: "当前没有异常信号",
        emptyText: "失败、恢复、路由变化和需要处理的异常会显示在这里。",
        items: filteredTraceItems,
      },
    ];

    const uniqueTaskIds = [...new Set(traceItems.map((i) => i.taskId).filter(Boolean) as string[])];
    const uniqueAgentTypes = [...new Set(traceItems.map((i) => i.agentType).filter(Boolean) as string[])];

    return { runtimeLanes, uniqueTaskIds, uniqueAgentTypes };
  }, [runtimeItems, traceFilter]);
  const visibleRuntimeLanes = runtimeLanes
    .map((lane) => ({
      ...lane,
      items:
        lane.id === "commands"
          ? lane.items.filter((item) => !item.superseded).slice(0, 2)
          : lane.id === "patches"
            ? lane.items.slice(0, 2)
            : lane.items.slice(0, 1),
    }))
    .filter((lane) => lane.items.length > 0);

  const isFilesFocused = workspaceFocus === "files";
  const isWorkspacePaneVisible = isFilesFocused || !workspacePaneCollapsed;
  const selectWorkspacePane = useCallback((pane: SessionWorkspacePaneKey) => {
    setWorkspacePane(pane);
    setWorkspacePaneCollapsed(false);
    if (pane !== "files") {
      setWorkspaceFocus(null);
    }
  }, []);
  const toggleFileFocus = useCallback(() => {
    setWorkspacePane("files");
    setWorkspacePaneCollapsed(false);
    setWorkspaceFocus((current) => (current === "files" ? null : "files"));
  }, []);
  const startWorkspacePaneResize = useCallback(
    (event: ReactPointerEvent<HTMLButtonElement>) => {
      if (workspacePaneCollapsed || isFilesFocused) {
        return;
      }
      const grid = event.currentTarget.closest(".session-workbench-grid") as HTMLElement | null;
      const rect = grid?.getBoundingClientRect();
      const gridWidth = rect?.width || window.innerWidth || 1200;
      const minWidth = Math.min(420, Math.max(360, gridWidth * 0.28));
      const maxWidth = Math.max(minWidth, gridWidth - 670);
      const startWidth = workspacePaneWidthPx ?? Math.min(maxWidth, Math.max(minWidth, gridWidth * 0.32));
      const startX = event.clientX;
      setWorkspacePaneResizing(true);
      event.currentTarget.setPointerCapture?.(event.pointerId);
      const onMove = (moveEvent: PointerEvent) => {
        const nextWidth = startWidth - (moveEvent.clientX - startX);
        setWorkspacePaneWidthPx(Math.round(Math.min(maxWidth, Math.max(minWidth, nextWidth))));
      };
      const onUp = () => {
        setWorkspacePaneResizing(false);
        window.removeEventListener("pointermove", onMove);
      };
      window.addEventListener("pointermove", onMove);
      window.addEventListener("pointerup", onUp, { once: true });
    },
    [isFilesFocused, workspacePaneCollapsed, workspacePaneWidthPx],
  );
  const workspacePaneTabs: Array<{
    id: SessionWorkspacePaneKey;
    label: string;
    description: string;
    icon: LucideIcon;
    count?: number;
  }> = [
    { id: "home", label: "工作区", description: "打开常用面板", icon: Home },
    { id: "files", label: "文件", description: "浏览项目文件", icon: Files, count: normalizedRelatedFiles.length },
    { id: "review", label: "审查", description: "查看代码改动", icon: ClipboardList, count: (patches?.length ?? 0) + (visibleActiveTask?.changedFiles?.length ? 1 : 0) },
    { id: "terminal", label: "终端", description: "本地 shell 与命令记录", icon: SquareTerminal, count: (visibleActiveTask?.commands?.length ?? 0) + (visibleActiveTask?.verification?.length ?? 0) },
    { id: "git", label: "Git", description: "分支与提交", icon: GitBranch, count: worktreeStatus?.dirtyFiles },
    { id: "diagnostics", label: "诊断", description: "失败、审批与子任务", icon: Activity, count: visibleRuntimeLanes.length },
  ];
  const toolPane = ["review", "terminal", "git"].includes(workspacePane)
    ? (workspacePane as SessionToolKey)
    : "review";
  const showDiagnosticsPane = workspacePane === "diagnostics" || workspacePane === "home";
  const workspaceGridStyle =
    workspacePaneWidthPx && !workspacePaneCollapsed && !isFilesFocused
      ? ({ "--session-workspace-pane-width": `${workspacePaneWidthPx}px` } as CSSProperties)
      : undefined;
  const workspaceClassName = [
    "session-workspace",
    "session-workspace-chat-only",
    isFilesFocused ? "session-workspace-files-focused" : "",
    workspacePaneCollapsed ? "session-workspace-pane-collapsed" : "",
    workspacePaneResizing ? "session-workspace-pane-resizing" : "",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <main className={workspaceClassName} aria-label="Session">
      <section className="session-workbench-grid" style={workspaceGridStyle}>
        <section className="session-conversation-column" aria-hidden={isFilesFocused ? "true" : undefined}>
          <header className="session-chat-header">
            <div className="session-chat-title-block">
              <p className="session-kicker">会话</p>
              <h1 id="session-title">{session.title}</h1>
              <div className="session-chip-row" aria-label="会话上下文">
                <StatusBadge label={formatStatusLabel(session.status ?? "active")} tone={getStatusTone(session.status)} />
                {visibleActiveTask?.status ? <StatusBadge label={formatStatusLabel(visibleActiveTask.status)} tone={getStatusTone(visibleActiveTask.status)} pulse={isTaskControllable(visibleActiveTask.status)} /> : null}
                {taskCount !== undefined ? <span>{taskCount} 个任务</span> : null}
                {composerContext?.model ? <span>{composerContext.model}</span> : null}
                {composerContext?.permissionMode ? <span>审批：{composerContext.permissionMode}</span> : null}
              </div>
            </div>
            <div className="session-chat-actions" aria-label="会话操作">
              <Button
                size="sm"
                variant="secondary"
                loading={taskBusyAction === "refresh"}
                disabled={!visibleActiveTask || !onRefreshTask}
                onClick={() => {
                  void onRefreshTask?.();
                }}
              >
                刷新任务
              </Button>
              <Button
                size="sm"
                variant="secondary"
                disabled={!onRefreshTrace}
                loading={busyId === "trace"}
                onClick={() => {
                  void onRefreshTrace?.();
                }}
              >
                刷新诊断
              </Button>
              <Button
                size="sm"
                variant="danger"
                loading={taskBusyAction === "stop"}
                disabled={!visibleActiveTask || !isTaskControllable(visibleActiveTask.status) || !onStopTask}
                onClick={() => {
                  if (visibleActiveTask) {
                    void onStopTask?.(visibleActiveTask.id);
                  }
                }}
              >
                停止任务
              </Button>
            </div>
          </header>

          <section className="session-console" aria-label="运行时控制台">
            <header className="session-console-heading">
              <div>
                <p className="session-kicker">活动流</p>
                <h2>消息与操作</h2>
              </div>
              <span>{activityItems.length} 个事件</span>
            </header>
            <div className="message-stream message-stream-chat-only" aria-label="会话消息">
              <ConversationTaskDigest
                activeTask={visibleActiveTask}
                patches={patches}
                backgroundJobs={backgroundJobs}
                composerContext={composerContext}
              />
              {messagesLoading && activityItems.length === 0 ? (
                <div className="message-stream-loading" aria-label="加载消息">
                  <div className="message-stream-loading-bar" />
                </div>
              ) : activityItems.length === 0 ? (
                <div className="message-stream-empty">
                  <p className="session-kicker">空白会话</p>
                  <h2>还没有消息</h2>
                  <p>从下方输入区发送第一条消息。</p>
                </div>
              ) : (
                <ConversationActivity
                  items={activityItems}
                  messages={messages}
                  activeTask={visibleActiveTask}
                  messagesLoading={messagesLoading}
                  onApprove={onApprove}
                  onReject={onReject}
                  onLoadPatch={onLoadPatch}
                  onCopyPatchPath={onCopyPatchPath}
                  onCopyRuntimeText={onCopyRuntimeText}
                  onRefreshCommandJob={onRefreshCommandJob}
                  onStopCommandJob={onStopCommandJob}
                  busyId={busyId}
                />
              )}
            </div>
          </section>
        </section>

        {isWorkspacePaneVisible ? (
          <button
            aria-label="调整右侧工作区宽度"
            className="session-sidebar-resizer"
            onPointerDown={startWorkspacePaneResize}
            type="button"
          >
            <GripVertical size={14} aria-hidden="true" />
          </button>
        ) : null}

        {isWorkspacePaneVisible ? (
          <aside className="session-runtime-column session-workspace-pane" aria-label="工作区侧栏">
            <header className="session-pane-chrome">
              <nav className="session-pane-tabs" aria-label="工作区页签" role="tablist">
                {workspacePaneTabs.map((tab) => {
                  const PaneIcon = tab.icon;
                  return (
                    <button
                      key={tab.id}
                      aria-selected={workspacePane === tab.id}
                      className="session-pane-tab"
                      onClick={() => selectWorkspacePane(tab.id)}
                      role="tab"
                      title={tab.description}
                      type="button"
                    >
                      <PaneIcon size={15} aria-hidden="true" />
                      <span>{tab.label}</span>
                      {tab.count ? <em>{tab.count}</em> : null}
                    </button>
                  );
                })}
              </nav>
              <button
                aria-label="隐藏右侧工作区"
                className="session-pane-action"
                disabled={isFilesFocused}
                onClick={() => setWorkspacePaneCollapsed(true)}
                title="隐藏右侧工作区"
                type="button"
              >
                <PanelRightClose size={16} aria-hidden="true" />
              </button>
            </header>

            <div className="session-pane-body">
              {workspacePane === "home" ? (
                <section className="session-pane-home" aria-label="工作区入口">
                  {workspacePaneTabs.filter((tab) => tab.id !== "home").map((tab) => {
                    const PaneIcon = tab.icon;
                    return (
                      <button
                        key={tab.id}
                        className="session-pane-home-card"
                        onClick={() => selectWorkspacePane(tab.id)}
                        type="button"
                      >
                        <PaneIcon size={22} aria-hidden="true" />
                        <strong>{tab.label}</strong>
                        <span>{tab.description}</span>
                        {tab.count ? <em>{tab.count}</em> : null}
                      </button>
                    );
                  })}
                </section>
              ) : null}

              {workspacePane === "files" ? (
                <section className="session-files-workspace" aria-label="文件工作区">
                  <FileWorkspacePanel
                    workspaceRoot={workspacePath}
                    workspaceLabel={workspaceLabel}
                    relatedFiles={normalizedRelatedFiles}
                    focused={isFilesFocused}
                    onToggleFocus={toggleFileFocus}
                  />
                </section>
              ) : null}

              {["review", "terminal", "git"].includes(workspacePane) ? (
                <SessionWorkspaceToolDock
                  activeTask={visibleActiveTask}
                  patches={patches}
                  backgroundJobs={backgroundJobs}
                  runtimeItems={runtimeItems}
                  worktreeStatus={worktreeStatus}
                  worktreeDiff={worktreeDiff}
                  worktreeBusyAction={worktreeBusyAction}
                  worktreeError={worktreeError}
                  composerContext={composerContext}
                  onLoadPatch={onLoadPatch}
                  onRefreshCommandJob={onRefreshCommandJob}
                  onStopCommandJob={onStopCommandJob}
                  onRefreshWorktree={onRefreshWorktree}
                  onLoadWorktreeDiff={onLoadWorktreeDiff}
                  onMergeWorktree={onMergeWorktree}
                  onCleanupWorktree={onCleanupWorktree}
                  busyId={busyId}
                  activeTool={toolPane}
                  onActiveToolChange={(tool) => setWorkspacePane(tool)}
                  showChrome={false}
                />
              ) : null}

              {showDiagnosticsPane ? (
                <section className="session-pane-runtime session-pane-diagnostics" aria-label="运行态详情" hidden={workspacePane === "home"}>
                  <RuntimeCockpitPanel
                    activeTask={visibleActiveTask}
                    approvals={approvals}
                    patches={patches}
                    traces={traces}
                    contextPreview={contextPreview}
                    taskBusyAction={taskBusyAction}
                    onRefreshTask={onRefreshTask}
                    onPauseTask={onPauseTask}
                    onResumeTask={onResumeTask}
                  />
                  {visibleActiveTask ? <TaskProgressPanel activeTask={visibleActiveTask} patches={patches} /> : null}
                  <AgentCollaborationPanel collaboration={collaboration} expectAgentWork={expectsAgentWork(activeTask)} />

                  {visibleRuntimeLanes.length ? (
                    <section className="session-runtime-lanes" aria-label="运行态摘要">
                      {visibleRuntimeLanes.map((lane) => (
                        <article className="session-runtime-lane" data-lane={lane.id} key={lane.id}>
                          <header>
                            <div>
                              <p className="session-kicker">{lane.eyebrow}</p>
                              <h3>{lane.title}</h3>
                            </div>
                            <span>{lane.items.length}</span>
                          </header>
                          {lane.id === "trace" ? (
                            <TraceFilterBar
                              filter={traceFilter}
                              onChange={setTraceFilter}
                              taskIds={uniqueTaskIds}
                              agentTypes={uniqueAgentTypes}
                            />
                          ) : null}
                          {lane.items.length > 0 ? (
                            <ul className="session-runtime-lane-items">
                              {lane.items.map((item) => (
                                <RuntimeLaneSummaryRow
                                  key={item.id}
                                  item={item}
                                  onApprove={onApprove}
                                  onReject={onReject}
                                  onLoadPatch={onLoadPatch}
                                  onCopyRuntimeText={onCopyRuntimeText}
                                  busyId={busyId}
                                />
                              ))}
                            </ul>
                          ) : (
                            <div className="session-runtime-lane-empty">
                              <strong>{lane.emptyTitle}</strong>
                              <span>{lane.emptyText}</span>
                            </div>
                          )}
                        </article>
                      ))}
                    </section>
                  ) : null}
                </section>
              ) : null}
            </div>
          </aside>
        ) : (
          <button
            aria-label="显示右侧工作区"
            className="session-sidebar-restore"
            onClick={() => setWorkspacePaneCollapsed(false)}
            type="button"
          >
            <PanelRightOpen size={16} aria-hidden="true" />
            <span>工作区</span>
          </button>
        )}
      </section>
    </main>
  );
}

export default SessionWorkspace;
