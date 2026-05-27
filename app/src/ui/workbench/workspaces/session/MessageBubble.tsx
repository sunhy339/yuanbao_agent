import { memo } from "react";
import { Bot, CheckCircle2, Info, ShieldAlert, TerminalSquare, UserRound, Wrench } from "lucide-react";
import { formatStatusLabel } from "../../../copy";
import { formatTimestamp } from "../../../../lib/formatUtils";
import type { SessionWorkspaceMessage } from "./types";
import { useTickWhen } from "./useTick";
import { THINKING_STALLED_MS, formatElapsedTime, getRoleLabel, getMessageDisplayTime } from "./utils";
import { MarkdownContent } from "./MarkdownContent";
import { stripAssistantRuntimeProgress } from "../../../../state/chatMessages";

function normalizeAssistantContent(content: string) {
  const cleaned = stripAssistantRuntimeProgress(content);
  const trimmed = cleaned.trim();
  if (!trimmed) {
    return "";
  }

  const lines = trimmed.split(/\r?\n/);
  const shortLines = lines.filter((line) => line.trim().length > 0 && line.trim().length <= 4).length;
  if (lines.length >= 8 && shortLines / lines.length > 0.72) {
    return lines
      .map((line) => line.trim())
      .filter(Boolean)
      .join("")
      .replace(/([。！？；])(?=\S)/g, "$1\n\n");
  }

  return cleaned;
}

function MessageAvatar({ role }: { role: SessionWorkspaceMessage["role"] }) {
  const iconProps = { size: 16, strokeWidth: 2.1, "aria-hidden": true };

  return (
    <span className="message-avatar" data-role={role} aria-hidden="true">
      {role === "assistant" ? (
        <Bot {...iconProps} />
      ) : role === "tool" ? (
        <Wrench {...iconProps} />
      ) : role === "system" ? (
        <Info {...iconProps} />
      ) : (
        <UserRound {...iconProps} />
      )}
    </span>
  );
}

function getMessageMetadataKind(message: SessionWorkspaceMessage) {
  const kind = message.metadata?.kind;
  return typeof kind === "string" ? kind : "";
}

function ToolBlockContent({ message }: { message: SessionWorkspaceMessage }) {
  const kind = getMessageMetadataKind(message);
  const isResult = kind === "tool_result";
  const isError = message.metadata?.isError === true || message.status === "failed";
  const Icon = isResult ? CheckCircle2 : TerminalSquare;
  const title = isResult ? "工具结果" : "工具调用";
  const content = message.content.trim();

  return (
    <section className="message-tool-block" data-kind={kind} data-error={isError ? "true" : "false"}>
      <div className="message-tool-block-head">
        <Icon size={14} strokeWidth={2} aria-hidden="true" />
        <strong>{message.toolName || title}</strong>
        <span>{title}</span>
      </div>
      {content ? <pre>{content}</pre> : null}
    </section>
  );
}

function PermissionRequestContent({ message }: { message: SessionWorkspaceMessage }) {
  const content = message.content.trim();

  return (
    <section className="message-permission-block">
      <div className="message-permission-block-head">
        <ShieldAlert size={14} strokeWidth={2} aria-hidden="true" />
        <strong>{message.toolName || "权限请求"}</strong>
        <span>等待确认</span>
      </div>
      {content ? <pre>{content}</pre> : null}
    </section>
  );
}

export const MessageBubble = memo(function MessageBubble({
  message,
  activityHint,
}: {
  message: SessionWorkspaceMessage;
  activityHint?: string;
}) {
  const metadataKind = getMessageMetadataKind(message);
  const isStatusThinking = metadataKind === "assistant_thinking";
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
      ? "收到工具、审批或正文输出后会自动更新。"
      : thinkingStalled
        ? "还没有收到可展示内容；后端可能正在等待模型、工具或审批。"
        : "正在等待模型或运行时返回第一段内容。");
  const displayContent = message.role === "assistant" ? normalizeAssistantContent(message.content) : message.content;
  const isToolBlock = metadataKind === "tool_use" || metadataKind === "tool_result";
  const isPermissionRequest = metadataKind === "permission_request";

  return (
    <article
      className={`message-bubble${isThinking ? " message-bubble-thinking" : ""}`}
      data-stalled={isThinking && thinkingStalled ? "true" : undefined}
      data-activity-kind="message"
      data-role={message.role}
      aria-label={`${getRoleLabel(message.role)}消息`}
    >
      {message.role !== "user" ? <MessageAvatar role={message.role} /> : null}
      <div className="message-bubble-body">
        <div className="message-bubble-head">
          <span>{getRoleLabel(message.role)}</span>
          {message.toolName ? <em>{message.toolName}</em> : null}
          {message.status ? <em>{formatStatusLabel(message.status)}</em> : null}
          {getMessageDisplayTime(message) ? (
            <time>{formatTimestamp(getMessageDisplayTime(message), { includeSeconds: true, forceTimeOnly: true })}</time>
          ) : null}
        </div>
        {isThinking ? (
          <div className="thinking-status" data-stalled={thinkingStalled ? "true" : "false"}>
            <div className="thinking-dots" aria-label="思考中">
              <span /><span /><span />
            </div>
            <p>
              <strong>{thinkingTitle}</strong>
              <time>{formatElapsedTime(thinkingElapsedMs)}</time>
            </p>
            <small>{thinkingCopy}</small>
          </div>
        ) : isPermissionRequest ? (
          <PermissionRequestContent message={message} />
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
