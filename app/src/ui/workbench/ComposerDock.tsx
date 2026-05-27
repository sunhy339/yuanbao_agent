import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowUp,
  ChevronDown,
  ChevronUp,
  CornerDownRight,
  MoreHorizontal,
  Plus,
  ShieldAlert,
  Trash2,
} from "lucide-react";
import type { QueuedPromptSubmission } from "../../state/eventRecordViews";
import { matchCommands, type SlashCommand } from "../../state/slashCommands";

interface ComposerDockProps {
  promptValue: string;
  onPromptChange: (value: string) => void;
  onSubmitPrompt: () => void;
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
  providerLabel: string;
  cwdLabel: string;
  permissionLabel?: string;
  permissionMode?: string;
  onPermissionModeChange?: (mode: string) => void;
  attachments?: string[];
  onAttachmentsChange?: (attachments: string[]) => void;
  onAttachmentError?: (message: string) => void;
  modelOptions?: Array<{ id: string; label: string; subtitle?: string }>;
  selectedModelId?: string;
  onSelectModel?: (modelId: string) => void;
  runtimeChildTasks?: ComposerRuntimeChildTask[];
  hidden?: boolean;
  layout?: "default" | "session";
}

export interface ComposerRuntimeChildTask {
  id: string;
  title: string;
  status?: string;
  workerName?: string;
  summary?: string;
  attention?: string;
  createdAt?: number;
  updatedAt?: number;
}

const COMMAND_LABEL = "新指令";
const COMMAND_PLACEHOLDER = "描述下一步要让本地智能体完成的事情...";
const SUBMIT_LABEL = "发送";
const SENDING_LABEL = "发送中...";
const SUPPLEMENT_LABEL = "引导";
const QUEUE_LABEL = "暂存待发";

const permissionOptions = [
  {
    id: "ask",
    title: "请求审批",
    text: "命令、文件编辑和高风险操作前先确认。",
  },
  {
    id: "edits",
    title: "允许工作区编辑",
    text: "允许直接编辑工作区文件，高风险操作仍会确认。",
  },
  {
    id: "plan",
    title: "先规划",
    text: "进入实现前先保持在可审阅的规划模式。",
  },
  {
    id: "skip",
    title: "完全访问权限",
    text: "减少审批提示，适合受控本地任务。",
  },
];

function compactProviderName(value: string) {
  if (/openai/i.test(value)) {
    return "OpenAI";
  }
  return value.split(/[/:|·-]/).map((part) => part.trim()).filter(Boolean)[0] || value;
}

function compactModelName(value?: string) {
  if (!value) {
    return "";
  }
  const match = value.match(/(?:gpt-)?(\d+(?:\.\d+)?(?:\s*[\w-]+)?)/i);
  if (match) {
    return match[1].replace(/\s+/g, " ").trim();
  }
  return value.replace(/^gpt[-_\s]*/i, "").trim() || value;
}

function normalizeRuntimeChildStatus(status?: string) {
  const normalized = status?.toLowerCase();
  if (!normalized) return "pending";
  if (["completed", "succeeded", "passed", "applied"].includes(normalized)) return "completed";
  if (["active", "running", "started", "planning", "verifying"].includes(normalized)) return "active";
  if (["failed", "error", "cancelled", "rejected"].includes(normalized)) return "failed";
  return "pending";
}

function runtimeChildState(childTask: ComposerRuntimeChildTask) {
  if (childTask.attention?.trim()) {
    return "warning";
  }
  return normalizeRuntimeChildStatus(childTask.status);
}

function parseStructuredRuntimeChildSummary(value?: string) {
  if (!value) {
    return null;
  }
  try {
    const parsed = JSON.parse(value);
    return parsed && typeof parsed === "object" && !Array.isArray(parsed)
      ? (parsed as Record<string, unknown>)
      : null;
  } catch {
    return null;
  }
}

