import { memo, useMemo, useState } from "react";
import {
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  CircleAlert,
  ShieldAlert,
  TerminalSquare,
} from "lucide-react";
import { Button } from "../../../v2/components/ui";
import { formatStatusLabel } from "../../../copy";
import { formatTimestamp } from "../../../../lib/formatUtils";
import type { SessionWorkspaceMessage } from "./types";
import { useTickWhen } from "./useTick";
import { THINKING_STALLED_MS, formatElapsedTime, formatToolNameLabel, getRoleLabel, getMessageDisplayTime } from "./utils";
import { MarkdownContent } from "./MarkdownContent";
import { stripAssistantRuntimeProgress } from "../../../../state/chatMessages";

function normalizeAssistantContent(content: string) {
  const cleaned = stripAssistantRuntimeProgress(content);
  const trimmed = cleaned.trim();
  if (!trimmed) {
    return "";
  }

  const lines = trimmed.split(/\r?\n/);
  const nonEmptyLines = lines.map((line) => line.trim()).filter(Boolean);
  const hasStructuredMarkdown = nonEmptyLines.some((line) =>
    /^(```|#{1,6}\s+|[-*+]\s+|\d+\.\s+|\|)/.test(line),
  );
  const joinedText = nonEmptyLines.join("");
  const cjkChars = joinedText.match(/[\u4e00-\u9fff]/g)?.length ?? 0;
  const shortCjkLines = nonEmptyLines.filter((line) => line.length <= 7 && /[\u4e00-\u9fff]/.test(line)).length;
  const isFragmentedCjk =
    nonEmptyLines.length >= 6 &&
    !hasStructuredMarkdown &&
    shortCjkLines / nonEmptyLines.length >= 0.62 &&
    cjkChars / Math.max(joinedText.length, 1) >= 0.42;

  if (isFragmentedCjk) {
    return lines
      .map((line) => line.trim())
      .filter(Boolean)
      .join("")
      .replace(/([。！？；!?;])(?=\S)/g, "$1\n\n");
  }

  return cleaned;
}

function shouldUseAssistantDocumentLayout(content: string) {
  const trimmed = content.trim();
  if (!trimmed) {
    return false;
  }
  if (/```/.test(trimmed)) {
    return true;
  }

  const lines = trimmed.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  const paragraphCount = trimmed.split(/\n\s*\n/).map((paragraph) => paragraph.trim()).filter(Boolean).length;
  const hasStructuredMarkdown = lines.some((line) =>
    /^(#{1,6}\s+|[-*+]\s+|\d+\.\s+|>\s+|\|.*\|)/.test(line),
  );

  return hasStructuredMarkdown || paragraphCount >= 2 || lines.length >= 8 || trimmed.length >= 520;
}

function getMessageMetadataKind(message: SessionWorkspaceMessage) {
  const kind = message.metadata?.kind;
  return typeof kind === "string" ? kind : "";
}

function getMessageMetadataString(message: SessionWorkspaceMessage, key: string) {
  const value = message.metadata?.[key];
  return typeof value === "string" && value.trim() ? value.trim() : "";
}

function getMessagePreviewSummary(message: SessionWorkspaceMessage) {
  const rows = message.metadata?.resultPreview;
  if (!Array.isArray(rows)) return "";
  return rows
    .map((row) => {
      if (!row || typeof row !== "object") return "";
      const record = row as Record<string, unknown>;
      const label = typeof record.label === "string" ? record.label.trim() : "";
      const value = typeof record.value === "string" ? record.value.trim() : "";
      return label && value ? `${label}: ${value}` : "";
    })
    .filter(Boolean)
    .slice(0, 3)
    .join(" · ");
}

function isRawJsonLike(value?: string) {
  const trimmed = value?.trim();
  return Boolean(
    trimmed &&
      ((trimmed.startsWith("{") && trimmed.endsWith("}")) ||
        (trimmed.startsWith("[") && trimmed.endsWith("]"))),
  );
}

function compactToolDisplayText(value: string, limit = 140) {
  return value.replace(/\s+/g, " ").trim().slice(0, limit);
}

function formatJsonValue(value: unknown) {
  if (typeof value === "string") {
    return value.trim();
  }
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  return "";
}

function summarizeJsonToolRecord(record: Record<string, unknown>, kind: string) {
  const parts: string[] = [];
  const command = formatJsonValue(record.command ?? record.cmd);
  const path = formatJsonValue(record.path ?? record.file ?? record.cwd ?? record.root);
  const query = formatJsonValue(record.query);
  const status = formatJsonValue(record.status);
  const summary = formatJsonValue(record.summary);
  const error = formatJsonValue(record.error);
  const stdout = formatJsonValue(record.stdout);
  const stderr = formatJsonValue(record.stderr);
  const items = Array.isArray(record.items) ? record.items : null;
  const changes = Array.isArray(record.changes) ? record.changes : null;

  if (command) parts.push(`命令：${command}`);
  if (path) parts.push(`路径：${path}`);
  if (query) parts.push(`查询：${query}`);
  if (items) parts.push(`找到 ${items.length} 项`);
  if (changes) parts.push(`${changes.length} 个变更`);
  if (status) parts.push(`状态：${formatStatusLabel(status)}`);
  if (summary) parts.push(summary);
  if (error) parts.push(`错误：${error}`);
  if (stderr) parts.push(`错误：${stderr.replace(/\s+/g, " ").slice(0, 80)}`);
  if (stdout) parts.push(stdout.replace(/\s+/g, " ").slice(0, 80));

  if (parts.length) {
    return parts.slice(0, 2).join(" · ");
  }

  const keys = Object.keys(record);
  if (keys.length) {
    return kind === "tool_result" ? `返回 ${keys.length} 个字段` : `包含 ${keys.length} 个参数`;
  }
  return "";
}

function summarizeToolContent(content: string, kind: string) {
  const trimmed = content.trim();
  if (!trimmed) {
    return kind === "tool_result" ? "无结果内容" : "无参数";
  }
  try {
    const parsed = JSON.parse(trimmed) as unknown;
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
      const summary = summarizeJsonToolRecord(parsed as Record<string, unknown>, kind);
      if (summary) return summary;
    }
    if (Array.isArray(parsed)) {
      return `返回 ${parsed.length} 项`;
    }
  } catch {
    // Non-JSON tool output is common; fall through to compact text.
  }
  return trimmed.replace(/\s+/g, " ").slice(0, 140);
}

function summarizeToolActivity(message: SessionWorkspaceMessage, inputText: string, resultText: string) {
  const displaySummary = getMessageMetadataString(message, "displaySummary");
  const resultSummary = getMessageMetadataString(message, "resultSummary");
  const previewSummary = getMessagePreviewSummary(message);
  const inputSummary = getMessageMetadataString(message, "inputSummary");
  const target = getMessageMetadataString(message, "target");
  const derivedInputSummary = inputText ? summarizeToolContent(inputText, "tool_use") : "";
  const derivedResultSummary = resultText ? summarizeToolContent(resultText, "tool_result") : "";
  if (displaySummary) return compactToolDisplayText(displaySummary);
  if (resultSummary) return compactToolDisplayText(resultSummary);
  if (previewSummary) return compactToolDisplayText(previewSummary);
  if (inputSummary && inputSummary !== target) return compactToolDisplayText(inputSummary);
  if (target && derivedResultSummary) return compactToolDisplayText(derivedResultSummary);
  if (derivedInputSummary && derivedResultSummary && derivedInputSummary !== derivedResultSummary) {
    return compactToolDisplayText(`${derivedInputSummary} - ${derivedResultSummary}`);
  }
  if (derivedInputSummary) return compactToolDisplayText(derivedInputSummary);
  if (derivedResultSummary) return compactToolDisplayText(derivedResultSummary);
  if (resultText && !isRawJsonLike(resultText)) return compactToolDisplayText(resultText);
  if (inputText && !isRawJsonLike(inputText) && inputText !== target) return compactToolDisplayText(inputText);
  return "";
}

function getToolTarget(value: unknown) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return "";
  }
  const record = value as Record<string, unknown>;
  const target = record.path ?? record.file ?? record.file_path ?? record.cwd ?? record.root ?? record.command ?? record.query;
  return typeof target === "string" ? target.trim() : "";
}

