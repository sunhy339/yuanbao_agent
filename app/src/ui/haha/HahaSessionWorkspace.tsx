import { useEffect, useMemo, useRef, useState } from "react";
import { ArrowDown, PanelRightClose, PanelRightOpen } from "lucide-react";
import { buildRuntimeItems } from "../workbench/workspaces/session/runtimeItemBuilder";
import { buildConversationActivity } from "../workbench/workspaces/session/ConversationActivity";
import { FileWorkspacePanel } from "../workbench/workspaces/session/FileWorkspacePanel";
import type { RuntimeTimelineItem, SessionWorkspaceProps } from "../workbench/workspaces/session/types";
import { isTaskControllable } from "../workbench/workspaces/session/utils";
import { HahaActivityItem } from "./HahaBlocks";
import "./haha.css";

function normalizeWorkspaceRelativePath(path: string) {
  return path.replace(/\\/g, "/").replace(/^[MADRCU?!]{1,2}\s+/, "").trim();
}

function unique(values: string[]) {
  return Array.from(new Set(values.map((value) => value.trim()).filter(Boolean)));
}

function isRuntimeVisible(item: RuntimeTimelineItem) {
  if (item.superseded) return false;
  if (item.kind === "trace") {
    const status = item.status?.toLowerCase();
    return Boolean(status && ["failed", "error", "warning", "cancelled"].includes(status));
  }
  return item.visibility !== "panel";
}

export function HahaSessionWorkspace({
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
  onRefreshCommandJob,
  onStopCommandJob,
  worktreeStatus,
  composerContext,
  busyId,
  messagesLoading,
}: SessionWorkspaceProps) {
  const [paneOpen, setPaneOpen] = useState(true);
  const scrollRef = useRef<HTMLElement | null>(null);

  const runtimeItems = useMemo(
    () => buildRuntimeItems({ session, activeTask, contextPreview, approvals, patches, traces, toolCalls, backgroundJobs }),
    [activeTask, approvals, backgroundJobs, contextPreview, patches, session, toolCalls, traces],
  );
  const activityItems = useMemo(
    () => buildConversationActivity(messages, runtimeItems.filter(isRuntimeVisible)),
    [messages, runtimeItems],
  );
  const workspacePath = composerContext?.cwd || activeTask?.activeWorktree?.worktreePath || "";
  const relatedFiles = unique([
    ...(activeTask?.changedFiles?.map((file) => file.path) ?? []),
    ...((patches ?? []).flatMap((patch) => (patch.files ?? []).map((file) => file.path ?? "")) as string[]),
    ...(worktreeStatus?.files ?? []),
  ].map(normalizeWorkspaceRelativePath));
  const running = isTaskControllable(activeTask?.status);

  useEffect(() => {
    document.documentElement.style.setProperty("--session-composer-side-reserve", paneOpen ? "clamp(520px, 42vw, 760px)" : "0px");
    return () => {
      document.documentElement.style.removeProperty("--session-composer-side-reserve");
    };
  }, [paneOpen]);

  if (!session) {
    return (
      <main className="haha-session haha-session-empty">
        <h1>新建会话</h1>
        <p>开始一个新的编码会话。</p>
      </main>
    );
  }

  return (
    <main className="haha-session" data-pane={paneOpen ? "open" : "closed"}>
      <section className="haha-session-main" ref={scrollRef}>
        <div className="haha-transcript">
          {activityItems.length ? (
            activityItems.map((item) => (
              <HahaActivityItem
                key={item.id}
                item={item}
                onLoadPatch={onLoadPatch}
                onCopyRuntimeText={onCopyRuntimeText}
                onApprove={onApprove}
                onReject={onReject}
                onRefreshCommandJob={onRefreshCommandJob}
                onStopCommandJob={onStopCommandJob}
                busyId={busyId}
              />
            ))
          ) : messagesLoading ? (
            <div className="haha-empty-note">正在加载会话消息...</div>
          ) : (
            <div className="haha-empty-note">还没有消息，直接从下方开始。</div>
          )}
        </div>
        <button
          type="button"
          className="haha-bottom-button"
          onClick={() => scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" })}
        >
          <ArrowDown size={17} />
        </button>
      </section>
      {paneOpen ? (
        <aside className="haha-file-pane">
          <header>
            <strong>项目目录</strong>
            {running ? <span>运行中</span> : null}
            <button type="button" onClick={() => setPaneOpen(false)} aria-label="隐藏文件区">
              <PanelRightClose size={16} />
            </button>
          </header>
          <FileWorkspacePanel
            workspaceRoot={workspacePath}
            workspaceLabel={workspacePath || "项目"}
            relatedFiles={relatedFiles}
          />
        </aside>
      ) : (
        <button type="button" className="haha-pane-restore" onClick={() => setPaneOpen(true)}>
          <PanelRightOpen size={16} />
          <span>文件</span>
        </button>
      )}
    </main>
  );
}