function summarizeRuntimeChildSummary(value?: string) {
  const structured = parseStructuredRuntimeChildSummary(value);
  if (structured) {
    const changedFiles = Array.isArray(structured.changedFiles) ? structured.changedFiles.length : undefined;
    const testsRun = Array.isArray(structured.testsRun) ? structured.testsRun.length : undefined;
    const risks = Array.isArray(structured.risks) ? structured.risks.length : undefined;
    const parts = [
      changedFiles !== undefined ? (changedFiles > 0 ? `${changedFiles} 个文件改动` : "无文件改动") : null,
      testsRun !== undefined ? (testsRun > 0 ? `${testsRun} 项测试` : "未运行测试") : null,
      risks !== undefined && risks > 0 ? `${risks} 个风险` : null,
    ].filter(Boolean) as string[];
    return parts[0] ?? undefined;
  }
  const text = value?.trim();
  if (!text) {
    return undefined;
  }
  if (text.startsWith("{") || text.startsWith("[")) {
    return undefined;
  }
  return text.length > 88 ? `${text.slice(0, 84).trimEnd()}...` : text;
}

function isGenericPlannerWorker(workerName?: string) {
  return /planner worker/i.test(workerName ?? "");
}

function isGenericPlannerScanTask(task: ComposerRuntimeChildTask) {
  return (
    isGenericPlannerWorker(task.workerName) &&
    /\b(inspect|identify|understand|locate|search|scan)\b/i.test(task.title ?? "")
  );
}

type VisibleRuntimeChildTask = ComposerRuntimeChildTask & {
  count?: number;
  displaySummary?: string;
  displayWorker?: string;
  collapsedKind?: "planner_scan";
};

function groupRuntimeChildTasks(tasks: ComposerRuntimeChildTask[]): VisibleRuntimeChildTask[] {
  const cards: VisibleRuntimeChildTask[] = [];
  const grouped = new Map<string, ComposerRuntimeChildTask[]>();

  for (const task of tasks) {
    const state = runtimeChildState(task);
    const genericPlanner = isGenericPlannerWorker(task.workerName);
    if (state === "completed" && genericPlanner && !task.attention && isGenericPlannerScanTask(task)) {
      const key = `planner_scan|planner`;
      grouped.set(key, [...(grouped.get(key) ?? []), task]);
      continue;
    }
    cards.push({
      ...task,
      displaySummary: task.attention ?? summarizeRuntimeChildSummary(task.summary),
      displayWorker: genericPlanner ? undefined : task.workerName,
    });
  }

  grouped.forEach((items, key) => {
    const first = items[0];
    cards.push({
      ...first,
      id: `group:${key}`,
      count: items.length,
      title: "已完成范围确认",
      displaySummary: `${items.length} 次范围确认已完成，尚未进入修改或验证。`,
      displayWorker: undefined,
      collapsedKind: "planner_scan",
    });
  });

  return cards;
}

