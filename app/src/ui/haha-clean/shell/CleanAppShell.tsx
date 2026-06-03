import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent, type KeyboardEvent as ReactKeyboardEvent, type PointerEvent as ReactPointerEvent } from "react";
import {
  Bot,
  Check,
  Code2,
  Folder,
  FolderOpen,
  GripVertical,
  MessageSquarePlus,
  Pencil,
  Search,
  Settings,
  Sparkles,
  Trash2,
  Wrench,
  X,
} from "lucide-react";
import { DesktopTitlebar } from "../../workbench/DesktopTitlebar";
import type { AppShellV2Props } from "../../v2/layout/AppShellV2";
import { CleanComposer } from "../composer/CleanComposer";
import { RuntimeClient } from "../../../lib/runtimeClient";
import { basename, statusTone } from "../shared/text";
import { FileWorkspacePanel, type FileWorkspaceTextSelection } from "../../workbench/workspaces/session/FileWorkspacePanel";
import "../clean.css";

const runtimeClient = new RuntimeClient();
const FILE_WORKSPACE_OPEN_EVENT = "haha-clean:open-file-workspace";
const FILE_REFERENCE_EVENT = "haha-clean:add-file-reference";
const FILE_PANE_DEFAULT_WIDTH = 720;
const FILE_PANE_MIN_WIDTH = 420;
const FILE_PANE_MAX_WIDTH = 980;
const FILE_PANE_CHAT_MIN_WIDTH = 560;
const FILE_PANE_RESIZE_STEP = 32;

function initialFilePaneWidth() {
  if (typeof window === "undefined") {
    return FILE_PANE_DEFAULT_WIDTH;
  }
  return Math.min(FILE_PANE_MAX_WIDTH, Math.max(FILE_PANE_MIN_WIDTH, Math.round(window.innerWidth * 0.38)));
}

function filePaneMaxWidth(shellWidth: number) {
  return Math.min(FILE_PANE_MAX_WIDTH, Math.max(FILE_PANE_MIN_WIDTH, shellWidth - FILE_PANE_CHAT_MIN_WIDTH));
}

function clampFilePaneWidth(width: number, shellWidth: number) {
  return Math.round(Math.min(filePaneMaxWidth(shellWidth), Math.max(FILE_PANE_MIN_WIDTH, width)));
}

function normalizePath(value: string) {
  return value.replace(/\\/g, "/").replace(/^[MADRCU?!]{1,2}\s+/, "").trim();
}

function unique(values: string[]) {
  return Array.from(new Set(values.map((value) => value.trim()).filter(Boolean)));
}

function resolveTabKind(activeTabId: string, tabs: AppShellV2Props["tabs"]) {
  const tab = tabs.find((item) => item.id === activeTabId);
  if (tab) return tab.kind;
  if (activeTabId.startsWith("session:")) return "session";
  if (activeTabId.startsWith("system:")) return activeTabId.slice("system:".length);
  return "session";
}

function systemLabel(kind: string) {
  const labels: Record<string, string> = {
    "new-session": "新建会话",
    mcp: "MCP",
    skills: "Skills",
    settings: "设置",
    scheduled: "计划",
    overview: "总览",
    appearance: "外观",
    playground: "组件",
  };
  return labels[kind] ?? kind;
}

