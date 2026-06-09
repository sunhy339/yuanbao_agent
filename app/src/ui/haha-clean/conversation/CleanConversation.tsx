import { memo, useMemo, useState } from "react";
import {
  BookMarked,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Circle,
  CircleAlert,
  Copy,
  CornerDownRight,
  GitBranch,
  FileDiff,
  Files,
  HelpCircle,
  History,
  ListChecks,
  MoreHorizontal,
  MousePointerClick,
  RotateCcw,
  ScanSearch,
  SendHorizontal,
  TerminalSquare,
  Target,
  Trash2,
  Wrench,
} from "lucide-react";
import type {
  ConversationActivityItem,
  RuntimeTimelineItem,
  SessionWorkspaceMessage,
} from "../../workbench/workspaces/session/types";
import {
  buildCommandOutput,
  parseUnifiedDiff,
  parsePatchFileSummaries,
} from "../../workbench/workspaces/session/utils";
import { sanitizeAssistantStatusContent, stripAssistantRuntimeProgress } from "../../../state/chatMessages";
import { attachmentsFromMetadata, CleanAttachmentGallery, imageAttachmentsFromText, uniqueAttachments } from "../shared/CleanAttachmentGallery";
import { CleanInlineMarkdown, CleanMarkdown } from "../shared/CleanMarkdown";
import {
  compactText,
  formatClock,
  formatDuration,
  isInFlight,
  runtimeLabel,
  runtimeSummary,
  statusLabel,
  statusTone,
  toolActionTitle,
} from "../shared/text";
import { transcriptKindForActivity, type CleanTranscriptKind } from "./transcriptModel";

type PatchFileSummary = {
  path: string;
  status?: string;
  additions?: number;
  deletions?: number;
};

const ALWAYS_ALLOW_APPROVAL_KINDS = new Set([
  "apply_patch",
  "write_file",
  "delete_file",
  "run_command",
  "network_access",
  "computer_use",
  "subagent_dispatch",
  "worktree_merge",
]);

function supportsAlwaysAllowKind(kind?: string | null) {
  return Boolean(kind && ALWAYS_ALLOW_APPROVAL_KINDS.has(kind));
}

function messageKind(message: SessionWorkspaceMessage) {
  const metaKind = message.metadata?.kind;
  if (typeof metaKind === "string") return metaKind;
  return message.kind ?? "";
}

