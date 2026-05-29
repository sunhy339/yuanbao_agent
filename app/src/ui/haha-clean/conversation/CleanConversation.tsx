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
  FileDiff,
  Files,
  HelpCircle,
  History,
  ListChecks,
  MoreHorizontal,
  MousePointerClick,
  RotateCcw,
  TerminalSquare,
  Target,
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
import { stripAssistantRuntimeProgress } from "../../../state/chatMessages";
import { CleanMarkdown } from "../shared/CleanMarkdown";
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

function toolInlineSummary(message: SessionWorkspaceMessage) {
  const input =
    message.metadata?.input && typeof message.metadata.input === "object"
      ? message.metadata.input as Record<string, unknown>
      : parseJson(typeof message.metadata?.inputText === "string" ? message.metadata.inputText : "");
  const target = readString(input?.path ?? input?.file ?? input?.cwd ?? input?.command ?? input?.query);
  const result = summarizeToolResultText(readString(message.metadata?.resultText));
  return compactText([target, result || message.content].filter(Boolean).join(" · "), 170);
}

function normalizeRuntimeToolName(item: RuntimeTimelineItem) {
  const name = (item.toolName || item.title || "").toLowerCase().replace(/\s+/g, "_");
  if (item.kind === "command") return "run_command";
  if (item.kind === "patch") return "apply_patch";
  return name;
}

function isQuietRuntime(item: RuntimeTimelineItem) {
  const name = normalizeRuntimeToolName(item);
  const status = item.status?.toLowerCase() ?? "";
  if (isInFlight(status) || ["failed", "error", "cancelled", "rejected"].includes(status)) {
    return false;
  }
  return ["read_file", "list_dir", "list_directory", "git_status", "git_diff", "search_files", "code_search"].includes(name);
}

type WorklogTreeNode = {
  item: RuntimeTimelineItem;
  children: WorklogTreeNode[];
  depth: number;
};