export function ComposerDock({
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
  providerLabel,
  cwdLabel,
  permissionLabel,
  permissionMode,
  onPermissionModeChange,
  attachments = [],
  onAttachmentsChange,
  onAttachmentError,
  modelOptions = [],
  selectedModelId,
  onSelectModel,
  runtimeChildTasks = [],
  hidden,
  layout = "default",
}: ComposerDockProps) {
  const dockRef = useRef<HTMLFormElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const modelPickerRef = useRef<HTMLDivElement>(null);
  const permissionPickerRef = useRef<HTMLDivElement>(null);
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [modelMenuOpen, setModelMenuOpen] = useState(false);
  const [permissionMenuOpen, setPermissionMenuOpen] = useState(false);

  const autoResize = useCallback((target: HTMLTextAreaElement) => {
    target.style.height = "auto";
    target.style.height = `${Math.min(target.scrollHeight, 200)}px`;
  }, []);

  const matches = useMemo(() => {
    if (!promptValue.startsWith("/") || promptValue.includes(" ")) return [];
    return matchCommands(promptValue.trim());
  }, [promptValue]);

  const showPopup = matches.length > 0;
  const canStop = Boolean(sending && onStopPrompt);
  const submitDisabled = disabled || Boolean(submitting) || (!promptValue.trim() && attachments.length === 0);
  const canQueue = Boolean(sending && onQueuePrompt && !submitDisabled);
  const canGuideQueuedPrompt = Boolean(sending && onGuideQueuedPrompt && !disabled && !submitting);
  const queuedPromptTotal = queuedPrompts.length || queuedPromptCount;
  const selectedModelValue = selectedModelId ?? modelOptions[0]?.id ?? "";
  const selectedModel = modelOptions.find((option) => option.id === selectedModelValue) ?? modelOptions[0];
  const selectedModelSubtitle =
    selectedModel?.subtitle && selectedModel.subtitle !== selectedModel.label ? selectedModel.subtitle : "";
  const composerProviderLabel = compactProviderName(selectedModelSubtitle || providerLabel);
  const composerModelLabel = compactModelName(selectedModel?.label ?? providerLabel);
  const visibleRuntimeChildTasks = runtimeChildTasks.slice(0, 6);
  const groupedRuntimeChildTasks = useMemo(
    () => groupRuntimeChildTasks(visibleRuntimeChildTasks),
    [visibleRuntimeChildTasks],
  );
  const onlyPlannerScans =
    groupedRuntimeChildTasks.length > 0 &&
    groupedRuntimeChildTasks.every((task) => task.collapsedKind === "planner_scan");
  const completedRuntimeChildCount = visibleRuntimeChildTasks.filter(
    (childTask) => runtimeChildState(childTask) === "completed",
  ).length;
  const activeRuntimeChildCount = visibleRuntimeChildTasks.filter((childTask) =>
    ["active", "pending"].includes(runtimeChildState(childTask)),
  ).length;
  const attentionRuntimeChildCount = visibleRuntimeChildTasks.filter((childTask) => runtimeChildState(childTask) === "warning").length;
  const currentPermissionOption =
    permissionOptions.find((option) => option.id === permissionMode) ??
    permissionOptions.find((option) => option.title === permissionLabel) ??
    permissionOptions[0];

  // reset selection when matches change
  useEffect(() => {
    setSelectedIndex(0);
  }, [matches.length]);

  useEffect(() => {
    if (!modelMenuOpen && !permissionMenuOpen) return;
    const handlePointerDown = (event: PointerEvent) => {
      if (!modelPickerRef.current?.contains(event.target as Node)) {
        setModelMenuOpen(false);
      }
      if (!permissionPickerRef.current?.contains(event.target as Node)) {
        setPermissionMenuOpen(false);
      }
    };
    document.addEventListener("pointerdown", handlePointerDown);
    return () => document.removeEventListener("pointerdown", handlePointerDown);
  }, [modelMenuOpen, permissionMenuOpen]);

  useEffect(() => {
    if (hidden) return undefined;
    const node = dockRef.current;
    if (!node) return undefined;

    const updateReserve = () => {
      const height = Math.ceil(node.getBoundingClientRect().height);
      document.documentElement.style.setProperty("--wb-composer-live-height", `${height + 32}px`);
    };

    updateReserve();
    const observer = typeof ResizeObserver !== "undefined" ? new ResizeObserver(updateReserve) : null;
    observer?.observe(node);
    window.addEventListener("resize", updateReserve);
    return () => {
      observer?.disconnect();
      window.removeEventListener("resize", updateReserve);
    };
  }, [attachments.length, hidden, modelMenuOpen, permissionMenuOpen, promptValue, queuedPrompts.length, visibleRuntimeChildTasks.length]);

  const applyCommand = useCallback(
    (cmd: SlashCommand) => {
      onPromptChange(cmd.name + " ");
      textareaRef.current?.focus();
    },
    [onPromptChange],
  );

  const handleAddFiles = useCallback(async () => {
    try {
      const { open } = await import("@tauri-apps/plugin-dialog");
      const selected = await open({ multiple: true, directory: false });
      const paths = Array.isArray(selected) ? selected : selected ? [selected] : [];
      if (!paths.length) return;
      onAttachmentsChange?.(Array.from(new Set([...attachments, ...paths])));
    } catch (reason) {
      onAttachmentError?.(reason instanceof Error ? reason.message : String(reason));
    }
  }, [attachments, onAttachmentError, onAttachmentsChange]);

  return (
    <form
      ref={dockRef}
      className={hidden ? "composer-dock composer-dock-hidden" : "composer-dock"}
      data-layout={layout}
      onSubmit={(event) => {
        event.preventDefault();
        if (!submitDisabled) {
          onSubmitPrompt();
        }
      }}
    >
      <div className="composer-meta" aria-label="输入区上下文">
        <span>{providerLabel}</span>
        {cwdLabel ? <span>{cwdLabel}</span> : null}
      </div>
      {visibleRuntimeChildTasks.length && !onlyPlannerScans ? (
        <details
          className="composer-runtime-child-tasks"
          aria-label="Runtime child tasks"
        >
          <summary>
            <span className="composer-task-icon" aria-hidden="true" />
            <strong>子任务进展</strong>
            <span className="composer-task-count">
              {completedRuntimeChildCount}/{visibleRuntimeChildTasks.length}
            </span>
            {activeRuntimeChildCount ? <span className="composer-child-active-count">{activeRuntimeChildCount} 进行中</span> : null}
            {attentionRuntimeChildCount ? <span className="composer-child-attention-count">{attentionRuntimeChildCount} 待留意</span> : null}
            <span className="composer-task-chevron" aria-hidden="true" />
          </summary>
          <ol>
            {groupedRuntimeChildTasks.map((childTask, index) => {
              const status = runtimeChildState(childTask);
              return (
                <li key={childTask.id || `${index}-${childTask.title}`} data-state={status}>
                  <span className="composer-task-check" aria-hidden="true" />
                  <div>
                    <span>#{index + 1}</span>
                    <strong>{childTask.title}</strong>
                    {childTask.displayWorker || childTask.displaySummary ? (
                      <small>
                        {[
                          childTask.displayWorker ? `worker: ${childTask.displayWorker}` : null,
                          childTask.displaySummary,
                        ]
                          .filter(Boolean)
                          .join(" · ")}
                      </small>
                    ) : null}
                  </div>
                  {childTask.count && childTask.count > 1 ? <b>{childTask.count} 次</b> : null}
                </li>
              );
            })}
          </ol>
        </details>
      ) : null}
      {attachments.length ? (
        <div className="composer-attachments" aria-label="附件">
          {attachments.map((attachment) => (
            <button
              key={attachment}
              type="button"
              title={attachment}
              onClick={() => onAttachmentsChange?.(attachments.filter((item) => item !== attachment))}
            >
              <span>{attachment.split(/[\\/]/).pop() ?? attachment}</span>
              <strong>×</strong>
            </button>
          ))}
        </div>
      ) : null}
      {queuedPrompts.length ? (
        <section className="composer-queued-prompts" aria-label="待发送消息">
          <ol>
            {queuedPrompts.map((item, index) => (
              <li key={item.id}>
                <span className="composer-queued-index" aria-hidden="true">{index + 1}</span>
                <p>{item.content}</p>
                {item.attachments.length ? <small>{item.attachments.length} 个附件</small> : null}
                <div className="composer-queued-actions">
                  <button
                    type="button"
                    className="composer-queued-guide"
                    disabled={!canGuideQueuedPrompt}
                    onClick={() => onGuideQueuedPrompt?.(item.id)}
                    title="引导到当前正在处理的任务"
                  >
                    <CornerDownRight size={13} strokeWidth={2} aria-hidden="true" />
                    <span>引导</span>
                  </button>
                  <button
                    type="button"
                    aria-label="上移待发送消息"
                    disabled={index === 0}
                    onClick={() => onQueuedPromptMove?.(item.id, "up")}
                    title="上移"
                  >
                    <ChevronUp size={13} strokeWidth={2} aria-hidden="true" />
                  </button>
                  <button
                    type="button"
                    aria-label="下移待发送消息"
                    disabled={index === queuedPrompts.length - 1}
                    onClick={() => onQueuedPromptMove?.(item.id, "down")}
                    title="下移"
                  >
                    <ChevronDown size={13} strokeWidth={2} aria-hidden="true" />
                  </button>
                  <button
                    type="button"
                    aria-label="删除待发送消息"
                    onClick={() => onQueuedPromptRemove?.(item.id)}
                    title="删除"
                  >
                    <Trash2 size={13} strokeWidth={2} aria-hidden="true" />
                  </button>
                  <button type="button" aria-label="更多待发送操作" disabled title="更多">
                    <MoreHorizontal size={13} strokeWidth={2} aria-hidden="true" />
                  </button>
                </div>
              </li>
            ))}
          </ol>
        </section>
      ) : null}
      <label className="composer-input">
        <span>{COMMAND_LABEL}</span>
        <textarea
          ref={textareaRef}
          aria-label="任务指令"
          value={promptValue}
          onChange={(event) => {
            onPromptChange(event.target.value);
            autoResize(event.target);
          }}
          onKeyDown={(event) => {
            // ── autocomplete navigation ──
            if (showPopup) {
              if (event.key === "ArrowDown") {
                event.preventDefault();
                setSelectedIndex((i) => (i + 1) % matches.length);
                return;
              }
              if (event.key === "ArrowUp") {
                event.preventDefault();
                setSelectedIndex((i) => (i - 1 + matches.length) % matches.length);
                return;
              }
              if (event.key === "Tab" || event.key === "Enter") {
                event.preventDefault();
                applyCommand(matches[selectedIndex]);
                return;
              }
              if (event.key === "Escape") {
                event.preventDefault();
                onPromptChange("");
                return;
              }
            }
            // ── normal submit ──
            if ((event.ctrlKey || event.metaKey) && event.key === "Enter" && !submitDisabled) {
              event.preventDefault();
              onSubmitPrompt();
            }
          }}
          placeholder={COMMAND_PLACEHOLDER}
          disabled={disabled}
          rows={1}
        />
        {showPopup && (
          <div className="slash-popup" role="listbox">
            {matches.map((cmd, idx) => (
              <button
                key={cmd.name}
                type="button"
                role="option"
                aria-selected={idx === selectedIndex}
                className={idx === selectedIndex ? "slash-popup-item slash-popup-active" : "slash-popup-item"}
                onClick={() => applyCommand(cmd)}
                onMouseEnter={() => setSelectedIndex(idx)}
              >
                <span className="slash-popup-name">{cmd.name}</span>
                <span className="slash-popup-desc">{cmd.description}</span>
              </button>
            ))}
          </div>
        )}
      </label>
      <div className="composer-toolbar" aria-label="输入工具">
        <div className="composer-toolbar-left">
          <button
            type="button"
            className="composer-tool-button"
            disabled={disabled || Boolean(submitting) || !onAttachmentsChange}
            aria-label="添加文件"
            title="添加文件"
            onClick={() => {
              void handleAddFiles();
            }}
          >
            <Plus size={17} strokeWidth={1.9} aria-hidden="true" />
            <strong>添加文件</strong>
          </button>
          {permissionLabel ? (
            <div className="composer-permission-picker" ref={permissionPickerRef}>
              <button
                type="button"
                className="composer-permission-button"
                aria-haspopup="menu"
                aria-expanded={permissionMenuOpen}
                onClick={() => setPermissionMenuOpen((current) => !current)}
              >
                <ShieldAlert size={14} strokeWidth={2} aria-hidden="true" />
                <span>{currentPermissionOption?.title ?? permissionLabel}</span>
                <ChevronDown size={13} strokeWidth={2} aria-hidden="true" />
              </button>
              {permissionMenuOpen ? (
                <div className="composer-permission-menu" role="menu" aria-label="选择权限模式">
                  {permissionOptions.map((option) => {
                    const selected = option.id === permissionMode || option.title === permissionLabel;
                    return (
                      <button
                        key={option.id}
                        type="button"
                        role="menuitemradio"
                        aria-checked={selected}
                        className="composer-permission-option"
                        onClick={() => {
                          onPermissionModeChange?.(option.id);
                          setPermissionMenuOpen(false);
                        }}
                      >
                        <span aria-hidden="true" />
                        <strong>{option.title}</strong>
                        <small>{option.text}</small>
                      </button>
                    );
                  })}
                </div>
              ) : null}
            </div>
          ) : null}
        </div>
        <div className="composer-toolbar-right">
          <div className="composer-model-picker" ref={modelPickerRef}>
            <button
              type="button"
              className="composer-model-trigger"
              aria-label={`选择模型 ${selectedModel?.label ?? providerLabel}`}
              aria-haspopup="listbox"
              aria-expanded={modelMenuOpen}
              disabled={disabled || Boolean(submitting) || !modelOptions.length || !onSelectModel}
              onClick={() => setModelMenuOpen((current) => !current)}
            >
              <span className="composer-model-full-label">{selectedModel?.label ?? providerLabel}</span>
              <span>{composerProviderLabel}</span>
              <strong>{composerModelLabel || selectedModel?.label || providerLabel}</strong>
              {selectedModelSubtitle ? <small>{selectedModelSubtitle}</small> : null}
              {modelMenuOpen ? <ChevronUp size={13} strokeWidth={2} aria-hidden="true" /> : <ChevronDown size={13} strokeWidth={2} aria-hidden="true" />}
            </button>
            {modelMenuOpen ? (
              <div className="composer-model-menu" role="listbox" aria-label="选择模型">
                {modelOptions.map((option) => {
                  const selected = option.id === selectedModelValue;
                  return (
                    <button
                      key={option.id}
                      type="button"
                      role="option"
                      aria-selected={selected}
                      className="composer-model-option"
                      onClick={() => {
                        onSelectModel?.(option.id);
                        setModelMenuOpen(false);
                      }}
                    >
                      <span aria-hidden="true" />
                      <div>
                        {option.subtitle ? <em>{option.subtitle}</em> : null}
                        <strong>{option.label}</strong>
                        <small>主模型</small>
                      </div>
                      {selected ? <b>默认</b> : null}
                    </button>
                  );
                })}
              </div>
            ) : null}
          </div>
          <label className="composer-model-select composer-model-select-hidden">
            <span>模型</span>
            <select
              aria-label="选择模型"
              value={selectedModelValue}
              disabled={disabled || Boolean(submitting) || !modelOptions.length || !onSelectModel}
              onChange={(event) => onSelectModel?.(event.currentTarget.value)}
            >
              {modelOptions.length ? (
                modelOptions.map((option) => (
                  <option key={option.id} value={option.id}>
                    {option.label}
                  </option>
                ))
              ) : (
                <option value="">{providerLabel}</option>
              )}
            </select>
          </label>
          <div className="composer-actions">
            {canStop ? (
              <button type="button" className="composer-stop" onClick={() => onStopPrompt?.()}>
                停止
              </button>
            ) : null}
            {sending && onQueuePrompt ? (
              <button
                type="button"
                className="composer-queue"
                disabled={!canQueue}
                onClick={() => onQueuePrompt?.()}
                title="暂存为待发送消息；暂存后可在上方列表选择引导到当前任务"
              >
                {QUEUE_LABEL}
                {queuedPromptTotal > 0 ? <span>{queuedPromptTotal}</span> : null}
              </button>
            ) : null}
            <button
              type="submit"
              className="composer-run"
              aria-label={submitting ? SENDING_LABEL : sending ? SUPPLEMENT_LABEL : SUBMIT_LABEL}
              disabled={submitDisabled}
              data-mode={sending ? "supplement" : "submit"}
              data-sending={submitting ? "true" : undefined}
            >
              <ArrowUp className="composer-run-icon" size={17} strokeWidth={2.5} aria-hidden="true" />
              <span className="composer-run-label">{submitting ? SENDING_LABEL : sending ? SUPPLEMENT_LABEL : SUBMIT_LABEL}</span>
            </button>
          </div>
        </div>
      </div>
    </form>
  );
}