function isDocumentMessage(content: string) {
  const text = content.trim();
  return (
    /```/.test(text) ||
    /^\s{0,3}(#{1,6}\s|[-*+]\s|\d+\.\s|>\s|\|.+\|)/m.test(text) ||
    text.split(/\n\s*\n/).filter(Boolean).length >= 2 ||
    text.split("\n").filter((line) => line.trim()).length >= 8
  );
}

function parseJson(value: string) {
  try {
    const parsed = JSON.parse(value);
    return parsed && typeof parsed === "object" ? parsed as Record<string, unknown> : null;
  } catch {
    return null;
  }
}

function readString(value: unknown) {
  if (typeof value === "string") return value.trim();
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return "";
}

function readRecordString(record: Record<string, unknown> | null | undefined, keys: string[]) {
  if (!record) return "";
  for (const key of keys) {
    const text = readString(record[key]);
    if (text) return text;
  }
  return "";
}

function readRecordNumber(record: Record<string, unknown> | null | undefined, keys: string[]) {
  if (!record) return null;
  for (const key of keys) {
    const value = record[key];
    if (typeof value === "number" && Number.isFinite(value)) return value;
    if (typeof value === "string" && value.trim() && Number.isFinite(Number(value))) return Number(value);
  }
  return null;
}

function readToolInputRecord(message: SessionWorkspaceMessage) {
  return message.metadata?.input && typeof message.metadata.input === "object"
    ? message.metadata.input as Record<string, unknown>
    : parseJson(typeof message.metadata?.inputText === "string" ? message.metadata.inputText : "");
}

function normalizedToolName(value?: string | null) {
  return String(value ?? "").trim().toLowerCase().replace(/\s+/g, "_");
}

const INTERNAL_APPROVAL_TOOL_NAMES = new Set(["completion_review", "advisor_tool"]);

function isInternalApprovalToolName(value?: string | null) {
  return INTERNAL_APPROVAL_TOOL_NAMES.has(normalizedToolName(value));
}

function looksLikeInternalPayloadText(value: string) {
  const text = value.trim();
  if (!text) return true;
  const lines = text.split(/\r?\n/).filter((line) => line.trim());
  if (
    lines.length >= 3 &&
    /(^|\n)\s*(_chatCompat|activeStep|currentStep|completedSteps|fingerprint|tool_results|workspaceRoot|sessionId|taskId|eventId|payload|metadata)\s*[:=]/.test(text)
  ) {
    return true;
  }
  if (/^[{\[]/.test(text)) {
    try {
      const parsed = JSON.parse(text);
      if (parsed && typeof parsed === "object") {
        const keys = Object.keys(parsed as Record<string, unknown>);
        return keys.some((key) => [
          "_chatCompat",
          "context",
          "currentTaskId",
          "eventId",
          "frames",
          "messages",
          "metadata",
          "options",
          "payload",
          "progress",
          "provider",
          "providerRequest",
          "providerResponse",
          "questions",
          "rawJson",
          "requestId",
          "sessionId",
          "taskId",
          "taskStatus",
          "toolCallId",
          "toolProgress",
          "tool_progress",
          "tool_results",
          "trace",
          "uiReplayScope",
          "visibility",
          "workspaceRoot",
          "yuanbao",
        ].includes(key));
      }
    } catch {
      return true;
    }
  }
  return false;
}

function cleanInlineDisplayText(value: string) {
  const text = value.trim();
  if (!text || looksLikeInternalPayloadText(text)) return "";
  return sanitizeAssistantStatusContent(text, text);
}

function toolQuestionText(message: SessionWorkspaceMessage) {
  const input = readToolInputRecord(message);
  const questionRecord =
    Array.isArray(input?.questions) && input.questions[0] && typeof input.questions[0] === "object"
      ? input.questions[0] as Record<string, unknown>
      : null;
  return (
    readMetadataString(message, ["question", "prompt", "summary", "message", "description"]) ||
    readRecordString(input, ["question", "prompt", "summary", "message", "description"]) ||
    readRecordString(questionRecord, ["question", "prompt", "summary", "message", "description"]) ||
    ""
  );
}

function isAskUserToolMessage(message: SessionWorkspaceMessage) {
  return normalizedToolName(message.toolName || readMetadataString(message, ["toolName", "name"])) === "ask_user_question";
}

function cleanSpecialEventMetadataValue(key: string, value: unknown) {
  if (SPECIAL_EVENT_METADATA_BLOCKLIST.has(key)) return "";
  if (typeof value === "string") return cleanInlineDisplayText(value);
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return "";
}

function summarizeNamedArray(record: Record<string, unknown>, keys: string[], noun: string) {
  for (const key of keys) {
    const value = record[key];
    if (!Array.isArray(value)) continue;
    const names = value
      .map((item) => {
        if (typeof item === "string") return item.trim();
        if (!item || typeof item !== "object") return "";
        const entry = item as Record<string, unknown>;
        return readRecordString(entry, ["path", "file", "name", "title", "label"]);
      })
      .filter(Boolean)
      .slice(0, 3);
    const suffix = names.length
      ? `：${names.join("、")}${value.length > names.length ? `，另 ${value.length - names.length} 项` : ""}`
      : "";
    return `${noun} ${value.length} 项${suffix}`;
  }
  return "";
}

function summarizeToolResultText(value: string) {
  const text = value.trim();
  if (!text) return "";
  const record = parseJson(text);
  if (!record) return compactText(text, 150);

  const listSummary =
    summarizeNamedArray(record, ["items", "entries", "children"], "找到") ||
    summarizeNamedArray(record, ["files", "changedFiles", "changes"], "涉及文件") ||
    summarizeNamedArray(record, ["matches", "results"], "返回结果");
  if (listSummary) return compactText(listSummary, 150);

  const status = readRecordString(record, ["status", "state"]);
  const exitCode = readRecordNumber(record, ["exitCode", "exit_code", "code"]);
  const stdout = readRecordString(record, ["stdout", "output"]);
  const stderr = readRecordString(record, ["stderr", "error"]);
  const message = readRecordString(record, ["summary", "message", "result"]);
  const parts: string[] = [];
  if (status) parts.push(statusLabel(status));
  if (exitCode !== null) parts.push(`退出码 ${exitCode}`);
  if (message) parts.push(compactText(message, 92));
  if (!message && stdout) parts.push(compactText(stdout, 92));
  if (stderr) parts.push(compactText(stderr, 92));
  return parts.length ? parts.join(" · ") : "结果已记录";
}

function structuredToolDetailText(value: string, fallbackLabel: string) {
  const text = value.trim();
  if (!text) return "";
  const record = parseJson(text);
  if (!record) return looksLikeInternalPayloadText(text) ? "" : text;
  const rows = [
    "summary",
    "message",
    "status",
    "state",
    "error",
    "reason",
    "path",
    "file",
    "target",
    "command",
    "exitCode",
    "durationMs",
  ].flatMap((key) => {
    const value = record[key];
    const clean = typeof value === "string" || typeof value === "number" || typeof value === "boolean"
      ? cleanInlineDisplayText(String(value))
      : "";
    return clean ? [`${key}: ${clean}`] : [];
  });
  const previewRows = metadataPreviewRows(record.resultPreview)
    .concat(metadataPreviewRows(record.preview))
    .map((row) => `${row.label}: ${row.value}`);
  const lines = [...rows, ...previewRows];
  if (!lines.length) {
    const summary = summarizeToolResultText(text);
    return summary ? `${fallbackLabel}\n${summary}` : "";
  }
  return `${fallbackLabel}\n${lines.join("\n")}`;
}

function toolInlineSummary(message: SessionWorkspaceMessage) {
  if (isAskUserToolMessage(message)) {
    return compactText(toolQuestionText(message) || "等待你补充信息", 170);
  }
  const displaySummary = cleanInlineDisplayText(readMetadataString(message, ["displaySummary", "resultSummary"]));
  const previewSummary = metadataPreviewRows(message.metadata?.resultPreview)
    .map((row) => `${row.label}: ${row.value}`)
    .join(" 路 ");
  if (displaySummary || previewSummary) return compactText(displaySummary || previewSummary, 170);
  const structuredResult = cleanInlineDisplayText(readString(message.metadata?.resultSummary));
  const result = summarizeToolResultText(readString(message.metadata?.resultText));
  if (structuredResult || result) return compactText(structuredResult || result, 170);
  if (message.streaming || isInFlight(message.status)) return "等待工具返回结果";
  const safeContent = cleanInlineDisplayText(message.content);
  const inputText = cleanInlineDisplayText(readString(message.metadata?.inputText));
  if (!safeContent || safeContent === inputText || looksLikeInternalPayloadText(safeContent)) return "";
  return compactText(safeContent, 170);
}

function toolInlineTarget(message: SessionWorkspaceMessage) {
  if (isAskUserToolMessage(message)) return "";
  const input = readToolInputRecord(message);
  return compactText(cleanInlineDisplayText(readString(input?.path ?? input?.file ?? input?.cwd ?? input?.command ?? input?.query)), 92);
}

function toolGroupLabel(item: RuntimeTimelineItem) {
  const name = normalizeRuntimeToolName(item);
  const category = typeof item.toolCategory === "string" ? item.toolCategory.trim().toLowerCase() : "";
  if (category === "context_read" || name === "read_file" || name === "list_dir" || name === "list_directory") return "读取";
  if (category === "search" || name === "search_files" || name === "code_search") return "搜索";
  if (category === "git" || name === "git_status" || name === "git_diff") return "Git";
  if (category === "verification") return "验证";
  if (item.kind === "command" || ["run_command", "command", "bash", "shell", "shell_command", "powershell"].includes(name)) return "命令";
  if (name === "apply_patch" || name === "write_file" || name === "edit_file" || item.kind === "patch") return "文件";
  if (name === "agent" || name === "task") return "Agent";
  return "工具";
}

function toolGroupSummary(items: RuntimeTimelineItem[]) {
  const counts = new Map<string, number>();
  for (const item of items) {
    const label = toolGroupLabel(item);
    counts.set(label, (counts.get(label) ?? 0) + 1);
  }
  return [...counts.entries()]
    .sort(([left], [right]) => compareWorklogGroupLabels(left, right))
    .map(([label, count]) => `${label} ${count}`)
    .join("、");
}

function runtimeItemToolTarget(item: RuntimeTimelineItem, toolName: string) {
  const inputRecord = parseJson(item.code || "");
  const targetFromInput = readRecordString(inputRecord, ["path", "file", "cwd", "root", "target", "query", "url", "command", "cmd"]);
  if (targetFromInput) return targetFromInput;
  const title = item.title?.trim() ?? "";
  const normalized = normalizedToolName(toolName);
  const verbPattern =
    normalized === "read_file" ? /^(?:read|读取)\s+/i :
    normalized === "search_files" || normalized === "code_search" ? /^(?:search|搜索)\s+/i :
    normalized === "list_dir" || normalized === "list_directory" ? /^(?:list|view|查看|列出)\s+/i :
    normalized === "write_file" ? /^(?:write|写入)\s+/i :
    normalized === "run_command" ? /^(?:run|运行)\s+/i :
    null;
  if (verbPattern) {
    const stripped = title.replace(verbPattern, "").trim();
    if (stripped && stripped !== title) return stripped;
  }
  return item.meta?.[0] || "";
}

function runtimeItemToToolMessage(item: RuntimeTimelineItem): SessionWorkspaceMessage | null {
  if (!["tool", "command", "patch"].includes(item.kind)) {
    return null;
  }
  const toolName =
    item.toolName ||
    (item.kind === "command" ? "run_command" : item.kind === "patch" ? "apply_patch" : normalizeRuntimeToolName(item));
  const normalizedStatus = item.status?.toLowerCase() ?? "";
  const needsDiagnostics = ["failed", "error", "blocked", "cancelled", "rejected"].includes(normalizedStatus);
  const safeInputText = needsDiagnostics && item.code && !looksLikeInternalPayloadText(item.code) ? item.code : "";
  const safeResultText = needsDiagnostics && item.rawDetail && !looksLikeInternalPayloadText(item.rawDetail) ? item.rawDetail : "";
  const target = runtimeItemToolTarget(item, toolName);
  const content = item.summary || "";
  return {
    id: `tool_activity:${item.toolUseId || item.sourceId || item.id}`,
    role: "assistant",
    content,
    taskId: item.taskId,
    toolName,
    status: item.status,
    createdAt: item.time,
    updatedAt: item.time,
    metadata: {
      kind: "tool_activity",
      toolUseId: item.toolUseId || item.sourceId || item.id,
      parentToolUseId: item.parentToolUseId,
      toolGroupId: item.toolGroupId,
      toolIndex: item.toolIndex,
      toolTotal: item.toolTotal,
      toolOperationId: item.toolOperationId,
      toolOperationLabel: item.toolOperationLabel,
      toolCategory: item.toolCategory,
      toolPhaseId: item.toolPhaseId,
      toolPhaseLabel: item.toolPhaseLabel,
      toolSemanticParentId: item.toolSemanticParentId,
      toolSemanticParentLabel: item.toolSemanticParentLabel,
      inputText: safeInputText,
      resultText: safeResultText || item.summary,
      resultPreview: item.previewRows,
      durationMs: item.durationMs,
      target,
      inputSummary: item.title,
    },
  };
}

function metadataPreviewRows(value: unknown): Array<{ label: string; value: string }> {
  if (!Array.isArray(value)) return [];
  return value
    .map((row) => {
      if (!row || typeof row !== "object") return null;
      const record = row as Record<string, unknown>;
      const label = typeof record.label === "string" ? record.label.trim() : "";
      const rowValue = typeof record.value === "string" ? record.value.trim() : "";
      return label && rowValue ? { label, value: rowValue } : null;
    })
    .filter((row): row is { label: string; value: string } => row !== null)
    .slice(0, 5);
}

function normalizeRuntimeToolName(item: RuntimeTimelineItem) {
  const name = (item.toolName || item.title || "").toLowerCase().replace(/\s+/g, "_");
  if (item.kind === "command") return "run_command";
  if (item.kind === "patch") return "apply_patch";
  return name;
}

const CONTEXT_SEARCH_COMMAND_RE =
  /^(rg|grep|ag|ack|findstr|select-string|find|where(?:\.exe)?|which|whereis|locate)\b/i;
const CONTEXT_READ_COMMAND_RE =
  /^(cat|head|tail|less|more|wc|stat|file|strings|jq|awk|cut|sort|uniq|tr|get-content|gc|get-item|test-path|resolve-path|get-filehash|get-acl|format-hex|pwd|get-location)\b/i;
const CONTEXT_LIST_COMMAND_RE =
  /^(ls|dir|tree|du|get-childitem|gci)\b/i;
const CONTEXT_GIT_COMMAND_RE =
  /^git\s+(status|diff|log|show|branch|remote|tag|rev-parse|rev-list|ls-files|grep|blame)\b/i;

function normalizedRuntimeCommand(item: RuntimeTimelineItem) {
  const candidates = [item.code, item.rawDetail, item.title]
    .map((value) => value?.trim())
    .filter((value): value is string => Boolean(value));
  for (const candidate of candidates) {
    const parsed = parseJson(candidate);
    const structured = readRecordString(parsed, ["command", "cmd"]);
    if (structured) return structured;
    if (
      CONTEXT_GIT_COMMAND_RE.test(candidate) ||
      CONTEXT_LIST_COMMAND_RE.test(candidate) ||
      CONTEXT_SEARCH_COMMAND_RE.test(candidate) ||
      CONTEXT_READ_COMMAND_RE.test(candidate)
    ) {
      return candidate;
    }
  }
  return candidates[0] ?? "";
}

function isShellRuntime(item: RuntimeTimelineItem) {
  const name = normalizeRuntimeToolName(item);
  return item.kind === "command" || ["run_command", "command", "bash", "shell_command", "powershell"].includes(name);
}

function contextCommandKind(item: RuntimeTimelineItem) {
  if (!isShellRuntime(item)) return "";
  const command = normalizedRuntimeCommand(item);
  if (!command) return "";
  if (CONTEXT_GIT_COMMAND_RE.test(command)) return "git";
  if (CONTEXT_LIST_COMMAND_RE.test(command)) return "list";
  if (CONTEXT_SEARCH_COMMAND_RE.test(command)) return "search";
  if (CONTEXT_READ_COMMAND_RE.test(command)) return "read";
  return "";
}

function isQuietRuntime(item: RuntimeTimelineItem) {
  const name = normalizeRuntimeToolName(item);
  const status = item.status?.toLowerCase() ?? "";
  if (isInFlight(status) || ["failed", "error", "cancelled", "rejected"].includes(status)) {
    return false;
  }
  return (
    ["read_file", "list_dir", "list_directory", "git_status", "git_diff", "search_files", "code_search"].includes(name) ||
    Boolean(contextCommandKind(item))
  );
}

function runtimeKindName(item: RuntimeTimelineItem) {
  if (item.kind === "command") return "command";
  if (item.kind === "patch") return "patch";
  if (item.kind === "approval") return "approval";
  return normalizeRuntimeToolName(item).replace(/_/g, "-") || item.kind;
}

const TOOL_CATEGORY_LABELS: Record<string, string> = {
  command: "命令",
  computer_use: "桌面操作",
  context_read: "读取上下文",
  file_change: "文件改动",
  git: "Git 检查",
  memory: "记忆",
  search: "搜索",
  subtask: "子任务",
  tool: "工具",
  verification: "验证",
  web: "网页读取",
};

const WORKLOG_GROUP_ORDER = [
  "审批",
  "文件改动",
  "验证",
  "命令",
  "Git 检查",
  "搜索",
  "读取上下文",
  "子任务",
  "记忆",
  "网页读取",
  "工具",
];

const VERIFICATION_COMMAND_RE =
  /\b(npm\s+(?:run\s+)?(?:test|typecheck|lint|build)|pnpm\s+(?:run\s+)?(?:test|typecheck|lint|build)|yarn\s+(?:test|typecheck|lint|build)|pytest|vitest|jest|playwright|tsc|ruff|eslint|mypy|cargo\s+(?:test|check|build)|go\s+test|dotnet\s+test)\b|\b(test|typecheck|lint|build|verify|check)\b/i;

function runtimeCommandText(item: RuntimeTimelineItem) {
  return [item.title, item.code, item.rawDetail].filter(Boolean).join("\n");
}

function isVerificationRuntime(item: RuntimeTimelineItem) {
  const name = normalizeRuntimeToolName(item);
  if (!isShellRuntime(item)) {
    return false;
  }
  return VERIFICATION_COMMAND_RE.test(runtimeCommandText(item));
}

function runtimeGroupLabel(item: RuntimeTimelineItem) {
  const operationLabel = typeof item.toolOperationLabel === "string" ? item.toolOperationLabel.trim() : "";
  if (operationLabel) {
    return operationLabel;
  }
  const semanticParentLabel = typeof item.toolSemanticParentLabel === "string" ? item.toolSemanticParentLabel.trim() : "";
  if (semanticParentLabel) {
    return semanticParentLabel;
  }
  const phaseLabel = typeof item.toolPhaseLabel === "string" ? item.toolPhaseLabel.trim() : "";
  if (phaseLabel) {
    return phaseLabel;
  }
  const category = typeof item.toolCategory === "string" ? item.toolCategory.trim() : "";
  if (category && TOOL_CATEGORY_LABELS[category]) {
    return TOOL_CATEGORY_LABELS[category];
  }
  const name = normalizeRuntimeToolName(item);
  if (isVerificationRuntime(item)) return "验证";
  const contextKind = contextCommandKind(item);
  if (contextKind === "git") return "Git 检查";
  if (contextKind === "search") return "搜索";
  if (contextKind === "read" || contextKind === "list") return "读取上下文";
  if (isShellRuntime(item)) return "命令";
  if (item.kind === "patch" || name === "apply_patch" || name === "write_file") return "文件改动";
  if (item.kind === "approval") return "审批";
  if (["read_file", "list_dir", "list_directory"].includes(name)) return "读取上下文";
  if (["git_status", "git_diff"].includes(name)) return "Git 检查";
  if (["search_files", "code_search"].includes(name)) return "搜索";
  if (item.kind === "task") return "子任务";
  if (item.kind === "memory") return "记忆";
  return "工具";
}

function compareWorklogGroupLabels(left: string, right: string) {
  const leftIndex = WORKLOG_GROUP_ORDER.indexOf(left);
  const rightIndex = WORKLOG_GROUP_ORDER.indexOf(right);
  const normalizedLeft = leftIndex === -1 ? Number.MAX_SAFE_INTEGER : leftIndex;
  const normalizedRight = rightIndex === -1 ? Number.MAX_SAFE_INTEGER : rightIndex;
  if (normalizedLeft !== normalizedRight) {
    return normalizedLeft - normalizedRight;
  }
  return left.localeCompare(right);
}

function worklogGroupEntries(items: RuntimeTimelineItem[]) {
  const groups = new Map<string, { label: string; count: number; tone: CleanTranscriptKind | "normal" }>();
  items.forEach((item) => {
    const label = runtimeGroupLabel(item);
    const existing = groups.get(label);
    const tone = item.kind === "approval" ? "permission_request" :
      item.kind === "patch" ? "change_set" :
      statusTone(item.status) === "danger" ? "error" :
      "normal";
    if (existing) {
      existing.count += 1;
      if (tone !== "normal") existing.tone = tone;
      return;
    }
    groups.set(label, { label, count: 1, tone });
  });
  return Array.from(groups.values()).sort((left, right) => compareWorklogGroupLabels(left.label, right.label));
}

function worklogDigest(items: RuntimeTimelineItem[], quietCount: number) {
  const labels = worklogGroupEntries(items)
    .slice(0, 4)
    .map((group) => `${group.label} ${group.count}`);
  const suffix = quietCount && quietCount === items.length ? "，均已收起为轻量日志" : quietCount ? `，${quietCount} 项低噪声` : "";
  return `${labels.join("、")}${suffix}`;
}

function worklogNarrative(items: RuntimeTimelineItem[]) {
  const groups = new Set(items.map(runtimeGroupLabel));
  if (groups.has("文件改动")) {
    return "我在处理文件改动，并把相关读写、检查和验证记录合并到下面。";
  }
  if (groups.has("审批")) {
    return "这里需要你确认权限或改动请求，相关上下文已收在下面。";
  }
  if (groups.has("验证")) {
    return "我在验证当前结果，并把测试、构建或检查输出收在下面。";
  }
  if (groups.has("命令")) {
    return "我在运行命令并记录结果，必要时可以展开查看完整输出。";
  }
  if (groups.has("Git 检查")) {
    return "我在核对 Git 状态和差异，结果已压缩成可展开的轻量日志。";
  }
  if (groups.has("搜索")) {
    return "我在搜索相关文件或内容，命中结果已合并到这组日志里。";
  }
  if (groups.has("读取上下文")) {
    return "我在读取项目上下文，低价值的读文件和目录检查已折叠收纳。";
  }
  return "我把这组工具和运行结果整理在下面，展开可以查看细节。";
}

type WorklogTreeNode = {
  item: RuntimeTimelineItem;
  children: WorklogTreeNode[];
  depth: number;
};

function runtimeTreeId(item: RuntimeTimelineItem) {
  return item.toolUseId || item.sourceId || item.id.replace(/^(tool|command|runtime):/, "");
}

function compareWorklogTreeNodes(left: WorklogTreeNode, right: WorklogTreeNode) {
  const leftItem = left.item;
  const rightItem = right.item;
  if (leftItem.toolGroupId && rightItem.toolGroupId && leftItem.toolGroupId === rightItem.toolGroupId) {
    const leftIndex = leftItem.toolIndex ?? Number.MAX_SAFE_INTEGER;
    const rightIndex = rightItem.toolIndex ?? Number.MAX_SAFE_INTEGER;
    if (leftIndex !== rightIndex) {
      return leftIndex - rightIndex;
    }
  }
  const leftTime = leftItem.time ?? Number.MAX_SAFE_INTEGER;
  const rightTime = rightItem.time ?? Number.MAX_SAFE_INTEGER;
  if (leftTime !== rightTime) {
    return leftTime - rightTime;
  }
  return leftItem.id.localeCompare(rightItem.id);
}

function buildWorklogTree(items: RuntimeTimelineItem[]): WorklogTreeNode[] {
  const byId = new Map<string, WorklogTreeNode>();
  const roots: WorklogTreeNode[] = [];

  items.forEach((item) => {
    byId.set(runtimeTreeId(item), { item, children: [], depth: 0 });
  });

  items.forEach((item) => {
    const node = byId.get(runtimeTreeId(item));
    if (!node) return;
    const parentId = item.parentToolUseId;
    const parent = parentId ? byId.get(parentId) : undefined;
    if (!parent || parent === node) {
      roots.push(node);
      return;
    }
    parent.children.push(node);
  });

  const assignDepth = (nodes: WorklogTreeNode[], depth: number): WorklogTreeNode[] =>
    [...nodes].sort(compareWorklogTreeNodes).map((node) => ({
      ...node,
      depth,
      children: assignDepth(node.children, Math.min(depth + 1, 4)),
    }));

  return assignDepth(roots, 0);
}

function flattenWorklogTree(nodes: WorklogTreeNode[]): WorklogTreeNode[] {
  const flat: WorklogTreeNode[] = [];
  const visit = (node: WorklogTreeNode) => {
    flat.push(node);
    node.children.forEach(visit);
  };
  nodes.forEach(visit);
  return flat;
}

type WorklogPhase = {
  id: string;
  label: string;
  nodes: WorklogTreeNode[];
};

function runtimeGroupKey(item: RuntimeTimelineItem) {
  const operationId = typeof item.toolOperationId === "string" ? item.toolOperationId.trim() : "";
  if (operationId) {
    return operationId;
  }
  const semanticParentId = typeof item.toolSemanticParentId === "string" ? item.toolSemanticParentId.trim() : "";
  return semanticParentId || runtimeGroupLabel(item);
}

function worklogPhaseTone(nodes: WorklogTreeNode[]) {
  if (nodes.some((node) => statusTone(node.item.status) === "danger")) return "danger";
  if (nodes.some((node) => isInFlight(node.item.status))) return "running";
  if (nodes.some((node) => statusTone(node.item.status) === "warning")) return "warning";
  if (nodes.length && nodes.every((node) => statusTone(node.item.status) === "success")) return "success";
  return "neutral";
}

function worklogPhaseEntries(nodes: WorklogTreeNode[]) {
  const phases = new Map<string, { label: string; nodes: WorklogTreeNode[] }>();
  const addNode = (node: WorklogTreeNode, phaseItem: RuntimeTimelineItem) => {
    const key = runtimeGroupKey(phaseItem);
    const label = runtimeGroupLabel(phaseItem);
    const phase = phases.get(key);
    if (phase) {
      phase.nodes.push(node);
    } else {
      phases.set(key, { label, nodes: [node] });
    }
  };
  nodes.forEach((node) => addNode(node, node.item));
  return Array.from(phases.entries())
    .map(([id, phase]): WorklogPhase => ({ id, label: phase.label, nodes: phase.nodes }))
    .sort((left, right) => compareWorklogGroupLabels(left.label, right.label) || left.id.localeCompare(right.id));
}

function worklogSummaryText(items: RuntimeTimelineItem[]) {
  const lines = items.map((item, index) => {
    const parts = [
      `#${index + 1}`,
      runtimeLabel(item),
      item.status ? statusLabel(item.status) : "",
      runtimeSummary(item),
      formatDuration(item.durationMs),
    ].filter(Boolean);
    return parts.join(" · ");
  });
  return [`已执行 ${items.length} 项`, ...lines].join("\n");
}

function worklogGroupCounts(items: RuntimeTimelineItem[]) {
  return worklogGroupEntries(items);
}