function runtimeTreeId(item: RuntimeTimelineItem) {
  return item.toolUseId || item.sourceId || item.id.replace(/^(tool|command|runtime):/, "");
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
    nodes.map((node) => ({
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

function splitDiffText(value: string) {
  const files: Array<{ oldPath: string; newPath: string; lines: ReturnType<typeof parseUnifiedDiff> }> = [];
  const sections = value.split(/\ndiff --git /g);
  sections.forEach((section, index) => {
    const text = index === 0 && !section.startsWith("diff --git ") ? section : `diff --git ${section}`;
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

function approvalFileSummaries(item: RuntimeTimelineItem) {
  const files = patchFileSummaries(item).filter((file) => !file.path.trim().startsWith("{"));
  if (files.length) return files;
  const source = [item.code, item.rawDetail].filter(Boolean).join("\n");
  const found = /"path"\s*:\s*"([^"]+)"/.exec(source)?.[1] ?? /(?:path|file|target)\s*[:=]\s*["']?([^"',\n\r]+)["']?/i.exec(source)?.[1];
  if (!found) return [];
  return [{ path: found.replace(/^[ab]\//, "").trim(), status: "修改" }];
}

function readMetadataString(message: SessionWorkspaceMessage, keys: string[]) {
  for (const key of keys) {
    const value = message.metadata?.[key];
    const text = readString(value);
    if (text) return text;
  }
  return "";
}

function messageTitleForKind(kind: CleanTranscriptKind | string, message: SessionWorkspaceMessage) {
  const title = readMetadataString(message, ["title", "label", "action", "event", "state"]);
  if (title) return title;
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

function MessageActions({
  message,
  align = "left",
  onCopyRuntimeText,
  onQuoteMessage,
}: {
  message: SessionWorkspaceMessage;
  align?: "left" | "right";
  onCopyRuntimeText?: (label: string, text: string) => void | Promise<void>;
  onQuoteMessage?: (text: string) => void;
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
              <button type="button" disabled title="需要后端支持从指定 transcript target 继续">
                <span>从这里继续</span>
                <small>等待上下文截断接口</small>
              </button>
            </li>
            <li>
              <button type="button" disabled title="需要后端 branchSession 接口">
                <span>从这里分支</span>
                <small>等待会话分支接口</small>
              </button>
            </li>
            <li>
              <button type="button" disabled title="需要后端 transcript mutation 接口">
                <span>删除消息</span>
                <small>等待会话记录变更接口</small>
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
}: {
  message: SessionWorkspaceMessage;
  onCopyRuntimeText?: (label: string, text: string) => void | Promise<void>;
  onQuoteMessage?: (text: string) => void;
}) {
  const content = stripAssistantRuntimeProgress(message.content).trim();
  if (!content) return null;
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
        <CleanMarkdown content={content} />
      </article>
      <MessageActions message={message} onCopyRuntimeText={onCopyRuntimeText} onQuoteMessage={onQuoteMessage} />
    </div>
  );
});

export const CleanUserMessage = memo(function CleanUserMessage({
  message,
  onCopyRuntimeText,
  onQuoteMessage,
}: {
  message: SessionWorkspaceMessage;
  onCopyRuntimeText?: (label: string, text: string) => void | Promise<void>;
  onQuoteMessage?: (text: string) => void;
}) {
  return (
    <div className="hc-message-stack" data-role="user">
      <article className="hc-message hc-user">
        <p>{message.content}</p>
      </article>
      <MessageActions message={message} align="right" onCopyRuntimeText={onCopyRuntimeText} onQuoteMessage={onQuoteMessage} />
    </div>
  );
});

export const CleanThinkingBlock = memo(function CleanThinkingBlock({ message }: { message: SessionWorkspaceMessage }) {
  const [expanded, setExpanded] = useState(false);
  const text = message.content.trim() || "正在思考";
  const preview = compactText(text.split(/\r?\n/).find((line) => line.trim()) ?? text, 120);
  return (
    <section className="hc-thinking">
      <button type="button" aria-expanded={expanded} onClick={() => setExpanded((open) => !open)}>
        {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        <span>正在思考{message.streaming ? "..." : ""}</span>
        <em>{preview}</em>
      </button>
      {expanded ? <pre>{text}</pre> : null}
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
  const failed = message.status === "failed" || message.metadata?.isError === true;
  const input = typeof message.metadata?.inputText === "string" ? message.metadata.inputText : "";
  const result = typeof message.metadata?.resultText === "string" ? message.metadata.resultText : "";
  const details = [input ? `输入\n${input}` : "", result ? `结果\n${result}` : "", message.content].filter(Boolean).join("\n\n");
  const title = toolActionTitle({
    toolName: message.toolName,
    title: readMetadataString(message, ["title", "label", "action"]),
    input,
    rawDetail: result || message.content,
  });
  return (
    <section className="hc-tool-inline" data-tone={failed ? "danger" : statusTone(message.status)}>
      <button type="button" aria-expanded={expanded} onClick={() => setExpanded((open) => !open)}>
        {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        <Wrench size={15} />
        <strong>{title}</strong>
        <span>{toolInlineSummary(message)}</span>
        <em>{message.streaming ? "运行中" : failed ? "失败" : "完成"}</em>
      </button>
      {expanded && details ? (
        <figure className="hc-tool-detail">
          <figcaption>
            <span>工具详情</span>
            {onCopyRuntimeText ? (
              <button type="button" onClick={() => void onCopyRuntimeText("工具详情", details)}>
                复制
              </button>
            ) : null}
          </figcaption>
          <pre>{details}</pre>
        </figure>
      ) : null}
    </section>
  );
});

export const CleanPermissionMessageBlock = memo(function CleanPermissionMessageBlock({
  message,
  onApprove,
  onReject,
  busyId,
}: {
  message: SessionWorkspaceMessage;
  onApprove?: (approvalId: string) => void | Promise<void>;
  onReject?: (approvalId: string) => void | Promise<void>;
  busyId?: string | null;
}) {
  const requestId = typeof message.metadata?.requestId === "string" ? message.metadata.requestId : "";
  const resolved = message.metadata?.resolved === true;
  const decision = typeof message.metadata?.decision === "string" ? message.metadata.decision : "";
  const busy = Boolean(requestId && busyId === requestId);
  const inputText = readMetadataString(message, ["parametersPreview", "inputText", "fullInput", "command"]);
  const permissionTitle = toolActionTitle({
    toolName: message.toolName,
    title: readMetadataString(message, ["title", "label"]),
    input: inputText,
    rawDetail: message.content,
    fallback: "权限请求",
  });
  return (
    <section className="hc-permission-card" data-resolved={resolved ? "true" : "false"}>
      <header>
        <CircleAlert size={15} />
        <div>
          <strong>{permissionTitle} 需要确认</strong>
          <span>{resolved ? `已${decision === "rejected" ? "拒绝" : "批准"}` : "等待你的操作"}</span>
        </div>
        <StatusChip status={resolved ? (decision === "rejected" ? "rejected" : "approved") : "waiting_approval"} />
      </header>
      {message.content ? <pre>{message.content}</pre> : null}
      {!resolved && requestId ? (
        <div className="hc-approval-actions">
          <button type="button" disabled={busy} onClick={() => void onApprove?.(requestId)}>批准</button>
          <button type="button" disabled={busy} onClick={() => void onReject?.(requestId)}>拒绝</button>
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
  const content = message.content.trim();
  const title = messageTitleForKind(transcriptKind, message);
  const summary = compactText(
    readMetadataString(message, ["summary", "description", "message"]) || content,
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
    .filter(([key, value]) => key !== "kind" && value !== undefined && value !== null && typeof value !== "object")
    .map(([key, value]) => `${key}: ${String(value)}`);
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

function readMetadataList(message: SessionWorkspaceMessage, keys: string[]) {
  for (const key of keys) {
    const value = message.metadata?.[key];
    if (Array.isArray(value)) return value;
  }
  return [];
}

export const CleanAskUserQuestionBlock = memo(function CleanAskUserQuestionBlock({
  message,
  onCopyRuntimeText,
}: {
  message: SessionWorkspaceMessage;
  onCopyRuntimeText?: (label: string, text: string) => void | Promise<void>;
}) {
  const question =
    readMetadataString(message, ["question", "prompt", "summary", "message", "description"]) ||
    message.content.trim() ||
    "需要你补充信息";
  const options = readMetadataList(message, ["options", "choices"]);
  return (
    <section className="hc-question-event">
      <header>
        <HelpCircle size={16} />
        <div>
          <strong>需要你确认</strong>
          <span>{question}</span>
        </div>
        <StatusChip status={readMetadataString(message, ["status"]) || message.status || "waiting"} />
      </header>
      {options.length ? (
        <div className="hc-question-options">
          {options.slice(0, 4).map((option, index) => {
            const record = option && typeof option === "object" ? option as Record<string, unknown> : null;
            const label = record ? readString(record.label ?? record.value ?? record.title) : readString(option);
            const description = record ? readString(record.description ?? record.detail) : "";
            return (
              <button type="button" key={`${label}:${index}`} disabled>
                <strong>{label || `选项 ${index + 1}`}</strong>
                {description ? <span>{description}</span> : null}
              </button>
            );
          })}
        </div>
      ) : null}
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
}: {
  message: SessionWorkspaceMessage;
  onCopyRuntimeText?: (label: string, text: string) => void | Promise<void>;
}) {
  const appName = readMetadataString(message, ["app", "application", "target", "windowTitle"]);
  const permission = readMetadataString(message, ["permission", "action", "summary", "description"]) || message.content.trim();
  const details = Object.entries(message.metadata ?? {})
    .filter(([key, value]) => key !== "kind" && value !== undefined && value !== null && typeof value !== "object")
    .map(([key, value]) => `${key}: ${String(value)}`)
    .join("\n");
  return (
    <section className="hc-computer-event">
      <header>
        <MousePointerClick size={16} />
        <div>
          <strong>Computer Use 权限</strong>
          <span>{[appName, permission].filter(Boolean).join(" · ") || "等待授权详情"}</span>
        </div>
        <StatusChip status={readMetadataString(message, ["status"]) || message.status || "waiting"} />
      </header>
      {details ? <pre>{details}</pre> : null}
      <footer>
        <button type="button" onClick={() => void onCopyRuntimeText?.("Computer Use 权限", [permission, details].filter(Boolean).join("\n\n"))}>
          <Copy size={13} />复制详情
        </button>
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

function DiffPreview({
  item,
  selectedPath,
}: {
  item: RuntimeTimelineItem;
  selectedPath?: string | null;
}) {
  const selected = selectedPath ? normalizeDiffPath(selectedPath) : "";
  const allGroups = item.diffLines?.length
    ? [{ oldPath: "", newPath: selected || "diff", lines: item.diffLines }]
    : splitDiffText(item.rawDetail || "");
  const diffGroups = selected
    ? allGroups.filter((group) => {
        const paths = [group.newPath, group.oldPath].map(normalizeDiffPath);
        return paths.includes(selected);
      })
    : allGroups;
  if (!diffGroups.length) return null;
  return (
    <div className="hc-diff-preview">
      {diffGroups.slice(0, 3).map((group, groupIndex) => (
        <section key={`${group.newPath}:${groupIndex}`} className="hc-diff-file">
          <header>
            <code>{group.newPath || group.oldPath || `diff ${groupIndex + 1}`}</code>
            <span>{group.lines.length} 行</span>
          </header>
          <ol>
            {group.lines.slice(0, 90).map((line, index) => (
              <li key={`${index}:${line.content}`} data-type={line.type}>
                <span>{line.type === "add" ? "+" : line.type === "remove" ? "-" : line.type === "header" ? "@" : " "}</span>
                <code>{line.content || " "}</code>
              </li>
            ))}
          </ol>
        </section>
      ))}
    </div>
  );
}

function ApprovalRuntimeBlock({
  item,
  onApprove,
  onReject,
  onLoadPatch,
  onCopyRuntimeText,
  busyId,
}: {
  item: RuntimeTimelineItem;
  onApprove?: (approvalId: string) => void | Promise<void>;
  onReject?: (approvalId: string) => void | Promise<void>;
  onLoadPatch?: (patchId: string) => void | Promise<void>;
  onCopyRuntimeText?: (label: string, text: string) => void | Promise<void>;
  busyId?: string | null;
}) {
  const [expanded, setExpanded] = useState(shouldExpandByDefault(item));
  const files = useMemo(() => approvalFileSummaries(item), [item]);
  const output = item.rawDetail || item.code || "";
  const canApprove = Boolean(
    item.sourceId &&
      ["pending", "waiting", "waiting_approval", "queued"].includes(item.status?.toLowerCase() ?? ""),
  );
  const busy = Boolean(item.sourceId && busyId === item.sourceId);
  const hasDiff = Boolean(item.diffLines?.length || item.rawDetail?.includes("diff --git"));

  return (
    <section className="hc-runtime hc-approval" data-tone={statusTone(item.status)} data-risky={item.riskLevel ?? "medium"}>
      <header className="hc-approval-head">
        <div>
          <span className="hc-runtime-eyebrow">需要确认</span>
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
                if (item.sourceId) void onLoadPatch?.(item.sourceId);
              }}
            >
              <code>{file.path}</code>
              <span>{[patchStatusLabel(file.status), file.additions !== undefined ? `+${file.additions}` : "", file.deletions !== undefined ? `-${file.deletions}` : ""].filter(Boolean).join(" ")}</span>
            </button>
          ))}
        </div>
      ) : null}
      {canApprove ? (
        <div className="hc-approval-actions">
          <button type="button" disabled={busy} onClick={() => void onApprove?.(item.sourceId ?? "")}>批准</button>
          <button type="button" disabled={busy} onClick={() => void onReject?.(item.sourceId ?? "")}>拒绝</button>
        </div>
      ) : null}
      {output || hasDiff ? (
        <button type="button" className="hc-diff-toggle" onClick={() => setExpanded((open) => !open)}>
          {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
          {expanded ? "收起详情" : hasDiff ? "查看差异" : "查看详情"}
        </button>
      ) : null}
      {expanded && hasDiff ? <DiffPreview item={item} /> : null}
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
}: {
  item: RuntimeTimelineItem;
  onLoadPatch?: (patchId: string) => void | Promise<void>;
  onCopyRuntimeText?: (label: string, text: string) => void | Promise<void>;
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

  return (
    <section className="hc-runtime hc-patch" data-kind={item.kind} data-tone={statusTone(item.status)}>
      <header className="hc-patch-head">
        <div>
          <span className="hc-runtime-eyebrow">改动</span>
          <strong>{item.title || "文件改动"}</strong>
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
            <button type="button" disabled title="需要后端提供 revert turn 接口">
              <RotateCcw size={13} />
              撤销本轮
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
      {expanded ? <DiffPreview item={item} selectedPath={selectedDiffPath} /> : null}
    </section>
  );
}

export const CleanRuntimeBlock = memo(function CleanRuntimeBlock({
  item,
  onApprove,
  onReject,
  onLoadPatch,
  onCopyRuntimeText,
  onQuoteMessage,
  onRefreshCommandJob,
  onStopCommandJob,
  busyId,
}: {
  item: RuntimeTimelineItem;
  onApprove?: (approvalId: string) => void | Promise<void>;
  onReject?: (approvalId: string) => void | Promise<void>;
  onLoadPatch?: (patchId: string) => void | Promise<void>;
  onCopyRuntimeText?: (label: string, text: string) => void | Promise<void>;
  onQuoteMessage?: (text: string) => void;
  onRefreshCommandJob?: (commandId: string) => void | Promise<void>;
  onStopCommandJob?: (commandId: string) => void | Promise<void>;
  busyId?: string | null;
}) {
  const [expanded, setExpanded] = useState(shouldExpandByDefault(item));
  const files = useMemo(() => patchFileSummaries(item), [item]);
  const output = item.kind === "command" ? buildCommandOutput(item) : item.rawDetail || item.code || "";
  const tone = statusTone(item.status);
  const busy = Boolean(item.sourceId && busyId === item.sourceId);
  const canApprove = item.kind === "approval" && item.sourceId && ["pending", "waiting", "waiting_approval", "queued"].includes(item.status?.toLowerCase() ?? "");
  const canStop = item.kind === "command" && item.sourceId && isInFlight(item.status) && onStopCommandJob;
  const canRefresh = item.kind === "command" && item.sourceId && onRefreshCommandJob;
  const risky = item.kind === "approval" || item.riskLevel === "medium" || item.riskLevel === "high";

  if (item.kind === "approval") {
    return (
      <ApprovalRuntimeBlock
        item={item}
        onApprove={onApprove}
        onReject={onReject}
        onLoadPatch={onLoadPatch}
        onCopyRuntimeText={onCopyRuntimeText}
        busyId={busyId}
      />
    );
  }

  if (item.kind === "patch") {
    return <PatchRuntimeBlock item={item} onLoadPatch={onLoadPatch} onCopyRuntimeText={onCopyRuntimeText} />;
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
              <button type="button" disabled={busy} onClick={() => void onApprove?.(item.sourceId ?? "")}>批准</button>
              <button type="button" disabled={busy} onClick={() => void onReject?.(item.sourceId ?? "")}>拒绝</button>
            </div>
          ) : null}
          {files.length ? (
            <div className="hc-change-list">
              {files.map((file) => (
                <button
                  type="button"
                  key={file.path}
                  onClick={() => {
                    if (item.sourceId) void onLoadPatch?.(item.sourceId);
                  }}
                >
                  <code>{file.path}</code>
                  <span>{[file.additions !== undefined ? `+${file.additions}` : "", file.deletions !== undefined ? `-${file.deletions}` : ""].filter(Boolean).join(" ") || "修改"}</span>
                </button>
              ))}
            </div>
          ) : null}
          {output ? (
            <figure className="hc-runtime-output">
              <figcaption>
                <span>{item.kind === "command" ? "Shell" : "详情"}</span>
                <button type="button" onClick={() => void onCopyRuntimeText?.("运行输出", output)}>
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
  const output = item.kind === "command" ? buildCommandOutput(item) : item.rawDetail || item.code || "";
  const summary = runtimeSummary(item);
  const detail = output || item.code || item.rawDetail || summary;
  return (
    <article className="hc-worklog-row" data-tone={statusTone(item.status)} data-depth={depth}>
      <button type="button" aria-expanded={expanded} onClick={() => setExpanded((open) => !open)}>
        {expanded ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
        <RuntimeIcon item={item} />
        <strong>{runtimeLabel(item)}</strong>
        {summary ? <span>{summary}</span> : null}
        {childCount ? <small>{childCount} 个子步骤</small> : null}
        <StatusChip status={item.status} />
        {formatDuration(item.durationMs) ? <time>{formatDuration(item.durationMs)}</time> : null}
      </button>
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
  const visible = expanded
    ? flatTree
    : (importantItems.length ? flatTree.filter((node) => !isQuietRuntime(node.item)).slice(0, 3) : flatTree.slice(0, 3));
  const labels = items.map(runtimeLabel).slice(0, 3);
  return (
    <section className="hc-worklog">
      <button type="button" className="hc-worklog-head" onClick={() => setExpanded((open) => !open)}>
        {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        <span>已执行 {items.length} 项{quietCount ? `，其中 ${quietCount} 项已折叠` : ""}</span>
        {!expanded && labels.length ? <em>{labels.join("、")}{items.length > labels.length ? "..." : ""}</em> : null}
      </button>
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
      {!expanded && flatTree.length > visible.length ? (
        <button type="button" className="hc-show-more" onClick={() => setExpanded(true)}>
          展开另外 {flatTree.length - visible.length} 项
        </button>
      ) : null}
    </section>
  );
}

export function CleanActivityItem({
  item,
  onApprove,
  onReject,
  onLoadPatch,
  onCopyRuntimeText,
  onQuoteMessage,
  onRefreshCommandJob,
  onStopCommandJob,
  busyId,
}: {
  item: ConversationActivityItem;
  onApprove?: (approvalId: string) => void | Promise<void>;
  onReject?: (approvalId: string) => void | Promise<void>;
  onLoadPatch?: (patchId: string) => void | Promise<void>;
  onCopyRuntimeText?: (label: string, text: string) => void | Promise<void>;
  onQuoteMessage?: (text: string) => void;
  onRefreshCommandJob?: (commandId: string) => void | Promise<void>;
  onStopCommandJob?: (commandId: string) => void | Promise<void>;
  busyId?: string | null;
}) {
  const transcriptKind = transcriptKindForActivity(item);
  if (item.kind === "runtime") {
    return (
      <div className="hc-activity" data-transcript-kind={transcriptKind}>
        <CleanRuntimeBlock
          item={item.runtime}
          onApprove={onApprove}
          onReject={onReject}
          onLoadPatch={onLoadPatch}
          onCopyRuntimeText={onCopyRuntimeText}
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
        <CleanWorklogBlock items={item.runtimeItems} onCopyRuntimeText={onCopyRuntimeText} />
      </div>
    );
  }

  const message = item.message;
  const kind = messageKind(message);
  const wrap = (node: JSX.Element | null) => (node ? <div className="hc-activity" data-transcript-kind={transcriptKind}>{node}</div> : null);
  if (kind === "assistant_thinking" || kind === "thinking" || (message.streaming && message.placeholder)) {
    return wrap(<CleanThinkingBlock message={message} />);
  }
  if (kind === "permission_request") {
    return wrap(<CleanPermissionMessageBlock message={message} onApprove={onApprove} onReject={onReject} busyId={busyId} />);
  }
  if (kind === "tool_use" || kind === "tool_result" || kind === "tool_activity") {
    return wrap(<CleanToolMessageBlock message={message} onCopyRuntimeText={onCopyRuntimeText} />);
  }
  if (kind === "ask_user_question") {
    return wrap(<CleanAskUserQuestionBlock message={message} onCopyRuntimeText={onCopyRuntimeText} />);
  }
  if (kind === "computer_use_permission" || kind === "computer_use_permission_request") {
    return wrap(<CleanComputerUsePermissionBlock message={message} onCopyRuntimeText={onCopyRuntimeText} />);
  }
  if (
    [
      "api_retry",
      "background_task",
      "compact_summary",
      "goal_event",
      "memory_event",
      "plan_update",
      "status",
      "system",
      "task_summary",
    ].includes(kind) ||
    message.role === "system" ||
    message.kind === "failure" ||
    message.status === "failed"
  ) {
    return wrap(<CleanSpecialEventBlock message={message} transcriptKind={transcriptKind} />);
  }
  if (message.role === "user") {
    return wrap(
      <CleanUserMessage message={message} onCopyRuntimeText={onCopyRuntimeText} onQuoteMessage={onQuoteMessage} />,
    );
  }
  if (message.role === "assistant") {
    return wrap(
      <CleanAssistantMessage message={message} onCopyRuntimeText={onCopyRuntimeText} onQuoteMessage={onQuoteMessage} />,
    );
  }
  return null;
}