function sessionTime(value?: number) {
  if (!value) return "";
  return new Date(value).toLocaleString("zh-CN", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

const FLOATING_TASK_STATUSES = new Set(["running", "started", "planning", "verifying", "waiting_approval", "queued"]);
const QUIET_FLOATING_STEPS = [
  /理解任务目标/,
  /分析任务目标/,
  /整理上下文/,
  /构建上下文/,
  /准备上下文/,
  /准备工具/,
  /规划任务/,
  /任务启动/,
  /等待模型/,
  /思考中/,
  /正在思考/,
  /正在输出回复/,
  /模型正在思考/,
  /任务运行中/,
  /understand(?:ing)? (?:the )?task/i,
  /analy[sz](?:e|ing) (?:the )?task/i,
  /build(?:ing)? context/i,
  /prepar(?:e|ing) context/i,
  /plan(?:ning)? (?:the )?task/i,
  /waiting for (?:the )?model/i,
];

function floatingTaskStatusText(status?: string | null, step?: string | null, stopPending = false) {
  if (stopPending) return "正在停止任务...";
  const normalized = String(status ?? "").trim().toLowerCase();
  if (!FLOATING_TASK_STATUSES.has(normalized)) return null;
  const currentStep = String(step ?? "").trim();
  if (currentStep && !QUIET_FLOATING_STEPS.some((pattern) => pattern.test(currentStep))) {
    return currentStep;
  }
  if (normalized === "running" || normalized === "started") return null;
  const labels: Record<string, string> = {
    planning: "正在规划",
    verifying: "正在验证",
    waiting_approval: "等待审批",
    queued: "排队中",
  };
  return labels[normalized] ?? null;
}

function LoadingSkeleton() {
  return (
    <div className="hc-loading" aria-label="正在加载">
      <span />
      <span />
      <span />
    </div>
  );
}
export function CleanAppShell({
  tabs,
  activeTabId,
  sessions,
  activeSessionId,
  workspaceName,
  composerVisible,
  promptValue,
  onPromptChange,
  onOpenSystemTab,
  onOpenSessionTab,
  onActivateTab,
  onCloseTab,
  onRenameSession,
  onDeleteSession,
  onSubmitPrompt,
  onQueuePrompt,
  onStopPrompt,
  queuedPrompts,
  onGuideQueuedPrompt,
  onQueuedPromptRemove,
  onQueuedPromptMove,
  disabled,
  sending,
  submitting,
  stopPending,
  queuedPromptCount,
  attachments,
  onAttachmentsChange,
  onAttachmentError,
  modelOptions,
  selectedModelId,
  onSelectModel,
  runtimeChildTasks,
  providerLabel,
  cwdLabel,
  permissionLabel,
  permissionMode,
  onPermissionModeChange,
  onWorkspacePathChange,
  useWorktree,
  onUseWorktreeChange,
  worktreeModeBusy,
  runtimeLabel,
  contextLabel,
  contextPreview,
  worktreeStatus,
  fileWorkspaceChangedFiles,
  activeTaskStatus,
  activeTaskCurrentStep,
  loading,
  children,
}: AppShellV2Props) {
  const activeKind = resolveTabKind(activeTabId, tabs);
  const shellComposerVisible = composerVisible && (activeKind === "session" || activeKind === "new-session");
  const dirtyCount = worktreeStatus?.dirtyFiles ?? 0;
  const workspaceRoot = cwdLabel;
  const floatingStatusText = floatingTaskStatusText(activeTaskStatus, activeTaskCurrentStep, stopPending);
  const [sessionQuery, setSessionQuery] = useState("");
  const [renamingSessionId, setRenamingSessionId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [fileReferenceQuery, setFileReferenceQuery] = useState<string | null>(null);
  const [searchedFileReferences, setSearchedFileReferences] = useState<Array<{ path: string }>>([]);
  const [filePaneOpen, setFilePaneOpen] = useState(false);
  const [filePaneWidth, setFilePaneWidth] = useState(initialFilePaneWidth);
  const [filePaneHasPreview, setFilePaneHasPreview] = useState(false);
  const [filePaneResizing, setFilePaneResizing] = useState(false);
  const [activeFilePath, setActiveFilePath] = useState<string | null>(null);
  const [activeFileRequestKey, setActiveFileRequestKey] = useState(0);
  const mainRef = useRef<HTMLElement | null>(null);
  const renameInputRef = useRef<HTMLInputElement | null>(null);
  const fileReferenceRequestId = useRef(0);
  const filteredSessions = useMemo(() => {
    const query = sessionQuery.trim().toLowerCase();
    if (!query) return sessions;
    return sessions.filter((session) => (
      [
        session.title || "New Session",
        session.status ?? "",
        session.id,
      ].some((value) => value.toLowerCase().includes(query))
    ));
  }, [sessionQuery, sessions]);
  const visibleSessions = filteredSessions.slice(0, 30);
  const hiddenSessionCount = Math.max(0, filteredSessions.length - visibleSessions.length);
  const fileReferenceOptions = useMemo(() => {
    const seen = new Set<string>();
    return [
      ...(worktreeStatus?.files ?? []).map((path) => ({ path })),
      ...searchedFileReferences,
    ].filter((option) => {
      const key = option.path.trim();
      if (!key || seen.has(key)) return false;
      seen.add(key);
      return true;
    });
  }, [searchedFileReferences, worktreeStatus?.files]);
  const normalizedFileChanges = useMemo(() => (
    (fileWorkspaceChangedFiles ?? []).map((file) => ({
      ...file,
      path: normalizePath(file.path),
    })).filter((file) => file.path)
  ), [fileWorkspaceChangedFiles]);
  const relatedFiles = useMemo(() => unique([
    activeFilePath ?? "",
    ...(worktreeStatus?.files ?? []),
    ...normalizedFileChanges.map((file) => file.path),
  ]), [activeFilePath, normalizedFileChanges, worktreeStatus?.files]);
  const recentWorkspaceOptions = useMemo(() => {
    const byPath = new Map<string, {
      path: string;
      name: string;
      branch?: string | null;
      isGit?: boolean;
      updatedAt?: number;
      sessionCount: number;
    }>();
    sessions.forEach((session) => {
      const path = (session.workspaceRoot ?? "").trim();
      if (!path) return;
      const key = path.replace(/\\/g, "/").toLowerCase();
      const existing = byPath.get(key);
      const updatedAt = session.updatedAt ?? 0;
      if (!existing) {
        byPath.set(key, {
          path,
          name: session.workspaceName ?? basename(path),
          branch: typeof session.repository?.branch === "string" ? session.repository.branch : null,
          isGit: Boolean(session.repository),
          updatedAt: session.updatedAt,
          sessionCount: 1,
        });
        return;
      }
      existing.sessionCount += 1;
      if (updatedAt > (existing.updatedAt ?? 0)) {
        existing.updatedAt = session.updatedAt;
        existing.name = session.workspaceName ?? basename(path);
        existing.branch = typeof session.repository?.branch === "string" ? session.repository.branch : existing.branch;
        existing.isGit = existing.isGit || Boolean(session.repository);
      }
    });
    return Array.from(byPath.values())
      .sort((left, right) => (right.updatedAt ?? 0) - (left.updatedAt ?? 0));
  }, [sessions]);
  const effectiveFilePaneWidth = filePaneHasPreview ? filePaneWidth : Math.min(filePaneWidth, 520);

  const handleFileReferenceQueryChange = useCallback((query: string | null) => {
    setFileReferenceQuery(query);
  }, []);

  const openFileWorkspace = useCallback((path?: string | null) => {
    const normalized = normalizePath(path ?? "");
    if (normalized) {
      setActiveFilePath(normalized);
      setActiveFileRequestKey((value) => value + 1);
    }
    setFilePaneOpen(true);
  }, []);

  const closeFileWorkspace = useCallback(() => {
    setFilePaneOpen(false);
    setFilePaneHasPreview(false);
  }, []);

  const openExternalFile = useCallback((absolutePath: string) => {
    void runtimeClient.openPath({ path: absolutePath });
  }, []);

  const addFileToChat = useCallback((path: string) => {
    window.dispatchEvent(new CustomEvent(FILE_REFERENCE_EVENT, { detail: { path } }));
  }, []);

  const addSelectionToChat = useCallback((path: string, selection: FileWorkspaceTextSelection) => {
    window.dispatchEvent(new CustomEvent(FILE_REFERENCE_EVENT, { detail: { path, selection } }));
  }, []);

  const startFilePaneResize = useCallback((event: ReactPointerEvent<HTMLDivElement>) => {
    const shellRect = mainRef.current?.getBoundingClientRect();
    const shellWidth = shellRect?.width ?? window.innerWidth;
    event.preventDefault();
    event.currentTarget.setPointerCapture?.(event.pointerId);
    setFilePaneResizing(true);

    const onMove = (moveEvent: PointerEvent) => {
      const containerRight = shellRect?.right ?? window.innerWidth;
      const nextWidth = containerRight - moveEvent.clientX;
      setFilePaneWidth(clampFilePaneWidth(nextWidth, shellWidth));
    };
    const onUp = () => {
      setFilePaneResizing(false);
      window.removeEventListener("pointermove", onMove);
    };

    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp, { once: true });
  }, []);

  const resizeFilePaneWithKeyboard = useCallback((event: ReactKeyboardEvent<HTMLDivElement>) => {
    const shellWidth = mainRef.current?.getBoundingClientRect().width ?? window.innerWidth;
    if (event.key === "ArrowLeft") {
      event.preventDefault();
      setFilePaneWidth((current) => clampFilePaneWidth(current + FILE_PANE_RESIZE_STEP, shellWidth));
    } else if (event.key === "ArrowRight") {
      event.preventDefault();
      setFilePaneWidth((current) => clampFilePaneWidth(current - FILE_PANE_RESIZE_STEP, shellWidth));
    } else if (event.key === "Home") {
      event.preventDefault();
      setFilePaneWidth(clampFilePaneWidth(FILE_PANE_MIN_WIDTH, shellWidth));
    } else if (event.key === "End") {
      event.preventDefault();
      setFilePaneWidth(clampFilePaneWidth(FILE_PANE_MAX_WIDTH, shellWidth));
    }
  }, []);

  const startRenameSession = useCallback((session: AppShellV2Props["sessions"][number]) => {
    setRenamingSessionId(session.id);
    setRenameValue(session.title || "New Session");
  }, []);

  const cancelRenameSession = useCallback(() => {
    setRenamingSessionId(null);
    setRenameValue("");
  }, []);

  const commitRenameSession = useCallback((event?: FormEvent) => {
    event?.preventDefault();
    if (!renamingSessionId) return;
    const nextTitle = renameValue.trim();
    const currentTitle = sessions.find((session) => session.id === renamingSessionId)?.title ?? "";
    if (nextTitle && nextTitle !== currentTitle) {
      onRenameSession(renamingSessionId, nextTitle);
    }
    setRenamingSessionId(null);
    setRenameValue("");
  }, [onRenameSession, renameValue, renamingSessionId, sessions]);

  const requestDeleteSession = useCallback((session: AppShellV2Props["sessions"][number]) => {
    const title = session.title || "New Session";
    if (window.confirm(`删除会话“${title}”？`)) {
      onDeleteSession(session.id);
    }
  }, [onDeleteSession]);

  useEffect(() => {
    if (fileReferenceQuery === null || !shellComposerVisible || !workspaceRoot) {
      setSearchedFileReferences([]);
      return undefined;
    }
    const query = fileReferenceQuery.trim();
    const requestId = fileReferenceRequestId.current + 1;
    fileReferenceRequestId.current = requestId;
    const timer = window.setTimeout(() => {
      runtimeClient
        .workspaceFileSearch({
          workspaceRoot,
          query,
          maxEntries: 24,
        })
        .then((result) => {
          if (fileReferenceRequestId.current !== requestId) return;
          setSearchedFileReferences(result.entries.map((entry) => ({ path: entry.path })));
        })
        .catch(() => {
          if (fileReferenceRequestId.current !== requestId) return;
          setSearchedFileReferences([]);
        });
    }, query ? 120 : 0);
    return () => {
      window.clearTimeout(timer);
    };
  }, [fileReferenceQuery, shellComposerVisible, workspaceRoot]);

  useEffect(() => {
    const handler = (event: Event) => {
      const detail = (event as CustomEvent<{ path?: string | null }>).detail;
      openFileWorkspace(detail?.path ?? null);
    };
    window.addEventListener(FILE_WORKSPACE_OPEN_EVENT, handler);
    return () => {
      window.removeEventListener(FILE_WORKSPACE_OPEN_EVENT, handler);
    };
  }, [openFileWorkspace]);

  useEffect(() => {
    if (!filePaneOpen) {
      return undefined;
    }
    const clampPaneWidth = () => {
      const shellWidth = mainRef.current?.getBoundingClientRect().width ?? window.innerWidth;
      setFilePaneWidth((current) => clampFilePaneWidth(current, shellWidth));
    };
    clampPaneWidth();
    window.addEventListener("resize", clampPaneWidth);
    return () => {
      window.removeEventListener("resize", clampPaneWidth);
    };
  }, [filePaneOpen, filePaneHasPreview]);

  useEffect(() => {
    const root = document.documentElement;
    root.style.setProperty("--hc-file-pane-reserve", filePaneOpen ? `${effectiveFilePaneWidth}px` : "0px");
    return () => {
      root.style.removeProperty("--hc-file-pane-reserve");
    };
  }, [effectiveFilePaneWidth, filePaneOpen]);

  useEffect(() => {
    if (!shellComposerVisible) {
      return undefined;
    }
    const handler = (event: Event) => {
      const detail = (event as CustomEvent<{
        path?: string;
        selection?: {
          text?: string;
          note?: string;
          startLine?: number;
          endLine?: number;
        };
      }>).detail;
      const path = detail?.path?.trim();
      if (!path) {
        return;
      }
      const base = promptValue.replace(/\s+$/, "");
      const prefix = base ? `${base} ` : "";
      const selection = detail?.selection;
      const quote = typeof selection?.text === "string"
        ? selection.text.replace(/^(?:\s*\r?\n)+|(?:\r?\n\s*)+$/g, "")
        : "";
      if (!quote) {
        onPromptChange(`${prefix}@${path} `);
        return;
      }
      const activeSelection = selection as NonNullable<typeof selection>;
      const lineSuffix = activeSelection.startLine
        ? `:${activeSelection.endLine && activeSelection.endLine !== activeSelection.startLine ? `${activeSelection.startLine}-${activeSelection.endLine}` : activeSelection.startLine}`
        : "";
      const quoted = quote.split(/\r?\n/).map((line) => `> ${line}`).join("\n");
      const note = activeSelection.note?.trim();
      onPromptChange(`${prefix}@${path}${lineSuffix}\n${quoted}${note ? `\n${note}` : ""}\n`);
    };
    window.addEventListener("haha-clean:add-file-reference", handler);
    return () => {
      window.removeEventListener("haha-clean:add-file-reference", handler);
    };
  }, [onPromptChange, promptValue, shellComposerVisible]);

  useEffect(() => {
    if (!renamingSessionId) return;
    renameInputRef.current?.focus();
    renameInputRef.current?.select();
  }, [renamingSessionId]);

  return (
    <div className="hc-app">
      <DesktopTitlebar />
      <div className="hc-app-body">
        <aside className="hc-sidebar" aria-label="工作区导航">
          <header className="hc-brand">
            <div className="hc-brand-mark" aria-hidden="true">Y</div>
            <div>
              <strong>Yuanbao Agent</strong>
              <span>{basename(cwdLabel) || workspaceName}</span>
            </div>
          </header>

          <nav className="hc-primary-nav" aria-label="主要操作">
            <button type="button" className={activeKind === "new-session" ? "is-active" : undefined} onClick={() => onOpenSystemTab("new-session")}>
              <MessageSquarePlus size={18} />
              <span>新建会话</span>
            </button>
            <button type="button" className={activeKind === "settings" ? "is-active" : undefined} onClick={() => onOpenSystemTab("settings")}>
              <Settings size={18} />
              <span>设置</span>
            </button>
            <button type="button" className={activeKind === "mcp" ? "is-active" : undefined} onClick={() => onOpenSystemTab("mcp")}>
              <Wrench size={18} />
              <span>MCP</span>
            </button>
            <button type="button" className={activeKind === "skills" ? "is-active" : undefined} onClick={() => onOpenSystemTab("skills")}>
              <Sparkles size={18} />
              <span>Skills</span>
            </button>
          </nav>

          <section className="hc-session-list" aria-label="会话">
            <div className="hc-section-title">
              <span>会话</span>
              <small>{sessionQuery.trim() ? `${filteredSessions.length}/${sessions.length}` : sessions.length}</small>
            </div>
            <label className="hc-session-search">
              <Search size={14} />
              <input
                aria-label="搜索会话"
                value={sessionQuery}
                placeholder="搜索会话"
                onChange={(event) => setSessionQuery(event.currentTarget.value)}
              />
              {sessionQuery ? (
                <button type="button" aria-label="清除搜索" onClick={() => setSessionQuery("")}>
                  <X size={13} />
                </button>
              ) : null}
            </label>
            <div className="hc-session-items">
              {visibleSessions.length ? (
                visibleSessions.map((session) => {
                  const title = session.title || "New Session";
                  const isRenaming = renamingSessionId === session.id;
                  return (
                    <div
                      key={session.id}
                      className="hc-session-row"
                      data-active={session.id === activeSessionId ? "true" : "false"}
                    >
                      {isRenaming ? (
                        <form className="hc-session-rename" onSubmit={commitRenameSession}>
                          <input
                            ref={renameInputRef}
                            aria-label="编辑会话名称"
                            value={renameValue}
                            onChange={(event) => setRenameValue(event.currentTarget.value)}
                            onKeyDown={(event) => {
                              if (event.key === "Escape") {
                                event.preventDefault();
                                cancelRenameSession();
                              }
                            }}
                          />
                          <button type="submit" aria-label="保存会话名称">
                            <Check size={14} />
                          </button>
                          <button type="button" aria-label="取消重命名" onClick={cancelRenameSession}>
                            <X size={14} />
                          </button>
                        </form>
                      ) : (
                        <>
                          <button
                            type="button"
                            className="hc-session-open"
                            aria-label={`打开会话 ${title}`}
                            onClick={() => onOpenSessionTab(session)}
                          >
                            <span className="hc-session-dot" data-tone={statusTone(session.status)} />
                            <span className="hc-session-copy">
                              <strong>{title}</strong>
                              <small>{sessionTime(session.updatedAt) || "刚刚"}</small>
                            </span>
                          </button>
                          <span className="hc-session-actions" aria-label={`${title} 操作`}>
                            <button
                              type="button"
                              className="hc-session-action"
                              aria-label={`重命名 ${title}`}
                              title="重命名"
                              onClick={() => startRenameSession(session)}
                            >
                              <Pencil size={13} />
                            </button>
                            <button
                              type="button"
                              className="hc-session-action"
                              aria-label={`删除 ${title}`}
                              title="删除"
                              onClick={() => requestDeleteSession(session)}
                            >
                              <Trash2 size={13} />
                            </button>
                          </span>
                        </>
                      )}
                    </div>
                  );
                })
              ) : (
                <p className="hc-empty-side">{sessionQuery.trim() ? "没有匹配的会话" : "暂无会话"}</p>
              )}
            </div>
            {hiddenSessionCount ? (
              <p className="hc-session-list-note">还有 {hiddenSessionCount} 个会话，可搜索定位</p>
            ) : (
              null
            )}
          </section>

          <footer className="hc-side-status">
            <div>
              <span className="hc-status-dot" data-tone={disabled ? "danger" : "success"} />
              <strong>{disabled ? "未连接" : "已连接"}</strong>
            </div>
            <small>{providerLabel}</small>
            {runtimeLabel ? <small>{runtimeLabel}</small> : null}
            {dirtyCount > 0 ? <small>{dirtyCount} 个文件改动</small> : null}
          </footer>
        </aside>

        <main
          ref={mainRef}
          className="hc-main"
          aria-label="工作区"
          data-file-pane={filePaneOpen && activeKind === "session" ? "open" : "closed"}
          data-file-preview={filePaneHasPreview ? "true" : "false"}
          data-file-resizing={filePaneResizing ? "true" : "false"}
          style={{ "--hc-file-pane-reserve": filePaneOpen && activeKind === "session" ? `${effectiveFilePaneWidth}px` : "0px" } as React.CSSProperties}
        >
          <header className="hc-tabs" aria-label="标签页">
            <div className="hc-tab-strip">
              {tabs.map((tab) => (
                <button
                  type="button"
                  key={tab.id}
                  className={tab.id === activeTabId ? "is-active" : undefined}
                  onClick={() => onActivateTab(tab.id)}
                >
                  {tab.kind === "session" ? <Bot size={15} /> : <Code2 size={15} />}
                  <span>{tab.kind === "session" ? tab.title || "New Session" : systemLabel(tab.kind)}</span>
                  {tab.closable ? (
                    <span
                      role="button"
                      tabIndex={0}
                      aria-label={`关闭 ${tab.title}`}
                      className="hc-tab-close"
                      onClick={(event) => {
                        event.stopPropagation();
                        onCloseTab(tab.id);
                      }}
                      onKeyDown={(event) => {
                        if (event.key === "Enter" || event.key === " ") {
                          event.preventDefault();
                          event.stopPropagation();
                          onCloseTab(tab.id);
                        }
                      }}
                    >
                      <X size={13} />
                    </span>
                  ) : null}
                </button>
              ))}
            </div>
            {activeKind === "session" ? (
              <button
                type="button"
                className="hc-tabs-file-toggle"
                aria-pressed={filePaneOpen}
                onClick={() => {
                  if (filePaneOpen) {
                    closeFileWorkspace();
                    return;
                  }
                  openFileWorkspace();
                }}
              >
                <FolderOpen size={16} />
                <span>文件</span>
              </button>
            ) : null}
          </header>

          <section className="hc-workspace" data-kind={activeKind} data-composer={shellComposerVisible ? "true" : "false"}>
            {loading ? <LoadingSkeleton /> : children}
          </section>

          {filePaneOpen && activeKind === "session" ? (
            <>
              <div
                aria-label="调整聊天和工作区宽度"
                aria-orientation="vertical"
                aria-valuemax={FILE_PANE_MAX_WIDTH}
                aria-valuemin={FILE_PANE_MIN_WIDTH}
                aria-valuenow={filePaneWidth}
                className="hc-shell-file-resizer"
                onDoubleClick={() => setFilePaneWidth(FILE_PANE_DEFAULT_WIDTH)}
                onKeyDown={resizeFilePaneWithKeyboard}
                onPointerDown={startFilePaneResize}
                role="separator"
                tabIndex={0}
                title="拖动调整工作区宽度"
              >
                <GripVertical size={14} strokeWidth={1.8} aria-hidden="true" />
              </div>
              <aside className="hc-file-pane" aria-label="文件工作区">
                <FileWorkspacePanel
                  workspaceRoot={workspaceRoot}
                  workspaceLabel={workspaceRoot || "项目"}
                  relatedFiles={relatedFiles}
                  changedFiles={normalizedFileChanges}
                  activeFilePath={activeFilePath}
                  activeFileRequestKey={activeFileRequestKey}
                  onOpenExternalFile={openExternalFile}
                  onAddFileToChat={addFileToChat}
                  onAddSelectionToChat={addSelectionToChat}
                  onPreviewStateChange={setFilePaneHasPreview}
                  onClose={closeFileWorkspace}
                />
              </aside>
            </>
          ) : null}

          {shellComposerVisible ? (
            <CleanComposer
              promptValue={promptValue}
              onPromptChange={onPromptChange}
              onSubmitPrompt={onSubmitPrompt}
              onQueuePrompt={onQueuePrompt}
              onStopPrompt={onStopPrompt}
              queuedPrompts={queuedPrompts}
              onGuideQueuedPrompt={onGuideQueuedPrompt}
              onQueuedPromptRemove={onQueuedPromptRemove}
              onQueuedPromptMove={onQueuedPromptMove}
              disabled={disabled}
              sending={sending}
              submitting={submitting}
              stopPending={stopPending}
              queuedPromptCount={queuedPromptCount}
              attachments={attachments}
              onAttachmentsChange={onAttachmentsChange}
              onAttachmentError={onAttachmentError}
              fileReferenceOptions={fileReferenceOptions}
              onFileReferenceQueryChange={handleFileReferenceQueryChange}
              modelOptions={modelOptions}
              selectedModelId={selectedModelId}
              onSelectModel={onSelectModel}
              runtimeChildTasks={runtimeChildTasks}
              providerLabel={providerLabel}
              cwdLabel={cwdLabel}
              permissionLabel={permissionLabel}
              permissionMode={permissionMode}
              onPermissionModeChange={onPermissionModeChange}
              onWorkspacePathChange={activeKind === "new-session" ? onWorkspacePathChange : undefined}
              recentWorkspaceOptions={recentWorkspaceOptions}
              useWorktree={useWorktree}
              onUseWorktreeChange={onUseWorktreeChange}
              worktreeModeBusy={worktreeModeBusy}
              contextLabel={contextLabel}
              contextPreview={contextPreview}
              worktreeStatus={worktreeStatus}
              variant={activeKind === "new-session" ? "new" : "session"}
            />
          ) : null}

          {floatingStatusText ? (
            <div className="hc-floating-status" data-tone={statusTone(activeTaskStatus)}>
              <Folder size={14} />
              <span>{floatingStatusText}</span>
            </div>
          ) : null}
        </main>
      </div>
    </div>
  );
}