function parseToolInputRecord(message: SessionWorkspaceMessage) {
  const input = message.metadata?.input;
  if (input && typeof input === "object" && !Array.isArray(input)) {
    return input as Record<string, unknown>;
  }
  const inputText = typeof message.metadata?.inputText === "string" ? message.metadata.inputText : message.content;
  try {
    const parsed = JSON.parse(inputText) as unknown;
    return parsed && typeof parsed === "object" && !Array.isArray(parsed)
      ? (parsed as Record<string, unknown>)
      : null;
  } catch {
    return null;
  }
}

function getToolStatusLabel(message: SessionWorkspaceMessage, isError: boolean, isActivity: boolean, isResult: boolean) {
  if (message.status === "cancelled") {
    return "已取消";
  }
  if (isError) {
    return "失败";
  }
  if (message.streaming) {
    return "运行中";
  }
  if (isActivity) {
    return "完成";
  }
  if (isResult) {
    return "结果";
  }
  return "调用";
}

function ToolBlockContent({ message }: { message: SessionWorkspaceMessage }) {
  const [expanded, setExpanded] = useState(false);
  const kind = getMessageMetadataKind(message);
  const isResult = kind === "tool_result";
  const isActivity = kind === "tool_activity";
  const isError = message.metadata?.isError === true || message.status === "failed";
  const Icon = isError ? CircleAlert : isResult || isActivity ? CheckCircle2 : TerminalSquare;
  const content = message.content.trim();
  const inputText = typeof message.metadata?.inputText === "string" ? message.metadata.inputText.trim() : content;
  const resultText = typeof message.metadata?.resultText === "string" ? message.metadata.resultText.trim() : "";
  const metadataInput = message.metadata?.input;
  const metadataInputText = message.metadata?.inputText;
  const inputRecord = useMemo(
    () => parseToolInputRecord(message),
    [content, metadataInput, metadataInputText],
  );
  const metadataTarget = getMessageMetadataString(message, "displayTarget") || getMessageMetadataString(message, "target");
  const inputSummary = getMessageMetadataString(message, "inputSummary");
  const target = metadataTarget || getToolTarget(inputRecord);
  const summary = isActivity ? summarizeToolActivity(message, inputText, resultText) : summarizeToolContent(content, kind);
  const displayTitle = getMessageMetadataString(message, "displayTitle");
  const toolLabel = displayTitle || formatToolNameLabel(message.toolName) || (isActivity ? "工具过程" : isResult ? "工具结果" : "工具调用");
  const statusLabel = getToolStatusLabel(message, isError, isActivity, isResult);
  const ToggleIcon = expanded ? ChevronDown : ChevronRight;
  const showInputDetail = Boolean(
    inputText &&
      !isRawJsonLike(inputText) &&
      inputText !== target &&
      inputText !== inputSummary,
  );
  const showRawInputDetail = Boolean(
    inputText &&
      isRawJsonLike(inputText) &&
      (isError || (!inputSummary && !target && !summary)),
  );
  const showResultDetail = Boolean(resultText && (!isRawJsonLike(resultText) || isError));

  return (
    <section className="message-tool-block" data-kind={kind} data-error={isError ? "true" : "false"}>
      <button
        aria-expanded={expanded}
        className="message-tool-block-head"
        onClick={() => setExpanded((current) => !current)}
        type="button"
      >
        <ToggleIcon className="message-tool-block-toggle" size={14} strokeWidth={2} aria-hidden="true" />
        <Icon size={14} strokeWidth={2} aria-hidden="true" />
        <strong>{toolLabel}</strong>
        {target ? <span>{target}</span> : null}
        <small>{summary}</small>
        <em>{statusLabel}</em>
      </button>
      {expanded && isActivity ? (
        <div className="message-tool-block-detail">
          {showInputDetail || showRawInputDetail ? (
            <section>
              <span>输入</span>
              <pre>{inputText}</pre>
            </section>
          ) : null}
          {showResultDetail ? (
            <section>
              <span>结果</span>
              <pre>{resultText}</pre>
            </section>
          ) : null}
        </div>
      ) : expanded && content ? (
        <pre>{content}</pre>
      ) : null}
    </section>
  );
}

