import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { matchCommands, type SlashCommand } from "../../state/slashCommands";

interface ComposerDockProps {
  promptValue: string;
  onPromptChange: (value: string) => void;
  onSubmitPrompt: () => void;
  onQueuePrompt?: () => void;
  onStopPrompt?: () => void;
  disabled: boolean;
  sending?: boolean;
  submitting?: boolean;
  queuedPromptCount?: number;
  providerLabel: string;
  cwdLabel: string;
  attachments?: string[];
  onAttachmentsChange?: (attachments: string[]) => void;
  onAttachmentError?: (message: string) => void;
  modelOptions?: Array<{ id: string; label: string; subtitle?: string }>;
  selectedModelId?: string;
  onSelectModel?: (modelId: string) => void;
  runtimeChildTasks?: ComposerRuntimeChildTask[];
  hidden?: boolean;
}

export interface ComposerRuntimeChildTask {
  id: string;
  title: string;
  status?: string;
  workerName?: string;
  summary?: string;
  attention?: string;
  updatedAt?: number;
}

const COMMAND_LABEL = "新指令";
const COMMAND_PLACEHOLDER = "描述下一步要让本地智能体完成的事情...";
const SUBMIT_LABEL = "发送";
const SENDING_LABEL = "发送中...";
const SUPPLEMENT_LABEL = "补充";
const QUEUE_LABEL = "暂存";

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

  return cards.sort((left, right) => {
    const leftState = runtimeChildState(left);
    const rightState = runtimeChildState(right);
    if (leftState === "warning" && rightState !== "warning") return -1;
    if (rightState === "warning" && leftState !== "warning") return 1;
    if (leftState === "active" && rightState !== "active") return -1;
    if (rightState === "active" && leftState !== "active") return 1;
    return 0;
  });
}

export function ComposerDock({
  promptValue,
  onPromptChange,
  onSubmitPrompt,
  onQueuePrompt,
  onStopPrompt,
  disabled,
  sending,
  submitting,
  queuedPromptCount = 0,
  providerLabel,
  cwdLabel,
  attachments = [],
  onAttachmentsChange,
  onAttachmentError,
  modelOptions = [],
  selectedModelId,
  onSelectModel,
  runtimeChildTasks = [],
  hidden,
}: ComposerDockProps) {
  const dockRef = useRef<HTMLFormElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const modelPickerRef = useRef<HTMLDivElement>(null);
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [modelMenuOpen, setModelMenuOpen] = useState(false);

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
  const selectedModelValue = selectedModelId ?? modelOptions[0]?.id ?? "";
  const selectedModel = modelOptions.find((option) => option.id === selectedModelValue) ?? modelOptions[0];
  const selectedModelSubtitle =
    selectedModel?.subtitle && selectedModel.subtitle !== selectedModel.label ? selectedModel.subtitle : "";
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

  // reset selection when matches change
  useEffect(() => {
    setSelectedIndex(0);
  }, [matches.length]);

  useEffect(() => {
    if (!modelMenuOpen) return;
    const handlePointerDown = (event: PointerEvent) => {
      if (!modelPickerRef.current?.contains(event.target as Node)) {
        setModelMenuOpen(false);
      }
    };
    document.addEventListener("pointerdown", handlePointerDown);
    return () => document.removeEventListener("pointerdown", handlePointerDown);
  }, [modelMenuOpen]);

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
  }, [attachments.length, hidden, modelMenuOpen, promptValue, visibleRuntimeChildTasks.length]);

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
      onSubmit={(event) => {
        event.preventDefault();
        if (!submitDisabled) {
          onSubmitPrompt();
        }
      }}
    >
      <div className="composer-meta" aria-label="输入区上下文">
        <span>{providerLabel}</span>
        <span>{cwdLabel}</span>
      </div>
      {visibleRuntimeChildTasks.length && !onlyPlannerScans ? (
        <details
          className="composer-runtime-child-tasks"
          aria-label="Runtime child tasks"
          open
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
      <div className="composer-toolbar" aria-label="输入工具">
        <button
          type="button"
          className="composer-tool-button"
          disabled={disabled || Boolean(submitting) || !onAttachmentsChange}
          onClick={() => {
            void handleAddFiles();
          }}
        >
          + 添加文件
        </button>
        <div className="composer-model-picker" ref={modelPickerRef}>
          <span>模型</span>
          <button
            type="button"
            className="composer-model-trigger"
            aria-haspopup="listbox"
            aria-expanded={modelMenuOpen}
            disabled={disabled || Boolean(submitting) || !modelOptions.length || !onSelectModel}
            onClick={() => setModelMenuOpen((current) => !current)}
          >
            <strong>{selectedModel?.label ?? providerLabel}</strong>
            {selectedModelSubtitle ? <small>{selectedModelSubtitle}</small> : null}
            <i aria-hidden="true">{modelMenuOpen ? "⌃" : "⌄"}</i>
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
      </div>
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
      <div className="composer-actions">
        {canStop ? (
          <button type="button" className="composer-stop" onClick={() => onStopPrompt?.()}>
            停止
          </button>
        ) : null}
        {sending && onQueuePrompt ? (
          <button type="button" className="composer-queue" disabled={!canQueue} onClick={() => onQueuePrompt?.()}>
            {QUEUE_LABEL}
            {queuedPromptCount > 0 ? <span>{queuedPromptCount}</span> : null}
          </button>
        ) : null}
        <button type="submit" className="composer-run" disabled={submitDisabled} data-sending={submitting ? "true" : undefined}>
          {submitting ? SENDING_LABEL : sending ? SUPPLEMENT_LABEL : SUBMIT_LABEL}
        </button>
      </div>
    </form>
  );
}
