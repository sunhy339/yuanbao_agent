import {
  type CSSProperties,
  type PointerEvent as ReactPointerEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { ArrowDown, Files, GripVertical, PanelRightClose, PanelRightOpen } from "lucide-react";
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
} from "./utils";
import { shouldDisplayTaskScaffold } from "./taskPhase";
import { buildRuntimeItems } from "./runtimeItemBuilder";
import { buildConversationActivity, ConversationActivity } from "./ConversationActivity";
import { AgentCollaborationPanel } from "./AgentCollaborationPanel";
import { ConversationTaskDigest } from "./ConversationTaskDigest";
import { FileWorkspacePanel } from "./FileWorkspacePanel";
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
  return compactText(item.summary || getRuntimeKindLabel(item.kind), 86);
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

function getMessageMetadataKind(message: { metadata?: Record<string, unknown> }) {
  const kind = message.metadata?.kind;
  return typeof kind === "string" ? kind : "";
}

const LOW_VALUE_TOOL_ACTIVITY_NAMES = new Set([
  "read_file",
  "list_dir",
  "list_directory",
  "git_status",
  "git_diff",
  "search_files",
  "code_search",
]);

function isFinishedLowValueToolActivity(message: SessionWorkspaceProps["messages"][number]) {
  if (getMessageMetadataKind(message) !== "tool_activity") {
    return false;
  }
  // Keep chat-compatible process blocks visible; users need to see the model's
  // cadence of thinking/tool usage in the main transcript.
  return false;
}

function isFinishedQuietProbeMessage(message: SessionWorkspaceProps["messages"][number]) {
  if (getMessageMetadataKind(message) !== "tool_activity") {
    return false;
  }
  const toolName = message.toolName?.toLowerCase() ?? "";
  if (!LOW_VALUE_TOOL_ACTIVITY_NAMES.has(toolName)) {
    return false;
  }
  if (message.metadata?.isError === true) {
    return false;
  }
  const status = message.status?.toLowerCase() ?? "";
  return !["running", "started", "pending", "queued", "waiting_approval"].includes(status);
}

function isImportantTraceForChat(item: RuntimeTimelineItem) {
  const status = item.status?.toLowerCase() ?? "";
  if (["failed", "error", "warning", "cancelled", "rejected"].includes(status)) {
    return true;
  }
  const haystack = compactMeta([item.title, item.summary, item.code, item.rawDetail, ...(item.meta ?? [])])
    .join("\n")
    .toLowerCase();
  return /runtime error|provider error|mcp error|task failed|permission|approval|blocked|timeout|recovery|failed|error/.test(haystack);
}

