import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowUp,
  ChevronDown,
  CircleHelp,
  Folder,
  Gauge,
  ImagePlus,
  Paperclip,
  Plus,
  Shield,
  Slash,
  Square,
  X,
} from "lucide-react";
import type { QueuedPromptSubmission } from "../../../state/eventRecordViews";
import { matchCommands } from "../../../state/slashCommands";
import type { ComposerRuntimeChildTask } from "../../workbench/ComposerDock";
import type { SessionWorkspaceContextPreview } from "../../workbench/workspaces/session/types";
import { basename } from "../shared/text";

export interface CleanComposerProps {
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
  variant?: "session" | "new";
}

const permissionOptions = [
  { id: "ask", label: "询问权限", description: "写入、命令和高风险操作前先确认。" },
  { id: "edits", label: "允许工作区编辑", description: "允许修改工作区文件，危险命令仍会走确认。" },
  { id: "plan", label: "计划模式", description: "只做分析和计划，不主动改动文件。" },
  { id: "skip", label: "完全访问权限", description: "尽量自动执行，适合你已确认目标时使用。" },
];

function contextUsage(context?: SessionWorkspaceContextPreview | null) {
  const used = context?.budgetStats?.estimatedInputTokens ?? context?.budgetStats?.estimatedTokens;
  const max = context?.budgetStats?.maxContextTokens;
  if (typeof used === "number" && typeof max === "number" && max > 0) {
    return `${Math.max(0, Math.min(99, Math.round((used / max) * 100)))}%`;
  }
  return "--";
}

function attachmentName(path: string) {
  return basename(path);
}

function formatTokenCount(value?: number | null) {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return "未知";
  }
  return value.toLocaleString("zh-CN");
}

function contextBudgetRows(contextPreview?: SessionWorkspaceContextPreview | null) {
  const stats = contextPreview?.budgetStats;
  const rows: Array<{ label: string; value: string }> = [];
  const used = stats?.estimatedInputTokens ?? stats?.estimatedTokens;
  const max = stats?.maxContextTokens;
  if (typeof used === "number" || typeof max === "number") {
    rows.push({ label: "预算", value: `${formatTokenCount(used)} / ${formatTokenCount(max)}` });
  }
  if (typeof stats?.messageTokens === "number") {
    rows.push({ label: "消息", value: formatTokenCount(stats.messageTokens) });
  }
  if (typeof stats?.toolSchemaTokens === "number") {
    rows.push({ label: "工具", value: formatTokenCount(stats.toolSchemaTokens) });
  }
  if (typeof stats?.stablePrefixTokens === "number") {
    rows.push({ label: "稳定前缀", value: formatTokenCount(stats.stablePrefixTokens) });
  }
  if (contextPreview?.toolCount) {
    rows.push({ label: "可用工具", value: `${contextPreview.toolCount} 个` });
  }
  return rows;
}

