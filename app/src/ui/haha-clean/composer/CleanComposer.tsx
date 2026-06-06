import { useCallback, useEffect, useMemo, useRef, useState, type DragEvent as ReactDragEvent, type KeyboardEvent as ReactKeyboardEvent } from "react";
import {
  ArrowUp,
  AtSign,
  ChevronDown,
  Check,
  Copy,
  Folder,
  Gauge,
  GitBranch,
  ImagePlus,
  Paperclip,
  Plus,
  Search,
  Shield,
  Slash,
  Square,
  X,
} from "lucide-react";
import type { GitLocalStatusResult, SessionLaunchOptions } from "@shared";
import type { QueuedPromptSubmission } from "../../../state/eventRecordViews";
import { RuntimeClient } from "../../../lib/runtimeClient";
import { matchCommands } from "../../../state/slashCommands";
import type { ComposerRuntimeChildTask } from "../../workbench/ComposerDock";
import type { SessionWorkspaceContextPreview, SessionWorkspaceWorktreeStatus } from "../../workbench/workspaces/session/types";
import { basename } from "../shared/text";

const composerRuntimeClient = new RuntimeClient();

export interface CleanFileReferenceOption {
  path: string;
  label?: string;
}

export interface CleanRecentWorkspaceOption {
  path: string;
  name?: string;
  repoName?: string | null;
  branch?: string | null;
  isGit?: boolean;
  sessionCount?: number;
  updatedAt?: number;
}

export type CleanSessionLaunchOptions = SessionLaunchOptions;

export interface CleanComposerProps {
  promptValue: string;
  onPromptChange(value: string): void;
  onSubmitPrompt(options?: CleanSessionLaunchOptions): void;
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
  fileReferenceOptions?: CleanFileReferenceOption[];
  onFileReferenceQueryChange?: (query: string | null) => void;
  modelOptions?: Array<{ id: string; label: string; subtitle?: string }>;
  selectedModelId?: string;
  onSelectModel?: (modelId: string) => void;
  runtimeChildTasks?: ComposerRuntimeChildTask[];
  providerLabel: string;
  cwdLabel: string;
  permissionLabel?: string;
  permissionMode?: string;
  onPermissionModeChange?: (mode: string) => void;
  onWorkspacePathChange?: (path: string) => void;
  recentWorkspaceOptions?: CleanRecentWorkspaceOption[];
  useWorktree?: boolean;
  onUseWorktreeChange?: (enabled: boolean) => void | Promise<void>;
  worktreeModeBusy?: boolean;
  onCopyText?: (text: string) => void | Promise<void>;
  contextLabel?: string;
  contextPreview?: SessionWorkspaceContextPreview | null;
  worktreeStatus?: SessionWorkspaceWorktreeStatus | null;
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
    return `${Math.max(0, Math.min(100, Math.round((used / max) * 100)))}%`;
  }
  return "--";
}

function clampPercent(value?: number | null, max?: number | null) {
  if (typeof value !== "number" || !Number.isFinite(value)) return 0;
  if (typeof max !== "number" || !Number.isFinite(max) || max <= 0) return 0;
  return Math.max(0, Math.min(100, (value / max) * 100));
}

function contextTokenDashboard(contextPreview?: SessionWorkspaceContextPreview | null) {
  const stats = contextPreview?.budgetStats;
  const max = stats?.maxContextTokens;
  const input = stats?.inputTokens ?? stats?.estimatedInputTokens ?? stats?.estimatedTokens ?? 0;
  const cacheRead = stats?.cacheReadTokens ?? 0;
  const output = stats?.outputTokens ?? 0;
  const used = stats?.estimatedInputTokens ?? stats?.estimatedTokens ?? input;
  const remaining = typeof max === "number" && Number.isFinite(max) ? Math.max(0, max - (used ?? 0)) : null;
  const percent = typeof max === "number" && max > 0 ? Math.max(0, Math.min(100, Math.round(((used ?? 0) / max) * 100))) : null;
  const updatedAt = typeof stats?.updatedAt === "number" ? stats.updatedAt : null;
  return {
    used,
    remaining,
    max,
    percent,
    input,
    cacheRead,
    output,
    updatedAt,
    estimated: Boolean(stats?.estimated),
    bars: [
      { label: "Input tokens", value: input, tone: "input", percent: clampPercent(input, max) },
      { label: "Cache read", value: cacheRead, tone: "cache", percent: clampPercent(cacheRead, max) },
      { label: "Output tokens", value: output, tone: "output", percent: clampPercent(output, max) },
    ],
  };
}