function isRuntimeItemVisibleInChat(item: RuntimeTimelineItem) {
  if (item.kind === "trace") {
    return isImportantTraceForChat(item);
  }
  return isChatVisibleEvent(
    {
      taskId: item.taskId ?? "root",
      visibility: item.visibility,
    },
    new Set<string>(),
  );
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

/*
 * Historical review/git/terminal dock code intentionally removed from the visible
 * workspace. Diff, command, approval, and failure details now live in the main
 * chat stream; the right pane is file browsing only.
 */

export function SessionWorkspace({
  session,
  messages,
  activeTask,
  contextPreview,
  approvals,
  patches,
  traces,
  toolCalls,
  collaboration,
  backgroundJobs,
  onApprove,
  onReject,
  onLoadPatch,
  onCopyPatchPath,
  onCopyRuntimeText,
  onRefreshCommandJob,
  onStopCommandJob,
  onRefreshTask,
  onStopTask,
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
  const conversationMessages = useMemo(
    () => messages.filter((message) => !isFinishedLowValueToolActivity(message)),
    [messages],
  );
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
    const chatVisibleItems = runtimeItems.filter(isRuntimeItemVisibleInChat);
    const visibleChatItems = chatVisibleItems;
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
        return isImportantTraceForChat(item);
      }
      if (item.kind === "patch" || item.id.startsWith("task-files:")) {
        return true;
      }
      if (item.kind === "approval") {
        return true;
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
      conversationMessages,
      collapsedChatItems.filter((item) => !(item.kind === "tool" && item.groupKey && hiddenCommandGroups.has(item.groupKey))),
    );
  }, [conversationMessages, runtimeItems]);
  const [workspacePaneCollapsed, setWorkspacePaneCollapsed] = useState(false);
  const [workspacePaneWidthPx, setWorkspacePaneWidthPx] = useState<number | null>(null);
  const [workspacePaneResizing, setWorkspacePaneResizing] = useState(false);
  const workspaceRootRef = useRef<HTMLElement | null>(null);
  const conversationColumnRef = useRef<HTMLElement | null>(null);
  const [showJumpToBottom, setShowJumpToBottom] = useState(false);
  const isWorkspacePaneVisible = !workspacePaneCollapsed;
  const updateJumpToBottomState = useCallback(() => {
    const column = conversationColumnRef.current;
    if (!column) {
      setShowJumpToBottom(false);
      return;
    }
    const rect = column.getBoundingClientRect();
    document.documentElement.style.setProperty("--session-scroll-bottom-left", `${Math.round(rect.left + rect.width / 2)}px`);
    const distanceToBottom = column.scrollHeight - column.scrollTop - column.clientHeight;
    setShowJumpToBottom(distanceToBottom > 220);
  }, []);
  const scrollConversationToBottom = useCallback(() => {
    const column = conversationColumnRef.current;
    if (!column) {
      return;
    }
    column.scrollTo({ top: column.scrollHeight, behavior: "smooth" });
    setShowJumpToBottom(false);
  }, []);
  const startWorkspacePaneResize = useCallback(
    (event: ReactPointerEvent<HTMLButtonElement>) => {
      if (workspacePaneCollapsed) {
        return;
      }
      const grid = event.currentTarget.closest(".session-workbench-grid") as HTMLElement | null;
      const rect = grid?.getBoundingClientRect();
      const gridWidth = rect?.width || window.innerWidth || 1200;
      const minWidth = Math.min(720, Math.max(560, gridWidth * 0.3));
      const maxWidth = Math.max(minWidth, gridWidth - 440);
      const startWidth = workspacePaneWidthPx ?? Math.min(maxWidth, Math.max(minWidth, gridWidth * 0.42));
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
    [workspacePaneCollapsed, workspacePaneWidthPx],
  );
  const workspaceGridStyle =
    workspacePaneWidthPx && !workspacePaneCollapsed
      ? ({ "--session-workspace-pane-width": `${workspacePaneWidthPx}px` } as CSSProperties)
      : undefined;

  useEffect(() => {
    const updateComposerReserve = () => {
      if (workspacePaneCollapsed) {
        document.documentElement.style.setProperty("--session-composer-side-reserve", "0px");
        return;
      }

      const root = workspaceRootRef.current;
      const pane = root?.querySelector<HTMLElement>(".session-workspace-pane");
      const grid = root?.querySelector<HTMLElement>(".session-workbench-grid");
      const appMain = root?.closest<HTMLElement>(".yb-app-main, .workbench-main");
      const resizer = root?.querySelector<HTMLElement>(".session-sidebar-resizer");
      const appMainRect = appMain?.getBoundingClientRect();
      const gridRect = grid?.getBoundingClientRect();
      const paneRect = pane?.getBoundingClientRect();
      const resizerRect = resizer?.getBoundingClientRect();

      if (gridRect && paneRect) {
        const rightEdge = appMainRect?.right ?? gridRect.right;
        const splitLeft = resizerRect?.left ?? paneRect.left;
        const reserve = Math.max(0, rightEdge - splitLeft);
        document.documentElement.style.setProperty("--session-composer-side-reserve", `${Math.ceil(reserve)}px`);
        return;
      }

      const fallback = workspacePaneWidthPx ? `${workspacePaneWidthPx}px` : "clamp(620px, 42vw, 1040px)";
      document.documentElement.style.setProperty("--session-composer-side-reserve", fallback);
    };

    updateComposerReserve();
    const observer = typeof ResizeObserver !== "undefined" ? new ResizeObserver(updateComposerReserve) : null;
    if (workspaceRootRef.current) {
      observer?.observe(workspaceRootRef.current);
    }
    window.addEventListener("resize", updateComposerReserve);
    return () => {
      observer?.disconnect();
      window.removeEventListener("resize", updateComposerReserve);
      document.documentElement.style.removeProperty("--session-composer-side-reserve");
    };
  }, [workspacePaneCollapsed, workspacePaneWidthPx]);

  const workspaceClassName = [
    "session-workspace",
    "session-workspace-chat-only",
    workspacePaneCollapsed ? "session-workspace-pane-collapsed" : "",
    workspacePaneResizing ? "session-workspace-pane-resizing" : "",
  ]
    .filter(Boolean)
    .join(" ");
  const taskStatus = visibleActiveTask?.status?.toLowerCase();
  const hasSettledPatchOrReview = Boolean(
    (patches ?? []).some((patch) => !isTaskControllable(patch.status)) ||
      visibleActiveTask?.activeWorktree?.lastStatus?.review ||
      visibleActiveTask?.activeWorktree?.lastStatus?.mergeApproval ||
      worktreeDiff?.diffStat ||
      worktreeDiff?.diff ||
      worktreeDiff?.preview,
  );
  const taskHasDigestEvidence = Boolean(
    (visibleActiveTask?.changedFiles?.length ?? 0) > 0 ||
      (visibleActiveTask?.commands?.length ?? 0) > 0 ||
      (visibleActiveTask?.verification?.length ?? 0) > 0,
  );
  const isTerminalTaskStatus = ["completed", "succeeded", "failed", "error", "cancelled", "rejected"].includes(taskStatus ?? "");
  const isPausedWithHandoff = Boolean(
    taskStatus === "paused" &&
      visibleActiveTask?.mainWorkflow &&
      ("automation" in visibleActiveTask.mainWorkflow ||
        "convergence" in visibleActiveTask.mainWorkflow ||
        "userTakeover" in visibleActiveTask.mainWorkflow),
  );
  const hasStandaloneDigestEvidence = Boolean(!visibleActiveTask && ((patches?.length ?? 0) > 0 || worktreeDiff?.diffStat || worktreeDiff?.diff || worktreeDiff?.preview));
  const hasReviewOrDiffEvidence = Boolean(
    visibleActiveTask?.activeWorktree?.lastStatus?.review ||
      visibleActiveTask?.activeWorktree?.lastStatus?.mergeApproval ||
      worktreeDiff?.diffStat ||
      worktreeDiff?.diff ||
      worktreeDiff?.preview,
  );
  const taskCanShowDigest = Boolean(
    visibleActiveTask &&
      (isTerminalTaskStatus ||
        isPausedWithHandoff ||
        (!isTaskControllable(visibleActiveTask.status) && (taskHasDigestEvidence || hasSettledPatchOrReview))),
  );
  const taskDigestReady = Boolean(
    hasStandaloneDigestEvidence ||
      isPausedWithHandoff ||
      (taskCanShowDigest && hasReviewOrDiffEvidence) ||
      (taskCanShowDigest && (taskHasDigestEvidence || hasSettledPatchOrReview)),
  );
  const taskDigest = (
    <ConversationTaskDigest
      activeTask={visibleActiveTask}
      patches={patches}
      backgroundJobs={backgroundJobs}
      composerContext={composerContext}
      worktreeDiff={worktreeDiff}
      onLoadPatch={onLoadPatch}
    />
  );
  const visibleTaskDigest = taskDigestReady ? taskDigest : null;
  const hasCollaborationWork = Boolean(
    (collaboration?.workers?.length ?? 0) > 0 ||
      (collaboration?.childTasks?.length ?? 0) > 0 ||
      (collaboration?.results?.length ?? 0) > 0,
  );
  const visibleCollaborationPanel = hasCollaborationWork ? (
    <AgentCollaborationPanel collaboration={collaboration} />
  ) : null;

  useEffect(() => {
    const column = conversationColumnRef.current;
    if (!column) {
      return;
    }
    updateJumpToBottomState();
    column.addEventListener("scroll", updateJumpToBottomState, { passive: true });
    const observer = typeof ResizeObserver !== "undefined" ? new ResizeObserver(updateJumpToBottomState) : null;
    observer?.observe(column);
    window.addEventListener("resize", updateJumpToBottomState);
    return () => {
      column.removeEventListener("scroll", updateJumpToBottomState);
      observer?.disconnect();
      window.removeEventListener("resize", updateJumpToBottomState);
      document.documentElement.style.removeProperty("--session-scroll-bottom-left");
    };
  }, [updateJumpToBottomState]);

  useEffect(() => {
    const frameId = window.requestAnimationFrame(updateJumpToBottomState);
    return () => window.cancelAnimationFrame(frameId);
  }, [activityItems.length, messagesLoading, taskDigestReady, updateJumpToBottomState]);

  return (
    <main className={workspaceClassName} aria-label="Session" ref={workspaceRootRef}>
      <section className="session-workbench-grid" style={workspaceGridStyle}>
        <section className="session-conversation-column" ref={conversationColumnRef}>
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
                variant="danger"
                loading={taskBusyAction === "stop"}
                disabled={!visibleActiveTask || !isTaskControllable(visibleActiveTask.status) || !onStopTask || taskBusyAction === "stop"}
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
              {messagesLoading && activityItems.length === 0 ? (
                <>
                  <div className="message-stream-loading" aria-label="加载消息">
                    <div className="message-stream-loading-bar" />
                  </div>
                  {visibleCollaborationPanel}
                  {visibleTaskDigest}
                </>
              ) : activityItems.length === 0 ? (
                <>
                  <div className="message-stream-empty">
                    <p className="session-kicker">空白会话</p>
                    <h2>还没有消息</h2>
                    <p>从下方输入区发送第一条消息。</p>
                  </div>
                  {visibleCollaborationPanel}
                  {visibleTaskDigest}
                </>
              ) : (
                <>
                  <ConversationActivity
                    items={activityItems}
                    messages={conversationMessages}
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
                  {visibleCollaborationPanel}
                  {visibleTaskDigest}
                </>
              )}
            </div>
          </section>
          {showJumpToBottom ? (
            <button
              aria-label="回到底部"
              className="session-scroll-bottom-button"
              onClick={scrollConversationToBottom}
              type="button"
            >
              <ArrowDown size={18} strokeWidth={2.1} aria-hidden="true" />
              <span>置底</span>
            </button>
          ) : null}
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
          <aside className="session-runtime-column session-workspace-pane" aria-label="右侧文件工作区">
            <header className="session-pane-chrome">
              <div className="session-pane-title">
                <Files size={15} aria-hidden="true" />
                <span>文件</span>
                {normalizedRelatedFiles.length ? <em>{normalizedRelatedFiles.length}</em> : null}
              </div>
              <div className="session-pane-actions">
                <button
                  aria-label="隐藏右侧工作区"
                  className="session-pane-action"
                  onClick={() => setWorkspacePaneCollapsed(true)}
                  title="隐藏右侧工作区"
                  type="button"
                >
                  <PanelRightClose size={16} aria-hidden="true" />
                </button>
              </div>
            </header>

            <div className="session-pane-body">
              <section className="session-files-workspace" aria-label="文件工作区">
                <FileWorkspacePanel
                  workspaceRoot={workspacePath}
                  workspaceLabel={workspaceLabel}
                  relatedFiles={normalizedRelatedFiles}
                />
              </section>
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