function splitDiffText(value: string) {
  const files: Array<{ oldPath: string; newPath: string; lines: ReturnType<typeof parseUnifiedDiff> }> = [];
  const normalizedValue = value.replace(/\r\n/g, "\n").trimStart();
  const sections = normalizedValue.split(/\ndiff --git /g);
  sections.forEach((section, index) => {
    const text = index === 0 || section.startsWith("diff --git ") ? section : `diff --git ${section}`;
    const oldPath = /^---\s+(.*)$/m.exec(text)?.[1]?.replace(/^a\//, "") ?? "";
    const newPath = /^\+\+\+\s+(.*)$/m.exec(text)?.[1]?.replace(/^b\//, "") ?? oldPath;
    const lines = parseUnifiedDiff(text);
    if (lines.length) {
      files.push({ oldPath, newPath, lines });
    }
  });
  return files;
}

function normalizeDiffPath(path: string) {
  return path.replace(/\\/g, "/").replace(/^[ab]\//, "").trim();
}

function normalizePatchSummaryLine(line: string): PatchFileSummary | null {
  const trimmed = line.trim();
  if (
    !trimmed ||
    /^diff --git\b/.test(trimmed) ||
    /^@@/.test(trimmed) ||
    /^[+-]{3}\s+/.test(trimmed) ||
    /^[+-]\s/.test(trimmed)
  ) {
    return null;
  }
  const match = /^(?:(added|modified|deleted|changed|updated?|created?)\s+)?(.+?)(?:\s+\((?:\+(\d+))?(?:\/?-(\d+))?\))?$/i.exec(trimmed);
  if (!match) return null;
  let path = match[2]?.trim() ?? "";
  path = path.replace(/^["']|["']$/g, "").replace(/^[ab]\//, "");
  if (!path || /\s/.test(path) && !/[./\\]/.test(path)) return null;
  if (/^(files|changedPaths|paths)\s*:/i.test(path)) return null;
  if (/^(update|apply|patch|approval|request)\b/i.test(path)) return null;
  return {
    path,
    status: match[1]?.toLowerCase() ?? "changed",
    additions: match[3] ? Number(match[3]) : undefined,
    deletions: match[4] ? Number(match[4]) : undefined,
  };
}

function isLikelyPatchPath(path: string) {
  const normalized = path.replace(/^[ab]\//, "").trim();
  if (!normalized || /^(update|apply|patch|approval|request)\b/i.test(normalized)) return false;
  if (/^(files|changedPaths|paths)\s*:/i.test(normalized)) return false;
  if (/^[-+]{3}\s+/.test(normalized)) return false;
  if (/\s/.test(normalized)) return false;
  return /[./\\]/.test(normalized);
}

function patchFileSummaries(item: RuntimeTimelineItem): PatchFileSummary[] {
  const parsed = parsePatchFileSummaries(item.code)
    .filter((file) => isLikelyPatchPath(file.path))
    .map((file) => ({
      path: file.path.replace(/^[ab]\//, ""),
      status: file.status,
      additions: file.additions,
      deletions: file.deletions,
    }));
  const manual = (item.code ?? "")
    .split(/\r?\n/)
    .map(normalizePatchSummaryLine)
    .filter((entry): entry is PatchFileSummary => Boolean(entry));
  const diffFiles = splitDiffText(item.rawDetail || "")
    .map((group) => normalizePatchSummaryLine(group.newPath || group.oldPath))
    .filter((entry): entry is PatchFileSummary => Boolean(entry));
  const byPath = new Map<string, PatchFileSummary>();
  [...parsed, ...manual, ...diffFiles].forEach((file) => {
    const path = file.path.replace(/^[ab]\//, "").trim();
    if (!path || byPath.has(path)) return;
    byPath.set(path, { ...file, path });
  });
  return Array.from(byPath.values());
}

function patchStatusLabel(status?: string) {
  const normalized = status?.toLowerCase();
  if (["added", "created", "create", "new"].includes(normalized ?? "")) return "新增";
  if (["deleted", "removed", "remove"].includes(normalized ?? "")) return "删除";
  return "修改";
}

function isGenericApprovalText(value?: string | null) {
  const normalized = value?.trim().toLowerCase();
  if (!normalized) return true;
  return /^(patch |tool |command |permission )?approval request$/.test(normalized) ||
    /^(patch |tool |command )?permission request$/.test(normalized);
}

function patchFileListText(files: PatchFileSummary[]) {
  return files
    .map((file) => {
      const meta = [
        patchStatusLabel(file.status),
        file.additions !== undefined ? `+${file.additions}` : "",
        file.deletions !== undefined ? `-${file.deletions}` : "",
      ].filter(Boolean).join(" ");
      return meta ? `${file.path}  ${meta}` : file.path;
    })
    .join("\n");
}

function patchReviewPrompt(item: RuntimeTimelineItem, files: PatchFileSummary[]) {
  const title = item.title || "本轮改动";
  const fileText = files.length ? patchFileListText(files) : "暂无结构化文件列表，请结合当前改动 diff 审查。";
  return [
    `请审查这轮改动：${title}`,
    "",
    "重点检查：",
    "- 行为是否符合需求",
    "- 是否有明显回归、边界问题或遗漏测试",
    "- diff 是否有不必要的改动",
    "",
    "改动文件：",
    fileText,
  ].join("\n");
}

function approvalKind(item: RuntimeTimelineItem) {
  return normalizedToolName(item.toolName || item.meta?.find((entry) => /^[a-z_]+$/i.test(entry)) || item.title);
}

function isPatchApproval(item: RuntimeTimelineItem) {
  const kind = approvalKind(item);
  return (
    ["apply_patch", "write_file", "delete_file"].includes(kind) ||
    Boolean(item.patchId || item.diffLines?.length || item.rawDetail?.includes("diff --git"))
  );
}

function approvalFileSummaries(item: RuntimeTimelineItem) {
  if (!isPatchApproval(item)) return [];
  const files = patchFileSummaries(item).filter((file) => !file.path.trim().startsWith("{"));
  if (files.length) return files;
  const source = [item.code, item.rawDetail].filter(Boolean).join("\n");
  const found = /"path"\s*:\s*"([^"]+)"/.exec(source)?.[1] ?? /(?:path|file|target)\s*[:=]\s*["']?([^"',\n\r]+)["']?/i.exec(source)?.[1];
  if (!found) return [];
  return [{ path: found.replace(/^[ab]\//, "").trim(), status: "修改" }];
}

function approvalRequestRecord(item: RuntimeTimelineItem) {
  return parseJson(item.rawDetail || "") || parseJson(item.code || "") || null;
}

function looksLikeInternalTaskReference(value?: string) {
  const text = value?.trim() ?? "";
  if (!text) return false;
  return (
    /^(?:c?task|child|subtask|worker|agent|prop|appr)[_-](?=[a-z0-9_-]{3,}$)[a-z0-9_-]+$/i.test(text) ||
    /^sub[-_]\d+$/i.test(text)
  );
}

function displayPlanTaskHandle(id: string, agentType: string, index: number) {
  if (id && !looksLikeInternalTaskReference(id)) return id;
  return readableAgentLabel(agentType) || `任务 ${index + 1}`;
}

function displayPlanDependencySummary(dependencies: string[]) {
  if (!dependencies.length) return "";
  const readable = dependencies.filter((dependency) => !looksLikeInternalTaskReference(dependency));
  if (readable.length) return `依赖 ${readable.join(", ")}`;
  return "依赖前序任务";
}

function planSubtasksFromRecord(record: Record<string, unknown> | null) {
  const raw = Array.isArray(record?.subtasks) ? record.subtasks : [];
  return raw
    .map((entry, index) => {
      if (typeof entry === "string") {
        const match = /^\s*-?\s*([^:：]+)[:：]\s*(.+)$/.exec(entry);
        return {
          id: match?.[1]?.trim() || `sub-${index}`,
          title: match?.[2]?.trim() || entry.trim(),
          description: "",
          agentType: "",
          dependencies: [] as string[],
        };
      }
      if (!entry || typeof entry !== "object") return null;
      const subtask = entry as Record<string, unknown>;
      return {
        id: readString(subtask.id) || readString(subtask.subtaskId) || `sub-${index}`,
        title: readString(subtask.title) || readString(subtask.subtaskTitle) || `子任务 ${index + 1}`,
        description: readString(subtask.description) || readString(subtask.summary),
        agentType: readString(subtask.agentType) || readString(subtask.agent_type),
        dependencies: Array.isArray(subtask.dependencies) ? subtask.dependencies.map(readString).filter(Boolean) : [],
      };
    })
    .filter((entry): entry is NonNullable<typeof entry> => Boolean(entry));
}

function PlanApprovalPreview({ item }: { item: RuntimeTimelineItem }) {
  const record = approvalRequestRecord(item);
  const previewSectionItems = item.previewSections?.find((section) => section.kind === "items")?.items ?? [];
  const subtasks = previewSectionItems.length
    ? previewSectionItems.map((entry, index) => ({
        id: entry.id || `sub-${index}`,
        title: entry.title,
        description: entry.description || "",
        agentType: entry.meta?.[0] ?? "",
        dependencies: [],
      }))
    : planSubtasksFromRecord(record);
  const mode = readRecordString(record, ["orchestrationMode", "mode"]) || "plan";
  const goal = readRecordString(record, ["goal"]);
  const count = readRecordNumber(record, ["subtaskCount", "taskCount"]) ?? subtasks.length;
  if (!record && !subtasks.length) return null;
  return (
    <div className="hc-plan-approval">
      <div className="hc-plan-approval-summary">
        <span>{mode}</span>
        <strong>{count ? `已拆分 ${count} 个子任务` : "执行计划"}</strong>
        {goal ? <small>{compactText(goal, 160)}</small> : null}
      </div>
      {subtasks.length ? (
        <div className="hc-agent-task-list hc-plan-subtask-list">
          {subtasks.slice(0, 8).map((task, index) => (
            <article key={`${task.id}:${index}`} data-tone="recorded">
              <Circle size={10} />
              <div>
                <strong>{task.title}</strong>
                <small>{[task.agentType ? readableAgentLabel(task.agentType) : "", displayPlanDependencySummary(task.dependencies)].filter(Boolean).join(" · ")}</small>
                {task.description ? <p>{compactText(task.description, 180)}</p> : null}
              </div>
              <em>{displayPlanTaskHandle(task.id, task.agentType, index)}</em>
            </article>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function CompletionEvidencePreview({ item }: { item: RuntimeTimelineItem }) {
  const evidence = item.completionEvidence;
  if (!evidence) return null;
  return (
    <div className="hc-completion-evidence">
      <p>{evidence.summary}</p>
      {evidence.metrics.length ? (
        <dl className="hc-approval-preview" aria-label="完成审查指标">
          {evidence.metrics.slice(0, 6).map((metric) => (
            <div key={`${metric.label}:${metric.value}`}>
              <dt>{metric.label}</dt>
              <dd>{metric.value}</dd>
            </div>
          ))}
        </dl>
      ) : null}
      {evidence.issues.length ? (
        <ul>
          {evidence.issues.slice(0, 4).map((issue, index) => <li key={`${index}:${issue}`}>{issue}</li>)}
        </ul>
      ) : null}
    </div>
  );
}

function readMetadataString(message: SessionWorkspaceMessage, keys: string[]) {
  for (const key of keys) {
    const value = message.metadata?.[key];
    const text = readString(value);
    if (text) return text;
  }
  return "";
}

function readMetadataPreviewRows(message: SessionWorkspaceMessage, keys: string[]) {
  for (const key of keys) {
    const rows = metadataPreviewRows(message.metadata?.[key]);
    if (rows.length) return rows;
  }
  return [];
}

function safePermissionDetailText(value: string) {
  const text = value.trim();
  if (!text || isGenericApprovalText(text) || looksLikeInternalPayloadText(text) || parseJson(text)) return "";
  return text;
}

function messageTitleForKind(kind: CleanTranscriptKind | string, message: SessionWorkspaceMessage) {
  const title = readMetadataString(message, ["title", "label", "action", "event", "state"]);
  if (title && !looksLikeInternalTaskReference(title) && !isInternalApprovalToolName(title)) return title;
  const labels: Record<string, string> = {
    api_retry: "API 重试",
    ask_user_question: "需要你补充信息",
    background_task: "后台任务",
    compact_summary: "上下文已压缩",
    computer_use_permission: "Computer Use 权限",
    error: "出错了",
    goal_event: "目标状态",
    memory_event: "记忆更新",
    plan_update: "计划更新",
    slash_command: "命令结果",
    status: "状态",
    system: "系统消息",
    task_summary: "任务摘要",
  };
  return labels[kind] ?? "运行事件";
}

function quoteMessageText(message: SessionWorkspaceMessage) {
  const content = message.content.trim();
  if (!content) return "";
  const speaker = message.role === "user" ? "用户" : "助手";
  return [`> ${speaker}：`, ...content.split(/\r?\n/).map((line) => `> ${line}`)].join("\n");
}

function messageAttachments(message: SessionWorkspaceMessage) {
  return uniqueAttachments([
    ...attachmentsFromMetadata(message.metadata),
    ...imageAttachmentsFromText(message.content),
  ]);
}

function MessageActions({
  message,
  align = "left",
  onCopyRuntimeText,
  onQuoteMessage,
  onContinueFromMessage,
  onBranchFromMessage,
  onDeleteMessage,
  busy = false,
}: {
  message: SessionWorkspaceMessage;
  align?: "left" | "right";
  onCopyRuntimeText?: (label: string, text: string) => void | Promise<void>;
  onQuoteMessage?: (text: string) => void;
  onContinueFromMessage?: (message: SessionWorkspaceMessage) => void | Promise<void>;
  onBranchFromMessage?: (message: SessionWorkspaceMessage) => void | Promise<void>;
  onDeleteMessage?: (message: SessionWorkspaceMessage) => void | Promise<void>;
  busy?: boolean;
}) {
  const [moreOpen, setMoreOpen] = useState(false);
  const content = message.content.trim();
  if (!content || !onCopyRuntimeText) return null;
  const quote = quoteMessageText(message);
  const closeMore = () => setMoreOpen(false);
  return (
    <div className="hc-message-actions" data-align={align}>
      <button type="button" title="复制消息" onClick={() => void onCopyRuntimeText("消息内容", content)}>
        <Copy size={13} />
        <span>复制</span>
      </button>
      <button
        type="button"
        title={onQuoteMessage ? "引用到输入框" : "复制为引用"}
        onClick={() => {
          if (onQuoteMessage) {
            onQuoteMessage(quote);
            return;
          }
          void onCopyRuntimeText("引用消息", quote);
        }}
      >
        <CornerDownRight size={13} />
        <span>引用</span>
      </button>
      <div>
        <button
          type="button"
          title="更多"
          aria-label="更多"
          aria-expanded={moreOpen}
          onClick={() => setMoreOpen((open) => !open)}
        >
          <MoreHorizontal size={14} />
        </button>
        {moreOpen ? (
          <menu aria-label="消息更多操作">
            <li>
              <button
                type="button"
                onClick={() => {
                  closeMore();
                  void onCopyRuntimeText("Markdown 引用", quote);
                }}
              >
                <span>复制为 Markdown</span>
                <small>保留发言人和引用格式</small>
              </button>
            </li>
            <li>
              <button
                type="button"
                onClick={() => {
                  closeMore();
                  void onCopyRuntimeText("消息 ID", message.id);
                }}
              >
                <span>复制消息 ID</span>
                <small>用于定位 transcript 记录</small>
              </button>
            </li>
            <li>
              <button
                type="button"
                disabled={!onContinueFromMessage || busy}
                title={onContinueFromMessage ? "截断当前会话到这条消息" : "需要后端支持从指定 transcript target 继续"}
                onClick={() => {
                  if (!onContinueFromMessage || busy) return;
                  closeMore();
                  void onContinueFromMessage(message);
                }}
              >
                <RotateCcw size={13} />
                <span>从这里继续</span>
                <small>保留到此处，后续记录从 transcript 删除</small>
              </button>
            </li>
            <li>
              <button
                type="button"
                disabled={!onBranchFromMessage || busy}
                title={onBranchFromMessage ? "复制到一条新的分支会话" : "需要后端 branchSession 接口"}
                onClick={() => {
                  if (!onBranchFromMessage || busy) return;
                  closeMore();
                  void onBranchFromMessage(message);
                }}
              >
                <GitBranch size={13} />
                <span>从这里分支</span>
                <small>复制到新会话，原会话保持不变</small>
              </button>
            </li>
            <li>
              <button
                type="button"
                disabled={!onDeleteMessage || busy}
                title={onDeleteMessage ? "删除这条会话记录" : "需要后端 transcript mutation 接口"}
                onClick={() => {
                  if (!onDeleteMessage || busy) return;
                  closeMore();
                  void onDeleteMessage(message);
                }}
              >
                <Trash2 size={13} />
                <span>删除消息</span>
                <small>仅删除 transcript，不撤销文件改动</small>
              </button>
            </li>
          </menu>
        ) : null}
      </div>
    </div>
  );
}

export const CleanAssistantMessage = memo(function CleanAssistantMessage({
  message,
  onCopyRuntimeText,
  onQuoteMessage,
  onContinueFromMessage,
  onBranchFromMessage,
  onDeleteMessage,
  busy,
}: {
  message: SessionWorkspaceMessage;
  onCopyRuntimeText?: (label: string, text: string) => void | Promise<void>;
  onQuoteMessage?: (text: string) => void;
  onContinueFromMessage?: (message: SessionWorkspaceMessage) => void | Promise<void>;
  onBranchFromMessage?: (message: SessionWorkspaceMessage) => void | Promise<void>;
  onDeleteMessage?: (message: SessionWorkspaceMessage) => void | Promise<void>;
  busy?: boolean;
}) {
  const content = stripAssistantRuntimeProgress(message.content).trim();
  const attachments = messageAttachments(message);
  if (!content && !attachments.length) return null;
  if (message.metadata?.kind === "assistant_progress") {
    return (
      <p className="hc-progress-line">
        {content}
      </p>
    );
  }
  return (
    <div className="hc-message-stack" data-role="assistant">
      <article className="hc-message hc-assistant" data-layout={isDocumentMessage(content) ? "document" : "bubble"}>
        {content ? <CleanMarkdown content={content} /> : null}
        <CleanAttachmentGallery attachments={attachments} onCopy={onCopyRuntimeText} />
      </article>
      <MessageActions
        message={message}
        onCopyRuntimeText={onCopyRuntimeText}
        onQuoteMessage={onQuoteMessage}
        onContinueFromMessage={onContinueFromMessage}
        onBranchFromMessage={onBranchFromMessage}
        onDeleteMessage={onDeleteMessage}
        busy={busy}
      />
    </div>
  );
});

export const CleanUserMessage = memo(function CleanUserMessage({
  message,
  onCopyRuntimeText,
  onQuoteMessage,
  onContinueFromMessage,
  onBranchFromMessage,
  onDeleteMessage,
  busy,
}: {
  message: SessionWorkspaceMessage;
  onCopyRuntimeText?: (label: string, text: string) => void | Promise<void>;
  onQuoteMessage?: (text: string) => void;
  onContinueFromMessage?: (message: SessionWorkspaceMessage) => void | Promise<void>;
  onBranchFromMessage?: (message: SessionWorkspaceMessage) => void | Promise<void>;
  onDeleteMessage?: (message: SessionWorkspaceMessage) => void | Promise<void>;
  busy?: boolean;
}) {
  const attachments = messageAttachments(message);
  return (
    <div className="hc-message-stack" data-role="user">
      <article className="hc-message hc-user">
        <CleanMarkdown content={message.content} />
        <CleanAttachmentGallery attachments={attachments} onCopy={onCopyRuntimeText} />
      </article>
      <MessageActions
        message={message}
        align="right"
        onCopyRuntimeText={onCopyRuntimeText}
        onQuoteMessage={onQuoteMessage}
        onContinueFromMessage={onContinueFromMessage}
        onBranchFromMessage={onBranchFromMessage}
        onDeleteMessage={onDeleteMessage}
        busy={busy}
      />
    </div>
  );
});

export const CleanThinkingBlock = memo(function CleanThinkingBlock({ message }: { message: SessionWorkspaceMessage }) {
  const [expanded, setExpanded] = useState(false);
  const text = message.content.trim() || "正在思考";
  const preview = compactText(text.split(/\r?\n/).find((line) => line.trim()) ?? text, 120);
  const transient = message.metadata?.transient === true;
  const title = transient ? "正在处理" : message.streaming ? "正在思考" : "思考";
  const canExpand = !transient;
  return (
    <section className="hc-thinking">
      <button type="button" aria-expanded={canExpand ? expanded : false} onClick={() => canExpand && setExpanded((open) => !open)}>
        {canExpand ? (expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />) : null}
        <span>{title}</span>
        <em><CleanInlineMarkdown content={preview} /></em>
      </button>
      {canExpand && expanded ? (
        <div className="hc-thinking-detail">
          <CleanMarkdown content={text} />
        </div>
      ) : null}
    </section>
  );
});

export const CleanToolMessageBlock = memo(function CleanToolMessageBlock({
  message,
  onCopyRuntimeText,
}: {
  message: SessionWorkspaceMessage;
  onCopyRuntimeText?: (label: string, text: string) => void | Promise<void>;
}) {
  const [expanded, setExpanded] = useState(false);
  const lifecycleStatus = typeof message.metadata?.status === "string" ? message.metadata.status : "";
  const blocked = message.status === "blocked" || lifecycleStatus.toLowerCase() === "blocked";
  const failed = !blocked && (message.status === "failed" || message.metadata?.isError === true);
  const cancelled = message.status === "cancelled";
  const askUserTool = isAskUserToolMessage(message);
  const question = toolQuestionText(message);
  const input = typeof message.metadata?.inputText === "string" ? message.metadata.inputText : "";
  const result = typeof message.metadata?.resultText === "string" ? message.metadata.resultText : "";
  const metadataTarget = cleanInlineDisplayText(readMetadataString(message, ["displayTarget", "target", "inputSummary"]));
  const fallbackInput = metadataTarget ? JSON.stringify({ target: metadataTarget }) : "";
  const previewRows = metadataPreviewRows(message.metadata?.resultPreview);
  const safeContent = cleanInlineDisplayText(message.content);
  const extraContent = safeContent && safeContent !== input && safeContent !== result ? safeContent : "";
  const needsDiagnostics = Boolean(blocked || failed || cancelled);
  const safeInputDetail = needsDiagnostics && input && !looksLikeInternalPayloadText(input) ? `输入\n${input}` : "";
  const safeResultDetail = needsDiagnostics && result && !looksLikeInternalPayloadText(result) ? `结果\n${result}` : "";
  const safeExtraDetail = needsDiagnostics && extraContent && !looksLikeInternalPayloadText(extraContent) ? extraContent : "";
  const details = askUserTool
    ? [
        question ? `问题\n${question}` : "",
        result && !looksLikeInternalPayloadText(result) ? `结果\n${result}` : "",
      ].filter(Boolean).join("\n\n")
    : [safeInputDetail, safeResultDetail, safeExtraDetail].filter(Boolean).join("\n\n");
  const displayDetails = askUserTool
    ? [
        question ? `Question\n${question}` : "",
        structuredToolDetailText(result, "Result"),
      ].filter(Boolean).join("\n\n")
    : [
        needsDiagnostics ? structuredToolDetailText(input, "Input") : "",
        needsDiagnostics ? structuredToolDetailText(result, "Result") : "",
        safeExtraDetail,
      ].filter(Boolean).join("\n\n");
  const displayTitle = cleanInlineDisplayText(readMetadataString(message, ["displayTitle"]));
  const title = askUserTool
    ? "需要你补充信息"
    : displayTitle
      ? displayTitle
      : toolActionTitle({
        toolName: message.toolName,
        title: cleanInlineDisplayText(readMetadataString(message, ["displayTitle", "displayTarget", "displaySummary", "target", "inputSummary", "title", "label", "action"])),
        input: input || fallbackInput,
        rawDetail: result && !looksLikeInternalPayloadText(result) ? result : safeContent,
      });
  const target = toolInlineTarget(message) || compactText(metadataTarget, 92);
  const showTarget = Boolean(target && !title.includes(target));
  const durationLabel = formatDuration(readRecordNumber(message.metadata, ["durationMs"]));
  const toneStatus = blocked ? "blocked" : message.status;
  const statusText = message.streaming ? "运行中" : blocked ? "已阻塞" : cancelled ? "已取消" : failed ? "失败" : "完成";
  return (
    <section className="hc-tool-inline" data-tone={failed ? "danger" : statusTone(toneStatus)} data-kind={(message.toolName ?? "tool").replace(/_/g, "-")}>
      <button type="button" aria-expanded={expanded} onClick={() => setExpanded((open) => !open)}>
        {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        <Wrench size={15} />
        <strong>{title}</strong>
        {showTarget ? <code>{target}</code> : null}
        <span>{toolInlineSummary(message)}</span>
        <em>{statusText}</em>
        {durationLabel ? <time>{durationLabel}</time> : null}
      </button>
      {expanded && previewRows.length ? (
        <dl className="hc-tool-preview" aria-label="工具结果预览">
          {previewRows.map((row) => (
            <div key={`${row.label}:${row.value}`}>
              <dt>{row.label}</dt>
              <dd>{row.value}</dd>
            </div>
          ))}
        </dl>
      ) : null}
      {expanded && displayDetails ? (
        <figure className="hc-tool-detail">
          <figcaption>
            <span>工具详情</span>
            {onCopyRuntimeText ? (
              <button type="button" onClick={() => void onCopyRuntimeText("工具详情", displayDetails)}>
                复制
              </button>
            ) : null}
          </figcaption>
          <pre>{displayDetails}</pre>
        </figure>
      ) : null}
    </section>
  );
});

export const CleanToolGroupBlock = memo(function CleanToolGroupBlock({
  items,
  onCopyRuntimeText,
}: {
  items: RuntimeTimelineItem[];
  onCopyRuntimeText?: (label: string, text: string) => void | Promise<void>;
}) {
  const [expanded, setExpanded] = useState(() => items.some((item) => isInFlight(item.status) || item.parentToolUseId));
  const running = items.some((item) => isInFlight(item.status));
  const failed = items.some((item) => statusTone(item.status) === "danger");
  const childCount = items.filter((item) => item.parentToolUseId).length;
  const summary = toolGroupSummary(items) || `${items.length} 个工具`;
  const statusText = running ? "运行中" : failed ? "有失败" : "已完成";
  const tree = useMemo(() => flattenWorklogTree(buildWorklogTree(items)), [items]);
  return (
    <section className="hc-tool-group" data-tone={failed ? "danger" : running ? "running" : "success"}>
      <button type="button" aria-expanded={expanded} onClick={() => setExpanded((open) => !open)}>
        {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        <Wrench size={15} />
        <strong>{summary}</strong>
        <span>{childCount ? `包含 ${childCount} 个子步骤` : "工具调用"}</span>
        <em>{statusText}</em>
      </button>
      {expanded ? (
        <div className="hc-tool-group-list">
          {tree.flatMap((node) => {
            const message = runtimeItemToToolMessage(node.item);
            return message ? (
              <div key={node.item.id} className="hc-tool-group-item" data-depth={node.depth}>
                <CleanToolMessageBlock message={message} onCopyRuntimeText={onCopyRuntimeText} />
              </div>
            ) : [];
          })}
        </div>
      ) : null}
    </section>
  );
});

export const CleanPermissionMessageBlock = memo(function CleanPermissionMessageBlock({
  message,
  onApprove,
  onApproveAlways,
  onReject,
  busyId,
}: {
  message: SessionWorkspaceMessage;
  onApprove?: (approvalId: string) => void | Promise<void>;
  onApproveAlways?: (approvalId: string) => void | Promise<void>;
  onReject?: (approvalId: string) => void | Promise<void>;
  busyId?: string | null;
}) {
  const requestId = typeof message.metadata?.requestId === "string" ? message.metadata.requestId : "";
  const resolved = message.metadata?.resolved === true;
  const decision = typeof message.metadata?.decision === "string" ? message.metadata.decision : "";
  const busy = Boolean(requestId && busyId === requestId);
  const approvalKind = readMetadataString(message, ["approvalKind", "kind"]) || message.toolName;
  const canAlwaysAllow = Boolean(onApproveAlways && supportsAlwaysAllowKind(approvalKind));
  const inputText = readMetadataString(message, ["parametersPreview", "inputText", "command"]);
  const detailText = safePermissionDetailText(message.content);
  const previewRows = readMetadataPreviewRows(message, ["previewRows", "preview"]);
  const changedPaths = readMetadataList(message, ["changedPaths", "paths", "files"]);
  const filesChanged = readMetadataString(message, ["filesChanged"]);
  const diffText = readMetadataString(message, ["diffText"]);
  const permissionTitle = toolActionTitle({
    toolName: message.toolName,
    title: readMetadataString(message, ["title", "label"]),
    input: inputText,
    rawDetail: message.content,
    fallback: "权限请求",
  });
  const resolutionLabel = resolved ? `已${decision === "rejected" ? "拒绝" : "批准"}` : "等待你的操作";
  const cardTitle = resolved ? permissionTitle : `${permissionTitle} 需要确认`;
  return (
    <section className="hc-permission-card" data-resolved={resolved ? "true" : "false"}>
      <header>
        <CircleAlert size={15} />
        <div>
          <strong>{cardTitle}</strong>
          <span>{resolutionLabel}</span>
        </div>
        <StatusChip status={resolved ? (decision === "rejected" ? "rejected" : "approved") : "waiting_approval"} />
      </header>
      {previewRows.length ? (
        <dl className="hc-approval-preview" aria-label="审批预览">
          {previewRows.slice(0, 5).map((row) => (
            <div key={`${row.label}:${row.value}`}>
              <dt>{row.label}</dt>
              <dd>{row.value}</dd>
            </div>
          ))}
        </dl>
      ) : null}
      {changedPaths.length ? (
        <div className="hc-change-list hc-change-list-compact">
          {changedPaths.slice(0, 6).map((path) => (
            <span key={path}>
              <code>{path}</code>
              <small>待确认</small>
            </span>
          ))}
          {changedPaths.length > 6 ? <span><code>另 {changedPaths.length - 6} 个文件</code></span> : null}
        </div>
      ) : filesChanged ? (
        <p className="hc-permission-meta">涉及 {filesChanged} 个文件</p>
      ) : null}
      {diffText ? (
        <DiffPreview
          item={{
            id: `${message.id}:diff`,
            kind: "approval",
            title: permissionTitle,
            status: resolved ? decision || "approved" : "waiting_approval",
            rawDetail: diffText,
          }}
        />
      ) : detailText ? <pre>{detailText}</pre> : null}
      {!resolved && requestId ? (
        <div className="hc-approval-actions">
          <button type="button" data-variant="allow" disabled={busy} onClick={() => void onApprove?.(requestId)}>允许一次</button>
          <button type="button" data-variant="deny" disabled={busy} onClick={() => void onReject?.(requestId)}>拒绝</button>
          <button
            type="button"
            data-variant="always"
            disabled={busy || !canAlwaysAllow}
            title={canAlwaysAllow ? "以后同类操作不再询问" : "此审批暂不支持始终允许"}
            onClick={() => void onApproveAlways?.(requestId)}
          >
            始终允许
          </button>
        </div>
      ) : null}
    </section>
  );
});

export const CleanSpecialEventBlock = memo(function CleanSpecialEventBlock({
  message,
  transcriptKind,
}: {
  message: SessionWorkspaceMessage;
  transcriptKind: CleanTranscriptKind;
}) {
  const [expanded, setExpanded] = useState(false);
  const content = cleanInlineDisplayText(message.content);
  const title = messageTitleForKind(transcriptKind, message);
  const summary = compactText(
    cleanInlineDisplayText(readMetadataString(message, ["summary", "description", "message"])) || content,
    180,
  );
  const icon =
    transcriptKind === "compact_summary" ? <History size={15} /> :
    transcriptKind === "goal_event" ? <Target size={15} /> :
    transcriptKind === "memory_event" ? <BookMarked size={15} /> :
    transcriptKind === "task_summary" || transcriptKind === "plan_update" ? <ListChecks size={15} /> :
    transcriptKind === "background_task" ? <Files size={15} /> :
    transcriptKind === "api_retry" ? <RotateCcw size={15} /> :
    transcriptKind === "ask_user_question" ? <HelpCircle size={15} /> :
    <CircleAlert size={15} />;
  const metadataLines = Object.entries(message.metadata ?? {})
    .filter(([key, value]) =>
      key !== "kind" &&
      !SPECIAL_EVENT_METADATA_BLOCKLIST.has(key) &&
      value !== undefined &&
      value !== null &&
      typeof value !== "object")
    .map(([key, value]) => {
      const cleaned = cleanSpecialEventMetadataValue(key, value);
      return cleaned ? `${key}: ${cleaned}` : "";
    })
    .filter(Boolean);
  const details = [content, metadataLines.join("\n")].filter(Boolean).join("\n\n");

  if (transcriptKind === "compact_summary") {
    return (
      <section className="hc-compact-divider">
        <span />
        <button type="button" disabled={!details} onClick={() => setExpanded((open) => !open)}>
          {icon}
          <strong>{title}</strong>
          {summary ? <em>{summary}</em> : null}
        </button>
        <span />
        {expanded && details ? <pre>{details}</pre> : null}
      </section>
    );
  }

  return (
    <section className="hc-special-event" data-kind={transcriptKind}>
      <button type="button" aria-expanded={expanded} onClick={() => setExpanded((open) => !open)}>
        {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        {icon}
        <strong>{title}</strong>
        {summary ? <span>{summary}</span> : null}
        <StatusChip status={readMetadataString(message, ["status", "phase"]) || message.status} />
      </button>
      {expanded && details ? <pre>{details}</pre> : null}
    </section>
  );
});

export const CleanSlashCommandBlock = memo(function CleanSlashCommandBlock({
  message,
  onCopyRuntimeText,
}: {
  message: SessionWorkspaceMessage;
  onCopyRuntimeText?: (label: string, text: string) => void | Promise<void>;
}) {
  const [expanded, setExpanded] = useState(false);
  const content = message.content.trim();
  const command = readMetadataString(message, ["command", "title"]) || "/command";
  const args = readMetadataString(message, ["args", "argument"]);
  const summary = compactText(
    readMetadataString(message, ["summary", "description", "message"]) ||
      content.split(/\r?\n/).find((line) => line.trim()) ||
      content,
    180,
  );
  const status = readMetadataString(message, ["status", "phase"]) || message.status;
  const detailLabel = `${command} result`;

  return (
    <section className="hc-slash-command" data-status={statusTone(status)}>
      <button type="button" aria-expanded={expanded} onClick={() => setExpanded((open) => !open)}>
        {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        <TerminalSquare size={15} />
        <code>{command}</code>
        {args ? <small>{args}</small> : null}
        {summary ? <span>{summary}</span> : null}
        <StatusChip status={status} />
      </button>
      {expanded && content ? (
        <figure className="hc-slash-command-detail">
          <figcaption>
            <span>命令详情</span>
            {onCopyRuntimeText ? (
              <button type="button" onClick={() => void onCopyRuntimeText(detailLabel, content)}>
                <Copy size={12} />
                <span>复制</span>
              </button>
            ) : null}
          </figcaption>
          <CleanMarkdown content={content} />
        </figure>
      ) : null}
    </section>
  );
});

function readMetadataList(message: SessionWorkspaceMessage, keys: string[]) {
  for (const key of keys) {
    const value = message.metadata?.[key];
    if (Array.isArray(value)) return value;
  }
  return [];
}

function readMetadataRecordList(message: SessionWorkspaceMessage, keys: string[]) {
  return readMetadataList(message, keys)
    .filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object" && !Array.isArray(item));
}

function recordFromValue(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function readMetadataRecord(message: SessionWorkspaceMessage, keys: string[]) {
  for (const key of keys) {
    const record = recordFromValue(message.metadata?.[key]);
    if (record) return record;
  }
  return null;
}

function readMetadataPlanRecord(message: SessionWorkspaceMessage) {
  const direct = readMetadataRecord(message, ["plan", "splitPlan", "executionPlan"]);
  if (direct) return direct;
  const nestedPayload = readMetadataRecord(message, ["payload"]);
  if (nestedPayload) {
    const nested = recordFromValue(nestedPayload.plan) ?? recordFromValue(nestedPayload.splitPlan) ?? recordFromValue(nestedPayload.executionPlan);
    if (nested) return nested;
  }
  return message.metadata && typeof message.metadata === "object"
    ? message.metadata as Record<string, unknown>
    : null;
}

function readPlanLikeSubtasks(message: SessionWorkspaceMessage) {
  const metadata = message.metadata ?? {};
  const plan = readMetadataPlanRecord(message);
  const sources = [
    Array.isArray(metadata.subtasks) ? metadata.subtasks : null,
    Array.isArray(metadata.tasks) ? metadata.tasks : null,
    Array.isArray(metadata.plan) ? metadata.plan : null,
    Array.isArray(metadata.splitPlan) ? metadata.splitPlan : null,
    Array.isArray(metadata.executionPlan) ? metadata.executionPlan : null,
    plan && Array.isArray(plan.subtasks) ? plan.subtasks : null,
    plan && Array.isArray(plan.tasks) ? plan.tasks : null,
  ].filter((items): items is unknown[] => Array.isArray(items));
  for (const source of sources) {
    if (source.length) return source;
  }
  return [];
}

function readAgentGroupTasks(message: SessionWorkspaceMessage) {
  const agentTasks = readMetadataRecordList(message, ["agentTasks", "tasks", "subtasks"]);
  if (agentTasks.length) return agentTasks;
  const members = readMetadataRecordList(message, ["members"]);
  const tasks: Record<string, unknown>[] = members.map((member, index) => ({
    id: readString(member.agentId) || readString(member.id) || `agent-${index}`,
    title: readString(member.currentTask) || readString(member.task) || readString(member.title) || readString(member.role) || `Agent ${index + 1}`,
    status: readString(member.status) || "recorded",
    agentType: readString(member.role) || readString(member.agentType),
    workerName: readString(member.name) || readString(member.agentId),
    summary: readString(member.summary) || readString(member.message),
  }));
  return tasks;
}

const SPECIAL_EVENT_METADATA_BLOCKLIST = new Set([
  "_chatCompat",
  "activeStep",
  "completedSteps",
  "context",
  "currentStep",
  "eventId",
  "fingerprint",
  "frames",
  "messages",
  "options",
  "agentTasks",
  "agentResults",
  "members",
  "plan",
  "progress",
  "provider",
  "providerRequest",
  "providerResponse",
  "payload",
  "questions",
  "rawJson",
  "requestId",
  "sessionId",
  "stepCount",
  "taskId",
  "toolCallId",
  "toolProgress",
  "tool_progress",
  "tool_results",
  "trace",
  "uiReplayScope",
  "visibility",
  "subtasks",
  "tasks",
  "workspaceRoot",
  "yuanbao",
]);

function formatAgentTaskStatus(status?: string) {
  const normalized = status?.toLowerCase() ?? "";
  if (["running", "started", "queued", "pending", "planning", "verifying"].includes(normalized)) return "运行中";
  if (["waiting_approval", "blocked"].includes(normalized)) return "等待确认";
  if (["completed", "complete", "done", "finished", "succeeded", "success", "passed"].includes(normalized)) return "完成";
  if (["cancelled", "canceled"].includes(normalized)) return "已取消";
  if (["failed", "error", "rejected"].includes(normalized)) return "失败";
  return status || "记录";
}

function agentTaskTone(status?: string) {
  const normalized = status?.toLowerCase() ?? "";
  if (["failed", "error", "rejected", "cancelled", "canceled"].includes(normalized)) return "danger";
  if (["running", "started", "queued", "pending", "planning", "verifying", "waiting_approval", "blocked"].includes(normalized)) return "running";
  if (["completed", "complete", "done", "finished", "succeeded", "success", "passed"].includes(normalized)) return "success";
  return "neutral";
}

function normalizeAgentLabel(value?: string) {
  return value?.trim().replace(/[_-]+/g, " ").replace(/\s+/g, " ").toLowerCase() ?? "";
}

function readableAgentLabel(value?: string) {
  const normalized = normalizeAgentLabel(value);
  if (!normalized) return "";
  return normalized.replace(/\b\w/g, (char) => char.toUpperCase());
}

function displayAgentMeta(workerName?: string, agentType?: string, durationMs?: number | null) {
  const worker = workerName?.trim() ?? "";
  const role = readableAgentLabel(agentType);
  const workerKey = normalizeAgentLabel(worker);
  const roleKey = normalizeAgentLabel(role);
  const isGenericWorkerName =
    Boolean(workerKey) &&
    Boolean(roleKey) &&
    (workerKey === roleKey || workerKey === `${roleKey} worker` || workerKey === `${roleKey} agent`);
  return [
    isGenericWorkerName ? "" : worker,
    role,
    durationMs !== null && durationMs !== undefined ? formatDuration(durationMs) : "",
  ].filter(Boolean);
}

function mergeAgentResultSummariesByTask(results: Record<string, unknown>[]) {
  const summaries = new Map<string, string>();
  for (const result of results) {
    const taskId = readString(result.taskId);
    const summary = readString(result.summary);
    if (!taskId || !summary) continue;
    const current = summaries.get(taskId);
    summaries.set(taskId, current && !current.includes(summary) ? `${current}\n${summary}` : (current || summary));
  }
  return summaries;
}

function looksLikeRawChildTaskId(value?: string) {
  return looksLikeInternalTaskReference(value);
}

function displayAgentTaskTitle(title: string, id: string, agentType: string, index: number) {
  const candidate = title || id;
  if (candidate && !looksLikeRawChildTaskId(candidate)) return candidate;
  const role = readableAgentLabel(agentType);
  return role ? `${role} task` : `Agent task ${index + 1}`;
}

function displayAgentTaskRecordTitle(task: Record<string, unknown>, id: string, index: number) {
  const agentType = readString(task.agentType) || readString(task.role);
  const candidates = [
    readString(task.currentTask),
    readString(task.task),
    readString(task.title),
    readString(task.name),
    readString(task.summary),
  ];
  const safe = candidates.find((candidate) => candidate && !looksLikeRawChildTaskId(candidate));
  return displayAgentTaskTitle(safe || "", id, agentType, index);
}

function displayGroupTitle(message: SessionWorkspaceMessage, fallback: string) {
  const title = readMetadataString(message, ["title", "label"]);
  if (title && !looksLikeInternalTaskReference(title) && !isInternalApprovalToolName(title)) {
    return title;
  }
  const summary = readMetadataString(message, ["currentTask", "summary", "status"]);
  if (summary && !looksLikeInternalTaskReference(summary)) {
    return compactText(summary, 80);
  }
  return fallback;
}

function displayPlanSubtaskTitle(record: Record<string, unknown>, index: number) {
  const candidates = [
    readString(record.currentTask),
    readString(record.task),
    readString(record.summary),
    readString(record.description),
    readString(record.title),
    readString(record.subtaskTitle),
    readString(record.name),
    readString(record.status),
  ];
  const safe = candidates.find((candidate) => candidate && !looksLikeRawChildTaskId(candidate));
  if (safe) return safe;
  const role = readableAgentLabel(readString(record.agentType) || readString(record.agent_type) || readString(record.role));
  return role ? `${role} task` : `子任务 ${index + 1}`;
}

export const CleanAgentTaskGroupBlock = memo(function CleanAgentTaskGroupBlock({
  message,
}: {
  message: SessionWorkspaceMessage;
}) {
  const [expanded, setExpanded] = useState(agentTaskTone(message.status) === "running");
  const tasks = readAgentGroupTasks(message);
  const results = readMetadataRecordList(message, ["agentResults"]);
  const resultSummariesByTask = useMemo(() => mergeAgentResultSummariesByTask(results), [results]);
  const title = displayGroupTitle(message, `派遣了 ${tasks.length} 个代理`);
  const summary = readMetadataString(message, ["summary"]) || message.content.trim();

  return (
    <section className="hc-agent-group" data-status={agentTaskTone(message.status)}>
      <button type="button" aria-expanded={expanded} onClick={() => setExpanded((open) => !open)}>
        {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        <Files size={15} />
        <strong>{title}</strong>
        {summary ? <span>{summary}</span> : null}
        <StatusChip status={message.status} />
      </button>
      {expanded ? (
        <div className="hc-agent-task-list">
          {tasks.map((task, index) => {
            const id = readString(task.id) || readString(task.title);
            const taskStatus = readString(task.status);
            const worker = readString(task.workerName);
            const agentType = readString(task.agentType);
            const resultSummary = id ? resultSummariesByTask.get(id) : "";
            const description = readString(task.attention) || readString(task.summary) || readString(task.errorMessage) || resultSummary;
            const duration = readRecordNumber(task, ["durationMs"]);
            const meta = displayAgentMeta(worker, agentType, duration);
            const title = displayAgentTaskRecordTitle(task, id, index);
            return (
              <article key={id} data-tone={agentTaskTone(taskStatus)}>
                <Circle size={10} />
                <div>
                  <strong>{title}</strong>
                  {meta.length ? <small>{meta.join(" · ")}</small> : null}
                  {description ? <p>{compactText(description, 180)}</p> : null}
                </div>
                <em>{formatAgentTaskStatus(taskStatus)}</em>
              </article>
            );
          })}
        </div>
      ) : null}
    </section>
  );
});

export const CleanPlanUpdateBlock = memo(function CleanPlanUpdateBlock({
  message,
}: {
  message: SessionWorkspaceMessage;
}) {
  const [expanded, setExpanded] = useState(true);
  const plan = readMetadataPlanRecord(message);
  const rawSubtasks = readPlanLikeSubtasks(message);
  const subtasks = rawSubtasks
    .map((entry, index) => {
      if (typeof entry === "string") {
        const title = entry.trim();
        return title ? {
          id: `sub-${index}`,
          title,
          description: "",
          agentType: "",
          dependencies: [] as string[],
          status: "",
        } : null;
      }
      const record = recordFromValue(entry);
      if (!record) return null;
      const title = displayPlanSubtaskTitle(record, index) ||
        readString(record.title) ||
        readString(record.subtaskTitle) ||
        readString(record.name) ||
        readString(record.summary) ||
        readString(record.description) ||
        `子任务 ${index + 1}`;
      return {
        id: readString(record.id) || readString(record.subtaskId) || readString(record.taskId) || `sub-${index}`,
        title,
        description: readString(record.description) || readString(record.summary),
        agentType: readString(record.agentType) || readString(record.agent_type) || readString(record.role),
        dependencies: Array.isArray(record.dependencies) ? record.dependencies.map(readString).filter(Boolean) : [],
        status: readString(record.status),
      };
    })
    .filter((entry): entry is NonNullable<typeof entry> => Boolean(entry));
  const mode =
    readRecordString(plan, ["orchestrationMode", "mode", "strategy"]) ||
    readMetadataString(message, ["orchestrationMode", "mode", "strategy"]) ||
    "plan";
  const goal = readRecordString(plan, ["goal", "summary"]) || readMetadataString(message, ["goal"]);
  const count = readRecordNumber(plan, ["subtaskCount", "taskCount"]) ?? subtasks.length;
  const summary =
    readMetadataString(message, ["summary", "description", "message"]) ||
    (count ? `已拆分 ${count} 个子任务，准备派发 agent。` : cleanInlineDisplayText(message.content));

  if (!subtasks.length && !summary) {
    return <CleanSpecialEventBlock message={message} transcriptKind="plan_update" />;
  }

  return (
    <section className="hc-agent-group hc-plan-event" data-status={agentTaskTone(message.status)}>
      <button type="button" aria-expanded={expanded} onClick={() => setExpanded((open) => !open)}>
        {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        <ListChecks size={15} />
        <strong>{displayGroupTitle(message, "计划更新")}</strong>
        {summary ? <span>{compactText(summary, 180)}</span> : null}
        <StatusChip status={readMetadataString(message, ["status"]) || message.status || "recorded"} />
      </button>
      {expanded ? (
        <div className="hc-plan-approval">
          <div className="hc-plan-approval-summary">
            <span>{mode}</span>
            <strong>{count ? `已拆分 ${count} 个子任务` : "执行计划"}</strong>
            {goal ? <small>{compactText(goal, 160)}</small> : null}
          </div>
          {subtasks.length ? (
            <div className="hc-agent-task-list hc-plan-subtask-list">
              {subtasks.slice(0, 12).map((task, index) => (
                <article key={`${task.id}:${index}`} data-tone={agentTaskTone(task.status)}>
                  <Circle size={10} />
                  <div>
                    <strong>{task.title}</strong>
                    <small>{[task.agentType ? readableAgentLabel(task.agentType) : "", displayPlanDependencySummary(task.dependencies)].filter(Boolean).join(" 路 ")}</small>
                    {task.description ? <p>{compactText(task.description, 180)}</p> : null}
                  </div>
                  <em>{task.status ? formatAgentTaskStatus(task.status) : displayPlanTaskHandle(task.id, task.agentType, index)}</em>
                </article>
              ))}
            </div>
          ) : null}
        </div>
      ) : null}
    </section>
  );
});

export const CleanAskUserQuestionBlock = memo(function CleanAskUserQuestionBlock({
  message,
  onCopyRuntimeText,
  onSubmitUserQuestionAnswer,
  busy,
  answered,
}: {
  message: SessionWorkspaceMessage;
  onCopyRuntimeText?: (label: string, text: string) => void | Promise<void>;
  onSubmitUserQuestionAnswer?: (message: SessionWorkspaceMessage, answer: string) => void | Promise<void>;
  busy?: boolean;
  answered?: boolean;
}) {
  const [answer, setAnswer] = useState("");
  const question =
    readMetadataString(message, ["question", "prompt", "summary", "message", "description"]) ||
    message.content.trim() ||
    "需要你补充信息";
  const options = readMetadataList(message, ["options", "choices"]);
  const canSubmit = Boolean(onSubmitUserQuestionAnswer) && !answered;
  const submitAnswer = (text: string) => {
    const trimmed = text.trim();
    if (!trimmed || !onSubmitUserQuestionAnswer || answered) return;
    void onSubmitUserQuestionAnswer(message, trimmed);
    setAnswer("");
  };
  return (
    <section className="hc-question-event">
      <header>
        <HelpCircle size={16} />
        <div>
          <strong>需要你确认</strong>
          <span>{question}</span>
        </div>
        <StatusChip status={answered ? "answered" : readMetadataString(message, ["status"]) || message.status || "waiting"} />
      </header>
      {options.length ? (
        <div className="hc-question-options">
          {options.slice(0, 4).map((option, index) => {
            const record = option && typeof option === "object" ? option as Record<string, unknown> : null;
            const label = record ? readString(record.label ?? record.value ?? record.title) : readString(option);
            const description = record ? readString(record.description ?? record.detail) : "";
            const value = record ? readString(record.value ?? record.label ?? record.title) : label;
            const answerText = [
              `选择：${label || value || `选项 ${index + 1}`}`,
              value && value !== label ? `值：${value}` : "",
              description ? `说明：${description}` : "",
            ].filter(Boolean).join("\n");
            return (
              <button
                type="button"
                key={`${label}:${index}`}
                disabled={!canSubmit || busy || answered}
                onClick={() => submitAnswer(answerText)}
              >
                <strong>{label || `选项 ${index + 1}`}</strong>
                {description ? <span>{description}</span> : null}
                <em>{canSubmit ? (busy ? "提交中..." : "点击提交这个回答") : "等待回答提交接口"}</em>
              </button>
            );
          })}
        </div>
      ) : null}
      <div className="hc-question-reply">
        <textarea
          value={answer}
          disabled={!canSubmit || busy || answered}
          rows={3}
          placeholder={canSubmit ? "补充说明或直接回答..." : "等待回答提交接口"}
          onChange={(event) => setAnswer(event.target.value)}
        />
        <button
          type="button"
          disabled={!canSubmit || busy || answered || !answer.trim()}
          onClick={() => submitAnswer(answer)}
        >
          <SendHorizontal size={13} />
          {busy ? "提交中" : "提交回答"}
        </button>
      </div>
      <footer>
        <button type="button" onClick={() => void onCopyRuntimeText?.("待确认问题", question)}>
          <Copy size={13} />复制问题
        </button>
      </footer>
    </section>
  );
});

export const CleanComputerUsePermissionBlock = memo(function CleanComputerUsePermissionBlock({
  message,
  onCopyRuntimeText,
  onApprove,
  onApproveAlways,
  onReject,
  busyId,
}: {
  message: SessionWorkspaceMessage;
  onCopyRuntimeText?: (label: string, text: string) => void | Promise<void>;
  onApprove?: (approvalId: string) => void | Promise<void>;
  onApproveAlways?: (approvalId: string) => void | Promise<void>;
  onReject?: (approvalId: string) => void | Promise<void>;
  busyId?: string | null;
}) {
  const approvalId = readMetadataString(message, ["approvalId", "requestId"]);
  const resolved = message.metadata?.resolved === true;
  const decision = readMetadataString(message, ["decision"]);
  const busy = Boolean(approvalId && busyId === approvalId);
  const canAlwaysAllow = Boolean(onApproveAlways);
  const appName = readMetadataString(message, ["app", "application", "target", "windowTitle"]);
  const action = readMetadataString(message, ["action", "permission", "summary", "description"]) || message.content.trim();
  const selector = readMetadataString(message, ["selector"]);
  const x = readMetadataString(message, ["x"]);
  const y = readMetadataString(message, ["y"]);
  const coordinates = x && y ? `${x}, ${y}` : "";
  const text = readMetadataString(message, ["text"]);
  const direction = readMetadataString(message, ["direction"]);
  const amount = readMetadataString(message, ["amount"]);
  const scroll = direction || amount ? [direction || "down", amount].filter(Boolean).join(" ") : "";
  const url = readMetadataString(message, ["url"]);
  const page = readMetadataString(message, ["pageId", "browserContextId"]);
  const permission = readMetadataString(message, ["permission", "summary", "description"]);
  const detailText = readMetadataString(message, ["details", "reason", "risk"]);
  const previewRows = readMetadataPreviewRows(message, ["previewRows", "preview"]);
  const fallbackRows = [
    { label: "应用", value: appName },
    { label: "动作", value: action },
    { label: "目标", value: selector || readMetadataString(message, ["target"]) },
    { label: "坐标", value: coordinates },
    { label: "文本", value: text },
    { label: "滚动", value: scroll },
    { label: "URL", value: url },
    { label: "Page", value: page },
    { label: "权限", value: permission },
    { label: "风险", value: detailText },
  ].filter((row) => row.value);
  const rows = (previewRows.length ? previewRows : fallbackRows).slice(0, 8);
  const summary = resolved
    ? `已${decision === "rejected" ? "拒绝" : "允许"}`
    : ([appName, action].filter(Boolean).join(" · ") || "等待授权详情");
  const hiddenDetailKeys = new Set([
    "kind",
    "resolved",
    "decision",
    "status",
    "request",
    "preview",
    "previewRows",
    "approvalId",
    "requestId",
    "app",
    "application",
    "target",
    "windowTitle",
    "action",
    "permission",
    "summary",
    "description",
    "details",
    "reason",
    "risk",
    "selector",
    "x",
    "y",
    "text",
    "direction",
    "amount",
    "url",
    "pageId",
    "browserContextId",
  ]);
  const rawDetails = Object.entries(message.metadata ?? {})
    .filter(([key, value]) => (
      !hiddenDetailKeys.has(key) &&
      value !== undefined &&
      value !== null &&
      typeof value !== "object"
    ))
    .map(([key, value]) => `${key}: ${String(value)}`)
    .join("\n");
  const details = [
    ...rows.map((row) => `${row.label}: ${row.value}`),
    detailText && !rows.some((row) => row.value === detailText) ? `详情: ${detailText}` : "",
    message.content.trim() && message.content.trim() !== action ? message.content.trim() : "",
    rawDetails,
  ].filter(Boolean).join("\n");
  return (
    <section className="hc-computer-event" data-resolved={resolved ? "true" : "false"}>
      <header>
        <MousePointerClick size={16} />
        <div>
          <strong>Computer Use 权限</strong>
          <span>{summary}</span>
        </div>
        <StatusChip status={resolved ? (decision === "rejected" ? "rejected" : "approved") : readMetadataString(message, ["status"]) || message.status || "waiting"} />
      </header>
      {rows.length ? (
        <dl className="hc-computer-preview" aria-label="Computer Use 权限详情">
          {rows.map((row) => (
            <div key={`${row.label}:${row.value}`}>
              <dt>{row.label}</dt>
              <dd>{row.value}</dd>
            </div>
          ))}
        </dl>
      ) : null}
      {rawDetails ? <pre>{rawDetails}</pre> : null}
      <footer>
        <button type="button" onClick={() => void onCopyRuntimeText?.("Computer Use 权限", details)}>
          <Copy size={13} />复制详情
        </button>
        {!resolved && approvalId ? (
          <>
            <button type="button" data-variant="allow" disabled={busy} onClick={() => void onApprove?.(approvalId)}>
              {busy ? "提交中" : "允许"}
            </button>
            <button
              type="button"
              data-variant="always"
              disabled={busy || !canAlwaysAllow}
              title={canAlwaysAllow ? "以后同类 Computer Use 操作不再询问" : "此审批暂不支持始终允许"}
              onClick={() => void onApproveAlways?.(approvalId)}
            >
              始终允许
            </button>
            <button type="button" data-variant="deny" disabled={busy} onClick={() => void onReject?.(approvalId)}>
              拒绝
            </button>
          </>
        ) : null}
      </footer>
    </section>
  );
});

function RuntimeIcon({ item }: { item: RuntimeTimelineItem }) {
  if (item.kind === "patch") return <FileDiff size={15} />;
  if (item.kind === "command") return <TerminalSquare size={15} />;
  if (item.kind === "approval") return <CircleAlert size={15} />;
  if (item.kind === "tool") return <Wrench size={15} />;
  if (statusTone(item.status) === "success") return <CheckCircle2 size={15} />;
  return <Circle size={15} />;
}

function shouldExpandByDefault(item: RuntimeTimelineItem) {
  if (item.kind === "approval" && ["pending", "waiting", "waiting_approval", "queued"].includes(item.status?.toLowerCase() ?? "")) {
    return true;
  }
  if (item.kind === "patch") return false;
  return false;
}

function StatusChip({ status }: { status?: string }) {
  return <em className="hc-status-chip" data-tone={statusTone(status)}>{statusLabel(status)}</em>;
}

type DiffGroup = {
  oldPath: string;
  newPath: string;
  lines: ReturnType<typeof parseUnifiedDiff>;
};

function diffGroupPath(group: DiffGroup, fallback = "diff") {
  return normalizeDiffPath(group.newPath || group.oldPath || fallback);
}

function diffGroupTitle(group: DiffGroup, index: number) {
  return diffGroupPath(group, `diff ${index + 1}`);
}

function diffLineStats(lines: DiffGroup["lines"]) {
  return lines.reduce(
    (stats, line) => {
      if (line.type === "add") return { ...stats, additions: stats.additions + 1 };
      if (line.type === "remove") return { ...stats, deletions: stats.deletions + 1 };
      return stats;
    },
    { additions: 0, deletions: 0 },
  );
}

function diffGroupToText(group: DiffGroup) {
  return group.lines
    .map((line) => {
      if (line.type === "add") return `+${line.content}`;
      if (line.type === "remove") return `-${line.content}`;
      if (line.type === "context") return ` ${line.content}`;
      return line.content;
    })
    .join("\n");
}

function parseHunkStart(content: string) {
  const match = /^@@\s+-(\d+)(?:,\d+)?\s+\+(\d+)(?:,\d+)?/.exec(content);
  if (!match) return null;
  return { oldLine: Number(match[1]), newLine: Number(match[2]) };
}

function diffRows(lines: DiffGroup["lines"]) {
  let oldLine = 0;
  let newLine = 0;
  return lines.map((line) => {
    const hunkStart = line.type === "header" ? parseHunkStart(line.content) : null;
    if (hunkStart) {
      oldLine = hunkStart.oldLine;
      newLine = hunkStart.newLine;
    }

    if (line.type === "add") {
      const row = { line, oldNumber: "", newNumber: newLine ? String(newLine) : "", targetLine: newLine || null };
      newLine += 1;
      return row;
    }
    if (line.type === "remove") {
      const row = { line, oldNumber: oldLine ? String(oldLine) : "", newNumber: "", targetLine: null };
      oldLine += 1;
      return row;
    }
    if (line.type === "context") {
      const row = {
        line,
        oldNumber: oldLine ? String(oldLine) : "",
        newNumber: newLine ? String(newLine) : "",
        targetLine: newLine || null,
      };
      if (oldLine) oldLine += 1;
      if (newLine) newLine += 1;
      return row;
    }
    return { line, oldNumber: "", newNumber: "", targetLine: null };
  });
}

function DiffPreview({
  item,
  selectedPath,
  onSelectPath,
  onCopyRuntimeText,
  onOpenFile,
}: {
  item: RuntimeTimelineItem;
  selectedPath?: string | null;
  onSelectPath?: (path: string) => void;
  onCopyRuntimeText?: (label: string, text: string) => void | Promise<void>;
  onOpenFile?: (path: string) => void;
}) {
  const selected = selectedPath ? normalizeDiffPath(selectedPath) : "";
  const allGroups: DiffGroup[] = item.diffLines?.length
    ? [{ oldPath: "", newPath: selected || "diff", lines: item.diffLines }]
    : splitDiffText(item.rawDetail || "");
  if (!allGroups.length) return null;
  const selectedIndex = Math.max(0, allGroups.findIndex((group) => {
    const paths = [group.newPath, group.oldPath].map(normalizeDiffPath);
    return selected ? paths.includes(selected) : false;
  }));
  const group = allGroups[selectedIndex] ?? allGroups[0];
  const rows = diffRows(group.lines);
  const visibleRows = rows.slice(0, 180);
  const overflowRows = Math.max(0, rows.length - visibleRows.length);
  const stats = diffLineStats(group.lines);
  const title = diffGroupTitle(group, selectedIndex);
  const changedPath = group.oldPath && group.newPath && normalizeDiffPath(group.oldPath) !== normalizeDiffPath(group.newPath)
    ? `${normalizeDiffPath(group.oldPath)} -> ${normalizeDiffPath(group.newPath)}`
    : "";
  return (
    <div className="hc-diff-preview">
      {allGroups.length > 1 ? (
        <div className="hc-diff-tabs" aria-label="差异文件">
          {allGroups.slice(0, 8).map((entry, index) => {
            const path = diffGroupPath(entry, `diff ${index + 1}`);
            const entryStats = diffLineStats(entry.lines);
            return (
              <button
                type="button"
                key={`${path}:${index}`}
                aria-pressed={index === selectedIndex}
                onClick={() => {
                  onSelectPath?.(path);
                  onOpenFile?.(path);
                }}
              >
                <code>{path}</code>
                <span>+{entryStats.additions} -{entryStats.deletions}</span>
              </button>
            );
          })}
          {allGroups.length > 8 ? <em>+{allGroups.length - 8}</em> : null}
        </div>
      ) : null}
      <section className="hc-diff-file">
        <header>
          <div>
            <code>{title}</code>
            {changedPath ? <small>{changedPath}</small> : null}
          </div>
          <div className="hc-diff-file-meta">
            <span data-kind="add">+{stats.additions}</span>
            <span data-kind="remove">-{stats.deletions}</span>
            <span>{group.lines.length} 行</span>
            {onCopyRuntimeText ? (
              <button type="button" onClick={() => void onCopyRuntimeText("文件差异", diffGroupToText(group))}>
                <Copy size={12} />复制
              </button>
            ) : null}
          </div>
        </header>
        <ol>
          {visibleRows.map(({ line, oldNumber, newNumber, targetLine }, index) => {
            const sourcePath = normalizeDiffPath(group.newPath || group.oldPath || selectedPath || "");
            const targetPath = sourcePath && targetLine ? `${sourcePath}:${targetLine}` : "";
            return (
              <li key={`${index}:${line.content}`} data-type={line.type}>
                <span className="hc-diff-sign">{line.type === "add" ? "+" : line.type === "remove" ? "-" : line.type === "header" ? "@" : " "}</span>
                <span className="hc-diff-old-line">{oldNumber}</span>
                {targetPath ? (
                  <button
                    type="button"
                    className="hc-diff-new-line"
                    title={`在右侧打开第 ${targetLine} 行`}
                    onClick={() => onOpenFile?.(targetPath)}
                  >
                    {newNumber}
                  </button>
                ) : (
                  <span className="hc-diff-new-line">{newNumber}</span>
                )}
                <code>{line.content || " "}</code>
              </li>
            );
          })}
          {overflowRows ? (
            <li data-type="header">
              <span className="hc-diff-sign">@</span>
              <span className="hc-diff-old-line" />
              <span className="hc-diff-new-line" />
              <code>已折叠 {overflowRows} 行，复制可获取当前文件完整差异</code>
            </li>
          ) : null}
        </ol>
      </section>
    </div>
  );
}

function ApprovalRuntimeBlock({
  item,
  onApprove,
  onApproveAlways,
  onReject,
  onLoadPatch,
  onCopyRuntimeText,
  onOpenFile,
  busyId,
}: {
  item: RuntimeTimelineItem;
  onApprove?: (approvalId: string) => void | Promise<void>;
  onApproveAlways?: (approvalId: string) => void | Promise<void>;
  onReject?: (approvalId: string) => void | Promise<void>;
  onLoadPatch?: (patchId: string) => void | Promise<void>;
  onCopyRuntimeText?: (label: string, text: string) => void | Promise<void>;
  onOpenFile?: (path: string) => void;
  busyId?: string | null;
}) {
  const kind = approvalKind(item);
  const structured = kind === "plan" || Boolean(item.completionEvidence);
  const [expanded, setExpanded] = useState(() => shouldExpandByDefault(item) && !structured);
  const files = useMemo(() => approvalFileSummaries(item), [item]);
  const hasDiff = Boolean(item.diffLines?.length || item.rawDetail?.includes("diff --git"));
  const statusForDiagnostics = item.status?.toLowerCase() ?? "";
  const diagnosticOutput = !hasDiff && ["failed", "error", "blocked", "rejected"].includes(statusForDiagnostics)
    ? [item.rawDetail, item.code].find((value) => value && !looksLikeInternalPayloadText(value)) ?? ""
    : "";
  const output = hasDiff ? item.rawDetail || "" : diagnosticOutput;
  const canApprove = Boolean(
    item.sourceId &&
      ["pending", "waiting", "waiting_approval", "queued"].includes(item.status?.toLowerCase() ?? ""),
  );
  const canAlwaysAllow = Boolean(onApproveAlways && item.supportsAlwaysAllow !== false);
  const busy = Boolean(item.sourceId && busyId === item.sourceId);
  const approvalStatus = item.status?.toLowerCase() ?? "";
  const approvalEyebrow = canApprove
    ? "需要确认"
    : approvalStatus === "approved"
      ? "已批准"
      : approvalStatus === "rejected"
        ? "已拒绝"
        : "已处理";

  return (
    <section className="hc-runtime hc-approval" data-tone={statusTone(item.status)} data-risky={item.riskLevel ?? "medium"}>
      <header className="hc-approval-head">
        <div>
          <span className="hc-runtime-eyebrow">{approvalEyebrow}</span>
          <strong>{runtimeLabel(item)}</strong>
          {runtimeSummary(item) ? <small>{runtimeSummary(item)}</small> : null}
        </div>
        <StatusChip status={item.status} />
      </header>
      {files.length ? (
        <div className="hc-change-list hc-change-list-compact">
          {files.slice(0, 6).map((file) => (
            <button
              type="button"
              key={file.path}
              onClick={() => {
                onOpenFile?.(file.path);
                if (item.patchId) void onLoadPatch?.(item.patchId);
              }}
            >
              <code>{file.path}</code>
              <span>{[patchStatusLabel(file.status), file.additions !== undefined ? `+${file.additions}` : "", file.deletions !== undefined ? `-${file.deletions}` : ""].filter(Boolean).join(" ")}</span>
            </button>
          ))}
        </div>
      ) : null}
      {kind === "plan" ? <PlanApprovalPreview item={item} /> : null}
      {item.completionEvidence ? <CompletionEvidencePreview item={item} /> : null}
      {item.previewRows?.length ? (
        <dl className="hc-approval-preview" aria-label="审批预览">
          {item.previewRows.slice(0, 5).map((row) => (
            <div key={`${row.label}:${row.value}`}>
              <dt>{row.label}</dt>
              <dd>{row.value}</dd>
            </div>
          ))}
        </dl>
      ) : null}
      {canApprove ? (
        <div className="hc-approval-actions">
          <button type="button" data-variant="allow" disabled={busy} onClick={() => void onApprove?.(item.sourceId ?? "")}>允许一次</button>
          <button type="button" data-variant="deny" disabled={busy} onClick={() => void onReject?.(item.sourceId ?? "")}>拒绝</button>
          <button
            type="button"
            data-variant="always"
            disabled={busy || !canAlwaysAllow}
            title={canAlwaysAllow ? "以后同类操作不再询问" : "此审批暂不支持始终允许"}
            onClick={() => void onApproveAlways?.(item.sourceId ?? "")}
          >
            始终允许
          </button>
        </div>
      ) : null}
      {output || hasDiff ? (
        <button type="button" className="hc-diff-toggle" onClick={() => setExpanded((open) => !open)}>
          {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
          {expanded ? "收起详情" : hasDiff ? "查看差异" : "查看详情"}
        </button>
      ) : null}
      {expanded && hasDiff ? <DiffPreview item={item} onCopyRuntimeText={onCopyRuntimeText} onOpenFile={onOpenFile} /> : null}
      {expanded && output && !hasDiff ? (
        <figure className="hc-runtime-output">
          <figcaption>
            <span>审批详情</span>
            <button type="button" onClick={() => void onCopyRuntimeText?.("审批详情", output)}>
              <Copy size={13} />复制
            </button>
          </figcaption>
          <pre>{output}</pre>
        </figure>
      ) : null}
    </section>
  );
}

function PatchRuntimeBlock({
  item,
  onLoadPatch,
  onCopyRuntimeText,
  onOpenFile,
  onQuoteMessage,
  onRevertTaskChanges,
  busyId,
}: {
  item: RuntimeTimelineItem;
  onLoadPatch?: (patchId: string) => void | Promise<void>;
  onCopyRuntimeText?: (label: string, text: string) => void | Promise<void>;
  onOpenFile?: (path: string) => void;
  onQuoteMessage?: (text: string) => void;
  onRevertTaskChanges?: (taskId: string) => void | Promise<void>;
  busyId?: string | null;
}) {
  const files = useMemo(() => patchFileSummaries(item), [item]);
  const diffGroups = useMemo(() => (
    item.diffLines?.length
      ? [{ oldPath: "", newPath: files[0]?.path ?? "diff", lines: item.diffLines }]
      : splitDiffText(item.rawDetail || "")
  ), [files, item.diffLines, item.rawDetail]);
  const diffPaths = useMemo(() => new Set(diffGroups.flatMap((group) => [group.newPath, group.oldPath].map(normalizeDiffPath)).filter(Boolean)), [diffGroups]);
  const totalAdditions = files.reduce((sum, file) => sum + (file.additions ?? 0), 0);
  const totalDeletions = files.reduce((sum, file) => sum + (file.deletions ?? 0), 0);
  const hasDiff = diffGroups.length > 0;
  const [expanded, setExpanded] = useState(false);
  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  const selectedDiffPath = selectedPath && diffPaths.has(normalizeDiffPath(selectedPath)) ? selectedPath : null;
  const fileListText = patchFileListText(files);
  const patchStatus = item.status?.toLowerCase();
  const canRevert = Boolean(item.taskId && onRevertTaskChanges && patchStatus === "applied");
  const revertBusy = Boolean(item.taskId && busyId === `revert:${item.taskId}`);

  return (
    <section className="hc-runtime hc-patch" data-kind={item.kind} data-tone={statusTone(item.status)}>
      <header className="hc-patch-head">
        <div>
          <span className="hc-runtime-eyebrow">改动</span>
          <strong>{runtimeLabel(item)}</strong>
          {runtimeSummary(item) ? <small>{runtimeSummary(item)}</small> : null}
        </div>
        <div className="hc-patch-side">
          <div className="hc-patch-meta">
            {files.length ? <span>{files.length} 个文件</span> : null}
            {totalAdditions || totalDeletions ? <span><b>+{totalAdditions}</b> <i>-{totalDeletions}</i></span> : null}
            <StatusChip status={item.status} />
          </div>
          <div className="hc-patch-actions" aria-label="改动操作">
            <button
              type="button"
              disabled={!files.length || !onCopyRuntimeText}
              onClick={() => void onCopyRuntimeText?.("改动文件列表", fileListText)}
            >
              <Copy size={13} />
              复制文件列表
            </button>
            <button
              type="button"
              disabled={!canRevert || revertBusy}
              title={canRevert ? "反向应用这轮已保存的 patch diff" : patchStatus === "reverted" ? "这轮改动已撤销" : "没有可撤销的已应用 patch"}
              onClick={() => item.taskId && void onRevertTaskChanges?.(item.taskId)}
            >
              <RotateCcw size={13} />
              {revertBusy ? "撤销中" : "撤销本轮"}
            </button>
            <button
              type="button"
              disabled={!onQuoteMessage}
              title={onQuoteMessage ? "写入输入框，继续审查这轮改动" : "需要先接入 composer 引用桥接"}
              onClick={() => onQuoteMessage?.(patchReviewPrompt(item, files))}
            >
              <ScanSearch size={13} />
              审查改动
            </button>
          </div>
        </div>
      </header>
      {files.length ? (
        <div className="hc-change-list hc-change-list-compact">
          {files.map((file) => (
            <button
              type="button"
              key={file.path}
              data-selected={selectedDiffPath === file.path ? "true" : "false"}
              onClick={() => {
                onOpenFile?.(file.path);
                if (diffPaths.has(normalizeDiffPath(file.path))) {
                  setSelectedPath(file.path);
                  setExpanded(true);
                  return;
                }
                if (item.sourceId) void onLoadPatch?.(item.sourceId);
              }}
            >
              <code>{file.path}</code>
              <span>
                {[patchStatusLabel(file.status), file.additions !== undefined ? `+${file.additions}` : "", file.deletions !== undefined ? `-${file.deletions}` : ""].filter(Boolean).join(" ")}
                {diffPaths.has(normalizeDiffPath(file.path)) ? " · 本地差异" : ""}
              </span>
            </button>
          ))}
        </div>
      ) : null}
      {hasDiff || item.sourceId ? (
        <button type="button" className="hc-diff-toggle" onClick={() => {
          if (!hasDiff && item.sourceId) void onLoadPatch?.(item.sourceId);
          setSelectedPath(null);
          setExpanded((open) => !open);
        }}>
          {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
          {expanded ? "收起差异" : selectedDiffPath ? "查看所选差异" : "查看全部差异"}
        </button>
      ) : null}
      {expanded ? (
        <DiffPreview
          item={item}
          selectedPath={selectedDiffPath}
          onSelectPath={setSelectedPath}
          onCopyRuntimeText={onCopyRuntimeText}
          onOpenFile={onOpenFile}
        />
      ) : null}
    </section>
  );
}

export const CleanRuntimeBlock = memo(function CleanRuntimeBlock({
  item,
  onApprove,
  onApproveAlways,
  onReject,
  onLoadPatch,
  onCopyRuntimeText,
  onOpenFile,
  onQuoteMessage,
  onRevertTaskChanges,
  onContinueFromMessage,
  onBranchFromMessage,
  onDeleteMessage,
  onRefreshCommandJob,
  onStopCommandJob,
  busyId,
}: {
  item: RuntimeTimelineItem;
  onApprove?: (approvalId: string) => void | Promise<void>;
  onApproveAlways?: (approvalId: string) => void | Promise<void>;
  onReject?: (approvalId: string) => void | Promise<void>;
  onLoadPatch?: (patchId: string) => void | Promise<void>;
  onCopyRuntimeText?: (label: string, text: string) => void | Promise<void>;
  onOpenFile?: (path: string) => void;
  onQuoteMessage?: (text: string) => void;
  onRevertTaskChanges?: (taskId: string) => void | Promise<void>;
  onContinueFromMessage?: (message: SessionWorkspaceMessage) => void | Promise<void>;
  onBranchFromMessage?: (message: SessionWorkspaceMessage) => void | Promise<void>;
  onDeleteMessage?: (message: SessionWorkspaceMessage) => void | Promise<void>;
  onRefreshCommandJob?: (commandId: string) => void | Promise<void>;
  onStopCommandJob?: (commandId: string) => void | Promise<void>;
  busyId?: string | null;
}) {
  const [expanded, setExpanded] = useState(shouldExpandByDefault(item));
  const files = useMemo(() => patchFileSummaries(item), [item]);
  const normalizedStatus = item.status?.toLowerCase() ?? "";
  const needsDiagnostics = ["failed", "error", "blocked", "cancelled", "rejected"].includes(normalizedStatus);
  const hasRuntimeDiff = Boolean(item.diffLines?.length || item.rawDetail?.includes("diff --git"));
  const output = item.kind === "command"
    ? buildCommandOutput(item)
    : hasRuntimeDiff
      ? item.rawDetail || ""
      : needsDiagnostics
        ? [item.rawDetail, item.code].find((value) => value && !looksLikeInternalPayloadText(value)) ?? ""
        : "";
  const tone = statusTone(item.status);
  const busy = Boolean(item.sourceId && busyId === item.sourceId);
  const canApprove = item.kind === "approval" && item.sourceId && ["pending", "waiting", "waiting_approval", "queued"].includes(item.status?.toLowerCase() ?? "");
  const canAlwaysAllow = Boolean(onApproveAlways && item.supportsAlwaysAllow !== false);
  const canStop = item.kind === "command" && item.sourceId && isInFlight(item.status) && onStopCommandJob;
  const canRefresh = item.kind === "command" && item.sourceId && onRefreshCommandJob;
  const risky = item.kind === "approval" || item.riskLevel === "medium" || item.riskLevel === "high";
  const outputLabel = item.kind === "command" ? "Shell 输出" : "工具详情";

  if (item.kind === "approval") {
    return (
      <ApprovalRuntimeBlock
        item={item}
        onApprove={onApprove}
        onApproveAlways={onApproveAlways}
        onReject={onReject}
        onLoadPatch={onLoadPatch}
        onCopyRuntimeText={onCopyRuntimeText}
        onOpenFile={onOpenFile}
        busyId={busyId}
      />
    );
  }

  if (item.kind === "patch") {
    return (
      <PatchRuntimeBlock
        item={item}
        onLoadPatch={onLoadPatch}
        onCopyRuntimeText={onCopyRuntimeText}
        onOpenFile={onOpenFile}
        onQuoteMessage={onQuoteMessage}
        onRevertTaskChanges={onRevertTaskChanges}
        busyId={busyId}
      />
    );
  }

  if (isQuietRuntime(item)) {
    return (
      <section className="hc-runtime-inline" data-tone={tone}>
        <RuntimeIcon item={item} />
        <strong>{runtimeLabel(item)}</strong>
        <span>{runtimeSummary(item)}</span>
        <StatusChip status={item.status} />
      </section>
    );
  }

  return (
    <section className="hc-runtime" data-kind={item.kind} data-tone={tone} data-risky={risky ? "true" : "false"}>
      <button type="button" className="hc-runtime-head" aria-expanded={expanded} onClick={() => setExpanded((open) => !open)}>
        {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        <RuntimeIcon item={item} />
        <strong>{runtimeLabel(item)}</strong>
        <span>{runtimeSummary(item)}</span>
        <StatusChip status={item.status} />
        {formatDuration(item.durationMs) ? <time>{formatDuration(item.durationMs)}</time> : formatClock(item.time) ? <time>{formatClock(item.time)}</time> : null}
      </button>
      {expanded ? (
        <div className="hc-runtime-body">
          {canApprove ? (
            <div className="hc-approval-actions">
              <button type="button" data-variant="allow" disabled={busy} onClick={() => void onApprove?.(item.sourceId ?? "")}>允许一次</button>
              <button type="button" data-variant="deny" disabled={busy} onClick={() => void onReject?.(item.sourceId ?? "")}>拒绝</button>
              <button
                type="button"
                data-variant="always"
                disabled={busy || !canAlwaysAllow}
                title={canAlwaysAllow ? "以后同类操作不再询问" : "此审批暂不支持始终允许"}
                onClick={() => void onApproveAlways?.(item.sourceId ?? "")}
              >
                始终允许
              </button>
            </div>
          ) : null}
          {files.length ? (
            <div className="hc-change-list">
              {files.map((file) => (
                <button
                  type="button"
                  key={file.path}
                  onClick={() => {
                    onOpenFile?.(file.path);
                    if (item.sourceId) void onLoadPatch?.(item.sourceId);
                  }}
                >
                  <code>{file.path}</code>
                  <span>{[patchStatusLabel(file.status), file.additions !== undefined ? `+${file.additions}` : "", file.deletions !== undefined ? `-${file.deletions}` : ""].filter(Boolean).join(" ")}</span>
                </button>
              ))}
            </div>
          ) : null}
          {output ? (
            <figure className="hc-runtime-output">
              <figcaption>
                <span>{outputLabel}</span>
                <button type="button" onClick={() => void onCopyRuntimeText?.(outputLabel, output)}>
                  <Copy size={13} />复制
                </button>
              </figcaption>
              <pre>{output}</pre>
            </figure>
          ) : null}
          {canRefresh || canStop ? (
            <div className="hc-runtime-actions">
              {canRefresh ? <button type="button" disabled={busy} onClick={() => void onRefreshCommandJob?.(item.sourceId ?? "")}>刷新</button> : null}
              {canStop ? <button type="button" disabled={busy} onClick={() => void onStopCommandJob?.(item.sourceId ?? "")}>停止</button> : null}
            </div>
          ) : null}
        </div>
      ) : null}
    </section>
  );
});

function CleanWorklogRuntimeRow({
  item,
  depth = 0,
  childCount = 0,
  onCopyRuntimeText,
}: {
  item: RuntimeTimelineItem;
  depth?: number;
  childCount?: number;
  onCopyRuntimeText?: (label: string, text: string) => void | Promise<void>;
}) {
  const [expanded, setExpanded] = useState(false);
  const normalizedStatus = item.status?.toLowerCase() ?? "";
  const needsDiagnostics = ["failed", "error", "blocked", "cancelled", "rejected"].includes(normalizedStatus);
  const commandOutput = item.kind === "command" ? buildCommandOutput(item) : "";
  const output = item.kind === "command"
    ? commandOutput
    : needsDiagnostics
      ? [item.rawDetail, item.code].find((value) => value && !looksLikeInternalPayloadText(value)) ?? ""
      : "";
  const summary = runtimeSummary(item);
  const detail = output || (needsDiagnostics ? summary : "");
  const kindName = runtimeKindName(item);
  return (
    <article className="hc-worklog-row" data-tone={statusTone(item.status)} data-kind={kindName} data-quiet={isQuietRuntime(item) ? "true" : "false"} data-depth={depth}>
      <button type="button" aria-expanded={expanded} onClick={() => setExpanded((open) => !open)}>
        {expanded ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
        <RuntimeIcon item={item} />
        <em className="hc-worklog-kind">{runtimeGroupLabel(item)}</em>
        <strong>{runtimeLabel(item)}</strong>
        {summary ? <span>{summary}</span> : null}
        {childCount ? <small>{childCount} 个子步骤</small> : null}
        <StatusChip status={item.status} />
        {formatDuration(item.durationMs) ? <time>{formatDuration(item.durationMs)}</time> : null}
      </button>
      {expanded && item.previewRows?.length ? (
        <dl className="hc-tool-preview" aria-label="工具结果预览">
          {item.previewRows.slice(0, 5).map((row) => (
            <div key={`${row.label}:${row.value}`}>
              <dt>{row.label}</dt>
              <dd>{row.value}</dd>
            </div>
          ))}
        </dl>
      ) : null}
      {expanded && detail ? (
        <figure className="hc-worklog-detail">
          <figcaption>
            <span>{item.kind === "command" ? "Shell" : "详情"}</span>
            <button type="button" onClick={() => void onCopyRuntimeText?.("工具详情", detail)}>
              <Copy size={12} />复制
            </button>
          </figcaption>
          <pre>{detail}</pre>
        </figure>
      ) : null}
    </article>
  );
}

function CleanWorklogPhaseRoot({ phase }: { phase: WorklogPhase }) {
  const tone = worklogPhaseTone(phase.nodes);
  const childTotal = phase.nodes.reduce((total, node) => total + node.children.length, 0);
  const runningCount = phase.nodes.filter((node) => isInFlight(node.item.status)).length;
  const failedCount = phase.nodes.filter((node) => statusTone(node.item.status) === "danger").length;
  const rootCount = phase.nodes.length;
  const summary = failedCount ? `${failedCount} 个异常` : runningCount ? `${runningCount} 个进行中` : childTotal ? `包含 ${childTotal} 个子步骤` : "语义阶段";
  return (
    <div className="hc-worklog-phase-root" data-tone={tone}>
      <span>
        <ListChecks size={13} />
        <strong>{phase.label}</strong>
      </span>
      <em>{rootCount} 项</em>
      <small>{summary}</small>
    </div>
  );
}

export function CleanWorklogBlock({
  items,
  onCopyRuntimeText,
}: {
  items: RuntimeTimelineItem[];
  onCopyRuntimeText?: (label: string, text: string) => void | Promise<void>;
}) {
  const [expanded, setExpanded] = useState(false);
  const quietCount = items.filter(isQuietRuntime).length;
  const importantItems = items.filter((item) => !isQuietRuntime(item));
  const tree = useMemo(() => buildWorklogTree(items), [items]);
  const flatTree = useMemo(() => flattenWorklogTree(tree), [tree]);
  const phases = useMemo(() => worklogPhaseEntries(tree), [tree]);
  const groupCounts = useMemo(() => worklogGroupCounts(items), [items]);
  const collapsedImportant = importantItems.length
    ? flatTree.filter((node) => !isQuietRuntime(node.item)).slice(0, 3)
    : [];
  const visible = expanded ? flatTree : collapsedImportant;
  const digest = worklogDigest(items, quietCount);
  const narrative = worklogNarrative(items);
  const summaryText = worklogSummaryText(items);
  const runningCount = items.filter((item) => isInFlight(item.status)).length;
  const failedCount = items.filter((item) => statusTone(item.status) === "danger").length;
  return (
    <section className="hc-worklog" data-expanded={expanded ? "true" : "false"}>
      <button type="button" className="hc-worklog-head" aria-expanded={expanded} onClick={() => setExpanded((open) => !open)}>
        {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        <span>已处理 {items.length} 项操作</span>
        <em>{expanded ? narrative : digest}</em>
        <small>
          {runningCount ? `${runningCount} 进行中` : failedCount ? `${failedCount} 失败` : quietCount ? `${quietCount} 已收起` : "过程日志"}
        </small>
      </button>
      <div className="hc-worklog-groups" aria-label="操作类别">
        {groupCounts.slice(0, 5).map((group) => (
          <span key={group.label} data-kind={group.tone}>
            {group.label}
            <b>{group.count}</b>
          </span>
        ))}
      </div>
      {expanded ? (
        <div className="hc-worklog-actions">
          <button
            type="button"
            disabled={!onCopyRuntimeText}
            onClick={() => void onCopyRuntimeText?.("工作日志摘要", summaryText)}
          >
            <Copy size={12} />
            复制摘要
          </button>
        </div>
      ) : null}
      {expanded ? (
        <div className="hc-worklog-phases">
          {phases.map((phase) => (
            <section className="hc-worklog-phase" key={phase.id} aria-label={`阶段：${phase.label}`}>
              <header>
                <span>{phase.label}</span>
                <small>{phase.nodes.length} 项</small>
              </header>
              <CleanWorklogPhaseRoot phase={phase} />
              <div className="hc-worklog-list">
                {flattenWorklogTree(phase.nodes).map((node) => (
                  <CleanWorklogRuntimeRow
                    key={node.item.id}
                    item={node.item}
                    depth={node.depth}
                    childCount={node.children.length}
                    onCopyRuntimeText={onCopyRuntimeText}
                  />
                ))}
              </div>
            </section>
          ))}
        </div>
      ) : (
        <div className="hc-worklog-list">
          {visible.map((node) => (
            <CleanWorklogRuntimeRow
              key={node.item.id}
              item={node.item}
              depth={node.depth}
              childCount={node.children.length}
              onCopyRuntimeText={onCopyRuntimeText}
            />
          ))}
        </div>
      )}
      {!expanded && importantItems.length > visible.length ? (
        <button type="button" className="hc-show-more" onClick={() => setExpanded(true)}>
          展开另外 {importantItems.length - visible.length} 项
        </button>
      ) : null}
    </section>
  );
}

export function CleanActivityItem({
  item,
  onApprove,
  onApproveAlways,
  onReject,
  onLoadPatch,
  onCopyRuntimeText,
  onOpenFile,
  onQuoteMessage,
  onRevertTaskChanges,
  onContinueFromMessage,
  onBranchFromMessage,
  onDeleteMessage,
  onRefreshCommandJob,
  onStopCommandJob,
  busyId,
  onSubmitUserQuestionAnswer,
  answeredQuestionIds,
}: {
  item: ConversationActivityItem;
  onApprove?: (approvalId: string) => void | Promise<void>;
  onApproveAlways?: (approvalId: string) => void | Promise<void>;
  onReject?: (approvalId: string) => void | Promise<void>;
  onLoadPatch?: (patchId: string) => void | Promise<void>;
  onCopyRuntimeText?: (label: string, text: string) => void | Promise<void>;
  onOpenFile?: (path: string) => void;
  onQuoteMessage?: (text: string) => void;
  onRevertTaskChanges?: (taskId: string) => void | Promise<void>;
  onContinueFromMessage?: (message: SessionWorkspaceMessage) => void | Promise<void>;
  onBranchFromMessage?: (message: SessionWorkspaceMessage) => void | Promise<void>;
  onDeleteMessage?: (message: SessionWorkspaceMessage) => void | Promise<void>;
  onRefreshCommandJob?: (commandId: string) => void | Promise<void>;
  onStopCommandJob?: (commandId: string) => void | Promise<void>;
  busyId?: string | null;
  onSubmitUserQuestionAnswer?: (message: SessionWorkspaceMessage, answer: string) => void | Promise<void>;
  answeredQuestionIds?: Set<string>;
}) {
  const transcriptKind = transcriptKindForActivity(item);
  if (item.kind === "runtime") {
    return (
      <div className="hc-activity" data-transcript-kind={transcriptKind}>
        <CleanRuntimeBlock
          item={item.runtime}
          onApprove={onApprove}
          onApproveAlways={onApproveAlways}
          onReject={onReject}
          onLoadPatch={onLoadPatch}
          onCopyRuntimeText={onCopyRuntimeText}
          onOpenFile={onOpenFile}
          onQuoteMessage={onQuoteMessage}
          onRevertTaskChanges={onRevertTaskChanges}
          onRefreshCommandJob={onRefreshCommandJob}
          onStopCommandJob={onStopCommandJob}
          busyId={busyId}
        />
      </div>
    );
  }
  if (item.kind === "worklog") {
    return (
      <div className="hc-activity" data-transcript-kind={transcriptKind}>
        {item.groupKind === "tool_group"
          ? <CleanToolGroupBlock items={item.runtimeItems} onCopyRuntimeText={onCopyRuntimeText} />
          : <CleanWorklogBlock items={item.runtimeItems} onCopyRuntimeText={onCopyRuntimeText} />}
      </div>
    );
  }

  const message = item.message;
  const kind = messageKind(message);
  const wrap = (node: JSX.Element | null) => (node ? <div className="hc-activity" data-transcript-kind={transcriptKind}>{node}</div> : null);
  if (kind === "task_summary" || kind === "plan_update") {
    return null;
  }
  if (kind === "assistant_thinking" || kind === "thinking" || (message.streaming && message.placeholder)) {
    return wrap(<CleanThinkingBlock message={message} />);
  }
  if (kind === "permission_request") {
    return wrap(
      <CleanPermissionMessageBlock
        message={message}
        onApprove={onApprove}
        onApproveAlways={onApproveAlways}
        onReject={onReject}
        busyId={busyId}
      />,
    );
  }
  if (kind === "tool_use" || kind === "tool_result" || kind === "tool_activity") {
    return wrap(<CleanToolMessageBlock message={message} onCopyRuntimeText={onCopyRuntimeText} />);
  }
  if (kind === "ask_user_question") {
    const questionAnswered =
      answeredQuestionIds?.has(message.id) ||
      message.metadata?.resolved === true ||
      message.metadata?.answered === true ||
      message.metadata?.status === "answered";
    return wrap(
      <CleanAskUserQuestionBlock
        message={message}
        onCopyRuntimeText={onCopyRuntimeText}
        onSubmitUserQuestionAnswer={onSubmitUserQuestionAnswer}
        busy={busyId === message.id}
        answered={questionAnswered}
      />,
    );
  }
  if (kind === "slash_command") {
    return wrap(<CleanSlashCommandBlock message={message} onCopyRuntimeText={onCopyRuntimeText} />);
  }
  if (kind === "computer_use_permission" || kind === "computer_use_permission_request") {
    return wrap(
      <CleanComputerUsePermissionBlock
        message={message}
        onCopyRuntimeText={onCopyRuntimeText}
        onApprove={onApprove}
        onApproveAlways={onApproveAlways}
        onReject={onReject}
        busyId={busyId}
      />,
    );
  }
  if (
    [
      "api_retry",
      "agent_task_group",
      "background_task",
      "compact_summary",
      "goal_event",
      "memory_event",
      "slash_command",
      "status",
      "system",
    ].includes(kind) ||
    message.role === "system" ||
    message.kind === "failure" ||
    message.status === "failed"
  ) {
    if ((kind === "background_task" || kind === "agent_task_group") && readAgentGroupTasks(message).length) {
      return wrap(<CleanAgentTaskGroupBlock message={message} />);
    }
    return wrap(<CleanSpecialEventBlock message={message} transcriptKind={transcriptKind} />);
  }
  if (message.role === "user") {
    return wrap(
      <CleanUserMessage
        message={message}
        onCopyRuntimeText={onCopyRuntimeText}
        onQuoteMessage={onQuoteMessage}
        onContinueFromMessage={onContinueFromMessage}
        onBranchFromMessage={onBranchFromMessage}
        onDeleteMessage={onDeleteMessage}
        busy={busyId === message.id}
      />,
    );
  }
  if (message.role === "assistant") {
    return wrap(
      <CleanAssistantMessage
        message={message}
        onCopyRuntimeText={onCopyRuntimeText}
        onQuoteMessage={onQuoteMessage}
        onContinueFromMessage={onContinueFromMessage}
        onBranchFromMessage={onBranchFromMessage}
        onDeleteMessage={onDeleteMessage}
        busy={busyId === message.id}
      />,
    );
  }
  return null;
}