export function CleanComposer({
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
  queuedPromptCount = 0,
  attachments = [],
  onAttachmentsChange,
  onAttachmentError,
  modelOptions = [],
  selectedModelId,
  onSelectModel,
  runtimeChildTasks,
  providerLabel,
  cwdLabel,
  permissionLabel,
  permissionMode,
  onPermissionModeChange,
  contextLabel,
  contextPreview,
  hidden,
  variant = "session",
}: CleanComposerProps) {
  const formRef = useRef<HTMLFormElement | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const [plusOpen, setPlusOpen] = useState(false);
  const [permissionOpen, setPermissionOpen] = useState(false);
  const [modelOpen, setModelOpen] = useState(false);
  const [contextOpen, setContextOpen] = useState(false);
  const [projectOpen, setProjectOpen] = useState(false);
  const selectedModel = modelOptions.find((option) => option.id === selectedModelId) ?? modelOptions[0];
  const hasPayload = Boolean(promptValue.trim() || attachments.length);
  const canSubmit = !disabled && !submitting && hasPayload;
  const canQueue = Boolean(sending && canSubmit && onQueuePrompt);
  const context = contextUsage(contextPreview);
  const contextRows = contextBudgetRows(contextPreview);
  const trimmedSections = contextPreview?.budgetStats?.trimmedSections ?? [];
  const droppedSections = contextPreview?.budgetStats?.droppedSections ?? [];
  const cwdName = basename(cwdLabel);
  const slashMatches = useMemo(() => {
    if (!promptValue.startsWith("/") || promptValue.includes(" ")) return [];
    return matchCommands(promptValue.trim()).slice(0, 6);
  }, [promptValue]);

  useEffect(() => {
    const node = textareaRef.current;
    if (!node) return;
    node.style.height = "auto";
    node.style.height = `${Math.min(180, Math.max(96, node.scrollHeight))}px`;
  }, [promptValue]);

  useEffect(() => {
    const node = formRef.current;
    if (!node || hidden) return undefined;
    const update = () => {
      document.documentElement.style.setProperty("--hc-composer-height", `${Math.ceil(node.getBoundingClientRect().height) + 18}px`);
    };
    update();
    const observer = typeof ResizeObserver !== "undefined" ? new ResizeObserver(update) : null;
    observer?.observe(node);
    window.addEventListener("resize", update);
    return () => {
      observer?.disconnect();
      window.removeEventListener("resize", update);
    };
  }, [attachments.length, hidden, plusOpen, permissionOpen, promptValue, queuedPrompts.length]);

  const addFiles = useCallback(async () => {
    try {
      const { open } = await import("@tauri-apps/plugin-dialog");
      const selected = await open({ multiple: true, directory: false });
      const paths = Array.isArray(selected) ? selected : selected ? [selected] : [];
      if (paths.length) {
        onAttachmentsChange?.(Array.from(new Set([...attachments, ...paths])));
      }
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
    const prefix = before && !/\s$/.test(before) ? " " : "";
    onPromptChange(`${before}${prefix}/${after}`);
    setPlusOpen(false);
    window.requestAnimationFrame(() => textareaRef.current?.focus());
  }, [onPromptChange, promptValue]);

  if (hidden) return null;

  return (
    <form
      ref={formRef}
      className="hc-composer"
      data-variant={variant}
      onSubmit={(event) => {
        event.preventDefault();
        if (canSubmit) onSubmitPrompt();
      }}
    >
      {queuedPrompts.length ? (
        <div className="hc-queue" aria-label="待发送内容">
          {queuedPrompts.map((item, index) => (
            <div className="hc-queue-item" key={item.id}>
              <span>{item.content}</span>
              <button type="button" onClick={() => onGuideQueuedPrompt?.(item.id)}>引导</button>
              <button type="button" disabled={index === 0} onClick={() => onQueuedPromptMove?.(item.id, "up")}>上移</button>
              <button type="button" disabled={index === queuedPrompts.length - 1} onClick={() => onQueuedPromptMove?.(item.id, "down")}>下移</button>
              <button type="button" onClick={() => onQueuedPromptRemove?.(item.id)} aria-label="删除待发送内容"><X size={14} /></button>
            </div>
          ))}
        </div>
      ) : null}

      <div className="hc-composer-card">
        {attachments.length ? (
          <div className="hc-attachments">
            {attachments.map((path) => (
              <span key={path}>
                <Paperclip size={13} />
                {attachmentName(path)}
                <button
                  type="button"
                  aria-label={`移除 ${attachmentName(path)}`}
                  onClick={() => onAttachmentsChange?.(attachments.filter((item) => item !== path))}
                >
                  <X size={12} />
                </button>
              </span>
            ))}
          </div>
        ) : null}
        <textarea
          ref={textareaRef}
          value={promptValue}
          placeholder={variant === "new" ? "随便问点什么..." : "描述下一步要本地智能体完成的事情..."}
          disabled={disabled}
          onChange={(event) => onPromptChange(event.currentTarget.value)}
          onKeyDown={(event) => {
            if ((event.ctrlKey || event.metaKey) && event.key === "Enter" && canSubmit) {
              event.preventDefault();
              onSubmitPrompt();
            }
          }}
        />
        {slashMatches.length ? (
          <div className="hc-slash-panel">
            {slashMatches.map((command) => (
              <button type="button" key={command.name} onClick={() => onPromptChange(`${command.name} `)}>
                <strong>{command.name}{command.argsHint ? <small> {command.argsHint}</small> : null}</strong>
                <span>{command.description}</span>
              </button>
            ))}
          </div>
        ) : null}
        <div className="hc-composer-toolbar">
          <div className="hc-toolbar-left">
            <div className="hc-menu">
              <button type="button" className="hc-icon-button" onClick={() => setPlusOpen((open) => !open)} aria-label="添加">
                <Plus size={21} />
              </button>
              {plusOpen ? (
                <div className="hc-popover hc-plus-popover">
                  <button type="button" onClick={() => { setPlusOpen(false); void addFiles(); }}>
                    <ImagePlus size={17} />
                    <span>添加文件或图片</span>
                  </button>
                  <button type="button" onClick={insertSlash}>
                    <Slash size={17} />
                    <span>斜杠命令</span>
                  </button>
                </div>
              ) : null}
            </div>
            <div className="hc-menu">
              <button type="button" className="hc-pill hc-permission" onClick={() => setPermissionOpen((open) => !open)}>
                <Shield size={15} />
                <span>{permissionLabel || "询问权限"}</span>
                <ChevronDown size={14} />
              </button>
              {permissionOpen ? (
                <div className="hc-popover">
                  {permissionOptions.map((option) => (
                    <button
                      type="button"
                      key={option.id}
                      data-active={option.id === permissionMode}
                      onClick={() => {
                        onPermissionModeChange?.(option.id);
                        setPermissionOpen(false);
                      }}
                    >
                      <strong>{option.label}</strong>
                      <small>{option.description}</small>
                    </button>
                  ))}
                </div>
              ) : null}
            </div>
          </div>
          <div className="hc-toolbar-right">
            <div className="hc-menu">
              <button
                type="button"
                className="hc-pill hc-context"
                aria-expanded={contextOpen}
                aria-label={`上下文 ${context}`}
                onClick={() => {
                  setContextOpen((open) => !open);
                  setModelOpen(false);
                  setProjectOpen(false);
                }}
                title="上下文"
              >
                <Gauge size={14} />
                <span>{context}</span>
              </button>
              {contextOpen ? (
                <div className="hc-popover hc-context-popover" aria-label="上下文详情">
                  <header>
                    <strong>上下文</strong>
                    <small>{contextLabel || `当前占用 ${context}`}</small>
                  </header>
                  {contextRows.length ? (
                    <dl>
                      {contextRows.map((row) => (
                        <div key={row.label}>
                          <dt>{row.label}</dt>
                          <dd>{row.value}</dd>
                        </div>
                      ))}
                    </dl>
                  ) : (
                    <p>暂无上下文统计。</p>
                  )}
                  {contextPreview?.taskFocus?.currentStep ? <p>当前步骤：{contextPreview.taskFocus.currentStep}</p> : null}
                  {contextPreview?.projectFocus ? <p>项目焦点：{contextPreview.projectFocus}</p> : null}
                  {trimmedSections.length || droppedSections.length ? (
                    <p>已压缩：{[...trimmedSections, ...droppedSections].slice(0, 4).join("、")}</p>
                  ) : null}
                </div>
              ) : null}
            </div>
            <div className="hc-menu">
              <button type="button" className="hc-model" onClick={() => setModelOpen((open) => !open)}>
                <span>{selectedModel?.label ?? providerLabel}</span>
                <ChevronDown size={14} />
              </button>
              {modelOpen ? (
                <div className="hc-popover hc-model-popover">
                  {modelOptions.map((option) => (
                    <button
                      type="button"
                      key={option.id}
                      data-active={option.id === selectedModelId}
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
              <button type="button" className="hc-stop" onClick={onStopPrompt}>
                <Square size={12} fill="currentColor" />
                停止
              </button>
            ) : null}
            {canQueue ? (
              <button type="button" className="hc-queue-button" onClick={() => onQueuePrompt?.("queued")}>
                暂存{queuedPromptCount ? ` ${queuedPromptCount}` : ""}
              </button>
            ) : null}
            <button type="submit" className="hc-send" disabled={!canSubmit} aria-label="发送">
              <ArrowUp size={19} />
            </button>
          </div>
        </div>
        <div className="hc-context-strip">
          <div className="hc-menu hc-strip-menu">
            <button
              type="button"
              className="hc-context-strip-button"
              aria-expanded={projectOpen}
              onClick={() => {
                setProjectOpen((open) => !open);
                setContextOpen(false);
                setModelOpen(false);
              }}
            >
              <Folder size={16} />{cwdName}
            </button>
            {projectOpen ? (
              <div className="hc-popover hc-project-popover" aria-label="项目目录">
                <header>
                  <strong>项目目录</strong>
                  <small>{cwdName}</small>
                </header>
                <p title={cwdLabel}>{cwdLabel || "未选择工作区"}</p>
                <p>后续会在这里补最近项目、分支和工作树切换。</p>
              </div>
            ) : null}
          </div>
          <button
            type="button"
            className="hc-context-strip-button"
            onClick={() => {
              setContextOpen((open) => !open);
              setProjectOpen(false);
              setModelOpen(false);
            }}
          >
            <Gauge size={14} />{contextLabel || `上下文 ${context}`}
          </button>
          <button type="button" className="hc-context-strip-button" disabled title="上下文能力说明">
            <CircleHelp size={14} />权限与上下文会随会话更新
          </button>
          {runtimeChildTasks?.length ? <span>{runtimeChildTasks.length} 个子任务</span> : null}
        </div>
      </div>
    </form>
  );
}
