import { memo, useState } from "react";
import { ApprovalCard, PatchPlanCard } from "../../../v2/components/runtime";
import { Button, StatusBadge } from "../../../v2/components/ui";
import { formatStatusLabel } from "../../../copy";
import type { RuntimeTimelineItem, DiffLine } from "./types";
import { useTickWhen } from "./useTick";
import {
  compactMeta,
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
  summarizeApprovalAction,
} from "./utils";

function looksLikeRuntimeMachineText(value?: string | null) {
  const normalized = (value ?? "").trim();
  if (!normalized) return false;
  if (/^(Task Cancelled|task\.[a-z0-9_.-]+|agent\.|command\.|provider\.)\b/i.test(normalized)) {
    return true;
  }
  if (
    /"?(sessionId|taskId|workspaceRoot|acceptanceCriteria|toolCallId|recoveryDecision|failureKind)"?\s*:/.test(
      normalized,
    )
  ) {
    return true;
  }
  if (!/^[{\[]/.test(normalized)) {
    return false;
  }
  try {
    const parsed = JSON.parse(normalized) as unknown;
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return false;
    const keys = new Set(Object.keys(parsed as Record<string, unknown>));
    return [
      "sessionId",
      "taskId",
      "workspaceRoot",
      "acceptanceCriteria",
      "toolCallId",
      "failureKind",
      "recoveryDecision",
      "cwd",
    ].some((key) => keys.has(key));
  } catch {
    return true;
  }
}

function sanitizeRuntimeDetail(value?: string | null) {
  const raw = (value ?? "").replace(/\r\n/g, "\n").trim();
  if (!raw) return "";
  if (/^(Task Cancelled|task\.cancelled)\b/i.test(raw)) {
    return "任务已取消，已停止继续执行。";
  }
  if (/Cannot supplement task that is not active|cannot transition from 'cancelled'|terminal state/i.test(raw)) {
    return "这条任务已经结束，不能继续补充；请重新发起一条任务。";
  }
  if (/concurrency limit exceeded/i.test(raw)) {
    return "模型并发额度暂时满了，请稍后重试。";
  }
  const lines = raw
    .split("\n")
    .filter((line) => {
      const trimmed = line.trim();
      if (!trimmed) return true;
      if (looksLikeRuntimeMachineText(trimmed)) return false;
      if (/^task\.[a-z0-9_.-]+/i.test(trimmed)) return false;
      return true;
    });
  const cleaned = lines.join("\n").trim();
  if (cleaned) return cleaned;
  if (/Command is not allowed by command allowlist|permission_denied/i.test(raw)) {
    return "命令没有真正执行：运行时策略拦截了这条命令，需要先审批或使用允许的等价命令。";
  }
  return "";
}

function readableTraceTitle(item: RuntimeTimelineItem) {
  const blob = `${item.title}\n${item.summary ?? ""}\n${item.code ?? ""}`;
  if (/^(Task Cancelled|task\.cancelled)/i.test(blob.trim())) return "任务已取消";
  if (/provider returned error|concurrency limit|provider request/i.test(blob)) return "模型调用异常";
  const sanitized = sanitizeRuntimeDetail(item.title);
  return sanitized || getRuntimeKindLabel(item.kind);
}

function buildCollapsedCommandBody(item: RuntimeTimelineItem) {
  const primaryDetail = sanitizeRuntimeDetail(
    item.kind === "command" ? buildCommandOutput(item) : runtimeDiagnosticDetail(item),
  );
  const actionLead =
    item.kind === "command" && item.summary
      ? sanitizeRuntimeDetail(item.summary)
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

function hasRuntimeDiff(item: RuntimeTimelineItem) {
  return Boolean(item.diffLines?.length || item.rawDetail?.includes("diff --git"));
}

function isDiagnosticRuntimeStatus(status?: string | null) {
  return ["failed", "error", "blocked", "cancelled", "rejected"].includes(status?.toLowerCase() ?? "");
}

function runtimeDiagnosticDetail(item: RuntimeTimelineItem) {
  if (hasRuntimeDiff(item)) {
    return item.rawDetail || "";
  }
  if (!isDiagnosticRuntimeStatus(item.status)) {
    return "";
  }
  return item.rawDetail || item.code || "";
}

function CompletionEvidenceCard({ item }: { item: RuntimeTimelineItem }) {
  const evidence = item.completionEvidence;
  if (!evidence) return null;
  const metrics = Array.isArray(evidence.metrics) ? evidence.metrics : [];
  const issues = Array.isArray(evidence.issues) ? evidence.issues : [];
  const auditApprovals = (evidence.audit?.approvals ?? [])
    .filter((approval) => approval.summary || approval.decision || approval.gateStatus || approval.reviewStatus || approval.verificationStatus)
    .slice(-3);

  return (
    <article
      className="runtime-event-card runtime-completion-card"
      data-activity-kind="runtime"
      data-kind="completion"
      data-status={item.status ?? evidence.status ?? "review"}
    >
      <header className="runtime-completion-head">
        <div>
          <span>完成审查</span>
          <strong>{item.title || "完成审查"}</strong>
        </div>
        {item.status ? <StatusBadge label={formatStatusLabel(item.status)} tone={getStatusTone(item.status)} compact /> : null}
      </header>
      {item.summary ? <p>{item.summary}</p> : null}
      <section className="runtime-completion-evidence" aria-label="Completion evidence">
        <div className="runtime-completion-evidence-head">
          {evidence.evidenceLevel ? <span>{evidence.evidenceLevel}</span> : null}
          {evidence.status ? <span>{evidence.status}</span> : null}
        </div>
        <p>{evidence.summary}</p>
        {metrics.length ? (
          <dl>
            {metrics.slice(0, 6).map((metric) => (
              <div key={`${metric.label}:${metric.value}`}>
                <dt>{metric.label}</dt>
                <dd>{metric.value}</dd>
              </div>
            ))}
          </dl>
        ) : null}
        {issues.length ? (
          <ul>
            {issues.slice(0, 4).map((issue) => (
              <li key={issue}>{issue}</li>
            ))}
          </ul>
        ) : null}
        {auditApprovals.length ? (
          <ul aria-label="Completion audit">
            {auditApprovals.map((approval, index) => (
              <li key={approval.approvalId ?? `${approval.kind ?? "approval"}:${index}`}>
                {approval.summary ? <span>{approval.summary}</span> : null}
                {[approval.decision, approval.gateStatus, approval.reviewStatus, approval.verificationStatus]
                  .filter(Boolean)
                  .map((part) => (
                    <small key={part}> {part}</small>
                  ))}
              </li>
            ))}
          </ul>
        ) : null}
        {evidence.reviewConclusion ? (
          <p>
            {[
              evidence.reviewConclusion.decision ? `review ${evidence.reviewConclusion.decision}` : null,
              evidence.reviewConclusion.decidedBy ? `by ${evidence.reviewConclusion.decidedBy}` : null,
              evidence.reviewConclusion.summary,
            ].filter(Boolean).join(" | ")}
          </p>
        ) : null}
      </section>
    </article>
  );
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
  const primaryDetail = sanitizeRuntimeDetail(
    item.kind === "command" ? buildCommandOutput(item) : runtimeDiagnosticDetail(item),
  );
  const secondaryPathDetail = item.kind === "command" ? buildCommandPathDetail(item) : "";
  const showSecondaryDetail = expanded || inFlight;
  const summaryText =
    item.kind === "command"
      ? buildCollapsedCommandBody(item)
      : compactText(sanitizeRuntimeDetail(item.summary), 180);
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

function countDiffLineTotals(diffLines?: DiffLine[]): { additions?: number; deletions?: number } {
  if (!diffLines?.length) {
    return {};
  }
  let additions = 0;
  let deletions = 0;
  let sawChange = false;
  for (const line of diffLines) {
    if (line.type === "add") {
      additions += 1;
      sawChange = true;
    } else if (line.type === "remove") {
      deletions += 1;
      sawChange = true;
    }
  }
  return sawChange ? { additions, deletions } : {};
}

function sumPatchFileTotals(
  files: ReturnType<typeof parsePatchFileSummaries>,
  field: "additions" | "deletions",
) {
  const known = files
    .map((file) => file[field])
    .filter((value): value is number => typeof value === "number");
  return known.length ? known.reduce((total, value) => total + value, 0) : undefined;
}

function readPatchMetaTotals(meta?: string[]) {
  const text = meta?.join(" ") ?? "";
  return {
    additions: /\+(\d+)/.exec(text)?.[1] ? Number(/\+(\d+)/.exec(text)?.[1]) : undefined,
    deletions: /-(\d+)/.exec(text)?.[1] ? Number(/-(\d+)/.exec(text)?.[1]) : undefined,
  };
}

interface RuntimeFileChangeRow {
  path: string;
  status?: string;
  additions?: number;
  deletions?: number;
  reason?: string;
}

function normalizeRuntimeFileChangePath(path: string) {
  const normalized = path
    .replace(/^[-+]\s*/, "")
    .replace(/^---\s+[ab]\//, "")
    .replace(/^\+\+\+\s+[ab]\//, "")
    .replace(/^[ab]\//, "")
    .trim();

  if (
    !normalized ||
    normalized === "/dev/null" ||
    normalized.startsWith("@@") ||
    normalized.startsWith("diff --git") ||
    /^(update|updated|create|created|delete|deleted|modify|modified)\s+/i.test(normalized) ||
    /\s/.test(normalized)
  ) {
    return "";
  }

  return normalized;
}

function parseRuntimeFileChangeRows(code?: string): RuntimeFileChangeRow[] {
  if (!code) {
    return [];
  }

  const rows = code
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const statusMatch = /^(added|modified|deleted|changed|新增|删除|修改|变更|已修改)\s+/i.exec(line);
      const status = statusMatch?.[1]?.toLowerCase();
      const rest = statusMatch ? line.slice(statusMatch[0].length).trim() : line;
      const parts = rest.split(/\s+-\s+/);
      const path = normalizeRuntimeFileChangePath(parts.shift()?.trim() ?? rest);
      const detail = parts.join(" - ");
      const additions = /\+(\d+)/.exec(detail)?.[1];
      const deletions = /-(\d+)/.exec(detail)?.[1];
      const reason = detail
        .replace(/\+\d+/g, "")
        .replace(/-\d+/g, "")
        .replace(/\s+/g, " ")
        .trim();
      return {
        path,
        status,
        additions: additions ? Number(additions) : undefined,
        deletions: deletions ? Number(deletions) : undefined,
        reason: reason || undefined,
      };
    })
    .filter((row) => Boolean(row.path));

  const seen = new Set<string>();
  return rows.filter((row) => {
    const key = `${row.path}:${row.status ?? ""}`;
    if (seen.has(key)) {
      return false;
    }
    seen.add(key);
    return true;
  });
}

function readPatchIdFromTaskFileItem(item: RuntimeTimelineItem) {
  const metaPatchId = item.meta?.find((entry) => /^patch[_:-]/i.test(entry) || /^patch_[\w-]+$/i.test(entry));
  return item.sourceId || metaPatchId;
}

function formatFileChangeStatus(status?: string) {
  if (status === "added") {
    return "新增";
  }
  if (status === "deleted") {
    return "删除";
  }
  if (status === "modified" || status === "changed") {
    return "修改";
  }
  return "变更";
}

function RuntimeFileChangeCard({
  item,
  expanded,
  onToggleExpanded,
  onLoadPatch,
}: {
  item: RuntimeTimelineItem;
  expanded: boolean;
  onToggleExpanded(): void;
  onLoadPatch?: (patchId: string) => void | Promise<void>;
}) {
  const rows = parseRuntimeFileChangeRows(item.code);
  const visibleRows = expanded ? rows : rows.slice(0, 5);
  const fileCount = rows.length || item.meta?.find((entry) => /个文件/.test(entry)) || "若干";
  const title = typeof fileCount === "number" ? `已记录 ${fileCount} 个文件改动` : `已记录 ${fileCount}改动`;
  const patchId = readPatchIdFromTaskFileItem(item);

  return (
    <article
      aria-label="代码改动摘要"
      className="runtime-file-change-card"
      data-activity-kind="runtime"
      data-kind={item.kind}
      data-status={item.status ?? "recorded"}
    >
      <button
        type="button"
        className="runtime-file-change-head"
        aria-expanded={expanded}
        onClick={onToggleExpanded}
      >
        <span className="runtime-file-change-icon" aria-hidden="true">+</span>
        <span>
          <strong>{title}</strong>
          <small>{compactText(item.summary, 140) || "这轮任务产生了文件改动；diff 会在主聊天里展开，文件可在右侧文件区打开。"}</small>
        </span>
        <StatusBadge label={formatStatusLabel(item.status ?? "recorded")} tone={getStatusTone(item.status)} compact />
        <i aria-hidden="true">{expanded ? "^" : "v"}</i>
      </button>
      {visibleRows.length ? (
        <div className="runtime-file-change-list">
          {visibleRows.map((row) => (
            <div className="runtime-file-change-row" key={`${row.status}:${row.path}`}>
              <strong>{row.path}</strong>
              <span>{formatFileChangeStatus(row.status)}</span>
              {row.additions !== undefined || row.deletions !== undefined ? (
                <em>
                  {compactMeta([
                    row.additions !== undefined ? `+${row.additions}` : undefined,
                    row.deletions !== undefined ? `-${row.deletions}` : undefined,
                  ]).join(" ")}
                </em>
              ) : null}
              {patchId && onLoadPatch ? (
                <button
                  type="button"
                  className="runtime-file-change-diff-button"
                  aria-label="查看文件差异"
                  onClick={() => {
                    void onLoadPatch(patchId);
                    onToggleExpanded();
                  }}
                >
                  查看文件差异
                </button>
              ) : null}
              {expanded && row.reason ? <small>{row.reason}</small> : null}
            </div>
          ))}
        </div>
      ) : null}
      {!expanded && rows.length > visibleRows.length ? <p className="runtime-file-change-more">另有 {rows.length - visibleRows.length} 个文件。</p> : null}
    </article>
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
  const normalizedApprovalStatus = item.status?.toLowerCase();
  const canResolveApproval =
    item.kind === "approval" &&
    item.sourceId &&
    ["pending", "queued", "waiting", "waiting_approval"].includes(normalizedApprovalStatus ?? "");
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
  const traceDetail = item.kind === "trace" ? sanitizeRuntimeDetail(item.code) : "";
  const canCopyTraceDetail = Boolean(item.kind === "trace" && onCopyRuntimeText && traceDetail.trim());
  const hasCommandActions = canRefreshCommand || canStopCommand || canCopyCommandOutput;

  if (item.kind === "task" && item.id.startsWith("task-files:")) {
    return (
      <RuntimeFileChangeCard
        item={item}
        expanded={expanded}
        onToggleExpanded={() => setExpanded((current) => !current)}
        onLoadPatch={onLoadPatch}
      />
    );
  }

  if (item.kind === "approval" && item.sourceId) {
    const actionSummary = item.meta?.[0] && item.meta[0] !== item.title ? item.meta[0] : undefined;
    const isPlanApproval = item.toolName === "plan" || item.meta?.includes("plan");
    return (
      <div className="runtime-event-card runtime-event-v2-card" data-activity-kind="runtime" data-kind={item.kind}>
        <ApprovalCard
          approval={{
            id: item.sourceId,
            title: actionSummary ?? summarizeApprovalAction({ title: item.title, kind: item.meta?.[1], command: item.code, parametersPreview: item.rawDetail }),
            kind: item.title,
            status: item.status ?? "pending",
            summary: item.summary,
            risk: item.riskLevel ?? "low",
            command: isPlanApproval ? undefined : item.code,
            cwd: item.meta?.find((entry) => /^[A-Z]:|^\//.test(entry)),
            requestedAt: item.time,
            previewRows: item.previewRows,
            previewSections: item.previewSections,
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

  if (item.kind === "completion") {
    return <CompletionEvidenceCard item={item} />;
  }

  if (item.kind === "patch" && item.sourceId) {
    const changedFiles = parsePatchFileSummaries(item.code);
    const diffTotals = countDiffLineTotals(item.diffLines);
    const metaTotals = readPatchMetaTotals(item.meta);
    const additions = sumPatchFileTotals(changedFiles, "additions") ?? metaTotals.additions ?? diffTotals.additions;
    const deletions = sumPatchFileTotals(changedFiles, "deletions") ?? metaTotals.deletions ?? diffTotals.deletions;
    return (
      <div className="runtime-event-card runtime-event-v2-card" data-activity-kind="runtime" data-kind={item.kind}>
        <PatchPlanCard
          patch={{
            id: item.sourceId,
            summary: item.title,
            status: item.status ?? "recorded",
            filesChanged: changedFiles.length || undefined,
            additions,
            deletions,
          }}
          changedFiles={changedFiles}
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
    const traceTitle = readableTraceTitle(item);
    const traceSummary = sanitizeRuntimeDetail(item.summary);
    return (
      <article
        className="runtime-event-card runtime-trace-row"
        data-activity-kind="runtime"
        data-kind={item.kind}
        data-status={item.status ?? "recorded"}
        data-superseded={item.superseded ? "true" : undefined}
      >
        <button
          aria-label={`${kindLabel} ${traceTitle}${item.status ? ` ${formatStatusLabel(item.status)}` : ""}`}
          aria-expanded={expanded}
          className="runtime-trace-row-summary"
          onClick={() => setExpanded((current) => !current)}
          type="button"
        >
          <span className="runtime-trace-dot" aria-hidden="true" />
          <span className="runtime-trace-row-copy">
            <strong>{traceTitle}</strong>
            {traceSummary ? <small>{compactText(traceSummary, 160)}</small> : null}
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
        {expanded && traceDetail ? (
          <div className="runtime-trace-row-detail">
            {canCopyTraceDetail ? (
              <div className="runtime-trace-row-actions">
                <Button
                  size="xs"
                  variant="secondary"
                  aria-label="复制详情"
                  onClick={() => {
                    void onCopyRuntimeText?.("诊断详情", traceDetail);
                  }}
                >
                  复制详情
                </Button>
              </div>
            ) : null}
            <pre>{traceDetail}</pre>
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
