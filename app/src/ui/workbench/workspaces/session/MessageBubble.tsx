import { memo } from "react";
import { Bot, Info, UserRound, Wrench } from "lucide-react";
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

export const MessageBubble = memo(function MessageBubble({
  message,
  activityHint,
}: {
  message: SessionWorkspaceMessage;
  activityHint?: string;
}) {
  const isThinking = Boolean(message.streaming && message.placeholder);
  const now = useTickWhen(isThinking);

  const thinkingStartedAt = message.createdAt ?? now;
  const thinkingElapsedMs = Math.max(0, now - thinkingStartedAt);
  const thinkingStalled = thinkingElapsedMs >= THINKING_STALLED_MS;
  const thinkingCopy =
    activityHint ||
    (thinkingStalled
      ? "还没有收到可展示内容；后台可能正在等待模型、工具或审批。"
      : "正在等待模型或运行时返回第一段内容。");
  const displayContent = message.role === "assistant" ? normalizeAssistantContent(message.content) : message.content;

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
              <strong>{thinkingStalled ? "仍在处理…" : "正在处理…"}</strong>
              <time>{formatElapsedTime(thinkingElapsedMs)}</time>
            </p>
            <small>{thinkingCopy}</small>
          </div>
        ) : message.role === "assistant" ? (
          <MarkdownContent content={displayContent} />
        ) : (
          <p>{displayContent}</p>
        )}
      </div>
    </article>
  );
});
