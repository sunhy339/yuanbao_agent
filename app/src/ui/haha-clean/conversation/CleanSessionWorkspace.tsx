import { useCallback, useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import { ArrowDown, PanelRightClose, PanelRightOpen } from "lucide-react";
import { buildConversationActivity } from "../../workbench/workspaces/session/ConversationActivity";
import { FileWorkspacePanel } from "../../workbench/workspaces/session/FileWorkspacePanel";
import { buildRuntimeItems } from "../../workbench/workspaces/session/runtimeItemBuilder";
import type { RuntimeTimelineItem, SessionWorkspaceProps } from "../../workbench/workspaces/session/types";
import { isRuntimeInFlight } from "../../workbench/workspaces/session/utils";
import { CleanActivityItem } from "./CleanConversation";

function normalizePath(value: string) {
  return value.replace(/\\/g, "/").replace(/^[MADRCU?!]{1,2}\s+/, "").trim();
}

function unique(values: string[]) {
  return Array.from(new Set(values.map((value) => value.trim()).filter(Boolean)));
}

function isVisibleRuntime(item: RuntimeTimelineItem) {
  if (item.superseded) return false;
  if (item.kind === "trace") {
    return ["failed", "error", "warning", "cancelled"].includes(item.status?.toLowerCase() ?? "");
  }
  return item.visibility !== "panel";
}

export function CleanSessionWorkspace({
  session,
  messages,
  activeTask,
  contextPreview,
  approvals,
  patches,
  traces,
  toolCalls,
  backgroundJobs,
  onApprove,
  onReject,
  onLoadPatch,
  onCopyRuntimeText,
  onQuoteMessage,
  onRefreshCommandJob,
  onStopCommandJob,
  worktreeStatus,
  composerContext,
  busyId,
  messagesLoading,
}: SessionWorkspaceProps) {
  const [paneOpen, setPaneOpen] = useState(true);
  const [paneWidth, setPaneWidth] = useState(620);
  const scrollRef = useRef<HTMLElement | null>(null);
  const runtimeItems = useMemo(
    () => buildRuntimeItems({ session, activeTask, contextPreview, approvals, patches, traces, toolCalls, backgroundJobs }),
    [activeTask, approvals, backgroundJobs, contextPreview, patches, session, toolCalls, traces],
  );
  const running = isRuntimeInFlight(activeTask?.status);
  const visibleRuntimeItems = useMemo(
    () => runtimeItems.filter((item) => isVisibleRuntime(item) && !(running && item.kind === "task")),
    [runtimeItems, running],
  );
  const activityItems = useMemo(
    () => buildConversationActivity(messages, visibleRuntimeItems),
    [messages, visibleRuntimeItems],
  );
  const workspaceRoot = composerContext?.cwd || activeTask?.activeWorktree?.worktreePath || "";
  const relatedFiles = unique([
    ...(activeTask?.changedFiles?.map((file) => file.path) ?? []),
    ...((patches ?? []).flatMap((patch) => (patch.files ?? []).map((file) => file.path ?? "")) as string[]),
    ...(worktreeStatus?.files ?? []),
  ].map(normalizePath));

  useEffect(() => {
    document.documentElement.style.setProperty("--hc-side-reserve", paneOpen ? `${paneWidth}px` : "0px");
    return () => {
      document.documentElement.style.removeProperty("--hc-side-reserve");
    };
  }, [paneOpen, paneWidth]);

  const startPaneResize = useCallback((event: ReactPointerEvent<HTMLDivElement>) => {
    if (!paneOpen) return;
    event.preventDefault();
    const layout = event.currentTarget.closest(".hc-session") as HTMLElement | null;
    const rect = layout?.getBoundingClientRect();
    const maxWidth = Math.max(420, Math.min(900, Math.round((rect?.width ?? 1300) * 0.68)));

    const move = (moveEvent: PointerEvent) => {
      if (!rect) return;
      const nextWidth = Math.round(rect.right - moveEvent.clientX);
      setPaneWidth(Math.min(maxWidth, Math.max(360, nextWidth)));
    };
    const stop = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", stop);
    };

    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", stop, { once: true });
  }, [paneOpen]);

  if (!session) {
    return (
      <main className="hc-session hc-session-empty">
        <h1>新建会话</h1>
        <p>开始一个新的编码会话。</p>
      </main>
    );
  }

  return (
    <main className="hc-session" data-pane={paneOpen ? "open" : "closed"}>
      <section className="hc-session-scroll" ref={scrollRef}>
        <div className="hc-transcript">
          {activityItems.length ? (
            activityItems.map((item) => (
              <CleanActivityItem
                key={item.id}
                item={item}
                onApprove={onApprove}
                onReject={onReject}
                onLoadPatch={onLoadPatch}
                onCopyRuntimeText={onCopyRuntimeText}
                onQuoteMessage={onQuoteMessage}
                onRefreshCommandJob={onRefreshCommandJob}
                onStopCommandJob={onStopCommandJob}
                busyId={busyId}
              />
            ))
          ) : messagesLoading ? (
            <div className="hc-empty-note">正在加载会话消息...</div>
          ) : (
            <div className="hc-empty-note">还没有消息，直接从下方开始。</div>
          )}
        </div>
        <button
          type="button"
          className="hc-bottom-jump"
          aria-label="跳到底部"
          onClick={() => scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" })}
        >
          <ArrowDown size={17} />
        </button>
      </section>
      {paneOpen ? (
        <>
          <div
            className="hc-session-resizer"
            role="separator"
            aria-label="调整文件区域宽度"
            aria-orientation="vertical"
            onPointerDown={startPaneResize}
          />
          <aside className="hc-file-pane">
            <header>
              <strong>项目目录</strong>
              {running ? <span>运行中</span> : null}
              <button type="button" aria-label="隐藏文件区" onClick={() => setPaneOpen(false)}>
                <PanelRightClose size={16} />
              </button>
            </header>
            <FileWorkspacePanel workspaceRoot={workspaceRoot} workspaceLabel={workspaceRoot || "项目"} relatedFiles={relatedFiles} />
          </aside>
        </>
      ) : (
        <button type="button" className="hc-pane-restore" onClick={() => setPaneOpen(true)}>
          <PanelRightOpen size={16} />
          文件
        </button>
      )}
    </main>
  );
}
