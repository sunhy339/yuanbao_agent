import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ArrowRight, ChevronDown, Folder, Gauge, Paperclip, Plus, Shield, Slash } from "lucide-react";
import type { QueuedPromptSubmission } from "../../state/eventRecordViews";
import { matchCommands } from "../../state/slashCommands";
import type { ComposerRuntimeChildTask } from "../workbench/ComposerDock";
import type { SessionWorkspaceContextPreview } from "../workbench/workspaces/session/types";

export interface HahaComposerProps {
  promptValue: string;
  onPromptChange(value: string): void;
  onSubmitPrompt(): void;
  onQueuePrompt?: (mode?: "queued" | "supplement") => void;
  onStopPrompt?: () => void;
  queuedPrompts?: QueuedPromptSubmission[];
  onGuideQueuedPrompt?: (id: string) => void;
  onQueuedPromptRemove?: (id: string) => void;
  onQueuedPromptMove?: (id: string, direction: "up" | "down") => void;
  disabled: boolean;
  sending?: boolean;
  submitting?: boolean;
  stopPending?: boolean;
  queuedPromptCount?: number;
  attachments?: string[];
  onAttachmentsChange?: (attachments: string[]) => void;
  onAttachmentError?: (message: string) => void;
  modelOptions?: Array<{ id: string; label: string; subtitle?: string }>;
  selectedModelId?: string;
  onSelectModel?: (modelId: string) => void;
  runtimeChildTasks?: ComposerRuntimeChildTask[];
  providerLabel: string;
  cwdLabel: string;
  permissionLabel?: string;
  permissionMode?: string;
  onPermissionModeChange?: (mode: string) => void;
  contextLabel?: string;
  contextPreview?: SessionWorkspaceContextPreview | null;
  hidden?: boolean;
  layout?: "default" | "session";
}

const permissionOptions = [
  { id: "ask", title: "询问权限" },
  { id: "edits", title: "允许编辑" },
  { id: "plan", title: "计划模式" },
  { id: "skip", title: "完全访问" },
];

function compactPath(value?: string) {
  if (!value?.trim()) return "选择项目";
  const normalized = value.replace(/\\/g, "/").replace(/\/+$/, "");
  return normalized.split("/").filter(Boolean).pop() || normalized;
}

function contextPercent(contextPreview?: SessionWorkspaceContextPreview | null) {
  const used = contextPreview?.budgetStats?.estimatedInputTokens ?? contextPreview?.budgetStats?.estimatedTokens;
  const max = contextPreview?.budgetStats?.maxContextTokens;
  if (typeof used === "number" && typeof max === "number" && max > 0) {
    return `${Math.max(0, Math.min(99, Math.round((used / max) * 100)))}%`;
  }
  return "--";
}