function ThinkingInlineBlock({
  content,
  elapsedMs,
  active,
}: {
  content: string;
  elapsedMs: number;
  active: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const lines = content.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  const preview = (lines[0] || "正在思考").replace(/\s+/g, " ");
  const hasDetails = lines.length > 1 || preview.length > 86;
  const label = preview.length > 86 ? `${preview.slice(0, 82).trimEnd()}...` : preview;

  return (
    <section className="message-thinking-inline" data-active={active ? "true" : "false"}>
      <button
        type="button"
        aria-expanded={expanded}
        disabled={!hasDetails}
        onClick={() => setExpanded((current) => !current)}
      >
        <ChevronRight className="message-thinking-chevron" size={13} strokeWidth={2} aria-hidden="true" />
        <span>正在思考</span>
        <small>{label}</small>
        <time>{formatElapsedTime(elapsedMs)}</time>
      </button>
      {expanded && hasDetails ? <pre>{content}</pre> : null}
    </section>
  );
}

function PermissionRequestContent({
  message,
  onApprove,
  onReject,
  busyId,
}: {
  message: SessionWorkspaceMessage;
  onApprove?(approvalId: string): void | Promise<void>;
  onReject?(approvalId: string): void | Promise<void>;
  busyId?: string | null;
}) {
  const content = message.content.trim();
  const requestId = getMessageMetadataString(message, "requestId");
  const decision = getMessageMetadataString(message, "decision");
  const resolved = message.metadata?.resolved === true || Boolean(decision);
  const isBusy = Boolean(requestId && busyId === requestId);
  const canResolve = Boolean(requestId && !resolved && (onApprove || onReject));
  const statusText = resolved ? (decision === "rejected" ? "已拒绝" : "已批准") : "等待确认";
  const toolLabel = formatToolNameLabel(message.toolName) || "权限请求";

  return (
    <section className="message-permission-block">
      <div className="message-permission-block-head">
        <ShieldAlert size={14} strokeWidth={2} aria-hidden="true" />
        <strong>{toolLabel}</strong>
        <span>{statusText}</span>
      </div>
      {content ? <pre>{content}</pre> : null}
      {canResolve ? (
        <div className="message-permission-actions">
          {onApprove ? (
            <Button
              size="xs"
              variant="secondary"
              loading={isBusy}
              onClick={() => {
                void onApprove(requestId);
              }}
            >
              批准
            </Button>
          ) : null}
          {onReject ? (
            <Button
              size="xs"
              variant="secondary"
              loading={isBusy}
              onClick={() => {
                void onReject(requestId);
              }}
            >
              拒绝
            </Button>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}

export const MessageBubble = memo(function MessageBubble({
  message,
  activityHint,
  onApprove,
  onReject,
  busyId,
}: {
  message: SessionWorkspaceMessage;
  activityHint?: string;
  onApprove?(approvalId: string): void | Promise<void>;
  onReject?(approvalId: string): void | Promise<void>;
  busyId?: string | null;
}) {
  const metadataKind = getMessageMetadataKind(message);
  const isStatusThinking = metadataKind === "assistant_thinking";
  const isProgressNote = metadataKind === "assistant_progress";
  const isThinking = isStatusThinking || Boolean(message.streaming && message.placeholder);
  const now = useTickWhen(isThinking);

  const thinkingStartedAt = message.createdAt ?? now;
  const thinkingElapsedMs = Math.max(0, now - thinkingStartedAt);
  const thinkingStalled = thinkingElapsedMs >= THINKING_STALLED_MS;
  const thinkingTitle =
    isStatusThinking && message.content.trim()
      ? message.content.trim()
      : thinkingStalled
        ? "仍在处理…"
        : "正在处理…";
  const thinkingCopy =
    activityHint ||
    (isStatusThinking
      ? ""
      : thinkingStalled
        ? "还没有收到可展示内容；后端可能正在等待模型、工具或审批。"
        : "正在等待模型或运行时返回第一段内容。");
  const displayContent = message.role === "assistant" ? normalizeAssistantContent(message.content) : message.content;
  const isToolBlock = metadataKind === "tool_use" || metadataKind === "tool_result" || metadataKind === "tool_activity";
  const isPermissionRequest = metadataKind === "permission_request";
  const isAssistantDocument =
    message.role === "assistant" &&
    !isThinking &&
    !isProgressNote &&
    !isToolBlock &&
    !isPermissionRequest &&
    shouldUseAssistantDocumentLayout(displayContent);

  return (
    <article
      className={`message-bubble${isThinking ? " message-bubble-thinking" : ""}${isProgressNote ? " message-bubble-progress" : ""}${isAssistantDocument ? " message-bubble-document" : ""}`}
      data-stalled={isThinking && thinkingStalled ? "true" : undefined}
      data-activity-kind="message"
      data-role={message.role}
      data-message-kind={metadataKind || undefined}
      aria-label={`${getRoleLabel(message.role)}消息`}
    >
      <div className="message-bubble-body">
        {!isAssistantDocument ? (
          <div className="message-bubble-head">
            <span>{getRoleLabel(message.role)}</span>
            {message.toolName ? <em>{formatToolNameLabel(message.toolName)}</em> : null}
            {message.status ? <em>{formatStatusLabel(message.status)}</em> : null}
            {getMessageDisplayTime(message) ? (
              <time>{formatTimestamp(getMessageDisplayTime(message), { includeSeconds: true, forceTimeOnly: true })}</time>
            ) : null}
          </div>
        ) : null}
        {isProgressNote ? (
          <p className="message-progress-note">{displayContent}</p>
        ) : isStatusThinking ? (
          <ThinkingInlineBlock active={message.streaming === true} content={message.content.trim() || "正在思考"} elapsedMs={thinkingElapsedMs} />
        ) : isThinking ? (
          <div className="thinking-status" data-stalled={thinkingStalled ? "true" : "false"}>
            <div className="thinking-dots" aria-label="思考中">
              <span /><span /><span />
            </div>
            <p>
              <strong>{thinkingTitle}</strong>
              <time>{formatElapsedTime(thinkingElapsedMs)}</time>
            </p>
            {thinkingCopy ? <small>{thinkingCopy}</small> : null}
          </div>
        ) : isPermissionRequest ? (
          <PermissionRequestContent message={message} onApprove={onApprove} onReject={onReject} busyId={busyId} />
        ) : isToolBlock ? (
          <ToolBlockContent message={message} />
        ) : message.role === "assistant" ? (
          <MarkdownContent content={displayContent} />
        ) : (
          <p>{displayContent}</p>
        )}
      </div>
    </article>
  );
});
