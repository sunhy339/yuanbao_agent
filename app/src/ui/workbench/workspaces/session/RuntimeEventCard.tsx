import { memo, useState } from "react";
import { ApprovalCard, PatchPlanCard } from "../../../v2/components/runtime";
import { Button, StatusBadge } from "../../../v2/components/ui";
import { formatStatusLabel } from "../../../copy";
import type { RuntimeTimelineItem, DiffLine } from "./types";
import { useTickWhen } from "./useTick";
import {
  compactText,
  getRuntimeKindLabel,
  getStatusTone,
  isRuntimeInFlight,
  getProcessStatusLabel,
  getProcessTimeLabel,
  parsePatchPath,
  parsePatchFileSummaries,
  buildCommandOutput,
  buildCommandPathDetail,
} from "./utils";

function buildCollapsedCommandBody(item: RuntimeTimelineItem) {
  const primaryDetail =
    item.kind === "command" ? buildCommandOutput(item) : item.code || item.rawDetail;
  const actionLead =
    item.kind === "command" && item.summary
      ? item.summary
          .split(/[·|]/)
          .map((part) => part.trim())
          .find(Boolean)
      : undefined;
  if (!primaryDetail && !actionLead) {
    return "";
  }
  const lines = (primaryDetail ?? "")
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      if (actionLead && line.startsWith(`${actionLead} · `)) {
        return line.slice(actionLead.length + 3).trim();
      }
      return line;
    })
    .filter((line) => !/^[A-Z]:\\|^\//.test(line))
    .filter((line) => !/^(输入|输出|错误|stdout|stderr|相关路径|日志路径)$/i.test(line))
    .filter((line) => !/^[{\[]/.test(line))
    .slice(0, 3);
  const combined = [actionLead, ...lines.filter((line) => line !== actionLead)].filter(Boolean).join("\n");
  return compactText(combined, 220);
}

function ProcessRuntimeCard({
  item,
  kindLabel,
  expanded,
  onToggleExpanded,
}: {
  item: RuntimeTimelineItem;
  kindLabel: string;
  expanded: boolean;
  onToggleExpanded(): void;
}) {
  const inFlight = isRuntimeInFlight(item.status);
  const [fallbackStartedAt] = useState(() => Date.now());
  const now = useTickWhen(inFlight);

  const statusLabel = getProcessStatusLabel(item.status);
  const timeLabel = getProcessTimeLabel(item, now, fallbackStartedAt);
  const primaryDetail =
    item.kind === "command" ? buildCommandOutput(item) : item.code || item.rawDetail;
  const secondaryPathDetail = item.kind === "command" ? buildCommandPathDetail(item) : "";
  const showSecondaryDetail = expanded || inFlight;
  const summaryText =
    item.kind === "command"
      ? buildCollapsedCommandBody(item)
      : compactText(item.summary, 180);
  const shouldShowSummary = Boolean(summaryText && !item.superseded);

  return (
    <article
      className="runtime-process-card"
      data-kind={item.kind}
      data-status={item.status ?? "recorded"}
      data-superseded={item.superseded ? "true" : undefined}
      data-active={inFlight ? "true" : "false"}
    >
      <button
        aria-label={`${kindLabel} ${item.title} ${statusLabel}${timeLabel ? ` ${timeLabel}` : ""}`}
        aria-expanded={expanded}
        className="runtime-process-head"
        onClick={onToggleExpanded}
        type="button"
      >
        <span className="runtime-process-spark" aria-hidden="true" />
        <h3>{item.title}</h3>
        <StatusBadge label={statusLabel} tone={getStatusTone(item.status)} compact />
        {timeLabel ? <time>{timeLabel}</time> : null}
      </button>
      {shouldShowSummary ? <p className="runtime-process-summary">{summaryText}</p> : null}
      {showSecondaryDetail && item.meta?.length ? (
        <div className="runtime-process-meta">
          {item.meta
            .filter((entry) => !/^[A-Z]:|^\//.test(entry))
            .slice(0, expanded ? 4 : 2)
            .map((entry) => (
            <span key={entry}>{entry}</span>
          ))}
        </div>
      ) : null}
      {expanded && primaryDetail ? <pre className="runtime-process-detail">{primaryDetail}</pre> : null}
      {expanded && secondaryPathDetail ? <pre className="runtime-process-subdetail">{secondaryPathDetail}</pre> : null}
    </article>
  );
}

function PatchDiffBody({ diffLines, isBusy }: { diffLines?: DiffLine[]; isBusy: boolean }) {
  if (!diffLines || diffLines.length === 0) {
    return (
      <p className="runtime-diff-empty" role="status">
        {isBusy ? "差异正在加载。" : "差异暂不可用。请在运行时写入改动后再试一次。"}
      </p>
    );
  }

  return (
    <div className="diff-view">
      {diffLines.map((line, lineIndex) => (
        <div key={lineIndex} className={`diff-line diff-line-${line.type}`}>
          <span className="diff-line-prefix">
            {line.type === "add" ? "+" : line.type === "remove" ? "-" : line.type === "header" ? "" : " "}
          </span>
          <span className="diff-line-content">{line.content}</span>
        </div>
      ))}
    </div>
  );
}

function PatchDiffDetail({
  expanded,
  diffLines,
  isBusy,
}: {
  expanded: boolean;
  diffLines?: DiffLine[];
  isBusy: boolean;
}) {
  if (!expanded) {
    return null;
  }

  return (
    <div className="runtime-event-detail">
      <PatchDiffBody diffLines={diffLines} isBusy={isBusy} />
    </div>
  );
}

export const RuntimeEventCard = memo(function RuntimeEventCard({
  item,
  onApprove,
  onReject,
  onLoadPatch,
  onCopyPatchPath,
  onCopyRuntimeText,
  onRefreshCommandJob,
  onStopCommandJob,
  busyId,
}: {
  item: RuntimeTimelineItem;
  onApprove?(approvalId: string): void | Promise<void>;
  onReject?(approvalId: string): void | Promise<void>;
  onLoadPatch?(patchId: string): void | Promise<void>;
  onCopyPatchPath?(patchId: string, path: string): void | Promise<void>;
  onCopyRuntimeText?(label: string, text: string): void | Promise<void>;
  onRefreshCommandJob?(commandId: string): void | Promise<void>;
  onStopCommandJob?(commandId: string): void | Promise<void>;
  busyId?: string | null;
}) {
  const [expanded, setExpanded] = useState(false);
  const kindLabel = getRuntimeKindLabel(item.kind);
  const canResolveApproval = item.kind === "approval" && item.status === "pending" && item.sourceId;
  const canLoadPatch = item.kind === "patch" && Boolean(item.sourceId && onLoadPatch);
  const patchPaths = item.kind === "patch" && item.sourceId && item.code
    ? item.code.split("\n").map(parsePatchPath).filter(Boolean)
    : [];
  const canRefreshCommand = item.kind === "command" && Boolean(item.sourceId && onRefreshCommandJob);
  const canStopCommand =
    item.kind === "command" &&
    Boolean(item.sourceId && onStopCommandJob && ["running", "started"].includes(item.status ?? ""));
  const isBusy = item.sourceId ? busyId === item.sourceId : false;
  const commandOutput = item.kind === "command" ? buildCommandOutput(item) : "";
  const canCopyCommandOutput = Boolean(item.kind === "command" && onCopyRuntimeText && commandOutput.trim());
  const canCopyTraceDetail = Boolean(item.kind === "trace" && onCopyRuntimeText && item.code?.trim());
  const hasCommandActions = canRefreshCommand || canStopCommand || canCopyCommandOutput;

  if (item.kind === "approval" && item.sourceId) {
    return (
      <div className="runtime-event-card runtime-event-v2-card" data-activity-kind="runtime" data-kind={item.kind}>
        <ApprovalCard
          approval={{
            id: item.sourceId,
            title: item.title,
            kind: item.meta?.[0],
            status: item.status ?? "pending",
            summary: item.summary,
            risk: item.riskLevel ?? "low",
            command: item.code,
            cwd: item.meta?.find((entry) => /^[A-Z]:|^\//.test(entry)),
            requestedAt: item.time,
            completionEvidence: item.completionEvidence,
          }}
          busy={isBusy}
          onApprove={(approvalId) => {
            void onApprove?.(approvalId);
          }}
          onReject={(approvalId) => {
            void onReject?.(approvalId);
          }}
        />
      </div>
    );
  }

  if (item.kind === "patch" && item.sourceId) {
    return (
      <div className="runtime-event-card runtime-event-v2-card" data-activity-kind="runtime" data-kind={item.kind}>
        <PatchPlanCard
          patch={{
            id: item.sourceId,
            summary: item.title,
            status: item.status ?? "recorded",
            filesChanged: parsePatchFileSummaries(item.code).length || undefined,
          }}
          changedFiles={parsePatchFileSummaries(item.code)}
          onOpenDiff={(patchId) => {
            void onLoadPatch?.(patchId);
            setExpanded(true);
          }}
        />
        <PatchDiffDetail expanded={expanded} diffLines={item.diffLines} isBusy={isBusy} />
      </div>
    );
  }

  if (item.kind === "command") {
    return (
      <div className="runtime-event-card runtime-event-v2-card" data-activity-kind="runtime" data-kind={item.kind}>
        <ProcessRuntimeCard
          item={item}
          kindLabel={kindLabel}
          expanded={expanded}
          onToggleExpanded={() => setExpanded((current) => !current)}
        />
        {hasCommandActions ? (
          <div className="runtime-event-actions">
            {canCopyCommandOutput ? (
              <Button
                size="xs"
                variant="secondary"
                aria-label="复制输出"
                onClick={() => {
                  void onCopyRuntimeText?.("命令输出", commandOutput);
                }}
              >
                复制输出
              </Button>
            ) : null}
            {canRefreshCommand ? (
              <Button
                size="xs"
                variant="secondary"
                loading={isBusy}
                onClick={() => {
                  void onRefreshCommandJob?.(item.sourceId ?? "");
                }}
              >
                刷新
              </Button>
            ) : null}
            {canStopCommand ? (
              <Button
                size="xs"
                variant="danger"
                loading={isBusy}
                onClick={() => {
                  void onStopCommandJob?.(item.sourceId ?? "");
                }}
              >
                停止
              </Button>
            ) : null}
          </div>
        ) : null}
      </div>
    );
  }

  if (item.kind === "tool") {
    return (
      <div className="runtime-event-card runtime-event-v2-card" data-activity-kind="runtime" data-kind={item.kind}>
        <ProcessRuntimeCard
          item={item}
          kindLabel={kindLabel}
          expanded={expanded}
          onToggleExpanded={() => setExpanded((current) => !current)}
        />
      </div>
    );
  }

  if (item.kind === "trace") {
    return (
      <article
        className="runtime-event-card runtime-trace-row"
        data-activity-kind="runtime"
        data-kind={item.kind}
        data-status={item.status ?? "recorded"}
        data-superseded={item.superseded ? "true" : undefined}
      >
        <button
          aria-label={`${kindLabel} ${item.title}${item.status ? ` ${formatStatusLabel(item.status)}` : ""}`}
          aria-expanded={expanded}
          className="runtime-trace-row-summary"
          onClick={() => setExpanded((current) => !current)}
          type="button"
        >
          <span className="runtime-trace-dot" aria-hidden="true" />
          <span className="runtime-trace-row-copy">
            <strong>{item.title}</strong>
            {item.summary ? <small>{compactText(item.summary, 160)}</small> : null}
          </span>
          {item.status ? <StatusBadge label={formatStatusLabel(item.status)} tone={getStatusTone(item.status)} compact /> : null}
          <i aria-hidden="true">{expanded ? "^" : "v"}</i>
        </button>
        {item.meta?.length ? (
          <div className="runtime-trace-row-meta">
            {item.meta
              .filter((entry) => !/^[A-Z]:|^\//.test(entry))
              .slice(0, expanded ? 4 : 2)
              .map((entry) => (
              <span key={entry}>{entry}</span>
            ))}
          </div>
        ) : null}
        {expanded && item.code ? (
          <div className="runtime-trace-row-detail">
            {canCopyTraceDetail ? (
              <div className="runtime-trace-row-actions">
                <Button
                  size="xs"
                  variant="secondary"
                  aria-label="复制详情"
                  onClick={() => {
                    void onCopyRuntimeText?.("诊断详情", item.code ?? "");
                  }}
                >
                  复制详情
                </Button>
              </div>
            ) : null}
            <pre>{item.code}</pre>
          </div>
        ) : null}
      </article>
    );
  }

  return (
    <article
      className="runtime-event-card"
      data-activity-kind="runtime"
      data-kind={item.kind}
      data-superseded={item.superseded ? "true" : undefined}
    >
      <button
        aria-label={`${kindLabel} ${item.title}${item.status ? ` ${formatStatusLabel(item.status)}` : ""}`}
        aria-expanded={expanded}
        className="runtime-event-summary"
        onClick={() => setExpanded((current) => !current)}
        type="button"
      >
        <span>{kindLabel}</span>
        <strong>{item.title}</strong>
        {item.status ? <StatusBadge label={formatStatusLabel(item.status)} tone={getStatusTone(item.status)} /> : null}
        <i aria-hidden="true">{expanded ? "⌃" : "⌄"}</i>
      </button>
      {item.meta?.length && !expanded ? (
        <div className="runtime-event-meta runtime-event-meta-compact">
          {item.meta.slice(0, 4).map((entry) => (
            <span key={entry}>{entry}</span>
          ))}
        </div>
      ) : null}
      {!expanded && item.summary ? (
        <p className="runtime-event-collapsed-summary">{compactText(item.summary, 180)}</p>
      ) : null}
      {canResolveApproval ? (
        <div className="runtime-event-actions">
          <button
            aria-label={`批准 ${item.title}`}
            onClick={() => {
              void onApprove?.(item.sourceId ?? "");
            }}
            type="button"
          >
            批准
          </button>
          <button
            aria-label={`拒绝 ${item.title}`}
            onClick={() => {
              void onReject?.(item.sourceId ?? "");
            }}
            type="button"
          >
            拒绝
          </button>
        </div>
      ) : null}
      {canLoadPatch || canRefreshCommand || canStopCommand ? (
        <div className="runtime-event-actions">
          {canLoadPatch ? (
            <Button
              size="xs"
              variant="secondary"
              loading={isBusy}
              onClick={() => {
                void onLoadPatch?.(item.sourceId ?? "");
                setExpanded(true);
              }}
            >
              加载差异
            </Button>
          ) : null}
          {canRefreshCommand ? (
            <Button
              size="xs"
              variant="secondary"
              loading={isBusy}
              onClick={() => {
                void onRefreshCommandJob?.(item.sourceId ?? "");
              }}
            >
              刷新
            </Button>
          ) : null}
          {canStopCommand ? (
            <Button
              size="xs"
              variant="danger"
              loading={isBusy}
              onClick={() => {
                void onStopCommandJob?.(item.sourceId ?? "");
              }}
            >
              停止
            </Button>
          ) : null}
        </div>
      ) : null}
      {expanded ? (
        <div className="runtime-event-detail">
          {item.meta?.length ? (
            <div className="runtime-event-meta">
              {item.meta.map((entry) => (
                <span key={entry}>{entry}</span>
              ))}
            </div>
          ) : null}
          {item.summary ? <p>{item.summary}</p> : null}
          {item.code ? <p className="runtime-event-code-summary">{item.code}</p> : null}
          {patchPaths.length && item.sourceId && onCopyPatchPath ? (
            <div className="runtime-patch-files" aria-label="改动文件">
              {patchPaths.map((path) => (
                <button
                  key={path}
                  type="button"
                  onClick={() => {
                    void onCopyPatchPath(item.sourceId ?? "", path);
                  }}
                >
                  <span>{path}</span>
                  <strong>复制路径</strong>
                </button>
              ))}
            </div>
          ) : null}
          {item.kind === "patch" ? <PatchDiffBody diffLines={item.diffLines} isBusy={isBusy} /> : null}
        </div>
      ) : null}
    </article>
  );
});