function relativeTimeLabel(timestamp?: number | null) {
  if (typeof timestamp !== "number" || !Number.isFinite(timestamp) || timestamp <= 0) {
    return null;
  }
  const diffMs = Math.max(0, Date.now() - timestamp);
  const minutes = Math.round(diffMs / 60_000);
  if (minutes < 1) return "刚刚更新";
  if (minutes < 60) return `${minutes} 分钟前更新`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} 小时前更新`;
  return `${Math.round(hours / 24)} 天前更新`;
}

function attachmentName(path: string) {
  return basename(path);
}

function isImageAttachment(path: string) {
  return /\.(png|jpe?g|gif|webp|bmp|svg)$/i.test(path);
}

function attachmentUrl(path: string) {
  return path.replace(/\\/g, "/");
}

function mergeAttachmentPaths(current: string[], incoming: string[]) {
  const next: string[] = [];
  const seen = new Set<string>();
  [...current, ...incoming].forEach((path) => {
    const normalized = path.replace(/\\/g, "/").trim();
    if (!normalized || seen.has(normalized)) return;
    seen.add(normalized);
    next.push(normalized);
  });
  return next;
}

function hasDraggedFiles(dataTransfer?: DataTransfer | null) {
  if (!dataTransfer) return false;
  if (dataTransfer.files?.length) return true;
  return Array.from(dataTransfer.types ?? []).some((type) => type === "Files" || type === "application/x-moz-file");
}

function droppedFilePath(file: File) {
  const richerFile = file as File & { path?: string; webkitRelativePath?: string };
  return richerFile.path?.trim() || richerFile.webkitRelativePath?.trim() || file.name.trim();
}

function pathsFromDrop(dataTransfer?: DataTransfer | null) {
  if (!dataTransfer?.files?.length) return [];
  return Array.from(dataTransfer.files).map(droppedFilePath).filter(Boolean);
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

function contextSectionGroupName(section: string) {
  if (section === "system_prompt") return "系统提示";
  if (section === "user_message") return "当前请求";
  if (section === "recent_conversation" || section === "session_summary") return "会话历史";
  if (section === "workspace_summary" || section === "project_focus" || section.startsWith("stable_workspace")) return "项目上下文";
  if (section.includes("memory")) return "记忆";
  if (section.startsWith("key_file") || section.startsWith("referenced_file")) return "引用文件";
  if (section.startsWith("task_history") || section === "scratchpad") return "任务状态";
  if (section.startsWith("patch_diff")) return "文件改动";
  if (section.startsWith("command_history") || section === "git_status") return "命令/Git";
  return "其他";
}

function contextSectionGroups(sections?: string[]) {
  const order = ["系统提示", "项目上下文", "记忆", "引用文件", "会话历史", "任务状态", "命令/Git", "文件改动", "当前请求", "其他"];
  const counts = new Map<string, number>();
  (sections ?? []).forEach((section) => {
    const label = contextSectionGroupName(section);
    counts.set(label, (counts.get(label) ?? 0) + 1);
  });
  return Array.from(counts.entries())
    .map(([label, count]) => ({ label, count }))
    .sort((left, right) => order.indexOf(left.label) - order.indexOf(right.label));
}

function contextPromptLayers(contextPreview?: SessionWorkspaceContextPreview | null) {
  const layers = contextPreview?.budgetStats?.promptLayers ?? [];
  return layers
    .map((layer) => ({
      name: typeof layer.name === "string" && layer.name.trim() ? layer.name.trim() : "layer",
      tokens: typeof layer.tokenEstimate === "number" && Number.isFinite(layer.tokenEstimate)
        ? layer.tokenEstimate
        : null,
    }))
    .filter((layer) => layer.name)
    .slice(0, 5);
}

const QUIET_CONTEXT_STEPS = [
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

function visibleContextStep(contextPreview?: SessionWorkspaceContextPreview | null) {
  const currentStep = contextPreview?.taskFocus?.currentStep?.trim();
  if (!currentStep) return null;
  return QUIET_CONTEXT_STEPS.some((pattern) => pattern.test(currentStep)) ? null : currentStep;
}

function contextCompositionRows(contextPreview?: SessionWorkspaceContextPreview | null) {
  const stats = contextPreview?.budgetStats;
  const max = stats?.maxContextTokens;
  const rows: Array<{ label: string; value: string; percent: number }> = [];
  const addTokens = (label: string, tokens?: number | null) => {
    if (typeof tokens !== "number" || !Number.isFinite(tokens)) return;
    rows.push({
      label,
      value: formatTokenCount(tokens),
      percent: typeof max === "number" && max > 0 ? Math.max(2, Math.min(100, (tokens / max) * 100)) : 0,
    });
  };
  const promptLayerTokens = (stats?.promptLayers ?? []).reduce((total, layer) => {
    const value = typeof layer.tokenEstimate === "number" && Number.isFinite(layer.tokenEstimate) ? layer.tokenEstimate : 0;
    return total + value;
  }, 0);
  addTokens("系统提示", promptLayerTokens || undefined);
  addTokens("工具 schema", stats?.toolSchemaTokens);
  addTokens("上下文消息", stats?.messageTokens);
  addTokens("稳定前缀", stats?.stablePrefixTokens);
  if (contextPreview?.toolCount) {
    rows.push({ label: "可用工具", value: `${contextPreview.toolCount} 个`, percent: 0 });
  }
  return rows;
}

function fileReferenceLabel(option: CleanFileReferenceOption) {
  return option.label || basename(option.path) || option.path;
}

function normalizeWorkspaceOption(option: string | CleanRecentWorkspaceOption): CleanRecentWorkspaceOption {
  return typeof option === "string" ? { path: option } : option;
}

function recentWorkspaceOptionsForLaunch(currentPath: string, workspaceOptions: CleanRecentWorkspaceOption[] = []) {
  if (typeof window === "undefined") {
    return uniqueWorkspaceOptions([{ path: currentPath }, ...workspaceOptions]);
  }
  const candidates: CleanRecentWorkspaceOption[] = [
    { path: currentPath },
    ...workspaceOptions,
    { path: window.localStorage.getItem("haha-clean:last-workspace") ?? "" },
    { path: window.localStorage.getItem("yuanbao-agent:last-workspace") ?? "" },
  ];
  try {
    const stored = JSON.parse(window.localStorage.getItem("haha-clean:recent-workspaces") ?? "[]");
    if (Array.isArray(stored)) {
      candidates.push(...stored
        .filter((item): item is string | CleanRecentWorkspaceOption => (
          typeof item === "string" ||
          (Boolean(item) && typeof item === "object" && typeof (item as CleanRecentWorkspaceOption).path === "string")
        ))
        .map(normalizeWorkspaceOption));
    }
  } catch {
    // Ignore invalid local cache.
  }
  return uniqueWorkspaceOptions(candidates);
}

function uniqueWorkspaceOptions(options: CleanRecentWorkspaceOption[]) {
  const seen = new Set<string>();
  return options
    .map((option) => ({
      ...option,
      path: option.path.trim(),
      name: option.name?.trim() || undefined,
    }))
    .filter((option) => option.path)
    .filter((option) => {
      const key = option.path.replace(/\\/g, "/").toLowerCase();
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    })
    .slice(0, 8);
}

function rememberWorkspacePath(path: string) {
  if (typeof window === "undefined") return;
  const normalized = path.trim();
  if (!normalized) return;
  const next = recentWorkspaceOptionsForLaunch(normalized).map((option) => option.path);
  window.localStorage.setItem("haha-clean:last-workspace", normalized);
  window.localStorage.setItem("haha-clean:recent-workspaces", JSON.stringify(next));
}

function isTauriBridgeAvailable() {
  return typeof window !== "undefined" && ("__TAURI_INTERNALS__" in window || "__TAURI__" in window);
}

function errorMessage(reason: unknown) {
  return reason instanceof Error ? reason.message : String(reason);
}

function gitFilePaths(status?: GitLocalStatusResult | null) {
  return status?.files?.map((file) => file.path).filter(Boolean) ?? [];
}

function branchMetaLabel(branch: NonNullable<GitLocalStatusResult["branches"]>[number]) {
  if (branch.current) return "当前分支";
  if (branch.checkedOut) return "已在其他工作树中检出";
  if (branch.remote && !branch.local) return branch.remoteRef || "远程分支";
  return "本地分支";
}

function isLaunchBranchOption(branch: NonNullable<GitLocalStatusResult["branches"]>[number]) {
  if (branch.current) return true;
  if (/^(agent|worktree-desktop)-/.test(branch.name)) return false;
  if (/^(agent|worktree-desktop)\//.test(branch.name)) return false;
  return true;
}

function worktreeModeFromStorage() {
  if (typeof window === "undefined") return false;
  return window.localStorage.getItem("haha-clean:use-worktree") === "true";
}

function rememberWorktreeMode(value: boolean) {
  if (typeof window === "undefined") return;
  window.localStorage.setItem("haha-clean:use-worktree", value ? "true" : "false");
}

function normalizeFileReferences(options: CleanFileReferenceOption[]) {
  const seen = new Set<string>();
  return options.filter((option) => {
    const key = option.path.trim();
    if (!key || seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function getFileReferenceToken(value: string, caretIndex: number) {
  const index = Math.max(0, Math.min(value.length, caretIndex));
  const before = value.slice(0, index);
  const match = before.match(/(?:^|\s)@([^\s@]*)$/);
  if (!match) return null;
  const query = match[1] ?? "";
  return {
    query,
    start: before.length - query.length - 1,
    end: index,
  };
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
  if (!value) return null;
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
  if (!text || text.startsWith("{") || text.startsWith("[")) {
    return undefined;
  }
  return text.length > 88 ? `${text.slice(0, 84).trimEnd()}...` : text;
}

function normalizeAgentLabel(value?: string) {
  return value?.trim().replace(/[_-]+/g, " ").replace(/\s+/g, " ").toLowerCase() ?? "";
}

function isGenericAgentWorker(workerName?: string, agentType?: string) {
  const worker = normalizeAgentLabel(workerName);
  const role = normalizeAgentLabel(agentType);
  if (!worker) return false;
  return Boolean(role) && (worker === role || worker === `${role} worker` || worker === `${role} agent`);
}

function isGenericPlannerWorker(workerName?: string, agentType?: string) {
  const worker = normalizeAgentLabel(workerName);
  return isGenericAgentWorker(workerName, agentType) || worker === "planner" || worker === "planner worker" || worker === "planner agent";
}

function isGenericPlannerScanTask(task: ComposerRuntimeChildTask) {
  return (
    isGenericPlannerWorker(task.workerName, task.agentType) &&
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
    const genericWorker = isGenericAgentWorker(task.workerName, task.agentType);
    const genericPlanner = isGenericPlannerWorker(task.workerName, task.agentType);
    if (state === "completed" && genericPlanner && !task.attention && isGenericPlannerScanTask(task)) {
      const key = "planner_scan|planner";
      grouped.set(key, [...(grouped.get(key) ?? []), task]);
      continue;
    }
    cards.push({
      ...task,
      displaySummary: task.attention ?? summarizeRuntimeChildSummary(task.summary),
      displayWorker: genericWorker || genericPlanner ? undefined : task.workerName,
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
  stopPending = false,
  queuedPromptCount = 0,
  attachments = [],
  onAttachmentsChange,
  onAttachmentError,
  fileReferenceOptions = [],
  onFileReferenceQueryChange,
  modelOptions = [],
  selectedModelId,
  onSelectModel,
  runtimeChildTasks = [],
  providerLabel,
  cwdLabel,
  permissionLabel,
  permissionMode,
  onPermissionModeChange,
  onWorkspacePathChange,
  recentWorkspaceOptions = [],
  useWorktree: controlledUseWorktree,
  onUseWorktreeChange,
  worktreeModeBusy = false,
  onCopyText,
  contextLabel,
  contextPreview,
  worktreeStatus,
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
  const [workspaceOpen, setWorkspaceOpen] = useState(false);
  const [branchOpen, setBranchOpen] = useState(false);
  const [worktreeOpen, setWorktreeOpen] = useState(false);
  const [branchFilter, setBranchFilter] = useState("");
  const [selectedBranchName, setSelectedBranchName] = useState<string | null>(null);
  const [localGitStatus, setLocalGitStatus] = useState<GitLocalStatusResult | null>(null);
  const [gitLoading, setGitLoading] = useState(false);
  const [gitError, setGitError] = useState<string | null>(null);
  const [localUseWorktree, setLocalUseWorktree] = useState(worktreeModeFromStorage);
  const [launchUseWorktree, setLaunchUseWorktree] = useState(controlledUseWorktree ?? worktreeModeFromStorage());
  const [slashIndex, setSlashIndex] = useState(0);
  const [slashDismissedFor, setSlashDismissedFor] = useState("");
  const [caretIndex, setCaretIndex] = useState(promptValue.length);
  const [fileReferenceIndex, setFileReferenceIndex] = useState(0);
  const [fileReferenceDismissedFor, setFileReferenceDismissedFor] = useState("");
  const [pendingPermissionMode, setPendingPermissionMode] = useState<string | null>(null);
  const [dragActive, setDragActive] = useState(false);
  const selectedModel = modelOptions.find((option) => option.id === selectedModelId) ?? modelOptions[0];
  const hasPayload = Boolean(promptValue.trim() || attachments.length);
  const canSubmit = !disabled && !submitting && hasPayload;
  const canQueue = Boolean(sending && canSubmit && onQueuePrompt);
  const visibleRuntimeChildTasks = runtimeChildTasks.slice(0, 6);
  const groupedRuntimeChildTasks = useMemo(
    () => groupRuntimeChildTasks(visibleRuntimeChildTasks),
    [visibleRuntimeChildTasks],
  );
  const completedRuntimeChildCount = visibleRuntimeChildTasks.filter(
    (childTask) => runtimeChildState(childTask) === "completed",
  ).length;
  const activeRuntimeChildCount = visibleRuntimeChildTasks.filter((childTask) =>
    ["active", "pending"].includes(runtimeChildState(childTask)),
  ).length;
  const attentionRuntimeChildCount = visibleRuntimeChildTasks.filter((childTask) => runtimeChildState(childTask) === "warning").length;
  const onlyPlannerScans = groupedRuntimeChildTasks.length === 1 && groupedRuntimeChildTasks[0]?.collapsedKind === "planner_scan";
  const context = contextUsage(contextPreview);
  const contextRows = contextBudgetRows(contextPreview);
  const contextComposition = contextCompositionRows(contextPreview);
  const contextGroups = contextSectionGroups(contextPreview?.budgetStats?.includedSections);
  const contextLayers = contextPromptLayers(contextPreview);
  const contextStep = visibleContextStep(contextPreview);
  const contextDashboard = contextTokenDashboard(contextPreview);
  const trimmedSections = contextPreview?.budgetStats?.trimmedSections ?? [];
  const droppedSections = contextPreview?.budgetStats?.droppedSections ?? [];
  const cwdName = basename(cwdLabel);
  const isNewSession = variant === "new";
  const useWorktree = isNewSession ? launchUseWorktree : controlledUseWorktree ?? localUseWorktree;
  const recentWorkspaces = useMemo(
    () => recentWorkspaceOptionsForLaunch(cwdLabel, recentWorkspaceOptions),
    [cwdLabel, recentWorkspaceOptions],
  );
  const dirtyFiles = (isNewSession ? localGitStatus?.dirtyFiles ?? worktreeStatus?.dirtyFiles : worktreeStatus?.dirtyFiles ?? localGitStatus?.dirtyFiles) ?? 0;
  const worktreeFiles = (isNewSession ? gitFilePaths(localGitStatus).length ? gitFilePaths(localGitStatus) : worktreeStatus?.files ?? [] : worktreeStatus?.files ?? gitFilePaths(localGitStatus));
  const worktreeFileCopyText = worktreeFiles.join("\n");
  const branchLabel = (isNewSession ? selectedBranchName ?? localGitStatus?.branch ?? worktreeStatus?.branch : worktreeStatus?.branch || localGitStatus?.branch) || "未检测";
  const upstreamLabel = (isNewSession ? localGitStatus?.upstream ?? worktreeStatus?.upstream : worktreeStatus?.upstream || localGitStatus?.upstream) || "无上游";
  const aheadCount = (isNewSession ? localGitStatus?.ahead ?? worktreeStatus?.ahead : worktreeStatus?.ahead ?? localGitStatus?.ahead) ?? 0;
  const behindCount = (isNewSession ? localGitStatus?.behind ?? worktreeStatus?.behind : worktreeStatus?.behind ?? localGitStatus?.behind) ?? 0;
  const cleanWorktree = (isNewSession ? localGitStatus?.clean ?? worktreeStatus?.clean : worktreeStatus?.clean ?? localGitStatus?.clean) ?? dirtyFiles === 0;
  const syncLabel = [
    aheadCount ? `领先 ${aheadCount}` : "",
    behindCount ? `落后 ${behindCount}` : "",
  ].filter(Boolean).join(" · ") || (upstreamLabel !== "无上游" ? "已同步" : "本地分支");
  const branchOptions = useMemo(() => {
    const query = branchFilter.trim().toLowerCase();
    return (localGitStatus?.branches ?? [])
      .filter(isLaunchBranchOption)
      .filter((branch) => {
        if (!query) return true;
        return [
          branch.name,
          branch.remoteRef ?? "",
          branch.worktreePath ?? "",
        ].some((value) => value.toLowerCase().includes(query));
      })
      .slice(0, 40);
  }, [branchFilter, localGitStatus?.branches]);
  const selectedBranch = localGitStatus?.branches?.find((branch) => branch.name === branchLabel) ?? null;
  const selectedBranchOptionName = selectedBranchName ?? localGitStatus?.branch ?? null;
  const selectedBranchWarnsOnCurrentWorktree = Boolean(
    isNewSession &&
    selectedBranch &&
    selectedBranch.name !== localGitStatus?.branch &&
    !useWorktree &&
    ((localGitStatus?.dirtyFiles ?? 0) > 0 || selectedBranch.checkedOut),
  );
  const launchOptions = useCallback((): CleanSessionLaunchOptions | undefined => {
    if (!isNewSession || !cwdLabel.trim()) return undefined;
    const repository: NonNullable<SessionLaunchOptions["repository"]> = { worktree: useWorktree };
    if (selectedBranchName) {
      repository.branch = selectedBranchName;
    }
    return {
      workDir: cwdLabel.trim(),
      repository,
    };
  }, [cwdLabel, isNewSession, selectedBranchName, useWorktree]);
  const submitPrompt = useCallback(() => {
    onSubmitPrompt(launchOptions());
  }, [launchOptions, onSubmitPrompt]);
  const chooseWorkspaceFolder = useCallback(async () => {
    if (!onWorkspacePathChange) return;
    try {
      const { open } = await import("@tauri-apps/plugin-dialog");
      const selected = await open({
        multiple: false,
        directory: true,
        title: "选择项目文件夹",
      });
      const selectedPath = Array.isArray(selected) ? selected[0] : selected;
      if (typeof selectedPath === "string" && selectedPath.trim()) {
        rememberWorkspacePath(selectedPath);
        setLocalGitStatus(null);
        setSelectedBranchName(null);
        onWorkspacePathChange(selectedPath);
        setWorkspaceOpen(false);
      }
    } catch (reason) {
      onAttachmentError?.(reason instanceof Error ? reason.message : String(reason));
    }
  }, [onAttachmentError, onWorkspacePathChange]);
  const selectRecentWorkspace = useCallback((path: string) => {
    if (!onWorkspacePathChange) return;
    rememberWorkspacePath(path);
    setLocalGitStatus(null);
    setSelectedBranchName(null);
    onWorkspacePathChange(path);
    setWorkspaceOpen(false);
  }, [onWorkspacePathChange]);
  const refreshLocalGitStatus = useCallback(async () => {
    if (!cwdLabel.trim() || !isTauriBridgeAvailable()) return;
    setGitLoading(true);
    setGitError(null);
    try {
      const status = await composerRuntimeClient.gitLocalStatus({ cwd: cwdLabel });
      setLocalGitStatus(status);
      setSelectedBranchName((current) => current ?? status.branch ?? status.defaultBranch ?? status.branches?.[0]?.name ?? null);
    } catch (reason) {
      setLocalGitStatus(null);
      setGitError(errorMessage(reason));
    } finally {
      setGitLoading(false);
    }
  }, [cwdLabel]);
  const selectBranch = useCallback((branch: string) => {
    if (!branch.trim()) return;
    setSelectedBranchName(branch);
    setBranchOpen(false);
    setBranchFilter("");
  }, []);
  const selectWorktreeMode = useCallback((nextUseWorktree: boolean) => {
    if (isNewSession) {
      setLaunchUseWorktree(nextUseWorktree);
    } else {
      setLocalUseWorktree(nextUseWorktree);
      void onUseWorktreeChange?.(nextUseWorktree);
    }
    rememberWorktreeMode(nextUseWorktree);
    setWorktreeOpen(false);
  }, [isNewSession, onUseWorktreeChange]);
  const contextSummary = [
    "上下文",
    contextLabel || `当前占用 ${context}`,
    `已使用: ${formatTokenCount(contextDashboard.used)}`,
    `剩余: ${formatTokenCount(contextDashboard.remaining)}`,
    `窗口: ${formatTokenCount(contextDashboard.max)}`,
    `Input tokens: ${formatTokenCount(contextDashboard.input)}`,
    `Cache read: ${formatTokenCount(contextDashboard.cacheRead)}`,
    `Output tokens: ${formatTokenCount(contextDashboard.output)}`,
    ...contextRows.map((row) => `${row.label}: ${row.value}`),
    contextStep ? `当前步骤: ${contextStep}` : "",
    contextPreview?.projectFocus ? `项目焦点: ${contextPreview.projectFocus}` : "",
    contextGroups.length ? `纳入上下文: ${contextGroups.map((group) => `${group.label} ${group.count}`).join("、")}` : "",
    contextLayers.length ? `系统提示层: ${contextLayers.map((layer) => `${layer.name}${layer.tokens ? ` ${layer.tokens}` : ""}`).join("、")}` : "",
    trimmedSections.length || droppedSections.length
      ? `已压缩: ${[...trimmedSections, ...droppedSections].slice(0, 4).join("、")}`
      : "",
  ].filter(Boolean).join("\n");
  const dropLabel = attachments.length ? "松开添加到附件" : "松开添加文件或图片";
  const slashMatches = useMemo(() => {
    if (promptValue === slashDismissedFor) return [];
    if (!promptValue.startsWith("/") || promptValue.includes(" ")) return [];
    return matchCommands(promptValue.trim()).slice(0, 6);
  }, [promptValue, slashDismissedFor]);
  const normalizedFileReferences = useMemo(
    () => normalizeFileReferences(fileReferenceOptions),
    [fileReferenceOptions],
  );
  const activeFileReference = useMemo(() => {
    if (promptValue === fileReferenceDismissedFor) return null;
    return getFileReferenceToken(promptValue, caretIndex);
  }, [caretIndex, fileReferenceDismissedFor, promptValue]);
  const fileReferenceMatches = useMemo(() => {
    if (!activeFileReference) return [];
    const query = activeFileReference.query.toLowerCase();
    const candidates = query
      ? normalizedFileReferences.filter((option) => {
          const haystack = `${option.path} ${fileReferenceLabel(option)}`.toLowerCase();
          return haystack.includes(query);
        })
      : normalizedFileReferences;
    return candidates.slice(0, 8);
  }, [activeFileReference, normalizedFileReferences]);

  useEffect(() => {
    onFileReferenceQueryChange?.(activeFileReference?.query ?? null);
  }, [activeFileReference?.query, onFileReferenceQueryChange]);

  useEffect(() => {
    setLocalGitStatus(null);
    setGitError(null);
    setBranchFilter("");
    setSelectedBranchName(null);
    if (!cwdLabel.trim() || !isTauriBridgeAvailable()) return undefined;
    let cancelled = false;
    setGitLoading(true);
    composerRuntimeClient
      .gitLocalStatus({ cwd: cwdLabel })
      .then((status) => {
        if (cancelled) return;
        setLocalGitStatus(status);
        setSelectedBranchName(status.branch ?? status.defaultBranch ?? status.branches?.[0]?.name ?? null);
      })
      .catch((reason) => {
        if (cancelled) return;
        setGitError(errorMessage(reason));
      })
      .finally(() => {
        if (!cancelled) setGitLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [cwdLabel]);

  useEffect(() => {
    setSlashIndex(0);
    if (slashDismissedFor && slashDismissedFor !== promptValue) {
      setSlashDismissedFor("");
    }
  }, [promptValue, slashDismissedFor]);

  useEffect(() => {
    setFileReferenceIndex(0);
    if (fileReferenceDismissedFor && fileReferenceDismissedFor !== promptValue) {
      setFileReferenceDismissedFor("");
    }
  }, [fileReferenceDismissedFor, promptValue]);

  useEffect(() => {
    setCaretIndex((current) => Math.min(current, promptValue.length));
  }, [promptValue.length]);

  useEffect(() => {
    if (!permissionOpen) {
      setPendingPermissionMode(null);
    }
  }, [permissionOpen]);

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

  const addAttachmentPaths = useCallback(
    (paths: string[]) => {
      const normalized = paths.map((path) => path.trim()).filter(Boolean);
      if (!normalized.length) {
        onAttachmentError?.("没有读取到可添加的文件。");
        return;
      }
      if (!onAttachmentsChange || disabled) return;
      const next = mergeAttachmentPaths(attachments, normalized);
      onAttachmentsChange(next);
    },
    [attachments, disabled, onAttachmentError, onAttachmentsChange],
  );

  const addFiles = useCallback(async () => {
    try {
      const { open } = await import("@tauri-apps/plugin-dialog");
      const selected = await open({ multiple: true, directory: false });
      const paths = Array.isArray(selected) ? selected : selected ? [selected] : [];
      if (paths.length) {
        addAttachmentPaths(paths);
      }
    } catch (reason) {
      onAttachmentError?.(reason instanceof Error ? reason.message : String(reason));
    }
  }, [addAttachmentPaths, onAttachmentError]);

  const handleDragEnter = useCallback(
    (event: ReactDragEvent<HTMLFormElement>) => {
      if (disabled || !hasDraggedFiles(event.dataTransfer)) return;
      event.preventDefault();
      event.stopPropagation();
      event.dataTransfer.dropEffect = "copy";
      setDragActive(true);
    },
    [disabled],
  );

  const handleDragOver = useCallback(
    (event: ReactDragEvent<HTMLFormElement>) => {
      if (disabled || !hasDraggedFiles(event.dataTransfer)) return;
      event.preventDefault();
      event.stopPropagation();
      event.dataTransfer.dropEffect = "copy";
      setDragActive(true);
    },
    [disabled],
  );

  const handleDragLeave = useCallback((event: ReactDragEvent<HTMLFormElement>) => {
    const nextTarget = event.relatedTarget;
    if (nextTarget instanceof Node && event.currentTarget.contains(nextTarget)) return;
    setDragActive(false);
  }, []);

  const handleDrop = useCallback(
    (event: ReactDragEvent<HTMLFormElement>) => {
      if (disabled || !hasDraggedFiles(event.dataTransfer)) return;
      event.preventDefault();
      event.stopPropagation();
      setDragActive(false);
      addAttachmentPaths(pathsFromDrop(event.dataTransfer));
    },
    [addAttachmentPaths, disabled],
  );

  useEffect(() => {
    if (hidden || typeof window === "undefined" || !(window as unknown as { __TAURI_INTERNALS__?: unknown }).__TAURI_INTERNALS__) {
      return undefined;
    }
    let disposed = false;
    let unlisten: (() => void) | undefined;
    const pointIsNearComposer = (position?: { x: number; y: number }, padding = 28) => {
      const node = formRef.current;
      if (!node || !position) return false;
      const rect = node.getBoundingClientRect();
      const ratio = window.devicePixelRatio || 1;
      const x = position.x > window.innerWidth + padding ? position.x / ratio : position.x;
      const y = position.y > window.innerHeight + padding ? position.y / ratio : position.y;
      return x >= rect.left - padding && x <= rect.right + padding && y >= rect.top - padding && y <= rect.bottom + padding;
    };
    void import("@tauri-apps/api/webview")
      .then(({ getCurrentWebview }) =>
        getCurrentWebview().onDragDropEvent((event) => {
          if (disposed || disabled) return;
          const payload = event.payload;
          if (payload.type === "enter" || payload.type === "over") {
            setDragActive(pointIsNearComposer(payload.position));
            return;
          }
          if (payload.type === "leave") {
            setDragActive(false);
            return;
          }
          setDragActive(false);
          if (payload.type === "drop" && pointIsNearComposer(payload.position, 48)) {
            addAttachmentPaths(payload.paths);
          }
        }),
      )
      .then((cleanup) => {
        if (disposed) {
          cleanup();
        } else {
          unlisten = cleanup;
        }
      })
      .catch(() => undefined);
    return () => {
      disposed = true;
      unlisten?.();
    };
  }, [addAttachmentPaths, disabled, hidden]);

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

  const syncCaret = useCallback(() => {
    const node = textareaRef.current;
    setCaretIndex(node?.selectionStart ?? promptValue.length);
  }, [promptValue.length]);

  const insertFileReferenceTrigger = useCallback(() => {
    const node = textareaRef.current;
    const start = node?.selectionStart ?? promptValue.length;
    const end = node?.selectionEnd ?? start;
    const before = promptValue.slice(0, start);
    const after = promptValue.slice(end);
    const prefix = before && !/\s$/.test(before) ? " " : "";
    const inserted = `${prefix}@`;
    const next = `${before}${inserted}${after}`;
    const nextCaret = before.length + inserted.length;
    onPromptChange(next);
    setCaretIndex(nextCaret);
    setFileReferenceDismissedFor("");
    setPlusOpen(false);
    window.requestAnimationFrame(() => {
      const input = textareaRef.current;
      input?.focus();
      input?.setSelectionRange(nextCaret, nextCaret);
    });
  }, [onPromptChange, promptValue]);

  const applySlashCommand = useCallback(
    (name: string) => {
      onPromptChange(`${name} `);
      setSlashDismissedFor("");
      window.requestAnimationFrame(() => textareaRef.current?.focus());
    },
    [onPromptChange],
  );

  const applyFileReference = useCallback(
    (option: CleanFileReferenceOption) => {
      if (!activeFileReference) return;
      const reference = `@${option.path} `;
      const next = `${promptValue.slice(0, activeFileReference.start)}${reference}${promptValue.slice(activeFileReference.end)}`;
      const nextCaret = activeFileReference.start + reference.length;
      onPromptChange(next);
      setFileReferenceDismissedFor("");
      setCaretIndex(nextCaret);
      window.requestAnimationFrame(() => {
        const input = textareaRef.current;
        input?.focus();
        input?.setSelectionRange(nextCaret, nextCaret);
      });
    },
    [activeFileReference, onPromptChange, promptValue],
  );

  const selectPermissionMode = useCallback(
    (mode: string) => {
      if (mode === "skip" && permissionMode !== "skip") {
        setPendingPermissionMode(mode);
        return;
      }
      onPermissionModeChange?.(mode);
      setPendingPermissionMode(null);
      setPermissionOpen(false);
    },
    [onPermissionModeChange, permissionMode],
  );

  const copyText = useCallback(
    (text: string) => {
      if (!text) return;
      if (onCopyText) {
        void onCopyText(text);
        return;
      }
      void window.navigator.clipboard?.writeText(text);
    },
    [onCopyText],
  );

  const handlePromptKeyDown = useCallback(
    (event: ReactKeyboardEvent<HTMLTextAreaElement>) => {
      if (fileReferenceMatches.length) {
        if (event.key === "ArrowDown") {
          event.preventDefault();
          setFileReferenceIndex((current) => (current + 1) % fileReferenceMatches.length);
          return;
        }
        if (event.key === "ArrowUp") {
          event.preventDefault();
          setFileReferenceIndex((current) => (current - 1 + fileReferenceMatches.length) % fileReferenceMatches.length);
          return;
        }
        if (event.key === "Home") {
          event.preventDefault();
          setFileReferenceIndex(0);
          return;
        }
        if (event.key === "End") {
          event.preventDefault();
          setFileReferenceIndex(fileReferenceMatches.length - 1);
          return;
        }
        if (event.key === "Enter" || event.key === "Tab") {
          event.preventDefault();
          applyFileReference(fileReferenceMatches[fileReferenceIndex] ?? fileReferenceMatches[0]);
          return;
        }
      }
      if (activeFileReference && event.key === "Escape") {
        event.preventDefault();
        setFileReferenceDismissedFor(promptValue);
        return;
      }

      if (slashMatches.length) {
        if (event.key === "ArrowDown") {
          event.preventDefault();
          setSlashIndex((current) => (current + 1) % slashMatches.length);
          return;
        }
        if (event.key === "ArrowUp") {
          event.preventDefault();
          setSlashIndex((current) => (current - 1 + slashMatches.length) % slashMatches.length);
          return;
        }
        if (event.key === "Home") {
          event.preventDefault();
          setSlashIndex(0);
          return;
        }
        if (event.key === "End") {
          event.preventDefault();
          setSlashIndex(slashMatches.length - 1);
          return;
        }
        if (event.key === "Enter" || event.key === "Tab") {
          event.preventDefault();
          applySlashCommand(slashMatches[slashIndex]?.name ?? slashMatches[0].name);
          return;
        }
        if (event.key === "Escape") {
          event.preventDefault();
          setSlashDismissedFor(promptValue);
          return;
        }
      }

      if ((event.ctrlKey || event.metaKey) && event.key === "Enter" && canSubmit) {
        event.preventDefault();
        submitPrompt();
      }
    },
    [
      activeFileReference,
      applyFileReference,
      applySlashCommand,
      canSubmit,
      fileReferenceIndex,
      fileReferenceMatches,
      promptValue,
      slashIndex,
      slashMatches,
      submitPrompt,
    ],
  );

  if (hidden) return null;

  return (
    <form
      ref={formRef}
      className="hc-composer"
      aria-label="消息输入"
      data-variant={variant}
      onDragEnter={handleDragEnter}
      onDragLeave={handleDragLeave}
      onDragOver={handleDragOver}
      onDrop={handleDrop}
      onSubmit={(event) => {
        event.preventDefault();
        if (canSubmit) submitPrompt();
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

      {visibleRuntimeChildTasks.length && !onlyPlannerScans ? (
        <details className="hc-runtime-child-tasks" aria-label="Runtime child tasks">
          <summary>
            <span className="hc-runtime-child-dot" aria-hidden="true" />
            <strong>子任务进展</strong>
            <span>{completedRuntimeChildCount}/{visibleRuntimeChildTasks.length}</span>
            {activeRuntimeChildCount ? <em>{activeRuntimeChildCount} 进行中</em> : null}
            {attentionRuntimeChildCount ? <em>{attentionRuntimeChildCount} 待留意</em> : null}
          </summary>
          <ol>
            {groupedRuntimeChildTasks.map((childTask, index) => {
              const status = runtimeChildState(childTask);
              return (
                <li key={childTask.id || `${index}-${childTask.title}`} data-state={status}>
                  <span className="hc-runtime-child-state" aria-hidden="true" />
                  <div>
                    <span>#{index + 1}</span>
                    <strong>{childTask.title}</strong>
                    {childTask.displayWorker || childTask.displaySummary ? (
                      <small>
                        {[
                          childTask.displayWorker ? `worker: ${childTask.displayWorker}` : null,
                          childTask.displaySummary,
                        ].filter(Boolean).join(" · ")}
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

      <div className="hc-composer-card" data-dragging={dragActive ? "true" : "false"}>
        {dragActive ? (
          <div className="hc-drop-overlay" role="status" aria-live="polite">
            <div>
              <ImagePlus size={18} />
              <span>{dropLabel}</span>
            </div>
          </div>
        ) : null}
        {attachments.length ? (
          <div className="hc-attachments">
            {attachments.map((path) => (
              <span key={path} data-kind={isImageAttachment(path) ? "image" : "file"}>
                {isImageAttachment(path) ? (
                  <img alt={attachmentName(path)} loading="lazy" src={attachmentUrl(path)} />
                ) : (
                  <Paperclip size={13} />
                )}
                <em title={path}>{attachmentName(path)}</em>
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
          aria-label="任务指令"
          placeholder={variant === "new" ? "随便问点什么..." : "描述下一步要本地智能体完成的事情..."}
          disabled={disabled}
          onChange={(event) => {
            onPromptChange(event.currentTarget.value);
            setCaretIndex(event.currentTarget.selectionStart ?? event.currentTarget.value.length);
          }}
          onClick={syncCaret}
          onKeyDown={handlePromptKeyDown}
          onKeyUp={syncCaret}
          onSelect={syncCaret}
        />
        {activeFileReference ? (
          <div
            className="hc-file-reference-panel"
            role="listbox"
            aria-label="文件引用"
            aria-activedescendant={fileReferenceMatches.length ? `hc-file-reference-option-${fileReferenceIndex}` : undefined}
          >
            {fileReferenceMatches.length ? (
              fileReferenceMatches.map((option, index) => (
                <button
                  type="button"
                  id={`hc-file-reference-option-${index}`}
                  key={option.path}
                  role="option"
                  aria-selected={index === fileReferenceIndex}
                  data-active={index === fileReferenceIndex}
                  onMouseEnter={() => setFileReferenceIndex(index)}
                  onClick={() => applyFileReference(option)}
                >
                  <strong>{fileReferenceLabel(option)}</strong>
                  <span>{option.path}</span>
                </button>
              ))
            ) : (
              <p>没有匹配文件。继续输入，或先从文件区打开/产生改动后再引用。</p>
            )}
          </div>
        ) : null}
        {slashMatches.length ? (
          <div
            className="hc-slash-panel"
            role="listbox"
            aria-label="斜杠命令"
            aria-activedescendant={`hc-slash-option-${slashIndex}`}
          >
            {slashMatches.map((command, index) => (
              <button
                type="button"
                id={`hc-slash-option-${index}`}
                key={command.name}
                role="option"
                aria-selected={index === slashIndex}
                data-active={index === slashIndex}
                onMouseEnter={() => setSlashIndex(index)}
                onClick={() => applySlashCommand(command.name)}
              >
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
                  <button type="button" onClick={insertFileReferenceTrigger}>
                    <AtSign size={17} />
                    <span>引用项目文件</span>
                  </button>
                  <button type="button" onClick={insertSlash}>
                    <Slash size={17} />
                    <span>斜杠命令</span>
                  </button>
                </div>
              ) : null}
            </div>
            <div className="hc-menu">
              <button
                type="button"
                className="hc-pill hc-permission"
                aria-haspopup="menu"
                aria-expanded={permissionOpen}
                onClick={() => setPermissionOpen((open) => !open)}
              >
                <Shield size={15} />
                <span>{permissionLabel || "询问权限"}</span>
                <ChevronDown size={14} />
              </button>
              {permissionOpen ? (
                <div className="hc-popover" role="menu" aria-label="选择权限模式">
                  {permissionOptions.map((option) => (
                    <button
                      type="button"
                      role="menuitemradio"
                      aria-checked={option.id === permissionMode}
                      key={option.id}
                      data-active={option.id === permissionMode}
                      onClick={() => selectPermissionMode(option.id)}
                    >
                      <strong>{option.label}</strong>
                      <small>{option.description}</small>
                    </button>
                  ))}
                  {pendingPermissionMode === "skip" ? (
                    <div className="hc-permission-confirm" role="alert">
                      <strong>确认完全访问权限？</strong>
                      <p>这会尽量跳过常规确认，适合你明确希望本地智能体自动执行读写和命令时使用。</p>
                      <div>
                        <button
                          type="button"
                          onClick={() => {
                            onPermissionModeChange?.("skip");
                            setPendingPermissionMode(null);
                            setPermissionOpen(false);
                          }}
                        >
                          确认完全访问
                        </button>
                        <button type="button" onClick={() => setPendingPermissionMode(null)}>取消</button>
                      </div>
                    </div>
                  ) : null}
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
                  <header className="hc-context-usage-head">
                    <strong>{contextDashboard.percent !== null ? `${contextDashboard.percent}%` : context}</strong>
                    <small>{contextDashboard.estimated ? "估算" : "实时"}</small>
                  </header>
                  <div className="hc-context-summary-grid" aria-label="上下文摘要">
                    <div>
                      <span>已使用</span>
                      <strong>{formatTokenCount(contextDashboard.used)}</strong>
                    </div>
                    <div>
                      <span>剩余</span>
                      <strong>{formatTokenCount(contextDashboard.remaining)}</strong>
                    </div>
                    <div>
                      <span>窗口</span>
                      <strong>{formatTokenCount(contextDashboard.max)}</strong>
                    </div>
                  </div>
                  <div className="hc-context-token-bars" aria-label="上下文 token 使用">
                    {contextDashboard.bars.map((row) => (
                      <div key={row.label}>
                        <span>
                          <strong>{row.label}</strong>
                          <em>{formatTokenCount(row.value)}</em>
                        </span>
                        <i data-tone={row.tone} style={{ width: `${Math.max(row.value ? 1 : 0, row.percent)}%` }} aria-hidden="true" />
                      </div>
                    ))}
                  </div>
                  {relativeTimeLabel(contextDashboard.updatedAt) ? <p>{relativeTimeLabel(contextDashboard.updatedAt)}</p> : null}
                  {contextGroups.length ? (
                    <div className="hc-context-section-groups" aria-label="纳入上下文">
                      {contextGroups.slice(0, 8).map((group) => (
                        <span key={group.label}>
                          {group.label}
                          <b>{group.count}</b>
                        </span>
                      ))}
                    </div>
                  ) : null}
                  {contextLayers.length ? (
                    <div className="hc-context-layers" aria-label="系统提示层">
                      <strong>系统提示层</strong>
                      {contextLayers.map((layer) => (
                        <span key={layer.name}>
                          {layer.name}
                          {layer.tokens ? <small>{formatTokenCount(layer.tokens)}</small> : null}
                        </span>
                      ))}
                    </div>
                  ) : null}
                  {contextStep ? <p>当前步骤：{contextStep}</p> : null}
                  {contextPreview?.projectFocus ? <p>项目焦点：{contextPreview.projectFocus}</p> : null}
                  {trimmedSections.length || droppedSections.length ? (
                    <p>已压缩：{[...trimmedSections, ...droppedSections].slice(0, 4).join("、")}</p>
                  ) : null}
                  <div className="hc-popover-actions">
                    <button type="button" onClick={() => copyText(contextSummary)}>
                      <Copy size={13} />
                      复制上下文
                    </button>
                  </div>
                </div>
              ) : null}
            </div>
            <div className="hc-menu">
              <button
                type="button"
                className="hc-model"
                aria-haspopup="listbox"
                aria-expanded={modelOpen}
                aria-label={`选择模型 ${selectedModel?.label ?? providerLabel}`}
                onClick={() => setModelOpen((open) => !open)}
              >
                <span>{selectedModel?.label ?? providerLabel}</span>
                <ChevronDown size={14} />
              </button>
              {modelOpen ? (
                <div className="hc-popover hc-model-popover" role="listbox" aria-label="选择模型">
                  {modelOptions.map((option) => (
                    <button
                      type="button"
                      role="option"
                      aria-selected={option.id === selectedModelId}
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
              <button type="button" className="hc-stop" data-pending={stopPending ? "true" : "false"} disabled={stopPending} onClick={onStopPrompt}>
                <Square size={12} fill="currentColor" />
                {stopPending ? "停止中" : "停止"}
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
          {isNewSession ? (
            <div className="hc-launch-bar" aria-label="会话启动环境">
              <div className="hc-menu hc-launch-menu">
                <button
                  type="button"
                  className="hc-launch-dir"
                  title={cwdLabel || "选择项目文件夹"}
                  disabled={!onWorkspacePathChange}
                  aria-haspopup="menu"
                  aria-expanded={workspaceOpen}
                  onClick={() => {
                    setWorkspaceOpen((open) => !open);
                    setBranchOpen(false);
                    setWorktreeOpen(false);
                    setPermissionOpen(false);
                    setContextOpen(false);
                    setModelOpen(false);
                  }}
                >
                  <Folder size={16} />
                  <span>{cwdName || "选择项目..."}</span>
                  <ChevronDown size={15} />
                </button>
                {workspaceOpen ? (
                  <div className="hc-popover hc-workspace-popover" role="menu" aria-label="选择项目文件夹">
                    <header>
                      <strong>最近</strong>
                    </header>
                    {recentWorkspaces.length ? (
                      <div className="hc-workspace-recent-list">
                        {recentWorkspaces.map((workspace) => {
                          const path = workspace.path;
                          const selected = path === cwdLabel;
                          const label = workspace.name || workspace.repoName || basename(path);
                          const detail = [
                            workspace.branch,
                            workspace.sessionCount ? `${workspace.sessionCount} 个会话` : "",
                            relativeTimeLabel(workspace.updatedAt),
                          ].filter(Boolean).join(" · ");
                          return (
                            <button key={path} type="button" role="menuitem" title={path} data-active={selected ? "true" : undefined} onClick={() => selectRecentWorkspace(path)}>
                              <Folder size={16} />
                              <span>
                                <strong>{label}</strong>
                                <small>{detail || path}</small>
                              </span>
                              {selected ? <Check size={15} /> : null}
                            </button>
                          );
                        })}
                      </div>
                    ) : (
                      <p>暂无最近项目</p>
                    )}
                    <div className="hc-popover-actions">
                      <button type="button" disabled={!onWorkspacePathChange} onClick={chooseWorkspaceFolder}>
                        <Folder size={15} />
                        选择其他文件夹
                      </button>
                    </div>
                  </div>
                ) : null}
              </div>
              <div className="hc-menu hc-launch-branch-menu">
                <button
                  type="button"
                  className="hc-launch-chip hc-launch-button"
                  title={gitError || branchLabel}
                  aria-haspopup="menu"
                  aria-expanded={branchOpen}
                  disabled={!cwdLabel || gitLoading}
                  onClick={() => {
                    setBranchOpen((open) => !open);
                    setWorkspaceOpen(false);
                    setWorktreeOpen(false);
                    setPermissionOpen(false);
                    setContextOpen(false);
                    setModelOpen(false);
                  }}
                >
                  <GitBranch size={14} />
                  <span>{gitLoading ? "检测中" : branchLabel}</span>
                  <ChevronDown size={14} />
                </button>
                {branchOpen ? (
                  <div className="hc-popover hc-branch-popover" role="menu" aria-label="选择分支">
                    <label className="hc-branch-search">
                      <Search size={14} />
                      <input
                        value={branchFilter}
                        placeholder="筛选分支..."
                        autoFocus
                        onChange={(event) => setBranchFilter(event.currentTarget.value)}
                      />
                    </label>
                    {gitError ? <p className="hc-launch-warning">{gitError}</p> : null}
                    {selectedBranchWarnsOnCurrentWorktree ? (
                      <p className="hc-launch-warning">
                        {selectedBranch?.checkedOut
                          ? "选中分支已在其他工作树中检出；使用独立工作树可避免改动当前目录。"
                          : `当前工作树有 ${dirtyFiles} 个文件改动，直接切换可能会被 Git 阻止。`}
                      </p>
                    ) : null}
                    {branchOptions.length ? (
                      <div className="hc-branch-list">
                        {branchOptions.map((branch) => (
                          <button
                            key={branch.name}
                            type="button"
                            role="menuitemradio"
                            aria-checked={branch.name === selectedBranchOptionName}
                            data-active={branch.name === selectedBranchOptionName ? "true" : undefined}
                            onClick={() => selectBranch(branch.name)}
                          >
                            <GitBranch size={14} />
                            <span>
                              <strong>{branch.name}</strong>
                              <small>{branchMetaLabel(branch)}</small>
                            </span>
                            {branch.name === selectedBranchOptionName ? <Check size={14} /> : null}
                          </button>
                        ))}
                      </div>
                    ) : (
                      <p className="hc-launch-empty">{gitLoading ? "正在读取分支..." : "没有可选分支"}</p>
                    )}
                    <div className="hc-popover-actions">
                      <button type="button" disabled={gitLoading || !cwdLabel} onClick={() => void refreshLocalGitStatus()}>
                        刷新状态
                      </button>
                    </div>
                  </div>
                ) : null}
              </div>
              <div className="hc-menu hc-launch-worktree-menu">
                <button
                  type="button"
                  className="hc-launch-chip hc-launch-button"
                  aria-haspopup="menu"
                  aria-expanded={worktreeOpen}
                  disabled={worktreeModeBusy}
                  onClick={() => {
                    setWorktreeOpen((open) => !open);
                    setWorkspaceOpen(false);
                    setBranchOpen(false);
                    setPermissionOpen(false);
                    setContextOpen(false);
                    setModelOpen(false);
                  }}
                >
                  <span>{useWorktree ? "独立工作树" : "当前工作树"}</span>
                  <small>{worktreeModeBusy ? "保存中" : cleanWorktree ? "干净" : `${dirtyFiles} 个改动`}</small>
                  <ChevronDown size={14} />
                </button>
                {worktreeOpen ? (
                  <div className="hc-popover hc-worktree-popover" role="menu" aria-label="工作树模式">
                    <button
                      type="button"
                      role="menuitemradio"
                      aria-checked={!useWorktree}
                      data-active={!useWorktree ? "true" : undefined}
                      disabled={worktreeModeBusy}
                      onClick={() => selectWorktreeMode(false)}
                    >
                      <strong>当前工作树</strong>
                      <small>直接使用所选项目目录。</small>
                      {!useWorktree ? <Check size={14} /> : null}
                    </button>
                    <button
                      type="button"
                      role="menuitemradio"
                      aria-checked={useWorktree}
                      data-active={useWorktree ? "true" : undefined}
                      disabled={worktreeModeBusy}
                      onClick={() => selectWorktreeMode(true)}
                    >
                      <strong>独立工作树</strong>
                      <small>写入任务会自动创建隔离工作树。</small>
                      {useWorktree ? <Check size={14} /> : null}
                    </button>
                    <p>{syncLabel}</p>
                  </div>
                ) : null}
              </div>
            </div>
          ) : (
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
                  <dl className="hc-project-status">
                    <div>
                      <dt>分支</dt>
                      <dd>{branchLabel}</dd>
                    </div>
                    <div>
                      <dt>上游</dt>
                      <dd>{upstreamLabel}</dd>
                    </div>
                    <div>
                      <dt>同步</dt>
                      <dd>{syncLabel}</dd>
                    </div>
                    <div>
                      <dt>改动</dt>
                      <dd>{worktreeStatus?.error || (dirtyFiles ? `${dirtyFiles} 个文件` : "工作区干净")}</dd>
                    </div>
                    <div>
                      <dt>最近文件</dt>
                      <dd>{worktreeFiles.slice(0, 3).join("、") || "暂无"}</dd>
                    </div>
                  </dl>
                  <div className="hc-popover-actions">
                    <button
                      type="button"
                      disabled={!cwdLabel}
                      onClick={() => copyText(cwdLabel)}
                    >
                      <Copy size={13} />
                      复制路径
                    </button>
                    <button
                      type="button"
                      disabled={!worktreeFiles.length}
                      onClick={() => copyText(worktreeFileCopyText)}
                    >
                      <Copy size={13} />
                      复制改动文件
                    </button>
                  </div>
                </div>
              ) : null}
            </div>
          )}
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
          {runtimeChildTasks?.length ? <span>{runtimeChildTasks.length} 个子任务</span> : null}
        </div>
      </div>
    </form>
  );
}
