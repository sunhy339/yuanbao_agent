import { useCallback, useEffect, useMemo, useState } from "react";
import type { WorkspaceFileEntry, WorkspaceFileReadResult } from "@shared";
import { RuntimeClient } from "../../../../lib/runtimeClient";
import { Button, StatusBadge } from "../../../v2/components/ui";
import { formatStatusLabel } from "../../../copy";
import type { SessionWorkspaceProps, RuntimeTimelineItem } from "./types";
import {
  buildCommandOutput,
  compactText,
  getRuntimeKindLabel,
  getStatusTone,
  isBackgroundProbeCommand,
  isSuccessfulRuntimeStatus,
  isTaskControllable,
} from "./utils";
import { shouldDisplayTaskScaffold, expectsAgentWork } from "./taskPhase";
import { buildRuntimeItems } from "./runtimeItemBuilder";
import { buildConversationActivity, ConversationActivity } from "./ConversationActivity";
import { ConversationTaskDigest } from "./ConversationTaskDigest";
import { TaskProgressPanel } from "./TaskProgressPanel";
import { RuntimeCockpitPanel } from "./RuntimeCockpitPanel";
import { AgentCollaborationPanel } from "./AgentCollaborationPanel";
import { TraceFilterBar } from "./TraceFilterBar";
import { WorktreePanel } from "./WorktreePanel";
import { isChatVisibleEvent } from "./visibilityRouting";
import "./session.css";

const workspaceFileClient = new RuntimeClient();
const WORKSPACE_PREVIEW_MAX_BYTES = 64 * 1024;

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
  return path
    .replace(/\\/g, "/")
    .replace(/^[MADRCU?!]{1,2}\s+/, "")
    .replace(/^"(.+)"$/, "$1")
    .trim();
}

function parentWorkspacePath(path: string) {
  const normalized = normalizeWorkspaceRelativePath(path);
  const index = normalized.lastIndexOf("/");
  return index > 0 ? normalized.slice(0, index) : "";
}

function fileNameFromPath(path: string) {
  const normalized = normalizeWorkspaceRelativePath(path);
  return normalized.split("/").filter(Boolean).at(-1) || normalized || ".";
}

