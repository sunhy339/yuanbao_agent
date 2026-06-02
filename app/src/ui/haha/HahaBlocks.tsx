import { memo, useMemo, useState } from "react";
import { CheckCircle2, ChevronDown, ChevronRight, CircleAlert, FileDiff, TerminalSquare, Wrench } from "lucide-react";
import { StatusBadge } from "../v2/components/ui";
import { formatStatusLabel } from "../copy";
import type { ConversationActivityItem, RuntimeTimelineItem, SessionWorkspaceMessage } from "../workbench/workspaces/session/types";
import {
  buildCommandOutput,
  compactMeta,
  compactText,
  formatElapsedTime,
  formatToolNameLabel,
  getStatusTone,
  parsePatchFileSummaries,
} from "../workbench/workspaces/session/utils";
import { HahaMarkdown } from "./HahaMarkdown";
import { stripAssistantRuntimeProgress } from "../../state/chatMessages";

function metadataKind(message: SessionWorkspaceMessage) {
  const kind = message.metadata?.kind;
  return typeof kind === "string" ? kind : "";
}

function shouldDocument(content: string) {
  const trimmed = content.trim();
  if (!trimmed) return false;
  return /```/.test(trimmed) ||
    /^\s{0,3}(#{1,6}\s|[-*+]\s|\d+\.\s|>\s|\|.+\|)/m.test(trimmed) ||
    trimmed.split(/\n\s*\n/).filter((chunk) => chunk.trim()).length >= 2 ||
    trimmed.split("\n").filter((line) => line.trim()).length >= 8;
}

function readMessageText(value: unknown) {
  if (typeof value === "string") return value.trim();
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return "";
}

function parseJsonObject(value: string) {
  try {
    const parsed = JSON.parse(value) as unknown;
    return parsed && typeof parsed === "object" && !Array.isArray(parsed)
      ? (parsed as Record<string, unknown>)
      : null;
  } catch {
    return null;
  }
}

function toolInput(message: SessionWorkspaceMessage) {
  if (message.metadata?.input && typeof message.metadata.input === "object" && !Array.isArray(message.metadata.input)) {
    return message.metadata.input as Record<string, unknown>;
  }
  const inputText = typeof message.metadata?.inputText === "string" ? message.metadata.inputText : message.content;
  return parseJsonObject(inputText) ?? {};
}

function toolSummary(message: SessionWorkspaceMessage) {
  const input = toolInput(message);
  const target = readMessageText(input.path ?? input.file ?? input.file_path ?? input.cwd ?? input.command ?? input.query);
  const result = readMessageText(message.metadata?.resultText);
  if (target && result) return `${target} · ${compactText(result, 90)}`;
  if (target) return target;
  if (result) return compactText(result, 110);
  return compactText(message.content, 120);
}

export const HahaThinkingBlock = memo(function HahaThinkingBlock({ message }: { message: SessionWorkspaceMessage }) {
  const [expanded, setExpanded] = useState(false);
  const content = message.content.trim() || "正在思考";
  const firstLine = content.split(/\r?\n/).map((line) => line.trim()).find(Boolean) ?? "正在思考";
  const preview = firstLine.length > 88 ? `${firstLine.slice(0, 84).trimEnd()}...` : firstLine;
  const elapsed = message.createdAt ? Math.max(0, Date.now() - message.createdAt) : 0;

  return (
    <div className="haha-thinking">
      <button type="button" aria-expanded={expanded} onClick={() => setExpanded((value) => !value)}>
        {expanded ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
        <strong>正在思考{message.streaming ? "..." : ""}</strong>
        <span>{preview}</span>
        <time>{formatElapsedTime(elapsed)}</time>
      </button>
      {expanded ? <pre>{content}</pre> : null}
    </div>
  );
});

export const HahaToolMessageBlock = memo(function HahaToolMessageBlock({ message }: { message: SessionWorkspaceMessage }) {
  const [expanded, setExpanded] = useState(false);
  const kind = metadataKind(message);
  const failed = message.status === "failed" || message.metadata?.isError === true;
  const inputText = typeof message.metadata?.inputText === "string" ? message.metadata.inputText : "";
  const resultText = typeof message.metadata?.resultText === "string" ? message.metadata.resultText : "";
  const details = compactMeta([inputText ? `输入\n${inputText}` : null, resultText ? `结果\n${resultText}` : null, message.content]).join("\n\n");
  const label = formatToolNameLabel(message.toolName) || (kind === "tool_result" ? "工具结果" : "工具调用");

  return (
    <section className="haha-tool-block" data-error={failed ? "true" : "false"}>
      <button type="button" aria-expanded={expanded} onClick={() => setExpanded((value) => !value)}>
        {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        {failed ? <CircleAlert size={15} /> : <Wrench size={15} />}
        <strong>{label}</strong>
        <span>{toolSummary(message)}</span>
        <em>{message.streaming ? "运行中" : failed ? "失败" : "完成"}</em>
      </button>
      {expanded && details ? <pre>{details}</pre> : null}
    </section>
  );
});

function runtimeSummary(item: RuntimeTimelineItem) {
  if (item.kind === "command") {
    return compactText(item.summary || buildCommandOutput(item) || item.code || "", 130);
  }
  return compactText(item.summary || item.rawDetail || item.code || "", 130);
}

export const HahaRuntimeBlock = memo(function HahaRuntimeBlock({
  item,
  onApprove,
  onReject,
  onLoadPatch,
  onCopyRuntimeText,
  onRefreshCommandJob,
  onStopCommandJob,
  busyId,
}: {
  item: RuntimeTimelineItem;
  onApprove?: (approvalId: string) => void | Promise<void>;
  onReject?: (approvalId: string) => void | Promise<void>;
  onLoadPatch?: (patchId: string) => void | Promise<void>;
  onCopyRuntimeText?: (label: string, text: string) => void | Promise<void>;
  onRefreshCommandJob?: (commandId: string) => void | Promise<void>;
  onStopCommandJob?: (commandId: string) => void | Promise<void>;
  busyId?: string | null;
}) {
  const isPatch = item.kind === "patch";
  const isApproval = item.kind === "approval";
  const status = item.status?.toLowerCase();
  const [expanded, setExpanded] = useState(isPatch || (isApproval && ["pending", "queued", "waiting", "waiting_approval"].includes(status ?? "")));
  const output = item.kind === "command" ? buildCommandOutput(item) : item.rawDetail || item.code || "";
  const files = useMemo(() => parsePatchFileSummaries(item.code), [item.code]);
  const Icon = isPatch ? FileDiff : item.kind === "command" ? TerminalSquare : isApproval ? CircleAlert : CheckCircle2;
  const canApprove = isApproval && item.sourceId && ["pending", "queued", "waiting", "waiting_approval"].includes(status ?? "");
  const canRefresh = item.kind === "command" && item.sourceId && onRefreshCommandJob;
  const canStop = item.kind === "command" && item.sourceId && onStopCommandJob && ["running", "started"].includes(status ?? "");
  const busy = Boolean(item.sourceId && busyId === item.sourceId);

  return (
    <section className="haha-runtime-block" data-kind={item.kind} data-status={item.status ?? "recorded"}>
      <button className="haha-runtime-head" type="button" aria-expanded={expanded} onClick={() => setExpanded((value) => !value)}>
        {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        <Icon size={15} />
        <strong>{item.title}</strong>
        <span>{runtimeSummary(item)}</span>
        {item.status ? <StatusBadge label={formatStatusLabel(item.status)} tone={getStatusTone(item.status)} compact /> : null}
      </button>
      {expanded ? (
        <div className="haha-runtime-detail">
          {canApprove ? (
            <div className="haha-approval-actions">
              <button type="button" disabled={busy} onClick={() => void onApprove?.(item.sourceId ?? "")}>批准</button>
              <button type="button" disabled={busy} onClick={() => void onReject?.(item.sourceId ?? "")}>拒绝</button>
            </div>
          ) : null}
          {isPatch && files.length ? (
            <div className="haha-file-list">
              {files.map((file) => (
                <button
                  type="button"
                  key={file.path}
                  onClick={() => {
                    if (item.sourceId) void onLoadPatch?.(item.sourceId);
                  }}
                >
                  <code>{file.path}</code>
                  <span>{compactMeta([
                    file.additions !== undefined ? `+${file.additions}` : null,
                    file.deletions !== undefined ? `-${file.deletions}` : null,
                  ]).join(" ") || "修改"}</span>
                </button>
              ))}
            </div>
          ) : null}
          {output ? (
            <figure className="haha-runtime-output">
              <figcaption>
                <span>{item.kind === "command" ? "Shell" : "详情"}</span>
                <button type="button" onClick={() => void onCopyRuntimeText?.("运行输出", output)}>复制</button>
              </figcaption>
              <pre>{output}</pre>
            </figure>
          ) : null}
          {canRefresh || canStop ? (
            <div className="haha-runtime-actions">
              {canRefresh ? <button type="button" disabled={busy} onClick={() => void onRefreshCommandJob?.(item.sourceId ?? "")}>刷新</button> : null}
              {canStop ? <button type="button" disabled={busy} onClick={() => void onStopCommandJob?.(item.sourceId ?? "")}>停止</button> : null}
            </div>
          ) : null}
        </div>
      ) : null}
    </section>
  );
});

export const HahaPermissionMessageBlock = memo(function HahaPermissionMessageBlock({
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
  const decision = typeof message.metadata?.decision === "string" ? message.metadata.decision : "";
  const resolved = message.metadata?.resolved === true || Boolean(decision);
  const busy = Boolean(requestId && busyId === requestId);

  return (
    <section className="haha-runtime-block haha-permission-block" data-kind="permission">
      <div className="haha-runtime-head">
        <ChevronRight size={14} />
        <CircleAlert size={15} />
        <strong>{formatToolNameLabel(message.toolName) || "权限请求"}</strong>
        <span>{compactText(message.content, 140)}</span>
        <StatusBadge label={resolved ? (decision === "rejected" ? "已拒绝" : "已批准") : "等待确认"} tone={resolved ? "success" : "warning"} compact />
      </div>
      {!resolved && requestId ? (
        <div className="haha-runtime-detail">
          <div className="haha-approval-actions">
            <button type="button" disabled={busy} onClick={() => void onApprove?.(requestId)}>批准</button>
            <button type="button" disabled={busy} onClick={() => void onReject?.(requestId)}>拒绝</button>
          </div>
        </div>
      ) : null}
    </section>
  );
});

export const HahaAssistantMessage = memo(function HahaAssistantMessage({ message }: { message: SessionWorkspaceMessage }) {
  const content = stripAssistantRuntimeProgress(message.content).trim();
  if (!content) return null;
  const document = shouldDocument(content);
  return (
    <article className="haha-message haha-assistant" data-layout={document ? "document" : "bubble"}>
      <div className="haha-assistant-shell">
        <HahaMarkdown content={content} />
      </div>
    </article>
  );
});

export const HahaUserMessage = memo(function HahaUserMessage({ message }: { message: SessionWorkspaceMessage }) {
  return (
    <article className="haha-message haha-user">
      <p>{message.content}</p>
    </article>
  );
});

export function HahaActivityItem({
  item,
  onApprove,
  onReject,
  onLoadPatch,
  onCopyRuntimeText,
  onRefreshCommandJob,
  onStopCommandJob,
  busyId,
}: {
  item: ConversationActivityItem;
  onApprove?: (approvalId: string) => void | Promise<void>;
  onReject?: (approvalId: string) => void | Promise<void>;
  onLoadPatch?: (patchId: string) => void | Promise<void>;
  onCopyRuntimeText?: (label: string, text: string) => void | Promise<void>;
  onRefreshCommandJob?: (commandId: string) => void | Promise<void>;
  onStopCommandJob?: (commandId: string) => void | Promise<void>;
  busyId?: string | null;
}) {
  if (item.kind === "runtime") {
    return (
      <HahaRuntimeBlock
        item={item.runtime}
        onApprove={onApprove}
        onReject={onReject}
        onLoadPatch={onLoadPatch}
        onCopyRuntimeText={onCopyRuntimeText}
        onRefreshCommandJob={onRefreshCommandJob}
        onStopCommandJob={onStopCommandJob}
        busyId={busyId}
      />
    );
  }
  if (item.kind === "worklog") {
    return (
      <div className="haha-worklog">
        <details>
          <summary>已执行 {item.runtimeItems.length} 项</summary>
          {item.runtimeItems.map((runtime) => (
            <HahaRuntimeBlock key={runtime.id} item={runtime} onLoadPatch={onLoadPatch} onCopyRuntimeText={onCopyRuntimeText} />
          ))}
        </details>
      </div>
    );
  }
  const message = item.message;
  const kind = metadataKind(message);
  if (kind === "assistant_thinking" || (message.streaming && message.placeholder)) return <HahaThinkingBlock message={message} />;
  if (kind === "tool_use" || kind === "tool_result" || kind === "tool_activity") return <HahaToolMessageBlock message={message} />;
  if (kind === "permission_request") return <HahaPermissionMessageBlock message={message} onApprove={onApprove} onReject={onReject} busyId={busyId} />;
  if (message.role === "user") return <HahaUserMessage message={message} />;
  if (message.role === "assistant") return <HahaAssistantMessage message={message} />;
  return null;
}
