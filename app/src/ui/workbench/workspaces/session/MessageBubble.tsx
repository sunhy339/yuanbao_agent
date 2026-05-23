import { memo } from "react";
import { formatStatusLabel } from "../../../copy";
import { formatTimestamp } from "../../../../lib/formatUtils";
import type { SessionWorkspaceMessage } from "./types";
import { useTickWhen } from "./useTick";
import { THINKING_STALLED_MS, formatElapsedTime, getRoleLabel, getMessageDisplayTime } from "./utils";
import { MarkdownContent } from "./MarkdownContent";

export const MessageBubble = memo(function MessageBubble({ message }: { message: SessionWorkspaceMessage }) {
  const isThinking = Boolean(message.streaming && message.placeholder);
  const now = useTickWhen(isThinking);

  const thinkingStartedAt = message.createdAt ?? now;
  const thinkingElapsedMs = Math.max(0, now - thinkingStartedAt);
  const thinkingStalled = thinkingElapsedMs >= THINKING_STALLED_MS;

  return (
    <article
      className={`message-bubble${isThinking ? " message-bubble-thinking" : ""}`}
      data-stalled={isThinking && thinkingStalled ? "true" : undefined}
      data-activity-kind="message"
      data-role={message.role}
      aria-label={`${getRoleLabel(message.role)}消息`}
    >
      <div className="message-bubble-head">
        <span>{getRoleLabel(message.role)}</span>
        {message.toolName ? <em>{message.toolName}</em> : null}
        {message.status ? <em>{formatStatusLabel(message.status)}</em> : null}
        {message.streaming && !message.placeholder ? <em>流式输出</em> : null}
        {getMessageDisplayTime(message) ? (
          <time>{formatTimestamp(getMessageDisplayTime(message), { includeSeconds: true, forceDateTime: true })}</time>
        ) : null}
      </div>
      {isThinking ? (
        <div className="thinking-status" data-stalled={thinkingStalled ? "true" : "false"}>
          <div className="thinking-dots" aria-label="思考中">
            <span /><span /><span />
          </div>
          <p>
            <strong>{thinkingStalled ? "仍在思考…" : "思考中…"}</strong>
            <time>{formatElapsedTime(thinkingElapsedMs)}</time>
          </p>
          {thinkingStalled ? <small>长时间无新输出。可以停止当前轮次或暂存下一条消息。</small> : null}
        </div>
      ) : message.role === "assistant" ? (
        <MarkdownContent content={message.content} />
      ) : (
        <p>{message.content}</p>
      )}
    </article>
  );
});