function formatFileSize(bytes?: number | null) {
  if (bytes === undefined || bytes === null) {
    return "";
  }
  if (bytes < 1024) {
    return `${bytes} B`;
  }
  if (bytes < 1024 * 1024) {
    return `${(bytes / 1024).toFixed(1)} KB`;
  }
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function canUseTauriInvoke() {
  if (typeof window === "undefined") {
    return false;
  }
  const bridgeWindow = window as typeof window & {
    __TAURI__?: unknown;
    __TAURI_INTERNALS__?: unknown;
  };
  return Boolean(bridgeWindow.__TAURI__ || bridgeWindow.__TAURI_INTERNALS__);
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
        {isBusy ? "差异正在加载。" : "差异暂不可用。请在运行时写入改动后再试一次。"}
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

type SessionToolKey = "files" | "review" | "terminal" | "git" | "browser" | "side-chat";

interface SessionToolCommand {
  id?: string;
  command: string;
  cwd?: string;
  status?: string;
  summary?: string;
  durationMs?: number | null;
  exitCode?: number | null;
  stdoutPath?: string | null;
  stderrPath?: string | null;
}

function SessionWorkspaceToolDock({
  activeTask,
  patches,
  backgroundJobs,
  worktreeStatus,
  worktreeDiff,
  worktreeBusyAction,
  composerContext,
  onLoadPatch,
  onRefreshCommandJob,
  onStopCommandJob,
  onRefreshWorktree,
  onLoadWorktreeDiff,
  onMergeWorktree,
  onCleanupWorktree,
  busyId,
}: {
  activeTask: SessionWorkspaceProps["activeTask"];
  patches?: SessionWorkspaceProps["patches"];
  backgroundJobs?: SessionWorkspaceProps["backgroundJobs"];
  worktreeStatus?: SessionWorkspaceProps["worktreeStatus"];
  worktreeDiff?: SessionWorkspaceProps["worktreeDiff"];
  worktreeBusyAction?: SessionWorkspaceProps["worktreeBusyAction"];
  composerContext?: SessionWorkspaceProps["composerContext"];
  onLoadPatch?: SessionWorkspaceProps["onLoadPatch"];
  onRefreshCommandJob?: SessionWorkspaceProps["onRefreshCommandJob"];
  onStopCommandJob?: SessionWorkspaceProps["onStopCommandJob"];
  onRefreshWorktree?: SessionWorkspaceProps["onRefreshWorktree"];
  onLoadWorktreeDiff?: SessionWorkspaceProps["onLoadWorktreeDiff"];
  onMergeWorktree?: SessionWorkspaceProps["onMergeWorktree"];
  onCleanupWorktree?: SessionWorkspaceProps["onCleanupWorktree"];
  busyId?: string | null;
}) {
  const [activeTool, setActiveTool] = useState<SessionToolKey>("files");
  const [filePath, setFilePath] = useState("");
  const [fileEntries, setFileEntries] = useState<WorkspaceFileEntry[]>([]);
  const [filePreview, setFilePreview] = useState<WorkspaceFileReadResult | null>(null);
  const [fileBusy, setFileBusy] = useState<"list" | "read" | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);
  const [fileBrowserPrimed, setFileBrowserPrimed] = useState(false);
  const canBrowseFiles = canUseTauriInvoke();
  const activeWorktree = activeTask?.activeWorktree;
  const workspacePath = composerContext?.cwd || activeWorktree?.worktreePath || "";
  const branchName = composerContext?.branch || activeWorktree?.branchName || "未识别分支";
  const workspaceLabel = workspacePath || "当前会话未提供工作目录";

  const relatedFiles = uniqueNonEmptyStrings([
    ...(activeTask?.changedFiles?.map((file) => file.path) ?? []),
    ...((patches ?? []).flatMap((patch) => patch.files?.map((file) => file.path) ?? [])),
    ...(worktreeStatus?.files ?? []),
  ]).map(normalizeWorkspaceRelativePath);
  const normalizedRelatedFiles = uniqueNonEmptyStrings(relatedFiles);

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
  const additions = patchStats.additions || taskPatchStats.additions;
  const deletions = patchStats.deletions || taskPatchStats.deletions;
  const patchCount = (patches?.length ?? 0) + (activeTask?.changedFiles?.length ? 1 : 0);
  const firstPatchId = patches?.[0]?.id;
  const dirtyFiles = worktreeStatus?.dirtyFiles ?? normalizedRelatedFiles.length;
  const diffLoaded = Boolean(
    worktreeDiff && !worktreeDiff.error && (worktreeDiff.diffStat || worktreeDiff.diff || worktreeDiff.files?.length),
  );
  const canRequestMerge = Boolean(activeWorktree && onMergeWorktree && diffLoaded && dirtyFiles === 0);
  const canCleanup = Boolean(activeWorktree && onCleanupWorktree && dirtyFiles === 0);
  const commandItems: SessionToolCommand[] = [
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
  ];
  const latestCommand = commandItems.at(-1);
  const latestDiffPreview = worktreeDiff?.preview || worktreeDiff?.diff || "";
  const toolTabs: Array<{ id: SessionToolKey; label: string; description: string; count?: number }> = [
    { id: "files", label: "文件", description: "浏览项目文件", count: normalizedRelatedFiles.length || fileEntries.length },
    { id: "review", label: "审查", description: "查看代码改动", count: patchCount },
    { id: "terminal", label: "终端", description: "命令与验证", count: commandItems.length },
    { id: "git", label: "Git", description: "分支与提交", count: dirtyFiles },
    { id: "browser", label: "浏览器", description: "预览入口" },
    { id: "side-chat", label: "侧聊", description: "独立上下文" },
  ];

  const loadDirectory = useCallback(
    async (path: string) => {
      if (!workspacePath || !canBrowseFiles) {
        return;
      }
      setFileBusy("list");
      setFileError(null);
      try {
        const result = await workspaceFileClient.workspaceFileList({
          workspaceRoot: workspacePath,
          path,
          maxEntries: 200,
        });
        setFilePath(result.path);
        setFileEntries(result.entries);
        setFilePreview(null);
      } catch (error) {
        setFileError(error instanceof Error ? error.message : String(error));
      } finally {
        setFileBusy(null);
      }
    },
    [canBrowseFiles, workspacePath],
  );

  const loadFile = useCallback(
    async (path: string) => {
      if (!workspacePath || !canBrowseFiles) {
        return;
      }
      const normalizedPath = normalizeWorkspaceRelativePath(path);
      setFileBusy("read");
      setFileError(null);
      try {
        const result = await workspaceFileClient.workspaceFileRead({
          workspaceRoot: workspacePath,
          path: normalizedPath,
          maxBytes: WORKSPACE_PREVIEW_MAX_BYTES,
        });
        setFilePreview(result);
        setFilePath(parentWorkspacePath(normalizedPath));
      } catch (error) {
        setFileError(error instanceof Error ? error.message : String(error));
      } finally {
        setFileBusy(null);
      }
    },
    [canBrowseFiles, workspacePath],
  );

  useEffect(() => {
    setFileBrowserPrimed(false);
    setFilePath("");
    setFileEntries([]);
    setFilePreview(null);
    setFileError(null);
  }, [workspacePath]);

  useEffect(() => {
    if (activeTool !== "files" || fileBrowserPrimed || !workspacePath || !canBrowseFiles) {
      return;
    }
    setFileBrowserPrimed(true);
    void loadDirectory("");
  }, [activeTool, canBrowseFiles, fileBrowserPrimed, loadDirectory, workspacePath]);

  return (
    <section className="session-tool-dock session-tool-dock-live" aria-label="工作区工具">
      <header className="session-tool-dock-header">
        <div>
          <p className="session-kicker">工作区</p>
          <h2>工具</h2>
        </div>
        <span title={branchName}>{branchName}</span>
      </header>

      <nav className="session-tool-tabs" role="tablist" aria-label="工作区工具类型">
        {toolTabs.map((tab) => (
          <button
            key={tab.id}
            aria-selected={activeTool === tab.id}
            className="session-tool-tab"
            data-tool={tab.id}
            onClick={() => setActiveTool(tab.id)}
            role="tab"
            type="button"
          >
            <span className="session-tool-icon" aria-hidden="true" />
            <span>
              <strong>{tab.label}</strong>
              <small>{tab.description}</small>
            </span>
            {tab.count ? <em>{tab.count}</em> : null}
          </button>
        ))}
      </nav>

      <div className="session-tool-panel" role="tabpanel">
        {activeTool === "files" ? (
          <>
            <div className="session-tool-panel-header">
              <div>
                <strong>文件浏览</strong>
                <small title={workspaceLabel}>{compactText(workspaceLabel, 54)}</small>
              </div>
              <Button
                size="xs"
                variant="secondary"
                disabled={!workspacePath || !canBrowseFiles}
                loading={fileBusy === "list"}
                onClick={() => {
                  void loadDirectory(filePath);
                }}
              >
                刷新
              </Button>
            </div>
            {!canBrowseFiles ? (
              <p className="session-tool-muted">桌面运行时中可浏览目录；当前预览先显示任务关联文件。</p>
            ) : null}
            {fileError ? <p className="session-tool-error">{fileError}</p> : null}
            <div className="session-file-browser">
              <div className="session-file-tree">
                <div className="session-file-bar">
                  <Button
                    size="xs"
                    variant="secondary"
                    disabled={!canBrowseFiles || !filePath}
                    onClick={() => {
                      void loadDirectory(parentWorkspacePath(filePath));
                    }}
                  >
                    上级
                  </Button>
                  <code>{filePath || "."}</code>
                </div>
                {fileEntries.length ? (
                  <ul>
                    {fileEntries.map((entry) => (
                      <li key={`${entry.kind}:${entry.path}`}>
                        <button
                          onClick={() => {
                            if (entry.kind === "directory") {
                              void loadDirectory(entry.path);
                            } else {
                              void loadFile(entry.path);
                            }
                          }}
                          type="button"
                        >
                          <span data-kind={entry.kind} aria-hidden="true" />
                          <strong>{entry.name}</strong>
                          <small>{entry.kind === "directory" ? "目录" : formatFileSize(entry.size)}</small>
                        </button>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="session-tool-muted">还没有目录条目。</p>
                )}
              </div>
              <div className="session-file-preview">
                <strong>{filePreview ? filePreview.path : "文件预览"}</strong>
                {filePreview ? (
                  filePreview.binary ? (
                    <p className="session-tool-muted">这是二进制文件，已跳过文本预览。</p>
                  ) : (
                    <pre>{filePreview.content || ""}</pre>
                  )
                ) : (
                  <p className="session-tool-muted">选择文件后预览前 64KB 文本内容。</p>
                )}
                {filePreview?.truncated ? <small>内容已截断</small> : null}
              </div>
            </div>
            <div className="session-related-files">
              <strong>任务相关文件</strong>
              {normalizedRelatedFiles.length ? (
                <ul>
                  {normalizedRelatedFiles.slice(0, 8).map((path) => (
                    <li key={path}>
                      <button
                        disabled={!canBrowseFiles || !workspacePath}
                        onClick={() => {
                          void loadFile(path);
                        }}
                        type="button"
                      >
                        <span>{fileNameFromPath(path)}</span>
                        <small>{path}</small>
                      </button>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="session-tool-muted">本轮还没有记录关联文件。</p>
              )}
            </div>
          </>
        ) : null}

        {activeTool === "review" ? (
          <>
            <div className="session-tool-panel-header">
              <div>
                <strong>代码审查</strong>
                <small>{patchCount ? `${patchCount} 个改动记录` : "等待代码变更"}</small>
              </div>
              <span className="session-tool-diff-stat">+{additions} -{deletions}</span>
            </div>
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
            {normalizedRelatedFiles.length ? (
              <ul className="session-tool-list">
                {normalizedRelatedFiles.slice(0, 10).map((path) => (
                  <li key={path}>
                    <strong>{fileNameFromPath(path)}</strong>
                    <small>{path}</small>
                  </li>
                ))}
              </ul>
            ) : null}
            {worktreeDiff?.error ? <p className="session-tool-error">{worktreeDiff.error}</p> : null}
            {worktreeDiff?.diffStat ? <p className="session-tool-muted">{worktreeDiff.diffStat}</p> : null}
            {latestDiffPreview ? <pre className="session-tool-code-preview">{latestDiffPreview.slice(0, 5000)}</pre> : null}
          </>
        ) : null}

        {activeTool === "terminal" ? (
          <>
            <div className="session-tool-panel-header">
              <div>
                <strong>命令与验证</strong>
                <small>{latestCommand ? compactText(latestCommand.command, 58) : "还没有运行命令"}</small>
              </div>
            </div>
            {commandItems.length ? (
              <ul className="session-tool-command-list">
                {commandItems.slice(-8).map((command, index) => (
                  <li key={command.id ?? `${command.command}-${index}`} data-status={command.status ?? "recorded"}>
                    <div>
                      <strong>{command.command}</strong>
                      <small>
                        {command.status ? formatStatusLabel(command.status) : "已记录"}
                        {command.exitCode !== undefined && command.exitCode !== null ? ` · exit ${command.exitCode}` : ""}
                        {command.durationMs ? ` · ${command.durationMs}ms` : ""}
                      </small>
                    </div>
                    {command.summary ? <p>{command.summary}</p> : null}
                    {command.cwd ? <code>{command.cwd}</code> : null}
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

        {activeTool === "git" ? (
          <>
            <div className="session-tool-panel-header">
              <div>
                <strong>Git 状态</strong>
                <small>{compactText(branchName, 58)}</small>
              </div>
              <span className="session-tool-diff-stat">{dirtyFiles} 个文件</span>
            </div>
            <p className="session-tool-muted" title={workspaceLabel}>
              {compactText(workspaceLabel, 80)}
            </p>
            <div className="session-tool-actions">
              {activeWorktree && onRefreshWorktree ? (
                <Button
                  size="xs"
                  variant="secondary"
                  loading={worktreeBusyAction === "status"}
                  onClick={() => {
                    void onRefreshWorktree(activeWorktree.id);
                  }}
                >
                  状态
                </Button>
              ) : null}
              {activeWorktree && onLoadWorktreeDiff ? (
                <Button
                  size="xs"
                  variant="secondary"
                  loading={worktreeBusyAction === "diff"}
                  onClick={() => {
                    void onLoadWorktreeDiff(activeWorktree.id, true);
                  }}
                >
                  差异
                </Button>
              ) : null}
              {activeWorktree && onMergeWorktree ? (
                <Button
                  size="xs"
                  variant="secondary"
                  loading={worktreeBusyAction === "requestMergeApproval" || worktreeBusyAction === "merge"}
                  disabled={!canRequestMerge}
                  onClick={() => {
                    void onMergeWorktree(activeWorktree.id);
                  }}
                >
                  合并申请
                </Button>
              ) : null}
              {activeWorktree && onCleanupWorktree ? (
                <Button
                  size="xs"
                  variant="secondary"
                  loading={worktreeBusyAction === "cleanup"}
                  disabled={!canCleanup}
                  onClick={() => {
                    void onCleanupWorktree(activeWorktree.id, false);
                  }}
                >
                  清理
                </Button>
              ) : null}
            </div>
            {worktreeStatus?.error ? <p className="session-tool-error">{worktreeStatus.error}</p> : null}
            {worktreeStatus?.files?.length ? (
              <ul className="session-tool-list">
                {worktreeStatus.files.slice(0, 12).map((file) => (
                  <li key={file}>
                    <strong>{fileNameFromPath(file)}</strong>
                    <small>{normalizeWorkspaceRelativePath(file)}</small>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="session-tool-muted">还没有 Git 扫描结果。</p>
            )}
          </>
        ) : null}

        {activeTool === "browser" ? (
          <div className="session-tool-placeholder">
            <strong>浏览器预览</strong>
            <p>这里会放运行中的本地页面和打开网站入口，方便和主任务结果并排检查。</p>
          </div>
        ) : null}

        {activeTool === "side-chat" ? (
          <div className="session-tool-placeholder">
            <strong>侧边聊天</strong>
            <p>这里会放独立上下文的辅助对话，不污染当前主任务链路。</p>
          </div>
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
        emptyText: "失败中的命令、运行中的检查和最近通过的验证会显示在这里。",
        items: commandsAndTools,
      },
      {
        id: "patches" as const,
        eyebrow: "改动",
        title: "补丁与文件",
        emptyTitle: "当前没有待看的改动",
        emptyText: "代码改动、补丁结果和关键文件变化会出现在这里。",
        items: patchItems,
      },
      {
        id: "trace" as const,
        eyebrow: "诊断",
        title: "异常与信号",
        emptyTitle: "当前没有异常信号",
        emptyText: "只保留失败、恢复、路由变化和需要处理的异常。",
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
  return (
    <main className="session-workspace session-workspace-chat-only" aria-label="Session">
      <section className="session-workbench-grid">
        <section className="session-conversation-column">
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
                  <p className="session-kicker">安静线程</p>
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

        <aside className="session-runtime-column" aria-label="运行态侧栏">
          <SessionWorkspaceToolDock
            activeTask={visibleActiveTask}
            patches={patches}
            backgroundJobs={backgroundJobs}
            worktreeStatus={worktreeStatus}
            worktreeDiff={worktreeDiff}
            worktreeBusyAction={worktreeBusyAction}
            composerContext={composerContext}
            onLoadPatch={onLoadPatch}
            onRefreshCommandJob={onRefreshCommandJob}
            onStopCommandJob={onStopCommandJob}
            onRefreshWorktree={onRefreshWorktree}
            onLoadWorktreeDiff={onLoadWorktreeDiff}
            onMergeWorktree={onMergeWorktree}
            onCleanupWorktree={onCleanupWorktree}
            busyId={busyId}
          />
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
          <WorktreePanel
            worktree={visibleActiveTask?.activeWorktree}
            status={worktreeStatus}
            diff={worktreeDiff}
            busyAction={worktreeBusyAction}
            error={worktreeError}
            onRefresh={onRefreshWorktree}
            onLoadDiff={onLoadWorktreeDiff}
            onMerge={onMergeWorktree}
            onCleanup={onCleanupWorktree}
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
        </aside>
      </section>
    </main>
  );
}

export default SessionWorkspace;
