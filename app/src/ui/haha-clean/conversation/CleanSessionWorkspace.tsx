import { useCallback, useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import { ArrowDown, PanelRightClose, PanelRightOpen } from "lucide-react";
import { buildConversationActivity } from "../../workbench/workspaces/session/ConversationActivity";
import { FileWorkspacePanel } from "../../workbench/workspaces/session/FileWorkspacePanel";
import { buildRuntimeItems } from "../../workbench/workspaces/session/runtimeItemBuilder";
import type {
  ConversationActivityItem,
  RuntimeTimelineItem,
  SessionWorkspaceMessage,
  SessionWorkspaceProps,
} from "../../workbench/workspaces/session/types";
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

const QUIET_INLINE_TOOL_NAMES = new Set([
  "read_file",
  "list_dir",
  "list_directory",
  "git_status",
  "git_diff",
  "search_files",
  "code_search",
]);

function normalizeToolName(value?: string | null) {
  return String(value ?? "").trim().toLowerCase().replace(/\s+/g, "_");
}

function stripToolEntityId(value?: string | null) {
  const text = String(value ?? "").trim();
  if (!text) return "";
  return text.replace(/^(message:|runtime:|tool:|command:|tool_activity:|tool_result:|tool_use:)/, "");
}

function metadataText(message: SessionWorkspaceMessage, key: string) {
  const value = message.metadata?.[key];
  return typeof value === "string" ? value.trim() : "";
}

function parseRecord(value: string) {
  try {
    const parsed = JSON.parse(value);
    return parsed && typeof parsed === "object" ? parsed as Record<string, unknown> : null;
  } catch {
    return null;
  }
}

function readRecordText(record: Record<string, unknown> | null, keys: string[]) {
  if (!record) return "";
  for (const key of keys) {
    const value = record[key];
    if (typeof value === "string" && value.trim()) return value.trim();
    if (typeof value === "number" && Number.isFinite(value)) return String(value);
  }
  return "";
}

function toolInputFingerprint(value?: string | null) {
  const text = String(value ?? "").trim();
  if (!text) return "";
  const record = parseRecord(text);
  const structured = readRecordText(record, ["path", "file", "cwd", "root", "target", "query", "url", "command", "cmd"]);
  return normalizePath(structured || text).toLowerCase();
}

function isQuietCompletedRuntime(item: RuntimeTimelineItem) {
  const name = normalizeToolName(item.toolName || item.title);
  const status = item.status?.toLowerCase() ?? "";
  if (!QUIET_INLINE_TOOL_NAMES.has(name)) return false;
  if (isRuntimeInFlight(status)) return false;
  return !["failed", "error", "cancelled", "rejected"].includes(status);
}

function runtimeMatchKeys(item: RuntimeTimelineItem) {
  return [
    item.toolUseId,
    item.sourceId,
    item.id,
    stripToolEntityId(item.id),
  ].map(stripToolEntityId).filter(Boolean);
}

function messageMatchKeys(message: SessionWorkspaceMessage) {
  return [
    metadataText(message, "toolUseId"),
    metadataText(message, "sourceId"),
    message.id,
    stripToolEntityId(message.id),
  ].map(stripToolEntityId).filter(Boolean);
}

function runtimeFingerprint(item: RuntimeTimelineItem) {
  const name = normalizeToolName(item.toolName || item.title);
  const target = toolInputFingerprint(item.code || item.rawDetail || item.title);
  return name && target ? `${name}:${target}` : "";
}

function messageFingerprint(message: SessionWorkspaceMessage) {
  const name = normalizeToolName(message.toolName || metadataText(message, "toolName"));
  const input = metadataText(message, "inputText") || message.content;
  const target = toolInputFingerprint(input);
  return name && target ? `${name}:${target}` : "";
}

function activityRuntimeItems(items: ConversationActivityItem[]) {
  return items.flatMap((item) => {
    if (item.kind === "runtime") return [item.runtime];
    if (item.kind === "worklog") return item.runtimeItems;
    return [];
  });
}

function shouldHideDuplicateInlineToolMessage(
  message: SessionWorkspaceMessage,
  runtimeIds: Set<string>,
  runtimeFingerprints: Set<string>,
) {
  const kind = message.metadata?.kind;
  if (kind !== "tool_use" && kind !== "tool_activity" && kind !== "tool_result") return false;
  const name = normalizeToolName(message.toolName || metadataText(message, "toolName"));
  if (!QUIET_INLINE_TOOL_NAMES.has(name)) return false;
  const status = message.status?.toLowerCase() ?? "";
  if (message.streaming || isRuntimeInFlight(status) || ["failed", "error", "cancelled", "rejected"].includes(status)) {
    return false;
  }
  if (messageMatchKeys(message).some((key) => runtimeIds.has(key))) return true;
  const fingerprint = messageFingerprint(message);
  return Boolean(fingerprint && runtimeFingerprints.has(fingerprint));
}

export function filterCleanDuplicateToolMessages(items: ConversationActivityItem[]) {
  const quietRuntimes = activityRuntimeItems(items).filter(isQuietCompletedRuntime);
  if (!quietRuntimes.length) return items;
  const runtimeIds = new Set(quietRuntimes.flatMap(runtimeMatchKeys));
  const runtimeFingerprints = new Set(quietRuntimes.map(runtimeFingerprint).filter(Boolean));
  return items.filter((item) => (
    item.kind !== "message" ||
    !shouldHideDuplicateInlineToolMessage(item.message, runtimeIds, runtimeFingerprints)
  ));
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
    () => filterCleanDuplicateToolMessages(buildConversationActivity(messages, visibleRuntimeItems)),
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