export function HahaComposer({
  promptValue,
  onPromptChange,
  onSubmitPrompt,
  onQueuePrompt,
  onStopPrompt,
  queuedPrompts = [],
  onGuideQueuedPrompt,
  onQueuedPromptRemove,
  onQueuedPromptMove,
  disabled,
  sending,
  submitting,
  stopPending = false,
  queuedPromptCount = 0,
  attachments = [],
  onAttachmentsChange,
  onAttachmentError,
  modelOptions = [],
  selectedModelId,
  onSelectModel,
  providerLabel,
  cwdLabel,
  permissionLabel,
  permissionMode,
  onPermissionModeChange,
  contextPreview,
  hidden,
  layout = "default",
}: HahaComposerProps) {
  const formRef = useRef<HTMLFormElement | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const [plusOpen, setPlusOpen] = useState(false);
  const [permissionOpen, setPermissionOpen] = useState(false);
  const [modelOpen, setModelOpen] = useState(false);
  const selectedModel = modelOptions.find((option) => option.id === selectedModelId) ?? modelOptions[0];
  const canSubmit = !disabled && !submitting && Boolean(promptValue.trim() || attachments.length);
  const canQueue = Boolean(sending && onQueuePrompt && canSubmit);
  const commandMatches = useMemo(() => {
    if (!promptValue.startsWith("/") || promptValue.includes(" ")) return [];
    return matchCommands(promptValue.trim());
  }, [promptValue]);
  const context = contextPercent(contextPreview);

  useEffect(() => {
    const node = textareaRef.current;
    if (!node) return;
    node.style.height = "auto";
    node.style.height = `${Math.min(220, Math.max(88, node.scrollHeight))}px`;
  }, [promptValue]);

  useEffect(() => {
    const node = formRef.current;
    if (!node || hidden) return undefined;
    const updateHeight = () => {
      document.documentElement.style.setProperty("--wb-composer-live-height", `${Math.ceil(node.getBoundingClientRect().height) + 24}px`);
    };
    updateHeight();
    const observer = typeof ResizeObserver !== "undefined" ? new ResizeObserver(updateHeight) : null;
    observer?.observe(node);
    window.addEventListener("resize", updateHeight);
    return () => {
      observer?.disconnect();
      window.removeEventListener("resize", updateHeight);
    };
  }, [attachments.length, hidden, plusOpen, permissionOpen, promptValue, queuedPrompts.length]);

  const addFiles = useCallback(async () => {
    try {
      const { open } = await import("@tauri-apps/plugin-dialog");
      const selected = await open({ multiple: true, directory: false });
      const paths = Array.isArray(selected) ? selected : selected ? [selected] : [];
      if (paths.length) onAttachmentsChange?.(Array.from(new Set([...attachments, ...paths])));
    } catch (reason) {
      onAttachmentError?.(reason instanceof Error ? reason.message : String(reason));
    }
  }, [attachments, onAttachmentError, onAttachmentsChange]);

  const insertSlash = useCallback(() => {
    const node = textareaRef.current;
    const start = node?.selectionStart ?? promptValue.length;
    const end = node?.selectionEnd ?? start;
    const before = promptValue.slice(0, start);
    const after = promptValue.slice(end);
    const space = before && !/\s$/.test(before) ? " " : "";
    const next = `${before}${space}/${after}`;
    onPromptChange(next);
    setPlusOpen(false);
    window.requestAnimationFrame(() => {
      const cursor = before.length + space.length + 1;
      textareaRef.current?.focus();
      textareaRef.current?.setSelectionRange(cursor, cursor);
    });
  }, [onPromptChange, promptValue]);

  if (hidden) return null;

  return (
    <form
      ref={formRef}
      className="haha-composer"
      data-layout={layout}
      onSubmit={(event) => {
        event.preventDefault();
        if (canSubmit) onSubmitPrompt();
      }}
    >
      {queuedPrompts.length ? (
        <div className="haha-queue">
          {queuedPrompts.map((item, index) => (
            <div className="haha-queue-item" key={item.id}>
              <span>{item.content}</span>
              <button type="button" onClick={() => onGuideQueuedPrompt?.(item.id)}>引导</button>
              <button type="button" onClick={() => onQueuedPromptMove?.(item.id, "up")} disabled={index === 0}>↑</button>
              <button type="button" onClick={() => onQueuedPromptMove?.(item.id, "down")} disabled={index === queuedPrompts.length - 1}>↓</button>
              <button type="button" onClick={() => onQueuedPromptRemove?.(item.id)}>删除</button>
            </div>
          ))}
        </div>
      ) : null}
      <div className="haha-composer-card">
        <label className="haha-composer-input">
          <span>输入</span>
          <textarea
            ref={textareaRef}
            value={promptValue}
            placeholder="随便问点什么..."
            disabled={disabled}
            onChange={(event) => onPromptChange(event.currentTarget.value)}
            onKeyDown={(event) => {
              if ((event.metaKey || event.ctrlKey) && event.key === "Enter" && canSubmit) {
                event.preventDefault();
                onSubmitPrompt();
              }
            }}
          />
        </label>
        {commandMatches.length ? (
          <div className="haha-slash-menu">
            {commandMatches.map((command) => (
              <button type="button" key={command.name} onClick={() => onPromptChange(`${command.name} `)}>
                <strong>{command.name}</strong>
                <span>{command.description}</span>
              </button>
            ))}
          </div>
        ) : null}
        <div className="haha-composer-toolbar">
          <div className="haha-composer-left">
            <div className="haha-menu-root">
              <button type="button" className="haha-icon-button" onClick={() => setPlusOpen((value) => !value)} aria-label="添加">
                <Plus size={20} />
              </button>
              {plusOpen ? (
                <div className="haha-plus-menu">
                  <button type="button" onClick={() => { setPlusOpen(false); void addFiles(); }}>
                    <Paperclip size={17} />
                    <span>添加文件或图片</span>
                  </button>
                  <button type="button" onClick={insertSlash}>
                    <Slash size={17} />
                    <span>斜杠命令</span>
                  </button>
                </div>
              ) : null}
            </div>
            <div className="haha-menu-root">
              <button type="button" className="haha-pill-button" onClick={() => setPermissionOpen((value) => !value)}>
                <Shield size={15} />
                <span>{permissionLabel || "询问权限"}</span>
                <ChevronDown size={14} />
              </button>
              {permissionOpen ? (
                <div className="haha-popover-menu">
                  {permissionOptions.map((option) => (
                    <button
                      type="button"
                      key={option.id}
                      data-selected={option.id === permissionMode}
                      onClick={() => {
                        onPermissionModeChange?.(option.id);
                        setPermissionOpen(false);
                      }}
                    >
                      {option.title}
                    </button>
                  ))}
                </div>
              ) : null}
            </div>
          </div>
          <div className="haha-composer-right">
            <button type="button" className="haha-context-button" title="上下文">
              <Gauge size={15} />
              <span>{context}</span>
            </button>
            <div className="haha-menu-root">
              <button type="button" className="haha-model-button" onClick={() => setModelOpen((value) => !value)}>
                <strong>{selectedModel?.label ?? providerLabel}</strong>
                <ChevronDown size={14} />
              </button>
              {modelOpen ? (
                <div className="haha-popover-menu haha-model-menu">
                  {modelOptions.map((option) => (
                    <button
                      type="button"
                      key={option.id}
                      data-selected={option.id === selectedModelId}
                      onClick={() => {
                        onSelectModel?.(option.id);
                        setModelOpen(false);
                      }}
                    >
                      <strong>{option.label}</strong>
                      {option.subtitle ? <small>{option.subtitle}</small> : null}
                    </button>
                  ))}
                </div>
              ) : null}
            </div>
            {sending && onStopPrompt ? (
              <button type="button" className="haha-stop-button" disabled={stopPending} onClick={onStopPrompt}>
                {stopPending ? "停止中" : "停止"}
              </button>
            ) : null}
            {sending && onQueuePrompt ? (
              <button type="button" className="haha-queue-button" disabled={!canQueue} onClick={() => onQueuePrompt?.("queued")}>
                暂存{queuedPromptCount ? ` ${queuedPromptCount}` : ""}
              </button>
            ) : null}
            <button type="submit" className="haha-run-button" disabled={!canSubmit} aria-label="发送">
              <ArrowRight size={20} />
            </button>
          </div>
        </div>
      </div>
      <div className="haha-project-row">
        <button type="button" className="haha-project-chip" title={cwdLabel}>
          <Folder size={18} />
          <span>{compactPath(cwdLabel)}</span>
        </button>
      </div>
    </form>
  );
}
