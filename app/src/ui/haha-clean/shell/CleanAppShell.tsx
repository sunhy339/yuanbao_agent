import type { ReactNode } from "react";
import {
  Bot,
  Code2,
  Folder,
  MessageSquarePlus,
  Settings,
  Sparkles,
  Wrench,
  X,
} from "lucide-react";
import { DesktopTitlebar } from "../../workbench/DesktopTitlebar";
import type { AppShellV2Props } from "../../v2/layout/AppShellV2";
import { CleanComposer } from "../composer/CleanComposer";
import { basename, statusTone } from "../shared/text";
import "../clean.css";

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
  runtimeLabel,
  contextLabel,
  contextPreview,
  worktreeStatus,
  activeTaskStatus,
  activeTaskCurrentStep,
  loading,
  children,
}: AppShellV2Props) {
  const activeKind = resolveTabKind(activeTabId, tabs);
  const shellComposerVisible = composerVisible && (activeKind === "session" || activeKind === "new-session");
  const sessionTabs = sessions.slice(0, 10);
  const dirtyCount = worktreeStatus?.dirtyFiles ?? 0;
  const fileReferenceOptions = (worktreeStatus?.files ?? []).map((path) => ({ path }));

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
              <small>{sessions.length}</small>
            </div>
            {sessionTabs.length ? (
              sessionTabs.map((session) => (
                <button
                  type="button"
                  key={session.id}
                  className={session.id === activeSessionId ? "is-active" : undefined}
                  onClick={() => onOpenSessionTab(session)}
                >
                  <span className="hc-session-dot" data-tone={statusTone(session.status)} />
                  <span>
                    <strong>{session.title || "New Session"}</strong>
                    <small>{sessionTime(session.updatedAt)}</small>
                  </span>
                </button>
              ))
            ) : (
              <p className="hc-empty-side">暂无会话</p>
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

        <main className="hc-main" aria-label="工作区">
          <header className="hc-tabs" aria-label="标签页">
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
          </header>

          <section className="hc-workspace" data-kind={activeKind} data-composer={shellComposerVisible ? "true" : "false"}>
            {loading ? <LoadingSkeleton /> : children}
          </section>

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
              queuedPromptCount={queuedPromptCount}
              attachments={attachments}
              onAttachmentsChange={onAttachmentsChange}
              onAttachmentError={onAttachmentError}
              fileReferenceOptions={fileReferenceOptions}
              modelOptions={modelOptions}
              selectedModelId={selectedModelId}
              onSelectModel={onSelectModel}
              runtimeChildTasks={runtimeChildTasks}
              providerLabel={providerLabel}
              cwdLabel={cwdLabel}
              permissionLabel={permissionLabel}
              permissionMode={permissionMode}
              onPermissionModeChange={onPermissionModeChange}
              contextLabel={contextLabel}
              contextPreview={contextPreview}
              variant={activeKind === "new-session" ? "new" : "session"}
            />
          ) : null}

          {activeTaskStatus || activeTaskCurrentStep ? (
            <div className="hc-floating-status" data-tone={statusTone(activeTaskStatus)}>
              <Folder size={14} />
              <span>{activeTaskCurrentStep || activeTaskStatus}</span>
            </div>
          ) : null}
        </main>
      </div>
    </div>
  );
}
